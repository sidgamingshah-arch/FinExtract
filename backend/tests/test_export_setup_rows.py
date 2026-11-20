"""The export's statement-setup rows: the filing's own currency and scale, and the control totals.

Three defects, found by reading the rows the export writes for the five reference filings:
* Source Currency read `UnitContext.source_currency`, a field that does not exist — blank on every
  export, although the currency was detected (CNY on the three CAS filings and on 1966, HKD on 佳明).
* Rounding showed the requested TARGET units, or nothing — not the scale the statements are printed
  in (units on the CAS filings, thousands on both HKEX filings).
* The control totals named only the HKFRS template's keys (bs_total_assets, …), so on the output_csv
  templates every control row — Total Assets, Total Equity & Liabilities, Total Income, Difference —
  was blank.

And two after them (`services.control_totals`):
* The controls read the PRINTED total, never the spread's own: the computed totals were handed to a
  decimal parser as `Calculated` objects it cannot read, so it fell back to the printed row —
  Difference 0 on every filing that prints its totals whatever was extracted, blank on every HKEX
  net-assets filing (China SCE and 佳明, whose spreads are out by 1,908,536 and 130,755 thousand).
* Unexplained Adj to Ret Profits was "0" whenever Difference was 0 and never looked at retained
  profits. It is now a roll-forward: opening + profit + the movements the spread identifies, against
  closing.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.routes.extractions import _build_supplemental_rows, _scale_name
from app.core.models.line_item import UnitContext

TPL = (pathlib.Path(__file__).resolve().parents[1]
       / "app" / "sample" / "templates" / "output_csv_hk_v1_template.json")


def _row(key, value):
    return {"canonical_key": key, "values": [
        {"period_label": "current", "value": str(value), "basis": "consolidated"}]}


def _setup(unit: UnitContext, rows=()):
    template = json.loads(TPL.read_text(encoding="utf-8"))
    doc = SimpleNamespace(unit_context=unit, notes=[], fmt=None)
    out = _build_supplemental_rows(template_def=template, base_rows=list(rows), disclosures=[],
                                   entity_name="X", doc_model=doc, ctx=None, options={})
    return {r["canonical_key"]: r for r in out}


def _value(row):
    vals = row.get("values") or []
    return vals[0].get("value_text") or vals[0].get("value") if vals else row.get("value")


@pytest.mark.parametrize("unit, currency, rounding", [
    (UnitContext(currency="CNY", scale_factor=Decimal(1)), "CNY", "units"),          # 单位：元
    (UnitContext(currency="CNY", scale_factor=Decimal(1000)), "CNY", "thousands"),   # 1966 RMB'000
    (UnitContext(currency="HKD", scale_factor=Decimal(1000)), "HKD", "thousands"),   # 佳明 $'000
])
def test_source_currency_and_rounding_are_the_filings_own(unit, currency, rounding):
    rows = _setup(unit)
    assert _value(rows["statement_setup_controls__source_currency"]) == currency
    assert _value(rows["statement_setup_controls__rounding"]) == rounding


def test_no_detected_currency_is_left_blank_not_guessed():
    rows = _setup(UnitContext(currency="", scale_factor=Decimal(1)))
    assert not _value(rows["statement_setup_controls__source_currency"])


def test_the_control_totals_read_the_output_template_keys():
    rows = _setup(UnitContext(currency="HKD", scale_factor=Decimal(1000)), rows=[
        _row("bs_ca__total_assets", "8140965"),
        _row("bs_cl__total_equity_and_liabilities", "8140965"),
        _row("is_pl__profit_for_the_year", "-352657")])
    assert Decimal(_value(rows["statement_setup_controls__total_assets"])) == Decimal("8140965")
    assert Decimal(_value(rows["statement_setup_controls__total_equity_reserves_liab"])) == Decimal("8140965")
    assert Decimal(_value(rows["statement_setup_controls__total_income_expenses"])) == Decimal("-352657")
    assert Decimal(_value(rows["statement_setup_controls__difference"])) == 0


def test_scale_names():
    assert [_scale_name(x) for x in (1, 1000, Decimal("1E5"), 1000000, None)] == [
        "units", "thousands", "lakhs", "millions", None]


def test_a_company_only_filings_setup_rows_are_its_own():
    """The setup and supplemental rows are written into every run. On a filing whose statements are
    all the company's, consolidated rows were the only figures on a Consolidated tab the filing never
    printed, and their controls were computed from derived lines unrelated to the statements."""
    from app.core.models.document import PageSource
    from app.core.models.enums import PageKind
    from app.services.periods import bases_present

    template = json.loads(TPL.read_text(encoding="utf-8"))
    doc = SimpleNamespace(unit_context=UnitContext(currency="INR", scale_factor=Decimal(100000)),
                          notes=[], fmt=None, line_items=[],
                          pages=[PageSource(index=0, kind=PageKind.FACE, statement="balance_sheet",
                                            scope="company")])
    base = [{"canonical_key": key, "values": [{"period_label": "current", "value": "39554",
                                               "basis": "standalone"}]}
            for key in ("bs_ca__total_assets", "bs_cl__total_equity_and_liabilities")]
    out = _build_supplemental_rows(template_def=template, base_rows=base, disclosures=[],
                                   entity_name="X", doc_model=doc, ctx=None, options={})
    rows = {r["canonical_key"]: r for r in out}

    assert {v["basis"] for r in out for v in r["values"]} == {"standalone"}
    assert Decimal(_value(rows["statement_setup_controls__total_assets"])) == Decimal("39554")
    assert bases_present(base + out) == ["standalone"]


# ---- the controls check the spread ----------------------------------------------------------------

def _rows(current: dict, prior: dict | None = None, residual: tuple = (), basis="consolidated"):
    out = []
    for key, value in current.items():
        values = [{"period_label": "current", "value": str(value), "basis": basis}]
        if prior and key in prior:
            values.append({"period_label": "prior", "value": str(prior[key]), "basis": basis})
        out.append({"canonical_key": key, "values": values,
                    "mapping_method": "residual" if key in residual else "exact"})
    for key, value in (prior or {}).items():
        if key not in current:
            out.append({"canonical_key": key, "mapping_method": "exact", "values": [
                {"period_label": "prior", "value": str(value), "basis": basis}]})
    return out


_HK = UnitContext(currency="HKD", scale_factor=Decimal(1000))


def _flags(row):
    return row.get("flags") or []


def test_difference_is_the_spreads_own_not_the_printed_totals():
    """The 佳明 shape: no printed total assets or total equity and liabilities (an HKEX net-assets
    balance sheet), and a spread that misses a liability. Difference shows the gap instead of
    being blank, and a printed total that agrees with itself no longer hides a missed line."""
    rows = _setup(_HK, rows=_rows({
        "bs_ca__total_current_assets": 1000, "bs_nca__total_non_current_assets": 500,
        "bs_equity__total_equity_and_reserves": 900, "bs_cl__total_liabilities": 450}))
    assert Decimal(_value(rows["statement_setup_controls__total_assets"])) == 1500
    assert Decimal(_value(rows["statement_setup_controls__total_equity_reserves_liab"])) == 1350
    assert Decimal(_value(rows["statement_setup_controls__difference"])) == 150
    assert "control:sum_of_lines:bs_ca__total_assets" in _flags(
        rows["statement_setup_controls__total_assets"])


def test_a_printed_total_that_its_lines_contradict_is_not_the_control():
    rows = _setup(_HK, rows=_rows({
        "bs_ca__total_current_assets": 1000, "bs_nca__total_non_current_assets": 500,
        "bs_ca__total_assets": 1600, "bs_cl__total_equity_and_liabilities": 1600}))
    assert Decimal(_value(rows["statement_setup_controls__total_assets"])) == 1500
    assert Decimal(_value(rows["statement_setup_controls__difference"])) == -100


def test_the_controls_are_read_on_the_statements_basis():
    from app.core.models.document import PageSource
    from app.core.models.enums import PageKind

    template = json.loads(TPL.read_text(encoding="utf-8"))
    doc = SimpleNamespace(unit_context=_HK, notes=[], fmt=None, line_items=[],
                          pages=[PageSource(index=0, kind=PageKind.FACE, statement="balance_sheet",
                                            scope="company")])
    base = (_rows({"bs_ca__total_assets": 777, "bs_cl__total_equity_and_liabilities": 700})
            + _rows({"bs_ca__total_assets": 39554,
                     "bs_cl__total_equity_and_liabilities": 39554}, basis="standalone"))
    out = _build_supplemental_rows(template_def=template, base_rows=base, disclosures=[],
                                   entity_name="X", doc_model=doc, ctx=None, options={})
    rows = {r["canonical_key"]: r for r in out}
    assert Decimal(_value(rows["statement_setup_controls__total_assets"])) == 39554
    assert Decimal(_value(rows["statement_setup_controls__difference"])) == 0


def test_the_retained_profits_roll_forward_ties_to_nil():
    """佳明, as the run reads it: 2,535,702 at the start of the year, a loss of 349,498, a transfer of
    reserves of 3,605 out of retained profits, 2,182,599 at the end."""
    rows = _setup(_HK, rows=_rows(
        {"bs_equity__retained_profits": 2182599, "is_pl__profit_for_the_year": -349498,
         "is_retained__transfer_to_reserves": -3605},
        prior={"bs_equity__retained_profits": 2535702}))
    row = rows["statement_setup_controls__unexplained_adj_to_ret_profits"]
    assert Decimal(_value(row)) == 0
    assert "retained_rollforward:counted:is_retained__transfer_to_reserves" in _flags(row)


def test_what_the_spread_does_not_identify_is_the_unexplained_adjustment():
    """澜起科技, as the run reads it today: the statement of changes in equity's surplus-reserve
    transfer (32,752,693.95), dividend (338,193,337.80) and internal transfer (503,296.35) reach no
    retained-profits line, so the roll-forward is short by exactly their sum."""
    rows = _setup(UnitContext(currency="CNY", scale_factor=Decimal(1)), rows=_rows(
        {"bs_equity__retained_profits": "4518383330.50",
         "is_pl__profit_for_the_year": "1411778923.59"},
        prior={"bs_equity__retained_profits": "3478053735.01"}))
    row = rows["statement_setup_controls__unexplained_adj_to_ret_profits"]
    assert Decimal(_value(row)) == Decimal("-371449328.1")
    assert "retained_rollforward:no_movement_read" in _flags(row)


def test_a_movement_the_sweep_put_on_a_line_is_not_explained():
    """China SCE and 佳明: the equity statement prints the year's loss in its retained-profits
    column, no retained-profits line claims it, and the residual sweep puts it on Other Adj to
    Retained Profits. Counted there it would be counted twice, beside the profit."""
    rows = _setup(_HK, rows=_rows(
        {"bs_equity__retained_profits": 2182599, "is_pl__profit_for_the_year": -349498,
         "is_retained__transfer_to_reserves": -3605,
         "is_retained__other_adj_to_retained_profits": -349498},
        prior={"bs_equity__retained_profits": 2535702},
        residual=("is_retained__other_adj_to_retained_profits",)))
    row = rows["statement_setup_controls__unexplained_adj_to_ret_profits"]
    assert Decimal(_value(row)) == 0
    assert ("retained_rollforward:swept_not_explained:is_retained__other_adj_to_retained_profits"
            in _flags(row))


def test_an_identified_other_adjustment_is_explained():
    rows = _setup(_HK, rows=_rows(
        {"bs_equity__retained_profits": 1100, "is_pl__profit_for_the_year": 200,
         "is_retained__other_adj_to_retained_profits": -100},
        prior={"bs_equity__retained_profits": 1000}))
    assert Decimal(_value(rows["statement_setup_controls__unexplained_adj_to_ret_profits"])) == 0


def test_a_proposed_dividend_is_not_deducted_from_the_years_retained_profits():
    rows = _setup(_HK, rows=_rows(
        {"bs_equity__retained_profits": 1200, "is_pl__profit_for_the_year": 200,
         "is_retained__proposed_cash_dividends": -80},
        prior={"bs_equity__retained_profits": 1000}))
    assert Decimal(_value(rows["statement_setup_controls__unexplained_adj_to_ret_profits"])) == 0


def test_no_opening_retained_profits_is_blank_and_says_why():
    """It used to read "0" here — whenever Difference was 0, which it is on this filing."""
    rows = _setup(_HK, rows=_rows(
        {"bs_ca__total_assets": 10, "bs_cl__total_equity_and_liabilities": 10,
         "bs_equity__retained_profits": 1200, "is_pl__profit_for_the_year": 200}))
    row = rows["statement_setup_controls__unexplained_adj_to_ret_profits"]
    assert not _value(row)
    assert "retained_rollforward:missing:opening_retained_profits" in _flags(row)
    assert "not_determined" in _flags(row)


def test_cents_are_kept_and_whole_figures_have_no_decimal_point():
    from app.services.control_totals import money

    assert str(money(8140965.0)) == "8140965"
    assert str(money(269103744306.53)) == "269103744306.53"
    assert str(money(0.1 + 0.2 - 0.3)) == "0"
    assert money(None) is None


def test_a_total_with_nothing_read_beneath_it_is_the_figure_as_printed_and_says_so():
    rows = _setup(_HK, rows=_rows({"bs_ca__total_assets": 8140965,
                                   "bs_cl__total_equity_and_liabilities": 8140965}))
    assert "control:as_shown:bs_ca__total_assets" in _flags(
        rows["statement_setup_controls__total_assets"])
