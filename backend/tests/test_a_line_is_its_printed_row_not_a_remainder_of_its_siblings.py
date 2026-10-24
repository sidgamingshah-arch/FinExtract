"""A LINE IS ITS PRINTED ROW, NOT A REMAINDER OF ITS SIBLINGS — THE USER'S DECISION (r9).

The user's words: "Each line becomes the printed row, and the rollup no longer subtracts siblings."

Five lines of the HK output template carried a rollup naming THEMSELVES as the reported total with
`reported_total_op: diff`, so `services.rollups.evaluate` published the printed row LESS the listed
children — and, where the row was not printed, the children's SUM, which the section total then
added beside them:

    Dividends Paid(Fin)        printed - Dvds Paid(Minority S'holders)
    LTD Bank                   printed - 14 other non-current debt lines
    Cash Paid to Employees     printed - the four other direct-method operating rows
    Cash in Hand and at Banks  printed - Pledged Deposits - Cash Equivalents      (HELD BACK)
    Cash Equivalents           printed - Pledged Deposits                         (HELD BACK)

MEASURED at 985bc8c, answer-free: Lai Sun (hkex_0191) prints no dividends paid row, so the template
made Dividends Paid(Fin) out of its minority child (-64,141 / -61,176) and the financing total
counted it twice: -458,613 / -1,053,752 against a printed -394,472 / -992,576. PetroChina's
'Dividends paid to non-controlling interests' (-11,139 / -9,085) could not be bound, because the
residual would have taken it off the printed 'Dividends paid to owners of the Company' (-82,360).

THE TWO CASH RESIDUALS ARE HELD BACK, with their figures in the r9 commit: without them Total
Current Assets reaches print on BYD and moves closer on 佳明, but moves FURTHER on China SCE (both
years) and on Far East Horizon (current) — there Pledged Deposits' own rollup (printed less
Pledged Deposits(LTP), one of the rollups the user said to leave alone) publishes -8,507,714, which
the cash residual had been adding back. So they are pinned here as still in place.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services import rollups
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TEMPLATE = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
SEED = TEMPLATES / "output_csv_hk_line_items.json"

LANDED = ("cf_financing__dividends_paid_fin", "bs_ncl__ltd_bank",
          "cf_oper_direct__cash_paid_to_employees")
HELD_BACK = ("bs_ca__cash_in_hand_and_at_banks", "bs_ca__cash_equivalents")
DIVIDENDS, MINORITY = "cf_financing__dividends_paid_fin", "cf_financing__dvds_paid_minority_sholders"
CFF = "cf_financing__cash_flows_from_finance_activities"
OTHER_FIN = "cf_financing__other_financing_cash_flows"


def _nodes() -> dict[str, dict]:
    out: dict[str, dict] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("canonical_key"):
                out[node["canonical_key"]] = node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(TEMPLATE["statements"])
    return out


NODES = _nodes()


def _rows(**figures) -> list[dict]:
    """One printed row per key, current year, consolidated."""
    return [{"canonical_key": key, "source_label": key, "printed_in": "face",
             "values": [{"basis": "consolidated", "period_label": "current", "value": str(v),
                         "provenance": {"source_kind": "native", "page_index": 1}}]}
            for key, v in figures.items()]


def _shown(rows) -> dict[str, float]:
    return rollups.figures_as_shown(TEMPLATE, rows, "consolidated", "current")


@pytest.mark.parametrize("key", LANDED)
def test_the_line_has_no_rollup_and_is_still_a_printed_template_line(key):
    node = NODES[key]
    assert "rollup" not in node and node["role"] == "line"
    assert key not in rollups.calculated_nodes(TEMPLATE)


def test_the_template_still_prints_480_keys_and_the_section_rollups_are_unchanged():
    printed = [k for k, n in NODES.items() if n.get("role") in ("line", "subtotal", "total")]
    assert len(NODES) == 480
    cff = NODES[CFF]["rollup"]["children"]
    assert DIVIDENDS in cff and MINORITY in cff
    assert "bs_ncl__ltd_bank" in NODES["bs_ncl__total_non_current_liabilities"]["rollup"]["children"]
    assert "cf_oper_direct__cash_paid_to_employees" in \
        NODES["cf_oper_direct__cash_flows_oper_activ_direct"]["rollup"]["children"]
    assert printed


@pytest.mark.parametrize("key", HELD_BACK)
def test_the_two_cash_residuals_are_held_back(key):
    """Pinned so that landing them is a decision, not a drift — see the module docstring."""
    rollup = NODES[key]["rollup"]
    assert rollup["reported_total_key"] == key and rollup["reported_total_op"] == "diff"
    assert "bs_ca__pledged_deposits" in rollup["children"]


def test_an_unprinted_dividends_paid_is_not_made_out_of_the_minority_dividends():
    """Lai Sun 2024/25: the statement prints 'Dividends paid to non-controlling interests of
    subsidiaries' -64,141 and no dividends paid row of its own. The financing total counts the
    -64,141 once."""
    rows = _rows(**{MINORITY: -64141, "cf_financing__interest_paid_fin": -300000,
                    CFF: -364141})
    shown = _shown(rows)
    assert DIVIDENDS not in shown
    assert shown[MINORITY] == -64141
    assert shown[CFF] == -364141


def test_a_printed_dividends_paid_keeps_its_row_beside_the_minority_dividends():
    """PetroChina 2024: 'Dividends paid to owners of the Company' -82,360 and 'Dividends paid to
    non-controlling interests' -11,139 are two rows; neither is taken off the other."""
    rows = _rows(**{DIVIDENDS: -82360, MINORITY: -11139, "cf_financing__interest_paid_fin": -12760,
                    CFF: -106259})
    shown = _shown(rows)
    assert shown[DIVIDENDS] == -82360 and shown[MINORITY] == -11139
    assert shown[CFF] == -106259


#: The fourteen lines LTD Bank's residual took off its printed row at 985bc8c.
LTD_BANK_SIBLINGS_AT_985BC8C = (
    "bs_ncl__entrusted_loan_payables_third_parties_ltp", "bs_ncl__perpetual_bond", "bs_ncl__repo_ltp",
    "bs_ncl__ltd_other", "bs_ncl__ltd_convertible", "bs_ncl__ltd_subordinated",
    "bs_ncl__finance_leases_ltp", "bs_ncl__entrusted_loan_payables_related_parties_ltp",
    "bs_ncl__due_to_directors_ltp", "bs_ncl__due_to_subsidiaries_ltp",
    "bs_ncl__due_to_related_parties_ltp", "bs_ncl__entrusted_loan_payables_shareholders_ltp",
    "bs_ncl__due_to_mi_ltp", "bs_ncl__due_to_sholder_ltp")


def test_ltd_bank_is_the_printed_row_with_every_other_debt_line_printed():
    """The residual needed all fourteen siblings to fire; with them printed it took them all off the
    printed bank borrowings. Now the printed row stands and the total adds each once."""
    assert "bs_ncl__ltd_bank" not in rollups.calculated_nodes(TEMPLATE)
    debt = {k: 10 for k in LTD_BANK_SIBLINGS_AT_985BC8C}
    assert set(debt) <= set(NODES["bs_ncl__total_non_current_liabilities"]["rollup"]["children"])
    rows = _rows(**{"bs_ncl__ltd_bank": 1000, **debt})
    shown = _shown(rows)
    assert shown["bs_ncl__ltd_bank"] == 1000
    assert shown["bs_ncl__total_non_current_liabilities"] == 1000 + 10 * len(debt)


def test_cash_paid_to_employees_is_the_printed_row_beside_the_other_direct_rows():
    """With all five direct-method rows printed — borrowing costs paid among them — the residual
    published employees as the printed figure LESS the other four."""
    rows = _rows(**{"cf_oper_direct__cash_receipts_from_customers": 1000,
                    "cf_oper_direct__cash_paid_to_suppliers": -500,
                    "cf_oper_direct__cash_paid_to_employees": -200,
                    "cf_oper_direct__income_taxes_paid_direct": -50,
                    "cf_oper_direct__borrowing_costs_paid": -20})
    shown = _shown(rows)
    assert shown["cf_oper_direct__cash_paid_to_employees"] == -200
    assert shown["cf_oper_direct__cash_flows_oper_activ_direct"] == 230


def test_petrochinas_non_controlling_dividends_row_binds_the_minority_line():
    shipped = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    matcher = OntologyMatcher(build_working_view(shipped))
    res = matcher.match("Dividends paid to non-controlling interests", statement="cash_flow",
                        section="CASH FLOWS FROM FINANCING ACTIVITIES")
    assert res is not None and res.canonical_key == MINORITY
    res = matcher.match("Dividends paid to owners of the Company", statement="cash_flow",
                        section="CASH FLOWS FROM FINANCING ACTIVITIES")
    assert res is not None and res.canonical_key == DIVIDENDS
