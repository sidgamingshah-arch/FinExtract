# FinEx backend

FastAPI application + the document-extraction pipeline.

## Install & run

```bash
pip install -e ".[dev]"
pytest -q
uvicorn app.main:app --reload
```

## Layout

```
app/
  config.py            Settings (env-driven; SQLite + local store + stub engines by default)
  main.py              FastAPI app; wires routers, registers adapters, init_db()
  core/
    models/            Pydantic domain model that flows through the pipeline
    pipeline.py        Ordered stage orchestrator
    stage.py           Stage protocol + PipelineContext
  ports/               Adapter Protocols (OCR, table, LLM, embeddings, object store, FX) + Registry
  adapters/            Concrete impls: local object store + stubs (real engines added here)
  schemas/             Template + LineItemSet schemas (one config engine), loader/validator, language parity
  services/            mapping (ensemble), working_view (LineItemSet -> the matcher's view),
                       numbers (locale parse+sign), reconcile (§20), documents
  stages/              ingest, integrity, language, classify, reconstruct, extract,
                       map_ontology, normalize, link_notes, reconcile, confidence
  db/                  SQLAlchemy base + ORM models
  api/                 Routers: documents, extractions, templates, line_items, languages, review
tests/                 Unit/golden tests + synthetic fixture generators
```

## Design principles

- **Everything external is a swappable adapter** behind a `Protocol`; the core never
  imports a vendor. Selection is config-driven via the registry. Default engines are
  stubs so the app installs and tests without heavy ML wheels.
- **One document model flows through stages**, each *enriching* it — independently
  testable and re-runnable, with provenance recorded per enrichment.
- **Provenance is first-class**: every value carries a normalized `(page, bbox)` so
  the frontend can hyperlink to the exact source region.
- **Deterministic first, LLM last**: heuristics/rules do the cheap auditable work;
  the LLM is a bounded, schema-constrained tie-breaker.

## Configuration (env vars, prefix `FINEX_`)

| Var | Default | Purpose |
|---|---|---|
| `FINEX_DATABASE_URL` | `sqlite:///./finex.db` | Postgres URL in prod |
| `FINEX_OBJECT_STORE_BACKEND` | `local` | `local` / `s3` / `minio` |
| `FINEX_OCR_PROVIDER` | `stub` | e.g. `paddle` once installed |

**The LLM is not an env var.** It is defined in one place, `config.toml`'s `[llm]` table; only
its API key comes from the environment (or `.env`), under the name `[llm].api_key_env` gives.
`FINEX_LLM__*` is ignored and named in a startup warning, and the Settings screen does not edit it.

See `docs/architecture/` for the full design.
