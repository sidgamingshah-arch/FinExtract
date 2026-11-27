"""A BARE FIGURE LINE THE READER PROVED IS ITS SECTION'S TOTAL BINDS THAT SECTION'S DECLARED TOTAL.

NEW FILE -> backend/tests/test_a_bare_section_total_is_its_sections_declared_total.py

Vivo Energy Mauritius 2025 prints the four subtotals of its statement of financial position with no
caption at all (printed page 67): 2,050,718 under the non-current assets, 2,738,706 under the
current assets, 258,549 and 3,323,069 under the two liability sections. The reader already proves
each one is its section's total — in every column it is exactly the sum of the rows printed under
the banner in force (`row_reconstruct._face_section_total`) — and captions it with that banner,
the only caption the page gives it. But the banner names the SECTION, not its total, so caption
mapping matched it to nothing: all four stayed unclassified and no balance-sheet section had a
printed total to be checked against.

The reader now flags the line (`FACE_SECTION_TOTAL_FLAG`) and the mapper asks for it by the total's
own name — "Total" and the banner — under the same statement and section gates. No alias is added:
a printed caption "Current assets" that is not such a line stays a banner. Phoenix Beverages, Lux
Island Resorts and Gamma Civic print the same bare totals.
"""
from __future__ import annotations

from app.services.row_reconstruct import FACE_SECTION_TOTAL_FLAG

from tests.face_row_harness import face, row, run


def _vivo_current_assets(flagged: bool):
    section = "Current assets"
    flags = (FACE_SECTION_TOTAL_FLAG,) if flagged else ()
    return [row("Inventories", "758382", "851697", section=section),
            row("Trade and other receivables", "1257948", "1255716", section=section),
            row("Cash and cash equivalents", "722376", "1006922", section=section),
            row("Current assets", "2738706", "3114308", section=section, flags=flags)]


def test_the_bare_current_assets_total_binds_total_current_assets():
    doc, logs = run(_vivo_current_assets(flagged=True), [face("balance_sheet")], residual=False)
    total = doc.line_items[-1]
    assert total.canonical_key == "bs_ca__total_current_assets"
    assert "bound_as_section_total:bs_ca__total_current_assets" in total.confidence.flags
    assert any(line.startswith("map_line_items:bound_as_section_total=1") for line in logs)


def test_a_banner_caption_the_reader_did_not_prove_is_not_bound_as_a_total():
    doc, _ = run(_vivo_current_assets(flagged=False), [face("balance_sheet")], residual=False)
    assert doc.line_items[-1].canonical_key != "bs_ca__total_current_assets"


def test_each_balance_sheet_section_binds_its_own_total():
    cases = {"Non-current assets": "bs_nca__total_non_current_assets",
             "Non-current liabilities": "bs_ncl__total_non_current_liabilities",
             "Current liabilities": "bs_cl__total_current_liabilities"}
    for banner, key in cases.items():
        rows = [row("Deferred tax liabilities" if "liabilities" in banner.lower()
                    else "Property, plant and equipment", "100", "90", section=banner),
                row(banner, "100", "90", section=banner, flags=(FACE_SECTION_TOTAL_FLAG,))]
        doc, _ = run(rows, [face("balance_sheet")], residual=False)
        assert doc.line_items[-1].canonical_key == key, banner


def test_a_bare_line_that_closes_only_a_block_of_its_section_is_not_its_total():
    """Lai Sun Garment 2024/25 (page index 186): under "OTHER COMPREHENSIVE INCOME/(EXPENSE)" the
    block that may be reclassified closes on a bare 253,776, the section on "... FOR THE YEAR"
    268,064 two rows later. The bare line plus the rows after it is the printed total, so it closes
    a block, and binding it as the section's total failed both OCI checks by the second block."""
    section = "OTHER COMPREHENSIVE INCOME/(EXPENSE)"
    rows = [row("Exchange realignments", "243212", "-229062", section=section),
            row("Share of other comprehensive income of associates", "6215", "-1459",
                section=section),
            row("Release of exchange reserve upon deregistration of subsidiaries", "2719", "-5090",
                section=section),
            row(section, "252146", "-235611", section=section, flags=(FACE_SECTION_TOTAL_FLAG,)),
            row("Changes in fair values of financial assets at fair value through other "
                "comprehensive income", "-2203", "-158886", section=section),
            row("Surplus on revaluation of properties upon reclassification to investment "
                "properties", "16491", "7218", section=section),
            row("OTHER COMPREHENSIVE INCOME/(EXPENSE) FOR THE YEAR", "266434", "-387279",
                section=section)]
    doc, logs = run(rows, [face("profit_and_loss")], residual=False)
    block = doc.line_items[3]
    assert block.canonical_key != "is_oci__total_other_comprehensive_income"
    assert not any(f.startswith("bound_as_section_total") for f in block.confidence.flags)
    assert any(line.startswith("map_line_items:section_block_subtotals_not_bound=1")
               for line in logs)


def test_a_section_total_followed_by_the_statement_total_is_still_bound():
    """Vivo's current assets are followed by "Total assets", which is not current assets plus
    anything printed between them."""
    rows = _vivo_current_assets(flagged=True) + [
        row("Total assets", "4789424", "5046046", section="Current assets")]
    doc, _ = run(rows, [face("balance_sheet")], residual=False)
    assert doc.line_items[3].canonical_key == "bs_ca__total_current_assets"
