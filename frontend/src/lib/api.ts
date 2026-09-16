/** Thin fetch client. All calls go to /api/v1 (proxied to FastAPI in dev). */
const BASE = "/api/v1";
const PROJECT = "demo";
const TOKEN_KEY = "finex-token";

export function getToken(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}
export function setStoredToken(token: string | null): void {
  if (typeof localStorage === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// The document currently being worked through the stepper (integrity → extract → review →
// export). Persisted so a page refresh keeps the real run bound to the SAME file rather
// than silently falling back to some other document in the shared list.
const ACTIVE_DOC_KEY = "finex-active-doc";
export function getStoredActiveDoc(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(ACTIVE_DOC_KEY);
}
export function setStoredActiveDoc(id: string | null): void {
  if (typeof localStorage === "undefined") return;
  if (id) localStorage.setItem(ACTIVE_DOC_KEY, id);
  else localStorage.removeItem(ACTIVE_DOC_KEY);
}

// The HISTORICAL run being read, stored WITH the document it belongs to. Persisted for the same
// reason the active document is: the pin is a session-wide mode that changes what every screen
// means, and a refresh that silently returned the reader to the latest run is the mode change
// nobody asked for — an export downloaded after it would be a different extraction from the one
// on screen a moment earlier.
//
// STORED AS `doc|run`, and read back only when the document still matches. A run id belongs to one
// filing, so a pin restored onto a different document would name a run that document never had —
// which is the same rule `setActiveDocumentId` enforces in memory, applied to what outlives it.
const PINNED_RUN_KEY = "finex-pinned-run";
export function getStoredPinnedRun(documentId: string | null): string | null {
  if (typeof localStorage === "undefined" || !documentId) return null;
  const raw = localStorage.getItem(PINNED_RUN_KEY) ?? "";
  const cut = raw.indexOf("|");
  if (cut < 1) return null;
  return raw.slice(0, cut) === documentId ? raw.slice(cut + 1) || null : null;
}
export function setStoredPinnedRun(documentId: string | null, runId: string | null): void {
  if (typeof localStorage === "undefined") return;
  if (documentId && runId) localStorage.setItem(PINNED_RUN_KEY, `${documentId}|${runId}`);
  else localStorage.removeItem(PINNED_RUN_KEY);
}

const NAV_KEY = "finex.nav.collapsed";
/** Whether the navigation rail is collapsed. Collapsed is the DEFAULT — the screens that matter
 *  here put a statement grid beside a page of the source document, and 214px of permanent menu is
 *  taken from that. An explicit choice is remembered; no choice yet means collapsed. */
export function getStoredNavCollapsed(): boolean {
  if (typeof localStorage === "undefined") return true;
  return localStorage.getItem(NAV_KEY) !== "0";
}
export function setStoredNavCollapsed(v: boolean): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(NAV_KEY, v ? "1" : "0");
}

const LI_LIST_KEY = "finex.lineItems.listCollapsed";
/** Whether the Line Items screen's list pane is put away. OPEN is the default, and the asymmetry
 *  with the nav rail above is deliberate: the rail is a menu, and a menu hidden on a first visit
 *  costs nothing because every screen has one. This list IS the Line Items screen's subject — 527
 *  configured lines — so collapsing it by default would open the screen on a rail and a form for
 *  whichever line happened to sort first, with no sign of what the screen is for.
 *
 *  An explicit choice is remembered; no choice yet means open. */
export function getStoredLiListCollapsed(): boolean {
  if (typeof localStorage === "undefined") return false;
  return localStorage.getItem(LI_LIST_KEY) === "1";
}
export function setStoredLiListCollapsed(v: boolean): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(LI_LIST_KEY, v ? "1" : "0");
}

/** Error carrying the HTTP status so callers (e.g. auth gating) can special-case 401.
 *  `detail` is the server's own explanation when it sent one — editors show it verbatim
 *  rather than a generic failure, so a rejected value says WHY it was rejected.
 *
 *  `code` is the machine-readable `detail.error` the judgement endpoints send instead of a
 *  sentence. Three different refusals share status 409 there — the figures moved, the subject is a
 *  conflict the queue cannot resolve, the write itself lost a race — and one status cannot tell
 *  them apart, so a screen that explains a 409 must read the code rather than assume the first
 *  meaning.
 *
 *  `fields` is the PER-FIELD refusal list (`detail.errors[]`), carried so an editor can put the
 *  server's message on the control that caused it. The configuration edit re-validates the whole
 *  set against the target template before it publishes, so a refusal names one field out of forty
 *  — and a forty-field form told only "422, this edit was not applied" leaves the author hunting.
 *  Undefined for every response that carries no such list, which is most of them: a screen must be
 *  able to tell "no per-field information" from "no problems". */
export class ApiError extends Error {
  status: number;
  detail?: string;
  code?: string;
  fields?: LineItemFieldError[];
  constructor(status: number, message: string, detail?: string, code?: string,
              fields?: LineItemFieldError[]) {
    super(message);
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.fields = fields;
  }
}

/** WHAT THE SERVER SAID IT REFUSED, for a screen that has to print it. `detail` is preferred over
 *  the status line because it names the thing — the concept, the status, the missing run — and a
 *  reader told only "500" has nothing to act on. Empty for anything that is not an error at all.
 *
 *  Lives here rather than in a screen because two of them print refusals the same way, and a second
 *  copy of this is a second place for "the server's own words" to quietly become "Error: 500". */
export function refusalText(err: unknown): string {
  if (err instanceof ApiError) return err.detail ?? err.message;
  return err instanceof Error ? err.message : "";
}

/** Pull FastAPI's `detail` out of an error body — the server's own sentence about what it refused.
 *
 *  TWO SHAPES, because the routes send two. Most raise `HTTPException(detail="a sentence")`; the
 *  configuration routes raise a STRUCTURED detail (`{error, message, errors[]}` —
 *  `routes/line_items.py::_refuse`) so one refusal can carry a summary AND a per-field list. This
 *  used to return the string shape only, which meant a structured detail was DROPPED ENTIRELY and
 *  the screen printed "422 Unprocessable Content" with none of the server's words — the worst case
 *  of the two, since a configuration refusal is the one an author is expected to act on.
 *
 *  `detail.message` is the sentence in that shape. FastAPI's own request-validation 422 sends
 *  `detail` as an ARRAY of `{loc, msg, type}`, which has no `message` and is not a sentence
 *  anybody wants shown, so arrays fall through to undefined and the status line stands. */
function errorDetail(text: string): string | undefined {
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

/** Pull `detail.errors[]` — the refusals ADDRESSED TO A FIELD — out of a structured error body.
 *
 *  The edit endpoint validates every field in one pass and writes nothing until there are no
 *  problems, so a refused save comes back naming each field that has to change (`field`), which
 *  entry of a list field it is (`index`), and the server's message verbatim. Read here rather than
 *  in a screen because the shape is the wire's, not one screen's, and a second copy of this parse
 *  is a second place for the server's words to quietly become "Error: 422".
 *
 *  NOT PARAPHRASED and NOT REORDERED: `message` is the only statement of a rule this client does
 *  not own. `field: null` is kept as null rather than dropped — those are the refusals that belong
 *  to the set rather than to a control (a rollup that does not tie), and a form has to show them
 *  somewhere. Entries with no `message` are skipped: there is nothing to put on a control.
 *
 *  Undefined, not `[]`, when the body carries no such list — "the server said nothing per field"
 *  and "the server said there are no field problems" are different answers. */
function errorFields(text: string): LineItemFieldError[] | undefined {
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    const detail = body.detail;
    if (!detail || typeof detail !== "object" || Array.isArray(detail)) return undefined;
    const raw = (detail as { errors?: unknown }).errors;
    if (!Array.isArray(raw)) return undefined;
    const out: LineItemFieldError[] = [];
    for (const entry of raw) {
      if (!entry || typeof entry !== "object") continue;
      const e = entry as { field?: unknown; index?: unknown; message?: unknown;
                           location?: unknown };
      if (typeof e.message !== "string") continue;
      out.push({
        field: typeof e.field === "string" ? e.field : null,
        index: typeof e.index === "number" ? e.index : null,
        message: e.message,
        // `location` survives only on entries that came from the upload door
        // (`items[3].aliasses`), which is not a control on any screen — kept so the banner can
        // still say where, omitted rather than blanked so a reader can test for it.
        ...(typeof e.location === "string" ? { location: e.location } : {}),
      });
    }
    return out.length ? out : undefined;
  } catch {
    return undefined;
  }
}

/** Pull `detail.error` out of a structured error body — the endpoint's own name for what it
 *  refused. Undefined when the body carries no such code, which is what keeps a caller from
 *  reporting a specific cause it was never told. */
function errorCode(text: string): string | undefined {
  try {
    const body = JSON.parse(text) as { detail?: { error?: unknown } };
    const code = body.detail && typeof body.detail === "object"
      ? (body.detail as { error?: unknown }).error : undefined;
    return typeof code === "string" ? code : undefined;
  } catch {
    return undefined;
  }
}

function authHeader(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeader(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, `${res.status} ${res.statusText} — ${text}`,
                       errorDetail(text), errorCode(text), errorFields(text));
  }
  if (res.status === 204) return undefined as T; // no content (e.g. DELETE)
  return res.json() as Promise<T>;
}

import type {
  AdminAuditResponse,
  AnalysisResponse,
  AppSettings,
  AuditEntry,
  AuditResponse,
  Basis,
  CellContext,
  Commentary,
  ConfigurationRecord,
  DemoUser,
  DocSearchResult,
  DocumentRunStatus,
  DocumentRunSummary,
  ExportFmt,
  ExportOption,
  ExtractionRunResponse,
  FxRate,
  FxRateInput,
  FxRateResolution,
  IntegrityResponse,
  LineItemEdit,
  LineItemEditResult,
  LineItemFieldError,
  LineItemSchema,
  LineItemVersionRef,
  Locale,
  LoginResponse,
  MappingEdit,
  Me,
  NoteDetail,
  NotesResponse,
  PagesResponse,
  ProjectResponse,
  ReviewResponse,
  SettingsPatch,
  SourceDoc,
  LineItemsResponse,
  StatementKey,
  StatementResponse,
  TemplateRef,
  TemplateResponse,
} from "../types";
// A VALUE, not a type: the one extension-per-format map, so the saved filename cannot disagree
// with the format that was requested (`format === "excel" ? ".xlsx" : ".json"` had no third arm).
import { EXPORT_EXT } from "../types";

export const api = {
  // --- auth / identity ---
  login: (username: string, password?: string) =>
    req<LoginResponse>(`/auth/login`, {
      method: "POST",
      body: JSON.stringify({ username, password: password ?? null }),
    }),
  logout: () => req<{ ok: boolean }>(`/auth/logout`, { method: "POST" }),
  demoUsers: () => req<{ users: DemoUser[]; demo_mode: boolean }>(`/auth/demo-users`),
  me: () => req<Me>(`/me`),
  // --- settings ---
  settings: () => req<AppSettings>(`/settings`),
  patchSettings: (body: SettingsPatch) =>
    req<AppSettings>(`/settings`, { method: "PATCH", body: JSON.stringify(body) }),
  // --- FX rate master (admin-maintained; drives presentation currency conversion) ---
  /** The whole master — readable by any authenticated user (the Workspace needs it). */
  fxRates: () => req<{ rates: FxRate[] }>(`/fx-rates`),
  /** Ask the master for one pair. Resolution happens server-side so the reciprocal of a
   *  stored rate is computed in exact decimal, and comes back labelled as derived. */
  resolveFxRate: (base: string, quote: string) =>
    req<FxRateResolution>(
      `/fx-rates/resolve?base=${encodeURIComponent(base)}&quote=${encodeURIComponent(quote)}`,
    ),
  /** Create or restate a rate for a pair + as-of date (admin). */
  upsertFxRate: (body: FxRateInput) =>
    req<FxRate>(`/fx-rates`, { method: "POST", body: JSON.stringify(body) }),
  updateFxRate: (id: string, body: FxRateInput) =>
    req<FxRate>(`/fx-rates/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteFxRate: (id: string) => req<void>(`/fx-rates/${id}`, { method: "DELETE" }),
  /** Hand the output to the reviewer. `documentId` says WHOSE filing is being submitted: the
   *  Export screen serves an uploaded document and the seeded sample from the same controls, and
   *  without it the server had no choice but to name the demo company in the audit entry. Omitted
   *  for the sample project, which is the only case that name is right for. The server refuses a
   *  document whose run has not named an entity rather than guessing one. */
  submitForReview: (documentId?: string) =>
    req<{ ok: boolean; entry: AuditEntry }>(
      `/projects/${PROJECT}/submit-review${documentId ? `?document_id=${encodeURIComponent(documentId)}` : ""}`,
      { method: "POST" }),
  commentary: (locale: Locale = "en") =>
    req<Commentary>(`/projects/${PROJECT}/commentary?locale=${locale}`),
  audit: () => req<AuditResponse>(`/projects/${PROJECT}/audit`),
  /** One uploaded document's audit trail — the runs against THAT filing.
   *
   *  A separate route because the trail is keyed by what was run against, and runs against a
   *  document have always been recorded under its id. Asking the demo project's route for them (the
   *  only thing the Analysis screen used to do) returned the sample's rows and never a real run's,
   *  so extraction and credit-narrative entries were written and never readable. */
  documentAudit: (documentId: string) =>
    req<AuditResponse>(`/documents/${encodeURIComponent(documentId)}/audit`),
  runAnalysis: () =>
    req<{ entry: AuditEntry; result: unknown }>(`/projects/${PROJECT}/analysis`, { method: "POST" }),
  project: () => req<ProjectResponse>(`/projects/${PROJECT}`),
  documents: () => req<{ documents: SourceDoc[] }>(`/documents`),
  /** Every stored version of THE configuration — the line-item sets, newest edit of each key
   *  first. This was `ontologies()` on `/ontologies`; line items is the single configuration
   *  engine, so there is one store of versions to list and no engine to pick between. */
  lineItemVersions: () => req<LineItemVersionRef[]>(`/line-items/versions`),
  templates: () => req<TemplateRef[]>(`/templates`),
  /** Start a run. The 202 carries the rulebook the run was CREATED with, so the screen can name
   *  what governs the figures from the moment the run exists rather than waiting for a result — or
   *  deciding for itself which rulebook that must have been. */
  /** Start an extraction — or, without `force`, get back the run this document ALREADY has on the
   *  same options. The endpoint is idempotent per (document, resolved template, resolved rulebook,
   *  the options that steer the pipeline), which is what lets a screen fire this on arrival without
   *  starting the filing over every time somebody navigates back to it. `force` is how the
   *  re-extract control says "another run of the same thing", which is a different request and
   *  cannot be told apart at the endpoint otherwise. `adopted` marks a response that handed back an
   *  existing run rather than starting one. */
  runExtraction: (
    documentId: string,
    body: {
      // WHICH CONFIGURATION the run maps against — a `line_item_versions` row. The field was
      // `ontology_version_id`; there is one configuration engine now, so a run pins a line-item
      // version and nothing else.
      line_item_version_id?: string; template_version_id?: string; force?: boolean;
    } = {},
  ) =>
    req<{ run_id: string; status: string; rulebook?: ConfigurationRecord | null;
          adopted?: boolean }>(
      `/documents/${documentId}/extractions`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
  /** Poll a background extraction run's status/result. */
  getRun: (runId: string) => req<ExtractionRunResponse>(`/extractions/${runId}`),
  stopRun: (runId: string) =>
    req<ExtractionRunResponse>(`/extractions/${runId}/cancel`, { method: "POST" }),
  /** Real per-document pre-flight integrity (drives the Integrity screen for an upload). */
  documentIntegrity: (documentId: string, locale: Locale = "en") =>
    req<IntegrityResponse>(`/documents/${documentId}/integrity?locale=${locale}`),
  /** Real per-document review queue, derived from the latest extraction (unmapped + low
   * confidence). */
  documentReview: (documentId: string, locale: Locale = "en") =>
    req<ReviewResponse>(`/documents/${documentId}/review?locale=${locale}`),
  /** Record that a named person examined a finding's figures and judged that they stand. The key
   *  travels in the BODY, not the path: a structural finding's scope key contains "/" and no
   *  check identifier is URL-safe. `evidenceDigest` is the figures the human was looking at —
   *  the server refuses the acceptance with 409 when they have since moved. */
  acceptFinding: (
    documentId: string, subjectKey: string, evidenceDigest: string, reason: string,
    locale: Locale = "en",
  ) =>
    req<{ ok: boolean; subject_key: string; status: string }>(
      `/documents/${documentId}/review/judgements?locale=${locale}`,
      { method: "POST", body: JSON.stringify({
          subject_key: subjectKey, evidence_digest: evidenceDigest, reason }) },
    ),
  /** Withdraw an acceptance. The row is not deleted — the verdict changes and the history keeps
   *  who accepted what — because erasing the record of who vouched for a break is not something
   *  an audit trail should permit. The 64-hex subject key is URL-safe. */
  withdrawAcceptance: (documentId: string, subjectKey: string) =>
    req<{ ok: boolean; subject_key: string; withdrawn: boolean }>(
      `/documents/${documentId}/review/judgements/${subjectKey}`, { method: "DELETE" },
    ),
  /** Re-map one printed row onto a different template line — the way a row-shaped review finding
   *  is RESOLVED. `canonicalKey: ""` un-maps the row, which is the only route back from a re-map
   *  that started from unmapped. Errors are meaningful and must reach the card: 409 means the row
   *  reference is ambiguous or the row already carries that concept, 422 that the target is not a
   *  line this run's template offers. */
  remapReviewRow: (documentId: string, rowRef: string, canonicalKey: string, reason: string,
                   locale: Locale = "en") =>
    req<{ ok: boolean; row_ref: string; label: string; from: string; to: string;
          remap: { from: string; to: string; reason: string; by: string; at: string } }>(
      `/documents/${documentId}/review/remap?locale=${locale}`,
      { method: "POST", body: JSON.stringify({ row_ref: rowRef, canonical_key: canonicalKey,
                                               reason }) },
    ),
  /** Derived analysis for a document: computed ratios, disclosure scan, free-form notes. */
  /** Derived analysis — ratios, notes and the disclosure scan — of the latest run, or of one
   *  NAMED historical run when `runId` is given. The Disclosures screen carries the
   *  contingent-liability findings, so without this it went on showing the newest extraction
   *  while the Workspace, the notes and the export had all moved onto a pinned past run. */
  documentAnalysis: (documentId: string, locale: Locale = "en", runId?: string) =>
    req<AnalysisResponse>(`/documents/${documentId}/analysis?locale=${locale}`
      + (runId ? `&run_id=${encodeURIComponent(runId)}` : "")),
  /** Real per-document notes index + detail, from line-item note references — of the latest
   *  run, or of one NAMED historical run when `runId` is given, so the notes shown beside a
   *  pinned statement belong to the same extraction as the statement. */
  documentNotes: (documentId: string, runId?: string) =>
    req<NotesResponse>(`/documents/${documentId}/notes`
      + (runId ? `?run_id=${encodeURIComponent(runId)}` : "")),
  /** One note's detail. `locale` is passed because the response now carries the note's own
   *  column labels, and their Current/Prior fallback is localized server-side. */
  documentNote: (documentId: string, no: string, locale: Locale = "en", runId?: string) =>
    req<NoteDetail>(`/documents/${documentId}/notes/${no}?locale=${locale}`
      + (runId ? `&run_id=${encodeURIComponent(runId)}` : "")),
  /** Edit ONE figure of a real extraction: a concept, in one basis, for one period.
   *  Basis and period are required, not defaulted — without them every edit landed on the
   *  consolidated current column, so editing the standalone grid or the prior year did nothing
   *  visible. The response echoes the figures the grid will now show.
   *  `period` is a slot name ("current"/"prior") OR a literal period label, which the server
   *  resolves the same way: a review finding's fix names the period as the filing printed it. */
  editDocumentLineItem: (
    documentId: string, key: string, value: number | null, formula: string,
    basis: Basis, period: string, comment = "",
  ) =>
    req<{
      status: string; value: string | null; label: string; comment: string;
      current: number | null; prior: number | null; combined_from: number;
    }>(
      `/documents/${documentId}/line-items/${encodeURIComponent(key)}`,
      { method: "PATCH", body: JSON.stringify({ value, formula, basis, period, comment }) },
    ),
  /** Revert an edited line item to its original machine-extracted values. */
  revertDocumentLineItem: (documentId: string, key: string) =>
    req<{ reverted: boolean }>(
      `/documents/${documentId}/line-items/${encodeURIComponent(key)}`,
      { method: "DELETE" },
    ),
  /** The latest extraction run for a document (drives the Export preview/counts) — or one
   *  NAMED historical run when `runId` is given, the same run a caller picked off `documentRuns`. */
  documentRun: (documentId: string, runId?: string) =>
    req<ExtractionRunResponse>(
      `/documents/${documentId}/run${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`),
  /** Every extraction run against this document, newest first — the Workspace's run picker. */
  documentRuns: (documentId: string) =>
    req<{ runs: DocumentRunSummary[] }>(`/documents/${documentId}/runs`),
  /** Whether a document has an extraction IN FLIGHT, and how far it has got — the per-document
   *  question, asked with no run id.
   *
   *  `documentRun` above cannot answer it: that route 404s until a run has a RESULT, which is
   *  exactly the state a working run is not in, and `getRun` needs an id a freshly loaded page does
   *  not have. `status: "none"` is a normal 200 answer meaning this document has never been
   *  extracted — never an error, so a caller does not have to read a status code to tell "extracting"
   *  from "nothing here". */
  documentRunStatus: (documentId: string) =>
    req<DocumentRunStatus>(`/documents/${documentId}/run-status`),
  /** Real per-page classification for the Page Scope screen (available pre-extraction). */
  documentPages: (documentId: string) =>
    req<PagesResponse>(`/documents/${documentId}/pages`),
  /** Persist the user's page selection for extraction (the Page Scope toggles). Extraction
   * then restricts itself to these pages; an empty list resets to the default (all face/notes). */
  setDocumentScope: (documentId: string, includedPages: number[]) =>
    req<{ ok: boolean; included_pages: number[]; count: number }>(
      `/documents/${documentId}/scope`,
      { method: "PUT", body: JSON.stringify({ included_pages: includedPages }) },
    ),
  /** The configured line items — the eight output lines and their sub-line items.
   *
   *  Signature unchanged; the RESPONSE now also carries `version` (which stored version answered,
   *  so the screen can caption itself with the row a run would map against instead of assuming)
   *  and `vocab` (every value an edit may legally carry, plus the reasons for the fields shown
   *  read-only). Both are declared on `LineItemsResponse`, so a screen reads them off the type
   *  rather than through a cast — a cast is how a payload field gets renamed with no reader
   *  noticing. `GET /line-items` selects latest-stored-wins, which is what makes invalidating
   *  `["line-items"]` after an edit show the newly published version as the one in force. */
  lineItems: () => req<LineItemsResponse>("/line-items"),
  /** Data-driven commentary computed from a document's real extraction (not the demo). */
  documentCommentary: (documentId: string, locale: Locale = "en") =>
    req<Commentary>(`/documents/${documentId}/commentary?locale=${locale}`),
  /** One statement of a document's real extraction, grouped for the Workspace grid — or the same
   *  statement from one NAMED historical run when `runId` is given. */
  documentStatement: (documentId: string, statement: StatementKey, basis: Basis,
                      locale: Locale = "en", runId?: string) =>
    req<StatementResponse>(
      `/documents/${documentId}/statement?statement=${statement}&basis=${basis}&locale=${locale}`
      + (runId ? `&run_id=${encodeURIComponent(runId)}` : ""),
    ),
  /** A window of spreadsheet cells around a value's origin — the Excel click-to-source
   * backdrop (mirrors fetchPageImage for PDFs). */
  cellContext: (documentId: string, sheet: string, cell: string) =>
    req<CellContext>(
      `/documents/${documentId}/cell-context?sheet=${encodeURIComponent(sheet)}&cell=${encodeURIComponent(cell)}`,
    ),
  /** Find text in a PDF's text layer: page + normalized box + the line it sits on. The viewer
   *  renders page IMAGES, so it has no text of its own to search. */
  searchDocument: (documentId: string, q: string, limit = 60) =>
    req<DocSearchResult>(
      `/documents/${documentId}/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  /** PNG of a PDF page (auth'd fetch → blob), used as the click-to-source backdrop. */
  fetchPageImage: async (documentId: string, pageIndex: number): Promise<Blob> => {
    const res = await fetch(`${BASE}/documents/${documentId}/pages/${pageIndex}/image`, {
      headers: { ...authHeader() },
    });
    if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText}`);
    return res.blob();
  },
  uploadDocument: async (file: File): Promise<{ id: string; page_count: number; integrity_report: unknown }> => {
    const fd = new FormData();
    fd.append("file", file);
    // No JSON Content-Type — let the browser set the multipart boundary.
    const res = await fetch(`${BASE}/documents`, { method: "POST", headers: { ...authHeader() }, body: fd });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new ApiError(res.status, `${res.status} ${res.statusText} — ${text}`);
    }
    return res.json();
  },
  deleteDocument: (id: string) =>
    req<void>(`/documents/${id}`, { method: "DELETE" }),
  /** Generate an LLM credit narrative that rationalises the deterministic credit view. */
  creditNarrative: (id: string, locale: Locale = "en") =>
    req<{ narrative: string; provider: string; model: string }>(
      `/documents/${id}/credit-narrative?locale=${locale}`, { method: "POST" }),
  integrity: (locale: Locale = "en") =>
    req<IntegrityResponse>(`/projects/${PROJECT}/integrity?locale=${locale}`),
  pages: (locale: Locale = "en") =>
    req<PagesResponse>(`/projects/${PROJECT}/pages?locale=${locale}`),
  statement: (statement: StatementKey, basis: Basis, locale: Locale = "en") =>
    req<StatementResponse>(`/projects/${PROJECT}/statements/${statement}?basis=${basis}&locale=${locale}`),
  /** Edit / revert one FIGURE of the seeded sample project. Named `editProjectLineItem` because
   *  `editLineItem` below is the CONFIGURATION edit (a line item's matching rules): the two are
   *  different things on different routes, and one name for both is how a figure edit and a
   *  configuration publish come to look interchangeable. */
  editProjectLineItem: (id: string, value: number | null, formula: string) =>
    req<{ id: string; value: number | null; formula: string }>(
      `/projects/${PROJECT}/line-items/${id}`,
      { method: "PATCH", body: JSON.stringify({ value, formula }) },
    ),
  revertProjectLineItem: (id: string) =>
    req<{ id: string }>(`/projects/${PROJECT}/line-items/${id}`, { method: "DELETE" }),
  notes: (locale: Locale = "en") => req<NotesResponse>(`/projects/${PROJECT}/notes?locale=${locale}`),
  note: (no: string, locale: Locale = "en") =>
    req<NoteDetail>(`/projects/${PROJECT}/notes/${no}?locale=${locale}`),
  review: (locale: Locale = "en") => req<ReviewResponse>(`/projects/${PROJECT}/review?locale=${locale}`),
  template: (locale: Locale = "en") => req<TemplateResponse>(`/projects/${PROJECT}/template?locale=${locale}`),
  exportOptions: () => req<{ options: ExportOption[] }>(`/projects/${PROJECT}/export-options`),
  exportUrl: () => `${BASE}/projects/${PROJECT}/export`,
  /** A real configured template rendered into the tree + the per-node config the Template screen
   *  shows (aliases/sign/netting, read from the line-item version that targets it). */
  templateDetail: (id: string, locale: Locale = "en") =>
    req<TemplateResponse>(`/templates/${id}/detail?locale=${locale}`),
  languages: () =>
    req<{ languages: { locale: string; name: string; rtl: boolean; supported: boolean }[]; fully_supported: string[] }>(
      `/languages`,
    ),
  /** Author a template from the frontend: POST a template definition (validated + versioned
   *  server-side). Returns the new version's id/key so it can be selected on Upload. */
  createTemplate: (definition: unknown) =>
    req<{ id: string; template_key: string; version: number }>(
      `/templates`, { method: "POST", body: JSON.stringify({ definition }) }),
  /** Publish a line-item set as a NEW VERSION of the configuration, FOR a template. This was
   *  `createOntology` on `/ontologies`; the store it wrote to is gone and `POST /line-items` is
   *  the only door into the one that replaced it. `targetTemplateKey` re-points a set authored
   *  against another template; it still has to validate against the one named, so a key the
   *  template doesn't define comes back as a 422 listing it. */
  publishLineItems: (definition: unknown, targetTemplateKey?: string) =>
    req<{ id: string; line_items_key: string; target_template_key: string; version: number;
          items: number }>(
      `/line-items`, {
        method: "POST",
        body: JSON.stringify({ definition, target_template_key: targetTemplateKey ?? null }),
      }),
  /** The shape an authored configuration must have, generated from the model the upload gate
   *  validates with: the JSON Schema plus a flat, per-field index to read it by. Admin-only,
   *  like every other configuration call — gate the control on `config:line_items`. */
  lineItemSchema: () => req<LineItemSchema>(`/line-items/schema`),
  /** One stored version's full definition — for "download, edit, upload back". `loads` is the
   *  server's own verdict on whether today's schema can still read the row. */
  lineItemVersionDetail: (id: string) =>
    req<{ id: string; line_items_key: string; target_template_key: string; version: number;
          created_at?: string | null; definition: unknown; loads?: boolean }>(
      `/line-items/versions/${id}`),
  /** What the template workbook's columns mean, straight from the reader that enforces them. */
  templateXlsxColumns: () =>
    req<{ columns: { key: string; header: string }[];
          kinds: { value: string; help: string }[]; required: string[] }>(
      `/templates/xlsx/columns`),
  /** Publish an edited template workbook as a NEW version of `templateKey` (blank = a new
   *  template named after the file). Nothing is overwritten: a past run still explains itself
   *  against the version it actually used. */
  uploadTemplateXlsx: async (
    file: File, templateKey: string, name: string,
  ): Promise<{ id: string; template_key: string; name: string; version: number;
               line_items: number }> => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("template_key", templateKey);
    fd.append("name", name);
    const res = await fetch(`${BASE}/templates/xlsx`,
                            { method: "POST", headers: { ...authHeader() }, body: fd });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new ApiError(res.status, `${res.status} ${res.statusText} — ${text}`,
                         errorDetail(text));
    }
    return res.json();
  },
  /** Edit ONE line item's DEFINITION inline — every authorable field on it, not a subset: the
   *  meaning fields (`description`, `definition`, the include/exclude criteria, `confusable_with`),
   *  the tree, the gate, recognition, measurement and the assembly arithmetic. The server validates
   *  the result and publishes a NEW line-item version (so a past extraction still explains itself
   *  against the version it actually used); the response carries that new version's id/number.
   *
   *  THE BODY IS `LineItemEdit`, which is the wire's own shape. Presence decides what changes —
   *  a key left out is untouched, an explicit `null` writes "nothing was said", and `[]`/`""` is a
   *  configured empty that is stored as empty and never re-defaulted. So do NOT strip `null`s or
   *  substitute a default on the way in: an author who cannot clear a list cannot undo their edit.
   *
   *  Was `editOntologyMapping` on `/ontologies/{id}/mappings`. The endpoint's own body names the
   *  item `key` — the ontology's `canonical_key` named a concept space that no longer exists —
   *  and the edit object handed in here is passed through unchanged.
   *
   *  `MappingEdit` IS STILL IN THE UNION, and only for `Template.tsx`, which is not in this
   *  change and still sends `canonical_key` / `include` / `exclude`. It is a BRIDGE, not a choice:
   *  drop it from this union and delete the interface the moment that screen sends `LineItemEdit`
   *  (the note on `MappingEdit` in `types.ts` says the same). Nothing new should be typed on it —
   *  the server declares `key` required and ignores keys it does not know, so an edit sent in the
   *  old spelling is a save that quietly does nothing, which is the defect class being closed.
   *
   *  A refusal comes back as 422 with `detail.errors[]` addressed per field; `req` lifts that onto
   *  `ApiError.fields` so the editor can show each message against the control that caused it.
   *
   *  THE NETTING-RULE EDIT THAT SAT BESIDE THIS IS GONE with the route that served it
   *  (`PATCH /ontologies/{id}/netting-rules`). Netting is part of the line-item set, so it is
   *  published like any other part of it — through the one configuration engine, not through a
   *  second ontology-shaped door. */
  editLineItem: (lineItemVersionId: string, edit: LineItemEdit | MappingEdit) =>
    req<LineItemEditResult & { key: string }>(
      `/line-items/versions/${lineItemVersionId}/items`,
      { method: "PATCH", body: JSON.stringify(edit) }),
  /** Edit the CONFIGURATION'S OWN settings — currently the master prompt.
   *
   *  Distinct from `editLineItem`, which edits one line: this is appended to the base instruction
   *  on every mapping call, so it is what a per-line prompt gets added to. Publishes a new version
   *  like every other edit. */
  editLineItemSet: (lineItemVersionId: string, edit: { prompt?: string }) =>
    req<LineItemEditResult>(
      `/line-items/versions/${lineItemVersionId}`,
      { method: "PATCH", body: JSON.stringify(edit) }),
  /** ADD a line item beyond what the template asked for.
   *
   *  Only key/label/inherits are sent; everything else is configured afterwards through
   *  `editLineItem`, which is the one place that knows how to validate each field and address a
   *  refusal to the control that caused it. The server makes every added item
   *  `namespace: "internal"` — "template" is not a namespace a request can ask for, because the
   *  template is the only thing that may put an item in it. */
  addLineItem: (lineItemVersionId: string,
                item: { key: string; label?: string; inherits?: string }) =>
    req<LineItemEditResult & { key: string }>(
      `/line-items/versions/${lineItemVersionId}/items`,
      { method: "POST", body: JSON.stringify(item) }),
  /** DELETE a line item the author added.
   *
   *  A template line's item is refused with 409 `template_item_protected`, and that refusal is the
   *  server's, not this screen's: the output has a column for that figure, so removing its
   *  configuration would leave a line nothing can fill. Hiding the control would leave the same
   *  delete one call away — so the UI hides it as a courtesy and the rule lives on the endpoint. */
  deleteLineItem: (lineItemVersionId: string, key: string) =>
    req<LineItemEditResult & { deleted: string; now_dangling_references_in?: string[] }>(
      `/line-items/versions/${lineItemVersionId}/items/${encodeURIComponent(key)}`,
      { method: "DELETE" }),
  /** The deployment-wide run trail (admin only — `audit:view`). */
  adminAudit: (limit = 500) => req<AdminAuditResponse>(`/audit?limit=${limit}`),
  listTemplates: () =>
    req<{ id: string; template_key: string; name: string; version: number }[]>(`/templates`),
};

/** POST the export request and trigger a browser download of the returned file. */
export async function downloadExport(body: {
  format: ExportFmt;
  basis: Basis;
  currency: string;
  units: string;
  include: Record<string, boolean>;
}): Promise<void> {
  const res = await fetch(api.exportUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = body.format === "excel" ? "spread.xlsx" : `extract.${EXPORT_EXT[body.format]}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Save an authenticated download to disk, preferring the name the SERVER chose.
 *  The server puts the source version in the filename, so the file the user edits says which
 *  version it was generated from — a detail a client-side name loses. (It had a second caller,
 *  the ontology skeleton download; that is gone, and this stays generic for the next one.) */
async function saveAsFile(res: Response, fallbackName: string): Promise<void> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, `${res.status} ${res.statusText}`, errorDetail(text));
  }
  const disp = res.headers.get("content-disposition") || "";
  const named = /filename="?([^";]+)"?/.exec(disp)?.[1];
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = named || fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Download a template as the editable workbook (auth'd fetch → blob, like the exports). */
export async function downloadTemplateXlsx(templateId: string, fallbackName: string): Promise<void> {
  await saveAsFile(
    await fetch(`${BASE}/templates/${templateId}/xlsx`, { headers: { ...authHeader() } }),
    `${fallbackName}.xlsx`,
  );
}

// THE SKELETON DOWNLOAD IS GONE. It fetched `/ontologies/skeleton?template_id=…` — a
// ready-to-edit ontology of empty stubs, one per canonical_key — and both the route and the
// generator behind it went with the ontology store. Authoring starts from the configuration
// already in force: `api.lineItemVersionDetail` serves a stored version's full definition to edit
// and `api.publishLineItems` publishes it back, and `api.lineItemSchema` states the shape. A set
// of stubs that recognises nothing is refused by that gate anyway (422 `recognises_nothing`), so
// there is nothing left for a skeleton to be the start of.

/** GET a REAL document's export (built from its latest extraction) and download it. Excel
 * uses the formatted, template-driven statement layout, localized to `locale`. CSV is the
 * two-column flat dump; `units` still applies to it (it is a conversion of the figures),
 * `include` does not (a two-column sheet has no analysis sheets to add). */
export async function downloadDocumentExport(
  documentId: string, format: ExportFmt, locale: Locale = "en", include?: string[],
  units?: string, runId?: string,
): Promise<void> {
  const inc = include && format === "excel" ? `&include=${include.join(",")}` : "";
  const un = units ? `&units=${encodeURIComponent(units)}` : "";
  // A pinned run must reach the download too: exporting the latest run while the screen shows
  // an older one hands the analyst a file that does not match what they were reading.
  const rid = runId ? `&run_id=${encodeURIComponent(runId)}` : "";
  const res = await fetch(
    `${BASE}/documents/${documentId}/export?fmt=${format}&layout=statement&locale=${locale}${inc}${un}${rid}`,
    { headers: { ...authHeader() } },
  );
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `extract.${EXPORT_EXT[format]}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
