"""THE US GAAP TEMPLATE IS THE FACE OF A FORM 10-K'S THREE PRIMARY STATEMENTS, AND NOTHING MORE.

The pair is built by `scripts/build_usgaap_pair.py`. These are the properties that make it what it
says it is:

  * the shipped files are exactly what the script builds, so the script is the reviewable source;
  * every line is a template line read from the FACE: no internal part, no note or sentence route,
    and the set is strict about it;
  * each asset, liability and cash-flow section has one leftover line; the statement of operations
    and stockholders' equity have none, and each leftover line refuses the totals printed in its
    section;
  * the statement-of-operations subtotals whose meaning depends on the filer's layout are read as
    printed;
  * a US balance sheet prints no non-current banner, so a non-current line binds under the current
    banner the reader carries past "Total current assets", and a caption printed in both places
    binds neither line;
  * the captions 10-K filers print bind their lines.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re

import pytest

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_template, validate_ontology_against_template
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = BACKEND / "app" / "sample" / "templates"
TEMPLATE = TEMPLATES / "output_csv_usgaap_v1_template.json"
SET = TEMPLATES / "output_csv_usgaap_line_items.json"

BS, PL, CF = "balance_sheet", "profit_and_loss", "cash_flow"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def raw():
    return (json.loads(TEMPLATE.read_text(encoding="utf-8")),
            json.loads(SET.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def usgaap():
    return load_line_item_set(json.loads(SET.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(usgaap):
    return OntologyMatcher(build_working_view(usgaap), locale="en", settings=get_settings())


def _nodes(template):
    for st in template["statements"]:
        for sec in st["sections"]:
            for node in sec["children"]:
                yield st["type"], sec["node_id"], node


# ── the source ─────────────────────────────────────────────────────────────────────────────────

def test_the_shipped_pair_is_what_the_script_builds(raw):
    template, line_items = _script("build_usgaap_pair").build()
    assert template == raw[0], "the template differs from scripts/build_usgaap_pair.py's: rebuild it"
    assert line_items == raw[1], "the line-item set differs from the script's: rebuild it"


def test_the_pair_loads_and_agrees_with_its_template(raw, usgaap):
    template = load_template(raw[0])
    assert validate_ontology_against_template(build_working_view(usgaap), template) == []
    node_keys = [node["canonical_key"] for _st, _sec, node in _nodes(raw[0])]
    assert len(node_keys) == len(set(node_keys))
    assert set(node_keys) == {i.key for i in usgaap.items}
    assert raw[1]["target_template_key"] == raw[0]["template_key"] == "output_csv_usgaap_v1"


def test_the_pair_is_seeded_at_boot():
    from app.sample import reference

    assert (TEMPLATE, SET) in [(t.resolve(), s.resolve()) for t, s in reference._EXTRA_PAIRS]


# ── face only ──────────────────────────────────────────────────────────────────────────────────

def test_every_line_is_a_face_line_of_the_three_statements(raw, usgaap):
    assert usgaap.strict_face_route is True
    for item in raw[1]["items"]:
        assert item["namespace"] == "template" and item["in_output"] is True, item["key"]
        assert not item["key"].startswith("sub__"), item["key"]
        assert item["route"] == "face", item["key"]
        assert "note_source" not in item and "cascade" not in item, item["key"]
    assert [st["type"] for st in raw[0]["statements"]] == [BS, PL, CF]


def test_the_keys_application_code_names_are_kept(usgaap):
    keys = {i.key for i in usgaap.items}
    assert {"bs_ca__total_assets", "bs_cl__total_equity_and_liabilities", "bs_equity__total_equity",
            "is_pl__sales_revenues", "is_pl__profit_for_the_year"} <= keys


def test_every_line_says_what_it_is(raw):
    for item in raw[1]["items"]:
        definition = item["definition"]
        assert len(definition) > 40 and "Extract the reported value" not in definition, item["key"]
        assert not re.search(r"\bnotes?\s+\d", definition, re.IGNORECASE), item["key"]


def test_no_per_share_or_share_count_line_is_carried(raw):
    """EPS is a per-share amount and share counts are not in the statements' scale; the face
    template carries neither."""
    for item in raw[1]["items"]:
        assert not re.search(r"per\s+share|weighted\s+average|shares\s+outstanding",
                             item["label"], re.IGNORECASE), item["key"]


# ── sections, leftovers and totals ─────────────────────────────────────────────────────────────

def test_each_asset_liability_and_cash_flow_section_has_one_leftover_line(raw):
    residuals = set(raw[1]["others_master"]["keys"])
    by_section: dict[str, list[str]] = {}
    for _st, sec, node in _nodes(raw[0]):
        if node["canonical_key"] in residuals:
            by_section.setdefault(sec, []).append(node["canonical_key"])
    assert set(by_section) == {"bs_ca", "bs_nca", "bs_cl", "bs_ncl", "cf_oper_indirect",
                               "cf_investing", "cf_financing"}
    assert all(len(v) == 1 for v in by_section.values()), by_section


def test_a_leftover_line_has_one_section_to_sweep(usgaap):
    """The sweep refuses a leftover line scoped to two sections, so the non-current leftovers keep
    their own section alone even though the non-current lines beside them also name the current
    one."""
    residuals = {k for k in json.loads(SET.read_text("utf-8"))["others_master"]["keys"]}
    for item in usgaap.items:
        if item.key in residuals:
            assert len(item.section_scope) == 1, (item.key, item.section_scope)


@pytest.mark.parametrize("key,caption", [
    ("bs_ca__other_current_assets", "Total current assets"),
    ("bs_ca__other_current_assets", "Total cash, cash equivalents, and short-term investments"),
    ("bs_ncl__other_non_current_liabilities", "Total liabilities"),
    ("bs_ncl__other_non_current_liabilities", "Redeemable noncontrolling interest"),
    ("cf_oper_indirect__other_operating_items", "Net cash provided by operating activities"),
    ("cf_investing__other_invest_cash_flows", "Net cash used in investing activities"),
    ("cf_financing__other_financing_cash_flows",
     "Effect of exchange rate changes on cash and cash equivalents"),
    ("cf_financing__other_financing_cash_flows", "Cash and cash equivalents, end of period"),
])
def test_a_leftover_line_refuses_the_totals_beside_it(usgaap, key, caption):
    item = {i.key: i for i in usgaap.items}[key]
    assert any(re.search(p, caption, re.IGNORECASE) for p in item.exclude_hints or ()), (key, caption)


def test_the_balance_sheet_balances_and_its_totals_add_up(raw):
    (bs,) = [st for st in raw[0]["statements"] if st["type"] == BS]
    assert bs["identities"][0]["lhs"] == "bs_ca__total_assets"
    rollups = {node["canonical_key"]: node["rollup"] for _st, _sec, node in _nodes(raw[0])
               if node.get("rollup")}
    assert rollups["bs_ca__total_assets"]["children"] == ["bs_ca__total_current_assets",
                                                         "bs_nca__total_non_current_assets"]
    # Temporary equity is neither a liability nor equity, and the right-hand total includes it.
    assert rollups["bs_cl__total_equity_and_liabilities"]["children"] == [
        "bs_cl__total_liabilities", "bs_mezz__temporary_equity", "bs_equity__total_equity"]
    assert rollups["bs_equity__total_equity"]["children"] == [
        "bs_equity__equity_attributable_to_owners", "bs_equity__non_controlling_interests"]


def test_the_net_change_in_cash_includes_the_exchange_rate_effect(raw):
    rollups = {node["canonical_key"]: node["rollup"] for _st, _sec, node in _nodes(raw[0])
               if node.get("rollup")}
    assert "cf_financing__net_foreign_exchange_difference" in rollups[
        "cf_financing__net_increase_in_cash"]["children"]
    assert rollups["cf_financing__end_of_period_cash_and_cash_equiv"]["children"] == [
        "cf_financing__net_increase_in_cash", "cf_financing__beg_of_period_cash_and_cash_equiv"]


def test_a_layout_dependent_subtotal_is_read_as_printed(raw):
    """A multi-step statement's total operating expenses excludes cost of revenue and a
    single-step statement's total costs and expenses includes it; operating income and income
    before taxes follow from whichever the filer prints. None carries a formula."""
    nodes = {node["canonical_key"]: node for _st, _sec, node in _nodes(raw[0])}
    for key in ("is_pl__total_operating_expenses", "is_pl__total_costs_and_expenses",
                "is_pl__operating_income", "is_pl__total_other_income_expense",
                "is_pl__income_before_taxes", "is_pl__profit_for_the_year"):
        assert nodes[key]["role"] in ("subtotal", "total") and "rollup" not in nodes[key], key
    assert nodes["is_pl__gross_profit"]["rollup"] == {
        "op": "diff", "children": ["is_pl__sales_revenues", "is_pl__cost_of_revenue"]}


# ── the US balance sheet's unheaded non-current rows ───────────────────────────────────────────

@pytest.mark.parametrize("caption,banner,key", [
    ("Property and equipment, net", "Current assets", "bs_nca__property_plant_and_equipment"),
    ("Goodwill", "Current assets", "bs_nca__goodwill"),
    ("Operating lease right-of-use assets", "Current assets",
     "bs_nca__operating_lease_right_of_use_assets"),
    ("Long-term debt", "Current liabilities", "bs_ncl__long_term_debt"),
    ("Long-term operating lease obligations", "Current liabilities",
     "bs_ncl__operating_lease_liabilities"),
    # The same caption on both sides, told apart by the banner.
    ("Deferred income taxes", "Current assets", "bs_nca__deferred_tax_assets"),
    ("Deferred income taxes", "Current liabilities", "bs_ncl__deferred_tax_liabilities"),
])
def test_a_non_current_line_binds_under_the_carried_current_banner(matcher, caption, banner, key):
    hit = matcher.match(caption, statement=BS, section=banner)
    assert hit.canonical_key == key and hit.confidence >= 0.9, (caption, hit.canonical_key)


@pytest.mark.parametrize("caption", ["Operating lease liabilities", "Term debt"])
@pytest.mark.parametrize("banner", ["Current liabilities", "Non-current liabilities"])
def test_a_caption_printed_on_both_sides_binds_neither_line(matcher, caption, banner):
    """Under the carried banner both rows would bind the current line and be added together."""
    hit = matcher.match(caption, statement=BS, section=banner)
    assert hit.canonical_key is None or hit.confidence < 0.85, (caption, banner, hit.canonical_key)


def test_a_current_line_does_not_bind_under_a_non_current_banner(matcher):
    hit = matcher.match("Marketable securities", statement=BS, section="Non-current assets")
    assert hit.canonical_key == "bs_nca__long_term_investments", hit.canonical_key


# ── the captions 10-K filers print ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("caption,statement,section,key", [
    ("Cash and cash equivalents", BS, "Current assets", "bs_ca__cash_and_cash_equivalents"),
    ("Accounts receivable, net of allowance for doubtful accounts of $944 and $830", BS,
     "Current assets", "bs_ca__accounts_receivable"),
    ("Merchandise inventories", BS, "Current assets", "bs_ca__inventories"),
    ("Property and equipment, net of accumulated depreciation of $93,653 and $76,421", BS,
     "Current assets", "bs_nca__property_plant_and_equipment"),
    ("Investments in and advances to affiliates", BS, "Current assets",
     "bs_nca__equity_method_investments"),
    ("Commercial paper", BS, "Current liabilities", "bs_cl__short_term_debt"),
    ("Long-term debt due within one year", BS, "Current liabilities", "bs_cl__short_term_debt"),
    ("Finance lease obligations due within one year", BS, "Current liabilities",
     "bs_cl__short_term_debt"),
    ("Accrued compensation and related benefits", BS, "Current liabilities",
     "bs_cl__accrued_liabilities"),
    ("Short-term unearned revenue", BS, "Current liabilities", "bs_cl__deferred_revenue"),
    ("Operating lease obligations due within one year", BS, "Current liabilities",
     "bs_cl__operating_lease_liabilities_current"),
    ("Long-term finance lease obligations", BS, "Current liabilities", "bs_ncl__long_term_debt"),
    ("Deferred income taxes and other", BS, "Current liabilities",
     "bs_ncl__deferred_tax_liabilities"),
    ("Liability for postemployment benefits", BS, "Current liabilities",
     "bs_ncl__pension_and_postretirement"),
    ("Commitments and contingencies Redeemable noncontrolling interest", BS,
     "Current liabilities", "bs_mezz__temporary_equity"),
    (("Common stock and additional paid-in capital, $0.00001 par value: 50,400,000 shares "
     "authorized; 15,116,786 and 15,550,061 shares issued and outstanding, respectively"), BS,
     "Shareholders’ equity", "bs_equity__common_stock"),
    ("Capital in excess of par value", BS, "Equity", "bs_equity__additional_paid_in_capital"),
    ("Profit employed in the business", BS, "Equity", "bs_equity__retained_earnings"),
    ("Accumulated other comprehensive loss", BS, "Equity",
     "bs_equity__accumulated_other_comprehensive_income"),
    ("Less: Treasury stock, at cost; 1,234 shares", BS, "Equity", "bs_equity__treasury_stock"),
    ("Total Bluegate shareholders’ equity", BS, "Equity", "bs_equity__equity_attributable_to_owners"),
    ("Nonredeemable noncontrolling interest", BS, "Equity", "bs_equity__non_controlling_interests"),
    ("Total liabilities, redeemable noncontrolling interest, and equity", BS, "Equity",
     "bs_cl__total_equity_and_liabilities"),
    ("Total net sales", PL, None, "is_pl__sales_revenues"),
    ("Membership and other income", PL, "Revenues", "is_pl__other_revenues"),
    ("Total cost of revenue", PL, "Cost of revenue", "is_pl__cost_of_revenue"),
    ("Cost of sales (exclusive of depreciation and amortization shown separately below)", PL, None,
     "is_pl__cost_of_revenue"),
    ("Gross margin", PL, None, "is_pl__gross_profit"),
    ("Sales and marketing", PL, None, "is_pl__selling_general_and_administrative"),
    ("Operating, selling, general and administrative expenses", PL, "Costs and expenses",
     "is_pl__selling_general_and_administrative"),
    ("Total costs and expenses", PL, None, "is_pl__total_costs_and_expenses"),
    ("Income from operations", PL, None, "is_pl__operating_income"),
    ("Interest, net", PL, "Interest", "is_pl__interest_expense"),
    ("Other income/(expense), net", PL, None, "is_pl__other_income_expense"),
    ("Income before provision for income taxes", PL, None, "is_pl__income_before_taxes"),
    ("Provision (benefit) for income taxes", PL, None, "is_pl__income_tax_expense"),
    ("Equity in profit (loss) of unconsolidated affiliated companies", PL, None,
     "is_pl__equity_in_earnings_of_affiliates"),
    ("Consolidated net income", PL, None, "is_pl__profit_for_the_year"),
    ("Less: Net income attributable to noncontrolling interests", PL, None,
     "is_pl__net_income_attributable_to_nci"),
    ("Consolidated net income attributable to Bluegate", PL, None,
     "is_pl__net_income_attributable_to_parent"),
    ("Cash, cash equivalents, and restricted cash and cash equivalents, beginning balances", CF, None,
     "cf_financing__beg_of_period_cash_and_cash_equiv"),
    ("Operations Net income", CF, None, "cf_oper_indirect__net_income"),
    ("Depreciation, amortization, and other", CF, "Operating activities",
     "cf_oper_indirect__depreciation_and_amortization"),
    ("Share-based compensation expense", CF, "Operating activities",
     "cf_oper_indirect__share_based_compensation"),
    ("Changes in certain assets and liabilities: Receivables, net", CF, "Operating activities",
     "cf_oper_indirect__chg_in_accounts_receivable"),
    ("Cash generated by operating activities", CF, "Operating activities",
     "cf_oper_indirect__cash_flows_oper_activ_indirect"),
    ("Net cash from operations", CF, None, "cf_oper_indirect__cash_flows_oper_activ_indirect"),
    ("Payments for acquisition of property, plant and equipment", CF, "Investing activities",
     "cf_investing__purchase_ppe"),
    ("Acquisition of companies, net of cash acquired, and purchases of intangible and other assets",
     CF, None, "cf_investing__acquisitions"),
    ("Proceeds from maturities of marketable securities", CF, "Investing activities",
     "cf_investing__proceeds_from_investments"),
    ("Net cash used in investing", CF, None, "cf_investing__cash_flows_from_invest_activities"),
    ("Proceeds from/(Repayments of) commercial paper, net", CF, "Financing activities",
     "cf_financing__net_change_short_term_debt"),
    ("Purchase of Company stock", CF, "Financing activities", "cf_financing__share_repurchases"),
    ("Payments for dividends and dividend equivalents", CF, "Financing activities",
     "cf_financing__dividends_paid"),
    ("Effect of exchange rates on cash, cash equivalents and restricted cash", CF, None,
     "cf_financing__net_foreign_exchange_difference"),
    ("Increase/(Decrease) in cash, cash equivalents, and restricted cash and cash equivalents", CF,
     None, "cf_financing__net_increase_in_cash"),
    ("Cash, cash equivalents and restricted cash at end of year", CF, None,
     "cf_financing__end_of_period_cash_and_cash_equiv"),
])
def test_a_10k_caption_binds_its_line(matcher, caption, statement, section, key):
    hit = matcher.match(caption, statement=statement, section=section)
    assert hit.canonical_key == key, (caption, hit.canonical_key, hit.confidence)
    assert hit.confidence >= 0.9, (caption, hit.confidence)


@pytest.mark.parametrize("caption,statement,section", [
    ("Products", PL, "Net sales"),
    ("Basic", PL, "Earnings per share"),
    ("Diluted net income per common share attributable to Bluegate", PL, None),
    ("Vendor non-trade receivables", BS, "Current assets"),
    ("Dividends paid to noncontrolling interest", CF, "Financing activities"),
    ("Cash paid for income taxes, net", CF, None),
])
def test_a_row_no_line_names_binds_nothing(matcher, caption, statement, section):
    hit = matcher.match(caption, statement=statement, section=section)
    assert hit.canonical_key is None or hit.confidence < 0.85, (caption, hit.canonical_key)
