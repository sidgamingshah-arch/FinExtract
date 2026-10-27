"""AnthropicLlmProvider: request construction + structured parsing (no network).

The live network call needs a real key; here we verify everything up to it — the
request body shape, JSON parsing (including fenced output), and that the Anthropic
request_id and token usage are surfaced in LlmMeta.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.adapters.anthropic_llm import AnthropicLlmProvider, LlmConfigError


class _Out(BaseModel):
    label: str
    score: float


def _fake_client(text: str):
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=11, output_tokens=22),
        model="claude-opus-4-8",
        _request_id="req_01TEST",
    )
    messages = SimpleNamespace(create=lambda **kw: resp)
    return SimpleNamespace(messages=messages)


def test_build_request_embeds_schema_and_messages():
    p = AnthropicLlmProvider()
    req = p.build_request(
        system="do the thing",
        messages=[{"role": "user", "content": "hello"}],
        response_schema=_Out,
        max_tokens=128,
    )
    assert req["max_tokens"] == 128
    assert req["messages"] == [{"role": "user", "content": "hello"}]
    # ONE TEXT BLOCK ENDING IN A PROMPT-CACHE BREAKPOINT (`llm.prompt_cache`, on by default),
    # holding the same text the string did.
    (block,) = req["system"]
    assert block["cache_control"] == {"type": "ephemeral"}
    assert "JSON Schema" in block["text"] and "do the thing" in block["text"]


def test_a_message_marked_for_the_cache_ends_in_a_breakpoint_and_the_switch_turns_both_off():
    from app.config import Settings
    msgs = [{"role": "user", "content": "THE REPORT", "cache": True},
            {"role": "user", "content": "the question"}]
    req = AnthropicLlmProvider().build_request(system="s", messages=msgs, response_schema=_Out)
    assert req["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "THE REPORT",
                                      "cache_control": {"type": "ephemeral"}}]},
        {"role": "user", "content": "the question"}]
    off = Settings()
    off.llm.prompt_cache = False
    plain = AnthropicLlmProvider(off).build_request(system="s", messages=msgs,
                                                    response_schema=_Out)
    assert plain["messages"] == [{"role": "user", "content": "THE REPORT"},
                                 {"role": "user", "content": "the question"}]
    assert isinstance(plain["system"], str) and plain["system"] == req["system"][0]["text"]


def test_complete_structured_parses_and_reports_meta():
    p = AnthropicLlmProvider()
    p._client = _fake_client(json.dumps({"label": "cash", "score": 0.9}))
    out, meta = p.complete_structured(
        system="s", messages=[{"role": "user", "content": "m"}], response_schema=_Out,
    )
    assert out.label == "cash" and out.score == 0.9
    assert meta["model"] == "claude-opus-4-8"
    assert meta["request_id"] == "req_01TEST"
    assert meta["input_tokens"] == 11 and meta["output_tokens"] == 22


def test_complete_structured_tolerates_code_fences():
    p = AnthropicLlmProvider()
    p._client = _fake_client('```json\n{"label": "ppe", "score": 0.5}\n```')
    out, _ = p.complete_structured(
        system="s", messages=[{"role": "user", "content": "m"}], response_schema=_Out,
    )
    assert out.label == "ppe"


def test_missing_key_raises_config_error(monkeypatch):
    # No key in the configured env var → a clear LlmConfigError, not a raw SDK error.
    from app.config import get_settings

    monkeypatch.delenv(get_settings().llm.api_key_env, raising=False)
    p = AnthropicLlmProvider()
    with pytest.raises(LlmConfigError):
        p._client_or_raise()
