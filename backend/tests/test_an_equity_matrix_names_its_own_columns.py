"""A CAS equity statement's columns are named by their own headings, and no other column's.

湖北能源 000883's statement of changes in equity (page index 117) was read with ten columns named
股本, "其 资本公积 先 续 他 股 债", 股, 收益, 专项储备, 盈余公积, "风险 未分配利润 准备", "小计 他",
益, 合计. Two causes, both in the header band:

* THE HEADINGS ARE STACKED OVER LINES THAT OVERLAP — each centred in its own cell — and the band
  stopped one line out from the anchor, losing 减：库存, 其他综合 and 少数股东权;
* THE COLUMNS THE FILING LEAVES EMPTY — 其他权益工具's 优先股 / 永续债 / 其他, 一般风险准备 and the
  catch-all 其他 — print their headings inside the next band, a character a line.

Geometry from the filing's native text layer. Measured across the 77 matrix pages of the eight
reference filings: every CAS equity statement is now named cleanly, and no page of the two HKEX
filings changes.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, _maybe_matrix

HEADER = [  # (text, x0, x1, y0, y1)
    ("归属于母公司所有者权益", 0.470, 0.562, 0.1677, 0.1803),
    ("项目", 0.145, 0.165, 0.1885, 0.2036), ("其他权益工具", 0.298, 0.348, 0.1883, 0.2008),
    ("少数股东权", 0.809, 0.851, 0.1950, 0.2075), ("所有者权益", 0.869, 0.911, 0.1950, 0.2075),
    ("一般", 0.637, 0.654, 0.1968, 0.2093), ("减：库存", 0.428, 0.462, 0.2052, 0.2177),
    ("其他综合", 0.481, 0.514, 0.2052, 0.2177), ("其", 0.727, 0.736, 0.2052, 0.2177),
    ("优", 0.295, 0.302, 0.2078, 0.2194), ("永", 0.320, 0.328, 0.2078, 0.2194),
    ("益", 0.826, 0.835, 0.2117, 0.2242), ("合计", 0.882, 0.899, 0.2117, 0.2242),
    ("股本", 0.250, 0.267, 0.2131, 0.2256), ("资本公积", 0.372, 0.406, 0.2135, 0.2260),
    ("专项储备", 0.532, 0.565, 0.2135, 0.2260), ("盈余公积", 0.584, 0.618, 0.2135, 0.2260),
    ("风险", 0.637, 0.654, 0.2135, 0.2260), ("未分配利润", 0.670, 0.712, 0.2135, 0.2260),
    ("小计", 0.764, 0.781, 0.2135, 0.2260), ("其", 0.344, 0.352, 0.2163, 0.2279),
    ("股", 0.441, 0.449, 0.2220, 0.2345), ("收益", 0.489, 0.506, 0.2220, 0.2345),
    ("他", 0.727, 0.736, 0.2220, 0.2345), ("先", 0.295, 0.302, 0.2248, 0.2364),
    ("续", 0.320, 0.328, 0.2248, 0.2364), ("准备", 0.637, 0.654, 0.2304, 0.2429),
    ("他", 0.344, 0.352, 0.2330, 0.2446), ("股", 0.295, 0.302, 0.2415, 0.2531),
    ("债", 0.320, 0.328, 0.2415, 0.2531),
]
FIGURES = [(0.236, 0.281, "6,520,949,388.00"), (0.366, 0.413, "10,667,250,632.39"),
           (0.430, 0.466, "82,985,644.62"), (0.479, 0.518, "279,912,494.83"),
           (0.532, 0.568, "59,285,840.09"), (0.580, 0.624, "1,592,609,946.62"),
           (0.668, 0.715, "13,200,189,348.05"), (0.750, 0.796, "32,237,212,005.36"),
           (0.810, 0.854, "6,256,977,971.62"), (0.869, 0.916, "38,494,189,976.98")]
ROWS = ["一、上年期末余额", "二、本年期初余额", "四、本期期末余额"]


def _page() -> list[Word]:
    words = [Word(text=t, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1)) for t, x0, x1, y0, y1 in HEADER]
    for i, caption in enumerate(ROWS):
        y = 0.2607 + 0.0822 * i
        words.append(Word(text=caption, bbox=BBox(x0=0.086, y0=y, x1=0.153, y1=y + 0.0125)))
        words += [Word(text=t, bbox=BBox(x0=x0, y0=y + 0.0003, x1=x1, y1=y + 0.0113))
                  for x0, x1, t in FIGURES]
    return words


def test_each_column_is_named_by_its_own_heading():
    _m, names = _maybe_matrix(_page(), statement="changes_in_equity")
    assert names == ["股本", "资本公积", "减：库存股", "其他综合收益", "专项储备", "盈余公积",
                     "未分配利润", "小计", "少数股东权益", "所有者权益合计"]
