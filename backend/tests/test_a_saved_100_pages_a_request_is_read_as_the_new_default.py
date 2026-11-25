"""A stored 100 pages a request, saved when 100 was the default, is read as the shipped 600.

The Settings screen saves every setting whenever one is changed, and "Restore defaults" saves them
all, so a deployment that saved anything before the default moved has 100 stored. Re-applied as
written it would keep 100 pages a part on the 1,000,000-token window. As with
`llm_request_grouping` (`settings_state._grouping_as_saved`), a pre-marker 100 is read and saved as
the shipped default; a 100 chosen from now on is saved with the marker and kept.
"""
from __future__ import annotations

import pytest

from app.config import get_settings


def _admin(client):
    tok = client.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _restart() -> None:
    from app.services import settings_state
    settings_state.reset(persisted=False)
    settings_state.load_persisted()


def _rows() -> dict:
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride
    from app.services import settings_state
    with SessionLocal() as s:
        return {r.key: (r.value or {}).get("v") for r in s.execute(
            select(SettingOverride).where(
                SettingOverride.scope == settings_state.SCOPE_EXTRACTION)).scalars().all()}


@pytest.fixture
def overrides(client):
    from app.services import settings_state
    was = get_settings().extraction.whole_pdf_pages_per_request
    settings_state.reset()
    yield client
    settings_state.reset()
    get_settings().extraction.whole_pdf_pages_per_request = was


def test_a_100_saved_before_is_read_and_saved_as_the_shipped_600(overrides):
    from app.db.base import SessionLocal
    from app.db.models import SettingOverride
    from app.services import settings_state
    with SessionLocal() as s:
        s.add(SettingOverride(scope=settings_state.SCOPE_EXTRACTION,
                              key="whole_pdf_pages_per_request", value={"v": 100}))
        s.commit()
    _restart()
    assert get_settings().extraction.whole_pdf_pages_per_request == 600
    rows = _rows()
    assert rows["whole_pdf_pages_per_request"] == 600, "the reading is saved, so made once"
    assert rows[settings_state._PAGES_PER_REQUEST_MARKER] is True
    _restart()
    assert get_settings().extraction.whole_pdf_pages_per_request == 600


def test_a_100_chosen_now_survives_a_restart(overrides):
    client = overrides
    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"extraction": {"whole_pdf_pages_per_request": 100}})
    assert get_settings().extraction.whole_pdf_pages_per_request == 100
    _restart()
    assert get_settings().extraction.whole_pdf_pages_per_request == 100


def test_another_saved_number_is_kept(overrides):
    from app.db.base import SessionLocal
    from app.db.models import SettingOverride
    from app.services import settings_state
    with SessionLocal() as s:
        s.add(SettingOverride(scope=settings_state.SCOPE_EXTRACTION,
                              key="whole_pdf_pages_per_request", value={"v": 40}))
        s.commit()
    _restart()
    assert get_settings().extraction.whole_pdf_pages_per_request == 40
