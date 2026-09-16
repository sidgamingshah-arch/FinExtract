"""The configuration API. There is exactly one, and this is it.

Line items are the single configuration engine, so this module carries the WHOLE of what a user or
an API consumer can do to the thing a run maps against: read the version in force, list the stored
versions, download one, read the schema it must satisfy, publish a new one, and EDIT one item
field by field. ``api/routes/ontologies.py`` stood beside this file and is DELETED — with it went
the skeleton download, the workbook download and upload, and the netting-rules editor (see
"WHAT IS GONE" below). Nothing here says ontology, because there is no longer a second
configuration surface to tell apart from this one.

WHAT THIS MODULE USED TO BE, so nobody reinstates it. It was READ-ONLY and it served the shipped
SEED FILE off disk, under a docstring saying nothing downstream read these definitions. Both halves
of that are now defects rather than caveats: a run reads a ``line_item_versions`` ROW
(``extraction_runs.line_item_version_id`` pins the exact one), so a screen rendering the file while
runs read the database is precisely the drift this API exists to close. Every read here goes through
``services.config_select.select_for_template`` — the one function that decides which stored version
is in force — and every write publishes a NEW version rather than mutating one, because a past run
references the version it used and must stay explicable.

THE THREE GATES ON PUBLISH are ported from the deleted ontology route and are the reason a skeleton
upload cannot become the configuration a real extraction runs on:

  * every key must resolve against the TARGET TEMPLATE (``_validate_against_target_template``),
  * a key the schema does not declare is REFUSED rather than dropped in silence, and
  * a set that recognises NOTHING is refused at the door.

The third one replaced five ranking rules in ``config_select``: a configuration that recognises
nothing is not a lower-priority configuration, it is not a configuration, and the door is the only
place an author is present to be told why.

THE ITEM EDIT ACCEPTS EVERY AUTHORABLE FIELD, which it did not always. ``ItemEdit`` carried
thirteen of ``LineItemDef``'s forty-odd, and the Line Items screen was read-only on the
justification that these definitions merely DESCRIBED derivations five services computed. That
justification expired — the matcher is built from this set and a run pins the version it used — so
a field that is authorable in the schema and unreachable through the edit body is a control the
product claims to have and does not. What is deliberately still not accepted is named in
``ItemEdit``'s own comment and served to the screen as ``vocab.not_editable``, because "absent
from the form" and "read-only for a reason" look identical to a reader and only one is a decision.

WHAT IS GONE with the ontology route, deliberately, each for its own reason. The SKELETON download
(a stub-per-template-key .json) and the WORKBOOK download/upload were authoring aids for a
hand-written rulebook; the configuration is generated and edited item by item now, and the workbook
was the widest door in the product — 3,000 lines of parser for a shape nothing else validates. The
NETTING-RULES editor governed nothing: the shipped configuration declares 0 netting rules (measured
on both shipped files), so it was an editor for an empty list. Re-add any of them only alongside the
code and the data that read them.
"""
from __future__ import annotations

import copy
import enum
import json
import re
import types
import typing
from typing import Any, Literal, get_args, get_origin

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db
from app.core.models.enums import SignConvention, StatementType

# THE VOCABULARIES ARE IMPORTED, NEVER RESTATED — the rule `schemas/line_items.py` opens with, and
# the reason `ItemEdit` below is typed on these aliases rather than on `str`. The first version of
# that schema declared its own `SearchScope` containing `income_statement`, a token nothing else in
# the backend spells, and read as a gate it would have refused every P&L item on every page. An
# edit body that re-declared the same closed sets would put that second spelling back, one door
# further out, where it is even harder to see.
from app.schemas.line_items import (
    AliasMatching,
    CascadeRung,
    ExtractionMode,
    LineItemDef,
    LineItemSet,
    LineItemType,
    NoteSelection, OutputStructure, Route,
    MappingVocabulary,
    Namespace,
    NoteSource,
    NoteUse,
    ResidualPolicy,
    Rollup,
    SearchScope,
    SectionDefaults,
    Side,
    SignExpectation,
    SignRule,
    Temporality,
    Term,
    TermsOp,
    UnitOfAccount,
    UnknownInheritsError,
    ValueScope,
    load_line_item_set,
)
from app.schemas.loader import load_template, unknown_keys
from app.security.rbac import (Permission, Principal, current_principal,
                               require)
from app.services import prose_grammar
from app.services.config_select import select_for_template
from app.services.line_items import build

router = APIRouter(prefix="/line-items", tags=["line-items"])

# The version a NEW set is authored at — read off the model so it cannot drift from the one the
# gate validates with. Both shipped files carry 1 (measured), which is also ``LineItemSet``'s
# default, so an author who omits the field lands on the same schema the gate applies.
CURRENT_SCHEMA_VERSION = LineItemSet.model_fields["schema_version"].default

# Every route here is gated on the SAME permission, the one the merge left: authoring the
# configuration is an admin job end to end, and a definition an analyst can read but not publish is
# an afternoon wasted. Named once so a new route cannot be added under a weaker gate by accident.
_GATE = Depends(require(Permission.CONFIG_LINE_ITEMS))


# ── which configuration, and can it still be read ─────────────────────────────────────────────

# Whether a STORED set can still be READ by today's schema, remembered per row.
#
# WHY THIS EXISTS AT ALL. The retired ontology store had 41 of 44 rows that no longer loaded, and
# nothing in the app had noticed: the picker served every row straight from the raw dict, start-up
# validated only the DISK seed, and a run could PIN a definition that cannot be loaded at all. A
# ``line_item_versions`` row written by an older schema fails the same way, so the same probe
# travels with the row.
#
# ONE FUNCTION, THREE READERS — the picker's ``loads`` field below, the boot-time warning
# (``main``) and the extraction refusal (``routes.extractions``). That is why it is public and why
# it lives here rather than being re-implemented at each site: those three could never disagree
# about which rows are broken, and the property is worth more than the tidiness of a local helper.
#
# CACHED because the picker is POLLED (frontend/src/api/queries.ts) and loading a 475-item set costs
# ~10ms; keyed on (id, created_at) and NOT on content, because ``line_item_versions`` is APPEND-ONLY
# — every publishing path here and in ``sample.reference`` INSERTs a new row with a new uuid and
# nothing UPDATEs ``definition``, so a repaired set is a row this cache has never seen.
_LOADABILITY: dict[tuple[str, object], str | None] = {}


def _first_load_error(exc: Exception) -> str:
    """One line naming what stops a stored definition loading, out of possibly hundreds.

    ``str(exc)`` on a schema-drift failure is hundreds of pydantic errors across thousands of
    lines, which is not a log line and not something to put on the wire. The first error plus a
    count is enough to recognise the fault and go looking; the whole list is a
    ``GET /line-items/versions/{id}`` and a re-load away.
    """
    errors = getattr(exc, "errors", None)
    found = []
    if callable(errors):
        try:
            found = list(errors())
        except Exception:  # noqa: BLE001 — not a pydantic error after all; fall through to str()
            found = []
    if found:
        first = found[0]
        where = ".".join(str(p) for p in (first.get("loc") or ()))
        more = f" (and {len(found) - 1} more)" if len(found) > 1 else ""
        return f"{where}: {first.get('msg', '')}{more}".lstrip(": ")
    return str(exc).strip().splitlines()[0]


def probe_line_item_load(row) -> str | None:
    """``None`` when this stored row still loads as a configuration, else the first reason it does not.

    ``resolve=True`` because the question being answered is "would a RUN be able to map with this
    row", and matching reads the RESOLVED shape — a set whose ``inherits`` no longer names a section
    is just as unusable as one the schema refuses, and it is the failure a shipped configuration
    could carry unnoticed (475 of 475 items take their gate from ``section_defaults``).

    Ported from the deleted ``routes.ontologies.probe_ontology_load``. Same contract, one engine.
    """
    key = (row.id, getattr(row, "created_at", None))
    if key not in _LOADABILITY:
        try:
            load_line_item_set(row.definition or {}, resolve=True)
            _LOADABILITY[key] = None
        except Exception as exc:  # noqa: BLE001 — any failure to load is the answer, whatever it is
            _LOADABILITY[key] = _first_load_error(exc)
    return _LOADABILITY[key]


def _prune_loadability(rows: list) -> None:
    """Keep one cache entry per LIVE row.

    Over a ``list(...)`` snapshot and with ``pop(..., None)``, because these endpoints are sync and
    FastAPI runs them in a threadpool: two concurrent polls, one iterating while the other deletes,
    is "dictionary changed size during iteration" — a 500 on a polled endpoint. Losing an entry to a
    race only costs one re-probe.
    """
    live = {(r.id, r.created_at) for r in rows}
    for stale in [k for k in list(_LOADABILITY) if k not in live]:
        _LOADABILITY.pop(stale, None)


def _all_versions(session: Session) -> list:
    from app.db.models import LineItemVersion

    return list(session.execute(
        select(LineItemVersion)
        .order_by(LineItemVersion.line_items_key, LineItemVersion.version.desc())
    ).scalars().all())


def _in_force_ids(session: Session, rows: list) -> set[str]:
    """The ids ``config_select`` names as in force, one per target template.

    The SERVER'S answer to "which configuration will the next run map against", asked of the one
    function that decides it. The client used to rank the list itself and could name a different
    row — the rule is latest-stored-wins, which needs ``created_at``, a field the payload never
    carried. A declaration the client cannot compute is a declaration the server has to make.
    """
    return {
        row.id
        for key in {r.target_template_key for r in rows if r.target_template_key}
        if (row := select_for_template(session, key)) is not None
    }


def _resolve_in_force(session: Session, template_key: str | None):
    """The stored version in force, and the template key it was chosen for.

    ``template_key`` is optional because the screen asks for "the configuration" and there is
    normally one. Omitted, it is taken from the most recently stored row and then put BACK through
    ``select_for_template`` rather than being returned directly — so even the defaulted answer comes
    from the single decider, and cannot drift from the one a run reads.
    """
    rows = _all_versions(session)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No line-item configuration is stored. Publish one with POST /line-items, or "
                   "restart so the shipped configuration is seeded.")
    key = template_key or max(rows, key=lambda r: (r.created_at, r.version, r.id)) \
        .target_template_key
    row = select_for_template(session, key)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No line-item configuration targets template {key!r}.")
    return row, key


def _version_identity(row) -> dict:
    """WHICH stored version answered — the part the seed-file read could never say.

    Served with every read so a screen can caption the definitions it is showing with the row a run
    would map against, instead of leaving the reader to assume they are the same thing.
    """
    return {"id": row.id, "line_items_key": row.line_items_key,
            "target_template_key": row.target_template_key, "version": row.version,
            "created_at": row.created_at.isoformat() if row.created_at else None}


def _sizes(definition: dict) -> dict:
    """How big a stored configuration actually is: items declared, and aliases across every locale.

    Served with the picker because the screens that name a configuration also describe its size, and
    with nothing to read they described a fabricated one. Counted off the stored definition rather
    than stored beside it, so an edit that publishes a new version cannot leave the count describing
    the old one.
    """
    items = definition.get("items") or []
    aliases = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        aliases += len(item.get("aliases") or [])
        # Per-locale aliases count too: they are the same kind of recognition evidence, and a set
        # carrying most of its vocabulary in zh would otherwise look nearly empty.
        for locale_aliases in (item.get("aliases_i18n") or {}).values():
            aliases += len(locale_aliases or [])
    return {"items": len(items), "aliases": aliases}


# ── what the editor may offer ──────────────────────────────────────────────────────────────────
#
# The Line Items screen is an EDITOR (see `ItemEdit` and `edit_line_item` below), and an editor
# offering a value the publish gate refuses is worse than no control at all: the author authors,
# saves, and is told no by a validator two layers down. So the closed sets are SERVED, from the
# same aliases the edit body is typed on and the loader validates with — one spelling, three
# readers (the schema, the edit body, the screen).
#
# `analyst_bucket` is the measured reason this block exists rather than being hard-coded in the
# frontend: a value naming no analyst section is refused by `_validate_against_target_template`,
# and before that refusal existed `bucket_of` silently filed those rows in Others.


# The four fields an author CANNOT change from the item editor, each with the one line that says
# why. Served with the vocabulary because "silently absent" and "read-only for a reason" look
# identical on a screen, and only one of them is a decision.
_NOT_EDITABLE: dict[str, str] = {
    "key": ("the identity every other declaration names — `parent`, `terms[].ref`, "
            "`children_if_decomposed`, `expected_components`, `never_sweep`, "
            "`sole_component_of` and the template's `canonical_key` all name it, and it is this "
            "endpoint's own selector, so rename via a full republish"),
    "children": ("computed from `parent` on every read — storage is flat, so editing the "
                 "projection cannot be persisted; reparent the child instead"),
    "aliases_i18n": ("edited one locale at a time through `aliases` + `locale`, never as a whole "
                     "map — a map-shaped write is how editing the Chinese aliases clobbers the "
                     "English ones"),
    "min_confidence_to_auto_accept": ("withdrawn — it was read by nothing (all four accept "
                                      "decisions compare against the global "
                                      "`settings.extraction.auto_accept_confidence`); the accept "
                                      "bar is that one global setting, so re-add this only "
                                      "alongside code that reads it"),
}


def _literal_values(annotation) -> list:
    """The tokens of a `Literal[...]` alias, in declaration order."""
    return list(get_args(annotation))

def _banner_vocabulary(st: LineItemSet):
    """The matcher's own `Vocabulary`, so the screen's idea of what constrains what is the engine's.

    Built here rather than reimplemented: `token_of_scope` resolves a compact id through
    `scope_tokens` and otherwise by the banner a scope id ENDS WITH, longest-first, and a second
    copy of that rule in a route is a second copy to drift.
    """
    from app.services.line_item_matching import Vocabulary

    return Vocabulary(st.vocabulary)


def _vocabulary(st: LineItemSet) -> dict:
    """Every value an item edit may legally carry, keyed the way the editor's controls are.

    Derived, never curated. A token added to `SearchScope` or a bucket added to `BUCKET_KEYS`
    reaches the screen the moment it exists, and a curated list beside the models is precisely the
    drift `GET /line-items/schema` was ported here to remove.
    """
    from app.services.buckets import BUCKET_KEYS

    # WHICH SECTION A LINE MAY BE CLAIMED UNDER — the SCOPE IDS this set actually uses, and only
    # those. Previously the raw banner tokens were offered alongside them, and measured on the
    # configuration in force that made the list twice as long as it needed to be for nothing:
    #
    #     20 scope ids      `bs_ca`, `is_pl`, `cf_financing`, `notes`, …   ALL 20 used by a line
    #     18 banner tokens  `current_assets`, `income_and_expenses`, …     17 of 18 used by NONE
    #
    # The two are not alternatives an author chooses between — they are the same eighteen sections
    # in two spellings. `token_of_scope` maps `bs_ca` onto the banner `current_assets` itself, so
    # naming the token instead of the id says nothing extra and loses the section identity that
    # `inherits`, the analyst bucket and `section_defaults` are all keyed on. The one token any line
    # does use, `profit_attributable_to`, IS a scope id here too, so nothing in force loses an
    # option. `section_scope` is still a free list on the model — a filing printing a banner nobody
    # has declared is exactly the case an author is here to handle — so this narrows a SUGGESTION
    # list and closes nothing.
    scope_ids = {s for d in st.items for s in d.section_scope if s}
    scope_ids |= {s for sec in st.section_defaults.values() for s in sec.section_scope if s}

    # `residual_policy.framework` and `.population` are free strings on the model, so what is
    # offered is what this set already declares plus the framework it actually carries — a
    # datalist, not a closed set, because the model does not close them.
    frameworks = {p.framework for d in st.items if (p := d.residual_policy) and p.framework}
    populations = {p.population for d in st.items if (p := d.residual_policy) and p.population}
    if st.residual_framework is not None:
        frameworks.add("residual_framework")
        populations.add(st.residual_framework.population)

    return {
        "statements": [s.value for s in StatementType],
        "scopes": _literal_values(SearchScope),
        "sides": _literal_values(Side),
        "rollups": _literal_values(Rollup),
        # How a calculated line's terms — or a cascade rung's — combine. Four values, and `sum` is
        # what every shipped formula means, so the control opens on the behaviour already in force.
        "terms_ops": _literal_values(TermsOp),
        "namespaces": _literal_values(Namespace),
        "types": _literal_values(LineItemType),
        # The three output structures, so the control cannot offer a fourth.
        "output_structures": _literal_values(OutputStructure),
        "note_selections": _literal_values(NoteSelection),
        # WHERE AN EXTRACTED LINE IS READ FROM — face, note tables or prose. Served so the control
        # offers exactly the three the pipeline implements and no fourth.
        "routes": _literal_values(Route),
        "value_scopes": _literal_values(ValueScope),
        "extraction_modes": _literal_values(ExtractionMode),
        "alias_matching": _literal_values(AliasMatching),
        "temporalities": _literal_values(Temporality),
        "units_of_account": _literal_values(UnitOfAccount),
        # The EXPECTATION (`sign_expectation` on the edit body, `sign_convention` on the model)…
        "sign_expectations": _literal_values(SignExpectation),
        # …and the NORMALISATION (`sign_rule.convention`), which is a different question. Six
        # values, of which the legacy 3-token UI vocabulary below can express three.
        "sign_conventions": [c.value for c in SignConvention],
        "legacy_sign_conventions": sorted(_SIGN_FROM_UI),
        "note_uses": _literal_values(NoteUse),
        "term_roles": _literal_values(Term.model_fields["role"].annotation),
        "analyst_buckets": list(BUCKET_KEYS),
        # `inherits` names a `section_defaults` entry of THIS set. A dangling one is not a load
        # error but a silent no-op that leaves the item with no gate at all, which is why the
        # options come from the set rather than from anything the client remembers.
        #
        # BOTH SPELLINGS ARE SERVED. `inherits_options` is the flat closed set the validator
        # compares against.
        "inherits_options": sorted(st.section_defaults),
        "section_scope_tokens": sorted(scope_ids),
        # WHICH OF THOSE ACTUALLY CONSTRAIN ANYTHING, so the screen can say so instead of leaving
        # an author to find out that a choice changed nothing.
        #
        # `token_of_scope` returns None for a scope id that names no banner, and an EMPTY resolved
        # scope means UNCONSTRAINED in `mapping._in_section` — so picking one of these is a
        # deliberate "any banner", not a narrower claim. Seven of the twenty are like that and 72
        # lines rely on it: `bs_top_level` and `profit_attributable_to`-style statement totals,
        # which no banner may constrain because a statement total routinely sits under the last
        # section printed above it; and the five compact sections a filing prints no banner for
        # (`statement_setup_controls`, `supplemental_data`, `off_balance_sheet_data`,
        # `credit_compliance`, `capital_and_lease_commitments`).
        "section_scope_unconstrained": sorted(
            s for s in scope_ids if _banner_vocabulary(st).token_of_scope(s) is None),
        "residual_frameworks": sorted(frameworks),
        "residual_populations": sorted(populations),
        # WHAT IS NOT AUTHORABLE HERE, AND WHY — served rather than restated in the screen, so a
        # field cannot quietly disappear from the editor with no reason attached. Point of order
        # for whoever adds a field: absent from the form is a defect; read-only with a reason is
        # a decision.
        "not_editable": _NOT_EDITABLE,
    }


# ── reads ──────────────────────────────────────────────────────────────────────────────────────


@router.get("", dependencies=[_GATE])
def get_line_items(template_key: str | None = None, session: Session = Depends(db)) -> dict:
    """The configuration IN FORCE, as the screen shows it, with the registry's own verdict.

    READ FROM THE DATABASE, never from the shipped file. The module-level ``SEED`` path and its
    ``_load()`` are deleted: a run maps against a ``line_item_versions`` row, so a screen serving
    the file on disk showed something no run necessarily used — and an inline edit published a new
    row that the screen then could not see.

    Sub-line items are nested UNDER their parent even though they are stored flat. Flat is right for
    editing — a sub-line item is a line item, and one shape means one editor — but a screen has to
    draw the hierarchy, and computing it here means the frontend never has to agree with the backend
    about what ``parent`` means.

    RESOLVED, not raw: the gate an item is actually matched under is the folded one (475 of 475
    items inherit theirs from ``section_defaults``), so serving the unfolded shape would show a
    screen full of items that appear to constrain nothing.
    """
    row, _ = _resolve_in_force(session, template_key)
    error = probe_line_item_load(row)
    if error is not None:
        # The one thing this screen must never do is render a definition that cannot be loaded as
        # though it were healthy — that is how 41 unloadable rows went unnoticed in the store this
        # one replaces. Same message shape as the extraction refusal, off the same probe.
        raise HTTPException(status_code=422, detail={
            "error": "configuration_unloadable",
            "message": (f"Line-item set {row.line_items_key!r} v{row.version} cannot be loaded, so "
                        f"it would govern nothing in a run: {error}. Republish it."),
            **_version_identity(row)})
    st = load_line_item_set(row.definition or {}, resolve=True)
    defs = st.items
    reg = build(defs)

    # WHAT EACH ITEM ITSELF DECLARED, keyed by key, off the UNRESOLVED stored dicts. The served
    # item is the RESOLVED one (475 of 475 take their gate from `section_defaults`), and once
    # validated an inherited value is indistinguishable from a declared one and from a model
    # default — which is exactly the distinction an editor has to draw, because saving a field the
    # item never declared turns an inherited value into a declared one and silently detaches the
    # item from its section. Computed once rather than per item: `payload` recurses.
    stored = row.definition or {}
    raw_list = stored.get("items") if isinstance(stored, dict) else stored
    raw_items = {d.get("key"): d for d in (raw_list or []) if isinstance(d, dict)}
    def payload(d) -> dict:
        out = d.model_dump(mode="json")
        out["children"] = [payload(k) for k in reg.children_of(d.key)]
        out["declared_fields"] = sorted(raw_items.get(d.key, {}).keys())
        # WHAT THE AUTHOR'S PLAIN PHRASES COMPILE TO — derived, read-only, and shown so the screen
        # is not asking anyone to trust it. The prose route is authored in words now
        # (`note_source.prose_subject` and `prose_landed_in`) and the patterns are generated, so
        # without this there is nowhere to check what a phrase list actually became.
        #
        # AT ITEM LEVEL AND NOT INSIDE `note_source`, deliberately. `note_source` is an
        # `_EDIT_MODELS` entry, so the screen sends the whole object back on every save — a
        # generated field inside it would round-trip into storage and reappear as though it had
        # been authored. Nothing outside `_EDIT_*` is ever written, so here it cannot.
        out["prose_compiled"] = prose_grammar.compile_for(d.note_source, st.prose_grammar)
        return out

    roots = sorted((d for d in defs if not d.parent), key=lambda d: (d.order, d.key))
    return {
        # WHICH version these definitions came from. Added when this endpoint stopped reading the
        # file: without it the payload could not distinguish the configuration in force from any
        # other, which is the whole defect being closed.
        "version": _version_identity(row),
        "items": [payload(d) for d in roots],
        # The set-level facts. `target_template_key` is the one the publish gate holds every key
        # against, and a bare JSON array had nowhere to put it — which is why the key-gate could not
        # simply be copied across from the ontology route.
        #
        # The vocabulary blocks are LIVE now. This comment used to carry a caveat saying the set's
        # six scoping vocabulary blocks were consulted only under
        # `extraction.mapping_engine = "line_items"` while the shipped default was "ontology", so a
        # configurator editing a banner here should not expect it to move a row. That switch is
        # gone: there is one engine, its input is this set, and an edit here is the only thing that
        # decides where a caption may be claimed.
        "set": {
            "schema_version": st.schema_version,
            "line_items_key": st.line_items_key,
            "target_template_key": st.target_template_key,
            "locale": st.locale,
            "supported_locales": st.supported_locales,
            "metadata": st.metadata.model_dump(mode="json"),
            # THE MASTER PROMPT, served so the screen can show and edit it. It is appended to the
            # framework's base instruction on every mapping call, so a reader looking at a line's
            # own prompt needs to see what it is being added to.
            "prompt": st.prompt,
            # Shown so a reader can see the gate is authored once per section rather than per
            # item — the two-layer model that survived the merge.
            "section_defaults": {k: v.model_dump(mode="json", exclude_none=True)
                                 for k, v in st.section_defaults.items()},
            # HOW A SENTENCE PLACES A FIGURE, authored once for the set. The screen needs the
            # subject vocabulary NAMES to offer them on a line, and needs the connective list to
            # say what the shared half of a prose rule already covers — a line only answers where
            # the figure landed, and without this the screen could not show why that is enough.
            "prose_grammar": st.prose_grammar.model_dump(mode="json"),
        },
        # EVERY VALUE THE EDITOR MAY OFFER, so the UI cannot present one the gate then refuses.
        # See `_vocabulary`: derived from the same aliases `ItemEdit` is typed on.
        "vocab": _vocabulary(st),
        "counts": {
            "total": len(defs),
            "output": sum(1 for d in defs if d.in_output),
            "sub_line_items": sum(1 for d in defs if d.parent),
            "by_type": {t: sum(1 for d in defs if d.type == t)
                        for t in ("extracted", "calculated", "intermediate", "derived")},
            "gated": sum(1 for d in defs if d.statement is not None),
            "inherited": sum(1 for d in defs if d.inherits),
        },
        # A configuration that does not load is the one thing this screen must never hide: the
        # whole reason it exists is that a 162-alternative regex was unreviewable.
        "problems": [{"key": p.key, "message": p.message, "severity": p.severity}
                     for p in reg.problems],
        "valid": reg.ok,
        # The order the engine would evaluate them in, so a reader can see that a formula's
        # inputs really do come first.
        "evaluation_order": reg.order,
    }


# A row that cannot be loaded is offered here as UNUSABLE rather than printed like any other, which
# is what the retired ontology picker could not do: it served all 44 rows straight from the raw dict
# and 41 of them did not load. Said in a comment and not in the docstring below, because a route
# docstring is the endpoint's OpenAPI description — an API consumer reads it, and this API is not
# supposed to mention an engine the product no longer has.
@router.get("/versions", dependencies=[_GATE])
def list_versions(session: Session = Depends(db)) -> list[dict]:
    """The picker: every stored version, newest edit of each key first.

    ``in_force`` and ``loads`` are both the SERVER'S answers, from the two functions that decide
    them elsewhere — ``config_select.select_for_template`` and :func:`probe_line_item_load`.
    """
    rows = _all_versions(session)
    _prune_loadability(rows)
    in_force = _in_force_ids(session, rows)
    return [{"id": r.id, "line_items_key": r.line_items_key,
             "target_template_key": r.target_template_key, "version": r.version,
             "created_at": r.created_at.isoformat() if r.created_at else None,
             "in_force": r.id in in_force,
             "loads": probe_line_item_load(r) is None,
             **_sizes(r.definition or {})}
            for r in rows]


# Declared BEFORE ``/versions/{version_id}``: routes match in declaration order, so registered after
# it "/versions/schema" would be read as an id. It is not under ``/versions`` at all for exactly
# that reason — the authoring aid and the stored rows do not share a namespace.
@router.get("/schema", dependencies=[_GATE])
def line_item_schema() -> dict:
    """The shape an uploaded configuration must have, generated from the model the gate validates with.

    ``json_schema`` is the machine-readable contract and ``field_help`` the flat index to read it by.
    Both are derived, so neither can describe a rule the upload gate does not enforce.
    """
    # Ported from the deleted ``ontologies.ontology_schema`` together with the generator that fed it
    # (``services/ontology_skeleton``'s ``field_help``/``json_schema``, whose only other caller was
    # the skeleton download that is gone). The generator lives at the foot of this module rather
    # than in a service of its own because this endpoint is the only reader left. Kept out of the
    # docstring above, which is served as this endpoint's OpenAPI description.
    return {"schema_version": CURRENT_SCHEMA_VERSION,
            "json_schema": LineItemSet.model_json_schema(),
            "field_help": _field_help()}


@router.get("/versions/{version_id}", dependencies=[_GATE])
def get_version(version_id: str, session: Session = Depends(db)) -> dict:
    """One stored version's FULL definition — download, edit, upload.

    Serves the definition exactly as stored, unresolved: what each item DECLARES is what an editor
    has to show and what an author has to hand back, and an inherited value merged in silently would
    be re-published as if it had been declared on the item.
    """
    from app.db.models import LineItemVersion

    row = session.get(LineItemVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line-item version not found")
    return {**_version_identity(row), "definition": row.definition,
            "loads": probe_line_item_load(row) is None}


# ── the publish gate ──────────────────────────────────────────────────────────────────────────


class LineItemSetCreate(BaseModel):
    definition: dict
    # Which template this configuration is FOR. A set carries its own target, but one authored
    # against one template is routinely re-pointed at a newly authored one — saying so on the
    # request beats hand-editing a quarter-megabyte of JSON, and it still has to validate against
    # that template.
    target_template_key: str | None = None


def _validate_against_target_template(session: Session, st: LineItemSet,
                                      *, key_field: str = "key",
                                      edited_key: str | None = None) -> dict:
    """Hold a configuration to the template it targets, and report WHICH version it was held to.

    Every publishing path goes through here, so validation cannot be skipped on one of them. On the
    retired ontology route it HAD been skipped on the inline-edit path, which guarded the check with
    ``if tpl_row is not None``: a definition whose ``target_template_key`` matched no stored template
    — a typo, a renamed key, a deleted template — published anyway and became the configuration in
    force while mapping onto keys no template declares. A configuration that cannot be validated is
    refused, because "unvalidatable" and "valid" are not the same answer.

    The checks are ported from ``loader.validate_ontology_against_template``, which takes an
    ``OntologyDefinition`` and so cannot be called with a set. Each one closes a measured defect:

    * a KEY the template does not declare maps a figure onto a column that does not exist. Items in
      the ``internal`` namespace are exempt BY DEFINITION — they are the set's own intermediates and
      name no output column (13 of 475 on the shipped file, measured, all ``internal``).
    * an ``analyst_bucket`` naming no section silently loses its rows: ``bucket_of`` refuses it and
      files them in Others, and nothing says why.
    * ``children_if_decomposed`` must name items OF THIS SET, because every reader compares an entry
      against a key that was matched — an entry that is not a key matches nothing and the
      containment it declares is silently unenforced. The pipe case is named separately: the
      generated rulebook shipped 30 of 31 carriers with every child packed into one pipe-joined
      string, so the whole file produced ONE decomposition instead of 25. That is a splitting
      mistake in whatever produced the file, not a typo in one name, and saying so points at the fix.
    * a ``mutually_exclusive_groups`` member that is not a key is the same requirement one level up,
      and it shipped broken the same way: 0 of 4 aggregates and 2 of 13 components existed among the
      462 keys. A group is a PROHIBITION, and a prohibition addressing nothing cannot be told apart
      from a filing that never triggered it. Every member is checked, not just the aggregate: a
      group missing one component permits exactly the double count it was written to prevent.

    EVERY ERROR CARRIES A ``field``, because this gate is reached from the ITEM EDITOR as well as
    from the upload door and a refusal an editor cannot pin to a control is a refusal the author
    cannot act on. Two arguments exist for that, and both are about not lying to the author:

    * ``key_field`` — the template-key check fires on ``namespace == "template" and key not in
      template``, and on an edit that CHANGED ``namespace`` the culprit is the namespace, not the
      key. Attributed to ``key`` there, the author reads "the key is wrong" about a key they cannot
      edit.
    * ``edited_key`` — a problem on a DIFFERENT item is not this author's control. A stored set can
      carry one the edit did not cause, and pointing it at the item in front of them would have
      them change a field that was never wrong. Those keep their ``location`` and lose their
      ``field``, which puts them in the banner where they belong.

    The returned record says which template version the check ran against — the NEWEST stored at
    publish time, which is not necessarily the version a run pins, so a reader with only the response
    would otherwise have to assume it.
    """
    from app.db.models import TemplateVersion

    from app.services.buckets import BUCKET_KEYS

    tpl_row = session.execute(
        select(TemplateVersion)
        .where(TemplateVersion.template_key == st.target_template_key)
        .order_by(TemplateVersion.version.desc())
    ).scalars().first()
    if tpl_row is None:
        raise HTTPException(
            status_code=422,
            detail=f"Target template {st.target_template_key!r} not found")
    try:
        template = load_template(tpl_row.definition or {})
    except Exception as exc:  # noqa: BLE001 — a stored template today's schema no longer accepts
        raise HTTPException(
            status_code=422,
            detail=f"Target template {tpl_row.template_key!r} v{tpl_row.version} cannot be read as "
                   f"a template ({exc}), so no configuration can be validated against it.") from exc

    template_keys = template.all_canonical_keys()
    own_keys = {d.key for d in st.items}
    errors: list[dict] = []
    for d in st.items:
        # A control only when this IS the item being edited — see the docstring.
        mine = edited_key is None or d.key == edited_key
        key_on = key_field if mine else None
        bucket_on = "analyst_bucket" if mine else None
        children_on = "children_if_decomposed" if mine else None
        if d.namespace == "template" and d.key not in template_keys:
            errors.append({"location": f"item:{d.key}", "field": key_on, "index": None,
                           "message": "key does not exist in the target template"})
        if d.analyst_bucket and d.analyst_bucket not in BUCKET_KEYS:
            errors.append({"location": f"item:{d.key}", "field": bucket_on, "index": None,
                           "message": (f"analyst_bucket {d.analyst_bucket!r} is not an analyst "
                                       f"section; expected one of {', '.join(BUCKET_KEYS)}")})
        for i, child in enumerate(d.children_if_decomposed):
            if "|" in child:
                errors.append({"location": f"item:{d.key}",
                               "field": children_on, "index": i,
                               "message": (f"children_if_decomposed entry {child!r} contains '|', "
                                           f"so it names no key — list each child separately")})
            elif child not in own_keys:
                errors.append({"location": f"item:{d.key}",
                               "field": children_on, "index": i,
                               "message": (f"children_if_decomposed names {child!r}, which is not "
                                           f"a key in this set, so the containment it declares is "
                                           f"unenforceable")})
    for group in st.global_rules.mutually_exclusive_groups:
        gid = group.id or "mutually_exclusive_group"
        for role, key in ([("aggregate", group.aggregate)]
                          + [("component", c) for c in group.components]):
            if key and key not in own_keys:
                # No item-editor control owns a set-level group, so this one carries no `field`:
                # an unattributed error belongs in the banner, not against a control.
                errors.append({"location": f"mutually_exclusive_group:{gid}",
                               "field": None, "index": None,
                               "message": (f"{role} {key!r} is not a key in this set, so the "
                                           f"exclusivity it declares can never be enforced")})
    for rule in st.decomposition_rules:
        if rule.face_key not in template_keys:
            errors.append({"location": f"decomposition:{rule.id}", "field": None, "index": None,
                           "message": f"face_key {rule.face_key!r} does not exist in the template"})
    if errors:
        # Same shape as every other refusal here, `location` kept alongside `field` — see
        # `_refuse`. The tag differs because this gate is about the CONFIGURATION against its
        # template, not about the one field an author just typed.
        _refuse(errors, error="invalid_configuration",
                what=(f"This configuration does not hold against template "
                      f"{tpl_row.template_key!r} v{tpl_row.version}"))
    return {"id": tpl_row.id, "template_key": tpl_row.template_key, "version": tpl_row.version}


# What counts as RECOGNITION EVIDENCE: the fields a caption can actually be matched on. A
# `calculated`/`derived` item legitimately carries none — it is computed, not recognised — so the
# door-check below is about the SET, not about each item.
_RECOGNISES = ("aliases", "pattern", "regex_hints", "keyword_hints")


def _recognises_anything(st: LineItemSet) -> bool:
    """Whether ANY item in this set could match a printed caption.

    THE DOOR-CHECK THAT REPLACED FIVE RANKING RULES (see ``services.config_select``). The hole those
    rules were really guarding is a SKELETON upload — every item a stub with no aliases at all —
    becoming the configuration a real extraction runs on. Ranking cannot answer that honestly: a
    configuration that recognises nothing is not a lower-priority configuration, it is not a
    configuration. Refused here, where an author is present to be told why.
    """
    for d in st.items:
        if any(getattr(d, f) for f in _RECOGNISES):
            return True
        if any(d.aliases_i18n.values()):
            return True
    return False


@router.post("", status_code=201, dependencies=[_GATE])
def create_line_item_set(body: LineItemSetCreate, session: Session = Depends(db)) -> dict:
    """Publish a configuration as a NEW VERSION. The only door into the store.

    The same three checks ``sample.reference`` puts the SHIPPED file through at boot, in the same
    order, so a file that boots is a file that publishes and vice versa.
    """
    from app.db.models import LineItemVersion

    definition = body.definition
    if body.target_template_key:
        definition = {**definition, "target_template_key": body.target_template_key}

    # The shape as AUTHORED (`resolve=False`) is what the stray-key report has to be taken against;
    # the RESOLVED load is what a run reads, and it is the only thing that catches a dangling
    # `inherits` — which is not a load error but a silent no-op leaving the item with no section gate
    # at all, the one failure a configuration could carry unnoticed.
    try:
        st = load_line_item_set(definition, resolve=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422,
                            detail=f"Invalid line-item schema: {exc}") from exc
    try:
        resolved = load_line_item_set(definition, resolve=True)
    except UnknownInheritsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail=f"Invalid line-item schema once the section layer is folded in: {exc}") from exc

    # An undeclared key is refused on UPLOAD — pydantic would otherwise drop it in silence and
    # publish a configuration missing the thing that was authored. NOT applied to the inline-edit
    # path (`_publish_new_version`), which derives its definition from a row already stored:
    # refusing there would make one stray key in an old row block every future edit to it. See
    # `loader.unknown_keys` for why this is not `extra='forbid'` on the models.
    #
    # The allowance is imported from `sample.reference` rather than restated, so the boot gate and
    # this door cannot disagree about which single measured, test-pinned key has no home
    # (`note_use_rationale`, on 394 of 475 shipped items — recovered in full by the section layer).
    # Two spellings of one allowance is how a shipped file comes to fail its own upload.
    from app.sample.reference import _accounted_for

    # Walked WHOLE and truncated AFTER filtering, not before: `unknown_keys` stops walking once it
    # has `limit` hits, and the ~394 accounted-for paths would otherwise fill the report and hide a
    # real stray key later in the file.
    stray = [p for p in unknown_keys(definition, st, limit=5000) if not _accounted_for(p)][:20]
    if stray:
        # No `field`: a stray key is a path in an uploaded file (`items[3].aliasses`), not a
        # control on a screen, so it belongs in the banner. Same envelope regardless — see
        # `_refuse`.
        _refuse([{"location": p, "field": None, "index": None,
                  "message": "key is not part of the line-item schema"} for p in stray],
                error="invalid_configuration",
                what="This configuration declares keys the line-item schema does not")

    if not _recognises_anything(resolved):
        raise HTTPException(
            status_code=422,
            detail={"error": "recognises_nothing",
                    "message": (f"This set declares {len(resolved.items)} line items and not one "
                                f"alias, pattern, regex hint or keyword hint between them, so no "
                                f"caption in any filing could ever match it. Publishing it would "
                                f"put it in force and every extraction would recognise nothing. "
                                f"Add recognition evidence, or edit the version already in "
                                f"force.")})

    validated_against = _validate_against_target_template(session, resolved)

    if not st.line_items_key:
        raise HTTPException(status_code=422,
                            detail="A configuration needs a line_items_key to be versioned under")
    max_ver = session.execute(
        select(func.max(LineItemVersion.version))
        .where(LineItemVersion.line_items_key == st.line_items_key)
    ).scalar()
    version = (max_ver or 0) + 1
    row = LineItemVersion(
        line_items_key=st.line_items_key,
        target_template_key=st.target_template_key,
        version=version,
        definition=definition,
    )
    session.add(row)
    session.commit()
    return {**_version_identity(row),
            "items": len(st.items),
            "validated_against_template": validated_against}


# ── the inline edit ───────────────────────────────────────────────────────────────────────────


# Ported from the deleted ``ontologies.MappingEdit``. ``canonical_key`` is now ``key`` — the only
# rename in this change that reaches an API consumer, and the one the whole change is about: the old
# field named the ontology's concept space, and there is no concept space any more. In a comment
# because a pydantic docstring is served as the request body's OpenAPI description.
#
# WHAT THIS BODY USED TO BE, so nobody trims it back. It accepted THIRTEEN fields while
# ``LineItemDef`` declares roughly forty, and the justification on the Line Items screen was that
# the definitions described derivations five services still computed, so nothing downstream read
# them. That justification expired: line items is the single configuration engine, the matcher is
# built from this set, and a run pins ``extraction_runs.line_item_version_id``. The definitions
# DRIVE extraction, so a field that is authorable in the schema and unreachable through this body
# is a control the product claims to have and does not.
#
# THREE FIELDS ARE STILL NOT ACCEPTED, each for its own reason, and the reasons are served to the
# screen as ``vocab.not_editable`` rather than left implicit:
#
#   * ``key`` — the identity every other declaration names, and this endpoint's own selector, so an
#     inline rename has no coherent target.
#   * ``children`` — a projection of ``parent`` computed on every read; editing it cannot persist.
#   * ``aliases_i18n`` — reachable one locale at a time through ``aliases`` + ``locale``. A
#     map-shaped write is precisely how editing the Chinese aliases clobbers the English ones.
#
# And ``min_confidence_to_auto_accept`` is WITHDRAWN, deliberately, and is not accepted here: it
# was read by nothing (all four accept decisions compare against the global
# ``settings.extraction.auto_accept_confidence``), it is absent from ``LineItemDef``, and accepting
# it would store a key the upload gate then refuses. Re-add it only alongside code that reads it.
class ItemEdit(BaseModel):
    """An inline edit to ONE line item. Only fields PRESENT in the body change.

    Presence, not truthiness: a field left out is untouched, an explicit ``null`` writes "nothing
    was said" where the schema has such a state, and ``[]`` / ``""`` is a CONFIGURED EMPTY that is
    stored as empty and never re-defaulted. Clearing a list has to be expressible or an author
    cannot undo their own edit.

    ``aliases`` is locale-scoped: it replaces that locale's ``aliases_i18n`` list (and the base
    ``aliases`` list when the locale is the set's own default), so editing the Chinese aliases can
    never silently clobber the English ones.
    """

    key: str
    locale: str | None = None
    aliases: list[str] | None = None
    # THE LEGACY 3-TOKEN UI SPELLING, kept because the Template screen sends it. It writes
    # `sign_rule.convention` through `_SIGN_FROM_UI` and can express three of the six real
    # `SignConvention` values. It is NOT `LineItemDef.sign_convention`: that field is the sign the
    # line is EXPECTED to carry, which review validation reads, and it is `sign_expectation` below.
    # Two fields, two questions; the screen must label them apart or an author edits one thinking
    # they changed the other.
    sign_convention: str | None = None
    label: str | None = None
    # The criteria the LLM actually reasons over. Aliases only help when the printed wording is
    # close to one; `definition`/`include_criteria`/`exclude_criteria`/`confusable_with` are what
    # let a caption be resolved by MEANING, so they have to be editable too or an analyst can only
    # ever tune string matching.
    definition: str | None = None
    # Extra instruction for THIS line, sent inside its own candidate entry. Refused by
    # `LineItemDef` on a non-`extracted` line, and that refusal lands on this field.
    prompt: str | None = None
    exclude_criteria: list[str] | None = None
    value_scope: ValueScope | None = None
    # Lexical rule hints (regex / keyword), the deterministic tier's controls. `exclude_hints` are
    # regex VETOES — prose belongs in `exclude_criteria`, and folding prose into this list either
    # fails validation or compiles as an accidental veto.
    keyword_hints: list[str] | None = None
    regex_hints: list[str] | None = None
    exclude_hints: list[str] | None = None
    # THE SECTION GATE, the half the merge brought over from the ontology: WHERE this item may be
    # claimed from. Editable because it is what decides whether a figure lands on the right line at
    # all — 420 of 1,969 shipped captions are claimed by more than one item, and 96 of those across
    # different statements, so `intangible assets` is separated by the gate and by nothing else.
    #
    # `statement` stays `str | None` rather than `StatementType | None` because `""` is the
    # spelling the existing editor clears it with and that has to keep meaning "claimable
    # anywhere"; the token is checked against `StatementType` in the apply, attributed to the field.
    statement: str | None = None
    statements: list[str] | None = None
    section_scope: list[str] | None = None

    # ── the structure of the tree ─────────────────────────────────────────────────────────────
    type: LineItemType | None = None
    # WHAT THE LINE OUTPUTS — a number, a phrase lifted from the page, or prose the model writes
    # from this line's prompt. See `schemas.line_items.OutputStructure`.
    output_structure: OutputStructure | None = None
    in_output: bool | None = None
    parent: str | None = None
    rollup: Rollup | None = None
    order: int | None = None
    namespace: Namespace | None = None

    # ── the rest of the gate ──────────────────────────────────────────────────────────────────
    inherits: str | None = None
    match_priority: int | None = None
    alias_matching: AliasMatching | None = None
    extraction_mode: ExtractionMode | None = None
    scopes: list[SearchScope] | None = None
    side: Side | None = None
    allow_contra: bool | None = None
    llm_only_if_note_tagged: bool | None = None
    note_selection: NoteSelection | None = None
    route: Route | None = None
    note_source: NoteSource | None = None
    note_use: NoteUse | None = None
    face_only: bool | None = None

    # ── containment and residuals ─────────────────────────────────────────────────────────────
    is_gross_parent: bool | None = None
    children_if_decomposed: list[str] | None = None
    sole_component_of: str | None = None
    residual_policy: ResidualPolicy | None = None
    expected_components: list[str] | None = None
    never_sweep: list[str] | None = None

    # ── recognition ───────────────────────────────────────────────────────────────────────────
    pattern: str | None = None

    # ── measurement ───────────────────────────────────────────────────────────────────────────
    temporality: Temporality | None = None
    unit_of_account: UnitOfAccount | None = None
    # `LineItemDef.sign_convention` — the EXPECTATION review validation reads. Named
    # `sign_expectation` on the wire because `sign_convention` is already taken by the legacy
    # 3-token spelling above, and one name for two fields is how an author changes the wrong one.
    sign_expectation: SignExpectation | None = None
    sign_rule: SignRule | None = None
    analyst_bucket: str | None = None

    # ── assembly ──────────────────────────────────────────────────────────────────────────────
    terms: list[Term] | None = None
    # HOW THE TERMS ABOVE COMBINE — sum, max, min or first. See `schemas.line_items.TermsOp`. On the
    # wire as a scalar rather than inside `terms`, because it is a property of the GROUP and not of
    # any one addend; a per-term copy would be four ways to disagree about one rule. The per-RUNG
    # spelling travels inside `cascade`, which is posted whole.
    terms_op: TermsOp | None = None
    cascade: list[CascadeRung] | None = None
    implemented_by: str | None = None

    # ── prose ─────────────────────────────────────────────────────────────────────────────────
    # Each documents a decision someone will otherwise re-litigate. (`section_disambiguation` was
    # here and is gone from the model — see its tombstone in `schemas/line_items.py`.)
    decomposition_rule: str | None = None
    others_rule: str | None = None
    derivation: str | None = None
    notes_as_source_rationale: str | None = None


def _clean_list(items: list[str] | None) -> list[str]:
    """Trim, drop blanks, de-duplicate — preserving the editor's ordering."""
    return list(dict.fromkeys(i.strip() for i in (items or []) if i and i.strip()))


# UI sign vocabulary → a real SignConvention value (app.core.models.enums.SignConvention).
# "auto" means let the pipeline decide from surrounding context rather than forcing a sign.
_SIGN_FROM_UI = {
    "as_reported": "natural_positive",
    "expense_contra": "natural_negative",
    "auto": "context",
}


# ── the apply tables ──────────────────────────────────────────────────────────────────────────
#
# TABLE-DRIVEN, replacing a chain of `if body.X is not None`. Two reasons, and the second is the
# defect being fixed. First, forty fields of that chain is forty chances to forget one — the
# thirteen-field version was that chain, and the fields it did not carry were invisible. Second,
# `is not None` cannot tell `null` from absent, so it could not express a CLEAR at all: an author
# emptying a list sent `[]`, which is not None and therefore worked, but an author clearing a
# nullable object had no way to say so. Presence (`model_fields_set`) distinguishes three states,
# which is the number the schema actually has.
#
# ItemEdit field -> the LineItemDef field it writes. Straight copy of the validated value.
_EDIT_SCALARS: dict[str, str] = {
    # The calculated rule builder's operator — see `ItemEdit.terms_op`.
    "terms_op": "terms_op",
    "label": "label",
    "definition": "definition",
    "prompt": "prompt",
    "type": "type",
    "output_structure": "output_structure",
    "in_output": "in_output",
    "rollup": "rollup",
    "order": "order",
    "namespace": "namespace",
    "inherits": "inherits",
    "match_priority": "match_priority",
    "alias_matching": "alias_matching",
    "extraction_mode": "extraction_mode",
    "value_scope": "value_scope",
    "side": "side",
    "allow_contra": "allow_contra",
    "llm_only_if_note_tagged": "llm_only_if_note_tagged",
    "note_selection": "note_selection",
    "route": "route",
    "statements": "statements",
    "note_use": "note_use",
    "face_only": "face_only",
    "is_gross_parent": "is_gross_parent",
    "temporality": "temporality",
    "unit_of_account": "unit_of_account",
    # THE ONE RENAME: `sign_expectation` on the wire is `sign_convention` on the model. See
    # `ItemEdit.sign_expectation`.
    "sign_expectation": "sign_convention",
    "analyst_bucket": "analyst_bucket",
    "implemented_by": "implemented_by",
    "decomposition_rule": "decomposition_rule",
    "others_rule": "others_rule",
    "derivation": "derivation",
    "notes_as_source_rationale": "notes_as_source_rationale",
}

# Lists of plain strings, written through `_clean_list` so the editor's ordering survives and
# blanks do not. `[]` IS STORED. The ones that name other keys, that must compile, or that are
# locale-scoped are special cases in the apply instead.
_EDIT_LISTS: dict[str, str] = {
    "exclude_criteria": "exclude_criteria",
    "keyword_hints": "keyword_hints",
    # `scopes` is a search ORDER, not a set, and `_clean_list` preserves order — which is why it
    # belongs here rather than being sorted anywhere on the way through.
    "scopes": "scopes",
}

# Nullable sub-objects: dumped in JSON mode so what is stored is what an upload would carry, and
# `null` disables the whole object (a residual with no policy, an item with no note source).
_EDIT_MODELS: dict[str, str] = {
    "residual_policy": "residual_policy",
    "note_source": "note_source",
    "sign_rule": "sign_rule",
}

# Lists of sub-objects. `[]` means "no terms" / "no rungs", which is what the model's own default
# says, so there is no separate null state to express.
_EDIT_MODEL_LISTS: dict[str, str] = {
    "terms": "terms",
    "cascade": "cascade",
}

# Which LineItemDef fields have a "nothing was said" state at all, read OFF THE MODEL so it cannot
# drift from what the loader accepts. An explicit `null` on a field that has no such state is
# refused with the value that does clear it, rather than being written and coming back as a
# pydantic error four layers down naming a path the author never typed.
_NULLABLE_ON_DEF: set[str] = {
    name for name, f in LineItemDef.model_fields.items()
    if type(None) in get_args(f.annotation)
}

# Every field of the edit body that writes something, for the refusal attribution below. `key` and
# `locale` are selectors, not values.
_EDITABLE_FIELDS: set[str] = set(ItemEdit.model_fields) - {"key", "locale"}

# LineItemDef field name -> the ItemEdit field that writes it, where the two differ. Only one does,
# and getting it wrong would attribute a sign-expectation refusal to the legacy normalisation
# control — a different question on a different row of the screen.
_MODEL_TO_EDIT_FIELD: dict[str, str] = {"sign_convention": "sign_expectation"}

# FIELDS NO CONFIGURATION SURFACE OFFERS, and therefore no longer writable here — field -> why,
# because a refusal that does not say what decides the question instead is a dead end.
#
# Applied where every edit passes through (see the loop in the apply below). The reason strings are
# the whole value of this table: an author who sent one of these was answering a question the
# console has stopped asking, and the useful reply is not "refused" but "X decides that now".
#
# NOT HERE, DELIBERATELY, and each is a case the obvious rule would have got wrong:
#   * `value_scope`, `confusable_with`, `exclude_hints` — `screens/Template.tsx` writes them
#     through this endpoint, so they are authorable, just not from the Line Items screen.
#   * `cascade`, `implemented_by`, `terms` — `requiredNow` forces them onto the form for a derived
#     or calculated line, so they are authorable there.
#   * `sign_convention` — the legacy 3-token sign RULE the Template screen sends, which is a
#     different question from `sign_expectation` below. One of these is retired and one is not,
#     and reading the names too quickly is how the wrong one gets refused.
_NOT_CONFIGURABLE: dict[str, str] = {
    # `type` is now the only field describing how a figure is obtained.
    "extraction_mode": "`type` says how a figure is obtained — extracted, calculated or derived",
    # ONE PROSE FIELD. `definition` and `prompt` asked the same person the same question twice —
    # "what is this line" and "what else should the model be told about it" — and both arrived in
    # the same request under different keys. They are merged: `LineItemDef` folds any stored
    # `prompt` into `definition` on load (so a set authored before the merge still works, and the
    # shipped seed has had its 61 such lines folded in place), and the field is refused here so a
    # new one cannot be created by API call for a question no screen asks.
    "prompt": "merged into `definition` — author the whole instruction there, in one field",
    # DECOMPOSITION IS ALWAYS ALLOWED, so there is no question left to answer. `note_use` asked
    # whether a cited note may SUPPLY a line's figure or only corroborate it; every reader of it
    # has been removed (`stages/note_sourced`, `stages/map_ontology`, `stages/residual`), and on
    # the shipped set the gate it drove could not fire — of the 70 items that resolved to
    # `evidence_only`, none was a note-sourced parent, none carried a `note_source` and none had a
    # child. What still decides whether a note is read is the line's own note route.
    "note_use": "decomposition is always allowed — a line's note route decides whether a note is "
                "read, not a separate permission",
    #
    # `in_output`, `namespace` AND `order` WERE HERE AND ARE NOT, and the reason is worth keeping
    # because the argument for refusing them was sound for most of the set and wrong where it
    # mattered. "The uploaded template decides which lines are delivered" holds for the 462
    # template lines. It does not hold for the 77 note-level PARTS: measured, all 77 declare
    # `namespace: internal` and `in_output: false`, and ZERO of them appear anywhere in the
    # template. The template has no row for a part, so it cannot decide a part's delivery or its
    # display order — and with these refused, a newly authored part would default to
    # `in_output: true` and publish as a spurious export row, which is to say parts would not be
    # authorable at all. That is exactly the surface someone extending the eight focus lines needs.
    # Residual is template-routed; these were v2 overrides on a path that works without them.
    "residual_policy": "the residual sweep is routed by the template's own `__others` keys",
    "never_sweep": "the residual sweep is routed by the template's own `__others` keys",
    "expected_components": "the residual sweep is routed by the template's own `__others` keys",
    # Section policy. Measured over the 18 sections, none of these varies inside one.
    "temporality": "this never varies inside a section — set it on the section",
    "sign_expectation": "this never varies inside a section — set it on the section",
    "face_only": "this never varies inside a section — set it on the section",
    # Parenthood the template already declares through its rollups.
    "is_gross_parent": "the template's `rollup.children` declares what a subtotal contains",
    "children_if_decomposed": "the template's `rollup.children` declares what a subtotal contains",
    "rollup": "the template's `rollup` declares how a subtotal combines its children",
    # Contested captions are settled by the BANNER the caption was printed under (`section_scope`)
    # — 170 of this set's contested captions are decided by it and nothing else, same statement and
    # different sub-heading. (This used to name `section_disambiguation`, which is gone: its 395
    # values held 13 generated strings, each restating the gate the banner already states.)
    "match_priority": "the banner a caption is printed under (`section_scope`) settles which line "
                      "claims it",
    # Declared by no item in the shipped set, and read by nothing that matters.
    "output_structure": "no line item declares this",
    "others_rule": "no line item declares this",
    "derivation": "no line item declares this — the cascade's own rung notes record the reasoning",
    "notes_as_source_rationale": "no line item declares this",
    "sole_component_of": "no line item declares this",
    "scopes": "the section decides where a caption is looked for",
    "side": "the section banner the caption was printed under decides this",
    "allow_contra": "no line item declares this",
    "analyst_bucket": "no line item declares this",
    "pattern": "`regex_hints` is the list form and is what the matcher reads",
    "decomposition_rule": "`global_rules.no_fabricated_split` carries this for the whole set",
}

# Validator messages that name no field of their own, and the control each one is really about.
# `_coherent`'s `from_section` refusal is the case that forces this table: its remedy sentence
# mentions `scopes` and `section_scope`, so a scan for field names in the text would land on
# either of those while the thing to change is `side`.
_MESSAGE_CULPRITS: tuple[tuple[str, str], ...] = (
    ("`from_section` needs", "side"),
    ("needs a cascade or", "cascade"),
    ("needs at least one term", "terms"),
    ("exactly one of `ref` or `const`", "terms"),
    ("`abs` applies to a referenced", "terms"),
)


def _err(field: str | None, message: str, index: int | None = None) -> dict:
    """One refusal, addressed to the control that caused it."""
    return {"field": field, "index": index, "message": message}


def _error_line(error: dict) -> str:
    field, index = error.get("field"), error.get("index")
    where = field if index is None else f"{field}[{index}]"
    return f"{where}: {error['message']}" if where else error["message"]


def _refuse(errors: list[dict], *, error: str = "invalid_edit",
            what: str = "This edit was not applied") -> None:
    """Refuse, saying WHICH FIELD each problem belongs to.

    The endpoint re-validates against the target template before it publishes, so a refusal is
    information the author needs rather than an error to swallow — and a refusal with no field on
    it lands in a banner the author reads once and cannot act on. One sentence for the summary, the
    per-field list for the controls, and the server's own message verbatim in both: a paraphrase
    here is a second spelling of a rule this module does not own.

    ONE SHAPE FOR EVERY REFUSAL FROM THIS FILE, tag included, because the client that has to render
    them is one client and a second shape is a second renderer that will be written later, worse,
    or not at all. ``location`` survives on the entries that carry it (the upload door reports
    ``items[3].aliasses``, which is not a control on any screen) — an entry can have both.
    """
    shown = "; ".join(_error_line(e) for e in errors[:5])
    more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
    raise HTTPException(status_code=422, detail={
        "error": error,
        "message": f"{what} — {len(errors)} problem(s) to fix first: {shown}{more}.",
        # Bounded like every other error list on this route: one bad paste can produce hundreds,
        # and the hundredth says nothing the first fifty did not.
        "errors": errors[:50],
    })


def _clear_hint(target_field: str) -> str:
    """What to send to clear a field that has no null state, in the JSON spelling."""
    field = LineItemDef.model_fields.get(target_field)
    if field is None:                          # pragma: no cover — table and model agree
        return "an empty value"
    if field.default_factory is not None:
        return _as_json(field.default_factory())
    return _as_json(field.default)


def _field_of_loc(loc: tuple) -> tuple[str | None, int | None]:
    """Map a pydantic error location onto the edit field that owns it.

    ``items.12.side`` -> ``side``; ``items.12.terms.0.ref`` -> ``terms`` index 0. The deepest
    segment that names an edit field wins, and the integer immediately after it is the row the
    editor has to highlight — `terms.0.ref` is one bad row in a term table, not a bad table.
    """
    field: str | None = None
    index: int | None = None
    for i, part in enumerate(loc):
        if not isinstance(part, str):
            continue
        name = _MODEL_TO_EDIT_FIELD.get(part, part)
        if name in _EDITABLE_FIELDS:
            field, index = name, None
            following = loc[i + 1] if i + 1 < len(loc) else None
            if isinstance(following, int):
                index = following
    return field, index


def _field_in_message(message: str) -> tuple[str | None, int | None]:
    """Attribute a whole-item validator's message, which carries no field in its location.

    ``LineItemDef._coherent`` and ``_refuse_uncompilable`` run on the item, so pydantic reports
    them at ``items.12`` with the field named only in the prose ("regex_hints[0] '(' : missing )").
    Read out of the prose rather than dropped, because "something on this item is wrong" is not a
    thing an author can act on.
    """
    for needle, field in _MESSAGE_CULPRITS:
        if needle in message:
            return field, None
    # Longest name first: `section_scope` must not be found inside a message that says `scopes`,
    # and `sign_convention` must not shadow `sign_rule.flip_if_label_matches`.
    for name in sorted(_EDITABLE_FIELDS, key=len, reverse=True):
        if name not in message:
            continue
        hit = re.search(re.escape(name) + r"\[(\d+)\]", message)
        return name, int(hit.group(1)) if hit else None
    return None, None


def _attributed_errors(exc: ValidationError, edited_index: int | None = None) -> list[dict]:
    """A pydantic failure, one error per control, with the server's message kept whole.

    Anything that cannot be attributed keeps its full path in the message instead of losing it —
    an unattributed error belongs in the banner, and a dropped one belongs nowhere.

    ``edited_index`` is the position of the item being edited, and errors on ANY OTHER item are
    deliberately left unattributed. A stored set can carry a problem the edit did not cause (a row
    written by an older schema, a sibling someone else broke), and pointing that at a control on
    the item in front of the author is worse than pointing at nothing: they would change a field
    that was never wrong and watch the same refusal come back.
    """
    out: list[dict] = []
    for error in exc.errors():
        loc = tuple(error.get("loc") or ())
        message = str(error.get("msg", "")).removeprefix("Value error, ")
        mine = (edited_index is None
                or len(loc) < 2 or loc[0] != "items" or loc[1] == edited_index)
        field, index = _field_of_loc(loc) if mine else (None, None)
        if field is None and mine:
            field, index = _field_in_message(message)
        if field is None:
            where = ".".join(str(p) for p in loc)
            message = f"{where}: {message}" if where else message
        out.append(_err(field, message, index))
    return out


def _models_now():
    """The timestamp helper `db.models` stamps every other column on this table with."""
    from app.db.models import _now

    return _now()


def _pinned_by_a_run(session: Session, version_id: str) -> bool:
    """Whether any extraction run names this configuration version.

    THE ONE THING THAT MAKES A VERSION IMMUTABLE. A run records the exact version it used so a
    reader can be told which configuration produced the figures; replacing that definition would
    retroactively change how a finished run is explained, and nothing would say so. So a pinned
    version is never written to, whatever session asks.
    """
    from app.db.models import ExtractionRun

    return session.execute(
        select(func.count()).select_from(ExtractionRun)
        .where(ExtractionRun.line_item_version_id == version_id)
    ).scalar_one() > 0


def _publish_new_version(session: Session, row, definition: dict, *, key_field: str = "key",
                         edited_key: str | None = None, edited_index: int | None = None,
                         session_id: str = "") -> dict:
    """Validate an edited definition and store it — replacing this session's version, or adding one.

    Shared by every inline edit so validation can never be skipped on one path.

    ONE VERSION PER SESSION, NOT ONE PER FIELD. Every edit used to add a version, so a sitting
    spent renaming a few lines produced a dozen of them and the history said nothing about what
    happened: measured on the live database, ELEVEN versions for three line-item edits and six
    deletions. An edit arriving in the same session as the version in force now REPLACES that
    version's definition; a different session starts a new one. A version is then one person's
    sitting, which is the unit a reader of the history actually wants.

    THREE CONDITIONS, ALL REQUIRED, or it inserts:

      * the version in force was authored in THIS session — `authored_in_session` matches
      * this caller HAS a session to be identified by. The `X-Role` dev header and every service
        call have none, and absence of a session id is not proof that the session is the same, so
        an unattributed publish always inserts.
      * NO RUN PINS IT. This is the original rule and it is untouched: a run names the exact
        version it used, and rewriting that definition would change how a finished run is
        explained. See `_pinned_by_a_run`.

    So the immutability that mattered is kept exactly — what changes is that an unpinned version
    nobody else has seen stops being frozen the instant it is written.

    THE REFUSALS ARE ATTRIBUTED, the publish itself is unchanged. Everything the loader can say
    about an edited definition is something an author has to fix on a control: a bad `side`, a term
    naming nothing, a regex that does not compile. Reported as `field`/`index`, it lands on the
    row that caused it; reported as one sentence off `str(exc)` — which is what this used to do —
    it is hundreds of pydantic lines in a banner. Anything that cannot be attributed keeps its
    sentence, so no message is lost either way.
    """
    from app.db.models import LineItemVersion

    try:
        st = load_line_item_set(definition, resolve=True)
    except UnknownInheritsError as exc:
        # NOT a load error and not a typo the schema can see: the item validates, loads, and
        # carries NONE of its section's gate, so nothing could ever place it. The message names
        # every offender and the sections that do exist, which is what makes it fixable.
        _refuse([_err("inherits", str(exc))])
    except ValidationError as exc:
        _refuse(_attributed_errors(exc, edited_index))
    except Exception as exc:  # noqa: BLE001 — a PatternOverlap or anything else the set refuses
        raise HTTPException(status_code=422,
                            detail=f"Edit produced an invalid configuration: {exc}") from exc

    validated_against = _validate_against_target_template(session, st, key_field=key_field,
                                                          edited_key=edited_key)

    definition["line_items_key"] = row.line_items_key

    # THE VERSION IN FORCE — the highest version of this set, which is what an edit is against.
    in_force = session.execute(
        select(LineItemVersion)
        .where(LineItemVersion.line_items_key == row.line_items_key)
        .order_by(LineItemVersion.version.desc())
        .limit(1)
    ).scalars().first()

    if (session_id and in_force is not None
            and in_force.authored_in_session == session_id
            and not _pinned_by_a_run(session, in_force.id)):
        # SAME SITTING, NOBODY ELSE HAS SEEN IT: replace it rather than stacking another.
        in_force.definition = definition
        # The same clock every other row on this table uses, so a reader comparing
        # `created_at` with `updated_at` is comparing like with like.
        in_force.updated_at = _models_now()
        session.add(in_force)
        session.commit()
        return {**_version_identity(in_force),
                "validated_against_template": validated_against}

    max_ver = session.execute(
        select(func.max(LineItemVersion.version))
        .where(LineItemVersion.line_items_key == row.line_items_key)
    ).scalar()
    new_row = LineItemVersion(
        line_items_key=row.line_items_key,
        target_template_key=row.target_template_key,
        version=(max_ver or 0) + 1,
        definition=definition,
        authored_in_session=session_id or None,
    )
    session.add(new_row)
    session.commit()
    return {**_version_identity(new_row),
            "validated_against_template": validated_against}


@router.patch("/versions/{version_id}/items", dependencies=[_GATE])
def edit_line_item(version_id: str, body: ItemEdit,
                   session: Session = Depends(db),
                   principal: Principal = Depends(current_principal)) -> dict:
    """Apply an edit to ONE line item by publishing a NEW version.

    Every authorable field on a line item arrives here; ``key``, ``children`` and ``aliases_i18n``
    are the three that do not, each for a reason served as ``vocab.not_editable``.

    Versioned rather than in-place: an extraction run pins the exact version it used
    (``extraction_runs.line_item_version_id``), so mutating a stored definition would retroactively
    change how past runs are explained. The edit is re-validated against the target template before
    it is published, so the editor cannot persist a configuration the pipeline would then reject.

    PRESENCE DECIDES WHAT CHANGES, not truthiness. A field left out of the body is untouched, an
    explicit ``null`` writes "nothing was said" where the schema has such a state, and ``[]`` /
    ``""`` is a configured empty that is stored as empty. A refusal names the field and, in a list,
    the entry — and a refused edit writes nothing at all.
    """
    from app.db.models import LineItemVersion

    row = session.get(LineItemVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line-item version not found")

    definition = copy.deepcopy(row.definition or {})
    items = definition.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="This version has no items to edit")

    # The POSITION as well as the dict: pydantic reports an error on `items.<i>.<field>`, and `i` is
    # how a refusal is told apart from a problem some OTHER item in the set was already carrying.
    # Taken from the walk rather than with `.index`, which compares by value.
    target_at = next((i for i, d in enumerate(items)
                      if isinstance(d, dict) and d.get("key") == body.key), None)
    if target_at is None:
        raise HTTPException(status_code=404,
                            detail=f"Line item {body.key!r} is not in this version")
    target = items[target_at]

    known = {d.get("key") for d in items if isinstance(d, dict)}
    sent = body.model_fields_set
    errors: list[dict] = []

    # A FIELD NO SCREEN CAN AUTHOR IS NOT WRITABLE THROUGH THE API EITHER.
    #
    # WHY THIS EXISTS. Retiring a control used to mean only that the invitation to set it here was
    # gone: the field kept working, the shipped values kept driving extraction, and the endpoint
    # kept ACCEPTING it. That is a half-measure, and the half that is left is the dangerous one — a
    # field writable by an API call and visible on no screen is a value nobody can see, review, or
    # explain, and the next author reading the configuration finds a figure driven by something the
    # console says is not configurable.
    #
    # THE RULE IS "NO SURFACE OFFERS IT", not "the Line Items screen retired it", and the
    # difference is load-bearing in two directions:
    #
    #   * `value_scope`, `confusable_with` and `exclude_hints` are retired on the Line Items screen
    #     and are STILL WRITABLE, because `screens/Template.tsx` sends all three through this very
    #     endpoint (`api.editLineItem`). Refusing them would break that screen's save.
    #   * `cascade`, `implemented_by` and `terms` are retired there too and are still writable,
    #     because `requiredNow` FORCES them back onto the form for a derived or calculated line —
    #     without them the save is refused with no control on screen to answer the refusal.
    #
    # THE SHIPPED VALUES ARE UNTOUCHED. This refuses a WRITE; it does not delete a field, and the
    # pipeline goes on reading whatever the stored set declares. What can no longer happen is a NEW
    # value arriving for a question the configuration surface no longer asks.
    for field in sorted(sent & set(_NOT_CONFIGURABLE)):
        errors.append(_err(field, f"`{field}` is no longer configurable — "
                                  f"{_NOT_CONFIGURABLE[field]}"))
    # EVERY PROBLEM IN ONE PASS, and NOTHING WRITTEN UNTIL THERE ARE NONE. The writes are staged
    # here and applied to `target` at the end, so a refused edit leaves the definition exactly as
    # it was — the property `test_the_controls_the_screen_withholds_are_the_ones_the_server_refuses`
    # asserts — and an author fixing three fields is told about three, not told about the first,
    # then the second, then the third.
    writes: dict[str, object] = {}

    def compile_all(values: list[str], field: str) -> None:
        """Refuse a pattern that does not compile, saying WHICH entry.

        A torn pattern is a SILENT hole rather than a crash: an exclusion that does not compile
        simply stops excluding, and the wrong rows get summed into a figure nobody can trace back
        (see `schemas.line_items._refuse_uncompilable` — splitting a shipped 34-alternative regex
        on `|` tore `^\\s*at\\s+(?:1|31)` into two fragments matching nothing). The index is here
        because a list of twenty regexes with one bad entry is not a list an author can eyeball.
        """
        for i, pattern in enumerate(values):
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(_err(field, f"{pattern!r} is not a valid regex: {exc}", i))

    def keys_exist(values: list[str], field: str) -> None:
        """Refuse an entry that names no line item in this set.

        Every reader of these lists compares an entry against a key that was MATCHED, so an entry
        that is not a key matches nothing and the relation it declares is silently unenforced —
        indistinguishable from a filing that never triggered it. That is the same requirement the
        publish gate applies to `children_if_decomposed`, applied at the field an author typed.

        The PIPE is called out separately, as it is at the gate: the generated rulebook shipped 30
        of 31 carriers with every child packed into one pipe-joined string, so the whole file
        produced ONE decomposition instead of 25. That is a splitting mistake, not a typo in one
        name, and saying so points at the fix.
        """
        for i, value in enumerate(values):
            if not value:
                continue
            if "|" in value:
                errors.append(_err(field, f"{value!r} contains '|', so it names no key — list "
                                          f"each entry separately", i))
            elif value not in known:
                errors.append(_err(field, f"{value!r} is not a key in this set, so the relation "
                                          f"it declares can never be enforced", i))

    # ── the three general tables ──────────────────────────────────────────────────────────────
    for field, into in _EDIT_SCALARS.items():
        if field not in sent:
            continue                            # ABSENT: do not touch
        value = getattr(body, field)
        if value is None and into not in _NULLABLE_ON_DEF:
            errors.append(_err(field, f"{field} has no 'nothing was said' state on a line item — "
                                      f"send {_clear_hint(into)} to clear it"))
            continue
        writes[into] = value

    for field, into in _EDIT_LISTS.items():
        if field not in sent:
            continue
        value = getattr(body, field)
        if value is None:
            errors.append(_err(field, f"{field} is a list, so it has no null state — send [] to "
                                      f"clear it, and [] is stored as empty"))
            continue
        writes[into] = _clean_list(value)

    for field, into in _EDIT_MODELS.items():
        if field not in sent:
            continue
        model = getattr(body, field)
        # `null` DISABLES the object, and that is a real configuration: a residual with no policy
        # of its own, an item read off no note. Written rather than dropped, because dropping it
        # would leave whatever the section layer says in force.
        writes[into] = None if model is None else model.model_dump(mode="json")

    for field, into in _EDIT_MODEL_LISTS.items():
        if field not in sent:
            continue
        rows = getattr(body, field)
        if rows is None:
            errors.append(_err(field, f"{field} is a list, so it has no null state — send [] to "
                                      f"clear it"))
            continue
        writes[into] = [r.model_dump(mode="json") for r in rows]

    # ── aliases: LOCALE-SCOPED, and the contract is exactly as it was ─────────────────────────
    if "aliases" in sent:
        if body.aliases is None:
            errors.append(_err("aliases", "aliases is a list, so it has no null state — send [] "
                                          "to clear this locale's aliases"))
        else:
            cleaned = _clean_list(body.aliases)
            locale = body.locale or definition.get("locale") or "en"
            i18n = dict(target.get("aliases_i18n") or {})
            i18n[locale] = cleaned
            writes["aliases_i18n"] = i18n
            # The base list mirrors the default locale (what non-localized consumers read). This
            # is the whole of the locale contract: ONE locale's list is replaced, so editing zh
            # cannot clobber en, and only the default locale also writes the base list.
            if locale == (definition.get("locale") or "en"):
                writes["aliases"] = cleaned

    # ── the sign fields, which are two different questions ───────────────────────────────────
    # `sign_rule` first, then the legacy 3-token spelling patches `convention` on whatever object
    # results — so a body sending both ends up with the legacy value in force rather than with the
    # order of two `if`s deciding it.
    if "sign_convention" in sent and body.sign_convention is not None:
        mapped = _SIGN_FROM_UI.get(body.sign_convention)
        if mapped is None:
            errors.append(_err("sign_convention",
                               f"unknown sign convention {body.sign_convention!r}; expected one "
                               f"of {sorted(_SIGN_FROM_UI)} (the legacy 3-token spelling), or "
                               f"send `sign_rule.convention` for the full vocabulary"))
        else:
            staged = writes.get("sign_rule", target.get("sign_rule"))
            rule = dict(staged or {})
            rule["convention"] = mapped
            writes["sign_rule"] = rule
    if "sign_rule" in sent and body.sign_rule is not None:
        # `SignRule` does NOT compile its own patterns — `LineItemDef._coherent` does, at publish,
        # as an item-level error naming a path the author never typed. Compiled here so the
        # offending entry is named, because a silent sign inversion is one of the most expensive
        # errors on a statement and this is the only field that causes one. Dotted field name: the
        # index belongs to `flip_if_label_matches`, not to the object.
        compile_all(body.sign_rule.flip_if_label_matches, "sign_rule.flip_if_label_matches")

    # ── the gate ─────────────────────────────────────────────────────────────────────────────
    if "statement" in sent:
        # `""` and `null` both clear the gate — "claimable on any statement", which is what `None`
        # means in the schema. A CONFIGURED EMPTY VALUE MEANS NOTHING, never "fall back to a
        # default", so it is written as null rather than dropped (dropping it would re-expose the
        # item to whatever its section layer declares).
        statement = (body.statement or "").strip() or None
        allowed = [s.value for s in StatementType]
        if statement is not None and statement not in allowed:
            errors.append(_err("statement", f"{statement!r} is not a statement; expected one of "
                                            f"{allowed}, or '' for 'claimable anywhere'"))
        else:
            writes["statement"] = statement
    if "statements" in sent:
        # THE SAME TREATMENT AS `section_scope`, because it is the same kind of declaration: a list
        # whose EMPTY value is a real configuration ("claimable on any statement") and whose null is
        # not. Validated member by member so a typo is named rather than silently narrowing the gate
        # to nothing — an unrecognised statement would make `_statements_of` return a set the gate
        # can never match, which refuses the concept everywhere and looks like a matching failure.
        if body.statements is None:
            errors.append(_err("statements", "statements is a list, so it has no null state — "
                                             "send [] for 'claimable on any statement'"))
        else:
            allowed = [s.value for s in StatementType]
            bad = [v for v in _clean_list(body.statements) if v not in allowed]
            if bad:
                errors.append(_err("statements", f"{bad!r} are not statements; expected values "
                                                 f"from {allowed}"))
            else:
                writes["statements"] = _clean_list(body.statements)
    if "section_scope" in sent:
        if body.section_scope is None:
            errors.append(_err("section_scope", "section_scope is a list, so it has no null "
                                                "state — send [] for 'unconstrained'"))
        else:
            # An empty list is UNCONSTRAINED here, by the schema's own rule — and it is stored, not
            # skipped, for the same reason.
            writes["section_scope"] = _clean_list(body.section_scope)
    # `note_source`'s THREE PATTERN GROUPS are compile-checked, but not here: `NoteSource` carries
    # its own `_patterns_compile` validator, so the body never parses at all when one of them does
    # not compile and FastAPI answers 422 with `loc: ["body", "note_source"]` and the group plus
    # index in the message ("row_caption_any[0] '(bad': missing )"). Re-checking it below the model
    # would be unreachable code pretending to be a guard. That the check lives on the model is the
    # point of typing this field on `NoteSource` rather than on `dict`: this object REPLACED the
    # 162-alternative `_QUALIFYING_RE` whitelist that refused a filing writing "Depreciation charge
    # for the year", and a torn pattern here is a silent hole rather than a crash.

    # ── recognition: the regex fields, each compiled where it is written ─────────────────────
    for field in ("regex_hints", "exclude_hints"):
        if field not in sent:
            continue
        value = getattr(body, field)
        if value is None:
            errors.append(_err(field, f"{field} is a list, so it has no null state — send [] to "
                                      f"clear it"))
            continue
        cleaned = _clean_list(value)
        # `exclude_hints` was accepted before and NOT compiled, unlike `regex_hints` — the one
        # asymmetry that mattered, because a torn veto stops vetoing in silence.
        compile_all(cleaned, field)
        writes[field] = cleaned
    if "pattern" in sent:
        pattern = body.pattern or ""            # `null` and `""` both mean "no pattern"
        compile_all([pattern] if pattern else [], "pattern")
        writes["pattern"] = pattern

    # ── the keys other declarations name ─────────────────────────────────────────────────────
    for field in ("expected_components", "never_sweep", "children_if_decomposed"):
        if field not in sent:
            continue
        value = getattr(body, field)
        if value is None:
            errors.append(_err(field, f"{field} is a list, so it has no null state — send [] to "
                                      f"clear it"))
            continue
        cleaned = _clean_list(value)
        keys_exist(cleaned, field)
        writes[field] = cleaned
    if "sole_component_of" in sent:
        # Nullable, and `""` means the same thing: this line is the sole component of nothing.
        sole = (body.sole_component_of or "").strip() or None
        if sole is not None and sole not in known:
            errors.append(_err("sole_component_of",
                               f"{sole!r} is not a key in this set, so the subtotal this line "
                               f"claims to be the sole component of does not exist"))
        else:
            writes["sole_component_of"] = sole
    for field in ("terms", "cascade"):
        if field not in sent or getattr(body, field) is None:
            continue
        for i, entry in enumerate(getattr(body, field)):
            # A cascade rung's terms are the same `Term` shape, so the same check reaches both —
            # a `ref` naming nothing is a term that silently contributes nothing to the sum.
            for term in (entry.terms if field == "cascade" else [entry]):
                if term.ref and term.ref not in known:
                    errors.append(_err(field, f"term ref {term.ref!r} is not a key in this set, "
                                              f"so it would contribute nothing to this formula",
                                       i))

    # ── the tree ─────────────────────────────────────────────────────────────────────────────
    if "parent" in sent:
        # `""` and `null` both make the item a ROOT. Refused on self-reference and on any cycle:
        # `build` reports a parent that is not a line item as a problem rather than a load error,
        # so an unchecked reparent would publish a set with a broken tree and no refusal at all.
        parent = (body.parent or "").strip()
        if not parent:
            writes["parent"] = ""
        elif parent == body.key:
            errors.append(_err("parent", "a line item cannot be its own parent"))
        elif parent not in known:
            errors.append(_err("parent", f"{parent!r} is not a key in this set"))
        else:
            # Walk upward with the edit APPLIED, over this set's own keys, so a cycle the edit
            # would create is caught before it is stored.
            parents = {d.get("key"): (d.get("parent") or "")
                       for d in items if isinstance(d, dict)}
            parents[body.key] = parent
            chain, cursor = [body.key], parent
            while cursor:
                if cursor in chain:
                    errors.append(_err("parent", f"that would make a cycle: "
                                                 f"{' -> '.join(chain + [cursor])}"))
                    break
                chain.append(cursor)
                cursor = parents.get(cursor, "")
            else:
                writes["parent"] = parent

    if errors:
        _refuse(errors)
    target.update(writes)

    # `namespace` is the attribution that depends on the edit: flipping it to `template` on a key
    # the template does not declare is refused by the publish gate, and told about `key` — which
    # is not editable — the author reads it as "the key is wrong".
    out = _publish_new_version(session, row, definition,
                               key_field="namespace" if "namespace" in sent else "key",
                               edited_key=body.key, edited_index=target_at,
                               session_id=principal.session_id)
    out["key"] = body.key
    return out


class ItemCreate(BaseModel):
    """A NEW line item, added beyond what the template asked for.

    Only `key` and `label` are taken. Everything else is configured afterwards through the edit
    endpoint, which is the one place that knows how to validate each field and how to attribute a
    refusal to the control that caused it — a create that accepted the whole shape would be a
    second, thinner spelling of that.
    """

    key: str
    label: str | None = None
    inherits: str | None = None


class SetEdit(BaseModel):
    """A change to the configuration ITSELF rather than to one of its lines.

    Only the master prompt for now. Kept deliberately narrow: every other set-level block
    (`global_rules`, `binding`, `normalisation`, `residual_framework`) is a structure whose editing
    needs its own controls and its own refusals, and a body that accepted all of them would be a
    second, unvalidated door into the whole configuration.
    """

    prompt: str | None = None


@router.patch("/versions/{version_id}", dependencies=[_GATE])
def edit_line_item_set(version_id: str, body: SetEdit,
                       session: Session = Depends(db),
                       principal: Principal = Depends(current_principal)) -> dict:
    """Edit the configuration's own settings by publishing a NEW version.

    THE MASTER PROMPT lives here because it applies to every mapping call rather than to one line:
    `mapping._build_system` appends it to the framework's base instruction, before the policies. A
    per-line prompt is the other half and goes through `PATCH .../items` — the two compose, and
    neither replaces the base instruction, whose shape the reply parser depends on.

    Versioned like every other edit: a run pins the version it used, so the prompt a past run was
    given stays readable rather than being rewritten under it.
    """
    from app.db.models import LineItemVersion

    row = session.get(LineItemVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line-item version not found")

    sent = body.model_fields_set
    if not sent:
        _refuse([_err(None, "nothing was sent to change")], what="This edit was not applied")

    definition = copy.deepcopy(row.definition or {})
    if "prompt" in sent:
        if body.prompt is None:
            # A string field has no "nothing was said" state; empty is a configured empty and is
            # stored as one, in keeping with every other block on the set.
            _refuse([_err("prompt", "prompt is text, so it has no null state — send \"\" to "
                                    "clear it, and an empty prompt adds nothing to the "
                                    "instruction")])
        definition["prompt"] = body.prompt

    return _publish_new_version(session, row, definition,
                                session_id=principal.session_id)


@router.post("/versions/{version_id}/items", status_code=201, dependencies=[_GATE])
def add_line_item(version_id: str, body: ItemCreate,
                  session: Session = Depends(db),
                  principal: Principal = Depends(current_principal)) -> dict:
    """Add a line item, as `namespace: "internal"`, by publishing a NEW version.

    The rule this serves: a template provisions its own lines, and an author may ADD to that set
    but never delete from it. So everything created here is `internal` — the author's own — and
    `template` is not a namespace a request can ask for. A key the template DOES declare is refused
    rather than quietly created as internal, because that item already exists and the author means
    to edit it.
    """
    from app.db.models import LineItemVersion

    row = session.get(LineItemVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line-item version not found")

    definition = copy.deepcopy(row.definition or {})
    items = definition.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="This version has no items to add to")

    key = (body.key or "").strip()
    if not key:
        _refuse([_err("key", "a line item needs a key")], what="This item was not added")
    if any(d.get("key") == key for d in items):
        _refuse([_err("key", f"{key!r} already exists in this configuration — edit it instead")],
                error="duplicate_key", what="This item was not added")

    item: dict = {"key": key, "label": (body.label or "").strip() or key,
                  "namespace": "internal", "type": "extracted", "in_output": False}
    if body.inherits:
        # Optional, and unvalidated here on purpose: a dangling `inherits` is caught by the
        # RESOLVED load inside `_publish_new_version` and reported against the `inherits` control,
        # which is a better message than anything this endpoint could invent.
        item["inherits"] = body.inherits
    # `in_output` defaults FALSE for an added item. A new line is not part of the deliverable until
    # somebody says so, and a template's own lines are the ones that are — provisioning sets it
    # true for those. Defaulting true here would silently widen the output on every addition.

    items.append(item)
    out = _publish_new_version(session, row, definition,
                               edited_key=key, edited_index=len(items) - 1,
                               session_id=principal.session_id)
    out["key"] = key
    return out


@router.delete("/versions/{version_id}/items/{key}", dependencies=[_GATE])
def delete_line_item(version_id: str, key: str,
                     session: Session = Depends(db),
                     principal: Principal = Depends(current_principal)) -> dict:
    """Delete an item — refused when the TEMPLATE put it there.

    THE REFUSAL IS SERVER-SIDE, not a hidden button. A template line's item exists because the
    deliverable has a column for that figure; removing it would leave the output with a line
    nothing can ever fill, and the loss would show up as a blank cell rather than as an error.
    Hiding the control in the UI leaves the same delete one API call away, so the rule lives here.

    Protection FOLLOWS THE TEMPLATE. `services.provision_line_items` demotes an item to
    `internal` when a re-uploaded template no longer carries its line, which is what makes that
    item deletable from then on — the author can discard it deliberately, having kept the aliases
    and criteria authored on it in the meantime.
    """
    from app.db.models import LineItemVersion
    from app.services.provision_line_items import protected_keys

    row = session.get(LineItemVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line-item version not found")

    definition = copy.deepcopy(row.definition or {})
    items = definition.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="This version has no items to delete from")

    at = next((i for i, d in enumerate(items) if d.get("key") == key), None)
    if at is None:
        raise HTTPException(status_code=404, detail=f"No line item {key!r} in this configuration")

    if key in protected_keys(definition):
        raise HTTPException(
            status_code=409,
            detail={"error": "template_item_protected",
                    "message": (f"{key!r} is a line of the target template, so its configuration "
                                f"cannot be deleted — the output has a column for that figure. "
                                f"Remove the line from the template and re-publish it, and this "
                                f"item becomes an ordinary one you can delete."),
                    "key": key})

    # Anything that NAMED the deleted item now names nothing. Reported rather than repaired: a
    # rollup term or a `sole_component_of` pointing at a removed key is a decision the author has
    # to make, and the resolved load inside `_publish_new_version` refuses it with the offending
    # control named — so this is a check that produces a better message, not a different outcome.
    referrers = [d.get("key") for d in items
                 if d.get("key") != key and key in json.dumps(d, ensure_ascii=False)]
    items.pop(at)
    out = _publish_new_version(session, row, definition,
                               session_id=principal.session_id)
    out["deleted"] = key
    if referrers:
        out["now_dangling_references_in"] = referrers
    return out


# ── the schema help index ─────────────────────────────────────────────────────────────────────
#
# Ported wholesale from ``services/ontology_skeleton.py`` (``field_help`` and its helpers), whose
# only other caller was the skeleton download that is gone with the ontology route. Generated from
# the models, so a field added to the schema is described the moment it exists and a renamed one
# cannot leave a stale path behind. The alternative — a curated list beside the models — is exactly
# the drift this endpoint exists to remove.

# The four blocks an author writes by hand, expanded field by field: the set root, the per-item
# definition, the section layer an item claims through ``inherits``, and the scoping vocabulary the
# merge brought over. Everything reachable below them is NAMED by its own entry (see ``_type_phrase``)
# rather than expanded, so the index stays flat enough to read in one pass.
_HELP_BLOCKS: tuple[tuple[type[BaseModel], str], ...] = (
    (LineItemSet, ""),
    (LineItemDef, "items[]."),
    (SectionDefaults, "section_defaults.<section_id>."),
    (MappingVocabulary, "vocabulary."),
)

_NONE = type(None)
_UNIONS = (typing.Union, types.UnionType)
_SCALARS: dict[Any, str] = {str: "text", bool: "true | false", int: "whole number",
                            float: "number", dict: "object", list: "list"}


def _is_model(ann: Any) -> bool:
    return isinstance(ann, type) and issubclass(ann, BaseModel)


def _first_line(doc: str | None) -> str:
    """First line of a docstring, whitespace-collapsed; '' when there is none."""
    for line in (doc or "").strip().splitlines():
        if line.strip():
            return " ".join(line.split())
    return ""


def _as_json(value: Any) -> str:
    """A value spelled the way it has to be typed into the file, not the way Python prints it.

    ``True`` and ``'exclusive_leaf'`` are not what an author writes; ``true`` and
    ``"exclusive_leaf"`` are, and a help text that shows the other one invites a 422.
    """
    return json.dumps(value, default=str, ensure_ascii=False)


def _type_phrase(ann: Any) -> str:
    """A one-line rendering of an annotation, in the vocabulary the JSON actually uses."""
    origin, args = get_origin(ann), get_args(ann)
    if origin is Literal:
        return "one of " + " | ".join(_as_json(a) for a in args)
    if origin in _UNIONS:
        present = [a for a in args if a is not _NONE]
        phrase = " or ".join(_type_phrase(a) for a in present)
        # ``None`` on an optional field is not "false" or "empty" — it is the absence of a
        # statement, and the section layer folds on exactly that distinction.
        return phrase + ("; null = nothing said" if len(present) != len(args) else "")
    if origin is list:
        return f"list[{_type_phrase(args[0])}]" if args else "list"
    if origin is dict:
        return (f"object keyed by {_type_phrase(args[0])} of {_type_phrase(args[1])}"
                if args else "object")
    if isinstance(ann, type) and issubclass(ann, enum.Enum):
        return "one of " + " | ".join(_as_json(m.value) for m in ann)
    if _is_model(ann):
        keys = ", ".join(f.alias or n for n, f in ann.model_fields.items())
        return f"{ann.__name__}{{{keys}}}"
    return _SCALARS.get(ann, getattr(ann, "__name__", str(ann)))


def _nested_doc(ann: Any) -> str:
    """The docstring of the model this field holds, through Optional / list / dict wrappers.

    That docstring is where the schema explains WHY a block exists (why a residual repeats the
    sweep terms, why ``with`` is aliased), which is the part an author cannot infer from the
    field name.
    """
    if _is_model(ann):
        return _first_line(ann.__doc__)
    args = [a for a in get_args(ann) if a is not _NONE]
    if get_origin(ann) is dict:
        args = args[1:]
    for arg in args:
        doc = _nested_doc(arg)
        if doc:
            return doc
    return ""


def _field_help() -> list[dict]:
    """A flat path → help index of the fields an author fills in."""
    out: list[dict] = []
    for model, prefix in _HELP_BLOCKS:
        for name, field in model.model_fields.items():
            parts = [field.description or _nested_doc(field.annotation),
                     _type_phrase(field.annotation)]
            # A factory default is a fresh list/object — the type phrase already says so, and
            # printing "defaults to []" beside it adds nothing.
            if not field.is_required() and field.default_factory is None \
                    and field.default is not None:
                parts.append(f"defaults to {_as_json(field.default)}")
            out.append({
                "path": f"{prefix}{field.alias or name}",
                "required": field.is_required(),
                "help": " ".join(p for p in parts if p),
            })
    return out
