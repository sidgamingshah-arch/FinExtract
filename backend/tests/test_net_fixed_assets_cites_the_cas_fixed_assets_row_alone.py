"""ON A CAS BALANCE SHEET NET FIXED ASSETS CITES 固定资产 ALONE.

NEW FILE -> backend/tests/test_net_fixed_assets_cites_the_cas_fixed_assets_row_alone.py

The template computes Net Fixed Assets from the printed 固定资产 and the parts printed as lines
of their own beside it — 在建工程 (Construction in Progress) and 使用权资产 (Other Fixed Assets) —
unless the parts already add up to the printed figure. The definition told the model to cite all
three rows and take their sum, so the parts were added twice: 湖北能源 000883 published net fixed
assets of 86,104,766,163.89 against a printed 75,643,796,087.48, and non-current and total assets
over by the same 10,460,970,076.41.
"""
from __future__ import annotations

import json
import pathlib

from app.services import rollups

SEED = (pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

_FA = {"statements": [{"type": "balance_sheet", "sections": [{
    "node_id": "bs_nca", "canonical_key": "bs_nca", "label": "NCA", "role": "header", "children": [
        {"node_id": "cip", "canonical_key": "bs_nca__construction_in_progress", "label": "CIP",
         "role": "line"},
        {"node_id": "rou", "canonical_key": "bs_nca__other_fixed_assets", "label": "ROU",
         "role": "line"},
        {"node_id": "gross", "canonical_key": "bs_nca__gross_fixed_assets", "label": "Gross",
         "role": "subtotal", "rollup": {"op": "sum", "children": [
             "bs_nca__construction_in_progress", "bs_nca__other_fixed_assets"]}},
        {"node_id": "acc", "canonical_key": "bs_nca__accum_deprec_and_impairment", "label": "Acc",
         "role": "line"},
        {"node_id": "net", "canonical_key": "bs_nca__net_fixed_assets", "label": "Net",
         "role": "line", "rollup": {"op": "sum", "children": [
             "bs_nca__gross_fixed_assets", "bs_nca__accum_deprec_and_impairment"]}}]}]}]}


def _row(key, value):
    return {"canonical_key": key, "values": [{"basis": "consolidated", "period_label": "current",
                                              "value": str(value), "confidence": {"flags": []}}]}


def _net(own):
    rows = [_row("bs_nca__net_fixed_assets", own),
            _row("bs_nca__construction_in_progress", "9084760397.72"),
            _row("bs_nca__other_fixed_assets", "1376209678.69")]
    return rollups.figures_as_shown(_FA, rows, "consolidated", "current")["bs_nca__net_fixed_assets"]


def test_the_cas_clause_asks_for_the_fixed_assets_row_alone():
    items = {i["key"]: i for i in json.loads(SEED.read_text("utf-8"))["items"]}
    text = items["bs_nca__net_fixed_assets"]["definition"]
    assert "Cite 固定资产 alone" in text
    assert "Cite each of those rows as a component" not in text


def test_the_printed_row_alone_gives_the_printed_net_fixed_assets():
    assert round(_net("65182826011.07"), 2) == 75643796087.48


def test_the_sum_of_the_three_rows_counts_the_parts_twice():
    """What the old wording produced: the line's own figure already held the parts."""
    assert round(_net("75643796087.48"), 2) == 86104766163.89
