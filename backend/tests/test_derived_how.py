r"""A DERIVED LINE PUBLISHES WHICH RUNG COMPUTED IT, AND WHAT THAT RUNG COULD NOT INCLUDE.

NEW FILE -> backend/tests/test_derived_how.py

THE GAP, measured on 2025041600195 through the deterministic pipeline. The workbook's "Line Items"
sheet already publishes everything a derived figure is built from: `Deprec & Impairment(Oper Exp)`
carries the live formula `=E292+E293` and the two contributing items sit beneath it as their own
rows, `PP&E note — depreciation` 566,457 and `Prepaid lease payments note — amortisation` 77,707.
What the sheet did NOT say is which of the author's five rungs produced that, or what the rung left
out — the "How" column was EMPTY on exactly those lines.

WHY IT WAS EMPTY, and it is not an oversight in the sheet. "How" renders `mapping_method`, which is
`LineItem.confidence.method` — a `MappingMethod`, whose every member names a way a printed CAPTION
was matched to a concept (exact, rule, fuzzy, embedding, llm, unmatched). A cascade-computed line
was never matched from a caption, so the field is correctly None. The rung was recorded all along,
in the derivation trail, as `method: "cascade:P4"` with `rung:P4` among its flags.

WHY THE MISSING-TERM COUNT IS THE HALF THAT MATTERS. That P4 resolved with `terms_missing:4`, and
one of the four absent terms is the cost-of-sales DEDUCTION: `sub__cos_depreciation` is role
`adjustment` in P3, P4 and P5, so its absence does not fail the rung — the rung resolves WITHOUT
the deduction and publishes a larger charge. The figure is honest and the formula beside it is
honest, and neither can say that. `stages/note_sourced.py` records the reproduction: 587,417
published where 529,841 was correct, a 57,576 overstatement whose only trace was this flag.
"""
from __future__ import annotations

from app.services.export_line_items import _derived_how


def _row(store: dict) -> dict:
    return {"derivation": store}


def test_the_rung_that_computed_a_line_is_published():
    """The method names the rung, so a reader can find it in the configuration."""
    row = _row({"consolidated:current": {"method": "cascade:P4", "formula": "a + b",
                                         "result": "644164", "inputs": [], "flags": ["rung:P4"]}})
    # `rung:P4` is dropped as a flag because the method already says P4 — printing both would
    # render "cascade:P4 · rung:P4".
    assert _derived_how(row, "consolidated") == "cascade:P4"


def test_a_rung_that_resolved_without_a_declared_term_says_so():
    """THE POINT OF THE WHOLE CHANGE. An `adjustment` term's absence is invisible in the figure and
    invisible in the formula; it is visible only here."""
    row = _row({"consolidated:current": {
        "method": "cascade:P4", "formula": "sub__ppe_depreciation + sub__prepaid_lease_depreciation",
        "result": "644164", "inputs": [], "flags": ["rung:P4", "terms_missing:4"]}})
    assert _derived_how(row, "consolidated") == "cascade:P4 · terms_missing:4"


def test_a_passed_over_rung_and_a_displaced_printed_figure_both_travel():
    """Two more qualifications a reader needs, and both were already recorded.

    `rungs_refused` means an earlier rung computed below zero and was skipped — "P3 computed -50,
    so P4 was used" is a different provenance from "P4 was the first rung with its inputs present".
    `displaced_printed` means the cascade overrode a figure the filing itself printed, which is the
    single most important thing to disclose about a derived number.
    """
    row = _row({"consolidated:current": {
        "method": "cascade:LTP_P1", "formula": "x - y", "result": "788507", "inputs": [],
        "flags": ["rung:LTP_P1", "rungs_refused:2", "displaced_printed:128412"]}})
    assert _derived_how(row, "consolidated") == (
        "cascade:LTP_P1 · rungs_refused:2 · displaced_printed:128412")


def test_flags_that_do_not_qualify_the_figure_are_left_out():
    """The column is one cell wide and shares a row with the number. Only the flags that change how
    the figure should be read belong in it; the rest stay in the trail the inspector renders."""
    row = _row({"consolidated:current": {
        "method": "cascade:P1", "formula": "a", "result": "10", "inputs": [],
        "flags": ["rung:P1", "note_sourced_prose", "grid_two_level_header", "some_future_flag"]}})
    assert _derived_how(row, "consolidated") == "cascade:P1"


def test_the_current_period_is_preferred_and_the_prior_is_the_fallback():
    """A line with only last year's figure should still explain that figure — the same rule
    `derivation.merge_for_basis` already applies to the formula."""
    both = _row({"consolidated:current": {"method": "cascade:P2", "flags": []},
                 "consolidated:prior": {"method": "cascade:P5", "flags": []}})
    assert _derived_how(both, "consolidated") == "cascade:P2"
    prior_only = _row({"consolidated:prior": {"method": "cascade:P5", "flags": []}})
    assert _derived_how(prior_only, "consolidated") == "cascade:P5"


def test_the_basis_is_respected_so_one_column_does_not_explain_another():
    """A standalone figure is not explained by the consolidated rung. `contributions_of` returns the
    basis its block belongs to for the same reason: writing one basis's amounts into every value
    column invented a standalone disclosure the filing never made."""
    row = _row({"consolidated:current": {"method": "cascade:P1", "flags": []},
                "standalone:current": {"method": "cascade:P4", "flags": ["terms_missing:2"]}})
    assert _derived_how(row, "consolidated") == "cascade:P1"
    assert _derived_how(row, "standalone") == "cascade:P4 · terms_missing:2"


def test_a_slot_under_this_basis_is_used_when_neither_period_is_named():
    """Real slots are not only `current` and `prior`: a movement note's columns reach the trail
    under their own captions, so a basis match has to fall back to any slot it owns."""
    row = _row({"consolidated:Retained profits": {"method": "cascade:CP_INTERMEDIATE",
                                                  "flags": ["terms_missing:9"]}})
    assert _derived_how(row, "consolidated") == "cascade:CP_INTERMEDIATE · terms_missing:9"


def test_a_row_with_no_derivation_publishes_nothing_here():
    """A figure read off a caption needs no explanation beyond its provenance, and the column must
    stay empty rather than inventing one — `mapping_method` is what fills it for those rows."""
    assert _derived_how(None, "consolidated") == ""
    assert _derived_how({}, "consolidated") == ""
    assert _derived_how({"derivation": {}}, "consolidated") == ""
    assert _derived_how({"derivation": None}, "consolidated") == ""


def test_a_trail_with_no_method_publishes_nothing_rather_than_a_bare_separator():
    """Guarding the join, not just the lookup: a trail entry with flags but no method would
    otherwise render " · terms_missing:1", which reads as a corrupt cell."""
    row = _row({"consolidated:current": {"method": "", "flags": ["terms_missing:1"]}})
    assert _derived_how(row, "consolidated") == ""


def test_a_missing_basis_still_explains_the_figure():
    """`contributions_of` returns None for the basis when a line has no contribution block at all.
    The line may still be cascade-computed — a rung over a `const`, or over lines whose own inputs
    are not note rows — and the column should say so rather than go blank on a technicality."""
    row = _row({"consolidated:current": {"method": "cascade:CP_ZERO",
                                         "flags": ["rung:CP_ZERO", "rungs_refused:1"]}})
    assert _derived_how(row, None) == "cascade:CP_ZERO · rungs_refused:1"
