"""AN INDIAN INTEGRATED REPORT IS READ WHOLE: both pages of a spread, in their own frame, in its unit.

Diagnosed from the run logs of five Indian filings (Chemplast Sanmar, Hindustan Unilever, JSW Steel,
IndiGo, Mukand) and measured on their published reports, fetched from the companies' sites, with
the Ind AS pair, answer-free. Each test here fails on the code before the change it pins.

* A SPREAD'S RIGHT PAGE CONTINUES ITS LEFT ONE. HUL prints assets on the left page of each balance
  sheet and equity and liabilities on the right, re-printing the column band and no title; the
  right page was notes, and every equity and liability line was missing.
* A HALF IS READ AS THE PAGE IT IS. Every distance the reader measures is a fraction of the page;
  a half of a spread is half as wide, and JSW's two years fell into one value column.
* THE COVER DOES NOT DECLARE THE UNIT. "unlocking a billion aspirations" set HUL's scale.
* A SIGNATORY'S NUMBER IS NOT A FIGURE, also where the block's names are read as its caption.
* A LINE SCOPED TO SEVERAL SECTIONS IS SHOWN ITS STATEMENT. The Ind AS P&L subtotals were asked
  where they are printed in requests that carried no row of the statement.
* A SUB-HEADING PRINTED IN BOTH PORTIONS does not take a current line to the non-current row.
* A CAPTION WRAPPED AROUND ITS FIGURES keeps them when its first line carries the note number.
* "TAX EXPENSES" IS THE TAX BANNER, as "Tax expense" is.
"""
from __future__ import annotations

import itertools
import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import face_context, line_item_requests, line_item_routes
from app.services import row_reconstruct as rr
from app.services.mapping import SourceRef, section_of_banner_only
from app.services.note_sourced import resolve_sources
from app.services.pdf_extract import in_half_frame
from app.stages.classify import _continue_on_the_facing_half
from app.stages.normalize import _scan_scale

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


def _w(text, x0, y0, x1, y1, page_bbox=None):
    return rr.Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1), page_bbox=page_bbox)


# ── a spread half in its own frame ────────────────────────────────────────────────────────────

def _two_year_rows(x_current, x_prior, width, rows=8):
    """Rows of two figures each, the two columns closer together on the SHEET than the reader's
    column tolerance (`_COL_TOL`, a fraction of the page) — the shape of a spread half. JSW Steel's
    standalone balance sheet sets its two years 0.057 of the sheet apart, and the spread of its
    figures' widths chained the two into one band."""
    return [[(x_current + width / 2, "73,322"), (x_prior + width / 2, "74,457")]
            for _ in range(rows)]


def test_a_halfs_two_columns_are_two_columns_in_its_own_frame():
    """In sheet coordinates the two columns chain into one cluster (the defect); in the half's
    frame, two. Since `_one_figure_per_row` the sheet's cluster is split as well — every row prints
    two figures in it — so the defect is pinned on the chaining, and the half's frame still reads
    the two years apart without that help."""
    sheet = _two_year_rows(0.377, 0.405, 0.024)
    flat = sorted(x for row in sheet for x in row)
    assert max(b[0] - a[0] for a, b in itertools.pairwise(flat)) <= rr._COL_TOL, \
        "the defect: on the sheet the two years chain into one cluster"
    assert len(rr._value_column_bands(sheet)) == 2      # …split, one figure per row
    words = [_w(t, x - 0.012, 0.2, x + 0.012, 0.21) for row in sheet for x, t in row]
    half = in_half_frame(words, 0.0, 0.5)
    framed = [[((w.bbox.x0 + w.bbox.x1) / 2, w.text) for w in half[i:i + 2]]
              for i in range(0, len(half), 2)]
    assert len(rr._value_column_bands(framed)) == 2


def test_a_halfs_words_keep_where_they_are_drawn():
    """Provenance shows the word on the sheet (`Word.source_bbox`), not in the half's frame."""
    w = _w("4,161", 0.80, 0.30, 0.84, 0.31)
    (framed,) = in_half_frame([w], 0.5, 1.0)
    assert framed.bbox.x0 == pytest.approx(0.60) and framed.bbox.x1 == pytest.approx(0.68)
    assert framed.source_bbox == w.bbox


# ── the facing half ───────────────────────────────────────────────────────────────────────────

def _line(text, y):
    return {"text": text, "y": float(y), "size": 9.0, "bold": False, "x0": 40.0, "x1": 300.0}

_BAND = [_line("(All amounts in ₹ crores, unless otherwise stated)", 20), _line("Particulars", 60),
         _line("Note", 60), _line("As at", 66), _line("31st March, 2025", 74), _line("As at", 66),
         _line("31st March, 2024", 74)]


def _halves(right_lines, right_feat=None):
    left = PageSource(index=143, kind=PageKind.FACE, statement="balance_sheet", scope="company")
    right = PageSource(index=143, kind=PageKind.NOTES)
    lines_left = [_line("Standalone Balance Sheet", 40), _line("as at 31st March, 2025", 50),
                  *_BAND, _line("ASSETS", 90), _line("Property, plant and equipment 3 7,710 7,178", 100)]
    feats = [SimpleNamespace(notes_banner=False, strong_title=True, matched_title="Balance Sheet"),
             right_feat or SimpleNamespace(notes_banner=False, strong_title=False, matched_title=None)]
    return [left, right], feats, [(lines_left, 842.0), (right_lines, 842.0)]


def test_the_right_page_of_a_spread_continues_the_left_ones_statement():
    """HUL 2024-25 page 144: EQUITY AND LIABILITIES under the left page's column band, no title."""
    pages, feats, cache = _halves([*_BAND, _line("EQUITY AND LIABILITIES", 90),
                                   _line("Equity share capital 17 235 235", 100)])
    assert _continue_on_the_facing_half(pages, feats, cache, {143: (0.5, 2)}) == 1
    right = pages[1]
    assert (right.kind, right.statement, right.scope) == (PageKind.FACE, "balance_sheet", "company")


@pytest.mark.parametrize("right_lines, feat", [
    # the first note, beside the cash flow's last page
    ([_line("1. Corporate information", 30), *_BAND], None),
    # a page of its own column band
    ([_line("Particulars", 60), _line("Year ended 31 March 2025", 74), _line("Year ended 2024", 74)],
     None),
    # a statement of its own, as Laurus and Asian Paints print one on each page
    ([*_BAND], SimpleNamespace(notes_banner=False, strong_title=True, matched_title="Profit")),
    # the notes running header
    ([*_BAND], SimpleNamespace(notes_banner=True, strong_title=False, matched_title=None)),
])
def test_a_right_page_with_anything_of_its_own_is_left_alone(right_lines, feat):
    pages, feats, cache = _halves(right_lines, feat)
    assert _continue_on_the_facing_half(pages, feats, cache, {143: (0.5, 2)}) == 0
    assert pages[1].kind is PageKind.NOTES


# ── the unit ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cover", [
    "integrated annual report 2024-25 aspire: unlocking a billion aspirations",
    "his life was an inspirational journey leading millions to follow the enlightened path",
    "market capitalisation as on march 31, 2025 : ` 6,878.56 crores",
])
def test_a_cover_line_does_not_declare_the_unit(cover):
    assert _scan_scale(cover, front_matter=True) is None


@pytest.mark.parametrize("declaration, scale", [
    ("(₹ in crores)", "crore"), ("(` in crores)", "crore"),
    ("(all amounts are in indian rupees in crores unless otherwise stated)", "crore"),
    ("amounts in ₹ million", "million"), ("(in millions, except per share data)", "million"),
    ("hk$ million", "million"), ("rs. in lakhs", "lakh"),
])
def test_a_declaration_on_the_cover_still_declares(declaration, scale):
    assert _scan_scale(declaration, front_matter=True) == scale
    assert _scan_scale(declaration) == scale


def test_a_statement_page_is_read_as_before():
    """Only the front matter is held to the declaration's form."""
    assert _scan_scale("unlocking a billion aspirations") == "billion"


# ── the signature block ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label, vals", [
    ("Partner Chairman Managing Director Chairman - Audit Committee Membership No.", ["203491"]),
    ("Place: Chennai Membership No. ACS", ["06248"]),
    ("per SURESH YADAV SWAYAM SAURABH SAJJAN JINDAL Partner Chief Financial Officer Chairman & "
     "Managing Director Membership No.:", ["119878"]),
])
def test_a_signatorys_number_is_not_a_figure(label, vals):
    """Chemplast Sanmar 2024-25: 203491 and 06248 reached Other current liabilities."""
    assert rr._is_noise_row(label, vals)


@pytest.mark.parametrize("label, vals", [
    ("Managing director's remuneration", ["1,234"]),
    ("Other current liabilities", ["40.14", "31.39"]),
    ("Remuneration to the partner", ["12.50"]),
])
def test_an_amount_beside_such_a_word_is_a_figure(label, vals):
    assert not rr._is_noise_row(label, vals)


# ── the request planner ───────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def indas():
    from app.schemas.line_items import load_line_item_set
    raw = json.loads((TEMPLATES / "output_csv_indas_line_items.json").read_text(encoding="utf-8"))
    return load_line_item_set(raw, resolve=True)


def test_a_line_claimable_under_several_sections_is_shown_its_statement(indas):
    """The Ind AS P&L subtotals are each claimable under every P&L section. Each was asked in a
    request of its own whose only 'section' was the empty one — no statement block."""
    from app.config import get_settings
    settings = get_settings()
    mode = settings.extraction.llm_request_grouping
    settings.extraction.llm_request_grouping = "identical"
    try:
        plans = line_item_requests.plan_requests(indas, [], settings)
    finally:
        settings.extraction.llm_request_grouping = mode
    by_key = {i.key: i for i in indas.items}
    starved = [k for p in plans for k in p.keys
               if line_item_routes.may_read_face(by_key[k])
               and not line_item_requests.face_statements(p, by_key)]
    assert starved == []
    plan = next(p for p in plans if "is_pl__profit_for_the_year" in p.keys)
    assert line_item_requests.face_statements(plan, by_key) == {"profit_and_loss"}
    assert {"is_pl__finance_costs", "is_pl__profit_loss_before_tax"} <= set(plan.keys), \
        "lines claimable under exactly the same sections share one request"


# ── the resolver ──────────────────────────────────────────────────────────────────────────────

def _face_row(caption, value, hint):
    li = LineItem(source_label=caption)
    li.values["v"] = ExtractedValue(basis=Basis.STANDALONE, period_label="current",
                                    value=Decimal(value), value_raw=Decimal(value),
                                    provenance=Provenance(page_index=5))
    li.section_hint = hint
    li.group_hint = "Financial liabilities"
    return li


def test_a_sub_heading_printed_in_both_portions_keeps_the_lines_own_section():
    """Chemplast Sanmar 2024-25 prints 'Financial liabilities / (i) Borrowings' under non-current
    AND current liabilities. A citation naming the sub-heading took the first row in document
    order, the non-current one, and was then refused as printed outside the line's section."""
    rows = [_face_row("(i) Borrowings", "581.22", "Non-current liabilities"),
            _face_row("(i) Borrowings", "408.20", "Current liabilities")]
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=5, kind=PageKind.FACE, statement="balance_sheet")]
    doc.line_items = rows
    cite = [SourceRef(statement="balance_sheet", caption="(i) Borrowings",
                      group="Financial liabilities")]
    res, unresolved = resolve_sources(cite, [], face_context.face_index(doc),
                                      sections={"current_liabilities"})
    assert not unresolved and res[0]["figures"] == {"current": "408.20"}
    res, _ = resolve_sources(cite, [], face_context.face_index(doc),
                             sections={"non_current_liabilities"})
    assert res[0]["figures"] == {"current": "581.22"}


# ── a caption wrapped around its figures ──────────────────────────────────────────────────────

def test_a_wrapped_caption_with_its_note_on_the_first_line_keeps_its_figures():
    """HUL 2024-25: "Total outstanding dues of creditors other than micro enterprises  22" /
    "10,767  9,926" / "and small enterprises" — the row was lost whole."""
    head = [_w("Total", 0.150, 0.4433, 0.18, 0.4559), _w("outstanding", 0.182, 0.4433, 0.25, 0.4559),
            _w("dues", 0.252, 0.4433, 0.28, 0.4559), _w("of", 0.282, 0.4433, 0.29, 0.4559),
            _w("creditors", 0.292, 0.4433, 0.34, 0.4559), _w("22", 0.535, 0.4433, 0.552, 0.4559)]
    figures = [_w("10,767", 0.744, 0.4490, 0.788, 0.4616), _w("9,926", 0.871, 0.4490, 0.907, 0.4616)]
    tail = [_w("and", 0.150, 0.4547, 0.17, 0.4673), _w("small", 0.172, 0.4547, 0.20, 0.4673),
            _w("enterprises", 0.202, 0.4547, 0.26, 0.4673)]
    nxt = [_w("Other", 0.130, 0.4710, 0.16, 0.4836), _w("financial", 0.162, 0.4710, 0.21, 0.4836),
           _w("liabilities", 0.212, 0.4710, 0.26, 0.4836), _w("20", 0.535, 0.4710, 0.552, 0.4836),
           _w("825", 0.78, 0.4710, 0.80, 0.4836), _w("797", 0.89, 0.4710, 0.907, 0.4836)]
    out = rr._merge_wrapped_labels([head, figures, tail, nxt])
    texts = [" ".join(w.text for w in row) for row in out]
    assert texts == ["Total outstanding dues of creditors 22 and small enterprises 10,767 9,926",
                     "Other financial liabilities 20 825 797"]


def test_a_figure_left_of_the_next_rows_figures_is_still_a_figure():
    """A caption line whose number stands in a VALUE column is a valued row, not a wrap head."""
    head = [_w("Revenue", 0.15, 0.40, 0.20, 0.412), _w("12", 0.80, 0.40, 0.82, 0.412)]
    figures = [_w("10,767", 0.744, 0.405, 0.788, 0.417)]
    assert not rr._caption_above_figures(head, figures)


# ── the tax banner ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("banner, section", [
    ("Tax expenses", "tax_expense"), ("Tax expense", "tax_expense"), ("EXPENSES", "expenses"),
    ("Total tax expenses", None), ("Other expenses", None),
])
def test_a_plural_banner_is_the_banner(banner, section):
    """HUL 2024-25 heads its tax lines "Tax expenses"; current and deferred tax stayed under the
    exceptional items above and reached no line."""
    assert section_of_banner_only(banner) == section
