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


def _row(label: str, key=None, column_index=None, printed_in=None):
    return {"id": str(uuid.uuid4()), "source_label": label, "canonical_key": key,
            "values": [_value(column_index)], "flags": [], "mapping_confidence": None,
            "printed_in": printed_in}


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


# --------------------------------------------------------------------------------------------
# The other half of "on the face of the statements": a note's detail line is not a face figure
# --------------------------------------------------------------------------------------------

def test_an_unplaced_note_row_is_not_reported_as_an_unmapped_face_item(client):
    """The category is "extracted but unmapped items ON THE FACE of statements", and a note detail
    line is not one.

    A note's rows are served through the Notes tab and the note-detail routes; they are not lines of
    the statement spread and were never candidates for a template line, so an unplaced one is not
    the defect this category names. On the real filing every unplaced row is already a face row, so
    this guard changes no count there — it is here so a filing whose notes carry unplaceable captions
    (a maturity table, a segment breakdown, a movement schedule) cannot fill the queue with them.
    """
    doc_id = _seed([
        _row("Within one year", printed_in="notes"),
        _row("Analysis of movements in the year", printed_in="notes"),
        _row("Deferred consideration payable", printed_in="face"),
    ], "queue-notes.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    unmapped = [c for c in body["checks"] if c["type"] == "unmapped"]
    assert [c["title"] for c in unmapped] == ["Deferred consideration payable"]


def test_an_unstamped_row_is_still_reported(client):
    """BOTH guards are POSITIVE signals, and this is why that matters.

    Not every row carries a ``printed_in`` stamp — it is set where the extraction can tell, and a row
    it could not place on either side of the filing has none. Testing for "is it stamped face" rather
    than "is it stamped notes" would drop exactly those rows: the ones the extraction understood
    least, which are the ones most likely to be a real mapping failure. The queue would then get
    quieter the worse the extraction got.
    """
    doc_id = _seed([_row("A caption on a page nothing classified")], "queue-unstamped.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    assert [c["type"] for c in body["checks"]] == ["unmapped"]


# --------------------------------------------------------------------------------------------
# The SEEDED SAMPLE teaches the same three categories, in the same words
# --------------------------------------------------------------------------------------------

def test_the_sample_speaks_the_real_routes_check_vocabulary(client):
    """The sample is the first thing a new user sees, so a category it shows had better exist.

    It used to speak its own: `subtotal`, `sign` and `note`, of which only `balance` was ever a kind
    the real route serves. Those names came from ``app/services/checks.py``, a parallel checks engine
    with no caller in the app (now deleted). The `note` card was worse than a synonym — note-tie
    findings are not raised at all any more, so the sample advertised a queue category that cannot
    occur, while showing NO card for the category that matters most.

    Derived from the real route's own maps rather than from a list written twice here: a kind added
    to `_ACCOUNTING_TYPES` or a new row-shaped kind is admitted automatically, and a kind the sample
    invents fails.
    """
    from app.api.routes.documents import _ACCOUNTING_TYPES, _ROW_SHAPED_TYPES

    review = client.get("/api/v1/projects/demo/review?locale=en").json()
    served = {c["type"] for c in review["checks"]}
    assert served, "the sample must serve findings at all"
    assert served <= set(_ACCOUNTING_TYPES) | set(_ROW_SHAPED_TYPES), served
    # …and it shows BOTH shapes, so a reader meets the accounting checks and the row-shaped finding.
    assert served & set(_ACCOUNTING_TYPES) and served & set(_ROW_SHAPED_TYPES)
    # The chips partition the list, and every chip selects something — see `_demo_review_tabs`.
    buckets = [t for t in review["tabs"] if t["types"] is not None]
    assert sum(t["count"] for t in buckets) == len(review["checks"])
    for tab in buckets:
        assert tab["count"] == len([c for c in review["checks"] if c["type"] in tab["types"]])
        assert tab["count"] > 0, tab["label"]


def test_a_card_kind_with_no_chip_fails_loudly_instead_of_going_invisible():
    """``_assert_known_kinds`` is the guard rail on the declaration above.

    A card reaches the screen through the chips, and the chips are built from
    ``_ACCOUNTING_TYPES``/``_ROW_SHAPED_TYPES``. So a kind nobody registered is a card no chip
    counts: invisible under every filter, while still inside the "All" total — which then disagrees
    with the sum of the chips beside it. That is the counts-disagree-with-content defect this file's
    module docstring is about, and it is cheaper to raise here than to find it on a screen.
    """
    import pytest

    from app.api.routes.documents import _assert_known_kinds

    # Everything the queue declares passes, asked of the declaration rather than of a copy of it.
    from app.api.routes.documents import _ACCOUNTING_TYPES, _ROW_SHAPED_TYPES
    _assert_known_kinds([{"type": t} for t in _ACCOUNTING_TYPES | _ROW_SHAPED_TYPES])

    # A retired kind is exactly as unregistered as an invented one, which is the point: bringing one
    # back means making the product decision again, not re-adding a literal.
    for kind in ("low_confidence", "off_template", "uncomputed", "note_tie", "sign", "subtotal"):
        with pytest.raises(AssertionError, match=kind):
            _assert_known_kinds([{"type": "balance"}, {"type": kind}])
