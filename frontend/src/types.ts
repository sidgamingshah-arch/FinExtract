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

/** One row of the DEPLOYMENT-WIDE trail — the same run record, plus which filing it was against.
 *  The per-document trail has no need of those two fields: there, the document is the question. */
export interface AdminAuditEntry extends AuditEntry {
  /** The document id the run was recorded under, or the sample project's id. */
  scope_key: string;
  /** The uploaded file's name. Empty for the seeded sample project, and empty for a document that
   *  has since been DELETED — an entry outlives the document it describes on purpose, so the trail
   *  still records that the run happened and what it cost. */
  document: string;
  scope_kind: "document" | "other";
}
/** What the trail adds up to, OVER THE ENTRIES RETURNED — see `truncated`. */
export interface AuditTotals {
  runs: number;
  /** Runs that used the LLM at all. Counted, not inferred from the token sum: a run that used the
   *  model and was reported zero tokens still used it, and a reader dividing tokens by runs needs
   *  the denominator to be the runs that could have spent any. */
  llm_runs: number;
  failed: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}
export interface AdminAuditResponse {
  entries: AdminAuditEntry[];
  totals: AuditTotals;
  limit: number;
  /** The cap was reached, so `totals` describes a WINDOW and not the whole table. The screen has to
   *  say so: a total printed over "the newest 500 of more" reads as the deployment's lifetime spend. */
  truncated: boolean;
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
  /* No `llm` / `reset_llm`: the LLM is defined only in backend/config.toml [llm]; the API
     answers 400 to either. */
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
  /** READ-ONLY echo of backend/config.toml [llm]; not shown on the Settings screen. */
  llm: {
    defined_in?: string;
    provider: string;
    model: string;
    max_tokens: number;
    timeout_seconds: number;
    base_url: string;
    api_key_env: string;
    key_configured: boolean;
  };
  ocr: {
    engine: string; languages: string[]; dpi: number;
    // Whether the offline engine can run on this server: the package, and its models on disk.
    docling?: { installed: boolean; models_dir: string; models_present: boolean };
  };
  /** A hosted document engine (config.toml [document_engine]); switched on by the
   *  `document_engine` extraction knob. Never carries the token, only whether one is set. */
  document_engine?: {
    defined_in: string;
    kensho: { submit_url: string; result_url: string; addresses_set: boolean;
              token_env: string; token_present: boolean };
  };
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
export type StatementKey =
  | "statement_setup"
  | "balance_sheet"
  | "profit_and_loss"
  | "cash_flow"
  | "covenants_supplemental"
  | "notes"
  | "changes_in_equity" | "kpi";
/** Views that exist only for a real extraction (there is no demo data behind them). */
export const DERIVED_STATEMENTS: StatementKey[] = ["kpi"];
export type RowKind = "section" | "subhead" | "item" | "subtotal" | "total";
export type ExportFmt = "excel" | "json" | "csv";
/** File extension and MIME-ish suffix each format is delivered under — ONE mapping, so the
 *  preview caption, the download button and the saved filename cannot name three things. */
export const EXPORT_EXT: Record<ExportFmt, string> = { excel: "xlsx", json: "json", csv: "csv" };

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
  /** Whether the rule SUBTRACTED this input. A cascade priority spelled "the wider disclosure
   *  less the cost-of-sales share" consumes one of its inputs negatively, so the row renders with
   *  a "−" and its own magnitude — the column has to add up to the figure above it as read. */
  deducted?: boolean;
  /** The filing's own words, for an input read out of PROSE rather than off a table row. A
   *  printed row is traceable by its caption; a figure stated in a sentence is traceable only by
   *  the sentence. Absent for every table-row input. */
  excerpt?: string | null;
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
   *  - `derived`              — assembled by an extraction rule from NOTE lines rather than read
   *                             off one caption. The template's own components were not
   *                             extracted, so the rollup could not check the figure — but unlike
   *                             `reported_uncomputed` the row DOES carry its `contributions`,
   *                             each with the page it was printed on, and that is its
   *                             traceability;
   *  - `reported_uncomputed`  — a calculated line none of whose components were extracted, so
   *                             the printed figure is shown UNVERIFIED and is in the review queue.
   *
   * `derived` was ABSENT FROM THIS UNION while the server had been sending it since c67e723, and
   * a type that under-states what an API returns is not a smaller type — it is a wrong one. The
   * server's own glossary for these values is `_CALC_NOTES` in api/routes/documents.py; anything
   * added there belongs here, and in `ORIGIN_CHIP` in screens/Workspace.tsx, in the same commit.
   */
  origin?: "extracted" | "calculated" | "manual" | "derived" | "reported_uncomputed";
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
  /** The printed figure to show BESIDE a calculated one, per period — the server's decision, not
   *  the grid's. Set only when that period's figure is the computation, the filing printed the
   *  line, and the two differ by more than the cross-check tolerance; null otherwise, and absent
   *  on a row that is not a calculated line. The published figure stays `v1`/`v2`. */
  printed1?: number | null;
  printed2?: number | null;
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

/** One line's trail: what it was assembled from, as the inspector renders it.
 *
 *  Served in `StatementResponse.traces` for every keyed row that HAS a trail — a row read straight
 *  off one caption explains itself through its own provenance and gets no entry. */
export interface LineItemTrace {
  label: string;
  /** Display only — a rendering of the arithmetic, never an expression to send back. */
  formula: string;
  method?: string | null;
  /** Face or note, so a reader knows whether they are being sent to a statement or a note. */
  printed_in?: string | null;
  contributions: RowContribution[];
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
  /** The TRAIL for every keyed row that has one — INCLUDING THE SUB-LINE ITEMS, which are not in
   *  `rows` at all.
   *
   *  `rows` is built from the template's sections, and the 77 sub-line items are not template
   *  nodes, so the grid has no row for the layer that actually corresponds to a printed note row.
   *  A derived parent's contribution names the sub-line it came from (`canonical_key`), and this
   *  is where that name resolves to something: the sub-line's own label, its formula, and its own
   *  contributions — each with the page it was printed on.
   *
   *  FLAT, keyed by canonical key, because the chain is deeper than two: a main line names a
   *  sub-line, and a sub-line assembled from several notes names each of those. Walk it by
   *  following `canonical_key` from one entry to the next; a contribution with no key is a leaf —
   *  a place in the document rather than a configured line — and its `source` is the end of the
   *  trail. Absent on responses stored before the field existed. */
  traces?: Record<string, LineItemTrace> | null;
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
  /** The CONFIGURATION card — the line-item set this project's filing is read against. It was
   *  `ontology: { file, rules, aliases, status }`; there is one configuration engine now, so the
   *  block names the same thing the Line Items screen configures and `rules` became `items`,
   *  because a line item is what is stored. Mirrors `routes/projects.py`'s own empty-project
   *  block key for key, so the sample and the real path cannot describe different shapes. */
  line_items: { file: string; items: number; aliases: number; status: string };
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
  /** The page's POSITION in the file, 0-based. The viewer's raster address, the input to the
   *  review queue's judgement anchor, and what the page-scope selection is expressed in. */
  page_index: number;
  /** The folio the PUBLISHER printed on that page — what a reader can look up, and what every
   *  citation names. A sibling of `page_index`, never a replacement: the two differ by whatever
   *  front matter the report has (measured across two real HK filings: 0 and 1). Null for a
   *  spreadsheet and for a page whose footer could not be read. */
  printed_page?: string | null;
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
  /** Which of the two column sets the figure was printed under. Served by `_serialize_rows` on
   *  every value, but OPTIONAL here: a run's rows are persisted JSON, so a result serialized
   *  before this field existed is still read back by this client and still has to render. */
  basis?: Basis;
  /** The column's real period-end date when extraction resolved one ("31 December 2023"), as
   *  opposed to the positional `period_label` ("current"). Null when it did not. */
  period_display?: string | null;
  /** Printed left-to-right position of a matrix statement's component column; null otherwise. */
  column_index?: number | null;
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
/** WHICH CONFIGURATION a run read the filing against — as the RUN recorded it when it started, not
 *  as a reader works out afterwards which one "must" have been in force. One engine: the record
 *  names a stored line-item version, and there is no second kind of configuration to select.
 *
 *  The distinction is the whole point. The client asks for a configuration by id; between that
 *  request and someone reading the result, a new version can be published, so a screen that
 *  re-derives "in force" from the version list labels a run with a configuration it never used —
 *  which is how a reload came to describe a superseded run as the current one. `status` is the
 *  server's own claim about the set the run used: `in_force` (it WAS the one in force for its
 *  template when the run started), `pinned` (a stored set, but not the one in force — reproducing
 *  an earlier spread), `engine_default` (the run named no configuration, so nothing in the filing
 *  was recognised) or `missing` (the id named nothing stored). See
 *  backend/app/api/routes/extractions.py::configuration_record.
 *
 *  `superseded` IS GONE from that union, with the sibling lookup that computed it. It answered "has
 *  some other stored definition DECLARED this key replaced" — labelling that decided nothing about
 *  what runs, since selection is latest-stored-wins, and could be true of the very row in force, so
 *  the record contradicted itself. `in_force` plus the in-force key/version below are the answer to
 *  "is this current"; do not reinstate a second, declarative one.
 *
 *  The WIRE KEY is still `rulebook` (see the server function above). It says nothing about an
 *  ontology — it is the configuration a run was read against — and renaming a key three consumers
 *  already read would be churn no reader can see. */
export interface ConfigurationRecord {
  line_item_version_id: string;
  line_items_key: string;
  version: number;
  target_template_key: string;
  status: "in_force" | "pinned" | "engine_default" | "missing";
  in_force: boolean;
  /** The configuration that WAS in force for the template when the run started, so a run that used
   *  another one can name what it departed from. */
  in_force_line_items_key: string;
  in_force_version: number;
  /** False when the recorded version's stored definition would not load: it governed nothing,
   *  however firmly the run names it. Present on the finished result, not on the start response. */
  applied?: boolean;
}
export interface ExtractionResult {
  locale: string;
  /** The configuration this run read the filing against (see ConfigurationRecord). */
  rulebook?: ConfigurationRecord | null;
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
  /** How line-item mapping actually ran. "deterministic" means the LLM was unavailable and the
   *  weaker rule/alias ensemble decided — surfaced so a degraded run is visible, not implied. */
  mapping?: { strategy: string; reason: string; llm_calls: number; model: string } | null;
}
/** How far the pipeline has got, as the run row records it.
 *
 *  The whole shape lives in the run's existing `progress` JSON column rather than in new table
 *  columns: `init_db` uses `create_all`, which never adds a column to an existing SQLite file, so
 *  new columns would break every database already on disk.
 *
 *  `stage` is the pipeline stage's own name (`"map_line_items"`, `"residual"`, …) — served, never
 *  guessed client-side, because the stage list is assembled in `core/pipeline.py` and the docs have
 *  already been wrong about it once. It read `"map_ontology"` here, which is the module the stage
 *  still lives in and has not been the served name since line items became the single
 *  configuration engine. Empty/absent while a run is still queued. */
export interface ExtractionProgress {
  /** "queued" | a stage name | "done" | "failed". */
  phase: string;
  pct: number;
  stage: string;
  stage_index: number;
  stage_count: number;
  /** The stages already finished, in order, so the screen can tick them off. */
  stages_done: string[];
  /** Progress WITHIN the stage in flight. `step_total === 0` means this stage reports no
   *  sub-steps, which is every stage but line-item mapping — read it as "no detail", never as
   *  "0 of 0 done". `step_label` says what a unit is ("LLM call", "row").
   *
   *  Why it exists: mapping is one stage and by far the longest, and it makes every LLM call in
   *  the run. Without this the percentage, the stage counter and the log tail all sat frozen for
   *  the whole of it, so a run that was working looked identical to one that had hung. */
  step_done: number;
  step_total: number;
  step_label: string;
  /** LLM calls the run has COMPLETED so far. Live — it used to appear only in the finished
   *  result, which is the one moment nobody needs it. */
  llm_calls: number;
  /** Requests that FAILED. Zero calls means two opposite things — nothing was asked, or
   *  everything was refused — and the panel used to hide the stat at zero, so a run whose every
   *  request failed looked exactly like a deliberately deterministic one. */
  llm_failures: number;
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
  /** Alongside the result rather than inside it, so the configuration can be named from the first
   *  poll — while the run is still running, and even when it fails without producing a result. */
  rulebook?: ConfigurationRecord | null;
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
/** One entry in a document's run history (`GET /documents/{id}/runs`) — light enough for a
 *  picker: no `result`, so listing every past run does not download each one's full spread. */
export interface DocumentRunSummary {
  run_id: string;
  run_number: number;
  status: string;
  created_at: string;
  rulebook?: ConfigurationRecord | null;
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
  /** The folio the filing PRINTED on that page when it printed one (a string, and not always
   *  numeric), falling back to the 1-based sheet position. Rendered, never arithmetic. */
  page: number | string | null;
  snippet: string;
  /** The figure the pipeline COMPUTED for this disclosure, where it computes one — today only
   *  contingent liabilities. A decimal STRING, never a number: these are money, and a JSON number
   *  would round ¥118,754,500.00 through a float. Null when the disclosure is qualitative, or when
   *  the rulebook forbids inferring an amount ("do not infer a numeric zero from silence") — which
   *  has to render blank, never as 0. */
  amount: string | null;
  currency: string | null;
  /** HOW that figure was arrived at, where the pipeline can say — today only contingent
   *  liabilities (`services/contingent_liabilities.disclosure_explanation`). All three are
   *  optional and absent on an ordinary presence-scan entry.
   *
   *  A total with no statement of what it is made of is the one thing a credit reader cannot
   *  use: ¥118,754,500 of "contingent liabilities" is unreviewable, while "Corporate guarantees
   *  118,754,500 across 3 disclosed items, p.209" can be checked against the page. */
  explanation?: string;
  /** The exposure summed per type. Per CURRENCY AND SCALE as well as type, which is why this is
   *  a list and not a map: a type disclosed in both thousands and millions is two entries and
   *  never one wrong sum — and in that case `amount` above is deliberately null, so this is the
   *  only set of figures there is. */
  breakdown?: DisclosureBreakdown[];
  /** One sentence per disclosed paragraph that fits no type, with its amount. */
  statements?: DisclosureStatement[];
  /** EVERY DISCLOSED ITEM, ONE BY ONE — the detail table. `breakdown` above sums per type and
   *  `statements` keeps only what fits no type, so an item that WAS classified appeared in neither
   *  on its own: a reader checking a corporate-guarantee subtotal against p.209 had nothing to
   *  check it against. */
  items?: DisclosureItem[];
  /** What that table comes to, per currency and scale. NEVER one blended figure: the derivation
   *  that published a single total across unlike units was removed deliberately, and this is the
   *  table's own column sum, not a figure for any line item. */
  item_totals?: DisclosureItemTotal[];
  /** Which basis and period the explanation describes, e.g. "consolidated:current". */
  basis_period?: string | null;
}
/** One row of the detail table — a single disclosed exposure. */
export interface DisclosureItem {
  description: string | null;
  classification: string | null;
  /** WHY it is that type. The classification runs in a fixed priority order, so a broad
   *  "guarantee" caption can lose to a more specific instrument — the decision worth showing. */
  classified_by?: string[];
  /** A decimal STRING for the same reason as `Disclosure.amount`. Null where the filing disclosed
   *  the item without an amount, which must render blank and never as 0. */
  amount: string | null;
  currency: string | null;
  scale?: string | null;
  note_number?: string | null;
  note_heading?: string | null;
  page?: number | string | null;
  counterparty?: string | null;
  /** Set when this exposure restates one already disclosed in another note. Shown and marked
   *  rather than hidden — it is excluded from the total, and a reader who could not see it would
   *  not know whether the filing disclosed the exposure once or twice. */
  duplicate_of?: string | null;
}

/** The detail table's column sum for one currency and scale. */
export interface DisclosureItemTotal {
  amount: string | null;
  currency: string | null;
  scale?: string | null;
  item_count?: number | null;
  /** How many items disclosed no amount. Part of the answer, not a footnote: a sum over eight
   *  items where three disclosed nothing is not a total of the exposure. */
  unpriced?: number | null;
  duplicates_excluded?: number | null;
}

export interface DisclosureBreakdown {
  type: string | null;
  /** A decimal STRING for the same reason as `Disclosure.amount`. */
  amount: string | null;
  currency: string | null;
  scale: string | null;
  item_count: number | null;
  source_pages: (number | string)[];
}
export interface DisclosureStatement {
  statement: string;
  amount: string | null;
  currency: string | null;
  source_note: string | null;
  page: number | string | null;
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
  /** Carried straight from the disclosure that raised the flag — see `Disclosure.page`. */
  page: number | string | null;
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
/** One stored version of THE configuration, as the picker lists it (`GET /line-items/versions`).
 *
 *  This replaced `OntologyRef`. Line items is the single configuration engine, so there is one kind
 *  of version to choose from and no engine to choose between — and `schema_version`, `supersedes`
 *  and `superseded` are gone with it: the server no longer computes a declarative "has something
 *  replaced this", because selection is latest-stored-wins and `in_force` is the whole answer. */
export interface LineItemVersionRef {
  id: string;
  line_items_key: string;
  target_template_key: string;
  /** Edits to THIS set. It counts revisions of one key, so it cannot rank two different sets that
   *  target the same template — which is what `in_force` is for. */
  version: number;
  /** When the version was stored. Null on a row that recorded no stamp. This is what the server's
   *  "whatever was stored last wins" rule ranks on, served so a picker can show the list in the
   *  order it is being ranked in — never so the client can re-run the rule (see `in_force`). */
  created_at?: string | null;
  /** True for the ONE version per target template that the next run will map against.
   *
   *  Served by the server, never re-derived here. The client used to rank the list itself under a
   *  comment claiming it mirrored the server's picker; it did not, so the screen could name a
   *  different configuration than the one a run actually used. Read this flag; do not sort. */
  in_force?: boolean;
  /** False when the STORED definition can no longer be read as a configuration by today's schema —
   *  the server tries to load it (`routes/line_items.py::probe_line_item_load`) rather than
   *  assuming a row it holds is usable, and drops such a row out of selection. It must not be
   *  offered as a choice either: a run pinned to it recognises nothing in the filing. Absent on an
   *  older server — read `loads === false`, never `!loads`, so an unstated answer is not read as
   *  "broken". */
  loads?: boolean;
  /** How big the configuration is: line items it declares, and aliases across every locale (the
   *  base list plus every `aliases_i18n` entry). Counted by the server off the stored definition,
   *  so a screen describing a version's size never has to invent one. */
  items?: number;
  aliases?: number;
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
   *  client never ranks versions itself — the same contract as `LineItemVersionRef.in_force`. Read
   *  it to default a selection; do not sort. */
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
  /** Which statement a face page is, and whose figures (consolidated / company / mixed). */
  statement?: string | null;
  entity?: string | null;
  /** How the page is read, top to bottom: the person's override when `overridden`, otherwise the
   *  classifier's own cut in the same form — the starting point of an edit. */
  parts?: PagePart[];
  overridden?: boolean;
}
/** One part of a page: it begins at `from_y` (fraction of the page height from the top) and
 *  runs to where the next part begins. */
export interface PagePart {
  from_y: number;
  kind: "face" | "notes" | "other";
  statement: string | null;
  entity: "consolidated" | "company" | null;
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
  /** The card kind. One of the accounting kinds — `balance`, `equity_tie`, `structural`,
   *  `calculated_mismatch`, `containment_gap` — or the row-shaped one, `unmapped`. The server
   *  declares that set (`_ACCOUNTING_TYPES` / `_ROW_SHAPED_TYPES` in routes/documents.py) and
   *  refuses to serve a card outside it, because a kind with no chip is invisible under every
   *  filter. Kept as `string` rather than a union: the tabs carry the types they select, so this
   *  screen never switches on the value, and a union here would make a server-side addition a
   *  build error in a client that does not need to know. */
  type: string;
  icon: string;
  title: string;
  where: string;
  severity: string;
  /** How loudly the card is painted, and it is a SEVERITY rather than a colour name:
   *  `high` a check that failed (arithmetic or a declared rule is broken), `med` something to place
   *  or confirm, `low` informational.
   *
   *  It said `"low" | "med" | "indigo"` and the server has never sent "indigo" — while every failed
   *  check it DOES send arrives as "high", which was in neither the type nor `toneColors`, so all
   *  four failing kinds fell through to the informational branch and rendered indigo. The unmapped
   *  card meanwhile said "low", which that function painted RED: the queue's loudest colour on its
   *  mildest finding and its quietest on every real failure. The seeded sample had a third reading
   *  again (`low` for the blocking balance card). One vocabulary now, both paths. */
  tone: "high" | "med" | "low";
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
  /** The row-shaped finding only — see `RemapOffer`. That is `unmapped`, and it is now the ONE kind:
   *  a printed row that reaches no line of the output, whether because nothing claimed the caption or
   *  because it was claimed for a concept this template declares nowhere (what used to be its own
   *  `off_template` kind). Re-mapping is the only way such a row reaches a statement at all, which is
   *  why this is the kind that carries the offer. `low_confidence` no longer exists — a mapping's
   *  strength is not a finding — so no card is served for a weakly-mapped row; see
   *  `ReviewPayload.weak_mappings` for how many there are. */
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
   *  by a balance, note-tie, structural, guard or calculated_mismatch finding counted as having
   *  none — and both the real route and the sample route now derive the definition the label
   *  states. A WEAKLY-MAPPED LINE IS ALSO EXCLUDED even though it raises no card, or the tile would
   *  certify it clean. Never recompute it client-side: one quantity, derived where it is served. */
  summary: { open: number; accepted: number; stale: number; conflict: number; passed: number };
  /** Lines whose MAPPING is weak. Not a finding and not in `checks`: this queue reports three things
   *  — a face figure that reaches no line, a validation rule that failed, and a subtotal that does
   *  not match its components — and the right answer to a weak match is to match it better, not to
   *  bill an analyst for it. The row still shows its own confidence badge in the Workspace.
   *
   *  Served because two readers must not conclude the data improved when all that happened is that
   *  this queue narrowed: the `passed` tile (which excludes these lines) and the commentary's
   *  data-quality caveat. Shown on this screen as a plain sentence, never as a card or a tab. */
  weak_mappings: number;
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
  /** Template concept supplied by this note row, when note decomposition mapped it. */
  canonical_key?: string | null;
  /** Combined face concept whose cited note supplied this mapped or residual detail. */
  supports_face_key?: string | null;
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
export interface NoteStructuredTable {
  columns: string[];
  rows: { label: string; section?: string | null; values: Record<string, string | null> }[];
}
export interface NoteDetail {
  /** As the filing prints it — see NoteIndexItem.no. */
  no: string;
  title: string;
  /** Where the note STARTS. A note continued across pages is one note, so this is the lowest page
   *  it was printed on — click-to-source lands on the note's own heading, not on a continuation. */
  page: number;
  /** Every page the note spans, ascending. One entry for a note printed on a single page. */
  pages?: number[];
  /** The folio printed on the note's FIRST page, and the folios across its span — siblings of
   *  `page`/`pages`, which stay sheet positions because the viewer navigates by them. */
  printed_page?: string | null;
  printed_pages?: string[];
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
  /** The source table's full shape, used for AI-structured multi-column note schedules. */
  table?: NoteStructuredTable;
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
  /** The line item this node maps to — the key an inline configuration edit targets. */
  canonical_key?: string;
  /** Whether the CONFIGURATION IN FORCE actually maps this template line.
   *
   *  A template node no line item declares has nothing to edit, so every write the editor offers
   *  for it is refused — the item PATCH answers 404 for a key the set does not declare, and
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
  /** Extra instruction for THIS line, carried inside its own candidate entry. Only sent for an
   *  `extracted` line — the server refuses it on any other type, and the refusal lands here. */
  prompt?: string;
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
  /** The line-item version whose rules this view shows — the target of inline edits, and `locale`
   *  says which alias list is being edited. Null when no stored configuration targets this template
   *  (nothing to edit). Served under this name by `routes/templates.py`; it was `ontology`, and
   *  there is one configuration engine now. */
  line_items?: { id: string; line_items_key: string; version: number; locale: string } | null;
}

/** THE TEMPLATE SCREEN'S LEGACY EDIT BODY. Superseded by `LineItemEdit` below — do not add
 *  fields here, and do not point a new caller at it.
 *
 *  It is kept only because `api.editLineItem` is still typed on it and `Template.tsx` still sends
 *  `canonical_key`, and neither file is in this change. The wire has moved: the endpoint is
 *  `PATCH /line-items/versions/{id}/items` and its body (`routes/line_items.py::ItemEdit`) takes
 *  `key`, not `canonical_key`, and `exclude_criteria`, not `include` /
 *  `exclude`. Pydantic IGNORES the keys it does not declare, so the two criteria lists the
 *  Template screen sends under the old names are accepted with a 200 and DROPPED — a save that
 *  silently does nothing, which is the defect class this whole change is closing. DELETE this
 *  interface the moment `api.ts` and `Template.tsx` send `LineItemEdit`; nothing else reads it. */
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
/** Every inline configuration edit publishes a NEW version — this is the version it published. It
 *  is never an in-place write: a run pins the exact version it used, so mutating a stored
 *  definition would retroactively change how a past run is explained. */
export interface LineItemEditResult {
  id: string;
  line_items_key: string;
  version: number;
}

/** One field of the line-item schema, as the authoring index lists it. `path` is the dotted
 *  location in the JSON (`items[].value_scope`), and `help` states the accepted values in
 *  JSON spelling — the upload gate refuses undeclared keys, so a guess costs a 422. */
export interface LineItemFieldHelp {
  path: string;
  required: boolean;
  help: string;
}
/** The shape an uploaded configuration must have. Generated from the same model the upload gate
 *  validates with, so it can never describe a rule the API has stopped enforcing. */
export interface LineItemSchema {
  schema_version: number;
  json_schema: Record<string, unknown>;
  field_help: LineItemFieldHelp[];
}

export interface ExportOption {
  key: string;
  label: string;
  on: boolean;
}

/* ── Line-item definitions (Configure > Line items) ──────────────────────────────────────────
 * The configuration behind the eight output lines and their sub-line items. Mirrors
 * backend/app/schemas/line_items.py — the type decides which fields carry meaning, which is why
 * almost everything below is optional rather than a discriminated union: the backend serves one
 * shape and the screen shows the parts that apply. */
export type LineItemType = "extracted" | "calculated" | "intermediate" | "derived";

/** WHAT A LINE OUTPUTS. `LineItemType` says how the value ARRIVES; this says what KIND of thing it
 *  is, which until now had one possible answer.
 *
 *   "value"  — a number. THE DEFAULT, and what every shipped line is. Everything that totals,
 *              reconciles or checks an identity assumes it.
 *   "phrase" — a short piece of text taken FROM the document (an audit opinion's wording).
 *   "prose"  — text the model writes for this line from the line's own `prompt`.
 *
 *  Only legal on an `extracted` line: no arithmetic produces a sentence, and the server refuses
 *  the pair. `prose` additionally requires a prompt, which is the only thing it is written from. */
export type OutputStructure = "value" | "phrase" | "prose";
/** Whether the FILING's own printed note reference goes ahead of a scored one.
 *
 *  This used to be `"semantic" | "patterns"` — which selector found a line's notes — and the two
 *  were never alternatives: a pattern-named note is passed unconditionally and scoring ADDS to it,
 *  so `patterns` only removed the line from the semantic pass. All 539 lines declared neither. */
export type NoteSelection = "cited_first" | "any";

/** WHERE AN `extracted` LINE'S FIGURE IS READ FROM — one choice, and one question replacing three
 *  implicit ones (`SectionDefaults.where()`, the presence of a `note_source`, and whether that
 *  object carried prose patterns, which an author could satisfy two of and not the third).
 *
 *    face         off the statement itself, and no note is searched
 *    note_tables  out of a cited note's table rows, with the prose fallback behind it, and NOT
 *                 off the statement
 *    prose        out of a sentence, for a figure the filing tabulates nowhere — skips the rows,
 *                 and NOT off the statement
 *    anywhere     any of the above, plus the pages that are neither a statement nor a note
 *
 *  IT IS EXCLUSIVE IN BOTH DIRECTIONS. A `face` line never reads a note, and a note route never
 *  reads the statement — the latter is recent, because the stage that binds a printed statement
 *  caption to a line did not read this field until it did.
 */
export type Route = "face" | "note_tables" | "prose" | "anywhere";
/** Where a caption may be READ FROM, in search order — not a gate. The tokens are
 *  `StatementType`'s own: this list once said `income_statement` and `changes_in_equity`, neither
 *  of which the backend knows (it says `profit_and_loss` and `equity_changes`), so a scope sent
 *  back would have matched nothing. */
export type SearchScope = "notes" | "balance_sheet" | "profit_and_loss" | "cash_flow"
  | "equity_changes" | "covenants_supplemental" | "statement_setup" | "front_matter";
/** The seven statements a line item may be gated to. `null` means claimable on any of them. */
export type StatementToken = "statement_setup" | "balance_sheet" | "profit_and_loss"
  | "cash_flow" | "equity_changes" | "covenants_supplemental" | "notes";
/** What a parent asserts about its children: addends, or alternative sources for one figure. */
export type LineItemRollup = "sum" | "alternatives" | "none";
/** Which key-space a definition lives in — a note-level part is deliberately off-template. */
export type LineItemNamespace = "template" | "internal";
/** `from_section` reads the side off the section banner a caption sits under — the right answer
 *  for a balance-sheet line printed inside a section, and no answer for a statement total. */
export type LineItemSide = "from_section" | "asset" | "liability" | "equity" | "none";
/** What a term's absence means. `adjustment` never justifies a cascade rung on its own. */
export type TermRole = "required" | "any_of" | "adjustment";
/* The scalar vocabularies below were spelled inline on `LineItemDef` and had to be spelled a
 * second time the moment an EDIT body needed them. Named once and read by both, because two
 * copies of a closed set is one copy that stops matching the backend's `Literal[...]` and an
 * editor that offers a token the publish gate refuses. Every one of them is served at runtime
 * under `LineItemVocab` as well — the type says what is legal, the vocab says what THIS set has. */
/** `disabled` makes the line unreachable by every matching tier, fillable only by the sweep. */
export type LineItemAliasMatching = "enabled" | "disabled";
/** Only `do_not_extract` suppresses a line. The other three stay candidates the matcher can
 *  recognise — reading `derive` as "do not extract" refuses a printed row and sweeps it. */
export type LineItemExtractionMode =
  | "extract" | "extract_or_derive" | "derive" | "do_not_extract";
/** Instant versus duration — the line's identity as a measurement. `null` means nothing said. */
export type LineItemTemporality = "instant" | "duration";
export type LineItemUnitOfAccount = "balance" | "flow" | "subtotal";
/** Whether a cited note may be a SOURCE for this line or only evidence for it. Three-valued:
 *  `null` is "nothing was said", which is NOT `evidence_only`. */
export type LineItemNoteUse = "evidence_only" | "decomposition_allowed";
/** The sign the line is EXPECTED to carry — a review trigger, never a transformation. This is
 *  `LineItemDef.sign_convention`, and it is sent as `sign_expectation` on an edit: the wire name
 *  `sign_convention` is taken by the legacy 3-token spelling, and one name for two questions is
 *  how an author changes the wrong one. */
export type SignExpectation = "positive_expected" | "negative_expected" | "either";
/** The convention a value is NORMALISED and stored under (`sign_rule.convention`) — a different
 *  question from `SignExpectation`. Six values; the legacy 3-token UI vocabulary the Template
 *  screen sends (`as_reported` / `expense_contra` / `auto`) can express three of them. */
export type SignRuleConvention = "natural" | "natural_positive" | "natural_negative"
  | "debit_positive" | "credit_positive" | "context";
/** Whether `note_source`'s three pattern groups are authored against RAW captions or against
 *  `normalize_label`-folded text. The shipped patterns were lifted from a matcher that reads raw
 *  captions, so folding them would stop some of them matching. */

export interface LineItemTerm {
  ref: string;
  const: number | null;
  sign: 1 | -1;
  abs: boolean;
  role: TermRole;
}
export interface CascadeRung {
  id: string;
  terms: LineItemTerm[];
  /** HOW THOSE TERMS COMBINE — sum | max | min | first. A property of the GROUP, not of any
   *  one addend. `sum` on every shipped rung. */
  terms_op?: TermsOp;
  note: string;
  /** A rung computing below zero is passed over and the next tried — the derivation services
   *  refuse a negative candidate, and the config that ported them originally did not. */
  refuse_negative: boolean;
}
/** Which note a sub-line item is read from and which of its rows count.
 *
 *  This object REPLACED a hard-coded heading list and a 162-alternative regex whitelist that
 *  refused a filing writing "Depreciation charge for the year". Widening it is the single edit the
 *  Line Items screen exists to make possible, so all four fields are authorable. */
/** How a group of terms combines. `sum` adds them; `max`/`min` pick the largest/smallest of
 *  several readings of one quantity; `first` takes the first present and ignores the rest, so
 *  the row order is the precedence. Applies to the BASE terms — `required` and `any_of`; an
 *  `adjustment` is a signed addition to whatever the base comes to, in every mode. */
export type TermsOp = "sum" | "max" | "min" | "first";

export interface NoteSource {
  note_title_any: string[];
  row_caption_any: string[];
  /** Regex VETOES over the note's rows. A torn pattern here does not fail loudly — the exclusion
   *  simply stops excluding — which is why the edit path compiles each one and attributes the
   *  compile error to this list and to the offending index. */
  row_caption_none: string[];
  /** WHICH MEASURE OF THE PERIOD this part reads — the slug the reader suffixes onto the period
   *  label ("allowance" is 坏账准备). Empty is the primary measure. */
  measure?: string;
  /** Whether this part's column must (true) or must not (false) come from a two-level
   *  period × measure header; null does not ask. */
  from_measure_grid?: boolean | null;
  /** WHICH PRINTED COLUMN this part reads, by the heading the filing prints over it — regexes
   *  searched on the whole heading and each script half. Both empty is the default and reads
   *  exactly as before. A selected figure is filed under the period the filing prints for it,
   *  never the positional key, and a column the reader could not name is never selected. */
  column_heading_any?: string[];
  /** Headings that must NOT be read, even where the list above admits them. The veto wins. */
  column_heading_none?: string[];
  /** THE RAW ESCAPE HATCH for the prose route — regexes over a note's SENTENCES. Authored in
   *  plain words through the two fields below now; this stays for a sentence shape the grammar
   *  cannot express. Measured over the 28 patterns it replaced, none needed it. */
  prose_any?: string[];
  /** WHICH SHARED SUBJECT VOCABULARY this line's sentences are about — a name into
   *  `LineItemSetInfo.prose_grammar.subjects`, e.g. "depreciation". */
  prose_subject?: string;
  /** WHERE THE SENTENCE SAYS THE FIGURE LANDED, in plain phrases: "other operating expenses",
   *  "cost of sales", 其他经营开支. The only per-line part of a prose rule — measured, all 28
   *  patterns this replaced decomposed into subject + connective + destination, and across the
   *  seven prose lines only this part differed. Traditional spellings are generated from the
   *  Simplified ones, so each phrase is written once. */
  prose_landed_in?: string[];
  /** THE SCORED HALF, at the same two levels as the patterns above: `note_terms` are scored
   *  against note HEADINGS (which note), `row_terms` against ROW CAPTIONS (which of its rows).
   *  Terms, not patterns — an unanticipated phrasing still ranks instead of not firing. */
  note_terms?: string[];
  row_terms?: string[];
  row_terms_none?: string[];
}
/** Sweep terms for a residual bucket — the section's unexplained remainder. */
export interface ResidualPolicy {
  framework: string;
  section_scope: string;
  population: string;
  cross_section: boolean;
  notes_as_source: boolean;
  plug: boolean;
  itemise: boolean;
}
/** How a value is NORMALISED: the convention it is stored under, plus regexes that flip its sign
 *  when the printed label matches. A silent sign inversion is one of the most expensive errors on
 *  a statement and `flip_if_label_matches` is the only field that causes one, so it is compiled on
 *  the edit path with the error attributed per index. */
export interface SignRule {
  /** Narrowed from `string`: the backend field is the six-value `SignConvention` enum, and a
   *  `string` here is how a select comes to offer a seventh value the publish gate then refuses. */
  convention: SignRuleConvention;
  flip_if_label_matches: string[];
}
export interface LineItemDef {
  key: string;
  label: string;
  type: LineItemType;
  description: string;
  /** The authoritative accounting meaning, matched against by the LLM's description tier.
   *  Separate from `description`, which is display prose. */
  definition: string;
  /** Extra instruction sent to the model beside this line's definition, when it is offered as a
   *  candidate. Only meaningful on an `extracted` line. */
  prompt: string;
  /** What this line outputs — a number, a phrase lifted from the page, or prose the model writes
   *  from `prompt`. OPTIONAL because the default is not written to storage: an existing
   *  configuration carries no such key, and absent MEANS `value`. */
  output_structure?: OutputStructure;
  in_output: boolean;
  parent: string;
  /** What this parenthood means arithmetically. The twelve parts of the depreciation line are
   *  `alternatives` — alternative sources for one figure, never addends. */
  rollup: LineItemRollup;
  order: number;
  namespace: LineItemNamespace;

  // ── the gate: where this line item may be claimed from ───────────────────────────────────
  /** Names a `section_defaults` entry; the gate is folded in from there before validation. */
  inherits: string | null;
  /** `null` means claimable on any statement — "nothing was said", not "nothing allowed". */
  statement: StatementToken | null;
  /** The banners it may be claimed under. EMPTY MEANS UNCONSTRAINED. */
  section_scope: string[];
  /** Descending tie-break for collisions the gate leaves standing. */
  match_priority: number | null;
  alias_matching: LineItemAliasMatching;
  extraction_mode: LineItemExtractionMode;
  /** Narrowed from `string` to the backend's own four-value `ValueScope`. It was already
   *  validated against that set on the edit path, so a wider type here only ever let a control
   *  offer a fifth value and collect the 422. */
  value_scope: ValueScope;
  residual_policy: ResidualPolicy | null;
  expected_components: string[];
  never_sweep: string[];
  confusable_with: string[];

  // ── containment ──────────────────────────────────────────────────────────────────────────
  /** Declares this line the gross parent of the children it already contains, so the pair is
   *  never loaded additively. Without it, 31 caption collisions had no discriminator. */
  is_gross_parent: boolean;
  children_if_decomposed: string[];
  sole_component_of: string | null;

  // ── extracted: how the caption is recognised ─────────────────────────────────────────────
  scopes: SearchScope[];
  side: LineItemSide;
  allow_contra: boolean;
  /** Only ask the model about this line when the face prints a note reference beside the row —
   *  and where it prints none, report 0 (or "" for a text line) rather than leaving it blank.
   *  `extraction_mode: extract` only; the server refuses it elsewhere. */
  llm_only_if_note_tagged: boolean;
  /** Whether a note the filing itself cites beside this line's face caption is offered first.
   *  `cited_first` by default; `any` ranks by score alone. */
  note_selection: NoteSelection;
  /** WHERE THIS LINE'S FIGURE IS READ FROM — one choice, asked only of an `extracted` line.
   *  `null` means nothing was said, which is NOT "face": a set authored before the field existed
   *  is read the way it always was. */
  route?: Route | null;
  /** Every statement this line may be claimed on. EMPTY MEANS UNCONSTRAINED — "nothing was
   *  said", not "no statement is allowed", the same convention `section_scope` uses. */
  statements?: string[];
  aliases: string[];
  /** Per-locale aliases; the matcher folds every locale into one index. */
  aliases_i18n: Record<string, string[]>;
  pattern: string;
  regex_hints: string[];
  keyword_hints: string[];
  /** Regex vetoes against the raw caption. Renamed from `exclude`, which was doing the work of
   *  two fields — these, and the prose criteria below. */
  exclude_hints: string[];
  exclude_criteria: string[];
  note_source: NoteSource | null;
  note_use: LineItemNoteUse | null;
  /** `null` means nothing was said, which is NOT `false` — v1 sets never expressed it. */
  face_only: boolean | null;
  // NO `min_confidence_to_auto_accept`. It was declared here, NON-OPTIONAL, and the server has
  // never sent it: the field was withdrawn from `LineItemDef` on the backend ("remove line item
  // level control for now") because nothing read it — all four accept decisions compare against
  // the global `settings.extraction.auto_accept_confidence`. So every item this screen rendered
  // carried `undefined` behind a type promising a number. A type that lies about the payload is
  // worse than a missing field, because the compiler stops asking. The screen names it in its
  // "not editable here" list off `LineItemVocab.not_editable`, which is where the reason lives;
  // re-add it here only alongside code that reads it.

  // ── measurement ──────────────────────────────────────────────────────────────────────────
  temporality: LineItemTemporality | null;
  unit_of_account: LineItemUnitOfAccount | null;
  /** An expectation, never a transformation — `sign_rule` performs the flip. Sent back as
   *  `sign_expectation` on an edit; see `SignExpectation`. */
  sign_convention: SignExpectation | null;
  sign_rule: SignRule | null;
  analyst_bucket: string | null;

  // ── prose a reviewer or the LLM reads ────────────────────────────────────────────────────
  decomposition_rule: string | null;
  others_rule: string | null;
  /** Which of two look-alike captions this is — resolves 30 of the 420 collisions. */
  derivation: string | null;
  /** `aggregation_note` and `template_note` were here and are gone from the model: 395 and 391 of
   *  539 items declared them, 54 KB of prose between them, and nothing in the pipeline read
   *  either. `notes_as_source_rationale` is kept because it IS read. */
  notes_as_source_rationale: string | null;

  terms: LineItemTerm[];
  /** How `terms` combine — see `TermsOp`. `sum` on every shipped formula. */
  terms_op?: TermsOp;
  cascade: CascadeRung[];
  /** The service that computes this line today, while the config only describes it. Clearing it
   *  is how an author hands the derivation over to an authored `cascade` — and a `derived` line
   *  with neither is refused, attributed to `type`. */
  implemented_by: string;
  /** Nested by the backend for display; storage is flat and keyed by `parent`. READ-ONLY: a
   *  projection of `parent` recomputed on every read, so an edit to it cannot be persisted. */
  children: LineItemDef[];
  /** WHICH FIELDS THIS ITEM ITSELF DECLARED, off the unresolved stored JSON.
   *
   *  Everything above is served RESOLVED — 475 of 475 shipped items take their gate from
   *  `section_defaults` via `inherits` — and once folded, an inherited value is indistinguishable
   *  from a declared one and from a model default. That is exactly the distinction an editor has
   *  to draw: saving a field the item never declared turns an inherited value into a declared one
   *  and silently detaches the item from its section. A field absent from this list and non-empty
   *  above was INHERITED, and the control says so. */
  declared_fields: string[];
  /** WHAT THIS LINE'S PLAIN PHRASES COMPILE TO — the sentence patterns generated from
   *  `note_source.prose_subject` and `prose_landed_in`, server-side.
   *
   *  DERIVED AND READ-ONLY. It is served so the screen is not asking anyone to trust it: the prose
   *  route is authored in words now and the patterns are generated, so without this there is
   *  nowhere to check what a phrase list actually became. Never sent back — it arrives at ITEM
   *  level rather than inside `note_source` precisely because `note_source` is posted wholesale on
   *  every save, and a generated field inside it would round-trip into storage and reappear as
   *  though someone had authored it. */
  prose_compiled?: string[];
}

/** AN INLINE EDIT TO ONE LINE ITEM — the body of `PATCH /line-items/versions/{id}/items`, mirroring
 *  `routes/line_items.py::ItemEdit`.
 *
 *  THREE STATES, and the payload has to be built with `JSON.stringify` semantics in mind because
 *  the server reads PRESENCE (`model_fields_set`), not truthiness:
 *    • ABSENT      — the field is untouched. Omit a key to leave it alone.
 *    • `null`      — "nothing was said", written where the schema has such a state
 *                    (`statement`, `match_priority`, `face_only`, `note_use`, `temporality`,
 *                    `unit_of_account`, `sign_expectation`, `analyst_bucket`, `sole_component_of`,
 *                    `inherits`, and the whole of `note_source` / `residual_policy` / `sign_rule`).
 *    • `[]` / `""` — a CONFIGURED EMPTY. Stored as empty and never re-defaulted, because an author
 *                    who cannot clear a list cannot undo their own edit.
 *  Do not drop a `null` on the way out (an `undefined` field disappears from the JSON and means
 *  "untouched" instead of "clear"), and do not substitute a default for an empty list.
 *
 *  `aliases` IS LOCALE-SCOPED. It replaces `aliases_i18n[locale]`, and the base `aliases` list as
 *  well when `locale` is the set's own default — the other locales are untouched, so editing the
 *  Chinese aliases can never clobber the English ones. That is also why `aliases_i18n` is not a
 *  field here: a map-shaped write is precisely how one locale overwrites another. Send `locale`
 *  with `aliases` or the set default is assumed.
 *
 *  EVERY EDIT PUBLISHES A NEW VERSION (`LineItemEditResult`), re-validated against the target
 *  template first. Never an in-place write: a run pins `extraction_runs.line_item_version_id`, so
 *  mutating a stored definition would retroactively change how a past run is explained.
 *
 *  Refusals come back as `LineItemEditRefusal`, addressed per field — show them on the control. */
export interface LineItemEdit {
  /** WHICH item to edit, and not itself editable. It is the endpoint's selector, and it is the
   *  identity `parent`, `terms[].ref`, `cascade[].terms[].ref`, `confusable_with`,
   *  `children_if_decomposed`, `expected_components`, `never_sweep`, `sole_component_of` and the
   *  target template's `canonical_key` all name — so an inline rename has no coherent target and
   *  no way to fix up the references. Rename via a full republish. Required, like the server's
   *  own field: a body with no key targets nothing. */
  key: string;
  /** Which locale's alias list `aliases` replaces. Omitted means the set's default locale. */
  locale?: string;

  // ── meaning: what this line IS, which is what lets a caption resolve by meaning rather than
  //    by string match. The four criteria fields are the highest-leverage controls on the screen.
  label?: string;
  description?: string;
  definition?: string;
  /** MERGED INTO `definition` — no longer configurable, and refused by the endpoint
   *  (`_NOT_CONFIGURABLE`). It stays on the wire so a set authored before the merge still parses;
   *  any stored value is folded into `definition` when the set is loaded. Author the whole
   *  instruction — what the line is, plus any rule specific to it — in `definition`. */
  prompt?: string;
  /** A number, a phrase off the page, or prose written from `prompt`. Refused unless the line is
   *  `extracted`; `prose` is refused with no prompt to write it from. */
  output_structure?: OutputStructure;
  exclude_criteria?: string[];
  /** Other keys of THIS set (unknown keys are refused, attributed to this field). Routes an
   *  unresolvable pair to review instead of letting the engine pick one at confidence 1.0. */
  confusable_with?: string[];
  /** Which of two look-alike captions this is. Read by `mapping.py` — not decoration. */

  // ── structure of the tree ─────────────────────────────────────────────────────────────────
  /** The premise of the whole model: which of the other groups carry meaning at all.
   *  `calculated`/`intermediate` with no `terms`, and `derived` with neither `cascade` nor
   *  `implemented_by`, are refused — attributed to `type` and to `terms`/`cascade`. */
  type?: LineItemType;
  /** Forced false for `type: intermediate`; an intermediate never reaches the output. */
  in_output?: boolean;
  /** Names an existing key of this set, or `""`/`null` to make the item a root. Self-reference
   *  and any cycle are refused, attributed to `parent`. */
  parent?: string | null;
  /** What the parenthood ASSERTS arithmetically. `alternatives` is what stops the rollup check
   *  summing the twelve alternative restatements of the depreciation line. */
  rollup?: LineItemRollup;
  /** Display order among siblings. DISPLAY ONLY — `match_priority` is the matching tie-break. */
  order?: number;
  /** `template` keys are held against the target template's `canonical_key`s by the publish gate;
   *  `internal` items are exempt because they name no output column. Flipping to `template` on a
   *  key the template does not declare is refused, attributed HERE and not to `key`. */
  namespace?: LineItemNamespace;
  value_scope?: ValueScope;

  // ── the gate: WHERE this line may be claimed from ─────────────────────────────────────────
  /** Names a `section_defaults` entry of this set; a dangling value is refused by name. */
  inherits?: string | null;
  /** One of `StatementToken`. `null` — or `""`, the spelling the existing editor clears with —
   *  means claimable anywhere. Typed as `string | null` on the wire and checked against the
   *  backend's `StatementType` in the apply, attributed to this field. */
  statement?: StatementToken | "" | null;
  /** The banners a caption may sit under. `[]` IS STORED and means unconstrained. */
  section_scope?: string[];
  /** Descending tie-break for the collisions the gate leaves standing. `null` is "nothing said",
   *  which is distinct from `0` — the floor residuals sit at, unreachable by matching. */
  match_priority?: number | null;
  extraction_mode?: LineItemExtractionMode;
  /** A search ORDER, not a gate — the order is meaningful and is preserved as sent. */
  scopes?: SearchScope[];
  /** `from_section` is refused unless the item can reach a section (a balance-sheet `statement`,
   *  `balance_sheet` in `scopes`, or a `section_scope` naming a side). */
  side?: LineItemSide;
  /** Whether a caption printed on the opposite side may fill this line. Off by default — a bare
   *  "Cash" once resolved to an overdraft. */
  allow_contra?: boolean;
  llm_only_if_note_tagged?: boolean;
  note_selection?: NoteSelection;
  route?: Route | null;
  /** Every statement this line may be claimed on. A list, so a caption printed on two is
   *  admitted under either; [] means unconstrained. Supersedes the singular `statement`. */
  statements?: string[];
  /** `null` disables note sourcing for this line entirely. */
  note_source?: NoteSource | null;
  note_use?: LineItemNoteUse | null;
  face_only?: boolean | null;

  // ── recognition: how the printed caption is matched ───────────────────────────────────────
  /** `disabled` makes the line unreachable by every matching tier while leaving it fillable by
   *  the residual sweep — the lock that defines a residual bucket. */
  alias_matching?: LineItemAliasMatching;
  /** THIS LOCALE'S captions only. See the locale contract in the interface doc above. */
  aliases?: string[];
  /** A single regex over the caption. Compiled server-side; the compile error is attributed
   *  here. */
  pattern?: string;
  regex_hints?: string[];
  keyword_hints?: string[];
  /** REGEX VETOES against the raw caption — a match here refuses the line. Deliberately distinct
   *  from `exclude_criteria`, which is prose: folding prose into this list either fails to compile
   *  or compiles as an accidental veto. Compiled per index on the edit path. */
  exclude_hints?: string[];

  // ── containment and residuals ─────────────────────────────────────────────────────────────
  /** Declares this line the gross parent of the children it already contains, so the pair is
   *  never loaded additively. Without it, 31 caption collisions had no discriminator. */
  is_gross_parent?: boolean;
  /** Keys of this set. Non-keys and pipe-joined strings are both refused here. */
  children_if_decomposed?: string[];
  sole_component_of?: string | null;
  /** `null` disables the policy; sending the object enables it. */
  residual_policy?: ResidualPolicy | null;
  expected_components?: string[];
  never_sweep?: string[];

  // ── measurement ───────────────────────────────────────────────────────────────────────────
  temporality?: LineItemTemporality | null;
  unit_of_account?: LineItemUnitOfAccount | null;
  /** `LineItemDef.sign_convention` — the EXPECTATION review validation reads. Named
   *  `sign_expectation` on the wire because `sign_convention` below is the legacy spelling. */
  sign_expectation?: SignExpectation | null;
  /** THE LEGACY 3-TOKEN UI SPELLING (`as_reported` / `expense_contra` / `auto`), kept because the
   *  Template screen sends it. It writes `sign_rule.convention` and can express three of the six
   *  real values — it is NOT `sign_expectation`. Prefer `sign_rule` below, which says all six. */
  sign_convention?: string;
  /** The NORMALISATION: the convention a value is stored under plus the label regexes that flip
   *  its sign. `null` clears it. */
  sign_rule?: SignRule | null;
  /** Validated against the served `LineItemVocab.analyst_buckets`. A value naming no section
   *  silently loses this line's rows to Others with nothing saying why. */
  analyst_bucket?: string | null;

  // ── assembly: the arithmetic a non-extracted line is built from ───────────────────────────
  /** The signed sum a `calculated`/`intermediate` line is assembled from. Every `ref` is checked
   *  against this set's keys, so a typo is refused rather than being a term that contributes
   *  nothing. `[]` is stored — and then refused for a calculated line, which is the point. */
  terms?: LineItemTerm[];
  /** How `terms` combine. A scalar on the wire because it is a property of the GROUP, not of
   *  any one addend — a per-term copy would be four ways to disagree about one rule. */
  terms_op?: TermsOp;
  /** The ordered attempts a `derived` line is assembled by — first rung that resolves wins, so
   *  the ORDER IS the priority and is preserved as sent. */
  cascade?: CascadeRung[];
  /** The service that computes this derivation today. Clearing it requires a `cascade`. */
  implemented_by?: string;

  // ── prose a reviewer or the model reads ───────────────────────────────────────────────────
  decomposition_rule?: string | null;
  others_rule?: string | null;
  derivation?: string | null;
  /** `aggregation_note` and `template_note` are gone from the endpoint as well as the model — an
   *  edit carrying either is now a refusal naming a field the screen no longer offers. */
  notes_as_source_rationale?: string | null;
}

/** EVERY VALUE AN EDIT MAY LEGALLY CARRY, served with the definitions (`_vocabulary`).
 *
 *  Derived on the server from the same `Literal[...]` aliases the edit body is typed on and the
 *  loader validates with, never curated: a token added to a backend enum reaches the screen the
 *  moment it exists. An editor offering a value the publish gate refuses is worse than no control
 *  at all — the author authors, saves, and is told no by a validator two layers down — so the
 *  controls are built from THESE lists and not from anything hardcoded in the frontend. */
export interface LineItemVocab {
  statements: StatementToken[];
  scopes: SearchScope[];
  sides: LineItemSide[];
  rollups: LineItemRollup[];
  /** The four ways a group of terms may combine, so the control cannot offer a fifth. */
  terms_ops?: TermsOp[];
  namespaces: LineItemNamespace[];
  types: LineItemType[];
  output_structures: OutputStructure[];
  note_selections: NoteSelection[];
  /** Where an extracted line's figure is read from — served so the control offers exactly the
   *  three routes the pipeline implements. */
  routes: Route[];
  value_scopes: ValueScope[];
  extraction_modes: LineItemExtractionMode[];
  alias_matching: LineItemAliasMatching[];
  temporalities: LineItemTemporality[];
  units_of_account: LineItemUnitOfAccount[];
  /** The EXPECTATION (`sign_expectation`)… */
  sign_expectations: SignExpectation[];
  /** …and the NORMALISATION (`sign_rule.convention`), which is a different question. */
  sign_conventions: SignRuleConvention[];
  /** The 3-token UI vocabulary `sign_convention` still accepts, for the Template screen. */
  legacy_sign_conventions: string[];
  note_uses: LineItemNoteUse[];
  term_roles: TermRole[];
  /** `services.buckets.BUCKET_KEYS`. Served so the select cannot offer a bucket the gate
   *  refuses. */
  analyst_buckets: string[];
  /** The `section_defaults` keys of THIS set — what `inherits` may name. From the set rather
   *  than from anything the client remembers, because a dangling value is not a load error but a
   *  silent no-op that leaves the item with no gate at all. */
  inherits_options: string[];
  /** THE SCOPE IDS this set uses — SUGGESTIONS, not a closed set: `section_scope` is a free list,
   *  and a filing printing an undeclared banner is exactly the case an author is here to handle.
   *
   *  THE RAW BANNER TOKENS ARE NO LONGER OFFERED. They were, alongside these, and measured on the
   *  configuration in force that doubled the list for nothing: all 20 scope ids are used by a
   *  line, and 17 of the 18 banner tokens by none. The two are not alternatives — they are the
   *  same eighteen sections in two spellings, and the token loses the section identity that
   *  `inherits`, the analyst bucket and `section_defaults` are all keyed on. */
  section_scope_tokens: string[];
  /** Which of those constrain NOTHING. `token_of_scope` returns null for a scope id naming no
   *  banner, and an empty resolved scope means unconstrained in the matcher — so choosing one is a
   *  deliberate "any banner". Seven of the twenty, relied on by 72 lines: the statement totals no
   *  banner may constrain, and the five compact sections a filing prints no banner for. */
  section_scope_unconstrained?: string[];
  /** Free strings on the model, so these are datalists too. */
  residual_frameworks: string[];
  residual_populations: string[];
  /** WHAT IS NOT AUTHORABLE HERE, AND WHY — field name → the one line that says why, served so
   *  the screen never has to restate a reason it does not own. Silently absent and read-only-for-
   *  a-reason look identical on a screen and only one of them is a decision: render every entry
   *  read-only WITH its reason. Carries `key`, `children`, `aliases_i18n` and the withdrawn
   *  `min_confidence_to_auto_accept`. */
  not_editable: Record<string, string>;
}

/** ONE REFUSAL, ADDRESSED TO THE CONTROL THAT CAUSED IT.
 *
 *  The edit endpoint re-validates against the target template before it publishes, so a refusal is
 *  information the author needs — not an error to swallow, and not a banner they read once and
 *  cannot act on. Show `message` verbatim against `field` (a paraphrase in the client is a second
 *  spelling of a rule the server owns).
 *
 *  `field` IS NULLABLE: a few refusals belong to the set rather than to a control (a rollup that
 *  does not tie, a decomposition rule) and the server sends `field: null` for those — show them at
 *  the top of the form. `index` addresses one row of a list field (`regex_hints[2]`,
 *  `terms[0].ref`, `note_source.row_caption_any[0]`). `location` survives on entries that came
 *  from the upload door (`items[3].aliasses`), which is not a control on any screen. */
export interface LineItemFieldError {
  field: string | null;
  index?: number | null;
  message: string;
  location?: string;
}
/** The 422 body of a refused edit — `detail` of the `HTTPException`. One shape for every refusal
 *  the route raises, tag included, so there is one renderer rather than a second one written later
 *  and worse. `message` is the single-sentence summary; `errors` is what the controls read, capped
 *  at 50 by the server because one bad paste can produce hundreds. */
export interface LineItemEditRefusal {
  error: string;
  message: string;
  errors: LineItemFieldError[];
}
export interface LineItemProblem {
  key: string;
  message: string;
  severity: string;
}
/** What is true of the SET rather than of an item. A bare JSON array had nowhere to say which
 *  template these keys bind to — which is the whole reason the set carries a header: the publish
 *  gate holds every key in the set against `target_template_key`. */
/** Line items an author has declared should share ONE model request.
 *
 *  SET-LEVEL, because a group is a relationship BETWEEN line items: a per-item "group" field would
 *  let two items disagree about which group they are in, and there would be no single place to
 *  read the grouping off. Read only when `extraction.llm_request_grouping` is "manual". */
export interface RequestGroup {
  name: string;
  members: string[];
  note: string;
}

export interface LineItemSetInfo {
  schema_version: number;
  line_items_key: string;
  target_template_key: string;
  locale: string;
  supported_locales: string[];
  metadata: {
    name: string; version: string; supersedes: string | null;
    changes: string[]; breaking_changes: string[];
  };
  /** THE MASTER PROMPT. Appended to the framework's base instruction on every mapping call, before
   *  the global policies. A line's own `prompt` is added on top of this, inside that line's
   *  candidate entry — so this is what a per-line prompt is adding to. */
  prompt: string;
  /** The manual grouping master. Empty is the normal state of one being built: the master says
   *  which lines SHARE a request, never which lines get one. */
  request_groups: RequestGroup[];
  /** The gate, authored once per section and claimed by `inherits`. */
  section_defaults: Record<string, Partial<{
    statement: StatementToken; section_scope: string[]; scopes: SearchScope[];
    side: LineItemSide; temporality: string; unit_of_account: string; note_use: string;
    note_use_rationale: string; sign_convention: string; match_priority: number;
    face_only: boolean; analyst_bucket: string;
  }>>;
  /** HOW A SENTENCE PLACES A FIGURE — the shared half of every prose rule, authored once for the
   *  set. A prose rule is subject + connective + destination, and measured across the seven lines
   *  that have one, the first two were identical on every line; so they live here and a line says
   *  only where the figure landed.
   *
   *  The screen needs `subjects` to offer the vocabulary names on a line, and `connective` to say
   *  what the shared half already covers — without which "just say where it landed" reads as a
   *  control that lost half its question. */
  prose_grammar?: {
    note?: string;
    connective: string[];
    subjects: Record<string, string[]>;
  };
}
export interface LineItemsResponse {
  /** WHICH STORED VERSION answered — the row a run would map against, served with every read.
   *  Declared here because the screen was reaching for it through a cast
   *  (`(q.data as { version?: LineItemVersionRef }).version`), and a cast is how a payload field
   *  gets renamed without a single reader noticing. After an edit publishes, this is the new
   *  version in force, which is what the screen has to caption itself with. Only the identity
   *  fields are sent on this one (`in_force`/`loads`/`items`/`aliases` come from
   *  `GET /line-items/versions`), which is why those are optional on the ref. */
  version: LineItemVersionRef;
  items: LineItemDef[];
  set: LineItemSetInfo;
  /** Every value the editor may offer, so no control can present one the publish gate refuses —
   *  and the reasons for the fields it must show read-only. See `LineItemVocab`. */
  vocab: LineItemVocab;
  counts: {
    total: number; output: number; sub_line_items: number;
    by_type: Record<LineItemType, number>;
    /** How many resolved a statement gate, and how many got it from a section. */
    gated: number; inherited: number;
  };
  problems: LineItemProblem[];
  valid: boolean;
  evaluation_order: string[];
}
