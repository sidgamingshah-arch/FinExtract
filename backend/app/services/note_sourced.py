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

from app.services import derivation, prose_grammar
from app.services.note_sections import open_to as _open_to
# One split for every side of every comparison — see `line_item_notes`.
from app.services.note_context import matches_title, subject_tokens

# A pattern an author mistyped must not take the run down, and must not silently match nothing
# either. Both outcomes are reported by `fill`, which returns the refusals alongside the fills.
_FLAGS = re.IGNORECASE

# THE LABEL `row_reconstruct._column_periods` GIVES A COLUMN WHOSE PERIOD IT COULD NOT READ —
# "col2", "col3", … past the first two positions. Bare, and only bare: "current:cost" names a
# measure of a known period and "current_col3" a kept restatement of one, and both are real slots.
_POSITIONAL_SLOT = re.compile(r"^col\d+$", re.IGNORECASE)


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


def _matches_title_any(title: str, compiled: list[tuple[str, re.Pattern | None]]) -> str | None:
    """`_matches_any` for a note HEADING, which arrives carrying an enumerator.

    A heading is matched as extracted and with its leading enumerator removed
    (`note_context.title_variants`), because 44% of the corpus's headings begin with a bare "、"
    and every anchored `note_title_any` pattern fails those. Row CAPTIONS are matched by
    `_matches_any` unchanged: a caption carries no enumerator, and stripping a leading character
    from one would be loosening a pattern for no reason.
    """
    for raw, rx in compiled:
        if rx is not None and matches_title(rx, title or ""):
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


def _table_basis(table, value) -> str:
    """The basis a note row's figure belongs to — the NOTE'S, where the note declares one.

    A CSRC filing repeats every material balance for the parent company alone, under the chapter
    heading 母公司财务报表(主要)项目注释, and `notes_extract` already marks those tables
    `basis=STANDALONE` from that heading. Its comment states the intent exactly: "the note's
    ``basis`` is set from it, so a consumer computing a consolidated figure can decline a
    company-only note instead of summing across bases." This reader was not that consumer — it took
    the basis off the row's VALUE, which is read from the note's own columns and says nothing about
    whose statements the note explains — so the producer honoured the distinction and nothing
    downstream did.

    MEASURED ON 1223214527, a Shenzhen-listed filing whose revenue note is printed twice. Note
    七、35 (the group's, p179) reads 411,974,409.31 and note 十八、4 (the parent's, p206) reads
    408,721,552.34, both captioned 主营业务, and `sub__revenue_note_principal_revenue` filed both
    under `consolidated`. Which one reached the published figure was decided by nothing better than
    which pages the classifier happened to read as notes: it published the PARENT's 408,721,552.34
    as the group's revenue, and with page detection widened it published their sum,
    820,695,961.65 — against the 42,223.93 万元 (422,239,300, principal plus other business) the
    filing states in its own MD&A.

    So a company-only note's rows go to STANDALONE and stop contesting the consolidated slot. The
    row's own basis is kept wherever the note declares none, which is every English filing and every
    chapter that is not the parent company's — the note-level signal narrows, it never invents.
    """
    declared = getattr(table, "basis", None)
    if declared is None:
        return _basis_of(value)
    return str(getattr(declared, "value", declared) or "") or _basis_of(value)


def select_rows(item, notes, periods: set[str] | None = None,
                note_sections: dict[str, set[str]] | None = None) -> list[NoteRowHit]:
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
        #
        # AND THE CHAPTER HEADING, because a mainland filing numbers three levels deep and only the
        # innermost caption reaches `title`: chapter 十二 "关联方及关联交易" yields notes titled
        # "、本企业的母公司情况：". An author describing where a figure lives writes the chapter, so
        # a pattern naming it matched nothing — `sub__rp_find_3`, the spec's reading of the
        # related-party note, produced no figure on any filing for exactly this reason. OFFERED AS
        # A THIRD ALTERNATIVE rather than concatenated, so an anchored pattern (`^\s*其他应收款`,
        # which is how almost every authored pattern is written) still matches the note's own title
        # from its first character.
        if not (_matches_title_any(title, titles)
                or _matches_any(str(getattr(table, "note_number", "")), titles)
                or _matches_title_any(str(getattr(table, "chapter_title", "") or ""), titles)):
            continue
        # THE LINE'S SECTION NARROWS WHICH NOTES IT MAY READ. `section_scope` says where a line
        # lives; until now the note path read it nowhere, so a line declaring `['bs_ca']` still
        # considered every note in the filing. `note_sections` resolves a note's section through
        # the note-to-face links, and `open_to` closes the door ONLY when the note resolves to
        # exactly one section and this line names a different one — a note of unknown or of
        # several sections stays open for everything, which is the same convention an empty
        # `section_scope` already has. See `services.note_sections` for why: the signal reaches
        # about 30% of notes, and narrowing on an absent signal would starve the context rather
        # than sharpen it.
        if note_sections is not None and not _open_to(
                item, str(getattr(table, "note_number", "") or ""), note_sections):
            continue
        # DOES THIS NOTE STATE ITS PERIODS ON THE BLOCKS? If any row carries a block period, the
        # note is a movement table and its COLUMNS are asset classes — so no row in it may be read
        # by column label, whether or not that row got a hint of its own.
        #
        # WHY THE WHOLE TABLE AND NOT JUST THE HINTED ROW. Measured on
        # 8ad0c02c-46bb-4e21-9962-a8d6da4ecf81.pdf once the gate was widened to the charge captions:
        # the movement rows that got no hint fell through to the column path, and because that
        # document's face carries many columns its `periods` set contains "col3", "col4" and "col6"
        # — so class columns arrived as periods and the line published 366,943,014.10 beside a prior
        # of 118,627,077.67 against a current of 6,225,356.67. The `column_index` guard cannot catch
        # them: these rows are not matrix rows, so they have no column index. The table's own
        # evidence that its periods live on the ROW axis is what rules them out.
        block_periods = any(str(getattr(r, "period_hint", "") or "")
                            for r in (getattr(table, "items", None) or ()))
        for row in getattr(table, "items", None) or ():
            caption = getattr(row, "raw_label", "") or ""
            matched = _matches_any(caption, counts)
            if not matched:
                continue
            if _matches_any(caption, vetoes):
                continue
            # A MOVEMENT ROW CARRIES ITS PERIOD ON THE BLOCK, NOT ON THE COLUMN. An asset note's
            # columns are asset CLASSES and its comparative year is a second block of rows, so
            # neither of the two guards below can read such a row: the class columns are exactly
            # what `column_index` exists to refuse, and the positional labels a classed column
            # falls back to ("col2") are exactly what `periods` exists to refuse. Both refusals
            # are right about the column and wrong about the row.
            #
            # `notes_extract` resolves the two separately — which period the BLOCK is for, and
            # which column TOTALS the row — and where it has both, they are the answer and the
            # column-level guards do not apply. One figure per row, in the slot the note states.
            #
            # MEASURED, on 2025041600195.pdf: right-of-use note 16 prints the charge once per year
            # and only the first column kept a period label, so this loop took that column from
            # BOTH blocks and the line published 16,847 + 11,307 = 28,154 — a quantity that is not
            # in the filing. It now publishes 77,707 current and 66,870 prior, which is what the
            # note's total column states. Property, plant and equipment note 15 published nothing
            # at all, because every value on a matrix row carries a `column_index`; it now
            # publishes 566,457 and 498,784.
            hint = str(getattr(row, "period_hint", "") or "")
            total_slot = str(getattr(row, "total_slot", "") or "")
            if hint and total_slot:
                for value in (getattr(row, "values", None) or {}).values():
                    if str(getattr(value, "period_label", "") or "") != total_slot:
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
                        basis=_table_basis(table, value), period=hint))
                continue

            # A movement table's rows are readable ONLY through the block period. One that did not
            # resolve — no anchor governs it, or no column totals it — contributes nothing, because
            # the only thing left to read it by is a column label this note does not use for time.
            if block_periods:
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
                # A POSITIONAL FALLBACK IS NOT A PERIOD. `col2`, `col3` … is what
                # `row_reconstruct._column_periods` emits when it could read NEITHER a heading date
                # nor a PRC period caption for a column — the label means "which period this is was
                # not determined", so publishing a figure under it asserts a period the parser
                # explicitly declined to name.
                #
                # THE `periods` FILTER BELOW CANNOT CATCH THEM, which is why this is separate.
                # `periods` is the set the FACE declares, and a face whose own columns were
                # unreadable declares the same fallbacks — measured on
                # 8ad0c02c-46bb-4e21-9962-a8d6da4ecf81.pdf, whose set contains "col3", "col4" and
                # "col6". So the note's class columns matched the face's unresolved ones and a
                # depreciation line published 366,943,014.10 as a period figure. Reachable only
                # once the row gate was widened to the charge captions, which is why nothing had
                # found it before.
                #
                # NOT a `periods` membership question and not a movement-table question: no line
                # should ever take a figure from a column whose period is unknown.
                if _POSITIONAL_SLOT.match(label):
                    continue
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
                    basis=_table_basis(table, value),
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

    __slots__ = ("key", "note_number", "note_title", "sentence", "matched_by", "amount", "period",
                 "provenance")

    def __init__(self, *, key, note_number, note_title, sentence, matched_by, amount, period,
                 provenance=None):
        self.key = key
        self.note_number = note_number
        self.note_title = note_title
        self.sentence = sentence
        self.matched_by = matched_by
        self.amount = amount
        self.period = period
        # WHERE THE SENTENCE IS, so a prose figure can be clicked through to the page like any
        # other citation. `_prose_provenance` has always existed for this and the ROW route has
        # always carried it; the deterministic prose trail did not, so `sub__pbt_oper_exp_
        # depreciation` — a line whose ONLY source on the reference filing is a footnote — reached
        # the inspector as an amount with no page. The end of a traceback is the page, and for
        # exactly the figures that are hardest to find by eye there was no end.
        self.provenance = provenance


def _sentences(text: str) -> list[str]:
    out = []
    for raw in re.split(r"(?<=[.。;；])\s+|[\r\n]+", text or ""):
        raw = " ".join(raw.split()).strip()
        if len(raw) >= _MIN_SENTENCE:
            out.append(raw)
    return out


def select_prose(item, notes, grammar=None) -> list[ProseHit]:
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

    `grammar` IS THE SET'S `prose_grammar`, passed in rather than reached for, because it is a
    property of the SET and this function is given one item. Absent, only the raw `prose_any`
    patterns are consulted — which is what every caller that has no set does.
    """
    src = getattr(item, "note_source", None)
    if src is None:
        return []
    titles = _compiled(getattr(src, "note_title_any", None))
    # WHAT COUNTS AS A PROSE SENTENCE FOR THIS LINE — the patterns generated from the plain phrases
    # the author wrote, plus any raw ones authored through the escape hatch.
    #
    # `prose_*` AND NOT `row_caption_any` — see the field's own comment for why neither the row
    # patterns nor the row terms can serve here. Nothing authored means this line has no prose
    # route, and returning nothing is the right answer: a line nobody has authored for prose should
    # produce no prose figure rather than a guess.
    counts = _compiled(list(getattr(src, "prose_any", None) or ())
                       + prose_grammar.compile_for(src, grammar))
    vetoes = _compiled(getattr(src, "row_caption_none", None))
    if not titles or not counts:
        return []

    hits: list[ProseHit] = []
    for table in notes or ():
        title = getattr(table, "title", "") or ""
        number = str(getattr(table, "note_number", "") or "")
        # The PROSE route reaches its note the same way the row route does, so a heading whose
        # enumerator defeated the pattern withheld the sentence as well as the table.
        if not (_matches_title_any(title, titles) or _matches_any(number, titles)):
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
                    sentence=sentence, matched_by=matched, amount=value, period=period,
                    provenance=_prose_provenance(table)))
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
    # `prose_any` IS IN THIS LIST AND WAS NOT. It is the raw escape hatch now that the prose route
    # is authored in plain phrases, so it is the one prose field that can still carry a broken
    # pattern — and a broken one there is the same silent hole as anywhere else: the sentence simply
    # stops matching and the line stays empty. The GENERATED patterns need no check; a plain phrase
    # cannot fail to compile, which is the point of generating them.
    for field in ("note_title_any", "row_caption_any", "row_caption_none", "prose_any"):
        for raw, rx in _compiled(getattr(src, field, None)):
            if rx is None:
                out.append(f"{item.key}.note_source.{field}: /{raw}/")
    return out


# ── resolving what the MODEL cited ────────────────────────────────────────────────────────────

def resolve_sources(sources, notes, face=None, *, allow_face: bool = True,
                    pages=None, allow_pages: bool = False) -> tuple[list[dict], list[dict]]:
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

    `allow_face=False` REFUSES A STATEMENT CITATION OUTRIGHT, for a line whose route says its figure
    is printed in a note (`services.line_item_routes.may_read_face`). Refused HERE and named as
    itself rather than by handing the arm an empty face index: an empty index reports "no printed
    row on balance_sheet matches that caption", which tells an author the caption was wrong when
    what was wrong was the place. A run has both states — a filing with no classified face pages
    really does have no face rows — so they must be distinguishable in the flag a reviewer reads.

    `pages` / `allow_pages` ARE THE THIRD INDEX, and the narrowest. `services.face_context.
    other_page_index` holds the rows of pages that are neither a statement nor a note, which exist
    at all only because a line declares `route: anywhere` and the extractor was widened for it
    (`services.pdf_extract`). Refused by DEFAULT, because every other route's author said where the
    figure is printed and a citation pointing outside that is an answer to a question nobody asked.
    """
    import re as _re

    # LOCAL, because `services.mapping` imports THIS module at module level — a module-level
    # import of the fold is a circular one. `mapping.normalize_statement` is still the single
    # owner of the equity statement's two spellings; this is only where it is reached from.
    from app.services.mapping import normalize_statement

    def norm(text: str) -> str:
        return _re.sub(r"[^0-9a-z一-鿿]+", "", (text or "").lower())

    rows: list[tuple[str, str, object, object]] = []
    for table in notes or ():
        number = str(getattr(table, "note_number", "") or "")
        for row in getattr(table, "items", None) or ():
            caption = getattr(row, "raw_label", "") or ""
            if caption:
                rows.append((number, caption, row, table))
    face_rows = list(face or ())

    resolved: list[dict] = []
    unresolved: list[dict] = []
    # THE CITATION'S OWN POSITION TRAVELS WITH IT, and it has to.
    #
    # `LineItemAnswer.signs` is POSITIONAL — `signs[i]` belongs to `sources[i]` — and the caller
    # sums the entries it gets back. Returning two unordered lists meant a caller that dropped or
    # reordered any entry silently re-paired the remaining figures with the wrong signs: a
    # declared deduction of 120,000 published as +120,000. `at` is what lets every consumer pair a
    # figure with the sign the model actually gave it, whatever it does with the rest.
    for at, ref in enumerate(sources or ()):
        want_note = str(getattr(ref, "note", "") or "").strip()
        want_cap = norm(getattr(ref, "caption", ""))
        quote = (getattr(ref, "quote", "") or "").strip()

        # A CITATION THAT NAMES A PAGE AND NOTHING ELSE IS LOOKED UP OFF THE STATEMENTS AND THE
        # NOTES ALTOGETHER — the `anywhere` route's own index. Taken FIRST and returning either
        # way, the same rule the statement arm follows and for the same reason: the three indexes
        # are alternatives, and falling through from a page citation to a note would publish a
        # note's figure for a citation that said page 12.
        want_page = getattr(ref, "page", None)
        # FOLDED TO THE CLASSIFIER'S SPELLING. A line gated to the statement of changes in equity
        # carries `equity_changes` (the `StatementType` value) and the face index is keyed
        # `changes_in_equity` (the page's verdict), so an unfolded comparison refused every equity
        # citation and reported it as "no printed row on equity_changes matches that caption" —
        # a diagnostic that sends an author to fix a caption when the spelling was the problem.
        want_stmt = normalize_statement(
            str(getattr(ref, "statement", "") or "").strip())
        if want_page is not None and not want_note and not want_stmt:
            if not allow_pages:
                unresolved.append({
                    "at": at, "note": "", "statement": "", "page": int(want_page),
                    "caption": getattr(ref, "caption", ""), "quote": quote,
                    "why": ("this line is not read from pages outside the statements and the "
                            "notes — only a line whose route is `anywhere` may cite one")})
                continue
            on_page = next(((pg, cap, row) for pg, cap, row in (pages or ())
                            if int(pg) == int(want_page) and want_cap
                            and ((got := norm(cap)) and (want_cap in got or got in want_cap))),
                           None)
            if on_page is None:
                elsewhere = sorted({int(pg) for pg, cap, _r in (pages or ())
                                    if want_cap and (g := norm(cap))
                                    and (want_cap in g or g in want_cap)
                                    and int(pg) != int(want_page)})
                unresolved.append({
                    "at": at, "note": "", "statement": "", "page": int(want_page),
                    "caption": getattr(ref, "caption", ""), "quote": quote,
                    "why": (f"no row on page {int(want_page)} matches that caption"
                            + (f" — it is on page {', '.join(str(n) for n in elsewhere)}"
                               if elsewhere else ""))})
                continue
            pg, caption, row = on_page
            figures, prov = {}, None
            for ev in (getattr(row, "values", None) or {}).values():
                if getattr(ev, "column_index", None) is not None:
                    continue
                if getattr(ev, "value", None) is None:
                    continue
                figures[str(getattr(ev, "period_label", "") or "?")] = str(ev.value)
                if prov is None:
                    prov = derivation._json_safe_provenance(getattr(ev, "provenance", None))
            if not figures:
                unresolved.append({
                    "at": at, "note": "", "statement": "", "page": int(pg),
                    "caption": getattr(ref, "caption", ""), "quote": quote,
                    "why": "that row is printed with no figure beside it"})
                continue
            # `off_statement` IS WHAT THE CALLER FLAGS ON, and it is not a detail. A figure on a
            # statement page is stated in the units that page declares, which `stages.normalize`
            # resolved and scaled; a page the classifier could not name declares nothing, so the
            # scale applied to it is the DOCUMENT's and nothing verified it for this page. The
            # figure is real and its scale is unverified, and only the caller can say so on the
            # row (`stages.line_item_llm._write`).
            resolved.append({"at": at, "note": "", "statement": "", "title": "",
                             "page": int(pg), "caption": caption, "figures": figures,
                             "provenance": prov, "quote": quote, "on_face": False,
                             "off_statement": True,
                             "row_id": str(getattr(row, "id", "") or "")})
            continue

        # A CITATION THAT NAMES A STATEMENT IS LOOKED UP ON THE FACE, not in the notes.
        #
        # Taken first and returning either way, because the two indexes are alternatives rather
        # than a fallback chain: a caption that happens to appear in both a note and on the face is
        # a DIFFERENT fact in each, and falling through from one to the other would publish the
        # note's figure for a citation that said "the face". The statement mismatch is reported as
        # itself for the same reason — "that caption is on another statement" tells an author
        # something "no such row" does not.
        if want_stmt and not want_note and not allow_face:
            unresolved.append({
                "at": at, "note": "", "statement": want_stmt,
                "caption": getattr(ref, "caption", ""), "quote": quote,
                "why": ("this line is read from its notes, not from the face of a statement — "
                        f"a row on {want_stmt} cannot be its source")})
            continue
        if want_stmt and not want_note:
            on_face = next(((st, cap, row) for st, cap, row in face_rows
                            if st == want_stmt and want_cap
                            and ((got := norm(cap)) and (want_cap in got or got in want_cap))),
                           None)
            if on_face is None:
                # EMPTY IS NOT A STATEMENT. A face row whose page resolved none carries "", and
                # reporting it read "it is on " with nothing after it — a diagnostic worse than
                # none, because it asserts the caption was found somewhere nameable.
                elsewhere = sorted({st for st, cap, _r in face_rows
                                    if st and want_cap and (g := norm(cap))
                                    and (want_cap in g or g in want_cap) and st != want_stmt})
                unresolved.append({
                    "at": at, "note": "", "statement": want_stmt,
                    "caption": getattr(ref, "caption", ""), "quote": quote,
                    "why": (f"no printed row on {want_stmt} matches that caption"
                            + (f" — it is on {', '.join(elsewhere)}" if elsewhere else ""))})
                continue
            st, caption, row = on_face
            figures, prov = {}, None
            for ev in (getattr(row, "values", None) or {}).values():
                if getattr(ev, "column_index", None) is not None:
                    continue
                if getattr(ev, "value", None) is None:
                    continue
                figures[str(getattr(ev, "period_label", "") or "?")] = str(ev.value)
                if prov is None:
                    prov = derivation._json_safe_provenance(getattr(ev, "provenance", None))
            if not figures:
                unresolved.append({
                    "at": at, "note": "", "statement": st,
                    "caption": getattr(ref, "caption", ""), "quote": quote,
                    "why": "that row is printed with no figure beside it"})
                continue
            # THE ROW'S OWN IDENTITY TRAVELS WITH THE RESOLVED CITATION, and it is the only
            # thing here that is not text. `stages.line_item_llm` needs to know WHICH printed row
            # a citation claimed, not merely which caption: a face row is the deterministic
            # proposal for exactly one line, so a citation naming it on ANOTHER line's answer has
            # moved that printed figure, and the line it was proposed for must not go on showing
            # it. A caption cannot answer that — two statements can print the same words — so the
            # row's `id` is carried and compared.
            resolved.append({"at": at, "note": "", "statement": st, "title": "",
                             "caption": caption, "figures": figures, "provenance": prov,
                             "quote": quote, "on_face": True,
                             "row_id": str(getattr(row, "id", "") or "")})
            continue

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
                        "at": at,
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
                    "at": at,
                    "note": want_note, "caption": getattr(ref, "caption", ""), "quote": quote,
                    "amount": stated,
                    "why": (f"the amount {stated} does not appear in note {want_note}'s text — a "
                            f"figure the model stated rather than located is refused")})
                continue
            unresolved.append({"at": at,
                               "note": want_note,
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
        resolved.append({"at": at,
                         "note": number, "title": getattr(table, "title", "") or "",
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
