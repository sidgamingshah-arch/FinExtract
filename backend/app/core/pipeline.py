"""Pipeline orchestrator.

Runs an ordered list of stages, each enriching the document model. A BLOCKER
integrity finding short-circuits the remaining stages (extraction can't proceed on
a corrupt/encrypted document), but everything else runs to completion so partial
results and all findings are available.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import DocumentModel
from app.core.stage import PipelineContext, Stage


@dataclass
class Pipeline:
    stages: list[Stage] = field(default_factory=list)

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        n = len(self.stages)
        for i, stage in enumerate(self.stages):
            ctx.emit_progress(stage.name, round(i / max(n, 1), 3))
            ctx.log(f"stage:{stage.name}:start")
            doc = stage.run(doc, ctx)
            ctx.log(f"stage:{stage.name}:done")

            # Short-circuit only on a hard blocker discovered by the integrity stage.
            if doc.integrity is not None and doc.integrity.has_blockers:
                ctx.log("pipeline:halted:integrity_blocker")
                break
        ctx.emit_progress("done", 1.0)
        return doc


def default_pipeline() -> Pipeline:
    """Assemble the default native-path pipeline.

    Imported lazily to avoid a circular import (stages import core models).
    """
    from app.stages.ingest import IngestStage
    from app.stages.integrity import IntegrityStage
    from app.stages.language import LanguageDetectStage
    from app.stages.classify import ClassifyStage
    from app.stages.extract import ExtractStage
    from app.stages.map_ontology import MapOntologyStage
    from app.stages.normalize import NormalizeStage
    from app.stages.link_notes import LinkNotesStage
    from app.stages.deprec_impairment import DeprecImpairmentStage
    from app.stages.secur_fincl_assets import SecurFinclAssetsStage
    from app.stages.related_party_receivables import RelatedPartyReceivablesStage
    from app.stages.sales_revenues import SalesRevenuesStage
    from app.stages.contingent_liabilities import ContingentLiabilitiesStage
    from app.stages.reconcile import ReconcileStage
    from app.stages.confidence import ConfidenceStage
    from app.stages.structural import StructuralStage
    from app.stages.prune_notes import PruneNotesStage
    from app.stages.residual import ResidualStage
    from app.stages.gap_closing import GapClosingStage
    from app.stages.face_mapping_contract import FaceMappingContractStage
    from app.stages.segment import SegmentStage

    # Table reconstruction is performed inside the extract stage (native pages via the
    # PyMuPDF text layer + shared row_reconstruct; scanned pages via the OCR port), so there
    # is no separate reconstruct stage — extraction consumes the reconstruction directly.
    return Pipeline(stages=[
        IngestStage(),
        IntegrityStage(),
        LanguageDetectStage(),
        ClassifyStage(),
        ExtractStage(),
        MapOntologyStage(),
        # A printed face line that matched no specific concept goes to its own section's
        # residual bucket rather than vanishing from the statement.
        ResidualStage(),
        NormalizeStage(),
        LinkNotesStage(),
        # Deprec & Impairment (Oper Exp)/(COS) are assembled from note-level datasets, never a
        # single printed caption — resolved here, after notes are linked and units normalized, so
        # the reconcile/structural checks below see the computed figure rather than a blank cell.
        DeprecImpairmentStage(),
        # Same reasoning for Secur & Other Fincl Assets (CP)/(LTP): note totals less proven
        # deductions, with the unabsorbed Level 3 fair-value amount carried from CP into LTP.
        SecurFinclAssetsStage(),
        # Due from Related Parties (LTP)/Other Receivables (CP): the highest of three independent
        # related-party measurements, and a gross receivable pool less its own proven deduction.
        RelatedPartyReceivablesStage(),
        # Sales(Revenues): the face reading stands (ordinary alias mapping, above); this only fills
        # a (basis, period) the face left with no value, from the 主营业务/主营业务收入 row of a
        # 营业收入 note — never the note's own combined total.
        SalesRevenuesStage(),
        # Contingent Liabilities: a classified narrative + tables, not a single figure — see
        # services.contingent_liabilities. The quantifiable total also lands on the ordinary
        # notes__contingent_liabilities LineItem, for grid/export consistency with every other
        # Notes-statement leaf.
        ContingentLiabilitiesStage(),
        ReconcileStage(),
        # Only notes cited from the face of the statements are published — after reconcile,
        # which needs every extracted note to check the note->face ties.
        PruneNotesStage(),
        ConfidenceStage(),
        # A subtotal that still does not tie may be missing a line the mapper could not place.
        # Asked BEFORE the structural checks, so a gap the model closes reports as tied rather
        # than as a defect the analyst has to chase down themselves.
        GapClosingStage(),
        # A run cannot succeed by hiding an unresolved face value behind a borrowed concept.
        # Verified non-additive aggregates are the only exception: their mapped components replace
        # them and the aggregate remains as evidence for the arithmetic check.
        FaceMappingContractStage(),
        StructuralStage(),
        # Last, and the position is the point: the eight analyst buckets are four balance-sheet
        # sections plus equity, all printed on one page, so only a row's RESOLVED section can
        # separate them. See stages/segment.py.
        SegmentStage(),
    ])
