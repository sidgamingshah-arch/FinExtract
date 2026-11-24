"""A model's sum that adds a 其中 breakdown row to the row it breaks down counts the money once.

The findings (fix_matrix:qizhong_breakdowns, securities_level3:earlier_fixes_full). A row with a
其中 breakdown under it IS the sum of that breakdown. 300319 迈捷 2024 七、2 prints
以公允价值计量且其变动计入当期损益的金融资产 431,478,888.81 with 其中：结构性理财产品 373,011,600.33 and
其中：权益工具投资 58,467,288.48 beneath it; a component answer citing all three published
862,957,777.62. The rules route reads the breakdown instead of the row (`note_sourced._broken_down`,
and across a page break `_broken_down_across_fragments`), but those fixes only filled slots the
model left empty — where the model answered, its sum stood, with a disagreement flag.

The model route now applies the rules' own reading of the shape (`note_sourced.breakdown_groups`,
`breakdowns_across_fragments`) to the model's citations (`services.answer_checks.
refuse_breakdowns_of_cited_totals`): the cited ROW is kept and each breakdown row added beside it
is refused — except where the row holds a component the line excludes (a forward) and the answer
cites the rest of its breakdown, where the breakdown is read instead, as the rules read it.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import answer_checks
from app.services.working_view import build_working_view
from app.stages.line_item_llm import LineItemLlmStage

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")
FVTPL = "sub__fa_cp_fvtpl_note_total"
PARENT = "以公允价值计量且其变动计入当期损益的金融资产"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(autouse=True)
def _restore_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider,
           ex.llm_document_context)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider,
         ex.llm_document_context) = was


def _row(caption, figures, y, *, group="", number="七、2", page=145):
    row = NoteItem(raw_label=caption, note_number=number, group_hint=group)
    for n, (period, amount) in enumerate(figures.items()):
        row.set_value(ExtractedValue(
            value=Decimal(amount), basis=Basis.CONSOLIDATED, period_label=period,
            provenance=Provenance(source_kind="native", page_index=page,
                                  bbox=BBox(x0=0.55 + 0.25 * n, y0=y, x1=0.65 + 0.25 * n,
                                            y1=y + 0.012))))
    return row


def _maijie():
    """300319's 七、2, current year."""
    return NotesTable(note_number="七、2", title="、交易性金融资产", items=[
        _row(PARENT, {"current": "431478888.81"}, 0.30),
        _row("结构性理财产品", {"current": "373011600.33"}, 0.32, group="其中："),
        _row("权益工具投资", {"current": "58467288.48"}, 0.34, group="其中："),
        _row("合计", {"current": "431478888.81"}, 0.36)])


def _doc(notes):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=3, kind=PageKind.FACE, statement="balance_sheet")]
    doc.notes = list(notes)
    li = LineItem(source_label="Profit before tax", canonical_key="pl_profit_before_tax")
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal("1000"), provenance=Provenance(page_index=3))
    doc.line_items = [li]
    return doc


def _run(shipped, doc, answer, context="full"):
    st = get_settings()
    ctx = PipelineContext(raw_bytes=b"", settings=st)
    ctx.line_items, ctx.ontology = shipped, build_working_view(shipped)
    ex = st.extraction
    ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, True, [answer["key"]]
    ex.llm_document_context = context

    class _P:
        id = "qizhong"

        def complete_structured(self, *, system, messages, response_schema, **_):
            return response_schema.model_validate({"answers": [answer]}), {}

    ctx.registry.register("llm", "qizhong", lambda: _P())
    st.llm.provider = "qizhong"
    LineItemLlmStage().run(doc, ctx)
    row = next((r for r in doc.line_items if r.canonical_key == answer["key"]), None)
    return row, ctx


def _by_period(row):
    return {ev.period_label: ev.value for ev in (row.values or {}).values()
            if ev.value is not None}


def _sum_of(*captions, signs=None):
    return {"key": FVTPL, "confidence": 0.9, "reason": "r", "role": "component",
            "signs": signs or [1] * len(captions),
            "sources": [{"note": "七、2", "caption": c} for c in captions]}


@pytest.mark.parametrize("context", ["full", "anywhere"])
def test_the_maijie_parent_and_its_breakdown_are_counted_once(shipped, context):
    row, ctx = _run(shipped, _doc([_maijie()]),
                    _sum_of(PARENT, "结构性理财产品", "权益工具投资"), context)
    assert _by_period(row) == {"current": Decimal("431478888.81")}
    refused = [m for m in ctx.logs if "citation REFUSED" in m]
    assert len(refused) == 2 and all("其中 breakdown" in m for m in refused), refused


def test_without_the_rule_the_same_answer_published_the_double_count(shipped, monkeypatch):
    """The control: 862,957,777.62, the figure the finding names."""
    monkeypatch.setattr(answer_checks, "refuse_breakdowns_of_cited_totals",
                        lambda e, index, **_k: (e, []))
    row, _ctx = _run(shipped, _doc([_maijie()]), _sum_of(PARENT, "结构性理财产品", "权益工具投资"))
    assert _by_period(row) == {"current": Decimal("862957777.62")}


def test_the_breakdown_alone_or_a_whole_answer_is_left_as_it_is(shipped):
    row, _ctx = _run(shipped, _doc([_maijie()]), _sum_of("结构性理财产品", "权益工具投资"))
    assert _by_period(row) == {"current": Decimal("431478888.81")}
    whole = dict(_sum_of(PARENT, "结构性理财产品"), role="whole")
    row, ctx = _run(shipped, _doc([_maijie()]), whole)
    assert _by_period(row) == {"current": Decimal("431478888.81")}
    assert not any("citation REFUSED" in m for m in ctx.logs)


def test_a_breakdown_row_subtracted_from_its_row_is_the_line_taking_it_out():
    table = _maijie()
    index = answer_checks.NoteIndex([table])
    entries = [{"at": i, "note": "七、2", "caption": r.raw_label,
                "figures": {"current": str(next(iter(r.values.values())).value)},
                "provenance": next(iter(r.values.values())).provenance.model_dump(mode="json")}
               for i, r in enumerate(table.items[:2])]
    assert answer_checks.refuse_breakdowns_of_cited_totals(
        entries, index, signs=[1, -1], component=True) == (entries, [])


def test_a_forward_in_the_breakdown_makes_the_breakdown_the_reading(shipped):
    """300319's prior year holds 其中：远期结售汇 565,202.29, a forward, inside 181,124,711.32. An
    answer citing the row and its whole breakdown loses the forward to the derivative rule; the
    breakdown is then the reading, not the row — 180,559,509.03, the rules' figure."""
    table = NotesTable(note_number="七、2", title="、交易性金融资产", items=[
        _row(PARENT, {"prior": "181124711.32"}, 0.30),
        _row("结构性理财产品", {"prior": "150000000.00"}, 0.32, group="其中："),
        _row("权益工具投资", {"prior": "30559509.03"}, 0.34, group="其中："),
        _row("远期结售汇", {"prior": "565202.29"}, 0.36, group="其中：")])
    row, ctx = _run(shipped, _doc([table]),
                    _sum_of(PARENT, "结构性理财产品", "权益工具投资", "远期结售汇"))
    assert _by_period(row) == {"prior": Decimal("180559509.03")}
    reasons = " ".join(m for m in ctx.logs if "citation REFUSED" in m)
    assert "excludes derivatives" in reasons and "the breakdown is the reading" in reasons
    # Cited without the rest of its breakdown, the row stands and the forward is not added.
    row, _ctx = _run(shipped, _doc([table]), _sum_of(PARENT, "远期结售汇"))
    assert _by_period(row) == {"prior": Decimal("181124711.32")}


def test_a_breakdown_printed_after_a_page_break_is_one_too(shipped):
    """301577 美信 七、2: the parent is the last row of page 158, its 其中 rows open page 159."""
    page_158 = NotesTable(note_number="七、2", title="、交易性金融资产", items=[
        _row(PARENT, {"current": "25020357.93"}, 0.89, page=157)])
    page_159 = NotesTable(note_number="七、2", title="、交易性金融资产", items=[
        _row("信托计划", {"current": "25020357.93"}, 0.107, group="其中：", page=158)])
    row, _ctx = _run(shipped, _doc([page_158, page_159]), _sum_of(PARENT, "信托计划"))
    assert _by_period(row) == {"current": Decimal("25020357.93")}


def test_chengzhis_second_parent_is_not_taken_for_a_breakdown(shipped):
    """诚志 七、2: the second parent row arrives inside the first's 其中 group. The first parent's
    breakdown is complete before it, so it is a parent of its own and an answer adding the two
    parents is left as it is (prior 200,023,277.01 + 550,468,453.20 = 750,491,730.21)."""
    table = NotesTable(note_number="七、2", title="、交易性金融资产", items=[
        _row(PARENT, {"prior": "200023277.01"}, 0.30),
        _row("其他", {"prior": "200023277.01"}, 0.32, group="其中："),
        _row("指定以公允价值计量且其变动计入当期损益的金融资产", {"prior": "550468453.20"}, 0.34,
             group="其中："),
        _row("其他", {"prior": "550468453.20"}, 0.36, group="其中：")])
    row, _ctx = _run(shipped, _doc([table]), _sum_of(
        PARENT, "指定以公允价值计量且其变动计入当期损益的金融资产"))
    assert _by_period(row) == {"prior": Decimal("750491730.21")}
