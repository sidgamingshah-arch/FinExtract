"""A CONFIGURATION VERSION IS ONE SITTING, NOT ONE KEYSTROKE — and a pinned one is still frozen.

NEW FILE -> backend/tests/test_one_version_per_session.py

WHAT THIS REPLACES. Every inline edit published a new version, so a sitting spent renaming a few
lines produced a dozen of them and the history said nothing about what happened. Measured on the
live database before the change: ELEVEN versions for three line-item edits and six deletions. A
reader looking for "what did this person change" had to diff eleven definitions.

An edit arriving in the SAME session as the version in force now REPLACES that version's
definition. A different session starts a new one. So a version is one person's sitting.

THE IMMUTABILITY THAT MATTERED IS UNCHANGED, and that is the half worth testing hardest. A run
records the exact configuration version it used so a reader can be told which configuration
produced the figures; replacing that definition would retroactively change how a finished run is
explained, and nothing would say so. So `_pinned_by_a_run` is checked before any in-place write,
and a pinned version is never touched — whatever session asks.

THE THIRD CONDITION IS THE ABSENCE OF PROOF. A caller with no session to be identified by — the
`X-Role` dev header, a service call, the reference-data seeder — always INSERTS. Coalescing needs
proof the session is the same, and an empty session id is not proof.
"""
from __future__ import annotations

import pytest

API = "/api/v1"


def _versions(client) -> list[dict]:
    r = client.get(f"{API}/line-items/versions")
    assert r.status_code == 200, r.text
    return r.json()


def _in_force(client) -> dict:
    return max(_versions_of(client), key=lambda v: v["version"])


@pytest.fixture
def probe(client):
    """A throwaway template and configuration, so edits here cannot disturb the shipped one.

    Reuses `test_line_item_edit_fields`'s definitions rather than inventing a second probe shape —
    one of them going stale against the schema is one more than necessary.
    """
    from app.db.models import LineItemVersion, TemplateVersion
    from tests.test_line_item_edit_fields import _CFG_KEY, _drop, _SET, _TEMPLATE, _TPL_KEY

    r = client.post(f"{API}/templates", json={"definition": _TEMPLATE})
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/line-items", json={"definition": _SET})
    assert r.status_code == 201, r.text
    try:
        yield r.json()
    finally:
        _drop(LineItemVersion, LineItemVersion.line_items_key, _CFG_KEY)
        _drop(TemplateVersion, TemplateVersion.template_key, _TPL_KEY)


def _key() -> str:
    from tests.test_line_item_edit_fields import _EDITED

    return _EDITED


def _versions_of(client) -> list[dict]:
    """Only THIS probe's versions — the shipped configuration has its own and they are not ours."""
    from tests.test_line_item_edit_fields import _CFG_KEY

    return [v for v in _versions(client) if v.get("line_items_key", _CFG_KEY) == _CFG_KEY]


def _edit(client, version_id: str, label: str, key: str | None = None):
    r = client.patch(f"{API}/line-items/versions/{version_id}/items",
                     json={"key": key or _key(), "label": label})
    assert r.status_code == 200, r.text
    return r.json()


def test_three_edits_in_one_session_are_one_version(client, probe):
    """THE POINT OF THE CHANGE. Three edits, one version — and it carries the LAST one."""
    start = _in_force(client)
    before = len(_versions_of(client))

    first = _edit(client, start["id"], "Cash one")
    second = _edit(client, first["id"], "Cash two")
    third = _edit(client, second["id"], "Cash three")

    assert len({first["id"], second["id"], third["id"]}) == 1, (
        "each edit published its own version")
    assert len(_versions_of(client)) == before + 1, "more than one version for one sitting"

    stored = client.get(f"{API}/line-items/versions/{third['id']}").json()["definition"]
    cash = next(i for i in stored["items"] if i["key"] == _key())
    assert cash["label"] == "Cash three", "the version does not carry the latest edit"


def test_an_edit_from_a_different_session_starts_a_new_version(anon_client, client, probe):
    """A NEW SITTING IS A NEW VERSION, which is what makes the history readable at all."""
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.conftest import _login_token

    start = _in_force(client)
    mine = _edit(client, start["id"], "Cash mine")

    # A second login is a second session, even for the same user.
    other_token = _login_token(anon_client, "admin")
    with TestClient(app, headers={"Authorization": f"Bearer {other_token}"}) as other:
        theirs = _edit(other, mine["id"], "Cash theirs")

    assert theirs["id"] != mine["id"], "a different session wrote into my version"
    assert theirs["version"] == mine["version"] + 1

    # …and mine still says what I left it saying.
    kept = client.get(f"{API}/line-items/versions/{mine['id']}").json()["definition"]
    cash = next(i for i in kept["items"] if i["key"] == _key())
    assert cash["label"] == "Cash mine", "the other session overwrote my version's content"


def test_a_version_a_run_pinned_is_never_replaced(client, probe):
    """THE RULE THAT DOES NOT BEND. A run names the exact version it used; rewriting that definition
    would change how a finished run is explained and nothing would say so. Same session or not."""
    from sqlalchemy import delete

    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    start = _in_force(client)
    mine = _edit(client, start["id"], "Cash before the run")

    # A run pins it — the one thing that makes a version immutable. Written straight to the table:
    # a real extraction would need a PDF and a provider, and the pin is the only part under test.
    with SessionLocal() as db:
        db.add(ExtractionRun(id="run-session-probe", document_id="doc-session-probe",
                             line_item_version_id=mine["id"]))
        db.commit()
    try:
        after = _edit(client, mine["id"], "Cash after the run")
    finally:
        with SessionLocal() as db:
            db.execute(delete(ExtractionRun).where(ExtractionRun.id == "run-session-probe"))
            db.commit()

    assert after["id"] != mine["id"], "a pinned version was replaced in place"
    pinned = client.get(f"{API}/line-items/versions/{mine['id']}").json()["definition"]
    cash = next(i for i in pinned["items"] if i["key"] == _key())
    assert cash["label"] == "Cash before the run", (
        "the run's own configuration changed under it")


def test_a_caller_with_no_session_always_inserts(client, probe):
    """The `X-Role` dev header carries no session, so there is nothing to coalesce onto. Absence of
    a session id is not proof that the session is the same, and this is the safe side of that."""
    from app.config import get_settings

    settings = get_settings()
    was = settings.auth.allow_role_header
    settings.auth.allow_role_header = True
    try:
        start = _in_force(client)
        # No Authorization at all — only the role header, so `via` is "role-header".
        first = client.patch(f"{API}/line-items/versions/{start['id']}/items",
                             json={"key": _key(), "label": "Header one"},
                             headers={"X-Role": "admin", "Authorization": ""})
        assert first.status_code == 200, first.text
        second = client.patch(f"{API}/line-items/versions/{first.json()['id']}/items",
                              json={"key": _key(), "label": "Header two"},
                              headers={"X-Role": "admin", "Authorization": ""})
        assert second.status_code == 200, second.text
        assert second.json()["id"] != first.json()["id"], (
            "an unattributed publish coalesced — it cannot know the session is the same")
    finally:
        settings.auth.allow_role_header = was


def test_the_session_id_is_not_the_token(client):
    """The id is written to the database and the token never is. If these were the same value, a
    bearer token would be sitting in `line_item_versions.authored_in_session`."""
    from app.security.session import _SESSIONS

    assert _SESSIONS, "no session to inspect"
    for token, sess in _SESSIONS.items():
        assert sess.id and sess.id != token, "the session id IS the token"
