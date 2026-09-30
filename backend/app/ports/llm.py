"""LLM provider port.

The default implementation should be an Anthropic Claude adapter using structured
tool-use / JSON mode with a Pydantic-validated schema and temperature 0 for
determinism. Verify the exact model id / params against the ``claude-api`` skill at
implementation time. Kept swappable for a self-hosted model (air-gapped).
"""
from __future__ import annotations

from typing import Protocol, Sequence, TypedDict, runtime_checkable

from pydantic import BaseModel


class LlmMessage(TypedDict):
    role: str
    content: str


class LlmMeta(TypedDict, total=False):
    # THE WHOLE PROMPT, cached part included, on every adapter — so a run's input total means the
    # same thing whichever provider served it. `cached_input_tokens` is the part of it the provider
    # served from its prompt cache (billed at the cached rate); `cache_write_tokens` is the part it
    # stored for later requests (billed at the write rate, where the provider charges one).
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    model: str
    confidence: float


def openai_usage(usage: dict | None) -> dict:
    """`input_tokens` / `output_tokens` / the two cache counts, from a Chat Completions `usage`.

    `prompt_tokens` already INCLUDES the cached tokens there. The cached count is
    `prompt_tokens_details.cached_tokens`; a gateway that also reports what it wrote (OpenRouter's
    `cache_write_tokens`) or relays a Claude-style `usage` is read too, so a figure the provider
    sends is never dropped. Absent fields are 0.
    """
    usage = usage or {}
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or usage.get("cache_read_input_tokens") or 0)
    written = int(details.get("cache_write_tokens") or usage.get("cache_creation_input_tokens") or 0)
    return {"input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "cached_input_tokens": cached, "cache_write_tokens": written}


def anthropic_usage(usage) -> dict:
    """The same four counts from a Messages API `usage` (an object or a dict).

    There `input_tokens` EXCLUDES cache reads and cache writes, so the whole prompt is their sum —
    which is what `LlmMeta.input_tokens` means on every adapter.
    """
    get = (usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d))
    fresh = int(get("input_tokens", 0) or 0)
    cached = int(get("cache_read_input_tokens", 0) or 0)
    written = int(get("cache_creation_input_tokens", 0) or 0)
    return {"input_tokens": fresh + cached + written,
            "output_tokens": int(get("output_tokens", 0) or 0),
            "cached_input_tokens": cached, "cache_write_tokens": written}


@runtime_checkable
class LlmProvider(Protocol):
    id: str

    def complete_structured(
        self,
        *,
        system: str,
        messages: Sequence[LlmMessage],
        response_schema: type[BaseModel],
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> tuple[BaseModel, LlmMeta]: ...
