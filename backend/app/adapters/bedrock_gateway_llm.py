"""Anthropic Messages adapter for the internal Bedrock invoke gateway."""
from __future__ import annotations

import os
from typing import Sequence

import httpx
from pydantic import BaseModel

from app.adapters._structured import LlmConfigError, schema_instruction, strip_fences
from app.config import Settings, get_settings
from app.ports.llm import LlmMessage, LlmMeta


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
        return f"{cfg.base_url.rstrip('/')}/model/bedrock.{cfg.model}/invoke"

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
                headers={"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"},
                json=self.build_body(system=system, messages=messages,
                                     response_schema=response_schema, max_tokens=max_tokens),
            )
        response.raise_for_status()
        payload = response.json()
        text = "".join(block.get("text", "") for block in payload.get("content", [])
                       if block.get("type") == "text")
        parsed = response_schema.model_validate_json(strip_fences(text))
        usage = payload.get("usage") or {}
        return parsed, {
            "model": payload.get("model", self._settings.llm.model),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        }