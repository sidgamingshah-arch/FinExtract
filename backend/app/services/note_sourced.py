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
from app.services.note_context import matches_title, note_blocks, same_table_title, subject_tokens

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
    """The first pattern that matches, or None. Returns the PATTERN so the trail can name it.

    A BILINGUAL CAPTION IS TRIED WHOLE AND AS EACH OF ITS TWO LANGUAGES. An HKEX note prints every
    row twice over — "Other receivables 其他應收款項" — and the authored patterns are anchored to a
    caption's whole extent (`^\s*other\s+receivables?\s*$`), so neither the English pattern nor the
    Chinese one could match either half: China SCE 1966's note 24 prints its other receivables at
    5,818,375 / 7,028,687 and `sub__cp_other_receivables_gross` selected nothing, so
    Other Receivables (CP) was blank. A caption in one script is tried exactly as before.
    """
    for candidate in _script_halves(text or ""):
        for raw, rx in compiled:
            if rx is not None and rx.search(candidate):
                return raw
    return None


_HAN_RUN = re.compile(r"[㐀-鿿豈-﫿]")
_HAN_PART = re.compile(r"[㐀-鿿豈-﫿　-〿＀-￯]+")


def _script_halves(text: str) -> list[str]:
    """``[text]``, or ``[text, latin half, han half]`` when the caption carries both scripts."""
    if not (_HAN_RUN.search(text) and re.search(r"[A-Za-z]{2,}", text)):
        return [text]
    han = "".join(_HAN_PART.findall(text))
    latin = re.sub(r"\s+", " ", _HAN_PART.sub(" ", text)).strip()
    return [text] + [h for h in (latin, han) if h and h != text]


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
                note_sections: dict[str, set[str]] | None = None,
                claimed: dict[str, set[str]] | None = None) -> list[NoteRowHit]:
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
    # A LINE THAT FINDS ITS NOTES BY MEANING has no title pattern; its notes are the ones its
    # `note_terms` cover, one sibling per note (`line_item_notes.claimed_notes`). Without the
    # run's claims to hand, the covered set is used as it stands.
    from app.services import line_item_notes
    by_meaning = line_item_notes.by_meaning_only(item)
    if by_meaning:
        mine = (set(claimed.get(item.key, ())) if claimed is not None
                else set(line_item_notes.covered_notes(item, notes)))
        readable = line_item_notes.readable_tables(item, notes)
    sums_items = line_item_notes.sums_line_items(item)
    # See `NoteSource.measure`: the slug of the note column this part reads, "" for the primary.
    measure = str(getattr(src, "measure", "") or "")
    wanted = {f"{p}:{measure}" for p in (periods or ())} if measure else None
    # See `NoteSource.from_measure_grid`: whether this part's column must (or must not) have been
    # read from a two-level header. None does not ask.
    #
    # LOCAL, for the reason `resolve_sources` gives about `normalize_statement`: `services.mapping`
    # imports THIS module at module level, and `row_reconstruct` reaches `mapping` through
    # `line_item_config`, so a module-level import of the flag is a circular one — measured, it
    # fails on `from app.services.row_reconstruct import GRID_FLAG` with row_reconstruct half
    # initialised. Imported from the one module that RAISES the flag all the same, so the two
    # cannot drift to different strings.
    from app.services.row_reconstruct import GRID_FLAG

    want_grid = getattr(src, "from_measure_grid", None)
    counts = _compiled(getattr(src, "row_caption_any", None))
    vetoes = _compiled(getattr(src, "row_caption_none", None))
    if not (titles or by_meaning) or not counts:
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
        if by_meaning:
            if (str(getattr(table, "note_number", "") or "") not in mine
                    or id(table) not in readable):
                continue
        elif not (_matches_title_any(title, titles)
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
        rows_here = list(getattr(table, "items", None) or ())
        broken_down = _broken_down(rows_here, counts)
        for index, row in enumerate(rows_here):
            # A ROW WITH A 其中 BREAKDOWN UNDER IT is the sum of that breakdown, so where the
            # breakdown is read the row is not read as well. Measured on 688008's 交易性金融资产
            # note and 300319's: "以公允价值计量且其变动计入当期损益的金融资产 1,783,494,750.68"
            # prints its 其中：结构性存款 and 其中：权益工具投资 beneath it, and a sum of the note's
            # line items counted the money twice. Reading the breakdown rather than the row is what
            # lets a vetoed component drop out — 300319's 其中：远期结售汇, a forward contract
            # inside the prior-year 181,124,711.32.
            if index in broken_down:
                continue
            # A LINE-ITEM SUM IS NOT READ OUT OF A MOVEMENT TABLE: its rows are the year's
            # movements (at 1 January, additions, disposals, at 31 December), and summing them is
            # not a balance. Its vetoes are three or four by design, too few to name every movement
            # caption — so the table is not read at all. A part that NAMES its row (a depreciation
            # charge) reads movement tables as it always did.
            if sums_items and block_periods:
                continue
            caption = getattr(row, "raw_label", "") or ""
            # THE GROUPING HEADER COUNTS AS THIS ROW'S CAPTION TOO, because one common note shape
            # puts the line-item caption on the GROUP and the counterparty on the row. A mainland
            # related-party note is exactly that table:
            #
            #     其他应收款：                      <- the group; the caption an author writes
            #       唐山唐钢气体有限公司   118,850.00  <- the row; a company name
            #       合计                118,850.00
            #
            # Matching `raw_label` alone, every row of such a note is a company name and no
            # authored `row_caption_any` can ever hit one. Measured: `sub__rp_find_3` — the spec's
            # third reading, "the net amounts attributable to related parties in the related-party
            # note" — selected zero rows on every filing, so the rule "take the highest of Find 1,
            # Find 2 and Find 3" was deciding from a sample of one.
            #
            # THE VETO APPLIES TO BOTH, and that is what keeps this from widening the gate: a row
            # admitted by its group is still refused by its own caption, so the group's `合计` and
            # every movement, gross and allowance row inside it are excluded exactly as before. The
            # group is checked SECOND, so a row that matches on its own caption behaves as it
            # always did.
            group = str(getattr(row, "group_hint", "") or "")
            matched = _matches_any(caption, counts) or (
                bool(group) and _matches_any(group, counts))
            if not matched:
                continue
            if _matches_any(caption, vetoes) or (group and _matches_any(group, vetoes)):
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
                # WHICH MEASURE OF THE PERIOD THIS PART ASKED FOR. Default "" is the primary
                # measure and the bare label, which is every part that existed before this and is
                # why the `wanted` set is just `periods` in that case. A part declaring
                # `measure: "allowance"` reads `current:allowance` instead — and the hit is
                # reported under the BARE period, so it fills `current`/`prior` like any other part
                # and nothing downstream needs to know. See `NoteSource.measure`.
                # WAS THIS COLUMN READ FROM A TWO-LEVEL HEADER? Asked BEFORE the measure filter,
                # because it is the question the PRIMARY column needs and `measure` cannot put: a
                # 账面余额 | 坏账准备 grid states its gross in the primary column, which has no
                # suffix, and a plain comparative states its reported amount there too. Measured
                # on a note of the first shape, the part reading "the reported amount" took the
                # gross and the line published 871,232,076.76 for a net of 683,092,791.26.
                if want_grid is not None:
                    on_grid = GRID_FLAG in tuple(
                        getattr(getattr(value, "confidence", None), "flags", None) or ())
                    if on_grid is not bool(want_grid):
                        continue
                if measure:
                    # THE SUFFIX IS REQUIRED, unconditionally, and not merely "allowed by the
                    # period filter". `periods` is None whenever the face declared no period
                    # labels of its own, and the membership test below is skipped in that case —
                    # so a part asking for the allowance column took the PRIMARY figure instead,
                    # and a note printing no allowance at all reported its balance as the
                    # allowance. `sub__rp_find_3` then deducted a number from itself.
                    if not label.endswith(f":{measure}"):
                        continue
                    if periods is not None and label not in wanted:
                        continue
                    label = label.rsplit(":", 1)[0]
                elif periods is not None and label not in periods:
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
                    # `label`, NOT `value.period_label`. The two are the same string for every
                    # part reading the primary measure — `label` was read off the value above —
                    # and they differ for a part declaring `measure`, where `label` has had the
                    # measure suffix stripped so the figure lands in `current`/`prior` like any
                    # other. Reading the value again here is what made that stripping dead code:
                    # the allowance was found, summed, and then filed under `current:allowance`,
                    # where the rung that deducts it could not see it.
                    period=label))
    return hits



_BREAKDOWN_GROUP = re.compile(r"^\s*(?:其中|of\s+which|including)\b", re.IGNORECASE)


def _broken_down(rows, counts) -> set[int]:
    """Indices of rows followed by a 其中 / "of which" breakdown that this line also reads."""
    out: set[int] = set()
    parent: int | None = None
    for i, row in enumerate(rows):
        group = str(getattr(row, "group_hint", "") or "")
        if _BREAKDOWN_GROUP.match(group):
            if parent is not None and _matches_any(str(getattr(row, "raw_label", "") or ""),
                                                   counts):
                out.add(parent)
            continue
        parent = i
    return out


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
        # THE NARRATIVE, NOT THE WHOLE NOTE. `prose_text` is the section's text with its tabulated
        # lines removed (`notes_extract._narrative_only`); `None` means nothing computed it — a
        # hand-built table, or a caller that supplied only `source_text` — and there the whole text
        # is all there is. "" means "computed, and this note is all table", which must yield NO
        # sentence rather than fall back to the table it just excluded.
        #
        # WHY THE ROUTE CANNOT SEE A TABLE. A printed table flattens into `source_text` with no
        # sentence punctuation, so this loop received the entire note as ONE sentence: on China SCE
        # 1966's profit-before-tax note a depreciation pattern matched across rows printed inches
        # apart and the FIRST grouped amount in the note — 17,475,980, its Cost of properties sold
        # — was published as the operating-expense depreciation charge.
        narrative = getattr(table, "prose_text", None)
        if narrative is None:
            narrative = getattr(table, "source_text", "") or ""
        for sentence in _sentences(narrative):
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


def take_by_rollup(amounts: list[Decimal], rollup: str) -> tuple[Decimal, list[bool]]:
    """What several contributions to ONE (basis, period) come to, and which of them counted.

    `sum` adds them. `alternatives` takes the FIRST and reports the rest as
    available-but-not-taken, because those are several disclosures of one charge rather than
    several charges — summing them would multiply a cost by the number of places the filing
    happened to mention it.

    ONE COPY, because both note routes have to answer this and they must answer it the same way.
    `resolve` asks it of a note's ROWS and `stages.note_sourced` asks it of a note's SENTENCES; the
    prose branch used to answer it by writing each hit in turn, and `_write` replaces a slot it
    already holds — so two sentences stating two components of one charge published the LAST of
    them. Measured on the shipped set, whose six prose lines all declare `rollup: "sum"`: two
    sentences worth 1,200 and 1,300 published 1,300.
    """
    if rollup == "alternatives":
        return amounts[0], [True] + [False] * (len(amounts) - 1)
    return sum(amounts, Decimal(0)), [True] * len(amounts)


def resolve(hits: list[NoteRowHit], rollup: str) -> dict[tuple[str, str], tuple[Decimal, list[dict]]]:
    """Per (basis, period): the figure this item's rows come to, and the trail that explains it.

    The rollup rule itself is :func:`take_by_rollup`, which the prose route reads too.
    """
    by_slot: dict[tuple[str, str], list[NoteRowHit]] = {}
    for h in hits:
        by_slot.setdefault((h.basis, h.period), []).append(h)

    out: dict[tuple[str, str], tuple[Decimal, list[dict]]] = {}
    for slot, rows in by_slot.items():
        amount, counted = take_by_rollup([r.amount for r in rows], rollup)
        out[slot] = (amount, [_trail_input(r, counted=c) for r, c in zip(rows, counted)])
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

# HOW MUCH OF THE LONGER STRING THE SHORTER ONE HAS TO BE, for a containment match to count.
#
# 0.5 rather than a character floor, and the distinction matters. A filing prints "合计" and "Total"
# as whole captions, so ANY absolute minimum length would refuse a model citing one of those
# EXACTLY — the commonest citation there is. A ratio does not care how short a caption is, only
# whether the two strings are comparable in length, which is the actual question.
_CAPTION_SPECIFICITY = 0.5


def _caption_matches(want: str, cap_norm: str) -> bool:
    """Whether a cited caption and a printed one are the same row's, both already normalised.

    THE RULE WAS CONTAINMENT EITHER WAY, which is right for what it was written for and unbounded
    in the one direction that bites. `resolve_sources` says it is "deliberately forgiving on
    punctuation and strict on words": a filing prints "Depreciation of property, plant and
    equipment^" and a model quoting it may drop the footnote marker or a comma, and neither changes
    which row is meant. Containment allows that.

    BUT A SHORT CAPTION IS CONTAINED IN ALMOST ANYTHING. Measured on 2025041600195, a citation of
    `nowhere in this note` — a phrase naming no row at all — RESOLVED, to a figure of 355: it
    normalises to `nowhereinthisnote`, and note 51 has rows captioned `Note`, `In` and `No`, each a
    substring of it. The corpus carries 1,744 captions of three normalised characters or fewer
    (mostly two-character CJK fragments off pages whose text layer came out shredded), so every one
    of them is a wildcard that can answer a citation meant for another row. The same rule is what
    made a correct citation of `At 31 December 2024` resolve to a row captioned `At`.

    AND A WRONG RESOLUTION IS WORSE THAN NO RESOLUTION HERE, which is why this is a refusal rather
    than a ranking. `resolve_sources`' own contract is that "a citation that resolves to nothing is
    returned as unresolved, not dropped and not believed", and the caller "keeps the mapping and
    flags it". A match that resolves to the wrong row skips all of that and publishes a figure with
    a citation behind it that a reviewer has no reason to doubt.

    EXACT EQUALITY ALWAYS PASSES, whatever the length. That is what keeps a citation of `合计`,
    `Total` or `At` against a row of the same name working, and confines the floor to the case it
    is for: one string standing in for a much longer other.
    """
    if not want or not cap_norm:
        return False
    if want == cap_norm:
        return True
    if not (want in cap_norm or cap_norm in want):
        return False
    shorter, longer = sorted((len(want), len(cap_norm)))
    return shorter >= _CAPTION_SPECIFICITY * longer


def _note_key(text) -> str:
    """A cited or extracted note number in one comparable spelling.

    NFKC so a full-width digit is a digit, and the prefixes a model or a filing puts in front of
    a number removed — "Note 12", "附注七、9" and "12." name the same note as "12" and "七、9".
    """
    import re as _re
    import unicodedata

    t = unicodedata.normalize("NFKC", str(text or "")).strip()
    t = _re.sub(r"^(?:notes?\.?|附注|附註)\s*", "", t, flags=_re.IGNORECASE)
    return t.strip().rstrip(".").strip()


def same_note(want, got) -> bool:
    """Whether a CITED note number names this EXTRACTED note — by identity, never by substring.

    THE BUG THIS REPLACES. Both call sites tested `want in got or got in want`, so a citation of
    note 12 also accepted notes 1 and 2, and on a CAS filing "七、1" accepted every one of 七、10 to
    七、19. Rows are scanned in document order and the first caption match wins, so note 1's
    "Total" was published for a line citing note 12.

    WHAT "EITHER HALF" STILL MEANS. A mainland note is chapter-qualified ("七、9") and a model may
    cite only the number: a BARE citation matches a qualified note whose NUMBER half is equal, and
    a qualified citation matches only itself. Two qualified numbers in different chapters never
    match — 七、9 and 十九、9 are the group's note and the parent company's.
    """
    from app.services.notes_extract import split_note_number

    w, g = _note_key(want), _note_key(got)
    if not w or not g:
        return False
    if w == g:
        return True
    w_ch, w_no = split_note_number(w)
    g_ch, g_no = split_note_number(g)
    if w_ch and g_ch:
        return False
    return bool(w_no) and w_no == g_no


def _notes_named(want, numbers):
    """The extracted note numbers a citation names, EXACT matches first and only those if any.

    A bare "9" on a CAS filing names both 七、9 and 十九、9, and nothing in the citation decides
    between them — so both stay candidates. But where the citation is itself qualified, or where
    one extracted number equals it exactly, that one is the note it named and the looser half-match
    is not consulted.
    """
    w = _note_key(want)
    exact = {n for n in numbers if _note_key(n) == w}
    return exact or {n for n in numbers if same_note(want, n)}


def best_caption(want: str, candidates, key=lambda c: c):
    """The candidate whose normalised caption is the one a citation named — or None.

    EXACT BEATS CONTAINMENT, and among containments the CLOSEST LENGTH wins; document order only
    breaks a tie. `_caption_matches` decides WHETHER two captions may match — its 0.5 length floor
    stops a two-character caption answering a citation meant for another row — and this decides
    WHICH of the admitted candidates is meant. The floor alone does not: 应收账款 is 4/6 of
    应收账款合计, so both pass it. The previous rule took the first caption where either contained the other,
    so a citation of "应收账款合计" resolved to the "应收账款" gross row printed above the total, and
    a bare "合计" row was contained in almost any cited "...合计" and matched from whichever note
    came first.

    `want` and every `key(candidate)` must already be normalised by the caller's `norm`.
    """
    if not want:
        return None
    exact = None
    near: list[tuple[int, int, object]] = []
    for index, cand in enumerate(candidates):
        got = key(cand)
        if not got:
            continue
        if got == want:
            exact = cand
            break
        # CONTAINMENT THROUGH `_caption_matches`, so its specificity floor still refuses a short
        # caption standing in for a much longer one; this function only decides AMONG the
        # candidates that floor admits, which is the half it did not cover.
        if _caption_matches(want, got):
            near.append((abs(len(got) - len(want)), index, cand))
    if exact is not None:
        return exact
    if not near:
        return None
    near.sort(key=lambda t: (t[0], t[1]))
    return near[0][2]


def _tied_captions(want: str, candidates, key=lambda c: c) -> list:
    """Every candidate `best_caption` would consider AS GOOD AS its pick, in document order.

    The same two tiers: the exact matches when there are any, otherwise the containments admitted
    by `_caption_matches` at the closest length. `best_caption` returns the first of these, so a
    list of one is a citation that named its row; more is a tie that document order decided.
    """
    if not want:
        return []
    exact = [c for c in candidates if key(c) and key(c) == want]
    if exact:
        return exact
    near = [(abs(len(key(c)) - len(want)), c) for c in candidates
            if key(c) and _caption_matches(want, key(c))]
    if not near:
        return []
    closest = min(d for d, _c in near)
    return [c for d, c in near if d == closest]


def _figures_by_basis(row) -> tuple[dict, dict, dict | None]:
    """A row's figures, KEYED BY BASIS as well as period — and the one basis the row is filed under.

    `(figures, by_basis, provenance)`. The flat `figures` was built from every value of the row
    keyed by period alone, so a row printing both bases for one period kept whichever value came
    last, and the caller had no way to say which basis the figure it wrote belonged to — it filed
    everything under the document's majority basis, so a figure cited from the company-only
    chapter landed in the CONSOLIDATED slot.

    The chosen basis is the row's only one when it has one; where it has several, consolidated when
    present (the slot the face populates), otherwise the first in document order. `figures` is that
    basis's periods, so every existing consumer keyed on period reads one basis, not a blend.
    """
    by_basis: dict[str, dict[str, str]] = {}
    order: list[str] = []
    prov = None
    for ev in (getattr(row, "values", None) or {}).values():
        if getattr(ev, "column_index", None) is not None:
            continue
        if getattr(ev, "value", None) is None:
            continue
        basis = _basis_of(ev) or "consolidated"
        if basis not in by_basis:
            by_basis[basis] = {}
            order.append(basis)
        by_basis[basis][str(getattr(ev, "period_label", "") or "?")] = str(ev.value)
        if prov is None:
            prov = derivation._json_safe_provenance(getattr(ev, "provenance", None))
    if not by_basis:
        return {}, {}, None
    chosen = "consolidated" if "consolidated" in by_basis else order[0]
    return dict(by_basis[chosen]), {"basis": chosen, **{"by": by_basis}}, prov


def resolve_sources(sources, notes, face=None, *, allow_face: bool = True,
                    pages=None, allow_pages: bool = False,
                    allow_rows: bool = True) -> tuple[list[dict], list[dict]]:
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

    `allow_rows=False` IS A `prose` LINE, whose author said the figure is stated in a SENTENCE and
    tabulated nowhere. `allow_face` already refuses it a statement row; this refuses it a NOTE's
    row, which nothing did — measured, a citation of note 9's "Depreciation of property, plant and
    equipment" resolved for `sub__ga_depreciation` and took that row's figure. The prose branch
    below is unaffected: an amount verified against the note's own text is still accepted, which is
    the one way such a line is meant to be answered.

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

    # (number, caption, row, table, block key, block heading) — the BLOCK being the one the request
    # showed the model this row in, by the same mapping the request was built with.
    rows: list[tuple[str, str, object, object, tuple[str, str], str]] = []
    for table, (block, heading) in zip(notes or (), note_blocks(notes)):
        number = str(getattr(table, "note_number", "") or "")
        for row in getattr(table, "items", None) or ():
            caption = getattr(row, "raw_label", "") or ""
            if caption:
                rows.append((number, caption, row, table, block, heading))
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
            on_page = best_caption(want_cap, [r for r in (pages or ())
                                              if int(r[0]) == int(want_page)],
                                   key=lambda r: norm(r[1]))
            if on_page is None:
                elsewhere = sorted({int(pg) for pg, cap, _r in (pages or ())
                                    if _caption_matches(want_cap, norm(cap))
                                    and int(pg) != int(want_page)})
                unresolved.append({
                    "at": at, "note": "", "statement": "", "page": int(want_page),
                    "caption": getattr(ref, "caption", ""), "quote": quote,
                    "why": (f"no row on page {int(want_page)} matches that caption"
                            + (f" — it is on page {', '.join(str(n) for n in elsewhere)}"
                               if elsewhere else ""))})
                continue
            pg, caption, row = on_page
            figures, filed, prov = _figures_by_basis(row)
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
                             "basis": filed.get("basis"), "figures_by_basis": filed.get("by"),
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
            on_face = best_caption(want_cap, [f for f in face_rows if f[0] == want_stmt],
                                   key=lambda f: norm(f[1]))
            if on_face is None:
                # EMPTY IS NOT A STATEMENT. A face row whose page resolved none carries "", and
                # reporting it read "it is on " with nothing after it — a diagnostic worse than
                # none, because it asserts the caption was found somewhere nameable.
                elsewhere = sorted({st for st, cap, _r in face_rows
                                    if st and _caption_matches(want_cap, norm(cap))
                                    and st != want_stmt})
                unresolved.append({
                    "at": at, "note": "", "statement": want_stmt,
                    "caption": getattr(ref, "caption", ""), "quote": quote,
                    "why": (f"no printed row on {want_stmt} matches that caption"
                            + (f" — it is on {', '.join(elsewhere)}" if elsewhere else ""))})
                continue
            st, caption, row = on_face
            figures, filed, prov = _figures_by_basis(row)
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
                             "basis": filed.get("basis"),
                             "figures_by_basis": filed.get("by"),
                             "row_id": str(getattr(row, "id", "") or "")})
            continue

        hit = None
        if want_cap and not allow_rows:
            # NAMED RATHER THAN SILENTLY FALLING THROUGH, the same way the statement refusal is:
            # "this line is read from a sentence, not a row" tells an author the PLACE was wrong,
            # where an unresolved caption would send them to fix the caption.
            unresolved.append({
                "at": at, "note": want_note, "caption": getattr(ref, "caption", ""),
                "quote": quote,
                "why": ("this line's figure is stated in prose, not in a table row — a row "
                        "citation cannot be its source (route=prose). Give the amount with the "
                        "sentence it is printed in instead")})
            continue
        if want_cap:
            pool = rows
            if want_note:
                named = _notes_named(want_note, {r[0] for r in rows})
                pool = [r for r in rows if r[0] in named]
            # THE BLOCK, WHEN THE CITATION NAMES ONE. Refused rather than widened when the note has
            # no such block: a citation of the wrong table is not made right by reading the note's
            # first matching caption instead, which is exactly the answer this narrowing replaces.
            want_table = str(getattr(ref, "table", "") or "").strip()
            if want_table and pool:
                in_table = [r for r in pool if same_table_title(want_table, r[4][1])]
                if not in_table:
                    headings = list(dict.fromkeys(r[5] for r in pool if r[5]))
                    unresolved.append({
                        "at": at, "note": want_note, "table": want_table,
                        "caption": getattr(ref, "caption", ""), "quote": quote,
                        "why": (f"note {want_note or '(none)'} has no table headed {want_table!r}"
                                + (f" — its tables are headed {'; '.join(headings[:8])}"
                                   if headings else ""))})
                    continue
                pool = in_table
            # THE GROUP NARROWS WHERE IT CAN, and only there. A row may carry no group of its own
            # while the model still names the heading it reads the row as being under, and that is
            # not a wrong citation; a group that matches no row is simply not used.
            want_group = norm(getattr(ref, "group", ""))
            if want_group:
                in_group = [r for r in pool
                            if _caption_matches(want_group,
                                                norm(getattr(r[2], "group_hint", "") or ""))]
                if in_group:
                    pool = in_group
            hit = best_caption(want_cap, pool, key=lambda r: norm(r[1]))
            if hit is not None:
                # A CAPTION THAT STILL NAMES SEVERAL ROWS IS REFUSED, where the request gave the
                # model a way to name one — the rows sit in different blocks or different groups —
                # and they print different figures. Document order used to break that tie, and on a
                # CAS note that is whichever table of the number happens to come first. Rows the
                # request presented identically keep the tie-break: nothing a model could have
                # written would have named one of them.
                tied = _tied_captions(want_cap, pool, key=lambda r: norm(r[1]))
                places = {(r[4], norm(getattr(r[2], "group_hint", "") or "")) for r in tied}
                figures = {tuple(sorted(_figures_by_basis(r[2])[0].items())) for r in tied}
                if len(places) > 1 and len(figures) > 1:
                    where = list(dict.fromkeys(
                        " / ".join(t for t in (r[5], str(getattr(r[2], "group_hint", "") or ""))
                                   if t) for r in tied))
                    unresolved.append({
                        "at": at, "note": want_note, "table": want_table,
                        "group": str(getattr(ref, "group", "") or ""),
                        "caption": getattr(ref, "caption", ""), "quote": quote,
                        "why": (f"that caption names {len(tied)} rows with different figures — "
                                f"under {'; '.join(where[:6])} — give the `table` and `group` "
                                f"the row is printed under")})
                    continue
        if hit is None:
            # THE PROSE CASE. A figure stated in a footnote belongs to no row, so there is nothing
            # to match a caption against. Where the model also gave the AMOUNT, this is the one
            # place it is allowed to — and the amount is VERIFIED against the note's own text
            # before it is accepted, which is what makes it a located figure rather than a
            # supplied one.
            #
            # THE NOTE'S OWN TEXT AND NOTHING ELSE. The witness used to be
            # `source_text + " " + quote`, and the quote is the MODEL'S — so an amount that
            # appeared nowhere in the filing was accepted as long as the sentence the model typed
            # around it contained the digits. That is self-certification standing where the
            # docstring below promises "the number must be demonstrably printed".
            #
            # ACROSS EVERY FRAGMENT, for the reason `_notes_by_number` records: note 51 of
            # 2025041600195 arrives as eight tables and only the eighth states the figure, so
            # asking one of them answers no about a note that says yes.
            stated = str(getattr(ref, "amount", "") or "").strip()
            fragments = _notes_by_number(notes, want_note)
            if stated and fragments:
                witness = "\n".join(getattr(t, "source_text", "") or "" for t in fragments)
                found = _amount_in_text(stated, witness)
                # The page comes from the fragment that actually states it, so click-to-source
                # lands on the sentence rather than on the note's first page.
                table = next((t for t in fragments
                              if _amount_in_text(stated, getattr(t, "source_text", "") or "")
                              is not None), fragments[0])
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
        number, caption, row, table, _block, _heading = hit
        figures, filed, prov = _figures_by_basis(row)
        resolved.append({"at": at,
                         "note": number, "title": getattr(table, "title", "") or "",
                         "caption": caption, "figures": figures, "provenance": prov,
                         "basis": filed.get("basis"), "figures_by_basis": filed.get("by"),
                         "quote": quote})
    return resolved, unresolved


def _notes_by_number(notes, number: str) -> list:
    """EVERY fragment of the note a citation names, not the first one.

    A NOTE ARRIVES IN FRAGMENTS — `line_item_notes` records one filing's note 1 arriving as 32
    tables — and a prose figure is verified against the note's own text, so which fragment answers
    decides whether a true citation is believed. Measured on 2025041600195: note 51 arrives as 8
    fragments, pages 170-177, and only the EIGHTH carries "1,885,020" in its `source_text`. Asking
    the first one whether the note states that figure answers no about a note that states it.

    That was invisible while the witness also included the model's own `quote`, because the quote
    carried the digits and the check passed on the model's word. Verifying against the note alone
    is what exposed it, which is the argument for verifying against the note alone.

    MATCHED BY IDENTITY (`same_note`), NOT BY SUBSTRING. A PRC note number is chapter-qualified
    ("七、9") and a model may cite only the number half, which is honoured; what is not is
    containment, which let a citation of 七、1 collect every fragment of 七、10 to 七、19 as well —
    so the witness was the concatenated text of eleven notes, and a figure printed in any of them
    "verified" against a note that never states it. An exact number is preferred where one exists.
    """
    want = (number or "").strip()
    if not want:
        return []
    tables = [t for t in (notes or ()) if str(getattr(t, "note_number", "") or "")]
    named = _notes_named(want, {str(t.note_number) for t in tables})
    return [t for t in tables if str(t.note_number) in named]


def _note_by_number(notes, number: str):
    """The first fragment of the note a citation names, for the callers that want one table."""
    found = _notes_by_number(notes, number)
    return found[0] if found else None


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
    """The stated amount, but only if that NUMBER — whole, not a run of its digits — is in the text.

    THE WHOLE SAFETY PROPERTY OF A PROSE FIGURE. The model may give an amount here and nowhere
    else, and what makes that safe is that the number must be demonstrably printed. "HK$529,841,000",
    "529,841,000" and "529841000" are the same number; 529,842,000 is not there and is refused.

    COMPARED AS NUMBERS, NOT AS DIGIT STRINGS, and both halves of the old rule were wrong. It
    stripped every separator from the text and asked whether the stated digits occurred ANYWHERE in
    what was left, so an invented 841,000 was "verified" against HK$529,841,000. And it removed the
    decimal point from the stated amount but not from the text, so a correctly printed 1,234.56 was
    always refused. Each printed number is now read as a token — thousands separators are a comma
    with at most one space after it, which is how a number wraps; a plain space is not a separator,
    so "2023 529,841,000" is two numbers — and the stated amount must EQUAL one of them.

    Returns the parsed figure rather than a boolean so the caller stores what was verified and not
    what was typed.
    """
    import re as _re

    raw = _re.sub(r"[^0-9.]", "", stated or "")
    if not raw or not _re.search(r"\d", raw):
        return None
    try:
        want = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    for token in _re.findall(r"(?<![\d.])\d{1,3}(?:,[ \u00a0\u202f]?\d{3})+(?:\.\d+)?(?![\d])"
                             r"|(?<![\d.,])\d+(?:\.\d+)?(?![\d,])", text or ""):
        try:
            if Decimal(_re.sub(r"[^0-9.]", "", token)) == want:
                return want
        except (InvalidOperation, ValueError):
            continue
    return None
