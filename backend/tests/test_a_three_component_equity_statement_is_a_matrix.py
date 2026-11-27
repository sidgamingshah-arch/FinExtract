"""A STATEMENT OF CHANGES IN EQUITY WITH THREE COMPONENTS IS READ AS A MATRIX.

NEW FILE -> backend/tests/test_a_three_component_equity_statement_is_a_matrix.py

Vivo Energy Mauritius 2025, a single entity with one reserve, prints its statement of changes in
equity in three columns — Share Capital | Retained Earnings | Total Equity — over fourteen rows
(printed page 68), and none was read:

* the day and the year of "At 01 January 2024" stood in bands of their own, so the page had six
  "columns", which could not be named (`equity_matrix_unnamed_columns=6(skipped)`). A 1-2 digit
  number beside a month name, and a year after one, is the caption's date
  (`row_reconstruct._is_caption_date_part`); Moroil sets it the US way, "July 1, 2024";
* with the dates gone the page has three columns, and a matrix wanted five, because four is the
  widest a two-column comparative prints. Below five, the page is a matrix only when its header
  names every column and the last one is the TOTAL — a comparative's columns are years or
  entities, never a total (`row_reconstruct._small_equity_matrix`).

The real page is pinned in `test_vivo_energy_mauritius_statements_read_as_printed`.
"""
from __future__ import annotations

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

PITCH_Y = 0.016
LINE_H = 0.012
COL_RIGHT = [0.62, 0.77, 0.90]


def _w(text: str, x0: float, y0: float, width: float = 0.06) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + width, y1=y0 + LINE_H))


def _cell(text: str, col: int, y0: float) -> Word:
    width = 0.008 * max(len(text), 1)
    return _w(text, COL_RIGHT[col] - width, y0, width)


def _label(text: str, y0: float, x0: float = 0.05) -> list[Word]:
    out, x = [], x0
    for tok in text.split():
        out.append(_w(tok, x, y0, 0.006 * len(tok)))
        x += 0.006 * len(tok) + 0.004
    return out


def _page(last_heading: tuple[str, str] = ("Total", "Equity")) -> list[Word]:
    words: list[Word] = []
    for c, (top, bottom) in enumerate((("Share", "Capital"), ("Retained", "Earnings"),
                                       last_heading)):
        words.append(_w(top, COL_RIGHT[c] - 0.05, 0.10))
        words.append(_w(bottom, COL_RIGHT[c] - 0.05, 0.10 + PITCH_Y))
    for c in range(3):
        words.append(_w("Rs’000", COL_RIGHT[c] - 0.05, 0.10 + 2 * PITCH_Y))
    y = 0.20
    for caption, cells in (("At 01 January 2024", ("293,223", "884,610", "1,177,833")),
                           ("Profit for the year", ("-", "516,510", "516,510")),
                           ("Other comprehensive loss for the year", ("-", "(9,511)", "(9,511)")),
                           ("Dividends declared (Note 26)", ("-", "(504,343)", "(504,343)")),
                           ("At 31 December 2024", ("293,223", "887,266", "1,180,489"))):
        words += _label(caption, y)
        for c, v in enumerate(cells):
            words.append(_cell(v, c, y))
        y += PITCH_Y
    return words


def _build(words: list[Word]):
    logs: list[str] = []
    items, _ = build_line_items(words, page_index=35, document_id="d1", source_kind="native",
                                statement="changes_in_equity", log=logs.append)
    return {li.source_label: li for li in items}, logs


def test_three_named_components_ending_in_a_total_are_a_matrix():
    rows, logs = _build(_page())
    assert any("equity_matrix_columns=3" in m for m in logs)
    opening = rows["At 01 January 2024"]
    got = {ev.period_label: ev.value for ev in opening.values.values()}
    assert sorted(got.values()) == [293223, 884610, 1177833]
    assert all(ev.basis is Basis.CONSOLIDATED for ev in opening.values.values())
    assert 2024 not in got.values() and 1 not in got.values()


def test_three_columns_whose_last_is_not_a_total_are_not_a_matrix():
    rows, logs = _build(_page(last_heading=("Group", "2023")))
    assert not any("equity_matrix_columns=3" in m for m in logs)


def test_three_columns_of_bare_integers_are_not_a_matrix():
    """Kaishan 2024 (page index 127): the tail of a cash flow over the first lines of the equity
    statement, its number fragments read as cells of 210, 619, 191 and 621. A component column
    holds printed amounts."""
    words: list[Word] = []
    for c, (top, bottom) in enumerate((("Share", "Capital"), ("Retained", "Earnings"),
                                       ("Total", "Equity"))):
        words.append(_w(top, COL_RIGHT[c] - 0.05, 0.10))
        words.append(_w(bottom, COL_RIGHT[c] - 0.05, 0.10 + PITCH_Y))
    y = 0.20
    for caption, cells in (("Opening", ("993", "210", "619")), ("Movement", ("375", "191", "621")),
                           ("Closing", ("324", "207", "512"))):
        words += _label(caption, y)
        for c, v in enumerate(cells):
            words.append(_cell(v, c, y))
        y += PITCH_Y
    _rows, logs = _build(words)
    assert not any("equity_matrix_columns=3" in m for m in logs)


def test_a_wide_matrix_of_narrow_rows_is_left_to_the_continuation_rule():
    """The three-column path is for a SMALL statement. Zhongshan Public Utilities 2024 (page indexes
    157 and 159): a header-less CAS continuation page of seven components, whose rows print fewer
    than five figures each, passed it and gave eight rows of cut captions. Five columns or more is
    the wide detector's to find, and a page it refuses stays refused."""
    right = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
    words: list[Word] = []
    for c, name in enumerate(("Share", "Premium", "Reserve", "Other", "Retained", "Total")):
        words.append(_w(name, right[c] - 0.05, 0.10))
    y = 0.20
    for caption, cols in (("Opening", (0, 4, 5)), ("Issue", (1, 4, 5)), ("Transfer", (2, 4, 5)),
                          ("Other", (3, 4, 5)), ("Premium", (0, 1, 5)), ("Closing", (2, 3, 5))):
        words += _label(caption, y)
        for c in cols:
            v = "1,234,567"
            width = 0.008 * len(v)
            words.append(_w(v, right[c] - width, y, width))
        y += PITCH_Y
    _rows, logs = _build(words)
    assert not any("equity_matrix_columns=" in m for m in logs)


def test_a_dated_caption_keeps_its_date_cells_off_an_equity_statement():
    """The day and the year of a dated caption are left out of the matrix cells on an equity
    statement only. On a note page they are read as they always were: a property, plant and
    equipment movement note ("As at April 01, 2024") otherwise passed for a matrix, and on Laurus
    Labs 2025-26 its standalone copy (page index 112) was then summed with the consolidated one, the
    depreciation charge coming out 821.09 against the 458.25 printed (p147)."""
    from app.services.row_reconstruct import _EQUITY_DATE_CAPTIONS, _matrix_cells

    row = _label("At 01 January 2024", 0.30) + [_cell("293,223", 0, 0.30)]
    assert [w.text for w in _matrix_cells(row)] == ["01", "2024", "293,223"]
    token = _EQUITY_DATE_CAPTIONS.set(True)
    try:
        assert [w.text for w in _matrix_cells(row)] == ["293,223"]
    finally:
        _EQUITY_DATE_CAPTIONS.reset(token)
    # …and the flag is the equity statement's own: set while that page is read, cleared after.
    _build(_page())
    assert _EQUITY_DATE_CAPTIONS.get() is False


def test_a_word_that_starts_like_a_month_is_not_one():
    """A month as a date prints it — in full, or cut to three or four letters. "Market" and
    "Declared" beside a note number are captions, and the bare `_MONTH_WORD` the caption reader
    uses inside a bracket is not widened by the date test's own pattern."""
    from app.services.row_reconstruct import _MONTH_TOKEN, _MONTH_WORD, _is_caption_date_part

    for month in ("July", "JANUARY", "Dec.", "Sept", "June,"):
        assert _MONTH_TOKEN.match(month)
    for word in ("Market", "Declared", "Decrease", "Mayor", "Junior"):
        assert not _MONTH_TOKEN.match(word)
        assert not _MONTH_WORD.match(word)
    assert not _is_caption_date_part(_label("Dividends declared 26", 0.3), 2)
    assert _is_caption_date_part(_label("Balance at July 1, 2024", 0.3), 3)
    assert _is_caption_date_part(_label("Balance at July 1, 2024", 0.3), 4)
