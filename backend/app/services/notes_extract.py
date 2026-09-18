"""Extract the NOTE detail tables from notes pages.

A note like "Note 15: Trade receivables" is followed by its own breakdown (the rows that
give the detail behind the face figure). This groups a notes page's words into note
sections by their headings and reconstructs each note's detail line items — free-format,
whatever rows the note contains — keeping page + bbox provenance. The result populates the
``NotesTable``/``NoteItem`` model so the All-Notes view and export can show the real detail.
"""
from __future__ import annotations

import re
import statistics
from decimal import Decimal

from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import NoteItem, NotesTable
from app.services.row_reconstruct import (
    GRID_FLAG, ColumnGrid, Word, _group_rows, _scan_row, build_line_items, row_tolerance)

# "Note 15: Trade receivables", "Note 15 Trade receivables", "15. Trade receivables"
#
# A PARENTHESISED NUMBER IS A HEADING TOO, and refusing it lost whole tables. A mainland filing
# numbers three levels deep — `十二、` the chapter, `5、` the note, `（6）` the table inside it — and
# this pattern required the digit at the start of the line, so `（6）关联方应收应付款项` matched
# NOTHING. The table was never opened as a note, so no `NotesTable` existed for any authored
# `note_source` to match, however exactly its patterns described the caption.
#
# Measured on 000709: its related-party receivable balances are printed under exactly that heading
# and `sub__rp_find_3` — the spec's "Find 3", the related-party note reading — produced no figure
# on any run. 184 notes were extracted from that filing and not one had a related-party title,
# which reads as "the filing does not disclose it" and is the opposite of the truth. The same
# numbering carries the revenue split (`（1）营业收入和营业成本`) and most other CAS note tables.
#
# BOTH BRACKET WIDTHS, because a filing sets them either way, and each half stays optional so the
# forms that already matched are untouched — `6、…` and `15. …` parse exactly as before. A bare
# `（1）` with no title is still refused by the no-title guard below, and a row carrying FIGURES
# never reaches here at all (`if values: return None`).
_HEADING = re.compile(
    r"^(?:note[s]?\.?\s+)?[（(]?(?P<no>\d{1,3})[）)]?\s*[:.\)\-]?\s*(?P<title>.*)$",
    re.IGNORECASE)

# A NOTE'S OWN SUBTOTAL OR TOTAL ROW, from its caption, in both languages the shipped rulebook
# supports. THE one definition of the question "is this note row a total rather than a detail".
#
# WHY IT LIVES HERE. It was written in ``stages.map_ontology`` as ``_DISCLOSURE_TOTAL``, to keep a
# note's own total out of a declared decomposition's components, with a comment explaining that
# ``role`` could not answer the question because every ``NoteItem`` is built ``LineRole.LINE``.
# That was true of the ROW BUILDER and not of the question: the caption is decided here, where the
# ``NoteItem`` is constructed, so the role can carry the verdict and every consumer reads one
# answer instead of re-deriving it. Two consumers do: ``map_ontology``'s disclosure split, and
# ``stages.reconcile``'s "a note's own subtotal isn't a detail" guard.
#
# THAT GUARD HAD NEVER FIRED, and this is the bug that makes this more than a tidy-up.
# ``services.reconcile`` builds a note's total by summing the DETAILS it is handed
# (``note_total += d.value``), so with every row a detail a note's printed total was summed
# alongside the rows it totals: a note that ties perfectly came out at
# ``residual = face - 2 x total = -face``. Measured on a real 270-page bilingual HKEX filing:
# 453 note rows, every one ``LineRole.LINE``, and of 109 reconciliation entries NOT ONE graded
# ``tied`` — 107 ``unconfirmed``, 2 ``untied``, and both of those ``untied`` were served to the
# analyst as "does not tie" assertions that were false. With the role set, 14 entries tie with
# residual exactly 0 (notes 6, 7, 20, 21, 26, 28) and both false assertions disappear.
#
# A PREFIX MATCH, and the asymmetry of the two failure directions is why. A MISS leaves a total
# row a detail, the note total is double-counted, the residual lands nowhere near the face figure
# and the tie grades ``unconfirmed`` — the same non-answer as before, and nothing is restated. A
# FALSE POSITIVE removes a real detail from the note total, which can turn a genuine tie into a
# reported break. So only the shape a filing prints at the START of a caption counts. Measured
# over the same filing's 453 rows this matched 36, every one a printed total or subtotal
# ("Subtotal 小計", "Total revenue 收益總額", "Total tax charge for the year 年內稅項開支總額").
#
# DELIBERATELY NOT ``row_reconstruct._TOTAL_LABEL``, which looks like the same thing: its Chinese
# alternatives are un-anchored and it is used with ``.search()``. On the same 453 rows it matches
# 7 that this rejects, and all 7 are ordinary details or prose — "Share of the joint ventures'
# total comprehensive loss 應佔合營公司的全面虧損總額", "Aggregate carrying amount of the Group's
# investments in the joint ventures 本集團於合營公司的投資賬面總額". Adopting it would delete real
# details from the note total, which is the failure direction that manufactures findings.
#
# "net" and "aggregate" are NOT prefixes here, for the same reason they were rejected in
# ``map_ontology``: "Net investment in leases", "Net book value", "Net carrying value 賬面淨值"
# and "Net assets 資產淨值" are ordinary component captions.
#
# ``小计`` (simplified) is in this alternation and was NOT in ``_DISCLOSURE_TOTAL``, which carried
# ``小計`` traditional and the simplified forms of the other two only. That gap was invisible there
# and would have been an inconsistency here: the subtotal arm below recognises both forms, so
# without it a simplified-Chinese "小计" was refused by the gate and then classifiable by the arm.
# The failure direction was the safe one (a missed total stays a detail and the tie declines), which
# is why nothing caught it.
#
# A NET-FLOW SUMMARY IS A TOTAL TOO, and it is the one shape whose caption does not open with a
# total WORD. A disposal- or acquisition-of-subsidiaries note closes its cash block with
#
#     Cash and cash equivalents disposed of            (3,902)
#     Cash consideration                              200,209
#     Consideration receivables                             –
#     Net inflow of cash and cash equivalents ...      196,307   <- the total of the three above
#
# and 200,209 - 3,902 = 196,307 exactly. Filed as a detail, that row is summed WITH the rows it
# totals, so the note total comes to twice the figure the face cites and the tie can never confirm.
# Measured on China SCE note 39: face 196,307, note total 392,614 — exactly double, in both
# periods (585,780 -> 1,171,560).
#
# NARROW ON PURPOSE, because the failure direction here is not symmetric: a missed total stays a
# detail and the tie declines (visible), while a detail wrongly read as a total is silently dropped
# from the sum and the tie then confirms against too little. "Net" opens plenty of real detail
# captions — net assets, net trade receivables, net book value — so only the cash-flow summary
# forms are matched, and `of cash` is required rather than assumed. Measured over the corpus: four
# rows match, all four are that summary line on SCE, and none on the two other filings.
_NOTE_TOTAL = re.compile(
    r"^\s*(total|sub-?total|合\s*計|總\s*計|小\s*計|合\s*计|总\s*计|小\s*计"
    r"|net\s+(?:cash\s+)?(?:in|out)flow(?:/?\(?(?:in|out)flow\)?)?\s+(?:of|in)\s+cash"
    r"|現金及現金等價物(?:流[入出])?淨額|现金及现金等价物(?:流[入出])?净额)", re.I)
# The subtotal arm of the same alternation, read separately only to tell the two roles apart. A
# note's SUBTOTAL is a partial sum inside the note; its TOTAL is the figure the face cites. Both
# are excluded from the details, so nothing downstream depends on getting the distinction right —
# it is served to the reader (the note pane emphasises them differently) and no arithmetic reads it.
_NOTE_SUBTOTAL = re.compile(r"^\s*(sub-?total|小\s*計|小\s*计)", re.I)


def note_row_role(caption: str | None) -> LineRole:
    """The role a note detail row carries, from its printed caption.

    ``LineRole.LINE`` unless the caption opens with a total or subtotal word — see ``_NOTE_TOTAL``
    for why the test is a prefix and why it is not the pattern in ``row_reconstruct``.
    """
    text = caption or ""
    if not _NOTE_TOTAL.match(text):
        return LineRole.LINE
    return LineRole.SUBTOTAL if _NOTE_SUBTOTAL.match(text) else LineRole.TOTAL


def _bare_note_number(row: list[Word]) -> str | None:
    """A note number printed on its own, with no title yet.

    Some filings split the heading across two lines: the first line carries only the note number
    and the next line carries the title. That shape is safe to recover here because it appears at
    the top of the note block, where a naked note number is not a detail row.
    """
    tokens = [w.text.strip() for w in row if w.text.strip()]
    if len(tokens) != 1:
        return None
    text = tokens[0]
    m = re.fullmatch(r"(?:note[s]?\.?\s*)?(?P<no>\d{1,3})\s*[:.\)\-]?", text, re.IGNORECASE)
    return m.group("no") if m is not None else None


def _title_only_row(row: list[Word]) -> str | None:
    """A title line with no numbers and no value column."""
    label_words, _, values = _scan_row(row)
    if values or not label_words:
        return None
    text = " ".join(w.text for w in label_words).strip()
    if not text:
        return None
    if text[0].islower() and text[0].isascii():
        return None
    return text


# CJK SENTENCE punctuation. A note's title is a name and carries none of it; the enumeration comma
# 、 is a different mark and DOES appear in real titles ("收益、其他收入及收益", "現金及現金等價物、
# 受限制現金"), so it is deliberately absent from this class.
_CJK_SENTENCE = re.compile(r"[，。；]")


# ── The notes' TOP-LEVEL CHAPTER, and why a note number alone is not an identity ───────────────
#
# A mainland (CSRC) annual report numbers its notes WITHIN each top-level chapter:
#
#     七、合并财务报表项目注释        notes 1 … 80
#     十四、关联方及关联交易          notes restart at 1
#     十九、母公司财务报表主要项目注释  notes restart at 1 again
#
# Keeping only the trailing number leaves the identity ambiguous, and not rarely: on 澜起科技
# 688008 FY2024, 15 of 48 note numbers carry two or more different headings. "note 2" names both
# the group's 交易性金融资产 (七、2) and the parent company's 其他应收款 (十九、2).
#
# WHAT THAT COST. Every mechanism that identifies a note by its number was unreliable for those:
# the note→face tie, each spec service's note lookup, and the restatement ledger that collapses
# two printings of ONE balance by comparing note numbers. Other Receivables (CP) published
# 2,484,202,201.08 against a printed 4,143,856.36, because the parent company's
# 1,247,570,989.98 was summed with the group's 4,143,856.36.
#
# THE FILING ITSELF SPELLS THE IDENTITY OUT: the balance sheet's 附注 column reads 七、9, not 9.
# Qualifying the note number with its chapter makes the two sides agree for the first time
# (row_reconstruct._note_ref_value keeps the chapter for the same reason), and an English filing —
# which prints no chapters — keeps bare numbers, byte for byte as before.
# ①-⑳ and ⑴-⑽, translated to "(n)" so `_HEADING` reads them with the bracketed numbers it
# already accepts. A mainland note nests three deep and these caption the innermost level.
_CIRCLED_DIGITS = {
    **{0x2460 + i: f"({i + 1})" for i in range(20)},      # ① … ⑳
    **{0x2474 + i: f"({i + 1})" for i in range(20)},      # ⑴ … ⒇
}


_CHAPTER_HEADING = re.compile(r"^\s*(?P<ch>[一二三四五六七八九十]{1,3})\s*、\s*(?P<title>.{0,60})$")
_CJK_UNITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def chapter_ordinal(numeral: str) -> int | None:
    """A CJK chapter numeral as an integer — 十九 -> 19 — or None if it is not one.

    Only the forms a chapter heading uses: 一…九, 十, 十一…十九, 二十…九十九. A filing has never
    needed more than 二十-odd chapters, and refusing the rest keeps this from accepting a numeral
    that is really part of a caption.
    """
    text = (numeral or "").strip()
    if not text or any(c not in _CJK_UNITS and c != "十" for c in text):
        return None
    if "十" not in text:
        return _CJK_UNITS.get(text) if len(text) == 1 else None
    tens, _, units = text.partition("十")
    if (tens and tens not in _CJK_UNITS) or (units and units not in _CJK_UNITS):
        return None                        # 十十, 十甲 — not a numeral this reader accepts
    high = _CJK_UNITS.get(tens, 1) if tens else 1
    return high * 10 + (_CJK_UNITS[units] if units else 0)


# The chapter that holds the PARENT COMPANY's own notes. A CSRC filing repeats every material
# balance for the company alone under this heading, so those notes state the SAME concepts as the
# group's with different figures — 其他应收款 is 4,143,856.36 in 七、9 and 1,247,570,989.98 in
# 十九、2. Pooling the two is how Other Receivables (CP) came to publish 2,484,202,201.08, and the
# chapter is what finally distinguishes them: the note's ``basis`` is set from it, so a consumer
# computing a consolidated figure can decline a company-only note instead of summing across bases.
_COMPANY_CHAPTER = re.compile(r"母公司")


def read_chapter(rows: list[list[Word]], seen: int) -> tuple[str, int, str] | None:
    """The last top-level chapter heading on this page, as ``(numeral, ordinal, title)``, or None.

    STRICTLY INCREASING, and that is the whole of what tells a chapter from its namesakes. The
    same CJK-numeral form is printed by three different things:

      * the top-level chapters themselves  — 七、合并财务报表项目注释
      * a sub-enumeration INSIDE one note  — 一、账面原值 / 二、累计折旧 in the 固定资产 note
      * a statement's own face lines        — 一、营业总收入 (a FACE page, so not seen here)

    A chapter's number only ever goes up, while a sub-enumeration restarts at 一、 — so a numeral
    that is not greater than the highest chapter already seen is not a chapter. Measured on
    688008: walking the notes from their first page this recovers all 18 chapters exactly, 五、
    through 二十、, and admits none of the note-internal enumerations. It is also self-correcting
    from a mid-document start, because a sub-enumeration never reaches a real chapter's number
    once one has been read.

    ``seen`` is the highest ordinal read so far in this document, which is why the caller carries
    it from page to page — a chapter heads a run of pages and is printed once.
    """
    found: tuple[str, int, str] | None = None
    for row in rows:
        _, _, values = _scan_row(row)
        if values:
            continue                       # a row with figures is a detail line, not a heading
        text = " ".join(w.text for w in row).strip()
        match = _CHAPTER_HEADING.match(text)
        if match is None:
            continue
        ordinal = chapter_ordinal(match.group("ch"))
        if ordinal is None or ordinal <= max(seen, found[1] if found else 0):
            continue
        title = match.group("title").strip()
        if title and _CJK_SENTENCE.search(title):
            continue                       # a sentence that happens to open with a numeral
        found = (match.group("ch"), ordinal, title)
    return found


def split_note_number(identity: str | None) -> tuple[int, str]:
    """A note identity back into ``(chapter ordinal, note number)`` — ``"七、9"`` -> ``(7, "9")``.

    ``0`` for the chapter when there is none, so a bare-numbered note (every English filing) sorts
    before the chaptered ones and among itself exactly as it always did.

    The inverse of :func:`qualified_note_number`, and the reason both live here: a sort key or a
    comparison that took the identity apart with its own regex would be a second definition of
    what a note identity IS, and the two would drift.
    """
    text = str(identity or "")
    head, sep, tail = text.partition("、")
    if not sep:
        return 0, text
    ordinal = chapter_ordinal(head)
    return (ordinal, tail) if ordinal is not None else (0, text)


def qualified_note_number(chapter: str | None, number: str | None) -> str | None:
    """``"七、9"`` from a chapter and a note number — or the bare number when there is no chapter.

    ONE spelling of the identity, called by everything that forms or compares one, so the note
    side and the face side cannot drift apart. An English filing has no chapters and this is the
    identity function for it.

    IDEMPOTENT, and that is not a nicety. A note table that runs over more than one page is carried
    to the next page as ``carry_note`` (``services.pdf_extract`` passes the last table's
    ``note_number``, which is already an identity), and this was stamping the chapter on again for
    every page the note continued over. Measured on 澜起科技 688008 the extractor produced 七、七、1,
    七、七、七、17, 七、七、七、七、九、9, 五、五、五、11 and 十九、十九、十九、十九、十九、2 — around 80 of
    the 219 note tables ``stages.prune_notes`` dropped had an identity like that, so they could
    never match the face citation that was looking for them.

    A number that ALREADY names a chapter keeps the one it has, and the cross-chapter cases in that
    same measurement say why this is the right answer rather than merely a cheaper one: 十九、十八、8
    and 八、七、81 are notes that STARTED in one chapter and continued onto a page where the reader
    had advanced to the next. The chapter a note was printed under is the chapter it belongs to; the
    page it spills onto does not re-home it.
    """
    if not number:
        return number
    if not chapter:
        return number
    if split_note_number(number)[0]:
        return number                      # already an identity — see the docstring
    return f"{chapter}、{number}"


def _is_heading(row: list[Word]) -> tuple[str, str] | None:
    """A heading row names a note (number + optional title) and carries no value column of
    its own — that's what separates 'Note 15: Trade receivables' from a data row.

    THE PATTERN ALONE IS NOT ENOUGH, because it accepts any row opening with up to three digits and
    a notes page is mostly PROSE. Measured on a 270-page bilingual HKEX filing it took three
    sentence fragments for headings, and each one did more damage than an extra entry: the note
    index is keyed by number, so a fragment claiming a number that a real note already has
    overwrote — or was overwritten by — the real note's table.

        '8,461,842,000元） （附註30(b)）。'                       -> note 8
        '17. 內的合同，因此該等修訂對本集'                          -> note 17
        'note 25 to the financial statements, the Group had the …' -> note 25

    Two refusals, and each is a property of headings rather than a blocklist:

    * a title must not open with a LOWERCASE Latin letter, punctuation or a digit. A heading names
      something ("REVENUE, OTHER INCOME AND GAINS", "Trade receivables"); "to the financial
      statements" is the middle of a sentence, and "36%" or ";" name nothing. This is also what
      catches a number that is the head of a LONGER NUMERAL: the separator class this pattern
      consumes does not include the comma, so "8,461,842,000" leaves its title opening on one —
      and a percentage split across its decimal point ("28.36%" read as note 28) leaves a digit;
    * a title must not contain CJK SENTENCE punctuation (，。；). Real titles use the ENUMERATION
      comma 、 instead, so a Chinese-only heading is untouched while a Chinese sentence is refused.

    A THIRD GUARD WAS WRITTEN AND REMOVED: an explicit test for a grouped numeral. Mutating it away
    changed no test and no measured output, because the first refusal above already covers every
    shape it was for. A guard that cannot fire reads like protection without being any.
    """
    _, _, values = _scan_row(row)
    if values:
        return None
    text = " ".join(w.text for w in row).strip()
    # A CIRCLED NUMERAL IS A HEADING TOO, and it opens the deepest level a mainland note uses:
    # 十二、6 the note, （6）the table, ①应收项目 / ②应付项目 the sub-table inside THAT. Translated to
    # the parenthesised form rather than matched separately, because `_HEADING` already accepts
    # that and one spelling of "a number in brackets" is easier to reason about than two.
    #
    # WHY IT MATTERS MORE THAN AN EXTRA NOTE IN THE INDEX. `notes_extract` calls
    # `row_reconstruct.build_line_items` once PER SECTION, and each call reads its own value bands
    # and its own column grid. So a sub-table that opens no section shares the geometry of the one
    # above it — and on 000709 page 196 the ②应付项目 payables table has TWO columns at x≈0.58 and
    # x≈0.73 while the ①应收项目 receivables table above it has FOUR at 0.41/0.58/0.67/0.75. The
    # payables columns land exactly on the receivables' 坏账准备 positions, so every related-party
    # payable on that page was filed as a BAD-DEBT ALLOWANCE:
    #
    #     应付账款：唐山唐钢气体有限公司   current:allowance = 468,770,511.21
    #
    # — a figure no payables line can ever read, because nothing asks for an allowance there.
    # Opening a section gives the sub-table its own bands and its own grid, which is the whole fix.
    text = text.translate(_CIRCLED_DIGITS)
    starts_note = text.lower().startswith("note")
    m = _HEADING.match(text)
    if not m:
        return None
    no = m.group("no")
    title = m.group("title").strip(" :.-")
    # Require an explicit "Note" prefix OR a title, so a bare number isn't a false heading.
    if not starts_note and not title:
        return None
    if title:
        if title[0].islower() and title[0].isascii():
            return None
        if not (title[0].isalpha() or ord(title[0]) > 0x2E7F):
            return None                     # punctuation or a digit — names nothing
        if _CJK_SENTENCE.search(title):
            return None
    return no, title


# ── A NOTE'S FIRST COLUMN CAN BE A MERGED CATEGORY CELL ──────────────────────────────────────
#
# The CSRC related-party note is captioned in TWO label columns, not one:
#
#     项目名称  |          关联方              |  期末余额 …
#     其他应收款 | 中国电子科技集团公司第三十八研究所 |  88,998.71 …
#               | 中国电子科技集团公司第四十八研究所 | 200,000.00 …
#               | 安徽中电光达通信技术有限公司        |  15,753.60 …
#
# 项目名称 is printed ONCE per block and the eight rows it governs carry nothing but a related
# party's NAME. Read row by row, eight of the nine rows of the 其他应收款 block name no receivable
# class at all, so a service asking "is this row an 其他应收款?" saw one row of nine — and the one
# it saw was 88,998.71 of a 757,464.77 period-end balance.
#
# The cell is also printed VERTICALLY MERGED: on the measured filing (Sun Create Electronics
# 11077098, page index 191) "其他应收款" is drawn as "其他应收" / "款" on two lines that straddle
# the block's first data row, so neither fragment is on any row's own baseline. That is why the
# cells are assembled from the section's geometry here rather than left to the row reader, which
# folds a caption into the row BELOW it and had nowhere to put a caption that spans several.
#
# The carry is written onto ``group_hint`` — "the sub-heading printed above this row WITHIN its
# section", which is exactly what a merged 项目名称 cell is — and only where the row reader left
# it empty, so a colon sub-heading it did read always wins.
_OUTER_COLUMN_GAP = 0.05        # clear air between the category column and the caption column
_MAX_CATEGORY_CHARS = 24        # a category is a caption; a sentence in the margin is not
_CJK_SENTENCE_MARK = re.compile(r"[，。；]")


def _category_cells(rows: list[list[Word]], fmt=None) -> list[tuple[float, str]]:
    """``(top y, caption)`` for each category cell printed in a note's OUTER label column.

    Empty unless the section really has two label columns: the captions have to split into a
    dominant column and a column at least ``_OUTER_COLUMN_GAP`` to its left, and there have to be
    at least two categories and three rows under them. Every one of those is a veto — a note with
    one label column must come out of here unchanged, because ``group_hint`` is read as a row's
    meaning by the mapper and by four services.
    """
    scanned = [(row, _scan_row(row, fmt, extract_note_refs=False)) for row in rows]
    valued = [(row, lw) for row, (lw, _nr, vw) in scanned if lw and vw]
    edges = [round(min(w.bbox.x0 for w in lw), 2) for _row, lw in valued]
    if len(edges) < 3:
        return []
    main = statistics.mode(edges)
    limit = main - _OUTER_COLUMN_GAP
    frags: list[tuple[float, float, str]] = []
    for row, (lw, _nr, _vw) in scanned:
        # Measured on the word's LEFT edge and bounded by the caption column's: a category cell
        # STARTS a clear gap to the left of the captions and ENDS before them. Testing the right
        # edge instead found nothing — "其他应收" is four characters wide and runs to within 0.02
        # of the caption column, which is the cell being wide, not the cell being absent.
        outer = [w for w in lw if w.bbox.x0 <= limit and w.bbox.x1 < main]
        if not outer:
            continue
        text = "".join(w.text for w in sorted(outer, key=lambda w: w.bbox.x0)).strip()
        if text:
            frags.append((min(w.bbox.y0 for w in outer), max(w.bbox.y1 for w in outer), text))
    if not frags:
        return []
    # A merged cell drawn on several lines is ONE category: join fragments that are printed
    # directly below one another, ignoring the data rows interleaved between them.
    frags.sort()
    cells: list[tuple[float, float, str]] = [frags[0]]
    for y0, y1, text in frags[1:]:
        py0, py1, ptext = cells[-1]
        line_h = max(py1 - py0, 1e-4)
        if -0.5 * line_h <= y0 - py1 <= 0.6 * line_h:
            cells[-1] = (py0, y1, ptext + text)
        else:
            cells.append((y0, y1, text))
    out = [(y0, text) for y0, _y1, text in cells
           if len(text) <= _MAX_CATEGORY_CHARS and not _CJK_SENTENCE_MARK.search(text)]
    return out if len(out) >= 2 else []


# ── the period a MOVEMENT row belongs to ─────────────────────────────────────────────────────────
#
# An asset note's columns are asset CLASSES and its comparative year is a second block of ROWS, so
# the year is printed on the block. Two devices do it, and both appear in the corpus:
#
#   * A CAPTION-ONLY HEADER OPENS a block — "For the year ended 31 December 2024" printed above the
#     rows it governs, carrying no figures of its own. (right-of-use note 16, 2025041600195.pdf)
#   * A BALANCE ANCHOR CLOSES one — "At 31 December 2023" printed below the movements it completes,
#     carrying the closing balance per class. (property, plant and equipment note 15, same filing)
#
# The two read in opposite directions, which is why the distinction is drawn on whether the row
# carries figures rather than on its wording. Getting it backwards is not a near miss: in note 15
# the accumulated-depreciation block opens "At 1 January 2023" and the charge row beneath it is the
# 2023 charge, while the NEXT charge row — after the 2023 closing balance — is 2024's. An
# opens-above rule applied to a closing anchor would label both 2023.
#
# A SHORT CAPTION ONLY. "At the end of the reporting period the Group had contracted for …" begins
# like an anchor and states a year, so prose is excluded by length; every anchor measured is under
# forty characters.
# The word boundary guards the LATIN arms only — "Additions" must not match the "at" arm. It cannot
# guard the Han ones: there is no boundary between two Han characters, so `\b` after 截至 would
# never match the date that follows it.
_PERIOD_PREFIX = re.compile(
    r"^\s*(?:(?:as\s+at|at|for\s+the\s+(?:year|period)\s+ended?"
    r"|(?:year|period)\s+ended?)\b|於|于|截至)", re.I)
_YEAR_IN_CAPTION = re.compile(r"(?:19|20)\d{2}")
# A YEAR WRITTEN IN HAN NUMERALS, which is how a Traditional-Chinese filing dates its blocks:
# 於二零二四年十二月三十一日. Measured on the corpus, SEVEN anchors are written this way and the
# Arabic-numeral pattern above matches none of them — so on that filing the block periods resolved
# to nothing and the movement rows went unattributed, which is the same silent gap this whole pass
# exists to close, in the other script.
#
# FOUR DIGITS, EXACTLY, and anchored to 年. A year is always written out digit by digit
# (二零二四, 一九九八) rather than as a compound number, so there is no 二千零二十四 to parse and no
# arithmetic to do — 十 appears in a Han DATE only in the month and day (十二月三十一日), which is
# why matching a run of digit characters immediately before 年 cannot pick one up.
_HAN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
               "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_HAN_YEAR_IN_CAPTION = re.compile(r"([〇零一二三四五六七八九]{4})\s*年")
_MAX_ANCHOR_CHARS = 48


def _year_of(caption: str) -> int | None:
    """The year a period caption names, in either script, or None.

    Arabic first because it is the common case and unambiguous; the Han reading is tried only when
    that finds nothing, so a caption carrying both ("二零二三年 (2023)") resolves once.

    THE FIRST YEAR IN THE CAPTION WINS, and one shipped anchor makes that a decision rather than an
    accident: 於二零二三年十二月三十一日及二零二四年一月一日 names the closing balance of one year and
    the opening of the next in a single row. As a CLOSING anchor it governs the rows above it, and
    those belong to the year that just ended — the first of the two.
    """
    arabic = _YEAR_IN_CAPTION.search(caption or "")
    if arabic is not None:
        return int(arabic.group(0))
    han = _HAN_YEAR_IN_CAPTION.search(caption or "")
    if han is None:
        return None
    value = 0
    for ch in han.group(1):
        value = value * 10 + _HAN_DIGITS[ch]
    # A four-digit run that is not a plausible reporting year is not a year: 零零零零 is not 0 AD.
    return value if 1900 <= value <= 2100 else None
# A row sits "at or below" an anchor within a line of it — the same slack `_category_for` allows,
# and for the same reason: a caption and the figures it governs are not always on one baseline.
_ANCHOR_TOL = 0.004
# The total has to equal the rest of the row to within this share of itself. Not exact equality:
# a filing rounds each class to the printed unit and the total to the same unit, so the column can
# be off by a few units of the last digit without the row being anything other than a total.
_TOTAL_TOLERANCE = 0.005


# A TOKEN THAT IS PART OF A DATE, not a figure. Needed because a date's own numerals are read as
# values: on right-of-use note 16 the row "As at 31 December 2024" reaches `_scan_row` as the label
# "As at" and the values 31 and 2024, so the caption loses its year and the row looks — to a bare
# "does it carry figures" test — exactly like a closing balance. Property, plant and equipment note
# 15 keeps the same date inside its label, because its label column is wide enough to hold it. The
# anchor text is therefore read off the WHOLE row, and a row "carries figures" only where something
# survives this filter.
_DATE_TOKEN = re.compile(
    r"^(?:\d{1,2}(?:st|nd|rd|th)?|(?:19|20)\d{2}"
    r"|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?"
    r"|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r"|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|年|月|日)$", re.I)


def _period_anchors(rows: list[list[Word]], fmt=None) -> list[tuple[float, int, bool]]:
    """``(top y, year, carries_figures)`` for every row of this note that states a period.

    ``carries_figures`` is what tells an opening header from a closing balance, and therefore which
    direction the year applies in — see this block's comment.
    """
    out: list[tuple[float, int, bool]] = []
    for row in rows:
        if not row:
            continue
        _labels, _refs, value_words = _scan_row(row, fmt, extract_note_refs=False)
        figures = [w for w in (value_words or ())
                   if not _DATE_TOKEN.match(w.text.strip().strip(",.()"))]
        # THE CAPTION IS THE ROW WITHOUT ITS FIGURES — and WITH its date, wherever the date landed.
        # Reading the whole row would put a closing balance's six class amounts in the caption and
        # push it past the length guard, which is how the PP&E anchors were missed; reading only the
        # label words would drop the date on a narrow table, which is how the right-of-use anchors
        # were missed. Excluding exactly the real figures is the one rule that holds for both.
        skip = {id(w) for w in figures}
        caption = " ".join(w.text for w in sorted(row, key=lambda w: w.bbox.x0)
                           if id(w) not in skip).strip()
        if not caption or len(caption) > _MAX_ANCHOR_CHARS \
                or not _PERIOD_PREFIX.match(caption):
            continue
        # BOTH SCRIPTS, through one reader — see `_year_of`.
        year = _year_of(caption)
        if year is None:
            continue
        out.append((min(w.bbox.y0 for w in row), year, bool(figures)))
    out.sort()
    return out


def _period_year_for(anchors: list[tuple[float, int, bool]], y: float | None) -> int | None:
    """The year governing the row printed at ``y``, or None when this note states none.

    Opening headers win where a note has them: a note that captions its blocks says so directly,
    and a closing balance in the same note is then just one of the rows that block governs.
    """
    if not anchors or y is None:
        return None
    opens = [(top, year) for top, year, has_figures in anchors if not has_figures]
    if opens:
        found: int | None = None
        for top, year in opens:
            if top <= y + _ANCHOR_TOL:
                found = year
            else:
                break
        if found is not None:
            return found
    # No header opened this block, so the first closing balance BELOW the row completes it.
    for top, year, has_figures in anchors:
        if has_figures and top >= y - _ANCHOR_TOL:
            return year
    return None


def _period_hint_for(anchors: list[tuple[float, int, bool]], newest: int | None,
                     y: float | None) -> str:
    """"current" / "prior" for the row printed at ``y``, or "" when the note states no period.

    ONLY THE TWO SLOTS THE STATEMENTS HAVE. A note showing three years would put its oldest in
    neither, and "" is the right answer for it: a figure with nowhere to go must not be published
    into the nearest slot.
    """
    if newest is None:
        return ""
    year = _period_year_for(anchors, y)
    if year is None:
        return ""
    if year == newest:
        return "current"
    return "prior" if year == newest - 1 else ""


def _total_slot_of(li) -> str:
    """The ``period_label`` of the value on this row that totals the others, else "".

    ARITHMETIC, NOT A HEADER MATCH. The total is the figure equal to the sum of the rest of the
    row. That holds in every script and survives a layout that omits the word "total" — and it is
    self-checking, where a header match that picked the wrong column would publish one asset
    class's charge as the whole charge with nothing to contradict it.

    Three values minimum, so a two-column row whose component happens to equal its total cannot be
    read either way round.
    """
    facts = [ev for ev in li.values.values() if getattr(ev, "value", None) is not None]
    if len(facts) < 3:
        return ""
    amounts = [Decimal(str(ev.value)) for ev in facts]
    whole = sum(amounts)
    for i, ev in enumerate(facts):
        rest = whole - amounts[i]
        if rest == 0:
            continue
        if abs(amounts[i] - rest) <= abs(rest) * Decimal(str(_TOTAL_TOLERANCE)):
            return str(getattr(ev, "period_label", "") or "")
    return ""


def _row_top(li) -> float | None:
    """Where a reconstructed row was printed, in page-normalised y.

    Its LABEL's top edge when there is one, and its first figure's otherwise: a row whose caption
    wrapped over the line above its figures is anchored by the caption it is captioned with.
    """
    for ev in li.values.values():
        prov = ev.provenance
        if prov is None:
            continue
        box = prov.label_bbox or prov.bbox
        if box is not None:
            return box.y0
    return None


def _category_for(cells: list[tuple[float, str]], y: float | None) -> str:
    """The category cell governing the row printed at ``y`` — the last one that opens at or
    above it. A row above the first cell belongs to no category, not to the first one."""
    if not cells or y is None:
        return ""
    found = ""
    for top, text in cells:
        if top <= y + 0.004:            # within a line of the cell's own top edge
            found = text
        else:
            break
    return found


def extract_note_tables(words: list[Word], *, page_index: int, document_id: str | None,
                        source_kind: str, scope=None,
                        normalisation=None, llm_provider=None,
                        ai_required: bool = False,
                        carry_note: tuple[str, str] | None = None,
                        log=None,
                        carry_grid: ColumnGrid | None = None,
                        grid_out: list[ColumnGrid | None] | None = None,
                        chapter: list | None = None,
                        known_captions: frozenset[str] | None = None) -> list[NotesTable]:
    """Split a notes page into note sections and reconstruct each note's detail rows.

    ``scope``/``normalisation`` are the run's own rulebook blocks; a note's columns are read by
    the same rules as the face it supports, or the note→face tie compares figures taken from
    different columns.

    ``llm_provider`` and ``ai_required`` remain accepted only for caller compatibility. Note
    tables are reconstructed exclusively from positioned source tokens. There is NO AI fallback
    for a matrix this reconstructor cannot fit, and that absence is deliberate rather than an
    oversight: the `extraction.llm_note_structuring` flag and the `note_structure_llm` service
    that advertised one were deleted, never having had a reader or a caller in any tree. A
    wider note is handled here or nowhere — see ``carry_grid`` for the two-level column grid and
    ``_category_for`` for a merged category cell.

    ``carry_note`` is the (number, title) of the note still open when the PREVIOUS page ended,
    for the caller to pass through page by page. Some filings print a note's own footnote legend
    (the explanations behind its "*"/"^"/"#" markers) a page or more after its table, with no
    heading of its own — prose that opens the page with no note number to claim it. Without a
    carry, that prose has nowhere to attach and is silently dropped from every page it opens
    before the next real heading. Seeding ``current`` with the carried note lets it attach
    instead, exactly as it would if the page break were not there.

    ``log`` is the run log. It was NOT passed through before, and that silence was a real gap: the
    reconstructor logs every scope decision it takes — which column it read as the current period,
    the unit it resolved, and now the two-level column grid — and on a filing whose figures are
    almost all in its notes (a PRC annual report), none of those decisions appeared in the run
    record at all.

    ``chapter`` is the notes' TOP-LEVEL CHAPTER, carried across pages as a one-element
    ``[numeral, ordinal]`` cell the caller owns — a chapter heads a run of pages and is printed
    once, and the strictly-increasing rule that identifies one needs the highest ordinal seen so
    far. Passing it in makes every note number on the page chapter-qualified; passing None keeps
    the bare numbers an English filing has always had. See ``read_chapter``.

    ``carry_grid`` is the two-level column grid read from an earlier page of the note still open,
    and ``grid_out`` collects the grid each section was read with so the caller can carry it to
    the next page. A PRC related-party note prints its 期末余额{账面余额|坏账准备} header once and
    then runs for eight pages; without the carry every page after the first reads four columns
    positionally, which is the mis-load the grid exists to prevent.
    """
    # The same page-derived tolerance the face uses: a note's detail lines are set as tightly as a
    # statement's, and two of them merged into one row interleave their captions (row_reconstruct.
    # row_tolerance). A note whose caption comes out scrambled ties to nothing.
    rows = _group_rows(words, row_tolerance(words, source_kind))
    # READ BEFORE THE SECTIONS ARE WALKED, because a chapter heading is a label-only row and the
    # walker would otherwise fold it into whichever note is open — it reaches the
    # ``current["words"].extend(row)`` arm and disappears into that note's text. The chapter is
    # orthogonal to the note number, so it changes nothing about where a section starts.
    if chapter is not None:
        found = read_chapter(rows, chapter[1] if chapter[0] else 0)
        if found is not None:
            chapter[0], chapter[1], chapter[2] = found
            if log:
                log(f"notes:page={page_index}:chapter={found[0]}、{found[2]}({found[1]})")
    chapter_numeral = chapter[0] if chapter else None
    # A COMPANY-ONLY NOTE, from the chapter that holds it. The heading is printed once, pages
    # before the note itself, so the chapter is the only thing on the page that can say so — and
    # the concepts these notes state are the same ones the group's notes state.
    chapter_basis = (Basis.STANDALONE
                     if chapter and len(chapter) > 2 and chapter[2]
                     and _COMPANY_CHAPTER.search(chapter[2]) else None)
    sections: list[dict] = []
    current: dict | None = None
    # Not appended to ``sections`` until it actually claims a row — a page that opens straight
    # onto a real heading must not leave a spurious empty table behind under the OLD note number.
    carried: dict | None = None
    if carry_note is not None:
        carried = {"no": carry_note[0], "title": carry_note[1], "words": []}
        current = carried
    i = 0
    while i < len(rows):
        row = rows[i]
        head = _is_heading(row)
        if head is not None:
            no, title = head
            current = {"no": no, "title": title, "words": []}
            sections.append(current)
        elif current is None:
            no = _bare_note_number(row)
            if no is not None:
                title = ""
                if i + 1 < len(rows):
                    next_title = _title_only_row(rows[i + 1])
                    if next_title is not None:
                        title = next_title
                        i += 1
                current = {"no": no, "title": title, "words": []}
                sections.append(current)
        elif current is not None:
            if current is carried and carried not in sections:
                sections.append(carried)
            current["words"].extend(row)
        i += 1

    tables: list[NotesTable] = []
    for sec in sections:
        # ``on_face=False``: the rulebook's ``company_only_markers`` rule is declared about the
        # FACE ("presence of …investments_in_subsidiaries on the face is strong evidence the
        # column is company-only"). A note IS where that caption is normally printed, and reading
        # it here would relabel the note of a consolidated statement as the Company's — breaking
        # the note→face tie, which matches on (basis, period).
        seen: list[ColumnGrid | None] = []
        # ONLY THE CARRIED SECTION INHERITS A GRID. A section that starts on its own heading is a
        # different table, and letting it borrow the previous note's header would hand a four-column
        # grid to whatever four columns the next note happens to print — the same wrong-column
        # failure this closes, in the opposite direction.
        items, _ = build_line_items(sec["words"], page_index=page_index,
                                    document_id=document_id, source_kind=source_kind,
                                    on_face=False, scope=scope, normalisation=normalisation,
                                    log=log,
                                    column_grid=(carry_grid if sec is carried else None),
                                    grid_out=seen, known_captions=known_captions)
        # What the NEXT page inherits is the grid of the note still open when this page ended, so
        # the carry is whatever the last section was read with — None included.
        carry_grid = seen[0] if seen else None
        if grid_out is not None:
            grid_out.append(carry_grid)
        if not items and not sec["title"]:
            continue
        table = NotesTable(note_number=qualified_note_number(chapter_numeral, sec["no"]),
                           title=sec["title"], basis=chapter_basis, source_pages=[page_index],
                           # THE CHAPTER'S OWN HEADING, carried rather than discarded. `read_chapter`
                           # returns `(numeral, ordinal, title)` and the title was used only for the
                           # 母公司 basis test above and for a log line, so a `note_source` naming
                           # the chapter — which is how a human says where a figure lives — matched
                           # nothing. See `NotesTable.chapter_title`.
                           chapter_title=(chapter[2] if chapter and len(chapter) > 2
                                          else "") or "",
                   source_text=" ".join(word.text for word in sec["words"]).strip())
        # The note's own 项目名称 column, if it has one — see `_category_cells` for the nine-row
        # block whose eight continuation rows named no receivable class at all without it.
        grouped = _group_rows(sec["words"], row_tolerance(sec["words"], source_kind))
        cells = _category_cells(grouped)
        # THE PERIOD STATED ON A BLOCK rather than on a column — read from the RAW rows, because the
        # caption-only header that opens a block carries no figures and so is never built into an
        # item. Same shape as the category pass above: scan the page's own rows, then attribute each
        # reconstructed row by where it was printed.
        anchors = _period_anchors(grouped)
        # The latest year the note names is its current period. Decided per note, not from the
        # document, because a note is self-contained about which years it shows and a filing whose
        # face period was misread would otherwise mislabel every movement row.
        newest = max((year for _y, year, _f in anchors), default=None)
        for li in items:
            # THE CAPTION DECIDES THE ROLE, EXCEPT WHERE THE BUILDER ALREADY KNEW. Almost every
            # row reaches here as ``LINE`` — the promotion that classifies a face row runs in
            # ``map_ontology``, which never sees a note — so the printed caption is what is left to
            # read it from. The one exception is a row whose caption the builder SYNTHESISED: a
            # note's block subtotal is printed on a bare line, and ``row_reconstruct`` gives it the
            # block's heading and the SUBTOTAL role, because the synthesised caption cannot be
            # re-read to recover what the geometry told it. ``li.role`` therefore wins whenever it
            # says anything other than LINE.
            role = li.role if li.role is not LineRole.LINE else note_row_role(li.source_label)
            ni = NoteItem(raw_label=li.source_label, ordinal=li.ordinal, role=role,
                          # Carried, not re-derived: the caption on such a row is indistinguishable
                          # from a row the filing captioned itself, and the note-internal arithmetic
                          # check only acts on the rows that claim to total the ones above them.
                          caption_borrowed=li.caption_borrowed,
                          # The rows the builder counted, so the arithmetic check does not have to
                          # guess them from a caption that is not an identity.
                          component_ordinals=list(li.component_ordinals),
                          section_hint=li.section_hint,
                          group_hint=(li.group_hint
                                      or _category_for(cells, _row_top(li))),
                          # Both empty unless this note states a period on its blocks AND this row
                          # has a column that totals the others — a movement row and nothing else.
                          period_hint=_period_hint_for(anchors, newest, _row_top(li)),
                          total_slot=_total_slot_of(li),
                          provenance=li.values and next(iter(li.values.values())).provenance or None)
            for ev in li.values.values():
                ni.set_value(ev)
                # The two-level-header call-out travels on the ROW as well as the value, because
                # the notes payload serves the row's flags and the note pane reads them; a value
                # flag alone would be raised where nothing on this screen looks for it.
                if GRID_FLAG in ev.confidence.flags and GRID_FLAG not in ni.confidence.flags:
                    ni.confidence.flags.append(GRID_FLAG)
            table.items.append(ni)
        tables.append(table)
    return without_empty_duplicates(tables)


def without_empty_duplicates(tables: list[NotesTable]) -> list[NotesTable]:
    """Drop a fragment that carries no rows when another fragment of the SAME note carries some.

    A note continued onto the next page is picked up by seeding a carried section with the
    previous page's number and title, which materialises on the first row that follows. On a
    filing whose notes pages carry a running header — "Notes to the Financial Statements" — that
    first row is the header, and when the note's own "(Continued)" heading comes next the carried
    section is left holding nothing but the furniture. The result was a third fragment for a
    two-page note, titled like the note and empty, which inflates the note count and can be
    served as a note with no content.

    Keyed on having no ITEMS rather than on recognising the furniture, because "what is a running
    header" is a per-filing question and this is not: a fragment with no rows, for a note that has
    rows elsewhere, adds nothing to the note however it came about.

    A note that is entirely narrative keeps its fragment — it is the only one for its number, so
    there is nothing for it to be a duplicate of.

    CALLED OVER THE WHOLE DOCUMENT as well as per page, because the fragments it has to compare
    are not always produced together. The carry that continues a note onto the next page is
    threaded across pages by ``services.pdf_extract``, so an empty fragment can be raised on one
    page while the fragment with the rows was raised on the previous one — and a per-page pass
    never sees the pair. That is not hypothetical: a backmatter page classified as notes
    ("Corporate Information") inherited the carry from the last real note and published a second,
    empty fragment under its number.
    """
    with_items = {t.note_number for t in tables if t.items}
    return [t for t in tables
            if t.items or t.note_number not in with_items or _carries_prose(t)]


# A FOOTNOTE LEGEND HAS NO ROWS AND IS NOT FURNITURE. The "*"/"^"/"#" explanations behind a note's
# markers, and its "(a)"/"(i)" narrative paragraphs, are routinely printed a page after the table
# they belong to — which is why the section builder above deliberately keeps a titled but
# item-less section. Figures are STATED in that prose and nowhere else: the depreciation charged
# to "other operating expenses", and a guarantee's amount, are routinely printed in a legend or a
# lettered paragraph rather than in any row. Everything downstream can only read what this
# function keeps, so dropping such a fragment does not merely inflate or deflate a note count, it
# deletes a reported number — emptiness of ROWS alone cannot be the test.
#
# Furniture is what the test above was reaching for: a carried section left holding the running
# header, whose text is nothing beyond the note's own title. That is what this predicate separates
# — presence of legend/narrative structure, or simply more prose than the heading accounts for.
_LEGEND_MARKER = re.compile(r"(?m)^\s*(?:[*^#@†‡]{1,2}|\((?:[a-z]|[ivx]{1,4})\))\s+\S")


def _carries_prose(table: NotesTable) -> bool:
    """Whether a row-less fragment holds content, rather than page furniture."""
    text = (table.source_text or "").strip()
    if not text:
        return False
    if _LEGEND_MARKER.search(text):
        return True
    body = text.replace(table.title or "", "").strip()
    return len(body) >= 200
