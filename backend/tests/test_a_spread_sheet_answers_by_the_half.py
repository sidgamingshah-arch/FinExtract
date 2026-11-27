"""A 2-UP SPREAD SHEET IS TWO PRINTED PAGES: A READER THAT KNOWS WHERE A FIGURE IS ASKS ITS HALF.

NEW FILE -> backend/tests/test_a_spread_sheet_answers_by_the_half.py

`classify._merge_spread_units` gives a spread sheet ONE statement (its first face half's) and one
folio, and keeps both halves under `evidence["spread"]["halves"]`. Measured on Vivo Energy
Mauritius' annual report 2025, every sheet a spread:

    sheet 34   left  STATEMENT OF PROFIT OR LOSS      printed 66
               right STATEMENT OF FINANCIAL POSITION  printed 67
    sheet 35   left  STATEMENT OF CHANGES IN EQUITY   printed 68
               right STATEMENT OF CASH FLOWS          printed 69

Read by the sheet: the classify log said statements=['changes_in_equity', 'profit_and_loss']; all
24 balance-sheet figures cited page 66 and all 31 cash-flow figures page 68; the 31 cash-flow rows,
each stamped printed_on:cash_flow, were filed under "Changes in equity" with the three cash-flow
buckets empty; and on the whole-PDF route the balance sheet and the cash flow were neither asked
against the statements alone nor covered by the lost-lines guard or FACE_LINE_OFF_FACE.

Each test here fails on the code before the change it pins.
"""
from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind, PrintedIn
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import page_spread

FOLD = 0.4975                       # Vivo's sheets: the fold as a fraction of the sheet's width


def _half(side: str, statement: str | None, folio: str | None, kind: str = "face",
          **evidence) -> dict:
    return {"side": side, "kind": kind, "statement": statement, "scope": None,
            "scope_columns": [], "printed_page": folio,
            "evidence": {"state": kind, "matched_title": statement, **evidence}}


def _sheet(index: int, left: dict, right: dict) -> PageSource:
    """A spread sheet as `_merge_spread_units` leaves it: its first face half's verdict."""
    lead = left if left["kind"] == "face" else right
    return PageSource(index=index, kind=PageKind.FACE, statement=lead["statement"],
                      printed_page=left["printed_page"] or right["printed_page"],
                      evidence={**lead["evidence"],
                                "spread": {"fold": FOLD, "halves": [left, right]}})


def _vivo_sheets() -> list[PageSource]:
    return [_sheet(34, _half("left", "profit_and_loss", "66"),
                   _half("right", "balance_sheet", "67")),
            _sheet(35, _half("left", "changes_in_equity", "68"),
                   _half("right", "cash_flow", "69"))]


def _row(label: str, key: str, page: int, x0: float, *, flags: tuple = ()) -> LineItem:
    row = LineItem(source_label=label, canonical_key=key, printed_in=PrintedIn.FACE)
    box = BBox(x0=x0, y0=0.30, x1=x0 + 0.1, y1=0.31)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal("5"), value_raw=Decimal("5"),
                                 provenance=Provenance(page_index=page, bbox=box,
                                                       label_bbox=box)))
    row.confidence.flags.extend(flags)
    return row


# ------------------------------------------------------------------ the helpers ---

def test_a_spread_sheet_prints_both_halves_statements():
    sheets = _vivo_sheets()
    assert [page_spread.statements_on(p) for p in sheets] == [
        ["profit_and_loss", "balance_sheet"], ["changes_in_equity", "cash_flow"]]
    # a whole page, and a page a person cut into parts, answer as before / by part
    assert page_spread.statements_on(PageSource(index=1, kind=PageKind.FACE,
                                                statement="balance_sheet")) == ["balance_sheet"]
    cut = PageSource(index=2, kind=PageKind.FACE, statement="cash_flow", evidence={
        "override_parts": [{"from_y": 0.0, "kind": "face", "statement": "balance_sheet"},
                           {"from_y": 0.5, "kind": "notes"},
                           {"from_y": 0.6, "kind": "face", "statement": "cash_flow"}]})
    assert page_spread.statements_on(cut) == ["balance_sheet", "cash_flow"]


def test_a_figure_takes_the_statement_and_the_folio_of_its_half():
    sheet = _vivo_sheets()[0]
    assert page_spread.statement_at(sheet, 0.20) == "profit_and_loss"
    assert page_spread.statement_at(sheet, 0.80) == "balance_sheet"
    assert page_spread.folio_at(sheet, 0.20) == "66"
    assert page_spread.folio_at(sheet, 0.80) == "67"
    assert page_spread.folio_at(sheet, None) == "66"          # no box: the sheet's own
    notes_beside = _sheet(40, _half("left", "cash_flow", "80"),
                          _half("right", None, "81", kind="notes"))
    assert page_spread.statement_at(notes_beside, 0.8) is None   # a notes half is no statement


# ------------------------------------------------------------------ the readers ---

def test_an_unstamped_row_on_the_right_half_is_on_the_right_halfs_statement():
    from app.services.buckets import page_statement_resolver, statement_resolver

    doc = DocumentModel(filename="vivo.pdf")
    doc.pages = _vivo_sheets()
    bs_row = _row("Inventories", "bs_ca__inventories", 34, 0.56)
    pl_row = _row("Revenue", "is_pl__sales_revenues", 34, 0.06)
    assert page_statement_resolver(doc)(bs_row) == "balance_sheet"
    assert statement_resolver(doc)(pl_row) == "profit_and_loss"
    # the reader's stamp still outranks the page
    stamped = _row("Inventories", "bs_ca__inventories", 34, 0.06,
                   flags=("printed_on:balance_sheet",))
    assert statement_resolver(doc)(stamped) == "balance_sheet"


def test_the_cash_flow_beside_the_equity_statement_is_bucketed_as_cash_flow():
    from app.services.buckets import segment_source

    doc = DocumentModel(filename="vivo.pdf")
    doc.pages = _vivo_sheets()
    cf = _row("Interest paid", "cf_oper_indirect__interest_paid", 35, 0.56,
              flags=("printed_on:cash_flow",))
    soce = _row("Dividends declared", "eq__dividends", 35, 0.06,
                flags=("printed_on:changes_in_equity",))
    # A movement of the equity statement whose caption the P&L's vocabulary names is still a line
    # of the statement it is printed in: the bucket asks where a row was printed, not what names it.
    movement = _row("Profit for the year", "is_retained__other_adj_to_retained_profits", 35, 0.06,
                    flags=("match_statement:profit_and_loss",))
    doc.line_items = [cf, soce, movement]
    out = segment_source(doc)
    where = {fid: seg.bucket for seg in out.segments for fid in seg.face_item_ids}
    assert where[str(soce.id)] == "changes_in_equity"
    assert where[str(movement.id)] == "changes_in_equity"
    assert where[str(cf.id)] != "changes_in_equity"


def test_a_citation_names_the_folio_of_the_half_its_figure_is_on():
    from app.api.routes.extractions import _folio_lookup, _prov_dict

    doc = DocumentModel(filename="vivo.pdf")
    doc.pages = [PageSource(index=33, printed_page="64"), *_vivo_sheets()]
    folio_of = _folio_lookup(doc)
    def cited(row) -> str | None:
        return _prov_dict(next(iter(row.values.values())).provenance, folio_of)["printed_page"]

    assert cited(_row("Inventories", "k", 34, 0.56)) == "67"
    assert cited(_row("Revenue", "k", 34, 0.06)) == "66"
    assert cited(_row("Interest paid", "k", 35, 0.56)) == "69"
    assert folio_of(34) == "66" and folio_of(33) == "64" and folio_of(99) is None


def test_the_whole_pdf_route_finds_both_statements_of_a_spread_sheet():
    from app.services import whole_pdf as W

    sheets = _vivo_sheets()
    assert [W.statement_values(p) for p in sheets] == [
        ["profit_and_loss", "balance_sheet"], ["changes_in_equity", "cash_flow"]]
    faces = {st for p in sheets for st in W.statement_values(p)} & set(W.FACE_STATEMENTS)
    assert faces == {"profit_and_loss", "balance_sheet", "cash_flow"}


def test_each_face_half_says_whose_figures_it_prints():
    from app.services.face_bases import page_entities

    sheet = _sheet(34, {**_half("left", "profit_and_loss", "66"), "scope": "consolidated"},
                   {**_half("right", "balance_sheet", "67"), "scope": "company"})
    assert page_entities(sheet) == ["consolidated", "company"]


def test_each_half_keeps_its_own_folio_and_the_sheet_takes_its_first():
    from app.stages.classify import _merge_spread_units

    whole = PageSource(index=45, width_pt=1190.0, height_pt=842.0)
    # The whole sheet's folio would be the right page's: it sits nearer the bottom edge.
    lines = [{"text": "88", "y": 815.0}, {"text": "89", "y": 830.0}]
    left = PageSource(index=45, kind=PageKind.FACE, statement="balance_sheet", printed_page="88")
    right = PageSource(index=45, kind=PageKind.FACE, statement="profit_and_loss",
                       printed_page="89")
    [page] = _merge_spread_units([whole], [(lines, 842.0)], [left, right],
                                 {45: (FOLD, 2, (0, 0))})
    halves = page.evidence["spread"]["halves"]
    assert [h["printed_page"] for h in halves] == ["88", "89"]
    assert page.printed_page == "88"


def test_a_spread_sheet_is_still_read_for_the_half_whose_section_is_on():
    """Page Scope's switched-off sections drop a page only when every part it reads is off; the
    sheet's parts named only its first half, so switching off the profit or loss dropped the
    balance sheet printed beside it."""
    from app.services.page_reading import narrow_targets

    sheets = _vivo_sheets()
    assert [p.index for p in narrow_targets(sheets, sections_off={"profit_and_loss"})] == [34, 35]
    assert [p.index for p in narrow_targets(
        sheets, sections_off={"profit_and_loss", "balance_sheet"})] == [35]


# ------------------------------------------------------------------ a sideways half's folio ---

FILINGS = pathlib.Path(__file__).resolve().parents[2] / "testdata" / "filings"


@pytest.mark.parametrize("stem, sheet, folios", [
    # the left half is set a quarter-turn; "506" is printed upright under it, "507" under the right
    ("asian_paints_2025_26_ind", 254, ["506", "507"]),
    # the standalone statement of changes in equity is the sideways left half, the cash flow the
    # upright right half (`test_a_sideways_half_of_a_spread_is_read_sideways`)
    ("jsw_steel_2024_25_ind", 219, ["436", "437"]),
])
def test_a_sideways_half_keeps_the_folio_printed_upright_under_it(stem, sheet, folios):
    """A folio is printed upright at the foot of the sheet even beside a half set sideways. Read in
    the half's turned frame it is no longer in a margin band: the half had no folio, and the sheet
    and every figure on that half took the other page's."""
    import fitz

    from app.stages import classify

    path = FILINGS / f"{stem}.pdf"
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    pdf = fitz.open(path)
    try:
        lines, height = classify._page_lines(pdf[sheet])
        src = PageSource(index=sheet)
        units, _cache, _texts, spreads = classify._spread_units(
            pdf, [src], [(lines, height)], ["\n".join(line["text"] for line in lines)])
    finally:
        pdf.close()
    assert 90 in spreads[sheet][2] or 270 in spreads[sheet][2]     # one half is read sideways
    assert [u.printed_page for u in units] == folios


def test_a_half_that_prints_no_folio_is_the_page_beside_the_one_facing_it():
    """The halves of a 2-up sheet are consecutive printed pages. Hindustan Unilever's sheet 233
    prints 464 on its left page and nothing on its right; a figure on the right half cited 464."""
    sheet = _sheet(233, _half("left", None, "464", kind="notes"),
                   _half("right", None, None, kind="notes"))
    assert page_spread.folio_at(sheet, 0.8) == "465"
    assert page_spread.folio_at(sheet, 0.2) == "464"
    first = _sheet(54, _half("left", None, None, kind="notes"),
                   _half("right", None, "107", kind="notes"))
    assert page_spread.folio_at(first, 0.2) == "106"
    # no number to count from: the sheet's own, as before
    roman = _sheet(3, _half("left", None, None, kind="notes"),
                   _half("right", None, "iv", kind="notes"))
    assert page_spread.folio_at(roman, 0.2) == "iv"
    assert page_spread.folio_of_statement(
        _sheet(35, _half("left", "changes_in_equity", "68"), _half("right", "cash_flow", None)),
        "cash_flow") == "69"
