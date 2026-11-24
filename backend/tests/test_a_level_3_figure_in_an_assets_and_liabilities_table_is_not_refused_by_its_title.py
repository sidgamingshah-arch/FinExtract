"""A LEVEL 3 FIGURE READ FROM A TABLE OF ASSETS AND LIABILITIES IS NOT REFUSED BY THE TABLE'S TITLE
(`services.whole_pdf_locate.Judge._column_checks`).

The two Level 3 parts refuse a row that is not a financial asset — an investment property, a
biological asset, a liability — and the test was run on the row's caption AND on the note table's
title. The CAS standard fair-value table is titled 以公允价值计量的资产和负债的期末公允价值 ("the
fair value of the assets AND liabilities measured at fair value"), and its 负债 matched the parts'
own liability pattern. So every nonzero Level 3 figure read from that table was refused, whatever
its row: 002273 十三、1's 持续以公允价值计量的资产总额 187,541,328.29, the very cell the rules
route reads and publishes. A nil passed only because a zero is never checked.

A title that names assets and liabilities together holds both, so it says nothing about one row;
its rows are told apart by their captions, as the rules tell them apart. A title of liabilities
alone, or of investment properties (1966's note 15, pinned in
`test_a_whole_pdf_figure_is_checked_where_the_rules_read_its_page`), still refuses.

The filing test drives the REAL pipeline on the real filing's pages, with the shipped stand-in
answering from a SCRIPT (`tests.whole_pdf_standin`): a scripted answer shows what the pipeline does
with that answer, never what a model would answer.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from app.services import whole_pdf_locate as L
from tests.whole_pdf_standin import filing_path, rows_of, run_whole_pdf, script_figure

STEM = "002273_crystal_optech_2024_prc"
L3_PRINTED = "sub__fa_cp_level_3_printed_total"


def _vetoes():
    """The shipped Level 3 printed-total part's own 'not a financial asset' patterns."""
    import json
    import pathlib

    from app.schemas.line_items import load_line_item_set

    seed = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "output_csv_hk_line_items.json")
    items = load_line_item_set(json.loads(seed.read_text(encoding="utf-8")), resolve=True)
    item = next(i for i in items.items if i.key == L3_PRINTED)
    return L._not_an_asset_vetoes(item)


@pytest.mark.parametrize("title,refused", [
    ("、以公允价值计量的资产和负债的期末公允价值", False),
    ("以公允价值计量的资产及负债", False),
    ("Assets and liabilities measured at fair value", False),
    ("Financial assets and financial liabilities measured at fair value", False),
    ("以公允价值计量的负债", True),
    ("Financial liabilities at fair value", True),
    ("Investment properties", True),
    ("生物资产", True),
])
def test_a_title_naming_assets_and_liabilities_together_says_nothing_about_one_row(title,
                                                                                   refused):
    table = NS(title=title, chapter_title="")
    assert L.not_a_financial_asset(_vetoes(), "持续以公允价值计量的资产总额", table) is refused


def test_a_liability_row_is_still_refused_by_its_own_caption():
    table = NS(title="、以公允价值计量的资产和负债的期末公允价值", chapter_title="")
    assert L.not_a_financial_asset(_vetoes(), "持续以公允价值计量的负债总额", table)


@pytest.mark.skipif(not filing_path(STEM).exists(), reason=f"{STEM}.pdf not present")
def test_the_level_3_total_of_a_cas_fair_value_table_is_the_models(tmp_path, monkeypatch):
    """002273 十三、1 (report page 183): the model's 187,541,328.29 is written on its own row and
    not refused; no rules row fills a slot the model answered."""
    script = {"units": "元", "lines": {L3_PRINTED: [script_figure(
        "187541328.29", page=183, caption="持续以公允价值计量的资产总额")]}}
    doc, _ctx, _rec = run_whole_pdf(monkeypatch, tmp_path, STEM, script, pages=[181, 182, 183])
    rows = rows_of(doc, L3_PRINTED)
    flags = [f for r in rows for f in r.confidence.flags]
    assert not any(f.startswith("whole_pdf:refused:not_a_financial_asset") for f in flags)
    model = [r for r in rows if "whole_pdf:rules_reading" not in r.confidence.flags]
    assert [ev.value for r in model for ev in r.values.values() if ev.value is not None] == [
        Decimal("187541328.29")]
    assert not any("whole_pdf:rules_reading" in r.confidence.flags for r in rows)
