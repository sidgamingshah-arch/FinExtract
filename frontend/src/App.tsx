import type { ReactNode } from "react";
import { useEffect } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { NavRail } from "./components/shell/NavRail";
import { TopBar } from "./components/shell/TopBar";
import Login from "./screens/Login";
import { useMe, useSettings } from "./lib/queries";
import { SCREENS } from "./screens/config";
import { useAppLocale, useUI } from "./store";
import { color } from "./theme";
import CommentaryScreen from "./screens/Commentary";
import ExportScreen from "./screens/Export";
import ExtractionView from "./screens/ExtractionView";
import IntegrityScreen from "./screens/Integrity";
import NotesScreen from "./screens/Notes";
import ReviewScreen from "./screens/Review";
import ScopeScreen from "./screens/Scope";
import SettingsScreen from "./screens/Settings";
import AuditScreen from "./screens/Audit";
import TemplateScreen from "./screens/Template";
import UploadScreen from "./screens/Upload";
import WorkspaceScreen from "./screens/Workspace";

/** The old per-document extraction URL, kept working — and kept MEANINGFUL.
 *
 * `/documents/:id` moved to `/extraction`, which reads the ACTIVE document from the store. A bare
 * redirect would therefore honour the path but discard the one thing it carried: a link to document
 * A, opened by someone whose active document is B, would silently show B's extraction under A's
 * URL. So the id in the path is adopted as the active document first. Links to this URL exist in the
 * wild — the Upload and Integrity screens both navigate to it, and it has been shared.
 */
function AdoptDocumentAndRedirect() {
  const { id } = useParams();
  const setActiveDocumentId = useUI((s) => s.setActiveDocumentId);
  const active = useUI((s) => s.activeDocumentId);
  useEffect(() => {
    if (id && id !== active) setActiveDocumentId(id);
  }, [id, active, setActiveDocumentId]);
  // Held until the store agrees, so the extraction screen never mounts against the outgoing
  // document and fires a run for it.
  if (id && id !== active) {
    return <div style={{ padding: 60, textAlign: "center", color: color.muted }}>Loading…</div>;
  }
  return <Navigate to="/extraction" replace />;
}

/** Route guard: renders the screen only if the caller's role may see it; otherwise
 * redirects to the first screen the role can access. */
function RequireScreen({ screen, children }: { screen: string; children: ReactNode }) {
  const { data: me, isPending } = useMe();
  if (isPending || !me) {
    return <div style={{ padding: 60, textAlign: "center", color: color.muted }}>Loading…</div>;
  }
  if (!me.screens.includes(screen)) {
    const first = me.screens[0] ?? "workspace";
    return <Navigate to={SCREENS[first]?.path ?? "/workspace"} replace />;
  }
  return <>{children}</>;
}

/** The authenticated application shell. */
function Shell() {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100%", overflow: "hidden" }}>
      <TopBar />
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <NavRail />
        <div style={{ flex: 1, minWidth: 0, overflowY: "auto", position: "relative" }}>
          <Routes>
            <Route path="/" element={<Navigate to="/workspace" replace />} />
            <Route path="/upload" element={<RequireScreen screen="upload"><UploadScreen /></RequireScreen>} />
            {/* Extraction is its OWN destination. It used to be mounted only at /documents/:id and
                gated on the `upload` screen, with no entry in SCREENS — so it appeared in no nav
                group, held no stepper slot, and `screenIdForPath` fell through to its "workspace"
                default, which left the rail and the stepper both highlighting Workspace while the
                reader was looking at the extraction. A screen the product cannot name is a screen
                the reader cannot find. */}
            <Route path="/extraction" element={<RequireScreen screen="extraction"><ExtractionView /></RequireScreen>} />
            {/* The old deep link still resolves, and still selects the document it names — see
                AdoptDocumentAndRedirect. */}
            <Route path="/documents/:id" element={<AdoptDocumentAndRedirect />} />
            <Route path="/integrity" element={<RequireScreen screen="integrity"><IntegrityScreen /></RequireScreen>} />
            <Route path="/scope" element={<RequireScreen screen="scope"><ScopeScreen /></RequireScreen>} />
            <Route path="/workspace" element={<RequireScreen screen="workspace"><WorkspaceScreen /></RequireScreen>} />
            <Route path="/notes" element={<RequireScreen screen="notes"><NotesScreen /></RequireScreen>} />
            <Route path="/review" element={<RequireScreen screen="review"><ReviewScreen /></RequireScreen>} />
            <Route path="/commentary" element={<RequireScreen screen="commentary"><CommentaryScreen /></RequireScreen>} />
            <Route path="/template" element={<RequireScreen screen="template"><TemplateScreen /></RequireScreen>} />
            <Route path="/settings" element={<RequireScreen screen="settings"><SettingsScreen /></RequireScreen>} />
            {/* Gated by the SCREEN list the server serves per role, the same as every other route
                here — the endpoint behind it is separately gated on `audit:view`, so a non-admin who
                reached the path would be refused by the API as well as by this guard. */}
            <Route path="/audit" element={<RequireScreen screen="audit"><AuditScreen /></RequireScreen>} />
            <Route path="/export" element={<RequireScreen screen="export"><ExportScreen /></RequireScreen>} />
            <Route path="*" element={<Navigate to="/workspace" replace />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const token = useUI((s) => s.token);
  const setUiLocalization = useUI((s) => s.setUiLocalization);
  const appLocale = useAppLocale();
  const { data: settings } = useSettings();

  // Keep the admin interface-localization flag in sync with the server.
  useEffect(() => {
    if (settings) setUiLocalization(settings.features.ui_localization);
  }, [settings, setUiLocalization]);

  // Reflect the effective interface locale on <html> (RTL only when the UI itself is
  // localized to Arabic; data-only Arabic keeps an LTR layout).
  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.dir = appLocale === "ar" ? "rtl" : "ltr";
    document.documentElement.lang = appLocale;
  }, [appLocale]);

  // No session → login. A 401 from any query clears the token (see main.tsx's
  // QueryCache handler), so an expired/rejected session lands here too. Transient
  // non-401 errors keep the shell rather than bouncing an authenticated user out.
  if (!token) return <Login />;
  return <Shell />;
}
