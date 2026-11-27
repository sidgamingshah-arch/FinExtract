"""A FIGURE THE LINE-ITEM SET ASSEMBLED FROM THE NOTES DOES NOT DECIDE HOW THE FACE SIGNS ITS COSTS.

NEW FILE -> backend/tests/test_a_figure_assembled_from_the_notes_does_not_decide_the_face_signs.py

A plain-sum check of the income statement needs the filing's signs, and `structural_checks.
_signed_on_filing` reads them off the face: a section whose figures include a negative and none of
whose declared costs is printed positive prints its costs in brackets. Vivo Energy Mauritius 2025
prints every cost on page 66 in brackets — and every profit-or-loss check was skipped as
`component_signs_not_established`, because ONE "cost" was positive: Depreciation & Impairment (Oper
Exp) +211,665 / +206,748, put together by cascade P4 from notes 14 and 16. Notes print magnitudes;
the face never printed that line. Grand Ming 2025/26 carries the same kind of row (cascade P3,
+19,729 / +21,258).

A row a cascade rung or a note declaration wrote (`cascade_rung:` / `note_sourced:` flags), and a
value with no page behind it, are no evidence of how the face signs anything.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis, PrintedIn
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.services.structural_checks import _signed_on_filing

COST_OF_SALES = "is_pl__cost_of_sales"
DEPRECIATION = "is_pl__deprec_and_impairment_oper_exp"
SECTION_OF = {COST_OF_SALES: "is_pl", DEPRECIATION: "is_pl"}
COSTS = frozenset({COST_OF_SALES, DEPRECIATION})


def _line(key: str, current: str, page: int | None, *flags: str) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key, printed_in=PrintedIn.FACE)
    li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                value=Decimal(current), value_raw=Decimal(current),
                                provenance=Provenance(page_index=page) if page is not None
                                else None))
    li.confidence.flags.extend(flags)
    return li


def test_a_cascade_assembled_cost_does_not_unsign_the_face():
    items = [_line(COST_OF_SALES, "-16475225", 34),
             _line(DEPRECIATION, "211665", None, "cascade_rung:P4")]
    assert _signed_on_filing(items, SECTION_OF, COSTS) == frozenset({"is_pl"})


def test_a_note_sourced_cost_does_not_unsign_the_face():
    items = [_line(COST_OF_SALES, "-16475225", 34),
             _line(DEPRECIATION, "211665", 80, "note_sourced:sub__prepaid_lease_depreciation")]
    assert _signed_on_filing(items, SECTION_OF, COSTS) == frozenset({"is_pl"})


def test_a_cost_the_face_prints_positive_still_says_the_costs_are_unsigned():
    items = [_line(COST_OF_SALES, "16475225", 34), _line(DEPRECIATION, "-211665", 34)]
    assert _signed_on_filing(items, SECTION_OF, COSTS) == frozenset()
