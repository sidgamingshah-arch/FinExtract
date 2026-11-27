"""THE CASH-FLOW TOTALS AND THE IFRS WORDINGS THE MAURITIAN FILINGS PRINT BIND THEIR LINES.

Three printed cash-flow totals bound no line, so each became an unclassified review card, and the
section's rollup and reconciliation were skipped as `target_not_extracted` / `no_reported_subtotal`
— a missed row would have gone unflagged:

  * "Net cash flows generated from operating activities"        Gamma Civic p93, Lux Island p127
  * "NET CASH FLOWS (USED IN) /GENERATED FROM INVESTING ACTIVITIES"  Gamma Civic p94
  * "Net cash generated from/(used in) financing activities"      Phoenix Beverages p131

Each total now carries the "generated from" and "(used in)/generated from" wordings for its own
activity. "Net cash used in/generated from investing activities" was an alias of Other Invest Cash
Flows, the section's REMAINDER, whose definition says "Not this line: the net cash flow from
investing activities"; it is now an alias of the total.

And four component wordings fell to residual lines (the totals were right, the lines wrong):
"Dividend Paid to Owners of the Company" (Gamma p94, 266,500,000; the plural bound), "Share of
Profit of Associates and Joint Ventures" on the cash flow (Gamma p93, (169,957,617)), "Loss/(Profit)
on Disposal of Property, Plant and Equipment" (Gamma p93, 103,714) and "Marketing, warehousing,
selling and distribution expenses" (Phoenix p129, (1,749,034)).
"""
from __future__ import annotations

import pytest

from tests.face_row_harness import face, row, run


def _bound(caption: str, statement: str) -> str | None:
    rows = [row(caption, "-386505", "-368413", flags=(f"printed_on:{statement}",))]
    doc, _ = run(rows, [face(statement)], residual=False)
    return doc.line_items[0].canonical_key


@pytest.mark.parametrize("caption, key", [
    ("Net cash flows generated from operating activities",
     "cf_oper_indirect__cash_flows_oper_activ_indirect"),
    ("NET CASH FLOWS (USED IN) /GENERATED FROM INVESTING ACTIVITIES",
     "cf_investing__cash_flows_from_invest_activities"),
    ("Net cash generated from/(used in) financing activities",
     "cf_financing__cash_flows_from_finance_activities"),
    ("Net cash (used in)/generated from investing activities",
     "cf_investing__cash_flows_from_invest_activities"),
    ("Net cash used in/generated from investing activities",
     "cf_investing__cash_flows_from_invest_activities"),
    ("Net cash generated from financing activities",
     "cf_financing__cash_flows_from_finance_activities"),
    # unchanged: the wordings the totals already had
    ("Net cash used in operating activities", "cf_oper_indirect__cash_flows_oper_activ_indirect"),
    ("Net cash used in investing activities", "cf_investing__cash_flows_from_invest_activities"),
])
def test_a_printed_cash_flow_total_binds_its_activitys_total(caption, key):
    assert _bound(caption, "cash_flow") == key


@pytest.mark.parametrize("caption, statement, key", [
    ("Dividend Paid to Owners of the Company", "cash_flow", "cf_financing__dividends_paid_fin"),
    ("Share of Profit of Associates and Joint Ventures", "cash_flow",
     "cf_oper_indirect__assoc_profit_share_net_of_dvds"),
    ("Loss/(Profit) on Disposal of Property, Plant and Equipment", "cash_flow",
     "cf_oper_indirect__cash_flow_disp_fixed_assets"),
    ("Marketing, warehousing, selling and distribution expenses", "profit_and_loss",
     "is_pl__selling_and_marketing_expenses"),
    # the P&L share of associates and JVs keeps its P&L line
    ("Share of Profit of Associates and Joint Ventures", "profit_and_loss",
     "is_pl__income_loss_from_related_party"),
])
def test_an_ifrs_component_wording_binds_its_line(caption, statement, key):
    assert _bound(caption, statement) == key


def test_the_investing_remainder_no_longer_carries_the_totals_wording():
    import json
    import pathlib

    templates = pathlib.Path(__file__).resolve().parents[1] / "app/sample/templates"
    for name, field, key_of in (("output_csv_hk_line_items.json", "items", "key"),
                                ("output_csv_hk_ontology.json", "mappings", "canonical_key")):
        data = json.loads((templates / name).read_text(encoding="utf-8"))
        node = next(x for x in data[field] if x[key_of] == "cf_investing__other_invest_cash_flows")
        assert not any("investing activities" in a.lower() for a in node.get("aliases") or []), name
