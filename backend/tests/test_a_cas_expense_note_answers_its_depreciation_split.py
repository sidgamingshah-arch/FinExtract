"""A `prose` depreciation split may take the depreciation row of its expense's OWN CAS note.

A CAS filing gives 管理费用, 销售费用 and 研发费用 a note each, tabulated by nature, and the note's
折旧与摊销 row IS the depreciation charged to that expense. `route: prose` refused every row, so on
four CAS filings the model's eight correct citations of those rows were all refused ("this line's
figure is stated in prose, not in a table row").

What `prose` was declared against is still refused (`test_a_prose_line_is_not_filled_from_a_row`):
the fixed-asset note's depreciation row, which is the TOTAL the split is part of, and any English
expense note's row.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, Provenance
from app.services import line_item_llm, line_item_routes
from app.services.line_item_config import load_shipped_set
from app.services.line_item_llm import LineItemAnswer
from app.services.mapping import SourceRef


@pytest.fixture(scope="module")
def shipped():
    return {i.key: i for i in load_shipped_set().items}


def _note(number: str, title: str, caption: str, amount: str) -> NotesTable:
    table = NotesTable(note_number=number, title=title, page_index=150)
    row = NoteItem(raw_label=caption, note_number=number)
    row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                 value=Decimal(amount), provenance=Provenance(page_index=150)))
    table.items.append(row)
    return table


def _resolve(item, note, number, caption):
    return line_item_llm.resolve(
        LineItemAnswer(key=item.key, role="whole", confidence=0.9,
                       sources=[SourceRef(note=number, caption=caption)]),
        [note], None,
        allow_face=line_item_routes.may_read_face(item),
        allow_rows=line_item_routes.may_read_table_rows(item),
        expense_rows=line_item_routes.expense_note_rows(item))


@pytest.mark.parametrize("key, title, caption", [
    ("sub__ga_depreciation", "管理费用", "折旧与摊销"),
    ("sub__selling_marketing_depreciation", "销售费用", "折旧及摊销"),
    ("sub__rd_depreciation", "研发费用", "折旧摊销"),
])
def test_the_expense_notes_own_depreciation_row_answers(shipped, key, title, caption):
    note = _note("七、42", title, caption, "86128419.63")
    resolved, unresolved, figures = _resolve(shipped[key], note, "七、42", caption)
    assert resolved and not unresolved, unresolved
    assert figures == {"current": Decimal("86128419.63")}


def test_the_fixed_asset_notes_total_is_still_refused(shipped):
    note = _note("七、21", "固定资产", "本期计提折旧", "2937455476.06")
    resolved, unresolved, _ = _resolve(shipped["sub__ga_depreciation"], note, "七、21",
                                       "本期计提折旧")
    assert not resolved and "not in a table row" in unresolved[0]["why"]


def test_another_row_of_the_expense_note_is_still_refused(shipped):
    note = _note("七、42", "管理费用", "职工薪酬", "381000000.00")
    resolved, unresolved, _ = _resolve(shipped["sub__ga_depreciation"], note, "七、42", "职工薪酬")
    assert not resolved and unresolved


def test_an_english_expense_note_is_still_refused(shipped):
    note = _note("9", "ADMINISTRATIVE EXPENSES", "Depreciation of property, plant and equipment",
                 "12345")
    resolved, unresolved, _ = _resolve(shipped["sub__ga_depreciation"], note, "9",
                                       "Depreciation of property, plant and equipment")
    assert not resolved and "not in a table row" in unresolved[0]["why"]


def test_only_prose_lines_carry_the_allowance(shipped):
    assert line_item_routes.expense_note_rows(shipped["sub__rp_find_2"]) is None
