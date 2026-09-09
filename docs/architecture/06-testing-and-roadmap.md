# Testing & roadmap

## Testing strategy

- **Synthetic fixtures with known ground truth** (`backend/tests/fixtures/generate.py`)
  are the backbone — no real (sensitive) statements needed to measure accuracy:
  - native PDFs via reportlab with controlled face + notes, note refs, and
    deliberately reconcilable numbers; plus dual-basis, group/company, multi-page,
    annual-report, HK running-header and comparative variants;
  - XLSX via openpyxl with negative number-formats and a hidden sheet;
  - **not built**: scanned variants by rendering + degrading (rotate/blur/noise/low-DPI)
    for OCR cases with exact expected coords, and corrupt/encrypted variants for the
    integrity blockers. The integrity blockers are exercised with hand-built byte inputs
    instead.
- **Golden/unit tests per stage** — fixture → expected result. The suite is
  **78 test modules / ~925 test functions** under `backend/tests/`, covering the pipeline
  and the API end to end: ingest routing + integrity, page classification (lexicon and
  gating), locale number parsing, the mapping ensemble (deterministic, LLM, v2 rulebook,
  bilingual), residual routing and the residual framework, normalization and signs,
  reconciliation arithmetic and the reconcile stage, prune-notes, structural checks and
  the coverage contract, gap closing / calculated lines, the periods resolver and edits,
  export honesty, the template workbook round-trip, the review queue (checks, fix
  actions, remap), extraction progress, rulebook plumbing and reference refresh, RBAC,
  settings and their persistence, FX rates, and each LLM/OCR adapter.
- **Property-based tests** — `hypothesis` is a declared dev dependency; **no
  property-based test has been written yet**. The intended target is reconciliation:
  random but consistent face/note sets → assert rollups + accounting identity hold and
  that reconciliation is idempotent.
- **Adapter contract tests** — four adapters have a module of their own, exercising the wire
  format and its mapping to the port's contract with faked responses:
  `test_anthropic_llm.py`, `test_azure_openai_llm.py`, `test_azure_doc_intelligence.py`,
  `test_docling_ocr.py`. **`adapters/openai_llm.py`** (the whole OpenAI-compatible-gateway
  path, registered as both `openai` and `openai_compatible`) and **`adapters/paddle_ocr.py`**
  have **no dedicated test**. The *one fixture through every adapter* matrix that would prove
  downstream stages adapter-agnostic is also not built.
- **Frontend regression** (Playwright, `frontend/e2e/smoke.spec.ts`) — well past a smoke
  test in scope: the greenfield empty state, loading the sample, note-reference hyperlinks,
  uploading a PDF and a spreadsheet as an analyst, integrity → extract end to end,
  role gating on the template/line-item/threshold surfaces, line-item + netting + criteria
  edits persisting, template workbook publish, version-in-force labelling, review
  filtering, accepting a finding across a reload, and the coverage band's counts.
  **Known broken:** the test named "real extraction: prior-year links, an edit that sticks,
  KPIs and Additional items" still clicks `seg-additional_items`, a Workspace tab that was
  removed with the "Additional items" view, so it times out. Fix the test (or restore the
  affordance) before treating a `pnpm e2e` run as green.

Run: `cd backend && pip install -e ".[dev]" && pytest -q`; `cd frontend && pnpm e2e`.

## Roadmap

**Backend** (✅ = done)
1. ✅ Skeleton + contracts (models, pipeline, registry, ports, FastAPI app).
2. ✅ Native ingest + per-page routing; integrity report; page classification (Viterbi
   decode over per-page evidence and document order).
3. ✅ Line-item mapping ensemble; locale number parsing; reconciliation arithmetic.
4. ✅ Row/value extraction with provenance + the two-level basis header — **inside the
   `extract` stage** via `services/row_reconstruct.py` (native text layer) and
   `services/excel_extract.py`, not a separate reconstruct stage. The `Table` / `Cell`
   models that stage was going to fill remain unpopulated.
5. ✅ Sign/unit normalization; note linking (`FaceNoteLink`); residual routing.
6. ✅ Reconciliation stage + `ReconciliationReport`; the checks/structural validation
   engines + the review queue with human judgements. Extraction is persisted as the run's
   JSON `result` payload — the relational Statement/LineItem/… tables were **not** built,
   deliberately (see
   [02-data-model-and-schemas](02-data-model-and-schemas.md#designed-not-built)).
7. ✅ OCR path into the same reconstruction: Docling (free, recommended), Azure AI
   Document Intelligence (cloud), PaddleOCR. Tesseract is named in `config.toml`'s comment
   but **has no adapter**.
8. ✅ Formula engine; export renderers (Excel/JSON); edit endpoints (no `If-Match`).
   Progress is polled, not pushed — the WebSocket stream is not built and the
   `progress_url` in the 202 response names the poll endpoint.
9. ✅ LLM beyond mapping: gap closing, containment netting, the credit narrative, and the
   analysis run — behind `azure_openai` (default), `anthropic` and OpenAI-compatible
   adapters.
10. **Outstanding.** Bind an `EmbeddingProvider` so the matcher's cosine tier actually
    runs (only a stub is registered today). Confidence calibration. Optimistic concurrency
    (`row_version` + `If-Match`) on edits. An append-only edit ledger — `services/audit.py`
    is process-local. Alembic migrations. A real background worker/broker in place of
    `BackgroundTasks`.

**Frontend**
✅ Foundations → viewer core (server-rasterized pages + normalized-bbox overlay) → grid
core → grid→viewer linkage → editing + server-side formulas → review queue with judgements
and re-map → notes tab → consolidated/standalone → integrity gate → template workbook
authoring + the Line Items configuration screen → export → the Extraction screen at
`/extraction` with live
stage/log reporting. Outstanding: bidirectional viewer↔grid focus, and route-level code
splitting (neither needed while the bundle is five dependencies).

## Deferred, non-blocking decisions

- LLM hosting: Azure OpenAI is the shipped default; Anthropic and any OpenAI-compatible
  gateway are one config change away (adapter + `[llm]`).
- Background worker (arq/RQ/Celery) — `BackgroundTasks` today; the task signature is
  already queue-shaped (JSON-carryable arguments only).
- Auth/multi-tenancy depth — `tenant_id` columns exist; identity is an in-memory session
  store pending an IdP.
- FX: the `FxConverter` port is left unbound because conversion runs off an
  **admin-maintained rate master** (`services/fx.py`, `/fx-rates`) rather than a feed. A
  feed adapter would bind the port; nothing else changes.
