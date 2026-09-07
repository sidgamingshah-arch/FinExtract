"""A provider key placed in ``.env`` must reach ``os.environ``, where the adapters look.

THE BUG THIS PINS. Every adapter reads its key at CALL time — ``anthropic_llm.py:48``,
``azure_doc_intelligence.py:92`` — with ``os.environ.get(cfg.api_key_env)``. That is deliberate:
the key never enters a settings object, never persists, and never reaches the settings API the
admin UI reads. But pydantic-settings loads ``.env`` into the MODEL, not into the process
environment, so a key written to ``.env`` was read by nothing at all — measured directly,
``"OPENROUTER_API_KEY" in os.environ`` was False both before and after ``get_settings()``.

The only thing that ever worked was an ad-hoc ``export`` in the shell that launched uvicorn. That
is invisible to anyone else, unshared, and silently lost on the next restart — taking LLM
extraction down with it while every other route kept answering 200, which is the failure mode
these tests exist to prevent recurring.

Every key here is FAKE and no test makes a network call.
"""
from __future__ import annotations

import os

import pytest

from app.config import Settings, _export_provider_keys

FAKE = "sk-or-v1-0000000000000000000000000000000000000000000000000000000000000000"


@pytest.fixture
def in_backend_dir(tmp_path, monkeypatch):
    """Run with cwd at a temp dir, so `.env` resolves there and the real file is untouched."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _settings() -> Settings:
    """A Settings whose `api_key_env` names the variable under test."""
    return Settings(llm={"api_key_env": "OPENROUTER_API_KEY"})


def test_a_key_in_dotenv_reaches_os_environ(in_backend_dir, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (in_backend_dir / ".env").write_text(f"OPENROUTER_API_KEY={FAKE}\n", encoding="utf-8")

    assert "OPENROUTER_API_KEY" not in os.environ
    _export_provider_keys(_settings())

    assert os.environ["OPENROUTER_API_KEY"] == FAKE


def test_a_real_environment_variable_is_never_overridden(in_backend_dir, monkeypatch):
    """Someone who exported a key for one run must not have it replaced by a stale file line.

    Same precedence `settings_customise_sources` already gives env over .env.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "exported-wins")
    (in_backend_dir / ".env").write_text(f"OPENROUTER_API_KEY={FAKE}\n", encoding="utf-8")

    _export_provider_keys(_settings())

    assert os.environ["OPENROUTER_API_KEY"] == "exported-wins"


def test_only_the_variables_the_config_names_are_exported(in_backend_dir, monkeypatch):
    """Not a general dotenv loader. `.env` also holds FINEX_* settings and unrelated toggles.

    Publishing all of them into the environment would let a settings file quietly change process
    behaviour far outside the key it was edited for.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SOMETHING_ELSE", raising=False)
    (in_backend_dir / ".env").write_text(
        f"OPENROUTER_API_KEY={FAKE}\nSOMETHING_ELSE=leaked\nTORCHDYNAMO_DISABLE=1\n",
        encoding="utf-8")

    _export_provider_keys(_settings())

    assert os.environ.get("OPENROUTER_API_KEY") == FAKE
    assert "SOMETHING_ELSE" not in os.environ


def test_a_missing_dotenv_is_not_an_error(in_backend_dir, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    _export_provider_keys(_settings())          # no .env at all

    assert "OPENROUTER_API_KEY" not in os.environ


def test_an_empty_value_is_not_exported(in_backend_dir, monkeypatch):
    """A blank line is "not configured", not a key of zero length.

    Exporting "" would make `key_configured` report True and the failure move from a clear
    "no API key found" at startup to a 401 from the provider mid-extraction.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (in_backend_dir / ".env").write_text("OPENROUTER_API_KEY=   \n", encoding="utf-8")

    _export_provider_keys(_settings())

    assert not os.environ.get("OPENROUTER_API_KEY")


def test_the_azure_ocr_key_is_carried_by_the_same_rule(in_backend_dir, monkeypatch):
    """`ocr.azure_api_key_env` is read the same way, so it must be published the same way."""
    monkeypatch.delenv("AZURE_DI_KEY", raising=False)
    (in_backend_dir / ".env").write_text("AZURE_DI_KEY=fake-azure-key\n", encoding="utf-8")

    _export_provider_keys(Settings(ocr={"azure_api_key_env": "AZURE_DI_KEY"}))

    assert os.environ["AZURE_DI_KEY"] == "fake-azure-key"


def test_the_key_never_appears_in_the_settings_model(in_backend_dir, monkeypatch):
    """The whole point of `api_key_env` is that the VALUE stays out of settings.

    `/api/v1/settings` is admin-readable and returns the model; a key that leaked into it would be
    served over HTTP to anyone with the config permission.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (in_backend_dir / ".env").write_text(f"OPENROUTER_API_KEY={FAKE}\n", encoding="utf-8")
    s = _settings()
    _export_provider_keys(s)

    assert FAKE not in s.model_dump_json()
    assert s.llm.api_key_env == "OPENROUTER_API_KEY"     # the NAME, never the value
