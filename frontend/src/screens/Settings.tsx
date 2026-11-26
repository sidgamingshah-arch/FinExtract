/** Settings (admin), in tabs. The first is HOW FILINGS ARE READ: a picker of three routes (rules
 * only; rules + model questions; the whole PDF to the model), each a card that says what it does,
 * what it is best for, what it costs and its limits, with only the chosen route's own options under
 * it. Then Advanced (the extra model calls, how requests are sent, scanned pages and the document
 * engine, the rules' thresholds — the rarely changed groups folded), Focus line items (the list),
 * FX rates, General (interface, sign-off, sample project) and System (read-only access settings).
 * A one-line summary of how the next run reads a filing sits above the tabs on every one of them.
 *
 * A ROUTE IS NOT A SETTING OF ITS OWN. It is a reading of two knobs the pipeline decides on,
 * `whole_pdf_to_model` and `llm_mapping`, and the backend serves the three cards with the values
 * that choose each (`extraction_routes`, from `settings_state.ROUTES`). Picking a card sets those
 * values as unsaved edits, exactly as flipping the switches would; nothing else moves. Every knob
 * the cards show is on the same tab, so a route change and its options are saved by one Save.
 *
 * The knobs are laid out from the backend's own descriptors: each names its tab, section, one-line
 * summary, whether its section starts folded, and the switch it is read under — an option only one
 * route reads hangs off that route's switch, which is how a card knows its own options. The LLM
 * itself is NOT here: it is defined only in backend/config.toml [llm].
 *
 * Edits are SAVED: the backend persists them and re-applies them at startup, so a change holds
 * across a restart. Each tab saves, and restores the shipped defaults of, its own knobs; a dot on a
 * tab marks unsaved edits there. */
import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent,
  type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

import { Card, ScreenHeader, Toggle } from "../components/ui";
import { useT } from "../i18n";
import { ApiError } from "../lib/api";
import { useMediaQuery } from "../lib/media";
import { useCan } from "../lib/rbac";
import {
  useAddFocusLineItem,
  useDeleteFocusLineItem,
  useDeleteFxRate,
  useFocusLineItems,
  useFxRates,
  usePatchSettings,
  useRestoreFocusLineItems,
  useSettings,
  useUpdateFxRate,
  useUpsertFxRate,
} from "../lib/queries";
import { color, font, radius } from "../theme";
import type {
  AppSettings, ExtractionField, ExtractionRoute, FocusLineItem, FxRate, FxRateInput,
} from "../types";

/** A phone: the screen drops to one column and a 16px gutter. */
const PHONE = "(max-width: 640px)";

/** Hidden on screen, read by a screen reader. */
const srOnly: CSSProperties = {
  position: "absolute", width: 1, height: 1, padding: 0, margin: -1, overflow: "hidden",
  clip: "rect(0,0,0,0)", whiteSpace: "nowrap", border: 0,
};

/** Read-only key/value row. */
function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 16, padding: "7px 0",
                  borderBottom: `1px solid ${color.hairline}`, flexWrap: "wrap" }}>
      <span style={{ fontSize: 12, color: color.muted }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 600, color: color.ink, fontFamily: font.mono,
                     textAlign: "right", overflowWrap: "anywhere", minWidth: 0 }}>{value}</span>
    </div>
  );
}

function SectionCard({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) {
  return (
    <Card style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    marginBottom: 8, gap: 10, flexWrap: "wrap" }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{title}</div>
        {note && <span style={{ fontSize: 10, color: color.muted2 }}>{note}</span>}
      </div>
      {children}
    </Card>
  );
}

/** A clickable toggle row bound to a boolean setting. */
function ToggleRow({
  on, onToggle, canEdit, saving, title, help, testId,
}: {
  on: boolean; onToggle: () => void; canEdit: boolean; saving: boolean; title: React.ReactNode;
  help: string; testId?: string;
}) {
  const t = useT();
  return (
    <div
      data-testid={testId}
      data-on={on ? "true" : "false"}
      role="switch"
      aria-checked={on}
      tabIndex={canEdit ? 0 : -1}
      onClick={() => canEdit && !saving && onToggle()}
      onKeyDown={(e) => {
        if ((e.key === " " || e.key === "Enter") && canEdit && !saving) { e.preventDefault(); onToggle(); }
      }}
      style={{
        display: "flex", alignItems: "flex-start", gap: 12, padding: "10px 12px", borderRadius: 9,
        background: color.rowAltBg, cursor: canEdit ? "pointer" : "default", opacity: canEdit ? 1 : 0.7,
        marginBottom: 8,
      }}
    >
      <div style={{ marginTop: 2 }}><Toggle on={on} /></div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, color: color.ink }}>
          {title}{" "}
          <span style={{ fontSize: 10.5, fontWeight: 600, color: on ? color.greenFg : color.muted }}>
            · {saving ? t("st.saving") : on ? t("st.on") : t("st.off")}
          </span>
        </div>
        <div style={{ fontSize: 11.5, color: color.sec2, lineHeight: 1.55, marginTop: 4 }}>{help}</div>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%", fontSize: 12, fontFamily: font.mono, color: color.ink,
  border: `1px solid ${color.controlBorder}`, borderRadius: 7, padding: "7px 9px",
  background: "#fff", boxSizing: "border-box",
};

/** One labeled editable field in a form. */
function Field({
  label, children,
}: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
      <span style={{ fontSize: 11, color: color.muted }}>{label}</span>
      {children}
    </label>
  );
}

type Value = number | boolean | string;
type Values = Record<string, Value | undefined>;

/** Whether a knob is read at all under the values on screen: with no `shown_when` always; else
 *  while the knob it hangs off is itself shown and holds one of the listed values. Evaluated on
 *  the values being edited, so turning a switch on shows its options at once. */
function isShown(f: ExtractionField, byKey: Map<string, ExtractionField>,
                 current: (k: string) => Value | undefined, seen = new Set<string>()): boolean {
  const when = f.shown_when;
  if (!when || seen.has(f.key)) return true;
  const parent = byKey.get(when.key);
  if (!parent) return true;
  seen.add(f.key);
  return isShown(parent, byKey, current, seen)
    && when.values.some((v) => String(v) === String(current(when.key)));
}

/** The knob a knob's `shown_when` chain ends at: for an option of a route, that route's switch. */
function rootOf(f: ExtractionField, byKey: Map<string, ExtractionField>): string {
  let key = f.key;
  const seen = new Set<string>();
  for (let at = byKey.get(key); at?.shown_when && !seen.has(key); at = byKey.get(key)) {
    seen.add(key);
    key = at.shown_when.key;
  }
  return key;
}

/** The route these values read a filing by: the first card whose values they all hold. The
 *  backend's cards cover both switches, so exactly one matches. */
function routeOf(routes: ExtractionRoute[], values: Values): ExtractionRoute | undefined {
  return routes.find((r) => Object.entries(r.sets).every(([k, v]) => Boolean(values[k]) === v));
}

/** A section heading as an address: `?open=matching-captions`. */
const slug = (section: string) => section.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/** A choice's name in words, from the knob's own descriptor; the bare value when it has none. */
function choiceLabel(f: ExtractionField | undefined, value: Value | undefined): string {
  return f?.choice_info?.find((c) => c.value === String(value))?.label ?? String(value ?? "");
}

/** "The whole report…" → "the whole report…", for a phrase set inside a sentence. */
const lowerFirst = (s: string) => (/^[A-Z][a-z]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s);

/** The first bound a typed value breaks, checked against the backend's own descriptor so the
 *  Save button never sends something the API will refuse. */
function boundError(f: ExtractionField, raw: Value | undefined): string | null {
  if (f.kind !== "number") return null;
  const v = Number(raw);
  if (raw === "" || raw === undefined || !Number.isFinite(v)) return `${f.label} must be a number`;
  if (f.min !== null && v < f.min) return `${f.label} must be at least ${f.min}`;
  if (f.max !== null && v > f.max) return `${f.label} must be at most ${f.max}`;
  return null;
}

const pill = (bg: string, fg: string): React.CSSProperties => ({
  fontSize: 9.5, fontWeight: 600, padding: "1px 7px", borderRadius: radius.pill,
  background: bg, color: fg, whiteSpace: "nowrap", fontFamily: font.sans,
});

const linkButton: CSSProperties = {
  background: "none", border: "none", padding: 0, fontSize: 11.5, fontWeight: 600,
  color: color.indigo, cursor: "pointer", fontFamily: font.sans,
};

/** A choice offered as a list of options in words, each with one line on what it does. */
function ChoiceList({ f, value, onChange, defaultValue }: {
  f: ExtractionField; value: Value | undefined; onChange: (v: Value) => void;
  defaultValue: Value | undefined;
}) {
  const t = useT();
  const name = useId();
  return (
    <div role="radiogroup" aria-label={f.label} data-testid={`ex-${f.key}`} data-value={String(value)}
         style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {(f.choice_info ?? []).map((c) => {
        const on = String(value) === c.value;
        return (
          <label key={c.value}
                 style={{ display: "flex", alignItems: "flex-start", gap: 9, padding: "8px 11px",
                          borderRadius: radius.control, cursor: "pointer",
                          border: `1px solid ${on ? color.indigoBorder2 : color.cardBorder}`,
                          background: on ? color.indigoTint : "#fff" }}>
            <input type="radio" name={name} value={c.value} checked={on}
                   data-testid={`ex-${f.key}-${c.value}`}
                   onChange={() => onChange(c.value)}
                   style={{ accentColor: color.indigo, marginTop: 2, flex: "0 0 auto" }} />
            <span style={{ minWidth: 0 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: on ? color.indigo : color.ink }}>
                {c.label}
              </span>
              {String(defaultValue) === c.value && (
                <span style={{ ...pill(color.segBg, color.sec2), marginInlineStart: 7 }}>
                  {t("st.choice.default")}
                </span>
              )}
              <span style={{ display: "block", fontSize: 11, color: color.sec2, lineHeight: 1.5,
                             marginTop: 2 }}>{c.note}</span>
            </span>
          </label>
        );
      })}
    </div>
  );
}

/** One knob's control: a switch, a list of choices, or a number. */
function KnobControl({ f, value, onChange, defaultValue }: {
  f: ExtractionField; value: Value | undefined; onChange: (v: Value) => void;
  defaultValue: Value | undefined;
}) {
  const t = useT();
  if (f.kind === "bool") {
    return (
      // Toggle is presentational; this button is the switch, and says so.
      <button type="button" role="switch" aria-checked={Boolean(value)} aria-label={f.label}
              data-testid={`ex-${f.key}`} onClick={() => onChange(!value)}
              style={{ display: "flex", alignItems: "center", gap: 8, background: "none",
                       border: "none", padding: 0, cursor: "pointer", justifySelf: "start" }}>
        <Toggle on={Boolean(value)} />
        <span style={{ fontSize: 11, fontWeight: 600, color: value ? color.greenFg : color.muted }}>
          {value ? t("st.on") : t("st.off")}
        </span>
      </button>
    );
  }
  if (f.kind === "choice") {
    if (f.choice_info?.length) {
      return <ChoiceList f={f} value={value} onChange={onChange} defaultValue={defaultValue} />;
    }
    return (
      <select data-testid={`ex-${f.key}`} value={String(value)} aria-label={f.label}
              onChange={(e) => onChange(e.target.value)} style={inputStyle}>
        {f.choices.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    );
  }
  return (
    <input data-testid={`ex-${f.key}`} type="number" value={String(value ?? "")} aria-label={f.label}
           step={f.step ?? 0.01} min={f.min ?? undefined} max={f.max ?? undefined}
           onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
           style={inputStyle} />
  );
}

/** One knob: its name, its one-line summary, the full explanation one click away, and its
 *  control — or, for a reader without the right to change it, its value. A list of choices takes
 *  the row's full width under the text; a switch or a number sits beside it, or under it on a
 *  phone. */
function KnobRow({ f, value, changed, hidden, canEdit, onChange, defaultValue, phone }: {
  f: ExtractionField; value: Value | undefined; changed: string | null; hidden: number;
  canEdit: boolean; onChange: (v: Value) => void; defaultValue: Value | undefined; phone: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const wide = f.kind === "choice" && !!f.choice_info?.length;
  const stacked = wide || phone;
  const control = canEdit ? (
    <KnobControl f={f} value={value} onChange={onChange} defaultValue={defaultValue} />
  ) : (
    <span style={{ fontSize: 12, fontWeight: 600, fontFamily: font.mono, color: color.ink,
                   textAlign: stacked ? "left" : "right" }}>{choiceLabel(f, value)}</span>
  );
  return (
    <div data-testid={`knob-${f.key}`}
         style={{ display: "grid", gridTemplateColumns: stacked ? "minmax(0,1fr)" : "minmax(0,1fr) 170px",
                  gap: stacked ? 9 : 14, alignItems: "start", padding: "11px 0",
                  borderBottom: `1px solid ${color.hairline}` }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: color.ink }}>{f.label}</span>
          {changed !== null && (
            <span title={`${t("st.default")}: ${changed}`}
                  style={pill(color.amberBg, color.amberFg)}>{t("st.changed")}</span>
          )}
        </div>
        <div style={{ fontSize: 11.5, color: color.sec2, marginTop: 3, lineHeight: 1.5 }}>
          {f.summary || f.help}
          {f.summary && f.help && (
            <button type="button" data-testid={`knob-more-${f.key}`} aria-expanded={open}
                    onClick={() => setOpen((o) => !o)}
                    style={{ ...linkButton, fontSize: 11, marginInlineStart: 6 }}>
              {open ? t("st.lessDetail") : t("st.moreDetail")}
            </button>
          )}
        </div>
        {open && (
          <div style={{ fontSize: 11, color: color.muted, marginTop: 6, lineHeight: 1.55,
                        padding: "8px 10px", background: color.rowAltBg, borderRadius: 7 }}>
            {f.help}
          </div>
        )}
        {hidden > 0 && (
          <div style={{ fontSize: 10.5, color: color.muted2, marginTop: 5 }}>
            {t(f.kind === "bool" ? "st.moreWhenOn" : "st.moreWhenOther").replace("{n}", String(hidden))}
          </div>
        )}
      </div>
      {control}
    </div>
  );
}

/** What every knob list needs to know about the screen's state. */
interface KnobState {
  s: AppSettings;
  all: ExtractionField[];
  edits: Record<string, Value>;
  setEdit: (k: string, v: Value) => void;
  canEdit: boolean;
  phone: boolean;
  /** Which folded sections are open, by `slug(section)`. */
  openGroups: Set<string>;
  toggleGroup: (id: string) => void;
}

/** Knobs under their section headings. A section the backend marks `folded` shows only its
 *  heading, how many settings it holds and how many differ from the default, and opens on a click;
 *  `extras` adds content (a read-only status, a link) at the end of the section with that slug. */
function KnobSections({ fields, st, extras }: {
  fields: ExtractionField[]; st: KnobState; extras?: Record<string, ReactNode>;
}) {
  const t = useT();
  const values = st.s.extraction ?? {};
  const defaults = st.s.extraction_defaults ?? {};
  const current = (k: string): Value | undefined => (k in st.edits ? st.edits[k] : values[k]);
  const byKey = new Map(st.all.map((f) => [f.key, f]));
  const shown = fields.filter((f) => isShown(f, byKey, current));
  const hiddenUnder = (key: string) =>
    fields.filter((f) => f.shown_when?.key === key && !isShown(f, byKey, current)).length;
  const sections: [string, ExtractionField[]][] = [];
  for (const f of shown) {
    const last = sections[sections.length - 1];
    if (last && last[0] === f.section) last[1].push(f);
    else sections.push([f.section, [f]]);
  }
  const changedFromDefault = (f: ExtractionField) =>
    f.key in defaults && String(values[f.key]) !== String(defaults[f.key]);
  const unsavedIn = (list: ExtractionField[]) =>
    list.some((f) => f.key in st.edits && String(st.edits[f.key]) !== String(values[f.key]));

  return (
    <>
      {sections.map(([title, list], i) => {
        const id = slug(title);
        const folded = list.every((f) => f.folded);
        const open = !folded || st.openGroups.has(id);
        const nChanged = list.filter(changedFromDefault).length;
        const rows = (
          <>
            {list.map((f) => (
              <KnobRow key={f.key} f={f} value={current(f.key)} canEdit={st.canEdit} phone={st.phone}
                       changed={changedFromDefault(f) ? String(defaults[f.key]) : null}
                       defaultValue={defaults[f.key]}
                       hidden={hiddenUnder(f.key)}
                       onChange={(v) => st.setEdit(f.key, v)} />
            ))}
            {extras?.[id]}
          </>
        );
        if (!folded) {
          return (
            <div key={title || i} data-testid={`st-section-${id}`} style={{ marginTop: i ? 16 : 0 }}>
              {/* A section of one knob is headed by that knob's own label; a second heading above
                  it (a group named for the one knob it holds) only repeats it. */}
              {title && list.length > 1 && (
                <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, color: color.muted,
                              textTransform: "uppercase", paddingBottom: 2 }}>{title}</div>
              )}
              {rows}
            </div>
          );
        }
        return (
          <div key={title || i} style={{ marginTop: i ? 10 : 0 }}>
            <button type="button" data-testid={`st-group-${id}`} data-open={open ? "true" : "false"}
                    aria-expanded={open} aria-controls={`st-group-body-${id}`}
                    onClick={() => st.toggleGroup(id)}
                    style={{ display: "flex", alignItems: "center", gap: 9, width: "100%",
                             textAlign: "start", background: open ? "#fff" : color.rowAltBg,
                             border: `1px solid ${color.hairline3}`, borderRadius: radius.control,
                             padding: "9px 11px", cursor: "pointer", flexWrap: "wrap" }}>
              <span aria-hidden style={{ fontSize: 10, color: color.muted, width: 10 }}>{open ? "▾" : "▸"}</span>
              <span style={{ fontSize: 12, fontWeight: 700, color: color.ink2 }}>{title}</span>
              <span style={{ fontSize: 11, color: color.muted, flex: "1 1 auto" }}>
                {list.length === 1 ? t("st.group.countOne")
                  : t("st.group.count").replace("{n}", String(list.length))}
                {nChanged > 0 && ` · ${t("st.group.changed").replace("{n}", String(nChanged))}`}
              </span>
              {unsavedIn(list) && (
                <span title={t("st.unsaved")}
                      style={{ width: 6, height: 6, borderRadius: 3, background: color.amberFg }} />
              )}
            </button>
            {open && (
              <div id={`st-group-body-${id}`} style={{ padding: "0 4px" }}>{rows}</div>
            )}
          </div>
        );
      })}
    </>
  );
}

/** A tab's Save and Restore defaults, for exactly the knobs in `keys`. While there are unsaved
 *  edits it stays in view at the bottom of the screen, so an edit far down is never left without
 *  its Save button. Restore defaults puts back this tab's knobs only — never another tab's, which an
 *  admin cannot see from here. */
function SaveBar({ s, keys, fields, edits, clearEdits, canEdit }: {
  s: AppSettings; keys: string[]; fields: ExtractionField[]; edits: Record<string, Value>;
  clearEdits: (keys: string[]) => void; canEdit: boolean;
}) {
  const t = useT();
  const patch = usePatchSettings();
  const values = s.extraction ?? {};
  const defaults = s.extraction_defaults ?? {};
  const current = (k: string): Value | undefined => (k in edits ? edits[k] : values[k]);
  const changedKeys = keys.filter((k) => k in edits && String(edits[k]) !== String(values[k]));
  const invalid = fields.filter((f) => keys.includes(f.key))
    .map((f) => boundError(f, current(f.key))).find(Boolean) ?? null;
  if (!canEdit) {
    return <div style={{ fontSize: 11, color: color.muted, margin: "4px 2px 14px" }}>{t("st.adminOnly")}</div>;
  }
  const blocked = patch.isPending || !!invalid || changedKeys.length === 0;
  const dirty = changedKeys.length > 0;
  return (
    <div style={{ position: dirty ? "sticky" : "static", bottom: 10, zIndex: 5, marginTop: 4,
                  marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
                    flexWrap: "wrap", padding: "10px 14px", borderRadius: radius.card,
                    background: changedKeys.length ? color.amberBg : color.surface,
                    border: `1px solid ${changedKeys.length ? "#f1dcae" : color.cardBorder}`,
                    boxShadow: changedKeys.length ? "0 4px 14px rgba(22,28,38,.10)" : "none" }}>
        <span data-testid="ex-message" role="status"
              style={{ fontSize: 11.5, lineHeight: 1.45, flex: "1 1 200px",
                       color: invalid || patch.isError ? color.redFg
                         : changedKeys.length ? color.amberFg : color.muted,
                       fontWeight: changedKeys.length || invalid ? 600 : 400 }}>
          {invalid
            ?? (patch.isError
                  ? `${(patch.error as { detail?: string } | null)?.detail ?? t("st.saveFailed")}`
                  : changedKeys.length
                    ? t("st.unsavedCount").replace("{n}", String(changedKeys.length))
                    : patch.isSuccess ? `✓ ${t("st.saved")} · ${t("st.nextRun")}` : t("st.nextRun"))}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <button type="button" data-testid="ex-reset" disabled={patch.isPending}
                  onClick={() => {
                    clearEdits(keys);
                    patch.mutate({ extraction: Object.fromEntries(
                      keys.filter((k) => k in defaults).map((k) => [k, defaults[k]])) });
                  }}
                  style={secondaryButton(patch.isPending)}>
            {t("st.restoreDefaults")}
          </button>
          <button type="button" data-testid="ex-save" disabled={blocked}
                  onClick={() => patch.mutate(
                    { extraction: Object.fromEntries(changedKeys.map((k) => [k, edits[k]])) },
                    { onSuccess: () => clearEdits(changedKeys) })}
                  style={primaryButton(blocked)}>
            {patch.isPending ? t("st.saving") : t("st.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** The knobs of one tab, under their section headings, with the tab's own Save and Restore
 *  defaults. Edits are held by the screen (`edits`), so moving to another tab and back keeps
 *  them. Every control is rendered from the backend's own descriptors, so a knob added there
 *  shows up with no change to this screen, and the bounds checked here are the API's. */
function KnobTab({ fields, st, clearEdits, extras }: {
  fields: ExtractionField[]; st: KnobState; clearEdits: (keys: string[]) => void;
  extras?: Record<string, ReactNode>;
}) {
  if (fields.length === 0) return null;
  return (
    <>
      <Card style={{ marginBottom: 12 }} pad={st.phone ? 14 : 18}>
        <KnobSections fields={fields} st={st} extras={extras} />
      </Card>
      <SaveBar s={st.s} keys={fields.map((f) => f.key)} fields={fields} edits={st.edits}
               clearEdits={clearEdits} canEdit={st.canEdit} />
    </>
  );
}

const primaryButton = (disabled: boolean): React.CSSProperties => ({
  fontSize: 12, fontWeight: 600, color: "#fff", background: disabled ? color.faint : color.indigo,
  border: "none", borderRadius: 8, padding: "8px 16px", cursor: disabled ? "default" : "pointer",
});
const secondaryButton = (disabled: boolean): React.CSSProperties => ({
  fontSize: 12, fontWeight: 600, color: color.sec2, background: "#fff",
  border: `1px solid ${color.controlBorder}`, borderRadius: 8, padding: "8px 14px",
  cursor: disabled ? "default" : "pointer",
});

/* ------------------------------ How filings are read ------------------------------ */

/** A switch for one of the extra model calls (row suggestions, disclosures, gap routing, the
 *  contingent-liabilities wording): a model switch on the Advanced tab. They go out whichever
 *  route reads the filing, Rules only included. */
const isExtraCall = (f: ExtractionField) =>
  f.tab === "advanced" && f.kind === "bool" && f.key.startsWith("llm_");

/** The words for a route as it applies to these values: its name, and for the model-questions
 *  route what the model reads with each question (and "focus line items only" when that is on). */
function routeText(r: ExtractionRoute | undefined, vals: Values, byKey: Map<string, ExtractionField>,
                   t: (k: string) => string): string {
  if (!r) return "";
  if (r.switch !== "llm_mapping") return r.name;
  const bits = [lowerFirst(choiceLabel(byKey.get("llm_document_context"), vals.llm_document_context))];
  if (vals.llm_focus_only) bits.push(t("st.summary.focusOnly"));
  return `${r.name} (${bits.filter(Boolean).join("; ")})`;
}

/** Why the model will not be asked at all, whatever a card says: no model configured, a provider
 *  that cannot be sent a file (whole PDF), or no key in the environment. Null when none applies.
 *  A route with no switch of its own (Rules only) asks the model nothing about line items, but
 *  `extraOn` extra calls may still be on: they meet the same stub provider and the same missing
 *  key, so the warning says so for them rather than leaving "they still go out" unqualified. */
function providerWarning(s: AppSettings, r: ExtractionRoute | undefined,
                         t: (k: string) => string, extraOn = 0): string | null {
  if (!r) return null;
  if (!r.switch) {
    if (extraOn <= 0) return null;
    if (s.llm.provider === "stub") return t("st.warn.noModelExtra");
    if (!s.llm.key_configured) return t("st.warn.noKeyExtra").replace("{env}", s.llm.api_key_env);
    return null;
  }
  if (s.llm.provider === "stub") return t("st.warn.noModel");
  if (r.switch === "whole_pdf_to_model" && s.llm.takes_files === false) {
    return t("st.warn.noFiles").replace("{p}", s.llm.provider);
  }
  if (!s.llm.key_configured) return t("st.warn.noKey").replace("{env}", s.llm.api_key_env);
  return null;
}

/** THE ONE LINE AT THE TOP OF EVERY TAB: how the next run reads a filing, from the SAVED values —
 *  what a run uses — and, while the route tab holds unsaved edits, a note that they are not in it. */
function RouteSummary({ s, routes, byKey, unsaved, onChange }: {
  s: AppSettings; routes: ExtractionRoute[]; byKey: Map<string, ExtractionField>;
  unsaved: boolean; onChange?: () => void;
}) {
  const t = useT();
  const values = s.extraction ?? {};
  const route = routeOf(routes, values);
  if (!route) return null;
  const fallback = route.fallback ? routeOf(routes, { ...values, ...route.fallback }) : undefined;
  // "Rules only" asks nothing about line items, but the extra calls have switches of their own.
  const extraOn = route.switch ? 0 : [...byKey.values()]
    .filter((f) => isExtraCall(f) && Boolean(values[f.key])).length;
  const warn = providerWarning(s, route, t, extraOn);
  // With the stub provider nothing goes out at all, so "still go out" would be untrue.
  const extraSent = extraOn > 0 && s.llm.provider !== "stub";
  return (
    <div data-testid="st-route-summary" data-route={route.id}
         style={{ padding: "10px 14px", borderRadius: radius.card, marginBottom: 14,
                  background: color.indigoTint, border: `1px solid ${color.indigoBorder}` }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "4px 12px", flexWrap: "wrap" }}>
        <span style={{ fontSize: 12.5, color: color.ink2, lineHeight: 1.55, flex: "1 1 260px", minWidth: 0 }}>
          {t("st.summary.readBy")} <b style={{ color: color.ink }}>{routeText(route, values, byKey, t)}</b>.
          {fallback && (
            <> {t("st.summary.ifFails")}{" "}
              <b style={{ color: color.ink }}>{lowerFirst(routeText(fallback, values, byKey, t))}</b>.</>
          )}
          {extraSent && (
            <span data-testid="st-route-summary-extra" style={{ color: color.sec2 }}>
              {" "}{t("st.summary.extraOn").replace("{n}", String(extraOn))}
            </span>
          )}
        </span>
        {unsaved && (
          <span data-testid="st-route-summary-unsaved"
                style={{ fontSize: 11, fontWeight: 600, color: color.amberFg }}>
            {t("st.summary.unsaved")}
          </span>
        )}
        {onChange && (
          <button type="button" data-testid="st-route-summary-change" onClick={onChange} style={linkButton}>
            {t("st.summary.change")}
          </button>
        )}
      </div>
      {warn && (
        <div style={{ fontSize: 11.5, color: color.amberFg, marginTop: 5, lineHeight: 1.5 }}>⚠ {warn}</div>
      )}
    </div>
  );
}

/** One route's card: a radio and its name, what it does, what it is best for, what it costs and
 *  its limits. The radio and the words are one label, so a click anywhere on the card picks it;
 *  arrow keys move between the three, as in any radio group. */
function RouteCard({ r, name, on, saved, isDefault, canEdit, onPick, phone }: {
  r: ExtractionRoute; name: string; on: boolean; saved: boolean; isDefault: boolean;
  canEdit: boolean; onPick: () => void; phone: boolean;
}) {
  const t = useT();
  const heads: [string, ReactNode][] = [
    [t("st.route.bestFor"), r.best_for],
    [t("st.route.cost"), r.cost],
    [t("st.route.limits"), (
      <span style={{ display: "block" }}>
        {r.limits.map((l) => (
          <span key={l} style={{ display: "flex", gap: 6, marginBottom: 3 }}>
            <span aria-hidden style={{ color: color.muted2 }}>•</span><span>{l}</span>
          </span>
        ))}
      </span>
    )],
  ];
  return (
    <label data-testid={`route-card-${r.id}`} data-on={on ? "true" : "false"}
           data-saved={saved ? "true" : "false"}
           style={{ display: "flex", flexDirection: "column", minWidth: 0,
                    cursor: canEdit ? "pointer" : "default", borderRadius: radius.card,
                    padding: phone ? "13px 14px" : "15px 16px",
                    background: on ? color.indigoTint : color.surface,
                    border: `1.5px solid ${on ? color.indigo : color.cardBorder}`,
                    boxShadow: on ? "0 2px 10px rgba(39,67,196,.08)" : "none" }}>
      <span style={{ display: "flex", alignItems: "flex-start", gap: 9 }}>
        <input type="radio" name={name} value={r.id} checked={on} disabled={!canEdit}
               data-testid={`route-pick-${r.id}`} onChange={onPick}
               style={{ accentColor: color.indigo, width: 16, height: 16, marginTop: 2, flex: "0 0 auto" }} />
        <span style={{ minWidth: 0 }}>
          <span style={{ display: "block", fontSize: 14, fontWeight: 600, lineHeight: 1.35,
                         color: on ? color.indigo : color.ink }}>{r.name}</span>
          {(saved || on || isDefault) && (
            <span style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 4 }}>
              {saved && <span data-testid={`route-in-use-${r.id}`} style={pill(color.greenBg, color.greenFg)}>{t("st.route.inUse")}</span>}
              {on && !saved && <span data-testid={`route-not-saved-${r.id}`} style={pill(color.amberBg, color.amberFg)}>{t("st.route.picked")}</span>}
              {isDefault && <span style={pill(color.segBg, color.sec2)}>{t("st.route.default")}</span>}
            </span>
          )}
        </span>
      </span>
      <span style={{ display: "block", fontSize: 12, color: color.sec, lineHeight: 1.55, marginTop: 8 }}>
        {r.summary}
      </span>
      {heads.map(([head, body]) => (
        <span key={head} style={{ display: "block", marginTop: 9 }}>
          <span style={{ display: "block", fontSize: 9.5, fontWeight: 700, letterSpacing: 0.5,
                         textTransform: "uppercase", color: color.muted, marginBottom: 2 }}>{head}</span>
          <span style={{ display: "block", fontSize: 11.5, color: color.sec2, lineHeight: 1.5 }}>{body}</span>
        </span>
      ))}
    </label>
  );
}

/** The chosen route's own options, in a panel under the cards (under the chosen card on a phone),
 *  headed by the route's name so it reads as that card's. */
function RouteOptionsPanel({ r, phone, warn, children }: {
  r: ExtractionRoute; phone: boolean; warn: string | null; children: ReactNode;
}) {
  const t = useT();
  return (
    <div data-testid={`route-options-${r.id}`}
         style={{ background: color.surface, borderRadius: radius.card, marginBottom: 12,
                  border: `1.5px solid ${color.indigo}`, padding: phone ? "12px 14px 14px" : "14px 18px 16px" }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, color: color.indigo,
                    textTransform: "uppercase", marginBottom: 4 }}>
        {t("st.route.optionsFor").replace("{route}", r.name)}
      </div>
      {warn && (
        <div style={{ fontSize: 11.5, color: color.amberFg, margin: "6px 0 4px", lineHeight: 1.5,
                      background: color.amberBg, borderRadius: 7, padding: "6px 9px" }}>⚠ {warn}</div>
      )}
      {children}
    </div>
  );
}

/** HOW FILINGS ARE READ: the three cards, the chosen one's options, and one Save for all of it. */
function RouteTab({ fields, routes, st, clearEdits, goTo }: {
  fields: ExtractionField[]; routes: ExtractionRoute[]; st: KnobState;
  clearEdits: (keys: string[]) => void; goTo: (tab: TabId, open?: string[]) => void;
}) {
  const t = useT();
  const name = useId();
  const fbName = useId();
  const values = st.s.extraction ?? {};
  const defaults = st.s.extraction_defaults ?? {};
  const current = (k: string): Value | undefined => (k in st.edits ? st.edits[k] : values[k]);
  const vals: Values = Object.fromEntries(st.all.map((f) => [f.key, current(f.key)]));
  const byKey = new Map(st.all.map((f) => [f.key, f]));
  const switches = new Set(routes.map((r) => r.switch).filter((k): k is string => !!k));
  const optionsOf = (r: ExtractionRoute | undefined) => (r?.switch
    ? fields.filter((f) => !switches.has(f.key) && rootOf(f, byKey) === r.switch) : []);
  const picked = routeOf(routes, vals);
  const saved = routeOf(routes, values);
  const shipped = routeOf(routes, defaults);
  const set = (sets: Record<string, boolean>) => {
    for (const [k, v] of Object.entries(sets)) st.setEdit(k, v);
  };
  // WHAT A CARD LEAVES ALONE IS WHAT IT WAS UNDER THAT CARD. Picking a card sets only its own
  // switches, so a switch another card set on the way rode along when the person came back: from
  // a saved Whole PDF, clicking Rules only (which turns the model's questions off) and then Whole
  // PDF again left "questions off" as an unsaved edit — the card read "In use" with no unsaved
  // badge, and Save changed what the route falls back to. Each card now keeps, while it is the
  // picked one, the switches it leaves alone (Whole PDF's fallback), and picking it again puts
  // those back: what the person set under it, else the saved value.
  const switchKeys = [...new Set(routes.flatMap((r) => Object.keys(r.sets)))];
  const leftAlone = useRef<Record<string, Record<string, Value | undefined>>>({});
  useEffect(() => {
    if (!picked) return;
    leftAlone.current[picked.id] = Object.fromEntries(
      switchKeys.filter((k) => !(k in picked.sets)).map((k) => [k, current(k)]));
  });
  const pickCard = (r: ExtractionRoute) => {
    set(r.sets);
    const under = leftAlone.current[r.id] ?? {};
    const others = switchKeys.filter((k) => !(k in r.sets));
    const back = others.filter((k) => k in under && under[k] !== undefined
                                      && String(under[k]) !== String(values[k]));
    for (const k of back) st.setEdit(k, under[k] as Value);
    clearEdits(others.filter((k) => !back.includes(k)));
  };
  const fallbackKeys = picked?.fallback ? Object.keys(picked.fallback) : [];
  const fallback = picked?.fallback ? routeOf(routes, { ...vals, ...picked.fallback }) : undefined;
  // The routes a failed run can land on: those whose values agree with the fallback's.
  const fallbackChoices = picked?.fallback
    ? routes.filter((r) => fallbackKeys.every((k) => r.sets[k] === picked.fallback![k])) : [];
  // A fallback with unsaved edits stays open, so what is about to be saved is in view.
  const fallbackEdited = !!picked?.fallback && [...optionsOf(fallback),
    ...fallbackChoices.flatMap((c) => Object.keys(c.sets).filter((k) => !fallbackKeys.includes(k)))
      .map((k) => byKey.get(k)).filter((f): f is ExtractionField => !!f)]
    .some((f) => f.key in st.edits && String(st.edits[f.key]) !== String(values[f.key]));
  const [fallbackOpen, setFallbackOpen] = useState(false);
  const showFallback = fallbackOpen || fallbackEdited;
  const extraCalls = st.all.filter(isExtraCall);
  const extraOn = extraCalls.filter((f) => Boolean(values[f.key])).length;
  const focusLink = {
    "which-lines-are-asked": (
      <div style={{ padding: "8px 0 2px" }}>
        <button type="button" data-testid="route-focus-list" onClick={() => goTo("focus")} style={linkButton}>
          {t("st.route.focusList")} →
        </button>
      </div>
    ),
  };

  const options = (r: ExtractionRoute) => {
    if (!r.switch) {
      // The extra calls still go out on this route — unless no model can be asked at all.
      const warnExtra = providerWarning(st.s, r, t, extraOn);
      const stub = st.s.llm.provider === "stub";
      return (
        <div data-testid="route-no-options" style={{ fontSize: 11.5, color: color.sec2, lineHeight: 1.6 }}>
          <div>{t("st.route.noOptions")}</div>
          {extraOn > 0 && (
            <div data-testid="route-extra-calls" style={{ color: color.amberFg }}>
              {stub ? `⚠ ${warnExtra}`
                : t("st.route.extraOn").replace("{n}", String(extraOn)).replace("{m}", String(extraCalls.length))}{" "}
              <button type="button" onClick={() => goTo("advanced", ["extra-model-calls"])} style={linkButton}>
                {t("st.route.openExtra")}
              </button>
              {!stub && warnExtra && <div>⚠ {warnExtra}</div>}
            </div>
          )}
          <div>
            {t("st.route.thresholds")}{" "}
            <button type="button" onClick={() => goTo("advanced", ["matching-captions", "reconciliation"])}
                    style={linkButton}>{t("st.route.openThresholds")}</button>
          </div>
        </div>
      );
    }
    return (
      <>
        <KnobSections fields={optionsOf(r)} st={st} extras={focusLink} />
        {r.fallback && (
          <div data-testid="route-fallback" data-route={fallback?.id ?? ""}
               style={{ marginTop: 14, padding: "10px 12px", borderRadius: radius.control,
                        background: color.rowAltBg, border: `1px solid ${color.hairline3}` }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: "4px 10px", flexWrap: "wrap" }}>
              <span style={{ fontSize: 12, color: color.ink2, lineHeight: 1.5, flex: "1 1 220px", minWidth: 0 }}>
                {t("st.route.fallback")}{" "}
                <b data-testid="route-fallback-text" style={{ color: color.ink }}>
                  {lowerFirst(routeText(fallback, vals, byKey, t))}
                </b>
              </span>
              {st.canEdit && !fallbackEdited && (
                <button type="button" data-testid="route-fallback-change" aria-expanded={showFallback}
                        onClick={() => setFallbackOpen((o) => !o)} style={linkButton}>
                  {showFallback ? t("st.route.fallbackDone") : t("st.route.fallbackChange")}
                </button>
              )}
            </div>
            {showFallback && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 11, color: color.muted, marginBottom: 6 }}>{t("st.route.fallbackHelp")}</div>
                <div role="radiogroup" aria-label={t("st.route.fallback")}
                     style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 4 }}>
                  {fallbackChoices.map((c) => {
                    const on = c.id === fallback?.id;
                    return (
                      <label key={c.id}
                             style={{ display: "flex", alignItems: "center", gap: 7, padding: "6px 11px",
                                      borderRadius: radius.control, cursor: "pointer", fontSize: 12,
                                      fontWeight: 600, color: on ? color.indigo : color.ink2,
                                      background: on ? color.indigoTint : "#fff",
                                      border: `1px solid ${on ? color.indigoBorder2 : color.controlBorder}` }}>
                        <input type="radio" name={fbName} checked={on} data-testid={`fallback-pick-${c.id}`}
                               onChange={() => set(Object.fromEntries(
                                 Object.entries(c.sets).filter(([k]) => !fallbackKeys.includes(k))))}
                               style={{ accentColor: color.indigo }} />
                        {c.name}
                      </label>
                    );
                  })}
                </div>
                {fallback?.switch && <KnobSections fields={optionsOf(fallback)} st={st} extras={focusLink} />}
              </div>
            )}
          </div>
        )}
      </>
    );
  };

  // The summary above already warns about the SAVED route; the panel warns about a picked one.
  const panel = picked && (
    <RouteOptionsPanel r={picked} phone={st.phone}
                       warn={picked.id !== saved?.id && picked.switch
                         ? providerWarning(st.s, picked, t) : null}>
      {options(picked)}
    </RouteOptionsPanel>
  );
  return (
    <>
      <fieldset style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend style={srOnly}>{t("st.route.legend")}</legend>
        <div style={{ display: "grid", gap: 12, marginBottom: 12,
                      gridTemplateColumns: st.phone ? "minmax(0,1fr)"
                        : `repeat(${Math.max(1, routes.length)}, minmax(0,1fr))` }}>
          {routes.map((r) => (
            <div key={r.id} style={{ display: "contents" }}>
              <RouteCard r={r} name={name} on={picked?.id === r.id} saved={saved?.id === r.id}
                         isDefault={shipped?.id === r.id} canEdit={st.canEdit} phone={st.phone}
                         onPick={() => pickCard(r)} />
              {/* On a phone the cards are one column, so the options go straight under theirs. */}
              {st.phone && picked?.id === r.id && panel}
            </div>
          ))}
        </div>
      </fieldset>
      {!st.phone && panel}
      <SaveBar s={st.s} keys={fields.map((f) => f.key)} fields={fields} edits={st.edits}
               clearEdits={clearEdits} canEdit={st.canEdit} />
    </>
  );
}

/** The engines a page can be read with, read-only, at the end of the "Pages and OCR" group. */
function EngineRows({ s }: { s: AppSettings }) {
  const t = useT();
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, color: color.muted,
                    textTransform: "uppercase" }}>
        {t("st.engines")} <span style={{ fontWeight: 500, textTransform: "none", letterSpacing: 0 }}>· {t("st.readOnlyShort")}</span>
      </div>
      <Row label={t("st.engine")} value={s.ocr.engine} />
      <Row label={t("st.languages")} value={s.ocr.languages.join(" · ")} />
      <Row label={t("st.dpi")} value={s.ocr.dpi} />
      {s.ocr.docling && (
        <Row label={t("st.doclingReady")}
             value={s.ocr.docling.installed && s.ocr.docling.models_present
               ? t("st.doclingYes")
               : !s.ocr.docling.installed ? t("st.doclingNotInstalled")
               : t("st.doclingNoModels").replace("{dir}", s.ocr.docling.models_dir)} />
      )}
      {s.document_engine && (
        <Row label={t("st.kensho")}
             value={!s.document_engine.kensho.addresses_set ? t("st.kenshoNoAddress")
               : !s.document_engine.kensho.token_present
                 ? t("st.kenshoNoToken").replace("{env}", s.document_engine.kensho.token_env)
               : t("st.kenshoReady")} />
      )}
    </div>
  );
}

/* ---------------------------- FX rate master (admin) ---------------------------- */

/* ISO-4217 shape, mirroring the server's own check so a typo is caught before a round trip
 * (the server still enforces it — this only makes the feedback immediate). */
const CCY_RE = /^[A-Z]{3}$/;

const BLANK_FX: FxRateInput = { base: "", quote: "", rate: "", as_of: "", source: "" };

/** Client-side validation mirroring the API's rules; returns a localized message or null.
 *  `Number` is used purely as a positivity/finiteness gate — the rate itself is sent as the
 *  typed STRING so an exact decimal like 0.0121 is never routed through a float. */
function fxFormError(f: FxRateInput, t: (k: string) => string): string | null {
  const base = f.base.trim().toUpperCase();
  const quote = f.quote.trim().toUpperCase();
  if (!CCY_RE.test(base) || !CCY_RE.test(quote)) return t("st.fx.errCode");
  if (base === quote) return t("st.fx.errSame");
  const probe = Number(f.rate.trim());
  if (!f.rate.trim() || !Number.isFinite(probe) || probe <= 0) return t("st.fx.errRate");
  return null;
}

const fxCell: React.CSSProperties = {
  fontSize: 11.5, fontFamily: font.mono, color: color.ink, padding: "7px 8px",
  borderBottom: `1px solid ${color.hairline}`, textAlign: "left",
};
const fxHead: React.CSSProperties = {
  fontSize: 10, fontWeight: 600, letterSpacing: 0.4, color: color.muted,
  padding: "6px 8px", borderBottom: `1px solid ${color.cardBorder}`, textAlign: "left",
};

/** The FX rate master: the admin-maintained rates the Workspace converts with.
 *  Non-admins see the rates read-only — no add/edit/delete controls at all. */
function FxRatesCard({ canEdit }: { canEdit: boolean }) {
  const t = useT();
  const { data, isPending } = useFxRates();
  const upsert = useUpsertFxRate();
  const update = useUpdateFxRate();
  const remove = useDeleteFxRate();
  // `editingId` null = the add form; a row id = editing that row in place.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FxRateInput>(BLANK_FX);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof FxRateInput, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const reset = () => { setEditingId(null); setForm(BLANK_FX); setError(null); };
  const startEdit = (r: FxRate) => {
    setEditingId(r.id);
    setForm({ base: r.base, quote: r.quote, rate: r.rate, as_of: r.as_of, source: r.source });
    setError(null);
  };

  const submit = () => {
    const err = fxFormError(form, t);
    if (err) { setError(err); return; }
    const body: FxRateInput = {
      base: form.base.trim().toUpperCase(),
      quote: form.quote.trim().toUpperCase(),
      rate: form.rate.trim(),
      // Omitted rather than empty: the server then dates the rate today, instead of us
      // inventing a date the admin never chose.
      as_of: form.as_of?.trim() ? form.as_of.trim() : undefined,
      source: form.source?.trim() ?? "",
    };
    // Surface the server's own rejection message — it names which rule failed.
    const onError = (e: unknown) =>
      setError(e instanceof ApiError ? (e.detail ?? e.message) : String(e));
    if (editingId) update.mutate({ id: editingId, body }, { onSuccess: reset, onError });
    else upsert.mutate(body, { onSuccess: reset, onError });
  };

  const rates = data?.rates ?? [];
  const saving = upsert.isPending || update.isPending;

  return (
    <SectionCard title={t("st.fx")} note={canEdit ? undefined : t("st.readOnly")}>
      <div style={{ fontSize: 11.5, color: color.sec2, lineHeight: 1.55, marginBottom: 10 }}>
        {t("st.fxHelp")}
      </div>

      {isPending ? (
        <div style={{ fontSize: 11.5, color: color.muted }}>{t("st.loading")}</div>
      ) : rates.length === 0 ? (
        <div style={{ fontSize: 11.5, color: color.amberFg, background: color.amberBg,
                      padding: "8px 10px", borderRadius: 7, lineHeight: 1.5 }}>
          {t("st.fx.empty")}
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={fxHead}>{t("st.fx.base")}</th>
                <th style={fxHead}>{t("st.fx.quote")}</th>
                <th style={{ ...fxHead, textAlign: "right" }}>{t("st.fx.rate")}</th>
                <th style={fxHead}>{t("st.fx.asOf")}</th>
                <th style={fxHead}>{t("st.fx.source")}</th>
                {canEdit && <th style={{ ...fxHead, textAlign: "right" }} />}
              </tr>
            </thead>
            <tbody>
              {rates.map((r) => (
                <tr key={r.id} style={{ background: r.id === editingId ? color.indigoTint2 : "transparent" }}>
                  <td style={fxCell}>{r.base}</td>
                  <td style={fxCell}>{r.quote}</td>
                  <td style={{ ...fxCell, textAlign: "right", fontWeight: 600 }}>{r.rate}</td>
                  <td style={fxCell}>{r.as_of}</td>
                  <td style={{ ...fxCell, fontFamily: font.sans, color: color.sec2 }}>
                    {r.source || "—"}
                  </td>
                  {canEdit && (
                    <td style={{ ...fxCell, textAlign: "right", whiteSpace: "nowrap" }}>
                      <button
                        onClick={() => startEdit(r)}
                        style={{ fontSize: 11, fontWeight: 600, color: color.indigo, background: "#fff",
                                 border: `1px solid ${color.indigoBorder2}`, borderRadius: radius.controlSm,
                                 padding: "4px 9px", cursor: "pointer", marginInlineEnd: 6 }}
                      >
                        {t("st.fx.edit")}
                      </button>
                      <button
                        onClick={() => { if (r.id === editingId) reset(); remove.mutate(r.id); }}
                        disabled={remove.isPending}
                        style={{ fontSize: 11, fontWeight: 600, color: color.redFg, background: "#fff",
                                 border: `1px solid ${color.controlBorder}`, borderRadius: radius.controlSm,
                                 padding: "4px 9px", cursor: "pointer" }}
                      >
                        {t("st.fx.delete")}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!canEdit && (
        <div style={{ fontSize: 11, color: color.muted, marginTop: 10 }}>{t("st.adminOnly")}</div>
      )}

      {canEdit && (
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${color.hairline}` }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>
            {editingId ? t("st.fx.editing") : t("st.fx.add")}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(118px, 1fr))", gap: 10 }}>
            <Field label={t("st.fx.base")}>
              <input value={form.base} maxLength={3} placeholder="USD" spellCheck={false}
                     onChange={(e) => set("base", e.target.value.toUpperCase())} style={inputStyle} />
            </Field>
            <Field label={t("st.fx.quote")}>
              <input value={form.quote} maxLength={3} placeholder="INR" spellCheck={false}
                     onChange={(e) => set("quote", e.target.value.toUpperCase())} style={inputStyle} />
            </Field>
            <Field label={t("st.fx.rate")}>
              <input value={form.rate} inputMode="decimal" placeholder="83.25" spellCheck={false}
                     onChange={(e) => set("rate", e.target.value)} style={inputStyle} />
            </Field>
            <Field label={t("st.fx.asOf")}>
              <input type="date" value={form.as_of ?? ""}
                     onChange={(e) => set("as_of", e.target.value)} style={inputStyle} />
            </Field>
            <Field label={t("st.fx.source")}>
              <input value={form.source ?? ""} placeholder={t("st.fx.sourceHint")}
                     onChange={(e) => set("source", e.target.value)} style={inputStyle} />
            </Field>
          </div>
          <div style={{ fontSize: 10.5, color: color.muted2, marginTop: 7 }}>
            {t("st.fx.direction")}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12, flexWrap: "wrap" }}>
            <button
              onClick={submit}
              disabled={saving}
              style={{ fontSize: 12, fontWeight: 600, color: "#fff",
                       background: saving ? color.faint : color.indigo, border: "none",
                       borderRadius: 8, padding: "8px 16px", cursor: saving ? "default" : "pointer" }}
            >
              {saving ? t("st.saving") : editingId ? t("st.save") : t("st.fx.add")}
            </button>
            {editingId && (
              <button
                onClick={reset}
                style={{ fontSize: 12, fontWeight: 600, color: color.ink2, background: "#fff",
                         border: `1px solid ${color.controlBorder}`, borderRadius: 8,
                         padding: "8px 14px", cursor: "pointer" }}
              >
                {t("st.fx.cancel")}
              </button>
            )}
            {error && (
              <span style={{ fontSize: 11.5, color: color.redFg, fontWeight: 600 }}>{error}</span>
            )}
          </div>
        </div>
      )}
    </SectionCard>
  );
}

/* ------------------------- Focus line items master (admin) ------------------------- */

/** How an entry is read by a focus run, as a badge. */
function FocusKind({ item }: { item: FocusLineItem }) {
  const t = useT();
  if (item.matched === 0) return <span style={pill(color.redBg, color.redFg)}>{t("st.focus.dead")}</span>;
  if (item.pattern) {
    return <span style={pill(color.indigoTint2, color.indigo)}>
      {t("st.focus.pattern").replace("{n}", String(item.matched))}</span>;
  }
  if (item.kind === "computed") {
    return <span style={pill(color.amberBg, color.amberFg)}>{t("st.focus.computed")}</span>;
  }
  return <span style={pill(color.greenBg, color.greenFg)}>{t("st.focus.asked")}</span>;
}

/** The Focus line items master: which line items a focus run of each template asks the model about.
 *
 *  ONE LIST PER TEMPLATE, picked at the top of the card: a run reads its own template's list and no
 *  other's, so a line several sets hold (Inventories, Goodwill, ...) is in focus only for the
 *  templates that list it. Whether a run is restricted to its list at all is an option of the
 *  "Rules + model questions" route (`llm_focus_only`, on How filings are read), and the banner says
 *  which route reads filings now, because the list does nothing on the others. Search the
 *  template's set, add a line — a line
 *  computed from its parts goes in with the parts its cascade reads, since it is never asked itself
 *  — or a prefix ending in *. Non-admins see the lists read-only. */
function FocusLineItemsCard({ canEdit, focusOnly, route, onRoute, template, onTemplate }: {
  canEdit: boolean; focusOnly: boolean; route?: string; onRoute: () => void; template?: string;
  onTemplate: (key: string) => void;
}) {
  const t = useT();
  const { data, isPending } = useFocusLineItems(template);
  const add = useAddFocusLineItem();
  const remove = useDeleteFocusLineItem();
  const restore = useRestoreFocusLineItems();
  const [query, setQuery] = useState("");
  const [note, setNote] = useState("");
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const current = data?.template ?? "";
  const templates = data?.templates ?? [];
  const pick = (key: string) => {
    setQuery(""); setFilter(""); setError(null); setMessage(null);
    onTemplate(key);
  };

  const items = data?.items ?? [];
  const shipped = data?.shipped ?? [];
  const inFocus = new Set(items.map((i) => i.key));
  const lines = data?.candidates ?? [];
  const q = query.trim().toLowerCase();
  const isPattern = q.endsWith("*") && q.length > 2;
  const prefix = isPattern ? q.slice(0, -1) : "";
  const patternCount = isPattern ? lines.filter((ln) => ln.key.startsWith(prefix)).length : 0;
  const results = q.length < 2 || isPattern ? [] : lines
    .filter((ln) => !inFocus.has(ln.key)
      && (ln.key.includes(q) || ln.label.toLowerCase().includes(q)))
    .slice(0, 12);
  const f = filter.trim().toLowerCase();
  const shown = f ? items.filter((i) => i.key.includes(f) || i.label.toLowerCase().includes(f)
    || i.note.toLowerCase().includes(f)) : items;

  const onError = (e: unknown) =>
    setError(e instanceof ApiError ? (e.detail ?? e.message) : String(e));
  const addKey = (key: string) => {
    setError(null);
    setMessage(null);
    add.mutate({ template: current, key, note: note.trim() || undefined }, {
      onSuccess: (res) => {
        setMessage(t("st.focus.added").replace("{n}", String(res.added?.length ?? 0)));
        setQuery("");
        setNote("");
      },
      onError,
    });
  };
  const restoreShipped = () => {
    if (!window.confirm(t("st.focus.restoreConfirm").replace("{n}", String(shipped.length)))) return;
    setError(null);
    setMessage(null);
    restore.mutate(current, { onError });
  };
  const busy = add.isPending || remove.isPending || restore.isPending;

  return (
    <SectionCard title={t("st.focus")} note={canEdit ? undefined : t("st.readOnly")}>
      <div style={{ fontSize: 11.5, color: color.sec2, lineHeight: 1.55, marginBottom: 10 }}>
        {t("st.focusHelp")}
      </div>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, color: color.muted,
                    textTransform: "uppercase", marginBottom: 6 }}>{t("st.focus.template")}</div>
      <div role="tablist" data-testid="focus-templates"
           style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
        {templates.map((tp) => {
          const on = tp.key === current;
          return (
            <button key={tp.key} role="tab" aria-selected={on} title={tp.key}
                    data-testid={`focus-template-${tp.key}`} data-on={on ? "true" : "false"}
                    onClick={() => !on && pick(tp.key)}
                    style={{ fontSize: 12, fontWeight: 600, padding: "6px 11px", borderRadius: 8,
                             cursor: on ? "default" : "pointer",
                             color: on ? color.indigo : color.ink2,
                             background: on ? color.indigoTint2 : "#fff",
                             border: `1px solid ${on ? color.indigoBorder2 : color.controlBorder}` }}>
              {tp.name}
              <span style={{ fontWeight: 500, color: on ? color.indigo : color.muted,
                             marginInlineStart: 6 }}>{tp.entries}</span>
            </button>
          );
        })}
      </div>
      <div data-testid="focus-state" data-on={focusOnly ? "true" : "false"}
           style={{ fontSize: 11.5, lineHeight: 1.5, padding: "8px 10px", borderRadius: 7,
                    marginBottom: 12,
                    color: focusOnly ? color.greenFg : color.amberFg,
                    background: focusOnly ? color.greenBg : color.amberBg }}>
        {focusOnly ? t("st.focus.on").replace("{n}", String(items.length)) : t("st.focus.off")}
        {focusOnly && route === "whole_pdf" && (
          <div style={{ marginTop: 4, color: color.amberFg }}>{t("st.focus.notUsedWholePdf")}</div>
        )}
        {route === "rules" && (
          <div style={{ marginTop: 4, color: color.amberFg }}>{t("st.focus.notUsedRules")}</div>
        )}
        <div style={{ marginTop: 4 }}>
          <button type="button" data-testid="focus-switch-link" onClick={onRoute} style={linkButton}>
            {t("st.focus.changeSwitch")} →
          </button>
        </div>
      </div>

      {canEdit && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{t("st.focus.add")}</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
            <input data-testid="focus-search" value={query} spellCheck={false}
                   placeholder={t("st.focus.search")}
                   onChange={(e) => { setQuery(e.target.value); setError(null); setMessage(null); }}
                   style={inputStyle} />
            <input data-testid="focus-note" value={note} placeholder={t("st.focus.why")}
                   onChange={(e) => setNote(e.target.value)}
                   style={{ ...inputStyle, fontFamily: font.sans }} />
          </div>
          {(results.length > 0 || isPattern || (q.length >= 2 && !isPattern)) && (
            <div data-testid="focus-results"
                 style={{ marginTop: 8, border: `1px solid ${color.cardBorder}`, borderRadius: 8,
                          maxHeight: 300, overflowY: "auto" }}>
              {isPattern && (
                <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px" }}>
                  <span style={{ fontSize: 12, flex: 1, minWidth: 0 }}>
                    {t("st.focus.addPattern").replace("{p}", q).replace("{n}", String(patternCount))}
                  </span>
                  <button disabled={busy || patternCount === 0} onClick={() => addKey(q)}
                          style={addButton(busy || patternCount === 0)}>{t("st.focus.addOne")}</button>
                </div>
              )}
              {!isPattern && results.length === 0 && (
                <div style={{ fontSize: 11.5, color: color.muted, padding: "8px 10px" }}>
                  {t("st.focus.noMatch")}
                </div>
              )}
              {results.map((ln) => (
                <div key={ln.key} data-testid="focus-result"
                     style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 10px",
                              borderTop: `1px solid ${color.hairline}` }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: color.ink }}>{ln.label}</div>
                    <div style={{ fontSize: 10.5, fontFamily: font.mono, color: color.sec2 }}>
                      {ln.key}{ln.statement ? ` · ${ln.statement}` : ""}
                    </div>
                  </div>
                  {ln.kind === "computed" && (
                    <span style={pill(color.amberBg, color.amberFg)}>{t("st.focus.computed")}</span>
                  )}
                  <button data-testid={`focus-add-${ln.key}`} disabled={busy} onClick={() => addKey(ln.key)}
                          style={addButton(busy)}>
                    {ln.kind === "computed" && ln.parts.length > 0
                      ? t("st.focus.addWithParts").replace("{n}", String(ln.parts.length))
                      : t("st.focus.addOne")}
                  </button>
                </div>
              ))}
            </div>
          )}
          {(error || message) && (
            <div data-testid="focus-message"
                 style={{ fontSize: 11.5, fontWeight: 600, marginTop: 8,
                          color: error ? color.redFg : color.greenFg }}>
              {error ?? `✓ ${message}`}
            </div>
          )}
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10,
                    marginBottom: 6, flexWrap: "wrap" }}>
        <span data-testid="focus-count" style={{ fontSize: 11, color: color.muted }}>
          {t("st.focus.count").replace("{n}", String(items.length))}
        </span>
        {items.length > 8 && (
          <input value={filter} placeholder={t("st.focus.filter")} spellCheck={false}
                 onChange={(e) => setFilter(e.target.value)}
                 style={{ ...inputStyle, width: "min(220px, 100%)", fontFamily: font.sans }} />
        )}
      </div>

      {isPending ? (
        <div style={{ fontSize: 11.5, color: color.muted }}>{t("st.loading")}</div>
      ) : items.length === 0 ? (
        <div style={{ fontSize: 11.5, color: color.amberFg, background: color.amberBg,
                      padding: "8px 10px", borderRadius: 7, lineHeight: 1.5 }}>
          {t("st.focus.empty")}
        </div>
      ) : (
        <div style={{ overflowX: "auto", maxHeight: 420, overflowY: "auto" }}>
          <table data-testid="focus-table"
                 style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
            <colgroup>
              <col style={{ width: canEdit ? "40%" : "44%" }} />
              <col style={{ width: "20%" }} />
              <col />
              <col style={{ width: "10%" }} />
              {canEdit && <col style={{ width: 82 }} />}
            </colgroup>
            <thead>
              <tr>
                <th style={fxHead}>{t("st.focus.line")}</th>
                <th style={fxHead}>{t("st.focus.kind")}</th>
                <th style={fxHead}>{t("st.focus.note")}</th>
                <th style={fxHead}>{t("st.focus.source")}</th>
                {canEdit && <th style={{ ...fxHead, textAlign: "right" }} />}
              </tr>
            </thead>
            <tbody>
              {shown.map((i) => (
                <tr key={i.id} data-testid="focus-row" data-key={i.key}>
                  <td style={{ ...fxCell, fontFamily: font.sans, overflowWrap: "anywhere" }}>
                    {i.label && <div style={{ fontWeight: 600 }}>{i.label}</div>}
                    <div style={{ fontFamily: font.mono, fontSize: 10.5, color: color.sec2 }}>{i.key}</div>
                  </td>
                  <td style={fxCell}><FocusKind item={i} /></td>
                  <td style={{ ...fxCell, fontFamily: font.sans, color: color.sec2, overflowWrap: "anywhere" }}>
                    {i.note || "—"}
                  </td>
                  <td style={{ ...fxCell, fontFamily: font.sans, color: color.muted }}>
                    {i.source === "shipped" ? t("st.focus.shipped") : t("st.focus.admin")}
                  </td>
                  {canEdit && (
                    <td style={{ ...fxCell, textAlign: "right" }}>
                      <button data-testid={`focus-remove-${i.key}`} disabled={busy}
                              onClick={() => { setError(null); setMessage(null);
                                               remove.mutate(i.id, { onError }); }}
                              style={{ fontSize: 11, fontWeight: 600, color: color.redFg, background: "#fff",
                                       border: `1px solid ${color.controlBorder}`,
                                       borderRadius: radius.controlSm, padding: "4px 9px",
                                       cursor: busy ? "default" : "pointer" }}>
                        {t("st.focus.remove")}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!canEdit && (
        <div style={{ fontSize: 11, color: color.muted, marginTop: 10 }}>{t("st.adminOnly")}</div>
      )}
      {canEdit && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
          <button data-testid="focus-restore" onClick={restoreShipped} disabled={busy}
                  style={{ fontSize: 12, fontWeight: 600, color: color.sec2, background: "#fff",
                           border: `1px solid ${color.controlBorder}`, borderRadius: 8,
                           padding: "8px 14px", cursor: busy ? "default" : "pointer" }}>
            {t("st.focus.restore").replace("{n}", String(shipped.length))}
          </button>
        </div>
      )}
    </SectionCard>
  );
}

const addButton = (disabled: boolean): React.CSSProperties => ({
  fontSize: 11, fontWeight: 600, color: "#fff", whiteSpace: "nowrap",
  background: disabled ? color.faint : color.indigo, border: "none",
  borderRadius: radius.controlSm, padding: "5px 11px", cursor: disabled ? "default" : "pointer",
});

/** The screen's tabs, in order. The extraction knobs say which of these they sit on
 *  (`ExtractionField.tab`); a knob naming a tab this build does not know lands on "other", shown
 *  only when something is on it, rather than vanishing. */
const TABS = ["route", "advanced", "focus", "fx", "general", "system"] as const;
type TabId = (typeof TABS)[number] | "other";

/** The tabs this screen had before the route picker, so a link or a bookmark to one still lands
 *  where its settings went, with their groups open. */
const TAB_ALIASES: Record<string, { tab: TabId; open: string[] }> = {
  model: { tab: "advanced", open: ["extra-model-calls", "sending-requests"] },
  reading: { tab: "advanced", open: ["pages-and-ocr"] },
  mapping: { tab: "advanced", open: ["matching-captions", "reconciliation"] },
};

export default function SettingsScreen() {
  const t = useT();
  const { data, isPending } = useSettings();
  const patch = usePatchSettings();
  const canEdit = useCan("config:settings");
  const phone = useMediaQuery(PHONE);
  const [params, setParams] = useSearchParams();
  // Edits not yet saved, by knob. Held here rather than in a tab, so moving between tabs keeps
  // them; each tab saves its own.
  const [edits, setEdits] = useState<Record<string, Value>>({});
  // Folded groups opened by the reader (by section slug); a link can open some (`?open=` or an
  // old tab name).
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => {
    const asked = params.get("tab") ?? "";
    return new Set([...(TAB_ALIASES[asked]?.open ?? []),
                     ...(params.get("open") ?? "").split(",").filter(Boolean)]);
  });

  if (isPending || !data) {
    return <div style={{ padding: 60, textAlign: "center", color: color.muted }}>{t("st.loading")}</div>;
  }
  const s: AppSettings = data;
  const fields = s.extraction_fields ?? [];
  const routes = s.extraction_routes ?? [];
  const byKey = new Map(fields.map((f) => [f.key, f]));
  const known = new Set<string>(TABS);
  const tabOf = (f: ExtractionField): TabId => (known.has(f.tab) ? (f.tab as TabId) : "other");
  const tabs: TabId[] = fields.some((f) => tabOf(f) === "other") ? [...TABS, "other"] : [...TABS];
  const askedRaw = params.get("tab") ?? "";
  const asked = (TAB_ALIASES[askedRaw]?.tab ?? askedRaw) as TabId;
  const tab: TabId = asked && tabs.includes(asked) ? asked : "route";
  const values = s.extraction ?? {};
  const unsaved = (id: TabId) => fields.some(
    (f) => tabOf(f) === id && f.key in edits && String(edits[f.key]) !== String(values[f.key]));
  const goTo = (id: TabId, open: string[] = []) => {
    if (open.length) setOpenGroups((g) => new Set([...g, ...open]));
    setParams((p) => { p.set("tab", id); p.delete("open"); return p; }, { replace: true });
  };
  const clearEdits = (keys: string[]) => setEdits((e) => {
    const next = { ...e };
    for (const k of keys) delete next[k];
    return next;
  });
  const st: KnobState = {
    s, all: fields, edits, canEdit, phone, openGroups,
    setEdit: (k, v) => setEdits((e) => ({ ...e, [k]: v })),
    toggleGroup: (id) => setOpenGroups((g) => {
      const next = new Set(g);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    }),
  };
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = tabs.indexOf(tab);
    const next = e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length]
      : e.key === "ArrowLeft" ? tabs[(i - 1 + tabs.length) % tabs.length]
      : e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[tabs.length - 1] : null;
    if (!next) return;
    e.preventDefault();
    goTo(next);
    requestAnimationFrame(() => document.getElementById(`st-tab-${next}`)?.focus());
  };

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: phone ? "18px 16px 40px" : "26px 30px 60px" }}>
      <ScreenHeader title={t("st.title")} subtitle={t("st.subhead")} />

      {routes.length > 0 && (
        <RouteSummary s={s} routes={routes} byKey={byKey} unsaved={unsaved("route")}
                      onChange={tab === "route" ? undefined : () => goTo("route")} />
      )}

      <div role="tablist" aria-label={t("st.title")} onKeyDown={onTabKey}
           style={{ display: "flex", gap: phone ? 6 : 2, flexWrap: "wrap", marginBottom: 6,
                    borderBottom: phone ? "none" : `1px solid ${color.cardBorder}` }}>
        {tabs.map((id) => {
          const on = id === tab;
          return (
            <button key={id} id={`st-tab-${id}`} role="tab" aria-selected={on}
                    aria-controls="st-tabpanel" tabIndex={on ? 0 : -1} data-testid={`st-tab-${id}`}
                    onClick={() => goTo(id)}
                    style={phone ? {
                      fontSize: 12, fontWeight: 600, padding: "6px 11px", cursor: "pointer",
                      borderRadius: radius.pill, color: on ? color.indigo : color.sec2,
                      background: on ? color.indigoTint2 : "#fff",
                      border: `1px solid ${on ? color.indigoBorder2 : color.controlBorder}`,
                    } : {
                      fontSize: 12.5, fontWeight: 600, padding: "8px 12px 9px",
                      background: "none", border: "none", cursor: "pointer",
                      color: on ? color.indigo : color.sec2, marginBottom: -1,
                      borderBottom: `2px solid ${on ? color.indigo : "transparent"}`,
                    }}>
              {t(`st.tab.${id}`)}
              {unsaved(id) && (
                <span title={t("st.unsaved")} data-testid={`st-tab-unsaved-${id}`}
                      style={{ display: "inline-block", width: 6, height: 6, borderRadius: 3,
                               background: color.amberFg, marginInlineStart: 6,
                               verticalAlign: "middle" }} />
              )}
            </button>
          );
        })}
      </div>

      <div id="st-tabpanel" role="tabpanel" aria-labelledby={`st-tab-${tab}`}>
        <div style={{ fontSize: 11.5, color: color.muted, margin: "8px 0 14px", lineHeight: 1.5 }}>
          {t(`st.tabIntro.${tab}`)}
        </div>

        {tab === "route" && (
          <RouteTab fields={fields.filter((f) => tabOf(f) === "route")} routes={routes} st={st}
                    clearEdits={clearEdits} goTo={goTo} />
        )}

        {tab === "advanced" && (
          <KnobTab fields={fields.filter((f) => tabOf(f) === "advanced")} st={st} clearEdits={clearEdits}
                   extras={{ "pages-and-ocr": <EngineRows s={s} /> }} />
        )}

        {/* The list a focus run asks about. Whether runs are restricted to it is the route's own
            option (`llm_focus_only`, on How filings are read); the banner reads the SAVED switch,
            from the same settings snapshot, so the two never disagree. */}
        {tab === "focus" && (
          <FocusLineItemsCard canEdit={canEdit} focusOnly={Boolean(s.extraction?.llm_focus_only)}
                              route={routeOf(routes, values)?.id}
                              onRoute={() => goTo("route")}
                              template={params.get("template") ?? undefined}
                              onTemplate={(key) => setParams((p) => { p.set("template", key); return p; },
                                                             { replace: true })} />
        )}

        {tab === "fx" && <FxRatesCard canEdit={canEdit} />}

        {tab === "general" && (
          <SectionCard title={t("st.features")}>
            <ToggleRow
              on={s.features.ui_localization}
              onToggle={() => patch.mutate({ ui_localization: !s.features.ui_localization })}
              canEdit={canEdit} saving={patch.isPending}
              title={t("st.localizeUI")} help={t("st.localizeUIHelp")}
            />
            <ToggleRow
              on={s.features.review_required}
              onToggle={() => patch.mutate({ review_required: !s.features.review_required })}
              canEdit={canEdit} saving={patch.isPending}
              title={t("st.reviewRequired")} help={t("st.reviewRequiredHelp")}
            />
            <ToggleRow
              on={s.features.seed_demo}
              onToggle={() => patch.mutate({ seed_demo: !s.features.seed_demo })}
              canEdit={canEdit} saving={patch.isPending}
              title={t("st.seedDemo")} help={t("st.seedDemoHelp")} testId="seed-demo"
            />
            {!canEdit && <div style={{ fontSize: 11, color: color.muted, marginTop: 4 }}>{t("st.adminOnly")}</div>}
            <div style={{ marginTop: 12 }}>
              <Row label={t("st.supportedLangs")} value={s.features.supported_locales.join(" · ")} />
              <Row label={t("st.defaultOutput")} value={s.features.default_output_locale} />
            </div>
          </SectionCard>
        )}

        {tab === "system" && (
          <SectionCard title={t("st.access")} note={t("st.readOnly")}>
            <Row label={t("st.roleHeader")} value={s.auth.allow_role_header ? t("st.on") : t("st.off")} />
            <Row label={t("st.demoMode")} value={s.auth.demo_mode ? t("st.on") : t("st.off")} />
            <Row label={t("st.sessionTtl")} value={s.auth.session_ttl_minutes} />
          </SectionCard>
        )}

        {tab === "other" && (
          <KnobTab fields={fields.filter((f) => tabOf(f) === "other")} st={st} clearEdits={clearEdits} />
        )}
      </div>
    </div>
  );
}
