"""The output template as a workbook an analyst can edit, and read back again.

A template is a section→line tree with rollups — JSON, which is the right shape for the
pipeline and the wrong shape for the person who decides what the spread should contain. This
module is the round trip: one flat sheet, one row per line, with the two facts that decide how a
line behaves stated in their own columns:

* **Kind** — ``extracted`` (read off the document by the mapper) or ``calculated`` (computed from
  other lines and never mapped). This is the distinction that makes a template reviewable: a
  calculated line that is mistakenly marked extracted gets a figure from the page instead of from
  its components, and a subtotal that stops tying is the first anyone hears of it.
* **Calculated from** — the exact lines a calculated one is made of, which is also what the
  structural checks recompute and compare, so editing this column changes what gets validated.

The workbook is authoritative on the way back in: ``extracted`` drops any rollup, ``calculated``
requires one, and every referenced key must exist in the sheet. Nothing is inferred silently —
an ambiguous edit is an error with the row number on it, not a guess.
"""
from __future__ import annotations

import io
import re

from app.core.models.enums import SignConvention
from app.services.mapping import normalize_statement
from app.services.statements import TITLES as STATEMENT_TITLES

# Column order of the Template sheet. Kept as data so the reader and the writer cannot drift.
#
# 'REQUIRED' USED TO BE THE LAST COLUMN AND IS GONE. Nothing in ``app/`` ever read
# ``TemplateNode.required`` — there is no post-run completeness check anywhere — so ticking the
# box set a field no code consults, and the sheet was offering an author a gate that did not
# exist. 0 of the 682 shipped nodes (480 + 202) ticked it, so no template loses anything by the
# removal. An analyst's saved copy of an older workbook still re-imports cleanly: :func:`_cells`
# matches on HEADER TEXT, so the leftover column is ignored rather than shifting every value one
# to the left — it simply stops being able to set the key.
#
# EVERY COLUMN HERE IS REQUIRED ON THE WAY BACK IN. Only four used to be: a workbook without its
# Section column then published every line as a top-level section, one without Role published no
# subtotal or total, and one without Sign turned every natural_negative line natural — each a
# valid template that said something the author never wrote, because every column has a default
# for a blank cell and a missing column reads as a blank cell on every row. A column of the author's own (notes, the
# legacy 'Required') is still ignored; a column of OURS that is absent is refused by name.
COLUMNS = [
    ("statement", "Statement"),
    ("section", "Section"),
    ("node_id", "Node ID"),
    ("canonical_key", "Canonical key"),
    ("label", "Label (en)"),
    ("label_zh", "Label (zh)"),
    ("label_ar", "Label (ar)"),
    ("label_fr", "Label (fr)"),
    ("role", "Role"),
    ("kind", "Kind"),
    ("op", "Calculation"),
    ("children", "Calculated from"),
    ("sign", "Sign"),
    ("expects_note", "Expects note"),
]
_WIDTHS = [20, 34, 40, 44, 40, 26, 26, 30, 11, 12, 12, 60, 14, 13]

KIND_EXTRACTED = "extracted"
KIND_CALCULATED = "calculated"
KIND_HEADING = "heading"
_KINDS = {KIND_EXTRACTED, KIND_CALCULATED, KIND_HEADING}
# Exactly the ops ``schemas.template.Rollup`` accepts. ``weighted_sum`` was still admitted here
# after the JSON gate dropped it, so the two authoring routes disagreed about what is legal: a
# workbook naming it was accepted and published a template whose op the JSON upload refuses and
# whose relation ``structural_checks`` can only report as ``unsupported_op`` — the calculated line
# it was authored on silently lost its arithmetic guard. A workbook that names it is now refused on
# the row that names it, which is the same answer the JSON route gives.
_OPS = {"sum", "diff"}
_ROLES = {"header", "line", "subtotal", "total"}
_LOCALES = ("zh", "ar", "fr")
# Exactly the values ``schemas.template.TemplateNode.sign`` accepts, checked on the row that names
# one: left to the schema, a typo was refused at publish with an enum error naming no row.
_SIGNS = tuple(s.value for s in SignConvention)

# The vocabulary lives in ``services.statements`` because the API serves it too — a screen
# offering a statement this importer would refuse is a disagreement with no owner.
_STATEMENT_TITLE = STATEMENT_TITLES
_TITLE_STATEMENT = {v.lower(): k for k, v in _STATEMENT_TITLE.items()}

# The Identities sheet, read by header text exactly as the Template sheet is. It used to be read by
# POSITION, so an inserted or reordered column moved values between fields without an error: with
# the two tolerance columns swapped, the balance-sheet identity's relative tolerance went from 0.1%
# to 100% and it could no longer fail.
IDENTITY_COLUMNS = [
    ("statement", "Statement"),
    ("id", "Identity ID"),
    ("lhs", "Left (canonical key)"),
    ("op", "Calculation"),
    ("rhs", "Right (canonical keys)"),
    ("tolerance_abs", "Tolerance (abs)"),
    ("tolerance_rel", "Tolerance (rel)"),
]

# The Read me row that names the template a workbook was downloaded from, so an upload under a new
# key can still find what the sheet itself cannot carry (see :func:`import_workbook`).
_SOURCE_KEY_LABEL = "Template key"


def _sheet_statement(st_type) -> str | None:
    """A definition's statement type → the workbook's spelling of it, or None when the workbook
    has no row for it. Folded first: a definition may store ``equity_changes``, which is the
    enum's spelling of the statement the sheet calls "Changes in equity"."""
    st = normalize_statement(str(st_type or ""))
    return st if st in _STATEMENT_TITLE else None


def inexpressible_statements(definition: dict) -> list[str]:
    """The statement types in a definition that a workbook cannot hold — a row for one is refused
    on the way back in, so the template can be read as a workbook but not authored as one."""
    return [str(st.get("type") or "") for st in definition.get("statements") or []
            if _sheet_statement(st.get("type")) is None]


class TemplateSheetError(ValueError):
    """An edited workbook that cannot be read as a template, with the offending row named."""


def _kind_of(node: dict) -> str:
    if (node.get("role") or "line") == "header":
        return KIND_HEADING
    return KIND_CALCULATED if node.get("rollup") else KIND_EXTRACTED


def _yes(v) -> str:
    return "yes" if v else ""


def build_template_xlsx(definition: dict, *, filename_hint: str = "template") -> bytes:
    """The template as an editable workbook: one row per line, plus its identity checks."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="34405A")
    sec_font = Font(bold=True)
    sec_fill = PatternFill("solid", fgColor="EEF1F6")
    calc_fill = PatternFill("solid", fgColor="FFF6E5")
    wrap = Alignment(vertical="top", wrap_text=True)

    ws = wb.active
    ws.title = "Template"
    ws.append([h for _k, h in COLUMNS])
    for i, w in enumerate(_WIDTHS, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for c in ws[1]:
        c.font, c.fill = head_font, head_fill
        c.alignment = Alignment(vertical="center", wrap_text=True)

    def write(node: dict, statement: str, section: str) -> None:
        i18n = node.get("label_i18n") or {}
        rollup = node.get("rollup") or {}
        kind = _kind_of(node)
        row = {
            "statement": statement, "section": section,
            "node_id": node.get("node_id") or "",
            "canonical_key": node.get("canonical_key") or "",
            "label": i18n.get("en") or node.get("label") or "",
            "role": node.get("role") or "line",
            "kind": kind,
            "op": rollup.get("op") or ("sum" if kind == KIND_CALCULATED else ""),
            # One key per line inside the cell: a 20-child subtotal is unreadable on one line,
            # and newline-separated is what an editor can actually work with.
            "children": "\n".join(rollup.get("children") or []),
            "sign": node.get("sign") or "natural",
            "expects_note": _yes(node.get("expects_note")),
        }
        for loc in _LOCALES:
            row[f"label_{loc}"] = i18n.get(loc, "")
        ws.append([row.get(k, "") for k, _h in COLUMNS])
        r = ws.max_row
        for c in ws[r]:
            c.alignment = wrap
        if kind == KIND_HEADING:
            for c in ws[r]:
                c.font, c.fill = sec_font, sec_fill
        elif kind == KIND_CALCULATED:
            for c in ws[r]:
                c.fill = calc_fill

    for stmt in definition.get("statements", []):
        st = str(stmt.get("type") or "")
        title = _STATEMENT_TITLE.get(_sheet_statement(st) or "", st.replace("_", " ").capitalize())
        for sec in stmt.get("sections") or []:
            write(sec, title, "")
            for child in sec.get("children") or []:
                write(child, title, sec.get("node_id") or "")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{ws.cell(1, len(COLUMNS)).column_letter}{ws.max_row}"
    kind_col = ws.cell(1, 1 + [k for k, _h in COLUMNS].index("kind")).column_letter
    dv = DataValidation(type="list", formula1=f'"{",".join(sorted(_KINDS))}"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{kind_col}2:{kind_col}{max(2, ws.max_row)}")

    # --- identity checks (statement-level, also calculated relationships) ---
    ids = wb.create_sheet("Identities")
    ids.append([h for _k, h in IDENTITY_COLUMNS])
    for c in ids[1]:
        c.font, c.fill = head_font, head_fill
    for stmt in definition.get("statements", []):
        st = str(stmt.get("type") or "")
        title = _STATEMENT_TITLE.get(_sheet_statement(st) or "", st)
        for ident in stmt.get("identities", []) or []:
            rhs = ident.get("rhs") or {}
            ids.append([title, ident.get("id") or "", ident.get("lhs") or "",
                        rhs.get("op") or "sum", "\n".join(rhs.get("children") or []),
                        ident.get("tolerance_abs", 1.0), ident.get("tolerance_rel", 0.001)])
    for col, w in zip("ABCDEFG", (20, 26, 44, 14, 60, 15, 15)):
        ids.column_dimensions[col].width = w
    for row in ids.iter_rows(min_row=2):
        for c in row:
            c.alignment = wrap
    ids.freeze_panes = "A2"

    # --- the contract, in the file itself ---
    rm = wb.create_sheet("Read me")
    rm.column_dimensions["A"].width = 26
    rm.column_dimensions["B"].width = 118
    lines = [
        ("Template", f"{definition.get('name') or filename_hint} "
                     f"(key: {definition.get('template_key') or '—'})"),
        # Machine-read on upload: an upload under a NEW key carries forward from this template what
        # the sheet cannot hold. Leave it as it is.
        (_SOURCE_KEY_LABEL, definition.get("template_key") or ""),
        ("", ""),
        ("How to use this", "Edit the Template sheet, then upload it back on the Template & "
                            "Ontology screen. A new template VERSION is created — earlier runs "
                            "keep explaining themselves against the version they used."),
        ("", ""),
        ("Statement", "Which statement the line belongs to. Keep the spelling used in the "
                      "existing rows; a new statement name is rejected rather than guessed."),
        ("Section", "The Node ID of the heading this line sits under. Leave BLANK for a row that "
                    "is itself a section heading or a statement-level total."),
        ("Node ID", "Stable identifier. Leave as-is for existing lines; for a new line use a "
                    "unique lowercase_with_underscores name (the canonical key is fine)."),
        ("Canonical key", "What extraction maps to and what every check, export and formula "
                          "refers to. Must be unique. Renaming one breaks the ontology entry and "
                          "any rollup that names it, so change both together."),
        ("Label (en/zh/ar/fr)", "What the line is called on screen and in the export, per output "
                                "language. English is required; the others fall back to it."),
        ("Role", "header | line | subtotal | total — presentation emphasis in the grid."),
        ("Kind", f"{KIND_EXTRACTED} — read off the document by the mapper.\n"
                 f"{KIND_CALCULATED} — computed from other lines and never mapped; requires "
                 f"'Calculated from'.\n"
                 f"{KIND_HEADING} — a section heading; carries no figure."),
        ("Calculation", "sum or diff — how the lines in 'Calculated from' combine (diff = first "
                        "minus the rest). Required for a calculated line."),
        ("Calculated from", "One canonical key per line. These are recomputed and compared with "
                            "the extracted figure, which is what makes a mis-mapping show up as a "
                            "failed check instead of a wrong number."),
        # This used to offer "natural or contra". 'contra' is not a sign convention the schema
        # knows, so a line marked with it was refused at publish with an enum error naming no row.
        ("Sign", f"One of {', '.join(_SIGNS)}. Leave natural (as reported) unless the "
                 f"template you downloaded already says otherwise for the line."),
        # This entry used to promise that 'yes' flagged a line "whose absence should be raised".
        # Nothing raises it: there is no post-run completeness check anywhere in app/. 'Required'
        # was the worse half of that promise — read by no code at all — and its column is now
        # gone; only 'Expects note', which really does feed the ontology skeleton, is still
        # authorable. The wording claims only what the remaining tick actually does.
        ("Expects note", "A note ABOUT the line rather than a gate on it. 'yes' is recorded on "
                         "the template node, comes back out in this workbook and in the JSON "
                         "export, and pre-fills the note_ref_hint of a generated ontology "
                         "skeleton. It is not checked after a run: a line left blank is not "
                         "reported as missing."),
        ("", ""),
        ("Shading", "Grey rows are section headings. Amber rows are calculated lines."),
        ("Identities sheet", "Statement-level equalities (e.g. total assets = total equity and "
                             "liabilities) checked after extraction, with their tolerances. "
                             "Columns are read by header text. Deleting the sheet is refused "
                             "when the template has identities; to remove them all, keep the "
                             "sheet with only its header row."),
        ("", ""),
        ("Not in this workbook", "Residual subtotals checked against a reported total, KPI "
                                 "ratios, statement headings and cross-statement ties have no "
                                 "column here. Uploading onto this template keeps them as they "
                                 "are; an edit that would change a residual line's Kind, "
                                 "Calculation, 'Calculated from' or canonical key is refused — "
                                 "make that edit in the JSON template."),
        ("Every column is read", "Do not delete, merge or duplicate one of these column headers, "
                                 "and do not merge cells under one within the table: a merged "
                                 "cell is read as blank in every row but its first. Columns may "
                                 "be reordered, and columns of your own may be added and merged "
                                 "freely."),
    ]
    lost = inexpressible_statements(definition)
    if lost:
        lines.insert(3, ("CANNOT BE UPLOADED BACK",
                         f"This template declares statement(s) a workbook cannot hold "
                         f"({', '.join(lost)}). This file is for reading; an upload of it is "
                         f"refused. Edit the template as JSON instead."))
    for a, b in lines:
        rm.append([a, b])
        rm.cell(rm.max_row, 1).font = sec_font
        rm.cell(rm.max_row, 2).alignment = wrap
    wb.move_sheet("Read me", offset=-2)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _letter(i: int) -> str:
    """A 0-based column index → its sheet letter, for a message an admin can find the cell by."""
    from openpyxl.utils import get_column_letter

    return get_column_letter(i + 1)


def _cells(ws, columns=COLUMNS, sheet: str = "Template") -> list[dict]:
    """Sheet rows as dicts keyed by our column names, matched on the HEADER TEXT so a reordered
    or extra column in an edited workbook doesn't shift every value one to the left.

    WHAT IS REFUSED HERE, AND WHY NONE OF IT COULD BE READ ANYWAY. Each of these used to come back
    as a well-formed row with some of its values missing, and every column has a default for a
    missing value — so the edit published, and said something nobody wrote:

    * a MERGED HEADER hides the columns under it (a Statement+Section merge lost Section, and every
      line became a top-level section);
    * a recognised header seen TWICE resolved to whichever came last, so a blank duplicate 'Sign'
      added at the end turned every contra line natural;
    * a MISSING column read as blank on every row (no Role column: no subtotal, no total);
    * a MERGED DATA CELL holds its value in its first cell only, so merged Section cells over six
      lines promoted five of them to top-level sections.
    """
    header = [str(c.value or "").strip().lower() for c in ws[1]]
    by_head = {h.lower(): (k, h) for k, h in columns}
    idx: dict[str, int] = {}
    title_at: dict[int, str] = {}
    for i, h in enumerate(header):
        hit = by_head.get(h)
        if not hit:
            continue                                   # a column of the author's own, or 'Required'
        key, title = hit
        if key in idx:
            raise TemplateSheetError(
                f"{sheet} sheet, header row: '{title}' appears twice (columns {_letter(idx[key])} "
                f"and {_letter(i)}). Delete or rename one of them — only one can be read, and "
                f"which one would be a guess.")
        idx[key] = i
        title_at[i] = title
    # A merge on the header row is refused only where it touches one of OUR headers: over the
    # author's own columns it hides nothing this importer reads.
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= 1 and any(i in title_at for i in range(rng.min_col - 1, rng.max_col)):
            raise TemplateSheetError(
                f"{sheet} sheet, header row: cells {rng.coord} are merged. Unmerge them so every "
                f"column has a header of its own — the columns under a merged header cannot be "
                f"identified, so their values would be read as missing.")
    missing = [h for k, h in columns if k not in idx]
    if missing:
        raise TemplateSheetError(
            f"{sheet} sheet, header row: missing column(s) "
            f"{', '.join(repr(h) for h in missing)}. Every column of the downloaded workbook is "
            f"read, and without one every row would be published with that column's default. "
            f"Restore it with the header spelled as downloaded (columns may be reordered, and "
            f"columns of your own may be added).")
    # A merged data cell is refused only inside the table: a merge in blank rows below it (or
    # above none of our values) holds nothing that could be read short.
    used = {n for n, row in enumerate(ws.iter_rows(min_row=2), start=2)
            if any(i < len(row) and str(row[i].value or "").strip() for i in idx.values())}
    for rng in ws.merged_cells.ranges:
        if rng.max_row < 2 or not any(r in used for r in range(max(rng.min_row, 2),
                                                                rng.max_row + 1)):
            continue
        hit = [title_at[i] for i in range(rng.min_col - 1, rng.max_col) if i in title_at]
        if hit:
            raise TemplateSheetError(
                f"{sheet} sheet, cells {rng.coord}: merged across the "
                f"{', '.join(repr(h) for h in hit)} column. Unmerge them and type the value into "
                f"every row — a merged range holds its value in its first cell only, so every "
                f"other row in it would be read with that column blank.")
    out = []
    for n, row in enumerate(ws.iter_rows(min_row=2), start=2):
        vals = {k: row[i].value if i < len(row) else None for k, i in idx.items()}
        if not any(str(v or "").strip() for v in vals.values()):
            continue                                   # a blank spacer row
        vals["_row"] = n
        out.append(vals)
    return out


def _s(v) -> str:
    return str(v).strip() if v is not None else ""


def _keys(v) -> list[str]:
    """A 'Calculated from' cell → canonical keys. Accepts one per line, or comma/plus separated,
    because that is how people actually type a list into a cell."""
    return [p for p in (x.strip() for x in re.split(r"[\n,;+]+", _s(v))) if p]


def _truthy(v) -> bool:
    return _s(v).lower() in {"yes", "y", "true", "1", "x", "✓"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _statement_type(title: str) -> str | None:
    """A Statement cell → a statement type, accepting the heading we wrote, the type itself, or
    the obvious respellings ("Profit and loss"). Anything else is refused, not guessed."""
    t = _s(title).lower()
    if t in _TITLE_STATEMENT:
        return _TITLE_STATEMENT[t]
    slug = _slug(t)
    if slug in _STATEMENT_TITLE:
        return slug
    return next((st for st, disp in _STATEMENT_TITLE.items() if _slug(disp) == slug), None)


def _tolerance(v, row_no: int, header: str) -> float | None:
    """A tolerance cell → a number, or None for a blank one (the schema default applies).

    Strict for the same reason the columns are read by header: a tolerance that was not a number
    used to be dropped in silence, so a typo restored the default and nobody was told."""
    if v is None or not _s(v):
        return None
    num = None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        num = float(v)
    elif not isinstance(v, bool):
        try:
            num = float(_s(v))
        except ValueError:
            num = None
    if num is None or num < 0:
        raise TemplateSheetError(
            f"Identities row {row_no}: {header} must be a number of zero or more, not '{_s(v)}'. "
            f"Leave it blank to use the default.")
    return num


def _walk(nodes):
    for n in nodes or ():
        yield n
        yield from _walk(n.get("children"))


def _nodes_of(definition: dict):
    for st in definition.get("statements") or ():
        yield from _walk(st.get("sections"))


# What a rollup can say that the sheet's two columns — Calculation and Calculated from — cannot,
# with the value that means "nothing was said". A field off its default here is a RESIDUAL (or a
# magnitude convention), and rebuilt from op and children alone it silently becomes a plain sum:
# on the five reference filings that is 29 false structural failures, the "residual read as an
# equation" break ``structural_checks`` exists to prevent.
_ROLLUP_DEFAULTS = {"reported_total_key": None, "reported_total_op": "sum",
                    "use_reported_total_components": False, "cost_magnitude_children": []}


def _rollup_extras(rollup: dict | None) -> dict:
    return {k: v for k, v in (rollup or {}).items()
            if k not in ("op", "children") and v != _ROLLUP_DEFAULTS.get(k)}


def _describe(extras: dict) -> str:
    parts = []
    if extras.get("reported_total_key"):
        parts.append(f"checked against the reported total '{extras['reported_total_key']}'")
    if extras.get("use_reported_total_components"):
        parts.append("built from the reported total's own components")
    if extras.get("reported_total_op", "sum") != "sum":
        parts.append(f"combined with its reported total by {extras['reported_total_op']}")
    if extras.get("cost_magnitude_children"):
        parts.append("with " + ", ".join(extras["cost_magnitude_children"])
                     + " spent as magnitudes")
    return "; ".join(parts) or "with rollup fields the workbook has no column for"


def workbook_source_key(data: bytes) -> str | None:
    """The template key a workbook was downloaded from, off its Read me sheet, or None.

    Read from the machine row the writer puts there, falling back to the "(key: …)" the title row
    has always carried, so a workbook downloaded before that row existed still names its source."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception:  # noqa: BLE001 — a corrupt file is the importer's error to report
        return None
    if "Read me" not in wb.sheetnames:
        return None
    fallback = None
    for row in wb["Read me"].iter_rows(max_col=2, values_only=True):
        a, b = (list(row) + [None, None])[:2]
        if _s(a) == _SOURCE_KEY_LABEL and _s(b):
            return _s(b)
        if _s(a) == "Template" and fallback is None:
            m = re.search(r"\(key: ([^)\s]+)\)\s*$", _s(b))
            if m and m.group(1) != "—":
                fallback = m.group(1)
    return fallback


def parse_template_xlsx(data: bytes, *, template_key: str, name: str,
                        previous: dict | None = None) -> dict:
    """An edited workbook → a template definition, or a TemplateSheetError naming the bad row.

    See :func:`import_workbook`, which also says what was carried forward from ``previous``."""
    return import_workbook(data, template_key=template_key, name=name, previous=previous)[0]


def import_workbook(data: bytes, *, template_key: str, name: str,
                    previous: dict | None = None) -> tuple[dict, list[str]]:
    """An edited workbook → ``(definition, carried)``, or a TemplateSheetError naming the bad row.

    Deliberately strict. A template drives what every extraction maps to and what every check
    recomputes, so a row this reader is unsure about is a row it refuses: guessing here would
    show up much later as a figure on the wrong line.

    ``previous`` is the definition the workbook is being published over — the latest version of
    its key, or of the template it was downloaded from. THE SHEET CANNOT SAY EVERYTHING A JSON
    TEMPLATE CAN: residual rollups (``reported_total_key`` and its companions), the KPI block,
    statement headings, cross-statement ties, labels in a locale without a column. Rebuilding the
    template from the sheet alone dropped all of them, and the result was still schema-valid, so
    no gate downstream could notice. They are now carried forward from ``previous`` wherever the
    sheet left the line they hang on unchanged, and every carry is named in ``carried`` so the
    upload can say so. An edit that would CHANGE one of them — a residual's components rewritten,
    a KPI's line deleted, a statement the sheet has no row for — is refused instead, because the
    sheet has no way to say what the author meant it to become.
    """
    import openpyxl

    if previous:
        lost = inexpressible_statements(previous)
        if lost:
            raise TemplateSheetError(
                f"Template '{previous.get('template_key') or template_key}' declares "
                f"statement(s) a workbook cannot hold: {', '.join(lost)}. Publishing a workbook "
                f"over it would drop them and every line in them, so it is refused. Edit this "
                f"template as JSON and upload that instead.")
    prev_nodes = {n.get("canonical_key"): n for n in _nodes_of(previous or {})}

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    if "Template" not in wb.sheetnames:
        raise TemplateSheetError("The workbook has no 'Template' sheet. Download the current "
                                 "template, edit that, and upload it back.")
    rows = _cells(wb["Template"])
    if not rows:
        raise TemplateSheetError("The Template sheet has no rows.")

    # Pass 1: validate every row, collect the keys that exist so rollups can be checked.
    known: set[str] = set()
    for r in rows:
        key = _s(r.get("canonical_key"))
        if not key:
            raise TemplateSheetError(f"Row {r['_row']}: Canonical key is required.")
        if key in known:
            raise TemplateSheetError(f"Row {r['_row']}: duplicate canonical key '{key}'.")
        known.add(key)

    statements: dict[str, dict] = {}
    order: list[str] = []
    sections: dict[tuple[str, str], dict] = {}
    row_of: dict[str, int] = {}

    for r in rows:
        row_no = r["_row"]
        title = _s(r.get("statement"))
        if not title:
            raise TemplateSheetError(f"Row {row_no}: Statement is required.")
        st = _statement_type(title)
        if st is None:
            raise TemplateSheetError(
                f"Row {row_no}: unknown Statement '{title}'. Use one of: "
                f"{', '.join(sorted(_STATEMENT_TITLE.values()))}. A template with any other "
                f"statement can only be authored as JSON.")

        key = _s(r.get("canonical_key"))
        row_of[key] = row_no
        label = _s(r.get("label"))
        if not label:
            raise TemplateSheetError(f"Row {row_no}: Label (en) is required for '{key}'.")
        kind = _s(r.get("kind")).lower() or KIND_EXTRACTED
        if kind not in _KINDS:
            raise TemplateSheetError(
                f"Row {row_no}: Kind must be one of {', '.join(sorted(_KINDS))}, not '{kind}'.")
        role = _s(r.get("role")).lower() or ("header" if kind == KIND_HEADING else "line")
        if role not in _ROLES:
            raise TemplateSheetError(
                f"Row {row_no}: Role must be one of {', '.join(sorted(_ROLES))}, not '{role}'.")
        sign = _s(r.get("sign")).lower() or "natural"
        if sign not in _SIGNS:
            raise TemplateSheetError(
                f"Row {row_no}: Sign must be one of {', '.join(_SIGNS)}, not '{sign}'.")
        children = _keys(r.get("children"))
        op = _s(r.get("op")).lower() or "sum"
        if kind == KIND_CALCULATED:
            # The one calculated line that legitimately names no components: a residual built from
            # its reported total's own components. The sheet cannot declare that, so it is
            # accepted only where the version being replaced already says so — and then carried.
            prev_roll = (prev_nodes.get(key) or {}).get("rollup") or {}
            childless_residual = (not children and not prev_roll.get("children")
                                  and bool(prev_roll.get("use_reported_total_components")))
            if not children and not childless_residual:
                raise TemplateSheetError(
                    f"Row {row_no}: '{key}' is marked {KIND_CALCULATED} but 'Calculated from' is "
                    f"empty — a calculated line has to say what it is calculated from.")
            if op not in _OPS:
                raise TemplateSheetError(
                    f"Row {row_no}: Calculation must be one of {', '.join(sorted(_OPS))}.")
            unknown = [c for c in children if c not in known]
            if unknown:
                raise TemplateSheetError(
                    f"Row {row_no}: '{key}' is calculated from key(s) that are not in this "
                    f"template: {', '.join(unknown)}.")
            if key in children:
                raise TemplateSheetError(f"Row {row_no}: '{key}' cannot be calculated from itself.")
        elif children:
            raise TemplateSheetError(
                f"Row {row_no}: '{key}' is marked {kind} but has 'Calculated from' set. Mark it "
                f"{KIND_CALCULATED}, or clear that column.")

        node: dict = {
            "node_id": _s(r.get("node_id")) or key,
            "canonical_key": key, "label": label, "role": role,
            "label_i18n": {"en": label,
                           **{loc: _s(r.get(f"label_{loc}")) for loc in _LOCALES
                              if _s(r.get(f"label_{loc}"))}},
            "sign": sign,
        }
        if _truthy(r.get("expects_note")):
            node["expects_note"] = True
        # No ``required`` branch: the column is gone (see COLUMNS), so a workbook — including an
        # older one that still carries the header — can no longer put the key on a node.
        if kind == KIND_CALCULATED:
            node["rollup"] = {"op": op, "children": children}

        if st not in statements:
            statements[st] = {"type": st, "sections": [], "identities": []}
            order.append(st)
        parent_id = _s(r.get("section"))
        if not parent_id:
            node["children"] = []
            statements[st]["sections"].append(node)
            sections[(st, node["node_id"])] = node
        else:
            parent = sections.get((st, parent_id))
            if parent is None:
                raise TemplateSheetError(
                    f"Row {row_no}: Section '{parent_id}' has no heading row above it in "
                    f"{title}. A section row (blank Section) must come before its lines.")
            parent["children"].append(node)

    # --- identities ---
    ident_row_of: dict[str, int] = {}
    has_identities = "Identities" in wb.sheetnames
    if has_identities:
        for r in _cells(wb["Identities"], IDENTITY_COLUMNS, "Identities"):
            n = r["_row"]
            title, ident_id, lhs = _s(r.get("statement")), _s(r.get("id")), _s(r.get("lhs"))
            if not ident_id and not lhs:
                continue
            if not lhs:
                raise TemplateSheetError(
                    f"Identities row {n}: Left (canonical key) is required for '{ident_id}'.")
            st = _statement_type(title) or _slug(title)
            if st not in statements:
                raise TemplateSheetError(
                    f"Identities row {n}: statement '{title}' is not in the Template sheet.")
            terms = _keys(r.get("rhs"))
            for k in [lhs, *terms]:
                if k not in known:
                    raise TemplateSheetError(
                        f"Identities row {n}: '{k}' is not a canonical key in this template.")
            op = _s(r.get("op")).lower() or "sum"
            if op not in _OPS:
                raise TemplateSheetError(
                    f"Identities row {n}: Calculation must be one of {', '.join(sorted(_OPS))}, "
                    f"not '{op}'.")
            ident = {"id": ident_id or f"{st}_identity_{n}", "lhs": lhs,
                     "rhs": {"op": op, "children": terms}}
            for field, header in (("tolerance_abs", "Tolerance (abs)"),
                                  ("tolerance_rel", "Tolerance (rel)")):
                tol = _tolerance(r.get(field), n, header)
                if tol is not None:
                    ident[field] = tol
            ident_row_of[ident["id"]] = n
            statements[st]["identities"].append(ident)

    definition = {"schema_version": 1, "template_key": template_key, "name": name,
                  "statements": [statements[st] for st in order]}
    carried = (_carry_forward(definition, previous, row_of, ident_row_of, has_identities)
               if previous else [])
    return definition, carried


def _carry_forward(definition: dict, previous: dict, row_of: dict[str, int],
                   ident_row_of: dict[str, int], has_identities: bool) -> list[str]:
    """Put back onto ``definition`` what ``previous`` says and the sheet cannot; refuse what it
    cannot put back faithfully. Returns one line per kind of thing carried, for the upload to
    report. See :func:`import_workbook`."""
    import copy

    src = previous.get("template_key") or "the current template"
    keys = {n["canonical_key"] for n in _nodes_of(definition)}
    prev_nodes = {n.get("canonical_key"): n for n in _nodes_of(previous)}
    prev_by_id = {n.get("node_id"): n for n in _nodes_of(previous) if n.get("node_id")}
    carried: list[str] = []

    # --- lines: residual rollups, and labels in a locale the sheet has no column for ---
    residuals = 0
    locales: set[str] = set()
    for node in _nodes_of(definition):
        key = node["canonical_key"]
        old = prev_nodes.get(key)
        if old is None:
            # A line the workbook added — unless it is a residual line under a new key: the same
            # Node ID, the old key gone. Carried by key, its residual would be dropped in silence.
            was = prev_by_id.get(node.get("node_id"))
            if (was is not None and was.get("canonical_key") not in keys
                    and _rollup_extras(was.get("rollup") or {})):
                raise TemplateSheetError(
                    f"Row {row_of[key]}: '{key}' has the Node ID of the residual line "
                    f"'{was['canonical_key']}' in '{src}' "
                    f"({_describe(_rollup_extras(was['rollup']))}). Renaming it here would "
                    f"publish it as a plain line and drop the residual. Restore the canonical key "
                    f"'{was['canonical_key']}', or rename it in the JSON template.")
            continue
        row = row_of[key]
        for loc, text in (old.get("label_i18n") or {}).items():
            if loc not in ("en", *_LOCALES) and text and loc not in node["label_i18n"]:
                node["label_i18n"][loc] = text
                locales.add(loc)
        old_roll = old.get("rollup") or {}
        extras = _rollup_extras(old_roll)
        if not extras:
            continue
        old_op, old_children = old_roll.get("op") or "sum", list(old_roll.get("children") or [])
        new_roll = node.get("rollup")
        if (new_roll is None or new_roll["op"] != old_op
                or list(new_roll["children"]) != old_children):
            raise TemplateSheetError(
                f"Row {row}: '{key}' is a residual line in '{src}' ({_describe(extras)}), and the "
                f"workbook has no column for that. Its Kind, Calculation or 'Calculated from' was "
                f"changed, which would publish it as a plain calculation and drop the residual. "
                f"Restore Kind '{KIND_CALCULATED}', Calculation '{old_op}' and 'Calculated from' "
                f"{', '.join(old_children) or '(blank)'}, or make this edit in the JSON template.")
        rtk = extras.get("reported_total_key")
        if rtk and rtk not in keys:
            raise TemplateSheetError(
                f"Row {row}: '{key}' is checked against the reported total '{rtk}', which is no "
                f"longer in the Template sheet. Keep the '{rtk}' row, or make this edit in the "
                f"JSON template.")
        new_roll.update(copy.deepcopy(extras))
        residuals += 1
    if residuals:
        carried.append(f"the reported-total and magnitude fields of {residuals} rollup(s)")
    if locales:
        carried.append(f"labels in {', '.join(sorted(locales))}")

    # --- statements: headings and anything else declared beside the section tree ---
    prev_st = {_sheet_statement(st.get("type")): st for st in previous.get("statements") or ()}
    headed = 0
    for st in definition["statements"]:
        old = prev_st.get(st["type"])
        if not old:
            continue
        kept = False
        for k, v in old.items():
            if k in ("type", "sections", "identities") or v in (None, "", [], {}):
                continue
            st[k] = copy.deepcopy(v)
            kept = True
        headed += kept
    if headed:
        carried.append(f"heading(s) of {headed} statement(s)")

    # --- identities: a deleted sheet is not "no identities", and a residual rhs is kept ---
    prev_idents = [i for st in previous.get("statements") or () for i in st.get("identities") or ()]
    if prev_idents and not has_identities:
        raise TemplateSheetError(
            f"The workbook has no 'Identities' sheet, but '{src}' declares {len(prev_idents)} "
            f"identit{'y' if len(prev_idents) == 1 else 'ies'} "
            f"({', '.join(str(i.get('id')) for i in prev_idents)}); publishing it would drop "
            f"every one. Restore the sheet from a fresh download — to remove them deliberately, "
            f"keep the sheet with only its header row.")
    prev_by_id = {i.get("id"): i for i in prev_idents}
    for st in definition["statements"]:
        for ident in st["identities"]:
            old_rhs = (prev_by_id.get(ident["id"]) or {}).get("rhs") or {}
            extras = _rollup_extras(old_rhs)
            if not extras:
                continue
            if (ident["rhs"]["op"] != (old_rhs.get("op") or "sum")
                    or ident["rhs"]["children"] != list(old_rhs.get("children") or [])):
                raise TemplateSheetError(
                    f"Identities row {ident_row_of[ident['id']]}: '{ident['id']}' is "
                    f"{_describe(extras)} in '{src}', which the sheet cannot express, and its "
                    f"Calculation or right-hand side was changed. Restore them, or make this "
                    f"edit in the JSON template.")
            ident["rhs"].update(copy.deepcopy(extras))

    # --- the template's own blocks: KPIs, cross-statement ties ---
    for k, v in previous.items():
        if k in ("schema_version", "template_key", "name", "statements") or not v:
            continue
        if k == "kpis":
            inters = list(v.get("intermediates") or ())
            ratios = list(v.get("ratios") or ())
            if not inters and not ratios:
                continue
            names = {i.get("key") for i in inters}
            broken = []
            for item in inters + ratios:
                for term in [*(item.get("terms") or ()), *(item.get("numerator") or ()),
                             *(item.get("denominator") or ())]:
                    for ref in [term.get("key"), *(term.get("fallback_keys") or ())]:
                        if ref and ref not in keys and ref not in names:
                            broken.append(f"{item.get('key')} uses {ref}")
            if broken:
                raise TemplateSheetError(
                    f"'{src}' has KPI(s) built on line(s) this workbook no longer has: "
                    f"{'; '.join(broken)}. A workbook cannot edit KPIs, so publishing it would "
                    f"leave them unable to compute. Keep those rows, or remove the KPIs in the "
                    f"JSON template first.")
            carried.append(f"{len(ratios)} KPI ratio(s) and {len(inters)} KPI intermediate(s)")
        elif k == "cross_statement_ties":
            broken = [f"{t.get('id')} uses {side.get('key')}" for t in v
                      for side in (t.get("lhs") or {}, t.get("rhs") or {})
                      if side.get("key") and side.get("key") not in keys]
            if broken:
                raise TemplateSheetError(
                    f"'{src}' has cross-statement tie(s) on line(s) this workbook no longer has: "
                    f"{'; '.join(broken)}. Keep those rows, or remove the ties in the JSON "
                    f"template first.")
            carried.append(f"{len(v)} cross-statement tie(s)")
        else:
            carried.append(f"'{k}'")
        definition[k] = copy.deepcopy(v)
    return carried
