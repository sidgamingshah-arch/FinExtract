"""`anywhere` MEANT NOTHING, AND NOW MEANS ONE THING — the gate it lifts.

NEW FILE -> backend/tests/test_anywhere_route.py

WHAT IT WAS. `anywhere` was accepted by the schema (`schemas.line_items.Route`), offered by the
config screen, described there as "looked for on the face AND in the note rows AND in prose —
nothing is ruled out", and then read by exactly ONE line of code: `note_sourced.route_of`, which
passed it through to a stage that never branched on it. So:

  * with a `note_source` it behaved as `note_tables` — rows, then prose;
  * without one the note stage never saw the line at all (`_declared_items` keeps only lines
    carrying a `note_source`), so it behaved as `face`.

It was decorative in every configuration, and zero of the shipped 527 lines declare it.

WHAT IT CANNOT DO, so that the label stops promising it. It cannot make the face be searched,
because the face already is: `stages.map_ontology` never reads `route`, so every line is
caption-matchable whatever the field says — which also means no route can stop a caption being
claimed. And it cannot reach a page outside the statements and the notes, because nothing
reconstructs those pages at all (`services.pdf_extract`'s target set is NOTES pages plus FACE pages
with a resolved statement).

WHAT IT NOW DOES. `note_sections.open_to` narrows which notes a line may read: closed only when a
note resolves to exactly one section and the line names a different one. A line whose author wrote
"do not constrain it" should not then have its note search closed against another section's note,
so `anywhere` is open to every note. That is the whole of the route's behaviour, and these tests
are what stop it going back to meaning nothing.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.note_sections import open_to

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


class _Line:
    """Only the two fields `open_to` reads."""

    def __init__(self, *, route: str = "", section_scope=()):
        self.route = route
        self.section_scope = list(section_scope)


# A note the links resolved to exactly one section — the only case the narrowing closes on.
ONE_SECTION = {"7": {"bs_ca"}}


def test_a_scoped_line_is_still_closed_against_another_sections_note():
    """THE NARROWING ITSELF, asserted first: without it the test below proves nothing, because
    every line would be open and `anywhere` would be indistinguishable again."""
    assert not open_to(_Line(section_scope=["bs_nca"]), "7", ONE_SECTION)


def test_anywhere_is_open_to_another_sections_note():
    """THE ROUTE'S ONE BEHAVIOUR. Same line, same note, same map — only the route differs."""
    assert open_to(_Line(route="anywhere", section_scope=["bs_nca"]), "7", ONE_SECTION)


@pytest.mark.parametrize("route", ["", "face", "note_tables", "prose"])
def test_no_other_route_lifts_the_narrowing(route):
    """The exemption is `anywhere`'s alone. A `note_tables` line that wanted it would have said so,
    and widening the others would change 60 shipped lines' note candidates in silence."""
    assert not open_to(_Line(route=route, section_scope=["bs_nca"]), "7", ONE_SECTION)


def test_anywhere_changes_nothing_where_the_narrowing_never_applied():
    """The four ways a line was already open stay open — the route adds an arm, it does not
    replace the rule."""
    unscoped = _Line(route="anywhere")
    assert open_to(unscoped, "7", ONE_SECTION), "an unscoped line was always open"
    scoped = _Line(route="anywhere", section_scope=["bs_ca"])
    assert open_to(scoped, "7", ONE_SECTION), "a note in the line's own section was always open"
    assert open_to(scoped, "99", ONE_SECTION), "an unresolved note was always open"
    assert open_to(scoped, "7", {"7": {"bs_ca", "bs_nca"}}), "a multi-section note was always open"


def test_the_shipped_set_declares_no_anywhere_line():
    """RECORDED, NOT REQUIRED. The count is the reason this change is safe to make at all: no
    shipped line's behaviour moves. If a future set declares one, this test is the place that says
    the blast radius has changed — update it deliberately rather than discovering it in a run.
    """
    s = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    declaring = [i.key for i in s.items if str(getattr(i, "route", "") or "") == "anywhere"]
    assert declaring == [], f"the set now declares `anywhere`: {declaring}"
