"""A note section is numbered under the chapter in force WHERE IT STARTS, not the page's last.

A mainland filing numbers its notes within each chapter, so the chapter is half of a note's
identity (七、9 is the group's 其他应收款, 十九、2 the parent company's). The chapter used to be read
once per page and applied to every section on it, so a page that ENDS one chapter and OPENS the next
numbered the sections above the heading under the chapter the heading opens. Measured on 澜起科技
688008 page 242: the related-party balances 应收项目 and 应付项目 came out as 十五、1 and 十五、2 —
share-based payment's numbers, under the title 股份支付 — and no related-party pattern could find
them. 迈捷 300319's parent-company revenue note was numbered into the supplementary chapter the same
way and read as the GROUP's revenue: 4,498,971,725.66 published against a printed 3,149,984,434.89.

A chapter may also print its content with no note heading of its own — 000709's 八、研发支出 goes
straight from the heading into its table — and that content is the chapter's own section.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word

H = 0.0107


def _w(text: str, x0: float, x1: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _read(words, chapter):
    return extract_note_tables(words, page_index=242, document_id="d", source_kind="native",
                               chapter=chapter)


PAGE_242 = [
    _w("（1）", 0.151, 0.193, 0.119), _w("应收项目", 0.222, 0.289, 0.119),
    _w("项目名称", 0.198, 0.268, 0.160), _w("关联方", 0.385, 0.437, 0.160),
    _w("期末账面余额", 0.533, 0.639, 0.160), _w("期初账面余额", 0.735, 0.840, 0.160),
    _w("英特尔公司", 0.333, 0.421, 0.180), _w("1,327,251.80", 0.572, 0.664, 0.180),
    _w("663,875.64", 0.813, 0.892, 0.180),
    _w("十五、", 0.151, 0.201, 0.586), _w("股份支付", 0.222, 0.289, 0.586),
    _w("1.", 0.151, 0.163, 0.611), _w("各项权益工具", 0.186, 0.287, 0.611),
    _w("项目", 0.198, 0.228, 0.640), _w("本期发生额", 0.533, 0.620, 0.640),
    _w("上期发生额", 0.735, 0.822, 0.640),
    _w("授予", 0.151, 0.185, 0.660), _w("1,000.00", 0.580, 0.664, 0.660),
    _w("2,000.00", 0.813, 0.892, 0.660),
]


def test_the_sections_above_a_chapter_heading_keep_the_chapter_they_began_in():
    chapter = ["十四", 14, "关联方及关联交易"]
    tables = _read(PAGE_242, chapter)
    got = [(t.note_number, t.title, t.chapter_title) for t in tables if t.items]
    assert got == [("十四、1", "应收项目", "关联方及关联交易"),
                   ("十五、1", "各项权益工具", "股份支付")]
    # and the chapter carried to the next page is the one this page ended in
    assert chapter == ["十五", 15, "股份支付"]


def test_a_chapter_that_opens_straight_onto_a_table_is_its_own_section():
    words = [
        _w("1.", 0.151, 0.163, 0.100), _w("租赁", 0.186, 0.230, 0.100),
        _w("项目", 0.198, 0.228, 0.130), _w("本期发生额", 0.533, 0.620, 0.130),
        _w("上期发生额", 0.735, 0.822, 0.130),
        _w("租赁收入", 0.151, 0.215, 0.150), _w("75,376,917.42", 0.560, 0.664, 0.150),
        _w("70,000,000.00", 0.790, 0.892, 0.150),
        _w("八、", 0.151, 0.185, 0.300), _w("研发支出", 0.200, 0.270, 0.300),
        _w("项目", 0.198, 0.228, 0.330), _w("本期发生额", 0.533, 0.620, 0.330),
        _w("上期发生额", 0.735, 0.822, 0.330),
        _w("人工费", 0.151, 0.200, 0.350), _w("157,322,242.69", 0.560, 0.664, 0.350),
        _w("147,487,768.05", 0.790, 0.892, 0.350),
        _w("合计", 0.151, 0.185, 0.370), _w("2,343,028,433.26", 0.545, 0.664, 0.370),
        _w("2,200,000,000.00", 0.775, 0.892, 0.370),
    ]
    tables = _read(words, ["七", 7, "合并财务报表项目注释"])
    by_number = {t.note_number: [i.raw_label for i in t.items] for t in tables if t.items}
    assert by_number == {"七、1": ["租赁收入"], "八、": ["人工费", "合计"]}
    own = next(t for t in tables if t.note_number == "八、")
    assert (own.title, own.chapter_title) == ("研发支出", "研发支出")
