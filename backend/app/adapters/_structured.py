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

# WHAT THAT CACHE COSTS, as multiples of the model's ordinary input price: `(write, read)`. Writing
# an entry with the five-minute lifetime costs 1.25x; reading it back costs 0.1x on most Claude
# models and less on the newest (0.05x, 0.025x). The larger figure is used, so a decision priced
# with it never expects a saving the provider does not give — on a cheaper-reading model it shares
# a little less than it could, and loses nothing. Read by `stages.line_item_llm` to decide which
# notes are worth sending once, cached, rather than in every request that needs them.
CACHE_PRICES = (1.25, 0.1)


def cache_prices(settings) -> tuple[float, float] | None:
    """`CACHE_PRICES` when this adapter's bodies carry breakpoints (`llm.prompt_cache`), else None:
    with the breakpoints off nothing is cached, and a block sent once for the cache would be paid
    for in full by every request that carries it."""
    return CACHE_PRICES if bool(getattr(settings.llm, "prompt_cache", True)) else None


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

    def content(m) -> object:
        if m.get("documents"):
            return document_content(m, cache=cache)
        return block(m["content"]) if cache and m.get("cache") else m["content"]

    body_messages = [{"role": m["role"], "content": content(m)} for m in messages]
    return (block(system) if cache else system), body_messages


def document_content(message, *, cache: bool) -> list[dict]:
    """A message carrying files, as Anthropic Messages content: each file as a base64 `document`
    block, then the message's text. The files come FIRST, which is where the Messages API wants a
    PDF, and with `cache` the last of them ends in a breakpoint — the file is the large part every
    request of a run sends again, so the requests after the first read it from the cache. A
    message marked `cache` keeps its own breakpoint after the text as well.
    """
    docs = [{"type": "document",
             "source": {"type": "base64", "media_type": d["media_type"], "data": d["data"]}}
            for d in message.get("documents") or ()]
    if cache and docs:
        docs[-1]["cache_control"] = dict(CACHE_CONTROL)
    text: dict = {"type": "text", "text": message["content"]}
    if cache and message.get("cache"):
        text["cache_control"] = dict(CACHE_CONTROL)
    return [*docs, text]


def chat_content(message) -> object:
    """A message's content in the Chat Completions format: its text, or, when it carries files,
    each as a `file` part (a data URL, named neutrally) followed by the text."""
    if not message.get("documents"):
        return message["content"]
    parts: list[dict] = [{"type": "file",
                          "file": {"filename": d.get("name") or f"document-{i}.pdf",
                                   "file_data": f"data:{d['media_type']};base64,{d['data']}"}}
                         for i, d in enumerate(message["documents"], start=1)]
    return [*parts, {"type": "text", "text": message["content"]}]


def request_timeout(settings, messages) -> float:
    """Seconds one attempt may wait. A request carrying a file waits `llm.document_timeout_seconds`
    — the model reads every page before it answers — and so does one carrying the whole report as
    text (`long_read`), for the same reason: measured on 湖北能源 2024, the balance-sheet batch of
    the report requests failed with ReadTimeout at 120 seconds and its lines fell to the rules.
    The note router's one call is marked the same way (`services.note_router.route`): on the same
    filing it failed with ReadTimeout at 120 seconds and every line kept only its configured notes.
    Every other request waits `llm.timeout_seconds`, as before."""
    llm = settings.llm
    if any(m.get("documents") or m.get("long_read") for m in messages or ()):
        return float(getattr(llm, "document_timeout_seconds", llm.timeout_seconds))
    return float(llm.timeout_seconds)


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
