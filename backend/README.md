# FinEx backend

FastAPI application + the document-extraction pipeline.

## Install & run

```bash
pip install -e ".[dev]"
pytest -q
uvicorn app.main:app --reload
```

## Scanned pages: offline OCR with Docling

A page with a text layer (a native PDF) is always read from that layer, exactly as printed; OCR is
only for pages without one. Docling reads those pages **entirely on this server**: its models sit in
`[ocr] docling_models_dir` (default `models/docling`), the adapter hands Docling that folder and
switches the Hugging Face client offline, so nothing is downloaded and nothing leaves the machine.

Admins switch it on and off on the **Settings** screen ("Read scanned pages (OCR)", on by default).
The OCR card there shows whether Docling is installed and its models are present.

Three ways to get it onto a server, all ending with no network access needed at run time:

| Route | Where you run it | Then |
|---|---|---|
| **Docker image** (recommended) | `docker build -t finex-backend .` on a machine with network access | run the image anywhere; models are baked in |
| **Offline bundle** | `./scripts/build_offline_bundle.sh` on a networked machine with the target's OS and Python | copy `dist/finex-docling-offline.tar.gz`; on the server: `tar xzf …`, `pip install --no-index --find-links offline/wheelhouse -e ".[pdf,docling]"`, `cp -r offline/models/docling models/docling` |
| **Direct** | `pip install -e ".[docling]"` then `python scripts/fetch_docling_models.py` | — |

Models and bundles are never committed (they are hundreds of MB): `backend/models/`, `offline/` and
`dist/` are git-ignored. Keep built images or bundles in your container registry or artifact store.

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
