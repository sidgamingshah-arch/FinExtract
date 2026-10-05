"""SQLAlchemy ORM models (persistence projection of the domain model).

Kept intentionally compact for this foundation: the entities the API needs now
(Document, versioned Template/LineItemSet, ExtractionRun). There is exactly ONE
configuration store here — the line-item set; the ``ontology_versions`` table that
used to sit beside it is gone (see ``LineItemVersion`` and ``db.base``). The full relational model
(Statement, LineItem, NotesTable, FaceNoteLink, ReviewItem, EditEvent, RuleResult,
Export) is documented in docs/architecture/02-data-model-and-schemas.md and lands
with the extraction persistence phase.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return datetime.now(timezone.utc).date()


class Document(Base):
    __tablename__ = "documents"
    # Dedup is per owner: two analysts uploading the same file each get their own document.
    __table_args__ = (UniqueConstraint("tenant_id", "owner", "content_hash", name="uq_doc_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default="default")
    owner: Mapped[str] = mapped_column(String(128), default="", index=True)
    filename: Mapped[str] = mapped_column(String(512), default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    fmt: Mapped[str] = mapped_column(String(16), default="unknown")
    object_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    locale: Mapped[str | None] = mapped_column(String(8), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    integrity_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Classified pages captured at upload (ingest→classify), so the Page Scope screen and
    # scope editing reuse them instead of re-running the pre-flight pipeline on every request.
    pages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # User-chosen extraction scope: explicit list of INCLUDED page indices. None = default
    # (all face/notes pages). Honoured by the extraction pipeline.
    page_scope: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # A person's corrections of how pages are read — parts, kinds, statements, entities
    # (`services.page_overrides`). Applied after the classifier on every run of this document.
    page_overrides: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    runs: Mapped[list["ExtractionRun"]] = relationship(back_populates="document")


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_key", "version", name="uq_tpl_ver"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    template_key: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    definition: Mapped[dict] = mapped_column(JSON)
    is_published: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class LineItemVersion(Base):
    """One immutable version of the LINE-ITEM SET — the single configuration engine.

    This replaces ``OntologyVersion`` (``ontology_versions``), which stood here and is
    deleted: the ontology is no longer a stored, selectable or user-visible thing, so
    there is nothing for a second configuration table to hold. ``db.base`` drops the old
    table on the way past; that drop only sticks because the model is gone from
    ``Base.metadata`` and ``create_all`` can no longer recreate it. Do not reinstate it —
    line items is the one place configuration lives.

    Deliberately the same SHAPE the ontology row had, so the version/pin machinery around
    it needed no rethink, MINUS ``is_published``: nothing ever SELECTed on that flag, and
    ``sample.reference._clear_prior_published`` records it had gone wrong on 19 rows at
    once. A flag no reader consults and no writer keeps true is not state, it is a lie
    with a column.
    """

    __tablename__ = "line_item_versions"
    __table_args__ = (UniqueConstraint("line_items_key", "version", name="uq_li_ver"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    line_items_key: Mapped[str] = mapped_column(String(128), index=True)
    target_template_key: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    definition: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # WHICH EDITING SESSION THIS VERSION BELONGS TO, and when it last changed.
    #
    # ONE VERSION PER SESSION, NOT ONE PER FIELD. Every inline edit used to add a version, so a
    # sitting spent renaming a few lines produced a dozen of them and the history said nothing
    # about what happened — measured on this database, eleven versions for three line-item edits
    # and six deletions. An edit arriving in the SAME session as the version in force now REPLACES
    # that version's definition; a new session starts a new version. So a version is one person's
    # sitting, which is the unit anybody reading the history actually wants.
    #
    # NULLABLE BECAUSE MOST ROWS PREDATE IT, and because a publish with no session to attribute —
    # the `X-Role` dev header, a service call, the reference-data seeder — must still be able to
    # write one. An unattributed publish always inserts: coalescing needs proof that the session is
    # the same, and absence of a session id is not proof.
    #
    # `updated_at` is what tells a reader a version was edited after it was created. Left null
    # until the first in-place replacement, so "created and never touched" stays distinguishable
    # from "created and then written to".
    authored_in_session: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # THE SHIPPED FILE THE REFERENCE SEEDER PUBLISHED THIS VERSION FROM, or null when anything else
    # published it — an upload, an inline edit, a template upload's provisioned set.
    #
    # Written once, at insert, by ``sample.reference._seed_line_items`` and by no other writer, and
    # READ: it is how the boot refresh tells its own last write from a person's
    # (``sample.reference._line_items_seeded``), so that a reverted shipped file is republished over
    # the seeder's version and never over an administrator's. Nothing else on the row can say so —
    # every service-side publish is unattributed — and content cannot either, since an administrator
    # may upload exactly what the file says. Not ``is_published`` coming back: that flag was
    # mutable and read by nobody; this is immutable and has one reader.
    #
    # Null on every row older than the column (``db.base`` adds it on the way past), which the
    # refresh reads as "not the seeder's" — the conservative answer.
    seeded_from: Mapped[str | None] = mapped_column(String(256), nullable=True)


class FxRate(Base):
    """One admin-maintained exchange rate: 1 ``base_ccy`` = ``rate`` ``quote_ccy`` on ``as_of``.

    The FX master is deliberately *only* what an administrator entered — there is no rate
    feed. Nothing is seeded: an empty table means "we hold no authoritative rate", which
    the resolver reports honestly instead of falling back to 1.
    """

    __tablename__ = "fx_rates"
    # One row per pair per as-of date. Without this a second entry for the same day would
    # silently coexist with the first and the resolver would have to guess which is current.
    __table_args__ = (
        UniqueConstraint("base_ccy", "quote_ccy", "as_of", name="uq_fx_pair_as_of"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    base_ccy: Mapped[str] = mapped_column(String(3), index=True)
    quote_ccy: Mapped[str] = mapped_column(String(3), index=True)
    # Held as text and parsed back with ``Decimal(...)``: these multiply financial figures,
    # and a binary FLOAT column cannot round-trip a rate like 0.0121 exactly — the drift
    # would land in the presented numbers. Same reasoning as the extracted values, which
    # are also persisted as strings.
    rate: Mapped[str] = mapped_column(String(48))
    # The date the rate is *for* (not when it was typed) — a converted figure is only
    # honest next to the date of the rate that produced it.
    as_of: Mapped[date] = mapped_column(Date, default=_today, index=True)
    # Free-text provenance ("ECB reference, 2026-08-01 fixing", "treasury desk") so a
    # reviewer can trace where an authoritative-looking number actually came from.
    source: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    # Human-readable id: entity-slug + timestamp (see services.audit.make_run_id);
    # widened past a bare UUID to hold the entity prefix.
    id: Mapped[str] = mapped_column(String(96), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    template_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # THE PIN that makes a run reproducible: the exact line-item version the mapper read.
    # Was ``ontology_version_id`` — the configuration a run used is now only ever a
    # line-item set, so re-reading this row tells you precisely what it was mapped against.
    # Nullable because a run recorded before the pin existed genuinely has no answer, and
    # inventing one would be worse than admitting it.
    line_item_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_number: Mapped[int] = mapped_column(Integer, default=1)
    engine_version: Mapped[str] = mapped_column(String(32), default="0.1.0")
    status: Mapped[str] = mapped_column(String(16), default="running")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    logs: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    document: Mapped["Document"] = relationship(back_populates="runs")


class ReviewJudgement(Base):
    """One human judgement on one review finding, keyed on WHAT was judged rather than on the
    finding's id — two of the eight check builders key on row index (api/routes/documents.py:715
    and :729), so an id-keyed acceptance would silently move onto a different line item after a
    re-run and mark a real problem as vouched for by someone who never saw it.

    ONE in-force row per (tenant_id, document_id, subject_key), never rival rows a reader has to
    date-sort to find the current answer. ``history`` is appended to on every state change,
    newest LAST, and nothing is ever deleted: a withdrawal flips ``verdict`` and keeps the row,
    because erasing who accepted a break is not something an audit trail should permit.

    No column holds a digest, a status or a count. All three are derived at serve time (see
    ``services.judgement.apply_judgements``) — a derived value stored beside its source is the
    two-places-computing-one-quantity bug, and here it would be the one that decides whether an
    acceptance still stands.

    Scope cut, stated rather than implied: nothing reads ``history`` yet, so this table is not
    described as an audit trail anywhere until something serves it.
    """

    __tablename__ = "review_judgements"
    __table_args__ = (UniqueConstraint("tenant_id", "document_id", "subject_key",
                                       name="uq_judgement_subject"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), default="default")
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    # sha256 of the canonicalized subject — 64 hex characters, so it is URL-safe (unlike a check
    # id, whose structural scope_key contains a "/").
    subject_key: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    # Why the finding was RAISED (mapping confidence and method), as opposed to the claim that was
    # confirmed. Recorded so a reader can see how uncertain a mapping was when it was accepted,
    # and deliberately NOT part of any match: re-running the mapper at a different confidence
    # must not withdraw a judgement about which concept the line is.
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    verdict: Mapped[str] = mapped_column(String(16), default="accepted")   # accepted|withdrawn
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128), default="")
    actor_role: Mapped[str] = mapped_column(String(16), default="")
    run_id: Mapped[str] = mapped_column(String(96), default="")
    history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SettingOverride(Base):
    """One administrator-changed setting, so a change survives a restart.

    Everything else in ``config.toml`` is deployment configuration and changes on redeploy;
    these are the values an admin sets from the Settings screen and expects to stay set.

    One row per setting rather than a single blob: adding a knob then needs no schema change
    and no migration of an existing row, and two admins changing different knobs cannot
    clobber each other's edit the way a whole-document write would.

    ``scope`` groups a setting to the object it is applied onto — "features", "llm",
    "extraction" — because the same short name could exist in more than one of them.

    NO SECRETS. The LLM API key is never written here; only the NAME of the environment
    variable it is read from (``api_key_env``), which is what the rest of the app stores too.
    The value column is JSON so a bool, a number and a string all round-trip as themselves
    instead of being stringified and guessed at on the way back.
    """

    __tablename__ = "setting_overrides"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_setting_override_scope_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    # {"v": <json value>} rather than a bare scalar: SQLite's JSON support stores a bare
    # ``null`` indistinguishably from SQL NULL, and "explicitly set to null" has to remain
    # different from "no row".
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AuditLogEntry(Base):
    """One run against one document — what ran it, what it cost, and how it ended.

    THE GAP THIS CLOSES. The trail was a process-local dict (``services/audit._LOG``), so every
    token count the product had ever recorded was lost when the API restarted. That is the one
    number in this system nobody can reconstruct after the fact: the rows can be re-extracted, the
    checks re-run, but what a run SPENT is only knowable at the moment it spent it. An audit trail
    that does not survive a restart is not an audit trail.

    ``scope_key`` is the DOCUMENT this run was against — or the seeded sample project's id, which is
    the one non-document scope, because the sample's own runs are recorded the same way. Not a
    foreign key: an entry has to outlive the document it describes. Deleting an uploaded file must
    not erase the record that an extraction was run on it and what that cost, and a ``ForeignKey``
    with a cascade is precisely how an audit row silently disappears.

    ``run_id`` is the human-readable id ``make_run_id`` mints (entity-slug + UTC stamp), unique
    across the table: it is the id the screens key their rows on, and two rows claiming one run id
    would render as one.

    Token counts are NULLABLE and null is meaningful — the run used no LLM at all, which is a
    different fact from having used zero tokens. Same for ``duration_ms``: handing output to a
    reviewer is an instant, not an interval.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_audit_log_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scope_key: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    entity: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(48), default="")
    provider: Mapped[str] = mapped_column(String(48), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="succeeded")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Indexed because every read of this table is "newest first", and the admin view reads the
    # whole table that way.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
