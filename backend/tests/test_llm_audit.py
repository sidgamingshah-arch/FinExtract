"""Tests for the LLM provider wiring, run-id scheme, and the token-audit log."""
from __future__ import annotations

import re

import app.adapters  # noqa: F401 — importing registers the built-in adapters
from app.config import get_settings
from app.ports.registry import registry
from app.services import audit as audit_svc
from app.services.analysis_llm import Analysis, Result


# --- run id: entity + date/time --------------------------------------------
def test_make_run_id_shape_and_uniqueness():
    a = audit_svc.make_run_id("Reliance Industries Ltd")
    b = audit_svc.make_run_id("Reliance Industries Ltd")
    assert re.match(r"^reliance-industries-ltd-\d{8}-\d{6}(-\d+)?$", a), a
    assert a != b  # same-second collisions get a suffix
    # non-alphanumeric entities still yield a usable slug
    assert audit_svc.make_run_id("  ").startswith("entity-")


# --- OpenAI-compatible adapter is selectable + builds the right request -----
def test_openai_provider_registered_and_builds_chat_body():
    assert "openai" in registry.available("llm")
    assert "openai_compatible" in registry.available("llm")

    from app.adapters.openai_llm import OpenAiLlmProvider
    from app.config import LlmSettings, Settings

    s = Settings()
    s.llm = LlmSettings(provider="openai", model="moonshotai/kimi-k3-free",
                        base_url="https://api.tokenrouter.com/v1")
    p = OpenAiLlmProvider(s)
    assert p._endpoint() == "https://api.tokenrouter.com/v1/chat/completions"

    body = p.build_body(system="SYS", messages=[{"role": "user", "content": "hi"}],
                        response_schema=Result, temperature=0.0, max_tokens=256)
    assert body["model"] == "moonshotai/kimi-k3-free"
    assert body["messages"][0]["role"] == "system"
    assert "JSON Schema" in body["messages"][0]["content"]  # schema instruction embedded
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    assert body["response_format"] == {"type": "json_object"}


def test_openai_provider_reads_the_completion_ceiling_from_gateway_errors():
    from app.adapters.openai_llm import _completion_limit_from_error

    message = ("max_tokens is too large: 4096000. This model supports at most "
               "128000 completion tokens, whereas you provided 4096000.")
    assert _completion_limit_from_error(message) == 128000
    assert _completion_limit_from_error("some other request error") is None


def test_mapping_json_does_not_request_reasoning_tokens(monkeypatch):
    """A structured mapping reply must reserve its small completion budget for JSON, not hidden
    reasoning.

    The schema is `line_item_llm.LineItemReply` — one citation list per line item — where it used
    to be the row batch's `LlmBatchDecision`. Same property, and it matters for the same measured
    reason: a model whose reasoning cannot be disabled spends the completion budget thinking and
    returns empty content with finish_reason=length.
    """
    from app.adapters.openai_llm import OpenAiLlmProvider
    from app.config import LlmSettings, Settings
    from app.services.line_item_llm import LineItemReply

    settings = Settings()
    settings.llm = LlmSettings(provider="openai", model="reasoning-model",
                               reasoning_effort="medium")
    provider = OpenAiLlmProvider(settings)
    body = provider.build_body(system="SYS", messages=[{"role": "user", "content": "hi"}],
                               response_schema=LineItemReply, temperature=0.0, max_tokens=1456)

    assert "reasoning_effort" not in body

    # THE CONTROL, and it is what proves the flag is not over-broad. A FREE-FORM call — gap
    # routing, the commentary, the contingent-liabilities narrative — is judgement written in
    # prose, and reasoning is exactly what it should be asking for. If the withholding leaked to
    # every structured schema, those would silently get worse and nothing here would notice.
    from app.services.gap_closing import OthersRoutingDecision

    free_form = provider.build_body(system="SYS", messages=[{"role": "user", "content": "hi"}],
                                    response_schema=OthersRoutingDecision, temperature=0.0,
                                    max_tokens=1456)
    assert free_form["reasoning_effort"] == "medium"
    assert not getattr(OthersRoutingDecision, "compact_json_reply", False)


# --- audit endpoint: seeded token usage, input/output separate --------------
def test_audit_endpoint_returns_seeded_token_usage(client):
    r = client.get("/api/v1/projects/demo/audit")
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert entries, "expected seeded audit entries"
    e = entries[0]
    for field in ("run_id", "entity", "action", "model", "input_tokens", "output_tokens", "total_tokens"):
        assert field in e
    analysis = next(x for x in entries if x["action"] == "analysis")
    assert analysis["input_tokens"] > 0 and analysis["output_tokens"] > 0
    assert analysis["total_tokens"] == analysis["input_tokens"] + analysis["output_tokens"]


# --- analysis run records real token usage to the audit log -----------------
def test_analysis_run_records_token_usage(client, monkeypatch):
    """POST /analysis with a stubbed provider records a run with input/output tokens."""
    provider_id = get_settings().llm.provider

    class _FakeProvider:
        id = "fake"

        def complete_structured(self, *, system, messages, response_schema, temperature=0.0, max_tokens=2048):
            result = Result(
                entity="Reliance Industries Ltd",
                mappings=[],
                analysis=Analysis(headline="h", ratios=[], revenue_growth_pct=7.0,
                                  profit_growth_pct=8.0, strengths=["s"], risks=["r"], caveats="c"),
            )
            return result, {"model": "moonshotai/kimi-k3-free", "input_tokens": 4321, "output_tokens": 654}

    # Point the configured provider id at the fake so no network call happens. Snapshot the
    # previous factory so we can RESTORE it — otherwise this fake leaks into the global
    # registry and later extraction tests would wrongly engage the LLM mapping path.
    prev_factory = registry._factories.get("llm", {}).get(provider_id)
    registry.register("llm", provider_id, _FakeProvider)
    audit_svc.clear("demo")
    try:
        r = client.post("/api/v1/projects/demo/analysis")
        assert r.status_code == 200, r.text
        entry = r.json()["entry"]
        assert entry["input_tokens"] == 4321 and entry["output_tokens"] == 654
        assert entry["total_tokens"] == 4975
        assert re.match(r"^reliance-industries-ltd-\d{8}-\d{6}", entry["run_id"])

        # It now shows on top of the audit log.
        top = client.get("/api/v1/projects/demo/audit").json()["entries"][0]
        assert top["run_id"] == entry["run_id"] and top["status"] == "succeeded"
    finally:
        audit_svc.clear("demo")
        if prev_factory is not None:
            registry.register("llm", provider_id, prev_factory)
        else:
            registry._factories.get("llm", {}).pop(provider_id, None)


def test_analysis_run_is_analyst_driven(anon_client, auth):
    """Running an LLM analysis is analyst-driven: analyst/reviewer/admin hold analysis:run;
    an unauthenticated caller is rejected (401)."""
    for role in ("analyst", "reviewer", "admin"):
        me = anon_client.get("/api/v1/me", headers=auth(role)).json()
        assert "analysis:run" in me["permissions"], role
    assert anon_client.post("/api/v1/projects/demo/analysis").status_code == 401
