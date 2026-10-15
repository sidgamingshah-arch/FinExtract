/** The coverage contract, on the screen the reviewer already reads.
 *
 * The pipeline has always computed it — how many of the template's relations could be evaluated
 * against this filing at all — and sent it to the run log, which no endpoint serves. So the
 * Review screen listed failures and said nothing about relations that were never evaluable, and
 * "3 relations passed" read as "the statement is verified". This band is that report reaching a
 * human, derived by the server in the same request as the findings it sits above.
 *
 * Two rules it exists to keep:
 *  - it counts RELATIONS, never findings and never lines, and says so in its own subhead, because
 *    the header tiles directly above it count the other two;
 *  - it prints NO percentage. Two fractions carry the same information and cannot be read as a
 *    score — and an unavailable report is stated in words, never rendered as "0 of 0", which is
 *    the exact misread the coverage report exists to prevent. */
import type { ReactNode } from "react";

import { useT } from "../i18n";
import { color, font } from "../theme";
import type { CoverageBlock, CoverageStatement } from "../types";

/** Tone for a coverage status. Only the COLOUR is decided here — the words are the payload's
 *  `status_label`, localized server-side, so the band can never label a status the server
 *  spells differently. An unknown code stays neutral rather than being guessed at. */
function statusTone(status: string): { fg: string; bg: string } {
  const s = (status || "").toUpperCase();
  if (s === "PASSED") return { fg: color.greenFg, bg: color.greenBg };
  if (s === "FAILED") return { fg: color.redFg, bg: color.redBg };
  if (s === "UNVALIDATED") return { fg: color.redFg, bg: color.redBg };
  if (s === "PARTIAL") return { fg: color.amberFg, bg: color.amberBg };
  if (s === "NOT_READ") return { fg: color.amberFg, bg: color.amberBg };
  return { fg: color.muted, bg: color.rowAltBg };
}

function Shell({ children, tone }: { children: ReactNode; tone?: "loud" | "muted" }) {
  return (
    <div
      data-testid="rv-coverage"
      style={{
        background: "#fff",
        border: `1px solid ${tone === "loud" ? color.amberFg : color.cardBorder}`,
        borderLeft: `3px solid ${tone === "loud" ? color.amberFg : color.indigo}`,
        borderRadius: 11,
        padding: "13px 16px 14px",
        marginBottom: 14,
      }}
    >
      {children}
    </div>
  );
}

function Head({ note }: { note?: string }) {
  const t = useT();
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, color: color.ink }}>{t("r.cov.title")}</div>
      <div style={{ fontSize: 11, color: color.muted, lineHeight: 1.5 }}>
        {note ?? t("r.cov.subhead")}
      </div>
    </div>
  );
}

/** A quantity and what it counts, always in that order and always from the payload. */
function Q({ n, label, fg }: { n: number; label: string; fg?: string }) {
  return (
    <span style={{ fontSize: 11.5, color: fg ?? color.sec }}>
      <span style={{ fontFamily: font.mono, fontWeight: 600 }}>{n}</span> {label}
    </span>
  );
}

function Pill({ text, status }: { text: string; status: string }) {
  const tone = statusTone(status);
  return (
    <span
      data-testid="rv-cov-status"
      data-status={status}
      style={{
        fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 20,
        background: tone.bg, color: tone.fg, whiteSpace: "nowrap",
      }}
    >
      {text}
    </span>
  );
}

/** One statement's row. `declarable` is the denominator the filing could answer at all, which is
 *  why an absent statement does not drag it down. */
function StatementRow({ cov }: { cov: CoverageStatement }) {
  const t = useT();
  const unvalidated = (cov.status || "").toUpperCase() === "UNVALIDATED";
  // Its pages were not read: nothing on it was checked, and the remedy is to read them.
  const notRead = (cov.status || "").toUpperCase() === "NOT_READ";
  return (
    <div
      data-testid="rv-coverage-stmt"
      data-statement={cov.statement}
      data-status={cov.status}
      style={{
        display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10,
        padding: "6px 0 6px 9px",
        borderLeft: `3px solid ${unvalidated ? color.redFg : notRead ? color.amberFg : "transparent"}`,
        borderBottom: `1px dashed ${color.hairline2}`,
      }}
    >
      <span style={{ fontSize: 12, fontWeight: 500, minWidth: 150 }}>{cov.label}</span>
      <Pill text={cov.status_label} status={cov.status} />
      <span style={{ fontSize: 11.5, color: color.sec, fontFamily: font.mono }}>
        {cov.evaluated} / {cov.declarable}
      </span>
      <Q n={cov.passed} label={t("r.cov.held")} fg={color.greenFg} />
      <Q n={cov.failed} label={t("r.cov.failed")} fg={cov.failed > 0 ? color.redFg : color.muted} />
      <Q n={cov.skipped} label={t("r.cov.notEvaluable")} fg={color.muted} />
      {unvalidated && (
        <span style={{ fontSize: 11, color: color.redFg, flexBasis: "100%", lineHeight: 1.5 }}>
          {t("r.cov.provedNothing")}
        </span>
      )}
      {notRead && (
        <span data-testid="rv-cov-not-read"
              style={{ fontSize: 11, color: color.amberFg, flexBasis: "100%", lineHeight: 1.5 }}>
          {t("r.cov.notRead")}
        </span>
      )}
    </div>
  );
}

export function CoverageBand({ block }: { block: CoverageBlock | undefined }) {
  const t = useT();

  // The payload is the only source of this report. Say it is absent rather than render an empty
  // band: silence where a coverage statement belongs reads as "everything was checked".
  if (!block) {
    return (
      <Shell tone="loud">
        <Head note={t("r.cov.missing")} />
      </Shell>
    );
  }

  if (!block.available) {
    // Stated, not zeroed. On the seeded sample there are no relations to report on at all, so
    // that one is muted; every other reason is a real gap in what this filing was checked against.
    const sample = block.reason === "sample";
    return (
      <Shell tone={sample ? "muted" : "loud"}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{t("r.cov.title")}</span>
          <span
            data-testid="rv-cov-unavailable"
            data-reason={block.reason}
            style={{ fontSize: 11.5, lineHeight: 1.5, color: sample ? color.muted : color.amberFg,
                     fontWeight: sample ? 400 : 600 }}
          >
            {block.reason_label}
          </span>
        </div>
      </Shell>
    );
  }

  const agg = block.aggregate;
  return (
    <Shell>
      <Head />

      {/* Aggregate — every number from block.aggregate, none of them added up here. */}
      <div
        data-testid="rv-cov-aggregate"
        style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 12,
                 paddingBottom: 9, borderBottom: `1px solid ${color.hairline2}` }}
      >
        <span style={{ fontSize: 12, fontWeight: 600 }}>{agg.label}</span>
        <Pill text={agg.status_label} status={agg.status} />
        <span style={{ fontSize: 11.5, color: color.ink2 }}>
          <span style={{ fontFamily: font.mono, fontWeight: 600 }}>
            {agg.evaluated} / {agg.declarable}
          </span>{" "}
          {t("r.cov.relationsEvaluated")}
        </span>
        <Q n={agg.passed} label={t("r.cov.held")} fg={color.greenFg} />
        <Q n={agg.failed} label={t("r.cov.failed")} fg={agg.failed > 0 ? color.redFg : color.muted} />
        <Q n={agg.skipped} label={t("r.cov.notEvaluable")} fg={color.muted} />
      </div>

      {/* The two fractions, side by side and never one without the other: how many of the
          relations that RAN held, and how many of the answerable ones ran at all. A single rate
          shown alone is the collapse this report exists to prevent. */}
      <div
        data-testid="rv-cov-fractions"
        style={{ display: "flex", gap: 22, flexWrap: "wrap", padding: "9px 0",
                 borderBottom: `1px solid ${color.hairline2}` }}
      >
        <span style={{ fontSize: 11.5, color: color.sec }}>
          {agg.validation_rate === null ? (
            t("r.cov.na")
          ) : (
            <>
              <span style={{ fontFamily: font.mono, fontWeight: 600 }}>
                {agg.passed} / {agg.evaluated}
              </span>{" "}
              {t("r.cov.ofThoseThatRan")}
            </>
          )}
        </span>
        <span style={{ fontSize: 11.5, color: color.sec }}>
          <span style={{ fontFamily: font.mono, fontWeight: 600 }}>
            {agg.evaluated} / {agg.declarable}
          </span>{" "}
          {t("r.cov.ofThoseAnswerable")}
        </span>
      </div>

      {/* Per statement, in the server's order. */}
      <div style={{ marginTop: 4 }}>
        {block.statements.map((s) => (
          <StatementRow key={s.statement} cov={s} />
        ))}
      </div>

      {/* Why relations could not be evaluated. THESE ARE LABELS: no cursor, no hover, no handler
          — a chip that looks like a filter here would be a control with nothing behind it. */}
      {block.skips.length > 0 && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
          {block.skips.map((s) => (
            <div
              key={s.bucket}
              data-testid="rv-cov-skip"
              data-bucket={s.bucket}
              style={{ maxWidth: 250, background: color.rowAltBg, border: `1px solid ${color.hairline3}`,
                       borderRadius: 8, padding: "6px 9px" }}
            >
              <div style={{ fontSize: 11.5, fontWeight: 600, color: color.sec }}>
                <span style={{ fontFamily: font.mono }}>{s.count}</span> {s.label}
                {!s.counts_in_denominator && (
                  <span style={{ fontWeight: 400, color: color.muted }}> · {t("r.cov.notCounted")}</span>
                )}
              </div>
              <div style={{ fontSize: 10.5, color: color.muted, lineHeight: 1.45, marginTop: 2 }}>
                {s.meaning}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Alarms in the server's order (unenforceable first). Rendered from this list ONLY: a
          second alarm synthesised from a statement's status would double-report it. */}
      {block.alarms.map((a, i) => (
        <div
          key={`${a.code}-${a.rule_id ?? a.statement ?? i}`}
          data-testid="rv-alarm"
          data-code={a.code}
          data-assurance-gap={a.assurance_gap}
          style={{
            marginTop: 9, padding: "8px 11px", borderRadius: 8, lineHeight: 1.5,
            background: a.assurance_gap ? color.redBg : color.amberBg,
            border: `1px solid ${a.assurance_gap ? color.redFg : color.amberFg}33`,
            color: a.assurance_gap ? color.redFg : color.amberFg,
          }}
        >
          <span style={{ fontSize: 11.5, fontWeight: 700 }}>{a.label}</span>
          {a.rule_id && (
            <span style={{ fontFamily: font.mono, fontSize: 10.5, marginLeft: 7 }}>{a.rule_id}</span>
          )}
          {a.statement && (
            <span style={{ fontSize: 10.5, marginLeft: 7, opacity: 0.85 }}>{a.statement}</span>
          )}
          <div style={{ fontSize: 11, marginTop: 2 }}>{a.text}</div>
        </div>
      ))}

      {/* Why the band's failed count can exceed the number of structural cards above it. */}
      {block.failed_reported_elsewhere > 0 && (
        <div data-testid="rv-cov-elsewhere" style={{ fontSize: 11, color: color.muted, marginTop: 9 }}>
          <span style={{ fontFamily: font.mono, fontWeight: 600 }}>
            {block.failed_reported_elsewhere}
          </span>{" "}
          {t("r.cov.reportedAbove")}
        </div>
      )}

      {/* Traceable: which run's stored relation rows this was recomputed from, and by which
          engine — so a screenshot of the band can be tied back to the extraction. */}
      <div
        data-testid="rv-cov-footer"
        style={{ fontSize: 10.5, color: color.faint, marginTop: 10, fontFamily: font.mono }}
      >
        {t("r.cov.recomputedFrom")} {block.run_id} · {block.engine_version}
      </div>
    </Shell>
  );
}
