"""A STATEMENT'S DATE LINE IS NOT A ROW OF FIGURES; A DATE IN A CAPTION IS THE CAPTION'S.

NEW FILE -> backend/tests/test_a_statement_date_line_is_not_a_row_of_figures.py

Gamma Civic 2024 prints "at 31 December 2024" under its balance-sheet title (printed page 88), and
the line came out as a row with 31 and 2024 in its two columns — swept into the other current
assets, so total current assets showed 3,204,079,968 / 3,103,680,810 against 3,204,079,937 /
3,103,678,786. Only where every figure is a date fragment: "At 1 July 2024 1,233,698" is a balance
row (`row_reconstruct._is_noise_row`, `_AT_A_DATE`).

The same filing wraps "CASH AND CASH EQUIVALENTS AT" / "1 JANUARY" (p94): the day was read as the
row's first figure and the month — text after a figure — was dropped, so neither the opening nor the
closing cash came out. A day or a year beside a month name ahead of the figures is the caption's
(`row_reconstruct._is_caption_date_part`).
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, _is_noise_row, _scan_row


def test_a_dated_subtitle_whose_figures_are_its_own_date_is_noise():
    assert _is_noise_row("at 31 December 2024", [Decimal(31), Decimal(2024)])
    assert _is_noise_row("At June 30, 2025", [Decimal(30), Decimal(2025)])


def test_a_dated_balance_row_with_real_figures_is_kept():
    assert not _is_noise_row("At 1 July 2024", [Decimal(1233698), Decimal(281973)])
    assert not _is_noise_row("At 30 June", [Decimal(828810), Decimal(1233698)])


def _row(*cells: tuple[str, float]) -> list[Word]:
    return [Word(text=t, bbox=BBox(x0=x, y0=0.5, x1=x + 0.006 * len(t), y1=0.512))
            for t, x in cells]


def test_the_day_and_month_opening_a_line_stay_in_its_caption():
    labels, _note, values = _scan_row(_row(("1", 0.06), ("JANUARY", 0.07), ("891,438,925", 0.45),
                                           ("894,897,770", 0.55)))
    assert [w.text for w in labels] == ["1", "JANUARY"]
    assert [w.text for w in values] == ["891,438,925", "894,897,770"]


def test_a_number_beside_no_month_is_still_a_figure():
    labels, _note, values = _scan_row(_row(("12", 0.06), ("1,000", 0.45)))
    assert not labels and [w.text for w in values] == ["12", "1,000"]


def test_the_date_stays_in_the_caption_once_the_wrap_has_joined_its_two_lines():
    """Gamma Civic's two lines once `_merge_wrapped_labels` has joined them: the day and month,
    printed with no year and with figures after them, stay in the caption."""
    labels, _note, values = _scan_row(_row(
        ("CASH", 0.06), ("AND", 0.09), ("CASH", 0.12), ("EQUIVALENTS", 0.15), ("AT", 0.25),
        ("1", 0.06), ("JANUARY", 0.07), ("891,438,925", 0.45), ("894,897,770", 0.55)))
    assert " ".join(w.text for w in labels) == "CASH AND CASH EQUIVALENTS AT 1 JANUARY"
    assert [w.text for w in values] == ["891,438,925", "894,897,770"]


def test_a_dated_caption_with_its_year_keeps_the_existing_reading():
    labels, _note, values = _scan_row(_row(("Balance", 0.06), ("as", 0.11), ("at", 0.13),
                                           ("1", 0.15), ("April", 0.16), ("2023", 0.20),
                                           ("612.40", 0.45), ("1,124.80", 0.55)))
    assert " ".join(w.text for w in labels) == "Balance as at"
