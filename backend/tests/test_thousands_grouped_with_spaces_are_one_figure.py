"""THOUSANDS GROUPED WITH A SPACE ARE ONE FIGURE, ON A PAGE THAT PRINTS THEM THAT WAY.

NEW FILE -> backend/tests/test_thousands_grouped_with_spaces_are_one_figure.py

Phoenix Beverages 2025 (MUR '000) groups its thousands with a thin space: "13 400 370", "(5 619 017)".
PyMuPDF gives each digit group as a word of its own, about a fifth of a character height apart, and
the row reader kept one group per column: every face figure kept only its first group — revenue
13 / 12 against 13 400 370 / 12 170 859 (printed page 129), total assets 15 / 10 against 15 953 406 /
10 067 363 (p128) — and all 36 compared figures were wrong.

`pdf_extract._join_spaced_thousands` joins them before the row is read, as a PAGE's convention: only
where the page prints at least five such runs and more of them than numbers grouped with a comma.
A postal code or a telephone number on an address page ("400 001", Mumbai) stays as printed.
"""
from __future__ import annotations

import pathlib

import pytest

from app.services.pdf_extract import _join_spaced_thousands

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PBL = BACKEND.parent / "testdata" / "filings" / "sem_phoenix_beverages_2025.pdf"


def _w(text: str, x0: float, x1: float, line: int, y0: float = 100.0, y1: float = 109.6):
    return (x0, y0, x1, y1, text, 1, line, 0)


def _revenue_row(line: int) -> list[tuple]:
    """Phoenix Beverages' revenue row as the text layer gives it (sheet 65, right half)."""
    return [_w("Revenue", 637.2, 668.8, line), _w("24", 900.4, 910.0, line),
            _w("13", 931.3, 940.9, line), _w("400", 942.7, 957.1, line),
            _w("370", 958.8, 973.2, line), _w("12", 987.5, 997.1, line),
            _w("170", 999.1, 1013.5, line), _w("859", 1015.5, 1029.9, line)]


def test_spaced_groups_join_into_one_figure_on_a_page_that_prints_them():
    words = [w for line in range(5) for w in _revenue_row(line)]
    out = _join_spaced_thousands(words)
    texts = [w[4] for w in out[:4]]
    assert texts == ["Revenue", "24", "13,400,370", "12,170,859"]
    # the note number "24" stands well clear of "13" and is not joined to it
    assert out[1][4] == "24"


def test_a_bracketed_negative_closes_its_run():
    words = []
    for line in range(5):
        words += [_w("(5", 931.3, 940.9, line), _w("619", 942.7, 957.1, line),
                  _w("017)", 958.8, 975.0, line), _w("(4", 987.5, 997.1, line),
                  _w("100", 999.1, 1013.5, line), _w("200)", 1015.5, 1031.9, line)]
    out = [w[4] for w in _join_spaced_thousands(words)]
    assert out[:2] == ["(5,619,017)", "(4,100,200)"]


def test_a_page_of_comma_grouped_figures_is_left_alone():
    words = [_w("400", 100, 112, 0), _w("001", 114, 126, 0)]          # one postal code
    words += [_w(f"{n},234", 300, 330, n + 1) for n in range(1, 9)]   # the page's own figures
    assert _join_spaced_thousands(words) is words


def test_a_few_runs_on_an_address_page_are_not_a_convention():
    words = [w for line in range(2) for w in (_w("400", 100, 112, line), _w("001", 114, 126, line))]
    assert _join_spaced_thousands(words) is words


@pytest.mark.skipif(not PBL.exists(), reason=f"{PBL.name} not present")
def test_phoenix_beverages_revenue_on_page_129_is_read_whole():
    import fitz

    from app.services.pdf_extract import _native_words

    with fitz.open(PBL) as pdf:
        page = pdf[65]
        words = _native_words(page, page.rect.width, page.rect.height)
    texts = {w.text for w in words}
    assert {"13,400,370", "12,170,859"} <= texts             # revenue, p129
    assert {"15,953,406", "10,067,363"} <= texts             # total assets, p128
