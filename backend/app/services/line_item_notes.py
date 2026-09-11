"""Which notes a LINE ITEM is in, decided by meaning against the note HEADERS.

THE UNIT OF WORK IS THE LINE ITEM. That is the whole point of this module, and it is what separates
it from `note_context.ContextPool.select`, which answers a different question with the same
arithmetic. `select` asks "which notes help explain THIS PRINTED CAPTION?", so its probe is built
from a row and its answer belongs to a row. A template is filled line item by line item, and a
line item exists whether or not the filing prints a caption resembling it — so the question that
fills a template is "given what this line MEANS, which of this filing's notes is it in?".

WHY HEADERS AND NOT WHOLE NOTES. A note's header is its author's own statement of subject, in six
or seven words. Scoring against the header plus every row caption lets a long note outscore a short
one on breadth rather than on subject: a forty-row note mentions more things, so it shares more
tokens with any probe. Measured on laisun, the widest note carries 7,538 characters against a
median of 2,547 — a threefold advantage that has nothing to do with what the note is about. Headers
are comparable in length to each other, so a threshold over them means the same thing for every
note.

THIS DOES NOT REPLACE THE AUTHORED REGEXES. That was the hope when this module was written and the
measurement refuted it. `scripts/calibrate_line_item_notes.py` scores every line item's probe
against the notes its own `note_source.note_title_any` patterns match — patterns that demonstrably
work, since they produced every figure in the focus runs — and the result on the two reference
filings is:

    laisun (English)     41 authored (line item, note) pairs   80.5% within the top 4, 92.7% top 10
    suncreate (Chinese)  120 pairs                             0% at ANY rank, every score 0.000

TWO SEPARATE FAILURES, and neither is a threshold that needs tuning.

  1. IT IS BLIND TO HAN SCRIPT, structurally. `subject_tokens` is `[a-z]{3,}`, so 固定资产折旧
     yields NO tokens at all and a Chinese heading scores 0.000 against every probe — including a
     Chinese one. suncreate's 0 of 120 is by construction, not by weak overlap, which is why no
     `min_score` recovers it. Authoring Chinese descriptions would not help until the tokeniser
     emits something for Han runs (character bigrams are the usual answer); the 473 zh aliases the
     configuration already carries are the only Chinese text available today, and this probe
     deliberately excludes aliases.
  2. A HEADER NAMES THE ASSET; A LINE ITEM NAMES THE CHARGE. `sub__ppe_depreciation` belongs in
     note 14, "PROPERTY, PLANT AND EQUIPMENT", and the only token they share is "and" — which has
     near-zero IDF by design. Measured rank: 77 of 190, score 0.013. That is not a vocabulary gap a
     better threshold closes: depreciation being disclosed inside the fixed-asset schedule is
     domain knowledge, and no bag-of-words similarity holds it. It is also exactly the reading the
     authored patterns were written for.

WHAT IT IS GOOD FOR, on the evidence. An in-language SUPPLEMENT: a phrasing no pattern anticipated
still scores, so it degrades to a ranked guess where a regex degrades to silence. At `min_score`
0.30 on laisun it keeps 37 of 41 authored notes at 3.7 notes per line item, which is a usable
request. It is not a replacement, and `identified_notes` stays the primary selector.

IT ALSO DOES NOT TOUCH THE ROW-LEVEL PATTERNS. `row_caption_any` decides which rows INSIDE a chosen
note count, and `row_caption_none` vetoes rows that must not — a different job from finding the
note, and one no header score can do.

THE THRESHOLD IS NOT INHERITED. `select`'s 0.22 was calibrated for a row-caption probe against
note-plus-rows units; both sides differ here.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.note_context import ContextPool, ContextUnit, subject_tokens


@dataclass(frozen=True)
class NoteHit:
    """One note a line item scored against, and how well."""

    note: str
    title: str
    score: float


def header_pool(notes) -> ContextPool:
    """One unit per note, carrying its HEADER only.

    IDF is computed over this pool, so a word common to many of THIS filing's headings — "assets",
    "total", "group" — carries almost no weight, and one that appears in a single heading carries
    the most. That is the same mechanism `ContextPool` uses for rows, and it is why no stopword list
    is needed: the weight reaches exactly zero for a token in every unit.
    """
    units = []
    for table in notes or ():
        title = (getattr(table, "title", "") or "").strip()
        number = str(getattr(table, "note_number", "") or "").strip()
        if not title and not number:
            continue
        # `captions=()` is the point: the unit's text is the heading and nothing else.
        units.append(ContextUnit(kind="note", ref=number or title, title=title, captions=()))
    return ContextPool(units)


def note_probe(item, parent=None) -> str:
    """LEVEL 1 — what the NOTE this line sits inside is about. Scored against note HEADERS.

    `note_source.note_terms` FIRST AND ALONE WHERE IT EXISTS, because it is the only text authored
    for this job. A heading names the CONTAINER ("administrative expenses", 管理费用) and everything
    else written about a line names its CONTENT ("depreciation of fixed assets") — measured, a
    blended probe scored 0.000 against the very heading its line belongs to, because every one of
    its tokens was a content word.

    The line's own prose is the FALLBACK, for a line nobody has given note terms. That is a guess
    and it is meant to look like one: it is the same blend that measured 40% on a PRC filing.
    """
    source = getattr(item, "note_source", None)
    authored = list(getattr(source, "note_terms", None) or ())
    if authored:
        return " ".join(authored)
    return _blended(item, parent)


def row_probe(item) -> str:
    """LEVEL 2 — what the ROW inside that note is called. Scored against ROW CAPTIONS.

    A separate vocabulary from level 1 and a separate pool, because what makes a word distinctive
    among a filing's headings is not what makes it distinctive among the rows of one note. Sharing
    either would let heading vocabulary drown out row vocabulary.
    """
    source = getattr(item, "note_source", None)
    return " ".join(getattr(source, "row_terms", None) or ())


def row_veto(item) -> str:
    """Captions that must NOT count, however well they score — the semantic `row_caption_none`."""
    source = getattr(item, "note_source", None)
    return " ".join(getattr(source, "row_terms_none", None) or ())


def _blended(item, parent=None) -> str:
    """Everything written about a line, in every locale — the fallback probe.

    Aliases are IN: the probe is turned into a set before scoring, so a token appearing in nine
    aliases counts once and repetition cannot inflate anything. Without them some lines carry no
    Han text at all, and a selector that silently returns nothing for those is the failure this
    module exists to avoid. NOT the `note_source` patterns, which are what the calibration measures
    against — feeding them back would make the measurement circular.
    """
    parts = [
        (getattr(item, "label", "") or ""),
        (getattr(item, "description", "") or ""),
        (getattr(item, "definition", "") or ""),
    ]
    parts.extend(str(x) for x in (getattr(item, "include", None) or ()))
    parts.extend(str(a) for a in (getattr(item, "aliases", None) or ()))
    by_locale = getattr(item, "aliases_i18n", None) or {}
    if hasattr(by_locale, "items"):
        for _locale, values in by_locale.items():
            parts.extend(str(a) for a in (values or ()))
    # A LINE'S PROBE IS BUILT FROM ITS OWN PROSE AND NOTHING ELSE.
    #
    # This used to append `_blended(parent)` — the parent's label, description, definition, include
    # criteria and every locale's aliases — to a child's probe, on the reasoning that a part with
    # thin prose could borrow its whole's. That is inference, and it is the wrong kind: it makes a
    # part score against notes its PARENT is about rather than notes IT is about, and the twelve
    # depreciation parts differ from one another only in the container each sits in, which is
    # precisely the distinction the parent's text cannot carry and drowns out.
    #
    # A part that scores nothing now is telling the truth: nothing written about it names the note
    # it lives in. The fix for that is `note_terms` on the part — the field authored for this job,
    # which `note_probe` prefers over this blend entirely — not a borrowed identity.
    #
    # `parent` is kept in the signature and ignored, so the several callers that thread it do not
    # all have to change in the same commit as the behaviour.
    _ = parent
    return " ".join(p for p in parts if p.strip())


def notes_for_line_item(item, pool: ContextPool, *, min_score: float = 0.30,
                        cap: int = 4, parent=None) -> list[NoteHit]:
    """The notes this line item is in, best first.

    `cap` bounds the request rather than the judgement: a line item genuinely spread over more
    notes than this is a configuration fact worth seeing, so `calibrate_line_item_notes.py` reports
    the rank of every authored note and not merely whether it survived the cap.
    """
    probe = set(subject_tokens(note_probe(item, parent)))
    if not probe:
        return []

    scored = []
    for index, unit in enumerate(pool.units):
        score = pool._score(unit, probe)
        if score >= min_score:
            scored.append((score, index, unit))
    # Descending score, document order breaking ties, so a rerun of the same filing selects the
    # same notes in the same order.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [NoteHit(note=u.ref, title=u.title, score=round(s, 4))
            for s, _i, u in scored[:cap]]


def note_sets(items, notes, *, min_score: float = 0.30, cap: int = 4) -> dict[str, list[NoteHit]]:
    """Every `extract` line item's note set, keyed by line-item key.

    THE INPUT TO BATCHING. Two line items may share one request only when they need the same notes,
    and this is where "the same notes" acquires a meaning — the note context is what a request pays
    for, so grouping on anything else would not save anything.

    Only `extract` lines are selected for: a derived or derivable line takes its figure from
    declared arithmetic and is never asked about (`_llm_withheld` in `services.mapping`), so
    building it a note set would cost a scoring pass to produce a set nothing reads.
    """
    pool = header_pool(notes)
    by_key = {i.key: i for i in items}
    out: dict[str, list[NoteHit]] = {}
    for item in items:
        if str(getattr(item, "extraction_mode", "extract")) != "extract":
            continue
        parent = by_key.get(getattr(item, "parent", "") or "")
        hits = notes_for_line_item(item, pool, min_score=min_score, cap=cap, parent=parent)
        if hits:
            out[item.key] = hits
    return out


def caption_agrees_with_row_terms(item, caption: str) -> tuple[bool, str]:
    """Does this caption look like the ROW this line item is, at all? `(ok, why_not)`.

    LEVEL 2 AS A CHECK RATHER THAN A SEARCH, and the cheapest useful form of it: the caption must
    share at least ONE subject token with `row_terms`. Not a cosine and not a threshold, because
    there is nothing here to calibrate — the question is whether the two texts are about the same
    thing at all, and zero shared tokens answers it.

    WHAT IT CATCHES, measured on a live run. The model mapped the face row "Other operating
    expenses" (1,026,959, a real income-statement total) to `sub__operating_expense_depreciation`,
    whose meaning is the depreciation CHARGED TO those expenses. It matched on the container's name.
    That caption shares no token with the line's row terms — every one of which is a depreciation
    phrase — so this refuses it. Only a cascade guard (`refuse_negative`) stopped that figure
    reaching the output, and that guard held only because the wrong number happened to be negative.

    WHY ONE TOKEN IS ENOUGH, and why it does not refuse legitimate phrasings. Measured against the
    same line's terms: "Depreciation charge for the year" shares `depreciation`; 折旧及摊销 shares
    折旧 and 摊销; "Depreciation of right-of-use assets" shares four. All 77 parts carry Han terms as
    well as English, so a Chinese caption is not refused for being Chinese — which was the failure
    mode to avoid, since a false refusal here loses a figure silently.

    A LINE WITH NO ROW TERMS IS NOT JUDGED. Absent configuration is not a negative finding, and
    refusing on it would make the check punish the lines nobody has authored yet.
    """
    source = getattr(item, "note_source", None)
    terms = list(getattr(source, "row_terms", None) or ())
    if not terms:
        return True, ""
    wanted = {t for term in terms for t in subject_tokens(str(term))}
    if not wanted:
        return True, ""
    got = set(subject_tokens(caption or ""))
    if got & wanted:
        return True, ""
    return False, (
        f"the caption shares no subject word with this line's row terms, so it names something "
        f"else — most often the note or expense TOTAL this line is a component of")


def group_by_note_set(sets: dict[str, list[NoteHit]], *, mode: str = "none",
                      similarity: float = 0.8) -> list[list[str]]:
    """Line items grouped into requests by the notes they need. One list per request.

    WHAT IS BEING SAVED, and why the grouping key is the note set rather than anything else. The
    note context is what a request pays for — measured, 22,597 of a 30,407-token request, 74% of
    it — so line items needing the SAME notes amortise one copy of that block instead of paying for
    it each. Grouping on any other property would save nothing.

    THREE MODES, and the default is the expensive one on purpose:

      * "none"      — one line item per request. The baseline, and the only mode whose answer is
                      attributable to a single line: nothing else shares the call, so nothing else
                      can have influenced it.
      * "identical" — the exact same note set, so no line item ever receives a note it did not ask
                      for. That matters beyond tidiness: an unasked-for note is how a wrong answer
                      acquires a plausible-looking source.
      * "similar"   — note sets whose Jaccard index reaches `similarity`. Bigger savings, and the
                      trade is that some line items see notes they did not select.

    A LINE ITEM WHOSE SET MATCHES NOTHING GETS ITS OWN REQUEST, in every mode. The grouping degrades
    to "none" for it rather than forcing it into the nearest group, because a forced group is
    exactly the case where a line item receives evidence for a different question.

    DETERMINISTIC ORDER, so the same filing produces the same requests: groups are keyed on the
    sorted note set and emitted in sorted key order, and members are sorted within a group. A
    rerun that regrouped would make two runs incomparable for no reason.
    """
    keyed = {key: tuple(sorted(h.note for h in hits)) for key, hits in sets.items()}
    if mode == "none" or not keyed:
        return [[key] for key in sorted(keyed)]

    if mode == "identical":
        buckets: dict[tuple, list[str]] = {}
        for key, notes in keyed.items():
            buckets.setdefault(notes, []).append(key)
        return [sorted(members) for _notes, members in sorted(buckets.items())]

    if mode != "similar":
        raise ValueError(f"unknown grouping mode {mode!r}; expected none, identical or similar")

    # GREEDY, AGAINST THE GROUP'S FIRST MEMBER rather than against its running union. Comparing to
    # the union lets a group drift: each new member need only resemble what the group has already
    # accumulated, so a chain of pairwise-similar sets ends up in one request with the first and
    # last sharing almost nothing. Comparing to the seed keeps every member within `similarity` of
    # the same set.
    remaining = sorted(keyed)
    groups: list[list[str]] = []
    while remaining:
        seed = remaining.pop(0)
        want = set(keyed[seed])
        members = [seed]
        for key in list(remaining):
            have = set(keyed[key])
            union = want | have
            if union and len(want & have) / len(union) >= similarity:
                members.append(key)
                remaining.remove(key)
        groups.append(sorted(members))
    return sorted(groups)
