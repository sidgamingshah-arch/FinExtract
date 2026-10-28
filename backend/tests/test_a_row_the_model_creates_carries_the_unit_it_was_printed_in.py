"""A ROW THE MODEL ROUTE CREATES CARRIES THE UNIT ITS FIGURE WAS PRINTED IN.

`note_sourced._write` gives a slot it writes FRESH the unit it is passed, or the one the row's other
column carries. The model route passed none, and a row its answer creates has no other column, so
the figure took the default: no currency, scale 1. Measured on China SCE 1966 (blind replay, r15):
50 figures the model route wrote onto rows it had created were labelled scale 1 with no currency,
beside 473 labelled CNY'000 — figures in thousands labelled as units, in the export's unit column
and in every structural check that compares scales.

Now each resolved row citation carries the unit its row is printed in (`note_sourced._unit_of`),
and the fresh slot takes it (`stages.line_item_llm._cited_unit`):

  * the cited rows declare a unit and agree: that unit;
  * none declares one: the row's other column's, else the statement's — how the declared note
    route reads a row whose page declares no unit;
  * they disagree: no unit is chosen, as before.

A figure stated in a sentence is put in the statements' units, so a fresh slot takes theirs; a
count stated in one — a headcount — takes none. A slot that already exists keeps its own.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.enums import PrintedIn
from app.core.models.line_item import UnitContext
from app.schemas.line_items import load_line_item_set
from app.stages.line_item_llm import _cited_unit
from tests.test_anywhere_reads_the_whole_report import _answers, _Recorder
from tests.test_anywhere_reads_the_whole_report import _run as _run_report
from tests.test_the_models_figure_is_checked import LAND, PBT_DEP, SEED, _doc, _face, _note, _run

THOUSANDS = UnitContext(currency="CNY", scale_factor=Decimal(1000))


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(autouse=True)
def _restore_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()),
           ex.llm_parallel_requests, st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, ex.llm_parallel_requests,
         st.llm.provider) = was


def _in(row, unit):
    for ev in row.values.values():
        ev.unit_ctx = unit
    return row


def _printed(row):
    """A face row as `buckets` leaves it: printed on the face, in the statement's unit."""
    row.printed_in = PrintedIn.FACE
    return _in(row, THOUSANDS)


def _unit(row, period="current"):
    ev = next(ev for ev in row.values.values() if ev.period_label == period)
    return (ev.unit_ctx.currency, ev.unit_ctx.scale_factor)


def test_a_fresh_row_takes_the_unit_of_the_face_row_it_was_cited_from(shipped):
    """No row carries Land; the answer cites a face row printed in CNY'000 that bound nothing."""
    doc = _doc(faces=[_printed(_face("Land", "100"))])
    row, _ctx, _p = _run(shipped, doc, LAND, [{"statement": "balance_sheet", "caption": "Land"}])
    assert row is not None and row is not doc.line_items[0], "the answer created the line's row"
    assert _unit(row) == ("CNY", Decimal(1000)), "it was ('', 1): thousands labelled as units"


def test_a_cited_note_row_that_declares_no_unit_takes_the_statements(shipped):
    """The declared note route reads an undeclared note row in the face's unit; so does this one."""
    doc = _doc([_note("7", "PROFIT BEFORE TAX", [("Depreciation", "6600")])],
               [_printed(_face("Profit before tax", "1000", "pl_profit_before_tax"))])
    row, _ctx, _p = _run(shipped, doc, PBT_DEP, [{"note": "7", "caption": "Depreciation"}])
    assert row is not None and row.values
    assert _unit(row) == ("CNY", Decimal(1000))


def test_cited_rows_in_two_scales_give_the_figure_no_one_unit():
    rows = [{"unit": THOUSANDS.model_dump(mode="json")},
            {"unit": UnitContext(currency="CNY", scale_factor=Decimal(1000000)).model_dump(
                mode="json")}]
    assert _cited_unit(rows, None, "consolidated") is None
    # …and rows that agree give theirs, a page's label aside.
    same = [{"unit": THOUSANDS.model_dump(mode="json")},
            {"unit": {**THOUSANDS.model_dump(mode="json"), "source_bbox_page": 12}}]
    got = _cited_unit(same, None, "consolidated")
    assert (got.currency, got.scale_factor) == ("CNY", Decimal(1000))


def test_a_slot_that_already_exists_keeps_its_own_unit(shipped):
    face = _printed(_face("Land", "100", LAND))
    doc = _doc([_note("7", "PROPERTY", [("Land", "100")])], [face])
    note_row = doc.notes[0].items[0]
    _in(note_row, UnitContext(currency="CNY", scale_factor=Decimal(1)))
    row, _ctx, _p = _run(shipped, doc, LAND, [{"statement": "balance_sheet", "caption": "Land"}])
    assert row is face and _unit(row) == ("CNY", Decimal(1000))


def test_a_sentences_money_takes_the_statements_unit_and_a_count_takes_none():
    """Both lines' rows are created by their answers: a covenant amount in HK$ and a headcount,
    on a filing presented in HK$'000."""
    doc, _ctx = _run_report(_Recorder(_answers()))
    by_key = {li.canonical_key: li for li in doc.line_items}
    assert _unit(by_key["sup__tnw"]) == ("HKD", Decimal(1000))
    assert _unit(by_key["sup__employees"]) == ("", Decimal(1))
    assert _unit(by_key["sup__employees"], "prior") == ("", Decimal(1)), "its comparative too"
