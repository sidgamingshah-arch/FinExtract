"""A bracketed table inside a numbered note is a table OF that note, not a note of its own.

A mainland note numbers three levels deep — 十二、 the chapter, 6、 the note, （1）/① the tables
inside it — and every level became a note NUMBER, so （1）应收项目 was note 十二、1 and
①采购商品情况表 was 十二、1 too. 河钢股份 000709's 十二、1 pooled nineteen tables from four different
notes, and the 十二、5 and 十二、6 that the rulebook and the face name held no rows at all.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word

H = 0.0107


def _w(text: str, x0: float, x1: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _table_row(caption: str, amount: str, y: float) -> list[Word]:
    return [_w(caption, 0.104, 0.300, y), _w(amount, 0.740, 0.820, y)]


def _read(words, carry=None, chapter=None):
    tables = extract_note_tables(words, page_index=175, document_id="d", source_kind="native",
                                 chapter=chapter if chapter is not None else ["十二", 12, "关联方及关联交易"],
                                 carry_note=carry)
    return [(t.note_number, t.title) for t in tables if t.items]


def test_bracketed_and_circled_tables_take_their_notes_number():
    words = ([_w("5、", 0.131, 0.150, 0.10), _w("关联交易情况", 0.155, 0.264, 0.10),
              _w("（1）", 0.122, 0.150, 0.13), _w("购销商品、提供和接受劳务的关联交易", 0.155, 0.466, 0.13),
              _w("①", 0.130, 0.148, 0.16), _w("采购商品情况表", 0.166, 0.290, 0.16)]
             + _table_row("承德燕山气体有限公司", "367,385,967.72", 0.19)
             + [_w("6、", 0.131, 0.150, 0.25), _w("关联方应收应付款项", 0.155, 0.300, 0.25),
                _w("（1）", 0.122, 0.150, 0.28), _w("应收项目", 0.155, 0.231, 0.28)]
             + _table_row("唐山唐钢气体有限公司", "118,850.00", 0.31))
    assert _read(words) == [("十二、5", "采购商品情况表"), ("十二、6", "应收项目")]


def test_a_note_continued_onto_the_next_page_keeps_its_tables():
    words = ([_w("②", 0.130, 0.148, 0.10), _w("应付项目", 0.161, 0.231, 0.10)]
             + _table_row("河钢集团有限公司", "546,069,377.65", 0.13))
    assert _read(words, carry=("十二、6", "应收项目", None)) == [("十二、6", "应付项目")]


def test_a_chapter_heading_closes_the_note():
    words = ([_w("十三、", 0.095, 0.140, 0.10), _w("股份支付", 0.145, 0.237, 0.10),
              _w("（1）", 0.122, 0.150, 0.13), _w("各项权益工具", 0.155, 0.260, 0.13)]
             + _table_row("授予", "1,000.00", 0.16))
    assert _read(words, carry=("十二、6", "应收项目", None)) == [("十三、1", "各项权益工具")]


def test_bracketed_notes_with_no_numbered_note_above_keep_their_numbers():
    words = ([_w("（1）", 0.122, 0.150, 0.10), _w("货币资金", 0.155, 0.231, 0.10)]
             + _table_row("银行存款", "1,000.00", 0.13)
             + [_w("（2）", 0.122, 0.150, 0.19), _w("应收票据", 0.155, 0.231, 0.19)]
             + _table_row("银行承兑汇票", "2,000.00", 0.22))
    assert _read(words, chapter=["七", 7, "合并财务报表项目注释"]) == [
        ("七、1", "货币资金"), ("七、2", "应收票据")]
