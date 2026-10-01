"""A mainland cash flow reads as printed, and a figure read out of other captions is counted once.

Measured on 000709 (河钢股份) before these changes, against what the filing prints:

* financing cash flow +259.7bn for a printed -2.9bn, and closing cash 313.6bn for 18.7bn — every
  outflow of the investing and financing blocks ADDED, because a CSRC layout prints them unsigned
  and only three operating captions carried a flip;
* "取得子公司及其他营业单位支付的现金净额支付其他与投资活动有关的现金" holding the outflow SUBTOTAL's
  19,025,416,852.11 — two blank lines welded onto the subtotal's figures;
* total equity and liabilities 958,370,316 over print: exactly the two related-party lines, each
  read out of 其他应付款 / 长期应付款 by the related-party note and added beside the face lines that
  already hold it.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services import rollups
from app.services.row_reconstruct import build_line_items
from app.stages.normalize import NormalizeStage

from tests.test_multiline_wrapped_caption import STEP, _line


def _lines(y0, specs):
    return [w for i, (text, x0, value) in enumerate(specs)
            for w in _line(y0 + i * STEP, text, x0=x0, value=value)]


# ── reading the page ─────────────────────────────────────────────────────────────────────────────

def test_a_blank_caption_that_wraps_is_not_welded_onto_the_next_line():
    """300319 prints 处置子公司及其他营业单位收到的 / 现金净额 with no figure. Joined, it is a complete
    line of the statement; it used to be carried on into the next row's caption."""
    words = _lines(0.30, [("处置子公司及其他营业单位收到的", 0.10, None), ("现金净额", 0.09, None),
                          ("收到其他与投资活动有关的现金", 0.10, "2,188,175,461.87"),
                          ("投资活动现金流入小计", 0.09, "2,202,003,564.86")])
    items, _ = build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                statement="cash_flow")
    assert [li.source_label for li in items] == ["收到其他与投资活动有关的现金", "投资活动现金流入小计"]


def test_an_of_which_breakdown_never_holds_more_than_its_parent():
    """The indent alone cannot see a breakdown end on a layout whose next line never returns to the
    parent's margin; the arithmetic can — an "of which" is part of its parent."""
    words = _lines(0.30, [("其他应付款", 0.10, "100.00"), ("其中：应付股利", 0.11, "30.00"),
                          ("应付利息", 0.158, "20.00"), ("长期应付款", 0.158, "500.00")])
    items, _ = build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                statement="balance_sheet")
    inside = {li.source_label: li.parent_id is not None for li in items}
    assert inside == {"其他应付款": False, "其中：应付股利": True, "应付利息": True, "长期应付款": False}


# ── signs ────────────────────────────────────────────────────────────────────────────────────────

def _cf_row(label, value, key=None):
    li = LineItem(source_label=label, role=LineRole.LINE, canonical_key=key)
    li.confidence.flags.append("printed_on:cash_flow")
    li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                basis=Basis.CONSOLIDATED, period_label="current"))
    return li


def _figures(doc):
    return [next(iter(li.values.values())).value for li in doc.line_items]


def test_an_unsigned_outflow_block_is_negated_and_says_so():
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.line_items = [_cf_row("取得借款收到的现金", "115"),
                      _cf_row("筹资活动现金流入小计", "115"),
                      _cf_row("偿还债务支付的现金", "111", "cf_financing__repayments_non_cur_borrowings"),
                      _cf_row("支付其他与筹资活动有关的现金", "13"),
                      _cf_row("筹资活动现金流出小计", "124")]
    NormalizeStage._negate_unsigned_cas_outflows(doc, PipelineContext(raw_bytes=b""))
    assert _figures(doc) == [Decimal(115), Decimal(115), Decimal(-111), Decimal(-13), Decimal(124)]
    ev = next(iter(doc.line_items[2].values.values()))
    assert ev.sign_normalised and ev.value_raw == Decimal(111)


def test_a_block_that_prints_its_outflows_signed_is_left_alone():
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.line_items = [_cf_row("投资活动现金流入小计", "10"),
                      _cf_row("购建固定资产、无形资产和其他长期资产支付的现金", "-19"),
                      _cf_row("投资支付的现金", "5"),
                      _cf_row("投资活动现金流出小计", "-14")]
    NormalizeStage._negate_unsigned_cas_outflows(doc, PipelineContext(raw_bytes=b""))
    assert _figures(doc) == [Decimal(10), Decimal(-19), Decimal(5), Decimal(-14)]


# ── a figure read out of other captions ─────────────────────────────────────────────────────────

_TEMPLATE = {"statements": [{"type": "balance_sheet", "sections": [{
    "node_id": "bs_cl", "canonical_key": "bs_cl", "label": "CL", "role": "header", "children": [
        {"node_id": "pay", "canonical_key": "bs_cl__other_payables", "label": "Other payables",
         "role": "line"},
        {"node_id": "rp", "canonical_key": "bs_cl__due_to_related_parties_cp", "label": "Due to RP",
         "role": "line"},
        {"node_id": "tot", "canonical_key": "bs_cl__total_current_liabilities", "label": "Total",
         "role": "subtotal", "rollup": {"op": "sum", "children": [
             "bs_cl__other_payables", "bs_cl__due_to_related_parties_cp"]}}]}]}]}


def _row(key, value, flags=()):
    return {"canonical_key": key, "values": [{
        "basis": "consolidated", "period_label": "current", "value": str(value),
        "confidence": {"flags": list(flags)}}]}


def test_a_carved_figure_is_shown_on_its_line_and_not_added_again():
    rows = [_row("bs_cl__other_payables", 1000),
            _row("bs_cl__due_to_related_parties_cp", 102, [rollups.CARVED_FROM_FACE])]
    shown = rollups.figures_as_shown(_TEMPLATE, rows, "consolidated", "current")
    assert shown["bs_cl__due_to_related_parties_cp"] == 102
    assert shown["bs_cl__total_current_liabilities"] == 1000


def test_a_related_party_line_printed_on_the_face_is_still_added():
    """China SCE 1966 prints "Due to related parties" as a line of its own: no carve, so it counts."""
    rows = [_row("bs_cl__other_payables", 1000), _row("bs_cl__due_to_related_parties_cp", 102)]
    shown = rollups.figures_as_shown(_TEMPLATE, rows, "consolidated", "current")
    assert shown["bs_cl__total_current_liabilities"] == 1102
