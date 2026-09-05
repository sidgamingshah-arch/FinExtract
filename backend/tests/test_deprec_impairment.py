"""services.deprec_impairment — the note-sourced priority cascade for Deprec & Impairment
(Oper Exp)/(COS). See docs/HKEX_Depreciation_Extraction_Logic_Revised.md for the spec."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, UnitContext
from app.services.deprec_impairment import compute


def _ev(value, basis="consolidated", period="current", currency="HKD", scale="1"):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency=currency, scale_factor=Decimal(scale)))


def _item(label, value, role=LineRole.LINE, **kw):
    it = NoteItem(raw_label=label, role=role)
    ev = _ev(value, **kw)
    it.values[ev.key.model_dump_json()] = ev
    return it


def _note(number, title, items, source_text=""):
    return NotesTable(note_number=number, title=title, items=items, source_text=source_text)


def _doc(*notes):
    return DocumentModel(notes=list(notes))


def test_p1_sums_direct_operating_expense_notes():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("29", "Selling and distribution expenses",
              [_item("Depreciation of property, plant and equipment", "50")]),
    )
    results = compute(doc)
    result = results[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("150")
    assert result.priority_used == "P1"
    assert result.status == "EXTRACTED_AND_COMPUTED"


def test_p1_falls_back_to_pbt_specific_callout_when_no_opex_note():
    doc = _doc(
        _note("6", "Profit before taxation",
              [_item("Depreciation of property, plant and equipment", "80")],
              source_text="Depreciation included in operating expenses of HK$80,000."),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("80")
    assert result.priority_used == "P1"


def test_p2_is_pbt_depreciation_minus_cos_depreciation():
    doc = _doc(
        _note("6", "Profit before taxation is arrived at after charging",
              [_item("Depreciation of property, plant and equipment", "500")]),
        _note("7", "Cost of sales",
              [_item("Depreciation of property, plant and equipment", "300")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("200")
    assert result.priority_used == "P2"


def test_negative_candidate_is_skipped_not_zeroed():
    doc = _doc(
        _note("6", "Profit before taxation",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("7", "Cost of sales",
              [_item("Depreciation of property, plant and equipment", "300")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value is None
    assert "NEGATIVE_RESIDUAL" in result.flags
    assert result.status == "NOT_FOUND_OR_NOT_COMPUTABLE"


def test_cos_p1_is_direct_and_p2_uses_final_oper_exp():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("7", "Cost of sales",
              [_item("Depreciation of property, plant and equipment", "300")]),
    )
    results = compute(doc)[("consolidated", "current")]
    assert results["cos"].value == Decimal("300")
    assert results["cos"].priority_used == "COS_P1"
    assert results["cos"].status == "DIRECTLY_EXTRACTED"


def test_cos_p2_falls_back_to_pbt_minus_oper_exp_final():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("6", "Profit before taxation is arrived at after charging",
              [_item("Depreciation of property, plant and equipment", "400")]),
    )
    results = compute(doc)[("consolidated", "current")]
    assert results["oper_exp"].value == Decimal("100")
    assert results["cos"].value == Decimal("300")
    assert results["cos"].priority_used == "COS_P2"


def test_combined_depreciation_and_amortisation_line_is_excluded():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation and amortisation", "999")]),
    )
    result = compute(doc)[("consolidated", "current")] if compute(doc) else None
    assert not result or result["oper_exp"].value is None


def test_movement_reconciliation_rows_are_excluded_from_asset_notes():
    doc = _doc(
        _note("15", "Property, plant and equipment", [
            _item("At 1 January", "1000"),
            _item("Depreciation charge for the year", "60"),
            _item("Disposals", "20"),
        ]),
        _note("7", "Cost of sales", [_item("Depreciation of property, plant and equipment", "10")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("50")
    assert result.priority_used == "P3"


def test_mixed_currency_within_a_dataset_is_not_used():
    doc = _doc(
        _note("28", "Administrative expenses", [
            _item("Depreciation of property, plant and equipment", "100", currency="HKD"),
            _item("Depreciation of property, plant and equipment", "50", currency="USD"),
        ]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value is None
    assert any(f.startswith("UNIT_MISMATCH") for f in result.flags)


def _item2(label, current, prior, role=LineRole.LINE, **kw):
    it = NoteItem(raw_label=label, role=role)
    for period, value in (("current", current), ("prior", prior)):
        ev = _ev(value, period=period, **kw)
        it.values[ev.key.model_dump_json()] = ev
    return it


def test_explicit_opex_inclusion_callout_overrides_item_sum():
    doc = _doc(
        _note("7", "LOSS FROM OPERATING ACTIVITIES", [
            _item2("Depreciation of property, plant and equipment", "306456", "364968"),
            _item2("Depreciation of right-of-use assets", "280961", "370867"),
        ], source_text=(
            "Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
            "included in \u201cother operating expenses\u201d on the face of the consolidated "
            "income statement.")),
    )
    current = compute(doc)[("consolidated", "current")]
    assert current["oper_exp"].value == Decimal("529841")
    assert current["oper_exp"].priority_used == "P1"
    assert current["cos"].value == Decimal("306456") + Decimal("280961") - Decimal("529841")
    assert current["cos"].priority_used == "COS_P2"
    prior = compute(doc)[("consolidated", "prior")]
    assert prior["oper_exp"].value == Decimal("665553")


def test_no_notes_yields_no_periods():
    assert compute(_doc()) == {}
