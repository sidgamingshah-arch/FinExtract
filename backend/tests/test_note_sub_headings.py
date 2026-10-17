"""A note row carries the sub-heading it is printed under.

f1 (an Ind AS filing) prints its notes as a figure-less heading at the label column's edge with its
rows indented beneath it, and a table title over each table's "Particulars" header. The reader
glued the heading onto the first row ("Inventories at the end of the year Work-in-progress") or
dropped it, so the second "Finished goods", the second "Balance at the end of the year" and the
second "Total" reached the model with nothing to tell them from the first.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word

_W, _H = 595.0, 842.0
_CHAR, _SPACE = 4.3, 2.2         # points per character, and between two words, at 8pt
_LEFT, _INDENT = 42.0, 54.0
_CUR, _PRI = 425.0, 553.0        # right edges of the two figure columns


def _line(text: str, x0: float, y: float) -> list[Word]:
    """One printed caption: its words set tight, left to right from ``x0`` (points)."""
    out, x = [], x0
    for t in text.split():
        out.append(Word(text=t, bbox=BBox(x0=x / _W, y0=y / _H,
                                          x1=(x + _CHAR * len(t)) / _W, y1=(y + 8) / _H)))
        x += _CHAR * len(t) + _SPACE
    return out


def _right(text: str, x1: float, y: float) -> list[Word]:
    width = sum(_CHAR * len(t) for t in text.split()) + _SPACE * (len(text.split()) - 1)
    return _line(text, x1 - width, y)


def _row(caption: str, x0: float, y: float, cur: str | None = None, pri: str | None = None):
    out = _line(caption, x0, y) if caption else []
    if cur is not None:
        out += _right(cur, _CUR, y)
    if pri is not None:
        out += _right(pri, _PRI, y)
    return out


def _header(y: float, lead: str = "Year ended") -> list[Word]:
    return (_line("Particulars", _LEFT, y) + _right(f"{lead} 31 March 2025", _CUR, y)
            + _right(f"{lead} 31 March 2024", _PRI, y))


def _items(*rows: list[Word]) -> list:
    words = _line("28. Changes in inventories", _LEFT, 40) + [w for r in rows for w in r]
    tables = extract_note_tables(words, page_index=3, document_id=None, source_kind="native")
    return [it for t in tables for it in t.items]


def _by_label(items) -> list[tuple[str, str]]:
    return [(it.raw_label, it.group_hint) for it in items]


def test_a_heading_over_indented_rows_is_their_group_not_their_caption():
    """f1 note 28: two blocks, the same two captions in each."""
    items = _items(
        _header(100.0),
        _row("Inventories at the end of the year", _LEFT, 112.5),
        _row("Work-in-progress", _INDENT, 125.0, "3,412.20", "3,108.77"),
        _row("Finished goods", _INDENT, 137.5, "2,805.45", "2,544.10"),
        _row("Inventories at the beginning of the year", _LEFT, 150.0),
        _row("Work-in-progress", _INDENT, 162.5, "3,108.77", "2,876.40"),
        _row("Finished goods", _INDENT, 175.0, "2,544.10", "2,301.55"),
    )
    got = _by_label(items)
    assert ("Work-in-progress", "Inventories at the end of the year") in got, got
    assert ("Finished goods", "Inventories at the end of the year") in got, got
    assert ("Finished goods", "Inventories at the beginning of the year") in got, got
    assert not any(label.startswith("Inventories at") and "Work" in label for label, _g in got)


def test_a_closing_line_stays_in_its_block_and_the_next_line_leaves_it():
    """f1 note 16's movement (a closing balance, flush with its heading) and note 25's revenue
    (a total that names its block, then a line of its own at the heading's level)."""
    items = _items(
        _header(100.0),
        _row("Retained earnings", _LEFT, 112.5),
        _row("Balance at the beginning of the year", _INDENT, 125.0, "22,876.63", "19,600.24"),
        _row("Profit for the year", _INDENT, 137.5, "4,550.96", "3,732.39"),
        _row("Balance at the end of the year", _LEFT, 150.0, "26,606.79", "22,876.63"),
        _row("(a) Sale of products", _LEFT, 162.5),
        _row("Domestic", _INDENT, 175.0, "65,214.36", "59,036.42"),
        _row("Total sale of products", _LEFT, 187.5, "75,076.54", "67,570.17"),
        _row("(b) Sale of services", _LEFT, 200.0, "742.60", "655.30"),
    )
    got = dict(_by_label(items))
    assert got["Balance at the end of the year"] == "Retained earnings"
    assert got["Total sale of products"] == "(a) Sale of products"
    assert got["(b) Sale of services"] == "", "a heading is invented where none is printed"


def test_a_table_title_over_its_header_heads_every_row_with_any_heading_inside_it():
    """f1 notes 6 and 19: the same captions in a non-current and a current table."""
    def table(title: str, y: float) -> list[list[Word]]:
        return [_row(title, _LEFT, y), _header(y + 12.5),
                _row("Provision for employee benefits", _LEFT, y + 25),
                _row("Gratuity (refer note 29)", _INDENT, y + 37.5, "512.85", "468.20"),
                _row("Total", _LEFT, y + 50, "512.85", "468.20")]
    items = _items(*table("Non-current", 100.0), *table("Current", 225.0))
    got = _by_label(items)
    assert ("Gratuity (refer note 29)", "Non-current / Provision for employee benefits") in got
    assert ("Gratuity (refer note 29)", "Current / Provision for employee benefits") in got
    # A bare "Total" is the table's, not the block's.
    assert [g for label, g in got if label == "Total"] == ["Non-current", "Current"], got


def test_a_hanging_continuation_still_wraps_into_its_caption():
    items = _items(
        _header(100.0),
        _row("Property, plant and", _LEFT, 112.5),
        _row("equipment", _INDENT, 121.9, "1,000.00", "900.00"),
    )
    assert [it.raw_label for it in items] == ["Property, plant and equipment"]
    assert items[0].group_hint == ""


def test_the_running_header_heads_nothing():
    items = _items(
        _row("DEVGIRI AUTOCOMP LIMITED Annual Report 2024-25", _LEFT, 62.5),
        _row("Depreciation for the year", _INDENT, 75.0, "120.00", "110.00"),
    )
    assert [it.group_hint for it in items] == [""]


def test_a_share_count_written_into_the_caption_is_not_a_figure():
    """f1 note 15: "2,50,00,000 (31 March 2024: 2,50,00,000) equity shares" / "of ₹ 10 each"
    beside the amounts. The count and the face value are words of the caption; the two amounts
    are the row's two periods."""
    items = _items(
        _header(100.0, "As at"),
        _row("Authorised", _LEFT, 112.5),
        _row("2,50,00,000 (31 March 2024: 2,50,00,000) equity shares", _INDENT, 125.0),
        _row("of ₹ 10 each", _INDENT, 134.4, "2,500.00", "2,500.00"),
    )
    assert len(items) == 1, [it.raw_label for it in items]
    (row,) = items
    assert row.raw_label.startswith("2,50,00,000 (31 March 2024: 2,50,00,000) equity shares")
    assert row.raw_label.endswith("of ₹ 10 each")
    assert row.group_hint == "Authorised"
    figures = sorted(str(ev.value) for ev in row.values.values())
    assert figures == ["2500.00", "2500.00"], figures


def test_the_open_headings_carry_across_a_page_break():
    """f1 note 32's payment-to-auditors table breaks after "As auditor". On the next page "In
    other capacity" closes that block, and the table's bare "Total" is the title's."""
    from app.services.row_reconstruct import build_line_items

    def build(rows, **kw):
        words = [w for r in rows for w in r]
        items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native",
                                    on_face=False, **kw)
        return items

    out: list = []
    first = build([_row("(a) Payment to auditors", _LEFT, 100.0), _header(112.5),
                   _row("As auditor", _LEFT, 125.0),
                   _row("Statutory audit fee", _INDENT, 137.5, "24.00", "22.00")], group_out=out)
    assert [it.group_hint for it in first] == ["(a) Payment to auditors / As auditor"]
    second = build([_row("Limited review of quarterly results", _INDENT, 100.0, "4.50", "4.00"),
                    _row("In other capacity", _LEFT, 112.5),
                    _row("Certification and other services", _INDENT, 125.0, "1.75", "1.60"),
                    _row("Total", _LEFT, 137.5, "34.50", "31.20")], carry_group=out[0])
    assert [(it.source_label, it.group_hint) for it in second] == [
        ("Limited review of quarterly results", "(a) Payment to auditors / As auditor"),
        ("Certification and other services", "(a) Payment to auditors / In other capacity"),
        ("Total", "(a) Payment to auditors")]


def test_a_dated_caption_keeps_its_date_and_its_figures():
    """The share-count rule leaves a date alone: "31 March 2024" is a caption's date, read back by
    `_label_keeping_its_date`, and the figures beside it are unchanged."""
    items = _items(_header(100.0, "As at"),
                   _row("Balance as at 31 March 2024", _LEFT, 112.5, "550.50", "486.20"))
    assert [it.raw_label for it in items] == ["Balance as at 31 March 2024"]
    assert {"486.20", "550.50"} <= {str(ev.value) for ev in items[0].values.values()}
