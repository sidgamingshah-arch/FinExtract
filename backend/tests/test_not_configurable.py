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
# `prompt` WAS HERE. `requiredNow` forced it onto the form when `output_structure` was `prose`,
# because the server refused prose without one. `prompt` is now merged into `definition` and
# refused by the endpoint, and the rule that forced it has been deleted from the form — so keeping
# it here would assert a clash that no longer exists in either direction.
FORCED_BACK_ON = frozenset({"cascade", "implemented_by", "terms"})


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
        # `description` WAS HERE AND THE FIELD IS GONE, so there is nothing to refuse. Its 85
        # values were sourcing instructions and went to `prompt` (77 extracted lines) and
        # `definition` (8 derived, which cannot carry a prompt).
        "pattern", "decomposition_rule",
        # `prompt` is MERGED INTO `definition` rather than deleted: the field stays on the wire so
        # a set authored before the merge still parses, `LineItemDef` folds it into `definition`
        # on load, and the 61 shipped lines that carried both have been folded in place. Refused
        # here so a new one cannot be created by API call for a question no screen asks.
        "prompt",
        # `note_use` — decomposition is ALWAYS allowed, so the question is gone and every reader of
        # it was removed (`stages/note_sourced`, `stages/map_ontology`, `stages/residual`). The
        # field stays on the wire and in the stored sets; nothing consults it.
        "note_use",
    }
    assert set(_NOT_CONFIGURABLE) == expected, {
        "refused but not in the spec": sorted(set(_NOT_CONFIGURABLE) - expected),
        "in the spec but still writable": sorted(expected - set(_NOT_CONFIGURABLE)),
    }


def test_the_count_someone_has_to_justify():
    """24 of 53 wire fields refused.

    53 SINCE `statements` ARRIVED — every statement a line may be claimed on, as a LIST. The
    singular `statement` was not merely narrow, it was actively refusing: `_in_statement` returns
    False for a concept "clearly on a different statement", so a caption genuinely printed on two —
    depreciation on the income statement and again in the cash-flow reconciliation, interest on the
    P&L and again under financing — had one printing gated for and the other REFUSED rather than
    merely unmatched, landing in a residual with nothing saying the gate did it. The singular is
    folded into the list on load and stays on the wire, so this is one field wider and one question
    fewer: the two halves of placing (which statements, which banners) are now asked the same way,
    where before `section_scope` was a list and the statement was a single value with no control at
    all. Measured identical to the old gate over 527 keys x 9 statements: 0 disagreements.

    52 SINCE `route` ARRIVED — where an extracted line's figure is read from: the face, a note's
    table rows, or prose. It is a question the configuration never asked and instead INFERRED from
    three declarations that had to agree (`SectionDefaults.where()`, itself derived from
    `face_only` and `scopes`; whether a `note_source` object was present; and whether that object
    carried prose patterns). An author could satisfy two and not the third, and the failure was
    silent — a line read from nowhere, or a face line quietly taking a figure out of a note, which
    reconciles against nothing. One asked question replaces three inferred ones, so the wire is one
    field wider and the surface is smaller.

    24 SINCE `note_use` WENT. It asked whether a cited note may SUPPLY a line's figure or only
    corroborate it — "notes are evidence for a face amount, never an independent source of one,
    unless note_use is decomposition_allowed". Decomposition is always allowed now, and all three
    readers are removed. Measured before removing: of the 70 items resolving to `evidence_only`,
    ZERO were a note-sourced parent, ZERO carried a `note_source` and ZERO had a child, so the
    `note_sourced` gate could not fire; `residual`'s term was already true via `face_only is
    False` on all 394 items that declare it. The one real widening is `map_ontology`'s
    note-permitted SHORTLIST, which went from 1 to 17 candidates on the hkfrs pair and is still
    gated downstream by the cited note, the same section and the column arithmetic.

    23 SINCE `prompt` WAS MERGED INTO `definition`. The configuration asked the same author the
    same question twice — "what is this line" and "what else should the model be told about it" —
    and both answers went into the same request under different keys, so nothing distinguished them
    but the key. Measured before merging: 462 lines carried a `definition` alone, 61 carried BOTH,
    and 0 carried a `prompt` alone, so a merge that PICKED one would have dropped authored
    instruction on 61 lines; it concatenates. The field stays on the wire (hence 51, not 50) and is
    folded on load, so a set authored before the merge still works — what is refused is authoring a
    NEW one for a question no screen asks.

    51 SINCE `terms_op` ARRIVED — how a calculated line's terms, or a cascade rung's, combine: sum,
    max, min or first. Added because the related-party spec's own selection rule is MAX_VALID(Find
    1, Find 2, Find 3) and the engine could only sum or take the first available, which the line's
    replaced rung note recorded as a compromise. `sum` is the default and every previously shipped
    formula means it, so the field widened the surface without moving a figure.

    51 UNTIL `confusable_with` WENT. Its two mutual pairs became exclusions: 36 vetoes for the other
    line's distinctive captions, and 9 aliases removed from the line they did not belong to. The number is here so widening the surface is a decision.

    52/23 UNTIL `description` WENT. Its 85 values described HOW a figure is sourced rather than what
    the line is, and the payload read them only as a fallback for a missing `definition` — a branch
    that never fired, because all 539 lines declare one. They moved to the field that reads them:
    `prompt` for the 77 extracted lines (all of which already had one) and `definition` for the 8
    derived, which cannot carry a prompt at all.

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
    assert len(_EDITABLE_FIELDS) == 53, len(_EDITABLE_FIELDS)
    assert len(_NOT_CONFIGURABLE) == 24, len(_NOT_CONFIGURABLE)


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
    # 527, not 546: the live configuration was exported into the shipped seed — see
    # `test_retired_derivations.test_the_shipped_set_is_the_configuration_in_force`.
    assert len(shipped.items) == 546  # 540 since the related-party PAYABLES gained the two readings the note prints as groups — 其他应付款 and 长期应付款 — which is what lets Due to Related Parties(CP) and its LTP twin publish at all  # 538 since the loss allowance split into its two readings — the 坏账准备 COLUMN of a measure grid and an allowance printed as its own ROW — which is what lets CP_P2 subtract it  # 536 since the other-receivables line gained the interest-and-dividends-receivable component its own definition names: a filing that prints 应收利息/应收股利 as siblings of 其他应收款 has not put them inside it  # 535 since the other-receivables NET split into the two ways a note prints one — the 账面价值 COLUMN of a gross/allowance/net grid, and a row whose own reported amount is the net; one part holds one `measure`, and 000709 needs both readings

    declared = {f for f in _NOT_CONFIGURABLE for i in raw["items"] if f in i}
    assert declared, "no shipped item declares a refused field — then this test proves nothing"
    # …and the values survived the load rather than being dropped on the way in.
    by_key = {i.key: i for i in shipped.items}
    sample = next(i for i in raw["items"] if "face_only" in i)
    assert getattr(by_key[sample["key"]], "face_only") == sample["face_only"]
