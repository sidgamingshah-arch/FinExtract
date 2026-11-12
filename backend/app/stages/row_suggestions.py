"""Row-suggestions stage — one model call proposes a template line for each printed row that reached
none, for the reviewer to apply.

Runs after every stage that can still place a row (mapping, residual routing, the line-item and
note-sourced routes, gap closing, the face-mapping contract) and after the structural checks, so it
sees exactly the rows that end the run raising an ``unmapped`` card — read through the card's own
test (`api.routes.documents._unmapped_card_positions`), not a second spelling of it. It writes a
suggestion onto each such row (`LineItem.suggestion`) and changes nothing else: see
`services.row_suggestions`.

Off with `extraction.llm_row_suggestions`; with no provider, or with the stub, it does nothing.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext


class RowSuggestionsStage:
    name = "row_suggestions"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        from app.ports.registry import registry
        from app.services import row_suggestions

        settings = ctx.settings
        if not getattr(settings.extraction, "llm_row_suggestions", True):
            ctx.log("row_suggestions:disabled")
            return doc
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        if not template or not doc.line_items:
            ctx.log("row_suggestions:skipped(no template or no line items)")
            return doc
        provider_id = getattr(settings.llm, "provider", "stub")
        if provider_id == "stub":
            ctx.log("row_suggestions:skipped(stub llm provider configured)")
            return doc

        # THE CARD'S OWN ROWS. The same serialized view the review queue is built from, and the
        # same test it raises an `unmapped` card with — so a suggestion is made for exactly the rows
        # a reviewer is shown, and for nothing else.
        from app.api.routes.documents import _remap_targets, _unmapped_card_positions
        from app.api.routes.extractions import _serialize_rows
        rows = _serialize_rows(doc)
        # ONLY A ROW NOTHING RECOGNISED — no concept, or the engine's placeholder for an unplaced
        # face row. A card is also raised for a row the configuration DID place, on a concept the
        # template declares nowhere: the rulebook and the template disagree about that line, which
        # is a configuration fix, not a row to place. (A row on an internal PART raises no card at
        # all — `documents._placed_on_a_part` — so a printed line is never suggested for one, which
        # would invite a reviewer to undo the split the configuration made and count its figure
        # twice.)
        from app.stages.face_mapping_contract import is_unclassified_face_key
        positions = [p for p in _unmapped_card_positions(rows, template)
                     if not rows[p].get("canonical_key")
                     or is_unclassified_face_key(rows[p].get("canonical_key"))]
        if not positions:
            ctx.log("row_suggestions:no unmapped row")
            return doc
        try:
            provider = registry.get("llm", provider_id)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"row_suggestions:skipped(llm provider unavailable: {type(exc).__name__})")
            return doc

        # What the serialized row does not carry and the question needs: the statement the row is
        # printed on (its page's), the section banner and the heading printed above it.
        by_id = {str(li.id): li for li in doc.line_items}
        statement_of_page = {p.index: p.statement for p in doc.pages if p.statement}
        views: list[dict] = []
        for r in rows:
            li = by_id.get(str(r.get("id") or ""))
            pages = ([ev.provenance.page_index for ev in li.values.values()
                      if ev.provenance is not None] if li is not None else [])
            views.append({**r,
                          "statement": next((statement_of_page[p] for p in pages
                                             if p in statement_of_page), ""),
                          "section_hint": (getattr(li, "section_hint", "") or "") if li else "",
                          "group_hint": (getattr(li, "group_hint", "") or "") if li else ""})
        definitions = {i.key: str(getattr(i, "definition", "") or "")
                       for i in (getattr(getattr(ctx, "line_items", None), "items", None) or ())}
        targets = _remap_targets(template, "en")
        suggestions, summary = row_suggestions.suggest(provider, views, positions, targets,
                                                       definitions)
        if summary.get("skipped"):
            ctx.log(f"row_suggestions:skipped({summary['skipped']})")
            return doc
        if summary.get("error"):
            ctx.llm_failures += 1
            ctx.log(f"row_suggestions:FAILED {summary['error']} — the unmapped rows keep their "
                    f"cards without a suggestion")
            return doc
        ctx.llm_calls += 1
        used = summary.get("usage") or {}
        ctx.llm_input_tokens += used.get("input_tokens", 0)
        ctx.llm_output_tokens += used.get("output_tokens", 0)
        ctx.llm_cached_tokens += used.get("cached_input_tokens", 0)
        ctx.llm_cache_write_tokens += used.get("cache_write_tokens", 0)
        written = 0
        for pos, suggestion in suggestions.items():
            li = by_id.get(str(rows[pos].get("id") or ""))
            if li is None:
                continue
            li.suggestion = suggestion
            written += 1
        refused = {k: summary.get(k, 0) for k in ("unknown_rows", "duplicate_rows",
                                                  "unknown_keys") if summary.get(k)}
        ctx.log(f"row_suggestions:rows={summary['rows']} suggested={written} "
                f"input={used.get('input_tokens', 0)} output={used.get('output_tokens', 0)}"
                + "".join(f" refused_{k}={v}" for k, v in refused.items()))
        return doc
