"""Contingent Liabilities — writes services.contingent_liabilities's structured output onto
DocumentModel.contingent_liabilities (paragraph + classified/unclassified tables, keyed by
"basis:period"), and the quantifiable total onto the notes__contingent_liabilities LineItem for
grid/export consistency with every other Notes-statement leaf.

Runs alongside the other computed-field stages: after notes are linked and units normalized,
before reconcile.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services.contingent_liabilities import ContingentLiabilitiesResult, compute

NOTES_KEY = "notes__contingent_liabilities"


def _jsonable(value):
    return str(value) if isinstance(value, Decimal) else value


def _to_dict(result: ContingentLiabilitiesResult) -> dict:
    return {
        "summary_paragraph": result.summary_paragraph,
        "classified_summary": [{k: _jsonable(v) for k, v in g.items()}
                               for g in result.classified_summary],
        "unclassified_items": [{k: _jsonable(v) for k, v in it.items()}
                               for it in result.unclassified_items],
        "status": result.status,
        "qa_flags": result.flags,
    }


class ContingentLiabilitiesStage:
    name = "contingent_liabilities"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        keys = {c.get("canonical_key") for st in (template or {}).get("statements", []) or []
                for sec in st.get("sections", []) or [] for c in (sec.get("children") or [])}
        if NOTES_KEY not in keys:
            ctx.log("contingent_liabilities:skipped(template does not declare the field)")
            return doc
        if not doc.notes:
            ctx.log("contingent_liabilities:skipped(no notes extracted)")
            return doc

        # The document's own page text, for the case where the disclosure's PAGE never became a
        # note. A pure-prose page carrying the guarantee totals was classified `face/balance_sheet`
        # on the measured filing, and a note-only search cannot reach a page that is not a note.
        # Guarded and best-effort: this stage must not fail a run because the text layer would not
        # re-read, and `compute` treats an empty list exactly as it treated no argument at all.
        page_texts: list[tuple[int, str]] = []
        try:
            from app.services.derived import document_text
            page_texts = document_text(ctx.raw_bytes or b"", doc.fmt.value)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"contingent_liabilities:page_text_unavailable({type(exc).__name__})")

        results = compute(doc, page_texts=page_texts)
        if not results:
            ctx.log("contingent_liabilities:no contingent-liability notes found")
            return doc
        results = {pk: self._maybe_enhance(result, ctx) for pk, result in results.items()}

        row = next((li for li in doc.line_items if li.canonical_key == NOTES_KEY), None)
        out: dict[str, dict] = {}
        for (basis, period_label), result in results.items():
            out[f"{basis}:{period_label}"] = _to_dict(result)
            if result.total_quantifiable is None:
                continue
            if row is None:
                row = LineItem(source_label=NOTES_KEY, canonical_key=NOTES_KEY,
                               ordinal=max((li.ordinal for li in doc.line_items), default=0) + 1)
                doc.line_items.append(row)
            row.set_value(ExtractedValue(value=result.total_quantifiable,
                                         value_raw=result.total_quantifiable,
                                         basis=Basis(basis), period_label=period_label))
            row.confidence.method = "computed:contingent_liabilities"
            for flag in result.flags:
                if flag not in row.confidence.flags:
                    row.confidence.flags.append(flag)
        doc.contingent_liabilities = out
        ctx.log(f"contingent_liabilities:{len(out)} period(s) computed")
        return doc

    @staticmethod
    def _maybe_enhance(result: ContingentLiabilitiesResult, ctx: PipelineContext) -> ContingentLiabilitiesResult:
        """Rewrite the prose via the configured LLM, if any — never the classification or a
        total, and never a hard failure: a stub provider, a disabled setting, or a failed/
        malformed call all leave the deterministic paragraph exactly as computed."""
        from app.config import get_settings
        from app.ports.registry import registry
        from app.services.contingent_liabilities import enhance_with_llm

        settings = get_settings()
        if not settings.extraction.llm_contingent_liabilities:
            return result
        provider_id = settings.llm.provider
        if provider_id == "stub":
            return result
        try:
            provider = registry.get("llm", provider_id)
        except KeyError:
            ctx.log(f"contingent_liabilities:unknown provider {provider_id!r}")
            return result
        try:
            enhanced, _meta = enhance_with_llm(provider, result,
                                               max_tokens=settings.llm.max_tokens)
        except Exception as exc:  # noqa: BLE001 - a narrative failure must not fail the run
            ctx.log(f"contingent_liabilities:llm_narrative_failed:{type(exc).__name__}")
            return result
        ctx.log(f"contingent_liabilities:narrative rewritten by {provider_id}")
        return enhanced
