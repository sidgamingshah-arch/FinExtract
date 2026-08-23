/** React Query hooks — the data layer each screen consumes. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Basis, FxRateInput, Locale, OntologyRef, SettingsPatch, StatementKey, TemplateRef } from "../types";
import { useUI } from "../store";
import { api, downloadOntologySkeleton } from "./api";

// --- auth / identity ---
/** Current principal — enabled only once a session token exists; no retry so a 401
 * surfaces immediately as "logged out". */
export const useMe = () => {
  const token = useUI((s) => s.token);
  return useQuery({ queryKey: ["me", token], queryFn: api.me, enabled: !!token, retry: false });
};
export const useDemoUsers = () =>
  useQuery({ queryKey: ["demo-users"], queryFn: api.demoUsers });

export function useLogin() {
  const qc = useQueryClient();
  const setToken = useUI((s) => s.setToken);
  return useMutation({
    mutationFn: (vars: { username: string; password?: string }) =>
      api.login(vars.username, vars.password),
    onSuccess: (res) => {
      setToken(res.token);
      qc.invalidateQueries();
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  const setToken = useUI((s) => s.setToken);
  return useMutation({
    mutationFn: () => api.logout().catch(() => ({ ok: true })),
    onSuccess: () => {
      setToken(null);
      qc.clear();
    },
  });
}

// --- settings ---
export const useSettings = () => {
  const token = useUI((s) => s.token);
  return useQuery({ queryKey: ["settings"], queryFn: api.settings, enabled: !!token });
};

export function usePatchSettings() {
  const qc = useQueryClient();
  const setUiLocalization = useUI((s) => s.setUiLocalization);
  return useMutation({
    mutationFn: (body: SettingsPatch) => api.patchSettings(body),
    onSuccess: (res) => {
      setUiLocalization(res.features.ui_localization);
      qc.setQueryData(["settings"], res);
      // Role gating (/me) depends on review_required; refresh it.
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

// --- FX rate master ---
/** The whole FX master (admin editor). Readable by any authenticated user. */
export const useFxRates = () => {
  const token = useUI((s) => s.token);
  return useQuery({ queryKey: ["fx-rates"], queryFn: api.fxRates, enabled: !!token });
};

/** Resolve one currency pair against the master. Disabled while `base`/`quote` are absent
 *  or equal — an identical pair is not a conversion, so there is nothing to look up.
 *  A "no rate configured" answer is data, not an error: it arrives as `resolved: false`
 *  and the caller must then decline to convert. */
export const useFxRateResolution = (base: string | undefined, quote: string | undefined) => {
  const enabled = !!base && !!quote && base !== quote;
  return useQuery({
    queryKey: ["fx-resolve", base ?? null, quote ?? null],
    queryFn: () => api.resolveFxRate(base as string, quote as string),
    enabled,
    retry: false,
  });
};

/** Create/restate a rate (admin). Invalidates the master AND every resolution, since a new
 *  rate can change — or newly enable — any pair currently on screen. */
export function useUpsertFxRate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FxRateInput) => api.upsertFxRate(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fx-rates"] });
      qc.invalidateQueries({ queryKey: ["fx-resolve"] });
    },
  });
}

export function useUpdateFxRate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; body: FxRateInput }) => api.updateFxRate(vars.id, vars.body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fx-rates"] });
      qc.invalidateQueries({ queryKey: ["fx-resolve"] });
    },
  });
}

export function useDeleteFxRate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteFxRate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["fx-rates"] });
      qc.invalidateQueries({ queryKey: ["fx-resolve"] });
    },
  });
}

/** Analyst hands the final output to the reviewer; refreshes the audit log. */
export function useSubmitForReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId?: string) => api.submitForReview(documentId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["audit"] }),
  });
}

// --- project data ---
export const useCommentary = (locale: Locale = "en") =>
  useQuery({ queryKey: ["commentary", locale], queryFn: () => api.commentary(locale) });
/** The audit trail for whatever is being worked on: an uploaded document's runs, or the sample's.
 *
 * `documentId` decides WHICH trail, and it has to, because the trail is keyed by what was run
 * against. Without it this hook always asked the demo project's route — so on a real filing the
 * Analysis screen showed the sample's rows, or nothing, while every extraction and credit-narrative
 * run against that filing sat unread under the document's own key. */
export const useAudit = (documentId?: string) =>
  useQuery({
    queryKey: ["audit", documentId ?? null],
    queryFn: () => (documentId ? api.documentAudit(documentId) : api.audit()),
  });

/** Trigger a live LLM analysis run; refreshes the audit log on completion. */
export function useRunAnalysis() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.runAnalysis(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["audit"] }),
  });
}
/** The SEEDED SAMPLE project — its title, its filename/pages/standard, and its own progress
 *  counts. It is never a real uploaded document's data, so a caller that has a real document
 *  active has no use for it.
 *
 *  `enabled` exists for exactly that case: the shell (nav rail, top bar) renders on EVERY screen,
 *  and it was issuing this request on every one of them even while a real document was being
 *  worked — then labelling the sample's answers as the active extraction's. Gated off, the shell
 *  makes no request it has nothing to say with, and reads nothing it must not print. */
export const useProject = (enabled = true) =>
  useQuery({ queryKey: ["project"], queryFn: api.project, enabled });
/** Whether a project's data is loaded (false in greenfield until a sample/real run exists). */
export const useProjectLoaded = () => useProject().data?.loaded ?? false;
export const useDocuments = () => useQuery({ queryKey: ["documents"], queryFn: api.documents });

export const useOntologies = () => useQuery({ queryKey: ["ontologies"], queryFn: api.ontologies });
export const useTemplates = () => useQuery({ queryKey: ["templates"], queryFn: api.templates });

/** The template VERSION in play: the one selected, else the LATEST the server names.
 *
 * ONE spelling, because three surfaces ask it — the Upload screen's template card, the rulebook card
 * beside it, and the extraction view when it resolves the version a run is launched against. They
 * each had their own copy, all of the same broken shape: `find(x => x.template_key === selectedKey)`
 * over a list holding EVERY version of that key, which answers the first row. Unordered, that was
 * v1 — so the screens named the oldest version as active and a run extracted against it, however
 * many revisions had been published.
 *
 * `is_latest` is the server's answer and is read, not re-derived (see `TemplateRef`). `list[0]` is
 * the last resort for a payload predating the flag, and the list is served newest-first so even that
 * degrades to something defensible.
 *
 * `key` narrows to one template when the caller knows which — the extraction view does, from the
 * rulebook's target — so a selection belonging to a DIFFERENT template cannot be returned as though
 * it were this one's. */
export function activeTemplate(
  list: TemplateRef[] | undefined,
  selectedId: string | null | undefined,
  key?: string,
): TemplateRef | undefined {
  const rows = (list ?? []).filter((t) => !key || t.template_key === key);
  return rows.find((t) => t.id === selectedId)
    ?? rows.find((t) => t.is_latest)
    ?? rows[0];
}

/** The rulebook IN FORCE among the rows matching `pred` — READ from the server, never ranked here.
 *
 * It lives in the data layer because two screens ask the question (the index, to name the rulebook
 * a template row is described by; the extraction view, to decide which one a run defaults to) and
 * they were answering it differently: each had its own `version >` comparison, which is a third and
 * fourth spelling of a rule the server already owns.
 *
 * THE DEFECT THIS CLOSES. Collapsing those two into one was right; the one was still wrong. It
 * ranked `[declares a supersession, version, ontology_key]` under a comment claiming it mirrored the
 * server's picker — a function that did not exist under that name, and whose actual rule is
 * "whatever was stored last wins" (`app/services/ontology_select.select_for_template`). That rule
 * turns on `created_at`, which this payload does not carry, so the client could not have applied it
 * even in principle. The two sides therefore named DIFFERENT rulebooks: the run mapped against the
 * one in force and the screen captioned it as pinned to an older one, telling an analyst their
 * extraction had used something it had not.
 *
 * So the server states it: `OntologyRef.in_force`, computed by asking `select_for_template` itself.
 * One implementation of the rule means there is nothing left to drift. Do not add a fallback
 * ranking here — a guess that disagrees with the extractor is exactly the defect above, and a row
 * whose payload predates the flag is better reported as "unknown" than named wrongly.
 *
 * `pred` narrows the candidates, normally to one target template, and the flag picks among them.
 * Where a caller cannot name a template — the extraction view and the Workspace both fall back to
 * an unfiltered call when no template is selected yet — several rows carry the flag, one per
 * template, and this returns the first in list order (the server orders by `ontology_key`, so it is
 * at least deterministic). That fallback names a rulebook for SOME template rather than the one
 * being read, which is the pre-existing weakness of asking without a template; it is not made worse
 * by reading the flag, and both callers use it only to have something to show before a template
 * is chosen.
 */
export function ontologyInForce(
  rows: OntologyRef[] | undefined,
  pred: (o: OntologyRef) => boolean = () => true,
): OntologyRef | undefined {
  return (rows ?? []).filter(pred).find((o) => o.in_force);
}

/** Run (and fetch) the extraction for one uploaded document — its real line items with
 * provenance, mapped against the given ontology/template. Extraction is a background job:
 * this POSTs once per (doc, ontology/template) to start it, then polls the run until it
 * reaches succeeded/failed. `enabled` gates the start until the ontology list has settled. */
export function useExtraction(
  documentId: string | undefined,
  ontologyId?: string,
  templateId?: string,
  enabled = true,
  /** A run this caller did NOT start, polled as if it had: the run `useDocumentRunStatus` found
   *  already in flight for the document. Without it, a screen that arrives mid-run has to choose
   *  between starting a second pipeline over the first and showing nothing at all.
   *
   *  A run we started still wins when both are present — a caller holding a run of its own is
   *  watching that one — but the two are not meant to coexist: the caller passing this is expected
   *  to keep `enabled` false for as long as it holds an adopted run. */
  adoptRunId?: string,
) {
  const start = useQuery({
    queryKey: ["extraction-start", documentId, ontologyId ?? null, templateId ?? null],
    queryFn: () => api.runExtraction(documentId as string, {
      ontology_version_id: ontologyId, template_version_id: templateId,
    }),
    enabled: !!documentId && enabled,
    staleTime: Infinity,
    retry: false,
  });
  const runId = start.data?.run_id ?? adoptRunId;
  const poll = useQuery({
    // Keyed on the run, not on how the caller came by it, so an adopted run and a started one share
    // one cache entry — two polls of the same run would double the request rate on a live pipeline.
    queryKey: ["extraction-run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: !!runId,
    // No `retry: false` here, though the start above keeps it: a failed POST must never be repeated
    // (that is a second pipeline), while a failed poll is one request that told us nothing about a
    // run still going. `poll.isError` reaches the caller as a FAILED RUN — with a re-run button under
    // it — so a single dropped request mid-extraction announced a failure the pipeline never had, and
    // went on announcing it, because the interval below stops when there is no data to poll on.
    // Keep polling while the background job is running; stop once it settles.
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1000 : false),
  });

  const run = poll.data;
  const succeeded = run?.status === "succeeded" && !!run?.result;
  const failed = start.isError || poll.isError || run?.status === "failed";
  return {
    data: succeeded ? run : undefined,
    isPending: (start.isPending && enabled) || (!!runId && !succeeded && !failed),
    isError: failed,
    error: (start.error as Error) ?? (poll.error as Error) ?? undefined,
    // WHICH rulebook this run read the filing against, as the run itself recorded it. Returned
    // separately from `data` because it is true of the run from the moment it exists — it must be
    // nameable while the run is still going and when it fails, not only once rows come back. The
    // finished result's copy is preferred because only it carries `applied`. It is never filled in
    // from a client-side guess: a screen that decides for itself which rulebook was in force is
    // how a superseded run came to be labelled as the current one.
    rulebook: run?.result?.rulebook ?? run?.rulebook ?? start.data?.rulebook ?? undefined,
    // HOW FAR IT HAS GOT, which the caller could not previously reach. `data` collapses to
    // undefined until the run succeeds — deliberately, so no screen renders half a spread — and
    // that also threw away the `progress` the poll had been fetching every second all along. So
    // progress is returned beside `data`, true of the run from the moment it exists, exactly like
    // `rulebook` above and for the same reason: a screen must be able to say what is happening
    // while it is happening. `runId` comes with it because a re-extract has to know which run the
    // screen is watching.
    runId,
    status: run?.status ?? (start.data?.status || (start.isPending ? "queued" : "")),
    progress: run?.progress ?? undefined,
    stages: run?.stages ?? undefined,
    logTail: run?.log_tail ?? undefined,
  };
}

/** Start a FRESH extraction for a document that already has one.
 *
 *  Not a variant of `useExtraction`: that hook's start query is keyed on
 *  (document, ontology, template) with `staleTime: Infinity`, so asking it again hands back the
 *  cached run rather than launching a new one — which is correct for a screen mount and useless for
 *  "re-extract this against the revised template". A run is PINNED to the template version it was
 *  launched against, so re-running is the only way a template revision reaches a document that was
 *  extracted before it.
 *
 *  THE NEW RUN IS WRITTEN INTO THE START QUERY'S CACHE, never removed from it. `removeQueries` on
 *  that key looks like the obvious way to make the screen pick up the new run, and it starts a
 *  SECOND run: the start query is MOUNTED while the button is on screen, so dropping its data
 *  leaves an observer with nothing and React Query immediately refetches — and that query's
 *  `queryFn` is the POST that launches an extraction. One click, two concurrent pipelines racing to
 *  write the same run rows. Seeding the cache with the response this mutation already has hands the
 *  screen the new run id with no refetch at all.
 *
 *  Every derived read of the document IS invalidated, because all of them are now about a
 *  superseded run. The run poll is left alone: the start query's new data changes the run id, so the
 *  poll re-keys onto the new run by itself. */
export function useReextract(documentId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { ontologyId?: string; templateId?: string } = {}) =>
      api.runExtraction(documentId as string, {
        ontology_version_id: vars.ontologyId, template_version_id: vars.templateId,
      }),
    onSuccess: (started, vars) => {
      // The same key `useExtraction` builds, so the mounted start query adopts this run rather
      // than launching another one of its own.
      qc.setQueryData(
        ["extraction-start", documentId, vars?.ontologyId ?? null, vars?.templateId ?? null],
        started,
      );
      // `document-run-status` included because it is what every screen now asks "is a run in
      // flight?" — and it stops polling on a terminal answer, so a cached `succeeded`/`failed` from
      // the run this one replaces would describe the document as idle for as long as the entry
      // lived, while the new run was working.
      for (const key of ["document-statement", "document-run", "document-run-status",
                         "document-review", "document-analysis", "document-commentary",
                         "document-notes"]) {
        qc.invalidateQueries({ queryKey: [key, documentId] });
      }
    },
  });
}

/** Cells around a value's spreadsheet origin — the Excel click-to-source backdrop.
 * Enabled only once a spreadsheet cell has been picked. */
export const useCellContext = (documentId: string, sheet?: string, cell?: string) =>
  useQuery({
    queryKey: ["cell-context", documentId, sheet ?? null, cell ?? null],
    queryFn: () => api.cellContext(documentId, sheet as string, cell as string),
    enabled: !!documentId && !!sheet && !!cell,
    staleTime: Infinity,
    retry: false,
  });

/** Upload a source document; refreshes the documents list and project, and marks the
 * uploaded document active so the Integrity/Extract steps operate on the real file. */
export function useUploadDocument() {
  const qc = useQueryClient();
  const setActiveDocumentId = useUI((s) => s.setActiveDocumentId);
  return useMutation({
    mutationFn: (file: File) => api.uploadDocument(file),
    onSuccess: (res) => {
      if (res?.id) setActiveDocumentId(res.id);
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["project"] });
    },
  });
}

/** Generate an LLM credit narrative for a document (on-demand; the deterministic credit
 * view is always available regardless). */
export function useCreditNarrative() {
  return useMutation({
    mutationFn: ({ id, locale }: { id: string; locale: Locale }) => api.creditNarrative(id, locale),
  });
}

/** Delete an uploaded document (owner/admin). Refreshes the list and clears the active
 * document when it's the one removed, so the pipeline steps fall back cleanly. */
export function useDeleteDocument() {
  const qc = useQueryClient();
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const setActiveDocumentId = useUI((s) => s.setActiveDocumentId);
  return useMutation({
    mutationFn: (id: string) => api.deleteDocument(id),
    onSuccess: (_res, id) => {
      if (activeDocumentId === id) setActiveDocumentId(null);
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["project"] });
    },
  });
}

/** Real per-document pre-flight integrity — the uploaded file's own results. */
export const useDocumentIntegrity = (documentId: string | undefined, locale: Locale = "en") =>
  useQuery({
    queryKey: ["document-integrity", documentId, locale],
    queryFn: () => api.documentIntegrity(documentId as string, locale),
    enabled: !!documentId,
    retry: false,
  });

/** Real per-document review queue (unmapped + low-confidence items from the latest run). */
export const useDocumentReview = (documentId: string | undefined, locale: Locale = "en") =>
  useQuery({
    queryKey: ["document-review", documentId, locale],
    queryFn: () => api.documentReview(documentId as string, locale),
    enabled: !!documentId,
    retry: false,
  });

/** Everything a judgement changes — and nothing else. Accepting a finding moves no figure, so
 *  the statement and the run are deliberately NOT invalidated; the review payload is refreshed
 *  by its whole prefix so every cached locale re-derives its counts, and the commentary with it
 *  because its data-quality prose consumes summary.open. */
function invalidateAfterJudgement(
  qc: ReturnType<typeof useQueryClient>, documentId: string | undefined,
) {
  qc.invalidateQueries({ queryKey: ["document-review", documentId] });
  qc.invalidateQueries({ queryKey: ["document-commentary", documentId] });
}

/** Record a judgement on one finding: a named person examined these figures and they stand.
 *  Errors are NOT swallowed — a 409 means the figures moved while the card was open, and the
 *  screen has to say so rather than leave the reviewer believing their acceptance landed. */
export function useAcceptFinding(documentId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { subjectKey: string; evidenceDigest: string; reason: string;
                         locale?: Locale }) =>
      api.acceptFinding(documentId as string, vars.subjectKey, vars.evidenceDigest, vars.reason,
                        vars.locale ?? "en"),
    onSuccess: () => invalidateAfterJudgement(qc, documentId),
  });
}

/** Withdraw an acceptance, putting the finding back in the open queue. The stored row keeps the
 *  history; only the in-force verdict changes. */
export function useWithdrawAcceptance(documentId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (subjectKey: string) => api.withdrawAcceptance(documentId as string, subjectKey),
    onSuccess: () => invalidateAfterJudgement(qc, documentId),
  });
}

/** Re-map one printed row onto a different template line — how a row-shaped finding is resolved.
 *
 *  Invalidated like an EDIT, not like a judgement: moving a row moves the figure into a different
 *  concept, so the statement grid, the KPIs, the commentary and the export all change with it.
 *  Refreshing only the queue would leave every other screen showing the pre-remap mapping.
 *
 *  Errors are NOT swallowed — a 409 means the row reference is ambiguous or already carries the
 *  concept, a 422 that the target is not one this run's template offers, and the card has to say
 *  so rather than leave the analyst believing the row moved. */
export function useRemapReviewRow(documentId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { rowRef: string; canonicalKey: string; reason?: string;
                         locale?: Locale }) =>
      api.remapReviewRow(documentId as string, vars.rowRef, vars.canonicalKey,
                         vars.reason ?? "", vars.locale ?? "en"),
    onSuccess: () => invalidateAfterEdit(qc, documentId),
  });
}

/** Derived analysis (ratios / disclosures / notes) for a document. */
export const useDocumentAnalysis = (documentId: string | undefined, locale: Locale = "en") =>
  useQuery({
    queryKey: ["document-analysis", documentId, locale],
    queryFn: () => api.documentAnalysis(documentId as string, locale),
    enabled: !!documentId,
    retry: false,
  });

/** Real per-document notes index + one note's detail. */
export const useDocumentNotes = (documentId: string | undefined) =>
  useQuery({
    queryKey: ["document-notes", documentId],
    queryFn: () => api.documentNotes(documentId as string),
    enabled: !!documentId,
    retry: false,
  });
/** One note's detail. `locale` is in the key as well as the request: the response carries the
 *  note's column labels, whose Current/Prior fallback is localized, so a cached English detail
 *  must not be reused for a zh reader. */
export const useDocumentNote = (documentId: string | undefined, no: number, locale: Locale = "en") =>
  useQuery({
    queryKey: ["document-note", documentId, no, locale],
    queryFn: () => api.documentNote(documentId as string, no, locale),
    enabled: !!documentId,
    retry: false,
  });

/** Everything a manual value changes. An edit moves the statement figure, and with it the
 *  accounting checks, the KPIs, the commentary and the export — refreshing only the grid left
 *  the rest of the app showing the pre-edit number. */
function invalidateAfterEdit(qc: ReturnType<typeof useQueryClient>, documentId: string | undefined) {
  for (const key of ["document-statement", "document-run", "document-review",
                     "document-analysis", "document-commentary"]) {
    qc.invalidateQueries({ queryKey: [key, documentId] });
  }
}

/** Edit ONE figure on a real extraction — a concept, in one basis, for one period.
 *  Errors are NOT swallowed: the caller shows the server's reason and keeps the editor open,
 *  because a rejected edit that closes the editor looks exactly like a saved one. */
export function useEditDocumentLineItem(documentId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      key: string; value: number | null; formula: string;
      // A slot name ("current"/"prior") or the period's printed label — the Review screen's
      // flip-sign fix names the period the way the relation that failed named it.
      basis: Basis; period: string; comment?: string;
    }) =>
      api.editDocumentLineItem(documentId as string, vars.key, vars.value, vars.formula,
                               vars.basis, vars.period, vars.comment ?? ""),
    onSuccess: () => invalidateAfterEdit(qc, documentId),
  });
}

/** Revert an edited real line item to its original machine-extracted values. */
export function useRevertDocumentLineItem(documentId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.revertDocumentLineItem(documentId as string, key),
    onSuccess: () => invalidateAfterEdit(qc, documentId),
  });
}

/** Publish an edited template workbook as a new template version; refreshes the template lists
 *  so the new version is immediately selectable on Upload. */
export function useUploadTemplateXlsx() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { file: File; templateKey: string; name: string }) =>
      api.uploadTemplateXlsx(vars.file, vars.templateKey, vars.name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["templates"] });
      qc.invalidateQueries({ queryKey: ["template-detail"] });
      qc.invalidateQueries({ queryKey: ["project"] });
    },
  });
}

/** Upload an ontology (the extraction rulebook) against a template. */
export function useUploadOntology() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { definition: unknown; targetTemplateKey?: string }) =>
      api.createOntology(vars.definition, vars.targetTemplateKey),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ontologies"] });
      qc.invalidateQueries({ queryKey: ["template-detail"] });
    },
  });
}

/** The shape an authored ontology must have (JSON Schema + the per-field index).
 *
 * `enabled` exists because the endpoint is admin-only: called without `config:ontology` it 403s,
 * so the screen must gate it on the permission rather than let every viewer fire a doomed request.
 * Generated from the models, so it cannot change between requests — fetched once and kept. */
export const useOntologySchema = (enabled = true) =>
  useQuery({
    queryKey: ["ontology-schema"],
    queryFn: api.ontologySchema,
    enabled,
    staleTime: Infinity,
    retry: false,
  });

/** Download a ready-to-edit ontology skeleton for a template.
 *
 * A mutation, not a query: it writes a file to the user's disk, so it must run when the button is
 * pressed and never be replayed from cache — and its failure has to reach the screen, because a
 * download that silently did nothing is indistinguishable from one the browser blocked. */
export function useDownloadOntologySkeleton() {
  return useMutation({
    mutationFn: (vars: { templateId: string; fallbackName: string }) =>
      downloadOntologySkeleton(vars.templateId, vars.fallbackName),
  });
}

/** What the template workbook's columns mean — read from the reader that enforces them, so the
 *  screen can never describe a contract the API doesn't hold to. */
export const useTemplateXlsxColumns = () =>
  useQuery({ queryKey: ["template-xlsx-columns"], queryFn: api.templateXlsxColumns });

/** Latest extraction run for a document (Export preview/counts). */
export const useDocumentRun = (documentId: string | undefined) =>
  useQuery({
    queryKey: ["document-run", documentId],
    queryFn: () => api.documentRun(documentId as string),
    enabled: !!documentId,
    retry: false,
  });

/** Whether a document has an extraction IN FLIGHT — polled while it has one, and left alone the
 *  moment it settles (the `useExtraction` poll's own shape: `refetchInterval` returns false).
 *
 *  This is the question a page loaded FRESH could not ask. `useExtraction`'s poll covers a run
 *  within one session because that session started it and holds its id; a hard reload has an empty
 *  cache and no run id, and `useDocumentRun` 404s until a run has a RESULT — so "being extracted
 *  right now" and "never extracted" arrived as the same answer, and a screen had to print the
 *  second. `status: "none"` is data here, not an error.
 *
 *  READ-ONLY, and that is load-bearing: the Workspace mounts this, and opening the Workspace must
 *  never POST a run just by being looked at. Nothing in this hook can start one.
 *
 *  NO `retry: false`, unlike the reads beside it. They decline retries because a 404 is one of their
 *  ordinary ANSWERS and repeating it is pure waste; here every answer is a 200, so an error is a
 *  genuine failure — and a transient one must not strand the caller, which it would: the interval
 *  below stops for anything that is not `running`, and with no data there is nothing for it to poll
 *  on, so one dropped request would leave a screen unable to say whether a run exists at all. */
export const useDocumentRunStatus = (documentId: string | undefined) =>
  useQuery({
    queryKey: ["document-run-status", documentId],
    queryFn: () => api.documentRunStatus(documentId as string),
    enabled: !!documentId,
    // NEVER SERVED FROM CACHE WITHOUT ASKING AGAIN. The client's default is `staleTime: 30_000`
    // (main.tsx), and this answer goes out of date the moment a run starts: an analyst who starts a
    // run and walks to the Workspace inside that window would be read the cached "none" — told the
    // document has not been extracted while their own run works on it, which is the exact sentence
    // this hook exists to stop. And because the interval below stops for every answer but `running`,
    // nothing would ever correct it. `staleTime: 0` makes every mount ask.
    staleTime: 0,
    // Poll while the run is working; stop the moment it settles. A terminal answer does not change
    // by itself, and a document nobody is extracting must not be polled once a second for ever.
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1000 : false),
  });
export const useIntegrity = (locale: Locale = "en", enabled = true) =>
  useQuery({ queryKey: ["integrity", locale], queryFn: () => api.integrity(locale), enabled });
export const usePages = (locale: Locale = "en", enabled = true) =>
  useQuery({ queryKey: ["pages", locale], queryFn: () => api.pages(locale), enabled });
export const useReview = (locale: Locale = "en", enabled = true) =>
  useQuery({ queryKey: ["review", locale], queryFn: () => api.review(locale), enabled });
export const useNotes = (locale: Locale = "en", enabled = true) =>
  useQuery({ queryKey: ["notes", locale], queryFn: () => api.notes(locale), enabled });
export const useNote = (no: number, locale: Locale = "en", enabled = true) =>
  useQuery({ queryKey: ["note", no, locale], queryFn: () => api.note(no, locale), enabled });
export const useTemplate = (locale: Locale = "en") =>
  useQuery({ queryKey: ["template", locale], queryFn: () => api.template(locale) });
/** A real configured template's structure (Template & Ontology screen, admin).
 *
 * Keeps the previous template's detail on screen while a newly selected one loads. Without it the
 * screen blanks to "Loading…" on every selection change — which also unmounted the authoring
 * panel the moment it published a template and selected the new version, taking its confirmation
 * (or its error) with it. */
export const useTemplateDetail = (id: string | undefined, locale: Locale = "en") =>
  useQuery({
    queryKey: ["template-detail", id, locale],
    queryFn: () => api.templateDetail(id as string, locale),
    enabled: !!id,
    placeholderData: keepPreviousData,
  });
export const useExportOptions = () =>
  useQuery({ queryKey: ["export-options"], queryFn: api.exportOptions });
export const useLanguages = () => useQuery({ queryKey: ["languages"], queryFn: api.languages });

export const useStatement = (statement: StatementKey, basis: Basis, locale: Locale = "en", enabled = true) =>
  useQuery({
    queryKey: ["statement", statement, basis, locale],
    queryFn: () => api.statement(statement, basis, locale),
    enabled,
  });

/** Real per-page classification for a document (Page Scope). */
export const useDocumentPages = (documentId: string | undefined) =>
  useQuery({
    queryKey: ["document-pages", documentId],
    queryFn: () => api.documentPages(documentId as string),
    enabled: !!documentId,
    retry: false,
  });

/** Text search over a document's source pages. Keyed on the SUBMITTED term (not what is being
 *  typed), so a search is one request per query rather than one per keystroke. */
export const useDocumentSearch = (documentId: string | undefined, q: string) =>
  useQuery({
    queryKey: ["document-search", documentId, q],
    queryFn: () => api.searchDocument(documentId as string, q),
    enabled: !!documentId && q.trim().length >= 2,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

/** Persist the user's page selection for extraction; refreshes the pages view. */
export function useSetDocumentScope(documentId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (includedPages: number[]) =>
      api.setDocumentScope(documentId as string, includedPages),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["document-pages", documentId] }),
  });
}

/** Data-driven commentary from a document's real extraction (Analysis screen, real mode). */
export const useDocumentCommentary = (documentId: string | undefined, locale: Locale = "en") =>
  useQuery({
    queryKey: ["document-commentary", documentId, locale],
    queryFn: () => api.documentCommentary(documentId as string, locale),
    enabled: !!documentId,
    retry: false,
  });

/** One statement of a document's real extraction (Workspace grid), labels in `locale`. */
export const useDocumentStatement = (
  documentId: string | undefined, statement: StatementKey, basis: Basis, locale: Locale = "en") =>
  useQuery({
    queryKey: ["document-statement", documentId, statement, basis, locale],
    queryFn: () => api.documentStatement(documentId as string, statement, basis, locale),
    enabled: !!documentId,
    retry: false,
  });

export function useEditLineItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; value: number | null; formula: string }) =>
      api.editLineItem(vars.id, vars.value, vars.formula),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["statement"] }),
  });
}
