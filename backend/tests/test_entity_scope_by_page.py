"""Whose figures a PAGE presents, when no column header on it says.

The two mechanisms that existed before this file both need something printed in the value area:
``row_reconstruct._basis_bands`` needs a two-basis column header ("Group | Company") over the
figures, and ``scope_selection.entity_scope.company_only_markers`` needs a specific line to be
present on the face. An HKEX filing prints the Company's statement of financial position on its
OWN page, past the notes, titled only "STATEMENT OF FINANCIAL POSITION" — no column header names
an entity and the marker line need not appear. Both mechanisms are silent, the page reads as
consolidated, and because it shares every label with the Group's balance sheet the spread ADDS the
Company's figures to the Group's.

The classifier already knows: ``classify._scope_of`` resolves the page's entity scope from its
title and its position relative to the notes, and writes it to ``PageSource.scope``. These tests
are about that verdict reaching the figures.
"""
from __future__ import annotations

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat
from app.core.stage import PipelineContext
from app.stages.classify import ClassifyStage
from app.stages.extract import ExtractStage
from app.stages.ingest import IngestStage


def _run(data: bytes) -> tuple[DocumentModel, list[str]]:
    doc = DocumentModel(filename="filing.pdf", fmt=DocFormat.PDF)
    ctx = PipelineContext(raw_bytes=data)
    for stage in (IngestStage(), ClassifyStage(), ExtractStage()):
        doc = stage.run(doc, ctx)
    return doc, ctx.logs


def _by_page(doc: DocumentModel) -> dict[int, dict[str, dict[tuple[str, str], object]]]:
    """{page_index: {source_label: {(basis, period): value}}} — the figures as extracted."""
    out: dict[int, dict[str, dict[tuple[str, str], object]]] = {}
    for li in doc.line_items:
        for ev in li.values.values():
            page = ev.provenance.page_index if ev.provenance else None
            if page is None:
                continue
            slot = (ev.basis.value, ev.period_label or "")
            out.setdefault(page, {}).setdefault(li.source_label, {})[slot] = ev.value
    return out


@pytest.fixture(scope="module")
def filing():
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_company_statement_after_notes_pdf

    return _run(make_company_statement_after_notes_pdf())


def test_the_classifier_already_knows_which_page_is_the_companys(filing):
    """The input to everything below, asserted separately so a failure downstream is never
    misread as the classifier having changed its mind."""
    doc, _ = filing
    scopes = {p.index: p.scope for p in doc.pages}
    assert scopes == {0: "consolidated", 1: None, 2: "company"}, scopes


def test_a_company_only_page_is_not_extracted_as_the_group(filing):
    """THE DEFECT. Both pages print "Investment properties"; the Group's is 36,683 and the
    Company's is 647. Tagged the same, they map to one canonical key and the spread adds them —
    which is how a real filing reported total non-current assets of 60,586,316 against a printed
    53,035,061."""
    doc, _ = filing
    pages = _by_page(doc)
    assert pages[0]["Investment properties"][("consolidated", "current")] == 36683
    assert pages[2]["Investment properties"][("standalone", "current")] == 647
    # No figure from the Company's page may be filed as the Group's, and vice versa.
    assert {b for slots in pages[0].values() for b, _ in slots} == {"consolidated"}
    assert {b for slots in pages[2].values() for b, _ in slots} == {"standalone"}


def test_the_page_scope_decision_is_in_the_run_log(filing):
    """A basis decision that moves numbers must be auditable from the log, the way
    ``entity_scope=two_basis_header`` and ``entity_scope=company_only`` already are."""
    _, logs = filing
    assert any("page=2" in m and "entity_scope=" in m for m in logs), [
        m for m in logs if "entity_scope" in m]


def test_the_document_reports_both_bases(filing):
    """What the Workspace reads to decide which basis tabs to offer: a filing carrying a Company
    statement genuinely has two answers, and ``periods.bases_present`` is where that shows up."""
    doc, _ = filing
    assert sorted({ev.basis.value for li in doc.line_items
                   for ev in li.values.values()}) == ["consolidated", "standalone"]
