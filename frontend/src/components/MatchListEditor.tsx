/** ONE LIST OVER SEVERAL FIELDS THAT ANSWER ONE QUESTION, each row saying HOW it matches.
 *
 *  WHAT THIS REPLACES, in two places on the Line Items form. Both had the same shape: a question
 *  an author has once, split across fields by the MECHANISM each field uses, so the first decision
 *  was always "which box does this go in" rather than "what is this caption".
 *
 *    which printed captions are this line?   `aliases` | `regex_hints` | `keyword_hints`
 *    which note holds it, which rows count,  `note_title_any` | `note_terms`,
 *    which rows are excluded?                `row_caption_any` | `row_terms`,
 *                                            `row_caption_none` | `row_terms_none`
 *
 *  The mechanisms are real and are kept — what goes is having to choose one up front. A row's mode
 *  names the mechanism in the author's words ("is exactly", "scored by meaning") and the component
 *  writes it to the field that implements it.
 *
 *  THE ROW REMEMBERS ITS FIELD, and every use of this component depends on it. The fields are not
 *  interchangeable at match time: `aliases` match a caption folded through `normalize_label` while
 *  `regex_hints` match the raw caption AND the folded one; a `*_any` regex MATCHES while a `*_terms`
 *  entry SCORES, so a phrasing nobody anticipated still ranks instead of silently matching nothing.
 *  A value that changed field on its own would change what it does. So reading the fields into one
 *  list and writing them back UNEDITED reproduces them byte for byte — checked against all 539
 *  shipped lines by `MatchListEditor.roundtrip.mjs` — and only an author changing a mode moves a
 *  row, which is then the edit they asked for.
 *
 *  A RAW PATTERN IS AN ESCAPE HATCH, NOT A MODE. `pattern` appears in the dropdown only on a row
 *  that already is one — a stored pattern this component will not paraphrase — and never as
 *  something to choose. Measured over the 389 shipped `regex_hints`, no row needs it: every one is
 *  a literal caption with forgiving spacing. It exists for the pattern somebody writes next.
 *
 *  VETOES ARE NEVER MIXED IN WITH MATCHES. Each use of this component covers rows that all point
 *  the same way — "these count" or "these are excluded", never both — because a mode dropdown
 *  offering both would make one mis-click invert the meaning of a row.
 */
import { useState } from "react";

import { FieldRow, WIDE_CONTROLS, helpStyle, inputStyle, smallBtn } from "./configFields";
import { Button } from "./ui";
import { color, font } from "../theme";

export type MatchMode =
  | "exact"          // the text IS this, folded through the matcher's normalisation
  | "exact_loose"    // …with tolerant spacing and dash variants        (anchored pattern)
  | "starts"         // it begins with this                             (^…)
  | "ends"           // …or ends with it                                (…$)
  | "contains"       // every one of these words appears somewhere in it
  | "scored"         // plain words SCORED against it, so an unanticipated wording still ranks
  | "pattern";       // a raw pattern, kept exactly as authored

export const MODE_LABEL: Record<MatchMode, string> = {
  exact: "is exactly",
  exact_loose: "is exactly (any spacing)",
  starts: "starts with",
  ends: "ends with",
  contains: "contains the words",
  scored: "scored by meaning",
  pattern: "matches the pattern",
};

/** WHAT EACH MODE ACTUALLY CATCHES, in a sentence with an example.
 *
 *  WHY THIS IS NOT OPTIONAL. The labels alone are a menu of near-synonyms: "is exactly" and "is
 *  exactly (any spacing)" read as the same thing, "contains the words" and "scored by meaning" read
 *  as the same thing, and nothing on the label says that the first four either FIRE OR DO NOT while
 *  the last two behave completely differently. An author choosing between them was guessing, and
 *  the measurement shows what guessing produced: 389 patterns written in a regex field where 389 of
 *  them meant "this caption, be forgiving about the spaces".
 *
 *  EVERY EXAMPLE IS A CAPTION FROM THE SHIPPED SET, and each says what the mode does NOT catch as
 *  well — which is the half that decides between two modes. */
const MODE_HELP: Record<MatchMode, string> = {
  exact:
    "The caption exactly as the filing prints it, word for word. “Land use rights” matches that "
    + "heading and nothing else — not “Prepaid land lease”, and not “Land use rights, net”.",
  exact_loose:
    "The same, but forgiving about spacing, hyphens and dash characters. “Accum Deprec & "
    + "Impairment(-)” still matches when a filing prints two spaces, an en-dash or a full-width "
    + "bracket. Still the WHOLE caption — nothing longer matches.",
  starts:
    "The caption begins with this. “Depreciation” catches “Depreciation of property, plant and "
    + "equipment” and “Depreciation and amortisation”, but NOT “Accumulated depreciation”, because "
    + "that does not start with it.",
  ends:
    "The caption ends with this. “(Gross)” catches “Trade Receivables(Gross)”. Useful for the "
    + "qualifier a template appends and the filing may or may not.",
  contains:
    "Every one of these words appears somewhere in the caption, in any order. “trade receivable” "
    + "catches “Net trade receivables” and “Trade and other receivables from related parties”. The "
    + "widest of the matching modes, and the easiest to make too wide.",
  scored:
    "Plain words, RANKED rather than matched. Nothing fires or fails: a heading sharing most of "
    + "these words ranks high, one sharing none ranks low, and the best few are used. This is what "
    + "reaches a filing that words the line in a way nobody listed — which is why a note is found "
    + "by meaning and a row is pinned by matching.",
  pattern:
    "A raw search pattern, shown exactly as stored, because this screen will not rewrite one it "
    + "cannot read back safely. It appears only on a row that already IS one — every other mode "
    + "writes the pattern for you. Of the 389 patterns in the shipped set, none needed this.",
};

/** Which field each mode writes to. Only the modes present here are offered. */
export type ModeMap = Partial<Record<MatchMode, string>>;

export interface MatchRow {
  field: string;
  /** Index within that field's own list, so an error can still land on its own row. */
  at: number;
  mode: MatchMode;
  /** What the author reads and edits — the pattern with its regex furniture removed. */
  text: string;
  /** The stored value, verbatim, so an untouched row round-trips exactly. */
  raw: string;
}

const META = /[.^$*+?()[\]{}|\\]/;
const DASH_CLASS = /\[[-‐-―\s\\]+\][?*+]?/g;
const WHITESPACE = /\\s[+*]?/g;

/** The literal a pattern expresses, or `null` when it expresses more than a literal. */
function literalOf(core: string): string | null {
  const folded = core.replace(DASH_CLASS, "-").replace(WHITESPACE, " ");
  let out = "";
  for (let i = 0; i < folded.length; i++) {
    const ch = folded[i];
    if (ch === "\\") {
      const next = folded[i + 1];
      // `\b`, `\d`, `\w` are regex power; `\&`, `\(`, `\.` are escaped literals.
      if (next === undefined || /[A-Za-z0-9]/.test(next)) return null;
      out += next;
      i++;
      continue;
    }
    if (META.test(ch)) return null;
    out += ch;
  }
  return out;
}

/** A stored value's mode and its readable text, given which field it came from.
 *
 *  ANCHORS DECIDE A PATTERN'S MODE and the rest is unwrapping. A pattern with NEITHER anchor stays
 *  a pattern rather than being called "contains": the two are not the same question, and guessing
 *  would be the kind of inference this component exists to remove.
 */
export function modeOf(field: string, raw: string, map: ModeMap): { mode: MatchMode; text: string } {
  if (field === map.exact) return { mode: "exact", text: raw };
  if (field === map.contains) return { mode: "contains", text: raw };
  if (field === map.scored) return { mode: "scored", text: raw };

  const head = raw.startsWith("^");
  const tail = raw.endsWith("$") && !raw.endsWith("\\$");
  const plain = literalOf(raw.slice(head ? 1 : 0, tail ? raw.length - 1 : raw.length));
  if (plain === null) return { mode: "pattern", text: raw };
  if (head && tail) return { mode: "exact_loose", text: plain };
  if (head) return { mode: "starts", text: plain };
  if (tail) return { mode: "ends", text: plain };
  return { mode: "pattern", text: raw };
}

/** The stored value for a mode and a plain text — the inverse of `modeOf`, so an author who
 *  changes a mode gets a value that means what the mode says. */
export function rawFor(mode: MatchMode, text: string): string {
  const t = text.trim();
  if (mode === "exact" || mode === "contains" || mode === "scored" || mode === "pattern") return t;
  const escaped = t.replace(/[.^$*+?()[\]{}|\\]/g, (m) => `\\${m}`).replace(/\s+/g, "\\s+");
  if (mode === "exact_loose") return `^${escaped}$`;
  if (mode === "starts") return `^${escaped}`;
  return `${escaped}$`;
}

/** The fields, read into one ordered list of rows. `order` fixes which field's rows come first, so
 *  the list does not reshuffle when a row moves between fields. */
export function rowsOf(values: Record<string, string[]>, order: string[],
                       map: ModeMap): MatchRow[] {
  const out: MatchRow[] = [];
  for (const field of order) {
    (values[field] ?? []).forEach((raw, at) =>
      out.push({ field, at, raw, ...modeOf(field, raw, map) }));
  }
  return out;
}

/** …and back. Every field in `order` is written, so clearing one stores an empty list rather than
 *  leaving the previous value in place.
 *
 *  A ROW OUTSIDE `order` WOULD BE LOST AT THE CALLER, which is why the component derives `order`
 *  from the map rather than trusting the two to agree. The caller's `onChange` reads the fields it
 *  knows by name (`patch({ aliases: next.aliases, … })`), so a key this function invented would be
 *  dropped on the way to the server without anything failing. Keeping the row in the result at
 *  least makes the mismatch visible to a caller that spreads the object. */
export function fieldsOf(rows: MatchRow[], order: string[]): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const field of order) out[field] = [];
  for (const row of rows) (out[row.field] ??= []).push(row.raw);
  return out;
}

export function MatchListEditor({
  label, help, error, editable, testid, map, order, values, onChange, indexErrors, emptyText,
  suffixOf, veto,
}: {
  label: string;
  help?: React.ReactNode;
  error?: string;
  editable: boolean;
  testid: string;
  /** Mode -> field. Only these modes are offered, in this object's order. */
  map: ModeMap;
  /** The fields this control owns, in the order their rows are listed. */
  order: string[];
  values: Record<string, string[]>;
  onChange: (next: Record<string, string[]>) => void;
  indexErrors?: Record<string, Record<number, string>>;
  emptyText?: string;
  /** A per-field note beside a row — used for the alias locale. */
  suffixOf?: (field: string) => string | undefined;
  /** Styles the rows as exclusions. The MODES are unchanged: a control is entirely one or the
   *  other, never a list where a dropdown could flip a row's polarity. */
  veto?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const offered = (Object.keys(MODE_LABEL) as MatchMode[]).filter((m) => map[m] && m !== "pattern");
  const [draftMode, setDraftMode] = useState<MatchMode>(offered[0] ?? "exact");
  // `order` PLUS ANY FIELD THE MAP NAMES THAT IT OMITS, so a mode can never write to a field this
  // component then fails to read back. The four call sites list the same fields in both, and this
  // makes a fifth that does not a display-order oddity rather than silent data loss.
  const fields = [...order, ...Object.values(map).filter((f): f is string =>
    !!f && !order.includes(f))];
  const rows = rowsOf(values, fields, map);
  const fg = veto ? color.redFg : color.ink2;

  const write = (next: MatchRow[]) => onChange(fieldsOf(next, fields));

  const commit = () => {
    const t = draft.trim();
    setDraft("");
    if (!t) return;
    const field = map[draftMode];
    if (!field) return;
    const raw = rawFor(draftMode, t);
    // A duplicate within the same field cannot change anything and makes a per-index refusal
    // ambiguous to attribute.
    if (rows.some((r) => r.field === field && r.raw === raw)) return;
    write([...rows, { field, at: rows.length, raw, mode: draftMode, text: t }]);
  };

  const retype = (row: MatchRow, mode: MatchMode) => {
    const field = map[mode];
    if (!field) return;
    write(rows.map((r) => (r === row
      ? { ...r, field, mode, raw: mode === "pattern" ? r.raw : rawFor(mode, r.text) }
      : r)));
  };

  return (
    <FieldRow label={label} help={help} error={error} editable={editable} testid={testid}>
      <div style={{ border: `1px solid ${error ? color.redFg : color.hairline2}`,
                     borderRadius: 7, background: color.rowAltBg }}>
        {rows.length === 0 && (
          <div style={{ ...helpStyle, padding: "8px 10px" }}>
            {emptyText ?? "Nothing listed — a configured empty, not a default."}
          </div>
        )}
        {rows.map((row, i) => {
          const rowError = indexErrors?.[row.field]?.[row.at];
          const suffix = suffixOf?.(row.field);
          return (
            <div key={`${row.field}-${row.at}-${row.raw}`}
                 data-testid={`${testid}-row-${i}`}
                 style={{ padding: "5px 8px",
                           borderBottom: i === rows.length - 1 ? "none"
                                                               : `1px solid ${color.hairline}` }}>
              <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8,
                             alignItems: "center" }}>
                {editable ? (
                  <select value={row.mode}
                          aria-label={`How ${row.text} matches`}
                          data-testid={`${testid}-mode-${i}`}
                          onChange={(e) => retype(row, e.target.value as MatchMode)}
                          style={{ ...inputStyle(true, false), padding: "3px 6px", fontSize: 11,
                                    width: "auto", cursor: "pointer" }}>
                    {[...offered, ...(row.mode === "pattern" ? ["pattern" as MatchMode] : [])]
                      .map((m) => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
                  </select>
                ) : (
                  <span style={{ fontSize: 11, color: color.sec2 }}>{MODE_LABEL[row.mode]}</span>
                )}
                <span style={{ fontFamily: row.mode === "pattern" ? font.mono : font.sans,
                                fontSize: row.mode === "pattern" ? 11 : 12,
                                color: rowError ? color.redFg : fg,
                                lineHeight: 1.5, wordBreak: "break-word" }}>
                  {row.text}
                  {suffix && (
                    <span style={{ fontSize: 10, color: color.muted, marginInlineStart: 6 }}>
                      {suffix}
                    </span>
                  )}
                </span>
                {editable && (
                  <Button variant="secondary" style={smallBtn} ariaLabel={`Remove ${row.text}`}
                          onClick={() => write(rows.filter((r) => r !== row))}>×</Button>
                )}
              </div>
              {rowError && (
                <div style={{ fontSize: 10.5, color: color.redFg, marginTop: 3 }}>{rowError}</div>
              )}
            </div>
          );
        })}
      </div>
      {editable && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 7,
                       flexWrap: "wrap" }}>
          <select value={draftMode}
                  aria-label={`How the new entry for ${label} matches`}
                  data-testid={`${testid}-new-mode`}
                  onChange={(e) => setDraftMode(e.target.value as MatchMode)}
                  style={{ ...inputStyle(true, false), padding: "5px 8px", fontSize: 11,
                            width: "auto", cursor: "pointer" }}>
            {offered.map((m) => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
          </select>
          <input value={draft}
                 data-testid={`add-${testid}`}
                 aria-label={`Add to ${label}`}
                 onChange={(e) => setDraft(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }}
                 onBlur={commit}
                 style={{ ...inputStyle(true, false), flex: "1 1 190px", minWidth: 150,
                           padding: "5px 10px", border: `1px dashed ${color.dashed}` }} />
          {rows.length > 0 && (
            <Button variant="secondary" style={smallBtn}
                    testid={`clear-${testid}`}
                    onClick={() => onChange(fieldsOf([], fields))}
                    title="Store empty lists — not a default">Clear all</Button>
          )}
        </div>
      )}
      {editable && (
        <p data-testid={`mode-help-${testid}`}
           style={{ ...helpStyle, margin: "6px 0 0", lineHeight: 1.55 }}>
          <b>{MODE_LABEL[draftMode]}</b>{" — "}{MODE_HELP[draftMode]}
        </p>
      )}
      {editable && map.scored && (
        <p style={{ ...helpStyle, margin: "5px 0 0", lineHeight: 1.55 }}>
          The first modes MATCH — a caption either satisfies one or it does not, and a caption
          nobody anticipated satisfies none of them. <b>Scored by meaning</b> RANKS instead, so an
          unexpected wording still comes out somewhere rather than nowhere. Most lines want both:
          matching for the wordings you know, scoring for the ones you do not.
        </p>
      )}
    </FieldRow>
  );
}

// THE CAPTION LIST NEEDS THE WHOLE WIDTH TOO — a row is a mode select plus a pattern, and at half
// width the pattern is the half that clips. Registered from here rather than listed in
// `configFields.WIDE_CONTROLS` itself, because that module does not import this one.
WIDE_CONTROLS.add(MatchListEditor);
