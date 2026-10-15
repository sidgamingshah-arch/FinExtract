"""The unit a figure was printed in against the unit it is published or compared in.

`row_reconstruct` reads each page's unit off its own header band ("RMB'000", "人民幣百萬元") and
writes it onto every fact it reads there, so a note printed in millions beside a face printed in
thousands carries its own scale. Two places then move a note figure next to a face figure: the
note route publishes a note row on a face line (`stages.note_sourced`), and the note-to-face tie
compares a note's rows with the face figure they explain (`stages.reconcile`). Neither compared
units, so a millions note published under a thousands face was a thousandfold understatement, and
the tie graded the same gap `unconfirmed`, which says nothing.

A UNIT IS COMPARED ONLY WHERE BOTH SIDES DECLARE IT. An unresolved unit is `UnitContext()` — no
currency, scale 1, no label — and that means "nothing was read", not "units of one". So a side whose
scale was not read is never rescaled, and a side whose currency was not read is never refused: the
figure is treated as it always was.
"""
from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple


class UnitCheck(NamedTuple):
    """What it takes to put a printed figure into the target's unit.

    `factor` multiplies the printed figure (1 when nothing changes). `rescaled` is
    ``(from_scale, to_scale)`` as written in the `unit_rescaled:` flag, or None. `currency_mismatch`
    is ``(printed_currency, target_currency)`` when both are declared and differ; the figure must
    then not be used at all, because no factor converts one currency into another.
    """
    factor: Decimal
    rescaled: tuple[str, str] | None
    currency_mismatch: tuple[str, str] | None


SAME = UnitCheck(Decimal(1), None, None)


def scale_declared(unit) -> bool:
    """Whether a scale was READ for this unit: a label from the header, or a factor other than 1."""
    if unit is None:
        return False
    return bool(getattr(unit, "units_label", None)) or _scale(unit) != 1


def currency_declared(unit) -> bool:
    return unit is not None and bool(getattr(unit, "currency", "") or "")


def scale_text(scale) -> str:
    """A scale as the flags write it: "1000", "1000000" — never "1E+6"."""
    return format(Decimal(str(scale)).normalize(), "f")


def compare(printed_in, target) -> UnitCheck:
    """How a figure printed in `printed_in` becomes a figure in `target`'s unit.

    A declared currency on both sides that differs is a mismatch, whatever the scales say. A
    declared scale on both sides that differs is a rescale by printed / target. Anything else is
    `SAME`.
    """
    if currency_declared(printed_in) and currency_declared(target):
        a, b = str(printed_in.currency), str(target.currency)
        if a.upper() != b.upper():
            return UnitCheck(Decimal(1), None, (a, b))
    if scale_declared(printed_in) and scale_declared(target):
        source, dest = _scale(printed_in), _scale(target)
        if source != dest and source and dest:
            return UnitCheck(source / dest, (scale_text(source), scale_text(dest)), None)
    return SAME


def _scale(unit) -> Decimal:
    try:
        return Decimal(str(getattr(unit, "scale_factor", 1) or 1))
    except ArithmeticError:
        return Decimal(1)
