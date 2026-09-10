"""The mapper OBEYS the v2 rulebook's control fields.

Three behaviours, each of which the schema declares and the matcher previously ignored:

* **the residual lock** — 13 concepts carry ``alias_matching: "disabled"``, ``match_priority: 0``
  and ``value_scope: "exclusive_residual"``. They are the section "Others" buckets: the section's
  parent minus its confirmed children, populated by the sweep and by nothing else. Reachable by
  matching they are the most attractive concepts in the file — "Others" is short enough to fuzz
  against anything, and a model offered a bucket will use it for a row it cannot place. Either way
  the figure lands in what is supposed to be the section's UNEXPLAINED remainder, and the
  reconciliation that would have reported the gap ties instead.
* **match_priority** — an ordering, so the long specific concept is read before the short generic
  one it collides with on token overlap. Not a score: it may not outrank evidence.
* **family resolution** — a decision that is right about what kind of thing a row is and wrong only
  about which section variant is corrected by the banner, not thrown away.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import get_settings
from app.core.models.enums import LineRole, MappingMethod
from app.schemas.loader import load_ontology
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.mapping import (
    LlmBatchDecision, LlmBatchItem, LlmMappingDecision, OntologyMatcher,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
V2 = json.loads((TEMPLATES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def v2():
    return load_ontology(V2, resolve=True)


def _matcher(ontology, provider=None, locale="zh") -> OntologyMatcher:
    return OntologyMatcher(ontology, locale=locale, settings=get_settings(),
                           llm_provider=provider)


class Spy:
    """Answers with fixed batch items (or abstains per line) and keeps every payload it was sent."""

    id = "fake"

    def __init__(self, items: list[LlmBatchItem] | None = None, single: str = ""):
        self._items = items or []
        self._single = single
        self.payloads: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        self.payloads.append(json.loads(messages[-1]["content"]))
        meta = {"model": "fake-llm", "input_tokens": 10, "output_tokens": 5}
        if response_schema is LlmBatchDecision:
            return LlmBatchDecision(mappings=list(self._items)), meta
        return LlmMappingDecision(canonical_key=self._single,
                                  confidence=0.95 if self._single else 0.0), meta

    def offered(self, call: int = 0) -> list[str]:
        return [c["canonical_key"] for c in self.payloads[call]["candidates"]]


# --- 1. the residual lock ----------------------------------------------------------------------

def test_the_thirteen_residual_buckets_are_locked_out_of_every_matching_index(v2):
    """One control field, checked in one place, keeps them out of all four matching tiers at once:
    the alias tier reads the alias index, the rule tier its hints, and the model the payload."""
    m = _matcher(v2)
    locked = {c.canonical_key for c in v2.mappings if c.alias_matching == "disabled"}

    assert len(locked) == 13, sorted(locked)
    assert all(k.endswith("__others") for k in locked)
    assert m._locked == locked
    # Out of the normalised alias index entirely — both directions of it.
    assert not [k for keys in m._alias_index.values() for k in keys if k in locked]
    assert not [k for k in m._alias_by_key if k in locked]
    # …and out of the set a caption may be mapped to.
    assert not (locked & set(m._extractable_keys()))


def test_a_balance_sheet_batch_stops_offering_the_five_asset_and_liability_buckets(v2):
    """Measured: of 78 balance-sheet concepts, 68 are offered. TWO declared reasons account for all
    ten that are not, and the last assertion is what pins that — a concept missing for a third
    reason would fail it.

      * FIVE ARE LOCKED: the bs "Others" buckets (`alias_matching: disabled`). All five declare
        `extraction_mode: extract`, so it is the lock and not the extract-only rule that withholds
        them — the residual's caption is the most attractive one in the ontology and a model offered
        a bucket will use it for a row it cannot place.
      * FIVE ARE DERIVABLE: the statement's `extract_or_derive` subtotals. A line the declared
        arithmetic can work out is not the model's to guess at. They stay fully matchable by the
        DETERMINISTIC tiers, which is why the boundary is applied where candidates are offered
        rather than in `_mappable_keys` — see
        `test_the_deterministic_tiers_still_reach_a_derivable_concept`.
    """
    m = _matcher(v2)
    bs = [c.canonical_key for c in v2.mappings if c.canonical_key.startswith("bs_")]
    offered = {e["canonical_key"] for e in m._concept_payload(bs)}

    buckets = {"bs_non_current_assets__others", "bs_current_assets__others", "bs_equity__others",
               "bs_non_current_liabilities__others", "bs_current_liabilities__others"}
    derivable = {c.canonical_key for c in v2.mappings
                 if c.canonical_key.startswith("bs_") and c.extraction_mode != "extract"}

    assert len(bs) == 78 and len(offered) == 68
    assert buckets <= m._locked, "the buckets are withheld by the LOCK, not by the offer rule"
    assert all(m._by_key[k].extraction_mode == "extract" for k in buckets)
    assert len(derivable) == 5, sorted(derivable)
    assert {k for k in bs if k not in offered} == buckets | derivable


def test_a_residual_bucket_cannot_win_however_its_hints_are_authored():
    """The lock is on the concept, not on the shipped file's authoring. The v2 buckets happen to
    carry no aliases at all today, so a lock that only relied on that would be untested and would
    fail open the moment an editor typed "Others" into the alias box — which is precisely what the
    v1 rulebook did, on ten of them.
    """
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key="bs_current_liabilities__others", label="Others",
                            value_scope="exclusive_residual", alias_matching="disabled",
                            match_priority=0, aliases=["Others", "Other payables"],
                            keyword_hints=["other"], regex_hints=[r"other"]),
            OntologyMapping(canonical_key="bs_current_liabilities__other_payables_and_accruals",
                            label="Other payables and accruals", aliases=["Other payables"],
                            match_priority=60, description="amounts owed other than trade"),
        ],
    )
    m = _matcher(ont)

    # An exact alias, a shared alias, a keyword hint and a regex hint: none of them reach it.
    assert m.match("Others").canonical_key != "bs_current_liabilities__others"
    assert m.match("Other payables").canonical_key == (
        "bs_current_liabilities__other_payables_and_accruals")
    assert m._rule("Other stuff entirely") is None
    # The lock itself: the concept is out of every matchable set, so no tier can reach it however
    # its hints are authored.
    assert "bs_current_liabilities__others" in m._unmatchable
    assert "bs_current_liabilities__others" not in m._mappable_keys()


def test_the_model_is_never_offered_a_bucket_on_either_llm_path(v2):
    """`_llm` and `match_batch` build their candidate lists differently — one from the
    deterministic shortlist, one from every concept on the statement — so the lock has to hold in
    the payload builder they share."""
    spy = Spy(items=[], single="")
    m = _matcher(v2, spy)

    m.match("A caption no concept covers at all", statement="balance_sheet")
    m.match_batch([("a", "Another caption no concept covers")], statement="cash_flow")

    for call in range(len(spy.payloads)):
        assert not [k for k in spy.offered(call) if k.endswith("__others")], spy.offered(call)


def test_the_sweep_still_reaches_a_bucket_the_matcher_cannot(v2):
    """Locked out of MATCHING is not locked out of the statement. The residual stage reads the
    template's section structure, never the matcher's indexes, so the bucket the mapper may not
    choose is still the one an unmapped face row is swept into — and the section still ties."""
    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import Basis, LineRole
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.core.stage import PipelineContext
    from app.schemas.loader import load_template
    from app.stages.residual import ResidualStage

    template = load_template(json.loads((TEMPLATES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))

    def li(ordinal: int, label: str, key: str | None, value: int,
           role: LineRole = LineRole.LINE) -> LineItem:
        item = LineItem(source_label=label, canonical_key=key, ordinal=ordinal, role=role)
        item.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=Basis.CONSOLIDATED,
            period_label="current", provenance=Provenance(page_index=0)))
        return item

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="balance_sheet")]
    doc.line_items = [
        li(0, "Trade and bills payables", "bs_current_liabilities__current_trade_payables", 100),
        li(1, "A caption no concept covers", None, 25),
        li(2, "Total current liabilities",
           "bs_current_liabilities__total_current_liabilities", 125, LineRole.SUBTOTAL),
    ]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = template
    ResidualStage().run(doc, ctx)

    assert doc.line_items[1].canonical_key == "bs_current_liabilities__others"
    # The same key, in the same run, is unreachable by matching.
    assert "bs_current_liabilities__others" in _matcher(v2)._locked


# --- 2. match_priority ordering ------------------------------------------------------------------

def test_the_colliding_pair_is_offered_in_the_order_the_rulebook_declares(v2):
    """`binding.match_priority`: "Long specific captions rank above short generic ones so 'Total
    assets less current liabilities' cannot be pre-empted by 'Total current liabilities' on token
    overlap." The order the list is read in was once the order an editor happened to add them to
    the file (71 before 73), and priority is what replaced that.

    THE ORIGINAL PAIR IS NO LONGER BOTH OFFERED. 'Total assets less current liabilities' is
    `extract_or_derive` — the framework can work it out — so the extract-only offer rule withholds
    it and the collision it guarded against can no longer arise in the CANDIDATE LIST at all. Both
    facts are asserted rather than the pair probe simply deleted: the concept's absence is pinned to
    its declared mode, so a future change that puts it back in front of the model re-arms the
    ordering question instead of silently dropping it. The ordering property itself is unchanged and
    still checked across the whole offered list, which is the stronger claim — it fails if ANY pair
    inverts, not only that one.

    The deterministic tiers still rank it: `_by_priority` and `_priority_of` read the rulebook, not
    the offer rule, so a filing that PRINTS the subtotal still has it read at the right priority.

    A LIVE COLLIDING PAIR REPLACES THE WITHDRAWN ONE, in the same call, because "no pair inverts" is
    also satisfiable by a list nothing ordered — an aggregate property alone would let the sort be
    deleted and still ship green. Measured on this file: "Current portion of long-term debt"
    (`match_priority` 68, declaration index 45) and "Borrowings (current)" (60, index 44) are both
    `extraction_mode: extract` and so both offered; each names the other in `confusable_with` (which
    the payload carries); and the long specific caption is DECLARED AFTER the short generic one, so a
    list read in declaration order puts the pair the wrong way round. That is the same shape the
    rulebook's own note describes, with a pair that survives the extract-only rule.

    WHY THIS CALL AND NOT A WIDER ONE. 12 candidates is under `llm_candidate_cap` (16), so
    `shortlist = all_keys` reaches the sort in DECLARATION order and `_by_priority` is the ONLY sort
    in the path — measured with that sort removed, the offered priorities come out
    [62, 62, 64, 56, 68, 60, 62, 60, 68, 54, 64, 82] and the pair inverts to 8 before 7, so both
    assertions below fail. A probe with no `section` would offer 32 and take the over-cap branch,
    whose expression already contains `_by_priority(all_keys)`; with the sort deleted such a probe
    still passes. Keep the section.
    """
    spy = Spy(single="")
    m = _matcher(v2, spy)
    m.match("Total current liabilites",           # OCR typo → no exact hit, so the LLM is consulted
            statement="balance_sheet", section="CURRENT LIABILITIES 流動負債")
    offered = spy.offered()

    withheld = "bs_total_assets_less_current_liabilities"
    assert m._by_key[withheld].extraction_mode == "extract_or_derive"
    assert withheld in m._llm_withheld and withheld not in offered
    # Still ranked, and still MATCHABLE, by the tiers that CAN bind it — which is what makes
    # withholding it from the OFFER safe rather than a concept lost to every tier.
    assert withheld in m._mappable_keys()
    assert m._priority_of(withheld) > m._priority_of(
        "bs_current_liabilities__total_current_liabilities")
    # The pair that IS still offered: priority, not declaration order, decides the reading order.
    specific = offered.index("bs_current_liabilities__current_portion_of_long_term_debt")   # 68
    generic = offered.index("bs_current_liabilities__current_borrowings")                   # 60
    assert specific < generic, list(zip(offered, [m._priority_of(k) for k in offered]))
    # …and the file declares them the wrong way round, which is the thing priority overrides. Without
    # this the assertion above would also pass on a list that was never sorted.
    decl = {mm.canonical_key: i for i, mm in enumerate(v2.mappings)}
    assert decl["bs_current_liabilities__current_portion_of_long_term_debt"] > decl[
        "bs_current_liabilities__current_borrowings"]
    # The whole list, not just one pair: descending declared priority.
    priorities = [m._priority_of(k) for k in offered]
    assert priorities == sorted(priorities, reverse=True), list(zip(offered, priorities))


def test_priority_orders_the_batch_candidates_too(v2):
    """One batch call offers the whole statement at once, so the order the list is written in is the
    only ranking the model gets."""
    spy = Spy(items=[])
    m = _matcher(v2, spy)
    m.match_batch([("a", "Some caption")], statement="balance_sheet")
    priorities = [m._priority_of(k) for k in spy.offered()]

    assert priorities == sorted(priorities, reverse=True)
    assert priorities[0] > priorities[-1], "the statement's concepts should not all tie"


def test_a_shared_alias_is_settled_by_priority_not_by_declaration_order(v2):
    """"Owners of the parent" is claimed byte-for-byte by the profit split (78) and the
    comprehensive-income split (82). With no banner to narrow it, the rulebook's binding order says
    to take the highest priority and says never to pick by declaration order — which is what taking
    the first claimant was, for the 83 aliases in this file that more than one concept claims."""
    m = _matcher(v2)

    assert m.match("Owners of the parent", statement="profit_and_loss").canonical_key == (
        "pl_total_comprehensive_income_attributable_to__owners_of_the_parent")
    # A banner still overrules priority: it is evidence, priority is only a tie-break.
    assert m.match("Owners of the parent", statement="profit_and_loss",
                   section="Profit attributable to").canonical_key == (
        "pl_profit_attributable_to__owners_of_the_parent")


def test_priority_orders_candidates_and_does_not_score_them(v2):
    """The dangerous reading of match_priority is as a weight. A concept with a high priority and no
    evidence must lose to one with low priority and a near-exact caption, or the highest-priority
    concept in the file quietly absorbs every unrecognised row."""
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key="bs_total_assets", label="Total assets",
                            aliases=["Total assets"], match_priority=99),
            OntologyMapping(canonical_key="bs_current_assets__inventories", label="Inventories",
                            aliases=["Inventories"], match_priority=10),
        ],
    )
    m = _matcher(ont)
    res = m.match("Inventories")                       # the low-priority concept's own alias
    assert res.canonical_key == "bs_current_assets__inventories"
    # …and the misspelling maps to NOTHING, deliberately: no tier decides on wording, so a caption
    # no alias and no rule claims is a visible gap rather than the highest-priority concept in the
    # file absorbing it.
    assert m.match("Inventorys").canonical_key is None

    # And on the real file, the model's answer still wins over the order it was offered in.
    spy = Spy(single="bs_current_liabilities__total_current_liabilities")
    got = _matcher(v2, spy).match("Total current liabilites", statement="balance_sheet",
                                  section="CURRENT LIABILITIES 流動負債")
    assert got.canonical_key == "bs_current_liabilities__total_current_liabilities"


def test_a_tie_on_token_overlap_is_settled_by_priority(v2):
    """Two concepts claiming the identical alias are separated by nothing the wording can offer, so
    which of them was returned first was dict insertion order. Priority is the rulebook's declared
    arbiter for exactly this tie — step 4 runs the alias tier in descending match_priority and says
    in as many words never to pick by declaration order."""
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            # Declared first, LOWER priority — so declaration order and priority disagree.
            OntologyMapping(canonical_key="bs_current_liabilities__current_deferred_revenue",
                            label="Deferred revenue", aliases=["Deferred income"],
                            match_priority=60),
            OntologyMapping(canonical_key="bs_non_current_liabilities__non_current_deferred_income",
                            label="Deferred income", aliases=["Deferred income"],
                            match_priority=62),
        ],
    )
    m = _matcher(ont)
    # Declared first is the LOWER priority, so declaration order and priority disagree here.
    assert m.match("Deferred income").canonical_key == (
        "bs_non_current_liabilities__non_current_deferred_income")
    # The misspelling is claimed by neither: an alias tier answers identity, not resemblance.
    assert m.match("Deferred incomes").canonical_key is None


def test_a_tie_inside_the_rule_tier_is_settled_by_priority_and_routed_to_review():
    """The same tie on the other deterministic tier. Two concepts' hints firing on one caption is
    an ambiguity no hint can resolve, so the answer is the highest-priority claimant — never the
    first declared — and it is scored below every accept bar so a human confirms it."""
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            # Declared first, LOWER priority, so declaration order and priority disagree.
            OntologyMapping(canonical_key="bs_current_assets__total_current_assets",
                            label="Total current assets", aliases=["Total current assets"],
                            keyword_hints=["current"], match_priority=60),
            OntologyMapping(canonical_key="bs_current_assets__inventories", label="Inventories",
                            aliases=["Inventories"], keyword_hints=["current"],
                            match_priority=82),
        ],
    )
    m = _matcher(ont)
    res = m.match("Something current, claimed by both hints")
    assert res.canonical_key == "bs_current_assets__inventories"
    assert res.method is MappingMethod.RULE and res.confidence == 0.6
    assert res.needs_review

    # One claimant is not a tie: it decides, and it decides on its own.
    ont.mappings[0].keyword_hints = []
    assert _matcher(ont).match("Something current").canonical_key == (
        "bs_current_assets__inventories")


# --- 3. family resolution ------------------------------------------------------------------------

BOTTOM_LINE = "pl_total_comprehensive_income_for_the_year"


def test_the_wrapped_bottom_line_is_rerouted_instead_of_discarded(v2):
    """The motivating case. A bilingual filing prints "TOTAL COMPREHENSIVE LOSS FOR THE YEAR"
    wrapped, so the banner row carries "TOTAL COMPREHENSIVE" and the caption reaching the matcher is
    the bare fragment "LOSS FOR THE YEAR" — an alias of the OTHER bottom line. The model answers
    `pl_profit_for_the_year`: right that the row is a bottom line, wrong about which. The banner
    identifies exactly one leaf of the family, so the answer is corrected."""
    spy = Spy(items=[LlmBatchItem(item_id="a", canonical_key="pl_profit_for_the_year",
                                  confidence=0.94)])
    m = _matcher(v2, spy)
    out = m.match_batch([("a", "LOSS FOR THE YEAR")], statement="profit_and_loss",
                        sections={"a": "TOTAL COMPREHENSIVE"})

    assert out["a"].canonical_key == BOTTOM_LINE
    assert out["a"].rerouted_from == "pl_profit_for_the_year"     # auditable, not silent
    assert m.usage["family_resolved"] == 1
    assert m.usage["family_routes"] == [f"pl_profit_for_the_year->{BOTTOM_LINE}"]
    assert m.usage["batch_refused"] == 0, "a re-route is preferred over a refusal"


def test_the_deterministic_path_resolves_the_same_row(v2):
    """The per-line path is all there is when no provider is configured, and the alias hit on the
    wrong leaf is real evidence of what the row is. Left alone it filed the largest figure on the
    statement as `pl_profit_for_the_year` at confidence 1.0, with the comprehensive-income line
    empty and the `pl_tci_tie` identity broken."""
    m = _matcher(v2)
    got = m.match("LOSS FOR THE YEAR", statement="profit_and_loss", section="TOTAL COMPREHENSIVE")

    assert got.canonical_key == BOTTOM_LINE
    assert got.method is MappingMethod.EXACT and got.rerouted_from == "pl_profit_for_the_year"
    # Without a banner, or under one that names no leaf of the family, nothing is corrected.
    assert m.match("LOSS FOR THE YEAR", statement="profit_and_loss").canonical_key == (
        "pl_profit_for_the_year")
    assert m.match("LOSS FOR THE YEAR", statement="profit_and_loss",
                   section="EXPENSES").canonical_key == "pl_profit_for_the_year"


def test_a_reroute_never_rewrites_an_answer_the_gate_accepted(v2):
    """Re-routing lives on the refusal branch only. An accepted answer — including the family leaf
    the banner does name — comes back exactly as the model gave it."""
    spy = Spy(items=[LlmBatchItem(item_id="a", canonical_key=BOTTOM_LINE, confidence=0.9),
                     LlmBatchItem(item_id="b", canonical_key="pl_profit_for_the_year",
                                  confidence=0.9)])
    m = _matcher(v2, spy)
    out = m.match_batch([("a", "Total comprehensive loss for the year"), ("b", "Loss for the year")],
                        statement="profit_and_loss", sections={"a": "TOTAL COMPREHENSIVE"})

    assert out["a"].canonical_key == BOTTOM_LINE and out["a"].rerouted_from is None
    assert out["b"].canonical_key == "pl_profit_for_the_year"      # no banner → nothing to settle
    assert m.usage["family_resolved"] == 0


def test_a_cross_section_answer_outside_any_family_is_still_refused(v2):
    """The gate's other arms are untouched: a current-liability concept for a row printed under
    current ASSETS is not a variant of the same fact, and no family covers it."""
    spy = Spy(items=[LlmBatchItem(item_id="a",
                                  canonical_key="bs_current_liabilities__current_trade_payables",
                                  confidence=0.99)])
    m = _matcher(v2, spy)
    out = m.match_batch([("a", "Trade and bills receivables")], statement="balance_sheet",
                        sections={"a": "CURRENT ASSETS 流動資產"})

    assert out["a"].canonical_key != "bs_current_liabilities__current_trade_payables"
    assert m.usage["batch_refused"] == 1 and m.usage["family_resolved"] == 0


def test_a_banner_naming_two_leaves_of_a_family_refuses_rather_than_guessing(v2):
    """A banner that settles nothing must not be treated as evidence.

    This used to be exercised via bonds payable, which shared the notes family and gave
    "NON-CURRENT LIABILITIES" two candidate leaves. Bonds has since been removed from the family
    outright — the template has no current-bonds node, so "CURRENT LIABILITIES" named exactly one
    leaf and a row printed "Bonds payable" was re-routed onto current NOTES payable, a different
    instrument, at confidence 1.0. So the ambiguity is now tested where it still exists: a banner
    naming a section no leaf of the family sits in.
    """
    spy = Spy(items=[LlmBatchItem(
        item_id="a", canonical_key="bs_current_liabilities__current_notes_payable",
        confidence=0.95)])
    m = _matcher(v2, spy)
    out = m.match_batch([("a", "Senior notes 優先票據")], statement="balance_sheet",
                        sections={"a": "NON-CURRENT ASSETS 非流動資產"})
    # The banner names a section the family has no leaf in, so nothing is corrected and the
    # answer is refused rather than moved somewhere the evidence does not point.
    assert out["a"].canonical_key != "bs_current_liabilities__current_notes_payable"
    assert m.usage["family_resolved"] == 0 and m.usage["batch_refused"] == 1

    # And with bonds out of the family, the non-current NOTES leaf is now unambiguous, so the
    # ordinary current/non-current correction this family exists for does fire.
    ok = Spy(items=[LlmBatchItem(
        item_id="a", canonical_key="bs_current_liabilities__current_notes_payable",
        confidence=0.95)])
    m3 = _matcher(v2, ok)
    out3 = m3.match_batch([("a", "Senior notes 優先票據")], statement="balance_sheet",
                          sections={"a": "NON-CURRENT LIABILITIES 非流動負債"})
    assert out3["a"].canonical_key == "bs_non_current_liabilities__non_current_notes_payable"
    assert m3.usage["family_resolved"] == 1

    resolvable = Spy(items=[LlmBatchItem(
        item_id="a", canonical_key="bs_non_current_liabilities__non_current_notes_payable",
        confidence=0.95)])
    m2 = _matcher(v2, resolvable)
    out2 = m2.match_batch([("a", "Senior notes and domestic bonds 優先票據及境內債券")],
                          statement="balance_sheet",
                          sections={"a": "CURRENT LIABILITIES 流動負債"})
    assert out2["a"].canonical_key == "bs_current_liabilities__current_notes_payable"
    assert m2.usage["family_resolved"] == 1


def test_a_reroute_target_goes_through_the_same_gate(v2):
    """Re-routing may correct which variant of a fact was chosen; it may not smuggle a concept past
    the gate's other arms. "Interest received on financing balances" names a member of the cash-flow
    activity vocabulary that neither leaf of the interest_received family is in, so the
    exclusive-vocabulary arm refuses the destination and the refusal stands."""
    operating = "cf_cash_flow_from_operating_activities__interest_received"
    spy = Spy(items=[LlmBatchItem(item_id="a", canonical_key=operating, confidence=0.9)])
    m = _matcher(v2, spy)
    out = m.match_batch([("a", "Interest received on financing balances")], statement="cash_flow",
                        sections={"a": "CASH FLOWS FROM INVESTING ACTIVITIES 投資活動"})

    assert out["a"].canonical_key != "cf_cash_flow_from_investing_activities__interest_received"
    assert m.usage["family_resolved"] == 0 and m.usage["batch_refused"] == 1

    # The same family, same banner, a caption that names nothing: re-routed.
    spy2 = Spy(items=[LlmBatchItem(item_id="a", canonical_key=operating, confidence=0.9)])
    m2 = _matcher(v2, spy2)
    out2 = m2.match_batch([("a", "Interest received 已收利息")], statement="cash_flow",
                          sections={"a": "CASH FLOWS FROM INVESTING ACTIVITIES 投資活動"})
    assert out2["a"].canonical_key == "cf_cash_flow_from_investing_activities__interest_received"


def test_a_reroute_is_recorded_on_the_row_it_moved(v2):
    """A corrected figure that looks like an ordinary hit is not reviewable. The reviewer opening
    the comprehensive-income bottom line has to be able to see that the caption on the page said
    "loss for the year"."""
    from app.core.models import DocumentModel
    from app.core.models.line_item import LineItem
    from app.core.stage import PipelineContext
    from app.stages.map_ontology import MapOntologyStage

    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = v2                                     # type: ignore[attr-defined]
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = [LineItem(source_label="LOSS FOR THE YEAR",
                               section_hint="TOTAL COMPREHENSIVE")]
    MapOntologyStage().run(doc, ctx)

    li = doc.line_items[0]
    assert li.canonical_key == BOTTOM_LINE
    assert "section_reroute_from:pl_profit_for_the_year" in li.confidence.flags


# --- 4. extraction_mode: derive is COMPUTED, extract_or_derive may be printed -------------------
# Both used to behave exactly like `extract` — only `do_not_extract` suppressed anything — so the
# distinction the v2 file draws between "the framework computes this" and "the filing sometimes
# prints it" existed only in the JSON.

DERIVED = "pl_profit_before_exceptional_items_and_tax"


def test_a_derive_concept_is_computed_and_never_bound_to_a_printed_caption(v2):
    """It carries an alias ("Profit before exceptional items and tax") and the template invented the
    line — HKEX filings do not print it. Offered as a candidate, a row that fuzzes against it
    OVERWRITES the computation with whatever caption happened to score, at a confidence that looks
    like a real hit. It stays EXTRACTABLE, because that is what `derive` means: the arithmetic in its
    own `derivation` produces it."""
    m = _matcher(v2)

    assert m._computed_only == {DERIVED}
    assert DERIVED in m._extractable_keys()          # extractable — by computation
    assert DERIVED not in m._mappable_keys()         # …and not by a caption
    assert not [k for keys in m._alias_index.values() for k in keys if k == DERIVED]
    assert DERIVED not in m._alias_by_key
    assert not m._concept_payload([DERIVED])
    assert m.match("Profit before exceptional items and tax",
                   statement="profit_and_loss").canonical_key != DERIVED


def test_extract_or_derive_is_still_bound_to_the_caption_that_prints_it(v2):
    """The neighbouring value, and the reason `derive` cannot simply be read as "do not extract": 20
    concepts are subtotals a filing may print on the face or leave to arithmetic. Refusing those would
    sweep a printed subtotal into the section residual, the more expensive of the two mistakes.

    20, not 22: the reviewer retired total cost of sales and total operating expenses, so the P&L now
    reads cost of sales, GROSS PROFIT, operating expenses, total operating cost. Both retired lines
    were `extract_or_derive`, and a filing that does print one of them now reaches the expenses
    residual instead — which is why ``pl_expenses__others`` carries the sweep for that namespace."""
    m = _matcher(v2)
    printed = [c.canonical_key for c in v2.mappings if c.extraction_mode == "extract_or_derive"]

    assert len(printed) == 20
    assert set(printed) <= set(m._mappable_keys())
    assert m.match("Total income", statement="profit_and_loss",
                   section="REVENUE").canonical_key == "pl_income__total_income"


def test_editing_extraction_mode_to_derive_takes_the_concept_out_of_matching():
    """The A/B on the shipped file: change one concept's `extraction_mode` and the caption that used
    to map to it stops mapping to it."""
    edited = json.loads(json.dumps(V2))
    for c in edited["mappings"]:
        if c["canonical_key"] == "pl_income__total_income":
            c["extraction_mode"] = "derive"
    m = _matcher(load_ontology(edited, resolve=True))

    assert m.match("Total income", statement="profit_and_loss",
                   section="REVENUE").canonical_key != "pl_income__total_income"
    assert "pl_income__total_income" in m._extractable_keys()


def test_an_exact_canonical_label_beats_a_higher_priority_borrowed_alias(v2):
    owner = next(m for m in v2.mappings
                 if m.statement.value == "profit_and_loss"
                 and m.extraction_mode == "extract"
                 and m.alias_matching == "enabled")
    wrong = next(m for m in v2.mappings
                 if m.statement == owner.statement
                 and m.canonical_key != owner.canonical_key
                 and m.extraction_mode == "extract"
                 and m.alias_matching == "enabled")
    edited = v2.model_copy(deep=True)
    edited_owner = next(m for m in edited.mappings if m.canonical_key == owner.canonical_key)
    edited_wrong = next(m for m in edited.mappings if m.canonical_key == wrong.canonical_key)
    edited_wrong.aliases.append(edited_owner.label)
    edited_wrong.match_priority = (edited_owner.match_priority or 0) + 100

    result = _matcher(edited).match(edited_owner.label, statement="profit_and_loss")

    assert result.canonical_key == edited_owner.canonical_key


# --- 5. the per-concept rulebook prose the decider is given --------------------------------------

def test_the_criteria_the_rulebook_wrote_for_the_decision_reach_the_decider(v2):
    """`section_disambiguation`, `derivation`, `is_gross_parent`/`children_if_decomposed` and
    `equivalence` were authored for a reader and read by nobody. They are prose (or a graph over
    keys), so the reader that can act on them is the semantic tier — which means the payload.

    THE COUNTS MOVED WITH THE EXTRACT-ONLY OFFER RULE, and one of them moved to zero. Only
    `extraction_mode: extract` concepts are put in front of the model now, so authored prose on a
    derivable concept no longer reaches it: `section_disambiguation` went 18 -> 16, and `derivation`
    — which is authored ONLY on concepts the framework can compute, i.e. exactly the withheld ones —
    went to 0. That is consistent rather than a loss (there is no point telling the model how a line
    is computed when the model is not asked about that line), but it does mean the `derivation`
    field is now dead weight in the payload, and it is asserted here so the day it stops being
    empty someone has to say why.
    """
    m = _matcher(v2)
    entries = {e["canonical_key"]: e
               for e in m._concept_payload([c.canonical_key for c in v2.mappings])}

    # 16 concepts carry the sentence that separates two look-alike captions.
    assert sum(1 for e in entries.values() if "section_disambiguation" in e) == 16
    assert "printed section only" in (
        entries["bs_current_liabilities__current_lease_liabilities"]["section_disambiguation"])
    # THE DERIVATION PROSE NO LONGER REACHES ANY DECIDER, because every concept carrying it is
    # withheld. Both halves are asserted: the payload is empty of it, AND the concept it used to be
    # probed on is absent for the declared reason rather than by accident.
    assert not [k for k, e in entries.items() if "derivation" in e]
    assert "pl_other_comprehensive_income_for_the_year" not in entries
    assert m._by_key["pl_other_comprehensive_income_for_the_year"].extraction_mode \
        == "extract_or_derive"
    # Containment, so the model does not propose the parent for a filing that prints the children.
    reserves = entries["bs_equity__reserves"]
    assert reserves["is_gross_parent"] is True
    assert "bs_equity__share_premium" in reserves["children_if_decomposed"]
    # One fact under two captions, so the twin is not read as a rival answer. Probed from the
    # `extract` side of the pair: `bs_net_assets` is `extract_or_derive` and so is withheld, which
    # leaves exactly one concept in the payload carrying `same_fact_as`.
    twin = entries["bs_equity__total_equity"]["same_fact_as"]
    assert twin["canonical_key"] == "bs_net_assets"
    assert "route to review" in twin["rule"]
    assert [k for k, e in entries.items() if "same_fact_as" in e] == ["bs_equity__total_equity"]


def test_the_sections_residual_expectations_are_offered_without_naming_the_bucket(v2):
    """`expected_components` lists the captions a section is EXPECTED to have no concept for. The
    buckets are locked out of every candidate list, which left the model with no licence to answer
    "none of these" — and a model offered concepts and a caption picks the nearest one, putting the
    figure on a specific wrong line while the section still ties. So the expectations are handed over
    and the bucket's own key is not."""
    spy = Spy(items=[])
    m = _matcher(v2, spy)
    m.match_batch([("a", "Bank overdrafts")], statement="balance_sheet",
                  sections={"a": "CURRENT LIABILITIES 流動負債"})

    payload = spy.payloads[0]
    expectations = payload["residual_expectations"]
    assert [e["section"] for e in expectations] == ["current_liabilities"]
    assert "Bank overdrafts" in expectations[0]["captions_with_no_dedicated_concept"]
    # The bucket itself is still unnameable: the expectations carry no canonical_key, and no
    # candidate the model may choose from is a residual.
    assert set(expectations[0]) == {"section", "captions_with_no_dedicated_concept"}
    assert not [c for c in payload["candidates"] if c["canonical_key"].endswith("__others")]
    assert "empty canonical_key" in m._batch_system


def test_editing_expected_components_changes_what_the_decider_is_told(v2):
    edited = json.loads(json.dumps(V2))
    for c in edited["mappings"]:
        if c["canonical_key"] == "bs_current_liabilities__others":
            c["expected_components"] = ["MARKER: a caption with no concept"]
    spy = Spy(items=[])
    _matcher(load_ontology(edited, resolve=True), spy).match_batch(
        [("a", "Anything")], statement="balance_sheet",
        sections={"a": "CURRENT LIABILITIES 流動負債"})

    assert spy.payloads[0]["residual_expectations"][0][
        "captions_with_no_dedicated_concept"] == ["MARKER: a caption with no concept"]


# --- 5b/6. containment: a gross parent is not filed alongside its mapped children ---------------

EQUITY = "CAPITAL AND RESERVES 資本及儲備"


def _equity_doc(*captions: str):
    """A balance-sheet page printing the given equity captions, one figure each."""
    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import Basis
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue, LineItem

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="balance_sheet")]
    for ordinal, caption in enumerate(captions):
        li = LineItem(source_label=caption, ordinal=ordinal, section_hint=EQUITY)
        li.set_value(ExtractedValue(
            value=Decimal(100 + ordinal), value_raw=Decimal(100 + ordinal),
            basis=Basis.CONSOLIDATED, period_label="current",
            provenance=Provenance(page_index=0)))
        doc.line_items.append(li)
    return doc


def _run_stage(doc, ontology):
    from app.core.stage import PipelineContext
    from app.stages.map_ontology import MapOntologyStage

    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = ontology                              # type: ignore[attr-defined]
    ctx.settings.llm.provider = "stub"                   # deterministic, no provider call
    MapOntologyStage().run(doc, ctx)
    return {li.source_label: li for li in doc.line_items}


def test_a_gross_parent_is_not_filed_alongside_the_children_it_contains(v2):
    """"If any component is printed, contain the parent and mark it as such.

    The aggregate remains evidence-only so it cannot be counted alongside its child.
    """
    rows = _run_stage(_equity_doc("Reserves", "Share premium"), v2)

    assert rows["Share premium"].canonical_key == "bs_equity__share_premium"
    assert rows["Reserves"].canonical_key is None
    flags = rows["Reserves"].confidence.flags
    assert "alloc:parent_gross_evidence_only" in flags
    assert "contains_mapped_children:bs_equity__share_premium" in flags
    assert "unfiled_aggregate:bs_equity__reserves" in flags
    # Marked a subtotal, which it is — and which is what keeps the residual sweep from adding it back
    # into the section under the name "Others", strictly worse than the double count.
    assert rows["Reserves"].role is LineRole.SUBTOTAL
    assert "alloc:child_component" in rows["Share premium"].confidence.flags


def test_the_aggregate_survives_when_the_face_prints_only_it(v2):
    """"Populate the aggregate only when the face prints a single undifferentiated 'Reserves' line."
    The guard must not cost the reading it was written to protect."""
    rows = _run_stage(_equity_doc("Reserves"), v2)

    assert rows["Reserves"].canonical_key == "bs_equity__reserves"
    assert rows["Reserves"].role is LineRole.LINE


def test_the_global_mutually_exclusive_group_is_read_on_its_own():
    """`global_rules.mutually_exclusive_groups` is the declaration, not a duplicate of the concept
    flags: strip `is_gross_parent` from the concept and the containment must still be enforced, or the
    global block is the inert half."""
    edited = json.loads(json.dumps(V2))
    for c in edited["mappings"]:
        if c["canonical_key"] == "bs_equity__reserves":
            c.pop("is_gross_parent", None)
            c.pop("children_if_decomposed", None)
    ont = load_ontology(edited, resolve=True)
    assert not ont.mappings[[c.canonical_key for c in ont.mappings].index(
        "bs_equity__reserves")].is_gross_parent

    rows = _run_stage(_equity_doc("Reserves", "Share premium"), ont)
    assert rows["Reserves"].canonical_key is None
    assert "unfiled_aggregate:bs_equity__reserves" in rows["Reserves"].confidence.flags


def test_the_concept_flags_are_read_on_their_own_too():
    """…and the other way round: delete the global group and `is_gross_parent` +
    `children_if_decomposed` must still keep the parent and its children apart."""
    edited = json.loads(json.dumps(V2))
    edited["global_rules"]["mutually_exclusive_groups"] = []
    ont = load_ontology(edited, resolve=True)

    rows = _run_stage(_equity_doc("Reserves", "Share premium"), ont)
    assert rows["Reserves"].canonical_key is None
    assert "contains_mapped_children:bs_equity__share_premium" in (
        rows["Reserves"].confidence.flags)
    assert "unfiled_aggregate:bs_equity__reserves" in rows["Reserves"].confidence.flags


def test_a_rulebook_declaring_no_containment_leaves_both_rows_filed():
    """The proof that the flags are what did it, not something else in the stage."""
    edited = json.loads(json.dumps(V2))
    edited["global_rules"]["mutually_exclusive_groups"] = []
    for c in edited["mappings"]:
        c.pop("is_gross_parent", None)
        c.pop("children_if_decomposed", None)
    rows = _run_stage(_equity_doc("Reserves", "Share premium"), load_ontology(edited, resolve=True))

    assert rows["Reserves"].canonical_key == "bs_equity__reserves"


# --- 5c. equivalence: two captions, one fact, and never a silent disagreement -------------------

def test_two_captions_declared_one_fact_may_not_disagree_in_silence(v2):
    """`equivalence`: "One economic fact under two captions… If both are printed and differ, route to
    review — do not average or pick." Each row is individually plausible and each subtotal it feeds
    still balances, so nothing else in the pipeline can see it."""
    doc = _equity_doc("Net assets", "Total equity")
    # One fact, two figures — and well beyond `recon_abs_tolerance`, which a rounding difference is
    # not: 100 against 150.
    for ev in doc.line_items[1].values.values():
        ev.value = ev.value_raw = Decimal(150)
    rows = _run_stage(doc, v2)

    assert rows["Net assets"].canonical_key == "bs_net_assets"
    assert rows["Total equity"].canonical_key == "bs_equity__total_equity"
    assert "equivalence_conflict:bs_equity__total_equity" in rows["Net assets"].confidence.flags
    assert "equivalence_conflict:bs_net_assets" in rows["Total equity"].confidence.flags
    assert "low_mapping_confidence" in rows["Net assets"].confidence.flags


def test_the_same_figure_under_both_captions_is_not_a_conflict(v2):
    """The ordinary case — and rounding is not a disagreement, so the comparison uses the
    reconciliation tolerance rather than exact equality."""
    doc = _equity_doc("Net assets", "Total equity")
    for li in doc.line_items:
        for ev in li.values.values():
            ev.value = ev.value_raw = Decimal(100)
    rows = _run_stage(doc, v2)

    assert not [f for f in rows["Net assets"].confidence.flags if f.startswith("equivalence")]


def test_deleting_the_equivalence_declaration_silences_the_finding():
    """Which is the point: the finding exists because the rulebook declares the two captions to be one
    fact. Nothing else about the two rows says so."""
    edited = json.loads(json.dumps(V2))
    for c in edited["mappings"]:
        c.pop("equivalence", None)
    rows = _run_stage(_equity_doc("Net assets", "Total equity"), load_ontology(edited, resolve=True))

    assert not [f for f in rows["Net assets"].confidence.flags if f.startswith("equivalence")]


def test_a_rulebook_declaring_none_of_this_is_unchanged():
    """Every field here is optional and every default is the previous behaviour: a concept with no
    ``match_priority`` and the default ``alias_matching: "enabled"`` locks nothing, and every
    ordering ties back to the order the file declares.

    Built here rather than loaded. This used to read the shipped thin rulebook, which declared none of
    these fields; one rulebook ships now and it declares all of them. What the test is actually about
    is the DEFAULTS an uploaded schema-1 rulebook still gets — a property to construct, not a file to
    point at.
    """
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key="bs_current_liabilities__others", label="Others",
                            aliases=["Others", "其他"]),
            OntologyMapping(canonical_key="bs_current_liabilities__current_trade_payables",
                            label="Trade payables", aliases=["Trade payables"]),
        ],
    )
    m = _matcher(ont)

    assert m._locked == set()
    assert {m._priority_of(c.canonical_key) for c in ont.mappings} == {0}
    keys = [c.canonical_key for c in ont.mappings]
    assert m._by_priority(keys) == keys
    # "Others" as an ordinary alias still matches, because nothing disabled alias matching.
    assert m.match("Others", statement="balance_sheet",
                   section="CURRENT LIABILITIES 流動負債").canonical_key == (
        "bs_current_liabilities__others")
