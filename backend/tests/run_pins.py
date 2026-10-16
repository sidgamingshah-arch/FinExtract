"""The template pin a test's extraction request carries.

A run that names no template is laid out on THE ONE template with a line-item set in force, and is
refused when there is none or several (``routes.extractions.default_template_id``). The shipped
reference data puts three in force, so a request that wants "the ordinary extraction" names the
template the old default chose — the newest one stored — explicitly, so these tests read the filing
exactly as they did before the default went.
"""
from __future__ import annotations


def newest_template_id() -> str:
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import TemplateVersion
    with SessionLocal() as s:
        row = s.execute(
            select(TemplateVersion).order_by(TemplateVersion.created_at.desc(), TemplateVersion.version.desc(),
                      TemplateVersion.id.desc()).limit(1)).scalars().first()
        return row.id


def run_body(body: dict | None = None) -> dict:
    """``body`` with the newest template pinned, unless it already pins a template or a set."""
    body = dict(body or {})
    if not body.get("template_version_id") and not body.get("line_item_version_id"):
        body["template_version_id"] = newest_template_id()
    return body
