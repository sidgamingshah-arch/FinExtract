"""services.deprec_impairment — the note-sourced priority cascade for Deprec & Impairment
(Oper Exp)/(COS). See docs/HKEX_Depreciation_Extraction_Logic_Revised.md for the spec."""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable, UnitContext
from app.services.deprec_impairment import compute


def _ev(value, basis="consolidated", period="current", currency="HKD", scale="1"):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis(basis), period_label=period,
                          unit_ctx=UnitContext(currency=currency, scale_factor=Decimal(scale)))


def _item(label, value, role=LineRole.LINE, **kw):
    it = NoteItem(raw_label=label, role=role)
    ev = _ev(value, **kw)
    it.values[ev.key.model_dump_json()] = ev
    return it


def _note(number, title, items, source_text=""):
    return NotesTable(note_number=number, title=title, items=items, source_text=source_text)


def _doc(*notes):
    return DocumentModel(notes=list(notes))


def test_p1_sums_direct_operating_expense_notes():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("29", "Selling and distribution expenses",
              [_item("Depreciation of property, plant and equipment", "50")]),
    )
    results = compute(doc)
    result = results[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("150")
    assert result.priority_used == "P1"
    assert result.status == "EXTRACTED_AND_COMPUTED"


def test_p2_is_the_pbt_notes_opex_specific_callout():
    doc = _doc(
        _note("6", "Profit before taxation",
              [_item("Depreciation of property, plant and equipment", "80")],
              source_text="Depreciation included in operating expenses of HK$80,000."),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("80")
    assert result.priority_used == "P2"


def test_p3_is_pbt_depreciation_minus_cos_depreciation():
    doc = _doc(
        _note("6", "Profit before taxation is arrived at after charging",
              [_item("Depreciation of property, plant and equipment", "500")]),
        _note("7", "Cost of sales",
              [_item("Depreciation of property, plant and equipment", "300")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("200")
    assert result.priority_used == "P3"


def test_negative_candidate_is_skipped_not_zeroed():
    doc = _doc(
        _note("6", "Profit before taxation",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("7", "Cost of sales",
              [_item("Depreciation of property, plant and equipment", "300")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value is None
    assert "NEGATIVE_RESIDUAL" in result.flags
    assert result.status == "NOT_FOUND_OR_NOT_COMPUTABLE"


def test_cos_p1_is_direct_and_p2_uses_final_oper_exp():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("7", "Cost of sales",
              [_item("Depreciation of property, plant and equipment", "300")]),
    )
    results = compute(doc)[("consolidated", "current")]
    assert results["cos"].value == Decimal("300")
    assert results["cos"].priority_used == "COS_P1"
    assert results["cos"].status == "DIRECTLY_EXTRACTED"


def test_cos_p2_falls_back_to_pbt_minus_oper_exp_final():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("6", "Profit before taxation is arrived at after charging",
              [_item("Depreciation of property, plant and equipment", "400")]),
    )
    results = compute(doc)[("consolidated", "current")]
    assert results["oper_exp"].value == Decimal("100")
    assert results["cos"].value == Decimal("300")
    assert results["cos"].priority_used == "COS_P2"


def test_combined_depreciation_and_amortisation_line_is_excluded():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation and amortisation", "999")]),
    )
    result = compute(doc)[("consolidated", "current")] if compute(doc) else None
    assert not result or result["oper_exp"].value is None


def test_movement_reconciliation_rows_are_excluded_from_asset_notes():
    doc = _doc(
        _note("15", "Property, plant and equipment", [
            _item("At 1 January", "1000"),
            _item("Depreciation charge for the year", "60"),
            _item("Disposals", "20"),
        ]),
        _note("7", "Cost of sales", [_item("Depreciation of property, plant and equipment", "10")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("50")
    assert result.priority_used == "P4"


def test_mixed_currency_within_a_dataset_is_not_used():
    doc = _doc(
        _note("28", "Administrative expenses", [
            _item("Depreciation of property, plant and equipment", "100", currency="HKD"),
            _item("Depreciation of property, plant and equipment", "50", currency="USD"),
        ]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value is None
    assert any(f.startswith("UNIT_MISMATCH") for f in result.flags)


def _item2(label, current, prior, role=LineRole.LINE, **kw):
    it = NoteItem(raw_label=label, role=role)
    for period, value in (("current", current), ("prior", prior)):
        ev = _ev(value, period=period, **kw)
        it.values[ev.key.model_dump_json()] = ev
    return it


def test_explicit_opex_inclusion_callout_overrides_item_sum():
    doc = _doc(
        _note("7", "LOSS FROM OPERATING ACTIVITIES", [
            _item2("Depreciation of property, plant and equipment", "306456", "364968"),
            _item2("Depreciation of right-of-use assets", "280961", "370867"),
        ], source_text=(
            "Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
            "included in \u201cother operating expenses\u201d on the face of the consolidated "
            "income statement.")),
    )
    current = compute(doc)[("consolidated", "current")]
    assert current["oper_exp"].value == Decimal("529841")
    assert current["oper_exp"].priority_used == "P2"
    assert current["cos"].value == Decimal("306456") + Decimal("280961") - Decimal("529841")
    assert current["cos"].priority_used == "COS_P2"
    prior = compute(doc)[("consolidated", "prior")]
    assert prior["oper_exp"].value == Decimal("665553")


def test_no_notes_yields_no_periods():
    assert compute(_doc()) == {}


# ── the agreed cascade: P1 needs no cost-of-sales figure, and P3-P5 assume a zero deduction
#    when the filing discloses no cost-of-sales depreciation ──────────────────────────────────
def test_p1_wins_without_any_cost_of_sales_note():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "100")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("100")
    assert result.priority_used == "P1"


def test_p3_takes_the_whole_pbt_total_when_no_cost_of_sales_depreciation_is_disclosed():
    # No cost-of-sales note anywhere: the deduction is zero, so the candidate reduces to the
    # PBT note's own total rather than becoming incomputable.
    doc = _doc(
        _note("6", "Profit before taxation",
              [_item("Depreciation of property, plant and equipment", "700")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("700")
    assert result.priority_used == "P3"
    assert "ASSUMED_ZERO_COS_DEPRECIATION" in result.flags


def test_the_assumed_zero_is_reported_and_not_asserted_when_a_cos_note_exists():
    doc = _doc(
        _note("6", "Profit before taxation",
              [_item("Depreciation of property, plant and equipment", "700")]),
        _note("7", "Cost of sales", [_item("Depreciation", "200")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("500")
    assert "ASSUMED_ZERO_COS_DEPRECIATION" not in result.flags


def test_p4_assumes_a_zero_deduction_too():
    doc = _doc(
        _note("14", "Property, plant and equipment",
              [_item("Depreciation charge for the year", "310")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("310")
    assert result.priority_used == "P4"


def test_p5_assumes_a_zero_deduction_too():
    doc = _doc(
        _note("31", "Cash generated from operations", [_item("Depreciation", "410")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("410")
    assert result.priority_used == "P5"


def test_cos_is_the_remainder_once_oper_exp_took_the_whole_pbt_total():
    # P3 having claimed the entire PBT total, the COS remainder is zero — the arithmetic the
    # assumed-zero deduction implies, reached without a second special case.
    doc = _doc(
        _note("6", "Profit before taxation",
              [_item("Depreciation of property, plant and equipment", "700")]),
    )
    result = compute(doc)[("consolidated", "current")]["cos"]
    assert result.value == Decimal("0")
    assert result.priority_used == "COS_P2"


def test_a_negative_p1_falls_through_to_the_next_priority_rather_than_ending_the_cascade():
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "-40")]),
        _note("6", "Profit before taxation",
              [_item("Depreciation of property, plant and equipment", "90")],
              source_text="Depreciation included in operating expenses of HK$90,000."),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("90")
    assert result.priority_used == "P2"
    assert "NEGATIVE_RESIDUAL" in result.flags


# ── duplicate control (§3.1 bilingual printings, §3.4 restatement across notes) ───────────────
def test_the_same_figure_restated_in_a_second_note_is_not_added_twice():
    # The English and Traditional Chinese printings of one administrative-expenses note.
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "100")]),
        _note("28A", "行政開支", [_item("物業、廠房及設備折舊", "100")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("100")
    assert any(f.startswith("POSSIBLE_DUPLICATE:") for f in result.flags)


def test_two_lines_of_one_note_charging_the_same_amount_are_both_counted():
    # Not a restatement: one note legitimately charging two asset classes the same amount.
    doc = _doc(
        _note("28", "Administrative expenses",
              [_item("Depreciation of property, plant and equipment", "60"),
               _item("Depreciation of investment property", "60")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("120")
    assert not any(f.startswith("POSSIBLE_DUPLICATE:") for f in result.flags)


def test_a_fixed_assets_parent_note_is_dropped_when_its_components_are_also_disclosed():
    # §4.4: adding the "Fixed assets" charge to the PPE charge it contains double counts it.
    doc = _doc(
        _note("14", "Property, plant and equipment",
              [_item("Depreciation charge for the year", "300")]),
        _note("15", "Fixed assets", [_item("Depreciation charge for the year", "500")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("300")
    assert result.priority_used == "P4"
    assert "TOTAL_COMPONENT_OVERLAP:fixed_asset_depreciation" in result.flags


def test_a_fixed_assets_note_stands_alone_when_no_component_note_is_disclosed():
    doc = _doc(
        _note("15", "Fixed assets", [_item("Depreciation charge for the year", "500")]),
    )
    result = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert result.value == Decimal("500")
    assert "TOTAL_COMPONENT_OVERLAP:fixed_asset_depreciation" not in result.flags


# --- the trace back to the source --------------------------------------------------------------
# A figure assembled from note datasets is only as good as what it can be checked against. These
# pin the two things the inspector needs from the evidence trail and used not to get: the filing's
# own words for an input read out of PROSE, and which input the winning priority SUBTRACTED.


def _inputs(result):
    from app.services.derivation import input_from_evidence

    return [input_from_evidence(e) for e in result.evidence]


CALLOUT = ("Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
           "included in “other operating expenses” on the face of the consolidated "
           "income statement.")


def _callout_doc():
    return _doc(_note("7", "LOSS FROM OPERATING ACTIVITIES", [
        _item2("Depreciation of property, plant and equipment", "306456", "364968"),
        _item2("Depreciation of right-of-use assets", "280961", "370867"),
    ], source_text=CALLOUT))


def test_a_callout_read_out_of_prose_carries_the_sentence_it_was_read_from():
    """The one input with no printed row to point at.

    Every other input is a table row the derivation locates by note, caption and page. This one
    was recorded under the internal name of the rule that found it — "explicit opex-inclusion
    callout" — which gave the reviewer a page number and nothing on the page to check.
    """
    oper = compute(_callout_doc())[("consolidated", "current")]["oper_exp"]

    callout = next(i for i in _inputs(oper) if i["value"] == "529841")
    assert callout["label"] == "Depreciation stated as included in operating expenses"
    assert "HK$529,841,000" in callout["excerpt"]
    assert "included in" in callout["excerpt"]
    assert callout["excerpt"].startswith("Depreciation charges of approximately")


def test_a_table_row_input_carries_no_excerpt():
    """The caption IS the trace for a printed row; a sentence would be noise beside it."""
    doc = _doc(
        _note("6", "Profit before taxation is arrived at after charging",
              [_item("Depreciation of property, plant and equipment", "500")]),
        _note("7", "Cost of sales",
              [_item("Depreciation of property, plant and equipment", "300")]),
    )

    oper = compute(doc)[("consolidated", "current")]["oper_exp"]

    assert all(i["excerpt"] is None for i in _inputs(oper))


def test_the_cost_of_sales_share_a_priority_subtracts_is_marked_deducted():
    """P3 is "profit-before-tax depreciation − cost-of-sales depreciation"."""
    doc = _doc(
        _note("6", "Profit before taxation is arrived at after charging",
              [_item("Depreciation of property, plant and equipment", "500")]),
        _note("7", "Cost of sales",
              [_item("Depreciation of property, plant and equipment", "300")]),
    )

    oper = compute(doc)[("consolidated", "current")]["oper_exp"]
    assert oper.priority_used == "P3" and oper.value == Decimal("200")

    by_value = {i["value"]: i for i in _inputs(oper)}
    assert by_value["500"]["deducted"] is False
    assert by_value["300"]["deducted"] is True


def test_nothing_is_marked_deducted_when_the_priority_subtracts_nothing():
    """P2 is a single callout, and an assumed-zero deduction has no evidence to mark."""
    oper = compute(_callout_doc())[("consolidated", "current")]["oper_exp"]

    assert oper.priority_used == "P2"
    assert all(i["deducted"] is False for i in _inputs(oper))


def test_cos_p2_reverses_the_role_of_every_operating_expense_input():
    """COS_P2 is `pbt depreciation − Deprec & Impairment (Oper Exp)`.

    So what Oper Exp added, COS subtracts. Without this the inspector listed three positive
    figures — 306,456 + 280,961 + 529,841 — under a total of 57,576.
    """
    cos = compute(_callout_doc())[("consolidated", "current")]["cos"]
    assert cos.priority_used == "COS_P2" and cos.value == Decimal("57576")

    by_value = {i["value"]: i for i in _inputs(cos)}
    assert by_value["306456"]["deducted"] is False
    assert by_value["280961"]["deducted"] is False
    assert by_value["529841"]["deducted"] is True


def test_the_inputs_the_inspector_lists_add_up_to_the_figure_above_them():
    """The whole point of the trail: a reviewer can add the column and get the row.

    Read through `merge_for_basis`, which is what the statement inspector calls, so what is
    checked is the arithmetic the analyst actually sees.
    """
    from app.services.derivation import build, input_from_evidence, merge_for_basis

    doc = _callout_doc()
    both = compute(doc)
    store = {}
    for period in ("current", "prior"):
        result = both[("consolidated", period)]["cos"]
        store[f"consolidated:{period}"] = build(
            method="deprec_impairment", formula=result.priority_used,
            inputs=[input_from_evidence(e) for e in result.evidence], result=result.value)

    _, contributions = merge_for_basis(store, "consolidated")

    assert sum(c["v1"] for c in contributions) == 57576
    assert sum(c["v2"] for c in contributions) == float(
        both[("consolidated", "prior")]["cos"].value)
