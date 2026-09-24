"""A FAILED TIE-BREAK MUST NOT READ AS A DELIBERATE 'KEEP BOTH'.

NEW FILE -> backend/tests/test_a_failed_tiebreak_says_so.py

`shared_figures` finds every printed cell more than one line item claims and asks the model which
line keeps it. "both" leaves the document untouched, and that is the right answer to a call that did
not answer — nothing should be deleted on a failure.

WHAT WAS WRONG WAS THE RECORD, not the decision. `resolve` swallowed every provider failure into
`return "both", "", 0.0` — no exception type, no message, nothing logged — and the stage then
flagged the rows `shared_figure_kept_on_both` and printed

    shared_figures:1000 on bs_ca__inventories|bs_ca__other_current_assets kept on both
    (openai_compatible: )

which is exactly what a considered judgement prints, minus a rationale nobody reads as significant.
So a duplicated figure that survived because the CALL FAILED could not be told from one the model
chose to leave — and that is the difference between "the configuration is wrong" and "the provider
is down", which is the first thing anyone chasing a duplicate needs to know.

This is the same conflation the mapper's `mapping_strategy_reason` exists to prevent one level up:
a degraded run must never be mistaken for a full-capability one.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.shared_figures import (PROVIDER_ERROR, Collision, SharedFigureDecision, resolve)

KEYS = ("bs_ca__inventories", "bs_ca__other_current_assets")


@pytest.fixture
def contest() -> Collision:
    """One printed cell, two unrelated lines — a real contest rather than a parent and its child."""
    return Collision(
        basis="consolidated", period="current", amount=Decimal("1000"), page_index=7,
        caption="Inventories", note_number="",
        claimants=[(KEYS[0], "Inventories", "", 0, "v"),
                   (KEYS[1], "Other Current Assets", "", 1, "v")])


class _Unreachable:
    def complete_structured(self, **_):
        raise RuntimeError("Gateway returned 400: json_validate_failed")


class _SaysBoth:
    def complete_structured(self, **_):
        return SharedFigureDecision(keep="both",
                                    rationale="both lines legitimately report this figure",
                                    confidence=0.8), {}


class _SaysOne:
    def complete_structured(self, **_):
        return SharedFigureDecision(keep=KEYS[0], rationale="the inventories note states it",
                                    confidence=0.9), {}


def test_a_provider_failure_keeps_both_and_names_itself(contest):
    """The decision is unchanged; the reason now travels with it."""
    keep, rationale, confidence = resolve(_Unreachable(), contest, {})
    assert keep == "both", "a failed call must never delete a figure"
    assert rationale.startswith(PROVIDER_ERROR), rationale
    assert "RuntimeError" in rationale and "json_validate_failed" in rationale, rationale
    assert confidence == 0.0


def test_a_considered_keep_both_is_not_marked_as_a_failure(contest):
    """THE CONTROL. If both outcomes carried the marker the distinction would be worthless."""
    keep, rationale, confidence = resolve(_SaysBoth(), contest, {})
    assert keep == "both"
    assert not rationale.startswith(PROVIDER_ERROR), rationale
    assert confidence == 0.8


def test_a_decided_contest_still_names_one_line(contest):
    """THE OTHER CONTROL: naming the failure must not stop the tie-break working."""
    keep, rationale, confidence = resolve(_SaysOne(), contest, {})
    assert keep == KEYS[0]
    assert not rationale.startswith(PROVIDER_ERROR)
    assert confidence == 0.9


def test_the_marker_is_one_constant_both_sides_read():
    """`stages.shared_figures` decides how to log and flag by testing this prefix. A prefix the
    service and the stage spell differently is a distinction that silently stops being drawn, which
    is why it is a constant and not a literal in two files."""
    import app.stages.shared_figures as stage_module

    source = (stage_module.__file__ or "")
    assert source
    text = open(source, encoding="utf-8").read()
    assert "PROVIDER_ERROR" in text, (
        "the stage must read the marker from the service rather than re-spelling it")
    assert 'startswith(PROVIDER_ERROR)' in text
