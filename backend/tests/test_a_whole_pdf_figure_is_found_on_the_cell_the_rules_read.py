"""HOW A WHOLE-PDF FIGURE IS FOUND AGAIN AMONG THE CELLS THE RULES READ ON ITS PAGE
(`services.whole_pdf_locate.RulesReading`, `Place`, `Judge`).

The checks a model answer is held to need to know WHAT was cited — which note, which row, which
column, which unit — and the whole-PDF reply says only a page, a caption and an amount. So each
figure is looked up among the cells the extraction read on that page. These pin the lookup rules on
small hand-built pages, where every answer is known:

  * the box the figure was placed at decides first; a cell whose caption is the one the model
    named beats a boxed total that only repeats the same amount;
  * an identical citation twice in one sum takes the next printed row of that amount (the
    line-item route's tie-break, `answer_checks.take_tied_row`), and where the page prints only one
    such row the second citation is refused as the same figure twice;
  * a year is "certain" only when every cell printing the amount on the page is in one period
    column, under a heading that names a period.

No model is called; the readings are built as `stages.whole_pdf._readings` builds them.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W
from app.services import whole_pdf_locate as L
from app.stages import whole_pdf as stage

REPO = pathlib.Path(__file__).resolve().parents[2]
SEED = REPO / "backend" / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"
GUARANTEES = "sub__cl_contingency_note_exposures"
FVTPL = "sub__fa_cp_fvtpl_note_total"
PAGE = 40


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _cell(amount: str, period: str, x: float, y: float, heading: str = "") -> ExtractedValue:
    box = BBox(x0=x, y0=y, x1=x + 0.1, y1=y + 0.015)
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period, column_period=period,
                          column_heading=heading or None, value=Decimal(amount),
                          value_raw=Decimal(amount),
                          provenance=Provenance(page_index=PAGE, bbox=box, value_bbox=box))


def _table(number: str, title: str, rows) -> NotesTable:
    items = []
    for i, (caption, cells) in enumerate(rows):
        item = NoteItem(raw_label=caption)
        for j, (amount, period, heading) in enumerate(cells):
            item.values[f"{period}{j}"] = _cell(amount, period, 0.5 + 0.2 * j, 0.2 + 0.03 * i,
                                                heading)
        items.append(item)
    return NotesTable(note_number=number, title=title, items=items, source_pages=[PAGE])


def _doc(tables) -> DocumentModel:
    doc = DocumentModel(filename="x.pdf")
    doc.pages = [PageSource(index=PAGE, kind=PageKind.NOTES)]
    doc.notes = list(tables)
    return doc


def _reading(key: str, amount: str, rows=(), period: str = "current", caption: str = ""):
    return W.Reading(key=key, basis="consolidated", period=period, amount=Decimal(amount),
                     caption=caption, period_end="", part=0,
                     check=W.Check("parts_on_page" if rows else "on_page", PAGE), on_face=False,
                     rows=[(c, Decimal(a)) for c, a in rows],
                     row_boxes=[(None, None)] * len(rows))


class _Ctx:
    raw_bytes = b""

    def log(self, _line):
        pass


def _judge(doc, shipped):
    return L.Judge(doc, _Ctx(), shipped, {}, log=lambda _line: None)


def test_the_flags_are_the_stages_own():
    """One spelling of each: the route's flag, and the review flag every writer imports from
    `services.review_state` (the checks here write none of their own — `Verdict.review` goes onto
    the slot through the stage)."""
    from app.services import review_state, whole_pdf_rules
    from app.stages import note_sourced

    assert L.FLAG == stage.FLAG == review_state.MODEL_FLAG
    assert (stage.REVIEW_FLAG == whole_pdf_rules.REVIEW_FLAG == note_sourced.REVIEW_FLAG
            == review_state.REVIEW_FLAG)
    assert not hasattr(L, "REVIEW_FLAG")


def test_an_identical_citation_takes_the_next_printed_row_of_that_amount(shipped):
    """Two guarantees of 6,220,000 under one wrapped caption are two printed rows: a sum citing the
    caption twice takes both, and nothing is refused (the 诚志 十四、4 shape)."""
    doc = _doc([_table("十四、4", "关联担保情况", [
        ("技有限公司南京诚志永清能源科", [("6220000.00", "current", "担保金额")]),
        ("技有限公司南京诚志永清能源科", [("6220000.00", "current", "担保金额")]),
        ("合计", [("12440000.00", "current", "担保金额")])])])
    judge = _judge(doc, shipped)
    r = _reading(GUARANTEES, "12440000.00", rows=[("技有限公司南京诚志永清能源科", "6220000.00"),
                                                  ("技有限公司南京诚志永清能源科", "6220000.00")])
    got = judge.judge_one(r)
    v = judge.verdicts[id(got)]
    assert v.value == Decimal("12440000.00") and not v.dropped
    cells = {tuple(e["cells"][0]) for e in v.kept}
    assert len(cells) == 2


def test_the_same_printed_row_cited_twice_in_a_sum_is_counted_once(shipped):
    doc = _doc([_table("十四、4", "关联担保情况", [
        ("南京诚志永清能源科技有限公司", [("6220000.00", "current", "担保金额")])])])
    judge = _judge(doc, shipped)
    r = _reading(GUARANTEES, "12440000.00", rows=[("南京诚志永清能源科技有限公司", "6220000.00"),
                                                  ("南京诚志永清能源科技有限公司", "6220000.00")])
    got = judge.judge_one(r)
    v = judge.verdicts[id(got)]
    assert v.value == Decimal("6220000.00")
    assert [d["refused"] for d in v.dropped] == ["same_figure_twice"]


def test_the_named_row_beats_a_total_that_repeats_its_amount():
    """300319 七、2 prints its parent row and its 合计 as one amount; a box that fell on the 合计
    does not decide against the caption the model gave."""
    doc = _doc([_table("七、2", "交易性金融资产", [
        ("以公允价值计量且其变动计入当期损益的金融资产", [("431478888.81", "current", "期末余额")]),
        ("结构性理财产品", [("373011600.33", "current", "期末余额")]),
        ("合计", [("431478888.81", "current", "期末余额")])])])
    reading = L.RulesReading(doc)
    total_box = doc.notes[0].items[2].values["current0"].provenance.value_bbox
    hit, found = reading.find(PAGE, Decimal("431478888.81"),
                              "以公允价值计量且其变动计入当期损益的金融资产", total_box, "current",
                              {}, ("k",))
    assert len(found) == 2
    assert hit.caption.startswith("以公允价值计量")
    hit, _found = reading.find(PAGE, Decimal("431478888.81"), "合计", total_box, "current", {},
                               ("k2",))
    assert hit.caption == "合计"


def test_a_year_is_certain_only_when_every_cell_printing_it_agrees(shipped):
    agree = _doc([_table("七、2", "交易性金融资产", [
        ("交易性金融资产", [("1783494750.68", "current", "期末余额"),
                     ("1703266947.76", "prior", "期初余额")]),
        ("合计", [("1783494750.68", "current", "期末余额"),
                ("1703266947.76", "prior", "期初余额")])])])
    judge = _judge(agree, shipped)
    r = judge.judge_one(_reading(FVTPL, "1703266947.76", caption="合计"))
    assert r.period == "prior"
    assert "whole_pdf:refiled:current->prior" in judge.verdicts[id(r)].flags

    # The same amount printed under both years on the page: the column does not say which.
    split = _doc([_table("七、2", "交易性金融资产", [
        ("交易性金融资产", [("1783494750.68", "current", "期末余额"),
                     ("1703266947.76", "prior", "期初余额")]),
        ("其他", [("1703266947.76", "current", "期末余额")])])])
    judge = _judge(split, shipped)
    r = judge.judge_one(_reading(FVTPL, "1703266947.76", caption="交易性金融资产"))
    assert r.period == "current" and r.review
    assert any(f.startswith("whole_pdf:column_period:prior")
               for f in judge.verdicts[id(r)].flags)


def test_a_figure_not_found_on_its_page_is_left_as_the_page_check_left_it(shipped):
    judge = _judge(_doc([]), shipped)
    r = _reading(FVTPL, "123")
    r.check = W.Check("not_found", PAGE)
    assert judge.judge_one(r) is r
    assert id(r) not in judge.verdicts


def test_a_level_column_filed_by_position_says_nothing_about_the_year(shipped):
    """1966 note 46 keys its Level 3 cells `prior` by position, under 'Significant unobservable
    inputs (Level 3)', in the 2023 table as in the 2022 one. A current Level 3 answer read there is
    neither re-filed nor sent to review for its year."""
    cell = _cell("344135", "prior", 0.7, 0.6,
                 "Significant unobservable inputs (Level 3) 重大不可觀察輸入數據（第三級）")
    cell.column_period = None
    row = NoteItem(raw_label="Financial assets at fair value through profit or loss")
    row.values["l3"] = cell
    doc = _doc([NotesTable(note_number="46", title="FAIR VALUE HIERARCHY", items=[row],
                           source_pages=[PAGE])])
    judge = _judge(doc, shipped)
    r = judge.judge_one(_reading("sub__fa_cp_level_3_rows", "344135",
                                 caption="Financial assets at fair value through profit or loss"))
    assert r.period == "current" and not r.review
    assert not any(f.startswith("whole_pdf:column_period") for f in judge.verdicts[id(r)].flags)
