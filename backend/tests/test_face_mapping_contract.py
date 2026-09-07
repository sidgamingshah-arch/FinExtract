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

# --- the key has to survive to the next run ------------------------------------------------------
#
# It used to be `row.id.hex`, a uuid minted per run. Measured on two identical runs of one
# 367-page filing: 66 unclassified face rows on each side and ZERO keys in common. Those rows
# carry real figures, so an operator could see a line, decide to name it, attach it under a
# template parent or put it in a formula — and none of that could be stored against anything,
# because the identity was regenerated before the next run answered. Configuring the extraction
# inventory is impossible until this is stable, which is why it is pinned here.

def test_the_same_printed_line_gets_the_same_key_on_a_second_run():
    """Determinism, asserted by running the stage twice over freshly built documents — the same
    way two runs of the same PDF reach it, rather than by re-running one object."""
    keys = []
    for _ in range(2):
        doc = DocumentModel(filename="f.pdf", line_items=[
            _row("Owners of the Company"), _row("Net exchange differences")])
        _run(doc)
        keys.append([li.canonical_key for li in doc.line_items])

    assert keys[0] == keys[1], "a second run must reproduce the keys exactly"
    assert all("__owners_of_the_company" in k or "__net_exchange_differences" in k
               for k in keys[0]), keys[0]


def test_the_key_says_what_the_line_is_rather_than_carrying_a_digest():
    """A key a person reads in a formula or a mapping table should name the caption. This is the
    difference between configuring against `owners_of_the_company` and against a uuid."""
    doc = DocumentModel(filename="f.pdf", line_items=[_row("Owners of the Company")])

    _run(doc)

    assert doc.line_items[0].canonical_key.endswith("__owners_of_the_company")


def test_one_section_printing_the_same_caption_twice_keeps_two_facts():
    """Two printed lines are two facts even when they read alike, so the second gets an occurrence
    index rather than colliding onto the first — which would silently drop a figure."""
    doc = DocumentModel(filename="f.pdf", line_items=[
        _row("Other income"), _row("Other income")])

    _run(doc)

    first, second = (li.canonical_key for li in doc.line_items)
    assert first != second
    assert first.endswith("__other_income") and second.endswith("__other_income__2")


def test_a_chinese_caption_folds_its_two_scripts_onto_one_key():
    """No usable ASCII, so the key is a digest of the NORMALISED caption — and normalisation folds
    Traditional to Simplified, so one concept printed either way is one key."""
    simplified = DocumentModel(filename="f.pdf", line_items=[_row("其他综合收益的税后净额")])
    traditional = DocumentModel(filename="f.pdf", line_items=[_row("其他綜合收益的稅後淨額")])

    _run(simplified)
    _run(traditional)

    assert (simplified.line_items[0].canonical_key
            == traditional.line_items[0].canonical_key)


def test_no_key_carries_a_uuid():
    """The regression guard. A 32-hex run of the shape uuid4().hex produces is what this replaced;
    if one reappears the key has stopped being derived from the filing."""
    import re

    doc = DocumentModel(filename="f.pdf", line_items=[
        _row("Administrative expenses"), _row("其他综合收益的税后净额")])

    _run(doc)

    for li in doc.line_items:
        assert not re.search(r"[0-9a-f]{32}", li.canonical_key), li.canonical_key
