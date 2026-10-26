"""ST OTHER LOANS PAYABLE READS THE BORROWINGS NOTE THAT SPLITS A COMBINED CURRENT ROW.

NEW FILE -> backend/tests/test_short_term_other_loans_reads_the_borrowings_note.py

China SCE 1966 prints its current borrowings as one face row, "Interest-bearing bank and other
borrowings" 9,817,976 / 10,742,959, and its note 30 splits it: "Bank loans — secured" 8,151,188 /
10,089,294 and "Other loans — secured" 1,666,788 / 653,665. ST Bank Loans Payable's definition takes
the bank part and names ST Other Loans Payable for the rest — but ST Other Loans Payable was read on
the face only, and its own definition said that combined row "is read as 'ST Bank Loans Payable'".
So the other loans reached no line, and total current liabilities published 129,866,020 /
122,997,224 against a printed 131,532,808 / 123,650,889.

The line now declares no route — the face and the notes, as ST Bank Loans Payable does — and note
terms naming the note of a combined row; its definition takes the other loans part of that row, and
a row of other loans printed on its own as printed.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import BBox
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NoteRef, NotesTable,
                                       Provenance)
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import derivation, line_item_notes, line_item_routes
from app.stages.note_sourced import _settle_against_the_face

_SEED = (Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")
OTHER, BANK = "bs_cl__st_other_loans_payable", "bs_cl__st_bank_loans_payable"


def _shipped(key: str):
    st = load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)
    return next(i for i in st.items if i.key == key)


def _titled(number: str, title: str, captions: tuple[str, ...]) -> NotesTable:
    table = NotesTable(note_number=number, title=title, page_index=9)
    for caption in captions:
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("1"), value_raw=Decimal("1"),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


def _sent(notes: list[NotesTable]) -> list[str]:
    item = _shipped(OTHER)
    return [h.note for h in line_item_notes.note_sets([item], notes).get(item.key, [])]


# ── the route and the notes the request carries ─────────────────────────────────────────────────

def test_the_line_may_read_the_face_and_the_notes():
    item = _shipped(OTHER)
    assert line_item_routes.may_read_notes(item) and line_item_routes.may_read_face(item)
    assert list(item.note_source.note_terms)


def test_a_combined_row_s_note_is_sent_and_a_bank_only_note_is_not():
    # 1966 note 30 and Grand Ming note 21: one note for bank and other loans.
    assert _sent([_titled("30", "INTEREST-BEARING BANK AND OTHER 30. 計息銀行及其他貸款 BORROWINGS",
                          ("Current Bank loans — secured", "Other loans — secured", "Total — current")),
                  _titled("29", "TRADE AND BILLS PAYABLES", ("Trade payables",))]) == ["30"]
    assert _sent([_titled("21", "BANK AND OTHER LOANS 21. 銀行及其他貸款",
                          ("Bank loans Secured 銀行貸款", "Other secured loans 其他有抵押貸款")),
                  _titled("20", "TRADE AND OTHER PAYABLES", ("Trade payables",))]) == ["21"]
    # Lai Sun: OTHER BORROWINGS is this line's note, BANK BORROWINGS is not.
    assert _sent([_titled("37", "BANK BORROWINGS", ("Secured bank loans",)),
                  _titled("38", "OTHER BORROWINGS", ("Other borrowing — unsecured",))]) == ["38"]
    # A note of bank borrowings only (Hon Kwok 26, BYD 27), and a CAS 短期借款 note, are not sent.
    assert "26" not in _sent([_titled("26", "INTEREST-BEARING BANK BORROWINGS",
                                      ("Bank loans — secured",))])
    assert _sent([_titled("27", "INTEREST-BEARING LOANS 27. 計息貸款", ("Bank loans",))]) == []
    assert _sent([_titled("七、32", "短期借款分类", ("信用借款", "保证借款")),
                  _titled("七、45", "长期借款分类", ("信用借款",))]) == []


# ── the two definitions split a combined row the same way ───────────────────────────────────────

def _definition(key: str) -> str:
    return next(i for i in json.loads(_SEED.read_text(encoding="utf-8"))["items"]
                if i["key"] == key)["definition"]


def test_the_bank_line_and_the_other_loans_line_split_a_combined_row_the_same_way():
    bank, other = _definition(BANK), _definition(OTHER)
    assert "this line is the bank loans part only" in bank
    assert "the other loans are 'ST Other Loans Payable'" in bank
    assert "this line is the other loans part of that row" in other
    assert "the bank loans part is 'ST Bank Loans Payable'" in other
    assert "it is read as 'ST Bank Loans Payable'" not in other
    # A row of other loans printed on its own is taken as printed: Lai Sun's "Other borrowings"
    # 775,973 includes a note payable of 412,182, and the line is still 775,973.
    assert "this line is that row as printed, whatever its breakdown lists" in other
    # Nothing is taken from a combined row the breakdown does not split.
    assert "this line takes nothing from it" in other


# ── the money is counted once, whichever way the bank part is read ──────────────────────────────

FACE, NOTES = 0, 1


def _prov(page: int, y: float, period: str) -> Provenance:
    x = 0.6 if period == "current" else 0.8
    return Provenance(source_kind="pdf", page_index=page,
                      bbox=BBox(x0=x, y0=y, x1=x + 0.1, y1=y + 0.01))


def _ev(amount: str, page: int, y: float, period: str) -> ExtractedValue:
    return ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                          basis=Basis.CONSOLIDATED, period_label=period,
                          provenance=_prov(page, y, period))


def _face(key: str, caption: str, amounts: dict[str, str], y: float) -> LineItem:
    li = LineItem(source_label=caption, canonical_key=key)
    for period, amount in amounts.items():
        li.set_value(_ev(amount, FACE, y, period))
    li.note_refs = [NoteRef(raw="30", numbers=["30"])]
    return li


def _read(key: str, y: float, amounts: dict[str, str]) -> LineItem:
    """A line the model filled from one row of note 30, printed at `y` on the notes page."""
    li = LineItem(source_label=key, canonical_key=key)
    store = None
    for period, amount in amounts.items():
        li.set_value(_ev(amount, NOTES, y, period))
        store = derivation.record(store, basis="consolidated", period_label=period,
                                  derivation=derivation.build(
                                      method="line_item_llm:cited_rows", formula=None,
                                      inputs=[{"label": "row", "note": "30", "value": amount,
                                               "provenance": _prov(NOTES, y, period).model_dump(
                                                   mode="json")}],
                                      result=amount))
    li.derivation = store
    return li


def _note_30() -> NotesTable:
    table = NotesTable(note_number="30", title="note 30", source_pages=[NOTES])
    for ordinal, (caption, amounts, y, role) in enumerate([
            ("Current Bank loans — secured", {"current": "8151188", "prior": "10089294"}, 0.30,
             LineRole.LINE),
            ("Other loans — secured", {"current": "1666788", "prior": "653665"}, 0.32,
             LineRole.LINE),
            ("Total — current", {"current": "9817976", "prior": "10742959"}, 0.34,
             LineRole.TOTAL)]):
        row = NoteItem(raw_label=caption, ordinal=ordinal, role=role)
        for period, amount in amounts.items():
            row.set_value(_ev(amount, NOTES, y, period))
        table.items.append(row)
    return table


COMBINED = {"current": "9817976", "prior": "10742959"}
BANK_PART = {"current": "8151188", "prior": "10089294"}
OTHER_PART = {"current": "1666788", "prior": "653665"}


def _settled(rows: list[LineItem]) -> dict[str, dict[str, str]]:
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = rows
    doc.notes = [_note_30()]
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[])
    items = [SimpleNamespace(key=k, section_scope=["bs_cl"], parent="", in_output=True)
             for k in (BANK, OTHER)]
    _settle_against_the_face(doc, ctx, items)
    return {li.canonical_key: {ev.period_label: str(ev.value) for ev in li.values.values()}
            for li in doc.line_items}


def test_the_bank_part_and_the_other_loans_read_from_the_note_add_up_to_the_combined_row():
    got = _settled([_read(BANK, 0.30, BANK_PART), _read(OTHER, 0.32, OTHER_PART)])
    assert got == {BANK: BANK_PART, OTHER: OTHER_PART}


def test_other_loans_inside_a_combined_row_the_bank_line_keeps_whole_are_not_counted_again():
    """Where ST Bank Loans Payable keeps the face row whole (the answer-free route binds it so),
    the other loans the model read from the note are inside it, and the line gives them up."""
    got = _settled([_face(BANK, "Interest-bearing bank and other borrowings", COMBINED, 0.50),
                    _read(OTHER, 0.32, OTHER_PART)])
    assert got == {BANK: COMBINED, OTHER: {"current": "0", "prior": "0"}}
