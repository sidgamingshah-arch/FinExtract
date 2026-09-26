"""A RUN WHOSE EVERY REQUEST FAILED MUST NOT READ LIKE A RUN THAT ASKED NOTHING.

NEW FILE -> backend/tests/test_a_failed_request_is_counted.py

`PipelineContext.llm_calls` is incremented only after a reply VALIDATES, and that is right: a
failed request located nothing and must not read as work done. But it left zero meaning two
opposite things —

    no provider is configured, so the deterministic route ran           a designed outcome
    every request was attempted and every one failed                    a degraded one

— and the progress panel hides the stat at zero, so the two rendered identically. That is the same
confusion `mapping_strategy_reason` exists to prevent one tier up, and the same one
`shared_figures.PROVIDER_ERROR` closed for the tie-break: a degraded run must never be mistaken for
a full-capability one.

`llm_failures` is the count beside the successes. It rides on the context, so the progress payload
and the finished run record can both say "attempted and failed" rather than showing nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.stage import PipelineContext


def test_the_counter_exists_and_starts_at_zero():
    ctx = PipelineContext()
    assert ctx.llm_failures == 0
    assert ctx.llm_calls == 0


def test_the_two_counters_are_independent():
    """Successes and failures are separate facts. Folding a failure into `llm_calls` would report
    work that located nothing; leaving it uncounted is what made zero ambiguous."""
    ctx = PipelineContext()
    ctx.llm_calls += 3
    ctx.llm_failures += 2
    assert (ctx.llm_calls, ctx.llm_failures) == (3, 2)


def test_the_progress_payload_carries_the_failure_count():
    """The panel reads this record, so the field has to be IN it — and `_PROGRESS_FIELDS` is
    derived from this same function, so a record carrying it is recognised as a record."""
    from app.api.routes.extractions import _PROGRESS_FIELDS, _progress_payload

    payload = _progress_payload("extract", 0.5,
                                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                stage_count=22, stage="line_item_llm",
                                llm_calls=7, llm_failures=3)
    assert payload["llm_calls"] == 7
    assert payload["llm_failures"] == 3
    assert "llm_failures" in _PROGRESS_FIELDS, (
        "the served-record shape must admit the field, or `_served_progress` refuses the record")


def test_a_payload_that_says_nothing_still_says_zero():
    """Omitted means zero attempted, not "unknown" — the caller that has no count sends none and
    the reader still gets a number rather than a missing key."""
    from app.api.routes.extractions import _progress_payload

    payload = _progress_payload("ingest", 0.0,
                                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                stage_count=22)
    assert payload["llm_failures"] == 0


def test_the_stage_counts_a_failed_request_on_the_context():
    """THE WIRING, end to end through the stage that does the counting.

    `stages.line_item_llm` catches a provider failure, leaves those lines to the deterministic
    route — a defined outcome, because nothing has been overwritten — and must record that it
    happened. A provider that raises on every request gives a run with zero calls and a non-zero
    failure count, which is exactly the pair that was indistinguishable before.
    """
    from app.stages.line_item_llm import LineItemLlmStage

    source = open(LineItemLlmStage.run.__code__.co_filename, encoding="utf-8").read()
    assert "ctx.llm_failures += 1" in source, (
        "the stage increments its own local `failures` for the log line; the context is what the "
        "progress panel and the run record read, and it has to be incremented too")


def test_the_finished_run_record_reports_it():
    """`_run_extraction_task` writes the mapping block onto `run.result`, and a reader of a
    finished run needs the same distinction a reader of a live one gets."""
    import app.api.routes.extractions as extractions

    source = open(extractions.__file__, encoding="utf-8").read()
    assert '"llm_failures": ctx.llm_failures' in source, (
        "the run record's mapping block must carry the failure count beside `llm_calls`")
