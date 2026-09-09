"""Rewrite the shipped template's balance sheet to the revised specification.

Applied once and kept, because the spec it encodes is reviewable here in a way 85 hand-edited JSON
nodes are not, and the profit-and-loss and cash-flow revisions will follow the same shape. Every
edit is add-or-replace, so running it twice leaves the same files.

What this changes, and why each matters:

* HK LADDER ORDER. Current liabilities now sit before non-current ones, with the ladder totals
  interleaved (net current assets → total assets less current liabilities → net assets), which is how
  an HKEX filing presents a balance sheet. The previous order put both liability sections after equity
  and left the ladder totals stranded at the bottom.

* SECTION IDS FOLLOW THE NEW ORDER. The ids are positional (``bs_s3_…`` is the third section), so
  leaving current liabilities called ``bs_s5_current_liabilities`` in third place would make the
  name lie about the position.

  To be accurate about what this does and does not matter for: the matching gate does NOT compare
  these ids to the template's. ``mapping.section_token_of_scope`` reads the section name off the END
  of a scope id (``bs_s3_current_liabilities`` and ``bs_s5_current_liabilities`` both resolve to the
  token ``current_liabilities``), so the number is decorative to the gate. What it IS required to be
  is internally consistent with the CONFIGURATION that gates onto it: the line-item set's
  ``section_defaults`` is keyed by section id and each item names one through ``inherits``. That
  side is authored in the set and rebuilt by ``scripts/build_line_items.py`` — this script writes
  the template and stops there.

* THE BALANCE CHECK BECOMES A REAL TEST. ``bs_net_assets`` was ``SUM(bs_equity__total_equity)`` — net
  assets defined as equity, so the one relation that proves a balance sheet balances was an identity
  with itself and could never fail. It is now the asset-side ladder
  (total assets less current liabilities − total non-current liabilities), which is what makes the
  net-assets-equals-equity relation one that can actually break. A tautology that
  reads as a passing check is worse than no check: it consumes a slot in the coverage report and
  reports success.

* EQUITY IS RESTRUCTURED. Reserves now absorbs share premium, treasury shares and shares held for
  award schemes alongside the four reserve captions, and ``equity_attributable_to_owners`` is a new
  node between reserves and total equity. Total equity is therefore reached in two steps
  (owners' + NCI) rather than one flat sum, which is what lets a break in the attribution be seen.

WHAT THIS USED TO DO AS WELL, and no longer does — stated so nobody reinstates it. Four of the
revision's items were edits to ``hkfrs_hk_china_ontology.json``, a second declaration of this same
balance sheet: the section-id rename, the two misspelt canonical keys
(``cuurent_notes_payable`` / ``current_potion_of_long_term_debt``), the new
``bs_current_assets__contract_assets`` concept, and the validation identities the restructure made
checkable (``bs_derived_consistency`` added, ``bs_balance`` dropped as the template's own
``bs_balances`` in different words). That file is seeded by nothing and loaded by nothing now that
line items is the single configuration engine, so those edits held a second answer in step with
the template and the second answer is gone. Anything the configuration has to say about this
balance sheet is authored in the line-item set (``app/sample/templates/output_csv_hk_*``) and
rebuilt by ``scripts/build_line_items.py``.

WHAT THE SPEC ASKED FOR AND THIS DOES NOT ENCODE — one item, stated rather than quietly dropped:
``COALESCE( bs_equity__reserves , SUM( … ) )`` on rows 79 and 80. There is no coalesce op and none is
added, because printed-if-present-else-computed is ALREADY how every calculated line behaves
(services/rollups.py: "The printed figure is never discarded. It is what the divergence is measured
against") — the face shows the computed figure and the printed one becomes a calculated_mismatch
review card. A plain ``sum`` rollup therefore already means what the COALESCE was written to mean,
and a second op spelling the same behaviour is the trap that got ``weighted_sum`` deleted.
"""
from __future__ import annotations

import json
import pathlib
import sys

TPL = pathlib.Path("app/sample/templates/hkfrs_hk_china_template.json")

# The template file, and nothing else. ``ONTOLOGY``, ``KEY_FIXES`` and ``SECTION_RENAMES`` stood
# here for the concept-file half this script no longer has — see the comment above ``main``.

# (canonical_key suffix, English label, zh label). Order is the order on screen.
NCA = [
    ("property_plant_and_equipment", "Property, Plant and Equipment", "物业、厂房及设备"),
    ("investment_properties", "Investment Properties", "投资物业"),
    ("right_of_use_assets", "Right-of-use assets", "使用权资产"),
    ("land_of_use_rights", "Land use rights", "土地使用权"),
    ("construction_in_progress", "Construction in progress", "在建工程"),
    ("properties_under_development", "Properties under development", "开发中物业"),
    ("goodwill", "Goodwill", "商誉"),
    ("other_intangible_assets", "Other Intangible assets", "其他无形资产"),
    ("intangible_assets_under_development", "Intangible assets under development", "在建无形资产"),
    ("investments_in_subsidiaries", "Investments in subsidiaries", "于子公司的投资"),
    ("interests_in_associates", "Interests in associates", "于联营公司的权益"),
    ("interests_in_joint_ventures", "Interests in joint ventures", "于合营企业的权益"),
    ("equity_investments_designated_at_fair_value_through_other_comprehensive_income",
     "Equity investments designated at fair value through other comprehensive income",
     "以公允价值计量且其变动计入其他综合收益的权益投资"),
    ("financial_assets_at_fair_value_through_profit_or_loss",
     "Financial assets at fair value through profit or loss",
     "以公允价值计量且其变动计入损益的金融资产"),
    ("other_non_current_financial_assets", "Other non-current financial assets",
     "其他非流动金融资产"),
    ("term_deposits", "Term Deposits", "定期存款"),
    ("prepayments_and_other_assets", "Prepayments and other assets", "预付款项及其他资产"),
    ("contract_in_progress", "Contract in progress", "在建合同"),
    ("deferred_income_tax_assets", "Deferred Income Tax Assets", "递延所得税资产"),
    ("others", "Others", "其他"),
]
CA = [
    ("inventories", "Inventories", "存货"),
    ("properties_under_development", "Properties under development", "开发中物业"),
    ("completed_properties_held_for_sale", "Completed properties held for sale", "持作销售的已完工物业"),
    ("trade_receivables", "Trade receivables", "应收账款"),
    ("contract_assets", "Contract assets", "合同资产"),
    ("prepayments_other_receivables_and_other_assets",
     "Prepayments, other receivables and other assets", "预付款项、其他应收款及其他资产"),
    ("due_from_related_parties", "Due from related parties", "应收关联方款项"),
    ("prepaid_income_tax", "Prepaid income tax", "预缴所得税"),
    ("financial_assets_at_fair_value_through_other_comprehensive_income",
     "Financial assets at fair value through other comprehensive income",
     "以公允价值计量且其变动计入其他综合收益的金融资产"),
    ("other_financial_assets", "Other Financial Assets", "其他金融资产"),
    ("pledged_deposits", "Pledged deposits", "已质押存款"),
    ("restricted_cash", "Restricted cash", "受限制现金"),
    ("bank_balances_other_than_cash_and_cash_equivalents",
     "Bank balances other than cash and cash equivalents", "除现金及现金等价物以外的银行存款"),
    ("cash_and_cash_equivalents", "Cash and cash equivalents", "现金及现金等价物"),
    ("others", "Others", "其他"),
]
CL = [
    ("current_trade_payables", "Trade payables", "应付账款"),
    ("current_notes_payable", "Notes payable", "应付票据"),
    ("contract_liabilities", "Contract liabilities", "合同负债"),
    ("other_payables_and_accruals", "Other payables and accruals", "其他应付款及预提费用"),
    ("due_to_related_parties", "Due to related parties", "应付关联方款项"),
    ("current_deferred_revenue", "Deferred Revenue", "递延收入"),
    ("current_lease_liabilities", "Lease Liabilities", "租赁负债"),
    ("current_borrowings", "Borrowings", "借款"),
    ("current_portion_of_long_term_debt", "Current portion of long term debt", "长期借款的即期部分"),
    ("other_current_financial_liabilities", "Other current financial liabilities",
     "其他流动金融负债"),
    ("current_income_tax_liabilities", "Income tax liabilities", "应付所得税"),
    ("others", "Others", "其他"),
]
NCL = [
    ("non_current_borrowings", "Borrowings", "借款"),
    ("non_current_bonds_payable", "Bonds payable", "应付债券"),
    ("non_current_notes_payable", "Notes Payable", "应付票据"),
    ("long_term_payables", "Long-term payables", "长期应付款"),
    ("non_current_lease_liabilities", "Lease liabilities", "租赁负债"),
    ("long_term_provisions", "Long-term provisions", "长期准备"),
    ("non_current_deferred_income", "Deferred income", "递延收益"),
    ("non_current_deferred_tax_liabilities_net", "Deferred tax liabilities (net)",
     "递延所得税负债（净额）"),
    ("others", "Others", "其他"),
]
# Equity is not a flat list: the two contributed-capital lines, then everything that rolls into
# reserves, then the attributable subtotal, then NCI.
EQ_HEAD = [
    ("share_capital", "Share capital", "股本"),
    ("other_equity_instruments", "Other equity instruments", "其他权益工具"),
]
# Treasury shares and shares held for award schemes belong here, negative: they are deductions from
# reserves, and reserves is where the spec puts them.
EQ_RESERVE_PARTS = [
    ("share_premium", "Share Premium", "股份溢价", "natural"),
    ("capital_reserve", "Capital reserve", "资本公积", "natural"),
    ("general_reserve", "General Reserve", "一般储备", "natural"),
    ("other_comprehensive_income_reserve", "Other comprehensive income reserve", "其他综合收益储备",
     "natural"),
    ("retained_earnings", "Retained earnings", "留存收益", "natural"),
    ("treasury_shares", "Treasury Shares", "库存股", "natural_negative"),
    ("shares_held_for_share_award_schemes", "Shares held for share award schemes",
     "为股份奖励计划持有的股份", "natural_negative"),
    ("others", "Others", "其他", "natural"),
]


def line(prefix: str, key: str, en: str, zh: str, sign: str = "natural") -> dict:
    ck = f"{prefix}__{key}"
    node = {"node_id": ck, "canonical_key": ck, "label": en, "role": "line",
            "label_i18n": {"en": en, "zh": zh}}
    if sign != "natural":
        node["sign"] = sign
    return node


def calc(ck: str, en: str, zh: str, op: str, children: list[str], role: str) -> dict:
    return {"node_id": ck, "canonical_key": ck, "label": en, "role": role,
            "label_i18n": {"en": en, "zh": zh}, "rollup": {"op": op, "children": children}}


def section(node_id: str, en: str, zh: str, children: list[dict]) -> dict:
    return {"node_id": node_id, "canonical_key": node_id, "label": en, "role": "header",
            "label_i18n": {"en": en, "zh": zh}, "children": children}


def build_balance_sheet() -> dict:
    p_nca, p_ca = "bs_non_current_assets", "bs_current_assets"
    p_cl, p_ncl, p_eq = "bs_current_liabilities", "bs_non_current_liabilities", "bs_equity"

    nca_lines = [line(p_nca, k, en, zh) for k, en, zh in NCA]
    nca_total = calc(f"{p_nca}__total_non_current_assets", "Total non-current assets",
                     "非流动资产总额", "sum", [c["canonical_key"] for c in nca_lines], "subtotal")

    ca_lines = [line(p_ca, k, en, zh) for k, en, zh in CA]
    ca_total = calc(f"{p_ca}__total_current_assets", "Total current assets", "流动资产总额",
                    "sum", [c["canonical_key"] for c in ca_lines], "subtotal")

    cl_lines = [line(p_cl, k, en, zh) for k, en, zh in CL]
    cl_total = calc(f"{p_cl}__total_current_liabilities", "Total current liabilities",
                    "流动负债总额", "sum", [c["canonical_key"] for c in cl_lines], "subtotal")

    ncl_lines = [line(p_ncl, k, en, zh) for k, en, zh in NCL]
    ncl_total = calc(f"{p_ncl}__total_non_current_liabilities", "Total non-current liabilities",
                     "非流动负债总额", "sum", [c["canonical_key"] for c in ncl_lines], "subtotal")

    eq_head = [line(p_eq, k, en, zh) for k, en, zh in EQ_HEAD]
    eq_reserve_parts = [line(p_eq, k, en, zh, sign) for k, en, zh, sign in EQ_RESERVE_PARTS]
    reserves = calc(f"{p_eq}__reserves", "Reserves", "储备", "sum",
                    [c["canonical_key"] for c in eq_reserve_parts], "subtotal")
    attributable = calc(f"{p_eq}__equity_attributable_to_owners",
                        "Equity attributable to owners of the Company", "本公司拥有人应占权益",
                        "sum", [c["canonical_key"] for c in eq_head] + [reserves["canonical_key"]],
                        "subtotal")
    nci = line(p_eq, "non_controlling_interests", "Non-controlling interests", "非控股权益")
    total_equity = calc(f"{p_eq}__total_equity", "Total equity", "权益总额", "sum",
                        [attributable["canonical_key"], nci["canonical_key"]], "subtotal")

    # The ladder, in presentation order.
    net_current = calc("bs_net_current_assets_liabilities", "Net current assets/(liabilities)",
                       "流动资产／（负债）净额", "diff",
                       [ca_total["canonical_key"], cl_total["canonical_key"]], "total")
    tal_cl = calc("bs_total_assets_less_current_liabilities",
                  "Total assets less current liabilities", "总资产减流动负债", "sum",
                  [nca_total["canonical_key"], net_current["canonical_key"]], "total")
    # NOT SUM(total_equity) — see the module docstring. This is the asset-side route, so the
    # equality with equity becomes a check that can actually fail.
    net_assets = calc("bs_net_assets", "NET ASSETS", "资产净值", "diff",
                      [tal_cl["canonical_key"], ncl_total["canonical_key"]], "total")
    total_liabilities = calc("bs_liabilities__total_liabilities", "Total liabilities", "负债总额",
                             "sum", [ncl_total["canonical_key"], cl_total["canonical_key"]],
                             "total")
    total_assets = calc("bs_total_assets", "Total Assets", "资产总额", "sum",
                        [nca_total["canonical_key"], ca_total["canonical_key"]], "total")
    total_eq_liab = calc("bs_total_equity_and_liabilities", "Total Equity and Liabilities",
                         "权益及负债总额", "sum",
                         [total_equity["canonical_key"], total_liabilities["canonical_key"]],
                         "total")

    sections = [
        section("bs_s1_non_current_assets", "Non-current assets", "非流动资产",
                nca_lines + [nca_total]),
        section("bs_s2_current_assets", "Current assets", "流动资产", ca_lines + [ca_total]),
        section("bs_s3_current_liabilities", "Current liabilities", "流动负债",
                cl_lines + [cl_total]),
        net_current, tal_cl,
        section("bs_s4_non_current_liabilities", "Non-current liabilities", "非流动负债",
                ncl_lines + [ncl_total]),
        net_assets,
        section("bs_s5_equity", "Equity", "权益",
                eq_head + eq_reserve_parts + [reserves, attributable, nci, total_equity]),
        total_liabilities, total_assets, total_eq_liab,
    ]

    # The footing, and only the footing. The other balance-sheet relations are the configuration's,
    # where each carries a severity — see the module docstring.
    identities = [{"id": "bs_balances", "lhs": "bs_total_assets",
                   "rhs": {"op": "sum", "children": [total_eq_liab["canonical_key"]]}}]
    return {"type": "balance_sheet", "sections": sections, "identities": identities}


# WHAT WAS HERE AND IS GONE, so nobody reinstates it: ~290 lines of surgery on
# ``app/sample/templates/hkfrs_hk_china_ontology.json`` — ``revise_ontologies`` and the eleven
# helpers only it called (``seed_contract_assets``, ``revise_keys``, ``rename_sections``,
# ``sync_declared_count``, ``sync_reserves_group``, ``revise_validation`` and their key/section
# rename tables). They kept a SECOND declaration of the same balance sheet in step with the
# template above: the concept file's section layer, its two misspelt canonical keys, its new
# concept and its validation identities. Line items is the single configuration engine now — the
# concept file is seeded by nothing, loaded by nothing and selectable nowhere, so keeping it in
# step kept nothing in step. What this script still owns is the template's balance sheet; the
# configuration that maps captions onto it is authored in the line-item set and rebuilt by
# ``scripts/build_line_items.py``.


def main() -> int:
    tpl = json.loads(TPL.read_text())
    for i, st in enumerate(tpl["statements"]):
        if st.get("type") == "balance_sheet":
            tpl["statements"][i] = build_balance_sheet()
            break
    else:
        print("no balance_sheet statement to replace")
        return 1
    TPL.write_text(json.dumps(tpl, ensure_ascii=False, indent=2) + "\n")

    bs = build_balance_sheet()
    rows = sum(1 + len(s.get("children") or []) if s.get("children") else 1
               for s in bs["sections"])
    print(f"balance sheet rewritten: {len(bs['sections'])} top-level nodes, {rows} screen rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
