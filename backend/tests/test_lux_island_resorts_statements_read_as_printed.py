"""LUX ISLAND RESORTS 2025: ITS STATEMENTS, ONCE FOUND, ARE READ AS PRINTED.

Once the three-line titles made sheets 62-64 faces (test_mauritian_statement_pages_are_found_and_
front_matter_is_not.py), six reading defects were left on them, each pinned here (Rs'000):

* "Notes" set on the baseline of "THE GROUP" / "THE COMPANY", with the year row folded under it,
  was read as a row "Notes THE GROUP THE COMPANY" 2025 / 2024 on all four statement halves and
  swept into the residuals (`row_reconstruct._is_basis_caption_row`).
* A footnote letter after a caption — "Income tax (expense)/ credit (a)", "Payments of long term
  borrowings (c)" — matched no alias, and the tax charge 246,682 / 238,079 was swept into the
  operating costs (`mapping.label_segments`, the last spelling tried).
* The OCI blocks are headed "Other comprehensive income that will not be reclassified" / "to profit
  or loss subsequently", and each heading was folded onto the row under it: the revaluation gain
  384,540 / (129,336) and the hedge movement (47,237) / (64,461) reached no line
  (`row_reconstruct._OCI_BLOCK_HEADING`).
* "Cash and cash equivalents at 30 June (a)" closed the cash roll-forward block with a footnote
  letter, so the block went unproved and the opening cash and the exchange effect were swept into
  financing (`map_ontology._BARE_DATE_CAPTION`).
* "Depreciation and amortisation" on the cash flow bound the internal part that feeds the income
  statement's depreciation, not the cash flow's own line — whose definition names that wording —
  so the operating cash flow computed 812,155 short (`cf_oper_indirect__depreciation` aliases).
* "Effects of exchange rate changes on cash and cash equivalents" (plural) matched no alias.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.enums import Basis


@pytest.mark.parametrize("label, vals, expected", [
    ("Notes THE GROUP THE COMPANY", [Decimal(2025), Decimal(2024), Decimal(2025), Decimal(2024)],
     True),
    ("THE GROUP THE COMPANY", [Decimal(2025), Decimal(2024)], True),
    ("Group Company", [Decimal(2024), Decimal(2023)], True),
    ("Notes", [Decimal(2025), Decimal(2024)], False),           # no entity named: not this rule
    ("The Group and the Company had no contingent liabilities", [Decimal(2025)], False),
    ("Notes THE GROUP THE COMPANY", [Decimal(13617744), Decimal(12946383)], False),
])
def test_the_entity_band_with_the_note_header_beside_it_is_furniture(label, vals, expected):
    from app.services.row_reconstruct import _is_basis_caption_row

    # The shipped rulebook's `entity_scope.signals` ("Group", "Company"), as `_entity_signals`
    # reduces them.
    signals = ((Basis.CONSOLIDATED, "group"), (Basis.STANDALONE, "company"))
    assert _is_basis_caption_row(label, vals, signals) is expected


@pytest.mark.parametrize("caption, key", [
    ("Income tax (expense)/ credit (a)", "is_pl__total_income_tax"),
    ("Income tax (expense)/ credit", "is_pl__total_income_tax"),
])
def test_a_footnote_letter_after_a_caption_does_not_hide_it(caption, key):
    from tests.face_row_harness import face, row, run

    rows = [row(caption, "-246682", "-238079", flags=("printed_on:profit_and_loss",))]
    doc, _ = run(rows, [face("profit_and_loss")], residual=False)
    assert doc.line_items[0].canonical_key == key


def test_only_a_trailing_lower_case_letter_or_numeral_is_a_footnote_marker():
    from app.services.mapping import label_segments

    assert "Impairment of financial assets" in label_segments(
        "Impairment of financial assets (iii)")
    assert label_segments("Net cash flow from operating activities (A)") == [
        "Net cash flow from operating activities (A)"]
    assert label_segments("Pledged deposits (note (b)) for loans") == [
        "Pledged deposits (note (b)) for loans"]


@pytest.mark.parametrize("text, heading", [
    ("Other comprehensive income that will not be reclassified to profit or loss subsequently",
     True),
    ("Other comprehensive income that may be reclassified to profit or loss subsequently", True),
    ("Items that will not be reclassified to profit or loss", True),
    ("Other comprehensive income that will not be reclassified", False),   # half a heading
    ("Other comprehensive income for the year", False),
])
def test_the_ifrs_oci_block_heading_is_a_heading(text, heading):
    from app.services.row_reconstruct import _is_face_group_heading

    assert _is_face_group_heading(text) is heading


@pytest.mark.parametrize("caption", [
    "Cash and cash equivalents at 30 June (a)", "Cash and cash equivalents at 01 July",
    "At June 30,", "CASH AND CASH EQUIVALENTS AT 31 DECEMBER"])
def test_a_dated_cash_balance_with_a_footnote_letter_is_a_balance(caption):
    from app.stages.map_ontology import _BARE_DATE_CAPTION, _MOVEMENT_IN_CASH

    assert _BARE_DATE_CAPTION.match(_MOVEMENT_IN_CASH.sub("", caption))


def test_luxs_cash_roll_forward_block_gives_opening_and_closing_cash():
    from tests.face_row_harness import face, row, run

    rows = [row("Net increase/ (decrease) in cash and cash equivalents", "458327", "-54168",
                page=64),
            row("Cash and cash equivalents at 01 July", "1044915", "1095121", page=64),
            row("Effects of exchange rate changes on cash and cash equivalents", "1573", "3962",
                page=64),
            row("Cash and cash equivalents at 30 June (a)", "1504815", "1044915", page=64)]
    for li in rows:
        li.confidence.flags.append("printed_on:cash_flow")
    run(rows, [face("cash_flow", 64)])
    keys = [li.canonical_key for li in rows]
    assert keys[1] == "cf_financing__beg_of_period_cash_and_cash_equiv", keys
    assert keys[2] == "cf_financing__net_foreign_exchange_difference", keys
    assert keys[3] == "cf_financing__end_of_period_cash_and_cash_equiv", keys


@pytest.mark.parametrize("caption, key", [
    ("Depreciation and amortisation", "cf_oper_indirect__depreciation"),
    ("- Depreciation and amortisation", "cf_oper_indirect__depreciation"),
    ("Acquisition of property, plant and equipment (b)", "cf_investing__purchase_ppe"),
    ("Payments of long term borrowings (c)", "cf_financing__repayments_non_cur_borrowings"),
    ("Effects of exchange rate changes on cash and cash equivalents",
     "cf_financing__net_foreign_exchange_difference"),
])
def test_luxs_cash_flow_captions_bind_their_lines(caption, key):
    from tests.face_row_harness import face, row, run

    rows = [row(caption, "812155", "743405", flags=("printed_on:cash_flow",))]
    doc, _ = run(rows, [face("cash_flow")], residual=False)
    assert doc.line_items[0].canonical_key == key


def test_the_income_statement_depreciation_still_reads_the_cash_flow_add_back():
    """Rung P6 of Deprec & Impairment (Oper Exp) reads the cash flow's own depreciation line, so
    moving the combined wording from the part to the line keeps the P&L figure (Laurus, Lux)."""
    import json
    import pathlib

    seed = (pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
            / "output_csv_hk_line_items.json")
    items = {i["key"]: i for i in json.loads(seed.read_text(encoding="utf-8"))["items"]}
    rungs = {r["id"]: [t["ref"] for t in r["terms"]]
             for r in items["is_pl__deprec_and_impairment_oper_exp"]["cascade"]}
    assert rungs["P6"][0] == "cf_oper_indirect__depreciation"
    assert Basis.CONSOLIDATED
