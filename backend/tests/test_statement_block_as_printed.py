"""A statement block carries that statement's rows, as the page prints them.

Measured on the twenty published filings before this: the request's statement block was the
reader's working copy rather than the statement —

* 688008's 财务费用 went out as 240504105.54 where the page prints -240,504,105.54 (the sign the
  engine normalises to, not the one printed);
* 688411's consolidated income statement opened with the company balance sheet's last rows
  (其他应付款, 实收资本（或股本）), because the page that statement ends on had been read as a note
  when the decode decided whether to split the page after it;
* 000685's consolidated balance sheet opened with the audit committee's minutes ('审计委员会' 7), a
  governance page titled a balance sheet off a table cell that wraps 资产负债表 / 日期后事项;
* 0160 opened every balance-sheet and cash-flow block with the units heading 'Notes HK$’' = 0;
* every CAS cash-flow block ran on into the indirect reconciliation printed in the notes;
* 688008's 公允价值变动收益 arrived captioned '七、70', its tail welded onto the next row
  ('列） 信用减值损失…'), and 300319's four blank financial-enterprise lines onto one caption;
* every CAS block said 'CNY, scale not stated' under a page that prints 单位：元.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.schemas.ontology import ScopeSelection
from app.services import face_context, units
from app.services.row_reconstruct import (Word, _statement_unit, _unit_context, _unit_signals,
                                          build_line_items)
from app.stages import classify
from app.stages.classify import ClassifyStage


def _row(caption, page, *, value="100", raw=None, key=None):
    li = LineItem(source_label=caption, canonical_key=key)
    li.values["v"] = ExtractedValue(
        basis=Basis.CONSOLIDATED, period_label="current", value=Decimal(value),
        value_raw=Decimal(raw if raw is not None else value), provenance=Provenance(page_index=page))
    return li


def _doc(rows, pages):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = pages
    doc.line_items = list(rows)
    return doc


def _face(index, statement, **evidence):
    return PageSource(index=index, kind=PageKind.FACE, statement=statement, evidence=evidence)


# ── figures as printed ───────────────────────────────────────────────────────────────────────────

def test_a_row_carries_the_figure_the_page_prints_not_the_normalised_sign():
    doc = _doc([_row("财务费用", 1, value="240504105.54", raw="-240504105.54")],
               [_face(1, "profit_and_loss")])
    row = face_context.face_rows(doc, {"profit_and_loss"})[0]["rows"][0]
    assert row["figures"] == {"current": "-240504105.54"}


# ── rows that are not the statement's ────────────────────────────────────────────────────────────

def test_a_units_heading_read_as_a_row_is_not_sent():
    doc = _doc([_row("Notes HK$’", 1, value="0"), _row("Notes receivable", 1, value="0"),
                _row("Investment properties", 1, value="5000")], [_face(1, "balance_sheet")])
    captions = [r["caption"] for r in face_context.face_rows(doc, {"balance_sheet"})[0]["rows"]]
    assert captions == ["Notes receivable", "Investment properties"]


def test_the_cash_flow_supplement_is_its_own_block_marked_as_printed_in_the_notes():
    pages = [_face(1, "cash_flow"), _face(9, "cash_flow", supplement=True,
                                          matched_title="现金流量表补充资料"),
             _face(10, "cash_flow")]
    doc = _doc([_row("销售商品、提供劳务收到的现金", 1), _row("净利润", 9), _row("无形资产摊销", 10)],
               pages)
    blocks = face_context.face_rows(doc, {"cash_flow"})
    assert [(b.get("part"), [r["caption"] for r in b["rows"]]) for b in blocks] == [
        (None, ["销售商品、提供劳务收到的现金"]), ("supplement", ["净利润", "无形资产摊销"])]
    assert blocks[1]["printed_in"] == "notes" and blocks[1]["title"] == "现金流量表补充资料"
    # Titled so, it is the supplement also where the classifier did not mark it.
    del pages[1].evidence["supplement"]
    pages[1].evidence["matched_title"] = "79、现金流量表补充资料"
    assert [b.get("part") for b in face_context.face_rows(doc, {"cash_flow"})] == [
        None, "supplement"]


def test_a_titled_run_that_prints_no_amount_is_not_a_statement():
    pages = [_face(63, "balance_sheet", matched_title="资产负债表"), _face(64, "balance_sheet"),
             _face(127, "balance_sheet", matched_title="1、合并资产负债表"),
             _face(128, "balance_sheet")]
    cache = [([{"text": "审计委员会", "y": 1.0}, {"text": "7", "y": 2.0}], 10.0),
             ([{"text": "2024 年07 月", "y": 1.0}], 10.0),
             ([{"text": "1、合并资产负债表", "y": 9.0}], 10.0),          # titled at the foot …
             ([{"text": "货币资金 6,843,296,852.61", "y": 1.0}], 10.0)]   # … figures overleaf
    assert classify._drop_runs_without_figures(pages, cache) == 2
    assert [(p.kind, p.statement) for p in pages] == [
        (PageKind.OTHER, None), (PageKind.OTHER, None),
        (PageKind.FACE, "balance_sheet"), (PageKind.FACE, "balance_sheet")]


def test_a_reclaimed_continuation_is_the_statement_running_above_the_next_title():
    prev = _face(134, "balance_sheet")
    prev.scope = "company"
    page = PageSource(index=135, kind=PageKind.NOTES, statement=None, evidence={})
    nxt = _face(136, "profit_and_loss", matched_title="合并利润表", matched_title_y=0.77,
                statement_before_title=None)
    feats = [SimpleNamespace(notes_banner=False, amounts_above_title=False),
             SimpleNamespace(notes_banner=False, amounts_above_title=False),
             SimpleNamespace(notes_banner=False, amounts_above_title=True)]
    cache = [([], 1.0), ([{"text": "衍生金融资产", "y": 0.1}], 1.0), ([], 1.0)]
    ClassifyStage._reclaim_statement_continuations([prev, page, nxt], feats, cache,
                                                   PipelineContext())
    assert page.kind is PageKind.FACE
    assert (nxt.evidence["statement_before_title"], nxt.evidence["scope_before_title"]) == (
        "balance_sheet", "company")


# ── captions ─────────────────────────────────────────────────────────────────────────────────────

LH = 0.011


def _words(y, text, x0=0.10, *, note=None, values=()):
    out = [Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x0 + 0.016 * len(text), y1=y + LH))]
    if note:
        out.append(Word(text=note, bbox=BBox(x0=0.45, y0=y, x1=0.50, y1=y + LH)))
    for x, v in zip((0.66, 0.84), values):
        out.append(Word(text=v, bbox=BBox(x0=x, y0=y, x1=x + 0.08, y1=y + LH)))
    return out


def test_a_caption_wrapped_around_its_figures_keeps_them_and_its_tail():
    """688008 sets 七、70 and both figures between the two lines of 公允价值变动收益（…号填 / 列）."""
    y = 0.40
    words = (_words(y, "投资收益（损失以“－”号填列）", values=("48,599,096.46", "90,978,489.24"))
             + _words(y + 1.3 * LH, "公允价值变动收益（损失以“－”号填")
             + [w for w in _words(y + 1.75 * LH, "x", note="七、70",
                                  values=("23,888,571.20", "-87,328,284.96"))[1:]]
             + _words(y + 2.3 * LH, "列）")
             + _words(y + 3.3 * LH, "信用减值损失（损失以“-”号填列）",
                      values=("-464,514.83", "143,860.44")))
    items, _ = build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                statement="profit_and_loss")
    by_caption = {li.source_label: str(li.values[next(iter(li.values))].value) for li in items}
    assert by_caption == {"投资收益（损失以“－”号填列）": "48599096.46",
                          "公允价值变动收益（损失以“－”号填列）": "23888571.20",
                          "信用减值损失（损失以“-”号填列）": "-464514.83"}


def test_a_chapter_note_reference_set_tight_against_a_wrapped_caption_is_the_note():
    """600990 runs 信用减值损失（损失以“-”号填 right up to the note column and outdents 列）."""
    y = 0.40
    head = _words(y, "信用减值损失（损失以“-”号填")
    head[0].bbox = BBox(x0=0.10, y0=y, x1=0.449, y1=y + LH)
    words = (head
             + [w for w in _words(y + 0.55 * LH, "x", note="七、71",
                                  values=("-47,657,791.88", "-43,487,389.97"))[1:]]
             + _words(y + 1.25 * LH, "列）", x0=0.07)
             + _words(y + 2.5 * LH, "资产处置收益（损失以“－”号填列）", values=("12,972,750.43",)))
    items, _ = build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                statement="profit_and_loss")
    assert "信用减值损失（损失以“-”号填列）" in [li.source_label for li in items]
    assert not any("七、71" in li.source_label for li in items)


def test_the_blank_financial_enterprise_lines_are_not_welded_onto_the_next_caption():
    y, step = 0.30, 1.4 * LH
    specs = [("拆出资金净增加额", ()), ("支付利息、手续费及佣金的现金", ()), ("支付保单红利的现金", ()),
             ("支付给职工以及为职工支付的现金", ("378,035,457.71", "343,322,143.25"))]
    words = [w for i, (t, v) in enumerate(specs) for w in _words(y + i * step, t, values=v)]
    items, _ = build_line_items(words, page_index=0, document_id="d", source_kind="native",
                                statement="cash_flow",
                                known_captions=frozenset())
    assert [li.source_label for li in items] == ["支付给职工以及为职工支付的现金"]


# ── units ────────────────────────────────────────────────────────────────────────────────────────

def test_the_mainland_units_line_is_read_and_said():
    scope = ScopeSelection()
    scope.units_and_currency.signals = ["RMB'000", "单位：元", "单位：万元"]
    signals = _unit_signals(scope)
    header = [[Word(text="2024年12月31日", bbox=BBox(x0=0.1, y0=0.1, x1=0.3, y1=0.11)),
               Word(text="单位：元", bbox=BBox(x0=0.6, y0=0.1, x1=0.7, y1=0.11)),
               Word(text="币种：人民币", bbox=BBox(x0=0.75, y0=0.1, x1=0.9, y1=0.11))],
              [Word(text="货币资金", bbox=BBox(x0=0.1, y0=0.13, x1=0.2, y1=0.14)),
               Word(text="6,843,296,852.61", bbox=BBox(x0=0.6, y0=0.13, x1=0.7, y1=0.14))]]
    unit = _unit_context(_statement_unit(header, signals), 0)
    doc_unit = SimpleNamespace(currency="CNY", scale_factor=1, units_label=None)
    assert units.describe(unit, doc_unit) == "CNY units (x1)"
    万 = next(s for s in signals if s[0].endswith("万元"))
    assert 万[2] == Decimal(10_000)


def test_a_continuation_page_that_restates_no_unit_does_not_split_the_block():
    entries = [{"_unit": "CNY units (x1)"}, {"_unit": "CNY, scale not stated"}]
    assert units.block_units(entries) == "CNY units (x1)"
    assert entries == [{}, {}]
    mixed = [{"_unit": "CNY units (x1)"}, {"_unit": "CNY thousands (x1,000)"}]
    assert units.block_units(mixed) == "varies by row; see each row's unit"
