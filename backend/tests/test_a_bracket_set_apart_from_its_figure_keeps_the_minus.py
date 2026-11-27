"""A NEGATIVE FIGURE PRINTED WITH A SPACE AFTER ITS BRACKET KEEPS ITS MINUS SIGN.

NEW FILE -> backend/tests/test_a_bracket_set_apart_from_its_figure_keeps_the_minus.py

Vivo Energy Mauritius 2025 prints its tax charge as "( 111,807)" (printed page 66, sheet 34). The
text layer gives the "(" as a word of its own, about 2pt before "111,807)":

    Income@60 tax@91 expense@106 11@337 (@412 111,807)@418 (@484 106,768)@489

The row reader put the lone "(" in the caption — "Income tax expense (" — and "111,807)" parsed as a
positive figure, so the tax expense came out +111,807 / +106,768 and the computed profit 753,153 /
730,046 against the 529,539 / 516,510 printed. `pdf_extract._join_spaced_open_brackets` joins the two
words before the row is read. Narrow on purpose: only a bare "(" followed, on the same printed line
and within about half a character height, by digits that close the bracket.
"""
from __future__ import annotations

import pathlib

import pytest

from app.services.pdf_extract import _join_spaced_open_brackets

BACKEND = pathlib.Path(__file__).resolve().parent.parent
VIVO = BACKEND.parent / "testdata" / "filings" / "sem_vivo_energy_mauritius_2025.pdf"


def _word(text: str, x0: float, x1: float, line: int, y0: float = 410.06, y1: float = 421.35):
    return (x0, y0, x1, y1, text, 20, line, 0)


def test_a_lone_bracket_and_the_figure_that_closes_it_are_one_word():
    words = [_word("Income", 59.53, 88.97, 0), _word("tax", 91.13, 104.25, 0),
             _word("expense", 106.41, 140.36, 0), _word("11", 336.87, 341.64, 1),
             _word("(", 411.93, 415.34, 2), _word("111,807)", 417.5, 447.39, 2),
             _word("(", 484.35, 487.24, 3), _word("106,768)", 489.4, 521.07, 3)]
    out = _join_spaced_open_brackets(words)
    assert [w[4] for w in out] == ["Income", "tax", "expense", "11", "(111,807)", "(106,768)"]
    joined = out[4]
    assert joined[0] == 411.93 and joined[2] == 447.39        # the box spans both words


def test_a_caption_aside_in_brackets_is_not_touched():
    words = [_word("Trade", 60, 80, 0), _word("receivables", 82, 120, 0),
             _word("(", 122, 125, 0), _word("Note", 126, 140, 0), _word("5)", 141, 148, 0),
             _word("(", 200, 203, 1), _word("a)", 204, 210, 1)]
    assert _join_spaced_open_brackets(words) == words


def test_a_bracket_far_from_the_figure_or_on_another_line_is_not_joined():
    far = [_word("(", 400, 403, 2), _word("111,807)", 430, 460, 2)]
    assert _join_spaced_open_brackets(far) == far
    other_line = [_word("(", 411.93, 415.34, 2), _word("111,807)", 417.5, 447.39, 3)]
    assert _join_spaced_open_brackets(other_line) == other_line


def test_a_page_with_no_lone_bracket_is_returned_as_it_was():
    words = [_word("Revenue", 60, 90, 0), _word("17,840,538", 300, 340, 1)]
    assert _join_spaced_open_brackets(words) is words


@pytest.mark.skipif(not VIVO.exists(), reason=f"{VIVO.name} not present")
def test_the_tax_charge_on_vivo_page_66_is_read_as_one_negative_figure():
    import fitz

    from app.services.pdf_extract import _native_words

    with fitz.open(VIVO) as pdf:
        page = pdf[34]
        words = _native_words(page, page.rect.width, page.rect.height)
    texts = [w.text for w in words]
    assert "(111,807)" in texts and "(106,768)" in texts
    assert "(" not in texts                  # no lone bracket is left on the sheet


def _spaced_page_words(line0: int = 10) -> list[tuple]:
    """Five figures grouped with spaces, as Phoenix Beverages prints them: the page's convention."""
    words = []
    for n in range(5):
        y0, y1 = 100.0 + 20 * n, 108.0 + 20 * n
        words += [_word("13", 300, 309, line0 + n, y0, y1), _word("400", 311, 324, line0 + n, y0, y1),
                  _word("370", 326, 339, line0 + n, y0, y1)]
    return words


def test_a_bracket_set_apart_from_a_figure_grouped_with_spaces_keeps_the_minus():
    """'( 52 079)': the bracket is joined after the digit groups, so it closes on '52,079)'.

    Built from real words: Vivo's gap between '(' and '111,807)' (2.16pt at an 11.3pt height) put in
    front of Phoenix's '(52 079)' (interest paid, printed page 131). In the other order the '(' was
    left alone and '52,079)' read as +52,079."""
    from app.services.pdf_extract import _join_split_figures
    from app.services.row_reconstruct import _num

    figure = [_word("Interest", 60, 95, 0), _word("paid", 97, 115, 0),
              _word("(", 411.93, 415.34, 2), _word("52", 417.5, 426.0, 2),
              _word("079)", 428.5, 446.0, 2)]
    out = _join_split_figures(_spaced_page_words() + figure)
    texts = [w[4] for w in out]
    assert "(52,079)" in texts and "(" not in texts, texts
    assert _num("(52,079)") == -52079
    assert texts.count("13,400,370") == 5                 # the page's other figures still join


def test_the_figure_joins_leave_a_page_without_either_convention_alone():
    from app.services.pdf_extract import _join_split_figures

    words = [_word("Revenue", 60, 90, 0), _word("17,840,538", 300, 340, 1)]
    assert _join_split_figures(words) == words
