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
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pathlib import Path

from app.api.deps import db, settings as get_settings_dep
# ``_latest_run`` is imported rather than re-spelled here: "the latest run for a document" is one
# ordering rule (``created_at`` descending), and a second copy of it would let this route and every
# other per-document read disagree about which run they are describing.
from app.api.routes.documents import _can_access, _latest_run, authorized_document
from app.config import Settings
from app.ports.object_store import LocalObjectStore
from app.schemas.loader import load_ontology, load_template
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
                      stage: str = "", stages_done: list[str] | None = None) -> dict:
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
        "started_at": _as_utc(started_at).isoformat(),
        "elapsed_ms": max(0, int((datetime.now(timezone.utc)
                                  - _as_utc(started_at)).total_seconds() * 1000)),
    }


# Every key an ``ExtractionProgress`` carries. Used to decide whether a STORED record is one of these
# records at all — see :func:`_served_progress`.
_PROGRESS_FIELDS = frozenset(_progress_payload(
    "", 0.0, started_at=datetime(1970, 1, 1, tzinfo=timezone.utc), stage_count=0))


def _served_progress(record: dict | None) -> dict | None:
    """A stored progress record, or None when the row does not carry this contract.

    Runs written before the contract existed hold ``{"phase": …, "pct": …}`` (or ``{}`` from the
    column default), and ``init_db`` uses ``create_all`` — so those rows are still on disk and still
    readable. Completing one here would mean inventing the stage count, the stage and the start time
    of a run whose pipeline is not recoverable, and ``ExtractionProgress`` declares every field
    required, so a screen would read ``undefined`` where the type promises a number. Saying "there is
    no progress record for this run" is the true answer; ``status`` still says how it ended.
    """
    if not record or not _PROGRESS_FIELDS.issubset(record):
        return None
    return record


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
            # Appended on every entry, a repeat included, so the count of stages behind this one is
            # the pipeline's own position and a stage run twice does not report the same index twice.
            self._entered.append(phase)
            # The emit precedes its stage, so the stage being announced is not yet done.
            self._write(self._payload(phase, pct, self._entered[:-1]))
        except Exception as exc:  # noqa: BLE001 — see above; reporting must not fail the run
            self._degraded(exc)

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
                                 stages_done=done)

    def _write(self, payload: dict) -> None:
        from app.db.base import SessionLocal
        from app.db.models import ExtractionRun

        session = SessionLocal()
        try:
            run = session.get(ExtractionRun, self.run_id)
            if run is None:
                return
            run.progress = payload
            # A moving WINDOW on the log, replaced each time rather than appended to: bounded, so a
            # thousand-stage-line run does not rewrite a growing column fourteen times, and the whole
            # log lands here anyway when the run settles. Before this the column stayed empty for the
            # entire run, so a screen watching a slow stage had nothing at all to show.
            run.logs = _log_tail(getattr(self._ctx, "logs", None))
            session.commit()
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
    """Evaluate the ontology's generic containment-netting policies against THIS extraction once,
    via the LLM, and cache the confirmed (resolved) rules on the run. The statement/export then
    apply the deterministic math from the cached decision — so a policy nets only where the model
    confirmed the containment, and per-request rendering stays fast. Best-effort and guarded."""
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


def _serialize_rows(doc_model, ontology=None) -> list[dict]:
    """Extracted line items in a view-friendly shape, each value with its provenance
    (sheet+cell for Excel, page+bbox for PDF) so the UI can show click-to-source.

    Three things about a row that were computed by the pipeline and then left behind travel with it
    from here, because every screen and every export reads THIS list:

    * ``printed_in`` — face or note. Not derivable downstream: face and note rows have the same
      shape, and a note's detail lines sum to a figure the face already reports, so adding the two
      together double-counts the filing.
    * ``bucket`` / ``bucket_label`` / ``section`` — which of the analyst sections the row belongs to
      (``services.buckets``). The segmentation already decided it; this joins the answer to the row
      instead of making every consumer re-derive it from the ontology.
    * ``notes`` — the note numbers this row CITES and that were actually extracted as note tables,
      resolved through the ``FaceNoteLink``s the link-notes stage built. A number the filing prints
      with no note behind it is not in the list, so the linkage cannot promise detail that is not
      there.
    """
    seg_of, label_of = _bucket_index(doc_model)
    notes_of = _linked_notes(doc_model)
    section_of = _section_index(ontology)
    rows = []
    for li in doc_model.line_items:
        values = []
        for ev in li.values.values():
            p = ev.provenance
            prov = None
            if p is not None:
                prov = _prov_dict(p)
            cv = ev.confidence
            values.append({
                "period_label": ev.period_label,
                "period_display": ev.period_display,  # real period-end date for headers, if any
                # Printed left-to-right position of a matrix's component column; null otherwise.
                "column_index": ev.column_index,
                "basis": ev.basis.value,
                "value": (str(ev.value) if ev.value is not None else None),
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
            "section": section_of.get(li.canonical_key or ""),
            "notes": notes_of.get(str(li.id), []),
            "role": li.role.value,
            "mapping_method": li.confidence.method,
            "mapping_confidence": li.confidence.mapping,
            "flags": list(li.confidence.flags),
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


def _section_index(ontology) -> dict[str, str]:
    """canonical_key → the template section id its concept was scoped to, for the rows that mapped.

    The bucket is what a reader wants; the section is what a reviewer needs when the bucket looks
    wrong, because it names the rulebook decision the bucket was derived from. Taken from the
    ontology the RUN was pinned to — the same object the segmentation read — so the two cannot
    disagree about which section a concept is in.
    """
    return {m.canonical_key: m.section_scope[0]
            for m in (getattr(ontology, "mappings", []) or []) if m.section_scope}


def _prov_dict(p):
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
    """
    if p is None:
        return None
    return {
        "source_kind": p.source_kind, "page_index": p.page_index,
        "sheet": p.sheet, "cell": p.cell, "label_cell": p.label_cell,
        "bbox": (p.bbox.model_dump() if p.bbox is not None else None),
        "label_bbox": (p.label_bbox.model_dump() if p.label_bbox is not None else None),
        "text_snippet": p.text_snippet,
    }


def _serialize_notes(doc_model) -> list[dict]:
    """Extracted note detail tables → view/export shape: each note with its own breakdown
    rows (label + period values) and provenance."""
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
                "provenance": _prov_dict(ev.provenance),
            } for ev in it.values.values()]
            # Carry the row's role (line/subtotal/total) and mapping confidence so the notes
            # detail renders subtotal/total emphasis and a per-row confidence badge.
            rows.append({"label": it.raw_label, "role": it.role.value,
                         "confidence": it.confidence.overall, "values": values})
        page = (nt.source_pages[0] if nt.source_pages else 0)
        notes.append({"no": nt.note_number, "title": nt.title, "page": page + 1, "rows": rows})
    return notes


class ExtractionOptions(BaseModel):
    template_version_id: str | None = None
    ontology_version_id: str | None = None
    basis: list[str] = []
    target_currency: str | None = None
    target_units: int | None = None
    # Whether the user asked to review/adjust detected page scope before
    # extraction. Defaults to auto (False): detect pages and extract in one pass.
    confirm_scope: bool = False
    # Entity name used to mint the run id (entity-slug + timestamp). Falls back to the
    # document filename when omitted.
    entity: str | None = None


def resolve_rulebook_id(session: Session, pinned_ontology_id: str | None,
                        pinned_template_id: str | None) -> str | None:
    """Which rulebook this run reads the filing against, when the caller pinned none.

    A run that names no rulebook used to map against NOTHING: ``_run_extraction_task`` left
    ``ontology = None``, so no caption resolved to a concept, no concept carried a section, and the
    spread came back as unmapped rows — while the comment beside the pin said "a run naming no
    rulebook is read by the shipped default". It was not. Every plain extraction — which is what the
    upload screen sends — produced a filing with nothing recognised in it.

    So the rulebook IN FORCE is resolved here and PINNED ON THE RUN, which is the part that makes
    this a defensible default rather than the substitution this codebase removed elsewhere: nothing
    is guessed at read time. ``_template_for_run`` deliberately has no fallback because findings
    attributed to a template the analyst never chose are worse than absent ones — nothing on the
    screen says where they came from. Here the run stores the id, ``rulebook_record`` reports the key
    and version, and the Workspace names it, so the analyst can see exactly which rulebook produced
    the figures and pin a different one.

    A caller's own pin always wins, including the legitimate case of reproducing an earlier spread
    with a superseded rulebook. Only the absence of a pin is filled in.
    """
    from app.db.models import TemplateVersion
    from app.sample.reference import shipped_template_key

    if pinned_ontology_id:
        return pinned_ontology_id
    # The template the run is laid out on decides which rulebook is in force for it, so the pair
    # cannot disagree — the ``pin_mismatch`` check below then holds by construction.
    template_key = ""
    if pinned_template_id:
        tpl = session.get(TemplateVersion, pinned_template_id)
        template_key = tpl.template_key if tpl is not None else ""
    if not template_key:
        template_key = shipped_template_key()
    chosen = _in_force_for_template(session, template_key) if template_key else None
    return chosen.id if chosen is not None else None


def _in_force_for_template(session: Session, template_key: str):
    """The rulebook in force for a template — the extractor's own choice, never a second copy
    of that rule (see ``services.ontology_select``)."""
    from app.services.ontology_select import select_for_template

    return select_for_template(session, template_key) if template_key else None


def rulebook_record(session: Session, ontology_version_id: str | None) -> dict:
    """WHICH rulebook this run reads the filing against, decided once, when the run starts.

    Recorded on the run because the alternative — a reader re-deriving it later — is what made
    reloading the extraction view an audit failure: the client asked for the rulebook IT thought
    was in force, ran the filing against a superseded one, and labelled the result as the rulebook
    in force. Which rulebook produced a figure is part of the figure. A run states it, and every
    view reports the stated value instead of guessing again.

    ``status`` is the whole claim in one word: ``in_force`` (this WAS the rulebook in force for its
    template when the run started), ``superseded`` (a stored rulebook declares it replaced — a
    legitimate thing to pin, never a thing to call current), ``pinned`` (live, but not the one in
    force), ``engine_default`` (the run named no rulebook, so it maps against none and its pages
    are read by the shipped default) or ``missing`` (the id named no stored rulebook).
    """
    from app.db.models import OntologyVersion
    from app.services.ontology_select import rulebooks_for_template, superseded_keys

    record = {
        "ontology_version_id": ontology_version_id or "",
        "ontology_key": "", "version": 0, "target_template_key": "",
        "status": "engine_default" if not ontology_version_id else "missing",
        "in_force": False,
        "in_force_ontology_key": "", "in_force_version": 0,
    }
    if not ontology_version_id:
        return record
    row = session.get(OntologyVersion, ontology_version_id)
    if row is None:
        return record

    siblings = rulebooks_for_template(session, row.target_template_key)
    superseded = row.ontology_key in superseded_keys(siblings)
    chosen = _in_force_for_template(session, row.target_template_key)
    # BEING IN FORCE WINS OVER THE REPLACEMENT LABEL, because the two describe different things and
    # only one of them decides what runs.
    #
    # Selection is now "the latest stored rulebook wins" (``ontology_select.select_for_template``),
    # while ``superseded`` still answers "has some stored rulebook, or the repo's retirement list,
    # declared this key replaced". Those can BOTH be true of one row: an admin re-uploads a rulebook
    # under a key that another stored rulebook says it supersedes, or under one the repo retired, and
    # that upload is now the newest thing stored — so it is what the next run maps against, whatever
    # an older declaration says about the name.
    #
    # Reporting `superseded` in that state produced a sentence that contradicted itself: "this run
    # used X, which has since been replaced — the rulebook in force is X". Naming one rulebook as
    # both the replaced one and the current one tells a reviewer nothing and discredits the rest of
    # the record. The rulebook that RUNS is in force; that is what "in force" means. A declared
    # replacement is only worth reporting about a rulebook that is NOT the one running, which is
    # exactly the case it was added for — a run pinned to an older rulebook someone has replaced.
    #
    # The flag is still served beside the status, because "in force, and its key was retired" is a
    # true and mildly interesting thing to be able to say (the picker already says it —
    # `tp.rb.inForceSuperseded`); it is just not the run's status.
    in_force = chosen is not None and chosen.id == row.id
    record.update({
        "ontology_key": row.ontology_key, "version": row.version,
        "target_template_key": row.target_template_key,
        "status": "in_force" if in_force else ("superseded" if superseded else "pinned"),
        "in_force": in_force,
        "superseded": superseded,
        "in_force_ontology_key": chosen.ontology_key if chosen is not None else "",
        "in_force_version": chosen.version if chosen is not None else 0,
    })
    return record


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
    from app.db.models import ExtractionRun, OntologyVersion, TemplateVersion

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
        ontology = None
        oid = options.get("ontology_version_id")
        if oid:
            ont_row = session.get(OntologyVersion, oid)
            if ont_row is not None:
                # RESOLVED, because this is the one call site whose result actually maps a filing.
                # A v2 rulebook declares statement / section_scope / temporality / face_only ONLY on
                # its section_defaults entries — zero concepts carry them — so loading it
                # unresolved gives every concept those fields as None and the whole section layer
                # is absent rather than degraded. A v1 definition has no section layer to fold and
                # comes back untouched, so this is safe for both.
                ontology = load_ontology(ont_row.definition, resolve=True)
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
        doc_model, ctx = run_extraction(data, filename=filename, ontology=ontology,
                                        included_pages=included_pages, template=template,
                                        progress_cb=progress, context_cb=progress.observe)
        run = session.get(ExtractionRun, run_id)
        if run is None:
            return

        # Presence scan for qualitative disclosures (auditor qualification, contingent
        # liabilities, guarantees, …) over the document text — stored on the run. The same
        # page text yields the entity name shown at the top of the extraction/statement.
        from app.services.derived import detect_entity_name, document_text, scan_disclosures
        entity_name = None
        try:
            pages_text = document_text(data, doc_model.fmt.value)
            disclosures = scan_disclosures(pages_text)
            entity_name = detect_entity_name(pages_text)
        except Exception:  # noqa: BLE001 — a scan failure must not fail the extraction
            disclosures = []

        recon = doc_model.reconciliation
        structural = doc_model.structural
        # The rulebook decision recorded when the run was created, carried onto the result with
        # whether that rulebook actually LOADED. A pinned rulebook whose stored definition will not
        # load governs nothing, and a result that claimed it did would be the same lie in a
        # different place.
        rulebook = dict((run.options or {}).get("rulebook") or {})
        if not rulebook:
            # A run created outside the route (a re-run script, a seed) still has to say which
            # rulebook produced its figures, so the record is made here rather than left blank.
            rulebook = rulebook_record(session, oid)
        rulebook["applied"] = ontology is not None
        run.result = {
            "locale": doc_model.locale,
            # Which rulebook produced these figures — stated by the run, never re-derived by a
            # reader (see :func:`rulebook_record`).
            "rulebook": rulebook,
            "format": doc_model.fmt.value,
            "filename": filename,
            "entity": entity_name,
            "pages": [p.model_dump(mode="json") for p in doc_model.pages],
            "page_count": len(doc_model.pages),
            "line_item_count": len(doc_model.line_items),
            "notes": len(doc_model.notes),
            "rows": _serialize_rows(doc_model, ontology),
            "note_details": _serialize_notes(doc_model),
            "disclosures": disclosures,
            "reconciliation": ([e.model_dump(mode="json") for e in recon.entries] if recon else []),
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
    except Exception as exc:  # noqa: BLE001 — record failure on the run, don't crash the worker
        run = session.get(ExtractionRun, run_id)
        if run is not None:
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
            run.logs = _log_tail([*trail, f"{type(exc).__name__}: {exc}"])
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
    from app.db.models import Document, ExtractionRun, TemplateVersion

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Settle which rulebook this run reads the filing against BEFORE it starts, and keep it on the
    # run. The caller may legitimately pin a superseded rulebook (reproducing an earlier spread),
    # and the run says so rather than letting the screen decide afterwards what it must have used.
    ontology_version_id = resolve_rulebook_id(session, body.ontology_version_id,
                                              body.template_version_id)
    rulebook = rulebook_record(session, ontology_version_id)

    # A pinned rulebook and a pinned template have to be about the SAME template, and nothing
    # downstream would ever notice that they were not: the rulebook decides which concept each
    # printed caption maps to and the template decides the grid those concepts are laid out in, so
    # this pair maps every caption with a rulebook validated against a template the spread never
    # uses. That is the established mechanism behind a cost-of-sales line turning up inside other
    # income — the run succeeds, and the spread is wrong with full confidence.
    #
    # Read off the rulebook record above rather than fetching the ontology row a second time, so
    # "which template is this rulebook for" has one spelling. Refused before the integrity gate
    # because nothing about the DOCUMENT can make this pair valid; it is a contradiction in the
    # request. Pinning one of the two, or neither, stays legal — both fields are optional, a run
    # naming no rulebook is read by the shipped default, and an id naming no stored row leaves
    # ``target_template_key`` empty, which is a missing pin rather than a conflicting one.
    target_key = rulebook["target_template_key"]
    if body.template_version_id and target_key:
        tpl_row = session.get(TemplateVersion, body.template_version_id)
        if tpl_row is not None and target_key != tpl_row.template_key:
            raise HTTPException(status_code=422, detail={
                "error": "pin_mismatch",
                "message": (
                    f"Rulebook {rulebook['ontology_key']!r} is written for template "
                    f"{target_key!r}, but this run pins template {tpl_row.template_key!r}. A run "
                    f"must map with the rulebook written for the template that shapes its spread."),
                "template_key": tpl_row.template_key,
                "ontology_target_template_key": target_key,
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

    entity = body.entity or Path(doc.filename or "").stem or "document"
    run_id = audit_svc.make_run_id(entity)
    # A run has ONE start time. Stamped here and written to `created_at` as well as to the progress
    # record's `started_at`, rather than letting the column's own default stamp a second instant a
    # millisecond later — the elapsed time a screen shows must be measured from the same moment the
    # run says it began.
    started_at = datetime.now(timezone.utc)
    # ONE options dict, stored on the run AND handed to the worker. The worker used to be given
    # ``body.model_dump()`` while the row stored something else, so anything settled here — the
    # rulebook resolved above, above all — was recorded on the run and then not used to produce its
    # figures. A run that says which rulebook it read the filing against and did not read it is
    # worse than one that says nothing.
    run_options = {**body.model_dump(),
                   "ontology_version_id": ontology_version_id,
                   "rulebook": rulebook,
                   "stages": pipeline_stage_names()}
    run = ExtractionRun(
        id=run_id, document_id=doc.id,
        template_version_id=body.template_version_id,
        # The RESOLVED id, not the request's: a run must be able to say which rulebook produced
        # its figures, and "whatever was in force at the time" is not an answer a later reader can
        # reconstruct — the rulebook in force changes every time one is published.
        ontology_version_id=ontology_version_id,
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
    (``rulebook_record``/``start_extraction``) rather than being unguessable. Every other read of a
    document's data is behind ``authorized_document``; the run is the same data by another route.

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
            "progress": _served_progress(run.progress),
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
            "progress": _served_progress(run.progress)}
