r"""THE NOTE COLUMN — a row says which note it cites, through the ONE accessor for that question.

NEW FILE -> backend/tests/test_statement_note_column.py

THE DEFECT, measured on a real extraction through the running server before it was fixed: the
balance-sheet payload carried `note` on 80 statement rows and non-empty on ZERO. The Workspace has a
Note column and a `NoteChip` per reference, so it rendered 56px of permanent blank, and every
click-through from a figure to the note it cites was unreachable.

THE CAUSE was one line in `routes/extractions.py`, which built the row as

    "note": li.note_number,

and `LineItem.note_number`'s own docstring says what that field is:

    "``note_number`` is the fallback for a row whose reference was scanned from the note column
     WITHOUT being parsed into a ``NoteRef``"

Every normally-scanned row parses its reference into `note_refs` — `row_reconstruct` and
`excel_extract` both append a `NoteRef` there — so `note_number` is empty on all of them and the key
came out null. `LineItem.cited_notes()` exists precisely as the one definition of "which notes does
this row point at", written because three stages had each rolled their own and disagreed, "so a
filing could have a note published but unlinked, or linked but filed in the wrong section". The row
builder was a fourth reader, and it read only the fallback.

WHY A TEST AND NOT JUST THE FIX. Nothing asserted the payload carried a note at all, which is how a
column stayed blank through every green run of the suite. These tests read the ROW the API builds,
so the field name the screen reads is the field name asserted here.
"""
from __future__ import annotations

from decimal import Decimal

from app.api.routes.extractions import _note_refs_of
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, LineItem, NoteRef, Provenance


def _row(*, refs: list[str] = (), note_number: str | None = None) -> LineItem:
    li = LineItem(source_label="Trade receivables", canonical_key="bs_ca__trade_receivables",
                  role=LineRole.LINE)
    for r in refs:
        li.note_refs.append(NoteRef(raw=r, numbers=[r]))
    if note_number is not None:
        li.note_number = note_number
    li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                value=Decimal("1000"), value_raw=Decimal("1000"),
                                provenance=Provenance(page_index=12)))
    return li


def test_a_parsed_reference_reaches_the_column():
    """THE CASE THAT WAS BROKEN, and it is the normal one: the reference was parsed into a
    `NoteRef`, so `note_number` is empty and reading it gave null."""
    li = _row(refs=["15"])
    assert li.note_number in (None, ""), "the premise is gone — note_number is now populated too"
    assert _note_refs_of(li) == {"note": "15", "note2": None}


def test_the_fallback_still_works():
    """A row whose reference was scanned from the note column without being parsed — the shape
    `residual` gives a face row it synthesised out of a note item. `cited_notes()` falls back to
    `note_number` itself, so the shape that WAS working still works."""
    assert _note_refs_of(_row(note_number="7")) == {"note": "7", "note2": None}


def test_both_references_travel_when_the_periods_cite_different_notes():
    """A row can cite "10" for the current year and "10a" for the prior, and the grid keeps both —
    collapsing to one drops the second linkage, which is why the frontend reads `note` AND
    `note2`."""
    assert _note_refs_of(_row(refs=["10", "10a"])) == {"note": "10", "note2": "10a"}


def test_a_reference_is_carried_as_printed():
    """A note reference is RENDERED, never arithmetic: "10a" is not 10, and 附注 12 is not 12."""
    assert _note_refs_of(_row(refs=["16(b)"]))["note"] == "16(b)"


def test_citation_order_is_the_filings_own():
    """The first reference is the one a reader should follow first, so the order is the order the
    filing printed — not sorted. `note_sort_key` is for LISTING notes, a different question."""
    assert _note_refs_of(_row(refs=["21", "3"])) == {"note": "21", "note2": "3"}


def test_a_row_citing_nothing_says_nothing():
    """Blank, never a placeholder: a row that cites no note and a row whose note was not extracted
    are the same to a reader, and neither is a zero."""
    assert _note_refs_of(_row()) == {"note": None, "note2": None}


# ── the aggregator, where a concept is assembled from several printed lines ────────────────────

def test_a_concept_combined_from_many_lines_shows_each_distinct_note_once():
    """`_cited_pair` replaces a hardcoded `"note2": None`. Ten contributing lines that all cite
    note 7 are one chip; a concept whose contributions cite two notes shows both."""
    from app.api.routes.documents import _cited_pair

    same = [{"note": "7"}, {"note": "7"}, {"note": "7"}]
    assert _cited_pair(same) == {"note": "7", "note2": None}

    two = [{"note": "7"}, {"note": "12", "note2": None}]
    assert _cited_pair(two) == {"note": "7", "note2": "12"}

    # A contribution's own second reference counts as well.
    assert _cited_pair([{"note": "10", "note2": "10a"}]) == {"note": "10", "note2": "10a"}
    assert _cited_pair([{"note": None}, {}]) == {"note": None, "note2": None}


def test_the_row_the_api_builds_carries_the_note_key():
    """END TO END ON THE PAYLOAD, because the field NAME is half the contract: the Workspace reads
    `row.note` and `row.note2`, and a builder that emitted `note_ref` instead would satisfy every
    test above and still render a blank column."""
    from app.api.routes.extractions import _serialize_rows
    from app.core.models import DocumentModel

    # A REAL DocumentModel, not a stub: `_serialize_rows` reads `pages` for the folio lookup and
    # `notes`/`links` for the note linkage, and a stub that happens to satisfy today's reads would
    # stop satisfying tomorrow's without the test noticing.
    doc = DocumentModel(filename="probe.pdf")
    doc.line_items = [_row(refs=["15"])]

    rows = _serialize_rows(doc)
    assert rows, "no row was built"
    assert "note" in rows[0] and "note2" in rows[0], sorted(rows[0])
    assert rows[0]["note"] == "15"
