# FinEx architecture

Design & architecture for the financial-statement extraction platform.

## Contents

1. [Overview & requirements](00-overview.md) — the problem, decisions, and how all 21
   requirements map to components.
2. [Extraction pipeline](01-extraction-pipeline.md) — the 15 stages
   `default_pipeline()` assembles: ingest → integrity → language_detect → classify →
   extract → map_ontology → residual → normalize → link_notes → reconcile → prune_notes
   → confidence → gap_closing → structural.
3. [Data model, template & ontology schemas, API](02-data-model-and-schemas.md).
4. [Note→face reconciliation](03-reconciliation.md) — the highest-value rule (§20).
5. [Multilingual parity](04-multilingual.md) — input = output for en/zh/ar/fr.
6. [Frontend](05-frontend.md) — the routes, the hand-rolled grid and source viewer, the
   Extraction screen, the review queue.
7. [Testing & roadmap](06-testing-and-roadmap.md).
8. [RBAC & commentary](07-rbac-and-commentary.md) — roles/permissions and the Analysis tab.
9. [Configuration & authentication](08-configuration-and-auth.md) — `config.toml`, the
   Settings API, sessions, and LLM provider selection.

## Decisions

- **Stack:** FastAPI (Python 3.11+) backend, React 18 / TypeScript frontend (Vite). The
  frontend has **five** runtime dependencies — react, react-dom, react-router-dom,
  @tanstack/react-query, zustand — and hand-rolls the grid, source viewer, i18n and theme
  tokens; see [05-frontend](05-frontend.md).
- **Extraction:** every external engine sits behind a swappable adapter (no mandatory cloud
  doc-AI). OCR ships **Docling** (free, pip-only, recommended), **Azure AI Document
  Intelligence** (cloud) and **PaddleOCR**, with a loud `stub` for offline runs; the shipped
  `config.toml` selects `docling`. Tesseract is named in that file's comment as an accepted
  value and **has no adapter**. The LLM decides semantics and never emits a value.
- **LLM:** `azure_openai` (`gpt-5-mini`) is the shipped default; `anthropic` and any
  OpenAI-compatible gateway are one config change away. The key is read from the environment
  at call time and never stored.
- **Infra:** portable, behind ports. Today: **SQLite** by default (Postgres via
  `FINEX_DATABASE_URL`) and a **local** object store — the S3-compatible backend is a
  config value with no adapter yet, and `FxConverter` is left unbound because currency
  conversion runs off an admin-maintained rate master rather than a feed.
- **Languages (seed):** English, Chinese, Arabic (RTL), French.
- **Reference data:** one template (`hkfrs_hk_china_v1`) and one rulebook
  (`hkfrs_hk_china`, 185 concepts, 13 residual buckets) ship, and are refreshed into the
  database on every startup when the shipped file differs from what is stored.
