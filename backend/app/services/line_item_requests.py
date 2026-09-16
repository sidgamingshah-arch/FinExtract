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
    # THE FACE'S EQUIVALENT OF `notes`. A line read off the face of a statement selects no note, so
    # the note block a request pays for is empty for it and there is nothing in the request to
    # locate its figure in — the contract's own advice in that position is to answer with an empty
    # `sources`. What such a line needs supplied is the SECTION it may be claimed under, and this
    # names which: the `(statement, section)` pairs, resolved from the line's own gate.
    sections: tuple[tuple[str, str], ...] = ()

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
    #
    # AND THIS IS WHERE EVERY FACE LINE LANDS, which is why the grouping happens here rather than
    # beside the note grouping above. A line read off the face selects no note, so `note_sets`
    # produces no entry for it and it falls through as a request of its own: measured on the
    # shipped set, 377 of the 506 asked-about lines, one request each.
    #
    # GROUPED BY THE SECTION IT MAY BE CLAIMED UNDER, for exactly the reason `group_by_note_set`
    # gives for grouping on the note set: the supplied context is what a request pays for, and
    # lines needing the SAME context amortise one copy of it. Measured on the shipped set, those
    # 377 lines occupy FOURTEEN `(statement, section)` groups — 88 on the income statement's own
    # section, 51 on non-current assets, 50 on current liabilities — and only two groups hold a
    # single line. So the face context costs 14 copies instead of 377.
    #
    # THE MODE STILL GOVERNS, and `"none"` still means one request per line. That is not timidity
    # about the default: the reason `config` gives for it is that lines sharing a call can
    # influence each other's answers, and without the per-line baseline there is no way to tell
    # help from bleed. A section block is a bigger shared context than a note block, so it makes
    # that hazard bigger rather than smaller, and the cheap mode stays something a run opts into.
    unplanned = [k for k in by_key if not any(k in plan.keys for plan in plans)]
    if mode in ("identical", "similar"):
        for (statement, section), keys in _by_section(unplanned, by_key).items():
            plans.append(RequestPlan(
                name=keys[0] if len(keys) == 1 else f"{statement}/{section}+{len(keys) - 1}",
                keys=tuple(keys), notes=(),
                sections=((statement, section),)))
        return plans
    for key in unplanned:
        plans.append(RequestPlan(name=key, keys=(key,), notes=notes_of.get(key, ()),
                                 sections=_sections_of_item(by_key[key])))
    return plans


def _sections_of_item(item) -> tuple[tuple[str, str], ...]:
    """The `(statement, section)` pairs this line may be claimed under, from its own gate.

    BOTH HALVES OR NEITHER. A line naming a statement and no section is claimable anywhere on that
    statement, and one naming a section and no statement is claimable under that section wherever
    it is printed; either way the missing half is `""`, which the caller reads as "unconstrained"
    rather than as a section named the empty string. A line that names neither gets no pair at all,
    because "supply me every section of every statement" is not a request, it is the whole filing.
    """
    # `.value` FIRST, BECAUSE `statements` HOLDS AN ENUM. `str(StatementType.PROFIT_AND_LOSS)` is
    # `"StatementType.PROFIT_AND_LOSS"`, and that repr would travel into the request as the name of
    # the statement to supply — a token no reader and no lookup recognises.
    def _tok(x) -> str:
        return str(getattr(x, "value", x) or "")

    statements = [t for t in (_tok(x) for x in (getattr(item, "statements", None) or ())) if t]
    sections = [t for t in (_tok(x) for x in (getattr(item, "section_scope", None) or ())) if t]
    if not statements and not sections:
        return ()
    return tuple((st, sec) for st in (statements or [""]) for sec in (sections or [""]))


def _by_section(keys, by_key: dict) -> dict[tuple[str, str], list[str]]:
    """Group keys by the one `(statement, section)` pair they are claimable under.

    ONLY A LINE WITH EXACTLY ONE PAIR IS GROUPED. A line scoped to two sections belongs in neither
    group's context alone, and putting it in the first would supply it half of what its own gate
    allows — a request that cannot answer the question it asks. Those keep a request of their own,
    which is what they had before this function existed.
    """
    out: dict[tuple[str, str], list[str]] = {}
    for key in keys:
        pairs = _sections_of_item(by_key[key])
        if len(pairs) != 1:
            # NOT A GROUP OF ITS OWN. Measured, 39 of the shipped set's asked-about lines name
            # neither a statement nor a section, and bundling them together was the first thing
            # this function did: one request for 39 lines that share NO context, which saves
            # nothing (there is no block to amortise) and buys the whole cross-influence hazard
            # the `"none"` default exists to avoid. They are asked about one at a time.
            out[("", key)] = [key]
            continue
        out.setdefault(pairs[0], []).append(key)
    return out


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
