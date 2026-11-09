"""An unmapped card prints its figure the way every other card does — thousands separated — and
formatting it moves no recorded acceptance.

The card used to print the figure exactly as stored ("868375" on Grand Ming's revenue), the one
figure on the review queue without separators. What is printed and what an acceptance is pinned to
are two different things here: the evidence keeps the unformatted string, because every acceptance
already recorded against an unmapped card was digested over it, and re-spelling it would reopen each
of them as stale over a figure that did not move.
"""
from __future__ import annotations

import pytest

from app.api.routes.documents import _build_review
from app.services import judgement


def _unmapped(label, value, y=0.40):
    return {"source_label": label, "canonical_key": None,
            "values": [{"basis": "consolidated", "period_label": "current", "value": value,
                        "provenance": {"source_kind": "native", "page_index": 0,
                                       "bbox": {"x0": 0.1, "y0": y, "x1": 0.9, "y1": y + 0.01}}}]}


def _card(value, locale="en"):
    review = _build_review([_unmapped("Revenue 收益", value)], "kaming.pdf", locale)
    return next(c for c in review["checks"] if c["type"] == "unmapped")


def _value_row(card):
    return next(row for row in card["calc"] if row[0] == "Value")


@pytest.mark.parametrize("stored, printed", [
    ("868375", "868,375"),
    ("-1234567", "-1,234,567"),
    # A figure as read, not an arithmetic result: no decimal it was printed with is dropped.
    ("10.91", "10.91"),
    ("1234567.50", "1,234,567.50"),
    ("999", "999"),
])
def test_the_card_prints_the_figure_thousands_separated_with_every_decimal_kept(stored, printed):
    assert _value_row(_card(stored)) == ["Value", printed, False]


@pytest.mark.parametrize("stored", ["n/a", "(see note 12)", ""])
def test_a_value_that_is_not_a_figure_is_printed_as_it_was_read(stored):
    assert _value_row(_card(stored))[1] == stored


def test_a_row_with_no_value_prints_a_dash():
    assert _value_row(_card(None))[1] == "—"


def test_the_evidence_keeps_the_unformatted_figure_so_its_digest_does_not_move():
    card = _card("868375")
    assert card["evidence"] == {"value": "868375"}
    assert card["evidence_digest"] == judgement.evidence_digest({"value": "868375"})


def test_an_acceptance_recorded_before_the_card_was_formatted_still_reads_accepted():
    """The judgement row exactly as the store holds one written before this change: its evidence is
    the unformatted string. It must still match, and what it shows "as accepted" must read like the
    card now does."""
    card = _card("868375")
    stored = {"subject_key": card["subject_key"], "subject": card["subject"],
              "evidence": {"value": "868375"}, "reason": "Revenue on p.6; it stands.",
              "actor": "rev", "actor_role": "reviewer", "at": "2026-08-12T09:00:00",
              "run_id": "run-1"}
    served = _build_review([_unmapped("Revenue 收益", "868375")], "kaming.pdf", "en",
                           judgements=[stored])
    again = next(c for c in served["checks"] if c["subject_key"] == card["subject_key"])

    assert again["status"] == "accepted"
    assert served["judgements"]["orphaned"] == []
    assert again["judgement"]["accepted_rows"] == [["Value", "868,375"]]
    # "As accepted" and "now" print the same figure the same way.
    assert again["judgement"]["accepted_rows"][0][1] == _value_row(again)[1]


def test_a_moved_figure_still_reads_stale_and_shows_both_figures_formatted():
    card = _card("868375")
    stored = {"subject_key": card["subject_key"], "subject": card["subject"],
              "evidence": card["evidence"], "reason": "", "actor": "rev",
              "actor_role": "reviewer", "at": "2026-08-12T09:00:00", "run_id": "run-1"}
    served = _build_review([_unmapped("Revenue 收益", "868376")], "kaming.pdf", "en",
                           judgements=[stored])
    again = next(c for c in served["checks"] if c["subject_key"] == card["subject_key"])

    assert again["status"] == "stale" and again["judgement"]["changed"] == ["value"]
    assert again["judgement"]["accepted_rows"] == [["Value", "868,375"]]
    assert _value_row(again)[1] == "868,376"


@pytest.mark.parametrize("locale", ["zh", "ar", "fr"])
def test_identity_and_the_printed_figure_are_the_same_in_every_locale(locale):
    en, other = _card("868375"), _card("868375", locale)
    assert (other["subject_key"], other["evidence_digest"]) == \
        (en["subject_key"], en["evidence_digest"])
    assert "868,375" in [row[1] for row in other["calc"]]
