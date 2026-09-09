#!/usr/bin/env python
"""Every configuration option a line item offers, what it FUNCTIONALLY does, and whether it earns
its place on the screen — as an Excel workbook for review.

WHY THIS EXISTS. The line-item screen offers 63 controls. Deciding which to remove needs two things
the screen itself cannot show: what each control actually does in plain words, and whether any
author has ever had a reason to touch it. The second is a measurement over the shipped set, and it
is what turns a taste argument into an evidence one:

  * A control NO item declares is a decision nobody has needed to make.
  * A control declared on hundreds of items with ONE distinct value is a constant wearing a control:
    it is a set-level policy that has been copied onto every row.
  * A control declared on a handful of items is real but rare, and belongs behind "advanced" rather
    than on the main form.

Two name mismatches are handled explicitly rather than measured wrong, because both would otherwise
report a live control as dead:

  * `sign_expectation` is the WIRE name for the model's `sign_convention` (462 of 475 items). The
    rename is deliberate — `sign_convention` on the wire is taken by the Template screen's legacy
    3-token spelling — so a naive probe of the JSON finds nothing and the most-used sign control in
    the file looks unused.
  * `in_output` is declared by no item and is still a real decision: it defaults to True and decides
    whether a line is delivered at all, so "0 declared" means "everything relies on the default",
    not "nobody cares".

Run:  python scripts/line_item_options_workbook.py [-o OUT.xlsx]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

# Field -> the group heading it sits under on the screen, in screen order. Extracted from
# `frontend/src/screens/LineItems.tsx` rather than restated, so a control that moves group is not
# silently described under the old one.
GROUPS: list[tuple[str, list[str]]] = [
    ("What is this line, in words?",
     ["label", "description", "definition", "prompt", "include_criteria", "exclude_criteria",
      "confusable_with", "section_disambiguation"]),
    ("Which printed captions are this line?",
     ["aliases", "pattern", "regex_hints", "keyword_hints", "exclude_hints", "alias_matching"]),
    ("Where may it be claimed from?",
     ["inherits", "statement", "section_scope", "scopes", "side", "allow_contra", "match_priority",
      "extraction_mode", "face_only", "note_use", "note_source", "note_source.note_title_any",
      "note_source.row_caption_any", "note_source.row_caption_none",
      "note_source.caption_normalization"]),
    ("How does it sit among the other lines?",
     ["type", "in_output", "parent", "rollup", "order", "namespace", "value_scope",
      "is_gross_parent", "children_if_decomposed", "sole_component_of", "expected_components",
      "never_sweep", "residual_policy", "residual_policy.framework",
      "residual_policy.section_scope", "residual_policy.population",
      "residual_policy.cross_section", "residual_policy.notes_as_source", "residual_policy.plug",
      "residual_policy.itemise"]),
    ("What kind of figure is it?",
     ["output_structure", "temporality", "unit_of_account", "sign_expectation",
      "sign_rule.convention",
      "sign_rule.flip_if_label_matches", "analyst_bucket"]),
    ("How is its value assembled?", ["terms", "cascade", "implemented_by"]),
    ("Notes for the next reader",
     ["decomposition_rule", "others_rule", "derivation", "aggregation_note", "template_note",
      "notes_as_source_rationale"]),
]

SCREEN = (pathlib.Path(__file__).resolve().parent.parent.parent
          / "frontend" / "src" / "screens" / "LineItems.tsx")


def _screen_set(name: str) -> set[str]:
    """Read `SIMPLE_FIELDS` / `RETIRED_FIELDS` OUT OF THE SCREEN rather than restating them here.

    A second copy of these lists is how the workbook comes to describe a form that no longer
    exists — it would keep reporting a control as advanced after it was promoted, or as live after
    it was retired, and the numbers in the Summary would be quietly wrong. The screen is the only
    authority on what it renders, so it is the one that is read.
    """
    import re
    src = SCREEN.read_text(encoding="utf-8")
    m = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", src, re.S)
    if not m:
        raise SystemExit(f"{name} not found in {SCREEN} — the workbook cannot describe the form.")
    # Only the quoted field names; the block carries prose and per-entry measurements too.
    return set(re.findall(r'"([a-z_.]+)"', m.group(1)))


SIMPLE = _screen_set("SIMPLE_FIELDS")
RETIRED = _screen_set("RETIRED_FIELDS")

# WIRE NAME -> the field to actually measure in the shipped JSON. See the module docstring.
PROBE = {"sign_expectation": "sign_convention"}

# Controls whose "0 declared" is a DEFAULT everything relies on, not an absence of interest.
DEFAULTED = {"in_output": "defaults to true; provisioning sets it and it decides delivery"}

# One sentence each: what the option DOES, in the terms of the extraction it governs. Written to be
# read by someone deciding whether they need the control, so it says what breaks without it rather
# than restating the field name.
MEANING: dict[str, str] = {
    "output_structure": "What the line outputs: a number (the default, and what everything that totals assumes), a short phrase lifted off the page, or prose the model writes from this line's prompt.",
    "prompt": "An extra instruction for THIS line, added to the master prompt inside its own candidate entry. Required when the line outputs prose, because prose is written from it.",
    "label": "The human name for this line — what shows on the screen, in review and in the export.",
    "description": "A free note about the line for people. The model never reads it; `definition` is the one it reads.",
    "definition": "The sentence the model reasons over to decide whether a caption means this line. The single most load-bearing field.",
    "include_criteria": "What this line DOES cover — the wordings and cases that should land here.",
    "exclude_criteria": "What this line must NOT absorb, even when the caption looks close. Stops a near-neighbour being swallowed.",
    "confusable_with": "Other lines this is genuinely mistakable for, so the model is told the pair apart instead of guessing.",
    "section_disambiguation": "Which of two look-alike captions this is, decided by the printed section it appeared under.",
    "aliases": "The actual printed captions that mean this line, per language. The primary way a row is recognised.",
    "pattern": "A single regular expression for the caption. Superseded by `regex_hints`, which is the list form.",
    "regex_hints": "Regular expressions for caption shapes an alias list cannot enumerate (numbering, punctuation, wrapping).",
    "keyword_hints": "Words whose presence makes this line more likely — a nudge, not a match on its own.",
    "exclude_hints": "Words whose presence makes this line wrong, blocking a match an alias would otherwise win.",
    "alias_matching": "Whether captions may be matched to this line at all. Disabled locks the line so only a calculation can fill it.",
    "inherits": "Which line this one sits under. Gives it its section gate and its place in the hierarchy.",
    "statement": "Which statement the line belongs to, so a P&L caption cannot resolve to a balance-sheet line.",
    "section_scope": "Which printed sections the line may be claimed from — the narrower gate inside the statement.",
    "scopes": "Where to search for the figure, in order. In practice the section already decides this.",
    "side": "Whether the line is a debit or a credit. Read off the printed banner in practice.",
    "allow_contra": "Whether a contra (negative-of-its-nature) figure may be filed here.",
    "match_priority": "Who wins when two lines both fit a caption. Higher takes it.",
    "extraction_mode": "Whether the figure is read off the page, calculated, or either — the line's basic source of value.",
    "face_only": "Whether a note may supply the figure or only the face of the statement. Arrives from the section.",
    "note_use": "How a note may be used for this line — as corroboration of a face figure, or as a breakdown that can be summed.",
    "note_source": "Which note to read the figure from, identified by its title and row captions.",
    "note_source.note_title_any": "Note titles that identify the right note for this line.",
    "note_source.row_caption_any": "Row captions inside that note which carry this line's figure.",
    "note_source.row_caption_none": "Row captions inside that note which must be ignored.",
    "note_source.caption_normalization": "How note captions are cleaned before they are compared.",
    "type": "Whether the line is extracted from the document or produced by a calculation.",
    "in_output": "Whether the line is delivered in the export at all, or exists only to support other lines.",
    "parent": "The line this one rolls up into for reporting purposes.",
    "rollup": "How this line's figure contributes to its parent's total.",
    "order": "Display position. The template already fixes the order it is delivered in.",
    "namespace": "Whether the line came from the uploaded template (undeletable) or was added here.",
    "value_scope": "Whether the figure is a standalone leaf or a total that contains other lines.",
    "is_gross_parent": "Marks a line that CONTAINS others, so it is never loaded alongside the children it already includes.",
    "children_if_decomposed": "The lines this one breaks into, used to refuse double-counting a parent and its parts.",
    "sole_component_of": "Declares this line the only component of another, so an undifferentiated figure may be assigned to it.",
    "expected_components": "What a section's leftover figure is expected to be made of, licensing a 'none of these' answer.",
    "never_sweep": "Captions that must never be swept into this line's residual.",
    "residual_policy": "How the unexplained remainder of a section is handled for this line.",
    "residual_policy.framework": "Which residual scheme applies.",
    "residual_policy.section_scope": "Which sections this line's residual is computed over.",
    "residual_policy.population": "Which rows count towards the residual.",
    "residual_policy.cross_section": "Whether the residual may draw from other sections.",
    "residual_policy.notes_as_source": "Whether notes may feed the residual.",
    "residual_policy.plug": "Whether an unexplained difference may be parked on this line.",
    "residual_policy.itemise": "Whether the residual is broken out or left as one figure.",
    "temporality": "Whether the figure is a balance at a date or a flow over a period.",
    "unit_of_account": "Whether the figure is money, a count, a ratio or a duration.",
    "sign_expectation": "The sign this line should carry, so a figure arriving the wrong way round is flagged in review.",
    "sign_rule.convention": "An alternative sign scheme for the line. `sign_expectation` is the one in use.",
    "sign_rule.flip_if_label_matches": "Flip the sign when the caption matches a given wording.",
    "analyst_bucket": "A grouping label for analyst-facing presentation.",
    "terms": "The named inputs a calculated line is built from.",
    "cascade": "Ordered fallbacks to try when the preferred source is absent.",
    "implemented_by": "Names the code that fills this line, where configuration cannot express it.",
    "decomposition_rule": "Whether the line may be split without printed evidence for the split.",
    "others_rule": "How an 'Others'/'Miscellaneous' caption is treated for this line.",
    "derivation": "How the figure is computed when the face does not print it.",
    "aggregation_note": "The rule for adding several figures into this line.",
    "template_note": "A note carried over from the source template about this line.",
    "notes_as_source_rationale": "Why notes are or are not allowed as a source for this line.",
}


def _get(item: dict, path: str):
    cur = item
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _empty(v) -> bool:
    return v in (None, "", [], {}, False)


def measure(items: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for _group, fields in GROUPS:
        for f in fields:
            probe = PROBE.get(f, f)
            values = [_get(i, probe) for i in items]
            used = [v for v in values if not _empty(v)]

            def _norm(v):
                return (json.dumps(v, sort_keys=True, ensure_ascii=False)
                        if isinstance(v, (list, dict)) else v)

            out[f] = {"used": len(used), "distinct": len({_norm(v) for v in used}),
                      "sample": json.dumps(used[0], ensure_ascii=False)[:120] if used else ""}
    return out


def verdict(field: str, m: dict, total: int, all_fields: set[str]) -> tuple[str, str, str]:
    """(evidence, recommendation, why) — driven by the measurement, not by opinion."""
    used, distinct = m["used"], m["distinct"]
    if field in RETIRED:
        if used == 0:
            return (f"removed — 0 of {total} declared it",
                    "Already removed",
                    "Hidden because no author ever needed it. The stored value still works.")
        return (f"removed by decision — {used} of {total} declare it, {distinct} distinct",
                "Already removed",
                "Removed to shrink the form, NOT because it was unused. Values already authored "
                "stay in force and keep driving extraction; a new one cannot be set here.")
    # NESTED SUB-CONTROLS ARE ONE MECHANISM, NOT SEVEN. `residual_policy` is rendered as its own
    # control plus seven children, and `note_source` as its own plus four — and each set is declared
    # by the SAME 11 and 13 items. So the form spends eleven controls asking one question twice,
    # which is a large part of why the screen reads as too much. Collapse each to its parent and
    # edit the detail there.
    if "." in field and field.split(".")[0] in all_fields:
        parent = field.split(".")[0]
        return (f"{used} of {total} — the same items that declare `{parent}`",
                f"Merge into `{parent}`",
                f"A detail of one mechanism, not a separate decision. `{parent}` and its children "
                f"are declared by the same items, so this should be one control.")
    if field in DEFAULTED:
        return (f"0 of {total} declare it, but {DEFAULTED[field]}",
                "Keep — simple",
                "Everything relies on the default, so nobody has had to set it. Still a real decision.")
    if used == 0:
        return (f"0 of {total} declare it",
                "Remove",
                "No author has ever needed this. Offering it implies a decision that has never arisen.")
    if distinct == 1 and used > 100:
        return (f"{used} of {total}, but only ONE distinct value",
                "Remove from the item — set once for the whole set",
                "A constant copied onto every row. It is a set-level policy, not a per-line choice.")
    if distinct <= 3 and used > 100:
        return (f"{used} of {total}, only {distinct} distinct values",
                "Remove from the item — set once, override rarely",
                "Near-constant. State the prevailing value at set level and let the few exceptions override.")
    if used <= 35:
        return (f"only {used} of {total} declare it",
                "Keep — advanced only",
                "A real mechanism, but rare. It belongs behind the advanced toggle, not on the main form.")
    return (f"{used} of {total}, {distinct} distinct values",
            "Keep — simple" if field in SIMPLE else "Keep — advanced",
            "Genuinely varies line by line; this is a real per-line decision.")


def build(out_path: pathlib.Path) -> int:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    items = json.loads(SEED.read_text(encoding="utf-8"))["items"]
    total = len(items)
    stats = measure(items)

    HEAD = PatternFill("solid", fgColor="1F3864")
    HEAD_F = Font(color="FFFFFF", bold=True, size=10)
    FILL = {"Remove": PatternFill("solid", fgColor="FCE4E4"),
            "Remove from the item — set once for the whole set": PatternFill("solid", fgColor="FFF0D9"),
            "Remove from the item — set once, override rarely": PatternFill("solid", fgColor="FFF0D9"),
            "Keep — advanced only": PatternFill("solid", fgColor="EDF3FA"),
            "Keep — advanced": PatternFill("solid", fgColor="E7F2E7"),
            "Keep — simple": PatternFill("solid", fgColor="D9EAD3"),
            "Already removed": PatternFill("solid", fgColor="EFEFEF")}
    MERGE_FILL = PatternFill("solid", fgColor="FFF0D9")
    THIN = Side(style="thin", color="D0D0D0")
    BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    TOP = Alignment(vertical="top", wrap_text=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Line item options"
    cols = [("#", 5), ("Section of the form", 30), ("Option", 30), ("Shown", 11),
            ("What it does (functional meaning)", 62), ("Items using it", 11),
            ("Distinct values", 11), ("Evidence", 34), ("Recommendation", 30),
            ("Why", 52), ("YOUR DECISION", 15), ("YOUR NOTES", 34)]
    ws.append([c[0] for c in cols])
    for n, (_h, w) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(n)].width = w
        cell = ws.cell(row=1, column=n)
        cell.fill, cell.font, cell.border = HEAD, HEAD_F, BOX
        cell.alignment = Alignment(vertical="center", wrap_text=True, horizontal="center")
    ws.row_dimensions[1].height = 34

    all_fields = {f for _g, fs in GROUPS for f in fs}
    rows_out = []
    n = 0
    for group, fields in GROUPS:
        for f in fields:
            n += 1
            m = stats[f]
            ev, rec, why = verdict(f, m, total, all_fields)
            shown = ("Retired" if f in RETIRED else "Simple" if f in SIMPLE else "Advanced")
            rows_out.append([n, group, f, shown, MEANING.get(f, ""), m["used"], m["distinct"],
                             ev, rec, why, "", ""])
    # Cut candidates first — the sheet exists to support a cut, so the work is at the top. Screen
    # order is preserved inside each band by the stable index in column 1.
    RANK = {"Remove": 0, "Remove from the item — set once for the whole set": 1,
            "Remove from the item — set once, override rarely": 1, "Already removed": 3,
            "Keep — advanced only": 4, "Keep — advanced": 5, "Keep — simple": 6}
    for _r in rows_out:
        RANK.setdefault(_r[8], 2 if _r[8].startswith("Merge into") else 9)
    rows_out.sort(key=lambda r: (RANK.get(r[8], 9), r[0]))

    for r in rows_out:
        ws.append(r)
        row = ws.max_row
        for c in range(1, len(cols) + 1):
            cell = ws.cell(row=row, column=c)
            cell.border, cell.alignment = BOX, TOP
            if c in (1, 4, 6, 7):
                cell.alignment = Alignment(vertical="top", horizontal="center", wrap_text=True)
        ws.cell(row=row, column=9).fill = (
            MERGE_FILL if r[8].startswith("Merge into") else FILL.get(r[8], PatternFill()))
        ws.cell(row=row, column=9).font = Font(bold=True, size=10)
        for c in (11, 12):
            ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor="FFFDE7")

    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:L{ws.max_row}"
    dv = DataValidation(type="list", formula1='"Keep,Remove,Merge,Not sure"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"K2:K{ws.max_row}")

    # ---- Summary -----------------------------------------------------------------------------
    s = wb.create_sheet("Summary")
    s.column_dimensions["A"].width = 52
    s.column_dimensions["B"].width = 12
    s.column_dimensions["C"].width = 74
    bands: dict[str, int] = {}
    for r in rows_out:
        bands[r[8]] = bands.get(r[8], 0) + 1

    def put(a, b="", c="", bold=False, fill=None):
        s.append([a, b, c])
        row = s.max_row
        for col in range(1, 4):
            s.cell(row=row, column=col).alignment = TOP
            if bold:
                s.cell(row=row, column=col).font = Font(bold=True)
            if fill:
                s.cell(row=row, column=col).fill = fill

    put("Line item configuration — what the screen offers, and what it earns", bold=True)
    put("")
    put(f"Controls the screen renders", len(rows_out),
        "Across 7 groups on the line-item form. 9 of them show in simple mode.")
    put(f"Line items measured against", total, "The in-force set (462 template + 13 internal).")
    put("")
    put("Recommendation", "Count", "What the evidence says", bold=True)
    merged = sorted(b for b in bands if b.startswith("Merge into"))
    for band in (["Remove", "Remove from the item — set once for the whole set",
                  "Remove from the item — set once, override rarely"] + merged
                 + ["Already removed", "Keep — advanced only", "Keep — advanced",
                    "Keep — simple"]):
        if band in bands:
            put(band, bands[band], {
                "Remove": "No item declares it. A decision nobody has needed to make.",
                "Remove from the item — set once for the whole set":
                    "Declared on hundreds of items with a single value — a constant wearing a control.",
                "Remove from the item — set once, override rarely":
                    "Two or three values across hundreds of items. Set the prevailing one once.",
                "Already removed": "Hidden from the form previously, on this same evidence.",
                "Keep — advanced only": "A real mechanism used by a handful of items.",
                "Keep — advanced": "Varies per line, but not needed for everyday authoring.",
                "Keep — simple": "The everyday controls. This is the form most authors should see.",
            }.get(band, "A detail of one mechanism rendered as its own control. Fold it into the "
                        "parent so the form asks the question once."),
            fill=FILL.get(band, MERGE_FILL if band.startswith("Merge into") else None))
    put("")
    gone = sum(v for k, v in bands.items() if k.startswith("Remove"))
    folded = sum(v for k, v in bands.items() if k.startswith("Merge into"))
    hidden = bands.get("Already removed", 0)
    visible = len(rows_out) - hidden
    put("Controls visible on the form today", visible,
        f"{len(rows_out)} rendered minus {hidden} already hidden.", bold=True)
    put("Removable outright", gone,
        "Dead controls, plus constants that belong at set level rather than on every line.",
        bold=True)
    put("Foldable into a parent control", folded,
        "residual_policy and note_source are each rendered as a parent plus children, declared by "
        "the same handful of items.", bold=True)
    put("Total reduction available", gone + folded,
        f"{gone + folded} of {visible} visible controls = "
        f"{100*(gone+folded)//visible}% — leaving {visible - gone - folded} on the form.",
        bold=True, fill=PatternFill("solid", fgColor="D9EAD3"))
    put("")
    put("Applied on review", bold=True)
    put("note_source promoted to the simple form", "4",
        "The switch plus its three pattern lists. The switch alone would reveal nothing when used, "
        "because the lists render only while it is on.")
    put("Removed by decision", "9",
        "match_priority, is_gross_parent, children_if_decomposed, expected_components, parent, "
        "rollup, cascade, implemented_by, decomposition_rule. Fields kept; controls gone.")
    put("Still one heading over one unused control", "terms",
        "\"How is its value assembled?\" now contains only `terms`, which 0 of 475 items declare. "
        "Its old justification (the Python derivations that would move into configuration) no "
        "longer holds — those services were removed. Retiring it closes the group.")
    put("")
    put("How each verdict was reached", bold=True)
    put("0 of 475 declare it", "", "Remove. Offering a control implies a decision that has never arisen.")
    put("Hundreds of items, 1 distinct value", "",
        "Remove from the item. It is a set-level policy that was copied onto every row.")
    put("Hundreds of items, 2-3 distinct values", "",
        "Remove from the item. State the prevailing value once; let the few exceptions override it.")
    put("<= 35 items", "", "Keep, advanced only. Real but rare; it should not crowd the main form.")
    put("Varies line by line", "", "Keep. This is a genuine per-line decision.")
    put("")
    put("Two name mismatches, handled explicitly", bold=True)
    put("sign_expectation", "462",
        "The wire name for the model's `sign_convention`. A naive probe of the JSON finds nothing "
        "and the most-used sign control looks dead. It is not.")
    put("in_output", "0",
        "Declared by no item yet a real decision: it defaults to true and decides whether a line is "
        "delivered. 0 means everything relies on the default.")
    put("notes_as_source_rationale", "0",
        "Genuinely unused. Not to be confused with `note_use_rationale` (394 items, one distinct "
        "value), which is a different field and is not rendered as a control at all.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    print(f"wrote {out_path}")
    print(f"  {len(rows_out)} controls rendered; {hidden} already hidden; {visible} visible today")
    print(f"  reduction available: {gone} removable + {folded} foldable = {gone + folded} "
          f"of {visible} visible ({100*(gone+folded)//visible}%)")
    for band, cnt in sorted(bands.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {cnt:3d}  {band}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="line_item_config_options.xlsx")
    args = ap.parse_args()
    return build(pathlib.Path(args.out).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
