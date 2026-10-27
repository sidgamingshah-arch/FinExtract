"""A CAPTION CITED AS PRINTED NAMES ITS ROW, where another differs from it only by a footnote mark.

NEW FILE -> backend/tests/test_a_caption_cited_as_printed_names_its_row.py

`resolve_sources` compares captions after `norm`, which keeps letters, digits and CJK and drops
everything else — forgiving, on purpose, of a model that loses a footnote marker or a comma. That
also makes two printed captions one. 佳明集團's note 8 prints, in one block with no group between
them:

    Less: Amount capitalised*  減：資本化金額*     (234,599)   — the finance costs capitalised
    Less: Amount capitalised   減：資本化金額      (56,917)    — the staff costs capitalised

The blind answer for Personnel & Benefit Expenses cited the second, exactly as printed. Both
normalise to the same string, the rows sit in one block and group, so the tie went to document
order, and staff costs published 119,088 + 2,664 - 234,599 = -112,847 against a printed 64,835.

Where the rows tie after `norm`, the one whose caption is the citation AS PRINTED — case and spacing
aside — is the one it names. A citation that matches neither as printed is decided as before.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

INTEREST = "Less: Amount capitalised* 減：資本化金額*"
STAFF = "Less: Amount capitalised 減：資本化金額"


def _row(caption: str, amount: str) -> NoteItem:
    item = NoteItem(raw_label=caption, note_number="8")
    item.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                  basis=Basis.CONSOLIDATED, period_label="current"))
    return item


def _note() -> list[NotesTable]:
    return [NotesTable(note_number="8", title="LOSS BEFORE TAXATION 8. 除稅前虧損", items=[
        _row("Interest on bank and other loans 銀行及其他貸款利息", "342417"),
        _row(INTEREST, "-234599"),
        _row("Salaries, wages and other benefits 薪金、工資及其他福利", "119088"),
        _row(STAFF, "-56917")])]


def _figure(caption: str) -> str | None:
    resolved, unresolved = resolve_sources([SourceRef(note="8", caption=caption)], _note())
    assert not unresolved, unresolved
    (hit,) = resolved
    return hit["figures"].get("current")


def test_the_caption_without_the_mark_is_the_row_printed_without_it():
    assert _figure(STAFF) == "-56917"


def test_the_caption_with_the_mark_is_still_its_own_row():
    assert _figure(INTEREST) == "-234599"


def test_case_and_spacing_are_not_what_tells_them_apart():
    assert _figure("less:  amount capitalised 減：資本化金額") == "-56917"


def test_a_citation_matching_neither_as_printed_is_decided_as_before():
    """No mark either way, and neither printed form: the tie stands and document order breaks it."""
    assert _figure("Less Amount capitalised 減資本化金額") == "-234599"
