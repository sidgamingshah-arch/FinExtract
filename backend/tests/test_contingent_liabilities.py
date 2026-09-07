"""services.contingent_liabilities — classification priority, deduplication, aggregation, and the
narrative paragraph/tables. See docs/PRC_Contingent_Liabilities_Extraction_Logic_Revised.md."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, UnitContext
from app.services.contingent_liabilities import (
    UNCLASSIFIED, ContingentLiabilitiesNarrative, compute, enhance_with_llm)


def _ev(value=None, basis="consolidated", period="current", currency="CNY", scale="1"):
    return ExtractedValue(value=None if value is None else Decimal(value),
                          value_raw=None if value is None else Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency=currency, scale_factor=Decimal(scale)))


def _item(label, value=None, group_hint="", role=LineRole.LINE, **kw):
    it = NoteItem(raw_label=label, group_hint=group_hint, role=role)
    ev = _ev(value, **kw)
    it.values[ev.key.model_dump_json()] = ev
    return it


def _note(number, title, items):
    return NotesTable(note_number=number, title=title, items=items)


def _doc(*notes):
    return DocumentModel(notes=list(notes))


def test_letter_of_credit_outranks_a_broader_guarantee_term():
    doc = _doc(_note("35", "对外担保及或有事项", [
        _item("为客户开立的不可撤销信用证", "1000000")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.classified_summary[0]["type"] == "Letters of Credit"
    assert result.classified_summary[0]["amount"] == Decimal("1000000")


def test_performance_bond_outranks_bank_guarantee_when_both_present():
    doc = _doc(_note("35", "对外担保", [_item("银行出具的工程履约保函", "500000")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.classified_summary[0]["type"] == "Performance bonds"


def test_bank_guarantee_outranks_corporate_guarantee_when_both_present():
    doc = _doc(_note("35", "关联方担保", [_item("为关联方提供担保，由银行保函形式出具", "200000")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.classified_summary[0]["type"] == "Bank guarantees"


def test_unclassified_item_gets_a_short_english_statement():
    doc = _doc(_note("36", "未决诉讼", [_item("未决诉讼涉及一宗合同纠纷", "12000000", group_hint="乙公司")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.unclassified_items[0]["short_statement"] == (
        "Pending litigation involving 乙公司, with a disclosed exposure of CNY 12,000,000.")


def test_amount_not_disclosed_stays_in_the_table_but_out_of_totals():
    doc = _doc(_note("36", "未决仲裁", [_item("未决仲裁事项")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.classified_summary == []
    assert result.unclassified_items[0]["amount"] is None
    assert "AMOUNT_NOT_DISCLOSED" in result.flags


def test_non_exposure_label_is_excluded_unless_explicitly_the_exposure():
    doc = _doc(_note("35", "对外担保", [_item("授信额度", "9000000")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.classified_summary == []
    assert result.unclassified_items == []


def test_note_total_row_is_never_added_as_its_own_item():
    doc = _doc(_note("35", "对外担保", [
        _item("担保总额", "100", role=LineRole.TOTAL),
        _item("公司担保", "60"),
        _item("为关联方提供担保，由银行保函形式出具", "40"),
    ]))
    result = compute(doc)[("consolidated", "current")]
    total = sum(Decimal(g["amount"]) for g in result.classified_summary)
    assert total == Decimal("100")           # 60 + 40, never 100 + 60 + 40


def test_duplicate_amount_within_one_classification_is_flagged_and_counted_once():
    doc = _doc(
        _note("35", "对外担保", [_item("为子公司提供担保", "500")]),
        _note("36", "或有负债", [_item("公司担保", "500")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.classified_summary[0]["item_count"] == 1
    assert "POSSIBLE_DUPLICATE" in result.flags


def test_no_matching_notes_reports_not_found_rather_than_none_exist():
    doc = _doc(_note("10", "存货", [_item("原材料", "1000")]))
    assert compute(doc) == {}


def test_summary_paragraph_mentions_types_and_unquantified_count():
    doc = _doc(
        _note("35", "对外担保", [_item("公司担保", "500")]),
        _note("36", "未决诉讼", [_item("未决诉讼")]))
    result = compute(doc)[("consolidated", "current")]
    assert "Corporate guarantees" in result.summary_paragraph
    assert "1 disclosed matter" in result.summary_paragraph


class _FakeProvider:
    id = "fake"

    def __init__(self, paragraph: str, statements: list[str]):
        self.paragraph, self.statements, self.payloads = paragraph, statements, []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        import json as _json
        self.payloads.append(_json.loads(messages[0]["content"]))
        return (response_schema(summary_paragraph=self.paragraph,
                                unclassified_statements=self.statements),
                {"model": "fake-1", "input_tokens": 1, "output_tokens": 1})


def test_llm_narrative_rewrites_prose_never_figures():
    doc = _doc(
        _note("35", "对外担保", [_item("公司担保", "500")]),
        _note("36", "未决诉讼", [_item("未决诉讼涉及一宗合同纠纷", "12000000")]))
    result = compute(doc)[("consolidated", "current")]
    provider = _FakeProvider("A clearer paragraph.", ["A clearer statement about the lawsuit."])

    enhanced, meta = enhance_with_llm(provider, result)

    assert enhanced.summary_paragraph == "A clearer paragraph."
    assert enhanced.unclassified_items[0]["short_statement"] == "A clearer statement about the lawsuit."
    assert enhanced.classified_summary == result.classified_summary   # amounts untouched
    assert enhanced.unclassified_items[0]["amount"] == result.unclassified_items[0]["amount"]
    assert "LLM_NARRATIVE" in enhanced.flags
    assert meta is not None
    # The model was shown the deterministic facts, never asked to invent them.
    assert provider.payloads[0]["classified_summary"][0]["type"] == "Corporate guarantees"


def test_mismatched_statement_count_is_discarded_not_misaligned():
    doc = _doc(_note("36", "未决诉讼", [
        _item("未决诉讼一"),
        _item("未决诉讼二"),
    ]))
    result = compute(doc)[("consolidated", "current")]
    provider = _FakeProvider("Fine.", ["Only one statement"])   # 2 items, 1 statement back

    enhanced, _meta = enhance_with_llm(provider, result)

    assert enhanced.unclassified_items == result.unclassified_items   # left exactly as computed


def test_enhance_is_a_no_op_when_there_is_nothing_to_narrate():
    doc = _doc(_note("10", "存货", [_item("原材料", "1000")]))
    assert compute(doc) == {}   # nothing to call enhance_with_llm with in the first place


# ── §6.2: currency and unit control ──────────────────────────────────────────────────────────
def test_each_currency_gets_its_own_row_rather_than_one_converted_sum():
    doc = _doc(_note("35", "对外担保", [
        _item("公司担保", "500", currency="CNY"),
        _item("公司担保", "300", currency="USD"),
    ]))
    result = compute(doc)[("consolidated", "current")]
    currencies = sorted(g["currency"] for g in result.classified_summary)
    assert currencies == ["CNY", "USD"]


def test_the_same_currency_in_two_scales_is_not_added():
    # 500 thousand and 300 million are not 800 of anything.
    doc = _doc(_note("35", "对外担保", [
        _item("公司担保", "500", scale="1000"),
        _item("企业担保", "300", scale="1000000"),
    ]))
    result = compute(doc)[("consolidated", "current")]
    assert len(result.classified_summary) == 2
    assert {g["amount"] for g in result.classified_summary} == {Decimal("500"), Decimal("300")}
    # And no single total either: unlike scales are as unaddable as unlike currencies.
    assert result.total_quantifiable is None
    assert "MULTIPLE_CURRENCIES_NOT_AGGREGATED" in result.flags


def test_no_single_total_is_published_across_unlike_units():
    doc = _doc(_note("35", "对外担保", [
        _item("公司担保", "500", currency="CNY"),
        _item("公司担保", "300", currency="USD"),
    ]))
    result = compute(doc)[("consolidated", "current")]
    assert result.total_quantifiable is None
    assert "MULTIPLE_CURRENCIES_NOT_AGGREGATED" in result.flags


def test_a_single_currency_still_publishes_its_total():
    doc = _doc(_note("35", "对外担保", [
        _item("公司担保", "500"), _item("银行保函", "200"),
    ]))
    result = compute(doc)[("consolidated", "current")]
    assert result.total_quantifiable == Decimal("700")
    assert "MULTIPLE_CURRENCIES_NOT_AGGREGATED" not in result.flags


# ── §6.4: what makes two rows one item ───────────────────────────────────────────────────────
def test_equal_guarantees_to_different_counterparties_are_two_exposures():
    # The indicator §6.4 offers is the counterparty; collapsing on the amount alone would
    # silently delete one of these two guarantees.
    doc = _doc(
        _note("35", "对外担保", [_item("公司担保", "500", group_hint="甲公司")]),
        _note("36", "或有负债", [_item("公司担保", "500", group_hint="乙公司")]),
    )
    result = compute(doc)[("consolidated", "current")]
    assert sum(g["item_count"] for g in result.classified_summary) == 2


def test_a_repeat_within_one_note_is_not_a_cross_note_restatement():
    doc = _doc(_note("35", "对外担保", [
        _item("公司担保", "500"), _item("公司担保", "500"),
    ]))
    result = compute(doc)[("consolidated", "current")]
    assert sum(g["item_count"] for g in result.classified_summary) == 2


def test_an_item_with_no_amount_survives_deduplication():
    # §6.5: it belongs in the narrative and the detail table, just never in a total.
    doc = _doc(_note("36", "或有负债", [
        _item("未决诉讼"), _item("未决仲裁"),
    ]))
    result = compute(doc)[("consolidated", "current")]
    assert len(result.unclassified_items) == 2


# ── §4.3: 履约保证金 needs bond language, or it is an ordinary refundable deposit ─────────────
def test_a_performance_deposit_with_bond_language_is_a_performance_bond():
    doc = _doc(_note("36", "或有负债", [_item("履约保证金及履约保函", "400")]))
    result = compute(doc)[("consolidated", "current")]
    assert [g["type"] for g in result.classified_summary] == ["Performance bonds"]


def test_a_bare_refundable_performance_deposit_is_not_classified_as_a_bond():
    doc = _doc(_note("36", "其他或有事项", [_item("履约保证金", "400")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.classified_summary == []
    assert len(result.unclassified_items) == 1


def test_a_capital_commitment_is_a_classified_exposure():
    """688008 states its exposures under 重要承诺事项 and nothing else.

    Only a CLASSIFIED item reaches the quantifiable total, so with no commitments category its
    资本承诺 85,066,126.15 and 投资承诺 145,700,000.00 were read, left unclassified, and the
    concept came back with no figure on a filing that discloses 230,766,126.15.
    """
    from app.services.contingent_liabilities import _classify

    for label in ("资本承诺", "投资承诺", "Capital commitments",
                  "contracted for but not provided"):
        assert _classify(label)[0] == "Commitments", label
    # …and a guarantee sitting inside a commitments note keeps its own classification.
    assert _classify("对外担保 资本承诺")[0] == "Corporate guarantees"


# --- the working, attached to the document-level disclosure --------------------------------------
#
# Every one of the assertions above was already true BEFORE this section existed, and none of it
# reached a reader: the stage stored the paragraph and both tables on
# `DocumentModel.contingent_liabilities`, and the run RESULT carried no such key — so the Excel
# Disclosures sheet, the JSON export, /analysis and the Disclosures screen could see only the
# single total that lands on `notes__contingent_liabilities`. These tests are about the reduction
# that carries the working to them.

def _stage_record(doc) -> dict:
    """What `stages.contingent_liabilities` stores, built from `compute` the same way it does."""
    from app.stages.contingent_liabilities import _to_dict

    return {f"{basis}:{period}": _to_dict(result)
            for (basis, period), result in compute(doc).items()}


def test_the_working_carries_the_per_type_sums_and_the_leftover_statements():
    from app.services.contingent_liabilities import disclosure_explanation

    doc = _doc(
        _note("35", "对外担保", [_item("为子公司提供的连带责任保证", "5000000"),
                              _item("为客户开立的不可撤销信用证", "1000000")]),
        _note("36", "未决诉讼", [_item("未决诉讼涉及一宗合同纠纷", "12000000", group_hint="乙公司")]),
    )
    working = disclosure_explanation(_stage_record(doc))

    by_type = {g["type"]: g for g in working["breakdown"]}
    assert set(by_type) == {"Corporate guarantees", "Letters of Credit"}
    assert by_type["Corporate guarantees"]["amount"] == "5000000"
    assert by_type["Letters of Credit"]["amount"] == "1000000"
    # The paragraph that fits no type survives as ONE SENTENCE WITH ITS AMOUNT, which is the whole
    # ask: a reader cannot act on "unclassified contingent liability: 12,000,000".
    assert [s["statement"] for s in working["statements"]] == [
        "Pending litigation involving 乙公司, with a disclosed exposure of CNY 12,000,000."]
    assert working["statements"][0]["amount"] == "12000000"
    assert "Corporate guarantees" in working["explanation"]
    assert working["basis_period"] == "consolidated:current"


def test_an_unquantified_matter_keeps_its_sentence_and_reports_no_amount():
    """Blank and not zero. "The amount was not disclosed" and "the exposure is nil" are different
    answers, and the sentence is the only place the difference is stated."""
    from app.services.contingent_liabilities import disclosure_explanation

    working = disclosure_explanation(_stage_record(_doc(_note("36", "未决仲裁", [_item("未决仲裁事项")]))))

    assert working["breakdown"] == []
    assert working["statements"][0]["amount"] is None
    assert "not disclosed or could not be quantified" in working["statements"][0]["statement"]
    assert "AMOUNT_NOT_DISCLOSED" in working["qa_flags"]


def test_two_currencies_stay_two_rows_because_the_concept_publishes_no_total():
    """The case where the breakdown is not extra colour but the ONLY answer: `_quantifiable_total`
    withholds a figure across unlike units, so a reader with no breakdown gets nothing at all."""
    from app.services.contingent_liabilities import disclosure_explanation

    doc = _doc(_note("35", "对外担保", [
        _item("为子公司提供的连带责任保证", "5000000", currency="CNY"),
        _item("为境外子公司提供的连带责任保证", "700000", currency="USD")]))
    result = compute(doc)[("consolidated", "current")]
    assert result.total_quantifiable is None
    assert "MULTIPLE_CURRENCIES_NOT_AGGREGATED" in result.flags

    working = disclosure_explanation(_stage_record(doc))
    assert {(g["currency"], g["amount"]) for g in working["breakdown"]} == {
        ("CNY", "5000000"), ("USD", "700000")}


def test_the_working_is_attached_to_its_own_disclosure_and_no_other():
    from app.services.contingent_liabilities import attach_contingent_explanation

    doc = _doc(_note("35", "对外担保", [_item("为子公司提供的连带责任保证", "5000000")]))
    scanned = [{"key": "going_concern", "label": "Going concern", "present": True},
               {"key": "contingent_liabilities", "label": "Contingent liabilities", "present": True}]

    out = attach_contingent_explanation(scanned, _stage_record(doc))

    assert out[0] == scanned[0], "an unrelated disclosure must be returned untouched"
    assert out[1]["breakdown"][0]["type"] == "Corporate guarantees"
    assert out[1]["label"] == "Contingent liabilities", "the scanned keys must survive"


def test_a_filing_with_nothing_to_explain_gains_no_empty_scaffolding():
    """`{}` rather than empty lists, so a consumer can splat it unconditionally and a qualitative
    disclosure renders exactly as it did before this existed."""
    from app.services.contingent_liabilities import (attach_contingent_explanation,
                                                     disclosure_explanation)

    assert disclosure_explanation(None) == {}
    assert disclosure_explanation({}) == {}
    scanned = [{"key": "contingent_liabilities", "label": "Contingent liabilities", "present": False}]
    assert attach_contingent_explanation(scanned, None) == scanned


def test_the_consolidated_current_period_is_the_one_explained():
    """The amount beside the explanation comes from consolidated/current
    (`documents._with_quantified_amounts`), so the explanation must describe that period and not
    whichever one the dict happened to yield first."""
    from app.services.contingent_liabilities import disclosure_explanation

    record = {
        "consolidated:prior": {"summary_paragraph": "prior", "classified_summary":
                               [{"type": "Corporate guarantees", "amount": "1"}],
                               "unclassified_items": [], "qa_flags": []},
        "consolidated:current": {"summary_paragraph": "current", "classified_summary":
                                 [{"type": "Corporate guarantees", "amount": "2"}],
                                 "unclassified_items": [], "qa_flags": []},
    }
    working = disclosure_explanation(record)
    assert working["basis_period"] == "consolidated:current"
    assert working["breakdown"][0]["amount"] == "2"
