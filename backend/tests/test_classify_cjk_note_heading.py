r"""A PRC NOTE PAGE IS CLASSIFIED BY ITS OWN HEADING, not only by where it sits.

NEW FILE -> backend/tests/test_classify_cjk_note_heading.py

`_NUMBERED_HEADING` requires WHITESPACE after the number, and CJK typesetting puts none after an
ideographic comma — "10、存货" is written closed up, always. So no numbered note heading in a PRC CAS
filing matched it, `PageFeat.note_heading` was False on every one of them, and `_emission` lost both
halves of its note evidence: the +2.5 for NOTES and the -3.0 against FACE. What remained was the
numeric-density point, FACE +1.0 against NOTES +0.5, so FACE won each page by 0.5 — and with
`(_FACE, _FACE)` free and `(_NOTES, _FACE)` at 3.0 the decode had no reason to leave.

MEASURED on a 287-page Shenzhen-listed report (10972689): 119 of 287 pages came out FACE, including
a 93-page unbroken run through the notes — PDF pages 180-272, headed "10、存货",
"12、一年内到期的非流动资产", "20、投资性房地产" — every one at 0.45-0.53 confidence because no title
matched on any of them. A face page never becomes a note, so those disclosures were unreachable.
After: 47 face pages, the statement block (PDF 107-122) intact, and notes extracted 179 -> 338.

WHY THE SIGNAL IS POSITION-BOUNDED, which is the whole safety of it. Relaxing the Latin arm instead
was measured and is what must not be done: `_features` sets `note_heading` from a `.search()` over
the WHOLE page text, so anything that pattern gains it gains on every page of every filing —
"1 January 2024", "31 December 2024" and every three-digit-then-word line on an untitled STATEMENT
page would read as a note heading. Swept, that moved 7,419 of 14,018 figures across seven filings
and took turnover on one from 408,721,552 to 820,695,961. `_ZH_NUMBERED_HEADING` is asked only of a
page's OPENING lines: a statement's face page opens with its title and column headings, and
"10、存货" at the top of a page is a note heading and nothing else. The Latin arm is untouched, byte
for byte, and the corpus shows it — six of the twelve reference filings change nothing at all, and
65 calculated figures change across the six CAS filings.

THE SECOND HALF IS THE INVARIANT'S ANCHOR. With the signal in place the front matter of a CAS filing
carries note evidence too, because its MD&A is itself full of numbered subsections. Opening the
decode path in FACE is free and saves the 1.0 that `(_PRE, _NOTES)` costs, so the cheapest path
became "FACE on page 1, NOTES from page 2" — which anchored `_notes_follow_the_face` at index 0 and
left all 105 front-matter pages in the notes. The anchor now has to be a face page carrying a TITLE.
"""
from __future__ import annotations

from app.stages.classify import (
    _FACE, _NOTES, _PRE, _ZH_NUMBERED_HEADING, _notes_follow_the_face,
    _opens_with_zh_note_heading)


class _Feat:
    """Only the field the invariant reads."""

    def __init__(self, strong_title: bool = False) -> None:
        self.strong_title = strong_title


def _lines(*texts: str) -> list[dict]:
    return [{"text": t} for t in texts]


# ── the heading itself ───────────────────────────────────────────────────────────────────────────

def test_a_cas_numbered_note_heading_is_recognised():
    """The 93-page run was headed by lines of exactly this shape."""
    for line in ("10、存货", "12、一年内到期的非流动资产", "20、投资性房地产",
                 "78、现金流量表项目", "1、公司基本情况", "3、其他"):
        assert _ZH_NUMBERED_HEADING.match(line), line


def test_a_printed_amount_is_not_a_heading():
    """THE RISK THE SEPARATOR GUARDS. The thousands separator is not in the class, so an amount
    cannot satisfy the break — which is what would otherwise make every figure on every statement
    a note heading."""
    for line in ("10,500.00", "11,945,593,260.30", "95,000,000.00", "1,424"):
        assert not _ZH_NUMBERED_HEADING.match(line), line


def test_a_date_a_decimal_and_a_running_header_are_not_headings():
    """Each is a real line off the measured filing. A four-digit year exceeds the digit bound with
    no separator to backtrack to; "5" is not a Han character; a bare caption has no number."""
    for line in ("2024年12月31日", "2024 年年度报告全文", "1.5 million",
                 "应收票据", "流动负债：", "其中：优先股"):
        assert not _ZH_NUMBERED_HEADING.match(line), line


def test_the_latin_form_is_not_this_pattern_s_business():
    """It is deliberately Han-only: the Latin arm already matches its own forms and is left exactly
    as it was, so this pattern adds a language rather than widening a rule."""
    assert not _ZH_NUMBERED_HEADING.match("14. Cash and cash equivalents")
    assert not _ZH_NUMBERED_HEADING.match("14 cash and cash equivalents")


# ── and only at the top of a page ────────────────────────────────────────────────────────────────

def test_a_page_that_opens_with_the_heading_is_recognised():
    assert _opens_with_zh_note_heading(_lines("10、存货", "项目", "1,234.00"))


def test_a_statement_face_page_is_not():
    """THE CASE THE POSITION BOUND EXISTS FOR. A PRC balance sheet opens with its title, its
    preparer line and its date — and further down it is full of note REFERENCES and rows beginning
    with a figure, which is why the question is what the page STARTS with."""
    assert not _opens_with_zh_note_heading(
        _lines("合并资产负债表", "编制单位：湖北能源集团股份有限公司", "2024年12月31日",
               "单位：元", "应收票据", "流动负债："))


def test_a_heading_below_the_top_zone_does_not_count():
    """A statement's continuation page can carry a numbered line well down the page; only the
    opening lines answer "does this page START a note"."""
    deep = _lines("合并利润表", "本期金额", "上期金额", "营业收入", "营业成本",
                  "销售费用", "管理费用", "10、存货")
    assert not _opens_with_zh_note_heading(deep)


def test_the_running_header_is_skipped_before_counting():
    """Every page of the measured filing begins with 湖北能源集团股份有限公司2024 年年度报告全文 and
    its folio; if those consumed the top-line budget the heading beneath them would be missed."""
    assert _opens_with_zh_note_heading(
        _lines("湖北能源集团股份有限公司2024 年年度报告全文", "180", "10、存货"))


# ── the invariant's anchor ───────────────────────────────────────────────────────────────────────

def test_the_notes_anchor_skips_an_untitled_face_page():
    """THE COVER-PAGE PATH. A one-page FACE at index 0 with no title must not anchor the notes:
    front matter decoded as NOTES after it is still front matter."""
    path = [_FACE] + [_NOTES] * 4 + [_FACE, _FACE] + [_NOTES] * 3
    feats = [_Feat(False)] * 5 + [_Feat(True), _Feat(False)] + [_Feat(False)] * 3
    out = _notes_follow_the_face(path, feats=feats)
    assert out[1:5] == [_PRE] * 4
    assert out[5] == _FACE and out[6] == _FACE
    assert out[7:] == [_NOTES] * 3


def test_a_face_page_printed_past_the_notes_survives():
    """Only the FIRST titled face page is an anchor — an HKEX filing prints the Company's own
    balance sheet past note 40 and that is what the NOTES -> FACE transition exists for."""
    path = [_PRE, _FACE, _NOTES, _NOTES, _FACE, _NOTES]
    feats = [_Feat(False), _Feat(True), _Feat(False), _Feat(False), _Feat(True), _Feat(False)]
    assert _notes_follow_the_face(path, feats=feats) == path


def test_with_no_titled_face_page_the_old_anchor_is_used():
    """FAIL OPEN, and to the previous behaviour rather than to nothing."""
    assert _notes_follow_the_face([_NOTES, _NOTES, _FACE, _NOTES],
                                  feats=[_Feat(False)] * 4) == [_PRE, _PRE, _FACE, _NOTES]


def test_without_feats_the_behaviour_is_unchanged():
    assert _notes_follow_the_face([_NOTES, _FACE, _NOTES]) == [_PRE, _FACE, _NOTES]


def test_a_filing_with_no_face_page_keeps_its_notes():
    """A notes section uploaded on its own would otherwise lose every page."""
    assert _notes_follow_the_face([_NOTES] * 3, feats=[_Feat(False)] * 3) == [_NOTES] * 3
