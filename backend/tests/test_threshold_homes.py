"""The per-deployment thresholds lifted out of services/mapping.py, pinned to the literals.

A threshold has three homes — protective CODE, the rulebook SET that calibrated it, or
per-deployment SETTINGS — and this file guards the SETTINGS half of that split for the payload
and provider caps in `services.mapping`. Two different failures are possible while a literal is
being replaced by a setting, and both are silent:

* the default and the literal differ, so declaring nothing in config.toml still changes how the
  pipeline calls the provider (a smaller batch reply is truncated JSON, which is not a partial
  answer — the chunk falls back to the weaker per-line path and the run still calls itself
  LLM-mapped);
* the setting is declared and read nowhere, so the file reports a control that does nothing.

So each cap is asserted from the OUTSIDE — the chunk sizes a batch actually cuts, the
`max_tokens` a call actually asks for, the number of examples that actually reach the system
prompt. That holds before the migration (mapping.py's literal) and after it (mapping.py reading
the setting), and fails if the number moves in between. The matchers are built from a FRESH
`Settings()` rather than `get_settings()`, because the admin settings screen mutates the cached
object in place (services.settings_state), and a value another test left there would otherwise
read as a migration failure.

Also recorded here: the caps deliberately NOT exposed, with the measurement that decided it. A
per-rulebook number offered as a deployment knob is as wrong as a literal left in code, and a
knob whose slice can never truncate is worse than either.
"""
from __future__ import annotations

import json
import pathlib
from uuid import uuid4

import pytest

from app.config import ExtractionSettings, Settings
from app.schemas.loader import load_ontology
from app.services.mapping import (LlmBatchDecision, LlmBatchItem, LlmMappingDecision,
                                  OntologyMatcher)

TEMPLATES = pathlib.Path("app/sample/templates")

# The five settings this package added, each against the mapping.py literal it replaces.
SHADOWED_LITERALS = {
    "llm_batch_max_items": 25,              # OntologyMatcher.BATCH_MAX_ITEMS
    "llm_batch_response_floor_tokens": 8192,  # max(8192, …) in _effective_batch_max_tokens
    "llm_line_max_tokens": 512,             # max_tokens=512 in _llm
    "llm_worked_examples_cap": 6,           # ontology.worked_examples[:6] in _build_system
    "llm_example_aliases_cap": 4,           # aliases_for(locale)[:4] in _concept_payload
}


def _default(name: str):
    """The shipped default, not the live value — that is what must equal the literal."""
    return ExtractionSettings.model_fields[name].default


class Spy:
    """Answers every structured call and records the response allocation each one asked for.

    Batch requests are recorded separately from per-line ones: a batch that resolves nothing
    falls back per line THROUGH THE SAME PROVIDER, so one list would mix the two budgets and the
    per-line assertion would be reading a batch cap.
    """

    id = "spy"

    def __init__(self) -> None:
        self.line_caps: list[int] = []
        self.batch_caps: list[int] = []
        self.batch_payloads: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        payload = json.loads(messages[-1]["content"])
        meta = {"model": "spy", "input_tokens": 1, "output_tokens": 1}
        if response_schema is LlmBatchDecision:
            self.batch_caps.append(max_tokens)
            self.batch_payloads.append(payload)
            return LlmBatchDecision(mappings=[
                LlmBatchItem(item_id=i["item_id"], canonical_key="", confidence=0.0)
                for i in payload["source_items"]]), meta
        self.line_caps.append(max_tokens)
        return LlmMappingDecision(canonical_key="", confidence=0.0), meta


def _ontology(name: str):
    return load_ontology(
        json.loads((TEMPLATES / name).read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def rulebook():
    """The rulebook the pipeline actually runs on (462 concepts)."""
    return _ontology("output_csv_hk_ontology.json")


@pytest.fixture(scope="module")
def authored_examples():
    """The one shipped rulebook that authors more worked examples than the cap admits: 8."""
    return _ontology("hkfrs_hk_china_ontology.json")


def _matcher(ontology, provider=None):
    return OntologyMatcher(ontology, settings=Settings(), llm_provider=provider)


# --- the shipped config must not already move them, or the tests below prove nothing ------------

def test_nothing_in_the_config_file_moves_these_off_their_defaults():
    """The behavioural assertions below compare mapping.py against the DEFAULT. That is only the
    same thing as comparing it against the shipped configuration while config.toml declares none
    of these — which is deliberate: a provider limit belongs in the deployment that has that
    provider, not checked in for every clone."""
    live = Settings().extraction
    for name, literal in SHADOWED_LITERALS.items():
        assert _default(name) == literal, f"{name} default drifted from the mapping.py literal"
        assert getattr(live, name) == literal, f"config.toml moved {name} away from the literal"


# --- transport size and response allocation -----------------------------------------------------

def test_the_batch_chunk_size_default_is_the_size_a_batch_is_actually_cut_into(rulebook):
    """A 170-caption statement, cut. The assertion is on the SIZES the provider saw, so it reads
    the same before and after `BATCH_MAX_ITEMS` becomes `extraction.llm_batch_max_items`."""
    cap = _default("llm_batch_max_items")
    spy = Spy()
    m = _matcher(rulebook, spy)
    items = [(str(uuid4()), f"Caption {i}") for i in range(170)]
    m.match_batch(items, statement="balance_sheet")

    sizes = [len(p["source_items"]) for p in spy.batch_payloads]
    assert sizes == [cap] * (170 // cap) + [170 % cap], sizes
    assert m.usage["batch_max_items"] == cap


def test_the_batch_response_floor_default_is_the_allocation_a_batch_asks_for(rulebook):
    """The floor, not the envelope derivation, is the batch budget in practice: derived(25) is
    2,256 tokens and the floor wins for every chunk up to 99 items (crossover at 100). So a
    default that did not equal the floor would change the allocation of EVERY batch call this
    pipeline makes, at any chunk size it uses."""
    floor = _default("llm_batch_response_floor_tokens")
    spy = Spy()
    m = _matcher(rulebook, spy)
    m.match_batch([(str(uuid4()), f"Caption {i}") for i in range(60)],
                  statement="balance_sheet")

    assert spy.batch_caps and set(spy.batch_caps) == {floor}, spy.batch_caps
    assert m._effective_batch_max_tokens(1) == floor
    assert m._effective_batch_max_tokens(_default("llm_batch_max_items")) == floor
    # …and the floor is a floor, not a ceiling: a chunk big enough to need more still gets more,
    # which is the half of `_effective_batch_max_tokens` that stays in code.
    assert m._effective_batch_max_tokens(400) > floor


def test_the_per_line_response_default_is_the_allocation_the_per_line_call_asks_for(rulebook):
    cap = _default("llm_line_max_tokens")
    spy = Spy()
    m = _matcher(rulebook, spy)
    m.match("A caption no alias in the rulebook claims")

    assert spy.line_caps == [cap], spy.line_caps
    assert not spy.batch_caps, "this must measure the per-line call, not a batch"


# --- prompt payload caps ------------------------------------------------------------------------

def test_the_worked_examples_cap_default_is_how_many_reach_the_system_prompt(authored_examples):
    """Measured on the rulebook where this bites: 8 authored, 6 admitted. The two dropped are
    1,023 characters — 16% of that ontology's system prompt — so the default has to be the cap and
    not the authored length, or the migration would change what the model is told."""
    cap = _default("llm_worked_examples_cap")
    assert len(authored_examples.worked_examples) > cap, "this rulebook no longer exercises the cap"

    system = _matcher(authored_examples)._build_system()
    tail = system.split("Worked examples:", 1)[1]
    assert len([ln for ln in tail.splitlines() if ln.startswith("- ")]) == cap


def test_the_example_aliases_cap_default_is_how_many_aliases_reach_a_candidate(rulebook):
    """137 of the 462 concepts author more than 4 aliases (median 3, longest 23), so the cap
    bounds the tail. Read off the payload the model is actually sent."""
    cap = _default("llm_example_aliases_cap")
    m = _matcher(rulebook)
    keys = m._by_priority([k for k in m._mappable_keys() if m._in_statement(k, "balance_sheet")])
    payload = m._concept_payload(keys)

    lengths = [len(entry["example_aliases"]) for entry in payload]
    assert max(lengths) == cap, max(lengths)
    truncated = sum(1 for k in keys
                    if len(m._by_key[k].aliases_for(m.locale)) > cap)
    assert truncated, "no candidate's alias list is truncated, so the cap is unmeasured here"


# --- the caps that were judged NOT to be settings, and why --------------------------------------

def test_the_review_shortlist_caps_can_never_truncate_so_they_are_not_knobs(rulebook):
    """`ranked[:5]`, `ranked[:4]`, `primary[:5]` and the batch payload's
    `deterministic_candidates[:3]` were left in code, because none of them can cut anything.

    The deterministic pool holds at most two candidates — one exact-alias hit and one rule hit,
    there being no string-similarity tier any more (see `extraction.evidence_floor`) — so `ranked`
    is at most 2 long and every one of those slices is the whole list. Measured over 22,740 probes
    (every label and alias of the shipped rulebook, under its own section and under every other
    section of its statement) `len(MappingResult.candidates)` was only ever 0 or 1. Exposing them
    would advertise four deployment knobs that cannot change an answer; the cheap corpus below is
    the standing guard, and it starts failing the day a tier is added that can propose a third
    candidate — at which point the slice becomes a real decision and needs a real home.
    """
    m = _matcher(rulebook)
    seen = set()
    for concept in rulebook.mappings:
        for label in [concept.label, *(concept.aliases or [])]:
            if not label:
                continue
            result = m.match(label, statement=concept.statement)
            seen.add(len(result.candidates))
    assert seen and max(seen) <= 2, seen


def test_the_batch_envelope_slope_is_calibrated_against_this_vocabulary_not_the_deployment(
        rulebook):
    """`_BATCH_RESPONSE_TOKENS_PER_ITEM = 80` and `_BATCH_RESPONSE_RESERVE = 256` stayed in code.

    80 is not a provider number: it is the cost of one serialised decision, and what makes a
    decision long is the CANONICAL KEY in it, which is rulebook content. mapping.py records the
    measurement as "this file's longest canonical_key (101 chars)"; the rulebook the pipeline runs
    on today has a longest key of 72, so the constant is already calibrated against a vocabulary
    that is not this one — exactly the coupling that a deployment knob would hide instead of fix.
    Asserted as an inequality, since the honest fix is to derive it from the loaded set.
    """
    longest = max((c.canonical_key for c in rulebook.mappings), key=len)
    envelope = LlmBatchDecision(mappings=[
        LlmBatchItem(item_id=str(uuid4()), canonical_key=longest, confidence=0.95,
                     allocation_status="parent_gross_evidence_only")
        for _ in range(_default("llm_batch_max_items"))]).model_dump_json()

    # ~3 characters per token for JSON of UUIDs and long snake_case identifiers.
    assert len(envelope) / 3 <= OntologyMatcher._batch_max_tokens(_default("llm_batch_max_items"))
    assert len(longest) < 101, (
        "the recorded 101-char measurement no longer describes this rulebook")
