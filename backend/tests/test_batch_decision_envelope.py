"""A model that echoes the schema's own envelope must not cost a whole chunk of decisions.

THE DEFECT THIS CLOSES, measured on the 四创电子 filing. Structured output here is obtained
model-agnostically: the response model's JSON Schema goes into the system prompt and the reply is
validated with Pydantic. `LlmBatchDecision.mappings` is declared `list[LlmBatchItem]`, so its
schema node reads `{"type": "array", "items": {...}}` — and one call in six came back having
reproduced that NODE instead of the array it describes:

    {"mappings": {"items": [{"item_id": "...", "canonical_key": "...", ...}]}}

    ValidationError: mappings — Input should be a valid array [type=list_type]

Every decision was present and correct. Only the wrapper was wrong, and it discarded a chunk of
six captions. The chunk then fell back per line, so the loss was SILENT: the run reported
`strategy: "llm_description"` with six successful calls while a sixth of its decisions had been
thrown away on an envelope.

WHY AT THE SCHEMA AND NOT IN `extract_json`. `extract_json` repairs TRANSPORT — fences, prose
around the object, trailing commentary. This is not malformed JSON; it is valid JSON of the wrong
shape for one field, and the field is the only thing that knows what shape it wanted.

THE UNWRAP MUST STAY NARROW. It never invents an entry: an envelope with no list inside is still
invalid, so a genuinely broken reply still raises rather than silently mapping nothing.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.mapping import LlmBatchDecision

_ITEM = {"item_id": "a", "canonical_key": "bs_ca__inventories", "confidence": 0.9}


# ── the shape that cost six decisions ────────────────────────────────────────────────────────────

def test_the_schema_items_envelope_is_unwrapped():
    """The exact payload from the run."""
    decision = LlmBatchDecision.model_validate({"mappings": {"items": [_ITEM]}})

    assert len(decision.mappings) == 1
    assert decision.mappings[0].canonical_key == "bs_ca__inventories"


def test_the_full_schema_node_is_unwrapped():
    """Some models return the whole node, `type` and all."""
    decision = LlmBatchDecision.model_validate(
        {"mappings": {"type": "array", "items": [_ITEM, dict(_ITEM, item_id="b")]}})

    assert [m.item_id for m in decision.mappings] == ["a", "b"]


def test_a_doubly_nested_mappings_key_is_unwrapped():
    """`{"mappings": {"mappings": [...]}}` — the same mistake with the field's own name."""
    decision = LlmBatchDecision.model_validate({"mappings": {"mappings": [_ITEM]}})

    assert len(decision.mappings) == 1


# ── and the ordinary case is untouched ───────────────────────────────────────────────────────────

def test_a_correct_reply_still_validates():
    decision = LlmBatchDecision.model_validate({"mappings": [_ITEM]})

    assert len(decision.mappings) == 1


def test_an_absent_field_is_still_an_empty_list():
    assert LlmBatchDecision.model_validate({}).mappings == []


def test_an_empty_array_is_still_empty():
    """"None of these captions map" is a licensed answer and must not be confused with a failure."""
    assert LlmBatchDecision.model_validate({"mappings": []}).mappings == []


# ── the unwrap refuses rather than inventing ─────────────────────────────────────────────────────

@pytest.mark.parametrize("broken", [
    {"mappings": {"items": "not a list"}},
    {"mappings": {"type": "array"}},              # an envelope with nothing in it
    {"mappings": {"item_id": "a"}},               # a single item where a list was asked for
    {"mappings": "bs_ca__inventories"},
    {"mappings": 7},
])
def test_a_reply_that_is_not_an_envelope_still_raises(broken):
    """A default may refuse; it may not silently assert.

    Turning any of these into an empty list would report "the model mapped nothing" — a claim the
    reply does not support — and the run would look like a successful call that found no concepts.
    """
    with pytest.raises(ValidationError):
        LlmBatchDecision.model_validate(broken)


def test_the_unwrap_does_not_swallow_an_invalid_item():
    """Unwrapping fixes the envelope, not the contents."""
    with pytest.raises(ValidationError):
        LlmBatchDecision.model_validate(
            {"mappings": {"items": [{"item_id": "a", "confidence": "not a number"}]}})
