# Frontend (React 18 + TypeScript, Vite)

Implemented in `frontend/`, from the FinExtract design handoff. Run with
`cd frontend && pnpm install && pnpm dev` alongside the backend; the Vite dev server
proxies `/api` to FastAPI. See `frontend/README.md` for the file map.

## Stack — what is actually installed

`frontend/package.json` declares **five** runtime dependencies and nothing else:

| Dependency | Used for |
|---|---|
| `react` 18 | the app |
| `react-dom` 18 | rendering it |
| `react-router-dom` 6 | routing (`src/App.tsx`) |
| `@tanstack/react-query` 5 | all durable server data (`src/lib/queries.ts`) |
| `zustand` 4 | the ephemeral UI store (`src/store.ts`) |

Dev-only: `vite`, `@vitejs/plugin-react`, `typescript`, `@playwright/test`, and the React
type packages.

Everything else is hand-rolled, deliberately, and the file that owns it is the thing to
name when it needs changing:

- **The grid** — plain flex/grid rows built by `src/screens/Workspace.tsx` from the
  statement payload (`sections → items → subtotals/totals`, a note column, and one column
  per period), with `src/components/ui.tsx` supplying the shared primitives (`Card`,
  `ScreenHeader`, `Pill`, `ConfidencePill`/`confReadout`, `NoteChip`, `Button`, `Segmented`,
  `FieldChip`, `Toggle`, `StatusIcon`).
- **The source viewer** — `src/components/SourceViewer.tsx`. PDF pages are **rasterized
  server-side** (`GET /documents/{id}/pages/{n}/image`, PyMuPDF) and rendered as `<img>`
  in a lazily-loading page stack; the highlight is a percent-positioned box over the page
  holder. Spreadsheet sources render a cell-context mini-grid instead.
- **i18n** — `src/i18n.ts` (core: shell, workspace, common terms) merging the per-screen
  dictionaries in `src/i18n/screens/*.ts`, exposed as `useT()`. `RTL_LOCALES` and
  `NATIVE_NAME` live there too.
- **Design tokens** — `src/theme.ts` (`color`, `font`, `radius`, `shadow`, `layout`,
  `confStyle`, plus the number helpers `fmtIN` / `fmtPlain` / `parseAccounting`). Components
  style inline from those constants; there is no CSS framework and no component library.
- **API types** — `src/types.ts`, hand-written TypeScript mirroring the backend payloads.
  There is no runtime schema validation at the boundary.
- **Excel** — written **server-side** by `backend/app/services/export.py` (openpyxl) and
  downloaded via `GET /documents/{id}/export?fmt=excel`. The client never builds a workbook.

### Libraries that were planned and not adopted

Earlier revisions of this document described **AG Grid, PDF.js, HyperFormula in a Web
Worker, ExcelJS, Tailwind, Radix, dnd-kit, React Hook Form, Zod and react-i18next** in the
present tense. **None of them is installed.** The design intent behind each is worth
keeping, so here is what replaced it:

| Intended | What ships instead |
|---|---|
| AG Grid (tree data, column groups, cell renderers) | hand-rolled rows in `Workspace.tsx`; sections/subtotals come from the template's own order, basis is a `Segmented` toggle rather than a column group |
| PDF.js (client-side viewport transform) | server-rasterized page PNGs + a percent-positioned overlay (`SourceViewer.tsx`) — no client PDF engine, no DPR/zoom maths |
| HyperFormula in a Web Worker | formulas are **strings evaluated server-side** by `backend/app/services/formula.py` (whitelisted AST); the client sends and displays the expression |
| ExcelJS | server-side openpyxl (`services/export.py`) |
| Tailwind + Radix | `src/theme.ts` tokens + `src/components/ui.tsx` primitives, inline styles |
| dnd-kit (template tree drag-and-drop) | the template is authored as an **Excel workbook** round-trip (`GET /templates/{id}/xlsx`, `POST /templates/xlsx`); the on-screen tree is read-only apart from per-node config |
| React Hook Form | plain controlled `useState` forms |
| Zod at the API boundary | hand-written types in `src/types.ts`; validation is the server's (422 responses are surfaced verbatim) |
| react-i18next | `src/i18n.ts` + `src/i18n/screens/*.ts` |

## State

- **TanStack Query** for durable server data — one hook per endpoint in `src/lib/queries.ts`.
- **Zustand** (`src/store.ts`) for the small ephemeral layer: output `locale`, the
  `uiLocalization` admin flag, the session `token`, `extractMode`, `activeDocumentId`,
  `dataset` (basis), `statement`, the selected row / note / expanded check, the selected
  template key and the export format. It is deliberately small — no zoom, no hover state,
  no dirty-edit buffer; edits go straight to the server and the query cache is invalidated.
- **React Router v6.** Routes are registered eagerly in `App.tsx`; there is no
  `React.lazy` code-splitting (there are no heavy PDF/grid bundles to split).

## Routes

`App.tsx` registers, all behind `RequireScreen` (which redirects a role away from a screen
`GET /me` says it cannot see):

```
/ → /workspace
/upload  ·  /integrity  ·  /scope  ·  /extraction  ·  /workspace  ·  /notes
/review  ·  /commentary  ·  /template  ·  /settings  ·  /export
/documents/:id → adopts :id as the active document, then redirects to /extraction
*              → /workspace
```

`src/screens/config.ts` is the single registry behind both the nav rail and the pipeline
stepper: `SCREENS` (11 entries), `NAV_GROUPS`, and `STEPPER` —
`Upload(1) → Integrity(2) → Scope(3) → **Extract(4) = /extraction** → Review(5) → Export(6)`.
Screens that are not themselves a step map to the step they sit at (`STEP_FOR_SCREEN`:
Workspace and All Notes sit at Extract, Analysis at Review), so the bar is never blank on
the screen an analyst spends most of their time on.

## Extraction screen (`/extraction`, `src/screens/ExtractionView.tsx`)

Step 4, and its own destination rather than something the Workspace happened to show. For a
caller who holds `pipeline:run` (analyst, admin) it starts — or adopts — the run for the
active document and **reports it while it is running**: a stage table built from the run's
own `stages` list, which stages are finished (`stages_done`), which one is in flight, pct,
elapsed time, and the tail of the pipeline log. Every figure is read from the served
`progress` record — a run the server has no progress for says *starting* rather than
printing a measured-looking 0%.

A caller without `pipeline:run` (a reviewer) would 403 on the POST, so the screen **reads**
the latest run instead, via `GET /documents/{id}/run`, and gets its rows, rulebook and
status. That endpoint serves no `progress` / `stages` / `log_tail`, and the progress panel is
rendered only on the run path, so the read-only reader sees no stage table — see
[07-rbac-and-commentary](07-rbac-and-commentary.md#role-based-access-control), where that gap
is called out.

**Progress is polled, not pushed.** `useExtraction` POSTs once per (document, line-item
version, template) and then polls `GET /extractions/{run_id}` at 1s while `status === "running"`.
There is no WebSocket anywhere in the product.

Once the run succeeds the screen lists the real line items with per-value confidence and
click-to-source; the rulebook the run **recorded** is named above the rows (never a
client-side guess), and a re-extract control (gated on `pipeline:run`) starts a fresh run
against a revised template or a different rulebook.

## Signature screen — side-by-side hyperlinked viewer

Left: the source page stack (`PageStack`) or the spreadsheet cell grid (`ExcelGrid`).
Right: the template grid. Highlights are drawn from **normalized [0..1] bboxes** as
percent-positioned boxes inside the page holder, so they survive any display scale with no
recompute — the y-flip is normalized server-side so the client never touches it. Pages load
lazily as they near the viewport (and the picked page loads immediately, rather than waiting
for the scroll → IntersectionObserver chain), so a 200+ page filing does not fetch every
page at once.

Selection is one-way today: clicking a figure drives the viewer. There is no
`focusRequest`-style bidirectional channel in the store.

### Both periods are figures

Each period's value carries its own provenance, so **both** columns are click-to-source:
clicking last year's number drives the viewer to the page last year's number was printed
on. A reviewer checks the comparative as much as the current year, and linking only the
current column left half the grid dead.

## Signature screen — editable grid with formulas

Sections → items → subtotals in the template's declared order, with a note column and the
consolidated/standalone basis as a toggle. The inspector along the bottom edge describes
the selected figure — its origin, its arithmetic, each contribution's page, the comment —
resolved **for the period on show**, not for the row.

Formulas are strings: the inspector's formula box is sent to
`PATCH /documents/{id}/line-items/{key}` and evaluated by `services/formula.py`
server-side; the number in the cell is the value the server computed. Confidence is shown
as a colour-coded pill plus a readout that names the band when nothing measured a
percentage. Units/currency switching is a **display-time transform only** — the stored
source-unit numbers are untouched, and a currency change is refused outright when the FX
master has no rate for the pair (`useFxRateResolution`).

### Editing

The inspector edits either period, and sends only the columns actually retyped — so
correcting this year does not restate last year as a manual value and detach it from
the page. A save closes the editor **only when the server accepted it**; a refusal
shows the server's own reason (wrong basis, unknown concept, bad formula) and stays
open. Closing unconditionally was indistinguishable from success: the figure simply
never changed and nothing said why. Rows that are not correctable figures — a computed
KPI, a line mapped to no concept, an equity movement — carry `editable: false` and get
no control, rather than one that cannot work.

## The grid serves the template's lines, and only those

`_build_statement` (backend) emits **every declared section and line of the run's template,
in the template's order, and nothing else** — extracted or not, so a gap is visible and
fillable. Two consequences:

* There is **no "Other extracted items" heading.** It used to append any row whose
  canonical key carried the statement's prefix but was not one of the template's declared
  children, which added lines the template does not define — and against a run pinned to a
  superseded template it re-appended the very lines whose position a template revision had
  corrected. It is gone, front and back.
* A mapped figure still never vanishes in silence. A row mapped to a concept the run's
  template puts on no statement is raised as an **`unmapped` review finding** carrying a
  re-map offer, so the queue both reports that the figure reaches no spread and offers the
  control that fixes it. (It had its own `off_template` kind once; it is now the second way
  into the one row-shaped category, because from the output's point of view "nothing claimed
  this caption" and "something claimed it for a concept this template does not declare" are
  the same fact — a figure on no line of the spread. The card names the concept when there
  is one, so the two remain distinguishable to the reader without being two categories.)

The **"Additional items"** Workspace view is likewise gone (`DERIVED_STATEMENTS` in
`src/types.ts` now lists `kpi` alone) — for the same reason: it was a second place that
rendered rows the template does not declare.

## Derived view: KPIs

The one Workspace view that is not a statement the document prints. Real-extraction only
(`DERIVED_STATEMENTS`), so the demo falls back to the balance sheet rather than asking for a
view the demo endpoint cannot serve. The ratio catalog is computed from this extraction,
current beside prior, grouped by category; every ratio lists the extracted figures it used,
each with its sign and its own page, so the arithmetic is checkable and an absent input is
visibly absent rather than silently zero. A ratio is not an amount: the response is
`presentation: "raw"`, rows carry pre-formatted `display1`/`display2` in their own unit
(×, %, days), and the currency/magnitude selectors are **absent rather than inert**. KPIs
are read-only — the fix for a wrong ratio is to fix the line items it came from.

## Review queue (`/review`, `src/screens/Review.tsx`)

THE QUEUE CARRIES EXACTLY THREE THINGS, and this is a product rule rather than an
implementation detail: an extracted figure from the **face** of a statement that reaches no
line of the output; a **validation rule** that failed (the balance identity, the equity tie,
the rulebook's relations and cross-concept guards); and a **section subtotal** that does not
match the values extracted under it — a printed subtotal its own components do not come to
(`calculated_mismatch`), a rollup relation that breaks (`structural`), or a printed combined
total the lines that replaced it do not account for (`containment_gap`). Anything that is
neither a defect nor actionable is kept out, because every card that is neither costs the
analyst the attention the three real ones need. `_ACCOUNTING_TYPES` / `_ROW_SHAPED_TYPES` in
`routes/documents.py` declare the legal kinds, the chips are built from them, and
`_assert_known_kinds` fails a card that is in neither — a kind with no chip is invisible
under every filter while still inside the "All" total.

`containment_gap` is worth naming because of what it replaced. A filing that prints a
combined caption AND its components on the face has printed the parent *gross*; filing both
would count the money twice, so `map_ontology._enforce_containment` un-files the parent
(clears its key, demotes it to a subtotal, records what replaced it). Where the components
do not add up to it, part of the printed figure is now on **no line**, and that has to be
reported. It used to be — by accident: the stage set `low_mapping_confidence` to route the
row to review, so the finding arrived as a card about a mapping's *strength*, describing a
row whose mapping was an exact match at 1.0. When that card went, the row still reached the
queue, now as `unmapped`, printing "— (no confident match)" about a caption the mapper had
recognised perfectly. On the real filing that was 5 of the 12 cards in the queue's most
important category. The card now says what happened, and the *arithmetic recomputed from the
served rows* decides whether it appears — not the stage's flag, so an analyst edit that
opens a gap is reported and one that closes it stops being.

**Tone is a severity, not a colour name** — `high` a failed check, `med` something to place
or confirm, `low` informational — and the three surfaces that read it now agree. They did
not: the TS type declared `indigo` (never sent), `toneColors` painted `low` red, the server
sent `high` for every failing kind and `low` for the row-shaped one, and the sample sent
`low` for its blocking balance card. On screen that meant every real failure rendered in the
informational colour while the queue's mildest finding was the only red thing on it.

Two card kinds were retired to that rule. A **weakly-mapped** row raises nothing — a
mapping's strength is a property of the mapping, shown on the row's own confidence badge in
the Workspace, and the right answer to a weak match is to match it better rather than to
bill an analyst for it. It is still *counted*: `weak_mappings` is served beside the findings
and stated in a sentence under the header, so narrowing the queue cannot read as the data
having improved, and both the "lines with no finding" tile and the commentary's
data-quality caveat still exclude those lines. A **calculated line with no extracted
components** likewise raises nothing — nothing disagrees with anything — and is reported in
the coverage band and in the line's own inspector note, which is where an un-checkable
relation belongs.

Findings grouped by type, with tabs the **server** defines (each tab carries the check
types it selects, so the client never filters by list position): All · Checks · Unmapped.
A tab is never served for a kind that cannot be emitted: the tabs partition the list, so a
chip selecting nothing renders as a permanent "0" that filters the list to empty — a control
that looks like a filter and is a dead end. Each card expands into the reconciliation breakdown, the
suggested fix, and up to three controls:

* **Accept / withdraw a judgement** (`review:resolve`) — a named person, a timestamp and a
  required reason, pinned to the *figures* judged, so a re-run that moves them reopens the
  finding as `stale` rather than leaving a stale acceptance standing. A finding the server
  serves as `conflict` gets no accept path at all, and says why.
* **Flip sign** (`extraction:edit`) — the one mechanical fix, offered only when the server
  resolved exactly one row to flip. Every other card gets a sentence saying the fix is
  manual instead of a button that would do nothing.
* **Re-map** (`extraction:edit`) — `POST /documents/{id}/review/remap` with the row
  reference and the target canonical key, which is what **resolves** the row-shaped finding
  (`unmapped`, by either route in). The candidate list is served once per payload
  as `remap_targets` (not per card); an empty target key un-maps the row, recording that it
  belongs to no template concept. The accounting findings carry no offer — a relation
  between several concepts gives no answer to which one to re-map.

## Configuration authoring (admin)

There is **no Template & Ontology screen** — no ontology picker, no ontology editor, no
ontology type and no ontology label anywhere in `frontend/`. A user configures the mapping
on the **Line Items** screen, because line items is the single configuration engine; a
second configuration surface is exactly what let a user configure line items and change
nothing.

**Template authoring** is two pages. **The index** (`src/screens/TemplateList.tsx`) lists
the template versions and carries the authoring desk: download a template as a workbook,
upload the edited workbook back as a new version, and upload a **line-item set** (JSON)
**against the template on screen** — which is the one it is validated against, so a rule for
a line the template does not define comes back naming the key. The workbook's column
contract is read from the endpoint that enforces it (`GET /templates/xlsx/columns`), so the
screen cannot describe columns the API would reject. **The detail page**
(`src/screens/Template.tsx`) is raised over the index and shows one version's structure
tree, per-node config and the netting policies.

**The Line Items screen** (`src/screens/LineItems.tsx`, route `/line-items`, gated on the
`line_items` screen permission) is the surface on which the configuration behind each output
line and its sub-line items can be READ — the parts each output line is assembled from, the
aliases and criteria that claim a caption, and the derivation cascade where a line is
derived. It reads `GET /line-items`. It is deliberately read-only for now, and says so on
screen: the definitions describe derivations that several services still compute
(`implemented_by` names which), so showing them before they drive everything is what lets
the configuration be checked against what the pipeline does.

## Extraction mode — auto vs. confirm page scope

Chosen on the Upload screen (`extractMode` in the Zustand store, default **auto**).
In **auto** mode the pipeline detects statement pages and extracts in one pass: the
Integrity screen's forward action becomes **Extract now**, and the Page Scope step is
skipped. In **confirm** mode the Integrity action is **Detect statement pages** → the Page
Scope screen, where the user reviews/adjusts detected pages before extraction. The choice
is sent to the backend as `confirm_scope` on the extraction run and persisted in the run's
options.

## Other screens

Login (shown until a session exists) and the top-bar user menu; integrity report (routing
gate before review); notes tab (all published notes + note-ref chips linking face↔note);
Analysis (`Commentary.tsx`) as a printable one-pager; Settings (admin); export dialog with
a live preview.

## Riskiest UI parts

Bbox alignment across zoom/DPR — mitigated by normalized coords + percent positioning +
server-side y-flip, which is why there is no client-side viewport transform to get wrong.
Keeping raw/override/formula/computed coherent with the units transform — the `present()` /
`parseAccounting` path in `Workspace.tsx` is the one place a figure is scaled for display,
and a `presentation: "raw"` payload (the KPIs) opts out of it entirely. Progress delivery —
a 1s poll of the run endpoint while it is `running` (`lib/queries.ts`); a push channel
would additionally need a REST snapshot on reconnect, and is not built. Excel fidelity is
the server's problem rather than the client's, which is where it belongs.
