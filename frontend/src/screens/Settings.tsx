/** Settings (admin), in tabs: General (interface, sign-off, sample project), AI model (what the
 * model is asked and how requests are put together), Focus line items (the focus-only switch and
 * its list), Reading documents (OCR, the document engine, the whole-PDF route), Mapping & checks
 * (the rules' thresholds), FX rates, and System (read-only access settings).
 *
 * The extraction knobs are laid out from the backend's own descriptors: each names its tab,
 * section, one-line summary, and the switch it is read under — an option only one route reads is
 * hidden while that route is off. The LLM itself is NOT here: it is defined only in
 * backend/config.toml [llm].
 *
 * Edits are SAVED: the backend persists them and re-applies them at startup, so a change holds
 * across a restart. Each tab saves, and restores the shipped defaults of, its own knobs. */
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Card, ScreenHeader, Toggle } from "../components/ui";
import { useT } from "../i18n";
import { ApiError } from "../lib/api";
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
  AppSettings, ExtractionField, FocusLineItem, FxRate, FxRateInput,
} from "../types";

/** Read-only key/value row. */
function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 16, padding: "7px 0", borderBottom: `1px solid ${color.hairline}` }}>
      <span style={{ fontSize: 12, color: color.muted }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 600, color: color.ink, fontFamily: font.mono, textAlign: "right" }}>{value}</span>
    </div>
  );
}

function SectionCard({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) {
  return (
    <Card style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
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
      onClick={() => canEdit && !saving && onToggle()}
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
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 11, color: color.muted }}>{label}</span>
      {children}
    </label>
  );
}

type Value = number | boolean | string;

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

/** One knob's control: a switch, a list of choices, or a number. */
function KnobControl({ f, value, onChange }: {
  f: ExtractionField; value: Value | undefined; onChange: (v: Value) => void;
}) {
  const t = useT();
  if (f.kind === "bool") {
    return (
      // Toggle is presentational; the surrounding control carries the interaction.
      <button data-testid={`ex-${f.key}`} onClick={() => onChange(!value)}
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
    return (
      <select data-testid={`ex-${f.key}`} value={String(value)}
              onChange={(e) => onChange(e.target.value)} style={inputStyle}>
        {f.choices.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    );
  }
  return (
    <input data-testid={`ex-${f.key}`} type="number" value={String(value ?? "")}
           step={f.step ?? 0.01} min={f.min ?? undefined} max={f.max ?? undefined}
           onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
           style={inputStyle} />
  );
}

/** One knob: its name, its one-line summary, the full explanation one click away, and its
 *  control — or, for a reader without the right to change it, its value. */
function KnobRow({ f, value, changed, hidden, canEdit, onChange }: {
  f: ExtractionField; value: Value | undefined; changed: string | null; hidden: number;
  canEdit: boolean; onChange: (v: Value) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div data-testid={`knob-${f.key}`}
         style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 170px", gap: 14,
                  alignItems: "start", padding: "11px 0", borderBottom: `1px solid ${color.hairline}` }}>
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
            <button data-testid={`knob-more-${f.key}`} onClick={() => setOpen((o) => !o)}
                    style={{ background: "none", border: "none", padding: 0, marginInlineStart: 6,
                             fontSize: 11, fontWeight: 600, color: color.indigo, cursor: "pointer" }}>
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
      {canEdit ? (
        <KnobControl f={f} value={value} onChange={onChange} />
      ) : (
        <span style={{ fontSize: 12, fontWeight: 600, fontFamily: font.mono, color: color.ink,
                       textAlign: "right" }}>{String(value)}</span>
      )}
    </div>
  );
}

/** The extraction knobs of one tab, under their section headings, with the tab's own Save and
 *  Restore defaults. Edits are held by the screen (`edits`), so moving to another tab and back
 *  keeps them; Save sends this tab's and Restore defaults puts back this tab's — never another
 *  tab's, which an admin cannot see from here. Every control is rendered from the backend's own
 *  descriptors, so a knob added there shows up with no change to this screen, and the bounds
 *  checked here are the API's. */
function KnobTab({ s, fields, all, canEdit, edits, setEdit, clearEdits }: {
  s: AppSettings; fields: ExtractionField[]; all: ExtractionField[]; canEdit: boolean;
  edits: Record<string, Value>; setEdit: (k: string, v: Value) => void;
  clearEdits: (keys: string[]) => void;
}) {
  const t = useT();
  const patch = usePatchSettings();
  const values = s.extraction ?? {};
  const defaults = s.extraction_defaults ?? {};
  const current = (k: string): Value | undefined => (k in edits ? edits[k] : values[k]);
  const byKey = new Map(all.map((f) => [f.key, f]));
  const keys = fields.map((f) => f.key);
  const changedKeys = keys.filter((k) => k in edits && String(edits[k]) !== String(values[k]));
  const invalid = fields.map((f) => boundError(f, current(f.key))).find(Boolean) ?? null;
  const shown = fields.filter((f) => isShown(f, byKey, current));
  const hiddenUnder = (key: string) =>
    fields.filter((f) => f.shown_when?.key === key && !isShown(f, byKey, current)).length;
  const sections: [string, ExtractionField[]][] = [];
  for (const f of shown) {
    const last = sections[sections.length - 1];
    if (last && last[0] === f.section) last[1].push(f);
    else sections.push([f.section, [f]]);
  }
  if (fields.length === 0) return null;

  return (
    <Card style={{ marginBottom: 14 }}>
      {sections.map(([title, list], i) => (
        <div key={title || i} style={{ marginTop: i ? 18 : 0 }}>
          {title && (
            <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, color: color.muted,
                          textTransform: "uppercase", paddingBottom: 2 }}>{title}</div>
          )}
          {list.map((f) => (
            <KnobRow key={f.key} f={f} value={current(f.key)} canEdit={canEdit}
                     changed={f.key in defaults && String(values[f.key]) !== String(defaults[f.key])
                       ? String(defaults[f.key]) : null}
                     hidden={hiddenUnder(f.key)}
                     onChange={(v) => setEdit(f.key, v)} />
          ))}
        </div>
      ))}

      {canEdit ? (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                      marginTop: 14, gap: 12, flexWrap: "wrap" }}>
          <span data-testid="ex-message"
                style={{ fontSize: 11, color: invalid || patch.isError ? color.redFg : color.muted,
                         flex: "1 1 220px" }}>
            {invalid
              ?? (patch.isError
                    ? `${(patch.error as { detail?: string } | null)?.detail ?? t("st.saveFailed")}`
                    : t("st.nextRun"))}
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {patch.isSuccess && !patch.isPending && changedKeys.length === 0 && (
              <span style={{ fontSize: 11, color: color.greenFg, fontWeight: 600 }}>✓ {t("st.saved")}</span>
            )}
            <button data-testid="ex-reset" disabled={patch.isPending}
                    onClick={() => {
                      clearEdits(keys);
                      patch.mutate({ extraction: Object.fromEntries(
                        keys.filter((k) => k in defaults).map((k) => [k, defaults[k]])) });
                    }}
                    style={secondaryButton(patch.isPending)}>
              {t("st.restoreDefaults")}
            </button>
            <button data-testid="ex-save" disabled={patch.isPending || !!invalid || changedKeys.length === 0}
                    onClick={() => patch.mutate(
                      { extraction: Object.fromEntries(changedKeys.map((k) => [k, edits[k]])) },
                      { onSuccess: () => clearEdits(changedKeys) })}
                    style={primaryButton(patch.isPending || !!invalid || changedKeys.length === 0)}>
              {patch.isPending ? t("st.saving") : t("st.save")}
            </button>
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 11, color: color.muted, marginTop: 10 }}>{t("st.adminOnly")}</div>
      )}
    </Card>
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
        <div style={{ fontSize: 11.5, color: color.muted }}>{t("st.saving")}</div>
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
          <div style={{ display: "grid", gridTemplateColumns: "90px 90px 1fr 150px 1fr", gap: 10 }}>
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

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12 }}>
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

const pill = (bg: string, fg: string): React.CSSProperties => ({
  fontSize: 9.5, fontWeight: 600, padding: "1px 7px", borderRadius: radius.pill,
  background: bg, color: fg, whiteSpace: "nowrap", fontFamily: font.sans,
});

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
 *  templates that list it. Kept on one tab with the switch that decides whether a run is restricted
 *  to its list at all (`llm_focus_only`, above it). Search the template's set, add a line — a line
 *  computed from its parts goes in with the parts its cascade reads, since it is never asked itself
 *  — or a prefix ending in *. Non-admins see the lists read-only. */
function FocusLineItemsCard({ canEdit, focusOnly, template, onTemplate }: {
  canEdit: boolean; focusOnly: boolean; template?: string; onTemplate: (key: string) => void;
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
      </div>

      {canEdit && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{t("st.focus.add")}</div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,2fr) minmax(0,1fr)", gap: 10 }}>
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
                    marginBottom: 6 }}>
        <span data-testid="focus-count" style={{ fontSize: 11, color: color.muted }}>
          {t("st.focus.count").replace("{n}", String(items.length))}
        </span>
        {items.length > 8 && (
          <input value={filter} placeholder={t("st.focus.filter")} spellCheck={false}
                 onChange={(e) => setFilter(e.target.value)}
                 style={{ ...inputStyle, width: 220, fontFamily: font.sans }} />
        )}
      </div>

      {isPending ? (
        <div style={{ fontSize: 11.5, color: color.muted }}>{t("st.saving")}</div>
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
const TABS = ["general", "model", "focus", "reading", "mapping", "fx", "system"] as const;
type TabId = (typeof TABS)[number] | "other";

export default function SettingsScreen() {
  const t = useT();
  const { data, isPending } = useSettings();
  const patch = usePatchSettings();
  const canEdit = useCan("config:settings");
  const [params, setParams] = useSearchParams();
  // Edits not yet saved, by knob. Held here rather than in a tab, so moving between tabs keeps
  // them; each tab saves its own.
  const [edits, setEdits] = useState<Record<string, Value>>({});

  if (isPending || !data) {
    return <div style={{ padding: 60, textAlign: "center", color: color.muted }}>Loading…</div>;
  }
  const s: AppSettings = data;
  const fields = s.extraction_fields ?? [];
  const known = new Set<string>(TABS);
  const tabOf = (f: ExtractionField): TabId => (known.has(f.tab) ? (f.tab as TabId) : "other");
  const tabs: TabId[] = fields.some((f) => tabOf(f) === "other") ? [...TABS, "other"] : [...TABS];
  const asked = params.get("tab") as TabId | null;
  const tab: TabId = asked && tabs.includes(asked) ? asked : "general";
  const values = s.extraction ?? {};
  const unsaved = (id: TabId) => fields.some(
    (f) => tabOf(f) === id && f.key in edits && String(edits[f.key]) !== String(values[f.key]));
  const knobs = (id: TabId) => (
    <KnobTab s={s} fields={fields.filter((f) => tabOf(f) === id)} all={fields} canEdit={canEdit}
             edits={edits}
             setEdit={(k, v) => setEdits((e) => ({ ...e, [k]: v }))}
             clearEdits={(keys) => setEdits((e) => {
               const next = { ...e };
               for (const k of keys) delete next[k];
               return next;
             })} />
  );

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "26px 30px 60px" }}>
      <ScreenHeader title={t("st.title")} subtitle={t("st.subhead")} />

      <div role="tablist" style={{ display: "flex", gap: 2, flexWrap: "wrap", marginBottom: 6,
                                   borderBottom: `1px solid ${color.cardBorder}` }}>
        {tabs.map((id) => {
          const on = id === tab;
          return (
            <button key={id} role="tab" aria-selected={on} data-testid={`st-tab-${id}`}
                    onClick={() => setParams((p) => { p.set("tab", id); return p; }, { replace: true })}
                    style={{ fontSize: 12.5, fontWeight: 600, padding: "8px 12px 9px",
                             background: "none", border: "none", cursor: "pointer",
                             color: on ? color.indigo : color.sec2, marginBottom: -1,
                             borderBottom: `2px solid ${on ? color.indigo : "transparent"}` }}>
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
      <div style={{ fontSize: 11.5, color: color.muted, margin: "8px 0 14px", lineHeight: 1.5 }}>
        {t(`st.tabIntro.${tab}`)}
      </div>

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

      {/* No model-connection card: the LLM is defined only in backend/config.toml [llm]. */}
      {tab === "model" && knobs("model")}

      {/* The focus-only switch and the list it restricts runs to, together. The list's banner reads
          the SAVED switch, from the same settings snapshot, so the two never disagree. */}
      {tab === "focus" && (
        <>
          {knobs("focus")}
          <FocusLineItemsCard canEdit={canEdit} focusOnly={Boolean(s.extraction?.llm_focus_only)}
                              template={params.get("template") ?? undefined}
                              onTemplate={(key) => setParams((p) => { p.set("template", key); return p; },
                                                             { replace: true })} />
        </>
      )}

      {tab === "reading" && (
        <>
          {knobs("reading")}
          <SectionCard title={t("st.engines")} note={t("st.readOnly")}>
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
          </SectionCard>
        </>
      )}

      {tab === "mapping" && knobs("mapping")}

      {tab === "fx" && <FxRatesCard canEdit={canEdit} />}

      {tab === "system" && (
        <SectionCard title={t("st.access")} note={t("st.readOnly")}>
          <Row label={t("st.roleHeader")} value={s.auth.allow_role_header ? t("st.on") : t("st.off")} />
          <Row label={t("st.demoMode")} value={s.auth.demo_mode ? t("st.on") : t("st.off")} />
          <Row label={t("st.sessionTtl")} value={s.auth.session_ttl_minutes} />
        </SectionCard>
      )}

      {tab === "other" && knobs("other")}
    </div>
  );
}
