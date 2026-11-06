r"""A NOTE'S SECOND TABLE READS ON ITS OWN GRID, A YEAR CAPTION IS A ROW, AND A COLUMN HEADER IS
NOT A PERIOD BLOCK — four defects of the notes reader found building the US GAAP (Form 10-K)
template, each measured on a synthetic 10-K note (fictional companies) or on Alphabet's 2025 10-K.

1. A NOTE PRINTING TWO TABLES with no numbered heading between them is ONE section, and a section
   is read with one set of value bands. Northwind's note 9 prints its term debt over "2025 | 2024"
   and then, after a sentence, its future principal payments in ONE column headed "Amount" — whose
   figures, standing at the right edge, were filed under the summary's PRIOR column, every one of
   them "2024". Cascade's note 11 ("Year Ending June 30,") was the same.
   `notes_extract._opens_a_headed_table` starts a new table there.

2. A YEAR IS A NUMBER to the row scanner, so "2026  $ 10,950" had no caption (the row was dropped,
   or took the "Amount" heading as its caption) and "Fiscal 2027  $ 2,598" came back "Fiscal".
   `row_reconstruct._caption_years` reads a schedule of years as row captions; and
   `_without_maturity_years` no longer removes a year that captions its row because a "maturities"
   above it — a word of the introducing sentence, or the table's title — looked like a column head.

3. "Year Ended June 30," set over "2025 | 2024 | 2023" grouped into ONE row with the year line, and
   that row was read as a block anchor OPENING a block of 2024. Every row beneath it was hinted
   `current`, so `note_sourced.select_rows` read cascade's supplemental cash flow note as a movement
   table and `notes_cash__interest_paid` selected nothing. `notes_extract._SPANNED_YEARS`.

4. Alphabet's note 7 prints a receivables sentence ending "… as of December 31, / 2024 and 2025,
   respectively." above its property and equipment table headed "As of December 31," over
   "2024 | 2025". The sentence's two years were read as the table's column header, both columns
   took "2025", the order fell back to position and the 2024 column was read as the current year:
   accumulated depreciation published current -79,390 and prior -98,485, the wrong way round.
   `row_reconstruct._bands_from_a_sentence`.

Each guard is narrow on purpose: run over every notes page of the five reference filings and the two
Ind AS ones, none of the four fires and no note row moves.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.geometry import BBox
from app.schemas.line_items import load_line_item_set
from app.services import note_sourced
from app.services.notes_extract import (
    _SPANNED_YEARS,
    _opens_a_headed_table,
    _period_anchors,
    extract_note_tables,
)
from app.services.row_reconstruct import (
    Word,
    _caption_years,
    _group_rows,
    _without_maturity_years,
)

_CH = 0.0068      # one character's width
_SP = 0.004       # one word space
_H = 0.012        # one line's height
_PITCH = 0.0208   # the row pitch these notes are set at
_SET = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_usgaap_line_items.json")


def _w(text: str, x0: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=min(x0 + _CH * len(text), 0.999), y1=y + _H))


def _phrase(text: str, x0: float, y: float) -> list[Word]:
    """Words of one phrase, set left to right from ``x0`` at an ordinary word space."""
    out, x = [], x0
    for tok in text.split():
        out.append(_w(tok, x, y))
        x += _CH * len(tok) + _SP
    return out


def _right(text: str, x1: float, y: float) -> Word:
    """A figure (or a heading) right-aligned at ``x1``, as a figure column sets it."""
    return _w(text, x1 - _CH * len(text), y)


def _row(caption: str, y: float, *figures: tuple[str, float]) -> list[Word]:
    return _phrase(caption, 0.10, y) + [_right(t, x1, y) for t, x1 in figures]


def _prose(text: str, y: float) -> list[Word]:
    return _phrase(text, 0.09, y)


def _read(words: list[Word], carry: tuple | None = None):
    return extract_note_tables(words, page_index=8, document_id="d", source_kind="native",
                               carry_note=carry)


def _cells(table) -> list[tuple[str, dict[str, Decimal]]]:
    return [(i.raw_label.rstrip(" $"), {v.period_label: v.value for v in i.values.values()})
            for i in table.items]


CUR, PRI = 0.775, 0.92        # the two columns' right edges


# ── 1 + 2: northwind's term debt, then its future principal payments ─────────────────────────────

def _northwind_note_9() -> list[Word]:
    y = 0.06
    words = [_right("2025", CUR, y), _right("2024", PRI, y)]
    summary = [("Total term debt principal $", "97,341", "105,982"),
               ("Unamortized premium/(discount) and issuance costs, net", "(679)", "(879)"),
               ("Total term debt $", "96,662", "105,103"),
               ("Less: Current portion of term debt", "(10,912)", "(9,822)"),
               ("Total non-current portion of term debt $", "85,750", "95,281")]
    for caption, cur, pri in summary:
        y += _PITCH
        words += _row(caption, y, (cur, CUR), (pri, PRI))
    y += 1.6 * _PITCH
    words += _prose("As of September 27, 2025, the future principal payments for the Company's "
                    "term debt were as follows (in millions):", y)
    y += 1.5 * _PITCH
    words.append(_right("Amount", PRI, y))
    for caption, fig in [("2026 $", "10,950"), ("2027", "12,342"), ("2028", "9,936"),
                         ("2029", "7,800"), ("2030", "5,153"), ("Thereafter", "51,160"),
                         ("Total $", "97,341")]:
        y += _PITCH
        words += _row(caption, y, (fig, PRI))
    return words


def test_a_second_table_under_its_own_heading_reads_on_its_own_grid():
    tables = _read(_northwind_note_9(), carry=("9", "Debt"))

    assert [t.note_number for t in tables] == ["9", "9"]
    summary, schedule = tables
    # The summary keeps both of its years.
    assert _cells(summary)[0] == ("Total term debt principal",
                                  {"current": Decimal(97341), "prior": Decimal(105982)})
    # The schedule is ONE column, and it is this year's — not the summary's prior column.
    assert _cells(schedule) == [
        ("2026", {"current": Decimal(10950)}), ("2027", {"current": Decimal(12342)}),
        ("2028", {"current": Decimal(9936)}), ("2029", {"current": Decimal(7800)}),
        ("2030", {"current": Decimal(5153)}), ("Thereafter", {"current": Decimal(51160)}),
        ("Total", {"current": Decimal(97341)})]


def test_only_a_one_column_schedule_heading_opens_a_table():
    """The split is for a schedule of ONE column after a table of several. "2025 | 2024" over two
    columns again is the same grid, and reads as it always did, in one section; so does the
    table's own header, a prose line, and a heading over a row of two figures."""
    y = 0.06
    words = [_right("2025", CUR, y), _right("2024", PRI, y)]
    words += _row("Inventories", y + _PITCH, ("7,286", CUR), ("6,331", PRI))
    words += _row("Raw materials", y + 2 * _PITCH, ("2,810", CUR), ("2,400", PRI))
    y += 4 * _PITCH
    words += [_right("2025", CUR, y), _right("2024", PRI, y)]                  # the same grid again
    words += _row("Land and buildings", y + _PITCH, ("25,000", CUR), ("24,200", PRI))
    y += 3 * _PITCH
    words.append(_right("Amount", PRI, y))                                     # over TWO figures
    words += _row("Machinery", y + _PITCH, ("84,000", CUR), ("78,163", PRI))
    rows = _group_rows(words, 0.006)
    section = {"words": [w for r in rows for w in r]}

    assert not any(_opens_a_headed_table(rows, i, section) for i in range(len(rows)))
    assert len(_read(words, carry=("4", "Details"))) == 1

    rows = _group_rows(_northwind_note_9(), 0.006)
    section = {"words": [w for r in rows for w in r]}
    texts = [[w.text for w in r] for r in rows]
    opened = [texts[i] for i in range(len(rows)) if _opens_a_headed_table(rows, i, section)]
    assert opened == [["Amount"]]


# ── 1 + 2: cascade's "Maturities" / "Year Ending June 30," ───────────────────────────────────────

def test_a_schedule_under_its_title_keeps_its_year_captions():
    y = 0.56
    words = _phrase("June 30, 2025", 0.68, y) + _phrase("June 30, 2024", 0.83, y)
    for caption, cur, pri in [("Total face value $", "43,699", "46,213"),
                              ("Unamortized discount and issuance costs", "(548)", "(1,276)"),
                              ("Total debt $", "43,151", "44,937")]:
        y += _PITCH
        words += _row(caption, y, (cur, CUR), (pri, PRI))
    y += 2 * _PITCH
    words += _phrase("Maturities", 0.09, y)
    y += 1.5 * _PITCH
    words += _phrase("Year Ending June 30,", 0.77, y)
    for caption, fig in [("2026 $", "3,050"), ("2027", "5,250"), ("2028", "4,600"),
                         ("2029", "3,200"), ("2030", "2,150"), ("Thereafter", "25,449"),
                         ("Total $", "43,699")]:
        y += _PITCH
        words += _row(caption, y, (fig, PRI))

    tables = _read(_phrase("NOTE 11 — DEBT", 0.09, 0.53) + words)

    assert [t.note_number for t in tables] == ["11", "11"]
    assert _cells(tables[1])[:2] == [("2026", {"current": Decimal(3050)}),
                                     ("2027", {"current": Decimal(5250)})]
    assert [c for c, _v in _cells(tables[1])] == [
        "2026", "2027", "2028", "2029", "2030", "Thereafter", "Total"]


# ── 2: bluegate's "Fiscal 2027" under the introducing sentence ───────────────────────────────────

def _bluegate_note_6() -> list[Word]:
    y = 0.50
    words = _prose("Annual maturities of long-term debt during the next five years and thereafter "
                   "are (amounts in millions):", y)
    y += 1.5 * _PITCH
    words += _phrase("Annual Maturities", 0.79, y)
    for caption, fig in [("Fiscal 2027 $", "2,598"), ("Fiscal 2028", "3,190"),
                         ("Fiscal 2029", "4,120"), ("Fiscal 2030", "2,804"),
                         ("Fiscal 2031", "3,210"), ("Thereafter", "20,077"), ("Total $", "35,999")]:
        y += _PITCH
        words += _row(caption, y, (fig, PRI))
    return words


def test_a_fiscal_year_caption_keeps_its_year():
    tables = _read(_phrase("Note 6. Short-term Borrowings and Long-term Debt", 0.09, 0.47)
                   + _bluegate_note_6())

    assert [c for c, _v in _cells(tables[0])] == [
        "Fiscal 2027", "Fiscal 2028", "Fiscal 2029", "Fiscal 2030", "Fiscal 2031",
        "Thereafter", "Total"]
    assert _cells(tables[0])[0][1] == {"current": Decimal(2598)}


def test_a_maturity_column_still_loses_its_years():
    """Far East Horizon's note 32, the case the maturity removal exists for: each maturity cell
    follows a caption and a rate, so it is the column's and goes."""
    head = _phrase("rate (%)", 0.42, 0.30) + _phrase("Maturity", 0.51, 0.30) \
        + _phrase("RMB’000", 0.59, 0.30)
    row = (_phrase("Bank loans – secured", 0.18, 0.33) + _phrase("3.45~4.50", 0.41, 0.33)
           + _phrase("2025", 0.525, 0.33) + [_right("63,570", 0.64, 0.33)])
    kept = [w.text for w in _without_maturity_years(head + row)]
    assert "2025" not in kept and "63,570" in kept


def test_a_mainland_schedule_of_years_is_left_as_it_was_read():
    """688008 prints its unused tax losses by year of expiry under 年份 | 期末金额 | 期初金额 — the
    same schedule, in a note whose words are Han. Reading it is not this change's to make."""
    y = 0.30
    rows = [_phrase("年份 期末金额 期初金额", 0.10, y)]
    for year, cur in [("2025", "179,886.98"), ("2026", "10,862,888.37"), ("2027", "6,397,023.73")]:
        y += _PITCH
        rows.append(_phrase(year, 0.10, y) + [_right(cur, 0.60, y), _right("1,234.00", 0.80, y)])
    assert _caption_years(rows, [0.55, 0.75]) == {}


# ── 3: cascade's supplemental cash flow information ──────────────────────────────────────────────

def _cascade_note_19() -> list[Word]:
    a, b, c = 0.66, 0.79, 0.92
    words = _phrase("NOTE 19 — SUPPLEMENTAL CASH FLOW INFORMATION", 0.09, 0.05)
    # "Year Ended June" shares the year line's baseline closely enough to be grouped with it.
    words += _phrase("Year Ended June", 0.555, 0.0855) + [_right("2024", b, 0.0865),
                                                          _right("2023", c, 0.0865)]
    words += _phrase("30, 2025", 0.61, 0.0997)
    words += _phrase("Cash paid during the year for:", 0.09, 0.1191)
    words += _row("Interest on debt $", 0.1399, ("1,789", a), ("2,010", b), ("1,968", c))
    words += _row("Income taxes, net of refunds", 0.1607, ("23,400", a), ("22,100", b),
                  ("21,000", c))
    return words


def test_a_period_phrase_over_year_columns_is_not_a_block():
    tables = _read(_cascade_note_19())

    assert len(tables) == 1
    assert [i.period_hint for i in tables[0].items] == ["", ""]
    assert _cells(tables[0])[0] == ("Interest on debt", {
        "current": Decimal(1789), "prior": Decimal(2010), "col2": Decimal(1968)})


def test_the_cash_paid_lines_read_the_note_by_column():
    tables = _read(_cascade_note_19())
    items = {i.key: i for i in load_line_item_set(
        json.loads(_SET.read_text(encoding="utf-8")), resolve=True).items}
    got = {key: sorted((h.period, h.amount) for h in note_sourced.select_rows(
               items[key], tables, {"current", "prior"}))
           for key in ("notes_cash__interest_paid", "notes_cash__income_taxes_paid")}

    assert got == {"notes_cash__interest_paid": [("current", Decimal(1789)),
                                                 ("prior", Decimal(2010))],
                   "notes_cash__income_taxes_paid": [("current", Decimal(23400)),
                                                     ("prior", Decimal(22100))]}


@pytest.mark.parametrize("caption, spans", [
    ("Year Ended June 2024 2023", True),
    ("Year Ended June 30, 2025 2024 2023", True),
    # A block names one period, and these keep their anchors.
    ("Year ended 31 March 2026", False),
    ("For the year ended 31 December 2024", False),
    ("At 31 December 2022 and 於二零二二年", False),
    ("As at March 31, 2026 As at March 31, 2025", False),
])
def test_only_a_phrase_spanning_several_bare_years_is_a_column_header(caption, spans):
    assert bool(_SPANNED_YEARS.search(caption)) is spans


def test_a_block_heading_over_the_columns_still_opens_its_block():
    """佳明's segment note prints one table per year, each under "Year ended 31 March 2026" centred
    over its columns. That heading names one year, and still opens its block."""
    rows = [_phrase("Year ended 31 March 2026", 0.62, 0.20),
            _row("Segment revenue", 0.25, ("391,802", 0.60), ("868,375", 0.90))]
    assert [(year, carries) for _y, year, carries in _period_anchors(rows)] == [(2026, False)]


# ── 4: Alphabet's property and equipment, under a receivables sentence ──────────────────────────

def test_a_sentence_naming_two_years_is_not_the_tables_header():
    y = 0.4567
    words = _phrase("Note 7. Supplemental Financial Statement Information", 0.089, y)
    y += _PITCH
    words += _phrase("Accounts Receivable", 0.09, y)
    y += _PITCH
    words += _phrase("The allowance for credit losses on accounts receivable was $879 million and "
                     "$924 million as of December 31,", 0.105, y)
    y += 0.0152
    words += _prose("2024 and 2025, respectively.", y)
    y += _PITCH
    words += _phrase("Property and Equipment, Net", 0.09, y)
    y += _PITCH
    words += _phrase("Property and equipment, net, consisted of the following (in millions):",
                     0.105, y)
    y += 1.2 * _PITCH
    words += _phrase("As of December 31,", 0.73, y)
    y += 0.018
    words += [_right("2024", 0.745, y), _right("2025", 0.882, y)]
    for caption, prior, cur in [("Technical infrastructure $", "141,852", "203,679"),
                                ("Office space", "45,403", "48,348"),
                                ("Less: accumulated depreciation", "(79,390)", "(98,485)"),
                                ("Property and equipment, net $", "171,036", "246,597")]:
        y += 0.019
        words += _row(caption, y, (prior, 0.78), (cur, 0.917))

    tables = _read(words)

    depreciation = dict(_cells(tables[0]))["Less: accumulated depreciation"]
    assert depreciation == {"current": Decimal(-98485), "prior": Decimal(-79390)}
