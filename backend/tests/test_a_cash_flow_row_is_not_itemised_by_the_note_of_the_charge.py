"""A CASH-FLOW ROW IS NOT ITEMISED BY THE NOTE THAT ITEMISES THE CHARGE.

Moroil 2025 prints "Interest paid 28" (26,946) / (29,272) on its cash flow (printed page 69) and
"Finance costs 28" on its income statement (p67). Note 28 (p122) itemises the finance COSTS — bank
overdrafts (4,841), loans (16,427), leases (5,678) — and the note decomposition read it as the
breakdown of the cash-flow row: the payment landed on Interest Expense, the add-back of the charge,
and Interest Paid (Oper) was left empty, cited to the note's "- Bank overdrafts" cell.

The note the income statement's own rows cite is the charge's, so a cash-flow row citing it is not
split by it (`map_ontology._split_from_disclosure`, the cash-flow gate). A cash-flow row citing a
note of its own is still split as before.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

from app.core.models import DocumentModel
from app.core.models.enums import Basis, PageKind, PrintedIn
from app.core.stage import PipelineContext

_SAMPLES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _value(value, period: str, page: int):
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue

    return ExtractedValue(value=Decimal(str(value)), value_raw=Decimal(str(value)),
                          basis=Basis.CONSOLIDATED, period_label=period,
                          provenance=Provenance(page_index=page))


def _face_line(caption: str, key: str, current, prior, note: str, page: int):
    from app.core.models.line_item import LineItem, NoteRef

    li = LineItem(source_label=caption, canonical_key=key, printed_in=PrintedIn.FACE,
                  note_number=note, note_refs=[NoteRef(raw=note, numbers=[note])])
    li.set_value(_value(current, "current", page))
    li.set_value(_value(prior, "prior", page))
    return li


def _note_table(number: str, title: str, rows, page: int):
    from app.core.models.line_item import NoteItem, NotesTable

    table = NotesTable(note_number=number, title=title, basis=Basis.CONSOLIDATED,
                       source_pages=[page])
    for caption, current, prior in rows:
        item = NoteItem(raw_label=caption)
        item.set_value(_value(current, "current", page))
        item.set_value(_value(prior, "prior", page))
        table.items.append(item)
    return table


def _doc(faces, notes):
    from app.core.models.document import PageSource

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, statement="profit_and_loss"),
                 PageSource(index=2, kind=PageKind.FACE, statement="cash_flow"),
                 PageSource(index=5, kind=PageKind.NOTES)]
    doc.line_items = list(faces)
    doc.notes = list(notes)
    return doc


def _split(doc):
    from app.config import get_settings
    from app.services.mapping import OntologyMatcher
    from app.schemas.loader import load_ontology, load_template
    from app.stages.map_ontology import MapOntologyStage

    raw = json.loads((_SAMPLES / "output_csv_hk_ontology.json").read_text(encoding="utf-8"))
    ontology = load_ontology(raw, resolve=True)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = ontology
    ctx.template = load_template(json.loads(
        (_SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8")))
    logs: list[str] = []
    ctx.log = logs.append
    matcher = OntologyMatcher(ontology, settings=ctx.settings)
    return MapOntologyStage()._split_from_disclosure(doc, ontology, matcher, ctx), logs


# As the note prints it: the rows under "Interest expense:", read with their heading.
_NOTE_28 = [("Interest expense: - Bank overdrafts", -4841, -5682),
            ("Interest expense: - Short term bank loans", -16427, -18768),
            ("Interest expense: - Lease liabilities", -5678, -4822)]


def test_interest_paid_is_not_split_by_the_finance_costs_note():
    doc = _doc([_face_line("Finance costs", "is_pl__interest_expense", -26946, -29272, "28", 0),
                _face_line("Interest paid", "cf_oper_indirect__interest_paid_oper", -26946, -29272,
                           "28", 2)],
               [_note_table("28", "Finance costs", _NOTE_28, 5)])

    _added, logs = _split(doc)

    paid = [li for li in doc.line_items if li.source_label == "Interest paid"]
    assert [li.canonical_key for li in paid] == ["cf_oper_indirect__interest_paid_oper"], [
        (li.source_label, li.canonical_key) for li in doc.line_items]
    assert {ev.period_label: ev.value for ev in paid[0].values.values()} == {
        "current": Decimal("-26946"), "prior": Decimal("-29272")}
    assert not any(li.canonical_key == "cf_oper_indirect__interest_expense"
                   for li in doc.line_items)
    assert any(line.startswith("map_line_items:split_declined("
                               "cf_oper_indirect__interest_paid_oper): its note 28 is the income "
                               "statement's") for line in logs), logs
