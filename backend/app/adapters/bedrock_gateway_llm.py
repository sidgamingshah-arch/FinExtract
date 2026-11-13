"""Anthropic Messages adapter for the internal Bedrock invoke gateway.

The CRISIL gateway serves a Bedrock model at ``{base_url}/model/{model}/invoke`` — e.g.
``https://llmgateway.crisil.local/api/bedrock/model/us.anthropic.claude-opus-4-7/invoke`` — with the
model id exactly as Bedrock names it, and reads the key from a ``token`` header
(``[llm].auth_header``). The body is Bedrock's Anthropic Messages format.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Sequence

import httpx
from pydantic import BaseModel

from app.adapters._structured import (LlmConfigError, cache_prices, messages_body,
                                      schema_instruction, strip_fences)
from app.config import Settings, get_settings
from app.ports.llm import LlmMessage, LlmMeta, anthropic_usage

_log = logging.getLogger(__name__)

#: The gateway statuses that say "not now" rather than "not this": rate-limited, or failing upstream.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: A short pause before the second attempt, so a gateway that is momentarily overloaded is not hit
#: again in the same instant.
_RETRY_PAUSE_SECONDS = 2.0


class BedrockGatewayLlmProvider:
    id = "bedrock_gateway"

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    @property
    def prompt_cache_prices(self) -> tuple[float, float] | None:
        """`(write, read)` price multiples of this provider's prompt cache, or None when its bodies
        carry no breakpoints (`_structured.cache_prices`)."""
        return cache_prices(self._settings)

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
        # WITH ITS PROMPT-CACHE BREAKPOINTS (`_structured.messages_body`), which Bedrock reads in
        # the Anthropic body as the Messages API does. `llm.prompt_cache` turns them off for a
        # gateway that refuses the field.
        body_system, body_messages = messages_body(
            f"{system}\n\n{schema_instruction(response_schema)}", messages,
            cache=bool(getattr(self._settings.llm, "prompt_cache", True)))
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": body_system,
            "messages": body_messages,
        }

    def complete_structured(self, *, system: str, messages: Sequence[LlmMessage],
                            response_schema: type[BaseModel], temperature: float = 0.0,
                            max_tokens: int = 2048) -> tuple[BaseModel, LlmMeta]:
        del temperature
        endpoint = self._endpoint()
        headers = {**self._auth_headers(), "Content-Type": "application/json"}
        body = self.build_body(system=system, messages=messages,
                               response_schema=response_schema, max_tokens=max_tokens)
        response = self._post(endpoint, headers, body)
        response.raise_for_status()
        payload = response.json()
        text = "".join(block.get("text", "") for block in payload.get("content", [])
                       if block.get("type") == "text")
        parsed = response_schema.model_validate_json(strip_fences(text))
        return parsed, {
            "model": payload.get("model", self._settings.llm.model),
            **anthropic_usage(payload.get("usage") or {}),
        }

    def _post(self, endpoint: str, headers: dict, body: dict) -> httpx.Response:
        """POST the request, and once more if the first attempt failed for a reason a second may not.

        A TIMEOUT USED TO LOSE THE WHOLE REQUEST. This adapter made one attempt, so one slow reply
        took every line the request held with it — on Madrigal's 10-K the eight current-liability
        lines, the current lease liabilities among them, were answered by nothing after a
        ReadTimeout. The second attempt is for what a second attempt can cure: a timeout or a
        dropped connection (`httpx.TransportError`) and a gateway that says it is busy or failing
        (`_RETRY_STATUSES`). A refusal of the request itself (400, 401, 403, 404) fails at once,
        because sending it again sends the same mistake again.

        `llm.retries` attempts after the first (1), each bounded by `llm.timeout_seconds`.
        """
        cfg = self._settings.llm
        attempts = 1 + max(0, int(getattr(cfg, "retries", 1) or 0))
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=cfg.timeout_seconds,
                                  verify=not cfg.disable_ssl_verify) as client:
                    response = client.post(endpoint, headers=headers, json=body)
            except httpx.TransportError as exc:
                if attempt >= attempts:
                    raise
                _log.warning("bedrock_gateway: attempt %d of %d failed (%s: %s); retrying",
                             attempt, attempts, type(exc).__name__, exc)
                time.sleep(_RETRY_PAUSE_SECONDS)
                continue
            if response.status_code in _RETRY_STATUSES and attempt < attempts:
                _log.warning("bedrock_gateway: attempt %d of %d answered HTTP %d; retrying",
                             attempt, attempts, response.status_code)
                time.sleep(_RETRY_PAUSE_SECONDS)
                continue
            return response
        raise AssertionError("unreachable: the last attempt returns or raises")