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
from app.services import page_spread
from app.services.row_reconstruct import (
    _AGE_UNIT, _CAPTION_GAP, DATE_TOKEN, DETECT_FOLD, GRID_FLAG, NIL_ROW_FLAG, ColumnGrid, Word, _caption_text,
    _carries_amounts, _fuse_bucket_numbers,
    _group_rows, _is_folio_row, _measure_slug, _opens_its_own_line, _prc_period_slot, _scan_row,
    _x_runs,
    build_line_items, row_tolerance)

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
# A capacity or rating written against its number with no space — 99MW, 2X1000MW, 400V, 110kV.
_GLUED_UNIT = re.compile(r"^\d{1,4}(?:[xX×]\d|[kKMG]?W[hp]?|[kK]?VA?)(?![A-Za-z])")
# HOW FAR A MAINLAND NOTE NUMBER MAY STEP FORWARD from the note still open in its chapter. Real
# notes rise one at a time, skipping where a heading went unread (000883's 七、79 and 七、80 sit on
# pages read as a cash-flow face, so 七、78 is followed by 七、81) or where an empty note publishes
# nothing; measured over the four CAS filings no real step exceeds six.
_NOTE_STEP_MAX = 10

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


def _is_bracketed(row: list[Word]) -> bool:
    """Whether a heading row is numbered in brackets or with a circled numeral — （1）, (2), ① —
    or with a CLOSING bracket alone — 1）, 2) — the levels a mainland note uses for the tables
    INSIDE it, not for notes. The half-bracket level is the one below （1）: 300319 prints
    "6、其他应收款" → "（1）其他应收款" → "1）其他应收款按款项性质分类情况" / "2）按账龄披露", and read as
    note numbers those became 七、1 and 七、2 — the ageing table filed under 交易性金融资产."""
    text = " ".join(w.text for w in row).strip().translate(_CIRCLED_DIGITS)
    return bool(re.match(r"^(?:[（(]\s*\d|\d{1,2}\s*[)）])", text))


def _follows_open_note(open_note: str | None, number: str, chapter: str | None = None) -> bool:
    """Whether heading ``number`` can be the note after ``open_note`` in the same chapter — strictly
    greater, by at most ``_NOTE_STEP_MAX``. True when no note is open, its number is unreadable, or
    it was opened in another chapter than ``chapter`` (a note carried onto the page where the next
    chapter's numbering has already begun — 300319's 十七、1 before 十八's first note)."""
    opened_in = str(open_note or "").rpartition("、")[0]
    if opened_in and chapter and opened_in != chapter:
        return True
    current = re.search(r"(\d{1,3})$", str(open_note or ""))
    if current is None or not str(number).isdigit():
        return True
    step = int(number) - int(current.group(1))
    return 0 < step <= _NOTE_STEP_MAX


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


# THE BAND A TABLE OPENS WITH — the period labels and the currency/units line. It carries no value
# column, so `_title_only_row` accepts it as a title, which is right for the job that function was
# written for and wrong for deciding whether a heading CONTINUES. Used only by the wrap join below.
_TABLE_BAND = re.compile(
    r"^[\s\d,.()'\u2019\u5e74\u6708\u65e5/\u2010-\u2015-]+$"
    r"|^(?:RMB|HK\$|US\$|\u4eba\u6c11\u5e63|\u6e2f\u5143|\u5343\u5143|\u5104)",
    re.IGNORECASE)

# HOW LONG A WRAPPED HEADING'S SECOND LINE CAN BE. A heading is a NAME, so its continuation is a
# few words; the first line of a note's PROSE is a sentence. Measured on the reference filings the
# real continuations are 22 and 26 characters ("THROUGH PROFIT OR LOSS 入損益的金融資產") and the
# prose openers this refuses run from 40 to well over 100.
_WRAP_MAX = 34

# A CONTINUATION IS SET IN HEADING STYLE, AND THIS IS THE TEST THAT MATTERS MOST.
#
# MEASURED, AND THE FIRST VERSION OF THE JOIN GOT IT WRONG IN BOTH DIRECTIONS. Accepting any
# `_title_only_row` swallowed the opening PROSE line of a CAS note into its title — 000709's 五、23
# became "、 预计负债 如果与或有事项相关的义务同时符合以下条件，本公司将其确认为预计负债：" — and
# then patterns matched words inside that sentence: six unrelated contingent-liability lines
# claimed the provisions POLICY note, 44 such pairs on 000709 and 46 on 300319. It also LOST four
# real pairs per CAS filing, the related-party receivable and payable lines, because an anchored
# pattern no longer reached a title with a sentence appended.
#
# It also made kaming's six depreciation lines match note 8 — which reads like a win and is the
# same defect: the swallowed line was "Loss before taxation is arrived at after charging", and
# `sub__pbt_depreciation` happens to name `arrived\s+at\s+after\s+charging`. Matching a note
# because its PROSE was pasted into its heading is luck, not a fix, so that gap is closed in
# configuration instead — by naming the spelling a filing at a loss actually prints.
#
# So: the Latin letters of a continuation, if it has any, must be UPPERCASE, the way these filings
# set a heading. That admits "THROUGH PROFIT OR LOSS" and refuses every sentence above.
_LOWER_LATIN = re.compile(r"[a-z]")

# AND BOTH LINES MUST BE BILINGUAL, which is the discriminator the second version of this was
# missing and the one that explains the defect rather than merely filtering it.
#
# WHY THE TRUNCATION HAPPENS AT ALL: an HKEX filing sets the English and the Han in two columns, so
# one printed heading arrives as ONE row carrying both — and when that heading is too long for its
# column it wraps, leaving half of each language on the next row. A CAS filing sets one language in
# one column, so its headings do not wrap and there is nothing here to repair.
#
# MEASURED, and this is what the caps-and-length guard alone still got wrong: on the CAS filings it
# swallowed the COLUMN HEADER BAND and the CSRC applicability marker, neither of which is a
# sentence and both of which are short — 七、13 became "、 使用权资产 项目 房屋及建筑物 机器设备
# 土地使用权 合计" and 七、13 on 688008 became "其他流动资产 √适用□不适用" — 57 titles on 000709
# and 69 on 300319, and it cost the four related-party receivable and payable pairs on each because
# their patterns are anchored. Requiring a Latin run on both lines refuses every one of those and
# keeps the four real wraps: 1966's notes 24, 26 and 29 and kaming's note 19.
_LATIN = re.compile(r"[A-Za-z]")
_HAS_HAN = re.compile(r"[\u3400-\u9fff]")


# CJK SENTENCE punctuation. A note's title is a name and carries none of it; the enumeration comma
# 、 is a different mark and DOES appear in real titles ("收益、其他收入及收益", "現金及現金等價物、
# 受限制現金"), so it is deliberately absent from this class.
_CJK_SENTENCE = re.compile(r"[，。；]")
# A DAY OF THE MONTH IS NOT A NOTE NUMBER. "30 June 2022 as a result of the declaration of the
# final dividend" and "31 December 2024, the balance of subordinated tranches" open lines of
# Far East Horizon's notes and read as notes 30 and 31 — under the numbers of its real 30 TRADE AND
# BILLS PAYABLES and 31 OTHER PAYABLES AND ACCRUALS, whose heading scores the fragments then
# shared. No note is titled with a month.
_MONTH_TITLE = re.compile(r"^(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|"
                          r"july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
                          r"dec(?:ember)?)\b", re.IGNORECASE)


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
    found = chapter_headings(rows, seen)
    return found[-1][1] if found else None


def notes_below_the_face(words: list[Word], source_kind: str, fold, seen: int) -> float | None:
    """Where a FACE page's notes begin — the top of the first chapter heading printed below the
    page's last row of figures — or None when the statement runs to the foot of the page.

    THE PAGE THE NOTES OPEN ON CAN BE A STATEMENT'S LAST PAGE. 000883 ends its company
    statement of changes in equity on PDF page 122 and prints 三、公司基本情况, 四、财务报表的编制基础
    and 五、重要会计政策及会计估计 beneath it; the page is a face, so the notes reader never saw those
    headings, and the 44 accounting-policy sections that follow came out as bare notes "2" to
    "44" under no chapter. A statement's own numbered lines (一、经营活动产生的现金流量：) are
    followed by rows of figures, so only a heading BELOW the last figure can open the notes, and
    `chapter_headings`' strictly-increasing rule still has to accept it. A filing that prints no
    chapters has none to find.
    """
    rows = _group_rows(words, row_tolerance(words, source_kind), fold=fold)
    # The page's folio is printed below everything, and on 000883's page 122 it was the only
    # "figure" on a page of notes text the classifier had called a face.
    last = max((i for i, r in enumerate(rows) if _carries_amounts(r) and not _is_folio_row(r)),
               default=-1)
    # A STATEMENT'S OWN BANNER CAN ALSO SIT BELOW ITS LAST FIGURE. 300319's company income
    # statement ends on "七、每股收益：" over two EPS lines it leaves blank, and read as chapter 七 it
    # put the real 七、合并财务报表项目注释 out of reach. A banner ends in a colon, and a statement's
    # blank lines are captions, not sentences: a chapter opening is followed by prose.
    for index, (_numeral, _ordinal, title) in chapter_headings(rows, seen):
        if index <= last or title.rstrip().endswith(("：", ":")):
            continue
        if any(_CJK_SENTENCE.search(" ".join(w.text for w in r)) for r in rows[index + 1:]):
            return min(w.bbox.y0 for w in rows[index])
    return None


def chapter_headings(rows: list[list[Word]], seen: int) -> list[tuple[int, tuple[str, int, str]]]:
    """Every top-level chapter heading on this page, IN ORDER, as ``(row index, chapter)``.

    `read_chapter`'s rule — strictly increasing ordinals, no figures on the row, no sentence
    punctuation in the title — applied to each heading rather than only the last, because a page
    can END one chapter and OPEN the next, and the sections above the heading belong to the first.

    MEASURED, and it is why the position matters: 澜起科技 688008 prints its related-party balances
    (6. 应收、应付关联方等未结算项目情况, page 242) above the heading 十五、股份支付 on the same page.
    Numbered by the page's LAST chapter, 应收项目 and 应付项目 became 十五、1 and 十五、2 — share-based
    payment's numbers, sharing a key with 各项权益工具 — and 迈捷 300319's did the same under
    十四、股份支付.
    """
    out: list[tuple[int, tuple[str, int, str]]] = []
    for index, row in enumerate(rows):
        _, _, values = _scan_row(row)
        if values:
            continue                       # a row with figures is a detail line, not a heading
        text = " ".join(w.text for w in row).strip()
        match = _CHAPTER_HEADING.match(text)
        if match is None:
            continue
        ordinal = chapter_ordinal(match.group("ch"))
        if ordinal is None or ordinal <= max(seen, out[-1][1][1] if out else 0):
            continue
        title = match.group("title").strip()
        if title and _CJK_SENTENCE.search(title):
            continue                       # a sentence that happens to open with a numeral
        out.append((index, (match.group("ch"), ordinal, title)))
    return out


# THE NAMESPACE OF A CHAPTER-NUMBERED SECTION READ BEFORE THE FINANCIAL STATEMENTS — a governance
# chapter on a page the classifier called notes (`pdf_extract` clears the chapter cell on the first
# balance-sheet page). Not a chapter numeral, so `split_note_number` reads such an identity as
# unchaptered, and nothing in the notes, which are numbered afresh from the statements, shares it.
BEFORE_THE_STATEMENTS = "front:"


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


# THE COLUMN HEADER AN IND AS NOTE TABLE OPENS WITH: "Particulars | As at 31 March 2025 | …",
# "Particulars | Not due | Less than 6 months | …".
_SUB_TABLE_HEADER = re.compile(r"^\s*particulars\b", re.IGNORECASE)
# THE COUNTERPARTY COLUMN THAT HEADS A MAINLAND RELATED-PARTY TABLE. A CAS 关联交易情况 note prints
# its tables one after another under ONE bracketed heading — "（1）购销商品、提供和接受劳务的关联
# 交易" holds 采购商品/接受劳务情况表 (关联方 | 关联交易内容 | 本期发生额 | 获批的交易额度 | 是否超过
# 交易额度 | 上期发生额) and then 出售商品/提供劳务情况表 (关联方 | 关联交易内容 | 本期发生额 |
# 上期发生额) — and each opens on a header whose first cell names the counterparty. Read as one
# section, the second table took the first one's six-column grid: on 002273 every related-party
# SALE was filed under 获批的交易额度, the approved limit, and on 000685 under 是否超过交易额度.
_COUNTERPARTY_TABLE_HEADER = re.compile(
    r"^(?:关联方|關聯方|承租方|出租方|被担保方|担保方|被擔保方|擔保方)(?:名称|名稱)?$")
# The line that names such a table — "出售商品/提供劳务情况表", "本公司作为出租方：" — so the new
# section is titled by the table it holds rather than by the heading the two tables share.
_TABLE_NAME = re.compile(r"(?:情况表|情況表|作为(?:出租方|承租方|担保方|被担保方)|作為(?:出租方|承租方|擔保方|被擔保方))[:：]?$")


def _opens_a_sub_table(row: list[Word]) -> bool:
    """Whether this figure-less row is the column header of a NEW table inside the open note.

    f1 note 10 prints the receivables table (two years across), then an ageing schedule per year
    (seven buckets across), each under its own "Particulars …" header and none under a numbered or
    bracketed heading. Read as one section they shared one set of value bands: the two-year
    columns and the seven buckets were counted together into nine "column N" keys and no bucket
    could be named. A header row that follows rows carrying figures opens its own section of the
    same note, so each table is read with its own bands and its own printed columns.
    """
    words = sorted(row, key=lambda w: w.bbox.x0)
    if not words:
        return False
    # A COUNTERPARTY HEADER CAPTIONS OTHER COLUMNS TOO (关联方 | 关联交易内容 | 本期发生额 …). A line
    # that is the word alone is a wrapped caption — 600990's "合并范围内 / 关联方" row, whose second
    # line opened a section in the middle of its 坏账准备 movement table.
    counterparty = (_COUNTERPARTY_TABLE_HEADER.match(words[0].text.strip()) is not None
                    and len(words) >= 3)
    if not (_SUB_TABLE_HEADER.match(words[0].text) or counterparty):
        return False
    # Its own bucket digits ("Less than 6 months") and dates ("As at 31 March 2025") are not figures.
    return not _carries_amounts(_fuse_bucket_numbers(row))


#: A running paragraph's lines, as `running_text_words` reads them.
_RUN_MIN_WORDS = 6          # a line of a sentence, not a caption with a figure or two
_RUN_FULL_WIDTH = 0.85      # of the section's own extent: the line is wrapped, not a caption cell
_RUN_GAP_RATIO = 3.0        # the widest space on the line against its typical one
_RUN_INDENT = 0.02          # how far a continuation line may start from its paragraph's left edge
_SENTENCE_END = (".", ":", ";", "\u3002")


def _even_spacing(row: list[Word]) -> bool:
    """Whether a printed line is ONE run of words — no column gap anywhere along it.

    Measured relative to the line itself, never in page units (the coordinates are normalised per
    axis, so a gap cannot be compared with a height): a table row puts a column's width between
    its caption and its figures, many times the space between two words of the caption, while a
    justified sentence spaces every word alike.
    """
    words = sorted(row, key=lambda w: w.bbox.x0)
    gaps = [max(b.bbox.x0 - a.bbox.x1, 0.0) for a, b in zip(words, words[1:])]
    if not gaps:
        return True
    # AGAINST THE NARROWEST SPACE, not the median: on an ageing row seven figure columns outnumber
    # the caption's two word spaces, so the median IS a column gap and the row reads as even.
    narrowest = min(gaps)
    if narrowest <= 0:
        return False
    return max(gaps) <= _RUN_GAP_RATIO * narrowest


_LOWER_WORD = re.compile(r"^[(\[]?[a-z][a-z'\u2019\-]*[,.;:)\]]*$")


def _wordy(row: list[Word]) -> bool:
    """The line reads as a sentence: mostly words, and among them lower-case ones.

    A sentence states a figure or two, a table row is mostly them. And a header band can be evenly
    spaced across the full width too — 1966's property note prints nine column headings at a
    regular pitch, "土地及樓宇 物業裝修 …" and a row of "RMB’000" — but it holds no lower-case running
    words, which every line of an English paragraph does.
    """
    words = [w for w in row if any(c.isalpha() for c in w.text)]
    lower = sum(1 for w in row if _LOWER_WORD.match(w.text.strip()))
    return len(words) * 3 >= len(row) * 2 and lower >= 3


def running_text_words(rows: list[list[Word]]) -> set[int]:
    """The ids of the words printed as a RUNNING PARAGRAPH among a note section's lines.

    A paragraph below a table — f1 note 17's "Nature of security and terms of repayment" — is the
    note's prose, not its rows. Read as rows, every wrapped line became one, captioned with the
    middle of a sentence and carrying whatever numbers the sentence happened to state: "(sanctioned
    ₹" {current: 9000.00}, "equal quarterly instalments of ₹" {current: 250.00, prior: 17, col2:
    31}. Those figures then took the value bands, so the TABLE's own current column was keyed
    `current_col1`.

    WHAT MAKES A LINE RUNNING TEXT, which is a question about the printed line and never about its
    words: it holds a sentence's worth of words (`_RUN_MIN_WORDS`), it is evenly spaced
    (`_even_spacing` — no column gap, so a header band "Particulars  As at …  As at …" and a row
    with figures under columns are both refused), and it spans the section's text width, as only a
    wrapped line does. The lines after it continue the paragraph while it wraps: evenly spaced,
    starting at its left edge, and following a line that did not end its sentence — so the short
    last line of a paragraph is in it, and a caption printed beneath a finished paragraph is not.
    A figure-less heading line printed directly above a paragraph is its heading and goes with it.

    The words stay in the section's text: `source_text` and the narrative are read from every word,
    so a paragraph reaches the model as prose, whole, and only stops being rows.
    """
    if not rows:
        return set()
    every = [w for row in rows for w in row]
    left = min(w.bbox.x0 for w in every)
    extent = max(w.bbox.x1 for w in every) - left
    if extent <= 0:
        return set()
    out: set[int] = set()
    run_left: float | None = None
    prev_open = False
    for i, row in enumerate(rows):
        words = sorted(row, key=lambda w: w.bbox.x0)
        even = _even_spacing(words)
        x0 = words[0].bbox.x0
        span = (words[-1].bbox.x1 - left) / extent
        starts = (even and len(words) >= _RUN_MIN_WORDS and span >= _RUN_FULL_WIDTH
                  and _wordy(words))
        continues = (run_left is not None and prev_open and even
                     and abs(x0 - run_left) <= _RUN_INDENT)
        if starts or continues:
            if starts and run_left is None and i > 0:
                above = sorted(rows[i - 1], key=lambda w: w.bbox.x0)
                if (id(above[0]) not in out and _even_spacing(above)
                        and not _carries_amounts(above) and _title_only_row(above) is not None):
                    out.update(id(w) for w in above)
            out.update(id(w) for w in words)
            run_left = x0 if starts and run_left is None else run_left
            prev_open = not words[-1].text.rstrip(")\u201d\u2019\"'").endswith(_SENTENCE_END)
            if starts and not prev_open:
                # A one-line sentence ends its paragraph; the next starts afresh.
                run_left = None
            elif not prev_open:
                run_left = None
        else:
            run_left, prev_open = None, False
    return out


def _wrapped_row_caption(rows: list[list[Word]], i: int, section: dict | None) -> bool:
    """Whether row ``i`` — numbered like a heading — is the FIRST LINE OF A WRAPPED ROW CAPTION
    inside a table, so it belongs to the rows rather than opening a section.

    A CAS movement table sets its captions in a narrow first column, so "（1）计提" prints as
    "（1）计" on one line, the row's figures on the next, and "提" below them. Read as a heading,
    "（1）计" opened a section titled "计", and the figures that followed went with it: 000883's
    固定资产 note lost every one of its bracketed rows — the depreciation charge 2,927,435,003.23
    among them — and 688008 and 002273 lost theirs the same way.

    Three things are true of such a line and not of a real sub-table heading: a table is already
    open (the section holds rows with figures), the very next line carries figures, and that line
    sits within a line's height of it with no caption of its own to the right of the fragment's
    indent — either no caption at all, or one that starts LEFT of the bracket, which is the second
    line of the same wrapped cell ("（4）外" over "币财务报表折算 109,835,296.51 …"). A real heading
    is followed by its column header, which carries no figures.
    """
    if section is None or not rows[i]:
        return False
    if i + 1 >= len(rows) or _is_folio_row(rows[i + 1]):
        return _caption_at_the_foot(rows, i, section)
    row, nxt = rows[i], rows[i + 1]
    # A TABLE CARRIED OPEN FROM THE PREVIOUS PAGE is open before any row of this page: 000883's
    # 固定资产情况 runs onto page 189 with "（1）处" / figures / "置或报废", which opened a note
    # titled "处" and took the impairment block's closing balance and the carrying amounts with it.
    if not section.get("open_table") and not any(
            _carries_amounts(r) for r in _rows_since(rows, i, section)):
        return False
    height = max(w.bbox.y1 - w.bbox.y0 for w in row)
    if min(w.bbox.y0 for w in nxt) - max(w.bbox.y1 for w in row) > 1.5 * height:
        return False
    if not _carries_amounts(nxt):
        # A ROW WITH NO FIGURES THIS YEAR wraps the same way — "（3）企" over "业合并增加" — and its
        # second line is ONE word set left of the bracket. A column header under a real heading
        # is several words across the columns.
        if (len(nxt) == 1 and _title_only_row(nxt) is not None
                and nxt[0].bbox.x0 < row[0].bbox.x0 - 0.01):
            return True
        # …AND A ROW WITH NO FIGURES THIS YEAR, WHOLE, is followed at the row pitch by the next
        # enumerated row: 000883's investment-property table prints "（1）外购" over "（2）存货\…" and
        # "（1）处置" over "（2）其他转" with nothing beside them. Read as headings they opened sections
        # titled "外购" and "其他转", and every row after them — the depreciation charge among them
        # — went to a note so titled. A real heading is followed by its unit line or its header.
        return (re.match(r"^\s*[（(]\s*\d", row[0].text) is not None
                and _title_only_row(nxt) is not None and _opens_its_own_line(nxt))
    first_amount = next((j for j, w in enumerate(nxt) if _carries_amounts([w])), 0)
    caption = nxt[:first_amount]
    if not caption:
        return True
    if _HEADING.match(" ".join(w.text for w in caption).strip()) and re.match(
            r"^\s*(?:[（(]\s*\d|\d)", caption[0].text):
        # …UNLESS THE LINE IS A ROW WITH NO FIGURES, set at the row pitch between two rows of the
        # table: 600990's 固定资产情况 prints "（3）企业合并增加" blank between "（2）在建工程转入" and
        # "3.本期减少金额 1,558,536.45 …", and read as a heading it opened a note titled
        # "企业合并增加" that took the 累计折旧 and 减值准备 blocks. A real heading stands clear of
        # the table above it.
        return (re.match(r"^\s*[（(]\s*\d", row[0].text) is not None
                and _caption_at_the_foot(rows, i, section))
    return caption[0].bbox.x0 < row[0].bbox.x0 - 0.01


def _caption_at_the_foot(rows: list[list[Word]], i: int, section: dict) -> bool:
    """Whether the bracketed line that ENDS a page is the first line of a row caption the next page
    completes — 300319's 固定资产情况 ends page 151 on "（3）企" (业合并增加 opens page 152).

    Read as a heading, it opened a section holding nothing, and the page's carries — the printed
    header, the open block heading — were that empty section's: the next page's 累计折旧 rows went
    out keyed col2 … col4. It is a row when a table is open above it and it is set at the row
    pitch below that table's last line; a real heading stands clear of the table above it.
    """
    row = rows[i]
    if i == 0 or not re.match(r"^\s*[（(]\s*\d", row[0].text):
        return False
    held = _rows_since(rows, i, section)
    if not held or not any(_carries_amounts(r) for r in held[-4:]):
        return False
    prev = held[-1]
    height = max(w.bbox.y1 - w.bbox.y0 for w in row)
    return min(w.bbox.y0 for w in row) - max(w.bbox.y1 for w in prev) <= 1.5 * height


def _rows_since(rows: list[list[Word]], i: int, section: dict) -> list[list[Word]]:
    """The rows before ``i`` whose words are in ``section`` — the rows the open section holds."""
    held = {id(w) for w in section.get("words", ())}
    return [r for r in rows[:i] if r and id(r[0]) in held]


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
    # A NUMBER GLUED TO A UNIT IS A QUANTITY, not a note: 000883's construction-in-progress table
    # names its projects by capacity — "99MW 农光互补", "2X1000MW 超超临界", "400V 厂用电系统" —
    # and a project whose figures sit on the next line read as notes 七、99, 七、2 and 七、400.
    if _GLUED_UNIT.match(text):
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
        if not starts_note and _MONTH_TITLE.match(title) and int(no) <= 31:
            return None
        # "1 年以内" is an ageing bucket, not note 1 — a count and its unit, now kept together as a
        # caption by `_scan_row`, so on a row with no figures it must not open a note instead.
        if not starts_note and _AGE_UNIT.match(title) and not re.match(r"^\s*[（(]", text):
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


#: A grouped amount, as a filing writes one in prose — thousands separators required, which is what
#: keeps years, note numbers and bare counts out. Same shape as `note_sourced._PROSE_AMOUNT`, which
#: is what will read the sentence this decides to keep.
_PROSE_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def _amount_is_inline(row: list[Word]) -> bool:
    """Whether this printed line states a grouped amount INSIDE a sentence rather than in a column.

    A table row's figures are the rightmost things on its line, because they sit under the value
    columns. A sentence states its figure and carries on — "…HK$529,841,000 (2024: HK$665,553,000)
    ARE INCLUDED IN 'other operating expenses'" — so there are words to the right of the last
    amount. That is the difference between the two, and it is the one the prose route has to see.

    Refused for a line with no grouped amount at all: the question does not arise, and answering
    it "no" is what leaves a caption-only lead-in where it belongs.
    """
    last = -1
    for i, w in enumerate(row):
        if _PROSE_GROUPED.search(w.text):
            last = i
    if last < 0:
        return False
    # A trailing footnote marker, note reference or bracket is not "carrying on".
    for w in row[last + 1:]:
        text = w.text.strip(" .,;:()（）[]*†#‡'\u2019\u201c\u201d")
        if len(text) >= 2 and not _PROSE_GROUPED.search(w.text) and not text.isdigit():
            return True
    return False


def _narrative_only(words: list[Word], items: list, source_kind: str) -> str:
    """The section's text with the lines it TABULATES removed — the note's narrative.

    WHY THE PROSE ROUTE NEEDS ITS OWN TEXT. `source_text` is every word of the section joined with
    spaces, and a printed table flattens into it with no sentence punctuation at all — so
    `note_sourced._sentences` returns the WHOLE NOTE as one sentence, a prose pattern matches
    across rows that are nowhere near each other on the page, and the first grouped amount in the
    note is taken as the figure. Measured on China SCE 1966's profit-before-tax note: the pattern
    "depreciation … included in … operating expenses" matched the flattened table and
    `sub__operating_expense_depreciation` published 17,475,980 — the note's FIRST amount, which is
    its Cost of properties sold — as a depreciation charge, on both the operating-expense part and
    the PBT callout, and on into the parent through rung P1.

    A LINE IS TABULAR IF IT BECAME A ROW WHOSE CAPTION NAMES A LINE ITEM. Two halves, and the
    second is what keeps the genuine case:

      * a line that produced NO row is narrative — the lead-in to a table, a footnote, a
        paragraph. Nothing tabulated it, so nothing here removes it.
      * a line that DID produce a row may still be narrative, because the reconstructor will make
        a row out of any line carrying a grouped amount — and the sentence this route exists for
        carries two ("Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000)
        are included in 'other operating expenses'"). What tells the two apart is WHERE the amount
        sits: a sentence states it INSIDE itself and goes on afterwards, while a table row puts it
        in a column, so the amounts are the rightmost things on the line. `_amount_is_inline`
        answers that, with `caption_shape.prose_reasons`' LENGTH test as a second way in for a
        sentence that happens to end on its figure.

        THE VERB TEST IS DELIBERATELY NOT USED HERE. `prose_reasons` calls a 60-character caption
        with a finite verb prose, which is right for its own question and too loose for this one:
        "Lease payments not included in the measurement of lease liabilities" is 66 characters and
        contains "included", and it is a tabulated row of the measured note — kept by that branch,
        with its two columns of figures, straight back into the narrative.

    Bounded by the ROW as printed, not by the item: a wrapped caption spans two printed lines and
    both belong to the row that absorbed them, which is why the y-band of the row's own words is
    the test rather than the item's label bbox.
    """
    from app.services import caption_shape

    # ITS OWN FOLD, deliberately not the page's. This reads PROSE in reading order, and a bilingual
    # note that sets its English and Chinese side by side is read one language column at a time —
    # which is what asking the section, rather than the page, gives. The page's fold is for the
    # rows a table is rebuilt from (see `extract_note_tables`), where a column break is not a fold.
    rows = _group_rows(words, row_tolerance(words, source_kind))
    inline = {i for i, row in enumerate(rows) if _amount_is_inline(row)}
    # The y-midpoint of every line that became a row, against the caption that row carried.
    #
    # OFF THE VALUES, NOT THE ITEM. `build_line_items` returns `LineItem`s whose own `provenance`
    # is unset — every box lives on the `ExtractedValue`s, and the label box is the same on all of
    # them because it is the row's caption. Reading `item.provenance` found None on all thirteen
    # rows of the measured note, so nothing was excluded and this returned the whole table.
    #
    # AND `source_label`, NOT `raw_label`: a `LineItem` carries the first and a `NoteItem` the
    # second, and this is handed `LineItem`s. Asking for the wrong one returned "" for every row,
    # which `prose_reasons` calls a line-item name — right by accident, and it would have kept no
    # genuine narrative row either.
    tabulated: list[tuple[float, float]] = []
    for item in items or ():
        label = str(getattr(item, "source_label", "")
                    or getattr(item, "raw_label", "") or "")
        # The LENGTH branch only — see the docstring for the tabulated row the verb branch kept.
        long_prose = [r for r in caption_shape.prose_reasons(label) if "chars=" in r]
        if long_prose:
            continue                       # a sentence the reconstructor made a row of — keep it
        for value in (getattr(item, "values", None) or {}).values():
            prov = getattr(value, "provenance", None)
            box = getattr(prov, "label_bbox", None) or getattr(prov, "bbox", None)
            if box is not None:
                tabulated.append((box.y0, box.y1))
                break
    if not tabulated:
        return " ".join(w.text for w in words).strip()
    # AN OVERLAP, NOT A MIDPOINT. A printed caption WRAPS — "Depreciation of property and" then
    # "equipment" — and the row that absorbed both lines has a label box spanning them, whose
    # midpoint falls in the WHITESPACE between them and so lies inside neither line's band. On the
    # measured note that left one of the two lines of every wrapped caption in the narrative, with
    # its figures: "Depreciation of property and 物業及設備折舊 equipment 14 80,427 70,896". Both
    # lines belong to the row, and overlap is what says so.
    kept: list[str] = []
    for i, row in enumerate(rows):
        lo = min(w.bbox.y0 for w in row)
        hi = max(w.bbox.y1 for w in row)
        if i not in inline and any(lo < y1 and y0 < hi for y0, y1 in tabulated):
            continue
        kept.extend(w.text for w in row)
    return " ".join(kept).strip()


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
    header = _header_rows(rows, fmt)
    for i, (row, (lw, _nr, _vw)) in enumerate(scanned):
        # THE COLUMN HEADER IS NOT A CATEGORY. Its label-column caption stands in the outer column
        # too, directly above the first category — so the merged-cell join below read 迈捷 300319's
        # 应付项目 header and first cell as ONE category, "项目名称应付账款", and its inventory
        # note's header as a category called "项目".
        if i in header:
            continue
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


# THE HEADER THAT NAMES BOTH LABEL COLUMNS. A mainland related-party note captions its two label
# columns 项目名称 | 关联方: the line item the balance is (应收账款, 预付款项) in the outer column, the
# counterparty in the inner one. Exact captions only, because a header that names one label column
# says nothing about a second.
_CATEGORY_HEADER = re.compile(r"^(?:项目名称|項目名稱|项目|項目)$")
_COUNTERPARTY_HEADER = re.compile(r"^(?:关联方|關聯方|关联方名称|關聯方名稱|关联方单位|關聯方單位)$")


def _is_caption_run(text: str) -> bool:
    """Whether one header phrase captions a COLUMN — a label column, or a period or measure over
    the value columns — rather than being anything a table body prints."""
    return bool(_CATEGORY_HEADER.match(text) or _COUNTERPARTY_HEADER.match(text)
                or _prc_period_slot(text) is not None or _measure_slug(text) is not None)


def _header_rows(rows: list[list[Word]], fmt=None) -> set[int]:
    """The indexes of the rows that make up a note's COLUMN HEADER.

    Above the first row carrying a figure, and every phrase on the row a column caption — both,
    because a category cell is ALSO printed above the first figure when it is merged over several
    rows ("其他应收" / "款", straddling the block's first data row) and must still be read.
    """
    first = next((i for i, row in enumerate(rows)
                  if _scan_row(row, fmt, extract_note_refs=False)[2]), len(rows))
    out: set[int] = set()
    for i, row in enumerate(rows[:first]):
        runs = [_caption_text(run) for run in _x_runs(row, _CAPTION_GAP)]
        if runs and all(_is_caption_run(text) for text in runs):
            out.add(i)
    return out


def _declared_categories(rows: list[list[Word]], fmt=None
                         ) -> tuple[list[tuple[float, str]], set[int]] | None:
    """A note's category column AS ITS OWN HEADER DECLARES IT, or None.

    Returns the category cells — ``(top y, caption)``, as :func:`_category_cells` does — and the
    ids of the words that printed them, which are not part of any row's caption.

    :func:`_category_cells` finds an outer label column from the figures' rows, and has to: with
    nothing else to go on it needs three valued rows and two categories before a margin is a
    column. A related-party note says so in its header instead — 项目名称 over the outer column,
    关联方 over the inner — and 澜起科技 688008 prints three such tables ONE ROW deep, with the
    category on the same line as the counterparty:

        项目名称   关联方
        应收账款   英特尔公司    86,260.80   431.30   1,914,028.85   9,570.14

    Nothing else read that row, so its caption was "应收账款英特尔公司" and its group was empty: the
    model was offered a counterparty glued to a line item, and a related-party receivable with no
    group to say which receivable it is.

    The two label columns divide at the midpoint of the two header captions. Below the header, a
    row's words left of it are its category and the rest its caption, with one exception: a row
    with FIGURES and nothing right of the boundary is a total or a line printed across both label
    columns (合计), which keeps its caption and ends the category above it. Vetoed — None, and the
    geometric reading is used — for a cell that is a sentence or longer than a caption.
    """
    boundary: float | None = None
    start = 0
    for i, row in enumerate(rows):
        if _scan_row(row, fmt, extract_note_refs=False)[2]:
            break
        runs = [(sum((w.bbox.x0 + w.bbox.x1) / 2 for w in run) / len(run), _caption_text(run))
                for run in _x_runs(row, _CAPTION_GAP)]
        outer = [x for x, text in runs if _CATEGORY_HEADER.match(text)]
        inner = [x for x, text in runs if _COUNTERPARTY_HEADER.match(text)]
        if len(outer) == 1 and len(inner) == 1 and outer[0] < inner[0]:
            boundary, start = (outer[0] + inner[0]) / 2, i + 1
            break
    if boundary is None:
        return None
    header = _header_rows(rows, fmt)
    taken: set[int] = set()
    # (top, bottom, caption, closes) — a closing fragment is a total row ending the category.
    frags: list[tuple[float, float, str, bool]] = []
    for i, row in enumerate(rows[start:], start):
        if i in header:
            continue
        lw, _nr, vw = _scan_row(row, fmt, extract_note_refs=False)
        outer = [w for w in lw if (w.bbox.x0 + w.bbox.x1) / 2 < boundary]
        if not outer:
            continue
        top, bottom = min(w.bbox.y0 for w in outer), max(w.bbox.y1 for w in outer)
        if vw and len(outer) == len(lw):
            frags.append((top, bottom, "", True))
            continue
        taken.update(id(w) for w in outer)
        text = "".join(w.text for w in sorted(outer, key=lambda w: w.bbox.x0)).strip()
        frags.append((top, bottom, text, False))
    # A merged cell drawn on several lines is ONE category — the same join `_category_cells` makes.
    cells: list[tuple[float, float, str, bool]] = []
    for frag in sorted(frags):
        if cells and not frag[3] and not cells[-1][3]:
            py0, py1, ptext, _ = cells[-1]
            line_h = max(py1 - py0, 1e-4)
            if -0.5 * line_h <= frag[0] - py1 <= 0.6 * line_h:
                cells[-1] = (py0, frag[1], ptext + frag[2], False)
                continue
        cells.append(frag)
    named = [text for _y0, _y1, text, closes in cells if not closes]
    if not named or any(len(text) > _MAX_CATEGORY_CHARS or _CJK_SENTENCE_MARK.search(text)
                        for text in named):
        return None
    return [(y0, text) for y0, _y1, text, _closes in cells], taken


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
    r"^\s*(?:(?:(?:(?:opening|closing)\s+)?balances?\s+(?:as\s+)?at|as\s+at|at"
    r"|for\s+the\s+(?:year|period)\s+ended?"
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
#
# THE PATTERN NOW LIVES WITH `_scan_row`, the function that mis-reads a date in the first place, and
# is imported rather than restated — `row_reconstruct.DATE_TOKEN`. Two copies of this vocabulary
# would drift, and the repair that gives a caption its date back
# (`row_reconstruct._label_keeping_its_date`) has to agree with the anchor pass below about which
# tokens are a date, or a row could be anchored to a period whose text its caption does not carry.
_DATE_TOKEN = DATE_TOKEN


def _period_anchors(rows: list[list[Word]], fmt=None) -> list[tuple[float, int, bool]]:
    """``(top y, year, carries_figures)`` for every row of this note that states a period.

    ``carries_figures`` is what tells an opening header from a closing balance, and therefore which
    direction the year applies in — see this block's comment.
    """
    out: list[tuple[float, int, bool]] = []
    rows = [row for row in rows if row]
    for k, row in enumerate(rows):
        _labels, _refs, value_words = _scan_row(row, fmt, extract_note_refs=False)
        figures = [w for w in (value_words or ())
                   if not _DATE_TOKEN.match(w.text.strip().strip(",.()"))]
        # A BALANCE CAPTION WRAPPED BEFORE ITS YEAR — "Balance as at 31 March" over "2024 | 1,245.60
        # | …" (f1 note 3, whose label column is too narrow for the date). The year and the figures
        # are on the next line, and the anchor is the caption's: without this the 2024 and 2025
        # closing balances anchored nothing, and every movement row above the net carrying amounts
        # took the first dated row below it — "prior" for both years.
        if not figures and k + 1 < len(rows):
            head = " ".join(w.text for w in sorted(row, key=lambda w: w.bbox.x0)).strip()
            nxt = rows[k + 1]
            _l2, _r2, nxt_values = _scan_row(nxt, fmt, extract_note_refs=False)
            nxt_figures = [w for w in (nxt_values or ())
                           if not _DATE_TOKEN.match(w.text.strip().strip(",.()"))]
            nxt_skip = {id(w) for w in nxt_figures}
            # A nil dash ("-" under Freehold land in the depreciation block) is a cell, not text.
            tail = " ".join(w.text for w in sorted(nxt, key=lambda w: w.bbox.x0)
                            if id(w) not in nxt_skip
                            and not re.fullmatch(r"[-–—−]", w.text.strip())).strip()
            if (nxt_figures and _PERIOD_PREFIX.match(head) and _year_of(head) is None
                    and re.fullmatch(r"(?:19|20)\d{2}", tail)):
                out.append((min(w.bbox.y0 for w in row), int(tail), True))
                continue
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
                        carry_note: tuple | None = None,
                        log=None,
                        carry_grid: ColumnGrid | None = None,
                        grid_out: list[ColumnGrid | None] | None = None,
                        carry_group: str | None = None,
                        group_out: list[str] | None = None,
                        chapter: list | None = None,
                        known_captions: frozenset[str] | None = None,
                        page_fold=DETECT_FOLD,
                        carry_header=None,
                        header_out: list | None = None,
                        reporting_year: int | None = None,
                        default_basis: Basis | None = None,
                        open_out: list | None = None) -> list[NotesTable]:
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

    ``carry_group`` is the SUB-HEADING still open when the previous page ended, threaded for the
    same reason as the grid and reported back through ``group_out``: a mainland note prints
    `长期应付款：` at the foot of a page and its counterparty rows at the top of the next, and a
    row whose caption is a company name has nothing else to identify it by. See
    ``row_reconstruct.build_line_items``' own note on the field.

    ``carry_grid`` is the two-level column grid read from an earlier page of the note still open,
    and ``grid_out`` collects the grid each section was read with so the caller can carry it to
    the next page. A PRC related-party note prints its 期末余额{账面余额|坏账准备} header once and
    then runs for eight pages; without the carry every page after the first reads four columns
    positionally, which is the mis-load the grid exists to prevent.

    ``carry_header`` is the PRINTED-COLUMN header (`row_reconstruct.HeaderModel`) the note still
    open on the previous page was read with, and ``header_out`` collects the header each section
    was named by. Unlike the grid it also carries WITHIN the page, to a section of the same note the
    walk split off below its header (迈捷 300319's 十二、1 is cut at the wrapped row caption
    "1.以公允价值计量且…", leaving its four Level 2 rows under no header) — and in either case only
    when those rows print no header of their own and their figures fit the carried one; see
    `row_reconstruct._name_note_columns`. ``reporting_year`` places a dated column heading.

    ``open_out`` receives the (number, title, basis, basis_from) of the LAST section the page
    opened, published or not, for the caller to carry as ``carry_note``. A table titled and headed
    at the foot of a page holds no rows there and is dropped as an empty fragment of its note, and
    the carry used to be the last table PUBLISHED: 000883's 固定资产情况 ran onto the next page
    under the title of the note's summary table, "、固定资产".

    ``default_basis`` is the basis the document's face statements print, when they print only one
    (`services.face_bases`; the caller passes STANDALONE alone). A note that prints no basis of its
    own is then read on it: the row builder reads such a note as the GROUP's, and on a filing whose
    statements are all the company's that put every note figure on a consolidated tab holding
    nothing else. Only a table whose every value is that no-band default takes it — a Group|Company
    band, a company-only chapter and a basis carried from the previous page all win. None keeps
    the reading every caller has always had.
    """
    # The same page-derived tolerance the face uses: a note's detail lines are set as tightly as a
    # statement's, and two of them merged into one row interleave their captions (row_reconstruct.
    # row_tolerance). A note whose caption comes out scrambled ties to nothing.
    # THE FOLD IS DECIDED ONCE, ON THE PAGE — by the caller, which still knows the page's shape
    # (`page_spread.page_fold`), and from these words only when it does not. A section is a few
    # rows of the page, and asked on its own a two-column table with a text column beside it can
    # pass for a 2-up spread — see `row_reconstruct._group_rows`. Every grouping of this page's
    # words below takes this answer.
    fold = page_spread.gutter_x(words) if page_fold is DETECT_FOLD else page_fold
    rows = _group_rows(words, row_tolerance(words, source_kind), fold=fold)
    # READ BEFORE THE SECTIONS ARE WALKED, because a chapter heading is a label-only row and the
    # walker would otherwise fold it into whichever note is open — it reaches the
    # ``current["words"].extend(row)`` arm and disappears into that note's text. The chapter is
    # orthogonal to the note number, so it changes nothing about where a section starts.
    #
    # BY POSITION, NOT BY PAGE. The chapter used to be read once per page and applied to every
    # section on it, so a section printed ABOVE a page's chapter heading was numbered under the
    # chapter that heading OPENS — see `chapter_headings` for 688008's related-party balances
    # arriving as share-based payment's 十五、1. Each section now takes the chapter in force where
    # it STARTS: the page-start chapter until the first heading, and each heading's after it.
    page_start = (tuple(chapter[:3]) if chapter is not None else (None, 0, ""))
    transitions: dict[int, tuple[str, int, str]] = {}
    if chapter is not None:
        for index, found in chapter_headings(rows, chapter[1] if chapter[0] else 0):
            transitions[index] = found
            if log:
                log(f"notes:page={page_index}:chapter={found[0]}、{found[2]}({found[1]})")
        if transitions:
            last = transitions[max(transitions)]
            chapter[0], chapter[1], chapter[2] = last
    in_force = page_start
    sections: list[dict] = []
    current: dict | None = None
    # Not appended to ``sections`` until it actually claims a row — a page that opens straight
    # onto a real heading must not leave a spurious empty table behind under the OLD note number.
    carried: dict | None = None
    if carry_note is not None:
        carried = {"no": carry_note[0], "title": carry_note[1], "words": [],
                   "chapter": page_start,
                   # A TABLE STILL OPEN at the foot of the previous page — its printed header or
                   # its block heading carried — so the page's first lines may be its rows. See
                   # `_wrapped_row_caption`.
                   "open_table": carry_header is not None or bool(carry_group)}
        # A CONTINUED NOTE KEEPS THE BASIS OF THE CHAPTER IT BEGAN IN, as it already keeps that
        # chapter's number (`qualified_note_number`: "the chapter a note was printed under is the
        # chapter it belongs to"). The basis used to come from the page, so the tail of a GROUP
        # note spilling onto the page where 母公司财务报表主要项目注释 opens was tagged STANDALONE:
        # 300319's 十七、1 (其他重要事项) arrives as three fragments, and the third, on page 181,
        # was a company-only table under the group's note number. A two-element carry — every
        # caller before this — keeps the page's basis, which is what it always had.
        if len(carry_note) > 2:
            carried["basis"] = carry_note[2]
        # …and why it had that basis, so a continuation of a 母公司 note is as explicit as its
        # first page (`NotesTable.basis_from`).
        if len(carry_note) > 3:
            carried["basis_from"] = carry_note[3]
        current = carried
    # THE NUMBERED NOTE A BRACKETED SUB-TABLE BELONGS TO. A mainland note numbers three levels
    # deep — 十二、the chapter, 6、the note, （1）/① the tables inside it — and every level used to
    # become a note NUMBER, so （1）应收项目 was note 十二、1 and ①采购商品情况表 was 十二、1 too:
    # 河钢股份 000709's 十二、1 pooled nineteen tables from four different notes (the parent company,
    # purchases, leases, guarantees, receivables), and the notes the face and the rulebook name as
    # 十二、5 and 十二、6 held no rows at all. A bracketed heading under a numbered note is a table
    # OF that note: it takes the note's number and keeps its own title, the way an English filing's
    # "(a)" sub-heading stays inside its note. Carried from the previous page, because a note's
    # tables run on across pages; a chapter heading closes it.
    open_note: str | None = (carry_note[0] if carry_note is not None and carry_note[0]
                             and not str(carry_note[0]).endswith("、") else None)
    # …AND WHEN NO NOTE WAS CARRIED, THE CHAPTER'S LAST ONE. The carry is reset by every page read
    # as a face, and a mainland filing prints its cash-flow supplement inside 七: on the notes page
    # after it, the supplement's own sections 2、3、4、 opened as notes 七、2 to 七、4, and every real
    # note after them (000883's 七、81 外币货币性项目 to 七、84) was then refused as too far ahead.
    # The chapter cell remembers the last note number read in its chapter (`chapter[3]`), and that
    # is what a page with no carry continues from. Only a caller that passes the fourth cell gets
    # it (`pdf_extract` does); a three-cell chapter keeps the carry as its only memory.
    remembers = chapter is not None and len(chapter) > 3
    if remembers and open_note is None and page_start[0]:
        open_note = chapter[3]
    i = 0
    while i < len(rows):
        row = rows[i]
        # A CHAPTER HEADING ENDS THE OPEN NOTE AND OPENS ONE OF ITS OWN. A note cannot run on into
        # the next chapter, and the heading row used to reach the `words.extend` arm below and be
        # folded into whatever note was open — its text, and every row after it until the next note
        # heading, credited to the previous chapter's last note.
        #
        # OPENS, NOT MERELY ENDS, and the first version of this only ended it, which lost figures:
        # a chapter may print its content with no note heading of its own. 河钢股份 000709's
        # 八、研发支出 goes straight from the heading into its table (人工费 … 合计
        # 2,343,028,433.26), and 迈捷 300319's does the same — both used to be folded into the
        # previous chapter's lease note, and with the open note merely ended they were dropped. The
        # chapter's own content is now a section identified as the chapter itself (`八、`), titled
        # with the chapter's heading; one with no rows is not published (see the table loop).
        if i in transitions:
            in_force = transitions[i]
            open_note = None
            current = {"no": f"{in_force[0]}、", "title": in_force[2], "words": [],
                       "chapter": in_force, "chapter_own": True}
            sections.append(current)
            i += 1
            continue
        head = _is_heading(row)
        # INSIDE A CHAPTER A NOTE NUMBER ONLY RISES. A mainland note prints numbered rows of its own
        # — a movement table's "2.本期增加金额" and "4.期末余额", whose figures sit on the rows
        # below, a payroll note's "1、工资、奖金、津贴" — and each one, read as a heading, minted a
        # note under a number that belongs to a real note: 000883's 七、21 固定资产 put its
        # movement rows under 七、2 交易性金融资产 and 七、3, and its cash-flow supplement's sections
        # became 七、2 / 七、3 / 七、4. A number that does not step forward from the open note
        # within `_NOTE_STEP_MAX` is a row of that note, read as one. Bracketed levels keep their
        # own rule below (`_is_bracketed`), and a filing with no chapters numbers as it always did.
        refused = (head is not None and in_force[0] and not _is_bracketed(row)
                   and not _follows_open_note(open_note, head[0], in_force[0]))
        if refused and current is None and open_note:
            # NOTHING OPEN ON THIS PAGE YET: the refused heading opens a table OF the open note, as
            # a bracketed one does, rather than its rows going nowhere.
            current = {"no": open_note, "title": head[1], "words": [], "chapter": in_force}
            sections.append(current)
            i += 1
            continue
        if refused:
            head = None
        if head is not None and _wrapped_row_caption(rows, i, current):
            head = None
        if head is not None:
            no, title = head
            # A HEADING THAT WRAPPED ONTO THE NEXT LINE IS ONE HEADING.
            #
            # MEASURED: 1966 prints "26. FINANCIAL ASSETS AT FAIR VALUE / THROUGH PROFIT OR LOSS"
            # over two lines and this kept the first, so the title was "FINANCIAL ASSETS AT FAIR
            # VALUE 26. 按公允值計量且其變動計" — cut before the words that say WHICH fair-value
            # category it is. The shipped pattern names the full phrase and therefore matched
            # nothing, and five focus lines (the FVTPL family, both current and non-current) were
            # sent no declared note for that filing. Authoring around it would have meant guessing
            # FVTPL against FVTOCI from a heading truncated before the word that distinguishes
            # them, which is the guess this removes rather than makes.
            #
            # ONE ROW, AND ONLY A ROW THAT CAN BE NOTHING ELSE. The row must carry no value column
            # (`_title_only_row`), must not open its own note (`_is_heading`, `_bare_note_number`),
            # must not be the period/currency band a table opens with (`_TABLE_BAND`) — that band
            # has no value column either, so without it every table's first heading would swallow
            # "2023 2022 RMB'000" — and must be set in heading style and heading length
            # (`_LOWER_LATIN`, `_WRAP_MAX`, `_CJK_SENTENCE`), which is what keeps a note's opening
            # PROSE line out. Measured over the five reference filings: 2 of 342 headings join,
            # both on 1966, and no CAS title changes at all.
            if i + 1 < len(rows):
                nxt = rows[i + 1]
                more = _title_only_row(nxt)
                if (more and len(more) <= _WRAP_MAX
                        and _LATIN.search(title) and _HAS_HAN.search(title)
                        and _LATIN.search(more)
                        and not _LOWER_LATIN.search(more)
                        and not _CJK_SENTENCE.search(more)
                        and not more.rstrip().endswith((":", "\uff1a"))
                        and not _TABLE_BAND.match(more)
                        and _is_heading(nxt) is None and _bare_note_number(nxt) is None):
                    title = f"{title} {more}".strip() if title else more
                    i += 1
            if _is_bracketed(row):
                no = open_note or no
            else:
                open_note = no
            current = {"no": no, "title": title, "words": [], "chapter": in_force}
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
                open_note = no
                current = {"no": no, "title": title, "words": [], "chapter": in_force}
                sections.append(current)
        elif current is not None:
            if current is carried and carried not in sections:
                sections.append(carried)
            if (_opens_a_sub_table(row) and i > 0
                    and any(_carries_amounts(r) for r in _rows_since(rows, i, current))):
                # A SECOND TABLE OF THE SAME NOTE, under its own column header — see
                # `_opens_a_sub_table`. The caption line just above the header goes with it.
                prev = rows[i - 1]
                lead = list(prev) if _title_only_row(prev) is not None else []
                # …and the line that NAMES the table, a line or two above its header ("出售商品/
                # 提供劳务情况表" over "单位：元"), goes with it and titles the new section.
                named = None
                for back in (1, 2):
                    if i - back < 0 or back > 1 and not lead:
                        break
                    text = " ".join(w.text for w in rows[i - back]).strip()
                    if _TABLE_NAME.search(text) and _title_only_row(rows[i - back]) is not None:
                        named = (back, text)
                        break
                if named is not None and named[0] == 2:
                    lead = list(rows[i - 2]) + lead
                if lead:
                    ids = {id(w) for w in lead}
                    current["words"] = [w for w in current["words"] if id(w) not in ids]
                current = {k: v for k, v in current.items() if k != "words"}
                if named is not None:
                    current["title"] = named[1].rstrip("：:")
                current["words"] = lead
                sections.append(current)
            current["words"].extend(row)
        i += 1
    if remembers:
        chapter[3] = open_note

    tables: list[NotesTable] = []
    # The printed-column header the previous section was named by, and which note it belonged to.
    prev_header = (carry_header, None)
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
        open_group: list[str] = []
        # The note's own 项目名称 column, if it has one — see `_category_cells` for the nine-row
        # block whose eight continuation rows named no receivable class at all without it, and
        # `_declared_categories` for the one-row table whose header names the column outright.
        # A DECLARED category is not part of any row's caption, so its words never reach the
        # reconstructor: "应收账款 英特尔公司" is the counterparty 英特尔公司 in the 应收账款 group.
        grouped = _group_rows(sec["words"], row_tolerance(sec["words"], source_kind), fold=fold)
        declared = _declared_categories(grouped)
        cells = declared[0] if declared is not None else _category_cells(grouped)
        row_words = ([w for w in sec["words"] if id(w) not in declared[1]]
                     if declared is not None else sec["words"])
        # A RUNNING PARAGRAPH IS THE NOTE'S PROSE, NOT ITS ROWS — see `running_text_words`. Only
        # the rows lose it: `source_text` and the narrative below are read from every word.
        running = running_text_words(grouped)
        if running:
            row_words = [w for w in row_words if id(w) not in running]
        # THE SAME NOTE'S HEADER, OFFERED: the carried section takes the previous page's; a later
        # section takes the one the section before it was named by, when both are the same note.
        sec_numeral_now = (sec.get("chapter") or page_start)[0]
        sec_note = qualified_note_number(sec_numeral_now, sec["no"])
        if sec is carried:
            offered = carry_header
        else:
            offered = prev_header[0] if prev_header[1] == sec_note else None
        named: list = []
        banded: list = []
        items, _ = build_line_items(row_words, page_index=page_index,
                                    document_id=document_id, source_kind=source_kind,
                                    on_face=False, scope=scope, normalisation=normalisation,
                                    log=log,
                                    column_grid=(carry_grid if sec is carried else None),
                                    grid_out=seen,
                                    carry_group=(carry_group if sec is carried else None),
                                    group_out=open_group, known_captions=known_captions,
                                    page_fold=fold,
                                    header_carry=offered, header_out=named,
                                    reporting_year=reporting_year, bands_out=banded)
        used_header = named[0] if named else None
        prev_header = (used_header, sec_note)
        if header_out is not None:
            header_out.append(used_header)
        # What the NEXT page inherits is the grid of the note still open when this page ended, so
        # the carry is whatever the last section was read with — None included.
        carry_grid = seen[0] if seen else None
        if grid_out is not None:
            grid_out.append(carry_grid)
        # …and the same for the sub-heading, on the same boundary: only the CARRIED section — the
        # one continuing the note the previous page left open — inherits it, and a section that
        # opens a new note starts with none.
        carry_group = open_group[0] if open_group else ""
        if group_out is not None:
            group_out.append(carry_group)
        if not items and (not sec["title"] or sec.get("chapter_own")):
            continue
        # THE SECTION'S OWN CHAPTER — the one in force where it started (see the walk above).
        sec_numeral, _sec_ordinal, sec_chapter_title = sec.get("chapter") or page_start
        # A COMPANY-ONLY NOTE, from the chapter that holds it. The heading is printed once, pages
        # before the note itself, so the chapter is the only thing on the page that can say so —
        # and the concepts these notes state are the same ones the group's notes state. A carried
        # note's own basis wins, for the reason given where it is carried.
        chapter_basis = (Basis.STANDALONE
                         if sec_chapter_title and _COMPANY_CHAPTER.search(sec_chapter_title)
                         else None)
        sec_basis = sec["basis"] if "basis" in sec else chapter_basis
        # WHY, recorded beside it (`NotesTable.basis_from`): a continued note is explicit when
        # the note it continues was; a basis carried from the face's default stays a default.
        if "basis" in sec:
            began = str(sec.get("basis_from") or "")
            basis_from = ("" if sec_basis is None
                          else "carried" if began in ("chapter", "carried") else began)
        else:
            basis_from = "chapter" if chapter_basis is not None else ""
        # A NOTE THAT SAYS NOTHING OF WHOSE IT IS TAKES THE FACE'S ONE BASIS — see
        # ``default_basis``. Only a table whose values are uniformly the row builder's no-band
        # CONSOLIDATED default: a note printing Group and Company columns carries both bases and
        # keeps them, and a carried note's own basis was decided where it began.
        if (sec_basis is None and default_basis is not None
                and all(ev.basis == Basis.CONSOLIDATED
                        for li in items for ev in (li.values or {}).values())):
            sec_basis = default_basis
            basis_from = "default"
        # A NOTE PRINTING GROUP AND COMPANY COLUMNS gave each value its basis from the band.
        if sec_basis is None and any(banded):
            basis_from = "band"
        table = NotesTable(note_number=qualified_note_number(sec_numeral, sec["no"]),
                           title=sec["title"], basis=sec_basis, basis_from=basis_from,
                           source_pages=[page_index],
                           # THE CHAPTER'S OWN HEADING, carried rather than discarded. `read_chapter`
                           # returns `(numeral, ordinal, title)` and the title was used only for the
                           # 母公司 basis test above and for a log line, so a `note_source` naming
                           # the chapter — which is how a human says where a figure lives — matched
                           # nothing. See `NotesTable.chapter_title`.
                           chapter_title=sec_chapter_title or "",
                   source_text=" ".join(word.text for word in sec["words"]).strip(),
                           # THE NARRATIVE, for the PROSE route alone — see `_narrative_only` for
                           # the figure a flattened table published as a depreciation charge.
                           prose_text=_narrative_only(sec["words"], items, source_kind))
        if open_out is not None:
            open_out.append((table.note_number, table.title, table.basis, table.basis_from))
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
                          column_groups=list(li.column_groups),
                          printed_columns=list(li.printed_columns),
                          group_hint=(li.group_hint
                                      or _category_for(cells, _row_top(li))),
                          # Both empty unless this note states a period on its blocks AND this row
                          # has a column that totals the others — a movement row and nothing else.
                          period_hint=_period_hint_for(anchors, newest, _row_top(li)),
                          total_slot=_total_slot_of(li),
                          provenance=li.values and next(iter(li.values.values())).provenance or None)
            for ev in li.values.values():
                # A COMPANY-ONLY NOTE'S FIGURES ARE THE COMPANY'S. The chapter's basis was put on
                # the TABLE and nowhere else, while `build_line_items` — reading a note that prints no
                # basis column — tags every value CONSOLIDATED. So every consumer that reads a
                # figure's own basis (the deterministic row route, the model's cited rows) read a
                # 母公司财务报表主要项目注释 figure as the GROUP's: measured, 95 values on 688008,
                # 248 on 000709 and 227 on 300319 sat in standalone tables tagged consolidated.
                # Re-keyed through `set_value`, because the value's key is derived from its basis.
                if sec_basis is not None and ev.basis != sec_basis:
                    ev = ev.model_copy(update={"basis": sec_basis})
                ni.set_value(ev)
                # The two-level-header call-out travels on the ROW as well as the value, because
                # the notes payload serves the row's flags and the note pane reads them; a value
                # flag alone would be raised where nothing on this screen looks for it.
                if GRID_FLAG in ev.confidence.flags and GRID_FLAG not in ni.confidence.flags:
                    ni.confidence.flags.append(GRID_FLAG)
            if NIL_ROW_FLAG in li.confidence.flags:
                ni.confidence.flags.append(NIL_ROW_FLAG)       # printed nil in every column
            table.items.append(ni)
        _inner_rows_take_the_outer_cell(table.items, sec["words"])
        tables.append(table)
    return without_empty_duplicates(tables)


# A ROW PRINTED ONLY IN THE INNER LABEL COLUMN BELONGS TO THE OUTER CELL ABOVE IT. An HKFRS
# related-party note captions its rows in two columns, nature | related party, and prints the
# nature once per group:
#
#     Sales of inventories     (ii)  Ultimate holding company      86,714     7,633
#                                    Fellow subsidiaries       17,989,310 11,536,290
#     Lease and ancillary …   (iii)  Ultimate holding company     183,411   188,151
#                                    Fellow subsidiaries        1,040,666   773,690
#
# Read row by row, ten rows of 0285's note 35 were captioned "Fellow subsidiaries" and nothing
# else, under one group: no citation could name which one, so the model could cite none of them
# and 17,989,310 of sales could not be reached. The outer cell is the row's meaning; it is written
# onto ``group_hint`` of the row that prints it and the inner rows below it — only where the inner
# caption repeats under one group with different figures, so a table that already tells its rows
# apart is left exactly as it was.
_INNER_COLUMN_GAP = 0.15        # page widths between the caption column and the inner label column


def _name_the_repeated_rows(boxes: list, words: list, left: float) -> None:
    """A CAPTION PRINTED IN THE SECOND COLUMN, THE COUNTERPARTY'S NAME WRAPPED IN THE FIRST.

    300391 p245 prints its 出售商品/提供劳务情况表 as 关联方 | 关联交易内容 | 本期 | 上期, and each
    counterparty's name over three lines — "湖北茉莉大药房连锁有限公" above the figures' baseline and
    "司" below it, the figure line carrying only "销售商品". Read row by row, four rows were captioned
    销售商品 with four different figures, and the names were nowhere a citation could reach. A row
    whose caption and group repeat with different figures is given, as its group, the words printed
    LEFT of the captions within its own band — halfway to the rows above and below. A table whose
    rows can already be told apart is left exactly as it was.
    """
    def figures(ni):
        return tuple(sorted((k, str(ev.value)) for k, ev in (ni.values or {}).items()))

    seen: dict[tuple[str, str], set] = {}
    for ni, _b in boxes:
        seen.setdefault((ni.raw_label.strip(), ni.group_hint or ""), set()).add(figures(ni))
    repeated = {k for k, v in seen.items() if len(v) > 1}
    if not repeated:
        return
    outside = [w for w in words if w.bbox.x1 <= left - 0.005]
    if not outside:
        return
    rows = sorted(boxes, key=lambda nb: (nb[1].y0 + nb[1].y1) / 2)
    mids = [(b.y0 + b.y1) / 2 for _ni, b in rows]
    names: list[tuple[object, str]] = []
    for i, (ni, b) in enumerate(rows):
        if (ni.raw_label.strip(), ni.group_hint or "") not in repeated:
            continue
        up = (mids[i] - mids[i - 1]) / 2 if i else (mids[1] - mids[0]) / 2 if len(mids) > 1 else 0
        down = (mids[i + 1] - mids[i]) / 2 if i + 1 < len(mids) else up
        band = [w for w in outside if mids[i] - up < (w.bbox.y0 + w.bbox.y1) / 2 <= mids[i] + down]
        text = "".join(w.text for w in sorted(band, key=lambda w: (round(w.bbox.y0, 3), w.bbox.x0)))
        names.append((ni, text.strip()))
    # Only when every repeated row found a name, and the names tell them apart.
    if names and all(t for _ni, t in names) and len({t for _ni, t in names}) == len(names):
        for ni, text in names:
            ni.group_hint = text


def _inner_rows_take_the_outer_cell(items: list, words: list) -> None:
    def label_box(ni):
        prov = getattr(ni, "provenance", None)
        return getattr(prov, "label_bbox", None) if prov is not None else None

    def figures(ni):
        return tuple(sorted((k, str(ev.value)) for k, ev in (ni.values or {}).items()))

    boxes = [(ni, label_box(ni)) for ni in items if ni.values]
    boxes = [(ni, b) for ni, b in boxes if b is not None]
    if len(boxes) < 2:
        return
    left = min(b.x0 for _ni, b in boxes)
    inner = [(ni, b) for ni, b in boxes if b.x0 > left + _INNER_COLUMN_GAP]
    if not inner:
        _name_the_repeated_rows(boxes, words, left)
        return
    if len(boxes) < 3:
        return
    seen: dict[tuple[str, str], set] = {}
    for ni, _b in inner:
        seen.setdefault((ni.raw_label.strip(), ni.group_hint or ""), set()).add(figures(ni))
    if not any(len(v) > 1 for v in seen.values()):
        return
    # THE OUTER CELLS, read off the words: each line printed left of the inner column, a line
    # directly under another joining it (the cell's other script, "出售廠房及機器" under "Sales of
    # plant and machinery"). Read from the words and not from the rows, because a cell printed over a
    # row of dashes — "Purchases of batteries (viii) … –  –" — is a row with no figure and no
    # provenance, and it still ends the cell above it.
    edge = min(b.x0 for _n, b in inner) - 0.01
    lines: dict[float, list] = {}
    for w in words:
        if w.bbox.x0 >= left - 0.005 and w.bbox.x1 <= edge:
            lines.setdefault(round(w.bbox.y0, 3), []).append(w)
    cells: list[list] = []            # [top, bottom, text]
    for y in sorted(lines):
        ws = sorted(lines[y], key=lambda w: w.bbox.x0)
        top, bottom = y, max(w.bbox.y1 for w in ws)
        text = " ".join(w.text for w in ws).strip()
        if cells and top - cells[-1][1] <= 0.8 * (bottom - top):
            cells[-1][1], cells[-1][2] = bottom, f"{cells[-1][2]} {text}"
        else:
            cells.append([top, bottom, text])
    if not cells:
        return
    for ni, b in boxes:
        if b.x0 > left + _INNER_COLUMN_GAP or b.x1 > edge:
            cell = next((c for c in reversed(cells) if c[0] <= b.y1), None)
            if cell is not None and cell[2]:
                ni.group_hint = cell[2]


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
