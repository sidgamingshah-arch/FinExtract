"""WHETHER A PAGE IS TWO PRINTED PAGES IS DECIDED ONCE, ON THE PAGE, AND A PORTRAIT PAGE IS ONE.

`row_reconstruct._group_rows` asks `page_spread.gutter_x` of whatever words it is handed, and the
readers hand it PART of a page — a note section, a batch below a statement title. Its four tests are
tests of a printed page, and a section can pass all of them: 河钢股份 000709's
计入当期损益的政府补助情况 prints its two figure columns either side of a blank band with a
其他收益 column down the right, and read as a note ending at the chapter heading below it, it was
split into two "pages" — every 上期 figure torn off its row.

And the page-level answer was wrong too. A 2-up spread is two portrait pages on one landscape
sheet, and the detector, handed normalised words, cannot see the sheet: measured over the five
reference filings, 31 pages were called spreads, EVERY ONE of them portrait — 000709's
related-party pages among them, where a figure column on the right read as a second printed page.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services import page_spread
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word, _group_rows

H = 0.0107
PITCH = 0.024


def _w(text: str, x0: float, x1: float, y: float) -> Word:
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


GRANTS = [("高新技术研发应用补助", "2,829,119.34", "3,637,773.66"),
          ("环境保护专项补助", "65,372,173.77", "13,121,038.32"),
          ("节能环保改造补助", "23,526,284.76", "21,136,217.52"),
          ("人才培养补助资金", "125,469.93", "334,309.02"),
          ("技术研发经费", "3,163,563.67", "2,329,772.39"),
          ("稳岗补贴", "352,689.12", "622,314.18"),
          ("科研补助经费", "18,867.92", "5,000.00"),
          ("企业发展奖补", "1,878,200.00", "8,571,800.00")] * 2


def _grants_table() -> list[Word]:
    """000709 page 170's geometry: caption | 本期 | 上期 | 列报项目 | 与资产/收益相关."""
    words = [_w("2、", 0.095, 0.115, 0.060), _w("计入当期损益的政府补助情况", 0.120, 0.330, 0.060),
             _w("补助项目", 0.166, 0.227, 0.096), _w("本期计入损益金额", 0.320, 0.441, 0.096),
             _w("上期计入损益金额", 0.489, 0.610, 0.096)]
    y = 0.126
    for caption, current, prior in GRANTS:
        words += [_w(caption, 0.104, 0.255, y), _w(current, 0.374, 0.454, y),
                  _w(prior, 0.547, 0.627, y), _w("其他收益", 0.645, 0.706, y),
                  _w("与资产相关", 0.790, 0.866, y)]
        y += PITCH
    return words


def test_the_grants_table_on_its_own_passes_for_a_spread():
    """The trap, stated: nothing in the four tests distinguishes this table from two pages."""
    assert page_spread.gutter_x(_grants_table()) is not None


def test_a_portrait_sheet_has_no_fold():
    words = _grants_table()
    assert page_spread.page_fold(words, 595.0, 842.0) is None
    assert page_spread.page_fold(words, 842.0, 595.0) == page_spread.gutter_x(words)
    # A statement printed sideways is read turned: its reading space is the sheet's other way up.
    assert page_spread.page_fold(words, 595.0, 842.0, 90) == page_spread.gutter_x(words)
    assert page_spread.page_fold(words, 842.0, 595.0, 270) is None


def test_rows_grouped_with_the_pages_answer_are_whole_lines():
    rows = _group_rows(_grants_table(), 0.005, fold=None)
    grant_rows = [r for r in rows if r[0].text in {c for c, _, _ in GRANTS}]
    assert len(grant_rows) == len(GRANTS)
    assert all(len(r) == 5 for r in grant_rows)


def test_a_note_section_on_one_printed_page_keeps_both_periods():
    tables = extract_note_tables(_grants_table(), page_index=170, document_id="d",
                                 source_kind="native", chapter=["十", 10, "政府补助"],
                                 page_fold=None)
    rows = [r for t in tables for r in t.items]
    assert len(rows) == len(GRANTS)
    for row, (_caption, current, prior) in zip(rows, GRANTS):
        got = {ev.period_label: ev.value for ev in row.values.values()}
        assert {k: str(v) for k, v in got.items()} == {
            "current": current.replace(",", ""), "prior": prior.replace(",", "")}, row.raw_label
