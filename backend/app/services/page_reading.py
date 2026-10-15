"""Which pages a run reads, and what each one is read for.

ONE RULE, TWO READERS. ``pdf_extract.extract_pdf`` decides which pages it reconstructs, and the Page
Scope screen tells a person what each page will be read for. The screen used to derive its label
from the page's kind and the person's page selection, while the reader applied cases the screen
did not know: a selected page the classifier called 'other' is not read, a face page with no
statement is not read, a selection holding no face or notes page is ignored, and a filing in which
nothing was classified is read whole. So the screen could say "in scope" over a page no run reads,
and "skipped" over one every run reads. Both now ask this module.

What a person can change here: the PAGE SELECTION (``Document.page_scope``, a set of 0-based
indices) and the SECTIONS SWITCHED OFF (``Document.sections_off``). A section is one of the four
statements or the notes. A switched-off section is skipped part by part, so a page shared with a
section that is still on is still read for that one. A selection that holds no page a run reads is
ignored, so it leaves no page out (``selection_applies``).

Works on the classifier's ``PageSource`` objects (the reader) and on the persisted page dicts (the
screen) alike.
"""
from __future__ import annotations

from app.services.page_overrides import classifier_parts
from app.services.statements import ACTIVE_STATEMENTS, ORDER

# The sections Page Scope lists, in the order a set of financial statements is read. The screen
# names them by these ids (frontend/src/screens/ScopePages.tsx SECTION_IDS, held equal by
# tests/test_scope_screen_strings.py).
SECTIONS: tuple[str, ...] = (*ORDER, "notes")


def _get(p, key, default=None):
    """A field of a page dict or of a ``PageSource``; an enum reads as its string value."""
    if isinstance(p, dict):
        return p.get(key, default)
    v = getattr(p, key, default)
    return getattr(v, "value", v)


def section_of(part: dict) -> str | None:
    """The section a part is read for: its statement for a face part naming one, 'notes' for a
    notes part, None for anything else (a part that is not read, or a face with no statement)."""
    if part.get("kind") == "face":
        st = part.get("statement")
        return st if st in ACTIVE_STATEMENTS else None
    if part.get("kind") == "notes":
        return "notes"
    return None


def _is_target(p) -> bool:
    kind, st, sk = _get(p, "kind"), _get(p, "statement"), _get(p, "source_kind")
    return (kind == "notes" or (kind == "face" and st in ACTIVE_STATEMENTS)
            or (sk == "scanned" and st in ACTIVE_STATEMENTS))


def effective_parts(p) -> list[dict]:
    """How the page is cut and what each part is: a person's override when the page carries one
    (``evidence.override_parts``, put there by ``page_overrides.apply``), else the classifier's own
    cut in the same form."""
    ev = _get(p, "evidence") or {}
    if ev.get("override_parts"):
        return [dict(x) for x in ev["override_parts"]]
    d = p if isinstance(p, dict) else p.model_dump(mode="json")
    return classifier_parts(d)


def default_targets(pages) -> list:
    """The pages a run reads before any choice of a person: notes pages, faces of a known
    statement, and scanned pages that carry one. A scanned page has no text layer to match a title
    against, so it would otherwise be dropped before ever reaching the OCR path. Every page when
    nothing was classified at all."""
    targets = [p for p in pages if _is_target(p)]
    return targets or list(pages)


def selection_applies(targets, included) -> bool:
    """Whether a page selection restricts ``targets``: only when it holds one of them. A selection
    holding none of them is ignored, because an empty or non-matching selection must never
    silently extract nothing — every target is read as if there were no selection."""
    if not included:
        return False
    inc = set(included)
    return any(_get(p, "index") in inc for p in targets)


def narrow_targets(targets, included=None, sections_off=None, log=None, dropped=None) -> list:
    """A person's choices on Page Scope applied to ``targets``.

    First the page selection, applied only when it leaves something to read
    (``selection_applies``). Then the switched-off sections: a page all of whose read parts belong
    to switched-off sections is not read at all. A page with a part still switched on stays, and
    the reader skips its other parts one by one.

    ``dropped``, when given, receives ``(page, reason)`` for each page left out, the reason being
    ``out_of_scope`` or ``section_off`` (``services.unread_pages``)."""
    if selection_applies(targets, included):
        inc = set(included)
        if dropped is not None:
            dropped.extend((p, "out_of_scope") for p in targets if _get(p, "index") not in inc)
        targets = [p for p in targets if _get(p, "index") in inc]
        if log:
            log(f"extract:page_scope_applied={sorted(inc)}")
    off = set(sections_off or ())
    if off:
        kept = []
        for p in targets:
            secs = {section_of(x) for x in page_reads(p, True)} - {None}
            if secs and secs <= off:
                if log:
                    log(f"extract:page={_get(p, 'index')}:sections_off={sorted(secs)}")
                if dropped is not None:
                    dropped.append((p, "section_off"))
                continue
            kept.append(p)
        targets = kept
    return targets


def reader_targets(pages, included=None, sections_off=None, log=None) -> list:
    """The pages the next run reads, as the Page Scope screen asks it.

    Without the widening a line declaring ``route=anywhere`` makes (``pdf_extract``): that depends
    on the line-item set a run is pinned to, which the screen does not know, and no shipped line
    declares it."""
    return narrow_targets(default_targets(pages), included, sections_off, log)


def page_reads(p, targeted: bool, sections_off=None) -> list[dict]:
    """The parts of a page the reader reads, top to bottom; [] when the page is not read.

    A page with an override, and a face or notes page, is read part by part. Any other page that
    is read — a scanned page with a statement, or a page of a filing in which nothing was
    classified — is read whole as a face. Parts of a switched-off section are left out."""
    if not targeted:
        return []
    off = set(sections_off or ())
    ev = _get(p, "evidence") or {}
    if ev.get("override_parts") or _get(p, "kind") in ("face", "notes"):
        parts = [x for x in effective_parts(p) if x.get("kind") in ("face", "notes")]
    else:
        parts = [{"from_y": 0.0, "kind": "face", "statement": _get(p, "statement"),
                  "entity": None}]
    return [x for x in parts if (section_of(x) or "") not in off]
