"""Every row of a table whose columns are not years reaches the model with the year it is.

f1 (Ind AS, INR lakhs) printed four shapes whose rows went out as `period: unstated`, or as two
rows with the same caption and different figures and nothing to tell them apart:

* the ageing schedules of notes 10 and 22, one table per year, each under a dated title
  ("Trade receivables ageing schedule as at 31 March 2024");
* the right-of-use and intangible movement notes, whose block heading is set beside the opening
  balance ("Gross carrying amount" | "Balance as at 1 April 2023"), so the caption lost its date,
  and whose "As at 31 March 2025" net carrying amount was dropped as a column-header line;
* the period x measure table of note 27 ("Year ended 31 March 2025 | % of total consumption |
  Year ended 31 March 2024 | % of total consumption"), whose two % columns were one key;
* the faces, whose headings "As at 31 March 2025  As at 31 March 2024" were read as one phrase
  (or lost to the statement's dated title), and "Year ended 31 March" over "2025 | 2024" as
  "March Year" / "March".
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
from app.services import note_context
from app.services import row_reconstruct as rr
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources
from app.services.row_reconstruct import Word

GLYPH = 0.006


def _row(text: str, y: float = 0.5, x: float = 0.10, step: float = GLYPH) -> list[Word]:
    out: list[Word] = []
    for token in text.split():
        out.append(Word(text=token, bbox=BBox(x0=x, y0=y, x1=x + GLYPH * len(token), y1=y + 0.01)))
        x += GLYPH * len(token) + step
    return out


def _right(text: str, x1: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x1 - 0.007 * len(text), y0=y, x1=x1, y1=y + 0.01))


# ── the reader: captions and rows ─────────────────────────────────────────────────────────────

def test_a_balance_caption_under_a_block_heading_keeps_its_date():
    row = _row("Gross carrying amount Balance as at 1 April 2023 612.40 1,124.80 1,737.20")
    label_words, _note, value_words = rr._scan_row(row)
    assert rr._join_words(label_words) == "Gross carrying amount Balance as at"
    fixed = rr._label_keeping_its_date(row, label_words, value_words)
    assert rr._join_words(fixed) == "Gross carrying amount Balance as at 1 April 2023"
    # An ordinary caption under the same heading is left as it was.
    row = _row("Gross carrying amount Additions 286.50 286.50")
    label_words, _note, value_words = rr._scan_row(row)
    assert rr._join_words(rr._label_keeping_its_date(row, label_words, value_words)) == \
        "Gross carrying amount Additions"


def _note_rows(lines: list[tuple[str, list[str]]]) -> list[Word]:
    ws: list[Word] = []
    for k, (caption, figures) in enumerate(lines):
        y = 0.30 + 0.014 * k
        ws += _row(caption, y=y, x=0.07, step=0.004)
        ws += [_right(v, x1, y) for v, x1 in zip(figures, (0.55, 0.70, 0.85))]
    return ws


def test_a_notes_dated_net_carrying_amount_is_a_row_not_a_header():
    ws = _note_rows([("Amortisation for the year", ["52.60", "40.00", "92.60"]),
                     ("Net carrying amount", []),
                     ("As at 31 March 2024", ["205.15", "156.25", "361.40"]),
                     ("As at 31 March 2025", ["211.30", "266.25", "477.55"])])
    items, _ = rr.build_line_items(ws, page_index=8, document_id=None, source_kind="native",
                                   on_face=False)
    closing = [li for li in items if li.source_label.startswith("As at 31 March 2025")]
    assert closing and sorted(str(v.value) for v in closing[0].values.values()) == \
        ["211.30", "266.25", "477.55"]
    # A header band's own date fragments are not figures: that line is still not a row.
    assert not rr._dated_balance_with_figures("As at 31 March 2025",
                                              [Decimal("31"), Decimal("2025")])


# ── the reader: face column headings ─────────────────────────────────────────────────────────

def test_two_close_set_as_at_headings_are_two_columns():
    rows = [_row("Particulars Note As at 31 March 2025 As at 31 March 2024", y=0.117, x=0.071,
                 step=0.012),
            [Word("Revenue", BBox(x0=0.07, y0=0.15, x1=0.12, y1=0.16)),
             _right("77,644.10", 0.70, 0.15), _right("69,882.62", 0.90, 0.15)]]
    texts = [t for t, _x in rr._period_bands(rows)]
    assert texts == ["As at 31 March 2025", "As at 31 March 2024"]


def test_a_heading_wrapped_before_its_year_takes_the_year_below_it():
    def w(t, x0, y):
        return Word(t, BBox(x0=x0, y0=y, x1=x0 + 0.006 * len(t), y1=y + 0.01))
    rows = [[w("Particulars", 0.071, 0.117), w("Note", 0.475, 0.117), w("Year", 0.554, 0.117),
             w("ended 31", 0.595, 0.117), w("March", 0.673, 0.117), w("Year", 0.762, 0.117),
             w("ended 31", 0.803, 0.117), w("March", 0.881, 0.117)],
            [w("No.", 0.486, 0.131), w("2025", 0.682, 0.131), w("2024", 0.891, 0.131)],
            [w("Revenue", 0.084, 0.148), _right("77,644.10", 0.70, 0.148),
             _right("69,882.62", 0.90, 0.148)]]
    texts = [t for t, _x in rr._period_bands(rows)]
    assert texts == ["Year ended 31 March 2025", "Year ended 31 March 2024"]


# ── the request: the period of each row ──────────────────────────────────────────────────────

CLASSES = ("Leasehold land", "Buildings", "Total")


def _matrix(caption: str, *amounts: str) -> NoteItem:
    row = NoteItem(raw_label=caption)
    for k, (name, amount) in enumerate(zip(CLASSES, amounts)):
        row.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                     basis=Basis.STANDALONE, period_label=name,
                                     period_display=name, column_index=k,
                                     provenance=Provenance(page_index=7)))
    return row


def _rou() -> NotesTable:
    rows = [_matrix("Gross carrying amount Balance as at 1 April 2023", "612.40", "1124.80",
                    "1737.20"),
            _matrix("Additions", "0", "286.50", "286.50"),
            _matrix("Balance as at 31 March 2024", "612.40", "1411.30", "2023.70"),
            _matrix("Additions", "0", "342.20", "342.20"),
            _matrix("Balance as at 31 March 2025", "612.40", "1753.50", "2365.90")]
    return NotesTable(note_number="4", title="Right-of-use assets", items=rows,
                      source_text="Right-of-use assets Particulars ...")


def _cell(key, amount, heading, x):
    return ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount), basis=Basis.STANDALONE,
                          period_label=key, column_heading=heading,
                          provenance=Provenance(page_index=9, value_bbox=BBox(
                              x0=x - 0.05, y0=0.5, x1=x, y1=0.51)))


def _aged(caption: str, not_due: str, total: str) -> NoteItem:
    row = NoteItem(raw_label=caption)
    for c in (_cell("current", not_due, "Not due", 0.36),
              _cell("current_col1", "1.00", "Less than 6 months", 0.45),
              _cell("current_col2", "2.00", "6 months - 1 year", 0.52),
              _cell("prior_col5", total, "Total", 0.89)):
        row.set_value(c)
    return row


def _receivables() -> NotesTable:
    cap = "(i) Undisputed trade receivables - considered good"
    rows = [_aged(cap, "10486.15", "10489.15"), _aged(cap, "9512.40", "9515.40")]
    text = ("Trade receivables ageing schedule as at 31 March 2025 (outstanding for following "
            "periods) Particulars Not due Less than 6 months " + cap + " 10,486.15 1.00 2.00 "
            "10,489.15 Trade receivables ageing schedule as at 31 March 2024 (outstanding for "
            "following periods) Particulars Not due Less than 6 months " + cap + " 9,512.40 ...")
    return NotesTable(note_number="10", title="Trade receivables", items=rows, source_text=text)


def _identified(tables) -> dict[str, list[dict]]:
    items = [LineItemDef(key="k", label="k", note_source=NoteSource(note_title_any=[".*"]))]
    return {n["note"]: n["rows"] for n in
            note_context.identified_notes(LineItemSet(items=items), tables)}


def test_an_opening_balance_dated_before_both_years_is_earlier_not_unstated():
    rows = _identified([_rou()])["4"]
    assert rows[0]["caption"] == "Gross carrying amount Balance as at 1 April 2023"
    assert rows[0]["period"] == "earlier"
    assert rows[0]["period_as_printed"] == "as at 1 April 2023"
    assert [r["period"] for r in rows[1:]] == ["prior", "prior", "current", "current"]
    # Neither year's figure, so a citation of it is refused rather than filed under one.
    _, refused = resolve_sources(
        [SourceRef(note="4", caption="Gross carrying amount Balance as at 1 April 2023",
                   column="Total")], [_rou()])
    assert refused and "before both years" in refused[0]["why"]


def test_an_ageing_table_takes_the_date_of_its_title():
    rows = _identified([_rou(), _receivables()])["10"]
    assert [(r["period"], r.get("period_as_printed")) for r in rows] == [
        ("current", "as at 31 March 2025"), ("prior", "as at 31 March 2024")]
    resolved, unresolved = resolve_sources(
        [SourceRef(note="10", caption="(i) Undisputed trade receivables - considered good",
                   column="Not due", period="prior")], [_rou(), _receivables()])
    assert unresolved == [] and resolved[0]["figures"] == {"prior": "9512.40"}


def test_an_ageing_table_with_no_dated_title_stays_unstated():
    table = _receivables()
    table.source_text = table.source_text.replace(" as at 31 March 2025", "").replace(
        " as at 31 March 2024", "")
    rows = _identified([_rou(), table])["10"]
    assert [r["period"] for r in rows] == ["unstated", "unstated"]


def _consumption() -> NotesTable:
    row = NoteItem(raw_label="Imported")
    for c in (_cell("current", "5889.36", "Year ended 31 March 2025 (₹ in lakhs)", 0.55),
              _cell("prior", "12.40", "of total consumption", 0.65),
              _cell("col2", "5101.75", "Year ended 31 March 2024 (₹ in lakhs)", 0.75),
              _cell("col3", "11.90", "of total consumption", 0.85)):
        row.set_value(c)
    return NotesTable(note_number="27", title="Cost of materials consumed", items=[row],
                      source_text="Value of imported and indigenous raw materials Imported ...")


def test_a_period_by_measure_row_keeps_both_measure_columns_and_files_each_by_its_year():
    rows = _identified([_rou(), _consumption()])["27"]
    assert rows[0]["period"] == "per column"
    assert rows[0]["figures"] == {
        "Year ended 31 March 2025 (₹ in lakhs)": "5889.36",
        "of total consumption (31 March 2025)": "12.40",
        "Year ended 31 March 2024 (₹ in lakhs)": "5101.75",
        "of total consumption (31 March 2024)": "11.90"}
    resolved, unresolved = resolve_sources(
        [SourceRef(note="27", caption="Imported",
                   column="Year ended 31 March 2024 (₹ in lakhs)")], [_rou(), _consumption()])
    assert unresolved == [] and resolved[0]["figures"] == {"prior": "5101.75"}
