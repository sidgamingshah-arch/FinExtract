"""Starting an extraction is IDEMPOTENT, so arriving at a screen cannot restart the filing.

THE DEFECT. Nothing in ``start_extraction`` looked for an existing run, so every POST minted a new
one with a fresh ``started_at`` and launched a fresh pipeline. The extraction screen fires that POST
when it mounts. So navigating away and back — or reloading, or opening a second tab, or
double-clicking — started the filing over: a second pipeline racing the first, the LLM tokens spent
twice, and the reader's elapsed clock jumping back to zero because it was honestly reporting a run
that had just begun. Reported as "the elapsed timer resets when I navigate away"; the timer was the
symptom and the duplicate run was the cause.

The client held this together with a cached query, which is not a guarantee: a cache is evicted on a
timer and gone on a reload, and it never covered the second tab at all. So the endpoint answers for
itself.

  * Asked for a run this document already has ON THE SAME OPTIONS, it hands that run back.
  * A run IN FLIGHT is never raced, ``force`` or not — there is no notion of two concurrent
    pipelines over one filing, and the reader would be shown whichever finished last.
  * Re-extracting stays possible and stays EXPLICIT. "Run it again" and "make sure this filing has
    been extracted" are different requests, and ``force`` is what tells them apart.

THE IDEMPOTENCY KEY IS EVERY OPTION THAT STEERS THE PIPELINE, not just the pins. The first version
compared the pins alone and an existing test caught it: two requests differing only in
``confirm_scope`` are asking for different things.
"""
from __future__ import annotations

import contextlib
import time
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fitz")

from tests.fixtures.generate import make_native_pdf


def _upload(client, name: str = "idem.pdf") -> str:
    return client.post("/api/v1/documents",
                       files={"file": (name, make_native_pdf(), "application/pdf")}).json()["id"]


def _runs(document_id: str) -> list:
    """Every run row for a document — the only honest way to count pipelines started."""
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as session:
        return session.query(ExtractionRun).filter(
            ExtractionRun.document_id == document_id).all()


def _await(client, doc_id: str) -> None:
    for _ in range(200):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            return
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


# --- a screen mounting must not restart the filing ---------------------------------------------

def test_posting_twice_while_a_run_is_in_flight_returns_the_same_run(client):
    """THE REPORTED BUG, at the endpoint. Two POSTs, one pipeline — and the second answer names the
    run already working rather than a new one, so the screen watches what it should."""
    doc_id = _upload(client)
    first = client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    second = client.post(f"/api/v1/documents/{doc_id}/extractions", json={})

    assert first.status_code == 202
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["adopted"] is True, "the second POST did not say it adopted"
    assert len(_runs(doc_id)) == 1, "a second pipeline was started over the first"


def test_the_second_answer_carries_the_run_s_own_start_time(client):
    """WHY THE CLOCK RESET. A duplicate run has a fresh ``started_at``, so the elapsed figure the
    screen reads restarts at zero on a filing that has been extracting for minutes. The adopted
    answer has to lead the reader back to the ORIGINAL run's progress record."""
    doc_id = _upload(client)
    first_id = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()["run_id"]
    started = client.get(f"/api/v1/extractions/{first_id}").json()["progress"]["started_at"]

    again = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()
    progress = client.get(f"/api/v1/extractions/{again['run_id']}").json()["progress"]
    assert progress["started_at"] == started, "the clock was restarted"


def test_a_finished_run_is_handed_back_rather_than_run_again(client):
    """Navigating back to a document that has already been extracted must not extract it again.
    The spread is already there, and re-running it spends the tokens a second time and replaces
    what the reader may have been reviewing."""
    doc_id = _upload(client)
    first_id = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()["run_id"]
    _await(client, doc_id)

    again = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()
    assert again["run_id"] == first_id and again["adopted"] is True
    assert len(_runs(doc_id)) == 1, "the finished filing was extracted a second time"


def test_a_pin_the_default_resolves_to_is_the_same_request(client):
    """The key compares RESOLVED ids. A caller pinning nothing and a caller pinning what the
    default resolves to are asking for the same run; comparing the raw requests would miss that and
    start a duplicate on the second arrival."""
    doc_id = _upload(client)
    first = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()
    _await(client, doc_id)

    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun
    with SessionLocal() as session:
        run = session.get(ExtractionRun, first["run_id"])
        resolved = {"template_version_id": run.template_version_id,
                    "ontology_version_id": run.ontology_version_id}

    again = client.post(f"/api/v1/documents/{doc_id}/extractions", json=resolved).json()
    assert again["run_id"] == first["run_id"], "an explicitly-pinned repeat started a new run"
    assert len(_runs(doc_id)) == 1


# --- but a different request is a different run -------------------------------------------------

def test_an_option_that_steers_the_pipeline_makes_it_a_different_run(client):
    """THE FLAW THE FIRST VERSION HAD: the key was the pins alone, so a request differing only in
    ``confirm_scope`` was handed the other one's run. Anything that changes what the pipeline does
    has to count."""
    doc_id = _upload(client)
    first = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()
    _await(client, doc_id)
    other = client.post(f"/api/v1/documents/{doc_id}/extractions",
                        json={"confirm_scope": True}).json()

    assert other["run_id"] != first["run_id"], "confirm_scope was ignored by the idempotency key"
    assert len(_runs(doc_id)) == 2


def test_force_is_how_the_reextract_control_asks_for_another_run(client):
    """Without this the re-extract button hits the same idempotency rule and is handed back the run
    it was asked to replace — a control that does nothing."""
    doc_id = _upload(client)
    first = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()
    _await(client, doc_id)

    forced = client.post(f"/api/v1/documents/{doc_id}/extractions", json={"force": True}).json()
    assert forced["run_id"] != first["run_id"], "force did not start a new run"
    assert forced.get("adopted") is None
    assert len(_runs(doc_id)) == 2


def test_a_failed_run_is_retried_rather_than_handed_back(client):
    """A failure is not an answer to "extract this document" — the caller asked for an extraction
    and does not have one. So the idempotency rule covers a SUCCEEDED run only."""
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    doc_id = _upload(client)
    first_id = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()["run_id"]
    _await(client, doc_id)
    with SessionLocal() as session:
        run = session.get(ExtractionRun, first_id)
        run.status = "failed"
        session.commit()

    again = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()
    assert again["run_id"] != first_id, "a failed run was served as the extraction"


# --- a run in flight is never raced -------------------------------------------------------------

@contextlib.contextmanager
def _held_open(doc_id: str, **options):
    """A run row that STAYS ``running`` for the duration of the block.

    The in-flight branch cannot be reached by POSTing twice quickly: ``TestClient`` drains
    background tasks before returning, so the fixture's pipeline is finished by the time a second
    request lands, and the two tests below passed for a while against the SETTLED branch instead —
    asserting nothing about the case they are named for. Inserting the row is the only way to hold
    the state the branch is about.
    """
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    run_id = f"held-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        session.add(ExtractionRun(id=run_id, document_id=doc_id, status="running",
                                  created_at=datetime.now(timezone.utc),
                                  options=dict(options), result=None))
        session.commit()
    try:
        yield run_id
    finally:
        with SessionLocal() as session:
            session.query(ExtractionRun).filter(ExtractionRun.id == run_id).delete()
            session.commit()


def test_a_different_request_while_a_run_is_working_is_refused_not_raced(client):
    """There is no notion of two concurrent pipelines over one filing: they would write the same
    run's rows twice and the reader would be shown whichever finished last. Asking for different
    rules mid-run is a real request that this run cannot answer, so it is refused — naming the run
    to wait for, rather than failing blankly."""
    doc_id = _upload(client, "clash.pdf")
    with _held_open(doc_id, confirm_scope=False) as in_flight:
        clash = client.post(f"/api/v1/documents/{doc_id}/extractions",
                            json={"confirm_scope": True})
        assert clash.status_code == 409, clash.text
        detail = clash.json()["detail"]
        assert detail["error"] == "run_in_flight"
        assert detail["run_id"] == in_flight, "the refusal did not name the run to wait for"
        assert len(_runs(doc_id)) == 1, "a second pipeline was started alongside the first"


def test_the_same_request_while_a_run_is_working_gets_that_run(client):
    """The navigate-away-and-back case, held against a run that is genuinely still going rather
    than one that finished in the meantime."""
    doc_id = _upload(client, "adopt.pdf")
    with _held_open(doc_id) as in_flight:
        again = client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
        assert again.status_code == 202
        assert again.json()["run_id"] == in_flight
        assert again.json()["adopted"] is True
        assert len(_runs(doc_id)) == 1


def test_force_does_not_race_a_run_in_flight_either(client):
    """``force`` means "another run of the same thing", not "a second pipeline alongside this one".
    It bypasses the settled-run rule; it does not bypass the in-flight one."""
    doc_id = _upload(client, "forced.pdf")
    with _held_open(doc_id) as in_flight:
        forced = client.post(f"/api/v1/documents/{doc_id}/extractions", json={"force": True})
        assert forced.status_code == 202
        assert forced.json()["run_id"] == in_flight, "force started a second concurrent pipeline"
        assert len(_runs(doc_id)) == 1


# --- the key's own contract ---------------------------------------------------------------------

def test_a_request_that_states_nothing_is_answered_by_any_run():
    """THE FLAW THE FIRST VERSION HAD, and it only shows up in use. The template default resolves to
    the LATEST one stored, so comparing resolved options meant publishing a template mid-run made an
    arriving screen's empty request look different from the run already in flight — and the reader
    was handed a 409 instead of their extraction. An empty request says "make sure this filing has
    been extracted", which any run of it answers."""
    from app.api.routes.extractions import ExtractionOptions, _satisfies

    class _Run:
        options = {"template_version_id": "an-older-template",
                   "ontology_version_id": "an-older-rulebook", "confirm_scope": True}
        template_version_id = "an-older-template"
        ontology_version_id = "an-older-rulebook"

    assert _satisfies(_Run(), ExtractionOptions())


def test_a_stated_pin_must_match():
    """The other half. Naming a template IS asking for that template, so a run on another one does
    not answer it — this is what keeps the rulebook-pin flow working."""
    from app.api.routes.extractions import ExtractionOptions, _satisfies

    class _Run:
        options = {"template_version_id": "t1", "ontology_version_id": "o1"}
        template_version_id = "t1"
        ontology_version_id = "o1"

    assert _satisfies(_Run(), ExtractionOptions(template_version_id="t1"))
    assert not _satisfies(_Run(), ExtractionOptions(template_version_id="t2"))
    assert not _satisfies(_Run(), ExtractionOptions(ontology_version_id="o2"))


def test_the_entity_name_does_not_make_it_a_different_run():
    """``entity`` only names the run id. Two requests differing in it are the same extraction, and
    treating them as different would restart the filing whenever a caller spelled it differently."""
    from app.api.routes.extractions import ExtractionOptions, _satisfies

    class _Run:
        options = {"confirm_scope": False}
        template_version_id = None
        ontology_version_id = None

    assert _satisfies(_Run(), ExtractionOptions(entity="Acme Ltd"))
    assert _satisfies(_Run(), ExtractionOptions(entity="ACME"))
