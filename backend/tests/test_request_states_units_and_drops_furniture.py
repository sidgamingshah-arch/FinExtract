"""WHAT A MODEL REQUEST CARRIES: the unit of every block, the statement's own rows only, and note
prose without the page template.

Found on f1 (the stand-in capture, `docs/testing/llm-standin.md`):

  * NO REQUEST STATED A UNIT — lint `no_unit` on 153 of 153 — while the set's own prompt warns the
    figures are "often scaled in lakhs or crores; do not assume either".
  * THE PROFIT AND LOSS BLOCK HELD ITS FOOTER AND SIGNATURE BLOCK ("The accompanying notes",
    "As per our report of even date"), and every standalone row arrived "under" the company's CIN.
  * NOTE PROSE CARRIED THE RUNNING HEADER ("DEVGIRI AUTOCOMP LIMITED Annual Report 2024-25") and
    the folio glued to the last figure ("1,071.55 11").
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind, PrintedIn
from app.core.models.geometry import Provenance
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable,
                                       UnitContext)
from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
from app.services import face_context, note_context, units

LAKHS = UnitContext(currency="INR", scale_factor=Decimal(100000), units_label="lakh")


def _value(page, value="100", unit=None):
    return ExtractedValue(basis=Basis.STANDALONE, period_label="current", value=Decimal(value),
                          value_raw=Decimal(value), provenance=Provenance(page_index=page),
                          unit_ctx=unit or UnitContext())


def _row(caption, page, *, banner=None, unit=None, printed_in=None):
    li = LineItem(source_label=caption)
    if banner:
        li.section_hint = banner
    li.printed_in = printed_in
    li.values["v"] = _value(page, unit=unit)
    return li


def _doc(rows, pages, *, unit=LAKHS, chrome=()):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = pages
    doc.line_items = list(rows)
    doc.unit_context = unit
    doc.page_chrome = list(chrome)
    return doc


def _pl(i=3):
    return PageSource(index=i, kind=PageKind.FACE, statement="profit_and_loss")


# --- 1. units ------------------------------------------------------------------------------------

def test_a_unit_is_the_rows_own_else_the_documents_else_not_stated():
    assert units.describe(UnitContext(currency="HKD", scale_factor=Decimal(1000)), LAKHS) \
        == "HKD thousands (x1,000)"
    # f1's face rows: the "(₹ in lakhs)" line was read as a label the reader could not resolve.
    seen = UnitContext(currency="INR", units_label="₹inlakhs")
    assert units.describe(seen, LAKHS) == "INR lakhs (x100,000)"
    assert units.describe(seen, None) == "INR, as printed '₹inlakhs'"
    assert units.describe(UnitContext(), None) == "not stated"


def test_every_statement_block_states_its_unit_and_rows_that_differ_say_so():
    doc = _doc([_row("Revenue", 3), _row("Other income", 3)], [_pl()])
    [block] = face_context.face_rows(doc, ["profit_and_loss"])
    assert block["unit"] == "INR lakhs (x100,000)"
    assert all("unit" not in r and "_unit" not in r for r in block["rows"])

    doc = _doc([_row("Revenue", 3),
                _row("Other income", 3, unit=UnitContext(currency="USD",
                                                         scale_factor=Decimal(1000000)))],
               [_pl()])
    [block] = face_context.face_rows(doc, ["profit_and_loss"])
    assert block["unit"].startswith("varies by row")
    assert [r["unit"] for r in block["rows"]] == ["INR lakhs (x100,000)", "USD millions (x1,000,000)"]


def _note_doc(chrome=(), folio="11"):
    table = NotesTable(note_number="14", title="Other current assets", source_pages=[10],
                       source_text="DEVGIRI AUTOCOMP LIMITED Annual Report 2024-25 Particulars "
                                   "As at 31 March 2025 Total 1,196.15 1,071.55 11",
                       items=[NoteItem(raw_label="Total", values={"v": _value(10, "1196.15")})])
    doc = _doc([], [PageSource(index=10, kind=PageKind.NOTES, printed_page=folio)],
               chrome=chrome)
    doc.notes = [table]
    items = [LineItemDef(key="k", label="k",
                         note_source=NoteSource(note_title_any=["Other current assets"]))]
    return LineItemSet(items=items), doc


def test_every_note_block_states_its_unit():
    cfg, doc = _note_doc()
    [note] = note_context.identified_notes(cfg, doc.notes, doc=doc)
    assert note["unit"] == "INR lakhs (x100,000)"
    assert all("_unit" not in r for r in note["rows"])
    # No document: the row's own unit, which here is none.
    [note] = note_context.identified_notes(cfg, doc.notes)
    assert note["unit"] == "not stated"


# --- 2. a statement block carries that statement only ------------------------------------------

def test_rows_the_run_marks_as_notes_stay_out_of_a_statement_block():
    pages = [_pl(3), PageSource(index=12, kind=PageKind.NOTES, statement="profit_and_loss")]
    doc = _doc([_row("Revenue", 3), _row("Lease liabilities", 12),
                _row("Borrowings repayable in instalments", 3, printed_in=PrintedIn.NOTES)], pages)
    [block] = face_context.face_rows(doc, ["profit_and_loss"])
    assert [r["caption"] for r in block["rows"]] == ["Revenue"]


def test_the_footer_and_signature_block_is_not_a_row_of_the_statement():
    doc = _doc([_row("Revenue", 3), _row("The accompanying notes", 3),
                _row("As per our report of even date attached. For and on behalf of", 3),
                _row("Membership No. 123456", 3)], [_pl()])
    [block] = face_context.face_rows(doc, ["profit_and_loss"])
    assert [r["caption"] for r in block["rows"]] == ["Revenue"]


def test_a_company_identity_line_is_not_a_section_heading():
    doc = _doc([_row("Revenue", 3, banner="CIN: L29301MH1996PLC101482"),
                _row("Other income", 3, banner="DEVGIRI AUTOCOMP LIMITED"),
                _row("Finance costs", 3, banner="Annual Report 2024-25"),
                _row("Current tax", 3, banner="VIII. Tax expense:")], [_pl()])
    [block] = face_context.face_rows(doc, ["profit_and_loss"])
    assert [r.get("under") for r in block["rows"]] == [None, None, None, "VIII. Tax expense:"]


# --- 3. note prose without the page template ---------------------------------------------------

def test_note_prose_is_sent_without_the_running_header_or_the_folio():
    cfg, doc = _note_doc(chrome=["devgiri autocomp limited", "annual report -", "as at march", "."])
    [note] = note_context.identified_notes(cfg, doc.notes, doc=doc)
    assert note["prose"] == "Particulars As at 31 March 2025 Total 1,196.15 1,071.55"


def test_a_number_ending_the_prose_is_kept_unless_it_is_that_pages_folio():
    cfg, doc = _note_doc(chrome=["devgiri autocomp limited"], folio="12")
    [note] = note_context.identified_notes(cfg, doc.notes, doc=doc)
    assert note["prose"].endswith("1,071.55 11")
    assert not note["prose"].startswith("DEVGIRI")
