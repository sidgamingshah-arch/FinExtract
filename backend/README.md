# FinEx backend

FastAPI application + the document-extraction pipeline.

## Install & run

```bash
pip install -e ".[dev,pdf,cjk]"
export LLM_GATEWAY_TOKEN=...      # the CRISIL LLM gateway key (or LLM_GATEWAY_TOKEN=... in .env)
pytest -q
uvicorn app.main:app --reload
```

On Windows, `Start-FinEx.bat` at the repository root does all of this, and starts the web app.
The LLM gateway, model and address ship in `config.toml` `[llm]` — see the root README.

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

## A hosted document engine: Kensho Extract

Kensho Extract can read a filing in place of the PDF's own text or OCR. It is sent the whole PDF
once and returns every page's text and table cells with their positions; those become positioned
words, and the same row-and-column reader as a native page reads them. **Off by default** — it
sends the filing outside this server.

1. In `config.toml` `[document_engine]`, set `kensho_submit_url` and `kensho_result_url` from
   Kensho's API documentation for your account (`{request_id}` in the result address is
   replaced by the id the submit call returns).
2. Put the token in the environment: `KENSHO_ACCESS_TOKEN`, or set `kensho_token_url` and
   `KENSHO_REFRESH_TOKEN` to exchange a refresh token for short-lived access tokens.
3. On the Settings screen, set **Document extraction engine** to `kensho` and choose **Pages the
   document engine reads**: `scanned` (in place of OCR) or `all` (in place of the text layer).
   The OCR card shows whether the addresses and a token are configured.

A page Kensho does not return, or a run where the call fails, is read as before, and the run log
says why (`extract:document_engine_failed(...)`). The engine plugs in through
`app/ports/document_engine.py`; another hosted engine is one adapter registered under
`document_engine`.

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
| `FINEX_OCR__ENGINE` | `docling` | OCR for scanned pages: `docling` / `azure` / `paddleocr` / `stub` |

**The LLM is not an env var.** It is defined in one place, `config.toml`'s `[llm]` table; only
its API key comes from the environment (or `.env`), under the name `[llm].api_key_env` gives.
`FINEX_LLM__*` is ignored and named in a startup warning, and the Settings screen does not edit it.

See `docs/architecture/` for the full design.
