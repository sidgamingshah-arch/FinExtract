"""FastAPI application entrypoint.

Wires the routers under the configured API prefix and registers built-in adapters.
Run locally with: ``uvicorn app.main:app --reload`` (from the ``backend`` dir).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.adapters  # noqa: F401 - registers built-in adapters on import
from app.api.routes import (
    audit,
    auth,
    documents,
    extractions,
    fx_rates,
    languages,
    projects,
    settings as settings_routes,
    templates, line_items,
)
from app.config import get_settings
from app.db.base import init_db


_LOG = logging.getLogger(__name__)


def _warn_unloadable_line_item_versions(session) -> int:
    """One WARNING per stored configuration that will not load. Returns how many there were.

    Probed through ``routes.line_items.probe_line_item_load``, the same function that answers the
    ``loads`` field on ``GET /line-items/versions``, so the boot log and the picker can never name
    different rows as broken. It populates that endpoint's cache on the way through, which also
    means the first poll after a boot does not pay for the probe. The rows come from that module's
    ``_all_versions`` for the same reason: probing the same rows with the same function is what
    makes the two answers one answer, and a second ``select`` here could drift from the picker's.

    Never raises: a database full of configurations nobody can load is a state to REPORT, not a
    reason to refuse to boot — the shipped seed is validated separately (``ensure_reference_data``),
    and it is the one a run falls back to.

    This stood as ``_warn_unloadable_ontologies`` over ``ontology_versions``. Line items is now the
    single configuration engine, so there is one store to probe and no engine to choose between.
    """
    from app.api.routes.line_items import _all_versions, probe_line_item_load

    broken = 0
    try:
        rows = _all_versions(session)
    except Exception as exc:  # noqa: BLE001 - a boot-time read failure must not take the app down
        _LOG.warning("stored line-item versions could not be probed for loadability: %s", exc)
        return 0
    for row in rows:
        error = probe_line_item_load(row)
        if error is not None:
            broken += 1
            _LOG.warning(
                "stored line-item set %s v%s (%s) WILL NOT LOAD and cannot be used for a run: %s",
                row.line_items_key, row.version, row.id, error)
    if broken:
        _LOG.warning("%d of %d stored line-item versions will not load; republish or reconcile them "
                     "(scripts/reconcile_reference_data.py)", broken, len(rows))
    return broken


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version="0.1.0")

    # Permit the Vite dev server (and same-origin prod builds) to call the API.
    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.on_event("startup")
    def _startup() -> None:  # pragma: no cover - trivial
        # SAY WHAT THIS PROCESS ACTUALLY MOUNTED. `api_prefix` and `app_name` are config
        # (config.toml, or FINEX_API_PREFIX / FINEX_APP_NAME), and until now nothing echoed the
        # effective value — the two keys shipped inside an `[app]` table that bound to nothing,
        # so a deployment that set a prefix got the /api/v1 default with no way to see it. Both
        # bind now; this is the confirmation. `print` rather than `_LOG.info` for the reason the
        # line-item warning below records: this app configures no logging, so only WARNING and
        # above reaches stderr — the same reason `install_shipped_caption_inventory(log=print)`
        # prints.
        print(f"[startup] {settings.app_name!r} serving the API under {settings.api_prefix!r}")
        init_db()
        # Re-apply the administrator's saved settings (feature flags, LLM config, extraction
        # thresholds) onto this process. After init_db so the table exists on a fresh database.
        from app.services.settings_state import load_persisted

        load_persisted()
        # Seed the reference template + line-item set so uploaded docs can be mapped out of the box.
        from app.db.base import SessionLocal
        from app.sample.reference import ensure_reference_data

        with SessionLocal() as session:
            ensure_reference_data(session)
            # SAY SO WHEN A STORED CONFIGURATION NO LONGER LOADS. `ensure_reference_data` validates
            # the DISK seed and nothing else, and /health is a static {"status": "ok"}, so a stored
            # row the schema has since stopped accepting was invisible everywhere. This is not
            # hypothetical: the retired ontology store reached 41 of 44 rows that no longer loaded
            # — all of them on the same `never_sweep: ["True"]` entries the schema had stopped
            # accepting — because the seed was repaired and republished while the rows already
            # stored were never migrated. `line_item_versions` is append-only and can drift exactly
            # the same way, which is why the probe travels with the row and is read here at boot.
            # Reported at WARNING because this app configures no logging: only WARNING and above
            # reaches stderr through `logging.lastResort`.
            _warn_unloadable_line_item_versions(session)

        # INSTALL THE CAPTION FOLD FROM CONFIG. `normalize_label` builds its eight patterns from a
        # character inventory — the bracket widths, the quote marks that mark a coined
        # abbreviation, the CAS enumerators and sign-note words, the Han code-point ranges. Those
        # are what a FILING varies, and until now they were module constants no rulebook could
        # reach: a filing glossing with ＂ or ﹁﹂ rather than the twelve shipped marks lost the
        # gloss, and the caption then matched nothing exactly. Measured: with that one pattern
        # disabled, 1,050 of 1,993 rulebook captions resolve differently and 74 land on a
        # DIFFERENT concept.
        #
        # ONCE, AT STARTUP, AND ONLY HERE. The fold has to be symmetric — it is applied to every
        # alias when the index is built and to every caption matched against it — so one process
        # holds exactly one inventory and `install_caption_inventory` refuses a conflicting
        # second. Doing it here rather than at import keeps `services.mapping` free of a
        # dependency on the seed, and keeps a test that builds a set by hand on the built-in.
        from app.services.line_item_config import install_shipped_caption_inventory

        install_shipped_caption_inventory(log=print)

    @application.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "app": settings.app_name}

    prefix = settings.api_prefix
    application.include_router(documents.router, prefix=prefix)
    application.include_router(extractions.router, prefix=prefix)
    application.include_router(templates.router, prefix=prefix)
    # `ontologies.router` was mounted here. Line items is the single configuration engine now:
    # everything the frontend and any API consumer needs comes from /line-items, so there is no
    # second surface to mount and nothing to reinstate.
    application.include_router(line_items.router, prefix=prefix)
    application.include_router(fx_rates.router, prefix=prefix)
    application.include_router(languages.router, prefix=prefix)
    application.include_router(projects.router, prefix=prefix)
    application.include_router(auth.router, prefix=prefix)
    application.include_router(settings_routes.router, prefix=prefix)
    application.include_router(audit.router, prefix=prefix)
    return application


app = create_app()
