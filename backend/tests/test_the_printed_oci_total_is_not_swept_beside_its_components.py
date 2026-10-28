"""THE PRINTED OTHER COMPREHENSIVE INCOME TOTAL IS NOT SWEPT IN BESIDE ITS OWN COMPONENTS.

Total Other Comprehensive Income is calculated from the OCI lines, and an OCI component no line
names is swept into Other Equity & Reserves Adj, the section's residual. The residual's
`exclude_hints` refused the statement's bottom lines — profit for the year, total comprehensive
income — but not the OCI TOTAL as five HKEX filings print it, so the total was swept in beside the
components it totals and the section came out at twice the printed figure (current / prior):

  佳明 2025/26       年內其他全面收益，扣除稅項 for the year, net of tax          4,739 / -15,573
  BYD Electronic    OTHER COMPREHENSIVE INCOME FOR THE YEAR, NET OF TAX
                    本年度其他綜合收益，扣除稅項                                 17,861 / 25,725
  Far East Horizon  OTHER COMPREHENSIVE INCOME FOR THE YEAR, NET OF TAX      641,328 / 136,036
  Lai Sun           OTHER COMPREHENSIVE INCOME/(EXPENSE) FOR THE YEAR        268,064 / -386,661
  PetroChina        OTHER COMPREHENSIVE INCOME, NET OF TAX                   -12,164 / 2,014

Lai Sun also swept its TOTAL COMPREHENSIVE EXPENSE FOR THE YEAR (-2,969,793 / -4,642,640): the
residual refused "total comprehensive income|loss" and not "expense".

PETROCHINA'S TWO COMPONENTS BOUND NOTHING. Its PDF prints each group's sub-header on the line above
the group's first item and the two merge into one caption — "Items that will not be reclassified to
profit or loss Fair value changes in equity investment measured at fair value through other
comprehensive income" (-109 / 64), "Items that are or may be reclassified subsequently to profit or
loss Cash flow hedges" (-8,111 / -1,893) — which the residual refuses as narrative. Each now binds
its line by an anchored regex. Two notes parts whose only keyword is "fair value" refuse a caption
that begins with an OCI sub-header: hit beside the OCI line, they made the rule tier ambiguous.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view
from tests.test_one_set_equity_and_cash_flow_bindings import SEED, _face, _row, _run

RESIDUAL = "is_oci__other_equity_and_reserves_adj"
TOTAL = "is_oci__total_other_comprehensive_income"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(shipped):
    return OntologyMatcher(build_working_view(shipped))


def _oci(rows, period: str) -> Decimal:
    """What the OCI lines carry in one period — what Total Other Comprehensive Income adds up."""
    return sum((ev.value for r in rows if (r.canonical_key or "").startswith("is_oci__")
                and r.canonical_key != TOTAL for ev in r.values.values()
                if ev.period_label == period and ev.value is not None), Decimal(0))


@pytest.mark.parametrize("caption", [
    "年內其他全面收益，扣除稅項 for the year, net of tax",
    "OTHER COMPREHENSIVE INCOME FOR THE YEAR, NET OF TAX 本年度其他綜合收益，扣除稅項",
    "OTHER COMPREHENSIVE INCOME FOR THE YEAR, NET OF TAX",
    "OTHER COMPREHENSIVE INCOME/(EXPENSE) FOR THE YEAR",
    "OTHER COMPREHENSIVE INCOME, NET OF TAX",
    "TOTAL COMPREHENSIVE EXPENSE FOR THE YEAR",
])
def test_a_printed_total_is_refused_by_the_residual(shipped, caption):
    sec = "OTHER COMPREHENSIVE INCOME"
    rows = [_row("Exchange realignments", "243212", "-229062", 0, sec),
            _row(caption, "243212", "-229062", 0, sec)]
    _run(rows, [_face("profit_and_loss")], shipped)
    assert rows[0].canonical_key == RESIDUAL, (rows[0].canonical_key, rows[0].confidence.flags)
    assert rows[1].canonical_key is None, (rows[1].canonical_key, rows[1].confidence.flags)
    assert any(f.startswith("residual_never_sweep:exclude_hints:")
               for f in rows[1].confidence.flags), rows[1].confidence.flags


def test_grand_mings_oci_lines_add_to_the_printed_total(shipped):
    sec = "Other comprehensive income 其他全面收益"
    rows = [
        _row("Loss for the year 年內虧損", "-349498", "-292055", 0, None),
        _row("Financial assets at fair value through other comprehensive income net", None,
             "-192", 0, sec),
        _row("Exchange differences on translation of foreign operations 換算海外業務的匯兌差額",
             "11806", "-3104", 0, sec),
        _row("Cash flow hedges – net movement in hedging reserve 現金流量對沖－對沖儲備的淨變動",
             "-7067", "-12277", 0, sec),
        _row("年內其他全面收益，扣除稅項 for the year, net of tax", "4739", "-15573", 0, sec),
        _row("Total comprehensive income for the year 年內全面收益總額", "-344759", "-307628", 0,
             sec),
    ]
    _run(rows, [_face("profit_and_loss")], shipped)
    assert [r.canonical_key for r in rows[1:4]] == [RESIDUAL] * 3
    assert _oci(rows, "current") == Decimal("4739")
    assert _oci(rows, "prior") == Decimal("-15573"), "it was -31,146: the total beside its parts"


def test_petrochinas_merged_captions_bind_their_lines(matcher):
    for caption, key in (
            ("Items that will not be reclassified to profit or loss Fair value changes in equity "
             "investment measured at fair value through other comprehensive income",
             "is_oci__unreal_gain_loss_investments"),
            ("Items that are or may be reclassified subsequently to profit or loss Cash flow "
             "hedges", "is_oci__deferred_hedging_gain_loss")):
        res = matcher.match(caption, statement="profit_and_loss",
                            section="OTHER COMPREHENSIVE INCOME")
        assert res.canonical_key == key, (caption, res)
        assert res.confidence >= 0.9, "one claimant: the notes parts refuse an OCI sub-header"


def test_a_bare_cash_flow_hedges_caption_is_not_taken_by_the_regex(matcher):
    res = matcher.match("Cash flow hedges", statement="profit_and_loss",
                        section="OTHER COMPREHENSIVE INCOME")
    assert res.canonical_key != "is_oci__deferred_hedging_gain_loss", res


def test_petrochinas_oci_lines_add_to_the_printed_total(shipped):
    sec = "OTHER COMPREHENSIVE INCOME"
    rows = [
        _row("PROFIT FOR THE YEAR", "183755", "180563", 0, None),
        _row("Items that will not be reclassified to profit or loss Fair value changes in equity "
             "investment measured at fair value through other comprehensive income", "-109", "64",
             0, sec),
        _row("Currency translation differences", "-137", "1515", 0, sec),
        _row("Items that are or may be reclassified subsequently to profit or loss Cash flow "
             "hedges", "-8111", "-1893", 0, sec),
        _row("Share of the other comprehensive income of associates and joint ventures", "212",
             "76", 0, sec),
        _row("Currency translation differences", "-4019", "2252", 0, sec),
        _row("OTHER COMPREHENSIVE INCOME, NET OF TAX", "-12164", "2014", 0, sec),
        _row("TOTAL COMPREHENSIVE INCOME FOR THE YEAR", "171591", "182577", 0, sec),
    ]
    _run(rows, [_face("profit_and_loss")], shipped)
    assert _oci(rows, "current") == Decimal("-12164")
    assert _oci(rows, "prior") == Decimal("2014")
