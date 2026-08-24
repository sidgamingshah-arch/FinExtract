"""Only notes linked to a face-statement item are published.

A filing's notes section is mostly not about the published figures — accounting policies,
segment commentary, governance tables. The requirement is that an unreferenced note is not
delivered at all, so this asserts the keep/drop rule and, just as importantly, that nothing is
dropped for the wrong reason (a note cited only from inside another note must not survive; a
filing we could not classify must not lose everything).
"""
from __future__ import annotations

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis
from app.core.models.line_item import (
    ExtractedValue,
    FaceNoteLink,
    LineItem,
    NoteRef,
    NotesTable,
    Provenance,
)
from app.core.stage import PipelineContext
from app.stages.prune_notes import PruneNotesStage


def _doc(face_pages=(0,), note_pages=(1,)) -> DocumentModel:
    doc = DocumentModel(filename="f.pdf")
    for i in face_pages:
        doc.pages.append(PageSource(index=i, kind=PageKind.FACE))
    for i in note_pages:
        doc.pages.append(PageSource(index=i, kind=PageKind.NOTES))
    return doc


def _face_item(label: str, page: int = 0, note: str | None = None,
               refs: list[str] | None = None) -> LineItem:
    li = LineItem(source_label=label)
    li.values["k"] = ExtractedValue(
        period_label="2023", basis=Basis.CONSOLIDATED, value=None,
        provenance=Provenance(source_kind="pdf", page_index=page),
    )
    if note:
        li.note_number = note
    if refs:
        li.note_refs = [NoteRef(raw=",".join(refs), numbers=refs)]
    return li


def _note(number: str) -> NotesTable:
    return NotesTable(note_number=number, title=f"Note {number}", source_pages=[1])


def _run(doc: DocumentModel) -> PipelineContext:
    ctx = PipelineContext()
    PruneNotesStage().run(doc, ctx)
    return ctx


def test_a_referenced_note_is_kept_and_an_unreferenced_one_is_dropped():
    doc = _doc()
    doc.line_items = [_face_item("Trade receivables", note="15")]
    doc.notes = [_note("15"), _note("2")]      # 2 = accounting policies, never cited
    _run(doc)
    assert [n.note_number for n in doc.notes] == ["15"]


def test_a_note_referenced_via_note_refs_is_kept():
    """Ranges/lists are parsed into note_refs rather than the single note column."""
    doc = _doc()
    doc.line_items = [_face_item("Borrowings", refs=["30", "31"])]
    doc.notes = [_note("30"), _note("31"), _note("32")]
    _run(doc)
    assert sorted(n.note_number for n in doc.notes) == ["30", "31"]


def test_a_subref_keeps_its_parent_note():
    """The shape the SCHEMA intends for a sub-reference."""
    doc = _doc()
    li = _face_item("Segment revenue")
    li.note_refs = [NoteRef(raw="6(a)", numbers=[], subrefs=["6(a)"])]
    doc.line_items = [li]
    doc.notes = [_note("6"), _note("7")]
    _run(doc)
    assert [n.note_number for n in doc.notes] == ["6"]


def test_a_subref_as_the_READERS_write_it_keeps_its_parent_note():
    """The shape that actually reaches this stage, and the one the test above does not cover:
    ``row_reconstruct`` and ``excel_extract`` put the whole printed token in ``numbers``, so
    ``subrefs`` is always empty and a rule reading only it never fires. The note was deleted, and
    the linkage and the section view then had nothing to show for a figure the filing explains."""
    doc = _doc()
    li = _face_item("Trade receivables")
    li.note_refs = [NoteRef(raw="16(b)", numbers=["16(b)"])]
    li.note_number = "16(b)"
    doc.line_items = [li]
    doc.notes = [_note("16"), _note("17")]
    _run(doc)
    assert [n.note_number for n in doc.notes] == ["16"]


def test_an_explicit_face_note_link_keeps_the_note():
    """The link built by the link stage is the authoritative signal."""
    doc = _doc()
    li = _face_item("Investment properties")
    doc.line_items = [li]
    note = _note("15")
    doc.notes = [note, _note("2")]
    doc.links = [FaceNoteLink(face_item_id=li.id, notes_table_id=note.id, note_number="15")]
    _run(doc)
    assert [n.note_number for n in doc.notes] == ["15"]


def test_a_reference_from_inside_another_note_does_not_keep_it():
    """A note-to-note cross reference is not a face reference — that is the whole point."""
    doc = _doc(face_pages=(0,), note_pages=(1,))
    note_internal = _face_item("Sub-line inside a note", page=1, note="12")
    doc.line_items = [_face_item("Trade receivables", note="15"), note_internal]
    doc.notes = [_note("15"), _note("12")]
    _run(doc)
    assert [n.note_number for n in doc.notes] == ["15"]


def test_a_link_whose_face_item_is_not_on_a_face_page_is_ignored():
    doc = _doc(face_pages=(0,), note_pages=(1,))
    inside = _face_item("Sub-line inside a note", page=1)
    doc.line_items = [inside]
    note = _note("12")
    doc.notes = [note]
    doc.links = [FaceNoteLink(face_item_id=inside.id, notes_table_id=note.id, note_number="12")]
    _run(doc)
    assert doc.notes == []


def test_no_face_reference_at_all_publishes_nothing_and_says_so():
    """Publishing every note would contradict the requirement; doing it silently would hide a
    note-column detection failure, so the run records it."""
    doc = _doc()
    doc.line_items = [_face_item("Trade receivables")]
    doc.notes = [_note("15"), _note("2")]
    ctx = _run(doc)
    assert doc.notes == []
    assert any("no_face_references" in line for line in ctx.logs)


def test_an_unclassifiable_filing_does_not_lose_every_note():
    """With no page classified as a face, every item counts — failing closed would deliver
    nothing for a filing we merely could not classify."""
    doc = DocumentModel(filename="f.pdf")          # no pages classified
    doc.line_items = [_face_item("Trade receivables", note="15")]
    doc.notes = [_note("15"), _note("2")]
    _run(doc)
    assert [n.note_number for n in doc.notes] == ["15"]


def test_the_drop_is_logged_so_a_missing_note_is_explainable():
    doc = _doc()
    doc.line_items = [_face_item("Trade receivables", note="15")]
    doc.notes = [_note("15"), _note("2"), _note("3")]
    ctx = _run(doc)
    line = next(line for line in ctx.logs if line.startswith("prune_notes:"))
    assert "kept=1" in line and "dropped=2" in line
    assert "'2'" in line and "'3'" in line


def test_a_document_with_no_notes_is_untouched():
    doc = _doc()
    doc.line_items = [_face_item("Trade receivables", note="15")]
    _run(doc)
    assert doc.notes == []
