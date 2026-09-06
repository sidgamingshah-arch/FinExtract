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
