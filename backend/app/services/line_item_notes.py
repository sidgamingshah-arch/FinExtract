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

THIS DOES NOT REPLACE THE AUTHORED REGEXES, and it is measured rather than argued.
`scripts/calibrate_line_item_notes.py` scores every line item's probe against the notes its own
`note_source.note_title_any` patterns match — patterns that demonstrably work, since they produced
every figure in the focus runs — and reports the RANK of each authored note.

    laisun (English)     42 authored (line item, note) pairs   42/42 delivered at cap 4
    suncreate (Chinese) 123 pairs                              75/123 delivered, 71.5% in the top 10

THE CHINESE NUMBER USED TO READ "0% at ANY rank, every score 0.000", AND THAT IS WORTH RECORDING
because the reason was never a threshold and was twice misdiagnosed:

  1. HAN USED TO YIELD NO TOKENS. `subject_tokens` was `[a-z]{3,}`, so 固定资产折旧 produced
     nothing and a Chinese heading scored 0.000 against every probe, including a Chinese one.
     THAT IS FIXED — it now emits Latin words and Han character BIGRAMS
     (`note_context.subject_tokens`), which was the answer this docstring used to name as future
     work. Authoring Chinese `note_terms` therefore does help, and on the one line whose terms were
     Traditional-only while its patterns carried Simplified, adding them is most of what took
     suncreate's zero-scoring pairs to none. A consequence worth knowing: Traditional and
     Simplified share no bigram unless the characters are identical, so 預付租賃款項 cannot score
     against 预付租赁款项 — BOTH spellings have to be authored, and
     `scripts/audit_script_coverage.py` is what checks that they are.

  2. A NOTE ARRIVES IN FRAGMENTS, AND THE CAP USED TO COUNT THEM. laisun's note 1 arrives as 32
     tables and suncreate's 五、1 as 28, each its own header unit, and `cap` sliced those units —
     so four hits were one note seen four times. Fixed in `notes_for_line_item`, which keeps the
     best-scoring fragment per note number BEFORE the cap. That alone took delivery from 81% to
     100% on laisun and 48% to 59% on suncreate, and it had been masking the vocabulary question
     entirely: a measurement comparing a regex hit on one fragment against a score on another
     reported 42 of 125 pairs as unreachable when 4 were.

  3. WHAT REMAINS IS REAL. A HEADER NAMES THE ASSET; A LINE ITEM NAMES THE CHARGE.
     `sub__ppe_depreciation` belongs in note 14, "PROPERTY, PLANT AND EQUIPMENT", and the only
     token they share is "and", which has near-zero IDF by design. Depreciation being disclosed
     inside the fixed-asset schedule is domain knowledge, and no bag-of-words similarity holds it.
     That is exactly the reading the authored patterns were written for, which is why the two are a
     UNION and not a replacement. 44 of suncreate's 123 authored notes still score below the 0.25
     floor, worst 0.077 — vocabulary the terms do not carry yet, and every one of them is ranked by
     the calibration script.

WHAT IT IS GOOD FOR, on the evidence. An in-language SUPPLEMENT: a phrasing no pattern anticipated
still scores, so it degrades to a ranked guess where a regex degrades to silence. `identified_notes`
stays the primary selector and passes a pattern-matched note unconditionally.

IT ALSO DOES NOT TOUCH THE ROW-LEVEL PATTERNS. `row_caption_any` decides which rows INSIDE a chosen
note count, and `row_caption_none` vetoes rows that must not — a different job from finding the
note, and one no header score can do.

THE THRESHOLD IS NOT INHERITED. `select`'s 0.22 was calibrated for a row-caption probe against
note-plus-rows units; both sides differ here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.note_context import (ContextPool, ContextUnit, matches_title,
                                       subject_tokens)


@dataclass(frozen=True)
class NoteHit:
    """One note a line item scored against, and how well.

    `via` says WHICH SEARCH FOUND IT, and it is not decoration: a note found by its heading was
    chosen on its author's own statement of subject, while one found by content was chosen because
    it contains a row this line is looking for. Those are different strengths of evidence and the
    run log, the request trail and `scripts/calibrate_line_item_notes.py` all need to tell them
    apart — a filing whose figures all arrive through widening is a filing whose header vocabulary
    needs work, and that is invisible if both look the same.
    """

    note: str
    title: str
    score: float
    via: str = "header"


# THE FLOOR BELOW WHICH A NOTE IS NOT OFFERED AT ALL. ONE constant, because it was the same literal
# 0.30 in two signatures and a reader had no way to know they were meant to move together.
#
# 0.25, DOWN FROM 0.30, and the trade is measured rather than assumed
# (`scripts/calibrate_line_item_notes.py`, laisun + suncreate):
#
#              authored notes kept                notes per line item (avg)
#              laisun (EN)      suncreate (PRC)   laisun   suncreate
#   0.30       42 / 42          68 / 125          6.4      5.6
#   0.25       42 / 42          69 / 125          7.3      7.3
#   0.35       34 / 42          63 / 125          3.0      4.4
#
# SO LOWERING IT BUYS RECALL ONLY ON THE PRC FILING, AND ONLY ONE NOTE OF 125, for ~30% more notes
# per line there and ~14% more on the English one. It is chosen anyway, for an asymmetry that the
# averages do not show: a note MISSED leaves the line empty and loses the figure, because a request
# cannot cite what it was not shown, while a note carried in error costs request size and nothing
# else. `cap=4` bounds how many survive, and the authored `note_title_any` patterns are a UNION
# with these hits rather than being replaced by them, so a wrong hit displaces nothing.
#
# RAISING IT IS THE OTHER DEFENSIBLE READING, and the numbers for it are above. Measured on the
# ten-note fixture in `tests/test_note_context_ranking.py`, true matches score 0.598 at worst and
# unrelated headings 0.308 at the 90th percentile — so a threshold in 0.31..0.59 admits no noise at
# all, and 0.35 is the cheapest point in that band. What it costs is 8 of laisun's 42 authored
# notes, which is the reason it was not taken: on that filing every one of the 42 is reachable, and
# a threshold that drops 8 of them to save request size is trading a figure for tokens.
#
# WHAT NEITHER SETTING FIXES is the 42 of 125 suncreate pairs that score EXACTLY 0.000
# (`scripts/why_zero_score.py`). Those are not near the floor, they are at zero, and 30 of them are
# a line's `note_title_any` matching a note it has nothing to do with — `sub__ppe_depreciation`'s
# pattern claiming 存货 (inventories) and ．会计处理方法 (accounting policy). The probe scoring
# 0.000 there is CORRECT; the regex is wrong. No floor recovers a bad pattern.
MIN_SCORE = 0.25

# BELOW THIS, THE HEADER SEARCH IS NOT CONFIDENT AND THE CONTENT SEARCH RUNS AS WELL.
#
# Not the same number as `MIN_SCORE` and not derivable from it. `MIN_SCORE` answers "is this note
# worth offering at all"; this answers "did the header route actually find what it was looking
# for". A line whose best heading scores 0.27 has cleared the floor and found nothing convincing —
# exactly the case where asking a different question is worth the request size.
#
# 0.40 sits between the two distributions the fixture in `tests/test_note_context_ranking.py`
# measures: the worst TRUE match scores 0.598 and unrelated headings reach 0.308 at the 90th
# percentile. So a best-score below 0.40 means the header route returned nothing it would itself
# call a true match, and above it the heading is its author's own statement of subject and is the
# better answer.
#
# RAISING IT WIDENS MORE OFTEN (more notes per request, more chances to find a figure nothing
# else reaches); LOWERING IT TRUSTS THE HEADERS FURTHER. At 0.0 the content search never runs and
# the behaviour is what it was before it existed, which is the way to turn this off.
# MEASURED, AND IT RECOVERS NOTHING AGAINST THE ONLY GROUND TRUTH THERE IS — so it ships OFF.
#
# `scripts/measure_widening.py` over the 9 CAS filings: 763 (line, authored note) pairs, 499
# delivered by the header route, 499 delivered with widening as well. ZERO recovered, for +8% notes
# per line and 153 notes admitted that no pattern names.
#
# WHY ZERO, and the reason is structural rather than a tuning failure. When a pattern names a note,
# that note's HEADING contains the pattern's words — and a line's `note_terms` are written from the
# same vocabulary, so the header route already finds it. The cases where the header route fails are
# the ones where the heading says something else entirely, and there the content search finds a
# DIFFERENT note. So widening cannot improve recall measured against the patterns; its entire
# value is the 153, and whether those are right is a judgement no script here can make.
#
# WHICH IS NOT THE SAME AS SAYING IT IS WORTHLESS. `note_context.identified_notes` already passes a
# pattern-named note UNCONDITIONALLY, so the 499 were never at risk — which means "recall against
# the patterns" was the wrong measurement for this feature, and the right one is whether any of the
# 153 holds a figure nothing else reaches. That needs a live run to answer: a request carrying a
# widened note either cites a row in it or does not.
#
# SO THE MECHANISM IS BUILT AND THE DEFAULT IS 0.0 (never widen). Set it to 0.40 to turn it on —
# that value is where the header route stops returning anything it would itself call a true match
# (the fixture in `tests/test_note_context_ranking.py` measures the worst true match at 0.598 and
# unrelated headings at 0.308 for the 90th percentile), so it is the threshold to try first.
WIDEN_BELOW = 0.0

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
        (getattr(item, "definition", "") or ""),
    ]
    # `include` IS NOT A FIELD. It was `include_criteria`, removed from the schema with the measured
    # reason recorded at `schemas/line_items.py` — 441 of 539 lines declared it and 375 of those
    # held one GENERATED sentence restating the label this probe already scores. The read survived
    # the removal, so this line contributed nothing to any probe on any of the 549 shipped items
    # while reading like a third input to the blend. Verified before deleting: the field is absent
    # from `LineItemDef.model_fields` and no item carries the attribute, so no probe changes.
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


def notes_for_line_item(item, pool: ContextPool, *, min_score: float = MIN_SCORE,
                        cap: int = 4, parent=None) -> list[NoteHit]:
    """The notes this line item is in, best first — ONE HIT PER NOTE NUMBER.

    `cap` bounds the request rather than the judgement: a line item genuinely spread over more
    notes than this is a configuration fact worth seeing, so `calibrate_line_item_notes.py` reports
    the rank of every authored note and not merely whether it survived the cap.

    THE CAP COUNTS NOTES, NOT FRAGMENTS, AND IT DID NOT USE TO. A note printed across several pages
    arrives as several tables all carrying its number — measured, laisun's note 1 arrives as 32
    fragments and 41 as 22; suncreate's 五、1 as 28 and 五、2 as 18 — and each becomes its own
    header unit. The slice ran over those units, so four hits were routinely one note seen four
    times, and `line_item_requests._note_keys` then deduplicated what was left. The line had
    already been charged four slots for one note.

    MEASURED, AND IT WAS THE BINDING CONSTRAINT — bigger than any threshold. Authored notes
    actually DELIVERED to a request, against the regexes as ground truth
    (`scripts/calibrate_line_item_notes.py` measures REACHABILITY, uncapped; this is delivery):

                            laisun (EN)      suncreate (PRC)
        cap over fragments  34 / 42  81.0%   60 / 125  48.0%
        cap over notes      42 / 42 100.0%   74 / 125  59.2%

    So the fragmentation was costing 8 of laisun's 42 authored notes and 14 of suncreate's 125 —
    lines whose figure was in a note the request never carried, which a model cannot cite and the
    deterministic route then had to find or leave empty. Distinct notes per line went 1.99 -> 2.34
    (laisun) and 1.03 -> 1.25 (suncreate) against a cap of 4.

    WHY IT HURTS THE PRC FILING TWICE. A CAS filing itemises far more (167 distinct note numbers
    against laisun's 52) with far shorter headings (median 8 characters against 32), so more of its
    units are continuation fragments of one note AND each carries less text to score on. The
    fragment that wins is then often a policy or judgement paragraph rather than the disclosure —
    which is the same failure `scripts/why_zero_score.py` reports from the other side.

    `_note_keys` still deduplicates, and that is deliberate rather than redundant: it is the one
    place the grouping modes compare note SETS, and two line items whose hits are fragments of one
    note must be seen to need the same note.
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
    # THEN one hit per note number, keeping the BEST-SCORING fragment of each — which is also the
    # one whose title is most likely to be the note's real heading rather than a continuation
    # line — and only then the cap.
    out: list[NoteHit] = []
    seen: set[str] = set()
    for score, _index, unit in scored:
        if unit.ref in seen:
            continue
        seen.add(unit.ref)
        out.append(NoteHit(note=unit.ref, title=unit.title, score=round(score, 4)))
        if len(out) == cap:
            break
    return out


def content_terms(item) -> list[str]:
    """Everything a line says its ROW is called — the vocabulary the content search matches on.

    `row_terms` and `row_caption_any` both describe the row, and they are pooled here because the
    question being asked is "does this note contain this line's row", which neither field owns on
    its own. `row_caption_any` entries are regex SOURCES, so they are used as literals only when
    they contain no metacharacter — a pattern is for matching a caption once the note is chosen,
    not for scanning a document.
    """
    import re as _re

    source = getattr(item, "note_source", None)
    if source is None:
        return []
    out: list[str] = [t for t in (getattr(source, "row_terms", None) or []) if t and t.strip()]
    for raw in (getattr(source, "row_caption_any", None) or []):
        if raw and not _re.search(r"[\\|()\[\]{}?*+^$]", raw):
            out.append(raw)
    # Longest first, so the specificity ranking below reads the most specific match it can.
    return sorted({t.strip().lower() for t in out if t.strip()}, key=len, reverse=True)


def content_vetoes(item) -> list[str]:
    """What must NOT be in a row for it to count — the same vetoes the row reader applies.

    Applied here as well as there, because a note admitted on a vetoed row is a note carried into
    the request for a row that will then be refused: the cost without the figure.
    """
    source = getattr(item, "note_source", None)
    if source is None:
        return []
    out = list(getattr(source, "row_terms_none", None) or [])
    out += [t for t in (getattr(source, "row_caption_none", None) or []) if t]
    return [t.strip().lower() for t in out if t and t.strip()]


def widen_by_content(item, notes, *, cap: int = 4) -> list[NoteHit]:
    """The notes anywhere in the document whose ROWS this line is looking for, best first.

    THE FALLBACK WHEN THE HEADING SEARCH COMES UP SHORT. It answers the other question — not "which
    heading is about this line" but "which note contains this line's row" — so it cannot fail the
    way the header route fails, because what it matches IS what the line is defined by.

    RANKED BY THE SPECIFICITY OF THE TERM THAT MATCHED, not by how many rows matched. `row_terms`
    carry both 固定资产折旧 and a bare 折旧, so counting matches would let a note mentioning
    depreciation in eight places outrank the one note captioned for it. The longest matching term
    is the closest thing to "this row IS what the line means", and the row count only breaks ties.

    Scored 0.0 and marked `via="content"`: there is no comparable similarity to report — a literal
    match is not a cosine — and reporting one would let a widened hit be compared against a header
    score as though they measured the same thing.
    """
    terms = content_terms(item)
    if not terms or not notes:
        return []
    vetoes = content_vetoes(item)

    # note number -> (longest matching term, matching rows, title)
    best: dict[str, tuple[int, int, str]] = {}
    for table in notes:
        number = str(getattr(table, "note_number", "") or "")
        if not number:
            continue
        title = (getattr(table, "title", "") or "")
        longest = hits = 0
        for row in (getattr(table, "items", None) or ()):
            caption = (getattr(row, "raw_label", "") or "").lower()
            if not caption or any(v in caption for v in vetoes):
                continue
            matched = next((t for t in terms if t in caption), None)
            if matched is None:
                continue
            hits += 1
            longest = max(longest, len(matched))
        if not hits:
            continue
        prior = best.get(number)
        if prior is None or (longest, hits) > prior[:2]:
            best[number] = (longest, hits, title)

    ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
    return [NoteHit(note=num, title=meta[2], score=0.0, via="content")
            for num, meta in ranked[:cap]]


def cited_notes(doc) -> dict[str, tuple[str, ...]]:
    """key -> the note numbers THE FILING ITSELF prints against that line's face row.

    THE FILING'S OWN STATEMENT OF WHERE THE DETAIL IS, which the line-item path did not have. Every
    note reached the model by scoring: a probe built from the line's prose against each note's
    heading, then a content widening when that came up short. Both are inferences about which note
    is about this line. A printed "Note 14" beside a face caption is not an inference — it is the
    preparer saying so, and `stages.link_notes` has been resolving those into `doc.links` all along
    for the note-to-face reconciliation.

    READ OFF THE FACE ROW, not the note. `FaceNoteLink.face_item_id` is a `LineItem` id and the key
    comes off that row's `canonical_key`, so a link survives only where the mapper placed the row —
    which is the right gate: a citation on a row nobody could place names a note for a line that
    does not exist.

    EVERY ROW CARRYING THE KEY, deduplicated by note number and in document order. A face caption
    printed on both the consolidated and the standalone statement carries two rows and usually the
    same reference; a line whose detail is split across two notes ("14, 15") carries two numbers and
    both are wanted.
    """
    if doc is None:
        return {}
    by_id = {li.id: li for li in (getattr(doc, "line_items", None) or ())}
    out: dict[str, list[str]] = {}
    for link in (getattr(doc, "links", None) or ()):
        row = by_id.get(getattr(link, "face_item_id", None))
        key = str(getattr(row, "canonical_key", "") or "") if row is not None else ""
        number = str(getattr(link, "note_number", "") or "")
        if not key or not number:
            continue
        seen = out.setdefault(key, [])
        if number not in seen:
            seen.append(number)
    return {key: tuple(values) for key, values in out.items()}


def by_meaning_only(item) -> bool:
    """Whether this line READS ROWS from notes it finds BY MEANING: `note_terms` and row rules,
    and no title regex. A line with `note_terms` and no row rules is not one — its terms steer
    which notes the model is shown and nothing else, as they always have."""
    src = getattr(item, "note_source", None)
    return (src is not None and not list(getattr(src, "note_title_any", None) or ())
            and bool(list(getattr(src, "note_terms", None) or ()))
            and bool(list(getattr(src, "row_caption_any", None) or ())))


def covered_notes(item, notes) -> tuple[str, ...]:
    """The notes whose HEADING carries every word of one of this line's `note_terms`, in order.

    SEARCH BY MEANING THAT A DETERMINISTIC READER CAN TRUST. The header score alone is too loose to
    add rows up from: measured on the five reference filings at the 0.25 floor, the current FVTPL
    terms ranked the fair-value HIERARCHY note first on 688008 (0.56, above 交易性金融资产 at 0.48)
    and admitted 计入当期损益的政府补助 and 交易性金融负债; the FVTOCI terms took kaming's
    "Other comprehensive income" note at 0.81. A model told what to sum can ignore a wrong note; a
    row sum cannot. So a note is this line's when its heading holds ALL the subject words of one
    term — any order, either script — which is meaning without phrasing: "Financial assets at fair
    value through profit or loss" and "按公允值計量且其變動計入損益的金融資產" are the same subject
    worded differently, and neither is "Financial liabilities at FVTPL".

    A note arrives in fragments, so its words are the union over every fragment under its number.
    """
    src = getattr(item, "note_source", None)
    terms = [set(subject_tokens(t)) for t in (getattr(src, "note_terms", None) or ()) if t]
    terms = [t for t in terms if t]
    if not terms:
        return ()
    words: dict[str, set[str]] = {}
    order: list[str] = []
    for table in notes or ():
        number = str(getattr(table, "note_number", "") or "")
        if not number:
            continue
        if number not in words:
            words[number] = set()
            order.append(number)
        words[number].update(subject_tokens(str(getattr(table, "title", "") or "")))
    return tuple(n for n in order if any(t <= words[n] for t in terms))


def heading_covered(item, title: str) -> bool:
    """Whether ONE TABLE's own heading carries every word of one of this line's `note_terms`.

    The note-level test above lets a line see a note; this one decides which of its TABLES it adds
    up. A note number can carry blocks that are not about it at all — 300319's 七、2 holds the
    交易性金融资产 table and also an ageing table (按账龄披露, p148) whose own note heading was not
    recognised, so a line-item sum over the note number added ageing buckets into FVTPL
    (431,478,888.81 became 510,484,980.09).
    """
    src = getattr(item, "note_source", None)
    words = set(subject_tokens(title or ""))
    return any((t := set(subject_tokens(term))) and t <= words
               for term in (getattr(src, "note_terms", None) or ()) if term)


def sums_line_items(item) -> bool:
    """Whether this part ADDS UP A NOTE'S LINE ITEMS — `row_caption_any` is the catch-all `\\S` —
    rather than reading rows it names. Only such a part is kept out of movement tables: a
    depreciation part reads exactly the movement row it names ("Depreciation charge for the
    year"), while a line-item sum over a movement table adds opening balance, additions and
    closing balance together."""
    src = getattr(item, "note_source", None)
    return list(getattr(src, "row_caption_any", None) or ()) == [r"\S"]


def readable_tables(item, notes) -> set[int]:
    """The TABLES a by-meaning line may read: every table in an unbroken run of one note number,
    where some table of that run carries a heading the line's terms cover.

    A RUN, NOT EACH TABLE'S OWN HEADING. A mainland related-party note prints its subject once —
    "6、关联方应收应付款项" — and then its tables under generic titles, "（1）应收项目" and
    "（2）应付项目"; a rule asking each table's heading would read none of them. What it has to keep
    out is a table filed under the number by mistake: 300319's 七、2 holds the 交易性金融资产 table
    (p145) and, pages and notes later, an ageing table (按账龄披露, p148) whose own note heading
    was not recognised. The first is a run the heading opens; the second is a later run of the same
    number with no heading of its own subject, and summing it added ageing buckets into FVTPL
    (431,478,888.81 became 510,484,980.09).
    """
    out: set[int] = set()
    run: list = []
    number = None

    def close():
        if run and any(heading_covered(item, str(getattr(t, "title", "") or "")) for t in run):
            out.update(id(t) for t in run)

    for table in notes or ():
        n = str(getattr(table, "note_number", "") or "")
        if n != number:
            close()
            run, number = [], n
        run.append(table)
    close()
    return out


def claimed_notes(items, notes) -> dict[str, set[str]]:
    """For every by-meaning line: the covered notes it may READ ROWS FROM, one sibling per note.

    ONE NOTE, ONE SIBLING. Parts of one parent add up, so a note two of them cover would be counted
    twice — 1966's note 26 "Financial assets at fair value through profit or loss" is covered by the
    FVTPL part and, through 公允值計量 in its Chinese heading, by the Level 3 part. It goes to the
    sibling whose header score for it is highest (0.92 against 0.39), ties to the one declared
    first. Different parents may share a note: the current and non-current lines both read a
    securities note and take different rows of it.
    """
    pool = header_pool(notes)
    covered: dict[str, tuple[str, ...]] = {}
    for item in items or ():
        if by_meaning_only(item):
            covered[item.key] = covered_notes(item, notes)
    out: dict[str, set[str]] = {key: set() for key in covered}
    by_parent: dict[str, list] = {}
    for item in items or ():
        if item.key in covered:
            by_parent.setdefault(str(getattr(item, "parent", "") or ""), []).append(item)
    for siblings in by_parent.values():
        score = {}
        for item in siblings:
            score[item.key] = {h.note: h.score for h in notes_for_line_item(
                item, pool, min_score=0.0, cap=len(notes or ()) or 1)}
        for number in {n for item in siblings for n in covered[item.key]}:
            wanting = [i for i in siblings if number in covered[i.key]]
            best = max(wanting, key=lambda i: score[i.key].get(number, 0.0))
            out[best.key].add(number)
    return out


def declared_notes(item, notes) -> tuple[str, ...]:
    """The notes of THIS filing that the line's own `note_title_any` names, in printed order —
    or, for a line that finds its notes by meaning alone, the notes its `note_terms` cover.

    ONE PREDICATE, because three places ask this question and each used to answer it in its own
    code. `note_context.identified_notes` decides on it which note's TEXT is attached to a request;
    `note_sets` (below) decides on it which notes a line's request NAMES; and
    `scripts/note_context_to_llm.py` reports the gap between those two. A report that can disagree
    with the code it reports on is the thing being removed here — not a disagreement that was
    measured. `note_context_to_llm` searched the raw heading rather than going through
    `matches_title`, which on this corpus claims exactly the same 421 (line, note) pairs; the
    enumerator forms that separate the two (see `_LEAD`) do not occur in any DECLARED note's
    heading on these five filings. So this is a deduplication, and the report's figures before and
    after it are the same figures.

    THE NUMBER IS MATCHED TOO, and not as a convenience: patterns written to key off an enumerated
    note ("^7$") are how several declarations name a disclosure whose heading the filing does not
    print in a form any pattern could anchor. Both halves mirror `identified_notes` exactly.
    """
    if by_meaning_only(item):
        return covered_notes(item, notes)
    src = getattr(item, "note_source", None)
    raw = list(getattr(src, "note_title_any", None) or ()) if src is not None else []
    if not raw:
        return ()
    pats = []
    for pattern in raw:
        try:
            pats.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            # UNREACHABLE THROUGH A LOADED SET — `schemas.line_items.NoteSource` refuses a pattern
            # that will not compile, which `tests/test_a_declared_note_reaches_its_own_line.py`
            # asserts rather than asserting this branch. Kept for symmetry with
            # `note_context.identified_notes`, which compiles the same field the same way.
            continue
    if not pats:
        return ()
    out: list[str] = []
    for table in notes or ():
        number = str(getattr(table, "note_number", "") or "")
        if not number or number in out:
            continue
        title = str(getattr(table, "title", "") or "")
        if any(matches_title(p, title) or p.search(number) for p in pats):
            out.append(number)
    return tuple(out)


def note_sets(items, notes, *, min_score: float = MIN_SCORE,
              cap: int = 4, cited: dict[str, tuple[str, ...]] | None = None
              ) -> dict[str, list[NoteHit]]:
    """Every `extract` line item's note set, keyed by line-item key.

    THE INPUT TO BATCHING. Two line items may share one request only when they need the same notes,
    and this is where "the same notes" acquires a meaning — the note context is what a request pays
    for, so grouping on anything else would not save anything.

    Only `extract` lines are selected for: a derived or derivable line takes its figure from
    declared arithmetic and is never asked about (`_llm_withheld` in `services.mapping`), so
    building it a note set would cost a scoring pass to produce a set nothing reads.

    `cited` PUTS THE FILING'S OWN REFERENCE FIRST. See `cited_notes`: a printed "Note 14" beside a
    face caption is the preparer's statement of where the detail is, and it outranks any probe
    because it is not an inference. Passing nothing keeps the pure-scoring behaviour, which is what
    every caller that has no document does (the audit scripts, the calibration sweep, the tests
    that hand in bare items).
    """
    pool = header_pool(notes)
    titles = {str(getattr(t, "note_number", "") or ""): str(getattr(t, "title", "") or "")
              for t in (notes or ())}
    by_key = {i.key: i for i in items}
    out: dict[str, list[NoteHit]] = {}
    for item in items:
        # THE BOUNDARY MOVED, and reading the mode is now wrong in BOTH directions.
        # `extract_or_derive` means the subtotal is printed on some filings and arithmetic on
        # others, and those ARE asked about; a DERIVED PARENT declares `extract` on eight of the
        # nine shipped items and is never asked about. So the mode built note sets for lines no
        # request can answer and skipped lines that now have one.
        # `line_item_requests.asked_about` is the one place that boundary is spelled.
        from app.services.line_item_requests import asked_about
        if not asked_about(item):
            continue
        # A DECLARED `face` LINE IS NEVER GIVEN NOTES, which is the other direction of the fence
        # `services.line_item_routes` already draws and the one nothing was enforcing.
        #
        # `may_read_face` is consulted before the statement block is built
        # (`line_item_requests.face_statements`) and before a citation naming a statement is
        # accepted, so a note-only line cannot be handed the face. `may_read_notes` had no reader
        # on this side: a `route: face` line reached here, `note_probe` fell back to `_blended` —
        # its label and aliases scored against note HEADINGS — and something always scores
        # something, so the line was issued a note set it is forbidden to answer from.
        #
        # AND IT COST THE LINE ITS STATEMENT, which is the part that makes this a context defect
        # rather than waste. Having a note set puts the line in the note-grouping branch of
        # `line_item_requests.plan_requests`, and that branch passes no `sections`; the `unplanned`
        # tail below it — the one whose comment says "THIS IS WHERE EVERY FACE LINE LANDS" — is
        # what attaches them. So `face_statements` found nothing declared and built no statement
        # block. Measured over the twelve reference filings on the twelve face-routed related-party
        # lines: on 2025032802704 and 2024 Annual Report every one of them received 1-4 notes,
        # ~10.5k characters of context it may not cite, and ZERO face rows; on 1223214527, where
        # their probes happened to score nothing, the same lines correctly received
        # `['balance_sheet']`. Whether a face line was shown its own statement turned on whether an
        # unrelated note heading happened to share a token with its label.
        #
        # SILENCE STILL KEEPS THE NOTE ROUTE — `may_read_notes` is false only for a DECLARED
        # `face`, never for a line that declares nothing — so a set authored before `route` existed
        # selects exactly the notes it did before. `anywhere` keeps notes too: it is the route that
        # refuses nothing.
        # Imported HERE rather than at module scope, for the reason `asked_about` is: importing
        # `line_item_routes` at the top drags `line_item_requests` in behind it and that module
        # imports this one.
        from app.services import line_item_routes
        if not line_item_routes.may_read_notes(item):
            continue
        parent = by_key.get(getattr(item, "parent", "") or "")
        # THE PRINTED REFERENCE, AHEAD OF EVERY PROBE. Only notes this document actually has — a
        # reference to a note the pruner dropped or the parser never built names nothing, and
        # passing it on would put a number in the request with no text under it.
        #
        # `via="cited"` rather than folding it into "header", because the run log and the request
        # trail have to be able to say a note arrived on the filing's own authority: a figure
        # located in a cited note and one located in a note that merely scored well are different
        # provenance, and a reviewer cannot see the difference in the number.
        # `note_selection: any` DECLINES THE PRIORITY. The trade is real — a citation displaces
        # the lowest-scoring note at the cap — so a line whose printed reference is known to point
        # at the wrong disclosure can say so, per line, and keep pure scoring.
        wants_citation = str(getattr(item, "note_selection", "cited_first") or "") != "any"
        first = [NoteHit(note=num, title=titles.get(num, ""), score=1.0, via="cited")
                 for num in ((cited or {}).get(item.key, ()) if wants_citation else ())
                 if num in titles][:cap]
        # THE LINE'S OWN DECLARATION, AHEAD OF THE CITATION AND AHEAD OF EVERY PROBE.
        #
        # THE ASYMMETRY THIS CLOSES, and it is the codebase's own reasoning applied on the side
        # that had not had it. `note_context.identified_notes` attaches a pattern-claimed note's
        # TEXT UNCONDITIONALLY and exempts it from `_SEMANTIC_NOTE_BUDGET`, because "an author
        # declared it, which is a stronger statement than any score" — and it ranks the filing's
        # printed citation BELOW that, inside the budget. This function did the reverse: the
        # citation took the one priority slot and ~650 authored `note_title_any` patterns took
        # none, reaching a line's own set only where their vocabulary happened to fall out of
        # `note_probe`. `line_item_llm.build_request` then INTERSECTS the two, so a note admitted
        # on the declaration's authority could be absent from the request of the line that
        # declared it. That is not a ranking preference, it is the two halves of one decision
        # disagreeing.
        #
        # MEASURED, with `scripts/note_context_to_llm.py` over the five reference filings: 170 of
        # 421 (line, declared note) pairs reached the model before this, and 21 lines were sent
        # notes while the note they declare — present in that filing, admitted to
        # `identified_notes` — was held out. Every one of the 21 is a depreciation part: kaming
        # hands the five of them note 9 and note 14 while all five declare note 15.
        #
        # IT IS EXEMPT FROM `cap` RATHER THAN COMPETING FOR IT, and that is not generosity, it is
        # the measured correction to the first version of this. Promoting the declaration INTO the
        # cap displaced 6 scored notes on the reference filing, and one of the 6 was the whole
        # defect: `sub__ga_depreciation` declares the ASSET notes — 投资性房地产, 固定资产, 在建工程,
        # where depreciation is charged FROM — and those three plus the pooled 七、1 filled all
        # four slots and evicted 七、64 管理费用, scored 1.000, the note that actually PRINTS the
        # G&A depreciation line. A declaration naming where a charge originates is not a
        # declaration that the functional split is not printed somewhere else, so the two belong
        # in one set and neither may push the other out.
        #
        # THE COST IS BOUNDED BY WHAT IS ALREADY ATTACHED. `note_context.identified_notes` carries
        # every pattern-claimed note's TEXT unconditionally and exempt from `_SEMANTIC_NOTE_BUDGET`
        # already; `line_item_llm.build_request` intersects that with the line's set, so a note
        # named here is a note the document-level block was already built to carry. That is the
        # same asymmetry read the other way round: this side was the one refusing what the other
        # side had already paid for. Measured across the five filings the sets grow 586 -> 617
        # note-slots on the reference filing, mean 2.74 -> 2.88 per line, and nothing is displaced.
        #
        # WHAT IT DOES NOT FIX, stated because the figure moves and could be read as this change's
        # doing: some named notes arrive as an entry with NO rows and NO prose — 38 of 206 named
        # pairs on the reference filing, 35 of 285 on 000709, 0 of 116 on 1966. The note entry
        # travels, the note's body does not, because the extracted table carries no items. Naming
        # more declared notes therefore raises the count of such entries (28 lines -> 33 on 000709)
        # without any of them being a note this function withheld. It is an extraction gap on the
        # CAS filings, and `scripts/note_context_to_llm.py` reports it on its own line.
        #
        # `note_selection: any` DOES NOT DECLINE THIS. That field says a line's printed reference
        # is not to be trusted ahead of a score, which is a statement about the FILING's claim;
        # `identified_notes` records that it "no longer gates" the pattern pass, and a line whose
        # author wrote the pattern and then declined to prefer it would be declaring two opposite
        # things. 539 of 539 shipped lines declare neither value, so nothing shipped reads
        # differently either way.
        # THE DECLARATION DECIDES MEMBERSHIP; THE SCORE STILL DECIDES ORDER. Measured, and the
        # second correction to this: ordering the declared notes as the document PRINTS them made
        # 七、2 作為出租人 — a lessor note, claimed through a pooled note key — the FIRST note for
        # `sub__ltp_nc_portion_of_fincl_asset_notes` and its three siblings, ahead of 七、18
        # 其他權益工具投資, which the probe scores 0.723 and which is the disclosure they are about.
        # On the spy route, which cites the first note's total line, that alone took the long-term
        # securities column from 575,243,925.97 — the printed figure — to 8,629,412,600.11 and put
        # `section_reconciliation:bs_nca` out by 8.05bn in both periods. Printed order is not an
        # opinion about relevance and must not be allowed to act as one.
        #
        # SO THE SCORING PASS IS RUN UNCAPPED AND UNFLOORED, once, and used twice: to rank the
        # declared notes among themselves, and — floored and capped exactly as before — as the
        # scored contribution. A declared note the probe scores nothing for keeps its place and
        # sorts last among the declared, which is the honest ranking for it.
        ranked = notes_for_line_item(item, pool, min_score=0.0, cap=len(notes or ()) or cap,
                                     parent=parent)
        rank = {h.note: (i, h.score) for i, h in enumerate(ranked)}
        declared = sorted(
            (NoteHit(note=num, title=titles.get(num, ""), score=1.0, via="declared")
             for num in declared_notes(item, notes) if num not in {c.note for c in first}),
            key=lambda h: rank.get(h.note, (len(ranked), 0.0))[0])
        held = {h.note for h in first} | {h.note for h in declared}
        scored = [h for h in ranked if h.score >= min_score and h.note not in held][:cap]
        # THE CITED/SCORED TRADE IS UNTOUCHED — `first + scored` is still bounded by `cap`, so a
        # citation still displaces the lowest-scoring note and `note_sets`' behaviour on a line
        # that declares nothing is byte-for-byte what it was.
        hits = declared + (first + scored)[:cap]
        # WIDEN BY CONTENT WHEN THE HEADER SEARCH CAME UP SHORT — see `WIDEN_BELOW`. The header
        # route asks which HEADING is about this line; when its best answer is not one it would
        # itself call a true match, the other question is worth asking: which note in this document
        # CONTAINS the rows this line is looking for.
        #
        # APPENDED, NEVER SUBSTITUTED, and the header hits keep their order. A heading is its
        # author's own statement of subject and outranks a row match on evidence; what widening
        # adds is reach where that statement was not found. The cap still bounds the total, so a
        # line with four confident headings widens to nothing extra.
        # READ OFF THE SCORED HITS, NOT OFF `hits`. A cited or declared note enters with score
        # 1.0, so asking `hits` would report a confident header match on every line the filing
        # cites or the configuration declares for, and switch widening off — for the lines
        # most likely to need it, since a citation and a thin probe often go together. The question widening answers is still "did the HEADER search come up
        # short", and only the header search can answer it.
        best = max((h.score for h in scored), default=0.0)
        if best < WIDEN_BELOW and len(hits) < cap:
            seen = {h.note for h in hits}
            for extra in widen_by_content(item, notes, cap=cap - len(hits)):
                if extra.note not in seen:
                    hits.append(extra)
                    seen.add(extra.note)
        if hits:
            out[item.key] = hits
    return out


def discriminating_tokens(item) -> set[str]:
    """The tokens that actually distinguish this line's ROW from its container.

    THE DEFECT THIS CLOSES, and it made the gate above almost inert. The check was
    `{t for term in row_terms for t in subject_tokens(term)}` — every token of every term — and
    `row_terms` are multi-word PHRASES. For `sub__fixed_asset_depreciation` the 36 terms include
    "depreciation of property plant and equipment" and "amortization and depreciation", so the
    accepted set contained 77 tokens including:

        assets  fixed  property  plant  equipment  investment  lease  payments  use  right  and

    and ANY caption containing the word "and" passed. Measured against the shipped configuration,
    the gate accepted "Deposits and other receivables" (on `and`) and "Contract assets, net (i)"
    (on `assets`) for a DEPRECIATION line, and 固定资产 — the container's own name, which is the
    single error the whole check was written to refuse.

    TWO NARROWINGS, and both are needed. Measured on that line against eight captions that must
    pass and six that must be refused: today's rule passes 8 and wrongly accepts 3; the first
    narrowing alone still accepts 固定资产; both together pass 8 and wrongly accept 0.

      1. ONLY TOKENS FROM A TERM THE AUTHOR NAMED ALONE. `depreciation`, `amortisation`, `折旧`,
         `摊销` are all authored as standalone terms; `assets`, `and`, `plant` never are — they
         exist only inside a phrase. A token that an author never used by itself was never their
         statement of what the row is called.
      2. MINUS THE NOTE-LEVEL VOCABULARY. A token that appears in `note_terms` or `note_title_any`
         names the CONTAINER by construction — that is what those fields are for — so it cannot be
         what distinguishes a row inside it. This is the two-level distinction the vocabulary work
         is about, applied to the check. It is what removes 资产 (from 固定资产折旧, whose bigrams
         include the container noun) while leaving 折旧.

    THE FALLBACK IS NOT DECORATION. If narrowing leaves nothing, the full token set is returned
    rather than an empty one: an empty set makes the gate refuse EVERY caption for that line and
    lose its figures silently, which is far worse than the over-acceptance being fixed here.
    Measured, no shipped line needs it — all 77 keep a non-empty, smaller set — so it exists for
    the line somebody authors next.
    """
    source = getattr(item, "note_source", None)
    terms = [str(t) for t in (getattr(source, "row_terms", None) or ())]
    if not terms:
        return set()
    every = {t for term in terms for t in subject_tokens(term)}
    # A "single word" is a term with no internal space. Han compounds have none by nature, which
    # is why narrowing (2) is what carries the Chinese half.
    solo = {t for term in terms if " " not in term.strip() for t in subject_tokens(term)}
    container: set[str] = set()
    for field in ("note_terms", "note_title_any"):
        for value in (getattr(source, field, None) or ()):
            container |= set(subject_tokens(str(value)))
    return (solo - container) or every


def caption_is_vetoed(item, caption: str) -> tuple[bool, str]:
    r"""Does this caption match something the author said must NEVER count? ``(vetoed, why)``.

    THE LARGEST BODY OF AUTHORED CONFIGURATION IN THE SET, AND IT WAS INERT ON THE PATH THAT
    PRODUCES FIGURES. Across the 77 parts there are 11,067 `row_terms_none` and 3,876
    `row_caption_none` entries — 14,943 exclusions against 4,211 positive terms, about three and a
    half negatives for every positive. `note_sourced` applies them on the deterministic row route
    (:140) and the prose route (:289). Nothing applied them to an LLM answer: `content_vetoes` is
    read only inside `widen_by_content`, and widening ships off (`WIDEN_BELOW = 0.0`).

    WHAT GOT THROUGH, measured on `sub__pbt_oper_exp_depreciation`, whose author vetoed
    `['^\s*at\s+\d', 'opening', 'closing', 'disposal', 'write.?off', 'transfer', 'reclassif']`:

        Opening balance of accumulated depreciation   vetoed   ACCEPTED
        Depreciation on disposal                     vetoed   ACCEPTED
        Depreciation transferred out                 vetoed   ACCEPTED

    Every one shares `depreciation`, so the positive floor passed them — and every one is a real
    row in a PP&E movement schedule, sitting beside the row that is actually wanted. An opening
    accumulated balance published as the period's charge is the error this vocabulary exists to
    prevent.

    BOTH FIELDS, AND THE DIFFERENCE BETWEEN THEM. `row_caption_none` entries are REGEXES matched
    against the caption; `row_terms_none` entries are plain terms, matched on shared subject tokens
    the way the positive floor matches. A term is not compiled as a pattern: `note_sourced` keeps
    them apart for the same reason, and treating a plain term as a regex is how a stray bracket
    becomes an accidental veto on everything.
    """
    source = getattr(item, "note_source", None)
    if source is None or not caption:
        return False, ""
    for raw in (getattr(source, "row_caption_none", None) or ()):
        try:
            if re.search(str(raw), caption, re.IGNORECASE):
                return True, f"the caption matches an authored exclusion /{raw}/"
        except re.error:
            continue          # a pattern that will not compile is reported by `bad_patterns`
    got = set(subject_tokens(caption))
    for raw in (getattr(source, "row_terms_none", None) or ()):
        if got & set(subject_tokens(str(raw))):
            return True, f"the caption shares a subject word with the excluded term {raw!r}"
    return False, ""


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
    wanted = discriminating_tokens(item)
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
