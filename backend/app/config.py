"""Application configuration.

Settings are layered, highest precedence first:

  1. Environment variables (prefix ``FINEX_``; nested keys use ``__``, e.g.
     ``FINEX_LLM__MODEL``, ``FINEX_FEATURES__UI_LOCALIZATION``).
  2. ``.env`` file.
  3. ``config.toml`` at the backend root — the human-editable, git-safe config file
     for LLM / OCR / extraction / auth / feature settings (see that file's comments).
  4. Built-in defaults below.

Everything infra-specific (database URL, object store, which OCR/LLM adapters to
use) can therefore be deferred to deployment. Defaults are chosen so the app runs
locally with zero external services (SQLite + local object store + stub engines).

Secrets (API keys, DB passwords) are NEVER read from ``config.toml`` — the LLM key is
read at call time from the environment variable named by ``llm.api_key_env``.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

_LOG = logging.getLogger(__name__)

# config.toml lives at the backend root (two levels up from this file: app/config.py).
_CONFIG_TOML = Path(__file__).resolve().parent.parent / "config.toml"


class AuthSettings(BaseModel):
    """Authentication / session behaviour."""

    # Accept the X-Role header as a dev/service credential when there is no session.
    # OFF by default (a real session token is the only way in); enable explicitly for
    # local dev / CI only. A valid session always takes precedence over this header.
    allow_role_header: bool = False
    # Passwordless quick-login for the seeded demo users. Set False in production.
    demo_mode: bool = True
    # Session token lifetime in minutes.
    session_ttl_minutes: int = 480


class FeatureSettings(BaseModel):
    """Admin-configurable feature flags."""

    # Localize the whole UI (not just extracted financial output). Startup default;
    # an admin can flip it at runtime from the Settings screen.
    ui_localization: bool = False
    # Require a second-person reviewer SIGN-OFF on the analyst's output. When False the
    # workflow closes at the analyst (they finalize & export directly). This governs the
    # sign-off/hand-off only — the human-in-the-loop Review Queue (unplaced face figures, failed
    # validation rules, subtotals that do not match) stays available to the analyst either way.
    # Admin-flippable at runtime.
    review_required: bool = True
    # Load the seeded sample project at startup. Off by default → the app starts
    # greenfield (empty); an admin can load/clear the sample at runtime from Settings.
    seed_demo: bool = False
    default_output_locale: str = "en"
    supported_locales: list[str] = Field(default_factory=lambda: ["en", "zh", "ar", "fr"])


class LlmSettings(BaseModel):
    """Configuration for the selected LLM adapter (used for mapping disambiguation).

    The default is GPT-5 mini on Azure OpenAI. Every field here is editable at run time from the
    Settings screen and persisted (see services.settings_state), so the default is a starting point
    rather than a commitment — switching model, deployment, region or provider is configuration, not
    a code change. The KEY is never configuration: only the NAME of the environment variable holding
    it is stored, so a credential cannot end up in the database or in a settings export.
    """

    provider: str = "openai_compatible"  # azure_openai | anthropic | bedrock_gateway | openai | openai_compatible | stub
    model: str = "azure-openai/gpt5.4-mini"
    # NO `temperature` here, deliberately. It used to be a field, admin-editable, persisted and
    # echoed back by GET /settings — and passed to NOTHING: none of the nine `complete_structured`
    # call sites forwarded it, so `ports.llm.LlmProvider.complete_structured`'s own
    # `temperature: float = 0.0` won on every call and the Settings screen confirmed a change that
    # no provider request ever carried. Sampling temperature is fixed at that port default for
    # determinism: this is structured financial extraction against a Pydantic schema, and two
    # adapters discard the parameter outright (anthropic_llm.py, bedrock_gateway_llm.py) while
    # azure_openai_llm.py drops it adaptively when the deployment rejects it. Wiring it up would
    # have made a non-zero value reachable, which no use case here wants; the knob is gone instead.
    # Per-call completion ceiling for the FREE-FORM calls (gap routing, contingent-liabilities
    # narrative, commentary/analysis, netting): each of those passes this through as the requested
    # allocation, so it replaces the callee's own default. Mapping ignores it and derives its own
    # budget — see `llm_batch_response_floor_tokens` below for why sending a shared ceiling to a
    # gateway is the wrong number. This was 4096000, which is that same failure with the numbers
    # filled in: the gateway replied "max_tokens is too large: 4096000. This model supports at most
    # 128000 completion tokens". 32768 is above every callee default (largest: run_analysis's 4096)
    # and below any advertised completion ceiling, so no reply is truncated and none is rejected.
    max_tokens: int = 32768
    timeout_seconds: int = 600
    base_url: str = "https://llmgateway.crisil.local/api/openai"
    api_key_env: str = "AZURE_OPENAI_API_KEY"  # env var the key is read from (not the key)
    reasoning_effort: str = "low"      # low | medium | high (provider/gateway dependent)
    # >0 sends OpenRouter's `reasoning.max_tokens` to CAP reasoning. Needed for free models where
    # reasoning is mandatory (cannot be disabled) and would otherwise spend the whole completion
    # budget thinking, leaving no JSON (finish_reason=length, empty content). 0 = don't send it.
    reasoning_max_tokens: int = 0
    disable_ssl_verify: bool = True

    # Azure OpenAI only. Azure does not address a model by name on a shared endpoint the way OpenAI
    # does — it addresses a DEPLOYMENT on your own resource, at
    # {azure_endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...
    # so the resource and the api-version are part of the address and cannot be inferred.
    azure_endpoint: str = ""          # e.g. https://<resource>.openai.azure.com
    azure_api_version: str = "2024-12-01-preview"
    # The deployment NAME, which an operator chooses when deploying and which is frequently not the
    # model name. Left empty it falls back to `model`, which is the common case where someone named
    # the deployment after the model it serves.
    azure_deployment: str = ""

    def azure_deployment_name(self) -> str:
        return (self.azure_deployment or self.model or "").strip()


class OcrSettings(BaseModel):
    # docling = recommended free, pip-only engine (layout + OCR + tables, no system binary);
    # azure = Azure AI Document Intelligence (cloud layout+OCR+tables); paddleocr / tesseract
    # are alternatives. Default stays "stub" so the app runs offline with zero external
    # services; set the engine (and provide its config/extra) for scanned docs.
    engine: str = "stub"              # docling | azure | paddleocr | tesseract | stub
    languages: list[str] = Field(default_factory=lambda: ["en"])
    dpi: int = 300

    # Azure AI Document Intelligence (used when engine = "azure"). The resource endpoint
    # and model are config; the key is read at call time from the env var named below, so
    # the secret never lives in config or the UI (same policy as the LLM key).
    azure_endpoint: str = ""                          # e.g. https://<resource>.cognitiveservices.azure.com
    azure_model: str = "prebuilt-layout"              # prebuilt-layout | prebuilt-read
    azure_api_version: str = "2024-11-30"
    azure_api_key_env: str = "AZURE_DI_KEY"


class ExtractionSettings(BaseModel):
    """Pipeline tuning: native/scanned detection, the mapping ensemble, reconciliation."""

    # Native-vs-scanned page detection (ingest.py:106). A page is NATIVE/MIXED when it clears BOTH
    # floors below — extracted character count and extracted-text area coverage. That is the whole
    # of the configurable rule: page DPI is deliberately NOT part of it. A `low_dpi_threshold: int
    # = 150` used to sit here under a comment naming ingest.py, which made these two read as a
    # third of a rule, but nothing anywhere read it (repo-wide grep: exactly one hit, its own
    # declaration) and it could not have worked — PageSource.dpi (core/models/document.py:27) is
    # never assigned, since ingest.py:114-123 constructs PageSource without it. Reviving it would
    # mean deriving a per-page DPI first, and would reclassify currently-NATIVE pages as SCANNED
    # into an OCR engine that defaults to "stub". Deleted instead.
    native_min_chars: int = 100
    native_min_text_coverage: float = 0.02
    # Mapping ensemble thresholds (see services/mapping.py).
    #
    # THERE IS NO STRING-SIMILARITY TIER: nothing maps a row on wording alone (the rulebook's own
    # ``binding.order`` never declared one). What remains is a MEASUREMENT of how nearly a caption
    # is an authored alias, on the coverage-weighted scale ``mapping._alias_similarity`` reports —
    # not a raw rapidfuzz ratio. Two guards read it and neither can map anything: whether the
    # deterministic evidence dissents from the model's choice, and whether a caption is the printed
    # name of a concept the framework COMPUTES (which must then not be re-homed onto a neighbour).
    #
    # 0.55 is measured, not guessed, and was measured while it was still an accept bar: swept
    # against a real 270-page filing with the template's own subtotals as the oracle, 0.70 → 0.55
    # changed not one mapping and every rollup kept tying, while 0.48 broke three subtotals and 0.40
    # broke five — and the extra mappings those bought were all wrong ("Loss on disposal of
    # investment properties" → ADDITIONS to investment properties). That sweep is why the tier is
    # gone rather than retuned: a looser bar never rescued an unmapped line, it stole a
    # correctly-routed one and asserted something false about it.
    evidence_floor: float = 0.55
    # …and the caption must also explain this much of the alias it is being compared to, so a
    # heading merely contained in a longer concept name is not read as that concept.
    alias_coverage_floor: float = 0.45
    mapping_margin: float = 0.08      # winner must beat runner-up by this margin
    # Confidence + reconciliation.
    auto_accept_confidence: float = 0.80
    recon_abs_tolerance: float = 1.0
    recon_rel_tolerance: float = 0.005
    # How close a note total must come to the face figure before we accept that the note really
    # is a BREAKDOWN of it. Beyond this the note is graded "unconfirmed" rather than asserted as
    # a mismatch — most cited notes are analyses or segment tables, not decompositions. Raising
    # it turns more near-misses into review items; lowering it reports fewer.
    recon_corroboration_rel: float = 0.05
    # Mapping strategy. When an LLM provider is configured, mapping is DESCRIPTION-BASED:
    # the model chooses the canonical concept by meaning (using each candidate's
    # description), not string similarity. The lexical/fuzzy tiers only pre-shortlist
    # candidates. Set false to force the deterministic ensemble even with an LLM present.
    llm_mapping: bool = True
    # RAISED 40 -> 60 when the parts of a line became recognisable concepts. There are 77 of them
    # and they sort at `match_priority` 80 against a median of 81, so at the old bound they took
    # 7-8 of the offered slots on a face chunk and displaced that statement's own concepts.
    #
    # THE BOUND EXISTS TO FIT A PROVIDER LIMIT, and the limit that forced a low value is gone. The
    # Groq free tier caps at 8,000 tokens per minute and cannot serve this pipeline at ANY cap (one
    # request is 30,000-40,000); the Gemini endpoint accepted a 42,736-token request in 7.2s. So
    # this is now a quality setting rather than a feasibility one. Measured at 60: a balance-sheet
    # chunk offers roughly 44 template concepts alongside 16 parts, instead of 8 and 8.
    llm_candidate_cap: int = 60   # max candidate concepts shown to the LLM per line
    # How many candidate concepts a REVIEWED row carries, and how many of them are named to the
    # model as the deterministic tiers' own reading.
    #
    # THESE WERE HARDCODED SLICES (`ranked[:5]`, `ranked[:4]`, `primary[:5]`,
    # `candidates[:3]`) and the reason given for leaving them in code was measured and correct at
    # the time: with no string-similarity tier the deterministic pool held at most ONE candidate,
    # so every slice was the whole list and exposing them would have advertised four knobs that
    # could not change an answer. `test_threshold_homes.py` pinned exactly that, and said what to
    # do when it stopped being true — "it starts failing the day a tier is added that can propose
    # a third candidate, at which point the slice becomes a real decision and needs a real home".
    #
    # THAT DAY ARRIVED when `confusable_with` was authored on output_csv_hk (40 mutual pairs over
    # the measured equal-priority collisions, including a 5-concept `is_oci__*` clique). A refused
    # confusable tie returns every tied concept as a candidate, so the pool now reaches 8 and the
    # slices truncate: `review_candidate_cap` decides what a reviewer sees, and
    # `llm_deterministic_candidate_cap` decides how much of the deterministic reading the model is
    # shown — at 3 of 8 it was dropping five of the very concepts the tie is about, on the one
    # call that could have resolved it.
    #
    # Defaults are the literals they replace, so shipped behaviour is unchanged.
    review_candidate_cap: int = 5
    llm_deterministic_candidate_cap: int = 3
    # The other two REQUEST-PAYLOAD caps, declared next to the candidate cap because they are the
    # same species: how much of the rulebook fits in one call, which is a context/cost budget the
    # deployment pays and not a number the vocabulary calibrated. Each caps HOW MANY; the rulebook's
    # own authoring order decides WHICH survive, so lowering either drops authored content off the
    # tail rather than choosing better content. Config-file only, like the cap above — a provider
    # budget is a deployment fact, not an operator's risk appetite, so neither is an admin knob
    # (see services.settings_state.EXTRACTION_KNOBS).
    #
    # Worked examples ride in the SYSTEM message, so every call of a run pays for them. The cap
    # bites on exactly one shipped rulebook: hkfrs_hk_china authors 8 (the two this drops are 1,023
    # characters, 16% of that ontology's 6,389-character system prompt) and output_csv_hk authors
    # none. So 6 is not the authored length of anything — raising it to 8 changes what one rulebook
    # tells the model, which is why the default is the literal it replaces and not len(examples).
    llm_worked_examples_cap: int = 6
    # Example aliases shown per candidate concept, i.e. paid once per candidate per call. Measured
    # on a balance-sheet payload at the shipped candidate cap: 4 truncates 4 of the 40 candidates'
    # alias lists and the aliases are 1,774 of the payload's 37,781 characters (5%) — against a
    # vocabulary whose median concept has 3 aliases and whose longest has 23. It therefore bounds
    # the long tail and leaves the typical concept's list intact.
    llm_example_aliases_cap: int = 4
    # DOCUMENT CONTEXT per source item (services.note_context). Notes and face rows are capped
    # separately on purpose: one cap over both lets a filing with forty notes crowd out the face
    # rows, and the face rows are what carry the whole-vs-component evidence the `role` decision
    # needs. `llm_context_char_budget` is the backstop — it cuts the already-ranked list, so what
    # it drops is always the least relevant unit and never whatever happened to be last.
    llm_context_notes_cap: int = 3
    llm_context_face_cap: int = 3
    llm_context_char_budget: int = 1200
    # Below this similarity a unit is not offered at all. A row whose subject appears nowhere else
    # in the filing should carry NO context rather than the three least-unrelated notes — padding
    # the request with material about other subjects is worse than sending none.
    #
    # THE NUMBER IS MEASURED. The score is an IDF-weighted cosine in 0..1, so it means the same
    # thing on a three-note extract as on a forty-note filing (verified in
    # `test_note_context_ranking`: 0.711 vs 0.697 for one probe against one note). Measured over ten
    # note subjects scored against the shipped rulebook's own criteria for the concept each note is
    # about, the CORRECT note ranked first 7 times out of 7 and scored 0.451 at worst / 0.605 at the
    # median, while unrelated pairs scored exactly 0.000 in 43 of 63 cases, 0.109 at the 90th
    # percentile and 0.448 at the very worst — so the two distributions do not overlap at all. 0.22
    # sits in that gap, near the noise end so a weak-but-real subject match is still offered. Raise
    # it to require strong matches; lower it to accept more, bounded by the caps above either way.
    llm_context_min_score: float = 0.22
    # A word appearing in at least this FRACTION of the rulebook's concepts is dropped from the
    # probe — the mirror of the pool's IDF, applied to the configuration side. It matters because
    # the shipped criteria are machine generated: all 462 definitions share one template sentence,
    # and without this filter the probe for a cash-flow translation adjustment selected the
    # trade-receivables note at 0.381 on `amounts`, `from` and `other`, every one of them from the
    # template and none from the concept. Concept frequency has a sharp cliff — 25 tokens in
    # 76%-99.6% of concepts (the sentence), then `balance` at 47% — so anything from 0.5 to 0.7
    # isolates exactly the boilerplate. 0 disables the filter.
    llm_context_criteria_boilerplate_fraction: float = 0.6
    # Restrict LLM disambiguation to these canonical_keys only; every other row is decided by the
    # deterministic ensemble (rule/alias tiers), never sent to the model. Empty = no restriction
    # (the default: LLM considered for any row the ensemble can't otherwise resolve).
    llm_only_keys: list[str] = Field(default_factory=list)
    # TEMPORARY (focus-run routing) — remove with the block it drives in stages/map_line_items.py.
    #
    # Restrict the LLM to the ROWS that could be one of these concepts, instead of restricting the
    # CANDIDATE LIST the way ``llm_only_keys`` does. That distinction is the whole point:
    # ``llm_only_keys`` leaves every other row in the request with a candidate list that cannot
    # contain its answer (so the model force-fits it onto one of the listed keys) and drops the
    # deterministic exact/alias answer those rows would otherwise have had. This instead decides
    # each row deterministically FIRST and only forwards the ones that are plausibly in focus —
    # with their candidate list untouched, so a forwarded row is still judged against the full
    # statement/section scope. Empty (the default) = no routing, i.e. today's behaviour exactly.
    llm_focus_keys: list[str] = Field(default_factory=list)
    # …and the switch that turns the above on, separated from it because the two answer different
    # questions and belong to different people. WHICH concepts are in focus is a deployment
    # decision (a list, so it cannot travel through the admin Settings patch, which carries only
    # float/bool/str); WHETHER to restrict this run to them is an operational one an admin flips
    # from the Settings screen.
    #
    # THE FALSE BELOW IS NOT WHAT SHIPS. config.toml sets `llm_focus_only = true` next to the 8
    # `llm_focus_keys` (config.toml:152-183), and config.toml is read for every `Settings()`
    # (`settings_customise_sources`, from the absolute `_CONFIG_TOML` path), so a fresh clone — and
    # the test suite — runs focus-routed unless something overrides it. It is on there so that it
    # TRAVELS: the switch an admin flips is stored in `setting_overrides` in the database, the
    # database is deliberately not in git, so a second machine used to start with routing off and
    # spend the whole provider budget on its first run. This built-in False is therefore the value
    # for a deployment with NO config.toml, not the value the product ships.
    #
    # It was previously argued here that keeping the switch off by default is what lets the key
    # list be checked in — "present but inert, so it cannot silently reconfigure the test suite".
    # Shipping the switch ON falsified that inference. The one test that leaned on the old default
    # now pins the precondition itself (tests/test_binding_order.py:501-506), which is where a
    # test's precondition belongs: a test about UNFOCUSED routing should say so rather than
    # inherit whichever way a deployment happens to point.
    llm_focus_only: bool = False

    # Refuse to ask the model about a note row whose caption is PROSE rather than a line-item name.
    #
    # map_line_items' per-line pass runs over every LINE row of every extracted note, and on a real
    # filing that means sentences: measured on a 367-page HKEX filing, 587 of 627 note rows reached
    # no concept and the longest run to 419 characters ("HK$237,892,000 and HK$222,784,000,
    # respectively, mainly represented sales proceeds rec…"). Each becomes its own paid call asking
    # which balance-sheet concept a sentence fragment is, and two such calls failed on truncated
    # JSON because the reply could not fit.
    #
    # ON BY DEFAULT, at the user's instruction, having been shown what it costs. Measured across
    # the two reference filings:
    #
    #   Chinese 210pp — 15 of 869 note rows skipped (2%), NO mapped row lost; all 15 are prose.
    #   English 367pp — 57 of 627 skipped (9%), 54 of them rows that reached no concept anyway,
    #                   and THREE that map today and no longer will.
    #
    # Two of those three are wrong today and worth losing: bs_nca__land bound to "Pursuant to the
    # PRC Corporate Income Tax Law, a 10% withholding tax is levied…" and to "withholding tax rate
    # may be applied if there is a tax treaty…", i.e. withholding-tax paragraphs mapped to LAND.
    #
    # THE THIRD IS THE PRICE, and it is recorded here so it is not rediscovered as a mystery:
    # bs_nca__goodwill bound to "Goodwill of HK$229,119,000 (2024: HK$215,950,000) arising from the
    # acquisition…" is plausibly CORRECT, and with this on, that row reaches no concept. It is
    # reported in the run log (map_line_items:prose_captions_skipped names the rows and the reason),
    # so the loss is visible rather than silent. Set to false to restore it.
    skip_prose_captions: bool = True
    # ── REMOVED: THE TWO-PATH PRECEDENCE KNOBS ──────────────────────────────────────────────────
    #
    # Four settings stood here — `complex_path_enabled`, `complex_path_services`,
    # `computed_path_precedence` ("complex"/"generic"/"corroborate") and the per-key override
    # `computed_path_by_key`. They arbitrated between TWO ways of reaching the same eight
    # concepts: the rulebook's own reading of a printed caption, and five hand-written
    # derivations (deprec_impairment ~208 enumerated entries, contingent_liabilities ~172,
    # secur_fincl_assets ~91, related_party_receivables ~73, sales_revenues ~18 — ~562 note
    # titles, row captions and formula variants in all) that assembled a figure from note-level
    # datasets through a priority cascade. `services/computed_paths.py` (269 lines) was the single
    # place that arbitration was applied; these four switched it.
    #
    # The derivations are gone, and computed_paths.py with them. There is now ONE path to a
    # figure — the configuration: an alias names the printed caption, the section banner scopes
    # it, the template's rollups check it. With one path there is no precedence to set, nothing to
    # gate per service, and nothing to corroborate, so all four settings are removed rather than
    # left inert. Any concept those derivations used to compute must now be reached by describing
    # its caption in the rulebook; until it is, the cell is blank by design.

    # Publish only notes a face row cites (see stages/prune_notes.py). False publishes every
    # extracted note table regardless of whether any face figure references it.
    prune_unreferenced_notes: bool = True
    # Concurrent LLM batch calls during map_line_items' per-statement pass (see stages/map_line_items
    # + services.mapping._match_chunk). Each chunk is an independent provider call; running several
    # in parallel is what turns a run's LLM time from "sum of every call" into "the slowest one",
    # bounded so a large filing does not open dozens of connections to the gateway at once.
    llm_max_concurrency: int = 6
    # ── ONE BATCH CALL'S SIZE: ITS RESPONSE ALLOCATION *AND* ITS VOCABULARY ─────────────────────
    #
    # How many captions travel in one structured request. What decides the number from the
    # gateway's side is how many decisions a given model returns as parseable JSON in one go — a
    # truncated batch is not a partial answer, the JSON fails to parse and the whole chunk silently
    # falls back to the weaker per-line path, on a run that still reports itself LLM-mapped.
    #
    # BUT IT IS NOT A PURE TRANSPORT NUMBER, and the comment that used to stand here said it was
    # ("the chunk bounds one RESPONSE's size and nothing about the vocabulary"). That is false.
    # `services.mapping._match_chunk` derives the section tokens from the rows IN THIS CHUNK and
    # narrows the offered candidate list by them, and it seeds the never-evicted keys from this
    # chunk's own rows before `llm_candidate_cap` bounds the fill. So the chunk boundary decides
    # WHICH concepts the model may choose from on each call: a smaller chunk carries fewer banners,
    # which is a narrower vocabulary — and it is also where cross-line context (a subtotal and the
    # lines it is made of, a section and its residual) is cut.
    #
    # Lowering this is therefore a mapping change, not a plumbing change. Re-baseline the
    # deployment that lowers it rather than assuming the same answers at a smaller size.
    llm_batch_max_items: int = 25
    # HOW LINE ITEMS ARE GROUPED INTO REQUESTS, when requests are driven by the line item rather
    # than by the printed row.
    #
    #   "none"      one request per line item. The baseline, and the only mode whose answers are
    #               attributable to a single line: nothing else shares the call, so nothing else can
    #               have influenced the answer.
    #   "identical" line items sharing the EXACT SAME selected note set share one request. Safe by
    #               construction — no line item ever receives a note it did not ask for, which is
    #               how a wrong answer acquires a plausible-looking source.
    #   "similar"   line items whose note sets OVERLAP enough share one request
    #               (`llm_group_similarity`). Bigger savings, and the trade is real: some line items
    #               then see notes they did not select.
    #
    # WHY GROUPING SAVES ANYTHING AT ALL. The note context is what a request pays for — measured,
    # `identified_notes` was 22,597 of a 30,407-token request, 74% of it. So line items needing the
    # same notes amortise one copy of that block instead of paying for it each. Measured on the 12
    # depreciation parts that all resolve to one note: 12 separate calls carry ~125,000 characters,
    # one batch of 12 carries ~22,000 — about 5.7x.
    #
    # DEFAULT "none", DELIBERATELY. Grouping has a real hazard beyond cost: line items sharing a
    # call can influence each other's answers, which sometimes helps (a subtotal and its components
    # seen together) and sometimes bleeds (one figure reused for two questions). Without the
    # per-line-item baseline there is no way to tell which happened, so the cheap mode is not the
    # default until there is something to compare it against.
    #   "manual"    the groups the line-item set declares in `request_groups`, and one
    #               request each for every asked-about line the master does not name. For
    #               the cases a score cannot see: a subtotal better judged beside its
    #               components, or two lines whose DISTINCTION is what the model keeps
    #               getting wrong. Read by `services.line_item_requests.plan_requests`.
    llm_request_grouping: Literal["none", "identical", "similar", "manual"] = "none"
    # The overlap two note sets need to share a request under "similar", as a Jaccard index. 0.8
    # means they must be nearly the same set; 1.0 is "identical" by another name. Read only in
    # "similar" mode.
    llm_group_similarity: float = 0.8
    # THERE IS NO REGISTRY SWITCH HERE ANY MORE. `mapping_engine` used to stand at this spot,
    # a Literal["ontology", "line_items"] defaulting to "ontology", and it selected between two
    # registries: the ontology rulebook and the merged line-item configuration. The line-item set
    # is now the SINGLE configuration engine — there is no second registry to point at, so there
    # is nothing to select and the setting is gone rather than pinned. Do not reinstate it: a
    # switch is what let the merged configuration ship inert, with what a user configured on the
    # Line Items screen affecting nothing.

    # Floor under a batch call's requested completion allocation. services.mapping also DERIVES a
    # budget from the response envelope (a reserve plus ~80 tokens a decision); that derivation
    # stays in code because it is measured against THIS vocabulary's longest canonical_key, and the
    # floor is the half that answers to the gateway instead. At the shipped chunk size the floor is
    # in fact the only number in play — derived(25) = 2,256 tokens, and the floor wins for every
    # chunk up to 99 items (crossover at 100) — so this is the batch response budget in practice.
    # It exists because sending `llm.max_tokens` (a request ceiling shared with every other call in
    # the app) makes compatible gateways reserve millions of tokens for a small structured reply and
    # time out before answering. How much headroom a reply needs is a fact about the gateway.
    # Bounded here and not in `services.settings_state`: this is not an EXTRACTION_KNOBS entry, so
    # `set_extraction_config`'s min/max refusal never sees it and a negative value would be sent
    # straight to the provider as the requested allocation. 0 is admitted deliberately — it means
    # "no floor, use the envelope derivation" (see services.mapping._effective_batch_max_tokens).
    llm_batch_response_floor_tokens: int = Field(8192, ge=0)
    # …and the same allocation for the per-line call, which answers with one decision. Measured:
    # an `LlmMappingDecision` carrying the rulebook's longest canonical_key and a 200-char reason
    # serialises to 371 characters ≈ 124 tokens, so 512 is roughly 4× headroom on the envelope. It
    # is a deployment number for the reason `llm.reasoning_max_tokens` documents: a model whose
    # reasoning cannot be disabled spends the completion budget thinking and returns empty content
    # with finish_reason=length, and how much budget that takes is a property of the model.
    # `ge=1` for the reason the floor above is bounded — no EXTRACTION_KNOBS entry, so nothing else
    # refuses a range — and 1 rather than 0 because this is the whole allocation and not a floor:
    # 0 asks the provider for a zero-token completion, which is not a configuration anyone wants.
    llm_line_max_tokens: int = Field(512, ge=1)
    # Mapping granularity. "per_statement" (default, most accurate) batches lines into ONE LLM
    # call so cross-line judgements — parent/child containment, residualisation, "Others"
    # handling — have context. The batch is one SOURCE PAGE in practice, so a statement spanning
    # two pages is decided in two calls; the name states the intent rather than the unit (see
    # services.mapping.match_batch). "per_line" maps each line independently (cheaper, less
    # context-aware).
    mapping_scope: Literal["per_statement", "per_line"] = "per_statement"
    # `llm_note_structuring` STOOD HERE AND WAS DELETED. It declared that "multi-column notes are
    # structured by the configured LLM before they can enter ontology mapping", it shipped `true`
    # in config.toml, and it had ZERO readers — no stage among the 21 in app/stages, no service, no
    # test. The capability it advertised lived in app/services/note_structure_llm.py, which had no
    # importer either and was deleted with it. Worse than dead, it CONTRADICTED the code it named:
    # `services.notes_extract.extract_note_tables` documents that note tables "are reconstructed
    # exclusively from positioned source tokens" and that its `llm_provider`/`ai_required`
    # parameters survive only for caller compatibility.
    #
    # Deleted rather than wired, because wiring it would have shipped two defects: the module's
    # `structure_note_matrix` set `period_label`/`period_display` to CATEGORY headers ("Buildings",
    # "Total") instead of periods — a different semantic from what link_notes and reconcile read —
    # and its `provider.complete_structured` call had no try/except, so it could not honour this
    # declaration's own promise that a failed call "drops the unsupported matrix".
    #
    # Gap closing. When a section subtotal computed from the template's lines differs from the
    # one the document printed, offer the model the extracted lines that reached no statement and
    # ask which belong in that section's "Others". Arithmetic bounds the choice — an option must
    # close the difference in BOTH periods — but the placement is the model's judgement. Off, or
    # with no provider configured, the difference stays a review item instead.
    llm_gap_routing: bool = True
    # Contingent Liabilities' narrative. The deterministic pass in services.contingent_liabilities
    # always classifies every item and computes every total from the filing's own figures — the
    # model never sees a number it was not given and never changes one. Enabled, it only rewrites
    # the summary paragraph and each unclassified item's short statement in clearer English; off,
    # or with no provider configured, the deterministic prose is what is shown.
    llm_contingent_liabilities: bool = True


def _unbound_toml_keys(values: Mapping[str, Any], model: type[BaseModel]) -> list[str]:
    """Dotted paths of every ``config.toml`` key that binds to no field on ``model``.

    Recurses into a key whose field is itself a ``BaseModel`` (``[auth]``, ``[llm]``, …), so a
    misspelt key INSIDE a real table is reported as ``llm.temprature`` rather than the table
    being reported as fine. Names are compared lowercased because pydantic-settings matches
    fields case-insensitively by default, and a report must not accuse a key that does bind.

    Pure and separate from the logging so it can be asserted on directly.
    """
    fields = model.model_fields
    lowered = {name.lower(): field for name, field in fields.items()}
    unbound: list[str] = []
    for key, value in values.items():
        field = lowered.get(str(key).lower())
        if field is None:
            unbound.append(str(key))
            continue
        annotation = field.annotation
        if (isinstance(annotation, type) and issubclass(annotation, BaseModel)
                and isinstance(value, Mapping)):
            unbound.extend(f"{key}.{sub}" for sub in _unbound_toml_keys(value, annotation))
    return unbound


_TOML_KEYS_REPORTED = False


def _report_unbound_toml_keys(
    source: TomlConfigSettingsSource, settings_cls: type[BaseSettings]
) -> None:
    """One WARNING naming every ``config.toml`` key that was loaded and then thrown away.

    WHY THIS EXISTS. ``config.toml``'s own header says "It is read at startup by
    app/config.py", and a reader reasonably assumes that means every key in it. It does not:
    ``Settings`` sets ``extra="ignore"``, so a key that matches no field is discarded in
    silence. That is exactly what happened to the shipped ``[app]`` table — `Settings` has no
    ``app`` field (``app_name`` and ``api_prefix`` are top-level), so
    ``TomlConfigSettingsSource(Settings)()`` handed back an ``app`` key and BOTH values under it
    were dropped. It went unnoticed for as long as it did only because the two shipped values
    equalled the built-in defaults; a deployment setting ``api_prefix = "/api/v2"`` got a server
    still mounted at /api/v1 and no indication why. Both keys are now bare top-level keys and
    bind — and this reports the next one that does not.

    ``extra="forbid"`` would be the tempting fix and is the wrong one: ``extra`` governs the
    whole ``Settings`` model, which is fed by the environment and ``.env`` as well as this file,
    so any stray ``FINEX_*`` variable in a deployment's shell would become a hard startup crash.
    A WARNING is the right severity for "you wrote a knob that does nothing" — and WARNING is
    also the floor that actually reaches stderr, since this app configures no logging and only
    WARNING and above gets through ``logging.lastResort``.

    Called from ``settings_customise_sources`` (rather than from ``get_settings``) so it fires
    for any ``Settings()`` — scripts and tests construct one directly — but guarded to fire ONCE
    per process, because ``Settings()`` is constructed many times over a run and the file cannot
    change underneath it.
    """
    global _TOML_KEYS_REPORTED
    if _TOML_KEYS_REPORTED:
        return
    _TOML_KEYS_REPORTED = True
    try:
        values = source()
    except Exception as exc:  # noqa: BLE001 - a bad/missing config.toml is the loader's error to raise
        _LOG.warning("config.toml could not be re-read to check for unbound keys: %s", exc)
        return
    if not isinstance(values, Mapping):
        return
    unbound = sorted(_unbound_toml_keys(values, settings_cls))
    if unbound:
        # Name the file the SOURCE read, not `_CONFIG_TOML`: a subclass or a test can point
        # `toml_file` elsewhere, and a warning that names the wrong file sends the reader to a
        # file whose keys are all fine.
        _LOG.warning(
            "%s: %d key(s) bind to no setting and are IGNORED: %s. Check the name and the "
            "nesting against Settings in app/config.py — a whole table listed here means the "
            "table itself is not a settings group, and every key under it was dropped.",
            getattr(source, "toml_file_path", _CONFIG_TOML), len(unbound), ", ".join(unbound))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINEX_",
        env_file=".env",
        env_nested_delimiter="__",
        toml_file=str(_CONFIG_TOML),
        extra="ignore",
    )

    app_name: str = "FinExtract Extraction API"
    api_prefix: str = "/api/v1"

    # Persistence — SQLite by default; point at Postgres in prod (portable via SQLAlchemy).
    database_url: str = "sqlite:///./finex.db"

    # Object storage — local filesystem by default (portable via the ObjectStore port).
    object_store_backend: str = "local"  # local | s3 | minio
    object_store_root: Path = Path("./_object_store")

    # Grouped, file-driven configuration (sections in config.toml).
    auth: AuthSettings = AuthSettings()
    features: FeatureSettings = FeatureSettings()
    llm: LlmSettings = LlmSettings()
    ocr: OcrSettings = OcrSettings()
    extraction: ExtractionSettings = ExtractionSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence (first wins): init args > env > .env > config.toml > file secrets.
        toml_settings = TomlConfigSettingsSource(settings_cls)
        # …and say so when a key in that file binds to nothing. See _report_unbound_toml_keys:
        # extra="ignore" drops an unknown key without a word, which is how the shipped `[app]`
        # table was read and discarded on every boot.
        _report_unbound_toml_keys(toml_settings, settings_cls)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            toml_settings,
            file_secret_settings,
        )


def _export_provider_keys(settings: Settings) -> None:
    """Publish the API keys named by ``*_api_key_env`` from ``.env`` into ``os.environ``.

    WHY THIS IS NEEDED AND WAS NOT OBVIOUS. Every adapter reads its key at CALL time with
    ``os.environ.get(cfg.api_key_env)`` — ``anthropic_llm.py:48``, ``azure_doc_intelligence.py:92``
    — deliberately, so the key is never held in a settings object, never persisted, and never
    reaches the settings API that the admin UI reads. But pydantic-settings loads ``.env`` into the
    MODEL, not into the process environment, so a key placed in ``.env`` was read by nothing:
    ``"OPENROUTER_API_KEY" in os.environ`` stayed False both before and after ``get_settings()``.
    The only thing that ever worked was an ad-hoc ``export`` in the shell that launched uvicorn —
    which is invisible, unshared, and silently lost on the next restart, taking LLM extraction with
    it while every other route kept answering.

    So: the VALUE still only ever lives in the environment, and ``.env`` becomes a place to put it
    that survives a restart. ``.env`` is gitignored (``.gitignore:35``), and this reads only the
    variables the configuration actually names as key-holders — not everything in the file.

    A REAL ENVIRONMENT VARIABLE ALWAYS WINS. Someone who exported a key for one run must not have
    it overridden by a stale line in a file, which is the same precedence ``settings_customise_sources``
    already gives env over .env.
    """
    named = {settings.llm.api_key_env, settings.ocr.azure_api_key_env}
    from_dotenv = DotEnvSettingsSource(Settings, env_file=".env", env_prefix="")
    try:
        values = from_dotenv()
    except Exception:                      # a missing or unreadable .env is not an error here
        return
    # DotEnvSettingsSource lowercases keys for field matching, so compare case-insensitively.
    lowered = {str(k).lower(): v for k, v in values.items()}
    for var in sorted(n for n in named if n):
        if os.environ.get(var):
            continue                       # an exported key wins over the file
        found = lowered.get(var.lower())
        if isinstance(found, str) and found.strip():
            os.environ[var] = found.strip()


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    _export_provider_keys(settings)
    return settings
