"""中山公用 000685's two reading faults.

THE NOTES' CHAPTERS ARE THEIR OWN (`pdf_extract`). The governance section prints chapters 八、 to
十七、 on pages the classifier calls notes, and the strictly-increasing chapter rule carried 十七 into
the notes, where it refused 三、 to 十七、: every note of chapters 七 to 十七 came out as 十七、84. The
financial statements open the notes' numbering: the chapter cell is cleared on the first
balance-sheet page.

A FIGURE WRAPPED OVER THE PAGE BREAK (`row_reconstruct.fuse_figures_wrapped_over_the_page`). The
ten-column lessee table ends a page on "530,09" / "703,66" and the next page opens with "1.75" /
"9.73" under them; 530,091.75 and 703,669.73 were in no row.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.geometry import BBox
from app.services.notes_extract import BEFORE_THE_STATEMENTS
from app.services.row_reconstruct import Word, fuse_figures_wrapped_over_the_page

H = 0.0107


def _w(text, x0, x1, y):
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _page_end(extra=()):
    return [_w("中山投", 0.104, 0.150, 0.8962), _w("土地使", 0.172, 0.217, 0.8962),
            _w("530,09", 0.243, 0.289, 0.8962), _w("703,66", 0.311, 0.356, 0.8962),
            _w("266", 0.882, 0.905, 0.9288), *extra]


def _page_start(extra=()):
    return [_w("中山公用事业集团股份有限公司2024", 0.549, 0.795, 0.0520),
            _w("资控股", 0.104, 0.150, 0.0873), _w("用权", 0.172, 0.202, 0.0873),
            _w("1.75", 0.259, 0.289, 0.0873), _w("9.73", 0.326, 0.356, 0.0873),
            _w("796,23", 0.513, 0.559, 0.1430), *extra]


def test_a_figure_wrapped_over_the_page_break_is_joined_on_its_own_page():
    words, ahead, joined = fuse_figures_wrapped_over_the_page(_page_end(), _page_start())
    assert joined == ["530,091.75", "703,669.73"]
    assert [w.text for w in words if w.text[0].isdigit()] == ["530,091.75", "703,669.73", "266"]
    assert "1.75" not in {w.text for w in ahead} and "9.73" not in {w.text for w in ahead}
    assert "796,23" in {w.text for w in ahead}


@pytest.mark.parametrize("end, start", [
    # a whole amount beside the heads: the last row is a row of figures, not a wrapped one
    (_page_end([_w("12,000.00", 0.400, 0.450, 0.8962)]), _page_start()),
    # text below the heads' row: the row did not end the page
    (_page_end([_w("说明：无。", 0.104, 0.300, 0.9100)]), _page_start()),
    # a third figure on the next page's first row: the counts differ
    (_page_end(), _page_start([_w("8.60", 0.529, 0.559, 0.0873)])),
    # a tail outside its head's column
    (_page_end(), [_w("1.75", 0.380, 0.410, 0.0873), _w("9.73", 0.420, 0.450, 0.0873)]),
])
def test_a_page_whose_last_row_did_not_wrap_is_left_alone(end, start):
    words, ahead, joined = fuse_figures_wrapped_over_the_page(end, start)
    assert joined == [] and words is end and ahead is start


# ------------------------------------------------------------------ end to end, through a PDF ---

fitz = pytest.importorskip("fitz")
W_PT, H_PT = 595.0, 842.0


def _put(page, x, y, text):
    page.insert_text((x, y), text, fontsize=8, fontname="china-s")


def _right(page, x1, y, text):
    width = fitz.get_text_length(text, fontname="china-s", fontsize=8)
    _put(page, x1 - width, y, text)


def _extract(pages, kinds):
    from app.config import get_settings
    from app.core.models.document import DocumentModel, PageSource
    from app.core.stage import PipelineContext
    from app.services.pdf_extract import extract_pdf

    doc = DocumentModel(filename="ar.pdf", content_hash="h")
    doc.pages = [PageSource(index=i, kind=kind, statement=statement, scope=scope,
                            width_pt=W_PT, height_pt=H_PT)
                 for i, (kind, statement, scope) in enumerate(kinds)]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    extract_pdf(pages, doc, ctx)
    return doc, ctx


def _governance_then_statements_then_notes() -> bytes:
    doc = fitz.open()
    gov = doc.new_page(width=W_PT, height=H_PT)
    _put(gov, 60, 40, "七、监事会工作情况")
    _put(gov, 60, 52, "本报告期内监事会共召开会议。")
    _put(gov, 60, 64, "1、员工数量")
    _put(gov, 60, 74, "项目"); _put(gov, 300, 74, "期末余额"); _put(gov, 450, 74, "期初余额")
    _put(gov, 60, 86, "在职员工"); _right(gov, 360, 86, "2,000.00"); _right(gov, 510, 86, "1,900.00")
    _put(gov, 60, 110, "十六、其他重大事项的说明")
    _put(gov, 60, 122, "本报告期内公司无其他重大事项。")
    _put(gov, 60, 130, "十七、公司子公司重大事项")
    _put(gov, 60, 150, "本报告期内公司子公司无重大事项。")
    face = doc.new_page(width=W_PT, height=H_PT)
    _put(face, 200, 60, "合并资产负债表")
    _put(face, 60, 90, "项目"); _put(face, 300, 90, "期末余额"); _put(face, 450, 90, "期初余额")
    for n, (cap, a, b) in enumerate((("货币资金", "1,000.00", "900.00"),
                                     ("资产总计", "1,000.00", "900.00"))):
        _put(face, 60, 110 + 18 * n, cap); _right(face, 360, 110 + 18 * n, a)
        _right(face, 510, 110 + 18 * n, b)
    notes = doc.new_page(width=W_PT, height=H_PT)
    _put(notes, 60, 80, "七、合并财务报表项目注释")
    _put(notes, 60, 100, "以下注释项目除非特别指出，期初指2024年1月1日。")
    _put(notes, 60, 130, "1、货币资金")
    _put(notes, 60, 150, "项目"); _put(notes, 300, 150, "期末余额"); _put(notes, 450, 150, "期初余额")
    _put(notes, 60, 170, "银行存款"); _right(notes, 360, 170, "1,000.00")
    _right(notes, 510, 170, "900.00")
    return doc.tobytes()


def test_a_chapter_read_before_the_statements_does_not_number_the_notes():
    from app.core.models.enums import PageKind

    doc, ctx = _extract(_governance_then_statements_then_notes(),
                        [(PageKind.NOTES, None, None),
                         (PageKind.FACE, "balance_sheet", "consolidated"),
                         (PageKind.NOTES, None, None)])
    numbers = {t.note_number for t in doc.notes if t.items}
    assert "七、1" in numbers, numbers
    assert not any(n.startswith("十七") for n in numbers if n), numbers
    assert any("statements_open_notes_numbering" in line for line in ctx.logs)
    # the governance section's own numbering keeps its numbers, out of the notes' namespace
    before = {t.note_number for t in doc.notes if 0 in t.source_pages}
    assert before and all(n.startswith(BEFORE_THE_STATEMENTS) for n in before), before
    notes = {t.note_number for t in doc.notes if 2 in t.source_pages}
    assert not before & notes, (before, notes)


def _lessee_table_over_the_break() -> bytes:
    doc = fitz.open()
    first = doc.new_page(width=W_PT, height=H_PT)
    _put(first, 60, 600, "（3）关联租赁情况")
    _put(first, 60, 630, "出租方名称"); _put(first, 120, 630, "租赁资产种类")
    _put(first, 200, 630, "本期发生额"); _put(first, 290, 630, "上期发生额")
    y = 760
    _put(first, 62, y, "中山投"); _put(first, 120, y, "土地使")
    _right(first, 250, y, "530,09"); _right(first, 340, y, "703,66")
    _put(first, 525, 790, "266")
    second = doc.new_page(width=W_PT, height=H_PT)
    _put(second, 330, 50, "中山公用事业集团股份有限公司2024")
    _put(second, 62, 80, "资控股"); _put(second, 120, 80, "用权")
    _right(second, 250, 80, "1.75"); _right(second, 340, 80, "9.73")
    _put(second, 62, 92, "集团有")
    _put(second, 62, 104, "限公司")
    _put(second, 60, 140, "关联租赁情况说明：无。")
    return doc.tobytes()


def test_the_wrapped_figure_reaches_the_note_s_row():
    from app.core.models.enums import PageKind

    doc, ctx = _extract(_lessee_table_over_the_break(),
                        [(PageKind.NOTES, None, None), (PageKind.NOTES, None, None)])
    assert any("figures_wrapped_over_page_break=530,091.75,703,669.73" in line
               for line in ctx.logs), [line for line in ctx.logs if "wrapped" in line]
    values = {ev.value for t in doc.notes for item in t.items for ev in item.values.values()}
    assert Decimal("530091.75") in values and Decimal("703669.73") in values, values
    assert Decimal("1.75") not in values and Decimal("9.73") not in values
