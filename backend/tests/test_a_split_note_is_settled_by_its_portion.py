"""A note that prints its current / non-current split is settled by the portion the face prints.

China SCE 1966's note 24 "Prepayments, other receivables and other assets" prints five line rows,
other receivables 5,818,375 (2022: 7,028,687) among them, then "Non-current portion (705,926)" and
"Current portion 15,062,723". The face prints the current portion under current assets — swept into
Other Current Assets — and the non-current one under non-current assets. Other Receivables (CP)
reads the note's other-receivables row. No row of the note totalled to the face figure (the portion
rows are line rows to the reader, and the reader keeps the note's own total only as prose), so the
settlement found no relation and the same money stood inside the swept row too: total current
assets over by 5,818,375 and 7,028,687.

The current portion is a total over the rows it splits for a CURRENT line, the non-current portion
for a NON-CURRENT one — and only where the two portions add up to the rows above them.

It was settled once: at f362711 China SCE's total current assets were 116,753,917 / 126,907,812.
The settlement then moved from note numbers to amounts (12be8d6), and its test
(`test_a_notes_money_is_on_the_face_once.NOTE_24`) gave note 24 a "Total 15,062,723" row that the
filing does not print; on the filing's own note there was nothing to match.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import LineRole
from app.core.models.line_item import NoteItem, NotesTable
from app.core.stage import PipelineContext
from app.stages.note_sourced import _note_totals, _printed_split, _settle_against_the_face
from tests.test_a_notes_money_is_on_the_face_once import FACE, NOTES, _ev, _face, _filled, _item

L, T = LineRole.LINE, LineRole.TOTAL

ITEMS = [_item("bs_ca__other_receivables_cp", "bs_ca"), _item("bs_ca__other_current_assets", "bs_ca"),
         _item("bs_nca__other_receivables_ltp", "bs_nca"),
         _item("bs_nca__other_non_current_assets", "bs_nca"),
         _item("bs_ca__prepayments_cp", "bs_ca")]
SCOPES = {"bs_ca__other_current_assets": "exclusive_residual",
          "bs_nca__other_non_current_assets": "exclusive_residual",
          "bs_ca__prepayments_cp": "exclusive_leaf"}


def _note(number: str, rows: list[tuple[str, str | None, str | None, float, LineRole]]) -> NotesTable:
    """A note table: (caption, current figure, prior figure, y, role) per row."""
    table = NotesTable(note_number=number, title=f"note {number}", source_pages=[NOTES])
    for ordinal, (caption, current, prior, y, role) in enumerate(rows):
        item = NoteItem(raw_label=caption, ordinal=ordinal, role=role)
        if current is not None:
            item.set_value(_ev(current, NOTES, y))
        if prior is not None:
            item.set_value(_ev(prior, NOTES, y, "prior"))
        table.items.append(item)
    return table


def _run(rows, notes):
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = rows
    doc.notes = list(notes)
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[SimpleNamespace(canonical_key=k, value_scope=v)
                                             for k, v in SCOPES.items()])
    _settle_against_the_face(doc, ctx, ITEMS)
    return {li.canonical_key: {ev.period_label: str(ev.value) for ev in li.values.values()}
            for li in doc.line_items}


# 1966's note 24 as the reader keeps it: every row a line row, the note's own total not a row.
NOTE_24 = _note("24", [
    ("Prepayments (note) 預付款項（附註）", "6425250", "5595241", 0.30, L),
    ("Deposits 按金", "336451", "779766", 0.32, L),
    ("Other receivables 其他應收款項", "5818375", "7028687", 0.34, L),
    ("Proceed from pre-sales of properties deposited in account of local governments", "2331137",
     None, 0.36, L),
    ("Cost of obtaining contracts 獲得合同的成本", "857436", "725906", 0.38, L),
    ("Non-current portion 非流動部份", "-705926", "-814465", 0.42, L),
    ("Current portion 流動部份", "15062723", "13315135", 0.44, L)])


def _1966(note=NOTE_24):
    return _run([_face("bs_ca__other_current_assets", "15062723", "24", prior="13315135"),
                 _face("bs_nca__other_non_current_assets", "705926", "24", y=0.6, prior="814465"),
                 _filled("bs_ca__other_receivables_cp", "5818375", "24", y=0.34,
                         prior="7028687")], [note])


def test_the_swept_current_row_gives_up_the_receivables_its_notes_current_portion_holds():
    got = _1966()
    assert got["bs_ca__other_current_assets"] == {"current": "9244348", "prior": "6286448"}
    assert got["bs_ca__other_receivables_cp"] == {"current": "5818375", "prior": "7028687"}
    # The non-current row prints the other portion: none of the current line's money is in it.
    assert got["bs_nca__other_non_current_assets"] == {"current": "705926", "prior": "814465"}


def test_the_split_is_read_off_the_rows_it_splits():
    rows = NOTE_24.items
    assert _printed_split(rows, "consolidated", "current") == (
        5, {"current": Decimal("15062723"), "non_current": Decimal("705926")})
    assert _printed_split(rows, "consolidated", "prior") == (
        5, {"current": Decimal("13315135"), "non_current": Decimal("814465")})
    receivables = rows[2]
    assert Decimal("15062723") in _note_totals(NOTE_24, receivables, "consolidated", "current",
                                               "current")
    assert Decimal("705926") in _note_totals(NOTE_24, receivables, "consolidated", "current",
                                             "non_current")
    # Without a side the portions are no total over the row; and they are no line rows of the
    # note's own total, which is the five rows (15,768,649), not those plus both portions.
    assert _note_totals(NOTE_24, receivables, "consolidated", "current") == {Decimal("15768649")}
    # A portion row is not a total over itself.
    assert _note_totals(NOTE_24, rows[6], "consolidated", "current", "current") == {
        Decimal("15768649")}


def test_a_non_current_line_is_settled_against_the_non_current_portion_only():
    """A line of non-current assets that read a row of the split note counts it as non-current
    money: the non-current row prints that portion, the current row does not."""
    note = _note("24", [("Deposits", "500", "400", 0.30, L), ("Other receivables", "200", "100", 0.34, L),
                        ("Less: Portion classified as current", "-450", "-380", 0.40, L),
                        ("Non-current portion", "250", "120", 0.42, L)])
    got = _run([_face("bs_ca__other_current_assets", "450", "24", prior="380"),
                _face("bs_nca__other_non_current_assets", "250", "24", y=0.6, prior="120"),
                _filled("bs_nca__other_receivables_ltp", "200", "24", y=0.34, prior="100")],
               [note])
    assert got["bs_nca__other_non_current_assets"] == {"current": "50", "prior": "20"}
    assert got["bs_ca__other_current_assets"] == {"current": "450", "prior": "380"}
    assert got["bs_nca__other_receivables_ltp"] == {"current": "200", "prior": "100"}


def test_a_printed_leaf_keeps_its_money_and_the_line_runs_without_it():
    """The face row is a line of its own rather than the residual: it keeps its printed money and
    the note line's calculation runs without what the current portion holds."""
    rows = [_face("bs_ca__prepayments_cp", "15062723", "24", prior="13315135"),
            _filled("bs_ca__other_receivables_cp", "5818375", "24", y=0.34, prior="7028687")]
    got = _run(rows, [NOTE_24])
    assert got["bs_ca__prepayments_cp"] == {"current": "15062723", "prior": "13315135"}
    assert got["bs_ca__other_receivables_cp"] == {"current": "0", "prior": "0"}


def test_the_split_after_a_printed_total_is_a_split_of_that_total():
    """Far East Horizon's FVTPL note shape: the line rows, their 'Total', then both portions."""
    note = _note("20", [("Unlisted equity investments", "280", "250", 0.30, L),
                        ("Other receivables", "120", "90", 0.34, L),
                        ("Total", "400", "340", 0.36, T),
                        ("Current portion", "150", "130", 0.40, L),
                        ("Non-current portion", "250", "210", 0.42, L)])
    got = _run([_face("bs_ca__other_current_assets", "150", "20", prior="130"),
                _filled("bs_ca__other_receivables_cp", "120", "20", y=0.34, prior="90")], [note])
    assert got["bs_ca__other_current_assets"] == {"current": "30", "prior": "40"}


def test_portions_that_do_not_add_up_to_the_rows_are_not_a_split():
    """Two portion captions whose figures are not the rows above them say nothing about those rows;
    the settlement stays as it was — nothing found, nothing taken."""
    note = _note("24", [("Deposits", "500", "400", 0.30, L), ("Other receivables", "200", "100", 0.34, L),
                        ("Non-current portion", "-250", "-120", 0.40, L),
                        ("Current portion", "460", "390", 0.42, L)])
    assert _printed_split(note.items, "consolidated", "current") is None
    got = _run([_face("bs_ca__other_current_assets", "460", "24", prior="390"),
                _filled("bs_ca__other_receivables_cp", "200", "24", y=0.34, prior="100")], [note])
    assert got["bs_ca__other_current_assets"] == {"current": "460", "prior": "390"}


def test_an_ageing_bucket_called_current_is_not_a_portion():
    """PetroChina's receivables ageing prints a 'Current' bucket (not yet due): no split."""
    note = _note("9", [("Current", "664", "646", 0.30, L), ("Within one year past due", "17", "25", 0.32, L),
                       ("Non-current portion", "681", "671", 0.40, L)])
    assert _printed_split(note.items, "consolidated", "current") is None


def test_several_portions_of_one_side_are_not_one_split():
    """Lai Sun's cash note takes its pledged balances out portion by portion: no single split."""
    note = _note("29", [("Cash and bank balances", "3132", "3264", 0.30, L),
                        ("Time deposits", "1292", "969", 0.32, L),
                        ("Less: Non-current portion of pledged balances", "-119", "-104", 0.34, L),
                        ("Less: Current portion of pledged balances", "-794", "-831", 0.36, L),
                        ("Less: Current portion of restricted balances", "-484", "-471", 0.38, L)])
    assert _printed_split(note.items, "consolidated", "current") is None
