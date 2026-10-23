"""A 2-UP SPREAD THAT PRINTS TWO STATEMENTS IS READ AS TWO STATEMENTS.

NEW FILE -> backend/tests/test_spread_statements_read_per_half.py

An Indian integrated annual report is published as 2-up spreads. Measured on the two shipped
filings of that shape:

* LAURUS LABS 2025-26 — every page carries a section tab strip ("Corporate Overview | Strategic
  Review | Conducting Business Responsibly | Statutory Reports | Financial Statements") across its
  top. The classifier read "Corporate Overview" / "Statutory Reports" in it as the page's own
  running header, so EVERY page was narrative: no page was a statement and none of the 153 model
  requests carried a statement row.
* ASIAN PAINTS 2025-26 — PDF page 184 prints the standalone Balance Sheet on the left and the
  Statement of Profit and Loss on the right. It was one profit-and-loss page, so the balance-sheet
  requests had no standalone block and the balance-sheet rows went out in the P&L one. Each half's
  columns were keyed under the other half's headings (`col4`/`col5`), the uncaptioned Current
  Assets total was dropped, the signing date went out as a row with the figure 2026, and the unit
  was read off "millions of consumers" on the cover — 'INR millions' over figures in crores.

Each test here fails on the code before the change it pins.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.enums import LineRole
from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word


# ------------------------------------------------------------------------------- classify ---

def _line(text, y, x0=40.0, x1=None):
    return {"text": text, "y": float(y), "size": 9.0, "bold": False,
            "x0": float(x0), "x1": float(x1 if x1 is not None else x0 + 6 * len(text))}


def test_a_section_tab_strip_does_not_make_a_statement_page_narrative():
    from app.stages.classify import _features

    lines = [_line("Corporate Overview", 45, 651), _line("Strategic Review", 45, 748),
             _line("Conducting Business Responsibly", 45, 832),
             _line("Statutory Reports", 45, 981), _line("Financial Statements", 45, 1066),
             _line("Standalone Statement of Profit and Loss", 86, 649),
             _line("for the year ended March 31, 2026", 110, 649),
             _line("Revenue from operations 17 6,089.07 5,216.98", 180, 649)]
    f = _features(104, lines, 842.0, "\n".join(l["text"] for l in lines), dated_titles=True)
    assert not f.narrative
    assert f.statement == "profit_and_loss"


def test_a_running_header_that_names_its_section_still_makes_the_page_narrative():
    from app.stages.classify import _features

    lines = [_line("Statutory Reports", 30), _line("Board's Report", 60),
             _line("Your Directors present the annual report.", 90)]
    f = _features(10, lines, 842.0, "\n".join(l["text"] for l in lines), dated_titles=True)
    assert f.narrative


@pytest.mark.parametrize("text", [
    "in the Consolidated Statement of Profit and Loss",
    "Consolidated Statement of Profit and Loss and",
    "Ind AS 7 – Statement of Cash Flows and Ind AS",
])
def test_a_line_of_policy_prose_is_not_a_mid_page_statement_title(text):
    from app.stages.classify import _mid_page_statement

    lines = [_line("Notes to the Consolidated Financial Statements", 40),
             _line("Property, plant and equipment are stated at cost.", 80), _line(text, 120)]
    name, _combined, _title, _ambig = _mid_page_statement(lines, lines[:1], dated_titles=True)
    assert name is None


def test_a_statement_name_under_a_note_heading_is_a_caption_inside_the_note():
    from app.stages.classify import _features

    # Asian Paints: a deferred-tax table headed "Balance Sheet" under "Note 18 : Income Taxes"
    lines = [_line("Financial Statements", 30), _line("Note 18 : Income Taxes (Contd.)", 60),
             _line("C. The Major Components of Deferred Tax", 80), _line("Balance Sheet", 100),
             _line("Deferred tax liabilities 254.28 237.23", 120)]
    f = _features(204, lines, 842.0, "\n".join(l["text"] for l in lines), dated_titles=True)
    assert f.statement is None


def test_a_sentence_in_the_title_zone_is_not_a_title():
    from app.stages.classify import _features

    lines = [_line("Financial Statements", 30), _line("Statement of Profit and Loss. The amortised", 60),
             _line("cost is reduced by impairment losses.", 75)]
    f = _features(188, lines, 842.0, "\n".join(l["text"] for l in lines), dated_titles=True)
    assert f.statement is None


def test_a_real_mid_page_title_is_still_found():
    from app.stages.classify import _mid_page_statement

    lines = [_line("Balance Sheet", 40), _line("Total equity and liabilities 34,534.49", 300),
             _line("STATEMENT OF PROFIT AND LOSS", 400)]
    name, *_ = _mid_page_statement(lines, lines[:1])
    assert name == "profit_and_loss"


# ------------------------------------------------------------------- the period headings ---

def _w(text, x0, y0, width=None, h=0.012):
    width = 0.0055 * max(len(text), 1) if width is None else width
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + width, y1=y0 + h))


def _heading_rows(texts_at):
    from app.services.row_reconstruct import _period_bands

    rows = [[_w("Particulars", 0.04, 0.10)] + [_w(t, x, 0.10) for t, x in texts_at],
            [_w("Inventories", 0.04, 0.13), _w("4,824.74", 0.33, 0.13), _w("5,848.23", 0.38, 0.13)],
            [_w("Loans", 0.04, 0.15), _w("1.00", 0.33, 0.15), _w("2.00", 0.38, 0.15)]]
    return _period_bands(rows)


@pytest.mark.parametrize("texts_at, want", [
    # Laurus Labs: two dates 0.023 of the page apart, read as one heading
    ([("March", 0.300), ("31,", 0.322), ("2026", 0.336), ("March", 0.360), ("31,", 0.382),
      ("2025", 0.396)], ["March 31, 2026", "March 31, 2025"]),
    # Asian Paints: numeric dates and financial years side by side
    ([("31.03.2026", 0.33), ("31.03.2025", 0.38)], ["31.03.2026", "31.03.2025"]),
    ([("2025-26", 0.33), ("2024-25", 0.37)], ["2025-26", "2024-25"]),
])
def test_two_adjacent_dates_are_two_column_headings(texts_at, want):
    assert [label for label, _x in _heading_rows(texts_at)] == want


def test_the_column_with_more_figures_takes_the_period_a_stray_shares():
    from app.core.models.enums import Basis
    from app.services.row_reconstruct import _column_periods

    # column 0 holds one stray (a sub-note "11.6") nearest the current heading; column 1 is the
    # real current-year column; column 2 the prior year
    bands = [("31.03.2026", 0.36), ("31.03.2025", 0.44)]
    out = _column_periods({Basis.CONSOLIDATED: [0, 1, 2]}, [0.33, 0.37, 0.44], bands,
                          support={0: 1, 1: 20, 2: 20})
    assert out[(Basis.CONSOLIDATED, 1)] == "current"
    assert out[(Basis.CONSOLIDATED, 2)] == "prior"
    assert out[(Basis.CONSOLIDATED, 0)] != "current"


# ----------------------------------------------------------- an uncaptioned face total ---

def _bs_half_words():
    rows = [("Current Assets", None, None), ("Inventories", "4,824.74", "5,848.23"),
            ("Trade Receivables", "3,939.36", "3,611.51"),
            ("Cash and Cash Equivalents", "1,005.72", "627.96"),
            ("Other Current Assets", "796.51", "773.71"),
            (None, "10,566.33", "10,861.41"),
            ("TOTAL ASSETS", "34,534.49", "30,371.37")]
    words, y = [], 0.20
    for caption, cur, pri in rows:
        x = 0.04
        for tok in (caption or "").split():
            words.append(_w(tok, x, y))
            x += 0.0055 * len(tok) + 0.003
        if cur:
            words.append(_w(cur, 0.37 - 0.0055 * len(cur), y))
            words.append(_w(pri, 0.44 - 0.0055 * len(pri), y))
        y += 0.02
    return words


def test_a_bare_line_that_totals_its_section_on_a_face_is_kept_as_that_section_total():
    from app.services.row_reconstruct import build_line_items

    items, _ = build_line_items(_bs_half_words(), page_index=0, document_id="d",
                                source_kind="native", statement="balance_sheet", page_fold=None)
    totals = [li for li in items if li.role is LineRole.SUBTOTAL]
    assert [li.source_label for li in totals] == ["Current Assets"]
    assert {str(v.value) for v in totals[0].values.values()} == {"10566.33", "10861.41"}


def test_a_bare_line_that_does_not_add_up_is_still_dropped_on_a_face():
    from app.services.row_reconstruct import build_line_items

    words = [w for w in _bs_half_words() if w.text not in ("10,566.33",)]
    words.append(_w("99,999.99", 0.37 - 0.0055 * 9, 0.30))
    items, _ = build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                statement="balance_sheet", page_fold=None)
    assert not [li for li in items if li.role is LineRole.SUBTOTAL]
    assert not any(str(v.value) == "99999.99" for li in items for v in li.values.values())


# --------------------------------------------------------------- what the request carries ---

def test_the_signing_date_is_not_a_statement_row():
    from types import SimpleNamespace as N

    from app.services.face_context import is_signature_date_row

    def row(caption, *figs):
        return N(source_label=caption, values={i: N(value=Decimal(f)) for i, f in enumerate(figs)})

    assert is_signature_date_row(row("Mumbai Mumbai 29th May", "2026", "2026"))
    assert not is_signature_date_row(row("Revenue from operations", "2026"))
    assert not is_signature_date_row(row("Balance as at 31st May", "1234.50"))


def test_a_count_of_millions_on_the_cover_is_not_the_unit():
    from app.stages.normalize import _scan_scale

    assert _scan_scale("connections with millions of consumers.") is None
    assert _scan_scale("connections with millions of consumers. (₹ in crores)") == "crore"
    assert _scan_scale("in thousands of us dollars") == "thousand"
    assert _scan_scale("in millions of rupees") == "million"


# ------------------------------------------------------------------- the recursion guard ---

def test_a_sub_table_cut_at_the_top_of_the_words_does_not_recurse(monkeypatch):
    from app.services import row_reconstruct as R

    words, y = [], 0.10
    names = ["Capital", "Securities", "Retained", "Reserve", "Total"]
    for i, name in enumerate(names):
        words.append(_w(name, 0.30 + 0.12 * i, y))
    for r in range(8):
        y += 0.02
        words.append(_w(f"Movement{r}", 0.04, y))
        for i in range(len(names)):
            words.append(_w(f"{r + 1},{i}00.00", 0.30 + 0.12 * i, y))
    monkeypatch.setattr(R, "_sub_table_cut", lambda m, fmt=None: 0)
    items, _ = R.build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                  statement="changes_in_equity", page_fold=None)
    assert items is not None


# -------------------------------------------------------------- the spread, end to end ---

fitz = pytest.importorskip("fitz")


def _spread_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=1191, height=842)
    put = lambda x, y, t: page.insert_text((x, y), t, fontsize=8)  # noqa: E731
    for x, tab in ((660, "Corporate Overview"), (760, "Strategic Review"),
                   (860, "Statutory Reports"), (960, "Financial Statements")):
        put(x, 40, tab)
    # LEFT — the balance sheet
    put(40, 70, "Balance Sheet"); put(40, 84, "as at 31st March 2026"); put(40, 98, "(₹ in Crores)")
    put(40, 118, "Particulars"); put(330, 118, "Notes"); put(420, 112, "As at"); put(490, 112, "As at")
    put(420, 124, "31.03.2026"); put(490, 124, "31.03.2025")
    bs = [("Inventories", "4,824.74", "5,848.23"), ("Trade Receivables", "3,939.36", "3,611.51"),
          ("Cash and Cash Equivalents", "1,005.72", "627.96"), ("Loans", "53.25", "40.67"),
          ("Other Financial Assets", "1,822.67", "1,158.11"),
          ("Other Current Assets", "796.51", "773.71")]
    put(40, 140, "Current Assets")
    y = 154
    for cap, a, b in bs:
        put(52, y, cap); put(420, y, a); put(490, y, b); y += 14
    put(420, y, "12,442.25"); put(490, y, "12,060.19"); y += 14
    put(40, y, "TOTAL ASSETS"); put(420, y, "12,442.25"); put(490, y, "12,060.19"); y += 14
    put(40, y + 20, "Mumbai"); put(150, y + 20, "Mumbai"); put(260, y + 20, "29th May")
    put(420, y + 20, "2026")
    # RIGHT — the statement of profit and loss
    put(640, 70, "Statement of Profit and Loss"); put(640, 84, "for the year ended 31st March 2026")
    put(640, 98, "(₹ in Crores)")
    put(640, 118, "Particulars"); put(930, 118, "Notes"); put(1020, 112, "Year"); put(1090, 112, "Year")
    put(1020, 124, "2025-26"); put(1090, 124, "2024-25")
    pl = [("Revenue from Sale of Products", "30,621.70", "29,270.69"),
          ("Other Operating Revenue", "301.51", "297.35"), ("Other Income", "620.44", "731.92"),
          ("Cost of Materials Consumed", "15,421.92", "14,690.62"),
          ("Employee Benefits Expense", "1,890.88", "1,753.69"),
          ("Other Expenses", "6,012.26", "5,610.04"), ("Profit Before Tax", "5,701.33", "5,821.24")]
    y = 140
    for cap, a, b in pl:
        put(640, y, cap); put(1020, y, a); put(1090, y, b); y += 14
    return doc.tobytes()


@pytest.fixture(scope="module")
def spread_doc():
    from app.core.models.document import DocumentModel
    from app.core.models.enums import DocFormat
    from app.core.pipeline import default_pipeline
    from app.core.stage import PipelineContext

    ctx = PipelineContext(raw_bytes=_spread_pdf())
    return default_pipeline().run(DocumentModel(filename="spread.pdf", fmt=DocFormat.PDF), ctx)


def test_each_half_of_the_spread_is_classified_as_its_own_statement(spread_doc):
    page = spread_doc.pages[0]
    halves = (page.evidence or {}).get("spread", {}).get("halves") or []
    assert [(h["kind"], h["statement"]) for h in halves] == [
        ("face", "balance_sheet"), ("face", "profit_and_loss")]


def test_each_half_s_rows_go_to_its_own_statement_block(spread_doc):
    from app.services.face_context import face_rows

    bs = face_rows(spread_doc, {"balance_sheet"})
    pl = face_rows(spread_doc, {"profit_and_loss"})
    bs_caps = {r["caption"] for b in bs for r in b["rows"]}
    pl_caps = {r["caption"] for b in pl for r in b["rows"]}
    assert "Inventories" in bs_caps and "Revenue from Sale of Products" not in bs_caps
    assert "Revenue from Sale of Products" in pl_caps and "Inventories" not in pl_caps


def test_each_half_s_columns_are_current_and_prior_under_its_own_headings(spread_doc):
    from app.services.face_context import face_rows

    for statement, caption, cur, pri in (("balance_sheet", "Inventories", "4824.74", "5848.23"),
                                         ("profit_and_loss", "Other Income", "620.44", "731.92")):
        rows = [r for b in face_rows(spread_doc, {statement}) for r in b["rows"]
                if r["caption"] == caption]
        assert rows and rows[0]["figures"] == {"current": cur, "prior": pri}


def test_the_spread_s_bare_total_and_signing_date(spread_doc):
    from app.services.face_context import face_rows

    rows = [r for b in face_rows(spread_doc, {"balance_sheet"}) for r in b["rows"]]
    # A bare total is shown by the heading it totals, saying no caption is printed beside it.
    assert any(r["caption"] == "Current Assets (total, printed without a caption)"
               and r["figures"] == {"current": "12442.25", "prior": "12060.19"} for r in rows)
    assert not any("Mumbai" in r["caption"] for r in rows)
