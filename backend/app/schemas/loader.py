"""Load & validate template + ontology definitions.

The key cross-check: every ``canonical_key`` referenced by the ontology (in mappings
and decomposition rules) must resolve against the template, and every rollup/identity
must reference node_ids that exist. This is enforced on upload so a bad
template/ontology pairing is rejected with a clear list of offending keys.

A second check applies on UPLOAD ONLY: a key the schema does not declare is reported
(:func:`unknown_keys`) instead of being silently dropped. See that function for why the
strictness cannot live on the models themselves.

A ``schema_version: 2`` ontology also carries a SECTION LAYER — properties authored once per
section in ``section_defaults`` and claimed by a concept through ``inherits``.
:func:`resolve_inherits` folds it in, and it runs BEFORE validation: see that function for why
it cannot be a model validator.
"""
from __future__ import annotations

import copy
import logging
from typing import Any

from pydantic import BaseModel

from app.schemas.ontology import OntologyDefinition
from app.schemas.template import TemplateDefinition

_LOG = logging.getLogger(__name__)


class ValidationError(BaseModel):
    location: str
    message: str


class UnknownInheritsError(ValueError):
    """A concept's ``inherits`` names a ``section_defaults`` entry that does not exist."""


def load_template(data: dict) -> TemplateDefinition:
    return TemplateDefinition.model_validate(data)


def load_ontology(data: dict, *, resolve: bool = False) -> OntologyDefinition:
    """Validate an ontology definition.

    ``resolve=True`` folds the v2 section layer in first (:func:`resolve_inherits`) and is how a
    caller that intends to MATCH with the rulebook should load it. It is opt-in, and the default
    stays off, because the read path has other jobs — listing rulebooks, rendering the ontology
    editor, reading netting rules — where a concept's own declaration is the thing being shown and
    an inherited value silently merged into it would be wrong. It also keeps a stored definition
    with a broken ``inherits`` from turning those pages into a 500.
    """
    if resolve:
        data = resolve_inherits(data)
    definition = OntologyDefinition.model_validate(data)
    _refuse_self_vetoing_concepts(definition)
    _warn_derive_and_disabled(definition)
    return definition


def _warn_derive_and_disabled(definition: OntologyDefinition) -> None:
    """Report a concept declaring BOTH ``extraction_mode: derive`` and ``alias_matching: disabled``.

    Each lock alone has a meaning and a mechanism. ``alias_matching: disabled`` says the concept is a
    swept residual and keeps its aliases out of the match index. ``extraction_mode: derive`` says the
    framework computes the concept, and it does TWO things: it keeps the concept out of every tier and
    payload, and — this is the part that gets lost — it files the concept's aliases in
    ``_computed_alias_by_key`` so that a printed caption naming it is REFUSED rather than handed to the
    nearest neighbouring subtotal (``services.mapping._computed_claim``).

    Declared together, the second half is silently gone. ``OntologyMatcher.__init__`` tests
    ``_locked`` first and ``continue``s, so the ``_computed_only`` branch that would have indexed the
    aliases never runs: the concept is hidden, the refusal is not armed, and a face row printing its
    caption falls through to whatever the fuzzy tier likes next — with the statement still tying,
    because the neighbour it lands on differs from the real concept by exactly the missing lines.

    THE BEHAVIOUR DECISION HAS NOW BEEN MADE, and ``derive`` wins: the two concepts that carried
    both locks on ``output_csv_hk_ontology.json`` — ``is_pl__deprec_and_impairment_oper_exp`` and
    ``bs_nca__secur_and_other_fincl_assets_ltp`` — are now ``alias_matching: enabled``. The lock was
    always redundant on them (``extraction_mode: derive`` alone keeps a concept out of every tier and
    every payload — ``services.mapping`` puts both values into ``_unmatchable``, and
    ``services.line_item_matching`` reads the two as an OR), so the ONLY thing it did was suppress the
    refusal. Before: ``_computed_alias_by_key`` held 1 entry out of 462 concepts and
    ``usage["computed_refused"]`` was structurally pinned at 0. After: 3 of 3 ``derive`` concepts are
    indexed and the refusal can fire. Both are also ``extraction.llm_focus_keys``, so with the LLM on
    theirs are exactly the captions being forwarded — and the model is not shown the concept either,
    which is why the refusal sits above the semantic tier as well as the deterministic one.

    The order mattered and is worth recording: unlocking them is only survivable because
    ``_computed_claim`` is scope-gated on ``statement`` and ``section_scope``. Unlocked without that
    gate, ``is_pl__deprec_and_impairment_oper_exp`` claims the verbatim cash-flow caption
    "Depreciation of property, plant and equipment" at 1.0, beating the 0.54 the row's own concept
    scores, and 8 correct mappings (7 cash-flow depreciation rows, 1 balance-sheet CIP row) come back
    None.

    So this reports the conjunction at ERROR and still does not raise. Two reasons, both unchanged
    from when it was a warning. This loader runs on every read of every stored definition — listing
    rulebooks, rendering the editor, the extraction worker — and callers there do not all expect
    failure (see :func:`unknown_keys` for the same argument in full); a stored rulebook that has the
    conjunction is precisely the one an editor needs to be able to OPEN. And the shipped file is
    machine-generated: ``alias_matching`` is read straight off the "Alias matching" column of
    ``_exports/*.xlsx`` by ``scripts/build_output_csv_template.py``, so the next regeneration can
    reinstate "disabled" from the workbook. A load gate would then turn that regeneration into a
    startup failure in ``sample.reference`` (``ReferenceSeedError``, i.e. the app does not boot) —
    strictly worse than a named, greppable diagnostic on a file that still loads. Whoever hits this
    message fixes the workbook column, not the JSON.
    """
    both = [m.canonical_key for m in definition.mappings
            if m.extraction_mode == "derive" and m.alias_matching == "disabled"]
    if both:
        _LOG.error(
            "ontology %s: %d concept(s) declare extraction_mode 'derive' AND alias_matching "
            "'disabled'; the residual lock is tested first, so their aliases are never indexed for "
            "the computed-claim refusal and a printed caption naming one is filed on a neighbouring "
            "concept instead of being refused (usage['computed_refused'] cannot leave 0 for them): "
            "%s. The lock is REDUNDANT on a 'derive' concept — that mode already excludes it from "
            "every tier and payload — so drop the 'disabled' and leave 'derive' in place. On the "
            "shipped file that means the \"Alias matching\" column of _exports/*.xlsx, which "
            "scripts/build_output_csv_template.py copies verbatim; a JSON-only edit is reinstated on "
            "the next regeneration",
            definition.ontology_key or "?", len(both), ", ".join(both))


def _refuse_self_vetoing_concepts(definition: OntologyDefinition) -> None:
    """Refuse a concept whose own ``exclude_hints`` match its own alias.

    An exclusion outranks an alias at match time, and it has to — the field exists so an editor
    looking at a mis-mapping can add one line and have it stop. The consequence is that a hint broad
    enough to match the concept's OWN alias deletes that alias with no signal whatever: the caption
    resolves to nothing, the alias sits in the file looking like it should have worked, and there is
    nowhere to look for the reason.

    Eight aliases in the shipped rulebook were dying that way when this check was written, and every
    one was a NEGATION killed by the thing it negates — "Other revenue and gains" by ``revenue``,
    "Taxes other than income tax" by ``income tax``, "Impairment losses on non-financial assets" by
    ``financial asset``, "息税前利润" by ``税前``. The first cost a real HK filing its income line,
    which then failed the residual router's sign test and reached an analyst as unmapped, with the
    alias it should have matched sitting three lines above the hint that killed it.

    So the contradiction is an AUTHORING ERROR and is refused at the door, in the same spirit as an
    unknown canonical key. The fix is always a narrower hint — anchoring ``revenue`` to ``^revenue``
    keeps the top-line caption out without eating "other revenue and gains" — and the message names
    the alias, the hint and the concept so it can be made without a search.
    """
    import re as _re

    bad: list[str] = []
    for m in definition.mappings:
        hints = m.exclude_hints or []
        if not hints:
            continue
        aliases = list(m.aliases or [])
        for per_locale in (m.aliases_i18n or {}).values():
            aliases.extend(per_locale or [])
        for alias in dict.fromkeys(a for a in aliases if a):
            for hint in hints:
                try:
                    if _re.search(hint, alias.lower(), _re.IGNORECASE):
                        bad.append(f"{m.canonical_key}: alias {alias!r} is excluded by its own "
                                   f"exclude_hint {hint!r}")
                        break
                except _re.error:
                    # A malformed hint is a different fault and is reported by whoever compiles it;
                    # it cannot veto anything, so it cannot cause this one.
                    continue
    if bad:
        raise ValueError(
            "ontology declares aliases its own exclude_hints delete, which would fail silently at "
            "match time — narrow the hint:\n  " + "\n  ".join(bad))


def resolve_inherits(data: dict) -> dict:
    """Fold each ``section_defaults`` entry into every concept naming it via ``inherits``.

    A key declared ON THE CONCEPT always wins; the section supplies only what the concept is
    silent about. Returns a new definition dict — the input is left alone, because callers hand
    this the ``definition`` of a live DB row.

    Deliberately NOT a pydantic validator, for two reasons. Once validated, a concept that
    inherited ``match_priority`` is indistinguishable from one that declared it and from one that
    declared nothing at all (the model's default filled the field), so "declared wins" can only be
    decided on the raw dict — and the same is true of every optional field the section layer
    touches. And resolving before validation means the RESOLVED shape is what gets validated, so
    a section default with a bad value fails at the door rather than reaching a concept.

    Without this fold the section layer is inert: ``section_scope``, ``statement``,
    ``temporality`` and ``face_only`` are authored on no concept at all, so the section-first
    binding order the rulebook specifies would have nothing to bind against.
    """
    sections = data.get("section_defaults")
    mappings = data.get("mappings")
    # A v1 rulebook has no section layer; leaving it untouched is the whole point of the opt-in.
    if not isinstance(sections, dict) or not isinstance(mappings, list):
        return data

    resolved: list[Any] = []
    missing: list[str] = []
    for entry in mappings:
        if not isinstance(entry, dict) or "inherits" not in entry:
            resolved.append(entry)
            continue
        name = entry["inherits"]
        base = sections.get(name) if isinstance(name, str) else None
        if not isinstance(base, dict):
            # A silent no-op here is the failure this whole function exists to prevent, one level
            # up: the concept would validate, load, and quietly carry none of its section's
            # properties — no section_scope, so the binding order can never place it.
            # Collected rather than raised on the spot so one pass names every offender.
            missing.append(f"{entry.get('canonical_key', '?')} inherits {name!r}")
            continue
        # Deep-copied so the 12 concepts sharing a section do not share its `include` list.
        resolved.append({**copy.deepcopy(base), **entry})

    if missing:
        known = ", ".join(sorted(sections)) or "(none)"
        raise UnknownInheritsError(
            f"{len(missing)} concept(s) inherit a section_defaults entry that does not exist: "
            + "; ".join(missing[:10])
            + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else "")
            + f". Declared sections: {known}"
        )
    return {**data, "mappings": resolved}


def unknown_keys(data: dict, model: BaseModel, *, limit: int = 40) -> list[str]:
    """Dotted paths of keys present in ``data`` that the schema does not declare.

    Pydantic's default is ``extra='ignore'``, so an undeclared key — a typo'd field name, or an
    ``inherits`` borrowed from some other tool's format — is dropped in silence: the definition
    publishes, reports success, and simply does not contain the thing that was authored. The
    author's next clue is an extraction that behaves as though the edit was never made.

    This is deliberately NOT ``model_config = ConfigDict(extra='forbid')`` on the models. The same
    two loaders read every definition already stored in the database, and those call sites do not
    all expect failure: ``languages.py`` has no handler at all (a 500), ``templates.py`` swallows
    the error and serves a silently emptied ontology, and the extraction worker swallows it into
    ``template = None``, which marks the run SUCCEEDED with every structural check quietly gone.
    Rejecting an undeclared key belongs at the door, where there is a request to fail and a person
    to tell; on the read path it would turn one bad row into an outage.

    Compares the input against a round-trip dump rather than introspecting the schema, so nested
    models, lists of models and ``dict[str, Model]`` fields all follow without special cases.
    ``limit`` caps the report — a definition authored against a wholly different format should say
    so in a sentence, not in nine hundred paths.
    """
    found: list[str] = []

    def walk(raw: Any, known: Any, path: str) -> None:
        if len(found) >= limit:
            return
        if isinstance(raw, dict) and isinstance(known, dict):
            for k, v in raw.items():
                where = f"{path}.{k}" if path else str(k)
                if k not in known:
                    found.append(where)
                    continue
                walk(v, known[k], where)
        elif isinstance(raw, list) and isinstance(known, list):
            # A list the model parsed is element-for-element with its input; a list it coerced
            # from something else is a type error the validator has already reported.
            for i, (rv, kv) in enumerate(zip(raw, known)):
                walk(rv, kv, f"{path}[{i}]")

    walk(data, model.model_dump(), "")
    return found


def validate_template(template: TemplateDefinition) -> list[ValidationError]:
    errors: list[ValidationError] = []
    node_ids = template.node_ids()
    for st in template.statements:
        for ident in st.identities:
            for term in [ident.lhs, *ident.rhs.children]:
                if term not in node_ids and term not in template.all_canonical_keys():
                    errors.append(ValidationError(
                        location=f"identity:{ident.id}",
                        message=f"references unknown node/key {term!r}",
                    ))
        for node in template._walk(st.sections):
            if node.rollup:
                for child in node.rollup.children:
                    if child not in node_ids:
                        errors.append(ValidationError(
                            location=f"rollup:{node.node_id}",
                            message=f"references unknown node_id {child!r}",
                        ))
    errors += _validate_kpis(template)
    return errors


def _validate_kpis(template: TemplateDefinition) -> list[ValidationError]:
    """The KPI block's references and its dependency graph, refused AT THE DOOR.

    Two authoring mistakes are caught here because neither is detectable later without inventing a
    number or reporting "unavailable" forever:

    * a term naming a key the template does not declare — the term can never carry a figure, so the
      KPI it belongs to reads as "inputs not extracted" on every filing, which is indistinguishable
      from thin extraction and is really a typo;
    * a cycle among the intermediates (net debt from capital employed from net debt) — nothing can
      be evaluated first, so every ratio built on either is permanently unavailable.

    Both would otherwise be silent: a KPI that never computes looks exactly like a document that
    never reported its inputs. Failing on upload is the only place there is an author to tell.
    """
    errors: list[ValidationError] = []
    kpis = template.kpis
    known = template.all_canonical_keys()
    intermediates = {i.key: i for i in kpis.intermediates}

    def check_terms(location: str, terms) -> None:
        for term in terms:
            for key in [term.key, *term.fallback_keys]:
                if key not in known and key not in intermediates:
                    errors.append(ValidationError(
                        location=location,
                        message=f"references {key!r}, which is neither a canonical_key in this "
                                f"template nor one of its kpi intermediates",
                    ))

    for inter in kpis.intermediates:
        check_terms(f"kpi_intermediate:{inter.key}", inter.terms)
    for ratio in kpis.ratios:
        check_terms(f"kpi:{ratio.key}", ratio.numerator)
        check_terms(f"kpi:{ratio.key}", ratio.denominator)

    # Depth-first cycle detection over intermediate → intermediate references only; a term naming a
    # canonical key is a leaf (a printed figure depends on nothing).
    state: dict[str, int] = {}                       # 1 = on the current path, 2 = settled
    cyclic: set[str] = set()

    def visit(key: str) -> None:
        if state.get(key) == 2:
            return
        if state.get(key) == 1:
            cyclic.add(key)
            return
        state[key] = 1
        for term in intermediates[key].terms:
            for cand in [term.key, *term.fallback_keys]:
                if cand in intermediates:
                    visit(cand)
        state[key] = 2

    for key in intermediates:
        visit(key)
    for key in sorted(cyclic):
        errors.append(ValidationError(
            location=f"kpi_intermediate:{key}",
            message="is part of a cycle of intermediates, so it can never be evaluated",
        ))
    return errors


def validate_ontology_against_template(
    ontology: OntologyDefinition, template: TemplateDefinition
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    template_keys = template.all_canonical_keys()

    for m in ontology.mappings:
        if m.canonical_key not in template_keys:
            errors.append(ValidationError(
                location=f"mapping:{m.canonical_key}",
                message="canonical_key does not exist in the target template",
            ))
    # An ``analyst_bucket`` naming no bucket would silently lose its rows: ``bucket_of`` refuses it
    # and files them in Others, and nothing would say why. Imported here rather than at module scope
    # so the schema layer keeps its module-level import graph free of the service layer.
    from app.services.buckets import BUCKET_KEYS

    for m in ontology.mappings:
        if m.analyst_bucket and m.analyst_bucket not in BUCKET_KEYS:
            errors.append(ValidationError(
                location=f"mapping:{m.canonical_key}",
                message=(f"analyst_bucket {m.analyst_bucket!r} is not an analyst section; "
                         f"expected one of {', '.join(BUCKET_KEYS)}"),
            ))
    # ``children_if_decomposed`` must name CONCEPTS OF THIS OWN RULEBOOK, because every reader
    # compares an entry against a key that was mapped: ``map_ontology._pairs_to_keep_apart`` feeds it
    # to ``_enforce_containment`` (`present = [c for c in components if c in printed]`) and to
    # ``_same_section_decompositions`` (which looks the child's ``section_scope`` up by key), and
    # ``services.mapping`` ships the list to the model as the concept's child keys. An entry that is
    # not a key therefore matches nothing, and the containment it declares is silently unenforced —
    # which is exactly how the GENERATED output_csv_hk rulebook shipped: 30 of its 31 carriers
    # packed every child into one pipe-joined string ("bs_nca__land | bs_nca__construction_in_progress"),
    # because scripts/build_output_csv_template.py split a pipe-separated workbook column on commas,
    # so the whole file produced ONE same-section decomposition instead of 25 and the LLM payload
    # carried the joined string verbatim. Refused on upload because nothing downstream can tell a
    # child key that does not exist from a component the filing simply did not print, and both read
    # as a total that ties. The pipe case is named separately: it is a splitting mistake in whatever
    # produced the file, not a typo in one concept name, and saying so is what points at the fix.
    ontology_keys = {m.canonical_key for m in ontology.mappings}
    for m in ontology.mappings:
        for child in m.children_if_decomposed:
            if "|" in child:
                errors.append(ValidationError(
                    location=f"mapping:{m.canonical_key}",
                    message=(f"children_if_decomposed entry {child!r} contains '|', so it names no "
                             f"canonical_key — list each child separately"),
                ))
            elif child not in ontology_keys:
                errors.append(ValidationError(
                    location=f"mapping:{m.canonical_key}",
                    message=(f"children_if_decomposed names {child!r}, which is not a canonical_key "
                             f"in this ontology, so the containment it declares is unenforceable"),
                ))
    # ``global_rules.mutually_exclusive_groups`` is the SAME requirement one level up, and it shipped
    # broken for the same reason. The group is the global half of the containment
    # ``children_if_decomposed`` states per concept — ``map_ontology._pairs_to_keep_apart`` reads
    # both into one list — so both halves are resolved by key and both are silently inert when a key
    # is not one. The generated output_csv_hk rulebook carried all four of the sibling rulebook's
    # groups verbatim in the WRONG KEY SPACE: measured, 0 of 4 aggregates and only 2 of 13 components
    # existed among its 462 canonical keys, all 17 existed among the sibling's 183, and no
    # output_csv_hk key carries a ``pl_`` prefix at all. ``_enforce_containment`` finds no row filed
    # on an aggregate that is not a concept and skips the whole group, so nothing double-counted and
    # nothing complained; ``equity_reserves`` resolved 2 of its 9 members, which is worse than
    # resolving none — on a screen or a diff it reads like working configuration.
    #
    # Refused rather than tolerated because a group is a PROHIBITION ("never load both"), and a
    # prohibition that addresses nothing cannot be told apart from a filing that never triggered it.
    # Every member is checked, not just the aggregate: a group missing one component permits exactly
    # the double count it was written to prevent, for that component alone.
    for group in ontology.global_rules.mutually_exclusive_groups:
        gid = group.id or "mutually_exclusive_group"
        for role, key in ([("aggregate", group.aggregate)]
                          + [("component", c) for c in group.components]):
            if key and key not in ontology_keys:
                errors.append(ValidationError(
                    location=f"mutually_exclusive_group:{gid}",
                    message=(f"{role} {key!r} is not a canonical_key in this ontology, so the "
                             f"exclusivity it declares can never be enforced"),
                ))
    for rule in ontology.decomposition_rules:
        if rule.face_key not in template_keys:
            errors.append(ValidationError(
                location=f"decomposition:{rule.id}",
                message=f"face_key {rule.face_key!r} does not exist in the template",
            ))
    return errors


def validate_pair(
    template: TemplateDefinition, ontology: OntologyDefinition
) -> list[ValidationError]:
    return (
        validate_template(template)
        + validate_ontology_against_template(ontology, template)
    )
