/** ONE LIST FOR "WHICH PRINTED CAPTIONS ARE THIS LINE", over the three fields that answered it.
 *
 *  WHAT THIS REPLACES. `aliases`, `regex_hints` and `keyword_hints` were three controls asking one
 *  question in three vocabularies, and the shipped set says so: 349 of 396 regex hints and 428 of
 *  635 keywords restated an alias on the same line. An author adding a caption had to choose a
 *  field first, and the fields are not obviously different — `aliases` matches the caption folded
 *  through `normalize_label`, `regex_hints` match the raw caption AND the folded one,
 *  `keyword_hints` require every word to be present somewhere in it.
 *
 *  AND NOBODY EVER NEEDED THE REGEX ONE. Measured by scanning all 389 shipped patterns token by
 *  token: 353 are a literal caption with whitespace or dash tolerance, 36 are a bare literal, and
 *  ZERO use regex power — no alternation, no character class that is not a dash variant, no `.`,
 *  no quantified group. What authors wrote in a regex field was "is exactly this caption, and be
 *  forgiving about the spaces", 389 times out of 389. So the field was never expressive, it was
 *  just harder to read: `^Accum\s+Deprec\s+\&\s+Impairment\([\-–—]?\)$` is
 *  "Accum Deprec & Impairment(-)".
 *
 *  THE ROW REMEMBERS ITS FIELD, and that is the invariant this whole component is built around.
 *  Reading the three fields into one list and writing them back UNEDITED must reproduce them
 *  byte for byte, because the fields are not interchangeable at match time: moving `^Land$` from
 *  `regex_hints` to `aliases` would stop it matching the raw caption, silently. So a row carries
 *  the field it came from, the displayed mode is DERIVED from that field plus the pattern's shape,
 *  and only an author changing the mode moves a row between fields — which is then the edit they
 *  asked for rather than a side effect of opening the form.
 *
 *  THE VETOES ARE NOT HERE. `exclude_hints` stays its own control, deliberately: every row in this
 *  list is positive evidence, and a mode dropdown that could also say "never match" would make one
 *  mis-click turn a match into a veto. That is the hazard the field names already warn about.
 */
import { useState } from "react";

import { FieldRow, helpStyle, inputStyle, smallBtn } from "./configFields";
import { Button } from "./ui";
import { color, font } from "../theme";

/** The three fields, and the one question each of them answers about a printed caption. */
export type RecognitionField = "aliases" | "regex_hints" | "keyword_hints";

export type RecognitionMode =
  | "exact"          // the caption IS this, folded              -> aliases
  | "exact_loose"    // …with tolerant spacing and dashes        -> regex_hints ^…$
  | "starts"         // the caption begins with this             -> regex_hints ^…
  | "ends"           // …or ends with it                         -> regex_hints …$
  | "contains"       // the caption contains this word           -> keyword_hints
  | "pattern";       // a raw pattern, kept as authored          -> regex_hints

export interface RecognitionRow {
  field: RecognitionField;
  /** Index within that field's own list, so a write can rebuild the three lists in order. */
  at: number;
  mode: RecognitionMode;
  /** What the author reads and edits — the pattern with its regex furniture removed. */
  text: string;
  /** The stored value, kept verbatim so an untouched row round-trips exactly. */
  raw: string;
}

const MODE_LABEL: Record<RecognitionMode, string> = {
  exact: "is exactly",
  exact_loose: "is exactly (any spacing)",
  starts: "starts with",
  ends: "ends with",
  contains: "contains the words",
  pattern: "matches the pattern",
};

/** Which field a mode writes to. The inverse of `modeOf`, and the only place the mapping lives. */
const FIELD_OF: Record<RecognitionMode, RecognitionField> = {
  exact: "aliases",
  exact_loose: "regex_hints",
  starts: "regex_hints",
  ends: "regex_hints",
  contains: "keyword_hints",
  pattern: "regex_hints",
};

const META = /[.^$*+?()[\]{}|\\]/;
const DASH_CLASS = /\[[-‐-―\s\\]+\][?*+]?/g;
const WHITESPACE = /\\s[+*]?/g;

/** A pattern's mode and its readable text.
 *
 *  ANCHORS DECIDE THE MODE and the rest is unwrapping. `^X$` is "is exactly", `^X` is "starts
 *  with", `X$` is "ends with", and a pattern with neither anchor is left as a pattern rather than
 *  guessed at — there are none in the shipped set, and inventing a mode for one would be the kind
 *  of inference this component exists to remove.
 */
export function modeOf(field: RecognitionField, raw: string): { mode: RecognitionMode; text: string } {
  if (field === "aliases") return { mode: "exact", text: raw };
  if (field === "keyword_hints") return { mode: "contains", text: raw };

  const head = raw.startsWith("^");
  const tail = raw.endsWith("$") && !raw.endsWith("\\$");
  const core = raw.slice(head ? 1 : 0, tail ? raw.length - 1 : raw.length);
  const plain = unescapePattern(core);
  if (plain === null) return { mode: "pattern", text: raw };
  if (head && tail) return { mode: "exact_loose", text: plain };
  if (head) return { mode: "starts", text: plain };
  if (tail) return { mode: "ends", text: plain };
  return { mode: "pattern", text: raw };
}

/** The literal a pattern expresses, or `null` when it expresses more than a literal.
 *
 *  Tolerance folds to a single space and an escaped character to itself; anything left that is a
 *  metacharacter means the pattern is doing something this editor must not paraphrase. */
function unescapePattern(core: string): string | null {
  const folded = core.replace(DASH_CLASS, "-").replace(WHITESPACE, " ");
  let out = "";
  for (let i = 0; i < folded.length; i++) {
    const ch = folded[i];
    if (ch === "\\") {
      const next = folded[i + 1];
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

/** The stored value for a mode and a plain text. The inverse of `modeOf`, so an author who changes
 *  a mode gets a pattern that means what the mode says. */
export function rawFor(mode: RecognitionMode, text: string): string {
  const t = text.trim();
  if (mode === "exact" || mode === "contains" || mode === "pattern") return t;
  // Escape every metacharacter, then let runs of whitespace match any spacing — which is what
  // every one of the 389 shipped patterns does.
  const escaped = t.replace(/[.^$*+?()[\]{}|\\]/g, (m) => `\\${m}`).replace(/\s+/g, "\\s+");
  if (mode === "exact_loose") return `^${escaped}$`;
  if (mode === "starts") return `^${escaped}`;
  return `${escaped}$`;
}

/** The three lists, read into one ordered list of rows. */
export function rowsOf(aliases: string[], regex: string[], keywords: string[]): RecognitionRow[] {
  const out: RecognitionRow[] = [];
  const add = (field: RecognitionField, values: string[]) =>
    values.forEach((raw, at) => out.push({ field, at, raw, ...modeOf(field, raw) }));
  add("aliases", aliases);
  add("regex_hints", regex);
  add("keyword_hints", keywords);
  return out;
}

/** …and back. A row whose `raw` is untouched writes its stored value verbatim, which is what makes
 *  opening the form and saving it a no-op. */
export function fieldsOf(rows: RecognitionRow[]): Record<RecognitionField, string[]> {
  const out: Record<RecognitionField, string[]> = {
    aliases: [], regex_hints: [], keyword_hints: [],
  };
  for (const row of rows) out[row.field].push(row.raw);
  return out;
}

export function RecognitionEditor({
  label, help, error, editable, testid, locale, aliases, regex, keywords, onChange, indexErrors,
}: {
  label: string;
  help?: React.ReactNode;
  error?: string;
  editable: boolean;
  testid: string;
  locale: string;
  aliases: string[];
  regex: string[];
  keywords: string[];
  onChange: (next: Record<RecognitionField, string[]>) => void;
  /** Per-field index errors, so a refused pattern still lands on its own row. */
  indexErrors?: Partial<Record<RecognitionField, Record<number, string>>>;
}) {
  const [draft, setDraft] = useState("");
  const [draftMode, setDraftMode] = useState<RecognitionMode>("exact");
  const rows = rowsOf(aliases, regex, keywords);

  const write = (next: RecognitionRow[]) => onChange(fieldsOf(next));

  const commit = () => {
    const t = draft.trim();
    setDraft("");
    if (!t) return;
    const field = FIELD_OF[draftMode];
    const raw = rawFor(draftMode, t);
    // A duplicate within the same field cannot change a match and makes a per-index refusal
    // ambiguous to attribute — the same rule the single-field editor applied.
    if (rows.some((r) => r.field === field && r.raw === raw)) return;
    write([...rows, { field, at: rows.length, raw, mode: draftMode, text: t }]);
  };

  const retype = (row: RecognitionRow, mode: RecognitionMode) => {
    const field = FIELD_OF[mode];
    const raw = mode === "pattern" ? row.raw : rawFor(mode, row.text);
    write(rows.map((r) => (r === row ? { ...r, field, mode, raw } : r)));
  };

  return (
    <FieldRow label={label} help={help} error={error} editable={editable} testid={testid}>
      <div style={{ border: `1px solid ${error ? color.redFg : color.hairline2}`,
                     borderRadius: 7, background: color.rowAltBg }}>
        {rows.length === 0 && (
          <div style={{ ...helpStyle, padding: "8px 10px" }}>
            Nothing listed — a configured empty, not a default. This line is then reachable only by
            its own label and by meaning.
          </div>
        )}
        {rows.map((row, i) => {
          const rowError = indexErrors?.[row.field]?.[row.at];
          return (
            <div key={`${row.field}-${row.at}-${row.raw}`}
                 data-testid={`recognition-row-${i}`}
                 style={{ padding: "5px 8px",
                           borderBottom: i === rows.length - 1 ? "none"
                                                               : `1px solid ${color.hairline}` }}>
              <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8,
                             alignItems: "center" }}>
                {editable ? (
                  <select value={row.mode}
                          aria-label={`How ${row.text} matches`}
                          data-testid={`recognition-mode-${i}`}
                          onChange={(e) => retype(row, e.target.value as RecognitionMode)}
                          style={{ ...inputStyle(true, false), padding: "3px 6px", fontSize: 11,
                                    width: "auto", cursor: "pointer" }}>
                    {(Object.keys(MODE_LABEL) as RecognitionMode[])
                      // `pattern` is offered only on a row that already IS one: it is the escape
                      // hatch for a pattern this editor will not paraphrase, not a mode to choose.
                      .filter((m) => m !== "pattern" || row.mode === "pattern")
                      .map((m) => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
                  </select>
                ) : (
                  <span style={{ fontSize: 11, color: color.sec2 }}>{MODE_LABEL[row.mode]}</span>
                )}
                <span style={{ fontFamily: row.mode === "pattern" ? font.mono : font.sans,
                                fontSize: row.mode === "pattern" ? 11 : 12,
                                color: rowError ? color.redFg : color.ink2,
                                lineHeight: 1.5, wordBreak: "break-word" }}>
                  {row.text}
                  {row.field === "aliases" && (
                    <span style={{ fontSize: 10, color: color.muted, marginInlineStart: 6 }}>
                      {locale}
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
                  aria-label="How the new caption matches"
                  data-testid={`recognition-new-mode-${testid}`}
                  onChange={(e) => setDraftMode(e.target.value as RecognitionMode)}
                  style={{ ...inputStyle(true, false), padding: "5px 8px", fontSize: 11,
                            width: "auto", cursor: "pointer" }}>
            {(Object.keys(MODE_LABEL) as RecognitionMode[])
              .filter((m) => m !== "pattern")
              .map((m) => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
          </select>
          <input value={draft}
                 placeholder="Paste a caption exactly as printed…"
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
                    onClick={() => onChange({ aliases: [], regex_hints: [], keyword_hints: [] })}
                    title="Store three empty lists — not a default">Clear all</Button>
          )}
        </div>
      )}
      <p style={{ fontSize: 10.5, color: color.muted, margin: "7px 0 0", lineHeight: 1.5 }}>
        <b>is exactly</b> stores the caption in <span style={{ fontFamily: font.mono }}>aliases</span>
        {" "}for <b style={{ fontFamily: font.mono }}>{locale}</b>;{" "}
        <b>any spacing</b>, <b>starts with</b> and <b>ends with</b> store a pattern in{" "}
        <span style={{ fontFamily: font.mono }}>regex_hints</span>; <b>contains</b> stores words in{" "}
        <span style={{ fontFamily: font.mono }}>keyword_hints</span>. Changing a row's mode moves it
        between those lists, and every row is positive evidence — the vetoes are the separate list
        below.
      </p>
    </FieldRow>
  );
}