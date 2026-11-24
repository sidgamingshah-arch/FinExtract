"""A 其中 GROUP THAT DOES NOT ADD UP INSIDE ITS ROW IS NOT A BREAKDOWN TO THE CHECKS ON A MODEL'S
ANSWER (`services.answer_checks.NoteIndex.parents`).

The checks refuse a 其中 row a sum adds beside the row it breaks down, and they read "breaks down"
off the rules' own reading (`note_sourced.breakdown_groups`). The rules use that reading only
where the 其中 rows are what the line itself counts (`note_sourced._broken_down`); the checks used
it for every table. And the row reader carries a 其中 heading down the table onto rows of the
table's own level, so a SIBLING row arrives inside the group. Measured on the rules' notes of
300319 十八、2 (按坏账计提方法分类披露): 按组合计提坏账准备, 合并关联方组合 and 合计 all carry the
heading 其中：, under 按单项计提坏账准备 — and a sum adding the individually and the collectively
assessed allowances (4,416,026.45 and 2,669,937.52) had the second refused as "a 其中 row of the
first". The same misreading shows on 000883 七、4 and on 301577 七、35, where 境外, 境内 and 合计
were read as 其中 rows of 业务类型. This ran on every document context, the shipped one included.

A group is a breakdown to the checks only where it can be one: none of its rows is a total, and
in no column does it add up to more than the row it breaks down. The rules' own reading is
unchanged (`breakdown_groups` is not touched), so the rules route publishes what it did.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.services import answer_checks, note_sourced
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources


def _row(caption, figures, y, *, group="", number="十八、2", page=199):
    row = NoteItem(raw_label=caption, note_number=number, group_hint=group)
    for n, (period, amount) in enumerate(figures.items()):
        row.set_value(ExtractedValue(
            value=Decimal(amount), basis=Basis.STANDALONE, period_label=period,
            provenance=Provenance(source_kind="native", page_index=page,
                                  bbox=BBox(x0=0.45 + 0.2 * n, y0=y, x1=0.55 + 0.2 * n,
                                            y1=y + 0.012))))
    return row


def _bad_debt():
    """300319 十八、2's allowance columns, as the rules read them: the heading 其中： carried down
    onto the second category, a sub-category and the total."""
    return NotesTable(note_number="十八、2", title="按坏账计提方法分类披露", items=[
        _row("按单项计提坏账准备", {"current": "4416026.45", "prior": "4416026.45"}, 0.30),
        _row("按组合计提坏账准备", {"current": "10580181.90", "prior": "2669937.52"}, 0.32,
             group="其中："),
        _row("合并关联方组合", {"current": "1334637.72"}, 0.34, group="其中："),
        _row("合计", {"current": "14996208.35", "prior": "7085963.97"}, 0.36, group="其中：")])


def _revenue():
    """301577 七、35's shape: the heading row 业务类型 and, carried down, 境外, 境内 and 合计."""
    return NotesTable(note_number="七、35", title="营业收入和营业成本", items=[
        _row("业务类型", {"current": "422239332.94"}, 0.30, number="七、35"),
        _row("磁性元器件", {"current": "127667088.80"}, 0.32, group="其中：", number="七、35"),
        _row("境外", {"current": "120154161.33"}, 0.34, group="其中：", number="七、35"),
        _row("境内", {"current": "302085171.61"}, 0.36, group="其中：", number="七、35"),
        _row("合计", {"current": "422239332.94"}, 0.38, group="其中：", number="七、35")])


def _fvtpl():
    """300319 七、2: a real breakdown — the 其中 rows add up to the row in each year."""
    return NotesTable(note_number="七、2", title="、交易性金融资产", items=[
        _row("以公允价值计量且其变动计入当期损益的金融资产",
             {"current": "431478888.81", "prior": "181124711.32"}, 0.30, number="七、2"),
        _row("结构性理财产品", {"current": "373011600.33", "prior": "130559509.03"}, 0.32,
             group="其中：", number="七、2"),
        _row("股权投资", {"current": "58467288.48", "prior": "50000000.00"}, 0.34,
             group="其中：", number="七、2"),
        _row("远期结售汇", {"prior": "565202.29"}, 0.36, group="其中：", number="七、2")])


def test_the_rules_reading_of_the_shape_is_unchanged():
    assert note_sourced.breakdown_groups(_bad_debt().items) == {0: [1, 2, 3]}
    assert note_sourced.breakdown_groups(_revenue().items) == {0: [1, 2, 3, 4]}


def test_a_group_with_a_total_row_or_adding_up_past_its_row_is_no_breakdown():
    for table in (_bad_debt(), _revenue()):
        assert answer_checks.NoteIndex([table]).parents == {}


def test_a_group_that_adds_up_inside_its_row_is_a_breakdown():
    table = _fvtpl()
    assert answer_checks.NoteIndex([table]).parents == {
        (id(table), 1): (id(table), 0), (id(table), 2): (id(table), 0),
        (id(table), 3): (id(table), 0)}


def test_the_two_allowance_categories_added_together_are_both_kept():
    table = _bad_debt()
    resolved, unresolved = resolve_sources(
        [SourceRef(note="十八、2", caption="按单项计提坏账准备", column="prior"),
         SourceRef(note="十八、2", caption="按组合计提坏账准备", column="prior")], [table])
    assert unresolved == []
    kept, refused = answer_checks.refuse_breakdowns_of_cited_totals(
        resolved, answer_checks.NoteIndex([table]), signs=[1, 1], component=True)
    assert [e["figures"] for e in kept] == [{"prior": "4416026.45"}, {"prior": "2669937.52"}]
    assert refused == []


def test_a_qizhong_row_added_to_the_row_it_breaks_down_is_still_refused():
    table = _fvtpl()
    resolved, _ = resolve_sources(
        [SourceRef(note="七、2", caption="以公允价值计量且其变动计入当期损益的金融资产",
                   column="current"),
         SourceRef(note="七、2", caption="结构性理财产品", column="current")], [table])
    kept, refused = answer_checks.refuse_breakdowns_of_cited_totals(
        resolved, answer_checks.NoteIndex([table]), signs=[1, 1], component=True)
    assert [e["caption"] for e in kept] == ["以公允价值计量且其变动计入当期损益的金融资产"]
    assert [r["refused"] for r in refused] == ["breakdown_of_cited_total"]
