"""The per-deployment thresholds lifted out of services/mapping.py, pinned to the literals.

A threshold has three homes — protective CODE, the rulebook SET that calibrated it, or
per-deployment SETTINGS — and this file guards the SETTINGS half of that split for the payload
and provider caps in `services.mapping`. Two different failures are possible while a literal is
being replaced by a setting, and both are silent:

* the default and the literal differ, so declaring nothing in config.toml still changes how the
  pipeline calls the provider (a smaller batch reply is truncated JSON, which is not a partial
  answer — the chunk falls back to the weaker per-line path and the run still calls itself
  LLM-mapped);
* the setting is declared and read nowhere, so the file reports a control that does nothing.

So each cap is asserted from the OUTSIDE — the chunk sizes a batch actually cuts, the
`max_tokens` a call actually asks for, the number of examples that actually reach the system
prompt. That holds before the migration (mapping.py's literal) and after it (mapping.py reading
the setting), and fails if the number moves in between. The matchers are built from a FRESH
`Settings()` rather than `get_settings()`, because the admin settings screen mutates the cached
object in place (services.settings_state), and a value another test left there would otherwise
read as a migration failure.

Asserting against the DEFAULT catches the first failure but is blind to the second, because the
default is deliberately equal to the literal — so each cap that has actually been wired is also
read back from a MOVED `Settings()`, which is the only form a hardcoded literal fails.

Also recorded here: the caps deliberately NOT exposed, with the measurement that decided it. A
per-rulebook number offered as a deployment knob is as wrong as a literal left in code, and a
knob whose slice can never truncate is worse than either.
"""
from __future__ import annotations

import json
import pathlib
from uuid import uuid4

import pytest

from app.config import ExtractionSettings, Settings
from app.schemas.loader import load_ontology
from app.services.mapping import (LlmBatchDecision, LlmBatchItem, LlmMappingDecision,
                                  OntologyMatcher)

TEMPLATES = pathlib.Path("app/sample/templates")

# The five settings this package added, each against the mapping.py literal it replaces.
SHADOWED_LITERALS = {
    "llm_batch_max_items": 25,              # OntologyMatcher.BATCH_MAX_ITEMS
    "llm_batch_response_floor_tokens": 8192,  # the floor in _effective_batch_max_tokens
    "llm_line_max_tokens": 512,             # the max_tokens the per-line _llm call asks for
    "llm_worked_examples_cap": 6,           # ontology.worked_examples[:6] in _build_system
    "llm_example_aliases_cap": 4,           # aliases_for(locale)[:4] in _concept_payload
}


def _default(name: str):
    """The shipped default, not the live value — that is what must equal the literal."""
    return ExtractionSettings.model_fields[name].default


class Spy:
    """Answers every structured call and records the response allocation each one asked for.

    Batch requests are recorded separately from per-line ones: a batch that resolves nothing
    falls back per line THROUGH THE SAME PROVIDER, so one list would mix the two budgets and the
    per-line assertion would be reading a batch cap.
    """

    id = "spy"

    def __init__(self) -> None:
        self.line_caps: list[int] = []
        self.batch_caps: list[int] = []
        self.batch_payloads: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        payload = json.loads(messages[-1]["content"])
        meta = {"model": "spy", "input_tokens": 1, "output_tokens": 1}
        if response_schema is LlmBatchDecision:
            self.batch_caps.append(max_tokens)
            self.batch_payloads.append(payload)
            return LlmBatchDecision(mappings=[
                LlmBatchItem(item_id=i["item_id"], canonical_key="", confidence=0.0)
                for i in payload["source_items"]]), meta
        self.line_caps.append(max_tokens)
        return LlmMappingDecision(canonical_key="", confidence=0.0), meta


def _ontology(name: str):
    return load_ontology(
        json.loads((TEMPLATES / name).read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def rulebook():
    """The rulebook the pipeline actually runs on (462 concepts)."""
    return _ontology("output_csv_hk_ontology.json")


@pytest.fixture(scope="module")
def authored_examples():
    """The one shipped rulebook that authors more worked examples than the cap admits: 8."""
    return _ontology("hkfrs_hk_china_ontology.json")


def _matcher(ontology, provider=None, **moved):
    """A matcher on a FRESH `Settings()`, optionally with some extraction fields MOVED.

    The moved form is what actually detects a dead setting. Asserting only against the shipped
    default cannot: the default is chosen to EQUAL the literal it replaces, so a reader that never
    consults the setting agrees with the assertion anyway. That is how
    `llm_batch_response_floor_tokens` and `llm_line_max_tokens` sat here declared and read by
    nothing — measured: with the floor set to 1234 the setting reported 1234 while
    `_effective_batch_max_tokens(1)` and `(25)` both still returned 8192.
    """
    settings = Settings()
    for name, value in moved.items():
        assert name in ExtractionSettings.model_fields, name
        setattr(settings.extraction, name, value)
    return OntologyMatcher(ontology, settings=settings, llm_provider=provider)


# --- the shipped config must not already move them, or the tests below prove nothing ------------

def test_nothing_in_the_config_file_moves_these_off_their_defaults():
    """The behavioural assertions below compare mapping.py against the DEFAULT. That is only the
    same thing as comparing it against the shipped configuration while config.toml declares none
    of these — which is deliberate: a provider limit belongs in the deployment that has that
    provider, not checked in for every clone."""
    live = Settings().extraction
    for name, literal in SHADOWED_LITERALS.items():
        assert _default(name) == literal, f"{name} default drifted from the mapping.py literal"
        assert getattr(live, name) == literal, f"config.toml moved {name} away from the literal"


# --- transport size and response allocation -----------------------------------------------------

def test_the_batch_chunk_size_default_is_the_size_a_batch_is_actually_cut_into(rulebook):
    """A 170-caption statement, cut. The assertion is on the SIZES the provider saw, so it reads
    the same before and after `BATCH_MAX_ITEMS` becomes `extraction.llm_batch_max_items`."""
    cap = _default("llm_batch_max_items")
    spy = Spy()
    m = _matcher(rulebook, spy)
    items = [(str(uuid4()), f"Caption {i}") for i in range(170)]
    m.match_batch(items, statement="balance_sheet")

    sizes = [len(p["source_items"]) for p in spy.batch_payloads]
    assert sizes == [cap] * (170 // cap) + [170 % cap], sizes
    assert m.usage["batch_max_items"] == cap


def _one_statement_doc(captions: list[str]):
    """A one-page balance sheet printing `captions`, with no section banner on any row.

    No banner is deliberate: `stages.map_ontology` splits a statement batch by printed section, so
    banners would cut the rows into several subgroups and the chunk sizes below would be measuring
    the SECTION split rather than `llm_batch_max_items`. One subgroup, one chunking decision.
    """
    from decimal import Decimal

    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import Basis
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue, LineItem

    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="balance_sheet")]
    doc.line_items = []
    for ordinal, caption in enumerate(captions):
        li = LineItem(source_label=caption, ordinal=ordinal)
        for period in ("current", "prior"):
            li.set_value(ExtractedValue(
                value=Decimal(1), value_raw=Decimal(1), basis=Basis("consolidated"),
                period_label=period, provenance=Provenance(page_index=0)))
        doc.line_items.append(li)
    return doc


def test_the_batch_chunk_size_is_read_from_the_setting_and_not_the_class_constant(rulebook):
    """MOVED, and driven through the STAGE — which is the only place this setting is read.

    `OntologyMatcher.match_batch` takes `chunk_size` and honours it (`size = chunk_size or
    self.BATCH_MAX_ITEMS`), so the matcher-level test above cannot detect the defect: it was the
    CALLER that never passed one. `stages.map_ontology` read `llm_batch_max_items` and spent it on
    the progress denominator and the `chunk_size=` log field only, so the provider kept receiving
    25 — measured: with the setting at 7 and 60 rows batched, the log said `chunk_size=7`,
    `llm_planned_calls=9`, and the payloads the provider saw were [25, 25, 10].

    So the assertion is on the sizes the PROVIDER saw under a moved setting, not on the log line
    and not on the plan — either of those would have passed while the wiring was dead. The plan is
    checked too, because its whole purpose is to predict this number.
    """
    from app.core.stage import PipelineContext
    from app.stages.map_ontology import MapOntologyStage

    moved = 7                       # neither the default 25 nor any factor of the 60 rows below
    assert moved != _default("llm_batch_max_items")
    spy = Spy()
    # A FRESH `Settings()`, like `_matcher`: `PipelineContext` defaults to the cached
    # `get_settings()` object, so moving a field on it would leak into every later test.
    settings = Settings()
    settings.extraction.llm_batch_max_items = moved
    # UNFOCUSED ROUTING, said explicitly. config.toml ships `llm_focus_only = true`, under which
    # the deterministic tiers answer all but the focus rows and almost nothing reaches a batch —
    # so the chunking this test measures would not happen at all.
    settings.extraction.llm_focus_only = False
    settings.llm.provider = "spy-chunk"

    ctx = PipelineContext(raw_bytes=b"", settings=settings)
    ctx.ontology = rulebook                                     # type: ignore[attr-defined]
    ctx.registry.register("llm", "spy-chunk", lambda: spy)      # type: ignore[attr-defined]
    doc = _one_statement_doc([f"Caption {i}" for i in range(60)])
    MapOntologyStage().run(doc, ctx)

    sizes = [len(p["source_items"]) for p in spy.batch_payloads]
    assert sizes == [moved] * (60 // moved) + [60 % moved], sizes
    planned = [ln for ln in ctx.logs if ln.startswith("map_line_items:llm_planned_calls=")]
    assert planned and f"chunk_size={moved}" in planned[0], planned
    assert f"llm_planned_calls={len(sizes)}" in planned[0], planned[0]


def test_the_batch_response_floor_default_is_the_allocation_a_batch_asks_for(rulebook):
    """The floor, not the envelope derivation, is the batch budget in practice: derived(25) is
    2,256 tokens and the floor wins for every chunk up to 99 items (crossover at 100). So a
    default that did not equal the floor would change the allocation of EVERY batch call this
    pipeline makes, at any chunk size it uses."""
    floor = _default("llm_batch_response_floor_tokens")
    spy = Spy()
    m = _matcher(rulebook, spy)
    m.match_batch([(str(uuid4()), f"Caption {i}") for i in range(60)],
                  statement="balance_sheet")

    assert spy.batch_caps and set(spy.batch_caps) == {floor}, spy.batch_caps
    assert m._effective_batch_max_tokens(1) == floor
    assert m._effective_batch_max_tokens(_default("llm_batch_max_items")) == floor
    # …and the floor is a floor, not a ceiling: a chunk big enough to need more still gets more,
    # which is the half of `_effective_batch_max_tokens` that stays in code.
    assert m._effective_batch_max_tokens(400) > floor


def test_the_batch_response_floor_is_read_from_the_setting_and_not_the_literal(rulebook):
    """MOVED, because the default cannot detect a dead setting: it equals the literal it replaces,
    so a reader that never consults it agrees with the test above anyway. That is precisely how the
    floor shipped inert — `max(8192, …)` in `_effective_batch_max_tokens` ignored the field, and a
    deployment that raised it for a gateway needing more headroom got 8192 regardless."""
    moved = _default("llm_batch_response_floor_tokens") * 2 + 7   # nothing else can produce it
    spy = Spy()
    m = _matcher(rulebook, spy, llm_batch_response_floor_tokens=moved)
    m.match_batch([(str(uuid4()), f"Caption {i}") for i in range(30)],
                  statement="balance_sheet")

    assert spy.batch_caps and set(spy.batch_caps) == {moved}, spy.batch_caps
    assert m._effective_batch_max_tokens(_default("llm_batch_max_items")) == moved
    # 0 is not "restore the default": it means no floor, so the envelope derivation stands alone.
    # `or 8192` in the reader would silently refuse that configuration.
    bare = _matcher(rulebook, llm_batch_response_floor_tokens=0)
    assert bare._effective_batch_max_tokens(25) == OntologyMatcher._batch_max_tokens(25)


def test_the_per_line_response_default_is_the_allocation_the_per_line_call_asks_for(rulebook):
    cap = _default("llm_line_max_tokens")
    spy = Spy()
    m = _matcher(rulebook, spy)
    m.match("A caption no alias in the rulebook claims")

    assert spy.line_caps == [cap], spy.line_caps
    assert not spy.batch_caps, "this must measure the per-line call, not a batch"


def test_the_per_line_response_cap_is_read_from_the_setting_and_not_the_literal(rulebook):
    """The same MOVED reading for the per-line allocation, which was `max_tokens=512,` in `_llm`.
    Worth pinning separately: this is the path a failed batch falls back to, so it is the call that
    runs when the response budget is already known to be wrong."""
    moved = _default("llm_line_max_tokens") + 137
    spy = Spy()
    m = _matcher(rulebook, spy, llm_line_max_tokens=moved)
    m.match("A caption no alias in the rulebook claims")

    assert spy.line_caps == [moved], spy.line_caps
    assert not spy.batch_caps, "this must measure the per-line call, not a batch"


# --- prompt payload caps ------------------------------------------------------------------------

def test_the_worked_examples_cap_default_is_how_many_reach_the_system_prompt(authored_examples):
    """Measured on the rulebook where this bites: 8 authored, 6 admitted. The two dropped are
    1,023 characters — 16% of that ontology's system prompt — so the default has to be the cap and
    not the authored length, or the migration would change what the model is told."""
    cap = _default("llm_worked_examples_cap")
    assert len(authored_examples.worked_examples) > cap, "this rulebook no longer exercises the cap"

    system = _matcher(authored_examples)._build_system()
    tail = system.split("Worked examples:", 1)[1]
    assert len([ln for ln in tail.splitlines() if ln.startswith("- ")]) == cap


def test_the_example_aliases_cap_default_is_how_many_aliases_reach_a_candidate(rulebook):
    """137 of the 462 concepts author more than 4 aliases (median 3, longest 23), so the cap
    bounds the tail. Read off the payload the model is actually sent."""
    cap = _default("llm_example_aliases_cap")
    m = _matcher(rulebook)
    keys = m._by_priority([k for k in m._mappable_keys() if m._in_statement(k, "balance_sheet")])
    payload = m._concept_payload(keys)

    lengths = [len(entry["example_aliases"]) for entry in payload]
    assert max(lengths) == cap, max(lengths)
    truncated = sum(1 for k in keys
                    if len(m._by_key[k].aliases_for(m.locale)) > cap)
    assert truncated, "no candidate's alias list is truncated, so the cap is unmeasured here"


# --- the caps that WERE judged not to be settings, and the one that changed its mind ------------

def test_the_review_shortlist_caps_now_truncate_so_they_have_a_home(rulebook):
    """THE TRIPWIRE ABOVE FIRED, AND THIS IS WHAT IT ASKED FOR.

    This test used to assert `max(len(result.candidates)) <= 2` and was called
    "..._can_never_truncate_so_they_are_not_knobs". Its reasoning was measured and correct: with
    no string-similarity tier the deterministic pool held one candidate, so `ranked[:5]`,
    `ranked[:4]`, `primary[:5]` and the batch payload's `deterministic_candidates[:3]` were each
    the whole list, and exposing them would have advertised four knobs that could not change an
    answer. It also said, in its own words, what to do when that stopped being true:

        "it starts failing the day a tier is added that can propose a third candidate — at which
         point the slice becomes a real decision and needs a real home."

    THAT DAY ARRIVED with `confusable_with` authored on output_csv_hk: 40 mutual pairs over the
    measured equal-priority collisions, including a 5-concept `is_oci__*` clique that shares seven
    aliases. A refused confusable tie returns every tied concept as a candidate, so the pool now
    reaches EIGHT on this rulebook and all four slices genuinely cut.

    So the assertion is inverted rather than relaxed: the caps are now load-bearing, they are
    settings (`extraction.review_candidate_cap`, `extraction.llm_deterministic_candidate_cap`),
    and what is pinned is that the code honours them. The old bound is kept below as a recorded
    measurement, not as a requirement.
    """
    m = _matcher(rulebook)
    seen = set()
    for concept in rulebook.mappings:
        for label in [concept.label, *(concept.aliases or [])]:
            if not label:
                continue
            result = m.match(label, statement=concept.statement)
            seen.add(len(result.candidates))

    assert seen, "the corpus produced no results, so this measures nothing"
    # The fact that made the caps real. If this ever drops back to <= 2 the caps stop being
    # load-bearing and the previous test's argument for keeping them in code applies again.
    assert max(seen) > 2, (
        f"the deterministic pool is back to at most {max(seen)} candidates, so the caps cannot "
        f"truncate — see this test's history before treating them as knobs")


def test_a_confusable_tie_is_never_truncated(rulebook):
    """The one candidate list the cap must NOT govern.

    A ranked shortlist cuts its tail and loses low-evidence guesses. A confusable tie has no tail:
    the tied set IS the answer ("one of these, and the engine will not choose"), so dropping a
    member removes the correct concept from the only list the reviewer sees, with nothing saying
    it was cut. Measured on output_csv_hk, the `is_oci__*` clique ties five concepts sharing seven
    aliases and segment candidates take the list to eight — a review cap of five would hide three.
    """
    m = _matcher(rulebook)
    biggest = 0
    for concept in rulebook.mappings:
        for label in [concept.label, *(concept.aliases or [])]:
            if not label:
                continue
            result = m.match(label, statement=concept.statement)
            if result.allocation_status == "unmapped_review" and result.candidates:
                biggest = max(biggest, len(result.candidates))

    cap = Settings().extraction.review_candidate_cap
    assert biggest > cap, (
        f"the largest tie is {biggest} against a review cap of {cap}; if ties no longer exceed "
        f"the cap this test cannot detect truncation and needs a rulebook that produces one")


def test_both_candidate_caps_are_read_from_settings_not_hardcoded():
    """The four literals are gone from the CODE.

    Docstrings and comments are stripped before the check, because the comments recording this
    migration necessarily quote the literals they replaced — matching prose instead of code is a
    trap this repo has hit before, and it makes a passing assertion meaningless in one direction
    and an impossible one in the other.
    """
    import ast
    import inspect

    from app.services import mapping as mapping_module

    source = inspect.getsource(mapping_module)
    for knob in ("review_candidate_cap", "llm_deterministic_candidate_cap"):
        assert knob in source, f"{knob} is declared in config.py and read by no code"

    # Re-emit the module from its AST with every docstring blanked: `ast.unparse` drops comments
    # outright, so what remains is executable code only.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    code_only = ast.unparse(tree)

    for literal in ("ranked[:5]", "ranked[:4]", "primary[:5]", "candidates[:3]"):
        assert literal not in code_only, (
            f"{literal} is back in code — a cap that can now truncate must not be a literal")


def test_a_zero_candidate_cap_means_none_not_unbounded():
    """A default may refuse; it may not silently assert.

    `int(x or DEFAULT)` reads a configured 0 as falsy and restores the default — the polarity
    defect this codebase has already been bitten by on `recon_rel_tolerance`. Zero here must mean
    "show no candidates", not "show all of them".

    Asserted from a MOVED `Settings()`, which per this module's docstring is the only form a
    hardcoded literal actually fails: the default is deliberately equal to the literal it
    replaced, so reading back the default proves nothing.
    """
    from app.services.mapping import _det_cap, _review_cap

    moved = Settings()
    moved.extraction.review_candidate_cap = 0
    moved.extraction.llm_deterministic_candidate_cap = 0

    assert _review_cap(moved) == 0
    assert _det_cap(moved) == 0

    # …and a moved non-zero value is honoured too, which is the migration itself.
    moved.extraction.review_candidate_cap = 7
    moved.extraction.llm_deterministic_candidate_cap = 6
    assert _review_cap(moved) == 7
    assert _det_cap(moved) == 6


def test_the_batch_envelope_slope_is_calibrated_against_this_vocabulary_not_the_deployment(
        rulebook):
    """`_BATCH_RESPONSE_TOKENS_PER_ITEM = 80` and `_BATCH_RESPONSE_RESERVE = 256` stayed in code.

    80 is not a provider number: it is the cost of one serialised decision, and what makes a
    decision long is the CANONICAL KEY in it, which is rulebook content. mapping.py records the
    measurement as "this file's longest canonical_key (101 chars)"; the rulebook the pipeline runs
    on today has a longest key of 72, so the constant is already calibrated against a vocabulary
    that is not this one — exactly the coupling that a deployment knob would hide instead of fix.
    Asserted as an inequality, since the honest fix is to derive it from the loaded set.
    """
    longest = max((c.canonical_key for c in rulebook.mappings), key=len)
    envelope = LlmBatchDecision(mappings=[
        LlmBatchItem(item_id=str(uuid4()), canonical_key=longest, confidence=0.95,
                     allocation_status="parent_gross_evidence_only")
        for _ in range(_default("llm_batch_max_items"))]).model_dump_json()

    # ~3 characters per token for JSON of UUIDs and long snake_case identifiers.
    assert len(envelope) / 3 <= OntologyMatcher._batch_max_tokens(_default("llm_batch_max_items"))
    assert len(longest) < 101, (
        "the recorded 101-char measurement no longer describes this rulebook")
