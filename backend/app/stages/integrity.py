"""Document integrity stage.

Collects **all** findings (never fails fast) into an ``IntegrityReport`` so the UI
can present every issue at once before the user commits to extraction. BLOCKER
findings (corruption / encryption) halt the pipeline; WARNING/INFO annotate.
"""
from __future__ import annotations

from app.core.models import (
    DocFormat,
    DocumentModel,
    IntegrityFinding,
    IntegrityReport,
    PageSourceKind,
    Severity,
)
from app.core.stage import PipelineContext


class IntegrityStage:
    name = "integrity"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        report = IntegrityReport(page_count=len(doc.pages))
        data = ctx.raw_bytes

        if doc.fmt == DocFormat.PDF and data:
            self._check_pdf(report, data, ctx)
        elif doc.fmt in (DocFormat.XLSX,) and data:
            self._check_excel(report, data, ctx)
        elif doc.fmt == DocFormat.UNKNOWN:
            report.add(IntegrityFinding(
                check_id="UNKNOWN_FORMAT", severity=Severity.BLOCKER,
                message="Unrecognised file format; cannot extract.",
            ))

        self._check_pages(report, doc)

        if doc.pages:
            scanned = sum(1 for p in doc.pages if p.source_kind == PageSourceKind.SCANNED)
            report.scanned_page_ratio = round(scanned / len(doc.pages), 3)
            report.has_text_layer = scanned < len(doc.pages)
            if scanned:
                report.add(IntegrityFinding(
                    check_id="MIXED_SCAN", severity=Severity.WARNING,
                    message=f"{scanned} of {len(doc.pages)} page(s) are image-only and require OCR.",
                    detail={"scanned_pages": scanned},
                ))

        doc.integrity = report
        ctx.log(f"integrity:findings={len(report.findings)} blockers={report.has_blockers}")
        return doc

    def _check_pdf(self, report: IntegrityReport, data: bytes, ctx: PipelineContext) -> None:
        try:
            import fitz
        except ImportError:
            return
        try:
            pdf = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            report.is_corrupt = True
            report.add(IntegrityFinding(
                check_id="CORRUPT", severity=Severity.BLOCKER,
                message=f"PDF could not be opened: {exc}",
            ))
            return

        if pdf.needs_pass:
            report.is_encrypted = True
            report.is_password_protected = True
            report.add(IntegrityFinding(
                check_id="PASSWORD_PROTECTED", severity=Severity.BLOCKER,
                message="PDF is password-protected; supply a password to extract.",
            ))

        # Rotated / inconsistent-dimension pages.
        dims = set()
        for i, page in enumerate(pdf):
            if page.rotation % 360 != 0:
                report.add(IntegrityFinding(
                    check_id="ROTATED_PAGE", severity=Severity.WARNING,
                    # NO PAGE NUMBER IN THE PROSE. It was the 0-BASED index while the same
                    # finding's `pages` label is 1-based, so one finding named two different pages
                    # in one row. The label is the single place a page is named, and it is the only
                    # one that knows whether the filing printed a folio.
                    message=f"This page is rotated {page.rotation}°.",
                    page_index=i, detail={"rotation": page.rotation},
                ))
            dims.add((round(page.rect.width), round(page.rect.height)))
        if len(dims) > 1:
            report.add(IntegrityFinding(
                check_id="INCONSISTENT_DIMENSIONS", severity=Severity.INFO,
                message=f"Pages have {len(dims)} distinct page sizes.",
                detail={"sizes": sorted(f"{w}x{h}" for w, h in dims)},
            ))
        pdf.close()

    def _check_excel(self, report: IntegrityReport, data: bytes, ctx: PipelineContext) -> None:
        try:
            import io

            import openpyxl
        except ImportError:
            return
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        except Exception as exc:  # noqa: BLE001
            report.is_corrupt = True
            report.add(IntegrityFinding(
                check_id="CORRUPT", severity=Severity.BLOCKER,
                message=f"Workbook could not be opened: {exc}",
            ))
            return
        for name in wb.sheetnames:
            ws = wb[name]
            if getattr(ws, "sheet_state", "visible") != "visible":
                report.add(IntegrityFinding(
                    check_id="HIDDEN_SHEET", severity=Severity.WARNING,
                    message=f"Worksheet {name!r} is hidden.",
                    detail={"sheet": name},
                ))
        wb.close()

    def _check_pages(self, report: IntegrityReport, doc: DocumentModel) -> None:
        if not doc.pages:
            report.add(IntegrityFinding(
                check_id="NO_PAGES", severity=Severity.BLOCKER,
                message="No pages/sheets could be read from the document.",
            ))
            return
        for p in doc.pages:
            if (p.source_kind == PageSourceKind.NATIVE
                    and p.text_char_count == 0
                    and p.image_area_coverage < 0.05):
                report.add(IntegrityFinding(
                    check_id="BLANK_PAGE", severity=Severity.INFO,
                    message="This page appears blank.",
                    page_index=p.index,
                ))
