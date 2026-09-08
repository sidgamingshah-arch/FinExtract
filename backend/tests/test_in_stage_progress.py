"""Progress reported from INSIDE a stage — how many LLM calls are needed, and how many are done.

THE DEFECT THIS CLOSES. `Pipeline.run` emits once before each stage, so everything a reader sees
moves only at stage boundaries. `map_ontology` is ONE stage and by far the longest: it makes every
LLM call in the run, one batched call per (statement, section) subgroup, concurrently. For the
whole of that the stage name, the percentage, the stage counter AND the log tail sat frozen —
because the only thing that flushed them was the next stage starting. A run that was working and a
run that had hung looked identical, which is exactly what was reported from the field.

WHAT IS REPORTED NOW: planned provider calls, calls completed, and a live cumulative count.
Planned is counted in PROVIDER CALLS rather than subgroups, because `match_batch` chunks a
subgroup at `BATCH_MAX_ITEMS` — a 60-row subgroup is three calls, and reporting subgroups would
understate a large filing several-fold.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.routes.extractions import (_PROGRESS_FIELDS, _RunProgress, _progress_payload,
                                        _served_progress)
from app.core.stage import PipelineContext

STARTED = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ── the record carries the new fields, and the contract knows about them ─────────────────────────

def test_the_payload_carries_the_step_and_call_fields():
    got = _progress_payload("map_ontology", 0.5, started_at=STARTED, stage_count=14,
                            stage="map_ontology", step_done=3, step_total=12,
                            step_label="LLM call", llm_calls=3)

    assert got["step_done"] == 3
    assert got["step_total"] == 12
    assert got["step_label"] == "LLM call"
    assert got["llm_calls"] == 3


def test_the_field_contract_includes_them_so_a_record_is_not_rejected():
    """`_PROGRESS_FIELDS` is derived from the payload, so it extends automatically — asserted
    because `_served_progress` REFUSES a stored record missing any declared key, and a field added
    to the payload but not to the contract would make every new record unservable."""
    for field in ("step_done", "step_total", "step_label", "llm_calls"):
        assert field in _PROGRESS_FIELDS

    served = _served_progress(_progress_payload("map_ontology", 0.5, started_at=STARTED,
                                                stage_count=14, step_total=12, step_done=3))
    assert served is not None
    assert served["step_total"] == 12


def test_a_stage_that_reports_nothing_carries_zeroes_not_nulls():
    """0/0 is how a screen is told there is no sub-step detail. It must not read as 0% of 0."""
    got = _progress_payload("classify", 0.2, started_at=STARTED, stage_count=14)

    assert (got["step_done"], got["step_total"], got["step_label"]) == (0, 0, "")


# ── the context hands a stage the callback, and never requires one ───────────────────────────────

def test_a_stage_can_report_steps_through_the_context():
    seen: list[tuple[int, int, str]] = []
    ctx = PipelineContext(step_cb=lambda d, t, l: seen.append((d, t, l)))

    ctx.emit_step(3, 12, "LLM call")
    assert seen == [(3, 12, "LLM call")]


def test_a_stage_reporting_steps_with_no_callback_is_a_no_op():
    """Every existing caller passes no `step_cb`; reporting must not require one."""
    PipelineContext().emit_step(3, 12, "LLM call")      # must not raise


# ── the percentage is interpolated WITHIN the stage's own slot ────────────────────────────────────

def _recorder(monkeypatch) -> tuple[_RunProgress, list[dict]]:
    written: list[dict] = []
    rec = _RunProgress("run-1", STARTED)
    rec.stage_names = ["a", "b", "c", "d"]              # a four-stage pipeline
    monkeypatch.setattr(rec, "_write", lambda payload: written.append(payload) or False)
    return rec, written


def test_the_step_percentage_stays_inside_the_stages_own_slot(monkeypatch):
    """Reporting done/total as the RUN's pct would send the bar backwards when the stage ends.

    Stage "c" is the third of four, so its slot is 0.50 to 0.75 and a stage half done sits at
    0.625 — never past the stage after it.
    """
    rec, written = _recorder(monkeypatch)
    rec._current = "c"
    rec._entered = ["a", "b", "c"]

    rec.step(6, 12, "LLM call")

    assert written[-1]["pct"] == 0.625
    assert written[-1]["stage"] == "c"
    assert written[-1]["stage_index"] == 2               # two stages behind it


def test_a_finished_step_run_does_not_reach_the_next_stages_slot(monkeypatch):
    rec, written = _recorder(monkeypatch)
    rec._current = "c"
    rec._entered = ["a", "b", "c"]

    rec.step(12, 12, "LLM call")

    assert written[-1]["pct"] == 0.75                    # the top of its own slot, not beyond


def test_a_step_report_outside_any_stage_is_ignored(monkeypatch):
    """The pipeline's closing emit clears `_current`; a late report must not resurrect a stage."""
    rec, written = _recorder(monkeypatch)
    rec._current = ""

    rec.step(3, 12, "LLM call")

    assert written == []


def test_a_zero_total_is_ignored_rather_than_dividing_by_zero(monkeypatch):
    rec, written = _recorder(monkeypatch)
    rec._current = "b"
    rec._entered = ["a", "b"]

    rec.step(0, 0, "")

    assert written == []


def test_the_step_count_is_clamped_to_its_total(monkeypatch):
    """A retry could report more done than planned; the DENOMINATOR is what grows, not the bar."""
    rec, written = _recorder(monkeypatch)
    rec._current = "a"
    rec._entered = ["a"]

    rec.step(99, 12, "LLM call")

    assert written[-1]["step_done"] == 12
    assert written[-1]["pct"] <= 0.25


# ── and a step count cannot leak from one stage into the next ────────────────────────────────────

def test_entering_a_new_stage_resets_the_step_count(monkeypatch):
    """A count left over would read as progress the new stage has not made."""
    rec, written = _recorder(monkeypatch)
    rec._current = "a"
    rec._entered = ["a"]
    rec.step(5, 12, "LLM call")
    assert written[-1]["step_total"] == 12

    rec("b", 0.25)                                       # the pipeline entering the next stage

    assert written[-1]["step_total"] == 0
    assert written[-1]["step_label"] == ""


# ── reporting must never be able to fail the run ─────────────────────────────────────────────────

def test_a_failing_write_is_swallowed_not_raised(monkeypatch):
    """This runs inside a stage, so anything escaping is recorded as a failed extraction — a run
    that reached its rows reported as broken because a status write did not land."""
    rec = _RunProgress("run-1", STARTED)
    rec.stage_names = ["a", "b"]
    rec._current = "a"
    rec._entered = ["a"]
    degraded: list[str] = []
    monkeypatch.setattr(rec, "_write", lambda _p: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(rec, "_degraded", lambda exc: degraded.append(str(exc)))

    rec.step(1, 2, "LLM call")                           # must not raise

    assert degraded == ["db down"]


def test_elapsed_ms_is_derived_from_the_run_clock_not_accumulated(monkeypatch):
    """One clock for the whole run, so a step report cannot drift from it."""
    rec, written = _recorder(monkeypatch)
    rec.started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    rec._current = "a"
    rec._entered = ["a"]

    rec.step(1, 4, "LLM call")

    assert 29_000 <= written[-1]["elapsed_ms"] <= 40_000
