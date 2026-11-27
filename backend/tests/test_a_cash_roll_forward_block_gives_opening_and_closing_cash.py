"""A CASH ROLL-FORWARD BLOCK CAPTIONED BY DATES GIVES THE OPENING AND THE CLOSING CASH.

NEW FILE -> backend/tests/test_a_cash_roll_forward_block_gives_opening_and_closing_cash.py

Moroil 2025 closes its statement of cash flows (printed page 69) on a block of its own:

    Net increase/(decrease) in cash and cash equivalents       12,091    (27,903)
    Movement in cash and cash equivalents
    At July 1,                                                (41,918)   (14,015)
    Increase/(decrease)                                         12,091    (27,903)
    At June 30,                                               (29,827)   (41,918)

The two balances are captioned by nothing but a date, so no alias names them; they and the
restated change were swept into the financing residual. Net cash used in financing activities
showed (161,552) / (190,488) against the (131,725) / (148,570) printed, opening cash was missing,
and closing cash showed (234,875) against (29,827). Phoenix Beverages 2025 (p131) prints the same
block with an exchange effect in it, and Gamma Civic 2024 (p94) captions the balances "CASH AND CASH
EQUIVALENTS AT 1 JANUARY" / "... AT 31 DECEMBER".

The balances are bound only where the block's own arithmetic proves them: opening + change (+ any
exchange effect) = closing, in every column that prints all three (`map_ontology.
cash_roll_forward_rows`). The bare "Increase/(decrease)" is read as a total and not swept
(`residual._TOTAL_CAPTION`).
"""
from __future__ import annotations

from app.stages.map_ontology import ROLL_FORWARD_FLAG

from tests.face_row_harness import face, row, run

OPENING = "cf_financing__beg_of_period_cash_and_cash_equiv"
CLOSING = "cf_financing__end_of_period_cash_and_cash_equiv"
OTHER_FINANCING = "cf_financing__other_financing_cash_flows"
FINANCING = "Financing activities"


def _moroil_block(closing_current: str = "-29827"):
    return [row("Dividends paid to Company's shareholders", "-44267", "-43264",
                section=FINANCING),
            row("Net cash used in financing activities", "-131725", "-148570", section=FINANCING),
            row("Net increase/(decrease) in cash and cash equivalents", "12091", "-27903",
                section=FINANCING),
            row("Movement in cash and cash equivalents At July 1,", "-41918", "-14015",
                section=FINANCING),
            row("Increase/(decrease)", "12091", "-27903", section=FINANCING),
            row("At June 30,", closing_current, "-41918", section=FINANCING)]


def _by_label(doc):
    return {li.source_label: li for li in doc.line_items}


def test_the_dated_balances_of_a_proved_block_are_opening_and_closing_cash():
    doc, logs = run(_moroil_block(), [face("cash_flow")])
    rows = _by_label(doc)
    assert rows["Movement in cash and cash equivalents At July 1,"].canonical_key == OPENING
    assert rows["At June 30,"].canonical_key == CLOSING
    assert f"{ROLL_FORWARD_FLAG}:{CLOSING}" in rows["At June 30,"].confidence.flags
    assert any(line.startswith("map_line_items:cash_roll_forward_bound=2") for line in logs)


def test_the_restated_change_is_not_swept_into_financing():
    doc, _ = run(_moroil_block(), [face("cash_flow")])
    change = _by_label(doc)["Increase/(decrease)"]
    assert change.canonical_key != OTHER_FINANCING
    assert any(f.startswith("residual_ineligible:total caption") for f in change.confidence.flags)


def test_a_block_whose_arithmetic_does_not_hold_binds_nothing():
    doc, _ = run(_moroil_block(closing_current="-29000"), [face("cash_flow")], residual=False)
    rows = _by_label(doc)
    assert rows["At June 30,"].canonical_key != CLOSING
    assert rows["Movement in cash and cash equivalents At July 1,"].canonical_key != OPENING


def test_an_exchange_effect_printed_before_the_block_is_part_of_its_arithmetic():
    rows = [row("NET (DECREASE)/INCREASE IN CASH AND CASH EQUIVALENTS", "-343477349", "6469910"),
            row("Net Foreign Exchange Differences", "31786899", "-9928755"),
            row("CASH AND CASH EQUIVALENTS AT 1 JANUARY", "891438925", "894897770"),
            row("CASH AND CASH EQUIVALENTS AT 31 DECEMBER 26", "579748475", "891438925")]
    doc, _ = run(rows, [face("cash_flow")], residual=False)
    got = _by_label(doc)
    assert got["CASH AND CASH EQUIVALENTS AT 1 JANUARY"].canonical_key == OPENING
    assert got["CASH AND CASH EQUIVALENTS AT 31 DECEMBER 26"].canonical_key == CLOSING
