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

/** Error carrying the HTTP status so callers (e.g. auth gating) can special-case 401.
 *  `detail` is the server's own explanation when it sent one — editors show it verbatim
 *  rather than a generic failure, so a rejected value says WHY it was rejected.
 *
 *  `code` is the machine-readable `detail.error` the judgement endpoints send instead of a
 *  sentence. Three different refusals share status 409 there — the figures moved, the subject is a
 *  conflict the queue cannot resolve, the write itself lost a race — and one status cannot tell
 *  them apart, so a screen that explains a 409 must read the code rather than assume the first
 *  meaning. */
export class ApiError extends Error {
  status: number;
  detail?: string;
  code?: string;
  constructor(status: number, message: string, detail?: string, code?: string) {
    super(message);
    this.status = status;
    this.detail = detail;
    this.code = code;
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

/** Pull FastAPI's `detail` out of an error body; undefined when it isn't a plain message. */
function errorDetail(text: string): string | undefined {
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : undefined;
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
                       errorDetail(text), errorCode(text));
  }
  if (res.status === 204) return undefined as T; // no content (e.g. DELETE)
  return res.json() as Promise<T>;
}

import type {
  AnalysisResponse,
  AppSettings,
  AuditEntry,
  AuditResponse,
  Basis,
  CellContext,
  Commentary,
  DemoUser,
  DocSearchResult,
  DocumentRunStatus,
  ExportFmt,
  ExportOption,
  ExtractionRunResponse,
  FxRate,
  FxRateInput,
  FxRateResolution,
  IntegrityResponse,
  Locale,
  LoginResponse,
  MappingEdit,
  Me,
  NettingRuleEdit,
  NoteDetail,
  NotesResponse,
  OntologyEditResult,
  OntologyRef,
  OntologySchema,
  PagesResponse,
  ProjectResponse,
  ReviewResponse,
  RulebookRecord,
  SettingsPatch,
  SourceDoc,
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
  ontologies: () => req<OntologyRef[]>(`/ontologies`),
  templates: () => req<TemplateRef[]>(`/templates`),
  /** Start a run. The 202 carries the rulebook the run was CREATED with, so the screen can name
   *  what governs the figures from the moment the run exists rather than waiting for a result — or
   *  deciding for itself which rulebook that must have been. */
  runExtraction: (
    documentId: string,
    body: { ontology_version_id?: string; template_version_id?: string } = {},
  ) =>
    req<{ run_id: string; status: string; rulebook?: RulebookRecord | null }>(
      `/documents/${documentId}/extractions`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
  /** Poll a background extraction run's status/result. */
  getRun: (runId: string) => req<ExtractionRunResponse>(`/extractions/${runId}`),
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
  documentAnalysis: (documentId: string, locale: Locale = "en") =>
    req<AnalysisResponse>(`/documents/${documentId}/analysis?locale=${locale}`),
  /** Real per-document notes index + detail, from line-item note references. */
  documentNotes: (documentId: string) =>
    req<NotesResponse>(`/documents/${documentId}/notes`),
  /** One note's detail. `locale` is passed because the response now carries the note's own
   *  column labels, and their Current/Prior fallback is localized server-side. */
  documentNote: (documentId: string, no: string, locale: Locale = "en") =>
    req<NoteDetail>(`/documents/${documentId}/notes/${no}?locale=${locale}`),
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
  /** The latest extraction run for a document (drives the Export preview/counts). */
  documentRun: (documentId: string) =>
    req<ExtractionRunResponse>(`/documents/${documentId}/run`),
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
  /** Data-driven commentary computed from a document's real extraction (not the demo). */
  documentCommentary: (documentId: string, locale: Locale = "en") =>
    req<Commentary>(`/documents/${documentId}/commentary?locale=${locale}`),
  /** One statement of a document's real extraction, grouped for the Workspace grid. */
  documentStatement: (documentId: string, statement: StatementKey, basis: Basis, locale: Locale = "en") =>
    req<StatementResponse>(
      `/documents/${documentId}/statement?statement=${statement}&basis=${basis}&locale=${locale}`,
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
  editLineItem: (id: string, value: number | null, formula: string) =>
    req<{ id: string; value: number | null; formula: string }>(
      `/projects/${PROJECT}/line-items/${id}`,
      { method: "PATCH", body: JSON.stringify({ value, formula }) },
    ),
  revertLineItem: (id: string) =>
    req<{ id: string }>(`/projects/${PROJECT}/line-items/${id}`, { method: "DELETE" }),
  notes: (locale: Locale = "en") => req<NotesResponse>(`/projects/${PROJECT}/notes?locale=${locale}`),
  note: (no: string, locale: Locale = "en") =>
    req<NoteDetail>(`/projects/${PROJECT}/notes/${no}?locale=${locale}`),
  review: (locale: Locale = "en") => req<ReviewResponse>(`/projects/${PROJECT}/review?locale=${locale}`),
  template: (locale: Locale = "en") => req<TemplateResponse>(`/projects/${PROJECT}/template?locale=${locale}`),
  exportOptions: () => req<{ options: ExportOption[] }>(`/projects/${PROJECT}/export-options`),
  exportUrl: () => `${BASE}/projects/${PROJECT}/export`,
  /** A real configured template rendered into the tree + per-node config the Template &
   *  Ontology screen shows (aliases/sign/netting from the paired ontology). */
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
  /** Upload an ontology (the extraction rulebook) FOR a template. `targetTemplateKey` re-points
   *  a rulebook written against another template; it still has to validate against the one named,
   *  so a key the template doesn't define comes back as a 422 listing it. */
  createOntology: (definition: unknown, targetTemplateKey?: string) =>
    req<{ id: string; ontology_key: string; target_template_key: string; version: number;
          mappings: number }>(
      `/ontologies`, {
        method: "POST",
        body: JSON.stringify({ definition, target_template_key: targetTemplateKey ?? null }),
      }),
  /** The shape an authored ontology must have, generated from the model the upload gate
   *  validates with: the JSON Schema plus a flat, per-field index to read it by. Admin-only,
   *  like every other ontology configuration call — gate the control on `config:ontology`. */
  ontologySchema: () => req<OntologySchema>(`/ontologies/schema`),
  /** A stored ontology's full definition — for "download, edit, upload back". */
  ontologyDetail: (id: string) =>
    req<{ id: string; ontology_key: string; target_template_key: string; version: number;
          definition: unknown }>(`/ontologies/${id}`),
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
  /** Edit ONE concept's rules inline — aliases, sign, and the mapping criteria the model
   *  reasons over. The server validates the result and publishes a NEW ontology version (so a
   *  past extraction still explains itself against the version it actually used); the
   *  response carries that new version's id/number. */
  editOntologyMapping: (ontologyId: string, edit: MappingEdit) =>
    req<OntologyEditResult & { canonical_key: string }>(
      `/ontologies/${ontologyId}/mappings`,
      { method: "PATCH", body: JSON.stringify(edit) }),
  /** Upsert or delete ONE netting rule. Netting restates a reported figure, so it goes
   *  through the same versioned publish + validation as a concept edit. */
  editNettingRule: (ontologyId: string, edit: NettingRuleEdit) =>
    req<OntologyEditResult>(
      `/ontologies/${ontologyId}/netting-rules`,
      { method: "PATCH", body: JSON.stringify(edit) }),
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
 *  Shared by the authoring downloads: both put the source version in the filename, so the file
 *  the user edits says which version it was generated from — a detail a client-side name loses. */
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

/** Download a ready-to-edit ontology for one template: every canonical_key already a stub inside
 *  the section it is printed under. It is a COMPLETE, valid rulebook — it can be uploaded back
 *  unmodified — so authoring means filling in aliases and criteria instead of discovering the
 *  expected shape from a sequence of 422s. Admin-only (`config:ontology`). */
export async function downloadOntologySkeleton(
  templateId: string, fallbackName: string,
): Promise<void> {
  await saveAsFile(
    await fetch(`${BASE}/ontologies/skeleton?template_id=${encodeURIComponent(templateId)}`,
                { headers: { ...authHeader() } }),
    `${fallbackName}_ontology_skeleton.json`,
  );
}

/** GET a REAL document's export (built from its latest extraction) and download it. Excel
 * uses the formatted, template-driven statement layout, localized to `locale`. CSV is the
 * two-column flat dump; `units` still applies to it (it is a conversion of the figures),
 * `include` does not (a two-column sheet has no analysis sheets to add). */
export async function downloadDocumentExport(
  documentId: string, format: ExportFmt, locale: Locale = "en", include?: string[],
  units?: string,
): Promise<void> {
  const inc = include && format === "excel" ? `&include=${include.join(",")}` : "";
  const un = units ? `&units=${encodeURIComponent(units)}` : "";
  const res = await fetch(
    `${BASE}/documents/${documentId}/export?fmt=${format}&layout=statement&locale=${locale}${inc}${un}`,
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
