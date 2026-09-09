# Overview & requirements

## Problem

Analysts re-key financials from the *face* of statements **and** the *notes*,
reconcile them, and verify totals by hand — slow, error-prone, unauditable. FinEx
automates extraction while keeping a human in control: every number carries a
confidence score and a link to its source region, validation rules catch imbalances,
and analysts correct output (with formulas) before exporting.

## Data flow

```
Upload
  → Integrity report (gate: block corrupt/encrypted; warn on scans/rotation)
  → Extraction run (the 16-stage pipeline, in a FastAPI background task)
      ingest → integrity → language_detect → classify → extract → map_ontology
             → residual → normalize → link_notes → reconcile → prune_notes
             → confidence → gap_closing → face_mapping_contract → structural → segment
  → Progress: the client POLLS GET /extractions/{run_id} once a second while the run is
    `running` — per-stage record + log tail. There is no WebSocket.
  → Extraction screen /extraction (stages while it runs; rows + click-to-source after)
  → Workspace (side-by-side source ↔ editable template grid)
  → Review queue (three things only: unplaced face figures, failed validation rules, and
    subtotals that do not match their components) → resolve
  → Export (Excel / JSON)
```

The stage order above is `backend/app/core/pipeline.py::default_pipeline()`'s; that
function is the single source of it. There is no separate `reconstruct` stage — table
reconstruction happens inside `extract`. See
[01-extraction-pipeline](01-extraction-pipeline.md).

The pipeline runs off the request thread in a **FastAPI `BackgroundTask`** (no broker; the
task signature is deliberately queue-shaped so one can be swapped in). What is persisted
during a run is the **progress record and the log tail**, written onto the `ExtractionRun`
row at each stage transition; the extracted rows land as **one JSON result payload** in the
commit that finishes the run. Individual stage outputs are *not* separately persisted, so
stages are not independently re-runnable from storage — re-running means a fresh run over
the whole pipeline (`POST /documents/{id}/extractions` again, which the UI exposes as
*re-extract*).

## Requirements → components (all 21)

| # | Requirement | Component |
|---|---|---|
| 1 | Excel / PDF / scanned extraction | `stages/ingest.py` routes (openpyxl / PyMuPDF / OCR port); reading happens in `stages/extract.py` |
| 2 | Face **and** notes | `stages/classify.py` + `NotesTable`/`NoteItem` + `stages/link_notes.py` + `stages/reconcile.py` |
| 3 | Output into predefined template | `schemas/template.py` + `LineItem.canonical_key`; the grid is built from the template alone (`documents.py::_build_statement`) |
| 4 | Template addable from frontend | Excel round-trip: `GET /templates/{id}/xlsx` + `POST /templates/xlsx` (`services/template_xlsx.py`), published as the next `TemplateVersion`. No drag-and-drop tree editor was built. |
| 5 | Configuration drives description-based extraction | `schemas/line_items.py` (the one configuration schema) → `services/working_view.py` → `services/mapping.py` + `stages/map_ontology.py` |
| 6 | Hyperlinked side-by-side view | `Provenance` (normalized bbox / sheet+cell) → `GET /documents/{id}/pages/{n}/image` + `GET /documents/{id}/cell-context`, rendered by `frontend/src/components/SourceViewer.tsx` |
| 7 | Edit output in UI | `PATCH`/`DELETE /documents/{id}/line-items/{key}` — the overlay is persisted onto the latest run (`edited_slots`, machine values snapshotted for an exact revert). The `EditEvent` table is designed, **not built**; the run/LLM ledger is `services/audit.py` (process-local). |
| 8 | Export Excel / JSON | `services/export.py` (openpyxl, server-side; formulas travel as a text column + cell notes, not live `=` cells) |
| 9 | Confidence per extraction | `ConfidenceVector` (per row **and** per value), set by `stages/confidence.py` + `stages/structural.py` |
| 10 | Edits show formulas | `services/formula.py` — whitelisted-AST engine, server-side; the client sends/stores the formula string |
| 11 | Review queue on failed checks | `services/checks.py` (balance / subtotal / sign / note tie) + `services/structural_checks.py` (declared arithmetic) → findings assembled at serve time by `GET /documents/{id}/review`; human verdicts persisted as `ReviewJudgement`. There is no `ReviewItem` table. |
| 12 | Sign detection & normalization | `services/numbers.py` (printed sign) + `stages/normalize.py` (label cues, `sign_rule`, `sign_convention` as an expectation) |
| 13 | Consolidated + standalone in one pass | `Basis`; values keyed by (basis, period); two-level column header detected in `services/row_reconstruct.py` |
| 14 | Change units / currency | `UnitContext` + a display-time transform in the Workspace + the admin FX master (`services/fx.py`, `/fx-rates`) + an export-time unit target. There is no `POST /statements/{id}/convert`. |
| 15 | Note number per line item | `LineItem.note_number` / `note_refs` + note chip |
| 16 | Separate notes tab | `NotesTable` + `stages/prune_notes.py` (only referenced notes are published) + `GET /documents/{id}/notes` + the All Notes screen |
| 17 | Upfront document-integrity test | `stages/integrity.py` → `IntegrityReport`; enforced again at `POST /documents/{id}/extractions` |
| 18 | Mixed scanned + native PDF | per-page routing in `stages/ingest.py`; `stages/extract.py` sends native pages through the text layer and scanned ones through the OCR port into the same reconstruction |
| 19 | Locate face/notes pages first | `stages/classify.py` (Viterbi decode over per-page evidence + document order) + the page-scope selection (`PUT /documents/{id}/scope`), which is what `extract` restricts itself to |
| 20 | **Note→face subtraction reconciliation** | `FaceNoteLink` + `services/reconcile.py` + `stages/reconcile.py` |
| 21 | **Multilingual, input = output parity** | `stages/language.py` + `schemas/languages.py` + `label_i18n` / `aliases_i18n` |

## Cross-cutting principles

- **Adapters everywhere** — OCR/LLM/object-store/FX behind `Protocol`s,
  selected by config from a registry. "Decide infra later" costs nothing. Note that only
  LLM, OCR and object-store are actually *bound* today — see the port table in
  [01-extraction-pipeline](01-extraction-pipeline.md#adapter-ports).
- **Provenance is first-class** — a value without `(page, bbox)` or `(sheet, cell)` is a bug.
- **Deterministic first, LLM last** — the numbers and their locations are read
  deterministically; the LLM decides semantics and never emits a value.
- **Versioned & reproducible** — each extraction run pins document hash + template
  version + **line-item version** (`extraction_runs.line_item_version_id`) + engine id, and
  records *which configuration* produced its figures rather than letting a reader re-derive
  it.
- **One configuration engine** — the line-item set is both the output shape and the matching
  rulebook. There is no second configuration store, no second configuration API and no
  switch between them.
