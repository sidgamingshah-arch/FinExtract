"""A field no configuration surface offers is not writable through the API either.

WHAT THIS CLOSES. Retiring a control used to mean only that the invitation to set it was gone from
the screen: the field kept working, the shipped values kept driving extraction, and the endpoint
kept ACCEPTING it. That is a half-measure and the remaining half is the dangerous one — a field
writable by an API call and visible on no screen is a value nobody can see, review or explain, and
the next author reading the console finds a figure driven by something the console says is not
configurable.

THE RULE IS "NO SURFACE OFFERS IT", not "the Line Items screen retired it", and the tests below pin
both directions of that distinction because the obvious rule gets each of them wrong:

  * `value_scope`, `confusable_with` and `exclude_hints` are retired on the Line Items screen and
    must stay writable, because `screens/Template.tsx` sends all three through this same endpoint.
  * `cascade`, `implemented_by` and `terms` are retired there too and must stay writable, because
    `requiredNow` forces them back onto the form for a derived or calculated line — without them a
    save is refused with no control on screen to answer the refusal.

WHAT IS NOT ASSERTED HERE: that the stored values are gone. They are not. This refuses a WRITE. The
pipeline goes on reading whatever the stored set declares, and every shipped figure is unaffected —
what can no longer happen is a NEW value arriving for a question nothing asks.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.api.routes.line_items import (_EDITABLE_FIELDS, _NOT_CONFIGURABLE, ItemEdit)

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# What `screens/Template.tsx` sends through `api.editLineItem` — read off its `edit` literal. A
# change there that is not mirrored here is exactly the regression this file exists to catch.
TEMPLATE_SCREEN_SENDS = frozenset({
    "key", "locale", "aliases", "sign_convention", "definition", "value_scope",
    "exclude_criteria", "confusable_with", "keyword_hints",
    "regex_hints", "exclude_hints",
})

# What `requiredNow` in `screens/LineItems.tsx` forces back onto the form despite retirement.
FORCED_BACK_ON = frozenset({"cascade", "implemented_by", "terms", "prompt"})


def test_every_refused_field_is_actually_on_the_wire():
    """A refusal naming a field the body cannot carry is dead code that reads as a guarantee."""
    unknown = sorted(f for f in _NOT_CONFIGURABLE if f not in _EDITABLE_FIELDS)
    assert not unknown, f"refused but not an editable field: {unknown}"


def test_every_refusal_says_what_decides_the_question_instead():
    """A refusal that does not name the alternative is a dead end. The author who sent one of these
    was answering a question the console stopped asking; "refused" does not tell them where it
    moved to."""
    for field, why in _NOT_CONFIGURABLE.items():
        assert why and len(why) > 15, f"{field} has no usable reason: {why!r}"
        assert not why.endswith("."), f"{field}'s reason reads as a sentence fragment in context"


def test_the_template_screen_can_still_save():
    """THE FIRST DIRECTION THE OBVIOUS RULE GETS WRONG. Three fields retired on the Line Items
    screen are sent by the Template screen through this very endpoint, so "retired there" is not
    "unauthorable"."""
    clash = sorted(TEMPLATE_SCREEN_SENDS & set(_NOT_CONFIGURABLE))
    assert not clash, (
        f"refusing {clash} breaks screens/Template.tsx, which sends them via api.editLineItem")


def test_a_control_the_form_forces_back_on_is_still_writable():
    """THE SECOND DIRECTION. `requiredNow` puts `cascade`/`implemented_by` back on a derived line's
    form and `terms` on a calculated one, because `_coherent` refuses the save without them — so
    refusing the write would make those line types unauthorable."""
    clash = sorted(FORCED_BACK_ON & set(_NOT_CONFIGURABLE))
    assert not clash, f"{clash} is forced onto the form but refused by the endpoint"


def test_the_fields_the_v2_spec_removed_are_all_refused():
    """The spec's own list, named rather than counted, so adding a field to the form without
    removing it here — or the reverse — fails."""
    expected = {
        "extraction_mode",                                    # `type` is the only source-of-figure
        "residual_policy", "never_sweep", "expected_components",   # residual is template-routed
        "temporality", "sign_expectation", "face_only",       # section policy
        "is_gross_parent", "children_if_decomposed", "rollup",     # the template's rollups
        "match_priority",                                     # the banner settles ties
        "output_structure", "others_rule", "derivation", "notes_as_source_rationale",
        "sole_component_of", "scopes", "side", "allow_contra", "analyst_bucket",
        "pattern", "description", "decomposition_rule",
    }
    assert set(_NOT_CONFIGURABLE) == expected, {
        "refused but not in the spec": sorted(set(_NOT_CONFIGURABLE) - expected),
        "in the spec but still writable": sorted(expected - set(_NOT_CONFIGURABLE)),
    }


def test_the_count_someone_has_to_justify():
    """23 of 52 wire fields refused. The number is here so widening the surface is a decision.

    53 UNTIL `section_disambiguation` WENT. Its 395 declarations held THIRTEEN distinct strings and
    every one was `"Bind only to {statement} / {section}."` — the line's own gate restated back to
    it, and naming an engine key. It was the request's only placement signal, so the payload now
    sends `printed_in` (the statement under the name a filing prints over it) instead.

    54 UNTIL `include_criteria` WENT. Measured before removing it: 375 of its 441 declarations held
    one generated sentence restating the line's own label, and it was sent to the model as `include`
    on 375 of the 518 lines a run asks about. The 69 authored ones were folded into `definition`,
    which both paths read.

    It was 26 until the audit found that `in_output`, `namespace` and `order` cannot be refused:
    the template has no row for an off-template PART (measured: 0 of the 77 appear in it), so it
    cannot decide a part's delivery, and refusing them made parts unauthorable."""
    assert len(_EDITABLE_FIELDS) == 52, len(_EDITABLE_FIELDS)
    assert len(_NOT_CONFIGURABLE) == 23, len(_NOT_CONFIGURABLE)


@pytest.mark.parametrize("field,value", [
    ("extraction_mode", "derive"),
    ("match_priority", 99),
    ("temporality", "instant"),
])
def test_the_body_still_PARSES_one_so_the_refusal_is_ours_to_word(field, value):
    """The refusal has to come from the apply, not from pydantic. `ItemEdit` still declares these,
    so a body carrying one parses and reaches the loop that refuses it BY NAME with a reason — a
    generic "extra fields not permitted" could not say what decides the question instead."""
    body = ItemEdit(key="k", **{field: value})
    assert field in body.model_fields_set
    assert field in _NOT_CONFIGURABLE


def test_no_shipped_item_would_now_be_unloadable():
    """THE HALF THAT IS NOT ASSERTED ANYWHERE ELSE: this refuses a WRITE and deletes nothing. The
    shipped set declares plenty of these — 462 items declare `face_only` alone — and it must go on
    loading and driving extraction exactly as before."""
    from app.schemas.line_items import load_line_item_set

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    shipped = load_line_item_set(raw, resolve=True)
    assert len(shipped.items) == 539

    declared = {f for f in _NOT_CONFIGURABLE for i in raw["items"] if f in i}
    assert declared, "no shipped item declares a refused field — then this test proves nothing"
    # …and the values survived the load rather than being dropped on the way in.
    by_key = {i.key: i for i in shipped.items}
    sample = next(i for i in raw["items"] if "face_only" in i)
    assert getattr(by_key[sample["key"]], "face_only") == sample["face_only"]
