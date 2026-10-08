"""Three rules for writing the model's cited figures on an Indian filing (`stages.line_item_llm`).

* A NOTE row with no entity of its own belongs to the statements printed before it. Note pages
  carry no entity, so their rows defaulted to CONSOLIDATED and every figure the model cited from
  the notes of a standalone-only annual report missed the standalone spread a CMA is read from.
* The written figure is the line's WHOLE figure for its basis and period, so no other row carrying
  the line may still hold one there (Schedule III's two trade-payable rows; a stray second row).
* A face aggregate swept into a catch-all BEFORE the model was asked keeps only what its note's
  cited rows leave over, or the note's rows are counted twice.

All three are switched on only for an Indian filing (`services.regime`), like the regime's
reading rules.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NotesTable
from app.stages import line_item_llm as stage


def _ctx():
    logs = []
    return SimpleNamespace(log=logs.append, logs=logs, indian_filing=True)


def _value(basis, period, amount, page=1):
    return ExtractedValue(basis=basis, period_label=period, value=Decimal(amount),
                          value_raw=Decimal(amount), provenance=Provenance(page_index=page))


def _doc(pages, notes=()):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=i, kind=kind, statement=st, scope=scope)
                 for i, kind, st, scope in pages]
    doc.notes = list(notes)
    return doc


def _note_entry(page, note="21", basis="consolidated"):
    return {"note": note, "caption": "Cash credit", "basis": basis,
            "figures": {"current": "100"}, "provenance": {"page_index": page}}


def test_a_note_after_the_companys_statements_is_the_companys():
    doc = _doc([(1, PageKind.FACE, "balance_sheet", "company"), (6, PageKind.NOTES, None, None)])
    assert stage._indian_note_is_the_companys(doc, _note_entry(6))


def test_a_note_after_the_groups_statements_is_the_groups():
    doc = _doc([(1, PageKind.FACE, "balance_sheet", "company"),
                (9, PageKind.FACE, "balance_sheet", "consolidated"),
                (14, PageKind.NOTES, None, None)])
    assert not stage._indian_note_is_the_companys(doc, _note_entry(14))
    assert stage._indian_note_is_the_companys(doc, _note_entry(5))


def test_a_note_that_says_whose_it_is_keeps_it():
    doc = _doc([(1, PageKind.FACE, "balance_sheet", "company")],
               notes=[NotesTable(note_number="21", title="Borrowings", basis=Basis.CONSOLIDATED)])
    assert not stage._indian_note_is_the_companys(doc, _note_entry(6))
    plain = _doc([(1, PageKind.FACE, "balance_sheet", "company")])
    assert not stage._indian_note_is_the_companys(plain, {**_note_entry(6), "on_face": True})
    assert not stage._indian_note_is_the_companys(plain, _note_entry(6, basis="standalone"))


def test_the_cited_figure_stands_down_the_lines_other_rows():
    key = "bs_icon__sundry_creditors_trade"
    a = LineItem(source_label="(A) micro and small enterprises", canonical_key=key)
    a.values["c"] = _value(Basis.STANDALONE, "current", "2184.36")
    a.values["p"] = _value(Basis.STANDALONE, "prior", "1986.75")
    b = LineItem(source_label="(B) others", canonical_key=key)
    b.values["c"] = _value(Basis.STANDALONE, "current", "11096.90")
    other = LineItem(source_label="Unrelated", canonical_key="bs_icon__share_capital")
    other.values["c"] = _value(Basis.STANDALONE, "current", "1824")
    doc = DocumentModel(filename="ar.pdf")
    doc.line_items = [a, b, other]
    stage._stand_down_other_rows(doc, b, key, "standalone", "current", _ctx())
    assert set(a.values) == {"p"}, "only this basis and period are stood down"
    assert "llm_superseded_by_cited_figure:standalone:current" in a.confidence.flags
    assert set(b.values) == {"c"} and set(other.values) == {"c"}


def _catch_all_doc(amount):
    swept = LineItem(source_label="(iii) Other financial liabilities",
                     canonical_key="bs_icon__other_current_liabilities_others", note_number="23")
    swept.values["c"] = _value(Basis.STANDALONE, "current", amount)
    doc = DocumentModel(filename="ar.pdf")
    doc.line_items = [swept]
    return doc, swept


_SET = SimpleNamespace(items=[
    SimpleNamespace(key="bs_icon__other_current_liabilities_others",
                    value_scope="exclusive_residual", residual_policy=None,
                    section_scope=["bs_cl"]),
    SimpleNamespace(key="bs_icon__interest_accrued", value_scope="exclusive_leaf",
                    residual_policy=None, section_scope=["bs_cl"]),
])


def _explained(*pairs):
    explained = {}
    for item, figure in pairs:
        stage._record_explained(explained, item, [
            {"note": "23", "basis": "standalone", "figures": {"current": figure}}], [], False)
    return explained


def test_a_swept_aggregate_keeps_only_what_its_notes_cited_rows_leave():
    line = _SET.items[1]
    doc, swept = _catch_all_doc("1858.24")
    stage._explain_swept_rows(doc, _SET, _explained((line, "1323.49")), _ctx())
    assert swept.values["c"].value == Decimal("534.75")

    doc, swept = _catch_all_doc("1323.49")
    stage._explain_swept_rows(doc, _SET, _explained((line, "1323.49")), _ctx())
    assert not swept.values, "fully explained: nothing is left in the catch-all"


def test_more_than_the_aggregate_is_a_citation_error_not_a_leftover():
    doc, swept = _catch_all_doc("100")
    stage._explain_swept_rows(doc, _SET, _explained((_SET.items[1], "250")), _ctx())
    assert swept.values["c"].value == Decimal("100")
    assert any(f.startswith("llm_note_rows_exceed_swept_aggregate") for f in
               swept.confidence.flags)


def test_a_line_in_another_section_explains_nothing_here():
    elsewhere = SimpleNamespace(key="bs_icon__security_deposits", value_scope="exclusive_leaf",
                                residual_policy=None, section_scope=["bs_nca"])
    doc, swept = _catch_all_doc("1858.24")
    stage._explain_swept_rows(doc, _SET, _explained((elsewhere, "1323.49")), _ctx())
    assert swept.values["c"].value == Decimal("1858.24")
