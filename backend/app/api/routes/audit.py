"""The whole run trail, for an administrator — every extraction and LLM run in the system.

WHY IT IS SEPARATE FROM THE PER-DOCUMENT TRAIL. ``GET /documents/{id}/audit`` answers "what has been
run against THIS filing", which is a question about a document and is gated on the same ownership
predicate as every other read of one. This answers "what has this deployment spent, and on what",
which is a question about the deployment: it deliberately crosses ownership, and so it is gated on
``AUDIT_VIEW`` — the admin-only permission that has existed since the RBAC map was written and, until
now, had no endpoint behind it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db
from app.security import Permission, current_principal, require

router = APIRouter(prefix="/audit", tags=["audit"],
                   dependencies=[Depends(current_principal)])


@router.get("", dependencies=[Depends(require(Permission.AUDIT_VIEW))])
def get_audit_trail(
    limit: int = Query(500, ge=1, le=5000),
    session: Session = Depends(db),
) -> dict:
    """Every recorded run, newest first, with what each one cost and which filing it was against.

    ``limit`` is stated rather than assumed: the table is append-only, so an uncapped read grows
    without bound while the screen showing it does not. The response says how many entries it
    carries and whether the cap was reached, because "500 runs" and "the first 500 of more" are
    different answers and a screen that cannot tell them apart will report the first as the total.

    THE TOTALS ARE OF WHAT IS RETURNED, and the payload says so by naming the same count twice.
    Summing the whole table server-side and returning it beside a truncated list would put a total
    over a set of rows that does not add up to it — the class of defect this codebase keeps
    deleting. An admin who needs the true total raises the cap.
    """
    from app.db.models import Document
    from app.services import audit as audit_svc

    pairs = audit_svc.all_recorded(limit)
    # ONE query for the filenames, not one per row. Only the scopes actually present are asked for.
    scopes = {scope for scope, _ in pairs}
    names: dict[str, str] = {}
    if scopes:
        rows = session.query(Document.id, Document.filename).filter(
            Document.id.in_(scopes)).all()
        names = {doc_id: (filename or "") for doc_id, filename in rows}

    entries = []
    for scope, entry in pairs:
        entries.append({
            **entry.to_dict(),
            "scope_key": scope,
            # The uploaded file this run was against. Empty for the seeded sample project, and
            # empty for a document that has since been DELETED — the entry outlives it on purpose,
            # so the trail still records that the run happened and what it cost.
            "document": names.get(scope, ""),
            "scope_kind": "document" if scope in names else "other",
        })
    return {
        "entries": entries,
        "totals": audit_svc.totals([e for _, e in pairs]),
        "limit": limit,
        # True when the cap was hit, so the screen can say the totals are of a window rather than
        # of everything. It cannot be derived from `len(entries) == limit` by the client without
        # knowing the cap was ours and not its own.
        "truncated": len(pairs) >= limit,
    }
