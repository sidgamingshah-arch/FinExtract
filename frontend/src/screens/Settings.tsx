/** Settings (admin) — surfaces backend configuration on the frontend and lets an admin
 * edit the runtime-mutable pieces: interface localization, the reviewer sign-off step,
 * the FX rate master
 * used for presentation currency conversion, and the extraction thresholds. The LLM is NOT
 * here: it is defined only in backend/config.toml [llm]. Everything else is config.toml / env
 * driven and shown read-only.
 *
 * Edits are SAVED: the backend persists them and re-applies them at startup, so a change holds
 * across a restart. "Restore defaults" puts back what config.toml shipped. */
import { useEffect, useState } from "react";

import { Card, ScreenHeader, Toggle } from "../components/ui";
import { useT } from "../i18n";
import { ApiError } from "../lib/api";
import { useCan } from "../lib/rbac";
import {
  useDeleteFxRate,
  useFxRates,
  usePatchSettings,
  useSettings,
  useUpdateFxRate,
  useUpsertFxRate,
} from "../lib/queries";
import { color, font, radius } from "../theme";
import type { AppSettings, ExtractionField, FxRate, FxRateInput } from "../types";

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

/** Turn a snake_case setting key into something readable, for a backend that reports the
 *  values but not the descriptors. */
function humanise(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Controls inferred from the VALUES alone.
 *
 * The backend normally describes each knob (label, bounds, step, explanation) and this screen
 * renders from that. An older backend returns the values without the descriptors — and rendering
 * from an empty descriptor list produced a card with a Save button and no fields in it, which
 * looks broken and says nothing about why. Inferring the control from each value's own type
 * keeps the screen usable against any backend; only the bounds are unknown, and the API refuses
 * an out-of-range value anyway. */
function inferFields(values: Record<string, number | boolean | string>): ExtractionField[] {
  return Object.entries(values).map(([key, v]) => ({
    key,
    kind: typeof v === "boolean" ? "bool" : typeof v === "number" ? "number" : "choice",
    label: humanise(key),
    help: "",
    min: null,
    max: null,
    step: typeof v === "number" && Number.isInteger(v) ? 1 : 0.01,
    // A string value with no declared options can only be offered as its current value; the
    // real choice list lives on the backend.
    choices: typeof v === "string" ? [v] : [],
  }));
}

/** The mapping / reconciliation thresholds, tunable by an admin.
 *
 * Every control is rendered from the backend's own field descriptors — label, bounds, step and
 * an explanation of what the knob does. Nothing about mapping is hardcoded here, so a knob added
 * on the backend shows up with no change to this screen, and the bounds the UI enforces are by
 * construction the bounds the API enforces. */
function ExtractionConfigCard({ s, canEdit }: { s: AppSettings; canEdit: boolean }) {
  const t = useT();
  const patch = usePatchSettings();
  const described = s.extraction_fields ?? [];
  const values = s.extraction ?? {};
  const inferred = described.length === 0 && Object.keys(values).length > 0;
  const fields = inferred ? inferFields(values) : described;
  const [form, setForm] = useState<Record<string, number | boolean | string>>(values);
  const [dirty, setDirty] = useState(false);

  // Re-sync when the server's view changes (a save, or a reset) — but never clobber an edit in
  // progress, which would silently discard what the admin is typing.
  useEffect(() => {
    if (!dirty) setForm(values);
  }, [values, dirty]);

  const set = (k: string, v: number | boolean | string) => {
    setDirty(true);
    setForm((f) => ({ ...f, [k]: v }));
  };

  /** The first bound a typed value breaks, checked against the backend's own descriptor so the
   *  Save button never sends something the API will refuse. */
  const localError = (): string | null => {
    for (const f of fields) {
      if (f.kind !== "number") continue;
      const v = Number(form[f.key]);
      if (!Number.isFinite(v)) return `${f.label} must be a number`;
      if (f.min !== null && v < f.min) return `${f.label} must be at least ${f.min}`;
      if (f.max !== null && v > f.max) return `${f.label} must be at most ${f.max}`;
    }
    return null;
  };
  const invalid = localError();
  const changed = fields.some((f) => String(form[f.key]) !== String(values[f.key]));
  const defaults = s.extraction_defaults ?? {};
  const movedFromDefault = (k: string) =>
    k in defaults && String(values[k]) !== String(defaults[k]);

  // Nothing to show at all — an older backend that reports no extraction block.
  if (fields.length === 0) {
    return (
      <SectionCard title={t("st.extraction")} note={t("st.readOnly")}>
        <div style={{ fontSize: 11.5, color: color.sec2, lineHeight: 1.55 }}>
          {t("st.extractionUnavailable")}
        </div>
      </SectionCard>
    );
  }

  if (!canEdit) {
    return (
      <SectionCard title={t("st.extraction")} note={t("st.readOnly")}>
        {fields.map((f) => (
          <Row key={f.key} label={f.label} value={String(values[f.key])} />
        ))}
      </SectionCard>
    );
  }

  return (
    <SectionCard title={t("st.extraction")} note={t("st.editable")}>
      <div style={{ fontSize: 11, color: color.sec2, marginBottom: 12, lineHeight: 1.55 }}>
        {t("st.extractionNote")}
        {inferred && (
          <span style={{ display: "block", marginTop: 5, color: color.amberFg, fontWeight: 600 }}>
            {t("st.extractionInferred")}
          </span>
        )}
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        {fields.map((f) => (
          <div key={f.key}
               style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 150px", gap: 12,
                        alignItems: "start", paddingBottom: 10,
                        borderBottom: `1px solid ${color.hairline}` }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: color.ink }}>{f.label}</span>
                {movedFromDefault(f.key) && (
                  <span title={`Default: ${String(defaults[f.key])}`}
                        style={{ fontSize: 9.5, fontWeight: 600, padding: "1px 6px",
                                 borderRadius: radius.pill, background: color.amberBg,
                                 color: color.amberFg }}>
                    {t("st.changed")}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 10.5, color: color.muted, marginTop: 3, lineHeight: 1.5 }}>
                {f.help}
              </div>
            </div>
            {f.kind === "bool" ? (
              // Toggle is presentational; the surrounding control carries the interaction.
              <button
                data-testid={`ex-${f.key}`}
                onClick={() => set(f.key, !form[f.key])}
                style={{ display: "flex", alignItems: "center", gap: 8, background: "none",
                         border: "none", padding: 0, cursor: "pointer", justifySelf: "start" }}
              >
                <Toggle on={Boolean(form[f.key])} />
                <span style={{ fontSize: 11, fontWeight: 600,
                               color: form[f.key] ? color.greenFg : color.muted }}>
                  {form[f.key] ? t("st.on") : t("st.off")}
                </span>
              </button>
            ) : f.kind === "choice" ? (
              <select data-testid={`ex-${f.key}`} value={String(form[f.key])}
                      onChange={(e) => set(f.key, e.target.value)} style={inputStyle}>
                {f.choices.map((c: string) => <option key={c} value={c}>{c}</option>)}
              </select>
            ) : (
              <input
                data-testid={`ex-${f.key}`}
                type="number"
                value={String(form[f.key] ?? "")}
                step={f.step ?? 0.01}
                min={f.min ?? undefined}
                max={f.max ?? undefined}
                onChange={(e) => set(f.key, e.target.value === "" ? "" : Number(e.target.value))}
                style={inputStyle}
              />
            )}
          </div>
        ))}
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                    marginTop: 13, gap: 12 }}>
        <span data-testid="ex-message"
              style={{ fontSize: 11, color: invalid ? color.redFg : color.muted }}>
          {invalid
            ?? (patch.isError
                  ? `${(patch.error as { detail?: string } | null)?.detail ?? t("st.saveFailed")}`
                  : "")}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {patch.isSuccess && !patch.isPending && !changed && (
            <span style={{ fontSize: 11, color: color.greenFg, fontWeight: 600 }}>
              ✓ {t("st.saved")}
            </span>
          )}
          <button
            data-testid="ex-reset"
            onClick={() => { setDirty(false); patch.mutate({ reset_extraction: true }); }}
            disabled={patch.isPending}
            style={{ fontSize: 12, fontWeight: 600, color: color.sec2, background: "#fff",
                     border: `1px solid ${color.controlBorder}`, borderRadius: 8,
                     padding: "8px 14px", cursor: patch.isPending ? "default" : "pointer" }}
          >
            {t("st.restoreDefaults")}
          </button>
          <button
            data-testid="ex-save"
            onClick={() => { setDirty(false); patch.mutate({ extraction: form }); }}
            disabled={patch.isPending || !!invalid || !changed}
            style={{
              fontSize: 12, fontWeight: 600, color: "#fff",
              background: patch.isPending || invalid || !changed ? color.faint : color.indigo,
              border: "none", borderRadius: 8, padding: "8px 16px",
              cursor: patch.isPending || invalid || !changed ? "default" : "pointer",
            }}
          >
            {patch.isPending ? t("st.saving") : t("st.save")}
          </button>
        </div>
      </div>
    </SectionCard>
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
    <SectionCard title={t("st.fx")} note={canEdit ? t("st.editable") : t("st.readOnly")}>
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

export default function SettingsScreen() {
  const t = useT();
  const { data, isPending } = useSettings();
  const patch = usePatchSettings();
  const canEdit = useCan("config:settings");

  if (isPending || !data) {
    return <div style={{ padding: 60, textAlign: "center", color: color.muted }}>Loading…</div>;
  }
  const s: AppSettings = data;
  const readOnlyNote = t("st.readOnly");

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "26px 30px 60px" }}>
      <ScreenHeader title={t("st.title")} subtitle={t("st.subhead")} />

      {/* Workflow & interface — the editable feature flags */}
      <SectionCard title={t("st.features")}>
        <ToggleRow
          on={s.features.ui_localization}
          onToggle={() => patch.mutate({ ui_localization: !s.features.ui_localization })}
          canEdit={canEdit}
          saving={patch.isPending}
          title={t("st.localizeUI")}
          help={t("st.localizeUIHelp")}
        />
        <ToggleRow
          on={s.features.review_required}
          onToggle={() => patch.mutate({ review_required: !s.features.review_required })}
          canEdit={canEdit}
          saving={patch.isPending}
          title={t("st.reviewRequired")}
          help={t("st.reviewRequiredHelp")}
        />
        <ToggleRow
          on={s.features.seed_demo}
          onToggle={() => patch.mutate({ seed_demo: !s.features.seed_demo })}
          canEdit={canEdit}
          saving={patch.isPending}
          title={t("st.seedDemo")}
          help={t("st.seedDemoHelp")}
          testId="seed-demo"
        />
        {!canEdit && <div style={{ fontSize: 11, color: color.muted, marginTop: 4 }}>{t("st.adminOnly")}</div>}
        <div style={{ marginTop: 12 }}>
          <Row label={t("st.supportedLangs")} value={s.features.supported_locales.join(" · ")} />
          <Row label={t("st.defaultOutput")} value={s.features.default_output_locale} />
        </div>
      </SectionCard>

      {/* No LLM card: the LLM is defined only in backend/config.toml [llm], never from here. */}
      {/* FX rate master — the rates the Workspace converts presentation currency with */}
      <FxRatesCard canEdit={canEdit} />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {/* OCR */}
        <SectionCard title={t("st.ocr")} note={readOnlyNote}>
          <Row label={t("st.engine")} value={s.ocr.engine} />
          <Row label={t("st.languages")} value={s.ocr.languages.join(" · ")} />
          <Row label={t("st.dpi")} value={s.ocr.dpi} />
        </SectionCard>
      </div>

      {/* Extraction & reconciliation */}
      <ExtractionConfigCard s={s} canEdit={canEdit} />

      {/* Access & session */}
      <SectionCard title={t("st.access")} note={readOnlyNote}>
        <Row label={t("st.roleHeader")} value={s.auth.allow_role_header ? t("st.on") : t("st.off")} />
        <Row label={t("st.demoMode")} value={s.auth.demo_mode ? t("st.on") : t("st.off")} />
        <Row label={t("st.sessionTtl")} value={s.auth.session_ttl_minutes} />
      </SectionCard>
    </div>
  );
}
