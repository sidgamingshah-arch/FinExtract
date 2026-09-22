"""A PERIOD-PREFIXED CAPTION KEEPS ITS DATE, SO TWO BALANCE ROWS ARE TELLABLE APART.

NEW FILE -> backend/tests/test_a_caption_keeps_its_date.py

`_scan_row` sends every numeric token to the values and DROPS a non-numeric token that follows one,
because text after the first number is neither label nor value. On a movement table's balance rows
that reduces the caption to the period prefix alone and discards the month name outright. Measured
on 2025041600195 (Shanghai Industrial Holdings AR2024) note 51, the Level 3 reconciliation:

    printed                     caption   values
    At 1 January 2023           'At'      7,939   340,135    348,074
    At 31 December 2023         'At'      7,731   1,637,000  402,563   2,047,294
    At 31 December 2024         'At'      7,478   1,637,000  240,542   1,885,020

WHY THAT IS WORSE THAN COSMETIC, and the reason this is tested at the resolver and not only at the
label. A model answer is a CITATION — the system prompt says "Do NOT state a figure. You are
locating a printed number, not reporting one" — and `note_sourced.resolve_sources` matches the cited
caption against these rows by containment either way after punctuation is stripped. So
`At 31 December 2024` normalises to `at31december2024`, the row's caption to `at`, `at` is contained
in it, and a CORRECT citation resolved to the FIRST of the three: 348,074, the opening balance of
the comparative year. It RESOLVED, so nothing was reported as unresolved — the answer was wrong and
confident. Before the fix no citation reached 1,885,020 at all.

THE VALUES ARE NOT PART OF THE REPAIR, and the third test pins that. Only the label's text is
rebuilt, so the value list, the column geometry inferred from it and
`notes_extract._is_heading`'s `if values: return None` guard see exactly what they saw before.
Measured across all twelve reference filings: zero published figures changed.
"""
from __future__ import annotations

import pytest

from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.core.models.enums import Basis
from app.services.note_sourced import resolve_sources
from app.services.row_reconstruct import (
    BBox, Word, _join_words, _label_keeping_its_date, _scan_row)

GLYPH = 0.006
LINE_H = 0.012


def _row(text: str, y: float = 0.5) -> list[Word]:
    """One visual row, laid out left to right the way the extractor sees it."""
    out: list[Word] = []
    x = 0.10
    for token in text.split():
        out.append(Word(text=token,
                        bbox=BBox(x0=x, y0=y, x1=x + GLYPH * len(token), y1=y + LINE_H)))
        x += GLYPH * len(token) + GLYPH
    return out


def _label_of(text: str) -> str:
    row = _row(text)
    label_words, _note, value_words = _scan_row(row)
    return _join_words(_label_keeping_its_date(row, label_words, value_words))


@pytest.mark.parametrize(
    "printed, expected",
    [
        # The three rows of the measured reconciliation, which must not collapse together.
        ("At 1 January 2023 7,939 340,135 348,074", "At 1 January 2023"),
        ("At 31 December 2023 7,731 1,637,000 402,563 2,047,294", "At 31 December 2023"),
        ("At 31 December 2024 7,478 1,637,000 240,542 1,885,020", "At 31 December 2024"),
        # The other spellings the prefix takes, including the one the old comment in
        # `notes_extract` was written about ("As at 31 December 2024" on right-of-use note 16).
        ("As at 31 December 2024 46,287 6,474 52,761", "As at 31 December 2024"),
        ("Balance at 1 January 2024 1,000", "Balance at 1 January 2024"),
    ],
)
def test_a_balance_row_keeps_the_date_it_was_printed_with(printed, expected):
    assert _label_of(printed) == expected


def test_two_balance_rows_are_not_the_same_caption():
    """THE INVARIANT, stated as itself rather than as two string comparisons.

    A citation can only name a caption, so two rows that differ solely by date must differ by
    caption or one of them is unreachable — whichever way the resolver breaks the tie.
    """
    captions = {_label_of("At 1 January 2023 7,939 340,135 348,074"),
                _label_of("At 31 December 2023 7,731 1,637,000 402,563 2,047,294"),
                _label_of("At 31 December 2024 7,478 1,637,000 240,542 1,885,020")}
    assert len(captions) == 3, captions


def test_a_caption_with_no_period_prefix_is_left_exactly_alone():
    """THE BOUND ON THE REPAIR.

    Rebuilding every label from the whole row would pull a row's figures into its caption, which is
    the mistake `_period_anchors` records making ("Reading the whole row would put a closing
    balance's six class amounts in the caption"). So the repair fires only where the label is
    NOTHING BUT a period prefix, and an ordinary caption must be untouched — including one that
    merely contains a number, and one that begins with "at" as an ordinary word.
    """
    assert _label_of("Trade receivables 1,234 5,678") == "Trade receivables"
    assert _label_of("Attributable to owners 1,234") == "Attributable to owners"
    # Nothing among the values is part of a date, so there is nothing to give back.
    assert _label_of("At 7,939 340,135") == "At"


def test_the_values_are_untouched_by_the_repair():
    """What bounds the blast radius: only the caption's TEXT changes."""
    row = _row("At 31 December 2024 7,478 1,637,000 240,542 1,885,020")
    label_words, _note, value_words = _scan_row(row)
    before = [w.text for w in value_words]
    _label_keeping_its_date(row, label_words, value_words)
    assert [w.text for w in value_words] == before


def _note_with(rows: list[tuple[str, str]]) -> NotesTable:
    table = NotesTable(note_number="51", title="FINANCIAL INSTRUMENTS", page_index=177)
    for caption, amount in rows:
        item = NoteItem(raw_label=caption, note_number="51")
        item.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                      value=__import__("decimal").Decimal(amount),
                                      provenance=Provenance(page_index=177)))
        table.items.append(item)
    return table


def test_a_citation_of_the_closing_balance_no_longer_lands_on_the_opening_one():
    """THE CONSEQUENCE, at the resolver — the reason the label matters at all.

    With the dates present a citation resolves to the row it names. The control below shows what
    this test would have measured before: given three rows all captioned "At", the same citation
    resolves to the first, because "at" is contained in "at31december2024".
    """
    from app.services.mapping import SourceRef

    fixed = [_note_with([("At 1 January 2023", "348074"),
                         ("At 31 December 2023", "2047294"),
                         ("At 31 December 2024", "1885020")])]
    resolved, unresolved = resolve_sources(
        [SourceRef(note="51", caption="At 31 December 2024")], fixed, None, allow_face=False)
    assert not unresolved, unresolved
    assert [str(r["caption"]) for r in resolved] == ["At 31 December 2024"]

    truncated = [_note_with([("At", "348074"), ("At", "2047294"), ("At", "1885020")])]
    was, _ = resolve_sources(
        [SourceRef(note="51", caption="At 31 December 2024")], truncated, None, allow_face=False)
    assert was and str(was[0]["caption"]) == "At", (
        "the truncated shape must still be reachable, or this test is not measuring the "
        "difference the repair makes")
