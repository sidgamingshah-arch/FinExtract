"""Description-based, LLM-driven ontology mapping (services.mapping)."""
from __future__ import annotations

import json

from app.config import get_settings
from app.core.models.enums import MappingMethod
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.mapping import (
    LlmBatchDecision, LlmBatchItem, LlmMappingDecision, OntologyMatcher,
)


def _ontology() -> OntologyDefinition:
    return OntologyDefinition(
        ontology_key="t", target_template_key="t",
        mappings=[
            OntologyMapping(
                canonical_key="trade_receivables", label="Trade receivables",
                description="amounts owed by customers for goods or services sold on credit",
                aliases=["Trade receivables"],
            ),
            OntologyMapping(
                canonical_key="cash_and_equivalents", label="Cash and cash equivalents",
                description="cash on hand and bank balances readily available",
                aliases=["Cash and cash equivalents"],
            ),
        ],
    )


class _FakeLlm:
    """Simulates a description-based decision: pick the concept whose description shares
    the most meaning (word overlap here) with the caption."""

    id = "fake"

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0, max_tokens=2048):
        payload = json.loads(messages[0]["content"])
        caption_words = set(payload["caption"].lower().replace(",", " ").split())
        best_key, best_overlap = "", 0
        for c in payload["candidates"]:
            text = (c.get("definition") or c.get("description") or "").lower()
            overlap = len(caption_words & set(text.split()))
            if overlap > best_overlap:
                best_key, best_overlap = c["canonical_key"], overlap
        decision = LlmMappingDecision(canonical_key=best_key, confidence=0.92 if best_key else 0.0)
        return decision, {"model": "fake-llm", "input_tokens": 120, "output_tokens": 9}


def test_description_based_beats_fuzzy():
    """A caption that shares no alias words still maps correctly via its meaning."""
    s = get_settings()
    caption = "Amounts due from customers"  # not lexically close to "Trade receivables"

    # Deterministic (no LLM): fuzzy can't confidently reach trade_receivables → review.
    det = OntologyMatcher(_ontology(), settings=s).match(caption)
    assert det.method is not MappingMethod.LLM
    assert det.needs_review or det.canonical_key != "trade_receivables"

    # Description-based (LLM primary): resolves by meaning.
    m = OntologyMatcher(_ontology(), settings=s, llm_provider=_FakeLlm())
    assert m.llm_enabled is True
    res = m.match(caption)
    assert res.method is MappingMethod.LLM
    assert res.canonical_key == "trade_receivables"
    assert res.confidence >= 0.92  # LLM-driven; may be nudged up by corroboration
    assert m.usage["calls"] == 1 and m.usage["input_tokens"] == 120


def test_exact_alias_short_circuits_without_calling_llm():
    m = OntologyMatcher(_ontology(), settings=get_settings(), llm_provider=_FakeLlm())
    res = m.match("Trade receivables")  # exact normalized alias
    assert res.method is MappingMethod.EXACT
    assert m.usage["calls"] == 0  # no tokens spent on an identity match


def test_ensemble_combines_methods_and_corroborates():
    """The LLM is a driver, not the sole authority: deterministic agreement boosts
    confidence, and every candidate's criteria + the ontology policies reach the model."""
    seen = {}

    class _Recorder(_FakeLlm):
        def complete_structured(self, *, system, messages, response_schema, temperature=0.0, max_tokens=2048):
            seen["system"] = system
            seen["payload"] = json.loads(messages[0]["content"])
            return super().complete_structured(system=system, messages=messages,
                                               response_schema=response_schema)

    ont = _ontology()
    # A do-not-extract heading must never be offered as a candidate.
    ont.mappings.append(OntologyMapping(
        canonical_key="current_assets_heading", label="Current assets",
        description="section heading", extraction_mode="do_not_extract", value_scope="not_applicable"))
    # Global policies should be injected into the system prompt.
    ont.global_rules.no_fabricated_split = "Do not invent a split."
    ont.global_rules.others_policy = ["Others is never a balancing plug."]

    # A keyword hint on the concept the model is going to pick, so a DETERMINISTIC method has an
    # opinion to corroborate with. (There is no string-similarity tier to supply one: nothing maps
    # a row on resemblance, so corroboration comes from the rule tier.)
    next(x for x in ont.mappings if x.canonical_key == "cash_and_equivalents").keyword_hints = [
        "cash"]
    m = OntologyMatcher(ont, settings=get_settings(), llm_provider=_Recorder())
    res = m.match("Cash & cash equivalents")  # the rule tier agrees with the LLM's pick
    assert res.canonical_key == "cash_and_equivalents" and res.method is MappingMethod.LLM
    assert "llm" in res.agreement and "rule" in res.agreement           # corroborated
    assert res.confidence > 0.92                                        # agreement boosted it
    # Candidate payload carries criteria; the do_not_extract heading is absent.
    keys = {c["canonical_key"] for c in seen["payload"]["candidates"]}
    assert "current_assets_heading" not in keys
    assert any("value_scope" in c for c in seen["payload"]["candidates"])
    assert "Do not invent a split." in seen["system"]
    assert "balancing plug" in seen["system"]


def test_llm_abstains_falls_back_to_deterministic():
    """If the LLM returns no concept, mapping falls back to the deterministic ensemble."""
    class _Abstain(_FakeLlm):
        def complete_structured(self, **kw):
            return LlmMappingDecision(canonical_key="", confidence=0.0), {
                "model": "fake-llm", "input_tokens": 50, "output_tokens": 2}

    ont = _ontology()
    m = OntologyMatcher(ont, settings=get_settings(), llm_provider=_Abstain())
    res = m.match("Cash and cash equivalents")  # exact alias → resolves even after abstain path
    assert res.canonical_key == "cash_and_equivalents"
    # A caption only resemblance could have matched now falls through to review: the deterministic
    # fallback is the alias tier and the rule tier, and neither claims this wording.
    assert m.match("Cash & cash equivalents").canonical_key is None
    # Authoring a rule hint is how the fallback is extended — the tier the rulebook declares.
    next(x for x in ont.mappings if x.canonical_key == "cash_and_equivalents").keyword_hints = [
        "cash"]
    m2 = OntologyMatcher(ont, settings=get_settings(), llm_provider=_Abstain())
    assert m2.match("Cash & cash equivalents").canonical_key == "cash_and_equivalents"


def test_per_statement_batch_maps_all_lines_in_one_call():
    """per_statement mapping decides many captions in ONE grounded call; the LLM
    references item_ids/keys (never values), and unlisted items fall back per-line."""
    calls = {"n": 0}

    class _BatchLlm:
        id = "fake"

        def complete_structured(self, *, system, messages, response_schema, temperature=0.0, max_tokens=2048):
            calls["n"] += 1
            return LlmBatchDecision(mappings=[
                LlmBatchItem(item_id="a", canonical_key="trade_receivables", confidence=0.9),
                LlmBatchItem(item_id="b", canonical_key="cash_and_equivalents", confidence=0.95),
            ]), {"model": "fake-llm", "input_tokens": 300, "output_tokens": 40}

    m = OntologyMatcher(_ontology(), settings=get_settings(), llm_provider=_BatchLlm())
    res = m.match_batch([("a", "Amounts due from customers"),
                         ("b", "Cash at bank and in hand"),
                         ("c", "Trade receivables")])   # exact alias → per-line fallback
    assert calls["n"] == 1                               # ONE call for the whole statement
    assert res["a"].canonical_key == "trade_receivables"
    assert res["b"].canonical_key == "cash_and_equivalents"
    assert res["c"].canonical_key == "trade_receivables"
    assert m.usage["input_tokens"] == 300


# --- the batch path's own hazards -------------------------------------------------------------
# Everything below had zero coverage, which is how an item_id the model made up survived long
# enough to crash a whole extraction run.

def _prefixed_ontology() -> OntologyDefinition:
    """Keys carrying a STATEMENT namespace, so the statement gate can actually filter them —
    the unprefixed keys in `_ontology` are allowed on every statement by design."""
    return OntologyDefinition(
        ontology_key="t", target_template_key="t",
        mappings=[
            OntologyMapping(
                canonical_key="bs_current_assets__trade_receivables", label="Trade receivables",
                description="amounts owed by customers", aliases=["Trade receivables"],
            ),
        ],
    )


class _RecordingLlm:
    """Answers with whatever it is told to, and keeps the payload it was sent.

    Dispatches on `response_schema`: the batch path and the per-line fallback ask for different
    shapes from the SAME provider, and a fake that ignores that hands `_llm` a batch decision to
    read `.canonical_key` off.
    """

    id = "fake"

    def __init__(self, mappings):
        self._mappings = mappings
        self.payloads: list[dict] = []
        self.systems: list[str] = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        meta = {"model": "fake-llm", "input_tokens": 10, "output_tokens": 5}
        if response_schema is LlmBatchDecision:
            self.payloads.append(json.loads(messages[0]["content"]))
            self.systems.append(system)
            return LlmBatchDecision(mappings=list(self._mappings)), meta
        # Per-line fallback: abstain, so the deterministic tiers decide and the test is not
        # measuring this fake's opinion.
        return LlmMappingDecision(canonical_key="", confidence=0.0), meta


def test_batch_discards_an_item_id_it_never_asked_about():
    """An invented / mistyped item_id must not reach the caller: `by_id[iid]` in the stage is a
    KeyError, which killed the entire extraction run rather than one line."""
    llm = _RecordingLlm([
        LlmBatchItem(item_id="a", canonical_key="trade_receivables", confidence=0.9),
        LlmBatchItem(item_id="ghost", canonical_key="cash_and_equivalents", confidence=0.9),
    ])
    m = OntologyMatcher(_ontology(), settings=get_settings(), llm_provider=llm)
    res = m.match_batch([("a", "Amounts due from customers")])

    assert set(res) == {"a"}                     # only what was asked about comes back
    assert "ghost" not in res
    assert m.usage["batch_unknown_ids"] == 1     # and the discard is counted, not silent


def test_batch_sends_the_normalised_section_and_omits_an_unrecognised_banner():
    """The banner is what the answer is graded on, so the model is told it — normalised, because
    the gate compares normalised tokens. A banner that normalises to nothing is omitted rather
    than passed through as raw text, which would imply a constraint the gate does not apply."""
    llm = _RecordingLlm([LlmBatchItem(item_id="a", canonical_key="trade_receivables",
                                      confidence=0.9)])
    m = OntologyMatcher(_ontology(), settings=get_settings(), llm_provider=llm)
    m.match_batch(
        [("a", "Trade debtors"), ("b", "Something else")],
        sections={"a": "CURRENT ASSETS", "b": "EQUITY AND LIABILITIES"},
    )
    items = {i["item_id"]: i for i in llm.payloads[0]["source_items"]}
    assert items["a"]["section"] == "current_assets"   # normalised, not "CURRENT ASSETS"
    assert "section" not in items["b"]                # umbrella banner → no constraint claimed


def test_batch_prompt_keeps_printed_order_and_explains_sections():
    llm = _RecordingLlm([])
    m = OntologyMatcher(_ontology(), settings=get_settings(), llm_provider=llm)
    order = [("a", "First"), ("b", "Second"), ("c", "Third")]
    m.match_batch(order, sections={"a": "CURRENT ASSETS"})

    assert [i["item_id"] for i in llm.payloads[0]["source_items"]] == ["a", "b", "c"]
    # The base instruction says "a single raw line-item caption"; the batch path must not inherit
    # that framing, and must define the section it is now handing over.
    assert "several captions" in llm.systems[0].lower()
    assert "section" in llm.systems[0].lower()
    assert m._system != m._batch_system            # per-line prompt left alone


def test_batch_does_not_call_the_provider_with_an_empty_candidate_list():
    """A statement the ontology covers no concepts for is not a question worth asking. The
    shipped ontology has no `eq_` keys at all, so changes-in-equity used to spend a real provider
    call on an empty candidate list and then fall back per line anyway."""
    llm = _RecordingLlm([])
    m = OntologyMatcher(_prefixed_ontology(), settings=get_settings(), llm_provider=llm)
    res = m.match_batch([("a", "Trade receivables")], statement="cash_flow")

    assert llm.payloads == []                    # the batch never reached the provider
    assert m.usage["calls"] == 0
    # And the row is still answered — unmatched, because the statement gate refuses a bs_ concept
    # on a cash-flow page for the same reason the candidate list was empty. The point is that no
    # call was spent to arrive there.
    assert res["a"].canonical_key is None
    assert res["a"].method is MappingMethod.UNMATCHED


def test_batch_failure_is_recorded_not_swallowed():
    """A refused or truncated batch used to fall back per line in silence, so a run whose every
    batch failed still reported itself as LLM-mapped with no error to point at."""

    class _Boom:
        id = "fake"

        def complete_structured(self, **_kw):
            raise RuntimeError("truncated JSON")

    m = OntologyMatcher(_ontology(), settings=get_settings(), llm_provider=_Boom())
    res = m.match_batch([("a", "Trade receivables")])

    assert m.usage["failures"] == 1
    assert "truncated JSON" in m.usage["last_error"]
    assert res["a"].canonical_key == "trade_receivables"   # fell back, still answered


def test_batch_refuses_a_wrong_section_answer_even_though_the_model_was_told_the_section():
    """The post-answer gate is a BACKSTOP, not decoration — and the batch path is the one path
    whose candidate list is not section-filtered before being offered, so a wrong-section answer
    is reachable only here. Deleting the gate outright used to pass the whole suite."""
    ont = OntologyDefinition(
        ontology_key="t", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key="bs_current_assets__trade_receivables",
                            label="Trade receivables", description="owed by customers"),
            OntologyMapping(canonical_key="bs_current_liabilities__trade_payables",
                            label="Trade payables", description="owed to suppliers"),
        ],
    )
    # The model answers with a CURRENT LIABILITIES concept for a row printed under CURRENT ASSETS.
    llm = _RecordingLlm([LlmBatchItem(item_id="a",
                                      canonical_key="bs_current_liabilities__trade_payables",
                                      confidence=0.99)])
    m = OntologyMatcher(ont, settings=get_settings(), llm_provider=llm)
    res = m.match_batch([("a", "Amounts due from customers")],
                        statement="balance_sheet", sections={"a": "CURRENT ASSETS"})

    assert res["a"].canonical_key != "bs_current_liabilities__trade_payables"
    assert m.usage["batch_refused"] == 1      # refused, and the refusal is counted


def test_the_per_line_call_still_gets_the_per_line_prompt():
    """The batch framing is additive so it cannot rewrite the per-line prompt — asserted at the
    CALL SITE, because comparing the two attributes does not prove which one is sent."""
    seen: list[tuple[str, str]] = []

    class _Spy:
        id = "fake"

        def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                                max_tokens=2048):
            seen.append((response_schema.__name__, system))
            return LlmMappingDecision(canonical_key="", confidence=0.0), {"model": "fake-llm"}

    m = OntologyMatcher(_ontology(), settings=get_settings(), llm_provider=_Spy())
    m.match("Amounts due from customers", statement="balance_sheet")

    assert [s for s, _ in seen] == ["LlmMappingDecision"]      # per-line schema, per-line path
    assert seen[0][1] == m._system
    assert "SEVERAL captions" not in seen[0][1]                # batch addendum stayed out
