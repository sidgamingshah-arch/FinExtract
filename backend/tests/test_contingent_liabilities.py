"""services.contingent_liabilities — classification priority, deduplication, aggregation, and the
narrative paragraph/tables. See docs/PRC_Contingent_Liabilities_Extraction_Logic_Revised.md."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, UnitContext
from app.services.contingent_liabilities import (
    UNCLASSIFIED, ContingentLiabilitiesNarrative, compute, enhance_with_llm)


def _ev(value=None, basis="consolidated", period="current"):
    return ExtractedValue(value=None if value is None else Decimal(value),
                          value_raw=None if value is None else Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency="CNY", scale_factor=Decimal(1)))


def _item(label, value=None, group_hint="", role=LineRole.LINE):
    it = NoteItem(raw_label=label, group_hint=group_hint, role=role)
    ev = _ev(value)
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
