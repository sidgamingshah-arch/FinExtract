"""The matrix face: a consolidated statement of changes in equity, whose columns are equity
COMPONENTS rather than periods. Covers column association, the "At <date>" label keeping its
year out of the values, the layout-only fallback, the refusal to guess, and the guarantee that a
two-column comparative is still read exactly as before."""
from __future__ import annotations

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

# Synthetic page geometry: 6 right-aligned component columns, a stacked bilingual header band,
# and a label column on the left. Line pitch/height mirror a real filing (captions wrap onto
# consecutive tight lines), which is what the row grouping has to keep apart.
PITCH_Y = 0.016
LINE_H = 0.012
COL_RIGHT = [0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
COLUMNS = [
    (["", "Issued", "capital"], "已發行股本"),
    (["Share", "premium", "account"], "股份溢價賬"),
    (["", "Other", "reserves"], "其他儲備"),
    (["", "Retained", "profits"], "保留溢利"),
    (["Non-", "controlling", "interests"], "非控股權益"),
    (["", "Total", "equity"], "權益總計"),
]
NAMES = ["Issued capital", "Share premium account", "Other reserves", "Retained profits",
         "Non-controlling interests", "Total equity"]


def _w(text: str, x0: float, y0: float, width: float = 0.06) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + width, y1=y0 + LINE_H))


def _cell(text: str, col: int, y0: float) -> Word:
    """A value cell: printed right-aligned against its column's right edge."""
    width = 0.008 * max(len(text), 1)
    return _w(text, COL_RIGHT[col] - width, y0, width)


def _label(text: str, y0: float, x0: float = 0.05) -> list[Word]:
    """A caption laid out word by word inside the label column (left of the value area)."""
    out, x = [], x0
    for tok in text.split():
        out.append(_w(tok, x, y0, 0.006 * len(tok)))
        x += 0.006 * len(tok) + 0.004
    return out


def _header_band(y0: float = 0.10) -> list[Word]:
    """The header: three English caption lines, the Chinese line, then the units line."""
    words: list[Word] = []
    for line in range(3):
        for c, (english, _) in enumerate(COLUMNS):
            if english[line]:
                words.append(_w(english[line], COL_RIGHT[c] - 0.05, y0 + line * PITCH_Y))
    for c, (_, han) in enumerate(COLUMNS):
        words.append(_w(han, COL_RIGHT[c] - 0.05, y0 + 3 * PITCH_Y))
    for c in range(len(COLUMNS)):
        words.append(_w("RMB’000", COL_RIGHT[c] - 0.05, y0 + 4 * PITCH_Y))
    return words


def _matrix_words() -> list[Word]:
    words = _header_band()
    y = 0.20
    # Opening balance: the date belongs to the LABEL, and every column carries a figure.
    words += _label("At 1 January 2023", y)
    for c, v in enumerate(["365,138", "1,200", "17,619", "21,494,767", "16,914,552",
                           "38,793,276"]):
        words.append(_cell(v, c, y))
    # A movement touching two columns; the rest print a nil dash, which is not a figure.
    y += PITCH_Y
    words += _label("Loss for the year", y)
    for c, v in enumerate(["–", "–", "–", "(7,991,050)", "(410,074)", "(8,401,124)"]):
        words.append(_cell(v, c, y))
    # A caption wrapping over two tight lines: the figures sit on the second one.
    y += PITCH_Y
    words += _label("Dividends paid to non-controlling", y)
    y += PITCH_Y
    words += _label("shareholders of subsidiaries", y, x0=0.06)
    for c, v in enumerate(["–", "–", "–", "–", "(122,425)", "(122,425)"]):
        words.append(_cell(v, c, y))
    # A section heading, which heads the indented rows beneath it rather than wrapping.
    y += PITCH_Y
    words += _label("Other comprehensive income:", y)
    y += PITCH_Y
    words += _label("Revaluation of properties", y, x0=0.06)
    for c, v in enumerate(["–", "–", "8,010", "–", "1,120", "9,130"]):
        words.append(_cell(v, c, y))
    return words


def _build(words: list[Word], statement: str | None = "changes_in_equity", **kw):
    logs: list[str] = []
    items, nxt = build_line_items(words, page_index=7, document_id="d1", source_kind="native",
                                  statement=statement, log=logs.append, **kw)
    return items, nxt, logs


def _by_label(items):
    return {li.source_label: li for li in items}


def test_each_value_is_attributed_to_its_named_equity_component():
    items, _, logs = _build(_matrix_words())
    assert any("equity_matrix_columns=6" in m for m in logs)
    rows = _by_label(items)
    opening = rows["At 1 January 2023"]
    got = {ev.period_label: str(ev.value) for ev in opening.values.values()}
    assert got == {
        "Issued capital": "365138", "Share premium account": "1200",
        "Other reserves": "17619", "Retained profits": "21494767",
        "Non-controlling interests": "16914552", "Total equity": "38793276",
    }
    # Column order/positional keys are never used for a matrix: a component is not a period.
    assert not {"current", "prior"} & set(got)
    # The header caption is what the UI shows for the column, too.
    assert {ev.period_display for ev in opening.values.values()} == set(NAMES)
    assert all(ev.basis is Basis.CONSOLIDATED for ev in opening.values.values())


def test_at_date_label_keeps_the_year_out_of_the_values():
    """The old two-column reconstruction read "At 1 January 2023" as label "At" plus a value of
    2023 in the first component column, shifting every real figure one column right."""
    items, _, _ = _build(_matrix_words())
    opening = _by_label(items)["At 1 January 2023"]
    assert "2023" not in {str(ev.value) for ev in opening.values.values()}
    assert opening.get_value(Basis.CONSOLIDATED, period_label="Issued capital").value == 365138


def test_nil_dashes_are_not_invented_as_zeros_but_still_place_the_columns():
    items, _, _ = _build(_matrix_words())
    loss = _by_label(items)["Loss for the year"]
    assert {ev.period_label: str(ev.value) for ev in loss.values.values()} == {
        "Retained profits": "-7991050", "Non-controlling interests": "-410074",
        "Total equity": "-8401124",
    }


def test_wrapped_caption_is_stitched_and_a_heading_is_not_glued_to_the_row_below():
    items, _, _ = _build(_matrix_words())
    labels = [li.source_label for li in items]
    assert "Dividends paid to non-controlling shareholders of subsidiaries" in labels
    assert "Revaluation of properties" in labels           # heading dropped, not prefixed
    assert not any(lbl.startswith("Other comprehensive income:") for lbl in labels)


def test_every_value_keeps_page_and_bbox_provenance():
    items, _, _ = _build(_matrix_words())
    for li in items:
        for ev in li.values.values():
            assert ev.provenance is not None
            assert ev.provenance.page_index == 7
            assert ev.provenance.bbox is not None and ev.provenance.value_bbox is not None
            assert ev.provenance.label_bbox is not None
            assert ev.provenance.text_snippet == li.source_label


def test_matrix_layout_is_detected_without_the_classifier_signal():
    """A mis-classified page (statement unknown) must still parse as a matrix — five or more
    value columns on a row is a layout no two-column comparative can produce."""
    named, _, _ = _build(_matrix_words())
    blind, _, _ = _build(_matrix_words(), statement=None)
    assert [li.source_label for li in blind] == [li.source_label for li in named]
    assert {ev.period_label for li in blind for ev in li.values.values()} == set(NAMES)


def test_unnameable_matrix_emits_nothing_and_says_why():
    """Columns with no caption above them cannot be attributed; a made-up name would be worse
    than no row at all, so the page is skipped with a log line."""
    words = [w for w in _matrix_words() if w.bbox.y0 >= 0.20]     # header band removed
    items, nxt, logs = _build(words)
    assert items == [] and nxt == 0
    assert any("equity_matrix_unnamed_columns" in m and "skipped" in m for m in logs)


def test_two_column_comparative_is_not_treated_as_a_matrix():
    """The main regression risk: a balance sheet / P&L / cash-flow face must keep its
    positional current+prior keys and its labels, matrix code or not."""
    words = [
        _w("2025", 0.72, 0.10, 0.04), _w("2024", 0.86, 0.10, 0.04),
        *_label("Trade receivables", 0.14), _cell("3,410", 4, 0.14), _cell("2,900", 5, 0.14),
        *_label("Inventories", 0.16), _cell("8,120", 4, 0.16), _cell("7,540", 5, 0.16),
        *_label("Cash and bank balances", 0.18), _cell("1,010", 4, 0.18),
        _cell("990", 5, 0.18),
    ]
    items, _, logs = _build(words, statement="balance_sheet")
    rows = _by_label(items)
    assert set(rows) == {"Trade receivables", "Inventories", "Cash and bank balances"}
    tr = rows["Trade receivables"]
    assert tr.get_value(Basis.CONSOLIDATED, period_label="current").value == 3410
    assert tr.get_value(Basis.CONSOLIDATED, period_label="prior").value == 2900
    assert not logs


def test_four_column_two_basis_statement_is_not_treated_as_a_matrix():
    """Consolidated + standalone × current + prior is four value columns — the widest a
    comparative gets, and still not a matrix."""
    # Each basis header is centred over its own pair of columns (right edges 0.60/0.70 and
    # 0.80/0.90), which is how the banding attributes a value column to a basis.
    words = [
        _w("Consolidated", 0.58, 0.10, 0.10), _w("Standalone", 0.78, 0.10, 0.10),
    ]
    for i, (label, vals) in enumerate([
        ("Trade receivables", ["3,410", "2,900", "1,100", "980"]),
        ("Inventories", ["8,120", "7,540", "2,200", "2,050"]),
        ("Goodwill", ["4,000", "4,000", "500", "500"]),
        ("Other assets", ["120", "130", "60", "70"]),
    ]):
        y = 0.14 + i * PITCH_Y
        words += _label(label, y)
        for c, v in enumerate(vals):
            words.append(_cell(v, c + 2, y))
    items, _, _ = _build(words, statement="balance_sheet")
    tr = _by_label(items)["Trade receivables"]
    assert tr.get_value(Basis.CONSOLIDATED, period_label="current").value == 3410
    assert tr.get_value(Basis.STANDALONE, period_label="current").value == 1100
    assert tr.get_value(Basis.STANDALONE, period_label="prior").value == 980


def test_equity_page_without_a_matrix_layout_falls_back_to_the_two_column_path():
    """A small entity presents changes in equity as a plain comparative; the classifier's
    'changes_in_equity' must not cost it its rows."""
    words = [
        *_label("Share capital", 0.14), _cell("500", 4, 0.14), _cell("500", 5, 0.14),
        *_label("Retained profits", 0.16), _cell("9,120", 4, 0.16), _cell("7,540", 5, 0.16),
        *_label("Total equity", 0.18), _cell("9,620", 4, 0.18), _cell("8,040", 5, 0.18),
    ]
    items, _, logs = _build(words)
    assert len(items) == 3
    assert items[0].get_value(Basis.CONSOLIDATED, period_label="current").value == 500
    assert any("equity_no_matrix_layout" in m for m in logs)


def test_a_header_less_continuation_page_of_a_matrix_is_refused_not_read_as_periods():
    """THE CONTINUATION PAGE. A statutory CAS statement of changes in equity runs over three pages
    per basis, and only the first carries the component header. ``_detect_matrix`` wants
    ``_MATRIX_MIN_ROWS`` rows of ``_MATRIX_MIN_COLS`` amounts before it will believe in a grid, and
    a continuation page does not have them — most movements touch two components, so only the
    balance rows are wide.

    Falling through to the comparative path then reads the component columns as PERIODS, which is
    the one thing ``_matrix_items`` is written to refuse. Measured on 澜起科技 688008: twelve rows
    over pages 160 and 163, among them the 2023 consolidated closing 资本公积 of 5,432,387,416.86
    filed as the CURRENT period's figure — the real 2024 figure, 5,625,969,898.50, is printed on
    the same page.
    """
    words = [
        *_label("三、本期增减变动金额", 0.14), _cell("6,048,987.00", 0, 0.14),
        _cell("193,582,481.64", 1, 0.14),
        *_label("四、本期期末余额", 0.16),
        *[_cell(v, c, 0.16) for c, v in enumerate(
            ["1,144,789,273.00", "5,625,969,898.50", "427,557,874.81", "255,293,498.30",
             "286,559,941.59", "4,518,383,330.50"])],
    ]

    items, nxt, logs = _build(words)

    assert items == [] and nxt == 0
    assert any("equity_matrix_continuation_no_header" in m and "skipped" in m for m in logs)


def test_a_two_column_equity_statement_is_still_not_a_continuation_page():
    """The width of ONE row is the whole discriminator, so the small entity's plain comparative —
    two amounts per row — has to keep falling through. Asserted next to the refusal above because
    the two answers come from the same test."""
    words = [
        *_label("Share capital", 0.14), _cell("500", 4, 0.14), _cell("500", 5, 0.14),
        *_label("Retained profits", 0.16), _cell("9,120", 4, 0.16), _cell("7,540", 5, 0.16),
    ]

    items, _, logs = _build(words)

    assert [li.source_label for li in items] == ["Share capital", "Retained profits"]
    assert any("equity_no_matrix_layout" in m for m in logs)


def _bases(items) -> set:
    return {ev.basis for li in items for ev in li.values.values()}


def test_the_page_scope_decides_whose_equity_statement_the_matrix_is():
    """A matrix's columns are components, so no column header names an entity and the words name
    none either — the page classifier's verdict is the only evidence there is. Without it the
    Company's own statement of changes in equity (an HKEX filing prints one past the notes, as
    the reserve note) was filed as the Group's, under the same component names.
    """
    company, _, _ = _build(_matrix_words(), page_scope="company")
    assert _bases(company) == {Basis.STANDALONE}
    group, _, _ = _build(_matrix_words(), page_scope="consolidated")
    assert _bases(group) == {Basis.CONSOLIDATED}
    # Unclassified pages keep reading their own words, exactly as before.
    assert _bases(_build(_matrix_words())[0]) == {Basis.CONSOLIDATED}


def test_a_note_is_not_relabelled_by_the_page_scope():
    """``PageSource.scope`` describes a STATEMENT. A note tabulating the Company's investments in
    subsidiaries belongs to the consolidated statements it is a note to, so the face-only rule the
    two-column path applies has to hold for the matrix path as well."""
    items, _, _ = _build(_matrix_words(), page_scope="company", on_face=False)
    assert _bases(items) == {Basis.CONSOLIDATED}


def test_every_component_records_which_column_it_was_printed_in():
    """The printed left-to-right order is part of a statement of changes in equity's meaning, and
    on a sideways page it cannot be recovered from the value's box — there the columns advance
    down the page's y. So each fact carries its column's index."""
    items, _, _ = _build(_matrix_words())
    opening = _by_label(items)["At 1 January 2023"]
    got = {ev.column_index: ev.period_label for ev in opening.values.values()}
    assert got == dict(enumerate(NAMES))
    # A period-keyed fact has no column axis to record.
    two_col, _, _ = _build([_w("Cash and bank", 0.05, 0.30),
                            _cell("1,000", 0, 0.30), _cell("900", 1, 0.30),
                            _w("Trade receivables", 0.05, 0.30 + PITCH_Y),
                            _cell("2,000", 0, 0.30 + PITCH_Y),
                            _cell("1,900", 1, 0.30 + PITCH_Y)], statement=None)
    assert two_col and all(ev.column_index is None
                           for li in two_col for ev in li.values.values())
