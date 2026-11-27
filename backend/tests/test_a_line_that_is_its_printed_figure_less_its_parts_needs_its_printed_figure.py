"""A LINE THE TEMPLATE DEFINES AS ITS PRINTED FIGURE LESS ITS PARTS IS NOT ITS PARTS.

NEW FILE -> backend/tests/test_a_line_that_is_its_printed_figure_less_its_parts_needs_its_printed_figure.py

The fixed template's Cash Equivalents is "printed cash equivalents less pledged deposits"
(`reported_total_key` the line itself, `reported_total_op: diff`), and Cash in Hand and at Banks
is "printed cash less pledged deposits less cash equivalents". With no cash equivalents printed,
the engine summed the parts: Cash Equivalents became the pledged deposits, and Cash in Hand lost
them twice. Measured on 佳明 2025/26 (p63): pledged bank deposits 135,199, cash 24,295 printed;
the grid showed Cash Equivalents 135,199 and Cash in Hand -246,103, and total current assets
1,171,178 against 1,301,933 printed. China SCE 2023 (p103) and BYD Electronic 2024 moved the same
way, each to its printed cash.

A SUBTOTAL with the same declaration keeps the sum of its parts: China SCE prints no inventories
line, and Inventories is its properties under development and completed properties for sale.

And a balance-sheet line its parts take below zero keeps its printed figure: Far East Horizon's
current restricted deposits 1,100,401 less the non-current ones came to -8,507,714 (p174).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services.rollups import evaluate

TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_v1_template.json"


@pytest.fixture(scope="module")
def template():
    return json.loads(TEMPLATE.read_text("utf-8"))


def _calc(template, figures):
    return evaluate(template, lambda key: figures.get(key))


def test_cash_equivalents_not_printed_are_not_the_pledged_deposits(template):
    calc = _calc(template, {"bs_ca__pledged_deposits": 135199.0,
                            "bs_ca__cash_in_hand_and_at_banks": 24295.0})
    assert not calc["bs_ca__cash_equivalents"].computable
    # Cash in Hand is then its printed figure: one of its parts is unknown.
    assert not calc["bs_ca__cash_in_hand_and_at_banks"].computable


def test_kamings_current_assets_are_the_printed_total(template):
    """佳明 2025/26, p63, with the other receivables carved (see the other-receivables test)."""
    figures = {"bs_ca__inventories": 1014226.0, "bs_ca__pledged_deposits": 135199.0,
               "bs_ca__current_tax_receivables": 1474.0,
               "bs_ca__trade_and_other_receivables": 126739.0,
               "bs_ca__cash_in_hand_and_at_banks": 24295.0}
    calc = _calc(template, figures)
    assert calc["bs_ca__total_current_assets"].value == pytest.approx(1301933.0)


def test_a_subtotal_not_printed_is_still_the_sum_of_its_parts(template):
    calc = _calc(template, {"bs_ca__wip": 83747682.0, "bs_ca__finished_goods": 6253504.0})
    assert calc["bs_ca__inventories"].computable
    assert calc["bs_ca__inventories"].value == pytest.approx(90001186.0)


def test_a_printed_line_less_its_parts_still_computes(template):
    calc = _calc(template, {"bs_ca__cash_equivalents": 500.0, "bs_ca__pledged_deposits": 100.0})
    assert calc["bs_ca__cash_equivalents"].computable
    assert calc["bs_ca__cash_equivalents"].value == pytest.approx(400.0)


def test_a_balance_its_parts_take_below_zero_keeps_its_printed_figure(template):
    calc = _calc(template, {"bs_ca__pledged_deposits": 1100401.0,
                            "bs_nca__pledged_deposits_ltp": 9608115.0})
    assert not calc["bs_ca__pledged_deposits"].computable
