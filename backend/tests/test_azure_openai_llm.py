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
    # The deployment reaches Azure through an internal gateway rather than an Azure resource
    # directly, so the provider is the OpenAI wire format with a `<vendor>/<deployment>` model id
    # and a base_url in config.toml. What this test is FOR is unchanged by that: whatever the
    # default is, the file and the code must agree on it.
    assert shipped["provider"] == "openai_compatible"
    assert shipped["model"] == "azure-openai/gpt5.4-mini"
    assert shipped["api_key_env"] == "AZURE_OPENAI_API_KEY"

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


def test_the_azure_address_is_editable_and_persisted(client):
    """"With the ability to change" means from the running product, not by editing config.toml —
    and it has to SURVIVE, or the next process reverts to the default silently."""
    r = client.patch("/api/v1/settings", json={"llm": {
        "provider": "azure_openai",
        "model": "gpt-5-mini",
        "azure_endpoint": "https://tenant-a.openai.azure.com",
        "azure_deployment": "spread-mini",
        "azure_api_version": "2025-01-01-preview",
    }})
    assert r.status_code == 200, r.text

    got = client.get("/api/v1/settings").json()["llm"]
    assert got["azure_endpoint"] == "https://tenant-a.openai.azure.com"
    assert got["azure_deployment"] == "spread-mini"
    assert got["azure_api_version"] == "2025-01-01-preview"
    assert "api_key" not in got                      # never round-trips a secret

    from app.services.settings_state import SCOPE_LLM, _stored

    assert _stored(SCOPE_LLM).get("azure_deployment") == "spread-mini"


def test_switching_provider_away_from_azure_still_works(client):
    """Default, not commitment: the whole point of it being configuration."""
    r = client.patch("/api/v1/settings", json={"llm": {
        "provider": "anthropic", "model": "claude-opus-4-8",
        "api_key_env": "ANTHROPIC_API_KEY",
    }})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/settings").json()["llm"]["provider"] == "anthropic"

    from app.adapters import register_builtins
    from app.ports.registry import registry

    register_builtins()
    assert registry.get("llm", "anthropic") is not None
