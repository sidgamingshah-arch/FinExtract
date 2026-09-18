"""Row/value extraction stage.

Reconstructs tables and turns their rows into ``LineItem``s in one step, per source:

* Excel — values + exact cell provenance read straight from the workbook.
* Native PDF — the PyMuPDF text layer feeds the shared ``row_reconstruct`` logic
  (locale-aware value parsing via ``services.numbers``, two-level
  Consolidated/Standalone header detection, note-ref capture, bbox provenance).
* Scanned PDF / image — the OCR port produces words that feed the same reconstruction.

Notes pages route to note-detail tables; every other in-scope page yields face line items.

Every source is read by the rulebook the RUN was pinned to (:func:`reconstruction_rules`), which
is what this stage threads into reconstruction.
"""
from __future__ import annotations

from app.core.models import DocumentModel
from app.core.models.enums import DocFormat
from app.core.stage import PipelineContext
from app.schemas.ontology import Normalisation, ScopeSelection


def reconstruction_rules(ctx: PipelineContext) -> tuple[ScopeSelection | None,
                                                        Normalisation | None]:
    """The ``scope_selection`` / ``normalisation`` blocks of the rulebook THIS RUN was pinned to.

    Those two blocks decide which printed COLUMN each figure is taken from — which column is the
    Group's, which one is the current period, what scale the numbers are in — so they have to come
    from the rulebook the run is labelled with. Reconstruction used to read them from the rulebook
    shipped as the one in force instead (``row_reconstruct.in_force_rules``), which meant a run
    pinned to rulebook A had its pages read by rulebook B's rules: the pin decided how the figures
    were MAPPED and not how they were READ, and pinning an older rulebook to reproduce an earlier
    spread silently produced neither rulebook's answer.

    ``(None, None)`` — a run started with no ontology at all — keeps the shipped default, which is
    what such a run has always been read with.
    """
    ontology = getattr(ctx, "ontology", None)
    if ontology is None:
        return None, None
    return getattr(ontology, "scope_selection", None), getattr(ontology, "normalisation", None)


def _transpose_equity_matrix(doc: DocumentModel, ctx: PipelineContext) -> None:
    """Turn the statement of changes in equity round, once every page has been read.

    HERE AND NOT IN RECONSTRUCTION, because reconstruction reads one page and the answer needs the
    document: a filing printing one page per year has its PRIOR year's closing balance as the
    latest balance on the first of them. See :mod:`app.services.equity_matrix`.

    THIS COMPUTES NO FIGURE. It relabels balances already extracted — the pipeline's note on
    `NoteSourcedStage` ("do not reinstate a stage here to fill it") is about stages that DERIVE a
    published figure out of enumerated note titles and captions, and that is not what this is: no
    note is read, no arithmetic is performed, and every value it appends is a cell of a printed
    balance row carrying that cell's own provenance.
    """
    from app.services.equity_matrix import transpose_closing_balances

    try:
        transpose_closing_balances(doc, log=ctx.log)
    except Exception as exc:                      # a layout nobody anticipated must not lose the run
        ctx.log(f"extract:equity_matrix_transpose_failed:{exc}")


class ExtractStage:
    name = "extract"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        # The run's own rulebook governs how its pages are read, not whichever rulebook is
        # currently shipped as the one in force; logged so a run's reading rules are auditable
        # from the log alone.
        scope, normalisation = reconstruction_rules(ctx)
        ctx.log("extract:reading_rules="
                + ("run_rulebook" if (scope is not None or normalisation is not None)
                   else "shipped_default"))

        # Excel: read values + exact cell provenance straight from the workbook (no OCR/LLM).
        if doc.fmt in (DocFormat.XLSX, DocFormat.XLS) and ctx.raw_bytes:
            from app.services.excel_extract import extract_workbook

            try:
                items = extract_workbook(ctx.raw_bytes, document_id=doc.content_hash,
                                         scope=scope, normalisation=normalisation)
            except Exception as exc:  # openpyxl missing / unreadable → surfaced, not fatal
                ctx.log(f"extract:xlsx_failed:{exc}")
                return doc
            doc.line_items.extend(items)
            ctx.log(f"extract:xlsx_line_items={len(items)}")
            return doc

        # PDF: native pages via the PyMuPDF text layer, scanned pages via the OCR port —
        # both converge on shared word→line-item reconstruction with bbox provenance.
        if doc.fmt == DocFormat.PDF and ctx.raw_bytes:
            from app.services.pdf_extract import extract_pdf

            extract_pdf(ctx.raw_bytes, doc, ctx, scope=scope, normalisation=normalisation)
            _transpose_equity_matrix(doc, ctx)
            return doc

        # Standalone image (scanned page as PNG/JPG/TIFF): OCR the bytes directly.
        if doc.fmt == DocFormat.IMAGE and ctx.raw_bytes:
            from app.services.pdf_extract import extract_image

            extract_image(ctx.raw_bytes, doc, ctx, scope=scope, normalisation=normalisation)
            _transpose_equity_matrix(doc, ctx)
            return doc

        ctx.log("extract:no_source_bytes")
        return doc
