"""A CITATION WHOSE COLUMN NO ROW PRINTS IS NAMED IN THE TRAIL — it does not take the run down.

NEW FILE -> backend/tests/test_an_unlocated_column_citation_is_named_not_raised.py

Measured on 河钢 000709: the model's allowance answer cited note 七、3's 合计 by column,
"期初余额 · 坏账准备", on a reconstructed row that does not print that column.
`note_sourced.resolve_sources` keeps such a citation as UNRESOLVED and records the column as the
model gave it — a string — while a resolved citation carries the column it picked as
`{heading, key}`. The trail label read both as a dict, so the string raised AttributeError in
`stages.line_item_llm._input_label` and the whole extraction failed.
"""
from __future__ import annotations

from app.stages.line_item_llm import _input_label, _unlocated, _unlocated_label


def test_an_unresolved_citation_names_the_column_it_asked_for():
    entry = {"at": 1, "note": "七、3", "statement": "", "caption": "合计",
             "column": "期初余额 · 坏账准备", "why": "no column of the row matched it"}

    assert _unlocated_label(entry) == "note 七、3: 合计 [期初余额 · 坏账准备]"

    (trail,) = _unlocated([entry], {}, "current", False)
    assert trail["label"] == "note 七、3: 合计 [期初余额 · 坏账准备]"
    assert (trail["located"], trail["status"], trail["reason"]) == (False, "missing", "not_located")


def test_a_resolved_citation_still_names_the_column_it_picked():
    entry = {"note": "七、3", "caption": "合计",
             "column": {"heading": "期末余额 · 坏账准备", "key": "current:allowance"}}
    assert _input_label(entry) == "note 七、3: 合计 [期末余额 · 坏账准备]"


def test_a_citation_without_a_column_is_named_by_its_row():
    assert _input_label({"note": "七、3", "caption": "合计"}) == "note 七、3: 合计"
