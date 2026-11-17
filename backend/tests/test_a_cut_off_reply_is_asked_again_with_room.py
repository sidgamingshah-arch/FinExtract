"""A model reply cut off at its budget is asked for once more, with twice the room.

Measured on four CAS filings' run logs: every failed line-item request and every failed disclosure
reading was a reply that stopped part-way through its JSON — `Invalid JSON: EOF while parsing a
string at line 1 column 3460` — Chinese captions and page citations running past a budget sized
for English. The whole request failed and its lines fell to the deterministic route.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.ports.llm import ROOM_CEILING, complete_with_room, cut_off


class Reply(BaseModel):
    answers: list[str]


class Provider:
    """Answers with JSON cut off at `fits` tokens' worth, one character a token."""

    def __init__(self, full: str, fits: int | None = None, fail: Exception | None = None):
        self.full, self.fits, self.fail, self.calls = full, fits, fail, []

    def complete_structured(self, *, system, messages, response_schema, max_tokens,
                            temperature=0.0):
        self.calls.append(max_tokens)
        if self.fail is not None:
            raise self.fail
        text = self.full if len(self.full) <= max_tokens else self.full[:max_tokens]
        return response_schema.model_validate_json(text), {"output_tokens": len(text)}


FULL = '{"answers": ["' + "应收账款" * 300 + '"]}'


def _ask(provider, max_tokens):
    return complete_with_room(provider, system="s", messages=[{"role": "user", "content": "q"}],
                              response_schema=Reply, max_tokens=max_tokens)


def test_a_reply_that_fits_is_asked_for_once():
    provider = Provider(FULL)
    reply, meta = _ask(provider, 2_000)
    assert provider.calls == [2_000] and reply.answers and "retried_for_room" not in meta


def test_a_cut_off_reply_is_asked_again_with_twice_the_room():
    provider = Provider(FULL)
    reply, meta = _ask(provider, 1_000)
    assert provider.calls == [1_000, 2_000]
    assert reply.answers[0].startswith("应收账款")
    assert meta["retried_for_room"] is True
    # both replies were written, and both are counted
    assert meta["output_tokens"] == len(FULL) + 1_000


def test_only_once():
    provider = Provider(FULL)
    with pytest.raises(Exception) as caught:
        _ask(provider, 300)
    assert provider.calls == [300, 600] and cut_off(caught.value)


def test_any_other_failure_is_raised_at_once():
    provider = Provider(FULL, fail=TimeoutError("gateway"))
    with pytest.raises(TimeoutError):
        _ask(provider, 1_000)
    assert provider.calls == [1_000]


def test_a_reply_that_is_wrong_rather_than_short_is_not_retried():
    provider = Provider('{"answer": "x"}')            # valid JSON, wrong shape
    with pytest.raises(Exception) as caught:
        _ask(provider, 1_000)
    assert provider.calls == [1_000] and not cut_off(caught.value)


def test_the_room_has_a_ceiling():
    provider = Provider('{"answers": ["' + "x" * (2 * ROOM_CEILING) + '"]}')
    with pytest.raises(Exception):
        _ask(provider, ROOM_CEILING)
    assert provider.calls == [ROOM_CEILING]


def test_both_readers_go_through_it():
    """The line-item request and the disclosure reading are the two calls that were cut off."""
    import inspect

    from app.services import disclosure_reading, line_item_llm

    assert "complete_with_room(" in inspect.getsource(line_item_llm.ask)
    assert "complete_with_room(" in inspect.getsource(disclosure_reading._read_one)
