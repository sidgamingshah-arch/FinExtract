"""The ROW hints that have to land with the note-gate widenings — and the one widening to drop.

A ROW HINT CAN ONLY BE JUDGED INSIDE THE NOTES THE GATE ADMITS, which is why this is a second pass
and not part of the first. Three of the five widened gates admit notes whose rows are NOT what the
part wants, and in two of those cases the row rule is what makes the widening safe. In the third no
row rule exists, so the widening is withdrawn.
"""
from __future__ import annotations

import json
import pathlib

FAM = pathlib.Path(__file__).resolve().parent / "_proposal_families.json"
OUT = pathlib.Path(__file__).resolve().parent / "_proposal_rows.json"

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

items = {i["key"]: i for i in json.loads(SEED.read_text(encoding="utf-8"))["items"]}
fam = json.loads(FAM.read_text(encoding="utf-8"))

# ── THE WIDENING THAT IS WITHDRAWN ───────────────────────────────────────────────────────────
#
# `sub__rp_note_by_counterparty` was to claim the PRC related-party unsettled-balances table — the
# biggest single gap found, 285 rows. IT IS WITHDRAWN, and the reason is a property of the table
# rather than of the pattern.
#
# Measured: the 296 rows inside that note across the corpus are 150 distinct COUNTERPARTY NAMES —
# 中国电子科技集团公司第十三研究所, 成都西科微波通讯有限公司, 中电科思仪科技股份有限公司. Not one row
# caption says receivable or payable. THE DIRECTION IS A COLUMN, not a row label, and this
# configuration layer selects ROWS.
#
# So claiming the note would put amounts due TO related parties into a line for amounts due FROM
# them, with no rule able to tell them apart. A 285-row gap is worth less than a payable published
# as a receivable, and closing it needs column-aware selection, which does not exist here.
WITHDRAWN = {
    "sub__rp_note_by_counterparty":
        "The 296 rows in that note are 150 distinct COUNTERPARTY NAMES; receivable versus payable "
        "is a COLUMN and this layer selects ROWS. No row rule can discriminate, so the note would "
        "feed a receivable line with payables. Needs column-aware selection, which does not exist."
}

fam["kept"] = [p for p in fam["kept"] if p["key"] not in WITHDRAWN]
fam["withdrawn"] = [{"key": k, "why": v} for k, v in WITHDRAWN.items()]
FAM.write_text(json.dumps(fam, ensure_ascii=False, indent=1), encoding="utf-8")

P: list[dict] = []


def add(key: str, field: str, whats_wrong: str, additions: list[str], evidence: str) -> None:
    cur = list(((items[key].get("note_source") or {}).get(field)) or [])
    P.append({"key": key, "field": f"note_source.{field}",
              "whats_wrong": whats_wrong,
              "proposed": cur + [a for a in additions if a not in cur],
              "add": additions, "evidence": evidence})


# ── REVENUE — the compound note bundles revenue with other income, so the ROW must separate them ─
add("sub__revenue_note_principal_revenue", "row_caption_any",
    "The widened gate admits 'TURNOVER AND OTHER REVENUE AND GAINS' — a note that bundles revenue "
    "WITH other income and gains. The heading cannot discriminate, so the row must: the note's own "
    "total of revenue is what this part wants, not the bundled total.",
    [r"^\s*total\s+turnover\b",
     r"total\s+turnover\s+from\s+contracts\s+with\s+customers",
     r"revenue\s+from\s+contracts\s+with\s+customers\s*(?:總計|总计)?\s*$",
     r"營業額總計|营业额总计|收入合計|收入合计"],
    "Inside the admitted notes: 'Total turnover' (7 filings), 'Total turnover from contracts with "
    "customers' (6), 'Total 總計' (5).")

add("sub__revenue_note_principal_revenue", "row_caption_none",
    "Nothing vetoes the OTHER-income rows the bundled note prints beside the revenue rows, and "
    "those are exactly what must not be summed into the top line.",
    [r"from\s+other\s+source",
     r"\brental\s+income\b|租金收入",
     r"\bother\s+income\b|其他收入(?!及)",
     r"\bgains?\s+on\b|\bdividend\s+income\b|\binterest\s+income\b"],
    "Inside the admitted notes: 'Turnover from other source — rental income' (6 filings) sits "
    "beside the revenue rows, and the heading itself is 'TURNOVER AND OTHER REVENUE AND GAINS'.")

# ── RIGHT-OF-USE — the note is a MOVEMENT schedule, so the charge row is the only one wanted ────
add("sub__prepaid_lease_depreciation", "row_caption_any",
    "The widened gate admits the right-of-use / 长期待摊费用 schedule, which is a MOVEMENT table: "
    "opening balance, additions, decreases, closing balance. This part wants the CHARGE for the "
    "period, and taking a balance row instead would publish the asset as if it were the expense.",
    [r"depreciation\s+charge",
     r"（1）計提|（1）计提|本期計提|本期计提",
     r"本期折舊|本期折旧|本期攤銷|本期摊销",
     r"charge\s+for\s+the\s+(?:year|period)"],
    "Inside the admitted notes: '（1）计提' (4 filings), 'Depreciation charge' (2), against the "
    "balance rows '1.期初余额' (8), '4.期末余额' (10), '2.本期增加金额' (11), '3.本期减少金额' (10).")

# ── EQUITY METHOD — the note carries the ASSOCIATES' OWN financial statements ────────────────
add("sub__ltp_included_equity_method_investments", "row_caption_any",
    "THE WIDENING IS ONLY SAFE WITH THIS. `长期股权投资` and the interests-in-associates notes carry "
    "the ASSOCIATES' OWN FINANCIAL STATEMENTS — measured, the 507 rows inside them include 'Current "
    "assets' (8 filings), 'Current liabilities' (7), 'Revenue' (4), 'Net assets' (4). Summing those "
    "into a financial-asset deduction would be wrong by orders of magnitude. What this part wants "
    "is the CARRYING AMOUNT of the investment and nothing else.",
    [r"carrying\s+amount\s+of\s+the\s+investments?",
     r"share\s+of\s+net\s+assets",
     r"長期股權投資合計|长期股权投资合计|對子公司投資|对子公司投资",
     r"帳面(?:價值|金額)|账面(?:价值|金额)"],
    "Inside the admitted notes: 'Carrying amount of the investment' (4 filings), 'Share of net "
    "assets' (5), '对子公司投资' (6) — against the associates' own statement lines listed above.")

add("sub__ltp_included_equity_method_investments", "row_caption_none",
    "The associates' own statement lines must be vetoed explicitly, not merely left unmatched: a "
    "future widening of the row patterns above would otherwise reach them.",
    [r"^\s*(?:current|non.current)\s+(?:assets?|liabilit(?:y|ies))\s*$",
     r"^\s*revenue\s*$|^\s*net\s+assets\s*$|^\s*cash\s+and\s+cash\s+equivalents\s*$",
     r"^\s*(?:profit|loss)\s+for\s+the\s+(?:year|period)\s*$"],
    "Measured inside the admitted notes: 'Current assets', 'Current liabilities', 'Non-current "
    "assets', 'Non-current liabilities', 'Revenue', 'Net assets', 'Cash and cash equivalents' are "
    "all present as row captions and all belong to the ASSOCIATE, not to the group's investment.")

OUT.write_text(json.dumps({"kept": P}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"withdrew {len(WITHDRAWN)} gate widening(s): {list(WITHDRAWN)}")
print(f"{len(P)} row proposal(s) -> {OUT.name}")
for p in P:
    print(f"   {p['key']:46s} {p['field'].replace('note_source.',''):17s} +{len(p['add'])}")
