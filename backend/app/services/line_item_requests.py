"""WHICH LINE ITEMS SHARE A REQUEST — the reader `llm_request_grouping` never had.

NEW FILE -> backend/app/services/line_item_requests.py

WHY THIS EXISTS. `extraction.llm_request_grouping` and `extraction.llm_group_similarity` have been
declared and read by nothing, because requests are ROW-driven: `mapping.match_batch` takes
`items = [(item_id, source_label)]` — printed rows, chunked by (statement, basis, period) — and
asks "which concept is each of these rows?". Nothing in that path has a line item to group, so the
settings configured a decision no code was making.

The grouping mechanism itself was already built and correct (`services.line_item_notes.note_sets`
and `group_by_note_set`). What was missing is the thing that calls them, decides the FOURTH mode,
and can be read and tested on its own. That is this module.

WHAT IS BEING SAVED, restated here because it is the only reason grouping is worth any complexity.
The note context is what a request pays for — measured on the reference filing, `identified_notes`
is 95,188 of a ~196,000-character request, and the SAME 32 notes go out byte-identical in all 21
calls. Line items needing the same notes can amortise one copy of that block instead of paying for
it each. Measured on the twelve depreciation parts, which all resolve to one note: twelve separate
requests carry ~125,000 characters, one request carrying all twelve carries ~22,000.

WHY "none" IS STILL THE DEFAULT. Grouping has a hazard that has nothing to do with cost: line items
sharing a call can influence each other's answers. Sometimes that helps (a subtotal seen beside the
lines it is made of) and sometimes it bleeds (one figure reused for two questions). Without the
per-line-item baseline there is nothing to compare a grouped run against, so the cheap mode does not
become the default until someone has measured the difference.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services import line_item_notes


@dataclass(frozen=True)
class RequestPlan:
    """One request: the line items it asks about, and the notes it will carry.

    `name` is what the run log calls it. For an authored group that is the author's own name, which
    is the point of authoring one — "Depreciation, one note" in a log is a decision a reader can
    check, where "group 3" is not. For a single line item it is the key.
    """

    name: str
    keys: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def shared(self) -> bool:
        return len(self.keys) > 1


def asked_about(item) -> bool:
    """Whether the model is ever asked about this line — the population being grouped.

    THE SAME BOUNDARY AS `mapping._llm_withheld`, and it has to be, or a plan carries a line no
    request can answer:

      * a DERIVED PARENT is never offered and never matched. Its figure is its declared cascade's,
        settled and not a trade-off to be reopened.
      * a RESIDUAL BUCKET carries a section's unexplained remainder, so a figure filed there makes
        the reconciliation that would have reported the gap tie instead.

    `extraction_mode` is deliberately NOT tested. `note_sets` filtered on it, which was the offer
    boundary when that function was written and is no longer: `extract_or_derive` means the subtotal
    is printed on some filings and arithmetic on others, and those ARE asked about.
    """
    # ONE SPELLING, and it is the schema's. `LineItemDef._never_asked` answers this question for
    # the configuration side (the edit door and the config screen read it), and two answers to one
    # question is how the screen came to believe a derived parent was asked about. Read through it
    # where the object offers it, and fall back to the same three tests for anything that does not
    # — a working-view projection, or a stub in a test.
    never = getattr(item, "_never_asked", None)
    if callable(never):
        return not never()
    if str(getattr(item, "type", "") or "") == "derived":
        return False
    if str(getattr(item, "value_scope", "") or "") == "exclusive_residual":
        return False
    if str(getattr(item, "extraction_mode", "") or "") == "derive":
        return False
    return True


def _note_keys(hits) -> tuple[str, ...]:
    """The note numbers a set of hits names, deduplicated and ordered.

    DEDUPLICATED BY NUMBER, which is also a correction. A note split across pages arrives as
    several tables that all carry its number — measured, note 4 of the reference filing arrives as
    seven fragments and note 47 as six — so a set of four hits is routinely one note seen four
    times. Two line items whose hits are fragments of the same note need the same note, and
    grouping on the raw hit list would say they did not.
    """
    seen: list[str] = []
    for hit in hits or ():
        num = str(getattr(hit, "note", "") or "")
        if num and num not in seen:
            seen.append(num)
    return tuple(seen)


def plan_requests(line_item_set, notes, settings, *, cited=None) -> list[RequestPlan]:
    """The requests to make for one document, in the order they should be made.

    Every line item the model is asked about appears in exactly one plan. That is the property the
    caller depends on and the reason the manual mode falls back per key: a partial master has to be
    usable, or nobody can grow one a group at a time.

    `cited` is `line_item_notes.cited_notes(doc)` — the notes the FILING prints against each line's
    face row, which go into the set ahead of anything a probe found. It reaches the grouping too,
    and that is the point rather than a side effect: two lines that cite the same note now share a
    request, which the scoring alone had no way to notice.
    """
    mode = str(getattr(settings.extraction, "llm_request_grouping", "none") or "none")
    similarity = float(getattr(settings.extraction, "llm_group_similarity", 0.8) or 0.8)
    items = [i for i in (getattr(line_item_set, "items", None) or []) if asked_about(i)]
    by_key = {i.key: i for i in items}

    # The note set per line item, computed once. Needed by every mode: the authored modes group on
    # it, and "manual" still has to say which notes each request carries.
    sets = line_item_notes.note_sets(items, notes, cited=cited)
    notes_of = {key: _note_keys(hits) for key, hits in sets.items()}

    if mode == "manual":
        return _manual_plans(line_item_set, by_key, notes_of)

    plans: list[RequestPlan] = []
    for group in line_item_notes.group_by_note_set(sets, mode=mode, similarity=similarity):
        keys = tuple(k for k in group if k in by_key)
        if not keys:
            continue
        merged: list[str] = []
        for key in keys:
            for num in notes_of.get(key, ()):
                if num not in merged:
                    merged.append(num)
        plans.append(RequestPlan(name=keys[0] if len(keys) == 1 else f"{keys[0]}+{len(keys) - 1}",
                                 keys=keys, notes=tuple(merged)))
    # Anything `note_sets` produced no entry for still has to be asked about — a line item whose
    # prose names no note scores nothing, and scoring nothing is not a reason to skip the line.
    for key in by_key:
        if not any(key in plan.keys for plan in plans):
            plans.append(RequestPlan(name=key, keys=(key,), notes=notes_of.get(key, ())))
    return plans


def _manual_plans(line_item_set, by_key: dict, notes_of: dict) -> list[RequestPlan]:
    """The authored groups, then one request for every asked-about line not in any of them.

    THE FALLBACK IS THE FEATURE. A master naming three groups out of seventy-seven lines is the
    normal state of one being built, and a mode that only asked about the named lines would make
    authoring the first group a regression. So the master says which lines SHARE a request; it does
    not say which lines get one.
    """
    plans: list[RequestPlan] = []
    claimed: set[str] = set()
    for group in (getattr(line_item_set, "request_groups", None) or []):
        keys = tuple(k for k in (getattr(group, "members", None) or []) if k in by_key)
        if not keys:
            continue                       # every member refused or unknown — nothing to ask
        merged: list[str] = []
        for key in keys:
            for num in notes_of.get(key, ()):
                if num not in merged:
                    merged.append(num)
        plans.append(RequestPlan(name=str(getattr(group, "name", "") or keys[0]),
                                 keys=keys, notes=tuple(merged)))
        claimed.update(keys)
    for key in by_key:
        if key not in claimed:
            plans.append(RequestPlan(name=key, keys=(key,), notes=notes_of.get(key, ())))
    return plans


def coverage(line_item_set) -> tuple[int, int]:
    """(lines named by the master, lines the model is asked about) — for the screen and the log.

    A master covering 19 of 77 and one covering all 77 look identical as a list of groups, and only
    one of them is finished. This is the number that tells them apart.
    """
    items = [i for i in (getattr(line_item_set, "items", None) or []) if asked_about(i)]
    keys = {i.key for i in items}
    named = {m for g in (getattr(line_item_set, "request_groups", None) or [])
             for m in (getattr(g, "members", None) or []) if m in keys}
    return len(named), len(keys)
