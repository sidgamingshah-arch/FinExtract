"""Note→face reconciliation (Requirement 20) — the highest-value logic.

When a detailed item comes from a NOTE and the FACE has an overarching line that
references that note, the face aggregate must be adjusted by **subtracting** the note
detail(s) that are *also* ingested as their own template lines, so nothing is
double-counted and totals tie.

This module implements the arithmetic as pure functions (no I/O) so it is exhaustively
unit- and property-testable. The stage wraps it.

Key invariants:
* Reconciliation is computed **from the raw face value**, never from an already
  reconciled figure → re-running is idempotent.
* Signs are respected: a detail that is itself negative is subtracted with its sign — within the
  note's own convention, which is not always the face's. ``ReconcileInput.note_orientation`` says
  which way round the two are, and the caller establishes it across all of a face line's periods.
* Values must share a unit context before subtraction (caller converts to base units).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class NoteDetail:
    item_id: str
    value: Decimal
    maps_to_distinct_template_line: bool = True  # only these cause double-counting


@dataclass
class ReconcileInput:
    face_item_id: str
    note_number: str
    raw_face_value: Decimal
    details: list[NoteDetail]
    tolerance_abs: Decimal = Decimal(1)
    tolerance_rel: Decimal = Decimal("0.001")
    coverage: Decimal = Decimal(1)          # for 1-note→N-face partitioning
    # How close the note total must come to the face figure before we accept that the note
    # really is a DECOMPOSITION of that figure. See ``tie_status`` below.
    corroboration_rel: Decimal = Decimal("0.05")
    # WHICH SIGN CONVENTION THE NOTE'S FIGURES ARE IN, relative to the face's. ``1`` when they
    # match, ``-1`` when the note prints the same quantity with the opposite sign — a note is a
    # schedule OF a charge and prints it positive, while a P&L presents that charge as a deduction
    # and prints it negative. Neither is wrong, and comparing them without saying which is which
    # made the tie report a residual of TWICE the figure on a note that reconciles exactly.
    #
    # THE CALLER DECIDES IT, not this function, and that is the whole safety of it: with a single
    # period there is nothing for an orientation to be consistent WITH, so a flip chosen here would
    # turn every sign error into a tie. The stage chooses one orientation for ALL of a face line's
    # periods at once, which is a claim about presentation that a per-column error cannot satisfy.
    note_orientation: int = 1


# A note table either decomposes the face figure or it does not, and the difference decides
# whether a mismatch is a defect or a non-event:
#   tied         — the note total equals the face figure within tolerance.
#   untied       — the note total is close enough to be plainly the SAME quantity, yet does
#                  not tie: a real, actionable discrepancy (rounding, sign, a missed row).
#   unconfirmed  — the note total is nowhere near the face figure. Most notes are not
#                  decompositions at all (an analysis of profit before tax, a segment table,
#                  a commitments schedule), and a face line can cite several tables of which
#                  only one is the breakdown. Claiming "does not tie" here would accuse the
#                  filing of an error we have no evidence for, so it is recorded, not raised.
TIE_TIED = "tied"
TIE_UNTIED = "untied"
TIE_UNCONFIRMED = "unconfirmed"

# The default corroboration band. A fresh run uses the CONFIGURED band (the reconcile stage
# passes ``extraction.recon_corroboration_rel``); this constant is what ``tie_status`` falls back
# to when grading an entry stored before the grade existed. Deliberately a constant and not the
# live setting: an old run's grade should reconstruct what that run would have said, not shift
# because an admin has since moved the slider.
CORROBORATION_REL = Decimal("0.05")


def tie_status(entry: dict) -> str:
    """The grade of a reconciliation entry as stored in a run result.

    Runs persisted before the grade existed carry only ``within_tolerance``, and those are
    precisely the runs whose entries were ungraded. The grade is fully determinable from the
    numbers already stored, so it is derived here rather than requiring re-extraction. Every
    consumer (review queue, checks engine, export) goes through this function so they cannot
    disagree about whether a note ties.
    """
    stored = entry.get("tie_status")
    if stored:
        return str(stored)
    if entry.get("within_tolerance"):
        return TIE_TIED
    try:
        face = abs(Decimal(str(entry.get("raw_face") or 0)))
        resid = abs(Decimal(str(entry.get("residual") or 0)))
    except (TypeError, ValueError, ArithmeticError):
        return TIE_UNCONFIRMED
    return TIE_UNTIED if resid <= CORROBORATION_REL * face else TIE_UNCONFIRMED


@dataclass
class ReconcileOutput:
    face_item_id: str
    note_number: str
    raw_face: Decimal
    subtracted: Decimal
    reconciled: Decimal
    residual: Decimal
    within_tolerance: bool
    tie_status: str = TIE_UNCONFIRMED
    warnings: list[str] = field(default_factory=list)


def tolerance(face: Decimal, abs_eps: Decimal, rel_eps: Decimal) -> Decimal:
    return max(abs_eps, (rel_eps * abs(face)))


def reconcile_face(inp: ReconcileInput) -> ReconcileOutput:
    """Compute the reconciled face value by subtracting the mapped note details.

    ``reconciled = raw_face - Σ(detail.value for details that map to a distinct
    template line)``. The ``residual`` is what remains after also accounting for the
    *full* note (i.e. how far the note total is from the face) — surfaced for review.

    ``tie_status`` grades the residual, because a large one usually means the note is not a
    breakdown of this figure rather than that the filing disagrees with itself.
    """
    warnings: list[str] = []

    # Dedupe details by id (guards N-notes→1-face double subtraction).
    seen: set[str] = set()
    subtracted = Decimal(0)
    note_total = Decimal(0)
    for d in inp.details:
        if d.item_id in seen:
            warnings.append(f"duplicate detail {d.item_id} ignored")
            continue
        seen.add(d.item_id)
        oriented = d.value * inp.note_orientation
        note_total += oriented
        if d.maps_to_distinct_template_line:
            subtracted += oriented

    reconciled = inp.raw_face_value - subtracted

    # Residual: the "other/unexplained" remainder between face and the FULL note total.
    residual = inp.raw_face_value - note_total
    tol = tolerance(inp.raw_face_value, inp.tolerance_abs, inp.tolerance_rel)
    within = abs(residual) <= tol

    # Corroboration decides whether a non-tie is a finding or simply not a breakdown.
    # A zero face figure has no meaningful relative band, so tolerance alone decides.
    band = inp.corroboration_rel * abs(inp.raw_face_value)
    if within:
        tie_status = TIE_TIED
    elif abs(residual) <= band:
        tie_status = TIE_UNTIED
    else:
        tie_status = TIE_UNCONFIRMED

    if reconciled < 0:
        warnings.append(
            "reconciled face value is negative — likely over-subtraction or a sign error"
        )

    return ReconcileOutput(
        face_item_id=inp.face_item_id,
        note_number=inp.note_number,
        raw_face=inp.raw_face_value,
        subtracted=subtracted,
        reconciled=reconciled,
        residual=residual,
        within_tolerance=within,
        tie_status=tie_status,
        warnings=warnings,
    )
