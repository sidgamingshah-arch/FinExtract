"""The run trail is DURABLE, and an administrator can read all of it.

WHAT THIS CLOSES. The trail was a process-local dict (``services/audit._LOG``), so every token count
the product had ever recorded was lost when the API restarted. That is the one number in this system
nobody can reconstruct after the fact: the rows can be re-extracted and the checks re-run, but what a
run SPENT is only knowable at the moment it spent it. A trail that does not survive a restart is not
a trail, and "where can I see the token counts for each run" had no durable answer.

Two properties carry the change, and the tests below are organised around them.

  * IT SURVIVES. A restart is simulated the only honest way available in-process — by discarding
    every module-level Python object the service holds and reading the trail back from the database.
    Asserting through a still-warm process would pass just as well against the dict this replaces.
  * AN ADMIN SEES EVERYTHING, AND ONLY AN ADMIN. The deployment-wide read crosses document
    ownership by design, which is exactly why it is the one route gated on ``audit:view``.

Two smaller things that are easy to get wrong and are each held here: a swallowed write must not
turn into a failed run, and a total must never be printed over a set of rows that does not add up to
it.
"""
from __future__ import annotations

import importlib
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.services import audit as audit_svc

pytest.importorskip("fitz")

from tests.fixtures.generate import make_native_pdf


@pytest.fixture(autouse=True)
def _empty_trail(anon_client):
    """A clean trail around every test here. The table is session-scoped and shared, and this
    module asserts on TOTALS — a row left behind by another test is an assertion failure with an
    unrelated cause.

    Depends on ``anon_client`` for the SCHEMA, not for the client: that fixture is what calls
    ``init_db``, and the service-level tests below touch the database without going through a route.
    """
    audit_svc.clear()
    yield
    audit_svc.clear()


def _entry(run_id: str, *, tokens: tuple[int, int] | None = (100, 20),
           action: str = "extraction", status: str = "succeeded",
           at: datetime | None = None) -> audit_svc.AuditEntry:
    return audit_svc.AuditEntry(
        run_id=run_id, entity="Acme Ltd", action=action, provider="anthropic",
        model="a-model", input_tokens=(tokens[0] if tokens else None),
        output_tokens=(tokens[1] if tokens else None), status=status, duration_ms=1500,
        created_at=(at or datetime.now(timezone.utc)).isoformat(),
    )


def _upload(client, name: str = "trail.pdf") -> str:
    return client.post("/api/v1/documents",
                       files={"file": (name, make_native_pdf(), "application/pdf")}).json()["id"]


# --- it survives -------------------------------------------------------------------------------

def test_the_trail_survives_a_restart():
    """THE WHOLE POINT. ``importlib.reload`` throws away every module-level object the service holds
    — which is all a restart destroyed before, because the store WAS a module-level object. Read
    back through the fresh module, the entry is still there.

    A test that read through the same warm module would pass against the dict this replaces, so it
    would assert nothing about durability at all.
    """
    audit_svc.record("doc-restart", _entry("run-survives", tokens=(4820, 1136)))

    fresh = importlib.reload(audit_svc)
    try:
        got = fresh.recorded("doc-restart")
        assert [e.run_id for e in got] == ["run-survives"]
        assert (got[0].input_tokens, got[0].output_tokens, got[0].total_tokens) \
            == (4820, 1136, 5956)
    finally:
        # Rebind the name every other test in the process holds, or they keep the reloaded copy.
        importlib.reload(audit_svc)


def test_a_stamp_comes_back_saying_it_is_utc():
    """SQLite has no timezone-aware DateTime: an aware value goes in and a NAIVE one comes back.
    Served without an offset, the client's ``new Date(created_at)`` reads it as LOCAL time, so a
    trail written at 02:00 UTC renders hours from when the run happened."""
    written = datetime(2026, 8, 26, 2, 0, 0, tzinfo=timezone.utc)
    audit_svc.record("doc-tz", _entry("run-tz", at=written))

    got = audit_svc.recorded("doc-tz")[0]
    parsed = datetime.fromisoformat(got.created_at)
    assert parsed.tzinfo is not None, f"no offset on {got.created_at!r}"
    assert parsed == written


def test_a_run_used_no_model_and_that_is_not_zero():
    """Null is a FACT here: the mapper resolved that filing lexically and never called the model,
    which is a different thing from having called it and spent nothing. Stored as null and read
    back as null, so no screen can report a cost that was never incurred as a measured zero."""
    audit_svc.record("doc-nollm", _entry("run-nollm", tokens=None))

    got = audit_svc.recorded("doc-nollm")[0]
    assert got.input_tokens is None and got.output_tokens is None
    assert got.total_tokens is None, "a run that used no model was given a token total"


def test_the_trail_is_per_scope():
    """One document's runs are that document's. The scope key is what keeps two filings' costs
    apart, and it is the field the deployment-wide read hands back so an admin can tell them
    apart again."""
    audit_svc.record("doc-a", _entry("run-a"))
    audit_svc.record("doc-b", _entry("run-b"))

    assert [e.run_id for e in audit_svc.recorded("doc-a")] == ["run-a"]
    assert [e.run_id for e in audit_svc.recorded("doc-b")] == ["run-b"]
    assert audit_svc.recorded("doc-none") == []


def test_a_write_that_cannot_land_does_not_fail_the_run():
    """An audit entry is a report ABOUT a run, not part of it. ``record`` is called on the failure
    path of an extraction, and a run that reached its rows must not be turned into a failure
    because a trail write did not land. It still returns the entry, and it still logs."""
    import app.db.base as db_base

    class _Broken:
        def __enter__(self):
            raise RuntimeError("database is gone")

        def __exit__(self, *a):
            return False

    original = db_base.SessionLocal
    db_base.SessionLocal = lambda: _Broken()          # type: ignore[assignment]
    try:
        entry = audit_svc.record("doc-broken", _entry("run-broken"))
    finally:
        db_base.SessionLocal = original               # type: ignore[assignment]
    assert entry.run_id == "run-broken", "record() raised instead of reporting"


# --- what it adds up to -------------------------------------------------------------------------

def test_totals_count_the_runs_that_could_have_spent_anything():
    """``llm_runs`` is counted, not inferred from the token sum: a run that used the model and was
    reported zero tokens still used it, and a reader dividing tokens by runs needs the denominator
    to be the runs that could have spent any."""
    entries = [_entry("r1", tokens=(100, 10)), _entry("r2", tokens=(0, 0)),
               _entry("r3", tokens=None), _entry("r4", tokens=(50, 5), status="failed")]
    got = audit_svc.totals(entries)

    assert got["runs"] == 4
    assert got["llm_runs"] == 3, "the zero-token run was dropped from its own denominator"
    assert got["failed"] == 1
    # A FAILED run's tokens ARE spent. Excluding them would understate the bill by exactly the
    # runs most worth noticing.
    assert (got["input_tokens"], got["output_tokens"], got["total_tokens"]) == (150, 15, 165)


def test_nothing_recorded_totals_to_zero_and_not_to_an_error():
    got = audit_svc.totals([])
    assert got == {"runs": 0, "llm_runs": 0, "failed": 0,
                   "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# --- the deployment-wide read -------------------------------------------------------------------

def test_an_admin_reads_every_document_s_runs_at_once(client):
    """The question this screen exists for: what has this deployment spent, and on what. It crosses
    document ownership deliberately — the per-document trail cannot answer it."""
    doc_id = _upload(client, "named.pdf")
    audit_svc.record(doc_id, _entry("run-doc", tokens=(200, 30)))
    audit_svc.record("some-other-scope", _entry("run-other", tokens=(10, 1)))

    body = client.get("/api/v1/audit").json()
    runs = {e["run_id"]: e for e in body["entries"]}
    assert set(runs) == {"run-doc", "run-other"}
    # The FILING each run was against, resolved to the uploaded file's own name — an admin reading
    # every run at once cannot use a document id.
    assert runs["run-doc"]["document"] == "named.pdf"
    assert runs["run-doc"]["scope_kind"] == "document"
    assert runs["run-other"]["document"] == "" and runs["run-other"]["scope_kind"] == "other"
    assert body["totals"]["total_tokens"] == 241


def test_the_deployment_read_is_newest_first(client):
    """Ordering is the whole readability of a trail, and the admin view's rows come pre-sorted
    rather than being re-sorted by a client that might not."""
    now = datetime.now(timezone.utc)
    audit_svc.record("d", _entry("older", at=now - timedelta(hours=2)))
    audit_svc.record("d", _entry("newest", at=now))
    audit_svc.record("d", _entry("middle", at=now - timedelta(hours=1)))

    got = [e["run_id"] for e in client.get("/api/v1/audit").json()["entries"]]
    assert got == ["newest", "middle", "older"], got


def test_the_cap_is_stated_and_the_totals_say_they_are_of_a_window(client):
    """A total printed over "the newest N of more" reads as the deployment's lifetime spend. The
    response has to distinguish "N runs" from "the first N of more", or the screen cannot."""
    now = datetime.now(timezone.utc)
    for i in range(4):
        audit_svc.record("d", _entry(f"r{i}", tokens=(100, 0),
                                    at=now - timedelta(minutes=i)))

    capped = client.get("/api/v1/audit", params={"limit": 2}).json()
    assert len(capped["entries"]) == 2 and capped["limit"] == 2
    assert capped["truncated"] is True
    # The totals are of what was RETURNED, which is what makes them checkable against the rows.
    assert capped["totals"]["runs"] == 2 and capped["totals"]["input_tokens"] == 200

    whole = client.get("/api/v1/audit", params={"limit": 100}).json()
    assert whole["truncated"] is False and whole["totals"]["runs"] == 4


def test_an_entry_outlives_the_document_it_describes(client):
    """Deleting an uploaded file must not erase the record that a run happened on it and what that
    cost — which is exactly what a ForeignKey with a cascade would do. The row stays; the filing
    column simply has no name to give."""
    doc_id = _upload(client, "doomed.pdf")
    audit_svc.record(doc_id, _entry("run-doomed", tokens=(300, 40)))
    assert client.delete(f"/api/v1/documents/{doc_id}").status_code in (200, 204)

    body = client.get("/api/v1/audit").json()
    row = next(e for e in body["entries"] if e["run_id"] == "run-doomed")
    assert row["document"] == "" and row["scope_kind"] == "other"
    assert row["total_tokens"] == 340, "the cost record went with the document"


def test_the_scope_column_is_not_a_foreign_key():
    """THE SCHEMA, asserted directly, because the runtime test above cannot see this.

    The suite's tables are created once and reused, so declaring ``scope_key`` a ForeignKey with
    ``ondelete="CASCADE"`` would change nothing in an already-created database and the deletion test
    would keep passing — right up to the first fresh deployment, where every audit row would vanish
    with its document. A cascade is the natural-looking way to write this column and the exact way
    to destroy the trail, so the absence of one is stated where it can be checked.
    """
    from app.db.models import AuditLogEntry

    col = AuditLogEntry.__table__.c.scope_key
    assert not col.foreign_keys, \
        f"scope_key references {[str(fk.target_fullname) for fk in col.foreign_keys]} — an audit " \
        f"entry must outlive the document it describes"


def test_only_an_admin_can_read_the_deployment_trail(anon_client, auth):
    """The one route that crosses document ownership, and the first endpoint behind ``audit:view``
    — the permission had been declared in the RBAC map since it was written with nothing enforcing
    it. Read through real sessions, not the X-Role dev header, because that header is off by
    default and a test that leaned on it would prove nothing about a deployment."""
    assert anon_client.get("/api/v1/audit", headers=auth("admin")).status_code == 200
    for role in ("analyst", "reviewer"):
        got = anon_client.get("/api/v1/audit", headers=auth(role)).status_code
        assert got == 403, f"{role} got {got}"
    # And unauthenticated is refused before the permission is even considered.
    assert anon_client.get("/api/v1/audit").status_code == 401


def test_a_real_extraction_writes_its_own_entry(client):
    """End to end: the trail is written by the pipeline, not only by these tests. The run's own id
    is what the entry is keyed on, so the trail row and the run can be lined up."""
    doc_id = _upload(client, "e2e.pdf")
    client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    for _ in range(100):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)

    entries = client.get(f"/api/v1/documents/{doc_id}/audit").json()["entries"]
    assert any(e["action"] == "extraction" for e in entries), entries
    # And the same run is in the deployment-wide read, under this document's name.
    wide = client.get("/api/v1/audit").json()["entries"]
    assert any(e["document"] == "e2e.pdf" and e["action"] == "extraction" for e in wide)
