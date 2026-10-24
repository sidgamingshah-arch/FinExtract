"""EVERY IND AS LINE SAYS WHAT IT IS, FOR THE IND AS TEMPLATE.

147 lines of `output_csv_indas_line_items.json` still carried the generic placeholder

    "Extract the reported value for 'Land' from the stated section. Do not calculate it."

after the HK set's lines were written (73e1c2b, c2e2daf). The definition is sent to the model
verbatim, so a placeholder tells it the label and nothing else. These definitions start from the HK
set's text for the same concept and are rewritten for the Ind AS template
(`output_csv_indas_v1_template.json`), which is smaller than the HK one: a peer named by label must
be a line THIS template has, a total named must be a total of THIS template's rollups, and the
captions are the ones a Schedule III Division II statement prints.

Pinned here, as properties rather than figures (no Indian filing is in the twenty captures; the Ind
AS set is read by none of them):

  * no placeholder is left, and a written definition is English (the set carries no Han), refers
    to no note (the stand-in lint's `refers_to_a_note`), names no filing, no other GAAP and no
    internal key;
  * a line the template's rollup adds into a total names that total (or a parent it is in), and a
    line that is itself a rollup or residual says that the arithmetic is the template's;
  * no definition names, in quotes, an HK line label the Ind AS template does not have — the
    model would be sent to a line that does not exist here;
  * a line's own label is not among the peers it is "beside";
  * where the set's own vocabulary places a Schedule III caption on a line, that line's definition
    names the caption, so the text and the binding say the same thing.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re

import pytest

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

BACKEND = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = BACKEND / "app" / "sample" / "templates"
SET = json.loads((TEMPLATES / "output_csv_indas_line_items.json").read_text(encoding="utf-8"))
TEMPLATE = json.loads((TEMPLATES / "output_csv_indas_v1_template.json").read_text(encoding="utf-8"))
HK_TEMPLATE = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
ITEMS = {i["key"]: i for i in SET["items"]}
PLACEHOLDER = "Extract the reported value for '"

#: The lines whose definition was the placeholder at c2e2daf, by prefix.
_WRITTEN = {
    "bs_nca": """
        land buildings leasehold_improvements plant_and_equipment furniture_and_fixtures
        vehicles_and_transport_equipment other_fixed_assets construction_in_progress
        accum_deprec_and_impairment goodwill accum_goodwill_amortized land_use_rights
        concessions_licenses_and_trdmrks patents start_up_capitalized_expenses
        r_and_d_capitalized_expenses other_intangible_assets investment_property_net
        investment_in_subsidiaries investments_in_associates invest_in_jvs_and_partnerships
        due_from_directors due_from_subsidiaries due_from_mi due_from_sholder_ltp
        other_non_current_assets deferred_tax_assets other_receivables_ltp""",
    "bs_ca": """
        raw_materials wip finished_goods short_term_securities current_tax_receivables
        prepayments_cp assets_related_to_disc_ops pledged_deposits other_current_assets
        trade_and_other_receivables trade_receivables_gross cash_equivalents
        cash_in_hand_and_at_banks""",
    "bs_equity": """
        common_share_capital pref_share_capital share_premium
        capital_and_restricted_reserves revaluation_reserves hedging_reserves other_reserves
        treasury_shares forex_translation_equity other_equity accum_oth_eqty_rsrv_inc
        minority_interest_equity""",
    "bs_ncl": """
        ltd_bank ltd_other ltd_convertible finance_leases_ltp due_to_related_parties_ltp
        lease_liabilities_ltp other_non_current_liabilities customer_advances_ltp
        deferred_income_ltp trade_payables_ltp retirement_benefits_ltp
        deferred_tax_liability other_provisions_ltp""",
    "bs_cl": """
        cpltd_bank cpltd_other st_bank_loans_payable st_other_loans_payable
        finance_leases_cp due_to_related_parties_cp lease_liabilities_cp
        other_current_liabilities customer_advances_cp deferred_income_cp
        current_tax_payable interest_payable dividends_payable
        trade_payables_related_parties trade_and_other_payables_cp trade_payables_cp
        retirement_benefits_cp tax_provision_cp other_provisions_cp""",
    "is_pl": """
        changes_in_inventories_incr_dcr cost_of_sales material_expenses
        other_operating_income personnel_and_benefit_expenses other_operating_expenses
        amort_and_impairment_intgbl interest_income interest_expense
        income_loss_from_joint_ventures current_income_taxes deferred_income_taxes_nc
        extraordinary_income_exp minority_interests_pl""",
    "is_oci": """
        unreal_gain_loss_fixed_assets unreal_gain_loss_invest_prpty
        unreal_gain_loss_investments deferred_hedging_gain_loss derivatives_fmv_adjustment
        minimum_pension_liability_adj other_equity_and_reserves_adj""",
    "is_retained": """
        cash_div_pref_shares proposed_cash_dividends cash_div_common_shares
        stock_dividends_nc transfer_to_reserves prior_period_adjustments
        other_adj_to_retained_profits""",
    "cf_oper_indirect": """
        net_profit_loss income_tax_expense depreciation amortization_and_asset_impairment
        interest_inc_received_oper interest_expense chg_in_trade_receivables
        chg_in_inventories chg_in_other_current_assets chg_in_trade_payables
        chg_in_provisions chg_in_other_current_liab income_taxes_paid_indirect
        interest_paid_oper other_non_cash_adjs_oper""",
    "cf_investing": """
        purchase_intangible_assets purchase_ppe proceeds_sale_ppe
        purchase_investment_property purchase_financial_assets proceeds_sale_fncl_assets
        investment_assoc_and_affiliates interest_income_rcvd_invest
        dividends_received_invest other_invest_cash_flows""",
    "cf_financing": """
        net_proceeds_issuance_of_capital proceeds_non_cur_borrowings
        repayments_non_cur_borrowings chg_in_current_borrowings interest_paid_fin
        dividends_paid_fin other_financing_cash_flows net_foreign_exchange_difference
        beg_of_period_cash_and_cash_equiv""",
}
WRITTEN = sorted(f"{prefix}__{rest}" for prefix, body in _WRITTEN.items() for rest in body.split())

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
#: The reference corpus by every name a definition could carry for it, the two Indian filings of
#: the twenty captures, and the other GAAPs: an Ind AS definition is not an HKFRS or CAS one.
FOREIGN = re.compile(
    r"PetroChina|China SCE|Grand Ming|Kaming|\bBYD\b|Far East Horizon|Hon Kwok|Lai Sun|Montage|"
    r"Hesteel|Maijie|Kaishan|Zhongshan|Hubei Energy|Crystal Optech|Changjiang|Meixin|Sun Create|"
    r"Hyperstrong|\bhkex\b|Asian Paints|Laurus|HKFRS|\bCAS\b|\bPRC\b|Hong Kong", re.I)


def _nodes(template: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("canonical_key"), str):
                out[node["canonical_key"]] = node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(template.get("statements"))
    return out


NODES = _nodes(TEMPLATE)
LABELS = {n["label"] for n in NODES.values()} | {i["label"] for i in SET["items"]}
HK_LABELS = {n["label"] for n in _nodes(HK_TEMPLATE).values()}


def _residual(key: str) -> bool:
    r = NODES.get(key, {}).get("rollup") or {}
    return r.get("reported_total_key") == key and r.get("reported_total_op") == "diff"


def _parents(key: str) -> tuple[list[str], list[str]]:
    """(summing parents, every parent) among this template's rollups."""
    summing, every = [], []
    for parent, node in NODES.items():
        r = node.get("rollup") or {}
        if key in (r.get("children") or []):
            every.append(parent)
            if not _residual(parent) and r.get("op", "sum") == "sum":
                summing.append(parent)
    return summing, every


def _refers_to_a_note(text: str) -> bool:
    path = BACKEND / "scripts" / "llm_standin.py"
    spec = importlib.util.spec_from_file_location("llm_standin_every_indas_def", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.refers_to_a_note(text)


def test_the_written_lines_are_lines_of_the_ind_as_template():
    assert len(WRITTEN) == 147 == len(set(WRITTEN))
    assert all(k in ITEMS and k in NODES for k in WRITTEN)


def test_no_ind_as_line_is_defined_by_the_placeholder():
    left = [i["key"] for i in SET["items"]
            if (i.get("definition") or "").startswith("Extract the reported value")]
    assert not left, (len(left), left[:10])


@pytest.mark.parametrize("key", WRITTEN)
def test_a_written_definition_is_ind_as_text_without_a_note_a_filing_or_a_key(key):
    text = ITEMS[key]["definition"]
    assert PLACEHOLDER not in text and len(text) >= 200
    assert not _HAN.search(text)
    assert not _refers_to_a_note(text)
    assert not FOREIGN.search(text), FOREIGN.search(text).group(0)
    assert not re.search(r"\b[a-z_]+__[a-z0-9_]+\b", text)


@pytest.mark.parametrize("key", WRITTEN)
def test_a_part_names_a_total_of_this_template(key):
    summing, every = _parents(key)
    if not summing:
        pytest.skip("no summing parent: a residual's member or a top-level line")
    text = ITEMS[key]["definition"]
    labels = [NODES[p]["label"] for p in every]
    assert any(label in text for label in labels), (key, labels)


@pytest.mark.parametrize("key", [k for k in WRITTEN if NODES[k].get("rollup")])
def test_a_total_says_its_arithmetic_is_the_templates(key):
    text = ITEMS[key]["definition"]
    assert "template" in text and "rollup" in text, key


@pytest.mark.parametrize("key", WRITTEN)
def test_no_peer_is_an_hk_line_this_template_lacks(key):
    quoted = re.findall(r"'([^']+)'", ITEMS[key]["definition"])
    dangling = sorted({q for q in quoted if q in HK_LABELS and q not in LABELS})
    assert not dangling, (key, dangling)


@pytest.mark.parametrize("key", WRITTEN)
def test_a_line_is_not_beside_itself(key):
    item = ITEMS[key]
    for beside in re.findall(r"\bbeside ([^.;:]*)", item["definition"]):
        assert not re.search(r"(?:^|, | and )" + re.escape(item["label"]) + r"(?=,| and |$)",
                             beside), (key, beside)


BS, PL = "balance_sheet", "profit_and_loss"

#: Schedule III captions the set's own aliases place on a written line, and the line.
PLACED = [
    ("Property, Plant and Equipment", BS, "bs_nca", "bs_nca__plant_and_equipment"),
    ("Capital work-in-progress", BS, "bs_nca", "bs_nca__construction_in_progress"),
    ("Right-of-use assets", BS, "bs_nca", "bs_nca__land_use_rights"),
    ("Investment Property", BS, "bs_nca", "bs_nca__investment_property_net"),
    ("Deferred tax assets (net)", BS, "bs_nca", "bs_nca__deferred_tax_assets"),
    ("Other non-current assets", BS, "bs_nca", "bs_nca__other_non_current_assets"),
    ("Current Tax Assets (Net)", BS, "bs_ca", "bs_ca__current_tax_receivables"),
    ("Cash and cash equivalents", BS, "bs_ca", "bs_ca__cash_in_hand_and_at_banks"),
    ("Bank balances other than cash and cash equivalents", BS, "bs_ca",
     "bs_ca__pledged_deposits"),
    ("Other current assets", BS, "bs_ca", "bs_ca__other_current_assets"),
    ("Assets classified as held for sale", BS, "bs_ca", "bs_ca__assets_related_to_disc_ops"),
    ("Equity share capital", BS, "bs_equity", "bs_equity__common_share_capital"),
    ("Other Equity", BS, "bs_equity", "bs_equity__other_equity"),
    ("Securities premium", BS, "bs_equity", "bs_equity__share_premium"),
    ("General reserve", BS, "bs_equity", "bs_equity__capital_and_restricted_reserves"),
    ("Non-controlling interests", BS, "bs_equity", "bs_equity__minority_interest_equity"),
    ("Lease liabilities", BS, "bs_ncl", "bs_ncl__lease_liabilities_ltp"),
    ("Deferred tax liabilities (net)", BS, "bs_ncl", "bs_ncl__deferred_tax_liability"),
    ("Other non-current liabilities", BS, "bs_ncl", "bs_ncl__other_non_current_liabilities"),
    ("Total outstanding dues of creditors other than micro enterprises and small enterprises",
     BS, "bs_cl", "bs_cl__trade_payables_cp"),
    ("Other current liabilities", BS, "bs_cl", "bs_cl__other_current_liabilities"),
    ("Current Tax Liabilities (Net)", BS, "bs_cl", "bs_cl__current_tax_payable"),
    ("Contract liabilities", BS, "bs_cl", "bs_cl__customer_advances_cp"),
    ("Cost of materials consumed", PL, None, "is_pl__material_expenses"),
    ("Purchases of stock-in-trade", PL, None, "is_pl__cost_of_sales"),
    ("Changes in inventories of finished goods, work-in-progress and stock-in-trade", PL, None,
     "is_pl__changes_in_inventories_incr_dcr"),
    ("Employee benefits expense", PL, None, "is_pl__personnel_and_benefit_expenses"),
    ("Finance costs", PL, None, "is_pl__interest_expense"),
    ("Other expenses", PL, None, "is_pl__other_operating_expenses"),
    ("Exceptional items", PL, None, "is_pl__extraordinary_income_exp"),
    ("Current tax", PL, None, "is_pl__current_income_taxes"),
    ("Deferred tax", PL, None, "is_pl__deferred_income_taxes_nc"),
    ("Share of profit of associates and joint ventures", PL, None,
     "is_pl__income_loss_from_joint_ventures"),
    ("Non-controlling interests", PL, None, "is_pl__minority_interests_pl"),
]


@pytest.fixture(scope="module")
def matcher():
    indas = load_line_item_set(SET, resolve=True)
    return OntologyMatcher(build_working_view(indas), locale="en", settings=get_settings())


@pytest.mark.parametrize("caption, statement, section, key", PLACED)
def test_a_caption_the_set_places_on_a_line_is_named_in_its_definition(
        matcher, caption, statement, section, key):
    assert key in WRITTEN
    assert matcher.match(caption, statement, section).canonical_key == key, caption
    assert caption.lower() in ITEMS[key]["definition"].lower(), (key, caption)
