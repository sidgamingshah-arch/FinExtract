"""A calculated line that differs from the document names the printed row nothing mapped.

When a total's components do not come to what the filing printed, the commonest cause is one row
on the same statement that reached no line of the template. ``services/gap_explanation.py`` names
that row, at read time, when it is the only row whose figures account for the difference in BOTH
periods with ONE orientation, and when mapping it onto a line the total is built from — simulated
with the rollup evaluator — closes the total. These tests pin what is named, what is refused, and
that nothing is stored: the remap stays the analyst's own POST /review/remap.
"""
from __future__ import annotations

import copy
import uuid

import pytest

from app.api.routes.documents import (
    _EXPLAINED_CLOSES, _EXPLAINED_PARTIAL, _REMAPPED_INPUT_NOTE, _CrossCheck, _build_review,
    _build_statement, _gap_explanations, _row_ref)
from app.services.gap_explanation import (
    MAX_HITS_PER_TARGET, explain_gaps, not_a_line, section_tokens)

PURCHASES = "engine_unclassified_face__profit_and_loss__expenses__purchases_of_stock_in_trade"


def _v(period, value, *, basis="consolidated", page=3, y=0.5):
    return {"basis": basis, "period_label": period, "value": str(value),
            "provenance": {"source_kind": "native", "page_index": page,
                           "bbox": {"x0": 0.6, "y0": y, "x1": 0.7, "y1": y + 0.01},
                           "label_bbox": {"x0": 0.1, "y0": y, "x1": 0.4, "y1": y + 0.01}}}


def _row(key, label, cur=None, prior=None, *, page=3, y=0.5, **extra):
    vals = []
    if cur is not None:
        vals.append(_v("current", cur, page=page, y=y))
    if prior is not None:
        vals.append(_v("prior", prior, page=page, y=y))
    return {"canonical_key": key, "source_label": label, "values": vals, "printed_in": "face",
            "role": "line", **extra}


def _line(key, label):
    return {"node_id": key, "canonical_key": key, "label": label, "role": "line", "rollup": None}


def _total(key, label, op, children, role="subtotal"):
    return {"node_id": key, "canonical_key": key, "label": label, "role": role,
            "rollup": {"op": op, "children": children}}


# A small P&L: income and expenses, each with a printed subtotal, and profit before tax as their
# difference — the shape every template's P&L has, small enough to read.
TEMPLATE = {
    "schema_version": 1, "template_key": "gx", "name": "GX",
    "statements": [{"type": "profit_and_loss", "sections": [
        {"node_id": "inc", "canonical_key": "pl_income", "label": "Income", "role": "header",
         "children": [_line("pl_revenue", "Revenue"),
                      _total("pl_total_income", "Total income", "sum", ["pl_revenue"])]},
        {"node_id": "exp", "canonical_key": "pl_expenses", "label": "Expenses", "role": "header",
         "children": [_line("pl_materials", "Cost of materials consumed"),
                      _line("pl_other_expenses", "Other expenses"),
                      _total("pl_total_expenses", "Total expenses", "sum",
                             ["pl_materials", "pl_other_expenses"])]},
        {"node_id": "pbt", "canonical_key": "pl_pbt", "label": "Profit before tax",
         "role": "total", "children": [],
         "rollup": {"op": "diff", "children": ["pl_total_income", "pl_total_expenses"]}},
    ]}],
}


class _Item:
    def __init__(self, key, scope):
        self.key, self.section_scope = key, [scope]


class _Set:
    """The two attributes the explanation reads off a line-item set."""
    items = [_Item("pl_revenue", "pl_income"), _Item("pl_total_income", "pl_income"),
             _Item("pl_materials", "pl_expenses"), _Item("pl_other_expenses", "pl_expenses"),
             _Item("pl_total_expenses", "pl_expenses")]
    cross_check_master = None


def _purchases(cur=73600, prior=67820, **kw):
    return _row(PURCHASES, "Purchases of stock-in-trade", cur, prior, y=0.6, **kw)


def _rows(*, pbt=(1400, 180), total_expenses=(98600, 89820), purchases=None):
    rows = [_row("pl_revenue", "Revenue from operations", 100000, 90000, y=0.2),
            _row("pl_materials", "Cost of materials consumed", 20000, 18000, y=0.3),
            _row("pl_other_expenses", "Other expenses", 5000, 4000, y=0.4)]
    if pbt is not None:
        rows.append(_row("pl_pbt", "Profit before tax", *pbt, y=0.8, role="total"))
    if total_expenses is not None:
        rows.append(_row("pl_total_expenses", "Total expenses (IV)", *total_expenses, y=0.7,
                         role="subtotal"))
    rows.append(purchases if purchases is not None else _purchases())
    return rows


def _explain(rows, template=TEMPLATE, *, pages=None, sections=None, stats=None):
    return _gap_explanations(rows, template, "consolidated", cross_check=_CrossCheck(),
                             line_item_set=sections, pages=pages, stats=stats)


def _stmt(rows, template=TEMPLATE, **kw):
    return _build_statement(rows, template, "profit_and_loss", "f.pdf", **kw)


def _row_of(d, key):
    return next(r for r in d["rows"] if r["id"] == key)


# --------------------------------------------------------------------------------------------
# what is named
# --------------------------------------------------------------------------------------------
def test_an_unplaced_cost_is_named_as_the_reason_profit_is_computed_too_high():
    # The filing's PBT (1,400) includes purchases the computation never saw, so the computed PBT
    # is too high by exactly their amount; total expenses is too LOW by the same amount. The two
    # lines take the row with opposite orientations, and the figures served are the row's own.
    ex = _explain(_rows())
    assert set(ex) == {"pl_total_expenses", "pl_pbt"}
    pbt, total = ex["pl_pbt"], ex["pl_total_expenses"]
    assert (pbt.orientation, total.orientation) == (-1, 1)
    for e in (pbt, total):
        assert (e.v1, e.v2) == (73600, 67820)
        assert e.closes and (e.remainder1, e.remainder2) == (0, 0)
        assert e.statement == "profit_and_loss" and e.basis == "consolidated"
    assert (pbt.gap1, pbt.gap2) == (-73600, -67820)


def test_one_period_matching_is_a_coincidence_not_an_explanation():
    assert _explain(_rows(purchases=_purchases(prior=50000))) == {}


def test_a_tied_prior_period_is_neither_explained_nor_broken():
    # Last year's PBT agrees with its components, so there is no two-period difference to explain
    # — even though the row has a prior figure, which would BREAK the tied year if it were mapped.
    stats: dict = {}
    assert _explain(_rows(pbt=(1400, 68000), total_expenses=None), stats=stats) == {}
    assert stats["one_period_gaps"] == 1


def test_the_orientation_must_hold_in_both_periods():
    assert _explain(_rows(purchases=_purchases(prior=-67820))) == {}


def test_a_near_match_is_served_with_its_remainder_and_never_as_closing():
    # 73,600 of a 73,604 difference: within half a percent, so the row is named — as accounting
    # for MOST of it, with the 4 that stays shown. Never as closing.
    ex = _explain(_rows(pbt=(1396, 180), total_expenses=None))["pl_pbt"]
    assert ex.closes is False
    assert (ex.remainder1, ex.remainder2) == (4.0, 0.0)
    served = _row_of(_stmt(_rows(pbt=(1396, 180), total_expenses=None)), "pl_pbt")["explained_by"]
    assert served["closes"] is False
    assert served["note"] == _EXPLAINED_PARTIAL
    assert (served["remainder1_display"], served["remainder2_display"]) == ("4", "0")


def test_a_row_printed_off_the_statements_pages_explains_nothing():
    rows = _rows(purchases=_purchases(page=9))
    assert _explain(rows) == {}
    # The classifier read page 9 as this statement (a continuation page), which widens the scope.
    widened = _explain(rows, pages=[{"index": 9, "statement": "profit_and_loss"}])
    assert set(widened) == {"pl_total_expenses", "pl_pbt"}


def test_a_row_whose_printed_section_is_not_part_of_the_total_explains_nothing():
    stats: dict = {}
    rows = _rows(purchases=_purchases(section="current_liabilities"))
    assert _explain(rows, sections=_Set(), stats=stats) == {}
    assert "pl_pbt" in stats["section_not_in_total"]


def test_a_destination_under_a_printed_only_subtotal_does_not_verify():
    # Raw materials is printed (20,000) but its parts are not: mapping the row under it would make
    # the subtotal computable and DROP the printed 20,000 rather than add 73,600 to expenses. The
    # simulation sees that, so no destination verifies.
    def template(with_other: bool) -> dict:
        tpl = copy.deepcopy(TEMPLATE)
        exp = tpl["statements"][0]["sections"][1]
        exp["children"] = [_line("pl_raw_imported", "Imported"),
                           _line("pl_raw_indigenous", "Indigenous"),
                           _total("pl_raw_materials", "Raw materials consumed", "sum",
                                  ["pl_raw_imported", "pl_raw_indigenous"]),
                           *([_line("pl_other_expenses", "Other expenses")] if with_other else []),
                           _total("pl_total_expenses", "Total expenses", "sum",
                                  ["pl_raw_materials",
                                   *(["pl_other_expenses"] if with_other else [])])]
        return tpl

    class Sections:
        items = [_Item("pl_revenue", "pl_income"), _Item("pl_raw_imported", "pl_expenses"),
                 _Item("pl_raw_indigenous", "pl_expenses"),
                 _Item("pl_other_expenses", "pl_expenses")]

    rows = [_row("pl_revenue", "Revenue from operations", 100000, 90000, y=0.2),
            _row("pl_raw_materials", "Raw materials consumed", 20000, 18000, y=0.3),
            _row("pl_pbt", "Profit before tax", 6400, 4180, y=0.8, role="total"),
            _purchases()]
    stats: dict = {}
    assert _explain(rows, template(False), sections=Sections(), stats=stats) == {}
    assert "pl_pbt" in stats["unverified"]
    ex = _explain(rows, template(True), sections=Sections())
    assert ex["pl_pbt"].destination == "pl_other_expenses"


def test_two_rows_that_both_explain_a_line_leave_it_unexplained():
    stats: dict = {}
    rows = _rows(total_expenses=None)
    rows.append(_row(None, "Traded goods purchased", 73600, 67820, y=0.65))
    assert MAX_HITS_PER_TARGET >= 2
    assert _explain(rows, stats=stats) == {}
    assert "pl_pbt" in stats["ambiguous"]


@pytest.mark.parametrize("caption,v1,v2", [
    ("Total expenses (IV)", 84271, 75000),
    ("III. Total income (I + II)", 85590, 80000),
    ("V. Profit before tax (III - IV)", 1319, 1045),
    ("Profit for the year", 985, 786),
    ("Gross profit", 5000, 4000),
    ("Net cash flow from operating activities (A)", 900, 800),
    ("Cash generated from operations", 950, 820),
    ("NET INCREASE/(DECREASE) IN CASH AND CASH EQUIVALENTS", 120, -40),
    ("流动负债合计", 12000, 11000),
    ("负债和所有者权益总计", 50000, 45000),
    ("其中：营业收入", 3000, 2800),
    ("四、利润总额", 700, 650),
    ("(1) Basic (₹)", 6.57, 5.21),
    ("Earnings per equity share (face value ₹10)", 6.57, 5.21),
    ("The accompanying notes are an integral part of these financial statements", 1, 43),
    ("For and on behalf of the Board of Directors", 28, 28),
    ("Managing Director DIN: 01234567", 1234567, 1234567),
    ("No.", 2025, 2024),
    ("项目附注", 2024, 2023),
    ("Significant accounting policies (Note", 1, 38),
])
def test_not_a_line_drops_totals_results_breakdowns_per_share_notes_years_and_signatures(
        caption, v1, v2):
    assert not_a_line({"source_label": caption, "role": "line"}, v1, v2) is not None


def test_not_a_line_keeps_the_captions_that_were_the_explanation():
    for caption in ("(B) total outstanding dues of creditors other than micro enterprises and "
                    "small enterprises", "Purchases of stock-in-trade", "Construction expenses",
                    "I. Revenue from operations (gross)",
                    "Net increase / (decrease) in short-term borrowings",
                    "Sundry widgets held pending resale", "Finance costs", "(1) Current tax"):
        assert not_a_line({"source_label": caption, "role": "line"}, 73600, 67820) is None, caption
    # The template's verdict on the row is read too: a row reconstructed as a subtotal is one.
    assert not_a_line({"source_label": "Purchases", "role": "subtotal"}, 1, 2) == "printed total"


@pytest.mark.parametrize("variant", [
    {"canonical_key": "sub__x"},
    {"printed_in": "notes"},
    {"column_index": 2},
    {"canonical_key": None, "flags": ["contains_mapped_children:pl_materials"]},
    {"canonical_key": None, "mapping_method": "manual_unmap"},
    "printed twice",
])
def test_parts_notes_matrix_rows_demoted_parents_hand_unmapped_rows_and_duplicates_are_not_candidates(
        variant):
    tpl = copy.deepcopy(TEMPLATE)
    # A memo line the totals are not built from, for the "same fact printed twice" case.
    tpl["statements"][0]["sections"].append(
        {"node_id": "memo", "canonical_key": "pl_memo", "label": "Memorandum", "role": "header",
         "children": [_line("pl_memo_purchases", "Purchases (memorandum)")]})
    row = _purchases()
    rows = _rows(total_expenses=None, purchases=row)
    assert set(_explain(rows, tpl)) == {"pl_pbt"}         # the unmodified row IS the explanation
    if variant == "printed twice":
        rows.append(_row("pl_memo_purchases", "Purchases of stock-in-trade", 73600, 67820,
                         page=4))
    elif "column_index" in variant:
        for v in row["values"]:
            v["column_index"] = variant["column_index"]
    else:
        row.update(variant)
    assert _explain(rows, tpl) == {}


# --------------------------------------------------------------------------------------------
# what is served, and where
# --------------------------------------------------------------------------------------------
def test_explaining_never_writes_to_the_rows():
    rows = _rows()
    before = copy.deepcopy(rows)
    _stmt(rows, line_item_set=_Set(), pages=[{"index": 3, "statement": "profit_and_loss"}])
    _build_review(rows, "f.pdf", "en", template_def=TEMPLATE, line_item_set=_Set(),
                  pages=[{"index": 3, "statement": "profit_and_loss"}], explain=True)
    assert rows == before


def test_the_line_carries_the_row_that_explains_its_printed_difference():
    rows = _rows()
    d = _stmt(rows, line_item_set=_Set())
    eb = _row_of(d, "pl_pbt")["explained_by"]
    assert eb["label"] == "Purchases of stock-in-trade"
    assert eb["src"] == "p.4" and eb["src2"] == "p.4"
    assert (eb["v1"], eb["v2"]) == (73600, 67820)
    assert (eb["v1_display"], eb["v2_display"]) == ("73,600", "67,820")
    assert eb["closes"] is True and eb["note"] == _EXPLAINED_CLOSES
    assert eb["row_ref"] == _row_ref(rows[-1])
    assert (eb["statement"], eb["basis"], eb["target"]) == ("profit_and_loss", "consolidated",
                                                            "pl_pbt")
    assert eb["target_label"] == "Profit before tax"
    assert eb["source"]["page_index"] == 3
    # The lines of the row's printed section that PBT is built from, by section label…
    assert eb["sections"] == ["Expenses"]
    # …and none when there is no section vocabulary to read the row's section against.
    assert _row_of(_stmt(rows), "pl_pbt")["explained_by"]["sections"] == []
    # Present on every calculated line, absent on a plain one — the shape printed1/2 has.
    assert "explained_by" not in _row_of(d, "pl_materials")
    assert _row_of(d, "pl_total_income")["explained_by"] is None


def test_a_manual_or_netted_line_carries_no_explanation():
    rows = _rows()
    pbt = next(r for r in rows if r["canonical_key"] == "pl_pbt")
    pbt["edited_slots"] = ["consolidated/current"]
    assert _row_of(_stmt(rows), "pl_pbt")["explained_by"] is None
    # A netting restatement replaces the figure the explanation was measured against. Netting
    # restates a figure ROW, so PBT is declared as one here.
    tpl = copy.deepcopy(TEMPLATE)
    tpl["statements"][0]["sections"][2]["role"] = "line"
    assert _row_of(_stmt(_rows(), tpl), "pl_pbt")["explained_by"] is not None
    rule = {"target_key": "pl_pbt", "subtract_keys": ["pl_other_expenses"], "label": "net"}
    netted = _row_of(_stmt(_rows(), tpl, netting_rules=[rule]), "pl_pbt")
    assert netted["inspector"]["tag"] == "netted"
    assert netted["explained_by"] is None


def _seed(rows, pages=None) -> str:
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    with SessionLocal() as s:
        doc = Document(filename="gx.pdf", fmt="pdf", byte_size=1, page_count=10,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin")
        s.add(doc)
        s.flush()
        tv = TemplateVersion(template_key=f"gx-{uuid.uuid4().hex[:8]}", name="GX", version=1,
                             definition=TEMPLATE)
        s.add(tv)
        s.flush()
        s.add(ExtractionRun(document_id=doc.id, status="succeeded",
                            options={"template_version_id": tv.id},
                            result={"rows": rows, "filename": "gx.pdf",
                                    "pages": pages or []}))
        s.commit()
        return doc.id


def test_the_explanation_reaches_the_payload_the_workspace_reads(client):
    # On page 9, which only the run's classified pages put on the P&L.
    rows = _rows(purchases=_purchases(page=9))
    doc_id = _seed(rows, pages=[{"index": 9, "statement": "profit_and_loss"}])
    d = client.get(f"/api/v1/documents/{doc_id}/statement",
                   params={"statement": "profit_and_loss", "basis": "consolidated"}).json()
    eb = _row_of(d, "pl_pbt")["explained_by"]
    assert eb["label"] == "Purchases of stock-in-trade"
    assert eb["src"] == "p.10"


def test_the_mismatch_card_names_the_row_that_explains_it():
    rows = _rows(total_expenses=None)
    # Total income printed 500 over its components this year only: a card, and no explanation.
    rows.append(_row("pl_total_income", "Total income", 100500, 90000, y=0.25, role="subtotal"))
    review = _build_review(rows, "f.pdf", "en", template_def=TEMPLATE, explain=True)
    cards = {c["id"]: c for c in review["checks"]}
    card = cards["chk-calc-consolidated-pl_pbt"]
    eb = card["explained_by"]
    assert (eb["v1_display"], eb["v2_display"]) == ("73,600", "67,820")
    assert card["fix_action"] is None and card["remap"] is None
    others = [c for c in review["checks"] if c["type"] != "unmapped" and c is not card]
    assert others and all(c["explained_by"] is None for c in others)
    assert cards["chk-calc-consolidated-pl_total_income"]["explained_by"] is None
    # The review payload gains no top-level key.
    assert set(review) == {"run_id", "checks", "tabs", "summary", "judgements", "coverage",
                           "remap_targets", "weak_mappings"}


def test_explanations_never_touch_a_findings_identity():
    rows = _rows()
    plain = _build_review(rows, "f.pdf", "en", template_def=TEMPLATE)
    explained = _build_review(rows, "f.pdf", "en", template_def=TEMPLATE, explain=True)
    assert [(c["subject_key"], c["evidence_digest"]) for c in plain["checks"]] \
        == [(c["subject_key"], c["evidence_digest"]) for c in explained["checks"]]
    assert any(c.get("explained_by") for c in explained["checks"])
    card = next(c for c in plain["checks"] if c["id"] == "chk-calc-consolidated-pl_pbt")
    judged = {"subject_key": card["subject_key"], "subject": card["subject"],
              "evidence": card["evidence"], "reason": "Checked against p.4.", "actor": "rev",
              "actor_role": "reviewer", "at": "2026-10-01T09:00:00", "run_id": "run-1"}
    again = _build_review(rows, "f.pdf", "en", template_def=TEMPLATE, judgements=[judged],
                          explain=True)
    served = next(c for c in again["checks"] if c["id"] == card["id"])
    assert served["status"] == "accepted" and served["explained_by"] is not None


def _relation(rule_id, period, difference, *, target="pl_pbt"):
    expected = {"current": 75000.0, "prior": 68000.0}[period]
    return {"rule_id": rule_id, "kind": "rollup", "status": "fail",
            "scope_key": f"consolidated/{period}",
            "expected": expected, "actual": expected + difference, "difference": difference,
            "details": {"target": target, "basis": "consolidated", "period_label": period,
                        "op": "diff", "components": ["pl_total_income", "pl_total_expenses"],
                        "component_values": {}}}


def test_the_relation_reporting_the_same_difference_carries_the_same_explanation():
    rows = _rows(total_expenses=None)
    structural = [_relation("rollup:pl_pbt", "current", -73600),
                  _relation("rollup:pl_pbt", "prior", -67820),
                  _relation("other:pl_pbt", "current", -500)]
    review = _build_review(rows, "f.pdf", "en", structural=structural, template_def=TEMPLATE,
                           explain=True)
    cards = {c["id"]: c for c in review["checks"]}
    for period in ("current", "prior"):
        card = cards[f"chk-structural-rollup:pl_pbt-consolidated/{period}"]
        assert card["explained_by"]["label"] == "Purchases of stock-in-trade"
    assert cards["chk-structural-other:pl_pbt-consolidated/current"]["explained_by"] is None
    # The calculated card states the same difference as the current relation, so it is suppressed.
    assert "chk-calc-consolidated-pl_pbt" not in cards


def test_the_explaining_rows_unmapped_card_lists_what_mapping_it_closes():
    rows = _rows()
    rows.append(_row(None, "Sundry items", 10, 20, y=0.9))
    review = _build_review(rows, "f.pdf", "en", template_def=TEMPLATE, explain=True)
    unmapped = {c["title"]: c for c in review["checks"] if c["type"] == "unmapped"}
    explains = unmapped["Purchases of stock-in-trade"]["remap"]["explains"]
    assert [(x["target"], x["closes"]) for x in explains] == [("pl_total_expenses", True),
                                                             ("pl_pbt", True)]
    assert all(x["statement"] == "profit_and_loss" and x["basis"] == "consolidated"
               for x in explains)
    assert explains[1]["target_label"] == "Profit before tax"
    assert unmapped["Sundry items"]["remap"]["explains"] == []


def test_the_review_route_serves_it_and_the_remap_closes_it(client):
    rows = _rows(total_expenses=None)
    doc_id = _seed(rows, pages=[{"index": 3, "statement": "profit_and_loss"}])
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    card = next(c for c in review["checks"] if c["id"] == "chk-calc-consolidated-pl_pbt")
    ref = card["explained_by"]["row_ref"]
    r = client.post(f"/api/v1/documents/{doc_id}/review/remap",
                    json={"row_ref": ref, "canonical_key": "pl_materials",
                          "reason": "Purchases are part of the cost of materials here."})
    assert r.status_code == 200, r.text
    after = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert not [c for c in after["checks"]
                if c["type"] == "calculated_mismatch" and c["target"] == "pl_pbt"]
    assert not [c for c in after["checks"] if (c.get("remap") or {}).get("row_ref") == ref]
    d = client.get(f"/api/v1/documents/{doc_id}/statement",
                   params={"statement": "profit_and_loss", "basis": "consolidated"}).json()
    pbt = _row_of(d, "pl_pbt")
    assert pbt["v1"] == 1400 and pbt["printed1"] is None and pbt["explained_by"] is None


def test_a_re_mapped_input_is_declared_on_the_structural_card():
    rows = _rows(total_expenses=None)
    moved = next(r for r in rows if r["canonical_key"] == "pl_materials")
    moved["flags"] = ["remapped_by_reviewer:unmapped->pl_materials"]
    relation = {"rule_id": "rollup:pl_total_expenses", "kind": "rollup", "status": "fail",
                "scope_key": "consolidated/current", "expected": 25000.0, "actual": 98600.0,
                "difference": 73600.0,
                "details": {"target": "pl_total_expenses", "basis": "consolidated",
                            "period_label": "current", "op": "sum",
                            "components": ["pl_materials", "pl_other_expenses"],
                            "component_values": {}}}
    review = _build_review(rows, "f.pdf", "en", structural=[relation], template_def=TEMPLATE)
    card = next(c for c in review["checks"] if c["type"] == "structural")
    assert card["inputs_edited"] is True
    assert card["inputs_edited_keys"] == ["pl_materials"]
    assert card["inputs_edited_note"] == _REMAPPED_INPUT_NOTE


def test_the_section_vocabulary_is_read_off_the_set():
    assert section_tokens(None) == {}
    assert section_tokens(_Set())["pl_materials"] == "expenses"
    assert explain_gaps([], TEMPLATE, "consolidated", candidates=[], remap_targets=[],
                        tolerance=0.5) == {}
