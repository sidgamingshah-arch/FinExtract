"""THE SAME AMOUNT, WRITTEN LATER, KEEPS THE SIGN THE SLOT ALREADY CARRIES.

NEW FILE -> backend/tests/test_a_late_writer_keeps_the_sign_the_slot_carries.py

The note→face split stores BYD Electronic's interest on lease liabilities as -73,088, in the
convention of the face's bracketed finance costs. The model then cites the same note row, printed
unsigned, and `note_sourced._write` put +73,088 in the slot: net interest moved by twice the figure,
on BYD, Far East Horizon, Hon Kwok and Lai Sun. A late writer bringing the same amount has no
better word on the sign than the stage that oriented it, so the slot keeps its sign — unless an
earlier writer derived the slot, which gives it no authority.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.stages.note_sourced import _write


def _slot(value: str, raw: str | None = None, *, derived=False, normalised=False) -> LineItem:
    li = LineItem(source_label="Interest on lease liabilities",
                  canonical_key="is_pl__interest_on_lease_liabilities")
    li.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(raw or value),
                                basis=Basis.CONSOLIDATED, period_label="current", derived=derived,
                                sign_normalised=normalised))
    return li


def _after(li: LineItem, amount: str) -> Decimal:
    _write(li, "consolidated", "current", Decimal(amount), by="line_item_llm")
    (ev,) = li.values.values()
    return ev.value


def test_an_oriented_slot_keeps_its_sign_when_the_same_amount_arrives_unsigned():
    li = _slot("-73088", "73088", normalised=True)
    assert _after(li, "73088") == Decimal("-73088")
    assert "line_item_llm_kept_sign:-73088" in li.confidence.flags
    assert not any(f.startswith("line_item_llm_displaced") for f in li.confidence.flags)


def test_a_printed_negative_keeps_its_sign_too():
    """Hon Kwok's face prints the figure in brackets: read as -2,910, never flipped by normalize."""
    li = _slot("-2910")
    assert _after(li, "2910") == Decimal("-2910")


def test_a_slot_an_earlier_writer_derived_takes_the_new_sign():
    li = _slot("500", derived=True)
    assert _after(li, "-500") == Decimal("-500")


def test_a_different_amount_replaces_the_figure_as_before():
    li = _slot("-73088", "73088", normalised=True)
    assert _after(li, "80000") == Decimal("80000")
    assert "line_item_llm_displaced_printed:-73088" in li.confidence.flags
