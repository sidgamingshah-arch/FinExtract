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


def subject_tokens(text: str) -> list[str]:
    """The words a subject is compared on. PUBLIC because `services.mapping` builds the probe
    and this module scores it: two different splits would let the boilerplate filter strip
    tokens the scorer still counts."""
    return _WORD.findall((text or "").lower())


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

    NOTE CAPTIONS COME FROM ``NotesTable.items``, NOT ``source_text``. ``source_text`` is the page
    dump the note was parsed out of — unbounded, and mostly narrative accounting policy that shares
    high-IDF words with everything and so scores noisily against every row. ``items`` are the note's
    own reconstructed rows, which is what a breakdown actually consists of. ``source_text`` is used
    only as a fallback for a note that produced no rows, and truncated when it is.
    """
    units: list[ContextUnit] = []
    for table in getattr(doc, "notes", None) or ():
        captions = [i.raw_label for i in (getattr(table, "items", None) or ())
                    if getattr(i, "raw_label", "")][:captions_per_unit]
        if not captions and getattr(table, "source_text", ""):
            captions = [ln.strip() for ln in table.source_text.splitlines()
                        if ln.strip()][:captions_per_unit]
        if not (captions or getattr(table, "title", "")):
            continue
        units.append(ContextUnit(kind="note", ref=str(table.note_number),
                                 title=table.title or "", captions=tuple(captions)))

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
