"""Build the ICON template and its line-item set from the Company v3 workbook draft.

ICON is an Indian bank credit-monitoring (CMA-style) spread: a Profit and Loss Statement, a
Balance Sheet, and Additional Information (contingent liabilities and other information) — the
first three sheets of the Company v3 workbook, which is also its output format. The workbook it comes
from reached this repository as a DRAFT LINE-ITEM SET (``sample_data/icon/
company_v3_line_items_draft.json``: 189 items keyed by workbook UUIDs, with each line's Excel
formula in its definition) and no template. This script is the one place that turns it into the
shipped pair, so the pair can be rebuilt and every decision below is reviewable in one file:

    python scripts/build_icon_pair.py          # writes app/sample/templates/output_csv_icon_*

WHAT IT DECIDES, and why:

* READABLE KEYS. ``pl_icon__`` / ``bs_icon__`` / ``cov_icon__`` replace the UUIDs: the Workspace
  scopes a statement's rows by the text before the first ``_`` of their keys, which a UUID does not
  have, and a key space of its own keeps ICON clear of the HK and Ind AS keys (``llm_focus_keys``,
  caption inventories). The map is ``KEY_OVERRIDES`` plus a slug of the label.
* THE TEMPLATE IS BUILT FROM THE WORKBOOK'S OWN FORMULAS. Every calculated line's rollup is its
  draft terms: ``sum``, or ``diff`` where the workbook subtracts. ICON stores expenses POSITIVE, as
  the workbook does (Net Sales = Total - Excise duty), so its rulebook does NOT negate expenses on
  load. A deduction that can never be negative but may be printed in brackets or after "Less:"
  (excise duty, operating expenses, interest, dividend, accumulated depreciation and amortisation)
  enters as a magnitude; one that can legitimately be negative (tax, which may be a credit) keeps
  its sign. Three totals roll up through their groups rather than the groups' leaves (current
  assets through receivables, inventory through raw materials, other non-current assets through
  exposure in group entities): the same arithmetic when the leaves are printed, and the printed
  group still reaches the total when they are not.
* NON-MONETARY ROWS ARE KPIs, NOT LINES. Every template line is treated as money: scaled to the
  presentation unit and FX rate on screen and in export. Current ratio, TOL/TNW, TTL/TNW, retained
  profit to net profit and the dividend rate therefore become template KPIs; the TRUE/FALSE balance
  check becomes a balance-sheet identity; the signed difference stays a calculated line. Net-sales
  growth needs two periods, which no rollup or KPI can express, so it is not carried.
* SECTIONS. Clean headers for the workbook's blocks (the draft's 21 UUID headers had no labels, six
  had no items and 21 non-current-asset items sat under a current-assets header). The caption gate
  is set per line: P&L income lines resolve to the ``income`` banner token, expense lines to
  ``expenses``, tax lines to ``tax_expense``, profit lines to all of them - so a row printed under a
  Schedule III "Expenses" or "Tax expense" heading can still be claimed. The statement totals
  (total outside liabilities, total liabilities and net worth, total assets) are ``bs_top_level``.
* CAPTIONS come from ``sample_data/icon/captions.json``, proposed per line from Schedule III and
  Revised Schedule VI wording and reviewed; each line's regex hint additionally lets its captions
  bind behind a leading enumerator or "Less:"/"Add:" ("IV. Expenses", "Less: Excise duty",
  "Profit before tax (V-VI)"), which the matcher does not strip.
* POLICY BLOCKS are the Ind AS set's (lakh/crore units, Standalone/Consolidated, the residual
  framework), except the sign convention above. One catch-all per balance-sheet section sweeps the
  rows no line claims; the P&L has none.
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

SRC = BACKEND / "scripts" / "sample_data" / "icon"
OUT = BACKEND / "app" / "sample" / "templates"
INDAS = OUT / "output_csv_indas_line_items.json"

TEMPLATE_KEY = "output_csv_icon_v1"
LINE_ITEMS_KEY = "output_csv_icon"

PREFIX = {"profit_and_loss": "pl_icon", "balance_sheet": "bs_icon",
          "covenants_supplemental": "cov_icon"}

# order -> readable slug, where the label's own slug is unclear or collides.
KEY_OVERRIDES = {
    3: "gross_sales_total", 4: "excise_duty", 6: "net_sales_growth_pct",
    7: "other_recurring_operating_income", 10: "raw_materials_imported",
    11: "raw_materials_indigenous", 14: "direct_labour", 15: "other_manufacturing_expenses",
    16: "processing_job_work_charges", 18: "other_manufacturing_expenses_others",
    22: "total_cost_of_sales", 23: "sga_expenses", 24: "salary_and_staff_expenses",
    25: "rent_rates_and_taxes", 28: "travelling_and_conveyance",
    29: "advertisement_and_sales_promotion", 34: "sga_others", 35: "total_operating_expenses",
    38: "interest_term_loans", 39: "interest_working_capital_loans", 43: "dividends_received",
    44: "profit_on_sale_of_assets_investments", 46: "non_operating_income_cash_subtotal",
    47: "non_operating_income_non_cash_others", 48: "non_operating_income_non_cash_subtotal",
    49: "non_operating_income_total", 53: "loss_on_sale_of_assets_investments",
    54: "other_non_operating_cash_expenses", 55: "other_non_operating_cash_expenses_2",
    56: "non_operating_expense_cash_subtotal", 58: "non_operating_expense_non_cash_others",
    59: "non_operating_expense_non_cash_subtotal", 60: "non_operating_expense_total",
    61: "net_non_operating_income", 62: "profit_before_tax", 63: "provision_for_taxes",
    66: "profit_after_tax", 67: "equity_dividend", 68: "dividend_rate_pct",
    70: "retained_to_net_profit_pct", 71: "bank_borrowings_applicant_bank",
    72: "bank_borrowings_other_banks", 73: "bank_borrowings_subtotal",
    74: "short_term_borrowings_others", 75: "short_term_borrowings_related_parties",
    76: "sundry_creditors_trade", 77: "advances_from_customers",
    80: "other_statutory_liabilities", 81: "cpltd", 82: "cpltd_debentures",
    83: "cpltd_preference_shares", 84: "cpltd_rupee_term_loans",
    85: "cpltd_foreign_currency_loans", 86: "cpltd_others",
    87: "other_current_liabilities_and_provisions", 90: "mtm_forex_loss_liability",
    93: "other_current_liabilities_others", 94: "other_current_liabilities_subtotal",
    96: "debentures_and_preference_shares", 97: "lease_liabilities", 98: "rupee_term_loans",
    99: "foreign_currency_loans", 100: "term_deposits",
    101: "long_term_borrowings_related_parties", 102: "other_term_liabilities_non_debt",
    105: "share_capital", 108: "other_reserves", 109: "surplus_or_deficit_in_profit_and_loss",
    116: "net_worth_others", 118: "total_liabilities_and_net_worth",
    122: "current_investments", 123: "govt_and_trustee_securities", 125: "receivables",
    128: "deferred_receivables_due_within_1_year", 130: "inventory_raw_materials",
    131: "inventory_raw_materials_imported", 132: "inventory_raw_materials_indigenous",
    133: "stocks_in_process", 135: "other_consumable_spares", 136: "advances_to_suppliers",
    138: "other_current_assets", 142: "other_advances_recoverable",
    143: "other_current_assets_others", 146: "land_building_plant_machinery",
    151: "group_investments", 152: "group_loans_and_advances",
    153: "other_non_current_investments_and_advances", 155: "other_non_current_assets",
    157: "dues_from_directors_promoters", 160: "retention_money_non_current_portion",
    161: "other_non_current_assets_others", 163: "intangible_assets",
    164: "accumulated_amortization", 167: "difference_in_bs_check", 168: "difference_in_bs",
    169: "tangible_net_worth", 172: "tol_to_tnw", 173: "ttl_to_tnw",
    176: "gratuity_not_provided", 177: "disputed_excise_customs_tax",
    178: "other_liabilities_not_provided", 180: "bills_discounted_and_purchased",
    182: "financial_guarantees_issued", 183: "redemption_premium_not_provided",
    184: "mtm_loss_on_derivatives_not_provided", 185: "losses_crystallized_outside_pl",
    186: "accumulated_depreciation_on_disposals", 187: "accumulated_amortization_on_disposals",
    188: "write_off_stock_in_process", 189: "write_off_finished_goods",
}

# Rows the template does not carry as lines (see the module docstring): they are KPIs, an
# identity, or not expressible.
NOT_LINES = {6, 68, 70, 167, 171, 172, 173}

# (header key, label, statement, orders, default section_scope, sign expectation). A section the
# workbook heads with a row of its own carries that row's words ("CURRENT LIABILITIES", "Add: Other
# non-operating Income"); the others keep a plain name, because the workbook prints no heading
# over them.
SECTIONS = [
    ("pl_icon_sales", "Sales", "profit_and_loss", range(1, 9), ["pl_income"], "either"),
    ("pl_icon_cost_of_sales", "Cost of Sales", "profit_and_loss", range(9, 23), ["pl_expenses"], "either"),
    ("pl_icon_sga", "Selling, General and Administrative Expenses", "profit_and_loss",
     range(23, 35), ["pl_expenses"], "either"),
    ("pl_icon_operating_profit", "Operating Profit and Interest", "profit_and_loss",
     range(35, 42), ["pl_expenses"], "either"),
    ("pl_icon_non_operating_income", "Add: Other non-operating Income", "profit_and_loss", range(42, 50),
     ["pl_income"], "either"),
    ("pl_icon_non_operating_expenses", "Deduct: Other non-operating expenses", "profit_and_loss",
     range(50, 62), ["pl_expenses"], "either"),
    ("pl_icon_profit", "Profit and Appropriation", "profit_and_loss", range(62, 71),
     ["pl_tax_expense", "pl_expenses"], "either"),
    ("bs_icon_current_liabilities", "CURRENT LIABILITIES", "balance_sheet", range(71, 96),
     ["bs_cl"], "positive_expected"),
    ("bs_icon_term_liabilities", "TERM LIABILITIES", "balance_sheet", range(96, 105),
     ["bs_ncl"], "positive_expected"),
    ("bs_icon_net_worth", "NET WORTH", "balance_sheet", range(105, 119), ["bs_equity"], "either"),
    ("bs_icon_current_assets", "CURRENT ASSETS", "balance_sheet", range(119, 145), ["bs_ca"],
     "positive_expected"),
    ("bs_icon_fixed_assets", "FIXED ASSETS", "balance_sheet", range(145, 150), ["bs_nca"],
     "positive_expected"),
    ("bs_icon_non_current_assets", "OTHER NON-CURRENT ASSETS", "balance_sheet", range(150, 163),
     ["bs_nca"], "positive_expected"),
    ("bs_icon_intangible_assets", "Intangible Assets", "balance_sheet", range(163, 166),
     ["bs_nca"], "positive_expected"),
    ("bs_icon_key_figures", "Total Assets and Key Figures", "balance_sheet", range(166, 174),
     ["bs_top_level"], "either"),
    ("cov_icon_contingent_liabilities", "Contingent Liabilities", "covenants_supplemental",
     range(174, 185), ["bs_top_level"], "either"),
    ("cov_icon_other_information", "Other Information", "covenants_supplemental",
     range(185, 190), ["bs_top_level"], "either"),
]

# THE WORKBOOK'S OWN SHEET NAMES. ICON's output is the Company v3 workbook itself (see
# `WORKBOOK`), so the template names its three statements the way the workbook's tabs do.
STATEMENT_LABELS = {"profit_and_loss": "Profit and Loss Statement",
                    "balance_sheet": "Balance Sheet",
                    "covenants_supplemental": "Additional Information"}

ALL_PL = ["pl_income", "pl_expenses", "pl_tax_expense", "pl_exceptional_items", "is_pl"]
# Per-line caption gates that differ from their section's.
SCOPE_OVERRIDES = {
    "pl_icon__profit_before_tax": ALL_PL, "pl_icon__profit_after_tax": ALL_PL,
    "pl_icon__equity_dividend": ALL_PL, "pl_icon__retained_profit": ALL_PL,
    "pl_icon__operating_profit_before_interest": ALL_PL,
    "pl_icon__operating_profit_after_interest": ALL_PL,
    "bs_icon__total_outside_liabilities": ["bs_top_level"],
    "bs_icon__total_liabilities_and_net_worth": ["bs_top_level"],
}
# Lines a row may also be claimed for from the balance sheet: a contingent-liabilities note the
# balance sheet cites carries the balance sheet's statement.
EXTRA_STATEMENTS = {"covenants_supplemental": ["covenants_supplemental", "balance_sheet"]}

# Terms that subtract a magnitude: never negative, but may be printed in brackets or after "Less:".
MAGNITUDE = {"pl_icon__excise_duty", "pl_icon__total_operating_expenses", "pl_icon__interest",
             "pl_icon__equity_dividend", "bs_icon__accumulated_depreciation",
             "bs_icon__accumulated_amortization"}
# Contra balances stored negative, so a section's lines add up to its closing total.
NATURAL_NEGATIVE = {"bs_icon__accumulated_depreciation", "bs_icon__accumulated_amortization"}

# Rollups that differ from the draft's terms (see the module docstring), as (op, children).
ROLLUP_OVERRIDES = {
    "bs_icon__total_current_assets": ("sum", [
        "bs_icon__cash_and_bank_balances", "bs_icon__govt_and_trustee_securities",
        "bs_icon__fixed_deposits_with_banks", "bs_icon__receivables",
        "bs_icon__deferred_receivables_due_within_1_year", "bs_icon__inventory",
        "bs_icon__advances_to_suppliers", "bs_icon__advance_payment_of_taxes",
        "bs_icon__other_current_assets"]),
    "bs_icon__inventory": ("sum", [
        "bs_icon__inventory_raw_materials", "bs_icon__stocks_in_process",
        "bs_icon__finished_goods", "bs_icon__other_consumable_spares"]),
    "bs_icon__total_other_non_current_assets": ("sum", [
        "bs_icon__exposure_in_group_entities",
        "bs_icon__other_non_current_investments_and_advances",
        "bs_icon__advances_to_suppliers_of_capital_goods", "bs_icon__other_non_current_assets"]),
    # Row 24 heading over (i) securities and (ii) deposits: the workbook prints no formula for it
    # and leaves it out of Total Current Assets, because its two parts are in that total already.
    "bs_icon__current_investments": ("sum", [
        "bs_icon__govt_and_trustee_securities", "bs_icon__fixed_deposits_with_banks"]),
    # =SUM(F61:F73): the twelve net-worth rows (row 66 is the "24 Others" heading).
    "bs_icon__net_worth": ("sum", [
        "bs_icon__share_capital", "bs_icon__general_reserve", "bs_icon__revaluation_reserve",
        "bs_icon__other_reserves", "bs_icon__surplus_or_deficit_in_profit_and_loss",
        "bs_icon__share_premium", "bs_icon__capital_redemption_reserve",
        "bs_icon__share_application_money", "bs_icon__foreign_currency_translation_reserve",
        "bs_icon__minority_interest", "bs_icon__quasi_equity", "bs_icon__net_worth_others"]),
    # =IF(F9="",0,F131-F75): total assets less total liabilities and net worth.
    "bs_icon__difference_in_bs": ("diff", [
        "bs_icon__total_assets", "bs_icon__total_liabilities_and_net_worth"]),
}

# Computed only: never matched by caption, never asked about.
DERIVE = {
    "pl_icon__total_cost_of_sales", "pl_icon__total_operating_expenses",
    "pl_icon__non_operating_income_cash_subtotal", "pl_icon__non_operating_income_non_cash_subtotal",
    "pl_icon__non_operating_income_total", "pl_icon__non_operating_expense_cash_subtotal",
    "pl_icon__non_operating_expense_non_cash_subtotal", "pl_icon__non_operating_expense_total",
    "pl_icon__net_non_operating_income", "pl_icon__retained_profit",
    "bs_icon__bank_borrowings_subtotal", "bs_icon__other_current_liabilities_subtotal",
    "bs_icon__current_investments", "bs_icon__inventory_raw_materials", "bs_icon__gross_block",
    "bs_icon__net_block", "bs_icon__exposure_in_group_entities", "bs_icon__other_non_current_assets",
    "bs_icon__total_other_non_current_assets", "bs_icon__net_intangible_assets",
    "bs_icon__difference_in_bs", "bs_icon__tangible_net_worth", "bs_icon__net_working_capital",
    "cov_icon__write_off_stock_in_process", "cov_icon__write_off_finished_goods",
}
# One catch-all per balance-sheet section: the rows no line claims are swept here, itemised.
CATCH_ALLS = ["bs_icon__other_current_liabilities_others", "bs_icon__net_worth_others",
              "bs_icon__other_current_assets_others", "bs_icon__other_non_current_assets_others"]
# Rows a catch-all never takes. A Schedule III face prints totals ICON does not carry ("Total
# non-current assets", "Total equity") and its column header can surface as a row ("Particulars"
# with the year as its figure); swept, either is counted twice. "Total outstanding dues of ..." is
# the MSME creditor caption, not a total.
_SWEEP_EXCLUDES = [r"^\W*(?:sub[\s-]*)?total\b(?!\s+outstanding)", r"^\W*particulars\b"]
# Calculated lines whose printed figure is read off the filing and compared with the computed one.
CROSS_CHECK = [
    "pl_icon__net_sales", "pl_icon__total_operating_income", "pl_icon__raw_materials",
    "pl_icon__interest", "pl_icon__provision_for_taxes", "pl_icon__profit_before_tax",
    "pl_icon__profit_after_tax", "bs_icon__cash_and_bank_balances", "bs_icon__receivables",
    "bs_icon__inventory", "bs_icon__total_current_liabilities", "bs_icon__total_term_liabilities",
    "bs_icon__net_worth", "bs_icon__total_current_assets", "bs_icon__total_assets",
    "bs_icon__total_outside_liabilities", "bs_icon__total_liabilities_and_net_worth",
    "cov_icon__contingent_liabilities",
]
STATEMENT_TOTALS = {"bs_icon__total_outside_liabilities", "bs_icon__total_liabilities_and_net_worth",
                    "bs_icon__total_assets", "pl_icon__profit_after_tax"}
# Analyst buckets the draft set wrong.
BUCKETS = {"pl_icon__gross_sales_total": "income", "pl_icon__operating_profit_before_interest": "others",
           "pl_icon__total_operating_expenses": "expenses",
           "pl_icon__operating_profit_after_interest": "others", "pl_icon__profit_before_tax": "others",
           "pl_icon__profit_after_tax": "others", "pl_icon__retained_profit": "others",
           "bs_icon__total_assets": "others", "bs_icon__total_liabilities_and_net_worth": "others",
           "bs_icon__total_outside_liabilities": "others"}

# Definitions that replace the draft's, and conventions appended to a definition so the model and a
# reviewer can see why a caption binds where it does.
DEFINITION_OVERRIDES = {
    "bs_icon__net_worth": "Net worth on the CMA basis: share capital, every reserve and the surplus "
                          "or deficit, share application money, minority interest, quasi equity "
                          "and other net-worth items, as the template computes it.",
    "bs_icon__current_investments": "Investments other than long-term: government and other trustee "
                                    "securities plus fixed deposits with banks, as the template "
                                    "computes it. A heading over its two parts, which Total Current "
                                    "Assets counts directly.",
    "bs_icon__difference_in_bs": "Total assets less total liabilities and net worth. Zero when the "
                                 "balance sheet balances; a non-zero figure is a gap to review, and "
                                 "a figure equal to total assets means the liabilities side was not "
                                 "read.",
}
APPEND_NOTES = {
    "pl_icon__misc_income": "When the filing prints only 'Other income', this line carries it; its "
                            "note splits interest, dividends, disposal gains and amounts written "
                            "back into their own lines and leaves the remainder here.",
    "pl_icon__sga_others": "When the filing prints only 'Other expenses', this line carries it; its "
                           "note splits power, stores, freight, rent, travel and the other named "
                           "expenses into their own lines and leaves the remainder here.",
    "bs_icon__other_reserves": "When the filing prints only 'Other equity' or 'Reserves and "
                               "surplus', this line carries it; its note splits the general reserve, "
                               "securities premium, retained earnings and the other named reserves "
                               "into their own lines and leaves the remainder here.",
    "bs_icon__bank_borrowings_applicant_bank": "Current 'Borrowings' bind here by convention: the "
                                              "filing does not say which bank applied. Its note "
                                              "splits current maturities, inter-corporate deposits "
                                              "and related-party loans into their own lines; a "
                                              "reviewer moves other banks' share to the next line.",
    "bs_icon__rupee_term_loans": "Non-current 'Borrowings' bind here by convention; its note splits "
                                 "debentures, foreign-currency loans, deposits and related-party "
                                 "loans into their own lines.",
    "bs_icon__land_building_plant_machinery": "When the filing prints only net property, plant and "
                                              "equipment, this line carries the net figure and "
                                              "accumulated depreciation stays blank, so Net Block "
                                              "is still right; a reviewer can restate gross and "
                                              "accumulated depreciation from the asset note.",
    "bs_icon__fixed_deposits_with_banks": "'Bank balances other than cash and cash equivalents' "
                                          "bind here: they are mostly deposits of three to twelve "
                                          "months. Margin money and deposits under lien are not.",
    "bs_icon__govt_and_trustee_securities": "Current investments, including units of mutual funds, "
                                            "bind here.",
    "pl_icon__salary_and_staff_expenses": "'Employee benefits expense' binds here when the filing "
                                          "does not separate factory wages.",
}

KPIS = {"intermediates": [], "ratios": [
    {"key": "current_ratio", "label": "Current Ratio", "label_i18n": {"en": "Current Ratio"},
     "category": "Liquidity", "unit": "x",
     "numerator": [{"key": "bs_icon__total_current_assets"}],
     "denominator": [{"key": "bs_icon__total_current_liabilities"}]},
    {"key": "tol_to_tnw", "label": "Total Outside Liabilities / Tangible Net Worth",
     "label_i18n": {"en": "Total Outside Liabilities / Tangible Net Worth"},
     "category": "Leverage", "unit": "x",
     "numerator": [{"key": "bs_icon__total_outside_liabilities"}],
     "denominator": [{"key": "bs_icon__tangible_net_worth"}]},
    {"key": "ttl_to_tnw", "label": "Total Term Liabilities / Tangible Net Worth",
     "label_i18n": {"en": "Total Term Liabilities / Tangible Net Worth"},
     "category": "Leverage", "unit": "x",
     "numerator": [{"key": "bs_icon__total_term_liabilities"}],
     "denominator": [{"key": "bs_icon__tangible_net_worth"}]},
    {"key": "retained_profit_to_net_profit", "label": "Retained Profit / Net Profit",
     "label_i18n": {"en": "Retained Profit / Net Profit"}, "category": "Profitability", "unit": "%",
     "numerator": [{"key": "pl_icon__retained_profit"}],
     "denominator": [{"key": "pl_icon__profit_after_tax"}]},
    {"key": "dividend_rate", "label": "Dividend Rate", "label_i18n": {"en": "Dividend Rate"},
     "category": "Profitability", "unit": "%",
     "numerator": [{"key": "pl_icon__equity_dividend"}],
     "denominator": [{"key": "bs_icon__share_capital"}]},
]}

PROMPT = (
    "ICON is an Indian bank credit-monitoring (CMA-style) spread: an operating statement, an "
    "analysis of the balance sheet, and contingent liabilities with other information, filled from "
    "a borrower's financial statements. Filings may follow Ind AS (Schedule III, Division II) or "
    "Indian GAAP (Revised Schedule VI), and are often scaled in lakhs or crores; do not assume "
    "either from the units alone. Extract only figures supported by filing citations. Keep "
    "standalone and consolidated reporting separate. Preserve reporting date, period duration, "
    "presentation currency and scale. Distinguish raw-material consumption from closing "
    "inventory, period charges from accumulated balances, funded borrowing from guarantees, and "
    "recognised liabilities from unprovided exposure. Do not treat unavailable as zero or "
    "calculate amounts in a citation-only answer.")


# ── the workbook ICON's output is written into ─────────────────────────────────────────────────
# THE COMPANY V3 WORKBOOK ITSELF, stored in app/sample/workbooks/, is the output format:
# `services.export_workbook` fills a copy of it. Its first three sheets are the template's three
# statements; its hidden "Ratios" sheet and its "Financial Spreading & Analysis" sheet are not
# ICON's and are left as they are. Every row it fills is found by the UUID in its column A, which is
# the key the draft was written with, so the map below is read off the draft and the workbook
# together and can be checked against both.
# Beside the templates rather than among them: `scripts/validate_reference_data.py` reads every
# JSON file there as reference data, and the workbook and its map are an output layout.
WORKBOOKS = BACKEND / "app" / "sample" / "workbooks"
WORKBOOK = WORKBOOKS / f"{TEMPLATE_KEY}.xlsx"
WORKBOOK_SHEETS = ("Profit and Loss Statement", "Balance Sheet", "Additional Information")
# The workbook's non-monetary rows that the template carries as KPIs (see NOT_LINES).
KPI_ORDERS = {68: "dividend_rate", 70: "retained_profit_to_net_profit", 171: "current_ratio",
              172: "tol_to_tnw", 173: "ttl_to_tnw"}
# A TOTAL THE WORKBOOK BYPASSES. Total Current Assets adds Domestic and Export Receivables, not the
# Receivables row above them, and Inventory adds Imported and Indigenous raw materials, not the Raw
# materials row. So a filing that prints only the group's figure has to enter it on one of the
# group's own rows to reach those totals — the row the line's definition already names for that
# case ("all current trade receivables are domestic", "all raw-material stock when no imported /
# indigenous split is given").
PRINTED_ONLY_TO = {"bs_icon__receivables": "bs_icon__domestic_receivables",
                   "bs_icon__inventory_raw_materials": "bs_icon__inventory_raw_materials_indigenous"}


def _workbook_map(draft: dict, keys: dict[str, str], labels: dict[str, str]) -> dict:
    """Where each ICON line, KPI and header field sits in the Company v3 workbook."""
    import openpyxl

    book = openpyxl.load_workbook(WORKBOOK)
    head = book[WORKBOOK_SHEETS[0]]
    header = {}
    for r in range(1, 17):
        name = str(head.cell(r, 5).value or "").split(" (")[0].strip()
        if name:
            header[name] = r
    where = {}
    for title in WORKBOOK_SHEETS:
        ws = book[title]
        for r in range(19, ws.max_row + 1):
            uuid = ws.cell(r, 1).value
            if not uuid:
                continue
            cell = ws.cell(r, 6)
            formula = isinstance(cell.value, str) and cell.value.startswith("=")
            where[uuid] = {"sheet": title, "row": r,
                           "sequence": str(ws.cell(r, 3).value or ""),
                           "label": re.sub(r"\s+", " ", str(ws.cell(r, 5).value or "")).strip(),
                           "input": not formula and not cell.protection.locked}
    rows: dict[str, dict] = {}
    for item in draft["items"]:
        spot = where.get(item["key"])
        assert spot is not None, f"draft row {item['order']} has no row in the workbook"
        if item["order"] in NOT_LINES:
            if item["order"] not in KPI_ORDERS:
                continue
            name = f"kpi:{KPI_ORDERS[item['order']]}"
        else:
            name = keys[item["key"]]
            assert spot["label"] == labels[name], (name, spot["label"], labels[name])
        rows[name] = {"uuid": item["key"], **{k: spot[k] for k in ("sheet", "row", "sequence",
                                                                   "input")}}
    return {
        "note": "Built by scripts/build_icon_pair.py. Where each ICON line sits in the Company v3 "
                "workbook its output is written into: the workbook UUID (column A), the sheet, "
                "the row, AUTO_SEQUENCE (column C) and whether the row is an input (unlocked, no "
                "formula) or computed by the workbook's own formula.",
        "template_key": TEMPLATE_KEY,
        "workbook": WORKBOOK.name,
        "sheets": list(WORKBOOK_SHEETS),
        "header": {"sheet": WORKBOOK_SHEETS[0], "first_value_column": "F", "periods": 30,
                   "rows": header},
        "rows": rows,
        "printed_only_to": PRINTED_ONLY_TO,
    }


def _slug(label: str) -> str:
    label = re.sub(r"\[[^\]]*\]|\((?:\d[^)]*|[ivx]+|A|B|PBT|PAT|% age)\)", "", label)
    label = re.sub(r"^\s*[ivx]+\)\s*", "", label.strip()).replace("&", " and ").replace("%", "pct")
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")




ROUTES = ("face", "note_tables", "prose", "anywhere")


def _prose(text) -> str:
    """Authored prose as the model is sent it: one paragraph, and no braces ("{Others}" is the
    workbook's label for a catch-all row; braces in prose read as a key reference)."""
    return re.sub(r"\s+", " ", str(text or "").replace("{", "").replace("}", "")).strip()


def _definition(item: dict, label: str, terms_text: str | None, residual_note: str | None) -> str:
    text = item.get("definition") or ""
    text = re.sub(r"\s*Source Excel formula for slot 1:.*$", "", text, flags=re.S)
    text = re.sub(r"\s*The signed terms below are a source-formula proposal only:.*$", "", text,
                  flags=re.S)
    text = text.replace("this draft does not enable an automatic residual sweep.", "").strip()
    m = re.match(r"Aggregate (.+?) on the workbook formula basis\. Components and signed "
                 r"calculation are: (.+?)\. (.*)$", text, re.S)
    if m and terms_text:
        text = f"{label}: {terms_text}, as the template computes it. {m.group(3)}"
    if residual_note:
        text = f"{text.rstrip('. ')}. {residual_note}"
    # "{Others}" is the workbook's label for a catch-all row; braces in prose read as a key reference.
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


# Schedule III also numbers an INSERTED item with a letter after the roman: "(ia) Lease
# liabilities", "(iia)". Without the letter the current lease liability matched no caption.
_ENUM = r"(?:(?:[ivxl]{1,6}[a-h]?|[a-h]|\d{1,2}[a-h]?)\s+){0,2}"

# SCHEDULE III HEADINGS THAT PRINT NO FIGURE, and that row reconstruction glues onto the row next to
# them. Measured on a Schedule III balance sheet and P&L laid out as the Division II format prints
# them: "(d) Financial assets" / "(i) Investments" reaches the matcher as "d financial assets i
# investments", "Equity" / "(a) Equity share capital" as "equity a equity share capital", "IV.
# Expenses" / "Cost of materials consumed" as "iv expenses cost of materials consumed", and "Total
# equity" / "Liabilities" as "total equity liabilities". The Ind AS set loses the same rows. The
# gluing is the engine's (row_reconstruct), and changing it moves every filing, so until that is
# measured on the reference corpus the ICON hints read through a glued heading instead.
_GLUED_BEFORE = ("financial assets", "financial liabilities", "equity", "expenses", "liabilities",
                 "tax expense", "tax expenses")
_GLUED_AFTER = ("liabilities",)


def _alternation(phrases) -> str:
    return "|".join(r"\s+".join(re.escape(w) for w in p.split())
                    for p in sorted(set(phrases), key=lambda p: (-len(p), p)))


def _regex_hint(aliases: list[str]) -> list[str]:
    """One rule-tier regex binding the line's own captions behind a leading enumerator, a
    "Less:"/"Add:", a glued Schedule III heading (`_GLUED_BEFORE` / `_GLUED_AFTER`) or a trailing
    roman cross-reference, on the matcher's normalised text."""
    from app.services.mapping import normalize_label

    alts = {normalize_label(a) for a in aliases if normalize_label(a)}
    if not alts:
        return []
    before = rf"(?:(?:{_alternation(_GLUED_BEFORE)})\s+{_ENUM})?"
    after = rf"(?:\s+(?:{_alternation(_GLUED_AFTER)}))?"
    return [rf"^{_ENUM}{before}(?:less\s+|add\s+)?(?:{_alternation(alts)})"
            rf"(?:\s+(?:[ivxl]{{1,5}}|\d{{1,2}}))*{after}$"]


# A spelling that normalises to what a GLUED ROW of another line reads as. "Total equity &
# liabilities" normalises to "total equity liabilities", which is exactly "Total equity" with the
# "Liabilities" heading glued on; as an alias of the statement total it took the equity subtotal at
# the exact tier, ahead of net worth's own hint. Such a spelling is matched on the PRINTED text
# instead (`_printed_hint`), where the "&" is still there to tell the two apart.
_GLUE_COLLISIONS = {"total equity liabilities"}


def _variants(aliases: list[str]) -> tuple[list[str], list[str]]:
    """Both spellings of "&"/"and" (normalisation turns "&" into a space, so they differ), split
    into the aliases and the spellings that collide with a glued row (`_GLUE_COLLISIONS`)."""
    from app.services.mapping import normalize_label

    out: list[str] = []
    held: list[str] = []
    for a in aliases:
        for v in (a, a.replace(" & ", " and "), re.sub(r"\band\b", "&", a)):
            v = re.sub(r"\s+", " ", v).strip()
            if not v or v in out or v in held:
                continue
            (held if normalize_label(v) in _GLUE_COLLISIONS else out).append(v)
    return out, held


def _with_variants(aliases: list[str]) -> list[str]:
    return _variants(aliases)[0]


def _printed_hint(spelling: str) -> str:
    """A rule-tier regex for one spelling AS PRINTED (the matcher also runs hints on the raw,
    lowercased caption), so its "&" survives."""
    words = [r"\s*&\s*" if w == "&" else re.escape(w.lower()) for w in spelling.split()]
    body = ""
    for w in words:
        body += w if (w.startswith(r"\s*") or body.endswith(r"\s*")) else (r"\s+" + w if body else w)
    return rf"^\W*{body}\W*$"


def build() -> tuple[dict, dict, dict]:
    draft = json.loads((SRC / "company_v3_line_items_draft.json").read_text(encoding="utf-8"))
    captions = json.loads((SRC / "captions.json").read_text(encoding="utf-8"))
    indas = json.loads(INDAS.read_text(encoding="utf-8"))

    keys = {i["key"]: f"{PREFIX[i['statements'][0]]}__{KEY_OVERRIDES.get(i['order'], _slug(i['label']))}"
            for i in draft["items"]}
    assert len(set(keys.values())) == len(keys), "duplicate ICON keys"
    by_order = {i["order"]: i for i in draft["items"]}
    labels = {keys[i["key"]]: re.sub(r"\s+", " ", i["label"]).strip() for i in draft["items"]}

    # Rollups: the draft's terms, or the override.
    rollups: dict[str, tuple[str, list[str]]] = {}
    for i in draft["items"]:
        k = keys[i["key"]]
        if i["order"] in NOT_LINES or not i.get("terms"):
            continue
        signs = [t["sign"] for t in i["terms"]]
        children = [keys[t["ref"]] for t in i["terms"]]
        if all(s == 1 for s in signs):
            rollups[k] = ("sum", children)
        else:
            assert signs[0] == 1 and all(s == -1 for s in signs[1:]), (k, signs)
            rollups[k] = ("diff", children)
    rollups.update(ROLLUP_OVERRIDES)

    section_of: dict[int, tuple] = {}
    for sec in SECTIONS:
        for o in sec[3]:
            section_of[o] = sec

    # ── template ────────────────────────────────────────────────────────────────────────────────
    statements = []
    for st_type in ("profit_and_loss", "balance_sheet", "covenants_supplemental"):
        st = {"type": st_type, "label": STATEMENT_LABELS[st_type],
              "label_i18n": {"en": STATEMENT_LABELS[st_type]}, "sections": []}
        for hdr, hlabel, stt, orders, _scope, _sign in SECTIONS:
            if stt != st_type:
                continue
            children = []
            for o in orders:
                if o in NOT_LINES:
                    continue
                k = keys[by_order[o]["key"]]
                node = {"node_id": k, "canonical_key": k, "label": labels[k],
                        "label_i18n": {"en": labels[k]}}
                if k in rollups:
                    op, kids = rollups[k]
                    node["role"] = "total" if k in STATEMENT_TOTALS else "subtotal"
                    roll = {"op": op, "children": kids}
                    mags = [c for c in kids[1:] if c in MAGNITUDE] if op == "diff" else []
                    if mags:
                        roll["cost_magnitude_children"] = mags
                    node["rollup"] = roll
                else:
                    node["role"] = "line"
                if k in NATURAL_NEGATIVE:
                    node["sign"] = "natural_negative"
                children.append(node)
            st["sections"].append({"node_id": hdr, "canonical_key": hdr, "label": hlabel,
                                   "label_i18n": {"en": hlabel}, "role": "header",
                                   "children": children})
        if st_type == "balance_sheet":
            st["identities"] = [{"id": "bs_icon_balances", "lhs": "bs_icon__total_assets",
                                 "rhs": {"op": "sum",
                                         "children": ["bs_icon__total_liabilities_and_net_worth"]}}]
        statements.append(st)
    template = {"schema_version": 1, "template_key": TEMPLATE_KEY, "name": "ICON",
                "statements": statements, "kpis": KPIS}

    # ── line-item set ───────────────────────────────────────────────────────────────────────────
    section_defaults = {}
    for hdr, _hl, stt, _orders, scope, sign in SECTIONS:
        sd = {"statement": stt, "section_scope": list(scope),
              "temporality": "duration" if stt == "profit_and_loss" else "instant",
              "sign_convention": sign}
        section_defaults[hdr] = sd

    items = []
    for i in draft["items"]:
        if i["order"] in NOT_LINES:
            continue
        k = keys[i["key"]]
        hdr, _hl, stt, _orders, scope, _sign = section_of[i["order"]]
        cap = captions.get(k, {})
        calculated = k in rollups
        terms = None
        terms_text = None
        if calculated:
            op, kids = rollups[k]
            roll_mags = {c for c in kids[1:] if c in MAGNITUDE} if op == "diff" else set()
            terms = []
            for n, c in enumerate(kids):
                t = {"ref": c, "sign": -1 if (op == "diff" and n > 0) else 1}
                if c in roll_mags:
                    t["abs"] = True
                terms.append(t)
            names = [labels[c] for c in kids]
            if op == "sum":
                terms_text = "the sum of " + ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
            else:
                terms_text = names[0] + " less " + " less ".join(names[1:])
        residual_note = ("Rows of this section that no other line claims are swept here, "
                         "itemised.") if k in CATCH_ALLS else None
        item = {
            "key": k, "label": labels[k],
            "type": "calculated" if calculated else "extracted",
            "namespace": "template", "in_output": True, "inherits": hdr,
            "statements": EXTRA_STATEMENTS.get(stt, [stt]),
            "section_scope": SCOPE_OVERRIDES.get(k, list(scope)),
            "order": i["order"],
            # THE REVIEWED DEFINITION FIRST: it is what the model reads to locate the line's figure
            # (`services.line_item_llm.line_item_payload` sends no alias), so it is curated with the
            # captions, per line, in `captions.json`.
            "definition": (_prose(cap.get("definition")) or DEFINITION_OVERRIDES.get(k)
                           or _definition(i, labels[k], terms_text, residual_note)),
            "temporality": i.get("temporality"),
            "unit_of_account": i.get("unit_of_account"),
            "sign_convention": i.get("sign_convention"),
            "analyst_bucket": BUCKETS.get(k, i.get("analyst_bucket")),
            "match_priority": 80,
        }
        if k in APPEND_NOTES and not cap.get("definition"):
            item["definition"] = f"{item['definition'].rstrip('. ')}. {APPEND_NOTES[k]}"
        # "(none)" is a reviewed decision that the line has no route: face and notes both.
        if cap.get("route") in ROUTES:
            item["route"] = cap["route"]
        if cap.get("exclude_criteria"):
            item["exclude_criteria"] = [_prose(x) for x in cap["exclude_criteria"] if _prose(x)]
        # WHICH NOTES THE MODEL IS SHOWN. Only a line declaring a `note_source` has its notes' TEXT
        # attached to its request (`services.note_context.identified_notes`); without one the
        # request names the notes and carries none of them. Heading words only — no row rules, so
        # the deterministic note reader reads nothing on their account.
        if cap.get("note_terms"):
            item["note_source"] = {"note_terms": list(cap["note_terms"])}
        if calculated:
            item["extraction_mode"] = "derive" if k in DERIVE else "extract_or_derive"
            item["terms"] = terms
            item["match_priority"] = 99 if k in STATEMENT_TOTALS else 90
        aliases, held = (([], []) if k in DERIVE or k in CATCH_ALLS
                         else _variants(cap.get("aliases", [])))
        if aliases:
            item["aliases"] = aliases
            item["aliases_i18n"] = {"en": list(aliases)}
            item["regex_hints"] = _regex_hint(aliases) + [_printed_hint(h) for h in held]
            if cap.get("exclude_hints"):
                item["exclude_hints"] = list(cap["exclude_hints"])
        if k in CATCH_ALLS:
            # THE SECTION THE SWEEP TAKES ITS ROWS FROM, stated rather than inherited. Left to the
            # `others_master` default it is the line's `inherits` — the ICON section id
            # (bs_icon_current_liabilities) — while the line scopes as the filing's section
            # (bs_cl), and `stages/residual` refuses a residual whose two scopes differ
            # ("never spans sections"): all four catch-alls swept nothing.
            (scope_id,) = item["section_scope"]
            item["residual_policy"] = {
                "framework": "residual_framework", "section_scope": scope_id,
                "population": "sweep_only", "cross_section": False, "notes_as_source": False,
                "plug": False, "itemise": True}
            item["exclude_hints"] = list(_SWEEP_EXCLUDES)
        items.append(item)

    # Section closing totals outrank the section's other calculated lines.
    for k in ("bs_icon__total_current_liabilities", "bs_icon__total_term_liabilities",
              "bs_icon__net_worth", "bs_icon__total_current_assets", "bs_icon__net_block",
              "bs_icon__total_other_non_current_assets", "bs_icon__net_intangible_assets",
              "cov_icon__contingent_liabilities", "pl_icon__total_operating_income",
              "pl_icon__total_cost_of_sales", "pl_icon__sga_expenses",
              "pl_icon__operating_profit_after_interest", "pl_icon__non_operating_income_total",
              "pl_icon__net_non_operating_income"):
        next(it for it in items if it["key"] == k)["match_priority"] = 95

    rules = copy.deepcopy(indas["global_rules"])
    rules["sign_convention"] = {
        "stored_value": "The reported value after paren-to-negative conversion.",
        "expenses_and_outflows": "Stored POSITIVE, as printed. The ICON template subtracts "
                                 "expense lines the way the CMA workbook does (Net Sales = Total "
                                 "less Excise duty; Operating Profit = Total operating income less "
                                 "operating expenses), so an expense is never negated on load.",
        "unsigned_source": "A filing that prints expenses as unsigned positives is read as "
                           "printed; nothing is negated.",
        "either": indas["global_rules"]["sign_convention"]["either"],
        "validation": indas["global_rules"]["sign_convention"]["validation"],
    }
    scope_sel = copy.deepcopy(indas["scope_selection"])
    markers = scope_sel.get("entity_scope", {}).get("company_only_markers", [])
    scope_sel["entity_scope"]["company_only_markers"] = [
        m.replace("bs_nca__investment_in_subsidiaries", "Investments in subsidiaries") for m in markers]

    line_items = {
        "schema_version": 1, "line_items_key": LINE_ITEMS_KEY,
        "target_template_key": TEMPLATE_KEY, "target_template_version": None,
        "locale": "en", "supported_locales": ["en"],
        "metadata": {
            "name": "ICON line items", "version": "1",
            "changes": [
                "Built by scripts/build_icon_pair.py from the Company v3 workbook draft "
                "(scripts/sample_data/icon/company_v3_line_items_draft.json) and the reviewed "
                "captions in scripts/sample_data/icon/captions.json.",
                "182 template lines; 7 non-monetary workbook rows are template KPIs, a "
                "balance-sheet identity, or not carried (net-sales growth).",
            ],
            "vocabulary_note": "English captions from Schedule III Division II and Revised "
                               "Schedule VI wording, reviewed per line. Not yet measured on an "
                               "Indian filing.",
        },
        "section_defaults": section_defaults,
        "vocabulary": copy.deepcopy(indas["vocabulary"]),
        "normalisation": copy.deepcopy(indas["normalisation"]),
        "binding": copy.deepcopy(indas["binding"]),
        "prompt": PROMPT,
        "global_rules": rules,
        "scope_selection": scope_sel,
        "residual_framework": copy.deepcopy(indas["residual_framework"]),
        "items": items,
        "others_master": {"note": "One catch-all per balance-sheet section; the P&L has none, "
                                  "so a P&L row no line claims is left for review.",
                          "keys": CATCH_ALLS},
        "cross_check_master": {"note": "Calculated ICON lines that filings print, whose printed "
                                       "figure is compared with the computed one.",
                               "keys": CROSS_CHECK, "tolerance": 0.5},
        "prose_grammar": copy.deepcopy(indas["prose_grammar"]),
    }
    workbook_map = _workbook_map(draft, keys, labels)
    return template, line_items, workbook_map


def main() -> None:
    template, line_items, workbook_map = build()
    (WORKBOOKS / f"{TEMPLATE_KEY}.json").write_text(
        json.dumps(workbook_map, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / f"{TEMPLATE_KEY}_template.json").write_text(
        json.dumps(template, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / f"{LINE_ITEMS_KEY}_line_items.json").write_text(
        json.dumps(line_items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(s["children"]) for st in template["statements"] for s in st["sections"])
    print(f"wrote {TEMPLATE_KEY}_template.json ({n} lines) and {LINE_ITEMS_KEY}_line_items.json "
          f"({len(line_items['items'])} items)")


if __name__ == "__main__":
    main()
