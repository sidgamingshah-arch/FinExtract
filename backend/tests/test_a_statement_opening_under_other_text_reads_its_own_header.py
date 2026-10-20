"""A STATEMENT THAT OPENS HALFWAY DOWN A PAGE READS ITS OWN COLUMN HEADER, AND NOTHING ABOVE IT.

000883's consolidated balance sheet opens two rows from the foot of the page its audit report ends
on. Three things printed on that page were read as part of the statement's header or columns:

* the report's signing date 二〇二五年四月二十三日, as the period over BOTH columns;
* the report's sentence "…对本期财务报表审计最为重要…", as a one-period caption that declared no
  columns, so the page's two figure rows showed none;
* the bare folio "107", as a figure standing under no column — which vetoes the header's reading.

So 交易性金融资产, printed under 期初余额 only, was filed as the current year's. And the page's
lower part — 三、公司基本情况 to 五、重要会计政策及会计估计 on the page where the company's equity
statement ends — was read as the face, so the policies that follow arrived under no chapter.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services import row_reconstruct as rr
from app.services.notes_extract import notes_below_the_face
from app.services.row_reconstruct import Word

H = 0.0107


def _w(text: str, x0: float, x1: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


PAGE_106 = [
    _w("从与治理层沟通过的事项中，我们确定哪些事项对本期财务报表审计最为重要", 0.133, 0.889, 0.330),
    _w("二〇二五年四月二十三日", 0.674, 0.841, 0.557),
    _w("1、合并资产负债表", 0.095, 0.246, 0.694),
    _w("项目", 0.206, 0.236, 0.773), _w("期末余额", 0.461, 0.521, 0.773),
    _w("期初余额", 0.731, 0.791, 0.773),
    _w("流动资产：", 0.095, 0.171, 0.792),
    _w("货币资金", 0.110, 0.171, 0.812), _w("2,268,835,469.85", 0.496, 0.617, 0.812),
    _w("1,624,897,762.96", 0.766, 0.887, 0.812),
    _w("交易性金融资产", 0.110, 0.216, 0.870), _w("67,164,039.35", 0.788, 0.887, 0.870),
    _w("107", 0.882, 0.905, 0.929),
]


def _rows(words):
    return rr._group_rows(words, rr.row_tolerance(words, "native"), fold=None)


def _figure_rows(rows):
    out = []
    for row in rows:
        _l, _n, values = rr._scan_row(row, None, extract_note_refs=True)
        figures = [w for w in values if rr._num(w.text, None) is not None]
        if figures and not rr._is_folio_row(row, None):
            out.append(figures)
    return out


def test_a_date_above_the_worded_header_is_not_a_column_period():
    assert rr._period_bands(_rows(PAGE_106), None) == []


def test_the_worded_header_declares_the_columns():
    rows = _rows(PAGE_106)
    bands = rr._header_declared_bands(rows, _figure_rows(rows), None)
    assert len(bands) == 2
    lone = 0.5 * (0.788 + 0.887)
    assert min(range(2), key=lambda i: abs(bands[i] - lone)) == 1, "the lone figure is 期初余额's"


def test_a_bare_folio_at_the_foot_is_not_a_figure():
    assert rr._is_folio_row([_w("107", 0.882, 0.905, 0.929)])
    assert not rr._is_folio_row([_w("107", 0.882, 0.905, 0.500)]), "not at the foot"
    assert not rr._is_folio_row([_w("合计", 0.110, 0.150, 0.929), _w("107", 0.882, 0.905, 0.929)])


def test_the_notes_begin_below_the_last_figure_of_the_face():
    words = [
        _w("四、本期期末余额", 0.095, 0.250, 0.100), _w("6,500,915,424.00", 0.700, 0.880, 0.100),
        _w("三、", 0.095, 0.125, 0.300), _w("公司基本情况", 0.130, 0.250, 0.300),
        _w("湖北能源集团股份有限公司（原名为湖北三环股份有限公司，以下简称“本公司”）", 0.130, 0.700, 0.320),
        _w("五、", 0.095, 0.125, 0.500), _w("重要会计政策及会计估计", 0.130, 0.330, 0.500),
    ]
    assert notes_below_the_face(words, "native", None, 0) == 0.300


def test_a_banner_over_blank_lines_below_the_last_figure_is_not_a_chapter():
    """300319's company income statement ends "六、综合收益总额 …" then "七、每股收益：" over two
    EPS lines it leaves blank, then the folio — read as chapter 七, it put the real notes out of
    reach."""
    words = [
        _w("六、综合收益总额", 0.095, 0.250, 0.847), _w("249,132,958.97", 0.500, 0.617, 0.847),
        _w("179,288,525.59", 0.766, 0.887, 0.847),
        _w("七、", 0.095, 0.125, 0.863), _w("每股收益：", 0.130, 0.230, 0.863),
        _w("（一）基本每股收益", 0.110, 0.300, 0.879), _w("（二）稀释每股收益", 0.110, 0.300, 0.894),
        _w("105", 0.882, 0.905, 0.929),
    ]
    assert notes_below_the_face(words, "native", None, 0) is None


def test_a_statements_own_numbered_lines_do_not_open_the_notes():
    words = [
        _w("一、", 0.095, 0.125, 0.100), _w("经营活动产生的现金流量：", 0.130, 0.330, 0.100),
        _w("销售商品、提供劳务收到的现金", 0.110, 0.330, 0.130),
        _w("48,852,686,800.55", 0.500, 0.617, 0.130), _w("48,156,930,039.32", 0.766, 0.887, 0.130),
    ]
    assert notes_below_the_face(words, "native", None, 0) is None
