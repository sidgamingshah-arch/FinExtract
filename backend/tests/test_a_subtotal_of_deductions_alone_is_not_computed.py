"""A BALANCE-SHEET SUBTOTAL IS NOT COMPUTED FROM ITS DEDUCTIONS ALONE.

NEW FILE -> backend/tests/test_a_subtotal_of_deductions_alone_is_not_computed.py

Net Trade Receivables is a `sum` of the gross and the allowance. Once the allowance was answered on
a filing whose gross was not, the subtotal was the allowance alone: Grand Ming published -81,
PetroChina -3,068 (its face prints 71,610) and Far East Horizon -1,637,436. Where the template gives
a subtotal a base and none of its base children is disclosed, the subtotal is not computed and the
figure the filing — or the model — gives it stands. On the P&L an expense alone is a figure, and
the rule does not reach it.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services.rollups import evaluate

TEMPLATE = (pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
            / "output_csv_hk_v1_template.json")


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def _net(template, **figures):
    calc = evaluate(template, lambda key: figures.get(key))
    return calc["bs_ca__net_trade_receivables"]


def test_the_allowance_alone_is_not_the_net_receivable(template):
    net = _net(template, bs_ca__allow_for_doubtful_accounts=-81.0)
    assert not net.computable


def test_gross_less_the_allowance_is(template):
    net = _net(template, bs_ca__trade_receivables_gross=20924.0,
               bs_ca__allow_for_doubtful_accounts=-81.0)
    assert net.computable and net.value == 20843.0


def test_the_gross_alone_still_computes_as_before(template):
    net = _net(template, bs_ca__trade_receivables_gross=371948.0)
    assert net.computable and net.value == 371948.0


def test_an_expense_alone_is_still_a_pl_subtotals_figure(template):
    calc = evaluate(template, lambda key: {"is_pl__interest_expense": -5000.0}.get(key))
    net_interest = calc["is_pl__net_interest_income_expense"]
    assert net_interest.computable and net_interest.value == -5000.0
