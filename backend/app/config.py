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

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

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
    temperature: float = 0.0
    max_tokens: int = 4096000
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

    # Native-vs-scanned page detection (see stages/ingest.py).
    native_min_chars: int = 100
    native_min_text_coverage: float = 0.02
    low_dpi_threshold: int = 150
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
    llm_candidate_cap: int = 40   # max candidate concepts shown to the LLM per line
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
    # Restrict LLM disambiguation to these canonical_keys only; every other row is decided by the
    # deterministic ensemble (rule/alias tiers), never sent to the model. Empty = no restriction
    # (the default: LLM considered for any row the ensemble can't otherwise resolve).
    llm_only_keys: list[str] = Field(default_factory=list)
    # TEMPORARY (focus-run routing) — remove with the block it drives in stages/map_ontology.py.
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
    # from the Settings screen. Keeping the switch OFF by default is also what lets the key list
    # be checked in: present but inert, so it cannot silently reconfigure the test suite the way an
    # always-on list did.
    llm_focus_only: bool = False

    # Refuse to ask the model about a note row whose caption is PROSE rather than a line-item name.
    #
    # map_ontology's per-line pass runs over every LINE row of every extracted note, and on a real
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
    # reported in the run log (map_ontology:prose_captions_skipped names the rows and the reason),
    # so the loss is visible rather than silent. Set to false to restore it.
    skip_prose_captions: bool = True
    # ── TWO PATHS TO A FIGURE ────────────────────────────────────────────────────────────────
    #
    # Eight concepts can be reached either by the GENERIC path (the rulebook: an alias names the
    # caption, the banner scopes it, the template's rollups check it) or by the COMPLEX path
    # (five hand-written derivations under services/, each implementing a spec in docs/ that
    # assembles the figure from note-level datasets through a priority cascade).
    #
    # They differ in where they are strong. The complex path is more capable on the filings its
    # spec was written from and holds the audited derivations; its reach is bounded by CLOSED
    # caption enumerations — 162 alternatives in deprec_impairment, 145 in
    # contingent_liabilities — so a filing spelling a caption differently gets a refusal, not a
    # wrong number. Measured on two filings from outside the reference set: 2 of 8 and 3 of 8
    # figures produced, revenue blank on both. The generic path resolves ~58% of printed captions
    # on any English filing, so it is broader and shallower.
    #
    # Until now nothing chose. Each derivation ran after mapping and wrote unconditionally, so the
    # complex path always won and the generic reading was discarded with no record. That default
    # is kept — `complex` — but it is now a decision that can be seen, changed and audited.
    # See services/computed_paths.py, which is the single place it is applied.
    #
    # Runs the five derivations at all. False measures what the rulebook alone can do, which is
    # the only honest way to see the generic path's real coverage.
    complex_path_enabled: bool = True
    # Restrict the complex path to these services by name (deprec_impairment,
    # secur_fincl_assets, related_party_receivables, sales_revenues, contingent_liabilities).
    # Empty = all of them, which is the default.
    complex_path_services: list[str] = Field(default_factory=list)
    # Who publishes when BOTH paths produce a figure for the same (concept, basis, period):
    #   "complex"     — the derivation publishes; what it displaced is recorded (today's behaviour)
    #   "generic"     — the printed reading publishes; the derivation is recorded as corroboration
    #   "corroborate" — the printed reading publishes where it has one, the derivation fills gaps,
    #                   and a disagreement beyond `recon_rel_tolerance` is flagged for review
    # A derivation always fills a gap the generic path left empty, under every setting: that is
    # the case the old code could not express, because it never knew a second path had answered.
    computed_path_precedence: Literal["complex", "generic", "corroborate"] = "complex"
    # Per-concept override of the above, keyed by canonical_key. A mapping, so it lives in
    # config.toml and cannot travel through the admin settings patch (which carries only
    # float/bool/str) — the same split `llm_focus_keys`/`llm_focus_only` uses, for the same
    # reason: WHICH concepts is a deployment decision, WHETHER is an operational one.
    computed_path_by_key: dict[str, str] = Field(default_factory=dict)

    # Publish only notes a face row cites (see stages/prune_notes.py). False publishes every
    # extracted note table regardless of whether any face figure references it.
    prune_unreferenced_notes: bool = True
    # Concurrent LLM batch calls during map_ontology's per-statement pass (see stages/map_ontology
    # + services.mapping._match_chunk). Each chunk is an independent provider call; running several
    # in parallel is what turns a run's LLM time from "sum of every call" into "the slowest one",
    # bounded so a large filing does not open dozens of connections to the gateway at once.
    llm_max_concurrency: int = 6
    # ── ONE BATCH CALL'S TRANSPORT SIZE AND RESPONSE ALLOCATION ─────────────────────────────────
    #
    # How many captions travel in one structured request. services.mapping calls it "a transport
    # chunk, not a semantic boundary" and that is the whole reason it is a deployment number: the
    # section results are carried into the statement pass either way, so the chunk bounds one
    # RESPONSE's size and nothing about the vocabulary. What decides it is how many decisions a
    # given model returns as parseable JSON in one go — a truncated batch is not a partial answer,
    # the JSON fails to parse and the whole chunk silently falls back to the weaker per-line path.
    llm_batch_max_items: int = 25
    # WHICH REGISTRY DECIDES WHERE A CAPTION LANDS. A FALLBACK, not a migration switch.
    #
    #   "ontology"     the incumbent. `services.mapping.OntologyMatcher` over the rulebook, with
    #                  every tier including the LLM one. This is where the value is and it stays
    #                  the default.
    #   "line_items"   the merged configuration, `services.line_item_matching.LineItemMatcher`,
    #                  DETERMINISTIC ONLY. The semantic tier is not ported: it consumes the same
    #                  per-item payload (definition, include/exclude criteria, confusable_with),
    #                  which the merged model carries in full, but pointing it at this registry is
    #                  separate work.
    #
    # SO THIS IS A WEAKER PATH BY CONSTRUCTION, and it is here because a deterministic fallback
    # that needs no provider is worth having when a gateway is down, a key has expired, or a run
    # has to be reproducible with nothing leaving the machine. It is not a route to better
    # extraction: `scripts/parity_line_items.py` shows the two agree on 11,433 of 11,433 rulebook
    # captions, and that says nothing whatever about the LLM tier this path lacks.
    #
    # Making this path good on a particular filing by adding captions it happens to print would be
    # the exact overfitting the generic framework exists to avoid. Widen the vocabulary, or accept
    # that the fallback maps less.
    mapping_engine: Literal["ontology", "line_items"] = "ontology"
    # Floor under a batch call's requested completion allocation. services.mapping also DERIVES a
    # budget from the response envelope (a reserve plus ~80 tokens a decision); that derivation
    # stays in code because it is measured against THIS vocabulary's longest canonical_key, and the
    # floor is the half that answers to the gateway instead. At the shipped chunk size the floor is
    # in fact the only number in play — derived(25) = 2,256 tokens, and the floor wins for every
    # chunk up to 99 items (crossover at 100) — so this is the batch response budget in practice.
    # It exists because sending `llm.max_tokens` (a request ceiling shared with every other call in
    # the app) makes compatible gateways reserve millions of tokens for a small structured reply and
    # time out before answering. How much headroom a reply needs is a fact about the gateway.
    llm_batch_response_floor_tokens: int = 8192
    # …and the same allocation for the per-line call, which answers with one decision. Measured:
    # an `LlmMappingDecision` carrying the rulebook's longest canonical_key and a 200-char reason
    # serialises to 371 characters ≈ 124 tokens, so 512 is roughly 4× headroom on the envelope. It
    # is a deployment number for the reason `llm.reasoning_max_tokens` documents: a model whose
    # reasoning cannot be disabled spends the completion budget thinking and returns empty content
    # with finish_reason=length, and how much budget that takes is a property of the model.
    llm_line_max_tokens: int = 512
    # Mapping granularity. "per_statement" (default, most accurate) batches lines into ONE LLM
    # call so cross-line judgements — parent/child containment, residualisation, "Others"
    # handling — have context. The batch is one SOURCE PAGE in practice, so a statement spanning
    # two pages is decided in two calls; the name states the intent rather than the unit (see
    # services.mapping.match_batch). "per_line" maps each line independently (cheaper, less
    # context-aware).
    mapping_scope: Literal["per_statement", "per_line"] = "per_statement"
    # Multi-column notes are structured by the configured LLM before they can enter ontology
    # mapping. A failed or unavailable model call drops the unsupported matrix rather than
    # publishing fragmented rows.
    llm_note_structuring: bool = False
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
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
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
