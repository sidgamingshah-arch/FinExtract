"""A LINE'S DEFINITION NAMES THE PRINTED ROW THE TEMPLATE'S ROLLUP MAKES IT.

The definition is sent to the model verbatim, and the model chooses the row it cites from it. Four
HK definitions were boilerplate ("the subtotal the filing PRINTS as '<label>' ... where the filing
prints no subtotal, answer with an empty sources list") that contradicted the template's own
arithmetic, so a blind reader picked a row the template does not mean, or none:

  * Profit(Loss) Bef Extraord Items = Profit(Loss) Before Tax + Total Income Tax. No CAS or HKFRS
    filing prints "before extraordinary items", so the reader answered empty; the row it is, is the
    group's net profit (净利润 / Profit for the year) before the split to non-controlling interests.
  * Profit for the year = Bef Extraord Items + extraordinary items + Minority Interests(PL). That is
    the OWNERS' share, not the printed group total "Profit for the year" the definition named.
  * Total Cost of Sales sums the cost-of-sales rows; a filing that prints one row ('Direct costs')
    and no subtotal prints the total in that row, which "no subtotal -> empty" told the reader to skip.
  * Net Fixed Assets = Gross Fixed Assets + Accum Deprec, and Gross Fixed Assets takes in Other Fixed
    Assets (使用权资产) and Construction in Progress (在建工程). The definition said none of that, so a
    reader cited 固定资产 alone.

Each assertion reads the relation from the template and the set, so it holds for whichever filing
the line is asked about, and fails on the old text.

Two more were the generic "Extract the reported value for '<label>'" text, which named no row:

  * Dividends Paid(Fin). A CAS cash flow statement prints dividends and interest paid as ONE row
    (分配股利、利润或偿付利息支付的现金), and a blind reader left the line empty because that row is
    not dividends alone. The combined row is this line's figure; otherwise the dividends-paid row.
  * Notes Payable(CP). A reader could not tell whether guaranteed notes due within a year are notes
    payable. They are not: the line is bills payable and short-term notes or commercial paper, and
    bonds, guaranteed and senior notes and the current portion of long-term notes are CPLTD Other,
    the line whose aliases carry them.
"""
from __future__ import annotations

import json
import pathlib

import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SET = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
ONTOLOGY = json.loads((TEMPLATES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
TEMPLATE = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))

ITEMS = {i["key"]: i for i in SET["items"]}


def _rollups() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            key, rollup = node.get("canonical_key"), node.get("rollup")
            if isinstance(key, str) and isinstance(rollup, dict):
                out[key] = list(rollup.get("children") or [])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(TEMPLATE.get("statements"))
    return out


ROLLUPS = _rollups()
BOILERPLATE_EMPTY = "Where the filing prints no subtotal on this line"


def _definition(key: str) -> str:
    return ITEMS[key]["definition"]


def test_profit_before_extraordinary_items_is_the_group_net_profit():
    key = "is_pl__profit_loss_bef_extraord_items"
    assert ROLLUPS[key] == ["is_pl__profit_loss_before_tax", "is_pl__total_income_tax"]
    text = _definition(key)
    # After tax, before the non-controlling share: the printed group net profit.
    assert "净利润" in text and "Profit for the year" in text
    assert "non-controlling" in text
    assert BOILERPLATE_EMPTY not in text


def test_profit_for_the_year_is_the_owners_share_because_its_rollup_takes_out_minorities():
    key = "is_pl__profit_for_the_year"
    assert "is_pl__minority_interests_pl" in ROLLUPS[key]
    assert "is_pl__profit_loss_bef_extraord_items" in ROLLUPS[key]
    text = _definition(key)
    assert "owners of the parent" in text and "归属于母公司" in text
    # It says which printed row it is NOT: the group total belongs to Bef Extraord Items.
    assert ITEMS["is_pl__profit_loss_bef_extraord_items"]["label"] in text
    assert "PRINTS as 'Profit for the year'" not in text


def test_a_lone_cost_of_sales_row_is_the_total_cost_of_sales():
    key = "is_pl__total_cost_of_sales"
    assert "is_pl__cost_of_sales" in ROLLUPS[key]
    text = _definition(key)
    assert BOILERPLATE_EMPTY not in text
    assert "single cost-of-sales row and no subtotal" in text
    assert "直接成本" in ITEMS["is_pl__cost_of_sales"]["aliases_i18n"]["zh"]
    assert "直接成本" in text and "Direct costs" in text
    # Total operating costs on a CAS statement is a different, wider figure.
    assert "营业总成本" in text


def test_net_fixed_assets_names_every_class_gross_fixed_assets_takes_in():
    key = "bs_nca__net_fixed_assets"
    assert ROLLUPS[key] == ["bs_nca__gross_fixed_assets", "bs_nca__accum_deprec_and_impairment"]
    classes = ROLLUPS["bs_nca__gross_fixed_assets"]
    text = _definition(key)
    for child in ("bs_nca__other_fixed_assets", "bs_nca__construction_in_progress"):
        assert child in classes
        zh = ITEMS[child]["aliases_i18n"]["zh"]
        assert zh[0] in text, (child, zh[0])
    for caption in ITEMS["bs_nca__other_fixed_assets"]["aliases_i18n"]["zh"]:
        assert caption in text, caption


def test_a_combined_dividends_and_interest_row_is_dividends_paid():
    key = "cf_financing__dividends_paid_fin"
    combined = "分配股利、利润或偿付利息支付的现金"
    # The deterministic route already binds the combined row here; the definition now says so too.
    assert combined in ITEMS[key]["aliases_i18n"]["zh"]
    text = _definition(key)
    assert combined in text
    assert "dividends and interest paid as one row" in text and "interest included" in text
    assert "row the statement prints for dividends paid" in text
    assert "Extract the reported value for" not in text


def test_notes_payable_is_bills_and_short_term_paper_and_names_where_bonds_go():
    key = "bs_cl__notes_payable_cp"
    text = _definition(key)
    assert "Extract the reported value for" not in text
    assert "Bills payable" in text and "commercial paper" in text and "应付票据" in text
    for excluded in ("bonds", "guaranteed notes", "senior notes", "current portion of long-term notes"):
        assert excluded in text, excluded
    # It names, by its label, the line the set gives bonds and senior notes in current liabilities.
    home = ITEMS["bs_cl__cpltd_other"]
    assert {"Bonds", "Senior notes", "Notes payables"} <= set(home["aliases"])
    assert f"'{home['label']}'" in text


@pytest.mark.parametrize("key", ["bs_nca__net_fixed_assets", "cf_financing__dividends_paid_fin",
                                 "bs_cl__notes_payable_cp"])
def test_the_ontology_carries_the_same_definition(key):
    concepts = [c for c in ONTOLOGY["concepts"] if c.get("canonical_key") == key] \
        if "concepts" in ONTOLOGY else []
    if not concepts:
        found: list[dict] = []

        def walk(node) -> None:
            if isinstance(node, dict):
                if node.get("canonical_key") == key and "definition" in node:
                    found.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(ONTOLOGY)
        concepts = found
    assert concepts and all(c["definition"] == _definition(key) for c in concepts)
