"""Contingent Liabilities — a DISCLOSURE stage. It writes narrative, and only narrative.

The one output is `DocumentModel.contingent_liabilities`: per "basis:period", a summary paragraph
plus the classified and unclassified tables that services.contingent_liabilities builds, for the
Disclosures screen and the exports to render as prose for a human. NOTHING is written onto any
LineItem — this stage sets no value on any row, and does not participate in the grid or in
reconcile arithmetic. It is the sole survivor of the computed-field stages precisely because its
output is a paragraph rather than a number.

REMOVED — the publish onto `notes__contingent_liabilities`. This stage used to also set a single
Decimal on that LineItem (with confidence.method "computed:contingent_liabilities" and the
service's qa_flags copied onto the row), taken from a derived total the service assembled out of
172 hand-enumerated entries: 26 note titles, 23 amount labels, 14 non-exposure phrases, 72
classifier terms and 19 matter types. That was a derivation and it is gone, together with the
services.computed_paths precedence gate that used to arbitrate between it and the rulebook's own
reading. THE FIGURE FOR `notes__contingent_liabilities` MUST NOW COME FROM CONFIGURATION — a
rulebook alias binding the 或有负债 / contingent-liability caption. Until the config describes it
that cell is blank, deliberately; do not reinstate a publish here.

Runs after notes are linked and units normalized, before reconcile.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext
from app.services.contingent_liabilities import ContingentLiabilitiesResult, compute

# Kept SOLELY as the template-declaration guard below: it asks "does this template want the
# concept at all", which decides whether the narrative is worth building. Nothing is written to a
# line item under this key any more.
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
        # THE ITEM-BY-ITEM TABLE AND ITS TOTALS. Stored per period like everything else here, so
        # `disclosure_explanation` can put them on the disclosure entry every reader already sees.
        "detail_items": [{k: _jsonable(v) for k, v in r.items()}
                         for r in result.detail_items],
        "detail_totals": [{k: _jsonable(v) for k, v in t.items()}
                          for t in result.detail_totals],
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
        # No precedence gate here any more. services.computed_paths existed to arbitrate between a
        # computed figure and the rulebook's own reading of the same caption; this stage publishes
        # no figure, so there is nothing to arbitrate and nothing to switch off. The narrative
        # always runs when the template declares the field.

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

        # The narrative, per period, and nothing else. A `notes__contingent_liabilities` LineItem
        # used to be looked up (or created) here and given the service's derived total, its
        # confidence.method set to "computed:contingent_liabilities" and its qa_flags copied over.
        # That derivation — a Decimal out of 172 hand-enumerated entries (26 note titles, 23 amount
        # labels, 14 non-exposure phrases, 72 classifier terms, 19 matter types) — is removed, so
        # this stage touches no line item at all and that figure must come from configuration.
        out: dict[str, dict] = {}
        for (basis, period_label), result in results.items():
            out[f"{basis}:{period_label}"] = _to_dict(result)
        doc.contingent_liabilities = out
        ctx.log(f"contingent_liabilities:{len(out)} period(s) computed")
        return doc

    @staticmethod
    def _maybe_enhance(result: ContingentLiabilitiesResult, ctx: PipelineContext) -> ContingentLiabilitiesResult:
        """Rewrite the prose via the configured LLM, if any — never the classification or any
        disclosed amount, and never a hard failure: a stub provider, a disabled setting, or a
        failed/malformed call all leave the deterministic paragraph exactly as computed."""
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
