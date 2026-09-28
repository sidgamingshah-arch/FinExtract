"""A note table too short for its figures to show its columns reads them off its own header.

`_value_column_bands` establishes a table's columns from its figures, and rightly refuses to on a
table with fewer than two rows that use more than one column. A mainland related-party note is
exactly that table: 澜起科技 688008 prints 应收项目, 应付项目 and 其他项目 as three tables ONE ROW
deep. With no columns there was no grid, and the four figures under

    期末余额{账面余额 | 坏账准备}   期初余额{账面余额 | 坏账准备}

went to the model as current, prior, col2, col3 — this year's 坏账准备 of 431.30 presented as LAST
YEAR'S balance with 英特尔公司. And a row printing a nil current balance beside its opening one
published the opening figure as the current one, because a lone figure was the first figure.

The geometry below is copied from those pages, because the offsets between a caption and the
right-aligned figures under it are exactly what the tolerance is measured against.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

H = 0.009


def _w(text: str, x0: float, x1: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _build(words):
    logs: list[str] = []
    items, _ = build_line_items(words, page_index=242, document_id="d", source_kind="native",
                                on_face=False, log=logs.append)
    return items, logs


def _figures(item) -> dict[str, str]:
    return {ev.period_label: str(ev.value) for ev in item.values.values()}


# 688008 page 242, 应收项目 — period band, label header, measure band, one data row.
RECEIVABLES = [
    _w("单位：元", 0.699, 0.769, 0.159), _w("币种：人民币", 0.787, 0.893, 0.159),
    _w("期末余额", 0.463, 0.534, 0.175), _w("期初余额", 0.723, 0.794, 0.175),
    _w("项目名称", 0.168, 0.239, 0.184), _w("关联方", 0.297, 0.350, 0.184),
    _w("账面余额", 0.405, 0.475, 0.192), _w("坏账准备", 0.521, 0.592, 0.192),
    _w("账面余额", 0.643, 0.714, 0.192), _w("坏账准备", 0.787, 0.857, 0.192),
    _w("英特尔公司", 0.274, 0.362, 0.209), _w("86,260.80", 0.419, 0.489, 0.209),
    _w("431.30", 0.558, 0.606, 0.209), _w("1,914,028.85", 0.641, 0.733, 0.209),
    _w("9,570.14", 0.831, 0.893, 0.209),
]

# 688008 page 242, 应付项目 — one header row, and a nil current balance printed as a dash.
PAYABLES = [
    _w("单位：元", 0.699, 0.769, 0.287), _w("币种：人民币", 0.787, 0.893, 0.287),
    _w("项目名称", 0.198, 0.268, 0.304), _w("关联方", 0.385, 0.437, 0.304),
    _w("期末账面余额", 0.533, 0.639, 0.304), _w("期初账面余额", 0.735, 0.840, 0.304),
    _w("英特尔公司", 0.333, 0.421, 0.321), _w("-", 0.659, 0.665, 0.321),
    _w("1,403,741.56", 0.800, 0.893, 0.321),
]


def test_a_one_row_table_is_read_as_period_by_measure():
    items, logs = _build(RECEIVABLES)
    row = next(li for li in items if li.source_label == "英特尔公司")
    assert _figures(row) == {"current": "86260.80", "current:allowance": "431.30",
                             "prior": "1914028.85", "prior:allowance": "9570.14"}, logs
    assert any("value_columns=header_declared(4)" in m for m in logs), logs


def test_a_lone_figure_under_the_opening_column_is_the_opening_balance():
    items, logs = _build(PAYABLES)
    row = next(li for li in items if li.source_label.startswith("英特尔公司"))
    assert _figures(row) == {"prior": "1403741.56"}, logs


def test_a_nil_dash_is_not_part_of_the_caption():
    items, _ = _build(PAYABLES)
    assert [li.source_label for li in items] == ["英特尔公司"]


def test_a_figure_under_no_declared_caption_keeps_todays_reading():
    """The header is held to the figures. A row printing a figure between two declared columns
    is not explained by the header, so no column is declared from it and nothing is relabelled."""
    stray = RECEIVABLES + [_w("注：", 0.151, 0.170, 0.240), _w("12.00", 0.615, 0.635, 0.240)]
    items, logs = _build(stray)
    assert not any("header_declared" in m for m in logs), logs
    row = next(li for li in items if li.source_label == "英特尔公司")
    assert not any(":" in (ev.period_label or "") for ev in row.values.values())


def test_a_comparative_first_table_is_read_by_its_captions_not_its_order():
    """迈捷 300319's 应付项目 sets each period caption wider than the figures under it, so the
    caption's centre falls just outside the value area and only one of the two was read — and a
    table read by position files every figure a year out when the comparative is printed first.
    Half a column's width of a column is over it."""
    words = [
        _w("项目名称", 0.166, 0.227, 0.433), _w("关联方", 0.376, 0.421, 0.433),
        # the comparative printed FIRST, in the 期末 caption's own geometry
        _w("期初账面余额", 0.556, 0.647, 0.433), _w("期末账面余额", 0.759, 0.849, 0.433),
        _w("宜宾益邦科技有限责任公司", 0.307, 0.488, 0.464),
        _w("291,181.25", 0.618, 0.693, 0.464), _w("92,055.70", 0.828, 0.896, 0.464),
        _w("深圳市特发信息技术服务有限公司", 0.307, 0.488, 0.486),
        _w("1,525,227.64", 0.603, 0.693, 0.486), _w("1,385,223.97", 0.805, 0.896, 0.486),
        _w("深圳特发东智科技有限公司", 0.307, 0.488, 0.507),
        _w("238,366.72", 0.821, 0.896, 0.507),
    ]
    items, logs = _build(words)
    row = next(li for li in items if li.source_label == "宜宾益邦科技有限责任公司")
    assert _figures(row) == {"prior": "291181.25", "current": "92055.70"}, logs
    lone = next(li for li in items if li.source_label == "深圳特发东智科技有限公司")
    assert _figures(lone) == {"current": "238366.72"}, logs
