r"""PLAIN PHRASES -> THE PROSE PATTERNS, so an author never writes a regex to reach a footnote.

WHAT THIS REPLACES. `note_source.prose_any` took raw regexes, and the seven lines that use it
carried twenty-eight of them, averaging 300 characters each:

    (?:depreciation|amortisation|amortization)[^.。]{0,200}?(?:included\s+in|charged\s+to|recognised
    \s+in|recognized\s+in|borne\s+by|allocated\s+to|presented\s+in|reported\s+(?:in|within))[^.。]
    {0,80}?(?:(?:other\s+)?operating\s+(?:expenses?|costs?))

An author reading that has to parse regex syntax before they can see the accounting question, and
the question is small: WHICH EXPENSE LINE does the sentence say the charge landed in.

THE DECOMPOSITION IS MEASURED, not assumed. All 28 stored patterns were split structurally and
every one is the same three-part sentence shape:

    SUBJECT            gap    CONNECTIVE          gap   DESTINATION      (the filing's own order)
    "depreciation"    <=200   "included in"      <=80   "other operating expenses"

    DESTINATION        gap    SUBJECT                                    (the reverse order)
    "cost of sales"   <=120   "amortisation"

and of those three parts, measured across the seven lines:

    SUBJECT      IDENTICAL on all 7 — `depreciation|amortisation|amortization`, 折旧|折舊|摊销|攤銷
    CONNECTIVE   IDENTICAL on all 7 — eight English phrasings and eight Han ones
    DESTINATION  the ONLY part that differs
    BOUNDS       IDENTICAL on all 7 — (200, 80) forward English, (120) reverse, (80, 40) / (40) Han

So the subject vocabulary and the connective vocabulary are not properties of a line — they are
properties of THIS KIND OF DISCLOSURE, stated once in `LineItemSet.prose_grammar`. The bounds are
properties of PROSE, stated here as constants, because all seven lines agreed and a bound is not an
accounting judgement. What is left on the line is the destination, in plain words.

WHY THE SUBJECT IS NAMED RATHER THAN REPEATED. It is shared today only because all seven prose
lines are depreciation splits. Pasting the same four words onto each line invites them to drift
apart silently; putting them in ONE shared list would mean a future line about staff costs widens
all seven, so that a sentence reading "staff costs … included in … administrative expenses" would
claim the G&A DEPRECIATION line. A named vocabulary is neither: the seven share one by name, and a
new subject is a new name that touches none of them. Same shape as `section_defaults`, which 475 of
475 items already take their gate from.

WHAT AN AUTHOR CANNOT NOW GET WRONG. A plain phrase cannot fail to compile, so the entire class of
error `_refuse_uncompilable` exists to catch — a pattern that silently stops matching, and a figure
that quietly includes or omits rows nobody can trace — is unreachable through this route. The raw
`prose_any` field is kept as the escape hatch for a sentence shape this grammar cannot express, on
the same principle as `MatchListEditor`'s `pattern` mode: an escape hatch, never the way in.
"""
from __future__ import annotations

import re

from app.services.han import has_han

# ── THE BOUNDS, AND WHY THESE NUMBERS ─────────────────────────────────────────────────────────
#
# All 28 shipped patterns carried these exact bounds, so they are constants rather than fields: a
# knob every line sets the same way is a knob that only offers a way to be inconsistent.
#
# WHY THEY ARE THIS WIDE — the measurement that made the prose route necessary at all. In the
# reference HK footnote, "Depreciation charges of approximately HK$529,841,000 (2024:
# HK$665,553,000) are included in 'other operating expenses'", the gap between "Depreciation" and
# "included in" is 74 characters: the amount, the comparative and a verb phrase all sit between
# them. A caption-length `.{0,40}` bound does not reach, which is why a `row_caption_any` pattern
# cannot serve here however well it works on a table row.
#
# WHY THEY ARE BOUNDED AT ALL. The gap excludes sentence-ending punctuation, so a match can never
# span two sentences; the bound then stops a very long single sentence from joining a subject at
# one end to an unrelated expense line at the other.
#
# WHY HAN IS TIGHTER. Chinese writes the same statement in about a third of the characters — 折旧计
# 入其他经营开支 is 10 characters where the English is 48 — so the same 200-character window would
# reach across several unrelated clauses.
_GAP_SUBJECT_TO_CONNECTIVE = 200
_GAP_CONNECTIVE_TO_PLACE = 80
_GAP_PLACE_TO_SUBJECT = 120
_HAN_GAP_SUBJECT_TO_CONNECTIVE = 80
_HAN_GAP_CONNECTIVE_TO_PLACE = 40
_HAN_GAP_PLACE_TO_SUBJECT = 40

# Anything but a sentence end, so no generated pattern can ever span two sentences. The Latin form
# excludes the full stop as well; the Han form does not, because "HK$1,234.00" inside a Chinese
# sentence would otherwise cut the window in half.
_GAP = "[^.。]"
_HAN_GAP = "[^。]"

_META = re.compile(r"([.^$*+?()\[\]{}|\\])")
# `&` and the dash family, the two characters a filing spaces inconsistently. `R&D`, `R & D` and
# `R&amp;D` are the same words to a reader and three different strings to `==`; the shipped
# patterns wrote `r\s*&\s*d` by hand for exactly this and nothing else needed it.
_AMP = re.compile(r"\s*&\s*")
_DASH = re.compile(r"\s*[-‐-―−]\s*")


def _variants() -> dict[str, str]:
    """Every Han character mapped to a class of ALL its spellings, Simplified and Traditional.

    WHY PER CHARACTER AND NOT PER PHRASE. Folding a whole phrase gives one twin — 分摊至 -> 分攤至 —
    and the shipped pattern was `分[摊攤][至于於]`, which also admits the MIXED spellings a filing
    can print when one character of a compound was typed in the other script. Expanding character
    by character reproduces the shipped alternation exactly instead of a subset of it, which is
    what lets this module claim equivalence rather than approximation.

    Built by inverting `han._T2S`, the generated Traditional -> Simplified table, so the vocabulary
    covered here is the vocabulary the rest of the matcher already folds. Ambiguity is welcome in
    this direction: 并 comes from both 並 and 併, and a pattern that admits all three is right.
    """
    from app.services.han import _T2S

    groups: dict[str, set[str]] = {}
    for trad, simp in _T2S.items():
        groups.setdefault(simp, {simp}).add(trad)
        groups.setdefault(trad, set()).update({simp, trad})
    # A character with variants needs the whole group under it, whichever spelling was authored.
    for simp, members in list(groups.items()):
        for ch in list(members):
            groups.setdefault(ch, set()).update(members)
    return {ch: "".join(sorted(members)) for ch, members in groups.items() if len(members) > 1}


_VARIANTS = _variants()


def _han_fragment(phrase: str) -> str:
    """A Han phrase as a pattern: every character that has variants becomes a class of them."""
    out = []
    for ch in phrase:
        if ch.isspace():
            # A filing may or may not space a Han compound; the extractor's own `_unspace_han`
            # handles the letter-spaced case, and here an authored space is simply optional.
            out.append(r"\s*")
            continue
        group = _VARIANTS.get(ch)
        out.append(f"[{group}]" if group else _META.sub(r"\\\1", ch))
    return "".join(out)


def _latin_fragment(phrase: str, *, plural: bool = False) -> str:
    r"""A Latin phrase as a pattern: forgiving about spacing, ampersands, dashes and the plural.

    THE OPTIONAL TRAILING `s` is the one liberty taken with the author's words, and it is taken
    ONLY on the destination — which is where the shipped patterns took it by hand, on every one:
    `expenses?`, `costs?`, `revenue|revenues`. Applied to the final word in both directions, added
    when absent and made optional when present, so "operating expenses" also reaches "operating
    expense" and "cost of revenue" also reaches "cost of revenues" — where the shipped pattern had
    to list both spellings.

    The subject and the connective do not get it, because the shipped patterns did not give it to
    them and because nothing needs it: no filing writes "borne bys". A liberty taken where it buys
    nothing is just a pattern that reads as though it were guessing.
    """
    text = " ".join((phrase or "").split())
    if not text:
        return ""
    words = text.split(" ")
    if plural:
        words[-1] = words[-1].rstrip("s") or words[-1]
    body = _META.sub(r"\\\1", " ".join(words))
    body = _AMP.sub(r"\\s*&\\s*", body)
    body = _DASH.sub(r"\\s*[-\\u2010-\\u2015\\u2212]\\s*", body)
    body = re.sub(r" +", r"\\s+", body)
    return body + "s?" if plural else body


def _alternation(phrases, han: bool, *, plural: bool = False) -> str:
    """The phrases of one script as a single group, in the order they were authored."""
    def frag(p: str) -> str:
        return _han_fragment(p) if han else _latin_fragment(p, plural=plural)

    parts = [f for f in (frag(p) for p in phrases if (p or "").strip()) if f]
    # Order preserved, duplicates dropped: two authored spellings of one phrase would otherwise
    # both appear in the pattern and neither would be wrong, only noisy in the trail.
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "|".join(out)


def _split_by_script(phrases) -> tuple[list[str], list[str]]:
    """Latin phrases and Han phrases, because they compile into different patterns.

    A pattern pairing an English subject with a Chinese destination would be matching a sentence
    that does not exist, and would carry the Latin bounds over Han text.
    """
    latin, han = [], []
    for p in phrases or ():
        if not (p or "").strip():
            continue
        (han if has_han(p) else latin).append(p)
    return latin, han


def compile_prose(subject, connective, landed_in) -> list[str]:
    """The prose patterns these three plain-phrase lists come to — up to four, two per script.

    FOUR PATTERNS FROM ONE DESTINATION, which is the second half of the reduction. A filing states
    the same fact in either order:

        "depreciation of 1,234 is included in other operating expenses"      subject first
        "other operating expenses include depreciation of 1,234"             destination first

    and in either script. The shipped set wrote all four by hand on every line; three of the four
    are mechanical, so writing them by hand only offered four places to disagree with itself.

    THE REVERSE ORDER TAKES NO CONNECTIVE, exactly as shipped. "Other operating expenses include
    depreciation" puts the verb between them and "Cost of sales — depreciation" has no verb at all,
    so requiring one would lose both. That makes the reverse pattern the looser of the two, which
    the tighter bound (120 against 200+80) is there to hold in check.
    """
    subj_latin, subj_han = _split_by_script(subject)
    conn_latin, conn_han = _split_by_script(connective)
    place_latin, place_han = _split_by_script(landed_in)

    out: list[str] = []
    for subj, conn, place, gap, a, b, c in (
        (subj_latin, conn_latin, place_latin, _GAP,
         _GAP_SUBJECT_TO_CONNECTIVE, _GAP_CONNECTIVE_TO_PLACE, _GAP_PLACE_TO_SUBJECT),
        (subj_han, conn_han, place_han, _HAN_GAP,
         _HAN_GAP_SUBJECT_TO_CONNECTIVE, _HAN_GAP_CONNECTIVE_TO_PLACE, _HAN_GAP_PLACE_TO_SUBJECT),
    ):
        han = gap == _HAN_GAP
        s = _alternation(subj, han)
        p = _alternation(place, han, plural=True)
        # NO DESTINATION IN THIS SCRIPT MEANS NO PATTERN IN IT. A line authored only in English
        # gets two patterns, not four — and not a Han pattern with an empty group, which would
        # match every sentence in the note.
        if not s or not p:
            continue
        k = _alternation(conn, han)
        if k:
            out.append(f"(?:{s}){gap}{{0,{a}}}?(?:{k}){gap}{{0,{b}}}?(?:{p})")
        out.append(f"(?:{p}){gap}{{0,{c}}}?(?:{s})")
    return out


def compile_for(note_source, grammar) -> list[str]:
    """The generated patterns for one line's `note_source`, or none if it has no prose destination.

    THE PREREQUISITE IS NAMED, NOT SILENT. A destination with no subject vocabulary behind it
    cannot produce a pattern, and returning nothing here would be the same silence the raw-regex
    field was criticised for. `LineItemSet` refuses the configuration at load with the line's key
    in the message, so this function is only ever reached with a subject it can resolve.
    """
    if note_source is None or grammar is None:
        return []
    landed_in = list(getattr(note_source, "prose_landed_in", None) or ())
    if not landed_in:
        return []
    name = (getattr(note_source, "prose_subject", "") or "").strip()
    subject = (getattr(grammar, "subjects", None) or {}).get(name) or []
    if not subject:
        return []
    return compile_prose(subject, getattr(grammar, "connective", None) or [], landed_in)
