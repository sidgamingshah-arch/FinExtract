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
    assert "JSON Schema" in body["system"]

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
