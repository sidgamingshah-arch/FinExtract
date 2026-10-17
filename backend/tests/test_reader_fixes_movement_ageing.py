"""Readers behind the movement and ageing tables, found by the stand-in on the f1 Ind AS filing.

- a notes-table ROW naming a statement ("Charged to the Statement of Profit and Loss | 98.55 |
  76.30") made a notes page a profit-and-loss face;
- a matrix's column names kept only their first header line ('Plant and', 'Office', 'Freehold'),
  and a word PyMuPDF joined across two column headers ('machineryinstallations') named neither;
- a sub-table printed under a matrix ("(B) Capital work-in-progress") was read into its columns;
- an ageing header's bucket digits ('6 months', '1-2 years') made it a figure row;
- a balance caption wrapped before its year left the block unanchored, so every movement row of
  a PP&E schedule was hinted 'prior'.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services import notes_extract as ne
from app.services import row_reconstruct as rr
from app.services.row_reconstruct import Word
from app.stages import classify


def _w(text: str, x0: float, y: float, x1: float | None = None, h: float = 0.010) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1 if x1 is not None else x0 + 0.008 * len(text),
                                     y1=y + h))


def _right(text: str, x1: float, y: float) -> Word:
    return _w(text, x1 - 0.007 * len(text), y, x1)


# ── 1. classify ──────────────────────────────────────────────────────────────────────────────

def _line(text, y, bold=False):
    return {"text": text, "y": y, "size": 9.0, "bold": bold}


def test_a_notes_row_naming_the_statement_is_not_a_mid_page_title():
    lines = [_line("Movement in deferred tax liabilities (net)", 700, True),
             _line("Balance at the beginning of the year", 727), _line("1,207.28", 727),
             _line("Charged to the Statement of Profit and Loss", 740), _line("98.55", 740),
             _line("76.30", 740)]
    name, _c, title, _a = classify._mid_page_statement(lines, [])
    assert name is None and title is None


def test_a_mid_page_statement_title_on_its_own_line_still_resolves():
    lines = [_line("Total equity and liabilities", 400), _line("12,345.00", 400),
             _line("Statement of Profit and Loss", 460, True)]
    name, _c, _t, _a = classify._mid_page_statement(lines, [])
    assert name == "profit_and_loss"


# ── 2/3. the matrix ──────────────────────────────────────────────────────────────────────────

EDGES = [0.36, 0.44, 0.52, 0.60, 0.68, 0.77]


def _ppe_words() -> list[Word]:
    ws = [_w("Particulars", 0.07, 0.100)]
    tops = ["Freehold", "Buildings", "Plant and", "Electrical", "Office", "Total"]
    bottoms = ["land", None, "machinery", "installations", "equipment", None]
    for e, t, b in zip(EDGES, tops, bottoms):
        ws.append(_right(t, e, 0.100))
        if b:
            ws.append(_right(b, e, 0.112))
    ws.append(_w("Gross carrying amount", 0.07, 0.130))
    y = 0.145
    for cap, vals in (("Balance as at 1 April 2023", ["1,245.60", "8,412.35", "28,654.20",
                                                      "1,876.40", "694.15", "40,882.70"]),
                      ("Additions", ["-", "612.40", "3,486.75", "214.60", "96.40", "4,410.15"]),
                      ("Disposals", ["-", "-", "(412.60)", "-", "(28.40)", "(441.00)"]),
                      ("Balance as at 31 March 2024", ["1,245.60", "9,024.75", "31,728.35",
                                                       "2,091.00", "762.15", "44,851.85"])):
        ws.append(_w(cap, 0.07, y))
        ws += [_right(v, e, y) for v, e in zip(vals, EDGES)]
        y += 0.014
    return ws


def test_matrix_column_names_join_the_lines_the_caption_wraps_on():
    _m, names = rr._maybe_matrix(_ppe_words(), statement=None)
    assert names == ["Freehold land", "Buildings", "Plant and machinery",
                     "Electrical installations", "Office equipment", "Total"]


def test_a_heading_running_out_of_the_label_column_names_no_column():
    ws = _ppe_words() + [_w("(A)", 0.07, 0.086), _w("Property, plant and", 0.10, 0.086, 0.30),
                         _w("equipment", 0.305, 0.086, 0.355)]
    _m, names = rr._maybe_matrix(ws, statement=None)
    assert names[0] == "Freehold land"


def test_a_sub_table_under_the_matrix_is_read_as_its_own_table():
    ws = _ppe_words()
    y = 0.240
    ws.append(_w("(B) Capital work-in-progress", 0.07, y))
    ws += [_w("Particulars", 0.07, y + 0.014), _right("2025", 0.68, y + 0.014),
           _right("2024", 0.77, y + 0.014)]
    for k, (cap, a, b) in enumerate((("Balance at the beginning of the year", "1,013.05", "845.30"),
                                     ("Additions during the year", "4,862.70", "4,236.15"),
                                     ("Balance at the end of the year", "1,363.40", "1,013.05"))):
        yy = y + 0.028 + 0.014 * k
        ws += [_w(cap, 0.07, yy), _right(a, 0.68, yy), _right(b, 0.77, yy)]
    items, _ = rr.build_line_items(ws, page_index=7, document_id=None, source_kind="native",
                                   on_face=False)
    cwip = [li for li in items if li.source_label.startswith("Additions during")]
    assert cwip, [li.source_label for li in items]
    labels = {v.period_label for v in cwip[0].values.values()}
    assert not labels & {"Office equipment", "Total"}
    assert all(v.column_index is None for v in cwip[0].values.values())


# ── 4. the ageing header ─────────────────────────────────────────────────────────────────────

def test_an_ageing_header_line_is_not_a_figure_row():
    row = [_w("Particulars", 0.07, 0.3), _w("Not", 0.313, 0.3), _w("due", 0.343, 0.3),
           _w("Less", 0.381, 0.3), _w("than", 0.417, 0.3), _w("6", 0.454, 0.3, 0.461),
           _w("6", 0.479, 0.3, 0.486), _w("months", 0.493, 0.3), _w("1-2", 0.583, 0.3),
           _w("years", 0.61, 0.3), _w("2-3", 0.676, 0.3), _w("years", 0.703, 0.3)]
    fused = rr._fuse_bucket_numbers(row)
    texts = [w.text for w in fused]
    assert "than 6" in texts and "6 months" in texts and "1-2 years" in texts
    assert not rr._carries_amounts(fused)


def test_a_bucket_line_with_an_amount_is_left_alone():
    row = [_w("less", 0.07, 0.3), _w("than", 0.11, 0.3), _w("3", 0.15, 0.3), _w("months", 0.17, 0.3),
           _w("years", 0.25, 0.3), _w("450.00", 0.66, 0.3)]
    assert [w.text for w in rr._fuse_bucket_numbers(row)] == [w.text for w in row]


def test_an_ageing_table_names_every_bucket_including_a_sparse_one():
    # Four buckets and a total: not a matrix (two full rows), and "Not due" holds two figures —
    # too few for the figures alone to show the column, so the printed header places it.
    edges = [0.370, 0.463, 0.650, 0.743, 0.929]
    ws = [_w("Particulars", 0.07, 0.30)]
    line1 = [("Not", 0.313), ("due", 0.343), ("Less", 0.381), ("than", 0.417), ("6", 0.454),
             ("1-2", 0.598), ("years", 0.622), ("2-3", 0.691), ("years", 0.715), ("Total", 0.899)]
    # Word spacing as printed: the words of one heading sit 0.004 apart, two headings further.
    for k, (t, x) in enumerate(line1):
        nxt = line1[k + 1][1] if k + 1 < len(line1) else x + 0.03
        ws.append(_w(t, x, 0.30, min(x + 0.0085 * len(t), nxt - 0.004)))
    ws.append(_w("months", 0.409, 0.312, 0.45))
    rows = [("Undisputed - considered good", ["10,486.15", "2,641.95", "62.70", "25.20",
                                              "13,215.00"]),
            ("Undisputed - credit impaired", [None, None, "88.15", "70.30", "158.45"]),
            ("Total", ["10,486.15", "2,641.95", "150.85", "95.50", "13,374.45"])]
    for k, (cap, vals) in enumerate(rows):
        y = 0.33 + 0.014 * k
        ws.append(_w(cap, 0.07, y, 0.26))
        ws += [_right(v, x1, y) for v, x1 in zip(vals, edges) if v]
    items, _ = rr.build_line_items(ws, page_index=9, document_id=None, source_kind="native",
                                   on_face=False)
    first = next(li for li in items if li.source_label.startswith("Undisputed - considered"))
    named = {v.column_heading: str(v.value) for v in first.values.values()}
    assert named == {"Not due": "10486.15", "Less than 6 months": "2641.95", "1-2 years": "62.70",
                     "2-3 years": "25.20", "Total": "13215.00"}


def test_a_particulars_header_after_figures_opens_a_sub_table():
    assert ne._opens_a_sub_table([_w("Particulars", 0.07, 0.3), _w("Not", 0.31, 0.3),
                                  _w("due", 0.34, 0.3), _w("6", 0.45, 0.3, 0.457),
                                  _w("months", 0.47, 0.3), _w("1-2", 0.58, 0.3),
                                  _w("years", 0.61, 0.3)])
    assert not ne._opens_a_sub_table([_w("Particulars", 0.07, 0.3), _w("1,013.05", 0.6, 0.3)])
    assert not ne._opens_a_sub_table([_w("Balance", 0.07, 0.3), _w("as", 0.12, 0.3)])


# ── 5. the period hint ───────────────────────────────────────────────────────────────────────

def test_a_balance_caption_wrapped_before_its_year_anchors_its_block():
    rows = [
        [_w("Balance as at 1 April 2023", 0.07, 0.10), _w("1,245.60", 0.40, 0.10)],
        [_w("Depreciation for the year", 0.07, 0.12), _w("286.40", 0.40, 0.12)],
        [_w("Balance as at 31 March", 0.07, 0.14)],
        [_w("2024", 0.07, 0.152), _w("-", 0.33, 0.152), _w("1,928.70", 0.40, 0.152)],
        [_w("Depreciation for the year", 0.07, 0.17), _w("304.85", 0.40, 0.17)],
        [_w("Balance as at 31 March", 0.07, 0.19)],
        [_w("2025", 0.07, 0.202), _w("2,233.55", 0.40, 0.202)],
    ]
    anchors = ne._period_anchors(rows)
    assert [(a[1], a[2]) for a in anchors] == [(2023, True), (2024, True), (2025, True)]
    newest = max(a[1] for a in anchors)
    assert ne._period_hint_for(anchors, newest, 0.12) == "prior"
    assert ne._period_hint_for(anchors, newest, 0.17) == "current"


# ── the word PyMuPDF joined across two headers ───────────────────────────────────────────────

def test_a_word_drawn_as_two_touching_strings_is_split():
    import fitz

    from app.services import pdf_extract

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    font = fitz.Font("helv")
    width = font.text_length("machinery", fontsize=8)
    page.insert_text((265, 120), "machinery", fontsize=8)
    page.insert_text((265 + width - 4, 120), "installations", fontsize=8)
    words = pdf_extract._native_words(page, page.rect.width, page.rect.height)
    texts = [w.text for w in words]
    assert "machinery" in texts and "installations" in texts, texts
