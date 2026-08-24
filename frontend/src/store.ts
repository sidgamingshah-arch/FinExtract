/** Ephemeral UI state (Zustand) — mirrors the wireframe's state model. Durable data
 * lives in React Query; this holds transient selection / toggles / edit-mode, plus the
 * session token and the two locale concerns:
 *
 *  - `locale`        — the OUTPUT language for extracted financial data (statements,
 *                      line items, notes). Always applied.
 *  - `uiLocalization`— admin flag (synced from GET /settings): when true the whole
 *                      interface is localized too; when false the UI stays English and
 *                      only financial output follows `locale`.
 *
 * `appLocale` (see useAppLocale) is the effective locale for interface chrome. */
import { create } from "zustand";

import {
  getStoredActiveDoc, getStoredNavCollapsed, getToken, setStoredActiveDoc, setStoredNavCollapsed,
  setStoredToken,
} from "./lib/api";
import type { Basis, ExportFmt, ExtractMode, Locale, StatementKey } from "./types";

interface UIState {
  locale: Locale; // output/data language
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
  exportFmt: ExportFmt;
  /** Navigation rail collapsed to icons. Persisted, and collapsed by default. */
  navCollapsed: boolean;

  setLocale: (l: Locale) => void;
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
  setFmt: (f: ExportFmt) => void;
  setNavCollapsed: (v: boolean) => void;
}

export const useUI = create<UIState>((set) => ({
  locale: "en",
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
  exportFmt: "excel",
  navCollapsed: getStoredNavCollapsed(),

  setLocale: (locale) => set({ locale }),
  setUiLocalization: (uiLocalization) => set({ uiLocalization }),
  setToken: (token) => {
    setStoredToken(token);
    set({ token });
  },
  setExtractMode: (extractMode) => set({ extractMode }),
  setActiveDocumentId: (activeDocumentId) => {
    setStoredActiveDoc(activeDocumentId);
    set({ activeDocumentId });
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
  setFmt: (exportFmt) => set({ exportFmt }),
  setNavCollapsed: (navCollapsed) => {
    setStoredNavCollapsed(navCollapsed);
    set({ navCollapsed });
  },
}));

/** Effective locale for interface chrome: the chosen language only when an admin has
 * enabled whole-interface localization; otherwise English (financial output still
 * localizes via `locale`). */
export function useAppLocale(): Locale {
  return useUI((s) => (s.uiLocalization ? s.locale : "en"));
}
