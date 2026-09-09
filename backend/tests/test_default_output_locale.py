"""`[features] default_output_locale` is the language requests are answered in by default.

WHAT WAS WRONG. The setting was read in exactly ONE place — the GET /settings echo — and applied
by nothing. All 18 locale-bearing endpoints (10 in api/routes/documents.py, 8 in
api/routes/projects.py) declared their own literal `locale: str = Query("en")`, so a HK/PRC
deployment that set `default_output_locale = "zh"` and restarted got English statements, notes and
exports — while the Settings screen rendered "zh" as fact beside them. The value looked live
because it was served back.

It is now resolved once, in `deps.output_locale`, which every one of those routes takes its
`locale` from: the caller's `?locale=` first, the configured default second. Resolved in the
dependency rather than by a line in each handler on purpose — several handlers use `locale` in an
early-return branch before anything else runs (projects.get_review passes it to
`_demo_review_payload` on the greenfield path), so one missed resolution line would be a route
that silently kept its own default.
"""
from __future__ import annotations

import inspect

import pytest

from app.api import deps
from app.config import Settings


# ── the dependency ───────────────────────────────────────────────────────────────────────────────

def test_the_dependency_reports_the_configured_locale(monkeypatch):
    monkeypatch.setattr(deps, "get_settings",
                        lambda: Settings(features={"default_output_locale": "zh"}))

    assert deps.default_locale() == "zh"


def test_a_blank_configured_locale_falls_back_to_english(monkeypatch):
    """An empty string is not a locale: every `tr()` lookup would miss on it and the caller would
    get English anyway, but by accident rather than by rule."""
    monkeypatch.setattr(deps, "get_settings",
                        lambda: Settings(features={"default_output_locale": ""}))

    assert deps.default_locale() == "en"


def test_the_environment_reaches_the_dependency(monkeypatch):
    """The deployment path, end to end: config.toml / env → Settings → the routes' default.

    `get_settings` is `lru_cache`d, so the cache is cleared on the way in AND on the way out —
    leaving a "zh" Settings cached would hand it to every later test in the session.
    """
    from app.config import get_settings

    monkeypatch.setenv("FINEX_FEATURES__DEFAULT_OUTPUT_LOCALE", "zh")
    get_settings.cache_clear()
    try:
        assert deps.default_locale() == "zh"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert deps.default_locale() == "en", "the shipped default did not come back"


def test_an_explicit_request_locale_wins_over_the_configured_default():
    """`?locale=` is a caller's decision about one response; the setting is only the default."""
    assert deps.output_locale(locale="en", configured="zh") == "en"
    assert deps.output_locale(locale=None, configured="zh") == "zh"


# ── no route keeps a default of its own ──────────────────────────────────────────────────────────

def test_no_locale_bearing_route_declares_its_own_default():
    """The regression guard. A new route written with `locale: str = Query("en")` is a route the
    setting does not reach, and nothing about it would look wrong on the screen it serves.
    """
    from app.api.routes import documents, projects

    for module in (documents, projects):
        source = inspect.getsource(module)
        assert 'locale: str = Query(' not in source, (
            f"{module.__name__} declares a locale default of its own instead of taking it from "
            "deps.output_locale")


def test_every_locale_parameter_is_still_a_query_parameter():
    """Resolving in a dependency must not change the wire contract: all 18 endpoints still take
    `?locale=`, and it is still optional."""
    from app.main import app

    found = [(path, method, prm)
             for path, ops in app.openapi()["paths"].items()
             for method, op in ops.items()
             for prm in (op.get("parameters") or [])
             if prm["name"] == "locale" and ("/documents/" in path or "/projects/" in path)]

    assert len(found) == 18, (
        f"expected 18 locale-bearing document/project endpoints, got {len(found)}")
    assert all(not prm.get("required") for _p, _m, prm in found)


# ── and it reaches a real export ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def extracted_document(client):
    """One document with one succeeded run — enough for the export route to serve a sheet."""
    from app.db.base import SessionLocal
    from app.db.models import Document, ExtractionRun

    with SessionLocal() as session:
        doc = Document(filename="locale-default.pdf", content_hash="locale-default-1",
                       object_key="tests/locale-default.pdf")
        session.add(doc)
        session.flush()
        run = ExtractionRun(
            document_id=doc.id, run_number=1, status="succeeded",
            result={"rows": [{"canonical_key": "bs_ca__cash_and_cash_equivalents",
                              "source_label": "Cash and cash equivalents",
                              "values": [{"value": "1200", "basis": "consolidated",
                                          "period_label": "current",
                                          "period_display": None}]}]})
        session.add(run)
        session.commit()
        ids = (doc.id, run.id)

    yield ids[0]

    with SessionLocal() as session:
        run_row = session.get(ExtractionRun, ids[1])
        if run_row is not None:
            session.delete(run_row)
        doc_row = session.get(Document, ids[0])
        if doc_row is not None:
            session.delete(doc_row)
        session.commit()


@pytest.fixture()
def chinese_default():
    """The deployment configured for Chinese output, overridden at the dependency the routes read.

    Overriding the dependency rather than the environment because that is the seam the routes
    actually go through — and because `get_settings` is process-cached, so an env var would leak
    into the rest of a session-scoped suite.
    """
    from app.main import app

    app.dependency_overrides[deps.default_locale] = lambda: "zh"
    try:
        yield
    finally:
        app.dependency_overrides.pop(deps.default_locale, None)


def test_an_export_with_no_locale_parameter_uses_the_configured_default(
        client, extracted_document, chinese_default):
    """THE MEASURED CASE. `GET /documents/{id}/export?fmt=csv` with no `locale` at all: the CSV
    header is the localized one ("项目,金额"), not "Line item,Value"."""
    got = client.get(f"/api/v1/documents/{extracted_document}/export?fmt=csv")

    assert got.status_code == 200, got.text
    header = got.content.decode("utf-8-sig").splitlines()[0]
    assert header == "项目,金额", header


def test_that_export_still_honours_an_explicit_locale(client, extracted_document, chinese_default):
    """A caller asking for English gets English even where the deployment defaults to Chinese."""
    got = client.get(f"/api/v1/documents/{extracted_document}/export?fmt=csv&locale=en")

    assert got.status_code == 200, got.text
    assert got.content.decode("utf-8-sig").splitlines()[0] == "Line item,Value"


def test_a_sample_project_screen_also_follows_the_default(client, chinese_default):
    """Not just the exports: the same resolution is what the project/document screens read."""
    localized = client.get("/api/v1/projects/demo/integrity").json()
    english = client.get("/api/v1/projects/demo/integrity?locale=en").json()

    assert localized["summary"] and english["summary"]
    assert localized["summary"] != english["summary"], (
        "the configured default did not reach the integrity route")


def test_the_settings_screen_reports_the_locale_that_will_be_applied(client, monkeypatch):
    """The echo now reads through the same resolver, so the screen cannot report a default that
    no request honours — which is the state this whole change closes.

    Patched at `deps.get_settings` rather than through `dependency_overrides`: `_snapshot()` calls
    `default_locale()` as a plain function (it is not a route parameter), so the override seam the
    locale-bearing routes use does not exist here — and this test would pass on the old code if it
    pretended otherwise.
    """
    monkeypatch.setattr(deps, "get_settings",
                        lambda: Settings(features={"default_output_locale": "zh"}))

    body = client.get("/api/v1/settings").json()

    assert body["features"]["default_output_locale"] == "zh"
