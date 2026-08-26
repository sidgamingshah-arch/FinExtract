/**
 * Design tokens — the single source of truth for the UI, transcribed exactly from
 * the design handoff. Components style with these constants (inline styles, mirroring
 * the wireframe) so every screen stays visually consistent. Do NOT introduce colors
 * outside this file.
 */
export const color = {
  // Page / surfaces
  pageBg: "#f5f6f8",
  surface: "#ffffff",
  cardBorder: "#e2e5ea",
  hairline: "#f2f3f5",
  hairline2: "#f0f1f4",
  hairline3: "#eef0f3",
  rowAltBg: "#fafbfc",

  // Ink
  ink: "#1e2530",
  ink2: "#374151",
  sec: "#4b5563",
  sec2: "#6b7280",
  muted: "#8a93a0",
  muted2: "#98a1ae",
  faint: "#9aa3b0",

  // Indigo (primary/accent)
  indigo: "#2743c4",
  indigoHover: "#1a2f9c",
  indigoTint: "#f4f6fe",
  indigoTint2: "#eef1fb",
  indigoTint3: "#eaf0ff",
  indigoBorder: "#dfe6fb",
  indigoBorder2: "#c3ccf4",

  // Dark chrome
  topbar: "#161c26",
  topbarBorder: "#0c1119",
  stepperActive: "#242b37",
  divider: "#2c333f",
  stepFuture: "#8a93a0",
  viewerBg: "#5b6472",
  viewerHeader: "#4a525f",

  // Semantic / confidence
  greenFg: "#1a7f5a",
  greenBg: "#e6f4ee",
  greenBg2: "#eaf4ef",
  amberFg: "#b7791f",
  amberBg: "#fdf3e0",
  redFg: "#c0392b",
  redBg: "#fdecea",
  excelGreen: "#217346",

  // Controls
  controlBorder: "#d4dae2",
  dashed: "#c2cad6",
  segBg: "#eef0f3",
  toggleOff: "#cfd4dc",
  trackBg: "#e7eaef",
} as const;

export const font = {
  sans: "'IBM Plex Sans', system-ui, sans-serif",
  mono: "'IBM Plex Mono', monospace",
} as const;

export const radius = {
  card: 12,
  cardSm: 11,
  control: 8,
  controlSm: 7,
  pill: 20,
  chip: 5,
  toggle: 10,
} as const;

export const shadow = {
  paper: "0 6px 22px rgba(0,0,0,.28)",
  focusRing: "0 0 0 3px rgba(39,67,196,.12)",
  segActive: "0 1px 2px rgba(0,0,0,.1)",
} as const;

export const layout = {
  topbarH: 52,
  navRail: 214,
  sourceViewer: "43%",
  notesList: 290,
  templateTree: 360,
  screenMax: 1120,
  screenMaxWide: 1180,
  // Workspace output grid columns: LINE ITEM / NOTE / period1 / period2 / CONF (one NOTE column).
  gridCols: "minmax(140px,1.4fr) 56px 110px 110px 60px",
} as const;

/** Confidence category → badge COLORS. Colour only, and deliberately so.
 *
 *  This helper also returned a representative percentage per category
 *  ({high:"96%", med:"78%", low:"54%"}) and three screens printed that literal as the MEASURED
 *  confidence of the very thing it sat above: a page classified at 0.40 rendered "54%", a note
 *  row at 0.41 rendered "54%", every 'high' row rendered "96%" whatever its real score, and a row
 *  with NO confidence at all rendered "78%" because the backend's `_conf_cat(None)` buckets a
 *  missing score as 'med'. A category is a bucket of a measurement, never the measurement, so the
 *  bucket's stand-in figure is no longer reachable from here — there is nothing left to print by
 *  accident. The measured percentage is served alongside the category (`Confidence.pct`,
 *  `PageCard.conf_pct`, `NoteDetailRow.conf_pct`) and is printed through
 *  components/ui.tsx::confReadout, which says "not scored" when the payload carries no number
 *  rather than inventing one. */
export type ConfCat = "high" | "med" | "low";
export function confStyle(cat: ConfCat): { bg: string; fg: string } {
  if (cat === "high") return { bg: color.greenBg, fg: color.greenFg };
  if (cat === "med") return { bg: color.amberBg, fg: color.amberFg };
  return { bg: color.redBg, fg: color.redFg };
}

/** A duration in milliseconds as a person reads it: "8s", "2m 04s".
 *
 * Lives here beside the other formatters because THREE surfaces print a duration and they must not
 * disagree about how one looks: the extraction screen's live gauge, the same screen's settled header,
 * and the Analysis screen's audit trail. It was a module-local helper in ExtractionView while only
 * that screen had one.
 *
 * Null/undefined — an instantaneous event, or an entry recorded before durations existed — is the
 * empty string, so a caller renders its own "—" rather than this inventing "0s" for something nobody
 * measured. */
export const fmtElapsed = (ms: number | null | undefined): string => {
  if (ms == null || !Number.isFinite(ms)) return "";
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
};

/** A token count with digit grouping, or an em-dash when the run used no LLM at all.
 *
 *  NULL IS A FACT HERE, not a missing value: the mapper resolved that filing lexically and never
 *  called the model, which is a different thing from having called it and spent nothing. Printing
 *  "0" for it would report a cost that was never incurred as a measured zero. Lives beside
 *  `fmtElapsed` because two screens show this trail — one document's runs and the deployment's —
 *  and a second copy of this is where "—" quietly becomes "0" on one of them. */
export const fmtTokens = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : n.toLocaleString();

/** A run's duration, or an em-dash for something instantaneous (handing output to a reviewer) or
 *  for an entry recorded before the field existed. Never "0s" for an unmeasured run. */
export const fmtDuration = (ms: number | null | undefined): string => fmtElapsed(ms) || "—";

/** Indian-grouping accounting formatter: negatives in parentheses, e.g. 12,68,100
 * and (1,210). Used for read-only monetary display (statement conventions). */
export const fmtIN = (n: number | null | undefined): string => {
  if (n == null) return "";
  const s = Math.abs(n).toLocaleString("en-IN");
  return n < 0 ? `(${s})` : s;
};

/** Plain signed formatter (e.g. -1,210) for editable inputs where a raw value is
 * expected. Pairs with parseAccounting() on save. */
export const fmtPlain = (n: number | null | undefined): string =>
  n == null ? "" : n.toLocaleString("en-IN");

/** Parse a user-entered value: strips grouping/currency and reads parentheses or a
 * leading minus as negative. Returns null for empty/unparseable. */
export const parseAccounting = (raw: string): number | null => {
  if (raw == null) return null;
  let s = raw.trim();
  if (!s) return null;
  let negative = false;
  const paren = s.match(/^\((.*)\)$/);
  if (paren) {
    negative = true;
    s = paren[1];
  }
  s = s.replace(/[,₹\s]/g, "").replace(/[−–]/g, "-");
  if (s.startsWith("-")) {
    negative = true;
    s = s.slice(1);
  }
  if (s === "") return null;
  const num = Number(s);
  if (Number.isNaN(num)) return null;
  return negative ? -num : num;
};
