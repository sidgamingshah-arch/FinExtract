"""A pre-existing documents table missing newer columns must be brought up to the current
model by init_db's reconcile step — create_all never ALTERs an existing table, so without
this every query selecting a newer column (e.g. pages / page_scope) 500s."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text


def test_reconcile_adds_missing_columns(tmp_path):
    from app.db.base import _reconcile_schema

    db = tmp_path / "stale.db"
    eng = create_engine(f"sqlite:///{db}", future=True)
    # Simulate an old DB: a documents table from before owner/pages/page_scope existed.
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE documents ("
            "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), content_hash VARCHAR(64), "
            "object_key VARCHAR(128))"
        ))

    _reconcile_schema(eng)

    cols = {c["name"] for c in inspect(eng).get_columns("documents")}
    for expected in ("owner", "pages", "page_scope", "sections_off", "filename", "status",
                     "page_count"):
        assert expected in cols, f"reconcile should have added {expected}"

    # And the widened dedup constraint now includes owner.
    uniques = inspect(eng).get_unique_constraints("documents")
    assert any("owner" in (uc.get("column_names") or []) for uc in uniques)
