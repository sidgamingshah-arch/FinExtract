/** Types mirroring the backend API responses (see backend/app/api/routes). */
import type { ConfCat } from "./theme";

export type Locale = "en" | "zh" | "ar" | "fr";
export type Role = "admin" | "reviewer" | "analyst";
export type Basis = "consolidated" | "standalone";
/** How the pipeline proceeds after the integrity check:
 *  - `auto`    — detect statement pages and extract in one pass (default).
 *  - `confirm` — pause on the Page Scope screen so the user reviews/adjusts
 *                the detected pages before extraction. */
export type ExtractMode = "auto" | "confirm";

export interface Me {
  authenticated: boolean;
  username: string;
  name: string;
  via: "session" | "role-header";
  role: Role;
  roles: Role[];
  permissions: string[];
  screens: string[];
}

export interface DemoUser {
  username: string;
  name: string;
  role: Role;
}
export interface LoginResponse {
  token: string;
  token_type: string;
  expires_in: number;
  user: { username: string; name: string; role: Role };
}

export interface CommentaryMetric {
  key: string;
  label: string;
  value: number;
  tone: "good" | "warn" | "bad";
}
export type TrendKind = "amount" | "ratio" | "percent";
export interface CommentaryTrend {
  key: string;
  label: string;
  kind: TrendKind;
  current: number;
  prior: number;
  delta: number;
  direction: "up" | "down" | "flat";
  favorable: boolean;
  tone: "good" | "warn" | "bad";
}
export interface Commentary {
  headline: string;
  assessment: string;
  metrics: CommentaryMetric[];
  trends: CommentaryTrend[];
  strengths: string[];
  weaknesses: string[];
  data_quality: string;
  basis: string;
}

/** One row of the audit log — a past LLM/extraction run and its token usage. */
export interface AuditEntry {
  run_id: string;
  entity: string;
  action: "analysis" | "extraction" | "submit_review" | string;
  provider: string;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  status: "succeeded" | "failed";
  /** How long the run took, in milliseconds, as measured by whoever ran it.
   *
   *  Null for something INSTANTANEOUS (a submission handed to a reviewer) and on entries recorded
   *  before the field existed — both render as "—", never as "0 ms", which would read as a measured
   *  run that took no time. This is the only place a FINISHED run's duration can be read: the
   *  extraction screen's live gauge shows `ExtractionProgress.elapsed_ms` while a run is in flight
   *  and is gone once results arrive. */
  duration_ms?: number | null;
  created_at: string;
}
export interface AuditResponse {
  entries: AuditEntry[];
}

/** Editable (non-secret) LLM configuration fields — the API key is never sent. */
export interface LlmConfigPatch {
  provider?: string;
  model?: string;
  base_url?: string;
  temperature?: number;
  max_tokens?: number;
  timeout_seconds?: number;
  api_key_env?: string;
}
/** One tunable extraction setting, as DESCRIBED BY THE BACKEND — bounds, step and an
 *  explanation of what it does. The Settings screen renders and validates from this, so the
 *  UI cannot disagree with what the API will accept. */
export interface ExtractionField {
  key: string;
  kind: "number" | "bool" | "choice";
  label: string;
  help: string;
  min: number | null;
  max: number | null;
  step: number | null;
  choices: string[];
}

/** Runtime-mutable settings an admin can PATCH. */
export interface SettingsPatch {
  ui_localization?: boolean;
  review_required?: boolean;
  seed_demo?: boolean;
  llm?: LlmConfigPatch;
  /** Restore the LLM configuration to what config.toml shipped. */
  reset_llm?: boolean;
  /** Mapping / reconciliation thresholds. Out-of-range values are refused by the API (422). */
  extraction?: Record<string, number | boolean | string>;
  /** Restore every extraction knob to the value config.toml shipped. */
  reset_extraction?: boolean;
}

export interface AppSettings {
  features: {
    ui_localization: boolean;
    review_required: boolean;
    seed_demo: boolean;
    default_output_locale: string;
    supported_locales: string[];
  };
  llm: {
    provider: string;
    model: string;
    temperature: number;
    max_tokens: number;
    timeout_seconds: number;
    base_url: string;
    api_key_env: string;
    key_configured: boolean;
  };
  ocr: { engine: string; languages: string[]; dpi: number };
  /** Runtime-tunable pipeline thresholds, keyed by knob name. Rendered from
   *  `extraction_fields` rather than a hardcoded list, so a knob added on the backend appears
   *  here with no frontend change. */
  extraction: Record<string, number | boolean | string>;
  /** What config.toml shipped for each knob — for "restore defaults" and for showing which
   *  values have been moved away from them. */
  extraction_defaults: Record<string, number | boolean | string>;
  extraction_fields: ExtractionField[];
  auth: { allow_role_header: boolean; demo_mode: boolean; session_ttl_minutes: number };
}

/** One rate from the admin-maintained FX master: 1 `base` = `rate` `quote` on `as_of`.
 *  `rate` stays a STRING end to end — the backend holds it as an exact decimal, and
 *  parsing it into a JS number here would be the one place drift could creep in. */
export interface FxRate {
  id: string;
  base: string;
  quote: string;
  rate: string;
  as_of: string;
  source: string;
  created_at: string | null;
  updated_at: string | null;
}
/** A rate submitted by the admin editor. `as_of` omitted means "as of today" (server-side). */
export interface FxRateInput {
  base: string;
  quote: string;
  rate: string;
  as_of?: string;
  source?: string;
}
/** The answer to "what is base→quote?". `resolved` false is a normal answer — the master
 *  holds no rate for the pair — and the caller must then refuse to convert. */
export type FxRateResolution =
  | {
      resolved: true;
      base: string;
      quote: string;
      rate: string;
      as_of: string;
      /** True when the rate is OUR arithmetic (a reciprocal), not a quote as entered. */
      derived: boolean;
      method: "direct" | "inverse";
      /** The currencies actually traversed, in the direction the master stores them. */
      path: string[];
      source: string;
      rate_id: string;
    }
  | {
      resolved: false;
      base: string;
      quote: string;
      reason: "no_rate_configured";
      detail: string;
    };
/** The views the Workspace can show: the four statements the document prints, plus `kpi` — the
 *  ratios computed off those statements, which is real-extraction only (nothing computes them for
 *  the demo payload).
 *
 *  There used to be a sixth, "Additional items", holding every extracted figure that reached no
 *  face statement. It is gone, and this comment described it for a while after: an off-template row
 *  is now a REVIEW finding carrying a re-map offer (`ReviewCheck.remap`), because a bucket the
 *  template does not declare is a figure nobody will ever reconcile — it looked extracted and was
 *  in fact unplaced. */
export type StatementKey = "balance_sheet" | "profit_and_loss" | "cash_flow"
  | "changes_in_equity" | "kpi";
/** Views that exist only for a real extraction (there is no demo data behind them). */
export const DERIVED_STATEMENTS: StatementKey[] = ["kpi"];
export type RowKind = "section" | "subhead" | "item" | "subtotal" | "total";
export type ExportFmt = "excel" | "json";

export interface Confidence {
  /** The BAND — the badge's colour, and its text when there is no measurement. */
  cat: ConfCat;
  /** The MEASURED confidence as a percentage (0–100). Optional, and it must stay optional: the
   *  real path omits the whole object when nothing scored the row (documents.py serves
   *  `confidence: null` rather than a band beside a made-up 60), and any payload that knows only
   *  the band should serve the band alone — components/ui.tsx::confReadout then names it instead of
   *  printing a figure nothing measured. */
  pct?: number | null;
}

export interface Inspector {
  tag: string;
  src: string;
  formula: string;
  result: string;
  note: string;
}

/** One printed line that went into a combined figure.
 *
 * Several printed lines legitimately share one concept — three depreciation lines, two tax
 * payments, whatever a section's residual "Others" bucket absorbs. The combined figure then
 * matches no single line on the page, so each contributor carries its own values and its own
 * source location and can be traced back to where it was printed. */
export interface RowContribution {
  label: string;
  canonical_key?: string | null;
  v1: number | null;
  v2: number | null;
  method?: string | null;
  /** Routed here because nothing more specific matched, rather than positively identified. */
  residual?: boolean;
  src: string;
  source?: ExtractionProvenance | null;
  /** …and the same for the prior period, printed in its own column on its own page. */
  src2?: string;
  source2?: ExtractionProvenance | null;
  /** Whether the figure above ADDS this line. False for a fact the filing printed twice — on the
   *  face and restated in a note, or on two statements — which is one fact with two references,
   *  not two amounts to sum. Per period, because the figure is. */
  counted?: boolean;
  counted2?: boolean;
}

export interface StatementRow {
  id: string;
  label: string;
  source_label?: string; // original-language label for the source "paper"
  kind: RowKind;
  level?: number;
  note?: string | null;
  note2?: string | null;
  /** Where the figure was PRINTED: on the face of a statement, or inside a note. A different
   *  question from `origin` below, which says whether the figure was read off the document or typed
   *  by an analyst — a row can be printed on the face and still carry a manual value. It matters
   *  because a note's detail lines sum to a figure the face already reports, so a reader adding
   *  both double-counts the filing. */
  printed_in?: "face" | "notes" | null;
  /** Which of the analyst sections this row belongs to — one of the thirteen the face of the
   *  statements is read in, or `others`. `bucket` is the key, `bucket_label` the wording to show. */
  bucket?: string | null;
  bucket_label?: string | null;
  /** The notes that DETAIL this figure: every note the contributing lines cite AND the run actually
   *  extracted, so a chip here always has a note behind it. `note`/`note2` above are what the page
   *  printed in its note column, which is a promise the filing makes rather than one this
   *  extraction can keep. */
  notes?: string[] | null;
  status?: "flag" | "recon" | "edited" | "missing" | null;
  confidence?: Confidence;
  editable?: boolean;
  formula?: string | null;
  inspector?: Inspector;
  /** Present only when more than one printed line was combined into this figure. */
  contributions?: RowContribution[] | null;
  v1: number | null;
  v2: number | null;
  /** Matrix layouts only: the row's figures keyed by column name (see StatementResponse.layout).
   *  v1/v2 stay null there, because a component is not a period. */
  cells?: Record<string, number | null> | null;
  source?: ExtractionProvenance | null;   // real docs: current-period value's source location
  source2?: ExtractionProvenance | null;  // …and the prior period's, which is a figure too
  /** Pre-formatted figures in the row's OWN unit (a KPI's ×, %, days). When present the grid
   *  renders these verbatim and applies no currency/magnitude presentation — scaling a current
   *  ratio into "thousands" would be nonsense. */
  display1?: string | null;
  display2?: string | null;
  /** Where the displayed figure came from:
   *  - `extracted`            — read off the document (the ordinary line);
   *  - `calculated`           — computed from the components the template declares;
   *  - `manual`              — a value an analyst typed, which outranks both;
   *  - `reported_uncomputed`  — a calculated line none of whose components were extracted, so
   *                             the printed figure is shown UNVERIFIED and is in the review queue.
   */
  origin?: "extracted" | "calculated" | "manual" | "reported_uncomputed";
  /** Per-period origin. The row-level `origin` is a summary for the chip; these are what each
   *  column actually is, and they can differ — a figure corrected this year says nothing about
   *  last year, and a period whose components were not extracted is not made computable by the
   *  other period's being so. */
  origin1?: StatementRow["origin"];
  origin2?: StatementRow["origin"];
  /** How the figure was reached, rendered for READING ("12,800 + 2,150 + 3,410"). Display only:
   *  it is not an expression, and sending it back as `formula` would have the server evaluate it
   *  and overwrite whatever the analyst typed. */
  arithmetic?: string | null;
  /** What the DOCUMENT printed for a calculated line. Never the line's displayed value — a
   *  subtotal that contradicts its components is a finding, not a figure — but kept so the
   *  divergence can be stated. */
  reported1?: number | null;
  reported2?: number | null;
  /** What the components come to, present even when a manual value is displayed instead. */
  calculated1?: number | null;
  calculated2?: number | null;
  /** Why a figure was overridden, per period ("current" / "prior"), with who and when. */
  comments?: Record<string, { text: string; by?: string; at?: string }> | null;
}

/** A named column of a matrix statement — an equity component, not a period. */
export interface StatementColumn {
  key: string;
  label: string;
}

export interface ViewerMeta {
  company: string;
  subtitle: string;
  chips: { label: string; active: boolean }[];
  callout: string;
}

export interface StatementResponse {
  statement: StatementKey;
  label: string;
  /** The basis these figures ACTUALLY are — not necessarily the one that was asked for. A statement
   *  whose rows carry only one basis is served for either request, because a filing that labelled
   *  one basis drew no distinction to filter on and the alternative was an empty grid. */
  basis: Basis;
  /** The basis the request asked for. Absent on responses stored before the field existed. */
  basis_requested?: Basis;
  /** True when `basis` is not `basis_requested` — the figures are the only ones the document has,
   *  served in answer to a different question. Surfaced rather than swallowed: showing the
   *  Company's figures under a tab captioned Consolidated without saying so mislabels a real
   *  number, which is worse than the empty grid this replaces. */
  basis_substituted?: boolean;
  /** Why: "requested" (no substitution) or "only_basis_in_document". */
  basis_reason?: string;
  /** Shape of the statement. "matrix" means the columns are NAMED (equity components, not
   *  periods) and every row carries `cells` instead of v1/v2. Absent means the two-column
   *  comparative default. */
  layout?: "comparative" | "matrix";
  /** Matrix layouts only: the named columns, in the order they are printed. */
  columns?: StatementColumn[];
  periods: string[];
  currency: string;
  currency_symbol: string;
  units: string;
  units_scale_factor?: number;  // detected source magnitude (e.g. 1000 for "in thousands"); default 1
  /** "raw" means the rows carry their own formatted figures (display1/display2) and must not be
   *  re-scaled or re-currencied. Absent means ordinary monetary presentation. */
  presentation?: "raw" | "monetary";
  rows: StatementRow[];
  /** Why this statement is serving no rows, or null while it is serving them.
   *
   *  An empty grid has more than one cause and they need telling apart: the run's template declares
   *  no statement of this type at all, or it declares one and nothing was extracted for the
   *  requested basis. "Nothing here" reads as "the extraction found nothing", which sends an analyst
   *  back into the document after figures that were never going to be shown. `viewer.callout`
   *  carries the same sentence, but only one of the Workspace's three viewer branches renders the
   *  callout — the paper preview, which a real PDF or workbook never takes — so the reason has to be
   *  readable where the rows would have been. */
  refused?: StatementRefusal | null;
  /** Set when the run's template version is no longer the newest stored one for its key. Stated by
   *  the server, never acted on: a re-extract is the analyst's call. */
  superseded_template?: SupersededTemplate | null;
  viewer: ViewerMeta;
  format?: string;       // real docs: "pdf" | "xlsx" | … → chooses the live source viewer
  page_count?: number;   // real docs: page count for the PDF viewer
}
export interface StatementRefusal {
  /** Machine-readable, so the client can branch without matching on prose. */
  reason: string;
  /** The reader's sentence, already localized by the server. */
  message: string;
}

export interface Project {
  id: string;
  entity: string;
  title: string;
  filename: string;
  pages: number;
  standard: string;
  currency: string;
  currency_symbol: string;
  units: string;
  periods: [string, string];
  bases: Basis[];
  /** Both counted by the server from the data it serves: `line_items` is the statement rows that
   *  are line items, `in_review` is the review route's own open count. There is no `pct` — "how
   *  far through the workflow is this project" has no source anywhere in the payload, so it is
   *  absent rather than served as a figure derived from nothing. */
  progress: { line_items: number; in_review: number };
  template: { key: string; name: string; line_items: number };
  ontology: { file: string; rules: number; aliases: number; status: string };
}

export interface SourceDoc {
  id?: string;
  name: string;
  ext: string;
  meta: string;
  tag: "Mixed" | "Native" | "Scanned";
}
export interface ProjectResponse {
  project: Project;
  documents: SourceDoc[];
  loaded: boolean;
}

/** Provenance of an extracted value — sheet+cell (Excel) or page+bbox (PDF). */
export interface ExtractionProvenance {
  source_kind: string;
  page_index: number;
  sheet: string | null;
  cell: string | null;
  label_cell: string | null;
  bbox: { x0: number; y0: number; x1: number; y1: number } | null;
  text_snippet: string | null;
}
export interface ValueConfidence {
  mapping: number;
  validation: number | null;
  overall: number;
  weakest: number;
  flags: string[];
}
export interface ExtractionValue {
  period_label: string;
  value: string | null;
  provenance: ExtractionProvenance | null;
  confidence?: ValueConfidence;
}
export interface ExtractionRow {
  source_label: string;
  canonical_key: string | null;
  note: string | null;
  role: string;
  mapping_method: string | null;
  mapping_confidence: number | null;
  flags: string[];
  values: ExtractionValue[];
}
export interface SourceUnits {
  currency: string;
  scale_factor: number;
  units_label: string | null;
}
/** WHICH rulebook a run read the filing against — as the RUN recorded it when it started, not as
 *  a reader works out afterwards which one "must" have been in force.
 *
 *  The distinction is the whole point. The client asks for a rulebook by id; between that request
 *  and someone reading the result, a new rulebook can be published, so a screen that re-derives
 *  "in force" from the ontology list labels a run with a rulebook it never used — which is how a
 *  reload came to describe a superseded run as the current one. `status` is the server's own claim
 *  about the rulebook the run used: `in_force` (it WAS the one in force for its template when the
 *  run started), `superseded` (a stored rulebook declares it replaced — legitimate to pin, never
 *  current), `pinned` (live, but not the one in force), `engine_default` (the run named no
 *  rulebook) or `missing` (the id named nothing stored). See
 *  backend/app/api/routes/extractions.py::rulebook_record. */
export interface RulebookRecord {
  ontology_version_id: string;
  ontology_key: string;
  version: number;
  target_template_key: string;
  status: "in_force" | "superseded" | "pinned" | "engine_default" | "missing";
  in_force: boolean;
  /** The rulebook that WAS in force for the template when the run started, so a run that used
   *  another one can name what it departed from. */
  in_force_ontology_key: string;
  in_force_version: number;
  /** False when the recorded rulebook's stored definition would not load: it governed nothing,
   *  however firmly the run names it. Present on the finished result, not on the start response. */
  applied?: boolean;
}
export interface ExtractionResult {
  locale: string;
  /** The rulebook this run read the filing against (see RulebookRecord). */
  rulebook?: RulebookRecord | null;
  format: string;
  filename: string;
  entity?: string | null;
  page_count?: number;
  /** How many LINES the run produced — `len(doc_model.line_items)`, the mapper's promoted subtotals
   *  included. The screen captions it "lines" (`ex.count`) for that reason: "line items" is the
   *  narrower population the TEMPLATE declares and the sample's own count reports, and one word for
   *  two populations is how the Export footer came to overstate what it had counted.
   *
   *  The FIELD keeps its name deliberately, here and in the JSON export's `line_item_count` /
   *  `line_items`: renaming a key in a downloadable artifact changes a schema people have already
   *  saved, for no gain a reader of the file can see. The label is what made a claim; it is the
   *  label that was wrong. */
  line_item_count: number;
  notes: number;
  rows: ExtractionRow[];
  units?: SourceUnits | null;
  /** How ontology mapping actually ran. "deterministic" means the LLM was unavailable and the
   *  weaker rule/alias ensemble decided — surfaced so a degraded run is visible, not implied. */
  mapping?: { strategy: string; reason: string; llm_calls: number; model: string } | null;
}
/** How far the pipeline has got, as the run row records it.
 *
 *  The whole shape lives in the run's existing `progress` JSON column rather than in new table
 *  columns: `init_db` uses `create_all`, which never adds a column to an existing SQLite file, so
 *  new columns would break every database already on disk.
 *
 *  `stage` is the pipeline stage's own name (`"map_ontology"`, `"residual"`, …) — served, never
 *  guessed client-side, because the stage list is assembled in `core/pipeline.py` and the docs have
 *  already been wrong about it once. Empty/absent while a run is still queued. */
export interface ExtractionProgress {
  /** "queued" | a stage name | "done" | "failed". */
  phase: string;
  pct: number;
  stage: string;
  stage_index: number;
  stage_count: number;
  /** The stages already finished, in order, so the screen can tick them off. */
  stages_done: string[];
  started_at: string;
  elapsed_ms: number;
}
/** One statement a template declares. `key` is a `StatementKey`; the LABEL is deliberately not
 *  taken from `title` — `ws.stmt.*` is translated in every shipped locale and the server's title is
 *  English, so `title` is only a last resort for a key this build has no translation for. */
export interface TemplateStatement {
  key: StatementKey;
  title: string;
  sections: number;
}

export interface ExtractionRunResponse {
  run_id: string;
  status: string;
  /** Alongside the result rather than inside it, so the rulebook can be named from the first poll
   *  — while the run is still running, and even when it fails without producing a result. */
  rulebook?: RulebookRecord | null;
  /** Declared, and now populated. The field was always served by `GET /extractions/{run_id}` and
   *  never declared here, so no component could reach it without widening this interface first —
   *  which is one of the three reasons extraction progress reached no screen. */
  progress?: ExtractionProgress | null;
  /** The stage names this run will pass through, in order, as the pipeline assembles them. */
  stages?: string[];
  /** The tail of the run log, flushed as stages complete rather than only at the end. */
  log_tail?: string;
  /** The bases this filing actually labelled, read off the run's own rows. The Workspace opens on
   *  one of these rather than always on Consolidated: a filing that printed one column has one
   *  answer, and offering a choice between two when only one exists invites a click on the empty
   *  one. Absent or empty means the run cannot say, and the built-in pair is offered. */
  bases?: Basis[];
  /** The statements THIS RUN's template declares, in the template's own order — what the Workspace
   *  builds its statement tabs from. Read off the template the run was pinned to, so publishing a
   *  new template cannot change the tabs above an existing spread. Absent or empty means the run
   *  cannot say (no template pinned, or a run stored before this field existed) and the caller
   *  falls back to the built-in set — it does NOT mean the template declares no statements. */
  statements?: TemplateStatement[];
  result: ExtractionResult;
}
/** Whether a document has an extraction IN FLIGHT, and how far it has got.
 *
 *  `/documents/{id}/run` answers 404 until a run has a RESULT, so a screen loaded fresh mid-run
 *  could not tell "extracting" from "never extracted" — it had to say the second, which is the
 *  wrong answer and the one that reads as "nothing happened". This is the per-document question,
 *  answerable without knowing a run id: a hard reload onto the Workspace has no run id to poll. */
export interface DocumentRunStatus {
  /** "none" | "running" | "succeeded" | "failed". */
  status: string;
  run_id: string;
  progress?: ExtractionProgress | null;
}

/** A run whose template version is no longer the newest published one for that template key.
 *
 *  A run is PINNED to the template it was launched against, so a spread built before the template
 *  was revised keeps rendering the old shape — which is exactly how a corrected line order can
 *  still look wrong on screen. Stated by the server so the screen can say it out loud and offer a
 *  re-extract, rather than a stale spread being indistinguishable from a current one. */
export interface SupersededTemplate {
  superseded: boolean;
  run_version: number;
  latest_version: number;
  template_key: string;
}
/** Derived analysis from a real extraction: ratios, disclosure scan, free-form notes. */
export interface Ratio {
  key: string;
  label: string;
  category: string;
  unit: string;
  formula: string;
  value: number | null;
  display: string;
  available: boolean;
}
export interface Disclosure {
  key: string;
  label: string;
  present: boolean;
  page: number | null;
  snippet: string;
}
export interface FreeNote {
  title: string;
  text: string;
}
export type CreditTone = "strong" | "adequate" | "weak";
export type CreditStance = CreditTone | "insufficient";
export interface CreditFactor {
  category: string;
  category_key: string;
  key: string;
  label: string;
  value: number | null;
  display: string;
  unit: string;
  tone: CreditTone;
  tone_label: string;
}
export interface CreditFlag {
  key: string;
  label: string;
  severity: "severe" | "high" | "watch";
  implication: string;
  page: number | null;
  snippet: string;
}
export interface CreditNarrative {
  text: string;
  provider: string;
  model: string;
}
export interface CreditAnalysis {
  stance: CreditStance;
  stance_label: string;
  factors: CreditFactor[];
  flags: CreditFlag[];
  summary: string;
  basis: string;
  narrative?: CreditNarrative;  // cached LLM narrative, when present
}
export interface AnalysisResponse {
  ratios: Ratio[];
  disclosures: Disclosure[];
  notes: FreeNote[];
  credit?: CreditAnalysis;
}

/** A window of spreadsheet cells around a value's origin (Excel click-to-source). */
export interface CellContextCell {
  ref: string;
  value: string;
  is_target: boolean;
  numeric: boolean;
}
export interface CellContext {
  sheet: string;
  target: string;
  col_letters: string[];
  row_numbers: number[];
  grid: CellContextCell[][];
}
export interface OntologyRef {
  id: string;
  ontology_key: string;
  target_template_key: string;
  /** Edits to THIS rulebook. It counts revisions of one key, so it cannot rank two different
   *  rulebooks that target the same template — which is what `superseded` is for. */
  version: number;
  schema_version?: number;
  /** The ontology_key this one replaces, as its own author declared. */
  supersedes?: string | null;
  /** True when another rulebook that is actually present declares it replaces this one. */
  superseded?: boolean;
  /** True for the ONE rulebook per target template that the next run will map against.
   *
   *  Served by the server, never re-derived here. The rule is "whatever was stored last wins", which
   *  needs `created_at` — a field this payload does not carry and should not, because then two
   *  implementations of the rule would exist and could disagree. The client used to rank the list
   *  itself on `[supersedes, version, ontology_key]` under a comment claiming it mirrored the
   *  server's picker; it did not, so the screen could name a different rulebook than the one a run
   *  actually used. Read this flag; do not sort. */
  in_force?: boolean;
  /** How big the rulebook is: concepts it declares, and aliases across every locale. Counted by
   *  the server off the stored definition, so a screen describing a rulebook's size never has to
   *  invent one. */
  concept_count?: number;
  alias_count?: number;
}
export interface TemplateRef {
  /** The TEMPLATE VERSION's id. This is what identifies a template to a run
   *  (`template_version_id`), and what a selection has to store — a `template_key` cannot
   *  distinguish v1 from v4, which is exactly how selecting a version came to be impossible. */
  id: string;
  template_key: string;
  name: string;
  version: number;
  is_published: boolean;
  /** True for the newest version of this key. Served by the server (`routes/templates.py`) so the
   *  client never ranks versions itself — the same contract as `OntologyRef.in_force`. Read it to
   *  default a selection; do not sort. */
  is_latest?: boolean;
}

export interface IntegrityStat {
  label: string;
  value: string;
  sub: string;
  tone: "neutral" | "warn" | "ok";
}
export interface IntegrityIssue {
  title: string;
  detail: string;
  pages: string;
  note: string;
  status: string;
  severity: "warn" | "ok" | "low";
}
export interface IntegrityResponse {
  score: number;
  grade: string;
  summary: string;
  stats: IntegrityStat[];
  issues: IntegrityIssue[];
}

export interface PageCard {
  no: number;
  kind?: "face" | "notes" | "other";
  cls: string;
  sub: string;
  /** The BUCKET the classifier's confidence fell into — a colour, not a quantity. */
  conf: ConfCat;
  /** The MEASURED classification confidence as a percentage (0–100), or null/absent when the
   *  classifier recorded none. The tile printed `confStyle(conf).pct` — 96/78/54 by bucket — as
   *  though it were this figure, so a page scored 0.40 read "54%" and an unscored page read "78%".
   *  Absent means unscored, and the tile says so rather than printing a bucket's stand-in. */
  conf_pct?: number | null;
  included: boolean;
  scan: "native" | "scanned";
  /** The page number PRINTED ON THE PAGE, when it prints one. `no` above is the page's position in
   *  the FILE — what every index in this product means and what the viewer scrolls to — and the two
   *  differ by however much front matter the report has. Null when the page carries no folio. */
  printed?: string | null;
}
export interface PagesResponse {
  pages: PageCard[];
  filters: { label: string; count: number }[];
  focused: number;
  total: number;
  skipped: number;
}

/** One hit from searching the source document's text layer. The box is in the same normalized
 *  page space as provenance, so a hit is highlighted by the same overlay a picked value is. */
export interface DocSearchHit {
  page_index: number;
  printed_page?: string | null;
  bbox: { x0: number; y0: number; x1: number; y1: number };
  snippet: string;
}
export interface DocSearchResult {
  query: string;
  hits: DocSearchHit[];
  count: number;
  /** True when the cap was reached — "these are the first n", not "these are all". */
  truncated: boolean;
  /** Pages with no text layer. They cannot be searched, and saying so is not the same as
   *  reporting no matches. */
  scanned_pages: number;
}

export interface ReviewCalcRow {
  0: string;
  1: string;
  2: boolean;
}
/** The one mechanical correction the product offers: flip a mis-signed figure. DERIVED BY THE
 *  SERVER — the client can never invent a fix, and most checks carry `fix_action: null` because
 *  no single edit is implied (a balance identity has two sides; a wrong subtotal must not be
 *  overwritten with the printed figure, which would hide the mis-mapped component).
 *
 *  Applying it is an ORDINARY edit — the same PATCH the Workspace uses — so the flip snapshots
 *  the original (the existing revert undoes it), records WHY in the edit comment, and re-derives
 *  every value-driven check. Both figures arrive pre-formatted: the browser formats no number. */
export interface FixAction {
  kind: "flip_sign";
  canonical_key: string;
  basis: Basis;
  period: string;
  label: string;
  from: number;
  to: number;
  from_display: string;
  to_display: string;
  comment: string;
}

/** The in-force judgement on a finding — a named person examined these figures and recorded
 *  that they stand. It does NOT mean the check passed, which is why an accepted card stays in
 *  the list rather than disappearing.
 *
 *  `accepted_rows` is the evidence AS JUDGED, localized and formatted server-side in the same
 *  [label, value] shape as `ReviewCheck.calc` so one renderer serves both. `changed` /
 *  `changed_label` are populated only when the figures have moved since — the check is then
 *  `stale`, which is the withdrawal of an acceptance made visible instead of silent. */
export interface CheckJudgement {
  verdict: "accepted";
  actor: string;
  actor_role: string;
  at: string;
  reason: string;
  run_id: string;
  accepted_rows: [string, string][];
  changed: string[];
  changed_label: string;
}

/** A judgement whose finding no longer exists in this run — corrected, or gone. Never
 *  auto-deleted (erasing who accepted a break is not something an audit trail permits), so the
 *  screen states that N prior judgements match nothing here rather than dropping them. */
export interface OrphanedJudgement {
  subject_key: string;
  subject_label: string;
  actor: string;
  actor_role: string;
  at: string;
  reason: string;
}

/** Coverage of ONE statement's template relations: how many could be evaluated at all, not how
 *  many passed. `validation_rate` / `coverage_rate` are the only rates the payload carries and
 *  are null together when nothing was evaluable — the band renders the two FRACTIONS instead,
 *  because a single rate shown alone reads as a score. */
export interface CoverageStatement {
  statement: string;
  label: string;
  passed: number;
  failed: number;
  skipped: number;
  evaluated: number;
  declarable: number;
  validation_rate: number | null;
  coverage_rate: number | null;
  skips: Record<string, number>;
  status: string;
  status_label: string;
}
/** One reason relations could not be evaluated, with what that reason MEANS. `counts_in_denominator`
 *  false marks a bucket excluded from `declarable` (the filing has no such statement). */
export interface CoverageSkip {
  bucket: string;
  count: number;
  label: string;
  meaning: string;
  counts_in_denominator: boolean;
}
/** A named defect in the coverage itself. `assurance_gap` marks the worst kind: a relation
 *  declared blocking that cannot run as authored, so it fires on no filing at all. */
export interface CoverageAlarm {
  code: string;
  label: string;
  rule_id: string | null;
  statement: string | null;
  text: string;
  assurance_gap: boolean;
}
/** The coverage contract, recomputed at serve time from the run's stored relation rows.
 *
 *  Unavailability is STATED, never rendered as zeros: "0 of 0 relations evaluated" is exactly
 *  the misread the coverage report exists to prevent, so the two variants are disjoint and the
 *  numbers only exist on the available one. */
export type CoverageBlock =
  | {
      available: false;
      reason: "not_extracted" | "no_template" | "no_relations" | "sample";
      reason_label: string;
    }
  | {
      available: true;
      run_id: string;
      engine_version: string;
      aggregate: CoverageStatement;
      statements: CoverageStatement[];
      skips: CoverageSkip[];
      alarms: CoverageAlarm[];
      /** Failed relations suppressed from the card list because their target already has its own
       *  finding — why the band's `failed` can exceed the number of structural cards above it. */
      failed_reported_elsewhere: number;
    };

export interface ReviewCheck {
  id: string;
  type: string;
  icon: string;
  title: string;
  where: string;
  severity: string;
  tone: "low" | "med" | "indigo";
  delta: string;
  target: string;
  calc: [string, string, boolean][];
  fix: string;
  /** WHAT was judged, and the figures it was judged against — both locale-free, so one
   *  acceptance holds in all four languages. `subject_key` is the identity a judgement is
   *  keyed on; `id` never is, because two of the check builders key on row index and an
   *  id-keyed acceptance would silently land on a different line item after a re-run.
   *  Null on the sample path, which carries no judgements at all. */
  subject: Record<string, unknown>;
  subject_key: string | null;
  evidence: Record<string, unknown>;
  evidence_digest?: string;
  /** "open" — nobody has recorded a judgement (the distinction that was missing);
   *  "accepted" — an in-force judgement against THESE figures;
   *  "stale" — accepted earlier against DIFFERENT figures, so it counts as outstanding work;
   *  "conflict" — this finding shares its subject_key with another that printed DIFFERENT
   *  figures, so identity cannot tell them apart and no judgement may be attributed to either.
   *
   *  The union is open-ended on purpose: a server that has learned a state this build does not
   *  know must not be assumed acceptable. The screen whitelists the states it can act on and
   *  treats anything else as non-judgeable — see KNOWN_STATUS / ACCEPTABLE_STATUS in
   *  screens/Review.tsx. (Named wrongly here as "JUDGEABLE_STATUS", a constant that does not
   *  exist: a comment pointing at nothing is how the next reader concludes the whitelist was
   *  removed.) Withdrawal is deliberately NOT gated on this status — see `judgement_withheld`. */
  status: "open" | "accepted" | "stale" | "conflict" | (string & {});
  /** Two findings whose subject AND evidence are identical share one judgement — accepting one
   *  accepts both. Knowingly allowed (the card showed the human nothing to tell them apart) and
   *  made loud rather than silent by this count, which the server derives.
   *
   *  Never true together with `conflict`: "accepting one accepts them all" is FALSE of findings
   *  that printed different figures, and that caption over such a pair is the defect. */
  ambiguous: boolean;
  ambiguous_count: number;
  /** The identity scheme failed on this finding: `conflict_count` findings in this payload share
   *  its subject_key while disagreeing about their evidence. `conflict_note` is the server's
   *  localized sentence saying so (and, when `judgement_withheld`, that a recorded acceptance is
   *  being held back rather than pinned to the wrong card). The screen prints that sentence and
   *  offers no acceptance; `judgement` is null on every conflict card. */
  conflict: boolean;
  conflict_count: number;
  conflict_note: string;
  /** True when the server HOLDS an in-force acceptance for this subject but refuses to show it on
   *  any card in the conflict group. It is the payload's only statement that a withdrawable row
   *  exists on a conflict card, and it is what the withdraw control is gated on there: DELETE
   *  /review/judgements/{subject_key} deliberately permits withdrawal on a conflicted subject,
   *  and gating the control on `status === "accepted"` instead left that acceptance permanently
   *  un-removable. */
  judgement_withheld: boolean;
  fix_action: FixAction | null;
  /** Row-shaped findings only — see `RemapOffer`. Those are `unmapped`, `low_confidence`, and
   *  `off_template`: a printed row the extraction placed on no template line, which the server now
   *  raises here instead of parking in a Workspace bucket. `off_template` is the case the offer
   *  matters most for, since re-mapping is the ONLY way such a row reaches a statement at all. */
  remap: RemapOffer | null;
  /** Structural checks only. `run.result["structural"]` is written once by the pipeline and is
   *  never recomputed on an edit, so the relation is not re-evaluated until the next extraction:
   *  the card does NOT vanish after its own fix, and the note says so. */
  inputs_edited: boolean;
  inputs_edited_keys: string[];
  inputs_edited_note: string;
  judgement: CheckJudgement | null;
}
/** A template line a printed row may be re-mapped onto. Served ONCE per review payload
 *  (`remap_targets`), never per card: it is the same 180-odd concepts for every finding.
 *  Calculated subtotals and section headers are excluded server-side — writing a printed figure
 *  onto a rollup produces a subtotal its own components contradict. */
export interface RemapTarget {
  canonical_key: string;
  label: string;
  statement: string;
  /** The section's label, for grouping the select. A flat list of 180 options is unusable. */
  section: string;
}

/** The re-map offer on a ROW-shaped finding (unmapped / low confidence). Null on every other
 *  card: an accounting finding is about a relation between several concepts, so offering to
 *  re-map it would have to guess which one the analyst meant. */
export interface RemapOffer {
  /** The handle the POST carries. Derived from the row's normalised caption plus the caption's
   *  own geometry, so it does not move when the figure does — and deliberately NOT the subject
   *  key, which folds in the finding's kind and the concept it was mapped to. */
  row_ref: string;
  label: string;
  /** "" for an unmapped row. */
  current_key: string;
  /** Set once a human has moved this row, so the decision is visible after the finding it
   *  answered has left the queue. */
  remapped: { from: string; to: string; reason: string; by: string; at: string } | null;
  remapped_note: string;
}

export interface ReviewResponse {
  /** The run the findings were derived from — printed by the coverage band so a screenshot is
   *  traceable. "" when the document has no run. */
  run_id: string;
  checks: ReviewCheck[];
  /** `types` is the set of `ReviewCheck.type` a tab selects; `null` is the everything tab. The
   *  tab says what it means rather than the client inferring it from position — a positional
   *  contract between a server list and a client array is what made the Page Scope filter chips
   *  filter by the wrong page kind. Counts are by TYPE over the whole list regardless of status,
   *  so each one still equals the length of the list clicking it produces. */
  tabs: { label: string; count: number; types?: string[] | null }[];
  /** `open` counts open, stale AND conflict — all three are outstanding work, and a conflict
   *  cannot be accepted by anyone at all; `stale` and `conflict` are those subsets, reported
   *  separately so the screen can state each out loud instead of burying them in one number.
   *
   *  `open` / `accepted` / `stale` / `conflict` count CARDS. `passed` counts LINES: extracted line
   *  items that NO served finding names, which is what the header tile above it says in all four
   *  locales. It was rows minus (unmapped + low-confidence) — a narrower set, so a line indicted
   *  by a balance, note-tie, structural, guard, calculated_mismatch or uncomputed finding counted
   *  as having none — and both the real route and the sample route now derive the definition the
   *  label states. Never recompute it client-side: one quantity, derived where it is served. */
  summary: { open: number; accepted: number; stale: number; conflict: number; passed: number };
  judgements: { orphaned: OrphanedJudgement[] };
  coverage: CoverageBlock;
  /** Empty when the run named no template — which is also when no card carries an offer, because
   *  a select with no options is worse than no control. */
  remap_targets: RemapTarget[];
}

export interface NoteIndexItem {
  /** As the filing prints it — a string, because a note can be numbered "16(b)" or "7A". */
  no: string;
  title: string;
  conf: ConfCat;
}
export interface NoteDetailRow {
  label: string;
  v1: number;
  v2: number;
  /** The BUCKET this row's mapping confidence fell into — the badge's colour. */
  conf?: ConfCat;
  /** The MEASURED mapping confidence as a percentage (0–100), or null/absent when none was
   *  recorded. The badge under the CONF. header printed the bucket's literal instead, so every
   *  'high' row read "96%" whatever its real score. Absent → the badge says "not scored". */
  conf_pct?: number | null;
  kind?: "sub" | "tot";
}
export interface NoteDetail {
  /** As the filing prints it — see NoteIndexItem.no. */
  no: string;
  title: string;
  page: number;
  linked_line: string;
  linked_label: string;
  rows: NoteDetailRow[];
  reconciliation: string | null;
  /** [current, prior] column labels, derived from the same value lists the rows' v1/v2 came
   *  from — the SAME key and order the statement endpoints use, so one client field serves both
   *  screens. The two headers were hardcoded "FY25"/"FY24" here while the Workspace showed the
   *  filing's real periods, so a 2023/2022 filing had the two screens labelling one figure
   *  differently. Absent (or blank) on a run extracted before the endpoint served it. */
  periods?: string[];
}
export interface NotesResponse {
  notes: NoteIndexItem[];
  count: number;
  linked: number;
}

export interface TemplateNode {
  id: string;
  label: string;
  lvl: number;
  head?: boolean;
  rule?: boolean;
}
export interface NodeConfig {
  breadcrumb: string;
  label: string;
  /** The concept this node maps to — the key an ontology edit targets. */
  canonical_key?: string;
  /** Whether the RULEBOOK IN FORCE actually maps this template line.
   *
   *  A template node the rulebook does not mention has no ontology entry to edit, so every write
   *  the editor offers for it is refused — the mapping PATCH answers 404 "not in this ontology", and
   *  offering the key in the confusable-with or netting pickers gets a 422. Absent means an older
   *  payload that never said; treat that as mapped, since that is what those payloads described. */
  mapped?: boolean;
  /** Merged display set (this locale + English fallback, capped) — read-only. */
  aliases: string[];
  /** RAW aliases stored for the requested locale — what the editor loads and saves back,
   *  so saving one language's aliases can't absorb another's fallbacks. */
  aliases_locale?: string[];
  sign: string;
  value_type: string;
  aggregation: string;
  netting: { expr: string; explain: string };
  /** The criteria the mapper reasons over. Aliases only fire when the printed wording is
   *  close to one; these decide the concept by MEANING, so they are editable too. Optional
   *  because the demo project's template view predates them. */
  definition?: string;
  include?: string[];
  exclude?: string[];
  /** Other canonical_keys this concept is easily confused with (server rejects unknown keys). */
  confusable_with?: string[];
  value_scope?: ValueScope;
  /** Lexical hints for the deterministic tier (keyword / regex / phrases that rule a match out). */
  keyword_hints?: string[];
  regex_hints?: string[];
  exclude_hints?: string[];
}
/** How a concept's value relates to its neighbours — mirrors the backend `ValueScope`. */
export type ValueScope =
  | "exclusive_leaf"
  | "exclusive_child"
  | "exclusive_residual"
  | "not_applicable";
export interface NettingRuleView {
  id: string;
  target_key: string;
  target_label: string;
  subtract: { key: string; label: string }[];
  add: { key: string; label: string }[];
  condition: string;
  label: string;
}
export interface TemplateResponse {
  tree: TemplateNode[];
  node_config: Record<string, NodeConfig>;
  template: { key: string; name: string; line_items: number };
  netting_rules?: NettingRuleView[];
  /** The ontology version whose rules this view shows — the target of inline edits.
   *  Null when no ontology targets this template (nothing to edit). */
  ontology?: { id: string; ontology_key: string; version: number; locale: string } | null;
}

/** An inline edit to ONE concept's mapping rules; only the fields present are changed.
 *  `aliases` is locale-scoped, so editing one language never clobbers another's list. */
export interface MappingEdit {
  canonical_key: string;
  locale?: string;
  aliases?: string[];
  sign_convention?: string;
  label?: string;
  description?: string;
  definition?: string;
  include?: string[];
  exclude?: string[];
  confusable_with?: string[];
  value_scope?: ValueScope;
  keyword_hints?: string[];
  regex_hints?: string[];
  exclude_hints?: string[];
}
/** Upsert or remove ONE netting rule, identified by its id. */
export interface NettingRuleEdit {
  id: string;
  delete?: boolean;
  target_key?: string;
  subtract_keys?: string[];
  add_keys?: string[];
  condition?: string;
  label?: string;
}
/** Every ontology edit publishes a new version — this is the version it published. */
export interface OntologyEditResult {
  id: string;
  ontology_key: string;
  version: number;
}

/** One field of the ontology schema, as the authoring index lists it. `path` is the dotted
 *  location in the JSON (`mappings[].value_scope`), and `help` states the accepted values in
 *  JSON spelling — the upload gate refuses undeclared keys, so a guess costs a 422. */
export interface OntologyFieldHelp {
  path: string;
  required: boolean;
  help: string;
}
/** The shape an uploaded ontology must have. Generated from the same model the upload gate
 *  validates with, so it can never describe a rule the API has stopped enforcing. */
export interface OntologySchema {
  schema_version: number;
  json_schema: Record<string, unknown>;
  field_help: OntologyFieldHelp[];
}

export interface ExportOption {
  key: string;
  label: string;
  on: boolean;
}
