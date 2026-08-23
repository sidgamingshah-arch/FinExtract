# Extraction pipeline

Each stage is `run(doc, ctx) -> DocumentModel`, *enriching* an immutable-ish document
model (`app/core/stage.py`, `app/core/models/document.py`). A BLOCKER integrity finding
short-circuits the rest; everything else runs to completion so all findings and partial
results are available (`app/core/pipeline.py::Pipeline.run`).

## Stages

**Fifteen stages, assembled by `app/core/pipeline.py::default_pipeline()`.** That
function is the only place the order is stated; `api/routes/extractions.py::pipeline_stage_names`
reads the list off it rather than keeping a copy, and the run row records the list it was
queued with. Do not add a third copy — the list below names each stage and its file, and
its order is `default_pipeline()`'s:

`ingest · integrity · language_detect · classify · extract · map_ontology · residual ·
normalize · link_notes · reconcile · prune_notes · confidence · gap_closing · structural ·
segment`

**Two passes over the first four.** `services/documents.py::analyze_document` runs
`ingest · integrity · language_detect · classify` alone at upload, synchronously, so the
integrity gate and the page scope exist before an extraction is started. The run then goes
through `default_pipeline()` from the beginning, re-reading the stored bytes — so a run
depends on nothing computed at upload and is reproducible from the file alone.

1. **Ingest & route** (`stages/ingest.py`) — MIME/magic detection (not extension).
   Excel → openpyxl (one "page" per sheet); PDF → **per-page** native-vs-scanned detection
   using PyMuPDF text-char-count, text-area coverage, and image-area coverage; image →
   scanned. A page with a text layer *and* more than half its area covered by images is
   `MIXED`. Mixed documents are handled per page, never a document-level flag.
2. **Integrity** (`stages/integrity.py`) — collects **all** findings into an
   `IntegrityReport` (BLOCKER/WARNING/INFO). Nine checks: unrecognised format, corruption,
   password/encryption, no-pages, blank pages, rotation, inconsistent page dimensions,
   hidden Excel sheets, mixed-scan ratio. BLOCKER halts the pipeline, and
   `POST /documents/{id}/extractions` refuses the run at the API boundary rather than
   returning a misleading empty success.
3. **Language detect** (stage name `language_detect`;
   `stages/language.py::LanguageDetectStage`) — sets the document `locale` (drives OCR
   pack, number parsing, ontology aliases). A
   dependency-free script/keyword heuristic (`detect_locale`) covers the seed set; a
   statistical detector (`fasttext`, the `lang` extra) is **not wired in** — the extra
   exists so one can be, nothing imports it today.
4. **Classify** (`stages/classify.py`) — locates FACE (BS/P&L/CF/Equity) vs NOTES vs OTHER.
   No longer a per-page title regex: per-page evidence becomes an **emission** score,
   document order becomes **transition** costs, and a **Viterbi decode** picks the page-kind
   sequence that best explains the whole filing — so one strong title cannot flip the rest
   of the document, and an HK filing that prints the company-only balance sheet *after*
   note 40 can still be recovered. The lexicon carries STRONG (self-anchoring) and WEAK
   (anchor-requiring) patterns per statement, in English and Han. Layout features
   participate — the page's title zone, numeric-token density, running-header suppression —
   and an ambiguous title is recorded in `page.evidence["title_ambig"]` rather than silently
   decided. There is **no LLM tie-break** in this stage. Notes pages are kept (needed for
   §20), and titles that looked like a statement and resolved to nothing land in
   `DocumentModel.unmapped_titles` so the lexicon's coverage is measurable.
5. **Extract** (`stages/extract.py`) — rows → `LineItem`s with values keyed by
   (basis, period), note refs, unit context, and provenance. **Table reconstruction happens
   inside this stage**, which is why there is no separate `reconstruct` stage (the comment
   in `default_pipeline()` says so): native pages go through the PyMuPDF text layer into
   `services/row_reconstruct.py`, scanned pages and standalone images go through the OCR
   port into the *same* `row_reconstruct` logic, and Excel is read cell-by-cell by
   `services/excel_extract.py`. The reading rules (which printed column is the Group's,
   which is the current period, what scale the figures are in) come from the rulebook the
   *run* was pinned to — `reconstruction_rules(ctx)`, not whichever rulebook is currently
   in force.
6. **Map (ontology)** (`stages/map_ontology.py` + `services/mapping.py`) — the
   multi-strategy matcher (below).
7. **Residual** (`stages/residual.py`) — a printed face line that matched no specific
   concept goes to its own section's residual bucket ("Others") instead of vanishing from
   the statement. Section placement is decided by structure, strongest signal first: the
   section banner above the row, then the next section subtotal at a higher ordinal, then
   the section of the nearest preceding mapped line. With a `schema_version: 2` rulebook
   loaded, *every term of the sweep* is read from the rulebook's `residual_framework`
   (when it runs, which rows are eligible, what a component must record, the identity the
   section must then satisfy, the conditions that send a section to review), overridable
   per concept by `residual_policy` / `never_sweep`.
8. **Normalize** (`stages/normalize.py`) — sign detection and unit-context resolution:
   `Less:`/`Add:` label cues, the ontology's `sign_rule.flip_if_label_matches`, and the
   printed-sign tier (parentheses / trailing minus) already decoded by `services/numbers.py`
   at extraction. It also *checks* the rulebook's `sign_convention`
   (`positive_expected` / `negative_expected` / `either`), which is an **expectation, not a
   transformation** — a figure arriving with the opposite sign is flagged and its sign
   confidence drops; the value is left as reported.
9. **Link notes** (`stages/link_notes.py`) — builds `FaceNoteLink`s from each face line's
   `note_refs` / `note_number` against an index of the extracted `NotesTable`s, labelling
   the relationship (`ONE_TO_ONE` / `NOTE_SPLITS_TO_MANY_FACE` / `MANY_NOTES_TO_ONE_FACE`)
   from the citation counts. Amount validation is the **reconcile** stage's job, not this
   one's — see [03-reconciliation](03-reconciliation.md).
10. **Reconcile** (`stages/reconcile.py` + `services/reconcile.py`) — the §20 subtraction
    and the note→face tie grading (see [03-reconciliation](03-reconciliation.md)).
11. **Prune notes** (`stages/prune_notes.py`) — publishes only the notes a face line
    actually references; accounting policies, governance tables and subsequent events are
    noise in the notes index, the export and the review queue. Runs *after* reconcile,
    which needs every extracted note to check the ties. Nothing is deleted from the source
    or from provenance — the log records exactly what was dropped.
12. **Confidence** (`stages/confidence.py`) — sets the `validation` sub-signal on extracted
    values from the checks available at this point (the balance-sheet identity per
    (basis, period), and the note→face tie from the reconcile report), so
    `ConfidenceVector.overall` is capped by participation in a failed check rather than
    reporting a clean OCR/mapping as confident. The row-based rule catalog that populates
    the review queue runs at the API layer instead — see
    [02-data-model-and-schemas](02-data-model-and-schemas.md#validation-engine-feeds-the-review-queue).
13. **Gap closing** (`stages/gap_closing.py` + `services/gap_closing.py`) — a subtotal that
    still does not tie may be missing a line the mapper could not place. **Arithmetic
    proposes and the model disposes**: only a subset of leftovers that closes the gap in
    *both* periods within tolerance is offered (one period is a coincidence, two is
    evidence), and the LLM picks one of those subsets or none — because whether "Pledged
    bank deposits" belongs under current assets is a question about meaning. Deliberately
    **before** the structural checks, so a gap the model closes reports as tied rather than
    as a defect the analyst has to chase. Gated on `extraction.llm_gap_routing` (default on)
    and on a non-`stub` provider; with neither, the gap stays a review item, which is the
    honest outcome. Confirmed routings are kept on `DocumentModel.gap_routings` so the
    decision is inspectable rather than an unexplained change of mapping.
14. **Structural** (`stages/structural.py` + `services/structural_checks.py`) — runs the
    arithmetic the template and the rulebook *declare*: template `rollup`s and statement
    `identities`, the rulebook's `validation.identities`, its
    `validation.cross_concept_guards` and its `validation.section_reconciliation`. Every
    relation produces a row **including the ones that could not be run**, each skip
    carrying a classifiable `reason` (`services/coverage.py`), so partial coverage is
    visible rather than implied. A failure flags the participating line items and values.

15. **Segment** (`stages/segment.py` + `services/buckets.py`) — files every face row and
    every note into the eight buckets an analyst reads a filing in: non-current assets,
    current assets, non-current liabilities, current liabilities, equity, P&L, cash flow,
    Others. **Last by necessity, not by convention**: a balance sheet prints four of these
    plus equity on a single page, so page classification can never separate them — only a
    row's resolved `section_scope` can, which does not exist until `map_ontology` and
    `residual` have run. The section → bucket edge is *derived* from the section id's own
    phrase against `mapping.HEADING_ROW_SECTIONS`, so the rulebook and this layer cannot
    drift into two ideas of what "current assets" is; a balance-sheet section with no
    bucket is reported in `unknown_sections` rather than counted as Others.
    Membership only — the figures stay on `line_items` / `notes` and the
    `/documents/{id}/buckets` endpoints join to them at serve time. Every face row lands in
    exactly ONE bucket (so that side is summable, and `unresolved_face_item_ids` separates
    "belongs in Others" from "nothing could place it"); a note cited from two buckets is
    filed in BOTH, because each section needs it in front of the reader — so the notes side
    is deliberately not a partition, `shared_notes` marks the overlap in every bucket
    holding it, and the index serves a distinct-note count.

**Where a design intent diverged.** Earlier revisions of this document described a
separate `stages/reconstruct.py` converging native and OCR pages on the `Table` / `Cell`
core models. That stage was never built and the file does not exist: reconstruction lives
inside `extract`, and the convergence point in practice is `row_reconstruct.Word` →
`LineItem`, not `Table`. `app/core/models/table.py` and `DocumentModel.tables` are still
declared and stay empty; the intent (one table shape whatever the source) survives in the
shared `row_reconstruct` path.

## Mapping — a combination of methods, LLM as the key driver

`services/mapping.py` — mapping a printed label to a canonical concept is a **combination
of methods, none forced out**; the LLM is a *key driver*, not the sole authority. Each
method contributes and they corroborate one another:

1. **Exact / normalized lexical** — identity alias match; short-circuits (free, no tokens).
2. **Rule / fuzzy** — every method runs and contributes candidate evidence (and
   pre-shortlists concepts for the LLM when the ontology exceeds
   `extraction.llm_candidate_cap`, default 40).
3. **LLM semantic decision** (`extraction.llm_mapping`, default on) — the driver: shown
   the caption plus candidate concepts *with their criteria* (definition, include /
   exclude, confusable-with, value_scope) and the ontology's global policies + worked
   examples, it chooses by **meaning**. So "Amounts due from customers" → `trade_receivables`
   with no matching alias, and repeated "Others" captions disambiguate by section context.
**Two tiers that used to be here are gone, and the removals are the contract getting shorter
rather than weaker.** A **fuzzy / string-similarity** tier decided rows on wording, which the
rulebook's own `binding.order` never declared and which produced the error nothing downstream can
see (on the shipped rulebook, "Profit before exceptional items and tax" filed as
`pl_profit_before_tax` at 0.61, accepted and unflagged — two subtotals differing by exactly the
exceptional items, so the statement still tied). A **semantic-embedding** tier existed in the
matcher but never ran: `map_ontology.py` built the matcher without a provider and the only
registered one was a stub that raised. Measured on a real 300-page filing, of the 15 rows fuzzy
used to name, 11 now land in their own section's residual *Others* — still on the statement,
still summing, itemised under their printed label — and 3 become unmapped for a human. Alias
similarity survives as a *measurement* (`_alias_similarity`) used by two guards, and can map
nothing on its own.

**Combination policy:** exact wins outright; otherwise the LLM makes the call but is
**corroborated by the deterministic methods** — agreement nudges confidence up; a strong
lexical disagreement lowers it and flags review; the methods that agreed are recorded
(`MappingResult.agreement`). When no LLM is configured or it abstains, the deterministic
ensemble decides with the margin-over-runner-up accept policy (`extraction.mapping_margin`).
Every value also carries an `allocation_status` (direct / child / residual / …) so
parent-child handling is auditable.

`extraction.mapping_scope` chooses the granularity: `per_statement` (the default, most
accurate) batches a statement's captions into one LLM decision; `per_line` maps each
caption independently (cheaper, less context). Which one ran, and *why* — a run that fell
back to the deterministic ensemble because no provider resolved says so — is recorded on
the run result's `mapping` block, so a degraded run is visibly weaker rather than silently
so.

Per-line LLM token usage accumulates on the pipeline context and is recorded on the
extraction run's audit-log entry. Winning method, confidence and per-strategy scores are
stored on the confidence vector; anything short of a confident, corroborated decision goes
to review, not a guess.

## Values & provenance — grounded extraction (LLM references, never transcribes)

Value-level provenance (click-to-source) is kept trustworthy **even with the LLM at the
centre** by a simple rule: **the LLM references facts, it never emits values.**

1. The source is parsed deterministically into atomic facts, each with its exact origin:
   - **Excel** (`services/excel_extract.py`): every numeric row → a `LineItem` whose
     `ExtractedValue.provenance` points at the precise **sheet + cell** (e.g. `P&L!C14`).
   - **Native PDF** (`services/pdf_extract.py` via PyMuPDF text layer): positioned words →
     rows → line items with **page + normalized bbox** provenance; note refs ("Note 15")
     captured, not mistaken for values.
   - **Scanned PDF / image** (same path): the page is rasterized and sent to the configured
     **OCR provider** (`ocr.engine`); OCR words come back with normalized bboxes and feed
     the *same* `row_reconstruct` logic (source_kind `ocr`). No OCR/LLM is needed for native
     inputs. The recommended **free** engine is **Docling** (`ocr.engine = "docling"`,
     `pip install -e ".[docling]"`) — pip-only, no system binary and no cloud, doing layout
     + OCR + table structure; `adapters/docling_ocr.py` maps its text items to word-level
     tokens with normalized top-left bboxes. For a **cloud** option, **Azure AI Document
     Intelligence** (`ocr.engine = "azure"`, `adapters/azure_doc_intelligence.py`) runs the
     `prebuilt-layout` model over REST (analyze + poll) and maps its word polygons to the
     same normalized-bbox contract; endpoint/model are config, the key is read from the env
     var named by `ocr.azure_api_key_env`. `paddleocr` is another alternative behind
     `.[ocr]`. Every engine is swappable via `ocr.engine` with no pipeline change; a
     `stub` engine that fails loudly is registered for offline use, and the shipped
     `config.toml` selects `docling`. **Tesseract is named in the config comment as an
     accepted value but has no adapter** — selecting it raises from the registry.
2. Mapping then decides *which canonical concept* each fact is, by meaning. In
   `per_statement` mode (`extraction.mapping_scope`, the default) the LLM sees the whole
   statement's captions **by `item_id`** plus the candidate concepts + policies, and
   returns `{item_id → canonical_key, allocation_status, confidence}`. It references ids
   and keys; the **numbers and their sheet/cell (or page/bbox) come from step 1**, so a
   value can always be traced back and verified. `per_line` maps each caption
   independently (less context, cheaper).

This is how accuracy (full-statement LLM context for containment/residual/"Others") and
hard value-level provenance coexist: the model does the semantics, the deterministic layer
owns the numbers and their location.

**In the UI.** An uploaded document's extraction is shown at **`/extraction`**
(`frontend/src/screens/ExtractionView.tsx`, registered as `SCREENS.extraction` in
`frontend/src/screens/config.ts`, step 4 of the stepper). The former per-document URL
`/documents/:id` still resolves: it adopts the id in the path as the active document and
then redirects to `/extraction` (`App.tsx::AdoptDocumentAndRedirect`). Each value's
provenance is a click-to-source chip, and the shared source panel
(`frontend/src/components/SourceViewer.tsx`) adapts to the source kind:
- **PDF** — clicking renders that page, server-rasterized to PNG via
  `GET /documents/{id}/pages/{n}/image` (PyMuPDF), and draws the value's normalized bbox as
  an overlay (percent-positioned, so it survives any display scale).
- **Excel** — clicking a `Sheet!Cell` chip fetches a window of surrounding cells via
  `GET /documents/{id}/cell-context?sheet=&cell=` (`services/excel_extract.cell_context`)
  and renders a mini spreadsheet grid with the exact origin cell highlighted — the
  spreadsheet analogue of the PDF overlay, so click-to-source is uniform across formats.

The run is mapped against the seeded reference ontology (`app/sample/reference.py` holds
the stored HKFRS template + rulebook equal to the shipped files on every startup), so the
"mapped to" column populates — via the LLM when reachable, else the deterministic alias
tier offline.

**Row reconstruction robustness.** `services/row_reconstruct.py` groups positioned words
(native text layer *or* OCR) into visual rows, then folds a **wrapped label** — a label
that breaks across two tightly-spaced, left-aligned lines (e.g. "Property, plant and" /
"equipment 12,500") — back into one line item so the caption isn't truncated to its last
fragment. The merge is deliberately conservative (paragraph-tight vertical gap + label-
column alignment, with an ALL-CAPS/`:`-suffixed section-header guard) so headings are never
swallowed into the item below them. It also has a second path for a **matrix** statement
(changes in equity), whose columns are equity components rather than periods.

## Progress reporting

There is **no WebSocket and no push channel**. `POST /documents/{id}/extractions` returns
`202` with a `progress_url` naming `GET /api/v1/extractions/{run_id}`, and the client polls
that once a second while the run is `running` (`frontend/src/lib/queries.ts::useExtraction`).

The poll is worth polling: `api/routes/extractions.py::_RunProgress` writes a full
`ExtractionProgress` record onto the run row at every stage transition — phase, pct, the
stage in flight, `stage_index` / `stage_count`, `stages_done`, `started_at`, `elapsed_ms` —
plus a bounded tail of the pipeline log, each in its own short-lived session so a
half-built result is never published. `GET /extractions/{run_id}` serves that record
alongside **the stage list the run was queued with** and the log tail, which is what lets
the Extraction screen tick stages off. A terminal record (`done` / `failed`) is written in
the same commit as the status and the result; a failure **names the stage that was in
flight**. Runs written before this contract existed return `progress: null` rather than a
half-record.

## Adapter ports

`ports/`: `OcrProvider`, `TableStructureProvider`, `LlmProvider` (structured JSON,
temp 0, Pydantic-validated), `EmbeddingProvider`, `ObjectStore` (local impl provided),
`FxConverter`. Concrete impls register into `Registry` keyed by id; config selects them.
Stub engines are registered for every kind and raise a clear message, so a mis-wired
pipeline fails loudly rather than silently.

Which ports are actually *bound* differs, and the difference matters when reading the rest
of these documents:

| Port | Adapters registered | Consumed by the pipeline |
|---|---|---|
| `LlmProvider` | `azure_openai` (default), `anthropic`, `openai` / `openai_compatible`, `stub` | yes — mapping, gap closing, netting, credit narrative |
| `OcrProvider` | `docling`, `azure` (Document Intelligence), `paddleocr`, `stub` | yes — scanned pages and images |
| `ObjectStore` | `local` | yes — uploaded bytes |
| `TableStructureProvider` | `stub` only | **no** — nothing calls it; `row_reconstruct` does the job |
| `FxConverter` | none — the port is left unbound | **no** — currency conversion is not a rate *feed*. It runs off an admin-maintained rate master (`app/db/models.py::FxRate`, `app/services/fx.py`, `GET/POST/PUT/DELETE /fx-rates`), which resolves a pair `direct` or `inverse`-and-flagged and **refuses** rather than triangulating. |
