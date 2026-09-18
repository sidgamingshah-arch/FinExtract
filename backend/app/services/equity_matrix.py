"""The statement of changes in equity, turned round: one row per equity COMPONENT.

WHY A TRANSPOSE IS NEEDED AT ALL. Every other statement has its concept on the ROW axis and its
periods across the columns, so a printed caption names a concept and a column names a period. A
matrix face is the other way up: its rows are MOVEMENTS ("Loss for the year", "2021 final
dividend") and its columns are COMPONENTS (share premium, exchange reserve, retained profits,
non-controlling interests). ``row_reconstruct._matrix_items`` files each figure under the component
column it was printed in, which is the only honest reading of the page — and it leaves the whole
statement unreachable, because `periods.slot_for` asks for "current" and a component is not a
period. Measured before this existed: 32 of 32 rows on China SCE and 11 of 11 on 佳明集團 carried a
`period_label` that was a column header, every one of them mapped to
`engine_unclassified_face__changes_in_equity__unresolved_section__*`, and the statement contributed
nothing to any published figure.

WHAT IS TRANSPOSED, AND WHAT IS NOT. Only the BALANCE rows. A balance row restates the equity
section of a balance sheet at a date, so reading it as "these concepts, at this period" is a
relabelling and not an inference. A movement row is not a concept in the template at all — there is
no "dividends paid to non-controlling shareholders" column to put it in — so the movements keep the
component labelling they were extracted with and this adds nothing for them.

WHY IT IS DOCUMENT-WIDE. `row_reconstruct` reads ONE PAGE at a time and China SCE prints one page
per year: page 106 closes at 31 December 2022 and page 107 at 31 December 2023. A page-local
"latest balance is the current period" reads page 106's prior-year close as the current year, and
the two pages then publish two different figures into one slot. Ranking the dates ACROSS the
document is the whole reason this is a separate pass, and it is a pass over rows already extracted
— it computes no figure and reads no note.

IT NEVER OUTRANKS THE BALANCE SHEET. Where the face printed a concept, the face's figure is the
filing stating the amount and this is a second evidence reference for it; `periods.summable` reads
the flag these values carry and refuses to ADD them to a slot another row already supplies. Where
the face did not — 佳明's balance sheet prints "Share capital" and one lumped "Reserves", and
nothing else — this is the only place the breakdown exists.
"""
from __future__ import annotations

from app.core.models.line_item import ExtractedValue, LineItem
from app.core.models.enums import LineRole, ValueSource
from app.services.row_reconstruct import EQUITY_BALANCE_FLAG

#: The flag a transposed ROW carries to say which statement's VOCABULARY names its caption, as
#: `f"{MATCH_STATEMENT_FLAG}:balance_sheet"`. Read by `stages.map_ontology._statement_of`.
#:
#: WHY IT IS NEEDED. The concept gate scopes a caption by the statement the page was classified as,
#: and every `bs_equity__*` concept is the BALANCE SHEET's. A component caption offered as a
#: `changes_in_equity` row is refused by that gate — measured: all nine of 佳明's components match
#: on `balance_sheet` and none of them on `changes_in_equity`, "Share capital" included. The
#: transposed row IS the balance sheet's equity section restated at a date, so the balance sheet is
#: the vocabulary that names it; its PROVENANCE still points at the equity-statement page it was
#: printed on, which is the question `_statement_of` used to be the only answer to.
MATCH_STATEMENT_FLAG = "match_statement"

#: The flag each transposed VALUE carries, so `periods.summable` can tell a restatement from an
#: addend. Distinct from :data:`row_reconstruct.EQUITY_BALANCE_FLAG`, which marks the row the
#: figure was read FROM.
TRANSPOSED_FLAG = "equity_matrix_restatement"

#: Both spellings of the one statement. The page classifier says `changes_in_equity` and
#: `StatementType` says `equity_changes`; `services.mapping.normalize_statement` owns the fold, and
#: this is a membership test over a handful of pages rather than a reason to import it.
_EQUITY_STATEMENT_SPELLINGS = frozenset({"changes_in_equity", "equity_changes"})


def _balance_date(row: LineItem) -> str:
    """The date a matrix balance row is the balance at, or "" for any other row."""
    for flag in (getattr(row.confidence, "flags", None) or ()):
        if str(flag).startswith(f"{EQUITY_BALANCE_FLAG}:"):
            return str(flag).split(":", 1)[1]
    return ""


def _blocks(rows) -> list[tuple[LineItem, LineItem, tuple[LineItem, ...]]]:
    """Each (opening balance, closing balance, movements) block of one matrix page, in order.

    A BLOCK IS WHAT A PERIOD IS ON THIS STATEMENT. The matrix has no period columns, so the period
    a movement belongs to is the one that CLOSES beneath it — and the closing balance is the one
    with movement rows between it and the balance above. 佳明 prints two blocks on one page; China
    SCE prints one page per year, whose opening balance is the previous page's closing balance
    reprinted, and this tells the two apart without reading either date.
    """
    out: list[tuple[LineItem, LineItem, tuple[LineItem, ...]]] = []
    opening: LineItem | None = None
    moves: list[LineItem] = []
    for row in rows:
        if _balance_date(row):
            if opening is not None and moves:
                out.append((opening, row, tuple(moves)))
            opening, moves = row, []
            continue
        moves.append(row)
    return out


def _matrix_pages(doc) -> dict[int, list[LineItem]]:
    """Each page carrying a flagged balance row, with that page's rows in printed order.

    THE UNIT OF BOTH PASSES. A block is printed on one page (佳明 prints two blocks on one page;
    China SCE prints one block per page and reprints the previous close as the new page's opening
    balance), and scoping to the page is also what keeps an ordinary two-column face row — which
    is never between two balance rows of one matrix — out of a block.

    A row whose values span pages is left out: it cannot be one row of one matrix page.
    """
    by_page: dict[int, list[LineItem]] = {}
    for row in (doc.line_items or ()):
        pages = {v.provenance.page_index for v in (row.values or {}).values()
                 if v.provenance is not None and v.provenance.page_index is not None}
        if len(pages) != 1:
            continue
        by_page.setdefault(pages.pop(), []).append(row)
    return {page: sorted(rows, key=lambda r: int(getattr(r, "ordinal", 0) or 0))
            for page, rows in sorted(by_page.items())
            if any(_balance_date(r) for r in rows)}


def _closing_dates(pages: dict[int, list[LineItem]]) -> list[str]:
    """The dates the matrix CLOSES at, latest first.

    A balance row is a close when movement rows sit between it and the balance above — see
    :func:`_blocks`.
    """
    closes = {_balance_date(close) for rows in pages.values()
              for _open, close, _moves in _blocks(rows)}
    return sorted(closes, reverse=True)


def transpose_closing_balances(doc, *, log=None) -> int:
    """Append one row per equity component, for the document's two latest balance dates.

    Returns the number of rows appended. A document with no matrix face, or one whose matrix has
    balances at fewer than two dates, is left exactly as it was: with one date there is nothing to
    call the comparative, and inventing one would put a single column's figures under both periods.
    """
    # ONLY THE EQUITY STATEMENT'S OWN PAGES. A movement schedule is a matrix too — an HKEX
    # property note prints "At 1 April 2024" over cost and depreciation columns and closes "At 31
    # March 2025" — and its balance rows satisfy the same two conditions. They do not reach here
    # today, because a note's rows become `NoteItem`s on `doc.notes` and never join
    # `doc.line_items`; this makes that a stated requirement rather than a lucky consequence, so a
    # future route that DID put a note's rows on the face could not turn "Plant and machinery" into
    # an equity component.
    #
    # AND NOT APPLIED AT ALL WHEN NO PAGE WAS CLASSIFIED AS THE EQUITY STATEMENT, which is the
    # matrix path's own promise kept: "detection is geometric so a mis-classified page still
    # parses" (`row_reconstruct._maybe_matrix`). A document in that state has balance rows on its
    # face and no page claiming to be the equity statement, so filtering by the classifier's
    # verdict would discard exactly the case the geometric fallback exists for.
    equity_pages = {p.index for p in (doc.pages or ())
                    if str(getattr(p, "statement", "") or "") in _EQUITY_STATEMENT_SPELLINGS}
    balances: list[tuple[str, LineItem]] = []
    for row in (doc.line_items or ()):
        when = _balance_date(row)
        if not when:
            continue
        pages = {v.provenance.page_index for v in (row.values or {}).values()
                 if v.provenance is not None and v.provenance.page_index is not None}
        if equity_pages and not (pages & equity_pages):
            continue
        balances.append((when, row))
    if not balances:
        return 0

    # THE TWO LATEST CLOSING DATES, not the two latest balance dates. A filing printing one page
    # per year restates the previous close as this page's OPENING balance, so the four dates on
    # China SCE are 2022-01-01, 2022-12-31, 2023-01-01 and 2023-12-31, and the second-latest is
    # 2023-01-01 — the opening of the CURRENT year offered as the prior year's figures. It read
    # the right numbers (an opening balance equals the close it follows), but by accident: a
    # filing that RESTATES its opening balance prints two different figures there, and the one
    # this should read is the close. `_closing_dates` asks which balances have movements above
    # them, which is what makes a balance a close.
    dates = _closing_dates(_matrix_pages(doc))
    if len(dates) < 2:
        if log:
            log("extract:equity_matrix_transpose=skipped(one closing date: "
                f"{dates[0] if dates else '-'})")
        return 0
    slot_of = {dates[0]: "current", dates[1]: "prior"}

    # component -> (basis, period) -> the value as printed. Keyed by the component NAME because
    # that is what `_matrix_items` put in `period_label`, and it is the same string on every page
    # of one statement — `_matrix_column_names` reads it off the header band each page repeats.
    #
    # FIRST WRITER WINS per (component, basis, period): a closing balance and the next page's
    # opening balance are the same figure at the same date, and a filing that prints both should
    # not have them added.
    cells: dict[str, dict[tuple[str, str], ExtractedValue]] = {}
    order: list[str] = []
    for when, row in balances:
        period = slot_of.get(when)
        if period is None:
            continue
        for value in (row.values or {}).values():
            if value.value is None:
                continue
            component = str(getattr(value, "period_label", "") or "")
            if not component:
                continue
            basis = getattr(value.basis, "value", value.basis)
            if component not in cells:
                cells[component] = {}
                order.append(component)
            cells[component].setdefault((str(basis), period), value)

    added = 0
    ordinal = max((int(getattr(r, "ordinal", 0) or 0) for r in doc.line_items), default=0)
    for component in order:
        ordinal += 1
        item = LineItem(source_label=component, ordinal=ordinal, role=LineRole.LINE,
                        source=ValueSource.MACHINE, section_hint="equity")
        item.confidence.flags.append(f"{MATCH_STATEMENT_FLAG}:balance_sheet")
        # CURRENT BEFORE PRIOR, because `periods.split_current_prior` falls back to the printed
        # ORDER for a row whose values name no period — and while these do name one, a consumer
        # reading the list positionally then agrees with one reading the labels.
        for period in ("current", "prior"):
            for (basis, slot), value in sorted(cells[component].items()):
                if slot != period:
                    continue
                fact = value.model_copy(deep=True)
                fact.period_label = period
                fact.period_display = dates[0] if period == "current" else dates[1]
                fact.period_end = None
                # The component is the CAPTION of this row now, so the column index it had on the
                # matrix says nothing about this one and would read as a period column.
                fact.column_index = None
                fact.confidence.flags = list(fact.confidence.flags or ()) + [TRANSPOSED_FLAG]
                item.set_value(fact)
        if item.values:
            doc.line_items.append(item)
            added += 1
    if log:
        log(f"extract:equity_matrix_transpose={added} component(s) "
            f"current={dates[0]} prior={dates[1]}")
    return added


# ── the movement rows, read down one column ─────────────────────────────────────────────────────
#
# THE TRANSPOSE ABOVE READS THE BALANCE ROWS ACROSS EVERY COLUMN. This reads ONE COLUMN down every
# movement row, and it is the other half of the same statement. Measured before it existed: 28 of
# China SCE's movement rows and 10 of 佳明's reached
# `engine_unclassified_face__changes_in_equity__unresolved_section__*` and published nothing,
# because a movement's figures are spread across the component columns and `periods.slot_for` asks
# for a period — "Retained profits" is not one.
#
# WHICH COLUMN, AND WHY ONLY ONE. The template's retained-earnings block (`is_retained__*`: the
# dividends, the transfers to reserves, the prior-period adjustments) is a reconciliation of
# RETAINED PROFITS, and the retained-profits column of the equity statement is that reconciliation
# as the filing prints it. Reading any other column into those concepts would be reading a
# different thing wearing the same caption, so nothing else is read.
#
# THE READING IS CHECKED BEFORE IT IS PUBLISHED. Opening retained profits plus the movements must
# equal closing retained profits, per block, or the block is refused. Measured on all four blocks
# of the two HK filings, it ties to the unit:
#
#   SCE 2022   21,760,983 + 24,544 - 206,093 - 84,667   = 21,494,767
#   SCE 2023   21,494,767 - 7,991,050 + 14,058          = 13,517,775
#   佳明 2025   2,827,757 - 292,055                      = 2,535,702
#   佳明 2026   2,535,702 - 349,498 - 3,605              = 2,182,599
#
# A column mis-read by one cell breaks that identity, so the check is what makes reading a single
# column by name safe: the name picks the column, the arithmetic confirms it was the right one.

#: The concept whose column this reads. Named rather than described because the rulebook owns the
#: words — the column is found through THIS concept's own aliases, in every language it declares
#: them in, so a filing printing 未分配利润 is read by the same rule as one printing "Retained
#: profits" and neither needs a vocabulary of its own here.
RETAINED_PROFITS_KEY = "bs_equity__retained_profits"

#: What the emitted rows declare themselves to be, so the concept gate reaches the right shelf:
#: the `is_retained` section of the profit-and-loss vocabulary, which is where the template keeps
#: the retained-earnings movements. Without the statement the gate scopes them to
#: `changes_in_equity`, which no namespace declares; without the section the same captions could
#: reach the whole P&L.
_MOVEMENT_STATEMENT = "profit_and_loss"
#: The BANNER phrase, not the section id — `section_hint` is read through
#: `mapping.section_of_banner`, which resolves the words a statement prints rather than a
#: namespace, and `transpose_closing_balances` above says "equity" for the same reason. Measured:
#: with the id the gate resolved nothing and every caption was waved through, which put 佳明's
#: "Loss for the year" — the OWNERS' share, read out of the retained-profits column — onto
#: `is_pl__profit_for_the_year`, the group figure. That is the mapping
#: `spec_alias_curation._THE_ATTRIBUTION_TAIL` exists to refuse, and the banner refuses it here.
_MOVEMENT_SECTION = "Adjustments to Retained Profits"

#: The flag each emitted VALUE carries. Deliberately NOT :data:`TRANSPOSED_FLAG`: a transposed
#: balance restates something the balance sheet also prints, and `periods.summable` refuses to add
#: it to a slot another row supplies. A dividend charged to retained profits is printed HERE and
#: nowhere else, so it is an ordinary addend and must stay one.
MOVEMENT_FLAG = "equity_matrix_movement"


def _fold(text: str) -> str:
    """A caption reduced to what two spellings of one name share."""
    return " ".join(str(text or "").split()).casefold()


def _retained_captions(line_items) -> frozenset[str]:
    """Every spelling the rulebook gives :data:`RETAINED_PROFITS_KEY`, folded.

    Read off the resolved line-item set rather than listed here, so the column is found by the
    rulebook's own words and a locale it adds later is picked up without touching this module.
    """
    for item in (getattr(line_items, "items", None) or ()):
        if getattr(item, "key", "") != RETAINED_PROFITS_KEY:
            continue
        names = [getattr(item, "label", "") or "", *(getattr(item, "aliases", None) or ())]
        for spellings in (getattr(item, "aliases_i18n", None) or {}).values():
            names.extend(spellings or ())
        return frozenset(_fold(n) for n in names if str(n or "").strip())
    return frozenset()


def _retained_column(rows, captions: frozenset[str]) -> str:
    """The name of the component column the rulebook calls retained profits, or ""."""
    seen: list[str] = []
    for row in rows:
        for value in (row.values or {}).values():
            name = str(getattr(value, "period_label", "") or "")
            if name and name not in seen:
                seen.append(name)
    for name in seen:
        folded = _fold(name)
        # Equality first, then the alias as a SUBSTRING — "Retained earnings" has to be found by
        # the declared "Retained earning", and 未分配利润 by itself. Never the other way round: a
        # column called "Total" must not be found by an alias that contains it.
        if folded in captions or any(alias and alias in folded for alias in captions):
            return name
    return ""


def _cell(row: LineItem, column: str):
    """The row's figure in one component column, or None."""
    for value in (row.values or {}).values():
        if value.value is not None and str(getattr(value, "period_label", "") or "") == column:
            return value
    return None


def _without_subtotals(movements, column: str) -> list:
    """The movements that are ADDENDS in this column, with the subtotal rows removed.

    Arithmetic, not vocabulary. A statement of changes in equity prints "Total comprehensive
    income for the year" under the rows it totals, and in the retained-profits column that
    subtotal equals the profit for the year exactly — so counting both doubles the year's result
    and the block stops reconciling. A row whose figure equals the running sum of the addends
    since the last subtotal IS that subtotal: it is dropped and the run starts again.

    A genuine movement that happens to equal the run is dropped too, and then the block does not
    reconcile and publishes nothing. That is the safe direction to fail in.
    """
    kept, run = [], None
    for row in movements:
        value = _cell(row, column)
        if value is None:
            continue                     # no figure in this column: not part of its reconciliation
        if run is not None and value.value == run:
            run = None
            continue
        kept.append((row, value))
        run = value.value if run is None else run + value.value
    return kept


def collapse_movement_rows(doc, *, line_items=None, log=None) -> int:
    """Append one row per movement in the equity statement's retained-profits column.

    Returns the number of rows appended. Reads no note, computes no published figure: every value
    is a printed cell of a printed row, carrying that cell's own provenance, relabelled with the
    period its block closes in.
    """
    captions = _retained_captions(line_items)
    if not captions:
        if log:
            log(f"extract:equity_movements=skipped(rulebook declares no {RETAINED_PROFITS_KEY})")
        return 0

    # Deliberately not scoped by the classifier's verdict, for the reason
    # `transpose_closing_balances` gives.
    pages = _matrix_pages(doc)
    blocks = [block for rows in pages.values() for block in _blocks(rows)]
    if not blocks:
        return 0

    column = _retained_column([row for rows in pages.values() for row in rows], captions)
    if not column:
        if log:
            log("extract:equity_movements=skipped(no retained-profits column)")
        return 0

    dates = _closing_dates(pages)
    if len(dates) < 2:
        if log:
            log("extract:equity_movements=skipped(one closing date: "
                f"{dates[0] if dates else '-'})")
        return 0
    slot_of = {dates[0]: "current", dates[1]: "prior"}

    # caption -> period -> the cells that make it up, in printed order.
    cells: dict[str, dict[str, list]] = {}
    order: list[str] = []
    for opening, close, movements in blocks:
        period = slot_of.get(_balance_date(close))
        if period is None:
            continue
        opened, closed = _cell(opening, column), _cell(close, column)
        if opened is None or closed is None:
            if log:
                log(f"extract:equity_movements=refused(block {_balance_date(close)}: "
                    f"{column!r} has no balance)")
            continue
        addends = _without_subtotals(movements, column)
        moved = sum((v.value for _row, v in addends), start=0)
        if opened.value + moved != closed.value:
            if log:
                log(f"extract:equity_movements=refused(block {_balance_date(close)} does not "
                    f"reconcile: {opened.value} + {moved} != {closed.value})")
            continue
        for row, value in addends:
            caption = str(row.source_label or "")
            if not caption:
                continue
            if caption not in cells:
                cells[caption] = {}
                order.append(caption)
            cells[caption].setdefault(period, []).append(value)

    added = 0
    ordinal = max((int(getattr(r, "ordinal", 0) or 0) for r in doc.line_items), default=0)
    for caption in order:
        ordinal += 1
        item = LineItem(source_label=caption, ordinal=ordinal, role=LineRole.LINE,
                        source=ValueSource.MACHINE, section_hint=_MOVEMENT_SECTION)
        item.confidence.flags.append(f"{MATCH_STATEMENT_FLAG}:{_MOVEMENT_STATEMENT}")
        # CURRENT BEFORE PRIOR, for the reason `transpose_closing_balances` states.
        for period in ("current", "prior"):
            printed = cells[caption].get(period) or ()
            if not printed:
                continue
            # ONE VALUE PER PERIOD, summed. `set_value` keys by (basis, period) and would keep
            # whichever cell was written last — and a statement that prints one movement on two
            # lines within a block means their sum, not the second of them.
            fact = printed[0].model_copy(deep=True)
            fact.value = sum((v.value for v in printed[1:]), start=printed[0].value)
            fact.period_label = period
            fact.period_display = dates[0] if period == "current" else dates[1]
            fact.period_end = None
            fact.column_index = None
            fact.confidence.flags = list(fact.confidence.flags or ()) + [MOVEMENT_FLAG]
            item.set_value(fact)
        if item.values:
            doc.line_items.append(item)
            added += 1
    if log:
        log(f"extract:equity_movements={added} movement(s) down {column!r} "
            f"current={dates[0]} prior={dates[1]}")
    return added
