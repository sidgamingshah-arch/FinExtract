"""services.derivation — how a computed figure explains itself.

A figure assembled out of note datasets rather than read off a caption is not checkable on its own:
a reviewer needs the inputs and the pages they were printed on. These tests pin the folding of two
per-period cascades into one contributions list, which is where the explanation either survives or
quietly loses a period.

`services.derivation` is a READER, and that is now the whole of it. The in-app writers of these
payloads — the derivation services, each computing an output line out of a hand-enumerated list of
note titles, row captions and formula variants — are DELETED, and those figures must come from
configuration instead. The trail these tests exercise is therefore read from STORED ROWS WRITTEN BY
EARLIER RUNS: `extraction_runs.result` is a durable JSON column and the payloads in it outlive the
code that wrote them, so every reader here still has live callers and every assertion still guards
them. The `method=` strings below are opaque labels off such a stored row, not names of anything
that runs today.
"""
from __future__ import annotations

from app.services.derivation import build, input_from_evidence, merge_for_basis, record

# The `method` of a stored derivation is a free-text label an earlier run wrote into
# `extraction_runs.result`. These two are the strings the deleted derivation services left behind,
# kept verbatim because that is what the rows this reader must still fold actually contain — they
# name nothing that exists in the app today, and no assertion here depends on their spelling.
STORED_METHOD = "deprec_impairment"          # written by a run predating the removal
STORED_METHOD_FINDS = "secur_fincl_assets"   # likewise, and its Find_1/Find_2 formula with it


def _ev(dataset, note, label, value, page=None, duplicate=None):
    out = {"dataset_key": dataset, "note_number": note, "note_heading": f"Note {note}",
           "line_item": label, "value": value,
           "provenance": {"page_index": page} if page else None}
    if duplicate:
        out["duplicate_of_note"] = duplicate
    return out


def _store(current_inputs, prior_inputs=None, *, formula="P3", basis="consolidated"):
    store = record(None, basis=basis, period_label="current",
                   derivation=build(method=STORED_METHOD, formula=formula,
                                    inputs=[input_from_evidence(e) for e in current_inputs],
                                    result="500"))
    if prior_inputs is not None:
        store = record(store, basis=basis, period_label="prior",
                       derivation=build(method=STORED_METHOD, formula=formula,
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
                   derivation=build(method=STORED_METHOD_FINDS, formula="Find_1 - Find_2",
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


def test_a_provenance_model_survives_the_json_column_the_run_is_stored_in():
    """A caller may pass a Provenance MODEL, not a dict, and the derivation is stored in a JSON
    column.

    Every helper above builds provenance as a dict, which is the one shape a real writer never
    produced: the four deleted derivation services all stored ``ev.provenance`` — a pydantic
    model — across seven call sites, and the rows they wrote are still in the database for
    :func:`input_from_evidence` and :func:`merge_for_basis` to read back. The coercion is pinned
    here because the reader must survive that stored shape, and because whatever
    configuration-driven source records this payload next is free to hand over a model too.
    A model reaching ``extraction_runs.result`` ends the run, and not with a bad figure: the flush
    raises ``TypeError: Object of type Provenance is not JSON serializable``, SQLAlchemy rolls the
    transaction back, and the ``status='succeeded'`` written in that same UPDATE is rolled back
    with it. A run that completed all 21 stages is left reporting ``running`` with a null result,
    for ever. json.dumps is the assertion because the JSON column is the actual requirement.
    """
    import json

    from app.core.models.geometry import BBox, Provenance

    prov = Provenance(page_index=7, bbox=BBox(x0=0, y0=0, x1=1, y1=1),
                      text_snippet="Depreciation", source_kind="native")
    row = input_from_evidence({
        "dataset_key": "pbt_depreciation", "note_number": "7", "note_heading": "Note 7",
        "line_item": "Depreciation", "value": "529841", "provenance": prov,
    })

    assert isinstance(row["provenance"], dict), "a model would break the run's JSON column"
    assert row["provenance"]["page_index"] == 7
    json.dumps(row)  # raises TypeError if anything in here is still a model

    # …and through the fold the inspector renders, which carries provenance under other keys.
    store = record(None, basis="consolidated", period_label="current",
                   derivation=build(method=STORED_METHOD, formula="P2",
                                    inputs=[row], result="529841"))
    _, contributions = merge_for_basis(store, "consolidated")
    json.dumps(contributions)
    assert contributions[0]["src"] == "p.7", "the page label reads the coerced dict"
