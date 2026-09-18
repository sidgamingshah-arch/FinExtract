"""A section ends at its CLOSING subtotal, not at the first rollup printed inside it.

``residual._sweep`` records the position each section closes at, so that a row wearing a stale
banner — ``row_reconstruct`` carries the last recognised heading down the page — is placed by the
accounting structure instead. The set it read was ``subtotal_of``: every concept the rulebook marks
``unit_of_account: subtotal``. Most of those are not section closes at all. They roll up their own
components in the MIDDLE of a section:

    bs_ca     inventories, net_trade_receivables, total_current_assets
    bs_nca    gross_fixed_assets, net_fixed_assets, net_intangibles, total_non_current_assets
    is_pl     nine intermediate margins, then profit_for_the_year

MEASURED ON 佳明集團 2025/26, whose balance sheet prints no "Total current assets" at all.
"Inventories of properties" is the third current asset printed and maps to ``bs_ca__inventories``,
so current assets was recorded as closing there — and every row below it had its own CURRENT ASSETS
banner discarded as spent. The next section subtotal anywhere below was the equity section's, three
sections away, so "Current tax assets" (1,474) and a derivative (397) were swept into
``bs_equity__other_reserves``: asset figures published as equity reserves, and
``bs_ca__other_current_assets`` left empty.

``rollups.section_members`` already sorts a section's subtotals by ascending ``match_priority`` so
that "the section's CLOSING subtotal is last" — its own words. The fix reads that order.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.rollups import section_members
from app.services.working_view import build_working_view

_SEED = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def members():
    cfg = load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)
    return section_members(build_working_view(cfg))


# --- the rulebook fact the fix rests on --------------------------------------------------------

@pytest.mark.parametrize("section,closes_with", [
    ("bs_ca", "bs_ca__total_current_assets"),
    ("bs_nca", "bs_nca__total_non_current_assets"),
    ("bs_cl", "bs_cl__total_current_liabilities"),
    ("bs_ncl", "bs_ncl__total_non_current_liabilities"),
    ("is_pl", "is_pl__profit_for_the_year"),
])
def test_a_sections_closing_subtotal_is_the_last_it_declares(members, section, closes_with):
    assert members[section].subtotals[-1] == closes_with


def test_a_mid_section_rollup_is_a_subtotal_but_not_the_close(members):
    """Both halves matter. Inventories IS declared a subtotal — it rolls up raw materials, work in
    progress and finished goods — so it belongs in the list; it is simply not the end of current
    assets."""
    subtotals = members["bs_ca"].subtotals

    assert "bs_ca__inventories" in subtotals
    assert "bs_ca__net_trade_receivables" in subtotals
    assert subtotals.index("bs_ca__inventories") < subtotals.index("bs_ca__total_current_assets")


def test_every_section_that_declares_a_subtotal_has_exactly_one_close(members):
    """What the fix indexes: one close per section, so no section can be closed twice or by a
    rollup inside it."""
    closes = {mem.subtotals[-1]: section for section, mem in members.items() if mem.subtotals}

    assert len(closes) == len([m for m in members.values() if m.subtotals])
    # And no mid-section rollup is among them.
    assert "bs_ca__inventories" not in closes
    assert "bs_nca__gross_fixed_assets" not in closes
    assert "is_pl__gross_profit" not in closes


# --- and what the stage does with it -----------------------------------------------------------

def test_the_stage_indexes_closes_not_every_subtotal(members):
    """The line the defect was on. ``closed_at`` is keyed by the CLOSING subtotal's concept, so a
    printed `bs_ca__inventories` row cannot close current assets."""
    import inspect

    from app.stages.residual import ResidualStage

    source = inspect.getsource(ResidualStage)
    assert "closes_of = {mem.subtotals[-1]" in source, (
        "closed_at must be built from each section's closing subtotal")
    assert "closed_at[section] = position" in source


def test_a_banner_is_spent_only_once_its_own_section_has_closed():
    """`_section_of_row`'s first signal, directly: the banner answers unless the section it names
    closed above this row."""
    from app.core.models.enums import LineRole, ValueSource
    from app.core.models.line_item import LineItem
    from app.stages.residual import ResidualStage

    row = LineItem(source_label="Current tax assets", ordinal=30, role=LineRole.LINE,
                   source=ValueSource.MACHINE, section_hint="Current assets 流動資產")
    # A statement long enough that `len(ordered)` is not itself the default "already closed":
    # the row sits at index 30 of 48, as it does on the filing this was measured on.
    ordered = [LineItem(source_label=f"row {i}", ordinal=i, role=LineRole.LINE,
                        source=ValueSource.MACHINE) for i in range(48)]
    ordered[30] = row
    placeable = {"bs_ca": ("current_assets", "balance_sheet"),
                 "bs_equity": ("equity", "balance_sheet")}
    args = dict(ordered=ordered, placeable=placeable, section_by_key={}, subtotal_of={},
                statement="balance_sheet", statement_of=lambda li: "balance_sheet")

    # Not closed: the banner stands.
    assert ResidualStage._section_of_row(idx=30, li=row, closed_at={}, **args) == "bs_ca"
    assert ResidualStage._section_of_row(
        idx=30, li=row, closed_at={"bs_ca": 40}, **args) == "bs_ca"
    # Closed above this row: spent, and with no structure to fall back on the answer is nothing
    # rather than a section the row is demonstrably not in.
    assert ResidualStage._section_of_row(
        idx=30, li=row, closed_at={"bs_ca": 27}, **args) is None
