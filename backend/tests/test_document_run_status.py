"""Per-document run status: is this document being extracted RIGHT NOW?

WHAT WAS MISSING, MEASURED. Nothing in the API answered "does this document have an extraction in
flight?". ``GET /documents/{id}/run`` refuses until a run has a RESULT
(``documents.py::get_document_run`` — ``if run is None or not run.result: raise 404``), which is
precisely the state a running run is not in, and ``GET /extractions/{run_id}`` needs a run id that a
freshly loaded page does not have. So a screen opened mid-run had one fact available to it — a 404 —
and the only sentence it could write from that was "this document has not been extracted", while the
pipeline was working on it.

Both halves of the gap are measured here against the SAME document: the new read reports ``running``
for a run with no result while ``/run`` still 404s on it, and it reports ``none`` — a 200 — for a
document nobody has ever extracted. Re-introduce the defect by putting
``if run is None or not run.result: raise 404`` back into ``get_document_run_status`` and both of
those tests fail; the ownership tests fail if its ``authorized_document`` dependency is dropped.

HOW THESE TESTS SEE A RUNNING RUN. Under ``TestClient`` a FastAPI BackgroundTask finishes inside the
POST, so no poll can catch one in flight (``test_extraction_progress`` documents the same
constraint). A run row in exactly the state the pipeline leaves mid-run — ``status="running"``,
``result=None``, a full progress record — is therefore written directly, and removed again: the
suite shares one database, so a hand-written run left behind is a run every later test that
enumerates them has to know about.
"""
from __future__ import annotations

import contextlib
import time
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fitz")

from tests.fixtures.generate import make_native_pdf


def _upload(client, name: str = "run-status.pdf") -> str:
    return client.post("/api/v1/documents",
                       files={"file": (name, make_native_pdf(), "application/pdf")}).json()["id"]


def _status(client, doc_id: str):
    r = client.get(f"/api/v1/documents/{doc_id}/run-status")
    assert r.status_code == 200, r.text
    return r.json()


def _progress_record(**over) -> dict:
    """A progress record in the shape the pipeline writes, built by the code that writes it — a
    literal here would pass whatever ``_served_progress`` happened to require on the day."""
    from app.api.routes.extractions import _progress_payload

    record = _progress_payload("map_ontology", 0.35,
                               started_at=datetime.now(timezone.utc), stage_count=14,
                               stage="map_ontology", stages_done=["ingest", "classify"])
    record.update(over)
    return record


@contextlib.contextmanager
def _run_row(doc_id: str, **fields):
    """A run row on ``doc_id`` for the duration of the block, then gone again."""
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    run_id = f"probe-{uuid.uuid4().hex[:12]}"
    with SessionLocal() as s:
        s.add(ExtractionRun(id=run_id, document_id=doc_id, options={}, **fields))
        s.commit()
    try:
        yield run_id
    finally:
        with SessionLocal() as s:
            row = s.get(ExtractionRun, run_id)
            if row is not None:
                s.delete(row)
                s.commit()


def _run_to_completion(client, doc_id: str) -> str:
    started = client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    for _ in range(200):
        if client.get(f"/api/v1/extractions/{run_id}").json()["status"] in {"succeeded", "failed"}:
            return run_id
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never settled")


def test_a_run_with_no_result_yet_reads_as_running(client):
    """THE CASE THE 404 HID. A run that is working has no result, so the only read that existed for
    a caller without a run id refused to describe it — and the screen had to call that "not
    extracted", which is the wrong answer and the one that reads as "nothing happened"."""
    doc_id = _upload(client)
    progress = _progress_record()
    with _run_row(doc_id, status="running", progress=progress, result=None) as run_id:
        served = _status(client, doc_id)
        assert served["status"] == "running", served
        assert served["run_id"] == run_id
        # The progress record is passed through, so the caller has the run's own stage and never has
        # to compute a stand-in for it. Every field is the stored one EXCEPT `elapsed_ms`, which is
        # a fact about now rather than about the last write: one record is stored per stage
        # transition, so a stamped figure sat frozen between stages and the screen's clock stopped
        # (routes/extractions._served_progress). Compared field by field so this test states which
        # of the two it is holding — `== progress` held both at once and could not distinguish the
        # derived clock from a record the endpoint had rebuilt or dropped fields from.
        assert {k: v for k, v in served["progress"].items() if k != "elapsed_ms"} \
            == {k: v for k, v in progress.items() if k != "elapsed_ms"}
        assert served["progress"]["elapsed_ms"] >= progress["elapsed_ms"], \
            "the clock went backwards"

        # …and the read that could not say this still cannot: same document, same instant.
        assert client.get(f"/api/v1/documents/{doc_id}/run").status_code == 404


def test_a_document_that_has_never_been_extracted_answers_none_and_not_an_error(client):
    """``none`` is information the caller asked for, not a failed request. Served as a 404 it would
    be indistinguishable from the in-flight case above — which is exactly how one status code came
    to stand for two different facts."""
    doc_id = _upload(client, "never-extracted.pdf")
    served = _status(client, doc_id)
    assert served == {"status": "none", "run_id": "", "progress": None}
    # The contrast, stated: the older read answers the same question with a refusal.
    assert client.get(f"/api/v1/documents/{doc_id}/run").status_code == 404


def test_the_status_follows_a_real_run_to_succeeded(client):
    """Against the real pipeline, so the words this route serves are the words a run actually
    records — not a set this test agreed with itself about."""
    doc_id = _upload(client, "real-run.pdf")
    assert _status(client, doc_id)["status"] == "none"

    run_id = _run_to_completion(client, doc_id)
    served = _status(client, doc_id)
    assert served["status"] == "succeeded", served
    assert served["run_id"] == run_id
    # The same run, and the same progress record, as the run's own endpoint reports: two reads of
    # one run that disagreed would be a screen contradicting itself between page loads.
    detail = client.get(f"/api/v1/extractions/{run_id}").json()
    assert served["progress"] == detail["progress"]
    assert served["progress"]["phase"] == "done"


def test_a_failed_run_reads_as_failed_and_names_the_stage_it_died_in(client):
    """A failed run is not "nothing has happened here", and it is not "still going" either. The
    stage comes along because it is already on the record — the caller that has to explain the
    failure should not need a second request to name it."""
    doc_id = _upload(client, "failed-run.pdf")

    def _explode(self, doc, ctx):
        raise RuntimeError("structural stage exploded")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.stages.structural.StructuralStage.run", _explode)
        run_id = _run_to_completion(client, doc_id)

    served = _status(client, doc_id)
    assert served["status"] == "failed", served
    assert served["run_id"] == run_id
    assert served["progress"]["phase"] == "failed"
    assert served["progress"]["stage"] == "structural"


def test_the_run_reported_is_the_LATEST_one(client):
    """A document is extracted more than once — a re-extract against a revised template is the
    ordinary case. Reporting whichever run the database handed back first would describe a document
    being re-extracted right now by the run that finished yesterday."""
    doc_id = _upload(client, "two-runs.pdf")
    first = _run_to_completion(client, doc_id)
    assert _status(client, doc_id)["run_id"] == first

    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with _run_row(doc_id, status="running", progress=_progress_record(), result=None) as second:
        # Stamped after the finished run, which is what makes it the latest — the fixture's default
        # `created_at` is `_now()`, and equal stamps would leave the ordering to chance.
        with SessionLocal() as s:
            row = s.get(ExtractionRun, second)
            row.created_at = datetime.now(timezone.utc).replace(year=2099)
            s.commit()
        served = _status(client, doc_id)
        assert served["run_id"] == second
        assert served["status"] == "running"


def test_a_run_recorded_before_the_progress_contract_serves_no_progress_rather_than_half_of_it(
        client):
    """``init_db`` uses ``create_all``, so runs written before the progress contract are still on
    disk holding ``{"phase": …, "pct": …}``. ``ExtractionProgress`` declares every field required,
    so passing that off as one of these records hands a screen ``undefined`` where the type promises
    a number. The same rule ``GET /extractions/{run_id}`` applies — one run, one answer."""
    doc_id = _upload(client, "legacy-progress.pdf")
    with _run_row(doc_id, status="running", progress={"phase": "queued", "pct": 0.0},
                  result=None) as run_id:
        served = _status(client, doc_id)
        assert served["progress"] is None
        # The run still says what it IS — the missing progress record costs the caller the stage,
        # not the fact that a run is in flight.
        assert served["status"] == "running" and served["run_id"] == run_id
        assert client.get(f"/api/v1/extractions/{run_id}").json()["progress"] is None


def test_reading_the_status_requires_a_session(anon_client, auth):
    """It reports on a filing being processed, so it sits behind the same session every other read
    of a document's data sits behind. ``extraction:view`` is the permission, matching the run's own
    endpoint: a caller who may not read the extraction may not read whether one is happening."""
    up = anon_client.post("/api/v1/documents",
                          files={"file": ("owned.pdf", make_native_pdf(), "application/pdf")},
                          headers=auth("analyst"))
    doc_id = up.json()["id"]

    assert anon_client.get(f"/api/v1/documents/{doc_id}/run-status").status_code == 401
    mine = anon_client.get(f"/api/v1/documents/{doc_id}/run-status", headers=auth("analyst"))
    assert mine.status_code == 200 and mine.json()["status"] == "none"


def test_the_status_of_someone_elses_document_is_404_not_403(anon_client, auth):
    """Ownership is a property of the DOCUMENT (``documents.py::_can_access``): its uploader, plus
    the reviewers and admins who work every analyst's queue. Only one analyst is seeded, so the
    denial is built directly.

    404 rather than 403, for the reason ``authorized_document`` gives: a 403 confirms the document
    exists, and "there is an extraction running on it" is a filing's existence leaked across
    tenants by another route.
    """
    from app.db.base import SessionLocal
    from app.db.models import Document

    doc_id = f"doc-{uuid.uuid4()}"
    with SessionLocal() as s:
        s.add(Document(id=doc_id, filename="theirs.pdf", owner="another.analyst",
                       content_hash=str(uuid.uuid4()), object_key=f"objects/{doc_id}"))
        s.commit()

    with _run_row(doc_id, status="running", progress=_progress_record(), result=None):
        assert anon_client.get(f"/api/v1/documents/{doc_id}/run-status",
                               headers=auth("analyst")).status_code == 404
        # A reviewer works every analyst's queue, so the same document IS theirs to read — the check
        # is ownership, not blanket secrecy.
        theirs = anon_client.get(f"/api/v1/documents/{doc_id}/run-status",
                                 headers=auth("reviewer"))
        assert theirs.status_code == 200 and theirs.json()["status"] == "running"
