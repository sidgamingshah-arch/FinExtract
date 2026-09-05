"""services.sales_revenues — Priority 2 note fallback only. Priority 1 (the face) is already
handled by the ordinary alias-matching mapper; see docs/PRC_Sales_Revenues_Extraction_Logic_Simplified.md."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, UnitContext
from app.services.sales_revenues import compute_note_fallback


def _ev(value, basis="consolidated", period="current"):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency="CNY", scale_factor=Decimal(1)))


def _item(label, value, group_hint="", role=LineRole.LINE):
    it = NoteItem(raw_label=label, group_hint=group_hint, role=role)
    ev = _ev(value)
    it.values[ev.key.model_dump_json()] = ev
    return it


def _note(number, title, items):
    return NotesTable(note_number=number, title=title, items=items)


def _doc(*notes):
    return DocumentModel(notes=list(notes))


def test_extracts_the_zhuying_revenue_row_from_the_revenue_note():
    doc = _doc(_note("30", "营业收入和营业成本", [
        _item("主营业务收入", "1000"),
        _item("主营业务成本", "600"),
    ]))
    result = compute_note_fallback(doc)[("consolidated", "current")]
    assert result.value == Decimal("1000")
    assert result.priority_used == "P2"


def test_cost_column_is_never_selected():
    doc = _doc(_note("30", "营业收入", [_item("主营业务成本", "600")]))
    assert compute_note_fallback(doc) == {}


def test_bare_zhuying_row_without_a_cost_suffix_is_accepted():
    doc = _doc(_note("30", "营业收入", [_item("主营业务", "1000")]))
    result = compute_note_fallback(doc)[("consolidated", "current")]
    assert result.value == Decimal("1000")


def test_note_not_titled_revenue_is_ignored():
    doc = _doc(_note("30", "管理费用", [_item("主营业务收入", "1000")]))
    assert compute_note_fallback(doc) == {}


def test_two_different_figures_for_the_same_period_are_flagged_possible_duplicate():
    doc = _doc(
        _note("30", "营业收入", [_item("主营业务收入", "1000")]),
        _note("31", "营业收入和营业成本", [_item("主营业务收入", "1200")]))
    result = compute_note_fallback(doc)[("consolidated", "current")]
    assert "POSSIBLE_DUPLICATE" in result.flags
