"""A CITED FIGURE IS WRITTEN INTO THE ROW THAT PRINTS ITS BASIS, AND IT IS THE LINE'S WHOLE FIGURE.

A mainland filing prints the group's statements and the company's own, and the deterministic
reader binds one face row of each to the same line. `stages.line_item_llm` kept only the LAST row
per line (`by_concept`), which is the company's, so a model's consolidated figure was written there
— beside the consolidated face row's own figure, which nothing stood down because that rule was
switched on for Indian filings only. Both were counted: measured on 000883 with stand-in answers,
net fixed assets 140.8bn against 75.6bn, and 税金及附加 published at exactly twice the printed
193,625,794.60.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable, Provenance)
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.working_view import build_working_view
from app.stages.line_item_llm import LineItemLlmStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
SUB = "sub__pbt_depreciation"          # asked about, and answered from a note TABLE row
GROUP_FACE, COMPANY_FACE, CITED = Decimal("9999"), Decimal("5555"), Decimal("280961")


@pytest.fixture(autouse=True)
def _restore_extraction_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = was


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _face(label: str, basis: Basis, amount: Decimal, page: int) -> LineItem:
    row = LineItem(source_label=label, canonical_key=SUB)
    row.values["v"] = ExtractedValue(basis=basis, period_label="current", value=amount,
                                     value_raw=amount, provenance=Provenance(page_index=page))
    row.confidence.method = "exact"
    row.confidence.mapping = 1.0
    return row


class _Answers:
    id = "answers"

    def complete_structured(self, *, system, messages, response_schema, **_):
        return response_schema.model_validate({"answers": [
            {"key": SUB, "confidence": 0.9, "reason": "note 7 states it",
             "sources": [{"note": "7", "caption": "Depreciation of right-of-use assets"}]}]}), {}


def _run(shipped, rows: list[LineItem]) -> DocumentModel:
    note_row = NoteItem(raw_label="Depreciation of right-of-use assets")
    note_row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                          value=CITED, value_raw=CITED,
                                          provenance=Provenance(page_index=88))
    doc = DocumentModel(filename="f.pdf")
    doc.notes = [NotesTable(note_number="7", title="LOSS FROM OPERATING ACTIVITIES",
                            items=[note_row])]
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    assert not getattr(ctx, "indian_filing", False), "this is the mainland case"
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [SUB]
    ctx.registry.register("llm", "answers", lambda: _Answers())
    ctx.settings.llm.provider = "answers"
    LineItemLlmStage().run(doc, ctx)
    return doc


def _figures(doc, basis: str) -> list[Decimal]:
    return [ev.value for r in doc.line_items if r.canonical_key == SUB
            for ev in r.values.values()
            if str(getattr(ev.basis, "value", ev.basis)) == basis and ev.period_label == "current"]


def test_the_group_figure_goes_on_the_group_row_not_the_companys(shipped):
    group = _face("Depreciation", Basis.CONSOLIDATED, GROUP_FACE, 3)
    company = _face("Depreciation", Basis.STANDALONE, COMPANY_FACE, 9)
    doc = _run(shipped, [group, company])          # the company's row comes last
    assert _figures(doc, "consolidated") == [CITED], "the cited figure is the line's only one"
    assert next(iter(group.values.values())).value == CITED, "written into the group's row"
    assert _figures(doc, "standalone") == [COMPANY_FACE], "the company's figure is untouched"


def test_a_second_row_of_the_same_basis_is_stood_down_on_a_mainland_filing(shipped):
    first = _face("Depreciation", Basis.CONSOLIDATED, GROUP_FACE, 3)
    stray = _face("Depreciation (continued)", Basis.CONSOLIDATED, Decimal("1234"), 4)
    doc = _run(shipped, [first, stray])
    assert _figures(doc, "consolidated") == [CITED]
    assert "llm_superseded_by_cited_figure:consolidated:current" in (
        first.confidence.flags + stray.confidence.flags)
