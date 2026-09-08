"""The analysis a screen shows must belong to the run the reader pinned.

THE MISALIGNMENT THIS CLOSES. `/documents/{id}/analysis` was pinned to `_latest_run`, and it is
what three screens read — Disclosures (which carries the contingent-liability findings), the
Extraction screen's analysis section, and the Commentary screen's credit view. Meanwhile the
Workspace, the notes index, the export and the top bar all honour a pinned run.

So opening a past run from the audit trail moved most of the app onto that run while those three
went on showing the newest one. A reader comparing a contingent-liability disclosure against the
balance sheet beside it would have been comparing two different extractions, with nothing on
screen saying so — and the Extraction screen's analysis section disagreed with the export it is
the on-screen twin of.

NO FALL-BACK, DELIBERATELY. A `run_id` that does not resolve is a 404, not the latest run.
Silently answering with a different extraction than the one asked for is the exact failure the pin
exists to prevent, and it would be invisible: the screen would render, the figures would look
plausible, and they would be the wrong run's.
"""
from __future__ import annotations

import inspect

import pytest

from app.api.routes.documents import get_document_analysis

# `client` and `auth` come from tests/conftest.py, which calls `init_db()` first. A bare
# `TestClient(app)` has no schema, and every assertion below then fails on "no such table:
# documents" instead of on the behaviour it is meant to check.


# ── the contract ─────────────────────────────────────────────────────────────────────────────────

def test_the_route_accepts_a_run_id():
    params = inspect.signature(get_document_analysis).parameters

    assert "run_id" in params, "the analysis route cannot be aligned to a run"


def test_run_id_is_optional_so_the_latest_run_stays_the_default():
    """Every existing caller passes none; the default must not change what they get."""
    default = inspect.signature(get_document_analysis).parameters["run_id"].default

    assert getattr(default, "default", default) is None


def test_the_route_reads_through_the_shared_resolver():
    """`_run_for_read` is the convention the sibling routes use, including its 404-not-fallback.

    Asserted on the source because the behaviour it brings — refusing to substitute the latest run
    — is the whole point, and a hand-rolled lookup here would quietly reintroduce the fallback.
    """
    # THE DOCSTRING IS STRIPPED FIRST. It names `_latest_run` while explaining what was replaced,
    # and an earlier version of this assertion matched that prose and failed — the same mistake a
    # regex in this codebase already made against its own comment once.
    source = inspect.getsource(get_document_analysis)
    body = source.replace(get_document_analysis.__doc__ or "", "")

    assert "_run_for_read" in body
    assert "_latest_run" not in body


# ── and it behaves, on data this test seeds itself ───────────────────────────────────────────────
# SEEDED, NOT FOUND. An earlier version of these looked for a document with two differing runs and
# skipped when it found none — so in the suite, where the database starts empty, all three skipped
# and verified nothing. A test that proves the behaviour only on a developer's machine is a test
# that will not catch the regression.


@pytest.fixture()
def two_runs(client):
    """One document with TWO runs whose analysis differs: the first has rows, the second is empty.

    The difference is the point. A route that ignored `run_id` would answer identically for both,
    and no assertion about status codes alone would notice.
    """
    from datetime import datetime, timedelta

    from app.db.base import SessionLocal
    from app.db.models import Document, ExtractionRun

    # EXPLICIT TIMESTAMPS, because "latest" is `created_at desc` and NOT `run_number`. Seeded in
    # one commit the two rows shared a timestamp, the tie broke on index order, and the first run
    # answered as the latest — so this test's own assumption about which run is newest was wrong
    # before the fixture said it out loud.
    earlier = datetime(2026, 1, 1, 9, 0, 0)
    later = earlier + timedelta(hours=1)

    with SessionLocal() as session:
        doc = Document(filename="alignment-fixture.pdf", content_hash="align-1",
                       object_key="tests/alignment-fixture.pdf")
        session.add(doc)
        session.flush()
        first = ExtractionRun(
            document_id=doc.id, run_number=1, status="succeeded", created_at=earlier,
            result={"rows": [{"canonical_key": "bs_ca__total_assets",
                              "values": [{"value": "1000", "basis": "consolidated",
                                          "period_label": "current"}]}],
                    "disclosures": [{"key": "contingent_liabilities", "present": True,
                                     "text": "guarantees of 4,200"}]})
        second = ExtractionRun(document_id=doc.id, run_number=2, status="succeeded",
                               created_at=later,
                               result={"rows": [], "disclosures": []})
        session.add_all([first, second])
        session.commit()
        ids = (doc.id, first.id, second.id)

    yield ids

    with SessionLocal() as session:
        for run_id in ids[1:]:
            run = session.get(ExtractionRun, run_id)
            if run is not None:
                session.delete(run)
        document = session.get(Document, ids[0])
        if document is not None:
            session.delete(document)
        session.commit()


def test_a_named_run_answers_with_that_runs_analysis(client, two_runs):
    doc_id, first, second = two_runs

    a = client.get(f"/api/v1/documents/{doc_id}/analysis?run_id={first}")
    b = client.get(f"/api/v1/documents/{doc_id}/analysis?run_id={second}")

    assert (a.status_code, b.status_code) == (200, 200)
    assert a.json()["disclosures"], "the first run's disclosure was not returned"
    assert not b.json()["disclosures"], "the second run has none, so none should come back"


def test_without_a_run_id_the_latest_run_is_served(client, two_runs):
    """The default must not change for the callers that pass nothing."""
    doc_id, _first, second = two_runs

    unpinned = client.get(f"/api/v1/documents/{doc_id}/analysis")
    latest = client.get(f"/api/v1/documents/{doc_id}/analysis?run_id={second}")

    assert unpinned.status_code == 200
    assert unpinned.json()["disclosures"] == latest.json()["disclosures"]
    for key in ("ratios", "disclosures", "notes", "credit"):
        assert key in unpinned.json(), f"the unpinned shape lost {key}"


def test_an_unresolvable_run_id_is_refused_rather_than_answered(client, two_runs):
    """404, never the latest run. Answering with a different extraction than the one asked for is
    invisible: the screen renders, the figures look plausible, and they are the wrong run's."""
    doc_id, _first, _second = two_runs

    got = client.get(f"/api/v1/documents/{doc_id}/analysis?run_id=not-a-run")

    assert got.status_code == 404


def test_a_run_id_belonging_to_another_document_is_refused(client, two_runs):
    """A run id names one filing. Carried onto another it would open the wrong spread — the same
    rule `Audit.open` relies on when it sets the document BEFORE the pin."""
    from app.db.base import SessionLocal
    from app.db.models import Document

    doc_id, first, _second = two_runs
    with SessionLocal() as session:
        other = Document(filename="other.pdf", content_hash="align-2",
                         object_key="tests/other.pdf")
        session.add(other)
        session.commit()
        other_id = other.id
    try:
        got = client.get(f"/api/v1/documents/{other_id}/analysis?run_id={first}")
        assert got.status_code == 404
    finally:
        with SessionLocal() as session:
            doc = session.get(Document, other_id)
            if doc is not None:
                session.delete(doc)
                session.commit()
