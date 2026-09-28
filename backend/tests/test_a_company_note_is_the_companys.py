"""A COMPANY-ONLY NOTE'S FIGURES ARE THE COMPANY'S — on the table, on its rows, and in prose.

NEW FILE -> backend/tests/test_a_company_note_is_the_companys.py

THREE DEFECTS OF ONE KIND, each measured on the three CAS reference filings.

1. THE ROWS OF A COMPANY-ONLY NOTE WERE TAGGED THE GROUP'S. `notes_extract` put the chapter's
   STANDALONE on the TABLE and nowhere else, while the row builder — reading a note that prints no
   basis column — tagged every value CONSOLIDATED: 95 values on 688008, 248 on 000709 and 227 on
   300319 sat in standalone tables tagged consolidated. Everything that reads a figure's OWN basis
   read them as the group's, including the model's cited rows (`note_sourced._figures_by_basis`),
   so the earlier fix that files a cited row under its own basis could not work on a real filing.
   Its test built the row with a standalone value by hand, which the extractor never produced; the
   tests below go through `extract_note_tables` instead.

2. A NOTE CONTINUED ACROSS A CHAPTER BOUNDARY TOOK THE BASIS OF THE PAGE, not of the chapter it
   was printed under — although it already kept that chapter's NUMBER. Both directions occur:
   300319's 十七、1 (其他重要事项, the group's) spills onto the page where 母公司财务报表主要项目注释
   opens and was tagged standalone; 000709's 十八、1 投资收益明细情况 (the parent's) spills onto the
   page where 补充资料 opens and was tagged the group's.

3. `_prose_basis` VOTED OVER THE FACE, the wrong population: 300319's face carries 335 standalone
   values against 260 consolidated, so every prose figure there would be filed in the parent's
   column. It now takes the basis of the note the sentence came from, then consolidated wherever a
   consolidated statement exists, and only then the face's own.

MEASURED MOVEMENT: 0 figures and 0 structural statuses on all five reference filings, on both
routes. That is the corpus not exercising these paths, not the fix doing nothing: the prose route
writes no figure on any of the five, and no published figure there is read off a company-only note
row. The tests pin the behaviour directly for the filing that does.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import note_sourced as ns
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word
from app.stages.note_sourced import _prose_basis

_COMPANY = ["十九", 19, "母公司财务报表主要项目注释"]
_GROUP = ["七", 7, "合并财务报表项目注释"]


def _w(text: str, x0: float, y0: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y0, x1=x0 + 0.06, y1=y0 + 0.010))


def _words(*lines):
    return [_w(tok, 0.06 + j * 0.2, 0.10 + i * 0.02)
            for i, line in enumerate(lines) for j, tok in enumerate(line)]


def _tables(*lines, chapter, carry=None):
    return extract_note_tables(_words(*lines), page_index=5, document_id=None,
                               source_kind="native", chapter=list(chapter), carry_note=carry)


def _bases(table) -> set[str]:
    return {str(getattr(ev.basis, "value", ev.basis)) for row in table.items
            for ev in (row.values or {}).values()}


# ── 1. the rows carry the table's basis ───────────────────────────────────────────────────────

def test_a_company_only_notes_rows_are_the_companys() -> None:
    tables = _tables(["1.", "应收账款"], ["账面余额", "1,000", "900"], chapter=_COMPANY)
    table = next(t for t in tables if t.items)

    assert table.basis == Basis.STANDALONE
    assert _bases(table) == {"standalone"}, "the table said standalone and its rows said group"


def test_a_group_notes_rows_are_untouched() -> None:
    tables = _tables(["1.", "应收账款"], ["账面余额", "1,000", "900"], chapter=_GROUP)
    table = next(t for t in tables if t.items)

    assert table.basis is None
    assert _bases(table) == {"consolidated"}


def test_a_cited_company_row_resolves_to_the_companys_basis() -> None:
    """THE END OF THE WIRE the earlier fix could not reach: the model cites a row from the company
    chapter and the resolver files it standalone — through the real extractor, not a hand-built
    row."""
    tables = _tables(["1.", "应收账款"], ["账面余额", "1,000", "900"], chapter=_COMPANY)
    cite = SimpleNamespace(note=tables[0].note_number, caption="账面余额", amount="", quote="",
                           statement="", page=None)

    resolved, _ = ns.resolve_sources([cite], tables)

    assert resolved and resolved[0]["basis"] == "standalone", resolved


# ── 2. a continued note keeps the basis of the chapter it began in ────────────────────────────

def test_a_group_note_continued_onto_the_company_chapters_page_stays_the_groups() -> None:
    """300319's 十七、1: the rows before the first heading on this page are the group note's."""
    tables = _tables(["星源电子", "1,000", "900"], ["1.", "应收账款"], ["账面余额", "5", "4"],
                     chapter=_COMPANY, carry=("十七、1", "资产负债表日存在的重要或有事项", None))
    carried = next(t for t in tables if t.note_number == "十七、1")
    opened = next(t for t in tables if t.note_number != "十七、1")

    assert carried.basis is None and _bases(carried) == {"consolidated"}
    assert opened.basis == Basis.STANDALONE and _bases(opened) == {"standalone"}


def test_a_company_note_continued_onto_a_group_chapters_page_stays_the_companys() -> None:
    """000709's 十八、1 投资收益明细情况, spilling onto the page where 补充资料 opens."""
    tables = _tables(["债务重组", "1,000", "900"],
                     chapter=["十九", 19, "补充资料"],
                     carry=("十八、1", "投资收益明细情况", Basis.STANDALONE))
    carried = next(t for t in tables if t.note_number == "十八、1")

    assert carried.basis == Basis.STANDALONE and _bases(carried) == {"standalone"}


def test_a_two_element_carry_keeps_the_pages_basis_as_before() -> None:
    tables = _tables(["债务重组", "1,000", "900"], chapter=_COMPANY, carry=("十八、1", "投资收益"))

    assert next(t for t in tables if t.note_number == "十八、1").basis == Basis.STANDALONE


def test_the_pdf_walker_carries_the_basis() -> None:
    """The one caller, asserted by what it passes — a two-element carry would silently restore the
    page's basis for every continued note."""
    import inspect

    from app.services import pdf_extract

    src = inspect.getsource(pdf_extract)
    assert "tables[-1].note_number, tables[-1].title, tables[-1].basis" in src


# ── 3. a prose figure takes the basis of its note ─────────────────────────────────────────────

def _doc(face: dict[str, int], notes=()) -> DocumentModel:
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.line_items = []
    for basis, n in face.items():
        for i in range(n):
            li = LineItem(source_label=f"{basis} {i}", canonical_key=f"k{basis}{i}")
            li.values[str(i)] = ExtractedValue(basis=Basis(basis), period_label="current",
                                               value=Decimal("1"), value_raw=Decimal("1"))
            doc.line_items.append(li)
    doc.notes = list(notes)
    return doc


def test_a_consolidated_note_is_the_groups_however_many_company_lines_the_face_prints() -> None:
    """300319: the face votes standalone 335 to 260, and the note is the group's."""
    group_note = _tables(["1.", "折旧"], ["折旧", "5", "4"], chapter=_GROUP)
    doc = _doc({"standalone": 335, "consolidated": 260}, group_note)

    assert _prose_basis(doc, [group_note[0].note_number]) == "consolidated"
    assert _prose_basis(doc) == "consolidated"


def test_a_company_only_notes_sentence_is_the_companys() -> None:
    company_note = _tables(["1.", "折旧"], ["折旧", "5", "4"], chapter=_COMPANY)
    doc = _doc({"consolidated": 500, "standalone": 10}, company_note)

    assert _prose_basis(doc, [company_note[0].note_number]) == "standalone"


def test_notes_that_disagree_fall_back_to_the_group() -> None:
    notes = (_tables(["1.", "折旧"], ["折旧", "5", "4"], chapter=_GROUP)
             + _tables(["2.", "折旧"], ["折旧", "5", "4"], chapter=_COMPANY))
    doc = _doc({"consolidated": 1}, notes)

    assert _prose_basis(doc, [n.note_number for n in notes]) == "consolidated"


def test_a_filing_with_no_group_files_under_the_company() -> None:
    assert _prose_basis(_doc({"standalone": 40})) == "standalone"
    assert _prose_basis(_doc({})) == "consolidated"
