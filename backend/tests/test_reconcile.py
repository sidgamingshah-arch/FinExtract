"""Tests for the note→face reconciliation arithmetic (Requirement 20)."""
from __future__ import annotations

from decimal import Decimal

from app.services.reconcile import NoteDetail, ReconcileInput, reconcile_face


def test_one_to_one_subtraction():
    # Face PPE = 12,800; note details (already ingested as their own lines) sum to it.
    inp = ReconcileInput(
        face_item_id="ppe",
        note_number="5",
        raw_face_value=Decimal("12800"),
        details=[
            NoteDetail("gross_block", Decimal("15000")),
            NoteDetail("acc_depreciation", Decimal("-2200")),
        ],
    )
    out = reconcile_face(inp)
    assert out.subtracted == Decimal("12800")
    assert out.reconciled == Decimal("0")     # fully explained by the note
    assert out.residual == Decimal("0")
    assert out.within_tolerance


def test_partial_leaves_residual():
    inp = ReconcileInput(
        face_item_id="investments",
        note_number="6",
        raw_face_value=Decimal("5000"),
        details=[NoteDetail("quoted", Decimal("3000"))],  # only part mapped
    )
    out = reconcile_face(inp)
    assert out.subtracted == Decimal("3000")
    assert out.reconciled == Decimal("2000")   # the residual "other" component
    assert out.residual == Decimal("2000")


def test_negative_signed_detail_subtracted_with_sign():
    inp = ReconcileInput(
        face_item_id="net_block",
        note_number="5",
        raw_face_value=Decimal("12800"),
        details=[NoteDetail("acc_depreciation", Decimal("-2200"))],
    )
    out = reconcile_face(inp)
    # subtracting a negative adds back: 12800 - (-2200) = 15000
    assert out.reconciled == Decimal("15000")


def test_dedupe_prevents_double_subtraction():
    inp = ReconcileInput(
        face_item_id="x",
        note_number="9",
        raw_face_value=Decimal("100"),
        details=[NoteDetail("a", Decimal("40")), NoteDetail("a", Decimal("40"))],
    )
    out = reconcile_face(inp)
    assert out.subtracted == Decimal("40")     # duplicate ignored
    assert any("duplicate" in w for w in out.warnings)


def test_idempotent_from_raw():
    inp = ReconcileInput(
        face_item_id="x", note_number="1", raw_face_value=Decimal("500"),
        details=[NoteDetail("a", Decimal("200"))],
    )
    first = reconcile_face(inp)
    second = reconcile_face(inp)   # recomputed from raw, never from reconciled
    assert first.reconciled == second.reconciled == Decimal("300")


def test_negative_reconciled_is_flagged():
    inp = ReconcileInput(
        face_item_id="x", note_number="1", raw_face_value=Decimal("100"),
        details=[NoteDetail("a", Decimal("250"))],
    )
    out = reconcile_face(inp)
    assert out.reconciled == Decimal("-150")
    assert any("negative" in w for w in out.warnings)


# ── a note's own total, where its caption did not say so ─────────────────────────────────────────

def _tie(face, values):
    return reconcile_face(ReconcileInput(
        face_item_id="f", note_number="n", raw_face_value=Decimal(str(face)),
        details=[NoteDetail(f"i{n}", Decimal(str(v))) for n, v in enumerate(values)]))


def test_a_movement_schedules_closing_balance_is_not_a_detail():
    """OPENING AND CLOSING ARE PRINTED UNDER THE SAME WORDS, so no caption test can separate them.

        At 1 January 2023        129,132
        Additions                158,605
        Disposal of subsidiaries (134,110)
        At 31 December 2023      153,627     <- the total of the three above

    On China SCE notes 19 and 33 both rows are truncated to "At", so the date that distinguishes
    them is not even present. Read as a detail, the closing balance is summed WITH the rows it
    totals and the note total comes to twice the face — which is what made five of that filing's
    one-to-one ties unconfirmable.

    The stray 18 and 22 in the values below are real: two fragments the note's own rows carry, and
    they are why the equality is tested to a tolerance rather than exactly.
    """
    out = _tie(153627, [129132, 158605, -134110, 153627, 18, 22])
    assert out.tie_status == "tied"
    assert any("note's own total" in w for w in out.warnings)


def test_a_net_closing_line_after_a_deduction_is_not_a_detail():
    """The same shape without a movement schedule: a cash note deducting restricted cash, then
    printing the net. `Subtotal` is already excluded by its caption; `Cash and cash equivalents` is
    not, and it equals what remains."""
    assert _tie(4884525, [5496426, 952500, -1564401, 4884525]).tie_status == "tied"


def test_three_genuine_details_that_happen_to_sum_are_left_alone():
    """THE FALSE POSITIVE THIS RULE HAS TO REFUSE, and the reason the face corroborates it.

    100, 200 and 300 against a face of 600 is three details, one of which is arithmetically the sum
    of the other two. Reading it as a total drops a real figure and makes the note tie against 300
    — a tie that confirms against too little, silently. With the face consulted, dropping 300 would
    leave 300 against a printed 600, which does not tie, so the coincidence is ignored and the note
    ties on all three.
    """
    out = _tie(600, [100, 200, 300])
    assert out.tie_status == "tied"
    assert out.residual == Decimal("0")
    assert not any("note's own total" in w for w in out.warnings)


def test_an_ambiguous_note_is_refused():
    """Two refusals that do not depend on the face at all.

    A two-row note where the rows are equal has no candidate — each trivially "totals" the other —
    and a note where two details each equal the sum of the rest does not say which is the total, so
    guessing would drop a real figure.
    """
    for face, values in ((100, [100, 100]), (4, [2, 2, 4, 4])):
        out = _tie(face, values)
        assert not any("note's own total" in w for w in out.warnings), (face, values)


def test_a_zero_detail_is_never_the_total():
    """Zero equals the sum of an empty set and of any note whose details cancel, so it can never be
    the evidence that a row is a total."""
    out = _tie(0, [50, -50, 0])
    assert not any("note's own total" in w for w in out.warnings)


def test_a_note_that_is_not_a_breakdown_still_declines():
    """The direction this module prefers everywhere: a residual too large to be rounding leaves the
    tie unconfirmed rather than being explained away. Measured on China SCE note 15 against
    `is_pl__other_operating_expenses`: a face of -3,754,084 and a note total of 2,085."""
    assert _tie(-3754084, [1000, 1085]).tie_status == "unconfirmed"
