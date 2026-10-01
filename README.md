# FinEx — Financial Statement Extraction Platform

Extracts financial data from documents (Excel, native PDF, scanned PDF/images —
often mixed within one file) into a **predefined, user-editable template**, with a
**confidence score** and **click-to-source provenance** on every number, automated
**checks-and-balances**, a human-in-the-loop **review queue**, and export to
well-formatted **Excel, JSON or CSV**.

Analysts today manually re-key financials from the *face* of statements **and** the
*notes*, reconcile them, and verify totals — slow, error-prone, and unauditable.
FinEx automates that while keeping a human in control: every machine value is
editable (with spreadsheet formulas), an edit is recorded on the run with its reason and is
exactly revertible to the machine figure, a reviewer's acceptance of a finding is stored
against the figures they actually saw, and nothing that fails a balance check slips through
silently.

## Status

An **end-to-end integrated product**: a FastAPI backend (a 14-stage extraction pipeline,
plus a seeded Ind-AS sample project served over the same kind of endpoints) and a
React/TypeScript frontend of **11 screens**, wired to the backend. Upload a real document
and every screen reads that document's own run; with no document active, the screens that
can fall back render the sample instead.

The extraction engines are real and swappable, not scaffolds: OCR ships **Docling** (free,
pip-only — the default in `backend/config.toml`), **Azure AI Document Intelligence** and
**PaddleOCR**; the LLM ships **Azure OpenAI** (default), **Anthropic** and any
OpenAI-compatible gateway. A loud `stub` is registered for each so the app also runs fully
offline. What is *not* built is listed plainly in
[`docs/architecture/06-testing-and-roadmap.md`](docs/architecture/06-testing-and-roadmap.md)
— chiefly: no embedding adapter is bound, no optimistic-concurrency (`If-Match`) on edits,
no Alembic migrations, and progress is polled rather than pushed.

## Monorepo layout

```
repo-extra-1/
  backend/            FastAPI app + extraction pipeline (Python 3.11+)
    app/              stages/, services/, api/routes/, schemas/, adapters/, ports/
    app/sample/       the shipped HKFRS/IFRS template + rulebook, and the demo dataset
    scripts/          operator scripts (reference-data reconcile, template builders, probes)
    tests/            78 test modules
  frontend/           React + TypeScript SPA (Vite) — the 11-screen workspace
  docs/architecture/  Design & architecture documents
```

## Quickstart

```bash
# backend  (terminal 1)
cd backend && pip install -e ".[dev]"
cp config.local.example.toml config.local.toml   # then fill in the LLM gateway address + model
pytest -q                                 # backend tests
uvicorn app.main:app --port 8000          # API at http://127.0.0.1:8000  (/docs for OpenAPI)

# frontend (terminal 2)
cd frontend && pnpm install
pnpm dev                                  # app at http://localhost:5173 (proxies /api → backend)
```

Then **sign in**. The app opens on a login screen; in demo mode use the one-click
"Sign in as …" buttons for the seeded users — **admin** (Priya Nair), **reviewer**
(Rahul Mehta), or **analyst** (Ana Ferreira) — or type the username with a matching
password.

The app starts **greenfield** (empty): upload a source document on the **Documents &
Template** screen (`/upload`) to begin. To explore every screen with data, an admin can flip **Load sample
project** on the **Settings** screen (or the "Load sample data" button on any empty
screen); set `[features].seed_demo = true` in `backend/config.toml` to load it at
startup instead. LLM configuration (provider/model/endpoint) is admin-editable on
Settings — the API key stays in the environment, never the UI.

Optional backend engines install behind extras so the core stays light:
`pip install -e ".[docling]"` (the recommended free OCR), `.[ocr]` (PaddleOCR),
`.[llm]` (the Anthropic SDK), `.[embeddings]`, `.[pdf]`, `.[lang]`, `.[cjk]`
(Traditional↔Simplified Han folding — `app/services/han.py` falls back to a built-in table
without it). Azure OpenAI and Azure Document Intelligence need no extra; both speak REST
over `httpx`.

### Frontend regression (Playwright)

```bash
cd frontend && pnpm e2e     # boots backend + Vite, drives the UI in Chromium
```

`frontend/e2e/smoke.spec.ts` covers the greenfield empty state, loading the sample,
note-reference hyperlinks, uploading a PDF and a spreadsheet as an analyst,
integrity → extract end to end, role gating on the config surfaces, line-item / netting /
criteria / threshold edits persisting, publishing an edited template workbook, the
version-in-force labelling, review filtering, accepting a finding across a reload, and the
coverage band's counts. One test currently fails — it drives the removed "Additional items"
Workspace tab; see
[`docs/architecture/06-testing-and-roadmap.md`](docs/architecture/06-testing-and-roadmap.md).

## The screens

Documents & Template → Integrity → Page Scope → **Extraction** (`/extraction`, step 4 — runs
the pipeline and reports its stages and log while it runs, then the extracted rows with
click-to-source) → **Workspace** (side-by-side source ↔ template, inline edit + formulas,
confidence scores, KPIs) → All Notes (note-to-face reconciliation) → Review Queue
(exactly three things: an unplaced face figure, a failed validation rule, and a subtotal
that does not match its components — with accept, flip-sign and **re-map** actions) → **Analysis** (one-page financial commentary — ratios,
**year-on-year trends**, strengths/risks) → Template (incl. the note-netting rule) →
**Line Items** (the single mapping configuration) → **Settings** (admin) → Export
(Excel/JSON). Fourteen in all (`frontend/src/screens/config.ts`), filtered per role by
`GET /me`.

Every one of them reads the **active uploaded document's own extraction** when there is one.
The seeded sample is the fallback for the screens that have one, so the app is explorable
before anything has been uploaded.

## Access control, config & languages

- **Login / session** — a bearer-token session (`POST /auth/login`) with seeded demo
  users. `GET /me` drives the nav and route guards; sign out from the top bar. Identity
  swaps to a real IdP without changing the permission model.
- **RBAC** — three roles (admin / reviewer / analyst). Configuration (templates,
  line items, page scope, export inclusions, settings) is admin-controlled; the analyst
  gets a simple flow. Server-side enforced (401/403) and reflected in the nav. See
  [`docs/architecture/07-rbac-and-commentary.md`](docs/architecture/07-rbac-and-commentary.md).
- **Configuration** — `backend/config.toml` (LLM, OCR, embeddings, extraction
  thresholds, auth, feature flags), env-overridable, surfaced on the admin Settings
  screen. This machine's LLM gateway address and model go in the gitignored
  `backend/config.local.toml` (copy `config.local.example.toml`), which overrides
  `config.toml` key by key; the API key goes in the environment or `backend/.env`. See [`docs/architecture/08-configuration-and-auth.md`](docs/architecture/08-configuration-and-auth.md).
- **Multilingual** — English, Chinese, Arabic (RTL) and French. By default the language
  picker localizes **only the extracted financial output and line items**; localizing the
  whole interface is an admin toggle. See
  [`docs/architecture/04-multilingual.md`](docs/architecture/04-multilingual.md).

## Capabilities & where they live

| Capability | Where it lives |
|---|---|
| Excel / native-PDF / scanned ingest, per-page routing | `app/stages/ingest.py` |
| Upfront document-integrity report (nine checks, blockers gate the run) | `app/stages/integrity.py` |
| Locate face / notes pages first (Viterbi decode over page evidence + document order) | `app/stages/classify.py` |
| Table reconstruction — native text layer and OCR converge on one path | `app/services/row_reconstruct.py`, `app/services/pdf_extract.py`, `app/services/excel_extract.py` (all driven by `app/stages/extract.py`) |
| OCR behind adapters (Docling / Azure DI / PaddleOCR / stub) | `app/adapters/`, selected by `[ocr].engine` |
| Line-item-driven mapping — exact, rule, fuzzy and an LLM that decides by meaning | `app/services/mapping.py`, `app/stages/map_ontology.py`, fed by `app/services/working_view.py` |
| Locale-aware number parsing + sign normalization | `app/services/numbers.py`, `app/stages/normalize.py` |
| A printed face line never disappears (residual sweep, rulebook-governed) | `app/stages/residual.py` |
| **Note→face subtraction reconciliation** | `app/services/reconcile.py`, `app/stages/reconcile.py` |
| Declared-arithmetic validation + the coverage contract | `app/services/structural_checks.py`, `app/services/checks.py`, `app/services/coverage.py` |
| Review queue with persisted human judgements and a re-map action | `GET`/`POST /documents/{id}/review*` in `app/api/routes/documents.py`, `app/services/judgement.py` |
| Confidence vector, per row **and** per value | `app/core/models/confidence.py` |
| Template + line-item schemas, versioned; template authored as a workbook | `app/schemas/`, `app/services/template_xlsx.py` |
| Multilingual parity (en/zh/ar/fr) | `app/schemas/languages.py` |
| Consolidated + standalone in one pass | `app/core/models/line_item.py` (values keyed by basis × period) |
| Side-by-side hyperlink provenance (normalized bbox / sheet+cell) | `app/core/models/geometry.py`, `GET /documents/{id}/pages/{n}/image`, `GET /documents/{id}/cell-context` |
| Formulas (whitelisted AST, server-side) | `app/services/formula.py` |
| Export — formatted Excel + JSON | `app/services/export.py` (openpyxl) |
| Currency / units presentation | `app/services/fx.py` + the `/fx-rates` master; units are a display transform plus an export target |

Full details in [`docs/architecture/`](docs/architecture/), which also names what is
**designed and not built** rather than describing it in the present tense.
