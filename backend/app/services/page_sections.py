"""Page Scope's sections — the four statements and the notes — and how an edit of one becomes the
document's page corrections and page selection.

A SECTION'S PAGES ARE DERIVED, NEVER STORED. A page belongs to a section when one of its parts is
read for it: the classifier's cut, or a person's override (``services.page_overrides``). Storing a
page list per section beside the per-page reading would be one fact in two places, and the reader
only ever reads the per-page one. So what a person changes is the page:

    add(page, S)     the page is read for S: an override of one part, or back to automatic when
                     that is how the classifier reads it already
    remove(page, S)  S's parts on the page are not read (kind "other"); its other parts stay
    pages(S, text)   S reads exactly the pages typed, except a page read for another section or
                     cut into parts, which is left as it is and reported as kept
    on(S, bool)      the section is switched on or off (``Document.sections_off``); its pages keep
                     their reading and are not read while it is off

Every page number here is 1-based — the sheet number under each page on the screen, the card's
``no``. The two older routes, PUT page-overrides and PUT scope, stay 0-based.
"""
from __future__ import annotations

import re

from app.services.page_overrides import ENTITIES, apply_to_page_dicts, classifier_parts, normalise
from app.services.page_reading import (SECTIONS, default_targets, effective_parts, page_reads,
                                       reader_targets, section_of, selection_applies)


class SectionEditError(ValueError):
    """A section edit that cannot be applied. ``code`` and ``params`` let the screen say it in the
    reader's language; ``message`` is the English sentence for any other caller."""

    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code, self.message, self.params = code, message, params


# Typed pages: "6, 9–10", "6 9 10", "6;7", "15-14", "7、8". A range is two numbers joined by a
# hyphen, a dash or a tilde. "p.12" is refused rather than read: in this product "p." names the
# folio PRINTED on a page, not its place in the file, and reading one as the other would move the
# wrong page.
_SEPARATORS = re.compile(r"[,;，；、،\s]+")
_JOINER = re.compile(r"\s*[-–—~～]\s*")
_RANGE = re.compile(r"(\d+)-(\d+)")
_NUMBER = re.compile(r"\d+")


def parse_pages(text, page_count: int) -> list[int]:
    """Sorted 1-based page numbers from typed text (or a list of numbers)."""
    if isinstance(text, (list, tuple)):
        tokens = [str(x) for x in text]
    else:
        flat = _JOINER.sub("-", str(text or "").strip())
        tokens = [t for t in _SEPARATORS.split(flat) if t]
    if not tokens:
        raise SectionEditError("pages_empty", "Give at least one page, like 6 or 14–15.")
    out: set[int] = set()
    for token in tokens:
        m = _RANGE.fullmatch(token)
        if m:
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            numbers = range(a, b + 1)
        elif _NUMBER.fullmatch(token):
            numbers = [int(token)]
        else:
            raise SectionEditError(
                "page_not_a_number",
                f"“{token}” is not a page number. Write pages like 6 or 14–15.", text=token)
        for n in numbers:
            if not 1 <= n <= page_count:
                raise SectionEditError(
                    "page_out_of_range",
                    f"Page {n} is not in this document; it has {page_count} pages.",
                    page=n, pages=page_count)
            out.add(n)
    return sorted(out)


def _membership(applied_pages) -> dict[str, list[int]]:
    """Each section's pages (1-based): a page is in every section one of its parts is read for."""
    members: dict[str, list[int]] = {s: [] for s in SECTIONS}
    for p in applied_pages:
        for s in {section_of(x) for x in effective_parts(p)} - {None}:
            members[s].append(p["index"] + 1)
    return members


def build(applied_pages, scope, sections_off) -> tuple[list[dict], dict[int, dict]]:
    """The sections the screen lists, and per 0-based index what the page is read for.

    ``applied_pages`` are the persisted page dicts with the overrides applied, as the screen lists
    them. ``reads`` is what the next run reads on the page; ``unread`` says why a page is not read:
    ``not_classified`` (no part of it is read for any section), ``out_of_scope`` (a page selection
    leaves it out), ``section_off`` (every section it is in is switched off). A selection the
    reader ignores leaves no page out, so it never makes a page ``out_of_scope``."""
    off = set(sections_off or ())
    chosen = set(scope) if selection_applies(default_targets(applied_pages), scope) else set()
    targeted = {p["index"] for p in reader_targets(applied_pages, scope, off)}
    members = _membership(applied_pages)
    per: dict[int, dict] = {}
    for p in applied_pages:
        idx = p["index"]
        reads = page_reads(p, idx in targeted, sections_off=off)
        unread = None
        if not reads:
            mine = {section_of(x) for x in effective_parts(p)} - {None}
            if mine and chosen and idx not in chosen:
                unread = "out_of_scope"
            elif mine and mine <= off:
                unread = "section_off"
            else:
                unread = "not_classified"
        per[idx] = {"reads": reads, "unread": unread}
    sections = []
    for s in SECTIONS:
        pages = members[s]
        read = [n for n in pages if any(section_of(x) == s for x in per[n - 1]["reads"])]
        sections.append({"id": s, "pages": pages, "read": read, "on": s not in off})
    return sections, per


def _entity_for(applied_by_index, idx: int, section: str, parts, classifier_own) -> str | None:
    """Whose figures a page added to a statement presents, when one click cannot say.

    The classifier's own entity for that statement on this page, else the page's own single face
    entity (a Company balance sheet moved to the income statement is still the Company's), else
    the entity of the page before when its last part is that statement (an untitled continuation
    of the Company statement is the Company's). Otherwise None: read as printed."""
    if section == "notes":
        return None
    for x in classifier_own or []:
        if section_of(x) == section and x.get("entity") in ENTITIES:
            return x["entity"]
    if len(parts) == 1 and parts[0].get("kind") == "face" and parts[0].get("entity") in ENTITIES:
        return parts[0]["entity"]
    prev = applied_by_index.get(idx - 1)
    if prev is not None:
        last = (effective_parts(prev) or [None])[-1]
        if last and section_of(last) == section and last.get("entity") in ENTITIES:
            return last["entity"]
    return None


def apply_edit(raw_pages, overrides, scope, sections_off, items, *, page_count):
    """The document's new ``(overrides, scope, sections_off, kept)`` after a list of section edits.

    ``raw_pages`` are the persisted classified pages (no override applied), ``overrides`` /
    ``scope`` / ``sections_off`` the document's current ones. Items apply in order; within an item,
    typed pages first, then the pages added, then the pages removed, then the switch. ``kept``
    lists the typed pages left as they were: ``{no, reason: "other_section", section}`` for a page
    read for another section, ``{no, reason: "has_parts", parts}`` for a page cut into parts.

    Raises SectionEditError, and nothing is to be written, for a refusal."""
    ovr = {o["page"]: o["parts"] for o in (overrides or [])}
    scope_set = set(scope) if scope else None
    off = set(sections_off or ())
    raw_by_index = {p["index"]: p for p in raw_pages}
    kept: list[dict] = []

    def applied() -> dict[int, dict]:
        cur = [{"page": k, "parts": v} for k, v in sorted(ovr.items())]
        pages = apply_to_page_dicts(raw_pages, cur) if cur else [dict(p) for p in raw_pages]
        return {p["index"]: p for p in pages}

    def settle_selection() -> None:
        # A SELECTION THE READER IGNORES IS DROPPED, NEVER GROWN. It holds no page a run reads, so
        # every page is read as if there were none (`page_reading.selection_applies`); a page added
        # to it would make it count, and the next run would read that page alone.
        nonlocal scope_set
        if scope_set is not None and not selection_applies(
                default_targets(list(applied().values())), scope_set):
            scope_set = None

    def set_parts(idx: int, parts: list[dict]) -> None:
        # BACK TO AUTOMATIC only when the page, with its override gone, reads exactly `parts`. An
        # untitled continuation page takes the reading carried from an override on the page before
        # it, so dropping its own override there would undo the click.
        if parts == classifier_parts(raw_by_index[idx]):
            ovr.pop(idx, None)
            if effective_parts(applied()[idx]) != parts:
                ovr[idx] = parts
        else:
            ovr[idx] = parts
        # An explicit page selection follows: a page now read is in it, a page no longer read for
        # anything is out of it — and a selection left holding no page that is read is dropped.
        if scope_set is not None:
            if any(section_of(x) for x in parts):
                scope_set.add(idx)
            else:
                scope_set.discard(idx)
                settle_selection()

    def add(no: int, s: str, typed: bool = False) -> None:
        idx = no - 1
        cur = applied()
        parts = effective_parts(cur[idx])
        if any(section_of(x) == s for x in parts):
            # Already read for S; a page selection that leaves it out takes it back.
            if scope_set is not None:
                scope_set.add(idx)
            return
        live = [x for x in parts if section_of(x)]
        if live and len(parts) > 1:
            if typed:
                kept.append({"no": no, "reason": "has_parts", "parts": len(parts)})
                return
            raise SectionEditError(
                "page_has_parts",
                f"Page {no} is cut into {len(parts)} parts. Choose which part is read for this "
                f"section in “How this page is read”.", page=no, parts=len(parts), section=s)
        if live and typed:
            kept.append({"no": no, "reason": "other_section", "section": section_of(live[0])})
            return
        own = classifier_parts(raw_by_index[idx])
        if len(own) == 1 and section_of(own[0]) == s:
            set_parts(idx, own)
            return
        if s == "notes":
            new = [{"from_y": 0.0, "kind": "notes", "statement": None, "entity": None}]
        else:
            new = [{"from_y": 0.0, "kind": "face", "statement": s,
                    "entity": _entity_for(cur, idx, s, parts, own)}]
        set_parts(idx, new)

    def remove(no: int, s: str) -> None:
        idx = no - 1
        parts = effective_parts(applied()[idx])
        if not any(section_of(x) == s for x in parts):
            return
        set_parts(idx, [({"from_y": x["from_y"], "kind": "other", "statement": None, "entity": None}
                         if section_of(x) == s else dict(x)) for x in parts])

    def in_range(no) -> None:
        if not isinstance(no, int) or not 1 <= no <= page_count:
            raise SectionEditError(
                "page_out_of_range", f"Page {no} is not in this document; it has {page_count} pages.",
                page=no, pages=page_count)

    settle_selection()
    for item in items:
        s = item["id"]
        if s not in SECTIONS:
            raise SectionEditError("unknown_section", f"Unknown section: {s}.", section=s)
        if item.get("pages") is not None:
            want = set(parse_pages(item["pages"], page_count))
            have = set(_membership(applied().values())[s])
            # Every typed page, those already in S too: a page of S that a page selection leaves
            # out is taken back into it, as a one-click add does, or typing it would save nothing.
            for no in sorted(want):
                add(no, s, typed=True)
            for no in sorted(have - want):
                remove(no, s)
            # Typing a section's pages is asking for them to be read.
            off.discard(s)
        for no in item.get("add") or []:
            in_range(no)
            add(no, s)
            # …and so is adding a page to it. Removing one never switches a section on or off.
            off.discard(s)
        for no in item.get("remove") or []:
            in_range(no)
            remove(no, s)
        if item.get("on") is not None:
            if item["on"]:
                off.discard(s)
            else:
                off.add(s)

    cleaned = normalise([{"page": k, "parts": v} for k, v in sorted(ovr.items())],
                        page_count=page_count)
    new_scope = (sorted(scope_set) or None) if scope_set is not None else None
    return cleaned or None, new_scope, (sorted(off) or None), kept


def selection_after_correction(raw_pages, before, after, scope) -> list[int] | None:
    """The page selection once the page corrections change from ``before`` to ``after`` (PUT
    /page-overrides, the "How this page is read" editor): it gains every page now given a face or
    notes part, so a correction is not silently left out of it. Only while the reader honours it:
    a selection holding no page a run reads is ignored, and is dropped rather than grown into one
    that counts — the next run would read the corrected page alone."""
    def in_force(overrides, selection) -> bool:
        pages = apply_to_page_dicts(raw_pages, overrides) if overrides else raw_pages
        return selection_applies(default_targets(pages), selection)

    if not scope or not in_force(before, scope):
        return None
    read = {o["page"] for o in after or [] if any(p["kind"] != "other" for p in o["parts"])}
    grown = sorted(set(scope) | read)
    return grown if in_force(after, grown) else None


def reads_current_pages(options: dict | None, doc) -> bool:
    """Whether a run read the pages the way the document now says to read them: the same page
    corrections, the same page selection and the same sections switched off.

    ONE TEST, TWO READERS: the extraction's idempotency (a page change asks for a new run) and the
    Page Scope screen's "Extract again to read them". A run recorded before the selection and the
    switches were recorded reads as having neither."""
    o = options or {}
    return (o.get("page_overrides") == (doc.page_overrides or None)
            and o.get("page_scope") == (sorted(doc.page_scope) if doc.page_scope else None)
            and o.get("sections_off") == (sorted(doc.sections_off) if doc.sections_off else None))


def demo_payload(cards: list[dict]) -> dict:
    """The sections and read-for fields for the sample project's page cards, which carry a kind
    and a statement and no parts: an included face or notes card is read whole for its section.
    The sample has no file, no runs and no edits."""
    for c in cards:
        part = {"from_y": 0.0, "kind": c.get("kind"), "statement": c.get("statement"),
                "entity": None}
        c["reads"] = [part] if c.get("included") and section_of(part) else []
        c["unread"] = None if c["reads"] else "not_classified"
    sections = []
    for sid in SECTIONS:
        pages = [c["no"] for c in cards
                 if section_of({"kind": c.get("kind"), "statement": c.get("statement")}) == sid]
        read = [c["no"] for c in cards if c["reads"] and section_of(c["reads"][0]) == sid]
        sections.append({"id": sid, "pages": pages, "read": read, "on": True})
    return {"format": None, "sections": sections, "read": sum(1 for c in cards if c["reads"]),
            "last_run": None, "locked": False}
