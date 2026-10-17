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


# THE SCALE NAMES A READER WRITES, by factor. A factor not listed is written as its multiplier
# alone, which is still exact.
_SCALE_NAMES = {Decimal(1000): "thousands", Decimal(100000): "lakhs", Decimal(1000000): "millions",
                Decimal(10000000): "crores", Decimal(1000000000): "billions"}

NOT_STATED = "not stated"


def describe(unit, fallback=None) -> str:
    """The unit a figure is printed in, as a request states it: ``"INR lakhs (x100,000)"``.

    WHAT THE RUN KNOWS, AND NOTHING IT DOES NOT. Each half — currency and scale — is the figure's
    own where it was read (`currency_declared`, `scale_declared`), else `fallback`'s (the
    document's unit), and a half known on neither side is said to be unknown rather than guessed.
    An Indian face row is the case the fallback exists for: its "(₹ in lakhs)" line is page chrome,
    so the row carries no scale of its own while the document's unit says lakhs — see
    `stages.note_sourced._statement_unit`. ``"not stated"`` when neither half is known.
    """
    currency = (str(unit.currency) if currency_declared(unit)
                else str(fallback.currency) if currency_declared(fallback) else "")
    scale = _scale_words(unit, fallback)
    if currency and scale:
        return f"{currency}{',' if scale.startswith('as printed') else ''} {scale}"
    if currency:
        return f"{currency}, scale not stated"
    if scale:
        return f"{scale}, currency not stated"
    return NOT_STATED


def _scale_words(unit, fallback) -> str:
    """The scale half of `describe`: a resolved factor first, the figure's own before the fallback.

    A LABEL WITH A FACTOR OF 1 IS A SCALE THE READER SAW AND DID NOT RESOLVE: the row reader keeps
    the printed signal ("₹inlakhs") when its scale vocabulary has no word for it, and leaves the
    factor at 1. Stating that as "x1" would be a guess the run never made, so a resolved factor —
    the document's, on f1 — wins over it, and with none the label is quoted as printed.
    """
    for u in (unit, fallback):
        if u is not None and _scale(u) != 1:
            factor = _scale(u)
            name = _SCALE_NAMES.get(factor)
            multiplier = (f"x{int(factor):,}" if factor == factor.to_integral_value()
                          else f"x{scale_text(factor)}")
            return f"{name} ({multiplier})" if name else f"({multiplier})"
    for u in (unit, fallback):
        if label := str(getattr(u, "units_label", None) or "").strip():
            return f"as printed '{label}'"
    return ""


def row_unit(row, fallback=None) -> str:
    """`describe` for a row: the unit most of its figures carry (matrix figures included)."""
    from collections import Counter

    seen = Counter(describe(getattr(ev, "unit_ctx", None), fallback)
                   for ev in (getattr(row, "values", None) or {}).values()
                   if getattr(ev, "value", None) is not None)
    return seen.most_common(1)[0][0] if seen else describe(None, fallback)


def block_units(entries: list[dict], fallback=None) -> str:
    """The unit a block states, given its rows' `_unit` (popped here).

    ONE UNIT FOR THE BLOCK WHEN ITS ROWS AGREE. Where they do not, the block says so and each row
    keeps its own `unit` — a block-level unit that is true of only some rows would be wrong about
    the rest.
    """
    units = [e.pop("_unit", None) for e in entries]
    distinct = {u for u in units if u}
    if len(distinct) <= 1:
        return next(iter(distinct)) if distinct else describe(None, fallback)
    for entry, unit in zip(entries, units):
        if unit:
            entry["unit"] = unit
    return "varies by row; see each row's unit"
