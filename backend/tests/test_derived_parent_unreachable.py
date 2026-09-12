"""A DERIVED PARENT IS REACHED BY NOTHING, and this is the measurement rather than the claim.

NEW FILE -> backend/tests/test_derived_parent_unreachable.py

THE OPEN QUESTION THIS CLOSES. `map_ontology` asserted that "a derived parent is reached by nothing
— not the model, not an alias, not a regex, not a semantic probe… four separate indexes in the
backend now agree on it", and the config plan flagged that claim as unverified: reading the locks in
`line_item_matching` suggested only 3 of the 9 derived lines were excluded, which would make the 258
recognition entries sitting on them live rather than inert. Deleting them would then have been a
behaviour change wearing a cleanup's clothes.

The reading was wrong and the claim is right. `_unmatchable` has THREE clauses, not two:

    alias_matching == "disabled"  or  extraction_mode == "derive"  or  type == "derived"

The third is what covers a derived parent regardless of the other two, and all 9 of 9 are in the
set. So the recognition was inert, and it is gone.

WHY THE PROBE AND NOT JUST THE SET. Membership of `_unmatchable` is one of four gates, and the
alias index itself is built with no filter at all — every derived line's aliases are still indexed,
and only the gate keeps them from winning. A test that asserted membership would pass while a
regression in the gate let those captions bind. So the assertion here is at the level of the
ANSWER: match every caption either matcher knows and compare.

THE THREE OTHER ROUTES, each with its own test below: `note_sets` builds no note probe for a line
`asked_about` rejects, `line_item_requests` never puts one in a request, and `line_item_payload` is
never called for one. Together with the matcher that is the "four indexes" the claim named, now
measured instead of asserted.
"""
from __future__ import annotations

import json
import pathlib

from app.schemas.line_items import load_line_item_set
from app.services import line_item_notes, line_item_requests
from app.services.line_item_matching import LineItemMatcher

SEED = (pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")


def _set():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")))


def _derived(st) -> list:
    return [d for d in st.items if str(getattr(d.type, "value", d.type)) == "derived"]


def test_every_derived_line_is_locked_out_of_the_matcher() -> None:
    st = _set()
    derived = _derived(st)
    unmatchable = LineItemMatcher(st)._unmatchable

    assert len(derived) == 9, len(derived)
    assert all(d.key in unmatchable for d in derived), (
        [d.key for d in derived if d.key not in unmatchable])


def test_no_caption_in_the_whole_set_resolves_to_a_derived_line() -> None:
    """THE ANSWER-LEVEL ASSERTION. Every label and every alias of every line, in every locale — the
    surface a filing's captions are drawn from — and not one of them may bind a derived parent."""
    st = _set()
    matcher = LineItemMatcher(st)
    derived = {d.key for d in _derived(st)}

    captions: set[str] = set()
    for d in st.items:
        captions.add(d.label or "")
        captions.update(d.aliases)
        for values in (d.aliases_i18n or {}).values():
            captions.update(values)
    captions.discard("")

    assert len(captions) > 1_500, f"only {len(captions)} captions probed — the surface shrank"
    bound = [(c, matcher.match(c).key) for c in sorted(captions)
             if matcher.match(c).key in derived]
    assert not bound, bound[:5]


def test_no_derived_line_carries_recognition_any_more() -> None:
    """258 entries were removed from the nine: 77 aliases, 142 locale aliases, 15 keyword hints,
    7 regex hints, 17 exclude hints. Re-adding one would not fail anything at runtime — which is
    exactly why it fails here."""
    for d in _derived(_set()):
        assert not d.aliases, d.key
        assert not d.aliases_i18n, d.key
        assert not d.regex_hints, d.key
        assert not d.keyword_hints, d.key
        assert not d.exclude_hints, d.key


def test_no_derived_line_is_asked_about_or_given_a_note_probe() -> None:
    """The other three routes. `note_sets` consults `asked_about` before scoring, so a line it
    rejects gets no note set — and a line with no note set is in no request."""
    st = _set()
    derived = _derived(st)

    assert not [d.key for d in derived if line_item_requests.asked_about(d)]
    selected = line_item_notes.note_sets(st.items, [])
    assert not [d.key for d in derived if d.key in selected]
