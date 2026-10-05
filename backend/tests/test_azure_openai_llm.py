"""Azure OpenAI as the default provider (GPT-5 mini), and switchable at run time."""
from __future__ import annotations

import pytest

from app.config import get_settings


def test_the_shipped_default_and_the_code_fallback_name_the_same_model():
    """Asserted against the SHIPPED config and the model default, not against the merged settings.

    A developer's git-ignored .env legitimately overrides provider/model/base_url for local work —
    routing through a gateway, for instance — and it wins over config.toml by design. A test reading
    the merged value therefore asserts whatever that machine happens to be pointed at, and would go
    green or red for reasons that have nothing to do with the product's default.
    """
    import tomllib
    from pathlib import Path

    from app.config import LlmSettings

    shipped = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "config.toml").read_text(encoding="utf-8"))["llm"]
    # The deployment reaches the model through the CRISIL LLM gateway's Bedrock invoke route, and
    # config.toml ships that gateway, so a machine supplies only the key. What this test is FOR is
    # unchanged by that: whatever the default is, the file and the code must agree on it.
    assert shipped["provider"] == "bedrock_gateway"
    assert shipped["model"] == "us.anthropic.claude-opus-4-7"
    assert shipped["base_url"] == "https://llmgateway.crisil.local/api/bedrock"
    assert shipped["api_key_env"] == "LLM_GATEWAY_TOKEN"

    # And the code's own fallback agrees, so a deployment without config.toml lands in the same
    # place. Asserted against the same three values rather than restated, so the pair cannot
    # drift apart with both halves of this test still passing.
    d = LlmSettings()
    assert (d.provider, d.model, d.api_key_env) == (
        shipped["provider"], shipped["model"], shipped["api_key_env"])
    # The KEY is never configuration — only the name of the variable holding it, so a credential
    # cannot reach the database or a settings export.
    assert not hasattr(d, "api_key")


def test_the_adapter_is_registered_under_both_ids():
    from app.adapters import register_builtins
    from app.ports.registry import registry

    register_builtins()
    for pid in ("azure_openai", "azure"):
        assert registry.get("llm", pid) is not None


def test_the_url_addresses_a_deployment_not_a_model(monkeypatch):
    """Azure puts the deployment in the PATH and the model nowhere. Built wrong, the request 404s
    against a resource that is configured perfectly."""
    from app.adapters.azure_openai_llm import AzureOpenAiLlmProvider

    s = get_settings()
    monkeypatch.setattr(s.llm, "model", "gpt-5-mini", raising=False)
    monkeypatch.setattr(s.llm, "azure_endpoint", "https://acme.openai.azure.com/", raising=False)
    monkeypatch.setattr(s.llm, "azure_deployment", "", raising=False)
    monkeypatch.setattr(s.llm, "azure_api_version", "2024-12-01-preview", raising=False)
    monkeypatch.setattr(s.llm, "base_url", "", raising=False)

    url = AzureOpenAiLlmProvider(s)._endpoint()
    assert url == ("https://acme.openai.azure.com/openai/deployments/gpt-5-mini"
                   "/chat/completions?api-version=2024-12-01-preview")

    # A deployment named something other than the model it serves — the common case — wins.
    monkeypatch.setattr(s.llm, "azure_deployment", "finex-mini-prod", raising=False)
    assert "/deployments/finex-mini-prod/" in AzureOpenAiLlmProvider(s)._endpoint()


def test_it_authenticates_with_api_key_not_a_bearer_token(monkeypatch):
    """Sent as a bearer token Azure refuses with a 401 that says nothing about which convention it
    expected, which is a long afternoon."""
    from app.adapters.azure_openai_llm import AzureOpenAiLlmProvider

    s = get_settings()
    monkeypatch.setenv(s.llm.api_key_env, "sekret")
    h = AzureOpenAiLlmProvider(s)._headers()
    assert h["api-key"] == "sekret"
    assert "Authorization" not in h


def test_a_missing_resource_or_deployment_says_which(monkeypatch):
    """There is no default host: the deployment lives on the customer's resource. Failing with a
    generic connection error would send someone looking at the network."""
    from app.adapters._structured import LlmConfigError
    from app.adapters.azure_openai_llm import AzureOpenAiLlmProvider

    s = get_settings()
    monkeypatch.setattr(s.llm, "base_url", "", raising=False)
    monkeypatch.setattr(s.llm, "azure_endpoint", "", raising=False)
    with pytest.raises(LlmConfigError, match="azure_endpoint"):
        AzureOpenAiLlmProvider(s)._endpoint()

    monkeypatch.setattr(s.llm, "azure_endpoint", "https://acme.openai.azure.com", raising=False)
    monkeypatch.setattr(s.llm, "azure_deployment", "", raising=False)
    monkeypatch.setattr(s.llm, "model", "", raising=False)
    with pytest.raises(LlmConfigError, match="deployment"):
        AzureOpenAiLlmProvider(s)._endpoint()


def test_the_azure_address_comes_from_config_toml_not_the_running_product(client):
    """The LLM is defined only in config.toml [llm]; the running product reports the Azure
    address but cannot change it."""
    r = client.patch("/api/v1/settings", json={"llm": {
        "provider": "azure_openai", "azure_endpoint": "https://tenant-a.openai.azure.com"}})
    assert r.status_code == 400 and "config.toml" in r.json()["detail"]

    from app.config import Settings, pin_llm, _PINNED_LLM

    saved = dict(_PINNED_LLM)
    try:
        pin_llm(provider="azure_openai", azure_endpoint="https://tenant-a.openai.azure.com",
                azure_deployment="spread-mini", azure_api_version="2025-01-01-preview")
        got = client.get("/api/v1/settings").json()["llm"]
        assert got["azure_endpoint"] == "https://tenant-a.openai.azure.com"
        assert got["azure_deployment"] == "spread-mini"
        assert got["azure_api_version"] == "2025-01-01-preview"
        assert "api_key" not in got                      # never round-trips a secret
        assert Settings().llm.azure_deployment == "spread-mini"
    finally:
        pin_llm()
        pin_llm(**saved)


def test_switching_provider_away_from_azure_still_works():
    """Default, not commitment: any registered provider can be named in config.toml [llm]."""
    from app.adapters import register_builtins
    from app.ports.registry import registry

    register_builtins()
    assert registry.get("llm", "anthropic") is not None


def test_config_local_toml_overrides_the_shared_file_key_by_key(tmp_path, monkeypatch):
    """This machine's gateway address and model come from the gitignored config.local.toml, over
    config.toml, and only the keys it names change — the shared settings stay config.toml's."""
    import app.config as config

    local = tmp_path / "config.local.toml"
    local.write_text('[llm]\nbase_url = "https://gw.example.invalid/api/openai"\n'
                     'model = "vendor/deployment"\n', encoding="utf-8")
    monkeypatch.setattr(config, "_LOCAL_TOML", local)
    s = config.Settings()
    assert s.llm.base_url == "https://gw.example.invalid/api/openai"
    assert s.llm.model == "vendor/deployment"
    assert s.llm.api_key_env == "LLM_GATEWAY_TOKEN"         # still config.toml's
    assert config.warn_if_llm_unconfigured(s) == []


def test_a_missing_gateway_is_reported_at_startup_with_where_to_set_it(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "_LOCAL_TOML", tmp_path / "config.local.toml")   # absent
    s = config.Settings()
    s.llm.provider, s.llm.base_url, s.llm.model = "openai_compatible", "", ""
    lines = config.warn_if_llm_unconfigured(s)
    assert lines and "base_url and model" in lines[0] and "config.local.toml" in lines[0]


def test_a_gateway_with_no_address_is_refused_not_sent_to_openai():
    """With the gateway address missing (no config.local.toml), the OpenAI-format adapter must not
    fall back to api.openai.com: that would send a filing to a provider nobody configured."""
    import pytest

    from app.adapters.openai_llm import OpenAiLlmProvider as P
    from app.config import Settings
    from app.adapters._structured import LlmConfigError

    s = Settings()
    s.llm.provider, s.llm.base_url = "openai_compatible", ""
    with pytest.raises(LlmConfigError, match="config.local.toml"):
        P(s)._endpoint()
    s.llm.provider = "openai"                     # OpenAI itself keeps its public address
    assert P(s)._endpoint() == "https://api.openai.com/v1/chat/completions"
