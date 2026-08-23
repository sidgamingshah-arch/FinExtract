"""Settings endpoint: non-secret config snapshot + admin-only runtime toggle."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_settings_state():
    from app.services.settings_state import reset
    reset()
    yield
    reset()


def test_settings_requires_auth(anon_client):
    assert anon_client.get("/api/v1/settings").status_code == 401


def test_settings_snapshot_exposes_config_without_secrets(anon_client, auth):
    body = anon_client.get("/api/v1/settings", headers=auth("analyst")).json()
    # LLM / OCR / extraction config from config.toml is surfaced for the frontend.
    assert body["llm"]["provider"] and body["llm"]["model"]
    assert "api_key_env" in body["llm"] and "key_configured" in body["llm"]
    assert "api_key" not in body["llm"]  # no raw key present
    assert body["ocr"]["engine"] and body["embeddings"]["model"]
    assert "evidence_floor" in body["extraction"]
    assert body["features"]["supported_locales"] == ["en", "zh", "ar", "fr"]


def test_ui_localization_toggle_is_admin_only(anon_client, auth):
    # Non-admins cannot change settings.
    assert anon_client.patch("/api/v1/settings", json={"ui_localization": True},
                             headers=auth("analyst")).status_code == 403
    assert anon_client.patch("/api/v1/settings", json={"ui_localization": True},
                             headers=auth("reviewer")).status_code == 403

    # Admin flips the interface-localization flag; the snapshot reflects it.
    r = anon_client.patch("/api/v1/settings", json={"ui_localization": True}, headers=auth("admin"))
    assert r.status_code == 200 and r.json()["features"]["ui_localization"] is True
    assert anon_client.get("/api/v1/settings",
                           headers=auth("analyst")).json()["features"]["ui_localization"] is True

    # And back off.
    anon_client.patch("/api/v1/settings", json={"ui_localization": False}, headers=auth("admin"))
    assert anon_client.get("/api/v1/settings",
                           headers=auth("admin")).json()["features"]["ui_localization"] is False
