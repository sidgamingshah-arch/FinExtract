"""THE LINE ITEMS FORM HAS ONE INVENTORY OF ITSELF, NOT THREE.

NEW FILE -> backend/tests/test_form_field_lists.py

`screens/LineItems.tsx` renders every control through one filter, `fld(name, render)`, and keeps
three lists of names beside it:

  * ``GROUP_FIELDS`` — membership per banner. A banner has to say how many controls are inside it
    BEFORE the controls render (React builds the heading first), so the count comes from walking
    this list through the same filter. A name listed here with no control inflates that count.
  * ``CONDITIONAL_FIELDS`` — the names ``withheldReason`` may speak about, and the set the form
    header counts as "withheld". A name here with no control inflates the header instead.
  * ``RETIRED_FIELDS`` — controls the server refuses. A name here with no control guards nothing.

WHAT THIS COSTS WHEN IT DRIFTS, and it had drifted on all three: removing the retired controls left
8 names inflating banner counts, 13 ``withheldReason`` rules speaking about controls that no longer
existed, and 33 dead entries in ``RETIRED_FIELDS``. The visible failure is a banner announcing four
fields over a group showing three, and a header saying six controls are withheld on a form that has
none of them — which is exactly the reading an author cannot distinguish from a load failure.

The rule is therefore an IDENTITY, not a subset: a name is in ``GROUP_FIELDS`` if and only if a
``fld`` call renders it, in exactly one group.

`parent` IS THE CASE THAT MAKES THIS WORTH A TEST. It was retired on a measured "13 of 475", taken
against an older set. Against the shipped 539 it is declared by exactly the 77 note-read parts —
every one of them — and it is the only field saying which whole a part explains, which the workspace
trace and the Line Items sheet's indentation both walk. A stale measurement in a comment retired the
one field the hierarchy depends on, and nothing failed.

WHY A SOURCE TEST. There is no vitest setup in `frontend/`, and the Playwright suite needs both
servers. Same idiom as `test_render_phase_reseed` and `test_origin_contract`: read the source,
assert the property, fail on the commit that breaks it.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (ROOT / "frontend" / "src" / "screens" / "LineItems.tsx").read_text(encoding="utf-8")
SEED = json.loads(
    (ROOT / "backend" / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
    .read_text(encoding="utf-8"))


def _block(head: str, close: str) -> str:
    start = SRC.index(head)
    return SRC[start:SRC.index(close, start)]


def _names(text: str) -> list[str]:
    """Quoted strings in the CODE, not in the comments beside it.

    Every one of these lists carries prose explaining it, and that prose quotes labels and former
    field names — `"How the figure is obtained"` above `assembly` is the example that caught this.
    Reading those as members made the test fail on a form that was correct, which is the worst kind
    of assertion: it teaches the next author to delete the explanation.
    """
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    stripped = re.sub(r"^\s*//.*$", "", stripped, flags=re.M)
    return re.findall(r'"([^"]+)"', stripped)


RENDERED = set(re.findall(r'\{fld\("([^"]+)"', SRC))
GROUPED = _names(_block("const GROUP_FIELDS", "} as const;"))
CONDITIONAL = set(_names(_block("const CONDITIONAL_FIELDS", "\n];")))
RETIRED = set(_names(_block("const RETIRED_FIELDS = new Set([", "]);")))


def test_every_control_is_in_exactly_one_group() -> None:
    """The identity, in the direction that inflates a banner count."""
    assert sorted(GROUPED) == sorted(RENDERED), (
        f"listed with no control: {sorted(set(GROUPED) - RENDERED)}; "
        f"rendered but ungrouped: {sorted(RENDERED - set(GROUPED))}")
    assert len(GROUPED) == len(set(GROUPED)), (
        f"in two groups at once: {sorted(n for n in set(GROUPED) if GROUPED.count(n) > 1)}")


def test_no_withholding_rule_speaks_about_a_control_that_is_not_there() -> None:
    """A withheld reason is rendered UNDER its control, so one without a control is a count in the
    header and a sentence nobody can reach."""
    assert not CONDITIONAL - RENDERED, sorted(CONDITIONAL - RENDERED)


def test_nothing_is_retired_unless_it_guards_a_control() -> None:
    """`RETIRED_FIELDS` is a filter, not a changelog. A name with no control is inert, and the
    measurements it carried belong in prose where they cannot be mistaken for live rules.

    The two exceptions are deliberate and named: `requiredNow` forces `cascade` and `implemented_by`
    back onto a derived line's form, so both must be retired AND rendered — a derived line that has
    neither is refused by the server, and withholding the controls would leave an author with a
    refusal and nothing on screen to answer it.
    """
    forced = {"cascade", "implemented_by"}
    assert RETIRED & RENDERED == forced, sorted(RETIRED & RENDERED)
    assert not RETIRED - RENDERED, sorted(RETIRED - RENDERED)


def test_parent_is_authorable_because_every_part_declares_it() -> None:
    """THE STALE-MEASUREMENT REGRESSION. If `parent` is ever retired again, this says why not."""
    parts = [i for i in SEED["items"] if i.get("note_source")]
    assert len(parts) == 77, len(parts)
    assert all(i.get("parent") for i in parts), (
        "a note-read part with no parent has nothing to trace back to")
    assert "parent" in RENDERED and "parent" not in RETIRED, (
        "`parent` links each of the 77 parts to the whole it explains — retiring it makes a new "
        "part unauthorable and the set's only hierarchy unreadable")


def test_the_band_numbers_cover_the_groups_that_render() -> None:
    """`BANDS` drives expand-all/collapse-all. A number with no group leaves the control claiming to
    open a section that is not there; a group with no number gets a banner that does not respond."""
    bands = set(_names(_block("const BANDS = [", "];")) or
                re.findall(r"\d+", _block("const BANDS = [", "];")))
    rendered_bands = set(re.findall(r"band\((\d+), ", SRC))
    assert bands == rendered_bands, f"BANDS={sorted(bands)} vs rendered={sorted(rendered_bands)}"
