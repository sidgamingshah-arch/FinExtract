"""What the review queue is allowed to contain.

The product rule: the queue carries exactly three things — a face figure nobody could place, a
validation rule that failed, and a section subtotal that does not match the values under it.
Anything else is either not a defect or not actionable, and every card that is neither costs the
analyst the attention the three real ones need.

This module pins the FIRST category's boundary, which is where the noise was: a row from a
statement served as a MATRIX is not a mapping failure. Measured on the China SCE 2023 filing before
the guard: 39 unmapped cards, 32 of them rows of the statement of changes in equity — a statement
with no mapped rows at all, because the template has no concept for an equity MOVEMENT and the
statement is served through the matrix view instead. The one thing the category exists for was
buried under rows nothing had asked the mapper to place.
"""
from __future__ import annotations

import uuid

import pytest


def _seed(rows: list[dict], filename: str = "queue.pdf") -> str:
    """A document with one stored succeeded run carrying `rows`.

    Seeded rather than extracted: what is under test is which rows the queue builder indicts, and a
    fixture PDF cannot produce a matrix row and a plain unmapped row side by side as cheaply.
    """
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun

    init_db()
    with SessionLocal() as session:
        doc = Document(filename=filename, fmt="pdf", byte_size=1, page_count=2,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin",
                       status="extracted")
        session.add(doc)
        session.flush()
        session.add(ExtractionRun(document_id=doc.id, status="succeeded", options={},
                                  result={"filename": filename, "rows": rows}))
        session.commit()
        return doc.id


def _value(column_index=None, value="1000"):
    return {"period_label": "current", "basis": "consolidated", "value": value,
            "column_index": column_index,
            "provenance": {"page_index": 0, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}}


def _row(label: str, key=None, column_index=None):
    return {"id": str(uuid.uuid4()), "source_label": label, "canonical_key": key,
            "values": [_value(column_index)], "flags": [], "mapping_confidence": None}


def test_a_matrix_row_is_not_reported_as_an_unmapped_face_item(client):
    """The guard. Both rows below are unmapped; only one of them is a mapping failure.

    "At 1 January 2023" is a movement row of the equity matrix — its figures sit in reserve COLUMNS
    (``column_index``), it was never a candidate for a canonical key, and the statement it belongs
    to is served as a grid. "Deferred consideration payable" is a face caption carrying money that
    nothing placed, which is the whole point of the category."""
    doc_id = _seed([
        _row("At 1 January 2023", column_index=0),
        _row("Transfer to statutory surplus reserve", column_index=3),
        _row("Deferred consideration payable"),
    ])
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    unmapped = [c for c in body["checks"] if c["type"] == "unmapped"]
    assert [c["title"] for c in unmapped] == ["Deferred consideration payable"]


def test_the_matrix_rows_are_counted_as_carrying_no_finding(client):
    """Not indicted, and therefore counted the way every other clean row is. A row excluded from the
    queue but still counted as having a finding would make the header tiles disagree with the list
    they head — the defect class this product has been bitten by twice."""
    doc_id = _seed([
        _row("At 1 January 2023", column_index=0),
        _row("Inventories", key="bs_current_assets__inventories"),
        _row("Deferred consideration payable"),
    ], "queue-counts.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    assert body["summary"]["open"] == 1
    # Three rows, one of them indicted: the other two carry no finding.
    tiles = {t.get("label"): t.get("count") for t in (body.get("tiles") or [])}
    if "No findings" in tiles:                     # the tile is optional in the payload
        assert tiles["No findings"] == 2


def test_a_row_with_no_column_index_is_judged_exactly_as_before(client):
    """The guard keys on ``column_index``, which only the matrix reader sets — not on the page's
    statement. So an ordinary face row on any page is unaffected, which is what stops this from
    quietly excusing a real mapping failure printed on an equity page."""
    doc_id = _seed([_row("A caption nothing placed")], "queue-plain.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    assert [c["type"] for c in body["checks"]] == ["unmapped"]


def test_the_signal_is_set_in_exactly_one_place():
    """Why ``column_index`` can be trusted as "this row came off a matrix": the matrix path in
    ``row_reconstruct`` is its only writer. If a second writer appears, this guard starts excusing
    rows it was never meant to, so the property is asserted rather than assumed."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    writers = [f"{p.relative_to(root)}:{i}"
               for p in root.rglob("*.py")
               for i, line in enumerate(p.read_text().splitlines(), 1)
               if re.search(r"column_index\s*=(?!=)", line) and "def " not in line]
    assert writers == ["services/row_reconstruct.py:2203"], writers
