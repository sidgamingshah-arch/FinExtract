"""A CITATION NAMES THE TABLE AND THE GROUP A ROW IS UNDER, because a note number and a caption do not.

A CAS note number carries many differently-headed tables — 河钢股份 000709's 十二、1 carries
nineteen — and 合计 is printed in most of them. The model cited `{note, caption}`, and the resolver
took the first 合计 in document order: a citation of {十二、1, 合计} meant for the related-party
payables took 84,179,120,681.23, the purchases-of-goods table's total, for a line whose own table
totals 952,791,540.11. Inside one table the same caption repeats per group, because a related-party
balance table prints a 合计 under 应收账款：, another under 其他应收款：.

And the request did not carry the group at all: rows were sent as `{caption, figures}`, so on a
related-party note — whose captions are counterparty NAMES — nothing told the model whether
"唐山唐钢气体有限公司 118,850.00" was a trade receivable, another receivable or a payable. The
deterministic route has always read the group.

So the request sends each row's `group`, a citation names the block `title` and the `group`, and
the resolver narrows to them — refusing a block the note does not have, and refusing a caption that
still names several rows with different figures where the request gave the model a way to tell
them apart.
"""
from __future__ import annotations

import json
from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
from app.services import note_context
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources


def _row(caption: str, amount: str, group: str = "") -> NoteItem:
    item = NoteItem(raw_label=caption, group_hint=group)
    item.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                  basis=Basis.CONSOLIDATED, period_label="current"))
    return item


def _table(title: str, *rows: NoteItem, number: str = "十二、1") -> NotesTable:
    return NotesTable(note_number=number, title=title, items=list(rows))


PAYABLES_TITLE = "应付项目"


def _notes() -> list[NotesTable]:
    return [
        _table("采购商品情况表", _row("河钢集团有限公司", "1000.00"),
               _row("合计", "84179120681.23")),
        _table(PAYABLES_TITLE,
               _row("青岛河钢新材料科技股份有限公司", "1393832.82", "合同负债："),
               _row("合计", "1393832.82", "合同负债："),
               _row("唐钢美锦（唐山）煤化工有限公司", "37492764.37", "应付账款："),
               _row("合计", "818991135.15", "应付账款：")),
        # the payables table continued onto the next page, with no heading of its own
        _table("", _row("唐山创元方大电气有限责任公司", "94959103.06", "其他应付款："),
               _row("合计", "102310636.03", "其他应付款：")),
    ]


def _resolve(**cite):
    resolved, unresolved = resolve_sources([SourceRef(note="十二、1", **cite)], _notes())
    return ([r["figures"].get("current") for r in resolved],
            [u["why"] for u in unresolved])


def test_a_caption_several_tables_print_is_refused_rather_than_taken_in_document_order():
    got, why = _resolve(caption="合计")
    assert got == [] and why and "different figures" in why[0], (got, why)


def test_the_table_and_the_group_name_the_row():
    assert _resolve(table=PAYABLES_TITLE, group="应付账款", caption="合计")[0] == ["818991135.15"]
    assert _resolve(table="采购商品情况表", caption="合计")[0] == ["84179120681.23"]


def test_a_continuation_page_is_read_as_the_table_it_continues():
    assert _resolve(table=PAYABLES_TITLE, group="其他应付款", caption="合计")[0] == [
        "102310636.03"]


def test_a_table_the_note_does_not_have_is_refused_not_widened():
    got, why = _resolve(table="关联担保情况", caption="合计")
    assert got == [] and "has no table headed" in why[0], (got, why)


def test_within_one_table_the_group_is_needed_where_the_caption_repeats():
    got, why = _resolve(table=PAYABLES_TITLE, caption="合计")
    assert got == [] and "different figures" in why[0], (got, why)


def test_a_group_no_row_carries_narrows_nothing():
    """A row may carry no group while the model names the heading it reads it as under; that is
    not a wrong citation, so the group is simply not used where it matches nothing."""
    assert _resolve(table="采购商品情况表", group="采购商品", caption="合计")[0] == [
        "84179120681.23"]


def test_rows_the_request_showed_identically_keep_the_document_order_tie_break():
    """Nothing a model could write names one of two rows in the same block and group with the same
    caption, so refusing would lose the figure for no gain. The first is taken, as before."""
    notes = [_table("长期应付款", _row("合计", "1.00", "长期应付款："),
                    _row("合计", "2.00", "长期应付款："), number="十二、7")]
    resolved, unresolved = resolve_sources(
        [SourceRef(note="十二、7", table="长期应付款", caption="合计")], notes)
    assert [r["figures"]["current"] for r in resolved] == ["1.00"] and not unresolved


def test_the_request_carries_each_rows_group_and_each_blocks_title():
    """The continuation page as extraction really delivers it: a section continued from the page
    before carries that page's heading (`extract_note_tables(carry_note=…)`)."""
    notes = _notes()
    notes[-1].title = PAYABLES_TITLE
    items = [LineItemDef(key="k", label="k",
                         note_source=NoteSource(note_title_any=["应付项目", "采购商品"]))]
    blocks = note_context.identified_notes(LineItemSet(items=items), notes)
    by_title = {b["title"]: b["rows"] for b in blocks}
    assert set(by_title) == {"采购商品情况表", PAYABLES_TITLE}
    assert by_title[PAYABLES_TITLE][-1] == {"caption": "合计", "group": "其他应付款：",
                                           "figures": {"current": "102310636.03"}}
    assert "group" not in by_title["采购商品情况表"][0]
    json.dumps(blocks, ensure_ascii=False)


def test_the_block_mapping_is_one_definition():
    """The request and the resolver read a table's block off the same function."""
    assert [key[1] for key, _heading in note_context.note_blocks(_notes())] == [
        "采购商品情况表", "应付项目", "应付项目"]
    assert [heading for _key, heading in note_context.note_blocks(_notes())] == [
        "采购商品情况表", PAYABLES_TITLE, PAYABLES_TITLE]
