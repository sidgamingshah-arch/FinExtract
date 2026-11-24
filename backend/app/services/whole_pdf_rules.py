"""THE RULES' READING BESIDE THE MODEL'S, on the whole-PDF route — what `stages.note_sourced` asks
before it writes a figure there.

On that route (`stages.whole_pdf`) the model reads the PDF file itself and its rows replace the
face rows the rules read, while the note tables the rules read stay in `doc.notes`. So
`note_sourced` reads its declared notes as it does on the rules route, and fills what the model
left empty: a blank Level 3 column the rules read as 0, the note parts of a request that failed.
Three things keep that a FILL and never a second reading of what the model already answered:

  1. A SLOT THE MODEL ANSWERED ON ANY OF ITS ROWS IS THE MODEL'S (`model_row_holding`). The stage
     writes one row per line AND BASIS, so a line answered for the group and the company has two
     rows, while `note_sourced` looks a line up by key and gets the last of them. Asked of that
     one row, "did the model answer the group's figure?" read the company's row, found no group
     slot, and the rules wrote the group's figure a second time beside the model's.
  2. A FIGURE THE RULES WRITE GOES ON A ROW OF THEIR OWN (`own_row`), never into a row the model
     wrote. A model row is stamped as the model's (`confidence.method`), so a rules figure put into
     one would read as the model's, and every later "did the model answer this?" would say yes.
  3. A NOTE FIGURE THE MODEL PLACED ON ANOTHER LINE IS NOT READ AGAIN (`drop_cited`). The model
     cites the page each figure is on and the figure as printed; a note row on that page printing
     that figure is the one it read. If the rules then read the same row for another line — a
     sibling part the model left empty — one printed amount would count twice in the parent. That
     is refused and logged. A ZERO is never refused: it adds nothing to any sum, and it is what a
     blank Level 3 column reads as.

THE TEST FOR "THE SAME PRINTED FIGURE" IS THE CELL WHERE IT CAN BE TOLD, else page and amount.
Each figure the model placed is put on the rules' note cell it sits on: the cell on its page that
prints its amount and lies at the box `services.whole_pdf.locate` drew for it (the two are drawn
from the same words, so they agree to within rounding). Only that cell is "placed". One row can
print one amount twice — a fully provided related-party receivable, gross = allowance: 000709's
河北承钢工贸有限责任公司 565,528,459.41 in both columns — and matching on page and amount alone
dropped the rules' allowance as "placed on the gross part", so Trade Receivables (Related Parties)
was published 565,528,459.41 too high. A figure that cannot be put on one cell — no box, no cell
near it, two cells equally near — still holds every cell of its page and amount: that errs on the
side of not counting one figure twice. The caption is the model's own words and is never compared
(`stages.note_sourced._row_identity` compares captions, which is why it does not catch this).

EVERY FUNCTION HERE IS A NO-OP ON EVERY OTHER ROUTE: each reads only rows the whole-PDF stage
wrote (`ROW_FLAG`), and a run with none — the rules route, the line-item model route — finds none.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

#: The flag `stages.whole_pdf` puts on every row it writes (`stages.whole_pdf.FLAG`; a test pins
#: the two together). Spelled here so this module does not import the stage.
ROW_FLAG = "whole_pdf"
#: On a row the rules made beside the model's (`own_row`): its figures are the rules' reading of
#: the notes on this route, for a slot the model left empty.
RULES_FLAG = "whole_pdf:rules_reading"


def is_model_row(row) -> bool:
    """Whether the whole-PDF stage wrote this row: its flag, and the model's mapping method."""
    conf = getattr(row, "confidence", None)
    if conf is None or ROW_FLAG not in (getattr(conf, "flags", None) or ()):
        return False
    return str(getattr(conf, "method", "") or "").lower().endswith("llm")


def _basis(ev) -> str:
    b = getattr(ev, "basis", "")
    return str(getattr(b, "value", b) or "")


def _held(row, basis: str, period: str):
    for ev in (getattr(row, "values", None) or {}).values():
        if (_basis(ev) == basis and str(getattr(ev, "period_label", "") or "") == period
                and ev.value is not None):
            return ev
    return None


def model_row_holding(doc, key: str, basis: str, period: str):
    """The row the model wrote for `key` that holds its `basis`/`period` figure, or None."""
    for row in getattr(doc, "line_items", None) or ():
        if row.canonical_key == key and is_model_row(row) and _held(row, basis, period):
            return row
    return None


def own_row(doc, row, key: str, label: str | None = None):
    """The row the rules write `key`'s figure on: `row` itself unless the model wrote it — then
    a row of the key the model did not write, made and appended to the document if there is none.
    `row` None stays None (the caller makes the row, as it always has)."""
    if row is None or not is_model_row(row):
        return row
    from app.core.models.line_item import LineItem

    mine = next((li for li in doc.line_items
                 if li.canonical_key == key and not is_model_row(li)), None)
    if mine is None:
        mine = LineItem(source_label=label or key, canonical_key=key)
        mine.confidence.flags.append(RULES_FLAG)
        doc.line_items.append(mine)
    return mine


def discard_if_empty(doc, row) -> bool:
    """Take a row `own_row` made back out of the document when it was given no figure — every
    slot was the model's — so no empty row of the line is left beside the model's. Only such a
    row (`RULES_FLAG`): a row any other writer made is left exactly as it is."""
    if row is None or RULES_FLAG not in (row.confidence.flags or ()):
        return False
    if any(ev.value is not None for ev in (row.values or {}).values()):
        return False
    doc.line_items[:] = [li for li in doc.line_items if li is not row]
    return True


def rules_face_unit(ctx, doc, basis: str):
    """THE UNIT THE FACE IS PRINTED IN, as the rules read it, for putting a note row into it — on
    the whole-PDF route only, None everywhere else.

    `note_sourced` puts each note row into the face's unit before it adds it
    (`stages.note_sourced._in_face_units`), and on the rules route the face rows carry the unit
    their page declares. The model's rows carry none (no scale read, no currency), so a note row
    printed in 万元 under a face in 元 was compared with "no declared unit" and read as printed:
    measured on 688008, key-management pay came out 5,849.45 where the rules route publishes
    58,494,500.00. The rules' own face rows, set aside by the whole-PDF stage
    (`PipelineContext.whole_pdf_rules_rows`), still carry the page's unit, so the conversion is
    decided against theirs: the most common DECLARED money unit of that basis's face rows, with
    the document's currency where the face names none.

    ONLY THE CONVERSION. The written slot keeps the unit the model's rows carry (the caller's
    `_statement_unit`), so a rules figure and the model's figures beside it are stamped alike.
    """
    rows = getattr(ctx, "whole_pdf_rules_rows", None)
    if not rows:
        return None
    from app.core.models.enums import PrintedIn
    from app.services.units import MONEY, measure_of, scale_declared

    counts: dict[tuple[str, str], int] = {}
    first: dict[tuple[str, str], object] = {}
    for li in rows:
        if getattr(li, "printed_in", None) != PrintedIn.FACE:
            continue
        for ev in (li.values or {}).values():
            if (getattr(ev, "column_index", None) is not None or ev.value is None
                    or _basis(ev) != basis):
                continue
            uc = getattr(ev, "unit_ctx", None)
            if uc is None or measure_of(uc) != MONEY or not scale_declared(uc):
                continue
            key = (uc.currency or "", str(uc.scale_factor))
            counts[key] = counts.get(key, 0) + 1
            first.setdefault(key, uc)
    if not counts:
        return None
    best = max(counts.values())
    unit = first[next(k for k in first if counts[k] == best)]
    declared = getattr(doc, "unit_context", None)
    if not unit.currency and declared is not None and getattr(declared, "currency", None):
        unit = unit.model_copy(update={"currency": declared.currency})
    return unit


def _amount(value) -> Decimal | None:
    try:
        return abs(Decimal(str(value))) if value not in (None, "") else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _page(prov) -> int | None:
    if prov is None:
        return None
    page = prov.get("page_index") if isinstance(prov, dict) else getattr(prov, "page_index", None)
    try:
        return int(page) if page is not None else None
    except (TypeError, ValueError):
        return None


#: How near the box `locate` drew for a model figure must be to a rules note cell, as a fraction of
#: the page, for the figure to be put on that cell; and how much nearer the nearest of two cells
#: within it must be for the figure to be put on it rather than on both.
_NEAR = 0.02
_CLEARLY_NEARER = 0.006


class Placed(dict):
    """`{page: {amount: {line keys}}}` — and, in `cells`, WHICH printed cell each line's figure
    sits on: `{(page, amount): {key: {cell, …}}}`, a cell as `note_sourced.cell_of` gives it, or
    None where the figure could not be put on one cell (it then holds every cell of its amount on
    that page)."""

    def __init__(self):
        super().__init__()
        self.cells: dict[tuple, dict[str, set]] = {}


def _box_corner(prov) -> tuple[float, float] | None:
    if prov is None:
        return None
    get = prov.get if isinstance(prov, dict) else (lambda k, d=None: getattr(prov, k, d))
    box = get("value_bbox") or get("bbox")
    if box is None:
        return None
    bget = box.get if isinstance(box, dict) else (lambda k, d=None: getattr(box, k, d))
    try:
        return float(bget("x0")), float(bget("y0"))
    except (TypeError, ValueError):
        return None


def _note_cells(doc) -> dict[tuple, list[tuple]]:
    """`{(page, amount): [(cell, (x0, y0)), …]}` — every printed note figure the rules read."""
    from app.services.note_sourced import cell_of

    out: dict[tuple, list[tuple]] = {}
    for table in getattr(doc, "notes", None) or ():
        for row in getattr(table, "items", None) or ():
            for ev in (getattr(row, "values", None) or {}).values():
                prov = getattr(ev, "provenance", None)
                cell, corner = cell_of(prov), _box_corner(prov)
                amount = _amount(getattr(ev, "value", None))
                if cell is None or corner is None or not amount:
                    continue
                out.setdefault((cell[0], amount), []).append((cell, corner))
    return out


def _on_cell(cells: list[tuple], corner) -> tuple | None:
    """The one note cell a figure placed at `corner` is, or None."""
    if corner is None or not cells:
        return None
    near = sorted((max(abs(c[0] - corner[0]), abs(c[1] - corner[1])), cell)
                  for cell, c in cells)
    near = [(d, cell) for d, cell in near if d <= _NEAR]
    if not near:
        return None
    if len(near) > 1 and near[1][0] - near[0][0] < _CLEARLY_NEARER:
        return None
    return near[0][1]


def model_figures(doc) -> Placed:
    """`{page: {amount: {line keys}}}` — every nonzero figure the model cited, as printed (unsigned),
    on the page it was read from, and the lines it put it on; with `cells`, the note cell each sits
    on where it can be told (`Placed`).

    Both the figure and, for a figure the model added up from printed rows, EACH ROW: the rows of a
    sum are in its trail (`stages.whole_pdf._citation`), each with its own page, amount and box, and
    a row the model counted inside one line's sum is as much "placed" as a figure cited whole.
    Empty on every route but the whole-PDF one.
    """
    out = Placed()
    rows = [row for row in getattr(doc, "line_items", None) or ()
            if is_model_row(row) and row.canonical_key]
    if not rows:
        return out
    printed = _note_cells(doc)

    def add(prov, amount, key) -> None:
        page = _page(prov)
        if page is None or amount is None or amount == 0:
            return
        out.setdefault(page, {}).setdefault(amount, set()).add(key)
        cell = _on_cell(printed.get((page, amount), []), _box_corner(prov))
        out.cells.setdefault((page, amount), {}).setdefault(key, set()).add(cell)

    for row in rows:
        key = row.canonical_key
        for ev in (row.values or {}).values():
            add(getattr(ev, "provenance", None),
                _amount(ev.value_raw if ev.value_raw is not None else ev.value), key)
        for trail in (row.derivation or {}).values():
            if not isinstance(trail, dict):
                continue
            for inp in trail.get("inputs") or ():
                if isinstance(inp, dict) and inp.get("counted", True):
                    add(inp.get("provenance"), _amount(inp.get("value")), key)
                    # A row put into the statements' scale: the figure AS PRINTED is the one a note
                    # row on the page prints (`stages.whole_pdf._citation`).
                    if inp.get("printed") is not None:
                        add(inp.get("provenance"), _amount(inp.get("printed")), key)
    return out


def cited_for_another_line(figures: dict, page: int | None, amount, key: str) -> set[str]:
    """The OTHER lines the model put this printed figure on (empty when none, or for a zero)."""
    value = _amount(amount)
    if page is None or not value:
        return set()
    return set(figures.get(page, {}).get(value, ())) - {key}


def _placed_here(figures, page, amount, others: set[str], hit_cell) -> set[str]:
    """The lines in `others` whose figure at `(page, amount)` is the cell `hit_cell` — or may be:
    a figure that could not be put on one cell holds them all, and so does a hit with no cell."""
    cells = getattr(figures, "cells", None)
    value = _amount(amount)
    if cells is None or hit_cell is None or value is None:
        return others
    placed = cells.get((page, value), {})
    return {k for k in others
            if not placed.get(k) or None in placed[k] or hit_cell in placed[k]}


def drop_cited(hits: list, figures: dict, key: str, log=None) -> list:
    """`hits` (`services.note_sourced.NoteRowHit`, read BEFORE any rescale, so each amount is the
    figure as printed) without a note row whose figure the model placed on another line — the very
    cell, where the model's figure could be put on one (`Placed.cells`)."""
    if not figures or not hits:
        return hits
    from app.services.note_sourced import cell_of

    kept = []
    for hit in hits:
        prov = getattr(getattr(hit, "value", None), "provenance", None)
        page = _page(prov)
        others = cited_for_another_line(figures, page, getattr(hit, "amount", None), key)
        if others:
            others = _placed_here(figures, page, getattr(hit, "amount", None), others,
                                  cell_of(prov))
        if others:
            if log is not None:
                log(f"note_sourced:{key}[{hit.basis}:{hit.period}]: note row {hit.caption!r} "
                    f"{hit.amount} (page {page + 1}) NOT read — the model placed this figure on "
                    f"{', '.join(sorted(others))}")
            continue
        kept.append(hit)
    return kept


def drop_cited_prose(hits: list, figures: dict, key: str, scale=None, log=None) -> list:
    """The same for sentences (`services.note_sourced.ProseHit`): a sentence states its figure in
    full, and the model may have given it in full or in the statements' unit, so both are asked."""
    if not figures or not hits:
        return hits
    try:
        factor = Decimal(str(scale)) if scale else None
    except (InvalidOperation, ValueError, TypeError):
        factor = None
    kept = []
    for hit in hits:
        page = _page(getattr(hit, "provenance", None))
        amount = getattr(hit, "amount", None)
        others = cited_for_another_line(figures, page, amount, key)
        if not others and factor and factor != 1 and _amount(amount):
            others = cited_for_another_line(figures, page, _amount(amount) / factor, key)
        if others:
            if log is not None:
                log(f"note_sourced:{key}[{hit.period}]: prose {amount} (page "
                    f"{page + 1 if page is not None else '?'}) NOT read — the model placed this "
                    f"figure on {', '.join(sorted(others))}")
            continue
        kept.append(hit)
    return kept
