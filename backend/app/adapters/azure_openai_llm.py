"""Azure OpenAI adapter — the default LLM provider (GPT-5 mini).

Azure speaks the same Chat Completions body as OpenAI, and two things about it are different enough
to be worth stating, because both are silent failures when got wrong:

* THE ADDRESS. OpenAI selects a model by name on one shared endpoint. Azure selects a *deployment*
  on your own resource, and the model name appears nowhere in the URL:
  ``{azure_endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...``.
  A deployment is frequently not named after the model it serves, so it is configured separately and
  falls back to ``llm.model`` only because naming them the same is the common case.
* THE AUTH. Azure authenticates with an ``api-key`` header. Sent as a bearer token the request is
  refused with a 401 that says nothing about which of the two conventions was expected.

The GPT-5 family also renamed the output cap to ``max_completion_tokens`` and rejects a non-default
``temperature``. Rather than hard-code a model-name test that goes stale on the next release, the
request is adapted on the 400 that says so — the same shape as the base adapter's existing
``response_format`` retry, and self-correcting when Azure changes its mind again.

Everything here is configuration (``config.toml [llm]``, editable at run time from the Settings
screen and persisted). The API KEY is not: only the NAME of the environment variable holding it is
stored, so a credential cannot reach the database or a settings export.
"""
from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel

from app.adapters._structured import LlmConfigError
from app.adapters.openai_llm import OpenAiLlmProvider
from app.ports.llm import LlmMessage, LlmMeta

__all__ = ["AzureOpenAiLlmProvider"]

# Parameters the GPT-5 family does not accept, and what to do about each. Keyed by the fragment
# Azure puts in the 400 body, because that is the thing that actually tells us.
_UNSUPPORTED_TEMPERATURE = "temperature"
_RENAMED_MAX_TOKENS = "max_completion_tokens"


class AzureOpenAiLlmProvider(OpenAiLlmProvider):
    id = "azure_openai"

    def _endpoint(self) -> str:
        cfg = self._settings.llm
        # base_url wins when set, so a gateway or a private-link host in front of Azure stays
        # reachable without a code change.
        base = (cfg.base_url or cfg.azure_endpoint or "").rstrip("/")
        if not base:
            raise LlmConfigError(
                "Azure OpenAI needs the resource endpoint. Set [llm].azure_endpoint to "
                "https://<resource>.openai.azure.com (or [llm].base_url to a gateway in front of "
                "it). Unlike OpenAI there is no default host: the deployment lives on your resource."
            )
        deployment = cfg.azure_deployment_name()
        if not deployment:
            raise LlmConfigError(
                "Azure OpenAI needs a deployment name. Set [llm].azure_deployment, or [llm].model "
                "when the deployment is named after the model it serves."
            )
        return (f"{base}/openai/deployments/{deployment}/chat/completions"
                f"?api-version={cfg.azure_api_version}")

    def _headers(self) -> dict:
        return {"api-key": self._api_key(), "Content-Type": "application/json"}

    def complete_structured(
        self,
        *,
        system: str,
        messages: Sequence[LlmMessage],
        response_schema: type[BaseModel],
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> tuple[BaseModel, LlmMeta]:
        """As the base adapter, but adapting to the GPT-5 parameter surface when Azure says to.

        The adaptation is driven by the 400 body rather than by a model-name test: a name test is
        wrong the moment a deployment is called something else, and stale the moment the next model
        family lands.
        """
        import httpx

        from app.adapters._structured import strip_fences

        cfg = self._settings.llm
        headers = self._headers()

        def _body(*, json_mode: bool, renamed_cap: bool, drop_temperature: bool) -> dict:
            b = self.build_body(
                system=system, messages=messages, response_schema=response_schema,
                temperature=temperature, max_tokens=max_tokens, json_mode=json_mode,
            )
            if renamed_cap:
                b["max_completion_tokens"] = b.pop("max_tokens")
            if drop_temperature:
                b.pop("temperature", None)
            return b

        json_mode, renamed_cap, drop_temperature = True, False, False
        try:
            with httpx.Client(
                timeout=cfg.timeout_seconds,
                verify=not cfg.disable_ssl_verify,
            ) as client:
                for _ in range(4):                       # one attempt per adaptation, then give up
                    resp = client.post(
                        self._endpoint(), headers=headers,
                        json=_body(json_mode=json_mode, renamed_cap=renamed_cap,
                                   drop_temperature=drop_temperature),
                    )
                    if resp.status_code != 400:
                        break
                    said = resp.text.lower()
                    if _RENAMED_MAX_TOKENS in said and not renamed_cap:
                        renamed_cap = True
                    elif _UNSUPPORTED_TEMPERATURE in said and not drop_temperature:
                        drop_temperature = True
                    elif "response_format" in said and json_mode:
                        json_mode = False
                    else:
                        break                            # a 400 we cannot adapt to: report it
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LlmConfigError(
                f"Azure OpenAI returned {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmConfigError(f"Request to Azure OpenAI failed: {exc}") from exc

        payload = resp.json()
        content = payload["choices"][0]["message"]["content"]
        parsed = response_schema.model_validate_json(strip_fences(content))

        usage = payload.get("usage", {}) or {}
        meta: LlmMeta = {
            # Azure echoes the underlying model, which is what the audit log should record — the
            # deployment name is an address, not an identity.
            "model": payload.get("model") or cfg.azure_deployment_name() or cfg.model,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
        rid = resp.headers.get("apim-request-id") or resp.headers.get("x-request-id")
        if rid:
            meta["request_id"] = rid  # type: ignore[typeddict-unknown-key]
        return parsed, meta
