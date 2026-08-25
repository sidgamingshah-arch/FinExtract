"""A caption is scoped by the section banner it was printed under.

Statements reuse one caption across two sections. A property developer prints
"Interest-bearing bank and other borrowings" once under non-current liabilities and again under
current, and "Senior notes and domestic bonds" the same way; "Properties under development"
appears under both asset sections. The words are byte-identical, so the caption alone cannot say
which concept it is — the banner above the row is the only distinguishing evidence on the page,
and without it both rows collapse onto one concept and one of the two figures is lost.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.loader import load_ontology
from app.services.mapping import OntologyMatcher

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def matcher() -> OntologyMatcher:
    definition = json.loads((TEMPLATES / "hkfrs_hk_china_ontology.json").read_text())
    return OntologyMatcher(load_ontology(definition), locale="zh")


@pytest.mark.parametrize("caption,banner,expected", [
    ("Interest-bearing bank and 計息銀行及其他貸款 other borrowings",
     "CURRENT LIABILITIES 流動負債", "bs_current_liabilities__current_borrowings"),
    ("Interest-bearing bank and 計息銀行及其他貸款 other borrowings",
     "NON-CURRENT LIABILITIES 非流動負債",
     "bs_non_current_liabilities__non_current_borrowings"),
    ("Senior notes and domestic bonds 優先票據及境內債券",
     "CURRENT LIABILITIES 流動負債", "bs_current_liabilities__current_notes_payable"),
    ("Senior notes and domestic bonds 優先票據及境內債券",
     "NON-CURRENT LIABILITIES 非流動負債",
     "bs_non_current_liabilities__non_current_notes_payable"),
    ("Properties under development 發展中物業", "CURRENT ASSETS 流動資產",
     "bs_current_assets__properties_under_development"),
    ("Properties under development 發展中物業", "NON-CURRENT ASSETS 非流動資產",
     "bs_non_current_assets__properties_under_development"),
])
def test_the_same_caption_resolves_differently_under_each_banner(matcher, caption, banner,
                                                                 expected):
    got = matcher.match(caption, statement="balance_sheet", section=banner)
    assert got.canonical_key == expected


def test_an_umbrella_banner_constrains_nothing(matcher):
    """IFRS statements print "EQUITY AND LIABILITIES" above the Equity / Non-current / Current
    sub-banners. Reading that as the equity section would refuse every liability concept
    beneath it, so a banner naming more than one section scopes nothing."""
    assert matcher._section_of("EQUITY AND LIABILITIES") is None
    got = matcher.match("Trade and bills payables 貿易應付款項及票據",
                        statement="balance_sheet", section="EQUITY AND LIABILITIES")
    assert got.canonical_key == "bs_current_liabilities__current_trade_payables"


def test_an_unrecognised_banner_constrains_nothing(matcher):
    """Same principle as the statement constraint: suppress a confident wrong answer, never
    drop a concept we merely could not place."""
    assert matcher._section_of("ASSETS") is None
    got = matcher.match("Contract liabilities 合同負債", statement="balance_sheet",
                        section="SOMETHING WE DO NOT KNOW")
    assert got.canonical_key == "bs_current_liabilities__contract_liabilities"


def test_a_key_with_no_section_namespace_is_always_allowed(matcher):
    """"TOTAL ASSETS LESS CURRENT LIABILITIES" is printed inside the current-liabilities block
    on a real filing, but its concept belongs to no section."""
    assert matcher._in_section("bs_total_assets", "CURRENT LIABILITIES 流動負債")
    assert matcher._in_section("pl_profit_before_tax", "CURRENT ASSETS")


def test_cash_flow_activities_scope_their_captions(matcher):
    """"Interest received" and "Dividends received" are printed under both operating and
    investing activities."""
    assert not matcher._in_section(
        "cf_cash_flow_from_operating_activities__interest_received",
        "CASH FLOWS FROM INVESTING ACTIVITIES 投資活動所得現金流量")
    assert matcher._in_section(
        "cf_cash_flow_from_investing_activities__interest_received",
        "CASH FLOWS FROM INVESTING ACTIVITIES 投資活動所得現金流量")


def test_the_banner_is_carried_from_the_page_onto_each_row():
    """The gate is worthless unless reconstruction actually records the banner, which it can
    only do by remembering a label-only row before discarding it."""
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word, build_line_items

    def word(text: str, x: float, y: float) -> Word:
        return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + 0.08, y1=y + 0.01))

    words = [
        word("NON-CURRENT", 0.10, 0.10), word("LIABILITIES", 0.20, 0.10),
        word("Interest-bearing", 0.10, 0.14), word("borrowings", 0.22, 0.14),
        word("9,817,976", 0.70, 0.14),
        word("CURRENT", 0.10, 0.20), word("LIABILITIES", 0.19, 0.20),
        word("Interest-bearing", 0.10, 0.24), word("borrowings", 0.22, 0.24),
        word("10,275,584", 0.70, 0.24),
    ]
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native")
    banners = [li.section_hint for li in items]
    assert banners == ["NON-CURRENT LIABILITIES", "CURRENT LIABILITIES"], banners


def test_a_wrapped_statement_title_does_not_scope_the_rows_beneath_it():
    """A title that wraps leaves a second line which, read alone, IS a section banner.

    "CONSOLIDATED STATEMENT OF PROFIT OR LOSS" / "AND OTHER COMPREHENSIVE INCOME" is how an HKEX
    filing prints its income statement, and ``section_of_banner`` resolves that second line to the
    other-comprehensive-income section — so the whole income statement was scoped into OCI and its
    tax line resolved to an OCI concept instead of the tax total. Nothing in a financial statement
    opens a section with "and", which is what makes the tail recognisable.

    The assertion is that whatever gets recorded SCOPES NOTHING, not that nothing is recorded. The
    title's FIRST line is still remembered — it is ALL-CAPS and label-only, so reconstruction cannot
    tell it from a banner — but it resolves to no section, so the gate it feeds is a no-op. It is
    the wrapped tail that resolves to a real section, and that is the one that did damage.
    """
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word, build_line_items

    def word(text: str, x: float, y: float) -> Word:
        return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + 0.08, y1=y + 0.01))

    words = [
        word("CONSOLIDATED", 0.10, 0.06), word("STATEMENT", 0.22, 0.06),
        word("OF", 0.32, 0.06), word("PROFIT", 0.36, 0.06), word("OR", 0.44, 0.06),
        word("LOSS", 0.48, 0.06),
        word("AND", 0.10, 0.09), word("OTHER", 0.15, 0.09),
        word("COMPREHENSIVE", 0.23, 0.09), word("INCOME", 0.37, 0.09),
        word("Income", 0.10, 0.14), word("tax", 0.18, 0.14), word("expense", 0.23, 0.14),
        word("(189,504)", 0.70, 0.14),
    ]
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native")

    from app.services.mapping import normalize_label, section_of_banner

    assert [li.source_label for li in items] == ["Income tax expense"]
    hint = items[0].section_hint
    assert section_of_banner(normalize_label(hint or "")) is None, hint
    # Specifically not the section the tail resolves to on its own, which is the whole defect.
    assert section_of_banner(normalize_label("AND OTHER COMPREHENSIVE INCOME")) is not None


def test_a_units_caption_is_not_a_banner_whatever_glyph_it_is_printed_with():
    """The column-units line scoped every row beneath it, and for two compounding reasons.

    The rulebook declares a unit-and-currency annotation strip, and it was matching only the
    STRAIGHT apostrophe while a typeset filing prints the typographic one — so "RMB’000" survived
    normalisation, and being ALL-CAPS with no colon it read as a banner. That is fixed in
    ``_strip_literals``. This asserts the backstop as well, for a currency the rulebook was never
    told about: the decision is made on the TOKENS, so an undeclared "HK$’000" is refused too.
    """
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word, build_line_items, _strip_literals

    # The declared strip itself, on both glyphs. Before the fix only the straight one was removed.
    assert _strip_literals("RMB’000 RMB’000", ("RMB'000",)).strip() == ""
    assert _strip_literals("RMB'000", ("RMB'000",)).strip() == ""

    def word(text: str, x: float, y: float) -> Word:
        return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + 0.08, y1=y + 0.01))

    for units in ("RMB\u2019000", "HK$\u2019000"):
        words = [
            word("CURRENT", 0.10, 0.06), word("LIABILITIES", 0.19, 0.06),
            word(units, 0.60, 0.10), word(units, 0.72, 0.10),
            word("Trade", 0.10, 0.14), word("payables", 0.18, 0.14),
            word("1,234", 0.70, 0.14),
        ]
        items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native")
        assert [li.source_label for li in items] == ["Trade payables"], units
        # The banner above the units line still scopes the row: a units caption is not a section,
        # and it is not a section BOUNDARY either.
        assert items[0].section_hint == "CURRENT LIABILITIES", (units, items[0].section_hint)


def test_a_colon_sub_heading_is_kept_without_displacing_the_section():
    """Two jobs, two fields. A colon sub-heading must not become the section — the rows under
    "Adjustments for:" are still that section's rows — but discarding it loses the only thing that
    names some of them, so it is carried as ``group_hint`` instead."""
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word, build_line_items

    def word(text: str, x: float, y: float) -> Word:
        return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + 0.08, y1=y + 0.01))

    words = [
        word("CURRENT", 0.10, 0.06), word("LIABILITIES", 0.19, 0.06),
        word("Adjustments", 0.10, 0.10), word("for:", 0.22, 0.10),
        word("Depreciation", 0.10, 0.14), word("1,234", 0.70, 0.14),
        word("NON-CURRENT", 0.10, 0.18), word("LIABILITIES", 0.22, 0.18),
        word("Borrowings", 0.10, 0.22), word("5,678", 0.70, 0.22),
    ]
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native")

    assert [li.source_label for li in items] == ["Depreciation", "Borrowings"]
    assert items[0].section_hint == "CURRENT LIABILITIES"
    assert items[0].group_hint == "Adjustments for:"
    # A new section ends the sub-heading's scope; it does not leak past the banner.
    assert items[1].section_hint == "NON-CURRENT LIABILITIES"
    assert items[1].group_hint == ""


def test_two_concepts_may_claim_the_same_alias_and_the_banner_decides(matcher):
    """The income statement prints "Owners of the parent" and "Non-controlling interests" TWICE
    — once splitting profit for the year, once splitting total comprehensive income. Both
    concepts legitimately claim the bare caption, so the alias index has to keep both: keeping
    one made the other unreachable, and the row came back unmapped even though its concept
    existed."""
    profit = matcher.match("Non-controlling interests 非控股權益", statement="profit_and_loss",
                           section="Profit/(loss) attributable to")
    assert profit.canonical_key == "pl_profit_attributable_to__non_controlling_interests"

    tci = matcher.match("Non-controlling interests 非控股權益", statement="profit_and_loss",
                        section="Total comprehensive loss attributable to")
    assert tci.canonical_key == (
        "pl_total_comprehensive_income_attributable_to__non_controlling_interests")


def test_a_loss_making_filing_still_names_the_comprehensive_income_split(matcher):
    """A filing reporting a loss prints "Total comprehensive LOSS attributable to", and one
    covering both prints "income/(loss)". Matching on the word "income" missed every such
    heading and sent the comprehensive-income split into the profit split."""
    from app.services.mapping import section_of_banner

    for heading in ("Total comprehensive loss attributable to",
                    "Total comprehensive income attributable to",
                    "Total comprehensive income/(loss) attributable to",
                    "下列各項應佔全面虧損總額"):
        assert section_of_banner(heading) == "total_comprehensive_income_attributable_to", heading
    # ...while the profit split is still its own section.
    assert section_of_banner("Profit/(loss) attributable to") == "profit_attributable_to"


def test_a_heading_printed_on_the_same_line_as_its_first_figure_still_scopes_the_rows():
    """Bilingual filings routinely print a heading and its first amount on one line:
    "Total comprehensive loss attributable to: ... Owners of the parent  (1,234)". The heading
    never appears as a label-only row, but it still scopes that row and the ones below it."""
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word, build_line_items

    def word(text: str, x: float, y: float) -> Word:
        return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + 0.05, y1=y + 0.01))

    words = [
        *[word(t, 0.10 + i * 0.05, 0.10) for i, t in
          enumerate("Profit attributable to: Owners of the parent".split())],
        word("1,000", 0.74, 0.10), word("2,000", 0.86, 0.10),
        *[word(t, 0.10 + i * 0.05, 0.14) for i, t in enumerate("Non-controlling interests".split())],
        word("100", 0.74, 0.14), word("200", 0.86, 0.14),
        *[word(t, 0.10 + i * 0.05, 0.18) for i, t in
          enumerate("Total comprehensive loss attributable to: Owners of the parent".split())],
        word("3,000", 0.74, 0.18), word("4,000", 0.86, 0.18),
        *[word(t, 0.10 + i * 0.05, 0.22) for i, t in enumerate("Non-controlling interests".split())],
        word("300", 0.74, 0.22), word("400", 0.86, 0.22),
    ]
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native")
    hints = [i.section_hint for i in items]
    # The second "Non-controlling interests" is scoped by the comprehensive-income heading, not
    # by the profit heading four rows above it.
    assert hints[1] == "Profit attributable to"
    assert hints[3] == "Total comprehensive loss attributable to"


# --- the income statement's ordinary sections ---------------------------------------------------
# Until these entries existed, section_of_key returned None for 34 of the 173 shipped concepts, so
# a P&L banner scoped nothing and _in_section waved every one of them through.

def test_pl_section_banners_resolve():
    from app.services.mapping import section_of_banner

    assert section_of_banner("REVENUE") == "income"
    assert section_of_banner("Turnover") == "income"
    assert section_of_banner("EXPENSES") == "expenses"
    assert section_of_banner("Non-operating expenses") == "non_operating_expenses"
    assert section_of_banner("Exceptional items") == "exceptional_items"
    assert section_of_banner("Income tax expense") == "tax_expense"
    assert section_of_banner("Taxation") == "tax_expense"


def test_pl_section_keys_resolve_and_the_compound_wins():
    """`section_of_key` matches "_<tok>__" as a substring, and "_non_operating_expenses__" contains
    "_expenses__" — so the compound has to be tested first or every non-operating concept reports
    itself as an ordinary expense."""
    from app.services.mapping import section_of_key

    assert section_of_key("pl_income__revenue_from_operations") == "income"
    assert section_of_key("pl_expenses__cost_of_goods_sold") == "expenses"
    assert section_of_key("pl_non_operating_expenses__finance_costs") == "non_operating_expenses"
    assert section_of_key("pl_exceptional_items__impairment_loss") == "exceptional_items"
    assert section_of_key("pl_tax_expense__current_tax") == "tax_expense"
    # A statement-level total sits in no section and must stay unconstrained.
    assert section_of_key("pl_profit_before_tax") is None


# --- collision families: the banner scopes a key that carries no section namespace ---------------
# `section_of_key` reads the section out of the "_<token>__" in a canonical key, so a
# statement-level key (`pl_profit_for_the_year`) has no section and `_in_section` waves it through
# under every banner. That is right for a subtotal, and wrong for one leaf of a pair the banner is
# the only evidence between.

@pytest.fixture(scope="module")
def v2_matcher() -> OntologyMatcher:
    definition = json.loads((TEMPLATES / "hkfrs_hk_china_ontology.json").read_text())
    return OntologyMatcher(load_ontology(definition, resolve=True), locale="zh")


def test_the_two_bottom_lines_are_separated_only_by_the_banner(v2_matcher):
    """Both P&L bottom lines are statement-level keys with no section namespace, and the caption
    cannot tell them apart: a wrapped "TOTAL COMPREHENSIVE LOSS FOR THE YEAR" reaches the matcher as
    "LOSS FOR THE YEAR", which is an alias of the profit line. The banner is the whole distinction,
    so the profit line is refused under the comprehensive-income banner and nowhere else."""
    assert not v2_matcher._in_section("pl_profit_for_the_year", "TOTAL COMPREHENSIVE")
    assert v2_matcher._in_section("pl_total_comprehensive_income_for_the_year",
                                  "TOTAL COMPREHENSIVE")
    # Every other banner leaves both of them unconstrained, as any statement-level key must be —
    # `section_hint` is the nearest PRECEDING banner, so a bottom line often carries a stale one.
    for banner in ("EXPENSES", "REVENUE", "Profit attributable to", "SOMETHING WE DO NOT KNOW"):
        assert v2_matcher._in_section("pl_profit_for_the_year", banner), banner
        assert v2_matcher._in_section("pl_total_comprehensive_income_for_the_year", banner), banner


def test_a_family_leaf_is_named_by_the_section_in_its_own_key(v2_matcher):
    """Only the two bottom lines need naming by hand; every other family leaf is placed by the
    section already in its canonical key, so the families cost no second copy of that mapping."""
    from app.services.mapping import family_leaf_named_by

    assert family_leaf_named_by("bs_non_current_liabilities__non_current_borrowings",
                                "CURRENT LIABILITIES 流動負債") == (
        "bs_current_liabilities__current_borrowings")
    assert family_leaf_named_by("cf_cash_flow_from_operating_activities__interest_received",
                                "CASH FLOWS FROM INVESTING ACTIVITIES 投資活動") == (
        "cf_cash_flow_from_investing_activities__interest_received")
    # A banner naming the leaf's own section settles nothing — there is nothing to correct.
    assert family_leaf_named_by("bs_current_assets__properties_under_development",
                                "CURRENT ASSETS 流動資產") is None
    # Neither does a concept in no family, nor a banner that names no section.
    assert family_leaf_named_by("bs_current_assets__inventories", "CURRENT LIABILITIES") is None
    assert family_leaf_named_by("pl_profit_for_the_year", "EQUITY AND LIABILITIES") is None


def test_a_balance_sheet_tax_caption_is_not_a_tax_section_banner():
    """"income tax" is excluded from the tax vocabulary on purpose: three BS captions contain it
    and are their own concepts, so matching it here would scope a balance-sheet row to tax_expense
    and refuse every bs_ concept under it."""
    from app.services.mapping import section_of_banner

    assert section_of_banner("Deferred income tax assets") is None
    assert section_of_banner("Prepaid income tax") is None
    assert section_of_banner("Income tax payable") is None


# --- a re-route may only ever correct the SECTION, never the concept ----------------------------
# Each case below was a real, reproduced wrong figure: the family was declared, the banner
# identified exactly one sibling, and the answer was "corrected" onto a different thing at
# confidence 1.0 — with the subtotals still tying, so nothing downstream could see it.

def test_an_ordinary_comprehensive_income_page_title_does_not_restate_profit():
    """The bare word "comprehensive" is the only banner vocabulary for the comprehensive bottom
    line, and it is deliberately broad so "comprehensive loss" matches. An ordinary HKEX page
    title, "STATEMENT OF COMPREHENSIVE INCOME", is captured as the section_hint for every row on
    the page — which re-routed "Profit for the year" onto total comprehensive income for the whole
    statement, collapsing two figures onto one concept and leaving pl_profit_for_the_year empty."""
    from app.services.mapping import family_leaf_named_by

    for banner in ("STATEMENT OF COMPREHENSIVE INCOME",
                   "CONSOLIDATED STATEMENT OF COMPREHENSIVE INCOME",
                   "OTHER COMPREHENSIVE INCOME",
                   "Total comprehensive income attributable to:"):
        assert family_leaf_named_by("pl_profit_for_the_year", banner) is None, banner
        assert family_leaf_named_by("pl_total_comprehensive_income_for_the_year", banner) is None


def test_bonds_payable_is_not_notes_payable_in_the_wrong_section():
    """The template has no current-bonds node, so "CURRENT LIABILITIES" identified exactly one leaf
    — current NOTES payable — and a row printed "Bonds payable" was filed there instead of the
    residual bucket that was the correct answer."""
    from app.services.mapping import family_leaf_named_by

    assert family_leaf_named_by("bs_non_current_liabilities__non_current_bonds_payable",
                                "CURRENT LIABILITIES") is None


def test_only_the_same_thing_in_another_section_may_be_rerouted():
    """The durable guard, independent of whether the family declarations are right: strip the
    section namespace and the current/non-current wording, and the two leaves must be the same
    concept. This is what makes a mistaken future declaration inert rather than dangerous."""
    from app.services.mapping import _is_variant_of

    # Same thing, different section — the entire licence for re-routing.
    assert _is_variant_of("bs_current_liabilities__current_lease_liabilities",
                          "bs_non_current_liabilities__non_current_lease_liabilities")
    assert _is_variant_of("cf_cash_flow_from_operating_activities__interest_received",
                          "cf_cash_flow_from_investing_activities__interest_received")
    assert _is_variant_of("pl_profit_attributable_to__non_controlling_interests",
                          "pl_total_comprehensive_income_attributable_to__non_controlling_interests")
    # Different things that merely resemble a sibling.
    assert not _is_variant_of("bs_non_current_liabilities__non_current_bonds_payable",
                              "bs_current_liabilities__current_notes_payable")
    assert not _is_variant_of("bs_current_liabilities__current_deferred_revenue",
                              "bs_non_current_liabilities__non_current_deferred_income")
    # A statement-level key has no section variant to be confused with.
    assert not _is_variant_of("pl_profit_for_the_year",
                              "pl_total_comprehensive_income_for_the_year")


# --- the gate's source of truth is section_scope, not the canonical key -------------------------
# `section_scope` is resolved onto all 173 concepts and was then ignored: the gate derived a row's
# section from the KEY namespace. The two agree in the shipped file, which is why reading the key was
# survivable — and exactly why it was not defensible. They diverge the moment an editor declares a
# concept in a section its key name does not match, and then the gate quietly obeys the key while the
# rulebook, the only place a reviewer can look up what the pipeline does, says something else.

CASH = "bs_current_assets__cash_and_cash_equivalents"
CASH_CAPTION = "Cash and cash equivalents 現金及現金等價物"


def _matcher_with(edit) -> OntologyMatcher:
    definition = json.loads((TEMPLATES / "hkfrs_hk_china_ontology.json").read_text())
    edit(definition)
    return OntologyMatcher(load_ontology(definition, resolve=True), locale="zh")


def test_editing_section_scope_moves_the_concept_the_gate_will_accept():
    """The A/B. As shipped, cash is a current asset and the gate refuses it under the non-current
    banner. Redeclare its `section_scope` — and change nothing else, least of all its key — and the
    gate follows the declaration, in both directions."""
    def move_cash_to_non_current(d):
        for c in d["mappings"]:
            if c["canonical_key"] == CASH:
                c["section_scope"] = ["bs_s1_non_current_assets"]

    shipped = _matcher_with(lambda _d: None)
    assert shipped.match(CASH_CAPTION, statement="balance_sheet",
                         section="CURRENT ASSETS 流動資產").canonical_key == CASH
    assert shipped.match(CASH_CAPTION, statement="balance_sheet",
                         section="NON-CURRENT ASSETS 非流動資產").canonical_key != CASH

    edited = _matcher_with(move_cash_to_non_current)
    assert edited.match(CASH_CAPTION, statement="balance_sheet",
                        section="NON-CURRENT ASSETS 非流動資產").canonical_key == CASH
    assert edited.match(CASH_CAPTION, statement="balance_sheet",
                        section="CURRENT ASSETS 流動資產").canonical_key != CASH


def test_a_concept_may_be_declared_in_two_sections_and_the_banner_admits_both():
    """`section_scope` is a LIST, which the key namespace cannot express at all. A concept declared in
    both asset sections is claimable under either banner — the one thing reading the key could never
    do, however the key was spelled."""
    def widen_cash(d):
        for c in d["mappings"]:
            if c["canonical_key"] == CASH:
                c["section_scope"] = ["bs_s1_non_current_assets", "bs_s2_current_assets"]

    m = _matcher_with(widen_cash)
    assert m._sections_of(CASH) == frozenset({"non_current_assets", "current_assets"})
    for banner in ("CURRENT ASSETS 流動資產", "NON-CURRENT ASSETS 非流動資產"):
        assert m.match(CASH_CAPTION, statement="balance_sheet",
                       section=banner).canonical_key == CASH, banner


def test_a_top_level_scope_is_constrained_by_no_banner():
    """`bs_top_level` names no section, and must not be read as one. `section_hint` is the nearest
    PRECEDING banner, so a statement total routinely carries the banner of the last section above it
    — "TOTAL ASSETS LESS CURRENT LIABILITIES" is printed inside the current-liabilities block."""
    m = _matcher_with(lambda _d: None)
    assert m._sections_of("bs_total_assets") == frozenset()
    assert m._in_section("bs_total_assets", "CURRENT LIABILITIES 流動負債")
    assert m._in_section("bs_net_assets", "CURRENT ASSETS 流動資產")


def test_a_scope_id_names_its_section_and_the_compound_wins():
    """The scope ids carry the section's printed POSITION as well as its name, so the token is read
    off the end — longest-first, since "bs_s1_non_current_assets" also ends with "current_assets"."""
    from app.services.mapping import section_token_of_scope

    assert section_token_of_scope("bs_s1_non_current_assets") == "non_current_assets"
    assert section_token_of_scope("bs_s2_current_assets") == "current_assets"
    assert section_token_of_scope("bs_s4_non_current_liabilities") == "non_current_liabilities"
    assert section_token_of_scope("pl_s3_non_operating_expenses") == "non_operating_expenses"
    assert section_token_of_scope("pl_s2_expenses") == "expenses"
    assert section_token_of_scope("pl_s7_total_comprehensive_income_attributable_to") == (
        "total_comprehensive_income_attributable_to")
    assert section_token_of_scope("cf_s1_cash_flow_from_operating_activities") == (
        "cash_flow_from_operating_activities")
    for top in ("bs_top_level", "pl_top_level", "cf_top_level"):
        assert section_token_of_scope(top) is None, top


def test_editing_the_declared_statement_moves_the_concept_the_gate_will_accept():
    """The same A/B one level up, and the hole the section work left behind. The STATEMENT gate read
    ``canonical_key.split("_", 1)[0]`` — the key prefix — so redeclaring a whole section onto another
    statement changed nothing: `bs_current_liabilities__current_trade_payables` was still gated as a
    balance-sheet concept while the rulebook said profit_and_loss. The declaration is now the source
    and the key namespace only the fallback, exactly as for `section_scope`."""
    PAYABLES = "bs_current_liabilities__current_trade_payables"

    def move_current_liabilities_to_the_pl(d):
        d["section_defaults"]["bs_s3_current_liabilities"]["statement"] = "profit_and_loss"

    shipped = _matcher_with(lambda _d: None)
    assert shipped._statement_of(PAYABLES) == "balance_sheet"
    assert shipped._in_statement(PAYABLES, "balance_sheet")
    assert not shipped._in_statement(PAYABLES, "profit_and_loss")

    edited = _matcher_with(move_current_liabilities_to_the_pl)
    assert edited._statement_of(PAYABLES) == "profit_and_loss"
    assert not edited._in_statement(PAYABLES, "balance_sheet")
    assert edited._in_statement(PAYABLES, "profit_and_loss")
    # …and through `match`, where the gate is what the caption is actually judged by.
    caption = "Trade and bills payables 貿易應付款項及票據"
    assert edited.match(caption, statement="balance_sheet").canonical_key != PAYABLES
    assert edited.match(caption, statement="profit_and_loss").canonical_key == PAYABLES


def test_the_two_spellings_of_the_equity_statement_are_one_statement():
    """`StatementType` spells it "equity_changes"; the page classifier and every caller say
    "changes_in_equity". Compared raw, a concept declaring the enum's spelling is refused on every
    equity page — the declaration would scope nothing at all rather than scope it wrongly."""
    from app.services.mapping import normalize_statement

    assert normalize_statement("equity_changes") == "changes_in_equity"
    assert normalize_statement("changes_in_equity") == "changes_in_equity"
    assert normalize_statement(None) == ""

    def declare_cash_on_the_equity_statement(d):
        for c in d["mappings"]:
            if c["canonical_key"] == CASH:
                c["statement"] = "equity_changes"

    m = _matcher_with(declare_cash_on_the_equity_statement)
    assert m._statement_of(CASH) == "changes_in_equity"
    assert m._in_statement(CASH, "changes_in_equity")
    assert not m._in_statement(CASH, "balance_sheet")


def test_a_v1_concept_declares_no_scope_and_falls_back_to_its_key(matcher):
    """The fallback is the whole reason v1 keeps working: no `section_scope` and no `statement` is
    authored anywhere in it, so the key namespace is all there is."""
    assert not any(c.section_scope for c in matcher.ontology.mappings)
    assert not any(c.statement for c in matcher.ontology.mappings)
    assert matcher._sections_of("bs_current_liabilities__current_borrowings") == frozenset(
        {"current_liabilities"})
    assert matcher._sections_of("bs_total_assets") == frozenset()
    assert matcher._statement_of("bs_current_liabilities__current_borrowings") == "balance_sheet"
    assert matcher._statement_of("cf_net_increase_decrease_in_cash_and_cash_equivalents") == (
        "cash_flow")
    # A key from no statement namespace at all is placed by neither, and is therefore unconstrained.
    assert matcher._statement_of("something_else") is None
    assert matcher._in_statement("something_else", "balance_sheet")


def test_the_legitimate_variants_still_reroute():
    """The guard must not have disabled the feature it protects."""
    from app.services.mapping import family_leaf_named_by

    assert family_leaf_named_by("bs_non_current_liabilities__non_current_lease_liabilities",
                                "CURRENT LIABILITIES") \
        == "bs_current_liabilities__current_lease_liabilities"
    assert family_leaf_named_by("cf_cash_flow_from_operating_activities__interest_received",
                               "INVESTING ACTIVITIES") \
        == "cf_cash_flow_from_investing_activities__interest_received"
    assert family_leaf_named_by("pl_profit_attributable_to__owners_of_the_parent",
                               "Total comprehensive income attributable to:") \
        == "pl_total_comprehensive_income_attributable_to__owners_of_the_parent"
