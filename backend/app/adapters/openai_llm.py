"""OpenAI-compatible LLM adapter for the LlmProvider port.

Speaks the OpenAI **Chat Completions** wire format (``POST {base_url}/chat/completions``)
so it works against OpenAI itself and any compatible gateway — TokenRouter, OpenRouter,
Together, a self-hosted vLLM, etc. — and any model they serve (e.g.
``moonshotai/kimi-k3-free``). Selected when ``config.toml [llm].provider`` is ``openai``
(or ``openai_compatible``); the endpoint comes from ``llm.base_url`` and the key is read
at call time from the env var named by ``llm.api_key_env``.

Structured output is obtained the same model-agnostic way as the Anthropic adapter:
embed the response model's JSON Schema in the system prompt and validate with Pydantic.
Uses ``httpx`` (already a dependency) — no vendor SDK required. Token usage is returned
in ``LlmMeta`` (``input_tokens`` / ``output_tokens``) for the audit log.
"""
from __future__ import annotations

import os
import re
import time
from typing import Sequence

import httpx
from pydantic import BaseModel

from app.adapters._structured import LlmConfigError, schema_instruction, strip_fences
from app.config import Settings, get_settings
from app.ports.llm import LlmMessage, LlmMeta

__all__ = ["OpenAiLlmProvider", "LlmConfigError"]

# Transient gateway conditions: a single one of these on one chunk used to fail the whole run.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3


def _completion_limit_from_error(message: str) -> int | None:
    match = re.search(r"supports at most ([\d,]+) completion tokens", message, re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else None


class OpenAiLlmProvider:
    id = "openai"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def _endpoint(self) -> str:
        base = self._settings.llm.base_url or "https://api.openai.com/v1"
        return base.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict:
        """Auth + content type. Overridden by the Azure adapter, which authenticates with an
        ``api-key`` header rather than a bearer token."""
        return {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}

    def _api_key(self) -> str:
        cfg = self._settings.llm
        key = os.environ.get(cfg.api_key_env)
        if not key:
            raise LlmConfigError(
                f"No API key found. Set the {cfg.api_key_env} environment variable "
                f"(configured via config.toml [llm].api_key_env)."
            )
        return key

    def build_body(
        self,
        *,
        system: str,
        messages: Sequence[LlmMessage],
        response_schema: type[BaseModel],
        temperature: float,
        max_tokens: int,
        json_mode: bool = True,
    ) -> dict:
        """Construct the exact request body (used by complete_structured and dry-runs)."""
        full_system = f"{system}\n\n{schema_instruction(response_schema)}"
        body: dict = {
            "model": self._settings.llm.model,
            "messages": [{"role": "system", "content": full_system},
                         *[{"role": m["role"], "content": m["content"]} for m in messages]],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        effort = (self._settings.llm.reasoning_effort or "").strip()
        # Mapping needs a compact, deterministic JSON decision. On reasoning models, reasoning
        # tokens share this response's completion budget; requesting them can exhaust a small
        # mapping batch before it emits any JSON at all.
        is_mapping_response = response_schema.__module__ == "app.services.mapping"
        if effort and not is_mapping_response:
            body["reasoning_effort"] = effort
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def complete_structured(
        self,
        *,
        system: str,
        messages: Sequence[LlmMessage],
        response_schema: type[BaseModel],
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> tuple[BaseModel, LlmMeta]:
        headers = self._headers()

        # Adaptive body builder — each flag is flipped at most once, driven by the 400 body.
        json_mode, renamed_cap, drop_temp = True, False, False
        provider_token_cap: int | None = None

        def _body() -> dict:
            b = self.build_body(
                system=system, messages=messages, response_schema=response_schema,
                temperature=temperature,
                max_tokens=min(max_tokens, provider_token_cap or max_tokens),
                json_mode=json_mode,
            )
            if renamed_cap:
                b["max_completion_tokens"] = b.pop("max_tokens")
            if drop_temp:
                b.pop("temperature", None)
            return b

        try:
            with httpx.Client(
                timeout=self._settings.llm.timeout_seconds,
                verify=not self._settings.llm.disable_ssl_verify,
            ) as client:
                # Retry transient gateway/transport failures with backoff; a 5xx or network blip
                # on one chunk must not fail the whole extraction.
                for attempt in range(_MAX_ATTEMPTS):
                    try:
                        # One attempt per possible 400 adaptation, including a provider-advertised
                        # token cap.
                        for _ in range(5):
                            resp = client.post(self._endpoint(), headers=headers, json=_body())
                            if resp.status_code != 400:
                                break
                            said = resp.text.lower()
                            advertised_cap = _completion_limit_from_error(resp.text)
                            if advertised_cap and advertised_cap < (provider_token_cap or max_tokens):
                                provider_token_cap = advertised_cap
                            elif "max_completion_tokens" in said and not renamed_cap:
                                renamed_cap = True
                            elif "temperature" in said and not drop_temp:
                                drop_temp = True
                            elif "response_format" in said and json_mode:
                                json_mode = False
                            else:
                                break
                        if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                            time.sleep(min(2 ** attempt, 8))
                            continue
                        resp.raise_for_status()
                        break
                    except httpx.HTTPStatusError:
                        raise
                    except httpx.HTTPError:
                        if attempt < _MAX_ATTEMPTS - 1:
                            time.sleep(min(2 ** attempt, 8))
                            continue
                        raise
        except httpx.HTTPStatusError as exc:
            raise LlmConfigError(
                f"Gateway returned {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmConfigError(f"Request to {self._endpoint()} failed: {exc}") from exc

        payload = resp.json()
        choice = payload["choices"][0]
        content = choice["message"].get("content") or ""
        if not content.strip():
            usage = payload.get("usage", {}) or {}
            completion = usage.get("completion_tokens_details", {}) or {}
            raise LlmConfigError(
                "Gateway returned an empty LLM response "
                f"(finish_reason={choice.get('finish_reason')!r}, "
                f"completion_tokens={usage.get('completion_tokens', 0)}, "
                f"reasoning_tokens={completion.get('reasoning_tokens', 0)}). "
                "Increase llm.max_tokens or lower reasoning_effort."
            )
        parsed = response_schema.model_validate_json(strip_fences(content))

        usage = payload.get("usage", {}) or {}
        meta: LlmMeta = {
            "model": payload.get("model", self._settings.llm.model),
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
        rid = resp.headers.get("x-request-id") or payload.get("id")
        if rid:
            meta["request_id"] = rid  # type: ignore[typeddict-unknown-key]
        return parsed, meta
