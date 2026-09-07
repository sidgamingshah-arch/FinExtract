"""A note's IDENTITY is its chapter plus its number, not its number alone.

A mainland (CSRC) annual report numbers its notes WITHIN each top-level chapter:

    七、合并财务报表项目注释         notes 1 … 80
    十四、关联方及关联交易           notes restart at 1
    十九、母公司财务报表主要项目注释   notes restart at 1 again

Keeping only the trailing number leaves the identity ambiguous, and not rarely — on 澜起科技
688008 FY2024, 15 of 48 note numbers carried two or more different headings. "note 2" named both
the group's 交易性金融资产 (七、2) and the parent company's 其他应收款 (十九、2).

WHAT THAT COST. Every mechanism that identifies a note by its number was unreliable for those:
the note→face tie, each spec service's note lookup, and the restatement ledger that collapses two
printings of ONE balance by comparing note numbers. Other Receivables (CP) published
2,484,202,201.08 against a printed 4,143,856.36, because the parent company's 1,247,570,989.98
was summed with the group's 4,143,856.36 — six hundred times the figure.

The filing itself spells the identity out: the balance sheet's 附注 column reads 七、9, not 9.
"""
from __future__ import annotations

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.services.notes_extract import (
    chapter_ordinal,
    extract_note_tables,
    qualified_note_number,
    read_chapter,
    split_note_number,
)
from app.services.row_reconstruct import Word, _group_rows, row_tolerance


def _w(text: str, x0: float, y0: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + 0.09, y1=y0 + 0.010))


def _rows(*lines):
    """Each line as its own printed row, top to bottom."""
    words = []
    for i, line in enumerate(lines):
        for j, token in enumerate(line):
            words.append(_w(token, 0.10 + j * 0.16, 0.10 + i * 0.020))
    return _group_rows(words, row_tolerance(words, "native"))


# ── the numeral ────────────────────────────────────────────────────────────────────────────────

def test_a_cjk_chapter_numeral_reads_as_its_ordinal():
    assert [chapter_ordinal(n) for n in ("一", "九", "十", "十一", "十九", "二十", "二十三")] \
        == [1, 9, 10, 11, 19, 20, 23]


def test_a_string_that_is_not_a_chapter_numeral_is_refused():
    """Refused rather than approximated: a numeral this reader guesses at is a chapter it invents,
    and every note under it gets an identity no other reader will form the same way."""
    for text in ("十十", "十甲", "甲", "", "7", "七七七七", "百"):
        assert chapter_ordinal(text) is None, text


# ── the reader ─────────────────────────────────────────────────────────────────────────────────

def test_a_chapter_heading_is_read_off_the_page():
    found = read_chapter(_rows(["七、合并财务报表项目注释"], ["1．货币资金"]), seen=6)

    assert found == ("七", 7, "合并财务报表项目注释")


def test_an_in_note_sub_enumeration_is_not_a_chapter():
    """THE WHOLE DISCRIMINATOR. The same CJK-numeral form is printed by three different things:

      * the top-level chapters          — 七、合并财务报表项目注释
      * a sub-enumeration INSIDE a note — 一、账面原值 / 二、累计折旧 in the 固定资产 note
      * a statement's own face lines     — 一、营业总收入 (a face page, so never seen here)

    A chapter's number only ever goes up; a sub-enumeration restarts at 一、. So a numeral that is
    not GREATER than the highest chapter already seen is not a chapter. Measured on 688008,
    walking the notes from their first page, this recovers all 18 chapters — 五、 through 二十、 —
    and admits none of the note-internal enumerations.
    """
    rows = _rows(["一、账面原值"], ["期初余额", "53,876,698.92"], ["二、累计折旧"])

    assert read_chapter(rows, seen=7) is None


def test_the_chapter_after_the_highest_seen_is_taken():
    rows = _rows(["一、账面原值"], ["十九、母公司财务报表主要项目注释"], ["二、累计折旧"])

    assert read_chapter(rows, seen=18) == ("十九", 19, "母公司财务报表主要项目注释")


def test_a_row_carrying_figures_is_never_a_chapter_heading():
    rows = _rows(["八、研发支出", "121,519,507.18"])

    assert read_chapter(rows, seen=7) is None


def test_a_sentence_opening_with_a_numeral_is_not_a_chapter():
    rows = _rows(["八、本公司于本期内收购了子公司，其对价为现金。"])

    assert read_chapter(rows, seen=7) is None


# ── the identity ───────────────────────────────────────────────────────────────────────────────

def test_the_identity_is_the_chapter_and_the_number():
    assert qualified_note_number("七", "9") == "七、9"
    assert qualified_note_number("十九", "2") == "十九、2"


def test_a_filing_with_no_chapters_keeps_bare_numbers():
    """An English/HKEX filing prints no chapter headings and must be byte-identical to before."""
    assert qualified_note_number(None, "15") == "15"
    assert qualified_note_number("七", None) is None


def test_forming_an_identity_twice_forms_the_same_identity():
    """A note table that runs over more than one page is handed to the next page as its
    ``carry_note``, and ``services.pdf_extract`` carries the last table's ``note_number`` — which
    is already an identity. Stamping the chapter on again produced, on 688008, 七、七、1 and then
    七、七、七、17 and then 七、七、七、七、九、9 as the note continued; around 80 of the 219 note tables
    ``stages.prune_notes`` dropped had an identity of that shape, so the face citation looking for
    them could never match one.
    """
    assert qualified_note_number("七", qualified_note_number("七", "1")) == "七、1"
    assert qualified_note_number("七", "七、七、1") == "七、七、1"     # already spoiled, not re-spoiled


def test_a_note_carried_onto_the_next_chapters_page_keeps_its_own_chapter():
    """THE REASON IDEMPOTENCE IS THE RIGHT RULE and not merely the cheap one. 十九、十八、8 and
    八、七、81 were both produced by a note that STARTED in one chapter and continued onto a page
    where the reader had already advanced to the next. The chapter a note was printed under is the
    chapter it belongs to; the page it spills onto does not re-home it."""
    assert qualified_note_number("十九", "十八、8") == "十八、8"


def test_the_identity_splits_back_for_sorting():
    """`split_note_number` is the inverse, and it exists so a sort key or a comparison never
    takes the identity apart with a regex of its own — two definitions of what an identity IS
    would drift, and the sort keys are exactly where that already happened."""
    assert split_note_number("七、9") == (7, "9")
    assert split_note_number("十九、2") == (19, "2")
    assert split_note_number("15") == (0, "15")      # chapter 0 sorts before every chapter
    assert split_note_number("16(b)") == (0, "16(b)")
    assert split_note_number(None) == (0, "")


# ── end to end through the extractor ───────────────────────────────────────────────────────────

def _note_words(*lines):
    words = []
    for i, line in enumerate(lines):
        for j, token in enumerate(line):
            words.append(_w(token, 0.10 + j * 0.30, 0.10 + i * 0.020))
    return words


def test_a_note_extracted_under_a_chapter_carries_it():
    chapter: list = [None, 0, ""]
    tables = extract_note_tables(
        _note_words(["七、合并财务报表项目注释"], ["9. 其他应收款"],
                    ["押金、保证金", "4,114,812.47"], ["其他", "29,043.89"]),
        page_index=187, document_id="d1", source_kind="native", chapter=chapter)

    assert [t.note_number for t in tables] == ["七、9"]
    assert tables[0].basis is None            # the group's ordinary note


def test_the_parent_company_chapter_makes_its_notes_company_only():
    """THE PAYOFF. A CSRC filing repeats every material balance for the parent company alone, so
    those notes state the SAME concepts as the group's with different figures. The chapter is the
    only thing on the page that says which is which — the heading is printed once, pages before
    the note — and `NotesTable.basis` is where the answer goes, so a consumer computing a
    CONSOLIDATED figure can decline a company-only note instead of pooling the two.
    """
    chapter: list = [None, 0, ""]
    tables = extract_note_tables(
        _note_words(["十九、母公司财务报表主要项目注释"], ["2. 其他应收款"],
                    ["合并范围内关联方资金拆借", "1,075,891,507.64"]),
        page_index=247, document_id="d1", source_kind="native", chapter=chapter)

    assert [t.note_number for t in tables] == ["十九、2"]
    assert tables[0].basis is Basis.STANDALONE


def test_the_chapter_carries_to_the_next_page():
    """A chapter heads a run of pages and is printed once, so the cell the caller owns is what
    keeps every later page under it."""
    chapter: list = [None, 0, ""]
    extract_note_tables(_note_words(["七、合并财务报表项目注释"], ["1. 货币资金"], ["库存现金", "1,204.00"]),
                        page_index=181, document_id="d1", source_kind="native", chapter=chapter)
    later = extract_note_tables(_note_words(["61. 营业收入和营业成本"], ["主营业务", "3,628,769,555.93"]),
                                page_index=218, document_id="d1", source_kind="native",
                                chapter=chapter)

    assert [t.note_number for t in later] == ["七、61"]


def test_passing_no_chapter_cell_leaves_every_number_bare():
    """The English path. `chapter=None` is what every non-mainland caller gets, and it must be
    the identity function."""
    tables = extract_note_tables(
        _note_words(["15. Trade receivables"], ["Gross", "3,410"]),
        page_index=40, document_id="d1", source_kind="native")

    assert [t.note_number for t in tables] == ["15"]
    assert tables[0].basis is None
