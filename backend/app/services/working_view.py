"""The matcher's working view of a line-item set — the exact inverse of the projection.

WHY THIS EXISTS. `services.ontology_projection` turned 462 rulebook concepts into line-item
definitions so the set could BE the rulebook. This is the other direction, and it is what lets the
ontology stop being a stored, selectable, user-visible artefact: the matching MECHANISM
(`services.mapping`, `stages/map_ontology`) still asks its questions of an `OntologyDefinition`,
but that object is now BUILT FROM the `LineItemSet` on the way in rather than loaded from a row in
`ontology_versions`. Line items is the single configuration engine; this is the adapter that made
rewriting three thousand lines of matcher unnecessary.

    LineItemSet  --build_working_view-->  OntologyDefinition  -->  the matcher
      (authored, versioned, the                (derived, in-memory,
       thing a user configures)                 never stored, never served)

NOTHING IS RETYPED HERE. `RENAMED`, `SAME` and `_jsonable` are imported from
`ontology_projection`, because a second copy of the field list is how a projection and its inverse
drift apart — and a drift between them is silent: it moves which line item a caption resolves to
on every filing, with every subtotal still tying. The inverse of `RENAMED` is computed, not
written out.

WHAT IS DELIBERATELY DROPPED, and why each is safe:

    13 items       `namespace != "template"` — the `sub__*` note-level parts. They were never
                   rulebook concepts (they are parts OF a line, not lines), they are off-template
                   by design, and admitting them would add 13 claimants to every matching tier.
                   475 - 13 = 462, which is exactly the rulebook's concept count.
    `type`         derived from `extraction_mode` by `TYPE_OF_MODE`, and `extraction_mode` is
                   itself in `SAME` and round-trips directly. Reconstructing the mode from the
                   type is not even possible — three modes map onto "extracted".
    `implemented_by`  written by the projection only to satisfy `LineItemDef`'s "a derived line
                   needs a cascade or an implementer" rule. It names an implementer, not a concept
                   property, `OntologyMapping` has nowhere to put it, and the shipped set names
                   one for none of the eight — every derived line there carries a cascade instead.

THE TWO HAZARDS THAT COULD NOT BE SETTLED BY READING, both now measured:

 1. DOUBLE-RESOLUTION OF THE SECTION LAYER. 462 of 462 concepts declare `statement` on none of
    themselves — the whole gate arrives from 18 `section_defaults` entries through `inherits` —
    and the set's items are stored ALREADY RESOLVED (`project_concept` consumed concepts loaded
    with `resolve=True`). So the obvious build is `resolve=False`, and it is WRONG. Measured on
    the shipped set, `resolve=True` differs from `resolve=False` on exactly one field:
    `note_use_rationale`, on 394 of 462 concepts, where `resolve=False` loses it and `resolve=True`
    agrees with the rulebook. Every other projected field is identical under both, because the
    items carry their resolved values and `resolve_inherits` lets a declaration win over a section
    default — so the fold is idempotent everywhere it has anything to fold onto.

    THE REASON FOR THAT ONE FIELD is worth writing down, because it looks like a bug in the
    projection and is not. `note_use_rationale` is in `SAME`, so the projection writes it to the
    seed (394 items carry it on disk), but `LineItemDef` declares NO SUCH FIELD, and pydantic's
    default `extra="ignore"` drops it at load. It is the only member of `SAME` with no home on
    `LineItemDef` — `UNHOMED` below is that measurement, kept as data so a second one cannot
    arrive unnoticed. The set's `section_defaults` still declare it, so folding the section layer
    is the ONLY place it can come back, and it is not cosmetic: `services.ontology_xlsx` publishes
    it as the reviewer workbook's "Note-use rationale" column, which would otherwise empty out on
    394 rows.

    So: `resolve=True`, and `tests/test_working_view_parity.py` pins both halves of that — the
    394-concept difference and the fact that nothing else moves.

 2. LOSING `residual_policy.model_fields_set`. `stages/residual._declared` asks
    `name in policy.model_fields_set` to tell a term the concept AUTHORED from one it inherits
    from `residual_framework`. A full `model_dump()` writes all seven terms at their defaults, so
    every term reads as authored and the framework — one definition meant to govern all of the
    residual buckets — governs none of them. `_jsonable` dumps with `exclude_unset` for exactly
    this reason, which is the second reason it is imported rather than reimplemented here.
    Measured: 11 of 11 residual concepts round-trip their `model_fields_set` concept-for-concept.
    (Eleven, not thirteen — the shipped rulebook has 11 `exclusive_residual` concepts and 11
    policies. The thirteen are the `sub__*` parts dropped above, a different set entirely.)

THE ONE INTENDED DIVERGENCE from the rulebook, and it is data winning over a table in code:
`is_pl__minority_interests_pl`. The rulebook declares `section_scope: ['is_pl']` and
`mapping._KEY_SECTION_OVERRIDES` corrects it to `profit_attributable_to` at match time; the
projection writes that correction INTO the item, so the working view declares
`['profit_attributable_to']` and the override re-applies to the same value — idempotent, pinned by
the parity test. Without the correction the ported matcher accepted 15 captions the rulebook
refuses.
"""
from __future__ import annotations

import functools
import hashlib
import json

from app.schemas.line_items import LineItemDef, LineItemSet
from app.schemas.loader import load_ontology
from app.schemas.ontology import OntologyDefinition
# Imported, never retyped: see the module docstring. `_jsonable` is private to the projection but
# it is the half that carries `exclude_unset` — copying it would copy hazard 2 into a second place
# where it could be "tidied" away.
from app.services.ontology_projection import RENAMED, SAME, _jsonable

# NO INVERTED COPY OF `RENAMED`. It is authored as ontology field -> line-item field, so inverting
# it is a matter of reading each pair right-to-left at the one place it is used (`_concept_of`) —
# `{v: k for k, v in RENAMED.items()}` would be a second table to keep in step for no gain.

# Members of `SAME` that `LineItemDef` does not declare, and which therefore CANNOT round-trip
# through the set — measured as exactly one, `note_use_rationale` (hazard 1 above). Computed rather
# than written down so adding a field to `SAME` without a home on `LineItemDef` shows up here and
# in the parity test instead of quietly vanishing from 394 concepts.
UNHOMED: tuple[str, ...] = tuple(f for f in SAME if f not in LineItemDef.model_fields)

# What `SAME` reduces to on the way back. Order follows `SAME` so a diff against the projection
# reads straight across.
CARRIED: tuple[str, ...] = tuple(f for f in SAME if f in LineItemDef.model_fields)


def _concept_of(item: LineItemDef) -> dict:
    """One `LineItemDef` as an `OntologyMapping` dict — the inverse of `project_concept`.

    ABSENT STAYS ABSENT. `project_concept` skips a field the concept did not declare so the
    round-tripped `model_fields_set` stays faithful; this asks `field in item.model_fields_set`
    for the same reason, and it is not the same question as "is the value empty". A set that
    configures `never_sweep: []` has DECLARED that nothing is protected, and that declaration must
    survive as a declaration — a configured empty value means nothing, never "fall back to a
    built-in default".

    The three `RENAMED` fields are written unconditionally, mirroring `project_concept`, which
    writes them unconditionally too: `canonical_key` is the identity and the two criteria lists are
    the prose the LLM is shown.
    """
    out: dict = {}
    for ont_field, li_field in RENAMED.items():          # read right-to-left: key -> canonical_key
        out[ont_field] = _jsonable(getattr(item, li_field))
    for field in CARRIED:
        if field in item.model_fields_set:
            out[field] = _jsonable(getattr(item, field))
    # `type` UNCONDITIONALLY, AND UNDER ITS OWN NAME. It is not in `SAME` because the two models
    # spell it differently — `LineItemDef.type` against `OntologyMapping.item_type` — and it is
    # not in `RENAMED` because that map is read in both directions and `project_concept` derives
    # the type from `extraction_mode` instead, for a rulebook concept that has never had one.
    #
    # WRITTEN EVEN WHEN THE ITEM DID NOT DECLARE IT, unlike everything in `CARRIED`: the matcher
    # reads this to decide whether the model is offered the concept at all (`_llm_withheld`), and
    # `type` has a real default rather than an absent state — an undeclared `type` IS `extracted`,
    # so passing the resolved value through says exactly what the configuration means.
    out["item_type"] = str(item.type)
    return out


def _definition_of(st: LineItemSet) -> dict:
    """The whole working view as a plain dict, ready for `loader.load_ontology`.

    Built as a dict and validated through the real loader rather than by constructing
    `OntologyDefinition` field by field, because the loader is where the section fold, the
    self-vetoing-alias refusal and the derive/disabled diagnostic live. A view assembled around it
    would be a rulebook nothing had checked.

    Every framework block the set carries is copied. The four the shipped set declares
    (`normalisation`, `binding`, `global_rules`, `scope_selection`) are the ones the pipeline reads
    on every run; the other four (`decomposition_rules`, `netting_rules`, `worked_examples`,
    `validation`) are declared on `LineItemSet` precisely so this view is COMPLETE — an undeclared
    block is the set's own empty declaration, not a hole this function goes somewhere else to
    fill. `metadata` is NOT carried: the two models describe different things (a set's name and
    changelog against a rulebook's), and nothing in the pipeline reads the rulebook's.
    """
    return {
        # A section layer is present, so this is what the rulebook schema calls a v2 definition.
        # Nothing branches on the number today; it is set truthfully rather than left at 1 so a
        # reader of the view is not told the section layer is absent while it is being folded.
        "schema_version": 2 if st.section_defaults else 1,
        "ontology_key": st.line_items_key,
        "target_template_key": st.target_template_key,
        "target_template_version": st.target_template_version,
        "locale": st.locale,
        "supported_locales": list(st.supported_locales),
        "number_format_by_locale": _jsonable(st.number_format_by_locale),
        "section_defaults": _jsonable(st.section_defaults),
        "residual_framework": _jsonable(st.residual_framework),
        "normalisation": _jsonable(st.normalisation),
        "binding": _jsonable(st.binding),
        # The master prompt, so `mapping._build_system` can append it. Carried here rather than
        # read off the set directly because the matcher only ever sees this object.
        "prompt": st.prompt,
        "global_rules": _jsonable(st.global_rules),
        "scope_selection": _jsonable(st.scope_selection),
        "decomposition_rules": _jsonable(st.decomposition_rules),
        "netting_rules": _jsonable(st.netting_rules),
        "worked_examples": _jsonable(st.worked_examples),
        "validation": _jsonable(st.validation),
        # EVERY DEFINITION IS A CONCEPT. THERE IS NO "SUB-LINE ITEM" KIND.
        #
        # This used to read `if i.namespace == "template"`, on the reasoning that the off-template
        # `sub__*` entries are "parts OF a line, never template lines". That confused two
        # independent things:
        #
        #   * WHERE A FIGURE IS PUBLISHED — which output column it lands in, and whether the config
        #     screen lets an author edit it. That is what `namespace` says, and it is unchanged:
        #     `routes/templates.py` and `routes/line_items.py` still filter on it, correctly, and an
        #     off-template key still has no output column.
        #   * WHETHER THE ENGINE MAY RECOGNISE IT — which tiers can bind a caption to it and
        #     whether the model is offered it. That has nothing to do with publication, and making
        #     it depend on publication is what left 77 real line items unrecognisable.
        #
        # THE COST OF THE OLD READING, measured. The 77 were absent from `_by_key`, so: no tier
        # could bind a caption to one; `_concept_payload` could not offer one; and naming one was
        # refused as a key that names nothing, which needed a special `sub_item_keys` channel and an
        # `identified_for` back-door to work around. Seven of the eight focus concepts are `derived`
        # parents the model is (rightly) not offered, and their parts were the only thing it COULD
        # usefully have answered with — so a live 45-call run changed none of the eight figures.
        #
        # NOTHING NEEDS AUTHORING TO MAKE THIS WORK, which is the sign the split was accidental
        # rather than designed: the resolve step already gives every one of them a statement
        # (`notes`), a `section_scope` (`['notes']`), a competitive `match_priority` (80 against a
        # median of 81), `extraction_mode: extract` and a real label. And none of the 77 declares an
        # alias, so admitting them cannot take a face row off a template concept by string
        # evidence — there are zero alias collisions between the two groups.
        "mappings": [_concept_of(i) for i in st.items],
    }


@functools.lru_cache(maxsize=2)
def _validated(digest: str, payload: str) -> OntologyDefinition:
    """`payload` validated as a rulebook, cached on its digest.

    `resolve=True` — see hazard 1 in the module docstring. The items are stored already resolved,
    so the fold is idempotent on everything except the one `SAME` field `LineItemDef` cannot hold,
    which the section layer is the only place to recover.

    Both arguments are the cache key by construction (`digest` is the digest OF `payload`), which
    is redundant and deliberate: the digest is what makes the key cheap to compare and the payload
    is what has to be validated. `maxsize=2` because a process serves one shipped set plus, at
    most, one set being previewed.

    WHAT THE CACHE ACTUALLY SAVES, measured on the shipped set (784 KB of payload): validation is
    26.3 ms of the 41 ms build, and it is what this skips. The 7.2 ms of `_definition_of`, 6.2 ms
    of `json.dumps` and 1.3 ms of sha256 are paid on every call, because `LineItemSet` is
    unhashable and the payload is the only thing that can be a key. That is the right trade at the
    call frequency — once per run, and on several request paths — and it is not a reason to key on
    the set's identity instead: two loads of the same file are different objects.

    THE RESULT IS SHARED. Callers must treat it as read-only; the matcher does.
    """
    return load_ontology(json.loads(payload), resolve=True)


def build_working_view(st: LineItemSet) -> OntologyDefinition:
    """The matcher's view of `st` — 462 concepts from the shipped set's 475 definitions.

    Keyed on the canonical JSON of the view rather than on the set object, because `LineItemSet` is
    a pydantic model and unhashable, and because two sets that project to the same view should hit
    the same cache entry.
    """
    payload = json.dumps(_definition_of(st), ensure_ascii=False, sort_keys=True)
    return _validated(hashlib.sha256(payload.encode("utf-8")).hexdigest(), payload)
