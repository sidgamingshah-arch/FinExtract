"""A COLUMN OF DASHES IS STILL A COLUMN (`row_reconstruct._value_column_bands`).

Madrigal Pharmaceuticals' 10-K (filed 2026-02-19) prints its Consolidated Statements of
Comprehensive Loss with three year columns, 2025 | 2024 | 2023. The company had no other
comprehensive income in 2024, so that column is "—" on every OCI row and carries a FIGURE on only
two rows — Net loss and Comprehensive loss. A value column needs `_COL_MIN_ROWS` (3) rows, so it was
refused, its two figures were dropped with it, and every 2023 figure on the page was filed as 2024:

    printed           Net loss  (288,284) | (465,892) | (373,630)
    read before       current -288,284, prior -373,630          (2024 lost, 2023 in its place)

That page's Net loss row and the income statement's then disagreed for 2024, so the rule that keeps
one fact printed on two statements from being added twice (`periods.summable`) no longer saw one
fact, and Net income (loss) was served as the sum of both rows.

A printed nil mark on a row that prints a figure counts toward the column it stands in.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, _value_column_bands, build_line_items

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures"
                  / "madrigal_10k_comprehensive_loss_words.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows():
    words = [Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d)) for t, a, b, c, d in FIX["words"]]
    items, _ordinal = build_line_items(words, page_index=7, document_id="madrigal",
                                       source_kind="native",
                                       statement="profit_and_loss")
    return {li.source_label.rstrip(" $"): {ev.period_label: str(ev.value)
                                          for ev in li.values.values()} for li in items}


def test_madrigals_net_loss_is_read_in_all_three_years(rows):
    assert rows["Net loss"] == {"current": "-288284", "prior": "-465892", "col2": "-373630"}
    assert rows["Comprehensive loss"] == {"current": "-287879", "prior": "-465892",
                                          "col2": "-373130"}


def test_a_2023_figure_is_never_filed_as_2024(rows):
    # 2024 printed "—" on these rows: there is no 2024 figure, and the 2023 one stays 2023's.
    assert rows["Total other comprehensive income"] == {"current": "405", "col2": "500"}
    assert "prior" not in rows["Net unrealized gain on available-for-sale securities"]


def test_a_dash_supports_a_column_of_figures_and_never_makes_one():
    # Two rows print a figure in the middle column; three more print a dash there.
    figures = [[(0.65, "(288,284)"), (0.79, "(465,892)"), (0.93, "(373,630)")],
               [(0.65, "392"), (0.93, "500")], [(0.65, "130")], [(0.65, "405"), (0.93, "500")],
               [(0.65, "(287,879)"), (0.79, "(465,892)"), (0.93, "(373,130)")]]
    assert len(_value_column_bands(figures)) == 2
    assert len(_value_column_bands(figures, [0.79, 0.79, 0.79])) == 3
    # Dashes alone, where no row prints a figure, are no column.
    assert len(_value_column_bands(figures[1:4], [0.79, 0.79, 0.79])) == 2


# ── a third column is never this year's ─────────────────────────────────────────────────────────

def test_a_row_left_holding_only_its_third_column_has_no_current_or_prior():
    """Madrigal's Net loss row after the model's answer took its 2025 and 2024 figures: only the
    2023 figure (col2) was left, and the positional fallback served it as 2025."""
    from app.services.periods import split_current_prior
    assert split_current_prior([{"period_label": "col2", "value": "-373630"}]) == (None, None)
    # A row positional from its first column is still read in order.
    cur, prior = split_current_prior([{"period_label": "col0", "value": "1"},
                                      {"period_label": "col1", "value": "2"},
                                      {"period_label": "col2", "value": "3"}])
    assert (cur["value"], prior["value"]) == ("1", "2")
    cur, prior = split_current_prior([{"period_label": "current", "value": "1"},
                                      {"period_label": "col2", "value": "3"}])
    assert cur["value"] == "1" and prior is None
