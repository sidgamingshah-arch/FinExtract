"""A note that prints no basis of its own is read on the one basis the face statements print.

THE DEFECT. The row builder reads a note with no Group|Company columns as the GROUP's
(`row_reconstruct._basis_of_columns` / `_basis_for` default to CONSOLIDATED; notes are rebuilt with
`on_face=False`), and only a mainland 母公司 chapter re-keyed a note. On the four Indian test filings
the statements are all the company's, so every note figure, sub-line part and derived parent landed
on a Consolidated tab holding nothing else — f4 showed LTP 412/368, CP 96/84, contingent liabilities
1,120/980 and a "Total assets" of 508/452 there, while the Standalone tab said "not extracted".

THE RULE, applied upstream in `notes_extract` so every reader of a note figure's own basis moves
together: when `services.face_bases` says the face prints only STANDALONE, a note table whose every
value is the row builder's no-band default takes standalone. A Group|Company band, a company-only
chapter and a basis carried from the previous page all win; a filing whose face prints the group's
statements, or whose face could not be read, is left exactly as it was.
"""
from __future__ import annotations

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, DocFormat
from app.core.models.geometry import BBox
from app.core.stage import PipelineContext
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word


def _w(text: str, x0: float, y0: float, width: float = 0.06) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + width, y1=y0 + 0.010))


def _words(*lines):
    return [_w(tok, 0.06 + j * 0.2, 0.10 + i * 0.02)
            for i, line in enumerate(lines) for j, tok in enumerate(line)]


_NOTE = (["3.", "Other financial assets"], ["Security deposits", "300", "270"],
         ["Total", "412", "368"])


def _bases(table) -> set[str]:
    return {ev.basis.value for row in table.items for ev in (row.values or {}).values()}


def _table(tables, number="3"):
    return next(t for t in tables if t.note_number == number and t.items)


# ── the rule, at the note reader ──────────────────────────────────────────────────────────────

def test_a_note_with_no_basis_takes_the_sole_basis_the_face_prints():
    tables = extract_note_tables(_words(*_NOTE), page_index=5, document_id=None,
                                 source_kind="native", default_basis=Basis.STANDALONE)
    table = _table(tables)

    assert table.basis == Basis.STANDALONE
    assert _bases(table) == {"standalone"}


def test_without_a_default_a_note_is_read_as_before():
    table = _table(extract_note_tables(_words(*_NOTE), page_index=5, document_id=None,
                                       source_kind="native"))

    assert table.basis is None
    assert _bases(table) == {"consolidated"}


def test_a_carried_basis_outranks_the_default():
    tables = extract_note_tables(_words(["Bank loans", "500", "400"]), page_index=5,
                                 document_id=None, source_kind="native",
                                 carry_note=("5", "Borrowings", Basis.CONSOLIDATED),
                                 default_basis=Basis.STANDALONE)
    carried = _table(tables, "5")

    assert carried.basis == Basis.CONSOLIDATED and _bases(carried) == {"consolidated"}


def test_a_carried_note_with_no_basis_takes_the_default():
    tables = extract_note_tables(_words(["Bank loans", "500", "400"]), page_index=5,
                                 document_id=None, source_kind="native",
                                 carry_note=("5", "Borrowings", None),
                                 default_basis=Basis.STANDALONE)
    carried = _table(tables, "5")

    assert carried.basis == Basis.STANDALONE and _bases(carried) == {"standalone"}


def test_a_note_that_prints_group_and_company_columns_keeps_both():
    """The HKEX house style: "Group" over one dated pair of columns, "Company" over the other."""
    def w(text, x, y, width=0.06):
        return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + width, y1=y + 0.012))

    words = [w("12.", 0.06, 0.03, 0.03), w("Trade", 0.10, 0.03), w("receivables", 0.17, 0.03, 0.1),
             w("Group", 0.58, 0.055), w("Company", 0.80, 0.055),
             w("2025", 0.55, 0.08), w("2024", 0.67, 0.08), w("2025", 0.79, 0.08),
             w("2024", 0.91, 0.08)]
    for i, (label, cells) in enumerate((("Receivables", ("3,410", "2,900", "341", "290")),
                                        ("Allowance", ("(10)", "(8)", "(1)", "(1)")))):
        y = 0.12 + i * 0.04
        words.append(w(label, 0.10, y, 0.10))
        words += [w(v, x, y) for v, x in zip(cells, (0.55, 0.67, 0.79, 0.91))]
    table = _table(extract_note_tables(words, page_index=5, document_id=None,
                                       source_kind="native", default_basis=Basis.STANDALONE),
                   "12")

    assert table.basis is None
    assert _bases(table) == {"consolidated", "standalone"}


# ── through the pipeline's first three stages ─────────────────────────────────────────────────

def _run(data: bytes, included_pages=None) -> tuple[DocumentModel, list[str]]:
    from app.stages.classify import ClassifyStage
    from app.stages.extract import ExtractStage
    from app.stages.ingest import IngestStage

    doc = DocumentModel(filename="filing.pdf", fmt=DocFormat.PDF)
    ctx = PipelineContext(raw_bytes=data)
    ctx.included_pages = included_pages
    for stage in (IngestStage(), ClassifyStage(), ExtractStage()):
        doc = stage.run(doc, ctx)
    return doc, ctx.logs


def _note_values(doc) -> set[str]:
    return {ev.basis.value for t in doc.notes for it in t.items for ev in it.values.values()}


@pytest.fixture(autouse=True)
def _renderers():
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")


def test_a_standalone_only_filings_notes_are_the_companys():
    from tests.fixtures.generate import make_indian_standalone_with_notes_pdf

    doc, logs = _run(make_indian_standalone_with_notes_pdf())

    assert doc.notes and all(t.basis == Basis.STANDALONE for t in doc.notes), [
        (t.note_number, t.basis) for t in doc.notes]
    assert _note_values(doc) == {"standalone"}
    assert any(line.startswith("extract:face_bases=standalone") for line in logs)
    assert any(line.startswith("extract:notes_on_face_basis=") for line in logs)


def test_a_group_and_parent_filing_is_untouched():
    """A mainland filing: the group's cash-flow statement, its supplement in the notes. The
    supplement in the parent company's chapter is the company's; in the group's, the group's."""
    from tests.fixtures.generate import make_cas_cash_flow_supplement_in_notes_pdf

    doc, logs = _run(make_cas_cash_flow_supplement_in_notes_pdf(
        chapter="十九、母公司财务报表主要项目注释"))
    assert "extract:face_bases=consolidated,standalone" in logs
    assert doc.notes and all(t.basis == Basis.STANDALONE for t in doc.notes if t.items)
    assert not any(line.startswith("extract:notes_on_face_basis=") for line in logs)

    doc, logs = _run(make_cas_cash_flow_supplement_in_notes_pdf())
    assert "extract:face_bases=consolidated" in logs
    assert all(t.basis is None for t in doc.notes)
    assert _note_values(doc) == {"consolidated"}


def test_an_hkex_company_statement_after_the_notes_leaves_the_notes_the_groups():
    from tests.fixtures.generate import make_company_statement_after_notes_pdf

    doc, logs = _run(make_company_statement_after_notes_pdf())

    assert "extract:face_bases=consolidated,standalone" in logs
    assert doc.notes and all(t.basis is None for t in doc.notes)


def test_an_unread_face_leaves_the_notes_as_they_were():
    from tests.fixtures.generate import make_multipage_pdf

    doc, logs = _run(make_multipage_pdf())

    assert any(line.startswith("extract:face_bases=unknown(") for line in logs)
    assert doc.notes and all(t.basis is None for t in doc.notes)


def test_the_page_scope_screen_does_not_change_whose_the_notes_are():
    """Hiding the group's statement narrows what is READ, not whose the notes are: the classifier's
    verdict on every page still decides."""
    from tests.fixtures.generate import make_company_statement_after_notes_pdf

    doc, logs = _run(make_company_statement_after_notes_pdf(), included_pages={1, 2})

    assert "extract:page_scope_applied=[1, 2]" in logs
    assert "extract:face_bases=consolidated,standalone" in logs
    assert doc.notes and all(t.basis is None for t in doc.notes)
