"""A leading small integer is a note reference only when the row's figures say so.

Two defects of the note fallback, measured on PetroChina 2024 (hkex_0857), and the glued-header
case that was meant to rescue it:

* the IFRS cash-flow statement (RMB millions) prints "Impairment of other non-current assets
  42  259" on a page with no note column. 259 has three digits, so 42 was read as note 42 and the
  current-year figure deleted: operating cash flow summed to 406,490 against a printed 406,532.
* a note column whose header is glued to its neighbour ("Notes2024") was never detected, because
  the header test is anchored on the whole word.
* and the same glued shape printed for another reason — PetroChina's superscript "Note" footnote
  marker on its 2023 heading, extracted as "2023Note" over the comparatives — must not turn the
  prior-year column into a note column.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, _detect_note_column, _group_rows, build_line_items


def _w(text: str, x0: float, y0: float, x1: float | None = None) -> Word:
    x1 = x0 + 0.009 * len(text) if x1 is None else x1
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y0 + 0.012))


def _build(words: list[Word]):
    items, _ = build_line_items(words, page_index=0, document_id="d1", source_kind="native")
    return {i.source_label: i for i in items}


def _figures(item) -> list[str]:
    return sorted(str(v.value) for v in item.values.values())


def _cash_flow_page(header: list[Word]) -> list[Word]:
    """PetroChina's IFRS cash-flow layout: caption, current at ~0.75, comparative at ~0.89."""
    rows = [("Profit for the year", "183,755", "180,563"),
            ("Accrual of provision for impairment of receivables, net", "743", "43"),
            ("Write down in inventories, net", "2,367", "6,411"),
            ("Impairment of other non-current assets", "42", "259"),
            ("Dividend income", "(30)", "(18)"),
            ("Interest expense", "20,731", "24,063")]
    words = list(header)
    for i, (label, cur, pri) in enumerate(rows):
        y = 0.30 + 0.02 * i
        words.append(_w(label, 0.15, y, 0.15 + 0.006 * len(label)))
        words.append(_w(cur, 0.77 - 0.009 * len(cur), y, 0.77))
        words.append(_w(pri, 0.905 - 0.009 * len(pri), y, 0.905))
    return words


def test_two_small_figures_are_two_figures_not_a_note_and_a_figure():
    page = _cash_flow_page([_w("2024", 0.736, 0.25, 0.770), _w("2023", 0.854, 0.25, 0.887)])
    by_label = _build(page)
    line = by_label["Impairment of other non-current assets"]
    assert line.note_number is None, "42 is the current-year figure, not note 42"
    assert _figures(line) == ["259", "42"]


def test_a_footnote_marker_glued_to_the_comparative_heading_is_not_a_note_column():
    """"2023Note" sits over the 2023 figures; "43" beneath it is a small comparative, not note 43."""
    header = [_w("2024", 0.736, 0.25, 0.770), _w("2023Note", 0.854, 0.25, 0.905)]
    page = _cash_flow_page(header)
    assert _detect_note_column(_group_rows(page)) is None
    by_label = _build(page)
    assert _figures(by_label["Accrual of provision for impairment of receivables, net"]) == ["43", "743"]
    assert _figures(by_label["Impairment of other non-current assets"]) == ["259", "42"]
    assert by_label["Impairment of other non-current assets"].note_number is None


def test_a_note_column_header_glued_to_the_year_is_still_the_note_column():
    """"Notes2024" printed with no space: the note column is found from the 'Notes' part, and a
    row whose only figure is note-number shaped keeps its note AND its figure."""
    # the glued word's characters are contiguous: "Notes" over the note column, "2024" after it
    header = [_w("Notes2024", 0.53, 0.25, 0.611), _w("2023", 0.85, 0.25, 0.89)]
    body = []
    for i, (label, note, cur, pri) in enumerate([("Cash and cash equivalents", "7", "1,259", "1,100"),
                                                 ("Loans to associates", "8", "259", "300"),
                                                 ("Other receivables", "9", "42", None)]):
        y = 0.30 + 0.02 * i
        body += [_w(label, 0.10, y, 0.10 + 0.006 * len(label)), _w(note, 0.55, y, 0.559),
                 _w(cur, 0.77 - 0.009 * len(cur), y, 0.77)]
        if pri:
            body.append(_w(pri, 0.89 - 0.009 * len(pri), y, 0.89))
    assert _detect_note_column(_group_rows(header + body)) is not None
    by_label = _build(header + body)
    assert by_label["Other receivables"].note_number == "9"
    assert _figures(by_label["Other receivables"]) == ["42"]
    assert by_label["Loans to associates"].note_number == "8"
    assert _figures(by_label["Loans to associates"]) == ["259", "300"]


def test_a_chinese_note_header_glued_to_the_caption_header_is_the_note_column():
    header = [_w("项目附注", 0.10, 0.25, 0.56), _w("2024年", 0.72, 0.25, 0.78), _w("2023年", 0.85, 0.25, 0.91)]
    body = []
    for i, (label, note, cur) in enumerate([("货币资金", "1", "451,359,912.13"), ("应收账款", "5", "36"),
                                            ("存货", "9", "12")]):
        y = 0.30 + 0.02 * i
        body += [_w(label, 0.10, y, 0.18), _w(note, 0.445, y, 0.452), _w(cur, 0.80 - 0.008 * len(cur), y, 0.80)]
    by_label = _build(header + body)
    assert by_label["应收账款"].note_number == "5"
    assert _figures(by_label["应收账款"]) == ["36"]


def test_a_note_number_before_a_formatted_amount_or_two_figures_is_still_a_note():
    """What the fallback is for, unchanged: "Revenue  6  45,230" and "Revenue  6  259  300"."""
    by_label = _build([_w("Revenue", 0.10, 0.30), _w("6", 0.55, 0.30), _w("45,230", 0.72, 0.30),
                       _w("Other", 0.10, 0.34), _w("income", 0.16, 0.34), _w("7", 0.55, 0.34),
                       _w("259", 0.74, 0.34), _w("300", 0.86, 0.34)])
    assert by_label["Revenue"].note_number == "6"
    assert _figures(by_label["Revenue"]) == ["45230"]
    assert by_label["Other income"].note_number == "7"
    assert _figures(by_label["Other income"]) == ["259", "300"]


def test_a_note_before_a_nil_dash_and_a_small_comparative_is_still_a_note():
    """"Impairment of right-of-use assets  7  –  218" (Far East Horizon), "Dividends paid  33  –
    (1)" (佳明): note, nil, comparative. The dash is a cell, so the row has two cells after the
    note and the 7 is not a current-year figure."""
    by_label = _build([_w("Impairment", 0.10, 0.30), _w("of", 0.20, 0.30), _w("right-of-use", 0.22, 0.30),
                       _w("assets", 0.33, 0.30), _w("7", 0.547, 0.30, 0.551), _w("–", 0.70, 0.30, 0.71),
                       _w("218", 0.81, 0.30, 0.838),
                       _w("Dividends", 0.10, 0.34), _w("paid", 0.19, 0.34), _w("33", 0.547, 0.34, 0.557),
                       _w("－", 0.70, 0.34, 0.71), _w("(1)", 0.82, 0.34, 0.838)])
    # (on a page this small the dash is read into the caption; placing it is another reader's job)
    line = {k.rstrip(" –－"): v for k, v in by_label.items()}
    assert line["Impairment of right-of-use assets"].note_number == "7"
    assert _figures(line["Impairment of right-of-use assets"]) == ["218"]
    assert line["Dividends paid"].note_number == "33"
    assert _figures(line["Dividends paid"]) == ["-1"]


def test_where_the_page_has_a_note_column_a_small_figure_outside_it_is_a_figure():
    """佳明's cash flow: Notes column at x≈0.66, "Interest received  30  8,127" with the 30 in the
    2026 column. Read on shape alone, 30 was note 30 and the current year's figure was deleted."""
    header = [_w("Notes", 0.645, 0.25, 0.669), _w("2026", 0.765, 0.25, 0.795), _w("2025", 0.885, 0.25, 0.915)]
    body = []
    for i, (label, note, cur, pri) in enumerate([("Proceeds from bank loans", "33", "1,764,521", "1,104,681"),
                                                 ("Repayments of bank loans", "33", "(1,611,490)", "(1,716,150)"),
                                                 ("Interest received", None, "30", "8,127")]):
        y = 0.30 + 0.02 * i
        body.append(_w(label, 0.10, y, 0.10 + 0.006 * len(label)))
        if note:
            body.append(_w(note, 0.650, y, 0.664))
        body += [_w(cur, 0.795 - 0.009 * len(cur), y, 0.795), _w(pri, 0.915 - 0.009 * len(pri), y, 0.915)]
    by_label = _build(header + body)
    assert by_label["Interest received"].note_number is None
    assert _figures(by_label["Interest received"]) == ["30", "8127"]
    assert by_label["Proceeds from bank loans"].note_number == "33"
