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
    OntologyMatcher, normalize_label,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
V2 = json.loads((TEMPLATES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def v2():
    return load_ontology(V2, resolve=True)


def _matcher(ontology, provider=None, locale="zh") -> OntologyMatcher:
    return OntologyMatcher(ontology, locale=locale, settings=get_settings(),
                           llm_provider=provider)


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


def test_the_balance_sheet_payload_omits_the_five_asset_and_liability_buckets(v2):
    """Measured: of 78 balance-sheet concepts, 73 are bindable by a printed caption, and the five
    that are not are the bs "Others" buckets (`alias_matching: disabled`). All five declare
    `extraction_mode: extract`, so it is the LOCK that puts them out of reach and not anything
    about how they are extracted — a residual's caption is the most attractive one in the
    ontology, and a row filed in a bucket lands in what is supposed to be the section's
    UNEXPLAINED remainder, so the reconciliation that would have reported the gap ties instead.

    IT USED TO BE 68 OF 78, and the other five were the statement's `extract_or_derive` subtotals.
    They were withheld from the CANDIDATE LIST a printed row was shown — a line the declared
    arithmetic can work out was not the model's to guess at — while staying fully matchable by the
    deterministic tiers, which is why the narrowing was applied where candidates were offered
    rather than in `_mappable_keys`. There is no candidate list: nothing asks a model which concept
    a printed row is. So the only boundary left here is the one that was never about offering, and
    the last two assertions pin both halves — the buckets are out, the derivables are in.
    """
    m = _matcher(v2)
    bs = [c.canonical_key for c in v2.mappings if c.canonical_key.startswith("bs_")]
    mappable = {k for k in m._mappable_keys() if k.startswith("bs_")}

    buckets = {"bs_non_current_assets__others", "bs_current_assets__others", "bs_equity__others",
               "bs_non_current_liabilities__others", "bs_current_liabilities__others"}
    derivable = {c.canonical_key for c in v2.mappings
                 if c.canonical_key.startswith("bs_") and c.extraction_mode != "extract"}

    assert len(bs) == 78
    assert buckets <= m._locked, "the buckets are out by the LOCK, not by any offer rule"
    assert all(m._by_key[k].extraction_mode == "extract" for k in buckets)
    assert len(derivable) == 5, sorted(derivable)
    # THE FIVE BUCKETS ARE THE WHOLE DIFFERENCE NOW. 78 balance-sheet concepts, 73 bindable — and
    # the five that are not are exactly the locked residuals.
    assert {k for k in bs if k not in mappable} == buckets
    # …and the five DERIVABLE subtotals are IN, which is the half that must not break. A printed
    # `extract_or_derive` subtotal has to be read off the page whatever the arithmetic could also
    # do, and it is a caption tier that reads it.
    assert derivable <= mappable, sorted(derivable - mappable)


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


# `test_the_model_is_never_offered_a_bucket` STOOD HERE. It checked that the lock held in the
# PAYLOAD BUILDER as well as in the matching indexes, because a candidate list was assembled from
# the rule tier's keys rather than from `_mappable_keys` and a concept kept out of one route had to
# be kept out of the other. There is one route: no payload offers a concept to be chosen from, so
# `test_the_thirteen_residual_buckets_are_locked_out_of_every_matching_index` above is the whole
# check.


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

def test_the_colliding_pair_is_read_in_the_order_the_rulebook_declares(v2):
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
    m = _matcher(v2)
    # The concepts a caption printed under this banner may be bound to, in the reading order the
    # rulebook declares. Read off `_by_priority` over the scoped set, which is what `match` ranks
    # with — it used to be read off the candidate list a provider spy had been handed, and that
    # list is gone with the row request.
    section = m._section_of("CURRENT LIABILITIES 流動負債")
    scoped = [k for k in m._mappable_keys()
              if m._in_statement(k, "balance_sheet")
              and (not m._sections_of(k) or section in m._sections_of(k))]
    offered = m._by_priority(scoped)

    # THE DERIVABLE SUBTOTAL IS IN THE LIST NOW, and that is the change. It used to be withheld
    # from the OFFER — `extract_or_derive` means "printed on some filings, arithmetic on others",
    # and a model shown a candidate list would guess at it — while staying matchable by the tiers
    # that CAN bind it. Nothing is offered to be chosen from any more, so the only question left is
    # whether a printed caption can reach it, and it must be able to.
    derivable = "bs_total_assets_less_current_liabilities"
    assert m._by_key[derivable].extraction_mode == "extract_or_derive"
    assert derivable in m._mappable_keys()
    assert m._priority_of(derivable) > m._priority_of(
        "bs_current_liabilities__total_current_liabilities")

    # The pair the rulebook's own note is about: priority, not declaration order, decides which is
    # read first.
    specific = offered.index("bs_current_liabilities__current_portion_of_long_term_debt")   # 68
    generic = offered.index("bs_current_liabilities__current_borrowings")                   # 60
    assert specific < generic, list(zip(offered, [m._priority_of(k) for k in offered]))
    # …and the file declares them the wrong way round, which is the thing priority overrides.
    # Without this the assertion above would also pass on a list that was never sorted.
    decl = {mm.canonical_key: i for i, mm in enumerate(v2.mappings)}
    assert decl["bs_current_liabilities__current_portion_of_long_term_debt"] > decl[
        "bs_current_liabilities__current_borrowings"]
    # The whole list, not just one pair: descending declared priority.
    priorities = [m._priority_of(k) for k in offered]
    assert priorities == sorted(priorities, reverse=True), list(zip(offered, priorities))


def test_by_priority_orders_a_statements_concepts(v2):
    """`_by_priority` is the reading order the rulebook declares, and it is what settles a tie the
    evidence rates equally — see `match`'s ranking, where score comes first and priority second."""
    m = _matcher(v2)
    bs = [k for k in m._mappable_keys() if m._in_statement(k, "balance_sheet")]
    priorities = [m._priority_of(k) for k in m._by_priority(bs)]

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

    # AND ON THE REAL FILE, the same thing: the highest-priority concept on the statement does not
    # absorb a caption no alias and no rule claims. The second half of this test used to hand a
    # provider spy the same misspelling and assert that the MODEL's answer beat the order the list
    # was offered in; there is no list and no call, so what is asserted instead is the property
    # that mattered — a priority is not a score, and an unrecognised caption is a visible gap.
    top = max(m._mappable_keys(), key=_matcher(v2)._priority_of)
    got = _matcher(v2).match("Total current liabilites", statement="balance_sheet",
                             section="CURRENT LIABILITIES 流動負債")
    assert got.canonical_key != top
    assert got.canonical_key is None or got.needs_review


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
    assert DERIVED in m._unmatchable
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

def test_the_criteria_the_rulebook_wrote_are_read_by_the_passes_that_can_act_on_them(v2):
    """`section_disambiguation`, `derivation`, `is_gross_parent`/`children_if_decomposed` and
    `equivalence` were authored for a reader. This pins WHICH reader each one actually has, which
    changed when the row request went — and two of the four now have none here.

    IT USED TO BE ONE READER FOR ALL FOUR: `_concept_payload`, the candidate list a printed row was
    shown, because they are prose (or a graph over keys) and the only thing that could act on prose
    was the semantic tier. That tier is gone. What is left splits in two:

      * CONTAINMENT AND EQUIVALENCE ARE READ DETERMINISTICALLY, by whole-document passes over the
        mapped rows (`stages.map_ontology._enforce_containment` / `_check_equivalence`). A gross
        parent is still not filed alongside the children it contains, and two captions the rulebook
        declares to be one fact still may not disagree in silence. Those are graphs over keys, and
        arithmetic can act on them.
      * `section_disambiguation` AND `derivation` ARE PROSE, and prose needs a reader that reads.
        On the ONTOLOGY they now have none: a confusable tie is REPORTED rather than resolved (both
        concepts emitted, the row routed to review), so nothing consults the sentence that would
        have separated them. The prose is not lost — `LineItemDef.section_disambiguation` travels
        into a line-item request as `how_to_tell_it_apart`
        (`services.line_item_llm.line_item_payload`) — but it gets there from the LINE ITEM, not
        from a concept, which is why this file can no longer assert it.

    Asserted as counts as well as behaviour so that the day either of those two acquires a reader
    again, someone has to come here and say so.
    """
    m = _matcher(v2)
    by_key = {mm.canonical_key: mm for mm in v2.mappings}

    # The prose is still AUTHORED, on the same concepts as before — this is not a data loss.
    disambiguated = [k for k, mm in by_key.items() if getattr(mm, "section_disambiguation", "")]
    assert len(disambiguated) >= 16, len(disambiguated)
    assert any("printed section only" in (by_key[k].section_disambiguation or "")
               for k in disambiguated), "the shipped wording is no longer authored anywhere"

    # CONTAINMENT: read by the deterministic pass, and the declaration is intact.
    reserves = by_key["bs_equity__reserves"]
    assert reserves.is_gross_parent is True
    assert "bs_equity__share_premium" in reserves.children_if_decomposed

    # EQUIVALENCE: one fact under two captions, so the twin is not read as a rival answer. The
    # field is `equivalence` on the concept; `same_fact_as` was the name the retired PAYLOAD gave
    # it, which is exactly the kind of coupling this rewrite removes.
    twin = by_key["bs_equity__total_equity"].equivalence
    assert twin is not None and twin.with_ == "bs_net_assets"
    assert "route to review" in twin.rule

    # …and the tie the disambiguation prose was for is REPORTED, not resolved — which is the
    # behaviour that replaced the reader.
    tied = m._exact_tie(normalize_label("Total equity"), allowed=lambda _k: True)
    if tied:
        got = m.match("Total equity", statement="balance_sheet")
        assert got.needs_review, "a confusable tie must be emitted for review, never picked"

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
