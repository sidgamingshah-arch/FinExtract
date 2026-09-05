"""The LLM mapping path, exercised with a fake provider.

Mapping by MEANING is the intended primary strategy; the deterministic ensemble is the
fallback when no provider is configured. Without an API key in CI we cannot measure the real
model's judgement, so these tests prove the *wiring* instead: that a configured provider is
actually consulted, that its decision wins, that the statement constraint still applies to
what it is offered, and — importantly — that a run WITHOUT a provider is recorded as the
weaker strategy rather than passing silently for a full-capability run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
ONTOLOGY = json.loads((_DIR / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


class FakeLlm:
    """Records what it was asked and answers with a fixed canonical key."""

    id = "fake"

    def __init__(self, key: str, confidence: float = 0.93):
        self.key = key
        self.confidence = confidence
        self.calls: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema,
                            temperature=0.0, max_tokens=2048):
        payload = json.loads(messages[-1]["content"])
        self.calls.append(payload)
        # Answer in whichever shape the caller asked for (single decision vs batch).
        fields = response_schema.model_fields
        if "mappings" in fields:
            items = payload.get("source_items", [])
            obj = response_schema(mappings=[
                {"item_id": it["item_id"], "canonical_key": self.key,
                 "confidence": self.confidence, "allocation_status": "direct_exclusive"}
                for it in items
            ])
        else:
            obj = response_schema(canonical_key=self.key, confidence=self.confidence,
                                  allocation_status="direct_exclusive", reason="fake")
        return obj, {"input_tokens": 11, "output_tokens": 7, "model": "fake-model"}


def _matcher(provider):
    from app.config import get_settings
    from app.schemas.loader import load_ontology
    from app.services.mapping import OntologyMatcher

    return OntologyMatcher(load_ontology(ONTOLOGY), locale="en",
                           settings=get_settings(), llm_provider=provider)


def test_a_configured_provider_is_consulted_and_decides():
    llm = FakeLlm("pl_income__other_income")
    m = _matcher(llm)
    # A caption no alias matches, so only the LLM can resolve it.
    res = m.match("Sundry receipts not otherwise classified", statement="profit_and_loss")
    assert llm.calls, "the provider should have been consulted"
    assert res.canonical_key == "pl_income__other_income"
    assert res.method.value == "llm"


def test_the_llm_is_only_offered_concepts_from_the_caption_s_statement():
    """The statement constraint must apply to the candidate list too — otherwise the model
    can place a P&L caption in the cash-flow statement however good its reasoning is."""
    llm = FakeLlm("pl_income__other_income")
    m = _matcher(llm)
    m.match("Some unmatched caption", statement="profit_and_loss")
    offered = llm.calls[0]["candidates"]
    keys = [c["canonical_key"] if isinstance(c, dict) else c for c in offered]
    assert keys, "candidates should be offered"
    assert all(k.startswith("pl_") for k in keys), f"non-P&L concepts offered: {keys[:5]}"


def test_usage_is_reported_for_the_audit_log():
    llm = FakeLlm("pl_income__other_income")
    m = _matcher(llm)
    m.match("Another unmatched caption", statement="profit_and_loss")
    assert m.usage["calls"] >= 1
    assert m.usage["input_tokens"] >= 1 and m.usage["output_tokens"] >= 1
    assert m.usage["model"] == "fake-model"


def test_exact_alias_is_still_refined_by_the_llm():
    """A deterministic exact hit is evidence, not a reason to bypass semantic refinement."""
    llm = FakeLlm("pl_income__other_income")
    m = _matcher(llm)
    res = m.match("REVENUE 收益", statement="profit_and_loss")
    assert res.canonical_key == "pl_income__other_income"
    assert res.method.value == "llm"
    assert llm.calls


def test_batch_mapping_uses_the_provider_and_respects_the_statement():
    llm = FakeLlm("cf_cash_flow_from_operating_activities__interest_income")
    m = _matcher(llm)
    out = m.match_batch([("a", "Unmatched caption one"), ("b", "Unmatched caption two")],
                        statement="cash_flow")
    assert set(out) == {"a", "b"}
    keys = [c["canonical_key"] if isinstance(c, dict) else c
            for c in llm.calls[0]["candidates"]]
    assert all(k.startswith("cf_") for k in keys)


def test_without_a_provider_the_matcher_reports_deterministic():
    m = _matcher(None)
    assert m.llm_enabled is False
    # And it still maps what it can, by alias.
    assert m.match("REVENUE 收益", statement="profit_and_loss").canonical_key


def test_a_run_records_which_strategy_it_used(monkeypatch):
    """A keyless run must be visibly deterministic — the whole point of surfacing this."""
    from app.core.stage import PipelineContext
    from app.stages.map_ontology import MapOntologyStage

    ctx = PipelineContext()
    monkeypatch.setattr(ctx.settings.llm, "provider", "stub", raising=False)

    from app.core.models import DocumentModel
    from app.schemas.loader import load_ontology

    doc = DocumentModel(filename="f.pdf")
    ctx.ontology = load_ontology(ONTOLOGY)      # type: ignore[attr-defined]
    MapOntologyStage().run(doc, ctx)
    # No line items → the stage short-circuits; the strategy fields must still be safe to read.
    assert ctx.mapping_strategy in ("", "deterministic")


@pytest.mark.parametrize("provider,expect", [("stub", "deterministic")])
def test_strategy_reason_explains_a_degraded_run(monkeypatch, provider, expect):
    from app.core.models import DocumentModel
    from app.core.models.line_item import LineItem
    from app.core.stage import PipelineContext
    from app.schemas.loader import load_ontology
    from app.stages.map_ontology import MapOntologyStage

    ctx = PipelineContext()
    monkeypatch.setattr(ctx.settings.llm, "provider", provider, raising=False)
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [LineItem(source_label="REVENUE 收益")]
    ctx.ontology = load_ontology(ONTOLOGY)      # type: ignore[attr-defined]
    MapOntologyStage().run(doc, ctx)
    assert ctx.mapping_strategy == expect
    assert ctx.mapping_strategy_reason, "a degraded run must say WHY it was degraded"
