"""THE 10-K ITEMS LEFT OVER — Welltower's REIT balance sheet and Schedule III, BlackRock's
asset-manager statements, Walmart's cut-off litigation reading.

Measured on the filings themselves (Welltower 2024, BlackRock 2025 and Walmart FY2025 annual
reports, from the filers' own sites), run rules-only on the US GAAP pair:

* BLACKROCK names its equity blocks over their rows — "Permanent equity", then "BlackRock, Inc.
  stockholders' equity" — and both were glued onto "Common stock, $0.01 par value;" (no line
  matched it). Its unclassified balance sheet prints a bare "Assets" over cash and "Liabilities"
  over accrued compensation, and its statement of income a bare "Expense": cash reached no line
  (`row_reconstruct._EQUITY_BLOCK_HEADING`, `_LONE_STATEMENT_HEADING`, the nested peel in
  `_split_face_heading_prefix`). Its fee revenue, its expenses, its "NCI" rows and the per-share
  block under "Earnings per share attributable to BlackRock, Inc. common stockholders:" reached no
  line, or the wrong one (the US GAAP set; `map_ontology._block_caption`).
* WELLTOWER prints "$ — $ —" for a debt line it no longer has, and the row was read as a caption's
  first line and folded onto "Senior unsecured notes" (`_nil_row`). "Net real estate
  investments" is the sum of the four rows above it with no "Total" in its caption, and the sweep
  took it into other non-current assets beside its parts (`residual._sums_of_the_rows_above`). Its
  Schedule III prints two narrow columns side by side, a column of years on every row and each
  property's street address after the figures: 4,104 figures were dropped as duplicates
  (`_one_figure_per_row`, `_bands_with_a_figure`, `_scan_row`).
* WALMART's litigation reply was cut off in its last point, after the retry with twice the room:
  the points it had finished are kept and checked (`disclosure_reading.salvage_cut_off_reply`),
  and the adapters say why a reply ended (its stop reason, on the error).
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from app.core.models.enums import Basis, PrintedIn
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.services import row_reconstruct as R
from app.services.row_reconstruct import Word, build_line_items, us_10k_rows

SET = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates" / \
    "output_csv_usgaap_line_items.json"


def _row(y: float, cells: list[tuple[str, float]], h: float = 0.010) -> list[Word]:
    """Words of one printed line: (text, x0), each word as wide as its text."""
    out = []
    for text, x0 in cells:
        x = x0
        for part in text.split(" "):
            w = 0.0055 * max(len(part), 1)
            out.append(Word(text=part, bbox=BBox(x0=x, y0=y, x1=x + w, y1=y + h)))
            x += w + 0.004
    return out


def _right(text: str, x1: float) -> tuple[str, float]:
    return text, x1 - 0.0055 * len(text)


def _items(words, statement, ten_k=True):
    with us_10k_rows(ten_k):
        items, _ = build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                    statement=statement)
    return items


def _figures(item) -> dict[str, str]:
    return {v.period_label: str(v.value) for v in item.values.values()}


def _years(y: float = 0.10) -> list[Word]:
    return (_row(y, [("December 31,", 0.62), ("December 31,", 0.80)])
            + _row(y + 0.015, [("2025", 0.66), ("2024", 0.84)]))


# ── headings over the first row of a block ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,heading", [
    ("Permanent equity", True), ("Temporary equity", True),
    ("BlackRock, Inc. stockholders' equity", True), ("Walmart shareholders’ equity", True),
    ("Stockholders' equity (deficit)", False),          # no company name: the section banner's
    ("Total stockholders' equity", False), ("Total BlackRock, Inc. stockholders' equity", False),
    ("Liabilities and stockholders' equity", False),
    ("Assets", True), ("Liabilities", True), ("Expense", True),
    ("Total assets", False), ("Other liabilities", False), ("Interest expense", False),
])
def test_what_a_heading_of_the_rows_beneath_it_is(text, heading):
    assert R._is_face_group_heading(text) is heading


def test_a_heading_nested_in_another_leaves_the_caption_under_both():
    words = (_years()
             + _row(0.14, [("Permanent equity", 0.03)])
             + _row(0.152, [("BlackRock, Inc. stockholders' equity", 0.04)])
             + _row(0.164, [("Common stock, $0.01 par value", 0.05), _right("2", 0.72),
                            _right("2", 0.90)])
             + _row(0.19, [("Additional paid-in capital", 0.05), _right("19,748", 0.72),
                           _right("13,446", 0.90)]))
    rows = {i.source_label: _figures(i) for i in _items(words, "balance_sheet")}
    assert rows["Common stock, $0.01 par value"] == {"current": "2", "prior": "2"}
    assert not any("Permanent" in k or "stockholders" in k for k in rows)


def test_a_bare_assets_heading_is_no_word_of_the_cash_caption():
    words = (_years()
             + _row(0.14, [("Assets", 0.03)])
             + _row(0.152, [("Cash and cash equivalents", 0.05), ("$", 0.64), _right("11,468", 0.72),
                            ("$", 0.82), _right("12,762", 0.90)])
             + _row(0.166, [("Accounts receivable", 0.05), _right("5,158", 0.72),
                            _right("4,304", 0.90)]))
    rows = {i.source_label: _figures(i) for i in _items(words, "balance_sheet")}
    cash = next(k for k in rows if "Cash and cash equivalents" in k)
    assert cash.startswith("Cash and cash equivalents")
    assert rows[cash] == {"current": "11468", "prior": "12762"}


# ── Welltower: a nil row with its dollar signs ─────────────────────────────────────────────────

def _dollar_nil() -> list[Word]:
    return _row(0.14, [("Unsecured credit facility and commercial paper", 0.05), ("$", 0.64),
                       _right("—", 0.72), ("$", 0.82), _right("—", 0.90)])


def test_a_10k_nil_row_may_print_its_dollar_signs():
    line = sorted(_dollar_nil(), key=lambda w: w.bbox.x0)
    with us_10k_rows(True):
        assert R._nil_row(line)
    assert not R._nil_row(line)                         # off a 10-K, as before


def test_the_row_under_a_nil_row_keeps_its_own_caption():
    words = (_years() + _dollar_nil()
             + _row(0.154, [("Senior unsecured notes", 0.05), _right("13,162,102", 0.72),
                            _right("12,844,743", 0.90)]))
    rows = {i.source_label: _figures(i) for i in _items(words, "balance_sheet")}
    assert rows["Senior unsecured notes"] == {"current": "13162102", "prior": "12844743"}


# ── Welltower's Schedule III: one figure per row in a column ───────────────────────────────────

def test_two_narrow_columns_chained_into_one_are_split_at_their_gutter():
    rows = [[(0.655, "554"), (0.690, "2024")] for _ in range(4)]
    flat = sorted(x for r in rows for x in r)
    split = R._one_figure_per_row([flat], rows)
    assert [[t for _x, t in c] for c in split] == [["554"] * 4, ["2024"] * 4]


def test_a_column_of_one_figure_per_row_is_left_whole():
    rows = [[(0.655 + 0.004 * i, f"{100 + i}")] for i in range(6)]
    flat = sorted(x for r in rows for x in r)
    assert R._one_figure_per_row([flat], rows) == [flat]


def test_on_a_10k_two_narrow_columns_are_two_bands_and_off_one_as_before():
    rows = [[(0.50, "1,234"), (0.655, "554"), (0.690, "2024")] for _ in range(6)]
    with us_10k_rows(True):
        assert len(R._value_column_bands(rows)) == 3
    # Off a 10-K the split is not made (Kaming's fixed-asset note lost its headings to it).
    assert len(R._value_column_bands(rows)) == 2


def test_a_column_of_years_on_every_row_is_kept_on_a_10k():
    bands = [0.50, 0.60, 0.70]
    rows = [[(0.50, "1,234"), (0.60, "554"), (0.70, "2024")] for _ in range(6)]
    with us_10k_rows(True):
        assert R._bands_with_a_figure(bands, rows) == bands
    assert R._bands_with_a_figure(bands, rows) == [0.50, 0.60]      # off a 10-K, as before


def test_a_years_band_on_a_row_or_two_is_still_dropped():
    bands = [0.50, 0.60, 0.70]
    rows = ([[(0.50, "2025"), (0.60, "2024")]]                 # the header's years
            + [[(0.60, "1,234"), (0.70, "554")] for _ in range(6)])
    with us_10k_rows(True):
        assert R._bands_with_a_figure(bands, rows) == [0.60, 0.70]


def test_a_street_number_after_the_figures_is_no_figure():
    line = sorted(_row(0.14, [("Adrian MI", 0.03), _right("1,234", 0.52), _right("554", 0.62),
                              _right("2024", 0.70), ("2625 N Adrian Highway", 0.74)]),
                  key=lambda w: w.bbox.x0)
    with us_10k_rows(True):
        _label, _note, values = R._scan_row(line)
    assert [w.text for w in values] == ["1,234", "554", "2024"]


# ── Welltower: a subtotal whose caption does not say so ────────────────────────────────────────

def _face(label: str, current: str, prior: str | None, page: int = 60) -> LineItem:
    li = LineItem(source_label=label, printed_in=PrintedIn.FACE)
    for period, value in (("current", current), ("prior", prior)):
        if value is None:
            continue
        li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                    basis=Basis.CONSOLIDATED, period_label=period,
                                    provenance=Provenance(page_index=page)))
    return li


def test_a_row_equal_to_the_sum_of_the_rows_above_it_is_their_subtotal():
    from app.stages.residual import _sums_of_the_rows_above

    # Welltower 2024 as printed: a year with no sales-type leases prints no figure for them.
    rows = [_face("Net real property owned", "40673242", "37063357"),
            _face("Right of use assets, net", "1201131", "350969"),
            _face("Investments in sales-type leases, net", "172260", None),
            _face("Real estate loans receivable, net of credit allowance", "1805044", "1361587"),
            _face("Net real estate investments", "43851677", "38775913"),
            _face("Investments in unconsolidated entities", "1768772", "1636531")]
    assert _sums_of_the_rows_above(rows) == {rows[4].id}


def test_no_sum_where_the_rows_do_not_add_up_or_stand_on_another_page():
    from app.stages.residual import _sums_of_the_rows_above

    off = [_face("A", "100", "90"), _face("B", "20", "10"), _face("C", "121", "100")]
    assert _sums_of_the_rows_above(off) == set()
    split = [_face("A", "100", "90", page=1), _face("B", "20", "10", page=1),
             _face("C", "120", "100", page=2)]
    assert _sums_of_the_rows_above(split) == set()


# ── Walmart: a reading cut off in its last point ───────────────────────────────────────────────

_PASSAGE = ("The Company is a defendant in the opioid litigation. In November 2022 the Company "
            "agreed to pay approximately $3.1 billion to settle substantially all opioid claims.")


def _cut_off_error(text: str):
    from app.services.disclosure_reading import DisclosureReply

    with pytest.raises(ValidationError) as caught:
        DisclosureReply.model_validate_json(text)
    return caught.value


def test_the_points_a_cut_off_reading_finished_are_kept_and_checked():
    from app.services.disclosure_reading import check_reply, salvage_cut_off_reply

    reply = json.dumps({"status": "disclosed", "summary": "Walmart discloses opioid litigation.",
                        "points": [{"text": "Walmart agreed to settle the opioid claims.",
                                    "passage": "P1",
                                    "quote": "agreed to pay approximately $3.1 billion",
                                    "amount": "$3.1 billion", "currency": "USD"}]})
    cut = reply[:-2] + ', {"text": "A second matter.", "passage": "P1", "amount": "'
    salvaged = salvage_cut_off_reply(_cut_off_error(cut))
    assert salvaged is not None and salvaged.status == "disclosed"
    assert [p.quote for p in salvaged.points] == ["agreed to pay approximately $3.1 billion"]
    reading, _refused = check_reply("litigation", salvaged,
                                    [{"id": "P1", "page": 88, "page_index": 87, "kind": "notes",
                                      "text": _PASSAGE}])
    assert reading is not None and len(reading["points"]) == 1


def test_a_reading_cut_off_before_any_point_keeps_its_status_and_summary():
    from app.services.disclosure_reading import salvage_cut_off_reply

    cut = '{"status": "general_only", "summary": "Ordinary-course claims only.", "points": [{"te'
    salvaged = salvage_cut_off_reply(_cut_off_error(cut))
    assert salvaged is not None
    assert (salvaged.status, salvaged.summary, salvaged.points) == (
        "general_only", "Ordinary-course claims only.", [])


def test_a_reply_that_is_wrong_rather_than_short_is_not_salvaged():
    from app.services.disclosure_reading import salvage_cut_off_reply

    assert salvage_cut_off_reply(_cut_off_error('{"summary": "no status"}')) is None
    assert salvage_cut_off_reply(RuntimeError("timeout")) is None


class _Out(BaseModel):
    label: str


def test_the_anthropic_adapter_says_why_a_reply_ended():
    from app.adapters.anthropic_llm import AnthropicLlmProvider

    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"label": "cut')],
                           usage=SimpleNamespace(input_tokens=1, output_tokens=3000),
                           model="m", stop_reason="max_tokens")
    provider = AnthropicLlmProvider()
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))
    with pytest.raises(ValidationError) as caught:
        provider.complete_structured(system="s", messages=[{"role": "user", "content": "x"}],
                                     response_schema=_Out, max_tokens=3000)
    assert "stop_reason=max_tokens output_tokens=3000 max_tokens=3000" in " ".join(
        caught.value.__notes__)


# ── the US GAAP set: the captions BlackRock and Welltower print ────────────────────────────────

@pytest.fixture(scope="module")
def matcher():
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.mapping import OntologyMatcher
    from app.services.working_view import build_working_view

    items = load_line_item_set(json.loads(SET.read_text(encoding="utf-8")), resolve=True)
    return OntologyMatcher(build_working_view(items), locale="en", settings=get_settings())


PL = ("profit_and_loss", "Revenue")


@pytest.mark.parametrize("caption,statement,section,key", [
    # BlackRock 2025's statement of income: with these the revenue lines add up to 24,216.
    ("Investment advisory and administration fees", *PL, "is_pl__net_sales"),
    ("Securities lending revenue", *PL, "is_pl__other_revenues"),
    ("Investment advisory performance fees", *PL, "is_pl__other_revenues"),
    ("Technology services and subscription revenue", *PL, "is_pl__other_revenues"),
    ("Distribution fees", *PL, "is_pl__other_revenues"),
    ("Advisory and other revenue", *PL, "is_pl__other_revenues"),
    ("Employee compensation and benefits", *PL, "is_pl__selling_general_and_administrative"),
    ("General and administration expense", *PL, "is_pl__selling_general_and_administrative"),
    ("Total sales, asset and account expense", *PL, "is_pl__cost_of_revenue"),
    ("Change in fair value of contingent consideration", *PL,
     "is_pl__other_operating_expense_income"),
    ("Amortization and impairment of intangible assets", *PL,
     "is_pl__depreciation_and_amortization"),
    ("Total expense", *PL, "is_pl__total_costs_and_expenses"),
    ("Net gain (loss) on investments", *PL, "is_pl__other_income_expense"),
    ("Net income (loss) attributable to NCI - CIPs", *PL, "is_pl__net_income_attributable_to_nci"),
    ("Net income attributable to BlackRock, Inc.", *PL, "is_pl__net_income_attributable_to_parent"),
    ("Comprehensive income (loss) attributable to NCI - Subco", "profit_and_loss",
     "Other comprehensive income (loss), net of tax:",
     "is_oci__comprehensive_income_attributable_to_nci"),
    # Read with its heading, the per-share block under an attribution heading.
    ("Earnings per share attributable to BlackRock, Inc. common stockholders: Basic", "profit_and_loss",
     "Earnings per share attributable to BlackRock, Inc. common stockholders:", "is_pl__eps_basic"),
    ("Weighted-average common shares outstanding: Diluted (including Subco Units)", "profit_and_loss",
     "Earnings per share attributable to BlackRock, Inc. common stockholders:",
     "is_pl__weighted_average_shares_diluted"),
    # Welltower 2024's balance sheet, a REIT's.
    ("Senior unsecured notes", "balance_sheet", "Liabilities:", "bs_ncl__long_term_debt"),
    ("Secured debt", "balance_sheet", "Liabilities:", "bs_ncl__long_term_debt"),
    ("Net real property owned", "balance_sheet", "Assets:",
     "bs_nca__property_plant_and_equipment"),
    ("Investments in unconsolidated entities", "balance_sheet", "Assets:",
     "bs_nca__equity_method_investments"),
    ("Redeemable noncontrolling interests", "balance_sheet", None, "bs_mezz__temporary_equity"),
    ("Total permanent equity", "balance_sheet", "Equity:", "bs_equity__total_equity"),
])
def test_the_captions_the_filings_print_reach_their_lines(matcher, caption, statement, section,
                                                         key):
    hit = matcher.match(caption, statement=statement, section=section)
    assert hit is not None and hit.canonical_key == key, hit


def test_a_noncontrolling_interests_row_is_not_the_owners(matcher):
    hit = matcher.match("Net income (loss) attributable to NCI - Subco", statement="profit_and_loss",
                        section="Revenue")
    assert hit is not None and hit.canonical_key != "is_pl__net_income_attributable_to_parent"


def test_a_per_share_heading_worded_as_an_attribution_names_the_rows_under_it():
    from app.stages.map_ontology import _block_caption

    heading = "Earnings per share attributable to BlackRock, Inc. common stockholders:"
    li = LineItem(source_label="Basic $", printed_in=PrintedIn.FACE, section_hint=heading)
    assert _block_caption(li, "profit_attributable_to") == (heading.rstrip(":"),
                                                             f"{heading} Basic $")
    # An attribution banner that names no per-share block lends its rows nothing.
    li = LineItem(source_label="Noncontrolling interests", printed_in=PrintedIn.FACE,
                  section_hint="Net income attributable to:")
    assert _block_caption(li, "profit_attributable_to") is None
