#!/usr/bin/env python
"""A BLANK WORKBOOK FOR AUTHORING A WHOLE LINE-ITEM SET OFFLINE — and the importer that publishes it.

WHY A WORKBOOK AND NOT THE SCREEN. The screen edits ONE line at a time against a stored version,
which is right for correcting a caption and wrong for authoring a set: a spread is hundreds of
lines, the decisions are comparative (which of these two claims that caption), and the person
making them wants a spreadsheet and no network. This produces the blank, hands it over, and reads
it back as a publishable set.

FIVE SHEETS, and the split is the schema's own shape rather than a convenience:

    Set          the set-level declarations — key, target template, locale, master prompt
    Sections     `section_defaults`: the GATE, authored once per section and claimed by `inherits`.
                 Nearly every line takes its statement and banners from here, which is why the
                 line sheet has an `inherits` column and not twelve gate columns.
    Line items   one row per line item. Only `key` is required by the schema.
    Cascade      a DERIVED line's ordered rungs, one row per TERM. A cascade cannot live in a cell:
                 it is a list of rungs each holding a list of signed terms, and flattening it into
                 one string is how a rung's order — which IS its priority — gets lost.
    Terms        a CALCULATED line's signed sum, one row per term. Same reason.
    Vocabulary   READ-ONLY. Every legal value for every closed field, generated from the schema's
                 own `Literal[...]` types so it cannot drift from what the loader accepts.

THE VOCABULARY SHEET IS THE POINT OF DOING THIS IN EXCEL AT ALL. Authoring against a closed
enumeration you cannot see means discovering it through a 422 on publish, one value at a time.

A LIST CELL HOLDS ONE VALUE PER LINE (alt+enter), never comma-separated — a regex may contain a
comma and a caption certainly can.

    python scripts/ontology_template.py -o ../_vocab/ontology_blank.xlsx
    python scripts/ontology_template.py --import filled.xlsx -o set.json
    # then: POST /api/v1/line-items  with {"definition": <set.json>}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import typing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# THE AUTHORABLE COLUMNS, and the order is the order the questions are asked in: what the line IS,
# then how its figure is obtained, then — for an extracted line only — how a caption reaches it.
# Deliberately NOT every field on the model: 23 of 54 are refused by the endpoint because no
# surface offers them (`routes/line_items._NOT_CONFIGURABLE`), and offering a column the publish
# will reject is the defect this workbook exists to avoid.
COLUMNS: list[tuple[str, str]] = [
    ("key", "REQUIRED and unique. The line's identity, referenced by cascade terms and by the "
            "output template. Lower_snake_case. Renaming one later silently empties any rung that "
            "names it."),
    ("label", "What the line is called on screen and in the export."),
    ("statement", "Which statement a caption must have been printed on for this line to claim it. "
                  "LEAVE BLANK for 'any' — an empty statement is what lets a note on one statement "
                  "feed a line on another, which is how a depreciation part in the balance-sheet "
                  "PP&E note feeds a profit-and-loss line."),
    ("type", "HOW THE FIGURE IS OBTAINED, and the only field that says so. `extracted` = read off "
             "a printed caption, and the ONLY type the model is ever asked about. `calculated` = "
             "summed from its components. `derived` = assembled by its cascade, and reached by "
             "nothing else: no caption, no alias, no semantic probe, never the model."),
    ("inherits", "The section this line belongs to — a key of the Sections sheet. Supplies the "
                 "statement and the banners the line may be found under. A line pointed at the "
                 "wrong section is usually not found at all, and correcting this is usually the fix."),
    ("parent", "For a PART of a derived line: the derived line it feeds. Leave blank otherwise."),
    ("namespace", "`template` for a line the output template publishes; `internal` for an "
                  "off-template part. All 77 shipped parts are `internal`."),
    ("in_output", "TRUE/FALSE — whether the line is delivered. Off-template parts are FALSE."),
    ("aliases", "CAPTIONS AS PRINTED, one per line. Matched after normalisation, so case and "
                "punctuation differences are already handled — do not write variants for those."),
    ("aliases_i18n_zh", "The same line's CHINESE captions, one per line. A single flat list cannot "
                        "hold both scripts: the matcher folds every locale into one index, and the "
                        "shipped set carries 473 distinct Chinese captions."),
    ("regex_hints", "REGEXES over the caption, one per line, for wordings an alias list cannot "
                    "enumerate. Matched against the caption as printed AND its normalised form."),
    ("keyword_hints", "One per line. EVERY word of a hint must appear in the caption — this is an "
                      "AND, not an OR, which is what makes it different from an alias."),
    ("exclude_hints", "REGEX VETOES over the caption. A caption matching one of these can never be "
                      "this line, however well it otherwise matched."),
    ("include_criteria", "Prose — what COUNTS as this line. Read by the description-matching tier "
                         "and by the model. Not patterns."),
    ("exclude_criteria", "Prose — what does NOT count, however close the wording."),
    ("definition", "Prose describing what the line MEANS. The most-read field in the set: the "
                   "description tier prefers it, and for an extracted line the model reasons over "
                   "it. Not a pattern."),
    ("prompt", "An extra instruction for THIS line, sent inside its own candidate entry. Only ever "
               "sent for an `extracted` line."),
    ("note_use", "Whether a note may be the SOURCE of this line's figure or only EVIDENCE for it. "
                 "`decomposition_allowed` lets a note fill it; `evidence_only` means a note may "
                 "corroborate a face amount and never supply one. READ OFF THE PARENT for a "
                 "derived line, so set it there too."),
    ("note_title_any", "NOTE-HEADER HINT — regexes over a note's HEADING. Which note this line's "
                       "figure lives in. A heading names the CONTAINER: 'ADMINISTRATIVE EXPENSES', "
                       "管理费用, 或有負債."),
    ("note_terms", "NOTE-HEADER HINT — plain words, scored against note HEADINGS, not matched. Same "
                   "question as the column before by a different mechanism, so an unanticipated "
                   "phrasing still ranks instead of not firing. KEEP THESE ABOUT THE NOTE."),
    ("row_caption_any", "ROW HINT — regexes over a ROW CAPTION inside the chosen note. Which rows "
                        "count toward the figure. A row names the CONTENT: 'Depreciation of fixed "
                        "assets', 固定资产折旧."),
    ("row_caption_none", "ROW HINT, VETO — rows that must NOT count even if the column before "
                         "matched them."),
    ("row_terms", "ROW HINT — plain words scored against ROW CAPTIONS. These also reach the model "
                  "and gate its answers, so a term here ranks a row AND tells the model what the "
                  "line is called."),
    ("row_terms_none", "ROW HINT, VETO, scored."),
    ("prose_any", "SENTENCE HINT — regexes over a note's SENTENCES, for a figure the filing states "
                  "in words and tabulates nowhere. Authored for sentence length, not caption "
                  "length. EMPTY MEANS NO PROSE ROUTE, which is the safe default."),
    ("llm_only_if_note_tagged", "TRUE/FALSE. TRUE for a line only ever disclosed in a note: where "
                                "the face prints no note reference the line reports 0 and no model "
                                "call is spent, because the filing not disclosing it and us not "
                                "finding it are different statements."),
]

LIST_COLUMNS = {"aliases", "aliases_i18n_zh", "regex_hints", "keyword_hints", "exclude_hints",
                "include_criteria", "exclude_criteria", "note_title_any", "note_terms",
                "row_caption_any", "row_caption_none", "row_terms", "row_terms_none", "prose_any"}
NOTE_SOURCE_COLUMNS = {"note_title_any", "note_terms", "row_caption_any", "row_caption_none",
                       "row_terms", "row_terms_none", "prose_any"}
BOOL_COLUMNS = {"in_output", "llm_only_if_note_tagged"}

SET_FIELDS: list[tuple[str, str, str]] = [
    ("line_items_key", "", "A short stable name for this configuration, e.g. output_csv_hk."),
    ("target_template_key", "", "The output template this set fills. The publish re-validates "
                                "against it, so a key that does not exist is refused."),
    ("locale", "en", "The set's own default language. `aliases` is the alias list for THIS locale."),
    ("supported_locales", "en\nzh", "One per line. A locale absent here cannot be authored."),
    ("prompt", "", "THE MASTER PROMPT — one instruction sent on every mapping call. The reply "
                   "contract is fixed in code and is not editable."),
]

SECTION_COLUMNS: list[tuple[str, str]] = [
    ("section", "The section token. A line's `inherits` names this."),
    ("statement", "The statement this section is printed on."),
    ("section_scope", "The printed banners a line in this section may sit under, one per line. "
                      "EMPTY MEANS UNCONSTRAINED and is stored as a real declaration."),
    ("temporality", "`instant` for a balance at a date, `duration` for a flow over a period."),
    ("unit_of_account", "`balance`, `flow`, or `subtotal`. LOAD-BEARING: `subtotal` is the sole "
                        "discriminator that tells the reconciliation which line closes a section, "
                        "so a subtotal mislabelled as a balance is summed into its own section."),
    ("note_use", "The section's default: may a note SOURCE a figure here, or only corroborate one."),
    ("sign_convention", "The sign a figure on this section is expected to carry."),
    ("face_only", "TRUE if a note may never be the source for lines in this section."),
]

CASCADE_COLUMNS: list[tuple[str, str]] = [
    ("parent_key", "The DERIVED line this rung belongs to."),
    ("rung_id", "e.g. P1, P2, LTP_P1. THE ORDER OF THE RUNGS IS THEIR PRIORITY — the first rung "
                "that resolves wins — so rung ids must sort in the order you want them tried."),
    ("term_ref", "A line item key this rung reads. One row per term."),
    ("sign", "1 to add, -1 to deduct."),
    ("role", "`required` — the rung cannot resolve without it. `adjustment` — its absence does not "
             "kill the rung, so the rung resolves WITHOUT the deduction. Get this wrong and a "
             "charge silently publishes gross of a deduction that was simply missing."),
    ("refuse_negative", "TRUE to refuse a rung that computes below zero and try the next one. A "
                        "charge, a balance and an exposure cannot be negative; a net movement can."),
    ("outranks_printed", "TRUE if this rung may DISPLACE a figure the filing printed. Only for a "
                         "rung that RECONSTRUCTS something the face does not state. FALSE where the "
                         "rung merely restates the face — a printed figure there is what the rung "
                         "was looking for."),
    ("note", "Why this rung exists, for the next reader."),
]

TERM_COLUMNS: list[tuple[str, str]] = [
    ("parent_key", "The CALCULATED line this term belongs to."),
    ("term_ref", "A line item key. One row per term."),
    ("sign", "1 to add, -1 to deduct."),
]


def _literals() -> list[tuple[str, str, str]]:
    """(field, legal values, where it is declared) for every closed field — from the schema."""
    from app.schemas import line_items as LI

    out: list[tuple[str, str, str]] = []
    for model_name in ("LineItemDef", "SectionDefaults", "NoteSource", "CascadeRung", "Term"):
        model = getattr(LI, model_name, None)
        if model is None:
            continue
        for name, field in model.model_fields.items():
            ann = field.annotation
            args = typing.get_args(ann)
            # `Literal[...]`, or `Literal[...] | None`
            vals: list[str] = []
            if typing.get_origin(ann) is typing.Literal:
                vals = [str(a) for a in args]
            else:
                for a in args:
                    if typing.get_origin(a) is typing.Literal:
                        vals = [str(x) for x in typing.get_args(a)]
            if vals:
                out.append((name, "  |  ".join(vals), model_name))
    # Statements come from an enum rather than a Literal.
    try:
        from app.core.models.enums import StatementType
        out.append(("statement", "  |  ".join(s.value for s in StatementType), "StatementType"))
    except Exception:  # noqa: BLE001
        pass
    return out


def export(out_path: pathlib.Path) -> int:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ink = "1F2937"
    wrap = Alignment(wrap_text=True, vertical="top")
    thin = Border(bottom=Side(style="thin", color="D1D5DB"))
    req = PatternFill("solid", fgColor="7F1D1D")     # required
    note_f = PatternFill("solid", fgColor="1E3A5F")  # note-header hints
    row_f = PatternFill("solid", fgColor="4A1E5F")   # row hints
    other = PatternFill("solid", fgColor="374151")
    grey = PatternFill("solid", fgColor="F3F4F6")

    def sheet(name: str, cols: list[tuple[str, str]], *, rows: int = 220) -> None:
        ws = wb.create_sheet(name)
        for j, (col, help_text) in enumerate(cols, start=1):
            h = ws.cell(1, j, col)
            h.font = Font(bold=True, color="FFFFFF", size=10)
            h.fill = (req if col == "key"
                      else note_f if col in ("note_title_any", "note_terms")
                      else row_f if col.startswith("row_")
                      else other)
            h.alignment = Alignment(wrap_text=True, vertical="bottom")
            # The help text sits in the cell comment, so the header stays readable and the guidance
            # is one hover away rather than on another sheet.
            from openpyxl.comments import Comment
            h.comment = Comment(help_text, "FinExtract", height=190, width=420)
            ws.column_dimensions[get_column_letter(j)].width = (
                34 if col in LIST_COLUMNS else 22 if col != "key" else 40)
        for i in range(2, rows + 2):
            for j in range(1, len(cols) + 1):
                ws.cell(i, j).alignment = wrap
                ws.cell(i, j).border = thin
                ws.cell(i, j).font = Font(size=9)
        ws.freeze_panes = "B2"

    # ── README ────────────────────────────────────────────────────────────────────────────────
    ws = wb.create_sheet("README")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 120
    body = [
        ("WHAT THIS IS", "A blank line-item set. Fill it in offline and hand it back; it becomes a "
                         "JSON set for POST /api/v1/line-items, which is the only door into the "
                         "store and runs the same checks the shipped file passes at boot."),
        ("", ""),
        ("THE SHEETS", "Set — the set-level declarations."),
        ("", "Sections — `section_defaults`, the GATE. Authored once per section and claimed by a "
             "line's `inherits`, which is why the line sheet has one `inherits` column instead of "
             "twelve gate columns."),
        ("", "Line items — one row per line. Only `key` is required."),
        ("", "Cascade — a DERIVED line's ordered rungs, ONE ROW PER TERM."),
        ("", "Terms — a CALCULATED line's signed sum, one row per term."),
        ("", "Vocabulary — READ-ONLY, generated from the schema. Every legal value for every closed "
             "field."),
        ("", ""),
        ("CONVENTIONS", "A LIST CELL HOLDS ONE VALUE PER LINE (alt+enter). Never comma-separate — a "
                        "regex may contain a comma and a caption certainly can."),
        ("", "An EMPTY cell means an empty list, which is a real declaration and not 'use a "
             "default'."),
        ("", "TRUE/FALSE for the boolean columns; leave blank for 'nothing said' where the field "
             "allows it."),
        ("", "Hover any column header for what that field does."),
        ("", ""),
        ("THE ONE RULE THAT MATTERS MOST",
         "NOTE-HEADER HINTS AND ROW HINTS ARE DIFFERENT VOCABULARIES AND MUST NOT BE MIXED."),
        ("", "A note's HEADING names the CONTAINER — 'ADMINISTRATIVE EXPENSES', 管理费用, 或有負債."),
        ("", "A row's CAPTION names the CONTENT — 'Depreciation of fixed assets', 固定资产折旧."),
        ("", "The blue columns are heading-level; the purple ones are row-level. A value written at "
             "the wrong level does not degrade gracefully — it degrades to silence. Measured on "
             "the shipped set: a blended probe scored 0.000 against the very heading its own line "
             "belongs to, because every one of its tokens was a content word."),
        ("", ""),
        ("HOW A FIGURE IS OBTAINED", "`type` is the only field that says, and it decides which "
                                     "other sheets apply:"),
        ("", "extracted  — read off a printed caption. The ONLY type the model is ever asked about. "
             "Fill in the alias/regex/keyword and hint columns."),
        ("", "calculated — summed from components. Fill in the Terms sheet."),
        ("", "derived    — assembled by its cascade. Fill in the Cascade sheet. Reached by nothing "
             "else: no caption, no alias, no semantic probe, and never the model — so its "
             "recognition columns should be left EMPTY and the recognition put on its parts."),
        ("", ""),
        ("BEFORE YOU PUBLISH", "python scripts/ontology_template.py --import <your file> -o set.json"),
        ("", "It reports every problem in one pass rather than the first one. A refused set "
             "publishes nothing at all."),
    ]
    r = 1
    for a, b in body:
        ca, cb = ws.cell(r, 1, a), ws.cell(r, 2, b)
        if a:
            ca.font = Font(bold=True, color=ink, size=11)
        cb.alignment = wrap
        cb.font = Font(size=10, color=ink)
        r += 1

    # ── Set ───────────────────────────────────────────────────────────────────────────────────
    ws = wb.create_sheet("Set")
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 46
    ws.column_dimensions["C"].width = 96
    for j, h in enumerate(("field", "value", "what it does"), start=1):
        c = ws.cell(1, j, h)
        c.font = Font(bold=True, color="FFFFFF", size=10)
        c.fill = other
    for i, (name, default, help_text) in enumerate(SET_FIELDS, start=2):
        ws.cell(i, 1, name).font = Font(size=10, name="Consolas")
        ws.cell(i, 2, default).alignment = wrap
        c = ws.cell(i, 3, help_text)
        c.alignment = wrap
        c.font = Font(size=9, color=ink)
        ws.row_dimensions[i].height = 42

    sheet("Sections", SECTION_COLUMNS, rows=40)
    sheet("Line items", COLUMNS, rows=400)
    sheet("Cascade", CASCADE_COLUMNS, rows=200)
    sheet("Terms", TERM_COLUMNS, rows=200)

    # ── Vocabulary (read-only) ────────────────────────────────────────────────────────────────
    ws = wb.create_sheet("Vocabulary")
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 78
    ws.column_dimensions["C"].width = 20
    for j, h in enumerate(("field", "legal values", "declared on"), start=1):
        c = ws.cell(1, j, h)
        c.font = Font(bold=True, color="FFFFFF", size=10)
        c.fill = other
    seen: set[str] = set()
    r = 2
    for name, vals, where in _literals():
        if name in seen:
            continue
        seen.add(name)
        ws.cell(r, 1, name).font = Font(size=10, name="Consolas")
        v = ws.cell(r, 2, vals)
        v.alignment = wrap
        v.font = Font(size=9, name="Consolas")
        ws.cell(r, 3, where).font = Font(size=9, color="6B7280")
        for j in (1, 2, 3):
            ws.cell(r, j).fill = grey
        r += 1
    ws.freeze_panes = "A2"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    print(f"wrote {out_path}")
    print(f"  README + Set + Sections + Line items ({len(COLUMNS)} cols) + Cascade + Terms "
          f"+ Vocabulary ({r - 2} closed fields)")
    return 0


def import_(xlsx: pathlib.Path, out_json: pathlib.Path) -> int:
    """A filled workbook to a set JSON, reporting every problem in one pass."""
    import openpyxl

    wb = openpyxl.load_workbook(xlsx)
    problems: list[str] = []

    def cell_list(v) -> list[str]:
        return [l.strip() for l in str(v or "").splitlines() if l.strip()]

    def cell_bool(v):
        if v is None or str(v).strip() == "":
            return None
        return str(v).strip().lower() in ("true", "yes", "y", "1")

    definition: dict = {"schema_version": 1}
    if "Set" in wb.sheetnames:
        for row in wb["Set"].iter_rows(min_row=2):
            name = str(row[0].value or "").strip()
            raw = row[1].value
            if not name:
                continue
            definition[name] = (cell_list(raw) if name == "supported_locales"
                                else str(raw or "").strip())

    if "Sections" in wb.sheetnames:
        ws = wb["Sections"]
        header = [str(c.value or "").strip() for c in ws[1]]
        sections: dict = {}
        for row in ws.iter_rows(min_row=2):
            cells = {header[j]: row[j].value for j in range(len(header)) if header[j]}
            token = str(cells.get("section") or "").strip()
            if not token:
                continue
            entry: dict = {}
            for col, _h in SECTION_COLUMNS:
                if col == "section":
                    continue
                v = cells.get(col)
                if v is None or str(v).strip() == "":
                    continue
                entry[col] = (cell_list(v) if col == "section_scope"
                              else cell_bool(v) if col == "face_only"
                              else str(v).strip())
            sections[token] = entry
        if sections:
            definition["section_defaults"] = sections

    items: list[dict] = []
    if "Line items" in wb.sheetnames:
        ws = wb["Line items"]
        header = [str(c.value or "").strip() for c in ws[1]]
        keys: set[str] = set()
        for n, row in enumerate(ws.iter_rows(min_row=2), start=2):
            cells = {header[j]: row[j].value for j in range(len(header)) if header[j]}
            key = str(cells.get("key") or "").strip()
            if not key:
                if any(str(v or "").strip() for v in cells.values()):
                    problems.append(f"Line items row {n}: values with no `key`")
                continue
            if key in keys:
                problems.append(f"Line items row {n}: `{key}` is defined twice")
            keys.add(key)
            item: dict = {"key": key}
            note_source: dict = {}
            for col, _h in COLUMNS:
                if col == "key":
                    continue
                v = cells.get(col)
                if v is None or str(v).strip() == "":
                    continue
                if col in NOTE_SOURCE_COLUMNS:
                    note_source[col] = cell_list(v)
                elif col == "aliases_i18n_zh":
                    item.setdefault("aliases_i18n", {})["zh"] = cell_list(v)
                elif col in LIST_COLUMNS:
                    item[col] = cell_list(v)
                elif col in BOOL_COLUMNS:
                    item[col] = cell_bool(v)
                else:
                    item[col] = str(v).strip()
            if note_source:
                item["note_source"] = note_source
            items.append(item)

    by_key = {i["key"]: i for i in items}
    # Rungs and terms, grouped back onto their parent — one row per TERM on the sheet.
    for sheet_name, target in (("Cascade", "cascade"), ("Terms", "terms")):
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        header = [str(c.value or "").strip() for c in ws[1]]
        for n, row in enumerate(ws.iter_rows(min_row=2), start=2):
            cells = {header[j]: row[j].value for j in range(len(header)) if header[j]}
            parent = str(cells.get("parent_key") or "").strip()
            ref = str(cells.get("term_ref") or "").strip()
            if not parent and not ref:
                continue
            if parent not in by_key:
                problems.append(f"{sheet_name} row {n}: parent_key `{parent}` is not a line item")
                continue
            if ref and ref not in by_key:
                problems.append(f"{sheet_name} row {n}: term_ref `{ref}` is not a line item")
            try:
                sign = int(cells.get("sign") or 1)
            except (TypeError, ValueError):
                problems.append(f"{sheet_name} row {n}: sign must be 1 or -1")
                sign = 1
            term = {"ref": ref, "sign": sign}
            if target == "cascade":
                term["role"] = str(cells.get("role") or "required").strip() or "required"
                rung_id = str(cells.get("rung_id") or "").strip()
                if not rung_id:
                    problems.append(f"Cascade row {n}: every term needs a rung_id")
                    continue
                rungs = by_key[parent].setdefault("cascade", [])
                rung = next((r for r in rungs if r["id"] == rung_id), None)
                if rung is None:
                    rung = {"id": rung_id, "terms": []}
                    for flag, col in (("refuse_negative", "refuse_negative"),
                                      ("outranks_printed", "outranks_printed")):
                        b = cell_bool(cells.get(col))
                        if b is not None:
                            rung[flag] = b
                    note = str(cells.get("note") or "").strip()
                    if note:
                        rung["note"] = note
                    rungs.append(rung)
                rung["terms"].append(term)
            else:
                by_key[parent].setdefault("terms", []).append(term)

    definition["items"] = items

    if problems:
        print(f"{len(problems)} problem(s) — nothing written:")
        for p in problems:
            print(f"   {p}")
        return 1

    # THE REAL LOADER IS THE ONLY HONEST CHECK, and it is the same one the publish runs — twice,
    # because an unresolved load reports stray keys while only the RESOLVED load catches a dangling
    # `inherits`, which is not a load error but leaves the line with no section gate at all.
    from app.schemas.line_items import load_line_item_set
    try:
        load_line_item_set(definition, resolve=False)
        load_line_item_set(definition, resolve=True)
    except Exception as exc:  # noqa: BLE001
        print(f"the set does not load, so it would not publish:\n   {exc}")
        return 1

    out_json.write_text(json.dumps(definition, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)} line item(s) -> {out_json}")
    print(f"  sections: {len(definition.get('section_defaults') or {})}")
    print(f"  derived with a cascade: {sum(1 for i in items if i.get('cascade'))}")
    print(f"  calculated with terms:  {sum(1 for i in items if i.get('terms'))}")
    print("\nloads clean, unresolved AND resolved. Publish with:")
    print("  POST /api/v1/line-items   {\"definition\": <the JSON>}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", type=pathlib.Path, default=None)
    ap.add_argument("--import", dest="imp", type=pathlib.Path, default=None)
    args = ap.parse_args()
    if args.imp:
        return import_(args.imp, args.out or args.imp.with_suffix(".set.json"))
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    return export(args.out or root / "_vocab" / "ontology_blank.xlsx")


if __name__ == "__main__":
    raise SystemExit(main())
