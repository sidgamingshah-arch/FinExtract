"""A HALF OF A 2-UP SPREAD PRINTED SIDEWAYS IS READ SIDEWAYS, as a page printed sideways whole is.

JSW Steel's integrated report 2024-25, sheet 219: the standalone statement of changes in equity is
set a quarter-turn on the left page — its words run up the sheet — beside the standalone cash flow
printed upright on the right. The sheet reads upright, so:

* classify never saw the half's title. Its lines were ordered by the sheet's height, the title
  "STANDALONE STATEMENT OF CHANGES IN EQUITY" (drawn up the left edge) was no line of the title
  zone, and the half was decoded as a face with no title, taking the statement before it — the
  standalone profit and loss on the right half of sheet 218;
* extract grouped its words by their shared height, which on a sideways table is a column, not a
  row, and the "rows" it made were read as profit and loss: other income -190 (prior year), OCI
  items 301 / 208 and -890, beside a dozen unclassified rows of glued column headings.

The consolidated statement of changes in equity, printed sideways on a sheet of its own (277), was
already read sideways (`pdf_extract.text_rotation`) and classified by its title.

Each test of the shape fails on the code before the change.
"""
from __future__ import annotations

import pytest

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word

# ------------------------------------------------------------------ classify, the lines ---


def _line(text, x0, x1, y0, y1, rot):
    return {"text": text, "y": float(y0), "y1": float(y1), "size": 8.0, "bold": False,
            "x0": float(x0), "x1": float(x1), "rot": rot, "chars": len(text)}


def test_a_half_reads_in_the_direction_most_of_its_text_is_drawn():
    from app.stages.classify import _half_rotation

    body = [_line("Retained earnings 41,054 48,473", 100 + 14 * i, 108 + 14 * i, 300, 700, 90)
            for i in range(6)]
    chrome = [_line("FINANCIAL STATEMENTS | STANDALONE", 30, 300, 20, 30, 0),
              _line("436", 30, 40, 820, 830, 0)]
    assert _half_rotation(body + chrome) == 90
    assert _half_rotation(chrome) == 0
    assert _half_rotation([]) == 0


def test_a_sideways_half_is_ordered_as_its_printed_page_reads():
    """Text that runs up the sheet reads from the half's left edge down; its upright running
    header and folio are not lines of it; the half's height is its width."""
    from app.stages.classify import _sideways_half

    lines = [
        _line("FINANCIAL STATEMENTS | STANDALONE", 30, 300, 20, 30, 0),
        _line("Dividends (822) (822)", 300, 308, 200, 780, 90),
        _line("STANDALONE STATEMENT OF CHANGES IN EQUITY", 30, 44, 120, 610, 90),
        _line("For the year ended 31 March 2025", 50, 58, 400, 610, 90),
    ]
    out, height = _sideways_half(lines, 0.0, 595.0, 842.0, 90)
    assert [line["text"] for line in out] == ["STANDALONE STATEMENT OF CHANGES IN EQUITY",
                                              "For the year ended 31 March 2025",
                                              "Dividends (822) (822)"]
    assert height == 595.0
    title = out[0]
    assert (title["y"], title["x0"], title["x1"]) == (30.0, 842.0 - 610.0, 842.0 - 120.0)


def test_text_that_runs_down_the_sheet_reads_from_the_half_s_right_edge():
    from app.stages.classify import _sideways_half

    lines = [_line("first", 580, 590, 100, 300, 270), _line("second", 560, 570, 100, 300, 270)]
    out, height = _sideways_half(lines, 595.0, 1190.0, 842.0, 270)
    assert [line["text"] for line in out] == ["first", "second"]
    assert height == 595.0 and out[0]["y"] == 1190.0 - 590.0


# ------------------------------------------------------------------- extract, the words ---

def _word(text, reading, sheet):
    return Word(text=text, bbox=BBox(x0=reading[0], y0=reading[1], x1=reading[2], y1=reading[3]),
                page_bbox=BBox(x0=sheet[0], y0=sheet[1], x1=sheet[2], y1=sheet[3]))


def test_a_sideways_half_s_words_are_measured_across_the_half():
    from app.services.pdf_extract import in_sideways_half_frame

    # Text drawn up the sheet: reading y IS the sheet's x. A word a quarter of the sheet across
    # is half way down the left page; a word on the right page is not the left half's.
    on_left = _word("Dividends", (0.10, 0.25, 0.20, 0.26), (0.25, 0.80, 0.26, 0.90))
    on_right = _word("Cash", (0.10, 0.75, 0.20, 0.76), (0.75, 0.80, 0.76, 0.90))
    (word,) = in_sideways_half_frame([on_left, on_right], 0.0, 0.5, 90)
    assert word.text == "Dividends"
    assert (word.bbox.y0, word.bbox.y1) == pytest.approx((0.50, 0.52))
    assert (word.bbox.x0, word.bbox.x1) == (0.10, 0.20)
    assert word.page_bbox == on_left.page_bbox          # provenance stays where it is drawn

    # Text drawn down the sheet: reading y is 1 - x, so the right half starts at 1 - 1.0.
    down = _word("Total", (0.10, 0.10, 0.20, 0.11), (0.89, 0.10, 0.90, 0.20))
    (word,) = in_sideways_half_frame([down], 0.5, 1.0, 270)
    assert (word.bbox.y0, word.bbox.y1) == pytest.approx((0.20, 0.22))


# -------------------------------------------------------------------- the sheet, end to end ---

fitz = pytest.importorskip("fitz")


def _jsw_shaped_pdf() -> bytes:
    """Two sheets of a JSW-shaped report: the balance sheet and the profit and loss upright, then
    the statement of changes in equity set sideways beside the cash flow set upright."""
    doc = fitz.open()
    sheet = doc.new_page(width=1191, height=842)
    put = lambda p, x, y, t: p.insert_text((x, y), t, fontsize=8)
    num = lambda p, x, y, t: p.insert_text(
        (x + 40 - fitz.get_text_length(t, fontsize=8), y), t, fontsize=8)
    put(sheet, 40, 40, "FINANCIAL STATEMENTS | STANDALONE")
    put(sheet, 40, 70, "Standalone Balance Sheet"); put(sheet, 40, 84, "As at 31 March 2025")
    put(sheet, 40, 98, "(₹ in crores)")
    put(sheet, 40, 118, "Particulars"); put(sheet, 330, 118, "Notes")
    put(sheet, 420, 118, "31 March 2025"); put(sheet, 490, 118, "31 March 2024")
    y = 140
    for cap, a, b in (("Inventories", "19,819", "23,234"), ("Trade receivables", "5,672", "6,498"),
                      ("Cash and cash equivalents", "9,595", "4,953"),
                      ("Total current assets", "46,344", "43,094")):
        put(sheet, 52, y, cap); num(sheet, 420, y, a); num(sheet, 490, y, b); y += 14
    put(sheet, 640, 70, "Standalone Statement of Profit and Loss")
    put(sheet, 640, 84, "For the year ended 31 March 2025"); put(sheet, 640, 98, "(₹ in crores)")
    put(sheet, 640, 118, "Particulars"); put(sheet, 930, 118, "Notes")
    put(sheet, 1010, 118, "31 March 2025"); put(sheet, 1080, 118, "31 March 2024")
    y = 140
    for cap, a, b in (("Revenue from operations", "1,27,702", "1,34,128"),
                      ("Other income", "1,865", "1,180"),
                      ("Cost of materials consumed", "65,779", "77,129"),
                      ("Other expenses", "30,121", "29,315"),
                      ("Profit before tax", "6,543", "12,102")):
        put(sheet, 652, y, cap); num(sheet, 1020, y, a); num(sheet, 1090, y, b); y += 14

    sheet = doc.new_page(width=1191, height=842)
    put(sheet, 40, 30, "FINANCIAL STATEMENTS | STANDALONE")
    put(sheet, 40, 830, "436 JSW STEEL")
    # LEFT — the statement of changes in equity, a quarter-turn: words run up the sheet, so a
    # row is a column of the sheet (one x), its caption starting at the foot, and successive
    # rows advance across it. A figure is set to END at its column's height.
    up = lambda x, y, t: sheet.insert_text((x, y), t, fontsize=8, rotate=90)
    fig = lambda x, end, t: sheet.insert_text(
        (x, end + fitz.get_text_length(t, fontsize=8)), t, fontsize=8, rotate=90)
    up(60, 800, "STANDALONE STATEMENT OF CHANGES IN EQUITY")
    up(80, 800, "For the year ended 31 March 2025")
    up(110, 800, "(₹ in crores)")
    up(150, 800, "Particulars")
    # The other-equity matrix, as JSW prints it: a column per reserve and a total, each heading
    # wrapped over two lines.
    ends = (560, 490, 420, 350, 280, 210)
    for end, heading in zip(ends, ("Capital reserve", "Securities premium", "Retained earnings",
                                   "General reserve", "Hedge reserve", "Total")):
        for x, part in zip((142, 151), heading.split()):
            sheet.insert_text((x, end + fitz.get_text_length(part, fontsize=7)), part,
                              fontsize=7, rotate=90)
    rows = (("Opening balance", "4,359", "7,742", "48,473", "10,213", "(890)", "69,897"),
            ("Profit for the year", "-", "-", "3,560", "-", "-", "3,560"),
            ("Other comprehensive income", "-", "-", "(190)", "-", "415", "225"),
            ("Dividends", "-", "-", "(822)", "-", "-", "(822)"),
            ("Transfer to general reserve", "-", "-", "(203)", "203", "-", "-"),
            ("Closing balance", "4,359", "7,742", "50,818", "10,416", "(475)", "72,860"))
    x = 190
    for caption, *figures in rows:
        up(x, 800, caption)
        for end, figure in zip(ends, figures):
            fig(x, end, figure)
        x += 40
    # RIGHT — the cash flow, upright.
    put(sheet, 640, 70, "Standalone Statement of Cash Flows")
    put(sheet, 640, 84, "For the year ended 31 March 2025"); put(sheet, 640, 98, "(₹ in crores)")
    put(sheet, 1010, 118, "31 March 2025"); put(sheet, 1080, 118, "31 March 2024")
    y = 140
    # As on JSW's sheet, most of the sheet's text is the upright half's, so the sheet reads
    # upright and is a spread; a sheet mostly sideways is read sideways whole.
    for cap, a, b in (("Profit before tax", "6,543", "12,102"),
                      ("Depreciation and amortisation expenses", "5,913", "5,435"),
                      ("Loss on sale of property, plant and equipment (net)", "138", "81"),
                      ("Interest income", "(1,684)", "(1,303)"),
                      ("Interest expense", "6,199", "6,055"),
                      ("Share based payment expense", "169", "208"),
                      ("Unrealised exchange (gain) / loss (net)", "382", "(202)"),
                      ("Operating profit before working capital changes", "18,790", "21,945"),
                      ("Decrease / (Increase) in inventories", "3,144", "(2,565)"),
                      ("Decrease / (Increase) in trade receivables", "818", "(263)"),
                      ("(Increase) in other assets", "(965)", "(4,832)"),
                      ("Cash flow from operations", "18,592", "7,892"),
                      ("Income taxes paid (net of refund received)", "(1,701)", "(2,443)"),
                      ("Net cash flow generated from operating activities (A)", "16,891",
                       "5,449")):
        put(sheet, 652, y, cap); num(sheet, 1020, y, a); num(sheet, 1090, y, b); y += 14
    return doc.tobytes()


@pytest.fixture(scope="module")
def jsw_doc():
    from app.core.models.document import DocumentModel
    from app.core.models.enums import DocFormat
    from app.core.pipeline import default_pipeline
    from app.core.stage import PipelineContext

    ctx = PipelineContext(raw_bytes=_jsw_shaped_pdf())
    doc = default_pipeline().run(DocumentModel(filename="jsw.pdf", fmt=DocFormat.PDF), ctx)
    doc._logs = list(ctx.logs)
    return doc


def _halves(doc, index):
    page = next(p for p in doc.pages if p.index == index)
    return (page.evidence or {}).get("spread", {}).get("halves") or []


def test_the_sideways_half_is_classified_by_its_own_title(jsw_doc):
    left, right = _halves(jsw_doc, 1)
    assert (left["kind"], left["statement"], left.get("rotation")) == \
        ("face", "changes_in_equity", 90)
    assert left["evidence"]["matched_title"] == "STANDALONE STATEMENT OF CHANGES IN EQUITY"
    assert (right["kind"], right["statement"]) == ("face", "cash_flow")
    assert "rotation" not in right


def test_the_sheet_before_is_untouched(jsw_doc):
    assert [(h["statement"], h.get("rotation")) for h in _halves(jsw_doc, 0)] == [
        ("balance_sheet", None), ("profit_and_loss", None)]


def test_no_equity_movement_is_read_as_profit_and_loss(jsw_doc):
    from app.services.face_context import face_rows

    rows = [r for b in face_rows(jsw_doc, {"profit_and_loss"}) for r in b["rows"]]
    captions = {r["caption"] for r in rows}
    assert "Other income" in captions                        # the real P&L row, on sheet 0
    assert not captions & {"Other comprehensive income", "Dividends", "Opening balance",
                           "Closing balance", "Transfer to general reserve"}
    assert not any("-190" in r["figures"].values() for r in rows)


def _equity_rows(doc) -> dict:
    """The rows read off the sideways half: caption -> {column: figure}. The statement of changes
    in equity is read as a matrix, so a figure is keyed by its component, not a period."""
    out = {}
    for item in doc.line_items:
        values = list(item.values.values())
        if values and all(v.provenance and v.provenance.page_index == 1
                          and v.provenance.bbox is not None and v.provenance.bbox.x0 < 0.5
                          for v in values):
            out[item.source_label] = {v.period_label: str(v.value) for v in values}
    return out


def test_the_sideways_half_is_read_as_rows_of_its_own_statement(jsw_doc):
    rows = _equity_rows(jsw_doc)
    assert list(rows) == ["Opening balance", "Profit for the year", "Other comprehensive income",
                          "Dividends", "Transfer to general reserve", "Closing balance"]
    assert rows["Other comprehensive income"] == {"Retained earnings": "-190",
                                                  "Hedge reserve": "415", "Total": "225"}
    assert rows["Closing balance"]["Total"] == "72860"
    # The upright running header and folio of the half are no row of it.
    assert not any("FINANCIAL" in c or "JSW" in c for c in rows)


def test_the_upright_half_beside_it_is_read_as_before(jsw_doc):
    from app.services.face_context import face_rows

    rows = {r["caption"]: r for b in face_rows(jsw_doc, {"cash_flow"}) for r in b["rows"]}
    assert rows["Depreciation and amortisation expenses"]["figures"] == {"current": "5913",
                                                                          "prior": "5435"}
