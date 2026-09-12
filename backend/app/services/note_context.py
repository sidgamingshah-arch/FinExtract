"""The notes and face rows that are ABOUT the same thing as a caption, chosen by meaning.

WHY THIS EXISTS. The batch request used to carry one piece of document evidence per row: the text
of the note that row explicitly CITES (``stages.map_ontology``, the old ``cited_note_text``). That
is the easy half of the problem and the smaller half:

* A caption with no printed note reference got NOTHING, however much the filing said about it
  elsewhere. On the shipped filings most face rows carry no reference number at all.
* The single hardest decision this tier now makes — is this row the WHOLE figure or one COMPONENT
  of it (``LlmBatchItem.role``) — is a decision ABOUT A RELATIONSHIP between two printed rows. The
  cited note alone cannot show it. The evidence is the other row: a note row of 4,000 beside a face
  line of 12,345 is a component; a note total of 12,345 beside the same face line is the same money
  printed twice. Both are invisible unless both rows are in the request.

So the context is now a SET of units — several notes AND face rows from elsewhere in the document —
selected because they discuss the same subject as the caption.

HOW "SAME SUBJECT" IS DECIDED, AND WHY IT IS NOT STRING MATCHING. There is no embedding provider in
this system (``services.mapping`` says so where the embedding tier used to be), so the similarity
cannot come from a model. It comes from the CONFIGURATION, which is the one place where the meaning
of each concept is written down in prose: a line item's ``definition``, ``include`` list and aliases
are sentences about what the concept IS. Scoring a note against those sentences asks "does this note
discuss what this concept is authored to mean" — which is a question about meaning even though the
arithmetic is over words. The probe is built from the row's own caption plus the criteria of the
concepts the deterministic tiers already think it might be, so the notes chosen for a row are the
notes that discuss the row's plausible readings.

Two properties make that work rather than merely look like it works:

* IDF OVER THE DOCUMENT'S OWN UNITS. Weight is document frequency, computed across this filing's
  own notes and rows — so "total", "group", "company", "year" earn ~0 weight automatically because
  they are everywhere, and "impairment", "leasehold", "debentures" earn a lot because they are not.
  No stopword list to maintain, and the weighting adapts to each filing's vocabulary.
* A SCALE-FREE SCORE. The similarity is an IDF-weighted COSINE, in 0..1. Two things follow that a
  weighted-overlap sum would get wrong: a long note no longer wins every row just by sharing more
  words with everything, and the score does not grow with the size of the filing — so the threshold
  in configuration means the same thing on a three-note extract as on a forty-note annual report.
  Measured over the shipped rulebook's own criteria (``test_note_context_ranking``): the correct
  note ranks first 7 times out of 7, scoring 0.360 at worst against 0.131 for the 90th percentile
  of unrelated pairs.

A CITED NOTE IS NEVER SUBJECT TO ANY OF THIS. An explicit printed reference is evidence; a
similarity score is an inference. Cited notes are included first and unconditionally, marked
``cited: true``, and only the remaining room is filled by score. So this can add context, never
take away the context the old behaviour supplied.

EVERYTHING IS BOUNDED, because context rides in the user message of every batch call. Notes and face
rows have separate caps, note captions are truncated per unit, and a character budget cuts the list
whatever the caps allow. Overrunning the response budget is what made the LLM path unusable before
(see ``_BATCH_RESPONSE_TOKENS_PER_ITEM``), so the bound is not optional.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

# Content words only: 3+ letters, so units, years and row numbers do not enter the vocabulary.
# Digits are excluded deliberately — an amount matching is handled by showing the amount, not by
# treating it as a word, because "12,345" and "12,346" are equally distant as tokens and utterly
# different as evidence.
_WORD = re.compile(r"[a-z]{3,}")

# HAN RUNS, AS OVERLAPPING CHARACTER BIGRAMS. Without this the tokeniser was `[a-z]{3,}` and
# nothing else, so 固定资产折旧 produced NO tokens and every Chinese heading scored exactly 0.000
# against every probe — including a Chinese one. Measured before the change: on suncreate, 0 of 120
# authored (line item, note) pairs were found at ANY rank, which is not a threshold that needs
# tuning but a script the scorer could not see.
#
# BIGRAMS RATHER THAN A SEGMENTER, deliberately. Chinese is written without spaces, so a word-level
# split needs a dictionary and a model, and a wrong split is silent. Overlapping 2-character
# shingles need neither and cannot mis-segment: 固定资产折旧 yields 固定/定资/资产/产折/折旧, which
# contains the real words 固定/资产/折旧 among near-misses that no other heading shares either. The
# IDF weighting then does what it does for English — a shingle in every heading contributes nothing,
# one in a single heading contributes the most — so the near-misses cost precision, not correctness.
#
# A ONE-CHARACTER RUN yields that character, because a lone 现 or 税 is a real subject word and a
# bigram cannot be formed from it.
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")

# Below this a note's "sentence" is a fragment — "(continued)", a stray figure, a page header —
# which matches nothing and only dilutes the IDF of the words that do.
_MIN_PROSE_SENTENCE = 26

# How many note NUMBERS similarity may add to `identified_notes`, over the whole document. See the
# bound's own comment in `identified_notes` for why a per-line cap is not enough.
_SEMANTIC_NOTE_BUDGET = 8


# THE ENUMERATOR A HEADING ARRIVES WITH, AND WHY IT HAS TO BE STRIPPED BEFORE MATCHING.
#
# MEASURED: 1,486 of 3,359 note headings in the corpus (44%) begin with a BARE separator - "、 公司
# 概况", "、其他应收款" - because the heading is extracted from a line whose Han enumerator ("五、")
# has been split off, leaving the separator at the front of the title. A further slice begins with
# the full enumerator, and some with a stray ")".
#
# Almost every authored `note_title_any` pattern is anchored: `^\s*(?:\d+[.、)]?\s*)?其他应收款`.
# That optional group admits an ARABIC enumerator and nothing else, so it cannot pass a leading
# "、" and the pattern fails on the very filings it was written for -
# `sub__rp_other_receivables_note` missed "、其他应收款" on 11 of 18 filings while its own pattern
# spells that exact phrase. The note then never reaches the request, so neither its rows nor its
# PROSE reach the model, and the line comes back empty for a reason no log names.
#
# FIXED HERE RATHER THAN IN THE CONFIGURATION, because the alternative is editing the anchor of
# every pattern in the set - hundreds of them, each a chance to widen one by accident. A pattern is
# tested against the heading AS EXTRACTED and against the heading with its enumerator removed, so:
#
#   * an anchored pattern now matches the content it names, whatever enumerator precedes it;
#   * a pattern that deliberately matches an enumerator still matches, because the raw spelling is
#     tried first and unchanged;
#   * nothing is loosened in the middle of a heading - only the leading run is removed.
_LEAD = re.compile(r"^[\s)）]*(?:[一二三四五六七八九十百]+|\d+)?\s*[、．。.)）]?\s*")


def title_variants(title: str) -> tuple[str, ...]:
    """The heading as extracted, and with any leading enumerator or separator removed.

    Both, in that order, and de-duplicated - a heading with no enumerator yields one string, so a
    caller pays nothing for the headings that never had the problem.
    """
    raw = title or ""
    out = [raw]
    stripped = _LEAD.sub("", raw, count=1).strip()
    if stripped and stripped != raw:
        out.append(stripped)
    return tuple(out)


def matches_title(pattern, title: str) -> bool:
    """Whether one compiled pattern matches a note heading, enumerator notwithstanding."""
    return any(pattern.search(v) for v in title_variants(title))



def subject_tokens(text: str) -> list[str]:
    """The words a subject is compared on, in both scripts.

    PUBLIC because `services.mapping` builds the probe and this module scores it: two different
    splits would let the boilerplate filter strip tokens the scorer still counts. That is also why
    the Han handling lives HERE rather than in the line-item selector — one split, or the two sides
    of every comparison stop agreeing.
    """
    lowered = (text or "").lower()
    out = _WORD.findall(lowered)
    for run in _HAN.findall(lowered):
        if len(run) == 1:
            out.append(run)
            continue
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


_tokens = subject_tokens          # the name used inside this module


@dataclass(frozen=True)
class ContextUnit:
    """One thing the document says, in the shape the model is shown it.

    ``kind`` is the distinction the model needs to reason about role: a ``note`` is a breakdown, a
    ``face`` row is a printed statement line. Same subject, different standing.
    """

    kind: str                                   # "note" | "face"
    ref: str                                    # note number, or the statement it was printed on
    title: str = ""
    captions: tuple[str, ...] = ()
    amount: str = ""                            # one representative figure, for face rows
    section: str = ""
    row_id: str = ""                            # so a row is never offered as context for itself
    # True for a unit that is a SENTENCE of a note's narrative rather than a row of its table.
    # Carried into the payload so the model can tell a printed row from a statement about one —
    # a footnote saying where a figure is included is evidence of a different kind from the
    # figure itself.
    prose: bool = False
    tokens: tuple[str, ...] = field(default=(), compare=False)

    def probe_text(self) -> str:
        return " ".join((self.title, *self.captions))

    @property
    def key(self) -> tuple[str, str]:
        """This unit's identity for de-duplication.

        A note is identified by its NUMBER — the same note reached twice is one note. A face row is
        identified by its ROW, because every row on a statement shares ``ref``: keying a face unit
        on ``ref`` would silently admit exactly one row per statement, which is the difference
        between offering the neighbourhood and offering a single arbitrary line.
        """
        return (self.kind, self.row_id or self.ref)

    def payload(self, *, cited: bool = False) -> dict:
        out: dict = {"kind": self.kind, "ref": self.ref}
        if self.title:
            out["title"] = self.title
        if self.section:
            out["section"] = self.section
        if self.captions:
            out["rows"] = list(self.captions)
        if self.amount:
            out["amount"] = self.amount
        if cited:
            # WHY IT IS HERE, so the model can weigh a printed reference above a resemblance.
            out["cited"] = True
        if self.prose:
            out["prose"] = True
        return out


def _with_tokens(unit: ContextUnit) -> ContextUnit:
    from dataclasses import replace
    return replace(unit, tokens=tuple(_tokens(unit.probe_text())))


class ContextPool:
    """Every unit this document offers, with the IDF weights its own vocabulary implies.

    Built ONCE per document rather than per batch: document frequency is a property of the filing,
    and computing it per call would make a unit's weight depend on which batch was asking.
    """

    def __init__(self, units: list[ContextUnit]) -> None:
        self.units = [_with_tokens(u) for u in units]
        seen = Counter()
        for u in self.units:
            seen.update(set(u.tokens))
        n = max(1, len(self.units))
        # Smoothed IDF, in the form that REACHES ZERO. `log((n + 1) / (df + 1))` is exactly 0 when
        # a token appears in every unit, so the filing's own boilerplate — "total", "group",
        # "company", "consolidated" — contributes literally nothing to any score. That is what
        # replaces a stopword list, and it only works because the zero is exact: the more familiar
        # `log(1 + n / (1 + df))` bottoms out near 0.69, which is enough for two boilerplate words
        # to select an unrelated note.
        self.idf = {tok: math.log((n + 1) / (df + 1)) for tok, df in seen.items()}

    def __len__(self) -> int:
        return len(self.units)

    def _score(self, unit: ContextUnit, probe: set[str]) -> float:
        """IDF-weighted cosine between the probe and the unit, in 0..1.

        WHY COSINE AND NOT A WEIGHTED-OVERLAP SUM. A raw sum of IDF weights grows with the pool:
        IDF is a logarithm of the unit count, so the same note and the same probe score higher in a
        forty-note filing than in a three-note extract. A THRESHOLD IN CONFIGURATION HAS TO MEAN THE
        SAME THING ON EVERY DOCUMENT, and under a sum it would silently be strict on small filings
        and loose on large ones — the small ones being exactly where a row most needs its context.
        Dividing by both vectors' norms removes the scale, and the result is a 0..1 fraction that
        reads as "how much of this subject the unit expresses".

        THE PROBE IS RESTRICTED TO THE POOL'S VOCABULARY. A definition uses words this filing never
        prints; those words say nothing about which of its notes is closest, and leaving them in the
        probe's norm would penalise every concept whose criteria happen to be verbose. So the
        question the score answers is: of the probe's meaning this document is CAPABLE of
        expressing, how much does this unit express.
        """
        if not unit.tokens or not probe:
            return 0.0
        shared = probe.intersection(unit.tokens)
        if not shared:
            return 0.0
        num = sum(self.idf.get(t, 0.0) ** 2 for t in shared)
        if not num:
            # Every shared token is boilerplate this filing prints everywhere. A match on those is
            # a coincidence, not a subject.
            return 0.0
        probe_norm = math.sqrt(sum(self.idf[t] ** 2 for t in probe if t in self.idf))
        unit_norm = math.sqrt(sum(self.idf.get(t, 0.0) ** 2 for t in set(unit.tokens)))
        if not probe_norm or not unit_norm:
            return 0.0
        return num / (probe_norm * unit_norm)

    def select(self, *, probe_text: str, cited_refs: set[str] = frozenset(),
               exclude_row_ids: set[str] = frozenset(), notes_cap: int = 3,
               face_cap: int = 3, char_budget: int = 1200,
               min_score: float = 0.22) -> list[dict]:
        """The units for one row: its cited notes first, then the closest by meaning.

        ``exclude_row_ids`` keeps the batch's own rows out of its context — they are already in
        ``source_items``, and offering a row to itself as corroboration would be a request that
        agrees with itself.
        """
        probe = set(_tokens(probe_text))
        chosen: list[tuple[ContextUnit, bool]] = []
        used: set[tuple[str, str]] = set()

        # (1) EXPLICIT REFERENCES, unconditionally and first.
        for unit in self.units:
            if unit.kind == "note" and unit.ref in cited_refs:
                chosen.append((unit, True))
                used.add(unit.key)

        # (2) THE REST BY SCORE, capped per kind so a filing with forty notes cannot crowd out the
        # face rows that carry the whole-vs-component evidence, and vice versa.
        room = {"note": max(0, notes_cap - len(chosen)), "face": max(0, face_cap)}
        ranked = sorted(
            (
                (self._score(u, probe), i, u)
                for i, u in enumerate(self.units)
                if u.key not in used
                and not (u.row_id and u.row_id in exclude_row_ids)
            ),
            # Descending score; document order breaks ties, so the selection is deterministic and a
            # rerun of the same filing produces the same request.
            key=lambda t: (-t[0], t[1]),
        )
        for score, _i, unit in ranked:
            if score < min_score or room.get(unit.kind, 0) <= 0:
                continue
            chosen.append((unit, False))
            room[unit.kind] -= 1
            used.add(unit.key)

        # (3) THE CHARACTER BUDGET, applied last and to the ordered list, so what it drops is
        # always the least relevant thing rather than whatever happened to be at the end.
        out: list[dict] = []
        spent = 0
        for unit, cited in chosen:
            entry = unit.payload(cited=cited)
            cost = sum(len(str(v)) for v in entry.values()) + sum(
                len(x) for x in entry.get("rows", ()))
            if out and spent + cost > char_budget:
                break
            out.append(entry)
            spent += cost
        return out


def build_pool(doc, stmt_by_page: dict[int, str] | None = None, *,
               captions_per_unit: int = 8) -> ContextPool:
    """Every note and face row of one document, as context units.

    A NOTE CONTRIBUTES TWO KINDS OF UNIT, and it used to contribute only the first.

    * ITS ROWS, from ``NotesTable.items`` — the note's own reconstructed breakdown, which is what a
      table of figures consists of.
    * ITS PROSE, from ``source_text``, ONE SENTENCE PER UNIT. This was previously a fallback used
      only when a note produced no rows, and the whole narrative arrived as a single unit when it
      was used at all. Both choices hid the thing this exists for.

    WHY A SENTENCE RATHER THAN THE NARRATIVE. A footnote states figures no row carries — measured on
    laisun.pdf, "^ Depreciation charges of approximately HK$529,841,000 … are included in 'other
    operating expenses'" is the operating-expense share of the depreciation charge, and 529841
    appears in no extracted row anywhere in that filing. As one 1,238-character unit that sentence
    scored 0.123 against a 0.22 threshold, because the score divides by the unit's own length and a
    long narrative shares words with everything. Split, the same sentence scores 0.137 on the
    shipped configuration and 0.352 once the concept's definition says what the line MEANS — rank 3
    of 1,466 units rather than 33.

    So the split is necessary and not sufficient: it makes a footnote reachable, and the
    definition decides whether anything reaches it.

    THE POOL GROWS — 404 units to about 1,466 on that filing — and the IDF is computed over the
    pool, so every score moves. That is the point rather than a side effect: a word common to a
    thousand policy sentences should weigh less than one that appears in three.
    """
    import re as _re

    units: list[ContextUnit] = []
    for table in getattr(doc, "notes", None) or ():
        rows = [i.raw_label for i in (getattr(table, "items", None) or ())
                if getattr(i, "raw_label", "")][:captions_per_unit]
        if rows or getattr(table, "title", ""):
            units.append(ContextUnit(kind="note", ref=str(table.note_number),
                                     title=table.title or "", captions=tuple(rows)))
        # THE PROSE, one sentence per unit. Sentences shorter than this carry no subject — a
        # fragment like "(continued)" or a bare figure would add a unit that matches nothing and
        # dilute the IDF of the words that do.
        prose = getattr(table, "source_text", "") or ""
        for sentence in _re.split(r"(?<=[.;])\s+|\n{2,}", prose):
            sentence = sentence.strip()
            if len(sentence) < _MIN_PROSE_SENTENCE:
                continue
            units.append(ContextUnit(kind="note", ref=str(table.note_number),
                                     title=table.title or "", captions=(sentence,),
                                     prose=True))

    pages = stmt_by_page or {}
    for li in getattr(doc, "line_items", None) or ():
        caption = getattr(li, "source_label", "") or ""
        if not caption:
            continue
        statement = ""
        amount = ""
        for ev in (getattr(li, "values", None) or {}).values():
            prov = getattr(ev, "provenance", None)
            if not statement and prov is not None:
                statement = pages.get(prov.page_index, "") or ""
            # ONE representative figure, because the model needs the MAGNITUDE to tell a component
            # from a repeated total, not the full period grid. Showing every column would triple the
            # cost of the block for a distinction the first number already settles.
            if not amount and getattr(ev, "value", None) is not None:
                amount = str(ev.value)
        units.append(ContextUnit(
            kind="face", ref=statement or "unclassified", captions=(caption,),
            amount=amount, section=getattr(li, "section_hint", "") or "",
            row_id=str(li.id)))
    return ContextPool(units)

# ── the notes the CONFIGURATION identifies ────────────────────────────────────────────────────

# The shortest run of characters worth calling a repeat rather than a coincidence. A page-header
# band or a repeated table-column row is hundreds of characters; a shared phrase of a dozen is two
# sentences happening to agree, and removing it would cut a sentence in half. 40 is measured
# against the blocks that actually recur on the reference filing — the smallest true furniture
# block is 129 characters and the largest 520, and nothing legitimate sits near the floor.
_REPEAT_BLOCK = 40
# How far back to look for a repeat. Furniture recurs page to page, so the previous fragment or
# two is where it is; comparing every new fragment against the whole accumulated note is quadratic
# on a long note for nothing. 24,000 characters is longer than any multi-fragment note measured
# (laisun's widest is 12,664).
_REPEAT_WINDOW = 24_000


def dedupe_prose(pieces: list[str]) -> str:
    """Every fragment's prose in document order, with text that already appeared removed.

    ORDER IS PRESERVED AND NOTHING DISTINCT IS DROPPED. Each fragment keeps the parts of itself
    that have not been seen; a block of `_REPEAT_BLOCK` characters or more that already appears in
    what has been kept so far is cut out. So the first occurrence of every string survives, which
    is the property `note_sourced.resolve_sources` depends on — it verifies a prose-stated amount
    against the note's own text, and an amount that was printed on three continuation pages still
    appears once.

    WHY NOT SUFFIX/PREFIX MATCHING, which is the obvious reading of "remove the overlap": measured
    on the reference filing, consecutive fragments share EXACTLY 0 characters at their joins. The
    repetition is not a seam, it is furniture reprinted mid-fragment — a column-header band, a
    "(continued)" sub-heading — so it has to be found wherever it sits.
    """
    import difflib

    kept: list[str] = []
    seen = ""
    for piece in pieces:
        text = (piece or "").strip()
        if not text:
            continue
        if not seen:
            kept.append(text)
            seen = text
            continue
        window = seen[-_REPEAT_WINDOW:]
        matcher = difflib.SequenceMatcher(None, window, text, autojunk=False)
        drop = []
        for block in matcher.get_matching_blocks():
            if block.size < _REPEAT_BLOCK:
                continue
            # SHRINK TO WHOLE WORDS BEFORE CUTTING, and this is not tidiness — it is the
            # difference between removing furniture and splicing a number.
            #
            # A matching block is the longest run of IDENTICAL characters, and it greedily absorbs
            # the shared parts of two figures that differ. Measured on laisun's note 15: fragment 4
            # reads "…Average market unit HK$13,600 The higher…" and fragment 5
            # "…Average market unit HK$13,500 The higher…", so difflib matches through "HK$13," and
            # again from "00" — and cutting both left the literal "5" where an amount had been.
            # Seven amounts were destroyed that way, across two notes, all of them unobservable
            # inputs in a fair-value table: exactly the numbers a filing states once.
            #
            # Pulling both edges back to whitespace means a cut can only ever remove whole tokens,
            # so a figure is either wholly repeated — in which case the earlier copy survives — or
            # wholly kept.
            start, end = block.b, block.b + block.size
            while start < end and not text[start].isspace():
                start += 1
            while end > start and not text[end - 1].isspace():
                end -= 1
            if end - start >= _REPEAT_BLOCK:
                drop.append((start, end))
        if drop:
            out, at = [], 0
            for start, end in drop:
                if start > at:
                    out.append(text[at:start])
                at = max(at, end)
            out.append(text[at:])
            # Single spaces where a block was cut, so two sentences do not run together.
            text = " ".join(part.strip() for part in out if part.strip())
        if text:
            kept.append(text)
            seen += "\n" + text
    return "\n".join(kept)


def identified_notes(line_item_set, notes, *, cited=None) -> list[dict]:
    """Every note a `note_source` declaration names, IN FULL — all rows and all prose.

    WHY IN FULL, AND WHY NOT SCORED. These are not notes a similarity function guessed at: an
    author has declared, in configuration, that this note is where a line's figure lives. That is a
    stronger statement than any score, so the note is passed whole and unconditionally — every row
    caption with its figures, and the surrounding PROSE.

    THE PROSE IS THE POINT. Measured on laisun.pdf: the operating-expense share of the depreciation
    charge is disclosed nowhere in a table. It is in a footnote — "^ Depreciation charges of
    approximately HK$529,841,000 (2024: HK$665,553,000) are included in 'other operating
    expenses'" — and 529841 appears in NO row value anywhere in the document. A row-caption regex
    cannot reach it, and the row-based context that preceded this could not either: the note
    reached the pool as a single run-on paragraph and scored 0.052 against a 0.22 threshold.

    ONCE PER REQUEST, NOT ONCE PER ROW, and that is a shape decision rather than a budget one. The
    identified notes are 24,910 characters on laisun; attaching them to each of a request's 43
    source items would be about a megabyte and would fail the provider outright rather than merely
    cost more. They are document-level evidence, so they belong beside the source items rather than
    inside each one.
    """
    import re
    decls = [i for i in (getattr(line_item_set, "items", None) or ())
             if getattr(i, "note_source", None) is not None]
    if not decls:
        return []
    compiled: list[tuple[str, list]] = []
    for item in decls:
        pats = []
        for raw in (getattr(item.note_source, "note_title_any", None) or ()):
            try:
                pats.append(re.compile(raw, re.IGNORECASE))
            except re.error:
                continue
        if pats:
            compiled.append((item.key, pats))

    # THE SEMANTIC PASS IS UNCONDITIONAL, and `note_selection` no longer gates it.
    #
    # IT USED TO. The field chose between `patterns` (the line's `note_title_any` regexes and only
    # those) and `semantic` (those PLUS the notes its meaning scores against the headings), and the
    # branch here skipped a `patterns` line. That was never a choice between selectors: this
    # function passes a pattern-named note UNCONDITIONALLY, so `patterns` removed the line from the
    # semantic pass and added nothing. Measured, 539 of 539 lines declared neither value, so the
    # option existed and was never taken.
    #
    # AND THE UNION IS MEASURED. Scored against the authored regexes as ground truth — they produced
    # every focus figure, so the notes they match are notes the line really is in — semantic
    # selection finds 100% of them within the top ten on the English filing and only 53% on the
    # Chinese one. Replacing the patterns would therefore LOSE notes on a PRC filing, silently,
    # which is the one outcome worse than carrying a note nobody asked for. Scoring ADDS the notes
    # the patterns missed — the gap it exists for: `折旧及摊销` matched none of 25 authored
    # depreciation patterns until anchored combined forms were added by hand.
    #
    # `note_selection` now decides only whether the FILING's own citation ranks ahead of a score —
    # applied below, per line, where the budget is spent.
    semantic_by_note: dict[str, set[str]] = {}
    if decls:
        from app.services.line_item_notes import header_pool, notes_for_line_item
        pool = header_pool(notes)
        by_key = {i.key: i for i in (getattr(line_item_set, "items", None) or ())}
        scored: dict[str, tuple[float, set[str]]] = {}
        for item in decls:
            parent = by_key.get(getattr(item, "parent", "") or "")
            for hit in notes_for_line_item(item, pool, parent=parent):
                best, keys = scored.get(hit.note, (0.0, set()))
                keys.add(item.key)
                scored[hit.note] = (max(best, hit.score), keys)
        # A TOTAL BOUND ON WHAT SIMILARITY MAY ADD, best-scoring first.
        #
        # WHY A TOTAL AND NOT A PER-LINE CAP, which `notes_for_line_item` already applies. A note
        # NUMBER is not a note: measured on the reference filing, 190 extracted tables carry only
        # about 33 distinct numbers, because a note continued across pages repeats its number on
        # every fragment. So claiming note "7" pulls in every fragment of note 7, and 23 numbers
        # added by 77 line items became 56 extra TABLES — `identified_notes` went from 31 tables to
        # 87 and one request from 30,407 tokens to 82,299. Per-line caps cannot see that, because
        # each line asked for a handful.
        #
        # AND AN INFERENCE DOES NOT EARN WHAT A DECLARATION EARNS. This function passes a note IN
        # FULL because an author declared it, which is a stronger statement than any score. A
        # semantically selected note is a guess; passing an unbounded number of guesses in full
        # spends the request on them. The regex-claimed notes are never subject to this bound.
        #
        # THE FILING'S OWN CITATION RANKS AHEAD OF EVERY SCORE, and it is inside the budget rather
        # than exempt from it. A printed "Note 14" beside a face caption is the preparer saying
        # where the detail is, so it is not the same kind of claim as a probe that scored 0.41 —
        # but it is also not an AUTHORED declaration about this configuration, which is what the
        # unconditional pass is for. So it spends budget, and spends it first: `_SEMANTIC_NOTE_
        # BUDGET` exists because a note NUMBER pulls in every fragment carrying it (23 numbers
        # became 56 extra tables and one request went from 30,407 to 82,299 tokens), and a citation
        # costs exactly the same as a guess does.
        # ONLY THE LINES THAT ASKED FOR IT. A line declaring `note_selection: any` has said its
        # printed reference is not to be trusted ahead of a score, so its citation does not buy a
        # place in the budget.
        prioritising = {i.key for i in decls
                        if str(getattr(i, "note_selection", "cited_first") or "") != "any"}

        # A CITATION ENTERS THE CANDIDATES; IT DOES NOT MERELY REORDER THEM. This is the whole
        # point and the first version got it wrong: it sorted `scored` so a cited note came first,
        # which does nothing for a cited note that SCORED NOTHING — and that is exactly the case
        # the capability is for. `note_sets` had already put it in the plan, so the line's
        # `notes_supplied` named note 14 while `build_request` — which takes the note TEXT from
        # here, filtered to the plan's numbers — carried no text for it. The model was pointed at a
        # note it could not read, which is worse than not offering it, and is the same failure the
        # "citation to a note this document does not have" case refuses from the other direction.
        #
        # Only a note THIS DOCUMENT HOLDS is admitted: `have` is built from the tables below, so a
        # reference the pruner dropped or the parser never built still names nothing.
        have = {str(getattr(t, "note_number", "") or "") for t in (notes or ())}
        for key, numbers in (cited or {}).items():
            if key not in prioritising:
                continue
            for number in numbers:
                if not number or number not in have:
                    continue
                best, keys = scored.get(number, (0.0, set()))
                keys.add(key)
                # 1.0 is above every probe score, so the sort below puts a citation first without
                # needing a second key — and a note that BOTH scored well and is cited keeps one
                # entry rather than two.
                scored[number] = (max(best, 1.0), keys)

        cited_numbers = {n for key, numbers in (cited or {}).items() if key in prioritising
                         for n in numbers}
        ranked = sorted(scored.items(),
                        key=lambda kv: (kv[0] not in cited_numbers, -kv[1][0]))
        for note, (_score, keys) in ranked[:_SEMANTIC_NOTE_BUDGET]:
            semantic_by_note[note] = keys

    # ONE ENTRY PER NOTE NUMBER, NOT ONE PER TABLE.
    #
    # A note printed across pages arrives as several tables all carrying its number — laisun's note
    # 4 as seven, note 6 as seven, note 15 as six — and this emitted one entry each. So the note's
    # number, title and `identified_for` went into the payload once per fragment, and a request
    # carrying twelve distinct notes carried thirty-nine entries.
    #
    # ALL THE PROSE STILL TRAVELS. The first attempt at this kept the longest fragment's prose and
    # that is measurably wrong: laisun's note 7 arrives as 1,765 / 1,589 / 1,195 characters and the
    # SHORTEST holds "^ Depreciation charges of approximately HK$529,841,000 … are included in
    # 'other operating expenses'" — the only source `sub__pbt_oper_exp_depreciation` has on that
    # filing, and the measured reason the prose route exists. Note 47 loses its only narrative
    # amount the same way; two of five multi-fragment notes.
    #
    # WHAT IS REMOVED IS THE REPETITION. Measured, consecutive fragments share NO text at their
    # joins (0.0% suffix/prefix) but 12.9% of the prose across laisun's multi-fragment notes is
    # repeated in blocks of 40 characters or more, and it is table furniture: the segment table's
    # 520-character column-header band reprinted on three continuation pages, the fair-value
    # table's header row, the "(continued)" sub-headings, the "2025 2024 … HK$'000" period band.
    # `dedupe_prose` cuts a block that already appeared and keeps the first occurrence of every
    # distinct string, which is the property `note_sourced.resolve_sources` needs: it verifies a
    # prose-stated amount against the note's own text, and an amount printed on three pages still
    # appears once.
    by_number: dict[str, dict] = {}
    order: list[str] = []
    for table in notes or ():
        title = getattr(table, "title", "") or ""
        number = str(getattr(table, "note_number", "") or "")
        # THROUGH `matches_title`, so an anchored pattern is not defeated by the enumerator the
        # heading arrives with — see `_LEAD`. This is the site that decides what reaches a request
        # AT ALL: a pattern that misses here takes the note's rows AND its prose with it.
        claimed = {key for key, pats in compiled
                   if any(matches_title(p, title) or p.search(number) for p in pats)}
        claimed |= semantic_by_note.get(number or title, set())
        wanted_by = sorted(claimed)
        if not wanted_by:
            continue
        rows = []
        for row in getattr(table, "items", None) or ():
            caption = getattr(row, "raw_label", "") or ""
            if not caption:
                continue
            figures = {}
            for ev in (getattr(row, "values", None) or {}).values():
                if getattr(ev, "column_index", None) is not None:
                    continue
                if getattr(ev, "value", None) is None:
                    continue
                figures[str(getattr(ev, "period_label", "") or "?")] = str(ev.value)
            rows.append({"caption": caption, **({"figures": figures} if figures else {})})

        key = number or title
        acc = by_number.get(key)
        if acc is None:
            # THE FIRST FRAGMENT'S TITLE IS THE NOTE'S. A later one is a continuation line —
            # "SEGMENT INFORMATION (CONTINUED)" — or, on a CAS filing, a sentence fragment.
            acc = {"note": number, "title": title, "_for": set(), "_rows": [], "_prose": []}
            by_number[key] = acc
            order.append(key)
        acc["_for"].update(wanted_by)
        acc["_rows"].extend(rows)
        prose = (getattr(table, "source_text", "") or "").strip()
        if prose:
            acc["_prose"].append(prose)

    out: list[dict] = []
    for key in order:
        acc = by_number[key]
        entry: dict = {"note": acc["note"], "title": acc["title"],
                       # WHICH declaration wanted it, so the model can see why this note is here
                       # and which line it is expected to speak to. Unioned across the fragments:
                       # a line whose pattern matched only the continuation page still asked for
                       # this note.
                       "identified_for": sorted(acc["_for"])}
        if acc["_rows"]:
            entry["rows"] = acc["_rows"]
        if acc["_prose"]:
            # The narrative, in page order, with repeated furniture removed once.
            entry["prose"] = dedupe_prose(acc["_prose"])
        out.append(entry)
    return out
