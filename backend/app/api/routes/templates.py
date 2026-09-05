"""Template CRUD (versioned) with schema validation on create.

Templates are authored two ways, and both land in the same validated, versioned place: a JSON
definition, or the Excel workbook a reviewer edits (see services.template_xlsx) — which is the
route an analyst actually uses, because deciding what a spread should contain is a
spreadsheet job, not a JSON one.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db
from app.schemas.loader import load_template, unknown_keys, validate_template
from app.security import Permission, require
from app.services.review_lines import is_statement_line

router = APIRouter(prefix="/templates", tags=["templates"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TemplateCreate(BaseModel):
    definition: dict


def _publish(session: Session, definition: dict) -> dict:
    """Validate a definition and store it as the next version of its template key."""
    from app.db.models import TemplateVersion

    try:
        template = load_template(definition)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Invalid template schema: {exc}") from exc

    # A key the schema does not declare is refused HERE, at the door, where there is a request to
    # fail and an author to tell. Silently dropping it published a template that did not contain
    # what was written — a mistyped `canonical_keys`, a `weights` on a rollup, an `inherits`
    # carried over from another format — and the first symptom was an extraction behaving as
    # though the edit had never been made. Deliberately not enforced when READING a stored
    # definition: see loader.unknown_keys.
    stray = unknown_keys(definition, template)
    if stray:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"location": p, "message": "key is not part of the template schema"}
                               for p in stray]})

    errors = validate_template(template)
    if errors:
        raise HTTPException(status_code=422,
                            detail={"errors": [e.model_dump() for e in errors]})

    max_ver = session.execute(
        select(func.max(TemplateVersion.version))
        .where(TemplateVersion.template_key == template.template_key)
    ).scalar()
    version = (max_ver or 0) + 1

    row = TemplateVersion(
        template_key=template.template_key,
        name=template.name,
        version=version,
        definition=definition,
    )
    session.add(row)
    session.commit()
    return {"id": row.id, "template_key": template.template_key, "name": template.name,
            "version": version,
            # A line is a node that CARRIES A FIGURE — this codebase's one predicate for that
            # (`review_lines`, which the review header counts both routes' populations with).
            # `get_template_detail`'s walk and `export._emit_nodes` ask it too, so the count an upload
            # reports, the count the Template screen prints and the rows the workbook holds cannot
            # drift. Spelled `!= "header"` here, a spacer was published as a line item.
            "line_items": len([n for n in template.all_nodes()
                               if is_statement_line({"role": n.role.value})])}


@router.post("", status_code=201, dependencies=[Depends(require(Permission.CONFIG_TEMPLATE))])
def create_template(body: TemplateCreate, session: Session = Depends(db)) -> dict:
    return _publish(session, body.definition)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


@router.get("/{template_id}/xlsx")
def download_template_xlsx(template_id: str, session: Session = Depends(db)) -> Response:
    """The template as an editable workbook — one row per line, with the extracted-vs-calculated
    column and each calculated line's components. Upload the edited file back to /templates/xlsx."""
    from app.db.models import TemplateVersion
    from app.services.template_xlsx import build_template_xlsx

    row = session.get(TemplateVersion, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    data = build_template_xlsx(row.definition or {}, filename_hint=row.name or row.template_key)
    fname = f"{_slug(row.template_key) or 'template'}_v{row.version}_template.xlsx"
    return Response(content=data, media_type=_XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/xlsx", status_code=201,
             dependencies=[Depends(require(Permission.CONFIG_TEMPLATE))])
async def create_template_from_xlsx(
    file: UploadFile = File(...),
    template_key: str = Form(""),
    name: str = Form(""),
    session: Session = Depends(db),
) -> dict:
    """An edited template workbook → a NEW template version.

    Uploading onto an existing ``template_key`` publishes the next version of it rather than
    replacing anything: a past extraction still explains itself against the version it actually
    ran with. Leave the key blank to start a new template from the workbook's own name.
    """
    from app.services.template_xlsx import TemplateSheetError, parse_template_xlsx

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    stem = re.sub(r"\.(xlsx|xlsm)$", "", file.filename or "template", flags=re.IGNORECASE)
    title = (name or stem).strip() or "Template"
    key = _slug(template_key) or _slug(title) or "template"
    try:
        definition = parse_template_xlsx(raw, template_key=key, name=title)
    except TemplateSheetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a corrupt/foreign workbook
        raise HTTPException(
            status_code=422,
            detail=f"That file could not be read as a template workbook ({exc}). Download the "
                   f"current template, edit it, and upload that.") from exc
    return _publish(session, definition)


@router.get("/xlsx/columns")
def template_xlsx_columns() -> dict:
    """What the workbook's columns mean — so the upload screen can state the contract it enforces
    instead of the user discovering it from a 422."""
    from app.services.template_xlsx import COLUMNS, KIND_CALCULATED, KIND_EXTRACTED, KIND_HEADING

    return {
        "columns": [{"key": k, "header": h} for k, h in COLUMNS],
        "kinds": [
            {"value": KIND_EXTRACTED,
             "help": "Read off the document by the mapper."},
            {"value": KIND_CALCULATED,
             "help": "Computed from other lines and never mapped; needs 'Calculated from'."},
            {"value": KIND_HEADING, "help": "A section heading; carries no figure."},
        ],
        "required": ["Statement", "Canonical key", "Label (en)", "Kind"],
    }


@router.get("")
def list_templates(session: Session = Depends(db)) -> list[dict]:
    """Every stored template version, NEWEST FIRST per key, each saying whether it is the latest.

    THE TWO DEFECTS THIS CLOSES, both caused by serving an unordered list of versions and leaving the
    client to make sense of it.

    This query had no ``ORDER BY``, so it came back in insertion order — v1, v2, v3, v4 — and the
    Upload screen's picker took ``find(x => x.template_key === selectedKey)``. That is the FIRST row
    with the key, which is v1, the oldest. So the screen named v1 as the active template no matter how
    many revisions had been published, and the extraction view resolved the run's template the same
    way — meaning a re-extraction ran against v1 and could not produce the revised statement order,
    whatever the analyst had chosen. Worse, every row in the picker set the same ``template_key``, so
    selecting v2 selected nothing: it stored the key it already held and the list re-answered v1.

    ``is_latest`` is the SERVER'S answer to "which version is current for this key", so the client
    reads it rather than ranking versions itself — the same rule as ``in_force`` on the ontology list,
    and for the same reason: one implementation cannot disagree with itself. The ordering is here too,
    because a list whose order carries meaning must not depend on how rows happened to be inserted.
    """
    from app.db.models import TemplateVersion

    rows = list(session.execute(
        select(TemplateVersion)
        .order_by(TemplateVersion.template_key, TemplateVersion.version.desc())
    ).scalars().all())
    # Highest version per key. Computed off the rows just read, so it cannot describe a different
    # set from the one being served.
    latest = {
        template_key: max(row.version for row in rows if row.template_key == template_key)
        for template_key in {row.template_key for row in rows}
    }
    return [{"id": row.id, "template_key": row.template_key, "name": row.name,
             "version": row.version, "is_published": row.is_published,
             "is_latest": row.version == latest[row.template_key]} for row in rows]

@router.get("/{template_id}")
def get_template(template_id: str, session: Session = Depends(db)) -> dict:
    from app.db.models import TemplateVersion

    row = session.get(TemplateVersion, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"id": row.id, "template_key": row.template_key, "version": row.version,
            "definition": row.definition}


_SIGN_UI = {"natural": "as_reported", "as_reported": "as_reported",
            "contra": "expense_contra", "expense_contra": "expense_contra",
            "expense_negative": "expense_contra"}

# A stored SignConvention → the three-way choice the Template screen shows. The ontology is
# the extraction rulebook, so its sign_rule wins over the template's static hint — otherwise an
# edit saved to the ontology would appear to do nothing on screen.
_ONT_SIGN_UI = {
    "natural": "as_reported", "natural_positive": "as_reported", "debit_positive": "as_reported",
    "natural_negative": "expense_contra", "credit_positive": "expense_contra",
    "context": "auto",
}


def _loc(node: dict, locale: str) -> str:
    return (node.get("label_i18n") or {}).get(locale) or node.get("label") or ""


@router.get("/{template_id}/detail")
def get_template_detail(template_id: str, locale: str = "en",
                        session: Session = Depends(db)) -> dict:
    """Render a REAL configured template into the tree + per-node config the Template &
    Ontology screen shows — so an admin sees the seeded/authored template instead of an
    empty screen (the demo-bound view only ever showed demo data). Aliases, sign convention
    and any note-decomposition rule are pulled from the ontology that targets this template."""
    from app.db.models import TemplateVersion

    row = session.get(TemplateVersion, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    tdef = row.definition or {}

    # The ontology that targets this template (latest version) supplies aliases/sign/netting.
    from app.schemas.loader import load_ontology

    from app.services.ontology_select import select_for_template

    ont_row = select_for_template(session, row.template_key)
    by_key = {}
    netting_rules: list[dict] = []
    # The concepts the rulebook DECLARES, read off the stored definition rather than off the loaded
    # object below, because that is the list the editor's writes are checked against: every one of
    # them looks the key up in `definition["mappings"]` (ontologies.edit_ontology_mapping, its
    # `confusable_with` guard, edit_netting_rule) with no resolve step. Taken from `by_key` instead,
    # a definition that VALIDATES but cannot be resolved — one `inherits` naming a section that is
    # not there — would empty this set through the `except` below and have the screen tell the reader
    # the rulebook declares no concept for any of its lines, while the server went on accepting the
    # very edits the screen had disabled.
    declared: set[str] = set()
    if ont_row:
        declared = {m.get("canonical_key")
                    for m in ((ont_row.definition or {}).get("mappings") or [])
                    if isinstance(m, dict)}
        try:
            ont = load_ontology(ont_row.definition, resolve=True)
            for m in ont.mappings:
                by_key[m.canonical_key] = m
            # Generic containment-netting policies (LLM-gated) — surfaced for the admin to review.
            def _lbl(k: str) -> str:
                mm = by_key.get(k)
                return (mm.label if mm and mm.label else k.replace("_", " "))
            for nr in ont.netting_rules:
                netting_rules.append({
                    "id": nr.id, "target_key": nr.target_key, "target_label": _lbl(nr.target_key),
                    "subtract": [{"key": k, "label": _lbl(k)} for k in nr.subtract_keys],
                    "add": [{"key": k, "label": _lbl(k)} for k in nr.add_keys],
                    "condition": nr.condition, "label": nr.label,
                })
        except Exception:  # noqa: BLE001 — a malformed ontology shouldn't blank the screen
            by_key = {}

    tree: list[dict] = []
    node_config: dict[str, dict] = {}
    leaves = 0

    def emit(nodes: list[dict], lvl: int, trail: list[str], stmt_label: str) -> None:
        """Walk one section tree, taking the heading/line branch from ``export._emit_nodes``.

        Branching on "is a top-level section" instead is what made a calculated total impossible to
        select. The template declares Gross Profit and sixteen other lines as CHILDLESS entries in
        ``sections[]`` (``pl_gross_profit``, ``pl_profit_before_tax``, ``bs_net_assets``,
        ``cf_closing_cash_and_cash_equivalents``, …), because that is where in the statement they
        are PRINTED. Emitting every section as a heading turned each of them into an inert row whose
        inner loop had nothing to iterate, so no ``node_config`` entry was ever written for it — and
        the screen resolved the click to an unrelated concept and showed that concept's rules under
        the heading of the line the analyst had clicked.

        Whether a node carries a figure is asked with ``review_lines.is_statement_line`` — the
        predicate ``export._emit_nodes`` and ``_publish``'s ``line_items`` count above now read as
        well, so all three agree on what a line is without three literals that have to keep
        matching. That is what gives ``LineRole.SPACER`` its treatment rather than a fourth opinion
        about it: a spacer is a presentational gap, so it reaches the tree as an unselectable row
        like a heading. Tested as ``== "header"``, it came out as a selectable LINE with a
        ``node_config`` entry — a blank gap in the statement offered as a concept to alias and give a
        sign convention to.

        Children are walked BELOW A LINE as well as below a heading, which ``_emit_nodes`` does not
        do — every node that carries a figure is a concept the rulebook may map (see ``mapped``
        below), and a concept that reaches no ``node_config`` entry is a concept the screen cannot
        answer about. It also keeps ``leaves`` equal to every keyed line for any template, not just
        for one nested no deeper than the shipped file. (An export that skips such a node is an
        export bug, not a reason for this screen to hide it.)
        """
        nonlocal leaves
        for node in nodes:
            label = _loc(node, locale)
            children = node.get("children") or []
            if not is_statement_line(node):
                tree.append({"id": f"sec:{node.get('node_id', label)}", "label": label,
                             "lvl": lvl, "head": True})
                emit(children, lvl + 1, [*trail, label], stmt_label)
                continue
            key = node.get("canonical_key")
            if not key:
                # A node with no key is not addressable: `node_config` is keyed by canonical_key, so
                # there is nothing to select and nothing to edit. Its subtree still is, and is
                # emitted at THIS level rather than indented under a row that was never drawn.
                # `_publish` does count such a node, so its `line_items` reads one higher — the
                # schema requires `canonical_key`, so reaching here at all means an empty string got
                # past the upload gate, and the fix for that belongs at the gate.
                emit(children, lvl, trail, stmt_label)
                continue
            leaves += 1
            m = by_key.get(key)
            decomp = getattr(m, "decomposition_rule", None) if m else None
            tree.append({"id": key, "label": label, "lvl": lvl, "rule": bool(decomp)})
            # `aliases` is the merged display set (locale + English fallback, capped).
            # `aliases_locale` is the RAW list stored for this locale — what the editor
            # loads and writes back, so saving zh aliases can't absorb the en fallbacks.
            default_locale = "en"
            if m is not None:
                raw_i18n = m.aliases_i18n.get(locale)
                aliases_locale = list(
                    raw_i18n if raw_i18n is not None
                    else (m.aliases if locale == default_locale else [])
                )
            else:
                aliases_locale = []
            node_config[key] = {
                # The path down to this line. A total printed at statement level has no section
                # above it, so it breadcrumbs to the statement alone rather than borrowing the
                # heading of whichever section happens to precede it.
                "breadcrumb": " / ".join([stmt_label, *trail]),
                "label": label,
                "canonical_key": key,
                # Does the RULEBOOK IN FORCE map this line? A template node no mapping mentions has
                # nothing for the editor to write to: the concept PATCH answers 404 "not in this
                # ontology", and offering the key in the confusable-with or netting pickers gets a
                # 422 for naming an unknown concept. The screen reads this to render such a node
                # read-only and say why, instead of an editor whose Save is always refused. The
                # shipped template has two — `bs_liabilities__total_liabilities` and
                # `bs_equity__equity_attributable_to_owners` — and making the calculated totals
                # selectable at all is what put the first of them in front of an analyst.
                "mapped": key in declared,
                "aliases_locale": aliases_locale,
                "aliases": (m.aliases_for(locale) if m else [])[:12],
                "sign": (
                    _ONT_SIGN_UI.get(str(m.sign_rule.convention.value), "as_reported")
                    if m is not None and (m.sign_rule.convention.value or "") != "natural"
                    else _SIGN_UI.get(str(node.get("sign", "natural")), "auto")
                ),
                # The criteria the LLM reasons over, so the editor can show and change what
                # actually drives meaning-based mapping rather than only string matching.
                "definition": (m.definition or m.description or "") if m else "",
                "include": list(m.include) if m else [],
                "exclude": list(m.exclude) if m else [],
                "confusable_with": list(m.confusable_with) if m else [],
                "value_scope": (m.value_scope if m else "exclusive_leaf"),
                "keyword_hints": list(m.keyword_hints) if m else [],
                "regex_hints": list(m.regex_hints) if m else [],
                "exclude_hints": list(m.exclude_hints) if m else [],
                "value_type": "Monetary",
                "aggregation": "Sum of children" if node.get("role") in ("subtotal", "total")
                               else "Direct value",
                "netting": {"expr": "", "explain": decomp or "No note-decomposition rule for this concept."},
            }
            emit(children, lvl + 1, [*trail, label], stmt_label)

    for stmt in tdef.get("statements", []):
        stmt_label = _loc(stmt, locale) or str(stmt.get("type", "")).replace("_", " ").title()
        tree.append({"id": f"stmt:{stmt.get('type')}", "label": stmt_label, "lvl": 0, "head": True})
        emit(stmt.get("sections") or [], 1, [], stmt_label)

    return {"tree": tree, "node_config": node_config, "netting_rules": netting_rules,
            # Which ontology version supplied the aliases/sign above — the editor PATCHes this
            # id, and `locale` tells it which alias list it is editing.
            "ontology": ({"id": ont_row.id, "ontology_key": ont_row.ontology_key,
                          "version": ont_row.version, "locale": locale} if ont_row else None),
            # `line_items` is every keyed non-heading node, which is the count `_publish` reports on
            # upload — one quantity, one spelling. It disagreed before this walk was fixed: the
            # seventeen lines printed at statement level were counted as headings here and as line
            # items there, so the screen said 170 about a template published as 187.
            "template": {"key": row.template_key, "name": row.name, "line_items": leaves}}
