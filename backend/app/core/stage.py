"""Pipeline stage protocol and context.

A stage is a callable ``run(doc, ctx) -> DocumentModel`` that *enriches* the document
model. Stages collect findings/progress on the context rather than raising for
recoverable problems, so the pipeline can surface all issues and remain re-runnable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.config import Settings, get_settings
from app.core.models import DocumentModel
from app.ports.registry import Registry, registry as default_registry


@dataclass
class PipelineContext:
    settings: Settings = field(default_factory=get_settings)
    registry: Registry = field(default_factory=lambda: default_registry)
    object_store: Any = None
    raw_bytes: bytes | None = None            # original uploaded file
    # Explicit extraction scope: INCLUDED page indices (0-based). None = default (all
    # face/notes pages). Set from the document's persisted page_scope so a user's page
    # selection on the Scope screen actually restricts what gets extracted.
    included_pages: set[int] | None = None
    logs: list[str] = field(default_factory=list)
    progress_cb: Callable[[str, float], None] | None = None
    # Reported from inside a stage: (units done, units total, what a unit is). See `emit_step`.
    step_cb: Callable[[int, int, str], None] | None = None
    # LLM usage accumulated across stages (description-based mapping, …) for the audit log.
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    # …and how much of the input the provider served from its prompt cache / stored for reuse.
    # Part of `llm_input_tokens`, not in addition to it (see `ports.llm.LlmMeta`).
    llm_cached_tokens: int = 0
    llm_cache_write_tokens: int = 0
    llm_calls: int = 0
    # REQUESTS THAT FAILED, counted beside the ones that succeeded — because `llm_calls` alone
    # cannot tell "no provider" from "every request failed", and those are opposite facts.
    #
    # `llm_calls` is incremented only after a reply validates (`stages.line_item_llm`), which is
    # right: a failed request located nothing and must not read as work done. But it left zero
    # meaning two things, and the UI hid the stat at zero, so a run whose every request failed
    # rendered identically to a fully deterministic one — the degraded outcome disguised as the
    # designed one, which is the exact confusion `mapping_strategy_reason` below exists to prevent
    # for the mapper.
    llm_failures: int = 0
    llm_model: str = ""
    # How ontology mapping actually ran, and why. A run with no LLM configured silently falls
    # back to the deterministic ensemble, which is materially weaker — recording it means a
    # degraded run is never mistaken for a full-capability one downstream.
    mapping_strategy: str = ""
    mapping_strategy_reason: str = ""

    def log(self, message: str) -> None:
        self.logs.append(message)

    def emit_progress(self, phase: str, pct: float) -> None:
        if self.progress_cb is not None:
            self.progress_cb(phase, pct)

    def emit_step(self, done: int, total: int, label: str = "") -> None:
        """Report progress from INSIDE a stage — "3 of 12 LLM calls finished".

        WHY THIS EXISTS. `Pipeline.run` emits once before each stage, so everything a reader sees
        moves only at stage boundaries. `map_ontology` is one stage and it is by far the longest:
        it cuts the document into (statement, section) subgroups and makes one batched LLM call per
        subgroup, concurrently. For the whole of that — minutes on a real filing — the stage name,
        the percentage, the stage counter AND the log tail all sit frozen, because the only thing
        that flushes them is the next stage starting. A reader cannot tell a run that is working
        from one that has hung.

        Deliberately a SEPARATE callback rather than a richer `progress_cb`. Every existing caller
        passes a two-argument callable, and widening that signature would break each of them for
        a report that must never be able to fail a run.

        A stage that has no notion of sub-steps simply never calls this, and nothing changes.
        """
        if self.step_cb is not None:
            self.step_cb(done, total, label)


class Stage(Protocol):
    name: str

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel: ...
