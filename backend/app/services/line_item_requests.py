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

WHY "identical" IS NOW THE DEFAULT, and what it costs. Under "none" a run with the focus list off
made one request per line — 418 figure requests on each of the six reference filings, 349 of them
a single face line sent its whole statement. "identical" asks lines needing the same context
together, at most `extraction.llm_lines_per_request` to a request (`capped`). The hazard has not
gone away: line items sharing a call can influence each other's answers — sometimes that helps (a
subtotal seen beside the lines it is made of), sometimes it bleeds (one figure reused for two
questions). "none" stays available as the per-line baseline a grouped run can be compared against;
`app.config.ExtractionSettings.llm_request_grouping` says why the default moved.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from app.services import heading_synonyms, line_item_notes, line_item_routes


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


def plan_requests(line_item_set, notes, settings, *, cited=None, routed=None,
                  stats=None) -> list[RequestPlan]:
    """The requests to make for one document, in the order they should be made.

    Every line item the model is asked about appears in exactly one plan. That is the property the
    caller depends on and the reason the manual mode falls back per key: a partial master has to be
    usable, or nobody can grow one a group at a time.

    `cited` is `line_item_notes.cited_notes(doc)` — the notes the FILING prints against each line's
    face row, which go into the set ahead of anything a probe found. It reaches the grouping too,
    and that is the point rather than a side effect: two lines that cite the same note now share a
    request, which the scoring alone had no way to notice.

    `extraction.llm_note_synonyms` ADDS THE SHARED SYNONYM LIST to each line's heading search
    (`heading_synonyms`), and `routed` — the model's own reading of the headings
    (`note_router.route`) — adds up to two notes per line after everything else. Neither displaces
    a note the configuration found; `stats` is told how many each added (`note_sets`).
    """
    mode = str(getattr(settings.extraction, "llm_request_grouping", "none") or "none")
    similarity = float(getattr(settings.extraction, "llm_group_similarity", 0.8) or 0.8)
    items = [i for i in (getattr(line_item_set, "items", None) or []) if asked_about(i)]
    by_key = {i.key: i for i in items}

    # The note set per line item, computed once. Needed by every mode: the authored modes group on
    # it, and "manual" still has to say which notes each request carries.
    synonyms = (heading_synonyms.extras_by_key(line_item_set)
                if getattr(settings.extraction, "llm_note_synonyms", True) else None)
    sets = line_item_notes.note_sets(items, notes, cited=cited, synonyms=synonyms, routed=routed,
                                     stats=stats)
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
        # AND THE SECTIONS ITS LINES MAY BE CLAIMED UNDER, like every other plan. A line with a note
        # set is not therefore a note-only line: the filing's own citation (`cited`) puts a FACE
        # total into this branch — 000883 prints 七、47 and 七、50 against 负债合计 — and a plan here
        # declared no sections, so `face_statements` built no statement block and the line was
        # asked for "the printed subtotal" with no statement in the request. Measured on 000883:
        # 24 face totals, 负债合计 / 利润总额 / 经营活动产生的现金流量净额 among them. Whether a line
        # may READ the block is still `face_statements`' per-key route test, so a note-only line is
        # given its notes and nothing more.
        plans.append(RequestPlan(name=keys[0] if len(keys) == 1 else f"{keys[0]}+{len(keys) - 1}",
                                 keys=keys, notes=tuple(merged),
                                 sections=_sections_of_keys(keys, by_key)))
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
    # THE MODE STILL GOVERNS, and `"none"` still means one request per line — the per-line
    # baseline an admin picks to compare a grouped run against. "identical" ships as the default
    # (`app.config` says why, and what it gives up): lines sharing a call can influence each
    # other's answers, and a section block is a bigger shared context than a note block, so it
    # makes that hazard bigger rather than smaller.
    unplanned = [k for k in by_key if not any(k in plan.keys for plan in plans)]
    if mode in ("identical", "similar"):
        for pairs, keys in _by_section(unplanned, by_key).items():
            statement = pairs[0][0]
            section = "|".join(dict.fromkeys(sec for _st, sec in pairs))
            plans.append(RequestPlan(
                name=keys[0] if len(keys) == 1 else f"{statement}/{section}+{len(keys) - 1}",
                keys=tuple(keys), notes=(), sections=pairs))
        # WHOLE GROUPS, NOT CUT HERE. A group of more than `llm_lines_per_request` lines is asked
        # in parts (`capped`), but the stage cuts them — after the focus list, the note-tag gate,
        # the text lines and the `anywhere` lines have taken out what they take — so a group is cut
        # by the lines it SENDS. Cut here, the P&L's 89 planned lines were cut into five parts and
        # then lost their 12 `anywhere` lines to the report: 77 lines in five requests, each with
        # the whole statement, where four hold them.
        return plans
    for key in unplanned:
        plans.append(RequestPlan(name=key, keys=(key,), notes=notes_of.get(key, ()),
                                 sections=_sections_of_item(by_key[key])))
    return plans


#: The most line items one grouped request asks about when the settings name no number — the
#: shipped `extraction.llm_lines_per_request`, where `app.config` says why 20.
LINES_PER_REQUEST = 20


def lines_per_request(settings) -> int:
    """`extraction.llm_lines_per_request`, at least 1."""
    try:
        cap = int(getattr(getattr(settings, "extraction", None), "llm_lines_per_request",
                          LINES_PER_REQUEST) or LINES_PER_REQUEST)
    except (TypeError, ValueError):
        cap = LINES_PER_REQUEST
    return max(1, cap)


def capped(plans: list[RequestPlan], cap: int) -> list[RequestPlan]:
    """Every plan of more than `cap` lines, asked in consecutive parts of even size.

    WHY A GROUP IS CUT AT ALL. Grouping is decided by the context lines share, and nothing about a
    shared context bounds how many lines share it: on 688411 one `(statement, section)` group sent
    77 income-statement lines, a reply budget of 31,824 tokens (`stages.line_item_llm._max_tokens`)
    that has to arrive inside one `llm.timeout_seconds` wait, and a request that times out loses
    every line in it to the rules. Cut into parts, each part is a reply of the measured size.

    EVEN PARTS, IN THE GROUP'S OWN ORDER — 77 at 20 is 20, 19, 19, 19, not 20, 20, 20, 17; 50 at 7
    is eight parts of 6 or 7, not seven of 7 and one of 1 — so a section's lines stay beside their
    neighbours and no part is a statement block sent for one line.

    EACH PART IS SENT WHAT ITS GROUP WAS SENT: the group's notes (so a line's `notes_supplied`,
    which `plan_and_notes` takes from its group, is the note block its request carries) and the
    group's sections — of which `face_statements` supplies the statements the part's own lines may
    read on the face, so a part holding only note lines carries no statement block, as such a
    group would not.

    CALLED BY THE STAGE on the plans it is about to send (`stages.line_item_llm`) — the report
    batches of the `anywhere` lines among them, which are at most `REPORT_LINES_PER_REQUEST` (12)
    and so cut only by a cap below that — under the modes that group by score, and by `as_sent`.
    "none" asks one line a request, and an authored "manual" group is asked as its author wrote it.
    """
    cap = max(1, int(cap))
    out: list[RequestPlan] = []
    for plan in plans:
        n = len(plan.keys)
        if n <= cap:
            out.append(plan)
            continue
        parts = -(-n // cap)
        small, extra = divmod(n, parts)
        start = 0
        for number in range(1, parts + 1):
            size = small + (1 if number <= extra else 0)
            keys = tuple(plan.keys[start:start + size])
            start += size
            out.append(replace(plan, name=_part_name(plan, keys, number), keys=keys))
    return out


def focus_keys(named, line_item_set) -> set[str]:
    """The line items a focus list names: each key as written, and for a key ending in "*" every
    asked-about line of this set whose key starts with what precedes it (`bs_icon__*`). A pattern
    only ever adds keys of this set, so a list serving every shipped set focuses each run on its
    own. Read by the stage and by the scripts that mirror it."""
    named = [str(k) for k in (named or ())]
    prefixes = tuple(k[:-1] for k in named if k.endswith("*") and len(k) > 1)
    keys = {k for k in named if not k.endswith("*")}
    keys |= {i.key for i in (getattr(line_item_set, "items", None) or ())
             if prefixes and i.key.startswith(prefixes) and asked_about(i)}
    return keys


def focused(plans: list[RequestPlan], focus) -> list[RequestPlan]:
    """Each plan kept with its focus lines only, and with the notes and sections it was planned
    with; a plan with none is not asked. A kept plan is then cut by `capped` like any other, so a
    group with more focus lines than `llm_lines_per_request` still goes in more than one request.

    So a focus run answers exactly the focus lines and every other line keeps what the rules gave
    it. It used to keep a group WHOLE when one of its lines was in focus — no difference at one line
    a request, and, with grouping shipped, a focus line's whole section asked and answered.
    """
    out: list[RequestPlan] = []
    for plan in plans:
        mine = tuple(k for k in plan.keys if k in focus)
        if mine:
            out.append(plan if mine == tuple(plan.keys) else relabel(plan, mine))
    return out


def relabel(plan: RequestPlan, keys) -> RequestPlan:
    """`plan` asked with `keys` (some of its lines, in its order), named after the lines it holds.

    THE RUN LOG NAMES A REQUEST BY ITS PLAN — `request(<name>) FAILED`, `unanswered(<name>)`,
    `usage request=<name>` — and a failed request's name is the only record of which lines it lost
    to the rules. A plan named after its first line (`bs_cl__cpltd_bank+6`) keeps that name only
    while it holds that line; one named after its section keeps its section and counts its lines
    again; an authored name is the author's and is kept.
    """
    return replace(plan, keys=tuple(keys), name=_label(plan, tuple(keys)))


def _label(plan: RequestPlan, keys: tuple[str, ...]) -> str:
    old = tuple(plan.keys)
    if not keys:
        return plan.name
    if old and plan.name in (old[0], f"{old[0]}+{len(old) - 1}"):
        return keys[0] if len(keys) == 1 else f"{keys[0]}+{len(keys) - 1}"
    head, plus, count = plan.name.rpartition("+")
    if plus and count.isdigit() and "/" in head:
        return keys[0] if len(keys) == 1 else f"{head}+{len(keys) - 1}"
    return plan.name


def _part_name(plan: RequestPlan, keys: tuple[str, ...], number: int) -> str:
    """A part of a group cut by `capped`: named after its own lines where its group was named after
    its first line, else the group's name without its line count, numbered — `balance_sheet/bs_cl#2`.
    """
    own = _label(plan, keys)
    if own != plan.name and "/" not in own:
        return own
    head, plus, count = plan.name.rpartition("+")
    return f"{head if plus and count.isdigit() else plan.name}#{number}"


#: At most this many `anywhere` lines share one report request. A request carrying the whole
#: report is the one whose shared context is largest, so its lines are batched, and the cap is the
#: largest group measured to answer well together (the twelve depreciation parts of one note).
REPORT_LINES_PER_REQUEST = 12


def report_plans(plans, by_key: dict) -> tuple[list[RequestPlan], list[RequestPlan]]:
    """``(plans, report plans)``: every `anywhere` line moved out of the plan it was in, into
    batches that are asked with the whole annual report (`line_item_llm.build_report`).

    A LINE THAT MAY BE READ FROM ANYWHERE IS GIVEN EVERYTHING, so the per-line selection that
    planned its request — its note set, its statement — no longer decides what it is sent, and
    nothing is left to keep it in a request of its own. Its `notes` still travel, as the line's
    `notes_supplied` ("look there first"). The plan's other lines stay as they were planned, the
    way `split_text_plans` leaves a plan's figure lines.

    BATCHED BY WHERE THE LINE IS PRINTED: its statement, then its section, in the set's own order,
    at most `REPORT_LINES_PER_REQUEST` to a batch and the batches of a statement even — so the
    lines of one statement are asked together, and a balance-sheet total is asked beside the
    balance-sheet lines rather than beside a covenant. A line names its statement on the line
    (`LineItemDef.statement`); one naming none is batched under its section.

    A NARRATIVE LINE IS BATCHED APART (`text_lines.is_prose_line`, `report:<place>:prose`): it is
    asked under a contract and a reply schema of its own, so it shares no batch with the phrases
    printed beside it, and adding one leaves every phrase batch as it was.
    """
    from app.services.text_lines import is_prose_line

    def anywhere(key: str) -> bool:
        return key in by_key and line_item_routes.reads_every_page(by_key[key])

    kept: list[RequestPlan] = []
    moved: list[str] = []
    notes_of: dict[str, tuple[str, ...]] = {}
    for plan in plans:
        mine = tuple(k for k in plan.keys if anywhere(k))
        rest = tuple(k for k in plan.keys if k not in mine)
        if rest:
            kept.append(plan if not mine else relabel(plan, rest))
        for key in mine:
            if key not in notes_of:
                moved.append(key)
                notes_of[key] = tuple(plan.notes)
    if not moved:
        return kept, []

    def tok(x) -> str:
        return str(getattr(x, "value", x) or "")

    def where(key: str) -> str:
        item = by_key[key]
        return tok(getattr(item, "statement", None)) or str(getattr(item, "inherits", "") or "")

    position = {key: i for i, key in enumerate(moved)}
    by_place: dict[tuple[str, bool], list[str]] = {}
    for key in sorted(moved, key=lambda k: (where(k), str(getattr(by_key[k], "inherits", "") or ""),
                                            position[k])):
        by_place.setdefault((where(key), is_prose_line(by_key[key])), []).append(key)
    report: list[RequestPlan] = []
    for place, prose in sorted(by_place, key=lambda p: min(position[k] for k in by_place[p])):
        keys = by_place[(place, prose)]
        batches = -(-len(keys) // REPORT_LINES_PER_REQUEST)
        size = -(-len(keys) // batches)
        for n in range(batches):
            chunk = tuple(keys[n * size:(n + 1) * size])
            if not chunk:
                continue
            notes: list[str] = []
            for key in chunk:
                notes += [num for num in notes_of[key] if num not in notes]
            report.append(RequestPlan(
                name=(f"report:{place or 'unplaced'}" + (":prose" if prose else "")
                      + (f"#{n + 1}" if batches > 1 else "")),
                keys=chunk, notes=tuple(notes), sections=_sections_of_keys(chunk, by_key)))
    return kept, report


def split_text_plans(plans, by_key: dict) -> tuple[list[RequestPlan], list[RequestPlan]]:
    """``(figure plans, text plans)``: every text line (`text_lines.is_text_line`) moved out of the
    plan it was in, into a text plan of that plan's name with ``:text`` after it, the plan's figure
    lines left as planned (named after the lines they hold, `relabel`).

    AND EACH TEXT PLAN BY KIND: its phrase lines and its narrative lines (`text_lines.is_prose_line`)
    in plans of their own (the narrative half named ``<plan>:prose``), because the two are asked
    under different contracts with different reply schemas (`stages.line_item_llm._ask_text`). Split
    here, before the cap cuts: a mixed text group cut first went out cut by lines that are never
    sent together — on the US GAAP set at a cap of 12, 11 phrase lines and 2 narrative ones in three
    requests where two hold them.
    """
    from app.services import text_lines

    figures: list[RequestPlan] = []
    texts: list[RequestPlan] = []
    for plan in plans:
        text = tuple(k for k in plan.keys if k in by_key and text_lines.is_text_line(by_key[k]))
        rest = tuple(k for k in plan.keys if k not in text)
        if rest:
            figures.append(plan if not text else relabel(plan, rest))
        if not text:
            continue
        whole = replace(plan, name=f"{plan.name}:text", keys=text, notes=(), sections=())
        prose = tuple(k for k in text if text_lines.is_prose_line(by_key[k]))
        phrase = tuple(k for k in text if k not in prose)
        if phrase:
            texts.append(whole if not prose else replace(whole, keys=phrase))
        if prose:
            texts.append(whole if not phrase else replace(whole, name=f"{whole.name}:prose",
                                                           keys=prose))
    return figures, texts


@dataclass(frozen=True)
class SentPlans:
    """The requests a run makes, as plans: see `as_sent`."""

    figures: list[RequestPlan]          # figure requests with what their lines selected
    texts: list[RequestPlan]            # text-line requests with the pages that print their lines
    report_figures: list[RequestPlan]   # `anywhere` lines asked with the whole annual report
    report_texts: list[RequestPlan]     # …and its text lines


def as_sent(plans: list[RequestPlan], by_key: dict, settings, *, focus=()) -> SentPlans:
    """The plans `plan_requests` made, taken through the line-item stage's own steps to the requests
    a run sends — for the scripts that price, sweep or grade them, so that what they count is what a
    run makes rather than the groups it starts from.

    THE STAGE'S STEPS, IN ITS ORDER (`stages.line_item_llm`): the focus list (`focused`, the "*"
    patterns already expanded by `focus_keys`); the text lines out of the figure requests and by
    kind (`split_text_plans`); unless `llm_document_context` is "selected", the `anywhere` lines
    into the report batches (`report_plans`), and under "full" every text line with the report; then
    the cap (`capped`, under the modes that group by score), on all four.

    NOT MODELLED, because each needs the document: the note-tag gate (which face rows print a note
    tag), a report too large to send (the stage then plans every line with the selected context),
    and a request with nothing to read (the stage names it and does not send it).
    `tests/test_requests_share_their_context.py` holds this to what the stage sends.
    """
    mode = str(getattr(settings.extraction, "llm_request_grouping", "none") or "none")
    context = str(getattr(settings.extraction, "llm_document_context", "anywhere") or "anywhere")
    cap = lines_per_request(settings)

    def cut(found):
        return capped(found, cap) if mode in ("identical", "similar") else found

    if focus:
        plans = focused(plans, focus)
    figures, texts = split_text_plans(plans, by_key)
    report_figures: list[RequestPlan] = []
    report_texts: list[RequestPlan] = []
    if context != "selected":
        figures, report_figures = report_plans(figures, by_key)
        texts, report_texts = report_plans(texts, by_key)
        if context == "full":
            report_texts, texts = [*texts, *report_texts], []
    return SentPlans(figures=cut(figures), texts=cut(texts), report_figures=cut(report_figures),
                     report_texts=cut(report_texts))


def face_statements(plan, by_key: dict) -> set[str]:
    """The statement tokens whose PRINTED ROWS this plan may be supplied, given its lines' routes.

    WHAT IT REPLACES: `{st for st, _sec in plan.sections}`, read straight off the plan. That set
    describes where the plan's lines are GATED, which is not the same question as where they may
    be READ FROM — a line declared `note_tables` is gated to a statement and forbidden the face,
    and the old set handed its request the statement's rows anyway.

    THE FILTER IS PER KEY, NOT PER PLAN, because a plan can mix. The note-set grouping puts lines
    together by the notes they select and knows nothing about their routes, so one request can
    carry a face line and a note-only line; supplying the block on the strength of the face line is
    right, and it is the note-only line's CITATION that is then refused
    (`services.note_sourced.resolve_sources`, `allow_face=False`). What this stops is the other
    case — a plan whose lines are ALL note-only being sent a statement it must not read.

    EMPTY MEANS NO BLOCK. `services.face_context.face_rows` returns nothing for an empty set, which
    is the behaviour a run had before the block existed.
    """
    eligible = [by_key[k] for k in (getattr(plan, "keys", None) or ())
                if k in by_key and line_item_routes.may_read_face(by_key[k])]
    if not eligible:
        return set()
    declared = {st for st, _sec in (getattr(plan, "sections", None) or ()) if st}
    if not declared:
        return set()
    own = {st for item in eligible for st, _sec in _sections_of_item(item) if st}
    return declared & own


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


def _sections_of_keys(keys, by_key: dict) -> tuple[tuple[str, str], ...]:
    """Every `(statement, section)` pair the plan's lines may be claimed under, in key order."""
    out: list[tuple[str, str]] = []
    for key in keys:
        for pair in (_sections_of_item(by_key[key]) if key in by_key else ()):
            if pair not in out:
                out.append(pair)
    return tuple(out)


def _by_section(keys, by_key: dict) -> dict[tuple[tuple[str, str], ...], list[str]]:
    """Group keys by the `(statement, section)` pairs they are claimable under — ALL of them.

    A LINE IS GROUPED WITH THE LINES CLAIMABLE UNDER EXACTLY ITS PAIRS. A line scoped to two
    sections belongs in neither one-section group's context alone, and putting it in the first
    would supply it half of what its own gate allows — a request that cannot answer the question
    it asks. Lines that share the same SEVERAL pairs need the same context, and are grouped on it
    as one-pair lines are on theirs.

    AND THE GROUP KEEPS ITS PAIRS, which it did not. Each of these lines was asked in a request of
    its own whose only "pair" was `("", key)` — no statement — so `face_statements` built it no
    statement block, and the line was asked where its figure is printed with no statement in the
    request. Measured on Chemplast Sanmar 2024-25 with the Ind AS set, whose P&L subtotals are each
    claimable under every P&L section: finance costs, depreciation, EBITDA, profit before
    exceptional items, share of associates, exceptional items, profit before tax, profit for the
    year and total comprehensive income — nine requests of ~550 characters, every one without a
    row of the statement; all five filings' logs said "answered but nothing located" for profit
    for the year.
    """
    out: dict[tuple[tuple[str, str], ...], list[str]] = {}
    for key in keys:
        pairs = _sections_of_item(by_key[key])
        if not pairs:
            # NOT A GROUP OF ITS OWN. Measured, 39 of the shipped set's asked-about lines name
            # neither a statement nor a section, and bundling them together was the first thing
            # this function did: one request for 39 lines that share NO context, which saves
            # nothing (there is no block to amortise) and buys the whole cross-influence hazard
            # grouping carries. They are asked about one at a time.
            out[(("", key),)] = [key]
            continue
        out.setdefault(pairs, []).append(key)
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
                                 keys=keys, notes=tuple(merged),
                                 sections=_sections_of_keys(keys, by_key)))
        claimed.update(keys)
    for key in by_key:
        if key not in claimed:
            plans.append(RequestPlan(name=key, keys=(key,), notes=notes_of.get(key, ()),
                                     sections=_sections_of_item(by_key[key])))
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
