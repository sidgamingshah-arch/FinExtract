"""FastAPI application entrypoint.

Wires the routers under the configured API prefix and registers built-in adapters.
Run locally with: ``uvicorn app.main:app --reload`` (from the ``backend`` dir).
"""
from __future__ import annotations

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
    ontologies,
    projects,
    settings as settings_routes,
    templates, line_items,
)
from app.config import get_settings
from app.db.base import init_db


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
        init_db()
        # Re-apply the administrator's saved settings (feature flags, LLM config, extraction
        # thresholds) onto this process. After init_db so the table exists on a fresh database.
        from app.services.settings_state import load_persisted

        load_persisted()
        # Seed the reference template + ontology so uploaded docs can be mapped out of the box.
        from app.db.base import SessionLocal
        from app.sample.reference import ensure_reference_data

        with SessionLocal() as session:
            ensure_reference_data(session)

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
    application.include_router(ontologies.router, prefix=prefix)
    application.include_router(line_items.router, prefix=prefix)
    application.include_router(fx_rates.router, prefix=prefix)
    application.include_router(languages.router, prefix=prefix)
    application.include_router(projects.router, prefix=prefix)
    application.include_router(auth.router, prefix=prefix)
    application.include_router(settings_routes.router, prefix=prefix)
    application.include_router(audit.router, prefix=prefix)
    return application


app = create_app()
