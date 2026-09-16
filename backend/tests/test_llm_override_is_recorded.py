"""A MODEL THAT REPLACES A PRINTED FIGURE MUST SAY SO — the three things it used to do in silence.

NEW FILE -> backend/tests/test_llm_override_is_recorded.py

WHAT THIS COSTS WHEN IT IS WRONG, measured with a spy provider before the fix. A face row matched
by exact caption at confidence 1.0, holding 9999 with its provenance on page 3, plus a note row
stating 280,961 that the model cites. The published result was 280,961 — the right answer under the
product decision that the model confirms or overrides — delivered like this:

    method: llm   mapping: 0.9
      value: 280961   page: 3          <-- the FACE page, for a figure off the note page
      flags: ['line_item_llm:1 cited row(s)', 'llm_reason:...']

Three separate silences in one result:

  * THE 9999 WAS GONE with nothing recording it had ever been there. `note_sourced._write`
    assigned `value` and `value_raw` in place, so the printed figure left no trace in the value, in
    a flag, or in the trail. The prose path had already solved this for itself
    (`_write_prose` appends `prose_value_displaced_printed:`) and the parent rollup had solved it
    the other way (`note_sourced_differs_from_printed:`, printed kept) — the row path had neither.
  * THE PROVENANCE POINTED AT THE WRONG PAGE. Only the two value fields were assigned, so
    `provenance` still described the face row: click-to-source highlighted the statement while the
    number came off a note, and the `derivation` trail — which does carry the cited note and its
    own page — disagreed with the value beside it. This is the defect a reviewer cannot detect by
    reading the screen, because the screen looks consistent.
  * THE DETERMINISTIC CONFIDENCE VANISHED. 1.0 became the model's 0.9 with nothing saying it had
    been higher, so "this row used to be an exact caption match" was recoverable only by diffing
    two runs.

WHY THE MODEL STILL WINS. That is the decision, not an accident: the deterministic route proposes
and the model overrides. These tests pin that the override is RECORDED, never that it is refused —
asserting the figure stays 9999 would pin the opposite product.
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

SUB = "sub__pbt_oper_exp_depreciation"
FACE_PAGE, NOTE_PAGE = 3, 88
PRINTED, CITED = Decimal("9999"), Decimal("280961")
CITE = [{"note": "7", "caption": "Depreciation of right-of-use assets"}]


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _note_row(caption: str, amount: str) -> NoteItem:
    row = NoteItem(raw_label=caption)
    row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=NOTE_PAGE))
    return row


def _notes() -> list[NotesTable]:
    return [NotesTable(note_number="7", title="LOSS FROM OPERATING ACTIVITIES",
                       items=[_note_row("Depreciation of right-of-use assets^", str(CITED))])]


def _face_row() -> LineItem:
    """The row as the deterministic mapper leaves it: exact caption, full confidence, own page."""
    row = LineItem(source_label="Depreciation", canonical_key=SUB)
    row.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=PRINTED, value_raw=PRINTED,
                                     provenance=Provenance(page_index=FACE_PAGE))
    row.confidence.method = "exact"
    row.confidence.mapping = 1.0
    return row


class _Answers:
    id = "answers"

    def __init__(self, answers):
        self.answers = answers

    def complete_structured(self, *, system, messages, response_schema, **_):
        return response_schema.model_validate({"answers": self.answers}), {}


def _run(shipped, *, face: LineItem | None):
    doc = DocumentModel(filename="f.pdf")
    doc.notes = _notes()
    doc.line_items = [face] if face is not None else []
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [SUB]
    provider = _Answers([{"key": SUB, "confidence": 0.9, "reason": "note 7 states it",
                          "sources": CITE}])
    ctx.registry.register("llm", "answers", lambda: provider)
    ctx.settings.llm.provider = "answers"
    LineItemLlmStage().run(doc, ctx)
    row = next(r for r in doc.line_items if r.canonical_key == SUB)
    slot = next(iter(row.values.values()))
    return row, slot


def test_the_model_still_wins_the_figure(shipped):
    """THE PRODUCT DECISION, pinned first so the rest reads as being about the record and not the
    outcome. Deterministic proposes; the model overrides."""
    _row, slot = _run(shipped, face=_face_row())
    assert slot.value == CITED, "the model's cited figure should publish"


def test_the_displaced_printed_figure_is_named(shipped):
    """DEFECT ONE. The printed figure the override replaced, on the row where a reviewer looks."""
    row, _slot = _run(shipped, face=_face_row())
    assert f"line_item_llm_displaced_printed:{PRINTED}" in row.confidence.flags, (
        f"nothing records that {PRINTED} was displaced; flags={row.confidence.flags}")


def test_the_provenance_follows_the_figure_to_the_note(shipped):
    """DEFECT TWO, and the one a reviewer cannot catch by eye: the number came off the note page, so
    click-to-source must not still point at the statement."""
    _row, slot = _run(shipped, face=_face_row())
    assert slot.provenance is not None, "a provenance must never be cleared — see `_write`"
    assert slot.provenance.page_index == NOTE_PAGE, (
        f"provenance still describes the displaced figure (page {slot.provenance.page_index})")


def test_the_superseded_deterministic_confidence_is_named(shipped):
    """DEFECT THREE. 1.0 became 0.9; that it had been an exact match at 1.0 must survive."""
    row, _slot = _run(shipped, face=_face_row())
    assert row.confidence.mapping == pytest.approx(0.9), "the model's own score still stamps"
    assert any(f.startswith("llm_superseded_exact:") for f in row.confidence.flags), (
        f"nothing records the exact 1.00 it replaced; flags={row.confidence.flags}")


def test_an_unopposed_answer_records_no_displacement(shipped):
    """THE GUARD AGAINST A FLAG THAT ALWAYS FIRES. With no printed figure to displace there is
    nothing to report, and a displacement flag on every answered line would be noise that teaches
    a reviewer to ignore it."""
    row, slot = _run(shipped, face=None)
    assert slot.value == CITED
    assert not [f for f in row.confidence.flags if "displaced_printed" in f], row.confidence.flags
    assert not [f for f in row.confidence.flags if f.startswith("llm_superseded_")], (
        row.confidence.flags)


# ── AND THE OTHER HALF: THE MODEL OFFERS NO ALTERNATIVE ──────────────────────────────────────
#
# THE PRODUCT DECISION these pin: the printed number is kept where the model suggests no
# alternative. It always was kept — no figure is written — but the row it sat on was taken over by
# the answer that failed to locate it, which cost the figure its confidence AND silenced the one
# route that might still have filled the line. See `_write_unanswered`.

_NO_ALTERNATIVE = [{"note": "7", "caption": "a caption that is in no extracted row"}]


def _run_unanswered(shipped, *, face: LineItem | None, sources=_NO_ALTERNATIVE, confidence=0.1):
    doc = DocumentModel(filename="f.pdf")
    doc.notes = _notes()
    doc.line_items = [face] if face is not None else []
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [SUB]
    provider = _Answers([{"key": SUB, "confidence": confidence,
                          "reason": "not stated in the supplied notes", "sources": sources}])
    ctx.registry.register("llm", "answers", lambda: provider)
    ctx.settings.llm.provider = "answers"
    LineItemLlmStage().run(doc, ctx)
    row = next(r for r in doc.line_items if r.canonical_key == SUB)
    return row, next(iter(row.values.values()), None)


def test_the_printed_figure_is_kept_when_the_model_offers_no_alternative(shipped):
    _row, slot = _run_unanswered(shipped, face=_face_row())
    assert slot is not None and slot.value == PRINTED, "the filing's own figure must survive"
    assert slot.provenance.page_index == FACE_PAGE, "and keep its own page"


def test_a_non_answer_does_not_destroy_the_deterministic_confidence(shipped):
    """The defect: `exact` at 1.00 became `llm` at the model's own score for a citation that never
    resolved, so the printed figure sorted to the bottom of a confidence-ordered review."""
    row, _slot = _run_unanswered(shipped, face=_face_row())
    assert row.confidence.method == "exact", f"method was taken over: {row.confidence.method}"
    assert row.confidence.mapping == pytest.approx(1.0), row.confidence.mapping
    assert "llm_located_nothing_kept_exact:1.00" in row.confidence.flags, row.confidence.flags


def test_a_non_answer_does_not_stand_the_deterministic_route_down(shipped):
    """The worse defect. `note_sourced._llm_holds` is true when the method ends in `llm` and the
    slot holds a value — a face row's exact situation — so a model that located nothing silenced
    the declared note route, the one thing that might still have filled the line."""
    from app.stages.note_sourced import _llm_holds
    row, _slot = _run_unanswered(shipped, face=_face_row())
    assert not _llm_holds(row, "consolidated", "current"), (
        "the note route is deferring to an answer that located nothing")


def test_the_non_answer_is_still_recorded(shipped):
    """Keeping the deterministic stamp must not cost the reviewer the model's account of itself —
    that record is the whole reason `_write_unanswered` exists."""
    row, _slot = _run_unanswered(shipped, face=_face_row())
    flags = " ".join(row.confidence.flags)
    assert "llm_answered_nothing_located:" in flags, row.confidence.flags
    assert "llm_reason:not stated in the supplied notes" in row.confidence.flags
    assert "llm_citation_unresolved:" in flags, row.confidence.flags


def test_a_line_with_no_deterministic_answer_is_still_claimed(shipped):
    """The guard the other way: with nothing to protect, "asked and found nothing" is the only
    thing known about the line, so the model keeps the row and says so."""
    row, slot = _run_unanswered(shipped, face=None)
    assert slot is None, "no figure should be invented"
    assert row.confidence.method == "llm", row.confidence.method
    assert not [f for f in row.confidence.flags if f.startswith("llm_located_nothing_kept_")]
