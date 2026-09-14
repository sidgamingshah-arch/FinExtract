"""Shared-figure stage — one printed figure on two line items, and the model breaks the tie.

Runs after every route that writes a figure (normalize, the line-item model call, the note-sourced
fills, component assembly) so it sees the finished mapping, and BEFORE reconcile — a tie checked
against a figure counted twice would report a tie that is not true.

The decision is the provider's; this stage only finds the contests and applies the answer. With no
provider configured, or the setting off, the collisions are recorded and flagged and every figure
stays exactly where it was — see services.shared_figures.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext


def _config_maps(ctx) -> tuple[dict[str, str], dict[str, str]]:
    """`(parent_of, definition_of)` from the run's line-item configuration.

    `parent_of` is what tells a real contest from a parent carrying its own child's figure, so a
    run without configuration reports nothing rather than reporting every rollup as a collision.
    """
    parent_of: dict[str, str] = {}
    definition_of: dict[str, str] = {}
    cfg = getattr(ctx, "line_items", None) or getattr(ctx, "line_item_set", None)
    for item in (getattr(cfg, "items", None) or ()):
        key = str(getattr(item, "key", "") or "")
        if not key:
            continue
        parent_of[key] = str(getattr(item, "parent", "") or "")
        definition_of[key] = str(getattr(item, "definition", "") or "")
    return parent_of, definition_of


class SharedFiguresStage:
    name = "shared_figures"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        from app.config import get_settings
        from app.ports.registry import registry
        from app.services.shared_figures import find_collisions, resolve

        settings = get_settings()
        parent_of, definition_of = _config_maps(ctx)
        if not parent_of:
            # NO CONFIGURATION, NO ANCESTRY. Without it a parent carrying its child's figure looks
            # exactly like two lines competing, and the stage would ask the model to break ties
            # that the declared arithmetic already settled.
            ctx.log("shared_figures:skipped(no line-item configuration on the run)")
            return doc

        collisions = find_collisions(doc, parent_of)
        if not collisions:
            ctx.log("shared_figures:no figure claimed by more than one line")
            return doc

        # RECORDED BEFORE ANYTHING IS DECIDED, so the audit shows what was found even when no
        # provider answers and nothing is changed.
        doc.shared_figures = [
            {"basis": c.basis, "period": c.period, "amount": str(c.amount),
             "page_index": c.page_index, "caption": c.caption, "note": c.note_number,
             "claimed_by": c.keys, "keep": "", "rationale": "", "confidence": 0.0}
            for c in collisions
        ]

        if not settings.extraction.llm_shared_figure_tiebreak:
            for c in collisions:
                for key, _label, _d, idx, _v in c.claimants:
                    _flag(doc, idx, f"shared_figure_unresolved:{'|'.join(c.keys)}")
            ctx.log(f"shared_figures:{len(collisions)} contest(s) flagged, tie-break disabled")
            return doc

        provider_id = settings.llm.provider
        if provider_id == "stub":
            for c in collisions:
                for key, _label, _d, idx, _v in c.claimants:
                    _flag(doc, idx, f"shared_figure_unresolved:{'|'.join(c.keys)}")
            ctx.log(f"shared_figures:{len(collisions)} contest(s) left for review "
                    f"(no LLM provider configured)")
            return doc
        try:
            provider = registry.get("llm", provider_id)
        except KeyError:
            ctx.log(f"shared_figures:unknown provider {provider_id!r}")
            return doc

        kept_both = removed = 0
        for n, c in enumerate(collisions):
            keep, rationale, conf = resolve(
                provider, c, definition_of,
                locale=getattr(ctx, "locale", "en") or "en",
                max_tokens=min(400, settings.llm.max_tokens))
            doc.shared_figures[n].update({"keep": keep, "rationale": rationale,
                                          "confidence": conf})
            if keep == "both":
                kept_both += 1
                for _key, _label, _d, idx, _v in c.claimants:
                    _flag(doc, idx, f"shared_figure_kept_on_both:{'|'.join(c.keys)}")
                ctx.log(f"shared_figures:{c.amount} on {'|'.join(c.keys)} kept on both "
                        f"({provider_id}: {rationale[:80]})")
                continue
            # ONE LINE KEEPS IT. The value is removed from the others rather than zeroed: a zero
            # asserts the filing disclosed nothing, and what happened here is that this line never
            # held the figure in the first place.
            for key, _label, _d, idx, vkey in c.claimants:
                if key == keep:
                    _flag(doc, idx, f"shared_figure_kept_here:{'|'.join(c.keys)}")
                    continue
                li = doc.line_items[idx] if 0 <= idx < len(doc.line_items) else None
                if li is None:
                    continue
                if vkey in (li.values or {}):
                    del li.values[vkey]
                    removed += 1
                _flag(doc, idx, f"shared_figure_removed:kept_on_{keep}")
            ctx.log(f"shared_figures:{c.amount} kept on {keep} only, removed from "
                    f"{len(c.claimants) - 1} other line(s) ({provider_id}: {rationale[:80]})")

        ctx.log(f"shared_figures:{len(collisions)} contest(s) — {kept_both} kept on both, "
                f"{removed} figure(s) removed, by {provider_id}")
        return doc


def _flag(doc: DocumentModel, idx: int, flag: str) -> None:
    """Record the verdict on the row, so a reader of the figure can see it was contested."""
    if not (0 <= idx < len(doc.line_items)):
        return
    li = doc.line_items[idx]
    if flag not in li.confidence.flags:
        li.confidence.flags.append(flag)
