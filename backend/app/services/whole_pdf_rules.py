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

THE FACE AND SETUP LINES THE MODEL LEFT EMPTY are filled here too (`fill_from_rules`, called once by
the stage): the rules' face rows, set aside unmapped, are mapped and signed as the rules route maps
and signs them, and each slot of an asked line no model row holds takes the rules' published figure,
under the same three guards (see the section "the lines the model left empty" below). Two more keep
a filled face figure from being counted beside the model's own reading of it: a face cell the rules
split out of its note is filled as one unit (`_split_slot`), and a filled face row is settled
against the pieces of it the model gave on other lines (`settle_against_model_pieces`, called by
`stages.note_sourced`). A filled figure in a section that does not add up goes to review
(`review_beside_a_difference`, called by `stages.structural`).

EVERY FUNCTION HERE IS A NO-OP ON EVERY OTHER ROUTE: each reads only rows the whole-PDF stage
wrote (`ROW_FLAG`) or set aside (`PipelineContext.whole_pdf_rules_rows`), and a run with none — the
rules route, the line-item model route — finds none.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.services import review_state as _review_state

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


def _row_cells(rows) -> dict[tuple, list[tuple]]:
    """`{(page, amount): [(cell, (x0, y0)), …]}` — every printed figure of these rows (the rules'
    face rows), as `_note_cells` gives the notes'."""
    from app.services.note_sourced import cell_of

    out: dict[tuple, list[tuple]] = {}
    for row in rows or ():
        for ev in (getattr(row, "values", None) or {}).values():
            prov = getattr(ev, "provenance", None)
            cell, corner = cell_of(prov), _box_corner(prov)
            raw = ev.value_raw if getattr(ev, "value_raw", None) is not None else ev.value
            amount = _amount(raw)
            if cell is None or corner is None or not amount:
                continue
            out.setdefault((cell[0], amount), []).append((cell, corner))
    return out


def model_figures(doc, printed: dict | None = None) -> Placed:
    """`{page: {amount: {line keys}}}` — every nonzero figure the model cited, as printed (unsigned),
    on the page it was read from, and the lines it put it on; with `cells`, the note cell each sits
    on where it can be told (`Placed`) — or the cell of `printed` (`{(page, amount): [(cell,
    corner)]}`, the note cells when it is not given).

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
    if printed is None:
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


# ── the lines the model left empty ───────────────────────────────────────────────────────────────
#
# ON THE WHOLE-PDF ROUTE THE RULES' FACE ROWS WERE ONLY SET ASIDE (`PipelineContext.
# whole_pdf_rules_rows`, never mapped), and only the note parts were filled back from the rules
# (`note_sourced`). So a face line the model left out — or whose request failed — stayed BLANK
# where the rules had read a figure, and so did Periods and Statement Date, which the export takes
# from the column headings the rules' rows carry. Measured with SCRIPTED answers (not a model's):
# 688008's Periods and Statement Date blank; 佳明's statements blank wherever the script gave nothing.
#
# `fill_from_rules` fills those slots with what the RULES ROUTE WOULD PUBLISH for them: the set-aside
# rows put through the rules' own mapping (`map_line_items`, `residual`) and signs (`normalize`) on a
# copy, so each figure is the rules' published figure, sign included. The same three guards as for
# the note parts keep it a fill:
#
#   1. A SLOT ANY MODEL ROW OF THE LINE HOLDS IS THE MODEL'S, even where the rules read another
#      figure for it: a model answer that disagrees is kept.
#   2. EVERY FIGURE GOES ON A ROW OF ITS OWN: a copy of the rules' row, holding only the slots it
#      fills, flagged `RULES_FLAG` on the row and on each figure, with the rules' confidence.
#   3. A PRINTED FIGURE THE MODEL PLACED ON ANY LINE IS NOT READ AGAIN — the very cell where the
#      model's figure can be put on one (`_on_cell`, the face cells as well as the note cells),
#      else every cell of its page and amount. A zero is never refused.
#
# A SLOT THE CHECKS REFUSED THE MODEL'S FIGURE FOR (`whole_pdf:refused:*`, and nothing kept) is
# empty for this purpose only when the rules' reading of it passes the same checks
# (`services.whole_pdf_locate.Judge`, on the rules' cell): a caption the checks veto for the line,
# a figure in another column, is not put back by the rules. A part withheld because a sibling part
# holds its note money (`llm_note_row_withheld`) is not filled at all.
#
# ONE FACE CELL THE RULES SPLIT IS FILLED AS ONE UNIT, slot by slot (`_Splits`). Where the rules
# itemise a face cell out of its note (`stages.map_ontology.MapOntologyStage._split_from_disclosure`),
# they publish the cell as a REMAINDER on its own line plus each piece on a line of its own, all
# flagged `split_from:<line>`, and they count every piece. The model answers the same cell the other
# way: the face row whole, with any piece a `carved_from_face` line takes from the note shown but
# counted nowhere. Filled slot by slot, the two ways mix. Measured with scripted answers on China
# SCE 1966 (finance costs (1,492,343) / (921,124), note 7 lease interest 1,490 / 20,162):
#
#   * the model gave the face row whole, the lease piece was filled beside it: net interest
#     -1,493,833, over by 1,490 (and 20,162 in the prior year);
#   * the model gave the lease interest on its carved line and left the face row out, the
#     remainder -1,490,853 was filled: net interest short by the carved 1,490, counted nowhere.
#
# So: where the model holds the split line's slot, no piece split from it is filled in that slot
# (the model's figure is the face cell's whole); where the slot is empty and the model holds a piece
# on a line that will be carved, the split line takes the face cell's whole — the remainder plus
# those pieces' rules figures — flagged `SPLIT_WHOLE_FLAG`.

#: On a model figure that carried no column heading, given the rules' heading for its year — what
#: the export's Periods and Statement Date are read from.
PERIOD_FLAG = f"{ROW_FLAG}:period_display_from_rules"
_REFUSED = f"{ROW_FLAG}:refused:"
_WITHHELD = "llm_note_row_withheld"
#: The flag the review queue selects a doubtful figure on — on the SLOT in doubt
#: (`services.review_state`). A row flag means every slot of the row: the rules' caption doubt.
REVIEW_FLAG = _review_state.REVIEW_FLAG
#: On a split line filled with the face cell's whole, because the model holds a carved piece of it:
#: `whole_pdf:rules_split_filled_whole:<piece line>=<the rules' figure for it>`.
SPLIT_WHOLE_FLAG = f"{ROW_FLAG}:rules_split_filled_whole"
_SPLIT_FROM = "split_from:"
_DECOMPOSED_FROM = "note_decomposed_from:"
_RESIDUAL_OF_SPLIT = "note_residual_to_face_item"


def _slot_of(row, basis: str, period: str):
    """The figure `row` holds for `basis`/`period`, or None."""
    return _held(row, basis, period)


def _statement_pages(doc) -> set[int]:
    from app.core.models.enums import PageKind

    return {int(getattr(p, "index", -1)) for p in (getattr(doc, "pages", None) or ())
            if getattr(p, "kind", None) is PageKind.FACE}


def _would_be_carved(row, ev, defs: dict, statement_pages: set[int]) -> bool:
    """Whether `stages.note_sourced.carve_note_figures` will carve this figure: a line that declares
    `carved_from_face`, on a row that is not a face row, read off any page but a statement's — the
    figure is then shown on its line and added into no total. Asked before the carve has run."""
    from app.core.models.enums import PrintedIn
    from app.services.rollups import CARVED_FROM_FACE, SETTLED_FROM_FACE

    flags = getattr(getattr(ev, "confidence", None), "flags", None) or ()
    if CARVED_FROM_FACE in flags:
        return True
    item = defs.get(str(getattr(row, "canonical_key", "") or ""))
    if item is None or not getattr(item, "carved_from_face", False):
        return False
    if getattr(row, "printed_in", None) is PrintedIn.FACE or SETTLED_FROM_FACE in flags:
        return False
    page = _page(getattr(ev, "provenance", None))
    return page is not None and page not in statement_pages


class _Splits:
    """The rules' rows split out of one face cell, by the line the cell was printed for:
    `{line: (rows split from it, the face row they were split from or None)}`."""

    def __init__(self, published):
        self.rows: dict[str, list] = {}
        self.whole: dict[str, object] = {}
        for row in published:
            for flag in row.confidence.flags or ():
                flag = str(flag)
                if flag.startswith(_SPLIT_FROM):
                    self.rows.setdefault(flag[len(_SPLIT_FROM):], []).append(row)
                elif flag.startswith(_DECOMPOSED_FROM):
                    self.whole[flag[len(_DECOMPOSED_FROM):]] = row

    def of(self, row) -> str | None:
        """The line `row`'s figure was split out of, or None."""
        for flag in row.confidence.flags or ():
            if str(flag).startswith(_SPLIT_FROM):
                return str(flag)[len(_SPLIT_FROM):]
        return None

    def pieces(self, line: str) -> list:
        return [r for r in self.rows.get(line, ()) if r.canonical_key != line]

    def remainder(self, line: str):
        return next((r for r in self.rows.get(line, ()) if r.canonical_key == line), None)


def rules_published_rows(doc, ctx, rules_rows) -> list:
    """The rules' face rows AS THE RULES ROUTE MAPS AND SIGNS THEM: deep copies of `rules_rows` with
    the notes, put through `map_line_items`, `residual` and `normalize` on a copy of the document,
    with a context of their own (nothing is logged, nothing written back)."""
    import copy

    from app.stages.map_ontology import MapOntologyStage
    from app.stages.normalize import NormalizeStage
    from app.stages.residual import ResidualStage

    view = doc.model_copy(update={
        "line_items": [li.model_copy(deep=True) for li in rules_rows],
        "notes": [t.model_copy(deep=True) for t in (getattr(doc, "notes", None) or ())]})
    quiet = copy.copy(ctx)
    quiet.logs = []
    quiet.progress_cb = None
    quiet.step_cb = None
    quiet.only_stages = None
    quiet.whole_pdf = None
    quiet.whole_pdf_rules_rows = None
    # A COPY, NOT THE OUTPUT: the rules' residual gap describes the rules' own itemisation of a
    # section, which is not what the whole-PDF output holds. On this context residual records the
    # gap on its slot without sending it to review; `review_beside_a_difference` asks the output's
    # own check of that section and column instead.
    quiet.rules_copy = True
    for stage in (MapOntologyStage(), ResidualStage(), NormalizeStage()):
        view = stage.run(view, quiet)
    return list(view.line_items)


def _slots_held(doc) -> dict[str, set[tuple[str, str]]]:
    """`{key: {(basis, period)}}` — every slot some row of the document holds a figure or a text
    for (on this route, when the fill runs, every row is the model's)."""
    out: dict[str, set[tuple[str, str]]] = {}
    for li in getattr(doc, "line_items", None) or ():
        if not li.canonical_key:
            continue
        for ev in (li.values or {}).values():
            if ev.value is not None or getattr(ev, "value_text", None) is not None:
                out.setdefault(li.canonical_key, set()).add(
                    (_basis(ev), str(ev.period_label or "")))
    return out


def _slots_refused(doc) -> tuple[dict[tuple[str, str, str], list[str]], set[str]]:
    """`({(key, basis, period): [check, …]}, {keys withheld})` off the model's rows' flags."""
    refused: dict[tuple[str, str, str], list[str]] = {}
    withheld: set[str] = set()
    for li in getattr(doc, "line_items", None) or ():
        key = li.canonical_key
        if not key:
            continue
        for flag in li.confidence.flags or ():
            flag = str(flag)
            if flag.startswith(_REFUSED):
                bits = flag.split(":")
                if len(bits) >= 6:
                    refused.setdefault((key, bits[-3], bits[-2]), []).append(bits[2])
            elif flag.startswith(_WITHHELD):
                withheld.add(key)
    return refused, withheld


def _display_by_period(rows) -> tuple[dict[tuple[int, str], str], dict[str, str]]:
    """`({(page, period label): heading}, {period label: heading})` — the column heading the rules'
    rows carry for each year: on each page, the one most of its figures carry; and, for a page
    the rules read no row on, the first row's — the one the export's Periods and Statement Date
    take on the rules route (`api.routes.extractions._period_display`)."""
    counts: dict[tuple[int, str], dict[str, int]] = {}
    first: dict[str, str] = {}
    for li in rows:
        for ev in (li.values or {}).values():
            shown = getattr(ev, "period_display", None)
            if not shown or ev.value is None:
                continue
            label = str(ev.period_label or "")
            first.setdefault(label, shown)
            page = _page(getattr(ev, "provenance", None))
            if page is not None:
                per = counts.setdefault((page, label), {})
                per[shown] = per.get(shown, 0) + 1
    by_page = {spot: max(per, key=lambda s, per=per: (per[s], -list(per).index(s)))
               for spot, per in counts.items() if per}
    return by_page, first


class _RulesCheck:
    """Whether the rules' figure for a slot passes the checks the model's was refused by — the
    same `Judge`, over the rules' own reading of the filing, asked of the rules' cell."""

    def __init__(self, doc, ctx, rules_rows, judge):
        self._args = (doc, ctx, rules_rows, judge)
        self._judge = None

    def passes(self, row, ev, key: str) -> tuple[bool, str]:
        from app.services import whole_pdf as W
        from app.services import whole_pdf_locate as L

        if self._judge is None:
            doc, ctx, rules_rows, judge = self._args
            view = doc.model_copy(update={"line_items": list(rules_rows)})
            self._judge = L.Judge(view, ctx, ctx.line_items, getattr(judge, "texts", None) or {},
                                  log=lambda _line: None)
        prov = getattr(ev, "provenance", None)
        printed = ev.value_raw if ev.value_raw is not None else ev.value
        box = getattr(prov, "value_bbox", None) or getattr(prov, "bbox", None)
        r = W.Reading(key=key, basis=_basis(ev), period=str(ev.period_label or ""),
                      amount=Decimal(str(printed)), caption=row.source_label or "",
                      period_end="", part=1, check=W.Check("on_page", _page(prov)),
                      on_face=True, box=box)
        if self._judge.judge_one(r) is None:
            return False, "every figure it rests on is refused"
        verdict = self._judge.verdicts.get(id(r))
        if verdict is None:
            return True, ""
        if verdict.dropped:
            return False, str(verdict.dropped[0].get("why") or verdict.dropped[0].get("refused"))
        moved = [f for f in verdict.flags if f.startswith(f"{ROW_FLAG}:refiled")]
        if moved:
            return False, moved[0]
        return True, ""


def fill_from_rules(doc, ctx, judge=None, asked=None) -> dict:
    """Fill every slot of an asked line the model left empty with the rules' published figure for
    it, on rows of their own (see the section comment above); and give each model figure with no
    column heading the rules' heading for its year. Returns what it did, for `ctx.whole_pdf`.

    `asked`: the keys the run asked (`services.whole_pdf.Plan.lines`) — a line not asked (a derived
    line, an intermediate working figure) is not filled. Every line of a section a person switched
    off on Page Scope is asked on this route, but the rules did not read that section, so it has no
    rules figure to fill from. `judge`: the run's `Judge`, whose page
    texts the refused-slot check reads. A no-op off the whole-PDF route: it reads only the rows the
    stage set aside (`ctx.whole_pdf_rules_rows`)."""
    out = {"slots": 0, "rows": 0, "lines": 0, "kept_for_the_model": 0,
           "placed_by_the_model": 0, "refused_by_the_checks": 0, "period_headings": 0,
           "split_inside_the_models_whole": 0, "split_filled_whole": 0}
    rules_rows = getattr(ctx, "whole_pdf_rules_rows", None)
    if not rules_rows:
        # SAID, not silent: a run whose rules read nothing to set aside (no statement page, a
        # filing the rules could not read) has nothing to fill the model's empty lines from.
        if getattr(ctx, "whole_pdf", None) is not None and hasattr(ctx, "log"):
            ctx.log("whole_pdf:rules_fill nothing_set_aside — the rules read no row to set aside, "
                    "so no line the model left empty is filled from them")
        return out
    try:
        published = rules_published_rows(doc, ctx, rules_rows)
    except Exception as exc:  # noqa: BLE001 - a fill that cannot be built leaves the slots empty
        ctx.log(f"whole_pdf:rules_fill_unavailable {type(exc).__name__}: {str(exc)[:160]} — the "
                f"lines the model left empty stay empty")
        return out
    try:
        fills = _fills(doc, ctx, judge, asked, rules_rows, published, out)
    except Exception as exc:  # noqa: BLE001 - nothing was written yet: the slots stay empty
        ctx.log(f"whole_pdf:rules_fill_unavailable {type(exc).__name__}: {str(exc)[:160]} — the "
                f"lines the model left empty stay empty")
        return {key: 0 for key in out}
    model_rows = list(doc.line_items)
    _in_the_models_unit(fills, doc, ctx)
    for row in fills:
        doc.line_items.append(row)
        out["rows"] += 1
        out["slots"] += len(row.values)
    out["lines"] = len({row.canonical_key for row in fills})

    # THE COLUMN HEADINGS: a model figure given no heading (`period_end`) takes the heading the
    # rules read for its year on its page (else the rules' first) — Periods and Statement Date are
    # read from these, off the first row that carries one. A heading the model gave is kept.
    on_page, first = _display_by_period(published)
    for li in model_rows:
        for ev in (li.values or {}).values():
            if getattr(ev, "period_display", None):
                continue
            label = str(ev.period_label or "")
            heading = on_page.get((_page(getattr(ev, "provenance", None)), label)) or first.get(label)
            if heading:
                ev.period_display = heading
                if PERIOD_FLAG not in ev.confidence.flags:
                    ev.confidence.flags.append(PERIOD_FLAG)
                out["period_headings"] += 1
    ctx.log(f"whole_pdf:rules_fill filled {out['slots']} slots from the rules on {out['rows']} "
            f"row(s) of {out['lines']} line(s) — kept for the model: "
            f"{out['kept_for_the_model']} held by it, {out['placed_by_the_model']} it placed on "
            f"a line, {out['refused_by_the_checks']} the checks refuse; column headings from the "
            f"rules: {out['period_headings']}; split face cells: "
            f"{out['split_inside_the_models_whole']} piece slot(s) inside the model's whole, "
            f"{out['split_filled_whole']} filled with the cell's whole")
    return out


def _in_the_models_unit(fills, doc, ctx) -> None:
    """Each filled money figure in the statement's scale, stamped with the unit the model's figures
    of its basis carry — as `note_sourced` writes a note figure on this route (`rules_face_unit`
    for the conversion, `_statement_unit` for the stamp).

    The rules' face rows carry the unit their page declares (China SCE: RMB'000) and the model's
    carry none, both printing the same thousands; left as read, the rules' figure beside the
    model's made the template's checks skip a section as `mixed_scale` (measured on 1966, scripted
    answers: the balance sheet's current-assets reconciliation went from checked to skipped). A
    rules row whose page declares another scale than the face's is multiplied into the face's, as a
    note row is (`note_sourced._in_face_units`); a per-share amount or a share count keeps its own.
    """
    from app.services.units import MONEY, compare, measure_of
    from app.stages.note_sourced import _statement_unit

    stamp: dict[str, object] = {}
    face: dict[str, object] = {}
    for row in fills:
        for ev in (row.values or {}).values():
            uc = getattr(ev, "unit_ctx", None)
            if uc is None or measure_of(uc) != MONEY or ev.value is None:
                continue
            basis = _basis(ev)
            if basis not in stamp:
                stamp[basis] = _statement_unit(doc, basis)
                face[basis] = rules_face_unit(ctx, doc, basis)
            check = compare(uc, face[basis])
            if check.rescaled and not check.currency_mismatch and check.factor != 1:
                ev.value = ev.value * check.factor
                flag = f"unit_rescaled:{check.rescaled[0]}:{check.rescaled[1]}"
                if flag not in ev.confidence.flags:
                    ev.confidence.flags.append(flag)
            if stamp[basis] is not None:
                ev.unit_ctx = stamp[basis].model_copy()


def _fills(doc, ctx, judge, asked, rules_rows, published, out: dict) -> list:
    """The rows `fill_from_rules` adds: each published rules row of an asked line holding only the
    slots it fills, flagged — nothing is written to the document here. Counts go into `out`."""
    from app.services.note_sourced import cell_of

    asked = set(asked) if asked is not None else None
    held = _slots_held(doc)
    refused, withheld = _slots_refused(doc)
    printed = _note_cells(doc)
    for spot, cells in _row_cells(published).items():
        printed.setdefault(spot, []).extend(cells)
    placed = model_figures(doc, printed)
    check = _RulesCheck(doc, ctx, rules_rows, judge)
    splits = _Splits(published)
    defs = {i.key: i for i in (getattr(getattr(ctx, "line_items", None), "items", None) or ())}
    statement_pages = _statement_pages(doc)

    fills = []
    for row in published:
        key = row.canonical_key
        if not key or (asked is not None and key not in asked) or key in withheld:
            continue
        keep = []
        for ck, ev in list((row.values or {}).items()):
            if ev.value is None:
                continue
            basis, period = _basis(ev), str(ev.period_label or "")
            if (basis, period) in held.get(key, ()):
                out["kept_for_the_model"] += 1
                continue
            line = splits.of(row)
            if line is not None:
                verdict = _split_slot(doc, splits, line, row, ev, basis, period, held, defs,
                                      statement_pages)
                if verdict is _INSIDE_THE_MODELS_WHOLE:
                    out["split_inside_the_models_whole"] += 1
                    ctx.log(f"whole_pdf:rules_fill:{key}[{basis}:{period}]: rules row "
                            f"{row.source_label!r} {ev.value} NOT filled — the rules split it out "
                            f"of {line}'s face cell, and the model holds {line} for this slot, "
                            f"which is the cell's whole")
                    continue
                if verdict is not None:
                    ev, pieces = verdict
                    row.values[ck] = ev
                    out["split_filled_whole"] += 1
                    ctx.log(f"whole_pdf:rules_fill:{key}[{basis}:{period}]: the face cell's whole "
                            f"{ev.value}, not the rules' remainder — the model holds "
                            f"{', '.join(f'{k} ({v})' for k, v in pieces)} on a line that is "
                            f"carved from the face, so that piece is counted nowhere else")
            prov = getattr(ev, "provenance", None)
            page = _page(prov)
            amount = ev.value_raw if ev.value_raw is not None else ev.value
            value = _amount(amount)
            others = (set(placed.get(page, {}).get(value, ())) if page is not None and value
                      else set())
            if others:
                others = _placed_here(placed, page, amount, others, cell_of(prov))
            if others:
                out["placed_by_the_model"] += 1
                ctx.log(f"whole_pdf:rules_fill:{key}[{basis}:{period}]: rules row "
                        f"{row.source_label!r} {amount} (page {page + 1}) NOT read — the model "
                        f"placed this figure on {', '.join(sorted(others))}")
                continue
            if (key, basis, period) in refused:
                ok, why = check.passes(row, ev, key)
                if not ok:
                    out["refused_by_the_checks"] += 1
                    ctx.log(f"whole_pdf:rules_fill:{key}[{basis}:{period}]: left empty — the "
                            f"checks refused the model's figure "
                            f"({','.join(sorted(set(refused[(key, basis, period)])))}) and the "
                            f"rules' {amount} ({row.source_label!r}) fails them too: {why}")
                    continue
                ctx.log(f"whole_pdf:rules_fill:{key}[{basis}:{period}]: the checks refused the "
                        f"model's figure; the rules' {amount} ({row.source_label!r}) passes them")
            keep.append(ck)
        if not keep:
            continue
        row.values = {ck: row.values[ck] for ck in keep}
        for ev in row.values.values():
            # THE RULES' SIGN, SETTLED: `normalize` decided it on the rules' own rows (the copy),
            # where the outflow blocks and the unsigned-expense vote can see every row; deciding it
            # again among the model's rows would not see them.
            ev.sign_normalised = True
            if RULES_FLAG not in ev.confidence.flags:
                ev.confidence.flags.append(RULES_FLAG)
        if RULES_FLAG not in row.confidence.flags:
            row.confidence.flags.append(RULES_FLAG)
        # The split-whole breadcrumb is named on the row as well; the slot's review flag is NOT
        # copied there — it is that slot's doubt, and on a fill row (not a model row) a row flag
        # means every slot. A caption doubt of the rules' reading the copy carries stays on the
        # row: it is this row's caption, so every slot's by nature.
        for ev in row.values.values():
            for flag in ev.confidence.flags:
                if str(flag).startswith(SPLIT_WHOLE_FLAG) and flag not in row.confidence.flags:
                    row.confidence.flags.append(flag)
        fills.append(row)
    return fills


#: `_split_slot`'s answer for a piece the model's figure already holds.
_INSIDE_THE_MODELS_WHOLE = object()


def _split_slot(doc, splits: _Splits, line: str, row, ev, basis: str, period: str, held: dict,
                defs: dict, statement_pages: set[int]):
    """How one slot of a rules row split out of `line`'s face cell is filled — see the section
    comment above. None: as the rules publish it. `_INSIDE_THE_MODELS_WHOLE`: not at all. Else
    `(figure, [(piece line, the rules' figure for it), …])`: the face cell's whole, for the
    remainder's slot."""
    remainder = splits.remainder(line)
    rest = _slot_of(remainder, basis, period) if remainder is not None else None
    if row.canonical_key != line:
        if (basis, period) not in held.get(line, ()):
            return None
        # THE MODEL HOLDS THE SPLIT LINE: its figure is the face cell's whole (the line's own
        # definition says so) — unless it is the rules' remainder to the unit, the model having
        # itemised the cell the rules' way, when the pieces are what makes it whole.
        mine = model_row_holding(doc, line, basis, period)
        got = _slot_of(mine, basis, period) if mine is not None else None
        if (got is not None and rest is not None and got.value is not None
                and abs(Decimal(str(got.value))) == abs(Decimal(str(rest.value)))):
            return None
        return _INSIDE_THE_MODELS_WHOLE
    carved = []
    for piece in splits.pieces(line):
        pkey = str(piece.canonical_key or "")
        mine = model_row_holding(doc, pkey, basis, period)
        if mine is None or not _would_be_carved(mine, _slot_of(mine, basis, period), defs,
                                                statement_pages):
            continue
        figure = _slot_of(piece, basis, period)
        if figure is not None and figure.value is not None:
            carved.append((pkey, Decimal(str(figure.value))))
    if not carved:
        return None
    whole = Decimal(str(ev.value)) + sum((v for _k, v in carved), Decimal(0))
    face = splits.whole.get(line)
    printed = _slot_of(face, basis, period) if face is not None else None
    new = ev.model_copy(deep=True)
    if printed is not None and Decimal(str(printed.value)) == whole:
        # THE FACE CELL ITSELF: its page, its box, its heading — the figure is the one it prints.
        new.value, new.value_raw, new.derived = printed.value, printed.value_raw, False
        new.provenance = printed.provenance.model_copy(deep=True) if printed.provenance else None
        new.unit_ctx = printed.unit_ctx.model_copy(deep=True)
        new.period_display = printed.period_display or new.period_display
    else:
        new.value = new.value_raw = whole
        new.derived = True
        new.confidence.flags.append(REVIEW_FLAG)
    for pkey, amount in carved:
        new.confidence.flags.append(f"{SPLIT_WHOLE_FLAG}:{pkey}={amount}")
    return new, carved


# ── a filled face row whose note pieces the model already gave ───────────────────────────────────
#
# THE MODEL CAN READ A FACE FIGURE AS ITS NOTE PIECES and put them on lines of their own, leaving
# the line the rules put the face row on empty — and the fill then put the face figure back whole
# beside the pieces, the cell guard never seeing it (the model placed NOTE cells, the fill read the
# FACE cell). Measured with scripted answers, every figure as the filing prints it:
#
#   * 000709 一年内到期的非流动负债 18,040,355,658.70 (p83): the model gave CPLTD Bank 9,936,568,335.00
#     from note 七、26 (p150); CPLTD Other was filled whole, and total current liabilities came out
#     156,558,553,893.43 against the printed 146,621,985,558.43.
#   * 1966 borrowings 9,817,976 (p105), the model's other loans 1,666,788 from note 30 (p219);
#     佳明 bank and other loans 5,401,406, its other loans 374,150 from note 21 (p126); 300319
#     短期借款 269,920,984.10, its 票据贴现 128,387,402.95 from note 七、22 (p160).
#   * 688008 应收账款 387,791,885.96 (p150), the NET figure the CAS rules publish on Trade
#     Receivables(Gross): the model gave the allowance -1,944,451.99 from note 七、5 (p184), the
#     gross was filled with the net, and the allowance came off twice — 1,944,451.99 short, inside
#     the checks' 0.1% tolerance, so nothing said so.
#
# So a filled face row's slot is SETTLED against the model's figures that are pieces of it, once
# the signs are final (`settle_against_model_pieces`, called by `stages.note_sourced` before any
# cascade reads the row): a model figure on ANOTHER line, same basis and period, read from THIS face
# row's own note — the note it cites, or, where it cites none, the note tables that print its
# figure — and counted in the same section total as the line. "Read from" is asked of the note cell
# the figure sits on (`_note_cells`, `_on_cell`, as `model_figures` asks), and only of the page and
# amount where it cannot be put on one cell and every cell of that amount there is the face row's
# note's: a figure from the next note on a shared page is not a piece. "Counted" is asked of the
# template's own arithmetic (`services.rollups.evaluate`), so a piece counts with the sign the
# section gives it — an allowance counted negative makes the gross the net plus the allowance.
#
# AND THE CELL MUST LIE IN A BLOCK OF ITS NOTE THAT ADDS UP TO THE FACE FIGURE (`_NoteBlocks`): a
# run of rows of the cell's column, the cell's row among them, that sums to it (七、26's four rows to
# 18,040,355,658.70; 佳明 note 18's nine rows to 126,739, its total row unread), or the cell's own
# row printing the face figure and the figure the two make (账面余额 | 坏账准备 | 账面价值). The note
# number alone is not enough, measured with scripted answers:
#
#   * a table that prints the carrying amount of EVERY face row of a section — 300319 note 十一、2
#     (流动性风险, p176), 十二、1 (fair values) — is in the fallback "own note" of each of them: the
#     model's trade payables 840,284,135.50 cited there withheld the filled 应付票据 587,870,376.36,
#     and total current liabilities came out 587,870,376.36 short;
#   * a cited sub-reference reaches its parent note (`LineItem.cited_notes_among`): 佳明 'Finance
#     costs 8(a)' reaches the one table numbered 8, and the model's sales commission 82,132 from
#     8(c) was taken out of finance costs (107,818 settled to 25,686);
#   * two face rows of a section can cite one note (China SCE note 29: other payables and accruals,
#     and contract liabilities).
#
# In none of those does the cell lie in a run that adds up to the face figure.
#
# The slot then takes the face figure LESS the pieces, flagged `LESS_MODEL_FLAG` with each piece and
# sent to review; where the pieces reach or pass the face figure the slot is left empty and the
# pieces are sent to review (`PIECES_REACH_FLAG`). Not settled, and logged: a line whose own
# cascade takes its note's part out of the printed row (`deducts_from_printed`, Trade and other
# payables), a line the template itself computes from those pieces (its own rollup resolves —
# Inventories less its classes, cash less its pledged deposits), an internal part, and a piece on a
# line that will be carved from the face (counted nowhere, so nothing to take out).

#: On a filled figure the model's own pieces of its face row were taken out of:
#: `whole_pdf:rules_reading_less_model:<line>=<the model's figure>`, one per piece.
LESS_MODEL_FLAG = f"{RULES_FLAG}_less_model"
#: On a model figure whose face row was NOT filled, because the model's pieces of it reach or pass
#: the face figure: `whole_pdf:pieces_reach_the_rules_reading:<the face row's line>`.
PIECES_REACH_FLAG = f"{ROW_FLAG}:pieces_reach_the_rules_reading"


def _template_dict(ctx) -> dict | None:
    template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
    if hasattr(template, "model_dump"):
        template = template.model_dump(mode="json")
    return template if isinstance(template, dict) else None


def _section_totals(template_def: dict) -> dict[str, str]:
    """`{line: the closing total of its own template section}` — the total no other total of the
    section feeds, among the section's sums that add the section's own lines only (current assets,
    not total assets).

    A line is given the total of the section it is DECLARED in before any other that reaches it: a
    residual rollup can list lines of another section (Other Reserves lists Interest Payable and
    Perpetual Bond), and reached first through it they were given equity's total."""
    from app.services.rollups import calculated_nodes

    nodes = calculated_nodes(template_def)
    out: dict[str, str] = {}
    declared: dict[str, str] = {}
    for statement in template_def.get("statements", []) or []:
        for section in statement.get("sections") or []:
            members = [section, *(section.get("children") or [])]
            keys = {n.get("canonical_key") for n in members if n.get("canonical_key")}
            totals = []
            for node in members:
                key, rollup = node.get("canonical_key"), node.get("rollup") or {}
                kids = rollup.get("children") or []
                if (key in nodes and kids and not rollup.get("reported_total_key")
                        and node.get("role") in ("subtotal", "total")
                        and all(k in keys for k in kids)):
                    totals.append(key)
            fed = {k for t in totals for k in (nodes[t].get("rollup") or {}).get("children") or ()}
            for total in [t for t in totals if t not in fed]:
                reach, todo = set(), [total]
                while todo:
                    for kid in (nodes.get(todo.pop(), {}).get("rollup") or {}).get("children") or ():
                        if kid not in reach:
                            reach.add(kid)
                            todo.append(kid)
                for key in reach:
                    (declared if key in keys else out).setdefault(key, total)
    out.update(declared)
    return out


class _Section:
    """One (basis, period)'s section arithmetic as `services.rollups` does it, over the figures the
    checks collect (`structural_checks.collect_values`) — with some figures moved or taken out, to
    ask what a fill and the model's pieces do to a total."""

    def __init__(self, template_def, doc, basis: str, period: str, carved: set[str]):
        from app.services.structural_checks import collect_values

        self.template = template_def
        self.slot = (basis, period)
        self.values = collect_values(doc.line_items)
        self.carved = carved
        self.swept = set()
        rows: dict[str, list] = {}
        for li in doc.line_items:
            if li.canonical_key and _held(li, basis, period) is not None:
                rows.setdefault(li.canonical_key, []).append(li)
        self.swept = {k for k, rs in rows.items()
                      if all(str(getattr(r.confidence, "method", "") or "") == "residual"
                             for r in rs)}

    def figure(self, key: str) -> Decimal | None:
        return self.values.get(key, self.slot)

    def move(self, key: str, delta: Decimal, *, gone: int = 0) -> None:
        """Change `key`'s figure by `delta` (one contributing row fewer per `gone`)."""
        per = self.values.values.setdefault(key, {})
        count = self.values.contributors.setdefault(key, {})
        left = count.get(self.slot, 0) - gone
        if gone and left <= 0:
            per.pop(self.slot, None)
            count.pop(self.slot, None)
            return
        per[self.slot] = per.get(self.slot, Decimal(0)) + delta
        count[self.slot] = max(left, 1)

    def shown(self, total: str, line: str, moves=()) -> tuple[float | None, bool]:
        """`(the total as the grid would show it, whether the template computes the line)` with
        `moves` applied: `[(key, delta, rows gone)]`. The line is computed where its own rollup
        resolves to a figure that is not the one its rows give it."""
        from app.services.rollups import evaluate

        saved = {key: (dict(self.values.values.get(key, {})),
                       dict(self.values.contributors.get(key, {}))) for key, *_ in moves}
        try:
            for key, delta, gone in moves:
                self.move(key, delta, gone=gone)

            def reported(key):
                v = self.figure(key)
                return None if v is None else float(v)

            calc = evaluate(self.template, reported, carved=lambda k: k in self.carved,
                            swept=lambda k: k in self.swept,
                            signed=lambda k: (k, self.slot) in self.values.signed)
            got = calc.get(total)
            mine = calc.get(line)
            own = reported(line)
            computed = (mine is not None and mine.computable
                        and (own is None or abs(float(mine.value) - own) >= 0.005))
            return (got.value if got is not None and got.computable else None), computed
        finally:
            for key, (per, count) in saved.items():
                self.values.values[key] = per
                self.values.contributors[key] = count


def _as_printed(ev) -> Decimal | None:
    if ev is None:
        return None
    raw = getattr(ev, "value_raw", None)
    raw = raw if raw is not None else getattr(ev, "value", None)
    try:
        return Decimal(str(raw)) if raw is not None else None
    except (InvalidOperation, ValueError, TypeError):
        return None


class _NoteBlocks:
    """Each rules note cell's place — its note, its table's rows, its row and its column — to ask
    whether a model figure on it lies in a block of its note that adds up to a face figure (see the
    section comment above)."""

    def __init__(self, doc):
        from app.services.note_sourced import cell_of

        self.at: dict[tuple, tuple] = {}
        for table in getattr(doc, "notes", None) or ():
            items = list(getattr(table, "items", None) or ())
            for index, item in enumerate(items):
                for column, ev in (getattr(item, "values", None) or {}).items():
                    cell = cell_of(getattr(ev, "provenance", None))
                    if cell is not None:
                        self.at.setdefault(cell, (table, items, index, column))
        #: `{cell: note number}`.
        self.notes = {cell: str(getattr(table, "note_number", "") or "")
                      for cell, (table, *_rest) in self.at.items()}

    def adds_up(self, cell, face) -> bool:
        """Whether `cell` lies in a block of its table that adds up to `face` (as printed, either
        sign): a run of rows of its column, its own row among them, summing to it; or its own row
        printing `face` in another column beside the figure the two make (a gross, its allowance,
        the net)."""
        hit = self.at.get(cell)
        target = _amount(face)
        if hit is None or not target:
            return False
        _table, items, mine, column = hit
        figures = [_as_printed((getattr(item, "values", None) or {}).get(column)) or Decimal(0)
                   for item in items]
        sums = [Decimal(0)]
        for figure in figures:
            sums.append(sums[-1] + figure)
        if any(abs(sums[end + 1] - sums[start]) == target
               for start in range(mine + 1) for end in range(mine, len(figures))):
            return True
        piece = abs(figures[mine])
        across = [abs(v) for k, ev in (getattr(items[mine], "values", None) or {}).items()
                  if k != column and (v := _as_printed(ev)) is not None]
        return target in across and (target + piece in across or abs(target - piece) in across)


def _cells_by_note(doc) -> tuple[dict, _NoteBlocks]:
    """`({(page, amount): [(cell, corner)]}, the cells' places)` over the rules' note tables."""
    return _note_cells(doc), _NoteBlocks(doc)


def _cells_of(prov, amount, printed: dict) -> list | None:
    """The note cell a printed figure sits on, else — no box, no cell near it — every cell of its
    page and amount. None where there is none."""
    page, value = _page(prov), _amount(amount)
    if page is None or not value:
        return None
    cells = printed.get((page, value), [])
    cell = _on_cell(cells, _box_corner(prov))
    if cell is not None:
        return [cell]
    return [c for c, _corner in cells] or None


def _read_from(row, ev, printed: dict) -> list | None:
    """The note cells a model figure may have been read from — each row of a sum, or the figure
    itself; every one of them must be a piece for the figure to be one."""
    trail = (getattr(row, "derivation", None) or {}).get(f"{_basis(ev)}:{ev.period_label}")
    inputs = [inp for inp in ((trail or {}).get("inputs") or ())
              if isinstance(inp, dict) and inp.get("counted", True)
              and not inp.get("canonical_key")]
    found: list = []
    for inp in inputs:
        amount = inp.get("printed") if inp.get("printed") is not None else inp.get("value")
        cells = _cells_of(inp.get("provenance"), amount, printed)
        if cells is None:
            break
        found += cells
    else:
        if inputs:
            return found
    raw = ev.value_raw if ev.value_raw is not None else ev.value
    return _cells_of(getattr(ev, "provenance", None), raw, printed)


def _own_notes(row, ev, printed: dict, note_at: dict) -> set[str]:
    """The face row's own note: the notes it cites; where it cites none that the rules read (a CAS
    face whose 附注 column was not read, a citation no note table is numbered as), every note whose
    tables print its figure."""
    from app.core.models.line_item import base_note_number
    from app.services.note_sourced import same_note

    cited = list(row.cited_notes())
    cited += [b for b in (base_note_number(t) for t in cited) if b]
    read = set(note_at.values()) - {""}
    if any(same_note(t, n) for t in cited for n in read):
        return set(cited)
    value = _amount(ev.value_raw if ev.value_raw is not None else ev.value)
    return {note_at[cell] for (_page, amount), cells in printed.items() if amount == value
            for cell, _corner in cells if note_at.get(cell)}


def _a_piece(cell, own: set[str], face, blocks: _NoteBlocks, same_note) -> bool:
    """Whether the note cell a model figure sits on is a piece of a face figure: of the face row's
    own note, and inside a block of it that adds up to the face figure (`_NoteBlocks.adds_up`)."""
    note = blocks.notes.get(cell)
    return (bool(note) and any(same_note(o, note) for o in own)
            and blocks.adds_up(cell, face))


def _deducts_own_part(item) -> bool:
    return any(getattr(rung, "deducts_from_printed", False)
               for rung in (getattr(item, "cascade", None) or ()))


def _quantum(*values) -> Decimal:
    """The finest decimal place any of `values` is printed to."""
    exps = [Decimal(str(v)).as_tuple().exponent for v in values if v is not None]
    exps = [e for e in exps if isinstance(e, int)]
    return Decimal(1).scaleb(min(exps)) if exps else Decimal("0.01")


def settle_against_model_pieces(doc, ctx) -> dict:
    """Take the model's pieces of each filled face row out of its filled figure — see the section
    comment above. Run once the signs are final and before anything reads the filled rows
    (`stages.note_sourced`). A no-op off the whole-PDF route: it reads only the rows
    `fill_from_rules` wrote (`RULES_FLAG`, face rows) beside the model's."""
    from app.core.models.enums import PrintedIn
    from app.services.note_sourced import same_note

    out = {"slots_less_model": 0, "slots_left_empty": 0}
    fills = [li for li in doc.line_items
             if RULES_FLAG in (li.confidence.flags or ()) and not is_model_row(li)
             and li.canonical_key and getattr(li, "printed_in", None) is PrintedIn.FACE]
    models = [li for li in doc.line_items if is_model_row(li) and li.canonical_key]
    template_def = _template_dict(ctx)
    if not fills or not models or not template_def:
        return out
    try:
        return _settle(doc, ctx, fills, models, template_def, same_note, out)
    except Exception as exc:  # noqa: BLE001 - nothing is half-written: each slot is set at once
        ctx.log(f"whole_pdf:rules_less_model_unavailable {type(exc).__name__}: "
                f"{str(exc)[:160]} — the filled rows stay as the rules read them")
        return out


def _settle(doc, ctx, fills, models, template_def, same_note, out: dict) -> dict:
    from app.services.rollups import calculated_nodes

    totals = _section_totals(template_def)
    template_keys = set(calculated_nodes(template_def)) | set(totals)
    defs = {i.key: i for i in (getattr(getattr(ctx, "line_items", None), "items", None) or ())}
    statement_pages = _statement_pages(doc)
    printed, blocks = _cells_by_note(doc)
    note_at = blocks.notes
    claimed: set[int] = set()
    sections: dict[tuple[str, str], _Section] = {}
    told: set[str] = set()

    def section(basis: str, period: str) -> _Section:
        if (basis, period) not in sections:
            carved = set()
            for li in doc.line_items:
                ev = _held(li, basis, period)
                if ev is not None and li.canonical_key and _would_be_carved(li, ev, defs,
                                                                            statement_pages):
                    carved.add(li.canonical_key)
            sections[(basis, period)] = _Section(template_def, doc, basis, period, carved)
        return sections[(basis, period)]

    for row in fills:
        key = str(row.canonical_key)
        total = totals.get(key)
        if total is None or key.startswith("sub__"):
            continue
        if _deducts_own_part(defs.get(key)):
            if key not in told:
                told.add(key)
                ctx.log(f"whole_pdf:rules_less_model:{key}: not settled here — its own cascade "
                        f"takes its note's part out of the printed row")
            continue
        for ck, ev in list((row.values or {}).items()):
            if ev.value is None or not ev.value:
                continue
            basis, period = _basis(ev), str(ev.period_label or "")
            own = _own_notes(row, ev, printed, note_at)
            if not own:
                continue
            face = ev.value_raw if ev.value_raw is not None else ev.value
            pieces = []
            for model in models:
                other = str(model.canonical_key)
                if other == key or other.startswith("sub__") or other not in template_keys:
                    continue
                got = _held(model, basis, period)
                if got is None or not got.value or id(got) in claimed:
                    continue
                if _would_be_carved(model, got, defs, statement_pages):
                    continue
                cells = _read_from(model, got, printed)
                if cells and all(_a_piece(cell, own, face, blocks, same_note) for cell in cells):
                    pieces.append((model, got))
            if not pieces:
                continue
            settled = _less_the_pieces(section(basis, period), total, key, ev, pieces)
            if settled is None:
                continue
            figure, counted, why = settled
            if why:
                ctx.log(f"whole_pdf:rules_less_model:{key}[{basis}:{period}]: not settled — {why}")
                continue
            listed = ", ".join(f"{m.canonical_key} {g.value}" for m, g in counted)
            claimed.update(id(g) for _m, g in counted)
            sec = section(basis, period)
            if figure == 0 or (figure < 0) != (Decimal(str(ev.value)) < 0):
                del row.values[ck]
                sec.move(key, -Decimal(str(ev.value)), gone=1)
                for model, got in counted:
                    # In review on the piece's slot; on the model's row, only the breadcrumb.
                    for flag in (f"{PIECES_REACH_FLAG}:{key}", REVIEW_FLAG):
                        if flag not in got.confidence.flags:
                            got.confidence.flags.append(flag)
                    if f"{PIECES_REACH_FLAG}:{key}" not in model.confidence.flags:
                        model.confidence.flags.append(f"{PIECES_REACH_FLAG}:{key}")
                out["slots_left_empty"] += 1
                ctx.log(f"whole_pdf:rules_less_model:{key}[{basis}:{period}]: rules row "
                        f"{row.source_label!r} {ev.value} NOT filled — the model's pieces of it "
                        f"({listed}) reach or pass it; sent to review")
                continue
            sec.move(key, figure - Decimal(str(ev.value)))
            before = ev.value
            ev.value = figure
            for flag in [f"{LESS_MODEL_FLAG}:{m.canonical_key}={g.value}" for m, g in counted] \
                    + [REVIEW_FLAG]:
                if flag not in ev.confidence.flags:
                    ev.confidence.flags.append(flag)
            # The settled slot is in review; the row carries the breadcrumb only, so a line
            # computed from its other year is not put in review by it.
            if LESS_MODEL_FLAG not in row.confidence.flags:
                row.confidence.flags.append(LESS_MODEL_FLAG)
            out["slots_less_model"] += 1
            ctx.log(f"whole_pdf:rules_less_model:{key}[{basis}:{period}]: rules row "
                    f"{row.source_label!r} {before} less the model's pieces of it from its note "
                    f"({listed}) = {figure}; sent to review")
        if not row.values:
            doc.line_items[:] = [li for li in doc.line_items if li is not row]
    if out["slots_less_model"] or out["slots_left_empty"]:
        ctx.log(f"whole_pdf:rules_less_model settled {out['slots_less_model']} filled slot(s) "
                f"against the model's note pieces; left {out['slots_left_empty']} empty")
    return out


def _less_the_pieces(sec: _Section, total: str, key: str, ev, pieces):
    """`(the slot's figure less the pieces, the pieces the section counts, why not)` — or None
    where no piece is counted in the line's section total. The figure is solved from the template's
    own arithmetic: with it, the total is what it is with the face figure alone and no piece."""
    face = Decimal(str(ev.value))
    with_all, _line = sec.shown(total, key)
    at_zero, _line = sec.shown(total, key, [(key, -face, 0)])
    if with_all is None or at_zero is None:
        return None
    step = (with_all - at_zero) / float(face)
    if abs(step) < 1e-9:
        return None                      # the line is not counted in this total

    def without(chosen):
        return sec.shown(total, key, [(str(m.canonical_key), -Decimal(str(g.value)), 1)
                                      for m, g in chosen])[0]

    counted = [(m, g) for m, g in pieces
               if (w := without([(m, g)])) is not None and abs(with_all - w) >= 0.005]
    if not counted:
        return None
    alone = without(counted)
    if alone is None:
        return None
    quantum = _quantum(face, *(g.value for _m, g in counted))
    figure = (face - Decimal(str((with_all - alone) / step))).quantize(quantum)
    then, computed = sec.shown(total, key, [(key, figure - face, 0)])
    if computed:
        return figure, counted, "the template computes the line from those pieces itself"
    if then is None or abs(then - alone) >= 0.005:
        return figure, counted, (f"the section does not add the pieces back as a remainder "
                                 f"would ({then} against {alone})")
    return figure, counted, ""


# ── a filled figure in a section that does not add up ────────────────────────────────────────────
#
# A SECTION THAT DOES NOT ADD UP WHILE IT HOLDS A FIGURE THE RULES FILLED is shown to a reviewer,
# however small the difference: the checks pass a section within 0.1% (`services.structural_checks.
# TOLERANCE_REL`), and on a large balance sheet that hides a filled figure that counts a piece twice
# or misses one (688008's 1,944,451.99 in 9.46bn). Only a review flag on the filled figures: no
# check's status moves, and no other route is touched (no row there carries `RULES_FLAG`).

#: `whole_pdf:rules_reading_in_a_section_that_differs:<section>:<basis>:<period>:<difference>`.
SECTION_DIFFERS_FLAG = f"{ROW_FLAG}:rules_reading_in_a_section_that_differs"
#: On a filled figure that carries the rules' residual gap (or plug) for its section and column, the
#: output's own reconciliation of which is missing or skipped — the doubt stands, on this slot:
#: `whole_pdf:rules_gap_unchecked:<section>:<basis>:<period>`.
GAP_UNCHECKED_FLAG = f"{ROW_FLAG}:rules_gap_unchecked"
#: …and where the output's own reconciliation of that section and column adds up to the cent — the
#: breadcrumb only, not review: `whole_pdf:rules_gap_answered_by_the_output:<section>:<basis>:<period>`.
GAP_ANSWERED_FLAG = f"{ROW_FLAG}:rules_gap_answered_by_the_output"
#: The rules' residual gap or plug on a slot (`stages.residual`): `<kind>:<section>:<basis>|<period>=<d>`.
_RULES_GAP = ("unallocated_gap:", "residual_plug_suspected:")


def _rules_gaps(ev, basis: str, period: str) -> list[str]:
    """The sections whose rules' gap or plug the filled figure `ev` carries for its own column."""
    out = []
    for flag in (ev.confidence.flags or ()):
        flag = str(flag)
        if not flag.startswith(_RULES_GAP):
            continue
        _kind, _, rest = flag.partition(":")
        section, _, col = rest.partition(":")
        col = col.split("=", 1)[0]
        if section and col == f"{basis}|{period}" and section not in out:
            out.append(section)
    return out


def _to_review(ev, row, flag: str) -> bool:
    """`flag` + REVIEW_FLAG on the slot, `flag` alone on the row (it names the slot). Whether the
    slot was not in review by this flag before."""
    new = flag not in ev.confidence.flags
    for f in (flag, REVIEW_FLAG):
        if f not in ev.confidence.flags:
            ev.confidence.flags.append(f)
    if flag not in row.confidence.flags:
        row.confidence.flags.append(flag)
    return new


def review_beside_a_difference(doc, report, ctx) -> int:
    """Flag for review every rules-filled figure (`RULES_FLAG`) of a section whose reconciliation
    shows any difference, passed or failed, in that figure's slot — on that SLOT: the difference is
    one column's. Returns the figures flagged. Called by `stages.structural` once the checks have
    run; a no-op where no row carries the flag.

    A FILLED FIGURE ALSO CARRIES THE RULES' OWN RESIDUAL GAP for its section and column, when the
    rules' reading did not itemise that section to its printed subtotal (`stages.residual`, on the
    rules' copy, where it goes to no review). That gap is about the rules' itemisation, not the
    figures the output holds, so the OUTPUT'S check of the same section and column answers it:
    evaluated with no difference, the slot keeps a breadcrumb and is not in review; with a
    difference, it is in review by the rule above; not evaluated (no result, or skipped), the
    doubt stands and the slot goes to review naming it. Sections are matched by name, exactly.

    THE LIMIT: this runs in `stages.structural`, AFTER the cascades of `stages.note_sourced`, so a
    line a cascade computes from a filled figure cannot see an inherited gap that goes to review
    here (`review_state.in_review` asks the flags when the cascade runs). No configured cascade
    takes such a figure today: a gap or plug lands only on a section's subtotals, the total of its
    residual, and the residual itself, and no cascade term names one of them.
    `tests/test_a_review_flag_sits_on_the_figure_in_doubt.py` fails if one ever does."""
    fills = [li for li in doc.line_items
             if li.canonical_key and RULES_FLAG in (li.confidence.flags or ())]
    if not fills or report is None:
        return 0
    flagged = 0
    evaluated: dict[tuple[str, str, str], Decimal] = {}
    for res in getattr(report, "results", None) or ():
        if getattr(res, "kind", "") != "section_reconciliation" or res.difference is None:
            continue
        try:
            difference = Decimal(str(res.difference))
        except (InvalidOperation, ValueError, TypeError):
            continue
        details = res.details or {}
        basis, period = str(details.get("basis") or ""), str(details.get("period_label") or "")
        name = str(details.get("section") or res.rule_id.split(":", 1)[-1])
        if str(getattr(res, "status", "") or "") != "skipped":
            evaluated[(name, basis, period)] = difference
        if difference == 0:
            continue
        keys = set(details.get("components") or ()) | {details.get("target")}
        flag = f"{SECTION_DIFFERS_FLAG}:{name}:{basis}:{period}:{difference}"
        hit = []
        for li in fills:
            if li.canonical_key not in keys:
                continue
            ev = _held(li, basis, period)
            if ev is None:
                continue
            _to_review(ev, li, flag)
            hit.append(li.canonical_key)
        if hit:
            flagged += len(hit)
            ctx.log(f"whole_pdf:rules_reading_review: {res.rule_id}[{basis}:{period}] "
                    f"{res.status} with a difference of {difference} while it holds figures the "
                    f"rules filled — sent to review: {', '.join(sorted(set(hit)))}")
    unchecked: list[str] = []
    answered: list[str] = []
    for li in fills:
        for ev in list((li.values or {}).values()):
            if ev.value is None:
                continue
            basis, period = _basis(ev), str(ev.period_label or "")
            for section in _rules_gaps(ev, basis, period):
                where = f"{section}:{basis}:{period}"
                difference = evaluated.get((section, basis, period))
                if difference is None:
                    if _to_review(ev, li, f"{GAP_UNCHECKED_FLAG}:{where}"):
                        flagged += 1
                        unchecked.append(f"{li.canonical_key}[{basis}:{period}]")
                elif difference == 0:
                    flag = f"{GAP_ANSWERED_FLAG}:{where}"
                    for flags in (ev.confidence.flags, li.confidence.flags):
                        if flag not in flags:
                            flags.append(flag)
                    answered.append(f"{li.canonical_key}[{basis}:{period}]")
                elif not any(str(f).startswith(f"{SECTION_DIFFERS_FLAG}:{where}:")
                             for f in ev.confidence.flags):
                    # The section differs in this column, and the figure is not among the ones
                    # its check names: the doubt the rules' gap raised is still unanswered.
                    if _to_review(ev, li, f"{SECTION_DIFFERS_FLAG}:{where}:{difference}"):
                        flagged += 1
                        unchecked.append(f"{li.canonical_key}[{basis}:{period}]")
    if unchecked:
        ctx.log(f"whole_pdf:rules_gap_review: the rules' reading left a gap in a section the "
                f"output does not check in that column — sent to review: {', '.join(unchecked)}")
    if answered:
        ctx.log(f"whole_pdf:rules_gap_answered: the rules' gap is answered by the output's own "
                f"check of the section, which adds up: {', '.join(answered)}")
    return flagged
