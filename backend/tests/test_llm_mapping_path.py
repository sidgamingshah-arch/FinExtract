"""WHAT A RUN REPORTS ABOUT ITS OWN MAPPING STRATEGY.

CONCEPT MAPPING IS DETERMINISTIC, and the strategy the run record names has to say so. Which
concept a printed caption is, is decided by exact normalised alias and the rule tier's authored
hints — no provider is consulted, whatever is configured. `stages.map_ontology` therefore reports
`deterministic` unconditionally, which is a change: it used to report `llm_description` whenever a
provider merely CONSTRUCTED, a claim about the stage that was never the same question as whether
the provider decided anything.

WHAT THIS FILE USED TO COVER, and where it went. Five tests exercised the row request with a fake
provider — a configured provider is consulted, its decision wins, the statement constraint narrows
what it is offered, an exact alias is still refined by it, and a batch respects the statement.
Every one of them is about a call that asked "which concept is this printed row?", and no such
call is made. The requests a run does make are about LINE ITEMS, and their wiring is
`tests/test_line_item_requests_run.py`.

WHAT REMAINS HERE is the half that was always about the RECORD rather than the call: a run says
which strategy it used, and it says why when a provider was configured and decided nothing.
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
