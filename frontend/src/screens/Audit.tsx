/** Run Trail — every extraction and LLM run in the deployment, with what each one spent.
 *
 *  DISTINCT FROM THE PANEL ON THE ANALYSIS SCREEN, which shows the runs against the ONE filing
 *  being worked. This screen crosses documents: it is how an administrator answers "what has this
 *  deployment spent, and on what", which nothing could answer before — the trail was a
 *  process-local dict, so it was empty after every restart. It is the only screen that reads past
 *  document ownership, which is why it is gated on `audit:view` rather than on the
 *  `commentary:view` its per-document sibling uses.
 *
 *  The totals are of the ENTRIES SHOWN. The table is append-only and the read is capped, so when
 *  the cap is reached the header says the figures describe a window — a lifetime-spend figure
 *  printed over the newest 500 of more rows is the kind of number this codebase keeps deleting. */
import { useState } from "react";

import { Card, ScreenHeader } from "../components/ui";
import { useT } from "../i18n";
import { useAdminAudit } from "../lib/queries";
import { color, fmtDuration, fmtTokens, font, radius } from "../theme";
import type { AdminAuditEntry, AuditTotals } from "../types";

const GRID = "1.5fr 1fr 1.15fr 0.75fr 1.1fr 0.6fr 0.8fr 0.8fr 0.8fr";
/** Page sizes an admin can ask for. The cap is the SERVER's, and it is stated rather than assumed:
 *  the response echoes the limit it applied and whether it truncated, so the figures above the
 *  table always describe the rows under it. */
const LIMITS = [100, 500, 2000];

/** One headline figure. Nothing is rendered for a quantity the trail cannot report. */
function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ flex: 1, minWidth: 120 }}>
      <div style={{ fontSize: 10.5, color: color.muted, letterSpacing: 0.3, marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 19, fontWeight: 600,
                    color: tone ?? color.ink }}>
        {value}
      </div>
    </div>
  );
}

function Totals({ totals, truncated, t }: {
  totals: AuditTotals; truncated: boolean; t: (k: string) => string;
}) {
  return (
    <Card>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
        <Stat label={t("ad.stat.runs")} value={totals.runs.toLocaleString()} />
        {/* Runs that reached the model at all. Shown beside the token totals because it is their
            denominator: the other runs mapped the filing lexically and could not have spent any. */}
        <Stat label={t("ad.stat.llmRuns")} value={totals.llm_runs.toLocaleString()} />
        <Stat label={t("ad.stat.failed")} value={totals.failed.toLocaleString()}
              tone={totals.failed > 0 ? color.redFg : undefined} />
        <Stat label={t("ad.stat.inTok")} value={totals.input_tokens.toLocaleString()} />
        <Stat label={t("ad.stat.outTok")} value={totals.output_tokens.toLocaleString()} />
        <Stat label={t("ad.stat.totTok")} value={totals.total_tokens.toLocaleString()} />
      </div>
      {/* THE CAP, SAID OUT LOUD. Without this the figures above read as the deployment's lifetime
          spend when they are the newest N runs of an unknown larger number. */}
      {truncated && (
        <div style={{ marginTop: 12, fontSize: 11.5, color: color.amberFg }}>
          {t("ad.truncated")}
        </div>
      )}
    </Card>
  );
}

export default function AuditScreen() {
  const t = useT();
  const [limit, setLimit] = useState(LIMITS[1]);
  const { data, isPending, isError } = useAdminAudit(limit);

  if (isPending) {
    return <div style={{ padding: 40, textAlign: "center", color: color.muted }}>Loading…</div>;
  }
  // A refused or broken read is not an empty trail, and must not render as one: "no runs recorded"
  // over a failed request would report a quiet deployment where there is a broken endpoint.
  if (isError || !data) {
    return (
      <div style={{ maxWidth: 1180, margin: "0 auto", padding: "26px 30px 60px" }}>
        <ScreenHeader title={t("ad.title")} subtitle={t("ad.subhead")} />
        <Card><div style={{ fontSize: 12.5, color: color.redFg }}>{t("ad.unavailable")}</div></Card>
      </div>
    );
  }

  const entries = data.entries;
  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", padding: "26px 30px 60px" }}>
      <ScreenHeader
        title={t("ad.title")}
        subtitle={t("ad.subhead")}
        right={
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5,
                          color: color.muted }}>
            {t("ad.show")}
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              style={{ fontSize: 12, padding: "6px 9px", borderRadius: radius.controlSm,
                       border: `1px solid ${color.cardBorder}` }}
            >
              {LIMITS.map((n) => (
                <option key={n} value={n}>{n.toLocaleString()}</option>
              ))}
            </select>
          </label>
        }
      />

      <div style={{ marginBottom: 16 }}>
        <Totals totals={data.totals} truncated={data.truncated} t={t} />
      </div>

      <Card pad={0} style={{ overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <div style={{ minWidth: 980 }}>
            <div
              data-testid="ad-head"
              style={{
                display: "grid", gridTemplateColumns: GRID, gap: 10, padding: "10px 14px",
                background: color.rowAltBg, borderBottom: `1px solid ${color.hairline2}`,
                fontSize: 10, fontWeight: 600, letterSpacing: 0.3, color: color.muted,
              }}
            >
              <span>{t("ad.col.run")}</span>
              <span>{t("ad.col.time")}</span>
              <span>{t("ad.col.document")}</span>
              <span>{t("ad.col.action")}</span>
              <span>{t("ad.col.model")}</span>
              <span style={{ textAlign: "right" }}>{t("ad.col.took")}</span>
              <span style={{ textAlign: "right" }}>{t("ad.col.inTok")}</span>
              <span style={{ textAlign: "right" }}>{t("ad.col.outTok")}</span>
              <span style={{ textAlign: "right" }}>{t("ad.col.totTok")}</span>
            </div>
            {entries.length === 0 && (
              <div style={{ padding: "18px 14px", fontSize: 12.5, color: color.muted }}>
                {t("ad.empty")}
              </div>
            )}
            {entries.map((e: AdminAuditEntry) => (
              <div
                key={e.run_id}
                data-testid="ad-row"
                style={{
                  display: "grid", gridTemplateColumns: GRID, gap: 10, padding: "11px 14px",
                  alignItems: "center", borderBottom: `1px solid ${color.hairline2}`,
                  opacity: e.status === "failed" ? 0.62 : 1,
                }}
              >
                <span style={{ fontFamily: font.mono, fontSize: 10.5, color: color.ink2,
                               wordBreak: "break-all" }}>
                  {e.run_id}
                </span>
                <span style={{ fontSize: 11, color: color.sec2 }}>
                  {new Date(e.created_at).toLocaleString()}
                </span>
                {/* The filing, or the ENTITY the run reported when the document is gone. An entry
                    outlives the document it describes, so a deleted upload leaves a row that still
                    accounts for its cost — and naming nothing there would make it unattributable. */}
                <span style={{ fontSize: 11, color: e.document ? color.ink : color.muted,
                               wordBreak: "break-word" }}>
                  {e.document || e.entity || t("ad.scopeGone")}
                </span>
                <span style={{ fontSize: 11 }}>
                  <span style={{
                    fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: radius.pill,
                    background: e.status === "failed" ? color.redBg
                      : e.action === "analysis" ? color.indigoTint2 : color.greenBg2,
                    color: e.status === "failed" ? color.redFg
                      : e.action === "analysis" ? color.indigo : color.greenFg,
                  }}>
                    {e.action}
                  </span>
                </span>
                <span style={{ fontFamily: font.mono, fontSize: 10.5, color: color.sec,
                               wordBreak: "break-all" }}>
                  {e.model || "—"}
                </span>
                <span style={{ fontFamily: font.mono, fontSize: 11.5, textAlign: "right",
                               color: color.sec2 }}>
                  {fmtDuration(e.duration_ms)}
                </span>
                <span style={{ fontFamily: font.mono, fontSize: 11.5, textAlign: "right" }}>
                  {fmtTokens(e.input_tokens)}
                </span>
                <span style={{ fontFamily: font.mono, fontSize: 11.5, textAlign: "right" }}>
                  {fmtTokens(e.output_tokens)}
                </span>
                <span style={{ fontFamily: font.mono, fontSize: 11.5, textAlign: "right",
                               fontWeight: 600 }}>
                  {fmtTokens(e.total_tokens)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </Card>
      <p style={{ margin: "12px 2px 0", fontSize: 11, color: color.muted2, lineHeight: 1.6 }}>
        {t("ad.footnote")}
      </p>
    </div>
  );
}
