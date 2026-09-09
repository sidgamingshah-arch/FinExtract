"""The context reaches a real batch request, on the shipped rulebook, and stays bounded.

The unit tests in `test_note_context.py` prove the selector picks the right notes. This file proves
the selector is WIRED — that a pool handed to `match_batch` actually shows up in the JSON the
provider receives. That is the defect class this codebase keeps finding (three payload caps declared
in configuration and read by nothing), and it is invisible from either side alone.
"""
from __future__ import annotations

import json
import pathlib

from app.config import get_settings
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.note_context import ContextPool, ContextUnit
from app.services.working_view import build_working_view

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


class _Capture:
    """Records the request and refuses to answer, so the test observes the payload only."""

    id = "capture"

    def __init__(self) -> None:
        self.system = ""
        self.user = ""

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.system, self.user = system, messages[-1]["content"]
        raise RuntimeError("captured")


def _pool() -> ContextPool:
    return ContextPool([
        ContextUnit(kind="note", ref="12", title="Trade and other receivables",
                    captions=("Trade receivables from third parties", "Less: loss allowance",
                              "Prepayments and deposits")),
        ContextUnit(kind="note", ref="15", title="Share capital",
                    captions=("Ordinary shares issued and fully paid",)),
        ContextUnit(kind="face", ref="balance_sheet",
                    captions=("Trade receivables from third parties",),
                    amount="96200", row_id="other-row"),
    ])


def _request(rows, *, pool=None, cited=None, banner="Current assets"):
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    spy = _Capture()
    matcher = OntologyMatcher(build_working_view(st), locale="en",
                              settings=get_settings(), llm_provider=spy)
    items = [(f"r{i}", caption) for i, caption in enumerate(rows)]
    try:
        matcher.match_batch(items, statement="balance_sheet",
                            sections={iid: banner for iid, _ in items},
                            context_pool=pool, cited_notes=cited)
    except Exception:
        pass
    assert spy.user, "no request was built"
    return json.loads(spy.user), spy.system, items


def test_the_matching_note_and_face_row_arrive_on_the_source_item():
    payload, _system, _items = _request(["Trade and other receivables"], pool=_pool())
    item = payload["source_items"][0]
    context = item.get("context")
    assert context, "no context reached the request"
    assert {u["kind"] for u in context} == {"note", "face"}
    assert [u for u in context if u["kind"] == "note"][0]["ref"] == "12"
    # The amount is the whole reason a face row is offered — see `note_context`.
    assert [u for u in context if u["kind"] == "face"][0]["amount"] == "96200"


def test_the_unrelated_note_is_not_offered():
    payload, _system, _items = _request(["Trade and other receivables"], pool=_pool())
    refs = {u["ref"] for u in payload["source_items"][0]["context"]}
    assert "15" not in refs, refs


def test_a_cited_note_arrives_even_when_it_does_not_resemble_the_caption():
    payload, _system, items = _request(["Trade and other receivables"], pool=_pool(),
                                       cited={"r0": {"15"}})
    context = payload["source_items"][0]["context"]
    cited = [u for u in context if u.get("cited")]
    assert [u["ref"] for u in cited] == ["15"], context


def test_no_pool_means_no_context_key_rather_than_an_empty_one():
    """An absent pool must leave the payload shaped as it was, not add `context: []` to every row —
    an empty list reads to the model as 'the filing says nothing about this row', which is a claim
    the absence of a pool does not support."""
    payload, _system, _items = _request(["Trade and other receivables"], pool=None)
    assert all("context" not in item for item in payload["source_items"])


def test_a_row_in_the_batch_is_not_its_own_context():
    """Both rows are in `source_items` already; offering either back as context would be a request
    that corroborates itself."""
    pool = ContextPool([
        ContextUnit(kind="face", ref="balance_sheet", captions=("Inventories",),
                    amount="42100", row_id="r1"),
        ContextUnit(kind="note", ref="13", title="Inventories",
                    captions=("Raw materials", "Finished goods")),
    ])
    payload, _system, _items = _request(["Inventories", "Inventories"], pool=pool)
    for item in payload["source_items"]:
        for unit in item.get("context") or ():
            assert unit["kind"] != "face", item


def test_the_evidence_is_read_before_the_closed_list():
    """Key order in the JSON is what the model reads in order. `source_items` and their context
    come first and `candidates` last, because the question is what each row MEANS and the candidate
    list is only the vocabulary the answer has to be expressed in."""
    payload, _system, _items = _request(["Trade and other receivables"], pool=_pool())
    keys = list(payload)
    assert keys.index("source_items") < keys.index("candidates")
    assert keys[-1] == "candidates", keys


def test_the_context_respects_the_configured_character_budget():
    """The budget is the backstop that keeps a filing with forty long notes from overrunning the
    request. A knob nothing reads is the defect this file exists to catch, so it is asserted by
    turning it down and measuring the request, not by reading the code."""
    # A MIXED filing, not twenty copies of one note. Twenty notes on the same subject would give
    # every shared word a document frequency of twenty and therefore an IDF of zero — the selector
    # would correctly offer nothing, and the test would pass for the wrong reason.
    big = ContextPool(
        [ContextUnit(kind="note", ref=f"r{n}", title="Trade and other receivables",
                     captions=tuple(f"Trade receivables from third parties, tranche {n}-{i}"
                                    for i in range(8)))
         for n in range(8)]
        + [ContextUnit(kind="note", ref=f"x{n}", title="Employee benefit obligations",
                       captions=tuple(f"Actuarial valuation assumption {n}-{i}"
                                      for i in range(8)))
           for n in range(12)]
    )
    settings = get_settings()
    original = settings.extraction.llm_context_char_budget
    try:
        settings.extraction.llm_context_char_budget = 200
        payload, _system, _items = _request(["Trade and other receivables"], pool=big)
        spend = sum(len(json.dumps(u, ensure_ascii=False))
                    for u in payload["source_items"][0].get("context") or ())
        assert 0 < spend < 700, spend
    finally:
        settings.extraction.llm_context_char_budget = original


def test_context_does_not_push_the_request_past_the_response_budget():
    """The failure that made this path unusable before was a request that no longer fit. The
    context is bounded per row, so a full-size batch has to stay in the same order of magnitude as
    one without context — asserted as a ratio, because the absolute size depends on the rulebook."""
    rows = ["Trade and other receivables", "Inventories", "Cash and cash equivalents",
            "Property, plant and equipment", "Prepayments and deposits"]
    bare, system_bare, _ = _request(rows, pool=None)
    rich, system_rich, _ = _request(rows, pool=_pool())
    size_bare = len(json.dumps(bare, ensure_ascii=False)) + len(system_bare)
    size_rich = len(json.dumps(rich, ensure_ascii=False)) + len(system_rich)
    assert size_rich < size_bare * 1.5, (size_bare, size_rich)
