"""Shared helpers for structured (JSON-schema-constrained) LLM adapters.

Both the Anthropic and OpenAI-compatible adapters obtain structured output the same
model-agnostic way: embed the response model's JSON Schema in the system prompt and
validate the returned text with Pydantic. These helpers keep that logic in one place.
"""
from __future__ import annotations

import json

from pydantic import BaseModel


class LlmConfigError(RuntimeError):
    """Raised when an LLM adapter is selected but cannot be used (missing key/SDK)."""


def schema_instruction(response_schema: type[BaseModel]) -> str:
    schema = json.dumps(response_schema.model_json_schema(), indent=2)
    return (
        "Respond with a single JSON object and NOTHING else — no prose, no code "
        "fences, no explanation before or after. The object MUST validate against "
        "this JSON Schema:\n\n" + schema
    )


# A PROMPT-CACHE BREAKPOINT in the Messages API's own spelling, which Bedrock's Anthropic body uses
# too. `ephemeral` is the only type; its default lifetime (five minutes, refreshed on every hit)
# outlasts the gap between the requests of one run, which are sent back to back.
CACHE_CONTROL = {"type": "ephemeral"}


def messages_body(system: str, messages, *, cache: bool) -> tuple[object, list[dict]]:
    """`(system, messages)` for an Anthropic Messages body, with its prompt-cache breakpoints.

    Without `cache` the system is the string and every message `{role, content}`, the body these
    adapters always sent. With it, the system text becomes one text block that ends in a
    breakpoint, and so does every message marked `cache` (`ports.llm.LlmMessage`). The words are
    unchanged and so is their order: a block holds the same text the string did, so what the model
    reads is the same either way.

    THE SYSTEM ALWAYS ENDS IN ONE, because it is the prefix every request of a run shares: the
    contract, the authored guidance and the reply schema are the same bytes on every figure
    request. The whole report is the second, on the requests that carry it. A prefix shorter than
    the model's minimum is not cached, which costs nothing and is not an error.
    """
    def block(text: str) -> list[dict]:
        return [{"type": "text", "text": text, "cache_control": dict(CACHE_CONTROL)}]

    body_messages = [{"role": m["role"],
                      "content": block(m["content"]) if cache and m.get("cache") else m["content"]}
                     for m in messages]
    return (block(system) if cache else system), body_messages


def strip_fences(text: str) -> str:
    """Tolerate a model that wraps JSON in ```json fences despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def extract_json(text: str) -> str:
    """Return a clean JSON string from possibly-chatty model output.

    Strict parsing first (the well-behaved case); on failure, isolate the first balanced
    JSON object/array embedded in surrounding prose — some free/reasoning models prepend
    their thinking or append a note despite the schema instruction to emit JSON only. Uses
    ``raw_decode`` so trailing text after the object is ignored. Falls back to the fenced
    text unchanged when nothing parses, so the caller still surfaces the original error.
    """
    t = strip_fences(text)
    try:
        json.loads(t)
        return t
    except Exception:  # noqa: BLE001 — chatty output; fall through to isolation
        pass
    start = next((i for i, ch in enumerate(t) if ch in "{["), None)
    if start is None:
        return t
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[start:])
        return json.dumps(obj)
    except Exception:  # noqa: BLE001
        return t
