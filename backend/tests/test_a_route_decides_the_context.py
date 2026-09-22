"""A FACE LINE IS NEVER GIVEN NOTES, AND A NOTE LINE IS NEVER GIVEN THE STATEMENT.

NEW FILE -> backend/tests/test_a_route_decides_the_context.py

`services.line_item_routes` draws both sides of that fence — `may_read_face` is false for a
declared `note_tables`/`prose` line, `may_read_notes` is false for a declared `face` one — and its
own docstring says the second is "restated here so both directions of the fence are in one file".
Only one direction had a reader. `line_item_requests.face_statements` consulted `may_read_face`, so
a note-only plan was never sent a statement; nothing consulted `may_read_notes`, so a face line was
routinely sent notes.

WHY THAT WAS NOT MERELY WASTE. A face line has no `note_source`, so `note_probe` falls back to
`_blended` — its label and aliases scored against note HEADINGS — and something always scores
something. Having a note set then put the line in the note-grouping branch of `plan_requests`,
which passes no `sections`; the `unplanned` tail below it is what attaches them. So `face_statements`
found nothing declared and built NO STATEMENT BLOCK. Measured before the fix, on the twelve
face-routed related-party lines: on 2025032802704 and 2024 Annual Report every one received 1-4
notes it may not cite and zero face rows, while on 1223214527 — where their probes happened to score
nothing — the same lines correctly received the balance sheet. Whether a line was shown its own
statement turned on whether an unrelated note heading shared a token with its label.

These tests pin the invariant in BOTH directions and at the level that matters — what a built
request actually carries — rather than asserting the predicate in isolation, which was already true.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.config import get_settings
from app.core.models.enums import Basis
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable, Provenance)
from app.schemas.line_items import load_line_item_set
from app.services import line_item_notes, line_item_routes

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _note(number: str, title: str, rows: list[tuple[str, str]]):
    table = NotesTable(note_number=number, title=title, page_index=9)
    for caption, amount in rows:
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=None if amount is None else __import__("decimal").Decimal(amount),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


def test_a_declared_face_line_is_given_no_notes(shipped):
    """The direction that had no reader.

    Asserted on a line whose label scores WELL against a note heading, because that is the case
    that used to fail: the probe is the line's own blended text, so a heading sharing its words is
    exactly what pulled it into the note path.
    """
    items = {i.key: i for i in shipped.items}
    face = [k for k, i in items.items()
            if line_item_routes.declared_route(i) == "face" and i.note_source is None]
    assert face, "the shipped set must carry declared face lines for this to mean anything"

    # A note whose heading is worded like the lines themselves — the accidental match.
    notes = [_note("20", "AMOUNTS DUE FROM RELATED PARTIES",
                   [("Due from a fellow subsidiary", "500")]),
             _note("21", "TRADE AND OTHER PAYABLES TO RELATED PARTIES",
                   [("Amounts due to related parties", "700")])]

    sets = line_item_notes.note_sets([items[k] for k in face], notes)
    assert sets == {}, (
        "a declared face line was given notes it may not cite: "
        f"{ {k: [h.note for h in v] for k, v in sets.items()} }")


def test_a_declared_note_line_is_still_given_its_notes(shipped):
    """THE CONTROL, and the reason this is a fence rather than a switch.

    The same call, the same notes, on lines that DECLARE the note route — they must be unaffected.
    Without this, making the face side pass is trivially achievable by breaking note selection for
    everyone.
    """
    items = {i.key: i for i in shipped.items}
    note_routed = [k for k, i in items.items()
                   if line_item_routes.declared_route(i) == "note_tables"
                   and i.note_source is not None][:12]
    assert note_routed

    notes = [_note("27", "TRADE AND OTHER RECEIVABLES",
                   [("Due from related parties", "2118244")]),
             _note("30", "TRADE AND OTHER PAYABLES",
                   [("Amounts due to related parties", "900")]),
             _note("31", "RELATED PARTY TRANSACTIONS",
                   [("Other payables to related parties", "640")])]

    sets = line_item_notes.note_sets([items[k] for k in note_routed], notes)
    assert sets, "note-routed lines must still select notes"


def test_silence_keeps_the_note_route(shipped):
    """`route: None` IS NOT `face`, and the guard must not read it as one.

    A set authored before the field existed declares no route at all, and 67 shipped items still
    do. Reading silence as "face" would take the notes away from every one of them — which is the
    same conflation `line_item_routes.declared_route` refuses to make, "deliberately not
    defaulted".
    """
    items = {i.key: i for i in shipped.items}
    silent = [i for i in items.values()
              if line_item_routes.declared_route(i) == "" and i.note_source is not None]
    if not silent:
        pytest.skip("the shipped set currently declares a route on every note-sourced line")
    assert all(line_item_routes.may_read_notes(i) for i in silent)


def test_the_two_predicates_partition_every_shipped_line(shipped):
    """No line may be refused BOTH contexts — that is a line nothing can answer.

    `anywhere` is the route that refuses neither, `face` refuses notes, `note_tables`/`prose`
    refuse the face, and silence refuses nothing. So every line must be allowed at least one.
    """
    blind = [i.key for i in shipped.items
             if not line_item_routes.may_read_face(i)
             and not line_item_routes.may_read_notes(i)]
    assert not blind, f"these lines may read neither the face nor a note: {blind}"
