"""Supported-languages endpoint — surfaces the multilingual parity registry.

Optionally scoped to a template + a LINE-ITEM SET, so the UI knows which languages are fully
supported (input = output parity) for a given extraction configuration.

WHAT WAS HERE AND IS GONE, so nobody reinstates it. This module used to take an
``ontology_version_id``, read ``ontology_versions`` and report parity against a rulebook. There is
no second configuration engine any more: line items is the one place configuration lives, so the
scope is a ``line_item_version_id``, the store is ``line_item_versions``, and the alias set parity
is measured over is the MATCHER'S WORKING VIEW of that set (``services.working_view``) — the same
object the mapper builds, so this screen reports the aliases a real run would actually index.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import db
from app.config import get_settings
from app.schemas.languages import evaluate_parity
from app.schemas.loader import load_template

router = APIRouter(prefix="/languages", tags=["languages"])

_LOG = logging.getLogger(__name__)


def _working_view(row):
    """The matcher's working view of a stored line-item set.

    ``load_line_item_set`` then ``build_working_view``, exactly as the mapper and
    ``documents._run_rulebook`` build it, so parity is measured over the concepts a run would
    really index rather than over a second reading of the same rows. Resolved (the loader's
    default), because the section layer a set authors once per section only reaches a concept
    through the fold.
    """
    from app.schemas.line_items import load_line_item_set
    from app.services.working_view import build_working_view

    return build_working_view(load_line_item_set(row.definition))


def _loaded_or_none(row):
    """A stored line-item set as a working view, or ``None`` plus a WARNING naming the row.

    For the DEFAULT path only, where there is no caller to refuse: see the call site for why the
    same failure is a 422 when the request named the configuration itself.
    """
    try:
        return _working_view(row)
    except Exception as exc:  # noqa: BLE001 — any failure to load is the same answer
        _LOG.warning(
            "language parity: stored line-item set %s v%s (%s) will not load, so it cannot describe "
            "the aliases a run would use and is not counted: %s",
            row.line_items_key, row.version, row.id, str(exc).strip().splitlines()[0])
        return None


def _seed_rulebook(session: Session):
    """The newest stored version of the configuration this repo SHIPS, or ``None``.

    Read from the store rather than from the file so the parity report describes a configuration a
    run could actually pin, and keyed on ``shipped_line_items_key()`` so "which configuration is
    ours" keeps one spelling (``output_csv_hk``). ``ensure_reference_data`` validates that file at
    start-up, so the row it seeds loads by construction — but it is still loaded through the same
    guard, because a database can hold a seed from an older schema.
    """
    from sqlalchemy import select as _select

    from app.db.models import LineItemVersion
    from app.sample.reference import shipped_line_items_key

    key = shipped_line_items_key()
    if not key:
        return None
    row = session.execute(
        _select(LineItemVersion).where(LineItemVersion.line_items_key == key)
        .order_by(LineItemVersion.version.desc())
    ).scalars().first()
    return _loaded_or_none(row) if row is not None else None


@router.get("")
def list_languages(
    template_version_id: str | None = Query(None),
    line_item_version_id: str | None = Query(None),
    session: Session = Depends(db),
) -> dict:
    from app.db.models import LineItemVersion, TemplateVersion

    from sqlalchemy import select

    template = None
    ontology = None                       # the WORKING VIEW of the set; never a stored rulebook
    if template_version_id:
        row = session.get(TemplateVersion, template_version_id)
        if row:
            template = load_template(row.definition)
    if line_item_version_id:
        row = session.get(LineItemVersion, line_item_version_id)
        if row:
            # A NAMED CONFIGURATION THAT WILL NOT LOAD IS A 422, NOT A 500 AND NOT A SHRUG.
            #
            # THE DEFECT THIS CLOSES: this call was bare, and this module has no handler of its own
            # and no global one — the exact call site ``loader.unknown_keys`` names as having "no
            # handler at all (a 500)". A stored definition today's schema refuses is not rare: it is
            # what retired the ontology store, where 41 of 44 rows failed to load, and a
            # ``line_item_versions`` row written by an older schema can fail the same way, so any
            # caller naming one would get the pydantic error dump rendered as an unhandled server
            # error.
            #
            # Refused rather than fallen back to no configuration at all, which is the tempting fix
            # and the wrong one: with no set every locale's ``has_line_item_aliases`` is False, so a
            # request that NAMED a configuration would be answered with an all-False parity set —
            # "this configuration supports no language at all" — which is a statement about that
            # set's aliases that nobody measured. The caller asked about a specific configuration;
            # if it cannot be read, say so.
            try:
                ontology = _working_view(row)
            except Exception as exc:  # noqa: BLE001 — any failure to load is the same answer
                # The reason is read off the exception rather than from a shared loadability probe:
                # the probe lived on the ontology API, which is gone with the engine it served.
                raise HTTPException(status_code=422, detail={
                    "error": "rulebook_unloadable",
                    "message": (
                        f"Line-item set {row.line_items_key!r} v{row.version} cannot be loaded, so "
                        f"the languages it supports cannot be reported: "
                        f"{str(exc).strip().splitlines()[0]}"),
                    "line_item_version_id": row.id,
                    "line_items_key": row.line_items_key,
                    "version": row.version,
                }) from exc

    # No explicit config → evaluate parity against the latest seeded template + the line-item set
    # in force for it, so the default call reports the real supported set (not an all-False collapse
    # that the UI would otherwise have to paper over).
    if template is None:
        row = session.execute(
            select(TemplateVersion).order_by(TemplateVersion.version.desc())
        ).scalars().first()
        if row:
            template = load_template(row.definition)
    if ontology is None and template is not None:
        from app.services.config_select import select_for_template

        # The configuration IN FORCE, not merely the highest-numbered one — language parity has to
        # describe the aliases a real run would actually use. ``config_select`` is the one place
        # that rule lives now (it replaced ``ontology_select``, which is deleted).
        row = select_for_template(session, template.template_key)
        if row:
            # THE DEFAULT CALL IS NOT A REQUEST ABOUT THIS ROW, so it is not refused over it: the
            # caller asked "which languages does this product support", and answering 422 because
            # of a stored row they never named would take the language picker out with it. Logged
            # and fallen back to the seeded configuration, which ``ensure_reference_data`` validates
            # at start-up and which therefore loads by construction — still a real measured alias
            # set, which the all-False collapse this default exists to prevent is not.
            ontology = _loaded_or_none(row)
            if ontology is None:
                ontology = _seed_rulebook(session)

    # CONFIG NARROWS THIS SET; IT NEVER WIDENS IT.
    #
    # THE DEFECT THIS CLOSES: `features.supported_locales` was configuration that refused
    # nothing. It was read in exactly two places — the read-only echo in GET /settings
    # (api/routes/settings.py:59) and a frontend guard that ignores a `default_output_locale`
    # outside it (frontend/src/lib/queries.ts:61) — so shrinking the shipped four-element list
    # to ["en"] left the switcher still offering all four, because the switcher is driven by
    # `fully_supported` from THIS call and this call never looked at the config.
    #
    # Threaded in as the `locales` argument `evaluate_parity` has always taken and no caller
    # ever passed (it defaulted to the whole seed set). That argument can only INTERSECT
    # SEED_LANGUAGES — see its docstring — which is the whole reason the wiring is shaped this
    # way rather than as a validator: the per-template+configuration parity gate stays the thing
    # that decides `supported`, and a config typo cannot invent a locale. Deliberately NOT
    # extended into a request-time check on `?locale=`, which 18 endpoints accept and answer
    # with an English fallback (routes/documents.py `_t()`); turning that into a 400 would
    # front the per-configuration parity gate with a flat config list. Empty/absent list (hence
    # the `or None`) = no narrowing, registry untouched.
    parity = evaluate_parity(
        template, ontology,
        locales=get_settings().features.supported_locales or None,
    )
    return {
        "languages": [
            {**p.model_dump(), "supported": p.supported, "missing": p.missing}
            for p in parity
        ],
        "fully_supported": [p.locale for p in parity if p.supported],
    }
