/** Ephemeral UI state (Zustand) — mirrors the wireframe's state model. Durable data
 * lives in React Query; this holds transient selection / toggles / edit-mode, plus the
 * session token and the two locale concerns:
 *
 *  - `locale`        — the OUTPUT language for extracted financial data (statements,
 *                      line items, notes). Always applied. Starts at the deployment's
 *                      configured default (`features.default_output_locale`, hydrated from
 *                      GET /settings) and is overridden by the language switcher.
 *  - `uiLocalization`— admin flag (synced from GET /settings): when true the whole
 *                      interface is localized too; when false the UI stays English and
 *                      only financial output follows `locale`.
 *
 * `appLocale` (see useAppLocale) is the effective locale for interface chrome. */
import { create } from "zustand";

import {
  getStoredActiveDoc, getStoredLiListCollapsed, getStoredNavCollapsed, getStoredPinnedRun,
  getToken, setStoredActiveDoc, setStoredLiListCollapsed, setStoredNavCollapsed,
  setStoredPinnedRun, setStoredToken,
} from "./lib/api";
import type { Basis, ExportFmt, ExtractMode, Locale, StatementKey } from "./types";

interface UIState {
  locale: Locale; // output/data language
  /** True once the viewer picked an output language themselves (the language switcher).
   *
   * The server's configured default (`features.default_output_locale`) may only fill an
   * UNCHOSEN locale: the /settings snapshot arrives asynchronously, so without this flag a
   * deployment defaulting to "zh" would yank the screen back to Chinese moments after someone
   * switched to English. */
  localeChosen: boolean;
  uiLocalization: boolean; // admin flag: localize whole UI (from /settings)
  token: string | null; // session token

  extractMode: ExtractMode; // chosen at upload: auto-extract vs confirm page scope
  activeDocumentId: string | null; // the real uploaded document being worked (drives integrity/extract)
  dataset: Basis;
  statement: StatementKey;
  sel: string; // selected line-item id in the workspace
  editing: boolean; // inspector edit mode
  note: string; // selected note (All Notes), as the filing numbers it ("16(b)")
  openCheck: string; // expanded review check
  tplSel: string; // selected template node
  // The template VERSION chosen for the next run (null = whatever the server says is latest).
  // A key was stored here once, which could not name a version: the picker set the key it already
  // held, so choosing v2 changed nothing, and the run resolved the key to whichever row came back
  // first — the oldest. One field, holding the thing a run actually needs.
  selectedTemplateId: string | null;
  /** A HISTORICAL extraction run being read instead of the latest one (null = latest).
   *
   * App-level rather than per-screen because it decides which extraction the whole session is
   * looking at: the statement grid, the notes beside it and the file an export downloads all have
   * to answer for the same run, or two extractions get read as one spread. Cleared whenever the
   * active document changes — a run id belongs to one document, and carrying it across would
   * name a run the new document has never had. */
  pinnedRunId: string | null;
  exportFmt: ExportFmt;
  /** Navigation rail collapsed to icons. Persisted, and collapsed by default. */
  navCollapsed: boolean;
  /** Line Items' list pane, put away so the form can have the width. Read by one screen,
   *  like `navCollapsed` is read only by `NavRail`. */
  liListCollapsed: boolean;

  setLocale: (l: Locale) => void;
  /** Adopt the deployment's configured default output language (from GET /settings). A no-op
   *  once the viewer has chosen for themselves. */
  hydrateDefaultLocale: (l: Locale) => void;
  setUiLocalization: (v: boolean) => void;
  setToken: (t: string | null) => void;
  setExtractMode: (m: ExtractMode) => void;
  setActiveDocumentId: (id: string | null) => void;
  setDataset: (b: Basis) => void;
  setStatement: (s: StatementKey) => void;
  selRow: (id: string) => void;
  selForEdit: (id: string) => void;
  startEdit: () => void;
  cancelEdit: () => void;
  stopEditing: () => void;
  setNote: (n: string) => void;
  toggleCheck: (id: string) => void;
  setTpl: (id: string) => void;
  setSelectedTemplateId: (id: string | null) => void;
  setPinnedRunId: (id: string | null) => void;
  setFmt: (f: ExportFmt) => void;
  setNavCollapsed: (v: boolean) => void;
  setLiListCollapsed: (v: boolean) => void;
}

export const useUI = create<UIState>((set, get) => ({
  // "en" only until the /settings snapshot lands (`hydrateDefaultLocale`, wired in
  // lib/queries.ts:useSettings). The store is created synchronously at module load, before any
  // request, so there has to be SOME value here; it is the pre-hydration placeholder rather than
  // the app's default output language, which is `[features] default_output_locale`.
  locale: "en",
  localeChosen: false,
  uiLocalization: false,
  token: getToken(),
  extractMode: "auto",
  activeDocumentId: getStoredActiveDoc(),
  dataset: "consolidated",
  statement: "balance_sheet",
  sel: "trade_recv",
  editing: false,
  note: "12",
  openCheck: "bs",
  tplSel: "trade_recv",
  selectedTemplateId: null,
  // Restored only for the document that is actually active — see `getStoredPinnedRun`.
  pinnedRunId: getStoredPinnedRun(getStoredActiveDoc()),
  exportFmt: "excel",
  navCollapsed: getStoredNavCollapsed(),
  liListCollapsed: getStoredLiListCollapsed(),

  setLocale: (locale) => set({ locale, localeChosen: true }),
  hydrateDefaultLocale: (locale) => {
    if (get().localeChosen) return;
    set({ locale });
  },
  setUiLocalization: (uiLocalization) => set({ uiLocalization }),
  setToken: (token) => {
    setStoredToken(token);
    set({ token });
  },
  setExtractMode: (extractMode) => set({ extractMode }),
  setActiveDocumentId: (activeDocumentId) => {
    setStoredActiveDoc(activeDocumentId);
    // A pinned run belongs to the document it was launched against, so it cannot survive a
    // change of document: every reader would ask for a run this document never had and get a
    // 404 where a spread should be. Cleared in storage as well as in memory, or a refresh would
    // restore a pin this call just decided was invalid.
    setStoredPinnedRun(null, null);
    set({ activeDocumentId, pinnedRunId: null });
  },
  setDataset: (dataset) => set({ dataset }),
  setStatement: (statement) => set({ statement, sel: "" }),
  selRow: (sel) => set({ sel, editing: false }),
  selForEdit: (sel) => set({ sel, editing: true }),
  startEdit: () => set({ editing: true }),
  cancelEdit: () => set({ editing: false }),
  stopEditing: () => set({ editing: false }),
  setNote: (note) => set({ note }),
  toggleCheck: (id) => set((s) => ({ openCheck: s.openCheck === id ? "" : id })),
  setTpl: (tplSel) => set({ tplSel }),
  setSelectedTemplateId: (selectedTemplateId) => set({ selectedTemplateId }),
  setPinnedRunId: (pinnedRunId) => {
    setStoredPinnedRun(get().activeDocumentId, pinnedRunId);
    set({ pinnedRunId });
  },
  setFmt: (exportFmt) => set({ exportFmt }),
  setNavCollapsed: (navCollapsed) => {
    setStoredNavCollapsed(navCollapsed);
    set({ navCollapsed });
  },
  setLiListCollapsed: (liListCollapsed) => {
    setStoredLiListCollapsed(liListCollapsed);
    set({ liListCollapsed });
  },
}));

/** The run every document reader should ask for: a pinned historical run, or undefined for the
 * latest. Undefined rather than null because that is what the api functions take — a caller
 * spreads it straight into a query without restating the null check. */
export function usePinnedRun(): string | undefined {
  return useUI((s) => s.pinnedRunId) ?? undefined;
}

/** Effective locale for interface chrome: the chosen language only when an admin has
 * enabled whole-interface localization; otherwise English (financial output still
 * localizes via `locale`). */
export function useAppLocale(): Locale {
  return useUI((s) => (s.uiLocalization ? s.locale : "en"));
}
