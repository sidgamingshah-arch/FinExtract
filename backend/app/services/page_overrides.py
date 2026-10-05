"""A person's correction of how a filing's pages are read, part by part.

The classifier decides, from each page's printed titles, whether a page is a statement FACE, a
NOTES page or neither, which statement a face is, and whose figures it presents. It also cuts a
page in two where a second statement starts part-way down (000709 page 88: 5、合并现金流量表 above,
6、母公司现金流量表 from y=0.77). When it is wrong there was no way to say so. An override is that
way: for one page, the PARTS it is made of, top to bottom —

    {"page": 88, "parts": [
        {"from_y": 0.0,  "kind": "face",  "statement": "cash_flow", "entity": "consolidated"},
        {"from_y": 0.77, "kind": "face",  "statement": "cash_flow", "entity": "company"}]}

``from_y`` is where the part begins, as a fraction of the page height from the top; a part runs
to where the next one begins. ``kind`` is ``face``, ``notes`` or ``other`` (not read). A face part
names its ``statement`` and may name its ``entity`` (``consolidated`` / ``company``; left out, the
reader decides from the page as it always has).

An override is saved with the document and REPLACES the classifier's verdict for that page on
every later run, which says so in its log. What a page hands on is its LAST part: an untitled
continuation page after it, read by the classifier as continuing the statement, continues the
override's statement instead.
"""
from __future__ import annotations

from app.core.models.enums import PageKind
from app.services.statements import ACTIVE_STATEMENTS

KINDS = ("face", "notes", "other")
ENTITIES = ("consolidated", "company")
# Classifier evidence that describes a cut or a region the override replaces. Left in place,
# `pdf_extract` would apply the classifier's cut on top of the person's.
_CUT_EVIDENCE = ("statement_before_title", "scope_before_title", "notes_above_title",
                 "face_ends_at_y", "closing_title", "closing_title_y", "scope_after_closing_title")


class OverrideError(ValueError):
    """An override that cannot be applied, with the reason a person can act on."""


def normalise(raw, page_count: int | None = None) -> list[dict]:
    """Validated overrides, one entry per page, parts sorted top to bottom. Raises OverrideError
    naming the page and the part. An entry with no parts is dropped: it removes that override."""
    out: dict[int, dict] = {}
    for entry in raw or []:
        if not isinstance(entry, dict):
            raise OverrideError("each override is an object with 'page' and 'parts'")
        page = entry.get("page")
        if not isinstance(page, int) or page < 0 or (page_count is not None and page >= page_count):
            raise OverrideError(f"page {page!r} is not a page of this document (0-based index)")
        parts_in = entry.get("parts") or []
        if not parts_in:
            out.pop(page, None)
            continue
        parts = []
        for n, part in enumerate(parts_in, 1):
            where = f"page {page + 1}, part {n}"
            try:
                y = float(part.get("from_y", 0.0))
            except (TypeError, ValueError):
                raise OverrideError(f"{where}: from_y must be a number between 0 and 1") from None
            if not 0.0 <= y < 1.0:
                raise OverrideError(f"{where}: from_y must be at least 0 and below 1")
            kind = part.get("kind")
            if kind not in KINDS:
                raise OverrideError(f"{where}: kind must be one of {', '.join(KINDS)}")
            statement = part.get("statement") or None
            entity = part.get("entity") or None
            if kind == "face":
                if statement not in ACTIVE_STATEMENTS:
                    raise OverrideError(f"{where}: a face part names its statement — one of "
                                        f"{', '.join(sorted(ACTIVE_STATEMENTS))}")
                if entity is not None and entity not in ENTITIES:
                    raise OverrideError(f"{where}: entity must be consolidated or company")
            else:
                statement = entity = None
            parts.append({"from_y": round(y, 4), "kind": kind, "statement": statement,
                          "entity": entity})
        parts.sort(key=lambda p: p["from_y"])
        if parts[0]["from_y"] != 0.0:
            raise OverrideError(f"page {page + 1}: the first part must start at the top (from_y 0)")
        if len({p["from_y"] for p in parts}) != len(parts):
            raise OverrideError(f"page {page + 1}: two parts start at the same place")
        out[page] = {"page": page, "parts": parts}
    return [out[k] for k in sorted(out)]


def _page_verdict(parts: list[dict]) -> tuple[PageKind, str | None, str | None]:
    """The page-level kind, statement and entity a multi-part page presents to the stages that
    read pages whole: a face if any part is one, and the statement of its LAST face part — the one
    still running when the page ends."""
    faces = [p for p in parts if p["kind"] == "face"]
    if faces:
        return PageKind.FACE, faces[-1]["statement"], faces[-1]["entity"]
    if any(p["kind"] == "notes" for p in parts):
        return PageKind.NOTES, None, None
    return PageKind.OTHER, None, None


def apply(pages, overrides, log=None) -> list[int]:
    """Apply overrides onto classified ``PageSource`` objects in place; returns the pages changed.

    Run straight after the classifier, so every stage after it — extraction, the statement gate,
    the notes pruning — reads the corrected pages. Then carries the last part on: a following
    face page the classifier called an untitled continuation, and nobody overrode, takes the
    override's closing statement and entity, up to the first page with a title of its own."""
    log = log or (lambda _msg: None)
    by_index = {p.index: p for p in pages}
    changed: list[int] = []
    for entry in overrides or []:
        ps = by_index.get(entry["page"])
        if ps is None:
            continue
        parts = entry["parts"]
        kind, statement, entity = _page_verdict(parts)
        before = f"{ps.kind.value}:{ps.statement or '-'}:{ps.scope or '-'}"
        ps.kind, ps.statement, ps.scope = kind, statement, entity
        ev = {k: v for k, v in (ps.evidence or {}).items() if k not in _CUT_EVIDENCE}
        ev["override_parts"] = parts
        ps.evidence = ev
        if "user_override" not in ps.classification_evidence:
            ps.classification_evidence.append("user_override")
        changed.append(ps.index)
        log(f"classify:page={ps.index}:override({before} -> "
            + " | ".join(f"{p['from_y']:.2f}:{p['kind']}"
                         + (f":{p['statement']}:{p['entity'] or 'auto'}" if p["kind"] == "face"
                            else "") for p in parts) + ")")

    overridden = {e["page"] for e in overrides or []}
    ordered = sorted(pages, key=lambda p: p.index)
    for pos, ps in enumerate(ordered):
        if ps.index not in overridden:
            continue
        last = (ps.evidence or {}).get("override_parts", [])[-1:]
        if not last or last[0]["kind"] != "face":
            continue
        for nxt in ordered[pos + 1:]:
            if (nxt.index in overridden or nxt.kind != PageKind.FACE
                    or (nxt.evidence or {}).get("matched_title")):
                break
            if (nxt.statement, nxt.scope) != (last[0]["statement"], last[0]["entity"]):
                log(f"classify:page={nxt.index}:override_carried_from={ps.index}"
                    f"({nxt.statement or '-'}:{nxt.scope or '-'} -> "
                    f"{last[0]['statement']}:{last[0]['entity'] or 'auto'})")
                nxt.statement, nxt.scope = last[0]["statement"], last[0]["entity"]
                changed.append(nxt.index)
    return sorted(set(changed))


def classifier_parts(page: dict) -> list[dict]:
    """How the CLASSIFIER cut a page, in override form — what the Page Scope screen starts an
    edit from, so a person corrects the reading rather than re-entering it."""
    ev = page.get("evidence") or {}
    kind = page.get("kind")
    if ev.get("override_parts"):
        return ev["override_parts"]
    if kind not in ("face", "notes"):
        return [{"from_y": 0.0, "kind": "other", "statement": None, "entity": None}]
    if kind == "notes":
        return [{"from_y": 0.0, "kind": "notes", "statement": None, "entity": None}]
    own = {"kind": "face", "statement": page.get("statement"),
           "entity": page.get("scope") if page.get("scope") in ENTITIES else None}
    y = ev.get("matched_title_y")
    parts = [{"from_y": 0.0, **own}]
    if isinstance(y, (int, float)) and 0.0 < y < 1.0:
        if ev.get("statement_before_title"):
            before = ev.get("scope_before_title")
            parts = [{"from_y": 0.0, "kind": "face", "statement": ev["statement_before_title"],
                      "entity": before if before in ENTITIES else None},
                     {"from_y": round(y, 4), **own}]
        elif ev.get("notes_above_title"):
            parts = [{"from_y": 0.0, "kind": "notes", "statement": None, "entity": None},
                     {"from_y": round(y, 4), **own}]
    ends = ev.get("face_ends_at_y")
    if isinstance(ends, (int, float)) and parts[-1]["from_y"] < ends < 1.0:
        parts.append({"from_y": round(ends, 4), "kind": "other", "statement": None,
                      "entity": None})
    return parts


def apply_to_page_dicts(pages: list[dict], overrides) -> list[dict]:
    """The same correction on the persisted page dicts the Page Scope screen lists."""
    from app.core.models.document import PageSource

    models = [PageSource.model_validate(p) for p in pages]
    apply(models, overrides)
    return [m.model_dump(mode="json") for m in models]
