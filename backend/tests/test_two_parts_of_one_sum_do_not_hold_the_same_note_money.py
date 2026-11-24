"""Two parts that one parent ADDS do not both hold the same note money.

The finding (securities_level3:find1_siblings_double_count_full). Securities (CP) is Find 1 − Find 3,
and Find 1 adds six parts. Under `llm_document_context = "full"` any part may cite any note, and
the only settlement between siblings was for two parts citing the very same printed CELL — the
first cell of each citation. So:

  * a part citing a note row's prior column beside a sibling citing the whole row held the same
    prior-year money twice;
  * 688008 七、2 prints 结构性存款 1,629,362,905.47 as a 其中 row inside 交易性金融资产
    1,783,494,750.68: the FVTPL part citing the row and the other-financial-assets part citing the
    structured deposits would make Securities (CP) 3,412,857,656.15.

`services.answer_checks.settle_siblings` decides, and `LineItemLlmStage._settle_shared_note_rows`
applies: the same printed figure goes to one part (the one whose note claim covers it, as the
rules' claim groups give it, else the one declared first); a 其中 row whose parent row a sibling
the parent adds already holds is withheld; and where two such parts may hold the same money but
nothing can say safely which — another column of one row, a row above a total the sibling holds —
both go to review. Two parts the parent SUBTRACTS one from the other (Find 1 and the Level 3 it
deducts) are never compared: one column of a row taken away from another is the rule.
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
from app.services.line_item_notes import claimed_notes
from app.services.working_view import build_working_view
from app.stages.line_item_llm import LineItemLlmStage

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")
CP = "bs_ca__secur_and_other_fincl_assets_cp"
FVTPL = "sub__fa_cp_fvtpl_note_total"
OTHER = "sub__fa_cp_other_fincl_assets_note_total"
LEVEL3 = "sub__fa_cp_level_3_total"
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


def _row(caption, figures, y, *, group="", number="七、2", page=181):
    row = NoteItem(raw_label=caption, note_number=number, group_hint=group)
    for n, (period, amount) in enumerate(figures.items()):
        row.set_value(ExtractedValue(
            value=Decimal(amount), basis=Basis.CONSOLIDATED, period_label=period,
            provenance=Provenance(source_kind="native", page_index=page,
                                  bbox=BBox(x0=0.55 + 0.25 * n, y0=y, x1=0.65 + 0.25 * n,
                                            y1=y + 0.012))))
    return row


def _montage():
    """688008's 七、2 (current year)."""
    return NotesTable(note_number="七、2", title="、交易性金融资产", items=[
        _row(PARENT, {"current": "1783494750.68"}, 0.30),
        _row("结构性存款", {"current": "1629362905.47"}, 0.32, group="其中："),
        _row("权益工具投资", {"current": "154131845.21"}, 0.34, group="其中："),
        _row("合计", {"current": "1783494750.68"}, 0.36)])


def _cells(row, *periods):
    return frozenset(answer_checks.note_sourced.cell_of(ev.provenance)
                     for ev in row.values.values() if not periods or ev.period_label in periods)


# ── the decisions ──────────────────────────────────────────────────────────────────────────────

def test_a_sibling_citing_a_rows_prior_column_beside_the_whole_row_is_withheld(shipped):
    """The whole row counts its current AND its prior; the sibling's citation of the prior column
    is the same printed figure, though not the first cell of either citation."""
    row = _row("Unlisted investments", {"current": "927258", "prior": "800000"}, 0.40,
               number="26")
    table = NotesTable(note_number="26", title="FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT OR "
                                               "LOSS", items=[row])
    index = answer_checks.NoteIndex([table])
    got = answer_checks.settle_siblings({FVTPL: [_cells(row)], OTHER: [_cells(row, "prior")]},
                                        shipped.items, index)
    assert [(s.kind, s.action, s.owner, s.other) for s in got] == [
        ("same_figure", "withhold", FVTPL, OTHER)]


def test_a_qizhong_row_held_beside_its_parent_row_is_withheld(shipped):
    table = _montage()
    index = answer_checks.NoteIndex([table])
    parent, deposits = table.items[0], table.items[1]
    claims = claimed_notes([i for i in shipped.items if getattr(i, "note_source", None)], [table])
    got = answer_checks.settle_siblings({FVTPL: [_cells(parent)], OTHER: [_cells(deposits)]},
                                        shipped.items, index, claims=claims)
    assert [(s.kind, s.action, s.parent, s.owner, s.other) for s in got] == [
        ("breakdown", "withhold", CP, FVTPL, OTHER)]


def test_a_qizhong_row_beside_rows_of_its_own_is_flagged_not_emptied(shipped):
    table = _montage()
    other_note = NotesTable(note_number="七、11", title="、其他非流动金融资产",
                            items=[_row("银行理财产品", {"current": "5000000.00"}, 0.50,
                                        number="七、11", page=190)])
    index = answer_checks.NoteIndex([table, other_note])
    got = answer_checks.settle_siblings(
        {FVTPL: [_cells(table.items[0])],
         OTHER: [_cells(table.items[1]), _cells(other_note.items[0])]}, shipped.items, index)
    assert [(s.kind, s.action) for s in got] == [("breakdown", "flag")]


def test_a_row_above_the_total_a_sibling_holds_is_flagged(shipped):
    """FVTPL cites the note's 合计 and the other part the 结构性存款 row: the money may be counted
    twice, but which part should give it up is not safe to say — both go to review."""
    table = _montage()
    index = answer_checks.NoteIndex([table])
    got = answer_checks.settle_siblings(
        {FVTPL: [_cells(table.items[3])], OTHER: [_cells(table.items[1])]}, shipped.items, index)
    assert [(s.kind, s.action, s.owner, s.other) for s in got] == [
        ("inside_total", "flag", FVTPL, OTHER)]


def test_another_column_of_one_row_is_flagged(shipped):
    row = _row("Listed equity securities", {"current": "100", "current:allowance": "10"}, 0.40,
               number="26")
    table = NotesTable(note_number="26", title="FINANCIAL ASSETS", items=[row])
    index = answer_checks.NoteIndex([table])
    got = answer_checks.settle_siblings(
        {FVTPL: [_cells(row, "current")], OTHER: [_cells(row, "current:allowance")]},
        shipped.items, index)
    assert [(s.kind, s.action) for s in got] == [("same_row", "flag")]


def test_find_1_and_the_level_3_it_deducts_are_never_compared(shipped):
    """A fair-value hierarchy row's total column and its Level 3 column: FVTPL adds one, Level 3
    is deducted — the rung subtracts them, which is the rule, not a double count."""
    row = _row("交易性金融资产", {"current": "1783494750.68", "current:level_3": "0.00"}, 0.60,
               number="十三、1", page=239)
    table = NotesTable(note_number="十三、1", title="、以公允价值计量的资产和负债的期末公允价值",
                       items=[row])
    index = answer_checks.NoteIndex([table])
    got = answer_checks.settle_siblings(
        {FVTPL: [_cells(row, "current")], LEVEL3: [_cells(row, "current:level_3")]},
        shipped.items, index)
    assert got == []
    # …AND CITING THE VERY SAME CELL, which is what "never compared" has to mean: step 1 (the
    # same printed figure) used to group the two by their shared parent and empty the Level 3.
    for level_3 in (LEVEL3, "sub__fa_cp_level_3_printed_total", "sub__fa_cp_level_3_rows"):
        got = answer_checks.settle_siblings(
            {FVTPL: [_cells(row, "current")], level_3: [_cells(row, "current")]},
            shipped.items, index)
        assert got == [], level_3


# ── the stage ─────────────────────────────────────────────────────────────────────────────────

def _doc(notes):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=3, kind=PageKind.FACE, statement="balance_sheet")]
    doc.notes = list(notes)
    li = LineItem(source_label="Profit before tax", canonical_key="pl_profit_before_tax")
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal("1000"), provenance=Provenance(page_index=3))
    doc.line_items = [li]
    return doc


def _run(shipped, doc, answers: dict, context="full"):
    st = get_settings()
    ctx = PipelineContext(raw_bytes=b"", settings=st)
    ctx.line_items, ctx.ontology = shipped, build_working_view(shipped)
    ex = st.extraction
    ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, True, list(answers)
    ex.llm_document_context = context

    class _P:
        id = "siblings"

        def complete_structured(self, *, system, messages, response_schema, **_):
            asked = [e["key"] for e in json.loads(messages[-1]["content"])["line_items"]]
            return response_schema.model_validate(
                {"answers": [answers[k] for k in asked if k in answers]}), {}

    ctx.registry.register("llm", "siblings", lambda: _P())
    st.llm.provider = "siblings"
    LineItemLlmStage().run(doc, ctx)
    return {r.canonical_key: r for r in doc.line_items if r.canonical_key in answers}, ctx


def _answer(key, caption, note="七、2"):
    return {"key": key, "confidence": 0.9, "reason": "r",
            "sources": [{"note": note, "caption": caption}]}


def test_the_stage_withholds_the_structured_deposits_from_the_other_part(shipped):
    rows, ctx = _run(shipped, _doc([_montage()]),
                     {FVTPL: _answer(FVTPL, PARENT), OTHER: _answer(OTHER, "结构性存款")})
    assert [ev.value for ev in rows[FVTPL].values.values()] == [Decimal("1783494750.68")]
    assert rows[OTHER].values == {}
    assert f"llm_note_row_claimed_by:{FVTPL}" in rows[OTHER].confidence.flags
    assert f"llm_note_row_breakdown_of:{FVTPL}" in rows[OTHER].confidence.flags
    assert "llm_note_row_withheld:1629362905.47" in rows[OTHER].confidence.flags
    assert any(f"{OTHER}: note 七、2 row '结构性存款' is a 其中 breakdown" in m for m in ctx.logs)


def test_the_stage_flags_both_parts_where_the_total_and_a_row_of_it_are_held(shipped):
    rows, ctx = _run(shipped, _doc([_montage()]),
                     {FVTPL: _answer(FVTPL, "合计"), OTHER: _answer(OTHER, "结构性存款")})
    for key, other in ((FVTPL, OTHER), (OTHER, FVTPL)):
        assert rows[key].values, key
        assert f"llm_note_rows_overlap:{other}" in rows[key].confidence.flags
        assert "low_mapping_confidence" in rows[key].confidence.flags
    assert any("both go to review" in m for m in ctx.logs)


def test_two_parts_reading_different_notes_are_left_alone(shipped):
    other_note = NotesTable(note_number="七、11", title="、其他非流动金融资产",
                            items=[_row("结构性存款", {"current": "5000000.00"}, 0.50,
                                        number="七、11", page=190)])
    rows, _ctx = _run(shipped, _doc([_montage(), other_note]),
                      {FVTPL: _answer(FVTPL, PARENT), OTHER: _answer(OTHER, "结构性存款", "七、11")})
    assert [ev.value for ev in rows[OTHER].values.values()] == [Decimal("5000000.00")]
    assert not any(f.startswith(("llm_note_row", "llm_note_rows_overlap"))
                   for f in rows[OTHER].confidence.flags + rows[FVTPL].confidence.flags)
