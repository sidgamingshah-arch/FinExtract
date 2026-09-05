"""Coverage and disambiguation for the HK/China (HKFRS) template + ontology, driven by the
captions a real HKEX property-developer filing actually prints (China SCE Group Holdings
AR2023, bilingual, Traditional Chinese).

Two distinct properties are pinned here:

* COVERAGE — a caption that is extracted and reconstructed correctly must not die because the
  template holds no such concept. Every caption below mapped to nothing before these concepts
  were added, so each assertion is a real gap that stays closed.
* DISAMBIGUATION — two concepts whose captions share most of their words must resolve to
  DIFFERENT keys. Both pairs below were confirmed mis-mappings caught by the structural layer
  as ``ambiguous_mapping`` (one canonical key holding two conflicting values), which is what
  happens when a caption lands on a neighbouring line of the same page.

The real ``OntologyMatcher`` is used, with the ``statement`` the caption was printed on, because
that constraint is part of the decision.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TEMPLATE = json.loads((_DIR / "hkfrs_hk_china_template.json").read_text(encoding="utf-8"))
ONTOLOGY = json.loads((_DIR / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def matcher():
    from app.config import get_settings
    from app.schemas.loader import load_ontology
    from app.services.mapping import OntologyMatcher

    return OntologyMatcher(load_ontology(ONTOLOGY, resolve=True), locale="en", settings=get_settings())


def _key(matcher, caption: str, statement: str, section: str | None = None) -> str | None:
    return matcher.match(caption, statement=statement, section=section).canonical_key


# Captions that name a COLLISION FAMILY and cannot be resolved by their own words. "Properties under
# development" is printed in current assets by a developer and in non-current assets by a landlord;
# "Non-controlling interests" is printed twice on one income statement, under the profit split and
# under the comprehensive-income split. The banner is the whole answer, so these carry the banner a
# filing prints — and `test_a_family_caption_alone_is_refused_rather_than_guessed` holds the other
# half: without it the matcher refuses instead of picking one.
_CURRENT_ASSETS = "CURRENT ASSETS 流動資產"
_PROFIT_SPLIT = "Profit attributable to: 下列各項應佔溢利："


# ---- coverage: the captions that previously mapped to nothing -----------------------------

# (caption as printed, statement it was printed on, banner above it or None, concept it means)
COVERAGE = [
    # a property developer's inventory: work in progress and finished stock
    ("Properties under development 發展中物業", "balance_sheet", _CURRENT_ASSETS, "bs_current_assets__properties_under_development"),
    ("Completed properties held for sale 持作出售已落成物業", "balance_sheet", None, "bs_current_assets__completed_properties_held_for_sale"),
    # cash that is NOT freely available
    ("Restricted cash 受限制現金", "balance_sheet", None, "bs_current_assets__restricted_cash"),
    # related-party balances, and the mirror on the other side of the balance sheet
    ("Due from related parties 應收關聯方款項", "balance_sheet", None, "bs_current_assets__due_from_related_parties"),
    ("Due to related parties 應付關聯方款項", "balance_sheet", None, "bs_current_liabilities__due_to_related_parties"),
    ("Prepaid income tax 預付稅項", "balance_sheet", None, "bs_current_assets__prepaid_income_tax"),
    # a P&L line of its own — its absence had let administrative expenses absorb it
    ("Other expenses 其他開支", "profit_and_loss", None, "pl_expenses__other_expenses"),
    # A developer's own wording for cost of sales. Measured on a real filing: "Direct costs 直接成本
    # (814,645)" matched nothing, was swept into a bucket under a stale banner, and gross profit came
    # out of the wrong number. The template is fixed, so an unrecognised cost of sales does not go
    # missing quietly — it turns up somewhere it changes a subtotal.
    ("Direct costs 直接成本", "profit_and_loss", None, "pl_expenses__cost_of_goods_sold"),
    # associates and joint ventures: balance-sheet investments ...
    ("Investments in associates 於聯營公司的投資", "balance_sheet", None, "bs_non_current_assets__interests_in_associates"),
    ("Investments in joint ventures 於合營公司的投資", "balance_sheet", None, "bs_non_current_assets__interests_in_joint_ventures"),
    # ... and the P&L "share of profits and losses of:" lines beneath their own heading
    ("Share of profits and losses of: 應佔下列各項溢利及虧損： Joint ventures 合營公司", "profit_and_loss", None, "pl_exceptional_items__share_of_profits_and_losses_of_joint_ventures"),
    ("Associates 聯營公司", "profit_and_loss", None, "pl_exceptional_items__share_of_profits_and_losses_of_associates"),
    # the attribution of the year's result — a flow, not the equity balance
    ("Profit/(loss) attributable to: 下列各項應佔溢利╱（虧損）： Owners of the parent 母公司擁有人", "profit_and_loss", None, "pl_profit_attributable_to__owners_of_the_parent"),
    ("Non-controlling interests 非控股權益", "profit_and_loss", _PROFIT_SPLIT, "pl_profit_attributable_to__non_controlling_interests"),
    # balance-sheet subtotals of the net-current-assets presentation format
    ("NET CURRENT ASSETS/(LIABILITIES) 流動資產╱（負債）淨額", "balance_sheet", None, "bs_net_current_assets_liabilities"),
    ("TOTAL ASSETS LESS CURRENT 總資產減流動負債 LIABILITIES", "balance_sheet", None, "bs_total_assets_less_current_liabilities"),
    # the other-comprehensive-income subtotal
    ("OTHER COMPREHENSIVE LOSS 年內其他全面虧損 FOR THE YEAR", "profit_and_loss", None, "pl_other_comprehensive_income_for_the_year"),
]


@pytest.mark.parametrize("caption,statement,section,expected", COVERAGE,
                         ids=[c.split()[0].lower() + "-" + e.rsplit("__", 1)[-1]
                              for c, _s, _b, e in COVERAGE])
def test_previously_uncovered_captions_now_map(matcher, caption, statement, section, expected):
    assert _key(matcher, caption, statement, section) == expected


def test_a_family_caption_alone_is_refused_rather_than_guessed(matcher):
    """The other half of the two banner-carrying entries above, and the reason they carry one.

    Both captions name a collision family, so their own words cannot say which member they are: a
    developer prints "Properties under development" in current assets and a landlord in non-current,
    and one income statement prints "Non-controlling interests" under the profit split AND under the
    comprehensive-income split. The matcher refuses rather than picking a member — the answer would be
    a real figure filed on the wrong concept at full confidence, which no subtotal catches because
    both members sit in the same total.
    """
    assert _key(matcher, "Properties under development 發展中物業", "balance_sheet") is None
    assert _key(matcher, "發展中物業", "balance_sheet") is None
    # …and the banner resolves it, in either section.
    assert _key(matcher, "發展中物業", "balance_sheet", _CURRENT_ASSETS) == \
        "bs_current_assets__properties_under_development"
    assert _key(matcher, "發展中物業", "balance_sheet", "NON-CURRENT ASSETS 非流動資產") == \
        "bs_non_current_assets__properties_under_development"


def test_every_newly_covered_concept_exists_in_the_template_and_ontology():
    """Coverage is only real if both halves ship it: a template node to hold the value and an
    ontology mapping with genuine criteria to recognise it."""
    from app.schemas.loader import (
        load_ontology, load_template, validate_ontology_against_template, validate_template,
    )

    tpl, ont = load_template(TEMPLATE), load_ontology(ONTOLOGY, resolve=True)
    assert validate_template(tpl) == []
    assert validate_ontology_against_template(ont, tpl) == []

    template_keys = tpl.all_canonical_keys()
    by_key = {m.canonical_key: m for m in ont.mappings}
    for _caption, _statement, _banner, key in COVERAGE:
        assert key in template_keys, f"{key} missing from the template"
        m = by_key.get(key)
        assert m is not None, f"{key} has no ontology mapping"
        assert m.definition and m.include and m.exclude, f"{key} lacks mapping criteria"
        assert m.label and m.aliases_i18n.get("en") and m.aliases_i18n.get("zh"), key
        # the Traditional Chinese wording travels in the zh list (only en/zh locales exist)
        assert set(m.aliases_i18n) <= {"en", "zh"}, key


def test_traditional_and_simplified_wordings_both_resolve(matcher):
    """A HK filing prints Traditional; a mainland one prints Simplified. Same concept."""
    for row in [
        ("發展中物業", "发展中物业", "bs_current_assets__properties_under_development",
         _CURRENT_ASSETS),
        ("持作出售已落成物業", "持作出售已落成物业",
         "bs_current_assets__completed_properties_held_for_sale"),
        ("受限制現金", "受限制现金", "bs_current_assets__restricted_cash"),
        ("應收關聯方款項", "应收关联方款项", "bs_current_assets__due_from_related_parties"),
        ("預付稅項", "预付税项", "bs_current_assets__prepaid_income_tax"),
    ]:
        section = rest[0] if (rest := row[3:]) else None
        trad, simp, key = row[0], row[1], row[2]
        assert _key(matcher, trad, "balance_sheet", section) == key, trad
        assert _key(matcher, simp, "balance_sheet", section) == key, simp


# ---- disambiguation: the two confirmed mis-mappings ---------------------------------------

def test_total_assets_less_current_liabilities_is_not_total_current_liabilities(matcher):
    """Confirmed mis-mapping #1. Both captions are printed on the same balance-sheet page and
    share the words 'total', 'current' and 'liabilities', but they are different figures
    (36,356,879 vs 131,532,808) — one canonical key cannot hold both."""
    less = _key(matcher, "TOTAL ASSETS LESS CURRENT 總資產減流動負債 LIABILITIES", "balance_sheet")
    total = _key(matcher, "Total current liabilities 流動負債總額", "balance_sheet")
    assert less == "bs_total_assets_less_current_liabilities"
    assert total == "bs_current_liabilities__total_current_liabilities"
    assert less != total
    # ... in either script, and for the un-wrapped English wording too
    assert _key(matcher, "總資產減流動負債", "balance_sheet") == less
    assert _key(matcher, "Total assets less current liabilities", "balance_sheet") == less
    assert _key(matcher, "流動負債總額", "balance_sheet") == total


def test_total_comprehensive_loss_is_not_the_profit_or_loss_bottom_line(matcher):
    """Confirmed mis-mapping #2 — resolved by wording, NOT by alias priority.

    "Loss for the year" is the profit-or-loss bottom line in essentially every IFRS filing, so
    the bare phrase must resolve there. Total comprehensive income is only claimed by captions
    that actually say "total comprehensive".

    The consequence for THIS filing is deliberate and stated rather than papered over: its
    comprehensive-income row loses its "TOTAL COMPREHENSIVE" head during reconstruction and
    reaches the matcher as the bare fragment, so it collides with the real P&L line and the
    structural layer raises `ambiguous_mapping` for review. Handing the bare phrase to total
    comprehensive income would silence that one flag by mis-mapping the bottom line of every
    other filing — a far worse trade. The real defect is upstream, in label reconstruction.
    """
    profit_or_loss = "pl_profit_for_the_year"
    comprehensive = "pl_total_comprehensive_income_for_the_year"

    # The bare and bilingual P&L wordings both land on the bottom line.
    assert _key(matcher, "LOSS FOR THE YEAR", "profit_and_loss") == profit_or_loss
    assert _key(matcher, "Loss for the year", "profit_and_loss") == profit_or_loss
    assert _key(matcher, "LOSS FOR THE YEAR 年內虧損", "profit_and_loss") == profit_or_loss
    assert _key(matcher, "年內虧損", "profit_and_loss") == profit_or_loss
    assert _key(matcher, "Profit for the year", "profit_and_loss") == profit_or_loss

    # Only captions that say "total comprehensive" claim the comprehensive-income line.
    assert _key(matcher, "TOTAL COMPREHENSIVE LOSS FOR THE YEAR 年內全面虧損總額",
                "profit_and_loss") == comprehensive
    assert _key(matcher, "Total comprehensive loss for the year", "profit_and_loss") == comprehensive
    assert _key(matcher, "年內全面虧損總額", "profit_and_loss") == comprehensive

    assert _key(matcher, "OTHER COMPREHENSIVE LOSS 年內其他全面虧損 FOR THE YEAR",
                "profit_and_loss") == "pl_other_comprehensive_income_for_the_year"


def test_the_two_confusable_pairs_declare_each_other():
    """``confusable_with`` and the exclusion criteria are what a description-driven (LLM) mapping
    reads, so the separation has to live in the DATA and not only in the alias index."""
    from app.schemas.loader import load_ontology

    by_key = {m.canonical_key: m for m in load_ontology(ONTOLOGY, resolve=True).mappings}
    for a, b in [("bs_total_assets_less_current_liabilities",
                  "bs_current_liabilities__total_current_liabilities"),
                 ("pl_total_comprehensive_income_for_the_year", "pl_profit_for_the_year")]:
        assert b in by_key[a].confusable_with, f"{a} should name {b}"
        assert a in by_key[b].confusable_with, f"{b} should name {a}"
        # and each spells out, in words, why the other one is not it
        assert any(term in " ".join(by_key[a].exclude).lower()
                   for term in ("total current liabilities", "profit"))
        assert any(term in " ".join(by_key[b].exclude).lower()
                   for term in ("assets less current liabilities", "comprehensive"))


def test_exclude_hints_stop_the_rule_tier_cross_firing():
    """The rule tier matches on regexes, where 'total ... current liabilities' overlap is exactly
    what went wrong. Each of the pair recognises itself and refuses the other's wording."""
    import re

    from app.schemas.loader import load_ontology

    by_key = {m.canonical_key: m for m in load_ontology(ONTOLOGY, resolve=True).mappings}
    less = by_key["bs_total_assets_less_current_liabilities"]
    total = by_key["bs_current_liabilities__total_current_liabilities"]

    def fires(m, text: str) -> bool:
        low = text.lower()
        if any(re.search(rx, low) for rx in m.exclude_hints):
            return False
        return any(re.search(rx, low) for rx in m.regex_hints)

    assert fires(less, "TOTAL ASSETS LESS CURRENT LIABILITIES")
    assert not fires(total, "TOTAL ASSETS LESS CURRENT LIABILITIES")
    assert fires(total, "Total current liabilities")
    assert not fires(less, "Total current liabilities")
    # and neither claims the third line of the same family
    assert not fires(total, "NET CURRENT ASSETS/(LIABILITIES)")
    assert not fires(less, "NET CURRENT ASSETS/(LIABILITIES)")


def test_the_equity_nci_balance_and_the_pl_nci_attribution_stay_apart(matcher):
    """'Non-controlling interests 非控股權益' is printed twice in the same filing: once as the
    equity BALANCE and once as the attribution of the year's RESULT. Which concept it is depends
    on the statement it was printed on."""
    assert _key(matcher, "Non-controlling interests 非控股權益",
                "balance_sheet") == "bs_equity__non_controlling_interests"
    # Within the income statement the statement alone is not enough — the same caption splits both
    # profit and total comprehensive income — so the banner above it is what decides.
    assert _key(matcher, "Non-controlling interests 非控股權益", "profit_and_loss",
                _PROFIT_SPLIT) == "pl_profit_attributable_to__non_controlling_interests"


def test_associates_and_joint_ventures_no_longer_land_on_subsidiaries(matcher):
    """Both equity-accounted captions used to fuzzy-match 'investments in subsidiaries' on the
    shared words, putting two conflicting values on one key."""
    jv = _key(matcher, "Investments in joint ventures 於合營公司的投資", "balance_sheet")
    assoc = _key(matcher, "Investments in associates 於聯營公司的投資", "balance_sheet")
    assert jv == "bs_non_current_assets__interests_in_joint_ventures"
    assert assoc == "bs_non_current_assets__interests_in_associates"
    assert len({jv, assoc, "bs_non_current_assets__investments_in_subsidiaries"}) == 3


# ---- the new subtotals are arithmetically real --------------------------------------------

def test_the_new_subtotals_reconcile_on_the_real_figures():
    """The added subtotal nodes declare their own arithmetic, so the structural layer can now
    verify them. Figures are the filing's own (CNY'000, FY2023)."""
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.schemas.loader import load_template
    from app.services.structural_checks import evaluate_structure

    figures = {
        "bs_non_current_assets__total_non_current_assets": 49_227_234,
        "bs_current_assets__total_current_assets": 118_662_453,
        "bs_current_liabilities__total_current_liabilities": 131_532_808,
        "bs_net_current_assets_liabilities": -12_870_355,
        "bs_total_assets_less_current_liabilities": 36_356_879,
        "pl_profit_before_tax": -8_211_620,
        "pl_tax_expense__total_tax_expense": -189_504,
        "pl_profit_for_the_year": -8_401_124,
        "pl_other_comprehensive_income_for_the_year": -499_675,
        "pl_total_comprehensive_income_for_the_year": -8_900_799,
    }
    items = []
    for key, num in figures.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(num), value_raw=Decimal(num),
                                    basis=Basis.CONSOLIDATED, period_label="current"))
        items.append(li)

    report = evaluate_structure(load_template(TEMPLATE), items)
    passed = {r.rule_id for r in report.results if r.status == "pass"}
    for rule in ("rollup:bs_net_current_assets_liabilities",
                 "rollup:bs_total_assets_less_current_liabilities",
                 "rollup:pl_total_comprehensive_income_for_the_year",
                 "rollup:pl_profit_for_the_year"):
        assert rule in passed, f"{rule} should reconcile: {[r for r in report.results if r.rule_id == rule]}"
    assert report.failures() == []
