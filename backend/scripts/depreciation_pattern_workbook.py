#!/usr/bin/env python
"""The depreciation configuration, laid out as the WORKED EXAMPLE for the other focus concepts.

WHY THIS SHAPE. Two of the eight focus concepts are fully configured and, since
`stages/note_sourced.py` and the cascade evaluation landed, fully working. The other six have a
parent and nothing else. So the question "how do I configure those six" has an answer already in
the repository, and this prints it in the form a reviewer can direct work from.

THE STRUCTURE THE 13 SHIPPED SUB-LINE ITEMS ACTUALLY USE, which is smaller than it looks:

  * ONE ROW VOCABULARY per concept family, authored once and shared by every child. All 13 carry
    the SAME 25 counting patterns and the SAME 33 vetoes — "what does a depreciation row look
    like, in English, Traditional and Simplified Chinese" is a property of the SUBJECT, not of the
    note it is read from.
  * ONE NOTE TITLE per child. 12 distinct titles across the 12 children of the operating-expense
    parent: the R&D note, the G&A note, the PP&E note, the cash-flow reconciliation, and so on.
    This is the only field that differs between them.
  * ONE CASCADE on the parent, in precedence order, saying how those notes combine — including
    optional terms, signed deductions, and rungs that only apply when an earlier one found nothing.

That distinction matters for the other six: the bulk of the authoring (58 patterns) is written ONCE
per concept, not once per note. A first attempt at these six wrote a bespoke pattern set per child
and produced 25 double-counting defects, most of them two children claiming the same note and then
being summed.

Run:  python scripts/depreciation_pattern_workbook.py [-o OUT.xlsx]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

PARENTS = ["is_pl__deprec_and_impairment_oper_exp", "is_pl__deprec_and_impairment_cos"]

# The six that have a parent and no children, each with the spec in the repository that defines it
# and what is therefore still missing. The specs are the five documents that match the five removed
# derivation services one-for-one.
TODO: list[dict] = [
    {"key": "bs_ca__secur_and_other_fincl_assets_cp",
     "spec": "docs/HKEX_Securities_Other_Financial_Assets_Extraction_Logic_Clean.md §5",
     "arithmetic": "Find_1_CP - Find_2_CP - Find_3_CP, where Find_1 is the SUM of unique note "
                   "TOTALS across the identified current-asset notes, Find_2 the deduction items "
                   "and Find_3 the level-3 fair-value total.",
     "note": "Reads note TOTALS, not component rows, and SUBTRACTS two things. Needs a cascade "
             "with signed adjustment terms; sub-items alone cannot express it."},
    {"key": "bs_nca__secur_and_other_fincl_assets_ltp",
     "spec": "docs/HKEX_Securities_Other_Financial_Assets_Extraction_Logic_Clean.md §6",
     "arithmetic": "Find_1_LTP - Find_2_LTP, plus a carry-forward from the CP calculation.",
     "note": "Depends on the CP concept's result, so the two must be evaluated in order. The "
             "evaluator already walks dependencies (`Registry.order`)."},
    {"key": "notes__contingent_liabilities",
     "spec": "docs/PRC_Contingent_Liabilities_Extraction_Logic_Revised.md",
     "arithmetic": "Per §6.4 the exposure is reached through several note headings and is not a "
                   "sum of them.",
     "note": "BLOCKED BY CONFIGURATION, not by code: `note_use` is `evidence_only`, inherited "
             "from the `notes` section default, so a note may corroborate this line and never "
             "supply it. The doc also records that publishing onto this row was deliberately "
             "removed from the contingent-liabilities stage. One control decides it."},
    {"key": "bs_nca__due_from_related_parties_ltp",
     "spec": "docs/PRC_Related_Party_and_Other_Receivables_Extraction_Logic.md",
     "arithmetic": "The same non-current related-party balance is printed by several notes from "
                   "different angles.",
     "note": "Almost certainly `alternatives` rather than `sum` — the counterparty-class notes "
             "restate one balance. Summing them would multiply it."},
    {"key": "bs_ca__other_receivables_cp",
     "spec": "docs/PRC_Related_Party_and_Other_Receivables_Extraction_Logic.md",
     "arithmetic": "By-nature components, net of the loss allowance.",
     "note": "The allowance is a NEGATIVE row and belongs in the arithmetic as an adjustment, "
             "not in the veto list. Locked out of caption matching today "
             "(`alias_matching: disabled`), which is coherent once it is note-sourced."},
    {"key": "is_pl__sales_revenues",
     "spec": "docs/PRC_Sales_Revenues_Extraction_Logic_Simplified.md",
     "arithmetic": "The disaggregation note's rows sum to one revenue figure.",
     "note": "One axis per child, NEVER several in one item: a child mixing by-channel and "
             "by-region captions double-counts the same revenue. Face-printed revenue outranks "
             "any note-derived figure, which the stage already enforces."},
]


def build(out: pathlib.Path) -> int:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    items = {i["key"]: i for i in raw["items"]}

    HEAD = PatternFill("solid", fgColor="1F3864")
    HEAD_F = Font(color="FFFFFF", bold=True, size=10)
    PARENT_FILL = PatternFill("solid", fgColor="DDEBF7")
    TODO_FILL = PatternFill("solid", fgColor="FFF2CC")
    YOURS = PatternFill("solid", fgColor="FFFDE7")
    THIN = Side(style="thin", color="D0D0D0")
    BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    TOP = Alignment(vertical="top", wrap_text=True)
    MONO = Font(name="Consolas", size=9)

    wb = Workbook()

    def sheet(title, cols):
        ws = wb.create_sheet(title) if wb.sheetnames != ["Sheet"] else wb.active
        ws.title = title
        ws.append([c[0] for c in cols])
        for n, (_h, w) in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(n)].width = w
            c = ws.cell(row=1, column=n)
            c.fill, c.font, c.border = HEAD, HEAD_F, BOX
            c.alignment = Alignment(vertical="center", wrap_text=True, horizontal="center")
        ws.row_dimensions[1].height = 32
        ws.freeze_panes = "B2"
        return ws

    def row(ws, values, *, fill=None, mono_cols=()):
        ws.append(values)
        r = ws.max_row
        for c in range(1, len(values) + 1):
            cell = ws.cell(row=r, column=c)
            cell.border, cell.alignment = BOX, TOP
            if fill:
                cell.fill = fill
            if c in mono_cols:
                cell.font = MONO
        return r

    # ── 1. the 15 line items ──────────────────────────────────────────────────────────────────
    ws = sheet("1. Line items", [
        ("#", 5), ("Key", 42), ("Label", 34), ("Role", 12), ("Parent", 38),
        ("Which note it reads", 52), ("Title pats", 9), ("Count pats", 10), ("Veto pats", 9),
        ("in_output", 9), ("YOUR NOTES", 34)])
    n = 0
    for pk in PARENTS:
        p = items[pk]
        n += 1
        row(ws, [n, pk, p["label"], "PARENT", "", "(not read from a note — it is computed by its "
                 "cascade from the children below)", "", "", "",
                 str(p.get("in_output", True)), ""], fill=PARENT_FILL)
        kids = sorted([i for i in raw["items"] if i.get("parent") == pk],
                      key=lambda x: x.get("order", 0))
        for k in kids:
            ns = k.get("note_source") or {}
            titles = ns.get("note_title_any") or []
            n += 1
            r = row(ws, [n, k["key"], k["label"], f"child {k.get('order',0)}", pk,
                         (titles[0] if titles else "(none)"),
                         len(titles), len(ns.get("row_caption_any") or []),
                         len(ns.get("row_caption_none") or []),
                         str(k.get("in_output", True)), ""], mono_cols=(6,))
            ws.cell(row=r, column=11).fill = YOURS

    # ── 2. the cascade ────────────────────────────────────────────────────────────────────────
    cs = sheet("2. Cascade", [
        ("Parent", 40), ("Rung", 9), ("Order", 7), ("Terms — how the figure is computed", 62),
        ("What it means", 62), ("YOUR NOTES", 34)])
    for pk in PARENTS:
        p = items[pk]
        for order, rung in enumerate(p.get("cascade") or (), 1):
            terms = []
            for t in rung["terms"]:
                sign = "− " if t.get("sign", 1) < 0 else ("+ " if terms else "")
                role = t.get("role", "required")
                terms.append(f"{sign}{t['ref']}  [{role}]")
            r = row(cs, [pk if order == 1 else "", rung["id"], order,
                         "\n".join(terms), rung.get("note", ""), ""], mono_cols=(4,))
            cs.cell(row=r, column=6).fill = YOURS

    # ── 3. the shared row vocabulary ──────────────────────────────────────────────────────────
    subs = [i for i in raw["items"] if i.get("namespace") == "internal"]
    shared_any = subs[0]["note_source"]["row_caption_any"]
    shared_none = subs[0]["note_source"]["row_caption_none"]
    vs = sheet("3. Row vocabulary (shared)", [
        ("#", 5), ("Kind", 24), ("Pattern", 66), ("YOUR NOTES", 40)])
    row(vs, ["", "", f"ALL {len(subs)} children share these lists verbatim — the row vocabulary is "
             f"a property of the SUBJECT (what a depreciation row looks like), not of the note it "
             f"is read from. Authored once, reused everywhere.", ""], fill=PARENT_FILL)
    for i, pat in enumerate(shared_any, 1):
        r = row(vs, [i, "COUNTS towards the figure", pat, ""], mono_cols=(3,))
        vs.cell(row=r, column=4).fill = YOURS
    for i, pat in enumerate(shared_none, 1):
        r = row(vs, [i, "VETO — never counts", pat, ""], mono_cols=(3,))
        vs.cell(row=r, column=4).fill = YOURS

    # ── 4. one title per note ─────────────────────────────────────────────────────────────────
    ts = sheet("4. Note titles", [
        ("Order", 7), ("Child key", 42), ("Which note", 30), ("Title pattern", 78),
        ("YOUR NOTES", 34)])
    for k in sorted(subs, key=lambda x: x.get("order", 0)):
        ns = k.get("note_source") or {}
        t = (ns.get("note_title_any") or [""])[0]
        r = row(ts, [k.get("order", 0), k["key"], k["label"].split("—")[0].strip(), t, ""],
                mono_cols=(4,))
        ts.cell(row=r, column=5).fill = YOURS

    # ── 5. what the other six need ────────────────────────────────────────────────────────────
    ns_ = sheet("5. The other six", [
        ("Concept", 42), ("Spec in this repo", 56), ("Arithmetic the spec defines", 60),
        ("What that implies", 60), ("children today", 12), ("YOUR DIRECTION", 40)])
    for t in TODO:
        kids = sum(1 for i in raw["items"] if i.get("parent") == t["key"])
        r = row(ns_, [t["key"], t["spec"], t["arithmetic"], t["note"], kids, ""], fill=TODO_FILL)
        ns_.cell(row=r, column=6).fill = YOURS

    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"wrote {out}")
    print(f"  1. Line items          : 2 parents + {len(subs)} children")
    print(f"  2. Cascade             : "
          f"{sum(len(items[p].get('cascade') or []) for p in PARENTS)} rungs")
    print(f"  3. Row vocabulary      : {len(shared_any)} counting + {len(shared_none)} veto, "
          f"shared by all {len(subs)}")
    print(f"  4. Note titles         : {len(subs)} (one per child)")
    print(f"  5. The other six       : {len(TODO)} concepts awaiting the same treatment")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="depreciation_line_items.xlsx")
    return build(pathlib.Path(ap.parse_args().out).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
