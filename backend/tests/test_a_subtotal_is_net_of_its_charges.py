"""Gross profit and operating profit are net of their charges whichever sign the filing printed.

`global_rules.sign_convention.expenses_and_outflows` says expenses are "Stored NEGATIVE. This is
required by the template: pl_gross_profit = sum(revenue, cost_of_goods_sold) ... only hold if
expense concepts carry a negative sign", and `unsigned_source` says a filing printing them
unsigned should be negated on load. Nothing does that negation: `normalize._negate_unsigned_expenses`
gathers a cohort of concepts declaring `negative_expected`, and the output-CSV rulebook declares
that on NONE of its 462. So a CAS filing's costs arrive positive — on 000709
`is_pl__cost_of_sales` +109,877,789,991.32 beside revenue +116,590,734,451.51 — and a flat sum made
gross profit the SUM of the two: 226,468,524,442.83 instead of 6,712,944,460.19.

Declaring each charge a MAGNITUDE answers it at the formula instead of at the figure, so the
subtotal is right whichever way the filing printed the charge and nothing has to be flipped on
load. Both declarations of the arithmetic carry it — `cost_magnitude_children` in the template,
which `services/rollups.evaluate` reads for the export, statement API and KPI path, and
`abs: true, sign: -1` on the configuration's terms, which `services/line_items.evaluate` reads.
`tests/test_formula_in_config` pins that the two agree; this file pins what they compute.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TEMPLATE = json.loads((_SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))

# 000709's consolidated income statement, as the filing prints it: costs UNSIGNED.
REVENUE = 116_590_734_451.51
COST = 109_877_789_991.32
GROSS = 6_712_944_460.19


def _evaluate(figures: dict[str, float]) -> dict:
    from app.services.rollups import evaluate

    return evaluate(TEMPLATE, lambda key: figures.get(key))


def _configured(figures: dict[str, float], key: str):
    """The same arithmetic down the CONFIGURATION's path, which the statement inspector reads."""
    from decimal import Decimal

    from app.schemas.line_items import load_line_item_set
    from app.services.line_items import evaluate as evaluate_item

    cfg = load_line_item_set(json.loads(
        (_SAMPLES / "output_csv_hk_line_items.json").read_text(encoding="utf-8")), resolve=True)
    known = {k: Decimal(str(v)) for k, v in figures.items()}
    return evaluate_item({d.key: d for d in cfg.items}[key], known)


@pytest.mark.parametrize("cost", [COST, -COST], ids=["printed unsigned", "printed negative"])
def test_gross_profit_is_revenue_less_the_cost_however_it_was_printed(cost):
    """THE ONE THE DEFECT WAS MEASURED ON. A CAS filing prints 营业成本 unsigned and an HKEX one
    prints it in brackets; the template's Gross Profit column is the same figure either way."""
    out = _evaluate({"is_pl__sales_revenues": REVENUE, "is_pl__cost_of_sales": cost})

    assert out["is_pl__total_cost_of_sales"].computable
    assert round(out["is_pl__total_cost_of_sales"].value, 2) == -COST
    assert out["is_pl__gross_profit"].computable
    assert round(out["is_pl__gross_profit"].value, 2) == GROSS


@pytest.mark.parametrize("cost", [COST, -COST], ids=["printed unsigned", "printed negative"])
def test_the_configuration_computes_the_same_gross_profit(cost):
    """Two declarations of one arithmetic have to agree on the NUMBER, not only on their terms.

    Every term supplied, because on this path they are all `role: required` — a configured
    `calculated` line does not resolve from a subset the way the template's rollup does.
    """
    figures = {"is_pl__sales_revenues": REVENUE, "is_pl__net_premium_earned": 0.0,
               "is_pl__changes_in_inventories_incr_dcr": 0.0,
               "is_pl__expenses_own_work_capitalized": 0.0,
               "is_pl__total_cost_of_sales": cost}
    got = _configured(figures, "is_pl__gross_profit")
    assert got.resolved and round(float(got.value), 2) == GROSS


@pytest.mark.parametrize("sign", [1, -1], ids=["printed unsigned", "printed negative"])
def test_operating_profit_is_net_of_every_operating_charge_however_printed(sign):
    """Four charges and two incomes, once each way round. Gross profit is NOT abs'd — a gross loss
    is a real figure — so it carries its own sign into the subtotal."""
    charges = {"is_pl__selling_and_marketing_expenses": 1_000 * sign,
               "is_pl__general_and_admin_expenses": 2_000 * sign,
               "is_pl__research_and_development": 3_000 * sign,
               "is_pl__deprec_and_impairment_oper_exp": 4_000 * sign}
    out = _evaluate({"is_pl__gross_profit": 50_000,
                     "is_pl__other_operating_income": 500,
                     "is_pl__grants_and_subsidies": 250, **charges})

    got = out["is_pl__net_operating_profit"]
    assert got.computable
    assert round(got.value, 2) == 50_000 + 500 + 250 - (1_000 + 2_000 + 3_000 + 4_000)


def test_a_gross_loss_carries_its_own_sign_into_operating_profit():
    """The counterweight to every `abs` above: `abs` on a two-sided column would publish a gross
    LOSS as a gross profit, and operating profit would be out by twice the loss."""
    out = _evaluate({"is_pl__gross_profit": -8_000, "is_pl__general_and_admin_expenses": 2_000})
    assert round(out["is_pl__net_operating_profit"].value, 2) == -10_000


def test_an_income_printed_negative_stays_negative():
    """`other_operating_income` is the income section's residual and can genuinely be a net debit.
    Nothing in this change may coerce it — the rulebook's `either` clause names exactly these:
    "Subtotals, working-capital movements, fair-value changes, share of results, OCI and net cash
    flows are sign-indeterminate. Retain the reported sign; never coerce"."""
    out = _evaluate({"is_pl__gross_profit": 10_000, "is_pl__other_operating_income": -1_500})
    assert round(out["is_pl__net_operating_profit"].value, 2) == 8_500


def test_the_charge_is_subtracted_once_not_twice():
    """The failure mode the two assembled depreciation charges first exposed: a magnitude member
    left undeclared moves the subtotal by 2× the figure, and one declared twice over would too."""
    out = _evaluate({"is_pl__gross_profit": 10_000,
                     "is_pl__deprec_and_impairment_oper_exp": 1_200})
    assert round(out["is_pl__net_operating_profit"].value, 2) == 8_800
    contributions = {c.canonical_key: c.contribution
                     for c in out["is_pl__net_operating_profit"].components}
    assert contributions["is_pl__deprec_and_impairment_oper_exp"] == -1_200


def test_a_residual_rollup_over_the_same_concept_is_not_adjusted():
    """`is_pl__other_operating_expenses` subtracts its broken-out children from its OWN printed
    total, where a child's arrival sign is the parent's. Declaring a magnitude there would deduct
    a charge the parent already contains — which is why the declaration is per rollup."""
    nodes = {}

    def walk(children):
        for node in children:
            if node.get("canonical_key"):
                nodes[node["canonical_key"]] = node
            walk(node.get("children") or ())

    for statement in TEMPLATE["statements"]:
        walk(statement.get("sections") or ())

    residual = nodes["is_pl__other_operating_expenses"]["rollup"]
    assert residual["reported_total_key"] == "is_pl__other_operating_expenses"
    assert not residual.get("cost_magnitude_children")
