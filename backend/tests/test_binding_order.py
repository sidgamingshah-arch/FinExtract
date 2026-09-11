"""``binding.order`` — the rulebook's declared precedence, as the engine actually runs it.

The v2 rulebook authors the binding order as eight numbered steps and the engine used to implement
about half of them. The load-bearing gap was step 3: "Restrict the candidate concept set to concepts
whose section_scope contains the resolved section." The full statement was offered to every tier and
to the model, and a cross-section answer was refused AFTER it came back — which spends the call,
drops the row to the weaker per-line path, and grades the model against a constraint it was never
given a candidate list under.

Covered here, one section per step:

* step 3 — the candidate set is narrowed BEFORE any matching or any LLM call
* step 4 — the rule tier runs in descending ``match_priority``, ``exclude_hints`` a hard veto
* step 5 — the semantic tier only ever sees the restricted set
* step 6 — a tie between mutually-``confusable_with`` concepts is resolved by
  ``section_disambiguation`` or emitted for review, never picked by declaration order

plus the batching the order presumes: one call per (statement, basis, period), chunked with a
response budget measured from the response envelope.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings, get_settings
from app.core.models.enums import MappingMethod
from app.schemas.loader import load_ontology
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.mapping import OntologyMatcher, section_of_key

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
V2_JSON = (TEMPLATES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8")


def _v2():
    return json.loads(V2_JSON)


@pytest.fixture(scope="module")
def v2():
    return load_ontology(json.loads(V2_JSON), resolve=True)


def _matcher(ontology, provider=None, locale="zh") -> OntologyMatcher:
    return OntologyMatcher(ontology, locale=locale, settings=get_settings(),
                           llm_provider=provider)


def test_the_deterministic_tiers_score_only_the_restricted_set():
    """Step 3 says "before any matching". Filtering each tier's OUTPUT reached the same winner but
    not the same shortlist: the capped top-8 handed to the semantic tier was drawn from a ranking
    that out-of-section concepts had already taken places in."""
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key="bs_current_assets__inventories", label="Inventories",
                            aliases=["Inventories"]),
            OntologyMapping(canonical_key="bs_current_liabilities__inventory_provision",
                            label="Inventory provision", aliases=["Inventories provision"]),
        ],
    )
    # A hint both concepts would fire on, so the restriction is the only thing that can separate
    # them — and the rule tier is handed the restricted set, not asked to filter afterwards.
    for mp in ont.mappings:
        mp.keyword_hints = ["inventor"]
    m = _matcher(ont)
    assert m._rule("inventories", {"bs_current_assets__inventories"}).canonical_key == (
        "bs_current_assets__inventories")
    # …and via `match`, where the restriction is computed from the banner.
    res = m.match("Inventories provision", statement="balance_sheet", section="CURRENT ASSETS")
    assert [c.canonical_key for c in res.candidates] == ["bs_current_assets__inventories"]


# --- step 4: the rule tier, in descending match_priority ----------------------------------------

def test_the_rule_tier_picks_the_highest_priority_hit_not_the_first_declared():
    """"Rule tier: … in descending match_priority." Two concepts whose hints both fire used to
    resolve to whichever the file declared first, so an editor adding a broad regex_hint near the top
    silently pre-empted every specific concept below it — and the only way to find out was to read
    the JSON in order."""
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            # Declared first, LOWER priority: declaration order and priority disagree.
            OntologyMapping(canonical_key="pl_expenses__other_operating_expenses",
                            label="Other operating expenses", match_priority=40,
                            regex_hints=[r"expenses"]),
            OntologyMapping(canonical_key="pl_expenses__staff_costs", label="Staff costs",
                            match_priority=70, regex_hints=[r"staff\s+expenses"]),
        ],
    )
    m = _matcher(ont)
    hit = m._rule("staff expenses")

    assert hit is not None and hit.canonical_key == "pl_expenses__staff_costs"


def test_an_exclude_hint_is_a_veto_and_not_a_score_penalty():
    """"Any exclude_hints hit is a hard veto, not a score penalty." So a vetoed concept loses to a
    lower-priority one that hits — it does not merely rank below it and win when nothing else does."""
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key="pl_non_operating_expenses__finance_costs",
                            label="Finance costs", match_priority=90, regex_hints=[r"finance"],
                            exclude_hints=[r"income"], aliases=["Finance costs"]),
            OntologyMapping(canonical_key="pl_income__finance_income", label="Finance income",
                            match_priority=20, regex_hints=[r"finance\s+income"]),
        ],
    )
    m = _matcher(ont)

    assert m._rule("Finance income").canonical_key == "pl_income__finance_income"
    # And across every tier, not only inside the rule tier: the alias is an exact hit and is still
    # refused, because the field is named exclude and the editor is promised it means never.
    assert m.match("Finance costs and income").canonical_key != (
        "pl_non_operating_expenses__finance_costs")


# `test_editing_section_disambiguation_changes_what_the_decider_is_told` STOOD BELOW, and moved.
#
# It edited a concept's `section_disambiguation` and read the sentence back out of the candidate
# payload a per-caption call was handed — the field's only reader, because prose needs a reader
# that reads and the semantic tier was the only one. That tier is gone: a confusable tie is now
# REPORTED rather than resolved (both concepts emitted, the row routed to review), which the tests
# in this section cover.
#
# The prose still reaches a reader, from the LINE ITEM rather than from the concept —
# `LineItemDef.section_disambiguation` travels into a line-item request as `how_to_tell_it_apart`
# (`services.line_item_llm.line_item_payload`) — so the round trip is asserted there:
# `tests/test_line_item_requests_run.py::test_the_lines_own_disambiguation_prose_reaches_the_request`.


# --- step 6: a confusable tie is never broken by declaration order ------------------------------

BORROWINGS = ("bs_non_current_liabilities__non_current_borrowings",
              "bs_current_liabilities__current_borrowings")


def test_an_alias_two_confusable_concepts_claim_equally_goes_to_review(v2):
    """The motivating case. "Interest-bearing bank and other borrowings" is claimed byte-for-byte by
    the current and non-current borrowings concepts; both sit at match_priority 60 and each names the
    other in `confusable_with`. With no banner the answer was the higher priority — a tie, so
    `max()` returned the first declared, i.e. non-current, at confidence 1.0. 38 of this file's 83
    shared aliases are claimed by such a pair, so for those the answer was an editor's row order.
    """
    m = _matcher(v2)
    got = m.match("Interest-bearing bank and other borrowings", statement="balance_sheet")

    assert got.canonical_key is None and got.needs_review
    assert {c.canonical_key for c in got.candidates} == set(BORROWINGS)
    assert m.usage["confusable_ties"] == 1


def test_the_banner_still_settles_the_pair_outright(v2):
    """Step 6 is the LAST resort, reached only when the section did not separate them. The feature
    this must not have broken is the one the whole gate exists for."""
    m = _matcher(v2)
    for banner, expect in (("CURRENT LIABILITIES 流動負債", BORROWINGS[1]),
                           ("NON-CURRENT LIABILITIES 非流動負債", BORROWINGS[0])):
        got = m.match("Interest-bearing bank and other borrowings", statement="balance_sheet",
                      section=banner)
        assert got.canonical_key == expect and got.method is MappingMethod.EXACT
    assert m.usage["confusable_ties"] == 0


def test_a_tie_the_evidence_only_scores_equally_is_also_emitted_for_review(v2):
    """Not just the alias tier: two concepts whose hints both fire and that
    name each other are the same tie one tier down, where the fall-back was declared priority and
    then dict order."""
    m = _matcher(v2)
    got = m.match("Properties under developments 發展中物業", statement="balance_sheet")

    assert got.canonical_key is None and got.needs_review
    assert {c.canonical_key for c in got.candidates} == {
        "bs_non_current_assets__properties_under_development",
        "bs_current_assets__properties_under_development"}


def test_a_one_way_confusable_edge_is_not_a_tie():
    """`confusable_with` is a directed graph and its one-way edges are mostly warnings about a
    bigger concept. Read as a tie set they would connect into a 47-concept component in the shipped
    file, and every equal score inside it would stop being answered."""
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        binding={"order": ["6. tie between concepts listed in each other's confusable_with"]},
        mappings=[
            OntologyMapping(canonical_key="bs_current_assets__a", label="A", aliases=["Widget"],
                            confusable_with=["bs_current_assets__b"], match_priority=50),
            OntologyMapping(canonical_key="bs_current_assets__b", label="B", aliases=["Widget"],
                            match_priority=50),
        ],
    )
    m = _matcher(ont)
    assert m._confusable_tie(["bs_current_assets__a", "bs_current_assets__b"]) == []
    assert m.match("Widget").canonical_key in {"bs_current_assets__a", "bs_current_assets__b"}


def test_a_rulebook_that_declares_no_binding_order_is_unchanged():
    """Step 6 is an implementation OF ``binding.order``, so it is enabled by its presence. A rulebook
    declaring no binding block has no ``section_disambiguation`` for a tie to be resolved by either,
    and keeps the answer it was authored to give.

    Built here rather than loaded: this used to read the shipped thin rulebook, which declared no
    binding block. One rulebook ships now and it declares one, so "declares none of this" is a
    property to construct, not a file to point at — which is the honest form of the test anyway,
    since it was never about that file.
    """
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key="pl_profit_attributable_to__non_controlling_interests",
                            label="Non-controlling interests",
                            aliases=["Non-controlling interests", "非控股權益"]),
            OntologyMapping(
                canonical_key="pl_total_comprehensive_income_attributable_to__"
                              "non_controlling_interests",
                label="Non-controlling interests",
                aliases=["Non-controlling interests", "非控股權益"]),
        ],
    )
    m = _matcher(ont)

    assert m._binding_order == []
    got = m.match("Non-controlling interests 非控股權益", statement="profit_and_loss")
    assert got.canonical_key == "pl_profit_attributable_to__non_controlling_interests"
    assert m.usage["confusable_ties"] == 0


# --- the batching the order presumes ------------------------------------------------------------

def _doc(pages: list[tuple[int, str | None]], rows: list[tuple[int, str, list[tuple[str, str]]]]):
    """A document with the given (page_index, statement) pages and (page, caption, columns) rows."""
    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import Basis
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue, LineItem

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=i, statement=st) for i, st in pages]
    items = []
    for ordinal, (page, caption, columns) in enumerate(rows):
        li = LineItem(source_label=caption, ordinal=ordinal)
        for basis, period in columns:
            li.set_value(ExtractedValue(
                value=Decimal(1), value_raw=Decimal(1), basis=Basis(basis),
                period_label=period, provenance=Provenance(page_index=page)))
        items.append(li)
    doc.line_items = items
    return doc


CURRENT = [("consolidated", "current"), ("consolidated", "prior")]


# --- a computed concept is out of every tier, and its caption is refused not re-homed -----------

DERIVED = "pl_profit_before_exceptional_items_and_tax"
DERIVED_CAPTION = "Profit before exceptional items and tax"


def test_a_caption_naming_a_computed_concept_is_refused_not_filed_on_a_neighbour(v2):
    """`extraction_mode: derive` means the framework COMPUTES the concept, so it is out of every
    matching tier and out of every payload. Hiding it is not the same as refusing the row, and that
    was the defect: with its own concept unreachable, this caption's next-best evidence was
    `pl_profit_before_tax`, and the fuzzy tier filed it there at 0.61, accepted and unflagged. The two
    subtotals differ by exactly the exceptional items, so the figure landed on the wrong line of the
    P&L and the statement still tied.

    Reasoning for the neighbouring value: `extract_or_derive` means the subtotal is SOMETIMES printed
    and sometimes left to arithmetic, so a row printed with its caption IS the concept and must be
    matched. Only `derive` says the face does not print it, so only `derive` refuses a row."""
    m = _matcher(v2)
    assert DERIVED in m._computed_only
    for index in (m._alias_index, m._alias_by_key):
        assert DERIVED not in index
    assert DERIVED not in m._mappable_keys()

    got = m.match(DERIVED_CAPTION, statement="profit_and_loss")
    assert got.canonical_key is None and got.needs_review
    assert got.computed_claim == DERIVED
    assert m.usage["computed_refused"] == 1

    # The neighbour is still reachable by its own caption — the refusal is of one caption, not of a
    # concept, and `extract_or_derive` concepts are untouched.
    assert m.match("Profit before tax 除税前溢利",
                   statement="profit_and_loss").canonical_key == "pl_profit_before_tax"
    assert [c.canonical_key for c in v2.mappings
            if c.extraction_mode == "extract_or_derive" and c.canonical_key not in m._mappable_keys()
            ] == []


def test_the_refusal_does_not_take_a_row_another_concept_matches_better():
    """The claim only stands when the computed concept explains the caption at least as well as the
    best matchable one. A `derive` concept whose wording overlaps a real one would otherwise start
    refusing rows that are somebody else's, which is a worse trade than the one being fixed: the two
    concepts below share three words, so almost every expense caption gives the computed one some
    evidence."""
    D = "pl_expenses__total_operating_expenses_before_depreciation"
    X = "pl_expenses__total_operating_expenses"
    ont = OntologyDefinition(
        ontology_key="k", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key=D, label="Total opex before depreciation",
                            extraction_mode="derive",
                            aliases=["Total operating expenses before depreciation"]),
            OntologyMapping(canonical_key=X, label="Total operating expenses",
                            aliases=["Total operating expenses"]),
        ],
    )
    m = _matcher(ont, locale="en")

    # The computed concept has a real claim on this caption and still loses: the matchable concept
    # explains it exactly, so the row is that concept's. (Exactly, because that is the strength a
    # claim now has to beat — no tier scores resemblance any more, so the rival's evidence is an
    # alias hit or a rule hit.)
    kept = m.match("Total operating expenses", statement="profit_and_loss")
    assert kept.canonical_key == X and kept.computed_claim is None
    assert m.usage["computed_refused"] == 0

    # The other side of the same comparison, so the threshold is shown to be a threshold.
    refused = m.match("Total operating expenses before depreciations", statement="profit_and_loss")
    assert refused.canonical_key is None and refused.computed_claim == D
    assert m.usage["computed_refused"] == 1


def test_a_computed_row_is_marked_a_subtotal_so_the_sweep_cannot_re_add_it(v2):
    """A refusal alone is not enough. An unclaimed face row with a value is swept into its section's
    "Others" (stages.residual), which would put a subtotal OF the section back INTO the section under
    a different name — strictly worse than the mis-mapping. A row that IS a computed subtotal is
    marked as one, which the sweep's own eligibility rules already exclude."""
    from app.core.stage import PipelineContext
    from app.core.models.enums import LineRole
    from app.stages.map_ontology import MapOntologyStage

    doc = _doc([(0, "profit_and_loss")], [(0, DERIVED_CAPTION, CURRENT)])
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = v2                                      # type: ignore[attr-defined]
    ctx.settings.llm.provider = "stub"                     # deterministic, no provider call
    MapOntologyStage().run(doc, ctx)

    row = doc.line_items[0]
    assert row.canonical_key is None
    assert row.role is LineRole.SUBTOTAL
    assert f"computed_concept_printed:{DERIVED}" in row.confidence.flags
    assert "low_mapping_confidence" in row.confidence.flags


# --- global_rules on the deterministic path ------------------------------------------------------
# Four `global_rules` blocks (`parent_child_allocation`, `duplicate_fact_rule`, `totals_policy`,
# `no_fabricated_split`) and `worked_examples` are read ONLY by `OntologyMatcher._build_system`, so on
# a run with no provider configured they did nothing at all. Two sentences of `parent_child_allocation`
# state a rule the deterministic path can test, and now do.

def test_a_containment_the_arithmetic_does_not_support_is_routed_to_review(v2):
    """"Subtract only on explicit inclusion wording, hierarchy, reconciliation or arithmetic support"
    and "If containment is uncertain, retain the parent as evidence and route to review." Unfiling the
    aggregate is done on the strength of the DECLARATION alone, and the arithmetic is the one of those
    four supports this stage can test. Reserves 900 with only Share premium 100 printed means three
    declared components were not printed (or not extracted): retaining the parent as evidence and
    saying nothing removes 800 from the statement, and every remaining check still ties."""
    from app.core.stage import PipelineContext
    from app.stages.map_ontology import MapOntologyStage

    def _equity(reserves: int, premium: int):
        doc = _doc([(0, "balance_sheet")],
                   [(0, "Reserves 儲備", CURRENT), (0, "Share premium 股份溢價", CURRENT)])
        for li, amount in zip(doc.line_items, (reserves, premium)):
            li.section_hint = "EQUITY 權益"
            for ev in li.values.values():
                ev.value = ev.value_raw = Decimal(amount)
        ctx = PipelineContext(raw_bytes=b"")
        ctx.ontology = v2                                  # type: ignore[attr-defined]
        ctx.settings.llm.provider = "stub"
        MapOntologyStage().run(doc, ctx)
        return doc.line_items[0]

    unexplained = _equity(900, 100)
    assert unexplained.canonical_key is None               # the containment still fires
    assert "containment_unexplained:bs_equity__reserves:2" in unexplained.confidence.flags
    assert "low_mapping_confidence" in unexplained.confidence.flags

    # …and where the components DO account for the aggregate the containment is confirmed on the page,
    # so the row is unfiled silently as before. A review flag on every containment is no review flag.
    confirmed = _equity(100, 100)
    assert confirmed.canonical_key is None
    assert not [f for f in confirmed.confidence.flags if f.startswith("containment_unexplained")]
    assert "low_mapping_confidence" not in confirmed.confidence.flags


def test_no_concept_declares_a_section_its_own_key_name_contradicts(v2):
    """A declared ``section_scope`` may say MORE than the key name; it may never say something else.

    This started as "the two agree on all 173 concepts", which held until the income statement gained
    an ``Other comprehensive income`` section: its concepts are keyed ``pl_oci__*``, a namespace
    ``section_of_key`` reads as no section at all, while ``inherits`` scopes them to
    ``pl_s8_other_comprehensive_income``. That is the declaration doing exactly what it is for —
    constraining a concept the key name leaves unconstrained — so requiring equality would have
    forced either a worse key or a weaker gate.

    A CONTRADICTION is still a defect, and is what this holds: a key namespaced under one section
    while the rulebook scopes it to a different one means the gate and the key disagree about which
    banner may claim the row, and whichever a reader consults tells them the wrong thing.
    """
    m = _matcher(v2)
    for c in v2.mappings:
        tok = section_of_key(c.canonical_key)
        declared = m._sections_of(c.canonical_key)
        if tok:
            assert declared == frozenset([tok]), c.canonical_key
        else:
            # No section in the key name: the declaration is free to name one, and free to name
            # none, but not to name two — a concept in two sections has no residual sweep at all.
            assert len(declared) <= 1, c.canonical_key
