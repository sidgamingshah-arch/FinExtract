/** Screen 4 — Workspace. The core extraction screen: source viewer (left) + editable output panel
 * (right) with the cell inspector along its bottom edge. Full-height flex layout, not a padded
 * page. Mirrors wireframe scrExtract + OUTPUT + SELINFO verbatim, data-driven from useStatement.
 *
 * Selecting a figure describes it in the inspector; "Edit value" turns the inspector into the
 * editor for both periods, with the comment recording why. Everything the inspector says — the
 * origin, the arithmetic, each contribution's page, the comment — is resolved for the period on
 * show, not for the row, because last year's figure is its own number with its own provenance. */
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useNavigate } from "react-router-dom";

import { Button, ConfidencePill, NoteChip, Segmented, StatusIcon, confReadout } from "../components/ui";
import { EmptyState } from "../components/EmptyState";
import { ExcelGrid, PageStack, toPicked, type Picked } from "../components/SourceViewer";
import { color, confStyle, font, layout, radius, shadow, fmtIN, fmtPlain, parseAccounting } from "../theme";
import { DERIVED_STATEMENTS } from "../types";
import type { Basis, FxRateResolution, StatementColumn, StatementKey, StatementResponse, StatementRow, SupersededTemplate } from "../types";
import { ApiError, refusalText } from "../lib/api";
import { activeTemplate, ontologyInForce, useDocumentRun, useDocumentRunStatus, useDocumentRuns, useDocumentStatement, useEditDocumentLineItem, useFxRateResolution, useOntologies, useReextract, useRevertDocumentLineItem, useStatement, useEditLineItem, useProjectLoaded, useTemplates } from "../lib/queries";
import { useCan } from "../lib/rbac";
import { useUI } from "../store";
import { useT } from "../i18n";
import { SCREENS } from "./config";

/* Very light vertical column divider for the output grid (subtle table gridlines). */
const COL_DIVIDER = "rgba(37,45,60,0.07)";
const colDiv: CSSProperties = { borderLeft: `1px solid ${COL_DIVIDER}` };

/* ---- toolbar labelled <select> chip (interactive; e.g. statement / units / currency) ---- */
function ToolSelect<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        color: color.sec,
        border: `1px solid ${color.cardBorder}`,
        borderRadius: radius.control,
        padding: "5px 9px 5px 11px",
      }}
    >
      <span style={{ color: color.muted }}>{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        style={{
          fontSize: 12,
          fontWeight: 600,
          fontFamily: font.sans,
          color: color.ink,
          border: "none",
          outline: "none",
          background: "transparent",
          cursor: "pointer",
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/* ---- one zoom step in the viewer's dark header chrome ----
 * Disabled at the end of the range: a control that cannot move must not look pressable. The title
 * doubles as the accessible name, because the glyph (− / +) names nothing on its own. */
function ZoomButton({ label, title, disabled, onClick }: {
  label: string; title: string; disabled: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      style={{
        fontSize: 12, lineHeight: 1, fontWeight: 600, width: 18, height: 18,
        display: "flex", alignItems: "center", justifyContent: "center",
        color: "#dfe3e9", background: "transparent", border: "none", padding: 0,
        cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.4 : 1,
      }}
    >
      {label}
    </button>
  );
}

/* ---- left source-viewer "paper" row ---- */
function PaperRow({ row, selected }: { row: StatementRow; selected: boolean }) {
  const k = row.kind;
  const isHead = k === "section" || k === "total";
  const isBold = isHead || k === "subtotal";
  const wt = isBold ? 700 : k === "subhead" ? 600 : 400;
  const fg = isHead ? "#111" : k === "subhead" ? "#333" : "#444";
  const showV = k === "item" || k === "subtotal" || k === "total";
  const v1 = showV ? fmtIN(row.v1) : "";
  const v2 = showV ? fmtIN(row.v2) : "";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 78px 78px",
        gap: 4,
        padding: selected ? "3px 6px" : "2px 0",
        background: selected ? color.indigoTint2 : "transparent",
        borderRadius: selected ? 4 : 0,
        margin: selected ? "2px -6px" : 0,
        position: "relative",
      }}
    >
      {selected && (
        <span
          style={{
            position: "absolute",
            left: -10,
            top: 0,
            bottom: 0,
            width: 3,
            background: color.indigo,
            borderRadius: 2,
          }}
        />
      )}
      <span style={{ fontWeight: wt, color: fg, paddingLeft: k === "item" ? 14 : 0 }}>
        {row.source_label ?? row.label}
      </span>
      <span style={{ textAlign: "right", fontFamily: font.mono, fontWeight: wt, color: fg }}>{v1}</span>
      <span style={{ textAlign: "right", fontFamily: font.mono, color: "#888" }}>{v2}</span>
    </div>
  );
}

/* Units presentation (display only; the raw value is untouched for editing/formulas). Values
 * are stored AS REPORTED, i.e. already in the source's magnitude (`srcScale`, e.g. 1000 for a
 * statement stated "in thousands"). Converting to a target magnitude therefore scales by
 * srcScale/target — never a naive divide, which would double-scale an already-scaled figure. */
type UnitTarget = "as_reported" | "thousands" | "millions" | "billions";
const TARGET_SCALE: Record<Exclude<UnitTarget, "as_reported">, number> = {
  thousands: 1e3,
  millions: 1e6,
  billions: 1e9,
};

/* Currencies offered for presentation. Rates come from the admin-maintained FX master
 * (/fx-rates) — never typed here and never guessed — so a re-currencied figure is always
 * shown with the rate, its as-of date, and a "derived" marker when the master only held the
 * opposite direction. When the master has no rate for the pair we do NOT convert: presenting
 * source figures under a target-currency label is the failure mode this replaces.
 * The document's own currency stays the default (no conversion at all). */
const CURRENCIES = ["USD", "EUR", "GBP", "INR", "CNY", "HKD", "JPY", "SGD", "AUD", "CAD"];

/* Zoom levels for the live PDF viewer. Discrete steps rather than free scaling so the control can
 * be honestly disabled at each end of the range — and so the percentage it reports is always one
 * of these, never a rounding of an arbitrary float. */
const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2];

/** The master's answer, but only when it actually carries a rate for the pair being shown.
 * Returning the resolved variant (or null) rather than the whole union makes the "no rate
 * configured" answer structurally unusable as a multiplier — the caller cannot accidentally
 * convert with it. */
type ResolvedFx = Extract<FxRateResolution, { resolved: true }>;
function appliedRate(res: FxRateResolution | undefined, converting: boolean): ResolvedFx | null {
  return converting && res !== undefined && res.resolved ? res : null;
}

/* A derived (inverted) rate is a repeating decimal carried at full precision — 20 digits of
 * it would swamp the caption. Show 6 significant digits there; the multiplier itself still
 * uses the full value the master returned, and the caption's tooltip carries it verbatim. */
function fmtRate(rate: string): string {
  const n = Number(rate);
  if (!Number.isFinite(n) || n === 0) return rate;
  return Number(n.toPrecision(6)).toString();
}

/** A rejected edit, in words the analyst can act on. The server's own `detail` is preferred —
 *  it names the concept, the basis or the bad formula — and only falls back to the status code
 *  when there is nothing better, because "something went wrong" is what silence already said. */
function editErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.detail) return err.detail;
    // A structured detail (e.g. {"error":"bad_formula","message":…}) travels in the body text.
    const m = /"message"\s*:\s*"([^"]+)"/.exec(err.message);
    if (m) return m[1];
    if (err.status === 403) return "Your role cannot edit extracted values.";
    if (err.status === 404) return "This line is not part of the extraction or its template.";
    return `The server refused the edit (${err.status}).`;
  }
  return err instanceof Error ? err.message : "The edit could not be saved.";
}

/* Accounting formatter with an explicit decimal count (fmtIN forces 0dp). */
function fmtDec(n: number, dp: number): string {
  const s = Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  return n < 0 ? `(${s})` : s;
}

function presentValue(raw: number | null, srcScale: number, target: UnitTarget): string {
  if (raw == null) return "";
  if (target === "as_reported") return fmtIN(raw);
  const scaled = raw * (srcScale / TARGET_SCALE[target]);
  // 0dp for large magnitudes, else 2dp — and never collapse a genuinely nonzero figure to 0.
  let dp = Math.abs(scaled) >= 1000 ? 0 : 2;
  while (scaled !== 0 && Number(scaled.toFixed(dp)) === 0 && dp < 6) dp += 2;
  return fmtDec(scaled, dp);
}

/* ---- matrix statement grid (equity components as columns, movements as rows) ----
 *
 * A statement of changes in equity is not a two-column comparative: its columns are equity
 * components (issued capital, each reserve, retained profits, non-controlling interests, total
 * equity) and its rows are movements through the year. There can be a dozen or more columns, so
 * the table scrolls horizontally with the caption column pinned — the caption is what makes any
 * cell readable, and losing it while scrolling makes the figures meaningless. */
function MatrixGrid({
  columns,
  rows,
  sel,
  present,
  linkable,
  onSelect,
}: {
  columns: StatementColumn[];
  rows: StatementRow[];
  sel: string;
  present: (raw: number | null) => string;
  linkable: boolean;
  onSelect: (id: string) => void;
}) {
  const LABEL_W = 300;
  const CELL_W = 118;
  const cell: CSSProperties = {
    ...colDiv,
    width: CELL_W,
    flex: "0 0 auto",
    padding: "0 10px",
    display: "flex",
    alignItems: "center",
    justifyContent: "flex-end",
    fontFamily: font.mono,
    fontSize: 11.5,
  };
  const pinned: CSSProperties = {
    width: LABEL_W,
    flex: "0 0 auto",
    position: "sticky",
    left: 0,
    zIndex: 1,
    padding: "0 12px",
    display: "flex",
    alignItems: "center",
  };

  return (
    <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
      <div style={{ display: "inline-block", minWidth: "100%" }}>
        {/* column header */}
        <div
          style={{
            display: "flex",
            height: 46,
            position: "sticky",
            top: 0,
            zIndex: 2,
            background: "#f7f8fa",
            borderBottom: `1px solid ${color.cardBorder}`,
          }}
        >
          <div style={{ ...pinned, background: "#f7f8fa", fontSize: 11, fontWeight: 700,
                        color: color.sec2, textTransform: "uppercase", letterSpacing: 0.3 }}>
            Movement
          </div>
          {columns.map((c) => (
            <div
              key={c.key}
              title={c.label}
              style={{ ...cell, alignItems: "flex-end", paddingBottom: 6, fontFamily: font.sans,
                       fontSize: 10.5, fontWeight: 700, color: color.sec2, textAlign: "right",
                       lineHeight: 1.25 }}
            >
              <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis",
                             whiteSpace: "normal", maxHeight: 28 }}>
                {c.label}
              </span>
            </div>
          ))}
        </div>

        {rows.map((r) => {
          const isBalance = r.kind === "subtotal" || r.kind === "total";
          const selected = r.id === sel;
          const jump = linkable && !!r.source && toPicked(r.source, r.label) !== null;
          const bg = selected ? color.indigoTint3 : isBalance ? color.rowAltBg : color.surface;
          return (
            <div
              key={r.id}
              onClick={() => onSelect(r.id)}
              title={jump ? "Click to show this row in the source document" : undefined}
              style={{
                display: "flex",
                height: 34,
                borderBottom: `1px solid ${color.hairline}`,
                background: bg,
                cursor: "pointer",
              }}
            >
              <div style={{ ...pinned, background: bg }}>
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: isBalance ? 600 : 400,
                    color: color.ink,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    borderBottom: jump ? `1px dashed ${selected ? color.indigo : color.dashed}` : "none",
                  }}
                >
                  {r.label}
                </span>
              </div>
              {columns.map((c) => {
                const v = r.cells?.[c.key] ?? null;
                return (
                  <div
                    key={c.key}
                    style={{
                      ...cell,
                      fontWeight: isBalance ? 600 : 400,
                      // An empty cell is meaningful here: the movement did not touch that
                      // component. Showing a dash rather than nothing says so.
                      color: v === null ? color.faint : color.ink,
                    }}
                  >
                    {v === null ? "–" : present(v)}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---- where a displayed figure came from ----
 *
 * A calculated line shows the figure its components come to, not the one printed on the page, so
 * the grid has to say which it is looking at. Silence here would make a computed subtotal
 * indistinguishable from an extracted one — and it is the difference between a number the
 * document asserts and a number this spread asserts. */
type Origin = NonNullable<StatementRow["origin"]>;
const ORIGIN_CHIP: Record<Origin, { label: string; bg: string; fg: string; help: string }> = {
  extracted: { label: "", bg: "", fg: "", help: "" },      // the ordinary case: no chip
  calculated: { label: "ƒ", bg: color.indigoTint2, fg: color.indigo,
                help: "Computed from this line's components, not read off the page" },
  manual: { label: "✎", bg: color.amberBg, fg: color.amberFg,
            help: "A value entered by hand, which overrides both the document and the components" },
  reported_uncomputed: { label: "!", bg: color.redBg, fg: color.redFg,
                         help: "Printed in the document but not verifiable — none of this line's "
                               + "components were extracted" },
};

/* ---- printed on the face, or inside a note ----
 *
 * Rendered only for the NOTE case, the same way OriginChip is rendered only for the unusual
 * origins: a filing's statement rows are overwhelmingly face rows, and a chip on every one of them
 * would cost every label the width of a cell that already ellipsises. The absence therefore means
 * face, and the inspector says which it is IN WORDS for the row on show — so the reader never has
 * to infer it from a missing chip.
 *
 * It has to be said at all because a note's detail lines sum to a figure the face already reports:
 * a reader adding a face row and its note's lines counts the filing's money twice. */
function PrintedInChip({ printedIn }: { printedIn?: StatementRow["printed_in"] }) {
  if (printedIn !== "notes") return null;
  return (
    <span
      title="Printed inside a note, not on the face of the statement"
      data-testid="printed-in-notes"
      style={{ fontSize: 9.5, fontWeight: 700, lineHeight: 1, padding: "3px 5px",
               borderRadius: 4, background: color.amberBg, color: color.amberFg,
               fontFamily: font.mono, flex: "0 0 auto" }}
    >
      note
    </span>
  );
}

function OriginChip({ origin }: { origin?: Origin }) {
  if (!origin || origin === "extracted") return null;
  const c = ORIGIN_CHIP[origin];
  return (
    <span
      title={c.help}
      data-testid={`origin-${origin}`}
      style={{ fontSize: 9.5, fontWeight: 700, lineHeight: 1, padding: "3px 5px",
               borderRadius: 4, background: c.bg, color: c.fg, fontFamily: font.mono,
               flex: "0 0 auto" }}
    >
      {c.label}
    </span>
  );
}

/* ---- right output-panel row ---- */
/** Which note a printed reference actually OPENS, out of the notes this row resolved to.
 *
 * The chip shows what the page printed ("16(b)"); the note that exists may be the parent table
 * ("16"), and only the notes in `row.notes` can be opened at all — the backend put them there
 * having already applied the exact-then-parent rule (`LineItem.cited_notes_among`). This picks
 * which of the row's own resolved notes a given printed chip corresponds to, and it does NOT
 * re-derive that rule: exact match, else the reference's leading number if the row resolved it,
 * else the reference unchanged so the failure is visible rather than silently redirected.
 */
function openable(ref: string, row: StatementRow): string {
  const notes = row.notes || [];
  if (notes.includes(ref)) return ref;
  const base = ref.match(/^\d+/)?.[0];
  return base && notes.includes(base) ? base : ref;
}


function OutputRow({
  row,
  sel,
  present,
  linkable,
  onSelect,
  onOpenNote,
}: {
  row: StatementRow;
  sel: string;
  present: (raw: number | null) => string;
  linkable: boolean;   // real doc whose value resolves to a source location
  /** Selecting a row also drives the source viewer — to the period whose figure was clicked. */
  onSelect: (id: string, period?: "current" | "prior") => void;
  onOpenNote: (ref: string) => void;
}) {
  const k = row.kind;
  const isSection = k === "section";
  const isSub = k === "subhead";
  const isSt = k === "subtotal";
  const isTot = k === "total";
  const isItem = k === "item";
  const selected = row.id === sel;

  let bg: string = color.surface;
  if (isSection) bg = "#f2f4f8";
  else if (isSt) bg = color.rowAltBg;
  else if (isTot) bg = color.indigoTint2;
  if (selected && isItem) bg = color.indigoTint3;

  const ind = isSection ? 0 : isSub ? 4 : row.level ? 26 : 14;
  const h = isSection ? 32 : 34;
  const wt = isSection || isTot ? 700 : isSub || isSt ? 600 : 400;
  const fg = isSection ? color.viewerBg : isSub ? color.sec2 : color.ink;
  const vwt = isSt || isTot ? 600 : selected ? 600 : 400;
  const vfg = isTot ? color.indigo : color.ink;
  const showV = isItem || isSt || isTot;
  // A row that carries its own formatted figures (a KPI's 1.35× / 12.4% / 45 days) renders them
  // verbatim: they are not amounts, so the currency/magnitude presentation must not touch them.
  const v1 = showV ? (row.display1 ?? present(row.v1)) : "";
  const v2 = showV ? (row.display2 ?? present(row.v2)) : "";
  // One NOTE column, but keep BOTH references when a row cites different notes per period
  // (e.g. note "10" current, "10a" prior) — collapsing to one would drop the second linkage.
  const noteRefs = [row.note, row.note2 && row.note2 !== row.note ? row.note2 : null].filter(
    (n): n is string => !!n,
  );
  // The value itself is the hyperlink: clicking a figure selects the row and drives the live
  // viewer to THAT figure's page+bbox (or cell). Each period is decorated independently — last
  // year's number is printed in its own column, often on its own page, and a reviewer checks it
  // just as much as this year's, so linking only the current column left half the grid dead.
  const links1 = linkable && isItem && !!row.source && toPicked(row.source, row.label) !== null;
  const links2 = linkable && isItem && !!row.source2 && toPicked(row.source2, row.label) !== null;

  /** One figure cell. Clicking it selects the row — which follows the figure to the page it was
   *  printed on and describes it in the inspector below, where it is also edited. */
  function valueCell(period: "current" | "prior", text: string, links: boolean,
                     weight: number, fg: string) {
    const note = row.comments?.[period]?.text;
    return (
      <span
        data-testid={showV ? `${period === "current" ? "v1" : "v2"}-${row.id}` : undefined}
        onClick={showV ? (e) => { e.stopPropagation(); onSelect(row.id, period); } : undefined}
        title={[links ? (period === "current"
                          ? "Click to show this figure in the source document"
                          : "Click to show last year's figure in the source document") : "",
                note ? `Note: ${note}` : ""].filter(Boolean).join(" · ") || undefined}
        style={{ ...colDiv, display: "flex", alignItems: "center", justifyContent: "flex-end",
                 gap: 4, fontFamily: font.mono, fontSize: 12, fontWeight: weight,
                 color: selected && links ? color.indigo : fg,
                 cursor: links ? "pointer" : "default" }}
      >
        {/* A figure carrying a reason why it was overridden says so where the figure is. */}
        {note && <span style={{ color: color.amberFg, fontSize: 10 }}>✎</span>}
        <span
          style={links
            ? { borderBottom: `1px dashed ${selected ? color.indigo : color.dashed}` }
            : undefined}
        >
          {text}
        </span>
      </span>
    );
  }

  return (
    <div
      onClick={() => onSelect(row.id)}
      style={{
        display: "grid",
        gridTemplateColumns: layout.gridCols,
        alignItems: "stretch",
        padding: "0 16px",
        height: h,
        borderBottom: `1px solid ${color.hairline}`,
        cursor: "pointer",
        background: bg,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 7, paddingLeft: ind, minWidth: 0 }}>
        {isSection && <span style={{ fontSize: 9, color: color.faint }}>▾</span>}
        <span
          style={{
            fontSize: 12.5,
            fontWeight: wt,
            color: fg,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {row.label}
        </span>
        <OriginChip origin={row.origin} />
        <PrintedInChip printedIn={row.printed_in} />
        <StatusIcon status={row.status} />
      </div>
      <div style={{ ...colDiv, display: "flex", alignItems: "center", justifyContent: "center",
                    gap: 4, flexWrap: "wrap" }}>
        {noteRefs.map((n) => (
          <NoteChip key={n} onClick={(e) => { e?.stopPropagation(); onOpenNote(openable(n, row)); }}>
            {n}
          </NoteChip>
        ))}
      </div>
      {valueCell("current", v1, links1, vwt, vfg)}
      {valueCell("prior", v2, links2, isSt || isTot ? 600 : 400, color.muted)}
      <div style={{ ...colDiv, display: "flex", alignItems: "center", justifyContent: "flex-end" }}>
        {/* The CONF. column prints the row's OWN served percentage. The pill used to render the
            CATEGORY's representative literal, so a row served `{cat:'low', pct:41}` displayed
            "54%" — a number derived from the bucket, not from the mapping it labels. */}
        {row.confidence
          ? <ConfidencePill cat={row.confidence.cat} pct={row.confidence.pct} testid="ws-conf" />
          : null}
      </div>
    </div>
  );
}

/* ---- inspector edit-mode body (local input state, remounts per selection) ----
 *
 * Both periods are editable, and the save only closes the editor when the server accepted it: a
 * rejected edit — a wrong basis, an unknown concept, a bad formula — must not look like a saved
 * one, with the figure on screen simply never changing and nothing saying why.
 *
 * The comment belongs to ONE period (the one the inspector is describing), because the reason last
 * year's figure was restated is not the reason this year's was. */
type PeriodEdit = { period: "current" | "prior"; value: number | null };

function InspectorEditor({
  row,
  periods,
  period,
  saving,
  error,
  onSave,
  onCancel,
}: {
  row: StatementRow;
  periods: string[];
  /** The period the comment is filed against, and the one a formula-only save recomputes. */
  period: "current" | "prior";
  saving: boolean;
  error: string | null;
  onSave: (edits: PeriodEdit[], formula: string, comment: string) => void;
  onCancel: () => void;
}) {
  // A CALCULATED line has no stored expression — `row.formula` is empty and the rollup is shown as
  // `arithmetic`, which is a rendering of figures ("100 + 50"), not an expression. Seeding the box
  // from it would send it back on save for the server to evaluate, and a computed result outranks
  // a typed value: the analyst's 200 would come back as 150.
  const [formula, setFormula] = useState(row.formula ?? "");
  const [v1, setV1] = useState(fmtPlain(row.v1));
  const [v2, setV2] = useState(fmtPlain(row.v2));
  const existingNote = row.comments?.[period]?.text ?? "";
  const [note, setNote] = useState(existingNote);

  useEffect(() => {
    setFormula(row.formula ?? "");
    setV1(fmtPlain(row.v1));
    setV2(fmtPlain(row.v2));
  }, [row.id, row.formula, row.v1, row.v2]);
  useEffect(() => { setNote(row.comments?.[period]?.text ?? ""); },
            [row.id, period, row.comments]);

  const commit = () => {
    // Only the columns actually retyped are sent. Sending both would restate last year's figure
    // as a manual value every time this year's is corrected, quietly detaching it from the page.
    const edits: PeriodEdit[] = [];
    if (v1 !== fmtPlain(row.v1)) edits.push({ period: "current", value: parseAccounting(v1) });
    if (v2 !== fmtPlain(row.v2)) edits.push({ period: "prior", value: parseAccounting(v2) });
    onSave(edits, formula, note);
  };

  const numInput = (val: string, set: (s: string) => void, testId: string) => (
    <input
      value={val}
      spellCheck={false}
      data-testid={testId}
      onChange={(e) => set(e.target.value)}
      style={{
        width: 120,
        fontFamily: font.mono,
        fontSize: 12,
        textAlign: "right",
        border: `1px solid ${color.controlBorder}`,
        borderRadius: 6,
        padding: "6px 9px",
        outline: "none",
      }}
    />
  );

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: color.surface,
          border: `1px solid ${color.indigo}`,
          borderRadius: radius.control,
          padding: "8px 11px",
          boxShadow: shadow.focusRing,
        }}
      >
        <span style={{ fontFamily: font.mono, fontSize: 12, color: color.amberFg, fontWeight: 600 }}>ƒx</span>
        <input
          value={formula}
          spellCheck={false}
          data-testid="edit-formula"
          placeholder="=bs_current_assets__cash + …"
          onChange={(e) => setFormula(e.target.value)}
          style={{
            fontFamily: font.mono,
            fontSize: 12,
            color: color.ink,
            flex: 1,
            border: "none",
            outline: "none",
            background: "transparent",
          }}
        />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 9, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: color.muted }}>{periods[0] || "Current"}</span>
        {numInput(v1, setV1, "edit-v1")}
        <span style={{ fontSize: 11, color: color.muted }}>{periods[1] || "Prior"}</span>
        {numInput(v2, setV2, "edit-v2")}
        <button
          onClick={commit}
          disabled={saving}
          data-testid="edit-save"
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: "#fff",
            background: saving ? color.muted : color.indigo,
            border: "none",
            borderRadius: radius.controlSm,
            padding: "7px 15px",
            cursor: saving ? "default" : "pointer",
          }}
        >
          {saving ? "Saving…" : "Save edit"}
        </button>
        <button
          onClick={onCancel}
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: color.ink2,
            background: "#fff",
            border: `1px solid ${color.controlBorder}`,
            borderRadius: radius.controlSm,
            padding: "7px 13px",
            cursor: "pointer",
          }}
        >
          Cancel
        </button>
        <span style={{ fontSize: 11, color: color.muted, flex: 1, minWidth: 200 }}>
          Enter a number in either period, or a formula referencing other line items — the formula
          is stored with the cell. Only the columns you change are saved.
        </span>
      </div>
      {/* why the figure was changed — saved with the edit, against the period named above */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginTop: 9 }}>
        <span style={{ fontSize: 11, color: color.muted, paddingTop: 7, whiteSpace: "nowrap" }}>
          Comment
        </span>
        <textarea
          value={note}
          data-testid="edit-comment"
          onChange={(e) => setNote(e.target.value)}
          placeholder={`Why is this figure being changed? Saved against `
                       + `${period === "current" ? periods[0] || "the current period"
                                                 : periods[1] || "the prior period"}`
                       + ` and carried into the export.`}
          rows={2}
          style={{ flex: 1, minWidth: 0, boxSizing: "border-box", fontSize: 11.5,
                   fontFamily: font.sans, color: color.ink, resize: "vertical",
                   border: `1px solid ${color.controlBorder}`, borderRadius: radius.control,
                   padding: "6px 9px", outline: "none", lineHeight: 1.5 }}
        />
      </div>
      {error && (
        <div
          data-testid="edit-error"
          style={{ marginTop: 9, padding: "7px 11px", background: color.redBg,
                   border: `1px solid ${color.redFg}22`, borderRadius: radius.control,
                   fontSize: 11.5, color: color.redFg, lineHeight: 1.5 }}
        >
          <strong style={{ fontWeight: 600 }}>Not saved.</strong> {error}
        </div>
      )}
    </>
  );
}

/** The rulebook and template a fresh run defaults to: the one in force for the template selected on
 *  the Upload screen, and failing that the one in force among everything stored — decided by the ONE
 *  shared rule (`ontologyInForce`), never by a version comparison of this screen's own.
 *
 *  Launching a run needs the pair because a POST that names neither records `engine_default` and maps
 *  against no rulebook at all — a spread offered as "rebuilt against the current template" would come
 *  back with nothing mapped to it. */
function useRunDefaults() {
  const ontQ = useOntologies();
  const tplQ = useTemplates();
  const selectedTemplateId = useUI((s) => s.selectedTemplateId);
  const selectedKey = activeTemplate(tplQ.data, selectedTemplateId)?.template_key ?? null;
  const ont = ontologyInForce(ontQ.data, (o) => o.target_template_key === selectedKey)
    ?? ontologyInForce(ontQ.data);
  // The version a RE-EXTRACT should use: the analyst's selection when it belongs to this rulebook's
  // template, else the latest. The comment here used to say the list "serves the CURRENT version per
  // key" — it does not, it serves every version, and `find` by key took the first, i.e. the oldest.
  // So the one action whose entire purpose is "rebuild against the current template" was rebuilding
  // against v1. `activeTemplate` is the same rule the Upload and extraction screens use.
  const tpl = ont
    ? activeTemplate(tplQ.data, selectedTemplateId, ont.target_template_key)
    : undefined;
  return { ont, tpl };
}

/* ---- a document that is uploaded but has no spread to show yet ----
 *
 * This branch rendered <EmptyState /> — "No project yet … Upload a source document on the Documents
 * screen to start an extraction. This workspace stays empty until a document is processed" — WHILE A
 * REAL EXTRACTION WAS MID-FLIGHT: `GET /documents/{id}/statement` 404s until a run has produced a
 * RESULT, so a running job and an account with nothing in it were the same screen, and the sentence
 * was false in the one case a reader most needed the truth. The genuine greenfield state still
 * belongs to <EmptyState /> and is decided by the caller (no active document at all).
 *
 * What this can say it asks the DOCUMENT, not a run id it may not have. It used to read the run the
 * Extraction screen started — that screen's start key, with the start disabled, because opening the
 * Workspace must never POST a run of its own just by being looked at — and after a hard reload there
 * is no cached run to read, so a live pipeline fell through to "nothing has been extracted yet". The
 * one situation this component exists for was the one it got wrong. `useDocumentRunStatus` needs no
 * run id and answers before a run has a result, which is what makes a fresh page load correct; it
 * stays a READ, so the Workspace still starts nothing.
 *
 * No stage, percentage or elapsed time is repeated here. The Extraction screen derives those from
 * the run once (`RunProgress`), and a second derivation is a second chance to disagree with it;
 * this states WHICH situation holds and links to the screen that reports it. */
function AwaitingExtraction({ documentId, statementError, reloadStatement, t }: {
  documentId: string | undefined;
  statementError: unknown;
  /** Ask for the spread again. The statement query does not retry (`retry: false`), so without
   *  this the reader would sit on "the spread appears here when the run finishes" after it had. */
  reloadStatement: () => void;
  t: (k: string) => string;
}) {
  const navigate = useNavigate();
  const runQ = useDocumentRunStatus(documentId);
  // ANSWERED THIS VISIT. The cache holds the previous visit's answer while the read refetches, and
  // an analyst who starts a run and walks over here would be shown that one — "not extracted yet"
  // over their own working pipeline, which is the whole defect on a shorter clock.
  const answered = runQ.isFetchedAfterMount && runQ.isSuccess;
  const status = answered ? runQ.data?.status : undefined;

  // The poll is what learns the run finished, so it is what asks for the spread. The statement is
  // fetched once and never retried, so a run that succeeded while the reader waited here would
  // otherwise leave this state on screen indefinitely under a promise it had already kept.
  const succeeded = status === "succeeded";
  useEffect(() => { if (succeeded) reloadStatement(); }, [succeeded, reloadStatement]);

  // A statement that failed for any reason other than "not extracted yet" is its own fact, and
  // saying "not extracted" over a 403 or a 500 would send the reader to run an extraction that is
  // not what is wrong.
  const notExtracted = statementError instanceof ApiError && statementError.status === 404;
  const state = !notExtracted ? "error"
    // A read that FAILED knows nothing about the run, and falling back to "none" would guess the one
    // answer that reads as "nothing happened". The failure is stated instead.
    : runQ.isError ? "unknown"
      // NOTHING HAS ANSWERED YET, and "not extracted yet" is the sentence that must not be printed in
      // that gap: it is the wrong answer for a run in flight, and printing it early is the same
      // defect on a shorter clock. Succeeded belongs here too — the spread has been asked for again
      // (above) and is on its way, so the wait is for the statement, not for an extraction.
      : !answered || succeeded ? "loading"
        : status === "failed" ? "failed"
          : status === "running" ? "running"
            : status === "none" ? "none"
              // A status word this build does not know is not "nothing happened". The server passes
              // the run's own word through unmapped, deliberately, so a state added to the pipeline
              // arrives here unrecognised — and falling off the end of this chain into "none" would
              // print the one sentence the read exists to prevent.
              : "unknown";
  const title = state === "error" ? t("ex.pending.errorTitle")
    : state === "loading" ? t("empty.loading")
      : state === "unknown" ? t("ex.pending.unknownTitle")
        : state === "failed" ? t("ex.failed")
          : state === "running" ? t("ex.running")
            : t("ex.pending.noneTitle");
  const body = state === "error" ? t("ex.pending.errorBody")
    : state === "loading" ? ""
      : state === "unknown" ? t("ex.pending.unknownBody")
        : state === "failed" ? t("ex.pending.failedBody")
          : state === "running" ? t("ex.pending.runningBody")
            : t("ex.pending.noneBody");
  // The server's own words for whichever refusal this state is about — the statement's, or the run
  // read's. "Something went wrong" names neither. When the read ANSWERED and the answer was a word
  // this build does not know, the word itself is what there is to report.
  const refusal = state === "error" ? refusalText(statementError)
    : state === "unknown" ? (refusalText(runQ.error) || (status ? `status: ${status}` : ""))
      : "";

  return (
    <div data-testid="ws-awaiting" data-state={state}
         style={{ maxWidth: 560, margin: "72px auto", textAlign: "center", padding: "0 24px" }}>
      <div style={{ fontSize: 40, color: color.faint, marginBottom: 14 }}>
        {SCREENS.extraction.icon}
      </div>
      <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 8px" }}>{title}</h2>
      <p style={{ fontSize: 13, color: color.sec2, lineHeight: 1.6, margin: "0 auto 20px",
                  maxWidth: 440 }}>
        {body}
      </p>
      {refusal && (
        <p style={{ fontFamily: font.mono, fontSize: 11, color: color.muted,
                    margin: "0 auto 20px", maxWidth: 440, wordBreak: "break-word" }}>
          {refusal}
        </p>
      )}
      <div style={{ display: "flex", gap: 9, justifyContent: "center", flexWrap: "wrap" }}>
        {/* The only state with nothing watching it. Every other one either resolves itself (the read
            polls a running run, and succeeding re-asks for the spread) or is the reader's answer;
            an unanswered read polls nothing, so without this the screen could not get off it. */}
        {state === "unknown" && (
          <Button variant="secondary" testid="ws-awaiting-recheck"
                  disabled={runQ.isFetching}
                  onClick={() => { void runQ.refetch(); reloadStatement(); }}>
            {runQ.isFetching ? t("ex.run.retryPending") : t("ex.readOnly.recheck")}
          </Button>
        )}
        <Button testid="ws-awaiting-open" onClick={() => navigate(SCREENS.extraction.path)}>
          {t("ex.pending.open")}
        </Button>
      </div>
    </div>
  );
}

/* ---- the run's template has since been revised ----
 *
 * A run is PINNED to the template version it was launched against, so a spread extracted before a
 * template revision keeps rendering the old shape — which is how a corrected line order can still
 * look wrong on screen with nothing on the page to explain it. The server states the mismatch
 * (`SupersededTemplate`); this says it out loud, names both versions, and offers the only thing that
 * changes it: another run.
 *
 * The button is gated on the permission the POST needs (`pipeline:run`): a reviewer can read the
 * spread and cannot launch a run, and a button that returns 403 is worse than no button. */
function SupersededBanner({ info, documentId, t }: {
  info: SupersededTemplate;
  documentId: string | undefined;
  t: (k: string) => string;
}) {
  const canRun = useCan("pipeline:run");
  const reextract = useReextract(documentId);
  // A run needs a rulebook and a template to be worth starting, and both are read from lists that
  // load asynchronously. No pair, no button: pressing it before they arrive would start a run that
  // maps against nothing, which is worse than the stale spread the banner is complaining about.
  const { ont, tpl } = useRunDefaults();
  return (
    <div
      data-testid="ws-superseded"
      data-run-version={String(info.run_version)}
      data-latest-version={String(info.latest_version)}
      style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap",
               padding: "7px 16px", background: color.amberBg,
               borderBottom: `1px solid ${color.hairline}`, fontSize: 11.5, color: color.amberFg,
               lineHeight: 1.5 }}
    >
      <strong style={{ fontWeight: 600 }}>{t("ex.superseded.title")}.</strong>
      <span>
        {t("ex.superseded.body")
          .replace("{run}", String(info.run_version))
          .replace("{latest}", String(info.latest_version))
          .replace("{key}", info.template_key)}
      </span>
      {/* One statement at a time: once a new run has started, the offer to start one is gone —
          pressing it again would queue a second run for the same revision. */}
      {canRun && ont && tpl && !reextract.isSuccess && (
        <button
          data-testid="ws-reextract"
          disabled={reextract.isPending}
          onClick={() => reextract.mutate({ ontologyId: ont.id, templateId: tpl.id })}
          style={{ fontSize: 11.5, fontWeight: 600, color: color.amberFg, background: "#fff",
                   border: `1px solid ${color.amberFg}55`, borderRadius: radius.controlSm,
                   padding: "4px 10px", cursor: reextract.isPending ? "default" : "pointer" }}
        >
          {reextract.isPending ? t("ex.superseded.starting") : t("ex.superseded.action")}
        </button>
      )}
      {reextract.isSuccess && (
        <span data-testid="ws-reextract-started" style={{ fontWeight: 600 }}>
          {t("ex.superseded.started")}
        </span>
      )}
      {reextract.isError && (
        <span data-testid="ws-reextract-error" style={{ color: color.redFg }}>
          {t("ex.superseded.failed")}{" "}
          <span style={{ fontFamily: font.mono, fontSize: 11 }}>
            {(reextract.error as Error)?.message}
          </span>
        </span>
      )}
    </div>
  );
}

export default function WorkspaceScreen() {
  const navigate = useNavigate();
  const t = useT();
  const { locale, dataset, setDataset, statement, setStatement, sel, selRow, editing, startEdit,
          cancelEdit, stopEditing, setNote } = useUI();
  // Which period the inspector is describing — its origin, its arithmetic, the page each
  // contribution was printed on, and the comment an edit is filed against. Per-period, because
  // last year's figure has its own provenance and its own reason for having been restated.
  const [inspPeriod, setInspPeriod] = useState<"current" | "prior">("current");
  // Units presentation is display-only (raw values stay intact for editing/formulas).
  const [unitTarget, setUnitTarget] = useState<UnitTarget>("as_reported");
  // Currency presentation: "" = the document's own currency (no conversion). A different target
  // is converted at the rate the FX master resolves for the pair — there is no manual entry.
  const [targetCcy, setTargetCcy] = useState<string>("");
  // Live PDF viewer magnification. It was three spans with cursor:pointer, no handler and a
  // literal "100%": a control that advertised a zoom the screen did not have.
  const [zoom, setZoom] = useState(1);
  // "Show only low-confidence rows" — the chip beside the toolbar count. It carried
  // cursor:pointer and no handler, so it read as a filter that did nothing.
  const [lowFilter, setLowFilter] = useState(false);
  // Open a note reference: select it and jump to the All Notes screen.
  //
  // The reference is passed through AS PRINTED. It used to be parseInt'd and dropped when that
  // failed, so a chip reading "16(b)" — the HK house style for a sub-note — was a control that did
  // nothing at all. Note numbers are strings on the wire for the same reason.
  const openNote = (ref: string) => {
    const no = ref.trim();
    if (!no) return;
    setNote(no);
    navigate(SCREENS.notes.path);
  };
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const usingReal = !!activeDocumentId;
  const loaded = useProjectLoaded();
  // Which past run this Workspace shows, or null for the latest. Reset the moment the active
  // document changes — a run id belongs to the document it was launched against, and carrying one
  // over onto a different document would either 404 or, worse, silently pick that document's run
  // with the same id (run ids are per-document but the state does not know that on its own).
  // The pin lives in the store, not here: the notes pane and the export have to answer for the
  // same run as this grid, and they are rendered by other screens.
  const selectedRunId = useUI((st) => st.pinnedRunId);
  const setSelectedRunId = useUI((st) => st.setPinnedRunId);
  // ON A CHANGE OF DOCUMENT, NOT ON MOUNT. `useEffect` with a dependency list also fires the
  // first time, so clearing unconditionally cleared a pin this screen had just been opened WITH
  // — leaving Notes, Export and the top bar answering for a run the grid had silently dropped.
  // Two ways in were affected: the Run Trail's "open this run", which pins and navigates here,
  // and an analyst simply leaving the screen and coming back, which remounts it. The store
  // already clears the pin inside `setActiveDocumentId` for the case this guards (a run id
  // belongs to one filing), so what is left here is the case where the id changed while both
  // screens were mounted.
  const lastDocRef = useRef(activeDocumentId);
  useEffect(() => {
    if (lastDocRef.current !== activeDocumentId) {
      lastDocRef.current = activeDocumentId;
      setSelectedRunId(null);
    }
  }, [activeDocumentId]);
  const runsQ = useDocumentRuns(activeDocumentId ?? undefined);
  const runs = runsQ.data?.runs ?? [];
  // KPIs and the additional-items remainder are derived from a REAL extraction; there is no demo
  // data behind them, so the demo workspace falls back to the balance sheet rather than asking
  // for a view the demo endpoint cannot serve.
  const derived = DERIVED_STATEMENTS.includes(statement);
  // THE TABS ARE THE TEMPLATE'S, not a list held here. A template that declares no cash flow used
  // to get a Cash flow tab that could only ever render an empty grid, and one that declares changes
  // in equity got no tab at all — the entry point was suppressed on the grounds that the statement
  // "is not part of the reviewed set", which is a judgement the template is the right place to make.
  // Read off the RUN's template (`ExtractionRunResponse.statements`), so publishing a new template
  // cannot re-tab a spread that already exists.
  const runQ = useDocumentRun(activeDocumentId ?? undefined, selectedRunId ?? undefined);
  const templateStatements = usingReal ? (runQ.data?.statements ?? []) : [];
  // THE BASES THIS FILING LABELLED. Opening on Consolidated whatever the document contains meant a
  // company-only filing showed its figures under a substitution notice explaining a mismatch the
  // screen had introduced. A filing with one basis now offers one, so there is nothing to explain.
  // Both are offered for the demo workspace and for a run that cannot say.
  const runBases = usingReal ? (runQ.data?.bases ?? []) : [];
  const offeredBases: Basis[] = runBases.length > 0
    ? runBases
    : (["consolidated", "standalone"] as Basis[]);
  // A stored or deep-linked basis the filing does not have would land on the empty tab it used to.
  const effectiveDataset: Basis = offeredBases.includes(dataset)
    ? dataset
    : (offeredBases[0] ?? "consolidated");
  // The fallback set, used for the demo workspace and for a run that cannot say which template it
  // used (none pinned, or stored before the field existed). Not a default the template overrides —
  // a substitute for an answer that is missing.
  const offeredStatements: StatementKey[] = (
    templateStatements.length > 0
      ? templateStatements.map((st) => st.key)
      : ([
          "statement_setup",
          "balance_sheet",
          "profit_and_loss",
          "cash_flow",
          "covenants_supplemental",
          "notes",
        ] as StatementKey[])
  )
    // Temporarily hidden from the front-end while these two statements are still settling.
    // The predicate is annotated `: boolean` deliberately: without it TypeScript narrows the
    // filtered element type to the members these comparisons leave behind, and the `concat`
    // below — whose argument is the full StatementKey[] — then cannot widen back to it, which
    // failed `tsc` and with it `npm run build`.
    .filter((key): boolean => key !== "statement_setup" && key !== "covenants_supplemental")
    .concat(usingReal ? DERIVED_STATEMENTS : []);
  // A stored or deep-linked statement the offered set does not contain would render an empty grid
  // with no way to tell that from a statement the filing omits, so fall back to the first offered.
  const inOffered = offeredStatements.includes(statement);
  const effectiveStatement: StatementKey =
    !usingReal && derived ? "balance_sheet"
    : inOffered ? statement
    : (offeredStatements[0] ?? "balance_sheet");
  const realQ = useDocumentStatement(activeDocumentId ?? undefined, effectiveStatement,
                                     effectiveDataset,
                                     locale, selectedRunId ?? undefined);
  const demoQ = useStatement(effectiveStatement, effectiveDataset, locale, !usingReal);
  const data = usingReal ? realQ.data : demoQ.data;
  const isPending = usingReal ? realQ.isPending : demoQ.isPending;
  const editMut = useEditLineItem();
  const realEditMut = useEditDocumentLineItem(activeDocumentId ?? undefined);
  const realRevertMut = useRevertDocumentLineItem(activeDocumentId ?? undefined);
  // The value's source location for the live viewer — set when a row is selected (real docs).
  const [picked, setPicked] = useState<Picked | null>(null);
  // Why the last save was refused, shown in the editor. Null while nothing has been rejected.
  const [editError, setEditError] = useState<string | null>(null);
  // A highlight belongs to one statement/basis; clear it when either changes so the viewer
  // never keeps pointing at a page/cell from the statement the user just navigated away from.
  useEffect(() => { setPicked(null); }, [statement, effectiveDataset]);
  // The FX lookup is resolved here, above the loading/empty early-returns, because hooks
  // cannot be called conditionally. `converting` is false until a real target is picked, so
  // the query stays disabled and no request goes out in the default (no conversion) case.
  const srcCcy = data?.currency || "";
  // A "raw" view (the KPIs) has no currency and no magnitude — its figures are ratios. A target
  // currency left selected from a statement must not follow the analyst into it and raise "no
  // rate for ? → USD" about numbers that were never in a currency.
  const rawView = data?.presentation === "raw";
  // `wantConvert` is the user's request; `converting` is whether it is even askable — a document
  // whose own currency was never determined has no pair to look up, so we say "not converted"
  // instead of firing a lookup for "? → USD".
  const wantConvert = !rawView && !!targetCcy && targetCcy !== srcCcy;
  const converting = wantConvert && !!srcCcy;
  const fxQ = useFxRateResolution(converting ? srcCcy : undefined, converting ? targetCcy : undefined);

  // Nothing uploaded and no demo project loaded: the genuine greenfield state, and the only one
  // "No project yet" describes.
  if (!usingReal && !loaded) return <EmptyState />;
  // A real document with no spread to show — which is where a run IN FLIGHT lands, because the
  // statement route 404s until a run has a result. It used to land on <EmptyState /> too, telling a
  // reader watching their own upload being extracted that they had not uploaded anything.
  if (usingReal && realQ.isError) {
    return <AwaitingExtraction documentId={activeDocumentId ?? undefined}
                               statementError={realQ.error}
                               reloadStatement={realQ.refetch} t={t} />;
  }
  if (isPending || !data) {
    return <div style={{ padding: 60, textAlign: "center", color: color.muted }}>Loading…</div>;
  }

  const d: StatementResponse = data;
  // Whether the run behind this spread was built on a template that has since been revised, as the
  // SERVER states it (`SupersededTemplate` — the client cannot tell, since the run's template
  // version is not in the spread). Read structurally: `StatementResponse` describes the spread, and
  // a payload served without the report simply says nothing here rather than the banner claiming a
  // version it cannot name.
  const superseded =
    (d as { superseded_template?: SupersededTemplate | null }).superseded_template ?? null;
  // Selecting a row also drives the live source viewer: resolve the clicked figure's provenance
  // to a pick so the document scrolls to and highlights that value's page+bbox (PDF) or cell
  // (Excel). Clicking last year's number goes to last year's figure, not this year's.
  const handleSelect = (id: string, period: "current" | "prior" = "current") => {
    selRow(id);
    // The inspector describes the figure that was clicked, so clicking last year's number
    // switches it to last year rather than silently explaining this year's instead.
    setInspPeriod(period);
    if (usingReal) {
      const row = d.rows.find((r) => r.id === id);
      const prov = period === "prior" ? (row?.source2 ?? null) : (row?.source ?? null);
      // Set (or clear, when the figure has no resolvable source) the live-viewer highlight so a
      // row without provenance never leaves a stale highlight from a previously clicked row.
      setPicked(toPicked(prov, row?.label ?? ""));
    }
  };
  // Units presentation: convert relative to the source's own magnitude (default 1 = ones).
  const srcScale = d.units_scale_factor ?? 1;
  // The master's answer, and only when it actually holds a rate for this pair. A pending or
  // failed lookup leaves `appliedFx` null, which means the panel stays in the SOURCE currency
  // rather than briefly showing unconverted figures under the target's label.
  const fxRes = fxQ.data;
  const appliedFx = appliedRate(fxRes, converting);
  const fxPending = converting && fxQ.isPending;
  const fxUnavailable = wantConvert && !appliedFx && !fxPending;
  // A failed lookup and a missing rate both stop the conversion, but they are different facts:
  // only the latter is something an administrator fixes by adding a rate.
  const fxLookupFailed = converting && fxQ.isError;
  // Currency conversion is applied before unit scaling; identity (rate 1) when not converting.
  // Raw values are never mutated. The multiplier is a JS number only because this whole
  // presentation path already is (toLocaleString) — the exact decimal arithmetic, including
  // the reciprocal of an inverted rate, is done server-side in Decimal.
  const fx = appliedFx ? Number(appliedFx.rate) : 1;
  // On a raw view the only amounts left are a KPI's own inputs, shown in the inspector. Those are
  // statement figures AS REPORTED — the KPI response declares no magnitude, so scaling them by
  // the statements' selection (÷1000 against a scale of 1) would misstate them.
  const present = (raw: number | null) =>
    rawView ? fmtIN(raw) : presentValue(raw == null ? null : raw * fx, srcScale, unitTarget);
  // A single, unambiguous caption for the whole output panel: the active magnitude, plus the
  // conversion when re-currencied — with the rate, the date it is AS OF, and a derived marker
  // naming the stored pair we inverted. Figures are never silently transformed.
  // A raw view (the KPIs) is in no magnitude at all: the selector is hidden and `present` does
  // not scale, so captioning it with a leftover "millions" would describe figures that are not in
  // millions.
  const activeUnits = rawView ? ""
    : unitTarget === "as_reported" ? d.units : t(`ws.units.${unitTarget}`).toLowerCase();
  const fxCaption = appliedFx
    ? `${srcCcy || "?"} → ${targetCcy} @ ${fmtRate(appliedFx.rate)} · ${t("ws.asOf")} ${appliedFx.as_of}`
      + (appliedFx.derived ? ` · ${t("ws.derived")} (${appliedFx.path.join(" → ")})` : "")
    : fxPending
      ? t("ws.rateLoading")
      // Not converting: name the currency the figures ARE in, so the panel is never ambiguous.
      : fxUnavailable ? `${srcCcy || "?"} (${t("ws.sourceCcy")})` : "";
  const unitsCaption = [
    activeUnits ? `${t("ws.figuresIn")} ${activeUnits}` : "",
    fxCaption,
  ].filter(Boolean).join("  ·  ");
  const lowConfCount = d.rows.filter((r) => r.confidence?.cat === "low").length;
  // The filter is only in force while there is something for it to select. A filter left on when a
  // refetch drops the last low-confidence row would otherwise leave an unexplained empty grid.
  const lowOnly = lowFilter && lowConfCount > 0;
  // What the OUTPUT panel lists. The source-viewer column is never filtered: it shows the document
  // as printed, and hiding a printed line there would misrepresent the page.
  const gridRows = lowOnly ? d.rows.filter((r) => r.confidence?.cat === "low") : d.rows;
  // Only the live PDF viewer has pages a zoom could act on. The Excel cell grid and the sample
  // "paper" mock have nothing that would move, so they get no zoom control at all.
  const pdfViewer = usingReal && d.format === "pdf" && !!activeDocumentId;
  const zoomIdx = ZOOM_STEPS.indexOf(zoom);
  // A matrix statement (changes in equity) has NAMED component columns from the document
  // instead of the fixed current/prior pair, so it renders through MatrixGrid.
  const isMatrix = d.layout === "matrix";
  const selRowObj = d.rows.find((r) => r.id === sel) ?? d.rows.find((r) => r.inspector);
  const insp = selRowObj?.inspector;
  const isEdited = selRowObj?.status === "edited";
  const cs = selRowObj?.confidence ? confStyle(selRowObj.confidence.cat) : confStyle("med");
  // The chip's TEXT comes from the served percentage, through the one helper every screen uses;
  // `cs` supplies only its colour. Two spellings of "what confidence does this row have" is how
  // the grid ended up printing a bucket's literal beside the inspector's real figure.
  const inspConf = confReadout(
    { cat: selRowObj?.confidence?.cat, pct: selRowObj?.confidence?.pct }, t);
  // Not every row is a figure someone can correct. A KPI is computed (fix its inputs instead), a
  // line mapped to no concept has no address to save against, and an equity movement is a
  // component grid. Offering a control that cannot work is how "editing doesn't work" starts.
  // A PINNED historical run is a record of what was extracted then, and the edit endpoints
  // write to the document's latest run — so an edit made while reading run #3 would silently
  // land on run #7. The pin therefore makes the grid read-only rather than offering a control
  // that saves somewhere the reader is not looking.
  const viewingHistorical = usingReal && !!selectedRunId;
  const canEditSel = !!selRowObj && selRowObj.editable !== false && !viewingHistorical
    && (selRowObj.kind === "item" || selRowObj.kind === "subtotal" || selRowObj.kind === "total");
  const saving = realEditMut.isPending || editMut.isPending;
  // The inspector describes ONE period, so it reads that period's origin rather than the row
  // summary — a row whose current figure was overridden still has an extracted prior one.
  const inspOrigin = (inspPeriod === "current" ? selRowObj?.origin1 : selRowObj?.origin2)
    ?? selRowObj?.origin ?? "extracted";
  const inspChip = ORIGIN_CHIP[inspOrigin];
  const inspReported = inspPeriod === "current" ? selRowObj?.reported1 : selRowObj?.reported2;
  const inspShown = inspPeriod === "current" ? selRowObj?.v1 : selRowObj?.v2;
  const inspComputed = inspPeriod === "current" ? selRowObj?.calculated1 : selRowObj?.calculated2;
  // A calculated line's divergence from the printed figure is a finding, so the inspector states
  // it. The printed figure is never the line's value, but silence would hide the disagreement.
  const inspDiverges = inspOrigin === "calculated" && inspReported != null && inspShown != null
    && Math.abs(inspShown - inspReported) > 0.5;
  const inspNote = selRowObj?.comments?.[inspPeriod];

  /** Save an edit: the retyped columns, the stored formula, and the reason.
   *
   *  The comment is filed against the period the inspector is describing; every other period keeps
   *  the note already recorded against it, so correcting this year does not restate why last year
   *  was changed. */
  const saveEdit = async (edits: PeriodEdit[], formula: string, comment: string) => {
    if (!selRowObj) return;
    const figureFor = (p: "current" | "prior") => (p === "current" ? selRowObj.v1 : selRowObj.v2);
    // A formula (or a comment) on its own is still an edit: it applies to the period on show.
    const work: PeriodEdit[] = edits.length
      ? [...edits]
      : [{ period: inspPeriod, value: figureFor(inspPeriod) }];
    // The comment is filed against the period on show, so that period has to be among the writes
    // or the reason would be typed, accepted, and never sent — which is what happens when the
    // inspector is showing last year while this year's column is the one being retyped.
    if (comment !== (selRowObj.comments?.[inspPeriod]?.text ?? "")
        && !work.some((e) => e.period === inspPeriod)) {
      work.push({ period: inspPeriod, value: figureFor(inspPeriod) });
    }
    try {
      for (const e of work) {
        const note = e.period === inspPeriod
          ? comment
          : (selRowObj.comments?.[e.period]?.text ?? "");
        if (usingReal) {
          await realEditMut.mutateAsync({ key: selRowObj.id, value: e.value, formula,
                                          // The basis the grid is SHOWING, so an edit lands on
                                          // the figure the analyst is looking at rather than on
                                          // a basis the filing may not even have.
                                          basis: effectiveDataset, period: e.period,
                                          comment: note });
        } else if (e.period === "current") {
          await editMut.mutateAsync({ id: selRowObj.id, value: e.value, formula });
        } else {
          throw new Error("The sample project only supports editing the current period. "
                          + "Upload a document to edit both.");
        }
      }
      setEditError(null);
      stopEditing();
    } catch (err) {
      // Deliberately stays in edit mode: the figures on screen did not change, and closing the
      // editor as if they had is the failure this replaces.
      setEditError(editErrorText(err));
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* ---------------- TOOLBAR ---------------- */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "11px 18px",
          background: color.surface,
          borderBottom: `1px solid ${color.cardBorder}`,
          flex: "0 0 auto",
        }}
      >
        <Segmented<Basis>
          options={offeredBases.map((b) => ({ value: b, label: t(`ws.${b}`) }))}
          value={effectiveDataset}
          onChange={setDataset}
        />
        <Segmented<StatementKey>
          // One entry per statement the run's template declares, in the template's own order, plus
          // the derived views (KPIs) which are computed off the extraction rather than declared by
          // a template. Labels come from `ws.stmt.*` — translated in every shipped locale — and fall
          // back to the server's English title only for a key this build has no translation for.
          // Additional items is gone entirely, front and back: see _build_statement.
          options={offeredStatements.map((key) => ({
            value: key,
            label: t(`ws.stmt.${key}`)
              || templateStatements.find((st) => st.key === key)?.title
              || key,
          }))}
          value={effectiveStatement}
          onChange={setStatement}
        />
        {usingReal && runs.length > 1 && (
        <ToolSelect<string>
          label={t("ws.run")}
          value={selectedRunId ?? ""}
          options={[
            { value: "", label: t("ws.run.latest") },
            ...runs
              .filter((r) => r.run_id !== runs[0]?.run_id)
              .map((r) => ({
                value: r.run_id,
                label: `#${r.run_number} · ${new Date(r.created_at).toLocaleString()}`
                       + (r.status !== "succeeded" ? ` (${r.status})` : ""),
              })),
          ]}
          onChange={(v) => setSelectedRunId(v || null)}
        />
        )}
        {!rawView && (
        <ToolSelect<string>
          label={t("ws.currency")}
          value={targetCcy || srcCcy}
          options={[
            ...(srcCcy ? [{ value: srcCcy, label: `${srcCcy} (${t("ws.sourceCcy")})` }] : []),
            ...CURRENCIES.filter((c) => c !== srcCcy).map((c) => ({ value: c, label: c })),
          ]}
          onChange={(v) => setTargetCcy(v === srcCcy ? "" : v)}
        />
        )}
        {fxUnavailable && (
          <span
            title={fxRes && !fxRes.resolved ? fxRes.detail : undefined}
            style={{ fontSize: 11.5, fontWeight: 600, color: color.amberFg,
                     background: color.amberBg, padding: "5px 10px",
                     borderRadius: radius.pill, whiteSpace: "nowrap" }}
          >
            {fxLookupFailed ? t("ws.rateFailed") : t("ws.noRate")} {srcCcy || "?"} → {targetCcy}
          </span>
        )}
        {/* A ratio has no magnitude to present — offering "in thousands" over 1.35× would only
            invite a nonsense reading, so the selector is absent on a raw view rather than inert. */}
        {!rawView && (
        <ToolSelect<UnitTarget>
          label={t("ws.units")}
          value={unitTarget}
          options={[
            { value: "as_reported", label: t("ws.units.as_reported") },
            { value: "thousands", label: t("ws.units.thousands") },
            { value: "millions", label: t("ws.units.millions") },
            { value: "billions", label: t("ws.units.billions") },
          ]}
          onChange={setUnitTarget}
        />
        )}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 9 }}>
          {/* The count is the rows the grid holds — it read `usingReal ? lowConfCount : 3` on the
              sample path, a fabricated 3 with the true count on the line above it. Both paths
              carry per-row confidence (the demo statement route sets it too), so there is one
              spelling. Shown only when there is something to count: a red "0 low-confidence" pill
              is noise, and pressing it would filter to an empty grid.
              There is no "unreconciled" chip any more. Nothing in the statement payload means it —
              `status: "recon"` covers BOTH a calculated-vs-printed divergence and a netting
              restatement — so the only options were a fabricated 2 or nothing, and the Review
              queue already counts reconciliation findings from the checks themselves. */}
          {lowConfCount > 0 && (
            <button
              data-testid="ws-lowconf"
              aria-pressed={lowOnly}
              title={lowOnly ? t("ws.showAll") : t("ws.lowconfOnly")}
              onClick={() => setLowFilter((v) => !v)}
              style={{
                fontSize: 11.5,
                fontWeight: 600,
                color: lowOnly ? "#fff" : color.redFg,
                background: lowOnly ? color.redFg : color.redBg,
                border: `1px solid ${lowOnly ? color.redFg : "transparent"}`,
                padding: "5px 10px",
                borderRadius: radius.pill,
                cursor: "pointer",
              }}
            >
              {lowConfCount} {t("ws.lowconf")}
            </button>
          )}
          <button
            onClick={() => navigate(SCREENS.export.path)}
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "#fff",
              background: color.indigo,
              border: "none",
              borderRadius: radius.control,
              padding: "7px 14px",
              cursor: "pointer",
            }}
          >
            {t("ws.export")}
          </button>
        </div>
      </div>

      {/* A strip of its own between the toolbar and the panels — out of the grid's way, since the
          spread underneath is still the thing being read (and is still valid: it is what the run
          produced, on the template it was launched against). */}
      {superseded?.superseded && (
        <SupersededBanner info={superseded} documentId={activeDocumentId ?? undefined} t={t} />
      )}
      {/* Read-only has to say WHY. A grid that silently refuses an edit reads as broken, and the
          reason is not visible from the figures — it is which run is pinned. */}
      {viewingHistorical && (
        <div
          data-testid="ws-historical-run"
          data-run-id={selectedRunId ?? ""}
          style={{ flex: "0 0 auto", display: "flex", alignItems: "center", gap: 9,
                   flexWrap: "wrap", padding: "7px 16px", background: color.amberBg,
                   borderBottom: `1px solid ${color.hairline}`, fontSize: 11.5,
                   color: color.amberFg, lineHeight: 1.5 }}
        >
          <strong style={{ fontWeight: 600 }}>
            {t("ws.run.historical").replace(
              "{n}",
              String(runs.find((r) => r.run_id === selectedRunId)?.run_number ?? "?"))}
          </strong>
          <span>{t("ws.run.historicalWhy")}</span>
        </div>
      )}
      {/* The figures below are the only basis this filing labelled, answering a request for the
          other one. Said out loud, in the same strip: an empty grid used to be shown here instead,
          and the fix must not trade it for the Company's figures captioned Consolidated. */}
      {d.basis_substituted && (
        <div
          role="status"
          style={{
            margin: "0 0 10px", padding: "9px 13px", borderRadius: 4,
            background: color.amberBg, color: color.amberFg,
            border: `1px solid ${color.amberFg}22`, fontSize: 12.5,
          }}
        >
          {t("ws.basis.substituted")
            .replace("{served}", t(`ws.${d.basis}`))
            .replace("{asked}", t(`ws.${d.basis_requested ?? "consolidated"}`))}
        </div>
      )}

      {/* ---------------- BODY ---------------- */}
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* -------- LEFT: source viewer -------- */}
        <div
          style={{
            width: layout.sourceViewer,
            flex: `0 0 ${layout.sourceViewer}`,
            background: color.viewerBg,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 12px",
              background: color.viewerHeader,
              color: "#dfe3e9",
              flex: "0 0 auto",
            }}
          >
            <span style={{ fontSize: 11.5, fontWeight: 600 }}>{d.viewer.company}</span>
            {/* A chip names what the viewer is showing. It carries NO cursor:pointer: there is no
                page navigation behind these labels, and the pointer promised a tab switch that
                never existed — worst of all on the sample path, which served an inactive-looking
                "Note 12 · p.171" chip beside the active one, reading as a tab that cannot be
                selected. The note references live in the viewer callout, as prose. */}
            <div style={{ display: "flex", gap: 4, marginLeft: 6 }}>
              {d.viewer.chips.map((c) => (
                <span
                  key={c.label}
                  data-testid="ws-viewer-chip"
                  style={{
                    fontSize: 10.5,
                    padding: "3px 8px",
                    borderRadius: radius.chip,
                    background: c.active ? color.indigo : "#3c4450",
                    color: c.active ? "#fff" : "#dfe3e9",
                    fontWeight: c.active ? 600 : 400,
                  }}
                >
                  {c.label}
                </span>
              ))}
            </div>
            {/* Zoom — rendered ONLY over the live PDF page stack, the one viewer where scaling the
                page column does something (the bbox highlight is already in percentages of that
                box, so the highlight scales with it). The level is read from state, never the
                literal "100%" that used to sit here, and each end of the range is visibly disabled
                rather than silently inert. */}
            {pdfViewer && (
              <div
                data-testid="viewer-zoom"
                style={{
                  marginLeft: "auto",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 11,
                  color: "#aab1bc",
                }}
              >
                <ZoomButton
                  label="−"
                  title={t("ws.zoomOut")}
                  disabled={zoomIdx <= 0}
                  onClick={() => setZoom(ZOOM_STEPS[Math.max(0, zoomIdx - 1)])}
                />
                <button
                  data-testid="viewer-zoom-level"
                  title={t("ws.zoomReset")}
                  aria-label={t("ws.zoomReset")}
                  disabled={zoom === 1}
                  onClick={() => setZoom(1)}
                  style={{
                    fontFamily: font.mono, fontSize: 11, color: "#dfe3e9",
                    background: "transparent", border: "none", padding: 0,
                    cursor: zoom === 1 ? "default" : "pointer", opacity: zoom === 1 ? 0.6 : 1,
                  }}
                >
                  {Math.round(zoom * 100)}%
                </button>
                <ZoomButton
                  label="+"
                  title={t("ws.zoomIn")}
                  disabled={zoomIdx >= ZOOM_STEPS.length - 1}
                  onClick={() => setZoom(ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, zoomIdx + 1)])}
                />
              </div>
            )}
          </div>
          {pdfViewer && activeDocumentId ? (
            <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 12, background: "#e9ebef" }}>
              <PageStack documentId={activeDocumentId} pageCount={d.page_count ?? 1}
                         picked={picked?.kind === "pdf" ? picked : null} maxHeight="100%"
                         scale={zoom} />
            </div>
          ) : usingReal && (d.format === "xlsx" || d.format === "xls") && activeDocumentId ? (
            <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 12, background: "#fff" }}>
              <ExcelGrid documentId={activeDocumentId}
                         picked={picked?.kind === "xlsx" ? picked : null} t={t} />
            </div>
          ) : (
          <div style={{ flex: 1, overflow: "auto", padding: 22, display: "flex", justifyContent: "center" }}>
            <div
              style={{
                width: "100%",
                maxWidth: 520,
                background: "#fff",
                borderRadius: 3,
                boxShadow: shadow.paper,
                padding: "34px 40px",
                fontSize: 11,
                color: "#333",
                height: "fit-content",
              }}
            >
              <div style={{ textAlign: "center", borderBottom: "2px solid #222", paddingBottom: 8, marginBottom: 4 }}>
                <div style={{ fontWeight: 700, fontSize: 13, letterSpacing: 0.3 }}>{d.viewer.company}</div>
                <div style={{ fontSize: 10, color: "#666", marginTop: 2 }}>{d.viewer.subtitle}</div>
              </div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "flex-end",
                  gap: 22,
                  fontSize: 9,
                  color: "#888",
                  padding: "4px 0 6px",
                  fontFamily: font.mono,
                }}
              >
                <span>{`(${d.currency_symbol} in ${d.units.toLowerCase()})`}</span>
                <span>{d.periods[0]}</span>
                <span>{d.periods[1]}</span>
              </div>
              {d.rows.map((r) => (
                <PaperRow key={r.id} row={r} selected={r.id === sel} />
              ))}
              <div
                style={{
                  marginTop: 14,
                  padding: "9px 11px",
                  background: color.indigoTint,
                  border: `1px solid ${color.indigoBorder}`,
                  borderRadius: 6,
                  fontSize: 9.5,
                  color: color.indigo,
                  lineHeight: 1.5,
                }}
              >
                {d.viewer.callout}
              </div>
            </div>
          </div>
          )}
        </div>

        {/* -------- RIGHT: output panel, with the cell inspector along the bottom -------- */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            background: color.surface,
            minHeight: 0,
          }}
        >
          {/* grid header — a matrix carries its own header inside MatrixGrid, because its
              columns come from the document rather than being the fixed current/prior pair. */}
          {!isMatrix && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: layout.gridCols,
              padding: "9px 16px",
              background: color.rowAltBg,
              borderBottom: `1px solid #e8eaee`,
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: 0.4,
              color: color.muted,
              flex: "0 0 auto",
            }}
          >
            <span>{t("col.lineitem")}</span>
            <span style={{ textAlign: "center", ...colDiv }}>{t("col.note")}</span>
            <span style={{ textAlign: "right", ...colDiv }}>{d.periods[0] ?? ""}</span>
            <span style={{ textAlign: "right", ...colDiv }}>{d.periods[1] ?? ""}</span>
            <span style={{ textAlign: "right", ...colDiv }}>{t("col.conf")}</span>
          </div>
          )}

          {/* units caption — labels the magnitude of every figure in the panel */}
          {unitsCaption && (
            <div
              // The caption rounds the rate for legibility; the tooltip carries the exact
              // multiplier (and the rate's own provenance note) so it can still be verified.
              title={appliedFx
                ? `1 ${srcCcy} = ${appliedFx.rate} ${targetCcy}`
                  + (appliedFx.source ? ` · ${appliedFx.source}` : "")
                : undefined}
              style={{ flex: "0 0 auto", padding: "3px 16px", background: color.rowAltBg,
                       borderBottom: `1px solid ${color.hairline}`, textAlign: "right",
                       fontSize: 10, fontStyle: "italic", color: color.muted }}
            >
              {unitsCaption}
            </div>
          )}

          {/* The master holds no rate for the chosen pair, so nothing was converted. Say so
            * where the figures are, and point at who can fix it — a silent fallback here
            * would label source-currency numbers as the target currency. */}
          {fxUnavailable && (
            <div style={{ flex: "0 0 auto", padding: "6px 16px", background: color.amberBg,
                          borderBottom: `1px solid ${color.hairline}`, fontSize: 11,
                          color: color.amberFg, lineHeight: 1.5 }}>
              <strong style={{ fontWeight: 600 }}>
                {fxLookupFailed ? t("ws.rateFailed") : t("ws.noRate")} {srcCcy || "?"} → {targetCcy}.
              </strong>{" "}
              {fxLookupFailed ? t("ws.rateFailedHint") : t("ws.noRateHint")}
            </div>
          )}

          {/* WHY THE GRID IS EMPTY, where the rows would have been. An empty spread has more than
              one cause — the run's template declares no statement of this type, it declares one and
              nothing was extracted for this basis, or a matrix the template does not carry — and
              "nothing here" reads as "the extraction found nothing", which sends an analyst back
              into the document after figures that were never going to be shown. The server states
              the reason and its sentence (`refused`); the same sentence also rides on
              `viewer.callout`, which only the paper-preview branch renders and a real PDF or
              workbook never takes. */}
          {d.refused && (
            <div
              data-testid="ws-refused"
              data-reason={d.refused.reason}
              style={{
                flex: 1, minHeight: 0, display: "flex", alignItems: "center",
                justifyContent: "center", padding: "40px 32px", textAlign: "center",
                fontSize: 12.5, lineHeight: 1.6, color: color.muted,
              }}
            >
              {d.refused.message}
            </div>
          )}

          {/* scroll body — `gridRows` is `d.rows` unless the low-confidence chip is pressed. */}
          {!d.refused && (isMatrix ? (
            <MatrixGrid columns={d.columns ?? []} rows={gridRows} sel={sel} present={present}
                        linkable={usingReal} onSelect={handleSelect} />
          ) : (
            <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
              {gridRows.map((r) => (
                <OutputRow
                  key={r.id} row={r} sel={sel} present={present} linkable={usingReal}
                  onSelect={handleSelect}
                  onOpenNote={openNote}
                />
              ))}
            </div>
          ))}

          {/* ---------------- CELL INSPECTOR ---------------- */}
          <div
            data-testid="cell-inspector"
            style={{
              flex: "0 0 auto",
              borderTop: `1px solid ${color.cardBorder}`,
              background: "#fbfcfd",
              padding: "12px 16px",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 10,
                marginBottom: 8,
                flexWrap: "wrap",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
                <span style={{ fontSize: 12.5, fontWeight: 600 }}>{selRowObj?.label ?? ""}</span>
                {/* The selected row's measured mapping confidence, with the word around it
                    localized — it was the English literal "confidence" beside four localized
                    labels, so the chip read "41% confidence" to a zh reader. `confReadout` also
                    keeps this honest if the payload ever carries no number: it says so rather than
                    rendering a bare "%". */}
                {selRowObj?.confidence && (
                  <span
                    data-testid="inspector-conf"
                    data-measured={String(inspConf.measured)}
                    title={inspConf.title}
                    style={{
                      fontSize: 10.5,
                      fontWeight: 600,
                      padding: "2px 8px",
                      borderRadius: radius.pill,
                      background: inspConf.measured ? cs.bg : "transparent",
                      color: inspConf.measured ? cs.fg : color.muted,
                      border: inspConf.measured ? undefined : `1px dashed ${color.dashed}`,
                    }}
                  >
                    {inspConf.text} {t("conf.label")}
                  </span>
                )}
                {/* Where the figure on show came from — extracted, computed from its components,
                    overridden by hand, or printed and unverifiable. */}
                {inspOrigin !== "extracted" && (
                  <span
                    title={inspChip.help}
                    data-testid={`inspector-origin-${inspOrigin}`}
                    style={{
                      fontSize: 10.5,
                      fontWeight: 600,
                      padding: "2px 8px",
                      borderRadius: radius.pill,
                      background: inspChip.bg,
                      color: inspChip.fg,
                    }}
                  >
                    {inspOrigin === "calculated" ? "calculated"
                      : inspOrigin === "manual" ? "manual override" : "printed, unverified"}
                  </span>
                )}
                {insp && inspOrigin === "extracted" && (
                  <span
                    style={{
                      fontSize: 10.5,
                      fontWeight: 600,
                      padding: "2px 8px",
                      borderRadius: radius.pill,
                      background: isEdited ? color.indigoTint2 : color.amberBg,
                      color: isEdited ? color.indigo : color.amberFg,
                    }}
                  >
                    {isEdited ? `Edited · ${insp.tag}` : insp.tag}
                  </span>
                )}
                {/* WHICH SECTION OF THE FACE this row belongs to, and WHERE IT WAS PRINTED —
                    stated in words for the row on show, so neither has to be inferred from the
                    presence or absence of a chip in the grid. The section is the analyst tag the
                    backend resolved (services/buckets.py), not the template heading the grid groups
                    by: the two usually agree and the tag is the one a reader groups the filing by. */}
                {selRowObj?.bucket_label && (
                  <span
                    title="The analyst section this figure is tagged with"
                    data-testid="inspector-section"
                    style={{ fontSize: 10.5, fontWeight: 600, padding: "2px 8px",
                             borderRadius: radius.pill, background: color.rowAltBg,
                             color: color.sec2, border: `1px solid ${color.hairline3}` }}
                  >
                    {selRowObj.bucket_label}
                  </span>
                )}
                {selRowObj?.printed_in && (
                  <span
                    data-testid={`inspector-printed-${selRowObj.printed_in}`}
                    title={selRowObj.printed_in === "face"
                      ? "Printed on the face of the statement"
                      : "Printed inside a note — its lines detail a face figure, so adding both "
                        + "counts the same money twice"}
                    style={{ fontSize: 10.5, fontWeight: 600, padding: "2px 8px",
                             borderRadius: radius.pill,
                             background: selRowObj.printed_in === "face" ? color.rowAltBg
                                                                        : color.amberBg,
                             color: selRowObj.printed_in === "face" ? color.sec2 : color.amberFg,
                             border: selRowObj.printed_in === "face"
                               ? `1px solid ${color.hairline3}` : undefined }}
                  >
                    {selRowObj.printed_in === "face" ? "on the face" : "in a note"}
                  </span>
                )}
                {/* THE NOTES THAT DETAIL THIS FIGURE — the linkage the extraction recorded, which
                    is not the same list as the note column's chips: those are what the page printed,
                    these are the notes this run actually read and can open. */}
                {(selRowObj?.notes || []).length > 0 && (
                  <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{ fontSize: 10.5, color: color.muted }}>detailed by</span>
                    {(selRowObj?.notes || []).map((n) => (
                      <NoteChip key={n} onClick={(e) => { e?.stopPropagation(); openNote(n); }}>
                        {n}
                      </NoteChip>
                    ))}
                  </span>
                )}
                {/* Which period everything below is about. */}
                {selRowObj && (
                  <span style={{ display: "flex", gap: 4 }}>
                    {(["current", "prior"] as const).map((p, i) => (
                      <button
                        key={p}
                        onClick={() => setInspPeriod(p)}
                        data-testid={`inspector-period-${p}`}
                        style={{ fontSize: 10.5, fontWeight: 600, padding: "3px 9px",
                                 borderRadius: radius.pill, cursor: "pointer",
                                 border: `1px solid ${inspPeriod === p ? color.indigo
                                                                       : color.controlBorder}`,
                                 background: inspPeriod === p ? color.indigoTint2 : "#fff",
                                 color: inspPeriod === p ? color.indigo : color.sec2 }}
                      >
                        {d.periods[i] || (p === "current" ? "Current" : "Prior")}
                      </button>
                    ))}
                  </span>
                )}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 11, color: color.muted, fontFamily: font.mono }}>
                  source: {insp?.src ?? ""}
                </span>
                {!editing && usingReal && selRowObj?.status === "edited" && (
                  <button
                    onClick={() => selRowObj && realRevertMut.mutate(selRowObj.id)}
                    style={{
                      fontSize: 11, fontWeight: 600, color: color.sec2, background: "#fff",
                      border: `1px solid ${color.controlBorder}`, borderRadius: radius.controlSm,
                      padding: "5px 11px", cursor: "pointer",
                    }}
                  >
                    ↺ Revert
                  </button>
                )}
                {!editing && canEditSel && (
                  <button
                    data-testid="edit-value"
                    onClick={() => { setEditError(null); startEdit(); }}
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: color.indigo,
                      background: "#fff",
                      border: `1px solid ${color.indigoBorder2}`,
                      borderRadius: radius.controlSm,
                      padding: "5px 11px",
                      cursor: "pointer",
                    }}
                  >
                    ✎ Edit value
                  </button>
                )}
              </div>
            </div>

            {editing && canEditSel && selRowObj ? (
              <InspectorEditor
                key={selRowObj.id}
                row={selRowObj}
                periods={d.periods}
                period={inspPeriod}
                saving={saving}
                error={editError}
                onSave={saveEdit}
                onCancel={() => { setEditError(null); cancelEdit(); }}
              />
            ) : (
              <>
                {/* The arithmetic behind the figure. For a calculated line this is the template's
                    rollup rendered from the figures — display only, never an expression to send
                    back: the server would evaluate it, and a computed result outranks a typed
                    value, so a hand-entered figure would be silently replaced by the old sum. */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    background: "#fff",
                    border: `1px solid ${color.controlBorder}`,
                    borderRadius: radius.control,
                    padding: "8px 11px",
                  }}
                >
                  <span style={{ fontFamily: font.mono, fontSize: 12, color: color.amberFg, fontWeight: 600 }}>ƒx</span>
                  <span
                    data-testid="inspector-arithmetic"
                    style={{ fontFamily: font.mono, fontSize: 12, color: color.ink, flex: 1,
                             wordBreak: "break-word" }}
                  >
                    {selRowObj?.arithmetic ?? selRowObj?.formula ?? ""}
                  </span>
                  <span style={{ fontFamily: font.mono, fontSize: 12, fontWeight: 600, color: color.indigo }}>
                    = {(inspPeriod === "current" ? selRowObj?.display1 : selRowObj?.display2)
                       ?? (inspShown == null ? (insp?.result ?? "") : present(inspShown))}
                  </span>
                </div>

                {/* What the document printed, when the figure shown is a DIFFERENT number. */}
                {inspDiverges && (
                  <div
                    data-testid="reported-divergence"
                    style={{ marginTop: 9, padding: "7px 11px", background: color.amberBg,
                             borderRadius: radius.control, fontSize: 11.5, color: color.amberFg,
                             lineHeight: 1.5 }}
                  >
                    <strong style={{ fontWeight: 600 }}>
                      The document printed {present(inspReported)}.
                    </strong>{" "}
                    This line shows what its components come to. The difference
                    ({present((inspShown ?? 0) - (inspReported ?? 0))}) is in the review queue.
                  </div>
                )}
                {inspOrigin === "manual" && inspComputed != null && (
                  <div style={{ fontSize: 11, color: color.sec2, marginTop: 7, lineHeight: 1.5 }}>
                    The components come to {present(inspComputed)}; the figure shown was entered
                    by hand.
                  </div>
                )}
                {!inspDiverges && inspOrigin !== "manual" && (
                  <div style={{ fontSize: 11, color: color.sec2, marginTop: 7, lineHeight: 1.5 }}>
                    {isEdited
                      ? "Manually edited from the front-end — the cell now carries the formula above. Original extraction confidence is retained."
                      : insp?.note ?? ""}
                  </div>
                )}

                {/* The reason an analyst recorded against this figure, beside the figure. */}
                {inspNote?.text && (
                  <div
                    data-testid="inspector-comment"
                    style={{ marginTop: 9, padding: "7px 11px", background: "#fff",
                             border: `1px solid ${color.controlBorder}`,
                             borderRadius: radius.control, fontSize: 11.5, color: color.ink,
                             lineHeight: 1.5 }}
                  >
                    <span style={{ color: color.amberFg, fontWeight: 600 }}>✎ </span>
                    {inspNote.text}
                    {inspNote.by && (
                      <span style={{ fontSize: 10.5, color: color.muted }}>
                        {" "}— {inspNote.by}
                        {inspNote.at ? ` · ${inspNote.at.slice(0, 10)}` : ""}
                      </span>
                    )}
                  </div>
                )}

                {/* A combined figure matches no single line on the page, so every line that went
                    into it is listed with its own amount and its own page — click one to jump
                    the viewer straight to where it was printed. */}
                {inspOrigin !== "reported_uncomputed" && selRowObj?.contributions?.length ? (
                  <div
                    style={{
                      marginTop: 9,
                      border: `1px solid ${color.controlBorder}`,
                      borderRadius: radius.control,
                      background: "#fff",
                      overflow: "hidden",
                    }}
                  >
                    {selRowObj.contributions.map((c, i) => {
                      // Each period was printed in its own column, often on its own page.
                      const prov = inspPeriod === "current" ? c.source : c.source2;
                      const src = inspPeriod === "current" ? c.src : c.src2;
                      const v = inspPeriod === "current" ? c.v1 : c.v2;
                      const jump = usingReal ? toPicked(prov ?? null, c.label) : null;
                      // A fact printed twice is evidence for the figure, not an addend of it. It
                      // still belongs in the list — it is a real place in the document a reviewer
                      // can check — but showing it with a "+" would make the column not add up.
                      const counted =
                        (inspPeriod === "current" ? c.counted : c.counted2) !== false;
                      return (
                        <div
                          key={`${c.label}-${i}`}
                          onClick={() => jump && setPicked(jump)}
                          title={jump ? `Show ${src} in the document` : undefined}
                          style={{
                            display: "flex",
                            alignItems: "baseline",
                            gap: 8,
                            padding: "6px 10px",
                            borderTop: i === 0 ? "none" : `1px solid ${color.cardBorder}`,
                            cursor: jump ? "pointer" : "default",
                          }}
                        >
                          <span style={{ fontFamily: font.mono, fontSize: 10.5, color: color.muted,
                                         minWidth: 14 }}>
                            {!counted ? "=" : c.deducted ? "−" : i === 0 ? "" : "+"}
                          </span>
                          <span style={{ flex: 1, minWidth: 0 }}>
                            <span style={{ fontSize: 11.5, color: color.ink, display: "block",
                                           textDecoration: jump ? "underline dotted" : "none" }}>
                              {c.label}
                            </span>
                            {/* An input read out of PROSE has no printed row to point at, so the
                                filing's own sentence is the trace — shown here rather than left to
                                a page number the reader cannot check anything against. */}
                            {c.excerpt ? (
                              <span style={{ fontSize: 10.5, color: color.muted, fontStyle: "italic",
                                             display: "block", marginTop: 2, lineHeight: 1.45 }}>
                                “{c.excerpt}”
                              </span>
                            ) : null}
                          </span>
                          {!counted ? (
                            <span style={{ fontSize: 9.5, fontWeight: 600, padding: "1px 6px",
                                           borderRadius: radius.pill, background: color.amberBg,
                                           color: color.amberFg }}>
                              printed twice
                            </span>
                          ) : c.residual ? (
                            <span style={{ fontSize: 9.5, fontWeight: 600, padding: "1px 6px",
                                           borderRadius: radius.pill, background: color.amberBg,
                                           color: color.amberFg }}>
                              {v == null ? "absent" : "routed"}
                            </span>
                          ) : null}
                          <span style={{ fontFamily: font.mono, fontSize: 11, color: color.sec2,
                                         minWidth: 46, textAlign: "right" }}>
                            {src || ""}
                          </span>
                          <span style={{ fontFamily: font.mono, fontSize: 11.5, fontWeight: 600,
                                         color: counted ? color.ink : color.muted, minWidth: 92,
                                         textAlign: "right" }}>
                            {/* A deducted input carries the "−" in its operator column, so the
                                amount shows its magnitude: "− 529,841" rather than "+ -529,841". */}
                            {v == null ? "—" : present(c.deducted ? Math.abs(v) : v)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
