"""A related-party note's row is a COUNTERPARTY, and the line item it is filed under is its group.

A mainland 关联方应收应付款项 table captions its two label columns 项目名称 | 关联方: the line item
(应收账款, 预付款项) in the outer column, the counterparty in the inner one. Two layouts reach this
reader, and both lost the line item:

* 澜起科技 688008 prints the category ON THE SAME LINE as the counterparty, one row per table. The
  geometric category reader needs three valued rows before a margin is a column, so it found none:
  the row was captioned "应收账款英特尔公司" and carried no group.
* 迈捷 300319 prints it on a line of its own directly under the header — and the header's own
  项目名称 stands in the outer column above it, so the merged-cell join read the two as ONE
  category, "项目名称应付账款".

The header names both columns, so the header decides: a word left of the midpoint between 项目名称
and 关联方 is the category, and the rest is the caption.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word

H = 0.009


def _w(text: str, x0: float, x1: float, y: float, h: float = H) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + h))


def _rows(words):
    tables = extract_note_tables(words, page_index=242, document_id="d", source_kind="native",
                                 chapter=[None, 0, ""])
    return [(r.group_hint, r.raw_label) for t in tables for r in t.items]


HEADING = [_w("（1）", 0.151, 0.193, 0.119), _w("应收项目", 0.222, 0.289, 0.119)]
SAME_LINE = HEADING + [
    _w("期末余额", 0.463, 0.534, 0.175), _w("期初余额", 0.723, 0.794, 0.175),
    _w("项目名称", 0.168, 0.239, 0.184), _w("关联方", 0.297, 0.350, 0.184),
    _w("账面余额", 0.405, 0.475, 0.192), _w("坏账准备", 0.521, 0.592, 0.192),
    _w("账面余额", 0.643, 0.714, 0.192), _w("坏账准备", 0.787, 0.857, 0.192),
    _w("应收账款", 0.151, 0.221, 0.209), _w("英特尔公司", 0.274, 0.362, 0.209),
    _w("86,260.80", 0.419, 0.489, 0.209), _w("431.30", 0.558, 0.606, 0.209),
    _w("1,914,028.85", 0.641, 0.733, 0.209), _w("9,570.14", 0.831, 0.893, 0.209),
]


def test_a_category_on_the_counterpartys_line_is_its_group_not_its_caption():
    assert _rows(SAME_LINE) == [("应收账款", "英特尔公司")]


def test_the_header_is_not_the_first_category():
    """300319 page 178's own boxes: the header's 项目名称 ends 0.0064 above 应付账款, inside the
    merged-cell join's 0.6 of a line — which is how the two became one category."""
    h = 0.0107
    words = [_w("（2）", 0.095, 0.140, 0.379, h), _w("应付项目", 0.148, 0.219, 0.379, h),
             _w("项目名称", 0.166, 0.227, 0.4325, h), _w("关联方", 0.376, 0.421, 0.4325, h),
             _w("期末账面余额", 0.556, 0.647, 0.4325, h),
             _w("期初账面余额", 0.759, 0.849, 0.4325, h),
             _w("应付账款", 0.104, 0.165, 0.4496, h),
             _w("宜宾益邦科技有限责任公司", 0.307, 0.488, 0.4642, h),
             _w("291,181.25", 0.618, 0.693, 0.4642, h), _w("92,055.70", 0.828, 0.896, 0.4642, h),
             _w("深圳特发东智科技有限公司", 0.307, 0.488, 0.4880, h),
             _w("238,366.72", 0.821, 0.896, 0.4880, h),
             _w("应付票据", 0.104, 0.165, 0.5220, h),
             _w("深圳特发东智科技有限公司", 0.307, 0.488, 0.5366, h),
             _w("519,786.66", 0.821, 0.896, 0.5366, h)]
    assert _rows(words) == [("应付账款", "宜宾益邦科技有限责任公司"),
                            ("应付账款", "深圳特发东智科技有限公司"),
                            ("应付票据", "深圳特发东智科技有限公司")]


def test_a_total_across_both_label_columns_keeps_its_caption_and_ends_the_category():
    words = SAME_LINE + [_w("合计", 0.151, 0.181, 0.230), _w("86,260.80", 0.419, 0.489, 0.230),
                         _w("431.30", 0.558, 0.606, 0.230),
                         _w("1,914,028.85", 0.641, 0.733, 0.230),
                         _w("9,570.14", 0.831, 0.893, 0.230)]
    assert _rows(words) == [("应收账款", "英特尔公司"), ("", "合计")]


def test_a_note_whose_header_names_one_label_column_is_left_alone():
    """河钢股份 000709 heads its related-party table with 项目 alone and states the line item as a
    colon sub-heading — nothing here declares a second label column, so nothing is taken out of a
    caption."""
    words = [_w("（1）", 0.130, 0.160, 0.080), _w("应收项目", 0.161, 0.231, 0.080),
             _w("项目", 0.279, 0.309, 0.281), _w("期末余额", 0.554, 0.614, 0.281),
             _w("期初余额", 0.721, 0.782, 0.281),
             _w("其他应收款：", 0.104, 0.195, 0.310),
             _w("唐山唐钢气体有限公司", 0.104, 0.255, 0.334),
             _w("118,850.00", 0.598, 0.666, 0.334), _w("28,026,677.88", 0.733, 0.820, 0.334)]
    assert _rows(words) == [("其他应收款：", "唐山唐钢气体有限公司")]
