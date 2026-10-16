"""The checks a reviewer needs to see: both columns, assumed zeros, signs from evidence, swept rows,
and two readings of one figure that disagree.

Five gaps in what the review queue and the coverage band showed, each a figure published with
nothing on screen to question it:

* the calculated cross-check compared printed subtotals with their components for the CURRENT
  column only, so a prior-year income statement or cash flow was checked by nothing at all;
* a structural relation that passed by taking absent components as zero said so in
  ``details.assumed_zero`` and nowhere a person could read, and coverage counted it as a plain pass;
* every plain sum on the income statement and the cash flow was skipped because the rulebook
  declares those sections `either` — including on a filing that prints its costs in parentheses,
  and including the cash roll-forward;
* a row the residual stage swept into an Others line raised no card: the section still ties;
* the model's figure standing against a different note reading, or in place of the figure printed
  on the face, was a row flag nothing read.
"""
from __future__ import annotations

from tests.run_pins import run_body
import copy
import json
import pathlib
import time
import uuid
from decimal import Decimal

import pytest

from app.api.routes.documents import (
    _TR, _build_review, _coverage_block, _structural_checks, _row_ref)
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, LineRole, PrintedIn
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_template
from app.services import coverage as cov_mod
from app.services.coverage import coverage
from app.services.structural_checks import evaluate_structure
from app.services.working_view import build_working_view
from app.stages.residual import ResidualStage

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
LOCALES = ("zh", "ar", "fr")


@pytest.fixture(scope="module")
def hk_template_def() -> dict:
    return json.loads((SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hk_template(hk_template_def):
    return load_template(copy.deepcopy(hk_template_def))


@pytest.fixture(scope="module")
def hk_view():
    """The shipped HK line-item set as the run reads it: the matcher's working view."""
    raw = json.loads((SAMPLES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    return build_working_view(load_line_item_set(raw, resolve=True))


def _prov(page: int) -> dict:
    return {"source_kind": "native", "page_index": page,
            "bbox": {"x0": 0.6, "y0": 0.2 + page / 100, "x1": 0.7, "y1": 0.21 + page / 100}}


def _v(period, value, *, basis="consolidated", page=3, flags=()):
    return {"basis": basis, "period_label": period, "value": str(value),
            "provenance": _prov(page), "confidence": {"flags": list(flags)}}


def _row(key, label, cur=None, prior=None, *, method="exact", flags=(), page=3, **kw):
    vals = []
    if cur is not None:
        vals.append(_v("current", cur, page=page, **kw))
    if prior is not None:
        vals.append(_v("prior", prior, page=page, **kw))
    return {"canonical_key": key, "source_label": label, "values": vals, "printed_in": "face",
            "mapping_method": method, "mapping_confidence": 1.0, "flags": list(flags)}


# ── R2.1 the calculated cross-check runs for both columns ────────────────────────────────────

_BS = {
    "schema_version": 1, "template_key": "t", "name": "T",
    "statements": [{"type": "balance_sheet", "sections": [
        {"node_id": "sec", "canonical_key": "bs_ca", "label": "Current assets", "role": "header",
         "children": [
             {"node_id": "n1", "canonical_key": "bs_ca__inventories", "label": "Inventories",
              "role": "line", "rollup": None},
             {"node_id": "n2", "canonical_key": "bs_ca__cash", "label": "Cash", "role": "line",
              "rollup": None},
             {"node_id": "n3", "canonical_key": "bs_ca__others", "label": "Others",
              "role": "line", "rollup": None},
             {"node_id": "n4", "canonical_key": "bs_ca__total", "label": "Total current assets",
              "role": "subtotal",
              "rollup": {"op": "sum", "children": ["bs_ca__inventories", "bs_ca__cash",
                                                   "bs_ca__others"]}},
         ]},
    ]}],
}


def test_a_prior_year_subtotal_its_components_do_not_come_to_raises_a_card():
    """The current column agrees; last year's printed total is 888 against components of 110."""
    rows = [_row("bs_ca__inventories", "Inventories", 100, 90),
            _row("bs_ca__cash", "Cash", 30, 20),
            _row("bs_ca__total", "Total current assets", 130, 888)]
    review = _build_review(rows, "f.pdf", "en", template_def=_BS)
    cards = {c["id"]: c for c in review["checks"] if c["type"] == "calculated_mismatch"}
    assert list(cards) == ["chk-calc-consolidated-prior-bs_ca__total"]
    card = cards["chk-calc-consolidated-prior-bs_ca__total"]
    assert card["subject"] == {"k": "calculated_mismatch", "key": "bs_ca__total",
                               "basis": "consolidated", "period": "prior"}
    assert card["evidence"]["reported"] == 888 and card["evidence"]["computed"] == 110
    assert card["where"].endswith("consolidated/prior")


def test_the_two_columns_are_two_findings_with_two_identities():
    """Both columns break; each is its own card, so accepting one never covers the other. The
    current column keeps the id it always had."""
    rows = [_row("bs_ca__inventories", "Inventories", 100, 90),
            _row("bs_ca__cash", "Cash", 30, 20),
            _row("bs_ca__total", "Total current assets", 999, 888)]
    cards = [c for c in _build_review(rows, "f.pdf", "en", template_def=_BS)["checks"]
             if c["type"] == "calculated_mismatch"]
    assert [c["id"] for c in cards] == ["chk-calc-consolidated-bs_ca__total",
                                        "chk-calc-consolidated-prior-bs_ca__total"]
    assert len({c["subject_key"] for c in cards}) == 2


# ── R2.2 a pass on assumed zeros is shown, and counted apart ─────────────────────────────────

def _result(rule, status, *, zeros=(), statement="balance_sheet", target="bs_ca__total",
            components=("bs_ca__inventories", "bs_ca__cash", "bs_ca__others"),
            actual="130", expected="130"):
    return {"rule_id": rule, "kind": "rollup", "scope_key": "consolidated/current",
            "status": status, "expected": expected, "actual": actual,
            "difference": str(Decimal(actual) - Decimal(expected)),
            "details": {"target": target, "components": list(components), "op": "sum",
                        "statement": statement, "basis": "consolidated",
                        "period_label": "current", "severity": "blocking",
                        "component_values": {c: "0" if c in zeros else "65" for c in components},
                        "assumed_zero": list(zeros)}}


def test_coverage_counts_a_pass_on_assumed_zeros_apart_and_never_calls_it_fully_verified():
    report = coverage([_result("rollup:a", "pass", zeros=("bs_ca__others",)),
                       _result("rollup:b", "pass")])
    bs = report.statements["balance_sheet"]
    assert (bs.buckets.passed, bs.buckets.passed_assumed_zero) == (2, 1)
    # Every relation ran and held, and one of them only on a line taken as nil.
    assert bs.coverage_rate == 1.0
    assert bs.status == cov_mod.PARTIAL and bs.status != cov_mod.PASSED
    assert report.as_dict()["aggregate"]["passed_assumed_zero"] == 1
    assert "pass_assumed_zero=1" in report.headline()
    # Without the assumed zero, the same run is fully verified.
    assert coverage([_result("rollup:b", "pass")]).statements["balance_sheet"].status \
        == cov_mod.PASSED


def test_a_failed_relation_names_its_assumed_zeros_on_the_card_and_in_what_was_judged():
    res = _result("rollup:a", "fail", zeros=("bs_ca__others",), actual="200", expected="130")
    card, = _structural_checks([res], "en", covered=set(), rows=[], template_def=_BS)
    assert card["assumed_zero"] == [{"key": "bs_ca__others", "label": "Others"}]
    assert card["assumed_zero_note"] == "Assumed zero: Others"
    assert card["evidence"]["assumed_zero"] == ["bs_ca__others"]
    # A card with no assumed zero keeps the evidence it was accepted under.
    plain, = _structural_checks([_result("rollup:b", "fail", actual="200", expected="130")], "en",
                                covered=set(), rows=[], template_def=_BS)
    assert "assumed_zero" not in plain["evidence"] and plain["assumed_zero_note"] == ""


def test_a_long_list_of_assumed_zeros_names_the_first_and_counts_the_rest():
    from app.api.routes.documents import _assumed_zero_note

    keys = [f"k{i}" for i in range(9)]
    assert _assumed_zero_note(keys, {}, "en") == "Assumed zero: k0, k1, k2, k3, k4, k5 … +3"


@pytest.mark.parametrize("locale", LOCALES)
def test_the_assumed_zero_sentence_is_in_every_locale(locale):
    res = _result("rollup:a", "fail", zeros=("bs_ca__others",), actual="200", expected="130")
    card, = _structural_checks([res], locale, covered=set(), rows=[], template_def=_BS)
    assert card["assumed_zero_note"] == f"{_TR['Assumed zero'][locale]}: Others"


def test_the_coverage_block_lists_every_pass_on_assumed_zeros():
    class _Run:
        id, engine_version = "run-1", "test"
        options = {"template_version_id": "tv-1"}
        template_version_id = "tv-1"
        result = {"structural": [_result("rollup:a", "pass", zeros=("bs_ca__others",)),
                                 _result("rollup:b", "pass")]}

    block = _coverage_block(_Run(), _BS, "en")
    assert block["available"] is True
    passes = block["assumed_zero_passes"]
    assert [(p["rule_id"], p["target_label"], p["note"]) for p in passes] == [
        ("rollup:a", "Total current assets", "Assumed zero: Others")]
    assert block["aggregate"]["passed_assumed_zero"] == 1


@pytest.mark.parametrize("locale", ("en", *LOCALES))
def test_the_export_caption_says_a_pass_rested_on_assumed_zeros(locale):
    """Every relation ran and held, one of them on a line taken as nil: the workbook must not read
    "the rest were not evaluable" (nothing was left), nor be silent as for a full pass."""
    from app.services.export import _EXPORT_TR, validation_caption

    block = {"available": True, "alarms": [], "aggregate": {
        "status": "PARTIAL", "passed": 2, "passed_assumed_zero": 1, "failed": 0,
        "evaluated": 2, "declarable": 2}}
    text, severe = validation_caption(block, locale)
    sentence = "relations held only with lines the filing does not print taken as zero."
    expected = sentence if locale == "en" else _EXPORT_TR[sentence][locale]
    of = "of" if locale == "en" else _EXPORT_TR["of"][locale]
    assert text == f"1 {of} 2 {expected}" and severe is False


# ── R2.3 signs established by evidence, on the shipped HK configuration ──────────────────────

def _items(figures: dict, *, basis=Basis.CONSOLIDATED) -> list[LineItem]:
    out = []
    for key, num in figures.items():
        li = LineItem(source_label=key, canonical_key=key, printed_in=PrintedIn.FACE)
        for period, value in (num.items() if isinstance(num, dict) else (("current", num),)):
            li.set_value(ExtractedValue(value=Decimal(str(value)), value_raw=Decimal(str(value)),
                                        basis=basis, period_label=period))
        out.append(li)
    return out


def _status(report, rule_id):
    res = next(r for r in report.results if r.rule_id == rule_id)
    return res.status, res.details.get("reason")


_PBT = {"is_pl__net_operating_profit": 300, "is_pl__net_interest_income_expense": -50,
        "is_pl__net_other_financial_inc_exp": 0, "is_pl__other_income_expense": 0,
        "is_pl__profit_loss_before_tax": 250}


def test_an_income_statement_that_prints_its_costs_in_parentheses_is_checked(hk_template, hk_view):
    report = evaluate_structure(hk_template, _items({**_PBT, "is_pl__cost_of_sales": -600}),
                                ontology=hk_view)
    assert _status(report, "rollup:is_pl__profit_loss_before_tax") == ("pass", None)


def test_unsigned_costs_still_establish_nothing(hk_template, hk_view):
    """A mainland or Indian income statement prints its costs unsigned: the plain sums stay
    unasserted, as before."""
    report = evaluate_structure(hk_template, _items({**_PBT, "is_pl__cost_of_sales": 600}),
                                ontology=hk_view)
    assert _status(report, "rollup:is_pl__profit_loss_before_tax") == (
        "skipped", "component_signs_not_established")


def test_one_negative_beside_an_unsigned_cost_is_not_evidence(hk_template, hk_view):
    """A CAS statement prints credit-impairment losses with a minus and cost of sales unsigned. The
    one negative does not sign the section."""
    figures = {**_PBT, "is_pl__cost_of_sales": 600, "is_pl__asset_impairment_loss": -10}
    report = evaluate_structure(hk_template, _items(figures), ontology=hk_view)
    assert _status(report, "rollup:is_pl__profit_loss_before_tax") == (
        "skipped", "component_signs_not_established")


def test_a_negative_row_swept_into_the_section_is_not_evidence(hk_template, hk_view):
    """A row the residual stage placed in the section is there by inference; its sign says nothing
    about how the filing prints the section's costs."""
    unsigned = {**_PBT, "is_pl__net_interest_income_expense": 50,
                "is_pl__profit_loss_before_tax": 350}
    items = _items({**unsigned, "is_pl__other_operating_expenses": -75})
    items[-1].confidence.method = "residual"
    report = evaluate_structure(hk_template, items, ontology=hk_view)
    assert _status(report, "rollup:is_pl__profit_loss_before_tax") == (
        "skipped", "component_signs_not_established")
    items[-1].confidence.method = "exact"
    report = evaluate_structure(hk_template, items, ontology=hk_view)
    assert _status(report, "rollup:is_pl__profit_loss_before_tax") == ("pass", None)


def test_a_sum_that_spends_its_costs_as_magnitudes_needs_no_signs(hk_template, hk_view):
    """Gross profit takes total cost of sales as a magnitude (`cost_magnitude_children`), so the sum
    is the arithmetic however the filing printed the cost. The total of costs itself is not: it is
    printed negative by one filing and unsigned by the next."""
    figures = {"is_pl__sales_revenues": 1000, "is_pl__total_cost_of_sales": 600,
               "is_pl__gross_profit": 400, "is_pl__cost_of_sales": 600}
    report = evaluate_structure(hk_template, _items(figures), ontology=hk_view)
    assert _status(report, "rollup:is_pl__gross_profit") == ("pass", None)
    assert _status(report, "rollup:is_pl__total_cost_of_sales") == (
        "skipped", "component_signs_not_established")


_ROLL_FORWARD = {
    "cf_financing__beg_of_period_cash_and_cash_equiv": 100,
    "cf_oper_indirect__cash_flows_oper_activ_indirect": 500,
    "cf_investing__cash_flows_from_invest_activities": -300,
    "cf_financing__cash_flows_from_finance_activities": -150,
    "cf_financing__net_foreign_exchange_difference": 5,
    "cf_financing__end_of_period_cash_and_cash_equiv": 155,
    "cf_oper_indirect__income_taxes_paid_indirect": -40,
}


def test_the_cash_roll_forward_is_a_shipped_rollup_and_is_checked(hk_template_def, hk_template,
                                                                     hk_view):
    """closing cash = opening + operating + investing + financing + FX is already a rollup of the
    shipped template, so the HK validation block needs no second copy of it. It was skipped on every
    filing; where the cash flow prints its outflows in parentheses it is checked."""
    node = next(n for st in hk_template_def["statements"] for n in _walk(st["sections"])
                if n.get("canonical_key") == "cf_financing__end_of_period_cash_and_cash_equiv")
    assert node["rollup"]["op"] == "sum" and node["rollup"]["children"] == [
        "cf_financing__beg_of_period_cash_and_cash_equiv",
        "cf_oper_indirect__cash_flows_oper_activ_indirect",
        "cf_investing__cash_flows_from_invest_activities",
        "cf_financing__cash_flows_from_finance_activities",
        "cf_financing__net_foreign_exchange_difference"]
    validation = json.loads((SAMPLES / "output_csv_hk_validation.json").read_text("utf-8"))
    assert validation["identities"] == []

    report = evaluate_structure(hk_template, _items(_ROLL_FORWARD), ontology=hk_view)
    assert _status(report, "rollup:cf_financing__end_of_period_cash_and_cash_equiv") == (
        "pass", None)
    # A break in it is now a finding, in either column.
    broken = {**_ROLL_FORWARD,
              "cf_financing__end_of_period_cash_and_cash_equiv": {"current": 155, "prior": 90},
              "cf_financing__beg_of_period_cash_and_cash_equiv": {"current": 100, "prior": 80},
              "cf_oper_indirect__cash_flows_oper_activ_indirect": {"current": 500, "prior": 10},
              "cf_investing__cash_flows_from_invest_activities": {"current": -300, "prior": -5},
              "cf_financing__cash_flows_from_finance_activities": {"current": -150, "prior": -4},
              "cf_financing__net_foreign_exchange_difference": {"current": 5, "prior": 0}}
    report = evaluate_structure(hk_template, _items(broken), ontology=hk_view)
    statuses = {r.scope_key: r.status for r in report.results
                if r.rule_id == "rollup:cf_financing__end_of_period_cash_and_cash_equiv"}
    assert statuses == {"consolidated/current": "pass", "consolidated/prior": "fail"}


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children") or [])


# ── R2.4 a swept row gets a card; a printed Others line does not ─────────────────────────────

def _line(o, label, key, v, *, role=LineRole.LINE, method=None, page=4):
    li = LineItem(source_label=label, canonical_key=key, ordinal=o, role=role,
                  printed_in=PrintedIn.FACE)
    li.confidence.method = method
    li.set_value(ExtractedValue(value=Decimal(v), value_raw=Decimal(v), basis=Basis.CONSOLIDATED,
                                period_label="current", provenance=Provenance(page_index=page)))
    return li


def test_a_printed_others_line_returning_to_its_own_bucket_is_marked_reported_other(hk_view):
    doc = DocumentModel(filename="f.pdf", locale="en")
    doc.pages = [PageSource(index=4, statement="balance_sheet")]
    doc.line_items = [
        _line(0, "Inventories", "bs_ca__inventories", 100, method="exact"),
        _line(1, "Other current assets", "bs_ca__other_current_assets", 30, method="exact"),
        _line(2, "Advances to contractors", None, 20),
        _line(3, "Total current assets", "bs_ca__total_current_assets", 150,
              role=LineRole.SUBTOTAL, method="exact"),
    ]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = hk_view
    ctx.mapping_strategy = "deterministic"
    ResidualStage().run(doc, ctx)

    printed, leftover = doc.line_items[1], doc.line_items[2]
    # Both land in the bucket by the only way in — the sweep — and only the printed line is the
    # filing's own Others.
    assert printed.canonical_key == leftover.canonical_key == "bs_ca__other_current_assets"
    assert "reported_other" in printed.confidence.flags
    assert "reported_other" not in leftover.confidence.flags
    entry, = ctx.residual_itemisation
    assert [c.get("reported_other", False) for c in entry["components"]] == [True, False]


def _swept_rows():
    return [
        _row("bs_ca__inventories", "Inventories", 100, 90, page=4),
        _row("bs_ca__other_current_assets", "Other current assets", 30, 25, page=4,
             method="residual", flags=["alloc:fallback_combined", "reported_other"]),
        _row("bs_ca__other_current_assets", "Advances to contractors", 20, 15, page=4,
             method="residual", flags=["alloc:fallback_combined", "residual_combined"]),
        _row("bs_ca__total_current_assets", "Total current assets", 150, 130, page=4),
    ]


def test_one_card_per_others_line_lists_each_swept_row_with_its_remap(hk_template_def):
    rows = _swept_rows()
    review = _build_review(rows, "f.pdf", "en", template_def=hk_template_def)
    card, = [c for c in review["checks"] if c["type"] == "swept"]
    assert card["target"] == "bs_ca__other_current_assets"
    listed, = card["swept_rows"]
    assert (listed["label"], listed["src"], listed["v1_display"], listed["v2_display"]) == (
        "Advances to contractors", "p.5", "20", "15")
    assert listed["remap"]["row_ref"] == _row_ref(rows[2])
    assert listed["remap"]["current_key"] == "bs_ca__other_current_assets"
    assert card["line"] == {"basis": "consolidated", "statement": "balance_sheet",
                            "target": "bs_ca__other_current_assets"}
    tab = next(t for t in review["tabs"] if t["types"] == ["swept"])
    assert tab["count"] == 1


def test_an_others_line_holding_only_its_printed_caption_raises_no_card(hk_template_def):
    rows = [r for r in _swept_rows() if r["source_label"] != "Advances to contractors"]
    review = _build_review(rows, "f.pdf", "en", template_def=hk_template_def)
    assert not [c for c in review["checks"] if c["type"] == "swept"]
    assert not [t for t in review["tabs"] if t["types"] == ["swept"]]


@pytest.mark.parametrize("locale", LOCALES)
def test_every_sentence_on_a_swept_card_is_translated(hk_template_def, locale):
    review = _build_review(_swept_rows(), "f.pdf", locale, template_def=hk_template_def)
    card, = [c for c in review["checks"] if c["type"] == "swept"]
    english = _build_review(_swept_rows(), "f.pdf", "en", template_def=hk_template_def)
    en_card, = [c for c in english["checks"] if c["type"] == "swept"]
    for key in ("title", "severity", "fix"):
        assert card[key] == _TR[en_card[key]][locale], key
    assert [r[0] for r in card["calc"]] == [_TR[r[0]][locale] for r in en_card["calc"]]
    tab = next(t for t in review["tabs"] if t["types"] == ["swept"])
    assert tab["label"] == _TR["Swept into Others"][locale]


def _seed(rows, template_def, notes=None) -> str:
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    with SessionLocal() as s:
        doc = Document(filename="sw.pdf", fmt="pdf", byte_size=1, page_count=10,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin")
        s.add(doc)
        s.flush()
        tv = TemplateVersion(template_key=f"sw-{uuid.uuid4().hex[:8]}", name="SW", version=1,
                             definition=template_def)
        s.add(tv)
        s.flush()
        s.add(ExtractionRun(document_id=doc.id, status="succeeded",
                            options={"template_version_id": tv.id},
                            result={"rows": rows, "filename": "sw.pdf", "pages": [],
                                    "note_details": notes or []}))
        s.commit()
        return doc.id


def test_a_swept_row_moved_from_its_card_leaves_the_card(client, hk_template_def):
    rows = _swept_rows()
    doc_id = _seed(rows, hk_template_def)
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    card, = [c for c in review["checks"] if c["type"] == "swept"]
    ref = card["swept_rows"][0]["remap"]["row_ref"]
    moved = client.post(f"/api/v1/documents/{doc_id}/review/remap",
                        json={"row_ref": ref, "canonical_key": "bs_ca__prepayments_cp",
                              "reason": "an advance paid, a prepayment"})
    assert moved.status_code == 200, moved.text
    after = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert not [c for c in after["checks"] if c["type"] == "swept"]


def test_the_run_keeps_the_residual_itemisation(client, monkeypatch):
    """It lived on the pipeline context only, so after the run it survived as log lines. The
    residual stage is wrapped to itemise one component, so the record has something in it on a
    fixture whose own template sweeps nothing; the figures stay exactly as the stage left them."""
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_rich_pdf

    entry = {"residual": "bs_ca__other_current_assets", "section": "bs_ca",
             "components": [{"source_row_label": "Advances to contractors",
                             "value": {"consolidated|current": Decimal("20")}}],
             "reconciliation": [{"column": "consolidated|current", "diff": 0.0}]}
    original = ResidualStage.run

    def itemising(self, doc, ctx):
        out = original(self, doc, ctx)
        ctx.residual_itemisation = [*(getattr(ctx, "residual_itemisation", None) or []), entry]
        return out

    monkeypatch.setattr(ResidualStage, "run", itemising)
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("rich.pdf", make_rich_pdf(),
                                         "application/pdf")}).json()["id"]
    client.post(f"/api/v1/documents/{doc_id}/extractions", json=run_body())
    for _ in range(200):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    result = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]
    assert result["residual_itemisation"][-1] == {
        **entry, "components": [{"source_row_label": "Advances to contractors",
                                 "value": {"consolidated|current": "20"}}]}


# ── R2.5 the conflict cards of the flag contract ─────────────────────────────────────────────

_NOTE_PAGE = 41


def _notes_holding(amount, *, period="current", basis="consolidated"):
    return [{"note_number": "12", "rows": [
        {"label": "Other receivables", "values": [
            {"basis": basis, "period_label": period, "value": str(amount),
             "provenance": _prov(_NOTE_PAGE)}]}]}]


def _disagreeing(model="1250", note="1300"):
    row = _row("bs_ca__other_receivables_cp", "Other receivables", model, "900", page=7,
               method="line_item_llm", flags=[f"llm_note_route_disagree:{model}:{note}"])
    return [row]


def test_two_readings_that_disagree_raise_a_card_naming_both_and_where(hk_template_def):
    rows = _disagreeing()
    review = _build_review(rows, "f.pdf", "en", template_def=hk_template_def,
                           notes=_notes_holding("1300"))
    card, = [c for c in review["checks"] if c["type"] == "readings_disagree"]
    assert card["title"] == "Two readings disagree"
    assert card["calc"][:4] == [["The model's figure, on the line", "1,250", True],
                                ["Read on", "p.8", False],
                                ["The configured note reading", "1,300", False],
                                ["Read on", f"p.{_NOTE_PAGE + 1}", False]]
    assert card["subject"]["period"] == "current" and card["delta"] == "-50"
    assert card["line"] == {"basis": "consolidated", "statement": "balance_sheet",
                            "target": "bs_ca__other_receivables_cp"}
    tab = next(t for t in review["tabs"] if t["label"] == "Readings disagree")
    assert tab["count"] == 1 and set(tab["types"]) == {"readings_disagree",
                                                       "model_replaced_printed"}


def test_no_disagreement_card_once_the_line_no_longer_holds_the_models_figure(hk_template_def):
    rows = _disagreeing()
    rows[0]["values"][0]["value"] = "1300"          # corrected to the note's figure
    review = _build_review(rows, "f.pdf", "en", template_def=hk_template_def)
    assert not [c for c in review["checks"] if c["type"] == "readings_disagree"]


def _displaced(now="5170", printed="9999"):
    row = _row("bs_ca__other_receivables_cp", "Other receivables", now, "4100", page=88,
               method="line_item_llm", flags=[f"line_item_llm_displaced_printed:{printed}"])
    # The model answered the current column only; the prior one still points at the face page.
    row["values"][1]["provenance"] = _prov(3)
    row["derivation"] = {"consolidated:current": {"method": "line_item_llm:1_cited_row"}}
    return [row]


def test_a_printed_figure_the_model_replaced_raises_a_card_with_both_figures(hk_template_def):
    review = _build_review(_displaced(), "f.pdf", "en", template_def=hk_template_def)
    card, = [c for c in review["checks"] if c["type"] == "model_replaced_printed"]
    assert card["calc"] == [
        ["Printed on the face · consolidated/current", "9,999", False],
        ["Read on", "p.4", False],
        ["The model replaced it with · consolidated/current", "5,170", True],
        ["Read on", "p.89", False]]
    assert card["line"]["target"] == "bs_ca__other_receivables_cp"


def test_where_the_model_wrote_every_column_the_face_is_the_statements_page(hk_template_def):
    """The model's write moves the provenance to the note it cited, so a row it answered in both
    columns no longer says which face page printed the figure; the statement's face page does."""
    rows = _displaced()
    rows[0]["flags"].append("line_item_llm_displaced_printed:3800")
    rows[0]["derivation"]["consolidated:prior"] = {"method": "line_item_llm:1_cited_row"}
    pages = [{"index": 2, "kind": "face", "statement": "balance_sheet", "printed_page": "3"},
             {"index": 9, "kind": "notes", "statement": None}]
    review = _build_review(rows, "f.pdf", "en", template_def=hk_template_def, pages=pages)
    card, = [c for c in review["checks"] if c["type"] == "model_replaced_printed"]
    assert card["calc"][1] == ["Read on", "p.3", False]
    assert card["calc"][4:6] == [["Printed on the face · consolidated/prior", "3,800", False],
                                 ["Read on", "p.3", False]]


def test_a_printed_figure_the_model_replaced_from_a_sentence_raises_the_same_card(hk_template_def):
    """The model's prose write records the figure it displaced as `prose_value_displaced_printed:`
    and its trail as `prose_sourced`; the card reads both, as it does the cited-row write."""
    rows = _displaced()
    rows[0]["flags"] = ["prose_value_displaced_printed:9999"]
    rows[0]["derivation"] = {"consolidated:current": {"method": "prose_sourced"}}
    review = _build_review(rows, "f.pdf", "en", template_def=hk_template_def)
    card, = [c for c in review["checks"] if c["type"] == "model_replaced_printed"]
    assert card["calc"][0] == ["Printed on the face · consolidated/current", "9,999", False]
    assert card["calc"][2] == ["The model replaced it with · consolidated/current", "5,170", True]


def test_no_card_where_the_printed_and_new_amounts_are_the_same(hk_template_def):
    review = _build_review(_displaced(now="9999"), "f.pdf", "en", template_def=hk_template_def)
    assert not [c for c in review["checks"] if c["type"] == "model_replaced_printed"]


@pytest.mark.parametrize("locale", LOCALES)
def test_every_sentence_on_a_conflict_card_is_translated(hk_template_def, locale):
    rows = _disagreeing() + _displaced()
    review = _build_review(rows, "f.pdf", locale, template_def=hk_template_def)
    english = _build_review(rows, "f.pdf", "en", template_def=hk_template_def)
    pairs = list(zip([c for c in english["checks"] if c["type"] in (
                          "readings_disagree", "model_replaced_printed")],
                     [c for c in review["checks"] if c["type"] in (
                          "readings_disagree", "model_replaced_printed")]))
    assert len(pairs) == 2
    for en_card, card in pairs:
        for key in ("title", "severity", "fix"):
            assert card[key] == _TR[en_card[key]][locale], key
        for en_row, row in zip(en_card["calc"], card["calc"]):
            head = en_row[0].split(" · ")[0]
            assert row[0].split(" · ")[0] == _TR[head][locale], head


def test_a_conflict_card_is_judged_like_any_other_and_raises_no_note_tie(client, hk_template_def):
    """Accepting needs a subject and a digest the accept route can reproduce; and the retired
    note-tie card stays retired."""
    doc_id = _seed(_disagreeing() + _displaced(), hk_template_def,
                   notes=_notes_holding("1300"))
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert not [c for c in review["checks"] if c["type"] == "note_tie"]
    card = next(c for c in review["checks"] if c["type"] == "readings_disagree")
    accepted = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                           json={"subject_key": card["subject_key"],
                                 "evidence_digest": card["evidence_digest"],
                                 "reason": "the model read the right note"})
    assert accepted.status_code == 200, accepted.text
    again = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert next(c for c in again["checks"]
                if c["subject_key"] == card["subject_key"])["status"] == "accepted"


# ── the screen strings ──────────────────────────────────────────────────────────────────────

_FRONTEND = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
_SCREEN_KEYS = ("r.sweptHead", "r.cov.heldAssumedZero", "r.cov.assumedZeroHead")


def test_the_screen_strings_are_in_every_locale():
    import re

    from tests.test_explained_by_ui_strings import LOCALES as ALL, _blocks

    path = _FRONTEND / "i18n" / "screens" / "review.ts"
    if not path.exists():                       # pragma: no cover - a backend-only checkout
        pytest.skip(f"{path} is not present")
    blocks = _blocks(path.read_text(encoding="utf-8"))
    for key in _SCREEN_KEYS:
        for locale in ALL:
            m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"',
                          blocks.get(locale, ""))
            assert m and m.group(1).strip(), (locale, key)
