"""A note a published figure was READ FROM is published with it.

`prune_notes` publishes the notes the FACE points at, and the face points at the note that EXPLAINS a
line. The note-sourced routes fill parts from a note no face row cites — a mainland related-party
table is printed under its chapter's own number, and 澜起科技 688008's 应收账款 英特尔公司 balance
is read from one — and the pruner then dropped that note after the figure had been published from
it, so the trail behind the figure pointed at a note the result did not contain.

The trail names its note on every input (`derivation.build(inputs=[{"note": …}])`), from both the
deterministic route and a cited row, so that is what is read here.
"""
from __future__ import annotations

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, NotesTable, Provenance
from app.core.stage import PipelineContext
from app.services import derivation
from app.stages.prune_notes import PruneNotesStage


def _doc() -> DocumentModel:
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=0, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=1, kind=PageKind.NOTES))
    return doc


def _face(label: str, note: str) -> LineItem:
    li = LineItem(source_label=label, note_number=note)
    li.values["k"] = ExtractedValue(period_label="current", basis=Basis.CONSOLIDATED, value=None,
                                    provenance=Provenance(source_kind="pdf", page_index=0))
    return li


def _part(key: str, *notes: str, counted: bool = True) -> LineItem:
    row = LineItem(source_label=key, canonical_key=key)
    row.derivation = derivation.record(None, basis="consolidated", period_label="current",
                                       derivation=derivation.build(
                                           method="note_sourced:sum_of_1_rows", formula=None,
                                           inputs=[{"label": "英特尔公司", "note": n, "value": "1",
                                                    "counted": counted} for n in notes],
                                           result="1"))
    return row


def _note(number: str) -> NotesTable:
    return NotesTable(note_number=number, title=f"note {number}", source_pages=[1])


def _run(doc: DocumentModel) -> list[str]:
    ctx = PipelineContext()
    ctx.settings.extraction.prune_unreferenced_notes = True
    PruneNotesStage().run(doc, ctx)
    return [n.note_number for n in doc.notes]


def test_the_note_a_part_was_read_from_is_kept():
    doc = _doc()
    doc.line_items = [_face("应收账款", "七、5"), _part("sub__rp_trade_receivable_gross", "十四、1")]
    doc.notes = [_note("七、5"), _note("十四、1"), _note("十五、1")]
    assert _run(doc) == ["七、5", "十四、1"]


def test_an_alternative_the_trail_shows_keeps_its_note_too():
    """The trail shows an alternative the rollup did not take, and it is only checkable with its
    note there to click through to."""
    doc = _doc()
    doc.line_items = [_face("应收账款", "七、5"), _part("sub__x", "十四、2", counted=False)]
    doc.notes = [_note("七、5"), _note("十四、2")]
    assert _run(doc) == ["七、5", "十四、2"]


def test_without_a_trail_the_uncited_note_is_still_dropped():
    doc = _doc()
    doc.line_items = [_face("应收账款", "七、5")]
    doc.notes = [_note("七、5"), _note("十四、1")]
    assert _run(doc) == ["七、5"]
