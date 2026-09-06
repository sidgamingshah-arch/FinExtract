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

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
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
    # Publish only notes a face row cites (see stages/prune_notes.py). False publishes every
    # extracted note table regardless of whether any face figure references it.
    prune_unreferenced_notes: bool = True
    # Concurrent LLM batch calls during map_ontology's per-statement pass (see stages/map_ontology
    # + services.mapping._match_chunk). Each chunk is an independent provider call; running several
    # in parallel is what turns a run's LLM time from "sum of every call" into "the slowest one",
    # bounded so a large filing does not open dozens of connections to the gateway at once.
    llm_max_concurrency: int = 6
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
