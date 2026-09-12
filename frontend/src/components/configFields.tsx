/** Field primitives for the CONFIGURATION editors — one control per kind of configured value.
 *
 * WHY THIS FILE EXISTS. The Line Items screen was read-only on a justification that expired: the
 * definitions used to describe derivations that services computed, so showing them was the whole
 * job. They now drive extraction (the matcher is built from the set, and a run pins
 * `extraction_runs.line_item_version_id`), which makes the screen an editor over ~70 authorable
 * fields. Inlining seventy controls into the detail pane produces 700 lines of JSX in which every
 * control is spelled slightly differently — and the Template screen already has its own second
 * spelling of the same chips-and-a-select ideas. So the controls live here, once, and both screens
 * read them.
 *
 * WHAT EVERY CONTROL IN HERE HAS TO GET RIGHT, because the configuration model says so:
 *
 *  1. ABSENT, NULL AND EMPTY ARE THREE DIFFERENT STATEMENTS. The edit endpoint reads PRESENCE
 *     (`model_fields_set`), so an untouched field must not be sent, `null` means "nothing was
 *     said", and `[]`/`""` is a CONFIGURED EMPTY that is stored and never re-defaulted. An author
 *     who cannot clear a list cannot undo their own edit, which is why every list control has a
 *     "Clear all" that yields `[]` rather than leaving the last chip undeletable, and why
 *     `NumberField` offers "nothing said" as a state of its own — `match_priority`'s 0 is the
 *     residual floor, a real and different assertion.
 *  2. A REFUSAL BELONGS TO A CONTROL. The endpoint re-validates against the target template before
 *     it publishes, so a 422 is information the author needs. Every control takes `error` and
 *     prints the SERVER'S sentence verbatim under itself — a paraphrase in the client is a second
 *     spelling of a rule the server owns. List controls take errors per index, because
 *     `regex_hints[2]` is one bad row, not a bad list.
 *  3. AN INHERITED VALUE IS NOT A DECLARED ONE. 475 of 475 shipped items take their gate from
 *     `section_defaults` via `inherits`, and once folded an inherited value is indistinguishable
 *     from a declared one. Saving such a field turns it into a declared one and silently detaches
 *     the item from its section, so `inherited` renders the badge that says so before the author
 *     types.
 *  4. NOT AUTHORABLE ≠ ABSENT. A field withheld on purpose (`key`, `children`, `aliases_i18n`, the
 *     withdrawn `min_confidence_to_auto_accept`) is rendered by `LockedRow` WITH its one-line
 *     reason. Silently absent and read-only-for-a-reason look identical on a screen and only one
 *     of them is a decision.
 *
 * Visual language: `Card`, `Button`, `Segmented` and `Toggle` from components/ui.tsx and the
 * `color`/`font`/`radius` tokens. Nothing new — the detail pane these sit in is already built.
 *
 * Copy is English in place, as on the Line Items screen it serves (that screen localizes only
 * `common.loading`); every label and help sentence is passed IN by the caller, so the field names
 * and their explanations stay next to the field table that decided them.
 *
 * TESTIDS: `FieldRow` stamps `data-testid="field-<name>"` when given `testid`. `Segmented` stamps
 * `seg-<option>` on its own options, which repeats across a form with several segmented controls —
 * address one through its field wrapper (`[data-testid="field-face_only"] [data-testid="seg-true"]`)
 * rather than expecting `seg-true` to be unique on the screen.
 */
import { useState, type CSSProperties, type ReactNode } from "react";

import { Button, Card, Segmented, Toggle } from "./ui";
import { color, font, radius } from "../theme";
import type { CascadeRung, LineItemTerm, TermRole } from "../types";

/* ── shared shell ───────────────────────────────────────────────────────────────────────────── */

/** What every control in this file takes. Shared as one type so a new control cannot quietly
 *  omit the error or the inherited badge and look finished. */
export interface FieldProps {
  /** The field as the author reads it. */
  label: string;
  /** One or two sentences on what the field CONTROLS — the consequence, not the datatype. */
  help?: ReactNode;
  /** False renders the value read-only. Pair it with `reason` or a `LockedRow` so the author is
   *  never left guessing whether the control is broken or the field is withheld. */
  editable: boolean;
  /** The server's own message for this field, verbatim. Printed in red under the control. */
  error?: string;
  /** The `section_defaults` entry this value was folded in from, when the item did not declare
   *  it itself. Renders the override warning. */
  inherited?: string;
  /** Stamped as `data-testid="field-<testid>"` on the wrapper. Use the FIELD NAME. */
  testid?: string;
}

const labelStyle: CSSProperties = { fontSize: 11.5, fontWeight: 600, color: color.ink2 };
const helpStyle: CSSProperties = { fontSize: 10.5, color: color.muted, lineHeight: 1.5 };

/** The one input shell. `invalid` puts the server's refusal on the control itself, because a red
 *  sentence eight fields down the form is not attributed to anything a reader can see. */
function inputStyle(editable: boolean, invalid?: boolean, mono?: boolean): CSSProperties {
  return {
    width: "100%", boxSizing: "border-box", fontSize: 12, lineHeight: 1.5,
    fontFamily: mono ? font.mono : font.sans,
    color: editable ? color.ink : color.sec,
    background: editable ? color.surface : color.rowAltBg,
    border: `1px solid ${invalid ? color.redFg : color.controlBorder}`,
    borderRadius: radius.controlSm, padding: "7px 10px", outline: "none",
  };
}

const smallBtn: CSSProperties = { fontSize: 11, fontWeight: 600, padding: "4px 9px",
                                  borderRadius: radius.controlSm };

/** A one-line note in the tone of the thing it is warning about. */
function Note({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "warn" | "veto" }) {
  const fg = tone === "warn" ? color.amberFg : tone === "veto" ? color.redFg : color.muted;
  return <div style={{ ...helpStyle, color: fg, marginTop: 4 }}>{children}</div>;
}

/** THE ⓘ TOGGLE. Every field's explanation hides behind one of these.
 *
 *  WHY. The explanations are the most valuable thing on this form and they were also what made it
 *  unusable: two sentences under each of forty-odd controls turns a form into a document, and an
 *  author scrolling to find one field reads three screens of prose they already know. Collapsed,
 *  the same form is a short list of labels; the prose is one click away and unchanged.
 *
 *  A DISCLOSURE, NOT A TOOLTIP, and deliberately. A hover tooltip is unreachable on a touch device,
 *  vanishes while you read it, and cannot be kept open beside the control you are editing — and
 *  these particular explanations are the ones that stop an author detaching a line from its
 *  section. They have to be readable at leisure, so the text lands in the flow and stays until it
 *  is dismissed.
 */
export function InfoToggle({ open, onToggle, about }: {
  open: boolean; onToggle: () => void;
  /** The field's label, so the control announces WHICH field it explains rather than just "info". */
  about: string;
}) {
  return (
    <button type="button" onClick={onToggle} aria-expanded={open}
            data-testid="field-info"
            aria-label={open ? `Hide what ${about} does` : `What does ${about} do?`}
            title={open ? "Hide the explanation" : "What does this do?"}
            style={{
              flex: "0 0 auto", width: 15, height: 15, padding: 0, lineHeight: "13px",
              fontSize: 10, fontWeight: 700, fontFamily: font.sans, cursor: "pointer",
              borderRadius: "50%", border: `1px solid ${open ? color.indigo : color.controlBorder}`,
              background: open ? color.indigo : "transparent",
              color: open ? color.surface : color.muted,
            }}>
      i
    </button>
  );
}

/** THE WRAPPER EVERY CONTROL RENDERS INSIDE: the label, the control, the server's message in red
 *  under it, and the inherited badge.
 *
 *  It deliberately has no `value`/`onChange` of its own — a wrapper that carried them would be a
 *  second, weaker version of the controls below, and the one thing this file is for is that there
 *  is exactly one spelling of each control. The control is `children`; everything the control does
 *  NOT own (naming itself, explaining itself, reporting a refusal, disclosing that the value came
 *  from a section) is owned here so no control can forget it. */
export function FieldRow({
  label, help, error, inherited, editable, testid, children, reason,
}: FieldProps & {
  children: ReactNode;
  /** Why the control is read-only, when it is. Shown instead of nothing — see the header. */
  reason?: string;
}) {
  // Collapsed by default — see `InfoToggle`. Per row rather than one flag for the form, so opening
  // one explanation does not unfold the other forty.
  const [showHelp, setShowHelp] = useState(false);
  return (
    <div data-testid={testid ? `field-${testid}` : undefined}
         style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap",
                     marginBottom: 4 }}>
        <span style={labelStyle}>{label}</span>
        {help && <InfoToggle open={showHelp} about={label}
                             onToggle={() => setShowHelp((v) => !v)} />}
        {inherited && <InheritedBadge from={inherited} />}
        {/* NOT behind the toggle. A read-only reason and a server refusal are not explanations of
            what the field does — they are the answers to "why can I not use this", asked by
            someone already looking at it. Hiding either leaves an author with a dead control and
            no account of it. */}
        {!editable && reason && (
          <span style={{ ...helpStyle, color: color.amberFg }}>{reason}</span>
        )}
      </div>
      {help && showHelp && (
        <div style={{ ...helpStyle, marginBottom: 6, paddingLeft: 9,
                       borderLeft: `2px solid ${color.indigoBorder2}` }}>
          {help}
        </div>
      )}
      {children}
      {error && (
        <div data-testid="field-error"
             style={{ fontSize: 11, color: color.redFg, lineHeight: 1.5, marginTop: 5 }}>
          {error}
        </div>
      )}
    </div>
  );
}

/** "from section <id>", and what typing here would do about it.
 *
 *  The whole gate arrives by inheritance in the shipped set, so nearly every gate control carries
 *  this. Saying it as a badge and a sentence — rather than as a tooltip — is the difference between
 *  an author knowing they are about to detach a line from its section and finding out later that
 *  475 items no longer agree about anything. */
export function InheritedBadge({ from }: { from: string }) {
  return (
    <span title="editing this here overrides the section default"
          style={{ display: "inline-flex", alignItems: "baseline", gap: 5, fontSize: 10,
                    background: color.segBg, color: color.sec2, borderRadius: radius.chip,
                    padding: "2px 6px" }}>
      <span>from section</span>
      <b style={{ fontFamily: font.mono }}>{from}</b>
      <span style={{ color: color.muted }}>— editing this here overrides the section default</span>
    </span>
  );
}

/** A field that is NOT authorable here, shown with the reason it is not.
 *
 *  Every entry the server serves under `LineItemVocab.not_editable` gets one of these: `key` (the
 *  identity every other declaration names, and the PATCH's own selector), `children` (a projection
 *  of `parent` recomputed on every read, so an edit to it cannot be persisted), `aliases_i18n` (a
 *  map-shaped write is precisely how editing Chinese clobbers English) and the withdrawn
 *  `min_confidence_to_auto_accept`. `value` is a node so the children row can carry navigation to
 *  the child's own detail — where the edit IS possible. */
export function LockedRow({ label, value, reason, mono = true, testid }: {
  label: string; value: ReactNode; reason: string; mono?: boolean; testid?: string;
}) {
  return (
    <div data-testid={testid ? `locked-${testid}` : undefined}
         style={{ display: "grid", gridTemplateColumns: "minmax(120px, 30%) 1fr", gap: "2px 12px",
                   padding: "7px 0", borderBottom: `1px solid ${color.hairline}` }}>
      <span style={{ ...labelStyle, color: color.sec }}>{label}</span>
      <span style={{ fontFamily: mono ? font.mono : font.sans, fontSize: 11.5, color: color.ink2,
                      wordBreak: "break-word" }}>
        {value === null || value === undefined || value === "" ? (
          <span style={{ color: color.muted, fontFamily: font.sans }}>nothing set</span>
        ) : value}
      </span>
      <span />
      <span style={helpStyle}>{reason}</span>
    </div>
  );
}

/* ── scalars ────────────────────────────────────────────────────────────────────────────────── */

/** A single-line string: `label`, `pattern`, `implemented_by`, `cascade[].id`, and the two free
 *  strings inside `residual_policy`.
 *
 *  `datalist` offers what the set already uses without CLOSING the set — `residual_policy.framework`
 *  and `population` are free strings on the model, and a select over "what exists today" is how an
 *  author is stopped from declaring the first of something. */
export function TextField({
  label, help, error, inherited, editable, testid, value, onChange, placeholder, mono, datalist,
  reason, monoNote,
}: FieldProps & {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  /** For a regex or a key — read literally, so shown literally. */
  mono?: boolean;
  datalist?: readonly string[];
  reason?: string;
  /** A line under the box: a live regex compile result, for instance. */
  monoNote?: ReactNode;
}) {
  const listId = datalist && testid ? `dl-${testid}` : undefined;
  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid} reason={reason}>
      {editable ? (
        <>
          <input
            value={value}
            list={listId}
            placeholder={placeholder}
            data-testid={testid ? `input-${testid}` : undefined}
            onChange={(e) => onChange(e.target.value)}
            style={inputStyle(true, !!error, mono)}
          />
          {listId && (
            <datalist id={listId}>
              {datalist!.map((d) => <option key={d} value={d} />)}
            </datalist>
          )}
        </>
      ) : (
        <div style={inputStyle(false, !!error, mono)}>
          {value || <span style={{ color: color.muted, fontFamily: font.sans }}>nothing set</span>}
        </div>
      )}
      {monoNote}
    </FieldRow>
  );
}

/** Prose: `description`, `definition`, and the six nullable notes.
 *
 *  `nullable` is not decoration. On the wire a prose field is either `""` — a configured empty,
 *  stored — or `null`, "nothing was said". The nullable prose fields are declared `string | null`
 *  precisely so that distinction survives, so an emptied box on one of them yields `null` and on
 *  `description`/`definition` yields `""`. The callback is typed `string | null` for both; a caller
 *  editing a non-nullable field writes `(v) => patch({ description: v ?? "" })`. */
export function TextArea({
  label, help, error, inherited, editable, testid, value, onChange, rows = 3, placeholder,
  nullable, reason,
}: FieldProps & {
  value: string | null;
  onChange: (v: string | null) => void;
  rows?: number;
  placeholder?: string;
  nullable?: boolean;
  reason?: string;
}) {
  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid} reason={reason}>
      {editable ? (
        <textarea
          value={value ?? ""}
          rows={rows}
          placeholder={placeholder}
          data-testid={testid ? `input-${testid}` : undefined}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(nullable && raw === "" ? null : raw);
          }}
          style={{ ...inputStyle(true, !!error), resize: "vertical", lineHeight: 1.55 }}
        />
      ) : (
        <p style={{ margin: 0, fontSize: 12, lineHeight: 1.55,
                     color: value ? color.sec : color.muted }}>
          {value || (nullable ? "nothing was said" : "nothing set")}
        </p>
      )}
    </FieldRow>
  );
}

/** A number, with "nothing said" as a STATE and not as an empty box.
 *
 *  `match_priority` is why: `null` is "no tie-break was declared" and `0` is the residual floor —
 *  a real assertion that puts the line out of reach of matching altogether. A box that treats an
 *  empty string as 0 collapses those two, and the author who cleared the field gets a residual.
 *  So when `allowNull` is set the control is a two-way segmented choice and the number input only
 *  exists in the second state; switching INTO it writes nothing until a digit is typed, because
 *  defaulting to 0 there would assert the floor on the author's behalf.
 *
 *  `order` is the non-nullable case (`allowNull` off): an emptied box is held as a local draft and
 *  no value is sent, rather than sending a 0 nobody typed. */
export function NumberField({
  label, help, error, inherited, editable, testid, value, onChange, allowNull, min, step,
  nullLabel = "nothing said", numberLabel = "a number", zeroNote, reason,
}: FieldProps & {
  value: number | null;
  onChange: (v: number | null) => void;
  allowNull?: boolean;
  min?: number;
  step?: number;
  nullLabel?: string;
  numberLabel?: string;
  /** Printed when the stored value is exactly 0, for the fields where 0 means something. */
  zeroNote?: string;
  reason?: string;
}) {
  // A local draft so "-" and "" are typeable without the parent seeing a value nobody meant. It is
  // dropped on blur, at which point the stored value is what shows.
  const [draft, setDraft] = useState<string | null>(null);
  // Whether the author has asked for a number at all. Seeded from the value and then owned here:
  // it has to survive the moment between "I want a number" and typing the first digit.
  const [wantNumber, setWantNumber] = useState(value !== null);
  const text = draft ?? (value === null ? "" : String(value));
  const showInput = !allowNull || wantNumber || value !== null;

  const commit = (raw: string) => {
    setDraft(raw);
    const trimmed = raw.trim();
    if (trimmed === "" || trimmed === "-") {
      if (allowNull) onChange(null);
      return;                                    // non-nullable: hold the draft, send nothing
    }
    const n = Number(trimmed);
    if (Number.isFinite(n)) onChange(n);
  };

  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid} reason={reason}>
      {editable && allowNull && (
        <div style={{ marginBottom: showInput ? 6 : 0, display: "inline-block" }}>
          <Segmented<"unset" | "set">
            options={[{ value: "unset", label: nullLabel }, { value: "set", label: numberLabel }]}
            value={value === null && !wantNumber ? "unset" : "set"}
            onChange={(v) => {
              if (v === "unset") { setWantNumber(false); setDraft(null); onChange(null); }
              else setWantNumber(true);
            }}
          />
        </div>
      )}
      {showInput && (editable ? (
        <input
          type="number"
          inputMode="numeric"
          value={text}
          min={min}
          step={step}
          data-testid={testid ? `input-${testid}` : undefined}
          onChange={(e) => commit(e.target.value)}
          onBlur={() => setDraft(null)}
          style={{ ...inputStyle(true, !!error, true), maxWidth: 160 }}
        />
      ) : (
        <div style={{ ...inputStyle(false, !!error, true), maxWidth: 160 }}>
          {value === null
            ? <span style={{ color: color.muted, fontFamily: font.sans }}>{nullLabel}</span>
            : value}
        </div>
      ))}
      {editable && allowNull && wantNumber && value === null && (
        <Note tone="warn">Type a number, or this stays “{nullLabel}”.</Note>
      )}
      {value === 0 && zeroNote && <Note tone="warn">{zeroNote}</Note>}
    </FieldRow>
  );
}

/** A two-state boolean — the ones that genuinely have two states.
 *
 *  `disabledReason` is for `in_output` under `type: intermediate`, which `_coherent` forces false:
 *  the control stays on screen, off, and says why. A toggle that vanished when the type changed
 *  would read as the field having been taken away. `tone="warn"` is for
 *  `residual_policy.plug`, the one flag here whose consequence is that a figure always ties and
 *  therefore hides the mapping gap it papers over. */
export function BoolField({
  label, help, error, inherited, editable, testid, value, onChange, onText, offText,
  disabledReason, tone,
}: FieldProps & {
  value: boolean;
  onChange: (v: boolean) => void;
  /** What being on / off MEANS, printed under the switch for the current state. */
  onText?: string;
  offText?: string;
  disabledReason?: string;
  tone?: "warn";
}) {
  const live = editable && !disabledReason;
  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={live}
              testid={testid} reason={disabledReason}>
      <span
        role="switch"
        aria-checked={value}
        aria-label={label}
        tabIndex={live ? 0 : -1}
        data-testid={testid ? `toggle-${testid}` : undefined}
        onClick={live ? () => onChange(!value) : undefined}
        onKeyDown={live ? (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onChange(!value); }
        } : undefined}
        style={{ display: "inline-flex", alignItems: "center", gap: 8,
                  cursor: live ? "pointer" : "not-allowed", opacity: live ? 1 : 0.6 }}
      >
        <Toggle on={value} />
        <span style={{ fontSize: 11.5, color: color.sec }}>{value ? "yes" : "no"}</span>
      </span>
      {(value ? onText : offText) && (
        <Note tone={tone === "warn" && value ? "warn" : "muted"}>{value ? onText : offText}</Note>
      )}
    </FieldRow>
  );
}

/** THREE states, because the field has three: `face_only`, and any other boolean where `null` is
 *  "nothing was said".
 *
 *  A checkbox over `face_only` asserts a policy the v1 sets were never written for — they simply
 *  never expressed it, and rendering that as `false` claims every one of them decided a note may
 *  be a source. */
export function TriBoolField({
  label, help, error, inherited, editable, testid, value, onChange,
  trueLabel = "yes", falseLabel = "no", nullLabel = "nothing said", stateHelp,
}: FieldProps & {
  value: boolean | null;
  onChange: (v: boolean | null) => void;
  trueLabel?: string;
  falseLabel?: string;
  nullLabel?: string;
  /** What each of the three states means, keyed by state. Printed for the current one. */
  stateHelp?: { yes?: string; no?: string; unset?: string };
}) {
  const token = value === null ? "unset" : value ? "yes" : "no";
  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid}>
      {editable ? (
        <div style={{ display: "inline-block" }}>
          <Segmented<"yes" | "no" | "unset">
            options={[{ value: "yes", label: trueLabel }, { value: "no", label: falseLabel },
                      { value: "unset", label: nullLabel }]}
            value={token}
            onChange={(v) => onChange(v === "unset" ? null : v === "yes")}
          />
        </div>
      ) : (
        <div style={{ fontSize: 12, color: value === null ? color.muted : color.ink }}>
          {value === null ? nullLabel : value ? trueLabel : falseLabel}
        </div>
      )}
      {stateHelp?.[token] && <Note>{stateHelp[token]}</Note>}
    </FieldRow>
  );
}

/** A closed vocabulary, built from what the SERVER served.
 *
 *  The options come from `LineItemVocab`, which the backend derives from the same `Literal[...]`
 *  aliases the loader validates with. A control offering a value the publish gate refuses is worse
 *  than no control: the author authors, saves, and is told no by a validator two layers down. So
 *  nothing in here hardcodes a token list.
 *
 *  `nullable` adds the "nothing said" entry for the genuinely nullable enums (`statement`,
 *  `note_use`, `temporality`, `unit_of_account`, `sign_convention`, `analyst_bucket`) — and only
 *  for those, because offering it on `type` would mean an item with no type.
 *
 *  `helpOf` prints EVERY option's consequence, not just the chosen one — `rollup` and
 *  `terms[].role` are choices that are only meaningful against the alternatives, and choosing
 *  `sum` where the parts are alternative restatements of one figure double-counts it. */
export function SelectField<T extends string>({
  label, help, error, inherited, editable, testid, value, onChange, options, labelOf, helpOf,
  nullable, nullLabel = "nothing was said", reason,
}: FieldProps & {
  value: T | null;
  onChange: (v: T | null) => void;
  options: readonly T[];
  labelOf?: (v: T) => string;
  helpOf?: (v: T) => string | undefined;
  nullable?: boolean;
  nullLabel?: string;
  reason?: string;
}) {
  const text = (v: T) => labelOf?.(v) ?? v;
  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid} reason={reason}>
      {editable ? (
        <select
          value={value ?? ""}
          data-testid={testid ? `select-${testid}` : undefined}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(raw === "" ? null : (raw as T));
          }}
          style={{ ...inputStyle(true, !!error), cursor: "pointer" }}
        >
          {/* An empty option only where `null` is a legal state. Where it is not, a blank entry
              would offer an item with no type / no side / no scope at all. */}
          {(nullable || value === null) && <option value="">{nullLabel}</option>}
          {options.map((o) => <option key={o} value={o}>{text(o)}</option>)}
        </select>
      ) : (
        <div style={inputStyle(false, !!error)}>
          {value === null
            ? <span style={{ color: color.muted }}>{nullLabel}</span>
            : text(value)}
        </div>
      )}
      {helpOf && (
        <div style={{ marginTop: 7, display: "flex", flexDirection: "column", gap: 4 }}>
          {options.map((o) => {
            const on = o === value;
            const h = helpOf(o);
            if (!h) return null;
            return (
              <div key={o} style={{ ...helpStyle, color: on ? color.sec : color.muted }}>
                <b style={{ color: on ? color.indigo : color.sec2 }}>{text(o)}</b>{" — "}{h}
              </div>
            );
          })}
        </div>
      )}
    </FieldRow>
  );
}

/* ── lists ──────────────────────────────────────────────────────────────────────────────────── */

/** Where a value's ORDER is the value: `scopes`.
 *
 *  `scopes` is a search order, not a gate — `notes` leads by default because for the eight output
 *  lines the note is the authoritative source, and the first scope that yields a figure is the one
 *  published. A set of checkboxes cannot express that at all: it would render two different
 *  configurations identically and let a save silently reorder them. So the chosen scopes are rows,
 *  numbered, with up/down, and the remainder is an add-picker. */
export function OrderedMultiSelect<T extends string>({
  label, help, error, inherited, editable, testid, value, onChange, options, labelOf, addLabel,
}: FieldProps & {
  value: T[];
  onChange: (v: T[]) => void;
  options: readonly T[];
  labelOf?: (v: T) => string;
  addLabel?: string;
}) {
  const text = (v: T) => labelOf?.(v) ?? v;
  const move = (i: number, by: number) => {
    const j = i + by;
    if (j < 0 || j >= value.length) return;
    const next = value.slice();
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  const rest = options.filter((o) => !value.includes(o));
  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid}>
      <div style={{ border: `1px solid ${error ? color.redFg : color.hairline2}`,
                     borderRadius: 7, background: color.rowAltBg }}>
        {value.length === 0 && (
          <div style={{ ...helpStyle, padding: "8px 10px" }}>
            No scope listed — a configured empty, not a default.
          </div>
        )}
        {value.map((v, i) => (
          <div key={v} data-testid={`ordered-${v}`}
               style={{ display: "grid", gridTemplateColumns: "18px 1fr auto", gap: 8,
                         alignItems: "center", padding: "5px 8px",
                         borderBottom: i === value.length - 1 ? "none"
                                                              : `1px solid ${color.hairline}` }}>
            <span style={{ fontFamily: font.mono, fontSize: 11, fontWeight: 600,
                            color: color.indigo }}>{i + 1}</span>
            <span style={{ fontSize: 12 }}>
              {text(v)}
              {i === 0 && value.length > 1 && (
                <span style={{ fontSize: 9.5, fontWeight: 700, textTransform: "uppercase",
                                letterSpacing: 0.3, color: color.greenFg, background: color.greenBg,
                                padding: "1px 5px", borderRadius: 3, marginLeft: 6 }}>
                  searched first
                </span>
              )}
            </span>
            {editable && (
              <span style={{ display: "flex", gap: 4 }}>
                <Button variant="ghost" style={smallBtn} title="Search this earlier"
                        ariaLabel={`Move ${text(v)} earlier`} disabled={i === 0}
                        onClick={() => move(i, -1)}>↑</Button>
                <Button variant="ghost" style={smallBtn} title="Search this later"
                        ariaLabel={`Move ${text(v)} later`} disabled={i === value.length - 1}
                        onClick={() => move(i, 1)}>↓</Button>
                <Button variant="secondary" style={smallBtn}
                        ariaLabel={`Remove ${text(v)}`}
                        onClick={() => onChange(value.filter((x) => x !== v))}>×</Button>
              </span>
            )}
          </div>
        ))}
      </div>
      {editable && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 7,
                       flexWrap: "wrap" }}>
          {rest.length > 0 && (
            <select
              value=""
              data-testid={testid ? `add-${testid}` : undefined}
              onChange={(e) => { if (e.target.value) onChange([...value, e.target.value as T]); }}
              style={{ ...inputStyle(true), width: "auto", maxWidth: 260, fontSize: 11.5,
                        padding: "5px 9px", border: `1px dashed ${color.dashed}`,
                        cursor: "pointer" }}
            >
              <option value="">{addLabel ?? "Add…"}</option>
              {rest.map((o) => <option key={o} value={o}>{text(o)}</option>)}
            </select>
          )}
          {value.length > 0 && (
            <Button variant="secondary" style={smallBtn} onClick={() => onChange([])}
                    title="Store an empty list — not a default">Clear all</Button>
          )}
        </div>
      )}
    </FieldRow>
  );
}

/** Chips, an input, a delete per row, and a Clear all that yields `[]`.
 *
 *  Every list of strings on the screen: aliases, the three regex groups, keyword hints, the two
 *  prose criteria lists, `note_source`'s three pattern groups and
 *  `sign_rule.flip_if_label_matches`.
 *
 *  THREE VARIANTS, and the distinction is load-bearing rather than cosmetic:
 *    • `plain` — inline chips, for short tokens (aliases, keywords).
 *    • `mono`  — full-width rows in the mono face, for anything read as a regex. A pattern that
 *                clips is the thing this screen was built to stop being invisible.
 *    • `veto`  — mono, in red. `exclude_hints` and `note_source.row_caption_none` REFUSE a match,
 *                and they sit next to `exclude_criteria`, which is prose shown to the model. The
 *                two are different fields and folding prose into the regex list either fails to
 *                compile or compiles as an accidental veto — so they never look alike.
 *    • `prose` — full-width rows in the sans face, for `exclude_criteria`,
 *                whose entries are sentences and unreadable as inline chips.
 *
 *  `indexErrors` is how a per-row refusal lands on its row: the server attributes a compile error
 *  to `regex_hints` AND to the offending index, and a message under the list saying "one of these
 *  does not compile" makes the author bisect eleven patterns by hand.
 *
 *  `suggest` OFFERS WHAT THE SET ALREADY USES WITHOUT CLOSING THE LIST. `section_scope` is the
 *  case: the server serves the banner tokens this set already declares, but they are SUGGESTIONS —
 *  a filing printing an undeclared banner is exactly what an author is here to handle, so a
 *  `<select>` over the known tokens would lock them out of the only job they came to do. A datalist
 *  offers the known ones and accepts anything.
 *
 *  DRAFTS CAN BE LIFTED. The Template screen learned this the hard way: a draft held inside the
 *  control and committed on blur is lost when the author types an alias and clicks Save directly,
 *  because the click can land before the blur. Pass `draft`/`onDraft` and fold the draft into the
 *  payload at save time; omit them and the control keeps its own, which is fine for a form whose
 *  save is not one click away. */
export function StringListEditor({
  label, help, error, inherited, editable, testid, value, onChange, variant = "plain",
  placeholder, indexErrors, draft, onDraft, emptyText, suggest,
}: FieldProps & {
  value: string[];
  onChange: (v: string[]) => void;
  variant?: "plain" | "mono" | "veto" | "prose";
  placeholder?: string;
  indexErrors?: Record<number, string>;
  draft?: string;
  onDraft?: (v: string) => void;
  emptyText?: string;
  /** Values this set already uses, offered on the input. Never a closed set — see above. */
  suggest?: readonly string[];
}) {
  const [own, setOwn] = useState("");
  const lifted = draft !== undefined && onDraft !== undefined;
  const text = lifted ? draft! : own;
  const setText = lifted ? onDraft! : setOwn;
  const isMono = variant === "mono" || variant === "veto";
  const rows = variant !== "plain";
  const fg = variant === "veto" ? color.redFg : color.ink2;
  const listId = suggest && suggest.length > 0 && testid ? `dl-list-${testid}` : undefined;

  const commit = (raw: string) => {
    const v = raw.trim();
    setText("");
    // Duplicates are dropped: a second identical alias cannot change a match, and it makes the
    // per-index attribution of a refusal ambiguous.
    if (v && !value.includes(v)) onChange([...value, v]);
  };

  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid}>
      {rows ? (
        <div style={{ border: `1px solid ${error ? color.redFg : color.hairline2}`,
                       borderRadius: 7, background: color.rowAltBg }}>
          {value.length === 0 && (
            <div style={{ ...helpStyle, padding: "8px 10px" }}>
              {emptyText ?? "Nothing listed — a configured empty, not a default."}
            </div>
          )}
          {value.map((v, i) => (
            <div key={`${v}-${i}`}
                 style={{ padding: "5px 8px",
                           borderBottom: i === value.length - 1 ? "none"
                                                                : `1px solid ${color.hairline}` }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8,
                             alignItems: "center" }}>
                <span style={{ fontFamily: isMono ? font.mono : font.sans,
                                fontSize: isMono ? 11 : 12, color: fg,
                                lineHeight: 1.5, wordBreak: isMono ? "break-all" : "break-word" }}>
                  {v}
                </span>
                {editable && (
                  <Button variant="secondary" style={smallBtn} ariaLabel={`Remove ${v}`}
                          onClick={() => onChange(value.filter((_, j) => j !== i))}>×</Button>
                )}
              </div>
              {indexErrors?.[i] && (
                <div style={{ fontSize: 10.5, color: color.redFg, marginTop: 3 }}>
                  {indexErrors[i]}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7, alignItems: "center" }}>
          {value.length === 0 && (
            <span style={helpStyle}>
              {emptyText ?? "Nothing listed — a configured empty, not a default."}
            </span>
          )}
          {value.map((v, i) => (
            <span key={`${v}-${i}`}
                  title={indexErrors?.[i]}
                  style={{ fontSize: 11.5, padding: "5px 11px", borderRadius: radius.pill,
                            background: indexErrors?.[i] ? color.redBg : color.indigoTint2,
                            color: indexErrors?.[i] ? color.redFg : color.indigo }}>
              {v}
              {editable && (
                <span role="button" aria-label={`Remove ${v}`} title="Remove"
                      onClick={() => onChange(value.filter((_, j) => j !== i))}
                      style={{ opacity: 0.55, cursor: "pointer", marginInlineStart: 5,
                                fontWeight: 700 }}>×</span>
              )}
            </span>
          ))}
        </div>
      )}
      {editable && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 7,
                       flexWrap: "wrap" }}>
          <input
            value={text}
            list={listId}
            placeholder={placeholder}
            data-testid={testid ? `add-${testid}` : undefined}
            aria-label={`Add to ${label}`}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); commit(text); }
            }}
            onBlur={() => commit(text)}
            style={{ ...inputStyle(true, false, isMono), flex: "1 1 190px", minWidth: 150,
                      padding: "5px 10px", border: `1px dashed ${color.dashed}` }}
          />
          {listId && (
            <datalist id={listId}>
              {suggest!.filter((s) => !value.includes(s)).map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          )}
          {value.length > 0 && (
            <Button variant="secondary" style={smallBtn} onClick={() => onChange([])}
                    testid={testid ? `clear-${testid}` : undefined}
                    title="Store an empty list — not a default">Clear all</Button>
          )}
        </div>
      )}
    </FieldRow>
  );
}

/* ── keys of this set ───────────────────────────────────────────────────────────────────────── */

/** One line item, as a picker offers it. Label AND key, because the key is what is stored and the
 *  label is what a reader recognises — a picker showing only labels lets two lines that print the
 *  same caption be told apart by nothing. */
export interface KeyOption {
  key: string;
  label?: string;
}

/** A key of THIS set — never a free-text field.
 *
 *  `parent`, `sole_component_of`, `confusable_with`, `children_if_decomposed`,
 *  `expected_components`, `never_sweep` and every `terms[].ref` all name a key of the same set, and
 *  the publish gate refuses a name that is not one. But a refusal is the good case: a prohibition
 *  that names a non-key (`never_sweep`) cannot be told apart from a filing that never triggered it,
 *  and a term whose `ref` is a typo is a term that silently contributes nothing. So the author
 *  picks from what exists.
 *
 *  Searchable over key AND label because the set is ~475 items and a `<select>` of 475 options is
 *  not a control. The result rows render INLINE below the input rather than as a popover: the
 *  detail pane scrolls in its own box, and an absolutely-positioned list clips against it.
 *
 *  Single (`value: string | null`, clearable to null) and multi (`value: string[]`, clearable to
 *  `[]`) are the same control — the field table asks for both and they differ only in what a pick
 *  does. */
export type KeyPickerProps = FieldProps & {
  options: readonly KeyOption[];
  placeholder?: string;
  /** Keys to keep out of the results — an item is never its own parent, nor confusable with
   *  itself, and the backend refuses both. */
  exclude?: readonly string[];
  indexErrors?: Record<number, string>;
  reason?: string;
} & (
  | { multi?: false; value: string | null; onChange: (v: string | null) => void }
  | { multi: true; value: string[]; onChange: (v: string[]) => void }
);

export function KeyPicker(props: KeyPickerProps) {
  const { label, help, error, inherited, editable, testid, options, placeholder, exclude,
          indexErrors, reason } = props;
  const [query, setQuery] = useState("");
  const chosen: string[] = props.multi ? props.value : (props.value ? [props.value] : []);
  const labelOf = (k: string) => options.find((o) => o.key === k)?.label || k;
  const known = (k: string) => options.some((o) => o.key === k);

  const needle = query.trim().toLowerCase();
  const matches = needle
    ? options.filter((o) =>
        !chosen.includes(o.key) && !exclude?.includes(o.key) &&
        `${o.key} ${o.label ?? ""}`.toLowerCase().includes(needle)).slice(0, 40)
    : [];

  const pick = (k: string) => {
    setQuery("");
    if (props.multi) { if (!props.value.includes(k)) props.onChange([...props.value, k]); }
    else props.onChange(k);
  };
  const drop = (k: string) => {
    if (props.multi) props.onChange(props.value.filter((x) => x !== k));
    else props.onChange(null);
  };

  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid} reason={reason}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 7, alignItems: "center" }}>
        {chosen.length === 0 && (
          <span style={helpStyle}>
            {props.multi ? "No key listed — a configured empty, not a default."
                         : "Nothing named."}
          </span>
        )}
        {chosen.map((k, i) => (
          <span key={k} data-testid={`key-chip-${k}`} title={indexErrors?.[i] ?? k}
                style={{ display: "inline-flex", alignItems: "baseline", gap: 5, fontSize: 11.5,
                          padding: "4px 10px", borderRadius: radius.pill,
                          background: !known(k) || indexErrors?.[i] ? color.redBg
                                                                    : color.indigoTint2,
                          color: !known(k) || indexErrors?.[i] ? color.redFg : color.indigo }}>
            <span>{labelOf(k)}</span>
            <span style={{ fontFamily: font.mono, fontSize: 9.5, opacity: 0.75 }}>{k}</span>
            {/* A stored key the set does not declare: the gate will refuse it, and until it does
                it is a declaration that never fires. Named as unknown rather than shown plain. */}
            {!known(k) && <span style={{ fontSize: 9.5, fontWeight: 700 }}>NOT A KEY</span>}
            {editable && (
              <span role="button" aria-label={`Remove ${labelOf(k)}`} title="Remove"
                    onClick={() => drop(k)}
                    style={{ opacity: 0.6, cursor: "pointer", fontWeight: 700 }}>×</span>
            )}
          </span>
        ))}
      </div>
      {editable && (
        <>
          <input
            value={query}
            placeholder={placeholder ?? "Search this set's line items by label or key…"}
            data-testid={testid ? `search-${testid}` : undefined}
            aria-label={`Search a line item for ${label}`}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setQuery("");
              // Enter takes the single unambiguous match. It never guesses between two.
              if (e.key === "Enter" && matches.length === 1) { e.preventDefault(); pick(matches[0].key); }
            }}
            style={{ ...inputStyle(true, false), marginTop: 7, padding: "6px 10px",
                      border: `1px dashed ${color.dashed}` }}
          />
          {needle && (
            <div style={{ marginTop: 5, maxHeight: 190, overflowY: "auto",
                           border: `1px solid ${color.hairline2}`, borderRadius: 7,
                           background: color.surface }}>
              {matches.length === 0 ? (
                <div style={{ ...helpStyle, padding: "8px 10px" }}>
                  No line item in this set matches <b>{query}</b>. Only keys this set declares are
                  legal here.
                </div>
              ) : matches.map((o) => (
                <div key={o.key} role="button" tabIndex={0}
                     data-testid={`key-option-${o.key}`}
                     onClick={() => pick(o.key)}
                     onKeyDown={(e) => {
                       if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(o.key); }
                     }}
                     style={{ padding: "5px 9px", cursor: "pointer",
                               borderBottom: `1px solid ${color.hairline}` }}>
                  <div style={{ fontSize: 12 }}>{o.label || o.key}</div>
                  <div style={{ fontFamily: font.mono, fontSize: 9.5, color: color.faint }}>
                    {o.key}
                  </div>
                </div>
              ))}
            </div>
          )}
          {props.multi && chosen.length > 0 && (
            <div style={{ marginTop: 7 }}>
              <Button variant="secondary" style={smallBtn} onClick={() => props.onChange([])}
                      testid={testid ? `clear-${testid}` : undefined}
                      title="Store an empty list — not a default">Clear all</Button>
            </div>
          )}
        </>
      )}
    </FieldRow>
  );
}

/* ── assembly: terms and cascade rungs ──────────────────────────────────────────────────────── */

/** What a term's ABSENCE means. Each option carries the consequence, because the third value is
 *  the one a boolean could not express and its absence produced a negative depreciation charge
 *  assembled from a deduction with nothing to deduct it from. */
export const TERM_ROLE_HELP: Record<TermRole, string> = {
  required: "must be present, or the rung fails",
  any_of: "one of this group is enough; those present are summed",
  adjustment: "contributes when present, and never justifies the rung on its own",
};

const TERM_ROLE_LABEL: Record<TermRole, string> = {
  required: "required", any_of: "any of", adjustment: "adjustment",
};

/** A fresh term. A `ref` term, because that is what nearly every term is; `const` is the
 *  exception the row can switch to. */
const newTerm = (): LineItemTerm => ({ ref: "", const: null, sign: 1, abs: false, role: "required" });

/** THE EDITABLE COUNTERPART OF THE READ-ONLY `Terms` LIST.
 *
 *  One row per term, over the real `Term` model, so the editor cannot express a shape the loader
 *  rejects:
 *    • `ref` XOR `const` — the model validator refuses both and refuses neither, so the row asks
 *      "a line, or a number?" and the exclusivity is expressed by the control instead of being
 *      discovered as a 422. Switching mode clears the other side.
 *    • `abs` is refused on a `const` term, so on a number row the toggle is off and says why.
 *    • `sign` is arithmetic INSIDE this formula. It is not `sign_convention` (an expectation
 *      review reads) and not `sign_rule` (a normalisation that flips a printed value), and the row
 *      says so once rather than leaving three sign fields on one screen to be confused.
 *
 *  Used for `terms` and, unchanged, for every `cascade[].terms` — one editor for one shape. */
export function TermRows({
  label, help, error, editable, testid, terms, onChange, options, errorAt, inherited,
}: FieldProps & {
  terms: LineItemTerm[];
  onChange: (v: LineItemTerm[]) => void;
  options: readonly KeyOption[];
  /** A refusal addressed to one row's one field — the server sends `terms[0].ref`. */
  errorAt?: (index: number, field: keyof LineItemTerm) => string | undefined;
}) {
  const set = (i: number, patch: Partial<LineItemTerm>) =>
    onChange(terms.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  const move = (i: number, by: number) => {
    const j = i + by;
    if (j < 0 || j >= terms.length) return;
    const next = terms.slice();
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };

  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {terms.length === 0 && (
          <div style={{ ...helpStyle, padding: "8px 10px", background: color.rowAltBg,
                         border: `1px solid ${color.hairline2}`, borderRadius: 7 }}>
            No terms. A <b>calculated</b> or <b>intermediate</b> line with none is refused — there
            is nothing for it to be assembled from.
          </div>
        )}
        {terms.map((term, i) => {
          const isConst = term.const !== null;
          return (
            <div key={i} data-testid={`term-${i}`}
                 style={{ border: `1px solid ${color.hairline2}`, borderRadius: 8,
                           background: color.rowAltBg, padding: 9 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                             marginBottom: 7 }}>
                <span style={{ fontFamily: font.mono, fontSize: 11, fontWeight: 700,
                                color: color.muted }}>{i + 1}</span>
                {editable ? (
                  <Segmented<"add" | "sub">
                    options={[{ value: "add", label: "+ add" }, { value: "sub", label: "− subtract" }]}
                    value={term.sign < 0 ? "sub" : "add"}
                    onChange={(v) => set(i, { sign: v === "sub" ? -1 : 1 })}
                  />
                ) : (
                  <span style={{ fontFamily: font.mono, fontSize: 12, fontWeight: 600,
                                  color: term.sign < 0 ? color.redFg : color.ink }}>
                    {term.sign < 0 ? "−" : "+"}
                  </span>
                )}
                {editable && (
                  <Segmented<"ref" | "const">
                    options={[{ value: "ref", label: "a line" }, { value: "const", label: "a number" }]}
                    value={isConst ? "const" : "ref"}
                    onChange={(v) => set(i, v === "const"
                      // Exactly one side may be set. Switching clears the other, and `abs` goes
                      // with it because the model refuses it on a const term.
                      ? { const: 0, ref: "", abs: false }
                      : { const: null, ref: "" })}
                  />
                )}
                <span style={{ flex: 1 }} />
                {editable && (
                  <span style={{ display: "flex", gap: 4 }}>
                    <Button variant="ghost" style={smallBtn} disabled={i === 0}
                            ariaLabel={`Move term ${i + 1} up`}
                            onClick={() => move(i, -1)}>↑</Button>
                    <Button variant="ghost" style={smallBtn} disabled={i === terms.length - 1}
                            ariaLabel={`Move term ${i + 1} down`}
                            onClick={() => move(i, 1)}>↓</Button>
                    <Button variant="secondary" style={smallBtn}
                            ariaLabel={`Remove term ${i + 1}`}
                            onClick={() => onChange(terms.filter((_, j) => j !== i))}>Remove</Button>
                  </span>
                )}
              </div>

              {isConst ? (
                <NumberField
                  label="Number" editable={editable} testid={`term-${i}-const`}
                  help="A fixed figure instead of a referenced line."
                  value={term.const} onChange={(v) => set(i, { const: v ?? 0 })}
                  error={errorAt?.(i, "const")}
                />
              ) : (
                <KeyPicker
                  label="Line" editable={editable} testid={`term-${i}-ref`}
                  help="Which other line of this set this addend reads."
                  value={term.ref || null} onChange={(v) => set(i, { ref: v ?? "" })}
                  options={options} error={errorAt?.(i, "ref")}
                />
              )}

              <BoolField
                label="Take the magnitude first (abs)" editable={editable}
                testid={`term-${i}-abs`}
                help="For a filing that prints accumulated depreciation as −1,842,330 or in
                      brackets. Deliberately separate from the sign above: collapsing the two would
                      make one of them inexpressible."
                disabledReason={isConst
                  ? "not allowed on a fixed number — the model refuses it"
                  : undefined}
                value={term.abs} onChange={(v) => set(i, { abs: v })}
                error={errorAt?.(i, "abs")}
              />

              <SelectField<TermRole>
                label="If it is missing" editable={editable} testid={`term-${i}-role`}
                value={term.role} onChange={(v) => set(i, { role: (v ?? "required") })}
                options={["required", "any_of", "adjustment"]}
                labelOf={(r) => TERM_ROLE_LABEL[r]}
                helpOf={(r) => TERM_ROLE_HELP[r]}
                error={errorAt?.(i, "role")}
              />
            </div>
          );
        })}
      </div>
      {editable && (
        <div style={{ marginTop: 8 }}>
          <Button variant="ghost" style={smallBtn} testid={testid ? `add-${testid}` : undefined}
                  onClick={() => onChange([...terms, newTerm()])}>Add a term</Button>
        </div>
      )}
    </FieldRow>
  );
}

const newRung = (n: number): CascadeRung => ({
  id: `P${n}`, terms: [], note: "", refuse_negative: true,
});

/** THE ORDERED ATTEMPTS A `derived` LINE IS ASSEMBLED BY — first rung that resolves wins, so the
 *  ORDER IS THE PRIORITY and the cards are reorderable.
 *
 *  `_coherent` requires a derived line to have a cascade or an `implemented_by`, and moving a
 *  derivation off a service and into configuration is only possible if the rungs are authorable at
 *  all. Each card is the real `CascadeRung`: an id (required — it is what the run log and the audit
 *  trail print, and a blank one makes the trail unreadable), the same `TermRows` editor, the note
 *  that is the only explanation a reviewer gets for a priority order, and `refuse_negative`, which
 *  the derivation services perform and the configuration that ported them originally did not: a
 *  rung computing −50 would win here and then be refused by the service it claims to describe. */
export function RungCards({
  label, help, error, editable, testid, cascade, onChange, options, rungErrorAt, termErrorAt,
  inherited,
}: FieldProps & {
  cascade: CascadeRung[];
  onChange: (v: CascadeRung[]) => void;
  options: readonly KeyOption[];
  /** A refusal addressed to one rung's own field — `cascade[1].id`. */
  rungErrorAt?: (index: number, field: keyof CascadeRung) => string | undefined;
  /** A refusal addressed to one term of one rung — `cascade[1].terms[0].ref`. */
  termErrorAt?: (rung: number, term: number, field: keyof LineItemTerm) => string | undefined;
}) {
  const set = (i: number, patch: Partial<CascadeRung>) =>
    onChange(cascade.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const move = (i: number, by: number) => {
    const j = i + by;
    if (j < 0 || j >= cascade.length) return;
    const next = cascade.slice();
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };

  return (
    <FieldRow label={label} help={help} error={error} inherited={inherited} editable={editable}
              testid={testid}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {cascade.length === 0 && (
          <div style={{ ...helpStyle, padding: "8px 10px", background: color.rowAltBg,
                         border: `1px solid ${color.hairline2}`, borderRadius: 7 }}>
            No rungs. A <b>derived</b> line needs either a cascade or an <b>implemented_by</b>
            {" "}naming the service that computes it today.
          </div>
        )}
        {cascade.map((rung, i) => (
          <Card key={i} pad={11} style={{ background: color.surface }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                           marginBottom: 8 }}>
              <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.3,
                              textTransform: "uppercase", color: color.muted }}>
                tried {i === 0 ? "first" : i === cascade.length - 1 ? "last" : `${i + 1}${
                  i === 1 ? "nd" : i === 2 ? "rd" : "th"}`}
              </span>
              <span style={{ flex: 1 }} />
              {editable && (
                <span style={{ display: "flex", gap: 4 }}>
                  <Button variant="ghost" style={smallBtn} disabled={i === 0}
                          ariaLabel={`Try rung ${rung.id} earlier`}
                          onClick={() => move(i, -1)}>↑</Button>
                  <Button variant="ghost" style={smallBtn} disabled={i === cascade.length - 1}
                          ariaLabel={`Try rung ${rung.id} later`}
                          onClick={() => move(i, 1)}>↓</Button>
                  <Button variant="secondary" style={smallBtn}
                          ariaLabel={`Remove rung ${rung.id}`}
                          onClick={() => onChange(cascade.filter((_, j) => j !== i))}>
                    Remove rung
                  </Button>
                </span>
              )}
            </div>

            <TextField
              label="Rung id" editable={editable} testid={`rung-${i}-id`} mono
              help="What the run log and the audit trail print for this attempt (“P1”, “Find_2”).
                    A reader traces a figure back through it, so it is required."
              value={rung.id} onChange={(v) => set(i, { id: v })}
              error={rungErrorAt?.(i, "id")
                     ?? (rung.id.trim() === "" ? "A rung with no id cannot be traced." : undefined)}
            />

            <TermRows
              label="What this rung computes" editable={editable} testid={`rung-${i}-terms`}
              terms={rung.terms} onChange={(v) => set(i, { terms: v })} options={options}
              errorAt={(ti, f) => termErrorAt?.(i, ti, f)}
            />

            <TextArea
              label="Why this rung exists" editable={editable} testid={`rung-${i}-note`} rows={2}
              help="The only explanation a reviewer gets for the priority order."
              value={rung.note} onChange={(v) => set(i, { note: v ?? "" })}
              error={rungErrorAt?.(i, "note")}
            />

            <BoolField
              label="Pass over a negative result" editable={editable}
              testid={`rung-${i}-refuse_negative`}
              value={rung.refuse_negative} onChange={(v) => set(i, { refuse_negative: v })}
              onText="A result below zero is skipped and the next rung tried — what the derivation
                      services do."
              offText="A negative result WINS here. Correct for a genuine net movement or a
                       carryforward; otherwise a rung computing −50 is published."
              error={rungErrorAt?.(i, "refuse_negative")}
            />
          </Card>
        ))}
      </div>
      {editable && (
        <div style={{ marginTop: 9 }}>
          <Button variant="ghost" style={smallBtn} testid={testid ? `add-${testid}` : undefined}
                  onClick={() => onChange([...cascade, newRung(cascade.length + 1)])}>
            Add a rung
          </Button>
        </div>
      )}
    </FieldRow>
  );
}
