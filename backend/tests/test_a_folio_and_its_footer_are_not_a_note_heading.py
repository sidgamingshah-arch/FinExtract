"""A PAGE NUMBER FOLLOWED BY THE RUNNING FOOTER IS NOT A NUMBERED NOTE HEADING.

NEW FILE -> backend/tests/test_a_folio_and_its_footer_are_not_a_note_heading.py

`classify._NUMBERED_HEADING` is searched over a page's text with its lines joined by newlines, and
its `\\s+` after the number crosses one. Vivo Energy Mauritius prints every page's folio on a line
of its own, then the running footer:

    66
    VIVO ENERGY MAURITIUS LIMITED   |    Annual Report 2025

so "66\\nVIVO" read as note 66's heading. It fired on 103 of the filing's 105 printed pages, and on
48 of them it was the only match — +2.5 to NOTES and -3.0 to FACE on the strategic report, the
governance pages and every statement half. Front-matter sheets 2-31 decoded as NOTES (margin 1.5).

The margin folio (`classify._folio_line`) is left out of the text the two note tests read. The
pattern itself is unchanged: a number set in the BODY of a page is still read as it was.

Each test here fails on the code before the change it pins, except the two that pin what must not
move.
"""
from __future__ import annotations

from app.stages import classify

H = 841.89


def _line(text: str, y: float, x0: float = 52.0) -> dict:
    return {"text": text, "y": float(y), "size": 9.0, "bold": False,
            "x0": float(x0), "x1": float(x0 + 5.5 * len(text))}


def _features(lines: list[dict]):
    return classify._features(5, lines, H, "\n".join(line["text"] for line in lines))


def test_the_folio_and_the_footer_are_not_a_note_heading():
    lines = [_line("OUR STRATEGY", 80.0),
             _line("We supply fuels and lubricants across the island.", 120.0),
             _line("66", 820.7, 30.0),
             _line("VIVO ENERGY MAURITIUS LIMITED | Annual Report 2025", 821.7, 60.0)]
    f = _features(lines)
    assert not f.note_heading


def test_page_one_and_its_footer_are_not_note_one():
    lines = [_line("CONTENTS", 80.0), _line("Our business at a glance", 120.0),
             _line("1", 820.7, 30.0),
             _line("VIVO ENERGY MAURITIUS LIMITED | Annual Report 2025", 821.7, 60.0)]
    f = _features(lines)
    assert not f.note_one and not f.note_heading


def test_a_note_heading_in_the_body_is_still_one():
    lines = [_line("NOTES TO THE FINANCIAL STATEMENTS", 60.0),
             _line("14 Property, plant and equipment", 120.0),
             _line("Cost 1,234,567 1,100,000", 150.0),
             _line("80", 820.7, 30.0),
             _line("VIVO ENERGY MAURITIUS LIMITED | Annual Report 2025", 821.7, 60.0)]
    assert _features(lines).note_heading


def test_a_number_alone_in_the_body_still_counts_as_it_did():
    """Only the MARGIN folio is left out: a note number set on its own line in the body, its title
    on the next, is read as before (`_FOLIO_BAND` keeps the search to the page's edges)."""
    lines = [_line("Revenue from contracts", 80.0), _line("14", 300.0, 40.0),
             _line("Cash and cash equivalents", 312.0), _line("80", 820.7, 30.0)]
    assert _features(lines).note_heading
