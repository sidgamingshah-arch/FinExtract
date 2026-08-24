"""Seeded demo-project endpoints — the data the workspace/review/notes/export views
render. Uploaded documents use the real pipeline (documents/extractions routers);
this router serves the reviewed Ind-AS sample project end-to-end.
"""
from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.config import get_settings
from app.sample.demo import CONF_PCT, DEMO, localize_label
from app.sample.i18n_data import tr
from app.security import Permission, current_principal, require
from app.services import review_lines
from app.services.export import build_json, build_xlsx
from app.services.page_scope import scope_counts

# Every project endpoint requires an authenticated caller (session token, or the
# X-Role dev header when enabled). Per-action permissions are enforced with require().
router = APIRouter(prefix="/projects", tags=["projects"],
                   dependencies=[Depends(current_principal)])

# In-memory edit overrides: {line_item_id: {"value": int, "formula": str}}.
_OVERRIDES: dict[str, dict] = {}


def _active() -> bool:
    """Whether the seeded sample project is loaded. Off = greenfield (empty app)."""
    from app.services.settings_state import get_seed_demo

    return get_seed_demo()


# Greenfield placeholder project meta — shape-compatible with the demo project so the
# shell renders, but carrying no data. Screens short-circuit to an empty state via the
# `loaded` flag returned by GET /projects/{id}.
_EMPTY_PROJECT = {
    "id": "demo", "entity": "", "title": "No project yet", "filename": "",
    "pages": 0, "standard": "", "currency": "", "currency_symbol": "", "units": "",
    "periods": ["", ""], "bases": ["consolidated", "standalone"],
    # `progress` is filled in by `get_project` from `_demo_progress`, on both paths, so the
    # greenfield zeros are counted from empty inputs rather than written out as zeros.
    "template": {"key": "", "name": "— none selected —", "line_items": 0},
    "ontology": {"file": "— none —", "rules": 0, "aliases": 0, "status": "none"},
}

_STD_SCALE = 0.88
# ONE chip per statement — a label naming what the viewer is showing, matching the single-chip
# shape the real routes serve (api/routes/documents.py:1599 and the statement builders).
#
# Each statement used to carry a second `active: False` note chip ("Note 12 · p.171"). An inactive
# chip beside an active one IS a tab control, and this viewer has no second tab to show: the sample
# source pane is a rendered paper mock with no pages behind it, so there is no p.171 to navigate
# to. The note references are not lost — every callout below names them in prose the reader can
# act on, instead of a chip that cannot be clicked.
_VIEWER = {
    "balance_sheet": {
        "company": "RELIANCE INDUSTRIES LIMITED",
        "subtitle": "Consolidated Balance Sheet as at 31 March 2025",
        "chips": [{"label": "BS · p.142", "active": True}],
        "callout": "↳ Linked to Note 12 — Trade receivables (p.171). Face value shown net of ₹12,400 cr related-party receivables reclassified under Note 12.3.",
    },
    "profit_and_loss": {
        "company": "RELIANCE INDUSTRIES LIMITED",
        "subtitle": "Consolidated Statement of Profit and Loss for the year ended 31 March 2025",
        "chips": [{"label": "P&L · p.145", "active": True}],
        "callout": "↳ Finance costs (Note 25) flagged: extracted as a credit; ontology expects an expense (negative).",
    },
    "cash_flow": {
        "company": "RELIANCE INDUSTRIES LIMITED",
        "subtitle": "Consolidated Statement of Cash Flows for the year ended 31 March 2025",
        "chips": [{"label": "CF · p.149", "active": True}],
        "callout": "↳ Closing cash ties to Note 13 (Cash & bank balances) and the face of the Balance Sheet.",
    },
}


def _demo_statement_line_items(statements: dict) -> int:
    """Statement rows that ARE line items — the sample's answer to "how many line items", which is
    the question the Export footer and the shell's progress card both put this figure under.

    A DIFFERENT QUANTITY from the review header's "lines with no finding", and no longer confused
    with it: that tile counts LINES, and a subtotal and a total are lines (they are what the balance
    card names) even though neither is a line item. Its population lives in
    services/review_lines.py, shared with the real route.

    THE EXPORT FOOTER'S OTHER PATH COUNTS THAT OTHER POPULATION, under its own word — a real run's
    rows are lines, the subtotals the mapper promotes included, so `len(rows)` was never an answer to
    THIS question, and for a while one label ("line items") headed both numbers. Keep the two words
    apart: whichever of them a future figure needs, it is the label that has to name the population.
    """
    return sum(1 for s in statements.values() for r in s["rows"] if r.get("kind") == "item")


def _demo_progress(statements: dict, checks: list[dict]) -> dict:
    """The sample project's progress figures, COUNTED from the data the sample serves.

    These were the literals {"pct": 72, "line_items": 148, "in_review": 12} in sample/demo.py,
    read by the Export footer — 148 line items over 33 rows, 12 in review over 4 findings, and a
    72% that was never a ratio of anything. `in_review` is the review route's own `open` count, so
    the Export footer and the Review header cannot state different numbers of outstanding
    findings for one seeded dataset.

    `pct` is GONE rather than derived: "how far through the workflow is this project" has no source
    in the sample at all, and a number with no source is the thing this codebase keeps deleting.
    """
    return {"line_items": _demo_statement_line_items(statements),
            "in_review": _demo_review_summary(checks, statements=statements)["open"]}


@router.get("/{project_id}")
def get_project(project_id: str) -> dict:
    if project_id != "demo":
        raise HTTPException(404, "Unknown project")
    if _active():
        checks = [_sample_check(c) for c in DEMO["review"]]
        return {"project": {**DEMO["project"],
                            "progress": _demo_progress(DEMO["statements"], checks)},
                "documents": DEMO["documents"], "loaded": True}
    return {"project": {**_EMPTY_PROJECT, "progress": _demo_progress({}, [])},
            "documents": [], "loaded": False}


@router.get("/{project_id}/integrity")
def get_integrity(project_id: str, locale: str = Query("en")) -> dict:
    if not _active():
        return {"score": 0, "grade": "", "summary": "", "stats": [], "issues": []}
    data = deepcopy(DEMO["integrity"])
    if locale != "en":
        data["grade"] = tr(data["grade"], locale)
        data["summary"] = tr(data["summary"], locale)
        for s in data["stats"]:
            s["label"] = tr(s["label"], locale)
            s["sub"] = tr(s["sub"], locale)
        for i in data["issues"]:
            i["title"] = tr(i["title"], locale)
            i["detail"] = tr(i["detail"], locale)
            i["note"] = tr(i["note"], locale)
            i["status"] = tr(i["status"], locale)
    return data


@router.get("/{project_id}/pages")
def get_pages(project_id: str, locale: str = Query("en")) -> dict:
    if not _active():
        return {"pages": [], "filters": [], "focused": 0, "total": 0, "skipped": 0}
    pages = deepcopy(DEMO["pages"])
    # Counted from the cards rather than served as literals: this route used to answer
    # `focused: 14, total: 84, skipped: 70` over ten page cards.
    counts = scope_counts(pages)
    if locale != "en":
        for p in pages:
            p["cls"] = tr(p["cls"], locale)
            p["sub"] = tr(p["sub"], locale)
        for f in counts["filters"]:
            f["label"] = tr(f["label"], locale)
    return {"pages": pages, **counts}


# Every statement the app can ask for. The sample project carries only some of them.
_KNOWN_STATEMENTS = ("balance_sheet", "profit_and_loss", "cash_flow", "changes_in_equity")


def _scale(v, basis: str):
    if v is None:
        return None
    return v if basis == "consolidated" else round(v * _STD_SCALE)


@router.get("/{project_id}/statements/{statement}")
def get_statement(project_id: str, statement: str,
                  basis: str = Query("consolidated"),
                  locale: str = Query("en")) -> dict:
    if not _active():
        return {"statement": statement, "label": "", "basis": basis, "periods": ["", ""],
                "currency": "", "currency_symbol": "", "units": "", "rows": [],
                "viewer": {"company": "", "subtitle": "", "chips": [], "callout": ""}}
    st = DEMO["statements"].get(statement)
    if st is None:
        # A statement the app knows about but this sample does not carry (the demo has no
        # statement of changes in equity) is an EMPTY statement, not an error — the tab exists
        # for every document, and 404 would surface as a failure rather than "nothing here".
        if statement in _KNOWN_STATEMENTS:
            return {"statement": statement, "label": "", "basis": basis, "periods": ["", ""],
                    "currency": "", "currency_symbol": "", "units": "", "rows": [],
                    "viewer": {"company": "", "subtitle": "", "chips": [], "callout": ""}}
        raise HTTPException(404, f"Unknown statement {statement!r}")
    proj = DEMO["project"]
    rows = []
    for r in st["rows"]:
        kind = r.get("kind", "item")
        # label = localized output; source_label = original (English) for the source view
        row = {"id": r["id"], "label": localize_label(r["label"], locale),
               "source_label": r["label"], "kind": kind}
        if kind == "item":
            row["level"] = 1
            row["note"] = r.get("note")
            row["note2"] = r.get("note2", r.get("note"))
            row["status"] = r.get("status")
            if r.get("conf"):
                row["confidence"] = {"cat": r["conf"], "pct": CONF_PCT[r["conf"]]}
            row["editable"] = True
            insp = DEMO["inspector"].get(r["id"], DEMO["default_inspector"])
            row["inspector"] = insp
        v1 = _scale(r.get("v1"), basis)
        v2 = _scale(r.get("v2"), basis)
        ov = _OVERRIDES.get(r["id"])
        if ov and kind == "item":
            v1 = ov.get("value", v1)
            row["status"] = "edited"
            row["formula"] = ov.get("formula")
        row["v1"] = v1
        row["v2"] = v2
        rows.append(row)
    viewer = deepcopy(_VIEWER.get(statement, _VIEWER["balance_sheet"]))
    if locale != "en":
        viewer["subtitle"] = tr(viewer["subtitle"], locale)
        viewer["callout"] = tr(viewer["callout"], locale)
    return {
        "statement": statement, "label": localize_label(st["label"], locale), "basis": basis,
        "periods": proj["periods"], "currency": proj["currency"],
        "currency_symbol": proj["currency_symbol"], "units": proj["units"],
        "rows": rows, "viewer": viewer,
    }


class EditBody(BaseModel):
    value: float | None = None
    formula: str | None = None


@router.patch("/{project_id}/line-items/{item_id}",
              dependencies=[Depends(require(Permission.EXTRACTION_EDIT))])
def edit_line_item(project_id: str, item_id: str, body: EditBody) -> dict:
    value = None if body.value is None else round(body.value)
    _OVERRIDES[item_id] = {"value": value, "formula": body.formula or ""}
    return {"id": item_id, "value": value, "formula": body.formula or "", "status": "edited"}


@router.delete("/{project_id}/line-items/{item_id}",
               dependencies=[Depends(require(Permission.EXTRACTION_EDIT))])
def revert_line_item(project_id: str, item_id: str) -> dict:
    _OVERRIDES.pop(item_id, None)
    return {"id": item_id, "reverted": True}


@router.get("/{project_id}/notes")
def get_notes(project_id: str, locale: str = Query("en")) -> dict:
    if not _active():
        return {"notes": [], "count": 0, "linked": 0}
    notes = deepcopy(DEMO["notes_index"])
    for n in notes:
        # The wire key is a string on both paths — see ``get_note``.
        n["no"] = str(n["no"])
    if locale != "en":
        for n in notes:
            n["title"] = tr(n["title"], locale)
    # Both counted, not asserted: the header used to read "48 notes · linked to 96 line items"
    # above a list of twelve. `linked` is the number of statement lines that cite one of these
    # notes, which is the same quantity the real route derives from a run's own rows.
    return {"notes": notes, "count": len(notes), "linked": _demo_linked_lines(notes)}


def _demo_linked_lines(notes: list[dict]) -> int:
    """Statement line items citing one of `notes` — the sample's answer to "linked to N lines"."""
    cited = {str(n["no"]) for n in notes}
    return sum(1 for s in DEMO["statements"].values() for r in s["rows"]
               if str(r.get("note") or "") in cited)


@router.get("/{project_id}/notes/{note_no}")
def get_note(project_id: str, note_no: str, locale: str = Query("en")) -> dict:
    # `periods` is read from the SAME list get_statement serves (DEMO["project"]["periods"]), which
    # is what makes the sample Workspace and the sample Notes screen structurally unable to label
    # the same figures differently. A blank pair is the empty-state shape; the client treats blank
    # as absent and falls back to its own Current/Prior wording.
    if not _active():
        return {"no": note_no, "title": "", "rows": [], "periods": ["", ""],
                "reconciliation": None}
    # The sample's own note numbers are int literals in ``sample/demo.py``; the WIRE key is a
    # string, because a real filing numbers a note "16(b)" and the two paths serve one shared type.
    # Compared as strings here rather than restating the sample data in a second spelling.
    detail = next((d for k, d in DEMO["note_detail"].items() if str(k) == note_no), None)
    if detail is None:
        idx = next((n for n in DEMO["notes_index"] if str(n["no"]) == note_no), None)
        if idx is None:
            raise HTTPException(404, "Unknown note")
        return {"no": note_no, "title": tr(idx["title"], locale), "rows": [],
                "periods": ["", ""], "reconciliation": None}
    detail = deepcopy(detail)
    detail["no"] = str(detail["no"])
    detail["periods"] = list(DEMO["project"]["periods"])
    if locale != "en":
        detail["title"] = tr(detail["title"], locale)
        detail["linked_label"] = tr(detail["linked_label"], locale)
        detail["reconciliation"] = tr(detail["reconciliation"], locale)
        for r in detail["rows"]:
            r["label"] = tr(r["label"], locale)
    return detail


# The judgement/fix fields every sample check carries, so the shared TypeScript type is TOTAL and
# the screen needs no "is this the demo?" branch. `subject_key: None` is the signal that this
# finding cannot be judged: the sample has no run and no document row to key a judgement to, so
# the accept control is absent here rather than rendered and dead. `fix_action: None` says the same
# about the mechanical fix — the two buttons that did nothing disappear on the demo path too.
def _sample_check(check: dict) -> dict:
    """One sample finding in the same shape the real route serves.

    ``names`` — the lines a finding indicts — is DERIVED from the check's own ``target`` rather than
    stored beside it: the sample states one target per finding, and the review header counts the
    line items no finding names. A hand-written second list would be the same quantity twice.
    """
    return {**check, **_SAMPLE_JUDGEMENT_FIELDS,
            "names": [check["target"]] if check.get("target") else []}


_SAMPLE_JUDGEMENT_FIELDS = {
    "subject_key": None, "status": "open", "judgement": None,
    "ambiguous": False, "ambiguous_count": 0, "fix_action": None,
    # No subject is keyed here, so two sample findings can never collide on one identity: the
    # conflict state the real route can serve is impossible, and the fields say so outright rather
    # than being absent for the client to guess at.
    "conflict": False, "conflict_count": 0, "conflict_note": "", "judgement_withheld": False,
    "inputs_edited": False, "inputs_edited_keys": [], "inputs_edited_note": "",
    # `remap: None` for the same reason as `fix_action`: the sample has no run whose rows a re-map
    # could write to, so the control is absent here rather than rendered and dead.
    "remap": None,
}


@router.get("/{project_id}/review")
def get_review(project_id: str, locale: str = Query("en")) -> dict:
    if not _active():
        return {"run_id": "", "checks": [], "tabs": [],
                # Greenfield: no checks and no statements, so every count derives to zero from
                # the empty inputs rather than being written out as four zeros.
                "summary": _demo_review_summary([], statements={}),
                "judgements": {"orphaned": []},
                "coverage": _sample_coverage(locale), "remap_targets": []}
    checks = [_sample_check(c) for c in deepcopy(DEMO["review"])]
    tabs = _demo_review_tabs(checks)
    if locale != "en":
        for c in checks:
            c["title"] = tr(c["title"], locale)
            c["where"] = tr(c["where"], locale)
            c["severity"] = tr(c["severity"], locale)
            c["fix"] = tr(c["fix"], locale)
            c["calc"] = [[tr(row[0], locale), tr(row[1], locale), row[2]] for row in c["calc"]]
        for t in tabs:
            t["label"] = tr(t["label"], locale)
    return {"run_id": "", "checks": checks, "tabs": tabs,
            "summary": _demo_review_summary(checks),
            "judgements": {"orphaned": []},
            # No run means no rows to re-map, so the sample offers no targets — the same reason
            # every sample check carries `remap: None`.
            "coverage": _sample_coverage(locale), "remap_targets": []}


def _sample_coverage(locale: str) -> dict:
    """Coverage stated as unavailable on the sample path, never rendered as zeros.

    The sample carries no structural validation run, so there is nothing to report — and "0 of 0
    relations evaluated" is the exact misread services/coverage.py exists to prevent.
    """
    return {"available": False, "reason": "sample",
            "reason_label": tr("The seeded sample project carries no structural validation run.",
                               locale)}


# Tab labels for the sample's check types, in the order the tabs are shown. The COUNTS are never
# written here — see `_demo_review_tabs`.
_DEMO_TAB_LABELS = (("balance", "Balance check"), ("subtotal", "Subtotals"),
                    ("sign", "Sign anomalies"), ("note", "Note reconciliation"))


def _demo_review_tabs(checks: list[dict]) -> list[dict]:
    """One tab per check type, counted from `checks`, and carrying the type it selects.

    The literals these replace claimed "All 12 · Balance check 1 · Subtotals 4 · Sign anomalies 3
    · Note reconciliation 4" over a list of four checks, one of each type. `types` is what the
    client filters by — see the same field on the real route in api/routes/documents.py.
    """
    return [{"label": "All", "count": len(checks), "types": None}] + [
        {"label": label, "count": sum(1 for c in checks if c.get("type") == kind),
         "types": [kind]}
        for kind, label in _DEMO_TAB_LABELS]


def _demo_lines_with_no_finding(statements: dict, checks: list[dict]) -> int:
    """The sample's statement LINES that no served finding names.

    Read off each check's ``names`` — the same field the real route serves for the same purpose
    (api/routes/documents.py::_accounting_checks) — so the two paths spell one definition. On the
    sample a finding names exactly the row id in its ``target``; those are the ids in
    ``demo.BALANCE_SHEET`` and its siblings, so this is derived from the two things being served
    rather than assumed. Counting `len(checks)` instead — which is what `passed` used to subtract —
    assumed one finding per line item and that every target IS a line item.

    THE POPULATION IS THE SHARED ONE (services/review_lines.py), not this route's own. It used to be
    ``kind == "item"`` only, so the tile answered 31 over 33 item rows while the same statements
    served 6 subtotal and 4 total rows too — 8 of which no finding names — understating the quantity
    its own label names by 8, under a label identical to the real route's.
    """
    named = {n for c in checks for n in (c.get("names") or [])}
    rows = [r for s in statements.values() for r in s["rows"]]
    return review_lines.lines_with_no_finding(
        rows, lambda _i, r: str(r.get("id") or "") in named)


def _demo_review_summary(checks: list[dict], statements: dict | None = None) -> dict:
    """`open` is the findings actually served; `passed` the statement lines NAMED BY NONE of them.

    `open: 12, passed: 136` were literals over four checks. `passed` is the same QUANTITY over the
    same POPULATION the real route serves under the same header tile — "lines with no finding", i.e.
    statement lines no served finding names (services/review_lines.py, called by both routes) — and
    not a second, unrelated number. It used to be `lines - len(checks)`, which is a different
    definition again: it assumed one finding per line item; then it became the item rows less the
    item rows a finding names, which excluded the sample's 6 subtotal and 4 total rows from a
    population the real path included.

    `accepted` and `stale` are COUNTED from the same list rather than written as zeros, so if the
    sample ever gained a judged finding the header could not keep saying none were judged.
    """
    if statements is None:
        statements = DEMO["statements"]
    accepted = sum(1 for c in checks if c.get("status") == "accepted")
    stale = sum(1 for c in checks if c.get("status") == "stale")
    # A conflict — two findings the queue cannot tell apart — is impossible on the sample path
    # (`subject_key: None`, so nothing is keyed at all), but it is COUNTED here rather than
    # written as a zero: the real route serves the same key, and a hand-written zero beside a
    # counted one is how the two shapes drift.
    conflict = sum(1 for c in checks if c.get("status") == "conflict")
    open_count = sum(1 for c in checks if c.get("status") != "accepted")
    return {"open": open_count, "accepted": accepted, "stale": stale, "conflict": conflict,
            "passed": _demo_lines_with_no_finding(statements, checks)}


@router.get("/{project_id}/template",
            dependencies=[Depends(current_principal)])
def get_template_tree(project_id: str, locale: str = Query("en")) -> dict:
    # Viewing the template structure is reference information any authenticated worker
    # needs (the analyst selects into it). Authoring/editing stays admin-only, enforced
    # on the write endpoints (POST /templates, CONFIG_TEMPLATE).
    if not _active():
        return {"tree": [], "node_config": {}, "template": _EMPTY_PROJECT["template"]}
    tree = deepcopy(DEMO["template_tree"])
    node_config = deepcopy(DEMO["node_config"])
    if locale != "en":
        for node in tree:
            node["label"] = localize_label(node["label"], locale)
        for cfg in node_config.values():
            cfg["breadcrumb"] = tr(cfg["breadcrumb"], locale)
            cfg["label"] = localize_label(cfg["label"], locale)
            cfg["value_type"] = tr(cfg["value_type"], locale)
            cfg["aggregation"] = tr(cfg["aggregation"], locale)
            if "netting" in cfg:
                cfg["netting"]["explain"] = tr(cfg["netting"]["explain"], locale)
    return {"tree": tree, "node_config": node_config, "template": DEMO["project"]["template"]}


@router.get("/{project_id}/export-options")
def get_export_options(project_id: str) -> dict:
    return {"options": DEMO["export_options"]}


@router.get("/{project_id}/commentary",
            dependencies=[Depends(require(Permission.COMMENTARY_VIEW))])
def get_commentary(project_id: str, locale: str = Query("en")) -> dict:
    if not _active():
        return {"headline": "", "assessment": "", "metrics": [], "trends": [],
                "strengths": [], "weaknesses": [], "data_quality": "", "basis": ""}
    from app.services.commentary import build_commentary

    # The findings actually served, not a stored total: the commentary reads how many are open.
    c = build_commentary(open_review_items=len(DEMO["review"]))
    if locale != "en":
        c["headline"] = tr(c["headline"], locale)
        c["assessment"] = tr(c["assessment"], locale)
        c["data_quality"] = tr(c["data_quality"], locale)
        c["basis"] = tr(c["basis"], locale)
        c["strengths"] = [tr(s, locale) for s in c["strengths"]]
        c["weaknesses"] = [tr(w, locale) for w in c["weaknesses"]]
        for mtr in c["metrics"]:
            mtr["label"] = tr(mtr["label"], locale)
        for tnd in c.get("trends", []):
            tnd["label"] = tr(tnd["label"], locale)
    return c


@router.get("/{project_id}/audit",
            dependencies=[Depends(require(Permission.COMMENTARY_VIEW))])
def get_audit(project_id: str) -> dict:
    """Audit trail of LLM/extraction runs for the SAMPLE PROJECT, with token usage and duration.

    An uploaded document's trail is a different key and a different route — see
    ``GET /documents/{id}/audit``. This one folds in the seeded rows, which describe the sample.
    """
    from app.services import audit as audit_svc

    seeded = deepcopy(DEMO["audit"]) if (project_id == "demo" and _active()) else []
    return audit_svc.served_trail(project_id, seeded)


@router.post("/{project_id}/analysis",
             dependencies=[Depends(require(Permission.ANALYSIS_RUN))])
def run_project_analysis(project_id: str) -> dict:
    """Run a live LLM financial analysis via the configured provider and record it to
    the audit log with the model's input/output token usage. The run id is derived from
    the entity name + timestamp. Analyst-driven (analysts/reviewers/admin)."""
    from app.ports.registry import registry as reg
    from app.services import audit as audit_svc
    from app.services.analysis_llm import build_demo_payload, run_analysis

    from datetime import datetime, timezone

    settings = get_settings()
    entity = DEMO["project"]["entity"]
    run_id = audit_svc.make_run_id(entity)
    provider_id = settings.llm.provider
    # Timed from before the provider is even resolved, so a failure that never reached the model
    # still reports the interval the caller waited — and success and failure measure the same thing.
    began = datetime.now(timezone.utc)

    def _fail(detail: str):
        audit_svc.record(project_id, audit_svc.AuditEntry(
            run_id=run_id, entity=entity, action="analysis", provider=provider_id,
            model=settings.llm.model, input_tokens=None, output_tokens=None, status="failed",
            duration_ms=audit_svc.elapsed_ms(began),
        ))
        raise HTTPException(status_code=502, detail=detail)

    try:
        provider = reg.get("llm", provider_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        result, meta = run_analysis(provider, build_demo_payload(), max_tokens=settings.llm.max_tokens)
    except Exception as exc:  # provider not configured / unreachable / bad response
        _fail(f"LLM analysis failed: {exc}")

    entry = audit_svc.record(project_id, audit_svc.AuditEntry(
        run_id=run_id, entity=entity, action="analysis", provider=provider_id,
        model=meta.get("model", settings.llm.model),
        input_tokens=meta.get("input_tokens"), output_tokens=meta.get("output_tokens"),
        status="succeeded", duration_ms=audit_svc.elapsed_ms(began),
    ))
    return {"entry": entry.to_dict(), "result": result.model_dump(mode="json")}


@router.post("/{project_id}/submit-review",
             dependencies=[Depends(require(Permission.REVIEW_SUBMIT))])
def submit_for_review(project_id: str, document_id: str | None = Query(default=None),
                      principal=Depends(current_principal)) -> dict:
    """Analyst hands the final output to the reviewer. Recorded to the audit log.

    The REVIEW_SUBMIT permission is only granted to the analyst while the review step
    is enabled (see security.effective_permissions), so this 403s once review is off.

    ``document_id`` NAMES WHAT IS BEING SUBMITTED, and it is the whole point of this signature.
    The Export screen serves an uploaded document and the seeded sample from the same controls, but
    this route hardcoded ``DEMO["project"]["entity"]`` — so submitting a real filing wrote an audit
    entry naming the demo company. An audit trail whose entries name the wrong entity is worse than
    no audit trail: it reads as a submission that happened, for a company nobody submitted.

    The entity comes from the run's own ``result["entity"]`` — the name the extraction read off the
    filing — never re-derived here. A document with no run, or a run that has not named an entity,
    is REFUSED (422) rather than falling back to the demo name: "I do not know whose filing this is"
    is the true answer, and the fallback is exactly the defect.

    Recorded under the DOCUMENT's key when one is named, so the submission appears in that
    document's own trail beside the extraction it is a submission of (``GET /documents/{id}/audit``).
    It used to go under ``project_id`` regardless — correct while the only audit view read the demo
    project's key, and wrong the moment a per-document trail existed, because a real filing's
    submission would have been the one event missing from its own history.
    """
    from app.api.routes.documents import _can_access, _latest_run
    from app.db.base import SessionLocal
    from app.db.models import Document
    from app.services import audit as audit_svc

    if document_id:
        with SessionLocal() as session:
            doc = session.get(Document, document_id)
            # 404 not 403, and the same predicate every other document read uses — existence must
            # not leak across tenants (see documents.authorized_document).
            if doc is None or not _can_access(doc, principal):
                raise HTTPException(status_code=404, detail="Document not found")
            run = _latest_run(session, document_id)
            entity = ((run.result or {}).get("entity") or "").strip() if run is not None else ""
            run_id = run.id if run is not None else ""
        if not entity:
            raise HTTPException(
                status_code=422,
                detail=f"Document {doc.filename!r} has no extracted entity name to submit under; "
                       f"run the extraction first.")
    else:
        # The seeded sample project, which is what this router is for.
        entity = DEMO["project"]["entity"]
        run_id = audit_svc.make_run_id(entity)

    entry = audit_svc.record(document_id or project_id, audit_svc.AuditEntry(
        run_id=run_id, entity=entity, action="submit_review",
        provider="—", model="—", input_tokens=None, output_tokens=None, status="succeeded",
        # No duration: handing output to a reviewer is an instant, not an interval. None renders as
        # "—" rather than "0 ms", which would read as a measured run that took no time.
    ))
    return {"ok": True, "entry": entry.to_dict()}


class ExportBody(BaseModel):
    format: str = "excel"
    basis: str = "consolidated"
    currency: str = "INR"
    units: str = "crore"
    include: dict = {}


@router.post("/{project_id}/export", dependencies=[Depends(require(Permission.EXPORT_RUN))])
def export_project(project_id: str, body: ExportBody) -> Response:
    statements = DEMO["statements"]
    notes_index = DEMO["notes_index"]
    note_detail = DEMO["note_detail"]
    if body.format == "json":
        data = build_json(statements, notes_index, note_detail,
                          basis=body.basis, currency=body.currency, units=body.units)
        return Response(content=data, media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename=extract.json"})
    data = build_xlsx(statements, notes_index, note_detail, basis=body.basis,
                      currency=body.currency, units=body.units, include=body.include)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=spread.xlsx"},
    )
