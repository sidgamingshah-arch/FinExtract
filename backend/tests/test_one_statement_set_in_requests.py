"""A REQUEST SENDS, AND A CITATION RESOLVES IN, THE ONE STATEMENT SET THE FACE READS.

PetroChina (hkex_0857) prints a CAS balance sheet (page index 125-126) and an IFRS one (227-228).
Since `statement_sets.ONE_SET_STATEMENTS` named the balance sheet, only the IFRS set fed the face,
but every request still carried BOTH consolidated `balance_sheet` blocks, the CAS one marked
`kept_apart`. A citation names a statement, an entity and a caption, never a set, and the two
sheets' captions differ only by case ("TOTAL NON-CURRENT ASSETS" / "Total non-current assets"),
which the gateway folds. A blind answer citing the IFRS 2,161,907 resolved to the CAS row,
2,162,163, printed first (requests 912a53c6c576d304, c9b638b8a636b7cb).

The other set of a ONE_SET statement is now neither sent nor looked up; its rows are still
extracted. A statement outside ONE_SET_STATEMENTS keeps its other set apart, as before.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import face_context
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

CAS_BS, IFRS_BS, CAS_EQ, IFRS_EQ = 125, 227, 129, 230


def _page(index, statement, framework):
    page = PageSource(index=index, kind=PageKind.FACE, statement=statement)
    page.evidence["framework"] = framework
    return page


def _row(caption, page, current, prior=None):
    li = LineItem(source_label=caption)
    for label, v in (("current", current), ("prior", prior)):
        if v is not None:
            li.values[label] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label,
                                              value=Decimal(v), value_raw=Decimal(v),
                                              provenance=Provenance(page_index=page))
    return li


def _petrochina():
    doc = DocumentModel(filename="hkex_0857.pdf")
    doc.pages = [_page(CAS_BS, "balance_sheet", "CAS"), _page(CAS_EQ, "changes_in_equity", "CAS"),
                 _page(IFRS_BS, "balance_sheet", "IFRS"),
                 _page(IFRS_EQ, "changes_in_equity", "IFRS")]
    # Document order, as the filing prints them: the CAS set first.
    doc.line_items = [_row("Total non-current assets", CAS_BS, "2162163", "2096139"),
                      _row("Total non-current liabilities", CAS_BS, "405827", "433096"),
                      _row("Short-term borrowings", CAS_BS, "45955"),
                      _row("Balance at January 1, 2024", CAS_EQ, "1"),
                      _row("TOTAL NON-CURRENT ASSETS", IFRS_BS, "2161907", "2095877"),
                      _row("TOTAL NON-CURRENT LIABILITIES", IFRS_BS, "405811", "433082"),
                      _row("Balance at January 1, 2024", IFRS_EQ, "2")]
    return doc


def test_a_request_sends_only_the_balance_sheet_the_face_reads():
    blocks = face_context.face_rows(_petrochina(), {"balance_sheet"})
    assert [(b["statement"], b["entity"]) for b in blocks] == [("balance_sheet", "consolidated")]
    assert "kept_apart" not in blocks[0]
    assert [r["caption"] for r in blocks[0]["rows"]] == ["TOTAL NON-CURRENT ASSETS",
                                                          "TOTAL NON-CURRENT LIABILITIES"]


def test_the_face_index_holds_no_row_of_the_other_set():
    looked_up = [(st, cap) for st, cap, _r in face_context.face_index(_petrochina())]
    assert ("balance_sheet", "Total non-current assets") not in looked_up
    assert ("balance_sheet", "Short-term borrowings") not in looked_up
    assert ("balance_sheet", "TOTAL NON-CURRENT ASSETS") in looked_up


def test_a_citation_of_a_caption_both_sets_print_resolves_to_the_read_set():
    doc = _petrochina()
    for caption, current, prior in (("TOTAL NON-CURRENT ASSETS", "2161907", "2095877"),
                                    ("TOTAL NON-CURRENT LIABILITIES", "405811", "433082")):
        resolved, unresolved = resolve_sources(
            [SourceRef(statement="balance_sheet", entity="consolidated", caption=caption)],
            [], face_context.face_index(doc))
        assert not unresolved, unresolved
        assert resolved[0]["figures"] == {"current": current, "prior": prior}


def test_a_citation_of_a_row_only_the_other_set_prints_resolves_to_nothing():
    resolved, unresolved = resolve_sources(
        [SourceRef(statement="balance_sheet", entity="consolidated",
                   caption="Short-term borrowings")],
        [], face_context.face_index(_petrochina()))
    assert not resolved and unresolved


def test_a_statement_outside_the_one_set_rule_still_keeps_its_other_set_apart():
    blocks = face_context.face_rows(_petrochina(), {"changes_in_equity"})
    assert [(b["set"], "kept_apart" in b) for b in blocks] == [("IFRS", False), ("CAS", True)]


def test_a_single_set_filing_sends_every_balance_sheet_row():
    doc = _petrochina()
    for page in doc.pages:
        page.evidence.pop("framework", None)
    blocks = face_context.face_rows(doc, {"balance_sheet"})
    assert len(blocks) == 1 and "set" not in blocks[0]
    assert len(blocks[0]["rows"]) == 5
