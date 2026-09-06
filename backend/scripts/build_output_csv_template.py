#!/usr/bin/env python3
"""
Build output_csv_hk_v1 template + enriched ontology JSON.

Reads:
  Output_CSV_Ontology_Revised_Extraction_Focused_v2.xlsx (or the same name .zip, with the xlsx
      inside) — committed at backend/_exports/. Override with $OUTPUT_CSV_ONTOLOGY_ZIP, or drop
      it in backend/_exports/, docs/, or the repository root and it will be found.
  Extraction Logic_Eng_v2.1.xlsx
  hkfrs_hk_china_ontology.json  (borrows normalisation/binding/global_rules)

Writes to backend/app/sample/templates/:
  output_csv_hk_v1_template.json
  output_csv_hk_ontology.json

Usage (from repo root):
  cd backend
  python scripts/build_output_csv_template.py
"""
from __future__ import annotations

import io, json, os, re, sys, zipfile
from collections import defaultdict
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.spec_alias_curation import curate_aliases, denied_aliases  # noqa: E402

# ── paths ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = Path(__file__).resolve().parents[1]
ONTOLOGY_STEM = "Output_CSV_Ontology_Revised_Extraction_Focused_v2"
ONTOLOGY_SUFFIXES = (".xlsx", ".zip")


def _locate_ontology_workbook() -> Path:
    """Where the source ontology export is, and a usable error when it is nowhere.

    Accepts the workbook either as a bare .xlsx or wrapped in a .zip, because it is passed around
    both ways. This used to name one contributor's Downloads folder by absolute Windows path, so
    a failure on any other machine — CI included — reported a path nobody else could have and
    read as a broken script rather than a missing input.
    """
    override = os.environ.get("OUTPUT_CSV_ONTOLOGY_ZIP")
    searched: list[Path] = []
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        searched.append(candidate)
    for directory in (_BACKEND / "_exports", _REPO_ROOT / "docs", _REPO_ROOT,
                      Path.home() / "Downloads"):
        for suffix in ONTOLOGY_SUFFIXES:
            candidate = directory / f"{ONTOLOGY_STEM}{suffix}"
            searched.append(candidate)
            if candidate.is_file():
                return candidate
    where = "\n  ".join(str(path) for path in searched)
    raise SystemExit(
        f"Cannot find {ONTOLOGY_STEM}{ONTOLOGY_SUFFIXES[0]} (or .zip).\n\n"
        "Set OUTPUT_CSV_ONTOLOGY_ZIP=/path/to/the/workbook, or place the file in one of:\n"
        f"  {where}\n\n"
        "It must carry the sheets: Sections, Concepts, Formula Dependencies, Template Field "
        "Audit.\n"
        "Note that the committed ontology is already curated "
        "(app/services/spec_alias_curation); rebuilding is only needed when the source "
        "workbook itself changes.")


def _load_ontology_workbook(path: Path):
    """The source workbook, whether it arrived as an .xlsx or zipped alongside other files.

    An .xlsx is itself a zip, so opening one with ZipFile succeeds and then hands back
    `[Content_Types].xml` as if it were the workbook — hence the suffix check rather than trying
    ZipFile first and hoping.
    """
    if path.suffix.lower() == ".xlsx":
        return openpyxl.load_workbook(path, data_only=True)
    with zipfile.ZipFile(path) as archive:
        inner = next((n for n in archive.namelist() if n.lower().endswith(".xlsx")), None)
        if inner is None:
            raise SystemExit(f"{path} contains no .xlsx: {archive.namelist()}")
        return openpyxl.load_workbook(io.BytesIO(archive.read(inner)), data_only=True)


ONTOLOGY_ZIP = _locate_ontology_workbook()
EXTRACT_XLSX = Path(__file__).resolve().parents[2] / "docs" / "Extraction Logic_Eng_v2.1.xlsx"
COMPUTED_XLSX = Path(__file__).resolve().parent.parent / "_exports" / "China_HongKong_Financial_Extraction_Ontology_v1.6.xlsx"
OUT_DIR      = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SHIPPED_ONT  = OUT_DIR / "hkfrs_hk_china_ontology.json"
CURATED_ONT  = OUT_DIR / "output_csv_hk_ontology.json"
CURATED_BASE = json.loads(CURATED_ONT.read_text(encoding="utf-8")) if CURATED_ONT.exists() else None
CURATED_TEMPLATE = OUT_DIR / "output_csv_hk_v1_template.json"
CURATED_TEMPLATE_BASE = (json.loads(CURATED_TEMPLATE.read_text(encoding="utf-8"))
                         if CURATED_TEMPLATE.exists() else None)

TEMPLATE_KEY = "output_csv_hk_v1"
ONTOLOGY_KEY = "output_csv_hk"

# ── helpers ───────────────────────────────────────────────────────────────────

def _rows(ws, skip: int = 1):
    for row in ws.iter_rows(min_row=skip + 1, values_only=True):
        if any(v is not None for v in row):
            yield tuple(row)

def _v(row, i, default=None):
    return row[i] if i < len(row) else default

def _quoted(text) -> list[str]:
    if not text:
        return []
    return [m.strip() for m in re.findall(r'"([^"]{1,150})"', str(text))
            if m.strip() and 1 < len(m.strip()) <= 150]

def _split_pipe(val) -> list[str]:
    return [s.strip() for s in str(val or "").split("|") if s.strip()]

def _split_comma(val) -> list[str]:
    return [s.strip() for s in str(val or "").split(",") if s.strip()]

# ── 1. Read ontology xlsx ─────────────────────────────────────────────────────

ont_wb = _load_ontology_workbook(ONTOLOGY_ZIP)

# sections: key → {label, template_sheet}
SECTIONS: dict[str, dict] = {}
for r in _rows(ont_wb["Sections"]):
    key, label, tsheet, _ = (_v(r, i) for i in range(4))
    if key:
        SECTIONS[str(key)] = {"label": str(label or ""), "template_sheet": str(tsheet or "")}

# concepts — build column-name index from header row
_hdr = [str(c or "").strip()
        for c in next(ont_wb["Concepts"].iter_rows(min_row=1, max_row=1, values_only=True))]
C = {h: i for i, h in enumerate(_hdr) if h}

def _cv(row, name, default=None):
    i = C.get(name)
    return row[i] if i is not None and i < len(row) else default

CONCEPTS: list[dict] = []
for r in _rows(ont_wb["Concepts"]):
    ckey = _cv(r, "Canonical key")
    if not ckey:
        continue
    CONCEPTS.append({
        "canonical_key":       str(ckey),
        "label":               str(_cv(r, "Label") or ""),
        "inherits":            str(_cv(r, "Inherits (section)") or ""),
        "definition":          str(_cv(r, "Definition — what this line MEANS") or ""),
        "aliases_en":          _split_comma(_cv(r, "Aliases (en)")),
        "aliases_zh":          _split_comma(_cv(r, "Aliases (zh)")),
        "keyword_hints":       _split_comma(_cv(r, "Keyword hints")),
        "regex_hints":         _split_comma(_cv(r, "Regex hints")),
        "exclude_hints":       _split_comma(_cv(r, "Exclude hints")),
        "include_text":        str(_cv(r, "Include (belongs here)") or ""),
        "exclude_text":        str(_cv(r, "Exclude (does NOT belong here)") or ""),
        "confusable_with":     _split_comma(_cv(r, "Confusable with")),
        "sign_convention":     str(_cv(r, "Sign convention") or "as_reported"),
        "value_scope":         str(_cv(r, "Value scope") or "exclusive_leaf"),
        "match_priority":      _cv(r, "Match priority"),
        "alias_matching":      str(_cv(r, "Alias matching") or "enabled"),
        "extraction_mode":     str(_cv(r, "Extraction mode") or "extract"),
        "unit_of_account":     _cv(r, "Unit of account"),
        "temporality":         _cv(r, "Temporality"),
        "note_use":            _cv(r, "Note use"),
        "face_only":           _cv(r, "Face only"),
        "is_gross_parent":     _cv(r, "Is gross parent"),
        "children_decomposed": _split_comma(_cv(r, "Children if decomposed")),
        "derivation":          _cv(r, "Derivation"),
        "decomposition_rule":  _cv(r, "Decomposition rule"),
        "aggregation_note":    _cv(r, "Aggregation note"),
        "section_disambig":    _cv(r, "Section disambiguation"),
        "sole_component_of":   _cv(r, "Sole component of"),
        "never_sweep":         _split_comma(_cv(r, "Never sweep")),
        "expected_components": _split_comma(_cv(r, "Expected components")),
        "template_note":       _cv(r, "Template note"),
        "notes_src_rationale": _cv(r, "Notes-as-source rationale"),
        "residual_policy_json": str(_cv(r, "Residual policy (JSON)") or ""),
        "equivalence_json":    str(_cv(r, "Equivalence (JSON)") or ""),
    })

# formula dependencies: derived key → [child canonical_keys]
FORMULA_DEPS: dict[str, list[str]] = {}
for r in _rows(ont_wb["Formula Dependencies"]):
    ckey, _, _, _, dep_str, ext_mode = (_v(r, i) for i in range(6))
    if ckey and dep_str:
        FORMULA_DEPS[str(ckey)] = _split_pipe(str(dep_str))
    if ckey and str(ext_mode or "") == "derive":
        for c in CONCEPTS:
            if c["canonical_key"] == str(ckey):
                c["extraction_mode"] = "derive"
                break

# template field audit: canonical_key → template row number (for ordering)
AUDIT_ORDER: dict[str, int] = {}
for r in _rows(ont_wb["Template Field Audit"]):
    tsheet, row_num, _, _, ckey = (_v(r, i) for i in range(5))
    if ckey:
        AUDIT_ORDER[str(ckey)] = int(row_num or 0)

# The source workbook contains this derived subtotal, but the legacy ontology omitted its output
# line and exposed only the enclosing section header and the later total-equity line.
_equity_base = next(c for c in CONCEPTS
                    if c["canonical_key"] == "bs_equity__total_equity_and_reserves")
CONCEPTS.append({
    **_equity_base,
    "canonical_key": "bs_equity__equity_and_reserves",
    "label": "Equity & Reserves",
    "definition": "Calculated equity and reserves subtotal from the declared component fields.",
    "aliases_en": [], "aliases_zh": [], "keyword_hints": [], "regex_hints": [],
    "exclude_hints": [], "include_text": "", "exclude_text": "", "confusable_with": [],
    "extraction_mode": "derive", "value_scope": "exclusive_parent",
    "children_decomposed": [], "derivation": None, "sole_component_of": None,
    "never_sweep": [], "expected_components": [], "residual_policy_json": "",
    "equivalence_json": "", "template_note": "Computed Field Logic Equity_022.",
})
AUDIT_ORDER["bs_equity__equity_and_reserves"] = 107
FORMULA_DEPS["bs_equity__equity_and_reserves"] = [
    "bs_equity__revaluation_reserves", "bs_equity__hedging_reserves",
    "bs_equity__other_reserves", "bs_equity__auditor_adj_on_retained_profits",
    "bs_equity__retained_profits", "bs_equity__treasury_shares",
    "bs_equity__forex_translation_equity",
    "bs_equity__subordinated_debt_from_related_parties",
    "bs_equity__subordinated_debt_equity", "bs_equity__other_equity",
    "bs_equity__policyholders_equity", "bs_equity__accum_oth_eqty_rsrv_inc",
]

# ── 2. Read extraction logic for alias enrichment ─────────────────────────────

# Value-scope and alias-matching enum maps — defined here as they're used in both
# the enrichment loop and the ontology builder below.
_VALUE_SCOPE_MAP: dict[str, str] = {
    "exclusive_leaf": "exclusive_leaf", "exclusive_child": "exclusive_child",
    "exclusive_residual": "exclusive_residual", "not_applicable": "not_applicable",
    "derived_total": "exclusive_leaf",    # derived subtotals stay as leaves
    "direct_exclusive": "exclusive_leaf", # direct extraction = standard leaf
    "subtotal": "exclusive_leaf", "total": "exclusive_leaf",
}
_ALIAS_MATCH_MAP: dict[str, str] = {
    "enabled": "enabled", "disabled": "disabled",
    "normalized_exact_then_regex": "enabled", "exact_then_regex": "enabled",
}

ext_wb = openpyxl.load_workbook(EXTRACT_XLSX, data_only=True)
LOGIC: dict[str, dict] = {}

for target in ["General", "NCA", "CA", "Equity", "NCL", "CL", "P&L", "Cash Flow", "Others", "Notes"]:
    # sheet name may have trailing space (e.g. "Equity ")
    actual = next((n for n in ext_wb.sheetnames if n.strip() == target), None)
    if actual is None:
        continue
    ws = ext_wb[actual]
    for r in ws.iter_rows(min_row=4, values_only=True):
        lbl = _v(r, 0)
        nature = _v(r, 1)
        logic_text = _v(r, 2)
        seq = _v(r, 3)
        if not lbl or str(lbl).strip() in ("Searching item", ""):
            continue
        LOGIC[str(lbl).strip().lower()] = {
            "extra_aliases": _quoted(str(logic_text) if logic_text else ""),
            "sequence": seq,
            "nature": str(nature or ""),
            "logic_text": str(logic_text or ""),
        }

# Enrich each concept with extra aliases + priority from extraction logic
for c in CONCEPTS:
    ld = LOGIC.get(c["label"].lower().strip())
    if not ld:
        continue
    existing_lc = {a.lower() for a in c["aliases_en"]}
    c["aliases_en"] += [a for a in ld["extra_aliases"]
                        if a.lower() not in existing_lc and 1 < len(a) <= 120]
    # sequence → match_priority (seq=1 → 80, seq=50 → 32; floor 10)
    seq = ld.get("sequence")
    if isinstance(seq, (int, float)) and 1 <= int(seq) <= 100:
        c["match_priority"] = max(10, 82 - int(seq))
    # Mark as residual ONLY if the label is a genuine "Other X" catch-all, not a computed
    # intermediate that just happens to depend on earlier fields.
    lbl_lower = c["label"].lower().strip()
    is_true_residual = (
        isinstance(seq, (int, float)) and int(seq) >= 2
        and re.search(r"Run (the Extraction Logic|all the Extraction)", ld["logic_text"], re.I)
        and re.search(r"^(other|others)\b", lbl_lower)
        and _VALUE_SCOPE_MAP.get(c["value_scope"], c["value_scope"]) == "exclusive_leaf"
    )
    if is_true_residual:
        c["value_scope"] = "exclusive_residual"
        c["alias_matching"] = "disabled"

# ── 3. Build template JSON ────────────────────────────────────────────────────

SEC_TO_STMT: dict[str, str] = {
    "bs_nca": "balance_sheet", "bs_ca": "balance_sheet",
    "bs_equity": "balance_sheet", "bs_ncl": "balance_sheet", "bs_cl": "balance_sheet",
    "is_pl": "profit_and_loss", "is_oci": "profit_and_loss", "is_retained": "profit_and_loss",
    "cf_oper_indirect": "cash_flow", "cf_oper_direct": "cash_flow",
    "cf_investing": "cash_flow", "cf_financing": "cash_flow",
}
SECTION_LABELS: dict[str, str] = {
    "bs_nca": "Non-Current Assets",
    "bs_ca": "Current Assets",
    "bs_equity": "Equity & Reserves",
    "bs_ncl": "Non-Current Liabilities",
    "bs_cl": "Current Liabilities",
    "is_pl": "Income & Expenses",
    "is_oci": "Other Equity & Reserve Income/(Expense)",
    "is_retained": "Adjustments to Retained Profits",
    "cf_oper_indirect": "Cash Flows from Operating Activities (Indirect Method)",
    "cf_oper_direct": "Cash Flows from Operating Activities (Direct Method)",
    "cf_investing": "Cash Flows from Investing Activities",
    "cf_financing": "Cash Flows from Financing Activities",
}
STMT_META: dict[str, tuple[str, str]] = {
    "balance_sheet":   ("Balance Sheet", "資產負債表"),
    "profit_and_loss": ("Income Statement & Comprehensive Income", "損益及其他全面收益表"),
    "cash_flow":       ("Cash Flow Statement", "現金流量表"),
}
STMT_SECTION_ORDER: dict[str, list[str]] = {
    "balance_sheet":   ["bs_nca", "bs_ca", "bs_equity", "bs_ncl", "bs_cl"],
    "profit_and_loss": ["is_pl", "is_oci", "is_retained"],
    "cash_flow":       ["cf_oper_indirect", "cf_oper_direct", "cf_investing", "cf_financing"],
}
SUPPLEMENTAL_OUTPUT_STATEMENTS: list[dict] = [
    {
        "type": "statement_setup",
        "label": "Statement Setup & Controls",
        "label_i18n": {"en": "Statement Setup & Controls"},
        "sections": [
            {
                "canonical_key": "statement_setup_controls",
                "label": "Statement Setup & Controls",
                "children": [
                    "Customer's Name", "Customer Statement Type", "Rounding",
                    "Source Currency", "Target Currency", "Statement Date", "Periods",
                    "Total Assets", "Total Equity & Reserves & Liab",
                    "Total Income & Expenses", "Difference", "Unexplained Adj to Ret Profits",
                    "Audit Opinion(Stmt Source)", "Accounting Standard", "Accountant",
                    "Analyst", "Statement type", "Status", "Reconcile To",
                ],
            }
        ],
    },
    {
        "type": "covenants_supplemental",
        "label": "Covenants & Supplemental Data",
        "label_i18n": {"en": "Covenants & Supplemental Data"},
        "sections": [
            {
                "canonical_key": "supplemental_data",
                "label": "Supplemental Data",
                "children": [
                    "Exchange Rate(Period End)", "Exchange Rate(Period Average)",
                    "Num Outstanding Common Shares", "Num Outstanding Preferred Shares",
                    "Num Employees", "Gross Premium Written*Insur. Co. Only",
                    "Gross Premium Written - life*Insur. Co. Only",
                    "Gross Premium Written - Non-life*Insur. Co. Only",
                    "Net Premium Written*Insur. Co. Only", "Net Investment Income*Insur. Co. Only",
                    "Net Technical Reserve(Equity side)*Insur. Co. Only",
                    "Net Technical Reserve(Liab. side)*Insur. Co. Only",
                ],
            },
            {
                "canonical_key": "off_balance_sheet_data",
                "label": "Off Balance Sheet Data",
                "children": [
                    "Off Balance Sheet Assets", "Operating Lease Commitments",
                    "Off Balance Sheet Liabilities",
                ],
            },
            {
                "canonical_key": "credit_compliance",
                "label": "Credit Compliance",
                "children": [
                    "Min Working Capital", "Min Current Ratio", "Min Quick Ratio",
                    "Max Debt to Tangible Worth", "Min Interest Coverage",
                    "Min Earnings Coverage", "Min Cash Flow Coverage",
                    "Max Capital Expenditures", "Max Off Balance Sheet Leverage",
                    "Min Net Profit Margin", "Min Return on Assets", "Min Return on Equity",
                    "Max Net Trade Receivable Days", "Max Inventory Days", "Max Trade Payable Days",
                    "Max Sales Growth", "Min Tangible Net Worth", "Max Subordinated Debt",
                    "Max Cash Dividends", "Min Cash Balance",
                ],
            },
            {
                "canonical_key": "capital_and_lease_commitments",
                "label": "CAPITAL AND LEASE COMMITMENTS",
                "children": [
                    "Capital commitments for fixed assets", "Capital commitments for LT assets",
                    "Capital commitments for intangible assets", "Capital Commitments for Investments",
                    "Capital Commitments", "Operating Lease Commitments (Notes)",
                ],
            },
        ],
    },
    {
        "type": "notes",
        "label": "Financial Statement Notes",
        "label_i18n": {"en": "Financial Statement Notes"},
        "sections": [
            {
                "canonical_key": "notes",
                "label": "Notes",
                "children": [
                    "Related Party Transactions", "Pledged Assets", "Secure Borrowings",
                    "Derivatives", "Contingent liabilities", "Auditor's opinion",
                    "其他Notes", "Confirmed with RM",
                ],
            }
        ],
    },
]

def _supplemental_node(section_key: str, label: str) -> dict:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    ckey = f"{section_key}__{key}"
    return {
        "node_id": ckey,
        "canonical_key": ckey,
        "label": label,
        "label_i18n": {"en": label},
        "role": "line",
    }

def _supplemental_statement(definition: dict) -> dict:
    sections = []
    for section in definition["sections"]:
        section_key = section["canonical_key"]
        sections.append({
            "node_id": section_key,
            "canonical_key": section_key,
            "label": section["label"],
            "label_i18n": {"en": section["label"]},
            "role": "header",
            "children": [_supplemental_node(section_key, label) for label in section["children"]],
        })
    return {**definition, "sections": sections}

RESIDUAL_BY_SECTION: dict[str, str] = {
    "bs_nca": "bs_nca__other_non_current_assets",
    "bs_ca": "bs_ca__other_current_assets",
    "bs_equity": "bs_equity__other_reserves",
    "bs_ncl": "bs_ncl__other_non_current_liabilities",
    "bs_cl": "bs_cl__other_current_liabilities",
    "is_pl": "is_pl__other_operating_expenses",
    "is_oci": "is_oci__other_equity_and_reserves_adj",
    "is_retained": "is_retained__other_adj_to_retained_profits",
    "cf_oper_indirect": "cf_oper_indirect__other_non_cash_adjs_oper",
    "cf_investing": "cf_investing__other_invest_cash_flows",
    "cf_financing": "cf_financing__other_financing_cash_flows",
}

for section, residual_key in RESIDUAL_BY_SECTION.items():
    for concept in CONCEPTS:
        if concept["canonical_key"] != residual_key:
            continue
        concept["value_scope"] = "exclusive_residual"
        concept["alias_matching"] = "disabled"
        concept["residual_policy_json"] = json.dumps({
            "framework": "residual_framework",
            "section_scope": section,
            "population": "sweep_only",
            "cross_section": False,
            "notes_as_source": False,
            "plug": False,
            "itemise": True,
        })
        break

# group concepts by section, sorted by template audit order
BY_SEC: dict[str, list[dict]] = defaultdict(list)
for c in CONCEPTS:
    if c["inherits"] in SEC_TO_STMT:
        BY_SEC[c["inherits"]].append(c)
for sk in BY_SEC:
    BY_SEC[sk].sort(key=lambda c: AUDIT_ORDER.get(c["canonical_key"], 9999))

# Formula Dependencies stores range endpoints. Expand only ranges explicitly written in the
# concept definition; standalone non-contiguous terms remain standalone.
_HOME = {c["canonical_key"]: section for section, concepts in BY_SEC.items() for c in concepts}
for concept in CONCEPTS:
    key = concept["canonical_key"]
    dependencies = FORMULA_DEPS.get(key)
    if not dependencies or key not in _HOME:
        continue
    replacements: dict[str, tuple[str, list[str]]] = {}
    for first, last in re.findall(r"\{([^{}]+)\}:\{([^{}]+)\}", concept["definition"]):
        section = _HOME.get(first)
        if not section or _HOME.get(last) != section:
            raise RuntimeError(f"Formula range crosses sections: {key}: {first}:{last}")
        section_keys = [c["canonical_key"] for c in BY_SEC[section]]
        start, end = section_keys.index(first), section_keys.index(last)
        replacements[first] = (last, section_keys[start:end + 1])
    expanded: list[str] = []
    consumed_ends: set[str] = set()
    for child in dependencies:
        if child in replacements:
            end, keys = replacements[child]
            expanded.extend(keys)
            consumed_ends.add(end)
        elif child not in consumed_ends:
            expanded.append(child)
    FORMULA_DEPS[key] = list(dict.fromkeys(expanded))

# MEMBERS WHOSE FIGURE IS A MAGNITUDE, not a signed amount, per rollup that sums them.
#
# The depreciation and impairment charges are the only two: every other member of these formulas
# is read straight off the face already signed, but these two are ASSEMBLED — the operating share
# comes out of the PBT note and the cost-of-sales share out of the segment or PPE note, and a
# service writes them after `normalize`, carrying the magnitude it summed. In a flat sum of
# negative cost lines a positive charge adds where its siblings subtract, so the subtotal moves by
# twice the figure. Declared per rollup rather than on the concept because the same concept is a
# magnitude only where the formula spends it: the residual rollups that subtract it from a
# reported parent must leave it alone.
COST_MAGNITUDE_CHILDREN: dict[str, list[str]] = {
    "is_pl__total_cost_of_sales": ["is_pl__deprec_and_impairment_cos"],
    "is_pl__net_operating_profit": ["is_pl__deprec_and_impairment_oper_exp"],
}

_TOTAL_LABELS = frozenset({
    "total assets", "total equity and liabilities", "total liabilities",
    "total equity & reserves", "profit for the year",
    "cash flows oper activ(indirect)", "cash flows oper activ(direct)",
    "cash flows from invest activities", "cash flows from finance activities",
})
# Label fragments indicating expense/contra sign
_NEG_MARKERS = re.compile(r"\(-\)$|\(-\)\s*$")

def _role(c: dict) -> str:
    if c["extraction_mode"] == "derive" or c["canonical_key"] in FORMULA_DEPS:
        return "total" if c["label"].lower() in _TOTAL_LABELS else "subtotal"
    return "line"

def _node(c: dict) -> dict:
    ckey = c["canonical_key"]
    lbl = c["label"]
    role = _role(c)
    node: dict = {
        "node_id": ckey,
        "canonical_key": ckey,
        "label": lbl,
        "label_i18n": {"en": lbl},
        "role": role,
    }
    sc = c.get("sign_convention", "")
    if _NEG_MARKERS.search(lbl) or sc in ("natural_negative", "expense_contra", "credit_positive"):
        node["sign"] = "natural_negative"
    if role in ("subtotal", "total") and ckey in FORMULA_DEPS:
        node["rollup"] = {"op": "sum", "children": FORMULA_DEPS[ckey]}
        magnitudes = COST_MAGNITUDE_CHILDREN.get(ckey)
        if magnitudes:
            stray = [child for child in magnitudes if child not in FORMULA_DEPS[ckey]]
            if stray:
                # The formula moved and the declaration did not. Silently dropping it would ship
                # a template that reads as sign-adjusted and is not.
                raise RuntimeError(
                    f"COST_MAGNITUDE_CHILDREN for {ckey} names non-members: {sorted(stray)}")
            node["rollup"]["cost_magnitude_children"] = list(magnitudes)
    return node

def _section_node(sec_key: str) -> dict:
    lbl = SECTION_LABELS[sec_key]
    return {
        "node_id": sec_key,
        "canonical_key": sec_key,
        "label": lbl,
        "label_i18n": {"en": lbl},
        "role": "header",
        "children": [_node(c) for c in BY_SEC.get(sec_key, [])],
    }

statements = []
for stmt_type, sec_keys in STMT_SECTION_ORDER.items():
    en_lbl, zh_lbl = STMT_META[stmt_type]
    statements.append({
        "type": stmt_type,
        "label": en_lbl,
        "label_i18n": {"en": en_lbl, "zh": zh_lbl},
        "sections": [_section_node(sk) for sk in sec_keys],
    })

template_json: dict = {
    "schema_version": 1,
    "template_key": TEMPLATE_KEY,
    "name": "Output CSV HK Financial Statement Template v1",
    "statements": [],
}
statement_setup = [statement for statement in SUPPLEMENTAL_OUTPUT_STATEMENTS
                   if statement["type"] == "statement_setup"]
other_supplemental = [statement for statement in SUPPLEMENTAL_OUTPUT_STATEMENTS
                      if statement["type"] != "statement_setup"]
template_json["statements"].extend(
    _supplemental_statement(statement) for statement in statement_setup
)
template_json["statements"].extend(statements)
template_json["statements"].extend(
    _supplemental_statement(statement) for statement in other_supplemental
)

# A reported aggregate can coexist with its classified detail. These rules preserve the aggregate
# as evidence while deriving the unclassified remainder when both forms are available.
CONDITIONAL_ROLLUPS: dict[str, dict] = {
    "bs_nca__net_fixed_assets": {
        "op": "sum",
        "children": [
            "bs_nca__gross_fixed_assets",
            "bs_nca__accum_deprec_and_impairment",
        ],
    },
    "bs_ca__inventories": {
        "reported_total_key": "bs_ca__inventories",
        "reported_total_op": "diff",
    },
    "bs_equity__retained_profits": {
        "reported_total_key": "bs_equity__retained_profits",
        "reported_total_op": "diff",
        "children": [
            "bs_equity__revaluation_reserves", "bs_equity__hedging_reserves",
            "bs_equity__other_reserves", "bs_equity__auditor_adj_on_retained_profits",
        ],
    },
    "is_oci__other_equity_and_reserves_adj": {
        "reported_total_key": "is_oci__total_other_comprehensive_income",
        "reported_total_op": "diff",
        "use_reported_total_components": True,
    },
    "cf_oper_indirect__other_non_cash_adjs_oper": {
        "reported_total_key": "cf_oper_indirect__cash_flows_oper_activ_indirect",
        "reported_total_op": "diff",
        "use_reported_total_components": True,
    },
    "cf_investing__other_invest_cash_flows": {
        "reported_total_key": "cf_investing__cash_flows_from_invest_activities",
        "reported_total_op": "diff",
        "use_reported_total_components": True,
    },
    "cf_financing__other_financing_cash_flows": {
        "reported_total_key": "cf_financing__cash_flows_from_finance_activities",
        "reported_total_op": "diff",
        "use_reported_total_components": True,
    },
}
HAND_AUTHORED_ROLLUP_KEYS = set(CONDITIONAL_ROLLUPS)

# Every arithmetic row in the normalized workbook must either compile to an existing template
# target or be named here because that output is outside this template's statement surface.
ARITHMETIC_RULE_EXCLUSIONS = {
    "P&L_091": "target absent from the output template",
    "Others_011": "insurance-only target absent from the output template",
    "Notes_013": "note-only target absent from the output template",
    "NCA_023": "balance-sheet accumulated intangible amortisation; extracted, not computed",
    "CA_038": "balance-sheet doubtful accounts allowance; extracted, not computed",
}
# Curated nodes that must stay plain extracted lines even if an older base carried a rollup.
EXTRACTED_NOT_COMPUTED_KEYS = {
    "bs_nca__accum_deprec_and_impairment",
    "bs_nca__accum_goodwill_amortized",
    "bs_nca__accum_intgbl_assets_amort",
    "bs_ca__allow_for_doubtful_accounts",
    "is_pl__goodwill_amortization",
}
FIELD_ID_CANONICAL_OVERRIDES = {
    "Equity_022": "bs_equity__equity_and_reserves",
    "Cash Flow_006": "cf_oper_indirect__minority_interests_cf",
    "Cash Flow_030": "cf_oper_indirect__income_taxes_paid_indirect",
    "Cash Flow_039": "cf_oper_direct__income_taxes_paid_direct",
}

logic_wb = openpyxl.load_workbook(COMPUTED_XLSX, data_only=True, read_only=True)
field_rows = _rows(logic_wb["Field Ontology"], skip=3)
field_labels = {str(row[0]): str(row[4]).strip() for row in field_rows if row[0] and row[4]}
canonical_by_label = {c["label"].strip().casefold(): c["canonical_key"] for c in CONCEPTS}
canonical_by_field = {
    field_id: FIELD_ID_CANONICAL_OVERRIDES.get(
        field_id, canonical_by_label.get(label.casefold()))
    for field_id, label in field_labels.items()
}
field_id_pattern = re.compile(r"(?:NCA|CA|Equity|NCL|CL|P&L|Cash Flow|Others|Notes)_\d{3}")
arithmetic_ids: set[str] = set()
compiled_ids: set[str] = set()
for row in _rows(logic_wb["Computed Field Logic"], skip=3):
    field_id = str(_v(row, 0) or "")
    computation_type = str(_v(row, 4) or "")
    dependency_ids = list(dict.fromkeys(field_id_pattern.findall(str(_v(row, 6) or ""))))
    dependency_ids = [dependency_id for dependency_id in dependency_ids
                      if dependency_id != field_id]
    is_arithmetic = computation_type == "COMPUTE_TOTAL" or (
        computation_type == "COMPUTE_RESIDUAL" and bool(dependency_ids))
    if not is_arithmetic:
        continue
    arithmetic_ids.add(field_id)
    if field_id in ARITHMETIC_RULE_EXCLUSIONS:
        continue
    target = canonical_by_field.get(field_id)
    if computation_type == "COMPUTE_TOTAL" and target in FORMULA_DEPS:
        compiled_ids.add(field_id)
        continue
    dependencies = [canonical_by_field.get(dependency_id) for dependency_id in dependency_ids]
    if not target or any(dependency is None for dependency in dependencies):
        missing = [dependency_id for dependency_id, dependency in zip(dependency_ids, dependencies)
                   if dependency is None]
        raise RuntimeError(
            f"Arithmetic rule {field_id} cannot resolve target={target!r}, dependencies={missing}")
    if computation_type == "COMPUTE_TOTAL":
        if target not in FORMULA_DEPS:
            CONDITIONAL_ROLLUPS[target] = {"op": "sum", "children": dependencies}
    else:
        CONDITIONAL_ROLLUPS.setdefault(target, {
            "reported_total_key": target,
            "reported_total_op": "diff",
            "children": dependencies,
        })
    compiled_ids.add(field_id)

unaccounted_arithmetic = arithmetic_ids - compiled_ids - set(ARITHMETIC_RULE_EXCLUSIONS)
if unaccounted_arithmetic:
    raise RuntimeError(f"Unaccounted arithmetic rules: {sorted(unaccounted_arithmetic)}")

_template_nodes = {
    node["canonical_key"]
    for statement in statements
    for section in statement["sections"]
    for node in section["children"]
} | {section["canonical_key"] for statement in statements for section in statement["sections"]}
missing_conditional_nodes = set(CONDITIONAL_ROLLUPS) - _template_nodes
if missing_conditional_nodes:
    raise RuntimeError(f"Conditional rollup target(s) missing from template: {missing_conditional_nodes}")
for section in (section for statement in statements for section in statement["sections"]):
    for node in [section, *section["children"]]:
        config = CONDITIONAL_ROLLUPS.get(node["canonical_key"])
        if config:
            node["rollup"] = {**(node.get("rollup") or {"op": "sum", "children": []}), **config}

# ── 3b. Specification-derived alias curation ─────────────────────────────────
# `extra_aliases` above is scraped from the Extraction Logic workbook's free-text formula column,
# which quotes the components a formula DEDUCTS and the notes it merely reads alongside the
# captions it matches. Left in, those aliases point the caption mapper at exactly the concepts a
# specification excludes — see app/services/spec_alias_curation for the per-concept rules and
# tests/test_spec_conformance.py for the same rules pinned against the generated file.
_denied_total = 0
for c in CONCEPTS:
    ckey = c["canonical_key"]
    for field_name in ("aliases_en", "aliases_zh"):
        original = c.get(field_name) or []
        removed = denied_aliases(ckey, original)
        if removed:
            c[field_name] = curate_aliases(ckey, original)
            _denied_total += len(removed)
            print(f"  curated {ckey}.{field_name}: dropped {len(removed)} "
                  f"specification-excluded alias(es): {removed}")
if _denied_total:
    print(f"  specification alias curation removed {_denied_total} alias(es) in total")

# ── 4. Build ontology JSON ────────────────────────────────────────────────────

_SEC_SIGN: dict[str, str] = {
    "bs_equity": "either", "is_pl": "either", "is_oci": "either",
    "is_retained": "either", "cf_oper_indirect": "either", "cf_oper_direct": "either",
    "cf_investing": "either", "cf_financing": "either",
}
_BS_SECS = {"bs_nca", "bs_ca", "bs_equity", "bs_ncl", "bs_cl"}

def _sec_default(sk: str) -> dict:
    stmt = SEC_TO_STMT[sk]
    is_bs = sk in _BS_SECS
    d: dict = {
        "statement": stmt,
        "section_scope": [sk],
        "temporality": "instant" if is_bs else "duration",
        "unit_of_account": "balance" if is_bs else "flow",
        "value_scope": "exclusive_leaf",
        "extraction_mode": "extract",
        "face_only": True,
        "note_use": "decomposition_allowed",
        "note_use_rationale": (
            "A cited note may supply dedicated template concepts before the face aggregate, but "
            "only when same-subsection detail and residual rows reconcile to the face amount."
        ),
        "sign_convention": _SEC_SIGN.get(sk, "positive_expected"),
        "match_priority": 50,
        "include": ["The face amount for the selected entity_scope, period, currency and unit."],
        "exclude": [
            "Amounts a dedicated concept in this section covers.",
            "Section subtotals and statement totals.",
        ],
    }
    return d

section_defaults = {sk: _sec_default(sk) for sk in SEC_TO_STMT}

_SIGN_MAP: dict[str, str] = {
    "natural": "natural", "natural_positive": "natural_positive",
    "natural_negative": "natural_negative", "debit_positive": "debit_positive",
    "credit_positive": "credit_positive", "context": "context",
    "expense_contra": "natural_negative",
}
# (value_scope + alias_matching maps are defined in section 2 above)

def _mapping(c: dict) -> dict | None:
    if c["inherits"] not in SEC_TO_STMT:
        return None
    ckey = c["canonical_key"]
    m: dict = {"canonical_key": ckey, "label": c["label"]}
    if c.get("definition"):
        m["definition"] = c["definition"]
    if c.get("aliases_en"):
        m["aliases"] = c["aliases_en"]
    if c.get("aliases_zh"):
        m["aliases_i18n"] = {"zh": c["aliases_zh"]}
    if c.get("keyword_hints"):
        m["keyword_hints"] = c["keyword_hints"]
    if c.get("regex_hints"):
        m["regex_hints"] = c["regex_hints"]
    if c.get("exclude_hints"):
        m["exclude_hints"] = c["exclude_hints"]
    if c.get("include_text"):
        m["include"] = [c["include_text"]]
    if c.get("exclude_text"):
        m["exclude"] = [c["exclude_text"]]
    if c.get("confusable_with"):
        m["confusable_with"] = c["confusable_with"]
    raw_vs = c.get("value_scope", "exclusive_leaf")
    m["value_scope"] = _VALUE_SCOPE_MAP.get(str(raw_vs), "exclusive_leaf")
    m["extraction_mode"] = c.get("extraction_mode", "extract")
    if ckey in FORMULA_DEPS or ckey in CONDITIONAL_ROLLUPS:
        m["extraction_mode"] = "extract_or_derive"
        m["unit_of_account"] = "subtotal"
    pri = c.get("match_priority")
    if pri is not None:
        try:
            m["match_priority"] = int(pri)
        except (TypeError, ValueError):
            pass
    raw_am = c.get("alias_matching", "enabled")
    m["alias_matching"] = _ALIAS_MATCH_MAP.get(str(raw_am), "enabled")
    m["inherits"] = c["inherits"]
    if c.get("face_only") is not None:
        m["face_only"] = bool(c["face_only"])
    if c.get("is_gross_parent"):
        m["is_gross_parent"] = bool(c["is_gross_parent"])
    if c.get("children_decomposed"):
        m["children_if_decomposed"] = c["children_decomposed"]
    if c.get("sole_component_of"):
        m["sole_component_of"] = str(c["sole_component_of"])
    if c.get("expected_components"):
        m["expected_components"] = c["expected_components"]
    if c.get("never_sweep"):
        m["never_sweep"] = c["never_sweep"]
    if c.get("decomposition_rule"):
        m["decomposition_rule"] = str(c["decomposition_rule"])
    if c.get("aggregation_note"):
        m["aggregation_note"] = str(c["aggregation_note"])
    if c.get("section_disambig"):
        m["section_disambiguation"] = str(c["section_disambig"])
    if c.get("derivation"):
        m["derivation"] = str(c["derivation"])
    if c.get("template_note"):
        m["template_note"] = str(c["template_note"])
    backend_sign = _SIGN_MAP.get(c.get("sign_convention", ""))
    if backend_sign:
        m["sign_rule"] = {"convention": backend_sign}
    rp = c.get("residual_policy_json", "")
    if rp and rp not in ("", "[]", "{}", "None"):
        try:
            parsed = json.loads(rp)
            if parsed and isinstance(parsed, dict):
                # Strip keys not in ResidualPolicy schema
                _RP_KEYS = {"framework", "section_scope", "population",
                            "cross_section", "notes_as_source", "plug", "itemise"}
                cleaned = {k: v for k, v in parsed.items() if k in _RP_KEYS}
                if cleaned:
                    m["residual_policy"] = cleaned
        except Exception:
            pass
    eq = c.get("equivalence_json", "")
    if eq and eq not in ("", "[]", "{}", "None"):
        try:
            parsed = json.loads(eq)
            if parsed and isinstance(parsed, dict):
                m["equivalence"] = parsed
        except Exception:
            pass
    return m

mappings = [m for c in CONCEPTS if (m := _mapping(c)) is not None]

shipped = json.loads(SHIPPED_ONT.read_text(encoding="utf-8"))
ontology_json: dict = {
    "schema_version": 2,
    "ontology_key": ONTOLOGY_KEY,
    "target_template_key": TEMPLATE_KEY,
    "locale": "en",
    "supported_locales": ["en"],
    "normalisation":      shipped.get("normalisation", {}),
    "binding":            shipped.get("binding", {}),
    "scope_selection":    shipped.get("scope_selection", {}),
    "global_rules":       shipped.get("global_rules", {}),
    "residual_framework": shipped.get("residual_framework", {}),
    "section_defaults":   section_defaults,
    "mappings":           mappings,
}

# ── 5. Validate with backend pydantic schemas ─────────────────────────────────

_backend = str(Path(__file__).resolve().parent.parent)
if _backend not in sys.path:
    sys.path.insert(0, _backend)
os.environ.setdefault("FINEX_DATABASE_URL", "sqlite:///./tmp_val.db")
os.environ.setdefault("PYTHONUTF8", "1")

from app.schemas.loader import (
    load_template, load_ontology, validate_ontology_against_template, resolve_inherits,
)

print("Validating template...")
tpl_obj = load_template(template_json)
node_count = sum(1 for _ in tpl_obj.all_nodes())
print(f"  ✓ {node_count} nodes across {len(statements)} statements")

print("Validating ontology...")
ont_obj = load_ontology(ontology_json)
print(f"  ✓ {len(ont_obj.mappings)} mappings")

print("Validating resolved ontology (inherits fold)...")
load_ontology(resolve_inherits(ontology_json))
print("  ✓ resolved ok")

print("Cross-validating ontology ↔ template...")
errors = validate_ontology_against_template(ont_obj, tpl_obj)
if errors:
    print(f"  ⚠ {len(errors)} cross-validation issue(s) — first 15:")
    for e in errors[:15]:
        print(f"    {e.location}: {e.message}")
else:
    print("  ✓ passed — every ontology key exists in the template")

# ── 6. Write output files ─────────────────────────────────────────────────────

OUT_DIR.mkdir(parents=True, exist_ok=True)
if CURATED_TEMPLATE_BASE is not None:
    def _nodes(definition: dict) -> dict[str, dict]:
        found: dict[str, dict] = {}
        pending = [section for statement in definition.get("statements", [])
                   for section in statement.get("sections", [])]
        while pending:
            node = pending.pop()
            if node.get("canonical_key"):
                found[node["canonical_key"]] = node
            pending.extend(node.get("children") or [])
        return found

    generated_nodes = _nodes(template_json)
    curated_nodes = _nodes(CURATED_TEMPLATE_BASE)
    generated_statement_types = [statement.get("type") for statement in template_json["statements"]]
    generated_statement_type_set = set(generated_statement_types)
    curated_by_type = {statement.get("type"): statement
                       for statement in CURATED_TEMPLATE_BASE["statements"]}
    for statement in template_json["statements"]:
        curated_by_type.setdefault(statement.get("type"), statement)
    CURATED_TEMPLATE_BASE["statements"] = [
        curated_by_type[statement_type] for statement_type in generated_statement_types
        if statement_type in curated_by_type
    ] + [
        statement for statement in CURATED_TEMPLATE_BASE["statements"]
        if statement.get("type") not in generated_statement_type_set
    ]
    for canonical_key, generated_node in generated_nodes.items():
        if (generated_node.get("rollup") and canonical_key in curated_nodes
            and not curated_nodes[canonical_key].get("rollup")):
            curated_nodes[canonical_key]["rollup"] = generated_node["rollup"]
    for canonical_key in EXTRACTED_NOT_COMPUTED_KEYS:
        if canonical_key in curated_nodes:
            curated_nodes[canonical_key].pop("rollup", None)
    subtotal_key = "bs_equity__equity_and_reserves"
    if subtotal_key not in curated_nodes:
        equity_section = curated_nodes["bs_equity"]
        total_index = next((index for index, node in enumerate(equity_section["children"])
                            if node.get("canonical_key") == "bs_equity__total_equity_and_reserves"),
                           len(equity_section["children"]))
        equity_section["children"].insert(total_index, generated_nodes[subtotal_key])
    template_json = CURATED_TEMPLATE_BASE

if CURATED_BASE is not None:
    generated_by_key = {mapping["canonical_key"]: mapping for mapping in ontology_json["mappings"]}
    curated_mappings = list(CURATED_BASE.get("mappings") or [])
    curated_by_key = {mapping["canonical_key"]: mapping for mapping in curated_mappings}
    subtotal_key = "bs_equity__equity_and_reserves"
    if subtotal_key not in curated_by_key:
        curated_mappings.append(generated_by_key[subtotal_key])
    ontology_json = {**CURATED_BASE, "mappings": curated_mappings}

tpl_path = OUT_DIR / f"{TEMPLATE_KEY}_template.json"
ont_path = OUT_DIR / f"{ONTOLOGY_KEY}_ontology.json"

tpl_path.write_text(json.dumps(template_json, indent=2, ensure_ascii=False), encoding="utf-8")
ont_path.write_text(json.dumps(ontology_json, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"\n✓  {tpl_path}")
print(f"✓  {ont_path}")
_in_scope = sum(1 for c in CONCEPTS if c["inherits"] in SEC_TO_STMT)
_enriched = sum(1 for c in CONCEPTS
                if c["inherits"] in SEC_TO_STMT and LOGIC.get(c["label"].lower().strip()))
print(f"\nSummary: {_in_scope} in-scope concepts, {_enriched} enriched from extraction logic")
