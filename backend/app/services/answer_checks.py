"""The checks a model's answer is held to before its figure is written — one set, for every route.

WHY ONE MODULE. The refusals on a model's citations were written inside `stages.line_item_llm`,
one at a time, each after a measured failure, and only that stage's answers passed through them.
A second route that asks a model for figures (the whole PDF sent to it) then had none of them,
and the rules' own guards on a note's structure (the 其中 breakdowns, the accounting-policy notes)
held only the rules' reading. Here they work on one documented shape — the CITATION ENTRY — so a
route that can describe what its answer cited gets every check by describing it.

THE CITATION ENTRY. A plain dict per cited place, as `services.note_sourced.resolve_sources`
returns it. Every key is optional: a check that needs a key the entry lacks leaves the entry alone.

  ``at``                int — the citation's position in the answer; `signs[at]` is its sign. A
                        refusal keeps it, so the answer's remaining terms keep their own signs.
  ``caption``           str — the printed row's caption (the model's own words for a sentence).
  ``title``             str — the title of the note table the row or sentence is in.
  ``note``              str — the note number cited ("七、2", "22"); "" for a statement or a page.
  ``statement``         str — the statement token of a face row.
  ``page``              int — the 1-based page, for a citation made by its page.
  ``on_face``           bool — a printed row of a primary statement.
  ``row_id``            str — that face (or page) row's id.
  ``prose``             bool — an amount stated in a sentence, not in a table row.
  ``prose_currency``    str | None — ISO code printed against that amount ("USD"); None where
                        nothing is printed beside it.
  ``unit``              dict | None — the row's `UnitContext` as JSON, None where it declares none.
  ``figures``           {period: amount as str} — what the entry counts.
  ``figures_by_basis``  {basis: {period: amount}} — the same, by basis.
  ``column``            {"heading", "key", "cells": [{"key", …}], …} — the one printed column a
                        citation picked; ``cells[i]["key"]`` is the key of `row.values`.
  ``basis``             str — the basis the figures are filed under.
  ``provenance``        dict — `page_index` (0-based) and `bbox`/`value_bbox` of the cited figure:
                        how a note row is found again (`note_sourced.cell_of`).
  ``cells``             [(page_index, x0, y0)] — the printed figures the entry counts, where the
                        route knows them and has no note row to find them through (whole-PDF).
  ``off_statement``     bool — a row on a page that is neither a statement nor a note.
  ``repeated_dropped``  [period] — set by `refuse_repeated_figures` on a kept entry whose figures
                        for these periods the answer already counts: they are taken off it, and
                        its `cells` say what it still counts.

A REFUSAL is a dict ``{"at", "note", "caption", "why", "refused"}`` and carries NO figure, so
`services.line_item_llm.combine_terms` counts nothing for it. ``refused`` names the rule:
``derivative``, ``policy_note``, ``breakdown_of_cited_total``, ``same_figure_twice``,
``excluded_caption`` or ``foreign_currency``.

THE ORDER (`check_answer`): derivative → policy note → 其中 breakdown → the same figure twice →
excluded face rows → foreign currency, then the scale step (`in_statement_scale`), which refuses
nothing and puts each remaining note row into the statements' scale. Row-level refusals come
before the excluded-face test because that one judges the answer as a WHOLE ("only face rows its
exclusions rule out") and must see what is left of it.

ACROSS ANSWERS (`settle_siblings`): two parts of one parent given the same printed figure, a 其中
row given to one part while a sibling holds the row it breaks down, and the like. Decided here,
applied by the caller, because only the caller has the written rows.

WHAT THIS DOES NOT TOUCH: the rules route. Nothing here is called on a reading the rules made.
`note_sourced.breakdown_groups` / `breakdowns_across_fragments` and `line_item_notes.
is_policy_note` are the rules' own logic, reused: `note_sourced._broken_down` and
`_broken_down_across_fragments` are now built on the first two and return what they returned.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from app.services import note_sourced
from app.services.line_item_notes import is_policy_note

#: The rules, in the order `check_answer` applies them. A caller may read it to say what ran.
ORDER = ("derivative", "policy_note", "breakdown_of_cited_total", "same_figure_twice",
         "excluded_caption", "foreign_currency", "statement_scale")


class Checked(NamedTuple):
    """`check_answer`'s result: the entries kept (rescaled), the refusals, and the log lines."""
    kept: list[dict]
    refused: list[dict]
    log: list[str]


def _refusal(entry: dict, why: str, rule: str) -> dict:
    return {"at": entry.get("at"), "note": entry.get("note"), "caption": entry.get("caption"),
            "why": why, "refused": rule}


def _sign(entry: dict, signs) -> int:
    at = entry.get("at")
    try:
        s = signs[int(at)] if at is not None and int(at) < len(signs) else 1
    except (TypeError, ValueError):
        s = 1
    return s if s in (1, -1) else 1


def _is_note_row(entry: dict) -> bool:
    return not (entry.get("on_face") or entry.get("prose") or entry.get("off_statement"))


# ── what the checks read off the document's notes, built once per run ────────────────────────

class NoteIndex:
    """The document's notes as the checks look them up: a cited figure's row, a row's 其中 parent,
    which tables are accounting-policy notes. Built lazily and once — a caller answering many
    lines keeps one (`stages.line_item_llm` does), so each check does not walk every note again.
    """

    def __init__(self, notes):
        self.notes = list(notes or ())
        self._located: dict | None = None
        self._position: dict | None = None
        self._policy: set | None = None
        self._parents: dict | None = None

    @property
    def located(self) -> dict:
        """`{cell: (table, row)}` — every printed note figure by its position."""
        if self._located is None:
            out: dict = {}
            for table in self.notes:
                for row in getattr(table, "items", None) or ():
                    for ev in (getattr(row, "values", None) or {}).values():
                        cell = note_sourced.cell_of(getattr(ev, "provenance", None))
                        if cell is not None:
                            out.setdefault(cell, (table, row))
            self._located = out
        return self._located

    @property
    def position(self) -> dict:
        """`{id(row): (table, index)}` for every note row."""
        if self._position is None:
            self._position = {id(row): (table, i) for table in self.notes
                              for i, row in enumerate(getattr(table, "items", None) or ())}
        return self._position

    @property
    def policy(self) -> set:
        """`id(table)` of every table in an unbroken run of one note number that
        `line_item_notes.is_policy_note` reads as an accounting-policy note."""
        if self._policy is None:
            out: set = set()
            run: list = []
            number = None
            for table in self.notes + [None]:
                n = None if table is None else str(getattr(table, "note_number", "") or "")
                if table is None or n != number:
                    if run and is_policy_note(run):
                        out.update(id(t) for t in run)
                    run, number = [], n
                if table is not None:
                    run.append(table)
            self._policy = out
        return self._policy

    @property
    def parents(self) -> dict:
        """`{(id(table), index): (id(table), index)}` — each 其中 breakdown row to the row it
        breaks down, within a fragment and across a page break (`note_sourced.breakdown_groups`,
        `note_sourced.breakdowns_across_fragments`: the rules' own reading of the shape).

        ONLY A GROUP THAT CAN BE A BREAKDOWN (`_can_break_down`). The rules use the reading only
        where the 其中 rows are what the line itself counts (`note_sourced._broken_down`); here it
        judges any sum a model gives, and the row reader carries a 其中 heading down onto rows of
        the table's own level — 300319 十八、2 reads 按组合计提坏账准备, 合并关联方组合 and 合计 as
        其中 rows of 按单项计提坏账准备, and a sum of the two allowance categories lost the second.
        A group with a total row in it, or adding up past its row, is the table's own level."""
        if self._parents is None:
            out: dict = {}
            for table in self.notes:
                rows = list(getattr(table, "items", None) or ())
                for parent, kids in note_sourced.breakdown_groups(rows).items():
                    if not _can_break_down(rows, parent, kids):
                        continue
                    for k in kids:
                        out[(id(table), k)] = (id(table), parent)
            for before, parent, table, kids in note_sourced.breakdowns_across_fragments(
                    self.notes):
                for k in kids:
                    out.setdefault((id(table), k), (id(before), parent))
            self._parents = out
        return self._parents

    def row_of(self, entry: dict):
        """`(table, row)` an entry's figure was read from, or None.

        By the cited figure's box first (`note_sourced.cell_of`) — the box cannot repeat. Where the
        figure has no box, by what the entry says: the note it names, the caption as printed and
        the figures it counts, and only when exactly ONE row of that note answers all three.
        """
        cell = note_sourced.cell_of(entry.get("provenance"))
        if cell is not None and cell in self.located:
            return self.located[cell]
        for c in entry.get("cells") or ():
            try:
                hit = self.located.get(tuple(c))
            except TypeError:
                hit = None
            if hit is not None:
                return hit
        if not _is_note_row(entry) or not str(entry.get("note") or "").strip():
            return None
        caption = str(entry.get("caption") or "")
        figures = {str(v) for v in (entry.get("figures") or {}).values()}
        if not caption or not figures:
            return None
        found = []
        for table in note_sourced._notes_by_number(self.notes, str(entry["note"])):
            for row in getattr(table, "items", None) or ():
                if str(getattr(row, "raw_label", "") or "") != caption:
                    continue
                printed = set()
                for ev in (getattr(row, "values", None) or {}).values():
                    if getattr(ev, "value", None) is not None:
                        printed.add(str(ev.value))
                        try:
                            printed.add(str(Decimal(str(ev.value)).normalize()))
                        except (InvalidOperation, ValueError):
                            pass
                normal = set()
                for v in figures:
                    try:
                        normal.add(str(Decimal(v).normalize()))
                    except (InvalidOperation, ValueError):
                        normal.add(v)
                if figures <= printed or normal <= printed:
                    found.append((table, row))
        return found[0] if len(found) == 1 else None

    def pos_of(self, entry: dict) -> tuple | None:
        """`(id(table), index)` of an entry's note row, or None."""
        got = self.row_of(entry)
        if got is None:
            return None
        _table, row = got
        where = self.position.get(id(row))
        return (id(where[0]), where[1]) if where else None

    def tables_of(self, entry: dict) -> list:
        """The note tables an entry was read from: its row's table, else the tables of the note it
        names (narrowed to its title where one matches), else none."""
        got = self.row_of(entry)
        if got is not None:
            return [got[0]]
        number = str(entry.get("note") or "").strip()
        if not number:
            return []
        tables = note_sourced._notes_by_number(self.notes, number)
        title = str(entry.get("title") or "").strip()
        if title:
            titled = [t for t in tables if str(getattr(t, "title", "") or "").strip() == title]
            tables = titled or tables
        return tables


def _can_break_down(rows: list, parent: int, kids: list[int]) -> bool:
    """Whether the 其中 rows `kids` can be a breakdown of the row `parent`: none of them is a
    total, and in no column the row prints do they add up to more than it (a rounding allowance
    of a tenth of a per cent, at least a cent). A breakdown is a part of its row; a group that is
    not stands beside it, whatever heading the reader carried onto it."""
    if any(_is_total(rows[k]) for k in kids if k < len(rows)):
        return False
    wanted = note_sourced._row_figures(rows[parent]) if parent < len(rows) else {}
    summed: dict = {}
    for k in kids:
        if k >= len(rows):
            continue
        for column, value in note_sourced._row_figures(rows[k]).items():
            summed[column] = summed.get(column, Decimal(0)) + value
    for column, whole in wanted.items():
        if column not in summed:
            continue
        allowance = max(Decimal("0.01"), abs(whole) * Decimal("0.001"))
        if abs(summed[column]) > abs(whole) + allowance:
            return False
    return True


def footprint(entry: dict, index: NoteIndex) -> frozenset:
    """The printed figures an entry COUNTS, as cells `(page_index, x0, y0)`.

    A citation of a whole note row counts every filed column of the row (current and prior), a
    citation of one column counts that column's cells, and an entry that says which cells it
    counts (`cells`) counts those. Where the row cannot be found again, the cited figure's own
    cell. Empty for a face row and a sentence, which `settle_siblings` does not compare.
    """
    if entry.get("on_face") or entry.get("prose"):
        return frozenset()
    if entry.get("cells"):
        try:
            return frozenset(tuple(c) for c in entry["cells"])
        except TypeError:
            return frozenset()
    got = index.row_of(entry)
    if got is None:
        cell = note_sourced.cell_of(entry.get("provenance"))
        return frozenset({cell}) if cell is not None else frozenset()
    _table, row = got
    values = getattr(row, "values", None) or {}
    picked = [c.get("key") for c in ((entry.get("column") or {}).get("cells") or ())
              if isinstance(c, dict)]
    if picked:
        evs = [values[k] for k in picked if k in values]
    else:
        basis = str(entry.get("basis") or "")
        filed = note_sourced.note_columns.FILED_PERIODS
        evs = [ev for ev in values.values()
               if getattr(ev, "value", None) is not None
               and getattr(ev, "column_index", None) is None
               and str(getattr(ev, "period_label", "") or "") in filed
               and (not basis or (note_sourced._basis_of(ev) or "consolidated") == basis)]
    cells = {note_sourced.cell_of(getattr(ev, "provenance", None)) for ev in evs}
    cells.discard(None)
    if not cells:
        cell = note_sourced.cell_of(entry.get("provenance"))
        return frozenset({cell}) if cell is not None else frozenset()
    return frozenset(cells)


# ── the refusals, one answer at a time ─────────────────────────────────────────────────────────

def derivative_vetoes(item) -> list:
    """The line's own `row_caption_none` patterns that exclude DERIVATIVES, compiled.

    Read off the authored vetoes rather than restated here, so the words — and their exception for
    structured deposits with embedded derivatives — are the ones the deterministic route already
    applies. A line whose vetoes say nothing about derivatives gets an empty list.
    """
    src = getattr(item, "note_source", None)
    out = []
    for raw in (getattr(src, "row_caption_none", None) or ()):
        if re.search(r"derivative|衍生", raw, re.IGNORECASE):
            try:
                out.append(re.compile(raw, re.IGNORECASE))
            except re.error:
                continue
    return out


def _taken_out_of_an_added_row(entries: list[dict], index: NoteIndex | None, signs,
                               component: bool) -> set[int]:
    """`id` of each SUBTRACTED note-row citation that is a 其中 breakdown row of a row the same
    sum ADDS — the answer taking a component out of the row it is printed inside."""
    if not component or index is None or not index.notes or len(entries) < 2:
        return set()
    added = {index.pos_of(e) for e in entries if _is_note_row(e) and _sign(e, signs) == 1}
    added.discard(None)
    if not added:
        return set()
    out: set[int] = set()
    for entry in entries:
        if _sign(entry, signs) != -1 or not _is_note_row(entry):
            continue
        pos = index.pos_of(entry)
        if pos is not None and index.parents.get(pos) in added:
            out.add(id(entry))
    return out


def refuse_derivatives(item, entries: list[dict], index: NoteIndex | None = None, *, signs=(),
                       component: bool = False) -> tuple[list[dict], list[dict]]:
    """`(kept, refused)`: a model citation of a DERIVATIVE, on a line that excludes derivatives.

    THE ONE AUTHORED EXCLUSION A MODEL ANSWER IS HELD TO. The rest of `row_caption_none` is
    deterministic-route bookkeeping (totals, the current/non-current split) that the request never
    carries, and grading the model against it would be unfair — see the note above
    `combine_terms`. "Excluding derivatives" is different: it is in the line's LABEL and its
    DEFINITION, so the model was told, and measured on 佳明 (kaming) it cited the interest rate
    swaps' notional amount (950,000, note 22 "Derivative financial instruments") for a current
    securities part anyway, and Securities (CP) published it.

    Both the row caption and the cited note's title are tested: a swap row captioned only by
    its counterparty still sits in a note titled "Derivative financial instruments". A refused
    citation carries no figure — not `figures`, not `amount` — so `combine_terms` counts nothing
    for it; it is returned to be recorded on the row and in the log.

    …EXCEPT A DERIVATIVE TAKEN OUT OF THE ROW IT IS PRINTED INSIDE. 300319 七、2 prints the prior
    year's 以公允价值计量且其变动计入当期损益的金融资产 181,124,711.32 with 其中：远期结售汇
    565,202.29 beneath it. A sum that adds the row and SUBTRACTS the forward (`signs`, or on the
    whole-PDF route the sign of the amount) gives 180,559,509.03, the rules' figure: that is the
    line excluding the derivative, which is what its veto asks. Refusing the subtraction published
    181,124,711.32 — the derivative back in. Only a 其中 row of a row the same sum adds
    (`NoteIndex.parents`); a derivative subtracted from anything else, or added, is refused.
    """
    vetoes = derivative_vetoes(item)
    if not vetoes or not entries:
        return entries, []
    taken_out = _taken_out_of_an_added_row(entries, index, signs, component)
    kept, refused = [], []
    for entry in entries:
        # AND THE PICKED COLUMN'S HEADING: a fair-value table prints "Derivative financial
        # instruments" as a column as readily as a row.
        texts = [str(entry.get("caption") or ""), str(entry.get("title") or ""),
                 str((entry.get("column") or {}).get("heading") or "")]
        if id(entry) in taken_out:
            kept.append(entry)
        elif any(v.search(t) for v in vetoes for t in texts if t):
            refused.append(_refusal(entry, "this line excludes derivatives, and the cited row is one",
                                    "derivative"))
        else:
            kept.append(entry)
    return kept, refused


def refuse_policy_notes(item, entries: list[dict], index: NoteIndex
                        ) -> tuple[list[dict], list[dict]]:
    """`(kept, refused)`: a cited note ROW of an accounting-policy note.

    THE RULE THE RULES READER APPLIES (`line_item_notes.is_policy_note`, through
    `readable_tables`): a policy note states how a balance is measured, not what it is, so its
    rows are not a balance any line adds up. A mainland report prints its policies as chapter 五
    「重要会计政策及会计估计」, an HKEX report as one note ("Material accounting policies"), and both
    name every class of financial asset in their sub-headings — so a by-meaning reader finds them,
    and on 688411 五、11 the list marker "(3)" was read as −3 and published as current FVTOCI.
    Under `llm_document_context = "full"` the report carries the policy chapter and the note fence
    is off, so a model citing such a row was accepted; nothing refused it.

    TABLE ROWS ONLY, AS THE RULES DO. A sentence of a policy note is left alone: the rules' prose
    reader reads every note a line covers, policies included, and a US filing's "Summary of
    significant accounting policies" states real amounts in its sentences. AND NOT WHERE THE LINE'S
    OWN AUTHOR NAMED THE NOTE: a line whose `note_title_any` matches the policy note reads it on
    the rules route too (no shipped line does).
    """
    if not entries or not index.notes or not index.policy:
        return entries, []
    src = getattr(item, "note_source", None)
    titles = note_sourced._compiled(getattr(src, "note_title_any", None))

    def named(table) -> bool:
        return bool(titles) and bool(
            note_sourced._matches_title_any(str(getattr(table, "title", "") or ""), titles)
            or note_sourced._matches_any(str(getattr(table, "note_number", "") or ""), titles)
            or note_sourced._matches_title_any(str(getattr(table, "chapter_title", "") or ""),
                                               titles))

    kept, refused = [], []
    for entry in entries:
        tables = index.tables_of(entry) if _is_note_row(entry) else []
        if tables and all(id(t) in index.policy for t in tables) and not any(map(named, tables)):
            refused.append(_refusal(
                entry, "the cited row is in an accounting-policy note, which states how a balance "
                       "is measured, not what it is — the rules route never reads it either",
                "policy_note"))
        else:
            kept.append(entry)
    return kept, refused


def refuse_breakdowns_of_cited_totals(entries: list[dict], index: NoteIndex, *, signs=(),
                                      component: bool = False, item=None
                                      ) -> tuple[list[dict], list[dict]]:
    """`(kept, refused)`: a 其中 breakdown row added to the very row it breaks down.

    A ROW WITH A 其中 BREAKDOWN UNDER IT IS THE SUM OF THAT BREAKDOWN. 300319 七、2 prints
    以公允价值计量且其变动计入当期损益的金融资产 431,478,888.81 with 其中：结构性理财产品 373,011,600.33
    and 其中：权益工具投资 58,467,288.48 beneath it; an answer adding all three is 862,957,777.62.
    The model has named the row, so the ROW is kept and each breakdown row counted beside it is
    refused. The breakdowns are found by the rules' own reading (`note_sourced.breakdown_groups`
    and `breakdowns_across_fragments`), so a 诚志-shaped table whose second parent arrives inside
    the first's group is not misread as a breakdown here either.

    …EXCEPT WHERE THE ROW HOLDS WHAT THE LINE EXCLUDES. The rules read the breakdown instead of
    the row (`note_sourced._broken_down`) because that is what lets a vetoed component drop out:
    300319's prior-year 181,124,711.32 holds 其中：远期结售汇 565,202.29, a forward. Where the row
    has a breakdown row this line's derivative vetoes name (`derivative_vetoes`) and the answer
    cites every OTHER breakdown row of it, the breakdown is the reading — the ROW is refused and
    the breakdown rows are kept, which gives 180,559,509.03, the rules' figure.

    ONLY A SUM, AND ONLY THE SAME SIGN. A "whole" answer takes its first figure and counts nothing
    twice. A breakdown row SUBTRACTED from its row (the forward out of the prior-year FVTPL) is the
    line taking a component out, which is right.
    """
    if not component or len(entries) < 2 or not index.notes:
        return entries, []
    cited: dict[tuple, set[int]] = {}
    pos_of: dict[int, tuple | None] = {}
    for entry in entries:
        pos = index.pos_of(entry) if _is_note_row(entry) else None
        pos_of[id(entry)] = pos
        if pos is not None:
            cited.setdefault(pos, set()).add(_sign(entry, signs))
    if not cited:
        return entries, []
    kids_of: dict[tuple, list[tuple]] = {}
    for kid, parent in index.parents.items():
        kids_of.setdefault(parent, []).append(kid)
    vetoes = derivative_vetoes(item) if item is not None else []
    tables = {id(t): t for t in index.notes}

    def caption_at(pos) -> str:
        rows = list(getattr(tables.get(pos[0]), "items", None) or ())
        return str(getattr(rows[pos[1]], "raw_label", "") or "") if pos[1] < len(rows) else ""

    # THE ROWS READ THROUGH THEIR BREAKDOWN: a cited row with an excluded component whose every
    # other breakdown row the answer also cites, with the row's own sign.
    through: set[tuple] = set()
    for parent, signs_here in cited.items():
        kids = kids_of.get(parent) or []
        excluded = {k for k in kids if any(v.search(caption_at(k)) for v in vetoes)}
        if not excluded:
            continue
        rest = [k for k in kids if k not in excluded]
        if rest and all(signs_here & cited.get(k, set()) for k in rest):
            through.add(parent)
    kept, refused = [], []
    for entry in entries:
        pos = pos_of[id(entry)]
        parent = index.parents.get(pos) if pos is not None else None
        if pos is not None and pos in through:
            refused.append(_refusal(
                entry, "this row holds a component the line excludes, and the answer cites the "
                       "rest of its 其中 breakdown — the breakdown is the reading, as the rules "
                       "read it, so the row is not added as well", "breakdown_of_cited_total"))
        elif (parent is not None and parent not in through
              and _sign(entry, signs) in cited.get(parent, ())):
            refused.append(_refusal(
                entry, "this row is a 其中 breakdown of a row the same answer adds — that row "
                       "already holds it, so adding it again counts the money twice",
                "breakdown_of_cited_total"))
        else:
            kept.append(entry)
    return kept, refused


def _slots(entry: dict) -> set[tuple[str, str]]:
    """`(basis, period)` of every figure an entry counts: its `figures`, under its `basis` (a face
    row cited for one entity carries every basis the row prints in `figures_by_basis`, and counts
    only its own)."""
    basis = str(entry.get("basis") or "")
    out = {(basis, str(p)) for p in (entry.get("figures") or {})}
    if not out and isinstance(entry.get("figures_by_basis"), dict):
        for b, figures in entry["figures_by_basis"].items():
            out.update((str(b), str(p)) for p in (figures or {}))
    return out


def _marks(entry: dict, index: NoteIndex) -> dict[object, str | None]:
    """The printed figures an entry counts, each with the period of the figure it gives: a note
    row's cells (`footprint`); a face row's (row, basis, period) slots, or the face cells the entry
    names (`cells`). None where the period of a cell is not known."""
    if entry.get("on_face"):
        row_id = str(entry.get("row_id") or "")
        if not row_id:
            return {}
        slots = _slots(entry)
        # A route that knows the very cell it read (the whole PDF) marks the cell: one figure
        # per period, so the period is the entry's own.
        cells = [tuple(c) for c in entry.get("cells") or () if c]
        if cells:
            period = next(iter(p for _b, p in slots), None) if len(slots) == 1 else None
            return {("face", row_id, c): period for c in cells}
        if not slots:
            return {("face", row_id): None}
        return {("face", row_id, b, p): p for b, p in slots}
    cells = footprint(entry, index)
    if not cells:
        return {}
    period_of: dict = {}
    got = index.row_of(entry)
    if got is not None:
        for ev in (getattr(got[1], "values", None) or {}).values():
            cell = note_sourced.cell_of(getattr(ev, "provenance", None))
            if cell is not None:
                period_of.setdefault(cell, str(getattr(ev, "period_label", "") or "") or None)
    return {c: period_of.get(c) for c in cells}


def _without_periods(entry: dict, periods: set[str], cells: set) -> dict:
    """The entry counting only its figures outside `periods` — and only `cells`, so every later
    check (`footprint`, `settle_siblings`) sees what it now counts."""
    out = dict(entry)
    out["figures"] = {p: v for p, v in (entry.get("figures") or {}).items() if p not in periods}
    basis = str(entry.get("basis") or "")
    if isinstance(entry.get("figures_by_basis"), dict):
        out["figures_by_basis"] = {
            b: ({p: v for p, v in (f or {}).items() if p not in periods}
                if str(b) == basis or not basis else f)
            for b, f in entry["figures_by_basis"].items()}
    if not entry.get("on_face"):
        out["cells"] = sorted(cells)
    out["repeated_dropped"] = sorted(periods)
    return out


def refuse_repeated_figures(entries: list[dict], index: NoteIndex, *, signs=(),
                            component: bool = False) -> tuple[list[dict], list[dict]]:
    """`(kept, refused)`: a sum that adds the SAME printed figure twice.

    The text route's repeated-citation tie-break (`take_tied_row`) gives an identical citation the
    next tied row; where there is no other row to give it — one row, or every tied row already
    taken — the second citation resolved to the first's row and was added again. A route with no
    tied rows to offer (the whole PDF, where a sum names its rows by figure) has only this. A
    figure counted with opposite signs is left to the answer.

    A PRINTED FIGURE IS A CELL OF A NOTE ROW, OR A FACE ROW'S FIGURE FOR ONE BASIS AND PERIOD. A
    face citation was marked by its row alone, so "Trade receivables, current" and "Trade
    receivables, prior" were one printed figure and the prior year was refused; a row printing
    Group 500 and Company 60, cited once per entity, lost the Company's 60 before the answer was
    split by entity (`line_item_llm.split_by_entity`).

    ONLY WHAT WAS COUNTED BEFORE IS TAKEN OUT. A citation every figure of which the sum already
    counts is refused. One that repeats SOME of them — a whole row after a citation of its prior
    column — keeps the rest: its repeated periods are taken off it (`repeated_dropped`), where
    each repeated cell's period is known and the citation keeps a figure. Refusing it whole lost
    the current year that only it gave.
    """
    if not component or len(entries) < 2:
        return entries, []
    seen: dict[object, set[int]] = {}
    kept: list[tuple[int, dict]] = []
    refused = []
    order = {id(e): n for n, e in enumerate(entries)}
    for entry in sorted(entries, key=lambda e: int(e.get("at") or 0)):
        sign = _sign(entry, signs)
        marks = _marks(entry, index)
        repeated = {m for m in marks if sign in seen.get(m, ())}
        if repeated:
            periods = {marks[m] for m in repeated}
            rest = {m: p for m, p in marks.items() if m not in repeated}
            figures = set((entry.get("figures") or {}))
            trimmable = (rest and None not in periods and periods <= figures
                         and figures - periods
                         and not any(p in periods for p in rest.values()))
            if not trimmable:
                refused.append(_refusal(
                    entry, "the answer already adds this printed figure — counting it again would "
                           "count it twice", "same_figure_twice"))
                continue
            trimmed = _without_periods(entry, periods, set(rest))
            order[id(trimmed)] = order[id(entry)]
            entry, marks = trimmed, rest
        for m in marks:
            seen.setdefault(m, set()).add(sign)
        kept.append((order[id(entry)], entry))
    kept.sort(key=lambda pair: pair[0])
    return [e for _n, e in kept], refused


def refuse_excluded_face(item, entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(kept, refused)`: an answer made ONLY of face rows the line's own `exclude_hints` rule out.

    THE SAME RULE THE RULES ROUTE APPLIES, AND NO WIDER. `exclude_hints` says which printed
    captions are NOT this line: `services.line_item_matching` never binds such a caption to it,
    whatever its aliases say. A model answer that is nothing but such rows is that same binding,
    made by the other route. Measured on the three CAS filings of the 2024 logs (湖北能源, 四创电子,
    诚志股份): every model answer for `sub__face_principal_revenue` cited the face's 营业收入 /
    营业总收入 — the TOTAL, other business revenue included — which that part's exclusions name.
    On 湖北能源's parent company, whose revenue note prints no 主营业务 row at all, the cascade then
    published 40,617,220.64 of 其他业务 revenue as Sales(Revenues), and the same figure was on
    Other Operating Income as well. The spec says in so many words not to use 营业收入 as a fallback.

    ONLY A WHOLE ANSWER OF VETOED FACE ROWS. An exclusion decides whether a caption standing alone
    IS the line; it says nothing about a row cited as one term of a sum (Trade and other
    receivables = "Trade receivables" + "Other receivables" — the first is vetoed for that line on
    its own, and rightly). So an answer citing any row the exclusions do not rule out — another
    face row, a note row, a sentence — is left whole.

    THE MODEL IS TOLD THE SAME THING: the line's definition says it in words, which is what makes
    holding the answer to it fair (`line_item_payload` sends the definition, not the patterns).
    """
    from app.services.mapping import normalize_label

    hints = list(getattr(item, "exclude_hints", None) or ())
    if not hints or not entries:
        return entries, []
    vetoes = []
    for raw in hints:
        try:
            vetoes.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue

    def vetoed(entry: dict) -> bool:
        caption = str(entry.get("caption") or "")
        spellings = {caption.lower(), normalize_label(caption)} - {""}
        return any(v.search(s) for v in vetoes for s in spellings)

    if not vetoes or not all(e.get("on_face") and vetoed(e) for e in entries):
        return entries, []
    refused = [_refusal(e, "this line's own caption exclusions rule that face row out — the rules "
                           "route never binds it to this line either", "excluded_caption")
               for e in entries]
    return [], refused


def statements_currency(doc) -> str | None:
    """The ISO code of the currency the statements are presented in, or None where none is read."""
    unit = getattr(doc, "unit_context", None)
    raw = str(getattr(unit, "currency", "") or "")
    return note_sourced.currency_iso(raw) or (raw.upper() or None)


def refuse_foreign_currency(doc, entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(kept, refused)`: a sentence's amount printed in a currency other than the statements'.

    A FIGURE IS ADDED ONLY TO FIGURES IN ITS OWN CURRENCY. Measured on 湖北能源 2024, note 十六、2:
    the contingent-liabilities answer summed seven 保函 stated in 元 (323,426,677.00) and two letters
    of credit stated in 美元 (6,568,662.00 and 51,823.11), and published 330,047,162.11 as RMB —
    a dollar counted as a yuan. The contingent-liabilities spec (§6.2) says not to convert and not
    to add across currencies; no line of a set stated in one currency can hold a sum that does.

    Read off the filing's text beside the located amount (`note_sourced.prose_currency_at` for a
    note's sentence, `note_sourced.currency_around` for a sentence cited by its page), never off
    the model's quote. Only a sentence whose currency is printed and is not the statements' is
    refused: an amount with no currency beside it, or a filing whose statements declare none, is
    kept as before.
    """
    statements = statements_currency(doc)
    if not statements or not entries:
        return entries, []
    kept, refused = [], []
    for entry in entries:
        printed = entry.get("prose_currency") if entry.get("prose") else None
        if printed and printed != statements:
            refused.append(_refusal(
                entry, (f"the note states this amount in {printed}, and the statements are in "
                        f"{statements} — it is not added to them"), "foreign_currency"))
        else:
            kept.append(entry)
    return kept, refused


def in_statement_scale(doc, entries: list[dict], *, log: Callable[[str], None] | None = None,
                       key: str = "", prefix: str = "line_item_llm") -> list[dict]:
    """Each cited NOTE ROW's figures in the scale of the statements of its basis.

    THE RULE THE RULES ROUTE APPLIES (`note_sourced._in_face_units`): a note row printed in another
    scale than the face it is published beside is multiplied into the face's. This route wrote the
    figure as printed instead, under the row's own unit, and every check and cascade that adds it
    to the statements' figures added 万元 to 元. Measured on 四创电子 2024, 十四、5 (7) 关键管理人员
    报酬, printed "单位：万元": the rules route read 4,763,700.00 / 4,216,700.00, the model's
    476.37 / 421.67 outranked it, and Related Party Transactions summed 476.37 with figures in 元.

    The row keeps what it printed (`printed_figures`, `rescaled`) for the trail, and its `unit`
    becomes the statement's, so `_cited_unit` stamps the slot with the unit the figure is now in.
    A face row is the statement's own, and a sentence's amount is scaled by its own words
    (`_prose_in_statement_units`); neither is touched. A currency that differs is left alone here:
    no factor converts it.
    """
    # LOCAL: the statement's unit and the prose basis are the note route's own answers, and that
    # stage imports the services this module is built from.
    from app.core.models.line_item import UnitContext
    from app.services.units import compare
    from app.stages.note_sourced import _prose_basis, _statement_unit

    out = []
    for entry in entries or ():
        unit = entry.get("unit")
        if entry.get("on_face") or entry.get("prose") or not isinstance(unit, dict):
            out.append(entry)
            continue
        try:
            printed_in = UnitContext(**unit)
        except Exception:  # noqa: BLE001 - a malformed citation keeps its figure as it was
            out.append(entry)
            continue
        basis = str(entry.get("basis") or _prose_basis(doc) or "consolidated")
        statement = _statement_unit(doc, basis)
        check = compare(printed_in, statement) if statement is not None else None
        if check is None or not check.rescaled or check.currency_mismatch:
            out.append(entry)
            continue

        def scaled(figures, factor=check.factor):
            moved = {}
            for period, raw in (figures or {}).items():
                try:
                    moved[period] = str(Decimal(str(raw)) * factor)
                except (InvalidOperation, ValueError, TypeError):
                    moved[period] = raw
            return moved

        entry = dict(entry)
        entry["printed_figures"] = dict(entry.get("figures") or {})
        entry["figures"] = scaled(entry.get("figures"))
        if isinstance(entry.get("figures_by_basis"), dict):
            entry["figures_by_basis"] = {b: scaled(f) for b, f in entry["figures_by_basis"].items()}
        entry["rescaled"] = list(check.rescaled)
        entry["unit"] = statement.model_dump()
        if log is not None:
            log(f"{prefix}:{key}: note {entry.get('note')!r} row "
                f"{str(entry.get('caption'))[:40]!r} printed at scale {check.rescaled[0]}, "
                f"put into the statement's scale {check.rescaled[1]}")
        out.append(entry)
    return out


def check_answer(item, entries: list[dict], *, doc, signs=(), component: bool = False,
                 key: str = "", index: NoteIndex | None = None,
                 prefix: str = "line_item_llm") -> Checked:
    """Every check, in `ORDER`, on one answer's resolved citations. `(kept, refused, log)`.

    `signs` and `component` are the answer's (`LineItemAnswer.signs`, role "component"): the
    breakdown and repeated-figure rules apply to a sum only. `index` is the run's `NoteIndex`,
    built here from `doc.notes` when the caller keeps none. The log lines are the caller's to
    write, in the order the stage has always written them: the rescales, then each refusal.
    """
    index = index if index is not None else NoteIndex(getattr(doc, "notes", None))
    signs = list(signs or ())
    kept, refused = refuse_derivatives(item, list(entries or ()), index, signs=signs,
                                       component=component)
    for step in (lambda e: refuse_policy_notes(item, e, index),
                 lambda e: refuse_breakdowns_of_cited_totals(e, index, signs=signs,
                                                             component=component, item=item),
                 lambda e: refuse_repeated_figures(e, index, signs=signs, component=component),
                 lambda e: refuse_excluded_face(item, e),
                 lambda e: refuse_foreign_currency(doc, e)):
        kept, more = step(kept)
        refused += more
    lines: list[str] = []
    kept = in_statement_scale(doc, kept, log=lines.append, key=key, prefix=prefix)
    for e in kept:
        if e.get("repeated_dropped"):
            lines.append(f"{prefix}:{key}: citation note={e.get('note')!r} "
                         f"caption={str(e.get('caption'))[:60]!r}: its "
                         f"{', '.join(e['repeated_dropped'])} figure is already in the answer, so "
                         f"only the rest of it is counted")
    for bad in refused:
        lines.append(f"{prefix}:{key}: citation REFUSED note={bad.get('note')!r} "
                     f"caption={str(bad.get('caption'))[:60]!r} ({bad.get('why')})")
    return Checked(kept, refused, lines)


# ── the text route's tie-break, as the one rule it is ─────────────────────────────────────────

def take_tied_row(repeats: dict, said: tuple, hit, tied: list, ident: Callable = id):
    """The row an IDENTICAL citation takes when its caption ties on several rows: the next one it
    has not taken yet, not the first one again.

    A counterparty name wrapped over two lines leaves many rows of one related-party table with the
    same caption fragment, and nothing the model can write names one of them; it can only cite the
    caption once per row. Measured on 诚志股份 2024, note 十四、4: thirteen guarantees print as
    "技有限公司南京诚志永清能源科", the answer cited that caption thirteen times, and document order
    resolved all thirteen to the first — 6,220,000 thirteen times, 804,190,000 published for
    guarantees that add up to 2,103,890,000. Only an IDENTICAL citation advances (`said` is
    everything the citation says) — one naming another column of the row is a different citation
    and still reads the row it names. With every tied row taken, the row found is returned again;
    a sum then meets `refuse_repeated_figures`.
    """
    taken = repeats.setdefault(said, set())
    if ident(hit) in taken:
        fresh = next((r for r in tied if ident(r) not in taken), None)
        if fresh is not None:
            hit = fresh
    taken.add(ident(hit))
    return hit


# ── across answers: two parts of one parent ────────────────────────────────────────────────────

class Settlement(NamedTuple):
    """One decision `settle_siblings` makes about two parts of one parent.

    `action`: "withhold" — `other` is emptied, its figure counted in `owner`'s; "keep" — `other`
    shares `owner`'s figure but also rests on rows of its own, so it is left as it is and said so;
    "flag" — the two may hold the same money and nothing here can say safely which, so both go
    to review. `kind`: "same_figure", "breakdown" (a 其中 row of a row the owner holds),
    "same_row" (another column of one row), "inside_total" (a row above a total the owner holds).
    """
    kind: str
    action: str
    parent: str
    owner: str
    other: str
    note: str
    caption: str


def _footprints(raw) -> list[frozenset]:
    """A key's recorded citations as footprints — a cell (the stage's old record) or a set."""
    out = []
    for fp in raw or ():
        if not fp:
            continue
        if isinstance(fp, (set, frozenset)):
            out.append(frozenset(fp))
        elif isinstance(fp, tuple):
            out.append(frozenset({fp}))
    return out


def _is_total(row) -> bool:
    role = str(getattr(getattr(row, "role", None), "value", getattr(row, "role", "")) or "")
    if role in ("total", "subtotal"):
        return True
    from app.services.notes_extract import note_row_role
    return str(note_row_role(getattr(row, "raw_label", "")).value) in ("total", "subtotal")


def settle_siblings(cited: dict[str, list], items, index: NoteIndex, *,
                    claims: dict[str, set[str]] | Callable[[], dict] | None = None
                    ) -> list[Settlement]:
    """What to do where two parts of one parent rest on the same note money. `cited` is each
    answered key's footprints (`footprint`, one per kept note-row citation). `claims` is
    `line_item_notes.claimed_notes` for the run, or a function that builds it — called only when
    two parts do share a printed figure, because the claim is the one costly read here.

    1. THE SAME PRINTED FIGURE (any cell two parts of one parent both count, not only the first
       cell of each citation): one part keeps it — the one whose claim covers the row's note
       (`line_item_notes.claimed_notes`, the rules' one-note-one-sibling claim), and where none or
       several do, the one declared first. From a Lai Sun run: note 26's "Non-current portion
       788,507" cited for the non-current FVTPL part and the non-current other-financial-assets
       part. What happens to the others depends on what ELSE they count:
       * every cell they count is one the keeper counts — WITHHELD;
       * a citation of theirs counts none of the shared cell — "keep": their figure also rests on
         other rows, and is left as it is;
       * otherwise they share some of their cells and not all — a whole row beside a sibling's
         citation of its prior column — and both go to review ("flag"). Withholding such a part
         emptied it whole, and the current-year figure only it held was lost.
       ONLY PARTS ONE SUM OF THE PARENT TAKES WITH THE SAME SIGN are compared (`_sums_under`,
       `_taken_alike`): Find 1 and
       the Level 3 that Securities (CP) deducts from it citing one cell is the rung taking a
       Level 3 holding out, not one figure counted twice.
    2. TERMS OF ONE SUM IN THE PARENT'S CASCADE, with the same sign (Find 1's six parts; never
       Find 1 against the Level 3 it deducts):
       * one part's row is a 其中 breakdown of a row its sibling holds (the rules' own reading,
         `NoteIndex.parents`) — the money is in the sibling's figure already, so the part is
         withheld when that is all it rests on, and both are flagged when it rests on more;
       * the two hold different columns of ONE row, or one holds a row printed above the total
         row the other holds — they may hold the same money, and a column can be a level or a
         measure rather than a share, so both are flagged rather than one emptied.
       688008 七、2 prints 结构性存款 1,629,362,905.47 inside 交易性金融资产 1,783,494,750.68; given to
       both the FVTPL part and the other-financial-assets part, Securities (CP) would be
       3,412,857,656.15.
    """
    items = list(items or ())
    order = {i.key: n for n, i in enumerate(items)}
    by_key = {i.key: i for i in items}
    parent_of = {i.key: str(getattr(i, "parent", "") or "") for i in items}
    fps = {k: _footprints(v) for k, v in (cited or {}).items()}
    built: list[dict] = []

    def claims_now() -> dict:
        if not built:
            built.append((claims() if callable(claims) else claims) or {})
        return built[0]
    out: list[Settlement] = []
    withheld: set[str] = set()
    said: set[tuple] = set()

    def note_and_caption(cell) -> tuple[str, str]:
        table, row = index.located.get(cell, (None, None))
        return (str(getattr(table, "note_number", "") or ""),
                str(getattr(row, "raw_label", "") or "")[:48])

    # 1. the same printed figure
    shared: dict[tuple, list[str]] = {}
    for key, prints in fps.items():
        parent = parent_of.get(key, "")
        if not parent:
            continue
        # `dict.fromkeys`, not a set: the groups are walked in the order they are built.
        for cell in dict.fromkeys(c for fp in prints for c in sorted(fp, key=repr)):
            shared.setdefault((parent, cell), []).append(key)
    counted = {k: frozenset().union(*v) if v else frozenset() for k, v in fps.items()}
    sums_of: dict[str, list] = {}
    # Pairs sent to review, by either step — a pair is flagged once.
    flagged: set[tuple] = set()
    for (parent, cell), keys in shared.items():
        keys = sorted(set(keys), key=lambda k: order.get(k, len(order)))
        if len(keys) < 2:
            continue
        note, caption = note_and_caption(cell)
        if parent not in sums_of:
            sums_of[parent] = _sums_under(parent, by_key)
        sums = sums_of[parent]
        # The parts one sum takes alike, each group in declaration order: Find 1's parts in one,
        # the Level 3 parts the parent deducts from them in another.
        groups: list[list[str]] = []
        for k in keys:
            if k in withheld:
                continue
            home = next((g for g in groups if all(_taken_alike(k, j, sums) for j in g)), None)
            if home is None:
                groups.append([k])
            else:
                home.append(k)
        for group in groups:
            if len(group) < 2:
                continue
            owners = [k for k in group if note and note in claims_now().get(k, ())]
            owner = owners[0] if len(owners) == 1 else group[0]
            for key in group:
                if key == owner or key in withheld:
                    continue
                if counted.get(key, frozenset()) <= counted.get(owner, frozenset()):
                    withheld.add(key)
                    out.append(Settlement("same_figure", "withhold", parent, owner, key, note,
                                          caption))
                    continue
                action = ("keep" if any(fp and cell not in fp for fp in fps.get(key, ()))
                          else "flag")
                if action == "flag":
                    if (key, owner) in flagged:
                        continue
                    flagged.update({(key, owner), (owner, key)})
                if (key, owner, action) not in said:
                    said.add((key, owner, action))
                    out.append(Settlement("same_figure", action, parent, owner, key, note,
                                          caption))

    # 2. terms of one sum
    def rows_of(key) -> dict[tuple, set]:
        rows: dict[tuple, set] = {}
        for fp in fps.get(key, ()):
            for cell in fp:
                got = index.located.get(cell)
                where = index.position.get(id(got[1])) if got else None
                if where is not None:
                    rows.setdefault((id(where[0]), where[1]), set()).add(cell)
        return rows

    def described(pos) -> tuple[str, str]:
        for table in index.notes:
            if id(table) == pos[0]:
                items_ = list(getattr(table, "items", None) or ())
                row = items_[pos[1]] if pos[1] < len(items_) else None
                return (str(getattr(table, "note_number", "") or ""),
                        str(getattr(row, "raw_label", "") or "")[:48])
        return "", ""

    for parent_key, parent_item in by_key.items():
        for rung in getattr(parent_item, "cascade", None) or ():
            terms = [t for t in (getattr(rung, "terms", None) or ())
                     if getattr(t, "ref", "") and t.ref in fps]
            if len(terms) < 2 or str(getattr(rung, "terms_op", "sum") or "sum") != "sum":
                continue
            for a in terms:
                for b in terms:
                    if a.ref == b.ref or int(a.sign or 1) != int(b.sign or 1):
                        continue
                    if a.ref in withheld or b.ref in withheld:
                        continue
                    a_rows, b_rows = rows_of(a.ref), rows_of(b.ref)
                    if not a_rows or not b_rows:
                        continue
                    inside = {p for p in b_rows if index.parents.get(p) in a_rows}
                    if inside:
                        note, caption = described(next(iter(inside)))
                        all_prints = all(any(index.located.get(c) for c in fp)
                                         for fp in fps.get(b.ref, ()))
                        if inside == set(b_rows) and all_prints:
                            withheld.add(b.ref)
                            out.append(Settlement("breakdown", "withhold", parent_key, a.ref,
                                                  b.ref, note, caption))
                        elif (a.ref, b.ref) not in flagged:
                            flagged.update({(a.ref, b.ref), (b.ref, a.ref)})
                            out.append(Settlement("breakdown", "flag", parent_key, a.ref, b.ref,
                                                  note, caption))
                        continue
                    if (a.ref, b.ref) in flagged:
                        continue
                    common = [p for p in a_rows if p in b_rows and not (a_rows[p] & b_rows[p])]
                    if common:
                        note, caption = described(common[0])
                        flagged.update({(a.ref, b.ref), (b.ref, a.ref)})
                        out.append(Settlement("same_row", "flag", parent_key, a.ref, b.ref, note,
                                              caption))
                        continue
                    above = _above_a_total(a_rows, b_rows, index)
                    if above is not None:
                        note, caption = described(above)
                        flagged.update({(a.ref, b.ref), (b.ref, a.ref)})
                        out.append(Settlement("inside_total", "flag", parent_key, a.ref, b.ref,
                                              note, caption))
    return out


def _sums_under(parent: str, by_key: dict) -> list[dict[str, set[int]]]:
    """One `{key: {sign, …}}` per cascade rung of `parent` (and one for its formula terms): the sign
    each line enters THAT sum with, through the working lines it is computed from. Securities (CP)
    adds the six Find 1 parts and subtracts `sub__fa_cp_level_3_total` (read from the two Level 3
    parts) in CP_INTERMEDIATE, and takes them the other way round through the overshoot residual in
    CP_ZERO — in no one sum do Find 1 and Level 3 have the same sign."""
    def expand(key: str, sign: int, depth: int, seen: frozenset, into: dict) -> None:
        item = by_key.get(key)
        if item is None or depth > 4 or key in seen:
            return
        terms = [t for rung in (getattr(item, "cascade", None) or ())
                 for t in (getattr(rung, "terms", None) or ())]
        terms += list(getattr(item, "terms", None) or ())
        for t in terms:
            ref = str(getattr(t, "ref", "") or "")
            if not ref:
                continue
            s = sign * _term_sign(t)
            into.setdefault(ref, set()).add(s)
            expand(ref, s, depth + 1, seen | {key}, into)

    item = by_key.get(parent)
    sums = [list(getattr(rung, "terms", None) or ()) for rung in (getattr(item, "cascade", None)
                                                                   or ())]
    sums.append(list(getattr(item, "terms", None) or ()))
    out = []
    for terms in sums:
        into: dict[str, set[int]] = {}
        for t in terms:
            ref = str(getattr(t, "ref", "") or "")
            if ref:
                into.setdefault(ref, set()).add(_term_sign(t))
                expand(ref, _term_sign(t), 1, frozenset({parent}), into)
        if into:
            out.append(into)
    return out


def _term_sign(t) -> int:
    try:
        return -1 if int(getattr(t, "sign", 1) or 1) < 0 else 1
    except (TypeError, ValueError):
        return 1


def _taken_alike(a: str, b: str, sums: list[dict[str, set[int]]]) -> bool:
    """Whether `a` and `b` may hold one printed figure twice: some sum of the parent takes both
    with the same sign — or the parent's sums do not reach one of them, so nothing says otherwise."""
    if not any(a in d for d in sums) or not any(b in d for d in sums):
        return True
    return any(d.get(a, set()) & d.get(b, set()) for d in sums)


def _above_a_total(a_rows: dict, b_rows: dict, index: NoteIndex) -> tuple | None:
    """A row of `b_rows` printed above a total row of `a_rows` in the same table and below the
    total before it — the rows that total adds up — or None."""
    tables = {id(t): t for t in index.notes}
    for (tid, i) in a_rows:
        rows = list(getattr(tables.get(tid), "items", None) or ())
        if i >= len(rows) or not _is_total(rows[i]):
            continue
        start = max((j for j in range(i) if _is_total(rows[j])), default=-1)
        for (tid_b, j) in b_rows:
            if tid_b == tid and start < j < i and not _is_total(rows[j]):
                return (tid_b, j)
    return None
