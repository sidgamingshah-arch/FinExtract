"""Anthropic Messages adapter for the internal Bedrock invoke gateway.

The CRISIL gateway serves a Bedrock model at ``{base_url}/model/{model}/invoke`` — e.g.
``https://llmgateway.crisil.local/api/bedrock/model/us.anthropic.claude-opus-4-7/invoke`` — with the
model id exactly as Bedrock names it, and reads the key from a ``token`` header
(``[llm].auth_header``). The body is Bedrock's Anthropic Messages format.
"""
from __future__ import annotations

import os
from typing import Sequence

import httpx
from pydantic import BaseModel

from app.adapters._structured import LlmConfigError, schema_instruction, strip_fences
from app.config import Settings, get_settings
from app.ports.llm import LlmMessage, LlmMeta, anthropic_usage


class BedrockGatewayLlmProvider:
    id = "bedrock_gateway"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def _api_key(self) -> str:
        cfg = self._settings.llm
        key = os.environ.get(cfg.api_key_env)
        if not key:
            raise LlmConfigError(f"No API key found. Set the {cfg.api_key_env} environment variable.")
        return key

    def _endpoint(self) -> str:
        cfg = self._settings.llm
        if not cfg.base_url or not cfg.model:
            raise LlmConfigError("Bedrock gateway requires llm.base_url and llm.model (deployment name).")
        return f"{cfg.base_url.rstrip('/')}/model/{cfg.model}/invoke"

    def _auth_headers(self) -> dict[str, str]:
        name = (self._settings.llm.auth_header or "token").strip()
        key = self._api_key()
        if name.lower() == "authorization":
            return {"Authorization": f"Bearer {key}"}
        return {name: key}

    def build_body(self, *, system: str, messages: Sequence[LlmMessage],
                   response_schema: type[BaseModel], max_tokens: int) -> dict:
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": f"{system}\n\n{schema_instruction(response_schema)}",
            "messages": [{"role": message["role"], "content": message["content"]}
                         for message in messages],
        }

    def complete_structured(self, *, system: str, messages: Sequence[LlmMessage],
                            response_schema: type[BaseModel], temperature: float = 0.0,
                            max_tokens: int = 2048) -> tuple[BaseModel, LlmMeta]:
        del temperature
        with httpx.Client(
            timeout=self._settings.llm.timeout_seconds,
            verify=not self._settings.llm.disable_ssl_verify,
        ) as client:
            response = client.post(
                self._endpoint(),
                headers={**self._auth_headers(), "Content-Type": "application/json"},
                json=self.build_body(system=system, messages=messages,
                                     response_schema=response_schema, max_tokens=max_tokens),
            )
        response.raise_for_status()
        payload = response.json()
        text = "".join(block.get("text", "") for block in payload.get("content", [])
                       if block.get("type") == "text")
        parsed = response_schema.model_validate_json(strip_fences(text))
        return parsed, {
            "model": payload.get("model", self._settings.llm.model),
            **anthropic_usage(payload.get("usage") or {}),
        }