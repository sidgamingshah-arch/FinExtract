"""The LLM is defined in one place: config.toml's [llm] table.

Four places could set it, each overriding the next — a value saved from the Settings screen,
FINEX_LLM__* environment variables, .env, then config.toml — so the file could name the CRISIL
gateway while every call went to OpenRouter. The environment now supplies only the API KEY, under
the variable name [llm].api_key_env gives; a FINEX_LLM__* variable is ignored and named.
"""
from __future__ import annotations

import logging
import tomllib

import pytest

from app import config
from app.config import Settings, pin_llm

_FILE = tomllib.loads(config._CONFIG_TOML.read_text(encoding="utf-8"))["llm"]


@pytest.fixture
def _unpinned():
    """The suite pins the stub provider (conftest); these tests look at the file itself."""
    saved = dict(config._PINNED_LLM)
    pin_llm()
    yield
    pin_llm(**saved)


def test_the_file_is_what_is_used(_unpinned):
    llm = Settings().llm
    for key in ("provider", "model", "base_url", "api_key_env"):
        assert getattr(llm, key) == _FILE[key], key


def test_an_environment_variable_does_not_override_the_file(_unpinned, monkeypatch, caplog):
    monkeypatch.setenv("FINEX_LLM__MODEL", "minimax/minimax-m2.7:free")
    monkeypatch.setenv("FINEX_LLM__BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(config, "_WARNED_LLM_SOURCES", set())
    with caplog.at_level(logging.WARNING, logger="app.config"):
        llm = Settings().llm
    assert llm.model == _FILE["model"] and llm.base_url == _FILE["base_url"]
    # …and it is SAID, naming the variables, rather than lost in silence.
    assert "FINEX_LLM__MODEL" in caplog.text and "config.toml" in caplog.text


def test_other_settings_still_read_the_environment(monkeypatch):
    monkeypatch.setenv("FINEX_OCR__DPI", "123")
    assert Settings().ocr.dpi == 123


def test_code_can_pin_a_provider_for_tests_and_scripts(_unpinned):
    pin_llm(provider="stub")
    assert Settings().llm.provider == "stub"
    assert Settings().llm.model == _FILE["model"]
    pin_llm()
    assert Settings().llm.provider == _FILE["provider"]


def test_an_impossible_completion_ceiling_fails_at_startup(_unpinned):
    with pytest.raises(Exception, match="max_tokens"):
        pin_llm(max_tokens=4096000)
        Settings()
    pin_llm()
