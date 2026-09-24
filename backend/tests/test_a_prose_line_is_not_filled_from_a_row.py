"""A `route: prose` LINE TAKES ITS FIGURE FROM A SENTENCE, NEVER FROM A TABLE ROW.

NEW FILE -> backend/tests/test_a_prose_line_is_not_filled_from_a_row.py

THE FENCE HAD TWO SIDES AND NEEDED THREE. `line_item_routes.may_read_face` says whether the
STATEMENT may supply a line's figure, `may_read_notes` whether a NOTE may — so a `prose` line is
refused the face and allowed its note, which is correct, because the sentence it wants is IN a note.
Neither predicate asks whether a TABLE ROW may supply it, and that is precisely what `prose` denies.

MEASURED, before `may_read_table_rows` existed. `sub__ga_depreciation` declares `route: prose`, and
an answer citing note 9's row "Depreciation of property, plant and equipment" RESOLVED and took
12,345 off that row. `stages.note_sourced` was the only reader that honoured the route — it opens
`hits = [] if route_of(item) == "prose"` — while the citation path consulted only `may_read_face`,
which is true of a face row and says nothing about a note's.

WHY THE MAPPER WAS NOT ALSO A HOLE, checked rather than assumed: all six shipped prose lines carry
zero `aliases` and zero `keyword_hints`, so `stages.map_ontology` has nothing to bind a caption to.
The citation path was the only way in. `test_a_prose_line_cannot_be_bound_by_a_caption` pins that,
because giving one of them an alias would reopen it.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.services import line_item_llm, line_item_routes
from app.services.line_item_config import load_shipped_set
from app.services.line_item_llm import LineItemAnswer
from app.services.mapping import SourceRef

ROW_CAPTION = "Depreciation of property, plant and equipment"
SENTENCE = ("Depreciation charges of approximately 12,345 are included in "
            "administrative expenses")


@pytest.fixture(scope="module")
def shipped():
    return {i.key: i for i in load_shipped_set().items}


@pytest.fixture(scope="module")
def note():
    """One note carrying BOTH a tabulated row and the sentence — the shape that made this a bug.

    A prose line's note legitimately reaches it as context, so the row is right there beside the
    sentence; nothing about the note tells the two apart. Only the route does.
    """
    table = NotesTable(note_number="9", title="ADMINISTRATIVE EXPENSES", page_index=40,
                       source_text=SENTENCE)
    row = NoteItem(raw_label=ROW_CAPTION, note_number="9")
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("12345"),
                                 provenance=Provenance(page_index=40)))
    table.items.append(row)
    return table


def _resolve(item, sources, note):
    return line_item_llm.resolve(
        LineItemAnswer(key=item.key, role="whole", confidence=0.9, sources=sources),
        [note], None,
        allow_face=line_item_routes.may_read_face(item),
        allow_rows=line_item_routes.may_read_table_rows(item))


def test_the_predicate_answers_for_prose_alone(shipped):
    """`face`, `note_tables`, `anywhere` and silence all tabulate; only `prose` does not."""
    by_route = {}
    for item in shipped.values():
        by_route.setdefault(line_item_routes.declared_route(item), []).append(item)
    assert by_route.get("prose"), "the shipped set must declare prose lines for this to mean anything"
    for route, items in by_route.items():
        expected = route != "prose"
        for item in items:
            assert line_item_routes.may_read_table_rows(item) is expected, (item.key, route)


def test_a_prose_line_refuses_a_row_citation(shipped, note):
    """THE DEFECT. The row is in the very note the line is given, and it must still be refused."""
    item = shipped["sub__ga_depreciation"]
    assert line_item_routes.declared_route(item) == "prose"
    resolved, unresolved, figures = _resolve(item, [SourceRef(note="9", caption=ROW_CAPTION)], note)
    assert not resolved, f"a table row filled a prose line: {figures}"
    assert unresolved and "prose" in unresolved[0]["why"], unresolved
    # NAMED, not merely unresolved: the author has to learn the PLACE was wrong, not the caption.
    assert "not in a table row" in unresolved[0]["why"]


def test_the_prose_route_itself_still_answers(shipped, note):
    """THE CONTROL WITHOUT WHICH THE FIX IS WORTHLESS. Refusing rows must not refuse the sentence —
    the amount verified against the note's own text is how such a line is meant to be answered."""
    item = shipped["sub__ga_depreciation"]
    resolved, unresolved, figures = _resolve(
        item, [SourceRef(note="9", caption="", amount="12345", quote=SENTENCE)], note)
    assert not unresolved, unresolved
    assert figures == {"prose": Decimal("12345")}, figures


def test_a_tabulated_line_still_takes_its_row(shipped, note):
    """THE OTHER CONTROL. The fence must close one route, not narrow every line — a
    `note_tables` line citing the same row is exactly what the citation path is for."""
    item = shipped["sub__rp_find_2"]
    assert line_item_routes.declared_route(item) == "note_tables"
    resolved, _unresolved, figures = _resolve(item, [SourceRef(note="9", caption=ROW_CAPTION)], note)
    assert resolved and figures == {"current": Decimal("12345")}, figures


def test_a_prose_line_cannot_be_bound_by_a_caption(shipped):
    """THE SECOND ROUTE IN, closed by the configuration rather than by code.

    `stages.map_ontology` binds a printed caption to a concept through `aliases` and
    `keyword_hints`, and it consults `may_read_face` only — so a prose line carrying either could
    be claimed by a NOTE row's caption there, which no predicate would refuse. None of the six
    does, which is why the citation path was the only hole; this fails the moment one gains them,
    so the gap is found before it is reached.
    """
    offenders = []
    for item in shipped.values():
        if line_item_routes.declared_route(item) != "prose":
            continue
        aliases = list(item.aliases or ())
        for values in (item.aliases_i18n or {}).values():
            aliases += list(values or ())
        if aliases or list(getattr(item, "keyword_hints", None) or ()):
            offenders.append(item.key)
    assert not offenders, (
        f"these prose lines are bindable by caption in map_ontology, which consults only "
        f"may_read_face: {offenders}")
