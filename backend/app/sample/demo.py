"""The seeded demo dataset + derived views.

Mirrors the design handoff's sample data exactly so the frontend renders identically
to the wireframe. Values are in INR crore. Standalone figures are derived as
round(consolidated * 0.88) — a placeholder for a second real dataset, per the design.
"""
from __future__ import annotations

from copy import deepcopy

CONF_PCT = {"high": 96, "med": 78, "low": 54}


def conf(cat: str) -> dict:
    return {"cat": cat, "pct": CONF_PCT.get(cat, 0)}


# --- Localized output labels (multilingual parity, keyed by English label) --------
# Standard IFRS / Ind-AS statement captions translated for the seed languages
# (en/zh/ar/fr). Illustrative professional terminology — review by a native financial
# reviewer before production. The statement endpoint resolves each row's display label
# via this table by the requested locale, falling back to the English label; the
# left-hand source "paper" keeps the original (English) label to reinforce that
# extraction maps a source in one language to output in the chosen language.
LABELS_I18N: dict[str, dict[str, str]] = {
    # Statement names
    "Balance Sheet": {"zh": "资产负债表", "ar": "الميزانية العمومية", "fr": "Bilan"},
    "Statement of P&L": {"zh": "利润表", "ar": "قائمة الأرباح والخسائر", "fr": "Compte de résultat"},
    "Cash Flow": {"zh": "现金流量表", "ar": "قائمة التدفقات النقدية", "fr": "Tableau des flux de trésorerie"},
    # Balance sheet
    "ASSETS": {"zh": "资产", "ar": "الأصول", "fr": "ACTIF"},
    "Non-current assets": {"zh": "非流动资产", "ar": "الأصول غير المتداولة", "fr": "Actifs non courants"},
    "Property, plant and equipment": {"zh": "物业、厂房及设备", "ar": "الممتلكات والآلات والمعدات", "fr": "Immobilisations corporelles"},
    "Capital work-in-progress": {"zh": "在建工程", "ar": "أعمال رأسمالية قيد التنفيذ", "fr": "Immobilisations en cours"},
    "Goodwill": {"zh": "商誉", "ar": "الشهرة", "fr": "Écart d'acquisition"},
    "Other intangible assets": {"zh": "其他无形资产", "ar": "أصول غير ملموسة أخرى", "fr": "Autres immobilisations incorporelles"},
    "Investments": {"zh": "投资", "ar": "استثمارات", "fr": "Placements"},
    "Loans": {"zh": "贷款", "ar": "قروض", "fr": "Prêts"},
    "Deferred tax assets (net)": {"zh": "递延所得税资产（净额）", "ar": "أصول ضريبية مؤجلة (صافي)", "fr": "Actifs d'impôt différé (net)"},
    "Total non-current assets": {"zh": "非流动资产合计", "ar": "إجمالي الأصول غير المتداولة", "fr": "Total des actifs non courants"},
    "Current assets": {"zh": "流动资产", "ar": "الأصول المتداولة", "fr": "Actifs courants"},
    "Inventories": {"zh": "存货", "ar": "المخزون", "fr": "Stocks"},
    "Trade receivables": {"zh": "应收账款", "ar": "الذمم المدينة التجارية", "fr": "Créances clients"},
    "Cash and cash equivalents": {"zh": "现金及现金等价物", "ar": "النقد وما في حكمه", "fr": "Trésorerie et équivalents de trésorerie"},
    "Bank balances other than above": {"zh": "上述以外的银行存款", "ar": "أرصدة بنكية أخرى", "fr": "Autres soldes bancaires"},
    "Other current assets": {"zh": "其他流动资产", "ar": "أصول متداولة أخرى", "fr": "Autres actifs courants"},
    "Total current assets": {"zh": "流动资产合计", "ar": "إجمالي الأصول المتداولة", "fr": "Total des actifs courants"},
    "TOTAL ASSETS": {"zh": "资产总计", "ar": "إجمالي الأصول", "fr": "TOTAL DE L'ACTIF"},
    "EQUITY AND LIABILITIES": {"zh": "权益及负债", "ar": "حقوق الملكية والالتزامات", "fr": "CAPITAUX PROPRES ET PASSIF"},
    "Equity": {"zh": "权益", "ar": "حقوق الملكية", "fr": "Capitaux propres"},
    "Equity share capital": {"zh": "股本", "ar": "رأس المال", "fr": "Capital social"},
    "Other equity": {"zh": "其他权益", "ar": "حقوق ملكية أخرى", "fr": "Autres capitaux propres"},
    "Non-current liabilities": {"zh": "非流动负债", "ar": "الالتزامات غير المتداولة", "fr": "Passifs non courants"},
    "Borrowings": {"zh": "借款", "ar": "القروض", "fr": "Emprunts"},
    "Provisions": {"zh": "拨备", "ar": "المخصصات", "fr": "Provisions"},
    "Current liabilities": {"zh": "流动负债", "ar": "الالتزامات المتداولة", "fr": "Passifs courants"},
    "Trade payables": {"zh": "应付账款", "ar": "الذمم الدائنة التجارية", "fr": "Dettes fournisseurs"},
    "Other financial liabilities": {"zh": "其他金融负债", "ar": "التزامات مالية أخرى", "fr": "Autres passifs financiers"},
    "TOTAL EQUITY AND LIABILITIES": {"zh": "权益及负债总计", "ar": "إجمالي حقوق الملكية والالتزامات", "fr": "TOTAL DES CAPITAUX PROPRES ET DU PASSIF"},
    # P&L
    "INCOME": {"zh": "收入", "ar": "الدخل", "fr": "PRODUITS"},
    "Revenue from operations": {"zh": "营业收入", "ar": "إيرادات التشغيل", "fr": "Chiffre d'affaires"},
    "Other income": {"zh": "其他收入", "ar": "إيرادات أخرى", "fr": "Autres produits"},
    "Total income": {"zh": "收入合计", "ar": "إجمالي الدخل", "fr": "Total des produits"},
    "EXPENSES": {"zh": "费用", "ar": "المصروفات", "fr": "CHARGES"},
    "Cost of materials consumed": {"zh": "材料消耗成本", "ar": "تكلفة المواد المستهلكة", "fr": "Coût des matières consommées"},
    "Employee benefits expense": {"zh": "员工福利费用", "ar": "مصروف منافع الموظفين", "fr": "Charges de personnel"},
    "Finance costs": {"zh": "财务费用", "ar": "تكاليف التمويل", "fr": "Charges financières"},
    "Depreciation and amortisation": {"zh": "折旧及摊销", "ar": "الإهلاك والاستهلاك", "fr": "Amortissements et dépréciations"},
    "Other expenses": {"zh": "其他费用", "ar": "مصروفات أخرى", "fr": "Autres charges"},
    "Total expenses": {"zh": "费用合计", "ar": "إجمالي المصروفات", "fr": "Total des charges"},
    "Profit before tax": {"zh": "税前利润", "ar": "الربح قبل الضريبة", "fr": "Résultat avant impôt"},
    # Cash flow
    "CASH FLOW FROM OPERATING ACTIVITIES": {"zh": "经营活动现金流量", "ar": "التدفقات النقدية من الأنشطة التشغيلية", "fr": "FLUX DE TRÉSORERIE LIÉS À L'EXPLOITATION"},
    "Adjustment: depreciation": {"zh": "调整：折旧", "ar": "تعديل: الإهلاك", "fr": "Retraitement : amortissements"},
    "Working capital changes": {"zh": "营运资本变动", "ar": "التغير في رأس المال العامل", "fr": "Variation du besoin en fonds de roulement"},
    "Net cash from operating activities": {"zh": "经营活动产生的现金净额", "ar": "صافي النقد من الأنشطة التشغيلية", "fr": "Flux net de trésorerie d'exploitation"},
    "CASH FLOW FROM INVESTING ACTIVITIES": {"zh": "投资活动现金流量", "ar": "التدفقات النقدية من الأنشطة الاستثمارية", "fr": "FLUX DE TRÉSORERIE LIÉS À L'INVESTISSEMENT"},
    "Purchase of PPE / CWIP": {"zh": "购建固定资产及在建工程", "ar": "شراء ممتلكات وآلات ومعدات", "fr": "Acquisitions d'immobilisations"},
    "Net cash used in investing activities": {"zh": "投资活动使用的现金净额", "ar": "صافي النقد المستخدم في الأنشطة الاستثمارية", "fr": "Flux net de trésorerie d'investissement"},
    "Cash and cash equivalents at year end": {"zh": "期末现金及现金等价物", "ar": "النقد وما في حكمه في نهاية السنة", "fr": "Trésorerie à la clôture"},
}


def localize_label(english_label: str, locale: str) -> str:
    if locale == "en" or not locale:
        return english_label
    return LABELS_I18N.get(english_label, {}).get(locale, english_label)


# --- Project meta -----------------------------------------------------------

# The sample's configured line-item count, stated ONCE. Two cards on the shell print it — the
# template card and the line-items configuration card below — and they are the same population, so
# a second literal would be the drift this module keeps deleting.
_LINE_ITEM_COUNT = 312

PROJECT = {
    "id": "demo",
    "entity": "Reliance Industries Ltd",
    "title": "Reliance Industries Ltd — FY2024-25 Annual Report",
    "filename": "AnnualReport_RIL_FY25.pdf",
    "pages": 84,
    "standard": "Ind-AS",
    "currency": "INR",
    "currency_symbol": "₹",
    "units": "Crore",
    "periods": ["FY25", "FY24"],
    "bases": ["consolidated", "standalone"],
    # NO `progress` HERE. It read {"pct": 72, "line_items": 148, "in_review": 12} — three figures
    # derived from nothing, over a sample holding 33 statement line items and 4 review findings.
    # The Export footer printed two of them, so replacing its hard-coded "148 · 12" with these
    # literals moved the same fabrication one file down. Counted at the point of service instead:
    # api/routes/projects.py::_demo_progress.
    "template": {"key": "indas_std_v4", "name": "Ind-AS Standard Spread v4",
                 "line_items": _LINE_ITEM_COUNT},
    # THE SINGLE CONFIGURATION ENGINE, on the card that used to name an ontology. This read
    # {"file": "ontology_indas_v4.json", "rules": 1240, "aliases": 380, "status": "valid"} — a
    # second configuration the user could see and select. There is no ontology to select any more:
    # line items ARE the configuration, so the card names the line-item set. `items` is the count
    # stated above rather than a second figure ("1,240 rules" over 312 line items answered nothing).
    "line_items": {"file": "line_items_indas_v4.json", "items": _LINE_ITEM_COUNT,
                   "aliases": 380, "status": "valid"},
}

# --- Source documents (upload screen) --------------------------------------

DOCUMENTS = [
    {"name": "AnnualReport_RIL_FY25.pdf", "ext": "PDF", "meta": "84 pages · 61 native · 23 scanned · 34 MB", "tag": "Mixed"},
    {"name": "Segment_Schedules_FY25.xlsx", "ext": "XLS", "meta": "6 sheets · 1.2 MB", "tag": "Native"},
    {"name": "Auditor_Report_signed.pdf", "ext": "PDF", "meta": "12 pages · fully scanned · 8 MB", "tag": "Scanned"},
]

# --- Integrity (screen 2) ---------------------------------------------------

INTEGRITY = {
    "score": 82,
    "grade": "Fair — proceed with review",
    "summary": "3 warnings · 1 blocking resolved",
    "stats": [
        {"label": "Pages", "value": "84", "sub": "2 documents", "tone": "neutral"},
        {"label": "Native / Scanned", "value": "61 / 23", "sub": "27% scanned", "tone": "neutral"},
        {"label": "Avg OCR quality", "value": "91%", "sub": "2 pages < 70%", "tone": "warn"},
        {"label": "Blocking issues", "value": "0", "sub": "1 resolved", "tone": "ok"},
    ],
    "issues": [
        {"title": "Low OCR confidence", "detail": "Faint scan, handwritten annotations", "pages": "43-44", "note": "OCR quality 62%", "status": "Warning", "severity": "warn"},
        {"title": "Rotated pages", "detail": "Landscape schedules rotated 90°", "pages": "71, 73", "note": "Auto-corrected", "status": "Resolved", "severity": "ok"},
        {"title": "Password protected", "detail": "Owner password on source PDF", "pages": "all", "note": "Unlocked on import", "status": "Resolved", "severity": "ok"},
        {"title": "Possible missing page", "detail": "Cash-flow statement continues off-sequence", "pages": "149-151", "note": "Verify p.150", "status": "Warning", "severity": "warn"},
        {"title": "Duplicate page", "detail": "Balance sheet appears twice", "pages": "142, 288", "note": "Using p.142", "status": "Warning", "severity": "warn"},
        {"title": "Currency & units detected", "detail": "Rupees in crore stated in header", "pages": "all", "note": "INR · Crore", "status": "OK", "severity": "ok"},
    ],
}

# --- Page scope (screen 3) --------------------------------------------------

# `kind` is face / notes / other, the same three the real classifier emits, because the Page Scope
# filter chips are matched to it by POSITION on the client. Without it these cards answered every
# filter, so each chip appeared to work while filtering nothing. Counts for the chips and the
# header are derived from these cards (app/services/page_scope.py) and are not stored here.
PAGES = [
    {"no": 142, "kind": "face", "cls": "Balance Sheet", "sub": "Consolidated", "conf": "high", "included": True, "scan": "native"},
    {"no": 143, "kind": "face", "cls": "Balance Sheet", "sub": "Standalone", "conf": "high", "included": True, "scan": "native"},
    {"no": 145, "kind": "face", "cls": "Statement of P&L", "sub": "Consolidated", "conf": "high", "included": True, "scan": "native"},
    {"no": 147, "kind": "face", "cls": "Statement of P&L", "sub": "Standalone", "conf": "med", "included": True, "scan": "native"},
    {"no": 149, "kind": "face", "cls": "Cash Flow", "sub": "Consolidated", "conf": "high", "included": True, "scan": "native"},
    {"no": 151, "kind": "face", "cls": "Cash Flow", "sub": "Standalone", "conf": "med", "included": True, "scan": "scanned"},
    {"no": 156, "kind": "notes", "cls": "Notes 1-4", "sub": "PPE, CWIP", "conf": "high", "included": True, "scan": "native"},
    {"no": 171, "kind": "notes", "cls": "Note 12", "sub": "Trade receivables", "conf": "high", "included": True, "scan": "native"},
    {"no": 43, "kind": "other", "cls": "Other", "sub": "Directors report", "conf": "low", "included": False, "scan": "scanned"},
    {"no": 288, "kind": "other", "cls": "Duplicate", "sub": "Balance sheet (dup)", "conf": "med", "included": False, "scan": "native"},
]

# --- Statements (screen 4) --------------------------------------------------
# kind: section | subhead | item | subtotal | total
# Each item carries note/note2, confidence category, and an optional status flag.

BALANCE_SHEET = [
    {"id": "sec_assets", "label": "ASSETS", "kind": "section"},
    {"id": "sh_nca", "label": "Non-current assets", "kind": "subhead"},
    {"id": "ppe", "label": "Property, plant and equipment", "note": "3", "v1": 423180, "v2": 398020, "conf": "high", "kind": "item"},
    {"id": "cwip", "label": "Capital work-in-progress", "note": "4", "v1": 61020, "v2": 72400, "conf": "high", "kind": "item"},
    {"id": "goodwill", "label": "Goodwill", "note": "5", "v1": 35400, "v2": 35400, "conf": "med", "kind": "item"},
    {"id": "intang", "label": "Other intangible assets", "note": "5", "v1": 12080, "v2": 11540, "conf": "high", "kind": "item"},
    {"id": "nc_inv", "label": "Investments", "note": "6", "v1": 187600, "v2": 164900, "conf": "high", "kind": "item"},
    {"id": "nc_loans", "label": "Loans", "note": "7", "v1": 9400, "v2": 8600, "conf": "low", "kind": "item", "status": "flag"},
    {"id": "dta", "label": "Deferred tax assets (net)", "note": "8", "v1": 21150, "v2": 19800, "conf": "high", "kind": "item"},
    {"id": "sub_nca", "label": "Total non-current assets", "v1": 749830, "v2": 710760, "kind": "subtotal"},
    {"id": "sh_ca", "label": "Current assets", "kind": "subhead"},
    {"id": "inv", "label": "Inventories", "note": "9", "v1": 125400, "v2": 110300, "conf": "high", "kind": "item"},
    {"id": "c_inv", "label": "Investments", "note": "10", "note2": "10a", "v1": 43000, "v2": 38900, "conf": "med", "kind": "item"},
    {"id": "trade_recv", "label": "Trade receivables", "note": "12", "v1": 84500, "v2": 71200, "conf": "med", "kind": "item", "status": "recon"},
    {"id": "cce", "label": "Cash and cash equivalents", "note": "13", "v1": 39100, "v2": 28900, "conf": "high", "kind": "item"},
    {"id": "bank", "label": "Bank balances other than above", "note": "13", "v1": 10200, "v2": 9100, "conf": "high", "kind": "item"},
    {"id": "c_loans", "label": "Loans", "note": "7", "v1": 2200, "v2": 1900, "conf": "low", "kind": "item", "status": "flag"},
    {"id": "oca", "label": "Other current assets", "note": "14", "v1": 26400, "v2": 24100, "conf": "high", "kind": "item", "status": "edited"},
    {"id": "sub_ca", "label": "Total current assets", "v1": 330800, "v2": 284400, "kind": "subtotal"},
    {"id": "tot_assets", "label": "TOTAL ASSETS", "v1": 1268100, "v2": 1142300, "kind": "total"},
    {"id": "sec_eq", "label": "EQUITY AND LIABILITIES", "kind": "section"},
    {"id": "sh_eq", "label": "Equity", "kind": "subhead"},
    {"id": "esc", "label": "Equity share capital", "note": "15", "v1": 12000, "v2": 12000, "conf": "high", "kind": "item"},
    {"id": "oe", "label": "Other equity", "note": "16", "v1": 589000, "v2": 521400, "conf": "high", "kind": "item"},
    {"id": "sh_ncl", "label": "Non-current liabilities", "kind": "subhead"},
    {"id": "nc_borrow", "label": "Borrowings", "note": "17", "v1": 142000, "v2": 158600, "conf": "med", "kind": "item"},
    {"id": "prov_nc", "label": "Provisions", "note": "18", "v1": 31000, "v2": 28400, "conf": "high", "kind": "item"},
    {"id": "sh_cl", "label": "Current liabilities", "kind": "subhead"},
    {"id": "c_borrow", "label": "Borrowings", "note": "17", "v1": 54000, "v2": 61200, "conf": "med", "kind": "item"},
    {"id": "payables", "label": "Trade payables", "note": "19", "v1": 92200, "v2": 84100, "conf": "high", "kind": "item"},
    {"id": "ofl", "label": "Other financial liabilities", "note": "20", "v1": 21400, "v2": 19800, "conf": "low", "kind": "item", "status": "flag"},
    {"id": "prov_c", "label": "Provisions", "note": "18", "v1": 11900, "v2": 10600, "conf": "high", "kind": "item"},
    {"id": "tot_eq", "label": "TOTAL EQUITY AND LIABILITIES", "v1": 1268100, "v2": 1142300, "kind": "total"},
]

PROFIT_AND_LOSS = [
    {"id": "sec_inc", "label": "INCOME", "kind": "section"},
    {"id": "rev", "label": "Revenue from operations", "note": "21", "v1": 964700, "v2": 901300, "conf": "high", "kind": "item"},
    {"id": "oth_inc", "label": "Other income", "note": "22", "v1": 21400, "v2": 18900, "conf": "high", "kind": "item"},
    {"id": "tot_inc", "label": "Total income", "v1": 986100, "v2": 920200, "kind": "subtotal"},
    {"id": "sec_exp", "label": "EXPENSES", "kind": "section"},
    {"id": "cogs", "label": "Cost of materials consumed", "note": "23", "v1": 512300, "v2": 486100, "conf": "high", "kind": "item"},
    {"id": "emp", "label": "Employee benefits expense", "note": "24", "v1": 61200, "v2": 55400, "conf": "high", "kind": "item"},
    {"id": "fin", "label": "Finance costs", "note": "25", "v1": 18400, "v2": 20100, "conf": "med", "kind": "item", "status": "flag"},
    {"id": "dep", "label": "Depreciation and amortisation", "note": "3", "v1": 41200, "v2": 38600, "conf": "high", "kind": "item"},
    {"id": "oth_exp", "label": "Other expenses", "note": "26", "v1": 214800, "v2": 201200, "conf": "high", "kind": "item"},
    {"id": "tot_exp", "label": "Total expenses", "v1": 847900, "v2": 801400, "kind": "subtotal"},
    {"id": "pbt", "label": "Profit before tax", "v1": 138200, "v2": 118800, "kind": "total"},
]

CASH_FLOW = [
    {"id": "cf_op", "label": "CASH FLOW FROM OPERATING ACTIVITIES", "kind": "section"},
    {"id": "cf_pbt", "label": "Profit before tax", "v1": 138200, "v2": 118800, "conf": "high", "kind": "item"},
    {"id": "cf_dep", "label": "Adjustment: depreciation", "note": "3", "v1": 41200, "v2": 38600, "conf": "high", "kind": "item"},
    {"id": "cf_wc", "label": "Working capital changes", "v1": -18600, "v2": -12400, "conf": "med", "kind": "item"},
    {"id": "cf_op_net", "label": "Net cash from operating activities", "v1": 160800, "v2": 145000, "kind": "subtotal"},
    {"id": "cf_inv", "label": "CASH FLOW FROM INVESTING ACTIVITIES", "kind": "section"},
    {"id": "cf_capex", "label": "Purchase of PPE / CWIP", "note": "3", "v1": -98400, "v2": -110200, "conf": "high", "kind": "item"},
    {"id": "cf_inv_net", "label": "Net cash used in investing activities", "v1": -92100, "v2": -104300, "kind": "subtotal"},
    {"id": "cf_close", "label": "Cash and cash equivalents at year end", "note": "13", "v1": 39100, "v2": 28900, "conf": "high", "kind": "total"},
]

STATEMENTS = {
    "balance_sheet": {"label": "Balance Sheet", "rows": BALANCE_SHEET},
    "profit_and_loss": {"label": "Statement of P&L", "rows": PROFIT_AND_LOSS},
    "cash_flow": {"label": "Cash Flow", "rows": CASH_FLOW},
}

# Per-line inspector info (source refs, derivation formula, netting explanation).
INSPECTOR = {
    "trade_recv": {"tag": "Note-netted", "src": "p.142 (face) · p.171 (Note 12)",
                   "formula": "Note12.total (96,900) - Note12.3 related_party (12,400)", "result": "84,500 cr",
                   "note": "Overarching face value netted against note detail per the line-item netting rule. Related-party receivables are carried separately under Other financial assets."},
    "ppe": {"tag": "Direct", "src": "p.142 (face) · p.156 (Note 3)",
            "formula": "Note3.net_block (gross 6,84,200 - accum. depreciation 2,61,020)", "result": "4,23,180 cr",
            "note": "High-confidence direct match to the face of the Balance Sheet, cross-checked against Note 3 net block."},
    "nc_loans": {"tag": "Low confidence", "src": "p.143 (scanned)",
                 "formula": "OCR value - awaiting review", "result": "9,400 cr",
                 "note": "Extracted from a scanned page with low OCR quality. Value could not be corroborated against a note. Sent to review queue."},
    "fin": {"tag": "Sign anomaly", "src": "p.145 (face) · Note 25",
            "formula": "Note25.finance_costs", "result": "18,400 cr",
            "note": "Extracted as a credit (positive). The line-item sign rule expects finance costs to be an expense (negative). Flagged to the review queue."},
}
DEFAULT_INSPECTOR = {"tag": "Direct", "src": "p.142 (face)", "formula": "direct match", "result": "", "note": "Direct match to the face of the statement."}

# --- Review queue (screen 5) -----------------------------------------------
# The four seeded checks from the design handoff. They tell one coherent story: a
# 1,240 cr related-party netting error propagates through the balance identity and a
# section subtotal, plus a mis-signed expense and a face figure that reaches no template line.
#
# THE `type` OF EACH CHECK IS THE REAL ROUTE'S VOCABULARY, not a second one. The queue carries
# exactly three things — a face figure that reaches no line of the output (`unmapped`), a validation
# rule that failed (`balance`, `equity_tie`, `structural`), and a section subtotal that does not
# match the values under it (`calculated_mismatch`) — and the sample is the first thing a new user
# sees, so it must teach those and only those.
#
# It used to speak its own: `subtotal`, `sign` and `note`, of which only `balance` was ever a kind
# the real route serves. Those names came from `app/services/checks.py`, a parallel checks engine
# with no caller, now deleted. The fourth card was a NOTE-TIE, a category the real route does not
# raise at all any more — so the sample was advertising a queue category that cannot occur, while
# not showing the one that matters most. It is now the `unmapped` card below.
REVIEW = [
    {"id": "bs", "type": "balance", "icon": "≠", "title": "Balance sheet does not balance",
     "where": "Consolidated · Assets vs Equity & Liabilities", "severity": "Blocking", "tone": "high",
     "delta": "Δ 1,240", "target": "tot_assets",
     "calc": [["Total assets", "12,68,100", False], ["Total equity & liabilities", "12,66,860", False], ["Difference", "1,240", True]],
     "fix": "The 1,240 cr related-party receivable was netted from Trade receivables but not removed from Other financial assets. Apply the Note 12.3 netting rule to Other financial assets."},
    # The third category: a printed subtotal its own components do not come to.
    {"id": "sub", "type": "calculated_mismatch", "icon": "≠", "title": "Section subtotal mismatch — Non-current assets",
     "where": "Extracted 7,49,830 vs calculated 7,48,590", "severity": "High", "tone": "high",
     "delta": "Δ 1,240", "target": "sub_nca",
     "calc": [["Sum of extracted line items", "7,48,590", False], ["Reported subtotal", "7,49,830", False], ["Difference", "1,240", True]],
     "fix": "A duplicated Loans line (Note 7) is counted in both current and non-current. Reassign the 1,240 cr to current per note reference."},
    # A failed validation rule: the rulebook's sign expectation for an expense concept. The real
    # route serves a cross-concept guard as `structural` too — one type for "a declared rule broke".
    {"id": "sign", "type": "structural", "icon": "±", "title": "Sign anomaly — Finance costs positive",
     "where": "Statement of P&L · expense shown as credit", "severity": "Medium", "tone": "high",
     "delta": "+18,400", "target": "fin",
     # "Ontology rule" / "Ontology sign rule …" here and in i18n_data.TR: the sign expectation is
     # declared on the LINE ITEM, which is the only configuration there is, and the card says so.
     # TR is keyed on the English source string, so both sides moved together.
     "calc": [["Extracted value", "+18,400", False], ["Expected sign (expense)", "negative", True], ["Line-item rule", "debit / negative", False]],
     "fix": "Line-item sign rule for Finance costs is expense = negative. Flip sign to −18,400 to match statement convention."},
    # The FIRST category, and the one the sample was missing: a figure printed on the face that
    # reaches no line of the output. Here it is the second way in — the caption mapped to a concept
    # this template declares nowhere — which is why the card can name what it was placed on.
    {"id": "unmapped", "type": "unmapped", "icon": "?", "title": "Deposits paid for land acquisition",
     "where": "Consolidated · p.142 · Non-current assets", "severity": "Unmapped", "tone": "med",
     "delta": "—", "target": "Deposits paid for land acquisition",
     "calc": [["Source label", "Deposits paid for land acquisition", False],
              ["Mapped to", "bs_nca__deposits_for_land", True],
              ["Value", "31,900", False]],
     "fix": "The rulebook placed this caption on a concept this template puts on no statement, so the figure appears on no line. Pick the correct template line item, or confirm the template needs the concept added."},
]
# No REVIEW_TABS / REVIEW_SUMMARY here. Both were stored counts that disagreed with the four
# checks above — "All 12" over a list of four, "open: 12, passed: 136" over the same four — and
# both are now counted from REVIEW where they are served (app/api/routes/projects.py).

# --- Notes (screen 6) -------------------------------------------------------

NOTES_INDEX = [
    {"no": 3, "title": "Property, plant & equipment", "conf": "high"},
    {"no": 4, "title": "Capital work-in-progress", "conf": "high"},
    {"no": 5, "title": "Goodwill & intangibles", "conf": "med"},
    {"no": 6, "title": "Non-current investments", "conf": "high"},
    {"no": 7, "title": "Loans", "conf": "low"},
    {"no": 9, "title": "Inventories", "conf": "high"},
    {"no": 10, "title": "Current investments", "conf": "med"},
    {"no": 12, "title": "Trade receivables", "conf": "med"},
    {"no": 13, "title": "Cash & bank balances", "conf": "high"},
    {"no": 16, "title": "Other equity", "conf": "high"},
    {"no": 17, "title": "Borrowings", "conf": "med"},
    {"no": 19, "title": "Trade payables", "conf": "high"},
]

NOTE_DETAIL = {
    12: {
        "no": 12, "title": "Trade Receivables", "page": 171,
        "linked_line": "trade_recv", "linked_label": "Current assets → Trade receivables",
        "rows": [
            {"label": "Trade receivables - considered good", "v1": 92140, "v2": 78900, "conf": "high"},
            {"label": "Receivables - significant increase in credit risk", "v1": 2410, "v2": 1980, "conf": "med"},
            {"label": "Receivables - credit impaired", "v1": 2350, "v2": 2120, "conf": "med"},
            {"label": "Less: allowance for expected credit loss", "v1": -1210, "v2": -1100, "conf": "high"},
            {"label": "Note 12 total", "v1": 96900, "v2": 81900, "kind": "sub"},
            {"label": "Less: related-party receivables (Note 12.3)", "v1": -12400, "v2": -10700, "conf": "med"},
            {"label": "Net - carried to face of Balance Sheet", "v1": 84500, "v2": 71200, "kind": "tot"},
        ],
        "reconciliation": "Note total ₹96,900 cr less related-party receivables of ₹12,400 cr (Note 12.3, also carried under Other financial assets) = ₹84,500 cr reported on the face of the Balance Sheet.",
    },
}

# --- Template & line items (screen 7) --------------------------------------
# Screen 7 used to pair the template with an ontology. Line items are now the single configuration
# engine, so the node config below (aliases, sign, netting) is line-item configuration.

TEMPLATE_TREE = [
    {"id": "sec_a", "label": "ASSETS", "lvl": 0, "head": True},
    {"id": "nca", "label": "Non-current assets", "lvl": 1, "head": False},
    {"id": "ppe", "label": "Property, plant and equipment", "lvl": 2, "rule": True},
    {"id": "inv6", "label": "Investments", "lvl": 2, "rule": True},
    {"id": "ca", "label": "Current assets", "lvl": 1, "head": False},
    {"id": "inv_c", "label": "Inventories", "lvl": 2, "rule": True},
    {"id": "trade_recv", "label": "Trade receivables", "lvl": 2, "rule": True},
    {"id": "cce", "label": "Cash and cash equivalents", "lvl": 2, "rule": True},
    {"id": "sec_e", "label": "EQUITY AND LIABILITIES", "lvl": 0, "head": True},
    {"id": "equity", "label": "Equity", "lvl": 1, "head": False},
    {"id": "borrow", "label": "Borrowings", "lvl": 2, "rule": True},
    {"id": "payables", "label": "Trade payables", "lvl": 2, "rule": True},
]

NODE_CONFIG = {
    "trade_recv": {
        "breadcrumb": "Current assets → Financial assets",
        "label": "Trade receivables",
        "aliases": ["Trade receivables", "Sundry debtors", "Debtors", "Accounts receivable", "Bills receivable"],
        "sign": "as_reported",
        "value_type": "Monetary · ₹ Crore",
        "aggregation": "Leaf — sum of children",
        "netting": {"expr": "face_value = Note12.total − Note12.related_party",
                    "explain": "When a line item is fetched from a note and an overarching item on the face references that note, subtract the overlapping detail so totals stay aligned."},
    },
}

# --- Export (screen 8) ------------------------------------------------------

# ONLY the options the workbook builder actually reads. `build_xlsx` honours `confidence`
# (services/export.py:704, :723) and `notes_sheet` (:735) and nothing else — "formulas",
# "note_refs", "audit" and "hyperlinks" were read nowhere in the codebase, so ticking them changed
# no byte of the file. A switch wired to nothing is a control that looks like it does something
# and does not, which is the same defect as a button with no handler.
EXPORT_OPTIONS = [
    {"key": "confidence", "label": "Confidence score column", "on": True},
    {"key": "notes_sheet", "label": "Separate All-notes sheet", "on": True},
]

# Illustrative audit trail for the seeded project — two prior LLM analysis runs plus
# the extraction run, showing per-run token usage (input/output separately). Real runs
# triggered via POST /projects/demo/analysis are appended on top of these at runtime.
AUDIT = [
    {
        "run_id": "reliance-industries-ltd-20250731-094212",
        "entity": "Reliance Industries Ltd", "action": "analysis",
        "provider": "anthropic", "model": "claude-opus-4-8",
        "input_tokens": 4820, "output_tokens": 1136, "total_tokens": 5956,
        "status": "succeeded", "created_at": "2025-07-31T09:42:12+00:00",
    },
    {
        "run_id": "reliance-industries-ltd-20250731-093004",
        "entity": "Reliance Industries Ltd", "action": "extraction",
        "provider": "anthropic", "model": "claude-opus-4-8",
        "input_tokens": 61240, "output_tokens": 8402, "total_tokens": 69642,
        "status": "succeeded", "created_at": "2025-07-31T09:30:04+00:00",
    },
]

DEMO = {
    "project": PROJECT, "documents": DOCUMENTS, "integrity": INTEGRITY,
    "pages": PAGES, "statements": STATEMENTS,
    "inspector": INSPECTOR, "default_inspector": DEFAULT_INSPECTOR,
    "review": REVIEW,
    "notes_index": NOTES_INDEX, "note_detail": NOTE_DETAIL,
    "template_tree": TEMPLATE_TREE, "node_config": NODE_CONFIG,
    "export_options": EXPORT_OPTIONS, "audit": AUDIT,
}


def clone(obj):
    return deepcopy(obj)
