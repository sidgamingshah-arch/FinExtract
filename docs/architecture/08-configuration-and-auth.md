# Configuration & authentication

## Configuration file (`backend/config.toml`)

Non-secret, deployment-tunable settings live in `backend/config.toml` and are loaded by
`app/config.py` via pydantic-settings. Layering, highest precedence first:

1. **Environment variables** — prefix `FINEX_`, nested keys use `__`
   (e.g. `FINEX_LLM__MODEL`, `FINEX_FEATURES__UI_LOCALIZATION=true`).
2. **`.env`** file.
3. **`config.toml`** — the human-editable file (checked into git).
4. Built-in defaults in `app/config.py`.

`settings_customise_sources` inserts a `TomlConfigSettingsSource` between dotenv and the
defaults, so env always wins over the file. Settings are grouped into nested models:

| Section | Keys |
|---|---|
| `[app]` | `name`, `api_prefix` |
| `[auth]` | `allow_role_header`, `demo_mode`, `session_ttl_minutes` |
| `[features]` | `ui_localization`, `review_required`, `seed_demo`, `default_output_locale`, `supported_locales` |
| `[llm]` | `provider`, `model`, `temperature`, `max_tokens`, `timeout_seconds`, `base_url`, `api_key_env`, plus the Azure address: `azure_endpoint`, `azure_api_version`, `azure_deployment` |
| `[ocr]` | `engine`, `languages`, `dpi`, plus the Azure Document Intelligence address: `azure_endpoint`, `azure_model`, `azure_api_version`, `azure_api_key_env` |
| `[extraction]` | native/scanned thresholds, the mapping thresholds (`evidence_floor` and `alias_coverage_floor` — how nearly a caption must BE an authored alias for the two guards that read it; `mapping_margin`, `auto_accept_confidence`), reconciliation tolerances (`recon_*`), and the LLM-mapping knobs (`llm_mapping`, `llm_candidate_cap`, `mapping_scope`, `llm_gap_routing`) |

**Secrets are never stored here.** The LLM key is read at call time from the environment
variable named by `llm.api_key_env` (shipped default **`AZURE_OPENAI_API_KEY`**, matching
the shipped `llm.provider = "azure_openai"`); the config only names the variable. The OCR
key is the same arrangement under `ocr.azure_api_key_env` (default `AZURE_DI_KEY`).
`GET /settings` reports whether the LLM variable is populated (`key_configured`), never its
value.

## Settings API + admin Settings screen

`GET /settings` (any authenticated caller) returns a non-secret snapshot of the config —
so the frontend can surface it and read runtime flags. `PATCH /settings`
(`config:settings`, admin only) changes the runtime-mutable settings, which are **three
groups, not one flag**:

1. the **feature flags** — `ui_localization`, `review_required`, `seed_demo` (load/clear
   the sample project);
2. the **LLM configuration** — `provider`, `model`, `base_url`, `temperature`,
   `max_tokens`, `timeout_seconds`, `api_key_env`, `azure_endpoint`, `azure_api_version`,
   `azure_deployment` (`LLM_EDITABLE`). The **key itself is never accepted from the UI** —
   only the *name* of the env var;
3. the **extraction thresholds** — the mapping ensemble's accept/candidate/margin bars and
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

**Editable at runtime.** An admin can change the LLM configuration (provider, model,
base_url, temperature, max_tokens, timeout, the Azure endpoint / api-version / deployment,
and the *name* of the key's env var) live from the Settings screen —
`PATCH /settings {"llm": {…}}`. `settings_state.set_llm_config` applies the edit onto the
process-wide `Settings.llm` so the provider registry and adapters pick it up immediately,
**and persists it to `setting_overrides`** so it survives a restart and is picked up by
every process against the same database (`PATCH /settings {"reset_llm": true}` restores what
`config.toml` shipped). The **API key itself is never accepted from the UI** — only
`api_key_env` is editable; the snapshot reports whether that env var is currently populated
(`key_configured`).

## Frontend flow

`frontend/src/lib/api.ts` sends the stored bearer token on every request and exposes
`login`/`logout`/`demoUsers`/`settings`/`patchSettings`. `App.tsx` shows `Login` until a
valid session exists (a 401 from `/me` returns the user to login); once authenticated it
renders the shell and, via a `useSettings()` sync, mirrors `features.ui_localization` into
the UI store so the interface-localization policy takes effect immediately.
