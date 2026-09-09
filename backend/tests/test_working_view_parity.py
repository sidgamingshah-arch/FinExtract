"""The gate that makes the ontology's removal provable: the working view IS the rulebook.

`services.working_view.build_working_view` is the inverse of `services.ontology_projection`, and
it is the load-bearing piece of making line items the single configuration engine — the matcher
keeps asking its questions of an `OntologyDefinition`, but that object is now derived from the
`LineItemSet` instead of loaded from a stored rulebook. An inexact inverse moves which line item a
caption resolves to, on every filing, with every subtotal still tying. So the inverse has to be
proved rather than reviewed.

THIS FILE REPLACES TWO SCRIPTS. `scripts/project_ontology.py` checked that every concept
declaration had a home on `LineItemDef`, and `scripts/parity_line_items.py` compared the ported
matcher against the incumbent. Both ran by hand, off the suite, against a rulebook that is being
withdrawn. The property that still matters is narrower and stronger: the working view built from a
set must equal the rulebook that set was projected from, field for field.

TWO GATES, and the split is deliberate.

  1. THE ROUND TRIP (`test_round_trip_*`) — rulebook -> `project_concept` -> `LineItemSet` ->
     `build_working_view` -> rulebook. This is what "exact inverse" means, it is independent of
     whether the SHIPPED seed happens to be current, and it cannot rot: regenerate either artefact
     and it still holds. Measured: 462 of 462 concepts, every projected field identical, with one
     intended divergence (`_SECTION_OVERRIDE`).

  2. THE SHIPPED SET (`test_shipped_*`) — the properties that must hold of the artefact the
     application actually boots with: 462 concepts and not 475, the framework blocks present,
     the residual policies' `model_fields_set` intact, the section override idempotent.

WHY GATE 2 DOES NOT PIN FIELD-FOR-FIELD EQUALITY, stated rather than quietly dropped. Measured
today, the shipped set is NOT a fresh projection of `output_csv_hk_ontology.json`: it diverges on
`confusable_with` (29 concepts), `description` (8), `section_disambiguation` (3) and
`aliases`/`aliases_i18n` (2 each). Only the `description` group is by design — those 8 are the keys
the rulebook and the configurator BOTH describe, where `merge_assembly` deliberately keeps the
configurator's prose. The other 34 are seed staleness: the rulebook was edited after the seed was
built. Pinning the current divergence exactly would either fail on arrival or bake the staleness in
and pass forever, so `test_shipped_set_diverges_only_in_the_known_classes` asserts the divergence
is CONFINED to those field names — a newly diverging field fails, a regenerated seed still passes.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import LineItemDef, load_line_item_set
from app.schemas.loader import load_ontology
from app.services.line_item_config import load_shipped_set
from app.services.mapping import _KEY_SECTION_OVERRIDES
from app.services.ontology_projection import RENAMED, SAME, _jsonable, project_concept
from app.services.working_view import (CARRIED, UNHOMED, _definition_of, build_working_view)

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
RULEBOOK = TEMPLATES / "output_csv_hk_ontology.json"

# Every field the projection claims to carry, in both directions.
PROJECTED = sorted(set(SAME) | set(RENAMED))

CONCEPTS = 462                 # the rulebook's concepts, and the set's `namespace == "template"`
ITEMS = 475                    # …plus the 13 `sub__*` note-level parts, which are not concepts
RESIDUALS = 11                 # concepts carrying a `residual_policy` (all `exclusive_residual`)

# The one concept whose working view deliberately DISAGREES with the rulebook, because the
# projection wrote `mapping._KEY_SECTION_OVERRIDES` into the data: the rulebook declares the broad
# `is_pl` namespace, the row really sits in the profit-attribution tail, and `_sections_of` used to
# override the declaration from a table in code where no reviewer could see it.
_SECTION_OVERRIDE = ("is_pl__minority_interests_pl", "section_scope")

# Fields on which the SHIPPED seed may lag the rulebook — see the module docstring. `description`
# is by design (the 8 merged keys keep the configurator's prose); the rest are staleness, and the
# assertion is that the divergence goes no wider than these names.
_SEED_LAG = {"description", "confusable_with", "section_disambiguation", "aliases", "aliases_i18n"}


def _rulebook():
    """The shipped rulebook, resolved — the shape `project_concept` consumed."""
    return load_ontology(json.loads(RULEBOOK.read_text(encoding="utf-8")), resolve=True)


def _by_key(definition):
    return {m.canonical_key: m for m in definition.mappings}


def _round_trip_set():
    """A `LineItemSet` that IS a fresh projection of the shipped rulebook.

    Only the projection and the set-level blocks the working view reads — no configurator overlay,
    so nothing but the inverse is under test here. `scripts/build_line_items.py` builds the shipped
    seed the same way and then merges the configurator's assembly over 8 keys.
    """
    raw = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    document = {
        "schema_version": 1,
        "line_items_key": raw["ontology_key"],
        "target_template_key": raw["target_template_key"],
        "locale": raw.get("locale", "en"),
        "supported_locales": raw.get("supported_locales", ["en"]),
        "section_defaults": raw.get("section_defaults") or {},
        "normalisation": raw.get("normalisation"),
        "binding": raw.get("binding"),
        "global_rules": raw.get("global_rules") or {},
        "scope_selection": raw.get("scope_selection"),
        "residual_framework": raw.get("residual_framework"),
        "items": [project_concept(m) for m in _rulebook().mappings],
    }
    return load_line_item_set(json.loads(json.dumps(document, ensure_ascii=False)))


def _field_diff(view, reference) -> dict[str, list[str]]:
    """Which projected fields disagree, and on which concepts. Field name -> canonical_keys."""
    got, want = _by_key(view), _by_key(reference)
    out: dict[str, list[str]] = {}
    for key in sorted(want):
        if key not in got:
            continue
        for field in PROJECTED:
            if _jsonable(getattr(want[key], field)) != _jsonable(getattr(got[key], field)):
                out.setdefault(field, []).append(key)
    return out


# ── gate 1: the round trip is an exact inverse ───────────────────────────────────────────────

def test_round_trip_covers_every_concept():
    view = build_working_view(_round_trip_set())
    reference = _rulebook()
    assert len(reference.mappings) == CONCEPTS
    assert set(_by_key(view)) == set(_by_key(reference))
    assert len(view.mappings) == CONCEPTS


def test_round_trip_is_field_for_field_identical():
    """The whole point. Every projected field, every concept, one documented exception."""
    diff = _field_diff(build_working_view(_round_trip_set()), _rulebook())
    offenders = sorted((field, key) for field, keys in diff.items() for key in keys)
    assert offenders == [(_SECTION_OVERRIDE[1], _SECTION_OVERRIDE[0])], (
        "the working view is no longer the exact inverse of the projection — a caption's answer "
        f"can move on every filing: {offenders}")


def test_round_trip_preserves_residual_policy_declarations():
    """`residual._declared` asks `name in policy.model_fields_set`, so a full dump is a defect.

    Dumped without `exclude_unset` every one of the seven terms reads as authored by the concept,
    and `residual_framework` — one definition meant to govern all of the residual buckets —
    governs none of them.
    """
    view, reference = build_working_view(_round_trip_set()), _rulebook()
    got, want = _by_key(view), _by_key(reference)
    residuals = [k for k, m in want.items() if m.residual_policy is not None]
    assert len(residuals) == RESIDUALS
    for key in residuals:
        policy, mirror = want[key].residual_policy, got[key].residual_policy
        assert mirror is not None, f"{key} lost its residual_policy entirely"
        assert mirror.model_fields_set == policy.model_fields_set, (
            f"{key}: authored terms {sorted(policy.model_fields_set)} came back as "
            f"{sorted(mirror.model_fields_set)}, so the framework governs the wrong terms")


def test_the_section_layer_must_be_folded_and_only_one_field_depends_on_it():
    """HAZARD 1, pinned in both directions. The items are stored ALREADY RESOLVED, so `resolve`
    looks like it cannot matter — and it matters on exactly one field.

    `note_use_rationale` is in `ontology_projection.SAME` but `LineItemDef` declares no such field,
    so the projection writes it to the seed and pydantic's `extra="ignore"` drops it at load. The
    set's `section_defaults` still declare it, which makes the fold the only route back. Everything
    else is idempotent because a declaration beats a section default.
    """
    assert UNHOMED == ("note_use_rationale",), (
        "a member of `SAME` has no home on `LineItemDef`, so the projection writes it and the "
        f"load drops it: {UNHOMED}. Either declare the field or take it out of `SAME`")
    assert "note_use_rationale" not in CARRIED

    payload = _definition_of(_round_trip_set())
    unfolded = load_ontology(json.loads(json.dumps(payload)), resolve=False)
    folded = load_ontology(json.loads(json.dumps(payload)), resolve=True)
    moved = {f: len(keys) for f, keys in _field_diff(unfolded, folded).items()}
    assert moved == {"note_use_rationale": 394}, (
        f"the section fold now moves more than the one unhomed field: {moved}")
    assert build_working_view(_round_trip_set()).mappings[0].note_use_rationale == \
        _by_key(_rulebook())[payload["mappings"][0]["canonical_key"]].note_use_rationale


# ── gate 2: the artefact the application boots with ──────────────────────────────────────────

def test_shipped_set_projects_462_concepts_not_475():
    st = load_shipped_set()
    assert len(st.items) == ITEMS
    view = build_working_view(st)
    assert len(view.mappings) == CONCEPTS
    dropped = {i.key for i in st.items} - set(_by_key(view))
    assert len(dropped) == ITEMS - CONCEPTS
    assert all(k.startswith("sub__") for k in dropped), (
        f"something other than the note-level parts was dropped: {sorted(dropped)}")
    assert all(i.namespace == "internal" for i in st.items if i.key in dropped)
    # The keys the matcher sees are the rulebook's keys, so no tier gains a claimant.
    assert set(_by_key(view)) == set(_by_key(_rulebook()))


def test_shipped_set_diverges_only_in_the_known_classes():
    """See the module docstring: the shipped seed lags the rulebook, and this bounds the lag."""
    diff = _field_diff(build_working_view(load_shipped_set()), _rulebook())
    unexpected = {f: keys for f, keys in diff.items()
                  if f not in _SEED_LAG
                  and (f, tuple(keys)) != (_SECTION_OVERRIDE[1], (_SECTION_OVERRIDE[0],))}
    assert not unexpected, (
        "the shipped set and the rulebook disagree on a field neither the configurator merge nor "
        f"a stale seed explains: { {f: len(k) for f, k in unexpected.items()} }")


def test_shipped_set_keeps_the_section_override_exactly_once():
    """`mapping._KEY_SECTION_OVERRIDES` is idempotent over the projected value.

    The projection wrote the correction into `section_scope`, so the table in code now re-applies
    it to a value that already carries it. Applied twice it would still be one entry; the assertion
    is that the entry is there ONCE and is the corrected section, not the rulebook's `is_pl`.
    """
    key, _ = _SECTION_OVERRIDE
    concept = _by_key(build_working_view(load_shipped_set()))[key]
    assert concept.section_scope == ["profit_attributable_to"]
    assert _KEY_SECTION_OVERRIDES[key] == "profit_attributable_to"
    reapplied = [_KEY_SECTION_OVERRIDES.get(key, s) for s in concept.section_scope]
    assert reapplied == concept.section_scope == ["profit_attributable_to"]
    assert _by_key(_rulebook())[key].section_scope == ["is_pl"]


def test_shipped_set_carries_the_framework_blocks():
    """The four blocks the pipeline reads on every run must arrive on the view, non-empty.

    A configured EMPTY value means nothing here — but an empty block on the SHIPPED set would mean
    the framework the run obeys came from somewhere this configuration cannot reach, which is the
    whole failure being removed.
    """
    view = build_working_view(load_shipped_set())
    for block in ("normalisation", "binding", "global_rules", "scope_selection"):
        got = getattr(view, block)
        assert got is not None, f"{block} did not survive onto the working view"
        assert _jsonable(got), f"{block} arrived empty, so the run's framework has no source"
    assert view.residual_framework is not None
    assert view.section_defaults and view.ontology_key == load_shipped_set().line_items_key
    assert view.target_template_key == "output_csv_hk_v1"


def test_the_view_is_cached_per_set():
    st = load_shipped_set()
    assert build_working_view(st) is build_working_view(st)
    # A different set is a different view, keyed on the canonical JSON rather than on identity.
    assert build_working_view(_round_trip_set()) is not build_working_view(st)


@pytest.mark.parametrize("field", ["type", "implemented_by"])
def test_derived_fields_are_not_carried_back(field):
    """`type` is derived from `extraction_mode` (which round-trips) and `implemented_by` names a
    service, so neither is a concept property and `OntologyMapping` has nowhere to put them."""
    assert field in LineItemDef.model_fields
    assert field not in PROJECTED
    concept = _definition_of(load_shipped_set())["mappings"][0]
    assert field not in concept
