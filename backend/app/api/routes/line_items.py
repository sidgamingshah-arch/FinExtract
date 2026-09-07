"""Line-item definitions — what the Line Items screen reads.

Serves the configuration for the eight output lines and their sub-line items: the type of each,
where an extracted line looks, how it recognises a caption, and the terms or cascade behind a
computed one. Validated on the way out, so the screen can show a bad configuration as a list of
problems rather than as a blank page or a wrong figure.

READ-ONLY FOR NOW, deliberately. The definitions describe derivations that five services still
compute (`implemented_by` names which), so nothing downstream reads them yet. Serving them first
means the screen can be checked against what the pipeline actually does before an edit here can
change anything — which is the opposite order to the one that put a 162-alternative regex in
production with no way to see it.
"""
from __future__ import annotations

import json
import pathlib

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.line_items import LineItemDef
from app.security.rbac import Permission, require
from app.services.line_items import build

router = APIRouter(prefix="/line-items", tags=["line-items"])

SEED = (pathlib.Path(__file__).resolve().parents[2]
        / "sample" / "templates" / "output_csv_hk_line_items.json")


def _load() -> list[LineItemDef]:
    if not SEED.exists():
        raise HTTPException(500, f"line-item definitions not found at {SEED.name}")
    try:
        raw = json.loads(SEED.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"line-item definitions are not valid JSON: {exc}") from exc
    return [LineItemDef.model_validate(d) for d in raw]


@router.get("", dependencies=[Depends(require(Permission.CONFIG_ONTOLOGY))])
def list_line_items() -> dict:
    """Every definition, in the order the screen shows them, with the registry's own verdict.

    Sub-line items are nested UNDER their parent in the response even though they are stored flat.
    Flat is right for editing — a sub-line item is a line item, and one shape means one editor —
    but a screen has to draw the hierarchy, and computing it here means the frontend never has to
    agree with the backend about what `parent` means.
    """
    defs = _load()
    reg = build(defs)

    def payload(d: LineItemDef) -> dict:
        out = d.model_dump(mode="json")
        out["children"] = [payload(k) for k in reg.children_of(d.key)]
        return out

    roots = sorted((d for d in defs if not d.parent), key=lambda d: (d.order, d.key))
    return {
        "items": [payload(d) for d in roots],
        "counts": {
            "total": len(defs),
            "output": sum(1 for d in defs if d.in_output),
            "sub_line_items": sum(1 for d in defs if d.parent),
            "by_type": {t: sum(1 for d in defs if d.type == t)
                        for t in ("extracted", "calculated", "intermediate", "derived")},
        },
        # A configuration that does not load is the one thing this screen must never hide: the
        # whole reason it exists is that a 162-alternative regex was unreviewable.
        "problems": [{"key": p.key, "message": p.message, "severity": p.severity}
                     for p in reg.problems],
        "valid": reg.ok,
        # The order the engine would evaluate them in, so a reader can see that a formula's
        # inputs really do come first.
        "evaluation_order": reg.order,
    }
