"""Which bases a document's face statements print — `services.face_bases`.

The answer is read off the classifier's page verdicts (`PageSource.scope`, the two halves of a page
cut at a second title, and a person's override part by part), never off the face values: a note
page the classifier took for a statement carries the row builder's consolidated default on a
filing that prints only the company's statements (f1 page 12, f5 page 8 of the Indian test
filings). A statement page ahead of the notes whose entity was not read leaves the answer unknown,
so a mainland 合并 page that failed to resolve can never let the 母公司 pages decide alone.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind, PrintedIn
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services.face_bases import REFUSED_FLAG, face_bases, printed_bases, refuse_unprinted


def _face(index: int, scope: str | None, statement: str = "balance_sheet",
          **evidence) -> PageSource:
    return PageSource(index=index, kind=PageKind.FACE, statement=statement, scope=scope,
                      evidence=dict(evidence))


def _notes(index: int) -> PageSource:
    return PageSource(index=index, kind=PageKind.NOTES)


def test_a_filing_whose_statements_are_the_companys_prints_one_basis():
    fb = face_bases([_face(0, "company"), _face(1, "company", "profit_and_loss"), _notes(2)])

    assert fb.known and fb.bases == {"standalone"} and fb.sole == "standalone"
    assert not fb.prints("consolidated") and fb.prints(Basis.STANDALONE)
    assert fb.describe() == "standalone"


def test_group_and_parent_statements_print_both_bases():
    """A mainland filing: the group's statements (合并), then the parent company's (母公司)."""
    pages = [_face(0, "consolidated"), _face(1, "consolidated", "profit_and_loss"),
             _face(2, "company"), _face(3, "company", "profit_and_loss"), _notes(4)]
    fb = face_bases(pages)

    assert fb.known and fb.sole is None
    assert fb.prints("consolidated") and fb.prints("standalone")
    assert fb.describe() == "consolidated,standalone"


def test_an_unread_statement_ahead_of_the_notes_leaves_the_answer_unknown():
    """A 合并 page that failed to resolve never lets the 母公司 pages decide the notes alone."""
    fb = face_bases([_face(0, None), _face(1, "company"), _notes(2)])

    assert not fb.known and fb.sole is None
    assert fb.prints("consolidated") and fb.prints("standalone"), "unknown refuses nothing"
    assert fb.describe().startswith("unknown(page=0: a statement page whose entity was not read")


def test_an_unread_face_among_the_notes_is_not_counted():
    """f1 page 12 / f5 page 8: a note page read as a P&L face, with no entity read."""
    fb = face_bases([_face(0, "company"), _notes(1), _face(2, None, "profit_and_loss"),
                     _notes(3)])

    assert fb.known and fb.bases == {"standalone"}
    assert fb.ignored_pages == (2,)
    assert "not counted" in fb.describe() and fb.describe().startswith("standalone")


def test_a_group_and_company_column_page_prints_both():
    assert face_bases([_face(0, "mixed")]).bases == {"consolidated", "standalone"}


def test_a_page_cut_at_a_second_title_counts_both_statements():
    before = face_bases([_face(0, "company", "balance_sheet",
                               statement_before_title="cash_flow",
                               scope_before_title="consolidated"), _notes(1)])
    assert before.bases == {"consolidated", "standalone"}

    closing = face_bases([_face(0, "consolidated", "cash_flow", closing_title="母公司现金流量表",
                                scope_after_closing_title="company"), _notes(1)])
    assert closing.bases == {"consolidated", "standalone"}

    unread = face_bases([_face(0, "company", "balance_sheet", statement_before_title="cash_flow",
                               scope_before_title=None), _notes(1)])
    assert not unread.known


def test_a_persons_override_is_read_part_by_part():
    def overridden(*parts) -> PageSource:
        page = _face(0, parts[-1].get("entity"))
        page.evidence = {"override_parts": [
            {"from_y": round(0.5 * n, 2), "statement": "cash_flow", **part}
            for n, part in enumerate(parts)]}
        return page

    both = face_bases([overridden({"kind": "face", "entity": "consolidated"},
                                  {"kind": "face", "entity": "company"}), _notes(1)])
    assert both.bases == {"consolidated", "standalone"}

    unread = face_bases([overridden({"kind": "face", "entity": None}), _notes(1)])
    assert not unread.known

    others = face_bases([overridden({"kind": "notes", "entity": None},
                                    {"kind": "other", "entity": None},
                                    {"kind": "face", "entity": "company"}), _notes(1)])
    assert others.known and others.bases == {"standalone"}


def test_no_statement_page_is_unknown():
    assert not face_bases([]).known
    assert not face_bases([_notes(0)]).known
    assert not face_bases([PageSource(index=0, kind=PageKind.FACE, statement=None)]).known


def _face_row(page: int, basis: Basis) -> LineItem:
    row = LineItem(source_label="Cash", canonical_key="bs_ca__cash", printed_in=PrintedIn.FACE)
    row.set_value(ExtractedValue(basis=basis, period_label="current", value=Decimal("5"),
                                 value_raw=Decimal("5"), provenance=Provenance(page_index=page)))
    return row


def test_printed_bases_add_what_a_counted_face_row_carries():
    """A page read as one entity can still print a second entity's column. Only the pages that
    decided the answer are read: a misread note page's consolidated default is not a statement."""
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [_face(0, "company")]
    doc.line_items = [_face_row(0, Basis.CONSOLIDATED)]
    assert printed_bases(doc).bases == {"consolidated", "standalone"}

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [_face(0, "company"), _notes(1), _face(2, None, "profit_and_loss"), _notes(3)]
    doc.line_items = [_face_row(2, Basis.CONSOLIDATED)]
    assert printed_bases(doc).bases == {"standalone"}


def test_refuse_unprinted_flags_and_logs_once():
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [_face(0, "company"), _notes(1)]
    row = LineItem(source_label="x", canonical_key="k")
    logs: list[str] = []

    for _ in range(2):
        assert refuse_unprinted(doc, row, "k", "consolidated", "current", Decimal("5"),
                                by="note_sourced", log=logs.append)
    assert row.confidence.flags.count(f"{REFUSED_FLAG}:consolidated:current") == 1
    assert any(line.startswith("note_sourced:k[consolidated:current] REFUSED 5") for line in logs)

    assert not refuse_unprinted(doc, row, "k", "standalone", "current", Decimal("5"),
                                by="note_sourced", log=logs.append)
    assert not refuse_unprinted(DocumentModel(filename="g.pdf"), row, "k", "consolidated",
                                "current", Decimal("5"), by="note_sourced")
