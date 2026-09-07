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

from app.schemas.line_items import LineItemSet, load_line_item_set
from app.security.rbac import Permission, require
from app.services.line_items import build

router = APIRouter(prefix="/line-items", tags=["line-items"])

SEED = (pathlib.Path(__file__).resolve().parents[2]
        / "sample" / "templates" / "output_csv_hk_line_items.json")


def _load() -> LineItemSet:
    """The set with its section layer already folded in.

    RESOLVED, not raw. The gate a definition is actually matched under is the folded one — 462
    ontology concepts declare `statement` on none of themselves and inherit all of it — so serving
    the unfolded shape would show a screen full of definitions that appear to constrain nothing.
    `?raw=true` asks for what each item declares ITSELF, which is what an editor needs.
    """
    if not SEED.exists():
        raise HTTPException(500, f"line-item definitions not found at {SEED.name}")
    try:
        raw = json.loads(SEED.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"line-item definitions are not valid JSON: {exc}") from exc
    try:
        return load_line_item_set(raw)
    except ValueError as exc:
        # A bad section default or a dangling `inherits` is exactly what this screen must show as
        # a named problem rather than as a 500 with a stack trace nobody can act on.
        raise HTTPException(500, f"line-item definitions do not load: {exc}") from exc


@router.get("", dependencies=[Depends(require(Permission.CONFIG_ONTOLOGY))])
def list_line_items() -> dict:
    """Every definition, in the order the screen shows them, with the registry's own verdict.

    Sub-line items are nested UNDER their parent in the response even though they are stored flat.
    Flat is right for editing — a sub-line item is a line item, and one shape means one editor —
    but a screen has to draw the hierarchy, and computing it here means the frontend never has to
    agree with the backend about what `parent` means.
    """
    st = _load()
    defs = st.items
    reg = build(defs)

    def payload(d) -> dict:
        out = d.model_dump(mode="json")
        out["children"] = [payload(k) for k in reg.children_of(d.key)]
        return out

    roots = sorted((d for d in defs if not d.parent), key=lambda d: (d.order, d.key))
    return {
        "items": [payload(d) for d in roots],
        # The set-level facts. `target_template_key` is the one the ontology requires at its own
        # door and a bare JSON array had nowhere to put, which is why the key-gate could not
        # simply be copied across.
        "set": {
            "schema_version": st.schema_version,
            "line_items_key": st.line_items_key,
            "target_template_key": st.target_template_key,
            "locale": st.locale,
            "supported_locales": st.supported_locales,
            "metadata": st.metadata.model_dump(mode="json"),
            # Shown so a reader can see the gate is authored once per section rather than per
            # item — the two-layer model that survived the merge.
            "section_defaults": {k: v.model_dump(mode="json", exclude_none=True)
                                 for k, v in st.section_defaults.items()},
        },
        "counts": {
            "total": len(defs),
            "output": sum(1 for d in defs if d.in_output),
            "sub_line_items": sum(1 for d in defs if d.parent),
            "by_type": {t: sum(1 for d in defs if d.type == t)
                        for t in ("extracted", "calculated", "intermediate", "derived")},
            "gated": sum(1 for d in defs if d.statement is not None),
            "inherited": sum(1 for d in defs if d.inherits),
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
