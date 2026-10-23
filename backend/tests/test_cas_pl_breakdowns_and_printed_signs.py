"""The CAS income-statement errors left after the note rows reached their lines.

Four defects, each measured on the reference corpus, each moving computed profit before tax off
the figure the filing prints:

(a) 其中：对联营企业和合营企业的投资收益 is bound to Income(Loss) from Related Party BESIDE 投资收益,
    which already contains it — counted twice. 000709 +631,525,977.09 / +684,699,218.15.
(b) 信用减值损失（损失以“-”号填列） printed POSITIVE is a reversal; the rollup spent it as a cost
    magnitude, moving the total by twice the figure. 688008 prior 287,720.88 (2 x 143,860.44).
(c) A 其中 breakdown that opens a page — its total printed at the foot of the page before — had no
    parent, and its second line was swept into Other Operating Expenses. 600990 prior 1,300,261.11.
(d) 其中：利息费用 printed -9,816.85 under an unsigned 财务费用 is a credit; -|raw| charged it.
    301577 current 19,633.70.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, PrintedIn
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services.rollups import CARVED_FROM_FACE, evaluate_rows

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def shipped():
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import load_template
    from app.services.working_view import build_working_view

    raw = json.loads((_SAMPLES / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
    cfg = load_line_item_set(json.loads(
        (_SAMPLES / "output_csv_hk_line_items.json").read_text(encoding="utf-8")), resolve=True)
    return raw, load_template(raw), build_working_view(cfg)


def _item(label: str, raw: str, *, key: str | None, parent: LineItem | None = None,
          period: str = "current") -> LineItem:
    li = LineItem(source_label=label, canonical_key=key, printed_in=PrintedIn.FACE,
                  parent_id=parent.id if parent else None)
    li.set_value(ExtractedValue(value=Decimal(raw), value_raw=Decimal(raw),
                                basis=Basis.CONSOLIDATED, period_label=period,
                                provenance=Provenance(page_index=0)))
    li.confidence.method = "exact"
    return li


def _row(label: str, value: str, key: str, *, printed_in: str = "face", flags=()) -> dict:
    return {"canonical_key": key, "source_label": label, "printed_in": printed_in,
            "mapping_method": "exact",
            "values": [{"basis": "consolidated", "period_label": "current", "value": value,
                        "confidence": {"flags": list(flags)}}]}


# ── (b) a line printed signed follows its printed sign ─────────────────────────────────────────

_NOP = {"statements": [{"type": "profit_and_loss", "sections": [{
    "canonical_key": "nop", "role": "subtotal",
    "rollup": {"op": "sum", "children": ["gp", "bad_debt", "selling"],
               "cost_magnitude_children": ["bad_debt", "selling"]}}]}]}


def _nop(*rows) -> float:
    return evaluate_rows(_NOP, list(rows), "consolidated", "current")["nop"].value


def test_a_reversal_printed_positive_adds_to_the_total():
    """688008 prior: 信用减值损失（损失以“-”号填列） 143,860.44 is a reversal and raises the profit."""
    assert _nop(_row("营业毛利", "1000", "gp"),
                _row("信用减值损失（损失以“-”号填列）", "143.86", "bad_debt"),
                _row("销售费用", "100", "selling")) == pytest.approx(1043.86)


def test_a_loss_printed_negative_is_still_a_charge():
    assert _nop(_row("营业毛利", "1000", "gp"),
                _row("信用减值损失（损失以“-” 号填列）", "-143.86", "bad_debt"),
                _row("销售费用", "100", "selling")) == pytest.approx(756.14)


def test_an_unsigned_cost_is_still_spent_as_a_magnitude():
    """The guard: a caption that says nothing about its sign keeps the magnitude rule, so a cost
    printed positive (CAS) or negative (HKEX) is spent either way."""
    for value in ("143.86", "-143.86"):
        assert _nop(_row("营业毛利", "1000", "gp"),
                    _row("Impairment losses on trade receivables", value, "bad_debt"),
                    _row("销售费用", "100", "selling")) == pytest.approx(756.14)


def test_the_note_is_read_only_off_a_face_row():
    assert _nop(_row("营业毛利", "1000", "gp"),
                _row("信用减值损失（损失以“-”号填列）", "143.86", "bad_debt", printed_in="note"),
                _row("销售费用", "100", "selling")) == pytest.approx(756.14)


def test_the_sign_note_is_recognised_in_the_spellings_the_corpus_prints():
    from app.services.rollups import prints_loss_with_minus

    for caption in ("信用减值损失（损失以“-”号填列）", "资产减值损失（损失以“-” 号填列）",
                    "信用减值损失（损失以“-”号填 列）", "资产减值损失(损失以\"－\"号填列)"):
        assert prints_loss_with_minus(caption), caption
    for caption in ("信用减值损失", "存货的减少（增加以“－”号填列）", "Impairment loss"):
        assert not prints_loss_with_minus(caption), caption


def test_the_structural_relation_follows_the_printed_sign_too():
    from app.services.structural_checks import Relation, _check, collect_values

    items = [_item("三、营业利润（亏损以“－”号填列）", "1043.86", key="nop"),
             _item("营业毛利", "1000", key="gp"),
             _item("信用减值损失（损失以“-”号填列）", "143.86", key="bad_debt"),
             _item("销售费用", "100", key="selling")]
    vals = collect_values(items)
    assert ("bad_debt", ("consolidated", "current")) in vals.signed
    rel = Relation(id="rollup:nop", kind="rollup", statement="profit_and_loss", target="nop",
                   components=("gp", "bad_debt", "selling"), op="sum",
                   magnitudes=frozenset({"bad_debt", "selling"}))
    assert _check(rel, ("consolidated", "current"), vals).status == "pass"


# ── (a) a 其中 row beside the total that holds it is shown, not added ──────────────────────────

def _carve(shipped, rows):
    from app.stages.map_ontology import MapOntologyStage

    raw, template, _ = shipped
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = template
    MapOntologyStage._carve_breakdowns_beside_their_total(doc, ctx)
    return doc


def _flags(li: LineItem) -> list[str]:
    return [f for ev in li.values.values() for f in ev.confidence.flags]


def test_the_associates_share_is_not_added_beside_the_investment_income_that_holds_it(shipped):
    """000709 current: 投资收益 649,740,308.49 holds its 其中 631,525,977.09."""
    total = _item("投资收益（损失以“－”号填列）", "649740308.49",
                  key="is_pl__other_fncl_asset_income_dvds")
    part = _item("其中：对联营企业和合营企业的投资收益", "631525977.09",
                 key="is_pl__income_loss_from_related_party", parent=total)
    doc = _carve(shipped, [total, part])
    assert CARVED_FROM_FACE in _flags(part)
    assert CARVED_FROM_FACE not in _flags(total)

    from app.api.routes.extractions import _serialize_rows
    raw = shipped[0]
    calc = evaluate_rows(raw, _serialize_rows(doc), "consolidated", "current")
    assert calc["is_pl__net_other_financial_inc_exp"].value == pytest.approx(649740308.49)


def test_a_breakdown_of_the_rollups_own_target_stays_a_component(shipped):
    """财务费用 is `is_pl__net_interest_income_expense`, the subtotal its 其中：利息费用 and 利息收入
    are components of — those rows ARE how the subtotal is computed."""
    finance = _item("财务费用", "-6183176066.54", key="is_pl__net_interest_income_expense")
    interest = _item("其中：利息费用", "-5144993111.59", key="is_pl__interest_expense",
                     parent=finance)
    _carve(shipped, [finance, interest])
    assert CARVED_FROM_FACE not in _flags(interest)


def test_a_breakdown_with_no_figure_in_the_parents_column_is_left(shipped):
    total = _item("投资收益", "100", key="is_pl__other_fncl_asset_income_dvds", period="prior")
    part = _item("其中：对联营企业和合营企业的投资收益", "40",
                 key="is_pl__income_loss_from_related_party", parent=total)
    _carve(shipped, [total, part])
    assert CARVED_FROM_FACE not in _flags(part)


# ── (d) a contra line under an unsigned expense keeps its printed sign ─────────────────────────

def _normalised(shipped, rows):
    from app.stages.normalize import NormalizeStage

    _, template, ontology = shipped
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = template
    ctx.ontology = ontology
    NormalizeStage().run(doc, ctx)
    return {li.source_label: next(iter(li.values.values())).value for li in doc.line_items}


def test_interest_printed_negative_under_an_unsigned_finance_cost_is_a_credit(shipped):
    """301577 current: 财务费用 -8,093,311.01, 其中：利息费用 -9,816.85."""
    finance = _item("财务费用", "-8093311.01", key="is_pl__net_interest_income_expense")
    interest = _item("其中：利息费用", "-9816.85", key="is_pl__interest_expense", parent=finance)
    out = _normalised(shipped, [finance, interest])
    assert out["其中：利息费用"] == Decimal("9816.85")
    assert out["财务费用"] == Decimal("8093311.01")


def test_interest_printed_positive_is_still_a_charge(shipped):
    finance = _item("财务费用", "247930035.60", key="is_pl__net_interest_income_expense")
    interest = _item("其中：利息费用", "304895510.76", key="is_pl__interest_expense",
                     parent=finance)
    assert _normalised(shipped, [finance, interest])["其中：利息费用"] == Decimal("-304895510.76")


def test_without_the_unsigned_parent_the_contra_rule_stands(shipped):
    """The guard: an HKEX "Finance costs (9,816)" has no 财务费用 above it and stays -|raw|."""
    interest = _item("Finance costs", "-9816.85", key="is_pl__interest_expense")
    assert _normalised(shipped, [interest])["Finance costs"] == Decimal("-9816.85")


# ── (c) a 其中 breakdown across the page break ──────────────────────────────────────────────────

def _page(lines, *, char: float = 0.0145, carry_above: LineItem | None = None):
    """``lines`` are ``(x0, caption, figure)``; 600990's page index 74, measured off its PDF."""
    from app.services.row_reconstruct import Word, build_line_items

    words: list[Word] = []
    for i, (x0, caption, cur) in enumerate(lines):
        y = 0.10 + i * 0.03
        words.append(Word(text=caption, bbox=BBox(x0=x0, y0=y, x1=x0 + char * len(caption),
                                                  y1=y + 0.012)))
        if cur is not None:
            words.append(Word(text=cur, bbox=BBox(x0=0.80, y0=y, x1=0.89, y1=y + 0.012)))
    items, _ = build_line_items(words, page_index=74, document_id=None, source_kind="native",
                                statement="profit_and_loss", carry_above=carry_above)
    return {i.source_label: i for i in items}


_PAGE_74 = [
    (0.204, "其中：对联营企业和合营企业", None),
    (0.151, "的投资收益", None),
    (0.248, "以摊余成本计量的金融资产终止确认收益", "-1,300,261.11"),
    (0.204, "汇兑收益（损失以“－”号填列）", None),
    (0.151, "信用减值损失（损失以“-”号填列）", "-43,487,389.97"),
]


def test_a_breakdown_opening_the_page_belongs_to_the_last_row_of_the_page_before():
    above = LineItem(source_label="投资收益（损失以“-”号填列）")
    rows = _page(_PAGE_74, carry_above=above)
    assert rows["以摊余成本计量的金融资产终止确认收益"].parent_id == above.id
    assert rows["信用减值损失（损失以“-”号填列）"].parent_id is None


def test_without_the_page_before_the_breakdown_has_nothing_to_be_inside():
    rows = _page(_PAGE_74)
    assert rows["以摊余成本计量的金融资产终止确认收益"].parent_id is None


def test_a_page_whose_first_body_line_is_not_a_breakdown_carries_nothing():
    above = LineItem(source_label="投资收益（损失以“-”号填列）")
    rows = _page([(0.151, "公允价值变动收益（损失以“－”号填列）", "5.00"),
                  (0.204, "其中：对联营企业和合营企业", None),
                  (0.248, "以摊余成本计量的金融资产终止确认收益", "1.00")], carry_above=above)
    assert all(r.parent_id != above.id for r in rows.values())


def test_the_wrapped_tail_of_a_breakdown_line_does_not_close_it():
    """Same page: the 其中： line wraps, its tail set back at the margin, and the breakdown's
    second line is still inside the row above."""
    rows = _page([(0.151, "投资收益（损失以“-”号填列）", "6,592,631.61"), *_PAGE_74])
    parent = rows["投资收益（损失以“-”号填列）"]
    assert rows["以摊余成本计量的金融资产终止确认收益"].parent_id == parent.id
    assert rows["信用减值损失（损失以“-”号填列）"].parent_id is None


def test_the_residual_refuses_the_breakdown_once_it_has_its_parent(shipped):
    from app.core.models.document import PageSource
    from app.stages.residual import ResidualStage

    _, template, ontology = shipped
    total = _item("投资收益（损失以“-”号填列）", "739747.47",
                  key="is_pl__other_fncl_asset_income_dvds")
    part = _item("以摊余成本计量的金融", "-1300261.11", key=None, parent=total)
    total.ordinal, part.ordinal = 0, 1
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="profit_and_loss")]
    doc.line_items = [total, part]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template, ctx.ontology, ctx.mapping_strategy = template, ontology, "deterministic"
    ResidualStage().run(doc, ctx)
    assert part.canonical_key is None
    assert "residual_ineligible:of which breakdown" in part.confidence.flags
