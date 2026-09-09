/** Screen 7 — Template & line items, in two pages.
 *
 * Page 1 is the index (see TemplateList.tsx): the templates that exist, one row each, and the
 * authoring desk. Page 2 — everything below — is one template's detail: its structure tree and
 * the inline editor for the LINE ITEM a template line maps to. It is raised OVER the index rather
 * than replacing it, so dismissing it puts the reader back on the list they were reading, filter
 * and scroll intact, and `?template=` keeps a reloaded tab on the version it was open on.
 *
 * There is ONE configuration engine — the line items — so the editor here saves through
 * `/line-items` and publishes a new line-item version. The netting-policy editor and the
 * starter-file download that used to live on this page addressed the ontology and are gone
 * (see the notes at their old sites below); nothing on this screen selects a second engine.
 */
import type { CSSProperties } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { Card } from "../components/ui";
import { TemplateList, sortTemplates } from "./TemplateList";
import { useAppLocale, useUI } from "../store";
import { color, font, radius } from "../theme";
import type { LineItemEdit, Locale, NodeConfig, TemplateRef, ValueScope } from "../types";
import { useTemplateDetail, useTemplates } from "../lib/queries";
import { ApiError, api } from "../lib/api";
import { useCan } from "../lib/rbac";
import { NATIVE_NAME, useT } from "../i18n";

const SIGN_OPTIONS: { key: string; labelKey: string }[] = [
  { key: "as_reported", labelKey: "tp.sign.asReported" },
  { key: "expense_contra", labelKey: "tp.sign.expenseContra" },
  { key: "auto", labelKey: "tp.sign.auto" },
];

function Radio({ on }: { on: boolean }) {
  return (
    <span
      style={{
        width: 15,
        height: 15,
        borderRadius: "50%",
        border: `2px solid ${on ? color.indigo : color.dashed}`,
        background: on ? color.indigo : "#fff",
        flex: "0 0 auto",
      }}
    />
  );
}

function FieldMock({ label, value }: { label: string; value: string }) {
  return (
    <>
      <div style={{ fontSize: 11, color: color.muted, marginBottom: 4 }}>{label}</div>
      <div
        style={{
          border: `1px solid ${color.cardBorder}`,
          borderRadius: radius.controlSm,
          padding: "7px 10px",
          fontSize: 12,
          fontWeight: 500,
        }}
      >
        {value} ▾
      </div>
    </>
  );
}

/** THE NETTING EXPRESSION RENDERER IS GONE with the netting editor and the read-only netting
 *  card below (see the note at the old render site). Netting is part of the line-item set, so it
 *  is published through the one configuration engine rather than shown in a display of its own. */

/** A line item that EXISTS in this template. `confusable_with` must name one: the server 422s on
 *  an unknown key, so it is picked from this list, never typed. */
interface Concept { key: string; label: string }

type Msg = { ok: boolean; text: string };

/** How an editor tells the detail page it is holding an unsaved change, under a name of its own
 *  (`concept`) so several editors can be dirty at once and each can go clean on its own. The
 *  detail needs the answer because the way OUT of the page — "← All templates" — lives in its
 *  header, and used to discard whatever was in an editor without a word. */
type ReportDirty = (source: string, dirty: boolean) => void;

/** The server's own `detail` when it sent one — a rejected key or an invalid regex has to say
 *  WHY, verbatim, or the admin is left guessing at what to change. */
function serverText(err: unknown): string {
  return err instanceof ApiError ? (err.detail ?? err.message) : String(err);
}

const textInput: CSSProperties = {
  width: "100%", boxSizing: "border-box", fontFamily: font.sans, fontSize: 12, color: color.ink,
  background: "#fff", border: `1px solid ${color.controlBorder}`, borderRadius: radius.controlSm,
  padding: "7px 10px", outline: "none",
};
const chipBase: CSSProperties = {
  fontSize: 11.5, fontWeight: 500, padding: "5px 11px", borderRadius: radius.pill,
};
// Chips carry meaning by colour: what belongs here (indigo), what must be kept out (red),
// and patterns that are read literally (mono).
const TONE = {
  indigo: { background: color.indigoTint2, color: color.indigo },
  red: { background: color.redBg, color: color.redFg },
  mono: { background: color.rowAltBg, color: color.ink2, fontFamily: font.mono },
} as const;

function FieldLabel({ label, hint }: { label: string; hint?: string }) {
  return (
    <div style={{ marginBottom: 5 }}>
      <div style={{ fontSize: 11.5, fontWeight: 600, color: color.ink2 }}>{label}</div>
      {hint && (
        <div style={{ fontSize: 10.5, color: color.muted, lineHeight: 1.5, marginTop: 2 }}>{hint}</div>
      )}
    </div>
  );
}

/** Pick a concept that exists. A free-text field would let a typo through, and a rule naming
 *  a line that isn't there never fires — indistinguishable from one that simply didn't apply. */
function KeyPicker({ options, placeholder, onPick }: {
  options: Concept[]; placeholder: string; onPick: (key: string) => void;
}) {
  return (
    <select
      value=""
      onChange={(e) => { if (e.target.value) onPick(e.target.value); }}
      style={{ ...textInput, width: "auto", maxWidth: 270, fontSize: 11.5, padding: "5px 9px",
               borderRadius: radius.pill, border: `1px dashed ${color.dashed}`, cursor: "pointer" }}
    >
      <option value="">{placeholder}</option>
      {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
    </select>
  );
}

/** Chips plus an add affordance — deliberately the same interaction as the alias editor, so
 *  every list on this screen behaves the same. Passing `options` swaps the free-text input for
 *  a picker, which is what the canonical-key lists use. */
function ChipList({
  label, hint, items, editable, tone = "indigo", draft, placeholder, testId, options, labelOf,
  onChange, onDraft, t,
}: {
  label: string; hint?: string; items: string[]; editable: boolean; tone?: keyof typeof TONE;
  draft?: string; placeholder?: string; testId?: string; options?: Concept[];
  labelOf?: (v: string) => string; onChange: (next: string[]) => void;
  onDraft?: (v: string) => void; t: (k: string) => string;
}) {
  function commit(raw: string) {
    const v = raw.trim();
    onDraft?.("");
    if (v && !items.includes(v)) onChange([...items, v]);
  }
  return (
    <div style={{ marginBottom: 13 }} data-testid={testId}>
      <FieldLabel label={label} hint={hint} />
      <div style={{ display: "flex", flexWrap: "wrap", gap: 7, alignItems: "center" }}>
        {items.map((v) => (
          <span key={v} title={labelOf ? v : undefined} style={{ ...chipBase, ...TONE[tone] }}>
            {labelOf ? labelOf(v) : v}
            {editable && (
              <span
                role="button"
                title={t("tp.removeItem")}
                onClick={() => onChange(items.filter((x) => x !== v))}
                style={{ opacity: 0.55, cursor: "pointer", marginInlineStart: 4, fontWeight: 700 }}
              >
                ×
              </span>
            )}
          </span>
        ))}
        {editable && options && (
          <KeyPicker
            options={options.filter((o) => !items.includes(o.key))}
            placeholder={placeholder ?? ""}
            onPick={(k) => onChange([...items, k])}
          />
        )}
        {editable && !options && (
          <input
            value={draft ?? ""}
            placeholder={placeholder}
            title={t("tp.addItemHint")}
            onChange={(e) => onDraft?.(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(draft ?? ""); } }}
            onBlur={() => commit(draft ?? "")}
            style={{ fontSize: 11.5, padding: "5px 11px", borderRadius: radius.pill,
                     border: `1px dashed ${color.dashed}`, color: color.ink, background: "transparent",
                     minWidth: 170, outline: "none",
                     fontFamily: tone === "mono" ? font.mono : font.sans }}
          />
        )}
        {!editable && items.length === 0 && (
          <span style={{ fontSize: 11.5, color: color.muted }}>{t("tp.noneSet")}</span>
        )}
      </div>
    </div>
  );
}

/** The mapping criteria this editor owns. Normalised on load so an absent field and an empty
 *  one compare equal — the dirty check is then a plain signature comparison. */
interface Criteria {
  definition: string;
  value_scope: ValueScope;
  include: string[];
  exclude: string[];
  confusable_with: string[];
  keyword_hints: string[];
  regex_hints: string[];
  exclude_hints: string[];
}
type CriteriaList = Exclude<keyof Criteria, "definition" | "value_scope">;

const EMPTY_DRAFTS: Record<CriteriaList, string> = {
  include: "", exclude: "", confusable_with: "", keyword_hints: "", regex_hints: "",
  exclude_hints: "",
};

function criteriaOf(cfg: NodeConfig): Criteria {
  return {
    definition: cfg.definition ?? "",
    value_scope: cfg.value_scope ?? "exclusive_leaf",
    include: cfg.include ?? [],
    exclude: cfg.exclude ?? [],
    confusable_with: cfg.confusable_with ?? [],
    keyword_hints: cfg.keyword_hints ?? [],
    regex_hints: cfg.regex_hints ?? [],
    exclude_hints: cfg.exclude_hints ?? [],
  };
}
const criteriaSig = (c: Criteria) => JSON.stringify(c);

/** What a save would persist: the committed chips plus whatever is still sitting in a list's
 *  input. Folded in here (rather than trusting blur to fire before the button's click) so
 *  typing a criterion and clicking Save directly can never drop it. `confusable_with` has no
 *  text input — it is picked — and simply never carries a draft. */
function withDrafts(c: Criteria, drafts: Record<CriteriaList, string>): Criteria {
  const out: Criteria = { ...c };
  (Object.keys(drafts) as CriteriaList[]).forEach((f) => {
    const v = drafts[f].trim();
    if (v && !out[f].includes(v)) out[f] = [...out[f], v];
  });
  return out;
}

// The four value scopes the backend accepts, each with an explanation — an analyst has no
// reason to know what "exclusive_residual" means, and picking the wrong one silently changes
// whether the figure is taken as printed, netted out of its parent, or computed.
const VALUE_SCOPES: { key: ValueScope; labelKey: string; hintKey: string }[] = [
  { key: "exclusive_leaf", labelKey: "tp.scope.leaf", hintKey: "tp.scope.leafHint" },
  { key: "exclusive_child", labelKey: "tp.scope.child", hintKey: "tp.scope.childHint" },
  { key: "exclusive_residual", labelKey: "tp.scope.residual", hintKey: "tp.scope.residualHint" },
  { key: "not_applicable", labelKey: "tp.scope.na", hintKey: "tp.scope.naHint" },
];

/** The criteria the mapper actually reasons over — grouped (meaning first, lexical hints
 *  second) so the editor reads as sections instead of a wall of inputs. Values are lifted:
 *  saving is the surrounding node editor's single Save, so one bar covers every change. */
function CriteriaEditor({
  criteria, drafts, concepts, canonicalKey, editable, onChange, onDraft, t,
}: {
  criteria: Criteria; drafts: Record<CriteriaList, string>; concepts: Concept[];
  canonicalKey: string | undefined; editable: boolean;
  onChange: (patch: Partial<Criteria>) => void;
  onDraft: (field: CriteriaList, v: string) => void; t: (k: string) => string;
}) {
  // A line item is never confusable with itself, and only keys this line-item set declares are
  // legal.
  const others = concepts.filter((c) => c.key !== canonicalKey);
  const labelOf = (k: string) => concepts.find((c) => c.key === k)?.label ?? k;
  const list = (field: CriteriaList, labelKey: string, hintKey: string, phKey: string,
                tone: keyof typeof TONE) => (
    <ChipList
      label={t(labelKey)} hint={t(hintKey)} placeholder={t(phKey)} tone={tone}
      items={criteria[field]} draft={drafts[field]} editable={editable}
      testId={`criteria-${field}`}
      onChange={(next) => onChange({ [field]: next } as Partial<Criteria>)}
      onDraft={(v) => onDraft(field, v)} t={t}
    />
  );

  return (
    <Card style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{t("tp.criteria")}</div>
      <p style={{ margin: "0 0 14px", fontSize: 11.5, color: color.sec2, lineHeight: 1.55 }}>
        {t("tp.criteriaHint")}
      </p>

      <FieldLabel label={t("tp.definition")} hint={t("tp.definitionHint")} />
      {editable ? (
        <textarea
          data-testid="criteria-definition"
          value={criteria.definition}
          rows={3}
          placeholder={t("tp.definitionPh")}
          onChange={(e) => onChange({ definition: e.target.value })}
          style={{ ...textInput, resize: "vertical", lineHeight: 1.55, marginBottom: 14 }}
        />
      ) : (
        <p style={{ margin: "0 0 14px", fontSize: 12, lineHeight: 1.55,
                    color: criteria.definition ? color.sec : color.muted }}>
          {criteria.definition || t("tp.noneSet")}
        </p>
      )}

      <FieldLabel label={t("tp.valueScope")} hint={t("tp.valueScopeHint")} />
      {editable ? (
        <select
          data-testid="criteria-scope"
          value={criteria.value_scope}
          onChange={(e) => onChange({ value_scope: e.target.value as ValueScope })}
          style={{ ...textInput, cursor: "pointer" }}
        >
          {VALUE_SCOPES.map((s) => <option key={s.key} value={s.key}>{t(s.labelKey)}</option>)}
        </select>
      ) : (
        <div style={{ fontSize: 12, fontWeight: 500 }}>
          {t((VALUE_SCOPES.find((s) => s.key === criteria.value_scope) ?? VALUE_SCOPES[0]).labelKey)}
        </div>
      )}
      {/* Every option explained, not just the chosen one — the choice is only meaningful
          against the alternatives. */}
      <div style={{ margin: "8px 0 16px", display: "flex", flexDirection: "column", gap: 4 }}>
        {VALUE_SCOPES.map((s) => {
          const on = s.key === criteria.value_scope;
          return (
            <div key={s.key} style={{ fontSize: 10.5, lineHeight: 1.5,
                                      color: on ? color.sec : color.muted }}>
              <b style={{ color: on ? color.indigo : color.sec2 }}>{t(s.labelKey)}</b>
              {" — "}{t(s.hintKey)}
            </div>
          );
        })}
      </div>

      {list("include", "tp.include", "tp.includeHint", "tp.includePh", "indigo")}
      {list("exclude", "tp.exclude", "tp.excludeHint", "tp.excludePh", "red")}
      <ChipList
        label={t("tp.confusable")} hint={t("tp.confusableHint")} placeholder={t("tp.confusablePick")}
        items={criteria.confusable_with} editable={editable} options={others} labelOf={labelOf}
        testId="criteria-confusable_with"
        onChange={(next) => onChange({ confusable_with: next })} t={t}
      />

      <div style={{ borderTop: `1px solid ${color.hairline3}`, marginTop: 3, paddingTop: 13,
                    marginBottom: 12, fontSize: 11.5, fontWeight: 600, color: color.sec }}>
        {t("tp.lexicalGroup")}
      </div>
      {list("keyword_hints", "tp.keywordHints", "tp.keywordHintsHint", "tp.keywordHintsPh", "indigo")}
      {list("regex_hints", "tp.regexHints", "tp.regexHintsHint", "tp.regexHintsPh", "mono")}
      {list("exclude_hints", "tp.excludeHints", "tp.excludeHintsHint", "tp.excludeHintsPh", "red")}
    </Card>
  );
}

/** THE NETTING-POLICY EDITOR IS GONE (`NettingRuleRow` + `NettingRules`, and the
 *  `PATCH /ontologies/{id}/netting-rules` call behind them).
 *
 *  It let an admin publish a target line net of its components, keyed by ontology concept and
 *  saved as a new ontology VERSION. There is one configuration engine now — the line items — and
 *  netting belongs to the line-item set, so it is published with the rest of that set rather than
 *  through a second, ontology-shaped door. The shipped configuration declares ZERO netting rules,
 *  so the editor governed nothing: porting its PATCH would have been work on a no-op. Do not
 *  reinstate it here; add netting to the line-item set and edit it through `/line-items`. */

/** Editable rules for the selected LINE ITEM: the aliases the extractor matches on, the sign
 *  convention, and the criteria the mapper reasons over (definition / include / exclude /
 *  confusable-with / scope / lexical hints). Saving publishes a NEW line-item version
 *  server-side (history preserved), then re-reads the screen so what you see is the stored
 *  result, not local optimism. */
function NodeRules({ cfg, canonicalKey, lineItemVersionId, concepts, locale, canEdit, onDirty, t }: {
  cfg: NodeConfig; canonicalKey: string | undefined; lineItemVersionId: string | undefined;
  concepts: Concept[]; locale: Locale; canEdit: boolean; onDirty: ReportDirty;
  t: (k: string) => string;
}) {
  const qc = useQueryClient();
  // Edit the RAW per-locale list (falls back to the merged set when the backend predates it).
  const stored = cfg.aliases_locale ?? cfg.aliases;
  const storedCriteria = criteriaOf(cfg);
  const [aliases, setAliases] = useState<string[]>(stored);
  const [sign, setSign] = useState<string>(cfg.sign);
  const [criteria, setCriteria] = useState<Criteria>(storedCriteria);
  const [draft, setDraft] = useState("");
  const [drafts, setDrafts] = useState<Record<CriteriaList, string>>(EMPTY_DRAFTS);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);

  // Switching concept (or language) starts a fresh edit: drop the pending drafts AND the last
  // save message, which belonged to the previous concept.
  const conceptKey = `${canonicalKey}|${locale}`;
  const seenConcept = useRef(conceptKey);
  useEffect(() => {
    if (seenConcept.current !== conceptKey) {
      seenConcept.current = conceptKey;
      setAliases(stored);
      setSign(cfg.sign);
      setCriteria(storedCriteria);
      setDraft("");
      setDrafts(EMPTY_DRAFTS);
      setMsg(null);
    }
  }, [conceptKey, stored, cfg.sign, storedCriteria]);

  // Adopt server truth when the stored rules change under us: our own save's refetch, or
  // another admin's edit. Deliberately does NOT clear `msg` -- the save confirmation has to
  // survive the very refetch that saving triggered.
  const storedKey = `${stored.join(" ")}|${cfg.sign}|${criteriaSig(storedCriteria)}`;
  const seenStored = useRef(storedKey);
  useEffect(() => {
    if (seenStored.current !== storedKey) {
      seenStored.current = storedKey;
      setAliases(stored);
      setSign(cfg.sign);
      setCriteria(storedCriteria);
    }
  }, [storedKey, stored, cfg.sign, storedCriteria]);

  // A template line the configuration in force does not declare has no item to PATCH: the save
  // answers 404 "not in this line-item set", and the confusable-with picker 422s on the key. So the
  // whole editor is read-only for it rather than a Save that is always refused — the detail's header
  // says why. `mapped === false` is the server SAYING it is unmapped; a payload that omits the field
  // predates it and described nodes that were mapped, so absent stays editable.
  const editable = canEdit && !!lineItemVersionId && !!canonicalKey && cfg.mapped !== false;
  // What a save would persist: the committed chips plus any alias still sitting in the input.
  // Folding the draft in here (rather than relying on the input's blur firing before the
  // button's click) means typing an alias and clicking Save directly can never drop it.
  const pending = draft.trim();
  const effective = pending && !aliases.includes(pending) ? [...aliases, pending] : aliases;
  const effCriteria = withDrafts(criteria, drafts);
  const dirty = effective.join(" ") !== stored.join(" ") || sign !== cfg.sign
    || criteriaSig(effCriteria) !== criteriaSig(storedCriteria);
  // The same answer the "Unsaved changes" bar below is drawn from, handed to the page: leaving
  // the detail has to ask before throwing this away.
  useEffect(() => {
    onDirty("concept", dirty);
    return () => onDirty("concept", false);
  }, [dirty, onDirty]);
  // The merged display set minus what we edit here = aliases inherited from the fallback
  // locale. Shown read-only so it's clear why the extractor also matches them.
  const inherited = cfg.aliases.filter((a) => !stored.includes(a));

  function addDraft() {
    const v = draft.trim();
    if (!v) return;
    if (!aliases.includes(v)) setAliases([...aliases, v]);
    setDraft("");
  }

  function discard() {
    setAliases(stored);
    setSign(cfg.sign);
    setCriteria(storedCriteria);
    setDraft("");
    setDrafts(EMPTY_DRAFTS);
    setMsg(null);
  }

  async function save() {
    if (!editable || !dirty) return;
    setBusy(true);
    setMsg(null);
    try {
      // One PATCH for the whole line item: aliases, sign and criteria are validated together,
      // so a bad regex or an unknown key cannot leave half the edit published.
      //
      // THREE OF THE KEYS THIS BODY SENT WERE THE ONTOLOGY-ERA SPELLING, and the save could not
      // have worked. `ItemEdit` (backend `routes/line_items.py`) names the item `key`, not
      // `canonical_key`, and the criteria `include_criteria` / `exclude_criteria`, not `include` /
      // `exclude`. `key` is REQUIRED there, so every save was a 422 on a field the screen never
      // sent; and pydantic IGNORES keys it does not declare, so even past that refusal the two
      // criteria lists would have been dropped in silence — a save reporting "Saved as v3" having
      // stored nothing of what was typed into the criteria editor. That silent no-op is the defect,
      // not the 422.
      //
      // Typed on `LineItemEdit` (the shape the endpoint actually accepts) rather than assembled as
      // a bare literal, so the next rename on the wire is a compile error here instead of another
      // save that succeeds and stores nothing.
      const edit: LineItemEdit = {
        key: canonicalKey!, locale, aliases: effective,
        // `sign_convention` STAYS, and is not `sign_expectation`. This screen's `SIGN_OPTIONS`
        // are the legacy 3-token sign-RULE vocabulary (`as_reported` / `expense_contra` /
        // `auto`), which the backend maps into `sign_rule.convention` — how a value is
        // NORMALISED. `sign_expectation` is a different question (the sign a figure is expected
        // to carry, which review validation reads), so sending this control's value under that
        // name would write an answer to a question nobody was asked.
        sign_convention: sign,
        definition: effCriteria.definition, value_scope: effCriteria.value_scope,
        include_criteria: effCriteria.include, exclude_criteria: effCriteria.exclude,
        confusable_with: effCriteria.confusable_with, keyword_hints: effCriteria.keyword_hints,
        regex_hints: effCriteria.regex_hints, exclude_hints: effCriteria.exclude_hints,
      };
      // `api.editLineItem` is still DECLARED on the legacy `MappingEdit`, whose required
      // `canonical_key` describes a body the endpoint refuses; api.ts is not part of this change.
      // Asserted through the function's own parameter type rather than by naming `MappingEdit`, so
      // this line follows api.ts when that signature is retyped on `LineItemEdit` and there is no
      // reference here to an interface that is due to be deleted.
      const res = await api.editLineItem(
        lineItemVersionId!, edit as unknown as Parameters<typeof api.editLineItem>[1]);
      setAliases(effective);
      setCriteria(effCriteria);
      setDraft("");
      setDrafts(EMPTY_DRAFTS);
      setMsg({ ok: true, text: t("tp.saved").replace("{v}", String(res.version)) });
      // Re-read the detail (and the line-item version list) so the screen shows the stored version.
      await qc.invalidateQueries({ queryKey: ["template-detail"] });
      qc.invalidateQueries({ queryKey: ["line-item-versions"] });
    } catch (err) {
      setMsg({ ok: false, text: `${t("tp.saveErr")} ${serverText(err)}`.slice(0, 300) });
    } finally {
      setBusy(false);
    }
  }

  const chip: CSSProperties = { ...chipBase, ...TONE.indigo };

  return (
    <>
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between",
                      gap: 10, marginBottom: 11 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{t("tp.aliases")}</span>
          {editable && (
            <span style={{ fontSize: 10.5, color: color.muted }}>
              {t("tp.editingLocale")} {NATIVE_NAME[locale]}
            </span>
          )}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7, alignItems: "center" }}>
          {aliases.map((a) => (
            <span key={a} style={chip}>
              {a}
              {editable && (
                <span
                  role="button"
                  title={t("tp.revert")}
                  onClick={() => setAliases(aliases.filter((x) => x !== a))}
                  style={{ opacity: 0.55, cursor: "pointer", marginInlineStart: 4, fontWeight: 700 }}
                >
                  ×
                </span>
              )}
            </span>
          ))}
          {editable && (
            <input
              value={draft}
              placeholder={t("tp.newAlias")}
              title={t("tp.addAliasHint")}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); addDraft(); }
              }}
              onBlur={addDraft}
              style={{ fontSize: 11.5, padding: "5px 11px", borderRadius: radius.pill,
                       border: `1px dashed ${color.dashed}`, color: color.ink,
                       background: "transparent", minWidth: 130, outline: "none" }}
            />
          )}
          {/* Aliases coming from the fallback locale — matched by the extractor, edited under
              their own language, so they're shown here read-only rather than silently merged. */}
          {inherited.map((a) => (
            <span key={`inh-${a}`} title={t("tp.viewOnly")}
                  style={{ ...chip, background: color.rowAltBg, color: color.muted }}>
              {a}
            </span>
          ))}
        </div>
      </Card>

      {/* The criteria the mapper reasons over — aliases only fire on close wording. */}
      <CriteriaEditor
        criteria={criteria}
        drafts={drafts}
        concepts={concepts}
        canonicalKey={canonicalKey}
        editable={editable}
        onChange={(p) => setCriteria((c) => ({ ...c, ...p }))}
        onDraft={(field, v) => setDrafts((d) => ({ ...d, [field]: v }))}
        t={t}
      />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        <Card>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 11 }}>{t("tp.signConvention")}</div>
          {SIGN_OPTIONS.map((opt) => {
            const on = opt.key === sign;
            return (
              <div
                key={opt.key}
                role={editable ? "button" : undefined}
                onClick={editable ? () => setSign(opt.key) : undefined}
                style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 0",
                         cursor: editable ? "pointer" : "default" }}
              >
                <Radio on={on} />
                <span style={{ fontSize: 12, color: on ? color.ink : color.sec2, fontWeight: on ? 600 : 400 }}>
                  {t(opt.labelKey)}
                </span>
              </div>
            );
          })}
        </Card>
        <Card>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 11 }}>{t("tp.dataTypeUnits")}</div>
          <div style={{ marginBottom: 11 }}>
            <FieldMock label={t("tp.valueType")} value={cfg.value_type} />
          </div>
          <FieldMock label={t("tp.aggregation")} value={cfg.aggregation} />
        </Card>
      </div>

      {editable && (dirty || msg) && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <button
            onClick={save}
            disabled={busy || !dirty}
            style={{ fontSize: 12, fontWeight: 600, color: "#fff", background: color.indigo,
                     border: "none", borderRadius: radius.control, padding: "8px 14px",
                     cursor: busy || !dirty ? "default" : "pointer", opacity: dirty ? 1 : 0.5 }}
          >
            {busy ? t("tp.saving") : t("tp.save")}
          </button>
          {dirty && !busy && (
            <button
              onClick={discard}
              style={{ fontSize: 12, color: color.sec, background: "none",
                       border: `1px solid ${color.controlBorder}`, borderRadius: radius.control,
                       padding: "8px 12px", cursor: "pointer" }}
            >
              {t("tp.revert")}
            </button>
          )}
          {dirty && <span style={{ fontSize: 11, color: color.muted }}>{t("tp.unsaved")}</span>}
          {msg && (
            <span style={{ fontSize: 11.5, color: msg.ok ? color.greenFg : color.redFg }}>
              {msg.text}
            </span>
          )}
        </div>
      )}
    </>
  );
}


/** Page 2 — one template's detail, raised over the index.
 *
 * Everything that belongs to a single template is here: its structure tree and the editor for the
 * line item a selected template line maps to. The way back to the list is rendered BEFORE the body
 * and in every state, including the two states where there is nothing to show — a reader who
 * followed a stale `?template=` link must not land on a screen with no exit.
 */
function TemplateDetail({ id, tpl, locale, canEdit, onDismiss, t }: {
  id: string; tpl: TemplateRef | undefined; locale: Locale; canEdit: boolean;
  onDismiss: () => void; t: (k: string) => string;
}) {
  const { data, isError } = useTemplateDetail(id, locale);
  const tplSel = useUI((s) => s.tplSel);
  const setTpl = useUI((s) => s.setTpl);
  // THE STARTER-FILE DOWNLOAD IS GONE (`useDownloadOntologySkeleton` and the error line beside
  // it). It emitted an empty ontology stubbed from this template's keys — a file for authoring a
  // second, rival rulebook. Authoring starts from the line-item configuration in force now, so
  // there is nothing to seed and no skeleton route to call.
  // Which editors are holding an unsaved change, by name. The page only needs to know whether ANY
  // of them is, to decide whether leaving costs the reader work.
  const [dirtyBy, setDirtyBy] = useState<Record<string, boolean>>({});
  const [confirmLeave, setConfirmLeave] = useState(false);
  // Identity-stable: the editors report from an effect, so a callback rebuilt on every render
  // would re-run it on every render. Returning the SAME object when nothing changed keeps React
  // from re-rendering us for a report that said what we already knew.
  const reportDirty = useCallback<ReportDirty>((source, dirty) => {
    setDirtyBy((m) => (!!m[source] === dirty ? m : { ...m, [source]: dirty }));
  }, []);
  const dirty = Object.values(dirtyBy).some(Boolean);

  // The selected concept, and ONLY that one. There used to be a fallback chain here — a hardcoded
  // "trade_recv", then whichever concept happened to be first — which let the editor answer about a
  // concept nobody had chosen: `trade_recv` is not a key this product's template declares, so a
  // selection the tree could not resolve (a calculated total, before the walk that serves them was
  // fixed; a `tplSel` left over from another template) rendered Property, Plant and Equipment's
  // aliases, sign and criteria under the heading of the line that was clicked, with no row
  // highlighted to give it away. An analyst editing that is editing the wrong concept.
  const cfg: NodeConfig | undefined = data ? data.node_config[tplSel] : undefined;
  // Every line item the CONFIGURATION IN FORCE declares, in statement order — the only legal
  // values for `confusable_with`, so it is picked from here. A template line the configuration does
  // not declare is left out: the server rejects any key the line-item set does not declare (422,
  // "names unknown items"), so offering it would be a picker entry whose only possible outcome is a
  // refused save. The shipped template has two such lines.
  const concepts: Concept[] = Object.entries(data?.node_config ?? {})
    .filter(([, c]) => c.mapped !== false)
    .map(([key, c]) => ({ key, label: c.label || key }));

  // Open ON a line: the detail's job is to show a concept's rules, so arriving with nothing
  // resolved SELECTS the tree's first line rather than rendering another concept's rules behind
  // its back. `tplSel` starts life as a demo key no template declares (store.ts) and survives a
  // move between templates, so "resolves to nothing" is the ordinary first state, not an error.
  // Selecting is the honest form of the deleted fallback: it moves the highlight in the tree, so
  // the row the reader sees selected is the concept the editor is editing.
  const first = data?.tree.find((n) => !n.head && data.node_config[n.id])?.id;
  useEffect(() => {
    if (!cfg && first) setTpl(first);
  }, [cfg, first, setTpl]);

  function body() {
    if (isError) {
      return (
        <div style={{ maxWidth: 520, margin: "60px auto", textAlign: "center", color: color.muted,
                      padding: "0 24px", fontSize: 12.5, lineHeight: 1.6 }}>
          {t("tp.detail.gone")}
        </div>
      );
    }
    if (!data) {
      return (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
                      color: color.muted }}>
          {t("empty.loading")}
        </div>
      );
    }
    // Greenfield (no project loaded yet): the template tree is empty. Show guidance rather
    // than crashing on a missing node config. A tree that HAS lines but no resolved selection is
    // not this state — it still renders, so the reader can pick one (see the right pane below).
    if (!data.tree.length) {
      return (
        <div style={{ maxWidth: 560, margin: "60px auto", textAlign: "center", color: color.muted,
                      padding: "0 24px" }}>
          <div style={{ fontSize: 28, marginBottom: 10 }}>◆</div>
          <h1 style={{ fontSize: 18, fontWeight: 600, color: color.ink, marginBottom: 8 }}>
            {t("tp.emptyTitle")}
          </h1>
          <p style={{ fontSize: 12.5, lineHeight: 1.6 }}>{t("tp.emptyHint")}</p>
        </div>
      );
    }

    return (
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* LEFT: template tree */}
        <div
          style={{
            width: 360,
            flex: "0 0 360px",
            borderRight: `1px solid ${color.cardBorder}`,
            background: color.surface,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          <div style={{ padding: 16, flex: "0 0 auto", borderBottom: `1px solid ${color.hairline3}` }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 3 }}>{t("tp.structure")}</h2>
            {/* WHOSE structure. The split into index + detail left the tree unlabelled: with
                several versions of several templates in the index, a reader who scrolled the tree
                had nothing on screen saying which one they were editing the rules of. */}
            <div data-testid="tpl-tree-template"
                 style={{ fontSize: 12, fontWeight: 600, color: color.ink, marginBottom: 2 }}>
              {name}
            </div>
            <div style={{ fontSize: 11.5, color: color.muted }}>
              <span style={{ fontFamily: font.mono }}>
                {[data.template.key, tpl && `v${tpl.version}`].filter(Boolean).join(" · ")}
              </span>
              {`  ·  ${data.template.line_items} ${t("tp.lineItems")}`}
            </div>
          </div>

          <div style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "8px 6px" }}>
            {data.tree.map((node) => {
              const sel = node.id === tplSel;
              const head = !!node.head;
              return (
                <div
                  key={node.id}
                  // Only lines map to a line item (headings carry no rules to edit) — and
                  // only lines are clickable. A heading used to select its own id, which no
                  // `node_config` key can match: the click threw away whatever the analyst had
                  // selected and put a different concept, or nothing, in the editor.
                  data-testid={head ? undefined : "tpl-node"}
                  onClick={head ? undefined : () => setTpl(node.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "7px 10px",
                    borderRadius: radius.controlSm,
                    cursor: head ? "default" : "pointer",
                    background: sel ? color.indigoTint : "transparent",
                    marginLeft: node.lvl * 16,
                  }}
                >
                  <span style={{ fontSize: 10, color: color.faint, width: 10 }}>{head ? "▾" : ""}</span>
                  <span
                    style={{
                      flex: 1,
                      fontSize: 12,
                      fontWeight: head ? 700 : sel ? 600 : 400,
                      color: head ? color.viewerBg : sel ? color.indigo : color.ink2,
                    }}
                  >
                    {node.label}
                  </span>
                  {node.rule && (
                    <span
                      style={{
                        fontSize: 9.5,
                        fontWeight: 600,
                        padding: "1px 6px",
                        borderRadius: 4,
                        background: color.indigoTint2,
                        color: color.indigo,
                      }}
                    >
                      rule
                    </span>
                  )}
                </div>
              );
            })}
          </div>

        </div>

        {/* RIGHT: node editor */}
        <div style={{ flex: 1, minWidth: 0, overflowY: "auto", padding: "26px 30px" }}>
          <div style={{ maxWidth: 680 }}>
            {cfg ? (
              <>
                <div style={{ fontSize: 11, color: color.muted, marginBottom: 3 }}>{cfg.breadcrumb}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 3 }}>
                  <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>{cfg.label}</h1>
                  {!canEdit && (
                    <span style={{ fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: radius.pill,
                                   background: color.rowAltBg, color: color.muted, border: `1px solid ${color.hairline3}` }}>
                      {t("tp.viewOnly")}
                    </span>
                  )}
                  {/* Not the same statement as "View only", which is about the READER's permission:
                      this line has no rules to edit at all, and an admin has to be told which of the
                      two is why the editor below is inert. */}
                  {cfg.mapped === false && (
                    <span data-testid="tpl-node-unmapped"
                          style={{ fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: radius.pill,
                                   background: color.amberBg, color: color.amberFg }}>
                      {t("tp.notMapped")}
                    </span>
                  )}
                </div>
                <p style={{ margin: "0 0 20px", color: color.sec2, fontSize: 12.5 }}>
                  {cfg.mapped === false ? t("tp.notMappedHint")
                    : canEdit ? t("tp.editorSubhead") : t("tp.viewOnlyHint")}
                </p>

                {/* Editable rules for this line item (aliases + sign + mapping criteria), saved
                    as a new line-item version. `line_items.id` is the version the PATCH targets:
                    the ONE configuration a template's detail edits. */}
                <NodeRules
                  cfg={cfg}
                  canonicalKey={cfg.canonical_key ?? tplSel}
                  lineItemVersionId={data.line_items?.id}
                  concepts={concepts}
                  locale={locale}
                  canEdit={canEdit}
                  onDirty={reportDirty}
                  t={t}
                />

                {/* THE READ-ONLY NETTING CARD IS GONE from here with the netting editor below —
                    it described one ontology concept's note-to-face restatement. Netting lives in
                    the line-item set now, so this page has no netting display of its own. */}
              </>
            ) : (
              /* No line item resolved, so no rules — never a stand-in for the one that
                 didn't. With the selection seeded above this is the case where the tree offers
                 nothing to select (a template of headings alone), which is why it asks rather than
                 explains. The words are the review screen's `r.remapPick`, the app's one localized
                 sentence for "choose a template line"; a second spelling of it is a second thing to
                 keep translated in four languages. */
              <div data-testid="tpl-no-selection"
                   style={{ margin: "40px auto", textAlign: "center", color: color.muted,
                            fontSize: 12.5, lineHeight: 1.6 }}>
                <div style={{ fontSize: 28, marginBottom: 10 }}>◆</div>
                {t("r.remapPick")}
              </div>
            )}

            {/* THE TEMPLATE-WIDE NETTING EDITOR STOOD HERE (`NettingRules` over
                `data.netting_rules`). It published ontology versions; the shipped configuration
                declares zero netting rules, so it governed nothing. Netting is part of the
                line-item set — edit it through `/line-items`, not by reinstating this. */}
          </div>
        </div>
      </div>
    );
  }

  const name = tpl?.name || data?.template.name || tpl?.template_key || "";
  return (
    <div
      data-testid="template-detail"
      style={{
        position: "absolute", inset: 0, zIndex: 5, background: color.pageBg,
        display: "flex", flexDirection: "column", minHeight: 0,
      }}
    >
      <div style={{ flex: "0 0 auto", background: color.surface,
                    borderBottom: `1px solid ${color.cardBorder}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 18px" }}>
          <button
            data-testid="tpl-detail-close"
            // An unsaved alias or criterion is work that only exists here: it is not stored until
            // Save publishes a new line-item version. Leaving used to discard it with no word, so
            // the ask comes first and the reader chooses.
            onClick={() => { if (dirty) setConfirmLeave(true); else onDismiss(); }}
            style={{ fontSize: 12, fontWeight: 600, color: color.indigo, background: "#fff",
                     border: `1px solid ${color.indigoBorder2}`, borderRadius: radius.control,
                     padding: "7px 12px", cursor: "pointer", whiteSpace: "nowrap" }}
          >
            {t("tp.detail.back")}
          </button>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: color.ink }}>{name}</div>
            <div style={{ fontSize: 11, color: color.muted, fontFamily: font.mono }}>
              {[tpl?.template_key ?? data?.template.key, tpl && `v${tpl.version}`]
                .filter(Boolean).join(" · ")}
            </div>
          </div>
          {/* THE STARTER-FILE DOWNLOAD CARD STOOD HERE. It handed an author an empty ontology
              stubbed from this template's keys — the first step of authoring a rival rulebook.
              The one configuration engine is the line items, and it ships configured, so the
              starting point is the version in force (Line items screen), not a blank file. */}
        </div>
        {/* Gated on `dirty` as well as on the ask: saving while the question is on screen answers
            it — there is nothing left to discard, so the warning must not keep claiming there is. */}
        {confirmLeave && dirty && (
          <div data-testid="tpl-leave-confirm"
               style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
                        padding: "9px 18px", background: color.amberBg,
                        borderTop: `1px solid ${color.amberFg}33` }}>
            <span style={{ fontSize: 11.5, color: color.amberFg, fontWeight: 600 }}>
              {t("tp.detail.leaveWarn")}
            </span>
            <button
              data-testid="tpl-leave-stay"
              onClick={() => setConfirmLeave(false)}
              style={{ fontSize: 12, fontWeight: 600, color: "#fff", background: color.indigo,
                       border: "none", borderRadius: radius.control, padding: "6px 12px",
                       cursor: "pointer" }}
            >
              {t("tp.detail.leaveStay")}
            </button>
            <button
              data-testid="tpl-leave-discard"
              onClick={onDismiss}
              style={{ fontSize: 12, color: color.redFg, background: "#fff",
                       border: `1px solid ${color.redFg}`, borderRadius: radius.control,
                       padding: "6px 12px", cursor: "pointer" }}
            >
              {t("tp.detail.leaveDiscard")}
            </button>
          </div>
        )}
      </div>
      {body()}
    </div>
  );
}

export default function TemplateScreen() {
  const locale = useAppLocale();
  const t = useT();
  // Render the REAL configured template(s), not the demo project: the index lists the templates
  // that exist, and a row's detail (tree + per-node config) comes from that template and the
  // line-item configuration in force for it.
  const tplList = useTemplates();
  const canEdit = useCan("config:template"); // authoring is admin-only
  // Which template's detail is up lives in the URL, so a reload — and the browser's own Back
  // button — return to what was on screen instead of resetting to the index.
  const [params, setParams] = useSearchParams();
  const openId = params.get("template") ?? undefined;
  // The version the authoring buttons act on. It follows the last row opened or picked, and
  // falls back to the first template so Download/Upload are never aimed at nothing.
  const [picked, setPicked] = useState<string | undefined>(undefined);

  if (!tplList.data) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center",
                    height: "100%", color: color.muted }}>
        {t("empty.loading")}
      </div>
    );
  }

  const templates = tplList.data;
  // Default the authoring target to the index's first row rather than the API's first record: the
  // list shows the newest version of a template at the top, and that is the one an admin means by
  // "the active template" when they download it to edit.
  const activeId = picked ?? openId ?? sortTemplates(templates)[0]?.id;

  function setOpen(id: string | undefined) {
    const next = new URLSearchParams(params);
    if (id) next.set("template", id);
    else next.delete("template");
    setParams(next);
  }

  return (
    <div style={{ position: "relative", height: "100%", minHeight: 0, display: "flex",
                  flexDirection: "column", overflow: "hidden" }}>
      {/* The index stays MOUNTED under the detail: dismissing has to put the reader back on the
          list they were reading, with its filter and scroll position, not on a fresh one. */}
      <TemplateList
        templates={templates}
        activeId={activeId}
        canEdit={canEdit}
        // …but mounted is not the same as reachable: while the detail is up the list is inert, so
        // Tab cannot walk out of a dirty editor into the rows behind it.
        covered={!!openId}
        locale={locale}
        onPick={setPicked}
        onOpen={(id) => { setPicked(id); setOpen(id); }}
        t={t}
      />
      {openId && (
        <TemplateDetail
          // Keyed on the template, so changing which one is open is a fresh MOUNT rather than a
          // re-render of the same instance. Unkeyed, browser Back (or a Tab-reachable row under the
          // overlay) swapped the subject while React reused the editor — leaving an unsaved alias
          // authored against one version on screen, still dirty, under the other version's header.
          key={openId}
          id={openId}
          tpl={templates.find((x) => x.id === openId)}
          locale={locale}
          canEdit={canEdit}
          onDismiss={() => setOpen(undefined)}
          t={t}
        />
      )}
    </div>
  );
}
