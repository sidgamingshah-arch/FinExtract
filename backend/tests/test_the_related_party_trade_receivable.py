"""The related-party TRADE receivable, read from the note — and the double count that is the point.

INSTRUCTED, and the overlap is accepted rather than avoided: "for now want to double count on
related parties". `bs_ca__trade_receivables_related_parties` was alias-only and empty on every
filing in the corpus, while a CAS 关联方应收应付款项 note tabulates the 应收账款 group on all three
mainland filings. That same group is ALREADY inside `sub__rp_find_3_gross`, which feeds
`bs_nca__due_from_related_parties_ltp` — Find 3 sums the whole related-party note — so 000709's
378,930,401.75 now publishes in BOTH columns. That is the instruction, recorded here so a future
reader does not "fix" it as a defect.

  000709  应收账款 合计   952,791,540.11 gross  less  573,861,138.36 allowance  =  378,930,401.75
                                        (prior 888,366,008.76 less 568,739,288.50 = 319,626,720.26)

THREE PARTS, because a `derived` parent cannot be matched to a caption
(`mapping._computed_parent`): the 账面余额 gross, the 坏账准备 allowance it is net of, and a FACE
part carrying the aliases the column had to give up in order to hold a cascade at all — the shape
`sub__face_principal_revenue` uses for `is_pl__sales_revenues`.

BOTH NOTE PARTS DECLARE `from_measure_grid: true`, AND THAT IS THE LOAD-BEARING GUARD. A
关联方 table is laid out 项目名称 | 账面余额 | 坏账准备 | 账面余额 | 坏账准备 — two periods, two measures
— and the reader can only tell the allowance column from a period when it reads that grid. It
needs more than one data row to derive the bands from, and 澜起科技 688008's table has exactly ONE
(英特尔公司). Measured without the guard, that filing published

    bs_ca__trade_receivables_related_parties  current 86,260.80   prior 431.30

where 431.30 is the 期末坏账准备 — the ALLOWANCE read as the comparative period. With the guard the
column stays empty there, which is the right answer: a figure in the wrong period is worse than no
figure. The same limit holds the PAYABLE twin back and it is not shipped — 688008 prints
`应付账款 英特尔公司 - 1,403,741.56`, the nil dash yields no word, one figure gives one band, and
1,403,741.56 (the PRIOR period) was published as current.

AND A NOTE TABLE'S COLUMN HEADER IS NOT A CAPTION'S HEAD. 688008's ②应付项目 table prints one header
row and it was folded onto the first data row —
`项目名称关联方期末账面余额期初账面余额应付账款英特尔公司 -` — so the group heading 应付账款, which is
the caption (the row's own text being a counterparty NAME), no longer started the label and no
anchored pattern could reach it. `_HDR_LABEL` already carries 期末余额|期初余额, which is why 000709
reads correctly; 期末账面余额 is the 账面 variant and contains neither as a substring.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models.geometry import BBox
from app.services.mapping import normalize_label
from app.services.row_reconstruct import (
    Word,
    _is_note_column_header,
    _pipeline_steps,
    build_line_items,
    in_force_rules,
)

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
COLUMN = "bs_ca__trade_receivables_related_parties"
GROSS, ALLOW = "sub__rp_trade_receivable_gross", "sub__rp_trade_receivable_allowance"
FACE = "sub__rp_face_trade_receivable"


@pytest.fixture(scope="module")
def shipped():
    from app.services.line_item_config import load_shipped_set
    return {i.key: i for i in load_shipped_set().items}


@pytest.fixture(scope="module")
def seed():
    return {i["key"]: i for i in json.loads(
        (TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))["items"]}


# ── the column ────────────────────────────────────────────────────────────────────────────────

def test_the_column_is_its_cascade_and_the_face_rung_is_last(shipped):
    col = shipped[COLUMN]
    assert str(col.type) == "derived"
    assert [r.id for r in col.cascade] == ["NET_OF_ALLOWANCE", "FROM_THE_FACE"]
    net = col.cascade[0]
    assert [(t.ref, t.role, t.sign) for t in net.terms] == [
        (GROSS, "required", 1), (ALLOW, "adjustment", -1)]
    assert net.refuse_negative
    # LAST, so the face can never displace the note — the note is the specific disclosure.
    face = col.cascade[-1]
    assert [t.ref for t in face.terms] == [FACE]
    assert not face.outranks_printed


def test_the_face_part_holds_the_aliases_the_column_gave_up(shipped, seed):
    assert not (seed[COLUMN].get("aliases") or [])
    assert not (seed[COLUMN].get("keyword_hints") or [])
    part = seed[FACE]
    assert part["aliases"], "the column's caption vocabulary must not simply be deleted"
    assert normalize_label("Trade receivables- related parties") in {
        normalize_label(a) for a in part["aliases"]}
    # A part with aliases needs a gate, or a caption could bind it from any statement.
    assert part["statement"] == "balance_sheet"
    assert part["section_scope"] == ["bs_ca"]
    assert part["route"] == "face"


@pytest.mark.parametrize("key", [GROSS, ALLOW])
def test_both_note_parts_require_the_measure_grid(seed, key):
    """Without this, 688008's single-data-row table published its 坏账准备 as the prior period."""
    assert seed[key]["note_source"]["from_measure_grid"] is True


def test_the_allowance_asks_for_its_column_by_name_and_the_gross_takes_the_primary(seed):
    assert seed[ALLOW]["note_source"]["measure"] == "allowance"
    assert not seed[GROSS]["note_source"].get("measure")


def test_only_the_guarded_trade_parts_reach_a_bare_应收项目_note(seed):
    """The asymmetry, and why it is closed on ONE side only.

    The payable side has carried `^\\s*[①②③④⑤⑥]?\\s*应付项目\\s*$` since it was written; the
    receivable side had no bare-title pattern at all, so it could only reach a note whose CHAPTER
    said 关联方及关联交易 — which 000709 prints and 688008/300319 do not (their related-party notes
    carry the NEXT chapter's title, bled across the heading).

    GIVING IT TO FIND 3 AS WELL WAS TRIED AND REVERTED, measured: Find 3 has no
    `from_measure_grid` guard and cannot take one, because it also reads note shapes that are
    plain comparatives rather than grids. With the bare title it reached 688008's single-data-row
    应收项目 table, the grid went unread, and `bs_nca__due_from_related_parties_ltp` published
    86,260.80 as the current period with 431.30 — the 期末坏账准备 — as the comparative. The
    pattern therefore lives only on the two parts that refuse a gridless table."""
    # BY MEANING NOW, and Find 3 reaches the bare 应收项目 heading too: its note terms name the
    # related-party note as filings head it. Re-measured with the family simulator on 688008 —
    # the filing where this was once reverted — the LTP column reads 85,829.50 / 1,904,458.71
    # both before and after, the grid being read as a grid now.
    for key in (GROSS, ALLOW, "sub__rp_find_3_gross", "sub__rp_find_3_allowance"):
        assert "应收项目" in seed[key]["note_source"]["note_terms"], key
        assert not seed[key]["note_source"]["note_title_any"], key


def test_the_trade_group_refuses_the_groups_that_belong_to_other_columns(seed):
    """One group per column: 其他应收款 and 长期应收款 are Find 3's, 预付款项 is a prepayment."""
    for key in (GROSS, ALLOW):
        none = " ".join(seed[key]["note_source"]["row_caption_none"])
        for other in ("其他应收款", "长期应收款", "预付款项", "应收票据"):
            assert other in none, (key, other)
        any_ = " ".join(seed[key]["note_source"]["row_caption_any"])
        assert "应收账款" in any_


def test_the_overlap_with_find_3_is_deliberate_and_recorded(seed):
    """Find 3 sums the whole related-party note, 应收账款 included, and feeds the LTP column. The
    instruction was to take the double count, so BOTH readings stay — and the reason is written
    down where a reader will meet it."""
    find3 = " ".join(seed["sub__rp_find_3_gross"]["note_source"]["row_caption_any"])
    assert "其他应收款" in find3, "Find 3 still reads the note's other-receivable group"
    # The prompt is a plain sentence now; the double count is the cascade's to record, and the LTP
    # column's own definition says it reads the whole related-party note, 应收账款 included.


# ── the column header is not a caption ────────────────────────────────────────────────────────

LINE_H = 0.011
GLYPH = 0.016


@pytest.fixture(scope="module")
def steps():
    scope, norm = in_force_rules()
    return _pipeline_steps(norm, scope)


def _w(text: str):
    return [Word(text=text, bbox=BBox(x0=0.1, y0=0.1, x1=0.1 + GLYPH * len(text), y1=0.111))]


@pytest.mark.parametrize("header", [
    "项目名称 关联方 期末账面余额 期初账面余额",
    "项目名称 关联方",
    "项目名称",
    "期末账面余额 期初账面余额",
    "項目名稱 關聯方 期末賬面餘額 期初賬面餘額",
])
def test_a_note_tables_column_header_is_recognised(steps, header):
    assert _is_note_column_header(_w(header), steps), header


@pytest.mark.parametrize("caption", [
    "应收账款", "其他应收款", "长期应收款", "预付款项", "应付账款",
    # the CAPTION column's own entries, which must never read as its header
    "货币资金", "存货", "合同资产", "归属于母公司所有者权益合计",
])
def test_a_real_caption_is_not_mistaken_for_a_column_header(steps, caption):
    assert not _is_note_column_header(_w(caption), steps), caption


def _line(y: float, text: str, *vals: str) -> list[Word]:
    out = [Word(text=text, bbox=BBox(x0=0.10, y0=y, x1=0.10 + GLYPH * len(text), y1=y + LINE_H))]
    for i, v in enumerate(vals):
        out.append(Word(text=v, bbox=BBox(x0=0.60 + 0.15 * i, y0=y, x1=0.70 + 0.15 * i,
                                          y1=y + LINE_H)))
    return out


def test_the_header_is_not_folded_onto_the_first_data_row():
    """688008's ②应付项目 table, to scale. The group heading has to keep starting the label or no
    anchored `row_caption_any` pattern can reach it."""
    words = [w for line in (_line(0.100, "项目名称关联方期末账面余额期初账面余额"),
                            _line(0.115, "应付账款英特尔公司", "-", "1,403,741.56"))
             for w in line]
    items, _ = build_line_items(words, page_index=0, document_id="d1", source_kind="native",
                                statement="notes", on_face=False)
    labels = [li.source_label for li in items]
    # The nil dash rides along in the label — it parses as no figure, so `_scan_row` leaves it on
    # the label side. What matters is that the header is gone and 应付账款 STARTS the caption.
    assert labels == ["应付账款英特尔公司 -"], labels
    assert not any("项目名称" in l for l in labels), labels
