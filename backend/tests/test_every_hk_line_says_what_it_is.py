"""EVERY HK LINE SAYS WHAT IT IS: NO DEFINITION IS "EXTRACT THE REPORTED VALUE FOR '<LABEL>'".

342 template lines of the HK set (and the same 342 concepts of the HK ontology) still carried the
generic placeholder after the four borrowing lines were written:

    "Extract the reported value for 'Short Term Securities' from the stated section. Do not
     calculate it."

The definition is sent to the model verbatim and is the fallback note-selection probe
(`line_item_notes._blended`), so a placeholder tells the model the label and nothing else: which
rows the line takes, which peer takes the rows it does not, and what the template's arithmetic
does with it. None of the 342 caused a wrong answer in the last round, which is why this file pins
properties rather than figures:

  * no placeholder is left, in the set or in the ontology entry for the same concept, and the two
    carry the same text;
  * a written definition refers to no note (the stand-in lint's `refers_to_a_note`) and names no
    filing of the reference corpus — a definition is the line's meaning, not a memory of one
    company's face;
  * a line the template's rollup adds into a total names that total (or the parent it is a part
    of), and a line that is itself a rollup or residual says that the arithmetic is the template's;
  * LTD Bank is the row the balance sheet prints. Until the user's r9 decision ("Each line becomes
    the printed row, and the rollup no longer subtracts siblings") its rollup was a residual — the
    printed figure less the debt lines with a figure of their own. Now nothing is taken off it: where
    one row prints bank and other borrowings together, the line cites that row as printed and is
    the whole of it, and LTD Other takes only a row of its own, never the other loans split out of
    that row — which would count them twice in Total Non-Current Liabilities.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = BACKEND / "app" / "sample" / "templates"
SET = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
ONTOLOGY = json.loads((TEMPLATES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
TEMPLATE = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
ITEMS = {i["key"]: i for i in SET["items"]}
CONCEPTS = {m["canonical_key"]: m for m in ONTOLOGY["mappings"]}
PLACEHOLDER = "Extract the reported value for '"

#: The lines whose definition was the placeholder at 12be7b2, by prefix.
_WRITTEN = {
    "bs_ca": """
        assets_plgd_under_securitization assets_related_to_disc_ops cash_equivalents
        cash_in_hand_and_at_banks commodities_ca current_tax_receivables deriv_and_hedg_assets_cp
        due_from_jvs_and_partnerships due_from_sholder_cp
        entrusted_loan_receivables_related_parties_cp entrusted_loan_receivables_shareholders_cp
        entrusted_loan_receivables_third_parties_cp finished_goods forwards_ca futures_ca
        operating_lease_receivables options_ca other_current_assets pledged_deposits
        pledged_secur_and_other_fincl_assets_cp prepaid_land_lease_payments_cp prepayments_cp
        raw_materials reverse_repo_cp segregated_account_ca short_term_securities
        subscribed_capital_called_not_paid suspense_account_ca swaps_ca trade_and_other_receivables
        trade_and_other_receivables_related_parties trade_receivables_gross
        trade_receivables_related_parties wealth_management_products_cp_from_cash_equ
        wealth_management_products_cp_from_otherasset wealth_management_products_cp_from_receivables
        wealth_management_products_cp_from_secur_and_fincl wip""",
    "bs_cl": """
        billings_in_excess_of_costs commodities_cl cpltd_convertible cpltd_subordinated
        current_tax_payable customer_advances_cp decomm_and_envir_costs_cp deferred_income_cp
        deriv_and_hedg_liab_cp dividends_payable due_to_directors_cp due_to_mi_cp
        due_to_related_parties_cp due_to_sholder_cp due_to_subsidiaries_cp
        entrusted_loan_payables_related_parties_cp entrusted_loan_payables_shareholders_cp
        entrusted_loan_payables_third_parties_cp finance_leases_cp forwards_cl futures_cl
        interest_payable ipo_loans_st_bank_loans_pay ipo_loans_st_other_loans_pay
        lease_liabilities_cp liabilities_to_enterprises options_cl other_current_liabilities
        other_provisions_cp overdrafts proposed_dividends repo_cp restruct_provision_cp
        retirement_benefits_cp segregated_account_cl share_option_costs_cp
        soc_sec_and_other_taxes_cp suspense_account_cl swaps_cl tax_provision_cp
        trade_and_other_payables_cp trade_and_other_payables_related_parties trade_financing
        trade_payables_cp trade_payables_related_parties""",
    "bs_equity": """
        accum_oth_eqty_rsrv_inc auditor_adj_on_retained_profits capital_and_restricted_reserves
        common_share_capital forex_translation_equity hedging_reserves minority_interest_equity
        other_equity other_reserves policyholders_equity pref_share_capital revaluation_reserves
        share_premium special_items_with_equity_character subordinated_debt_equity
        subordinated_debt_from_related_parties treasury_shares""",
    "bs_nca": """
        accum_deprec_and_impairment accum_goodwill_amortized buildings commodities_nca
        concessions_licenses_and_trdmrks construction_in_progress deferred_tax_assets
        deriv_and_hedg_assets_ltp due_from_directors due_from_mi due_from_sholder_ltp
        due_from_subsidiaries entrusted_loan_receivables_related_parties_ltp
        entrusted_loan_receivables_shareholders_ltp entrusted_loan_receivables_third_parties_ltp
        forwards_nca furniture_and_fixtures futures_nca goodwill invest_in_jvs_and_partnerships
        investment_in_subsidiaries investment_property_net investments_in_associates land
        land_use_rights leasehold_improvements options_nca other_fixed_assets
        other_intangible_assets other_non_current_assets other_receivables_ltp patents
        plant_and_equipment pledged_deposits_ltp pledged_secur_and_other_fincl_assets_ltp
        prepaid_land_lease_payments_ltp prepayments_ltp r_and_d_capitalized_expenses restricted_cash
        reverse_repo_ltp rtrmnt_plan_actuarial_surplus start_up_capitalized_expenses swaps_nca
        vehicles_and_transport_equipment wealth_management_products_ltp_from_otherasset
        wealth_management_products_ltp_from_receivables
        wealth_management_products_ltp_from_secur_and_fincl""",
    "bs_ncl": """
        commodities_ncl customer_advances_ltp decomm_and_envir_costs_ltp deferred_income_ltp
        deferred_tax_liability deriv_and_hedg_liab_ltp due_to_directors_ltp due_to_mi_ltp
        due_to_related_parties_ltp due_to_sholder_ltp due_to_subsidiaries_ltp
        entrusted_loan_payables_related_parties_ltp entrusted_loan_payables_shareholders_ltp
        entrusted_loan_payables_third_parties_ltp finance_leases_ltp forwards_ncl futures_ncl
        lease_liabilities_ltp ltd_bank ltd_convertible ltd_other ltd_subordinated options_ncl
        other_non_current_liabilities other_provisions_ltp perpetual_bond repo_ltp
        restruct_provision_ltp retirement_benefits_ltp share_option_costs_ltp
        soc_sec_and_other_taxes_ltp swaps_ncl tax_provision_ltp trade_and_other_payables_ltp
        trade_payables_ltp""",
    "cf_financing": """
        beg_of_period_cash_and_cash_equiv cash_flow_extraord_items_fin cash_flow_from_hedging_fin
        chg_in_current_borrowings dvds_paid_minority_sholders interest_paid_fin
        issue_costs_non_cur_borrowings net_foreign_exchange_difference
        net_proceeds_issuance_of_capital other_financing_cash_flows proceeds_non_cur_borrowings
        purchase_sale_treasury_stock repayments_non_cur_borrowings translation_adj_relating_to_cash""",
    "cf_investing": """
        acq_of_sub_net_of_cash_acqrd cash_flow_extraord_items_invest cash_flow_hedging_invest
        dividends_received_invest interest_income_rcvd_invest investment_assoc_and_affiliates
        other_invest_cash_flows payment_fin_lease_liab proceeds_sale_fncl_assets
        proceeds_sale_intgbl_assets proceeds_sale_investment_prop proceeds_sale_of_subsidiary
        proceeds_sale_other_assets proceeds_sale_ppe purchase_financial_assets
        purchase_intangible_assets purchase_investment_property purchase_other_assets purchase_ppe
        sale_assoc_and_affiliates""",
    "cf_oper_direct": """
        borrowing_costs_paid cash_paid_to_employees cash_paid_to_suppliers
        cash_receipts_from_customers income_taxes_paid_direct""",
    "cf_oper_indirect": """
        amortization_and_asset_impairment assoc_profit_share_net_of_dvds
        cash_flow_disp_financial_assets cash_flow_disp_fixed_assets cash_flow_extraord_items_oper
        cash_flow_from_hedging_oper chg_in_deferred_taxes chg_in_fncl_instruments_trading
        chg_in_inventories chg_in_other_current_assets chg_in_other_current_liab
        chg_in_other_receivables chg_in_other_wkng_capital_items chg_in_post_employ_bnft_oblig
        chg_in_prepayments_and_def_assets chg_in_provisions chg_in_taxes_payable
        chg_in_trade_payables chg_in_trade_receivables depreciation dividends_paid_oper
        dividends_received_oper income_tax_expense income_taxes_paid_indirect interest_expense
        interest_inc_received_oper interest_paid_oper minority_interests_cf net_profit_loss
        other_after_tax_income_expense other_non_cash_adjs_oper""",
    "is_oci": """
        deferred_hedging_gain_loss derivatives_fmv_adjustment minimum_pension_liability_adj
        other_equity_and_reserves_adj unreal_gain_loss_fixed_assets unreal_gain_loss_invest_prpty
        unreal_gain_loss_investments""",
    "is_pl": """
        adjsts_to_scrtzn_programs amort_and_impairment_intgbl amort_and_impairment_intgbl_cos
        asset_impairment_loss bad_debt_expense bad_debt_expense_other_receivables
        bad_debt_expense_trade_and_other_receivables bank_charge_and_interest_expense
        capitalized_costs changes_in_inventories_incr_dcr commission_expenses_cost_of_sales
        commission_expenses_other_operating_exp commission_income_other_operating_inc
        commission_income_sales cost_of_sales current_income_taxes deferred_income_taxes_nc
        deferred_interest_expense derivatives_cos exchange_gain_loss_sale_of_bus
        expenses_own_work_capitalized extraordinary_income_exp extraordinary_income_exp_nc
        finance_cost_on_derivatives fmv_adj_on_derivatives fmv_adj_on_fncl_assets
        fmv_adj_on_investment_properties fmv_adj_on_wealth_management_products
        fncl_income_from_derivatives fncl_income_from_wealth_management_products
        gain_loss_disp_of_disc_ops gain_loss_disp_of_fixed_assets gain_loss_on_derivatives
        gain_loss_on_fncl_assets gain_loss_on_invest_properties
        gain_loss_on_wealth_management_products general_and_admin_expenses goods_and_services
        goodwill_amortization grants_and_subsidies impairment_fixed_assets_oper_exp
        impairment_intgbl income_loss_from_joint_ventures income_loss_from_related_party
        interest_expense interest_income interest_income_from_derivatives
        interest_income_from_entrusted_loan_receivables
        interest_income_from_wealth_management_products interest_on_capital_market_instruments
        interest_on_debt_market_instruments interest_on_lease_liabilities interest_on_trade_finance
        interest_paid_to_related_parties material_expenses minority_interests_pl
        net_claims_and_benefits net_premium_earned officers_compensation
        operating_lease_and_rent_expenses other_debt_service_expenses other_fncl_asset_income_dvds
        other_non_oper_inc_exp_nc other_non_operating_inc_exp other_oper_exp_provisions
        other_operating_expenses other_operating_income other_taxes prior_year_tax_adj
        prov_for_retirement_costs rents_and_royalty_income research_and_development
        restructuring_costs rlzd_forex_translation_g_l selling_and_marketing_expenses
        share_option_costs""",
    "is_retained": """
        cash_div_common_shares cash_div_pref_shares other_adj_to_retained_profits
        prior_period_adjustments proposed_cash_dividends stock_dividends_nc transfer_to_reserves""",
}
WRITTEN = sorted(f"{prefix}__{rest}" for prefix, body in _WRITTEN.items() for rest in body.split())

#: The reference corpus, by every name a definition could carry for it.
FILINGS = re.compile(
    r"PetroChina|China SCE|Grand Ming|佳明|Kaming|\bBYD\b|Far East Horizon|Hon Kwok|Lai Sun|Montage|"
    r"澜起|Hesteel|河钢|Maijie|Kaishan|Zhongshan|Hubei Energy|Crystal Optech|Changjiang|Meixin|"
    r"Sun Create|Hyperstrong|\bhkex\b", re.I)


def _rollups() -> dict[str, dict]:
    out: dict[str, dict] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            key, rollup = node.get("canonical_key"), node.get("rollup")
            if isinstance(key, str) and isinstance(rollup, dict):
                out[key] = rollup
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(TEMPLATE.get("statements"))
    return out


ROLLUPS = _rollups()


def _residual(key: str) -> bool:
    r = ROLLUPS.get(key) or {}
    return r.get("reported_total_key") == key and r.get("reported_total_op") == "diff"


def _parents(key: str) -> tuple[list[str], list[str]]:
    """(summing parents, every parent): the template rollups and the set cascades naming `key`."""
    summing, every = [], []
    for parent, r in ROLLUPS.items():
        if key in (r.get("children") or []):
            every.append(parent)
            if not _residual(parent) and r.get("op", "sum") == "sum":
                summing.append(parent)
    for parent, item in ITEMS.items():
        for rung in item.get("cascade") or []:
            if any(t.get("ref") == key for t in rung.get("terms") or []):
                every.append(parent)
                summing.append(parent)
    return summing, every


def _refers_to_a_note(text: str) -> bool:
    path = BACKEND / "scripts" / "llm_standin.py"
    spec = importlib.util.spec_from_file_location("llm_standin_every_def", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.refers_to_a_note(text)


def test_the_written_lines_are_template_lines_of_the_set():
    assert len(WRITTEN) == 342 == len(set(WRITTEN))
    assert all(ITEMS[k]["namespace"] == "template" and k in CONCEPTS for k in WRITTEN)


def test_no_hk_line_is_defined_by_the_placeholder():
    left = [i["key"] for i in SET["items"] if (i.get("definition") or "").startswith(PLACEHOLDER)]
    assert not left, (len(left), left[:10])


@pytest.mark.parametrize("key", WRITTEN)
def test_the_ontology_carries_the_set_definition(key):
    assert CONCEPTS[key]["definition"] == ITEMS[key]["definition"]
    assert not CONCEPTS[key]["definition"].startswith("Extract the reported value")


@pytest.mark.parametrize("key", WRITTEN)
def test_a_written_definition_says_what_the_line_is_without_a_note_or_a_filing(key):
    text = ITEMS[key]["definition"]
    assert PLACEHOLDER not in text and len(text) >= 200
    assert not _refers_to_a_note(text)
    assert not FILINGS.search(text), FILINGS.search(text).group(0)
    # No internal key: the model reads labels.
    assert not re.search(r"\b[a-z_]+__[a-z0-9_]+\b", text)


@pytest.mark.parametrize("key", WRITTEN)
def test_a_part_names_the_total_it_is_in(key):
    summing, every = _parents(key)
    if not summing:
        pytest.skip("no summing parent: a residual's member or a top-level line")
    text = ITEMS[key]["definition"]
    labels = [ITEMS[p]["label"] for p in every]
    assert any(label in text for label in labels), (key, labels)


@pytest.mark.parametrize("key", [k for k in WRITTEN if k in ROLLUPS])
def test_a_total_says_its_arithmetic_is_the_templates(key):
    text = ITEMS[key]["definition"]
    assert "template" in text, key
    if _residual(key):
        # A residual's figure is its printed row less the children with a figure of their own:
        # the definition says the rollup does that, so the model cites the row as printed.
        assert "rollup" in text, key


def test_ltd_bank_is_a_combined_row_as_printed_and_ltd_other_takes_only_a_row_of_its_own():
    """THE USER'S DECISION (r9): "Each line becomes the printed row, and the rollup no longer
    subtracts siblings." LTD Bank is no longer a residual of the other debt lines, so a combined
    bank-and-other borrowings row is LTD Bank WHOLE — and LTD Other must not also take the other
    loans out of it, or Total Non-Current Liabilities counts them twice."""
    key = "bs_ncl__ltd_bank"
    assert key not in ROLLUPS and not _residual(key)
    text = ITEMS[key]["definition"]
    assert "Interest-bearing bank and other borrowings" in text
    assert "cite that row as printed" in text and "nothing is taken off it" in text
    assert "the whole row is this line" in text and "never a part split out of" in text
    assert "takes the debt lines that have a figure of their own out of this line" not in text
    assert "bank loans part only" not in text
    assert "'长期借款' as printed" in text or "长期借款 as printed" in text
    for peer in ("bs_ncl__ltd_other", "bs_ncl__ltd_convertible", "bs_ncl__ltd_subordinated",
                 "bs_ncl__perpetual_bond", "bs_cl__cpltd_bank"):
        assert ITEMS[peer]["label"] in text, peer
    other = ITEMS["bs_ncl__ltd_other"]["definition"]
    assert "this line is the other loans part of it" not in other
    assert "takes nothing out of it" in other and ITEMS[key]["label"] in other
    # Both parts now name the total they are added into, beside each other.
    for k in (key, "bs_ncl__ltd_other"):
        assert "Total Non-Current Liabilities" in ITEMS[k]["definition"], k
        assert CONCEPTS[k]["definition"] == ITEMS[k]["definition"], k


#: The lines whose figure the template's own residual used to reduce or make up, and which the
#: user's r9 decision makes the printed row (`reported_total_key` naming themselves, op `diff`).
_NO_LONGER_RESIDUAL = ("cf_financing__dividends_paid_fin", "bs_ncl__ltd_bank",
                       "cf_oper_direct__cash_paid_to_employees")


@pytest.mark.parametrize("key", _NO_LONGER_RESIDUAL)
def test_a_line_that_is_no_longer_a_residual_says_it_is_the_printed_row(key):
    """THE USER'S DECISION (r9): the line is its printed row; its definition no longer says the
    template's rollup adjusts it, and the ontology carries the same text."""
    assert key not in ROLLUPS
    text = ITEMS[key]["definition"]
    assert "as printed" in text
    for gone in ("rollup takes the debt lines", "whatever adjustment this template's rollup",
                 "rollup deducts"):
        assert gone not in text, (key, gone)
    assert CONCEPTS[key]["definition"] == text


def test_minority_dividends_are_a_row_of_their_own_added_beside_dividends_paid():
    """THE USER'S DECISION (r9). Dividends Paid(Fin) no longer deducts Dvds Paid(Minority S'holders),
    so a minority-dividends figure taken from an 'of which' row inside the combined CAS row would be
    counted twice in the financing total. The line takes only a row of its own."""
    key = "cf_financing__dvds_paid_minority_sholders"
    text = ITEMS[key]["definition"]
    assert "rollup deducts" not in text and "This template's rollup" not in text
    assert "as a row of its own" in text and "is not this line's figure" in text
    assert "其中：子公司支付给少数股东的股利、利润" in text
    assert ITEMS["cf_financing__dividends_paid_fin"]["label"] in text
    assert "Cash Flows from Finance Activities" in text
    dividends = ITEMS["cf_financing__dividends_paid_fin"]["definition"]
    assert "never made up from the non-controlling dividends" in dividends
    assert ITEMS[key]["label"] in dividends
    assert CONCEPTS[key]["definition"] == text


def test_the_operating_residual_names_where_taxes_and_surcharges_go_not_a_reader_defect():
    text = ITEMS["is_pl__other_operating_expenses"]["definition"]
    assert "税金及附加" in text
    assert ITEMS["is_pl__other_non_income_related_taxes"]["label"] in text
    # The glued-caption defect on two CAS faces is the face reader's to fix, not this line's text.
    assert "blank captions" not in text and "分保费用" not in text


#: The face lines whose "beside ..." sentence listed their own label among their peers at c2e2daf:
#: the eight fixed-asset classes, the seven intangible kinds and the ten non-current derivative
#: lines ("It is one of the lines this template's rollup adds into Gross Fixed Assets, beside Land,
#: Buildings, ..." on the definition of Land itself).
_BESIDE_THEMSELVES = """
    bs_nca__land bs_nca__buildings bs_nca__leasehold_improvements bs_nca__plant_and_equipment
    bs_nca__furniture_and_fixtures bs_nca__vehicles_and_transport_equipment
    bs_nca__other_fixed_assets bs_nca__construction_in_progress bs_nca__goodwill
    bs_nca__land_use_rights bs_nca__concessions_licenses_and_trdmrks bs_nca__patents
    bs_nca__start_up_capitalized_expenses bs_nca__r_and_d_capitalized_expenses
    bs_nca__other_intangible_assets bs_nca__futures_nca bs_nca__options_nca bs_nca__forwards_nca
    bs_nca__commodities_nca bs_nca__swaps_nca bs_ncl__futures_ncl bs_ncl__options_ncl
    bs_ncl__forwards_ncl bs_ncl__commodities_ncl bs_ncl__swaps_ncl""".split()


def _beside_itself(item: dict) -> list[str]:
    hits = []
    for beside in re.findall(r"\bbeside ([^.;:]*)", item.get("definition") or ""):
        if re.search(r"(?:^|, | and )" + re.escape(item["label"]) + r"(?=,| and |$)", beside):
            hits.append(beside)
    return hits


def test_no_hk_line_is_beside_itself():
    """A line is one of a total's parts BESIDE its peers; listing its own label among them reads as
    a second line of the same name."""
    offenders = {i["key"]: _beside_itself(i) for i in SET["items"] if _beside_itself(i)}
    assert not offenders, offenders


@pytest.mark.parametrize("key", _BESIDE_THEMSELVES)
def test_a_part_that_was_beside_itself_still_names_its_total_and_its_peers(key):
    text = ITEMS[key]["definition"]
    assert CONCEPTS[key]["definition"] == text
    assert re.search(r"\bbeside [^.]*\b(?:and)\b", text), key
    summing, every = _parents(key)
    assert any(ITEMS[p]["label"] in text for p in every), key
