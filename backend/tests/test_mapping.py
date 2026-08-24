from __future__ import annotations

from app.core.models.enums import MappingMethod
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.mapping import OntologyMatcher, normalize_label


def _ontology() -> OntologyDefinition:
    return OntologyDefinition(
        ontology_key="test",
        target_template_key="t",
        mappings=[
            OntologyMapping(
                canonical_key="assets.current.cash",
                aliases=["Cash and cash equivalents", "Cash & bank balances"],
                keyword_hints=["cash"],
                exclude_hints=["restricted cash"],
            ),
            OntologyMapping(
                canonical_key="assets.current.receivables",
                aliases=["Trade receivables", "Trade and other receivables"],
            ),
        ],
    )


def test_normalize_label():
    assert normalize_label("Cash & Bank  Balances!") == "cash bank balances"


def test_exact_match_early_exits():
    m = OntologyMatcher(_ontology())
    r = m.match("Cash and cash equivalents")
    assert r.canonical_key == "assets.current.cash"
    assert r.method == MappingMethod.EXACT and r.confidence == 1.0


def test_a_typo_is_left_for_a_human_rather_than_guessed():
    """There is no string-similarity tier: a misspelling maps to NOTHING and says so.

    It used to be absorbed, which is the same mechanism that filed "Profit before exceptional items
    and tax" as ``profit_before_tax`` on the shipped rulebook — two subtotals differing by exactly
    the exceptional items, so the figure landed on the wrong line and the statement still tied. A
    caption nothing can place is now a visible gap. The remedy is authored, not inferred: an alias
    for the wording the filer actually prints, or a rule hint."""
    m = OntologyMatcher(_ontology())
    r = m.match("Trade recievables")  # misspelled
    assert r.canonical_key is None and r.needs_review

    ont = _ontology()
    next(x for x in ont.mappings
         if x.canonical_key == "assets.current.receivables").aliases.append("Trade recievables")
    fixed = OntologyMatcher(ont).match("Trade recievables")
    assert fixed.canonical_key == "assets.current.receivables"
    assert fixed.method is MappingMethod.EXACT


def test_unmatched_routes_to_review():
    m = OntologyMatcher(_ontology())
    r = m.match("Deferred tax liability (net)")
    assert r.canonical_key is None or r.needs_review


def test_exclude_hints_veto_a_concept_across_every_tier():
    """The field is named exclude and the ontology editor presents it as "never map a caption
    like this here", so it has to hold everywhere — not only in the rule tier. While it applied
    to rules alone, an excluded caption still arrived via fuzzy or an alias, and an analyst
    adding the exclusion to fix a mis-mapping would see nothing change.
    """
    from app.schemas.ontology import OntologyDefinition, OntologyMapping
    from app.services.mapping import OntologyMatcher

    onto = OntologyDefinition(
        ontology_key="t", target_template_key="t", locale="en",
        mappings=[
            OntologyMapping(
                canonical_key="cf_cash_flow_from_investing_activities__disposal_of_subsidiaries",
                label="Disposal of subsidiaries",
                aliases=["Disposal of subsidiaries"],
                # A P&L add-back shares the wording but is not a movement of cash.
                exclude_hints=[r"\b(gain|loss)s? on disposal\b"],
            ),
        ],
    )
    m = OntologyMatcher(onto, locale="en")

    # The caption the concept is for still maps.
    assert m.match("Disposal of subsidiaries").canonical_key.endswith("disposal_of_subsidiaries")
    # The excluded wording does not — via the alias/exact tier...
    assert m.match("Gain on disposal of subsidiaries").canonical_key is None
    # ...nor via fuzzy, which is where it used to slip through.
    assert m.match("Gain on disposal of subsidiaries, net").canonical_key is None


def test_a_hint_typed_in_the_case_a_human_uses_still_fires():
    """THE DEFECT THIS CLOSES, found on a reviewer's own edits and silent by construction.

    A hint is a REGEX, and it is searched against a caption the matcher has already lowercased while
    the pattern is taken verbatim from the rulebook. So a hint typed the way anyone writes a caption
    — "Finance Cost", "Non-current Assets", "Short term" — matched nothing, ever. Every one of the
    415 hints shipped in the rulebook happens to be lowercase, so no shipped behaviour was wrong and
    no test failed; the trap was waiting for the next person to add one. A reviewer added thirteen
    through the ontology workbook and hit it thirteen times out of thirteen: each exclusion looked
    accepted, appeared in the workbook, and did nothing.

    Both hint families are asserted, because they are read at two different sites.
    """
    onto = OntologyDefinition(
        ontology_key="t", target_template_key="t", locale="en",
        mappings=[
            OntologyMapping(
                canonical_key="pl_expenses__others",
                label="Other expenses",
                # The second alias is the one the exclusion has to beat. Without it the excluded
                # caption reaches no tier of this concept and the assertion below would hold for the
                # wrong reason — the exclusion never being consulted at all.
                aliases=["Other expenses", "Finance costs and other items"],
                # Capitalised exactly as a reviewer typed it into the workbook.
                exclude_hints=["Finance Cost"],
            ),
            OntologyMapping(
                canonical_key="bs_current_liabilities__bills_payable",
                label="Bills payable",
                # No alias to match on: the regex hint is the only route to this concept.
                regex_hints=[r"Bills? Payable"],
            ),
        ],
    )
    m = OntologyMatcher(onto, locale="en")

    # The concept still claims the caption it is for.
    assert m.match("Other expenses").canonical_key == "pl_expenses__others"
    # …and the capitalised exclusion is honoured against a caption printed in ordinary sentence case,
    # which is the pairing that failed: pattern "Finance Cost" against text "finance costs …". This
    # caption is an exact alias of the concept, so only the exclusion can keep it out.
    assert m.match("Finance costs and other items").canonical_key != "pl_expenses__others"
    # The other site. A capitalised regex hint reaches its concept.
    assert m.match("Bills payable").canonical_key == "bs_current_liabilities__bills_payable"
