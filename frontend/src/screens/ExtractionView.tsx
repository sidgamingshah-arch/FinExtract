/** Screen 4 — Extraction. The run and its output for the active document, on its own screen
 * (SCREENS.extraction, /extraction) rather than as something the Workspace tab happened to show.
 *
 * While the run is in flight the screen REPORTS it — the stages, which are finished, which one it
 * is on, how far through and the tail of the log (see RunProgress). It used to print one static
 * "Extracting…" for the whole of a multi-stage run.
 *
 * Once the run succeeds: real line items with click-to-source provenance. Clicking a PDF value
 * opens a Source panel that renders that page and highlights the value's bounding box. Mapping runs
 * against the rulebook in force for the selected template, or whichever one the reader pins here
 * instead — and the rulebook the run RECORDED is named above the rows, from the run's own record
 * (see RulebookPicker). Distinct from the demo-driven workspace: this reads a live extraction run. */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Button, Card } from "../components/ui";
import { EmptyState } from "../components/EmptyState";
import { ExcelSourcePanel, PagedSource, toPicked, type Picked } from "../components/SourceViewer";
import { useT } from "../i18n";
import { refusalText } from "../lib/api";
import {
  activeTemplate, ontologyInForce, useDocumentAnalysis, useDocumentRunStatus, useExtraction, useOntologies,
  useReextract, useStopRun, useTemplates,
} from "../lib/queries";
import { useCan } from "../lib/rbac";
import { useUI } from "../store";
import { SCREENS } from "./config";
import { color, fmtElapsed, font, radius } from "../theme";
import type { ExtractionProgress, ExtractionRow, Locale, OntologyRef, RulebookRecord } from "../types";

const GRID = "1.8fr 56px 1.3fr 1.1fr";
/** Stage table: marker / stage / status — the Integrity screen's issue-row shape. */
const STAGE_GRID = "22px 1fr 110px";

/** Digit grouping follows the FILING, not the app: Indian filings group as 12,68,100 while
 *  IFRS/HKFRS filings group as 1,268,100. Rendering an HK filing with Indian grouping would
 *  misrepresent the printed figure, so the source currency selects the convention. */
function groupingLocale(currency: string | undefined): string {
  return currency === "INR" ? "en-IN" : "en-US";
}

/** Format an extracted figure for reading: grouped digits, negatives in parentheses (the
 *  accounting convention used elsewhere in the app). Anything non-numeric is passed through
 *  unchanged rather than coerced — a caption that failed to parse must not become "0". */
function fmtFigure(raw: string | null, loc: string): string {
  if (raw == null || raw === "") return "—";
  const n = Number(raw);
  if (!Number.isFinite(n)) return raw;
  const abs = Math.abs(n).toLocaleString(loc, { maximumFractionDigits: 2 });
  return n < 0 ? `(${abs})` : abs;
}

function RowLine({ row, t, onPick, loc }: {
  row: ExtractionRow; t: (k: string) => string; onPick: (p: Picked) => void; loc: string;
}) {
  const flagged = row.flags?.includes("low_mapping_confidence");
  // The model's own stated justification for an LLM-decided row (see
  // stages/map_ontology.py::_apply), carried as a row flag so it reaches here without a schema
  // change. Shown as a small "why" marker rather than inline text — a rationale sentence would
  // otherwise crowd out the figures on every LLM-mapped row.
  const llmReason = row.flags?.find((f) => f.startsWith("llm_reason:"))?.slice("llm_reason:".length);
  return (
    <div style={{ display: "grid", gridTemplateColumns: GRID, gap: 10, padding: "9px 14px",
                  alignItems: "center", borderBottom: `1px solid ${color.hairline2}` }}>
      <span style={{ fontSize: 12.5, color: color.ink, display: "flex", alignItems: "center", gap: 6 }}>
        {row.source_label}
        {llmReason && (
          <span title={`Why this concept: ${llmReason}`}
                style={{ fontSize: 10, fontFamily: font.mono, color: color.indigo, cursor: "help",
                         border: `1px solid ${color.indigoBorder2}`, borderRadius: 3, padding: "0 4px" }}>
            why?
          </span>
        )}
      </span>
      <span style={{ fontSize: 11, fontFamily: font.mono, color: color.muted }}>{row.note ?? ""}</span>
      <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {row.values.map((v, i) => {
          const picked = toPicked(v.provenance, row.source_label);
          const conf = v.confidence;
          // A value that participates in a failed check (balance / note tie) is flagged so a
          // doubtful number reads at a glance — per value, not just per row.
          const low = conf && (conf.flags.length > 0 || conf.overall < 0.75);
          const confTip = conf
            ? `confidence ${Math.round(conf.overall * 100)}%${conf.flags.length ? " · " + conf.flags.join(", ") : ""}`
            : "";
          const p = v.provenance;
          // The figure itself is the click-to-source link — click the number to jump to it in
          // the page image. A light page/cell ref sits beside it (no heavy chip).
          const ref = p
            ? p.source_kind === "spreadsheet" && p.sheet
              ? `${p.sheet}!${p.cell ?? ""}`
              // The folio the filing printed, falling back to the sheet position. Same string the
              // backend puts on a review card and in the export.
              : `p.${p.printed_page || p.page_index + 1}`
            : null;
          const clickable = !!picked;
          return (
            <span
              key={i}
              onClick={clickable ? () => onPick(picked!) : undefined}
              role={clickable ? "button" : undefined}
              title={[v.value != null ? `as printed: ${v.value}` : "", confTip,
                       clickable ? "click to show in source" : ""]
                       .filter(Boolean).join(" · ") || undefined}
              style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
                       gap: 8, cursor: clickable ? "pointer" : "default" }}
            >
              <span style={{ fontSize: 11.5, fontFamily: font.mono, color: low ? color.amberFg : color.ink,
                             borderBottom: clickable ? `1px dashed ${color.indigoBorder2}` : undefined }}>
                {fmtFigure(v.value, loc)}{low ? " ⚠" : ""}
              </span>
              {ref && (
                <span style={{ fontFamily: font.mono, fontSize: 10, color: clickable ? color.indigo : color.muted2,
                               whiteSpace: "nowrap" }}>
                  {ref}{p?.bbox ? " ⤢" : ""}
                </span>
              )}
            </span>
          );
        })}
      </span>
      <span style={{ display: "flex", flexDirection: "column" }}>
        <span style={{ fontSize: 11.5, color: row.canonical_key ? color.ink2 : color.faint }}>
          {row.canonical_key ?? t("ex.unmapped")}
          {flagged && <span style={{ color: color.amberFg, marginInlineStart: 6 }}>⚠</span>}
        </span>
        {row.mapping_method && (
          <span style={{ fontSize: 9.5, fontFamily: font.mono, color: color.muted2 }}>{row.mapping_method}</span>
        )}
      </span>
    </div>
  );
}

/** Localized heading for a ratio category (backend sends the English category name). */
const CAT_KEY: Record<string, string> = {
  Liquidity: "ex.cat.liquidity", Leverage: "ex.cat.leverage", Coverage: "ex.cat.coverage",
  Efficiency: "ex.cat.efficiency", Profitability: "ex.cat.profitability",
};

/** Derived analysis (computed from the extracted values): ratios, qualitative disclosures,
 * and free-form notes — the on-screen twin of the export's Ratios/Disclosures/Notes sheets. */
function AnalysisSection({ id, locale, t }: { id: string; locale: Locale; t: (k: string) => string }) {
  const q = useDocumentAnalysis(id, locale);
  // Reserve the section's footprint with a skeleton while the (separate) analysis query loads,
  // so it fills in place instead of popping in below and pushing the layout.
  if (!q.data) {
    if (q.isError) return null;
    const bar = (w: string, h = 12) => (
      <div style={{ width: w, height: h, borderRadius: 5, background: color.rowAltBg }} />
    );
    return (
      <div style={{ marginTop: 22, display: "grid", gap: 18 }} aria-hidden>
        {[0, 1, 2].map((k) => (
          <Card key={k}>
            <div style={{ display: "grid", gap: 10 }}>
              {bar("120px", 14)}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 8 }}>
                {[0, 1, 2, 3].map((j) => (
                  <div key={j} style={{ border: `1px solid ${color.hairline3}`, borderRadius: 8,
                                        padding: "8px 10px", display: "grid", gap: 6 }}>
                    {bar("60%", 16)}{bar("80%", 10)}
                  </div>
                ))}
              </div>
            </div>
          </Card>
        ))}
      </div>
    );
  }
  const { ratios, disclosures, notes } = q.data;

  // Group ratios by category, preserving the backend's category ordering.
  const groups: { cat: string; items: typeof ratios }[] = [];
  for (const r of ratios) {
    const cat = r.category || "Profitability";
    let g = groups.find((x) => x.cat === cat);
    if (!g) { g = { cat, items: [] }; groups.push(g); }
    g.items.push(r);
  }

  return (
    <div style={{ marginTop: 22, display: "grid", gap: 18 }}>
      <Card>
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.3, color: color.muted, marginBottom: 10 }}>
          {t("ex.ratios")}
        </div>
        {groups.map((g) => (
          <div key={g.cat} style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase",
                          color: color.sec2, marginBottom: 6 }}>
              {t(CAT_KEY[g.cat] || "") || g.cat}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 8 }}>
              {g.items.map((r) => (
                <div key={r.key} title={r.formula}
                     style={{ border: `1px solid ${color.hairline3}`, borderRadius: 8, padding: "8px 10px",
                              opacity: r.available ? 1 : 0.5 }}>
                  <div style={{ fontSize: 15, fontWeight: 700, fontFamily: font.mono,
                                color: r.available ? color.ink : color.faint }}>{r.display}</div>
                  <div style={{ fontSize: 10.5, color: color.sec2 }}>{r.label}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </Card>

      <Card>
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.3, color: color.muted, marginBottom: 10 }}>
          {t("ex.highlights")}
        </div>
        {notes.map((n, i) => (
          <div key={i} style={{ marginBottom: 9 }}>
            <div style={{ fontSize: 11.5, fontWeight: 600, color: color.ink }}>{n.title}</div>
            <div style={{ fontSize: 11.5, color: color.sec2, lineHeight: 1.5 }}>{n.text}</div>
          </div>
        ))}
      </Card>

      <Card>
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.3, color: color.muted, marginBottom: 10 }}>
          {t("ex.disclosures")}
        </div>
        {disclosures.map((d) => (
          <div key={d.key} style={{ display: "grid",
                                    // …plus a column for the AMOUNT, where the pipeline computed
                                    // one. This panel used to say only that a disclosure existed
                                    // and on which page, so a reader was told a filing HAS
                                    // contingent liabilities while the ¥118,754,500 the pipeline
                                    // had already computed sat on a row they were not looking at.
                                    gridTemplateColumns: "1.4fr 70px 1.1fr 2.5fr",
                                    gap: 10, alignItems: "center", padding: "6px 0",
                                    borderBottom: `1px solid ${color.hairline2}` }}>
            <span style={{ fontSize: 12, color: color.ink }}>{d.label}</span>
            <span style={{ fontSize: 10.5, fontWeight: 700,
                           color: d.present ? color.greenFg : color.faint }}>
              {d.present ? t("view.folioN").replace("{n}", String(d.page)) : "—"}
            </span>
            {/* Right-aligned and tabular, like every other figure on this screen. Blank rather
                than "0" when there is no amount: most of these disclosures are qualitative, and
                for the quantified one an empty cell means "no figure was disclosed", which is a
                different statement from "the exposure is nil". */}
            <span style={{ fontSize: 11.5, color: color.ink, textAlign: "right",
                           fontVariantNumeric: "tabular-nums" }}>
              {d.amount
                ? `${d.currency ? `${d.currency} ` : ""}${Number(d.amount).toLocaleString(
                    undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                : ""}
            </span>
            <span style={{ fontSize: 11, color: color.muted, fontStyle: d.snippet ? "italic" : "normal",
                           whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {d.snippet || t("ex.notFound")}
            </span>
          </div>
        ))}
      </Card>
    </div>
  );
}

/** What the RUN says about the rulebook it read the filing against.
 *
 *  Every word here comes out of the run's own record (see `RulebookRecord`). The screen used to
 *  write this sentence itself, from the ontology list and its own idea of which rulebook was in
 *  force — and a reload after a newer rulebook was published then described a run that had used a
 *  replaced rulebook as governed by the current one. Which rulebook produced a figure is part of
 *  the figure, so it is reported, never recomputed: a run that used a superseded rulebook says
 *  "replaced" even when every list on the client insists otherwise.
 */
function describeRun(r: RulebookRecord, t: (k: string) => string): { text: string; warn: boolean } {
  const named = (s: string) =>
    s.replace("{key}", r.ontology_key).replace("{v}", String(r.version));
  if (r.status === "engine_default") return { text: t("tp.rb.usedEngineDefault"), warn: true };
  if (r.status === "missing") {
    return { text: t("tp.rb.usedMissing").replace("{id}", r.ontology_version_id), warn: true };
  }
  // A rulebook whose stored definition would not load governed nothing, however firmly the run
  // names it — so the sentence that names it also says that.
  const dead = r.applied === false ? ` ${t("tp.rb.notApplied")}` : "";
  if (r.status === "in_force") {
    return {
      text: named(t("tp.rb.usedInForce")).replace("{tpl}", r.target_template_key) + dead,
      warn: r.applied === false,
    };
  }
  // Departing from what is in force is worth naming what was departed from — except when the
  // record's successor IS this rulebook, which happens when every stored rulebook for the template
  // has been replaced and there is nothing current to point at.
  const successor = r.in_force_ontology_key && r.in_force_ontology_key !== r.ontology_key
    ? ` ${t("tp.rb.inForceIs").replace("{key}", r.in_force_ontology_key)
          .replace("{v}", String(r.in_force_version))}`
    : "";
  const text = named(t(r.status === "superseded" ? "tp.rb.usedSuperseded" : "tp.rb.usedPinned"));
  return { text: text + successor + dead, warn: true };
}

/** Which rulebook a run reads the filing against, and what the run then recorded.
 *
 *  Until this existed the answer was a property of the configuration: whichever rulebook declared
 *  itself the successor won, and nobody could pin an older one, or read one filing against two of
 *  them to see what changed. The default is still the rulebook in force — computed by the ONE
 *  shared rule (see `ontologyInForce`), not a local copy of it.
 *
 *  Choosing is not cosmetic: `ontology_version_id` travels with the POST that starts the run, so
 *  the pick decides the rules the mapper reasons with. Each choice is its own cached run, which is
 *  what makes comparing two of them on one filing a matter of switching back and forth.
 *
 *  The select is the CHOICE — a statement about the configuration, so its annotations come from the
 *  ontology list. The sentence under it is the RUN, so it comes from `record` and from nothing
 *  else. Until the run reports one there is no run to describe, and the line says what is being
 *  started instead of making a claim about figures that are not on screen yet.
 *
 *  Choosing only means something to a reader who can START a run: the pick travels with the POST, so
 *  for a reader who cannot (no `pipeline:run`) the select is dropped entirely — and with it any
 *  client-derived name for the rulebook. What is left is the RUN's own sentence, which is the only
 *  answer that is true about the figures on screen: naming the rulebook in force above a sentence
 *  saying the run used a superseded one is the contradiction this component exists to prevent.
 */
function RulebookPicker({ rows, inForce, chosen, record, onChoose, canChoose, templateKey, t }: {
  rows: OntologyRef[]; inForce: OntologyRef | undefined; chosen: OntologyRef | undefined;
  record: RulebookRecord | undefined; onChoose: (id: string) => void; canChoose: boolean;
  templateKey: string | undefined; t: (k: string) => string;
}) {
  // Newest-looking first, and stable: same key together, highest version on top.
  const sorted = [...rows].sort((a, b) => (a.ontology_key === b.ontology_key
    ? b.version - a.version
    : a.ontology_key.localeCompare(b.ontology_key)));
  const used = record ? describeRun(record, t) : undefined;

  return (
    <div data-testid="ex-rulebook"
         style={{ display: "grid", gap: 5, marginBottom: 12, maxWidth: 760 }}>
      {canChoose && (
      <div style={{ display: "flex", alignItems: "baseline", gap: 9, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: color.muted }}>{t("tp.rb.label")}</span>
        <select
          data-testid="ex-rulebook-pick"
          value={chosen?.id ?? ""}
          onChange={(e) => onChoose(e.target.value)}
          style={{ fontSize: 12, fontWeight: 600, padding: "5px 9px", borderRadius: 7,
                   border: `1px solid ${color.controlBorder}`, background: "#fff", color: color.ink,
                   maxWidth: 420 }}
        >
          {!chosen && <option value="">{t("tp.rb.none")}</option>}
          {sorted.map((o) => (
            <option key={o.id} value={o.id}>
              {`${o.ontology_key} · v${o.version}`}
              {/* Exclusive, because the two read as a contradiction together. `ontologyInForce`
                  prefers a live row, so a superseded rulebook is only ever in force when EVERY
                  rulebook for the template has been superseded — which is exactly the state a
                  reader most needs described plainly rather than as "— in force — superseded". */}
              {o.id === inForce?.id
                ? ` — ${t(o.superseded ? "tp.rb.inForceSuperseded" : "tp.rb.inForce")}`
                : o.superseded ? ` — ${t("tp.rb.superseded")}` : ""}
              {templateKey && o.target_template_key !== templateKey
                ? ` — ${t("tp.rb.otherTemplate").replace("{tpl}", o.target_template_key)}` : ""}
            </option>
          ))}
        </select>
      </div>
      )}
      {used ? (
        // The id and the status are on the element that prints them so that what the run recorded
        // can be checked against what was chosen, rather than inferred from the wording.
        <span data-testid="ex-rulebook-used"
              data-rulebook-id={record?.ontology_version_id}
              data-rulebook-status={record?.status}
              style={{ fontSize: 11, color: used.warn ? color.amberFg : color.muted2,
                       lineHeight: 1.5 }}>
          {used.text}
        </span>
      ) : (
        <span data-testid="ex-rulebook-pending"
              style={{ fontSize: 11, color: color.muted2, lineHeight: 1.5 }}>
          {chosen
            ? t("tp.rb.starting").replace("{key}", chosen.ontology_key)
                .replace("{v}", String(chosen.version))
            : t("tp.rb.none")}
        </span>
      )}
    </div>
  );
}

/** A pipeline stage's name in words.
 *
 *  The identifiers are the pipeline's own (`app/stages/*.py::name`), and `translate` answers with
 *  the KEY it was given when no locale holds a string for it — so a stage the pipeline gains would
 *  print "ex.stage.whatever", which is a blank row as far as a reader is concerned. An unknown
 *  stage prints its identifier instead: the pipeline assembles the list (core/pipeline.py), so this
 *  screen must survive it growing without a release of its own. */
function stageLabel(name: string, t: (k: string) => string): string {
  const key = `ex.stage.${name}`;
  const s = t(key);
  return s === key ? name : s;
}

/** The phase words that are NOT a stage. `phase` carries either one of these or the stage's own
 *  name (see `ExtractionProgress`) — the pipeline reports progress as `emit_progress(stage.name, …)`
 *  — so the stage a run is on has to be read from `phase` as well as from `stage`, or a run that
 *  states its stage in the field it has always used would be reported as merely "queued". */
const NON_STAGE_PHASES = new Set(["", "queued", "running", "done", "failed"]);

/** The stage a run is on, read from either field the contract puts it in — ONE spelling of the
 *  question, because the progress panel and the failure notice must not disagree about which stage
 *  the run reached. Empty when the run names none. */
function currentStage(progress: ExtractionProgress | undefined): string {
  if (!progress) return "";
  return progress.stage || (NON_STAGE_PHASES.has(progress.phase) ? "" : progress.phase);
}

/** How long the run has been going: THE RUN'S OWN FIGURE, advanced by the time since it arrived.
 *
 *  Still not a clock this screen starts on mount — that timer would disagree with the pipeline's the
 *  moment a poll came late or the tab was backgrounded, and the pipeline's figure is the one that
 *  means anything. The anchor here is always the server's `elapsed_ms`; only the delta measured
 *  locally SINCE that number arrived is added to it. So the two can never diverge by more than one
 *  poll, and every poll snaps them back into agreement.
 *
 *  WHY IT IS NEEDED AT ALL. `elapsed_ms` used to be stamped into the progress record when the record
 *  was WRITTEN, and `_RunProgress` writes one record per stage transition — so the figure was frozen
 *  between stages and this line sat still and then jumped. On a slow stage (mapping a long filing
 *  through the LLM) it can sit still for minutes, which reads as a hung run rather than a working
 *  one. The server now derives it per request (`routes/extractions._served_progress`), which unfreezes
 *  it; this makes it advance BETWEEN requests too, so a poll that is slow or dropped does not stop
 *  the clock on a run that is plainly still going.
 *
 *  `Math.max` because the one visible glitch that matters is a clock ticking BACKWARDS, which is
 *  what a stale anchor would produce on the single render between a new figure arriving and the
 *  effect that re-anchors on it.
 *
 *  A SETTLED RUN IS LEFT ALONE. Its stored figure is a duration, measured when it finished, and it
 *  must not climb for as long as somebody leaves the screen open. */
function useLiveElapsed(elapsedMs: number | undefined, live: boolean): number | undefined {
  const [anchor, setAnchor] = useState<{ ms: number; at: number } | null>(null);
  const [, tick] = useState(0);
  const known = elapsedMs != null && Number.isFinite(elapsedMs);
  useEffect(() => {
    if (known) setAnchor({ ms: elapsedMs as number, at: Date.now() });
  }, [elapsedMs, known]);
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [live]);
  if (!known) return undefined;
  if (!live || anchor === null) return elapsedMs;
  return Math.max(elapsedMs as number, anchor.ms + Math.max(0, Date.now() - anchor.at));
}

/** WHAT THE RUN IS DOING, WHILE IT IS DOING IT.
 *
 *  For the whole of a multi-stage run this screen printed one static caption — "Extracting…" — over
 *  a poll that had been fetching the stage, its index, the stages already finished, the elapsed time
 *  and the log tail every second since the run started. Nothing on screen separated a run that was
 *  working from one that had hung, and a filing that takes minutes looked identical to a failure.
 *
 *  Every figure here is READ from `progress`; none is interpolated. A run the server has not
 *  recorded progress for says it is STARTING rather than printing a measured-looking 0%, a stage
 *  list the run has not reported is absent rather than assembled from a guess at the pipeline's
 *  shape, and the elapsed/percentage cards are simply not rendered when the run states neither. */
function RunProgress({ progress, stages, logTail, live, canStop, stopping, onStop, t }: {
  progress: ExtractionProgress | undefined;
  stages: string[] | undefined;
  logTail: string | undefined;
  /** Whether the run is still going, which is what decides between a clock and a duration. */
  live: boolean;
  canStop: boolean;
  stopping: boolean;
  onStop: () => void;
  t: (k: string) => string;
}) {
  const finished = new Set(progress?.stages_done ?? []);
  const current = currentStage(progress);
  // The stage list as SERVED, and nothing else — the pipeline assembles it, so it is never
  // reconstructed here. The stage the run says it is ON is added when the list does not carry it
  // (or has not arrived): the alternative is a table in which every row says "pending" while the
  // run is plainly working, or no table at all.
  const listed = stages?.length ? stages : [];
  const rows = current && !listed.includes(current) ? [...listed, current] : listed;
  // `pct` is the fraction the run row records (`{"phase": "done", "pct": 1.0}`), so it is
  // multiplied out here rather than assumed to already be a percentage — and clamped, because a
  // percentage outside 0–100 is a broken contract, not a measurement.
  //
  // The zero a run is CREATED with (`{"phase": "queued", "pct": 0.0}`) is a placeholder for a
  // measurement nobody has taken yet, not a measurement of nothing done: printing it would park a
  // "0% complete" gauge over a run that is working, which is the reading this panel exists to
  // prevent. So a zero counts only once the run also names the stage it is zero through.
  const fraction = progress && Number.isFinite(progress.pct)
    && !(progress.pct === 0 && !current) ? progress.pct : null;
  const pct = fraction === null ? null : Math.max(0, Math.min(100, Math.round(fraction * 100)));
  const elapsed = fmtElapsed(useLiveElapsed(progress?.elapsed_ms, live));
  // Queued is what the run says about itself; "starting" is what we say when it has said nothing.
  const phase = current ? stageLabel(current, t)
    : progress ? t("ex.run.queued") : t("ex.run.starting");

  const stats: { label: string; value: string; mono?: boolean }[] = [
    { label: t("ex.run.stage"), value: phase },
  ];
  // The position is printed only when BOTH halves are served, and exactly as served: "0 of 14"
  // assembled from two absences would read as a measured position in a pipeline that has reported
  // nothing, and re-basing the index would be a second spelling of a count the run already states.
  if (progress && Number.isFinite(progress.stage_index)
      && Number.isFinite(progress.stage_count) && progress.stage_count > 0) {
    stats.push({ label: t("ex.run.stages"),
                 value: `${progress.stage_index} / ${progress.stage_count}`, mono: true });
  }
  // WHAT THE STAGE IN FLIGHT IS DOING. Printed only when the run reports a total, because
  // `step_total === 0` means the stage has no sub-steps rather than none done — and "0 / 0" would
  // read as a stalled measurement instead of an absent one. Ontology mapping is the only stage
  // that reports these, and it is the one where a reader could previously not tell a working run
  // from a hung one: it makes every LLM call in the run, behind a single frozen stage name.
  if (progress && Number.isFinite(progress.step_total) && progress.step_total > 0) {
    const unit = progress.step_label || t("ex.run.steps");
    stats.push({ label: unit, value: `${progress.step_done} / ${progress.step_total}`,
                 mono: true });
  }
  // Cumulative across the run, so it keeps its meaning after mapping has finished and the
  // per-stage counter above has gone. Shown from the first call rather than from zero: a "0" on a
  // run with no LLM configured would invite the reader to wait for a number that never comes.
  if (progress && Number.isFinite(progress.llm_calls) && progress.llm_calls > 0) {
    stats.push({ label: t("ex.run.llmCalls"), value: String(progress.llm_calls), mono: true });
  }
  if (elapsed) stats.push({ label: t("ex.run.elapsed"), value: elapsed, mono: true });

  return (
    <div data-testid="ex-progress">
      {/* Header: what is happening (left) + how far it has got (right) — the Integrity screen's
          header shape, so the two read as the same product. */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between",
                    gap: 18, marginBottom: 18 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>{t("ex.running")}</h1>
          <p style={{ margin: 0, color: color.sec2, fontSize: 12.5, maxWidth: 620 }}>
            {t("ex.run.subhead")}
          </p>
          {canStop && (
            <Button variant="secondary" testid="ex-stop-run"
                    disabled={stopping}
                    onClick={onStop}
                    style={{ fontSize: 12, padding: "7px 14px", marginTop: 12 }}>
              {stopping ? t("ex.run.stopping") : t("ex.run.stop")}
            </Button>
          )}
        </div>
        {/* No card at all when the run has reported no percentage: an empty gauge, or one reading
            0%, is a measurement nobody took. The stage cards below still say what is happening. */}
        {pct !== null && (
          <div data-testid="ex-progress-pct" data-pct={String(pct)}
               style={{ background: color.surface, border: `1px solid ${color.cardBorder}`,
                        borderRadius: radius.card, padding: "12px 22px", textAlign: "center",
                        whiteSpace: "nowrap" }}>
            <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1, fontFamily: font.mono,
                          color: color.indigo }}>
              {pct}%
            </div>
            <div style={{ fontSize: 10, color: color.muted, marginTop: 3 }}>
              {t("ex.run.complete")}
            </div>
          </div>
        )}
      </div>

      {/* Stat row — one card per quantity the run actually states. */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${stats.length},1fr)`, gap: 12,
                    marginBottom: 18 }}>
        {stats.map((s) => (
          <div key={s.label} style={{ background: color.surface,
                                      border: `1px solid ${color.cardBorder}`,
                                      borderRadius: radius.cardSm, padding: "14px 15px" }}>
            <div style={{ fontSize: 11, color: color.muted, marginBottom: 7 }}>{s.label}</div>
            <div style={{ fontSize: 17, fontWeight: 600, color: color.ink,
                          fontFamily: s.mono ? font.mono : undefined }}>
              {s.value}
            </div>
          </div>
        ))}
      </div>

      {/* The stages, ticked off as the run finishes them. */}
      <Card pad={0} style={{ overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: STAGE_GRID, gap: 12,
                      padding: "11px 16px", background: color.rowAltBg,
                      borderBottom: `1px solid ${color.cardBorder}`, fontSize: 10.5,
                      fontWeight: 600, letterSpacing: 0.4, color: color.muted }}>
          <span></span>
          <span>{t("ex.run.colStage")}</span>
          <span>{t("ex.run.colStatus")}</span>
        </div>
        {rows.length === 0 && (
          <div style={{ padding: "18px 16px", fontSize: 12.5, color: color.muted }}>
            {t("ex.run.noStages")}
          </div>
        )}
        {rows.map((name) => {
          // `stages_done` is the run's own record of what it finished, so a stage it reports as
          // both current and done reads as done — the poll that catches the handover must not show
          // the same stage twice in two states.
          const done = finished.has(name);
          const active = !done && name === current;
          const state = done ? "done" : active ? "active" : "pending";
          const mark = done ? "✓" : active ? "●" : "○";
          const markFg = done ? color.greenFg : active ? color.indigo : color.faint;
          const pill = done ? { bg: color.greenBg2, fg: color.greenFg, text: t("ex.run.statusDone") }
            : active ? { bg: color.indigoTint2, fg: color.indigo, text: t("ex.run.statusActive") }
              : { bg: color.rowAltBg, fg: color.muted, text: t("ex.run.statusPending") };
          return (
            <div key={name} data-testid="ex-progress-stage" data-stage={name} data-state={state}
                 style={{ display: "grid", gridTemplateColumns: STAGE_GRID, gap: 12,
                          padding: "10px 16px", alignItems: "center",
                          borderBottom: `1px solid ${color.hairline2}`,
                          background: active ? color.indigoTint : undefined }}>
              <span style={{ fontSize: 11, color: markFg, textAlign: "center" }}>{mark}</span>
              <div>
                <div style={{ fontSize: 12.5, fontWeight: active ? 600 : 400,
                              color: done || active ? color.ink : color.sec2 }}>
                  {stageLabel(name, t)}
                </div>
                {/* The identifier the run reports, beside the label it was given — so what the
                    server said can be checked against what the screen shows. */}
                <div style={{ fontSize: 9.5, fontFamily: font.mono, color: color.muted2 }}>{name}</div>
              </div>
              <span style={{ justifySelf: "start", fontSize: 10.5, fontWeight: 600,
                             padding: "3px 9px", borderRadius: radius.pill,
                             background: pill.bg, color: pill.fg }}>
                {pill.text}
              </span>
            </div>
          );
        })}
      </Card>

      {/* The tail of the run log, as the pipeline flushes it. */}
      {logTail && (
        <Card style={{ marginTop: 18 }}>
          <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.3, color: color.muted,
                        marginBottom: 10 }}>
            {t("ex.run.log")}
          </div>
          {/* Pipeline output, not prose: forced LTR and left-aligned so an RTL interface does not
              reorder log lines that only mean anything in the order and direction they were
              written. */}
          <pre data-testid="ex-progress-log"
               dir="ltr"
               style={{ margin: 0, maxHeight: 220, overflow: "auto", textAlign: "left",
                        fontFamily: font.mono, fontSize: 10.5, lineHeight: 1.6, color: color.sec2,
                        whiteSpace: "pre-wrap", wordBreak: "break-word",
                        background: color.rowAltBg, borderRadius: radius.control,
                        padding: "9px 11px" }}>
            {logTail}
          </pre>
        </Card>
      )}
    </div>
  );
}

/** Known canonical-key prefixes → statement labels for the filter dropdown. */
const STMT_LABELS: Record<string, string> = {
  bs: "Balance sheet", pl: "Profit & loss", cf: "Cash flow", eq: "Changes in equity",
};

export default function ExtractionView() {
  // The extraction is bound to the ACTIVE document, not to an id in the path: the screen is
  // mounted at /extraction (SCREENS.extraction) and /documents/:id only redirects to it, so
  // reading a route param here would leave every visit with no document at all.
  const id = useUI((s) => s.activeDocumentId) ?? undefined;
  const nav = useNavigate();
  const t = useT();
  const outputLocale = useUI((s) => s.locale);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [stmt, setStmt] = useState<string>("all");   // statement filter (by canonical prefix)

  const ontQ = useOntologies();
  const tplQ = useTemplates();
  const selectedTemplateId = useUI((s) => s.selectedTemplateId);
  // The KEY of the selected version — what a rulebook targets. Derived from the one selection rather
  // than stored beside it, so the two cannot drift out of step.
  const selectedKey = activeTemplate(tplQ.data, selectedTemplateId)?.template_key ?? null;
  const ready = ontQ.isFetched && tplQ.isFetched;   // don't POST until the lists settle
  // The rulebook a run DEFAULTS to: the one in force for the template the analyst selected on the
  // Upload screen, and failing that the one in force among everything stored. "In force" is one
  // shared rule (see ontologyInForce) — the copy that used to live here compared versions, which
  // cannot rank two different rulebooks that target the same template.
  //
  // There used to be a middle fallback naming `hkfrs_hk_china_v1` by hand, and a reader who had not
  // picked a template on the Upload screen — a fresh browser, so the common case — fell into it and
  // had the filing read against exactly that: a rulebook the adopted v2 declares it replaces. The
  // run's own record is what exposed it, reporting `superseded` while this screen was still calling
  // it in force. A named key cannot follow an adoption; the shared rule can, so it decides.
  const inForce =
    ontologyInForce(ontQ.data, (o) => o.target_template_key === selectedKey) ||
    ontologyInForce(ontQ.data);
  // …and the one the reader PINNED, which outranks it. Empty means "follow whatever is in force",
  // so publishing a new rulebook moves an unpinned reader forward rather than freezing them.
  //
  // Held in the URL, not in component state. As state it did not survive a reload: the reader was
  // silently moved back to the rulebook in force while still looking at figures the run had produced
  // under the pinned one — the same class of mismatch as the reload blocker, arriving by a different
  // door. The URL also makes the pin shareable, which is what someone comparing two rulebooks on one
  // filing actually needs: `?rulebook=<id>` IS the comparison, and it is the same mechanism
  // `?template=` uses on the Template screen. Deliberately not localStorage — a pin belongs to the
  // thing being read, not to the person reading, and a sticky pin would silently govern the NEXT
  // filing too.
  const [params, setParams] = useSearchParams();
  const pinnedId = params.get("rulebook") ?? "";
  const setPinnedId = useCallback((next: string) => {
    setParams((prev) => {
      const p = new URLSearchParams(prev);
      if (next) p.set("rulebook", next);
      else p.delete("rulebook");
      return p;
      // `replace` so choosing a rulebook does not stack a history entry per click — Back should
      // leave the screen, not walk the reader through every pick they tried.
    }, { replace: true });
  }, [setParams]);
  // A pin naming a rulebook that is no longer served (deleted, or a stale shared link) must not
  // leave the screen blank: fall through to what is in force, which is also what the picker shows.
  const ont = (pinnedId ? ontQ.data?.find((o) => o.id === pinnedId) : undefined) ?? inForce;
  // THE VERSION THE RUN IS LAUNCHED AGAINST, and the reason a re-extraction could not pick up a
  // revised template. `find(tt => tt.template_key === ...)` matched a KEY over a list carrying every
  // version of it and took the first row — v1 on an unordered payload — so the pipeline validated and
  // shaped output against the OLDEST template no matter what had been published or chosen since.
  // Now: the analyst's selection when it belongs to this rulebook's template, else the latest for it
  // (`activeTemplate`, the same rule the Upload screen shows).
  const tpl = ont
    ? activeTemplate(tplQ.data, selectedTemplateId, ont.target_template_key)
    : undefined;
  // `rulebook` is what THIS run recorded — keyed on this document and this choice, so switching the
  // pick cannot leave the previous run's rulebook labelling the new one. `progress`, `stages` and
  // `logTail` come back BESIDE `data` because `data` stays undefined until the run succeeds, which
  // is exactly what used to throw away everything the poll knew about a run in flight.
  //
  // WHICH RUN THE SCREEN IS SHOWING, AND HOW IT GOT IT. An analyst comes here TO run the extraction,
  // so `useExtraction` POSTs once per (document, rulebook, template) and polls it. A reviewer holds
  // `extraction:view` and NOT `pipeline:run` — that POST is a 403 for them — and this screen is in
  // all three working roles' nav, so a role that cannot start a run watches whatever run the
  // per-document read names. A screen that 403s on arrival is not a screen its reader was given.
  const canRun = useCan("pipeline:run");
  // DOES THIS DOCUMENT ALREADY HAVE A RUN IN FLIGHT? A hard reload arrives with an empty query
  // cache, so the start query had nothing and POSTed — a SECOND pipeline over the one already
  // working on the same document, and the reader watching the newer of the two. This read answers
  // without a run id and answers for a run that has produced nothing yet, so the run in flight can
  // be adopted instead of raced.
  const runQ = useDocumentRunStatus(id);
  // ANSWERED THIS VISIT, not out of the cache. The status query holds the previous visit's answer
  // while it refetches, and acting on that is how the duplicate comes straight back: a re-extract
  // started from the Workspace leaves `succeeded` in the cache, and a screen that trusted it would
  // POST over the run that is working right now.
  const answered = runQ.isFetchedAfterMount && runQ.isSuccess;
  const latestRunId = (answered && runQ.data?.run_id) || undefined;
  const inFlight = answered && runQ.data?.status === "running";
  // ADOPTED ONCE AND KEPT — per document, because this screen reads the ACTIVE document from the
  // store and does not remount when that changes. Kept, because the read stops saying `running` the
  // moment the run settles: re-deriving this every render would drop the run just as its spread
  // arrived and re-enable the POST, replacing what the reader waited for with a run of this
  // screen's own. It also records the PIN it was adopted under — see `startBlocked`.
  const [adopted, setAdopted] = useState<{ doc: string; runId: string; pin: string } | null>(null);
  useEffect(() => {
    if (inFlight && latestRunId && id) {
      setAdopted((prev) => (prev && prev.doc === id
        ? prev : { doc: id, runId: latestRunId, pin: pinnedId }));
    }
  }, [inFlight, latestRunId, id, pinnedId]);
  const mine = adopted && adopted.doc === id ? adopted : null;
  // A reader who cannot start a run has no run of their own to wait for, so they watch the latest one
  // whatever state it is in — which is how they see a run at all before it has a result.
  const watching = mine?.runId ?? (canRun ? undefined : latestRunId);
  // WHY THE POST IS HELD, kept separate from WHICH RUN IS WATCHED, because pinning changes one and
  // must not change the other. Choosing another rulebook is the reader asking for this filing read
  // against different rules — a run this screen may start — so the block lifts; the adopted run
  // merely FINISHING is not, so it does not. The screen goes on reporting the run it adopted
  // throughout, and the new pick's run starts when that one ends rather than racing it (`!inFlight`).
  const startBlocked = !!mine && mine.pin === pinnedId;
  // NO ANSWER, NO RUN. A read that has not landed — or that failed — cannot say whether a pipeline
  // is already working on this document, and starting one on that basis is the duplicate above.
  const starting = ready && canRun && answered && !inFlight && !startBlocked;
  const extr = useExtraction(id, ont?.id, tpl?.id, starting, watching);
  const data = extr.data;
  const rulebook = extr.rulebook;
  const progress = extr.progress;
  const stages = extr.stages;
  const logTail = extr.logTail;
  // THE RUN'S OWN STATUS, which is what separates a clock from a duration for the elapsed line
  // below. Read off the run rather than inferred from which panel is showing: the panel condition
  // happens to imply "in flight" today, and a timer that keeps running on a settled run is exactly
  // the bug that inference would hide the next time the condition changes.
  const runStatus = extr.status;
  const stopped = runStatus === "canceled";
  // "none" is the read's own 200 answer — this document has never been extracted — and it is the
  // only thing to say to a reader who cannot start one. It is no longer inferred from a 404, which
  // was the same response the route gave for a run that was working.
  const noRun = !canRun && answered && runQ.data?.status === "none";
  // A READ THAT FAILED IS NOT A RUN THAT FAILED, and the two must not share a panel: `ex.failed`
  // asserts the pipeline broke and offers to start another one, so serving it for a status read that
  // 500d would report a failure nothing observed and invite a second run over one that may be
  // working. Kept apart, each says only what it knows.
  //
  // And it is only worth saying while this screen holds no run: with one being polled, that run's own
  // progress is the better answer to the same question, and hiding it behind "we cannot tell whether
  // a run is in flight" would suppress the report of a run visibly in flight.
  const readFailed = runQ.isError && !extr.runId;
  const isError = extr.isError;
  const error = extr.error;
  // WHAT THE SCREEN IS WAITING ON, and they are different things. For a reader who can start a run,
  // everything before the spread IS the run — the lists settling, the read that decides whether to
  // start one or adopt one, the POST, the pipeline — so one panel covers the lot and says "starting"
  // until the run reports otherwise. For a reader who cannot, only the read saying a run is in flight
  // makes this an extraction in progress; waiting on the read itself is loading, and describing that
  // as a run would attribute one to someone who may have none.
  //
  // Both are exclusive of `data` and of each other: these are branches of "what is there to show",
  // and a panel saying the run is starting must not stack above the spread that arrived.
  const awaitingRun = !data && !isError && !readFailed && !stopped && (canRun || inFlight);
  const awaitingRead = !canRun && !data && !isError && !readFailed && !inFlight && !noRun;
  const failedStage = currentStage(progress);
  const reextract = useReextract(id);
  const stopRun = useStopRun(id);

  // No document is being worked, so there is no extraction to report on — the same greenfield
  // guidance the other pipeline screens show, rather than a blank page.
  if (!id) return <EmptyState />;

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "22px 30px 60px" }}>
      <button onClick={() => nav(SCREENS.upload.path)}
              style={{ fontSize: 12, color: color.indigo, background: "none", border: "none", cursor: "pointer", padding: 0, marginBottom: 12 }}>
        {t("ex.back")}
      </button>

      {/* Above the results, and present while one is still running: which rulebook governs this
          run is a decision, so it belongs where the run is, not only in a file on the server —
          and what the run RECORDED belongs next to the figures it produced. For a reader who
          cannot start a run there is no decision to show, so it appears only once there is a run
          to describe — the alternative is "starting <rulebook>" printed at someone who is starting
          nothing. */}
      {ontQ.data && ontQ.data.length > 0 && (canRun || rulebook) && (
        <RulebookPicker
          rows={ontQ.data} inForce={inForce} chosen={ont} record={rulebook}
          onChoose={setPinnedId} canChoose={canRun}
          templateKey={selectedKey ?? tpl?.template_key} t={t}
        />
      )}

      {/* Nothing to read and no way to start one: said plainly, because the reader's next move is
          to ask someone who can run it rather than to look for a button they do not have. */}
      {noRun && (
        <div data-testid="ex-norun" style={{ marginTop: 6 }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>
            {t("ex.pending.noneTitle")}
          </h1>
          <p style={{ margin: 0, color: color.sec2, fontSize: 12.5, maxWidth: 620,
                      lineHeight: 1.6 }}>
            {t("ex.readOnly.none")}
          </p>
          {/* The read polls only while a run is RUNNING, and there is no run here — so nothing will
              tell this reader when someone else starts one. The way forward is a control that asks
              again, rather than a sentence telling them to reload the browser. */}
          <div style={{ marginTop: 12 }}>
            <Button variant="secondary" testid="ex-recheck"
                    disabled={runQ.isFetching}
                    onClick={() => { void runQ.refetch(); }}
                    style={{ fontSize: 12, padding: "7px 14px" }}>
              {runQ.isFetching ? t("ex.run.retryPending") : t("ex.readOnly.recheck")}
            </Button>
          </div>
        </div>
      )}

      {/* The read that says whether a run is in flight did not answer, so neither "extracting" nor
          "not extracted" can be claimed — and neither can "the extraction failed". What is offered is
          the one thing that helps: ask again. */}
      {readFailed && (
        <div data-testid="ex-run-unknown" style={{ marginTop: 6 }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>
            {t("ex.pending.unknownTitle")}
          </h1>
          <p style={{ margin: 0, color: color.sec2, fontSize: 12.5, maxWidth: 620,
                      lineHeight: 1.6 }}>
            {t("ex.pending.unknownBody")}
          </p>
          <p style={{ fontFamily: font.mono, fontSize: 11, color: color.muted, maxWidth: 620,
                      margin: "6px 0 0", wordBreak: "break-word" }}>
            {refusalText(runQ.error)}
          </p>
          <div style={{ marginTop: 12 }}>
            <Button variant="secondary" testid="ex-run-unknown-recheck"
                    disabled={runQ.isFetching}
                    onClick={() => { void runQ.refetch(); }}
                    style={{ fontSize: 12, padding: "7px 14px" }}>
              {runQ.isFetching ? t("ex.run.retryPending") : t("ex.readOnly.recheck")}
            </Button>
          </div>
        </div>
      )}

      {/* A run — this reader's own, or the one already in flight that this screen adopted rather
          than raced. Before either exists the panel says "starting", because that is all that is
          true. */}
      {awaitingRun && (
        <RunProgress progress={progress} stages={stages} logTail={logTail} t={t}
                     live={runStatus !== "succeeded" && runStatus !== "failed" && runStatus !== "canceled"}
                     canStop={canRun && !!extr.runId && runStatus === "running"}
                     stopping={stopRun.isPending}
                     onStop={() => { if (extr.runId) stopRun.mutate(extr.runId); }} />
      )}
      {stopped && (
        <div data-testid="ex-stopped"
             style={{ padding: "12px 14px", background: color.rowAltBg, color: color.sec2,
                      border: `1px solid ${color.cardBorder}`, borderRadius: 9,
                      fontSize: 12.5, lineHeight: 1.6 }}>
          <b>{t("ex.run.stopped")}.</b>
          {canRun && (
            <div style={{ marginTop: 10 }}>
              <Button variant="primary" testid="ex-rerun-stopped"
                      disabled={reextract.isPending}
                      onClick={() => reextract.mutate({ ontologyId: ont?.id, templateId: tpl?.id })}>
                {reextract.isPending ? t("ex.run.retryPending") : t("ex.run.retry")}
              </Button>
            </div>
          )}
        </div>
      )}
      {/* A fetch, not a run. */}
      {awaitingRead && (
        <div style={{ padding: 50, textAlign: "center", color: color.muted }}>
          {t("empty.loading")}
        </div>
      )}
      {isError && (
        <div data-testid="ex-failed"
             style={{ padding: "12px 14px", background: color.redBg, color: color.redFg,
                      borderRadius: 9, fontSize: 12.5, lineHeight: 1.6 }}>
          <b>{t("ex.failed")}.</b>{" "}
          <span style={{ fontFamily: font.mono, fontSize: 11 }}>{error?.message}</span>
          {/* WHERE it stopped, when the run recorded a stage before failing. Named rather than
              implied: "extraction failed" alone leaves a reader with nothing to act on. */}
          {failedStage && (
            <div data-testid="ex-failed-stage" data-stage={failedStage} style={{ marginTop: 4 }}>
              {t("ex.run.failedAt").replace("{stage}", stageLabel(failedStage, t))}
            </div>
          )}
          {canRun && (
            <div style={{ marginTop: 10 }}>
              {/* Re-running is the only way past a failed run, and `useReextract` starts a FRESH
                  one — the start query is cached with staleTime: Infinity, so asking the screen's
                  own hook again would hand back the run that just failed. */}
              <Button
                testid="ex-retry"
                disabled={reextract.isPending}
                onClick={() => reextract.mutate({ ontologyId: ont?.id, templateId: tpl?.id })}
                style={{ fontSize: 12, padding: "7px 14px" }}
              >
                {reextract.isPending ? t("ex.run.retryPending") : t("ex.run.retry")}
              </Button>
              {reextract.isError && (
                <div data-testid="ex-retry-error" style={{ marginTop: 6, fontSize: 11.5 }}>
                  {t("ex.run.retryFailed")}{" "}
                  <span style={{ fontFamily: font.mono, fontSize: 11 }}>
                    {(reextract.error as Error)?.message}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {data && (() => {
        const res = data.result;
        const u = res.units;
        // The finished run's own elapsed figure. `progress` is the record the run settled with, so
        // this is the run's measurement rather than anything this screen times itself.
        const settledElapsed = fmtElapsed(progress?.elapsed_ms);
        // Statement filter options: the canonical-key prefixes actually present, plus
        // All / Unmapped. Prefix-based so it tracks whatever template the run used.
        const prefixes = Array.from(
          new Set(res.rows.map((r) => r.canonical_key?.split("_")[0]).filter(Boolean) as string[]),
        );
        const shown = res.rows.filter((r) =>
          stmt === "all" ? true
            : stmt === "unmapped" ? !r.canonical_key
              : r.canonical_key?.startsWith(`${stmt}_`));
        return (
        <>
          <div style={{ marginBottom: 14 }}>
            <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 4px" }}>
              {res.entity || t("ex.title")}
            </h1>
            <p style={{ margin: 0, color: color.sec2, fontSize: 12.5 }}>
              <span style={{ fontFamily: font.mono }}>{res.filename}</span>
              {"  ·  "}{res.format.toUpperCase()}
              {"  ·  "}{res.line_item_count} {t("ex.count")}
              {/* HOW LONG THE RUN TOOK, once it is over. The progress panel carries this while a
                  run is in flight and that panel is gone the moment `data` arrives (`awaitingRun`),
                  so a finished extraction reported its duration nowhere — on the screen whose job is
                  to account for the run. Read off the run's OWN final progress record, the same
                  field the live gauge read, so the two cannot disagree; absent for a run stored
                  before that contract existed, and then simply not printed. */}
              {settledElapsed && <>{"  ·  "}{t("ex.run.elapsed")} {settledElapsed}</>}
              {u?.currency && <>{"  ·  "}<b>{u.currency}</b></>}
              {u?.units_label && <>{"  ·  "}{t("ex.inUnits")} {u.units_label}</>}
            </p>
            {/* RE-EXTRACT A RUN THAT SUCCEEDED, which until now there was no way to do.
                `useReextract` existed and already sent `force: true`, but it was rendered ONLY
                inside the failure branch — so a document whose run had SUCCEEDED offered no
                control at all, and POSTing an extraction is idempotent per (document, template,
                rulebook): the request came back `adopted: true` carrying the very run the reader
                was trying to replace. The only way past it was to upload the file again under a
                new document id.
                It belongs here rather than beside the retry button because the two mean different
                things — that one is "this run failed, try again", this one is "this run is fine
                and I want a newer one, on today's code or today's rulebook". */}
            {canRun && (
              <div style={{ marginTop: 10 }}>
                <Button
                  testid="ex-reextract"
                  variant="ghost"
                  disabled={reextract.isPending}
                  onClick={() => reextract.mutate({ ontologyId: ont?.id, templateId: tpl?.id })}
                  style={{ fontSize: 12, padding: "6px 12px" }}
                >
                  {reextract.isPending ? t("ex.run.retryPending") : t("ex.run.reextract")}
                </Button>
                {reextract.isError && (
                  <div data-testid="ex-reextract-error" style={{ marginTop: 6, fontSize: 11.5 }}>
                    {t("ex.run.retryFailed")}{" "}
                    <span style={{ fontFamily: font.mono, fontSize: 11 }}>
                      {(reextract.error as Error)?.message}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* How mapping ran. A deterministic-only run (no LLM configured) is materially
              weaker at resolving captions by meaning, so it is stated rather than implied. */}
          {res.mapping && res.mapping.strategy !== "llm_description" && (
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap",
                          background: color.amberBg, border: `1px solid ${color.amberFg}33`,
                          borderRadius: 8, padding: "8px 11px", marginBottom: 12 }}>
              <b style={{ fontSize: 11.5, color: color.amberFg }}>{t("ex.detMapping")}</b>
              <span style={{ fontSize: 11.5, color: color.sec2 }}>{t("ex.detMappingHint")}</span>
              {res.mapping.reason && (
                <span style={{ fontFamily: font.mono, fontSize: 10.5, color: color.muted }}>
                  ({res.mapping.reason})
                </span>
              )}
            </div>
          )}

          {/* Statement filter */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <span style={{ fontSize: 11, color: color.muted }}>{t("ex.statement")}</span>
            <select value={stmt} onChange={(e) => setStmt(e.target.value)}
                    style={{ fontSize: 12, padding: "5px 9px", borderRadius: 7,
                             border: `1px solid ${color.controlBorder}`, background: "#fff", color: color.ink }}>
              <option value="all">{t("ex.allStatements")}</option>
              {prefixes.map((p) => (
                <option key={p} value={p}>{STMT_LABELS[p] || p.toUpperCase()}</option>
              ))}
              <option value="unmapped">{t("ex.unmapped")}</option>
            </select>
            <span style={{ fontSize: 11, color: color.muted2 }}>{shown.length} / {res.rows.length}</span>
          </div>

          <div style={{ display: "flex", gap: 18, alignItems: "flex-start" }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <Card pad={0} style={{ overflow: "hidden" }}>
                <div style={{ display: "grid", gridTemplateColumns: GRID, gap: 10, padding: "10px 14px",
                              background: color.rowAltBg, borderBottom: `1px solid ${color.hairline2}`,
                              fontSize: 10, fontWeight: 600, letterSpacing: 0.3, color: color.muted }}>
                  <span>{t("ex.col.lineitem")}</span>
                  <span>{t("ex.col.note")}</span>
                  <span>{t("ex.col.value")} · {t("ex.col.source")}</span>
                  <span>{t("ex.col.mapping")}</span>
                </div>
                {shown.length === 0 && (
                  <div style={{ padding: "18px 14px", fontSize: 12.5, color: color.muted }}>{t("ex.empty")}</div>
                )}
                {shown.map((row, i) => (
                  <RowLine key={i} row={row} t={t} onPick={setPicked}
                           loc={groupingLocale(u?.currency)} />
                ))}
              </Card>
            </div>
            {res.format === "pdf" && id && (
              <PagedSource documentId={id} pageCount={res.page_count ?? 1}
                           picked={picked?.kind === "pdf" ? picked : null} t={t} />
            )}
            {(res.format === "xlsx" || res.format === "xls") && id && (
              <ExcelSourcePanel documentId={id} picked={picked?.kind === "xlsx" ? picked : null} t={t} />
            )}
          </div>
          {id && <AnalysisSection id={id} locale={outputLocale} t={t} />}
        </>
        );
      })()}
    </div>
  );
}
