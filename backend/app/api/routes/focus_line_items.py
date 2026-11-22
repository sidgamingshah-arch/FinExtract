"""Focus line items master — which line items a focus run asks the model about, per template.

Reading is open to any authenticated caller; adding, removing and restoring the shipped list is
administrator-only under ``config:settings``, the permission that gates the Settings screen this
master is kept from — as the FX rate master is. Every change is applied onto the list the next run
reads (``services.focus_master``). Whether a run is restricted to the list at all is the
``extraction.llm_focus_only`` switch on the same screen, off as shipped.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import db
from app.security import Permission, current_principal, require
from app.services import focus_master

router = APIRouter(prefix="/focus-line-items", tags=["focus-line-items"])

_admin = Depends(require(Permission.CONFIG_SETTINGS))


class FocusLineItemBody(BaseModel):
    """The template whose list it goes on; a line-item key of that template's set, or a prefix
    ending in ``*``; why it is in focus; and, for a line computed from its parts, whether its parts
    go in with it (they do unless told otherwise — a computed line is never asked itself)."""

    template: str
    key: str
    note: str | None = None
    with_parts: bool = True


@router.get("", dependencies=[Depends(current_principal)])
def list_focus_line_items(template: str | None = None, session: Session = Depends(db)) -> dict:
    """One template's list (the first, when none is named): each entry with the lines it names,
    whether focus-only is on, the template's shipped list, every line of its set a key may name, and
    the templates a list is kept for."""
    try:
        return focus_master.payload(session, template)
    except focus_master.FocusError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", status_code=201, dependencies=[_admin])
def add_focus_line_item(body: FocusLineItemBody, session: Session = Depends(db)) -> dict:
    """Add a key (and a computed line's parts) to a template's list. A key already there is kept —
    re-adding it only updates its note — so the call is safe to repeat."""
    try:
        added = focus_master.add(session, body.template, body.key, body.note or "",
                                 with_parts=body.with_parts)
    except focus_master.FocusError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"added": added, **focus_master.payload(session, body.template)}


@router.delete("/{item_id}", status_code=204, dependencies=[_admin])
def delete_focus_line_item(item_id: str, session: Session = Depends(db)) -> Response:
    if not focus_master.remove(session, item_id):
        raise HTTPException(status_code=404, detail="Focus line item not found")
    return Response(status_code=204)


@router.post("/restore", dependencies=[_admin])
def restore_shipped_focus_line_items(template: str, session: Session = Depends(db)) -> dict:
    """Replace one template's list with config.toml's shipped list for it."""
    try:
        focus_master.restore_shipped(session, template)
    except focus_master.FocusError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return focus_master.payload(session, template)
