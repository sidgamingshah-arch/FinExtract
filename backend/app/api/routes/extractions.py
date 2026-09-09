"""Extraction endpoints: start a run and fetch its status.

``POST /documents/{id}/extractions`` returns 202 immediately and runs the pipeline in a FastAPI
BackgroundTask; progress and results are read by polling ``GET /extractions/{run_id}`` (the
frontend polls once a second while the run is ``running`` — see ``lib/queries.ts``).

``GET /documents/{id}/run-status`` answers the same question for a caller that has no run id — a
page loaded fresh mid-run — and answers it for a run that has produced nothing yet, which is the
one thing the two reads above cannot do between them (see :func:`get_document_run_status`).

There is no WebSocket stream. The earlier "stubbed WS contract" note in this docstring described
something that was never built, and the run has not been synchronous since extraction moved to the
background task.

WHAT THE POLL IS WORTH POLLING FOR: the run row carries a live per-stage progress record and the
tail of the pipeline log, written as each stage is reached (:class:`_RunProgress`), plus the
pipeline's own stage list so a reader can tick the stages off. Before that, the poll answered
``queued`` for the entire duration of a multi-minute run and then ``done`` — a declared-live number
that never moved.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from pathlib import Path

from app.api.deps import db, settings as get_settings_dep
# ``_latest_run`` is imported rather than re-spelled here: "the latest run for a document" is one
# ordering rule (``created_at`` descending), and a second copy of it would let this route and every
# other per-document read disagree about which run they are describing.
from app.api.routes.documents import (
    _can_access,
    _latest_run,
    _run_line_item_version_id,
    _run_template_id,
    authorized_document,
)
from app.config import Settings
from app.ports.object_store import LocalObjectStore
from app.schemas.loader import load_template
from app.security import Permission, Principal, current_principal, require
from app.services import audit as audit_svc
from app.services.documents import run_extraction

router = APIRouter(tags=["extractions"])

logger = logging.getLogger(__name__)

# How much of the pipeline log the progress screen is given. The whole log is written once, when the
# run settles; this is the moving tail that makes a slow stage inspectable instead of opaque.
_LOG_TAIL_LINES = 100


def pipeline_stage_names() -> list[str]:
    """The stage names a run passes through, in the order the pipeline assembles them.

    Read off ``default_pipeline()`` on every call, never copied into a literal here. The published
    stage list has been wrong once already — a stated pipeline that was missing four of the stages
    that actually run — and a second copy of it is a second thing to go stale the next time a stage
    is added. A screen ticking stages off against a stale list mislabels every run.
    """
    from app.core.pipeline import default_pipeline

    return [stage.name for stage in default_pipeline().stages]


def _log_tail(lines: list[str] | None) -> str:
    """The tail of a pipeline log, ONE spelling of "tail" for the mid-run flush and for what the
    endpoint serves — so the screen never sees the window change size mid-run."""
    return "\n".join((lines or [])[-_LOG_TAIL_LINES:])


def _as_utc(stamp: datetime) -> datetime:
    """A start stamp read as UTC when it says nothing about its zone.

    A naive stamp would make every ``elapsed_ms`` subtraction raise ``TypeError`` — including the one
    on the failure path, which would leave the run row untouched at ``running`` and a polling client
    waiting on it for ever. UTC is what ``models._now`` and every other stamp in this codebase means.
    """
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def _progress_payload(phase: str, pct: float, *, started_at: datetime, stage_count: int,
                      stage: str = "", stages_done: list[str] | None = None,
                      step_done: int = 0, step_total: int = 0, step_label: str = "",
                      llm_calls: int = 0) -> dict:
    """One ``ExtractionProgress`` record (the shape declared in ``frontend/src/types.ts``).

    ``_PROGRESS_FIELDS`` is the same shape read back: a record missing any of these keys is not one
    of these records, and :func:`_served_progress` refuses to pass it off as one.

    Written in one place so the queued row, every stage transition and the terminal state cannot
    disagree about the shape — the terminal states used to be a two-key ``{phase, pct}`` dict, which
    would have collapsed the fields a screen reads at the exact moment it reads them.

    ``elapsed_ms`` is derived from ``started_at`` at each emit rather than accumulated across emits,
    so it cannot drift from the clock.
    """
    done = list(stages_done or [])
    return {
        "phase": phase,
        "pct": pct,
        "stage": stage,  # the stage in flight; "" when none is
        # How far into the pipeline's stage SEQUENCE this record sits, which is just the number of
        # stages behind it — counted, never looked up by name. A pipeline may legitimately run the
        # same stage twice (``GapClosingStage`` exists to re-do work), and a name lookup would send
        # progress backwards on the second pass.
        "stage_index": len(done),
        "stage_count": stage_count,
        "stages_done": done,
        # PROGRESS WITHIN THE STAGE IN FLIGHT, which is the whole reason a reader could not tell a
        # working run from a hung one. `map_line_items` is one stage and by far the longest — it
        # makes one batched LLM call per (statement, section) subgroup, concurrently — and until
        # this existed, nothing moved for the whole of it: not the percentage, not the stage
        # counter, and not the log tail, because the only thing that flushed them was the NEXT
        # stage starting.
        #
        # 0/0 means "this stage reports no sub-steps", which is every stage but one. A screen must
        # read it as "no detail available", never as "0% of 0 done".
        "step_done": step_done,
        "step_total": step_total,
        "step_label": step_label,        # what a unit IS: "LLM batch", "page", …
        # Cumulative LLM calls the run has made, live rather than only in the final result. The
        # question actually being asked mid-run is "how many calls have completed", and the count
        # was already on the context — it just never left it until the run finished.
        "llm_calls": llm_calls,
        "started_at": _as_utc(started_at).isoformat(),
        "elapsed_ms": max(0, int((datetime.now(timezone.utc)
                                  - _as_utc(started_at)).total_seconds() * 1000)),
    }


# Every key an ``ExtractionProgress`` carries. Used to decide whether a STORED record is one of these
# records at all — see :func:`_served_progress`.
_PROGRESS_FIELDS = frozenset(_progress_payload(
    "", 0.0, started_at=datetime(1970, 1, 1, tzinfo=timezone.utc), stage_count=0))


# A run that has STOPPED. Named as the terminal set rather than the live one on purpose: the thing
# that must be true is that a settled run's duration stops moving, and anything else — a status this
# codebase does not have yet — is a run still going, which should get a clock. An allowlist would
# silently freeze the clock on it instead. (It also had a dead member: "queued" is a progress PHASE,
# never a run row's status; the row is created `running`, see `start_extraction`.)
_SETTLED_STATUSES = frozenset({"succeeded", "failed", "canceled"})


_SUPPLEMENTAL_STATEMENTS = frozenset({"statement_setup", "covenants_supplemental", "notes"})


def _safe_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _pick_row_value(rows: list[dict], key: str, period: str = "current") -> str | None:
    for row in rows:
        if row.get("canonical_key") != key:
            continue
        for v in row.get("values", []) or []:
            if (v.get("period_label") or "") == period and v.get("value") is not None:
                return str(v.get("value"))
    return None


def _period_display(rows: list[dict], period: str) -> str | None:
    for row in rows:
        for v in row.get("values", []) or []:
            if (v.get("period_label") or "") == period and v.get("period_display"):
                return str(v.get("period_display"))
    return None


def _iter_leaf_nodes(node) -> list[dict]:
    if not isinstance(node, dict):
        return []
    children: list[dict] = []
    for field in ("line_items", "rows", "children", "items", "components"):
        for child in node.get(field, []) or []:
            if isinstance(child, dict):
                children.append(child)
    out: list[dict] = []
    for child in children:
        out.extend(_iter_leaf_nodes(child))
    key = node.get("canonical_key")
    if isinstance(key, str) and key and not children:
        out.append(node)
    return out


def _supplemental_template_nodes(template_def: dict | None) -> list[dict]:
    if not isinstance(template_def, dict):
        return []
    nodes: list[dict] = []
    for stmt in template_def.get("statements", []) or []:
        if not isinstance(stmt, dict) or (stmt.get("type") not in _SUPPLEMENTAL_STATEMENTS):
            continue
        for sec in stmt.get("sections", []) or []:
            if isinstance(sec, dict):
                nodes.extend(_iter_leaf_nodes(sec))
    seen: set[str] = set()
    deduped: list[dict] = []
    for n in nodes:
        key = str(n.get("canonical_key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(n)
    return deduped


def _supp_row(key: str, label: str, *, value: str | None,
              period: str = "current", period_display: str | None = None,
              basis: str = "consolidated") -> dict:
    values = []
    if value is not None and str(value).strip() != "":
        values.append({
            "period_label": period,
            "period_display": period_display,
            "column_index": None,
            "basis": basis,
            "value": str(value),
            "provenance": None,
            "confidence": {
                "mapping": 1.0,
                "validation": 1.0,
                "overall": 1.0,
                "weakest": "mapping",
                "flags": ["supplemental:derived"],
            },
        })
    return {
        "id": f"supp::{key}",
        "source_label": label,
        "canonical_key": key,
        "note": None,
        "printed_in": "other",
        "bucket": None,
        "bucket_label": None,
        "section": None,
        "notes": [],
        "role": "line",
        "mapping_method": "supplemental",
        "mapping_confidence": 1.0,
        "flags": ["supplemental:template_declared"],
        "values": values,
    }


def _build_supplemental_rows(*, template_def: dict | None, base_rows: list[dict],
                             disclosures: list[dict], entity_name: str | None,
                             doc_model, ctx, options: dict | None) -> list[dict]:
    nodes = _supplemental_template_nodes(template_def)
    if not nodes:
        return []
    existing = {str(r.get("canonical_key") or "") for r in base_rows}
    if not nodes:
        return []

    from app.services.derived import compute_ratios
    from app.services.rollups import evaluate_rows

    current_display = _period_display(base_rows, "current")
    prior_display = _period_display(base_rows, "prior")
    periods_text = " / ".join([x for x in [current_display, prior_display] if x]) or None

    computed = evaluate_rows(template_def, base_rows, "consolidated", "current", "en") if template_def else {}
    assets = (_safe_decimal(computed.get("bs_total_assets"))
              or _safe_decimal(_pick_row_value(base_rows, "bs_total_assets", "current")))
    erl = (_safe_decimal(computed.get("bs_total_equity_and_liabilities"))
           or _safe_decimal(_pick_row_value(base_rows, "bs_total_equity_and_liabilities", "current")))
    total_income = (_safe_decimal(computed.get("pl_profit_for_the_year"))
                    or _safe_decimal(_pick_row_value(base_rows, "pl_profit_for_the_year", "current")))
    diff = (assets - erl) if (assets is not None and erl is not None) else None

    # Ratios keyed by their standard derived identifiers for covenant/supplemental fields.
    ratio_map: dict[str, dict] = {}
    for r in compute_ratios(base_rows, basis="consolidated", period="current", locale="en",
                            template_def=template_def):
        k = str(r.get("key") or "").strip()
        if k:
            ratio_map[k] = r

    hit_disclosures = {str(d.get("key") or ""): d for d in disclosures if d.get("present")}
    unit = getattr(doc_model, "unit_context", None)
    target_currency = (options or {}).get("target_currency") or getattr(unit, "target_currency", None)
    source_currency = getattr(unit, "source_currency", None)
    rounding = (options or {}).get("target_units")
    if rounding is None and unit is not None:
        rounding = getattr(unit, "target_units", None)
    statement_date = current_display

    setup_values: dict[str, str | None] = {
        "statement_setup_controls__customer_s_name": entity_name,
        "statement_setup_controls__customer_statement_type": "Financial statements",
        "statement_setup_controls__rounding": (str(rounding) if rounding is not None else None),
        "statement_setup_controls__source_currency": (str(source_currency) if source_currency else None),
        "statement_setup_controls__target_currency": (str(target_currency) if target_currency else None),
        "statement_setup_controls__statement_date": statement_date,
        "statement_setup_controls__periods": periods_text,
        "statement_setup_controls__total_assets": (str(assets) if assets is not None else None),
        "statement_setup_controls__total_equity_reserves_liab": (str(erl) if erl is not None else None),
        "statement_setup_controls__total_income_expenses": (
            str(total_income) if total_income is not None else None),
        "statement_setup_controls__difference": (str(diff) if diff is not None else None),
        "statement_setup_controls__unexplained_adj_to_ret_profits": ("0" if diff == 0 else None),
        "statement_setup_controls__audit_opinion_stmt_source": (
            "Qualified" if "auditor_qualification" in hit_disclosures else "No qualification detected"),
        "statement_setup_controls__accounting_standard": None,
        "statement_setup_controls__accountant": None,
        "statement_setup_controls__analyst": None,
        "statement_setup_controls__statement_type": (getattr(doc_model.fmt, "value", None) if hasattr(doc_model, "fmt") else None),
        "statement_setup_controls__status": "extracted",
        "statement_setup_controls__reconcile_to": None,
    }

    def _norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())

    out: list[dict] = []
    for node in nodes:
        key = str(node.get("canonical_key") or "").strip()
        if not key or key in existing:
            continue
        label = str((node.get("label_i18n") or {}).get("en") or node.get("label") or key)
        value: str | None = None
        period = "current"
        period_display = current_display

        if key in setup_values:
            value = setup_values[key]
        elif key.startswith("notes__"):
            nkey = key.removeprefix("notes__")
            if nkey == "related_party_transactions":
                value = "Yes" if "related_party" in hit_disclosures else "No"
            elif nkey == "contingent_liabilities":
                # LEFT EMPTY DELIBERATELY, never "No". This concept is NUMERIC and its figure has
                # to come from configuration — a note source the rulebook describes, bound like any
                # other line. Reaching this branch means nothing bound it, and the rulebook is
                # explicit about what may not be said then:
                # exclude[2] "Do not infer a numeric zero from silence", and
                # docs/PRC_Contingent_Liabilities_Extraction_Logic_Revised.md §8.1 "Do not state
                # that no contingent liabilities exist merely because no amount was disclosed."
                #
                # A "No" here is not a cautious answer, it is a fabricated negative — and it was
                # published at confidence {mapping 1.0, validation 1.0} on a PRC filing that
                # discloses HK-equivalent ¥118,754,500 of 保函 and 国内信用证 in plain prose on
                # page 197. A reader cannot distinguish that from a genuine nil. An empty cell can
                # be chased; a confident "No" closes the question.
                value = None
            elif nkey == "auditor_s_opinion":
                value = "Qualified" if "auditor_qualification" in hit_disclosures else "Unqualified/Not detected"
            elif nkey == "notes":
                value = f"{len(getattr(doc_model, 'notes', []) or [])} extracted note tables"
            elif nkey == "confirmed_with_rm":
                value = "No"
            else:
                value = "No" if hit_disclosures else None
        else:
            key_norm = _norm(key)
            for rk, rv in ratio_map.items():
                if _norm(rk) in key_norm:
                    raw = rv.get("value")
                    value = (str(raw) if raw is not None else None)
                    if value is None and rv.get("current") is not None:
                        value = str(rv.get("current"))
                    break
            if value is None:
                for dkey in hit_disclosures:
                    if _norm(dkey) in key_norm:
                        value = "Yes"
                        break

        out.append(_supp_row(key, label, value=value, period=period,
                             period_display=period_display, basis="consolidated"))
    return out


def _served_progress(record: dict | None, status: str = "") -> dict | None:
    """A stored progress record, or None when the row does not carry this contract.

    Runs written before the contract existed hold ``{"phase": …, "pct": …}`` (or ``{}`` from the
    column default), and ``init_db`` uses ``create_all`` — so those rows are still on disk and still
    readable. Completing one here would mean inventing the stage count, the stage and the start time
    of a run whose pipeline is not recoverable, and ``ExtractionProgress`` declares every field
    required, so a screen would read ``undefined`` where the type promises a number. Saying "there is
    no progress record for this run" is the true answer; ``status`` still says how it ended.

    ``elapsed_ms`` IS RE-DERIVED HERE FOR A RUN STILL IN FLIGHT, and that is the defect this closes.
    ``_RunProgress`` writes one record per stage transition, and the elapsed figure was stamped into
    it at write time — so between two stages, however long that took, every poll returned the SAME
    number and the screen's Elapsed line sat frozen and then jumped. On a slow stage (mapping a long
    filing through the LLM is the obvious one) it can sit still for minutes, which reads as a hung
    run rather than a working one. The clock is not a per-stage measurement: it is
    ``now - started_at``, and ``started_at`` is in the record, so the honest answer is computed when
    it is asked for.

    A SETTLED RUN KEEPS ITS STORED FIGURE, which is the whole reason this is conditional. That
    number is the run's DURATION, measured when it finished; re-deriving it would make a finished
    run's elapsed time keep climbing for as long as anyone left the screen open.
    """
    if not record or not _PROGRESS_FIELDS.issubset(record):
        return None
    if status in _SETTLED_STATUSES:
        return record
    try:
        began = datetime.fromisoformat(str(record.get("started_at")))
    except (TypeError, ValueError):
        # A start stamp this endpoint cannot read is not a reason to serve nothing: the rest of the
        # record is intact, and the stored elapsed figure is still the last true measurement.
        return record
    # A COPY. `record` is the run row's JSON column, and mutating it here would write a read-time
    # figure into the object the session may flush.
    return {**record,
            "elapsed_ms": max(0, int((datetime.now(timezone.utc)
                                      - _as_utc(began)).total_seconds() * 1000))}


class RunCanceled(Exception):
    """The run's row says ``canceled`` — stop the pipeline where it stands.

    Raised from ``_RunProgress.__call__`` (the pipeline's ``progress_cb``, which fires between
    every pair of stages) and caught by ``_run_extraction_task``. It is the whole of the
    cooperative half of cancellation: ``cancel_run`` writes the word onto the row, and this carries
    that word back into the worker.

    AN ``Exception``, DELIBERATELY NOT A ``BaseException``, and both halves of that matter:

      * ``_run_extraction_task``'s handler ends with ``if not isinstance(exc, Exception): raise``,
        so a BaseException subclass would be re-raised out of the worker. Starlette awaits sync
        BackgroundTasks inside the request/response cycle — which is also why TestClient runs them
        synchronously — so that re-raise would surface out of the client's own POST: a 500 handed
        back for a cancellation that WORKED.
      * ``_RunProgress.__call__`` wraps its body in ``except Exception`` on purpose, so raising
        inside that body would be swallowed by the very guard that stops a progress write failing a
        run. It is raised AFTER the guard instead — see ``__call__``.

    Before this, cancelling wrote ``status='canceled'`` and nothing else: the worker read that
    field once, AFTER all 21 stages had finished, so a cancel bought nothing. One observed run kept
    working for 160 seconds past its cancel, and because a replacement run was started meanwhile,
    two 367-page extractions competed for the same rate-limited provider quota.
    """

    def __init__(self, run_id: str, stage: str = "") -> None:
        super().__init__(f"run {run_id} was canceled"
                         + (f" before stage {stage}" if stage else ""))
        self.run_id = run_id
        self.stage = stage


class _RunProgress:
    """Persists the pipeline's progress onto the run row, one small write per stage transition.

    THE DEFECT THIS CLOSES: ``Pipeline.run`` has always emitted a progress event before each of its
    stages and once more when it finishes (``core/pipeline.py``), and ``_run_extraction_task`` called
    ``run_extraction`` without a ``progress_cb`` — which defaults to None, so every one of those
    emits was a no-op. ``run.progress`` therefore held ``queued`` for the whole duration of a run and
    then jumped to ``done``, and ``run.logs`` was written once, at the very end. The API served a
    field that looked live and was not.

    Each write opens its OWN short-lived session. The worker's session is mid-flight while these
    fire: it assembles ``run.result`` across many statements and commits once, at the end, so
    committing that session from here would publish a half-built run to whoever is polling. Fourteen
    tiny commits over a run is the cheap side of that trade.

    Nothing in here may fail the extraction. Progress is a report ABOUT the run, not part of it, and
    a run that reached its rows must not be recorded as failed because a status write did not land.
    """

    def __init__(self, run_id: str, started_at: datetime) -> None:
        self.run_id = run_id
        # The instant the RUN started — the run row's own ``created_at``, handed down by the request
        # that queued it. ONE clock for the whole run: stamping a second one here made ``started_at``
        # jump forward and ``elapsed_ms`` fall back towards zero the moment the worker picked the run
        # up, and lost the time it spent queued, which is time the reader was waiting.
        self.started_at = _as_utc(started_at)
        self.stage_names = pipeline_stage_names()
        self._entered: list[str] = []   # every stage entry the pipeline made, in order
        self._current = ""              # the stage in flight; "" once the pipeline is past them all
        self._ctx = None                # the live context, whose log tail each write flushes
        # (done, total, label) reported from inside the stage in flight. Reset on every stage
        # entry: a count left over from the previous stage would read as progress this one has
        # not made, and 0/0 is how a screen is told there is no sub-step detail.
        self._step: tuple[int, int, str] = (0, 0, "")

    def observe(self, ctx) -> None:
        """Take the live pipeline context as soon as it exists, because its ``logs`` list is what the
        tail is flushed from and ``run_extraction`` only returns it once every stage has finished."""
        self._ctx = ctx

    @property
    def ctx_logs(self) -> list[str]:
        """The stage trail so far. Read by the worker's failure path, which has no other route to it:
        ``run_extraction`` raised rather than returning, so the context it would have handed back does
        not exist outside this recorder."""
        return list(getattr(self._ctx, "logs", None) or [])

    def __call__(self, phase: str, pct: float) -> None:
        """The pipeline's ``progress_cb``: called before each stage, and once with ``done``.

        Guarded as a whole, not just around the commit: this runs INSIDE ``Pipeline.run``, so
        anything that escapes here propagates out of ``run_extraction`` and gets recorded as a failed
        extraction — a run that reached its rows reported as broken because a status write did not
        land.
        """
        canceled = False
        try:
            if phase not in self.stage_names:
                # The pipeline's closing ``done`` emit means ITS work is over, not the RUN's: the
                # worker still serialises the rows, scans the disclosures and detects the entity
                # before it commits a result. Publishing phase ``done`` at pct 1.0 here would hand a
                # poller a completion it can act on while ``status`` is still ``running`` and
                # ``result`` is still null. The bookkeeping is kept; ``settle`` publishes it in the
                # same commit as the status and the result, which is when it becomes true.
                self._current = ""
                return
            self._current = phase
            self._step = (0, 0, "")
            # Appended on every entry, a repeat included, so the count of stages behind this one is
            # the pipeline's own position and a stage run twice does not report the same index twice.
            self._entered.append(phase)
            # The emit precedes its stage, so the stage being announced is not yet done.
            canceled = self._write(self._payload(phase, pct, self._entered[:-1]))
        except Exception as exc:  # noqa: BLE001 — see above; reporting must not fail the run
            self._degraded(exc)
        # OUTSIDE THE GUARD ABOVE, AND THAT IS THE ENTIRE MECHANISM. `cancel_run` only writes
        # `canceled` onto the row; the worker's own check happens after the pipeline has finished,
        # so a cancellation used to stop nothing. This is the one place that runs between every
        # pair of stages AND already holds a session, so it is where the row gets asked. Raising
        # inside the `try` would hand the exception straight to the `except Exception` above, which
        # exists precisely to swallow it, and the pipeline would carry on as if nothing was asked.
        if canceled:
            raise RunCanceled(self.run_id, phase)

    def settle(self, phase: str) -> dict:
        """The terminal record for the worker's own commit — ``done`` or ``failed`` — in the same
        shape as every emit before it.

        ``failed`` NAMES the stage that was in flight, which is the first thing a reader of a failed
        run wants and the only thing the two-key dict this replaced could never say.

        ``pct`` is the fraction of the pipeline the run actually got through, not a flat 1.0: the
        pipeline short-circuits on an integrity blocker and still finishes, and a record reading
        "done, 100%, 2 of 14 stages" contradicts itself on the same screen.
        """
        done = self._entered[:-1] if self._current else list(self._entered)
        n = len(self.stage_names)
        return self._payload(phase, round(len(done) / max(n, 1), 3), done)

    def _payload(self, phase: str, pct: float, done: list[str]) -> dict:
        return _progress_payload(phase, pct, started_at=self.started_at,
                                 stage_count=len(self.stage_names), stage=self._current,
                                 stages_done=done,
                                 step_done=self._step[0], step_total=self._step[1],
                                 step_label=self._step[2],
                                 llm_calls=int(getattr(self._ctx, "llm_calls", 0) or 0))

    def step(self, done: int, total: int, label: str = "") -> None:
        """The pipeline's ``step_cb``: a stage reporting progress from inside itself.

        WHAT THIS BUYS. Three things were frozen for the whole of `map_line_items` — the percentage,
        the stage counter, and the LOG TAIL — because `_write` only ran on a stage transition. One
        write per completed unit unfreezes all three, and carries a live LLM call count that
        previously only appeared in the final result.

        THE PERCENTAGE IS INTERPOLATED WITHIN THE STAGE, not replaced. The stage's own share of the
        pipeline is one slot out of `stage_count`, so a stage that is 3 of 12 units done sits a
        quarter of the way across its own slot and never overtakes the stage after it. Reporting
        `done/total` as the run's pct would send the bar backwards the moment the stage finished.

        Guarded exactly as `__call__` is, and for the same reason: this runs inside a stage, so
        anything escaping here is recorded as a failed extraction — a run that reached its rows
        reported as broken because a status write did not land. Cancellation is deliberately NOT
        raised from here; it stays on the stage boundary, so a stage cannot be torn down halfway
        through assembling a document.
        """
        try:
            if not self._current or total <= 0:
                return
            # CLAMPED ONCE, AND THE CLAMP FEEDS THE PERCENTAGE TOO. Clamping only the reported
            # count while computing the bar from the raw one is worse than not clamping at all: a
            # caller reporting 99 of 12 (a retry, or a miscounted plan) drove `base + slot * 8.25`
            # straight through `min(1.0, …)` and parked the bar at 100% while the first stage of
            # four was still running. Caught by a test, not by a filing.
            shown = max(0, min(done, total))
            self._step = (shown, total, label)
            behind = self._entered[:-1]
            slot = 1.0 / max(len(self.stage_names), 1)
            base = len(behind) * slot
            self._write(self._payload(self._current,
                                      round(min(1.0, base + slot * (shown / total)), 3), behind))
        except Exception as exc:  # noqa: BLE001 — reporting must never fail the run
            self._degraded(exc)

    def _write(self, payload: dict) -> bool:
        """Flush one progress record. Returns True only when the row POSITIVELY says ``canceled``.

        The cancellation read IS this method's existing ``session.get`` — no second query per
        stage, and the fresh short-lived session is what makes it work at all: the worker's own
        session has held this row since before the cancel was committed and would keep answering
        ``running`` for the rest of the run.

        A MISSING ROW IS NOT A CANCELLATION, and neither is a read that failed (the caller's guard
        catches that and leaves its flag False). Cancellation has to be asserted by the row itself
        — inferring it from absent evidence would stop the probes and re-run scripts that drive
        this recorder against a run id they never inserted.
        """
        from app.db.base import SessionLocal
        from app.db.models import ExtractionRun

        session = SessionLocal()
        try:
            run = session.get(ExtractionRun, self.run_id)
            if run is None:
                return False
            if run.status == "canceled":
                # Nothing is written: `cancel_run` already stamped `phase: canceled` against the
                # stage that was in flight, and overwriting it here with the stage about to NOT run
                # would publish forward motion on a run that has just been stopped.
                return True
            run.progress = payload
            # A moving WINDOW on the log, replaced each time rather than appended to: bounded, so a
            # thousand-stage-line run does not rewrite a growing column fourteen times, and the whole
            # log lands here anyway when the run settles. Before this the column stayed empty for the
            # entire run, so a screen watching a slow stage had nothing at all to show.
            run.logs = _log_tail(getattr(self._ctx, "logs", None))
            session.commit()
            return False
        finally:
            session.close()   # rolls back anything left pending by a failed commit

    def _degraded(self, exc: Exception) -> None:
        """Say why progress stopped moving, in both places a reader looks: the server log, which
        always keeps it, and the run's own log, where it survives to the end of a run that succeeds
        (the final write there carries the whole of ``ctx.logs``). A progress record that froze with
        nothing explaining why is how a working extraction comes to be reported as hung."""
        logger.warning("run %s: progress write failed (%s: %s)",
                       self.run_id, type(exc).__name__, exc, exc_info=True)
        ctx_logs = getattr(self._ctx, "logs", None)
        if ctx_logs is not None:
            ctx_logs.append(f"progress:write_failed:{type(exc).__name__}: {exc}")


def _maybe_cache_credit_narrative(session: Session, run, locale: str, entity: str | None) -> None:
    """Auto-generate the LLM credit narrative once and cache it on the run, so the Analysis
    screen / export show it without a manual click. Best-effort and fully guarded: it runs only
    when a real LLM provider is configured, and any failure (no key, unreachable, thin data)
    leaves the deterministic credit view untouched — the extraction has already succeeded."""
    try:
        from app.config import get_settings

        settings = get_settings()
        if settings.llm.provider == "stub":
            return
        from app.ports.registry import registry as reg
        from app.services.analysis_llm import run_credit_narrative
        from app.services.derived import build_credit_analysis, localize_disclosures

        rows = run.result.get("rows", [])
        disclosures = localize_disclosures(run.result.get("disclosures", []), locale)
        credit = build_credit_analysis(rows, disclosures, locale=locale)
        if not credit.get("factors") and not credit.get("flags"):
            return
        provider = reg.get("llm", settings.llm.provider)
        result, meta = run_credit_narrative(provider, credit, entity=entity or "",
                                            locale=locale, max_tokens=settings.llm.max_tokens)
        run.result = {**run.result, "credit_narrative": {
            "text": result.narrative, "provider": settings.llm.provider,
            "model": meta.get("model", settings.llm.model)}}
        session.commit()
    except Exception:  # noqa: BLE001 — optional enrichment; never disturb a succeeded run
        session.rollback()


def _maybe_cache_netting(session: Session, run, locale: str) -> None:
    """Evaluate the LINE-ITEM SET's generic containment-netting policies against THIS extraction
    once, via the LLM, and cache the confirmed (resolved) rules on the run. The statement/export
    then apply the deterministic math from the cached decision — so a policy nets only where the
    model confirmed the containment, and per-request rendering stays fast. Best-effort and
    guarded."""
    try:
        from app.config import get_settings

        settings = get_settings()
        if settings.llm.provider == "stub":
            return
        from app.api.routes.documents import _netting_rules_for_run
        rules = _netting_rules_for_run(session, run)
        if not rules:
            return
        from app.ports.registry import registry as reg
        from app.services.netting import resolve_netting

        provider = reg.get("llm", settings.llm.provider)
        resolved = resolve_netting(provider, run.result.get("rows", []), rules,
                                   max_tokens=settings.llm.max_tokens)
        run.result = {**run.result, "netting": resolved}
        session.commit()
    except Exception:  # noqa: BLE001 — optional; a succeeded extraction is never disturbed
        session.rollback()


def _folio_lookup(doc_model):
    """``page_index -> the folio printed on that page``, as a callable, or None when the document
    has no folios at all (every spreadsheet, and a PDF whose footers the classifier could not read).

    Built once per serialization and closed over, rather than scanned per provenance record: a
    200-page filing has thousands of values, and a linear scan per value is quadratic for a lookup
    that is a dict.
    """
    folios = {pg.index: pg.printed_page for pg in (doc_model.pages or [])
              if getattr(pg, "printed_page", None)}
    if not folios:
        return None
    return folios.get


def _serialize_rows(doc_model, working_view=None) -> list[dict]:
    """Extracted line items in a view-friendly shape, each value with its provenance
    (sheet+cell for Excel, page+bbox for PDF) so the UI can show click-to-source.

    Three things about a row that were computed by the pipeline and then left behind travel with it
    from here, because every screen and every export reads THIS list:

    * ``printed_in`` — face or note. Not derivable downstream: face and note rows have the same
      shape, and a note's detail lines sum to a figure the face already reports, so adding the two
      together double-counts the filing.
    * ``bucket`` / ``bucket_label`` / ``section`` — which of the analyst sections the row belongs to
      (``services.buckets``). The segmentation already decided it; this joins the answer to the row
      instead of making every consumer re-derive it from the configuration.
    * ``derivation`` — for the eight concepts assembled from note datasets rather than read off a
      caption, the rule that produced the figure and the note lines it consumed. Computed by the
      services, and previously discarded here: only the first evidence item's page reference
      survived as the value's provenance, leaving a reviewer the winning priority and nothing to
      check it with.
    * ``notes`` — the note numbers this row CITES and that were actually extracted as note tables,
      resolved through the ``FaceNoteLink``s the link-notes stage built. A number the filing prints
      with no note behind it is not in the list, so the linkage cannot promise detail that is not
      there.

    ``working_view`` is the MATCHER'S VIEW of the line-item set this run mapped with (built by
    ``services.working_view.build_working_view``). Nothing here reads it — every field above is
    read off the model the pipeline already annotated — and it stays in the signature so a caller
    hands over the configuration it mapped with without having to know which serializer consults
    it. It was called ``ontology`` while the ontology was a stored, selectable object; line items
    is now the single configuration engine and the only thing there is to hand over.
    """
    seg_of, label_of = _bucket_index(doc_model)
    notes_of = _linked_notes(doc_model)
    folio_of = _folio_lookup(doc_model)
    rows = []
    for li in doc_model.line_items:
        values = []
        for ev in li.values.values():
            p = ev.provenance
            prov = None
            if p is not None:
                prov = _prov_dict(p, folio_of)
            cv = ev.confidence
            values.append({
                "period_label": ev.period_label,
                "period_display": ev.period_display,  # real period-end date for headers, if any
                # Printed left-to-right position of a matrix's component column; null otherwise.
                "column_index": ev.column_index,
                "basis": ev.basis.value,
                "value": (str(ev.value) if ev.value is not None else None),
                # THE UNIT THIS FIGURE IS IN, per value rather than per document.
                #
                # Neither field was emitted, so every one of a run's values arrived at the screen
                # with no currency and no scale (measured: None on all 663 English and 517 Chinese
                # values) and the UI could only ever show the DOCUMENT-level currency from
                # result['units']. That is right for a single-currency filing and wrong for the
                # ones this rulebook is written for: a note stating a USD balance inside an RMB
                # statement would be labelled RMB, and a note printed in thousands beside a face
                # printed in units would be labelled the same as the face. The pipeline has always
                # carried both on the value's own unit context — §"Persist unit on every fact;
                # never normalise scale silently" — and only the serializer dropped them.
                "currency": (getattr(ev.unit_ctx, "currency", None) or None
                             if getattr(ev, "unit_ctx", None) is not None else None),
                "scale": (str(ev.unit_ctx.scale_factor)
                          if getattr(ev, "unit_ctx", None) is not None
                          and getattr(ev.unit_ctx, "scale_factor", None) is not None else None),
                "provenance": prov,
                # Per-value confidence vector — the weakest signal and any flags let the UI
                # colour and explain each number, not just the row.
                "confidence": {
                    "mapping": cv.mapping, "validation": cv.validation,
                    "overall": cv.overall, "weakest": cv.weakest,
                    "flags": list(cv.flags),
                },
            })
        rows.append({
            # The row's id, so the bucket segmentation written by the same run can name which rows
            # belong to which bucket without a second copy of the figures (services/buckets.py).
            # Per-run and not stable across re-runs — anything that has to survive a re-run keys on
            # the canonical key or the label geometry instead, see ``_prov_dict``.
            "id": str(li.id),
            "source_label": li.source_label,
            "canonical_key": li.canonical_key,
            "note": li.note_number,
            # Where it was printed, which of the analyst sections it belongs to, and which extracted
            # notes detail it — see this function's docstring for why each has to travel with the row.
            "printed_in": (li.printed_in.value if li.printed_in else None),
            "bucket": seg_of.get(str(li.id)),
            "bucket_label": label_of.get(seg_of.get(str(li.id)) or ""),
            "section": _section_of_row(li),
            "notes": notes_of.get(str(li.id), []),
            "role": li.role.value,
            "mapping_method": li.confidence.method,
            "mapping_confidence": li.confidence.mapping,
            "flags": list(li.confidence.flags),
            # How a service-computed figure was reached, per basis:period. Absent for a row read
            # off a caption, which needs no explanation beyond its provenance.
            **({"derivation": li.derivation} if li.derivation else {}),
            "values": values,
        })
    return rows


def _bucket_index(doc_model) -> tuple[dict[str, str], dict[str, str]]:
    """(row id → bucket key, bucket key → label) from the segmentation this run already computed.

    Empty when the segment stage did not run, which is how a partial pipeline stays serializable:
    the field is then absent rather than guessed.
    """
    from app.services.buckets import BUCKET_LABELS

    store = getattr(doc_model, "buckets", None)
    if store is None:
        return {}, {}
    out = {row_id: seg.bucket for seg in store.segments for row_id in seg.face_item_ids}
    return out, dict(BUCKET_LABELS)


def _linked_notes(doc_model) -> dict[str, list[str]]:
    """row id → the note numbers it cites THAT EXIST as extracted note tables, in citation order.

    Read off the ``FaceNoteLink``s rather than off ``note_number``: that field holds what the page
    printed in its note column, which is a promise the filing makes and not one this extraction can
    keep — a note the run never parsed would otherwise be offered as a link to nothing.

    AND INTERSECTED WITH THE NOTES THE RUN PUBLISHED, because the links are older than the note
    list. ``link_notes`` runs before ``prune_notes``, which drops every note no face row cites, so a
    link can name a table that is no longer in ``doc.notes`` by the time this serializes — and the
    guarantee above would then be false for exactly the notes the run decided not to publish.
    Intersecting here makes the guarantee independent of stage order.
    """
    published = {str(nt.note_number).strip() for nt in (getattr(doc_model, "notes", []) or [])
                 if nt.note_number is not None}
    out: dict[str, list[str]] = {}
    for link in getattr(doc_model, "links", []) or []:
        if str(link.note_number).strip() not in published:
            continue
        got = out.setdefault(str(link.face_item_id), [])
        if link.note_number not in got:
            got.append(link.note_number)
    return out


def _section_of_row(li) -> str | None:
    """Section token for one row, from printed section first and key namespace second."""
    from app.services.mapping import section_of_banner, section_of_key

    return (section_of_banner(getattr(li, "section_hint", None) or "")
            or section_of_key(getattr(li, "canonical_key", None) or ""))


def _prov_dict(p, folio_of=None):
    """One provenance record as the API serves it — ONE spelling, used by the face rows and the
    note rows alike (the face path used to carry an inline copy of this dict, and the two then
    disagreed about which fields exist).

    ``label_bbox`` is carried because the row LABEL's geometry is the only box on a paginated source
    that does not move when the figure does. ``bbox`` is the value word's box (row_reconstruct.py),
    so "Cash and cash equivalents 1,204" and the same line printed 12,048 have different ``bbox``
    x0s — right-aligned figures grow leftwards. The review queue's judgement subject is anchored on
    this geometry (api/routes/documents.py::_prov_anchor), and an anchor that moves with the figure
    means a reviewer's acceptance is reported as belonging to a finding that was corrected when the
    figure merely changed. The label box is what makes the anchor value-independent, and it never
    reached the anchor before because this serializer dropped it.

    ``printed_page`` is the folio the PUBLISHER printed on that page, and it is here rather than
    looked up per call site because this is the ONE serializer both face rows and note rows pass
    through. Every citation in the product is a function of a provenance dict, so carrying the folio
    alongside the sheet index is what lets a citation name the number the reader can actually look
    up — without threading a page map through ``_build_statement``, ``_build_review`` and
    ``_inspector``, whose positional signatures tests call directly.

    A SIBLING, NEVER A REDEFINITION. ``page_index`` stays the 0-based sheet position: it is the
    viewer's raster address, the input to the judgement anchor, and what the page-scope selection is
    expressed in. The two are different facts about the same page and the product needs both. The
    folio is None for a spreadsheet (a worksheet has no folio) and for any page whose footer the
    classifier could not read — 5 of 270 on the filing this was measured against — so every reader
    needs a fallback.
    """
    if p is None:
        return None
    return {
        "source_kind": p.source_kind, "page_index": p.page_index,
        "printed_page": (folio_of(p.page_index) if folio_of is not None else None),
        "sheet": p.sheet, "cell": p.cell, "label_cell": p.label_cell,
        "bbox": (p.bbox.model_dump() if p.bbox is not None else None),
        "label_bbox": (p.label_bbox.model_dump() if p.label_bbox is not None else None),
        "text_snippet": p.text_snippet,
    }


def _serialize_notes(doc_model) -> list[dict]:
    """Extracted note detail tables → view/export shape: each note with its own breakdown
    rows (label + period values) and provenance."""
    folio_of = _folio_lookup(doc_model)
    notes = []
    for nt in doc_model.notes:
        rows = []
        for it in nt.items:
            values = [{
                "period_label": ev.period_label,
                # The printed column header ("31 December 2024"), carried the same way the face
                # serializer carries it above. Without it a note table's own column dates were
                # dropped on the way to the API, so the Notes screen could only ever fall back to
                # Current/Prior even for Excel and date-banded PDFs.
                "period_display": ev.period_display,
                "basis": ev.basis.value,
                "value": (str(ev.value) if ev.value is not None else None),
                # THE PER-VALUE FLAGS, which the face serializer has always carried and this one
                # dropped. A note's column can now be read from a TWO-LEVEL header (a period band
                # over a measure band — `row_reconstruct.GRID_FLAG`), and that is an
                # interpretation a reviewer has to be told about on the figure it produced. Without
                # this the call-out was raised on the value and served to nobody.
                "flags": list(ev.confidence.flags),
                "provenance": _prov_dict(ev.provenance, folio_of),
            } for ev in it.values.values()]
            # Carry the row's role (line/subtotal/total) and mapping confidence so the notes
            # detail renders subtotal/total emphasis and a per-row confidence badge.
            supports_face_key = next((flag.split(":", 1)[1]
                                      for flag in it.confidence.flags
                                      if flag.startswith("supports_face_item:")), None)
            rows.append({"label": it.raw_label, "role": it.role.value,
                         "canonical_key": it.canonical_key,
                         "supports_face_key": supports_face_key,
                         "flags": list(it.confidence.flags),
                         "confidence": it.confidence.overall, "values": values,
                         # THE SUB-HEADING, AND WHETHER THIS ROW'S CAPTION IS ITS OWN.
                         #
                         # A note's block subtotal is printed on a bare line, so reconstruction
                         # gives it the block's heading — a caption that appears nowhere on the page
                         # in that position. Served unmarked it is indistinguishable from a printed
                         # one, which is a small lie to the reader; and `note_block_subtotals` names
                         # its block by this same `group_hint`, so without it a consumer of that
                         # list has no key to find the block or its rows in this payload.
                         "group": it.group_hint,
                         "caption_borrowed": it.caption_borrowed,
                         "component_ordinals": list(it.component_ordinals),
                         "ordinal": it.ordinal})
        page = (nt.source_pages[0] if nt.source_pages else 0)
        # ``page`` stays the 1-based SHEET position: the Notes screen turns it straight back into an
        # index to scroll the viewer and to size the page stack. ``printed_page`` is the folio, a
        # sibling for the citation, and None when the classifier could not read one.
        notes.append({"no": nt.note_number, "title": nt.title, "page": page + 1,
                      "printed_page": (folio_of(page) if folio_of is not None else None),
                      "rows": rows})
    return notes


class ExtractionOptions(BaseModel):
    template_version_id: str | None = None
    # THE CONFIGURATION THIS RUN IS PINNED TO — a ``line_item_versions`` row, and the only kind of
    # configuration there is. This field was ``ontology_version_id``; the ontology is no longer a
    # stored, selectable or user-visible thing, so there is nothing else a caller could name.
    # Breaking on purpose, and the break is not silent: the old key names no field, so a caller
    # still sending it has pinned nothing, and the run then resolves the set IN FORCE for its
    # template (:func:`resolve_configuration_id`), PINS it, and reports it by key and version in
    # the ``rulebook`` block of every response. A caller that meant an older version can see from
    # that block that it did not get one, which is what the block is for.
    line_item_version_id: str | None = None
    basis: list[str] = []
    target_currency: str | None = None
    target_units: int | None = None
    # Whether the user asked to review/adjust detected page scope before extraction. Auto (False)
    # when not stated: detect pages and extract in one pass.
    #
    # `None` rather than `False` as the default so NOT STATED is distinguishable from STATED FALSE.
    # It is the difference between a screen mounting ("make sure this filing has been extracted")
    # and the Scope screen asking for a confirm-scope run, and the idempotency check in
    # ``start_extraction`` compares only what the caller actually stated. Stored normalised, so a
    # run's options always carry a concrete bool.
    confirm_scope: bool | None = None
    # Entity name used to mint the run id (entity-slug + timestamp). Falls back to the
    # document filename when omitted.
    entity: str | None = None
    # RUN IT AGAIN even though this document already has a run on these same pins.
    #
    # Without this, POSTing an extraction is IDEMPOTENT per (document, template, configuration) —
    # see ``start_extraction`` — which is what a screen that fires the POST on arrival needs it to
    # mean.
    # Re-extracting is the one case where the caller means "another run of the same thing", and it
    # has to be able to say so, or the two intentions are indistinguishable at the endpoint.
    force: bool = False


def _satisfies(existing, body) -> bool:
    """Whether a run this document ALREADY has answers THIS request — the idempotency test
    ``start_extraction`` applies.

    JUDGED ON WHAT THE CALLER ACTUALLY STATED, and nothing else. A request that pins nothing is
    asking "make sure this filing has been extracted", and any run of it answers that; a request
    that pins a template is asking for that template specifically.

    Comparing the RESOLVED options instead was the first version, and it is wrong in a way that only
    shows up in use: the template default resolves to the latest one stored, so publishing a
    template while a run was working would make an arriving screen's empty request look different
    from the run already in flight — and the reader would be handed a conflict instead of their
    extraction. Same for ``confirm_scope``: a run started from the Scope screen carries True, and a
    screen merely mounting sends nothing, which must not read as a request for a different run.

    Every field here steers the pipeline, so a caller who names one is asking for it. ``entity`` is
    absent because it only names the run id, and ``force`` because it is the control that asks for
    this test to be skipped.
    """
    opts = existing.options or {}
    if body.template_version_id and body.template_version_id != _run_template_id(existing):
        return False
    if body.line_item_version_id \
            and body.line_item_version_id != _run_line_item_version_id(existing):
        return False
    if body.confirm_scope is not None \
            and bool(body.confirm_scope) != bool(opts.get("confirm_scope")):
        return False
    if body.basis and list(body.basis) != list(opts.get("basis") or []):
        return False
    if body.target_currency and body.target_currency != opts.get("target_currency"):
        return False
    if body.target_units is not None and body.target_units != opts.get("target_units"):
        return False
    return True


def resolve_template_id(session: Session, pinned_template_id: str | None,
                        pinned_line_item_id: str | None = None) -> str | None:
    """Which template lays out this run's spread: the caller's pin, else THE LATEST ONE STORED.

    THE DEFAULT USED TO BE THE SHIPPED TEMPLATE, and that is the whole reason this function exists.
    A run that pinned nothing resolved its configuration against ``shipped_template_key()``, so an
    uploaded template never became the default: it had to be pinned on every single run, and any
    run where that was forgotten mapped the filing against the shipped HKFRS spread instead —
    succeeding, and quietly laying the figures out on a grid nobody chose. Reported by the person
    it happened to: a template and a 400-item configuration uploaded and tested against for days,
    while runs that omitted the pin were reading neither.

    "Latest" is the newest VERSION OF ANY TEMPLATE, not the newest key to appear: re-uploading a
    revision of an older template makes that template current again, which is what an author
    editing a spread means by publishing it. Ordered on ``created_at`` with ``version`` and ``id``
    behind it, because two versions published inside one clock tick would otherwise resolve on
    whatever order the database felt like returning — the same insertion-order dependence
    ``services.config_select`` was written to remove from the configuration half.

    A PINNED CONFIGURATION STILL DECIDES ITS OWN TEMPLATE. If the caller pinned a line-item set but
    no template, the answer is the newest version of the template that set is WRITTEN for, not the
    newest template overall — otherwise defaulting the template would manufacture the very mismatch
    ``start_extraction`` refuses, out of a request that named only one of the two. The pair agrees
    by construction, and ``pin_mismatch`` goes on meaning what it says: a contradiction the CALLER
    stated, not one this resolver introduced.

    Returns None only when nothing is stored at all, which is a legitimate state — the template is
    optional, and ``_template_for_run`` deliberately has no read-time fallback, so a run with no
    template serves no template-derived findings rather than findings from a substituted one.
    """
    from app.db.models import LineItemVersion, TemplateVersion

    if pinned_template_id:
        return pinned_template_id

    q = select(TemplateVersion)
    if pinned_line_item_id:
        # ``line_item_versions``, the ONE configuration store — the ``ontology_versions`` row this
        # used to read is gone, and with it the second engine a run could be pinned to.
        cfg = session.get(LineItemVersion, pinned_line_item_id)
        target = (cfg.target_template_key or "") if cfg is not None else ""
        if target:
            q = q.where(TemplateVersion.template_key == target)
    row = session.execute(
        q.order_by(TemplateVersion.created_at.desc(), TemplateVersion.version.desc(),
                   TemplateVersion.id.desc()).limit(1)
    ).scalars().first()
    return row.id if row is not None else None


def resolve_configuration_id(session: Session, pinned_line_item_id: str | None,
                             pinned_template_id: str | None) -> str | None:
    """Which LINE-ITEM SET this run reads the filing against, when the caller pinned none.

    ONE ENGINE, so there is one question left to ask here: which stored VERSION of the
    configuration. The ontology this used to resolve is not a stored, selectable thing any more —
    ``line_item_versions`` is the whole of it — and nothing here should grow a second store to
    choose from again.

    A run that names no configuration used to map against NOTHING: ``_run_extraction_task`` left
    the matcher's view ``None``, so no caption resolved to a line item, no line item carried a
    section, and the spread came back as unmapped rows — while the comment beside the pin said "a
    run naming none is read by the shipped default". It was not. Every plain extraction — which is
    what the upload screen sends — produced a filing with nothing recognised in it.

    So the set IN FORCE is resolved here and PINNED ON THE RUN, which is the part that makes this a
    defensible default rather than the substitution this codebase removed elsewhere: nothing is
    guessed at read time. ``_template_for_run`` deliberately has no fallback because findings
    attributed to a template the analyst never chose are worse than absent ones — nothing on the
    screen says where they came from. Here the run stores the id, :func:`configuration_record`
    reports the key and version, and the Workspace names it, so the analyst can see exactly which
    configuration produced the figures and pin a different one.

    A caller's own pin always wins, including the legitimate case of reproducing an earlier spread
    with a configuration that has since been superseded by a newer one. Only the absence of a pin
    is filled in.
    """
    from app.db.models import TemplateVersion
    from app.sample.reference import shipped_template_key

    if pinned_line_item_id:
        return pinned_line_item_id
    # The template the run is laid out on decides which configuration is in force for it, so the
    # pair cannot disagree — the ``pin_mismatch`` check below then holds by construction.
    template_key = ""
    if pinned_template_id:
        tpl = session.get(TemplateVersion, pinned_template_id)
        template_key = tpl.template_key if tpl is not None else ""
    if not template_key:
        template_key = shipped_template_key()
    chosen = _in_force_for_template(session, template_key) if template_key else None
    return chosen.id if chosen is not None else None


def _in_force_for_template(session: Session, template_key: str):
    """The line-item set in force for a template — the extractor's own choice, never a second copy
    of that rule (see ``services.config_select``, which replaced ``services.ontology_select``)."""
    from app.services.config_select import select_for_template

    return select_for_template(session, template_key) if template_key else None


def configuration_record(session: Session, line_item_version_id: str | None) -> dict:
    """WHICH CONFIGURATION this run reads the filing against, decided once, when the run starts.

    Recorded on the run because the alternative — a reader re-deriving it later — is what made
    reloading the extraction view an audit failure: the client asked for the configuration IT
    thought was in force, ran the filing against an older one, and labelled the result as the
    configuration in force. Which configuration produced a figure is part of the figure. A run
    states it, and every view reports the stated value instead of guessing again.

    ``status`` is the whole claim in one word: ``in_force`` (this WAS the set in force for its
    template when the run started), ``pinned`` (a stored set, but not the one in force —
    reproducing an earlier spread), ``engine_default`` (the run named no configuration, so nothing
    in the filing was recognised) or ``missing`` (the id named no stored set).

    ``superseded`` IS GONE, with the sibling lookup that computed it. It answered "has some other
    stored definition, or the repo's retirement list, DECLARED this key replaced" — metadata
    labelling that explicitly did not decide what runs, sourced from ``metadata.supersedes`` on the
    ontology and consumed only by the ontology API field and a frontend badge, both of which go
    with the ontology surface. Selection is "the latest stored set wins"
    (``config_select.select_for_template``), so the two could BOTH be true of one row and the
    record then contradicted itself — "this run used X, which has been replaced; the set in force
    is X". What a reader needs is ``in_force`` and the key/version of whatever IS in force, which
    are both still here. Do not reinstate a second, declarative answer to "is this current".

    WHY THE WIRE KEY IS STILL ``rulebook``. This dict is stored as ``options["rulebook"]`` and
    served as ``result["rulebook"]``, and ``routes.documents``, ``scripts/run_filing.py`` and the
    frontend all read it under that name. "Rulebook" says nothing about an ontology — it is the
    configuration a run was read against — so renaming the key would be churn across three
    consumers for no gain to anybody who can see it.
    """
    from app.db.models import LineItemVersion

    record = {
        "line_item_version_id": line_item_version_id or "",
        "line_items_key": "", "version": 0, "target_template_key": "",
        "status": "engine_default" if not line_item_version_id else "missing",
        "in_force": False,
        "in_force_line_items_key": "", "in_force_version": 0,
    }
    if not line_item_version_id:
        return record
    row = session.get(LineItemVersion, line_item_version_id)
    if row is None:
        return record

    chosen = _in_force_for_template(session, row.target_template_key)
    in_force = chosen is not None and chosen.id == row.id
    record.update({
        "line_items_key": row.line_items_key, "version": row.version,
        "target_template_key": row.target_template_key,
        "status": "in_force" if in_force else "pinned",
        "in_force": in_force,
        "in_force_line_items_key": chosen.line_items_key if chosen is not None else "",
        "in_force_version": chosen.version if chosen is not None else 0,
    })
    return record


# ``(row id, created_at) -> the first reason it will not load, or None``. Keyed on the stamp as well
# as the id so a row rewritten in place re-probes instead of answering from a stale entry.
_LOADABILITY: dict[tuple, str | None] = {}


def _first_load_error(exc: Exception) -> str:
    """One line naming WHERE and WHAT, out of a pydantic error set that can run to hundreds.

    ``str(exc)`` on a set this size is ~1,400 lines, which is not a log line and not something to
    put on the wire. The first error plus a count is enough to recognise the fault and go looking.

    Moved here from the deleted ``routes.ontologies``: the refusal below is the last reader that
    needs it, and it was not worth a module of its own.
    """
    errors = getattr(exc, "errors", None)
    found = []
    if callable(errors):
        try:
            found = list(errors())
        except Exception:  # noqa: BLE001 — not a pydantic error after all; fall through to str()
            found = []
    if found:
        first = found[0]
        where = ".".join(str(p) for p in (first.get("loc") or ()))
        more = f" (and {len(found) - 1} more)" if len(found) > 1 else ""
        return f"{where}: {first.get('msg', '')}{more}".lstrip(": ")
    return str(exc).strip().splitlines()[0]


def probe_configuration_load(row) -> str | None:
    """``None`` when this stored line-item set still loads, else the first reason it does not.

    THE ONE PROBE. It answered ``loads`` on the ontology picker, the boot-time warning and the
    refusal below from a single function in ``routes.ontologies``, precisely so a screen, a log and
    a refusal could never name different rows as broken. That module is gone and line items is the
    single configuration engine, so the function lives here with the refusal that most needs it and
    the ``/line-items`` reader imports it — one answer, three readers, as before.

    ``resolve=True`` because the question is "would a RUN be able to map with this row", and the
    extraction path folds the section layer in (``_run_extraction_task``): a set whose ``inherits``
    no longer names anything is just as unusable as one the schema refuses.

    Cached per row, so polling an endpoint that consults it pays the load once rather than per
    request. A test that rewrites a stored definition in place clears ``_LOADABILITY``.
    """
    from app.schemas.line_items import load_line_item_set

    key = (row.id, getattr(row, "created_at", None))
    if key not in _LOADABILITY:
        try:
            load_line_item_set(row.definition or {}, resolve=True)
            _LOADABILITY[key] = None
        except Exception as exc:  # noqa: BLE001 — any failure to load is the answer, whatever it is
            _LOADABILITY[key] = _first_load_error(exc)
    return _LOADABILITY[key]


def _run_extraction_task(run_id: str, object_key: str, filename: str, options: dict,
                         entity: str, provider: str, model_fallback: str,
                         included_pages: list[int] | None = None,
                         started_at: str | None = None) -> None:
    """Run the pipeline off the request thread and record the outcome on the run row. Opens
    its own DB session + object store (the request's are gone by the time this executes).

    ``started_at`` is the run row's ``created_at`` as an ISO string — the instant the run started,
    passed down so the whole run is timed by one clock. A string rather than a ``datetime`` because
    every other argument here is one a JSON task queue can carry, and the broker swap this task was
    shaped for (``services/documents``) is not supposed to change the signature. Optional, because a
    caller outside the route (a re-run script, a probe) has no queued instant to hand over.
    """
    from app.config import get_settings
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun, LineItemVersion, TemplateVersion
    from app.schemas.line_items import load_line_item_set
    from app.services.working_view import build_working_view

    began = datetime.fromisoformat(started_at) if started_at else datetime.now(timezone.utc)
    progress: _RunProgress | None = None
    session = SessionLocal()
    try:
        # Assembling the recorder assembles the pipeline (for its stage list), so it happens INSIDE
        # the failure path: a stage module that will not import would otherwise kill this task with
        # the run row untouched — left at `running` for a client to poll for ever.
        progress = _RunProgress(run_id, began)
        settings = get_settings()
        store = LocalObjectStore(settings.object_store_root)
        # THE CONFIGURATION THIS RUN WAS PINNED TO, read from the ONE store there is. This used to
        # load an ``ontology_versions`` row through ``loader.load_ontology``; the ontology is no
        # longer stored, selectable or user-visible, so the run reads its ``LineItemVersion`` and
        # ``working_view.build_working_view`` derives the view the matcher asks its questions of.
        # The matching MECHANISM is unchanged and still takes an ``OntologyDefinition`` (hence
        # ``run_extraction(ontology=...)``, an internal parameter name), but its INPUT is now the
        # line-item set — see ``services.working_view`` for the parity measurements.
        working_view = None
        lid = options.get("line_item_version_id")
        if lid:
            cfg_row = session.get(LineItemVersion, lid)
            if cfg_row is not None:
                # RESOLVED, because this is the one call site whose result actually maps a filing,
                # and ``services.working_view`` measured what the fold is worth: `resolve=True`
                # differs from `resolve=False` on exactly one field, `note_use_rationale`, on 394 of
                # 462 items, where unresolved loses it and resolved agrees with what the reviewer
                # workbook publishes. Everything else is identical because the items carry their
                # resolved values already. ``load_line_item_set`` resolves by default; stated
                # explicitly here because this is the call site where it matters.
                #
                # UNGUARDED, deliberately, and ``start_extraction`` is what makes that safe: a set
                # that will not load is refused at the door (see the refusal there), so a failure
                # here is a run created outside the route and belongs in `failed` with the reason
                # in its logs rather than silently mapping the filing against nothing.
                st = load_line_item_set(cfg_row.definition, resolve=True)
                working_view = build_working_view(st)
        # The template is the run's target definition; the structural stage validates the
        # extraction against the rollups and identities it declares.
        template = None
        tid = options.get("template_version_id")
        if tid:
            tpl_row = session.get(TemplateVersion, tid)
            if tpl_row is not None:
                try:
                    template = load_template(tpl_row.definition)
                except Exception:  # noqa: BLE001 — a bad stored template must not fail the run
                    template = None

        data = store.get(object_key)
        # ``ontology=`` is ``services.documents.run_extraction``'s own parameter name for the
        # matcher's working view. Internal and left alone on purpose: what it is HANDED is the view
        # built from the line-item set above, and renaming three thousand lines of matcher was
        # never the point.
        doc_model, ctx = run_extraction(data, filename=filename, ontology=working_view,
                                        included_pages=included_pages, template=template,
                                        progress_cb=progress, context_cb=progress.observe,
                                        step_cb=progress.step)
        run = session.get(ExtractionRun, run_id)
        if run is None:
            return
        if run.status == "canceled":
            return

        # Presence scan for qualitative disclosures (auditor qualification, contingent
        # liabilities, guarantees, …) over the document text — stored on the run. The same
        # page text yields the entity name shown at the top of the extraction/statement.
        from app.services.derived import detect_entity_name, document_text, scan_disclosures
        entity_name = None
        try:
            pages_text = document_text(data, doc_model.fmt.value)
            disclosures = scan_disclosures(pages_text, folio_of=_folio_lookup(doc_model))
            entity_name = detect_entity_name(pages_text)
        except Exception:  # noqa: BLE001 — a scan failure must not fail the extraction
            disclosures = []
        # THE CONTINGENT-LIABILITY WORKING RIDES WITH ITS OWN DISCLOSURE. The presence scan says
        # only "found, page 209"; the working is the narrative behind it — what each disclosed
        # matter is, and a one-sentence account of every paragraph that fits no type. Folded in
        # here because `disclosures` is the one list that reaches the Excel sheet, the JSON export,
        # /analysis and the Disclosures screen, so the reasoning arrives everywhere the disclosure
        # does. Runs after the pipeline, so `doc_model.contingent_liabilities` is populated.
        from app.services.contingent_liabilities import attach_contingent_explanation
        disclosures = attach_contingent_explanation(disclosures, doc_model.contingent_liabilities)

        recon = doc_model.reconciliation
        structural = doc_model.structural
        # The configuration decision recorded when the run was created, carried onto the result
        # with whether a line-item set was actually READ for it.
        #
        # WHAT `applied` DISTINGUISHES, precisely, because the comment here used to claim more than
        # the field can carry: it said "whether that definition actually LOADED", and one that does
        # NOT load cannot reach this line at all. The load at the top of this function is
        # deliberately unguarded, so a definition today's schema refuses raises there and control
        # goes to `except BaseException` below with the run written `failed` — no result, no
        # `applied` — and ``start_extraction`` now refuses such a pin at the door before any run is
        # minted, so it is not even reached. So False means ONE thing: this run had no configuration
        # to read — it named none (`status: engine_default`), or the id it named matches no stored
        # row (`status: missing`). Both of those mean nothing was recognised in the filing, which is
        # the claim worth carrying onto the result.
        rulebook = dict((run.options or {}).get("rulebook") or {})
        if not rulebook:
            # A run created outside the route (a re-run script, a seed) still has to say which
            # configuration produced its figures, so the record is made here rather than left blank.
            rulebook = configuration_record(session, lid)
        # `working_view is not None` and nothing more: the set was found and read. It is not a
        # verdict on the configuration's quality, and — see above — it can no longer be a report of
        # a load failure, because a load failure has no result to report on.
        rulebook["applied"] = working_view is not None
        base_rows = _serialize_rows(doc_model, working_view)
        template_def = template.model_dump(mode="json") if template is not None else None
        supplemental_rows = _build_supplemental_rows(
            template_def=template_def,
            base_rows=base_rows,
            disclosures=disclosures,
            entity_name=entity_name,
            doc_model=doc_model,
            ctx=ctx,
            options=options,
        )

        run.result = {
            "locale": doc_model.locale,
            # Which configuration produced these figures — stated by the run, never re-derived by a
            # reader (see :func:`configuration_record`).
            "rulebook": rulebook,
            "format": doc_model.fmt.value,
            "filename": filename,
            "entity": entity_name,
            "pages": [p.model_dump(mode="json") for p in doc_model.pages],
            "page_count": len(doc_model.pages),
            "line_item_count": len(doc_model.line_items),
            "notes": len(doc_model.notes),
            "rows": [*base_rows, *supplemental_rows],
            "note_details": _serialize_notes(doc_model),
            "disclosures": disclosures,
            # The contingent-liability working IN FULL, per basis:period — the paragraph, the
            # matters as disclosed and the unclassified statements, exactly as the disclosure stage
            # read them. The disclosure entry above carries a reduction of this for the one period
            # a document-level disclosure is about; this is the whole of it, for a machine consumer
            # and for the other period. It was read on every run and never serialised, so nothing
            # downstream could see the narrative a reviewer is meant to read.
            "contingent_liabilities": doc_model.contingent_liabilities,
            "reconciliation": ([e.model_dump(mode="json") for e in recon.entries] if recon else []),
            # A note's OWN arithmetic: a block subtotal the filing printed on a bare line, against
            # the rows above it. Served separately from `reconciliation` because it is a different
            # claim — no face line takes part, and it is evaluated whether or not any face line
            # cites the note. Serialized rather than only logged: the §20 split re-derives a block's
            # total by summing the block's details, and the whole point of recovering the printed
            # figure is that a disagreement reaches a person instead of a log line nobody reads.
            "note_block_subtotals": ([c.model_dump(mode="json")
                                      for c in recon.note_block_subtotals] if recon else []),
            # Template-structure validation: relations checked (pass/fail) AND the ones that
            # could not be checked, so partial coverage is visible rather than implied.
            "structural": ([r.model_dump(mode="json") for r in structural.results]
                           if structural else []),
            # Leftover lines a model placed in a section's Others to reconcile a printed subtotal
            # with its components — kept so the routing is inspectable, not silent.
            "gap_routings": list(doc_model.gap_routings or []),
            # Which of the eight analyst buckets each face row and each note belongs to. Membership
            # only — the figures live once, on ``rows`` and ``note_details``, and the buckets
            # endpoints join to them at serve time (services/buckets.py).
            "buckets": (doc_model.buckets.model_dump(mode="json")
                        if doc_model.buckets else None),
            "units": (doc_model.unit_context.model_dump(mode="json")
                      if doc_model.unit_context else None),
            # How mapping ran. Surfaced (not just logged) so a deterministic-only run — the
            # fallback when no LLM is configured — is visibly weaker rather than silently so.
            "mapping": {
                "strategy": ctx.mapping_strategy or "deterministic",
                "reason": ctx.mapping_strategy_reason,
                "llm_calls": ctx.llm_calls,
                "model": ctx.llm_model or "",
            },
        }
        run.status = "succeeded"
        run.progress = progress.settle("done")
        run.logs = "\n".join(ctx.logs)
        session.commit()

        _maybe_cache_credit_narrative(session, run, doc_model.locale or "en", entity_name)
        _maybe_cache_netting(session, run, doc_model.locale or "en")

        used_llm = ctx.llm_calls > 0
        audit_svc.record(run.document_id, audit_svc.AuditEntry(
            run_id=run_id, entity=entity, action="extraction",
            provider=provider, model=ctx.llm_model or model_fallback,
            input_tokens=ctx.llm_input_tokens if used_llm else None,
            output_tokens=ctx.llm_output_tokens if used_llm else None,
            status="succeeded",
            # Timed from `began`, the run row's own created_at — the same instant the progress
            # records are timed from, so the trail and the live gauge cannot disagree about when the
            # run started. It is the WHOLE run, pipeline and persistence both, which is the duration
            # a reader asking "how long did this take" means.
            duration_ms=audit_svc.elapsed_ms(began),
        ))
    except RunCanceled:
        # AN ORDERLY STOP, NOT A FAILURE, and it must not be filed as one. The row already says
        # `canceled` — that is what the pipeline noticed — the user knows why, and nothing is wrong
        # with the filing or the code. The row is left untouched: `cancel_run` published the phase
        # against the stage that was actually in flight, which is more informative than a terminal
        # record written later.
        #
        # The audit entry IS written, and as "canceled" rather than "failed": a cancelled run still
        # spent real provider budget on the batches it had already sent, so a trail that recorded
        # only runs which finished would under-report spend — but recording a deliberate
        # cancellation as a failure would misreport its cause. Not re-raised, because this is a
        # normal outcome and letting it escape a BackgroundTask would surface a traceback out of
        # the client's own POST for a button the user meant to press.
        session.rollback()
        canceled_run = session.get(ExtractionRun, run_id)
        audit_svc.record(
            canceled_run.document_id if canceled_run else "unknown", audit_svc.AuditEntry(
                run_id=run_id, entity=entity, action="extraction",
                provider=provider, model=model_fallback,
                input_tokens=None, output_tokens=None, status="canceled",
                duration_ms=audit_svc.elapsed_ms(began)))
    except BaseException as exc:  # noqa: BLE001 — record failure on the run, don't crash the worker
        # BaseException, not Exception, and that difference is the whole point. asyncio.CancelledError
        # has been a BaseException since Python 3.8 (its MRO is CancelledError -> BaseException), so
        # `except Exception` could not see it — and Starlette runs BackgroundTasks inside the
        # request/response cycle, where a cancellation is exactly what a client going away produces.
        # A cancelled run therefore left this function without touching the row: status stayed
        # `running`, result stayed null, no error was recorded, and nothing on the screen could tell
        # an ABANDONED run from a slow one. A 21-stage run that had already done 20 stages of work
        # then polled for ever. Recording it costs nothing and is re-raised below, so cancellation
        # still propagates and SystemExit/KeyboardInterrupt still stop the process.
        #
        # ROLL BACK FIRST. If the exception came from the commit itself — a result the JSON column
        # cannot take, say — the session is already in a failed transaction, and every statement on
        # it raises PendingRollbackError until it is rolled back. That included this handler's own
        # `session.get` below, so the handler died on the wreckage of the failure it exists to
        # record: no status written, no audit row, nothing in the log. A 21-stage run then looked
        # like it had silently evaporated, which is a far harder thing to diagnose than the
        # TypeError underneath it. Unconditional because rolling back a clean session is a no-op.
        session.rollback()
        run = session.get(ExtractionRun, run_id)
        if run is not None and run.status != "canceled":
            run.status = "failed"
            # Carries the stage that was in flight, so a failure reports WHERE it happened rather
            # than only that it happened. Without a recorder the pipeline never got as far as being
            # assembled, and the record says exactly that: no stages, none done.
            run.progress = (progress.settle("failed") if progress is not None else
                            _progress_payload("failed", 1.0, started_at=began, stage_count=0))
            # The exception ON TOP OF the stage trail, not instead of it. This used to assign the
            # exception alone, which discarded every `stage:*:start`/`:done` line the recorder had
            # been flushing — on the one kind of run where that trail is the whole diagnosis. A
            # failure that reports its own type and nothing about where the pipeline had got to
            # sends the reader back to reproduce it just to learn which stage it was.
            trail = list(getattr(progress, "ctx_logs", None) or [])
            # A cancellation carries no message of its own — str(CancelledError()) is "" — so a bare
            # "CancelledError: " would name the type and say nothing about what it means. Spell out
            # that the worker was abandoned rather than that the pipeline rejected the filing: the
            # two call for completely different responses from whoever reads this.
            detail = (f"{type(exc).__name__}: {exc}" if isinstance(exc, Exception) else
                      f"worker abandoned before finalizing ({type(exc).__name__}) — the run was "
                      f"cancelled out from under the pipeline, not refused by it; re-run it")
            run.logs = _log_tail([*trail, detail])
            session.commit()
        audit_svc.record(run.document_id if run else "unknown", audit_svc.AuditEntry(
            run_id=run_id, entity=entity, action="extraction",
            provider=provider, model=model_fallback,
            input_tokens=None, output_tokens=None, status="failed",
            # A failed run's duration is how long it ran BEFORE failing, which is the useful half of
            # the question: a pipeline that died at once and one that died after four minutes are
            # different problems.
            duration_ms=audit_svc.elapsed_ms(began),
        ))
        if not isinstance(exc, Exception):
            # Recorded, not swallowed. A cancellation must still cancel and SystemExit must still
            # exit — absorbing either here would trade a stranded run row for a process that ignores
            # shutdown, which is a worse bug than the one this handler exists to fix.
            raise
    finally:
        session.close()


@router.post("/documents/{document_id}/extractions", status_code=202,
             dependencies=[Depends(require(Permission.PIPELINE_RUN)), Depends(authorized_document)])
def start_extraction(
    document_id: str,
    body: ExtractionOptions,
    background: BackgroundTasks,
    session: Session = Depends(db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Kick off extraction as a background job. Returns 202 immediately with a 'running'
    run; the frontend polls GET /extractions/{run_id} (or /documents/{id}/run) until it
    reaches 'succeeded'/'failed'. Keeps the API responsive on large files without a
    separate worker/broker."""
    from app.db.models import Document, ExtractionRun, LineItemVersion, TemplateVersion

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Settle BOTH of this run's pins BEFORE it starts, and keep them on the run. The caller may
    # legitimately pin an older line-item set (reproducing an earlier spread), and the run says so
    # rather than letting the screen decide afterwards what it must have used.
    #
    # THE TEMPLATE FIRST, because it is what the configuration is scoped to. Defaulting it to the
    # latest stored template (``resolve_template_id``) is what makes an uploaded template the one a
    # run actually uses: the default was the SHIPPED template, so a run that pinned nothing laid the
    # spread out on the shipped grid however many templates had been uploaded since.
    template_version_id = resolve_template_id(session, body.template_version_id,
                                              body.line_item_version_id)
    line_item_version_id = resolve_configuration_id(session, body.line_item_version_id,
                                                    template_version_id)
    rulebook = configuration_record(session, line_item_version_id)

    # A pinned configuration and a pinned template have to be about the SAME template, and nothing
    # downstream would ever notice that they were not: the line-item set decides which line each
    # printed caption maps to and the template decides the grid those lines are laid out in, so this
    # pair maps every caption with a configuration validated against a template the spread never
    # uses. That is the established mechanism behind a cost-of-sales line turning up inside other
    # income — the run succeeds, and the spread is wrong with full confidence.
    #
    # Read off the configuration record above rather than fetching the row a second time, so "which
    # template is this configuration for" has one spelling. Refused before the integrity gate
    # because nothing about the DOCUMENT can make this pair valid; it is a contradiction in the
    # request. Pinning one of the two, or neither, stays legal — both fields are optional, a run
    # naming no configuration recognises nothing, and an id naming no stored row leaves
    # ``target_template_key`` empty, which is a missing pin rather than a conflicting one.
    # BOTH PINS MUST BE THE CALLER'S for this to be a contradiction in the request. The template is
    # now defaulted when the caller names none, and ``resolve_template_id`` derives that default
    # from a pinned set's own target — so a resolved-vs-pinned pair agrees by construction and can
    # never reach here. Testing the RESOLVED id instead would turn a request naming one of the two
    # into a 422 about a template the caller never chose.
    target_key = rulebook["target_template_key"]
    if body.template_version_id and target_key:
        tpl_row = session.get(TemplateVersion, body.template_version_id)
        if tpl_row is not None and target_key != tpl_row.template_key:
            raise HTTPException(status_code=422, detail={
                "error": "pin_mismatch",
                "message": (
                    f"Configuration {rulebook['line_items_key']!r} is written for template "
                    f"{target_key!r}, but this run pins template {tpl_row.template_key!r}. A run "
                    f"must map with the configuration written for the template that shapes its "
                    f"spread."),
                "template_key": tpl_row.template_key,
                # The key was ``ontology_target_template_key``; nothing a reader meets says
                # ontology any more, and there is only one configuration this can be about.
                "configuration_target_template_key": target_key,
            })

    # Enforce the integrity gate at the API boundary: a document with BLOCKER findings
    # (corrupt / encrypted / unreadable) cannot be extracted — refuse rather than return a
    # misleading "succeeded" empty run.
    report = doc.integrity_report or {}
    blockers = [f for f in report.get("findings", []) if f.get("severity") == "blocker"]
    if blockers:
        raise HTTPException(status_code=422, detail={
            "error": "integrity_blocked",
            "message": "This document did not pass the integrity check and cannot be extracted.",
            "blockers": [f.get("message") for f in blockers],
        })

    # --- ONE RUN PER REQUEST, NOT ONE PER ARRIVAL ----------------------------------------------
    # THE DEFECT THIS CLOSES. Nothing here used to look for an existing run, so every POST minted a
    # new one with a fresh `started_at` and launched a fresh pipeline. The extraction screen fires
    # this POST when it mounts, so navigating away and back — or reloading, or opening a second tab,
    # or double-clicking — started the filing over: a second pipeline racing the first, the LLM
    # tokens spent twice, and the reader's elapsed clock jumping back to zero because it was
    # honestly reporting a run that had just begun. The client held this together with a cached
    # query, which is not a guarantee: a cache is evicted on a timer and gone on a reload, and the
    # two-tab case it never covered at all.
    #
    # So the endpoint answers for itself. POSTing an extraction is IDEMPOTENT per (document,
    # resolved template, resolved configuration): asked for a run that already exists on the same
    # pins, it hands back THAT run rather than starting another. Re-extracting stays possible and
    # stays EXPLICIT — `force` is how a caller says "another run of the same thing", a different
    # intention from "make sure this filing has been extracted" and could not be told apart before.
    #
    # Resolved ids, not the request's: two callers naming the pins differently — one pinning
    # nothing, one pinning what the default resolves to — are asking for the same run, and comparing
    # the requests would miss that.
    existing = _latest_run(session, doc.id)
    if existing is not None:
        same_request = _satisfies(existing, body)
        if existing.status == "running":
            # A RUN IN FLIGHT IS NEVER RACED, force or not. The product has no notion of two
            # concurrent pipelines over one filing — they would write the same run rows twice and
            # the reader would be shown whichever finished last. Same pins: this is the run the
            # caller wants, so hand it over. Different pins: the caller is asking for the filing to
            # be read against other rules, which is a real request and cannot be answered by a run
            # already reading it against these — so it is refused, naming the run to wait for.
            if same_request:
                return {"run_id": existing.id, "status": existing.status,
                        "rulebook": (existing.options or {}).get("rulebook") or rulebook,
                        "progress_url": f"/api/v1/extractions/{existing.id}",
                        "adopted": True}
            raise HTTPException(status_code=409, detail={
                "error": "run_in_flight",
                "message": ("A run is already extracting this document. Wait for it to "
                            "finish before starting one with different options."),
                "run_id": existing.id,
            })
        # A SETTLED run on the same pins is the answer to "extract this document" — the spread is
        # already there. `force` is what distinguishes the re-extract button from a screen mounting.
        # A FAILED run is not an answer, so it is retried rather than handed back: the caller asked
        # for an extraction and does not have one.
        if same_request and existing.status == "succeeded" and not body.force:
            return {"run_id": existing.id, "status": existing.status,
                    "rulebook": (existing.options or {}).get("rulebook") or rulebook,
                    "progress_url": f"/api/v1/extractions/{existing.id}",
                    "adopted": True}

    # --- A CONFIGURATION THAT WILL NOT LOAD IS REFUSED AT THE DOOR ------------------------------
    # THE DEFECT THIS CLOSES. ``_run_extraction_task`` reads the run's configuration resolved and
    # UNGUARDED — deliberately, see the comment at that call — so a stored definition today's schema
    # refuses raises there, lands in the task's ``except BaseException``, and the run is written
    # `failed` after the recorder has been assembled and the file fetched. The caller got a 202 and
    # a run id, and the only account of what went wrong is a pydantic error in ``run.logs``.
    # MEASURED on the workspace database when this was written: 40 of the 42 stored configuration
    # rows failed to load with the same 358 validation errors, and the picker served all 42 — so
    # pinning one is an ordinary click, not a contrived request.
    #
    # REFUSED, never degraded to "map against nothing" the way the template load beside it is. The
    # two are not comparable: a template that will not load costs the run its structural checks,
    # while no configuration means no caption resolves to a line item at all (see
    # :func:`resolve_configuration_id`) — a completed 21-stage run that recognised NOTHING,
    # explained by one amber sentence. THIS IS A LIVE INVARIANT and it survived the merge of the
    # ontology into line items unchanged: the store it probes moved from ``ontology_versions`` to
    # ``line_item_versions``, the reason it exists did not. Refusing an unusable configuration where
    # there is a caller to tell is this codebase's own doctrine (the ``/line-items`` publish gate
    # refuses a set that recognises nothing; ``services.config_select.select_for_template``).
    #
    # PLACED AFTER THE ADOPT BLOCK, and not beside the ``pin_mismatch`` check above, which is the
    # part that makes this safe. 18 of the 19 succeeded runs in the workspace database are pinned to
    # a configuration that no longer loads; re-opening one of those spreads is a mount POST on the
    # same pins, and refusing it would make a past extraction unreadable to punish a definition that
    # nothing is about to load. This gate is about the run that is ABOUT TO START, so it sits
    # exactly where that run is about to be minted — an adopted run is never refused, a new one
    # never begins doomed.
    #
    # Probed through :func:`probe_configuration_load`, the ONE probe — the same function the
    # ``/line-items`` reader and the boot-time warning answer from — so the picker, the log and this
    # refusal can never disagree about which rows are broken. Cached per row, so a poll of this
    # endpoint pays the load once, not per request.
    #
    # The RESOLVED id, because that is the one the worker will read: a run whose configuration was
    # defaulted fails just as completely as one whose configuration was pinned. The message says
    # which of the two it is, so a caller that pinned nothing is not told to unpin something.
    if line_item_version_id:
        cfg_row = session.get(LineItemVersion, line_item_version_id)
        # A missing row is a DIFFERENT fault and is not this gate's business: `configuration_record`
        # already reports it as `missing`, and the run reads no configuration rather than failing.
        load_error = probe_configuration_load(cfg_row) if cfg_row is not None else None
        if load_error is not None:
            pinned = bool(body.line_item_version_id)
            named = f"{rulebook['line_items_key']!r} v{rulebook['version']}"
            raise HTTPException(status_code=422, detail={
                # The error CODE is unchanged: ``routes.documents`` reports the same fault under
                # this string and the frontend keys its message on it. "Rulebook" says nothing about
                # an ontology, so there is nothing here for a user to stop seeing.
                "error": "rulebook_unloadable",
                "message": (
                    (f"Configuration {named} cannot be loaded, so it would govern nothing in this "
                     f"run: {load_error}. Pin a configuration that loads, or republish this one."
                     if pinned else
                     f"The configuration in force for this template, {named}, cannot be loaded, "
                     f"so a run would recognise nothing in this filing: {load_error}. Republish "
                     f"it, or pin a configuration that loads.")),
                "line_item_version_id": line_item_version_id,
                "line_items_key": rulebook["line_items_key"],
                "version": rulebook["version"],
                # Whether the CALLER chose this configuration or it was defaulted for them — the two
                # need different actions from whoever reads the message.
                "pinned": pinned,
                "reason": load_error,
            })

    entity = body.entity or Path(doc.filename or "").stem or "document"
    run_id = audit_svc.make_run_id(entity)
    # A run has ONE start time. Stamped here and written to `created_at` as well as to the progress
    # record's `started_at`, rather than letting the column's own default stamp a second instant a
    # millisecond later — the elapsed time a screen shows must be measured from the same moment the
    # run says it began.
    started_at = datetime.now(timezone.utc)
    # ONE options dict, stored on the run AND handed to the worker. The worker used to be given
    # ``body.model_dump()`` while the row stored something else, so anything settled here — the
    # configuration resolved above, above all — was recorded on the run and then not used to produce
    # its figures. A run that says which configuration it read the filing against and did not read
    # it is worse than one that says nothing.
    run_options = {**body.model_dump(),
                   # A CONCRETE BOOL. The field is tri-state on the way in so that "not stated" can
                   # be told from "stated False" by the idempotency test above; a run's stored
                   # options must not carry that distinction onward — the worker and every later
                   # reader want the effective value.
                   "confirm_scope": bool(body.confirm_scope),
                   # THE RESOLVED IDS, not the request's, and for the same reason in both cases: a
                   # run must be able to say which template shaped its spread and which
                   # configuration produced its figures. ``_run_template_id`` and
                   # ``_run_line_item_version_id`` read the column OR this key, so leaving the
                   # request's None here would have one of them answering "no template" while the
                   # other named one.
                   "template_version_id": template_version_id,
                   "line_item_version_id": line_item_version_id,
                   # Under the key ``rulebook`` still — see :func:`configuration_record` for why
                   # that name stays while the ontology behind it is gone.
                   "rulebook": rulebook,
                   "stages": pipeline_stage_names()}
    run = ExtractionRun(
        id=run_id, document_id=doc.id,
        template_version_id=template_version_id,
        # THE PIN THAT MAKES THIS RUN REPRODUCIBLE, and the RESOLVED id rather than the request's:
        # a run must be able to say which configuration produced its figures, and "whatever was in
        # force at the time" is not an answer a later reader can reconstruct — the set in force
        # changes every time one is published. Was ``ontology_version_id``, against a store that no
        # longer exists; ``line_item_versions`` is the one place configuration lives.
        line_item_version_id=line_item_version_id,
        # The stage list THIS run will walk, recorded at the moment it is queued. Serving the
        # live pipeline's list instead would make an old run disagree with itself the next time
        # a stage is added: its frozen `stage_count`/`stages_done` would be measured against a
        # longer list, and a screen ticking stages off would show a finished run as incomplete.
        status="running", options=run_options,
        created_at=started_at,
        # The full progress shape from the first poll, not a two-key stub: a screen that reads
        # `stage_count` to draw its stage list must be able to draw it before the first stage
        # reports, and a client polling faster than the worker starts sees this row.
        progress=_progress_payload("queued", 0.0, started_at=started_at,
                                   stage_count=len(pipeline_stage_names())),
        result=None,
    )
    session.add(run)
    session.commit()

    background.add_task(_run_extraction_task, run_id, doc.object_key, doc.filename or "",
                        run_options, entity, settings.llm.provider, settings.llm.model,
                        doc.page_scope, started_at.isoformat())
    # The URL of the mechanism that ACTUALLY reports progress — the endpoint the client polls.
    # This field used to name `/extractions/{run_id}/stream`, a WebSocket route that exists
    # nowhere in this codebase: a GET on it 404s, no client ever read it, and the docs correction
    # that removed the same claim from the prose left it standing here in machine-readable form.
    # Pointing it at GET /extractions/{run_id} rather than deleting it keeps the response
    # self-describing for a caller that has only the response to go on, and there is now a test
    # holding this URL to being the one the API serves.
    return {"run_id": run_id, "status": "running", "rulebook": rulebook,
            "progress_url": f"/api/v1/extractions/{run_id}"}


@router.get("/extractions/{run_id}",
            dependencies=[Depends(require(Permission.EXTRACTION_VIEW))])
def get_run(run_id: str, session: Session = Depends(db),
            principal: Principal = Depends(current_principal)) -> dict:
    """One run's status, progress and result.

    AUTHENTICATED AND OWNERSHIP-SCOPED, which it was not. This route carried no dependency at all,
    and it now serves the pipeline's log tail as well as the result — so an unauthenticated caller
    could read a filing's extracted figures and the pipeline's own commentary on them, given a run
    id, and run ids are composed from the entity slug and a timestamp
    (``audit.make_run_id``/``start_extraction``) rather than being unguessable. Every other read
    of a document's data is behind ``authorized_document``; the run is the same data by another
    route.

    A run the caller may not see answers 404 rather than 403, for the reason
    ``authorized_document`` gives: existence must not leak across tenants.
    """
    from app.db.models import Document, ExtractionRun

    run = session.get(ExtractionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    # Ownership is a property of the DOCUMENT, so it is checked there — the same predicate
    # `authorized_document` applies, reached by run id instead of document id.
    doc = session.get(Document, run.document_id)
    if doc is None or not _can_access(doc, principal):
        raise HTTPException(status_code=404, detail="Run not found")
    # The recorded rulebook rides alongside the result so the view can name it from the FIRST poll
    # — while the run is still running, and even if it fails — instead of computing a candidate of
    # its own and labelling the run with that.
    return {"run_id": run.id, "status": run.status,
            # Null rather than a half-record for a run that predates this contract — see
            # :func:`_served_progress`.
            "progress": _served_progress(run.progress, run.status),
            "rulebook": (run.options or {}).get("rulebook"),
            # The two things a progress screen needs beside `progress` and cannot derive: WHICH
            # stages this run passes through, and what the pipeline has been saying while it works.
            # The stage list is the pipeline's own (:func:`pipeline_stage_names`) — never a copy kept
            # here or in the client, because a stage added to the pipeline would otherwise leave
            # every screen ticking off a list of stages that no longer describes a run.
            # THIS run's own stage list, as recorded when it was queued — never the live
            # pipeline's, which is a different question once a stage has been added. A run
            # queued before the list was recorded falls back to the live one, which is the
            # best answer available for it and matches what it was actually built from.
            "stages": (run.options or {}).get("stages") or pipeline_stage_names(),
            "log_tail": _log_tail((run.logs or "").splitlines()),
            "result": run.result}


@router.post("/extractions/{run_id}/cancel",
             dependencies=[Depends(require(Permission.PIPELINE_RUN))])
def cancel_run(run_id: str, session: Session = Depends(db),
               principal: Principal = Depends(current_principal)) -> dict:
    from app.db.models import Document, ExtractionRun

    run = session.get(ExtractionRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    doc = session.get(Document, run.document_id)
    if doc is None or not _can_access(doc, principal):
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        run.status = "canceled"
        run.progress = {**(run.progress or {}), "phase": "canceled"}
        run.logs = _log_tail([*(run.logs or "").splitlines(), "run:canceled_by_user"])
        session.commit()
    return {"run_id": run.id, "status": run.status,
            "progress": _served_progress(run.progress, run.status)}


@router.get("/documents/{document_id}/run-status",
            dependencies=[Depends(require(Permission.EXTRACTION_VIEW)),
                          Depends(authorized_document)])
def get_document_run_status(document_id: str, session: Session = Depends(db)) -> dict:
    """DOES THIS DOCUMENT HAVE AN EXTRACTION IN FLIGHT? — for its latest run, answered without a
    run id.

    Neither existing read can answer it. ``GET /documents/{id}/run`` refuses until a run has a
    RESULT (``documents.py::get_document_run`` — ``if run is None or not run.result``), which is
    exactly the state a running run is not in, and ``GET /extractions/{run_id}`` needs an id the
    caller may not have: a hard reload arrives with an empty query cache and no run id anywhere.
    So a screen loaded FRESH mid-run could report only what the 404 alone proved — "this document
    has not been extracted" — while the pipeline was working on it. That is the wrong answer, and
    it is the one that reads as "nothing happened".

    ``none`` IS AN ANSWER, served 200. "This document has never been extracted" is information the
    caller asked for, not a failure of the request; making it an error is what left one 404 standing
    for two different facts and forced every reader to guess which one it meant.

    The run's own ``status`` word is passed through rather than mapped onto an expected set: a
    status this route does not recognise is a run it cannot describe, and folding one into
    ``running`` or ``failed`` would be this endpoint inventing the answer.

    Deliberately NOT the result, the stage list or the log: this is the question a caller with no
    run id can ask, and ``run_id`` is what it hands back so the caller can then read the run itself
    through ``get_run`` — one shape for the run's detail, not a second half-copy of it here.
    """
    run = _latest_run(session, document_id)
    if run is None:
        return {"status": "none", "run_id": "", "progress": None}
    return {"status": run.status, "run_id": run.id,
            # Null rather than a half-record for a run that predates the progress contract — the
            # same rule :func:`get_run` applies (see :func:`_served_progress`), so the two reads of
            # one run cannot disagree about whether it has progress to report.
            "progress": _served_progress(run.progress, run.status)}
