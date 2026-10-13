"""A face row no line claimed still says which section its note belongs to.

`services.note_sections` resolves a note's section from the face rows that cite it — through the
row's concept. A row the mapper left unclassified has no concept, so its note resolved to nothing
and stayed open to every line. Measured on an Ind AS test filing: "(v) Other financial assets"
under current assets cites note 14 and maps to no line; the NON-CURRENT "other financial assets"
part read note 14 with note 6, 412 + 96 = 508 against the 412 the face prints.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import (ExtractedValue, FaceNoteLink, LineItem, NoteRef,
                                       NotesTable)
from app.services.note_sections import note_sections, open_to


def _face(key: str, note: str, hint: str = "") -> LineItem:
    li = LineItem(source_label=key, canonical_key=key, section_hint=hint or None)
    li.set_value(ExtractedValue(value=Decimal(96), basis=Basis.STANDALONE, period_label="current"))
    li.note_refs = [NoteRef(raw=note, numbers=[note])]
    return li


SET = SimpleNamespace(items=[
    SimpleNamespace(key="bs_ca__other_current_assets", section_scope=["bs_ca"], route="face"),
    SimpleNamespace(key="bs_nca__other_non_current_assets", section_scope=["bs_nca"], route="face"),
    SimpleNamespace(key="sub__ltp_other_fincl_assets_note_total", section_scope=["bs_nca"],
                    route="note_tables"),
])


def _doc(*rows: LineItem) -> DocumentModel:
    doc = DocumentModel(filename="f4.pdf")
    doc.line_items = list(rows)
    for row in rows:
        for number in row.cited_notes():
            table = NotesTable(note_number=number, title=f"note {number}")
            doc.notes.append(table)
            doc.links.append(FaceNoteLink(face_item_id=row.id, notes_table_id=table.id,
                                          note_number=number))
    return doc


def test_an_unclassified_current_row_places_its_note_in_current_assets():
    row = _face("engine_unclassified_face__balance_sheet__current_assets__v_other_financial_assets",
                "14")
    sections = note_sections(_doc(row), SET)
    assert sections == {"14": {"bs_ca"}}
    part = SET.items[2]
    assert not open_to(part, "14", sections)


def test_the_banner_it_was_printed_under_counts_too():
    row = _face("engine_unclassified_face__x", "14", hint="Current assets")
    assert note_sections(_doc(row), SET) == {"14": {"bs_ca"}}


def test_a_row_that_names_no_section_leaves_its_note_open():
    row = _face("engine_unclassified_face__x", "14", hint="(b) Financial assets")
    assert note_sections(_doc(row), SET) == {}
