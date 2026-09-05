"""services.related_party_receivables — Due from Related Parties (LTP) is MAX_VALID across three
candidates; Other Receivables (CP) is a gross pool less its own proven related-party deduction.
See docs/PRC_Related_Party_and_Other_Receivables_Extraction_Logic.md."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, UnitContext
from app.services.related_party_receivables import _finalize_cp, compute


def _ev(value, basis="consolidated", period="current", currency="CNY", scale="1"):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency=currency, scale_factor=Decimal(scale)))


def _face(label, value, section_hint, group_hint="", **kw):
    li = LineItem(source_label=label, section_hint=section_hint, group_hint=group_hint)
    li.set_value(_ev(value, **kw))
    return li


def _item(label, value, group_hint="", role=LineRole.LINE, **kw):
    it = NoteItem(raw_label=label, group_hint=group_hint, role=role)
    ev = _ev(value, **kw)
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


def test_a_negative_cp_candidate_is_nulled_rather_than_published():
    # §5.4: a negative result "indicates an extraction, scope, unit, or duplication issue".
    # This row is a summed child of Total Current Assets, so a negative asset would break the
    # balance-sheet identity two levels up rather than flagging the row that actually failed.
    candidate, status, flags = _finalize_cp(Decimal("100"), Decimal("150"), inspected=True)
    assert candidate is None
    assert status == "NEGATIVE_RESIDUAL"
    assert "NEGATIVE_RESIDUAL" in flags


def test_an_unestablished_deduction_is_nulled_rather_than_publishing_the_gross_pool():
    # §8: returning the gross pool would put the same related-party money in this row and in
    # Due from Related Parties (LTP) at once.
    candidate, status, flags = _finalize_cp(Decimal("100"), None, inspected=False)
    assert candidate is None
    assert status == "RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED"
    assert "RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED" in flags


def test_a_searched_pool_with_no_related_party_line_deducts_a_proven_zero():
    # §3.6 permits zero "when all relevant notes have been searched and explicitly contain no
    # qualifying amount" — the distinction that separates this case from the one above.
    candidate, status, flags = _finalize_cp(Decimal("100"), None, inspected=True)
    assert candidate == Decimal("100")
    assert status == "COMPUTED"
    assert "RELATED_PARTY_DEDUCTION_PROVEN_NIL" in flags
    assert "RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED" not in flags


def test_an_ordinary_deduction_is_subtracted():
    candidate, status, flags = _finalize_cp(Decimal("100"), Decimal("30"), inspected=True)
    assert candidate == Decimal("70")
    assert status == "COMPUTED"


def test_nothing_found_at_all_stays_null():
    doc = _doc()
    assert compute(doc) == {}


# ── §3.1/§9: units are normalised before addition, and a mix is reported not summed ───────────
def test_a_candidate_mixing_scales_is_unusable_rather_than_silently_wrong():
    doc = _doc(notes=[
        _note("8", "其他应收款", [
            _item("应收关联方款项", "500", scale="1"),
            _item("关联方往来款", "2", scale="1000"),
        ]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert "UNIT_MISMATCH:Find_2" in result.flags
    assert result.value is None
    assert result.status == "NOT_COMPUTABLE"


def test_a_candidate_mixing_currencies_is_reported():
    doc = _doc(notes=[
        _note("8", "其他应收款", [
            _item("应收关联方款项", "500", currency="CNY"),
            _item("关联方往来款", "300", currency="USD"),
        ]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert "CURRENCY_MISMATCH:Find_2" in result.flags


# ── §4.5/§8: validity, and ties as corroboration ─────────────────────────────────────────────
def test_a_negative_candidate_never_wins_the_maximum():
    # Find_2 is negative and Find_3 positive: the negative is rejected, not merely out-maxed.
    doc = _doc(notes=[
        _note("8", "其他应收款", [_item("应收关联方款项", "-400")]),
        _note("40", "关联方及关联交易", [_item("应收关联方款项", "250")]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("250")
    assert any(f.startswith("NEGATIVE_RESIDUAL:") for f in result.flags)


def test_all_candidates_negative_leaves_the_field_uncomputable():
    doc = _doc(notes=[_note("8", "其他应收款", [_item("应收关联方款项", "-400")])])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value is None
    assert result.status == "NOT_COMPUTABLE"


def test_tied_candidates_corroborate_the_figure_instead_of_flagging_duplication():
    # §4.5: "Select the value once. Retain all tied candidates as supporting sources."
    doc = _doc(notes=[
        _note("8", "其他应收款", [_item("应收关联方款项", "700")]),
        _note("40", "关联方及关联交易", [_item("应收关联方款项", "700")]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("700")
    assert "corroborated by" in (result.formula_used or "")
    assert not any(f.startswith("POSSIBLE_DUPLICATE") for f in result.flags)


def test_the_selected_candidate_carries_its_evidence():
    doc = _doc(notes=[_note("8", "其他应收款", [_item("应收关联方款项", "700")])])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.evidence
    assert any(e.get("line_item") == "应收关联方款项" for e in result.evidence)


def test_a_balance_restated_in_a_second_note_is_not_added_twice_within_one_candidate():
    # §3.5: "If the same related-party balance appears under two source notes but represents the
    # same underlying receivable, retain one value within that candidate." Both note headings are
    # Simplified because §3.1 scopes this rule to Simplified-Chinese PRC filings — the English /
    # Traditional pairing that §3.1 of the HKEX specs warns about does not arise here.
    doc = _doc(notes=[
        _note("8", "其他应收款", [_item("应收关联方款项", "900")]),
        _note("9", "长期应收款", [_item("应收关联方款项", "900")]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("900")
    assert "POSSIBLE_DUPLICATE:Find_2" in result.flags


def test_two_different_related_party_balances_in_two_notes_are_both_counted():
    doc = _doc(notes=[
        _note("8", "其他应收款", [_item("应收关联方款项", "900")]),
        _note("9", "长期应收款", [_item("关联方往来款", "300")]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("1200")
    assert "POSSIBLE_DUPLICATE:Find_2" not in result.flags


# ── §4.3: a pooled allowance makes the net related-party amount underivable ───────────────────
def test_a_pooled_allowance_flags_the_candidate_but_still_reports_a_figure():
    # Gross debtor balances plus ONE combined provision: the related-party share of that
    # provision cannot be read off, and §4.3 forbids allocating it. The gross figure is still
    # reported so a reviewer has a number to check.
    doc = _doc(notes=[
        _note("8", "其他应收款", [
            _item("应收关联方款项", "1000"),
            _item("坏账准备", "-60"),
        ]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("1000")
    assert "NET_AMOUNT_NOT_DERIVABLE:Find_2" in result.flags


def test_an_explicitly_net_amount_is_not_flagged_even_beside_a_pooled_allowance():
    # §3.2's first preference: the line says it is already a net carrying amount.
    doc = _doc(notes=[
        _note("8", "其他应收款", [
            _item("应收关联方款项账面价值", "940"),
            _item("坏账准备", "-60"),
        ]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("940")
    assert not any(f.startswith("NET_AMOUNT_NOT_DERIVABLE") for f in result.flags)


def test_a_note_with_no_pooled_allowance_is_not_flagged():
    doc = _doc(notes=[_note("8", "其他应收款", [_item("应收关联方款项", "1000")])])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert result.value == Decimal("1000")
    assert not any(f.startswith("NET_AMOUNT_NOT_DERIVABLE") for f in result.flags)


def test_a_line_stating_it_is_net_of_the_allowance_is_not_flagged():
    # §3.2's second preference: a closing balance already less its provision.
    doc = _doc(notes=[
        _note("8", "其他应收款", [
            _item("应收关联方款项期末余额减坏账准备", "940"),
            _item("坏账准备", "-60"),
        ]),
    ])
    result = compute(doc)[("consolidated", "current")]["ltp"]
    assert not any(f.startswith("NET_AMOUNT_NOT_DERIVABLE") for f in result.flags)
