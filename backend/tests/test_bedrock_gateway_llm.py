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

    assert provider._endpoint() == "https://llm-gateway.example.invalid/api/bedrock/model/bedrock.us.anthropic.claude-opus-4-7/invoke"
    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert body["max_tokens"] == 256
    assert body["messages"] == [{"role": "user", "content": "caption"}]
    assert "JSON Schema" in body["system"]