"""The note-header hints for the remaining 7 families, authored against the 18-filing corpus.

Written as a script rather than typed as JSON so the regex escapes are Python's to interpret — a
`\\b` inside a shell heredoc becomes a literal backspace, which corrupted an earlier proposal.

EVERY VALUE IS PURELY ADDITIVE to what the shipped set already declares. That is deliberate: the
shipped patterns were authored against two filings and are RIGHT about those two, so the job is
reach, not replacement. `check_proposals.py` reports "strictly wider, nothing lost" when that holds
and says so when it does not.

NOTE-HEADER HINTS ONLY. Row hints for these families are a separate pass, because a row pattern can
only be judged inside the notes the gate claims — so the gate has to be settled first.
"""
from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "_proposal_families.json"

P: list[dict] = []


def add(key: str, whats_wrong: str, add_patterns: list[str], evidence: str,
        excluded: str = "") -> None:
    P.append({"key": key, "field": "note_source.note_title_any",
              "whats_wrong": whats_wrong, "add": add_patterns,
              "evidence": evidence, "must_not_claim": excluded})


# ── DUE FROM RELATED PARTIES — the single biggest gap in the exercise ─────────────────────────
#
# `、 应收、应付关联方等未结算项目情况` carries 285 ROWS and is claimed by NOTHING. It is the PRC
# related-party UNSETTLED BALANCES table — the primary disclosure of amounts due from and to related
# parties, and the one place an A-share filing states them. Its HK counterpart, `BALANCES WITH
# RELATED PARTIES 25. 應收╱應付關聯方款項`, is likewise unclaimed.
#
# THE TABLE HOLDS BOTH DIRECTIONS — receivable AND payable — which is why the note gate widening
# must land WITH row discrimination and not before it: claiming the note without a row rule that
# separates 应收 from 应付 would let a payable fill a receivable line. The row pass is next.
add("sub__rp_note_by_counterparty",
    "Claims nothing in the PRC filings. The related-party unsettled-balances table — 285 rows in "
    "11077098 — is the primary disclosure of amounts due from related parties and no part of this "
    "family reaches it. Its HK counterpart is unclaimed too.",
    [r"應收、應付關聯方等未結算項目情況|应收、应付关联方等未结算项目情况",
     r"應收.{0,8}應付關聯方.{0,10}未結算|应收.{0,8}应付关联方.{0,10}未结算",
     r"balances\s+with\s+related\s+part",
     r"應收.{0,4}應付關聯方款項|应收.{0,4}应付关联方款项"],
    "11077098 '、 应收、应付关联方等未结算项目情况' (285 rows) and '应收、应付关联方等未结算项目情况' "
    "(3 rows); a filing in the corpus heads the same disclosure 'BALANCES WITH RELATED PARTIES 25. "
    "應收╱應付關聯方款項' (8 rows).",
    "The table carries BOTH receivable and payable columns, so the row hints must separate 应收 "
    "from 应付 before this gate is widened — otherwise a payable can fill a receivable line.")

add("sub__rp_lt_receivables_note",
    "Reaches one filing. `长期应收款` — the PRC long-term receivables schedule — is unclaimed in "
    "the filings that print it.",
    [r"長期應收款|长期应收款"],
    "'、长期应收款' (4 rows) and '、 长期应收款' (3 rows) appear in the corpus unclaimed.",
    "GENERIC 长期应收款 IS NOT NECESSARILY RELATED-PARTY. Measured earlier in this work, a fuzzy "
    "tier bound 长期应收款 (203,287,842.45) onto this line on suncreate and the caption appears in "
    "neither the 23 English nor the 11 Chinese aliases — a probable mis-binding. Claiming the NOTE "
    "is safe because the row rule still has to find a related-party row inside it; claiming the "
    "FACE caption would not be.")

# ── REVENUE — the compound HK heading ────────────────────────────────────────────────────────
#
# HK filings do not head the note "REVENUE". They head it "REVENUE, OTHER INCOME AND GAINS" or
# "TURNOVER AND OTHER REVENUE AND GAINS" — revenue bundled with the other-income lines — and a
# pattern expecting revenue alone misses the note entirely, including its continuation fragments
# which carry most of the rows (82 of them in one filing).
add("sub__revenue_note_principal_revenue",
    "Misses the COMPOUND heading HK filings actually use. 'TURNOVER AND OTHER REVENUE AND GAINS' "
    "and 'REVENUE, OTHER INCOME AND GAINS' are the real headings, and their (CONTINUED) fragments "
    "carry most of the rows — 82 in one filing.",
    [r"turnover\s+and\s+other\s+revenue",
     r"revenue,?\s+other\s+income\s+and\s+gains?",
     r"revenue\s+and\s+other\s+income\s+and\s+gains?",
     r"收益[、，]?\s*其他收入及收益|收入[、，]?\s*其他收入及收",
     r"營業額及其他收入|营业额及其他收入",
     r"收入與成本|收入与成本"],
    "'TURNOVER AND OTHER REVENUE AND GAINS' (17 rows) and its '(CONTINUED)' (82 rows); "
    "'REVENUE, OTHER INCOME AND GAINS 6. 收益、其他收入及收益（續）' (21 rows); 'REVENUE, OTHER "
    "INCOME AND GAINS (Continued) 5. 收入、其他收入及收' (19 rows); 'REVENUE AND OTHER INCOME AND "
    "GAINS/ 7. 收益及其他收入及收益╱（虧損）淨' (16 rows); '、收入与成本' (36 rows).",
    "The row rule must still separate revenue from OTHER income inside the note — the heading "
    "bundles them, so the note gate alone cannot.")

# ── DEPRECIATION, OPERATING EXPENSE ─────────────────────────────────────────────────────────
#
# The line is Deprec AND IMPAIRMENT, so amortisation of the other amortised non-current assets
# belongs in it. Three classes are unclaimed in every filing that prints them.
add("sub__prepaid_lease_depreciation",
    "Reaches NOTHING in any of the 18 filings. The part is named for 'prepaid lease payments', "
    "which is the pre-IFRS-16 caption; every filing that adopted IFRS 16 renamed it RIGHT-OF-USE "
    "ASSETS, and the PRC analogue is 长期待摊费用. Neither is claimed.",
    [r"right.of.use\s+assets?",
     r"使用權資產|使用权资产",
     r"長期待攤費用|长期待摊费用",
     r"prepaid\s+lease|預付租賃|预付租赁|租賃土地|租赁土地"],
    "'RIGHT-OF-USE ASSETS' (6 rows) and its '(continued)' (4 rows); '长期待摊费用' at 七、28 "
    "(5 rows), 十七、28 (2 rows), 四、28 (3 rows), 十七、14 (2 rows).",
    "The POLICY notes of the same subject are titled by the policy question — '摊销方法', "
    "'摊销年限', '无形资产的初始计量', '长期资产减值' — and carry 0 rows, so they contribute nothing "
    "even where a pattern touches them. That is why no policy-marker word appears above.")

# ── FINANCIAL ASSETS, NON-CURRENT ───────────────────────────────────────────────────────────
add("sub__ltp_included_equity_method_investments",
    "The equity-method deduction cannot find its own note. `长期股权投资` — long-term equity "
    "investments, 68 rows — is the PRC schedule for exactly what this part deducts, and it is "
    "claimed by nothing.",
    [r"長期股權投資|长期股权投资",
     r"(?:interests?|investments?)\s+in\s+(?:associates?|joint\s+ventures?)",
     r"於聯營公司|于联营公司|於合營|于合营"],
    "'、长期股权投资' (68 rows) unclaimed in the corpus.",
    "")

add("sub__ltp_other_fincl_assets_note_total",
    "Reaches nothing. `其他非流动金融资产` is the PRC heading for exactly this part's subject and "
    "is unclaimed.",
    [r"其他非流動金融資產|其他非流动金融资产",
     r"other\s+non.current\s+financial\s+assets?"],
    "'其他非流动金融资产' (3 rows) unclaimed.",
    "FAIR-VALUE notes are NOT added — 'FAIR VALUE MEASUREMENTS' (85 rows), 'FAIR VALUE HIERARCHY "
    "OF FINANCIAL INSTRUMENTS' (52 rows), 'Fair value estimation' (66 rows). Those level the same "
    "assets by Level 1/2/3 and claiming them would restate the balance a second time. They belong "
    "only to `sub__fa_cp_level_3_total`, which asks specifically for the Level 3 figure.")

pathlib.Path(OUT).write_text(
    json.dumps({"kept": [{**p, "proposed": []} for p in P]}, ensure_ascii=False, indent=1),
    encoding="utf-8")
print(f"{len(P)} note-header proposals -> {OUT.name}")
for p in P:
    print(f"   {p['key']:50s} +{len(p['add'])} patterns")
