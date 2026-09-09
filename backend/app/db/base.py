"""SQLAlchemy base + session factory.

SQLite by default (zero-setup); swap ``FINEX_DATABASE_URL`` for Postgres in prod.
JSON columns hold the versioned template/line-item/integrity payloads. Alembic would
own DDL in prod; here ``init_db`` uses ``create_all`` to stay runnable out of the box,
with ``_reconcile_schema`` doing the small forward-migrations ``create_all`` cannot.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def _reconcile_schema(eng: Engine) -> None:
    """Lightweight, idempotent forward-migration for an *existing* database.

    This app uses ``create_all`` (no Alembic), which never ALTERs a table that already
    exists — so a database created before a column/constraint was added would keep the old
    shape and break. Rather than force a manual DB reset, four steps run in order:

    (a) the ``documents`` special case — add the ``owner`` column and widen the dedup unique
        constraint to ``(tenant_id, owner, content_hash)`` so two owners can hold the same
        file. This one needs a table rebuild on SQLite, so it stays hand-written.
    (b) EVERY other table already in the DB gains, nullable, any column its model has and
        it lacks. This is how an existing ``extraction_runs`` picks up
        ``line_item_version_id`` — the pin that makes a run reproducible.
    (c) ``DROP TABLE IF EXISTS ontology_versions``. INTENTIONAL, DOCUMENTED DATA LOSS: the
        ontology is no longer a configuration engine, line items is the only one, and the
        instruction was drop rather than convert (41 of the 44 stored rows no longer load
        against their own schema, so there is nothing worth converting). Anyone holding a
        hand-edited rulebook row loses it here; that was the stated trade.
    (d) best-effort drop of the orphaned ``extraction_runs.ontology_version_id``. Needs
        SQLite >= 3.35, so it is wrapped: on an older build the column is simply left
        behind, unreferenced by any model and read by nothing.

    A brand-new database skips (a) and (b) entirely (``create_all`` makes it current) and
    finds nothing to drop in (c)/(d).
    """
    from app.db import models  # noqa: F401  — register every table on Base.metadata

    _reconcile_documents(eng)      # (a)
    _backfill_missing_columns(eng)  # (b)

    with eng.begin() as conn:
        existing = set(inspect(conn).get_table_names())
        # (c) The ontology store. Gone, not migrated — see the docstring.
        conn.execute(text("DROP TABLE IF EXISTS ontology_versions"))
        # (d) The pin it used to be selected by.
        if "extraction_runs" in existing:
            cols = {c["name"] for c in inspect(conn).get_columns("extraction_runs")}
            if "ontology_version_id" in cols:
                try:
                    conn.execute(
                        text("ALTER TABLE extraction_runs DROP COLUMN ontology_version_id"))
                except Exception:  # pragma: no cover — sqlite3 < 3.35 has no DROP COLUMN
                    pass  # left orphaned rather than risking the boot; nothing reads it


def _backfill_missing_columns(eng: Engine) -> None:
    """(b) ADD COLUMN, nullable, for any model column an already-existing table lacks.

    ``documents`` is excluded because ``_reconcile_documents`` owns it (its ``owner``
    column is special-cased NOT NULL, and it may rebuild the table wholesale).
    """
    insp = inspect(eng)
    existing = set(insp.get_table_names())

    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name == "documents" or table.name not in existing:
                continue
            have = {c["name"] for c in inspect(conn).get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(dialect=eng.dialect)
                conn.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}"))


def _reconcile_documents(eng: Engine) -> None:
    """(a) The ``documents`` owner column + widened dedup constraint. Unchanged."""
    from app.db.models import Document

    insp = inspect(eng)
    if "documents" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("documents")}
    uniques = insp.get_unique_constraints("documents")
    has_owner = "owner" in cols
    owner_in_unique = any("owner" in (uc.get("column_names") or []) for uc in uniques)

    # Generic column backfill: add any model column the existing table is missing (e.g.
    # ``pages``/``page_scope`` added after the DB was first created). ``create_all`` never
    # ALTERs an existing table, so without this a pre-existing DB 500s on every query that
    # selects a newer column. New columns are added nullable (owner is special-cased NOT NULL).
    missing = [c for c in Document.__table__.columns if c.name not in cols and c.name != "owner"]
    if has_owner and owner_in_unique and not missing:
        return  # already current

    with eng.begin() as conn:
        for col in missing:
            ddl_type = col.type.compile(dialect=eng.dialect)
            conn.execute(text(f'ALTER TABLE documents ADD COLUMN {col.name} {ddl_type}'))
        if not has_owner:
            conn.execute(text("ALTER TABLE documents ADD COLUMN owner VARCHAR(128) NOT NULL DEFAULT ''"))
        if not owner_in_unique:
            if eng.dialect.name == "sqlite":
                # SQLite can't alter a table-level UNIQUE in place → rebuild + copy.
                tmp = Document.__table__.to_metadata(MetaData(), name="documents_new")
                tmp.indexes.clear()  # avoid index-name clashes with the live table
                tmp.create(bind=conn)
                shared = [c.name for c in Document.__table__.columns
                          if c.name in {c["name"] for c in inspect(conn).get_columns("documents")}]
                collist = ", ".join(shared)
                conn.execute(text(f"INSERT INTO documents_new ({collist}) SELECT {collist} FROM documents"))
                conn.execute(text("DROP TABLE documents"))
                conn.execute(text("ALTER TABLE documents_new RENAME TO documents"))
            else:  # postgres / others
                conn.execute(text("ALTER TABLE documents DROP CONSTRAINT IF EXISTS uq_doc_hash"))
                conn.execute(text(
                    "ALTER TABLE documents ADD CONSTRAINT uq_doc_hash "
                    "UNIQUE (tenant_id, owner, content_hash)"
                ))


def init_db() -> None:
    # Import models so they are registered on Base.metadata before create_all.
    from app.db import models  # noqa: F401

    _reconcile_schema(engine)          # bring an existing DB up to the current model
    Base.metadata.create_all(bind=engine)  # create anything still missing (indexes, new tables)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
