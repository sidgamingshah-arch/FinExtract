"""API dependencies."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.base import get_session
from app.ports.object_store import LocalObjectStore


def db() -> Iterator[Session]:
    yield from get_session()


def settings() -> Settings:
    return get_settings()


def object_store() -> LocalObjectStore:
    return LocalObjectStore(get_settings().object_store_root)


def default_locale() -> str:
    """The deployment's configured output language — ``[features] default_output_locale``.

    ``or "en"``, so a blank value in config.toml resolves to the shipped default rather than to
    an empty locale that every ``tr()`` call would then miss on.
    """
    return get_settings().features.default_output_locale or "en"


def output_locale(locale: str | None = Query(None),
                  configured: str = Depends(default_locale)) -> str:
    """The output language for THIS request: the caller's ``?locale=``, else the configured default.

    WHAT WENT WRONG. ``features.default_output_locale`` was read in exactly one place — the GET
    /settings echo (routes/settings.py) — and honoured nowhere. All 18 locale-bearing endpoints
    (10 in routes/documents.py, 8 in routes/projects.py) declared their own literal
    ``locale: str = Query("en")``, so a HK/PRC deployment that set ``default_output_locale = "zh"``
    got English statements, notes and exports while the Settings screen reported "zh" as fact.

    WHY A DEPENDENCY RATHER THAN A LINE IN EACH HANDLER. Resolving inside the bodies would put an
    ``or`` on 18 handlers, and several of them read ``locale`` in an early-return branch before
    anything else happens (projects.get_review passes it to ``_demo_review_payload`` on the
    greenfield path, and to ``_sample_coverage`` on both) — one missed resolution line is a route
    that silently keeps its own default. Resolving here means the parameter a handler receives is
    already the effective locale, so no handler can get it wrong. FastAPI still collects the
    ``locale`` query parameter from this sub-dependency, so the wire contract and the OpenAPI
    schema are unchanged: an explicit ``?locale=`` always wins.
    """
    return locale or configured
