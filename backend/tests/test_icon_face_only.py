"""ICON is read from the statement faces only: no ICON line can take a figure out of a note.

Every route that can write a figure from a note is checked against the shipped ICON set:

  * the note-sourced stage (rows and the prose fallback) reads only `_declared_items`, which drops
    every declared `face` line;
  * the model route gives a `face` line no notes (`line_item_notes.note_sets`) and refuses a
    citation that names one (`line_item_routes.may_read_notes`, consulted by `stages.line_item_llm`);
  * the dynamic split in `stages.map_ontology`, which files a face row's note components onto the
    section's lines, gives them to no line of a set that declares `strict_face_route`.

The split fence is a set's declaration, and the HK set does not make it: its face template lines
still take split components (`test_a_deduction_is_one_row_once::test_a_template_face_line_still_splits`),
which the last test here restates so the two cannot drift.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import load_line_item_set
from app.services import line_item_notes, line_item_routes
from app.services.line_item_requests import asked_about
from app.stages.map_ontology import route_fenced_decompositions
from app.stages.note_sourced import _declared_items

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
ICON = TEMPLATES / "output_csv_icon_line_items.json"
HK = TEMPLATES / "output_csv_hk_line_items.json"


def _load(path):
    return load_line_item_set(json.loads(path.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def icon():
    return _load(ICON)


def _reads_a_figure(item) -> bool:
    """An extracted line, or a calculated line a filing may print (`extract_or_derive`)."""
    return str(item.type) == "extracted" or str(item.extraction_mode) == "extract_or_derive"


def test_every_icon_line_that_reads_a_figure_declares_face_and_no_note_source(icon):
    readers = [i for i in icon.items if _reads_a_figure(i)]
    assert len([i for i in readers if str(i.type) == "extracted"]) == 131
    assert not [i.key for i in readers if line_item_routes.declared_route(i) != "face"]
    assert not [i.key for i in icon.items if i.note_source is not None]
    assert icon.strict_face_route is True


def test_no_icon_extracted_line_can_take_a_note_figure(icon):
    extracted = [i for i in icon.items if str(i.type) == "extracted"]
    # The note readers: the note-sourced stage's rows and prose, and the model's note citations.
    assert not [i.key for i in extracted if line_item_routes.may_read_notes(i)]
    assert not {i.key for i in _declared_items(icon)} & {i.key for i in extracted}
    # The split: a component of a face row's cited note lands on no ICON line.
    assert not [i.key for i in extracted
                if line_item_routes.may_take_note_split(i, icon.strict_face_route)]


def test_an_icon_request_is_given_no_notes(icon):
    """Notes whose headings are worded like ICON's own lines — the accidental match."""
    def note(number, title, caption):
        table = NotesTable(note_number=number, title=title, page_index=9)
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.STANDALONE, period_label="current",
                                     value=Decimal("100"), provenance=Provenance(page_index=9)))
        table.items.append(row)
        return table

    notes = [note("24", "Revenue from operations", "Sale of products - Export"),
             note("28", "Other expenses", "Power and fuel"),
             note("36", "Contingent liabilities and commitments", "Claims not acknowledged as debts")]
    asked = [i for i in icon.items if asked_about(i)]
    assert asked
    assert line_item_notes.note_sets(asked, notes) == {}


def test_a_strict_set_declines_a_split_onto_face_lines(icon):
    by_key = {i.key: i for i in icon.items}
    decl = ("bs_icon__cash_and_bank_balances",
            ["bs_icon__cash_balance", "bs_icon__bank_balance"], "bs_ca")

    kept, declined = route_fenced_decompositions([decl], by_key, strict_face=True)
    assert kept == [] and declined == ["bs_icon__cash_and_bank_balances"]

    # The same declaration on a set that does not say it is strict splits as it always did.
    assert route_fenced_decompositions([decl], by_key) == ([decl], [])


def test_the_hk_set_is_not_strict_and_its_face_lines_still_split():
    hk = _load(HK)
    assert hk.strict_face_route is False
    by_key = {i.key: i for i in hk.items}
    decl = ("bs_ca__other_current_assets", ["bs_ca__prepayments_cp"], "bs_ca")
    assert route_fenced_decompositions([decl], by_key, hk.strict_face_route) == ([decl], [])
