"""A CITED TERM THAT CANNOT BE CONFIRMED IS COUNTED, NAMED AND SENT TO REVIEW — never dropped.

NEW FILE -> backend/tests/test_unverified_terms.py

WHAT THIS REPLACES, and both failures published a wrong number rather than losing one.

The model answers "this line is 500,000 less 120,000", citing two rows. One citation fails — its
caption is refused by the row-terms floor, or the amount it states is not in the note's text. The
old path filtered that entry out of `resolved` and re-ran `figures_of`:

    nothing refused   ->  380,000   correct
    B refused         ->  500,000   a PARTIAL SUM, published as the line's figure, unmarked
    A refused         -> +120,000   `signs` is POSITIONAL and was not filtered with the entries,
                                    so B moved to index 0 and took A's sign: a declared DEDUCTION
                                    published as an ADDITION

The second is the expensive one — a sign inversion on a statement — and neither said anything. The
decision is to keep every term in the model's own position, count it, and report which could not be
confirmed, so the figure is the model's whole claim and the line cannot read as settled.

TWO KINDS OF UNCONFIRMED TERM, and the difference matters to a reader:

  * REFUSED CAPTION — the row resolved, so the FIGURE IS REAL (it came off an extracted row). Only
    its identity is in doubt.
  * STATED BUT NOT FOUND — the model gave an amount that is not in the note's text. The figure
    itself is unconfirmed. This is the case that would have published the fabricated RMB 6.6 bn the
    corpus run caught, which is why the review flag is the load-bearing half of the change.

A citation that names NOTHING — no matching row and no stated amount — contributes no term at all;
`stages.line_item_llm._write_unanswered` records the answer on the row instead of writing a figure.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.line_item_llm import combine_terms


def _row(at: int, caption: str, current: str, refused: str = "") -> dict:
    entry = {"at": at, "note": "7", "caption": caption, "figures": {"current": current}}
    if refused:
        entry["row_terms_refused"] = refused
    return entry


def _stated(at: int, amount: str) -> dict:
    """A citation whose amount the note's text does not contain."""
    return {"at": at, "note": "7", "caption": "", "amount": amount,
            "why": "the amount does not appear in note 7's text"}


# ── the arithmetic ────────────────────────────────────────────────────────────────────────────

def test_a_component_sum_with_nothing_refused_is_unchanged():
    figures, unverified = combine_terms(
        [_row(0, "Depreciation charge", "500000"), _row(1, "Amortisation", "120000")],
        [], [1, -1], component=True, fallback_period="current")

    assert figures == {"current": Decimal("380000")}
    assert unverified == []


def test_a_refused_term_is_still_counted_so_the_sum_is_the_models_own():
    """THE PARTIAL-SUM DEFECT. Dropping the second term published 500,000 as the line's figure."""
    figures, unverified = combine_terms(
        [_row(0, "Depreciation charge", "500000"),
         _row(1, "Other operating expenses", "120000", refused="shares no subject word")],
        [], [1, -1], component=True, fallback_period="current")

    assert figures == {"current": Decimal("380000")}, "the refused term was dropped from the sum"
    assert len(unverified) == 1
    assert unverified[0]["caption"] == "Other operating expenses"


def test_the_sign_stays_with_the_term_the_model_gave_it():
    """THE SIGN-INVERSION DEFECT, and the reason `at` exists.

    `signs` is positional. With the FIRST term refused and the entries filtered, the survivor moved
    to index 0 and took the first sign — so a declared deduction of 120,000 published as +120,000.
    Here the refused term is the first one and the survivor must keep sign -1.
    """
    figures, unverified = combine_terms(
        [_row(0, "Other operating expenses", "500000", refused="shares no subject word"),
         _row(1, "Amortisation", "120000")],
        [], [1, -1], component=True, fallback_period="current")

    # 500,000 - 120,000, with both terms counted and the second still a DEDUCTION.
    assert figures == {"current": Decimal("380000")}
    assert len(unverified) == 1
    assert unverified[0]["sign"] == 1, "the refused term lost the sign the model gave it"


def test_a_stated_amount_the_note_does_not_contain_is_counted_at_the_fallback_period():
    """A bare amount carries no column, so it joins the period the caller names — and is reported
    unverified, because this is the case that publishes a figure nobody located."""
    figures, unverified = combine_terms(
        [_row(0, "Depreciation charge", "500000")],
        [_stated(1, "120000")], [1, -1], component=True, fallback_period="current")

    assert figures == {"current": Decimal("380000")}
    assert len(unverified) == 1
    assert unverified[0]["amount"] == "120000"


def test_a_citation_that_names_nothing_contributes_no_term():
    """No matching row and no stated amount. There is nothing to count and nothing to mark — the
    answer is recorded on the row by the caller instead."""
    figures, unverified = combine_terms(
        [_row(0, "Depreciation charge", "500000")],
        [{"at": 1, "note": "7", "caption": "Depreciation charge for the year",
          "why": "no extracted row in that note matches the caption"}],
        [1, -1], component=True, fallback_period="current")

    assert figures == {"current": Decimal("500000")}
    assert unverified == [], "a citation with no figure was reported as an unverified TERM"


# ── whole vs component ────────────────────────────────────────────────────────────────────────

def test_a_whole_answer_takes_the_first_figure_and_does_not_sum():
    """Each citation claims to BE the line's figure — a face line and the note total behind it are
    one figure printed twice. Summing them would publish a number printed nowhere."""
    figures, _unverified = combine_terms(
        [_row(0, "Total depreciation", "587417"), _row(1, "Depreciation", "587417")],
        [], [1, 1], component=False, fallback_period="current")

    assert figures == {"current": Decimal("587417")}


def test_signs_shorter_than_the_citations_default_to_addition():
    """A model that gives fewer signs than sources is not an error — the missing ones are additions,
    which is what `signs[at] if at < len(signs)` has always meant. Asserted so a later rewrite of
    the indexing cannot change it silently."""
    figures, _u = combine_terms(
        [_row(0, "A", "100"), _row(1, "B", "200"), _row(2, "C", "300")],
        [], [1], component=True, fallback_period="current")

    assert figures == {"current": Decimal("600")}
