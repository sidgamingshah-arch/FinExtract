"""The export's statement-setup rows: the filing's own currency and scale, and the control totals.

Three defects, found by reading the rows the export writes for the five reference filings:
* Source Currency read `UnitContext.source_currency`, a field that does not exist — blank on every
  export, although the currency was detected (CNY on the three CAS filings and on 1966, HKD on 嘉民).
* Rounding showed the requested TARGET units, or nothing — not the scale the statements are printed
  in (units on the CAS filings, thousands on both HKEX filings).
* The control totals named only the HKFRS template's keys (bs_total_assets, …), so on the output_csv
  templates every control row — Total Assets, Total Equity & Liabilities, Total Income, Difference —
  was blank.
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
    (UnitContext(currency="HKD", scale_factor=Decimal(1000)), "HKD", "thousands"),   # 嘉民 $'000
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
