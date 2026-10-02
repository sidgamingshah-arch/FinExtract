"""Docling runs offline, from models on this server, behind an admin switch.

What an administrator can rely on:

* the Settings screen says whether Docling can actually run here — the package, and its models in
  ``[ocr] docling_models_dir`` — before anyone turns OCR on;
* with the switch off, a page without a text layer is skipped and the run log says why;
* the adapter never downloads: a missing model folder is an error naming the folder and the script
  that fills it, and the converter is pinned to that folder with the Hugging Face client offline.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.adapters import docling_ocr
from app.adapters._structured import LlmConfigError
from app.config import BACKEND_DIR, get_settings
from app.services.settings_state import reset


@pytest.fixture(autouse=True)
def _restore_settings():
    yield
    reset()


def _settings(folder):
    return SimpleNamespace(ocr=SimpleNamespace(docling_models_dir=str(folder)))


def _admin(client):
    tok = client.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


# ── where the models are, and whether they are there ─────────────────────────────────────────────

def test_a_relative_models_folder_is_taken_from_the_backend_folder():
    assert docling_ocr.models_dir(_settings("models/docling")) == (
        BACKEND_DIR / "models" / "docling").resolve()


def test_an_empty_folder_is_not_models(tmp_path):
    (tmp_path / "docling").mkdir()
    assert not docling_ocr.docling_status(_settings(tmp_path / "docling"))["models_present"]


def test_a_populated_folder_is_models(tmp_path):
    (tmp_path / "docling" / "layout").mkdir(parents=True)
    status = docling_ocr.docling_status(_settings(tmp_path / "docling"))
    assert status["models_present"] and status["models_dir"] == str(tmp_path / "docling")


# ── the adapter never downloads ──────────────────────────────────────────────────────────────────

def test_missing_models_are_an_error_naming_the_folder_and_the_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(docling_ocr, "docling_status", lambda s=None: {
        "installed": True, "models_dir": str(tmp_path), "models_present": False})
    with pytest.raises(LlmConfigError) as exc:
        docling_ocr.DoclingOcrProvider(_settings(tmp_path))._converter_or_raise()
    assert str(tmp_path) in str(exc.value)
    assert "scripts/fetch_docling_models.py" in str(exc.value)


def test_a_missing_package_points_at_the_offline_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(docling_ocr, "docling_status", lambda s=None: {
        "installed": False, "models_dir": str(tmp_path), "models_present": True})
    with pytest.raises(LlmConfigError, match="offline bundle"):
        docling_ocr.DoclingOcrProvider(_settings(tmp_path))._converter_or_raise()


def test_going_offline_pins_the_folder_and_closes_the_hub(monkeypatch, tmp_path):
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "DOCLING_ARTIFACTS_PATH"):
        monkeypatch.delenv(var, raising=False)
    docling_ocr._go_offline(str(tmp_path))
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["DOCLING_ARTIFACTS_PATH"] == str(tmp_path)


# ── the admin switch ─────────────────────────────────────────────────────────────────────────────

def _ctx(on: bool, engine: str = "docling"):
    from app.core.stage import PipelineContext

    ctx = PipelineContext(raw_bytes=b"")
    ctx.settings = get_settings().model_copy(deep=True)
    ctx.settings.extraction.ocr_scanned_pages = on
    ctx.settings.ocr.engine = engine
    return ctx


def test_switched_off_no_engine_is_resolved_and_the_log_says_why():
    from app.services.pdf_extract import _resolve_ocr

    ctx = _ctx(False)
    assert _resolve_ocr(ctx) is None
    assert any("ocr_off(admin)" in str(line) for line in ctx.logs)


def test_switched_on_the_configured_engine_is_resolved():
    from app.services.pdf_extract import _resolve_ocr

    provider = _resolve_ocr(_ctx(True))
    assert provider is not None and provider.id == "docling"


def test_the_switch_and_the_status_reach_the_settings_screen(client):
    body = client.get("/api/v1/settings", headers=_admin(client)).json()
    assert body["extraction"]["ocr_scanned_pages"] is True            # on out of the box
    assert set(body["ocr"]["docling"]) == {"installed", "models_dir", "models_present"}

    res = client.patch("/api/v1/settings", headers=_admin(client),
                       json={"extraction": {"ocr_scanned_pages": False}})
    assert res.status_code == 200 and res.json()["extraction"]["ocr_scanned_pages"] is False
    assert get_settings().extraction.ocr_scanned_pages is False
