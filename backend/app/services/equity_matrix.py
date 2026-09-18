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

    dates = sorted({when for when, _row in balances}, reverse=True)
    if len(dates) < 2:
        if log:
            log(f"extract:equity_matrix_transpose=skipped(one balance date: {dates[0]})")
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
