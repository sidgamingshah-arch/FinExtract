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
    from app.stages.reconcile import ReconcileStage
    from app.stages.confidence import ConfidenceStage
    from app.stages.structural import StructuralStage
    from app.stages.prune_notes import PruneNotesStage
    from app.stages.residual import ResidualStage
    from app.stages.gap_closing import GapClosingStage
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
        ReconcileStage(),
        # Only notes cited from the face of the statements are published — after reconcile,
        # which needs every extracted note to check the note->face ties.
        PruneNotesStage(),
        ConfidenceStage(),
        # A subtotal that still does not tie may be missing a line the mapper could not place.
        # Asked BEFORE the structural checks, so a gap the model closes reports as tied rather
        # than as a defect the analyst has to chase down themselves.
        GapClosingStage(),
        StructuralStage(),
        # Last, and the position is the point: the eight analyst buckets are four balance-sheet
        # sections plus equity, all printed on one page, so only a row's RESOLVED section can
        # separate them. See stages/segment.py.
        SegmentStage(),
    ])
