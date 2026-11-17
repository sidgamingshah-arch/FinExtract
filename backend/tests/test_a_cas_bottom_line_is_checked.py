"""A CAS income statement's bottom lines are checked, though its costs are printed unsigned.

The sign gate skipped every income-statement relation on a CAS filing: its cost of sales is printed
unsigned, so no section of the statement is signed on the filing. The bottom lines hold no cost at
all — 五、净利润 = 四、利润总额 − 所得税费用, and the profit attributable to owners = 净利润 −
少数股东损益 — and their parts carry their own signs: 利润总额 and 净利润 say in their captions that
a loss is printed with a minus, and income tax and minority interests are lines the configuration
orients (`sign_rule.flip_if_label_matches`). Measured on the eight reference filings: both relations
pass on all six CAS ones, where they were skipped.

A subtotal is not signed merely by being one — 财务费用 is a net cost a CAS face prints unsigned —
which `tests/test_reviewer_checks.py` holds for profit before tax.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis, PrintedIn
from app.core.models.line_item import ExtractedValue, LineItem
from app.services.structural_checks import (Relation, _signed_by_their_own_rules,
                                            collect_values)

CAPTIONS = {"pbt": "四、利润总额（亏损总额以“－”号填列）", "net": "五、净利润（净亏损以“－”号填列）",
            "tax": "减：所得税费用", "minority": "2.少数股东损益", "finance": "财务费用",
            "interest_income": "利息收入", "interest_expense": "其中：利息费用"}


def _vals(**by_key):
    items = []
    for key, num in by_key.items():
        li = LineItem(source_label=CAPTIONS.get(key, key), canonical_key=key,
                      printed_in=PrintedIn.FACE)
        li.set_value(ExtractedValue(value=Decimal(str(num)), value_raw=Decimal(str(num)),
                                    basis=Basis.CONSOLIDATED, period_label="current"))
        items.append(li)
    return collect_values(items)


NET = Relation(id="rollup:net", kind="rollup", statement="profit_and_loss", target="net",
               components=("pbt", "tax"), op="sum")
ATTRIBUTABLE = Relation(id="rollup:owners", kind="rollup", statement="profit_and_loss",
                        target="owners", components=("net", "extraordinary", "minority"), op="sum")
FINANCE = Relation(id="rollup:finance", kind="rollup", statement="profit_and_loss",
                   target="finance", components=("interest_income", "interest_expense"), op="sum")
PBT = Relation(id="rollup:pbt", kind="rollup", statement="profit_and_loss", target="pbt",
               components=("operating", "finance"), op="sum")
ORIENTED = frozenset({"minority", "tax"})


def test_a_caption_that_signs_itself_and_an_oriented_line_establish_the_sum():
    """河钢 000709: 利润总额 + 减：所得税费用 (flipped by its sign rule) = 净利润."""
    vals = _vals(pbt="1033425186.50", tax="-232585183.25", net="800840003.25")
    assert _signed_by_their_own_rules(NET, vals, frozenset(), ORIENTED)


def test_an_absent_line_takes_no_part():
    """Minority interests are oriented by their sign rule; no extraordinary items are printed."""
    vals = _vals(net="2219319748.69", minority="-405241174.05", owners="1814078574.64")
    assert _signed_by_their_own_rules(ATTRIBUTABLE, vals, frozenset(), ORIENTED)


def test_a_line_no_rule_orients_keeps_the_relation_unchecked():
    """Net finance costs: interest income is a line nothing orients."""
    vals = _vals(interest_income="27996719.43", interest_expense="-1015149551.06",
                 finance="-1030923210.80")
    assert not _signed_by_their_own_rules(FINANCE, vals, frozenset(), ORIENTED)


def test_a_subtotal_whose_caption_does_not_sign_it_is_not_signed():
    """财务费用 is a net cost a CAS face prints unsigned; being a subtotal establishes nothing."""
    vals = _vals(operating="300", finance="50", pbt="350")
    assert not _signed_by_their_own_rules(PBT, vals, frozenset(), ORIENTED)


def test_a_total_of_costs_is_never_signed_this_way():
    vals = _vals(pbt="10", tax="-2", net="8")
    assert not _signed_by_their_own_rules(NET, vals, frozenset({"net"}), ORIENTED)


def test_a_relation_with_no_part_printed_is_not_established():
    assert not _signed_by_their_own_rules(NET, _vals(net="8"), frozenset(), ORIENTED)
