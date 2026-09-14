r"""AN ASSET MOVEMENT NOTE STATES ITS PERIOD ON THE BLOCK, not on the column.

NEW FILE -> backend/tests/test_note_movement_periods.py

THE DEFECT, measured through the deterministic pipeline on 2025041600195.pdf before the fix. The
depreciation and impairment operating-expense line published 28,154 for the prior year and nothing
for the current. The right-of-use note prints the charge once per comparative year:

    Leased properties / Leasehold land (Note i) / Total
    For the year ended 31 December 2024
      Depreciation charge        16,847    60,860    77,707
    For the year ended 31 December 2023
      Depreciation charge        11,307    55,563    66,870

Its columns are asset CLASSES, and only the first kept a period label — the other two fell back to
the positional "col2"/"col3". So the row gate took the one labelled column from BOTH blocks and
added them: 16,847 + 11,307 = 28,154, a quantity that appears nowhere in the filing. The charges
are 77,707 and 66,870.

Property, plant and equipment published nothing at all, for the opposite reason: its note has six
class columns, so it is read as a MATRIX, every value carries a ``column_index``, and
``select_rows`` refuses those by design — a matrix column is an axis of decomposition, not of time.

BOTH REFUSALS ARE RIGHT ABOUT THE COLUMN AND WRONG ABOUT THE ROW, which is why the fix adds a
row-axis period (``NoteItem.period_hint``) and the column that totals the row
(``NoteItem.total_slot``) rather than loosening either guard.

THE TWO DEVICES READ IN OPPOSITE DIRECTIONS, and that is the whole subtlety:

  * A CAPTION-ONLY HEADER OPENS a block — "For the year ended 31 December 2024" above its rows.
  * A BALANCE ANCHOR CLOSES one — "At 31 December 2023" below the movements it completes.

Applying the opening rule to a closing anchor mislabels a whole year. In the PP&E note the
accumulated-depreciation block opens "At 1 January 2023"; the charge beneath it is the 2023 charge,
and the NEXT charge row — past the 2023 closing balance — is 2024's. Read as "the last header at or
above", both come out 2023 and the current year is silently lost. ``test_a_closing_anchor_governs_
the_rows_above_it`` is that regression.

AND A DATE'S NUMERALS ARE READ AS FIGURES, which is the trap underneath the trap. "As at 31
December 2024" reaches the row scanner as the label "As at" plus the values 31 and 2024 — so the
caption loses its year, and a bare "does this row carry figures?" test calls an opening header a
closing balance. ``test_a_date_is_not_a_figure`` pins the discrimination.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.geometry import BBox
from app.services.notes_extract import (
    _period_anchors, _period_hint_for, _period_year_for, _total_slot_of)
from app.services.row_reconstruct import Word


def W(t: str, x0: float, y: float, w: float = 0.11, h: float = 0.012) -> Word:
    return Word(text=t, bbox=BBox(x0=x0, y0=y, x1=x0 + w, y1=y + h))


def _row(caption: str, y: float, *amounts: str) -> list[Word]:
    """One note row: a caption on the left, amounts in the value columns on the right.

    The caption's tokens are packed into the label area whatever their number, so a long prose row
    stays on the page — `BBox` refuses a coordinate past 1.0, and a wrapped note sentence has more
    tokens than a fixed pitch has room for.
    """
    toks = caption.split()
    pitch = min(0.055, 0.44 / max(len(toks), 1))
    words = [W(tok, 0.10 + pitch * i, y, min(0.05, pitch)) for i, tok in enumerate(toks)]
    words += [W(a, 0.60 + 0.10 * i, y, 0.08) for i, a in enumerate(amounts)]
    return words


# ── the two shapes, as the corpus prints them ────────────────────────────────────────────────────

def _rou_rows() -> list[list[Word]]:
    """Right-of-use note 16: caption-only year headers OPENING each block."""
    return [
        _row("For the year ended 31 December 2024", 0.20),
        _row("Depreciation charge", 0.23, "16,847", "60,860", "77,707"),
        _row("Exchange difference", 0.26, "(15,856)", "(13,940)", "(29,796)"),
        _row("For the year ended 31 December 2023", 0.29),
        _row("Depreciation charge", 0.32, "11,307", "55,563", "66,870"),
    ]


def _ppe_rows() -> list[list[Word]]:
    """PP&E note 15's accumulated-depreciation block: balance anchors CLOSING each year."""
    return [
        _row("At 1 January 2023", 0.20, "836,253", "1,079,098", "3,203,564", "5,118,915"),
        _row("Provided for the year", 0.23, "130,376", "99,568", "268,840", "498,784"),
        _row("At 31 December 2023", 0.26, "929,273", "1,138,692", "3,337,505", "5,405,470"),
        _row("Provided for the year", 0.29, "139,257", "107,888", "319,312", "566,457"),
        _row("At 31 December 2024", 0.32, "1,030,000", "1,190,000", "3,476,488", "5,696,488"),
    ]


# ── which direction a year applies in ────────────────────────────────────────────────────────────

def test_a_caption_only_header_governs_the_rows_below_it():
    """The right-of-use shape. The header carries no figures of its own, so it OPENS a block and
    the rows beneath it are that year's."""
    anchors = _period_anchors(_rou_rows())
    years = [(year, carries) for _y, year, carries in anchors]
    assert years == [(2024, False), (2023, False)], anchors

    assert _period_year_for(anchors, 0.23) == 2024      # the first charge row
    assert _period_year_for(anchors, 0.32) == 2023      # the second, under the 2023 header


def test_a_closing_anchor_governs_the_rows_above_it():
    """THE REGRESSION THAT MATTERS. The PP&E anchors carry balances, so each CLOSES its year and
    the charge above it belongs to that year. Read in the other direction — "the last anchor at or
    above" — the 566,457 charge would be labelled 2023 along with the 498,784 one, and the current
    year's depreciation would be lost while looking perfectly plausible."""
    anchors = _period_anchors(_ppe_rows())
    assert all(carries for _y, _year, carries in anchors), "premise gone: anchors lost their figures"

    assert _period_year_for(anchors, 0.23) == 2023      # above "At 31 December 2023"
    assert _period_year_for(anchors, 0.29) == 2024      # above "At 31 December 2024"


def test_a_date_is_not_a_figure():
    """"As at 31 December 2024" reaches the scanner as the label "As at" and the values 31 and
    2024. Counting those as figures would make an opening header look like a closing balance and
    flip the direction the year applies in — so the anchor caption is the row minus its REAL
    figures, and the date travels with the caption wherever it landed."""
    rows = [_row("As at 31 December 2024", 0.20),
            _row("Carrying amount", 0.23, "474,847", "253,259", "728,106")]
    anchors = _period_anchors(rows)
    assert len(anchors) == 1, anchors
    _y, year, carries = anchors[0]
    assert (year, carries) == (2024, False)


def test_a_movement_caption_is_never_mistaken_for_an_anchor():
    """"Additions" begins with the letters of the "at" arm and a note's prose can open with "At"
    and mention a year. Neither is an anchor: the arm is word-bounded, and prose is excluded by
    length."""
    prose = ("At the end of the reporting period the Group had contracted with its "
             "suppliers for capital expenditure of HK$4,000 (2023: HK$3,000)")
    rows = [_row("Additions", 0.20, "1,086,241"),
            _row(prose, 0.23),
            _row("Amortisation", 0.26, "1,000")]
    assert _period_anchors(rows) == []


# ── which column is the row's total ──────────────────────────────────────────────────────────────

class _Fact:
    def __init__(self, label: str, value: str) -> None:
        self.period_label = label
        self.value = Decimal(value)


class _Li:
    def __init__(self, **vals: str) -> None:
        self.values = {k: _Fact(k, v) for k, v in vals.items()}


def test_the_total_column_is_found_by_arithmetic():
    """Not by matching "Total": the column equal to the sum of the others is the total in every
    script, and on a layout that omits the word. Here the right-of-use row, whose surviving labels
    are the positional fallbacks."""
    assert _total_slot_of(_Li(prior="16847", col2="60860", col3="77707")) == "col3"
    # And the matrix shape, where the columns kept their printed names.
    assert _total_slot_of(_Li(**{"Hotel property": "130376", "Leasehold": "99568",
                                 "Plant": "268840", "Total": "498784"})) == "Total"


def test_a_row_with_no_total_names_none():
    """A row whose columns do not sum to any of them has no total, and "" is the answer. Picking
    the largest would publish one asset class's charge as the whole charge."""
    assert _total_slot_of(_Li(prior="100", col2="200", col3="500")) == ""


def test_two_columns_are_never_resolved():
    """A component that equals its own total is ambiguous both ways round, so a two-value row is
    declined rather than guessed."""
    assert _total_slot_of(_Li(prior="1000", col2="1000")) == ""


def test_rounding_in_the_last_digit_is_still_a_total():
    """Each class is rounded to the printed unit and so is the total, so the column can miss the
    sum by a few units without the row being anything but a total."""
    assert _total_slot_of(_Li(a="333", b="333", c="333", total="1000")) == "total"


# ── the slot a year maps to ──────────────────────────────────────────────────────────────────────

def test_the_latest_year_the_note_names_is_its_current_period():
    anchors = _period_anchors(_ppe_rows())
    assert _period_hint_for(anchors, 2024, 0.29) == "current"
    assert _period_hint_for(anchors, 2024, 0.23) == "prior"


def test_a_third_year_lands_in_neither_slot():
    """The statements have two slots. A figure with nowhere to go must not be published into the
    nearest one — "" declines it, and the line reports nothing rather than a wrong year."""
    anchors = [(0.20, 2022, True), (0.30, 2023, True), (0.40, 2024, True)]
    assert _period_hint_for(anchors, 2024, 0.35) == "current"
    assert _period_hint_for(anchors, 2024, 0.25) == "prior"
    assert _period_hint_for(anchors, 2024, 0.15) == ""


def test_a_note_that_states_no_period_hints_nothing():
    """Most notes are not movement tables. They must come out of here untouched, because a hint is
    read as authoritative by `select_rows` and would bypass the column guards."""
    rows = [_row("Trade receivables", 0.20, "1,000", "2,000"),
            _row("Less: loss allowance", 0.23, "(100)", "(200)")]
    assert _period_anchors(rows) == []
    assert _period_hint_for([], None, 0.20) == ""


# ── end to end: the row the line actually gets ───────────────────────────────────────────────────

def test_select_rows_takes_one_figure_per_period_from_the_total_column():
    """THE PAYOFF, on the shape that was published wrong. Two charge rows, three columns each, and
    the line must come away with 77,707 current and 66,870 prior — not the 28,154 that summing one
    labelled column across both blocks produced, and not the six figures on the rows."""
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
    from app.services.note_sourced import select_rows

    def _item(hint: str, **cells: str) -> NoteItem:
        it = NoteItem(raw_label="Depreciation charge", period_hint=hint, total_slot="col3")
        for label, amount in cells.items():
            it.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                        basis=Basis.CONSOLIDATED, period_label=label))
        return it

    table = NotesTable(note_number="16", title="RIGHT-OF-USE ASSETS")
    table.items = [_item("current", prior="16847", col2="60860", col3="77707"),
                   _item("prior", prior="11307", col2="55563", col3="66870")]

    class _Cfg:
        note_title_any = [r"right.of.use"]
        row_caption_any = [r"depreciation\s+charge"]
        row_caption_none: list[str] = []

    class _Line:
        key = "sub__prepaid_lease_depreciation"
        note_source = _Cfg()

    hits = select_rows(_Line(), [table], periods={"current", "prior"})
    got = sorted((h.period, h.amount) for h in hits)
    assert got == [("current", Decimal("77707")), ("prior", Decimal("66870"))], got


def test_a_movement_table_is_never_read_by_its_column_labels():
    """THE REGRESSION THE WIDENED GATE CAUSED, caught by the figures sweep and not by the suite.

    Once the gate matched the charge captions, movement rows that resolved to NO block period fell
    through to the column path. Their columns are asset classes, but a class column's label is only
    refused when it is a matrix column (`column_index`) or absent from the document's `periods`
    set — and on 8ad0c02c-46bb-4e21-9962-a8d6da4ecf81.pdf that document's face has enough columns
    that `periods` contains "col3", "col4" and "col6". So the line published 366,943,014.10 as a
    period figure, with a prior of 118,627,077.67 against a current of 6,225,356.67.

    The rule is the table's, not the row's: a note that states its periods on the BLOCKS does not
    state them on its columns, so every row in it is readable through the hint or not at all.
    """
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
    from app.services.note_sourced import select_rows

    def _cell(it, label, amount):
        it.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                    basis=Basis.CONSOLIDATED, period_label=label))

    resolved = NoteItem(raw_label="Depreciation charge", period_hint="current",
                        total_slot="col3", ordinal=0)
    _cell(resolved, "prior", "10")
    _cell(resolved, "col2", "20")
    _cell(resolved, "col3", "30")
    # A second charge row in the same note that no anchor governs — the shape that leaked.
    unresolved = NoteItem(raw_label="Depreciation charge", ordinal=1)
    _cell(unresolved, "col3", "366943014.10")
    _cell(unresolved, "prior", "118627077.67")

    table = NotesTable(note_number="7", title="FIXED ASSETS")
    table.items = [resolved, unresolved]

    class _Cfg:
        note_title_any = [r"fixed\s+assets?"]
        row_caption_any = [r"depreciation\s+charge"]
        row_caption_none: list[str] = []

    class _Line:
        key = "sub__fixed_asset_depreciation"
        note_source = _Cfg()

    hits = select_rows(_Line(), [table], periods={"current", "prior", "col3", "col6"})
    assert [(h.period, h.amount) for h in hits] == [("current", Decimal("30"))], \
        [(h.period, str(h.amount)) for h in hits]


def test_a_column_whose_period_was_never_read_is_not_a_period():
    """"col3" is `row_reconstruct._column_periods` reporting that it could read NEITHER a heading
    date nor a PRC period caption for that column. Taking a figure from it publishes a period the
    parser explicitly declined to name.

    THE `periods` FILTER CANNOT CATCH IT, which is the point of a separate guard: `periods` is the
    set the FACE declares, and a face whose own columns were unreadable declares the same
    fallbacks. On 8ad0c02c-46bb-4e21-9962-a8d6da4ecf81.pdf that set contains "col3", "col4" and
    "col6", so the note's class columns matched the face's unresolved ones and a depreciation line
    published 366,943,014.10 as a period figure.

    A SUFFIXED LABEL IS STILL A REAL SLOT and must survive: "current:cost" is a measure of a known
    period, "current_col3" a restatement kept beside the original.
    """
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
    from app.services.note_sourced import select_rows

    it = NoteItem(raw_label="Depreciation charge")
    for label, amount in (("col3", "366943014.10"), ("current", "500"),
                          ("current:cost", "700")):
        it.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                    basis=Basis.CONSOLIDATED, period_label=label))
    table = NotesTable(note_number="7", title="FIXED ASSETS")
    table.items = [it]

    class _Cfg:
        note_title_any = [r"fixed\s+assets?"]
        row_caption_any = [r"depreciation\s+charge"]
        row_caption_none: list[str] = []

    class _Line:
        key = "sub__fixed_asset_depreciation"
        note_source = _Cfg()

    # `periods` deliberately contains the fallback, as the measured document's does.
    hits = select_rows(_Line(), [table],
                       periods={"current", "current:cost", "col3"})
    got = sorted((h.period, str(h.amount)) for h in hits)
    assert got == [("current", "500"), ("current:cost", "700")], got


def test_an_unhinted_row_still_obeys_the_column_guards():
    """The hint is the ONLY thing that bypasses them. A row without one keeps the old behaviour, so
    an ordinary note cannot start publishing its component columns as periods."""
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
    from app.services.note_sourced import select_rows

    it = NoteItem(raw_label="Depreciation charge")          # no period_hint, no total_slot
    it.set_value(ExtractedValue(value_raw=Decimal("5"), value=Decimal("5"),
                                basis=Basis.CONSOLIDATED, period_label="col2"))
    table = NotesTable(note_number="16", title="RIGHT-OF-USE ASSETS")
    table.items = [it]

    class _Cfg:
        note_title_any = [r"right.of.use"]
        row_caption_any = [r"depreciation\s+charge"]
        row_caption_none: list[str] = []

    class _Line:
        key = "sub__prepaid_lease_depreciation"
        note_source = _Cfg()

    assert select_rows(_Line(), [table], periods={"current", "prior"}) == []


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
