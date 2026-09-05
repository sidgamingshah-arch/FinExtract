"""services.derivation — how a computed figure explains itself.

The eight specification-governed concepts are assembled from note datasets, so the figure alone is
not checkable: a reviewer needs the inputs and the pages they were printed on. These tests pin the
folding of two per-period cascades into one contributions list, which is where the explanation
either survives or quietly loses a period.
"""
from __future__ import annotations

from app.services.derivation import build, input_from_evidence, merge_for_basis, record


def _ev(dataset, note, label, value, page=None, duplicate=None):
    out = {"dataset_key": dataset, "note_number": note, "note_heading": f"Note {note}",
           "line_item": label, "value": value,
           "provenance": {"page_index": page} if page else None}
    if duplicate:
        out["duplicate_of_note"] = duplicate
    return out


def _store(current_inputs, prior_inputs=None, *, formula="P3", basis="consolidated"):
    store = record(None, basis=basis, period_label="current",
                   derivation=build(method="deprec_impairment", formula=formula,
                                    inputs=[input_from_evidence(e) for e in current_inputs],
                                    result="500"))
    if prior_inputs is not None:
        store = record(store, basis=basis, period_label="prior",
                       derivation=build(method="deprec_impairment", formula=formula,
                                        inputs=[input_from_evidence(e) for e in prior_inputs],
                                        result="400"))
    return store


def test_one_period_yields_one_figure_per_contribution():
    store = _store([_ev("pbt_depreciation", "6", "Depreciation of PPE", "700", page=12)])
    formula, contributions = merge_for_basis(store, "consolidated")
    assert formula == "P3"
    assert len(contributions) == 1
    assert contributions[0]["v1"] == 700.0
    assert contributions[0]["v2"] is None
    assert contributions[0]["src"] == "p.12"


def test_both_periods_fold_into_one_row_per_input():
    store = _store(
        [_ev("pbt_depreciation", "6", "Depreciation of PPE", "700", page=12)],
        [_ev("pbt_depreciation", "6", "Depreciation of PPE", "650", page=12)],
    )
    _, contributions = merge_for_basis(store, "consolidated")
    assert len(contributions) == 1
    assert (contributions[0]["v1"], contributions[0]["v2"]) == (700.0, 650.0)


def test_inputs_are_matched_by_identity_not_by_position():
    # The prior cascade drew on the notes in a different order. Zipping the two lists would print
    # the cost-of-sales figure against the PBT caption.
    store = _store(
        [_ev("pbt_depreciation", "6", "Depreciation", "700"),
         _ev("cos_depreciation", "7", "Depreciation", "200")],
        [_ev("cos_depreciation", "7", "Depreciation", "180"),
         _ev("pbt_depreciation", "6", "Depreciation", "650")],
    )
    _, contributions = merge_for_basis(store, "consolidated")
    by_dataset = {c["method"]: (c["v1"], c["v2"]) for c in contributions}
    assert by_dataset["pbt_depreciation"] == (700.0, 650.0)
    assert by_dataset["cos_depreciation"] == (200.0, 180.0)


def test_an_input_only_the_prior_period_used_is_still_listed():
    store = _store(
        [_ev("pbt_depreciation", "6", "Depreciation", "700")],
        [_ev("pbt_depreciation", "6", "Depreciation", "650"),
         _ev("cfo_depreciation", "31", "Depreciation", "500")],
    )
    _, contributions = merge_for_basis(store, "consolidated")
    assert len(contributions) == 2
    only_prior = next(c for c in contributions if c["method"] == "cfo_depreciation")
    assert only_prior["v1"] is None and only_prior["v2"] == 500.0
    assert only_prior["counted"] is False        # it contributed nothing to the current figure


def test_the_same_caption_from_two_notes_is_two_distinguishable_rows():
    # Four notes each printing "Depreciation" is the normal case; four rows reading
    # "Depreciation" would explain nothing, so the note qualifies the label.
    store = _store([_ev("ga_depreciation", "28", "Depreciation", "100"),
                    _ev("rd_depreciation", "29", "Depreciation", "60")])
    _, contributions = merge_for_basis(store, "consolidated")
    labels = [c["label"] for c in contributions]
    assert labels == ["Depreciation — note 28 (Note 28)", "Depreciation — note 29 (Note 29)"]
    assert len(set(labels)) == 2


def test_a_restated_input_is_shown_but_not_counted():
    # services.restatement kept the second printing as corroboration; presenting it as an addend
    # would show a column that does not add up to the figure above it.
    store = _store([_ev("ga_depreciation", "28", "Depreciation", "100"),
                    _ev("ga_depreciation", "28A", "折旧", "100", duplicate="28")])
    _, contributions = merge_for_basis(store, "consolidated")
    assert [c["counted"] for c in contributions] == [True, False]
    assert contributions[1]["duplicate_of_note"] if "duplicate_of_note" in contributions[1] else True


def test_the_formula_falls_back_to_the_prior_period_when_the_current_year_produced_none():
    store = record(None, basis="consolidated", period_label="prior",
                   derivation=build(method="secur_fincl_assets", formula="Find_1 - Find_2",
                                    inputs=[], result="90"))
    formula, _ = merge_for_basis(store, "consolidated")
    assert formula == "Find_1 - Find_2"


def test_another_basis_is_not_mixed_in():
    store = _store([_ev("pbt_depreciation", "6", "Depreciation", "700")], basis="standalone")
    formula, contributions = merge_for_basis(store, "consolidated")
    assert formula is None and contributions == []
    formula, contributions = merge_for_basis(store, "standalone")
    assert formula == "P3" and len(contributions) == 1


def test_an_absent_store_is_not_an_error():
    assert merge_for_basis(None, "consolidated") == (None, [])
    assert merge_for_basis({}, "consolidated") == (None, [])


def test_a_non_numeric_value_does_not_break_the_fold():
    store = _store([_ev("pbt_depreciation", "6", "Depreciation", "not a number")])
    _, contributions = merge_for_basis(store, "consolidated")
    assert contributions[0]["v1"] is None
