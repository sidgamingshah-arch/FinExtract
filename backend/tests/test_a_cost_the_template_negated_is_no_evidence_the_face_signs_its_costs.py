"""A COST THE TEMPLATE NEGATED IS NO EVIDENCE THAT THE FACE SIGNS ITS COSTS.

`structural_checks._signed_on_filing` decides whether a filing prints its costs negative, from the
face rows a concept claimed: a negative figure is evidence that it does, a declared cost printed
positive that it does not. A row assembled from the notes, and a row the residual swept, are no
evidence either way.

On an Ind AS income statement read with the HK template every printed cost is swept into Other
Operating Expenses, printed unsigned — no evidence. What was left was 'Finance costs', which the
template stores negative whatever the page printed (`sign: natural_negative`): Asian Paints 2025/26
prints 195.41 (p232) and IndiGo 2024/25 prints 50,800 (p233), and both were stored -195.41 /
-50,800. That template-made minus was the section's only "negative", so the section read as signed,
and the profit-or-loss reconciliation added the unsigned costs: Asian Paints 66,551.60 against a
printed 4,325.35, IndiGo 1,507,780 against 72,584. A figure the page printed positive and the
template negated is a cost the filing printed unsigned.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis, PrintedIn
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.services.structural_checks import _signed_on_filing

INTEREST = "is_pl__interest_expense"
COST_OF_SALES = "is_pl__cost_of_sales"
OTHER_COSTS = "is_pl__other_operating_expenses"
SECTION_OF = {INTEREST: "is_pl", COST_OF_SALES: "is_pl", OTHER_COSTS: "is_pl"}
COSTS = frozenset({COST_OF_SALES, OTHER_COSTS})


def _line(key: str, printed: str, stored: str | None = None, *, flags: tuple[str, ...] = (),
          method: str | None = None) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key, printed_in=PrintedIn.FACE)
    ev = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                        value=Decimal(stored if stored is not None else printed),
                        value_raw=Decimal(printed), provenance=Provenance(page_index=232))
    if flags:
        ev.sign_normalised = True
        ev.confidence.flags.extend(flags)
    li.set_value(ev)
    if method:
        li.confidence.method = method
    return li


NEGATED = ("sign_rule:natural_negative", "sign_normalised")


def test_a_finance_cost_printed_unsigned_and_negated_by_the_template_signs_nothing():
    """Asian Paints p232: the costs swept, 'Finance Costs' 195.41 stored -195.41."""
    items = [_line(OTHER_COSTS, "15384.05", method="residual"),
             _line(OTHER_COSTS, "35416.25", method="residual"),
             _line(INTEREST, "195.41", "-195.41", flags=NEGATED)]
    assert _signed_on_filing(items, SECTION_OF, COSTS) == frozenset()


def test_a_finance_cost_printed_in_brackets_still_signs_the_section():
    """Vivo p66: the page prints '(28,948)'; the template's sign changes nothing."""
    items = [_line(COST_OF_SALES, "-16475225"), _line(INTEREST, "-28948")]
    assert _signed_on_filing(items, SECTION_OF, COSTS) == frozenset({"is_pl"})


def test_a_credit_kept_as_printed_under_an_unsigned_expense_is_not_read_as_unsigned():
    """301577: a net interest credit printed -9,816.85 under 财务费用 keeps its sign."""
    kept = ("sign_rule:natural_negative", "sign_rule:as_printed_under_unsigned_expense",
            "sign_normalised")
    items = [_line(COST_OF_SALES, "-1000"), _line(INTEREST, "-9816.85", "9816.85", flags=kept)]
    assert _signed_on_filing(items, SECTION_OF, COSTS) == frozenset({"is_pl"})
