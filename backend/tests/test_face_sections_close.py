"""A numbered face line opens its own section, and a banner is matched as the heading it is.

f1's standalone profit and loss (Schedule III, Ind AS) read sections past their end:
"V. Profit before exceptional items and tax (III - IV)" inside "IV. Expenses", "IX. Profit for the
year (VII - VIII)" inside "VIII. Tax expense:", and the rows under "XII. Earnings per equity share
(face value of ₹ 10 each)" in the EQUITY section, because the word "equity" sits inside the
heading. Sections decide which lines may bind a row, so the Ind AS set refused "Profit for the year"
under the tax-expense heading, and "Other expenses" under "IV. Expenses" — a section that set does
not scope any of its concepts to.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models.enums import LineRole
from app.schemas.line_items import load_line_item_set
from app.services.line_item_matching import LineItemMatcher, Vocabulary
from app.services.mapping import section_as_declared, section_of_banner
from app.services.row_reconstruct import _is_noise_row
from tests.test_captions_cut_or_joined import PNL, _build

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _set(name: str):
    return load_line_item_set(json.loads((TEMPLATES / f"output_csv_{name}_line_items.json")
                                         .read_text(encoding="utf-8")))


# ── the heading is read as a heading ────────────────────────────────────────────────────────────

NUMBERED = [
    ("IV. Expenses", "expenses"),
    ("VIII. Tax expense:", "tax_expense"),
    ("VI. Exceptional items", "exceptional_items"),
    ("X. Other comprehensive income", "other_comprehensive_income"),
    ("I. Revenue from operations", "income"),
    ("A. Cash flow from operating activities", "cash_flow_from_operating_activities"),
    ("XII. Earnings per equity share (face value of ₹ 10 each)", None),
    ("V. Profit before exceptional items and tax (III - IV)", None),
    ("IX. Profit for the year (VII - VIII)", None),
    ("XI. Total comprehensive income for the year (IX + X)", None),
]

# The unnumbered banners every other regime prints resolve exactly as before.
UNNUMBERED = [
    ("Equity", "equity"),
    ("EQUITY", "equity"),
    ("Equity attributable to owners of the parent", "equity"),
    ("Total equity", "equity"),
    ("Capital and reserves", "equity"),
    ("Total equity and liabilities", None),
    ("EQUITY AND LIABILITIES", None),
    ("Cash flows from operating activities", "cash_flow_from_operating_activities"),
    ("所有者权益（或股东权益）：", "equity"),
    ("Total comprehensive income attributable to:", "total_comprehensive_income_attributable_to"),
]


@pytest.mark.parametrize("text,token", NUMBERED + UNNUMBERED)
def test_a_banner_names_the_section_its_heading_is(text, token):
    assert section_of_banner(text) == token


@pytest.mark.parametrize("name", ["icon", "indas", "hk"])
@pytest.mark.parametrize("text,token", NUMBERED + UNNUMBERED)
def test_every_set_reads_the_heading_the_same_way(name, text, token):
    vocab = Vocabulary(_set(name).vocabulary)
    if name != "hk" and any("\u4e00" <= ch <= "\u9fff" for ch in text):
        token = None                        # the Indian sets declare no Han banners
    assert vocab.section_of_banner(text) == token


# ── a roman-numbered line closes the section before it ──────────────────────────────────────────

def test_a_numbered_line_with_figures_closes_the_section_above_it():
    items = {li.source_label: li for li in _build(PNL, on_face=True, statement="profit_and_loss")}
    assert items["Other expenses"].section_hint == "IV. Expenses"
    pbe = items["V. Profit before exceptional items and tax (III - IV)"]
    assert pbe.section_hint == "V. Profit before exceptional items and tax (III - IV)"
    assert items["VII. Profit before tax (V - VI)"].section_hint == "VII. Profit before tax (V - VI)"
    assert items["(1) Current tax"].section_hint == "VIII. Tax expense:"
    pat = items["IX. Profit for the year (VII - VIII)"]
    assert pat.section_hint == "IX. Profit for the year (VII - VIII)"
    assert section_of_banner(pat.section_hint) is None
    assert section_of_banner(items["(1) Basic (₹)"].section_hint) is None
    assert items["Remeasurements of the defined benefit plans"].section_hint \
        == "X. Other comprehensive income"


def test_a_line_printing_its_own_arithmetic_is_a_total():
    items = {li.source_label: li for li in _build(PNL, on_face=True, statement="profit_and_loss")}
    for cap in ("Total expenses (IV)", "V. Profit before exceptional items and tax (III - IV)",
                "VII. Profit before tax (V - VI)", "IX. Profit for the year (VII - VIII)"):
        assert items[cap].role is LineRole.TOTAL, cap
    for cap in ("Other expenses", "(1) Current tax", "Total tax expense"):
        assert items[cap].role is LineRole.LINE, cap


def test_the_note_columns_header_tail_is_no_line():
    assert _is_noise_row("No.", [2025, 2024])
    assert _is_noise_row("Note No.", [2025, 2024])
    assert not _is_noise_row("No.", [77644.10, 69882.62])


# ── a part of the one profit-and-loss section ───────────────────────────────────────────────────

def test_a_set_with_one_pl_section_reads_its_parts_as_that_section():
    assert section_as_declared("expenses", {"income_and_expenses"}) == "income_and_expenses"
    assert section_as_declared("expenses", {"expenses", "income_and_expenses"}) == "expenses"
    assert section_as_declared("equity", {"income_and_expenses"}) == "equity"
    assert section_as_declared("expenses", {"current_assets"}) == "expenses"


@pytest.mark.parametrize("caption,section,key", [
    ("Other expenses", "IV. Expenses", "is_pl__other_operating_expenses"),
    ("Profit for the year", "VIII. Tax expense:", "is_pl__profit_for_the_year"),
    ("Current tax", "VIII. Tax expense:", "is_pl__current_income_taxes"),
])
def test_the_ind_as_set_binds_under_its_pl_headings(caption, section, key):
    assert LineItemMatcher(_set("indas")).match(caption, "profit_and_loss", section).key == key


def test_the_icon_set_keeps_reading_the_parts_it_scopes():
    m = LineItemMatcher(_set("icon"))
    assert m.match("Other expenses", "profit_and_loss", "IV. Expenses").key == "pl_icon__sga_others"
    # ICON scopes revenue to `income`, so an expenses heading still keeps revenue off.
    assert m.match("Revenue from operations", "profit_and_loss", "IV. Expenses").key is None


@pytest.mark.parametrize("caption,section,key", [
    ("Other expenses", "IV. Expenses", "is_pl__other_operating_expenses"),
    ("Profit for the year", "VIII. Tax expense:", "is_pl__profit_for_the_year"),
    ("Current tax", "VIII. Tax expense:", "is_pl__current_income_taxes"),
])
def test_the_runs_matcher_binds_under_the_ind_as_pl_headings(caption, section, key):
    from app.config import get_settings
    from app.services.mapping import OntologyMatcher
    from app.services.working_view import build_working_view

    m = OntologyMatcher(build_working_view(_set("indas")), locale="en", settings=get_settings())
    assert m.match(caption, "profit_and_loss", section).canonical_key == key


def test_a_numbered_line_is_its_concepts_own_caption_to_the_residual():
    from app.stages.residual import _untested_dedicated

    captions = {"is_pl": {"other income": ("is_pl__other_operating_income", ())}}
    assert _untested_dedicated(captions, "is_pl", "II. Other income") \
        == "is_pl__other_operating_income"
    assert _untested_dedicated(captions, "is_pl", "Other income") == "is_pl__other_operating_income"
