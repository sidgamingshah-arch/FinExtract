"""THE OPEN ITEMS OF THE OVERALL SWEEP — each read on its filing and fixed where it is decided.

* CHEMPLAST SANMAR 2024-25 prints its statements of changes in equity sideways. The classifier
  measured "printed above the title" in the sheet's y, which runs across a sideways page, saw the
  share-capital table's figures beside the title and recorded the cash flow as still running
  above it; the page was split and "Balance at March 31" (158,109,574 shares, 79.06) was swept
  into other financing (`classify._printed_before`).
* JSW STEEL 2024-25 prints each year of its cash flow in two columns under one heading — the
  adjustments inner, the subtotals outer — and the inner was kept as `current_col0`, a period no
  check reads: every operating check saw profit before tax and the tax paid alone
  (`row_reconstruct._column_periods`, `_columns_printed_together`). Its NCI row "(NCI)", its
  "opening / closing balances" of cash and the footnoted rows of its reconciliation of financing
  liabilities ("Borrowings *") reached no line or the wrong one (the Ind AS set).
* MADRIGAL's 10-K prints "Total liabilities 656,900" over "Stockholders' equity:", and the two
  read as the known caption "Total liabilities [and] stockholders' equity"
  (`row_reconstruct._completes_the_caption_above`); its "Proceeds from related parties - warrants,
  exercise of common stock options" reached no line (the US GAAP set).
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.config import get_settings
from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.schemas.line_items import load_line_item_set
from app.services import row_reconstruct as R
from app.services.mapping import OntologyMatcher
from app.services.row_reconstruct import Word
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


def _matcher(name: str) -> OntologyMatcher:
    raw = json.loads((TEMPLATES / name).read_text(encoding="utf-8"))
    return OntologyMatcher(build_working_view(load_line_item_set(raw, resolve=True)), locale="en",
                           settings=get_settings())


# ── Chemplast: above the title, as a sideways page reads ───────────────────────────────────────

def _line(x0: float, x1: float, y: float, rot: int) -> dict:
    return {"text": "x", "x0": x0, "x1": x1, "y": y, "y1": y + 10, "rot": rot}


def test_on_a_page_printed_sideways_above_is_along_the_sheets_width():
    from app.stages.classify import _printed_before

    title = _line(40, 52, 300, 90)                     # the title, first line as the page reads
    beside = _line(60, 72, 120, 90)                    # a figure line: smaller y, but after it
    assert not _printed_before(beside, title)
    assert _printed_before(_line(20, 32, 500, 90), title)
    # Text running down the sheet (270) reads from the right edge.
    title270 = _line(540, 552, 300, 270)
    assert not _printed_before(_line(500, 512, 120, 270), title270)
    assert _printed_before(_line(560, 572, 500, 270), title270)
    # The upright running header beside a sideways body says nothing either way.
    assert not _printed_before(_line(10, 200, 20, 0), title)


def test_an_upright_page_reads_above_as_before():
    from app.stages.classify import _printed_before

    title = _line(40, 300, 300, 0)
    assert _printed_before(_line(40, 300, 120, 0), title)
    assert not _printed_before(_line(40, 300, 500, 0), title)


# ── JSW: two columns of one period ─────────────────────────────────────────────────────────────

_BANDS = [0.809, 0.850, 0.895, 0.934]
_HEADINGS = [("31 March 2025", 0.830), ("31 March 2024", 0.915)]


def _periods(together, support=None, restated_cols=None):
    return R._column_periods({Basis.STANDALONE: [0, 1, 2, 3]}, _BANDS, _HEADINGS,
                             support=support or {0: 20, 1: 6, 2: 20, 3: 6},
                             together=together, restated_cols=restated_cols)


def test_an_inner_and_an_outer_column_under_one_heading_are_both_its_period():
    labels = _periods(set())
    assert {c: labels[(Basis.STANDALONE, c)] for c in range(4)} == {
        0: "current", 1: "current", 2: "prior", 3: "prior"}


def test_columns_a_row_prints_in_both_are_two_measures_of_the_period():
    labels = _periods({frozenset((0, 1)), frozenset((2, 3))})
    assert labels[(Basis.STANDALONE, 0)] != labels[(Basis.STANDALONE, 1)]
    assert labels[(Basis.STANDALONE, 2)] != labels[(Basis.STANDALONE, 3)]


def test_a_stray_beside_the_heading_and_a_note_keep_their_own_label():
    stray = _periods(set(), support={0: 20, 1: 2, 2: 20, 3: 6})
    assert stray[(Basis.STANDALONE, 1)] not in ("current", "prior")
    off_face = _periods(None)                           # a note table: `together` is not asked
    assert off_face[(Basis.STANDALONE, 0)] != off_face[(Basis.STANDALONE, 1)]


def test_a_headings_date_fragments_are_no_row_printed_in_two_columns():
    rows = [[(0.825, "31"), (0.845, "2025"), (0.905, "31"), (0.925, "2024")],
            [(0.809, "5,913"), (0.895, "5,435")],
            [(0.850, "6,543"), (0.934, "12,102")]]
    assert R._columns_printed_together(rows, _BANDS) == {frozenset((0, 2)), frozenset((1, 3))}


# ── Madrigal: a heading is no caption's tail ───────────────────────────────────────────────────

def _words(text: str, y: float) -> list[Word]:
    out, x = [], 0.06
    for part in text.split():
        out.append(Word(text=part, bbox=BBox(x0=x, y0=y, x1=x + 0.006 * len(part), y1=y + 0.01)))
        x += 0.006 * len(part) + 0.004
    return out


def test_a_colon_ended_heading_does_not_complete_the_caption_above():
    known = frozenset({"total liabilities and stockholders equity", "total liabilities stockholders equity"})
    head = _words("Total liabilities", 0.445)
    assert not R._completes_the_caption_above(head, _words("Stockholders' equity:", 0.459), (),
                                              known)


# ── the configuration ──────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def indas():
    return _matcher("output_csv_indas_line_items.json")


@pytest.fixture(scope="module")
def usgaap():
    return _matcher("output_csv_usgaap_line_items.json")


@pytest.mark.parametrize("caption,statement,section,key", [
    ("Non-controlling interests (NCI)", "balance_sheet", "Equity", "bs_equity__non_controlling_interests"),
    ("Cash and cash equivalents - closing balances (note 15)", "cash_flow",
     "Cash flow from financing activities", "cf_financing__end_of_period_cash_and_cash_equiv"),
    ("Cash and cash equivalents - opening balances", "cash_flow",
     "Cash flow from financing activities", "cf_financing__beg_of_period_cash_and_cash_equiv"),
])
def test_jsws_captions_reach_their_lines(indas, caption, statement, section, key):
    hit = indas.match(caption, statement=statement, section=section)
    assert hit is not None and hit.canonical_key == key, hit


@pytest.mark.parametrize("caption,swept", [
    ("Borrowings *", False), ("Lease liabilities $", False), ("Borrowings (Current)^", False),
    ("Borrowing *", False), ("Borrowings (refer note 20)", False),
    ("Proceeds from issue of commercial paper", True), ("Buy-back of equity shares", True),
])
def test_the_reconciliation_of_financing_liabilities_is_no_financing_row(caption, swept):
    raw = json.loads((TEMPLATES / "output_csv_indas_line_items.json").read_text(encoding="utf-8"))
    other = next(i for i in raw["items"] if i["key"] == "cf_financing__other_financing_cash_flows")
    vetoed = any(re.search(rx, caption, re.IGNORECASE) for rx in other.get("exclude_hints") or [])
    assert vetoed is not swept


def test_madrigals_related_party_proceeds_are_stock_issued(usgaap):
    sec = "Cash flows from financing activities:"
    hit = usgaap.match("Proceeds from related parties - warrants, exercise of common stock options, "
                       "net of transaction costs", statement="cash_flow", section=sec)
    assert hit is not None and hit.canonical_key == "cf_financing__proceeds_from_stock_issuance"
    loan = usgaap.match("Proceeds from related party loans", statement="cash_flow", section=sec)
    assert loan is None or loan.canonical_key != "cf_financing__proceeds_from_stock_issuance"
