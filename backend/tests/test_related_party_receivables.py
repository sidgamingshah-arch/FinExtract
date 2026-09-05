"""services.related_party_receivables — Due from Related Parties (LTP) is MAX_VALID across three
candidates; Other Receivables (CP) is a gross pool less its own proven related-party deduction.
Per the reviewer's directive, a condition that would null the value under the strict spec instead
populates the best-available figure with a review flag. See
docs/PRC_Related_Party_and_Other_Receivables_Extraction_Logic.md."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, UnitContext
from app.services.related_party_receivables import _finalize_cp, compute


def _ev(value, basis="consolidated", period="current"):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency="CNY", scale_factor=Decimal(1)))


def _face(label, value, section_hint, group_hint=""):
    li = LineItem(source_label=label, section_hint=section_hint, group_hint=group_hint)
    li.set_value(_ev(value))
    return li


def _item(label, value, group_hint="", role=LineRole.LINE):
    it = NoteItem(raw_label=label, group_hint=group_hint, role=role)
    ev = _ev(value)
    it.values[ev.key.model_dump_json()] = ev
    return it


def _note(number, title, items):
    return NotesTable(note_number=number, title=title, items=items)


def _doc(notes=(), faces=()):
    return DocumentModel(notes=list(notes), line_items=list(faces))


def test_find_1_is_a_face_row_explicitly_identified_as_related_party():
    doc = _doc(faces=[_face("其他应收款", "500", "非流动资产", group_hint="关联方")])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("500")
    assert result.status == "COMPUTED"


def test_ltp_selects_the_max_not_the_sum_of_the_three_candidates():
    doc = _doc(
        faces=[_face("应收关联方款项", "500", "非流动资产")],
        notes=[_note("12", "其他应收款", [_item("期末余额", "800", group_hint="关联方组合")]),
               _note("40", "关联方及关联交易", [_item("其他应收款项", "300")])])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("800")
    assert result.formula_used == "MAX_VALID(Find_2)"


def test_entrusted_loans_are_excluded_from_all_three_ltp_candidates():
    doc = _doc(faces=[_face("委托贷款及垫款- 关联方", "900", "非流动资产")])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value is None
    assert "ENTRUSTED_LOAN_NOT_SEPARABLE" in result.flags


def test_related_party_note_excludes_payables_and_deposits_and_investments():
    doc = _doc(notes=[_note("40", "关联方及关联交易", [
        _item("其他应收款项", "300"),
        _item("应付关联方款项", "999"),
        _item("预收款项", "999"),
        _item("关联方投资", "999"),
    ])])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("300")


def test_cp_gross_minus_related_party_deduction_within_the_same_pool():
    doc = _doc(faces=[
        _face("其他应收款", "1000", "流动资产"),
        _face("拆出资金", "200", "流动资产", group_hint="关联方"),
    ])
    result = compute(doc)[("consolidated", "current")]["cp"]
    assert result.value == Decimal("1000")
    assert result.status == "COMPUTED"


def test_cp_deduction_does_not_exclude_entrusted_loans_unlike_ltp():
    doc = _doc(faces=[_face("拆出资金", "400", "流动资产", group_hint="关联方委托贷款")])
    result = compute(doc)[("consolidated", "current")]["cp"]
    # CP's own deduction test has no entrusted-loan carve-out: a related-party face row in the
    # gross pool is deducted regardless, so gross(400) - deduction(400) = 0.
    assert result.value == Decimal("0")


def test_negative_cp_candidate_is_populated_with_a_review_flag_not_nulled():
    # The deduction is a subset of the gross pool by construction in this pipeline (see
    # _cp_pool), so a negative candidate is exercised directly against the arithmetic that would
    # see one from a source that does not guarantee the subset relationship.
    candidate, flags = _finalize_cp(Decimal("100"), Decimal("150"))
    assert candidate == Decimal("-50")
    assert "NEGATIVE_RESIDUAL" in flags


def test_missing_deduction_is_populated_from_gross_with_a_review_flag():
    candidate, flags = _finalize_cp(Decimal("100"), None)
    assert candidate == Decimal("100")
    assert "RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED" in flags


def test_nothing_found_at_all_stays_null():
    doc = _doc()
    assert compute(doc) == {}
