"""A NOTE FIGURE THE FACE ALREADY CARRIES INSIDE ITS CAPTIONS IS SHOWN, NOT ADDED.

NEW FILE -> backend/tests/test_a_note_figure_the_face_already_carries_is_not_added.py

A by-function income statement charges its staff costs inside cost of sales, selling,
administrative and R&D expenses, and the profit-before-tax note states them again by nature. The
model route read the note's total onto Personnel & Benefit Expenses and Net Operating Profit added
it beside the function lines that already hold it: BYD Electronic published a profit before tax of
-14,079,152 against a printed 4,740,416 (staff costs 18,965,744); Far East Horizon and Lai Sun the
same way. Interest capitalised is taken out of the finance costs before the face prints them, and
Hon Kwok's net interest counted it a second time (-88,309). A line declaring `carved_from_face`
shows its note figure and no total adds it; a face that prints the line keeps it in every total.
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
from app.stages.note_sourced import FACE_COST_SIGN, carve_note_figures

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
FACE, NOTES = 0, 1
STAFF = "is_pl__personnel_and_benefit_expenses"
SELLING = "is_pl__selling_and_marketing_expenses"


def _row(key: str, amount: str, page: int, printed_in=None) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key, printed_in=printed_in)
    li.set_value(ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                                basis=Basis.CONSOLIDATED, period_label="current",
                                provenance=Provenance(source_kind="pdf", page_index=page)))
    return li


def _carve(*rows: LineItem) -> PipelineContext:
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = list(rows)
    ctx = PipelineContext()
    ctx.line_items = SimpleNamespace(items=[SimpleNamespace(key=STAFF, carved_from_face=True),
                                            SimpleNamespace(key=SELLING, carved_from_face=False)])
    ctx.template = json.loads((TEMPLATES / "output_csv_hk_v1_template.json").read_text("utf-8"))
    carve_note_figures(doc, ctx, by="test")
    return ctx


def _flags(li: LineItem) -> list[str]:
    (ev,) = li.values.values()
    return list(ev.confidence.flags)


def test_the_hk_set_declares_staff_costs_and_capitalised_interest():
    items = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text("utf-8"))["items"]
    assert {i["key"] for i in items if i.get("carved_from_face")} == {
        STAFF, "is_pl__interest_expense_capitalised"}


def test_staff_costs_read_from_a_note_are_carved_and_shown_in_the_faces_cost_sign():
    """BYD prints its function costs in brackets; the note's 18,965,744 is shown negative too."""
    staff = _row(STAFF, "18965744", NOTES)
    selling = _row(SELLING, "-1888464", FACE, PrintedIn.FACE)
    ctx = _carve(staff, selling)
    (ev,) = staff.values.values()
    assert rollups.CARVED_FROM_FACE in ev.confidence.flags and FACE_COST_SIGN in ev.confidence.flags
    assert (ev.value, ev.value_raw) == (Decimal("-18965744"), Decimal("18965744"))
    assert rollups.CARVED_FROM_FACE not in _flags(selling)
    assert any("note_figures_carved=1" in line for line in ctx.logs)


def test_on_a_face_that_prints_its_costs_unsigned_the_figure_keeps_its_printed_sign():
    """A CAS statement prints 销售费用 unsigned, and stores it so: the carved figure stays positive."""
    staff = _row(STAFF, "52000000", NOTES)
    _carve(staff, _row(SELLING, "106148504.70", FACE, PrintedIn.FACE))
    (ev,) = staff.values.values()
    assert rollups.CARVED_FROM_FACE in ev.confidence.flags
    assert ev.value == Decimal("52000000")


def test_a_face_that_prints_the_line_keeps_it_in_every_total():
    """PetroChina's by-nature statement prints "Employee compensation costs" as a row of its own."""
    staff = _row(STAFF, "-179257", FACE, PrintedIn.FACE)
    _carve(staff)
    assert rollups.CARVED_FROM_FACE not in _flags(staff)


_PL = {"statements": [{"type": "income_statement", "sections": [{
    "node_id": "is_pl", "canonical_key": "is_pl", "label": "PL", "role": "header", "children": [
        {"node_id": "gp", "canonical_key": "is_pl__gross_profit", "label": "GP", "role": "line"},
        {"node_id": "sm", "canonical_key": SELLING, "label": "S&M", "role": "line"},
        {"node_id": "st", "canonical_key": STAFF, "label": "Staff", "role": "line"},
        {"node_id": "nop", "canonical_key": "is_pl__net_operating_profit", "label": "NOP",
         "role": "subtotal", "rollup": {"op": "sum", "children": [
             "is_pl__gross_profit", SELLING, STAFF],
             "cost_magnitude_children": [SELLING, STAFF]}}]}]}]}


def _served(key, value, flags=()):
    return {"canonical_key": key, "values": [{
        "basis": "consolidated", "period_label": "current", "value": str(value),
        "confidence": {"flags": list(flags)}}]}


def test_operating_profit_does_not_add_a_carved_staff_cost():
    rows = [_served("is_pl__gross_profit", 12301306), _served(SELLING, -1888464),
            _served(STAFF, -18965744, [rollups.CARVED_FROM_FACE])]
    shown = rollups.figures_as_shown(_PL, rows, "consolidated", "current")
    assert shown[STAFF] == -18965744
    assert shown["is_pl__net_operating_profit"] == 12301306 - 1888464
