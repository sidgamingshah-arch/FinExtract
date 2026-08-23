"""An administrator's settings survive a restart.

The point of persisting these is that an admin who lowers a threshold or points the app at a
different model does not silently get the config-file value back the next time the process
restarts. So the tests here do not check that a row was written — they check that the value is
still in force after the in-process state has been thrown away and reloaded from the database,
which is what a restart actually is.

Two things must NOT persist: the API key (never written at all), and the config-file defaults
(a reset deletes the rows rather than saving the old default over the new one).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import SettingOverride
from app.services import settings_state


def _admin(client):
    tok = client.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _restart() -> None:
    """What a restart does to this process: forget the in-memory overlay, keep the database,
    then re-apply what was stored — exactly the startup sequence in ``main``."""
    settings_state.reset(persisted=False)     # keep the rows; drop the cache
    settings_state.load_persisted()


@pytest.fixture(autouse=True)
def _clean_overrides():
    yield
    settings_state.reset()                    # clears rows AND cache


def _rows(scope: str) -> dict:
    with SessionLocal() as s:
        return {r.key: (r.value or {}).get("v") for r in s.execute(
            select(SettingOverride).where(SettingOverride.scope == scope)).scalars().all()}


def test_an_extraction_threshold_survives_a_restart(client):
    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"extraction": {"evidence_floor": 0.62, "mapping_scope": "per_line"}})
    assert get_settings().extraction.evidence_floor == 0.62

    _restart()

    assert get_settings().extraction.evidence_floor == 0.62
    assert get_settings().extraction.mapping_scope == "per_line"
    # …and the API reports the same thing a fresh client would read.
    live = client.get("/api/v1/settings", headers=_admin(client)).json()["extraction"]
    assert live["evidence_floor"] == 0.62 and live["mapping_scope"] == "per_line"


def test_the_llm_configuration_survives_a_restart(client):
    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"llm": {"provider": "anthropic", "model": "claude-sonnet-5",
                               "temperature": 0.3, "max_tokens": 2048}})
    _restart()

    llm = get_settings().llm
    assert llm.provider == "anthropic"
    assert llm.model == "claude-sonnet-5"
    assert llm.temperature == 0.3
    assert llm.max_tokens == 2048


def test_the_feature_flags_survive_a_restart(client):
    before = client.get("/api/v1/settings", headers=_admin(client)).json()["features"]
    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"ui_localization": not before["ui_localization"],
                       "review_required": not before["review_required"]})
    _restart()

    after = client.get("/api/v1/settings", headers=_admin(client)).json()["features"]
    assert after["ui_localization"] is not before["ui_localization"]
    assert after["review_required"] is not before["review_required"]


def test_the_api_key_is_never_persisted(client):
    """Only the NAME of the env var is stored. A client that tries to send a key must not get
    it written to the database — nor accepted at all."""
    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"llm": {"api_key_env": "MY_KEY_VAR", "api_key": "sk-secret-value",
                               "key": "sk-secret-value"}})

    stored = _rows(settings_state.SCOPE_LLM)
    assert stored.get("api_key_env") == "MY_KEY_VAR"
    assert "api_key" not in stored and "key" not in stored
    with SessionLocal() as s:
        blob = " ".join(str(r.value) for r in s.execute(select(SettingOverride)).scalars().all())
    assert "sk-secret-value" not in blob


def test_restoring_defaults_deletes_the_rows_rather_than_saving_the_old_value(client):
    """If a reset wrote the current defaults back as overrides, a later change to config.toml
    would be masked forever by a saved copy of the value it replaced."""
    h = _admin(client)
    client.patch("/api/v1/settings", headers=h, json={"extraction": {"evidence_floor": 0.31}})
    assert _rows(settings_state.SCOPE_EXTRACTION).get("evidence_floor") == 0.31

    client.patch("/api/v1/settings", headers=h, json={"reset_extraction": True})
    assert _rows(settings_state.SCOPE_EXTRACTION) == {}

    _restart()
    shipped = client.get("/api/v1/settings", headers=h).json()["extraction_defaults"]
    assert get_settings().extraction.evidence_floor == shipped["evidence_floor"]


def test_resetting_the_llm_config_also_clears_its_rows(client):
    h = _admin(client)
    client.patch("/api/v1/settings", headers=h, json={"llm": {"model": "some-other-model"}})
    assert _rows(settings_state.SCOPE_LLM).get("model") == "some-other-model"

    client.patch("/api/v1/settings", headers=h, json={"reset_llm": True})
    assert _rows(settings_state.SCOPE_LLM) == {}
    _restart()
    assert get_settings().llm.model != "some-other-model"


def test_defaults_are_the_config_files_even_after_a_restart_with_overrides_stored(client):
    """"Restore defaults" has to keep meaning what config.toml shipped. The defaults are
    captured BEFORE the stored overrides are applied, so a restart cannot turn a saved
    override into the new default — which would make the reset button a no-op."""
    h = _admin(client)
    shipped = client.get("/api/v1/settings", headers=h).json()["extraction_defaults"]

    client.patch("/api/v1/settings", headers=h, json={"extraction": {"evidence_floor": 0.33}})
    _restart()

    body = client.get("/api/v1/settings", headers=h).json()
    assert body["extraction"]["evidence_floor"] == 0.33          # the override is in force…
    assert body["extraction_defaults"] == shipped              # …but the default is unchanged
    restored = client.patch("/api/v1/settings", headers=h,
                            json={"reset_extraction": True}).json()["extraction"]
    assert restored["evidence_floor"] == shipped["evidence_floor"]


def test_a_stored_value_that_is_no_longer_valid_does_not_stop_startup(client):
    """A knob's range can tighten between releases, leaving a saved value outside it. That must
    fall back to the config default rather than crash the process on boot."""
    settings_state.set_extraction_config(evidence_floor=0.42)
    with SessionLocal() as s:
        row = s.execute(select(SettingOverride).where(
            SettingOverride.scope == settings_state.SCOPE_EXTRACTION,
            SettingOverride.key == "evidence_floor")).scalar_one()
        row.value = {"v": 99.0}               # impossible now
        s.commit()

    _restart()                                 # must not raise
    shipped = client.get("/api/v1/settings",
                         headers=_admin(client)).json()["extraction_defaults"]
    assert get_settings().extraction.evidence_floor == shipped["evidence_floor"]


def test_one_row_per_setting_so_separate_edits_do_not_clobber_each_other(client):
    """The reason this is a row-per-setting table and not one blob: two admins changing
    different knobs must both survive."""
    h = _admin(client)
    client.patch("/api/v1/settings", headers=h, json={"extraction": {"evidence_floor": 0.61}})
    client.patch("/api/v1/settings", headers=h, json={"extraction": {"mapping_margin": 0.11}})

    _restart()
    ex = get_settings().extraction
    assert ex.evidence_floor == 0.61 and ex.mapping_margin == 0.11


def test_whether_the_sample_project_is_loaded_also_survives_a_restart(client):
    """Loading or clearing the sample is a deliberate admin action, so it persists like the
    other flags — an admin who loaded it should still have it after a restart.

    The consequence is that the app's INITIAL state now depends on stored state, which is what
    broke a browser test that assumed a fresh process always starts empty. Recorded here so the
    behaviour is a decision rather than a surprise: anything needing the sample on or off must
    set it, not assume it.
    """
    h = _admin(client)
    client.patch("/api/v1/settings", headers=h, json={"seed_demo": True})
    _restart()
    assert client.get("/api/v1/settings", headers=h).json()["features"]["seed_demo"] is True

    client.patch("/api/v1/settings", headers=h, json={"seed_demo": False})
    _restart()
    assert client.get("/api/v1/settings", headers=h).json()["features"]["seed_demo"] is False
