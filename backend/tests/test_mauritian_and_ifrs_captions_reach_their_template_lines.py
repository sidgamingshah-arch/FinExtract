"""MAURITIAN AND IFRS CAPTIONS REACH THE TEMPLATE LINES THEY NAME, NOT THEIR SECTION'S RESIDUAL.

NEW FILE -> backend/tests/test_mauritian_and_ifrs_captions_reach_their_template_lines.py

Aliases added to `output_csv_hk_line_items.json` (modified keys only; no key added or removed, the
fixed template unchanged). Each was measured on the five reference filings and the rest of the
corpus before it went in, and each pins a caption the filing prints:

  * below operating profit, so the operating profit the grid computes is the printed one:
    "Finance income" (Vivo p66 7,065 / 11,701), "Share of profit of joint ventures" (Vivo p66),
    "Share of result(s) of associate" (Moroil p67, Phoenix p129) — operating profit 670,394 /
    641,763 became 658,492 / 628,618 as printed on Vivo;
  * "Cost of operations" (Moroil p67 1,565,644): gross profit 1,852,716 became 287,072 as printed;
  * "Profit before finance costs" (Phoenix p129 1,188,509): its operating profit;
  * balance-sheet lines the residuals held: current tax, retirement and employee benefit
    obligations, investments in joint ventures / associates / subsidiaries, "Stated capital",
    "Cash at bank and in hand", "Bank and cash balances", "Borrowings" (by its section);
  * cash-flow lines: the profit line, amortisation, disposal gains, working-capital changes,
    purchases and disposals of PP&E and intangibles, dividends paid, borrowings;
  * "Dividends declared" on the equity statement (Vivo p68 (486,456) / (504,343)).

"Provision for credit losses" was NOT aliased: Vivo prints a 1,802 release in 2024, and a positive
figure on a claimed cost line made the profit-and-loss signs unestablished (every P&L check went to
`component_signs_not_established`) and the swept residual a magnitude — 2024 operating profit
628,618 printed came out 593,244. It stays in Other Operating Expenses, where the total is right.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services.mapping import normalize_label

SET = pathlib.Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_line_items.json"

ALIASES = [
    ("is_pl__interest_income", "Finance income"),
    ("is_pl__income_loss_from_joint_ventures", "Share of profit of joint ventures"),
    ("is_pl__income_loss_from_related_party", "Share of result of associate"),
    ("is_pl__cost_of_sales", "Cost of operations"),
    ("is_pl__net_operating_profit", "Profit before finance costs"),
    ("is_oci__total_other_comprehensive_income", "Other comprehensive loss for the year, net of tax"),
    ("bs_cl__current_tax_payable", "Current tax liabilities"),
    ("bs_ca__current_tax_receivables", "Current tax assets"),
    ("bs_ncl__retirement_benefits_ltp", "Retirement benefit obligations"),
    ("bs_ncl__retirement_benefits_ltp", "Employee benefit obligations"),
    ("bs_nca__invest_in_jvs_and_partnerships", "Investment in joint ventures"),
    ("bs_nca__investments_in_associates", "Investment in associate"),
    ("bs_equity__common_share_capital", "Stated capital"),
    ("bs_ca__cash_in_hand_and_at_banks", "Cash at bank and in hand"),
    ("bs_ncl__ltd_bank", "Borrowings"),
    ("bs_cl__cpltd_bank", "Borrowings"),
    ("cf_oper_indirect__net_profit_loss", "Profit before income tax"),
    ("cf_oper_indirect__chg_in_inventories", "Decrease/(increase) in inventories"),
    ("cf_investing__purchase_ppe", "Payments for purchase of property, plant and equipment and intangible assets"),
    ("cf_financing__dividends_paid_fin", "Dividends paid to Company's shareholders"),
    ("is_retained__cash_div_common_shares", "Dividends declared"),
]


@pytest.fixture(scope="module")
def items():
    return {x["key"]: x for x in json.loads(SET.read_text("utf-8"))["items"]}


@pytest.mark.parametrize("key,alias", ALIASES)
def test_the_caption_is_an_alias_of_its_line_in_both_lists(items, key, alias):
    item = items[key]
    assert alias in item["aliases"]
    if "en" in (item.get("aliases_i18n") or {}):
        assert alias in item["aliases_i18n"]["en"]


@pytest.mark.parametrize("key,alias", ALIASES)
def test_no_other_line_of_the_same_section_claims_the_caption(items, key, alias):
    """One caption, one line per section: "Borrowings" is two lines' alias, one per section."""
    want = normalize_label(alias)
    scope = items[key].get("inherits")
    rivals = [k for k, x in items.items() if k != key and x.get("inherits") == scope
              and any(normalize_label(a) == want for a in x.get("aliases") or ())]
    assert rivals == []


def test_the_release_of_a_credit_loss_is_left_to_the_residual(items):
    assert "Provision for credit losses" not in items[
        "is_pl__bad_debt_expense_trade_and_other_receivables"]["aliases"]
