"""A row's caption is the words printed as its caption — an ageing bucket's count, a wrapped tail.

TWO WAYS A CAPTION LOST WORDS THAT WERE PRINTED AS PART OF IT.

AN AGEING BUCKET'S COUNT READ AS A FIGURE. "1 年以内（含1 年）" opens on a bare integer set tight
against its unit; `_scan_row` took the 1 as the row's first value, and — text after a figure being
neither label nor value — dropped the rest of the caption. 澜起科技 688008's 应付账款 ageing table
came out with the table's HEADER for a caption ("项目期末余额期初余额") and a current figure of 1,
its real current balance filed as the prior year's and the prior year's as `col2`.

A CAPTION WRAPPED AROUND ITS FIGURES LOST ITS TAIL TO THE NEXT ROW. A cell whose caption runs to
two lines sets its figures BETWEEN them; the second line was folded forward, so 迈捷 300319's
payables read "深圳市特发信息技术服务有" and "限公司深圳特发东智科技有限公司".
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, _scan_row, build_line_items

H = 0.0107


def _w(text: str, x0: float, x1: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _build(words):
    items, _ = build_line_items(words, page_index=1, document_id="d", source_kind="native",
                                on_face=False)
    return [(li.source_label, {ev.period_label: str(ev.value) for ev in li.values.values()})
            for li in items]


def test_an_ageing_count_is_part_of_the_caption():
    row = [_w("1", 0.107, 0.116, 0.487), _w("年以内（含1", 0.120, 0.222, 0.487),
           _w("年）", 0.226, 0.261, 0.487), _w("211,153,859.60", 0.458, 0.568, 0.487)]
    label, _ref, values = _scan_row(row)
    assert [w.text for w in label] == ["1", "年以内（含1", "年）"]
    assert [w.text for w in values] == ["211,153,859.60"]


def test_a_count_not_tight_against_a_unit_is_still_a_figure():
    row = [_w("应付账款", 0.107, 0.170, 0.5), _w("1", 0.458, 0.466, 0.5), _w("年", 0.60, 0.61, 0.5)]
    assert [w.text for w in _scan_row(row)[2]] == ["1"]


def test_an_ageing_table_reads_its_buckets_and_periods():
    words = [_w("项目", 0.200, 0.235, 0.472), _w("期末余额", 0.422, 0.492, 0.472),
             _w("期初余额", 0.683, 0.753, 0.472),
             _w("1", 0.107, 0.116, 0.487), _w("年以内（含1", 0.120, 0.222, 0.487),
             _w("年）", 0.226, 0.261, 0.487), _w("211,153,859.60", 0.458, 0.568, 0.487),
             _w("131,115,391.99", 0.739, 0.849, 0.487),
             _w("合计", 0.200, 0.235, 0.506), _w("211,153,859.60", 0.458, 0.568, 0.506),
             _w("131,115,391.99", 0.739, 0.849, 0.506)]
    assert _build(words) == [
        ("1 年以内（含1 年）", {"current": "211153859.60", "prior": "131115391.99"}),
        ("合计", {"current": "211153859.60", "prior": "131115391.99"})]


def test_an_ageing_bucket_does_not_take_the_lines_above_it():
    """688008's parent-company receivables: the band's name and its 其中： sub-heading on lines of
    their own above the bucket's row."""
    words = [_w("账龄", 0.248, 0.283, 0.201), _w("期末账面余额", 0.464, 0.570, 0.201),
             _w("期初账面余额", 0.721, 0.826, 0.201),
             _w("1", 0.151, 0.160, 0.218), _w("年以内", 0.164, 0.217, 0.218),
             _w("其中：1", 0.151, 0.213, 0.235), _w("年以内分项", 0.217, 0.305, 0.235),
             _w("1", 0.151, 0.160, 0.252), _w("年以内", 0.164, 0.217, 0.252),
             _w("222,936,138.65", 0.526, 0.636, 0.252)]
    assert [label for label, _ in _build(words)] == ["1 年以内"]


def test_a_caption_wrapped_around_its_figures_keeps_its_tail():
    """300319 page 178's own geometry: the second party's name is split around its figures."""
    words = [_w("宜宾益邦科技有限责任公司", 0.307, 0.488, 0.4642),
             _w("291,181.25", 0.618, 0.693, 0.4642), _w("92,055.70", 0.828, 0.896, 0.4642),
             _w("深圳市特发信息技术服务有", 0.307, 0.488, 0.4790),
             _w("1,525,227.64", 0.603, 0.693, 0.4860), _w("1,385,223.97", 0.805, 0.896, 0.4860),
             _w("限公司", 0.307, 0.352, 0.4930),
             _w("深圳特发东智科技有限公司", 0.307, 0.488, 0.5075),
             _w("238,366.72", 0.821, 0.896, 0.5075)]
    assert [label for label, _ in _build(words)] == [
        "宜宾益邦科技有限责任公司", "深圳市特发信息技术服务有限公司", "深圳特发东智科技有限公司"]


def test_evenly_spaced_lines_are_not_a_wrapped_cell():
    """Prose is left-aligned and evenly spaced too; a figure on a line of its own between two text
    lines a full line apart is not a caption wrapped around it."""
    pitch = 0.017
    words = [_w("Amortisation is provided on the", 0.157, 0.40, 0.30),
             _w("30", 0.60, 0.62, 0.30 + pitch), _w("1,234", 0.80, 0.85, 0.30 + pitch),
             _w("Investment properties", 0.157, 0.30, 0.30 + 2 * pitch),
             _w("5,000", 0.80, 0.85, 0.30 + 2 * pitch)]
    labels = [label for label, _ in _build(words)]
    assert "Investment properties" in labels, labels
