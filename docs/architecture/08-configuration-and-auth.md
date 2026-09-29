# Configuration & authentication

## Configuration file (`backend/config.toml`)

Non-secret, deployment-tunable settings live in `backend/config.toml` and are loaded by
`app/config.py` via pydantic-settings. Layering, highest precedence first:

1. **Environment variables** — prefix `FINEX_`, nested keys use `__`
   (e.g. `FINEX_FEATURES__UI_LOCALIZATION=true`).
2. **`.env`** file.
3. **`config.toml`** — the human-editable file (checked into git).
4. Built-in defaults in `app/config.py`.

**The LLM is the exception: `config.toml [llm]` is the only place it is defined.** Layers 1
and 2 are not read for the `[llm]` table (a `FINEX_LLM__*` variable is ignored and named in a
startup WARNING), and nothing at run time — no Settings screen, no stored override — can
change it. Only the API key lives outside the file, in the environment (or `.env`) under the
name `[llm].api_key_env` gives. Tests and offline scripts pin a provider in code with
`app.config.pin_llm` (the suite pins `stub` in `tests/conftest.py`).

`settings_customise_sources` inserts a `TomlConfigSettingsSource` between dotenv and the
defaults, so env always wins over the file. Most settings are grouped into nested models, one
model per table; a few sit at the top level and take no table at all:

| Section | Keys |
|---|---|
| *(no table — bare top-level keys, before the first `[section]`)* | `app_name`, `api_prefix`, `database_url`, `object_store_backend`, `object_store_root` |
| `[auth]` | `allow_role_header`, `demo_mode`, `session_ttl_minutes` |
| `[features]` | `ui_localization`, `review_required`, `seed_demo`, `default_output_locale`, `supported_locales` |
| `[llm]` | `provider`, `model`, `max_tokens`, `timeout_seconds`, `base_url`, `api_key_env`, plus the Azure address: `azure_endpoint`, `azure_api_version`, `azure_deployment` |
| `[ocr]` | `engine`, `languages`, `dpi`, plus the Azure Document Intelligence address: `azure_endpoint`, `azure_model`, `azure_api_version`, `azure_api_key_env` |
| `[extraction]` | native/scanned thresholds, the mapping thresholds (`evidence_floor` and `alias_coverage_floor` — how nearly a caption must BE an authored alias for the two guards that read it; `mapping_margin`, `auto_accept_confidence`), reconciliation tolerances (`recon_*`), and the LLM-mapping knobs (`llm_mapping`, `llm_candidate_cap`, `mapping_scope`, `llm_gap_routing`) |

**There is no mapping-engine selector.** `[extraction]` briefly carried a `mapping_engine`
key that chose between an ontology path and a line-items path. Line items is now the single
configuration engine, so there is nothing to select: the key and the branch it fed are gone,
and a `mapping_engine` left in a local `config.toml` binds to nothing and is reported by the
unbound-key warning below. Do not reinstate it — a second mapping path is what made a
configured line item able to affect nothing.

The first row is ungrouped **on purpose**. `app_name` and `api_prefix` are top-level fields on
`Settings`, so they must be written as bare keys; the file used to declare them as
`[app] name` + `[app] api_prefix`, which matched no field and — because `Settings` sets
`extra="ignore"` — was loaded and then discarded in silence. It went unnoticed because both
shipped values equal the built-in defaults, so `api_prefix = "/api/v2"` produced a server still
mounted at `/api/v1` and said nothing. They are not moved under an `AppSettings` submodel
because that would rename the env contract from `FINEX_API_PREFIX` to `FINEX_APP__API_PREFIX`.
Note that changing the prefix also needs a frontend change: `frontend/src/lib/api.ts` hardcodes
`/api/v1`.

**Unbound keys are reported.** `settings_customise_sources` compares the TOML payload's keys
(and each table's keys, against that table's submodel) with `Settings`' fields and logs one
**WARNING** naming every key that binds to nothing — once per process, at startup. `extra` stays
`"ignore"` rather than `"forbid"` deliberately: `extra` governs the whole `Settings` model, which
the environment and `.env` also feed, so forbidding extras would turn any stray `FINEX_*`
variable into a hard startup crash. `app/main.py` also prints the effective `app_name` and
`api_prefix` at startup.

**Secrets are never stored here.** The LLM key is read at call time from the environment
variable named by `llm.api_key_env` (shipped default **`AZURE_OPENAI_API_KEY`**, matching
the shipped `llm.provider = "openai_compatible"` gateway); the config only names the variable. The OCR
key is the same arrangement under `ocr.azure_api_key_env` (default `AZURE_DI_KEY`).
`GET /settings` reports whether the LLM variable is populated (`key_configured`), never its
value.

## Settings API + admin Settings screen

`GET /settings` (any authenticated caller) returns a non-secret snapshot of the config —
so the frontend can surface it and read runtime flags. `PATCH /settings`
(`config:settings`, admin only) changes the runtime-mutable settings, which are **two
groups**:

1. the **feature flags** — `ui_localization`, `review_required`, `seed_demo` (load/clear
   the sample project);
2. the **extraction thresholds** — the mapping ensemble's accept/candidate/margin bars and
   the reconciliation tolerances (`EXTRACTION_KNOBS`). Each knob's bounds, step and
   explanation are served by the API as `extraction_fields`, so the screen renders and
   validates from the backend's own definition instead of a second copy; an out-of-range
   value is a **422 naming the field**, never a silently clamped substitute.
   `extraction_defaults` is what `config.toml` shipped, for "restore defaults".

Overrides live in `app/services/settings_state.py` — and they are **persisted**, not merely
in memory: every change writes a row to `setting_overrides` (one row per setting, so two
admins changing different knobs cannot clobber each other) and `load_persisted()` re-applies
them onto the process at startup (`app/main.py`). The in-memory copies are a read-through
cache of that table. `config.toml` remains the source of the **defaults** — "restore
defaults" means what the file shipped, never the last value that happened to be stored. No
secret is ever written.

The admin **Settings** screen (`frontend/src/screens/Settings.tsx`) renders the whole
snapshot: the read-only parts (OCR, embeddings, access flags, locales) alongside the
editable ones above. Non-admins do not see the screen; the server enforces the
`config:settings` permission regardless.

## Session authentication

`app/security/session.py` provides a self-contained session layer so login/logout works
end-to-end with no external infrastructure:

- **Seeded demo users**, one per role — `admin` (Priya Nair), `reviewer` (Rahul Mehta),
  `analyst` (Ana Ferreira). Password equals the username; in **demo mode** the seeded
  users can log in passwordlessly (the "Sign in as …" quick-login buttons).
- `POST /auth/login` → `{username, password?}` authenticates and returns an opaque
  **bearer token**; the token maps to an in-memory session with a TTL.
- `POST /auth/logout` invalidates the token. `GET /auth/demo-users` lists the seeded
  users (no secrets) for the login screen.
- `current_principal` checks the `Authorization: Bearer …` session **first and treats it
  as authoritative** — a valid session's role can never be overridden by a header. Only
  when there is no valid session does it consider an `X-Role` dev/service header, and even
  then solely when `auth.allow_role_header` is enabled. That flag is **off by default**
  (secure by default: a real session is the only way in); enable it explicitly for local
  dev / CI. It 401s when neither yields a principal. `current_role` derives the role from
  the principal, and `require(permission)` builds on it.

**Production hardening** (documented, out of scope for the demo): keep
`auth.allow_role_header=false` (the default) and set `auth.demo_mode=false`; replace the
in-memory session store with a shared, persistent one (Redis / signed JWT); back the
seeded users with a real user store or an IdP (OIDC/SAML). None of this changes the
permission matrix or the API contract.

## LLM provider selection

`config.toml [llm].provider` chooses the adapter the registry hands out (`app/adapters`):

- **`azure_openai` (the shipped default, registered under `azure_openai` and `azure`)** —
  Azure OpenAI (`azure_openai_llm.py`). Azure addresses a **deployment on your own
  resource** rather than a model on a shared host, so the URL is built from
  `azure_endpoint` + `azure_deployment` (falling back to `model`) + `azure_api_version`, and
  it authenticates with an `api-key` header. The shipped `model` is `gpt-5-mini`; the key
  comes from `AZURE_OPENAI_API_KEY` by default.
- **`anthropic`** — Anthropic Messages API via the official SDK (`anthropic_llm.py`).
- **`openai` / `openai_compatible`** — the OpenAI **Chat Completions** wire format
  (`openai_llm.py`, via `httpx`), so any compatible gateway works — OpenAI, TokenRouter,
  OpenRouter, a self-hosted vLLM — with a `vendor/model` id. Set `base_url` to the gateway
  (ending in `/v1`) and point `api_key_env` at the key's env var.
- **`stub`** — offline / no-op, and the value the pipeline checks for when deciding whether
  the LLM tiers run at all (`stages/map_ontology.py`, the netting and credit-narrative
  passes). `local` is documented in the config comment as offline but **is not registered**,
  so selecting it raises from the registry.

Every real adapter gets structured output the same model-agnostic way (JSON Schema embedded
in the system prompt, Pydantic-validated — shared in `adapters/_structured.py`) and returns
input/output token usage in `LlmMeta` for the audit log. Adapters are registered lazily, so
registration needs neither the SDK nor a key. Keys are read from the environment at call
time, never from `config.toml`.

**Not editable at runtime.** The LLM configuration is read-only in `GET /settings` (for
diagnosis, with `key_configured`) and is not on the Settings screen. `PATCH /settings` with `llm`
or `reset_llm` answers 400 naming `config.toml`. Rows an older release saved under the `llm`
scope of `setting_overrides` are deleted at startup and logged.

## Frontend flow

`frontend/src/lib/api.ts` sends the stored bearer token on every request and exposes
`login`/`logout`/`demoUsers`/`settings`/`patchSettings`. `App.tsx` shows `Login` until a
valid session exists (a 401 from `/me` returns the user to login); once authenticated it
renders the shell and, via a `useSettings()` sync, mirrors `features.ui_localization` into
the UI store so the interface-localization policy takes effect immediately.
