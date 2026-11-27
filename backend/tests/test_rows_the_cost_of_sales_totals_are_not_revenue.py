"""THE FACE'S REVENUE PART IS THE REVENUE, NOT THE COST OF SALES' COMPONENTS.

The revenue-block rule binds the rows printed between the revenue row and the cost of sales to the
face's revenue part (Far East Horizon prints its revenue as four rows and no total). Phoenix
Beverages 2025 (p129) prints "Revenue 13 400 370", then "Manufacturing costs (5 619 017)" and
"Excise and other specific duties (3 689 224)", then their total "Cost of sales (9 308 241)": both
cost rows were taken as revenue, and the face's revenue part came to 4,092,129 — its gross profit.
Rows the cost of sales itself totals are its components (`map_ontology._totals_its_rows`), recorded
as its breakdown so the residual does not sweep them beside it.
"""
from __future__ import annotations

FACE = "sub__face_principal_revenue"


def test_rows_the_cost_of_sales_totals_are_not_revenue():
    """Phoenix Beverages p129 (MUR '000): nothing is bound to the face's revenue part but Revenue."""
    from app.core.models.enums import Basis
    from app.stages.map_ontology import REVENUE_BLOCK_FLAG
    from tests.face_row_harness import face, row, run

    rows = [row("Revenue", "13400370", "12170859", page=65),
            row("Manufacturing costs", "-5619017", "-5038182", page=65),
            row("Excise and other specific duties", "-3689224", "-3418341", page=65),
            row("Cost of sales", "-9308241", "-8456523", page=65),
            row("Gross profit", "4092129", "3714336", page=65)]
    for li in rows:
        li.confidence.flags.append("printed_on:profit_and_loss")
    run(rows, [face("profit_and_loss", 65)], residual=False)
    assert rows[0].canonical_key == FACE
    assert rows[3].canonical_key == "is_pl__cost_of_sales"
    for li in rows[1:3]:
        assert li.canonical_key != FACE, li.confidence.flags
        assert not any(f.startswith(REVENUE_BLOCK_FLAG) for f in li.confidence.flags)
        assert li.parent_id == rows[3].id          # recorded as the cost of sales' breakdown
    assert Basis.CONSOLIDATED     # the harness files every figure on the group basis


def test_the_cost_of_sales_parts_are_not_swept_beside_the_cost_of_sales():
    """Phoenix Beverages p129: left unclaimed, the two parts were swept into Other Operating
    Expenses beside the cost of sales that holds them, and operating profit came to (8,119,732)
    against 1,188,509 printed — 9,308,241 short."""
    from tests.face_row_harness import face, row, run

    rows = [row("Revenue", "13400370", "12170859", page=65),
            row("Manufacturing costs", "-5619017", "-5038182", page=65),
            row("Excise and other specific duties", "-3689224", "-3418341", page=65),
            row("Cost of sales", "-9308241", "-8456523", page=65),
            row("Gross profit", "4092129", "3714336", page=65),
            row("Other income", "48588", "67580", page=65),
            row("Administrative expenses", "-1187132", "-851200", page=65)]
    for li in rows:
        li.confidence.flags.append("printed_on:profit_and_loss")
    run(rows, [face("profit_and_loss", 65)])
    swept = [li.source_label for li in rows if li.canonical_key == "is_pl__other_operating_expenses"]
    assert swept == [], swept
    for li in rows[1:3]:
        assert "residual_ineligible:of which breakdown" in li.confidence.flags, li.confidence.flags


def test_a_revenue_block_printed_as_rows_is_still_the_revenue():
    """Far East Horizon p172 (RMB'000): four revenue rows, no revenue total, then the cost of sales."""
    from app.stages.map_ontology import REVENUE_BLOCK_FLAG
    from tests.face_row_harness import face, row, run

    rows = [row("Interest income", "21182108", "22467103", page=172),
            row("Revenue from operating leases", "6350698", "6126865", page=172),
            row("Revenue from contracts with customers", "10353429", "9508737", page=172),
            row("Tax and surcharges", "-137079", "-142907", page=172),
            row("Cost of sales", "-20000000", "-21000000", page=172)]
    for li in rows:
        li.confidence.flags.append("printed_on:profit_and_loss")
    run(rows, [face("profit_and_loss", 172)], residual=False)
    assert [li.canonical_key for li in rows[:4]] == [FACE] * 4
    assert sum(1 for li in rows[:4]
               if any(f.startswith(REVENUE_BLOCK_FLAG) for f in li.confidence.flags)) == 3
