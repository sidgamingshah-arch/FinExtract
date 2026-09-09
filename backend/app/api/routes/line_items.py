"""The configuration API. There is exactly one, and this is it.

Line items are the single configuration engine, so this module carries the WHOLE of what a user or
an API consumer can do to the thing a run maps against: read the version in force, list the stored
versions, download one, read the schema it must satisfy, publish a new one, and correct one item
inline. ``api/routes/ontologies.py`` stood beside this file and is DELETED — with it went the
skeleton download, the workbook download and upload, and the netting-rules editor (see
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
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db
from app.schemas.line_items import (
    LineItemDef,
    LineItemSet,
    MappingVocabulary,
    SectionDefaults,
    UnknownInheritsError,
    load_line_item_set,
)
from app.schemas.loader import load_template, unknown_keys
from app.security.rbac import Permission, require
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

    def payload(d) -> dict:
        out = d.model_dump(mode="json")
        out["children"] = [payload(k) for k in reg.children_of(d.key)]
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
            # Shown so a reader can see the gate is authored once per section rather than per
            # item — the two-layer model that survived the merge.
            "section_defaults": {k: v.model_dump(mode="json", exclude_none=True)
                                 for k, v in st.section_defaults.items()},
        },
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


def _validate_against_target_template(session: Session, st: LineItemSet) -> dict:
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
        if d.namespace == "template" and d.key not in template_keys:
            errors.append({"location": f"item:{d.key}",
                           "message": "key does not exist in the target template"})
        if d.analyst_bucket and d.analyst_bucket not in BUCKET_KEYS:
            errors.append({"location": f"item:{d.key}",
                           "message": (f"analyst_bucket {d.analyst_bucket!r} is not an analyst "
                                       f"section; expected one of {', '.join(BUCKET_KEYS)}")})
        for child in d.children_if_decomposed:
            if "|" in child:
                errors.append({"location": f"item:{d.key}",
                               "message": (f"children_if_decomposed entry {child!r} contains '|', "
                                           f"so it names no key — list each child separately")})
            elif child not in own_keys:
                errors.append({"location": f"item:{d.key}",
                               "message": (f"children_if_decomposed names {child!r}, which is not "
                                           f"a key in this set, so the containment it declares is "
                                           f"unenforceable")})
    for group in st.global_rules.mutually_exclusive_groups:
        gid = group.id or "mutually_exclusive_group"
        for role, key in ([("aggregate", group.aggregate)]
                          + [("component", c) for c in group.components]):
            if key and key not in own_keys:
                errors.append({"location": f"mutually_exclusive_group:{gid}",
                               "message": (f"{role} {key!r} is not a key in this set, so the "
                                           f"exclusivity it declares can never be enforced")})
    for rule in st.decomposition_rules:
        if rule.face_key not in template_keys:
            errors.append({"location": f"decomposition:{rule.id}",
                           "message": f"face_key {rule.face_key!r} does not exist in the template"})
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors[:50]})
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
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"location": p,
                                "message": "key is not part of the line-item schema"}
                               for p in stray]})

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
class ItemEdit(BaseModel):
    """An inline edit to ONE line item's matching rules. Only provided fields change.

    ``aliases`` is locale-scoped: it replaces that locale's ``aliases_i18n`` list (and the base
    ``aliases`` list when the locale is the set's own default), so editing the Chinese aliases can
    never silently clobber the English ones.
    """

    key: str
    locale: str | None = None
    aliases: list[str] | None = None
    sign_convention: str | None = None
    label: str | None = None
    description: str | None = None
    # The criteria the LLM actually reasons over. Aliases only help when the printed wording is
    # close to one; `definition`/`include_criteria`/`exclude_criteria`/`confusable_with` are what
    # let a caption be resolved by MEANING, so they have to be editable too or an analyst can only
    # ever tune string matching.
    definition: str | None = None
    include_criteria: list[str] | None = None
    exclude_criteria: list[str] | None = None
    confusable_with: list[str] | None = None
    value_scope: str | None = None
    # Lexical rule hints (regex / keyword), the deterministic tier's controls.
    keyword_hints: list[str] | None = None
    regex_hints: list[str] | None = None
    exclude_hints: list[str] | None = None
    # THE SECTION GATE, the half the merge brought over from the ontology: WHERE this item may be
    # claimed from. Editable because it is what decides whether a figure lands on the right line at
    # all — 420 of 1,969 shipped captions are claimed by more than one item, and 96 of those across
    # different statements, so `intangible assets` is separated by the gate and by nothing else.
    statement: str | None = None
    section_scope: list[str] | None = None


_VALUE_SCOPES = {"exclusive_leaf", "exclusive_child", "exclusive_residual", "not_applicable"}


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


def _publish_new_version(session: Session, row, definition: dict) -> dict:
    """Validate an edited definition and store it as the NEXT version of the same set.

    Shared by every inline edit so validation can never be skipped on one path: a run references the
    exact version it used, so an edit must ADD a version rather than mutate one — mutating a stored
    definition would retroactively change how a past run is explained.
    """
    from app.db.models import LineItemVersion

    try:
        st = load_line_item_set(definition, resolve=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422,
                            detail=f"Edit produced an invalid configuration: {exc}") from exc

    validated_against = _validate_against_target_template(session, st)

    max_ver = session.execute(
        select(func.max(LineItemVersion.version))
        .where(LineItemVersion.line_items_key == row.line_items_key)
    ).scalar()
    definition["line_items_key"] = row.line_items_key
    new_row = LineItemVersion(
        line_items_key=row.line_items_key,
        target_template_key=row.target_template_key,
        version=(max_ver or 0) + 1,
        definition=definition,
    )
    session.add(new_row)
    session.commit()
    return {**_version_identity(new_row),
            "validated_against_template": validated_against}


@router.patch("/versions/{version_id}/items", dependencies=[_GATE])
def edit_line_item(version_id: str, body: ItemEdit,
                   session: Session = Depends(db)) -> dict:
    """Apply an inline item edit by publishing a NEW version.

    Versioned rather than in-place: an extraction run pins the exact version it used
    (``extraction_runs.line_item_version_id``), so mutating a stored definition would retroactively
    change how past runs are explained. The edit is re-validated against the target template before
    it is published, so the editor cannot persist a configuration the pipeline would then reject.
    """
    from app.db.models import LineItemVersion

    row = session.get(LineItemVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Line-item version not found")

    definition = copy.deepcopy(row.definition or {})
    items = definition.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="This version has no items to edit")

    target = next((d for d in items if d.get("key") == body.key), None)
    if target is None:
        raise HTTPException(status_code=404,
                            detail=f"Line item {body.key!r} is not in this version")

    if body.aliases is not None:
        cleaned = _clean_list(body.aliases)
        locale = body.locale or definition.get("locale") or "en"
        i18n = dict(target.get("aliases_i18n") or {})
        i18n[locale] = cleaned
        target["aliases_i18n"] = i18n
        # The base list mirrors the default locale (what non-localized consumers read).
        if locale == (definition.get("locale") or "en"):
            target["aliases"] = cleaned

    if body.sign_convention is not None:
        mapped = _SIGN_FROM_UI.get(body.sign_convention)
        if mapped is None:
            raise HTTPException(status_code=422,
                                detail=f"Unknown sign convention {body.sign_convention!r}; "
                                       f"expected one of {sorted(_SIGN_FROM_UI)}")
        rule = dict(target.get("sign_rule") or {})
        rule["convention"] = mapped
        target["sign_rule"] = rule

    if body.label is not None:
        target["label"] = body.label
    if body.description is not None:
        target["description"] = body.description
    if body.definition is not None:
        target["definition"] = body.definition
    if body.value_scope is not None:
        if body.value_scope not in _VALUE_SCOPES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown value_scope {body.value_scope!r}; expected one of "
                       f"{sorted(_VALUE_SCOPES)}")
        target["value_scope"] = body.value_scope
    if body.confusable_with is not None:
        # These name OTHER line items; a typo would silently weaken the very disambiguation the
        # field exists for, so unknown keys are rejected rather than stored.
        known = {d.get("key") for d in items}
        unknown = [k for k in body.confusable_with if k and k not in known]
        if unknown:
            raise HTTPException(status_code=422,
                                detail=f"confusable_with names unknown line items: {unknown}")
        target["confusable_with"] = _clean_list(body.confusable_with)
    if body.statement is not None:
        # `""` clears the gate — "claimable on any statement", which is what `None` means in the
        # schema. A CONFIGURED EMPTY VALUE MEANS NOTHING, never "fall back to a default", so it is
        # written as null rather than dropped (dropping it would re-expose the item to whatever its
        # section layer declares).
        target["statement"] = body.statement or None
    if body.section_scope is not None:
        # An empty list is UNCONSTRAINED here, by the schema's own rule — and it is stored, not
        # skipped, for the same reason.
        target["section_scope"] = _clean_list(body.section_scope)
    for field in ("include_criteria", "exclude_criteria", "keyword_hints", "exclude_hints"):
        value = getattr(body, field)
        if value is not None:
            target[field] = _clean_list(value)
    if body.regex_hints is not None:
        # A bad pattern would raise inside the matcher on every future run, so it is compiled here
        # and refused now rather than breaking extraction later.
        cleaned = _clean_list(body.regex_hints)
        for pattern in cleaned:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise HTTPException(status_code=422,
                                    detail=f"Invalid regex {pattern!r}: {exc}") from exc
        target["regex_hints"] = cleaned

    out = _publish_new_version(session, row, definition)
    out["key"] = body.key
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
