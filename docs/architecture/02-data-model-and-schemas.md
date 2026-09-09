# Data model, schemas & API

## Domain model (Pydantic, flows through the pipeline)

`app/core/models/` — the serializable model enriched stage-by-stage and returned by
the API:

- **`DocumentModel`** — pages, integrity report, line items, notes, links, the
  reconciliation and structural reports, confirmed `gap_routings`, detected `locale`,
  `unit_context`, and `unmapped_titles` (headings that looked like a statement title and
  resolved to nothing, kept so the classifier's lexicon coverage is measurable).
- **`PageSource`** — `kind` (face/notes/other), `source_kind`
  (native / scanned / **mixed**, per page — `ingest.py` assigns `MIXED` to a page that has a
  text layer *and* more than half its area covered by images), geometry, the
  native-vs-scanned feature values, and the classifier's own evidence (which title it
  matched, the decode margin, the statement and entity `scope` it read).
- **`LineItem`** — `canonical_key`, `parent_id` (rollup tree), `role`, a dict of
  **`ExtractedValue` keyed by (basis, period)**, `sign_convention`, `note_refs`,
  `note_number`, `reconciliation_role`, `formula`, `ConfidenceVector`.
- **`ExtractedValue`** — `value_raw` (as printed) + `value` (sign-normalized) +
  `reconciled` (post-§20), `UnitContext`, `Provenance`, `ConfidenceVector`.
- **`Provenance`** — normalized `(page, bbox)` in original page space **or**
  `(sheet, cell)` for spreadsheets, plus `label_bbox` (the row label's geometry, which
  unlike the value's box does not move when the figure changes — the review queue's
  judgement anchor depends on that) and recorded transforms.
- **`NotesTable` / `NoteItem`** and **`FaceNoteLink`** — backbone of §20.
- **`IntegrityReport`**, **`ReconciliationReport`**, **`StructuralReport`**, **`RuleResult`**,
  **`ReviewItemModel`**.

**`Table` / `Cell`** (`app/core/models/table.py`) were designed as the convergence point
where native and OCR pages meet. In the shipped pipeline they are **never populated** —
`DocumentModel.tables` stays empty, and the real convergence happens one level lower, at
`services/row_reconstruct.Word` → `LineItem`, which both the text-layer path and the OCR
path feed. The models are kept because the intent (one shape whatever the source) is what
`row_reconstruct` implements; nothing reads them today.

## Persistence (SQLAlchemy)

`app/db/models.py` — what actually exists: `Document`, versioned `TemplateVersion` /
`LineItemVersion`, `ExtractionRun`, `FxRate`, `ReviewJudgement`, `SettingOverride`
(SQLite by default; Postgres via `FINEX_DATABASE_URL`).

**`LineItemVersion` (`line_item_versions`) is the single versioned configuration store.**
There is no second one: the `ontology_versions` table stood here, held a separately
authored, separately selected rulebook, and is **gone** — line items is the one
configuration engine, so there is one table to version, one thing to publish and one
thing a run can pin. A row is `(line_items_key, version)` unique, carries the whole set as
`definition` JSON, and names the template it targets (`target_template_key`).

**`ExtractionRun.line_item_version_id` is the run's pin** — the exact configuration the
mapper read, so re-reading the row tells you precisely what a spread was mapped against
and the run is reproducible. It replaced `ontology_version_id`; nullable only because a
run recorded before the pin existed genuinely has no answer.

An extraction's **rows live inside `ExtractionRun.result`** as one JSON payload, not in
relational tables, and every manual edit is an overlay written back onto that payload
(`edited_slots` records exactly which `(basis, period)` slots a human typed into, machine
values snapshotted first so a revert is exact). `ExtractionRun` also carries `options`
(including the run's rulebook record and the stage list it was queued with), `progress`,
`logs`, `engine_version` and `run_number` — which is how a run stays reproducible and can
say which rulebook produced its figures.

**`ReviewJudgement`** is one human judgement on one review finding — the record that a
named person examined a finding's figures and recorded that they stand. It is keyed on
`(tenant_id, document_id, subject_key)`, where `subject_key` is the sha256 of the
finding's canonicalized *subject* rather than its id: two of the review-check builders
key their id on the extracted row's INDEX, so an id-keyed acceptance would silently move
onto a different line item after a re-run. `app/services/judgement.py` is the only place
in the codebase that hashes, and it holds the reasoning. A subject carries only what the
finding ASSERTS: no figure (a figure that moves means "stale, look again", not "different
finding") and nothing positional — not a row index, and not the rule id, because
`structural_checks._unique` disambiguates a repeated authored id with the entry's ordinal
among the rulebook's sentences, so deleting an unrelated sentence would otherwise
renumber a still-failing one and orphan its acceptance. Nor may a figure decide whether a
finding is EMITTED: a guard's `details.target` is derived from its violations, and while
it gated emission a failing guard could vanish from the queue by colliding with the
balance card's target. No column stores a digest, a
status or a count — all three are derived at serve time, because a derived value
persisted beside its source drifts from it.

Coverage is likewise NOT persisted: `run.result["structural"]` already holds every
relation row, and `app/services/coverage.py` recomputes the report from a stored run.

### Designed, not built

The fuller relational model (`Statement`, `LineItem`, `NotesTable`, `FaceNoteLink`,
`ReviewItem`, `EditEvent`, `RuleResult`, `Export` tables) is a design, and these
properties are what it is designed *for*. **None of it is implemented** — the paragraph
above describes what exists instead:

- **Reproducibility** — done, on `ExtractionRun` (document hash + template version +
  **line-item version** + engine version + the recorded rulebook).
- **Concurrency** — *not built*: no `row_version`, no `If-Match`, no `409` on a stale
  edit. An edit is last-write-wins onto the run payload. The append-only `EditEvent` log
  is likewise unbuilt; `services/audit.py` keeps a **process-local** run/LLM ledger
  (surfaced by `GET /projects/{id}/audit`) and says so in its own docstring.
- **Money** — *not as designed*: figures are persisted as **strings inside JSON** and
  parsed back with `Decimal` (the same reasoning `FxRate.rate` states — a binary float
  column cannot round-trip a rate or a figure exactly). There is no `NUMERIC(28,4)`
  column. The statement-level `units` multiplier is real (`UnitContext`).
- **Migrations** — *no Alembic*. `app/db/base.py::init_db` uses `create_all` plus a
  narrow, idempotent `_reconcile_schema` that adds missing `documents` columns and widens
  the dedup constraint, so an existing SQLite file keeps working. Template and line-item
  definitions carry a `schema_version` (and `schemas/loader.py` folds a v2 rulebook's
  section layer on load), but there is no lazy payload-migrator for stored run results.
  Dropping `ontology_versions` deliberately did **not** convert its rows: 41 of the 44
  stored there no longer loaded against their own schema, so a migration that drops them
  is the honest one.

## Template schema (`app/schemas/template.py`)

Ordered section→node tree. Subtotal/total nodes declare a `rollup {op, children}` so
subtotals are recomputed and compared to extracted values. Statement `identities`
(Assets = Liabilities + Equity, with tolerance) and `cross_statement_ties` (CF closing
cash = BS cash; P&L net income = equity movement) are declarative. Nodes carry
`label_i18n` for output parity. Duplicate canonical keys are rejected at load.

### Authoring a template as a workbook (`app/services/template_xlsx.py`)

JSON is the right shape for the pipeline and the wrong shape for the person deciding
what a spread should contain, so the template round-trips through Excel:
`GET /templates/{id}/xlsx` renders one row per line and `POST /templates/xlsx`
(multipart, admin) reads an edited workbook back as the **next version** of a template
key — nothing is overwritten, so an extraction that already ran still explains itself
against the version it used. **This is the whole of "add a template from the frontend"** —
the on-screen tree is read-only apart from per-node config; no drag-and-drop editor exists.

Two columns carry the decisions:

* **Kind** — `extracted` (mapped off the document), `calculated` (computed from other
  lines and never mapped), `heading`. A calculated line marked extracted would take a
  figure from the page instead of from its components, so this is the reviewable fact.
* **Calculated from** — the canonical keys a calculated line is made of, one per cell
  line. This is exactly what the structural checks recompute, so editing it changes
  what gets validated.

The reader is deliberately strict and names the offending row: a calculated line with
no components, an extracted line *with* components, a component key not in the sheet,
a duplicate canonical key, a line above its section heading, or an unknown statement
name are all `422` rather than a guess. Columns are matched on header TEXT, so a
reordered sheet or an extra notes column still reads correctly. `Identities` and
`Read me` sheets carry the statement equalities and the column contract, and
`GET /templates/xlsx/columns` serves that contract to the UI so the screen cannot
describe columns the API would reject.

## Reading a figure: one resolver (`app/services/periods.py`)

Every consumer of an extracted row — the statement view, the Excel export, the note
tables, the accounting checks, the ratios — has to answer the same two questions, and
answering them differently is how a prior-year figure gets printed under the current
year or a validated total stops being the total on screen. Both answers live here.

* `split_current_prior(values)` — a value that NAMES its period (`current`/`prior`)
  wins, and a period nothing was printed for stays `None`. The positional fallback
  (first value is current) applies **only** when no value names a period at all
  (columns read as `col0`/`col1`, or a matrix row's component columns). Filings print
  lines for one year only — a deposit pledged last year and released since — and the
  positional read reported that figure as this year's, inventing a current-year number
  the document never contained.
* `concept_value(group, basis, period)` — several printed lines legitimately map to one
  concept (three depreciation lines; a section's residual "Others"), so the default is
  their **sum**. A manual edit **replaces** it: entering 200 over a combined 150 shows
  200, not 350. Edits are recorded per `(basis, period)` in `edited_slots`, so
  correcting the consolidated current column does not restate the standalone or the
  prior one.

## Line-item schema (`app/schemas/line_items.py`)

**The one configuration schema.** A `LineItemSet` is both the output shape *and* the
matching rulebook — the merge that removed the separate ontology. It is *data, not code* —
versioned and hot-swappable per job.

Per item (`LineItemDef`, keyed on `key`): the output side — `label`, `in_output`, `parent`,
`rollup`, `order`, `namespace`; and the matching side — `aliases` + `aliases_i18n`,
`keyword_hints`, `regex_hints`, `exclude_hints`, `include_criteria` /`exclude_criteria`,
`pattern`, `sign_rule`, `confusable_with`, `match_priority`, `alias_matching`,
`extraction_mode`, `value_scope`, `residual_policy`, `note_source` / `note_use`, and the
derived-line pair `cascade` / `implemented_by`.

Top-level: `section_defaults` + per-item `inherits` (the section layer — the shipped set
declares `statement` on no item at all and inherits the whole gate from 18 entries),
`vocabulary` (a `MappingVocabulary`: `section_banners`, `umbrella_banners`, `scope_tokens`,
`statement_prefixes`, `statement_spellings`, `exclusive_vocabularies`, `families`,
`section_overrides`, `caption_transforms`, `caption_characters`), `residual_framework`
(which governs the residual sweep in `stages/residual.py`), `decomposition_rules` (the
face↔note tie + residual-as-"other"), `netting_rules`, `scope_selection` / `normalisation`
(which decide how a filing's COLUMNS are read), `validation` (`identities`,
`cross_concept_guards`, `section_reconciliation`), `worked_examples`,
`number_format_by_locale` (per-locale `NumberFormat`, which is what the value parser reads)
and `global_rules`.

**Cross-check on upload** (`schemas/loader.py`): every item `key` and
`decomposition.face_key` must resolve against the template; rollups/identities must
reference existing node ids; `unknown_keys` reports any key the schema does not declare
rather than dropping it in silence. Failures return `422` with the offending keys.

A configured **empty** value means empty — it is a declaration, never a cue to fall back to
a built-in default.

### Which line-item version is in force

More than one line-item version can target one template, so the choice is a resolver rather
than a convention. **The resolver is `select_for_template(session, template_key)` in
`app/services/ontology_select.py`** — the module keeps its old file name (an internal
symbol, renaming it buys nothing), but what it ranks is rows of `line_item_versions` —
**and it is the authority**: read it rather than any prose about it, here or elsewhere.
Whichever way it ranks, a run may still **pin** a version to reproduce an earlier spread —
that pin is `ExtractionRun.line_item_version_id` — and `rulebook_record` labels it
`in_force` / `pinned` / `superseded` / `engine_default` / `missing`.

*Where this is going, stated as a plan and not as behaviour:* the ranking is being
**simplified to "the latest version wins"** — one test instead of five, so that publishing a
newer set for a template is all it takes for that set to govern. **That is not what
the code does today.** `select_for_template` currently applies five tests in order (drop
declared/retired supersessions → the *shipped* key wins → a set that declares a
supersession beats one that declares none → the *incumbent* wins a tie → then version, then
key), which deliberately means an upload does **not** take over merely by arriving. Until the
simplification lands, a newly uploaded set governs only if it declares what it supersedes.

One thing a reader should not be misled about: the shipped-key test is what lets a revision
of the repo's own set reach a reader at all — see the retired-key list in
`app/sample/reference.py` for why a retired key cannot declare its own retirement.

The client keeps **no** competing answer to this question. It used to
(`queries.ts::ontologyInForce` ranked stored rulebooks itself, on a *different* test order
from the server, so the two could disagree and a run started from the client's pick was
stamped `pinned` rather than `in_force`). The picker and its ranking are gone with the
rest of the ontology surface: the server resolves, the client asks.

### The shipped reference data

One template and one line-item set ship, in `app/sample/templates/`:

| | key | contents |
|---|---|---|
| template | `output_csv_hk_v1` | the fixed output CSV spread — Hong Kong / China |
| line-item set | `output_csv_hk` (targets the template above) | **475 items** (462 `namespace: template` + 13 note-level `internal`), 18 `section_defaults`, **11 residual buckets** (`value_scope: exclusive_residual`), and the `vocabulary` block |

The residual buckets cover the balance-sheet sections plus equity, the P&L sections plus
OCI, and the cash-flow sections. The **tax charge deliberately has none** — a sweep bucket
there would absorb a line that belongs on a named tax item.

The 13 `internal` items are the note-level `sub__*` parts. They are parts *of* a line rather
than lines, so they are off-template by design; `475 - 13 = 462` is exactly what the
matcher's working view is built from (see
[01-extraction-pipeline](01-extraction-pipeline.md)).

`app/sample/reference.py::ensure_reference_data` **refreshes both into the database on
every startup**, publishing a new version whenever the shipped file differs from the newest
stored one (compared on canonical content, so identical content writes nothing). It is not
a one-time seed: it used to write v1 only when no version existed and then never look at
the files again, so four revisions of the shipped template never reached a running app. Two
consequences worth knowing: a version published **on a shipped key**
does not survive a restart (it is logged at WARNING when replaced — put lasting edits in
the repo's files or under a key of your own), and every shipped file is put through the
same gates `POST /templates` / `POST /line-items` apply *before* anything is written, so a
broken shipped file fails startup with its path named instead of poisoning a read path.
That last gate is why any new schema field must ship with data that satisfies it:
`reference.py` raises `ReferenceSeedError` at startup rather than boot with a seed that
does not validate.

## Validation engine (feeds the review queue)

Two pure, unit-tested modules, both run **at serve time** over a stored run rather than by
a persisted rule catalog:

* `services/checks.py` — the row-based checks over the flat statement rows: the balance
  identity (total assets == total equity and liabilities), subtotal rollups (a subtotal
  equals the item rows since the previous boundary), sign anomalies, and
  `check_reconciliation` over the reconcile stage's entries.
* `services/structural_checks.py` — the arithmetic the **template** and the **rulebook**
  declare: template rollups + statement identities, the rulebook's
  `validation.identities`, its `cross_concept_guards` and its `section_reconciliation`.
  Every relation emits a `RuleResult`-shaped row **including the ones it could not run**,
  each skip carrying a classifiable `reason` (`services/coverage.py` turns those into the
  coverage contract the UI shows), so partial coverage is visible rather than implied. A
  relation is evaluated only when the total and every declared component was extracted —
  unless the section owns a residual bucket, in which case an absent child is genuinely
  nil and is listed in `assumed_zero`. Nothing is derived or back-filled to make a
  relation balance.

`GET /documents/{id}/review` assembles the queue from those results plus the one
**row-shaped** finding — `unmapped`: a figure extracted from the FACE of a statement that
reaches no line of the output, whether because nothing claimed the caption or because it was
claimed for a concept the run's template declares nowhere. It serves the tabs (with the
check types each selects), the summary counts, `weak_mappings` (lines whose mapping is weak:
counted, never carded), the coverage block, the in-force judgements and `remap_targets`. The
accounting kinds are `balance`, `equity_tie`, `structural`, `calculated_mismatch` and
`containment_gap` (a printed combined total the lines that replaced it do not account for);
`_ACCOUNTING_TYPES` / `_ROW_SHAPED_TYPES` declare that set and `_assert_known_kinds` holds
every served card to it. There is **no `RuleDefinition` catalog and no
`ReviewItem` table**: the only persisted review state is the human verdict
(`ReviewJudgement`), which is deliberate — a finding is derived from the run, so storing it
would create a second copy to go stale.

## Formulas (safe)

No `eval`. Parse to a whitelisted AST (numbers, cell/`note:` refs, `+ - * /`,
`SUM/AVG/MIN/MAX/ABS/IF`); refs resolve only within the run. Per-run dependency graph
+ topological recompute; cycles surfaced as a cell error. This runs **server-side**
(`app/services/formula.py`) — the client sends and displays the expression string.

The Excel export does not build a live spreadsheet. A formula is carried for AUDIT, not
for recalculation: the flat extraction sheet has a `Formula` column holding the
expression as the analyst entered it (`build_rows_xlsx`), and the statement workbook
attaches it as a cell note on the row's label cell, beside the arithmetic of any
calculated line (`build_statement_workbook`). References are canonical line-item keys
(`bs_current_assets__inventories`), resolved server-side by `services/formula.py`, and
are never translated to cell addresses — the number in the cell is the value the server
computed.

## API (`/api/v1`, REST)

**Documents & extraction**
`GET /documents`, `POST /documents` (upload + integrity, hash-dedup per owner),
`GET /documents/{id}`, `DELETE /documents/{id}`,
`GET /documents/{id}/integrity`, `GET /documents/{id}/pages`,
`PUT /documents/{id}/scope`,
`POST /documents/{id}/extractions` (202 + `progress_url`),
`GET /extractions/{run_id}` (status + progress + **the run's stage list** + log tail +
result — the client polls this once a second while a run is `running`; **there is no
WebSocket**), `GET /documents/{id}/run`.

**Reading a run**
`GET /documents/{id}/statement`, `GET /documents/{id}/notes`,
`GET /documents/{id}/notes/{note_no}`, `GET /documents/{id}/analysis`,
`GET /documents/{id}/commentary`, `POST /documents/{id}/credit-narrative`,
`GET /documents/{id}/pages/{n}/image` (server-rasterized page PNG),
`GET /documents/{id}/cell-context?sheet=&cell=`,
`GET /audit` (`limit=`) is the deployment-wide run trail — every extraction and LLM run,
newest first, with its token usage and duration, from the durable `audit_log` table.
Admin only (`audit:view`): it is the one read that crosses document ownership. The
per-document trail stays at `GET /documents/{id}/audit`.

`GET /documents/{id}/export` (`fmt=excel|json|csv`, `layout=flat|statement`, `include=…`).
`csv` is the flat form for a consumer that is another program: one sheet, a row per line item and
a column per figure. It is as wide as the filing is — two columns ("Line item, Value") for a
statement that printed one column of figures, three headed by the years for one that printed two,
wider again with a column per (basis, period) pair when Group and Company are both extracted.
`layout` and `include` do not apply to it.

**Editing & review**
`PATCH`/`DELETE /documents/{id}/line-items/{key}` (value, formula, comment,
revert-to-extracted), `GET /documents/{id}/review` (findings + their human judgements +
the coverage contract + `remap_targets`),
`POST`/`DELETE /documents/{id}/review/judgements[/{subject_key}]` (accept a finding,
withdraw an acceptance), **`POST /documents/{id}/review/remap`** (re-file one printed row
onto a different template line — the action that RESOLVES a row-shaped finding; an empty
`canonical_key` un-maps it, an ambiguous `row_ref` is refused with `409` rather than
resolved to the first match).

**Configuration & identity**
`templates` (incl. `GET /{id}/xlsx`, `POST /xlsx`, `GET /xlsx/columns`, `GET /{id}/detail`)
and `line-items` (incl. `GET /schema`, `GET /skeleton`, `PATCH /{id}/mappings`,
`PATCH /{id}/netting-rules`) CRUD with validation. **There is no `/ontologies` router** —
it was the second configuration API and it is gone; everything a client needs about the
mapping configuration comes from `/line-items`, and an endpoint that took an
`ontology_version_id` takes a `line_item_version_id`. `GET`/`PATCH /settings`;
`POST /auth/login`, `POST /auth/logout`, `GET /auth/demo-users`, `GET /me`;
`GET /languages` (parity); the FX master (`GET /fx-rates`, `GET /fx-rates/resolve`,
`POST`, `PUT /{id}`, `DELETE /{id}`); and the seeded-sample `projects` router
(`GET /projects/{id}` + its statement/notes/review/commentary/audit reads,
`POST /projects/{id}/analysis`, `POST /projects/{id}/submit-review`,
`POST /projects/{id}/export`).

**Not built:** a push/WebSocket progress channel, `If-Match` optimistic concurrency on
edits, and `POST /statements/{id}/convert` — unit/currency conversion is a display
transform in the Workspace (backed by the FX master's `resolve`) plus an export-time unit
target.
