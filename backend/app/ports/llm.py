"""LLM provider port.

The default implementation should be an Anthropic Claude adapter using structured
tool-use / JSON mode with a Pydantic-validated schema and temperature 0 for
determinism. Verify the exact model id / params against the ``claude-api`` skill at
implementation time. Kept swappable for a self-hosted model (air-gapped).
"""
from __future__ import annotations

from typing import NotRequired, Protocol, Sequence, TypedDict, runtime_checkable

from pydantic import BaseModel


class LlmDocument(TypedDict):
    """A FILE SENT TO THE MODEL AS ITSELF — a PDF, in base64 — rather than as text the app read
    out of it. Carried by the whole-PDF route (`services.whole_pdf`) and nothing else."""
    media_type: str          # "application/pdf"
    data: str                # the file, base64, one line
    name: str                # a neutral name, "document-1.pdf" — never the uploaded file's own


class LlmMessage(TypedDict):
    role: str
    content: str
    # A PROMPT-CACHE BREAKPOINT AFTER THIS MESSAGE: everything up to its end is the same on every
    # request that sends it, so a provider that caches prompts may serve that prefix from its
    # cache. Set on the whole-report message (`services.line_item_llm.ask`). An Anthropic-format
    # adapter turns it into `cache_control` (`adapters._structured.messages_body`); an adapter whose
    # provider caches by prefix on its own, or not at all, sends `role` and `content` only and
    # never forwards it.
    cache: NotRequired[bool]
    # A LONG READ: the message carries the WHOLE annual report as text, so the model reads every
    # page before it answers, as it does a file — or it is the note router's one call over every
    # heading and every line, whose reply is as long as the set. The request waits
    # `llm.document_timeout_seconds` rather than `llm.timeout_seconds`
    # (`adapters._structured.request_timeout`). Like `cache`, an
    # adapter sends `role` and `content` only and never forwards it.
    long_read: NotRequired[bool]
    # FILES SENT BEFORE THIS MESSAGE'S TEXT, in order. Only an adapter whose class says
    # `takes_documents = True` is handed a message carrying them (`takes_documents`); it sends each
    # as the provider's own file block — a `document` block on the Anthropic body, a `file` part
    # on Chat Completions — and, where it caches, ends the files with a breakpoint, so the
    # requests after the first read the file from the cache.
    documents: NotRequired[list[LlmDocument]]


def takes_documents(provider) -> bool:
    """Whether this provider can be sent a file as itself (`LlmMessage.documents`)."""
    return bool(getattr(provider, "takes_documents", False))


def carries_documents(messages) -> bool:
    """Whether any of these messages carries a file."""
    return any(m.get("documents") for m in messages or ())


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
    # Set when the reply was cut off at its budget and asked for again with more room
    # (`complete_with_room`).
    retried_for_room: bool


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


#: The most a reply given more room is allowed. Twice the request's own budget, up to this.
ROOM_CEILING = 16_000


def cut_off(exc: BaseException) -> bool:
    """Whether a provider call failed because the reply was CUT OFF — its JSON ends mid-value.

    A reply that hits its token budget arrives as JSON that stops part-way, and validating it raises
    pydantic's `json_invalid` "EOF while parsing". That is not a wrong answer, it is a short budget:
    measured on four CAS filings, every failed line-item request and every failed disclosure
    reading was one (`EOF while parsing a string at line 1 column 3460`), Chinese captions and
    page citations running past a budget sized for English.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return False
    try:
        found = errors()
    except Exception:  # noqa: BLE001 - not a validation error after all
        return False
    return any(e.get("type") == "json_invalid" and "EOF" in str(e.get("msg", ""))
               for e in found or ())


def complete_with_room(provider, *, system: str, messages, response_schema: type[BaseModel],
                       max_tokens: int) -> tuple[BaseModel, LlmMeta]:
    """`complete_structured`, asked once more with twice the room when the reply is cut off.

    ONE RETRY, AND ONLY FOR A CUT-OFF REPLY: any other failure raises at once, as before, because
    asking again cannot change it. The retry's meta says so (`retried_for_room`), and its output
    tokens are both calls' — the first reply was paid for too.
    """
    try:
        return provider.complete_structured(system=system, messages=messages,
                                            response_schema=response_schema,
                                            max_tokens=max_tokens)
    except Exception as exc:  # noqa: BLE001 - re-raised unless it is a cut-off reply
        if not cut_off(exc) or max_tokens >= ROOM_CEILING:
            raise
    reply, meta = provider.complete_structured(system=system, messages=messages,
                                               response_schema=response_schema,
                                               max_tokens=min(ROOM_CEILING, 2 * max_tokens))
    meta = dict(meta or {})
    meta["retried_for_room"] = True
    meta["output_tokens"] = int(meta.get("output_tokens") or 0) + max_tokens
    return reply, meta

