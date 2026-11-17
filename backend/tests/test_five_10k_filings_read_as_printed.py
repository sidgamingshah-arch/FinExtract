"""FIVE US 10-K FILINGS READ AS PRINTED — the fixes their run logs called for.

Measured on Nike FY2026, Welltower 2024, Alphabet 2025, BlackRock 2025 and Walmart FY2025, each
fetched from the filer's own site and run rules-only on the US GAAP pair:

* BLACKROCK prints a figure's dollar sign tight against it ("Total assets $169,998 $138,615"): the
  figures were no numbers, the row a caption folded onto the rows under it, total assets read as
  accrued compensation's 3,830 (`row_reconstruct._DOLLAR_SET_TIGHT`). Its common stock caption ends
  on a semicolon and the share counts printed under it were folded onto "Additional paid-in
  capital" (`_english_tail_of_the_row_above`). Its three-year cash flow lost two 2025 figures to
  the note fallback, "75" and "5" read as notes (`_no_note_column_on_a_10k`).
* NIKE prints its units line beside its years, "(Dollars in millions) 2026 2025 2024", and the
  years swept into other operating items (`_UNITS_CAPTION`); a three-year row past twelve words
  was never "in the statement's columns" and was refused as prose (`residual._THREE_YEAR_FACES`).
* WALMART prints "Total Walmart shareholders' equity", the noncontrolling interest, and "Total
  shareholders' equity": both totals bound the owners' line and were summed
  (`map_ontology._the_total_printed_below_its_part`).
* EVERY 10-K's financing section was reconciled against the net change in cash
  (`residual._totals_of_residuals`).

The currency stamped on a 10-K figure is pinned in `test_a_currency_code_is_a_word_of_its_own`.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.models.enums import Basis, LineRole, PrintedIn
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.services import row_reconstruct as R
from app.services.row_reconstruct import Word, build_line_items, us_10k_rows

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
TEMPLATE = TEMPLATES / "output_csv_usgaap_v1_template.json"
SET = TEMPLATES / "output_csv_usgaap_line_items.json"


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
    """A figure set flush right at `x1`."""
    return text, x1 - 0.0055 * len(text)


def _items(words, statement, ten_k=True):
    with us_10k_rows(ten_k):
        items, _ = build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                    statement=statement)
    return items


def _figures(item) -> dict[str, str]:
    return {v.period_label: str(v.value) for v in item.values.values()}


# ── BlackRock: the dollar sign set tight ──────────────────────────────────────────────────────

@pytest.mark.parametrize("token,value", [
    ("$169,998", Decimal(169998)), ("$(12,829)", Decimal(-12829)),
    ("($1,216)", Decimal(-1216)), ("$0.01", Decimal("0.01")),
])
def test_a_dollar_sign_set_tight_is_read_off_a_10k_figure(token, value):
    with us_10k_rows(True):
        assert R._num(token) == value
    assert R._num(token) is None                      # off a 10-K, as before


@pytest.mark.parametrize("token", ["$", "$—", "HK$100", "$(49),"])
def test_nothing_else_carrying_a_dollar_sign_becomes_a_figure(token):
    with us_10k_rows(True):
        assert R._num(token) is None


def _blackrock_assets() -> list[Word]:
    return (_row(0.10, [("December 31,", 0.62), ("December 31,", 0.80)])
            + _row(0.115, [("2025", 0.66), ("2024", 0.84)])
            + _row(0.14, [("Goodwill", 0.05), _right("35,283", 0.72), _right("25,949", 0.90)])
            + _row(0.155, [("Total assets", 0.03), _right("$169,998", 0.72),
                           _right("$138,615", 0.90)])
            + _row(0.170, [("Liabilities", 0.03)])
            + _row(0.185, [("Accrued compensation and benefits", 0.05), ("$", 0.64),
                           _right("3,830", 0.72), ("$", 0.82), _right("2,964", 0.90)]))


def test_total_assets_printed_tight_against_its_dollar_sign_is_a_row_of_its_own():
    rows = {i.source_label: _figures(i) for i in _items(_blackrock_assets(), "balance_sheet")}
    assert rows["Total assets"] == {"current": "169998", "prior": "138615"}
    accrued = next(v for k, v in rows.items() if "Accrued compensation" in k)
    assert accrued == {"current": "3830", "prior": "2964"}
    assert not any("$169,998" in k for k in rows)


def _blackrock_equity() -> list[Word]:
    return (_row(0.10, [("December 31,", 0.62), ("December 31,", 0.80)])
            + _row(0.115, [("2025", 0.66), ("2024", 0.84)])
            + _row(0.14, [("Common stock, $0.01 par value;", 0.05), _right("2", 0.72),
                          _right("2", 0.90)])
            + _row(0.152, [("Shares authorized: 500,000,000 at December 31, 2025 and 2024;",
                            0.07)])
            + _row(0.163, [(("Shares issued: 156,276,289 and 155,318,170 at December 31, 2025 "
                             "and 2024, respectively;"), 0.09)])
            + _row(0.174, [(("Shares outstanding: 155,069,171 and 154,947,813 at December 31, "
                             "2025 and 2024, respectively"), 0.09)])
            + _row(0.188, [("Additional paid-in capital", 0.05), _right("19,748", 0.72),
                           _right("13,446", 0.90)])
            + _row(0.202, [("Retained earnings", 0.05), _right("37,899", 0.72),
                           _right("35,611", 0.90)]))


def test_the_share_counts_under_a_caption_ending_on_a_semicolon_stay_with_it():
    rows = {i.source_label: _figures(i) for i in _items(_blackrock_equity(), "balance_sheet")}
    assert rows["Additional paid-in capital"] == {"current": "19748", "prior": "13446"}
    common = next(k for k in rows if k.startswith("Common stock"))
    assert "$0.01 par value" in common and "Shares outstanding" in common
    assert rows[common] == {"current": "2", "prior": "2"}


def _alphabet_equity() -> list[Word]:
    """Alphabet 2025: a par value set tight at the end of a caption's first line."""
    return (_row(0.10, [("December 31,", 0.70)])
            + _row(0.115, [("2024", 0.72), ("2025", 0.86)])
            + _row(0.14, [(("Class A, Class B, and Class C stock and additional paid-in "
                            "capital, $0.001"), 0.10)])
            + _row(0.152, [(("par value per share: 300,000 shares authorized (Class A "
                             "180,000, Class B"), 0.10)])
            + _row(0.164, [(("5,515) and 12,088 (Class A 5,822, Class B 837, Class C 5,429) "
                             "shares"), 0.10)])
            + _row(0.176, [("issued and outstanding", 0.10), _right("84,800", 0.78),
                           _right("93,126", 0.92)]))


def test_a_par_value_set_tight_against_its_caption_is_no_figure():
    words = _alphabet_equity()
    at = next(i for i, w in enumerate(words) if w.text == "$0.001")
    assert R._tight_after(words[at - 1], words[at])
    rows = {i.source_label: _figures(i) for i in _items(words, "balance_sheet")}
    (caption,) = [k for k in rows if k.startswith("Class A")]
    assert "$0.001 par value" in caption
    # Alphabet prints 2024 before 2025: the current year is the right-hand column.
    assert rows[caption] == {"current": "93126", "prior": "84800"}


def _three_years(caption: str, a: str, b: str, c: str, y: float) -> list[Word]:
    return _row(y, [(caption, 0.05), _right(a, 0.66), _right(b, 0.78), _right(c, 0.90)])


def _blackrock_cash_flow() -> list[Word]:
    return (_row(0.10, [("2025", 0.62), ("2024", 0.74), ("2023", 0.86)])
            + _three_years("Accrued compensation and benefits", "659", "367", "145", 0.13)
            + _three_years("Accounts payable and accrued liabilities", "75", "259", "(26)", 0.145)
            + _three_years("Other financing activities", "5", "(18)", "26", 0.16))


def test_a_small_figure_on_a_10k_with_no_note_column_is_a_figure():
    rows = {i.source_label: i for i in _items(_blackrock_cash_flow(), "cash_flow")}
    ap = rows["Accounts payable and accrued liabilities"]
    assert ap.note_number is None and _figures(ap) == {"current": "75", "prior": "259",
                                                      "col2": "-26"}
    other = rows["Other financing activities"]
    assert other.note_number is None and _figures(other)["current"] == "5"


def test_off_a_10k_the_note_fallback_is_as_it_was():
    rows = {i.source_label: i for i in _items(_blackrock_cash_flow(), "cash_flow", ten_k=False)}
    assert rows["Accounts payable and accrued liabilities"].note_number == "75"


# ── Nike: the units line beside the years ─────────────────────────────────────────────────────

def _nike_cash_flow() -> list[Word]:
    return (_row(0.08, [("Year Ended May 31,", 0.70)])
            + _row(0.095, [("(Dollars in millions)", 0.05), ("2026", 0.62), ("2025", 0.74),
                           ("2024", 0.86)])
            + _three_years("Impairment and other", "50", "33", "48", 0.12)
            + _three_years("Net foreign currency adjustments", "22", "37", "(138)", 0.135))


def test_a_10k_face_units_line_beside_its_years_is_no_row():
    labels = [i.source_label for i in _items(_nike_cash_flow(), "cash_flow")]
    assert "(Dollars in millions)" not in labels
    assert "Impairment and other" in labels


@pytest.mark.parametrize("caption,units", [
    ("(Dollars in millions)", True), ("(In millions, except per share data)", True),
    ("(in millions, except shares and per share data)", True),
    ("(Amounts in millions, except per share data)", True),
    ("Balance at December 31", False), ("Total", False),
])
def test_the_units_caption(caption, units):
    assert bool(R._UNITS_CAPTION.match(caption)) is units


# ── the residual on a three-year face ─────────────────────────────────────────────────────────

def _face_row(label: str, **by_period) -> LineItem:
    li = LineItem(source_label=label, printed_in=PrintedIn.FACE)
    for period, value in by_period.items():
        li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                    basis=Basis.CONSOLIDATED, period_label=period))
    return li


def test_a_10k_rows_third_year_is_a_statement_column():
    from app.stages import residual

    row = _face_row("(Increase) decrease in prepaid expenses, operating lease right-of-use "
                    "assets and other current and non-current assets",
                    current="519", prior="-224", col2="-260")
    assert residual._is_narrative_row(row)              # read as a two-year face: refused
    token = residual._THREE_YEAR_FACES.set(True)
    try:
        assert not residual._is_narrative_row(row)
    finally:
        residual._THREE_YEAR_FACES.reset(token)


def test_a_residual_is_reconciled_against_the_total_that_sums_it():
    from app.stages.residual import _totals_of_residuals

    ctx = SimpleNamespace(template_def=json.loads(TEMPLATE.read_text(encoding="utf-8")))
    totals = _totals_of_residuals(ctx, {"cf_financing__other_financing_cash_flows",
                                        "cf_oper_indirect__other_operating_items",
                                        "bs_ca__other_current_assets"})
    total, reached = totals["cf_financing__other_financing_cash_flows"]
    assert total == "cf_financing__cash_flows_from_finance_activities"
    assert "cf_financing__dividends_paid" in reached
    assert not {"cf_financing__net_foreign_exchange_difference",
                "cf_financing__beg_of_period_cash_and_cash_equiv"} & reached
    assert totals["cf_oper_indirect__other_operating_items"][0] == \
        "cf_oper_indirect__cash_flows_oper_activ_indirect"
    assert totals["bs_ca__other_current_assets"][0] == "bs_ca__total_current_assets"


def test_the_section_rule_stands_where_it_takes_the_residuals_own_total():
    from app.services.rollups import SectionMembers
    from app.stages.residual import _against_its_own_total

    fin = "cf_financing__cash_flows_from_finance_activities"
    net = "cf_financing__net_increase_in_cash"
    mem = SectionMembers(section="cf_financing", subtotals=[fin, net],
                         dedicated=["cf_financing__dividends_paid",
                                    "cf_financing__net_foreign_exchange_difference"],
                         residuals=["cf_financing__other_financing_cash_flows"])
    own = (fin, frozenset({"cf_financing__dividends_paid",
                           "cf_financing__other_financing_cash_flows"}))
    # The section rule takes the net change in cash: reconciled against the financing total.
    values = {fin: {"current": -2292.0}, net: {"current": 99.0}}
    narrowed = _against_its_own_total(mem, own, values, "current")
    assert narrowed.subtotals == [fin]
    assert narrowed.dedicated == ["cf_financing__dividends_paid"]
    # The section rule takes the financing total itself (no net change printed): unchanged.
    assert _against_its_own_total(mem, own, {fin: {"current": -2292.0}}, "current") is mem


# ── Walmart: the total printed below its part ─────────────────────────────────────────────────

def _bound(label: str, key: str, ordinal: int, current: str, prior: str) -> LineItem:
    li = LineItem(source_label=label, canonical_key=key, ordinal=ordinal, role=LineRole.SUBTOTAL,
                  printed_in=PrintedIn.FACE)
    for period, value in (("current", current), ("prior", prior)):
        li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                    basis=Basis.CONSOLIDATED, period_label=period,
                                    provenance=Provenance(page_index=56)))
    return li


def _walmart_doc(total_prior: str = "90349"):
    owners = "bs_equity__equity_attributable_to_owners"
    rows = [_bound("Retained earnings", "bs_equity__retained_earnings", 1, "98313", "89814"),
            _bound("Total Walmart shareholders' equity", owners, 2, "91013", "83861"),
            _bound("Nonredeemable noncontrolling interest", "bs_equity__non_controlling_interests",
                   3, "6408", "6488"),
            _bound("Total shareholders' equity", owners, 4, "97421", total_prior)]
    return SimpleNamespace(line_items=rows)


def _ctx():
    logs: list[str] = []
    return SimpleNamespace(template_def=json.loads(TEMPLATE.read_text(encoding="utf-8")),
                           settings=SimpleNamespace(extraction=SimpleNamespace(
                               recon_abs_tolerance=1.0)),
                           log=logs.append, logs=logs)


def test_the_total_printed_below_the_owners_equity_and_its_nci_is_total_equity():
    from app.stages.map_ontology import MapOntologyStage

    doc, ctx = _walmart_doc(), _ctx()
    assert MapOntologyStage._the_total_printed_below_its_part(doc, ctx) == 1
    keys = {li.source_label: li.canonical_key for li in doc.line_items}
    assert keys["Total shareholders' equity"] == "bs_equity__total_equity"
    assert keys["Total Walmart shareholders' equity"] == "bs_equity__equity_attributable_to_owners"


def test_nothing_moves_where_the_page_does_not_add_up():
    from app.stages.map_ontology import MapOntologyStage

    doc = _walmart_doc(total_prior="90000")            # 83,861 + 6,488 is 90,349
    assert MapOntologyStage._the_total_printed_below_its_part(doc, _ctx()) == 0
    assert doc.line_items[-1].canonical_key == "bs_equity__equity_attributable_to_owners"


# ── the US GAAP set: the captions the five filings print ──────────────────────────────────────

@pytest.fixture(scope="module")
def matcher():
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.mapping import OntologyMatcher
    from app.services.working_view import build_working_view

    items = load_line_item_set(json.loads(SET.read_text(encoding="utf-8")), resolve=True)
    return OntologyMatcher(build_working_view(items), locale="en", settings=get_settings())


@pytest.mark.parametrize("caption,statement,section,key", [
    ("Total selling and administrative expense", "profit_and_loss", None,
     "is_pl__selling_general_and_administrative"),
    ("Change in net foreign currency translation adjustment", "profit_and_loss",
     "Other comprehensive income (loss), net of tax:", "is_oci__foreign_currency_translation"),
    ("Change in net gains (losses) on cash flow hedges", "profit_and_loss",
     "Other comprehensive income (loss), net of tax:", "is_oci__cash_flow_hedges"),
    ("Change in net gains (losses) on other", "profit_and_loss",
     "Other comprehensive income (loss), net of tax:", "is_oci__other"),
    ("Derivative and financial instruments designated as hedges gain (loss)", "profit_and_loss",
     "Other comprehensive income (loss):", "is_oci__cash_flow_hedges"),
    (("Increase (decrease) in accounts payable, accrued liabilities, operating lease liabilities "
      "and other current and non-current liabilities"), "cash_flow",
     "Cash provided (used) by operations:", "cf_oper_indirect__chg_in_accounts_payable"),
    # Welltower 2024's financing section, every row of which had been swept into other financing.
    ("Effect of foreign currency translation on cash and cash equivalents and restricted cash",
     "cash_flow", "Financing activities:", "cf_financing__net_foreign_exchange_difference"),
    ("Net proceeds from issuance of senior unsecured notes", "cash_flow", "Financing activities:",
     "cf_financing__proceeds_from_debt"),
    ("Net proceeds from the issuance of secured debt", "cash_flow", "Financing activities:",
     "cf_financing__proceeds_from_debt"),
    ("Payments to extinguish senior unsecured notes", "cash_flow", "Financing activities:",
     "cf_financing__repayment_of_debt"),
    ("Payments on secured debt", "cash_flow", "Financing activities:",
     "cf_financing__repayment_of_debt"),
    ("Net proceeds from the issuance of common stock", "cash_flow", "Financing activities:",
     "cf_financing__proceeds_from_stock_issuance"),
    ("Cash distributions to stockholders", "cash_flow", "Financing activities:",
     "cf_financing__dividends_paid"),
    ("Net increase (decrease) under unsecured credit facility and commercial paper", "cash_flow",
     "Financing activities:", "cf_financing__net_change_short_term_debt"),
])

def test_the_captions_the_filings_print_reach_their_lines(matcher, caption, statement, section,
                                                         key):
    hit = matcher.match(caption, statement=statement, section=section)
    assert hit is not None and hit.canonical_key == key, hit


def test_a_dividend_declared_and_not_paid_is_not_dividends_paid(matcher):
    hit = matcher.match("Dividends declared and not paid", statement="cash_flow",
                        section="Cash flows from financing activities:")
    assert hit is None or hit.canonical_key != "cf_financing__dividends_paid"
    paid = matcher.match("Dividends — common and preferred", statement="cash_flow",
                         section="Cash flows from financing activities:")
    assert paid is not None and paid.canonical_key == "cf_financing__dividends_paid"


def test_a_distribution_to_noncontrolling_interests_is_no_dividend(matcher):
    hit = matcher.match("Distributions to noncontrolling interests(1)", statement="cash_flow",
                        section="Financing activities:")
    assert hit is None or hit.canonical_key != "cf_financing__dividends_paid"
