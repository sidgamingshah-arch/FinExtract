"""Securities (CP) read off a note split by a page break, a 其中 that ends where it adds up, and
an accounting-policy note that is not a balance.

Measured on the CAS filings' 交易性金融资产 notes against the figures they print:

* 301577 美信 2024 七、2 prints its parent row as the last line of page 158 and 其中：信托计划 /
  结构性存款 at the top of page 159, where the table has no header of its own. 结构性存款's one
  figure stands under 期初余额 but was filed "current" (first figure by order), and the parent was
  read beside its breakdown: Securities (CP) current 65,042,873.39 for a face of 25,020,357.93.
* 诚志股份 2024 七、2 prints two parent rows, each with a 其中：其他; the second parent arrived
  inside the first's 其中 group and the prior year counted 550,468,453.20 twice.
* 688411 海博思创 2024 五、11 (accounting policies) ends a line of prose on the list marker "(3)",
  read as −3 and published as current FVTOCI financial assets, so the current line became 0.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, Provenance
from app.schemas.line_items import load_line_item_set
from app.services import line_item_notes
from app.services.note_sourced import (
    _broken_down,
    _broken_down_across_fragments,
    _compiled,
    resolve,
    select_rows,
)
from app.services.row_reconstruct import (
    HeaderModel,
    PrintedColumn,
    Word,
    _file_by_the_header,
    _Placed,
    build_line_items,
)

SEED = pathlib.Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_line_items.json"
FVTPL = "sub__fa_cp_fvtpl_note_total"
FVTOCI = "sub__fa_cp_fvtoci_note_total"
H = 0.011
ALL = _compiled([r"\S"])


@pytest.fixture(scope="module")
def by_key():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {i.key: i for i in st.items}


def _w(text, x0, x1, y):
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _cells(li):
    return {ev.period_label: str(ev.value) for ev in li.values.values()}


def _row(caption, current=None, prior=None, group="", number="七、2"):
    row = NoteItem(raw_label=caption, note_number=number, group_hint=group)
    for label, amount in (("current", current), ("prior", prior)):
        if amount is not None:
            row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label,
                                         value=Decimal(amount), provenance=Provenance(page_index=1)))
    return row


def _table(title, rows, number="七、2", chapter=""):
    t = NotesTable(note_number=number, title=title, page_index=1, chapter_title=chapter)
    t.items.extend(rows)
    return t


def _sum(item, tables):
    got = resolve(select_rows(item, tables, {"current", "prior"}), "sum")
    return {period: str(amount) for (_b, period), (amount, _t) in got.items()}


# ── a figure placed by order is filed under the column the carried header names ─────────────────

def _meixin_pages(logs):
    """301577's 七、2 in its own geometry: the header and the parent on page 158, the 其中 rows on
    page 159 with the figures printed where page 158's columns were."""
    header: list = []
    first, _ = build_line_items([
        _w("项目", 0.18, 0.21, 0.80), _w("期末余额", 0.53, 0.60, 0.80),
        _w("期初余额", 0.80, 0.87, 0.80),
        _w("以公允价值计量且其变动计入当期损益的金融资产", 0.095, 0.40, 0.89),
        _w("25,020,357.93", 0.518, 0.617, 0.89), _w("15,002,157.53", 0.788, 0.887, 0.89),
    ], page_index=157, document_id="t", source_kind="native", on_face=False,
        header_out=header, log=logs.append)
    second, _ = build_line_items([
        _w("其中：", 0.095, 0.13, 0.09),
        _w("信托计划", 0.095, 0.156, 0.107), _w("25,020,357.93", 0.518, 0.617, 0.107),
        _w("结构性存款", 0.095, 0.171, 0.122), _w("15,002,157.53", 0.788, 0.887, 0.122),
        _w("合计", 0.095, 0.125, 0.158), _w("25,020,357.93", 0.518, 0.617, 0.158),
        _w("15,002,157.53", 0.788, 0.887, 0.158),
    ], page_index=158, document_id="t", source_kind="native", on_face=False,
        header_carry=header[0], log=logs.append)
    return first, second


def test_a_lone_figure_under_the_prior_column_is_the_prior_year():
    logs: list[str] = []
    _first, second = _meixin_pages(logs)
    rows = {li.source_label: li for li in second}
    assert _cells(rows["结构性存款"]) == {"prior": "15002157.53"}
    assert _cells(rows["信托计划"]) == {"current": "25020357.93"}
    assert _cells(rows["合计"]) == {"current": "25020357.93", "prior": "15002157.53"}
    assert any("period_from_header('结构性存款',current->prior)" in line for line in logs)


def _placed(li, ev, x0, x1):
    return _Placed(row=0, col=None, word=_w("1", x0, x1, 0.1), fact=ev)


def _by_order(*figures):
    li = LineItem(source_label="row")
    placed = {}
    for label, x0, x1 in figures:
        ev = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label, value=Decimal(1),
                            provenance=Provenance(page_index=0))
        li.set_value(ev)
        placed[id(ev)] = _placed(li, ev, x0, x1)
    return li, placed


def test_a_header_with_two_columns_per_year_leaves_the_order_alone():
    """账面余额 and 坏账准备 under each year name a measure too; a bare "prior" would lose it."""
    header = HeaderModel(columns=[
        PrintedColumn("期末余额 · 账面余额", 0.30, 0.40, period="current"),
        PrintedColumn("期末余额 · 坏账准备", 0.45, 0.55, period="current"),
        PrintedColumn("期初余额 · 账面余额", 0.60, 0.70, period="prior"),
        PrintedColumn("期初余额 · 坏账准备", 0.75, 0.85, period="prior"),
    ], groups=[])
    li, placed = _by_order(("current", 0.62, 0.68))
    _file_by_the_header([li], placed, header)
    assert {ev.period_label for ev in li.values.values()} == {"current"}


def test_figures_that_would_share_a_slot_keep_their_order():
    header = HeaderModel(columns=[
        PrintedColumn("期末余额", 0.50, 0.60, period="current"),
        PrintedColumn("期初余额", 0.78, 0.88, period="prior"),
    ], groups=[])
    li, placed = _by_order(("current", 0.80, 0.86), ("prior", 0.82, 0.88))
    logs: list[str] = []
    _file_by_the_header([li], placed, header, log=logs.append)
    assert sorted(ev.period_label for ev in li.values.values()) == ["current", "prior"]
    assert any("slot_shared" in line for line in logs)


# ── a 其中 breakdown printed across the page break ──────────────────────────────────────────────

def test_the_parent_above_the_break_is_read_through_its_breakdown(by_key):
    """301577: 25,020,357.93 / 15,002,157.53, the face, where it published 65,042,873.39."""
    page_158 = _table("、交易性金融资产", [
        _row("以公允价值计量且其变动计入当期损益的金融资产", "25020357.93", "15002157.53")])
    page_159 = _table("、交易性金融资产", [
        _row("信托计划", "25020357.93", group="其中："),
        _row("结构性存款", None, "15002157.53", group="其中："),
        _row("合计", "25020357.93", "15002157.53", group="其中："),
    ])
    assert _sum(by_key[FVTPL], [page_158, page_159]) == {
        "current": "25020357.93", "prior": "15002157.53"}


def test_a_breakdown_after_the_break_that_does_not_add_up_is_not_taken_for_one():
    above = _table("、交易性金融资产", [_row("债务工具投资", "100", "80")])
    below = _table("、交易性金融资产", [_row("结构性存款", "30", None, group="其中：")])
    assert _broken_down_across_fragments([above, below], ALL) == {}


def test_the_next_note_is_not_a_continuation():
    above = _table("、交易性金融资产", [_row("债务工具投资", "100")])
    below = _table("、应收票据", [_row("银行承兑票据", "100", group="其中：")], number="七、3")
    assert _broken_down_across_fragments([above, below], ALL) == {}


# ── a 其中 group ends where it adds up to its row ───────────────────────────────────────────────

def test_chengzhis_second_parent_is_a_parent_not_a_breakdown_row(by_key):
    """诚志股份 七、2: prior 750,491,730.21 (200,023,277.01 + 550,468,453.20), not 1,300,960,183.41."""
    rows = [
        _row("以公允价值计量且其变动计入当期损益的金融资产", "1372158938.52", "200023277.01"),
        _row("其他", "1372158938.52", "200023277.01", group="其中："),
        _row("指定以公允价值计量且其变动计入当期损益的金融资产", None, "550468453.20",
             group="其中："),
        _row("其他", None, "550468453.20", group="其中："),
        _row("合计", "1372158938.52", "750491730.21", group="其中："),
    ]
    assert _broken_down(rows, ALL) == {0, 2}
    assert _sum(by_key[FVTPL], [_table("、交易性金融资产", rows)]) == {
        "current": "1372158938.52", "prior": "750491730.21"}


def test_a_breakdown_that_lists_only_some_components_keeps_every_row_after_it():
    rows = [
        _row("权益工具投资", "100"),
        _row("上市公司股票", "40", group="其中："),
        _row("非上市股权", "20", group="其中："),
    ]
    assert _broken_down(rows, ALL) == {0}


# ── an accounting-policy note is not a balance ──────────────────────────────────────────────────

def test_a_policy_chapter_is_not_read_for_a_balance(by_key):
    """688411 五、11: the "(3)" that ends a line of the derecognition policy is not −3 of FVTOCI."""
    policy = _table("金融资产转移的确认依据及计量方法", [
        _row("利终止；(2)该金融资产已转移，且将金融资产所有权上几乎所有的风险和报酬转移给转入方；",
             "-3", number="五、11")], number="五、11", chapter="重要会计政策及会计估计")
    heading = _table("以公允价值计量且其变动计入其他综合收益的金融资产", [], number="五、11",
                     chapter="重要会计政策及会计估计")
    assert _sum(by_key[FVTOCI], [heading, policy]) == {}


def test_the_hkfrs_policies_note_is_a_policy_note():
    assert line_item_notes.is_policy_note([_table("MATERIAL ACCOUNTING POLICIES 重大會計政策", [],
                                                  number="3")])
    assert line_item_notes.is_policy_note([_table("3. Summary of significant accounting policies",
                                                  [], number="3")])


def test_a_balance_note_is_not_a_policy_note():
    assert not line_item_notes.is_policy_note([_table("、交易性金融资产", [],
                                                      chapter="合并财务报表项目注释")])
    assert not line_item_notes.is_policy_note([_table(
        "FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR LOSS", [], number="26")])
