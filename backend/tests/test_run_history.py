"""GET /documents/{id}/runs, and run_id-scoped reads across the workspace — the historical-runs
picker used to show an older extraction instead of only the latest.

Every panel of the workspace has to honour the same pin. A picker that moved the statement grid
to run #3 while the notes, buckets and export stayed on the latest run would present two
extractions as one spread, which is worse than not offering the pin at all."""
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
                        "note_details": [{"no": str(10 + i),
                                          "title": f"Note from run {i + 1}",
                                          "page": 1, "rows": []}],
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


# ── the rest of the workspace honours the same pin ───────────────────────────────────────────
def test_the_notes_index_follows_the_pinned_run(client):
    doc_id, run_ids = _doc_with_runs(3)
    latest = client.get(f"/api/v1/documents/{doc_id}/notes").json()
    assert [n["no"] for n in latest["notes"]] == ["12"]

    older = client.get(f"/api/v1/documents/{doc_id}/notes?run_id={run_ids[0]}").json()
    assert [n["no"] for n in older["notes"]] == ["10"]


def test_one_note_follows_the_pinned_run(client):
    doc_id, run_ids = _doc_with_runs(2)
    # Note 10 exists only in the first run, so serving it proves the pin reached this reader.
    pinned = client.get(f"/api/v1/documents/{doc_id}/notes/10?run_id={run_ids[0]}")
    assert pinned.status_code == 200
    assert pinned.json()["no"] == "10"


def test_the_buckets_endpoint_follows_the_pinned_run(client):
    doc_id, run_ids = _doc_with_runs(2)
    for run_id in run_ids:
        pinned = client.get(f"/api/v1/documents/{doc_id}/buckets?run_id={run_id}")
        assert pinned.status_code == 200


def test_the_export_follows_the_pinned_run(client):
    doc_id, run_ids = _doc_with_runs(2)
    older = client.get(f"/api/v1/documents/{doc_id}/export?fmt=csv&run_id={run_ids[0]}")
    assert older.status_code == 200
    # 1000 is the first run's figure; the latest run carries 1001.
    body = older.content.decode("utf-8-sig")
    assert "1000" in body and "1001" not in body

    latest = client.get(f"/api/v1/documents/{doc_id}/export?fmt=csv")
    assert "1001" in latest.content.decode("utf-8-sig")


def test_an_unknown_run_id_is_a_404_rather_than_a_silent_fall_back_to_latest(client):
    # A pinned read that quietly served the latest run would show one extraction under another
    # run's name — the reader would have no way to tell.
    doc_id, _ = _doc_with_runs(2)
    for path in ("notes", "buckets", "statement", "run"):
        response = client.get(f"/api/v1/documents/{doc_id}/{path}?run_id={uuid.uuid4().hex}")
        assert response.status_code == 404, f"{path} fell back instead of 404"


def test_a_run_id_belonging_to_another_document_is_not_served(client):
    mine, _ = _doc_with_runs(1)
    theirs, their_runs = _doc_with_runs(1)
    response = client.get(f"/api/v1/documents/{mine}/notes?run_id={their_runs[0]}")
    assert response.status_code == 404
