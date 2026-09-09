"""Add up the rows the model declared components of one line item.

Runs after ``normalize`` and before ``reconcile``: the components have to be in one scale and one
sign convention before they are added — adding a thousands figure to a units one produces a total
the filing never states — and reconcile has to see the ASSEMBLED figure, since a component set that
does not tie to a printed subtotal is exactly what it exists to report.

Nothing here enumerates a caption. The stage asks the document which rows the mapper marked, and
`services.assemble_components` adds those up; which rows those are was decided by the
configuration and the model, not by a list in this file. That is the whole difference between this
and the five derivation stages it replaces.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext
from app.services.assemble_components import assemble


class AssembleComponentsStage:
    name = "assemble_components"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        if not doc.line_items:
            ctx.log("assemble_components:skipped(no line items)")
            return doc
        filled = assemble(doc, log=ctx.log)
        # SAYS SO EITHER WAY. Zero is the ordinary answer on a filing that prints every line
        # directly, and it must not be indistinguishable from a stage that failed to run — the
        # defect class this codebase has been bitten by repeatedly.
        ctx.log(f"assemble_components:{filled} concept slot(s) assembled from declared components")
        return doc
