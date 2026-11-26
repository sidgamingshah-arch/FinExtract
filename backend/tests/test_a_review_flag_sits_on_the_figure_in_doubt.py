"""A REVIEW FLAG SITS ON THE FIGURE IN DOUBT, NOT ON ITS ROW (`services.review_state`).

The user's report: "Make review flags per slot, not per row". A line went to review for BOTH
years when only one year was in doubt. Measured with the scripted stand-in: on 300319 (half run)
the prior-year current-assets reconciliation passes with a 565,202.29 difference while the current
year is 0, yet the CURRENT figures of trade receivables gross 1,060,784,431.16, prepayments
18,226,947.34 and total current assets 3,192,911,850.95 went to review too; on r9 the cash row's
current year 327,254,438.68 went to review for a prior-year difference.

Here: the writers put the flag on the slot (the fill's section difference, the settlement, the
cascades' part in review, the residual gap on the rules route) and a row flag only for a doubt
about the caption; every reader — the queue, the statement grid, the API, the export — asks the one
predicate. The pipeline cases run the REAL pipeline on the filing's own pages with the shipped
stand-in answering from a script; no model is called.
"""
from __future__ import annotations

import io
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.models.reports import RuleResult, StructuralReport
from app.services import review_state as RS
from app.services import whole_pdf_rules as WR
from tests.whole_pdf_standin import (
    HK_SET,
    HK_TEMPLATE,
    TEMPLATES,
    filing_path,
    rows_of,
    run_whole_pdf,
    script_figure,
    standin_module,
)

REVIEW = RS.REVIEW_FLAG
standin = standin_module()


# ── the fill: a section difference in one column is that column's ──────────────────────────────

TCA = "bs_ca__total_current_assets"
GROSS = "bs_ca__trade_receivables_gross"
PREPAY = "bs_ca__prepayments_cp"


def _fill(key: str, figures: dict[str, str], *, slot_flags: dict[str, list[str]] | None = None):
    row = LineItem(source_label=key, canonical_key=key)
    for period, amount in figures.items():
        ev = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period, value=Decimal(amount),
                            value_raw=Decimal(amount))
        ev.confidence.flags.extend([WR.RULES_FLAG, *(slot_flags or {}).get(period, ())])
        row.set_value(ev)
    row.confidence.flags.append(WR.RULES_FLAG)
    row.confidence.method = "alias"
    return row


def _section(name: str, period: str, difference, status="pass", basis="consolidated",
             components=(GROSS, PREPAY)):
    details = {"section": name, "target": f"{name}__total", "components": list(components)}
    if status != "skipped":
        details.update(basis=basis, period_label=period)
    return RuleResult(rule_id=f"section_reconciliation:{name}", kind="section_reconciliation",
                      status=status, scope_key=f"{basis}/{period}",
                      difference=None if difference is None else Decimal(difference),
                      details=details)


def _slot(row, period, basis="consolidated"):
    return RS.slot_of(row, basis, period)


def test_a_prior_only_difference_puts_the_prior_fill_figure_in_review_and_not_the_current():
    """The half/300319 shape: the output's bs_ca check is 0.00 in the current column and
    565,202.29 in the prior one. Only the prior slots of the filled rows go to review; the rows
    carry the breadcrumb that names the slot, and no review flag."""
    gross = _fill(GROSS, {"current": "1060784431.16", "prior": "980000000.00"})
    prepay = _fill(PREPAY, {"current": "18226947.34", "prior": "17000000.00"})
    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [gross, prepay]
    report = StructuralReport(results=[_section("bs_ca", "current", "0.00"),
                                       _section("bs_ca", "prior", "565202.29")])
    ctx = SimpleNamespace(log=lambda _m: None)
    assert WR.review_beside_a_difference(doc, report, ctx) == 2
    flag = f"{WR.SECTION_DIFFERS_FLAG}:bs_ca:consolidated:prior:565202.29"
    for row in (gross, prepay):
        assert RS.in_review(row, "consolidated", "prior")
        assert not RS.in_review(row, "consolidated", "current")
        assert flag in _slot(row, "prior").confidence.flags
        assert flag in row.confidence.flags and REVIEW not in row.confidence.flags


def test_a_line_computed_from_the_clean_year_of_a_filled_row_is_not_put_in_review():
    """`note_sourced._carry_review` asks the part's SAME slot: the current figure computed from
    the prepayments fill row is clean, its prior figure rests on a slot in review."""
    from app.stages.note_sourced import _carry_review

    prepay = _fill(PREPAY, {"current": "18226947.34", "prior": "17000000.00"},
                   slot_flags={"prior": [REVIEW]})
    total = LineItem(source_label=TCA, canonical_key=TCA)
    for period in ("current", "prior"):
        total.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                       value=Decimal(1), value_raw=Decimal(1)))
    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [prepay, total]
    got = SimpleNamespace(inputs=[{"ref": PREPAY}])
    ctx = SimpleNamespace(log=lambda _m: None)
    for period in ("current", "prior"):
        _carry_review(doc, got, total, "consolidated", period, ctx, where="t")
    assert not any(f.startswith("part_in_review:") for f in _slot(total, "current").confidence.flags)
    assert f"part_in_review:{PREPAY}" in _slot(total, "prior").confidence.flags
    assert f"part_in_review:{PREPAY}:consolidated:prior" in total.confidence.flags
    assert REVIEW not in total.confidence.flags


def test_a_model_row_holding_a_computed_figure_keeps_its_other_slot_out_of_review():
    """`carriers[0]` can be a model row holding the line's other year: the computed slot goes to
    review, the model's own figure beside it does not."""
    from app.stages.note_sourced import _carry_review

    part = _fill(PREPAY, {"current": "5", "prior": "6"}, slot_flags={"current": [REVIEW]})
    model = LineItem(source_label=TCA, canonical_key=TCA)
    for period in ("current", "prior"):
        ev = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period, value=Decimal(9),
                            value_raw=Decimal(9))
        ev.confidence.flags.append("whole_pdf")
        model.set_value(ev)
    model.confidence.flags.append("whole_pdf")
    model.confidence.method = "llm"
    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [part, model]
    _carry_review(doc, SimpleNamespace(inputs=[{"ref": PREPAY}]), model, "consolidated",
                  "current", SimpleNamespace(log=lambda _m: None), where="t")
    assert RS.in_review(model, "consolidated", "current")
    assert not RS.in_review(model, "consolidated", "prior")
    assert REVIEW not in model.confidence.flags


def test_the_rules_gap_on_a_filled_figure_is_answered_by_the_outputs_own_check():
    """The rules' copy records its residual gap on the slot it names, without review
    (`ctx.rules_copy`). The output's own check of that section and column answers it: 0.00 —
    a breadcrumb, no review; a difference — in review for it; not checked (a skipped section,
    or none) — in review, saying so."""
    gap = "unallocated_gap:{}:consolidated|{}=473252479"
    tca = _fill(TCA, {"current": "3192911850.95", "prior": "3100000000.00"},
                slot_flags={"current": [gap.format("bs_ca", "current")],
                            "prior": [gap.format("bs_ca", "prior")]})
    fin = _fill("cf_financing__cash_flows_from_financ_activities", {"current": "-5"},
                slot_flags={"current": [gap.format("cf_financing", "current")]})
    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [tca, fin]
    report = StructuralReport(results=[
        _section("bs_ca", "current", "0.00", components=(TCA,)),
        _section("bs_ca", "prior", "565202.29", components=()),
        _section("cf_financing", "current", None, status="skipped")])
    WR.review_beside_a_difference(doc, report, SimpleNamespace(log=lambda _m: None))
    cur, prior = _slot(tca, "current"), _slot(tca, "prior")
    assert f"{WR.GAP_ANSWERED_FLAG}:bs_ca:consolidated:current" in cur.confidence.flags
    assert not RS.in_review(tca, "consolidated", "current")
    # The prior column differs; the total is not among its check's components, and is still
    # in review for the gap the difference leaves unanswered.
    assert RS.in_review(tca, "consolidated", "prior")
    assert any(f.startswith(f"{WR.SECTION_DIFFERS_FLAG}:bs_ca:consolidated:prior:")
               for f in prior.confidence.flags)
    # A section the output did not check in that column: the doubt stands, on its slot.
    assert RS.in_review(fin, "consolidated", "current")
    assert (f"{WR.GAP_UNCHECKED_FLAG}:cf_financing:consolidated:current"
            in _slot(fin, "current").confidence.flags)
    assert REVIEW not in tca.confidence.flags and REVIEW not in fin.confidence.flags


def test_the_residual_plug_goes_to_review_on_its_column_only():
    """`stages.residual._column_to_review`, the plug's path as the gap's: the slot of the column
    the plug names, never the row."""
    from app.stages.residual import _column_to_review

    row = LineItem(source_label="Others", canonical_key="bs_ca__other_current_assets")
    for basis in (Basis.CONSOLIDATED, Basis.STANDALONE):
        row.set_value(ExtractedValue(basis=basis, period_label="current", value=Decimal(5),
                                     value_raw=Decimal(5)))
    plug = "residual_plug_suspected:bs_ca:standalone|current=5"
    row.confidence.flags.append(plug)
    _column_to_review(SimpleNamespace(), row, plug, "standalone|current")
    assert RS.in_review(row, "standalone", "current")
    assert not RS.in_review(row, "consolidated", "current")
    assert REVIEW not in row.confidence.flags


# ── the real pipeline on a real filing's pages ─────────────────────────────────────────────────

MONTAGE = "688008_montage_2024_prc"
MONTAGE_PAGES = [148, 149, 150, 181, 182, 183]
ALLOWANCE = "bs_ca__allow_for_doubtful_accounts"


def _present(stem):
    return pytest.mark.skipif(not filing_path(stem).exists(), reason=f"{stem}.pdf not present")


@pytest.fixture(scope="module")
def montage_one_year(tmp_path_factory):
    """688008 (澜起科技): the model gives the allowance for doubtful accounts 1,944,451.99 (note 七、5,
    p184) for the CURRENT year only and leaves the gross out; the settlement against the model's
    pieces is switched off, so the current gross is filled with the face net 387,791,885.96 and
    current assets come out 1,944,451.99 short. The prior year is the rules' own reading."""
    from app.api.routes.extractions import _serialize_rows
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import load_template
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads((TEMPLATES / HK_SET).read_text(encoding="utf-8")),
                             resolve=True)
    with pytest.MonkeyPatch.context() as mp:
        settings = get_settings()
        mp.setattr(settings.extraction, "whole_pdf_to_model", False)
        mp.setattr(settings.llm, "provider", "stub")
        tpl = load_template(json.loads((TEMPLATES / HK_TEMPLATE).read_text(encoding="utf-8")))
        rules_doc, _ctx = run_extraction(
            filing_path(MONTAGE).read_bytes(), filename=f"{MONTAGE}.pdf",
            ontology=build_working_view(cfg), template=tpl, line_items=cfg,
            included_pages=set(MONTAGE_PAGES))
    script = standin.whole_pdf_script_from_result({"rows": _serialize_rows(rules_doc)}, cfg,
                                                  leave_out=[GROSS])
    script["lines"][ALLOWANCE] = [script_figure("1944451.99", page=184, caption="坏账准备")]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(WR, "settle_against_model_pieces", lambda doc, ctx: {})
        return run_whole_pdf(mp, tmp_path_factory.mktemp("montage_one_year"), MONTAGE, script,
                             pages=MONTAGE_PAGES)


def _filled(doc, key):
    return next((r for r in rows_of(doc, key)
                 if WR.RULES_FLAG in r.confidence.flags and not WR.is_model_row(r)), None)


@_present(MONTAGE)
def test_on_688008_only_the_year_whose_section_differs_is_in_review(montage_one_year):
    doc, _ctx, _rec = montage_one_year
    results = {r.scope_key: r for r in doc.structural.results
               if r.rule_id == "section_reconciliation:bs_ca"}
    assert results["consolidated/current"].difference == Decimal("1944451.99")
    assert results["consolidated/prior"].difference == 0
    gross = _filled(doc, GROSS)
    assert gross is not None
    assert _slot(gross, "current").value == Decimal("387791885.96")
    assert RS.in_review(gross, "consolidated", "current")
    assert not RS.in_review(gross, "consolidated", "prior")
    assert REVIEW not in gross.confidence.flags
    # Served: the line is in the queue for ONE figure.
    from app.api.routes.extractions import _serialize_rows

    served = next(r for r in _serialize_rows(doc) if r["canonical_key"] == GROSS
                  and WR.RULES_FLAG in r["flags"])
    assert RS.row_review(served, 0.8) == {"row": False, "slots": ["consolidated/current"]}


# ── the readers: queue, statement grid, edits, remap, export ───────────────────────────────────

CUR_TAX = "bs_ca__current_tax_receivables"      # a whole-PDF model row, one year in doubt
LEASE = "bs_ca__operating_lease_receivables"     # a rules row whose caption is in doubt
RAW = "bs_ca__raw_materials"                     # clean
WIP = "bs_ca__wip"                               # a blank line with a row doubt

_DIFFERS = f"{WR.SECTION_DIFFERS_FLAG}:bs_ca:consolidated:prior:565202.29"


def _value(period, amount, flags=(), mapping=1.0, y=0.3):
    return {"basis": "consolidated", "period_label": period, "value": amount, "scale": "1",
            "provenance": {"source_kind": "native", "page_index": 0,
                           "label_bbox": {"x0": 0.1, "y0": y, "x1": 0.4, "y1": y + 0.017}},
            "confidence": {"mapping": mapping, "flags": list(flags)}}


def _rows():
    return [
        {"canonical_key": CUR_TAX, "source_label": "Tax recoverable", "printed_in": "face",
         "mapping_method": "llm", "mapping_confidence": 0.9, "flags": ["whole_pdf"],
         "values": [_value("current", "100", ["whole_pdf", "whole_pdf:not_found", REVIEW], 0.3,
                           y=0.1),
                    _value("prior", "90", ["whole_pdf", "whole_pdf:on_page"], 0.9, y=0.1)]},
        {"canonical_key": PREPAY, "source_label": "Prepayments", "printed_in": "face",
         "mapping_method": "alias", "mapping_confidence": 0.95,
         "flags": [WR.RULES_FLAG, _DIFFERS],
         "values": [_value("current", "18226947.34", [WR.RULES_FLAG], 0.95, y=0.2),
                    _value("prior", "17000000", [WR.RULES_FLAG, _DIFFERS, REVIEW], 0.95,
                           y=0.2)]},
        {"canonical_key": LEASE, "source_label": "Lease receivables", "printed_in": "face",
         "mapping_method": "fuzzy", "mapping_confidence": 0.6, "flags": [REVIEW],
         "values": [_value("current", "40", (), 0.6, y=0.3), _value("prior", "30", (), 0.6,
                                                                     y=0.3)]},
        {"canonical_key": RAW, "source_label": "Raw materials", "printed_in": "face",
         "mapping_method": "alias", "mapping_confidence": 0.98, "flags": [],
         "values": [_value("current", "7", (), 0.98, y=0.4), _value("prior", "6", (), 0.98,
                                                                    y=0.4)]},
        {"canonical_key": WIP, "source_label": "Work in progress", "printed_in": "other",
         "mapping_method": "supplemental", "mapping_confidence": 0.0,
         "flags": ["not_determined", REVIEW], "values": []},
    ]


@pytest.fixture(scope="module")
def template_def() -> dict:
    return json.loads((TEMPLATES / HK_TEMPLATE).read_text(encoding="utf-8"))


def _seed(template_def: dict, rows: list[dict]) -> str:
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    with SessionLocal() as s:
        doc = Document(filename="slot.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin")
        s.add(doc)
        s.flush()
        tv = TemplateVersion(template_key=f"slot-{uuid.uuid4().hex[:8]}", name="S", version=1,
                             definition=template_def)
        s.add(tv)
        s.flush()
        s.add(ExtractionRun(document_id=doc.id, status="succeeded",
                            options={"template_version_id": tv.id},
                            result={"rows": rows, "filename": "slot.pdf", "pages": []}))
        s.commit()
        return doc.id


def _review(client, doc_id):
    r = client.get(f"/api/v1/documents/{doc_id}/review")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_queue_counts_lines_and_the_figures_in_review(client, template_def):
    """A line one year of which is in doubt is one line and ONE figure; a caption doubt is one
    line and every figure; a blank line with a row doubt is one line and no figure."""
    doc_id = _seed(template_def, _rows())
    review = _review(client, doc_id)
    assert review["weak_mappings"] == 4                    # CUR_TAX, PREPAY, LEASE, WIP
    assert review["weak_slots"] == 1 + 1 + 2 + 0


def test_the_run_serves_each_figures_review_state(client, template_def):
    doc_id = _seed(template_def, _rows())
    rows = {r["canonical_key"]: r for r in
            client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]}
    assert rows[CUR_TAX]["review_slots"] == ["consolidated/current"]
    assert rows[CUR_TAX]["review_row"] is False
    cur, prior = rows[CUR_TAX]["values"]
    assert cur["in_review"] and cur["review_reasons"][:1] == [RS.FIGURE]
    assert "whole_pdf:not_found" in cur["review_reasons"]
    assert prior["in_review"] is False
    assert rows[PREPAY]["review_slots"] == ["consolidated/prior"]
    assert rows[LEASE]["review_row"] is True
    assert rows[LEASE]["review_slots"] == ["consolidated/current", "consolidated/prior"]
    assert rows[WIP]["review_row"] is True and rows[WIP]["review_slots"] == []
    # What a row-level display shows: the weakest figure's own score; the stored score as stored.
    assert rows[CUR_TAX]["shown_confidence"] == 0.3 and rows[CUR_TAX]["mapping_confidence"] == 0.9
    assert rows[LEASE]["shown_confidence"] == 0.6
    # Derived on the serve, never stored.
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as s:
        stored = s.query(ExtractionRun).filter(ExtractionRun.document_id == doc_id).one().result
    assert all("review_slots" not in r for r in stored["rows"])


def test_the_statement_grid_marks_the_column_in_review(client, template_def):
    doc_id = _seed(template_def, _rows())
    st = client.get(f"/api/v1/documents/{doc_id}/statement",
                    params={"statement": "balance_sheet", "basis": "consolidated"}).json()
    by_id = {r["id"]: r for r in st["rows"]}
    prepay = by_id[PREPAY]
    assert prepay["review1"] is False and prepay["review2"] is True
    assert _DIFFERS in prepay["review_why"]["prior"] and "current" not in prepay["review_why"]
    tax = by_id[CUR_TAX]
    assert tax["review1"] is True and tax["review2"] is False
    assert tax["confidence1"]["pct"] == 30 and tax["confidence2"]["pct"] == 90
    # The row's one pill shows its weakest figure (`review_state.shown_score`), as before the
    # review was per slot, though the stored row score is its best figure's.
    assert tax["confidence"]["pct"] == 30
    lease = by_id[LEASE]
    assert lease["review1"] is True and lease["review2"] is True
    assert RS.CAPTION in lease["review_why"]["current"]
    raw = by_id[RAW]
    assert raw["review1"] is False and raw["review2"] is False and raw["review_why"] is None


def _patch(client, doc_id, key, value, period="current"):
    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/{key}",
                     json={"value": value, "formula": "", "basis": "consolidated",
                           "period": period, "comment": ""})
    assert r.status_code == 200, r.text


def test_an_edit_answers_the_figures_doubt_and_a_revert_brings_it_back(client, template_def):
    doc_id = _seed(template_def, _rows())
    before = _review(client, doc_id)
    _patch(client, doc_id, CUR_TAX, 101)
    after = _review(client, doc_id)
    assert after["weak_slots"] == before["weak_slots"] - 1
    assert after["weak_mappings"] == before["weak_mappings"] - 1      # nothing else in review
    # A caption doubt is not answered by typing over a figure.
    _patch(client, doc_id, LEASE, 41)
    assert _review(client, doc_id)["weak_slots"] == after["weak_slots"]
    r = client.delete(f"/api/v1/documents/{doc_id}/line-items/{CUR_TAX}")
    assert r.status_code == 200, r.text
    reverted = _review(client, doc_id)
    assert reverted["weak_slots"] == before["weak_slots"]
    assert reverted["weak_mappings"] == before["weak_mappings"]


def test_a_save_that_keeps_the_figure_does_not_take_it_out_of_review(client, template_def):
    """What the Workspace's Save sends for a note typed beside a doubtful figure, or for Save
    pressed with nothing retyped: the SAME figure, for the period on show. The slot is recorded in
    `edited_slots`, but the figure is still the machine's, and so is its doubt."""
    doc_id = _seed(template_def, _rows())
    before = _review(client, doc_id)
    for comment in ("not sure - check note 12", ""):
        r = client.patch(f"/api/v1/documents/{doc_id}/line-items/{CUR_TAX}",
                         json={"value": 100, "formula": "", "basis": "consolidated",
                               "period": "current", "comment": comment})
        assert r.status_code == 200, r.text
        after = _review(client, doc_id)
        assert (after["weak_slots"], after["weak_mappings"]) == \
            (before["weak_slots"], before["weak_mappings"]), comment
        row = {x["canonical_key"]: x for x in client.get(
            f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]}[CUR_TAX]
        assert row["edited_slots"] == ["consolidated/current"]
        assert row["values"][0]["in_review"] is True
        tax = {x["id"]: x for x in client.get(
            f"/api/v1/documents/{doc_id}/statement",
            params={"statement": "balance_sheet", "basis": "consolidated"}).json()["rows"]}[CUR_TAX]
        assert tax["review1"] is True
    # Retyping the figure is what answers it.
    _patch(client, doc_id, CUR_TAX, 101)
    assert _review(client, doc_id)["weak_slots"] == before["weak_slots"] - 1


def _remap_target(template_def, taken):
    from app.api.routes.documents import _remap_targets

    return next(t["canonical_key"] for t in _remap_targets(template_def, "en")
                if t["canonical_key"].startswith("bs_ca__") and t["canonical_key"] not in taken)


def test_a_remap_answers_the_caption_and_keeps_a_model_figures_own_doubt(client, template_def):
    from app.api.routes.documents import _row_ref

    rows = _rows()
    doc_id = _seed(template_def, rows)
    taken = {r["canonical_key"] for r in rows}
    target = _remap_target(template_def, taken)
    r = client.post(f"/api/v1/documents/{doc_id}/review/remap",
                    json={"row_ref": _row_ref(rows[0]), "canonical_key": target, "reason": "t"})
    assert r.status_code == 200, r.text
    served = {x["canonical_key"]: x for x in
              client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]}
    moved = served[target]
    cur = next(v for v in moved["values"] if v["period_label"] == "current")
    assert REVIEW in cur["confidence"]["flags"] and cur["confidence"]["mapping"] == 0.3
    assert moved["review_slots"] == ["consolidated/current"]
    # A rules row's slot doubts go with the old mapping, as before.
    r = client.post(f"/api/v1/documents/{doc_id}/review/remap",
                    json={"row_ref": _row_ref(rows[1]),
                          "canonical_key": _remap_target(template_def, taken | {target}),
                          "reason": "t"})
    assert r.status_code == 200, r.text
    served = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    moved = next(x for x in served if x["source_label"] == "Prepayments")
    assert moved["review_slots"] == []
    assert all(REVIEW not in v["confidence"]["flags"] for v in moved["values"])


def test_the_export_carries_each_figures_review_state(client, template_def):
    import openpyxl

    doc_id = _seed(template_def, _rows())
    data = json.loads(client.get(f"/api/v1/documents/{doc_id}/export?fmt=json").content)
    prepay = next(r for r in data["line_items"] if r["canonical_key"] == PREPAY)
    assert prepay["review_slots"] == ["consolidated/prior"] and prepay["review_row"] is False
    values = {v["period"]: v for v in prepay["values"]}
    assert values["current"]["in_review"] is False and values["current"]["review_reasons"] == []
    assert values["prior"]["in_review"] is True and _DIFFERS in values["prior"]["review_reasons"]
    # A row-level score is the line's weakest figure's, where a figure carries its own; the stored
    # row score of a model row is its best figure's (0.9), and the doubtful year would vanish.
    tax = next(r for r in data["line_items"] if r["canonical_key"] == CUR_TAX)
    assert tax["mapping_confidence"] == 0.3
    lease = next(r for r in data["line_items"] if r["canonical_key"] == LEASE)
    assert lease["mapping_confidence"] == 0.6                   # a rules row: its own score

    flat = openpyxl.load_workbook(io.BytesIO(client.get(
        f"/api/v1/documents/{doc_id}/export?fmt=excel&layout=flat").content))
    ws = flat.active
    headers = [c.value for c in ws[1]]
    assert headers == ["#", "Line item", "Note", "Mapped to", "Method", "Confidence",
                       "Current", "Prior", "Formula", "Source"]           # columns unmoved
    line = next(r for r in ws.iter_rows(min_row=2) if r[3].value == PREPAY)
    assert line[6].comment is None
    assert next(r for r in ws.iter_rows(min_row=2) if r[3].value == CUR_TAX)[5].value == "30%"
    assert line[7].comment is not None and line[7].comment.text.startswith("In review")

    book = openpyxl.load_workbook(io.BytesIO(client.get(
        f"/api/v1/documents/{doc_id}/export?fmt=excel&layout=statement").content))
    notes = [c.comment.text for ws in book.worksheets for row in ws.iter_rows()
             for c in row if c.comment is not None]
    assert any("In review [consolidated/prior]" in n and _DIFFERS in n for n in notes)
    assert not any("In review [consolidated/current]" in n and "Prepayments" in n for n in notes)
    # The statement workbook's Conf column: the weakest figure's score, as before.
    confs = {c.value: row for ws in book.worksheets for row in ws.iter_rows() for c in row
             if isinstance(c.value, str)}
    tax_row = next(row for label, row in confs.items() if "tax" in label.lower()
                   and any(isinstance(c.value, (int, float)) and c.value == 100 for c in row))
    assert "30%" in [c.value for c in tax_row]


# ── the limit of the inherited gap: it goes to review after the cascades have run ──────────────

def test_no_cascade_term_names_a_line_that_can_carry_a_residual_gap():
    """A FILLED FIGURE'S INHERITED RULES GAP GOES TO REVIEW IN `stages.structural`
    (`whole_pdf_rules.review_beside_a_difference`), AFTER the cascades of `stages.note_sourced`
    have asked whether their terms are in review. Before the review was per slot the rules' copy
    wrote a bare row flag that the cascades saw; now a line computed from such a figure would not
    be put in review. That is safe only while no cascade (or `terms` line) takes a figure that can
    carry a gap or a plug: a section's subtotals, the total that sums its residual, and the
    residual itself (`stages.residual`). This fails the day one does — order the inherited gap's
    review before the cascades, or carry it in `review_state.in_review`, before adding that term."""
    from app.schemas.line_items import load_line_item_set
    from app.services.rollups import calculated_nodes, section_members
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads((TEMPLATES / HK_SET).read_text(encoding="utf-8")),
                             resolve=True)
    members = section_members(build_working_view(cfg))
    carriers = {k for m in members.values() for k in (*m.subtotals, *m.residuals)}
    residuals = {k for m in members.values() for k in m.residuals}
    template = json.loads((TEMPLATES / HK_TEMPLATE).read_text(encoding="utf-8"))
    for key, node in calculated_nodes(template).items():
        if residuals & set((node.get("rollup") or {}).get("children") or ()):
            carriers.add(key)
    assert {"bs_ca__total_current_assets", "bs_ca__other_current_assets"} <= carriers  # it reads
    refs = {}
    for item in cfg.items:
        terms = [t for rung in (item.cascade or ()) for t in (rung.terms or ())]
        # A line's plain `terms` carry review only where `note_sourced` evaluates them: an
        # off-template internal line (`_fill_childless_internal`). A statement subtotal's terms are
        # the template's rollup (`services.rollups.evaluate`), which carries no review, as before.
        if str(item.namespace or "") == "internal" and not item.in_output:
            terms += list(item.terms or ())
        for t in terms:
            if getattr(t, "ref", None):
                refs.setdefault(t.ref, item.key)
    assert refs, "no cascade term was read — the guard would pass on nothing"
    named = {ref: by for ref, by in refs.items() if ref in carriers}
    assert named == {}, f"cascade terms that can carry a residual gap or plug: {named}"
