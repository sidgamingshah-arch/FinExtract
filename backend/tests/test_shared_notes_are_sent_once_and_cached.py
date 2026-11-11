"""`extraction.llm_shared_notes`: the notes many requests share are sent once, cached.

A request's question carries the notes its lines selected, and no provider caches a question, so a
note ten requests needed was paid for ten times — measured on the five reference filings, 628k of
the 852k estimated tokens of notes sent were a note some other request had already carried. Sending
EVERY note once in front of every request cost more on four of the five, so the decision is per
note: a note goes in the shared block when more requests carry it than the block would cost it
(`line_item_llm.shared_note_ids`).

What is pinned: the rule; the block's bytes; what a request then sends; that a line may still cite
only the notes it selected, so a shared note in front of a request whose lines did not select it
is refused; and that nothing changes where the provider caches nothing, or with the switch off.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel, NotesTable
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem
from app.schemas.line_items import load_line_item_set
from app.services import line_item_llm


# ── THE RULE ─────────────────────────────────────────────────────────────────────────────────

def test_a_note_is_shared_when_more_requests_carry_it_than_the_cached_block_would_cost_it():
    # 30 requests carry a note: the bar is 1.25 + 0.1 x 29 = 4.15 requests.
    sets = [{"7"} for _ in range(5)] + [{"9"} for _ in range(4)] + [{"x"} for _ in range(21)]
    assert line_item_llm.shared_note_ids(sets, write=1.25, read=0.1) == {"7", "x"}


def test_a_request_with_no_note_does_not_count_toward_the_bar():
    with_empty = [{"7"}, {"7"}, set(), set(), set(), set(), set(), set()]
    # Two requests carry a note: the bar is 1.25 + 0.1 = 1.35, and note 7 is carried twice.
    assert line_item_llm.shared_note_ids(with_empty, write=1.25, read=0.1) == {"7"}


def test_one_request_shares_nothing():
    assert line_item_llm.shared_note_ids([{"7", "8"}], write=1.25, read=0.1) == set()
    assert line_item_llm.shared_note_ids([], write=1.25, read=0.1) == set()


def test_a_dearer_read_shares_fewer_notes():
    sets = [{"7"} for _ in range(5)] + [set(), {"9"}]
    # Six carry a note. At a 0.1 read the bar is 1.75; at 0.9 it is 5.75, so note 7 (5) stays out.
    assert line_item_llm.shared_note_ids(sets, write=1.25, read=0.1) == {"7"}
    assert line_item_llm.shared_note_ids(sets, write=1.25, read=0.9) == set()


# ── THE BLOCK AND THE QUESTION ───────────────────────────────────────────────────────────────

IDENTIFIED = [{"note": "7", "title": "PPE", "rows": [{"caption": "Buildings"}]},
              {"note": "8", "title": "LEASES", "rows": [{"caption": "Right-of-use"}]},
              {"note": "9", "title": "TAX", "rows": [{"caption": "Current tax"}]}]


def test_the_block_is_one_lead_line_then_the_shared_notes_in_filing_order():
    text = line_item_llm.shared_notes_message(IDENTIFIED, {"8", "7"})
    lead, body = text.split("\n", 1)
    assert lead == line_item_llm.SHARED_NOTES_LEAD
    assert [n["note"] for n in json.loads(body)["notes"]] == ["7", "8"]
    assert " " not in body.split('"caption":')[0].replace("PPE", ""), "compact JSON"
    assert text == line_item_llm.shared_notes_message(IDENTIFIED, {"7", "8"}), "the same bytes"


def test_a_question_keeps_only_its_own_notes_in_its_own_order():
    request = {"line_items": [{"key": "a"}], "notes": IDENTIFIED, "statement_rows": [1]}
    out = line_item_llm.without_shared(request, {"7"})
    assert list(out) == ["line_items", "notes", "statement_rows"]
    assert [n["note"] for n in out["notes"]] == ["8", "9"]
    assert request["notes"] == IDENTIFIED, "the request it was given is left as it was"


def test_a_question_left_with_no_note_of_its_own_sends_no_notes_key():
    out = line_item_llm.without_shared({"line_items": [], "notes": IDENTIFIED[:1]}, {"7"})
    assert out == {"line_items": []}


# ── THE STAGE ────────────────────────────────────────────────────────────────────────────────
#
# Five note lines. Four select notes 7 AND 8, the fifth note 7 only — so both notes are carried by
# more requests than the bar (1.25 + 0.1 x 4 = 1.65) and both are shared, and the fifth line's
# request is sent note 8 in the block although its line did not select it. A sixth line selects
# note 9 alone, and its request is sent as before.

KEYS_78 = [f"sub__shared_{n}" for n in range(4)]
KEY_7 = "sub__seven_only"
KEY_9 = "sub__nine_only"


def _set():
    def line(key, patterns):
        return {"key": key, "label": key, "inherits": "n", "route": "note_tables",
                "definition": "a note figure", "type": "extracted",
                "note_source": {"note_title_any": patterns}}
    return load_line_item_set({
        "section_defaults": {"n": {"statement": "notes"}},
        "items": [*(line(k, [r"^PROPERTY", r"^LEASES"]) for k in KEYS_78),
                  line(KEY_7, [r"^PROPERTY"]), line(KEY_9, [r"^TAXATION"])]}, resolve=True)


def _note(number, title, caption, value):
    row = NoteItem(raw_label=caption)
    row.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(value), value_raw=Decimal(value))
    return NotesTable(note_number=number, title=title, items=[row])


def _doc():
    doc = DocumentModel(filename="ar.pdf")
    doc.notes = [_note("7", "PROPERTY, PLANT AND EQUIPMENT", "Buildings", "500"),
                 _note("8", "LEASES", "Right-of-use assets", "80"),
                 _note("9", "TAXATION", "Current tax", "30")]
    return doc


def _answer(key, note, caption):
    return {"key": key, "confidence": 0.9, "reason": "r",
            "sources": [{"note": note, "caption": caption}]}


ANSWERS = {**{k: _answer(k, "7", "Buildings") for k in KEYS_78},
           # THE FENCE: note 8 is in front of this line's request, and its line did not select it.
           KEY_7: _answer(KEY_7, "8", "Right-of-use assets"),
           KEY_9: _answer(KEY_9, "9", "Current tax")}


class _Caching:
    """Answers by key, records every call, and caches as the Claude adapters declare."""
    id = "caching"
    prompt_cache_prices = (1.25, 0.1)

    def __init__(self):
        self.calls: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        asked = [e["key"] for e in json.loads(messages[-1]["content"])["line_items"]]
        return response_schema.model_validate(
            {"answers": [ANSWERS[k] for k in asked if k in ANSWERS]}), {"input_tokens": 1}


class _NoCache(_Caching):
    id = "nocache"
    prompt_cache_prices = None


@pytest.fixture(autouse=True)
def _restore():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), ex.llm_shared_notes,
           ex.llm_request_grouping, ex.llm_parallel_requests, st.llm.provider)
    yield
    (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, ex.llm_shared_notes,
     ex.llm_request_grouping, ex.llm_parallel_requests, st.llm.provider) = was


def _run(provider, **extraction):
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage

    doc, st = _doc(), _set()
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items, ctx.ontology = st, build_working_view(st)
    ex = ctx.settings.extraction
    ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, False, []
    ex.llm_request_grouping, ex.llm_parallel_requests = "none", 1
    for name, value in extraction.items():
        setattr(ex, name, value)
    ctx.registry.register("llm", provider.id, lambda: provider)
    ctx.settings.llm.provider = provider.id
    LineItemLlmStage().run(doc, ctx)
    return doc, ctx


def _value(doc, key):
    li = next((li for li in doc.line_items if li.canonical_key == key), None)
    return next((v.value for v in li.values.values() if v.value is not None), None) if li else None


def _keys(call):
    return [e["key"] for e in json.loads(call["messages"][-1]["content"])["line_items"]]


def test_the_shared_notes_go_once_in_front_and_each_question_keeps_only_its_own():
    provider = _Caching()
    _doc_, ctx = _run(provider)
    by_line = {_keys(c)[0]: c for c in provider.calls}
    assert set(by_line) == {*KEYS_78, KEY_7, KEY_9}
    shared = [by_line[k] for k in (*KEYS_78, KEY_7)]
    (block,) = {json.dumps(c["messages"][0], sort_keys=True) for c in shared}
    first = json.loads(block)
    assert first["cache"] is True and first["content"].startswith(line_item_llm.SHARED_NOTES_LEAD)
    assert [n["note"] for n in json.loads(first["content"].split("\n", 1)[1])["notes"]] == ["7", "8"]
    for call in shared:
        assert len(call["messages"]) == 2
        assert "notes" not in json.loads(call["messages"][1]["content"]), "all its notes are shared"
        assert "cache" not in call["messages"][1]
    # Each line still says which notes are its own.
    supplied = {e["key"]: e["notes_supplied"] for c in shared
                for e in json.loads(c["messages"][1]["content"])["line_items"]}
    assert supplied[KEY_7] == ["7"] and all(supplied[k] == ["7", "8"] for k in KEYS_78)
    # THE NOTE NO OTHER REQUEST CARRIES STAYS IN ITS QUESTION, and that request is as it was.
    (alone,) = by_line[KEY_9]["messages"]
    assert [n["note"] for n in json.loads(alone["content"])["notes"]] == ["9"]
    # ONE SYSTEM TEXT FOR ALL OF THEM: the block's own lead line says what it is.
    assert len({c["system"] for c in provider.calls}) == 1
    assert any(line.startswith("line_item_llm:shared_notes notes=2 ") and "requests=5 of=6" in line
               for line in ctx.logs), ctx.logs


def test_a_line_may_still_cite_only_the_notes_it_selected():
    doc, ctx = _run(_Caching())
    assert all(_value(doc, k) == Decimal("500") for k in KEYS_78), "a shared note it selected"
    assert _value(doc, KEY_9) == Decimal("30"), "its own note, in its question"
    # Note 8 was in front of this line's request; its line selected note 7 only.
    assert _value(doc, KEY_7) is None
    assert any(KEY_7 in line and "note 8 was not supplied to this line" in line
               for line in ctx.logs), ctx.logs[-12:]


def test_the_figures_are_the_ones_the_requests_wrote_before():
    shared_doc, _ = _run(_Caching())
    plain_doc, _ = _run(_Caching(), llm_shared_notes=False)
    keys = [*KEYS_78, KEY_7, KEY_9]
    assert [_value(shared_doc, k) for k in keys] == [_value(plain_doc, k) for k in keys]


@pytest.mark.parametrize("provider, switch", [(_NoCache(), True), (_Caching(), False)])
def test_a_provider_that_caches_nothing_or_the_switch_off_sends_every_note_in_its_question(
        provider, switch):
    _run(provider, llm_shared_notes=switch)
    assert provider.calls and all(len(c["messages"]) == 1 for c in provider.calls)
    notes = {_keys(c)[0]: [n["note"] for n in json.loads(c["messages"][0]["content"])["notes"]]
             for c in provider.calls}
    assert notes[KEY_7] == ["7"] and notes[KEY_9] == ["9"]
    assert all(notes[k] == ["7", "8"] for k in KEYS_78)


def test_the_claude_adapters_declare_their_cache_prices_and_none_without_breakpoints():
    from app.adapters.anthropic_llm import AnthropicLlmProvider
    from app.adapters.bedrock_gateway_llm import BedrockGatewayLlmProvider
    settings = get_settings()
    was = settings.llm.prompt_cache
    try:
        settings.llm.prompt_cache = True
        assert BedrockGatewayLlmProvider(settings).prompt_cache_prices == (1.25, 0.1)
        assert AnthropicLlmProvider(settings).prompt_cache_prices == (1.25, 0.1)
        settings.llm.prompt_cache = False
        assert BedrockGatewayLlmProvider(settings).prompt_cache_prices is None
        assert AnthropicLlmProvider(settings).prompt_cache_prices is None
    finally:
        settings.llm.prompt_cache = was


def test_the_openai_format_adapters_declare_none():
    from app.adapters.azure_openai_llm import AzureOpenAiLlmProvider
    from app.adapters.openai_llm import OpenAiLlmProvider
    assert getattr(AzureOpenAiLlmProvider, "prompt_cache_prices", None) is None
    assert getattr(OpenAiLlmProvider, "prompt_cache_prices", None) is None
