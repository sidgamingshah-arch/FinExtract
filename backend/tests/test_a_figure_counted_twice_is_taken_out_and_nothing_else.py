"""WHERE A MODEL'S ANSWERS COUNT ONE PRINTED FIGURE TWICE, ONLY THE FIGURE COUNTED TWICE IS TAKEN OUT
(`services.answer_checks.refuse_repeated_figures`, `settle_siblings`).

Each check that keeps one printed figure from being counted twice was taking out MORE than the
figure counted twice:

  * TWO PARTS SHARING SOME OF THEIR CELLS. A footprint holds every filed cell a citation counts.
    The FVTPL part citing only note 26's 'Unlisted investments' PRIOR column (800,000) and the
    other-financial-assets part citing the WHOLE row (927,258 / 800,000) share the prior cell — and
    the other part was emptied in full, its current-year 927,258 with it, which no part then held.
    A part is now withheld only when every cell it counts is one its sibling counts; where it
    shares some and not all, both parts go to review.
  * ONE FACE ROW CITED ONCE PER COLUMN, OR ONCE PER ENTITY. A face citation was marked by its row
    alone, so "Trade receivables, current" and "Trade receivables, prior" were "the same printed
    figure", and the prior year was refused; a row printing Group 500 and Company 60, cited once
    per entity, lost the Company's 60 before the answer was split by entity. A face citation is
    now marked by its row AND the basis and period it counts.
  * A WHOLE ROW AFTER ONE OF ITS COLUMNS. A sum citing 'Bank loans' prior (400) and then the whole
    row (500 / 400) had the whole row refused, and with it the current 500 nothing else gave. Now
    only the cells already counted are taken off the later citation, which keeps the rest.
  * FIND 1 AND THE LEVEL 3 IT DEDUCTS. Step 1 of `settle_siblings` (the same printed figure)
    compared every two parts of one parent, so FVTPL and the Level 3 part Securities (CP)
    SUBTRACTS, citing one cell, had the Level 3 deduction emptied. Two parts the parent adds with
    opposite signs are not compared in either step.

No model is called: the line-item stage is driven by a scripted provider that returns a fixed
answer, which shows what the checks do with that answer, not what a model would answer.
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
from app.services.line_item_llm import split_by_entity
from app.services.mapping import SourceRef
from app.services.note_sourced import cell_of, resolve_sources
from app.services.working_view import build_working_view
from app.stages.line_item_llm import LineItemLlmStage

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")
FVTPL = "sub__fa_cp_fvtpl_note_total"
OTHER = "sub__fa_cp_other_fincl_assets_note_total"
DEBT = "sub__fa_cp_debt_investments_note_total"
L3 = "sub__fa_cp_level_3_total"
L3_PRINTED = "sub__fa_cp_level_3_printed_total"


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


def _note_row(caption, figures, *, number="26", page=40, y=0.40):
    row = NoteItem(raw_label=caption, note_number=number)
    for n, (period, amount) in enumerate(figures.items()):
        row.set_value(ExtractedValue(
            value=Decimal(amount), basis=Basis.CONSOLIDATED, period_label=period,
            provenance=Provenance(source_kind="native", page_index=page,
                                  bbox=BBox(x0=0.55 + 0.25 * n, y0=y, x1=0.65 + 0.25 * n,
                                            y1=y + 0.012))))
    return row


def _unlisted():
    row = _note_row("Unlisted investments", {"current": "927258", "prior": "800000"})
    return row, NotesTable(note_number="26", title="FINANCIAL ASSETS AT FAIR VALUE THROUGH PROFIT "
                                                   "OR LOSS", items=[row])


def _cells(row, *periods):
    return frozenset(cell_of(ev.provenance) for ev in row.values.values()
                     if not periods or ev.period_label in periods)


# ── two parts of one parent ─────────────────────────────────────────────────────────────────

def test_a_part_sharing_some_of_its_cells_is_flagged_not_emptied(shipped):
    row, table = _unlisted()
    got = answer_checks.settle_siblings(
        {FVTPL: [_cells(row, "prior")], OTHER: [_cells(row)]}, shipped.items,
        answer_checks.NoteIndex([table]))
    assert [(s.kind, s.action, s.owner, s.other) for s in got] == [
        ("same_figure", "flag", FVTPL, OTHER)]


def test_a_part_whose_every_cell_its_sibling_counts_is_still_withheld(shipped):
    row, table = _unlisted()
    index = answer_checks.NoteIndex([table])
    got = answer_checks.settle_siblings(
        {FVTPL: [_cells(row)], OTHER: [_cells(row, "prior")]}, shipped.items, index)
    assert [(s.kind, s.action, s.owner, s.other) for s in got] == [
        ("same_figure", "withhold", FVTPL, OTHER)]
    # …over two citations as well: each of its cells is one the sibling counts.
    got = answer_checks.settle_siblings(
        {FVTPL: [_cells(row, "current"), _cells(row, "prior")],
         OTHER: [_cells(row, "current"), _cells(row, "prior")]}, shipped.items, index)
    assert [(s.kind, s.action, s.owner, s.other) for s in got] == [
        ("same_figure", "withhold", FVTPL, OTHER)]


def _doc(notes):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=3, kind=PageKind.FACE, statement="balance_sheet")]
    doc.notes = list(notes)
    li = LineItem(source_label="Profit before tax", canonical_key="pl_profit_before_tax")
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal("1000"), provenance=Provenance(page_index=3))
    doc.line_items = [li]
    return doc


def _run(shipped, doc, answers: dict):
    st = get_settings()
    ctx = PipelineContext(raw_bytes=b"", settings=st)
    ctx.line_items, ctx.ontology = shipped, build_working_view(shipped)
    ex = st.extraction
    ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, True, list(answers)
    ex.llm_document_context = "full"

    class _Scripted:
        id = "scripted"

        def complete_structured(self, *, system, messages, response_schema, **_):
            asked = [e["key"] for e in json.loads(messages[-1]["content"])["line_items"]]
            return response_schema.model_validate(
                {"answers": [answers[k] for k in asked if k in answers]}), {}

    ctx.registry.register("llm", "scripted", lambda: _Scripted())
    st.llm.provider = "scripted"
    LineItemLlmStage().run(doc, ctx)
    return {r.canonical_key: r for r in doc.line_items if r.canonical_key in answers}, ctx


def test_the_stage_keeps_the_current_year_only_the_wider_part_holds(shipped):
    _row, table = _unlisted()
    rows, ctx = _run(shipped, _doc([table]), {
        FVTPL: {"key": FVTPL, "confidence": 0.9, "reason": "r", "sources": [
            {"note": "26", "caption": "Unlisted investments", "column": "prior"}]},
        OTHER: {"key": OTHER, "confidence": 0.9, "reason": "r", "sources": [
            {"note": "26", "caption": "Unlisted investments"}]}})
    assert {ev.period_label: ev.value for ev in rows[OTHER].values.values()} == {
        "current": Decimal("927258"), "prior": Decimal("800000")}
    for key, other in ((FVTPL, OTHER), (OTHER, FVTPL)):
        assert f"llm_note_rows_overlap:{other}" in rows[key].confidence.flags
        assert "low_mapping_confidence" in rows[key].confidence.flags
    assert not any(f.startswith("llm_note_row_claimed_by") for f in rows[OTHER].confidence.flags)
    assert not any("left empty" in m for m in ctx.logs)


def test_find_1_and_the_level_3_it_deducts_citing_one_cell_are_not_compared(shipped):
    """The parent ADDS FVTPL and SUBTRACTS Level 3: one printed figure in both is the rung taking
    a Level 3 holding out, not a double count — whichever Level 3 part holds it."""
    row = _note_row("Unlisted investments (Level 3)", {"current": "927258", "prior": "800000"})
    index = answer_checks.NoteIndex([NotesTable(note_number="26", title="FAIR VALUE", items=[row])])
    for level_3 in (L3, L3_PRINTED):
        assert answer_checks.settle_siblings(
            {FVTPL: [_cells(row)], level_3: [_cells(row)]}, shipped.items, index) == []
    # Two parts the parent adds, citing that cell, are still settled.
    got = answer_checks.settle_siblings(
        {FVTPL: [_cells(row)], DEBT: [_cells(row)], L3: [_cells(row)]}, shipped.items, index)
    assert [(s.kind, s.action, s.owner, s.other) for s in got] == [
        ("same_figure", "withhold", FVTPL, DEBT)]


# ── one answer ──────────────────────────────────────────────────────────────────────────────

def _face(caption, slots):
    li = LineItem(source_label=caption)
    for n, (basis, period, value) in enumerate(slots):
        li.values[f"{basis}:{period}"] = ExtractedValue(
            value=Decimal(value), basis=basis, period_label=period,
            provenance=Provenance(source_kind="native", page_index=3,
                                  bbox=BBox(x0=0.5 + 0.1 * n, y0=0.3, x1=0.55 + 0.1 * n, y1=0.31)))
    return [("balance_sheet", caption, li)]


def _repeated(resolved, notes=()):
    return answer_checks.refuse_repeated_figures(
        resolved, answer_checks.NoteIndex(list(notes)), signs=[1] * len(resolved),
        component=True)


def test_one_face_row_cited_once_per_column_keeps_both_years():
    face = _face("Trade receivables", [(Basis.CONSOLIDATED, "current", "100"),
                                       (Basis.CONSOLIDATED, "prior", "80")])
    resolved, _ = resolve_sources(
        [SourceRef(statement="balance_sheet", caption="Trade receivables", column="current"),
         SourceRef(statement="balance_sheet", caption="Trade receivables", column="prior")],
        [], face)
    kept, refused = _repeated(resolved)
    assert [e["figures"] for e in kept] == [{"current": "100"}, {"prior": "80"}]
    assert refused == []


def test_one_face_row_cited_once_per_entity_keeps_both_entities():
    face = _face("Cash and bank balances", [(Basis.CONSOLIDATED, "current", "500"),
                                            (Basis.STANDALONE, "current", "60")])
    resolved, _ = resolve_sources(
        [SourceRef(statement="balance_sheet", caption="Cash and bank balances",
                   entity="consolidated"),
         SourceRef(statement="balance_sheet", caption="Cash and bank balances",
                   entity="standalone")], [], face)
    kept, refused = _repeated(resolved)
    assert refused == []
    assert [[e["figures"] for e in g[0]] for g in split_by_entity(kept, refused)] == [
        [{"current": "500"}], [{"current": "60"}]]


def test_one_face_figure_cited_twice_is_still_refused():
    face = _face("Trade receivables", [(Basis.CONSOLIDATED, "current", "100")])
    resolved, _ = resolve_sources(
        [SourceRef(statement="balance_sheet", caption="Trade receivables"),
         SourceRef(statement="balance_sheet", caption="Trade receivables")], [], face)
    kept, refused = _repeated(resolved)
    assert [e["figures"] for e in kept] == [{"current": "100"}]
    assert [r["refused"] for r in refused] == ["same_figure_twice"]


def test_a_whole_row_after_one_of_its_columns_keeps_the_other_column():
    row = _note_row("Bank loans", {"current": "500", "prior": "400"}, number="12")
    table = NotesTable(note_number="12", title="BORROWINGS", items=[row])
    resolved, _ = resolve_sources(
        [SourceRef(note="12", caption="Bank loans", column="prior"),
         SourceRef(note="12", caption="Bank loans")], [table])
    kept, refused = _repeated(resolved, [table])
    assert [e["figures"] for e in kept] == [{"prior": "400"}, {"current": "500"}]
    assert refused == []
    assert kept[1]["repeated_dropped"] == ["prior"]
    # The trimmed citation counts only what it kept, so a sibling check sees no prior cell in it.
    index = answer_checks.NoteIndex([table])
    assert answer_checks.footprint(kept[1], index) == _cells(row, "current")
    # check_answer says what it took off.
    checked = answer_checks.check_answer(None, resolved, doc=DocumentModel(), signs=[1, 1],
                                         component=True, key="k", index=index)
    assert any("prior" in line and "already" in line for line in checked.log)


def test_a_row_cited_twice_whole_is_still_refused():
    row = _note_row("Bank loans", {"current": "500", "prior": "400"}, number="12")
    table = NotesTable(note_number="12", title="BORROWINGS", items=[row])
    resolved, _ = resolve_sources(
        [SourceRef(note="12", caption="Bank loans"), SourceRef(note="12", caption="Bank loans")],
        [table])
    kept, refused = _repeated(resolved, [table])
    assert len(kept) == 1 and [r["refused"] for r in refused] == ["same_figure_twice"]


def test_two_cells_of_one_face_row_named_by_the_route_are_two_printed_figures():
    """The whole-PDF route names the face cell each figure is on (`cells`): 1966's statement of
    changes in equity prints its closing row 'At 31 December 2023' with one figure per reserve,
    and a sum of two of them is two printed figures — while the same cell twice is one."""
    def face(at, cell, amount):
        return {"at": at, "on_face": True, "row_id": "r1", "basis": "consolidated",
                "figures": {"current": amount}, "cells": [cell], "caption": "At 31 December 2023"}
    a, b = (107, 0.366, 0.4), (107, 0.416, 0.4)
    kept, refused = _repeated([face(0, a, "5130954"), face(1, b, "1883822")])
    assert len(kept) == 2 and refused == []
    kept, refused = _repeated([face(0, a, "5130954"), face(1, a, "5130954")])
    assert len(kept) == 1 and [r["refused"] for r in refused] == ["same_figure_twice"]
