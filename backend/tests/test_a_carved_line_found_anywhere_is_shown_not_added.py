"""A CARVED LINE IS SHOWN, NOT ADDED, WHEREVER IN THE REPORT ITS FIGURE WAS FOUND.

NEW FILE -> backend/tests/test_a_carved_line_found_anywhere_is_shown_not_added.py

`carved_from_face` (`LineItemDef`) says a line's figure, read off the face, is money the face already
prints inside other captions: it is shown on its line and added into no total
(`stages.note_sourced.carve_note_figures`). The `anywhere` route lets the model find such a figure on
any page of the report, and the blind answers to the whole-report requests found two ways past the
carve. Measured on the three mainland reference filings:

  * A FIGURE STATED IN A SENTENCE WAS WRITTEN WITH NO PAGE. `LineItemLlmStage._write_prose` wrote its
    slot without a provenance, so `buckets` stamped the row a face row and the carve, which reads
    the figure's page, never saw it. 河钢股份's capitalised interest, 493,877,376.21, cited by its
    sentence on page 143, was added into net interest.
  * LEASE INTEREST READ FROM A CAS NOTE WAS NOT CARVED. A CAS income statement prints 其中：利息费用,
    and the 财务费用 note prints 其中：租赁负债利息费用 inside it, so net interest added the note's figure
    beside the face row that already holds it. Profit before tax: 澜起科技 1,414,130,448.45 against a
    printed 1,412,617,850.07 (its whole 1,512,598.38 of interest is lease interest), 迈捷
    376,576,510.05 against 375,389,209.87, and 河钢股份 1,611,021,583.78 against 924,303,090.57 with
    both. An HKFRS filing's lease interest is the row the note-to-face split carves out of the
    finance costs and stamps FACE (`split_from:is_pl__interest_expense`), and it stays in the total.
  * AND THE CARVE READS EVERY PAGE THAT IS NOT A STATEMENT, not only the notes: an `anywhere` line
    may cite the directors' report for a figure the face already holds, and a notes-only test let
    that figure into the total.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis, PrintedIn
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.services import rollups
from app.stages.line_item_llm import LineItemLlmStage
from app.stages.note_sourced import carve_note_figures

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
FACE, NOTES, OTHER, UNKNOWN = 0, 1, 2, 3
CAPITALISED = "is_pl__interest_expense_capitalised"
LEASE = "is_pl__interest_on_lease_liabilities"
STAFF = "is_pl__personnel_and_benefit_expenses"
INTEREST = "is_pl__interest_expense"


def _template() -> dict:
    return json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text("utf-8"))


def _shipped_carved() -> set[str]:
    items = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text("utf-8"))["items"]
    return {i["key"] for i in items if i.get("carved_from_face")}


def _doc(*rows: LineItem) -> DocumentModel:
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=FACE, kind=PageKind.FACE),
                 PageSource(index=NOTES, kind=PageKind.NOTES),
                 PageSource(index=OTHER, kind=PageKind.OTHER),
                 PageSource(index=UNKNOWN, kind=PageKind.UNKNOWN)]
    doc.line_items = list(rows)
    return doc


def _row(key: str, amount: str, page: int, printed_in=None) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key, printed_in=printed_in)
    li.set_value(ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                                basis=Basis.CONSOLIDATED, period_label="current",
                                provenance=Provenance(page_index=page)))
    return li


def _carve(doc: DocumentModel, keys: set[str]) -> PipelineContext:
    ctx = PipelineContext()
    ctx.line_items = SimpleNamespace(items=[SimpleNamespace(key=k, carved_from_face=True)
                                            for k in keys])
    ctx.template = _template()
    carve_note_figures(doc, ctx, by="test")
    return ctx


def _carved(li: LineItem) -> bool:
    return all(rollups.CARVED_FROM_FACE in ev.confidence.flags for ev in li.values.values())


def _served(key: str, value: str, flags=()) -> dict:
    return {"canonical_key": key, "values": [{
        "basis": "consolidated", "period_label": "current", "value": value,
        "confidence": {"flags": list(flags)}}]}


# ── THE SENTENCE ─────────────────────────────────────────────────────────────────────────────

def test_a_figure_stated_in_a_sentence_is_written_with_its_page_and_carved():
    """河钢股份's capitalised interest, cited by the sentence on page 143 that states it."""
    face = _row(INTEREST, "-5144993111.59", FACE, PrintedIn.FACE)
    row = LineItem(source_label="Interest expense - capitalised", canonical_key=CAPITALISED)
    doc = _doc(face, row)
    sentence = {"note": "", "statement": "", "title": "", "page": NOTES + 1,
                "figures": {"prose": "493877376.21"},
                "provenance": {"page_index": NOTES, "source": "page_prose"},
                "quote": "合计 493,877,376.21", "prose": True, "on_face": False,
                "prose_scale": None, "prose_prior": None, "prose_money": True}
    ctx = PipelineContext()
    LineItemLlmStage._write_prose(row, doc, Decimal("493877376.21"), [sentence], ctx,
                                  SimpleNamespace(key=CAPITALISED, label=row.source_label))
    (ev,) = row.values.values()
    assert ev.value == Decimal("493877376.21")
    assert ev.provenance is not None and ev.provenance.page_index == NOTES, \
        "the slot says which page states its figure, as a cited row's does"

    _carve(doc, {CAPITALISED})
    assert _carved(row), "a figure read off a notes page is inside the face's finance costs"


def test_a_sentence_replacing_a_figure_an_earlier_writer_derived_takes_its_page():
    """The slot follows the figure: where a sentence replaces a derived one, the page goes too."""
    row = _row(CAPITALISED, "1", FACE)
    (held,) = row.values.values()
    held.derived = True
    doc = _doc(_row(INTEREST, "-100", FACE, PrintedIn.FACE), row)
    sentence = {"note": "7", "title": "FINANCE COSTS", "figures": {"prose": "88309"},
                "provenance": {"page_index": NOTES, "source": "note_prose"},
                "quote": "interest of 88,309 was capitalised", "prose": True}
    LineItemLlmStage._write_prose(row, doc, Decimal("88309"), [sentence], PipelineContext(),
                                  SimpleNamespace(key=CAPITALISED, label=CAPITALISED))
    assert (held.value, held.provenance.page_index) == (Decimal("88309"), NOTES)


# ── EVERY PAGE THAT IS NOT A STATEMENT ───────────────────────────────────────────────────────

def test_a_carved_figure_read_off_any_page_but_a_statement_is_carved():
    """The directors' report is not a notes page, and a staff cost it states is still inside the
    face's function costs; a figure on a statement page is the face's own and is never carved."""
    notes, other, unknown = (_row(STAFF, "943489", page) for page in (NOTES, OTHER, UNKNOWN))
    on_face = _row(STAFF, "179257", FACE)
    for li in (notes, other, unknown, on_face):
        _carve(_doc(li), {STAFF})
    assert _carved(notes) and _carved(other) and _carved(unknown)
    assert not _carved(on_face)


def test_a_face_row_is_never_carved_wherever_its_figure_was_re_read():
    """PetroChina's by-nature "Employee compensation costs" is a face row: kept in every total."""
    staff = _row(STAFF, "-179257", OTHER, PrintedIn.FACE)
    _carve(_doc(staff), {STAFF})
    assert not _carved(staff)


# ── LEASE INTEREST ───────────────────────────────────────────────────────────────────────────

def test_the_hk_set_carves_lease_interest():
    assert LEASE in _shipped_carved()


def test_lease_interest_a_cas_note_prints_inside_the_faces_interest_is_not_added_again():
    """澜起科技: 其中：利息费用 1,512,598.38 on the face, 租赁负债利息费用 1,512,598.38 in note 七、66."""
    lease = _row(LEASE, "1512598.38", NOTES)
    doc = _doc(_row(INTEREST, "-1512598.38", FACE, PrintedIn.FACE),
               _row("is_pl__interest_income", "229704818.49", FACE, PrintedIn.FACE), lease)
    _carve(doc, _shipped_carved())
    assert _carved(lease)
    assert lease.values[next(iter(lease.values))].value == Decimal("1512598.38"), \
        "shown as the note prints it: a carved figure moves no total, whatever its sign"

    shown = rollups.figures_as_shown(_template(), [
        _served(INTEREST, "-1512598.38"), _served("is_pl__interest_income", "229704818.49"),
        _served(LEASE, "1512598.38", [rollups.CARVED_FROM_FACE])], "consolidated", "current")
    assert shown[LEASE] == 1512598.38
    assert round(shown["is_pl__net_interest_income_expense"], 2) == round(
        229704818.49 - 1512598.38, 2)


def test_lease_interest_the_split_carved_out_of_the_finance_costs_stays_in_the_total():
    """BYD Electronic: the split stores the note's -73,088 on a row it stamps FACE."""
    lease = _row(LEASE, "-73088", NOTES, PrintedIn.FACE)
    _carve(_doc(_row(INTEREST, "-150000", FACE, PrintedIn.FACE), lease), _shipped_carved())
    assert not _carved(lease)
