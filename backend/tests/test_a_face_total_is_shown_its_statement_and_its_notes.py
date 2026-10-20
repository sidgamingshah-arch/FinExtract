"""A LINE WITH A NOTE SET STILL SEES ITS STATEMENT, AND EVERY NOTE IT IS TOLD ABOUT IS SENT.

Measured on 000883 before either fix: 负债合计 carries the filing's own citation of 七、47 and 七、50,
so `plan_requests` put it in the note-set branch — which declared no sections — and the request
asked for "the printed subtotal" with no statement rows in it (24 face totals in all). And the
line's `notes_supplied` named those two notes while the request carried neither: `build_request`
takes the note text from `identified_notes`, a pool only `note_source` lines and a bounded number
of scored or cited notes reach (51 of the 263 notes named across that filing's requests).
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.schemas.line_items import load_line_item_set
from app.services import line_item_llm, line_item_requests

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
KEY = "bs_cl__total_liabilities"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _note(number: str, title: str, caption: str, amount: str) -> NotesTable:
    row = NoteItem(raw_label=caption, note_number=number)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal(amount), provenance=Provenance(page_index=214)))
    return NotesTable(note_number=number, title=title, page_index=214, items=[row])


def _doc():
    """A face row of the line, and the filing's printed reference from it to note 七、47."""
    return SimpleNamespace(line_items=[SimpleNamespace(id="r1", canonical_key=KEY)],
                           links=[SimpleNamespace(face_item_id="r1", note_number="七、47")])


def test_the_cited_line_is_planned_with_its_notes_and_its_statement(shipped):
    notes = [_note("七、47", "、租赁负债", "租赁负债", "702063683.82")]
    plans, by_key, _notes_of, _identified = line_item_llm.plan_and_notes(
        shipped, notes, get_settings(), doc=_doc())
    plan = next(p for p in plans if KEY in p.keys)
    assert "七、47" in plan.notes, "the filing's citation is the line's note"
    assert plan.sections, "a note-set plan declares the sections its lines may be claimed under"
    assert line_item_requests.face_statements(plan, by_key) == {"balance_sheet"}


def test_every_note_a_line_is_told_about_is_in_its_request(shipped):
    notes = [_note("七、47", "、租赁负债", "租赁负债", "702063683.82")]
    plans, by_key, notes_of, identified = line_item_llm.plan_and_notes(
        shipped, notes, get_settings(), doc=_doc())
    plan = next(p for p in plans if KEY in p.keys)
    request = line_item_llm.build_request(plan, by_key, notes_of, identified, None)
    named = {n for li in request["line_items"] for n in li["notes_supplied"]}
    sent = {str(n.get("note")) for n in request["notes"]}
    assert named and named <= sent, f"named {sorted(named)} but sent {sorted(sent)}"
    block = next(n for n in request["notes"] if n.get("note") == "七、47")
    assert KEY in block["identified_for"]
