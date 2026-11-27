"""A NOTE THAT PRINTS ONE CHARGE IN TWO OF ITS TABLES PRINTS ONE CHARGE.

NEW FILE -> backend/tests/test_a_figure_a_note_restates_in_another_table_is_counted_once.py

IFRS 16's right-of-use note prints the year's depreciation in its movement table and again in the
table of "amounts recognised in profit or loss". Vivo Energy Mauritius 2025, note 16:

    p92  movement table   Charge for the year                 17,426   16,633
    p93  P&L amounts      Depreciation of right-of-use asset  17,426   16,633

`sub__prepaid_lease_depreciation` summed the two (34,852 / 33,266), and Deprec & Impairment
(Oper Exp) showed 211,665 / 206,748 against the 194,239 / 190,115 note 9 prints (p86). Rows of
ONE table are components and still add; only the same figure from ANOTHER table of the same note,
on the same or a neighbouring sheet, is the same fact (`services.note_sourced._restated`).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services import note_sourced as NS


def _hit(table, amount, period, page, caption="Charge for the year", note="16"):
    value = SimpleNamespace(provenance=SimpleNamespace(page_index=page, label_bbox=None),
                            value=Decimal(amount))
    return NS.NoteRowHit(key="sub__prepaid_lease_depreciation", note_number=note,
                         note_title="RIGHT-OF-USE ASSETS", caption=caption, matched_by="x",
                         value=value, amount=Decimal(amount), basis="consolidated",
                         period=period, table=table)


def test_the_charge_printed_again_in_the_notes_profit_or_loss_table_counts_once():
    movement, recognised = object(), object()
    hits = [_hit(movement, "17426", "current", 47), _hit(movement, "16633", "prior", 47),
            _hit(recognised, "17426", "current", 47, "Depreciation of right-of-use asset"),
            _hit(recognised, "16633", "prior", 47, "Depreciation of right-of-use asset")]
    out = NS.resolve(hits, "sum")
    assert out[("consolidated", "current")][0] == Decimal("17426")
    assert out[("consolidated", "prior")][0] == Decimal("16633")
    inputs = out[("consolidated", "current")][1]
    assert [i["counted"] for i in inputs] == [True, False]
    assert inputs[1]["reason"] == "restated"


def test_two_rows_of_one_table_with_the_same_figure_still_add():
    """Two asset classes charged the same amount in one movement table are two charges."""
    table = object()
    hits = [_hit(table, "500", "current", 47, "Buildings"),
            _hit(table, "500", "current", 47, "Vehicles")]
    assert NS.resolve(hits, "sum")[("consolidated", "current")][0] == Decimal("1000")


def test_a_note_numbered_alike_in_another_set_of_statements_is_not_judged():
    """An Indian filing numbers its standalone and its consolidated notes alike; two tables of
    "note 4" eighty sheets apart are two notes, and are left as they were read."""
    hits = [_hit(object(), "59775", "current", 173, note="4"),
            _hit(object(), "59775", "current", 253, note="4")]
    assert NS.resolve(hits, "sum")[("consolidated", "current")][0] == Decimal("119550")


def test_another_note_printing_the_same_figure_is_not_judged_here():
    hits = [_hit(object(), "17426", "current", 47, note="16"),
            _hit(object(), "17426", "current", 46, note="9")]
    assert NS.resolve(hits, "sum")[("consolidated", "current")][0] == Decimal("34852")


def test_an_alternatives_part_is_unchanged():
    movement, recognised = object(), object()
    hits = [_hit(movement, "17426", "current", 47), _hit(recognised, "17426", "current", 47)]
    out = NS.resolve(hits, "alternatives")[("consolidated", "current")]
    assert out[0] == Decimal("17426")
    assert [i.get("reason") for i in out[1]] == [None, "alternative"]
