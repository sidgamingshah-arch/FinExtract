"""Build the US GAAP (Form 10-K) template and its line-item set: three statements and additional items.

    python scripts/build_usgaap_pair.py     # writes app/sample/templates/output_csv_usgaap_*

WHAT IT DECIDES, and why:

* THREE STATEMENTS, READ FROM THE FACE. The Consolidated Balance Sheets, the Consolidated
  Statements of Operations (or Income, or Earnings) and the Consolidated Statements of Cash Flows,
  each line one of the rows Regulation S-X Article 5 (Rules 5-02 and 5-03) and ASC 230's indirect
  method lead a 10-K to print. Every line declares ``route: face``, none carries a
  ``note_source``, and the set declares ``strict_face_route``, so no stage puts a note's figure on
  a line: a row the face does not print is left empty. The statement of comprehensive income, the
  statement of stockholders' equity, earnings per share and share counts are not carried.
* ADDITIONAL ITEMS, READ FROM THE NOTES AND THE AUDITOR'S REPORT, as a fourth statement of the
  template (`NOTE_LINES`): the supplemental cash flow disclosures, debt maturities, principal and
  fair value, the current and deferred tax and the effective tax rate, the inventory split,
  property at cost and accumulated depreciation, the auditor's report (the firm, the opinion's
  heading and sentences, the internal-control opinion, the going-concern paragraph, the first
  critical audit matter), contingent liabilities and related-party transactions. They are the
  only lines not read off a statement's face, and none of them feeds a statement's arithmetic.
* THE LINES ARE THE ROWS 10-K FILERS PRINT MOST. Which rows those are is not guessed: the face
  rows of the XBRL financial data filed with 10-Ks, ranked by how often a filer prints each one,
  put Total assets, Total liabilities and stockholders' equity, Retained earnings, Cash and cash
  equivalents, the three activity totals, Stock-based compensation, Repurchase of common stock and
  Payments for property, plant and equipment near the top. A row printed by few filers is left to
  its section's leftover line rather than given one of its own.
* A US BALANCE SHEET PRINTS NO "NON-CURRENT" HEADING. It prints "Current assets:", then "Total
  current assets", then the non-current rows with no banner over them, and the same for the
  liabilities. The reader carries the "Current assets" banner on past its total, so a non-current
  line here is also allowed under the current banner (its ``section_scope`` names the current
  section second, after its own): the first scope is the one it adds up in, the second only lets
  it bind. A current line is not given the reverse. A bare caption some filers print in BOTH
  places ("Operating lease liabilities", "Term debt") is an alias of neither line: under the
  carried banner both rows would bind the current line and be added. It is left to the request
  text, which closes a section at its own total. "Deferred income taxes", printed among the assets
  and again among the liabilities, is told apart by the banner: the asset line is not allowed
  under a liability banner, nor the liability line under an asset one.
* ONE LEFTOVER LINE PER ASSET, LIABILITY AND CASH-FLOW SECTION. The rows of those sections that
  no line claims are swept into the section's "Other ..." line, itemised, so every section still
  adds up to the total it prints. The statement of operations has none: its layout differs too
  much from one filer to the next ("Products" and "Services" under Net sales and again under Cost
  of sales), and a row no line claims is left for review rather than hidden in a bucket. Nor has
  stockholders' equity: every row a 10-K prints there has a line, and the sweep places a row the
  carried banner cannot place by the next subtotal printed below it — on a balance sheet that
  prints no "Total liabilities", the equity total. Measured on a retailer's layout, two long-term
  liability rows were swept into equity. With no equity bucket such a row is left for review, and
  the balance-sheet total that no longer adds up says so.
* TEMPORARY EQUITY HAS A SECTION OF ITS OWN. Redeemable noncontrolling interests and redeemable
  preferred stock are printed between the liabilities and the equity and are neither. In the
  equity section they would break its reconciliation; swept, they would land in whichever section
  comes next.
* EXPENSES ARE STORED AS PRINTED, positive, as a 10-K prints them. Subtotals whose meaning depends
  on the layout (total operating expenses, total costs and expenses, operating income, income
  before income taxes) are read as printed and not recomputed. Cash flows keep the sign the
  statement prints, and the net change in cash includes the effect of exchange rates, as a 10-K
  prints it.
* KEYS. The ``bs_*``/``is_*``/``cf_*`` section prefixes stay, and so do the keys application code
  names: ``bs_ca__total_assets``, ``bs_cl__total_equity_and_liabilities``,
  ``bs_equity__total_equity``, ``is_pl__sales_revenues``, ``is_pl__profit_for_the_year``.
"""
from __future__ import annotations

import copy
import json
import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import build_icon_pair as icon

SRC = BACKEND / "scripts" / "sample_data" / "usgaap"
OUT = BACKEND / "app" / "sample" / "templates"
TEMPLATE_KEY = "output_csv_usgaap_v1"
LINE_ITEMS_KEY = "output_csv_usgaap"

PL = ["is_pl"]
ATTRIBUTION = ["profit_attributable_to", "is_pl"]
TOP = ["bs_top_level"]
NCA = ["bs_nca", "bs_ca"]
NCL = ["bs_ncl", "bs_cl"]

# (id, label, statement, section_scope, sign convention, analyst bucket)
SECTIONS = [
    ("bs_ca", "Current assets", "balance_sheet", ["bs_ca"], "positive_expected", "current_assets"),
    ("bs_nca", "Non-current assets", "balance_sheet", ["bs_nca"], "positive_expected",
     "non_current_assets"),
    ("bs_cl", "Current liabilities", "balance_sheet", ["bs_cl"], "positive_expected",
     "current_liabilities"),
    ("bs_ncl", "Non-current liabilities", "balance_sheet", ["bs_ncl"], "positive_expected",
     "non_current_liabilities"),
    ("bs_mezz", "Temporary equity", "balance_sheet", ["bs_mezz"], "positive_expected", "others"),
    ("bs_equity", "Stockholders' equity", "balance_sheet", ["bs_equity"], "either", "equity"),
    ("is_pl", "Statement of operations", "profit_and_loss", PL, "either", "others"),
    ("cf_oper_indirect", "Operating activities", "cash_flow", ["cf_oper_indirect"], "either",
     "cash_flow_operating"),
    ("cf_investing", "Investing activities", "cash_flow", ["cf_investing"], "either",
     "cash_flow_investing"),
    ("cf_financing", "Financing activities", "cash_flow", ["cf_financing"], "either",
     "cash_flow_financing"),
]
NOTES_LABEL = "Additional Items"
STATEMENT_LABELS = {"balance_sheet": "Consolidated Balance Sheets",
                    "profit_and_loss": "Consolidated Statements of Operations",
                    "cash_flow": "Consolidated Statements of Cash Flows"}

# Captions a leftover line never takes: a total printed in its section.
_TOTALS = [r"^\W*(?:sub[\s-]*)?total\b", r"^\W*net\s+cash\b"]


def L(key, label, aliases=(), definition="", *, exclude=(), regex=(), sign="positive_expected",
      scope=None, roll=None, role=None, bucket=None):
    """One line of the spec. ``roll`` is ``(op, [children])`` for a calculated line."""
    return {"key": key, "label": label, "aliases": list(aliases), "definition": definition,
            "exclude": list(exclude), "regex": list(regex), "sign": sign, "scope": scope,
            "roll": roll, "role": role, "bucket": bucket}


def R(key, label, definition, exclude=(), aliases=()):
    """A section's leftover line: filled by the sweep, never by a caption match."""
    d = L(key, label, aliases, definition, exclude=list(_TOTALS) + list(exclude))
    d["residual"] = True
    return d


# Rule-tier patterns run on the matcher's normalised text (`mapping.normalize_label`: lower case,
# punctuation gone, "&" a space): "Property, plant and equipment, net" is "property plant and
# equipment net", "Net cash provided by (used in) operating activities" is "net cash provided by used
# in operating activities". `E` is a leading enumerator, `LESS` a "Less:" / "Add:".
E = rf"^{icon._ENUM}"
LESS = r"(?:less\s+|add\s+|plus\s+)?"
# "Commitments and contingencies" prints no figure, and row reconstruction glues it onto the row
# below it: "Commitments and contingencies Redeemable noncontrolling interest".
_BS_HEADS = r"commitments\s+and\s+contingencies(?:\s+(?:see\s+)?notes?\s+\d{1,2})?"
EB = rf"^{icon._ENUM}(?:(?:{_BS_HEADS})\s+{icon._ENUM})?"
# A cash flow statement's headings, which row reconstruction can glue onto the first row printed
# under them: "Operating activities: Net income", "Changes in operating assets and liabilities:
# Accounts receivable", and the one-word "Operations" / "Investing" / "Financing" some filers print.
_CF_HEADS = (r"(?:cash\s+flows?\s+(?:from|provided\s+by|used\s+in)\s+)?(?:operating|investing|financing)"
             r"\s+activities"
             r"|operations|investing|financing"
             r"|adjustments?\s+to\s+reconcile\s+(?:\w+\s+){1,4}?to\s+(?:\w+\s+){0,6}?operating\s+activities"
             r"|changes\s+in\s+(?:certain\s+|operating\s+)?assets\s+and\s+liabilities(?:\s+net\s+of\s+(?:\w+\s+){0,6}?"
             r"(?:acquisitions?|acquired|divestitures?))?"
             r"|(?:net\s+)?changes?\s+in\s+working\s+capital")
PCF = rf"^{icon._ENUM}(?:(?:{_CF_HEADS})\s+{icon._ENUM})?{LESS}"
_CHANGE = r"(?:(?:increase|decrease)\s+(?:(?:increase|decrease)\s+)?in\s+)?"
_PPE = r"(?:property|plant|fixed\s+assets|capital\s+assets)"
_SECURITIES = (r"(?:short\s+term\s+|long\s+term\s+|other\s+|non\s*marketable\s+)?(?:marketable\s+securities"
               r"|investments|securities|available\s+for\s+sale\s+(?:debt\s+)?securities|debt\s+securities"
               r"|equity\s+securities)")
_DEBT = r"(?:long\s+term\s+|term\s+|senior\s+)?(?:debt|borrowings?|notes|loans|bonds)"
_NOT_PER_SHARE = [r"per\s+(?:common\s+)?share", r"\bbasic\b", r"\bdiluted\b"]

LINES = {
    # ── BALANCE SHEET ──────────────────────────────────────────────────────────────────────────
    "bs_ca": [
        L("bs_ca__cash_and_cash_equivalents", "Cash and cash equivalents",
          ["Cash and cash equivalents", "Cash and equivalents", "Cash"],
          "Cash on hand and in banks, and short-term, highly liquid investments with original "
          "maturities of three months or less, as the balance sheet prints them. Restricted cash "
          "printed as its own row is the next line.",
          exclude=[r"restricted"],
          regex=[E + r"cash\s+(?:and\s+)?(?:cash\s+)?equivalents(?:\s+at\s+carrying\s+value)?$"]),
        L("bs_ca__restricted_cash", "Restricted cash",
          ["Restricted cash", "Restricted cash and cash equivalents"],
          "Cash whose use is restricted by contract or regulation, printed as its own current row.",
          exclude=[r"non\s*current|long\s+term"],
          regex=[E + r"restricted\s+cash\b.*$"]),
        L("bs_ca__short_term_investments", "Short-term investments",
          ["Short-term investments", "Marketable securities", "Short-term marketable securities",
           "Current marketable securities"],
          "Marketable securities and other investments held as current assets: debt and equity "
          "securities the filer expects to realise within a year.",
          exclude=[r"long\s+term|non\s*current"],
          regex=[E + r"(?:short\s+term|current)\s+(?:marketable\s+securities|investments)"
                 r"(?:\s+at\s+fair\s+value)?$",
                 E + r"marketable\s+securities(?:\s+current)?$"]),
        L("bs_ca__accounts_receivable", "Accounts receivable, net",
          ["Accounts receivable, net", "Accounts receivable", "Receivables, net", "Receivables",
           "Trade receivables, net", "Trade accounts receivable, net", "Accounts and notes receivable, net"],
          "Amounts customers owe for goods and services delivered, net of the allowance for credit "
          "losses, as the balance sheet prints them. Vendor, finance and other non-trade "
          "receivables are not this line.",
          exclude=[r"non\s*trade", r"\bvendor", r"financ", r"related\s+part", r"long\s+term",
                   r"non\s*current"],
          regex=[E + r"(?:trade\s+)?(?:accounts\s+)?(?:and\s+notes\s+)?receivables?(?:\s+trade)?"
                 r"(?:\s+net)?(?:\s+(?:less|net\s+of)\b.*)?$"]),
        L("bs_ca__inventories", "Inventories",
          ["Inventories", "Inventory", "Inventories, net", "Merchandise inventories"],
          "Raw materials, work in process and finished goods or merchandise held for sale, as one "
          "printed row.",
          regex=[E + r"(?:merchandise\s+)?inventor(?:y|ies)(?:\s+net)?$"]),
        L("bs_ca__assets_held_for_sale", "Assets held for sale",
          ["Assets held for sale", "Current assets held for sale",
           "Current assets of discontinued operations", "Assets of discontinued operations"],
          "Assets of a disposal group classified as held for sale or of discontinued operations, "
          "printed as a current row.",
          regex=[E + r"(?:current\s+)?assets\s+(?:held\s+for\s+sale|of\s+discontinued\s+operations)\b.*$"]),
        R("bs_ca__other_current_assets", "Prepaid expenses and other current assets",
          "Every current-asset row the face prints that no line above claims, itemised: prepaid "
          "expenses, vendor non-trade receivables, derivative assets, income taxes receivable.",
          aliases=["Other current assets", "Prepaid expenses and other current assets",
                   "Prepaid expenses and other", "Prepaid expenses"]),
        L("bs_ca__total_current_assets", "Total current assets", ["Total current assets"],
          "Total current assets as printed, and the sum of the current-asset lines.",
          role="subtotal",
          roll=("sum", ["bs_ca__cash_and_cash_equivalents", "bs_ca__restricted_cash",
                        "bs_ca__short_term_investments", "bs_ca__accounts_receivable",
                        "bs_ca__inventories", "bs_ca__assets_held_for_sale",
                        "bs_ca__other_current_assets"])),
    ],
    "bs_nca": [
        L("bs_nca__long_term_investments", "Long-term investments",
          ["Long-term investments", "Long-term marketable securities", "Marketable securities",
           "Non-current marketable securities", "Investments", "Equity investments",
           "Equity and other investments", "Other investments"],
          "Marketable securities and other investments held beyond a year: debt securities, equity "
          "securities and non-marketable investments. Investments accounted for by the equity "
          "method are the next line.",
          scope=NCA,
          exclude=[r"equity\s+method", r"affiliat", r"unconsolidated", r"subsidiar", r"short\s+term"],
          regex=[E + r"(?:long\s+term\s+|non\s*current\s+)?(?:marketable\s+securities|investments)"
                 r"(?:\s+non\s*current)?$"]),
        L("bs_nca__equity_method_investments", "Equity method investments",
          ["Equity method investments", "Investments in and advances to affiliates",
           "Investments in unconsolidated affiliates", "Investments in affiliated companies",
           "Investments in equity affiliates", "Investments in associates"],
          "Investments in affiliates and joint ventures the filer accounts for by the equity method "
          "(ASC 323), as one printed row.",
          scope=NCA,
          regex=[E + r"investments?\s+in\s+(?:and\s+advances\s+to\s+)?(?:unconsolidated\s+|equity\s+|"
                 r"nonconsolidated\s+)?(?:affiliates?|affiliated\s+companies|associates|joint\s+ventures)\b.*$",
                 E + r"equity\s+method\s+investments?\b.*$"]),
        L("bs_nca__property_plant_and_equipment", "Property, plant and equipment, net",
          ["Property, plant and equipment, net", "Property and equipment, net",
           "Property, plant and equipment", "Property and equipment", "Net property and equipment",
           "Property, equipment and software, net"],
          "Land, buildings, machinery and equipment net of accumulated depreciation, as the balance "
          "sheet prints them. Assets held under finance leases printed on their own row are left "
          "to the other non-current assets.",
          scope=NCA,
          exclude=[r"financ", r"right\s+of\s+use", r"investment\s+property"],
          regex=[E + r"(?:net\s+)?property(?:\s+plant)?(?:\s+and)?\s+equipment(?:\s+and\s+software)?"
                 r"(?:\s+net)?(?:\s+(?:of|less)\b.*)?$"]),
        L("bs_nca__operating_lease_right_of_use_assets", "Operating lease right-of-use assets",
          ["Operating lease right-of-use assets", "Operating lease assets", "Right-of-use assets",
           "Operating lease right-of-use assets, net", "Operating lease ROU assets"],
          "The right to use leased assets under operating leases (ASC 842), as one printed row.",
          scope=NCA, exclude=[r"financ"],
          regex=[E + r"(?:operating\s+lease\s+)?(?:right\s+of\s+use|rou)\s+assets?(?:\s+net)?$",
                 E + r"operating\s+lease\s+(?:right\s+of\s+use\s+|rou\s+)?assets?(?:\s+net)?$"]),
        L("bs_nca__goodwill", "Goodwill", ["Goodwill", "Goodwill, net"],
          "The excess of the price paid for acquired businesses over the fair value of their net "
          "identifiable assets, as the balance sheet prints it.",
          scope=NCA, regex=[E + r"goodwill(?:\s+net)?$"]),
        L("bs_nca__intangible_assets", "Intangible assets, net",
          ["Intangible assets, net", "Other intangible assets, net", "Acquired intangible assets, net",
           "Intangibles, net", "Intangible assets", "Identifiable intangible assets, net",
           "Purchased intangible assets, net", "Other intangible assets"],
          "Identifiable intangible assets other than goodwill (customer relationships, technology, "
          "trade names, licences), net of accumulated amortisation.",
          scope=NCA, exclude=[r"^\W*goodwill"],
          regex=[E + r"(?:other\s+|acquired\s+|identifiable\s+|purchased\s+|finite\s+lived\s+)?"
                 r"intangibles?(?:\s+assets?)?(?:\s+other\s+than\s+goodwill)?(?:\s+net)?\b.*$"]),
        L("bs_nca__deferred_tax_assets", "Deferred income tax assets",
          ["Deferred income taxes", "Deferred tax assets", "Deferred income tax assets",
           "Deferred taxes", "Deferred income taxes, net", "Deferred tax assets, net"],
          "Deferred tax assets net of any valuation allowance, printed among the assets.",
          scope=NCA, exclude=[r"liabilit"],
          regex=[E + r"deferred\s+(?:income\s+)?tax(?:es)?(?:\s+assets?)?(?:\s+net)?$"]),
        R("bs_nca__other_non_current_assets", "Other non-current assets",
          "Every non-current-asset row the face prints that no line above claims, itemised: other "
          "assets, finance lease assets, long-term receivables, capitalised software, restricted "
          "cash held beyond a year.",
          aliases=["Other assets", "Other non-current assets", "Other long-term assets",
                   "Other assets, net", "Other noncurrent assets"]),
        L("bs_nca__total_non_current_assets", "Total non-current assets",
          ["Total non-current assets", "Total noncurrent assets", "Total long-term assets"],
          "The sum of the non-current-asset lines. Most 10-K balance sheets print no such total; "
          "where one is printed it is read and compared.",
          role="subtotal",
          roll=("sum", ["bs_nca__long_term_investments", "bs_nca__equity_method_investments",
                        "bs_nca__property_plant_and_equipment",
                        "bs_nca__operating_lease_right_of_use_assets", "bs_nca__goodwill",
                        "bs_nca__intangible_assets", "bs_nca__deferred_tax_assets",
                        "bs_nca__other_non_current_assets"])),
        L("bs_ca__total_assets", "Total assets", ["Total assets"],
          "Total assets as printed, and the sum of total current and total non-current assets.",
          scope=TOP, role="total", regex=[E + r"total\s+assets$"],
          roll=("sum", ["bs_ca__total_current_assets", "bs_nca__total_non_current_assets"])),
    ],
    "bs_cl": [
        L("bs_cl__short_term_debt", "Short-term debt and current portion of long-term debt",
          ["Short-term debt", "Short-term borrowings", "Commercial paper",
           "Current portion of long-term debt", "Current maturities of long-term debt",
           "Long-term debt due within one year", "Notes payable", "Current portion of debt",
           "Debt due within one year", "Current debt", "Loans payable", "Term debt, current"],
          "Borrowings due within a year: commercial paper, short-term borrowings and notes payable, "
          "and the current portion of long-term debt. Where the face prints them on separate rows "
          "the rows are added, and so is the current part of finance lease obligations, which a "
          "credit analyst counts as debt. Operating lease liabilities are not this line. A bare "
          "'Term debt' printed in both the current and the non-current liabilities is left to the "
          "request text, which closes the current section at its total.",
          exclude=[r"operating\s+lease", r"long\s+term\s+debt\s+net\s+of", r"less\s+current"],
          regex=[E + r"short\s+term\s+(?:debt|borrowings?|notes|loans?)\b.*$",
                 E + r"current\s+(?:portion|maturities)\s+of\s+(?:long\s+term\s+)?(?:debt|borrowings?|"
                 r"notes|term\s+debt)\b.*$",
                 E + r"(?:long\s+term\s+)?debt\s+due\s+within\s+one\s+year\b.*$",
                 E + r"commercial\s+paper\b.*$",
                 E + r"(?:notes|loans)\s+payable(?:\s+to\s+banks)?(?:\s+current)?$",
                 E + r"(?:current\s+(?:portion\s+of\s+)?|short\s+term\s+)finance\s+lease\s+(?:liabilities|"
                 r"obligations?)$",
                 E + r"finance\s+lease\s+(?:liabilities|obligations?)\s+(?:current|due\s+within\s+one\s+year)$"]),
        L("bs_cl__accounts_payable", "Accounts payable",
          ["Accounts payable", "Trade accounts payable", "Accounts payable and accrued liabilities",
           "Trade payables", "Accounts payable, trade", "Accounts payable and accrued expenses"],
          "Amounts owed to suppliers for goods and services received, as the balance sheet prints "
          "them; where the face prints accounts payable together with accrued liabilities as one "
          "row, that row.",
          exclude=[r"\bother\b", r"related\s+part", r"non\s*current|long\s+term"],
          regex=[E + r"(?:trade\s+)?accounts\s+payable\b.*$", E + r"trade\s+payables?\b.*$"]),
        L("bs_cl__accrued_liabilities", "Accrued liabilities",
          ["Accrued liabilities", "Accrued expenses", "Accrued compensation and benefits",
           "Accrued compensation", "Accrued payroll", "Accrued interest"],
          "Expenses incurred and not yet paid (compensation, interest, warranties, other accruals), "
          "printed as their own rows; where the face prints several accrual rows they are added. "
          "A combined 'accrued and other current liabilities' row is the leftover line's.",
          exclude=[r"income\s+tax", r"\bother\b", r"non\s*current|long\s+term"],
          regex=[E + r"accrued\s+(?:\w+\s+){0,3}?(?:liabilities|expenses|compensation|payroll|wages|"
                 r"benefits|interest)\b.*$"]),
        L("bs_cl__deferred_revenue", "Deferred revenue",
          ["Deferred revenue", "Unearned revenue", "Contract liabilities", "Deferred revenue, current",
           "Current deferred revenue", "Deferred revenues", "Deferred income"],
          "Consideration received for goods or services not yet delivered, due within a year "
          "(contract liabilities under ASC 606).",
          exclude=[r"non\s*current|long\s+term|net\s+of\s+current|less\s+current", r"tax"],
          regex=[E + r"(?:current\s+|short\s+term\s+)?(?:deferred|unearned)\s+(?:revenues?|income)(?:\s+current)?$",
                 E + r"(?:current\s+)?contract\s+liabilities(?:\s+current)?$"]),
        L("bs_cl__income_taxes_payable", "Income taxes payable",
          ["Income taxes payable", "Accrued income taxes", "Taxes payable",
           "Current income taxes payable", "Short-term income taxes"],
          "Income taxes owed to tax authorities and due within a year, as the balance sheet prints them. "
          "Long-term income taxes payable are the other non-current liabilities.",
          exclude=[r"non\s*current|long\s+term"],
          regex=[E + r"(?:accrued\s+|current\s+)?(?:income\s+)?taxes\s+payable(?:\s+current)?$",
                 E + r"accrued\s+income\s+taxes$"]),
        L("bs_cl__operating_lease_liabilities_current", "Operating lease liabilities, current",
          ["Operating lease liabilities, current", "Current operating lease liabilities",
           "Current portion of operating lease liabilities",
           "Operating lease obligations due within one year", "Short-term operating lease liabilities"],
          "The part of operating lease liabilities (ASC 842) due within a year. A bare 'Operating "
          "lease liabilities' printed in both the current and the non-current liabilities is left "
          "to the request text, which closes the current section at its total.",
          exclude=[r"non\s*current|long\s+term|less\s+current|net\s+of\s+current"],
          regex=[E + r"(?:current\s+(?:portion\s+of\s+)?|short\s+term\s+)operating\s+lease\s+"
                 r"(?:liabilities|liability|obligations?)$",
                 E + r"operating\s+lease\s+(?:liabilities|liability|obligations?)\s+(?:current|due\s+"
                 r"within\s+one\s+year)$"]),
        L("bs_cl__liabilities_held_for_sale", "Liabilities held for sale",
          ["Liabilities held for sale", "Current liabilities held for sale",
           "Current liabilities of discontinued operations", "Liabilities of discontinued operations"],
          "Liabilities of a disposal group classified as held for sale or of discontinued "
          "operations, printed as a current row.",
          regex=[E + r"(?:current\s+)?liabilities\s+(?:held\s+for\s+sale|of\s+discontinued\s+operations)"
                 r"\b.*$"]),
        R("bs_cl__other_current_liabilities", "Other current liabilities",
          "Every current-liability row the face prints that no line above claims, itemised: other "
          "current liabilities, accrued and other liabilities printed as one row, current finance "
          "lease liabilities, dividends payable.",
          aliases=["Other current liabilities", "Accrued expenses and other current liabilities",
                   "Accrued and other current liabilities", "Other accrued liabilities"]),
        L("bs_cl__total_current_liabilities", "Total current liabilities",
          ["Total current liabilities"],
          "Total current liabilities as printed, and the sum of the current-liability lines.",
          role="subtotal",
          roll=("sum", ["bs_cl__short_term_debt", "bs_cl__accounts_payable",
                        "bs_cl__accrued_liabilities", "bs_cl__deferred_revenue",
                        "bs_cl__income_taxes_payable", "bs_cl__operating_lease_liabilities_current",
                        "bs_cl__liabilities_held_for_sale", "bs_cl__other_current_liabilities"])),
    ],
    "bs_ncl": [
        L("bs_ncl__long_term_debt", "Long-term debt",
          ["Long-term debt", "Long-term debt, net of current portion", "Long-term debt, less current portion",
           "Long-term debt, net", "Long-term borrowings", "Term debt, non-current",
           "Long-term debt, excluding current maturities", "Long-term debt due after one year",
           "Non-current debt", "Long-term notes payable"],
          "Borrowings due after a year (notes, bonds, term loans), net of the current portion, as "
          "the balance sheet prints them, and the long-term part of finance lease obligations, "
          "which a credit analyst counts as debt; the rows are added. Operating lease liabilities "
          "are not this line.",
          scope=NCL,
          exclude=[r"operating\s+lease", r"^\W*current\s+(?:portion|maturities)", r"due\s+within\s+one\s+year"],
          regex=[E + r"long\s+term\s+(?:debt|borrowings?|notes(?:\s+payable)?|obligations)\b.*$",
                 E + r"(?:non\s*current|noncurrent)\s+(?:debt|borrowings)\b.*$",
                 E + r"term\s+debt\s+non\s*current$",
                 E + r"(?:long\s+term|non\s*current)\s+finance\s+lease\s+(?:liabilities|obligations?)$",
                 E + r"finance\s+lease\s+(?:liabilities|obligations?)\s+(?:non\s*current|long\s+term|"
                 r"(?:less|net\s+of)\s+current\s+portion)$"]),
        L("bs_ncl__operating_lease_liabilities", "Operating lease liabilities, non-current",
          ["Long-term operating lease liabilities", "Operating lease liabilities, non-current",
           "Operating lease liabilities, noncurrent", "Non-current operating lease liabilities",
           "Noncurrent operating lease liabilities", "Long-term operating lease obligations",
           "Operating lease liabilities, less current portion",
           "Operating lease liabilities, net of current portion"],
          "The part of operating lease liabilities (ASC 842) due after a year.",
          scope=NCL,
          exclude=[r"due\s+within\s+one\s+year", r"short\s+term",
                   r"^\W*current\s+(?:portion\s+of\s+)?operating"],
          regex=[E + r"(?:long\s+term|non\s*current)\s+operating\s+lease\s+(?:liabilities|liability|"
                 r"obligations?)$",
                 E + r"operating\s+lease\s+(?:liabilities|liability|obligations?)\s+(?:non\s*current|"
                 r"long\s+term|(?:less|net\s+of)\s+current\s+portion)$"]),
        L("bs_ncl__deferred_tax_liabilities", "Deferred income tax liabilities",
          ["Deferred income taxes", "Deferred tax liabilities", "Deferred income tax liabilities",
           "Deferred taxes", "Deferred income taxes, net", "Deferred tax liabilities, net"],
          "Deferred tax liabilities, printed among the liabilities; where the face prints them together "
          "with other long-term liabilities as one row, that row.",
          scope=NCL, exclude=[r"\bassets?\b"],
          regex=[E + r"deferred\s+(?:income\s+)?tax(?:es)?(?:\s+liabilit(?:y|ies))?(?:\s+net)?"
                 r"(?:\s+and\s+other(?:\s+(?:long\s+term\s+|non\s*current\s+)?liabilities)?)?$"]),
        L("bs_ncl__deferred_revenue", "Deferred revenue, non-current",
          ["Long-term deferred revenue", "Deferred revenue, non-current", "Deferred revenue, noncurrent",
           "Long-term unearned revenue", "Non-current deferred revenue",
           "Deferred revenue, net of current portion", "Long-term contract liabilities",
           "Contract liabilities, non-current"],
          "Consideration received for goods or services to be delivered after a year.",
          scope=NCL,
          regex=[E + r"(?:long\s+term|non\s*current)\s+(?:deferred|unearned)\s+revenues?\b.*$",
                 E + r"(?:deferred|unearned)\s+revenues?\s+(?:non\s*current|long\s+term|net\s+of\s+current"
                 r"\s+portion|less\s+current\s+portion)\b.*$",
                 E + r"(?:long\s+term\s+|non\s*current\s+)?contract\s+liabilities\s*(?:non\s*current|"
                 r"long\s+term)?$"],
          exclude=[r"^\W*current", r"^\W*(?:deferred|unearned)\s+revenues?$", r"^\W*contract\s+liabilities$"]),
        L("bs_ncl__pension_and_postretirement", "Pension and postretirement benefits",
          ["Pension and postretirement benefits", "Pension and other postretirement benefits",
           "Accrued pension liabilities", "Pension liabilities", "Employee benefit obligations",
           "Liability for postemployment benefits", "Accrued postretirement benefits",
           "Retirement benefits", "Postretirement benefits other than pensions"],
          "The unfunded obligation for defined-benefit pensions and other postretirement and "
          "postemployment benefits, as the balance sheet prints it.",
          scope=NCL,
          regex=[E + r"(?:accrued\s+|liabilit(?:y|ies)\s+for\s+)?(?:pension|post\s*retirement|"
                 r"post\s*employment|retirement)\b.*$",
                 E + r"employee\s+benefit\s+obligations?\b.*$"]),
        R("bs_ncl__other_non_current_liabilities", "Other non-current liabilities",
          "Every non-current-liability row the face prints that no line above claims, itemised: "
          "other long-term liabilities, long-term income taxes payable, finance lease liabilities, "
          "asset retirement obligations.",
          aliases=["Other long-term liabilities", "Other non-current liabilities", "Other liabilities",
                   "Other noncurrent liabilities", "Other liabilities and deferred credits",
                   "Other long-term obligations"],
          exclude=[r"redeemable", r"temporary\s+equity", r"commitments\s+and\s+contingencies"]),
        L("bs_ncl__total_non_current_liabilities", "Total non-current liabilities",
          ["Total non-current liabilities", "Total noncurrent liabilities", "Total long-term liabilities"],
          "The sum of the non-current-liability lines. Most 10-K balance sheets print no such "
          "total; where one is printed it is read and compared.",
          role="subtotal",
          roll=("sum", ["bs_ncl__long_term_debt", "bs_ncl__operating_lease_liabilities",
                        "bs_ncl__deferred_tax_liabilities", "bs_ncl__deferred_revenue",
                        "bs_ncl__pension_and_postretirement",
                        "bs_ncl__other_non_current_liabilities"])),
        L("bs_cl__total_liabilities", "Total liabilities", ["Total liabilities"],
          "Total liabilities as printed, and the sum of total current and total non-current "
          "liabilities.",
          scope=TOP, role="total", regex=[E + r"total\s+liabilities$"],
          roll=("sum", ["bs_cl__total_current_liabilities", "bs_ncl__total_non_current_liabilities"])),
    ],
    "bs_mezz": [
        L("bs_mezz__temporary_equity", "Temporary equity",
          ["Redeemable noncontrolling interests", "Redeemable noncontrolling interest",
           "Temporary equity", "Redeemable convertible preferred stock", "Redeemable preferred stock",
           "Mezzanine equity", "Redeemable non-controlling interests"],
          "Redeemable noncontrolling interests and redeemable preferred stock printed between the "
          "liabilities and the stockholders' equity (temporary or mezzanine equity, ASC 480-10-S99). "
          "It is neither a liability nor part of total equity.",
          scope=["bs_mezz", "bs_ncl", "bs_cl", "bs_equity"],
          regex=[EB + r"(?:redeemable\s+(?:non\s*controlling|convertible|preferred)|temporary\s+equity|"
                 r"mezzanine\s+equity)\b.*$"]),
    ],
    "bs_equity": [
        L("bs_equity__preferred_stock", "Preferred stock",
          ["Preferred stock", "Preferred shares", "Convertible preferred stock"],
          "Preferred stock classified in stockholders' equity, at its carrying amount. Redeemable "
          "preferred stock is temporary equity.",
          exclude=[r"redeemable"],
          regex=[EB + r"(?:convertible\s+|series\s+\w+\s+)?preferred\s+(?:stock|shares)\b.*$"]),
        L("bs_equity__common_stock", "Common stock",
          ["Common stock", "Common shares", "Common stock and additional paid-in capital", "Capital stock"],
          "Common stock at par or stated value, the caption usually carrying the par value and the "
          "shares authorised and issued. Where the face prints common stock and additional "
          "paid-in capital as one row, that row, and the next line is left empty.",
          exclude=[r"treasury", r"repurchas"],
          regex=[EB + r"(?:class\s+[a-c]\s+)?common\s+(?:stock|shares)\b.*$", EB + r"capital\s+stock\b.*$"]),
        L("bs_equity__additional_paid_in_capital", "Additional paid-in capital",
          ["Additional paid-in capital", "Capital in excess of par value", "Paid-in capital",
           "Capital surplus"],
          "Amounts paid for stock above its par value, and the credit for stock-based compensation.",
          regex=[E + r"(?:additional\s+)?paid\s+in\s+capital\b.*$",
                 E + r"capital\s+in\s+excess\s+of\s+(?:par|stated)\s+value\b.*$"]),
        L("bs_equity__retained_earnings", "Retained earnings (accumulated deficit)",
          ["Retained earnings", "Retained earnings (accumulated deficit)", "Accumulated deficit",
           "Reinvested earnings", "Earnings reinvested in the business", "Retained deficit",
           "Accumulated earnings", "Profit employed in the business"],
          "Cumulative earnings not distributed as dividends, or the accumulated deficit.",
          sign="either",
          regex=[E + r"(?:retained|reinvested|accumulated)\s+(?:earnings|deficit)\b.*$",
                 E + r"earnings\s+(?:retained|reinvested)\b.*$"]),
        L("bs_equity__accumulated_other_comprehensive_income",
          "Accumulated other comprehensive income (loss)",
          ["Accumulated other comprehensive income (loss)", "Accumulated other comprehensive loss",
           "Accumulated other comprehensive income"],
          "The accumulated balance of other comprehensive income: currency translation, unrealised "
          "gains and losses on securities and hedges, and pension adjustments.",
          sign="either", regex=[E + r"accumulated\s+other\s+comprehensive\b.*$"]),
        L("bs_equity__treasury_stock", "Treasury stock",
          ["Treasury stock, at cost", "Treasury stock", "Common stock held in treasury, at cost",
           "Treasury shares, at cost"],
          "The cost of the filer's own shares repurchased and held in treasury, printed as a "
          "deduction.",
          sign="either",
          regex=[E + LESS + r"(?:(?:common\s+)?(?:stock|shares)\s+held\s+in\s+treasury|treasury\s+"
                 r"(?:stock|shares))\b.*$"]),
        L("bs_equity__equity_attributable_to_owners", "Total stockholders' equity",
          ["Total stockholders' equity", "Total shareholders' equity",
           "Total stockholders' equity (deficit)", "Total shareholders' equity (deficit)",
           "Total stockholders' deficit"],
          "The equity of the filer's own stockholders, as printed: the sum of the equity lines "
          "above, before noncontrolling interests.",
          sign="either", role="subtotal",
          exclude=[r"liabilit", r"non\s*controlling", r"minority", r"redeemable"],
          regex=[E + r"total\s+(?:\w+\s+){0,4}?(?:stock|share)holders\s+(?:equity|deficit|investment)"
                 r"(?:\s+deficit)?(?:\s+attributable\s+to\s+.*)?$",
                 E + r"total\s+equity\s+attributable\s+to\s+.*$"],
          roll=("sum", ["bs_equity__preferred_stock", "bs_equity__common_stock",
                        "bs_equity__additional_paid_in_capital", "bs_equity__retained_earnings",
                        "bs_equity__accumulated_other_comprehensive_income",
                        "bs_equity__treasury_stock"])),
        L("bs_equity__non_controlling_interests", "Noncontrolling interests",
          ["Noncontrolling interests", "Noncontrolling interest", "Non-controlling interests",
           "Non-controlling interest", "Minority interest", "Minority interests"],
          "The equity in consolidated subsidiaries not owned by the filer, classified in equity. "
          "Redeemable noncontrolling interests are temporary equity; a 'nonredeemable' one is "
          "this line.",
          sign="either", exclude=[r"(?<!non)(?<!non )redeemable", r"^\W*total"],
          regex=[E + r"(?:equity\s+attributable\s+to\s+)?(?:non\s*redeemable\s+)?(?:non\s*controlling|minority)"
                 r"\s+interests?\b.*$"]),
        L("bs_equity__total_equity", "Total equity",
          ["Total equity", "Total equity (deficit)",
           "Total stockholders' equity and noncontrolling interests"],
          "Total equity as printed: the stockholders' equity and the noncontrolling interests.",
          sign="either", role="subtotal",
          regex=[E + r"total\s+equity(?:\s+deficit)?$",
                 E + r"total\s+(?:stock|share)holders\s+equity\s+(?:and|including)\s+non\s*controlling\b.*$"],
          roll=("sum", ["bs_equity__equity_attributable_to_owners",
                        "bs_equity__non_controlling_interests"])),
        L("bs_cl__total_equity_and_liabilities", "Total liabilities and stockholders' equity",
          ["Total liabilities and stockholders' equity", "Total liabilities and shareholders' equity",
           "Total liabilities and equity", "Total liabilities, redeemable noncontrolling interest, and equity",
           "Total liabilities and stockholders' equity (deficit)"],
          "The balance-sheet total on the right-hand side: total liabilities, temporary equity and "
          "total equity. It equals total assets.",
          scope=TOP, role="total",
          regex=[E + r"total\s+liabilities\b.*\b(?:equity|deficit)\b.*$"],
          roll=("sum", ["bs_cl__total_liabilities", "bs_mezz__temporary_equity",
                        "bs_equity__total_equity"])),
    ],
    # ── STATEMENT OF OPERATIONS ───────────────────────────────────────────────────────────────
    "is_pl": [
        L("is_pl__net_sales", "Net sales", ["Net sales", "Sales", "Net product sales", "Product sales"],
          "Revenue from the sale of goods and services, where the face prints it as a row of its "
          "own above other revenue. A face printing one revenue figure prints it as total revenues.",
          bucket="income",
          regex=[E + r"(?:net\s+)?(?:product\s+)?sales(?:\s+to\s+customers)?$"]),
        L("is_pl__other_revenues", "Other revenues",
          ["Other revenues", "Other revenue", "Other operating revenues", "Membership and other income"],
          "Revenue the face prints beside net sales: membership fees, franchise and licence "
          "income, other operating revenue.",
          bucket="income",
          regex=[E + r"other\s+(?:operating\s+)?revenues?$"]),
        L("is_pl__sales_revenues", "Total revenues",
          ["Total revenues", "Total revenue", "Total net sales", "Total net revenue", "Total net revenues",
           "Revenues", "Revenue", "Net revenues", "Net revenue", "Total sales and revenues",
           "Sales and revenues", "Net sales and revenues", "Total sales", "Operating revenues",
           "Total operating revenues", "Revenues, net", "Net sales and operating revenues"],
          "Total revenues as printed: the top line of the statement of operations. Where the face "
          "prints net sales and other revenues it is their sum.",
          role="subtotal", bucket="income", exclude=[r"\bcost", r"\bother\b"],
          regex=[E + r"(?:total\s+)?(?:net\s+)?(?:operating\s+)?revenues?(?:\s+net)?$",
                 E + r"(?:total\s+)?(?:net\s+)?sales\s+and\s+(?:operating\s+)?revenues?$",
                 E + r"total\s+(?:net\s+)?(?:product\s+)?sales$"],
          roll=("sum", ["is_pl__net_sales", "is_pl__other_revenues"])),
        L("is_pl__cost_of_revenue", "Cost of revenue",
          ["Cost of sales", "Cost of revenue", "Cost of revenues", "Total cost of sales",
           "Total cost of revenue", "Total cost of revenues", "Cost of goods sold", "Cost of products sold",
           "Costs of revenue", "Cost of goods and services sold"],
          "The cost of the goods and services sold, as printed, positive; where the face prints a "
          "product and a service cost and their total, the total.",
          bucket="expenses", exclude=[r"^\W*gross"],
          regex=[E + r"(?:total\s+)?costs?\s+of\s+(?:net\s+)?(?:sales|revenues?|goods\s+(?:and\s+services\s+)?"
                 r"sold|products\s+sold|merchandise\s+sold|products\s+and\s+services(?:\s+sold)?|sales\s+and"
                 r"\s+services)\b.*$"]),
        L("is_pl__gross_profit", "Gross profit",
          ["Gross margin", "Gross profit", "Total gross margin", "Gross profit (loss)"],
          "Total revenues less cost of revenue. Where the face prints it, that figure is read and "
          "compared.",
          sign="either", role="subtotal", bucket="income",
          regex=[E + r"(?:total\s+)?gross\s+(?:profit|margin)(?:\s+loss)?$"],
          roll=("diff", ["is_pl__sales_revenues", "is_pl__cost_of_revenue"])),
        L("is_pl__research_and_development", "Research and development",
          ["Research and development", "Research and development expenses", "Research and development expense",
           "Research, development and engineering", "Technology and development"],
          "Research and development expense printed as an operating expense row of its own, as "
          "printed, positive.",
          bucket="expenses", regex=[E + r"research\s+(?:and\s+)?development\b.*$"]),
        L("is_pl__selling_general_and_administrative", "Selling, general and administrative",
          ["Selling, general and administrative", "Selling, general and administrative expenses",
           "Sales and marketing", "General and administrative", "Selling and marketing",
           "Marketing, general and administrative", "Operating, selling, general and administrative expenses",
           "Selling and administrative expenses", "General and administrative expenses"],
          "Selling, marketing, general and administrative expense, as printed, positive. Where the "
          "face prints sales and marketing and general and administrative on separate rows, the "
          "rows are added.",
          bucket="expenses",
          regex=[E + r"(?:operating\s+)?selling\s+(?:general\s+)?(?:and\s+)?(?:administrative|marketing)"
                 r"(?:\s+and\s+(?:administrative|general))*(?:\s+expenses?)?$",
                 E + r"(?:sales|selling)\s+and\s+marketing(?:\s+expenses?)?$",
                 E + r"(?:marketing\s+)?general\s+(?:and\s+)?administrative(?:\s+expenses?)?$"]),
        L("is_pl__depreciation_and_amortization", "Depreciation and amortization",
          ["Depreciation and amortization", "Depreciation, depletion and amortization", "Depreciation",
           "Amortization of intangible assets", "Amortization of acquired intangible assets"],
          "Depreciation and amortisation printed as its own operating expense row; where the face "
          "prints amortisation of intangibles separately, the rows are added. Empty where the face "
          "includes it in cost of revenue and the other expense lines.",
          bucket="expenses",
          regex=[E + r"depreciation(?:\s+depletion)?(?:\s+and)?(?:\s+amorti[sz]ation)?(?:\s+expense)?$",
                 E + r"amorti[sz]ation\s+of\s+(?:acquired\s+|purchased\s+)?(?:intangible\s+assets|"
                 r"intangibles)$"]),
        L("is_pl__restructuring_and_impairment", "Restructuring and impairment charges",
          ["Restructuring charges", "Restructuring and other charges", "Impairment charges",
           "Goodwill impairment", "Impairment of goodwill", "Asset impairments",
           "Restructuring and impairment charges"],
          "Restructuring, exit and impairment charges printed as operating expense rows of their "
          "own; the rows are added.",
          bucket="expenses",
          regex=[E + r"(?:restructuring|impairment|goodwill\s+(?:and\s+\w+\s+)?impairment|asset\s+"
                 r"impairments?)\b.*$"]),
        L("is_pl__other_operating_expense_income", "Other operating expense (income), net",
          ["Other operating expense (income), net", "Other operating expenses",
           "Other operating (income) expense, net", "Other operating income", "Other operating expense, net"],
          "Other operating expense or income printed as a row of its own within operating "
          "expenses, with the sign the face gives it.",
          sign="either", bucket="expenses",
          regex=[E + r"other\s+operating\s+(?:income\s+)?(?:expenses?|costs?|income|charges)(?:\s+income)?"
                 r"(?:\s+expenses?)?(?:\s+net)?$"]),
        L("is_pl__total_operating_expenses", "Total operating expenses",
          ["Total operating expenses", "Operating expenses"],
          "Total operating expenses as printed. A multi-step statement prints it below gross "
          "profit, excluding cost of revenue; it is read as printed and not recomputed.",
          role="subtotal", bucket="expenses",
          regex=[E + r"(?:total\s+)?operating\s+expenses$"]),
        L("is_pl__total_costs_and_expenses", "Total costs and expenses",
          ["Total costs and expenses", "Total costs and operating expenses", "Total operating costs",
           "Total expenses", "Costs and expenses", "Total operating costs and expenses"],
          "Total costs and expenses as printed. A single-step statement prints it below total "
          "revenues, including cost of revenue; it is read as printed and not recomputed.",
          role="subtotal", bucket="expenses",
          regex=[E + r"(?:total\s+)?(?:operating\s+)?costs?\s+and\s+(?:operating\s+)?expenses$",
                 E + r"total\s+(?:operating\s+)?(?:costs|expenses)$"]),
        L("is_pl__operating_income", "Operating income (loss)",
          ["Operating income", "Operating income (loss)", "Operating loss", "Income from operations",
           "Loss from operations", "Income (loss) from operations", "Operating profit", "Operating earnings",
           "Earnings from operations"],
          "Operating income or loss as printed; read as printed and not recomputed.",
          sign="either", role="subtotal", bucket="income",
          regex=[E + r"operating\s+(?:income|profit|loss|earnings)(?:\s+(?:loss|profit|income))?$",
                 E + r"(?:income|loss|earnings|profit)(?:\s+(?:loss|income))?\s+from\s+operations$"]),
        L("is_pl__interest_expense", "Interest expense",
          ["Interest expense", "Interest expense, net", "Interest, net", "Net interest expense",
           "Interest and debt expense"],
          "Interest expense on debt, as printed; where the face prints interest only net of "
          "interest income, that net figure.",
          sign="either", bucket="interest",
          regex=[E + LESS + r"(?:net\s+)?interest\s+(?:and\s+(?:debt|other)\s+)?(?:expense|charges?|costs?)\b.*$",
                 E + r"interest\s+net$"]),
        L("is_pl__interest_income", "Interest income",
          ["Interest income", "Interest and dividend income", "Interest income and other",
           "Interest and other income, net", "Investment income", "Interest and investment income"],
          "Interest and investment income on cash and securities, printed as a non-operating row of "
          "its own, with the sign the face gives it.",
          sign="either", bucket="interest",
          regex=[E + r"interest\s+(?:and\s+(?:dividend|investment|other)\s+)?income\b.*$",
                 E + r"investment\s+income(?:\s+net)?$"]),
        L("is_pl__other_income_expense", "Other income (expense), net",
          ["Other income (expense), net", "Other income (expense)", "Other income, net", "Other expense, net",
           "Other (income) expense, net", "Other income", "Other expense", "Other (gains) and losses",
           "Other non-operating income (expense), net", "Nonoperating income (expense)", "Other, net"],
          "Other non-operating income and expense printed as a row of its own, with the sign the "
          "face gives it.",
          sign="either", bucket="non_operating",
          exclude=[r"^\W*total", r"comprehensive", r"^\W*other\s+operating\b"],
          regex=[E + r"other\s+(?:non\s*operating\s+)?(?:income|expenses?|gains?|loss(?:es)?)(?:\s+(?:and\s+)?"
                 r"(?:income|expenses?|gains?|loss(?:es)?))*(?:\s+net)?$",
                 E + r"non\s*operating\s+(?:income|expenses?)(?:\s+(?:income|expenses?))*(?:\s+net)?$",
                 E + r"other\s+net$"]),
        L("is_pl__total_other_income_expense", "Total other income (expense), net",
          ["Total other income (expense), net", "Total other income (expense)",
           "Total other (income) expense, net", "Total non-operating income (expense)",
           "Total other expense, net", "Total other income, net"],
          "Total non-operating income and expense as printed; read as printed and not recomputed.",
          sign="either", role="subtotal", bucket="non_operating",
          regex=[E + r"total\s+(?:other|non\s*operating|interest\s+and\s+other)\s+(?:income|expenses?|gains?|"
                 r"loss(?:es)?)(?:\s+(?:and\s+)?(?:income|expenses?|gains?|loss(?:es)?))*(?:\s+net)?$"]),
        L("is_pl__income_before_taxes", "Income before income taxes",
          ["Income before income taxes", "Income before provision for income taxes",
           "Income (loss) before income taxes", "Earnings before income taxes", "Loss before income taxes",
           "Income before taxes", "Consolidated profit before taxes"],
          "Income or loss before income taxes as printed; read as printed and not recomputed.",
          sign="either", role="subtotal", bucket="income",
          regex=[E + r"(?:consolidated\s+)?(?:income|earnings|profit|loss)(?:\s+(?:loss|income|earnings|profit))?"
                 r"(?:\s+from\s+continuing\s+operations)?\s+before\s+(?:provision\s+for\s+|benefit\s+from\s+)?"
                 r"(?:income\s+)?tax(?:es|ation)?\b.*$"]),
        L("is_pl__income_tax_expense", "Provision for income taxes",
          ["Provision for income taxes", "Income tax expense", "Provision for (benefit from) income taxes",
           "Income tax provision", "Income taxes", "Benefit from income taxes",
           "Provision (benefit) for income taxes", "Income tax expense (benefit)",
           "Total provision for income taxes"],
          "The income tax expense or benefit for the year, current and deferred together, as "
          "printed.",
          sign="either", bucket="expenses",
          exclude=[r"\bbefore\b", r"^\W*(?:current|deferred)\b", r"payable"],
          regex=[E + r"(?:total\s+)?(?:provision|benefit)(?:\s+(?:for|from))?(?:\s+(?:benefit|provision))?"
                 r"(?:\s+(?:for|from))?\s+(?:income\s+)?taxes\b.*$",
                 E + r"(?:total\s+)?income\s+tax(?:es)?(?:\s+(?:expense|provision|benefit))*$"]),
        L("is_pl__equity_in_earnings_of_affiliates", "Equity in earnings of affiliates",
          ["Equity in earnings of affiliates", "Equity in net income of affiliates",
           "Equity in earnings (losses) of unconsolidated affiliates", "Income from equity method investments",
           "Equity income"],
          "The filer's share of the earnings of equity-method investees, as printed.",
          sign="either", bucket="non_operating",
          regex=[E + r"equity\s+(?:in\s+)?(?:net\s+)?(?:earnings|income|profit|loss(?:es)?)\b.*$",
                 E + r"(?:income|earnings|loss)(?:\s+loss)?\s+from\s+equity\s+method\s+investments?\b.*$"]),
        L("is_pl__income_from_continuing_operations", "Income from continuing operations",
          ["Income from continuing operations", "Net income from continuing operations",
           "Income (loss) from continuing operations", "Earnings from continuing operations"],
          "Income or loss from continuing operations after income taxes, where the face prints it.",
          sign="either", role="subtotal", bucket="income",
          exclude=[r"\bbefore\b", r"attributable"] + _NOT_PER_SHARE,
          regex=[E + r"(?:net\s+)?(?:income|earnings|loss|profit)(?:\s+(?:loss|income|earnings))?\s+from\s+"
                 r"continuing\s+operations(?:\s+net\s+of\s+(?:income\s+)?tax(?:es)?)?$"]),
        L("is_pl__discontinued_operations", "Income (loss) from discontinued operations",
          ["Income (loss) from discontinued operations, net of tax", "Discontinued operations, net of tax",
           "Loss from discontinued operations", "Income from discontinued operations"],
          "The result of operations the filer has disposed of or classified as held for sale, net of "
          "tax, as printed below income from continuing operations.",
          sign="either", bucket="non_operating", exclude=list(_NOT_PER_SHARE) + [r"attributable"],
          regex=[E + r"(?:net\s+)?(?:(?:income|earnings|loss|profit)(?:\s+(?:loss|income|earnings))?\s+"
                 r"(?:from|on)\s+)?discontinued\s+operations\b.*$"]),
        L("is_pl__profit_for_the_year", "Net income (loss)",
          ["Net income", "Net income (loss)", "Net loss", "Net earnings", "Consolidated net income",
           "Net earnings (loss)", "Net (loss) income", "Profit of consolidated and affiliated companies",
           "Net income including noncontrolling interests"],
          "Net income or loss for the year as printed, including any part attributable to "
          "noncontrolling interests.",
          sign="either", role="total", bucket="income",
          exclude=[r"attributable", r"available\s+to", r"\bfrom\b"] + _NOT_PER_SHARE,
          regex=[E + r"(?:consolidated\s+)?net\s+(?:income|earnings|loss|profit)(?:\s+(?:loss|income|earnings))?"
                 r"(?:\s+including\s+non\s*controlling\s+interests?)?$"]),
        L("is_pl__net_income_attributable_to_nci", "Net income attributable to noncontrolling interests",
          ["Net income attributable to noncontrolling interests",
           "Less: Net income attributable to noncontrolling interests",
           "Net (income) loss attributable to noncontrolling interests",
           "Consolidated net income attributable to noncontrolling interest",
           "Net income attributable to noncontrolling interest"],
          "The part of net income attributable to noncontrolling interests, with the sign the face "
          "gives it (often printed as a deduction).",
          sign="either", scope=ATTRIBUTION, bucket="income",
          exclude=list(_NOT_PER_SHARE) + [r"comprehensive"],
          regex=[E + LESS + r"(?:consolidated\s+)?(?:net\s+)?(?:income|earnings|loss|profit)\s+"
                 r"(?:(?:loss|income|earnings|profit)\s+)?attributable\s+to\s+(?:the\s+)?(?:non\s*controlling|"
                 r"minority)\s+interests?\b.*$"]),
        L("is_pl__net_income_attributable_to_parent", "Net income attributable to the company",
          ["Net income attributable to common stockholders", "Net income attributable to shareholders",
           "Net income attributable to the Company", "Net income available to common stockholders",
           "Profit"],
          "The part of net income attributable to the filer's own stockholders, where the face "
          "prints it; empty where the filer has no noncontrolling interests and prints net income "
          "alone.",
          sign="either", scope=ATTRIBUTION, bucket="income",
          exclude=list(_NOT_PER_SHARE) + [r"non\s*controlling", r"minority", r"comprehensive"],
          regex=[E + r"(?:consolidated\s+)?(?:net\s+)?(?:income|earnings|loss|profit)(?:\s+(?:loss|income|"
                 r"earnings|profit))?\s+(?:attributable|available)\s+to\s+.*$"]),
    ],
    # ── STATEMENT OF CASH FLOWS ───────────────────────────────────────────────────────────────
    "cf_oper_indirect": [
        L("cf_oper_indirect__net_income", "Net income",
          ["Net income", "Net income (loss)", "Net loss", "Net earnings", "Consolidated net income",
           "Net (loss) income", "Profit of consolidated and affiliated companies",
           "Net income including noncontrolling interests"],
          "Net income or loss, the first row of the operating activities under the indirect "
          "method.",
          sign="either", exclude=[r"attributable", r"\bfrom\b"],
          regex=[PCF + r"(?:consolidated\s+)?net\s+(?:income|earnings|loss|profit)(?:\s+(?:loss|income|"
                 r"earnings))?(?:\s+including\s+non\s*controlling\s+interests?)?$"]),
        L("cf_oper_indirect__depreciation_and_amortization", "Depreciation and amortization",
          ["Depreciation and amortization", "Depreciation, depletion and amortization", "Depreciation",
           "Depreciation, amortization and other", "Amortization of intangible assets"],
          "Depreciation and amortisation added back to net income; where the face prints "
          "amortisation of intangibles separately, the rows are added.",
          sign="either",
          exclude=[r"debt|discount|premium|issuance|financing\s+costs|lease|contract|deferred"],
          regex=[PCF + r"depreciation\b.*$", PCF + r"amorti[sz]ation\b.*$"]),
        L("cf_oper_indirect__share_based_compensation", "Stock-based compensation",
          ["Stock-based compensation expense", "Share-based compensation expense", "Stock-based compensation",
           "Share-based compensation", "Equity-based compensation"],
          "Stock-based compensation expense, a non-cash charge, added back to net income among the "
          "adjustments to operating cash flow.",
          sign="either",
          regex=[PCF + r"(?:stock|share|equity)\s+based\s+compensation\b.*$",
                 PCF + r"stock\s+compensation\b.*$"]),
        L("cf_oper_indirect__deferred_income_taxes", "Deferred income taxes",
          ["Deferred income taxes", "Deferred income tax expense (benefit)", "Deferred taxes",
           "Deferred income tax provision", "Deferred income tax benefit"],
          "The deferred part of the income tax expense, added back to net income.",
          sign="either", regex=[PCF + r"(?:provision\s+for\s+)?deferred\s+(?:income\s+)?tax(?:es)?\b.*$"]),
        L("cf_oper_indirect__chg_in_accounts_receivable", "Change in accounts receivable",
          ["Accounts receivable", "Accounts receivable, net", "Receivables", "Trade receivables",
           "Accounts and notes receivable"],
          "The change in accounts receivable among the changes in operating assets and liabilities, "
          "with the sign the face gives it.",
          sign="either", exclude=[r"non\s*trade", r"\bvendor", r"financ"],
          regex=[PCF + _CHANGE + r"(?:trade\s+)?(?:accounts\s+)?(?:and\s+notes\s+)?receivables?(?:\s+net)?$"]),
        L("cf_oper_indirect__chg_in_inventories", "Change in inventories",
          ["Inventories", "Inventory", "Merchandise inventories"],
          "The change in inventories among the changes in operating assets and liabilities.",
          sign="either", regex=[PCF + _CHANGE + r"(?:merchandise\s+)?inventor(?:y|ies)$"]),
        L("cf_oper_indirect__chg_in_accounts_payable", "Change in accounts payable",
          ["Accounts payable", "Accounts payable and accrued liabilities", "Trade payables",
           "Accounts payable and accrued expenses"],
          "The change in accounts payable among the changes in operating assets and liabilities.",
          sign="either",
          regex=[PCF + _CHANGE + r"(?:trade\s+)?(?:accounts\s+payable|payables)(?:\s+and\s+accrued\s+"
                 r"(?:liabilities|expenses))?$"]),
        R("cf_oper_indirect__other_operating_items", "Other operating items",
          "Every operating-activities row the face prints that no line above claims, itemised: "
          "other adjustments to net income, gains and losses on sales, and the changes in other "
          "operating assets and liabilities.",
          aliases=["Other", "Other, net", "Other operating activities"],
          exclude=[r"(?:cash\s+)?(?:generated|provided|used)\s+(?:by|in|from)\s+operating\s+activities"]),
        L("cf_oper_indirect__cash_flows_oper_activ_indirect", "Net cash provided by operating activities",
          ["Net cash provided by operating activities", "Net cash provided by (used in) operating activities",
           "Net cash used in operating activities", "Cash generated by operating activities",
           "Net cash from operating activities", "Cash provided by operating activities"],
          "The operating activities total as printed, and the sum of the operating lines.",
          sign="either", role="subtotal",
          regex=[E + r"(?:net\s+)?cash(?:\s+flows?)?(?:\s+(?:provided|generated|used|from|by|in))+\s+"
                 r"operating\s+activities\b.*$",
                 E + r"(?:net\s+)?cash(?:\s+(?:provided|generated|used|from|by|in))+\s+operations$"],
          roll=("sum", ["cf_oper_indirect__net_income", "cf_oper_indirect__depreciation_and_amortization",
                        "cf_oper_indirect__share_based_compensation",
                        "cf_oper_indirect__deferred_income_taxes",
                        "cf_oper_indirect__chg_in_accounts_receivable",
                        "cf_oper_indirect__chg_in_inventories",
                        "cf_oper_indirect__chg_in_accounts_payable",
                        "cf_oper_indirect__other_operating_items"])),
    ],
    "cf_investing": [
        L("cf_investing__purchase_ppe", "Purchases of property and equipment",
          ["Purchases of property and equipment", "Payments for acquisition of property, plant and equipment",
           "Capital expenditures", "Additions to property and equipment",
           "Purchases of property, plant and equipment", "Payments for property and equipment"],
          "Cash paid for property, plant and equipment (capital expenditures), with the negative "
          "sign the face gives it.",
          sign="either",
          regex=[PCF + r"(?:purchases?|acquisitions?|additions?|payments?\s+for(?:\s+(?:the\s+)?"
                 r"(?:purchase|acquisition)s?)?|expenditures?\s+for|investments?\s+in)\s+(?:of\s+|to\s+)?"
                 + _PPE + r"\b.*$",
                 PCF + r"capital\s+(?:expenditures|spending)\b.*$"]),
        L("cf_investing__proceeds_sale_ppe", "Proceeds from sale of property and equipment",
          ["Proceeds from sale of property and equipment", "Proceeds from disposal of property, plant and equipment",
           "Proceeds from the sale of property and equipment"],
          "Cash received on the sale or disposal of property, plant and equipment.",
          sign="either",
          regex=[PCF + r"proceeds\s+(?:from|on)\s+(?:the\s+)?(?:sales?|disposals?|dispositions?)\s+of\s+"
                 + _PPE + r"\b.*$"]),
        L("cf_investing__acquisitions", "Acquisitions, net of cash acquired",
          ["Acquisitions, net of cash acquired", "Acquisition of businesses, net of cash acquired",
           "Payments for acquisitions, net of cash acquired", "Business acquisitions, net of cash acquired",
           "Payments to acquire businesses, net of cash acquired"],
          "Cash paid to acquire businesses, net of the cash they held; where the face prints "
          "acquisitions together with purchases of intangible assets as one row, that row.",
          sign="either",
          exclude=[(r"^\W*(?:purchases?|payments?\s+for|additions?)\s+(?:of\s+|to\s+)?(?:property|plant|"
                   r"equipment|securities|investments|intangible)")],
          regex=[PCF + r"(?:payments?\s+(?:for|to\s+acquire)\s+)?(?:business(?:es)?\s+)?acquisitions?"
                 r"(?:\s+of\s+(?:businesses|companies|subsidiaries))?(?:\s+net\s+of\s+cash\s+acquired)?$",
                 PCF + r"payments?\s+to\s+acquire\s+businesses\b.*$",
                 PCF + r"acquisitions?\s+of\s+(?:businesses|companies|subsidiaries)\b.*$"]),
        L("cf_investing__purchase_investments", "Purchases of investments",
          ["Purchases of marketable securities", "Purchases of investments",
           "Payments for acquisition of marketable securities", "Purchases of short-term investments"],
          "Cash paid for marketable securities and other investments; where the face prints "
          "purchases of several kinds of securities on separate rows, the rows are added.",
          sign="either",
          regex=[PCF + r"(?:purchases?|acquisitions?|payments?\s+for(?:\s+(?:the\s+)?(?:purchase|acquisition)s?)?)"
                 r"\s+(?:of\s+)?" + _SECURITIES + r"\b.*$"]),
        L("cf_investing__proceeds_from_investments", "Proceeds from sales and maturities of investments",
          ["Proceeds from maturities of marketable securities", "Proceeds from sales of marketable securities",
           "Maturities of investments", "Sales of investments", "Proceeds from sales and maturities of investments"],
          "Cash received on the sale, maturity or redemption of marketable securities and other "
          "investments; where the face prints sales and maturities on separate rows, the rows are "
          "added.",
          sign="either",
          regex=[PCF + r"(?:proceeds\s+from\s+(?:the\s+)?)?(?:sales?|maturities|maturity|redemptions?|"
                 r"disposals?)(?:\s+and\s+(?:sales?|maturities|redemptions?))?\s+of\s+" + _SECURITIES + r"\b.*$"]),
        R("cf_investing__other_invest_cash_flows", "Other investing activities",
          "Every investing-activities row the face prints that no line above claims, itemised: "
          "purchases of intangible assets, proceeds from divestitures, loans made and collected.",
          aliases=["Other", "Other investing activities", "Other, net"],
          exclude=[r"(?:cash\s+)?(?:generated|provided|used)\s+(?:by|in|from)\s+investing\s+activities"]),
        L("cf_investing__cash_flows_from_invest_activities", "Net cash used in investing activities",
          ["Net cash used in investing activities", "Net cash provided by (used in) investing activities",
           "Cash used in investing activities", "Net cash from investing activities"],
          "The investing activities total as printed, and the sum of the investing lines.",
          sign="either", role="subtotal",
          regex=[E + r"(?:net\s+)?cash(?:\s+flows?)?(?:\s+(?:provided|generated|used|from|by|in))+\s+"
                 r"investing\s+activities\b.*$",
                 E + r"(?:net\s+)?cash(?:\s+(?:provided|generated|used|from|by|in))+\s+investing$"],
          roll=("sum", ["cf_investing__purchase_ppe", "cf_investing__proceeds_sale_ppe",
                        "cf_investing__acquisitions", "cf_investing__purchase_investments",
                        "cf_investing__proceeds_from_investments",
                        "cf_investing__other_invest_cash_flows"])),
    ],
    "cf_financing": [
        L("cf_financing__proceeds_from_debt", "Proceeds from issuance of debt",
          ["Proceeds from issuance of long-term debt", "Proceeds from issuance of term debt, net",
           "Proceeds from issuance of debt", "Proceeds from long-term debt", "Proceeds from borrowings"],
          "Cash received from issuing long-term debt (notes, bonds, term loans), net of issuance "
          "costs where the face says so. Commercial paper is the next line but one.",
          sign="either", exclude=[r"short\s+term", r"commercial\s+paper", r"repay"],
          regex=[PCF + r"(?:proceeds\s+from\s+(?:the\s+)?(?:issuances?\s+of\s+)?|issuances?\s+of\s+)" + _DEBT
                 + r"\b.*$"]),
        L("cf_financing__repayment_of_debt", "Repayments of debt",
          ["Repayments of term debt", "Repayments of debt", "Payments of long-term debt",
           "Repayments of long-term debt", "Repayment of borrowings", "Principal payments on long-term debt"],
          "Cash paid to repay or retire long-term debt (notes, bonds, term loans), with the "
          "negative sign the face gives it.",
          sign="either", exclude=[r"short\s+term", r"commercial\s+paper", r"lease"],
          regex=[PCF + r"(?:repayments?|payments?|principal\s+payments?|retirements?|redemptions?|"
                 r"extinguishment)\s+(?:of|on)\s+(?:the\s+)?" + _DEBT + r"\b.*$"]),
        L("cf_financing__net_change_short_term_debt", "Net change in short-term borrowings",
          ["Net change in short-term borrowings", "Commercial paper, net",
           "Proceeds from/(Repayments of) commercial paper, net", "Short-term borrowings, net"],
          "The net cash from or to commercial paper and other short-term borrowings, as one "
          "printed row.",
          sign="either",
          regex=[PCF + r"(?:\w+\s+){0,6}?(?:commercial\s+paper|short\s+term\s+(?:debt|borrowings?|notes|loans))"
                 r"\b.*$"]),
        L("cf_financing__share_repurchases", "Repurchases of common stock",
          ["Repurchases of common stock", "Common stock repurchased", "Purchase of Company stock",
           "Payments for repurchase of common stock", "Purchases of treasury stock"],
          "Cash paid to repurchase the filer's own common stock, whether retired or held in "
          "treasury, with the negative sign the face gives it.",
          sign="either",
          regex=[PCF + r"(?:repurchases?|purchases?|payments?\s+for\s+(?:the\s+)?repurchases?|buybacks?)\s+"
                 r"(?:of\s+)?(?:company\s+|common\s+|treasury\s+|class\s+[a-c]\s+|our\s+)?(?:stock|shares|"
                 r"common\s+stock)\b.*$",
                 PCF + r"(?:common\s+)?(?:stock|shares?)\s+repurchased\b.*$",
                 PCF + r"share\s+repurchases?\b.*$"]),
        L("cf_financing__dividends_paid", "Dividends paid",
          ["Payments for dividends and dividend equivalents", "Common stock cash dividends paid",
           "Dividends paid", "Cash dividends paid", "Dividends paid to shareholders"],
          "Cash dividends paid to the filer's stockholders. Dividends paid to noncontrolling "
          "interests are the leftover line's.",
          sign="either", exclude=[r"non\s*controlling", r"minority", r"received"],
          regex=[PCF + r"(?:payments?\s+(?:for|of)\s+)?(?:common\s+(?:stock\s+)?)?(?:cash\s+)?dividends?\b.*$"]),
        L("cf_financing__proceeds_from_stock_issuance", "Proceeds from issuance of common stock",
          ["Proceeds from issuance of common stock", "Common stock issued",
           "Proceeds from exercise of stock options", "Proceeds from employee stock plans"],
          "Cash received from issuing stock, including under employee stock plans and option "
          "exercises.",
          sign="either",
          regex=[PCF + r"proceeds\s+from\s+(?:the\s+)?(?:issuances?|sales?)\s+of\s+(?:common\s+|class\s+[a-c]\s+)?"
                 r"(?:stock|shares|equity)\b.*$",
                 PCF + r"proceeds\s+from\s+(?:the\s+)?exercises?\s+of\s+(?:employee\s+)?(?:stock|share)\s+"
                 r"options\b.*$",
                 PCF + r"proceeds\s+from\s+(?:employee\s+)?(?:stock|share)\s+(?:plans|option\s+exercises|purchase"
                 r"\s+plans?)\b.*$",
                 PCF + r"(?:common\s+)?stock\s+issued\b.*$"]),
        R("cf_financing__other_financing_cash_flows", "Other financing activities",
          "Every financing-activities row the face prints that no line above claims, itemised: "
          "taxes paid for the net share settlement of equity awards, finance lease payments, "
          "dividends paid to noncontrolling interests.",
          aliases=["Other", "Other financing activities", "Other, net"],
          exclude=[r"(?:cash\s+)?(?:generated|provided|used)\s+(?:by|in|from)\s+financing\s+activities",
                   r"exchange\s+rate", r"increase|decrease|net\s+change", r"beginning|end\s+of|ending"]),
        L("cf_financing__cash_flows_from_finance_activities", "Net cash used in financing activities",
          ["Net cash used in financing activities", "Net cash provided by (used in) financing activities",
           "Cash used in financing activities", "Net cash from financing activities"],
          "The financing activities total as printed, and the sum of the financing lines.",
          sign="either", role="subtotal",
          regex=[E + r"(?:net\s+)?cash(?:\s+flows?)?(?:\s+(?:provided|generated|used|from|by|in))+\s+"
                 r"financing\s+activities\b.*$",
                 E + r"(?:net\s+)?cash(?:\s+(?:provided|generated|used|from|by|in))+\s+financing$"],
          roll=("sum", ["cf_financing__proceeds_from_debt", "cf_financing__repayment_of_debt",
                        "cf_financing__net_change_short_term_debt", "cf_financing__share_repurchases",
                        "cf_financing__dividends_paid", "cf_financing__proceeds_from_stock_issuance",
                        "cf_financing__other_financing_cash_flows"])),
        L("cf_financing__net_foreign_exchange_difference", "Effect of exchange rate changes",
          ["Effect of exchange rate changes on cash, cash equivalents and restricted cash",
           "Effect of exchange rate changes on cash and cash equivalents",
           "Effect of foreign exchange rates on cash and cash equivalents"],
          "The effect of exchange-rate changes on cash, cash equivalents and restricted cash.",
          sign="either",
          regex=[E + r"(?:net\s+)?(?:effects?|impacts?)\s+of\s+(?:changes\s+in\s+)?(?:foreign\s+)?(?:currency\s+)?"
                 r"(?:exchange|rate)\b.*$"]),
        L("cf_financing__net_increase_in_cash", "Net increase (decrease) in cash",
          ["Net increase (decrease) in cash, cash equivalents and restricted cash",
           "Increase/(Decrease) in cash, cash equivalents, and restricted cash and cash equivalents",
           "Net change in cash and cash equivalents", "Net increase in cash and cash equivalents",
           "Net decrease in cash and cash equivalents"],
          "The net change in cash, cash equivalents and restricted cash for the year: the three "
          "activity totals and the effect of exchange rates.",
          sign="either", role="subtotal",
          regex=[E + r"(?:net\s+)?(?:increase|decrease|change)(?:\s+(?:increase|decrease))?\s+in\s+cash\b.*$"],
          roll=("sum", ["cf_oper_indirect__cash_flows_oper_activ_indirect",
                        "cf_investing__cash_flows_from_invest_activities",
                        "cf_financing__cash_flows_from_finance_activities",
                        "cf_financing__net_foreign_exchange_difference"])),
        L("cf_financing__beg_of_period_cash_and_cash_equiv", "Cash at beginning of year",
          ["Cash, cash equivalents and restricted cash, beginning of period",
           "Cash and cash equivalents, beginning of year",
           "Cash, cash equivalents, and restricted cash and cash equivalents, beginning balances",
           "Cash and cash equivalents at beginning of year", "Beginning of period", "Beginning of year"],
          "Cash, cash equivalents and restricted cash at the start of the year.",
          sign="either",
          regex=[E + r"cash\b.*\b(?:beginning|start)\s+(?:of\s+(?:the\s+)?(?:period|year|fiscal\s+year)|"
                 r"balances?)\b.*$",
                 E + r"(?:balance\s+)?(?:at\s+)?(?:the\s+)?beginning\s+of\s+(?:the\s+)?(?:period|year)$"]),
        L("cf_financing__end_of_period_cash_and_cash_equiv", "Cash at end of year",
          ["Cash, cash equivalents and restricted cash, end of period",
           "Cash and cash equivalents, end of year",
           "Cash, cash equivalents, and restricted cash and cash equivalents, ending balances",
           "Cash and cash equivalents at end of year", "End of period", "End of year"],
          "Cash, cash equivalents and restricted cash at the end of the year: the opening balance "
          "and the net change.",
          sign="either", role="subtotal",
          regex=[E + r"cash\b.*\b(?:end\s+of\s+(?:the\s+)?(?:period|year|fiscal\s+year)|ending\s+balances?)"
                 r"\b.*$",
                 E + r"(?:balance\s+)?(?:at\s+)?(?:the\s+)?end\s+of\s+(?:the\s+)?(?:period|year)$"],
          roll=("sum", ["cf_financing__net_increase_in_cash",
                        "cf_financing__beg_of_period_cash_and_cash_equiv"])),
    ],
}

# The closing cash balance ends the statement: the supplemental disclosures printed below it (cash
# paid for interest and taxes, non-cash investing) are not rows of any section, and the sweep
# stops at a statement-level total (`stages.residual`, the `*_top_level` scopes).
SCOPE_OVERRIDES = {"cf_financing__end_of_period_cash_and_cash_equiv": ["cf_top_level"]}

# ── ADDITIONAL ITEMS: read from the notes and the auditor's report ─────────────────────────────
#
# A fourth statement of the template, `notes`, and the only lines of the set that are not read
# off a statement's face. Each says where it is read from and nothing more:
#
#   * a figure the note TABULATES under a caption 10-K filers print the same way ("Raw materials",
#     "Total current", "Thereafter", "Accumulated depreciation") is read by a `note_source` with
#     row patterns, inside the note its title names — and the model may still cite it;
#   * a figure whose caption is not fixed or that is often stated in a sentence (letters of credit
#     outstanding, the fair value of debt) is the model's alone, with `note_terms` saying which
#     notes it is shown. THE DEBT MATURITY TABLE IS THE MODEL'S, row patterns or not, and cited
#     from the page text: a note's second table reads on the first table's column grid, so on two
#     test layouts the single-column maturities landed under the comparative's "2024" heading, their
#     year captions lost or cut to "Fiscal", and a "Thereafter" row pattern put this year's figure
#     in the prior year;
#   * TEXT is lifted off the page as printed (`phrase`) and checked against that page: the
#     auditor, the opinion heading and sentences, the going-concern paragraph, the first critical
#     audit matter, the contingencies and related-party sentences, and the effective tax rate with
#     its percent sign, so no statement unit can scale it. Nothing is GENERATED: a `prose` line
#     cannot be loaded (its prompt is folded into the definition before the check that requires
#     one) and nothing downstream reads one (`services.text_lines`), and a written summary could
#     not be checked against the page in any case.
#
# NO NOTE LINE DECLARES A SECTION SCOPE. A note-reading line scoped to a section is closed to a
# note the face cites from a different one (`note_sections.open_to`), and every note here is
# cited from the face — "Long-term debt (Note 5)" ties the debt note to the non-current
# liabilities. Unscoped, the line is open to every note, and its title patterns choose.
NOTE_SECTIONS = [
    ("notes_cash", "Cash flow supplemental"),
    ("notes_debt", "Debt"),
    ("notes_tax", "Income taxes"),
    ("notes_assets", "Inventories and property"),
    ("notes_audit", "Auditor's report"),
    ("notes_contingencies", "Commitments and contingencies"),
    ("notes_related", "Related parties"),
]


def NS(title=(), rows=(), none=(), terms=()):
    """A `note_source`: the note's title patterns, the rows that count, the rows that never do,
    and the plain terms that say which notes the model is shown."""
    out = {}
    if title:
        out["note_title_any"] = list(title)
    if rows:
        out["row_caption_any"] = list(rows)
    if none:
        out["row_caption_none"] = list(none)
    if terms:
        out["note_terms"] = list(terms)
    return out


def N(key, label, definition, *, route="note_tables", note=None, structure="value",
      statements=("notes",), aliases=(), regex=(), exclude=(), scope=(), sign="either",
      instant=False):
    """One additional item."""
    return {"key": key, "label": label, "definition": definition, "route": route, "note": note,
            "structure": structure, "statements": list(statements),
            "aliases": list(aliases), "regex": list(regex), "exclude": list(exclude),
            "scope": list(scope), "sign": sign, "instant": instant}


_DEBT_TITLE = [r"\bdebt\b", r"\bborrowings\b", r"notes\s+payable", r"credit\s+(?:facilities|agreements?)"]
_DEBT_TERMS = ["debt", "long-term debt", "term debt", "borrowings", "notes payable", "senior notes",
               "credit facility", "maturities of debt"]
_TAX_TITLE = [r"income\s+tax", r"taxes\s+on\s+income"]
_TAX_TERMS = ["income taxes", "provision for income taxes", "current and deferred income taxes",
              "effective tax rate"]
_DETAIL = r"(?:financial\s+statement|balance\s+sheet)\s+(?:details|components|information)"
_INV_TITLE = [r"inventor", _DETAIL]
_PPE_TITLE = [r"property(?:,)?\s+(?:plant(?:,)?\s+)?(?:and\s+)?equipment", r"fixed\s+assets", _DETAIL]
_AUDIT_TERMS = ["report of independent registered public accounting firm", ("opinion on the "
                "financial statements"), "critical audit matters", ("internal control over financial "
                "reporting")]
_CC_TITLE = [r"commitments", r"contingenc", r"guarantees", r"legal\s+proceedings"]
_CC_TERMS = ["commitments and contingencies", "contingencies", "legal proceedings", "litigation",
             "guarantees", "letters of credit", "purchase obligations"]
_RP_TITLE = [r"related[\s-]+part(?:y|ies)", r"transactions\s+with\s+affiliates"]
_RP_TERMS = ["related party transactions", "related parties", "transactions with affiliates"]
# The title a 10-K's auditor's report prints at its head: the name that sends a text line the page.
_REPORT = ["Report of Independent Registered Public Accounting Firm"]
_YEAR_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}

NOTE_LINES = {
    "notes_cash": [
        N("notes_cash__interest_paid", "Cash paid for interest",
          "Interest paid in cash during the year, net of amounts capitalised where the filing nets "
          "them, as the supplemental cash flow disclosure prints it: below the statement of cash "
          "flows or in a note. Not interest received.",
          route="anywhere", statements=("cash_flow", "notes"), scope=["cf_top_level"],
          aliases=["Cash paid for interest", "Interest paid", "Cash paid for interest, net of amounts capitalized",
                   "Interest paid, net of capitalized interest", "Cash paid during the year for interest"],
          regex=[E + r"(?:cash\s+paid\s+(?:during\s+the\s+(?:year|period)\s+)?for\s+interest|interest\s+paid)\b.*$"],
          exclude=[r"received", r"income\s+tax"],
          note=NS(title=[r"supplemental", r"cash\s+flow\s+information"],
                  rows=[r"^\s*(?:cash\s+paid\s+(?:during\s+the\s+(?:year|period)\s+)?for\s+)?interest(?:\s+paid)?\b"],
                  none=[r"received|income|capitali[sz]ed\s+interest\s*$"],
                  terms=["supplemental cash flow information", "cash paid for interest"])),
        N("notes_cash__income_taxes_paid", "Cash paid for income taxes",
          "Income taxes paid in cash during the year, net of refunds where the filing nets them, "
          "as the supplemental cash flow disclosure prints it: below the statement of cash flows "
          "or in a note.",
          route="anywhere", statements=("cash_flow", "notes"), scope=["cf_top_level"],
          aliases=["Cash paid for income taxes, net", "Income taxes paid", "Cash paid for income taxes",
                   "Income taxes paid, net of refunds", "Cash paid during the year for income taxes"],
          regex=[E + r"(?:cash\s+paid\s+(?:during\s+the\s+(?:year|period)\s+)?for\s+(?:income\s+)?taxes|"
                 r"(?:income\s+)?taxes\s+paid)\b.*$"],
          exclude=[r"interest", r"refunds?\s+received"],
          note=NS(title=[r"supplemental", r"cash\s+flow\s+information"],
                  rows=[(r"^\s*(?:cash\s+paid\s+(?:during\s+the\s+(?:year|period)\s+)?for\s+)?(?:income\s+)?taxes"
                        r"(?:\s+paid)?\b")],
                  none=[r"interest|deferred|payable"],
                  terms=["supplemental cash flow information", "cash paid for income taxes"])),
    ],
    "notes_debt": [
        *[N(f"notes_debt__due_in_year_{n}", f"Debt maturing in year {n}",
            f"The principal of debt due in the {_YEAR_WORDS[n]} fiscal year after the balance-sheet "
            f"date, from the debt note's table of future principal payments or maturities: the row "
            f"for that year ('{2025 + n}' for a 2025 year-end). Principal, before unamortised "
            f"discount and issuance costs; the current year-end only. Lease payments and interest "
            f"are not this line. Cite the printed table line from the page.",
            route="anywhere", note=NS(terms=_DEBT_TERMS), instant=True) for n in range(1, 6)],
        N("notes_debt__due_thereafter", "Debt maturing thereafter",
          "The principal of debt due after the fifth fiscal year following the balance-sheet date: "
          "the 'Thereafter' row of the debt note's table of future principal payments. Lease "
          "payments and interest are not this line. Cite the printed table line from the page.",
          route="anywhere", note=NS(terms=_DEBT_TERMS), instant=True),
        N("notes_debt__total_principal", "Total debt principal",
          "The total principal (face) amount of debt outstanding at the balance-sheet date, as the "
          "debt note prints it: the total of the table of future principal payments or the 'total "
          "principal' row, before unamortised discount, premium and issuance costs and before the "
          "current portion is deducted.",
          note=NS(title=_DEBT_TITLE,
                  rows=[r"^\s*total\s+(?:term\s+|long[\s-]+term\s+)?(?:debt|borrowings|notes)\s+principal\b",
                        r"^\s*total\s+(?:principal|face)\s+(?:amount|value)\b",
                        r"^\s*total\s+principal\b"],
                  none=[r"fair\s+value|current\s+portion|less|net\s+of|unamortized|carrying"],
                  terms=_DEBT_TERMS), instant=True),
        N("notes_debt__fair_value", "Fair value of debt",
          "The estimated fair value of the filer's debt at the balance-sheet date, as the debt or "
          "fair-value note states it, often in a sentence ('the fair value of our long-term debt "
          "was $88.4 billion'). Expressed in the statements' unit.",
          route="anywhere", note=NS(terms=_DEBT_TERMS + ["fair value"]), instant=True),
    ],
    "notes_tax": [
        N("notes_tax__current_income_tax", "Current income tax expense",
          "The current part of the income tax expense for the year, federal, state and foreign "
          "together: the 'Total current' row of the income tax note's table of the components of "
          "the provision.",
          note=NS(title=_TAX_TITLE,
                  rows=[r"^\s*total\s+current\b(?!\s+(?:assets|liabilit))",
                        r"^\s*current\s+(?:income\s+)?tax(?:es)?(?:\s+(?:expense|provision))?\s*$"],
                  none=[r"deferred|\bassets?\b|liabilit|payable|receivable"], terms=_TAX_TERMS)),
        N("notes_tax__deferred_income_tax", "Deferred income tax expense (benefit)",
          "The deferred part of the income tax expense or benefit for the year: the 'Total "
          "deferred' row of the income tax note's table of the components of the provision. Not "
          "the deferred tax assets or liabilities at the year-end.",
          note=NS(title=_TAX_TITLE,
                  rows=[r"^\s*total\s+deferred\b(?!\s+(?:income\s+)?tax(?:es)?\s+(?:assets|liabilit))",
                        r"^\s*deferred\s+(?:income\s+)?tax(?:es)?\s+(?:expense|benefit|provision)\b",
                        r"^\s*deferred\s+(?:income\s+)?tax(?:es)?\s*$"],
                  none=[r"\bassets?\b|liabilit|valuation"], terms=_TAX_TERMS)),
        N("notes_tax__effective_tax_rate", "Effective tax rate",
          "The effective income tax rate for the year as the filing prints it, with its percent "
          "sign ('16.0%'): from the income tax note's rate reconciliation or the sentence stating "
          "it. The current year only.",
          route="anywhere", structure="phrase", note=NS(terms=_TAX_TERMS)),
    ],
    "notes_assets": [
        N("notes_assets__raw_materials", "Inventories: raw materials",
          "Raw materials and supplies held in inventory at the year-end, from the inventories note "
          "or the balance-sheet details note.",
          note=NS(title=_INV_TITLE, rows=[r"^\s*raw\s+materials?\b"], none=[r"total|reserve|lifo"],
                  terms=["inventories", "raw materials"]), instant=True, sign="positive_expected"),
        N("notes_assets__work_in_process", "Inventories: work in process",
          "Work in process held in inventory at the year-end, from the inventories note or the "
          "balance-sheet details note.",
          note=NS(title=_INV_TITLE, rows=[r"^\s*work[\s-]+in[\s-]+(?:process|progress)\b"],
                  none=[r"total|reserve|lifo"], terms=["inventories", "work in process"]),
          instant=True, sign="positive_expected"),
        N("notes_assets__finished_goods", "Inventories: finished goods",
          "Finished goods (or finished products) held in inventory at the year-end, from the "
          "inventories note or the balance-sheet details note.",
          note=NS(title=_INV_TITLE, rows=[r"^\s*finished\s+(?:goods|products)\b"],
                  none=[r"total|reserve|lifo"], terms=["inventories", "finished goods"]),
          instant=True, sign="positive_expected"),
        N("notes_assets__ppe_at_cost", "Property, plant and equipment, at cost",
          "Property, plant and equipment at cost, before accumulated depreciation: the gross total "
          "of the property note's table ('Gross property, plant and equipment', 'Total, at cost').",
          note=NS(title=_PPE_TITLE,
                  rows=[r"^\s*(?:total\s+)?gross\s+property\b",
                        (r"^\s*(?:total\s+)?property(?:,)?\s+(?:plant(?:,)?\s+)?(?:and\s+)?equipment(?:,)?\s+"
                        r"(?:at\s+cost|gross)\b"),
                        r"^\s*total(?:,)?\s+at\s+cost\b"],
                  none=[r"\bnet\b", r"accumulated", r"expense"],
                  terms=["property, plant and equipment", "gross"]), instant=True,
          sign="positive_expected"),
        N("notes_assets__accumulated_depreciation", "Accumulated depreciation",
          "Accumulated depreciation and amortisation of property, plant and equipment at the "
          "year-end, with the sign the property note gives it (usually printed as a deduction).",
          note=NS(title=_PPE_TITLE,
                  rows=[r"^\s*(?:less\s*:?\s*)?accumulated\s+depreciation\b"],
                  none=[r"expense|charge|for\s+the\s+year|intangible"],
                  terms=["property, plant and equipment", "accumulated depreciation"]), instant=True),
    ],
    "notes_audit": [
        N("notes_audit__auditor", "Independent auditor",
          "The registered public accounting firm that signs the report on the consolidated "
          "financial statements, as printed in its signature without the '/s/' (for example "
          "'Hartwell & Rowe LLP'). Not the engagement partner.",
          route="anywhere", structure="phrase", note=NS(terms=_AUDIT_TERMS), aliases=_REPORT),
        N("notes_audit__opinion_type", "Audit opinion (as headed)",
          "The kind of opinion the auditor expresses on the consolidated financial statements, as "
          "the report names it: the heading of its opinion section, copied as printed. A US "
          "report heads an unqualified opinion 'Opinion on the Financial Statements', and a "
          "modified one 'Qualified Opinion', 'Adverse Opinion' or 'Disclaimer of Opinion'. Not "
          "the heading of the opinion on internal control.",
          route="anywhere", structure="phrase", note=NS(terms=_AUDIT_TERMS),
          aliases=["Audit Opinion"] + _REPORT),
        N("notes_audit__opinion_on_financial_statements", "Opinion on the financial statements",
          "The auditor's opinion on the consolidated financial statements, in the auditor's own "
          "words: the sentence of the report's opinion section that begins 'In our opinion', "
          "copied as printed. Not the opinion on internal control.",
          route="anywhere", structure="phrase", note=NS(terms=_AUDIT_TERMS), aliases=_REPORT),
        N("notes_audit__opinion_on_internal_control", "Opinion on internal control",
          "The auditor's opinion on the filer's internal control over financial reporting, in the "
          "auditor's own words: the sentence of that report's opinion section that begins 'In our "
          "opinion', copied as printed, whether it finds control effective or not. Empty where the "
          "10-K carries no auditor's report on internal control.",
          route="anywhere", structure="phrase", note=NS(terms=_AUDIT_TERMS), aliases=_REPORT),
        N("notes_audit__going_concern", "Going concern paragraph",
          "The first sentence of the auditor's going-concern paragraph, copied as printed: the "
          "sentence stating that conditions raise substantial doubt about the filer's ability to "
          "continue as a going concern. Empty when the report includes no such paragraph.",
          route="anywhere", structure="phrase", note=NS(terms=_AUDIT_TERMS + ["going concern"]),
          aliases=_REPORT),
        N("notes_audit__critical_audit_matter", "Critical audit matter",
          "The title of the first critical audit matter the auditor's report communicates, copied "
          "as printed (for example 'Uncertain Tax Positions'); or the report's sentence stating that "
          "there are no critical audit matters.",
          route="anywhere", structure="phrase", note=NS(terms=_AUDIT_TERMS), aliases=_REPORT),
    ],
    "notes_contingencies": [
        N("notes_contingencies__assessment", "Contingencies: the filer's assessment",
          "The sentence of the commitments and contingencies note (or the legal proceedings note) "
          "that states the filer's conclusion on its legal proceedings, claims and other "
          "contingencies, copied as printed: whether a loss is probable, an amount is accrued, or "
          "the outcome is not expected to have a material adverse effect (ASC 450). Empty when "
          "the notes disclose no contingencies.",
          route="anywhere", structure="phrase", note=NS(terms=_CC_TERMS)),
        N("notes_contingencies__guarantees_and_letters_of_credit", "Guarantees and letters of credit",
          "The guarantees, standby letters of credit and surety bonds the filer has issued and that "
          "are outstanding at the balance-sheet date, summed: the maximum potential amount of future "
          "payments the notes state (ASC 460). Not the undrawn capacity of a credit facility.",
          route="anywhere", note=NS(terms=_CC_TERMS), instant=True),
        N("notes_contingencies__accrued_loss_contingencies", "Accrued loss contingencies",
          "The amount recognised as a liability at the balance-sheet date for loss contingencies "
          "(litigation, claims, environmental and similar matters), as the notes state it. Empty "
          "where the filing states no amount.",
          route="anywhere", note=NS(terms=_CC_TERMS), instant=True),
        N("notes_contingencies__purchase_obligations", "Purchase obligations",
          "The total of the filer's unconditional purchase obligations and purchase commitments at "
          "the balance-sheet date, as the commitments note prints or states it.",
          route="anywhere",
          note=NS(title=_CC_TITLE,
                  rows=[r"^\s*total\s+(?:unconditional\s+)?purchase\s+(?:obligations|commitments)\b"],
                  terms=_CC_TERMS), instant=True),
    ],
    "notes_related": [
        N("notes_related__disclosure", "Related parties: the disclosure",
          "The sentence of the related-party note that describes the principal related-party "
          "relationship and the transactions with it, or that states the filer had no material "
          "related-party transactions, copied as printed (ASC 850). Empty when the filing says "
          "nothing about related parties.",
          route="anywhere", structure="phrase", note=NS(terms=_RP_TERMS)),
        N("notes_related__sales_to_related_parties", "Sales to related parties",
          "Revenue from sales of goods and services to related parties during the year, as the "
          "related-party note prints or states it.",
          route="anywhere",
          note=NS(title=_RP_TITLE,
                  rows=[r"^\s*(?:net\s+)?(?:sales|revenues?)\s+(?:to|from)\b"],
                  none=[r"due|receivable|payable|balance"], terms=_RP_TERMS)),
        N("notes_related__purchases_from_related_parties", "Purchases from related parties",
          "Purchases of goods and services from related parties during the year, including rent and "
          "fees paid to them, as the related-party note prints or states it; where the note prints "
          "several kinds on separate rows, the rows are added.",
          route="anywhere",
          note=NS(title=_RP_TITLE,
                  rows=[r"^\s*purchases?\s+(?:of\s+[a-z ]{0,40}?)?from\b",
                        r"^\s*(?:fees|rent|payments)\s+(?:paid\s+)?to\b"],
                  none=[r"due|receivable|payable|balance"], terms=_RP_TERMS)),
        N("notes_related__due_from_related_parties", "Amounts due from related parties",
          "Receivables from related parties at the balance-sheet date, as the related-party note "
          "prints or states them.",
          route="anywhere",
          note=NS(title=_RP_TITLE,
                  rows=[r"^\s*(?:amounts?\s+)?(?:due|receivables?)\s+from\b"],
                  terms=_RP_TERMS), instant=True),
        N("notes_related__due_to_related_parties", "Amounts due to related parties",
          "Payables to related parties at the balance-sheet date, as the related-party note prints "
          "or states them.",
          route="anywhere",
          note=NS(title=_RP_TITLE,
                  rows=[r"^\s*(?:amounts?\s+)?(?:due|payables?)\s+to\b"],
                  terms=_RP_TERMS), instant=True),
    ],
}


def _hint(aliases: list[str], statement: str) -> list[str]:
    """The line's own captions as one rule-tier regex, behind a leading enumerator, a "Less:" and
    a heading row reconstruction glues on (`build_icon_pair._GLUED_BEFORE`, and the statement's
    own: `_BS_HEADS`, `_CF_HEADS`)."""
    from app.services.mapping import normalize_label

    alts = {normalize_label(a) for a in aliases if normalize_label(a)}
    if not alts:
        return []
    heads = icon._alternation(icon._GLUED_BEFORE)
    heads += {"balance_sheet": "|" + _BS_HEADS, "cash_flow": "|" + _CF_HEADS}.get(statement, "")
    before = rf"(?:(?:{heads})\s+{icon._ENUM})?"
    return [rf"^{icon._ENUM}{before}{LESS}(?:{icon._alternation(alts)})$"]


KPIS = {"intermediates": [], "ratios": [
    {"key": "current_ratio", "label": "Current ratio", "category": "Liquidity", "unit": "x",
     "numerator": [{"key": "bs_ca__total_current_assets"}],
     "denominator": [{"key": "bs_cl__total_current_liabilities"}]},
    {"key": "quick_ratio", "label": "Quick ratio", "category": "Liquidity", "unit": "x",
     "numerator": [{"key": "bs_ca__total_current_assets"}, {"key": "bs_ca__inventories", "sign": -1}],
     "denominator": [{"key": "bs_cl__total_current_liabilities"}]},
    {"key": "cash_ratio", "label": "Cash ratio", "category": "Liquidity", "unit": "x",
     "numerator": [{"key": "bs_ca__cash_and_cash_equivalents"},
                   {"key": "bs_ca__short_term_investments", "optional": True}],
     "denominator": [{"key": "bs_cl__total_current_liabilities"}]},
    {"key": "operating_cash_flow_ratio", "label": "Operating cash flow ratio", "category": "Liquidity",
     "unit": "x", "numerator": [{"key": "cf_oper_indirect__cash_flows_oper_activ_indirect"}],
     "denominator": [{"key": "bs_cl__total_current_liabilities"}]},
    {"key": "debt_to_equity", "label": "Debt to equity", "category": "Leverage", "unit": "x",
     "numerator": [{"key": "bs_cl__short_term_debt", "optional": True},
                   {"key": "bs_ncl__long_term_debt", "optional": True}],
     "denominator": [{"key": "bs_equity__total_equity"}]},
    {"key": "liabilities_to_equity", "label": "Liabilities to equity", "category": "Leverage",
     "unit": "x", "numerator": [{"key": "bs_cl__total_liabilities"}],
     "denominator": [{"key": "bs_equity__total_equity"}]},
    {"key": "debt_ratio", "label": "Debt ratio (liabilities/assets)", "category": "Leverage",
     "unit": "%", "numerator": [{"key": "bs_cl__total_liabilities"}],
     "denominator": [{"key": "bs_ca__total_assets"}]},
    {"key": "equity_ratio", "label": "Equity ratio", "category": "Leverage", "unit": "%",
     "numerator": [{"key": "bs_equity__total_equity"}], "denominator": [{"key": "bs_ca__total_assets"}]},
    {"key": "interest_coverage", "label": "Interest coverage (operating income/interest)",
     "category": "Leverage", "unit": "x", "numerator": [{"key": "is_pl__operating_income"}],
     "denominator": [{"key": "is_pl__interest_expense"}]},
    {"key": "dso", "label": "Days sales outstanding (DSO)", "category": "Efficiency", "unit": "days",
     "numerator": [{"key": "bs_ca__accounts_receivable"}], "denominator": [{"key": "is_pl__sales_revenues"}]},
    {"key": "dio", "label": "Days inventory outstanding (DIO)", "category": "Efficiency",
     "unit": "days", "numerator": [{"key": "bs_ca__inventories"}],
     "denominator": [{"key": "is_pl__cost_of_revenue"}]},
    {"key": "dpo", "label": "Days payables outstanding (DPO)", "category": "Efficiency",
     "unit": "days", "numerator": [{"key": "bs_cl__accounts_payable"}],
     "denominator": [{"key": "is_pl__cost_of_revenue"}]},
    {"key": "gross_margin", "label": "Gross margin", "category": "Profitability", "unit": "%",
     "numerator": [{"key": "is_pl__gross_profit"}], "denominator": [{"key": "is_pl__sales_revenues"}]},
    {"key": "operating_margin", "label": "Operating margin", "category": "Profitability", "unit": "%",
     "numerator": [{"key": "is_pl__operating_income"}], "denominator": [{"key": "is_pl__sales_revenues"}]},
    {"key": "net_margin", "label": "Net profit margin", "category": "Profitability", "unit": "%",
     "numerator": [{"key": "is_pl__profit_for_the_year"}], "denominator": [{"key": "is_pl__sales_revenues"}]},
    {"key": "return_on_equity", "label": "Return on equity (ROE)", "category": "Profitability",
     "unit": "%", "numerator": [{"key": "is_pl__profit_for_the_year"}],
     "denominator": [{"key": "bs_equity__total_equity"}]},
    {"key": "return_on_assets", "label": "Return on assets (ROA)", "category": "Profitability",
     "unit": "%", "numerator": [{"key": "is_pl__profit_for_the_year"}],
     "denominator": [{"key": "bs_ca__total_assets"}]},
    {"key": "free_cash_flow_margin", "label": "Free cash flow margin", "category": "Profitability",
     "unit": "%", "numerator": [{"key": "cf_oper_indirect__cash_flows_oper_activ_indirect"},
                                {"key": "cf_investing__purchase_ppe"}],
     "denominator": [{"key": "is_pl__sales_revenues"}]},
]}

PROMPT = (
    "These are the consolidated financial statements of a US registrant filed on Form 10-K under US "
    "GAAP: the Consolidated Balance Sheets, the Consolidated Statements of Operations (or Income) "
    "and the Consolidated Statements of Cash Flows. Amounts are in US dollars, usually in millions or "
    "thousands except per-share amounts; the statements of operations and of cash flows print three "
    "years and the balance sheet two. Every line of the three statements is a row the face of those "
    "statements prints: cite the printed statement row, never a note, and leave a line empty when the "
    "face does not print it. The additional items are read from the notes, the supplemental cash flow "
    "disclosures and the report of the independent registered public accounting firm, as each line "
    "says; a text item is copied or written exactly as its line asks, and left empty when the filing "
    "does not disclose it. A 10-K balance sheet usually prints no 'Non-current assets' or "
    "'Non-current liabilities' heading: the rows after 'Total current assets' and before 'Total "
    "assets' are non-current assets, and the rows after 'Total current liabilities' and before the "
    "equity are non-current liabilities. Expenses are read as printed, positive.")

SIGN_CONVENTION = {
    "stored_value": "The reported value after paren-to-negative conversion.",
    "expenses_and_outflows": "Stored POSITIVE, as a 10-K statement of operations prints them. The "
                             "template subtracts them where it computes a figure: gross profit is "
                             "total revenues less cost of revenue. A cash outflow keeps the negative "
                             "sign the statement of cash flows prints.",
    "unsigned_source": "A filing that prints expenses as unsigned positives is read as printed; "
                       "nothing is negated.",
}


def build() -> tuple[dict, dict]:
    policy = json.loads((SRC / "policy.json").read_text(encoding="utf-8"))
    section_of = {}
    order = 0
    for sec in SECTIONS:
        for spec in LINES[sec[0]]:
            section_of[spec["key"]] = sec
    keys = list(section_of)
    assert len(keys) == len(set(keys)), "duplicate keys"
    for spec in (s for sec in SECTIONS for s in LINES[sec[0]]):
        for child in (spec["roll"] or ("", []))[1]:
            assert child in section_of, (spec["key"], child)
        for pattern in spec["regex"] + spec["exclude"]:
            re.compile(pattern)
    for sid, _slabel in NOTE_SECTIONS:
        for spec in NOTE_LINES[sid]:
            assert spec["key"] not in section_of, spec["key"]
            section_of[spec["key"]] = sid
            note = spec["note"] or {}
            for pattern in (spec["regex"] + spec["exclude"] + note.get("note_title_any", [])
                            + note.get("row_caption_any", []) + note.get("row_caption_none", [])):
                re.compile(pattern)

    # ── template ────────────────────────────────────────────────────────────────────────────────
    statements = []
    for st_type in ("balance_sheet", "profit_and_loss", "cash_flow"):
        st = {"type": st_type, "label": STATEMENT_LABELS[st_type],
              "label_i18n": {"en": STATEMENT_LABELS[st_type]}, "sections": []}
        for sid, slabel, stt, _scope, _sign, _bucket in SECTIONS:
            if stt != st_type:
                continue
            children = []
            for spec in LINES[sid]:
                node = {"node_id": spec["key"], "canonical_key": spec["key"], "label": spec["label"],
                        "label_i18n": {"en": spec["label"]},
                        "role": spec["role"] or "line"}
                if spec["roll"]:
                    op, kids = spec["roll"]
                    node["rollup"] = {"op": op, "children": list(kids)}
                children.append(node)
            st["sections"].append({"node_id": sid, "canonical_key": sid, "label": slabel,
                                   "label_i18n": {"en": slabel}, "role": "header",
                                   "children": children})
        if st_type == "balance_sheet":
            st["identities"] = [{"id": "bs_balances", "lhs": "bs_ca__total_assets",
                                 "rhs": {"op": "sum",
                                         "children": ["bs_cl__total_equity_and_liabilities"]}}]
        statements.append(st)
    statements.append({
        "type": "notes", "label": NOTES_LABEL, "label_i18n": {"en": NOTES_LABEL},
        "sections": [{"node_id": sid, "canonical_key": sid, "label": slabel,
                      "label_i18n": {"en": slabel}, "role": "header",
                      "children": [{"node_id": spec["key"], "canonical_key": spec["key"],
                                    "label": spec["label"], "label_i18n": {"en": spec["label"]},
                                    "role": "line"} for spec in NOTE_LINES[sid]]}
                     for sid, slabel in NOTE_SECTIONS]})
    template = {"schema_version": 1, "template_key": TEMPLATE_KEY,
                "name": "US GAAP (Form 10-K) — the statements and additional items",
                "statements": statements, "kpis": KPIS}

    # ── line-item set ───────────────────────────────────────────────────────────────────────────
    section_defaults = {}
    for sid, _sl, stt, scope, sign, _bucket in SECTIONS:
        section_defaults[sid] = {"statement": stt, "section_scope": list(scope),
                                 "temporality": "instant" if stt == "balance_sheet" else "duration",
                                 "sign_convention": sign}
    items, residuals, cross = [], [], []
    for sid, _sl, stt, scope, _sign, bucket in SECTIONS:
        for spec in LINES[sid]:
            order += 1
            k = spec["key"]
            calculated = bool(spec["roll"])
            item = {
                "key": k, "label": spec["label"],
                "type": "calculated" if calculated else "extracted",
                "namespace": "template", "in_output": True, "inherits": sid,
                "statements": [stt],
                "section_scope": list(SCOPE_OVERRIDES.get(k) or spec["scope"] or scope),
                "order": order,
                "definition": icon._prose(spec["definition"]),
                "temporality": "instant" if stt == "balance_sheet" else "duration",
                "unit_of_account": ("subtotal" if (calculated or spec["role"])
                                    else "balance" if stt == "balance_sheet" else "flow"),
                "sign_convention": spec["sign"],
                "analyst_bucket": spec["bucket"] or bucket,
                "match_priority": 80,
                "route": "face",
            }
            if calculated:
                op, kids = spec["roll"]
                item["extraction_mode"] = "extract_or_derive"
                item["terms"] = [{"ref": c, "sign": -1 if (op == "diff" and n) else 1}
                                 for n, c in enumerate(kids)]
                item["match_priority"] = 99 if spec["role"] == "total" else 95
                cross.append(k)
            elif spec["role"]:
                item["match_priority"] = 95
            if spec.get("residual"):
                residuals.append(k)
                item["match_priority"] = 0
                item["exclude_hints"] = list(spec["exclude"])
                if spec["aliases"]:
                    item["aliases"] = list(spec["aliases"])
                    item["aliases_i18n"] = {"en": list(spec["aliases"])}
            else:
                aliases, held = icon._variants(spec["aliases"])
                item["aliases"] = aliases
                item["aliases_i18n"] = {"en": list(aliases)}
                item["regex_hints"] = (_hint(aliases, stt) + [icon._printed_hint(h) for h in held]
                                       + spec["regex"])
                if spec["exclude"]:
                    item["exclude_hints"] = [x if x.startswith("(?i)") else f"(?i){x}"
                                             for x in spec["exclude"]]
            items.append(item)

    face_lines = len(items)
    for sid, _slabel in NOTE_SECTIONS:
        section_defaults[sid] = {"statement": "notes", "section_scope": [],
                                 "temporality": "duration", "sign_convention": "either"}
        for spec in NOTE_LINES[sid]:
            order += 1
            k = spec["key"]
            text = spec["structure"] != "value"
            item = {
                "key": k, "label": spec["label"], "type": "extracted",
                "namespace": "template", "in_output": True, "inherits": sid,
                "statements": list(spec["statements"]), "section_scope": list(spec["scope"]),
                "order": order, "definition": icon._prose(spec["definition"]),
                "temporality": "instant" if spec["instant"] else "duration",
                "unit_of_account": "balance" if spec["instant"] else "flow",
                "sign_convention": spec["sign"], "analyst_bucket": "others",
                "match_priority": 80, "route": spec["route"],
            }
            if text:
                item["output_structure"] = spec["structure"]
            if spec["note"]:
                item["note_source"] = copy.deepcopy(spec["note"])
            if spec["aliases"] and text:
                # A TEXT LINE'S NAMES choose the pages it is sent (`text_lines.select_pages`); it
                # binds no row, so it carries no pattern.
                item["aliases"] = list(spec["aliases"])
                item["aliases_i18n"] = {"en": list(spec["aliases"])}
            elif spec["aliases"]:
                aliases, held = icon._variants(spec["aliases"])
                item["aliases"] = aliases
                item["aliases_i18n"] = {"en": list(aliases)}
                item["regex_hints"] = (_hint(aliases, spec["statements"][0])
                                       + [icon._printed_hint(h) for h in held] + spec["regex"])
            if spec["exclude"]:
                item["exclude_hints"] = [f"(?i){x}" for x in spec["exclude"]]
            items.append(item)

    rules = copy.deepcopy(policy["global_rules"])
    rules["sign_convention"] = {**SIGN_CONVENTION,
                                "either": policy["global_rules"]["sign_convention"]["either"],
                                "validation": policy["global_rules"]["sign_convention"]["validation"]}

    n_notes = len(items) - face_lines
    line_items = {
        "schema_version": 1, "line_items_key": LINE_ITEMS_KEY,
        "target_template_key": TEMPLATE_KEY, "target_template_version": None,
        "locale": "en", "supported_locales": ["en"],
        "metadata": {
            "name": "US GAAP (Form 10-K) line items", "version": "1",
            "changes": [
                ("Built by scripts/build_usgaap_pair.py: the rows the face of a Form 10-K's "
                 "consolidated balance sheets, statements of operations and indirect-method "
                 "statements of cash flows print."),
                (f"{face_lines} statement lines, every one read from the face (strict_face_route); "
                 "no internal parts. One leftover line per asset, liability and cash-flow section; "
                 "none in the statement of operations or in stockholders' equity."),
                (f"{n_notes} additional items, read from the notes and the auditor's report: the "
                 "supplemental cash flow disclosures, debt maturities, principal and fair value, "
                 "the current and deferred tax and the effective tax rate, the inventory split, "
                 "property at cost and accumulated depreciation, the audit opinion, contingent "
                 "liabilities and related-party transactions."),
            ],
            "breaking_changes": [],
            "vocabulary_note": "English captions in the wording 10-K filers print, with the "
                               "variants of the most-printed face rows.",
        },
        "section_defaults": section_defaults,
        "vocabulary": copy.deepcopy(policy["vocabulary"]),
        "normalisation": copy.deepcopy(policy["normalisation"]),
        "binding": copy.deepcopy(policy["binding"]),
        "prompt": PROMPT,
        "global_rules": rules,
        "scope_selection": copy.deepcopy(policy["scope_selection"]),
        "residual_framework": copy.deepcopy(policy["residual_framework"]),
        "items": items,
        "others_master": {"note": "One leftover line per balance-sheet and cash-flow section: the "
                                  "rows of the section no line claims are swept there, itemised. "
                                  "The statement of operations has none, so a row no line claims "
                                  "is left for review.",
                          "keys": residuals},
        "cross_check_master": {"note": "The calculated lines a filing prints: the printed figure "
                                       "is read and compared with the one the lines add up to.",
                               "keys": cross, "tolerance": 0.5},
        "prose_grammar": copy.deepcopy(policy["prose_grammar"]),
        "strict_face_route": True,
    }
    return template, line_items


def main() -> None:
    template, line_items = build()
    (OUT / f"{TEMPLATE_KEY}_template.json").write_text(
        json.dumps(template, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / f"{LINE_ITEMS_KEY}_line_items.json").write_text(
        json.dumps(line_items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(s["children"]) for st in template["statements"] for s in st["sections"])
    print(f"wrote {TEMPLATE_KEY}_template.json ({n} lines) and {LINE_ITEMS_KEY}_line_items.json "
          f"({len(line_items['items'])} items)")


if __name__ == "__main__":
    main()
