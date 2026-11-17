"""A wrapped caption keeps its own lines, and gives none of them to the caption beneath it.

Three shapes from 四创电子 600990, geometry from the native text layer:

1. A BLANK CAPTION CLOSED BY ITS SIGN NOTE. The cash-flow supplement prints every line of its
   layout, so 公允价值变动损失（收益以“－”号 / 填列） stands blank above 财务费用（收益以“－”号填列）
   63,190,779.45. The two were read as one caption holding the finance costs. The CAS sign note
   （…号填列） ends a caption.
2. A TAIL OPENING THE NEXT PAGE. 经营性应付项目的增加（减少以 holds -242,203,660.48 at the foot of
   page index 165; page 166 opens “－”号填列）, which was folded onto 其他（合同资产）. A line that
   closes a bracket it never opened is never the head of the caption beneath it, and the brackets on
   the two sides of the break complete the caption they split.
3. A THREE-LINE CAPTION IN A NARROW NOTE CELL, its figures on the middle line: 商业 / 承兑 / 汇票.
   The third line was folded forward ("汇票银行承兑", "汇票合计"); the page prints 商业承兑汇票 and
   银行承兑汇票 whole, and that is the evidence it is the row above's.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

H = 0.0126


def _w(text: str, x0: float, x1: float, y: float, h: float = H) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + h))


def _face(words, *, carry=None, spliced=None):
    items, _ = build_line_items(words, page_index=165, document_id="d", source_kind="native",
                                statement="cash_flow", on_face=True, carry_caption=carry,
                                spliced_out=spliced)
    return {li.source_label: [str(v.value) for v in li.values.values()] for li in items}


# ── 1. a blank caption closed by its sign note ───────────────────────────────────────────────

SUPPLEMENT = [
    _w("固定资产报废损失（收益以“－”号", 0.11, 0.38, 0.6748),
    _w("347,162.93", 0.54, 0.62, 0.6817), _w("347,795.52", 0.78, 0.85, 0.6817),
    _w("填列）", 0.11, 0.16, 0.6909),
    _w("公允价值变动损失（收益以“－”号", 0.11, 0.38, 0.7077),
    _w("填列）", 0.11, 0.16, 0.7238),
    _w("63,190,779.45", 0.52, 0.62, 0.7395), _w("70,804,700.47", 0.76, 0.85, 0.7395),
    _w("财务费用（收益以“－”号填列）", 0.11, 0.37, 0.7406),
    _w("-6,592,631.61", 0.53, 0.62, 0.7562), _w("-739,747.47", 0.77, 0.85, 0.7562),
    _w("投资损失（收益以“－”号填列）", 0.11, 0.37, 0.7574),
]


def test_a_blank_caption_closed_by_its_sign_note_is_not_the_next_ones_head():
    got = _face(SUPPLEMENT)
    assert got["财务费用（收益以“－”号填列）"] == ["63190779.45", "70804700.47"]
    assert got["投资损失（收益以“－”号填列）"] == ["-6592631.61", "-739747.47"]
    assert not any(label.startswith("公允价值变动损失") and values
                   for label, values in got.items())
    assert got["固定资产报废损失（收益以“－”号填列）"] == ["347162.93", "347795.52"]


# ── 2. a tail opening the next page ──────────────────────────────────────────────────────────

NEXT_PAGE = [
    _w("“－”号填列）", 0.11, 0.23, 0.0879),
    _w("117,471,107.65", 0.51, 0.62, 0.1040), _w("46,455,134.92", 0.76, 0.85, 0.1040),
    _w("其他（合同资产）", 0.11, 0.25, 0.1046),
    _w("244,695,836.40", 0.51, 0.62, 0.1208), _w("108,234,967.17", 0.75, 0.85, 0.1208),
    _w("经营活动产生的现金流量净额", 0.11, 0.34, 0.1214),
]


def test_a_tail_opening_the_page_completes_the_caption_it_closes():
    spliced: list[str] = []
    got = _face(NEXT_PAGE, carry="经营性应付项目的增加（减少以", spliced=spliced)
    assert spliced == ["经营性应付项目的增加（减少以“－”号填列）"]
    assert got["其他（合同资产）"] == ["117471107.65", "46455134.92"]
    assert got["经营活动产生的现金流量净额"] == ["244695836.40", "108234967.17"]


def test_with_no_caption_to_finish_the_tail_stands_alone():
    """No carried caption, nothing open: the fragment is still nobody's head."""
    got = _face(NEXT_PAGE)
    assert got["其他（合同资产）"] == ["117471107.65", "46455134.92"]


def test_a_complete_caption_on_the_page_before_takes_no_tail():
    """The brackets must balance on both sides of the break: a carried caption with nothing open is
    never extended."""
    spliced: list[str] = []
    _face(NEXT_PAGE, carry="存货的减少（增加以“－”号填列）", spliced=spliced)
    assert spliced == []


# ── 3. a three-line caption in a narrow note cell ────────────────────────────────────────────

H2 = 0.0107
NARROW = [
    _w("商业", 0.059, 0.097, 0.0876, H2),
    _w("75,207,545.19", 0.118, 0.197, 0.1008, 0.0122), _w("95.30", 0.224, 0.254, 0.1008, 0.0122),
    _w("2,907,585.03", 0.275, 0.347, 0.1008, 0.0122),
    _w("承兑", 0.059, 0.097, 0.1014, H2),
    _w("汇票", 0.059, 0.089, 0.1154, H2),
    _w("银行", 0.065, 0.097, 0.1298, H2),
    _w("3,712,873.95", 0.125, 0.197, 0.1430, 0.0122), _w("4.70", 0.230, 0.254, 0.1430, 0.0122),
    _w("承兑", 0.059, 0.097, 0.1436, H2),
    _w("汇票", 0.059, 0.089, 0.1576, H2),
    _w("78,920,419.14", 0.118, 0.197, 0.1713, 0.0123), _w("100.00", 0.216, 0.254, 0.1713, 0.0123),
    _w("2,907,585.03", 0.275, 0.347, 0.1713, 0.0123),
    _w("合计", 0.063, 0.093, 0.1720, H2),
    # the next table on the page prints the two captions whole
    _w("组合计提项目：商业承兑汇票", 0.107, 0.337, 0.2676),
    _w("商业承兑汇票", 0.107, 0.213, 0.3341),
    _w("银行承兑汇票", 0.107, 0.213, 0.3509),
]


def test_the_third_line_of_a_narrow_cell_is_the_row_aboves():
    items, _ = build_line_items(NARROW, page_index=121, document_id="d", source_kind="native",
                                on_face=False)
    labels = [li.source_label for li in items if li.values]
    assert labels[:3] == ["商业承兑汇票", "银行承兑汇票", "合计"]
