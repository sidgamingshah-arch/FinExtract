"""Fill a line item from the note rows its own configuration names — for ANY item, from config.

WHY THIS EXISTS, AND WHAT IT REPLACES. `LineItemDef.note_source` says which note a line is read
from and which of that note's rows count: a list of regexes for the note's TITLE, a list for the row
captions that COUNT, and a list for the captions that must be EXCLUDED even when a counting pattern
matched them. Thirteen shipped sub-line items author it, in English, Traditional and Simplified
Chinese — roughly 650 patterns.

NOTHING READ ANY OF THEM. Five concept-specific derivation services used to, and when those were
removed (~2,731 lines, ~562 enumerated entries) nothing took over: a grep for `note_source` across
the whole backend found one hit outside the schema and the edit API, and that hit is a field NAME in
`ontology_projection`'s passthrough list. `OntologyMapping` does not carry the field at all. So
every one of those patterns was authored, shown on the configuration screen, saved, versioned — and
consulted on no run. That is the exact failure this codebase keeps finding, at its largest scale
yet, and it is why the answer to "configure these eight concepts" had to start here rather than with
more configuration.

WHAT THIS IS NOT. It is not a port of those five services. They enumerated concepts: a function per
target, with the note titles and captions written into Python. This reads the DECLARATION, so it
serves the thirteen items that exist and the seventieth nobody has authored yet, with no code change
— which is the whole point of moving the rulebook into configuration.

HOW A FIGURE IS CHOSEN, and the distinction the configuration already makes:

* ``rollup: "sum"``   — the rows are COMPONENTS. Add them. A note that splits depreciation by
  function prints four rows and all four belong on the line.
* ``rollup: "alternatives"`` — the rows are ALTERNATIVE SOURCES FOR ONE FIGURE, never addends. The
  twelve sub-items under ``is_pl__deprec_and_impairment_oper_exp`` are exactly this: twelve places
  the same depreciation charge might be disclosed. Adding them would multiply one cost by twelve.
  The FIRST match in declared ``order`` wins, and the others are recorded in the trail as the
  alternatives that were available but not taken.
* ``rollup: "none"``  — the parenthood carries no arithmetic. The child is filled and left alone.

THE PARENT IS NEVER SILENTLY OVERWRITTEN. A parent the filing PRINTS is the filing's own statement
of the figure, and a note-derived one is an inference from a breakdown; replacing the first with the
second would discard the more authoritative number. So a parent that already carries a figure keeps
it, and the note-derived value is recorded against the child with a flag saying the two coexist.

EVERY FIGURE CARRIES ITS TRAIL, written through ``services.derivation`` — the note number, the row
caption, the page, and the arithmetic — because a figure assembled out of four note rows is
unreviewable otherwise, and the statement inspector already renders that structure with
click-to-source.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.services import derivation
# One split for every side of every comparison — see `line_item_notes`.
from app.services.note_context import subject_tokens

# A pattern an author mistyped must not take the run down, and must not silently match nothing
# either. Both outcomes are reported by `fill`, which returns the refusals alongside the fills.
_FLAGS = re.IGNORECASE


def _compiled(patterns) -> list[tuple[str, re.Pattern | None]]:
    out: list[tuple[str, re.Pattern | None]] = []
    for p in patterns or ():
        try:
            out.append((p, re.compile(p, _FLAGS)))
        except re.error:
            out.append((p, None))
    return out


def _matches_any(text: str, compiled: list[tuple[str, re.Pattern | None]]) -> str | None:
    """The first pattern that matches, or None. Returns the PATTERN so the trail can name it."""
    for raw, rx in compiled:
        if rx is not None and rx.search(text or ""):
            return raw
    return None


def _num(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _basis_of(value) -> str:
    """The basis as the string the trail is keyed on — `Basis` is an enum, and `str()` on it gives
    "Basis.CONSOLIDATED", which keys a slot nothing can look up (see `assemble_components._basis`)."""
    b = getattr(value, "basis", "")
    return str(getattr(b, "value", b) or "")


class NoteRowHit:
    """One note row a line item's declaration selected, with everything the trail needs."""

    __slots__ = ("key", "note_number", "note_title", "caption", "matched_by", "value", "amount",
                 "basis", "period")

    def __init__(self, *, key, note_number, note_title, caption, matched_by, value, amount,
                 basis, period):
        self.key = key
        self.note_number = note_number
        self.note_title = note_title
        self.caption = caption
        self.matched_by = matched_by
        self.value = value
        self.amount = amount
        self.basis = basis
        self.period = period


def select_rows(item, notes, periods: set[str] | None = None) -> list[NoteRowHit]:
    """The note rows THIS item's `note_source` declares, across every note whose title matches.

    Three gates, in the order the declaration reads: the note's title must match, the row's caption
    must match something in `row_caption_any`, and it must match nothing in `row_caption_none`. The
    veto is applied last and unconditionally — its whole purpose is to remove a row a counting
    pattern already claimed, which is how "depreciation" stops picking up "accumulated depreciation"
    and the movement rows of a fixed-asset table.
    """
    src = getattr(item, "note_source", None)
    if src is None:
        return []
    titles = _compiled(getattr(src, "note_title_any", None))
    counts = _compiled(getattr(src, "row_caption_any", None))
    vetoes = _compiled(getattr(src, "row_caption_none", None))
    if not titles or not counts:
        return []

    hits: list[NoteRowHit] = []
    for table in notes or ():
        title = getattr(table, "title", "") or ""
        # The note NUMBER is offered to the title patterns too, because a filing whose note headings
        # were captured without their text still identifies the note by its number.
        if not (_matches_any(title, titles) or _matches_any(str(getattr(table, "note_number", "")),
                                                            titles)):
            continue
        for row in getattr(table, "items", None) or ():
            caption = getattr(row, "raw_label", "") or ""
            matched = _matches_any(caption, counts)
            if not matched:
                continue
            if _matches_any(caption, vetoes):
                continue
            for value in (getattr(row, "values", None) or {}).values():
                # A MATRIX COLUMN IS NOT A PERIOD. `ExtractedValue.column_index` is set only for a
                # fact printed in a NAMED COMPONENT column — an industry segment, a class of
                # equity — and those columns are an axis of decomposition, not of time.
                #
                # Measured on laisun.pdf before this guard: the revenue segment note gave
                # `is_pl__sales_revenues` twelve figures keyed `col2` … `col11` alongside
                # `current`/`prior`, and the value that landed in the CURRENT slot was one
                # segment's revenue (2,609,259) rather than the total the face prints
                # (4,995,768). A per-segment figure published as the year's revenue, on a line
                # that then reconciles against nothing.
                if getattr(value, "column_index", None) is not None:
                    continue
                # AND THE COLUMN MUST BE ONE THE STATEMENTS USE. A note prints columns that are not
                # periods at all: measured on suncreate.pdf, the 营业收入和营业成本 note prints revenue
                # and COST side by side, and the cost column arrived as a slot named
                # `current:cost` carrying 2,239,996,631.60 onto the REVENUE line. `periods` is the
                # set the face declares — see the stage, which derives it from the document.
                label = str(getattr(value, "period_label", "") or "")
                if periods is not None and label not in periods:
                    continue
                amount = _num(getattr(value, "value", None)
                              if getattr(value, "value", None) is not None
                              else getattr(value, "value_raw", None))
                if amount is None:
                    continue
                hits.append(NoteRowHit(
                    key=item.key, note_number=str(getattr(table, "note_number", "")),
                    note_title=title, caption=caption, matched_by=matched,
                    value=value, amount=amount,
                    basis=_basis_of(value),
                    period=str(getattr(value, "period_label", "") or "")))
    return hits



# ── a figure the filing states only in PROSE ─────────────────────────────────────────────────────
#
# THE CASE THIS EXISTS FOR, measured on the reference HK filing. The operating-expense share of the
# depreciation charge is disclosed in a footnote and NOWHERE ELSE: "Depreciation charges of
# approximately HK$529,841,000 (2024: HK$665,553,000) are included in 'other operating expenses' on
# the face of the consolidated income statement." 529841 appears in no extracted row anywhere in the
# document, so no row-caption pattern and no row-term can reach it, and the line stays empty however
# plainly the filing states it.
#
# A 4-DIGIT YEAR IS NOT AN AMOUNT. `(2024: HK$665,553,000)` carries both the prior figure and the
# year that labels it, and a naive number scan reads 2024 as the first amount in the sentence.
_PROSE_AMOUNT = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d,])")
# The parenthetical that labels a comparative: `(2024: ... 665,553,000)`.
_PROSE_PRIOR = re.compile(r"[(（]\s*(?:19|20)\d{2}\s*[:：][^)）]*?"
                          r"((?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?)")
# A sentence shorter than this is a fragment — a footnote marker, a page header, a stray figure —
# and matching one says nothing. Same floor `note_context` uses when it splits prose into units.
_MIN_SENTENCE = 26


class ProseHit:
    """A figure a note states in a sentence rather than in a row."""

    __slots__ = ("key", "note_number", "note_title", "sentence", "matched_by", "amount", "period")

    def __init__(self, *, key, note_number, note_title, sentence, matched_by, amount, period):
        self.key = key
        self.note_number = note_number
        self.note_title = note_title
        self.sentence = sentence
        self.matched_by = matched_by
        self.amount = amount
        self.period = period


def _sentences(text: str) -> list[str]:
    out = []
    for raw in re.split(r"(?<=[.。;；])\s+|[\r\n]+", text or ""):
        raw = " ".join(raw.split()).strip()
        if len(raw) >= _MIN_SENTENCE:
            out.append(raw)
    return out


def select_prose(item, notes) -> list[ProseHit]:
    r"""The figures this item's PROSE PATTERNS find in a matched note's prose.

    THE PATTERNS, NOT THE TERMS, and that distinction is the whole correctness of this function.
    A `row_caption_any` pattern is typically a CONJUNCTION with a proximity bound:

        (?:depreciation|amortisation).{0,40}(?:administrative(?:\s+expenses?)?|selling…|operating…)

    — "a depreciation word within forty characters of an expense-function word". The derived
    `row_terms` are that pattern split on `|`, which turns the conjunction into a DISJUNCTION:
    `depreciation` OR `operating expenses`. Measured, that cost real figures: a term-based version
    of this function matched any sentence merely mentioning operating expenses and replaced the
    reference filing's depreciation charge of 587,417 with 36,966,000, and invented two more
    figures on lines that should have stayed empty. The proximity and the two-part structure are
    exactly what made the pattern specific, so prose is matched with the pattern intact.

    THE TERMS ARE STILL NOT USELESS — they floor the model's answers
    (`line_item_notes.caption_agrees_with_row_terms`), where a disjunction is sound because the
    question there is only whether a caption is about the same subject at all. They cannot decide
    what COUNTS, and that has now been measured three times: as a row acceptance rule on one shared
    token (4 of 8 figures wrong), on whole-term containment (6 of 8), and here on prose.

    ALSO REQUIRED, because a pattern match alone is not a figure:
      * The sentence must carry a GROUPED amount — thousands separators — which keeps years, note
        numbers and bare percentages out. A 4-digit year is explicitly not an amount.
      * `row_caption_none` vetoes, as it does for rows.

    A FALLBACK, NOT AN ALTERNATIVE. The caller consults this only for an item whose ROW route found
    nothing: a row is the filing's own tabulation and a sentence is a narrative restatement, so
    prose competing with rows would sometimes replace the first with the second.
    """
    src = getattr(item, "note_source", None)
    if src is None:
        return []
    titles = _compiled(getattr(src, "note_title_any", None))
    # `prose_any` AND NOT `row_caption_any` — see the field's own comment for why neither the row
    # patterns nor the row terms can serve here. An empty `prose_any` means this line has no prose
    # route, and returning nothing is the right answer: a line nobody has authored for prose should
    # produce no prose figure rather than a guess.
    counts = _compiled(getattr(src, "prose_any", None))
    vetoes = _compiled(getattr(src, "row_caption_none", None))
    if not titles or not counts:
        return []

    hits: list[ProseHit] = []
    for table in notes or ():
        title = getattr(table, "title", "") or ""
        number = str(getattr(table, "note_number", "") or "")
        if not (_matches_any(title, titles) or _matches_any(number, titles)):
            continue
        for sentence in _sentences(getattr(table, "source_text", "") or ""):
            matched = _matches_any(sentence, counts)
            if matched is None:
                continue
            if _matches_any(sentence, vetoes):
                continue
            amounts = _PROSE_AMOUNT.findall(sentence)
            if not amounts:
                continue
            prior = _PROSE_PRIOR.search(sentence)
            prior_text = prior.group(1) if prior else None
            # The first grouped amount is the current one; the comparative is the one the
            # parenthetical year labels. Where they are the same string the sentence states a
            # single figure, and reading it as both periods would invent a comparative.
            current = next((a for a in amounts if a != prior_text), None)
            for period, text in (("current", current), ("prior", prior_text)):
                value = _num((text or "").replace(",", ""))
                if value is None:
                    continue
                hits.append(ProseHit(
                    key=item.key, note_number=number, note_title=title,
                    sentence=sentence, matched_by=matched, amount=value, period=period))
    return hits

def _trail_input(hit: NoteRowHit, *, counted: bool) -> dict:
    return {
        # WHERE IT WAS PRINTED, off the note row itself.
        "label": hit.caption,
        "note": hit.note_number,
        "value": str(hit.amount),
        "provenance": derivation._json_safe_provenance(getattr(hit.value, "provenance", None)),
        # WHY IT WAS SELECTED — the author's own pattern, so a wrong selection is traceable to the
        # line of configuration that made it rather than to "the engine".
        "excerpt": f"note '{hit.note_title}' row matched /{hit.matched_by}/",
        "deducted": False,
        "counted": counted,
    }


def resolve(hits: list[NoteRowHit], rollup: str) -> dict[tuple[str, str], tuple[Decimal, list[dict]]]:
    """Per (basis, period): the figure this item's rows come to, and the trail that explains it.

    `sum` adds them. `alternatives` takes the FIRST and records the rest as available-but-not-taken,
    because those rows are twelve disclosures of one charge rather than twelve charges — summing
    them would multiply a cost by the number of places the filing happened to mention it.
    """
    by_slot: dict[tuple[str, str], list[NoteRowHit]] = {}
    for h in hits:
        by_slot.setdefault((h.basis, h.period), []).append(h)

    out: dict[tuple[str, str], tuple[Decimal, list[dict]]] = {}
    for slot, rows in by_slot.items():
        if rollup == "alternatives":
            taken, rest = rows[0], rows[1:]
            inputs = [_trail_input(taken, counted=True)]
            inputs += [_trail_input(r, counted=False) for r in rest]
            out[slot] = (taken.amount, inputs)
        else:
            total = sum((r.amount for r in rows), Decimal(0))
            out[slot] = (total, [_trail_input(r, counted=True) for r in rows])
    return out


def method_of(rollup: str, count: int) -> str:
    """How a figure was reached, as the audit trail and the export name it.

    THE LINE-ITEM REQUEST GETS ITS OWN PREFIX, not `note_sourced:`. The rows behind it were named
    by a model and RESOLVED here (`resolve_sources`), not selected by a declared pattern — so a
    reader asking "where did this number come from" must be able to tell the two apart. Both read
    the same notes and both take the figure off a printed row, which is exactly why the trail has
    to say which route ran rather than leaving them indistinguishable.
    """
    if rollup == "line_item_llm":
        return f"line_item_llm:{count}_cited_row{'s' if count != 1 else ''}"
    if rollup == "alternatives":
        return f"note_sourced:first_of_{count}_alternatives"
    return f"note_sourced:sum_of_{count}_rows"


def trail(*, rollup: str, item_label: str, amount: Decimal, inputs: list[dict]) -> dict:
    # A CITED ROW IS ALWAYS COUNTED. `counted` marks which of a declared selection's candidate rows
    # were actually added (`_trail_input`), a distinction the row route needs because a
    # `alternatives` rollup offers several and takes one. A citation carries no such candidacy: the
    # model named these rows and `figures_of` already decided how they combine, so every input here
    # is part of the answer and a formula that listed none of them would read as an empty trail.
    if rollup == "line_item_llm":
        inputs = [{**i, "counted": True} for i in inputs]
    counted = [i for i in inputs if i.get("counted")]
    formula = (" + ".join(i["label"] for i in counted) if rollup != "alternatives"
               else (counted[0]["label"] if counted else None))
    return derivation.build(
        method=method_of(rollup, len(inputs)),
        formula=formula, inputs=inputs, result=amount,
        flags=[f"note_rows:{len(counted)}"]
             + ([f"alternatives_not_taken:{len(inputs) - len(counted)}"]
                if len(inputs) > len(counted) else []))


def bad_patterns(item) -> list[str]:
    """Patterns on this item that do not compile, named so a refusal points at the author's line.

    Reported rather than raised: one mistyped regex on one sub-line item must not take down a run
    over a 300-page filing, and must not silently match nothing either — which is what a bare
    try/except around the whole selection would do.
    """
    src = getattr(item, "note_source", None)
    if src is None:
        return []
    out: list[str] = []
    for field in ("note_title_any", "row_caption_any", "row_caption_none"):
        for raw, rx in _compiled(getattr(src, field, None)):
            if rx is None:
                out.append(f"{item.key}.note_source.{field}: /{raw}/")
    return out


# ── resolving what the MODEL cited ────────────────────────────────────────────────────────────

def resolve_sources(sources, notes) -> tuple[list[dict], list[dict]]:
    """Match each citation the model gave against the extracted rows. Returns (resolved, unresolved).

    WHY THIS EXISTS RATHER THAN TRUSTING THE CITATION. The candidates offered to the model are
    suggestions, and it may answer past them — which is what lets a caption reach the concept its
    section did not predict. The price is that such an answer is only worth the printed row behind
    it, so the model names the row and THIS resolves it: the page, the figure and the note all come
    off the extracted row, never from the model, which was given no page index and no bbox.

    MATCHING IS DELIBERATELY FORGIVING ON PUNCTUATION AND STRICT ON WORDS. A filing prints
    "Depreciation of property, plant and equipment^" and a model quoting it may drop the footnote
    marker or the comma; neither changes which row is meant. A paraphrase does, so the words
    themselves must be there — containment either way, after the punctuation is stripped.

    A CITATION THAT RESOLVES TO NOTHING IS RETURNED AS UNRESOLVED, not dropped and not believed.
    The caller keeps the mapping and flags it, because "the model cited a row we cannot find" and
    "the model cited nothing" are different failures and only one of them is the model's.
    """
    import re as _re

    def norm(text: str) -> str:
        return _re.sub(r"[^0-9a-z一-鿿]+", "", (text or "").lower())

    rows: list[tuple[str, str, object, object]] = []
    for table in notes or ():
        number = str(getattr(table, "note_number", "") or "")
        for row in getattr(table, "items", None) or ():
            caption = getattr(row, "raw_label", "") or ""
            if caption:
                rows.append((number, caption, row, table))

    resolved: list[dict] = []
    unresolved: list[dict] = []
    for ref in sources or ():
        want_note = str(getattr(ref, "note", "") or "").strip()
        want_cap = norm(getattr(ref, "caption", ""))
        quote = (getattr(ref, "quote", "") or "").strip()
        hit = None
        if want_cap:
            for number, caption, row, table in rows:
                if want_note and want_note not in number and number not in want_note:
                    continue
                got = norm(caption)
                if got and (want_cap in got or got in want_cap):
                    hit = (number, caption, row, table)
                    break
        if hit is None:
            # THE PROSE CASE. A figure stated in a footnote belongs to no row, so there is nothing
            # to match a caption against. Where the model also gave the AMOUNT, this is the one
            # place it is allowed to — and the amount is VERIFIED against the note's own text
            # before it is accepted, which is what makes it a located figure rather than a
            # supplied one.
            stated = str(getattr(ref, "amount", "") or "").strip()
            table = _note_by_number(notes, want_note)
            if stated and table is not None:
                found = _amount_in_text(stated, (getattr(table, "source_text", "") or "") + " " + quote)
                if found is not None:
                    resolved.append({
                        "note": want_note, "title": getattr(table, "title", "") or "",
                        "caption": getattr(ref, "caption", ""),
                        # Keyed `prose` rather than a period: the sentence says which line the
                        # figure belongs to, not which column, and inventing a period here would
                        # put a figure in a year the filing never assigned it to. The caller
                        # decides the column from the row it is filling.
                        "figures": {"prose": str(found)},
                        "provenance": _prose_provenance(table),
                        "quote": quote, "prose": True})
                    continue
                unresolved.append({
                    "note": want_note, "caption": getattr(ref, "caption", ""), "quote": quote,
                    "amount": stated,
                    "why": (f"the amount {stated} does not appear in note {want_note}'s text — a "
                            f"figure the model stated rather than located is refused")})
                continue
            unresolved.append({"note": want_note,
                               "caption": getattr(ref, "caption", ""),
                               "quote": quote,
                               "why": ("no extracted row in that note matches the caption — it may "
                                       "be stated in prose, which carries no row")})
            continue
        number, caption, row, table = hit
        figures = {}
        prov = None
        for ev in (getattr(row, "values", None) or {}).values():
            if getattr(ev, "column_index", None) is not None:
                continue
            if getattr(ev, "value", None) is None:
                continue
            figures[str(getattr(ev, "period_label", "") or "?")] = str(ev.value)
            if prov is None:
                prov = derivation._json_safe_provenance(getattr(ev, "provenance", None))
        resolved.append({"note": number, "title": getattr(table, "title", "") or "",
                         "caption": caption, "figures": figures, "provenance": prov,
                         "quote": quote})
    return resolved, unresolved


def _note_by_number(notes, number: str):
    """The note a citation names. Matched loosely because a PRC note number is chapter-qualified
    ("七、9") and a model may cite either half."""
    want = (number or "").strip()
    if not want:
        return None
    for table in notes or ():
        got = str(getattr(table, "note_number", "") or "")
        if got and (want in got or got in want):
            return table
    return None


def _prose_provenance(table) -> dict | None:
    """A page for a prose figure, off the NOTE rather than off a row.

    There is no row, so there is no `Provenance` to copy — but `NotesTable.source_pages` records
    which pages the note was parsed from, and the first of them is where the sentence is. Without
    this the figure would reach the screen with no click-to-source at all, which for a number the
    model located rather than read is the last thing a reviewer should be denied.
    """
    pages = list(getattr(table, "source_pages", None) or ())
    if not pages:
        return None
    return {"page_index": pages[0], "source": "note_prose"}


def _amount_in_text(stated: str, text: str) -> Decimal | None:
    """The stated amount, but only if that number really is in the text. Otherwise None.

    THE WHOLE SAFETY PROPERTY OF A PROSE FIGURE. The model is permitted to give an amount here and
    nowhere else, and what makes that safe is that the number must be demonstrably printed: the
    comparison is on DIGITS, so "HK$529,841,000", "529,841,000" and "529841000" are the same
    number, while 529,842,000 is not there and is refused.

    Returns the parsed figure rather than a boolean so the caller stores what was verified and not
    what was typed.
    """
    import re as _re

    digits = _re.sub(r"[^0-9]", "", stated or "")
    if not digits:
        return None
    haystack = _re.sub(r"[,\s ]", "", text or "")
    if digits not in haystack:
        return None
    try:
        return Decimal(digits) if "." not in stated else Decimal(
            _re.sub(r"[^0-9.]", "", stated))
    except (InvalidOperation, ValueError):
        return None
