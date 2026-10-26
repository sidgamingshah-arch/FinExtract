"""A FACE ROW MAY PRINT A NOTE TOTAL LESS THE ROWS THE FACE PRINTS ON ROWS OF THEIR OWN — AND A
CURRENT LINE'S MONEY IS NEVER INSIDE A TOTAL OF THE NON-CURRENT BLOCK.

NEW FILE -> backend/tests/test_a_face_row_nets_out_the_rows_it_prints_apart.py

Far East Horizon's note 32 prints "Subtotal – current" 143,776,669 over its lease liabilities
277,212 and its borrowings; the face prints "Lease liabilities" 277,212 on a row of its own and
"Interest-bearing bank and other borrowings" 143,499,457 — the subtotal less the lease
liabilities. The current portion of long-term bank loans the model read (64,157,317) is inside that
face row, but no total the note prints equals it, so the settlement never found it there and total
current liabilities published 232,021,307 against a printed 167,863,990.

China SCE's note 30 prints the current and the non-current borrowings in one table. The current
bank loans 8,151,188 have a total below them in the NEXT block, which the face prints as the
non-current "Interest-bearing bank and other borrowings"; read as containing them, ST Bank Loans
Payable gave its figure up and published 0.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import BBox
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NoteRef, NotesTable,
                                       Provenance)
from app.core.stage import PipelineContext
from app.services import derivation
from app.stages.note_sourced import _settle_against_the_face

FACE, NOTES = 0, 1
T, L = LineRole.TOTAL, LineRole.LINE


def _prov(page: int, y: float, period: str) -> Provenance:
    x = 0.6 if period == "current" else 0.8
    return Provenance(source_kind="pdf", page_index=page,
                      bbox=BBox(x0=x, y0=y, x1=x + 0.1, y1=y + 0.01))


def _ev(amount: str, page: int, y: float, period: str = "current") -> ExtractedValue:
    return ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                          basis=Basis.CONSOLIDATED, period_label=period,
                          provenance=_prov(page, y, period))


def _face(key: str, caption: str, amounts: dict[str, str], y: float, note: str = "") -> LineItem:
    li = LineItem(source_label=caption, canonical_key=key)
    for period, amount in amounts.items():
        li.set_value(_ev(amount, FACE, y, period))
    if note:
        li.note_refs = [NoteRef(raw=note, numbers=[note])]
    return li


def _read(key: str, note: str, rows: list[tuple[float, dict[str, str]]]) -> LineItem:
    """A line the model filled from note rows, each printed at its `y` on the notes page."""
    li = LineItem(source_label=key, canonical_key=key)
    store = None
    for period in ("current", "prior"):
        cited = [(y, amounts[period]) for y, amounts in rows if period in amounts]
        if not cited:
            continue
        total = sum((Decimal(a) for _y, a in cited), Decimal(0))
        li.set_value(_ev(str(total), NOTES, cited[0][0], period))
        store = derivation.record(store, basis="consolidated", period_label=period,
                                  derivation=derivation.build(
                                      method="line_item_llm:cited_rows", formula=None,
                                      inputs=[{"label": "row", "note": note, "value": a,
                                               "provenance": _prov(NOTES, y, period).model_dump(
                                                   mode="json")} for y, a in cited],
                                      result=str(total)))
    li.derivation = store
    return li


def _note(number: str, rows: list[tuple[str, dict[str, str], float, LineRole]]) -> NotesTable:
    table = NotesTable(note_number=number, title=f"note {number}", source_pages=[NOTES])
    for ordinal, (caption, amounts, y, role) in enumerate(rows):
        item = NoteItem(raw_label=caption, ordinal=ordinal, role=role)
        for period, amount in amounts.items():
            item.set_value(_ev(amount, NOTES, y, period))
        table.items.append(item)
    return table


def _item(key, section):
    return SimpleNamespace(key=key, section_scope=[section], parent="", in_output=True)


ITEMS = [_item("bs_cl__cpltd_bank", "bs_cl"), _item("bs_cl__other_current_liabilities", "bs_cl"),
         _item("bs_cl__lease_liabilities_cp", "bs_cl"),
         _item("bs_cl__st_bank_loans_payable", "bs_cl"), _item("bs_ncl__ltd_bank", "bs_ncl")]


def _run(rows, notes):
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = rows
    doc.notes = list(notes)
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[SimpleNamespace(
        canonical_key="bs_cl__other_current_liabilities", value_scope="exclusive_residual")])
    _settle_against_the_face(doc, ctx, ITEMS)
    return {(li.canonical_key, li.source_label): {ev.period_label: str(ev.value)
                                                  for ev in li.values.values()}
            for li in doc.line_items}


# Far East Horizon note 32, current block (2024 / 2023).
NOTE_32 = _note("32", [
    ("Lease liabilities (Note 14(b))", {"current": "277212", "prior": "280495"}, 0.30, L),
    ("Bank loans – secured", {"current": "63570", "prior": "27553"}, 0.32, L),
    ("Current portion of long term bank loans – secured",
     {"current": "5754196", "prior": "5472032"}, 0.34, L),
    ("Current portion of long term bank loans – unsecured",
     {"current": "58403121", "prior": "47341854"}, 0.36, L),
    ("Bonds and other loans", {"current": "79278570", "prior": "79549045"}, 0.38, L),
    ("Subtotal – current", {"current": "143776669", "prior": "132670484"}, 0.40, T)])
CPLTD = [(0.34, {"current": "5754196", "prior": "5472032"}),
         (0.36, {"current": "58403121", "prior": "47341854"})]
BORROWINGS = ("bs_cl__other_current_liabilities", "Interest-bearing bank and other borrowings")


def test_the_residual_gives_up_a_part_inside_a_total_it_prints_net_of_a_row_printed_apart():
    rows = [_face(*BORROWINGS, {"current": "143499457", "prior": "132389989"}, 0.50, "32"),
            _face("bs_cl__lease_liabilities_cp", "Lease liabilities",
                  {"current": "277212", "prior": "280495"}, 0.52),
            _read("bs_cl__cpltd_bank", "32", CPLTD)]
    got = _run(rows, [NOTE_32])
    assert got[BORROWINGS] == {"current": str(143499457 - 64157317),
                               "prior": str(132389989 - 52813886)}
    assert got[("bs_cl__cpltd_bank", "bs_cl__cpltd_bank")] == {"current": "64157317",
                                                                "prior": "52813886"}


def test_without_the_row_printed_apart_the_face_row_contains_nothing_and_nothing_moves():
    rows = [_face(*BORROWINGS, {"current": "143499457", "prior": "132389989"}, 0.50, "32"),
            _read("bs_cl__cpltd_bank", "32", CPLTD)]
    got = _run(rows, [NOTE_32])
    assert got[BORROWINGS] == {"current": "143499457", "prior": "132389989"}


def test_one_year_netting_to_the_face_row_by_coincidence_settles_nothing():
    """The prior year's face figure is not the prior subtotal less the prior lease liabilities."""
    rows = [_face(*BORROWINGS, {"current": "143499457", "prior": "132000000"}, 0.50, "32"),
            _face("bs_cl__lease_liabilities_cp", "Lease liabilities",
                  {"current": "277212", "prior": "280495"}, 0.52),
            _read("bs_cl__cpltd_bank", "32", CPLTD)]
    got = _run(rows, [NOTE_32])
    assert got[BORROWINGS] == {"current": "143499457", "prior": "132000000"}


# China SCE note 30: the current block, then the non-current block, one table.
NOTE_30 = _note("30", [
    ("Current Bank loans — secured", {"current": "8151188", "prior": "10089294"}, 0.30, L),
    ("Other loans — secured", {"current": "1666788", "prior": "653665"}, 0.32, L),
    ("Total — current", {"current": "9817976", "prior": "10742959"}, 0.34, T),
    ("Non-current Bank loans — secured", {"current": "20000000", "prior": "21000000"}, 0.40, L),
    ("Other loans — secured", {"current": "5000000", "prior": "4000000"}, 0.42, L),
    ("Total — non-current", {"current": "25000000", "prior": "25000000"}, 0.44, T)])


def test_a_current_line_is_not_counted_inside_the_non_current_blocks_total():
    rows = [_face("bs_ncl__ltd_bank", "Interest-bearing bank and other borrowings",
                  {"current": "25000000", "prior": "25000000"}, 0.70, "30"),
            _read("bs_cl__st_bank_loans_payable", "30",
                  [(0.30, {"current": "8151188", "prior": "10089294"})])]
    got = _run(rows, [NOTE_30])
    assert got[("bs_cl__st_bank_loans_payable", "bs_cl__st_bank_loans_payable")] == {
        "current": "8151188", "prior": "10089294"}
