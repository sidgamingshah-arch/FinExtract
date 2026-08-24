"""Gap-closing stage — ask the model where a leftover line belongs when a subtotal won't tie.

Runs after mapping and residual routing (so it only sees lines those two genuinely could not
place) and before the structural checks (so a subtotal the routing fixes reports as tied rather
than as a defect the analyst has to chase).

The decision is the provider's; the arithmetic only decides what it is allowed to choose from.
With no provider configured the stage does nothing at all — see services.gap_closing.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext


def _rows_view(doc: DocumentModel) -> list[dict]:
    """The line items in the same dict shape the API serves, so the gap logic has ONE input
    format whether it is called from the pipeline or from a stored run."""
    from app.api.routes.extractions import _serialize_rows

    return _serialize_rows(doc)


class GapClosingStage:
    name = "gap_closing"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        from app.config import get_settings
        from app.ports.registry import registry
        from app.services.gap_closing import find_gaps, leftovers, resolve_all

        settings = get_settings()
        if not settings.extraction.llm_gap_routing:
            ctx.log("gap_closing:disabled")
            return doc
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        if not template or not doc.line_items:
            ctx.log("gap_closing:skipped(no template or no line items)")
            return doc

        rows = _rows_view(doc)
        # Nothing to reconcile, or nothing to reconcile it WITH: skip before paying for a call.
        gaps = [g for b in ("consolidated", "standalone") for g in find_gaps(rows, template, b)]
        if not gaps:
            ctx.log("gap_closing:no gaps")
            return doc
        if not any(leftovers(rows, template, b) for b in ("consolidated", "standalone")):
            ctx.log(f"gap_closing:{len(gaps)} gap(s), no unplaced lines to offer")
            return doc

        provider_id = settings.llm.provider
        if provider_id == "stub":
            # An honest no-op: the gaps stay in the review queue rather than being closed by
            # arithmetic coincidence. Whether a caption belongs in a section is a judgement.
            ctx.log(f"gap_closing:{len(gaps)} gap(s) left for review (no LLM provider configured)")
            return doc
        try:
            provider = registry.get("llm", provider_id)
        except KeyError:
            ctx.log(f"gap_closing:unknown provider {provider_id!r}")
            return doc

        routings = resolve_all(provider, rows, template,
                               locale=getattr(ctx, "locale", "en") or "en",
                               max_tokens=settings.llm.max_tokens)
        if not routings:
            ctx.log(f"gap_closing:{len(gaps)} gap(s), none confirmed by {provider_id}")
            return doc

        # Apply to the DOCUMENT MODEL, so every later stage (structural checks, confidence) and
        # every consumer of the run sees the line where the model placed it.
        # The indices are positions in `rows`, which _serialize_rows built by walking
        # doc.line_items in order — so index straight into that same list, not a re-sorted copy.
        moved = 0
        for routing in routings:
            for idx in routing.get("moved") or []:
                if not (0 <= idx < len(doc.line_items)):
                    continue
                li = doc.line_items[idx]
                li.canonical_key = routing["others_key"]
                # ``confidence.method`` — a LineItem has no ``mapping_method`` field, and assigning
                # one raised ValueError on a strict pydantic model, so this stage crashed the whole
                # run every time the provider CONFIRMED a routing. It never failed in testing
                # because the confirmed path was the one nothing exercised: the assertions for "the
                # routed line records why it moved" and "the subtotal ties" were made against a
                # second implementation that moved the line inside a list of row DICTS, where any
                # key assignment succeeds, and that nothing in the pipeline called. Both now run
                # through this stage, and that twin is deleted.
                #
                # ``mapping_method`` is the name the API serves this under
                # (``extractions._serialize_rows`` reads it off ``li.confidence.method``), which is
                # what made the wrong spelling look right.
                li.confidence.method = "llm_gap_routing"
                moved += 1
        doc.gap_routings = routings                     # cached on the run for the UI/audit
        ctx.log(f"gap_closing:{len(routings)} gap(s) closed, {moved} line(s) routed "
                f"by {provider_id}")
        return doc
