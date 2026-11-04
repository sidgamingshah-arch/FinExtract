"""Build the Ind AS template and its line-item set: the face of a Schedule III Division II filing.

    python scripts/build_indas_pair.py      # writes app/sample/templates/output_csv_indas_*

The pair this replaces was the HK set with Ind AS captions added beside the HKFRS ones: 198
printed lines and 65 internal parts, 46 lines read from note tables and 6 from sentences, and
lines no Indian balance sheet prints (Land use rights, Due from MI, Due from S'holder, Pledged
Deposits, Start-Up Capitalized Expenses, a gross block built up from asset classes). Its own
metadata said the inherited note patterns had never been measured on an Indian filing.

WHAT IT DECIDES, and why:

* THE LINES ARE THE ROWS SCHEDULE III PRINTS. Division II fixes the face of an Ind AS balance
  sheet and statement of profit and loss, line by line, and an Indian cash flow statement follows
  Ind AS 7's indirect method. Each line here is one of those rows; a subtotal is one the format
  prints (Total non-current assets, Total income, Profit before tax, Net cash from operating
  activities). No line splits a printed row into parts the face does not print.
* FACE ONLY. Every line declares ``route: face``, none carries a ``note_source``, and the set
  declares ``strict_face_route``, so no stage puts a note's figure on a line (the ICON precedent,
  ``scripts/build_icon_pair.py``). A row the face does not print is left empty.
* ONE LEFTOVER LINE PER SECTION. The rows of a section that no line claims are swept into that
  section's "Other ..." line, itemised, so every section still adds up to the total it prints. The
  P&L has none: a Schedule III statement of profit and loss prints a fixed list, and a row no line
  claims is left for review rather than hidden in a bucket. Each leftover line refuses the totals
  and subtotals printed in its section, which would otherwise be counted twice.
* EXPENSES ARE STORED AS PRINTED, positive, as Schedule III prints them under "Expenses", and the
  template subtracts them (profit before exceptional items and tax = total income less total
  expenses), the way ICON does. Cash flows keep the sign the statement prints.
* KEYS. The ``bs_*``/``is_*``/``cf_*`` section prefixes stay (the Workspace and the section gate
  read them). A key whose concept is unchanged is kept; one whose meaning differs from the HK
  line of the same name is new (``is_pl__finance_costs`` is a positive cost, not the HK
  ``is_pl__interest_expense`` stored negative). The keys application code names are kept:
  ``bs_ca__total_assets``, ``bs_cl__total_equity_and_liabilities``, ``bs_equity__total_equity``,
  ``is_pl__sales_revenues``, ``is_pl__profit_for_the_year``.
* CAPTIONS follow Schedule III wording, with the spelling variants and exclusions ICON's reviewed
  captions use for the same rows, and the same rule-tier regex (``build_icon_pair._regex_hint``)
  so a caption still binds behind an enumerator or a glued heading ("(d) Financial assets (i)
  Investments").
* POLICY BLOCKS (units in lakhs and crores, Standalone/Consolidated columns, note references, the
  residual framework) are the Indian reading rules the set already had, kept in
  ``sample_data/indas/policy.json``; only the sign convention is restated, for the reason above.
* NOT CARRIED: earnings per share (a per-share amount in rupees, not a figure in the statements'
  scale) and the statement of changes in equity.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import build_icon_pair as icon

SRC = BACKEND / "scripts" / "sample_data" / "indas"
OUT = BACKEND / "app" / "sample" / "templates"
TEMPLATE_KEY = "output_csv_indas_v1"
LINE_ITEMS_KEY = "output_csv_indas"

# The P&L caption gates, as ICON sets them: a Schedule III statement of profit and loss heads its
# rows "Income", "Expenses" and "Tax expense", and a line may only claim a row under its own head.
PL_INCOME, PL_EXPENSES, PL_TAX, PL_EXCEPTIONAL = (["pl_income"], ["pl_expenses"],
                                                  ["pl_tax_expense"], ["pl_exceptional_items"])
ALL_PL = ["pl_income", "pl_expenses", "pl_tax_expense", "pl_exceptional_items", "is_pl"]
TOP = ["bs_top_level"]

# (id, label, statement, section_scope, sign convention, analyst bucket)
SECTIONS = [
    ("bs_nca", "Non-current assets", "balance_sheet", ["bs_nca"], "positive_expected",
     "non_current_assets"),
    ("bs_ca", "Current assets", "balance_sheet", ["bs_ca"], "positive_expected", "current_assets"),
    ("bs_equity", "Equity", "balance_sheet", ["bs_equity"], "either", "equity"),
    ("bs_ncl", "Non-current liabilities", "balance_sheet", ["bs_ncl"], "positive_expected",
     "non_current_liabilities"),
    ("bs_cl", "Current liabilities", "balance_sheet", ["bs_cl"], "positive_expected",
     "current_liabilities"),
    ("is_pl", "Income and expenses", "profit_and_loss", ALL_PL, "either", "others"),
    ("is_oci", "Other comprehensive income", "profit_and_loss", ["is_oci"], "either", "others"),
    ("cf_oper_indirect", "Cash flows from operating activities", "cash_flow", ["cf_oper_indirect"],
     "either", "cash_flow_operating"),
    ("cf_investing", "Cash flows from investing activities", "cash_flow", ["cf_investing"],
     "either", "cash_flow_investing"),
    ("cf_financing", "Cash flows from financing activities", "cash_flow", ["cf_financing"],
     "either", "cash_flow_financing"),
]
STATEMENT_LABELS = {"balance_sheet": "Balance Sheet",
                    "profit_and_loss": "Statement of Profit and Loss",
                    "cash_flow": "Statement of Cash Flows"}

# Captions a leftover line never takes: a total or subtotal printed in its section, and a column
# header surfacing as a row ("Particulars" with the year as its figure). "Total outstanding dues
# of ..." is the MSME creditor caption, not a total.
_TOTALS = [r"^\W*(?:sub[\s-]*)?total\b(?!\s+outstanding)", r"^\W*particulars\b"]


def L(key, label, aliases=(), definition="", *, exclude=(), regex=(), sign="positive_expected",
      scope=None, roll=None, role=None, mode=None, bucket=None):
    """One line of the spec. ``roll`` is ``(op, [children])`` for a calculated line."""
    return {"key": key, "label": label, "aliases": list(aliases), "definition": definition,
            "exclude": list(exclude), "regex": list(regex), "sign": sign, "scope": scope,
            "roll": roll, "role": role, "mode": mode, "bucket": bucket}


def R(key, label, definition, exclude=(), aliases=()):
    """A section's leftover line: filled by the sweep, never by a caption match."""
    d = L(key, label, aliases, definition, exclude=list(_TOTALS) + list(exclude))
    d["residual"] = True
    return d


NOT_RIGHT_OF_USE = [r"right[-\s]*of[-\s]*use"]

# Rule-tier patterns on the matcher's normalised text (`mapping.normalize_label`: lower case,
# punctuation gone, "&" a space). `P` is a leading enumerator and a "Less:"/"Add:"; `T` is a
# trailing cross-reference — "(A)", "(I-II)", "[A+B+C]" normalise to "a", "i ii", "a b c".
P = rf"^{icon._ENUM}(?:less\s+|add\s+)?"
T = r"(?:\s+(?:[ivxl]{1,5}|\d{1,2}|[a-h]))*$"
# A cash flow statement's activity headings, which row reconstruction glues onto the first row
# printed under them: "Cash flow from operating activities Profit before tax".
_CF_GLUED = ("cash flow from operating activities", "cash flows from operating activities",
             "cash flow from investing activities", "cash flows from investing activities",
             "cash flow used in investing activities", "cash flows used in investing activities",
             "cash flow from financing activities", "cash flows from financing activities",
             "cash flow used in financing activities", "cash flows used in financing activities",
             "adjustments for", "adjustment for", "adjustments for changes in working capital",
             "changes in working capital", "working capital adjustments", "working capital changes")

LINES = {
    # ── BALANCE SHEET ──────────────────────────────────────────────────────────────────────────
    "bs_nca": [
        L("bs_nca__property_plant_and_equipment", "Property, plant and equipment",
          ["Property, plant and equipment", "Property, plant & equipment", "Property plant and equipment",
           "Tangible assets", "Fixed assets", "Tangible fixed assets"],
          "Property, plant and equipment as the balance sheet prints it among the non-current "
          "assets: the net carrying amount, read as one row. Not capital work-in-progress, "
          "right-of-use assets or investment property, which are lines of their own.",
          exclude=[r"work[-\s]*in[-\s]*progress", r"under\s+development", *NOT_RIGHT_OF_USE,
                   r"investment\s+propert", r"held\s+for\s+sale", r"advance"]),
        L("bs_nca__construction_in_progress", "Capital work-in-progress",
          ["Capital work-in-progress", "Capital work in progress", "Capital works in progress",
           "Capital work-in-progress (CWIP)"],
          "Capital work-in-progress: property, plant and equipment under construction, as the "
          "balance sheet prints it. Not intangible assets under development, and not capital "
          "advances.",
          exclude=[r"intangible", r"capital\s+advances?"]),
        L("bs_nca__right_of_use_assets", "Right-of-use assets",
          ["Right-of-use assets", "Right of use assets", "Right-of-use asset", "Right of use asset",
           "Leasehold right-of-use assets"],
          "Right-of-use assets recognised under Ind AS 116, as the balance sheet prints them "
          "among the non-current assets. Not the lease liabilities that go with them."),
        L("bs_nca__investment_property_net", "Investment property",
          ["Investment property", "Investment properties", "Investment property (net)"],
          "Investment property — land and buildings held to earn rentals or for capital "
          "appreciation — as the balance sheet prints it.",
          exclude=[r"under\s+(?:construction|development)"]),
        L("bs_nca__goodwill", "Goodwill",
          ["Goodwill", "Goodwill on consolidation"],
          "Goodwill — on consolidation or acquired in a business combination — as the balance sheet "
          "prints it among the non-current assets. Not other intangible assets.",
          exclude=[r"capital\s+reserve", r"equity\s+method"]),
        L("bs_nca__other_intangible_assets", "Other intangible assets",
          ["Other intangible assets", "Intangible assets", "Other intangible asset",
           "Intangible assets other than goodwill"],
          "Intangible assets other than goodwill (software, brands, licences, customer "
          "relationships), as the balance sheet prints them. Not intangible assets under "
          "development.",
          exclude=[r"under\s+development", r"^\W*goodwill"]),
        L("bs_nca__intangible_assets_under_development", "Intangible assets under development",
          ["Intangible assets under development", "Intangible asset under development",
           "Intangible assets under developments"],
          "Intangible assets under development, as the balance sheet prints them."),
        L("bs_nca__biological_assets", "Biological assets other than bearer plants",
          ["Biological assets other than bearer plants", "Biological assets",
           "Biological asset other than bearer plants"],
          "Biological assets other than bearer plants, as the balance sheet prints them."),
        L("bs_nca__investments_equity_method", "Investments accounted for using the equity method",
          ["Investments accounted for using the equity method",
           "Investment accounted for using the equity method",
           "Investments accounted for using equity method",
           "Equity accounted investments", "Equity accounted investees",
           "Investments in associates and joint ventures", "Investment in associates and joint ventures",
           "Investments in associates", "Investment in associates",
           "Investments in joint ventures", "Investment in joint ventures"],
          "Investments in associates and joint ventures carried under the equity method, as a "
          "consolidated balance sheet prints them. Other non-current investments are "
          "Investments.",
          exclude=[r"share\s+of\s+(?:net\s+)?(?:profit|loss|result)"]),
        L("bs_nca__investments", "Investments",
          ["Investments", "Non-current investments", "Other investments",
           "Investments in subsidiaries", "Investment in subsidiaries",
           "Investments in subsidiaries, associates and joint ventures",
           "Investments in subsidiaries and associates"],
          "Non-current investments, the first of the non-current financial assets — and, on a "
          "standalone balance sheet, the investments in subsidiaries, associates and joint "
          "ventures it prints as their own row. Not investments a consolidated balance sheet "
          "carries under the equity method.",
          exclude=[r"equity\s+method", r"property", r"share\s+of\s+(?:net\s+)?(?:profit|loss)"]),
        L("bs_nca__trade_receivables", "Trade receivables",
          ["Trade receivables", "Non-current trade receivables", "Trade receivable"],
          "Trade receivables due after more than twelve months, among the non-current financial "
          "assets."),
        L("bs_nca__loans", "Loans",
          ["Loans", "Loan", "Non-current loans", "Loans to employees", "Loans to related parties"],
          "Loans given (to employees, related parties or others) and recoverable after more than "
          "twelve months, among the non-current financial assets. Not borrowings, which are "
          "liabilities.",
          exclude=[r"from|borrowing"]),
        L("bs_nca__other_financial_assets", "Other financial assets",
          ["Other financial assets", "Other financial asset", "Other non-current financial assets",
           "Others"],
          "The non-current financial assets other than investments, trade receivables and loans "
          "(security deposits, bank deposits due after twelve months, derivative assets), as the "
          "balance sheet prints them under 'Others' or 'Other financial assets'."),
        L("bs_nca__deferred_tax_assets", "Deferred tax assets (net)",
          ["Deferred tax assets (net)", "Deferred tax assets", "Deferred tax asset (net)",
           "Deferred tax asset", "Deferred tax assets, net"],
          "Deferred tax assets, net, as the balance sheet prints them.",
          exclude=[r"liabilit"]),
        L("bs_nca__income_tax_assets", "Income tax assets (net)",
          ["Income tax assets (net)", "Income tax assets", "Non-current tax assets (net)",
           "Non-current tax assets", "Income tax asset (net)", "Advance income tax (net)",
           "Advance tax (net of provision)", "Tax assets (net)"],
          "Income tax paid in advance or recoverable after more than twelve months, net, as the "
          "balance sheet prints it among the non-current assets. Not deferred tax assets.",
          exclude=[r"deferred", r"liabilit"]),
        R("bs_nca__other_non_current_assets", "Other non-current assets",
          "Other non-current assets: the row the balance sheet prints as 'Other non-current "
          "assets' (capital advances, prepaid expenses, balances with government authorities), "
          "and every other non-current-asset row no line of this template names, swept here and "
          "itemised.",
          aliases=["Other non-current assets"]),
        L("bs_nca__total_non_current_assets", "Total non-current assets",
          ["Total non-current assets", "Total non current assets", "Non-current assets total"],
          "Total non-current assets: the sum of the non-current asset lines, as the template "
          "computes it; where the balance sheet prints the total — on a row of its own, or on "
          "the 'Non-current assets' heading row — that figure is read and compared.",
          exclude=[r"^\W*total\s+current", r"less\s+current\s+liabilities"], role="subtotal",
          regex=[r"^non\s+current\s+assets$"],
          roll=("sum", ["bs_nca__property_plant_and_equipment", "bs_nca__construction_in_progress",
                        "bs_nca__right_of_use_assets", "bs_nca__investment_property_net",
                        "bs_nca__goodwill", "bs_nca__other_intangible_assets",
                        "bs_nca__intangible_assets_under_development", "bs_nca__biological_assets",
                        "bs_nca__investments_equity_method", "bs_nca__investments",
                        "bs_nca__trade_receivables", "bs_nca__loans",
                        "bs_nca__other_financial_assets", "bs_nca__deferred_tax_assets",
                        "bs_nca__income_tax_assets", "bs_nca__other_non_current_assets"])),
    ],
    "bs_ca": [
        L("bs_ca__inventories", "Inventories",
          ["Inventories", "Inventory", "Stock-in-trade"],
          "Inventories as the balance sheet prints them, read as one row.",
          exclude=[r"changes?\s+in|increase|decrease|write[-\s]?down|written\s+down"]),
        L("bs_ca__investments", "Investments",
          ["Investments", "Current investments", "Investment"],
          "Current investments (mutual fund units, bonds and the like held for sale within the "
          "year), the first of the current financial assets."),
        L("bs_ca__trade_receivables", "Trade receivables",
          ["Trade receivables", "Trade receivable", "Sundry debtors", "Debtors"],
          "Trade receivables, net of the allowance for expected credit loss, as the balance sheet "
          "prints them among the current financial assets.",
          exclude=[r"other\s+receivables"]),
        L("bs_ca__cash_and_cash_equivalents", "Cash and cash equivalents",
          ["Cash and cash equivalents", "Cash & cash equivalents", "Cash and cash equivalent",
           "Cash and bank balances"],
          "Cash and cash equivalents as the balance sheet prints them. Not bank balances other "
          "than cash and cash equivalents, which are their own line.",
          exclude=[r"other\s+than\s+cash"]),
        L("bs_ca__other_bank_balances", "Bank balances other than cash and cash equivalents",
          ["Bank balances other than cash and cash equivalents", "Other bank balances",
           "Bank balances other than (ii) above", "Bank balances other than (iii) above",
           "Bank balances other than (iv) above", "Bank balances other than above",
           "Other balances with banks"],
          "Bank balances other than cash and cash equivalents (deposits of three to twelve "
          "months, earmarked and unclaimed-dividend balances), as the balance sheet prints them."),
        L("bs_ca__loans", "Loans",
          ["Loans", "Loan", "Current loans", "Loans to employees", "Loans to related parties"],
          "Loans given (to employees, related parties or others) and recoverable within twelve "
          "months, among the current financial assets. Not borrowings, which are liabilities.",
          exclude=[r"from|borrowing"]),
        L("bs_ca__other_financial_assets", "Other financial assets",
          ["Other financial assets", "Other financial asset", "Other current financial assets",
           "Others"],
          "The current financial assets other than investments, trade receivables, cash, bank "
          "balances and loans (interest accrued, security deposits, derivative assets, "
          "receivables other than trade), as the balance sheet prints them."),
        L("bs_ca__current_tax_receivables", "Current tax assets (net)",
          ["Current tax assets (net)", "Current tax assets", "Current tax asset (net)",
           "Current tax asset", "Income tax assets (net)", "Advance income tax (net)"],
          "Income tax paid in advance or recoverable within twelve months, net, as the balance "
          "sheet prints it among the current assets.",
          exclude=[r"deferred", r"liabilit", r"non[-\s]?current"]),
        R("bs_ca__other_current_assets", "Other current assets",
          "Other current assets: the row the balance sheet prints as 'Other current assets' "
          "(advances to suppliers, prepaid expenses, balances with government authorities, "
          "contract assets), and every other current-asset row no line of this template names, "
          "swept here and itemised.",
          aliases=["Other current assets"]),
        L("bs_ca__assets_related_to_disc_ops", "Assets classified as held for sale",
          ["Assets classified as held for sale", "Assets held for sale",
           "Non-current assets classified as held for sale", "Non-current assets held for sale",
           "Assets of disposal group classified as held for sale"],
          "Assets, or disposal groups, classified as held for sale, as the balance sheet prints "
          "them.",
          exclude=[r"liabilit"]),
        L("bs_ca__total_current_assets", "Total current assets",
          ["Total current assets", "Total current asset", "Current assets total"],
          "Total current assets: the sum of the current asset lines, as the template computes it; "
          "where the balance sheet prints the total — on a row of its own, or on the 'Current "
          "assets' heading row — that figure is read and compared.",
          exclude=[r"net\s+current", r"non[-\s]?current", r"less\s+current\s+liabilities"],
          role="subtotal", regex=[r"^current\s+assets$"],
          roll=("sum", ["bs_ca__inventories", "bs_ca__investments", "bs_ca__trade_receivables",
                        "bs_ca__cash_and_cash_equivalents", "bs_ca__other_bank_balances",
                        "bs_ca__loans", "bs_ca__other_financial_assets",
                        "bs_ca__current_tax_receivables", "bs_ca__other_current_assets",
                        "bs_ca__assets_related_to_disc_ops"])),
        L("bs_ca__total_assets", "Total assets",
          ["Total assets", "Total asset"],
          "Total assets: total non-current assets plus total current assets, as the template "
          "computes it; where the balance sheet prints it, that figure is read and compared.",
          exclude=[r"total\s+assets\s+less", r"net\s+current", r"total\s+(?:non[-\s]?)?current"],
          role="total", scope=TOP,
          roll=("sum", ["bs_nca__total_non_current_assets", "bs_ca__total_current_assets"])),
    ],
    "bs_equity": [
        L("bs_equity__common_share_capital", "Equity share capital",
          ["Equity share capital", "Share capital", "Equity shares capital", "Equity Share Capital",
           "Paid-up share capital"],
          "Equity share capital as the balance sheet prints it, the first row of equity. Not "
          "preference share capital, share application money or other equity.",
          exclude=[r"preference", r"application\s+money", r"warrants?", r"suspense",
                   r"authori[sz]ed"]),
        R("bs_equity__other_equity", "Other equity",
          "Other equity: the row the balance sheet prints as 'Other equity' (reserves and "
          "surplus, retained earnings, items of other comprehensive income), and every other "
          "equity row no line of this template names (instruments entirely equity in nature, "
          "share application money pending allotment), swept here and itemised.",
          exclude=[r"attributable", r"non[-\s]?controlling|minority"],
          aliases=["Other equity", "Reserves and surplus"]),
        L("bs_equity__equity_attributable_to_owners", "Equity attributable to owners",
          ["Equity attributable to owners of the Company", "Equity attributable to owners of the parent",
           "Equity attributable to equity holders of the Company",
           "Equity attributable to equity holders of the parent",
           "Equity attributable to shareholders of the Company",
           "Equity attributable to owners"],
          "Equity attributable to the owners of the parent: equity share capital plus other "
          "equity, as the template computes it; where a consolidated balance sheet prints it, "
          "that figure is read and compared.",
          sign="either", role="subtotal",
          regex=[(r"^(?:total\s+)?equity\s+attributable\s+to\s+(?:the\s+)?(?:owners|equity\s+holders"
                 r"|shareholders)\b.*$")],
          roll=("sum", ["bs_equity__common_share_capital", "bs_equity__other_equity"])),
        L("bs_equity__non_controlling_interests", "Non-controlling interests",
          ["Non-controlling interests", "Non-controlling interest", "Non controlling interests",
           "Minority interests", "Minority interest"],
          "The equity of non-controlling shareholders in subsidiaries, as a consolidated balance "
          "sheet prints it.",
          sign="either", exclude=[r"share\s+of", r"attributable", r"profit", r"loss",
                                  r"comprehensive"]),
        L("bs_equity__total_equity", "Total equity",
          ["Total equity", "Total Equity", "Total equity (A)", "Total shareholders' funds",
           "Shareholders' funds"],
          "Total equity: equity attributable to owners plus non-controlling interests, as the "
          "template computes it; where the balance sheet prints it — on a row of its own, or on "
          "the 'Equity' heading row — that figure is read and compared.",
          sign="either", exclude=[r"liabilit", r"attributable"], role="subtotal",
          regex=[r"^equity$"],
          roll=("sum", ["bs_equity__equity_attributable_to_owners",
                        "bs_equity__non_controlling_interests"])),
    ],
    "bs_ncl": [
        L("bs_ncl__borrowings", "Borrowings",
          ["Borrowings", "Non-current borrowings", "Long-term borrowings", "Borrowing"],
          "Non-current borrowings (term loans, debentures, bonds), the first of the non-current "
          "financial liabilities.",
          exclude=[r"current\s+maturit", r"interest\s+accrued"]),
        L("bs_ncl__lease_liabilities_ltp", "Lease liabilities",
          ["Lease liabilities", "Lease liability", "Non-current lease liabilities",
           "Long-term lease liabilities"],
          "Lease liabilities due after more than twelve months, as the balance sheet prints them.",
          exclude=[r"current\s+maturit", r"receivable"]),
        L("bs_ncl__trade_payables_ltp", "Trade payables",
          ["Trade payables", "Trade payable",
           "Total outstanding dues of micro enterprises and small enterprises",
           "Total outstanding dues of creditors other than micro enterprises and small enterprises",
           "Total outstanding dues of micro and small enterprises",
           "Total outstanding dues of creditors other than micro and small enterprises"],
          "Trade payables due after more than twelve months: both rows Schedule III prints under "
          "the heading, dues to micro and small enterprises and to other creditors, added.",
          exclude=[r"capital\s+creditors", r"employee"]),
        L("bs_ncl__other_financial_liabilities", "Other financial liabilities",
          ["Other financial liabilities", "Other financial liability",
           "Other non-current financial liabilities"],
          "The non-current financial liabilities other than borrowings, lease liabilities and "
          "trade payables (deposits received, derivative liabilities), as the balance sheet "
          "prints them."),
        L("bs_ncl__provisions", "Provisions",
          ["Provisions", "Provision", "Long-term provisions", "Non-current provisions"],
          "Non-current provisions (employee benefits, warranties, restoration), as the balance "
          "sheet prints them.",
          exclude=[r"tax"]),
        L("bs_ncl__deferred_tax_liability", "Deferred tax liabilities (net)",
          ["Deferred tax liabilities (net)", "Deferred tax liabilities", "Deferred tax liability (net)",
           "Deferred tax liability", "Deferred tax liabilities, net"],
          "Deferred tax liabilities, net, as the balance sheet prints them.",
          exclude=[r"\bassets?\b"]),
        R("bs_ncl__other_non_current_liabilities", "Other non-current liabilities",
          "Other non-current liabilities: the row the balance sheet prints as 'Other non-current "
          "liabilities' (deferred income, advances received), and every other "
          "non-current-liability row no line of this template names, swept here and itemised.",
          aliases=["Other non-current liabilities"]),
        L("bs_ncl__total_non_current_liabilities", "Total non-current liabilities",
          ["Total non-current liabilities", "Total non current liabilities"],
          "Total non-current liabilities: the sum of the non-current liability lines, as the "
          "template computes it; where the balance sheet prints it — on a row of its own, or on "
          "the 'Non-current liabilities' heading row — that figure is read and compared.",
          exclude=[r"^\W*total\s+current"], role="subtotal",
          # "Total non-current liabiliti es": the word broken across two text runs.
          regex=[r"^non\s+current\s+liabilities$",
                 r"^total\s+non\s+current\s+liabilit\w*(?:\s+\w{1,3})?$"],
          roll=("sum", ["bs_ncl__borrowings", "bs_ncl__lease_liabilities_ltp",
                        "bs_ncl__trade_payables_ltp", "bs_ncl__other_financial_liabilities",
                        "bs_ncl__provisions", "bs_ncl__deferred_tax_liability",
                        "bs_ncl__other_non_current_liabilities"])),
    ],
    "bs_cl": [
        L("bs_cl__borrowings", "Borrowings",
          ["Borrowings", "Current borrowings", "Short-term borrowings", "Borrowing"],
          "Current borrowings — working-capital loans, commercial paper and the current "
          "maturities of long-term debt Schedule III places here — the first of the current "
          "financial liabilities.",
          exclude=[r"interest\s+accrued"]),
        L("bs_cl__lease_liabilities_cp", "Lease liabilities",
          ["Lease liabilities", "Lease liability", "Current lease liabilities",
           "Current maturities of lease liabilities"],
          "Lease liabilities due within twelve months, as the balance sheet prints them.",
          exclude=[r"receivable"]),
        L("bs_cl__trade_payables_cp", "Trade payables",
          ["Trade payables", "Trade payable", "Sundry creditors",
           "Total outstanding dues of micro enterprises and small enterprises",
           "Total outstanding dues of creditors other than micro enterprises and small enterprises",
           "Total outstanding dues of micro and small enterprises",
           "Total outstanding dues of creditors other than micro and small enterprises"],
          "Trade payables: both rows Schedule III prints under the heading, total outstanding "
          "dues of micro and small enterprises and of other creditors, added. Not capital "
          "creditors or employee dues, which are other financial liabilities.",
          exclude=[r"capital\s+creditors", r"employee", r"acceptances?"]),
        L("bs_cl__other_financial_liabilities", "Other financial liabilities",
          ["Other financial liabilities", "Other financial liability",
           "Other current financial liabilities"],
          "The current financial liabilities other than borrowings, lease liabilities and trade "
          "payables (interest accrued, unclaimed dividends, capital creditors, deposits, "
          "derivative liabilities), as the balance sheet prints them."),
        R("bs_cl__other_current_liabilities", "Other current liabilities",
          "Other current liabilities: the row the balance sheet prints as 'Other current "
          "liabilities' (statutory dues, advances from customers, contract liabilities), and "
          "every other current-liability row no line of this template names, swept here and "
          "itemised.",
          aliases=["Other current liabilities"]),
        L("bs_cl__provisions", "Provisions",
          ["Provisions", "Provision", "Short-term provisions", "Current provisions"],
          "Current provisions (employee benefits, warranties), as the balance sheet prints them. "
          "Not the provision for current tax, which is current tax liabilities.",
          exclude=[r"tax"]),
        L("bs_cl__current_tax_payable", "Current tax liabilities (net)",
          ["Current tax liabilities (net)", "Current tax liabilities", "Current tax liability (net)",
           "Current tax liability", "Income tax liabilities (net)", "Income tax liabilities",
           "Provision for taxation (net of advance tax)", "Provision for tax (net)"],
          "Income tax payable for the year less tax paid in advance, net, as the balance sheet "
          "prints it among the current liabilities.",
          exclude=[r"deferred", r"\bassets?\b"]),
        L("bs_cl__liabilities_held_for_sale", "Liabilities directly associated with assets held for sale",
          ["Liabilities directly associated with assets classified as held for sale",
           "Liabilities associated with assets held for sale",
           "Liabilities directly associated with assets held for sale",
           "Liabilities of disposal group classified as held for sale"],
          "The liabilities of a disposal group classified as held for sale, as the balance sheet "
          "prints them."),
        L("bs_cl__total_current_liabilities", "Total current liabilities",
          ["Total current liabilities", "Total current liability"],
          "Total current liabilities: the sum of the current liability lines, as the template "
          "computes it; where the balance sheet prints it — on a row of its own, or on the "
          "'Current liabilities' heading row — that figure is read and compared.",
          exclude=[r"^\W*total\s+non[\s-]*current", r"net\s+current"], role="subtotal",
          regex=[r"^current\s+liabilities$", r"^total\s+current\s+liabilit\w*(?:\s+\w{1,3})?$"],
          roll=("sum", ["bs_cl__borrowings", "bs_cl__lease_liabilities_cp",
                        "bs_cl__trade_payables_cp", "bs_cl__other_financial_liabilities",
                        "bs_cl__other_current_liabilities", "bs_cl__provisions",
                        "bs_cl__current_tax_payable", "bs_cl__liabilities_held_for_sale"])),
        L("bs_cl__total_liabilities", "Total liabilities",
          ["Total liabilities", "Total liability"],
          "Total liabilities: total non-current plus total current liabilities, as the template "
          "computes it; where the balance sheet prints it, that figure is read and compared.",
          exclude=[r"equity", r"net\s+worth", r"current"], role="subtotal", scope=TOP,
          roll=("sum", ["bs_ncl__total_non_current_liabilities",
                        "bs_cl__total_current_liabilities"])),
        L("bs_cl__total_equity_and_liabilities", "Total equity and liabilities",
          ["Total equity and liabilities", "Total equity & liabilities",
           "Total liabilities and equity"],
          "Total equity and liabilities: total equity plus total liabilities, as the template "
          "computes it; where the balance sheet prints it, that figure is read and compared.",
          exclude=[r"^\W*total\s+liabilities\W*$", r"^\W*total\s+assets", r"less\s+current"],
          role="total", scope=TOP,
          roll=("sum", ["bs_equity__total_equity", "bs_cl__total_liabilities"])),
    ],
    # ── STATEMENT OF PROFIT AND LOSS ───────────────────────────────────────────────────────────
    "is_pl": [
        L("is_pl__revenue_from_sale_of_products_and_services", "Sale of products and services",
          ["Revenue from sale of products", "Revenue from sale of services", "Sale of products",
           "Sale of services", "Revenue from sale of goods", "Sale of goods",
           "Sale of products and services", "Revenue from sale of products and services",
           "Revenue from contracts with customers", "Sale of manufactured goods",
           "Sale of traded goods"],
          "The sale of products and services, where the statement prints it as rows of their own "
          "above 'Revenue from operations' rather than in a note; both rows bind here and add.",
          exclude=[r"\bgross\b", r"other\s+operating", r"cost\s+of", r"scrap"], scope=PL_INCOME,
          bucket="income"),
        L("is_pl__other_operating_revenue", "Other operating revenue",
          ["Other operating revenue", "Other operating revenues", "Other operating income",
           "Other revenue from operations"],
          "Other operating revenue (scrap sales, export incentives, royalties), where the "
          "statement prints it as a row of its own within revenue from operations.",
          exclude=[r"expens", r"non[\s-]*operating"], scope=PL_INCOME, bucket="income"),
        L("is_pl__sales_revenues", "Revenue from operations",
          ["Revenue from operations", "Revenue from operations (net)",
           "Income from operations", "Total revenue from operations", "Revenue"],
          "Revenue from operations, the first line of the statement of profit and loss, before "
          "other income: the sale of products and services plus other operating revenue, as the "
          "template computes it; where the statement prints the figure, that figure is read and "
          "compared.",
          exclude=[r"\bgross\b", r"total\s+(?:income|revenue)\b(?!\s+from)", r"other\s+income"],
          scope=PL_INCOME, role="subtotal", bucket="income",
          roll=("sum", ["is_pl__revenue_from_sale_of_products_and_services",
                        "is_pl__other_operating_revenue"])),
        L("is_pl__other_income", "Other income",
          ["Other income", "Other income (net)", "Other non-operating income"],
          "Other income as the statement of profit and loss prints it, after revenue from "
          "operations (interest, dividends, gains on investments).",
          exclude=[r"comprehensive", r"operating\s+revenue|from\s+operations", r"expens"],
          scope=PL_INCOME, bucket="income"),
        L("is_pl__total_income", "Total income",
          ["Total income", "Total revenue", "Total Income"],
          "Total income: revenue from operations plus other income, as the template computes it; "
          "where the statement prints it, that figure is read and compared.",
          exclude=[r"comprehensive", r"from\s+operations", r"tax"], scope=PL_INCOME,
          role="subtotal", bucket="income",
          roll=("sum", ["is_pl__sales_revenues", "is_pl__other_income"])),
        L("is_pl__cost_of_materials_consumed", "Cost of materials consumed",
          ["Cost of materials consumed", "Cost of raw materials consumed", "Cost of raw material consumed",
           "Raw materials consumed", "Cost of raw materials and components consumed",
           "Cost of material consumed", "Materials consumed", "Consumption of raw materials"],
          "Cost of materials consumed, under the expenses of the statement of profit and loss.",
          exclude=[r"inventor|closing\s+stock|opening\s+stock"], scope=PL_EXPENSES,
          bucket="expenses"),
        L("is_pl__purchases_of_stock_in_trade", "Purchases of stock-in-trade",
          ["Purchases of stock-in-trade", "Purchase of stock-in-trade", "Purchases of stock in trade",
           "Purchase of stock in trade", "Purchases of traded goods", "Purchase of traded goods"],
          "Purchases of stock-in-trade (goods bought for resale), under expenses.",
          scope=PL_EXPENSES, bucket="expenses"),
        L("is_pl__changes_in_inventories", "Changes in inventories",
          ["Changes in inventories of finished goods, work-in-progress and stock-in-trade",
           "Changes in inventories of finished goods, stock-in-trade and work-in-progress",
           "Changes in inventories of work-in-progress, finished goods and stock-in-trade",
           "Changes in inventories of finished goods, work-in-progress and traded goods",
           "Changes in inventories of finished goods and work-in-progress",
           "Changes in inventories of stock-in-trade",
           "Changes in inventories", "Change in inventories"],
          "Changes in inventories of finished goods, work-in-progress and stock-in-trade, under "
          "expenses: opening less closing stock, so an increase in stock prints negative. Kept as "
          "printed.",
          exclude=[r"raw\s+materials?", r"trade\s+receivables|payables|working\s+capital"],
          sign="either", scope=PL_EXPENSES, bucket="expenses"),
        L("is_pl__personnel_and_benefit_expenses", "Employee benefits expense",
          ["Employee benefits expense", "Employee benefit expense", "Employee benefits expenses",
           "Employee benefit expenses", "Employee costs", "Employee cost", "Staff costs",
           "Personnel expenses"],
          "Employee benefits expense — salaries, wages, contributions to provident and other "
          "funds, share-based payments, staff welfare — as the row under expenses prints it.",
          exclude=[r"payable|outstanding|advance|loan"], scope=PL_EXPENSES, bucket="expenses"),
        L("is_pl__other_expenses", "Other expenses",
          ["Other expenses", "Other expense", "Other operating expenses"],
          "Other expenses, the row under expenses that holds the expenses no other expense line "
          "names (power, freight, advertising, repairs), read as one row and not split.",
          exclude=[r"^\s*other\s+(?:operating\s+)?income", r"comprehensive"], scope=PL_EXPENSES,
          bucket="expenses"),
        L("is_pl__finance_costs", "Finance costs",
          ["Finance costs", "Finance cost", "Finance charges", "Interest and finance charges",
           "Interest and finance costs", "Interest and other finance costs", "Borrowing costs",
           "Interest expense"],
          "Finance costs: interest on borrowings and lease liabilities and other borrowing costs. "
          "Stored positive, as printed — within expenses on a Schedule III statement, or below "
          "EBITDA on one that prints it.",
          exclude=[r"income|received|receivable|earned", r"capitali[sz]ed"], scope=ALL_PL,
          bucket="interest"),
        L("is_pl__depreciation_and_amortisation", "Depreciation and amortisation expense",
          ["Depreciation and amortisation expense", "Depreciation and amortization expense",
           "Depreciation and amortisation expenses", "Depreciation and amortization expenses",
           "Depreciation, amortisation and impairment expense",
           "Depreciation, amortization and impairment expense",
           "Depreciation and amortisation", "Depreciation and amortization",
           "Depreciation and impairment expense", "Depreciation expense", "Depreciation"],
          "Depreciation and amortisation expense, read as the one row the statement prints — "
          "within expenses, or below EBITDA on a statement that prints it.",
          exclude=[r"accumulated|provision\s+for\s+depreciation", r"cash\s+flow|add\s*back"],
          scope=ALL_PL, bucket="expenses"),
        # THE SUBTOTALS BELOW ARE READ AS PRINTED, with no formula. What they add up depends on
        # how the company lays the statement out, and both reference filings lay it out apart from
        # Schedule III's own: Asian Paints and Laurus print 'Total expenses' before finance costs and
        # depreciation, then EBITDA, then those two; Asian Paints adds its share of associates before
        # exceptional items, Laurus after tax. A formula for either layout would fail the other.
        L("is_pl__total_expenses", "Total expenses",
          ["Total expenses", "Total expense", "Total Expenses"],
          "Total expenses as the statement prints the total. Read, not computed: a Schedule III "
          "statement includes finance costs and depreciation in it, one that prints EBITDA leaves "
          "them below it.",
          exclude=[r"tax", r"comprehensive"], scope=PL_EXPENSES, role="subtotal",
          bucket="expenses"),
        L("is_pl__ebitda", "EBITDA",
          ["EBITDA", "Earnings before interest, tax, depreciation and amortisation (EBITDA)",
           "Earnings before interest, tax, depreciation and amortisation",
           "Earning before interest, tax, depreciation and amortisation (EBITDA)",
           "Profit before finance costs, depreciation and amortisation, exceptional items and tax"],
          "Earnings before interest, tax, depreciation and amortisation, where the statement "
          "prints it (total income less total expenses on that layout). Read, not computed; "
          "empty where the statement does not print it.",
          regex=[(r"^(?:[ivxl]{1,6}\s+)?earnings?\s+before\s+interest\s+tax\s+depreciation\s+"
                 r"(?:and\s+)?amorti[sz]ation\b.*$"),
                 (r"^(?:[ivxl]{1,6}\s+)?(?:profit|earnings?)\s+before\s+(?:interest|finance\s+costs?)"
                 r"\s+(?:and\s+)?(?:tax(?:ation)?\s+)?depreciation\b.*$")],
          exclude=[r"\bafter\b"], sign="either", scope=ALL_PL, role="subtotal", bucket="others"),
        L("is_pl__profit_before_exceptional_items_and_tax",
          "Profit before exceptional items and tax",
          ["Profit before exceptional items and tax", "Profit/(loss) before exceptional items and tax",
           "(Loss)/profit before exceptional items and tax", "Loss before exceptional items and tax",
           "Profit before exceptional item and tax", "Profit before exceptional items and taxes"],
          "Profit before exceptional items and tax as the statement prints it. Read, not "
          "computed; empty where the statement prints no such line.",
          exclude=[r"share\s+of"], sign="either", scope=ALL_PL, role="subtotal", bucket="others"),
        L("is_pl__share_of_profit_of_associates", "Share of profit of associates and joint ventures",
          ["Share of profit of associates and joint ventures",
           "Share of profit/(loss) of associates and joint ventures",
           "Share of net profit of associates and joint ventures accounted for using the equity method",
           "Share of profit of associates", "Share of profit/(loss) of associates",
           "Share of profit in associates", "Share of profit of joint ventures",
           "Share in net profit of associates", "Share of net profit of associates"],
          "The share of the profit or loss of associates and joint ventures under the equity "
          "method, wherever a consolidated statement prints it — before exceptional items or "
          "after tax. Kept as printed: a share of loss is negative.",
          regex=[P + r"share\s+(?:of|in)\s+(?:net\s+)?(?:profit|loss|result)s?(?:\s+(?:profit|loss))?"
                 r"\s+(?:of|from|in)\s+(?:the\s+)?(?:associates?|joint\s+ventures?)\b.*$"],
          exclude=[r"comprehensive", r"\boci\b", r"before|after"], sign="either", scope=ALL_PL,
          bucket="others"),
        L("is_pl__exceptional_items", "Exceptional items",
          ["Exceptional items", "Exceptional item", "Exceptional items (net)",
           "Exceptional items - (expense)/income", "Exceptional (loss)/gain"],
          "Exceptional items as the statement prints them, with the sign printed: Schedule III "
          "prints them as a deduction from profit before exceptional items and tax.",
          exclude=[r"before", r"after"], sign="either", scope=ALL_PL, bucket="others"),
        L("is_pl__profit_loss_before_tax", "Profit before tax",
          ["Profit before tax", "Profit/(loss) before tax", "(Loss)/profit before tax",
           "Loss before tax", "Profit before taxation", "Profit/(loss) before taxation",
           "Profit before income tax", "Profit before tax for the year"],
          "Profit before tax as the statement prints it. Read, not computed: whether the share "
          "of associates and the exceptional items are above it depends on the layout.",
          exclude=[r"exceptional", r"continuing|discontinued", r"share\s+of", r"comprehensive"],
          sign="either", scope=ALL_PL, role="subtotal", bucket="others"),
        L("is_pl__current_income_taxes", "Current tax",
          ["Current tax", "Current income tax", "Current tax expense", "Current tax for the year"],
          "Current tax for the year, under tax expense. Not deferred tax and not tax for earlier "
          "years, which are lines of their own.",
          exclude=[r"deferred", r"earlier\s+years?|prior\s+(?:years?|periods?)", r"comprehensive",
                   r"paid|payable|liabilit|\basset"],
          sign="either", scope=PL_TAX, bucket="expenses"),
        L("is_pl__deferred_income_taxes_nc", "Deferred tax",
          ["Deferred tax", "Deferred tax charge", "Deferred tax credit", "Deferred tax charge/(credit)",
           "Deferred tax expense", "Deferred tax expense/(credit)", "Deferred tax (credit)/charge"],
          "Deferred tax for the year, under tax expense; a credit is negative.",
          exclude=[r"comprehensive|relating\s+to\s+items", r"\b(?:asset|assets|liability|liabilities)\b",
                   r"earlier\s+years?"],
          sign="either", scope=PL_TAX, bucket="expenses"),
        L("is_pl__earlier_years_tax", "Tax relating to earlier years",
          ["Tax relating to earlier years", "Tax adjustments relating to earlier years",
           "Taxes of earlier years", "Tax of earlier years", "Income tax relating to earlier years",
           "Short/(excess) provision for tax of earlier years",
           "Short/(excess) tax provision for earlier years", "Earlier years tax",
           "Tax in respect of earlier years"],
          "Tax for earlier years charged or written back this year, under tax expense.",
          regex=[r"^(?=.*\btax)(?=.*\b(?:earlier|prior|previous)\s+(?:years?|periods?)\b).*$"],
          exclude=[r"deferred"], sign="either", scope=PL_TAX, bucket="expenses"),
        L("is_pl__total_income_tax", "Total tax expense",
          ["Total tax expense", "Total tax expenses", "Tax expense", "Tax expenses",
           "Total income tax expense", "Income tax expense", "Total tax expense/(credit)"],
          "Total tax expense: current tax, deferred tax and tax for earlier years, as the template "
          "computes it; where the statement prints it, that figure is read and compared.",
          exclude=[r"comprehensive|relating\s+to\s+items|reclassified", r"deferred",
                   r"current\s+tax", r"earlier", r"discontinued", r"paid|payable|refund"],
          sign="either", scope=PL_TAX, role="subtotal", bucket="expenses",
          roll=("sum", ["is_pl__current_income_taxes", "is_pl__deferred_income_taxes_nc",
                        "is_pl__earlier_years_tax"])),
        L("is_pl__profit_for_the_year", "Profit for the year",
          ["Profit for the year", "Profit/(loss) for the year", "(Loss)/profit for the year",
           "Loss for the year", "Profit for the period", "Profit/(loss) for the period",
           "Net profit for the year", "Profit after tax", "Net profit after tax",
           "Profit for the year after share of profit/(loss) of associates and joint ventures"],
          "Profit for the year as the statement prints it — after the share of associates where "
          "the statement adds it after tax. Read, not computed.",
          exclude=[r"comprehensive", r"attributable", r"\bbefore\b", r"continuing|discontinued",
                   r"per\s+share|\beps\b", r"non[-\s]?controlling|minority",
                   r"brought\s+forward|carried|transferred|opening|closing|balance"],
          regex=[(r"^(?:[ivxl]{1,6}\s+)?(?:net\s+)?(?:profit|loss)(?:\s+(?:loss|profit))?\s+"
                 r"(?:for\s+the\s+(?:year|period)\s+after|after\s+tax(?:ation)?(?:\s+(?:and|&))?)"
                 r"\s+(?:the\s+)?share\s+(?:of|in)\s+(?:net\s+)?(?:profit|loss|result)")],
          sign="either", scope=ALL_PL, role="total", bucket="others"),
        L("is_pl__profit_attributable_to_owners", "Profit attributable to owners",
          ["Owners of the Company", "Owners of the parent", "Equity holders of the parent",
           "Equity holders of the parent company", "Equity holders of the Company",
           "Shareholders of the Company", "Owners of the Holding Company"],
          "The profit for the year attributable to the owners of the parent, as a consolidated "
          "statement prints it under 'Profit for the year attributable to'.",
          sign="either", scope=["profit_attributable_to"], bucket="others"),
        L("is_pl__profit_attributable_to_nci", "Profit attributable to non-controlling interests",
          ["Non-controlling interests", "Non-controlling interest", "Non controlling interests",
           "Minority interests", "Minority interest"],
          "The profit for the year attributable to non-controlling interests, as a consolidated "
          "statement prints it under 'Profit for the year attributable to'.",
          sign="either", scope=["profit_attributable_to"], bucket="others"),
    ],
    "is_oci": [
        R("is_oci__other_comprehensive_income_items", "Other comprehensive income items",
          "The items of other comprehensive income the statement prints — items that will not "
          "be reclassified to profit or loss (remeasurement of defined benefit plans, equity "
          "instruments through other comprehensive income), items that will be reclassified "
          "(foreign currency translation, cash flow hedges), and the income tax on each group — "
          "swept here with the sign printed and itemised.",
          exclude=[r"total\s+comprehensive", r"^\W*total\s+other\s+comprehensive",
                   (r"other\s+comprehensive\s+(?:income|loss|expense)\W*(?:/\W*\(?(?:loss|expense)\)?)?"
                   r"\s+for\s+the\s+(?:year|period)"),
                   r"other\s+comprehensive\s+(?:income|loss)\W*(?:/\W*\(?(?:loss|expense)\)?)?\W*,?\s*net\s+of\s+tax",
                   r"^\W*other\s+comprehensive\s+(?:income|loss)(?:\W*/\W*\(?(?:loss|expense)\)?)?\W*$",
                   r"(?:profit|loss)\s+for\s+the\s+(?:year|period)", r"attributable|owners\s+of",
                   r"non[-\s]?controlling|minority", r"per\s+share|\beps\b",
                   r"^\W*(?:\(?[ivx]{1,4}\)?\W*)?(?:basic|diluted)\b"]),
        L("is_oci__total_other_comprehensive_income", "Total other comprehensive income",
          ["Total other comprehensive income", "Total other comprehensive income/(loss)",
           "Total other comprehensive (loss)/income", "Other comprehensive income for the year, net of tax",
           "Other comprehensive income/(loss) for the year, net of tax",
           "Other comprehensive income for the year", "Other comprehensive income/(loss) for the year",
           "Other comprehensive (loss)/income for the year", "Total other comprehensive income, net of tax",
           "Other comprehensive income, net of tax"],
          "Total other comprehensive income for the year, net of tax: the sum of its items, as "
          "the template computes it; where the statement prints it, that figure is read and "
          "compared.",
          regex=[r"^(?:[ivxl]{1,6}\s+|[a-h]\s+)?(?:total\s+)?other\s+comprehensive\s+(?:income|loss|expense)"
                 r"(?:\s+(?:income|loss|expense))?(?:\s+for\s+the\s+(?:year|period))?"
                 r"(?:\s+net\s+of\s+tax(?:es)?)?" + T],
          exclude=[r"total\s+comprehensive\s+income\s+for", r"attributable"], sign="either",
          role="subtotal", roll=("sum", ["is_oci__other_comprehensive_income_items"])),
        L("is_oci__total_comprehensive_income", "Total comprehensive income for the year",
          ["Total comprehensive income for the year", "Total comprehensive income/(loss) for the year",
           "Total comprehensive (loss)/income for the year", "Total comprehensive income for the period",
           "Total comprehensive income", "Total comprehensive income/(loss)"],
          "Total comprehensive income for the year: profit for the year plus total other "
          "comprehensive income, as the template computes it; where the statement prints it, "
          "that figure is read and compared.",
          regex=[r"^(?:[ivxl]{1,6}\s+|[a-h]\s+)?total\s+comprehensive\s+(?:income|loss|expense)"
                 r"(?:\s+(?:income|loss|expense))?(?:\s+for\s+the\s+(?:year|period))?"
                 r"(?:\s+net\s+of\s+tax(?:es)?)?" + T],
          exclude=[r"other\s+comprehensive", r"attributable"], sign="either", role="total",
          scope=["is_oci", "is_pl"],
          roll=("sum", ["is_pl__profit_for_the_year", "is_oci__total_other_comprehensive_income"])),
    ],
    # ── STATEMENT OF CASH FLOWS ────────────────────────────────────────────────────────────────
    "cf_oper_indirect": [
        L("cf_oper_indirect__profit_before_tax", "Profit before tax",
          ["Profit before tax", "Profit/(loss) before tax", "(Loss)/profit before tax",
           "Profit before taxation", "Profit before income tax", "Net profit before tax",
           "Profit before tax for the year", "Loss before tax"],
          "Profit before tax, the first row of the operating activities section.",
          exclude=[r"exceptional", r"working\s+capital"], sign="either"),
        L("cf_oper_indirect__depreciation_and_amortisation", "Depreciation and amortisation",
          ["Depreciation and amortisation expense", "Depreciation and amortization expense",
           "Depreciation and amortisation", "Depreciation and amortization",
           "Depreciation, amortisation and impairment expense",
           "Depreciation, amortization and impairment expense",
           "Depreciation and impairment expense", "Depreciation"],
          "Depreciation and amortisation added back among the adjustments.", sign="either"),
        L("cf_oper_indirect__finance_costs", "Finance costs",
          ["Finance costs", "Finance cost", "Interest expense", "Interest and finance charges",
           "Interest and other finance costs", "Finance costs (net)"],
          "Finance costs added back among the adjustments to profit before tax in the operating "
          "activities section. Not the interest paid, which is a financing cash flow.",
          exclude=[r"income|received|paid"], sign="either"),
        L("cf_oper_indirect__interest_income", "Interest income",
          ["Interest income", "Interest income classified as investing cash flows",
           "Interest income on financial assets", "Interest received", "Interest income (net)"],
          "Interest income deducted among the adjustments (it is an investing inflow).",
          exclude=[r"expense|paid|cost"], sign="either"),
        L("cf_oper_indirect__chg_in_trade_receivables", "Change in trade receivables",
          ["(Increase)/decrease in trade receivables", "Decrease/(increase) in trade receivables",
           "(Increase)/Decrease in trade receivables", "Trade receivables",
           "Increase in trade receivables", "Decrease in trade receivables",
           "Change in trade receivables", "(Increase)/decrease in trade and other receivables"],
          "The movement in trade receivables among the working capital changes, kept as printed.",
          sign="either"),
        L("cf_oper_indirect__chg_in_inventories", "Change in inventories",
          ["(Increase)/decrease in inventories", "Decrease/(increase) in inventories",
           "(Increase)/Decrease in inventories", "Inventories", "Increase in inventories",
           "Decrease in inventories", "Change in inventories"],
          "The movement in inventories among the working capital changes, kept as printed.",
          sign="either"),
        L("cf_oper_indirect__chg_in_trade_payables", "Change in trade payables",
          ["Increase/(decrease) in trade payables", "(Decrease)/increase in trade payables",
           "Increase/(Decrease) in trade payables", "Trade payables", "Increase in trade payables",
           "Decrease in trade payables", "Change in trade payables",
           "Increase/(decrease) in trade and other payables"],
          "The movement in trade payables among the working capital changes, kept as printed.",
          sign="either"),
        L("cf_oper_indirect__income_taxes_paid_indirect", "Income taxes paid (net)",
          ["Income taxes paid (net of refunds)", "Income taxes paid (net)", "Income taxes paid",
           "Income tax paid (net of refunds)", "Income tax paid (net)", "Income tax paid",
           "Direct taxes paid (net of refunds)", "Direct taxes paid (net)", "Direct taxes paid",
           "Taxes paid (net of refunds)", "Taxes paid (net)", "Taxes paid"],
          "Income taxes paid, net of refunds, after cash generated from operations; an outflow "
          "is negative.",
          sign="either"),
        R("cf_oper_indirect__other_operating_items", "Other operating items",
          "Every other row of the operating activities section — the adjustments for non-cash "
          "and non-operating items and the working capital changes no line of this template "
          "names — swept here with the sign printed and itemised.",
          exclude=[r"operating\s+profit\s+before\s+working\s+capital",
                   r"cash\s+(?:generated|used)\s+(?:from|in)\s+operati",
                   r"net\s+cash.*operating\s+activit", r"cash\s+flows?\s+from\s+operating",
                   r"^\W*adjustments?\s+for", r"working\s+capital\s+(?:changes|adjustments)\W*$",
                   r"^\W*changes\s+in\s+working\s+capital"]),
        L("cf_oper_indirect__cash_flows_oper_activ_indirect", "Net cash from operating activities",
          ["Net cash generated from operating activities", "Net cash from operating activities",
           "Net cash flow from operating activities", "Net cash flows from operating activities",
           "Net cash generated by operating activities", "Net cash (used in)/generated from operating activities",
           "Net cash generated from/(used in) operating activities", "Net cash used in operating activities",
           "Net cash inflow from operating activities", "Cash generated from operating activities (net)",
           "Net cash provided by operating activities"],
          "Net cash from operating activities: the sum of the operating lines, as the template "
          "computes it; where the statement prints it, that figure is read and compared.",
          sign="either", role="total",
          roll=("sum", ["cf_oper_indirect__profit_before_tax",
                        "cf_oper_indirect__depreciation_and_amortisation",
                        "cf_oper_indirect__finance_costs", "cf_oper_indirect__interest_income",
                        "cf_oper_indirect__chg_in_trade_receivables",
                        "cf_oper_indirect__chg_in_inventories",
                        "cf_oper_indirect__chg_in_trade_payables",
                        "cf_oper_indirect__income_taxes_paid_indirect",
                        "cf_oper_indirect__other_operating_items"])),
    ],
    "cf_investing": [
        L("cf_investing__purchase_ppe", "Purchase of property, plant and equipment",
          ["Purchase of property, plant and equipment",
           "Purchase of property, plant and equipment including capital work-in-progress",
           "Purchase of property, plant and equipment and intangible assets",
           "Payments for purchase of property, plant and equipment",
           "Acquisition of property, plant and equipment",
           "Payment for acquisition of property, plant and equipment",
           "Purchase of property, plant and equipment, intangible assets and capital work-in-progress",
           "Purchase of fixed assets", "Capital expenditure on property, plant and equipment",
           "Purchase of property, plant and equipment (including capital advances)"],
          "Payments for property, plant and equipment (with capital work-in-progress and capital "
          "advances where the statement includes them); an outflow, negative.",
          exclude=[r"sale|disposal|proceeds"], sign="either"),
        L("cf_investing__proceeds_sale_ppe", "Proceeds from sale of property, plant and equipment",
          ["Proceeds from sale of property, plant and equipment",
           "Proceeds from disposal of property, plant and equipment",
           "Sale of property, plant and equipment", "Proceeds from sale of fixed assets",
           "Proceeds from sale of property, plant and equipment and intangible assets"],
          "Proceeds from the sale of property, plant and equipment and other fixed assets, an "
          "investing inflow.", sign="either"),
        L("cf_investing__purchase_financial_assets", "Purchase of investments",
          ["Purchase of investments", "Purchase of current investments",
           "Purchase of non-current investments", "Investment in mutual funds",
           "Purchase of mutual fund units", "Payments to acquire investments",
           "Purchase of other investments"],
          "Payments for investments other than in subsidiaries; an outflow, negative.",
          exclude=[r"sale|redemption|proceeds|subsidiar"], sign="either"),
        L("cf_investing__proceeds_sale_fncl_assets", "Proceeds from sale of investments",
          ["Proceeds from sale of investments", "Proceeds from sale/redemption of investments",
           "Sale of investments", "Proceeds from sale of current investments",
           "Proceeds from sale of non-current investments", "Proceeds from redemption of investments",
           "Sale of mutual fund units", "Redemption of mutual funds"],
          "Proceeds from selling or redeeming investments (mutual funds, bonds, other current and "
          "non-current investments), an investing inflow.",
          exclude=[r"subsidiar"], sign="either"),
        L("cf_investing__interest_income_rcvd_invest", "Interest received",
          ["Interest received", "Interest income received", "Interest received on investments and deposits"],
          "Interest received on deposits, loans and investments, an investing inflow. Not the "
          "interest income deducted among the operating adjustments.", sign="either"),
        L("cf_investing__dividends_received_invest", "Dividends received",
          ["Dividends received", "Dividend received", "Dividend income received",
           "Dividends received from associates", "Dividend received from subsidiaries"],
          "Dividends received from investments, associates and others, an investing inflow. Not the "
          "dividend income deducted among the operating adjustments.", sign="either"),
        R("cf_investing__other_invest_cash_flows", "Other investing cash flows",
          "Every other row of the investing activities section no line of this template names "
          "(bank deposits placed and matured, loans given, acquisitions), swept here with the "
          "sign printed and itemised.",
          exclude=[r"net\s+cash.*investing\s+activit", r"cash\s+flows?\s+from\s+investing"]),
        L("cf_investing__cash_flows_from_invest_activities", "Net cash used in investing activities",
          ["Net cash used in investing activities", "Net cash from investing activities",
           "Net cash flow from investing activities", "Net cash flows from investing activities",
           "Net cash (used in)/generated from investing activities",
           "Net cash generated from/(used in) investing activities",
           "Net cash generated from investing activities", "Net cash used in investing activities (B)",
           "Net cash (used in)/from investing activities", "Net cash outflow from investing activities"],
          "Net cash from investing activities: the sum of the investing lines, as the template "
          "computes it; where the statement prints it, that figure is read and compared.",
          sign="either", role="total",
          roll=("sum", ["cf_investing__purchase_ppe", "cf_investing__proceeds_sale_ppe",
                        "cf_investing__purchase_financial_assets",
                        "cf_investing__proceeds_sale_fncl_assets",
                        "cf_investing__interest_income_rcvd_invest",
                        "cf_investing__dividends_received_invest",
                        "cf_investing__other_invest_cash_flows"])),
    ],
    "cf_financing": [
        L("cf_financing__net_proceeds_issuance_of_capital", "Proceeds from issue of shares",
          ["Proceeds from issue of equity shares", "Proceeds from issue of shares",
           "Proceeds from issue of share capital", "Proceeds from issuance of equity shares",
           "Proceeds from allotment of shares", "Proceeds from exercise of share options",
           "Proceeds from issue of equity shares under employee stock option scheme"],
          "Proceeds from issuing equity shares, including on the exercise of employee stock "
          "options, a financing inflow.", sign="either"),
        L("cf_financing__proceeds_from_borrowings", "Proceeds from borrowings",
          ["Proceeds from borrowings", "Proceeds from non-current borrowings",
           "Proceeds from long-term borrowings", "Proceeds from current borrowings",
           "Proceeds from short-term borrowings", "Proceeds from short-term borrowings (net)",
           "Proceeds from/(repayment of) current borrowings (net)",
           "Proceeds from/(repayment of) short-term borrowings (net)",
           "Net proceeds from current borrowings", "Proceeds from term loans"],
          "Proceeds from borrowings, long or short term, and the net movement in current "
          "borrowings where the statement prints one row.", sign="either"),
        L("cf_financing__repayment_of_borrowings", "Repayment of borrowings",
          ["Repayment of borrowings", "Repayment of non-current borrowings",
           "Repayment of long-term borrowings", "Repayment of current borrowings",
           "Repayment of short-term borrowings", "Repayment of term loans",
           "Repayments of borrowings"],
          "Repayments of long-term or short-term borrowings, a financing outflow, negative. Not "
          "payments of lease liabilities.",
          exclude=[r"lease"], sign="either"),
        L("cf_financing__lease_payments", "Payment of lease liabilities",
          ["Payment of lease liabilities", "Payment of principal portion of lease liabilities",
           "Principal payment of lease liabilities", "Repayment of lease liabilities",
           "Payment of interest portion of lease liabilities", "Lease payments",
           "Payment of lease liabilities (including interest)", "Principal elements of lease payments"],
          "Payments of lease liabilities, principal and interest where the statement prints them "
          "apart; an outflow, negative.", sign="either"),
        L("cf_financing__interest_paid_fin", "Interest paid",
          ["Interest paid", "Finance costs paid", "Interest and other finance costs paid",
           "Interest paid on borrowings"],
          "Interest paid, where the statement places it under financing (with the interest on "
          "lease liabilities where the statement includes it); an outflow, negative.",
          sign="either"),
        L("cf_financing__dividends_paid_fin", "Dividends paid",
          ["Dividends paid", "Dividend paid", "Dividends paid to equity shareholders",
           "Dividend paid on equity shares", "Dividends paid to owners of the Company",
           "Dividends paid to non-controlling interests"],
          "Dividends paid to equity shareholders (and to non-controlling interests where the "
          "statement adds them), a financing outflow, negative.", sign="either"),
        R("cf_financing__other_financing_cash_flows", "Other financing cash flows",
          "Every other row of the financing activities section no line of this template names "
          "(buy-back of shares, transactions with non-controlling interests), swept here with the "
          "sign printed and itemised.",
          exclude=[r"net\s+cash.*financing\s+activit", r"cash\s+flows?\s+from\s+financing",
                   r"net\s+(?:increase|decrease|change)", r"cash\s+and\s+cash\s+equivalents\s+at",
                   r"effect\s+of\s+exchange", r"opening|closing", r"beginning|end\s+of\s+the",
                   # THE TABLES PRINTED BELOW THE CLOSING CASH BALANCE — the components of cash
                   # and cash equivalents, and the changes in liabilities from financing
                   # activities. The sweep stops at a statement's closing total, but it reads one
                   # closing total per statement TYPE, so below the standalone statement (printed
                   # first) it does not stop: Asian Paints' standalone financing section took
                   # 6,161.08 of liquid mutual funds and 1,179.29 of lease liabilities.
                   (r"^\W*(?:-\s*)?(?:cash\s+(?:on|in)\s+hand|balances?\s+with\s+banks|current\s+accounts?"
                   r"|cash\s+credit\s+accounts?|cheques|remittances?\s+in\s+transit"
                   r"|deposits?\s+with\s+original\s+maturity|in\s+(?:current|deposit)\s+accounts?)"),
                   (r"cash\s+and\s+cash\s+equivalents\s*(?:\(refer|in\s+(?:the\s+)?(?:standalone\s+|consolidated\s+)?"
                   r"statement|as\s+per)"),
                   r"liquid\s+mutual\s+funds?", r"loans?\s+repayable\s+on\s+demand",
                   r"^\W*(?:borrowings|lease\s+liabilities|other\s+liabilities)\s*\(refer\s+note"]),
        L("cf_financing__cash_flows_from_finance_activities", "Net cash used in financing activities",
          ["Net cash used in financing activities", "Net cash from financing activities",
           "Net cash flow from financing activities", "Net cash flows from financing activities",
           "Net cash (used in)/generated from financing activities",
           "Net cash generated from/(used in) financing activities",
           "Net cash generated from financing activities", "Net cash (used in)/from financing activities",
           "Net cash outflow from financing activities"],
          "Net cash from financing activities: the sum of the financing lines, as the template "
          "computes it; where the statement prints it, that figure is read and compared.",
          sign="either", role="total",
          roll=("sum", ["cf_financing__net_proceeds_issuance_of_capital",
                        "cf_financing__proceeds_from_borrowings",
                        "cf_financing__repayment_of_borrowings", "cf_financing__lease_payments",
                        "cf_financing__interest_paid_fin", "cf_financing__dividends_paid_fin",
                        "cf_financing__other_financing_cash_flows"])),
        L("cf_financing__net_increase_in_cash", "Net increase in cash and cash equivalents",
          ["Net increase/(decrease) in cash and cash equivalents",
           "Net (decrease)/increase in cash and cash equivalents",
           "Net increase in cash and cash equivalents", "Net decrease in cash and cash equivalents",
           "Net change in cash and cash equivalents", "Net increase/(decrease) in cash and cash equivalents (A+B+C)"],
          "The net change in cash and cash equivalents: the three activities added, as the "
          "template computes it; where the statement prints it, that figure is read and "
          "compared.",
          sign="either", role="subtotal",
          roll=("sum", ["cf_oper_indirect__cash_flows_oper_activ_indirect",
                        "cf_investing__cash_flows_from_invest_activities",
                        "cf_financing__cash_flows_from_finance_activities"])),
        L("cf_financing__beg_of_period_cash_and_cash_equiv", "Cash and cash equivalents at the beginning of the year",
          ["Cash and cash equivalents at the beginning of the year",
           "Cash and cash equivalents at the beginning of the period",
           "Cash and cash equivalents as at the beginning of the year",
           "Opening cash and cash equivalents", "Cash and cash equivalents - opening balance",
           "Add: Cash and cash equivalents at the beginning of the year"],
          "Cash and cash equivalents at the beginning of the year.",
          sign="either"),
        L("cf_financing__net_foreign_exchange_difference", "Effect of exchange rate changes",
          ["Effect of exchange rate changes on cash and cash equivalents",
           "Effect of exchange differences on cash and cash equivalents",
           "Effect of exchange rate fluctuations on cash and cash equivalents",
           "Exchange difference on translation of foreign currency cash and cash equivalents",
           "Effect of exchange differences on translation of foreign currency cash and cash equivalents",
           "Effect of foreign exchange rate changes"],
          "The effect of exchange rate changes on cash and cash equivalents held in foreign "
          "currency.",
          sign="either"),
        L("cf_financing__end_of_period_cash_and_cash_equiv", "Cash and cash equivalents at the end of the year",
          ["Cash and cash equivalents at the end of the year",
           "Cash and cash equivalents at the end of the period",
           "Cash and cash equivalents as at the end of the year",
           "Closing cash and cash equivalents", "Cash and cash equivalents - closing balance"],
          "Cash and cash equivalents at the end of the year: the net change plus the opening "
          "balance and the effect of exchange rate changes, as the template computes it; where "
          "the statement prints it, that figure is read and compared.",
          sign="either", role="subtotal",
          roll=("sum", ["cf_financing__net_increase_in_cash",
                        "cf_financing__beg_of_period_cash_and_cash_equiv",
                        "cf_financing__net_foreign_exchange_difference"])),
    ],
}

# A cash-flow caption's pattern may follow a glued activity heading.
_PCF = rf"^{icon._ENUM}(?:(?:{icon._alternation(_CF_GLUED)})\s+{icon._ENUM})?(?:less\s+|add\s+)?"
_ASSETS = r"(?:property\s+plant\s+(?:and\s+)?equipment|fixed\s+assets|tangible\s+assets)"
_BORROWINGS = (r"(?:long\s+term\s+|short\s+term\s+|non\s+current\s+|current\s+)?"
               r"(?:borrowings?|loans?|term\s+loans?|debentures)")
# Rule-tier patterns for the rows of a cash flow statement, whose captions vary more than any face
# statement's: each reads a family of wordings rather than one spelling.
CF_REGEX = {
    "cf_oper_indirect__income_taxes_paid_indirect": [
        _PCF + r"(?:direct\s+|income\s+)?tax(?:es)?\s+paid\b.*$"],
    "cf_investing__purchase_ppe": [
        _PCF + r"(?:purchases?|acquisitions?|payments?\s+for(?:\s+(?:purchase|acquisition))?)\s+"
        rf"(?:of\s+)?{_ASSETS}\b.*$"],
    "cf_investing__proceeds_sale_ppe": [
        _PCF + rf"(?:proceeds\s+(?:from|on)\s+)?(?:sales?|disposals?)\s+(?:of\s+)?{_ASSETS}\b.*$"],
    "cf_investing__purchase_financial_assets": [
        _PCF + r"(?:purchases?|acquisitions?|payments?\s+(?:for|to\s+acquire))\s+(?:of\s+)?"
        r"(?:current\s+|non\s+current\s+|other\s+)?investments?\b.*$"],
    "cf_investing__proceeds_sale_fncl_assets": [
        _PCF + r"(?:proceeds\s+(?:from|on)\s+)?(?:sales?|redemptions?|disposals?)"
        r"(?:\s+(?:sales?|redemptions?|disposals?))?\s+of\s+(?:current\s+|non\s+current\s+|other\s+)?"
        r"investments?\b.*$"],
    "cf_investing__interest_income_rcvd_invest": [_PCF + r"interest\s+(?:income\s+)?received\b.*$"],
    "cf_investing__dividends_received_invest": [_PCF + r"dividends?\s+(?:income\s+)?received\b.*$"],
    "cf_financing__net_proceeds_issuance_of_capital": [
        _PCF + r"proceeds\s+from\s+(?:the\s+)?(?:issue|issuance|allotment)\s+of\s+"
        r"(?:equity\s+)?(?:shares?|share\s+capital)\b.*$",
        _PCF + r"proceeds\s+(?:from|on)\s+(?:the\s+)?exercise\s+of\s+(?:employee\s+)?"
        r"(?:stock|share)\s+options?\b.*$"],
    "cf_financing__proceeds_from_borrowings": [
        _PCF + rf"proceeds\s+(?:from|of)\s+{_BORROWINGS}\b.*$"],
    "cf_financing__repayment_of_borrowings": [
        _PCF + rf"repayments?\s+(?:of\s+)?{_BORROWINGS}\b.*$"],
    "cf_financing__lease_payments": [
        _PCF + r"(?:re)?payments?\s+(?:of\s+)?(?:the\s+)?(?:(?:principal|interest)\s+"
        r"(?:portion|element|component)s?\s+(?:of\s+)?)?lease\s+liabilit\w*\b.*$"],
    "cf_financing__interest_paid_fin": [
        _PCF + r"(?:interest|finance\s+costs?|borrowing\s+costs?)\b.*\bpaid\b.*$"],
    "cf_financing__dividends_paid_fin": [_PCF + r"dividends?\s+paid\b.*$"],
    "cf_financing__beg_of_period_cash_and_cash_equiv": [
        (r"^(?:add\s+)?cash\s+and\s+cash\s+equivalents\s+(?:as\s+)?(?:at|on)\s+(?:the\s+)?"
        r"(?:beginning|start|commencement|1st\s+april|1\s+april)\b.*$")],
    "cf_financing__end_of_period_cash_and_cash_equiv": [
        (r"^cash\s+and\s+cash\s+equivalents\s+(?:as\s+)?(?:at|on)\s+(?:the\s+)?"
        r"(?:end|close|year\s+end|31st\s+march|31\s+march)\b.*$")],
    "cf_financing__net_foreign_exchange_difference": [
        (r"^(?:add\s+|less\s+)?(?:net\s+)?(?:effect|impact)\s+of\s+(?:foreign\s+)?(?:currency\s+)?"
        r"exchange\b.*$"),
        (r"^(?:add\s+|less\s+)?(?:net\s+)?(?:foreign\s+)?exchange\s+(?:rate\s+)?"
        r"(?:differences?|gains?|loss(?:es)?|fluctuations?)\b.*\bcash\s+and\s+cash\s+equivalents\b.*$")],
}
# The closing cash balance ends the statement: rows printed below it (the components of cash, the
# reconciliation of liabilities from financing activities) are narrative, and the sweep stops at
# a statement-level total (`stages.residual`, the `*_top_level` scopes).
SCOPE_OVERRIDES = {"cf_financing__end_of_period_cash_and_cash_equiv": ["cf_top_level"]}

CROSS_CHECK_EXCEPT: set[str] = set()


def _hint(aliases: list[str], glued) -> list[str]:
    """`build_icon_pair._regex_hint`, with a trailing cross-reference that may be a LETTER too —
    "(A)", "(A+B)", "[A+B+C]" — and the glued headings the caller names."""
    from app.services.mapping import normalize_label

    alts = {normalize_label(a) for a in aliases if normalize_label(a)}
    if not alts:
        return []
    before = rf"(?:(?:{icon._alternation(glued)})\s+{icon._ENUM})?"
    after = rf"(?:\s+(?:{icon._alternation(icon._GLUED_AFTER)}))?"
    return [(rf"^{icon._ENUM}{before}(?:less\s+|add\s+)?(?:{icon._alternation(alts)})"
            rf"(?:\s+(?:[ivxl]{{1,5}}|\d{{1,2}}|[a-h]))*{after}$")]

KPIS = {"intermediates": [], "ratios": [
    {"key": "current_ratio", "label": "Current ratio", "category": "Liquidity", "unit": "x",
     "numerator": [{"key": "bs_ca__total_current_assets"}],
     "denominator": [{"key": "bs_cl__total_current_liabilities"}]},
    {"key": "quick_ratio", "label": "Quick ratio", "category": "Liquidity", "unit": "x",
     "numerator": [{"key": "bs_ca__total_current_assets"}, {"key": "bs_ca__inventories", "sign": -1}],
     "denominator": [{"key": "bs_cl__total_current_liabilities"}]},
    {"key": "cash_ratio", "label": "Cash ratio", "category": "Liquidity", "unit": "x",
     "numerator": [{"key": "bs_ca__cash_and_cash_equivalents"},
                   {"key": "bs_ca__other_bank_balances", "optional": True}],
     "denominator": [{"key": "bs_cl__total_current_liabilities"}]},
    {"key": "operating_cash_flow_ratio", "label": "Operating cash flow ratio", "category": "Liquidity",
     "unit": "x", "numerator": [{"key": "cf_oper_indirect__cash_flows_oper_activ_indirect"}],
     "denominator": [{"key": "bs_cl__total_current_liabilities"}]},
    {"key": "debt_to_equity", "label": "Debt to equity", "category": "Leverage", "unit": "x",
     "numerator": [{"key": "bs_ncl__borrowings", "optional": True},
                   {"key": "bs_cl__borrowings", "optional": True}],
     "denominator": [{"key": "bs_equity__total_equity"}]},
    {"key": "liabilities_to_equity", "label": "Liabilities to equity", "category": "Leverage",
     "unit": "x", "numerator": [{"key": "bs_cl__total_liabilities"}],
     "denominator": [{"key": "bs_equity__total_equity"}]},
    {"key": "debt_ratio", "label": "Debt ratio (liabilities/assets)", "category": "Leverage",
     "unit": "%", "numerator": [{"key": "bs_cl__total_liabilities"}],
     "denominator": [{"key": "bs_ca__total_assets"}]},
    {"key": "equity_ratio", "label": "Equity ratio", "category": "Leverage", "unit": "%",
     "numerator": [{"key": "bs_equity__total_equity"}], "denominator": [{"key": "bs_ca__total_assets"}]},
    {"key": "interest_coverage", "label": "Interest coverage", "category": "Leverage", "unit": "x",
     "numerator": [{"key": "is_pl__profit_loss_before_tax"}, {"key": "is_pl__finance_costs"}],
     "denominator": [{"key": "is_pl__finance_costs"}]},
    {"key": "dso", "label": "Days sales outstanding (DSO)", "category": "Efficiency", "unit": "days",
     "numerator": [{"key": "bs_ca__trade_receivables"}], "denominator": [{"key": "is_pl__sales_revenues"}]},
    {"key": "dio", "label": "Days inventory outstanding (DIO)", "category": "Efficiency",
     "unit": "days", "numerator": [{"key": "bs_ca__inventories"}],
     "denominator": [{"key": "is_pl__cost_of_materials_consumed", "optional": True},
                     {"key": "is_pl__purchases_of_stock_in_trade", "optional": True},
                     {"key": "is_pl__changes_in_inventories", "optional": True}]},
    {"key": "dpo", "label": "Days payables outstanding (DPO)", "category": "Efficiency",
     "unit": "days", "numerator": [{"key": "bs_cl__trade_payables_cp"}],
     "denominator": [{"key": "is_pl__cost_of_materials_consumed", "optional": True},
                     {"key": "is_pl__purchases_of_stock_in_trade", "optional": True},
                     {"key": "is_pl__changes_in_inventories", "optional": True}]},
    {"key": "operating_margin", "label": "EBIT margin", "category": "Profitability", "unit": "%",
     "numerator": [{"key": "is_pl__profit_loss_before_tax"}, {"key": "is_pl__finance_costs"}],
     "denominator": [{"key": "is_pl__sales_revenues"}]},
    {"key": "net_margin", "label": "Net profit margin", "category": "Profitability", "unit": "%",
     "numerator": [{"key": "is_pl__profit_for_the_year"}], "denominator": [{"key": "is_pl__sales_revenues"}]},
    {"key": "return_on_equity", "label": "Return on equity (ROE)", "category": "Profitability",
     "unit": "%", "numerator": [{"key": "is_pl__profit_for_the_year"}],
     "denominator": [{"key": "bs_equity__total_equity"}]},
    {"key": "return_on_assets", "label": "Return on assets (ROA)", "category": "Profitability",
     "unit": "%", "numerator": [{"key": "is_pl__profit_for_the_year"}],
     "denominator": [{"key": "bs_ca__total_assets"}]},
]}

PROMPT = (
    "These are Ind AS financial statements of an Indian entity, presented under Schedule III, "
    "Division II of the Companies Act 2013: a Balance Sheet, a Statement of Profit and Loss and a "
    "Statement of Cash Flows, standalone or consolidated. Amounts are in INR and usually scaled in "
    "lakhs or crores. Every line of this template is a row the face of those statements prints: "
    "cite the printed statement row, never a note, and leave a line empty when the face does not "
    "print it. Expenses are read as printed, positive. Keep standalone and consolidated figures "
    "apart.")

SIGN_CONVENTION = {
    "stored_value": "The reported value after paren-to-negative conversion.",
    "expenses_and_outflows": "Stored POSITIVE, as Schedule III prints them under 'Expenses'. The "
                             "template subtracts them: profit before exceptional items and tax is "
                             "total income less total expenses, and profit from continuing "
                             "operations is profit before tax less total tax expense. A cash "
                             "outflow keeps the negative sign the statement of cash flows prints.",
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
    template = {"schema_version": 1, "template_key": TEMPLATE_KEY,
                "name": "Ind AS (India) — Schedule III Division II",
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
                if k not in CROSS_CHECK_EXCEPT:
                    cross.append(k)
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
                glued = icon._GLUED_BEFORE + (_CF_GLUED if stt == "cash_flow" else ())
                item["regex_hints"] = (_hint(aliases, glued) + [icon._printed_hint(h) for h in held]
                                       + spec["regex"] + CF_REGEX.get(k, []))
                if spec["exclude"]:
                    item["exclude_hints"] = [x if x.startswith("(?i)") else f"(?i){x}"
                                             for x in spec["exclude"]]
            items.append(item)

    rules = copy.deepcopy(policy["global_rules"])
    rules["sign_convention"] = {**SIGN_CONVENTION,
                                "either": policy["global_rules"]["sign_convention"]["either"],
                                "validation": policy["global_rules"]["sign_convention"]["validation"]}
    scope_sel = copy.deepcopy(policy["scope_selection"])
    markers = scope_sel.get("entity_scope", {}).get("company_only_markers", [])
    scope_sel["entity_scope"]["company_only_markers"] = [
        m.replace("bs_nca__investment_in_subsidiaries", "Investments in subsidiaries") for m in markers]

    n_lines = len(items)
    line_items = {
        "schema_version": 1, "line_items_key": LINE_ITEMS_KEY,
        "target_template_key": TEMPLATE_KEY, "target_template_version": None,
        "locale": "en", "supported_locales": ["en"],
        "metadata": {
            "name": "Ind AS (India) line items", "version": "2",
            "changes": [
                ("Built by scripts/build_indas_pair.py: the rows the face of a Schedule III "
                "Division II balance sheet and statement of profit and loss print, and the rows "
                "of an Ind AS 7 indirect-method statement of cash flows."),
                (f"{n_lines} lines, every one read from the face (strict_face_route); no internal "
                "parts, no note or sentence routes. One leftover line per balance-sheet and "
                "cash-flow section, and one for other comprehensive income."),
                ("Replaces version 1, which was the HK set with Ind AS captions added: 198 lines "
                "and 65 internal parts, 46 lines read from note tables and 6 from sentences."),
            ],
            "breaking_changes": [
                ("Lines retired with the HK-derived spine: the gross block and accumulated "
                "depreciation, the intangible build-up, related-party and shareholder balances, "
                "the adjustments to retained profits, the notes section, and the direct-method "
                "cash flow line."),
            ],
            "vocabulary_note": "English captions in Schedule III Division II wording, with the "
                               "spelling variants ICON's reviewed captions use for the same rows.",
        },
        "section_defaults": section_defaults,
        "vocabulary": copy.deepcopy(policy["vocabulary"]),
        "normalisation": copy.deepcopy(policy["normalisation"]),
        "binding": copy.deepcopy(policy["binding"]),
        "prompt": PROMPT,
        "global_rules": rules,
        "scope_selection": scope_sel,
        "residual_framework": copy.deepcopy(policy["residual_framework"]),
        "items": items,
        "others_master": {"note": "One leftover line per balance-sheet and cash-flow section, and "
                                  "one for other comprehensive income: the rows of the section no "
                                  "line claims are swept there, itemised. The statement of profit "
                                  "and loss has none, so a row no line claims is left for review.",
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
