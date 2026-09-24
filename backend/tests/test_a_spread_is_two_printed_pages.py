"""A 2-UP SPREAD IS TWO PRINTED PAGES, AND NO ROW MAY CROSS THE FOLD.

NEW FILE -> backend/tests/test_a_spread_is_two_printed_pages.py

An Indian integrated annual report is routinely published 2-up: one 1188x792pt landscape PDF page
carries two printed A4 pages, each with its own folio. Measured on Asian Paints' Integrated Annual
Report 2025-26, PDF page index 183 holds the Balance Sheet on the left and the Statement of Profit
and Loss on the right, and page 182 prints the folios "362" and "363" side by side.

WHAT THAT DID, measured before `services.page_spread` existed. `row_reconstruct._group_rows`
clusters words by vertical position across the whole page, so two printed pages sharing a baseline
were folded into one row:

    Property, Plant and Equipment   2A   6,040.18   6,285.40   30,621.70   29,270.69
                                         \\_ PPE, two years _/  \\_ Revenue from Sale of
                                                                  Products, two years _/

PPE's row carried Revenue's figures. Four value columns on a page that prints two also collapses
the inferred column geometry for the page, and captions fuse across the gutter into text no
rulebook can match — "Current Assets Other Expenses", "Financial Assets EARNING BEFORE INTEREST,
TAX,". With the fold honoured the same page grouped into 136 rows instead of 76 and every balance
sheet row carried only its own two periods.

THE HARD PART IS NOT FINDING A SPREAD, IT IS REFUSING A WIDE TABLE. An equity matrix and a PPE
cost-and-depreciation schedule are landscape too, with a caption column on the left and many value
columns, and splitting one down the middle strands every figure from its caption — a worse failure
than the one being fixed. The shipped equity-matrix fixture is the negative control here, and it is
not hypothetical: a first version of the detector split it, and `test_equity_matrix` caught it.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services import page_spread
from app.services.row_reconstruct import Word, _group_rows

LINE_H = 0.012
PITCH = 0.016


def _w(text: str, x0: float, y0: float, width: float | None = None) -> Word:
    w = 0.007 * max(len(text), 1) if width is None else width
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=min(x0 + w, 1.0), y1=y0 + LINE_H))


def _printed_page(x0: float, captions, *, values=True, y0: float = 0.08) -> list[Word]:
    """One printed page: a caption column at its own left margin, figures at its right."""
    words: list[Word] = []
    y = y0
    for caption in captions:
        x = x0
        for tok in caption.split():
            words.append(_w(tok, x, y))
            x += 0.007 * len(tok) + 0.004
        if values:
            words.append(_w("1,234.56", x0 + 0.30, y, 0.05))
            words.append(_w("2,345.67", x0 + 0.37, y, 0.05))
        y += PITCH
    return words


BALANCE_SHEET = ["Property Plant and Equipment", "Capital work-in-progress", "Goodwill",
                 "Other Intangible Assets", "Investments", "Trade Receivables", "Inventories",
                 "Cash and Cash Equivalents", "TOTAL ASSETS"]
PROFIT_AND_LOSS = ["Revenue from Sale of Products", "Other Operating Revenue", "Other Income",
                   "Total Income", "Cost of Materials Consumed", "Employee Benefits Expense",
                   "Finance Costs", "Depreciation and Amortisation Expense", "PROFIT BEFORE TAX"]


def _spread() -> list[Word]:
    return _printed_page(0.04, BALANCE_SHEET) + _printed_page(0.54, PROFIT_AND_LOSS)


def test_a_spread_is_detected_and_its_fold_is_near_the_middle():
    gutter = page_spread.gutter_x(_spread())
    assert gutter is not None, "two printed pages side by side were read as one"
    assert 0.40 <= gutter <= 0.60, gutter


def test_no_row_spans_the_fold():
    """THE DEFECT. Every row must hold words from one printed page only."""
    words = _spread()
    gutter = page_spread.gutter_x(words)
    for row in _group_rows(words, 0.006):
        sides = {(w.bbox.x0 + w.bbox.x1) / 2 < gutter for w in row}
        assert len(sides) == 1, (
            "a row crossed the fold: " + " ".join(w.text for w in row))


def test_the_left_page_keeps_its_own_two_periods():
    """The consequence that matters: a caption's row carries ITS figures, not the facing page's."""
    rows = _group_rows(_spread(), 0.006)
    by_caption = {}
    for row in rows:
        label = " ".join(w.text for w in row if not w.text[0].isdigit())
        figures = [w.text for w in row if w.text[0].isdigit()]
        if label:
            by_caption[label.strip()] = figures
    ppe = next((v for k, v in by_caption.items() if k.startswith("Property Plant")), None)
    assert ppe == ["1,234.56", "2,345.67"], (ppe, sorted(by_caption))
    assert len(by_caption) >= len(BALANCE_SHEET) + len(PROFIT_AND_LOSS) - 2, sorted(by_caption)


def test_the_reading_order_is_left_page_then_right_page():
    """Concatenated, not re-interleaved by y — the reading order of two printed pages is one after
    the other, and sorting the union back by vertical position is the order of neither."""
    words = _spread()
    rows = _group_rows(words, 0.006)
    gutter = page_spread.gutter_x(words)
    sides = [((r[0].bbox.x0 + r[0].bbox.x1) / 2) < gutter for r in rows]
    # Every left-page row precedes every right-page row.
    assert sides == sorted(sides, reverse=True), sides


def test_one_printed_page_is_not_a_spread():
    """THE FIRST CONTROL. An ordinary page has no empty band near its middle that runs its height."""
    words = _printed_page(0.04, BALANCE_SHEET + PROFIT_AND_LOSS)
    assert page_spread.gutter_x(words) is None


def test_a_wide_single_table_is_not_a_spread():
    """THE CONTROL THAT MATTERS, and the one a first version of the detector failed.

    An equity matrix: one caption column on the left, six right-aligned value columns across the
    page, and a stacked column-header band whose words sit near the middle. Those header words are
    wordy and they are near the fold, so a word-count test accepted them and the page was split in
    two — stranding every figure from its caption. What separates this from a spread is that its
    mid-page words occupy only the few rows of the header band, while a printed page's captions run
    down the whole page; `_MIN_CAPTION_ROWS` is what reads that difference.
    """
    cols = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
    headers = [["", "Issued", "capital"], ["Share", "premium", "account"],
               ["", "Other", "reserves"], ["", "Retained", "profits"],
               ["Non-", "controlling", "interests"], ["", "Total", "equity"]]
    words: list[Word] = []
    for line in range(3):
        for c, stack in enumerate(headers):
            if stack[line]:
                words.append(_w(stack[line], cols[c] - 0.05, 0.10 + line * PITCH))
    for c in range(len(cols)):
        words.append(_w("RMB'000", cols[c] - 0.05, 0.10 + 3 * PITCH))
    y = 0.20
    for caption in ["At 1 January 2023", "Loss for the year", "Dividends paid",
                    "Revaluation of properties", "At 31 December 2023"]:
        x = 0.05
        for tok in caption.split():
            words.append(_w(tok, x, y))
            x += 0.007 * len(tok) + 0.004
        for c, col in enumerate(cols):
            words.append(_w("365,138", col - 0.048, y, 0.048))
        y += PITCH
    assert page_spread.gutter_x(words) is None, (
        "a wide single table was split down the middle, which strands every figure from its "
        "caption")


def test_a_nearly_empty_page_is_not_a_spread():
    """A cover page or a section divider has an empty middle and nothing either side of it."""
    words = [_w("Financial", 0.10, 0.40), _w("Statements", 0.10, 0.42),
             _w("2025-26", 0.70, 0.40)]
    assert page_spread.gutter_x(words) is None


def test_a_column_break_off_centre_is_not_a_fold():
    """The band has to be near the middle. A two-column narrative page with a wide gap at x=0.25
    is not a spread, and `_CENTRE_BAND` is what refuses it."""
    words = _printed_page(0.02, BALANCE_SHEET, values=False) + \
        _printed_page(0.30, PROFIT_AND_LOSS, values=False)
    gutter = page_spread.gutter_x(words)
    assert gutter is None or 0.40 <= gutter <= 0.60, gutter


def test_halves_partition_every_word_exactly_once():
    words = _spread()
    gutter = page_spread.gutter_x(words)
    left, right = page_spread.halves(words, gutter)
    assert len(left) + len(right) == len(words)
    assert all((w.bbox.x0 + w.bbox.x1) / 2 < gutter for w in left)
    assert all((w.bbox.x0 + w.bbox.x1) / 2 >= gutter for w in right)


def test_grouping_is_unchanged_when_there_is_no_fold():
    """INERT ON EVERY FILING THAT IS NOT A SPREAD, which is the whole shipped corpus. The detector
    returning None must leave `_group_rows` doing exactly what it did."""
    words = _printed_page(0.04, BALANCE_SHEET)
    assert page_spread.gutter_x(words) is None
    rows = _group_rows(words, 0.006)
    assert len(rows) == len(BALANCE_SHEET), len(rows)
    for row in rows:
        assert sum(1 for w in row if w.text[0].isdigit()) == 2, [w.text for w in row]
