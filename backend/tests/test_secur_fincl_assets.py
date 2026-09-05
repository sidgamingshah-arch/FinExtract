"""services.secur_fincl_assets — the note-sourced parent-minus-deductions formula, with the
unabsorbed Level 3 amount carried from CP into LTP. See
docs/HKEX_Securities_Other_Financial_Assets_Extraction_Logic_Clean.md for the spec."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, UnitContext
from app.services.secur_fincl_assets import compute


def _ev(value, basis="consolidated", period="current", currency="HKD", scale="1"):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency=currency, scale_factor=Decimal(scale)))


def _item(label, value, role=LineRole.LINE, **kw):
    it = NoteItem(raw_label=label, role=role)
    ev = _ev(value, **kw)
    it.values[ev.key.model_dump_json()] = ev
    return it


def _note(number, title, items):
    return NotesTable(note_number=number, title=title, items=items)


def _face(note_number, section_hint):
    return LineItem(note_number=note_number, section_hint=section_hint)


def _doc(notes, faces):
    return DocumentModel(notes=notes, line_items=faces)


def test_cp_is_note_total_minus_deductions_minus_level_3():
    doc = _doc(
        [_note("5", "Financial assets at fair value through profit or loss", [
             _item("Financial assets at fair value through profit or loss", "1000", role=LineRole.TOTAL),
             _item("Derivative financial instruments", "100"),
         ]),
         _note("6", "Fair value hierarchy", [_item("Level 3", "50")])],
        [_face("5", "CURRENT ASSETS")])
    result = compute(doc)[("consolidated", "current")]["cp"]
    assert result.value == Decimal("850")
    assert result.status == "COMPUTED"


def test_negative_cp_reports_zero_and_carries_forward_to_ltp():
    doc = _doc(
        [_note("5", "Financial assets at fair value through profit or loss",
               [_item("Financial assets at fair value through profit or loss", "100", role=LineRole.TOTAL)]),
         _note("6", "Fair value hierarchy", [_item("Level 3", "300")]),
         _note("7", "Financial assets at fair value through other comprehensive income",
               [_item("Financial assets at fair value through other comprehensive income", "500",
                      role=LineRole.TOTAL)])],
        [_face("5", "CURRENT ASSETS"), _face("7", "NON-CURRENT ASSETS")])
    results = compute(doc)[("consolidated", "current")]
    assert results["cp"].value == Decimal("0")
    assert results["cp"].status == "NEGATIVE_CP_CARRIED_TO_LTP"
    # LTP base 500, minus the 200 of Level 3 the CP note could not absorb.
    assert results["ltp"].value == Decimal("300")
    assert results["ltp"].status == "COMPUTED"


def test_ltp_deducts_related_party_and_associate_and_jv_investments():
    doc = _doc(
        [_note("7", "Financial assets at fair value through other comprehensive income", [
             _item("Financial assets at fair value through other comprehensive income", "1000",
                   role=LineRole.TOTAL),
             _item("Investment in associates", "200"),
             _item("Investment in joint ventures", "100"),
         ])],
        [_face("7", "NON-CURRENT ASSETS")])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    # No Level 3 note at all -> no CP candidate -> missing carryforward -> LTP_base unclamped.
    assert result.value == Decimal("700")
    assert result.status == "MISSING_CP_CARRYFORWARD_TO_LTP"


def test_missing_level_3_note_is_flagged_and_not_treated_as_zero():
    doc = _doc(
        [_note("5", "Financial assets at fair value through profit or loss",
               [_item("Financial assets at fair value through profit or loss", "1000", role=LineRole.TOTAL)])],
        [_face("5", "CURRENT ASSETS")])
    result = compute(doc)[("consolidated", "current")]["cp"]
    assert result.value == Decimal("1000")
    assert result.status == "MISSING_LEVEL_3"
    assert "MISSING_LEVEL_3" in result.flags


def test_unclassified_note_is_not_used_for_either_field():
    doc = _doc(
        [_note("5", "Financial assets at fair value through profit or loss",
               [_item("Financial assets at fair value through profit or loss", "1000", role=LineRole.TOTAL)])],
        [])  # no face row cites note 5, so it cannot be classified current/non-current
    results = compute(doc).get(("consolidated", "current"), {})
    assert not results.get("cp") or results["cp"].value is None
    assert not results.get("ltp") or results["ltp"].value is None


def test_a_note_printing_both_a_subtotal_and_a_grand_total_is_counted_once():
    # §3.3: "Extract the note total once." Summing the grand total together with the subtotals
    # it already contains would count the note's contents twice.
    doc = _doc(
        [_note("9", "Financial assets at fair value through profit or loss", [
             _item("Listed equity securities", "300"),
             _item("Unlisted investments", "200"),
             # A PARTIAL subtotal, covering the listed holdings only — so the grand total and
             # the subtotals are different figures and the choice between them is observable.
             _item("Total listed securities", "300", role=LineRole.SUBTOTAL),
             _item("Total", "500", role=LineRole.TOTAL),
         ]),
         _note("30", "Fair value hierarchy", [_item("Level 3", "0")])],
        [_face("9", "Current assets")],
    )
    result = compute(doc)[("consolidated", "current")]["cp"]
    assert result.value == Decimal("500")


def test_a_note_with_only_subtotals_still_reports_them():
    doc = _doc(
        [_note("11", "Other financial assets", [
             _item("Debt investments", "120"),
             _item("Subtotal", "120", role=LineRole.SUBTOTAL),
         ]),
         _note("30", "Fair value hierarchy", [_item("Level 3", "0")])],
        [_face("11", "Current assets")],
    )
    result = compute(doc)[("consolidated", "current")]["cp"]
    assert result.value == Decimal("120")
