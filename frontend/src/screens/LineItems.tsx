/** Line items — the configuration behind the eight output lines and their sub-line items.
 *
 * WHY THIS SCREEN EXISTS. Those eight lines are each assembled from parts that lived only as
 * Python: `deprec_impairment` alone reads thirteen note-level datasets and resolves them through a
 * five-rung cascade, and the template declares the eight with no children at all — so nothing
 * outside that module knew the parts existed. Widening one caption meant editing a
 * 162-alternative regex and shipping a release. This is the surface on which the whole arrangement
 * is read AND authored.
 *
 * THIS IS THE EDITOR FOR THE ONE CONFIGURATION THE PIPELINE READS. It was read-only, and said so,
 * on the justification that the definitions only DESCRIBED derivations five services computed
 * (`implemented_by` names which), so nothing downstream read them. That justification expired:
 * line items is the single configuration engine, the matcher is built from this set by
 * `services/working_view.py`, and a run pins `extraction_runs.line_item_version_id`. The
 * definitions DRIVE extraction, so a field that is authorable in the schema and unreachable from
 * here is a control the product claims to have and does not.
 *
 * A SAVE PUBLISHES A NEW VERSION and never writes in place. A run pins the exact version it used,
 * so mutating a stored definition would retroactively change how a past run is explained; the
 * endpoint (`PATCH /line-items/versions/{id}/items`) re-validates the whole edited set against the
 * target template and stores a new row, and the caption above the list then names that row as the
 * one in force. A refusal is therefore information the author needs — it comes back addressed per
 * field and is printed on the control that caused it, in the server's own words.
 *
 * WHAT THE FORM HAS TO GET RIGHT is stated once, on `components/configFields.tsx`, which owns
 * every control: absent / null / configured-empty are three different statements, a refusal
 * belongs to a control, an inherited value is not a declared one, and a field withheld on purpose
 * is shown read-only WITH its reason rather than being silently absent. The groups below are
 * ordered as QUESTIONS, meaning first: `description` and `definition` are what let a caption
 * resolve by meaning rather than by string match, so they are at the top of the pane and not
 * behind a disclosure.
 */
import { useState, type ReactNode } from "react";

import {
  BoolField, KeyPicker, LockedRow, NumberField, OrderedMultiSelect, RungCards, SelectField,
  StringListEditor, TermRows, TextArea, TextField, TriBoolField, type KeyOption,
} from "../components/configFields";
import { Button, Card } from "../components/ui";
import { useT } from "../i18n";
import { ApiError, refusalText } from "../lib/api";
import {
  useAddLineItem, useDeleteLineItem, useEditLineItemConfig, useLineItems,
} from "../lib/queries";
import { useCan } from "../lib/rbac";
import { SCREENS } from "./config";
import { color, font, radius } from "../theme";
import type {
  CaptionNormalization, LineItemAliasMatching, LineItemDef, LineItemEdit,
  LineItemExtractionMode, LineItemNamespace, LineItemNoteUse, LineItemRollup, LineItemSetInfo,
  LineItemSide, LineItemTemporality, LineItemType, LineItemUnitOfAccount, LineItemVocab,
  NoteSource, ResidualPolicy, SearchScope, SignExpectation, SignRuleConvention, StatementToken,
  ValueScope,
} from "../types";

const TYPE_TONE: Record<LineItemType, { bg: string; fg: string; label: string }> = {
  extracted: { bg: color.greenBg, fg: color.greenFg, label: "Extracted" },
  calculated: { bg: color.indigoTint2, fg: color.indigo, label: "Calculated" },
  intermediate: { bg: color.segBg, fg: color.sec2, label: "Intermediate" },
  derived: { bg: color.amberBg, fg: color.amberFg, label: "Derived" },
};

const SCOPE_LABEL: Record<string, string> = {
  notes: "Notes to the accounts",
  balance_sheet: "Balance sheet",
  profit_and_loss: "Profit & loss",
  cash_flow: "Cash flow",
  equity_changes: "Changes in equity",
  covenants_supplemental: "Covenants / supplemental",
  statement_setup: "Statement setup",
  front_matter: "Chairman / MD&A",
};

/** The seven statements a line item can be gated to, as a reader names them. The TOKENS come from
 *  `vocab.statements`; this map only spells them for a human, and falls back to the token. */
const STATEMENT_LABEL: Record<string, string> = {
  statement_setup: "Statement setup",
  balance_sheet: "Balance sheet",
  profit_and_loss: "Profit & loss",
  cash_flow: "Cash flow",
  equity_changes: "Changes in equity",
  covenants_supplemental: "Covenants / supplemental",
  notes: "Notes",
};

const SIDE_LABEL: Record<string, string> = {
  from_section: "From the section banner", asset: "Asset", liability: "Liability",
  equity: "Equity", none: "n/a — nothing was said",
};

const ROLLUP_HELP: Record<string, string> = {
  sum: "the parts below add up to this line",
  alternatives: "the parts below are alternative sources for ONE figure — never summed",
  none: "this line has no parts",
};

const MODE_HELP: Record<string, string> = {
  extract: "read off the filing",
  extract_or_derive: "read off the filing, or computed if no row is printed",
  derive: "computed — and STILL a candidate the matcher can recognise",
  do_not_extract: "the only value that removes this line from candidacy entirely",
};

const VALUE_SCOPE_HELP: Record<string, string> = {
  exclusive_leaf: "a stand-alone figure that overlaps nothing",
  exclusive_child: "a component of a gross parent — never summed with it",
  exclusive_residual: "computed as the unexplained remainder of its section",
  not_applicable: "not extracted, so overlap does not arise",
};

const ALIAS_MATCHING_HELP: Record<string, string> = {
  enabled: "reachable by every matching tier",
  disabled: "unreachable by every matching tier — fillable only by the residual sweep. This is "
    + "the lock that defines a residual bucket.",
};

const NAMESPACE_HELP: Record<string, string> = {
  template: "held against the target template's canonical keys by the publish gate",
  internal: "names no output column, so the key gate does not apply — a note-level part that is "
    + "part of a line rather than a line",
};

const NOTE_USE_HELP: Record<string, string> = {
  evidence_only: "a cited note may evidence this figure but never be its source",
  decomposition_allowed: "a cited note may be the SOURCE this line is read from",
};

/** A fresh nullable sub-object, so switching one on writes a shape the loader accepts rather than
 *  a half-object the model then refuses. Every list starts EMPTY — a configured empty, which is
 *  what "I have declared this object and not yet its patterns" actually means. */
const NEW_NOTE_SOURCE: NoteSource = {
  note_title_any: [], row_caption_any: [], row_caption_none: [], caption_normalization: "none",
};
const NEW_RESIDUAL_POLICY: ResidualPolicy = {
  framework: "", section_scope: "", population: "", cross_section: false,
  notes_as_source: false, plug: false, itemise: false,
};

function Tag({ type }: { type: LineItemType }) {
  const t = TYPE_TONE[type];
  return (
    <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.3, textTransform: "uppercase",
                    padding: "3px 7px", borderRadius: 4, background: t.bg, color: t.fg,
                    whiteSpace: "nowrap" }}>
      {t.label}
    </span>
  );
}

/** A monospace list of patterns, scrollable in its own box.
 *
 *  Kept from the read-only screen for exactly one job now: the SIBLING LOCALES' alias lists, which
 *  are read-only here by contract (`aliases` is locale-scoped, and a map-shaped write is precisely
 *  how editing the Chinese aliases clobbers the English ones). A caption list that clips is the
 *  thing this screen was built to stop being invisible. */
function Patterns({ label, values, tone }: { label: string; values: string[]; tone?: string }) {
  if (!values.length) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.3, textTransform: "uppercase",
                    color: color.muted, marginBottom: 5 }}>
        {label} <span style={{ fontFamily: font.mono, fontWeight: 500 }}>({values.length})</span>
      </div>
      <div style={{ maxHeight: 132, overflowY: "auto", border: `1px solid ${color.hairline2}`,
                    borderRadius: 7, background: color.rowAltBg }}>
        {values.map((v, i) => (
          <div key={`${v}-${i}`}
               style={{ fontFamily: font.mono, fontSize: 11, padding: "4px 8px",
                        color: tone ?? color.ink2, wordBreak: "break-all",
                        borderBottom: i === values.length - 1 ? "none"
                                                              : `1px solid ${color.hairline}` }}>
            {v}
          </div>
        ))}
      </div>
    </div>
  );
}

/** ONE GROUP OF THE FORM, headed by the QUESTION it answers.
 *
 *  The headings are questions rather than field-category nouns because the author arrives with a
 *  question ("why did this caption land on the wrong line?") and not with a field name. */
function Group({ question, note, right, children }: {
  question: string; note?: ReactNode; right?: ReactNode; children: ReactNode;
}) {
  return (
    <Card pad={13} style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between",
                     gap: 12, flexWrap: "wrap", marginBottom: note ? 4 : 10 }}>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: color.ink }}>{question}</h3>
        {right}
      </div>
      {note && (
        <p style={{ margin: "0 0 11px", fontSize: 10.5, color: color.muted, lineHeight: 1.5 }}>
          {note}
        </p>
      )}
      {children}
    </Card>
  );
}

/** Is this value a statement at all? Used only to decide whether an INHERITED badge applies: a
 *  field the item did not declare but which carries a value got that value from its section. */
const nonEmpty = (v: unknown) =>
  v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0);

/** Deep equality, so a field TOUCHED and then put back is not sent.
 *
 *  It matters more than it looks: sending a field the item never declared turns an inherited value
 *  into a declared one and silently detaches the item from its section, so "the author clicked
 *  into this control" must not be enough to make it part of the payload. Only a value that
 *  actually differs from what the server served is sent. */
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => deepEqual(x, b[i]));
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a as object);
    const kb = Object.keys(b as object);
    if (ka.length !== kb.length) return false;
    return ka.every((k) => deepEqual((a as Record<string, unknown>)[k],
                                     (b as Record<string, unknown>)[k]));
  }
  return false;
}

/** THE ALIAS LIST THE EDITOR IS EDITING, for one locale.
 *
 *  `aliases` on the wire replaces `aliases_i18n[locale]` — and the base `aliases` list too when
 *  `locale` is the set's own default. So the baseline a draft is compared against has to be read
 *  the same way round, or switching locale would look like an edit. */
function aliasesFor(item: LineItemDef, set: LineItemSetInfo, locale: string): string[] {
  const perLocale = item.aliases_i18n ?? {};
  if (perLocale[locale]) return perLocale[locale];
  return locale === set.locale ? (item.aliases ?? []) : [];
}

/** WHAT THE SERVER SERVED FOR ONE EDITABLE FIELD — the value a draft is compared against.
 *
 *  Two fields are not a straight name match, and both are deliberate:
 *   * `aliases` is locale-scoped (above).
 *   * `sign_expectation` on the wire IS `sign_convention` on the item. The wire name
 *     `sign_convention` is taken by the legacy 3-token spelling the Template screen sends, and one
 *     name for two questions is how an author changes the wrong one. */
function baselineOf(item: LineItemDef, set: LineItemSetInfo, locale: string,
                    field: keyof LineItemEdit): unknown {
  if (field === "aliases") return aliasesFor(item, set, locale);
  const name = field === "sign_expectation" ? "sign_convention" : field;
  return (item as unknown as Record<string, unknown>)[name];
}

/* ── the detail pane: the editor ─────────────────────────────────────────────────────────────── */

interface EditorProps {
  item: LineItemDef;
  set: LineItemSetInfo;
  /** Every value a control may offer. Absent only on a server that predates it, which is the one
   *  case the form must refuse to author in rather than guess a vocabulary. */
  vocab: LineItemVocab | undefined;
  keys: KeyOption[];
  canEdit: boolean;
  versionId: string | undefined;
  versionNumber: number | undefined;
  locale: string;
  onLocale: (locale: string) => void;
  draft: Partial<LineItemEdit>;
  patch: (p: Partial<LineItemEdit>) => void;
  drop: (field: keyof LineItemEdit) => void;
  errors: Record<string, string>;
  indexErrors: Record<string, Record<number, string>>;
  formError: string | null;
  saved: number | null;
  saving: boolean;
  changed: (keyof LineItemEdit)[];
  summary: string;
  onSave: () => void;
  onDiscard: () => void;
  onSelect: (key: string) => void;
  aliasDraft: string;
  onAliasDraft: (v: string) => void;
}

function Detail(p: EditorProps) {
  const { item, set, vocab, keys, locale, patch, drop, errors, indexErrors } = p;
  // NO VOCABULARY, NO AUTHORING. Every select's options come from what the server served, derived
  // there from the same `Literal[...]` aliases the loader validates with. A control offering a
  // token this deployment's gate refuses is worse than no control: the author authors, saves, and
  // is told no by a validator two layers down.
  const editable = p.canEdit && !!vocab && !!p.versionId;

  /** The value in force in the form: the draft when the author has touched the field, else what
   *  the server served. The baseline is spelled at each call, which is also where a reader can see
   *  which served field the control writes. */
  const g = <T,>(field: keyof LineItemEdit, base: T): T =>
    (field in p.draft ? (p.draft[field] as unknown as T) : base);

  const declared = new Set(item.declared_fields ?? []);
  // A server that does not state what an item declared says nothing about inheritance either —
  // reading an empty list as "declared nothing" would badge every populated control on the screen.
  const knowsDeclared = (item.declared_fields ?? []).length > 0;
  /** The section this value was folded in from, when the item did not declare it itself. */
  const inh = (field: string, value: unknown) =>
    (knowsDeclared && !declared.has(field) && nonEmpty(value) && item.inherits)
      ? item.inherits : undefined;

  /** ONE FIELD, WRAPPED SO A TEST AND A READER CAN BOTH FIND IT.
   *
   *  `li-field-<name>` addresses the control; `li-field-error-<name>` appears only when the server
   *  refused this field, and WRAPS the control so the refusal is rendered exactly once — by the
   *  control itself, in the server's own words, with the invalid border on the input. A second copy
   *  of the message under a marker element would be the same sentence twice. */
  const fld = (name: string, render: (error?: string) => ReactNode) => (
    <div data-testid={`li-field-${name}`} key={name}>
      <div data-testid={errors[name] ? `li-field-error-${name}` : undefined}>
        {render(errors[name])}
      </div>
    </div>
  );
  const idx = (name: string) => indexErrors[name];

  const type = g<LineItemType>("type", item.type);
  const noteSource = g<NoteSource | null>("note_source", item.note_source);
  const residual = g<ResidualPolicy | null>("residual_policy", item.residual_policy);
  const signRule = g("sign_rule", item.sign_rule);
  const perLocale = item.aliases_i18n ?? {};
  const otherLocales = Object.keys(perLocale).filter((l) => l !== locale);

  const setNoteSource = (patchNs: Partial<NoteSource>) =>
    patch({ note_source: { ...(noteSource ?? NEW_NOTE_SOURCE), ...patchNs } });
  const setResidual = (patchRp: Partial<ResidualPolicy>) =>
    patch({ residual_policy: { ...(residual ?? NEW_RESIDUAL_POLICY), ...patchRp } });

  // A live compile of `pattern`, so a torn regex is known before the save rather than as a 422.
  // The server compiles it too and its refusal is what lands on the control; this is only the
  // faster half of the same answer.
  const pattern = g("pattern", item.pattern);
  let patternNote: ReactNode = null;
  if (pattern) {
    try {
      new RegExp(pattern);
      patternNote = <div style={{ fontSize: 10.5, color: color.greenFg, marginTop: 4 }}>
        compiles
      </div>;
    } catch (e) {
      patternNote = <div style={{ fontSize: 10.5, color: color.redFg, marginTop: 4 }}>
        does not compile: {(e as Error).message}
      </div>;
    }
  }

  const lockReason = !p.canEdit
    ? "you do not have `config:line_items`"
    : !vocab
      ? "this server did not serve the editor's vocabulary, so no control can offer a legal value"
      : !p.versionId
        ? "no stored line-item version answered this read, so there is nothing to publish from"
        : undefined;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>{item.label || item.key}</h2>
        <Tag type={type} />
        {g("in_output", item.in_output)
          ? <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                            letterSpacing: 0.3, padding: "2px 6px", borderRadius: 4,
                            background: color.indigo, color: "#fff" }}>In output</span>
          : <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                            letterSpacing: 0.3, padding: "2px 6px", borderRadius: 4,
                            background: color.segBg, color: color.muted }}>Internal</span>}
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 11, color: color.muted, margin: "3px 0 10px" }}>
        {item.key}
      </div>

      {/* WHY EVERY CONTROL IS DISABLED, said once and at the top. `FieldRow` prints a reason on
          the controls that take one, but a form of seventy disabled controls needs the answer
          before the reader starts hunting for it. */}
      {lockReason && (
        <div style={{ background: color.amberBg, color: color.amberFg, borderRadius: 8,
                       padding: "8px 11px", fontSize: 11.5, marginBottom: 12, lineHeight: 1.5 }}>
          Read-only — {lockReason}.
        </div>
      )}

      {p.formError && (
        <div data-testid="li-form-error"
             style={{ background: color.redBg, color: color.redFg, borderRadius: 8,
                       padding: "9px 12px", fontSize: 12, marginBottom: 12, lineHeight: 1.55 }}>
          {p.formError}
        </div>
      )}
      {p.saved !== null && p.changed.length === 0 && (
        <div data-testid="li-saved"
             style={{ background: color.greenBg, color: color.greenFg, borderRadius: 8,
                       padding: "8px 11px", fontSize: 11.5, marginBottom: 12 }}>
          Published as v{p.saved} — the version the next run pins.
        </div>
      )}

      {/* ── 1. MEANING ────────────────────────────────────────────────────────────────────────
          FIRST, AND NOT BEHIND A DISCLOSURE. `definition` is what `meaning()` hands the
          description-matching tier (it prefers it over `description`), and the four criteria
          fields are what let a caption be resolved by MEANING rather than by string match — which
          is the difference between widening one line and editing a 162-alternative regex. */}
      <Group question="What is this line, in words?"
             note="What the model reads when the printed caption is not close to any alias.">
        {fld("label", (e) => (
          <TextField label="Label" testid="label" editable={editable} reason={lockReason}
                     help="The name on this screen, the statement grids and the export. Display
                           prose — it has no matching consequence."
                     value={g("label", item.label)} onChange={(v) => patch({ label: v })}
                     error={e} inherited={inh("label", item.label)} />
        ))}
        {fld("description", (e) => (
          <TextArea label="Description" testid="description" editable={editable} rows={3}
                    reason={lockReason}
                    help="Display prose about the line — and the model's semantic text when
                          `definition` is empty."
                    value={g("description", item.description)}
                    onChange={(v) => patch({ description: v ?? "" })}
                    error={e} inherited={inh("description", item.description)} />
        ))}
        {fld("definition", (e) => (
          <TextArea label="Definition — the authoritative accounting meaning" testid="definition"
                    editable={editable} rows={4} reason={lockReason}
                    help="Preferred over the description by `meaning()`, so this is the text a
                          printed caption is compared against by the description tier. The single
                          highest-leverage field for resolving a caption by meaning."
                    value={g("definition", item.definition)}
                    onChange={(v) => patch({ definition: v ?? "" })}
                    error={e} inherited={inh("definition", item.definition)} />
        ))}
        {fld("include_criteria", (e) => (
          <StringListEditor label="Counts as this line" testid="include_criteria"
                            editable={editable} variant="prose"
                            help="Prose criteria shown to the model — what COUNTS. One statement
                                  per entry."
                            placeholder="e.g. bank balances repayable on demand"
                            value={g("include_criteria", item.include_criteria)}
                            onChange={(v) => patch({ include_criteria: v })}
                            error={e} indexErrors={idx("include_criteria")}
                            inherited={inh("include_criteria", item.include_criteria)} />
        ))}
        {fld("exclude_criteria", (e) => (
          <StringListEditor label="Does NOT count as this line" testid="exclude_criteria"
                            editable={editable} variant="prose"
                            help={<>Prose criteria shown to the model. Deliberately distinct from
                                  the regex vetoes in <b>Which printed captions are this line?</b>
                                  {" "}— prose here, patterns there. Folding prose into the regex
                                  list either fails to compile or compiles as an accidental
                                  veto.</>}
                            placeholder="e.g. bank overdrafts, which are a liability"
                            value={g("exclude_criteria", item.exclude_criteria)}
                            onChange={(v) => patch({ exclude_criteria: v })}
                            error={e} indexErrors={idx("exclude_criteria")}
                            inherited={inh("exclude_criteria", item.exclude_criteria)} />
        ))}
        {fld("confusable_with", (e) => (
          <KeyPicker label="Easy to confuse with" testid="confusable_with" multi
                     editable={editable} reason={lockReason} options={keys} exclude={[item.key]}
                     help="Routes an unresolvable pair to review instead of letting the engine
                           pick one at confidence 1.0."
                     value={g("confusable_with", item.confusable_with)}
                     onChange={(v) => patch({ confusable_with: v })}
                     error={e} indexErrors={idx("confusable_with")}
                     inherited={inh("confusable_with", item.confusable_with)} />
        ))}
        {fld("section_disambiguation", (e) => (
          <TextArea label="Which of two look-alike captions this is" testid="section_disambiguation"
                    editable={editable} rows={2} nullable reason={lockReason}
                    help="Not decoration: `mapping.py` reads it, and it answers exactly the
                          question a containment collision asks."
                    value={g("section_disambiguation", item.section_disambiguation)}
                    onChange={(v) => patch({ section_disambiguation: v })}
                    error={e}
                    inherited={inh("section_disambiguation", item.section_disambiguation)} />
        ))}
      </Group>

      {/* ── 2. RECOGNITION ───────────────────────────────────────────────────────────────────
          THE LOCALE BEING EDITED IS IN THE HEADER, because `aliases` is locale-scoped: it
          replaces THAT locale's list, and the base list too when the locale is the set default.
          The other locales are read-only beside it and say so. A map-shaped write is precisely how
          editing the Chinese aliases clobbers the English ones. */}
      <Group question="Which printed captions are this line?"
             note={<>Recognition evidence, matched against the caption as printed. Aliases are
                   edited ONE LOCALE AT A TIME — the selector says which.</>}
             right={
               <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
                 <span style={{ fontSize: 10.5, color: color.muted }}>editing locale</span>
                 <select
                   value={locale}
                   data-testid="li-locale"
                   aria-label="Alias locale being edited"
                   disabled={!editable}
                   onChange={(ev) => p.onLocale(ev.target.value)}
                   style={{ fontSize: 11.5, fontFamily: font.mono, padding: "4px 8px",
                             borderRadius: radius.controlSm, cursor: editable ? "pointer" : "default",
                             border: `1px solid ${color.controlBorder}`,
                             background: editable ? color.surface : color.rowAltBg,
                             color: color.ink }}
                 >
                   {set.supported_locales.map((l) => <option key={l} value={l}>{l}</option>)}
                 </select>
               </span>
             }>
        {fld("aliases", (e) => (
          <StringListEditor label={`Aliases (${locale})`} testid="aliases" editable={editable}
                            help={<>The printed captions that claim this line in{" "}
                                  <b style={{ fontFamily: font.mono }}>{locale}</b>. Saving
                                  replaces this locale's list only.</>}
                            placeholder="Paste a caption exactly as printed…"
                            value={g("aliases", aliasesFor(item, set, locale))}
                            onChange={(v) => patch({ aliases: v })}
                            draft={p.aliasDraft} onDraft={p.onAliasDraft}
                            error={e} indexErrors={idx("aliases")} />
        ))}
        {locale === set.locale && (
          <p style={{ fontSize: 10.5, color: color.sec2, margin: "-6px 0 12px", lineHeight: 1.5 }}>
            <b style={{ fontFamily: font.mono }}>{locale}</b> is this set's default locale, so
            saving updates the base <span style={{ fontFamily: font.mono }}>aliases</span> list as
            well as <span style={{ fontFamily: font.mono }}>aliases_i18n[{locale}]</span>.
          </p>
        )}
        {otherLocales.length > 0 && (
          <div style={{ marginBottom: 14 }}>
            {otherLocales.map((l) => (
              <Patterns key={l} label={`Aliases (${l}) — read-only here`}
                        values={perLocale[l] ?? []} />
            ))}
            <p style={{ fontSize: 10.5, color: color.muted, margin: "7px 0 0", lineHeight: 1.5 }}>
              editing <b style={{ fontFamily: font.mono }}>{locale}</b> never touches these —
              switch the locale above to edit one of them.
            </p>
          </div>
        )}
        {fld("pattern", (e) => (
          <TextField label="Pattern" testid="pattern" editable={editable} mono reason={lockReason}
                     help="A single regex over the caption. Compiled server-side; a compile error
                           comes back on this control."
                     value={pattern} onChange={(v) => patch({ pattern: v })}
                     monoNote={patternNote} error={e} inherited={inh("pattern", item.pattern)} />
        ))}
        {fld("regex_hints", (e) => (
          <StringListEditor label="Regex hints — positive evidence" testid="regex_hints"
                            editable={editable} variant="mono"
                            help="Patterns that positively evidence a match. Each is compiled; a
                                  refusal names the entry."
                            value={g("regex_hints", item.regex_hints)}
                            onChange={(v) => patch({ regex_hints: v })}
                            error={e} indexErrors={idx("regex_hints")}
                            inherited={inh("regex_hints", item.regex_hints)} />
        ))}
        {fld("keyword_hints", (e) => (
          <StringListEditor label="Keyword hints" testid="keyword_hints" editable={editable}
                            help="Keywords the deterministic tier scores on."
                            value={g("keyword_hints", item.keyword_hints)}
                            onChange={(v) => patch({ keyword_hints: v })}
                            error={e} indexErrors={idx("keyword_hints")}
                            inherited={inh("keyword_hints", item.keyword_hints)} />
        ))}
        {fld("exclude_hints", (e) => (
          <StringListEditor label="Regex VETOES — never match" testid="exclude_hints"
                            editable={editable} variant="veto"
                            help={<><b>Regexes against the RAW caption</b> — a match here refuses
                                  the line outright. Not the same field as “does not count as this
                                  line” above, which is prose shown to the model: a torn pattern
                                  here does not fail loudly, the exclusion simply stops
                                  excluding.</>}
                            value={g("exclude_hints", item.exclude_hints)}
                            onChange={(v) => patch({ exclude_hints: v })}
                            error={e} indexErrors={idx("exclude_hints")}
                            inherited={inh("exclude_hints", item.exclude_hints)} />
        ))}
        {fld("alias_matching", (e) => (
          <SelectField<LineItemAliasMatching>
            label="Alias matching" testid="alias_matching" editable={editable} reason={lockReason}
            options={vocab?.alias_matching ?? []}
            helpOf={(v) => ALIAS_MATCHING_HELP[v]}
            value={g("alias_matching", item.alias_matching)}
            onChange={(v) => v && patch({ alias_matching: v })}
            error={e} inherited={inh("alias_matching", item.alias_matching)} />
        ))}
      </Group>

      {/* ── 3. THE GATE ──────────────────────────────────────────────────────────────────────
          WHERE a caption may be claimed from. Hundreds of the set's normalised captions are
          claimed by more than one line item and some of those claims span different statements,
          so the gate is what settles which line a caption reaches. Nearly all of it arrives by
          INHERITANCE from a `section_defaults` entry — hence the badges. */}
      <Group question="Where may it be claimed from?"
             note="The gate is authored once per section and claimed by `inherits`; editing a
                   gate field here overrides the section for this line only.">
        {fld("inherits", (e) => (
          <SelectField<string>
            label="Inherits its gate from" testid="inherits" editable={editable} nullable
            reason={lockReason} nullLabel="nothing — this line declares its own gate"
            help="Names a `section_defaults` entry, folded in before validation. Repointing an
                  item at the right section is the cheapest correct fix for a mis-gated line."
            options={vocab?.inherits_options ?? []}
            value={g("inherits", item.inherits)} onChange={(v) => patch({ inherits: v })}
            error={e} />
        ))}
        {fld("statement", (e) => (
          <SelectField<StatementToken>
            label="Statement" testid="statement" editable={editable} nullable reason={lockReason}
            nullLabel="any — nothing was said"
            help="The statement a caption must have been printed on for this line to claim it."
            options={vocab?.statements ?? []} labelOf={(s) => STATEMENT_LABEL[s] ?? s}
            value={g("statement", item.statement)} onChange={(v) => patch({ statement: v })}
            error={e} inherited={inh("statement", item.statement)} />
        ))}
        {fld("section_scope", (e) => (
          <StringListEditor label="Section banners it may sit under" testid="section_scope"
                            editable={editable}
                            help="EMPTY MEANS UNCONSTRAINED, and an empty list is stored as
                                  empty. The suggestions are what this set already declares — a
                                  filing printing an undeclared banner is exactly the case you are
                                  here to handle, so anything is accepted."
                            emptyText="Unconstrained — a configured empty, not a default."
                            suggest={vocab?.section_scope_tokens}
                            value={g("section_scope", item.section_scope)}
                            onChange={(v) => patch({ section_scope: v })}
                            error={e} indexErrors={idx("section_scope")}
                            inherited={inh("section_scope", item.section_scope)} />
        ))}
        {fld("scopes", (e) => (
          <OrderedMultiSelect<SearchScope>
            label="Where to look, in search order" testid="scopes" editable={editable}
            help="A search ORDER, not a gate — the first scope that yields a figure is published.
                  `notes` leads by default because for the eight output lines the note is the
                  authoritative source."
            options={vocab?.scopes ?? []} labelOf={(s) => SCOPE_LABEL[s] ?? s}
            addLabel="Add a scope…"
            value={g("scopes", item.scopes)} onChange={(v) => patch({ scopes: v })}
            error={e} inherited={inh("scopes", item.scopes)} />
        ))}
        {fld("side", (e) => (
          <SelectField<LineItemSide>
            label="Asset, liability or equity" testid="side" editable={editable}
            reason={lockReason}
            help="`from_section` reads the side off the banner the caption sits under — and is
                  refused unless this line can reach a section (a balance-sheet statement,
                  `balance_sheet` in the scopes, or a section scope naming a side)."
            options={vocab?.sides ?? []} labelOf={(s) => SIDE_LABEL[s] ?? s}
            value={g("side", item.side)} onChange={(v) => v && patch({ side: v })}
            error={e} inherited={inh("side", item.side)} />
        ))}
        {fld("allow_contra", (e) => (
          <BoolField label="A caption on the OPPOSITE side may fill this line" testid="allow_contra"
                     editable={editable}
                     help="Off by default — a bare “Cash” once resolved to an overdraft. On
                           legitimately for an instrument that appears on both sides."
                     onText="The opposite side may fill this line."
                     offText="A caption printed on the opposite side can never fill this line."
                     value={g("allow_contra", item.allow_contra)}
                     onChange={(v) => patch({ allow_contra: v })}
                     error={e} inherited={inh("allow_contra", item.allow_contra)} />
        ))}
        {fld("match_priority", (e) => (
          <NumberField label="Match priority" testid="match_priority" editable={editable}
                       allowNull reason={lockReason}
                       nullLabel="nothing said" numberLabel="a priority"
                       help="Descending tie-break for the collisions the gate leaves standing."
                       zeroNote="0 is the residual floor — a line at 0 is unreachable by matching
                                 altogether. That is a different assertion from “nothing said”."
                       value={g("match_priority", item.match_priority)}
                       onChange={(v) => patch({ match_priority: v })}
                       error={e} inherited={inh("match_priority", item.match_priority)} />
        ))}
        {fld("extraction_mode", (e) => (
          <SelectField<LineItemExtractionMode>
            label="How the figure is obtained" testid="extraction_mode" editable={editable}
            reason={lockReason}
            help="Only `do_not_extract` suppresses the line. Reading `derive` as “do not extract”
                  refuses a printed row and sweeps it into a residual."
            options={vocab?.extraction_modes ?? []} helpOf={(v) => MODE_HELP[v]}
            value={g("extraction_mode", item.extraction_mode)}
            onChange={(v) => v && patch({ extraction_mode: v })}
            error={e} inherited={inh("extraction_mode", item.extraction_mode)} />
        ))}
        {fld("face_only", (e) => (
          <TriBoolField label="Only readable off the face of a statement" testid="face_only"
                        editable={editable}
                        trueLabel="face only" falseLabel="notes allowed" nullLabel="nothing said"
                        help="Genuinely three-valued: `null` is not `false` — v1 sets never
                              expressed this at all."
                        stateHelp={{
                          yes: "A note may never be the source for this line.",
                          no: "A note may be read for this line, subject to the fields below.",
                          unset: "Nothing was said, which is not the same as “notes allowed”.",
                        }}
                        value={g("face_only", item.face_only)}
                        onChange={(v) => patch({ face_only: v })}
                        error={e} inherited={inh("face_only", item.face_only)} />
        ))}
        {fld("note_use", (e) => (
          <SelectField<LineItemNoteUse>
            label="What a cited note may be" testid="note_use" editable={editable} nullable
            reason={lockReason} nullLabel="nothing said"
            help="Three-valued: “nothing said” is NOT `evidence_only`."
            options={vocab?.note_uses ?? []} helpOf={(v) => NOTE_USE_HELP[v]}
            value={g("note_use", item.note_use)} onChange={(v) => patch({ note_use: v })}
            error={e} inherited={inh("note_use", item.note_use)} />
        ))}

        {/* NOTE SOURCE — the object that REPLACED a hard-coded heading list and a 162-alternative
            regex whitelist that refused a filing writing "Depreciation charge for the year".
            Widening it is the single edit this screen exists to make possible, and it was
            reachable from nowhere. */}
        {fld("note_source", (e) => (
          <BoolField label="Read this line from a NOTE" testid="note_source" editable={editable}
                     help="Which note a sub-line item is read from and which of its rows count.
                           Switching it off writes `null` — no note sourcing for this line."
                     onText="The three pattern groups below decide which note and which of its
                             rows count."
                     offText="No note source declared."
                     value={noteSource !== null}
                     onChange={(v) => patch({ note_source: v ? (item.note_source ?? NEW_NOTE_SOURCE)
                                                              : null })}
                     error={e} inherited={inh("note_source", item.note_source)} />
        ))}
        {noteSource && (
          <div style={{ borderLeft: `2px solid ${color.indigoBorder2}`, paddingLeft: 11,
                         marginBottom: 14 }}>
            {fld("note_source.note_title_any", (e) => (
              <StringListEditor label="Note title matches any of" testid="note_source-note_title_any"
                                editable={editable} variant="mono"
                                help="Regexes over the note's heading."
                                value={noteSource.note_title_any}
                                onChange={(v) => setNoteSource({ note_title_any: v })}
                                error={e} indexErrors={idx("note_source.note_title_any")} />
            ))}
            {fld("note_source.row_caption_any", (e) => (
              <StringListEditor label="Rows that COUNT" testid="note_source-row_caption_any"
                                editable={editable} variant="mono"
                                help="The replacement for the 162-alternative whitelist. Widen it
                                      here instead of shipping a release."
                                value={noteSource.row_caption_any}
                                onChange={(v) => setNoteSource({ row_caption_any: v })}
                                error={e} indexErrors={idx("note_source.row_caption_any")} />
            ))}
            {fld("note_source.row_caption_none", (e) => (
              <StringListEditor label="Rows that must be EXCLUDED"
                                testid="note_source-row_caption_none"
                                editable={editable} variant="veto"
                                help="A veto over the note's rows. A torn pattern here stops
                                      excluding in silence, so each one is compiled."
                                value={noteSource.row_caption_none}
                                onChange={(v) => setNoteSource({ row_caption_none: v })}
                                error={e} indexErrors={idx("note_source.row_caption_none")} />
            ))}
            {fld("note_source.caption_normalization", (e) => (
              <SelectField<CaptionNormalization>
                label="The three groups above are written against"
                testid="note_source-caption_normalization" editable={editable} reason={lockReason}
                help="The shipped patterns were lifted from a matcher that reads RAW captions, so
                      folding them would stop some of them matching."
                options={vocab?.caption_normalizations ?? []}
                labelOf={(v) => (v === "none" ? "raw captions, as printed"
                                              : "normalised text (mapping_v1)")}
                value={noteSource.caption_normalization}
                onChange={(v) => v && setNoteSource({ caption_normalization: v })}
                error={e} />
            ))}
          </div>
        )}
      </Group>

      {/* ── 4. STRUCTURE ─────────────────────────────────────────────────────────────────────
          The tree, and what a parenthood ASSERTS. `rollup` exists because the rollup check would
          otherwise have summed the twelve alternative restatements of the depreciation line. */}
      <Group question="How does it sit among the other lines?">
        {fld("type", (e) => (
          <SelectField<LineItemType>
            label="Type" testid="type" editable={editable} reason={lockReason}
            help="The premise of the whole model: which of the other groups carry meaning at all.
                  A calculated or intermediate line with no terms, and a derived line with neither
                  a cascade nor an `implemented_by`, are both refused."
            options={vocab?.types ?? []} labelOf={(v) => TYPE_TONE[v].label}
            helpOf={(v) => ({
              extracted: "recognised from a printed caption",
              calculated: "summed from the terms below",
              intermediate: "summed from the terms below, and never reaches the output",
              derived: "assembled by the cascade below, or by the service that computes it today",
            } as Record<string, string>)[v]}
            value={type}
            onChange={(v) => {
              if (!v) return;
              patch({ type: v });
              // `_coherent` FORCES `in_output` false for an intermediate. Dropping a drafted
              // `in_output` here rather than sending false keeps the edit honest: the author has
              // not declared anything about the output, the type has.
              if (v === "intermediate") drop("in_output");
            }}
            error={e} />
        ))}
        {fld("in_output", (e) => (
          <BoolField label="Reaches the output template" testid="in_output" editable={editable}
                     help="Whether the line reaches the statement screens and the export."
                     disabledReason={type === "intermediate"
                       ? "an intermediate never reaches the output — forced by the type"
                       : undefined}
                     value={type === "intermediate" ? false : g("in_output", item.in_output)}
                     onChange={(v) => patch({ in_output: v })}
                     error={e} inherited={inh("in_output", item.in_output)} />
        ))}
        {fld("parent", (e) => (
          <KeyPicker label="Part of" testid="parent" editable={editable} reason={lockReason}
                     options={keys} exclude={[item.key]}
                     help="Which line this one is a part of. Clearing it makes this a root line.
                           Self-reference and any cycle are refused."
                     value={g("parent", item.parent) || null}
                     onChange={(v) => patch({ parent: v })}
                     error={e} inherited={inh("parent", item.parent)} />
        ))}
        {fld("rollup", (e) => (
          <SelectField<LineItemRollup>
            label="What the parenthood asserts" testid="rollup" editable={editable}
            reason={lockReason}
            help="Choosing `sum` where the parts are alternative restatements of ONE figure
                  double-counts it."
            options={vocab?.rollups ?? []} helpOf={(v) => ROLLUP_HELP[v]}
            value={g("rollup", item.rollup)} onChange={(v) => v && patch({ rollup: v })}
            error={e} inherited={inh("rollup", item.rollup)} />
        ))}
        {fld("order", (e) => (
          <NumberField label="Display order among siblings" testid="order" editable={editable}
                       reason={lockReason}
                       help="DISPLAY ONLY. It does not affect matching — `match_priority` in the
                             gate above is the matching tie-break."
                       value={g("order", item.order)} onChange={(v) => patch({ order: v ?? 0 })}
                       error={e} />
        ))}
        {fld("namespace", (e) => (
          <SelectField<LineItemNamespace>
            label="Key space" testid="namespace" editable={editable} reason={lockReason}
            help="Flipping to `template` on a key the target template does not declare is refused
                  — and the refusal lands here, not on the key."
            options={vocab?.namespaces ?? []} helpOf={(v) => NAMESPACE_HELP[v]}
            value={g("namespace", item.namespace)} onChange={(v) => v && patch({ namespace: v })}
            error={e} />
        ))}
        {fld("value_scope", (e) => (
          <SelectField<ValueScope>
            label="May it overlap another line?" testid="value_scope" editable={editable}
            reason={lockReason} help="Read by the residual sweep."
            options={vocab?.value_scopes ?? []} helpOf={(v) => VALUE_SCOPE_HELP[v]}
            value={g("value_scope", item.value_scope)}
            onChange={(v) => v && patch({ value_scope: v })}
            error={e} inherited={inh("value_scope", item.value_scope)} />
        ))}
        {fld("is_gross_parent", (e) => (
          <BoolField label="This is a GROSS PARENT of the children it contains"
                     testid="is_gross_parent" editable={editable}
                     help="Without it, caption collisions had no discriminator and both claimants
                           were loaded additively — double-counting a figure the filing printed
                           once."
                     onText="Never loaded additively with the children named below."
                     offText="No containment declared."
                     value={g("is_gross_parent", item.is_gross_parent)}
                     onChange={(v) => patch({ is_gross_parent: v })}
                     error={e} inherited={inh("is_gross_parent", item.is_gross_parent)} />
        ))}
        {fld("children_if_decomposed", (e) => (
          <KeyPicker label="Children this parent already contains" testid="children_if_decomposed"
                     multi editable={editable} reason={lockReason} options={keys}
                     exclude={[item.key]}
                     help="Keys of this set. A pipe-joined string names no key — list each entry
                           separately."
                     value={g("children_if_decomposed", item.children_if_decomposed)}
                     onChange={(v) => patch({ children_if_decomposed: v })}
                     error={e} indexErrors={idx("children_if_decomposed")}
                     inherited={inh("children_if_decomposed", item.children_if_decomposed)} />
        ))}
        {fld("sole_component_of", (e) => (
          <KeyPicker label="Sole component of" testid="sole_component_of" editable={editable}
                     reason={lockReason} options={keys} exclude={[item.key]}
                     help="When the face prints only the subtotal, the whole undifferentiated
                           figure becomes this line — and the inference is refused as soon as any
                           sibling is evidenced."
                     value={g("sole_component_of", item.sole_component_of)}
                     onChange={(v) => patch({ sole_component_of: v })}
                     error={e} inherited={inh("sole_component_of", item.sole_component_of)} />
        ))}
        {fld("expected_components", (e) => (
          <KeyPicker label="Components expected before it sweeps" testid="expected_components"
                     multi editable={editable} reason={lockReason} options={keys}
                     help="The keys a residual or parent expects to be evidenced. A typo here
                           silently disables the expectation, so entries are picked, not typed."
                     value={g("expected_components", item.expected_components)}
                     onChange={(v) => patch({ expected_components: v })}
                     error={e} indexErrors={idx("expected_components")}
                     inherited={inh("expected_components", item.expected_components)} />
        ))}
        {fld("never_sweep", (e) => (
          <KeyPicker label="Never absorb these keys" testid="never_sweep" multi
                     editable={editable} reason={lockReason} options={keys}
                     help="A prohibition. One that names a non-key cannot be told apart from a
                           filing that never triggered it."
                     value={g("never_sweep", item.never_sweep)}
                     onChange={(v) => patch({ never_sweep: v })}
                     error={e} indexErrors={idx("never_sweep")}
                     inherited={inh("never_sweep", item.never_sweep)} />
        ))}

        {/* RESIDUAL POLICY — a bucket's own copy of the sweep terms it is populated under.
            `section_scope` inside it is the one term that is not global, so the object cannot be
            authored anywhere but per item. */}
        {fld("residual_policy", (e) => (
          <BoolField label="This line is a RESIDUAL BUCKET" testid="residual_policy"
                     editable={editable}
                     help="Its own copy of the sweep terms it is populated under. Switching it off
                           writes `null` — no policy."
                     onText="Populated by the sweep, under the terms below."
                     offText="No residual policy declared."
                     value={residual !== null}
                     onChange={(v) => patch({
                       residual_policy: v ? (item.residual_policy ?? NEW_RESIDUAL_POLICY) : null,
                     })}
                     error={e} inherited={inh("residual_policy", item.residual_policy)} />
        ))}
        {residual && (
          <div style={{ borderLeft: `2px solid ${color.indigoBorder2}`, paddingLeft: 11,
                         marginBottom: 14 }}>
            {fld("residual_policy.framework", (e) => (
              <TextField label="Framework" testid="residual_policy-framework" editable={editable}
                         mono reason={lockReason}
                         help="Which global residual framework block this policy repeats. A free
                               string on the model, so the list is a suggestion."
                         datalist={vocab?.residual_frameworks}
                         value={residual.framework}
                         onChange={(v) => setResidual({ framework: v })} error={e} />
            ))}
            {fld("residual_policy.section_scope", (e) => (
              <TextField label="The one section it may not leave"
                         testid="residual_policy-section_scope" editable={editable} mono
                         reason={lockReason}
                         help="What makes “may not cross a section” mean anything. Per-item by
                               necessity — a global value cannot express it."
                         datalist={vocab?.section_scope_tokens}
                         value={residual.section_scope}
                         onChange={(v) => setResidual({ section_scope: v })} error={e} />
            ))}
            {fld("residual_policy.population", (e) => (
              <TextField label="How the bucket is filled" testid="residual_policy-population"
                         editable={editable} mono reason={lockReason}
                         help="Sweep only, versus anything else declared."
                         datalist={vocab?.residual_populations}
                         value={residual.population}
                         onChange={(v) => setResidual({ population: v })} error={e} />
            ))}
            {fld("residual_policy.cross_section", (e) => (
              <BoolField label="May pull rows from OUTSIDE that section"
                         testid="residual_policy-cross_section" editable={editable}
                         value={residual.cross_section}
                         onChange={(v) => setResidual({ cross_section: v })}
                         onText="The sweep may reach outside the section above."
                         offText="The sweep is confined to the section above."
                         error={e} />
            ))}
            {fld("residual_policy.notes_as_source", (e) => (
              <BoolField label="Note rows may fill it, not only face rows"
                         testid="residual_policy-notes_as_source" editable={editable}
                         value={residual.notes_as_source}
                         onChange={(v) => setResidual({ notes_as_source: v })} error={e} />
            ))}
            {fld("residual_policy.plug", (e) => (
              <BoolField label="May be computed as a PLUG" testid="residual_policy-plug"
                         editable={editable} tone="warn"
                         help="(reported subtotal − mapped children)."
                         onText="A plug ALWAYS ties, so it hides the mapping gap it papers over."
                         offText="Never computed as the difference — a gap stays visible as a gap."
                         value={residual.plug} onChange={(v) => setResidual({ plug: v })}
                         error={e} />
            ))}
            {fld("residual_policy.itemise", (e) => (
              <BoolField label="Itemise what was swept" testid="residual_policy-itemise"
                         editable={editable}
                         help="Decides whether a reviewer can see what was swept, or only one
                               collapsed figure."
                         value={residual.itemise} onChange={(v) => setResidual({ itemise: v })}
                         error={e} />
            ))}
          </div>
        )}
      </Group>

      {/* ── 5. MEASUREMENT ───────────────────────────────────────────────────────────────────
          THE TWO SIGN FIELDS ARE LABELLED APART, because they are two questions and one name for
          both is how an author changes the wrong one: `sign_convention` on the item is the sign
          the line is EXPECTED to carry (a review trigger, sent as `sign_expectation`), and
          `sign_rule.convention` is how a value is NORMALISED. */}
      <Group question="What kind of figure is it?">
        {fld("temporality", (e) => (
          <SelectField<LineItemTemporality>
            label="Instant or duration" testid="temporality" editable={editable} nullable
            reason={lockReason} help="The line's identity as a measurement; validation reads it."
            options={vocab?.temporalities ?? []}
            value={g("temporality", item.temporality)} onChange={(v) => patch({ temporality: v })}
            error={e} inherited={inh("temporality", item.temporality)} />
        ))}
        {fld("unit_of_account", (e) => (
          <SelectField<LineItemUnitOfAccount>
            label="Balance, flow or subtotal" testid="unit_of_account" editable={editable} nullable
            reason={lockReason} options={vocab?.units_of_account ?? []}
            value={g("unit_of_account", item.unit_of_account)}
            onChange={(v) => patch({ unit_of_account: v })}
            error={e} inherited={inh("unit_of_account", item.unit_of_account)} />
        ))}
        {fld("sign_expectation", (e) => (
          <SelectField<SignExpectation>
            label="Expected sign — a review trigger" testid="sign_expectation" editable={editable}
            nullable reason={lockReason}
            help="The sign this line is EXPECTED to carry. Never a transformation: a value against
                  the expectation is flagged for review, not flipped. This is the item's own
                  `sign_convention`, sent as `sign_expectation`."
            options={vocab?.sign_expectations ?? []}
            value={g("sign_expectation", item.sign_convention)}
            onChange={(v) => patch({ sign_expectation: v })}
            error={e} inherited={inh("sign_convention", item.sign_convention)} />
        ))}
        {fld("sign_rule.convention", (e) => (
          <SelectField<SignRuleConvention>
            label="How the value is NORMALISED" testid="sign_rule-convention" editable={editable}
            nullable reason={lockReason} nullLabel="no sign rule at all"
            help="The convention a figure is stored under. Clearing it removes the whole sign rule
                  — including the label regexes below."
            options={vocab?.sign_conventions ?? []}
            value={signRule?.convention ?? null}
            onChange={(v) => patch({
              // The rule is nullable AS AN OBJECT and the convention is its required half, so the
              // convention IS the on/off switch: no convention means no normalisation declared.
              sign_rule: v === null ? null
                                    : { convention: v,
                                        flip_if_label_matches: signRule?.flip_if_label_matches ?? [] },
            })}
            error={e} inherited={inh("sign_rule", item.sign_rule)} />
        ))}
        {fld("sign_rule.flip_if_label_matches", (e) => (
          <StringListEditor label="Flip the sign when the printed label matches"
                            testid="sign_rule-flip_if_label_matches"
                            editable={editable && !!signRule} variant="mono"
                            help={signRule
                              ? "A silent sign inversion is one of the most expensive errors on a"
                                + " statement, and this is the only field that causes one."
                              : "Choose a normalisation convention above first — these regexes"
                                + " live inside the sign rule."}
                            value={signRule?.flip_if_label_matches ?? []}
                            onChange={(v) => patch({
                              sign_rule: { convention: signRule?.convention ?? "natural",
                                           flip_if_label_matches: v },
                            })}
                            error={e} indexErrors={idx("sign_rule.flip_if_label_matches")} />
        ))}
        {fld("analyst_bucket", (e) => (
          <SelectField<string>
            label="Analyst section for its rows" testid="analyst_bucket" editable={editable}
            nullable reason={lockReason} nullLabel="nothing said — read from the printed section"
            help="Used when the printed section cannot say — the interest case, which no statement
                  prints a section for. A value naming no section loses this line's rows to Others
                  with nothing saying why, so the options are the ones the gate accepts."
            options={vocab?.analyst_buckets ?? []}
            value={g("analyst_bucket", item.analyst_bucket)}
            onChange={(v) => patch({ analyst_bucket: v })}
            error={e} inherited={inh("analyst_bucket", item.analyst_bucket)} />
        ))}
      </Group>

      {/* ── 6. ASSEMBLY ──────────────────────────────────────────────────────────────────────
          BOTH BLOCKS STAY ON SCREEN, the current type's expanded and the other collapsed. Hiding
          the one that does not apply is how a type change makes a group vanish and an author
          concludes the field was taken away — and both are needed while a line is being moved
          from one type to the other. */}
      <Group question="How is its value assembled?"
             note="A calculated or intermediate line is a signed sum of terms; a derived line is
                   an ordered cascade of attempts, the first that resolves winning.">
        <details open={type === "calculated" || type === "intermediate"}>
          <summary style={{ cursor: "pointer", fontSize: 11.5, fontWeight: 600,
                             color: color.ink2, marginBottom: 9 }}>
            The signed sum — for a <b>calculated</b> or <b>intermediate</b> line
          </summary>
          {fld("terms", (e) => (
            <TermRows label="Terms" testid="terms" editable={editable}
                      help="Every `ref` is checked against this set's keys, so a typo is refused
                            rather than being a term that silently contributes nothing."
                      terms={g("terms", item.terms)} onChange={(v) => patch({ terms: v })}
                      options={keys} error={e}
                      // The server attributes a bad term ref to `terms` AND the term's index; the
                      // message names the ref, so it lands on that row's line picker.
                      errorAt={(i, f) => (f === "ref" ? idx("terms")?.[i] : undefined)} />
          ))}
        </details>
        <details open={type === "derived"} style={{ marginTop: 12 }}>
          <summary style={{ cursor: "pointer", fontSize: 11.5, fontWeight: 600,
                             color: color.ink2, marginBottom: 9 }}>
            The priority cascade — for a <b>derived</b> line
          </summary>
          {fld("cascade", (e) => (
            <RungCards label="Rungs, tried in order" testid="cascade" editable={editable}
                       help="The ORDER IS the priority. A derived line needs either a cascade or an
                             `implemented_by` below."
                       cascade={g("cascade", item.cascade)} onChange={(v) => patch({ cascade: v })}
                       options={keys} error={e}
                       // Rung-level attribution is by rung index (the server names the offending
                       // ref in the message), so it is shown on that rung's line pickers.
                       termErrorAt={(rung, _term, f) =>
                         (f === "ref" ? idx("cascade")?.[rung] : undefined)} />
          ))}
          {fld("implemented_by", (e) => (
            <TextField label="Computed today by" testid="implemented_by" editable={editable} mono
                       reason={lockReason}
                       help="The service that computes this derivation while the configuration only
                             describes it. Clearing it hands the derivation over to the cascade
                             above — and a derived line with neither is refused."
                       value={g("implemented_by", item.implemented_by)}
                       onChange={(v) => patch({ implemented_by: v })} error={e} />
          ))}
        </details>
      </Group>

      {/* ── 7. PROSE ─────────────────────────────────────────────────────────────────────────
          Nullable prose: an emptied box yields `null` ("nothing was said"), not `""`. These
          document decisions someone will otherwise re-litigate. */}
      <Group question="Notes for the next reader"
             note="Free prose. Nothing matches on these — they record a decision.">
        {fld("decomposition_rule", (e) => (
          <TextArea label="How a combined parent decomposes" testid="decomposition_rule"
                    editable={editable} rows={2} nullable reason={lockReason}
                    value={g("decomposition_rule", item.decomposition_rule)}
                    onChange={(v) => patch({ decomposition_rule: v })} error={e}
                    inherited={inh("decomposition_rule", item.decomposition_rule)} />
        ))}
        {fld("others_rule", (e) => (
          <TextArea label="What this line's “Others” remainder may contain" testid="others_rule"
                    editable={editable} rows={2} nullable reason={lockReason}
                    value={g("others_rule", item.others_rule)}
                    onChange={(v) => patch({ others_rule: v })} error={e}
                    inherited={inh("others_rule", item.others_rule)} />
        ))}
        {fld("derivation", (e) => (
          <TextArea label="How the figure is derived, in words" testid="derivation"
                    editable={editable} rows={2} nullable reason={lockReason}
                    value={g("derivation", item.derivation)}
                    onChange={(v) => patch({ derivation: v })} error={e}
                    inherited={inh("derivation", item.derivation)} />
        ))}
        {fld("aggregation_note", (e) => (
          <TextArea label="How contributing rows are aggregated" testid="aggregation_note"
                    editable={editable} rows={2} nullable reason={lockReason}
                    value={g("aggregation_note", item.aggregation_note)}
                    onChange={(v) => patch({ aggregation_note: v })} error={e}
                    inherited={inh("aggregation_note", item.aggregation_note)} />
        ))}
        {fld("template_note", (e) => (
          <TextArea label="Its relationship to the output template" testid="template_note"
                    editable={editable} rows={2} nullable reason={lockReason}
                    value={g("template_note", item.template_note)}
                    onChange={(v) => patch({ template_note: v })} error={e}
                    inherited={inh("template_note", item.template_note)} />
        ))}
        {fld("notes_as_source_rationale", (e) => (
          <TextArea label="Why a note may be the authoritative source" testid="notes_as_source_rationale"
                    editable={editable} rows={2} nullable reason={lockReason}
                    value={g("notes_as_source_rationale", item.notes_as_source_rationale)}
                    onChange={(v) => patch({ notes_as_source_rationale: v })} error={e}
                    inherited={inh("notes_as_source_rationale", item.notes_as_source_rationale)} />
        ))}
      </Group>

      {/* ── 8. LOCKED ────────────────────────────────────────────────────────────────────────
          NOT AUTHORABLE ≠ ABSENT. The entries and their reasons come from
          `vocab.not_editable` — served rather than restated here, so a field cannot quietly
          disappear from the form with no reason attached, and so a reason this screen does not own
          has exactly one spelling. */}
      <Group question="Not editable here"
             note="Withheld on purpose, each with the reason. Being silently absent and being
                   read-only for a reason look identical on a screen, and only one of them is a
                   decision.">
        {Object.entries(vocab?.not_editable ?? {}).map(([field, reason]) => {
          // The VALUE for each locked field. `children` carries navigation, because the edit that
          // moves a child is possible — on the child's own detail, through `parent`.
          let value: ReactNode;
          if (field === "key") value = item.key;
          else if (field === "children") {
            value = item.children.length === 0 ? undefined : (
              <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {item.children.map((c) => (
                  <span key={c.key} role="button" tabIndex={0}
                        data-testid={`li-child-${c.key}`}
                        onClick={() => p.onSelect(c.key)}
                        onKeyDown={(ev) => {
                          if (ev.key === "Enter" || ev.key === " ") {
                            ev.preventDefault(); p.onSelect(c.key);
                          }
                        }}
                        style={{ fontSize: 11, fontFamily: font.sans, cursor: "pointer",
                                  color: color.indigo, background: color.indigoTint2,
                                  borderRadius: radius.chip, padding: "2px 7px" }}>
                    {c.label || c.key} →
                  </span>
                ))}
              </span>
            );
          } else if (field === "aliases_i18n") {
            const locales = Object.keys(perLocale);
            value = locales.length === 0 ? undefined
              : `${locales.map((l) => `${l} ${(perLocale[l] ?? []).length}`).join(" · ")}`
                + " — switch the editing locale above to change one of them";
          } else if (field === "min_confidence_to_auto_accept") {
            // Withdrawn, so the server does not send it at all. Naming it here is the point: it
            // was removed deliberately ("remove line item level control for now") and its absence
            // has to read as a decision rather than as an oversight.
            value = <span style={{ fontFamily: font.sans, color: color.muted }}>not served</span>;
          } else {
            value = undefined;
          }
          return <LockedRow key={field} label={field} testid={field} value={value}
                            reason={reason} />;
        })}
      </Group>

      {/* ── THE SAVE BAR ─────────────────────────────────────────────────────────────────────
          Sticky at the foot of the pane the moment anything differs from what the server served.
          It names the count, the fields, and what the save DOES — publish a new version, which is
          the version the next run pins. A save that silently does nothing is the defect this whole
          change is fixing, so the button says what will happen and the confirmation says what
          did. */}
      {p.changed.length > 0 && (
        <div style={{ position: "sticky", bottom: 0, background: color.surface,
                       borderTop: `1px solid ${color.cardBorder}`, padding: "11px 0 2px",
                       marginTop: 4 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap",
                         marginBottom: 8 }}>
            <b data-testid="li-dirty-count" style={{ fontSize: 12 }}>
              {p.changed.length} field{p.changed.length === 1 ? "" : "s"} changed
            </b>
            <span style={{ fontSize: 11, color: color.sec2, fontFamily: font.mono,
                            minWidth: 0, wordBreak: "break-word" }}>
              {p.summary}
            </span>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <Button variant="secondary" testid="li-discard" onClick={p.onDiscard}
                    disabled={p.saving}>
              Discard
            </Button>
            <Button testid="li-save" onClick={p.onSave} disabled={!editable || p.saving}
                    title={lockReason}>
              {p.saving
                ? "Publishing…"
                : `Save — publishes v${(p.versionNumber ?? 0) + 1}, which becomes the version the`
                  + " next run pins"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function LineItemsScreen() {
  const t = useT();
  const q = useLineItems();
  const canEdit = useCan("config:line_items");
  const save = useEditLineItemConfig();
  // Add and delete publish a new version exactly as an edit does — see `useAddLineItem`.
  const add = useAddLineItem();
  const remove = useDeleteLineItem();
  const [adding, setAdding] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newInherits, setNewInherits] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  // SEARCH AND TYPE FILTER. 475 items in a tree is not browsable: finding one meant scrolling, and
  // the detail pane rendered at the TOP of a column as tall as the list, so clicking a line near
  // the bottom put its detail far above the reader's scroll position — it looked like nothing had
  // happened. The filter narrows the list and the panes below scroll independently, which is the
  // structural half of the same fix.
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<LineItemType | null>(null);

  // THE EDIT STATE. `draft` holds only the fields the author has TOUCHED — an untouched field is
  // never sent, because sending a field the item never declared turns an inherited value into a
  // declared one and silently detaches the item from its section.
  const [draft, setDraft] = useState<Partial<LineItemEdit>>({});
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [indexErrors, setIndexErrors] = useState<Record<string, Record<number, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  // Null until the author chooses, so the set's own default locale is what is edited on arrival.
  const [localeSel, setLocaleSel] = useState<string | null>(null);
  // THE ALIAS INPUT'S DRAFT, LIFTED. The Template screen learned this the hard way: a half-typed
  // alias committed on blur is lost when the author types it and clicks Save directly, because the
  // click can land before the blur. The save bar is in the same pane here, so the draft is held
  // where the payload is built and folded in at save time.
  const [aliasDraft, setAliasDraft] = useState("");

  if (q.isError) {
    return (
      <div style={{ padding: "28px 32px", maxWidth: 820 }}>
        <Card>
          <div data-testid="line-items-error" style={{ fontSize: 12.5 }}>
            The line-item configuration could not be loaded.{" "}
            <span style={{ fontFamily: font.mono, fontSize: 11, color: color.sec2 }}>
              {(q.error as Error)?.message}
            </span>
          </div>
        </Card>
      </div>
    );
  }
  if (!q.data) {
    return (
      <div style={{ padding: "28px 32px" }}>
        <Card><div style={{ fontSize: 12.5, color: color.muted }}>{t("common.loading")}</div></Card>
      </div>
    );
  }

  const { items, counts, problems, valid, set, vocab } = q.data;
  // WHICH STORED VERSION THIS IS. `GET /line-items` reads a `line_item_versions` row and names it
  // (`routes/line_items.py::_version_identity`), so the screen can caption the definitions with the
  // version a run would pin — and, after a save, with the version the save published. Read
  // defensively: a server that does not state it leaves the caption off rather than inventing one,
  // because a configured-empty identity means nothing, never "assume the latest".
  const inForce = q.data.version;
  const locale = localeSel ?? set.locale;
  const flat: LineItemDef[] = [];
  const walk = (xs: LineItemDef[]) => xs.forEach((x) => { flat.push(x); walk(x.children); });
  walk(items);
  const byKey = new Map(flat.map((d) => [d.key, d]));
  // Every key of this set, as the pickers offer them: `parent`, `confusable_with`, `terms[].ref`
  // and the rest all name one, and the publish gate refuses a name that is not one.
  const keyOptions: KeyOption[] = flat.map((d) => ({ key: d.key, label: d.label }));

  // Where each item sits, so a filtered hit can show its parent — a child key on its own ("P2")
  // says nothing about which line it belongs to, and the tree indentation that used to convey it
  // is gone while filtering.
  const parentOf = new Map<string, string>();
  const mapParents = (xs: LineItemDef[], parent: string | null) => xs.forEach((x) => {
    if (parent) parentOf.set(x.key, parent);
    mapParents(x.children, x.key);
  });
  mapParents(items, null);

  const needle = query.trim().toLowerCase();
  // Matched over everything a configurator would search BY: the key and label, plus every alias in
  // every locale and the hint patterns — searching only labels would miss the Chinese caption that
  // is the reason to open the line at all.
  const hay = (d: LineItemDef) => [
    d.key, d.label, d.definition,
    ...(d.aliases ?? []),
    ...Object.values(d.aliases_i18n ?? {}).flat(),
    ...(d.keyword_hints ?? []),
  ].filter(Boolean).join("  ").toLowerCase();
  const hit = (d: LineItemDef) =>
    (!typeFilter || d.type === typeFilter) && (!needle || hay(d).includes(needle));

  const filtering = !!needle || !!typeFilter;
  const hits = filtering ? flat.filter(hit) : [];
  // While filtering, the selection must be one of the visible rows — otherwise the detail pane
  // shows a line the list no longer offers, which is the same disorientation in reverse.
  const selected = filtering
    ? ((sel && hits.some((d) => d.key === sel) && byKey.get(sel)) || hits[0])
    : ((sel && byKey.get(sel)) || items[0]);

  /* ── the draft, measured against what the server served ─────────────────────────────────── */

  // WHICH DRAFTED FIELDS ACTUALLY DIFFER. Deep, and per field: a control the author clicked into
  // and left as it was must not be part of the payload (see `deepEqual`), and an alias list is
  // compared against THIS LOCALE's list.
  const changed: (keyof LineItemEdit)[] = selected
    ? (Object.keys(draft) as (keyof LineItemEdit)[]).filter(
        (f) => !deepEqual(draft[f], baselineOf(selected, set, locale, f)))
    : [];

  /** The one-line summary in the save bar. Aliases are summarised as a delta rather than listed:
   *  "+2 −1 (zh)" says what changed and in WHICH locale, which is the half of the alias contract
   *  an author has to be able to see before they publish. */
  const summary = changed.map((f) => {
    if (f !== "aliases") return f;
    const base = aliasesFor(selected!, set, locale);
    const next = (draft.aliases ?? []) as string[];
    const added = next.filter((a) => !base.includes(a)).length;
    const removed = base.filter((a) => !next.includes(a)).length;
    return `aliases +${added} −${removed} (${locale})`;
  }).join(", ");

  const resetDraft = () => {
    setDraft({});
    setServerErrors({});
    setIndexErrors({});
    setFormError(null);
    setAliasDraft("");
  };

  /** Selecting another line. Guarded when dirty: the draft belongs to ONE item (it is keyed by
   *  nothing but the selection), so carrying it across would apply an edit authored for one line
   *  to another. */
  const chooseKey = (key: string) => {
    if (selected?.key === key) return;
    if (changed.length > 0
        && !window.confirm(
             `${changed.length} unsaved change(s) to “${selected?.label || selected?.key}”.`
             + " Discard them?")) {
      return;
    }
    resetDraft();
    setSaved(null);
    setSel(key);
  };

  /** A field enters the draft the moment it is touched, and its own refusal is cleared with it —
   *  a red sentence that survives the edit that fixed it is a screen arguing with the author. */
  const patch = (p: Partial<LineItemEdit>) => {
    setDraft((d) => ({ ...d, ...p }));
    const touched = Object.keys(p);
    const stale = (field: string) =>
      touched.some((k) => field === k || field.startsWith(`${k}.`));
    setServerErrors((prev) => {
      const next: Record<string, string> = {};
      for (const [k, v] of Object.entries(prev)) if (!stale(k)) next[k] = v;
      return next;
    });
    setIndexErrors((prev) => {
      const next: Record<string, Record<number, string>> = {};
      for (const [k, v] of Object.entries(prev)) if (!stale(k)) next[k] = v;
      return next;
    });
  };

  /** Un-touch a field — used where one control's value makes another's meaningless (`in_output`
   *  under `type: intermediate`). Dropping it is not the same as sending the forced value: the
   *  author has declared nothing about it, and only the type has. */
  const drop = (field: keyof LineItemEdit) =>
    setDraft((d) => {
      const next = { ...d };
      delete next[field];
      return next;
    });

  const onLocale = (next: string) => {
    if (next === locale) return;
    // THE ALIAS DRAFT IS SCOPED TO THE LOCALE IT WAS TYPED IN. Carrying it across would write one
    // locale's captions into another's list — exactly the clobbering the locale-scoped contract
    // exists to prevent — so it is dropped, with the author told.
    if ("aliases" in draft
        && !window.confirm(`Unsaved alias changes for “${locale}” will be discarded.`
                           + ` Switch to “${next}”?`)) {
      return;
    }
    if ("aliases" in draft) drop("aliases");
    setAliasDraft("");
    setServerErrors((prev) => {
      const { aliases: _drop, ...rest } = prev;
      return rest;
    });
    setLocaleSel(next);
  };

  const onSave = () => {
    if (!selected || !inForce?.id) return;
    // THE PAYLOAD IS THE CHANGED FIELDS ONLY, plus the selector and the locale the aliases belong
    // to. `key` is not editable — it is the endpoint's own selector — and `locale` says which
    // alias list `aliases` replaces.
    const edit: LineItemEdit = { key: selected.key, locale };
    const body = edit as unknown as Record<string, unknown>;
    for (const f of changed) body[f] = draft[f];
    // Fold in a half-typed alias so a click on Save does not lose it (see `aliasDraft`).
    const pendingAlias = aliasDraft.trim();
    if (pendingAlias) {
      const current = (("aliases" in draft ? draft.aliases : aliasesFor(selected, set, locale))
                       ?? []) as string[];
      if (!current.includes(pendingAlias)) body.aliases = [...current, pendingAlias];
    }
    setServerErrors({});
    setIndexErrors({});
    setFormError(null);
    save.mutate({ lineItemVersionId: inForce.id, edit }, {
      onSuccess: (result) => {
        // The draft is cleared only on success. `["line-items"]` has been invalidated by the
        // mutation, so the caption above the list re-renders naming the version just published.
        resetDraft();
        setSaved(result.version);
      },
      onError: (err) => {
        // A REFUSAL IS INFORMATION, NOT AN ERROR TO SWALLOW. The endpoint re-validates the whole
        // edited set against the target template before it publishes, and answers with each field
        // that has to change and the server's own sentence about it. Anything it did not attribute
        // to a field belongs to the set rather than to a control and goes to the banner.
        const fields = err instanceof ApiError ? err.fields : undefined;
        const byField: Record<string, string> = {};
        const byIndex: Record<string, Record<number, string>> = {};
        const loose: string[] = [];
        for (const e of fields ?? []) {
          if (!e.field) { loose.push(e.message); continue; }
          const message = e.index === null || e.index === undefined
            ? e.message
            : `${e.message} (entry ${e.index + 1})`;
          byField[e.field] = byField[e.field] ? `${byField[e.field]} · ${message}` : message;
          if (e.index !== null && e.index !== undefined) {
            byIndex[e.field] = { ...(byIndex[e.field] ?? {}), [e.index]: e.message };
          }
        }
        setServerErrors(byField);
        setIndexErrors(byIndex);
        // The server's summary sentence, plus anything unattributed. `refusalText` prefers the
        // structured `detail.message`; a `note_source` pattern that does not compile is refused by
        // the model itself and comes back as FastAPI's own array-shaped 422, which has no
        // per-field list at all — so the status line with the body is what there is to show, and
        // showing it is better than "Error: 422".
        const head = refusalText(err) || "The configuration was refused.";
        setFormError([head, ...loose].join(" "));
      },
    });
  };

  const row = (d: LineItemDef, depth: number) => (
    <div key={d.key}>
      <div role="button" tabIndex={0} data-testid={`li-row-${d.key}`}
           aria-current={d.key === selected?.key}
           onClick={() => chooseKey(d.key)}
           onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); chooseKey(d.key); } }}
           style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 10,
                     alignItems: "center", padding: "8px 10px", cursor: "pointer",
                     borderRadius: 7, marginLeft: depth * 16,
                     background: d.key === selected?.key ? color.indigoTint2 : "transparent",
                     border: `1px solid ${d.key === selected?.key ? color.indigoBorder
                                                                  : "transparent"}` }}>
        <span style={{ minWidth: 0 }}>
          <span style={{ fontSize: depth ? 12 : 12.5, color: depth ? color.sec : color.ink,
                          display: "block", overflow: "hidden", textOverflow: "ellipsis",
                          whiteSpace: "nowrap" }}>
            {d.label || d.key}
          </span>
          <span style={{ fontFamily: font.mono, fontSize: 9.5, color: color.faint,
                          display: "block", overflow: "hidden", textOverflow: "ellipsis",
                          whiteSpace: "nowrap" }}>{d.key}</span>
        </span>
        <Tag type={d.type} />
        <span style={{ width: 16, textAlign: "center", fontSize: 11, fontWeight: 700,
                        color: d.in_output ? color.indigo : color.faint }}>
          {d.in_output ? "✓" : "–"}
        </span>
      </div>
      {d.children.map((k) => row(k, depth + 1))}
    </div>
  );

  return (
    <div style={{ padding: "28px 32px", maxWidth: 1320 }}>
      <div style={{ marginBottom: 6 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between",
                      gap: 16, flexWrap: "wrap" }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 4px" }}>Line items</h1>
          {/* THE TEMPLATE IS REACHED FROM HERE, not from a nav entry of its own. It used to sit in
              the rail as "Template & Line Items" directly above this screen, which read as two
              configuration masters for one engine. Its own job — which lines the output has, and
              the per-line alias and sign editor over them — is still needed, so the destination
              stays and this is the way in. */}
          <a href={SCREENS.template.path}
             style={{ fontSize: 12, color: color.indigo, textDecoration: "none",
                      border: `1px solid ${color.indigoBorder2}`, borderRadius: radius.control,
                      padding: "4px 10px", whiteSpace: "nowrap" }}>
            {SCREENS.template.icon} Output template &amp; per-line editor →
          </a>
        </div>
        <p style={{ margin: 0, color: color.sec2, fontSize: 12.5 }}>
          The lines that reach the output template, and the parts each is assembled from. Each
          line's <b>type</b> decides what it needs. This is the single configuration the pipeline
          reads — and it is edited here.
        </p>
        {inForce && (
          <p data-testid="li-in-force" style={{ margin: "5px 0 0", fontSize: 11.5,
                                                 color: color.muted }}>
            Showing{" "}
            <b style={{ fontFamily: font.mono, color: color.sec2 }}>
              {inForce.line_items_key} v{inForce.version}
            </b>
            {" — the stored version in force, the one the next run pins. A save publishes the"}
            {" next one."}
          </p>
        )}
      </div>

      {/* ADD A LINE ITEM. The template provisions an item for every line it carries, and an author
          adds beyond that set — so this creates an `internal` item, which is the only namespace a
          request may ask for. `in_output` starts FALSE server-side: a new line is not part of the
          deliverable until somebody says so. Configuration then happens through the pane on the
          right, which is the one place that validates each field and attributes a refusal. */}
      {canEdit && inForce?.id && (
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap",
                       marginBottom: 10 }}>
          {!adding
            ? (
              <button onClick={() => setAdding(true)} data-testid="li-add-open"
                      style={{ fontSize: 12, cursor: "pointer", padding: "6px 12px",
                                borderRadius: radius.control, background: color.indigo,
                                color: "#fff", border: 0 }}>
                + Add line item
              </button>
            ) : (
              <div style={{ display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap",
                             padding: 10, border: `1px solid ${color.cardBorder}`,
                             borderRadius: radius.card, background: color.surface }}>
                <input autoFocus value={newKey} onChange={(e) => setNewKey(e.target.value)}
                       data-testid="li-add-key" placeholder="key (e.g. bs_ca__my_line)"
                       style={{ fontSize: 12, padding: "6px 9px", width: 230,
                                 fontFamily: font.mono, borderRadius: radius.control,
                                 border: `1px solid ${color.cardBorder}` }} />
                <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)}
                       data-testid="li-add-label" placeholder="label"
                       style={{ fontSize: 12, padding: "6px 9px", width: 200,
                                 borderRadius: radius.control,
                                 border: `1px solid ${color.cardBorder}` }} />
                {/* `inherits` is what gives the item a section gate. Offered as a picker over the
                    sections that EXIST, because a dangling value is the one configuration failure
                    that is silent — the item validates and simply carries no gate, so nothing can
                    place it. */}
                <select value={newInherits} onChange={(e) => setNewInherits(e.target.value)}
                        data-testid="li-add-inherits"
                        style={{ fontSize: 12, padding: "6px 9px", borderRadius: radius.control,
                                  border: `1px solid ${color.cardBorder}` }}>
                  <option value="">section… (optional)</option>
                  {Object.keys(set.section_defaults).sort().map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                <button data-testid="li-add-save" disabled={!newKey.trim() || add.isPending}
                        onClick={() => add.mutate(
                          { lineItemVersionId: inForce.id,
                            item: { key: newKey.trim(), label: newLabel.trim() || undefined,
                                    inherits: newInherits || undefined } },
                          { onSuccess: (r) => {
                              setAdding(false); setNewKey(""); setNewLabel(""); setNewInherits("");
                              setSel(r.key);          // land the author on what they just made
                            } })}
                        style={{ fontSize: 12, cursor: "pointer", padding: "6px 12px",
                                  borderRadius: radius.control, border: 0, color: "#fff",
                                  background: newKey.trim() ? color.indigo : color.muted2 }}>
                  {add.isPending ? "Adding…" : "Add"}
                </button>
                <button onClick={() => { setAdding(false); add.reset(); }}
                        style={{ fontSize: 12, cursor: "pointer", padding: "6px 10px",
                                  borderRadius: radius.control, background: "none",
                                  border: `1px solid ${color.cardBorder}`, color: color.sec2 }}>
                  Cancel
                </button>
                {add.error && (
                  <div data-testid="li-add-error"
                       style={{ fontSize: 11.5, color: color.redFg, flexBasis: "100%" }}>
                    {refusalText(add.error)}
                  </div>
                )}
              </div>
            )}
        </div>
      )}

      {/* A configuration that does not load is the one thing this screen must not hide. */}
      {!valid && (
        <div style={{ background: color.redBg, color: color.redFg, borderRadius: 8,
                       padding: "9px 12px", fontSize: 12, margin: "10px 0" }}>
          <b>This configuration will not load.</b>{" "}
          {problems.filter((p) => p.severity === "error").length} error(s) —{" "}
          {problems.filter((p) => p.severity === "error")[0]?.message}
        </div>
      )}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", margin: "12px 0 16px",
                     fontSize: 11.5, color: color.sec2 }}>
        <span><b style={{ fontFamily: font.mono }}>{counts.output}</b> output lines</span>
        <span><b style={{ fontFamily: font.mono }}>{counts.sub_line_items}</b> sub-line items</span>
        {/* THE PER-TYPE COUNTS MOVED into the filter row below, where they are the filter chips.
            They read the same numbers off `counts.by_type`; keeping a second, read-only copy here
            put the same four figures on screen twice, one of them clickable and one not, which is
            the kind of thing a reader has to test by clicking to understand. */}
        <span title="how many resolved a statement gate, and how many got it from a section
rather than declaring it themselves">
          <b style={{ fontFamily: font.mono }}>{counts.gated}</b> gated
          {counts.inherited ? <> · <b style={{ fontFamily: font.mono }}>{counts.inherited}</b> inherited</> : null}
        </span>
        {/* THE "READ-ONLY — THESE DESCRIBE WHAT THE PIPELINE COMPUTES TODAY" CHIP IS GONE. It was
            true while five services computed the derivations and nothing read the definitions;
            the matcher is built from this set now and a run pins the version it used, so the chip
            described the screen as the opposite of what it is. */}
      </div>

      {/* THE SET-LEVEL FACTS. `target_template_key` is what binds this configuration to ONE
          template — the key-gate the retired second engine kept in its own file and a bare JSON
          array had nowhere to put, which is why it had to be declared on the set itself when the
          two models were merged into the single configuration engine. */}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", margin: "0 0 16px",
                     fontSize: 11, color: color.muted }}>
        <span>binds to <b style={{ fontFamily: font.mono, color: color.sec2 }}>
          {set.target_template_key || "—"}</b></span>
        <span><b style={{ fontFamily: font.mono, color: color.sec2 }}>
          {Object.keys(set.section_defaults).length}</b> section defaults</span>
        <span>locales <b style={{ fontFamily: font.mono, color: color.sec2 }}>
          {set.supported_locales.join(", ")}</b></span>
        {/* The AUTHORED version string inside the definition — not the stored row's version, which
            the header states. Labelled since both are on screen now and they need not agree. */}
        {set.metadata.version && <span>authored v{set.metadata.version}</span>}
      </div>

      {/* SEARCH. Above both panes because it governs the list, and full width so a long caption
          being pasted in to find its line has room. */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                     marginBottom: 10 }}>
        <div style={{ position: "relative", flex: "1 1 320px", minWidth: 240 }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") setQuery(""); }}
            data-testid="li-search"
            aria-label="Search line items"
            placeholder="Search key, label, alias in any locale, or hint…"
            style={{ width: "100%", boxSizing: "border-box", padding: "7px 30px 7px 10px",
                      fontSize: 12.5, borderRadius: radius.control, color: color.ink,
                      border: `1px solid ${query ? color.indigoBorder : color.cardBorder}`,
                      background: color.surface, outlineColor: color.indigo }}
          />
          {query && (
            <button onClick={() => setQuery("")} aria-label="Clear search"
                    style={{ position: "absolute", right: 4, top: 4, border: 0, cursor: "pointer",
                              background: "none", color: color.muted, fontSize: 14,
                              lineHeight: "20px", padding: "0 6px" }}>×</button>
          )}
        </div>
        {/* The type counts were already on screen as a read-only legend; making them CLICKABLE
            costs nothing and turns the commonest question ("show me the derived ones") into one
            click instead of a scroll. */}
        {(Object.keys(TYPE_TONE) as LineItemType[]).filter((k) => counts.by_type[k]).map((k) => {
          const on = typeFilter === k;
          return (
            <button key={k} onClick={() => setTypeFilter(on ? null : k)}
                    aria-pressed={on} data-testid={`li-filter-${k}`}
                    style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.3, cursor: "pointer",
                              textTransform: "uppercase", padding: "4px 8px",
                              borderRadius: radius.control, whiteSpace: "nowrap",
                              background: on ? TYPE_TONE[k].fg : TYPE_TONE[k].bg,
                              color: on ? color.surface : TYPE_TONE[k].fg,
                              border: `1px solid ${on ? TYPE_TONE[k].fg : "transparent"}` }}>
              {TYPE_TONE[k].label} {counts.by_type[k]}
            </button>
          );
        })}
        {filtering && (
          <span data-testid="li-hit-count"
                style={{ fontSize: 11.5, color: color.sec2, fontFamily: font.mono }}>
            {hits.length} of {flat.length}
          </span>
        )}
      </div>

      {/* EACH PANE SCROLLS ON ITS OWN. Both used to grow with their content under
          `alignItems: start`, so the list was ~475 rows tall and the detail sat at the TOP of a
          column that long — click a line near the bottom and its detail rendered thousands of
          pixels above where you were looking. Bounding each pane to the viewport and making the
          detail sticky is what makes selecting a line show you that line. */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1fr) minmax(380px, 1.1fr)",
                     gap: 16, alignItems: "start" }}>
        <Card pad={10}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 10,
                         padding: "0 10px 8px", borderBottom: `1px solid ${color.hairline2}`,
                         fontSize: 10.5, fontWeight: 700, letterSpacing: 0.3, color: color.muted,
                         textTransform: "uppercase" }}>
            <span>Line item</span><span>Type</span><span>Out</span>
          </div>
          <div style={{ marginTop: 4, maxHeight: "calc(100vh - 300px)", minHeight: 220,
                         overflowY: "auto" }}>
            {filtering
              ? (hits.length
                  ? hits.map((d) => (
                      <div key={d.key}>
                        {parentOf.get(d.key) && (
                          <div style={{ fontSize: 9.5, fontFamily: font.mono, color: color.faint,
                                         padding: "3px 10px 0" }}>
                            in {byKey.get(parentOf.get(d.key)!)?.label
                                 || parentOf.get(d.key)}
                          </div>
                        )}
                        {row(d, 0)}
                      </div>
                    ))
                  : <div style={{ fontSize: 12.5, color: color.muted, padding: "14px 10px" }}>
                      Nothing matches <b>{query}</b>
                      {typeFilter && <> in <b>{TYPE_TONE[typeFilter].label}</b></>}.
                    </div>)
              : items.map((d) => row(d, 0))}
          </div>
        </Card>
        <Card>
          <div style={{ position: "sticky", top: 0, maxHeight: "calc(100vh - 240px)",
                         overflowY: "auto" }}>
            {/* WHOSE ITEM THIS IS, and the delete only where it is allowed.
                A `template` item exists because the deliverable has a column for that figure, so
                deleting it would leave a line nothing can fill — the endpoint refuses it with 409
                and the reason is stated here rather than leaving a reader to click and find out.
                The UI hiding the button is a courtesy; the rule is the server's, because otherwise
                the same delete is one API call away. */}
            {selected && canEdit && inForce?.id && (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                             gap: 10, flexWrap: "wrap", marginBottom: 10, paddingBottom: 8,
                             borderBottom: `1px solid ${color.hairline2}` }}>
                <span style={{ fontSize: 11, color: color.muted }}>
                  {selected.namespace === "template"
                    ? "From the output template — its configuration cannot be deleted."
                    : "Added beyond the template — yours to remove."}
                </span>
                {selected.namespace !== "template" && (
                  <button data-testid={`li-delete-${selected.key}`} disabled={remove.isPending}
                          onClick={() => remove.mutate(
                            { lineItemVersionId: inForce.id, key: selected.key },
                            { onSuccess: () => setSel(null) })}
                          style={{ fontSize: 11.5, cursor: "pointer", padding: "4px 10px",
                                    borderRadius: radius.control, background: "none",
                                    border: `1px solid ${color.redFg}55`, color: color.redFg,
                                    whiteSpace: "nowrap" }}>
                    {remove.isPending ? "Deleting…" : "Delete this item"}
                  </button>
                )}
              </div>
            )}
            {remove.error && (
              <div data-testid="li-delete-error"
                   style={{ fontSize: 11.5, color: color.redFg, marginBottom: 10 }}>
                {refusalText(remove.error)}
              </div>
            )}
            {selected
              ? <Detail item={selected} set={set} vocab={vocab} keys={keyOptions}
                        canEdit={canEdit} versionId={inForce?.id} versionNumber={inForce?.version}
                        locale={locale} onLocale={onLocale}
                        draft={draft} patch={patch} drop={drop}
                        errors={serverErrors} indexErrors={indexErrors} formError={formError}
                        saved={saved} saving={save.isPending}
                        changed={changed} summary={summary}
                        onSave={onSave}
                        onDiscard={() => { resetDraft(); setSaved(null); }}
                        onSelect={chooseKey}
                        aliasDraft={aliasDraft} onAliasDraft={setAliasDraft} />
              : <div style={{ fontSize: 12.5, color: color.muted }}>Select a line item.</div>}
          </div>
        </Card>
      </div>
    </div>
  );
}
