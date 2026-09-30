"""Real Anthropic Claude adapter for the LlmProvider port.

Implements ``complete_structured`` against the Anthropic Messages API using the
official ``anthropic`` SDK (install the ``llm`` extra: ``pip install -e ".[llm]"``).
The model, timeout and endpoint come from ``config.toml`` ``[llm]``; the API key is
read at call time from the environment variable named by ``llm.api_key_env`` (default
``ANTHROPIC_API_KEY``) and is never stored in config.

Structured output is obtained by embedding the response model's JSON Schema in the
system prompt and validating the returned text with Pydantic. This is deliberately
model-agnostic — it works across Claude model families without depending on a
particular model's ``output_config`` / sampling-parameter support — and keeps the
adapter a thin, swappable port implementation. The response carries the Anthropic
``request_id`` and token usage back in ``LlmMeta`` for auditing.
"""
from __future__ import annotations

import os
from typing import Sequence

from pydantic import BaseModel

from app.adapters._structured import LlmConfigError, schema_instruction, strip_fences
from app.config import Settings, get_settings
from app.ports.llm import LlmMessage, LlmMeta, anthropic_usage

__all__ = ["AnthropicLlmProvider", "LlmConfigError"]


class AnthropicLlmProvider:
    id = "anthropic"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._client = None  # lazily constructed so importing this module needs no key

    def _client_or_raise(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic  # noqa: WPS433 - lazy import; only needed when selected
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
            raise LlmConfigError(
                "The 'anthropic' SDK is not installed. Run: pip install -e \".[llm]\""
            ) from exc

        cfg = self._settings.llm
        api_key = os.environ.get(cfg.api_key_env)
        if not api_key:
            raise LlmConfigError(
                f"No API key found. Set the {cfg.api_key_env} environment variable "
                f"(configured via config.toml [llm].api_key_env)."
            )
        kwargs: dict = {"api_key": api_key, "timeout": cfg.timeout_seconds}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def build_request(
        self,
        *,
        system: str,
        messages: Sequence[LlmMessage],
        response_schema: type[BaseModel],
        max_tokens: int = 2048,
    ) -> dict:
        """Construct the exact request body (used by complete_structured and dry-runs)."""
        full_system = f"{system}\n\n{schema_instruction(response_schema)}"
        return {
            "model": self._settings.llm.model,
            "max_tokens": max_tokens,
            "system": full_system,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }

    def complete_structured(
        self,
        *,
        system: str,
        messages: Sequence[LlmMessage],
        response_schema: type[BaseModel],
        temperature: float = 0.0,  # accepted for port compatibility; not sent (newer
        max_tokens: int = 2048,    # Claude models reject sampling params with a 400)
    ) -> tuple[BaseModel, LlmMeta]:
        client = self._client_or_raise()
        req = self.build_request(
            system=system, messages=messages,
            response_schema=response_schema, max_tokens=max_tokens,
        )
        resp = client.messages.create(**req)

        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        parsed = response_schema.model_validate_json(strip_fences(text))

        meta: LlmMeta = {
            "model": resp.model,
            **anthropic_usage(resp.usage),
        }
        # request_id is public despite the underscore; log it when reporting to Anthropic.
        rid = getattr(resp, "_request_id", None)
        if rid:
            meta["request_id"] = rid  # type: ignore[typeddict-unknown-key]
        return parsed, meta
