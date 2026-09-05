"""GET /documents/{id}/runs, and run_id-scoped reads of /run and /statement — the historical-runs
picker the Workspace uses to show an older extraction instead of only the latest."""
from __future__ import annotations

import uuid


def _doc_with_runs(n: int) -> tuple[str, list[str]]:
    """A document with `n` succeeded runs, oldest first, each with a distinct row value so a
    caller can tell which run's result it actually got back."""
    from datetime import datetime, timedelta

    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun

    init_db()
    run_ids: list[str] = []
    with SessionLocal() as session:
        doc = Document(filename="history.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin",
                       status="extracted")
        session.add(doc)
        session.flush()
        base = datetime(2026, 1, 1)
        for i in range(n):
            row = ExtractionRun(
                document_id=doc.id, status="succeeded", run_number=i + 1,
                options={"rulebook": {"key": f"rb{i}"}},
                created_at=base + timedelta(hours=i),
                result={"rows": [{"canonical_key": "is_pl__sales_revenues",
                                  "values": [{"basis": "consolidated", "period_label": "current",
                                             "value": str(1000 + i)}]}],
                        "filename": "history.pdf"})
            session.add(row)
            session.flush()
            run_ids.append(row.id)
        session.commit()
        return doc.id, run_ids


def test_runs_endpoint_lists_every_run_newest_first(client):
    doc_id, run_ids = _doc_with_runs(3)
    body = client.get(f"/api/v1/documents/{doc_id}/runs").json()
    assert [r["run_id"] for r in body["runs"]] == list(reversed(run_ids))
    assert [r["run_number"] for r in body["runs"]] == [3, 2, 1]


def test_run_endpoint_defaults_to_latest_and_can_be_pinned_to_an_older_run(client):
    doc_id, run_ids = _doc_with_runs(2)
    latest = client.get(f"/api/v1/documents/{doc_id}/run").json()
    assert latest["run_id"] == run_ids[-1]

    older = client.get(f"/api/v1/documents/{doc_id}/run?run_id={run_ids[0]}").json()
    assert older["run_id"] == run_ids[0]
    assert older["result"]["rows"][0]["values"][0]["value"] == "1000"


def test_run_id_from_another_document_answers_404_not_the_wrong_spread(client):
    doc_a, run_ids_a = _doc_with_runs(1)
    doc_b, _ = _doc_with_runs(1)
    res = client.get(f"/api/v1/documents/{doc_b}/run?run_id={run_ids_a[0]}")
    assert res.status_code == 404
