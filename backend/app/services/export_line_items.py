"""The LINE ITEMS sheet — every configured line, its sub-lines, and the arithmetic that links them.

WHAT THIS SHEET IS FOR, and why the statement sheets cannot be it. A statement sheet is shaped by
the TEMPLATE: it carries the nodes the template declares, in the order a reader expects to see a
balance sheet. The 77 SUB-LINE ITEMS are not template nodes — they are the layer that corresponds
to a printed note row, the layer `services.working_view` drops and the layer the model is actually
asked about — so a reader of the statement sheets can see that depreciation is 587,417 and has
nowhere to go to find out which two note rows that is. This sheet is that somewhere.

THREE THINGS IT DOES:

  1. EVERY CONFIGURED LINE, including all the sub-lines, in configuration order with each sub-line
     under the line it belongs to. A line the filing never printed is still listed, blank — the
     configuration is what it is whether or not this filing exercised it.
  2. A LINE BUILT FROM SEVERAL FIGURES CARRIES A LIVE FORMULA, so a reader can select the cell and
     see the arithmetic rather than a number with a comment about it. Two sources:
       * declared `terms` — the line's own arithmetic over OTHER line items, written as a
         reference to their rows on this sheet. This is the main-to-sub link, in the workbook.
       * a derivation's note rows — the individual printed rows a figure was assembled from, each
         emitted as its own indented row with its note, page and amount, the parent cell summing
         them.
  3. IT IS THE WORKBOOK'S ONE COPY OF EVERY FIGURE. The statement sheets reference it
     (`export._emit_nodes`), so a number appears once and every other appearance points here.

THE HONESTY RULE, AND IT IS ABSOLUTE. `tests/test_export_honesty.py` has always required that the
number in a cell is the number the server computed, and a live formula is a way to break that: a
sum over four rows where the server required all four and got two would recalculate to a total the
server refused to publish. So a formula is written ONLY when its operands are on this sheet AND
evaluating it reproduces the server's own figure to within half a unit. Where it does not, the
literal is written and the reason goes in the cell comment instead. `_terms_formula` and
`_sum_formula` are the only places that decide this, and both return None rather than guess.
"""
from __future__ import annotations

from app.services.derivation import merge_for_basis
from app.services.periods import concept_value

SHEET = "Line Items"
SHEET_REF = "'" + SHEET + "'!"

# Indent per level: a line, its sub-lines, and a sub-line's individual note rows.
_INDENT = {0: 0, 1: 2, 2: 4}

_HEAD = {
    "Line item": {"zh": "项目", "ar": "البند", "fr": "Poste"},
    "Key": {"zh": "键", "ar": "المفتاح", "fr": "Clé"},
    "Kind": {"zh": "类型", "ar": "النوع", "fr": "Type"},
    "Note": {"zh": "附注", "ar": "إيضاح", "fr": "Note"},
    "How": {"zh": "来源方式", "ar": "الطريقة", "fr": "Méthode"},
    "Source": {"zh": "来源", "ar": "المصدر", "fr": "Source"},
}


def _h(term: str, locale: str) -> str:
    return _HEAD.get(term, {}).get(locale, term) if locale != "en" else term


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _tree(items: list) -> list[tuple[object, int]]:
    """(item, depth) in configuration order, each sub-line directly under its parent.

    A part whose declared parent is not in the set is emitted at depth 0 rather than dropped: an
    orphan is a configuration fact worth seeing, and hiding it here would make the sheet disagree
    with the count of what is configured.
    """
    known = {getattr(i, "key", "") for i in items}
    by_parent: dict[str, list] = {}
    roots: list = []
    for it in items:
        parent = getattr(it, "parent", "") or ""
        if parent and parent in known:
            by_parent.setdefault(parent, []).append(it)
        else:
            roots.append(it)
    out: list[tuple[object, int]] = []
    for it in roots:
        out.append((it, 0))
        for kid in by_parent.get(getattr(it, "key", ""), []):
            out.append((kid, 1))
    return out


def _contributions(row: dict | None, basis: str) -> list[dict]:
    """The note rows one figure was assembled from, as the inspector renders them."""
    if not row or not row.get("derivation"):
        return []
    _formula, contributions = merge_for_basis(row["derivation"], basis)
    return contributions


def _terms_formula(terms, row_of: dict[str, int], values: dict[str, float | None],
                   col: str, target: float | None) -> str | None:
    """A declared-`terms` formula as an Excel expression, or None when it must not be written.

    None means "write the literal instead", and it is returned whenever the formula could disagree
    with what the server published: a referenced line that is not on this sheet, a term that is a
    bare constant with no ref to anchor it, or an evaluation that does not reproduce ``target``.
    A referenced line with no figure in this slot is SKIPPED rather than referenced — a blank cell
    reads as zero in Excel, and zero is only accidentally the same thing as absent — and the
    ``target`` check below is what then decides whether what is left is still the published figure.
    """
    if not terms or target is None:
        return None
    parts: list[str] = []
    running = 0.0
    for term in terms:
        ref = getattr(term, "ref", "") or ""
        const = getattr(term, "const", None)
        sign = int(getattr(term, "sign", 1) or 1)
        use_abs = bool(getattr(term, "abs", False))
        if const is not None and not ref:
            token, amount = f"{const:g}", float(const)
        else:
            if ref not in row_of:
                return None
            amount = values.get(ref)
            if amount is None:
                continue
            token = f"{SHEET_REF}{col}{row_of[ref]}"
        if use_abs:
            token, amount = f"ABS({token})", abs(amount)
        parts.append(("-" if sign < 0 else "+") + token)
        running += sign * amount
    if not parts or abs(running - target) > 0.5:
        return None
    expr = "".join(parts)
    return "=" + (expr[1:] if expr.startswith("+") else expr)


def _sum_formula(contributions: list[dict], period: str, col: str, first_row: int,
                 target: float | None, scale: float) -> str | None:
    """``=D31+D32`` over the note rows written beneath a line, or None when they do not add up.

    An ``alternatives`` rollup is exactly why the total is checked rather than assumed: it offers
    several rows and TAKES ONE, so summing its block would multiply a charge by the number of
    places the filing happened to disclose it. Those rows are marked not-counted and excluded here,
    and if what remains does not reproduce ``target`` no formula is written at all.
    """
    if target is None or len(contributions) < 2:
        return None
    refs: list[str] = []
    running = 0.0
    for offset, contribution in enumerate(contributions):
        counted = (contribution.get("counted") if period == "current"
                   else contribution.get("counted2")) is not False
        amount = _num(contribution.get("v1") if period == "current" else contribution.get("v2"))
        if not counted or amount is None:
            continue
        refs.append(f"{col}{first_row + offset}")
        running += amount * (scale if scale != 1.0 else 1.0)
    if len(refs) < 2 or abs(running - target) > 0.5:
        return None
    return "=" + "+".join(refs)


def _where(prov: dict | None) -> str:
    """One provenance as a reader's citation: the folio the publisher printed, else the sheet."""
    if not prov:
        return ""
    if prov.get("sheet") or prov.get("cell"):
        return " ".join(str(x) for x in (prov.get("sheet"), prov.get("cell")) if x)
    folio = prov.get("printed_page")
    if folio:
        return f"p. {folio}"
    page = prov.get("page_index")
    return f"sheet {page + 1}" if isinstance(page, int) else ""


def build_line_items_sheet(wb, line_item_set, rows: list[dict], *, period_cols, locale: str = "en",
                           scale: float = 1.0, num_fmt: str = "#,##0;(#,##0)",
                           units_caption: str | None = None) -> dict:
    """Write the sheet and return ``{(key, basis, period): (address, figure)}`` for linking.

    The map is what `export._emit_nodes` uses to make a statement cell point here instead of
    holding its own copy of the figure, so the two can never come to disagree.

    THE FIGURE TRAVELS WITH THE ADDRESS, and it has to. A statement sheet writes the COMPUTED
    value for a calculated node — `services.rollups.evaluate_rows`, which may legitimately differ
    from the extracted row this sheet reports, and when it does the difference is the finding the
    statement sheet records in a comment. Replacing that cell with a reference to this sheet would
    silently publish the other number. So the caller compares before it links, and keeps its own
    literal wherever the two disagree.
    """
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    items = list(getattr(line_item_set, "items", None) or [])
    ink = "1f2937"
    head_fill = PatternFill("solid", fgColor="243044")
    sub_fill = PatternFill("solid", fgColor="F5F7FA")
    right = Alignment(horizontal="right")

    ws = wb.create_sheet(SHEET[:31])
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("canonical_key"):
            by_key.setdefault(row["canonical_key"], []).append(row)

    # Column layout: Line item | Key | Kind | Note | [basis x period] | How | Source
    first_val = 5
    how_col = first_val + len(period_cols)
    src_col = how_col + 1

    ws.cell(1, 1, "Line items — every configured line, its sub-lines, and their arithmetic"
            ).font = Font(bold=True, size=12, color=ink)
    if units_caption:
        ws.cell(2, 1, units_caption).font = Font(size=9, color="8A94A6")
    hdr = 3
    names = [_h("Line item", locale), _h("Key", locale), _h("Kind", locale), _h("Note", locale)]
    names += [f"{b.capitalize()} {p}" for (b, p) in period_cols]
    names += [_h("How", locale), _h("Source", locale)]
    for ci, name in enumerate(names, start=1):
        cell = ws.cell(hdr, ci, name)
        cell.font = Font(bold=True, color="FFFFFF", size=9)
        cell.fill = head_fill
    ws.column_dimensions["A"].width = 54
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 11
    ws.column_dimensions["D"].width = 8
    for ci in range(first_val, src_col + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 16
    ws.freeze_panes = ws.cell(hdr + 1, 1)

    plan = _tree(items)

    def contributions_of(key: str) -> tuple[str | None, list[dict]]:
        """``(basis, note rows)`` for a line — the FIRST basis that has a block, and whose it is.

        Not a block per basis: the printed rows a figure was assembled from do not differ by basis
        on any shipped configuration, and emitting one per basis would list the same rows twice
        under different headings.

        THE BASIS COMES BACK WITH THEM, AND IT MUST. These amounts belong to one basis's columns.
        Writing them into every value column — which is what happened first — put the consolidated
        figure 306,456 under Standalone as well, inventing a standalone disclosure the filing never
        made, and the parent's own SUM stayed consolidated-only so the columns did not even agree
        with each other.
        """
        group = by_key.get(key) or []
        if not group:
            return None, []
        for (basis, _period) in period_cols:
            got = _contributions(group[0], basis)
            if len(got) > 1:
                return basis, got
        return None, []

    # ── PASS 1: where each line WILL be, so a formula can reference a row not yet written ─────
    #
    # Two passes rather than one because the arithmetic points in both directions: a line's terms
    # can reference a sub-line declared after it, and a one-pass writer would have to emit that
    # reference before knowing its address.
    row_of: dict[str, int] = {}
    cursor = hdr + 1
    for item, _depth in plan:
        row_of[getattr(item, "key", "")] = cursor
        cursor += 1 + len(contributions_of(getattr(item, "key", ""))[1])

    def value_of(key: str, basis: str, period: str) -> float | None:
        group = by_key.get(key) or []
        if not group:
            return None
        return _num(concept_value(group, basis, period))

    def presented(value: float | None) -> float | None:
        if value is None:
            return None
        return round(value * scale) if scale != 1.0 else value

    # ── PASS 2: write ────────────────────────────────────────────────────────────────────────
    r = hdr + 1
    for item, depth in plan:
        key = getattr(item, "key", "")
        group = by_key.get(key) or []
        row = group[0] if group else None
        contrib_basis, contributions = contributions_of(key)
        terms = list(getattr(item, "terms", None) or ())

        label = ws.cell(r, 1, str(getattr(item, "label", "") or key))
        label.alignment = Alignment(indent=_INDENT.get(depth, 0))
        label.font = Font(bold=(depth == 0), color=ink, size=10)
        ws.cell(r, 2, key).font = Font(size=8, color="8A94A6")
        ws.cell(r, 3, str(getattr(item, "type", "") or "extracted")).font = Font(size=9,
                                                                                color="8A94A6")
        ws.cell(r, 4, (row or {}).get("note") or "")
        if depth:
            for ci in range(1, src_col + 1):
                ws.cell(r, ci).fill = sub_fill

        unwritten: list[str] = []
        for (basis, period) in period_cols:
            ci = first_val + period_cols.index((basis, period))
            col = get_column_letter(ci)
            target = presented(value_of(key, basis, period))

            formula = None
            if terms:
                values = {k: presented(value_of(k, basis, period)) for k in row_of}
                formula = _terms_formula(terms, row_of, values, col, target)
                if formula is None and target is not None:
                    unwritten.append(
                        f"{basis}:{period} — this line's declared arithmetic is not written as a "
                        f"live reference here, because evaluating it does not reproduce the figure "
                        f"the server published (a term is absent, or its line carries no figure "
                        f"in this period). The number in the cell is the server's.")
            if formula is None and contributions and basis == contrib_basis:
                formula = _sum_formula(contributions, period, col, r + 1, target, scale)

            cell = ws.cell(r, ci, formula if formula is not None else target)
            cell.number_format = num_fmt
            cell.alignment = right
            if depth == 0:
                cell.font = Font(bold=True)

        ws.cell(r, how_col, (row or {}).get("mapping_method") or ("" if row else "not extracted")
                ).font = Font(size=9, color="8A94A6")
        if row:
            values_list = row.get("values") or []
            ws.cell(r, src_col,
                    _where(values_list[0].get("provenance") if values_list else None)
                    ).font = Font(size=9, color="8A94A6")
        if unwritten:
            label.comment = Comment("\n\n".join(unwritten), "FinExtract")
        r += 1

        # The individual printed rows, each addressable and each with its own page.
        for contribution in contributions:
            caption = ws.cell(r, 1, str(contribution.get("label") or ""))
            caption.alignment = Alignment(indent=_INDENT[2])
            caption.font = Font(size=9, color="4B5563", italic=True)
            ws.cell(r, 3, "note row").font = Font(size=8, color="8A94A6")
            for (basis, period) in period_cols:
                ci = first_val + period_cols.index((basis, period))
                # ONLY the basis these rows were read under. Another basis's column is left blank
                # rather than filled with this one's figure — see `contributions_of`.
                amount = (presented(_num(contribution.get("v1") if period == "current"
                                         else contribution.get("v2")))
                          if basis == contrib_basis else None)
                cell = ws.cell(r, ci, amount)
                cell.number_format = num_fmt
                cell.alignment = right
                cell.font = Font(size=9, color="4B5563")
            counted = contribution.get("counted") is not False
            # "not taken" is not a footnote: an `alternatives` rollup lists rows it did NOT add,
            # and a reader adding the column up needs to know which ones those are.
            ws.cell(r, how_col, "counted" if counted else "not taken").font = Font(size=8,
                                                                                  color="8A94A6")
            ws.cell(r, src_col, _where(contribution.get("source")
                                       or contribution.get("source2"))
                    ).font = Font(size=9, color="8A94A6")
            if contribution.get("excerpt"):
                caption.comment = Comment(str(contribution["excerpt"]), "FinExtract")
            r += 1

    cells: dict[tuple[str, str, str], tuple[str, float | None]] = {}
    for (basis, period) in period_cols:
        col = get_column_letter(first_val + period_cols.index((basis, period)))
        for key, at in row_of.items():
            cells[(key, basis, period)] = (f"{SHEET_REF}{col}{at}",
                                           presented(value_of(key, basis, period)))
    return cells
