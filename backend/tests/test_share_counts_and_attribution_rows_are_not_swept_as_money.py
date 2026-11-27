"""A SHARE COUNT, AND A ROW UNDER AN ATTRIBUTION HEADING, ARE NOT SWEPT INTO A RESIDUAL AS MONEY.

NEW FILE -> backend/tests/test_share_counts_and_attribution_rows_are_not_swept_as_money.py

Two rows printed under the comprehensive income that are not amounts of comprehensive income:

* Vivo Energy Mauritius 2025 prints the number of shares under its earnings per share — "Number of
  shares used in the calculation ('000) 29,322 29,322" (printed page 66). It was swept into the OCI
  residual as money and turned other comprehensive loss (15,766) / (9,511) into +13,556 / +19,811.
  Share-count wording only: "Shares issued", a transaction in money on an equity statement, is
  still swept (`residual._SHARE_COUNT`).
* Moroil 2025 splits its total comprehensive income "attributable to:" "Owners of the Company" and
  "Non-controlling interests" (p68). The second caption names no attribution itself — a bare
  "Non-controlling interests" is also the balance sheet's equity line — so 2,518 / 2,864 was swept
  into the OCI residual, and other comprehensive income showed (1,970) / 60,016 against (4,488) /
  57,152. A row printed under a heading that splits a profit or an income by owner is an
  attribution row (`residual._is_attribution_row`).
"""
from __future__ import annotations

import pytest

from app.stages.residual import _is_attribution_row, _is_per_share_row

from tests.face_row_harness import face, row, run

OCI_OTHERS = "is_oci__other_equity_and_reserves_adj"


@pytest.mark.parametrize("caption", [
    "Number of shares used in the calculation ('000)",
    "Weighted average number of ordinary shares",
    "Weighted average shares in issue ('000)",
    "Earnings per share (Rs)",
])
def test_a_share_count_or_a_per_share_figure_is_not_an_amount(caption):
    assert _is_per_share_row(row(caption, "29322"))


@pytest.mark.parametrize("caption", ["Shares issued", "Issue of shares", "Share capital"])
def test_a_transaction_in_shares_is_still_an_amount(caption):
    assert not _is_per_share_row(row(caption, "1000"))


def test_a_row_under_an_attribution_heading_is_an_attribution_row():
    nci = row("Non-controlling interests", "2518", "2864")
    nci.group_hint = "Total comprehensive income attributable to:"
    assert _is_attribution_row(nci)
    equity_line = row("Non-controlling interests", "21487", "19972", section="Equity")
    equity_line.group_hint = "Equity attributable to owners of the Company"
    assert not _is_attribution_row(equity_line)


def test_vivo_share_count_leaves_the_oci_residual_alone():
    oci = "Other comprehensive income"
    rows = [row("Profit for the year", "529539", "516510"),
            row("Remeasurement of retirement benefit obligations", "-18671", "-11605", section=oci),
            row("Deferred tax on remeasurement", "2905", "2094", section=oci),
            row("Total comprehensive income for the year", "513773", "506999"),
            row("Basic and diluted earnings per share (Rs)", "18.06", "17.62"),
            row("Number of shares used in the calculation ('000)", "29322", "29322")]
    doc, _ = run(rows, [face("profit_and_loss")])
    count = doc.line_items[-1]
    assert count.canonical_key != OCI_OTHERS
    assert any(f.startswith("residual_ineligible:") for f in count.confidence.flags)


@pytest.mark.parametrize("caption", [
    "Basic (Rs.)", "Diluted (Rs.)",            # IndiGo 2024/25, p233 and p153
    "Basic (in ₹)", "Diluted (in ₹)",          # Asian Paints 2025/26, p183
    "Basic (in I)", "Diluted (in I )",         # JSW 2024/25, p218 (the ₹ glyph read as "I")
    "Diluted (`)",                             # Laurus Labs 2025/26, p104
    "Basic and diluted (Rs.)",
    "Earnings per equity share of face value of ₹10 each",
])
def test_an_eps_line_printed_by_its_kind_alone_is_not_an_amount(caption):
    assert _is_per_share_row(row(caption, "187.93"))


@pytest.mark.parametrize("caption", ["Basic salary", "Diluted (Note 12) adjustment",
                                     "Basic pay and allowances",
                                     # bare: only under a per-share heading (test_residual_framework)
                                     "Basic", "Basic and diluted"])
def test_a_caption_that_only_begins_with_basic_is_still_an_amount(caption):
    assert not _is_per_share_row(row(caption, "1000"))


def test_indigos_eps_lines_leave_the_oci_residual_alone():
    """IndiGo 2024/25, p233 (₹ million): the OCI lines add to the printed (95) / (146) once the
    EPS lines under them are not swept as money; with them, 280.60 / 277.45."""
    oci = "Other comprehensive income"
    rows = [row("Profit for the year", "72584", "81725"),
            row("- Remeasurements of defined benefit plans (net of tax) 16.b (iv)", "-89", "-152",
                section=oci),
            row("- Net loss due to foreign currency translation differences (net of tax) 16.c (ii)",
                "-46", None, section=oci),
            row("- Debt instruments through other comprehensive income (net of tax) 16.c (i)",
                "40", "6", section=oci),
            row("Other comprehensive income / (loss) for the year, net of tax", "-95", "-146",
                section=oci),
            row("Total comprehensive income for the year", "72489", "81579", section=oci),
            row("Basic (Rs.)", "187.93", "211.84", section=oci),
            row("Diluted (Rs.)", "187.67", "211.61", section=oci)]
    run(rows, [face("profit_and_loss")])
    assert rows[6].canonical_key != OCI_OTHERS and rows[7].canonical_key != OCI_OTHERS
    swept = sum(ev.value for r in rows if r.canonical_key == OCI_OTHERS
                for ev in r.values.values() if ev.period_label == "current")
    assert swept == -95
