from __future__ import annotations

from pydantic import BaseModel

from app.adapters.bedrock_gateway_llm import BedrockGatewayLlmProvider
from app.config import Settings


class _Out(BaseModel):
    label: str


def test_bedrock_gateway_builds_anthropic_invoke_request():
    settings = Settings()
    settings.llm.provider = "bedrock_gateway"
    settings.llm.base_url = "https://llm-gateway.example.invalid/api/bedrock"
    settings.llm.model = "us.anthropic.claude-opus-4-7"
    provider = BedrockGatewayLlmProvider(settings)

    body = provider.build_body(system="map", messages=[{"role": "user", "content": "caption"}],
                               response_schema=_Out, max_tokens=256)

    # The model id goes into the path exactly as Bedrock names it — no provider prefix before it.
    assert provider._endpoint() == "https://llm-gateway.example.invalid/api/bedrock/model/us.anthropic.claude-opus-4-7/invoke"
    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert body["max_tokens"] == 256
    assert body["messages"] == [{"role": "user", "content": "caption"}]
    # The system text is one block ending in a prompt-cache breakpoint (`llm.prompt_cache`), which
    # Bedrock reads in the Anthropic body; the text is what the string was.
    (block,) = body["system"]
    assert block["cache_control"] == {"type": "ephemeral"} and "JSON Schema" in block["text"]


def test_the_report_message_is_cached_and_the_switch_sends_the_plain_body():
    settings = Settings()
    msgs = [{"role": "user", "content": "THE REPORT", "cache": True},
            {"role": "user", "content": "the question"}]
    body = BedrockGatewayLlmProvider(settings).build_body(
        system="map", messages=msgs, response_schema=_Out, max_tokens=256)
    assert body["messages"][0]["content"] == [{"type": "text", "text": "THE REPORT",
                                               "cache_control": {"type": "ephemeral"}}]
    assert body["messages"][1] == {"role": "user", "content": "the question"}
    settings.llm.prompt_cache = False
    plain = BedrockGatewayLlmProvider(settings).build_body(
        system="map", messages=msgs, response_schema=_Out, max_tokens=256)
    assert plain["messages"] == [{"role": "user", "content": "THE REPORT"},
                                 {"role": "user", "content": "the question"}]
    assert plain["system"] == body["system"][0]["text"]

def test_the_shipped_gateway_is_called_at_its_invoke_address_with_the_key_in_a_token_header(
        monkeypatch):
    """config.toml ships the CRISIL gateway: a machine sets only LLM_GATEWAY_TOKEN."""
    import httpx

    from app import config

    saved = dict(config._PINNED_LLM)
    config.pin_llm()                                 # the file's own [llm], not the suite's stub
    try:
        settings = Settings()
    finally:
        config.pin_llm(**saved)
    monkeypatch.setenv(settings.llm.api_key_env, "my-key")
    seen = {}

    class _Client:
        def __init__(self, **kw):
            seen["verify"] = kw.get("verify")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            seen.update(url=url, headers=headers, body=json)
            return httpx.Response(200, request=httpx.Request("POST", url), json={
                "model": "us.anthropic.claude-opus-4-7",
                "content": [{"type": "text", "text": '{"label": "ok"}'}],
                "usage": {"input_tokens": 3, "output_tokens": 2}})

    monkeypatch.setattr(httpx, "Client", _Client)
    parsed, meta = BedrockGatewayLlmProvider(settings).complete_structured(
        system="Respond concisely.", messages=[{"role": "user", "content": "Hello"}],
        response_schema=_Out, max_tokens=100)
    assert seen["url"] == ("https://llmgateway.crisil.local/api/bedrock/model/"
                           "us.anthropic.claude-opus-4-7/invoke")
    assert seen["headers"]["token"] == "my-key" and "Authorization" not in seen["headers"]
    assert seen["body"]["max_tokens"] == 100 and parsed.label == "ok"


def test_a_gateway_expecting_a_bearer_token_can_be_configured():
    settings = Settings()
    settings.llm.auth_header = "Authorization"
    provider = BedrockGatewayLlmProvider(settings)
    provider._api_key = lambda: "k"
    assert provider._auth_headers() == {"Authorization": "Bearer k"}


def test_a_missing_key_is_reported_at_startup_naming_the_variable(monkeypatch):
    from app.config import warn_if_llm_key_missing

    settings = Settings()
    settings.llm.provider, settings.llm.api_key_env = "bedrock_gateway", "LLM_GATEWAY_TOKEN"
    monkeypatch.delenv("LLM_GATEWAY_TOKEN", raising=False)
    assert "LLM_GATEWAY_TOKEN" in warn_if_llm_key_missing(settings)[0]
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "x")
    assert warn_if_llm_key_missing(settings) == []


# ── one retry, and an attempt bounded at 120 seconds ─────────────────────────────────────────────
# Madrigal's 10-K: the request holding the eight current-liability lines timed out after 600
# seconds and was never sent again, so none of those lines was answered.

def _gateway(monkeypatch, outcomes, **llm):
    """A provider whose httpx.Client answers each attempt with the next outcome: an exception to
    raise, or an HTTP status to return."""
    import httpx

    from app.adapters import bedrock_gateway_llm

    settings = Settings()
    settings.llm.base_url = "https://llm-gateway.example.invalid/api/bedrock"
    settings.llm.model = "m"
    for k, v in llm.items():
        setattr(settings.llm, k, v)
    monkeypatch.setenv(settings.llm.api_key_env, "k")
    monkeypatch.setattr(bedrock_gateway_llm, "_RETRY_PAUSE_SECONDS", 0)
    calls = []

    class _Client:
        def __init__(self, **kw):
            calls.append(kw.get("timeout"))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            outcome = outcomes[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return httpx.Response(outcome, request=httpx.Request("POST", url), json={
                "content": [{"type": "text", "text": '{"label": "ok"}'}], "usage": {}})

    monkeypatch.setattr(httpx, "Client", _Client)
    return BedrockGatewayLlmProvider(settings), calls


def _ask(provider):
    return provider.complete_structured(system="s", messages=[{"role": "user", "content": "q"}],
                                        response_schema=_Out, max_tokens=50)


def test_a_timed_out_request_is_sent_once_more(monkeypatch):
    import httpx

    provider, calls = _gateway(monkeypatch, [httpx.ReadTimeout("slow"), 200])
    parsed, _ = _ask(provider)
    assert parsed.label == "ok" and len(calls) == 2


def test_after_one_retry_the_timeout_stands(monkeypatch):
    import httpx
    import pytest

    provider, calls = _gateway(monkeypatch, [httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")])
    with pytest.raises(httpx.ReadTimeout):
        _ask(provider)
    assert len(calls) == 2


def test_a_busy_gateway_is_asked_again_and_a_refused_request_is_not(monkeypatch):
    import httpx
    import pytest

    provider, calls = _gateway(monkeypatch, [503, 200])
    assert _ask(provider)[0].label == "ok" and len(calls) == 2
    provider, calls = _gateway(monkeypatch, [400, 200])
    with pytest.raises(httpx.HTTPStatusError):
        _ask(provider)
    assert len(calls) == 1, "a malformed request is not sent twice"


def test_no_retries_means_one_attempt(monkeypatch):
    import httpx
    import pytest

    provider, calls = _gateway(monkeypatch, [httpx.ConnectError("down"), 200], retries=0)
    with pytest.raises(httpx.ConnectError):
        _ask(provider)
    assert len(calls) == 1


def test_an_attempt_waits_120_seconds_at_most():
    import pytest
    from pydantic import ValidationError

    from app import config
    from app.config import LlmSettings

    assert LlmSettings().timeout_seconds == 120 and LlmSettings().retries == 1
    with pytest.raises(ValidationError):
        LlmSettings(timeout_seconds=600)
    saved = dict(config._PINNED_LLM)
    config.pin_llm()                                 # the shipped file's own [llm]
    try:
        shipped = Settings().llm
    finally:
        config.pin_llm(**saved)
    assert shipped.timeout_seconds == 120 and shipped.retries == 1
