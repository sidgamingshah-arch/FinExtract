from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, PrintedIn
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.stages.face_mapping_contract import FaceMappingContractStage


def _row(label: str, key: str | None = None, *, note: str | None = None,
         printed_in: PrintedIn = PrintedIn.FACE) -> LineItem:
    row = LineItem(source_label=label, canonical_key=key, note_number=note,
                   printed_in=printed_in)
    row.set_value(ExtractedValue(
        value=Decimal("100"), value_raw=Decimal("100"), basis=Basis.CONSOLIDATED,
        period_label="current", provenance=Provenance(page_index=0)))
    return row


def _run(doc: DocumentModel) -> PipelineContext:
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = object()
    FaceMappingContractStage().run(doc, ctx)
    return ctx


def test_an_unmapped_face_value_gets_a_unique_non_ontology_storage_key():
    doc = DocumentModel(filename="f.pdf", line_items=[_row("Administrative expenses")])

    ctx = _run(doc)

    row = doc.line_items[0]
    assert row.canonical_key.startswith(
        "engine_unclassified_face__unknown_statement__unresolved_section__")
    assert row.confidence.mapping == 0.0
    assert row.confidence.method == "engine_unclassified_face"
    assert "requires_concept_review" in row.confidence.flags
    assert "stored_unclassified=1" in ctx.logs[-1]


def test_mapped_rows_note_rows_and_value_less_headers_pass():
    header = LineItem(source_label="Notes")
    doc = DocumentModel(filename="f.pdf", line_items=[
        _row("Revenue", "is_pl__sales_revenues"),
        _row("Note detail", printed_in=PrintedIn.NOTES),
        header,
    ])

    ctx = _run(doc)

    assert ctx.logs[-1] == "face_mapping_contract:passed"


def test_an_extraction_only_run_without_an_ontology_is_not_a_mapping_run():
    ctx = PipelineContext(raw_bytes=b"")
    FaceMappingContractStage().run(
        DocumentModel(filename="f.pdf", line_items=[_row("Unmapped")]), ctx)

    assert ctx.logs[-1] == "face_mapping_contract:skipped(no ontology)"


def test_a_contained_face_aggregate_is_resolved_evidence_not_an_unmapped_fact():
    parent = _row("Reserves")
    parent.confidence.flags += [
        "unfiled_aggregate:bs_equity__reserves",
        "contains_mapped_children:bs_equity__share_premium",
    ]
    child = _row("Share premium", "bs_equity__share_premium")

    _run(DocumentModel(filename="f.pdf", line_items=[parent, child]))


def test_only_a_complete_note_split_exempts_its_parent():
    parent = _row("Prepayments and other receivables", note="12")
    parent.confidence.flags += [
        "note_decomposed_from:bs_ca__prepayments_and_other_receivables",
        "decomposed_into:bs_ca__prepayments,bs_ca__other_receivables",
    ]
    prepaid = _row("Prepayments", "bs_ca__prepayments", note="12")
    prepaid.confidence.flags.append("split_from:bs_ca__prepayments_and_other_receivables")
    other = _row("Other receivables", "bs_ca__other_receivables", note="12")
    other.confidence.flags.append("split_from:bs_ca__prepayments_and_other_receivables")

    _run(DocumentModel(filename="f.pdf", line_items=[parent, prepaid, other]))
    assert parent.canonical_key is None

    other.note_number = "13"
    _run(DocumentModel(filename="f.pdf", line_items=[parent, prepaid, other]))
    assert parent.canonical_key.startswith("engine_unclassified_face__")


def test_two_unclassified_rows_never_share_a_key_or_enter_a_real_concept():
    first = _row("First unknown")
    second = _row("Second unknown")

    _run(DocumentModel(filename="f.pdf", line_items=[first, second]))

    assert first.canonical_key != second.canonical_key
    assert first.canonical_key.startswith("engine_unclassified_face__")
    assert second.canonical_key.startswith("engine_unclassified_face__")


def test_unclassified_storage_stays_in_its_subsection_and_is_counted_unresolved():
    from app.core.models.document import PageSource
    from app.services.buckets import segment_source

    row = _row("Unknown current asset")
    row.section_hint = "CURRENT ASSETS"
    doc = DocumentModel(filename="f.pdf", line_items=[row],
                        pages=[PageSource(index=0, statement="balance_sheet")])

    _run(doc)
    buckets = segment_source(doc)

    assert str(row.id) in buckets.segment("current_assets").face_item_ids
    assert buckets.unresolved_face_item_ids == [str(row.id)]


def test_contract_runs_after_gap_closing_and_before_structural_checks():
    names = [stage.name for stage in default_pipeline().stages]
    assert names.index("gap_closing") < names.index("face_mapping_contract")
    assert names.index("face_mapping_contract") < names.index("structural")