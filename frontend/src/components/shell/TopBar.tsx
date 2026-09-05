/** Fixed top bar: logo + wordmark + active document title + pipeline stepper + avatar.
 * The stepper marks steps before the current pipeline position as done (green check). */
import { useLocation, useNavigate } from "react-router-dom";

import { useDocumentRuns, useDocuments, useMe, useProject } from "../../lib/queries";
import { useUI } from "../../store";
import { color, radius } from "../../theme";
import { useT } from "../../i18n";
import { STEPPER, stepIdForPath } from "../../screens/config";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { UserMenu } from "./UserMenu";

/** Says, everywhere, that the session is reading a historical run.
 *
 * The pin is app-level, so it changes what the notes pane shows and what an export downloads on
 * screens that have no picker of their own. A mode that quietly rewrites what every screen means
 * has to be visible from every screen, and clearable from there too — otherwise a reader who
 * pinned a run on the workspace an hour ago has no way to know why an export looks stale.
 */
function PinnedRunChip() {
  const t = useT();
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const pinnedRunId = useUI((s) => s.pinnedRunId);
  const setPinnedRunId = useUI((s) => s.setPinnedRunId);
  // Only to name the run by its number; the pin itself is authoritative without it, so a run
  // list that has not arrived yet shows the chip with a placeholder rather than hiding it.
  const runsQ = useDocumentRuns(pinnedRunId ? (activeDocumentId ?? undefined) : undefined);
  if (!pinnedRunId) return null;
  const runNumber = runsQ.data?.runs?.find((r) => r.run_id === pinnedRunId)?.run_number;
  return (
    <div
      data-testid="topbar-pinned-run"
      data-run-id={pinnedRunId}
      style={{ display: "flex", alignItems: "center", gap: 7, padding: "3px 8px",
               borderRadius: radius.control, background: color.amberBg, color: color.amberFg,
               fontSize: 11, fontWeight: 600, whiteSpace: "nowrap" }}
    >
      <span>{t("ws.run.historical").replace("{n}", String(runNumber ?? "?"))}</span>
      <button
        type="button"
        onClick={() => setPinnedRunId(null)}
        title={t("ws.run.clearPin")}
        style={{ border: "none", background: "transparent", color: "inherit", cursor: "pointer",
                 font: "inherit", textDecoration: "underline", padding: 0 }}
      >
        {t("ws.run.clearPin")}
      </button>
    </div>
  );
}

export function TopBar() {
  const nav = useNavigate();
  const loc = useLocation();
  const { data: me } = useMe();
  const t = useT();
  const extractMode = useUI((s) => s.extractMode);
  // When a real uploaded document is being worked, the title bar reflects THAT file — not
  // the demo project — so demo chrome never bleeds into a real run.
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const usingReal = !!activeDocumentId;
  // Not requested while a real document is active: the sample project is not what this bar is
  // naming then, so there is nothing in its answer to print. (Same wrong-source rule as the nav
  // rail's progress card.)
  const { data } = useProject(!usingReal);
  const { data: docsData } = useDocuments();
  const activeDoc = docsData?.documents?.find((d) => d.id === activeDocumentId);
  // Which document is active is settled by `activeDocumentId`, NOT by whether its row has arrived
  // from /documents yet. Branching on `activeDoc` meant that for as long as that request was in
  // flight — every cold load and every invalidation — the bar printed the SAMPLE project's title
  // and its "…· 33 pages · IFRS" subtitle as the identity of the real file being worked. A pending
  // request is not "no document": it is this document, not yet named.
  const title = usingReal
    ? activeDoc?.name ?? t("empty.loading")
    : data?.project.title ?? t("empty.loading");
  const subtitle = usingReal
    ? activeDoc?.meta ?? ""
    : data
    ? `${data.project.filename} · ${data.project.pages} ${t("tb.pages")} · ${data.project.standard}`
    : "";
  // In auto mode the pipeline skips the manual Page Scope confirmation, so the
  // stepper collapses to Upload → Integrity → Extract → Review → Export. Also limit the
  // stepper to steps the caller's role can actually reach (e.g. a reviewer has no upload).
  const steps = STEPPER
    .filter((s) => !(extractMode === "auto" && s.id === "scope"))
    .filter((s) => !me || me.screens.includes(s.id));
  // The STEP reached, which is not the same as the screen shown: the Workspace and All Notes
  // are read after the extraction and sit at its step, so the bar marks progress instead of
  // going blank on the screen an analyst spends most of their time on.
  const curIdx = steps.findIndex((s) => s.id === stepIdForPath(loc.pathname));

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        height: 52,
        flex: "0 0 52px",
        padding: "0 16px",
        background: color.topbar,
        color: "#e7ebf1",
        borderBottom: `1px solid ${color.topbarBorder}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <div
          style={{
            width: 26,
            height: 26,
            borderRadius: 6,
            background: color.indigo,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: 13,
            color: "#fff",
          }}
        >
          FX
        </div>
        <span style={{ fontWeight: 600, letterSpacing: ".2px" }}>FinExtract</span>
      </div>

      <div style={{ width: 1, height: 22, background: color.divider }} />

      <PinnedRunChip />

      <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.25, minWidth: 0, overflow: "hidden" }}>
        <span style={{ fontWeight: 600, fontSize: 12.5, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {title}
        </span>
        <span style={{ fontSize: 11, color: color.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {subtitle}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 2, marginLeft: "auto" }}>
        {steps.map((s, i) => {
          const active = i === curIdx;
          const done = curIdx !== -1 && i < curIdx;
          return (
            <div
              key={s.id}
              onClick={() => nav(s.path)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 7,
                padding: "5px 10px",
                borderRadius: 7,
                cursor: "pointer",
                background: active ? color.stepperActive : "transparent",
              }}
            >
              <span
                style={{
                  width: 18,
                  height: 18,
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 10,
                  fontWeight: 600,
                  background: active ? color.indigo : done ? color.greenFg : color.divider,
                  color: active || done ? "#fff" : color.muted,
                }}
              >
                {done ? "✓" : i + 1}
              </span>
              <span
                style={{
                  fontSize: 11.5,
                  fontWeight: active ? 600 : 500,
                  color: active ? "#fff" : done ? "#aeb6c1" : color.muted,
                }}
              >
                {t(`step.${s.id}`)}
              </span>
            </div>
          );
        })}
      </div>

      <div style={{ width: 1, height: 22, background: color.divider }} />
      <LanguageSwitcher />
      <div style={{ width: 1, height: 22, background: color.divider }} />
      <UserMenu />
    </div>
  );
}
