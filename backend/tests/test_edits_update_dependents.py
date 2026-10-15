"""Edits update what depends on them.

An analyst's edit or remap changes the run's served rows and nothing else, and four things that
depend on those rows were frozen at extraction:

* the structural relations and the coverage counted from them (``run.result["structural"]``,
  written once), so a correction never reached the cards, the band or the export caption;
* the acceptance of a structural or guard card, whose evidence was taken over those frozen
  figures and so could not go stale when an input was edited;
* a manual formula, evaluated once when it was typed and stored as a literal;
* the cached credit narrative, served after the figures it describes had changed.

The relations are now evaluated from the served rows on every read, with the run's own template
and line-item set; the stored result stays on the run as its history.
"""
from __future__ import annotations

import json
import pathlib
import time
import uuid

import pytest

from app.api.routes.documents import (
    _EDITED_INPUT_LIVE, _GUARD_FIX_LIVE, _REMAPPED_INPUT_LIVE, _TR, _build_review,
    _narrative_in_force)
from app.schemas.loader import load_template
from app.services.analysis_llm import credit_figures_digest
from app.services.formula import reevaluate, stored_formulas
from app.services.structural_checks import evaluate_structure, items_of_rows

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
LOCALES = ("zh", "ar", "fr")

# A current-assets section printed with inventories mis-signed, on a template whose section owns
# an Others line (so a line it does not print is nil): 3,510 printed, -2,200 + 1,310 = -890 summed.
_TARGET = "bs_current_assets__total_current_assets"
_INV = "bs_current_assets__inventories"
_REC = "bs_current_assets__trade_receivables"
_CASH = "bs_current_assets__cash_and_cash_equivalents"


@pytest.fixture(scope="module")
def template_def() -> dict:
    return json.loads((SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8"))


def _row(key, cur, label=None, prior=None, **extra):
    values = [{"basis": "consolidated", "period_label": "current", "value": str(cur),
               "scale": "1"}]
    if prior is not None:
        values.append({"basis": "consolidated", "period_label": "prior", "value": str(prior),
                       "scale": "1"})
    return {"canonical_key": key, "source_label": label or key, "printed_in": "face",
            "mapping_method": "alias", "values": values, **extra}


def _rows(inventories=-2200):
    return [_row(_INV, inventories, "Inventories"), _row(_REC, 1310, "Trade receivables"),
            _row(_TARGET, 3510, "Total current assets")]


def _relations(template_def, rows) -> list[dict]:
    report = evaluate_structure(load_template(json.loads(json.dumps(template_def))),
                                items_of_rows(rows))
    return [r.model_dump(mode="json") for r in report.results]


def _rollup(relations: list[dict]) -> dict:
    return next(r for r in relations if r["rule_id"] == f"rollup:{_TARGET}"
                and r["scope_key"] == "consolidated/current")


def _seed(template_def: dict, rows: list[dict], *, options: dict | None = None,
          extra: dict | None = None) -> str:
    """A document whose latest run is these rows against this template."""
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    with SessionLocal() as s:
        doc = Document(filename="e.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin")
        s.add(doc)
        s.flush()
        tv = TemplateVersion(template_key=f"e-{uuid.uuid4().hex[:8]}", name="E", version=1,
                             definition=template_def)
        s.add(tv)
        s.flush()
        s.add(ExtractionRun(document_id=doc.id, status="succeeded",
                            options={"template_version_id": tv.id, **(options or {})},
                            result={"rows": rows, "filename": "e.pdf", "pages": [],
                                    **(extra or {})}))
        s.commit()
        return doc.id


def _stored(doc_id: str) -> dict:
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as s:
        return dict(s.query(ExtractionRun).filter(ExtractionRun.document_id == doc_id).one().result)


def _card(review: dict, target: str = _TARGET) -> dict | None:
    return next((c for c in review["checks"] if c["type"] == "structural"
                 and c["target"] == target), None)


def _patch(client, doc_id, key, value, formula="", period="current"):
    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/{key}",
                     json={"value": value, "formula": formula, "basis": "consolidated",
                           "period": period, "comment": ""})
    assert r.status_code == 200, r.text
    return r.json()


# ── the adapter: served rows → the line items the structural stage evaluated ─────────────────────

def test_a_run_nobody_touched_is_evaluated_from_its_rows_exactly_as_the_stage_stored_it(client):
    """The read-time evaluation is only worth serving if, on rows nobody changed, it gives back what
    the pipeline stored: every relation, status, figure and detail."""
    from app.api.routes.documents import _structural_for_run
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun
    from app.sample.reference import shipped_line_items_key
    from tests.fixtures.generate import make_native_pdf

    pytest.importorskip("fitz")
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("p.pdf", make_native_pdf(),
                                         "application/pdf")}).json()["id"]
    cfg = next(c for c in client.get("/api/v1/line-items/versions").json()
               if c["line_items_key"] == shipped_line_items_key())
    tpl = next(t for t in client.get("/api/v1/templates").json()
               if t["template_key"] == cfg["target_template_key"])
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    for _ in range(200):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter(ExtractionRun.document_id == doc_id).one()
        stored = run.result["structural"]
        live, from_rows = _structural_for_run(session, run)
    assert from_rows is True and stored
    assert live == stored
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert review["coverage"]["evaluated_from"] == "rows"


def test_an_edited_slot_replaces_its_concepts_figure_and_a_cleared_one_has_none(template_def):
    """An edit is the analyst's figure for the concept, as the grid shows it: the other printed
    lines of the concept no longer add in that slot, and only in that slot."""
    rows = _rows() + [_row(_INV, 100, "Inventories (spares)", prior=50)]
    assert _rollup(_relations(template_def, rows))["details"]["component_values"][_INV] == "-2100"
    rows[0] = {**rows[0], "edited": True, "edited_slots": ["consolidated/current"],
               "values": [{**rows[0]["values"][0], "value": "2200"}]}
    rel = _rollup(_relations(template_def, rows))
    assert rel["details"]["component_values"][_INV] == "2200"      # 100 no longer adds
    assert rel["status"] == "pass"
    # The other row's prior figure is untouched by an edit to the current column.
    items = {li.source_label: li for li in items_of_rows(rows)}
    assert [ev.period_label for ev in items["Inventories (spares)"].values.values()] == ["prior"]

    cleared = [{**rows[0], "values": [{**rows[0]["values"][0], "value": None}]}, *rows[1:]]
    assert not [ev for li in items_of_rows(cleared) if li.canonical_key == _INV
                for ev in li.values.values() if ev.period_label == "current"]


def test_rows_added_after_the_run_are_not_evaluated_and_a_computed_row_stays_computed():
    rows = [{**_row("statement_setup_controls__entity", 1), "mapping_method": "supplemental"},
            # A run stored before rows carried `is_computed`: read through the writer's own flag.
            {**_row(_INV, 5), "flags": ["note_sourced:2"]},
            {**_row(_REC, 5), "is_computed": True},
            _row(_CASH, 5)]
    items = {li.canonical_key: li for li in items_of_rows(rows)}
    assert "statement_setup_controls__entity" not in items
    assert items[_INV].is_computed and items[_REC].is_computed
    assert not items[_CASH].is_computed


def test_the_served_row_says_whether_the_pipeline_computed_it():
    from app.api.routes.extractions import _serialize_rows
    from app.core.models import DocumentModel, LineItem

    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [LineItem(source_label="a", canonical_key=_INV, is_computed=True),
                      LineItem(source_label="b", canonical_key=_REC)]
    assert [r["is_computed"] for r in _serialize_rows(doc)] == [True, False]


# ── the review, the band and the export follow an edit or a remap ───────────────────────────────

def test_an_edit_updates_the_card_the_band_and_the_export_and_the_run_keeps_its_history(
        client, template_def):
    stored = _relations(template_def, _rows())
    doc_id = _seed(template_def, _rows(), extra={"structural": stored})
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    card = _card(review)
    assert card is not None and card["delta"] == "4,400"
    failed = review["coverage"]["aggregate"]["failed"]

    # An edit that does not fix it: the card carries the new figure and says it is in the relation.
    _patch(client, doc_id, _REC, 1500)
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    card = _card(review)
    assert card["delta"] == "4,210"
    assert card["inputs_edited"] is True and card["inputs_edited_note"] == _EDITED_INPUT_LIVE

    # An edit that does: the card goes, and the band and the export count the relation as held.
    _patch(client, doc_id, _INV, 2010)
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert _card(review) is None
    assert review["coverage"]["aggregate"]["failed"] == failed - 1
    exported = json.loads(client.get(f"/api/v1/documents/{doc_id}/export?fmt=json").content)
    assert exported["validation"]["coverage"]["aggregate"]["failed"] == failed - 1

    # The run still records what the pipeline found at extraction.
    assert _stored(doc_id)["structural"] == stored


def test_a_remap_onto_a_component_updates_the_relation(client, template_def):
    """A printed row nothing claimed, re-mapped onto the line the total is missing."""
    rows = [_row(_INV, 2200, "Inventories"), _row(_REC, 1310, "Trade receivables"),
            _row(_TARGET, 4010, "Total current assets"),
            {**_row(None, 500, "Cash at bank"), "id": "r-cash"}]
    doc_id = _seed(template_def, rows)
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert _card(review)["delta"] == "500"
    unmapped = next(c for c in review["checks"] if c["type"] == "unmapped")
    moved = client.post(f"/api/v1/documents/{doc_id}/review/remap",
                        json={"row_ref": unmapped["remap"]["row_ref"], "canonical_key": _CASH,
                              "reason": "the bank balance"})
    assert moved.status_code == 200, moved.text
    assert _card(client.get(f"/api/v1/documents/{doc_id}/review").json()) is None


def test_a_run_whose_configuration_no_longer_loads_is_served_as_stored(client, template_def):
    """Evaluating without the run's rulebook would drop its relations and report the rest as the
    run's coverage, so the stored result is served — and its card says it waits for the next
    extraction."""
    stored = _relations(template_def, _rows())
    edited = _rows()
    edited[0] = {**edited[0], "edited": True, "edited_slots": ["consolidated/current"],
                 "values": [{**edited[0]["values"][0], "value": "2200"}]}
    doc_id = _seed(template_def, edited, options={"line_item_version_id": "gone"},
                   extra={"structural": stored})
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert review["coverage"]["evaluated_from"] == "extraction"
    card = _card(review)
    assert card is not None and card["delta"] == "4,400"
    assert "next extraction" in card["inputs_edited_note"]


# ── an acceptance goes stale when an input moves ────────────────────────────────────────────────

def _accept(client, doc_id, card):
    r = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                    json={"subject_key": card["subject_key"],
                          "evidence_digest": card["evidence_digest"], "reason": "checked"})
    assert r.status_code == 200, r.text


def test_an_accepted_structural_card_goes_stale_when_an_input_is_edited(client, template_def):
    doc_id = _seed(template_def, _rows())
    card = _card(client.get(f"/api/v1/documents/{doc_id}/review").json())
    _accept(client, doc_id, card)
    assert _card(client.get(f"/api/v1/documents/{doc_id}/review").json())["status"] == "accepted"

    _patch(client, doc_id, _REC, 1500)
    after = _card(client.get(f"/api/v1/documents/{doc_id}/review").json())
    assert after["subject_key"] == card["subject_key"]
    assert after["status"] == "stale"
    assert "components" in after["judgement"]["changed"]


def test_on_a_stored_relation_the_edited_inputs_are_in_the_evidence(template_def):
    """A relation served as stored cannot move, so the figures its edited lines show now go into
    its evidence, and only when there are any (an untouched card keeps its digest)."""
    stored = _relations(template_def, _rows())
    clean = _card(_build_review(_rows(), "e.pdf", "en", [], stored, template_def))
    assert "inputs_now" not in clean["evidence"]
    edited = _rows()
    edited[1] = {**edited[1], "edited": True, "edited_slots": ["consolidated/current"],
                 "values": [{**edited[1]["values"][0], "value": "1500"}]}
    accepted = {"subject_key": clean["subject_key"], "subject": clean["subject"],
                "evidence": clean["evidence"], "reason": "checked", "actor": "a"}
    card = _card(_build_review(edited, "e.pdf", "en", [], stored, template_def,
                               judgements=[accepted]))
    assert card["evidence"]["inputs_now"] == {_REC: 1500}
    assert card["status"] == "stale"


def test_a_mutually_exclusive_guard_moves_with_each_loaded_components_figure():
    """The aggregate's figure alone was its evidence: a loaded component moving from 300 to 900
    left an accepted guard accepted."""
    from app.services.structural_checks import Guard, MappedValues, _guard_slot

    guard = Guard(id="guard:mutually_exclusive:bs_ca__others", predicate="mutually_exclusive",
                  keys=("bs_ca__others", "bs_ca__prepayments"), statement="balance_sheet",
                  precondition="always", text="Others excludes prepayments")

    def card(prepayments):
        vals = MappedValues(values={"bs_ca__others": {("consolidated", "current"): 700},
                                    "bs_ca__prepayments": {("consolidated", "current"):
                                                           prepayments}})
        res = _guard_slot(guard, ("consolidated", "current"), vals, {})
        return _build_review([], "e.pdf", "en", [], [res.model_dump(mode="json")],
                             structural_live=True)["checks"][0]

    first, moved = card(300), card(900)
    assert first["subject_key"] == moved["subject_key"]
    assert first["evidence_digest"] != moved["evidence_digest"]
    assert first["fix"] == _GUARD_FIX_LIVE


# ── a stored formula follows the lines it names ─────────────────────────────────────────────────

def test_a_formula_line_follows_an_edit_to_a_line_it_names(client, template_def):
    rows = [_row(_INV, 2200, "Inventories"), _row(_REC, 1310, "Trade receivables")]
    doc_id = _seed(template_def, rows)
    typed = _patch(client, doc_id, _TARGET, None, formula=f"={_INV}+{_REC}")
    assert typed["current"] == 3510
    assert stored_formulas(next(r for r in _stored(doc_id)["rows"]
                                if r["canonical_key"] == _TARGET)) == {
        "consolidated/current": f"={_INV}+{_REC}"}

    _patch(client, doc_id, _INV, 2500)
    statement = client.get(f"/api/v1/documents/{doc_id}/statement?statement=balance_sheet").json()
    line = next(r for r in statement["rows"] if r.get("id") == _TARGET)
    assert line["v1"] == 3810
    run_rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    assert next(r for r in run_rows if r["canonical_key"] == _TARGET)["values"][0]["value"] \
        == "3810"
    # The stored literal is the one typed; the served figure is the formula's today.
    assert next(r for r in _stored(doc_id)["rows"]
                if r["canonical_key"] == _TARGET)["values"][0]["value"] == "3510"

    # A plain value typed over the formula's slot ends the formula there.
    _patch(client, doc_id, _TARGET, 4000)
    assert stored_formulas(next(r for r in _stored(doc_id)["rows"]
                                if r["canonical_key"] == _TARGET)) == {}


def test_formulas_are_evaluated_in_order_and_one_that_cannot_be_keeps_its_figure():
    def driven(key, formula, stored):
        return {**_row(key, stored), "edited": True, "edited_slots": ["consolidated/current"],
                "formulas": {"consolidated/current": formula}}

    rows = [_row("a", 10), _row("b", 5),
            driven("u", "=t*2", 24),                       # reads a formula line below it
            driven("t", "=a+b", 12),
            driven("x", "=y+1", 1), driven("y", "=x+1", 2),  # read each other
            driven("m", "=missing+1", 7)]
    out = {r["canonical_key"]: r for r in reevaluate(rows)}
    assert out["t"]["values"][0]["value"] == "15"
    assert out["u"]["values"][0]["value"] == "30"
    assert out["x"]["values"][0]["value"] == "1" and out["y"]["values"][0]["value"] == "2"
    assert out["x"]["formula_errors"] == {"consolidated/current": "circular reference"}
    assert out["m"]["values"][0]["value"] == "7"
    assert "missing" in out["m"]["formula_errors"]["consolidated/current"]
    # The stored rows are never written.
    assert rows[3]["values"][0]["value"] == "12" and "formula_errors" not in rows[4]
    # Nothing to evaluate: the same list comes back.
    plain = [_row("a", 1)]
    assert reevaluate(plain) is plain


def test_a_formula_typed_before_the_slot_was_recorded_is_placed_when_one_slot_was_edited():
    legacy = {**_row("t", 12), "edited": True, "edited_slots": ["consolidated/current"],
              "formula": "=a+b"}
    assert stored_formulas(legacy) == {"consolidated/current": "=a+b"}
    two = {**legacy, "edited_slots": ["consolidated/current", "consolidated/prior"]}
    assert stored_formulas(two) == {}
    out = reevaluate([_row("a", 10), _row("b", 5), legacy])
    assert out[2]["values"][0]["value"] == "15"


def test_a_formula_that_no_longer_evaluates_is_said_on_the_line(client, template_def):
    """A line it names was cleared: the formula keeps the figure last stored, and the line says
    why rather than showing that figure as the formula's."""
    rows = [_row(_INV, 2200, "Inventories"), _row(_CASH, 100, "Cash")]
    doc_id = _seed(template_def, rows)
    assert _patch(client, doc_id, _TARGET, None, formula=f"={_INV}+{_CASH}")["current"] == 2300
    _patch(client, doc_id, _CASH, None)
    statement = client.get(f"/api/v1/documents/{doc_id}/statement?statement=balance_sheet").json()
    line = next(r for r in statement["rows"] if r.get("id") == _TARGET)
    assert line["v1"] == 2300
    assert "unknown reference" in line["formula_errors"]["current"]


# ── the cached narrative stands only while its figures do ───────────────────────────────────────

def _narrated_run(rows, digest="auto"):
    from app.services.derived import build_credit_analysis

    class _Run:
        result = {"rows": rows, "disclosures": []}

    credit = build_credit_analysis(rows, [], locale="en")
    narrative = {"text": "Liquidity is adequate.", "provider": "p", "model": "m"}
    if digest == "auto":
        narrative["figures_digest"] = credit_figures_digest(credit)
    _Run.result["credit_narrative"] = narrative
    return _Run()


def _credit_rows(current_assets=500, current_liabilities=250, edited=False):
    ca = _row("bs_current_assets__total_current_assets", current_assets)
    if edited:
        ca = {**ca, "edited": True, "edited_slots": ["consolidated/current"]}
    return [ca, _row("bs_current_liabilities__total_current_liabilities", current_liabilities)]


def test_the_narrative_is_withheld_once_the_figures_it_was_written_from_change():
    from app.services.derived import build_credit_analysis

    run = _narrated_run(_credit_rows())
    assert build_credit_analysis(_credit_rows(), [], locale="en")["factors"], \
        "the fixture must give the credit view a figure to write about"
    assert _narrative_in_force(run, _credit_rows())[0]["text"] == "Liquidity is adequate."
    # The same figures read in another language: the narrative stands.
    zh = build_credit_analysis(_credit_rows(), [], locale="zh")
    assert _narrative_in_force(run, _credit_rows(), credit=zh)[0] is not None
    # A current-assets edit moves the current ratio: withheld, and said.
    assert _narrative_in_force(run, _credit_rows(900, edited=True)) == (None, True)


def test_a_narrative_stored_without_its_figures_is_withheld_after_any_edit():
    run = _narrated_run(_credit_rows(), digest=None)
    assert _narrative_in_force(run, _credit_rows())[0] is not None
    assert _narrative_in_force(run, _credit_rows(edited=True)) == (None, True)


def test_the_analysis_route_says_the_narrative_was_withheld(client, template_def):
    from app.services.derived import build_credit_analysis

    rows = _credit_rows()
    credit = build_credit_analysis(rows, [], locale="en", template_def=template_def)
    doc_id = _seed(template_def, rows, extra={
        "disclosures": [],
        "credit_narrative": {"text": "Liquidity is adequate.", "provider": "p", "model": "m",
                             "figures_digest": credit_figures_digest(credit)}})
    shown = client.get(f"/api/v1/documents/{doc_id}/analysis").json()["credit"]
    assert shown["narrative"]["text"] == "Liquidity is adequate."
    _patch(client, doc_id, "bs_current_assets__total_current_assets", 900)
    after = client.get(f"/api/v1/documents/{doc_id}/analysis").json()["credit"]
    assert "narrative" not in after and after["narrative_withdrawn"] is True
    exported = json.loads(client.get(f"/api/v1/documents/{doc_id}/export?fmt=json").content)
    assert "Liquidity is adequate." not in json.dumps(exported, ensure_ascii=False)


# ── every new sentence, in every locale ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("locale", LOCALES)
def test_the_new_review_sentences_are_translated(locale):
    for sentence in (_EDITED_INPUT_LIVE, _REMAPPED_INPUT_LIVE, _GUARD_FIX_LIVE,
                     "Figures now on the lines it uses"):
        assert _TR[sentence][locale] and _TR[sentence][locale] != sentence


_FRONTEND = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


@pytest.mark.parametrize("key,where", [("cm.creditNarrativeWithdrawn",
                                        "i18n/screens/commentary.ts"),
                                       ("ws.formulaStale", "i18n.ts")])
def test_the_screen_strings_are_in_every_locale(key, where):
    text = (_FRONTEND / where).read_text(encoding="utf-8")
    assert text.count(f'"{key}":') == 4, key
