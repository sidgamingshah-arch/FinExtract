"""A mainland caption whose remainder is printed BELOW its figures keeps its whole name.

THE SHAPE. A CSRC income statement sets its figures beside a caption's FIRST line and prints the
remainder underneath them, because the caption column is narrow and the OCI block's captions are
long. Raw geometry, 300319 page index 103:

    归属母公司所有者的其他综合收益        -10,758,236.29   -305,285.33
    的税后净额
    （一）不能重分类进损益的其他           -10,825,600.00   -284,303.92
    综合收益
    1.重新计量设定受益计划变动
    额

``_merge_wrapped_labels`` folds a label-only line FORWARD into the valued row beneath it unless
``_looks_like_wrapped_tail`` recognises it, which it does by a leading connective, a parenthetical
alternative, or a bracket closed that was never opened. 的税后净额 / 综合收益 / 变动 / 额 have none
of those, so each folded forward onto the NEXT caption and the middle caption's own tail went with
it. The mapper was handed 的税后净额（一）不能重分类进损益的其他 and
合收益的金额4.其他债权投资信用减值准备5.现金流量套期储备6.外币财务报表折算差额 — four captions in one
row — while 7.其他归属于少数股东的其他综合收益的税后净额七、综合收益总额 held 330,011,283.77.

WHAT IT COST, measured across the corpus: not one of the template's seven ``is_oci__*`` columns
received a figure on 688008, and two slots out of 28 on each of 000709 and 300319. Recovering the
captions also recovered 688008's ``cf_investing__purchase_financial_assets`` (5,160,520,367.64)
and ``cf_investing__purchase_ppe`` (379,121,899.19), which the same glue had been feeding into the
investing section's catch-all.

THE RULE. A fragment is the rest of the caption above it when the TWO TOGETHER are a caption the
vocabulary knows and the fragment alone is not — see ``_completes_the_caption_above``. Not
geometry: the tails ARE outdented relative to their heads, and so is almost everything else (240
of the PRC filings' label-only lines start left of the line above, and most are complete captions
— 合同资产 under 其中：数据资源, （二）按所有权归属分类 under 2.终止经营净利润), so an indent rule
glues those instead.

The vocabulary had to be completed for the rule to be able to fire at all: the curated CAS face
caption list stopped at the P&L spine and held none of the net-profit or OCI sub-block.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models.geometry import BBox
from app.services.mapping import normalize_label
from app.services.row_reconstruct import (
    Word,
    _CAS_FACE_CAPTIONS,
    _completes_the_caption_above,
    _is_known_caption,
    _looks_like_wrapped_tail,
    build_line_items,
)

LINE_H = 0.011
STEP = 0.015
GLYPH = 0.016


def _line(y: float, text: str, *, x0: float = 0.10, value: str | None = None,
          value2: str | None = None) -> list[Word]:
    """One printed line. CJK has no spaces, so the whole caption is one Word."""
    out = [Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x0 + GLYPH * len(text), y1=y + LINE_H))]
    if value:
        out.append(Word(text=value, bbox=BBox(x0=0.66, y0=y, x1=0.74, y1=y + LINE_H)))
    if value2:
        out.append(Word(text=value2, bbox=BBox(x0=0.84, y0=y, x1=0.92, y1=y + LINE_H)))
    return out


def _captions(*lines: list[Word]) -> dict[str, list[str]]:
    words = [w for line in lines for w in line]
    items, _ = build_line_items(words, page_index=0, document_id="d1", source_kind="native",
                                statement="profit_and_loss")
    return {li.source_label: [str(v.value) for v in li.values.values()] for li in items}


# ── the vocabulary the rule runs on ───────────────────────────────────────────────────────────

# Every caption below is printed on the face of BOTH filings the list is grounded in (澜起科技
# 688008 and 河钢股份 000709), measured the way the rest of the list was.
_OCI_BLOCK = (
    "按经营持续性分类", "持续经营净利润", "终止经营净利润", "按所有权归属分类",
    "归属于母公司股东的净利润", "少数股东损益",
    "归属母公司所有者的其他综合收益的税后净额", "归属于少数股东的其他综合收益的税后净额",
    "不能重分类进损益的其他综合收益", "将重分类进损益的其他综合收益",
    "重新计量设定受益计划变动额", "权益法下不能转损益的其他综合收益",
    "其他权益工具投资公允价值变动", "企业自身信用风险公允价值变动",
    "权益法下可转损益的其他综合收益", "其他债权投资公允价值变动",
    "金融资产重分类计入其他综合收益的金额", "其他债权投资信用减值准备",
    "现金流量套期储备", "外币财务报表折算差额",
    "归属于母公司所有者的综合收益总额", "归属于少数股东的综合收益总额",
)


@pytest.mark.parametrize("caption", _OCI_BLOCK)
def test_the_cas_caption_list_holds_the_net_profit_and_oci_block(caption):
    """Without these the rule cannot fire — a fragment's join is recognisable only if the whole
    caption is in the vocabulary — AND each one was itself being eaten by the forward-fold, which
    is why all seven `is_oci__*` columns were empty on 688008."""
    assert normalize_label(caption) in _CAS_FACE_CAPTIONS


@pytest.mark.parametrize("fragment", ["项目", "小计", "准备", "列）", "单位：元", "额", "变动",
                                      "综合收益", "的税后净额"])
def test_a_fragment_is_not_admitted_to_the_caption_list(fragment):
    """The list's own rule: "Header words and fragments the same scan turned up — 项目, 小计, 准备,
    列）, 单位：元 — are deliberately absent: each is a piece of a caption or a column header, and
    admitting one would stop a legitimate wrap from merging." The block added above must not have
    smuggled one in, and the tails the new rule reads must stay unrecognised or it cannot tell
    them from a line of their own."""
    assert normalize_label(fragment) not in _CAS_FACE_CAPTIONS


# ── the rule ──────────────────────────────────────────────────────────────────────────────────

def _w(text: str, y: float = 0.1) -> list[Word]:
    return [Word(text=text, bbox=BBox(x0=0.1, y0=y, x1=0.1 + GLYPH * len(text), y1=y + LINE_H))]


@pytest.fixture(scope="module")
def steps():
    """The SHIPPED normalisation pipeline. It is what strips a CAS enumeration — 1. / （一） — so
    the join of an enumerated head and its tail normalises onto the bare caption the list holds.
    Without it the rule can only see the two unenumerated shapes, which is half the block."""
    from app.services.row_reconstruct import _pipeline_steps
    from app.schemas.line_items import load_line_item_set

    seed = json.loads((Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
                       / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    return _pipeline_steps(load_line_item_set(seed, resolve=True).normalisation)


@pytest.mark.parametrize("head,tail", [
    ("归属母公司所有者的其他综合收益", "的税后净额"),
    ("（一）不能重分类进损益的其他", "综合收益"),
    ("1.重新计量设定受益计划变动", "额"),
    ("2.权益法下不能转损益的其他", "综合收益"),
    ("3.其他权益工具投资公允价值", "变动"),
    ("（二）将重分类进损益的其他综", "合收益"),
])
def test_a_fragment_the_caption_above_completes_is_a_tail(steps, head, tail):
    """The six the corpus prints. Each head is an incomplete caption and each tail is a bare
    fragment; joined, every one is a caption the vocabulary knows."""
    assert _completes_the_caption_above(_w(head), _w(tail), steps, frozenset())
    # …and neither half on its own is, which is what makes the join the evidence.
    assert not _looks_like_wrapped_tail(tail)
    assert not _is_known_caption(_w(tail), steps, frozenset())


@pytest.mark.parametrize("above,below", [
    # A COMPLETE CAPTION is a line of its own, however incomplete the line above it looks.
    ("其中：数据资源", "合同资产"),
    ("2.终止经营净利润", "存货"),
    ("归属母公司所有者的其他综合收益", "应收账款"),
    # …and a fragment the caption above does NOT complete stays where it was.
    ("其他应收款", "的税后净额"),
    ("存货", "综合收益"),
])
def test_a_fragment_the_caption_above_does_not_complete_is_left_alone(steps, above, below):
    assert not _completes_the_caption_above(_w(above), _w(below), steps, frozenset())


def test_the_join_is_tested_as_one_string_not_sub_line_by_sub_line():
    """THE TRAP THIS RULE FELL INTO FIRST, and the reason it does not call `_is_known_caption`.

    That helper tests the whole line AND each of its printed sub-lines — a bilingual caption comes
    back interleaved — so it answers True for `prev + fragment` whenever PREV ALONE is a known
    caption, which is nearly every valued row on a CAS face. Written that way the rule fired on
    every label-only line in the corpus: 000709 lost every section total it had and
    `bs_cl__other_current_liabilities` went from 6,249,186,163.04 to 275,352,930,469.57.
    """
    # Two PRINTED lines, which is what makes `_label_sub_lines` split them.
    head, fragment = _w("存货", y=0.100), _w("XX不是任何标题的一部分", y=0.100 + STEP)
    # `_is_known_caption` says yes to the pair, because 存货 alone is one of its sub-lines…
    assert _is_known_caption(_w("存货"), (), frozenset())
    assert _is_known_caption(head + fragment, (), frozenset())
    # …and the rule says no, because the JOIN is not a caption anyone recognises.
    assert not _completes_the_caption_above(head, fragment, (), frozenset())


# ── end to end ────────────────────────────────────────────────────────────────────────────────

def test_the_oci_block_is_read_as_the_captions_it_prints():
    """The 300319 page-103 shape, drawn to scale. Before the fix the first row's caption was
    的税后净额（一）不能重分类进损益的其他 and 综合收益 was gone."""
    got = _captions(
        _line(0.100, "归属母公司所有者的其他综合收益", value="-10,758,236.29", value2="-305,285.33"),
        _line(0.100 + STEP, "的税后净额"),
        _line(0.100 + 2 * STEP, "（一）不能重分类进损益的其他", x0=0.13,
              value="-10,825,600.00", value2="-284,303.92"),
        _line(0.100 + 3 * STEP, "综合收益"),
    )
    assert "归属母公司所有者的其他综合收益的税后净额" in got, list(got)
    assert got["归属母公司所有者的其他综合收益的税后净额"] == ["-10758236.29", "-305285.33"]
    assert "（一）不能重分类进损益的其他综合收益" in got, list(got)
    assert got["（一）不能重分类进损益的其他综合收益"] == ["-10825600.00", "-284303.92"]


def test_a_caption_whose_figures_come_after_it_still_folds_forward():
    """The counterweight. The OTHER shape — figures beside the caption's LAST line — is what the
    forward-fold exists for, and the new backward route must not pre-empt it: it is asked only
    where the row above already HAS its figures, and only with nothing pending."""
    got = _captions(
        _line(0.100, "应收账款", value="1,000", value2="2,000"),
        _line(0.100 + STEP, "一年内到期的非流动", x0=0.13),
        _line(0.100 + 2 * STEP, "资产", x0=0.13, value="3,000", value2="4,000"),
    )
    assert "一年内到期的非流动资产" in got, list(got)
    assert got["一年内到期的非流动资产"] == ["3000", "4000"]
    assert got["应收账款"] == ["1000", "2000"]


def test_an_empty_template_line_is_welded_to_neither_neighbour():
    """A CSRC face prints every line of the standard layout whether the filer used it or not, so a
    complete caption stands there with no figures at all — spaced exactly like a continuation.
    `known`/`_CAS_FACE_CAPTIONS` is what tells the two apart, and completing that list must not
    have changed it: 专项储备 must be glued to neither the row above nor the row below.

    It is not emitted as a row of its own — a valueless row never is, before this change or after
    — so what this asserts is that BOTH neighbours keep their own caption and their own figures.
    """
    got = _captions(
        _line(0.100, "其他综合收益", value="-10,693,000.00", value2="132,600.00"),
        _line(0.100 + STEP, "专项储备"),
        _line(0.100 + 2 * STEP, "盈余公积", value="110,889,427.15", value2="84,893,571.25"),
    )
    assert sorted(got) == ["其他综合收益", "盈余公积"], list(got)
    assert got["其他综合收益"] == ["-10693000.00", "132600.00"]
    assert got["盈余公积"] == ["110889427.15", "84893571.25"]
