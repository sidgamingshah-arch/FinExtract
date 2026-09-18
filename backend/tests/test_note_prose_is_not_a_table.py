"""A note's PROSE route must not read the note's TABLES.

`NotesTable.source_text` is every word of the section joined with spaces, so a printed table
flattens into it with no sentence punctuation at all. `note_sourced._sentences` splits on
`[.。;；]`, so the whole note arrived as ONE sentence: a prose pattern then matched across rows
printed inches apart, and `_PROSE_AMOUNT` took the FIRST grouped amount in the note as the figure.

MEASURED ON CHINA SCE 1966 note 8 (profit before tax). The pattern
"depreciation … included in … operating expenses" matched the flattened table and
`sub__operating_expense_depreciation` published 17,475,980 — which is the note's
**Cost of properties sold** — as the operating-expense depreciation charge, on that part and on
the PBT callout, and on into `is_pl__deprec_and_impairment_oper_exp` through rung P1.

`notes_extract._narrative_only` now computes `prose_text`: the section's text with its tabulated
lines removed. This file is that separation.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services import notes_extract as NE
from app.services.row_reconstruct import Word, build_line_items

LINE_H = 0.011


def _words(text: str, y: float, x0: float = 0.05, per_char: float = 0.004) -> list[Word]:
    """One printed line of running text, laid out left to right in the label column."""
    out, x = [], x0
    for token in text.split():
        width = min(per_char * len(token), 0.05)
        out.append(Word(text=token, bbox=BBox(x0=min(x, 0.94), y0=y,
                                              x1=min(x + width, 0.95), y1=y + LINE_H)))
        x += width + 0.002
    return out


def _row(caption: str, amounts: tuple[str, ...], y: float) -> list[Word]:
    """A TABLE row: its caption on the left and its figures under the value columns, which is what
    makes the figures the rightmost things on the line."""
    out = _words(caption, y)
    for i, amount in enumerate(amounts):
        x = 0.62 + i * 0.16
        out.append(Word(text=amount, bbox=BBox(x0=x, y0=y, x1=x + 0.10, y1=y + LINE_H)))
    return out


FOOTNOTE = ("Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
            "included in other operating expenses.")


def _note(*, footnote: bool = True) -> tuple[list[Word], list]:
    words = _words("14. PROPERTY, PLANT AND EQUIPMENT", 0.10)
    words += _words("The charge for the year is arrived at after charging:", 0.13)
    words += _row("Depreciation of property, plant and equipment", ("80,427", "70,896"), 0.16)
    words += _row("Depreciation of right-of-use assets", ("22,145", "58,451"), 0.19)
    if footnote:
        words += _words(FOOTNOTE, 0.24)
    items, _ = build_line_items(words, page_index=1, document_id="d", source_kind="native",
                                on_face=False)
    return words, items


def _narrative(**kw) -> str:
    words, items = _note(**kw)
    return NE._narrative_only(words, items, "native")


def test_a_tabulated_row_is_not_part_of_the_notes_prose():
    got = _narrative()
    assert "80,427" not in got, got
    assert "22,145" not in got, got
    assert "Depreciation of right-of-use assets" not in got


def test_the_sentence_the_route_exists_for_survives_with_its_figures():
    """The whole point of the prose route: a share of the depreciation charge the filing states in
    a footnote and tabulates nowhere. Removing the tables must not remove this."""
    got = _narrative()
    assert "529,841,000" in got, got
    assert "included in other operating expenses" in got


def test_the_lead_in_and_the_heading_are_kept():
    """They tabulate nothing, so nothing here removes them — and the note's own heading is how a
    `note_title_any` pattern reaches the sentence in the first place."""
    got = _narrative()
    assert "PROPERTY, PLANT AND EQUIPMENT" in got
    assert "arrived at after charging" in got


def test_a_note_that_is_all_table_yields_no_prose_at_all():
    """And "" is not the same as `None`: `select_prose` falls back to `source_text` only when
    nothing computed the narrative, so a note whose every line is tabulated must come back empty
    rather than come back as the table it just excluded."""
    got = _narrative(footnote=False)
    assert "80,427" not in got and "22,145" not in got, got


def test_a_wrapped_caption_takes_both_of_its_printed_lines_with_it():
    """A row whose caption wraps spans two printed lines, and the label box that spans them has its
    MIDPOINT in the whitespace between — inside neither line's band. Measured on SCE note 8, that
    left one line of every wrapped caption in the narrative WITH its figures:
    "Depreciation of property and 物業及設備折舊 equipment 14 80,427 70,896"."""
    words = _words("8. PROFIT BEFORE TAX", 0.10)
    words += _words("Depreciation of property and", 0.16)
    words += _row("equipment", ("80,427", "70,896"), 0.175)
    words += _words(FOOTNOTE, 0.24)
    items, _ = build_line_items(words, page_index=1, document_id="d", source_kind="native",
                                on_face=False)
    got = NE._narrative_only(words, items, "native")
    assert "80,427" not in got, got
    assert "529,841,000" in got, got


def test_a_verby_table_caption_is_still_a_table():
    """`caption_shape.prose_reasons` calls a 60-character caption with a finite verb prose, which
    is right for its own question — is this caption a line-item NAME — and too loose for this one.
    "Lease payments not included in the measurement of lease liabilities" is 66 characters, carries
    "included", and is a tabulated row of SCE note 8. Only the LENGTH branch is consulted here, and
    the amount's position decides the rest."""
    words = _words("8. PROFIT BEFORE TAX", 0.10)
    words += _row("Lease payments not included in the measurement of lease liabilities",
                  ("8,121", "5,089"), 0.16)
    items, _ = build_line_items(words, page_index=1, document_id="d", source_kind="native",
                                on_face=False)
    got = NE._narrative_only(words, items, "native")
    assert "8,121" not in got, got


def test_an_inline_amount_keeps_a_row_the_reconstructor_made_of_a_sentence():
    """The reconstructor will make a row out of any line carrying a grouped amount, the footnote
    included. What keeps it is WHERE the amount sits: a sentence states it and carries on, so there
    are words to the right of it, while a table row's figures are the line's rightmost things."""
    line = _words(FOOTNOTE, 0.20)
    assert NE._amount_is_inline(line) is True
    row = _row("Depreciation of right-of-use assets", ("22,145", "58,451"), 0.20)
    assert NE._amount_is_inline(row) is False
    assert NE._amount_is_inline(_words("The charge for the year", 0.20)) is False
