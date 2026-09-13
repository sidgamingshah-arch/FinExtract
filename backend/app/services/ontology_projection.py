"""Turn ontology concepts into line-item definitions — the content half of the merge.

The schema merge made `LineItemDef` able to CARRY everything a concept declares. This module is
what actually moves the 462, so the line-item set stops being 21 definitions describing a corner of
the rulebook and becomes the rulebook.

WHY THIS IS APP CODE AND NOT A SCRIPT. Two callers need the same projection and must not drift:
`scripts/build_line_items.py` writes the seed with it, and `scripts/project_ontology.py` checks
parity with it. A projection that differs between the thing that builds and the thing that verifies
proves nothing at all.

THE THREE POPULATIONS, measured on the shipped rulebook:

    454  concepts the configurator never described  ->  projected as-is
      8  keys in BOTH — exactly the output lines    ->  projected, then the configurator's
                                                        assembly (cascade, rollup, type) wins
     13  `sub__*` note-level parts, configurator only -> carried through untouched

The 8 are the whole point of the overlap rule. The ontology knows WHERE those lines may be
claimed from; the configurator knows HOW they are assembled from note-level parts. Neither
description is complete and the merged definition needs both halves, so the merge is per-field
rather than one artefact winning.
"""
from __future__ import annotations

import copy
from enum import Enum
from typing import Any

from app.services.mapping import _KEY_SECTION_OVERRIDES

# Ontology field -> line-item field, where the merge deliberately renamed it. `exclude` is the
# rename that mattered: the rulebook has BOTH `exclude` (prose criteria shown to the LLM) and
# `exclude_hints` (regexes that veto a match), and the first configurator collapsed them into one
# regex-validated list — which would either refuse the prose at the door or compile it as an
# accidental veto.
RENAMED: dict[str, str] = {
    "canonical_key": "key",
    # `include` -> `include_criteria` is gone with the field. A rulebook concept that still declares
    # `include` now lands nowhere, which `scripts/project_ontology.py` reports as an unhomed
    # declaration — the right outcome: it says the prose has no destination rather than writing it
    # to a field nothing reads.
    "exclude": "exclude_criteria",
}

# Carried under the same name. `scripts/project_ontology.py` fails the build if a concept declares
# a field that lands nowhere HERE — which is a check that every declaration has a home, not that
# anything reads it.
#
# THAT DISTINCTION WAS WORTH THE CORRECTION. This comment used to claim "every one of these is read
# by live code or by the LLM payload", and it was false. `min_confidence_to_auto_accept` WAS in the
# tuple below, carried 0.85 on all 462 concepts, and was read by NOTHING — not by the ported
# matcher and not by the incumbent, where all four accept decisions compare against the global
# `settings.extraction.auto_accept_confidence` (default 0.80) instead. It has since been removed
# from `LineItemDef` outright rather than wired up, so the rulebook's copy no longer lands here.
# `ResidualFramework.alias_matching`, `allow_contra` and `caption_normalization` are still in that
# state and still carried.
#
# A projection that carries a field faithfully is doing its job. A COMMENT that promises the field
# is live is the failure this whole exercise keeps finding, and it is worse here than in config,
# because the next person reads it as evidence and stops looking.

SAME: tuple[str, ...] = (
    # `description` IS NOT HERE: the line-item model no longer has the field, so a rulebook
    # concept declaring it lands nowhere and the projection script reports an unhomed declaration.
    # Its 85 shipped values were sourcing instructions, and they went to `prompt` and `definition`.
    # `confusable_with` IS NOT HERE either: the line-item model no longer has it, so a rulebook
    # concept declaring it lands nowhere. Its two shipped pairs became `exclude_hints`.
    "label", "definition", "prompt", "value_scope",
    "extraction_mode",
    "analyst_bucket", "aliases", "aliases_i18n", "keyword_hints", "regex_hints", "exclude_hints",
    "sign_rule", "inherits", "statement", "section_scope",
    "temporality", "face_only", "unit_of_account", "note_use", "note_use_rationale",
    "sign_convention", "match_priority", "alias_matching", "residual_policy",
    "expected_components", "never_sweep",
    # Containment — the discriminator whose absence left 31 caption collisions undecidable, all
    # of one shape: a gross parent against the child it contains, same statement, same banner,
    # same priority. mapping.py and map_ontology.py both read these.
    "is_gross_parent", "children_if_decomposed", "sole_component_of",
    # Prose. `section_disambiguation` IS NOT HERE: the line-item model no longer has the field, so
    # a concept declaring it lands nowhere and the projection script reports an unhomed declaration
    # — the same outcome `include` got. `mapping.py` still reads the RULEBOOK's own copy for its
    # confusable-tie step, which is a different model on a different path.
    "decomposition_rule", "others_rule", "derivation",
    "notes_as_source_rationale",
)

# How `extraction_mode` becomes a line-item `type`. The ontology says how a value may ARRIVE;
# `type` says the same in the configurator's vocabulary.
#
# `derive` is the interesting one: it means the framework COMPUTES this and a filing does not print
# it. That is `derived`, and `mapping.py` keeps such a concept out of every matching tier and out of
# the LLM payload — which the merged model expresses as `extraction_mode` rather than by having the
# matcher special-case a type.
TYPE_OF_MODE: dict[str, str] = {
    "extract": "extracted",
    "extract_or_derive": "extracted",   # printed when printed, derived when not — read first
    "derive": "derived",
    "do_not_extract": "extracted",      # refused by `extraction_mode`, never by `type`
}

# Fields the configurator owns for a key it shares with the ontology. Everything else on such a
# key comes from the rulebook, because the rulebook is what knows where a caption may be claimed.
ASSEMBLY_FIELDS: tuple[str, ...] = (
    "type", "cascade", "terms", "rollup", "implemented_by", "parent", "order", "in_output",
    "namespace", "note_source", "scopes", "side", "allow_contra",
)


def _jsonable(value: Any) -> Any:
    """Plain JSON types only.

    The resolved concept hands over live pydantic models (`SignRule`, `ResidualPolicy`) and enum
    members (`StatementType`), which `LineItemDef` would happily re-validate but `json.dumps`
    refuses — and the seed has to round-trip through a file. Converted here rather than at the
    call site so the projection is serialisable by construction for every caller.
    """
    if hasattr(value, "model_dump"):
        # `exclude_unset` IS THE WHOLE POINT, not a size optimisation. `residual_policy` is read
        # through `residual._declared`, which asks `name in policy.model_fields_set` to decide
        # whether the CONCEPT overrode a term or inherits the framework's value. A plain dump
        # writes all seven fields at their defaults, so the reloaded policy reports every term as
        # authored — and the `residual_framework` block, one definition meant to govern all
        # thirteen residuals, would then govern none of them. Measured on a real policy:
        # authored {itemise, section_scope} dumps as all 7 and round-trips as all 7; with
        # `exclude_unset` it round-trips as the 2 that were written.
        return value.model_dump(mode="json", exclude_unset=True)
    if isinstance(value, Enum):
        return value.value              # StatementType.BALANCE_SHEET -> "balance_sheet"
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def project_concept(m: Any) -> dict:
    """One resolved `OntologyMapping` as a `LineItemDef` dict.

    Takes the RESOLVED concept — `loader.load_ontology(..., resolve=True)` — because the gate is
    what matters and 462 of 462 concepts declare `statement` on none of themselves. Projecting the
    unresolved shape would produce 462 definitions that constrain nothing.
    """
    out: dict = {}
    for src, dst in RENAMED.items():
        out[dst] = _jsonable(getattr(m, src))
    for field in SAME:
        value = getattr(m, field, None)
        if value is None or value == [] or value == {}:
            continue                     # absent stays absent; a default is not a declaration
        out[field] = _jsonable(value)

    # A HARD-CODED CORRECTION, TURNED INTO DATA. `mapping._KEY_SECTION_OVERRIDES` holds one entry:
    # `is_pl__minority_interests_pl` -> `profit_attributable_to`. The concept declares `is_pl`,
    # the broad namespace, but the row it describes really sits in the profit-attribution tail —
    # so `_sections_of` overrides the declaration in code, and the gate obeys the override while
    # the rulebook, the only place a reviewer can look, says otherwise.
    #
    # Projecting the override INTO `section_scope` is the merge doing its job: the definition now
    # declares the section it is actually gated to, the matcher needs no table, and a reviewer
    # reading the config sees what the pipeline does. Measured: without this the ported matcher
    # accepted 15 captions the rulebook refuses, every one of them a minority-interest caption
    # offered under the income-and-expenses banner.
    override = _KEY_SECTION_OVERRIDES.get(out["key"])
    if override:
        out["section_scope"] = [override]

    mode = str(getattr(m, "extraction_mode", "extract") or "extract")
    out["type"] = TYPE_OF_MODE.get(mode, "extracted")
    if out["type"] == "derived":
        # A derived line needs a cascade or a named implementer. The rulebook says HOW in prose
        # (`derivation`), which is not a cascade, so the projection records who computes it today
        # rather than inventing rungs nobody wrote.
        out["implemented_by"] = "rulebook_derivation"
    out.setdefault("namespace", "template")
    return out


def merge_assembly(projected: dict, configured: dict) -> dict:
    """A key the rulebook and the configurator both describe: gate from one, assembly from the other.

    Field-by-field rather than whole-artefact, because each side knows something the other does
    not. For `is_pl__deprec_and_impairment_oper_exp` the rulebook supplies the statement, the
    section, the priority, the aliases and the sign expectation; the configurator supplies the
    five-rung cascade and the twelve note-level parts it draws on. Letting either win outright
    would throw away half of what is known about that line.
    """
    merged = copy.deepcopy(projected)
    for field in ASSEMBLY_FIELDS:
        if field in configured:
            merged[field] = copy.deepcopy(configured[field])
    # The configurator's own prose is written for a reader of this screen; keep the rulebook's
    # `definition` (which the LLM matches against) either way.
    return merged


def build_definitions(concepts: list[Any], configured: list[dict]) -> tuple[list[dict], dict]:
    """The whole merged set, plus a census of where each definition came from.

    Order is the rulebook's declaration order first, then the configurator's own additions. That
    is deliberate and load-bearing: for a caption whose claimants tie on every principled field —
    one such pair exists, `presented in` — both engines answer by declaration order, so preserving
    the rulebook's order is what makes the merged set equivalent rather than merely similar.
    """
    by_key = {d["key"]: d for d in configured}
    out: list[dict] = []
    census = {"projected": 0, "merged": 0, "configurator_only": 0}

    seen: set[str] = set()
    for concept in concepts:
        projected = project_concept(concept)
        key = projected["key"]
        seen.add(key)
        if key in by_key:
            out.append(merge_assembly(projected, by_key[key]))
            census["merged"] += 1
        else:
            out.append(projected)
            census["projected"] += 1

    for definition in configured:
        if definition["key"] not in seen:
            out.append(copy.deepcopy(definition))
            census["configurator_only"] += 1

    return out, census
