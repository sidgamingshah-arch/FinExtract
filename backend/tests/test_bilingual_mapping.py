"""Mapping a bilingual, Traditional-Chinese filing — the generic defects found by running a
real HKEX annual report (China SCE Group Holdings AR2023) end to end.

Three properties, each of which failed before and each of which affects EVERY HK/Taiwan
filing, not just the one we ran:

* Traditional and Simplified forms of a caption are the same concept.
* A bilingual caption ("REVENUE 收益") matches as well as either language alone.
* The statement a caption was printed on constrains which concept may win, so the same
  wording resolves differently on the P&L and in the cash-flow reconciliation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
ONTOLOGY = json.loads((_DIR / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def matcher():
    from app.config import get_settings
    from app.schemas.loader import load_ontology
    from app.services.mapping import OntologyMatcher

    return OntologyMatcher(load_ontology(ONTOLOGY, resolve=True), locale="en", settings=get_settings())


# ---- Han folding -------------------------------------------------------------------------

def test_traditional_folds_to_simplified():
    from app.services.han import has_han, to_simplified

    assert to_simplified("銷售成本") == "销售成本"
    assert to_simplified("投資物業") == "投资物业"
    assert to_simplified("財務費用") == "财务费用"
    assert to_simplified("Cost of sales") == "Cost of sales"     # Latin untouched
    assert not has_han("Revenue")


def test_folding_survives_without_opencc(monkeypatch):
    """The built-in table must cover real statement vocabulary, because OpenCC is optional."""
    import app.services.han as han

    monkeypatch.setattr(han, "_CC", None)
    for trad, simp in [("銷售成本", "销售成本"), ("財務費用", "财务费用"),
                       ("遞延稅項資產", "递延税项资产"), ("年內虧損", "年内亏损"),
                       ("綜合損益及其他全面收益表", "综合损益及其他全面收益表")]:
        assert han.to_simplified(trad) == simp


def test_normalize_label_makes_script_variants_equal():
    from app.services.mapping import normalize_label

    assert normalize_label("銷售成本") == normalize_label("销售成本")


# ---- bilingual captions ------------------------------------------------------------------

def test_label_segments_splits_a_bilingual_caption():
    from app.services.mapping import label_segments

    assert label_segments("REVENUE 收益") == ["REVENUE 收益", "REVENUE", "收益"]
    assert label_segments("Revenue") == ["Revenue"]              # monolingual unaffected


@pytest.mark.parametrize("caption,expected", [
    ("REVENUE 收益", "pl_income__revenue_from_operations"),
    ("Cost of sales 銷售成本", "pl_expenses__cost_of_goods_sold"),
    ("Gross profit 毛利", "pl_gross_profit"),
    ("Other income and gains 其他收入及收益", "pl_income__other_income"),
    ("Administrative expenses 行政開支", "pl_expenses__general_and_administrative_expenses"),
    ("PROFIT/(LOSS) BEFORE TAX 除稅前溢利╱（虧損）", "pl_profit_before_tax"),
    ("Income tax expense 稅項開支", "pl_tax_expense__total_tax_expense"),
    ("LOSS FOR THE YEAR 年內虧損", "pl_profit_for_the_year"),
])
def test_real_pl_face_lines_map(matcher, caption, expected):
    """The exact captions printed on page 102 of the real filing."""
    assert matcher.match(caption, statement="profit_and_loss").canonical_key == expected


@pytest.mark.parametrize("caption,expected", [
    ("收益", "pl_income__revenue_from_operations"),
    ("銷售成本", "pl_expenses__cost_of_goods_sold"),
    ("毛利", "pl_gross_profit"),
])
def test_traditional_only_captions_map(matcher, caption, expected):
    assert matcher.match(caption, statement="profit_and_loss").canonical_key == expected


# ---- statement scoping -------------------------------------------------------------------

def test_the_same_caption_resolves_per_statement(matcher):
    """"Profit before tax" is a P&L subtotal AND the opening line of the cash-flow
    reconciliation. Which concept is right depends on the page it was printed on."""
    pl = matcher.match("Profit/(loss) before tax 除稅前溢利", statement="profit_and_loss")
    cf = matcher.match("Profit/(loss) before tax 除稅前溢利", statement="cash_flow")
    assert pl.canonical_key == "pl_profit_before_tax"
    assert cf.canonical_key and cf.canonical_key.startswith("cf_")


def test_a_pl_caption_never_wins_a_balance_sheet_concept(matcher):
    """The fair-value line is a P&L movement; it used to resolve to the BS asset itself."""
    res = matcher.match("Changes in fair value of investment properties, net 投資物業公允值變動淨額",
                        statement="profit_and_loss")
    assert res.canonical_key is None or res.canonical_key.startswith("pl_")


def test_a_balance_sheet_caption_never_wins_a_pl_concept(matcher):
    res = matcher.match("Deferred tax assets 遞延稅項資產", statement="balance_sheet")
    assert res.canonical_key is None or res.canonical_key.startswith("bs_")


def test_unknown_statement_does_not_constrain(matcher):
    """With no statement determined, mapping must still work (never fail closed)."""
    assert matcher.match("REVENUE 收益").canonical_key == "pl_income__revenue_from_operations"


# ---- header / non-line rows --------------------------------------------------------------

@pytest.mark.parametrize("label", ["二零二三年 二零二二年", "2023 2022", "二零二三年"])
def test_period_header_rows_are_not_line_items(label):
    from app.services.row_reconstruct import _is_noise_row

    assert _is_noise_row(label, [0, 0])


@pytest.mark.parametrize("label", ["Profit for the year ended 2023", "REVENUE 收益",
                                   "Trade receivables 貿易應收款項"])
def test_real_captions_are_kept(label):
    from app.services.row_reconstruct import _is_noise_row

    assert not _is_noise_row(label, [20960968, 26705112])


@pytest.mark.parametrize("label", [
    # 佳明集團 2025/26 heads each statement page with the date AND the currency it is stated in.
    "於二零二六年三月三十一日（以港元列示）",
    "於二零二五年三月三十一日(以港元列示)",
    # The mainland and the English spellings of the same thing.
    "2024 RMB'000",
    "二零二三年（未經審核）",
    "As at 31 December 2024",
    "expressed in thousands of Hong Kong dollars",
])
def test_a_period_caption_with_its_units_on_it_is_still_a_period_caption(label):
    """WHY THE ANNOTATIONS HAVE TO COME OFF FIRST. ``_is_period_only_label`` asks whether anything
    SUBSTANTIVE is left once the period tokens are struck out, and a filing does not print a bare
    date: it prints the date, the currency it is stated in, the scale, and whether the figures are
    audited. Each of those left a residue, the label read as substantive, and the heading was
    published as a line item whose amount was the year — on 佳明集團 the two date headings put
    2026 and 2025 into ``bs_ca__other_current_assets`` and ``bs_cl__other_current_liabilities``.

    於 / 于 / as at / as of are struck for the same reason: a preposition is not a caption.
    """
    from app.services.row_reconstruct import _is_period_only_label

    assert _is_period_only_label(label)


@pytest.mark.parametrize("label", [
    "Trade receivables",
    "Total assets less current liabilities",
    "Balance as at 1 January",
    "於期末",
    "於二零二六年三月三十一日之投資",
    "Cash and cash equivalents at end of year",
])
def test_a_caption_that_merely_mentions_a_period_keeps_its_figures(label):
    """The other side of the same rule. A period token inside a real caption is not the caption,
    and ``於期末``/``Balance as at 1 January`` are the rows a statement of changes in equity is
    made of — the strike-out must leave their substance behind."""
    from app.services.row_reconstruct import _is_period_only_label

    assert not _is_period_only_label(label)


def test_a_section_heading_does_not_capture_its_note_number():
    """"EQUITY HOLDERS OF THE PARENT" carries no amount; the 13 beside it is a note ref."""
    from app.services.row_reconstruct import _is_noise_row

    assert _is_noise_row("EQUITY HOLDERS OF THE PARENT", [13])
    assert not _is_noise_row("Trade receivables", [13])          # a real caption keeps its value
