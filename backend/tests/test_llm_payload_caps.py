"""The three request-payload caps must reach the requests they describe.

THE DEFECT THIS CLOSES, measured on two real filings. `extraction.llm_candidate_cap` (40) was
applied in `OntologyMatcher.match` — the PER-LINE path — and nowhere else. `_match_chunk`, the
batch path that decides essentially every statement row, called `_concept_payload` with no bound
at all, so every call offered the whole statement:

    balance_sheet   202 concepts   196,607 chars   ~49,151 tokens   in EVERY call

The provider refused it outright (413 `request_too_large` on the 367-page filing) or rate-limited
it on tokens-per-minute (429, "Requested 15931"). Every mapping call failed, and both runs
completed reporting `strategy: "deterministic"` with `llm_calls: 0`. Nothing in the output said
the model had never been asked — the figures simply came from the weaker path.

WHY IT SCALED THE WRONG WAY. Request size grew with the size of the filing's statements, so the
bigger the document the more certain it was to lose the LLM path entirely. That is the opposite of
the intended behaviour and it is why this is a bound and not a tuning preference.

THE OTHER TWO CAPS WERE DEAD. `llm_example_aliases_cap` and `llm_worked_examples_cap` were
declared in config, each with a measured justification in its own comment, and read by NO code —
the payload used literal `[:4]` and `[:6]`. Turning either knob changed nothing. Their defaults
are the literals they replace, so shipped behaviour is unchanged; what changes is that the setting
now governs the payload it documents.

These are liveness tests: does shipped configuration actually reach the rule, and is the bound
applied on every path that builds a request rather than just one of them?
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.schemas.loader import load_ontology
from app.services import mapping as mapping_module
from app.services.mapping import OntologyMatcher

# The shipped rulebook these filings actually run against — 462 concepts, 202 of them on the
# balance sheet. The sizes quoted throughout this module were measured on it, so the test reads
# the same seed rather than a hand-built stub: a cap is only meaningful against a real vocabulary.
_SEED = (Path(__file__).resolve().parent.parent
         / "app" / "sample" / "templates" / "output_csv_hk_ontology.json")


@pytest.fixture
def shipped_matcher():
    """A fresh matcher per test, because the matcher memoises key lookups.

    Note that `get_settings()` is cached, so the settings OBJECT is shared across tests however
    this fixture is scoped — the one test that changes a cap restores it in a `finally`, and that
    restore is what protects the rest of the suite, not the scope of this fixture.
    """
    ontology = load_ontology(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)
    return OntologyMatcher(ontology, locale="en", settings=get_settings())


# ── the bound is applied on the batch path, not only the per-line one ────────────────────────────

def _payload_sizes(matcher, statement, captions, sections=None):
    """Build a real chunk payload and report (concept count, characters) of the candidate list."""
    items = [(f"i{n}", c) for n, c in enumerate(captions)]
    sec = sections or {f"i{n}": None for n in range(len(captions))}

    seen: dict = {}
    original = OntologyMatcher._concept_payload

    def spy(self, keys):
        out = original(self, keys)
        seen["n"] = len(out)
        seen["chars"] = len(json.dumps(out, ensure_ascii=False, indent=2))
        return out

    OntologyMatcher._concept_payload = spy
    try:
        # No provider is configured, so the call fails and `_match_chunk` falls back per line —
        # it does NOT propagate, which is the point of that fallback. The payload is assembled
        # BEFORE the call either way, and that is the thing under test.
        matcher._match_chunk(items, statement=statement, sec=sec, preliminary={})
    finally:
        OntologyMatcher._concept_payload = original

    assert seen, "no candidate payload was built, so this measures nothing"
    return seen["n"], seen["chars"]


def test_a_batch_chunk_does_not_offer_the_whole_statement(shipped_matcher):
    """The regression itself: 202 concepts in every balance-sheet call."""
    matcher = shipped_matcher
    mappable = [k for k in matcher._mappable_keys() if matcher._in_statement(k, "balance_sheet")]
    cap = matcher.settings.extraction.llm_candidate_cap
    if len(mappable) <= cap:
        pytest.skip(f"this ontology has only {len(mappable)} balance-sheet concepts, under the cap")

    offered, _chars = _payload_sizes(matcher, "balance_sheet", [
        "Cash and cash equivalents", "Trade and other receivables", "Inventories",
        "Property, plant and equipment", "Total current assets", "Bank borrowings",
    ])

    assert offered <= cap, (
        f"a batch chunk offered {offered} concepts against a cap of {cap}; the whole statement in "
        f"one request is what returned 413/429 and silently degraded two real runs to the "
        f"deterministic path")


def test_the_bound_actually_shrinks_the_request(shipped_matcher):
    """A cap that leaves the request the same size is not a bound."""
    matcher = shipped_matcher
    mappable = [k for k in matcher._mappable_keys() if matcher._in_statement(k, "balance_sheet")]
    if len(mappable) <= matcher.settings.extraction.llm_candidate_cap:
        pytest.skip("ontology smaller than the cap")

    uncapped = len(json.dumps(matcher._concept_payload(matcher._by_priority(mappable)),
                              ensure_ascii=False, indent=2))
    _offered, capped = _payload_sizes(matcher, "balance_sheet", ["Total current assets"])

    assert capped < uncapped, "the capped payload is no smaller than the uncapped one"
    # The measured reduction was 49,151 -> 9,432 tokens (81%). Asserting merely "smaller" would
    # pass on a cap that trimmed one concept, which would not have cleared either provider limit.
    assert capped < uncapped / 2, (
        f"{capped:,} chars vs {uncapped:,} uncapped — not enough of a reduction to fit the "
        f"request limits that failed")


def test_every_path_that_builds_a_candidate_list_applies_the_cap():
    """The general form of the defect: one bounded path, one unbounded, same request shape.

    Both `_llm` (per-line) and `_match_chunk` (batch) assemble a candidate list and send it. The
    cap lived in `match` only, so the busier of the two ran unbounded.
    """
    unbounded = [name for name in ("_match_chunk", "match")
                 if "llm_candidate_cap" not in inspect.getsource(
                     getattr(OntologyMatcher, name))]

    assert not unbounded, (
        f"{unbounded} build an LLM candidate list without reading llm_candidate_cap")


# ── seeds are never evicted by the cap ───────────────────────────────────────────────────────────

def test_a_rows_own_deterministic_evidence_survives_the_cap(shipped_matcher):
    """Capping by priority alone would drop the concept the row's own tier proposed.

    That is the same defect the section restriction exists to prevent: grading the model on an
    answer it was given no candidate for. A chunk carries up to BATCH_MAX_ITEMS captions, so the
    cap bounds the FILL and the seeds go in first.
    """
    matcher = shipped_matcher
    statement = "balance_sheet"
    mappable = [k for k in matcher._mappable_keys() if matcher._in_statement(k, statement)]
    if len(mappable) <= matcher.settings.extraction.llm_candidate_cap:
        pytest.skip("ontology smaller than the cap")

    # A caption the deterministic tiers place on a LOW-priority concept — precisely the one a
    # priority-ordered cut would evict.
    by_priority = matcher._by_priority(mappable)
    tail = by_priority[-1]
    concept = matcher._by_key.get(tail)
    aliases = concept.aliases_for(matcher.locale) if concept else []
    if not aliases:
        pytest.skip(f"lowest-priority concept {tail} has no alias to probe with")

    caption = aliases[0]
    resolved = matcher.match(caption, statement=statement, section=None)
    if not resolved or not resolved.canonical_key:
        pytest.skip(f"the deterministic tiers do not resolve {caption!r}, so there is no seed")

    items = [("i0", caption)]
    seen: dict = {}
    original = OntologyMatcher._concept_payload

    def spy(self, keys):
        out = original(self, keys)
        seen["keys"] = [e["canonical_key"] for e in out]
        return out

    OntologyMatcher._concept_payload = spy
    try:
        matcher._match_chunk(items, statement=statement, sec={"i0": None}, preliminary={})
    finally:
        OntologyMatcher._concept_payload = original

    assert seen, "no candidate payload was built, so this measures nothing"
    assert resolved.canonical_key in seen.get("keys", []), (
        f"the cap evicted {resolved.canonical_key}, which is what the deterministic tiers "
        f"proposed for {caption!r} — the model would be asked to confirm a concept it was never "
        f"offered")


# ── the cap must not become a second section restriction ─────────────────────────────────────────

def _per_section(matcher, keys):
    """How many of `keys` fall in each declared section (statement-level concepts under None)."""
    counts: dict = {}
    for k in keys:
        sections = matcher._sections_of(k)
        counts[frozenset(sections) or None] = counts.get(frozenset(sections) or None, 0) + 1
    return counts


@pytest.mark.parametrize("statement", ["balance_sheet", "profit_and_loss"])
def test_the_cap_starves_no_section(shipped_matcher, statement):
    """A priority-ordered cut re-imposes the narrowing the gate just declined.

    `match_priority` correlates with statement-level totals and with whichever section happens to
    carry the rulebook's most specific concepts, so a 40-slot list filled by priority alone is
    wildly skewed. Measured on the shipped rulebook:

        balance_sheet     non_current_liabilities   1 of 35 by priority,  7 stratified
                          current_liabilities       3 of 50 by priority,  8 stratified
        profit_and_loss   adjustments_to_retained_profits
                                                    0 of  6 by priority,  6 stratified

    The P&L row is the sharp one: a whole section unreachable, as a side effect of a SIZE bound,
    with the gate never saying no. So when the cap bites the cut is spread across sections.
    """
    matcher = shipped_matcher
    mappable = [k for k in matcher._mappable_keys() if matcher._in_statement(k, statement)]
    cap = matcher.settings.extraction.llm_candidate_cap
    if len(mappable) <= cap:
        pytest.skip(f"{statement} has only {len(mappable)} concepts, under the cap")

    every = set(_per_section(matcher, mappable))
    offered = _per_section(matcher, matcher._stratified_fill(mappable)[:cap])

    starved = sorted(str(s) for s in every if not offered.get(s))
    assert not starved, (
        f"{statement}: the cap left {starved} with no candidate at all — a section restriction "
        f"imposed by a size bound rather than by the gate")


def test_the_stratification_is_load_bearing(shipped_matcher):
    """Prove the naive order really does starve a section, so this suite fails if
    `_stratified_fill` is ever 'simplified' back to `_by_priority`.

    Checked across the statements rather than against one: whether priority order starves anything
    depends on how the rulebook distributes `match_priority`, and pinning the statement that
    happens to discriminate today would make this test quietly stop discriminating tomorrow.
    """
    matcher = shipped_matcher
    cap = matcher.settings.extraction.llm_candidate_cap

    skew = []
    for statement in ("balance_sheet", "profit_and_loss", "cash_flow", "changes_in_equity"):
        mappable = [k for k in matcher._mappable_keys()
                    if matcher._in_statement(k, statement)]
        if len(mappable) <= cap:
            continue
        every = set(_per_section(matcher, mappable))
        naive = _per_section(matcher, matcher._by_priority(mappable)[:cap])
        strat = _per_section(matcher, matcher._stratified_fill(mappable)[:cap])
        starved_naive = {s for s in every if not naive.get(s)}
        if starved_naive:
            skew.append((statement, sorted(str(s) for s in starved_naive)))
        # Wherever the naive fill starves a section, the stratified one must not.
        assert not {s for s in every if not strat.get(s)}, statement

    assert skew, (
        "priority order starves no section anywhere in this rulebook, so every assertion above "
        "would also pass with the stratification removed — this test no longer discriminates")


def test_statement_level_concepts_survive_the_cap(shipped_matcher):
    """A subtotal can be printed under any banner, which is why it belongs to no section and
    survives the section restriction. A size bound must not undo that."""
    matcher = shipped_matcher
    statement = "balance_sheet"
    mappable = [k for k in matcher._mappable_keys() if matcher._in_statement(k, statement)]
    cap = matcher.settings.extraction.llm_candidate_cap
    if len(mappable) <= cap:
        pytest.skip("ontology smaller than the cap")

    statement_level = [k for k in mappable if not matcher._sections_of(k)]
    if not statement_level:
        pytest.skip("this rulebook declares no statement-level balance-sheet concepts")

    kept = set(matcher._stratified_fill(mappable)[:cap])
    assert kept & set(statement_level), (
        "the cap kept no statement-level concept, so a subtotal printed under any banner has "
        "nothing to map to")


# ── the two caps that were read by nothing ───────────────────────────────────────────────────────

@pytest.mark.parametrize("knob", ["llm_example_aliases_cap", "llm_worked_examples_cap"])
def test_the_declared_payload_cap_is_read_by_the_code_it_describes(knob):
    """Declared in config with a measured justification, then never read.

    Located by searching the module rather than naming a method, so the test survives a rename of
    the site while still failing if the knob stops being read anywhere.
    """
    source = inspect.getsource(mapping_module)

    assert knob in source, (
        f"{knob} is declared in config.py and read by no code in services/mapping.py — the "
        f"number governs no request and turning the knob does nothing")


def test_the_alias_cap_default_preserves_shipped_behaviour(shipped_matcher):
    """Wiring a dead knob must not change what ships. The default is the literal it replaced."""
    matcher = shipped_matcher
    cap = matcher.settings.extraction.llm_example_aliases_cap

    assert cap == 4, "the shipped default is the literal `[:4]` this replaced"

    longest = max((matcher._by_key[k] for k in matcher._mappable_keys()
                   if k in matcher._by_key),
                  key=lambda m: len(m.aliases_for(matcher.locale)), default=None)
    if longest is None or len(longest.aliases_for(matcher.locale)) <= cap:
        pytest.skip("no concept in this ontology has more aliases than the cap")

    entry = matcher._concept_payload([longest.canonical_key])
    assert entry, "the probe concept produced no payload entry"
    assert len(entry[0]["example_aliases"]) == cap


def test_a_zero_cap_is_not_read_as_unbounded(shipped_matcher):
    """A default may refuse; it may not silently assert. `0` must mean none, not all.

    `int(x or 0)` reads a configured 0 as falsy, so a naive `or` fallback would restore the
    literal default and send every alias — the knob would do the opposite of what it says.
    """
    matcher = shipped_matcher
    concept = next((matcher._by_key[k] for k in matcher._mappable_keys()
                    if k in matcher._by_key and matcher._by_key[k].aliases_for(matcher.locale)),
                   None)
    if concept is None:
        pytest.skip("no concept with aliases")

    matcher.settings.extraction.llm_example_aliases_cap = 0
    try:
        entry = matcher._concept_payload([concept.canonical_key])
    finally:
        matcher.settings.extraction.llm_example_aliases_cap = 4

    assert entry and entry[0]["example_aliases"] == [], (
        "a cap of 0 sent aliases anyway — the setting is being read as unbounded")
