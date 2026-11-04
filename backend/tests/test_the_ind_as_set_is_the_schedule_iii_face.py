"""THE IND AS TEMPLATE IS THE FACE OF A SCHEDULE III DIVISION II FILING, AND NOTHING MORE.

The pair is built by `scripts/build_indas_pair.py`. It replaced the HK set with Ind AS captions
added beside the HKFRS ones — 198 lines and 65 internal parts, 46 read from note tables and 6 from
sentences, and lines no Indian balance sheet prints. These are the properties that make the new
one what it says it is:

  * the shipped files are exactly what the script builds, so the script is the reviewable source;
  * every line is a template line read from the FACE: no internal part, no note or sentence route,
    no note patterns, and the set is strict about it;
  * each balance-sheet and cash-flow section has one leftover line, the statement of profit and
    loss none, and each leftover line refuses the totals printed in its section;
  * the profit-and-loss subtotals whose arithmetic depends on the company's layout are read as
    printed, with no formula a valid layout would fail;
  * the captions two Indian filings print bind their lines (Asian Paints 2025-26, Laurus Labs
    2025-26, measured answer-free with this pair);
  * ICON, whose build reads its policy blocks from this set, builds unchanged.
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
TEMPLATE = TEMPLATES / "output_csv_indas_v1_template.json"
SET = TEMPLATES / "output_csv_indas_line_items.json"
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


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
def indas():
    return load_line_item_set(json.loads(SET.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(indas):
    return OntologyMatcher(build_working_view(indas), locale="en", settings=get_settings())


def _nodes(template):
    for st in template["statements"]:
        for sec in st["sections"]:
            for node in sec["children"]:
                yield st["type"], sec["node_id"], node


# ── the source ─────────────────────────────────────────────────────────────────────────────────

def test_the_shipped_pair_is_what_the_script_builds(raw):
    template, line_items = _script("build_indas_pair").build()
    assert template == raw[0], "the template differs from scripts/build_indas_pair.py's: rebuild it"
    assert line_items == raw[1], "the line-item set differs from the script's: rebuild it"


def test_icon_builds_unchanged_from_this_sets_policy_blocks():
    """`build_icon_pair` copies the Ind AS set's vocabulary, normalisation, binding, scope
    selection, residual framework and prose grammar, so rewriting this set must not move ICON."""
    template, line_items, _workbook = _script("build_icon_pair").build()
    assert template == json.loads((TEMPLATES / "output_csv_icon_v1_template.json").read_text("utf-8"))
    assert line_items == json.loads((TEMPLATES / "output_csv_icon_line_items.json").read_text("utf-8"))


def test_the_pair_loads_and_agrees_with_its_template(raw, indas):
    template = load_template(raw[0])
    assert validate_ontology_against_template(build_working_view(indas), template) == []
    node_keys = [node["canonical_key"] for _st, _sec, node in _nodes(raw[0])]
    assert len(node_keys) == len(set(node_keys))
    assert set(node_keys) == {i.key for i in indas.items}


# ── face only ──────────────────────────────────────────────────────────────────────────────────

def test_every_line_is_a_face_line_of_the_template(raw, indas):
    assert indas.strict_face_route is True
    for item in raw[1]["items"]:
        assert item["namespace"] == "template" and item["in_output"] is True, item["key"]
        assert not item["key"].startswith("sub__"), item["key"]
        assert item["route"] == "face", item["key"]
        assert "note_source" not in item and "cascade" not in item, item["key"]
    assert {st["type"] for st in raw[0]["statements"]} == {"balance_sheet", "profit_and_loss",
                                                           "cash_flow"}


def test_the_keys_application_code_names_are_kept(indas):
    keys = {i.key for i in indas.items}
    assert {"bs_ca__total_assets", "bs_cl__total_equity_and_liabilities", "bs_equity__total_equity",
            "is_pl__sales_revenues", "is_pl__profit_for_the_year"} <= keys


def test_the_vocabulary_is_english_and_every_line_says_what_it_is(raw):
    for item in raw[1]["items"]:
        text = json.dumps(item, ensure_ascii=False)
        assert not _HAN.search(text), item["key"]
        definition = item["definition"]
        assert len(definition) > 40 and "Extract the reported value" not in definition, item["key"]
        assert not re.search(r"\bnotes?\s+\d", definition, re.IGNORECASE), item["key"]


# ── sections, leftovers and totals ─────────────────────────────────────────────────────────────

def test_each_balance_sheet_and_cash_flow_section_has_one_leftover_line(raw):
    residuals = set(raw[1]["others_master"]["keys"])
    by_section: dict[str, list[str]] = {}
    for st, sec, node in _nodes(raw[0]):
        if node["canonical_key"] in residuals:
            by_section.setdefault(sec, []).append(node["canonical_key"])
    assert set(by_section) == {"bs_nca", "bs_ca", "bs_equity", "bs_ncl", "bs_cl", "is_oci",
                               "cf_oper_indirect", "cf_investing", "cf_financing"}
    assert all(len(v) == 1 for v in by_section.values()), by_section
    assert "is_pl" not in by_section


@pytest.mark.parametrize("key,caption", [
    ("cf_oper_indirect__other_operating_items", "Operating Profit before working capital changes"),
    ("cf_oper_indirect__other_operating_items", "Cash generated from Operating activities"),
    ("cf_oper_indirect__other_operating_items", "Net Cash generated from Operating activities"),
    ("cf_financing__other_financing_cash_flows", "Cash on hand"),
    ("cf_financing__other_financing_cash_flows", "Add : Investments in Liquid Mutual Funds"),
    ("cf_financing__other_financing_cash_flows", "Lease Liabilities (Refer note 15)"),
    ("is_oci__other_comprehensive_income_items", "Total Other Comprehensive Income (A+B)"),
    ("is_oci__other_comprehensive_income_items", "Other comprehensive income for the year, net of tax"),
    ("is_oci__other_comprehensive_income_items", "TOTAL COMPREHENSIVE INCOME FOR THE YEAR"),
    ("bs_ca__other_current_assets", "Total current assets"),
])
def test_a_leftover_line_refuses_the_totals_beside_it(indas, key, caption):
    item = {i.key: i for i in indas.items}[key]
    assert any(re.search(p, caption, re.IGNORECASE) for p in item.exclude_hints or ()), (key, caption)


def test_the_balance_sheet_balances_and_its_totals_add_up(raw):
    (bs,) = [st for st in raw[0]["statements"] if st["type"] == "balance_sheet"]
    assert bs["identities"][0]["lhs"] == "bs_ca__total_assets"
    rollups = {node["canonical_key"]: node["rollup"] for _st, _sec, node in _nodes(raw[0])
               if node.get("rollup")}
    assert rollups["bs_ca__total_assets"]["children"] == ["bs_nca__total_non_current_assets",
                                                         "bs_ca__total_current_assets"]
    assert rollups["bs_cl__total_equity_and_liabilities"]["children"] == [
        "bs_equity__total_equity", "bs_cl__total_liabilities"]


def test_a_layout_dependent_pl_subtotal_is_read_as_printed(raw):
    """Asian Paints and Laurus both print 'Total expenses' before finance costs and depreciation,
    then EBITDA; Asian Paints adds its share of associates before exceptional items, Laurus after
    tax. A formula for Schedule III's own layout fails both, so these carry none."""
    nodes = {node["canonical_key"]: node for _st, _sec, node in _nodes(raw[0])}
    for key in ("is_pl__total_expenses", "is_pl__ebitda",
                "is_pl__profit_before_exceptional_items_and_tax", "is_pl__profit_loss_before_tax",
                "is_pl__profit_for_the_year"):
        assert nodes[key]["role"] in ("subtotal", "total") and "rollup" not in nodes[key], key
    for key, children in (("is_pl__sales_revenues", 2), ("is_pl__total_income", 2),
                          ("is_pl__total_income_tax", 3),
                          ("is_oci__total_comprehensive_income", 2)):
        assert len(nodes[key]["rollup"]["children"]) == children, key


# ── the captions two Indian filings print ──────────────────────────────────────────────────────

@pytest.mark.parametrize("caption,statement,section,key", [
    # Asian Paints 2025-26
    ("Revenue from Sale of Products", "profit_and_loss", "Revenue",
     "is_pl__revenue_from_sale_of_products_and_services"),
    ("Other Operating Revenue", "profit_and_loss", "Revenue", "is_pl__other_operating_revenue"),
    ("EARNING BEFORE INTEREST, TAX, DEPRECIATION AND AMORTISATION (EBITDA) (I-II)",
     "profit_and_loss", None, "is_pl__ebitda"),
    ("Share of profit in Associates", "profit_and_loss", None,
     "is_pl__share_of_profit_of_associates"),
    ("Short/(Excess) tax provision for earlier years", "profit_and_loss", "Tax expense",
     "is_pl__earlier_years_tax"),
    ("Total Other Comprehensive Income (A+B)", "profit_and_loss", "Other comprehensive income",
     "is_oci__total_other_comprehensive_income"),
    ("- Owners of the Company", "profit_and_loss", "PROFIT FOR THE YEAR ATTRIBUTABLE TO :",
     "is_pl__profit_attributable_to_owners"),
    ("Non-Current Assets", "balance_sheet", "Non-current assets", "bs_nca__total_non_current_assets"),
    ("Investments in Associates", "balance_sheet", "Non-current assets",
     "bs_nca__investments_equity_method"),
    ("Other Balances with Banks", "balance_sheet", "Current assets", "bs_ca__other_bank_balances"),
    ("Total Outstanding dues of Micro Enterprises and Small Enterprises", "balance_sheet",
     "Current liabilities", "bs_cl__trade_payables_cp"),
    ("Income Tax paid (net of refund)", "cash_flow", "Operating activities",
     "cf_oper_indirect__income_taxes_paid_indirect"),
    ("Purchase of property, plant and equipment and other intangible assets", "cash_flow",
     "Investing activities", "cf_investing__purchase_ppe"),
    ("Sale of current investments (net)", "cash_flow", "Investing activities",
     "cf_investing__proceeds_sale_fncl_assets"),
    ("Repayment of principal portion of lease liabilities", "cash_flow", "Financing activities",
     "cf_financing__lease_payments"),
    ("Finance costs (including interest on lease liabilities) paid", "cash_flow",
     "Financing activities", "cf_financing__interest_paid_fin"),
    ("Add : Cash and cash equivalents as at 1st April", "cash_flow", "Financing activities",
     "cf_financing__beg_of_period_cash_and_cash_equiv"),
    ("Cash and cash equivalents as at 31st March", "cash_flow", "Financing activities",
     "cf_financing__end_of_period_cash_and_cash_equiv"),
    ("(D) NET INCREASE / (DECREASE) IN CASH AND CASH EQUIVALENTS [A+B+C]", "cash_flow",
     "Financing activities", "cf_financing__net_increase_in_cash"),
    # Laurus Labs 2025-26
    ("III. Earnings before Interest, Tax, Depreciation and Amortisation (EBITDA) (I-II)",
     "profit_and_loss", None, "is_pl__ebitda"),
    ("VII. Share of profit/(loss) from associates and joint venture, net of tax",
     "profit_and_loss", None, "is_pl__share_of_profit_of_associates"),
    ("Total other comprehensive loss for the year, net of tax", "profit_and_loss", None,
     "is_oci__total_other_comprehensive_income"),
    ("Total equity attributable to equity holders of Holding Company", "balance_sheet", "Equity",
     "bs_equity__equity_attributable_to_owners"),
    ("Total non-current liabiliti es", "balance_sheet", "Non-current liabilities",
     "bs_ncl__total_non_current_liabilities"),
    ("Cash flow from operating activities Profit before tax", "cash_flow", None,
     "cf_oper_indirect__profit_before_tax"),
    ("Net cash generated from operating activities (A)", "cash_flow", "Operating activities",
     "cf_oper_indirect__cash_flows_oper_activ_indirect"),
    ("Cash flow from financing activities Proceeds from exercise of employee stock options",
     "cash_flow", None, "cf_financing__net_proceeds_issuance_of_capital"),
    ("Cash and cash equivalents at the year end", "cash_flow", "Financing activities",
     "cf_financing__end_of_period_cash_and_cash_equiv"),
])
def test_a_schedule_iii_caption_binds_its_line(matcher, caption, statement, section, key):
    hit = matcher.match(caption, statement=statement, section=section)
    assert hit.canonical_key == key, (caption, hit.canonical_key, hit.confidence)
    assert hit.confidence >= 0.9, (caption, hit.confidence)


@pytest.mark.parametrize("caption,statement,section", [
    ("Cash generated from Operating activities", "cash_flow", "Operating activities"),
    ("Effect of exchange rates on translation of operating cashflows", "cash_flow",
     "Operating activities"),
    ("Basic (in ₹)", "profit_and_loss", "Other comprehensive income"),
])
def test_a_row_no_line_names_binds_nothing(matcher, caption, statement, section):
    hit = matcher.match(caption, statement=statement, section=section)
    assert hit.canonical_key is None or hit.confidence < 0.85, (caption, hit.canonical_key)
