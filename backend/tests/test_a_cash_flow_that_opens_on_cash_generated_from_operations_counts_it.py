"""A CASH FLOW STATEMENT THAT OPENS ON "CASH GENERATED FROM OPERATIONS" COUNTS THAT ROW.

NEW FILE -> backend/tests/test_a_cash_flow_that_opens_on_cash_generated_from_operations_counts_it.py

The operating residual's `exclude_hints` refuse "Cash generated from operations": on a face that
prints the reconciliation of profit to operating cash, the row is the TOTAL of the rows above it,
and sweeping it as well would count that reconciliation twice. Moroil 2025 (printed page 69) and
Phoenix Beverages 2025 (p131) print the reconciliation in a note instead (32(a), 31(a)) and OPEN
the statement on that row, so it is the only figure the face gives for it. Refused, it left the
section short by exactly its amount: Moroil's net cash from operating activities was rebuilt from
the interest and tax lines alone, (28,301) / (31,175) against the 188,838 / 168,271 printed, and
Phoenix Beverages' showed (442,347) / (209,023) against 1,666,269 / 1,998,159.

The row is swept when it is the first figure its statement prints (`residual._opens_its_statement`).
China SCE 1966, Lai Sun and BYD Electronic print the reconciliation on the face above the same
caption, and it stays refused there.
"""
from __future__ import annotations

from app.stages.residual import OPENS_ITS_STATEMENT_FLAG

from tests.face_row_harness import face, row, run

OTHER_NON_CASH = "cf_oper_indirect__other_non_cash_adjs_oper"
OPERATING = "Operating activities"


def _moroil_operating(page: int = 1):
    return [row("Cash generated from operations", "217139", "199446", page=page,
                section=OPERATING),
            row("Interest paid", "-26946", "-29272", page=page, section=OPERATING),
            row("Tax paid - net", "-1137", "-1401", page=page, section=OPERATING),
            row("Tax refund", "281", "131", page=page, section=OPERATING),
            row("CSR paid", "-499", "-633", page=page, section=OPERATING),
            row("Net cash generated from operating activities", "188838", "168271", page=page,
                section=OPERATING)]


def test_the_row_a_statement_opens_on_is_swept_into_the_operating_section():
    rows = [row("Profit for the year", "78447", "84313", page=0)] + _moroil_operating()
    doc, _ = run(rows, [face("profit_and_loss", 0), face("cash_flow", 1)])
    cgo = next(li for li in doc.line_items if li.source_label == "Cash generated from operations")
    assert cgo.canonical_key == OTHER_NON_CASH
    assert any(f.startswith(OPENS_ITS_STATEMENT_FLAG) for f in cgo.confidence.flags)


def test_the_same_caption_under_a_reconciliation_printed_above_it_stays_refused():
    rows = [row("Profit before income tax", "641346", "623278", page=1, section=OPERATING),
            row("Depreciation on property, plant and equipment", "176813", "173482", page=1,
                section=OPERATING),
            row("Decrease in trade and other payables", "-188700", "-87749", page=1,
                section=OPERATING)] + _moroil_operating()
    doc, _ = run(rows, [face("cash_flow", 1)])
    cgo = next(li for li in doc.line_items if li.source_label == "Cash generated from operations")
    assert cgo.canonical_key != OTHER_NON_CASH
    assert not any(f.startswith(OPENS_ITS_STATEMENT_FLAG) for f in cgo.confidence.flags)
