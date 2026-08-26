"""Audit log — run identifiers and an append-only trail of LLM/extraction runs.

Two responsibilities:

  * ``make_run_id(entity)`` mints a human-readable run id from the entity name plus the
    UTC date and time (e.g. ``infosys-limited-20260807-021455``), with a short suffix on
    the rare same-second collision so ids stay unique.
  * A DURABLE append-only trail of :class:`AuditEntry` records — one per run — in the
    ``audit_log`` table, surfaced per document, per sample project, and whole for an admin.
    Each entry carries the LLM token usage (input and output separately) so cost is auditable
    per run.

IT USED TO BE A PROCESS-LOCAL DICT, and that is the defect this module now closes. Every token
count the product had ever recorded was lost when the API restarted — and that is the one number
here nobody can reconstruct afterwards: the rows can be re-extracted and the checks re-run, but
what a run SPENT is only knowable at the moment it spent it. A trail that does not survive a
restart is not a trail.

Each write opens its OWN short-lived session, for the same reason ``_RunProgress`` does
(``routes/extractions``): the caller's session is mid-flight — it is assembling a run result and
commits once, at the end — so committing it from here would publish a half-built run to whoever is
polling. And nothing in here may fail the caller. An audit entry is a report ABOUT a run, not part
of it, and a run that reached its rows must not be turned into a failure because a trail write did
not land. The one thing a swallowed write must not do is stay quiet, so it is logged.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "entity").strip().lower()).strip("-")
    return (s or "entity")[:48]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Run ids already handed out this process — guards against same-second collisions.
_ISSUED: set[str] = set()


def make_run_id(entity: str, *, at: datetime | None = None) -> str:
    """entity-slug + UTC timestamp, e.g. ``infosys-limited-20260807-021455``."""
    ts = (at or _now()).strftime("%Y%m%d-%H%M%S")
    base = f"{_slug(entity)}-{ts}"
    run_id = base
    n = 2
    while run_id in _ISSUED:
        run_id = f"{base}-{n}"
        n += 1
    _ISSUED.add(run_id)
    return run_id


def elapsed_ms(since: datetime) -> int:
    """Milliseconds from ``since`` to now — ONE spelling of "how long did this take".

    Every recording site measures the same quantity the same way, so two entries in one trail cannot
    mean different things by their duration. Never negative: a clock that has gone backwards should
    report "no time at all", not a negative elapsed that renders as a nonsense figure.

    A NAIVE ``since`` IS READ AS UTC, which is what ``_now`` and every other stamp in this codebase
    means. It is not hypothetical: the extraction task reconstructs its start from an ISO string
    (``_run_extraction_task``'s ``started_at``), a stamp that need not carry a zone, and subtracting a
    naive datetime from an aware one raises ``TypeError``. Raising HERE would be raising inside the
    recording of a run's outcome — including the failure path, which would abandon the run row at
    ``running`` and leave a polling client waiting on it for ever. The same normalisation the progress
    payload already applies, for the same reason (``routes.extractions._as_utc``).
    """
    at = since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)
    return max(0, int((_now() - at).total_seconds() * 1000))


@dataclass
class AuditEntry:
    run_id: str
    entity: str
    action: str                       # "analysis" | "extraction" | "credit_narrative" | …
    provider: str                     # "anthropic" | "openai" | "local" | "stub" | …
    model: str
    input_tokens: int | None          # None when the run used no LLM
    output_tokens: int | None
    status: str = "succeeded"         # "succeeded" | "failed"
    # HOW LONG THE RUN TOOK, in milliseconds, measured by whoever ran it (see :func:`elapsed_ms`).
    #
    # Optional and honestly optional: an entry for something INSTANTANEOUS — a submission handed to
    # a reviewer — has no duration to report, and None renders as "—" rather than as "0 ms", which
    # would read as a measurement of a run that took no time. It stays None on old entries too.
    #
    # Recorded here because the trail is the only place a finished run's duration can be read. The
    # extraction screen's live progress carries `elapsed_ms` while a run is in flight, and that panel
    # is gone the moment results arrive — so "how long did that extraction take?" had no answer at
    # all once it had finished, on the screen whose job is to account for the run.
    duration_ms: int | None = None
    created_at: str = field(default_factory=lambda: _now().isoformat())

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d


def _as_utc(stamp: datetime) -> datetime:
    """A stored stamp read back as UTC when the column says nothing about its zone.

    SQLite has no timezone-aware DateTime: an aware value goes in and a NAIVE one comes back. Served
    without an offset, the client's ``new Date(created_at)`` reads it as LOCAL time, so a trail
    written at 02:00 UTC renders hours away from when the run happened. Every stamp this codebase
    writes is UTC (``_now``), so saying so on the way out is the whole fix.
    """
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def _to_entry(row) -> AuditEntry:
    return AuditEntry(
        run_id=row.run_id, entity=row.entity, action=row.action, provider=row.provider,
        model=row.model, input_tokens=row.input_tokens, output_tokens=row.output_tokens,
        status=row.status, duration_ms=row.duration_ms,
        created_at=_as_utc(row.created_at).isoformat(),
    )


def record(scope_key: str, entry: AuditEntry) -> AuditEntry:
    """Append one run to the trail. Returns the entry either way — see the module docstring for
    why a failed write is swallowed rather than raised."""
    from app.db.base import SessionLocal
    from app.db.models import AuditLogEntry

    try:
        with SessionLocal() as session:
            session.add(AuditLogEntry(
                scope_key=scope_key, run_id=entry.run_id, entity=entry.entity,
                action=entry.action, provider=entry.provider, model=entry.model,
                input_tokens=entry.input_tokens, output_tokens=entry.output_tokens,
                status=entry.status, duration_ms=entry.duration_ms,
                created_at=datetime.fromisoformat(entry.created_at),
            ))
            session.commit()
    except Exception:
        # DELIBERATELY EVERYTHING, and the breadth is the contract rather than laziness. This is
        # called on the FAILURE path of an extraction: a run that reached its rows must not be
        # turned into a failed run because a report about it did not land, and narrowing this to the
        # exceptions I can think of is a bet that the ones I cannot think of will not happen. What
        # it must never be is silent, hence `exception` — the traceback reaches the log even though
        # the caller is told nothing.
        logger.exception("audit trail write failed for run %s (scope %s)",
                         entry.run_id, scope_key)
    return entry


def recorded(scope_key: str) -> list[AuditEntry]:
    """One scope's runs, oldest first — the order the in-memory list had, so the callers that
    sort for themselves are unaffected."""
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import AuditLogEntry

    with SessionLocal() as session:
        rows = session.execute(
            select(AuditLogEntry).where(AuditLogEntry.scope_key == scope_key)
            .order_by(AuditLogEntry.created_at, AuditLogEntry.id)
        ).scalars().all()
        return [_to_entry(r) for r in rows]


def all_recorded(limit: int = 500) -> list[tuple[str, AuditEntry]]:
    """``(scope_key, entry)`` for the whole trail, NEWEST FIRST — the admin view's read.

    Capped, and the cap is the caller's to state, because an unbounded read of an append-only
    table grows without limit and the screen that shows it does not. The scope travels with each
    entry: an admin reading every run at once needs to know which filing each was against, and it
    is the one field a per-document read never had to carry.
    """
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import AuditLogEntry

    with SessionLocal() as session:
        rows = session.execute(
            select(AuditLogEntry)
            .order_by(AuditLogEntry.created_at.desc(), AuditLogEntry.id.desc())
            .limit(limit)
        ).scalars().all()
        return [(r.scope_key, _to_entry(r)) for r in rows]


def totals(entries: list[AuditEntry]) -> dict:
    """What the trail adds up to: runs, how many used an LLM, and the tokens they spent.

    ``llm_runs`` is counted rather than inferred from the token sum: a run that used the LLM and
    was reported zero tokens is still a run that used it, and a reader dividing tokens by runs
    needs the denominator to be the runs that could have spent any.
    """
    llm = [e for e in entries if e.total_tokens is not None]
    return {
        "runs": len(entries),
        "llm_runs": len(llm),
        "failed": sum(1 for e in entries if e.status == "failed"),
        "input_tokens": sum(e.input_tokens or 0 for e in llm),
        "output_tokens": sum(e.output_tokens or 0 for e in llm),
        "total_tokens": sum(e.total_tokens or 0 for e in llm),
    }


def served_trail(key: str, seeded: list[dict] | None = None) -> dict:
    """The audit payload for one key — newest first, seeded rows folded in.

    ONE spelling, because two routes serve this: the seeded sample project by its project id, and an
    uploaded document by its document id. Assembling and sorting it separately in each is how the two
    come to disagree about ordering — and ordering is the whole readability of a trail.
    """
    entries = [e.to_dict() for e in recorded(key)] + list(seeded or [])
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return {"entries": entries}


def clear(scope_key: str | None = None) -> None:
    """Test helper — reset the trail (and, for a whole reset, the issued-id guard).

    A real DELETE now, not a dict pop. The trail is append-only in the product: nothing in the API
    removes an entry, and this is reachable only from tests.
    """
    from app.db.base import SessionLocal
    from app.db.models import AuditLogEntry

    with SessionLocal() as session:
        q = session.query(AuditLogEntry)
        if scope_key is not None:
            q = q.filter(AuditLogEntry.scope_key == scope_key)
        q.delete(synchronize_session=False)
        session.commit()
    if scope_key is None:
        _ISSUED.clear()
