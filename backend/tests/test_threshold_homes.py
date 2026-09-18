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
from app.services.mapping import OntologyMatcher

TEMPLATES = pathlib.Path("app/sample/templates")

# The settings this package added, each against the mapping.py literal it replaces.
#
# FOUR OF THE ORIGINAL FIVE ARE GONE with the row-driven LLM path, and so are the tests that
# measured them: `llm_batch_max_items` (the chunk a batch of printed rows was cut into),
# `llm_batch_response_floor_tokens` (that chunk's reply allocation), `llm_line_max_tokens` (the
# allocation for a single-caption call) and `llm_example_aliases_cap` (how many aliases travelled
# with each CANDIDATE concept — there are no candidates now: a line-item request names the line).
# Each described a request about a printed ROW, and no such request is made; see config.py's
# tombstones.
#
# ONE REMAINS, and it is the one that was never about a row: worked examples are the rulebook's own
# judgement, they ride in the SYSTEM message, and `mapping.authored_guidance` still assembles that
# for every line-item request.
SHADOWED_LITERALS = {
    "llm_worked_examples_cap": 6,           # ontology.worked_examples[:6] in authored_guidance
}


def _default(name: str):
    """The shipped default, not the live value — that is what must equal the literal."""
    return ExtractionSettings.model_fields[name].default


def _ontology(name: str):
    return load_ontology(
        json.loads((TEMPLATES / name).read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def rulebook():
    """The rulebook the pipeline actually runs on (462 concepts)."""
    return _ontology("output_csv_hk_ontology.json")


@pytest.fixture(scope="module")
def tied_rulebook():
    """A rulebook that GUARANTEES a confusable tie wider than the review cap.

    THIS USED TO BE THE SHIPPED RULEBOOK, and the two tests below read its `is_oci__*` clique —
    five concepts sharing seven aliases, taking a candidate list to eight. That clique was a
    DEFECT: the section total's caption sat on five of its own components at equal priority with
    no label owner, so `match()` returned whichever declaration order reached first and the other
    four were unreachable for that caption. Resolving it (see
    `services.spec_alias_curation._THE_OCI_TOTAL_IS_NOT_A_COMPONENT`) took the set's unbreakable
    ties from 140 to 8 — and took these two tests' PRECONDITION with it.
    
    Reading the shipped set for the tie was the wrong shape twice over. It made a test of the
    CODE's behaviour depend on an accident of the DATA, and it made the suite reward leaving
    undiscriminating captions in the configuration: every tie resolved brought this file closer to
    failing. The property under test — a ranked shortlist may be cut, a tie may not — belongs to
    `services.mapping`, so it is tested against a fixture that cannot stop producing one.

    Six concepts in one section sharing one alias, none of them its label owner, all at equal
    priority, each naming the other five as confusable: the exact shape the resolved clusters had.

    All four conditions are load-bearing, and `services.mapping._exact_tie` is where to read why.
    Same section so the scoping gate admits all six on one caption; nobody's label so
    `_prefer_label_owners` does not reduce them to one; equal `match_priority` so step 4 cannot
    settle it; and MUTUAL `confusable_with` because `_mutually_confusable` requires each concept to
    name the other. Drop any one of them and `match()` returns `direct_exclusive` on the first
    declared concept — which is precisely the defect step 6 exists to refuse, and what these tests
    would then silently stop testing. `binding.order` must also be declared: `_confusable_tie`
    returns [] for a rulebook that states no precedence, since step 6 is a v2 declaration.
    """
    keys = [f"bs_ca__tied_{n}" for n in range(6)]
    return load_ontology({
        "ontology_key": "tied_fixture",
        "target_template_key": "output_csv_hk_v1",
        "binding": {"order": [
            "1. Resolve the statement.",
            "2. Resolve the printed section.",
            "3. Restrict candidates to concepts whose section_scope contains it.",
            "4. Rule tier in descending match_priority.",
            "5. Semantic tier over the restricted set only.",
            "6. Tie between concepts listed in each other's confusable_with: emit both as "
            "candidates and route to review. Never pick by declaration order.",
        ]},
        "section_defaults": {"bs_ca": {"statement": "balance_sheet",
                                       "section_scope": ["bs_ca"], "match_priority": 81}},
        "mappings": [
            {"canonical_key": key, "label": f"Tied concept {n}",
             "inherits": "bs_ca", "definition": "a synthetic tie member",
             "aliases": ["A caption none of them owns"],
             "confusable_with": [k for k in keys if k != key]}
            for n, key in enumerate(keys)
        ],
    }, resolve=True)


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


# --- prompt payload caps ------------------------------------------------------------------------

def test_the_worked_examples_cap_default_is_how_many_reach_the_system_prompt(authored_examples):
    """Measured on the rulebook where this bites: 8 authored, 6 admitted. The two dropped are
    1,023 characters — 16% of that ontology's system prompt — so the default has to be the cap and
    not the authored length, or the migration would change what the model is told.

    Read off `authored_guidance`, which is where `OntologyMatcher._build_system` went when the
    matcher stopped making calls. The prompt it assembles is the CONFIGURED half — the master
    prompt, the rulebook's policies, its worked examples — and it is now prefixed by the
    line-item reply contract instead of the row one.
    """
    cap = _default("llm_worked_examples_cap")
    assert len(authored_examples.worked_examples) > cap, "this rulebook no longer exercises the cap"

    from app.services.mapping import authored_guidance

    system = authored_guidance(authored_examples)
    tail = system.split("Worked examples:", 1)[1]
    assert len([ln for ln in tail.splitlines() if ln.startswith("- ")]) == cap


# --- the caps that WERE judged not to be settings, and the one that changed its mind ------------

def test_the_review_shortlist_caps_now_truncate_so_they_have_a_home(tied_rulebook):
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
    m = _matcher(tied_rulebook)
    seen = set()
    for concept in tied_rulebook.mappings:
        for label in [concept.label, *(concept.aliases or [])]:
            if not label:
                continue
            result = m.match(label, statement=concept.statement)
            seen.add(len(result.candidates))

    assert seen, "the corpus produced no results, so this measures nothing"
    # The fact that makes the caps real, measured on a fixture that cannot stop producing a tie —
    # see `tied_rulebook` for why this no longer reads the shipped set.
    assert max(seen) > 2, (
        f"the deterministic pool is back to at most {max(seen)} candidates, so the caps cannot "
        f"truncate — see this test's history before treating them as knobs")


def test_a_confusable_tie_is_never_truncated(tied_rulebook):
    """The one candidate list the cap must NOT govern.

    A ranked shortlist cuts its tail and loses low-evidence guesses. A confusable tie has no tail:
    the tied set IS the answer ("one of these, and the engine will not choose"), so dropping a
    member removes the correct concept from the only list the reviewer sees, with nothing saying
    it was cut.

    MEASURED ON A SYNTHETIC RULEBOOK, not on the shipped one — see `tied_rulebook`. It used to read
    the `is_oci__*` clique, which was a defect that has since been repaired, and a test of the
    code's behaviour must not depend on a defect in the data surviving.
    """
    m = _matcher(tied_rulebook)
    biggest = 0
    for concept in tied_rulebook.mappings:
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


def test_the_review_candidate_cap_is_read_from_settings_not_hardcoded():
    """The literals are gone from the CODE.

    Docstrings and comments are stripped before the check, because the comments recording this
    migration necessarily quote the literals they replaced — matching prose instead of code is a
    trap this repo has hit before, and it makes a passing assertion meaningless in one direction
    and an impossible one in the other.
    """
    import ast
    import inspect

    from app.services import mapping as mapping_module

    source = inspect.getsource(mapping_module)
    # ONE CAP, NOT TWO. `llm_deterministic_candidate_cap` decided how much of the deterministic
    # reading was NAMED TO THE MODEL on a per-caption call; there is no such call, a confusable tie
    # is reported rather than resolved, and the reviewer is the only consumer of the shortlist left.
    assert "review_candidate_cap" in source, (
        "review_candidate_cap is declared in config.py and read by no code")
    assert "llm_deterministic_candidate_cap" not in source, (
        "the retired cap is back in code — see config.py's note on why one cap serves")

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


def test_a_zero_review_cap_means_none_not_unbounded():
    """A default may refuse; it may not silently assert.

    `int(x or DEFAULT)` reads a configured 0 as falsy and restores the default — the polarity
    defect this codebase has already been bitten by on `recon_rel_tolerance`. Zero here must mean
    "show no candidates", not "show all of them".

    Asserted from a MOVED `Settings()`, which per this module's docstring is the only form a
    hardcoded literal actually fails: the default is deliberately equal to the literal it
    replaced, so reading back the default proves nothing.
    """
    from app.services.mapping import _review_cap

    moved = Settings()
    moved.extraction.review_candidate_cap = 0
    assert _review_cap(moved) == 0

    # …and a moved non-zero value is honoured too, which is the migration itself.
    moved.extraction.review_candidate_cap = 7
    assert _review_cap(moved) == 7


