"""A COLUMN CITATION OF A CAPTION TWO ROWS SHARE TAKES THE ROW THAT PRINTS THE COLUMN.

NEW FILE -> backend/tests/test_a_column_citation_takes_the_row_that_prints_the_column.py

河钢 000709's 七、3（1）以摊余成本计量的应收账款 prints its 期末余额 table and, under （续）, its 期初余额
table in one block, and both total rows are 合计. The model cited 合计 by "期末余额 · 坏账准备" and by
"期初余额 · 坏账准备". Document order took the first 合计 for both, and the second citation was
refused — "no column of that row is headed '期初余额 · 坏账准备'" — so the allowance and the gross
receivables lost their opening balances. Among the rows a caption ties on, the column narrows to
the one printing it, as the table, the group and the period already do.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

TITLE = "以摊余成本计量的应收账款"


def _total(period: str, heading: str, gross: str, allowance: str) -> NoteItem:
    item = NoteItem(raw_label="合计")
    for key, measure, amount in ((period, "账面余额", gross),
                                 (f"{period}:allowance", "坏账准备", allowance)):
        item.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                      basis=Basis.CONSOLIDATED, period_label=key,
                                      column_heading=f"{heading} · {measure}",
                                      column_period=period))
    return item


def _notes() -> list[NotesTable]:
    return [NotesTable(note_number="七、3", title=TITLE, items=[
        _total("current", "期末余额", "2689546185.47", "1394836830.02"),
        _total("prior", "期初余额", "2595130766.28", "1445376980.12")])]


def _cite(column: str):
    return resolve_sources([SourceRef(note="七、3", table=TITLE, caption="合计", column=column)],
                           _notes())


def test_the_opening_column_is_read_off_the_continued_tables_total():
    resolved, unresolved = _cite("期初余额 · 坏账准备")
    assert unresolved == []
    assert [r["figures"] for r in resolved] == [{"prior": "1445376980.12"}]


def test_the_closing_column_still_reads_the_first_total():
    resolved, unresolved = _cite("期末余额 · 坏账准备")
    assert unresolved == []
    assert [r["figures"] for r in resolved] == [{"current": "1394836830.02"}]


def test_a_column_no_row_prints_is_still_refused_and_named():
    resolved, unresolved = _cite("期末余额 · 账面价值")
    assert resolved == []
    assert "no column of that row is headed" in unresolved[0]["why"]
