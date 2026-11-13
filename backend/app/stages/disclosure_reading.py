"""Disclosure-reading stage — the Disclosures screen's topics read by the model, one prompt per
topic, every point checked against the page it cites (`services.disclosure_reading`).

Writes `DocumentModel.disclosure_readings` and nothing else: no line item, no figure, no check. The
worker folds the readings onto the disclosure entries (`disclosure_reading.attach_readings`), which
is the one list the Disclosures screen, the exports, /analysis and the credit view read.

Off with `extraction.llm_disclosures`; with no provider, or with the stub, it makes no call and the
screen shows the presence scan as before.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext


class DisclosureReadingStage:
    name = "disclosure_reading"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        from app.ports.registry import registry
        from app.services import disclosure_reading

        settings = ctx.settings
        if not getattr(settings.extraction, "llm_disclosures", True):
            ctx.log("disclosure_reading:disabled")
            return doc
        provider_id = getattr(settings.llm, "provider", "stub")
        if provider_id == "stub":
            ctx.log("disclosure_reading:skipped(stub llm provider configured)")
            return doc
        # The document's own page text — the same text the presence scan reads, so a passage the
        # model is shown is one the scan could have found. Best-effort: a text layer that will not
        # re-read costs the run its readings, never the run.
        try:
            from app.services.derived import document_text
            pages = document_text(ctx.raw_bytes or b"", doc.fmt.value)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"disclosure_reading:skipped(page text unavailable: {type(exc).__name__})")
            return doc
        if not pages:
            ctx.log("disclosure_reading:skipped(no page text)")
            return doc
        try:
            provider = registry.get("llm", provider_id)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"disclosure_reading:skipped(llm provider unavailable: {type(exc).__name__})")
            return doc

        try:
            from app.services.derived import detect_entity_name
            entity = detect_entity_name(pages)
        except Exception:  # noqa: BLE001 — the company's name is context, not a requirement
            entity = None
        folios = {p.index: p.printed_page for p in doc.pages if getattr(p, "printed_page", None)}
        kinds = {p.index: getattr(p.kind, "value", p.kind) for p in doc.pages}
        results = disclosure_reading.read_disclosures(
            provider, pages, folio_of=folios.get if folios else None, kind_of=kinds.get,
            entity=entity)
        if not results:
            ctx.log("disclosure_reading:no passage on any topic")
            return doc
        readings: dict[str, dict] = {}
        for key, r in results.items():
            if r.get("error") and r.get("reading") is None and not r.get("usage"):
                ctx.llm_failures += 1
                ctx.log(f"disclosure_reading:{key}:FAILED {r['error']} — the screen shows the scan")
                continue
            ctx.llm_calls += 1
            used = r.get("usage") or {}
            ctx.llm_input_tokens += used.get("input_tokens", 0)
            ctx.llm_output_tokens += used.get("output_tokens", 0)
            ctx.llm_cached_tokens += used.get("cached_input_tokens", 0)
            ctx.llm_cache_write_tokens += used.get("cache_write_tokens", 0)
            refused = {k: v for k, v in (r.get("refused") or {}).items() if v and k != "error"}
            tail = "".join(f" refused_{k}={v}" for k, v in refused.items())
            reading = r.get("reading")
            if reading is None:
                ctx.log(f"disclosure_reading:{key}:WITHHELD {r.get('error') or 'reply refused'} "
                        f"passages={r['passages']}{tail} — the screen shows the scan")
                continue
            readings[key] = r
            ctx.log(f"disclosure_reading:{key}:{reading['status']} points={len(reading['points'])} "
                    f"passages={r['passages']} input={used.get('input_tokens', 0)} "
                    f"output={used.get('output_tokens', 0)}{tail}")
        doc.disclosure_readings = readings
        return doc
