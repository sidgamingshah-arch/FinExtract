/** Line items — the configuration behind the eight output lines and their sub-line items.
 *
 * WHY THIS SCREEN EXISTS. Those eight lines were each assembled from parts that lived only as
 * Python: `deprec_impairment` alone read thirteen note-level datasets and resolved them through a
 * five-rung cascade, and the template declared the eight with no children at all — so nothing
 * outside that module knew the parts existed. Widening one caption meant editing a
 * 162-alternative regex and shipping a release. This is the surface on which the whole arrangement
 * is read AND authored.
 *
 * THOSE SERVICES ARE NOW GONE — all five derivation services (2,731 lines carrying ~562
 * hand-enumerated note titles, row captions and formula variants) were deleted, and only the
 * contingent-liabilities DISCLOSURE survives, because its output is a paragraph rather than a
 * number. Nothing in the pipeline computes those figures any more, so THIS SCREEN IS WHERE THEY
 * COME FROM: a line item with no alias, definition or cascade describing its caption leaves its
 * output cell blank, by design. `implemented_by` is free text and now reads as history — a note of
 * what once computed a line, not a switch that routes anything.
 *
 * THIS IS THE EDITOR FOR THE ONE CONFIGURATION THE PIPELINE READS. It was read-only, and said so,
 * on the justification that the definitions only DESCRIBED derivations five services computed
 * (`implemented_by` named which), so nothing downstream read them. That justification expired
 * twice over: line items is the single configuration engine, the matcher is built from this set by
 * `services/working_view.py`, a run pins `extraction_runs.line_item_version_id` — and the
 * derivations the field described no longer exist. The definitions DRIVE extraction, so a field
 * that is authorable in the schema and unreachable from here is a control the product claims to
 * have and does not.
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
  BoolField, InfoToggle, KeyPicker, LockedRow, RungCards,
  SelectField, StringListEditor, TermRows, TextArea, TextField, type KeyOption,
} from "../components/configFields";
import { RequestGroups } from "../components/RequestGroups";
import { Button, Card } from "../components/ui";
import { useT } from "../i18n";
import { ApiError, refusalText } from "../lib/api";
import {
  useAddLineItem, useDeleteLineItem, useEditLineItemConfig, useEditLineItemSet,
  useLineItems,
} from "../lib/queries";
import { useCan } from "../lib/rbac";
import { SCREENS } from "./config";
import { color, font, radius } from "../theme";
import type {
  LineItemDef, LineItemEdit,
  LineItemNoteUse, LineItemSetInfo,
  LineItemType, LineItemVocab,
  NoteSource, StatementToken,
 NoteSelection,} from "../types";

const TYPE_TONE: Record<LineItemType, { bg: string; fg: string; label: string }> = {
  extracted: { bg: color.greenBg, fg: color.greenFg, label: "Extracted" },
  calculated: { bg: color.indigoTint2, fg: color.indigo, label: "Calculated" },
  intermediate: { bg: color.segBg, fg: color.sec2, label: "Intermediate" },
  derived: { bg: color.amberBg, fg: color.amberFg, label: "Derived" },
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







const NOTE_USE_HELP: Record<string, string> = {
  evidence_only: "a cited note may evidence this figure but never be its source",
  decomposition_allowed: "a cited note may be the SOURCE this line is read from",
};

/** A fresh nullable sub-object, so switching one on writes a shape the loader accepts rather than
 *  a half-object the model then refuses. Every list starts EMPTY — a configured empty, which is
 *  what "I have declared this object and not yet its patterns" actually means. */
const NEW_NOTE_SOURCE: NoteSource = {
  note_title_any: [], row_caption_any: [], row_caption_none: [],
  prose_any: [], note_terms: [], row_terms: [], row_terms_none: [],
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
/** SIMPLE — the eight questions a configurator answers to make a line work.
 *
 *  73 editable fields was a schema browser, not a configuration screen. The cut is evidence-led,
 *  measured over the 475 shipped items: these are the fields an author sets to define a line at
 *  all, and between them they cover every line in the shipped set. Everything else is either an
 *  override of a section-level default or machinery for a case that arises on a handful of lines.
 *
 *  `aliases` and `definition` carry the most weight: aliases catch the wording a filing prints,
 *  and the definition is what resolves a wording nobody listed. `exclude_criteria` sharpens that.
 *  `inherits` decides where the line may be found and `type` how it gets a figure.
 */
/** SIMPLE IS PER TYPE, NOT ONE LIST FOR EVERY LINE.
 *
 *  WHAT WAS WRONG WITH ONE LIST. `definition`, `aliases` and the include/exclude criteria are the
 *  fields that resolve a PRINTED CAPTION — they are read by the description-matching tier and they
 *  are the prose the model reasons over. A `derived` line has no caption to resolve: its figure is
 *  its cascade's, and it is never offered to the model (`_llm_withheld`). So on the nine derived
 *  lines the simple form opened with four prose boxes that nothing reads, and the one control that
 *  decides the figure — the cascade — was behind the advanced toggle. The same holds for the
 *  arithmetic types, whose figure is `terms`.
 *
 *  SO THE SPLIT IS A FUNCTION OF `type`, and each type's simple form is the fields that decide
 *  THAT kind of line:
 *
 *      extracted     read off the page      -> meaning, captions, note sourcing
 *      derived       a declared cascade     -> the cascade, and where it may read a printed row
 *      calculated /  arithmetic over other
 *      intermediate  lines                  -> the terms
 *
 *  EVERY TYPE KEEPS `COMMON`: what the line is called, what kind of line it is, whether it is
 *  delivered, and what kind of figure it holds. Nothing is removed from the screen — advanced
 *  still shows every control, and `requiredNow` still forces a control the server would refuse
 *  the save without.
 */
/** WHAT EVERY LINE ITEM ANSWERS, whatever its type — the v2 spec's universal three.
 *
 *  `in_output`, `output_structure` and `sign_expectation` were here and are gone: delivery is the
 *  template's decision, `output_structure` was declared by 0 of 539 items while sitting on the
 *  SIMPLE form, and the sign never varies inside a section so it is section policy. */
const COMMON_SIMPLE = ["label", "statement", "type"];

/** What the line is, in words, plus the captions that reach it. Read by the matcher and the model,
 *  and therefore simple ONLY for a line either of them can reach. */
const MEANING_SIMPLE = [
  "definition",               // 539 of 539, 539 distinct — the most-authored field in the set
  "exclude_criteria",         // 462 of 539, 31 distinct
  // THE THREE RECOGNITION LISTS, which are not interchangeable. `aliases` are captions folded
  // through `normalize_label` and matched exactly; `regex_hints` are patterns matched against the
  // raw caption AND the normalised one; `keyword_hints` require every word to be present rather
  // than matching a shape. A filing wording a row unexpectedly is caught by the second or third
  // where the first cannot reach it.
  "aliases",                  // 462 of 539, 461 distinct
  "regex_hints",              // 393 of 539, 393 distinct
  "keyword_hints",            // 461 of 539, 461 distinct
  // PER-LANGUAGE CAPTIONS. Not a separate control — the `aliases` editor is locale-scoped and
  // writes `aliases_i18n[locale]`, with the locale selector above it. 395 items declare it and the
  // shipped set carries 473 distinct Chinese captions, which a single flat list cannot hold
  // because the matcher folds every locale into one index.
  // THE LINE'S OWN INSTRUCTION to the model, on the simple form because the model is only ever
  // asked about an extracted line — so this is the one type where it does anything.
  "prompt",                   // 77 of 539, 77 distinct
];

/** WHERE A FIGURE MAY BE READ FROM — the note-sourcing block, simple for an extracted line because
 *  reading a figure out of a note is what most of the set's parts do.
 *
 *  THE PATTERN AND TERM LISTS COME WITH THE SWITCH, and that is not a liberty. `note_source` is
 *  only a BoolField — whether a note is read at all — and the lists under it are what say WHICH
 *  note and which of its rows. They render only while the switch is on, so promoting the switch
 *  alone would put a control on the simple form that reveals nothing when you use it.
 *
 *  Both halves are here because they answer the same two questions in two different ways: the
 *  `*_any`/`*_none` regexes MATCH, the `*_terms` lists SCORE. A line whose phrasing nobody
 *  anticipated is reached by the second and not by the first, which is the whole reason the
 *  semantic half exists. `prose_any` is the third route — a figure the filing states in a sentence
 *  and tabulates nowhere. */
const NOTE_SOURCE_SIMPLE = [
  "note_source",
  "note_source.note_title_any", "note_source.row_caption_any", "note_source.row_caption_none",
  "note_source.note_terms", "note_source.row_terms", "note_source.row_terms_none",
  "note_source.prose_any",
  // WHETHER THE MODEL IS ASKED, AND WITH WHAT. Both are conditional on `extract`, so on a line
  // that never reaches the model they are withheld with a reason rather than shown here.
  "note_selection", "llm_only_if_note_tagged",
];

/** The simple form for one line, by its type.
 *
 *  `cascade` and `implemented_by` are already forced onto a derived line's form by `requiredNow`
 *  — without them the save is refused with no control to answer the refusal. Naming them here is
 *  what makes them the FIRST thing an author of a derived line sees, rather than controls that
 *  appear because a validator complained. */
function simpleFieldsFor(type: string): Set<string> {
  const out = [...COMMON_SIMPLE];
  if (type === "derived") {
    // THE CASCADE, AND NOTHING THAT MATCHES A CAPTION. A derived line's figure is its cascade's:
    // no caption, alias, regex or semantic probe reaches it, and the model is never offered it.
    // So the simple form is the rungs and where they may read from, and every recognition field is
    // not merely advanced but WITHHELD with a reason (see `withheldReason`).
    //
    // `aliases` WAS HERE AND IS GONE. It was added on the measurement that
    // `is_pl__sales_revenues` publishes 4,995,768 off the face by its alias with no rung firing —
    // which was true, and was the short-circuit worth removing rather than a route worth keeping:
    // a figure that arrives on the parent means no rung ran, so the record loses which of the
    // filing's disclosures it came from. The face figure now enters through rung P1, whose part
    // carries the recognition.
    //
    // `definition` and the criteria stay advanced for the same reason they always did: their
    // readers are the description tier and the model, and neither sees a derived line.
    //
    // `extraction_mode` WAS HERE AND IS RETIRED: `type` is now the only field saying how a figure
    // is obtained, and this control was the other half of the crossed naming that caused it to be
    // read for the wrong question.
    //
    // `note_use` IS HERE, AND A CASCADE IS NOT SUFFICIENT WITHOUT IT. The permission is read off
    // the PARENT, not the part — `stages/note_sourced._note_permission`, "Read off the PARENT,
    // because the parent is the concept being filled" — and the gate refuses the whole fill when
    // it is not `decomposition_allowed`. Measured, exactly ONE item in the set overrides its
    // section default for this field: `notes__contingent_liabilities` declares
    // `decomposition_allowed` where the `notes` section says `evidence_only`. Without that
    // override its six-rung cascade publishes blank on every filing. So "a derived line only needs
    // its rule" is false by one item, and that item is one of the eight focus lines.
    out.push("cascade", "implemented_by", "inherits", "note_use");
  } else if (type === "calculated") {
    // A SUBTOTAL NEEDS NOTHING BUT ITS TYPE. Its components are named by the template's
    // `rollup: {op: "sum", children: [...]}`, and `services/export.py` evaluates them from there —
    // so the config asserts nothing about the arithmetic and there is nothing to author. `terms`
    // is on the form for the arithmetic line that names its own inputs instead of inheriting the
    // template's, which is what `requiredNow` still forces.
    out.push("terms");
  } else {
    out.push(...MEANING_SIMPLE, ...NOTE_SOURCE_SIMPLE, "inherits", "section_scope",
             // TO BE SOURCED FROM A NOTE OR THE FACE — the one gate switch the spec keeps per
             // line. `face_only` is retired beside it: 462 of 539 items declare it with ONE
             // distinct value and it never varies inside a section, so it is section policy.
             // `section_disambiguation` WAS HERE. Its 395 values held 13 distinct strings,
             // every one `"Bind only to {statement} / {section}."` — the line's own gate restated,
             // and named by engine key. The BANNER (`section_scope`) is what actually settles a
             // contested caption: 170 of this set's collisions are same-statement, different
             // sub-heading, and it is already on this form.
             "note_use");
  }
  return new Set(out);
}

/** THE TWO CONTROLS THAT ARE ON THE FORM AND MUST NOT BE OFFERED.
 *
 *  This set used to hold 39 names and is down to two, because retiring a control and REMOVING it
 *  are different things and only the first needs a filter. A retired control was still rendered,
 *  still counted in its banner, still in `withheldReason`'s rules — `fld` just returned null for
 *  it. Once the control itself is gone there is nothing to filter, so a name here without a
 *  matching `fld` call guards nothing and reads as a live rule; `tests/test_form_field_lists.py`
 *  refuses one.
 *
 *  THE RETIREMENT RECORD, kept because the measurements are the argument and would otherwise be
 *  rediscovered field by field. Counted against the 475 authored shipped items
 *  (`_audit/field_usage.py`), or the 539 resolved where noted.
 *
 *    declared by NOBODY (0 of 475) — a control nobody has had a reason to touch:
 *      `pattern` (`regex_hints` is the list form, used on 393)   `scopes`   `side`
 *      `allow_contra`   `face_only`   `sole_component_of`   `analyst_bucket`
 *      `sign_rule.convention` + `sign_rule.flip_if_label_matches`   `output_structure`
 *      `others_rule`   `derivation`   `notes_as_source_rationale` (read if set, and never set)
 *
 *    a CONSTANT wearing a control — declared on hundreds with one or two distinct values:
 *      `temporality` (462 of 539, 2 distinct, 0 of 18 sections vary)
 *      `sign_expectation` (462 of 539, `sign_convention` on the wire, 0 vary)
 *      `decomposition_rule` (391 of 475, 3 distinct, 358 restating `global_rules`)
 *      `note_use_rationale` (394 of 475, ONE distinct value — never rendered here; recorded so a
 *       future author does not add one)
 *
 *    the TEMPLATE already declares it:
 *      `rollup` / `is_gross_parent` / `children_if_decomposed` (its `rollup.children`)
 *      `order` (its row order)   `residual_policy` + `never_sweep` + `expected_components`
 *      (its `__others` keys route the sweep)   `value_scope` (462 of 539, 2 distinct)
 *
 *    superseded by a field that says it better:
 *      `extraction_mode` (by `type`)   `description` (by `definition`, which the model reads)
 *      `match_priority` (462 of 475, 76 distinct — the shipped ranking stands and ties can no
 *       longer be re-broken from this screen)
 *
 *  `parent` IS NOT IN THAT RECORD AND WAS. It was retired on "13 of 475"; re-measured against the
 *  shipped 539 it is declared by exactly the 77 note-read parts — all of them — and it is the only
 *  field saying which whole a part explains. See `tests/test_form_field_lists.py`.
 *
 *  Removing a control does not remove the FIELD: a stored value keeps working and the endpoint
 *  still accepts it. What went is the invitation to set it here.
 */
const RETIRED_FIELDS = new Set([
  // BOTH ARE RENDERED, and `requiredNow` forces both back the moment `type` is `derived` — which
  // is the only state either one means anything in. So the filter and the override together say
  // "offered on a derived line, nowhere else", and neither half works alone: without the filter
  // every extracted line carries two controls for a cascade it will never have, and without the
  // override choosing `derived` produces a server refusal with no control on screen to answer.
  "cascade",                 //  2 of 475 — ordered fallbacks when the preferred source is absent.
  "implemented_by",          //  1 of 475 — names the code filling a line configuration cannot.
]);

/** The three types the v2 spec keeps, in the order an author meets them.
 *
 *  `intermediate` IS GONE and `calculated` STAYS THOUGH NOTHING DECLARES IT — which looks backwards
 *  until you count what `extract_or_derive` actually was. All 33 items declaring it are SUBTOTALS
 *  (`total_assets`, `gross_profit`, `profit_loss_before_tax`, …), 32 of them `role: subtotal`/`total`
 *  in the template with their own `rollup: {op: "sum", children: [...]}`. The template's rollup is
 *  already what fills them — `services/export.py` evaluates the template's calculated lines from
 *  their components and puts the printed figure in a CELL COMMENT, "a subtotal that contradicts its
 *  own components is a finding, not the figure to hand to a reader". So those 33 are `calculated`,
 *  and `calculated` is the type they are migrating to rather than a speculative future value.
 *
 *  `intermediate` by contrast is declared by no item and means only "calculated, and never
 *  delivered" — which is now the template's decision, not a type. */
const SPEC_TYPES = ["extracted", "calculated", "derived"] as const;

/** THE MASTER PROMPT, edited in place — the one instruction that applies to every mapping call.
 *
 *  WHY IT IS A SCREEN-LEVEL BLOCK and not a field on a line. It belongs to the CONFIGURATION, not
 *  to any item: `PATCH /line-items/versions/{id}` writes it and publishes a new version exactly as
 *  an item edit does. Putting it on the item form would imply each line has one.
 *
 *  WHAT IS NOT HERE, deliberately. The reply contract — the shape of the answer and the citation
 *  rules the parser depends on — is fixed in code (`mapping._LLM_REPLY_CONTRACT`) and is not
 *  offered for editing, because an admin who could change it could produce a reply the system
 *  cannot read. This box is the half that is a matter of judgement.
 */
function MasterPrompt({ versionId, served, canEdit }: {
  versionId: string; served: string; canEdit: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(served);
  const save = useEditLineItemSet();
  // Re-seed when the server serves a different version, so a publish elsewhere is not overwritten
  // by a stale draft still sitting in this box.
  //
  // KEYED ON `versionId` rather than on `served`. This one worked, because `served` is a string and
  // strings compare by value — but the same two lines in `RequestGroups`, where the value is an
  // array built with `?? []`, were an infinite re-render that took the screen down. Keying both on
  // the version removes the dependence on the served value's TYPE, and says what the trigger is.
  const [seed, setSeed] = useState(versionId);
  if (seed !== versionId) { setSeed(versionId); setText(served); }
  const dirty = text !== served;

  return (
    <Card pad={12} style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <b style={{ fontSize: 12.5 }}>Master prompt</b>
        <InfoToggle open={open} about="the master prompt" onToggle={() => setOpen((v) => !v)} />
        <span style={{ fontSize: 11, color: color.muted }}>
          {served ? `${served.length} characters, sent on every mapping call` : "not set"}
        </span>
        <span style={{ flex: 1 }} />
        <button type="button" data-testid="li-master-prompt-toggle"
                onClick={() => setOpen((v) => !v)}
                style={{ fontSize: 11.5, cursor: "pointer", padding: "3px 10px",
                          border: `1px solid ${color.cardBorder}`, borderRadius: radius.control,
                          background: "transparent", color: color.sec2 }}>
          {open ? "Close" : canEdit ? "Edit" : "View"}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 9 }}>
          <p style={{ margin: "0 0 7px", fontSize: 10.5, color: color.muted, lineHeight: 1.5 }}>
            Appended to the fixed reply contract and read before the global policies, so this is
            where a standing rule about how captions should be judged belongs. The reply shape and
            the citation rules are fixed in code and are not editable here — changing them could
            produce an answer the pipeline cannot read.
          </p>
          <TextArea label="Instruction sent with every mapping call" testid="master_prompt"
                    editable={canEdit} rows={6}
                    reason={canEdit ? undefined : "you do not have `config:line_items`"}
                    value={text} onChange={(v) => setText(v ?? "")} />
          {canEdit && (
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <Button testid="li-master-prompt-save" disabled={!dirty || save.isPending}
                      onClick={() => save.mutate({ lineItemVersionId: versionId,
                                                    edit: { prompt: text } })}>
                {save.isPending ? "Publishing…" : "Save — publishes a new version"}
              </Button>
              {dirty && (
                <button type="button" onClick={() => setText(served)}
                        style={{ fontSize: 11.5, cursor: "pointer", border: 0,
                                  background: "transparent", color: color.sec2 }}>
                  Discard
                </button>
              )}
              {save.isError && (
                <span style={{ fontSize: 11, color: color.redFg }}>
                  {refusalText(save.error as ApiError) ?? (save.error as Error)?.message}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}


/** WHAT THE CURRENT SELECTIONS MAKE MEANINGLESS — the reason a control is withheld, or null.
 *
 *  WHY THIS IS NOT A UI PREFERENCE. Most of these rules are ENFORCED BY THE SERVER, in
 *  `LineItemDef._coherent` (backend/app/schemas/line_items.py). Before this, the form offered every
 *  control regardless of the line's type, so an author could fill in a prompt on a calculated line,
 *  save, and be told "a prompt is only sent for an `extracted` line" by a validator two layers
 *  down. Offering a control whose value the save will refuse is worse than not offering it.
 *
 *  The rest are inert rather than refused: the value stores fine and no code ever reads it. Those
 *  are the more dangerous half, because nothing contradicts them — an author sets aliases on a line
 *  the matcher can never reach and the configuration looks like it is working.
 *
 *  NOTHING IS HIDDEN SILENTLY. Every reason returned here is counted in the form header and can be
 *  revealed, because a control that is absent and a control that is withheld for a stated reason
 *  look identical on a screen and only one of them is a decision — the rule this file already
 *  applies to `LockedRow`.
 */
function withheldReason(name: string, sel: {
  type: string; extractionMode: string; aliasMatching: string; outputStructure: string;
}): string | null {
  const { type, extractionMode, aliasMatching } = sel;

  // ── SERVER-ENFORCED ────────────────────────────────────────────────────────────────────────
  // `_coherent`: a prompt is only ever sent for an `extracted` line, and is refused elsewhere.
  if (name === "prompt" && type !== "extracted") {
    return `a prompt is only sent for an extracted line, and this one is ${type} — its figure comes from arithmetic, so the model is never asked about it`;
  }
  // `_coherent`: terms are REQUIRED for calculated/intermediate and meaningless otherwise.
  if (name === "terms" && !["calculated", "intermediate"].includes(type)) {
    return `terms are the inputs of an arithmetic line; a ${type} line does not have any`;
  }
  // `_coherent`: a derived line needs a cascade or `implemented_by`; neither means anything else.
  if (["cascade", "implemented_by"].includes(name) && type !== "derived") {
    return `only a derived line is filled this way; this one is ${type}`;
  }
  // `_coherent`: A NOTE SOURCE ON A DERIVED LINE FILLS THE PARENT AND SKIPS ITS CASCADE.
  //
  // This one is not inert — it WORKS, and working is the bug.
  // `stages/note_sourced.py::_declared_items` reads every item declaring a `note_source`, so a
  // derived parent with one gets filled straight from those note rows: no rung runs, the record
  // loses which of the filing's disclosures the number came from, and the arithmetic that would
  // have cross-checked it against the other rungs never happens. The figure looks identical
  // either way, which is why the server refuses it rather than leaving it to be noticed.
  //
  // None of the nine shipped derived lines declares one. That was luck rather than a rule, and
  // this is the rule.
  if ((name === "note_source" || name.startsWith("note_source.")) && type === "derived") {
    return "a note source names where in the notes a figure is READ from, and a derived line is "
         + "not read — its figure is its cascade's. Declaring one here would fill the parent "
         + "directly and skip every rung; put it on the part whose rung reads that note";
  }

  // `_coherent`: both of these are about whether the MODEL is asked, and TWO declarations decide
  // that — the mirror of `LineItemDef._never_asked` and of `mapping._llm_withheld`. A `derived`
  // line is filled by its declared cascade whatever the mode says, and a mode other than `extract`
  // withholds the offer. The server raises rather than ignoring, so the controls are withheld here
  // rather than left on to be refused.
  if (["note_selection", "llm_only_if_note_tagged"].includes(name)) {
    const subject = name === "note_selection"
      ? "this chooses what note context a line's own request carries"
      : "this asks whether the model is consulted";
    if (type === "derived") {
      return `${subject}, and a derived line's figure comes from its declared cascade — the model is never asked about it`;
    }
    if (extractionMode !== "extract") {
      return `${subject}, and a ${extractionMode} line takes its figure from declared arithmetic wherever the row is not printed — the model is never asked about it`;
    }
  }

  // ── INERT BY CONFIGURATION ─────────────────────────────────────────────────────────────────
  // A line the matcher can never reach gets no caption offered to it, so every caption-matching
  // control is text that is stored and never read. `alias_matching: "disabled"` and
  // `extraction_mode: "derive"` are the two locks that produce it — together they are
  // `mapping._unmatchable`, and a concept in that set cannot be reached by any caption or any
  // model decision.
  const CAPTION_MATCHING = ["aliases", "regex_hints", "keyword_hints", "exclude_hints"];
  if (CAPTION_MATCHING.includes(name)) {
    if (aliasMatching === "disabled") {
      return "caption matching is switched off for this line, so nothing here is ever consulted";
    }
    if (extractionMode === "derive") {
      return "a derive-only line is never offered to the matcher, so nothing here is ever consulted";
    }
    // A DERIVED PARENT IS REACHED BY NOTHING — not the model, not an alias, not a regex, not a
    // semantic probe. Its figure is its declared cascade's, and four separate indexes in the
    // backend now agree on it (`mapping._unmatchable`, the alias index, `_mappable_keys`, and
    // `line_item_matching._unmatchable`). The shipped set still DECLARES aliases on these nine —
    // 9 to 23 each — which is exactly why the control has to say so: offering an editor for a
    // list nothing reads is how an author spends an afternoon widening a match that cannot fire.
    // Whatever a caption has to say about a derived line belongs on one of its PARTS.
    if (type === "derived") {
      return "a derived line's figure is its cascade's, and no caption, alias or semantic probe "
           + "may reach it — put the recognition on the part whose rung reads that source";
    }
  }
  return null;
}

/** Fields the CURRENT selections make mandatory, shown even when retired or in simple mode.
 *
 *  The one case where withholding a control would trap an author. `_coherent` refuses a derived
 *  line that has neither a cascade nor an `implemented_by`, and both of those controls are retired
 *  — so choosing "derived" would produce a refusal with no control on the screen to answer it. The
 *  same holds for `terms` on a calculated line, which is behind the advanced toggle.
 */
function requiredNow(name: string, sel: { type: string; outputStructure: string }): boolean {
  if (["cascade", "implemented_by"].includes(name)) return sel.type === "derived";
  if (name === "terms") return ["calculated", "intermediate"].includes(sel.type);
  // PROSE IS WRITTEN FROM THE PROMPT AND NOTHING ELSE, and the server refuses prose without one.
  // So the moment an author chooses prose, the prompt has to be on the form whatever the mode —
  // otherwise the save is refused with no control on screen to answer the refusal.
  if (name === "prompt") return sel.outputStructure === "prose";
  return false;
}

/** Every field `withheldReason` can speak about — the ONLY controls conditionality can remove.
 *
 *  Kept beside the rules rather than as a list of all 63 field names, because a second copy of the
 *  form's inventory is a copy that goes stale. Adding a rule above means adding its field here, and
 *  the test below this file's usage asserts the two agree.
 */
/** THE FORM'S GROUPING, AS DATA — one entry per banner, in the order a line is decided.
 *
 *  WHY THE MEMBERSHIP IS A LIST HERE and not implied by where a control sits in the JSX. Each
 *  banner has to say how many controls are inside it before the controls are rendered (React
 *  builds the heading first), and it has to know whether the group is worth rendering at all. Both
 *  answers come from walking the group's field names through the same filter `fld` applies, so the
 *  names have to exist as a list. Two copies of that list — one for the visibility test, one for
 *  the count — would drift, and the visible way it drifts is a banner announcing four fields over
 *  a group showing three.
 *
 *  THE ORDER IS THE ARGUMENT. What the line IS comes before which captions reach it, which comes
 *  before where it may be claimed from — so the numbers on the banners are how far through that
 *  reasoning the reader has got, rather than decoration.
 *
 *  THE INVARIANT, asserted by `tests/test_form_field_lists.py`: a name is listed here IF AND ONLY
 *  IF a `fld(...)` call renders it, each in exactly one group. A RETIRED field used to be listed
 *  too, which read as documentation and behaved as a defect — `shows` filters it out, so the name
 *  sat in the group's list contributing nothing while `RETIRED_FIELDS` already recorded it with
 *  its measurement. Two inventories of the same form is the drift this comment warns about; one of
 *  them going stale is how a banner comes to announce four fields over a group showing three.
 */
const GROUP_FIELDS = {
  meaning: ["label", "definition", "prompt", "exclude_criteria"],
  recognition: ["aliases", "regex_hints", "keyword_hints", "exclude_hints"],
  gate: ["inherits", "statement", "section_scope", "note_selection", "llm_only_if_note_tagged",
         "note_use", "note_source"],
  /** Rendered only while the `note_source` switch is on, so counted only then. */
  noteSource: ["note_source.note_title_any", "note_source.row_caption_any",
               "note_source.row_caption_none", "note_source.note_terms", "note_source.row_terms",
               "note_source.row_terms_none", "note_source.prose_any"],
  structure: ["parent"],
  // `type` FIRST, because it selects which of the other three applies. It used to sit in
  // `structure`, one group away from the fields it governs and under a question about hierarchy
  // ("How does it sit among the other lines?") — while the field LABELLED "How the figure is
  // obtained" was `extraction_mode`, which decided something else entirely. That crossed naming is
  // not cosmetic: it is what let the LLM gate key on the wrong field and offer revenue to the
  // model, and what made a cascade's protection depend on a mode value.
  assembly: ["type", "terms", "cascade", "implemented_by"],
} as const;

const CONDITIONAL_FIELDS = [
  "prompt", "terms", "cascade", "implemented_by",
  // withheld on a line the model is never asked about — see withheldReason
  "llm_only_if_note_tagged", "note_selection",
  // withheld on a DERIVED line, where a note source would fill the parent and skip its cascade.
  // The switch and every control under it, so rolling the group open shows the reason once rather
  // than seven silent inputs.
  "note_source", "note_source.note_title_any", "note_source.row_caption_any",
  "note_source.row_caption_none", "note_source.note_terms", "note_source.row_terms",
  "note_source.row_terms_none", "note_source.prose_any",
  "aliases", "regex_hints", "keyword_hints", "exclude_hints",
];

/** What this line's own selections withhold, computed BEFORE the form renders.
 *
 *  Up front rather than collected as the controls are walked, because the count belongs in the
 *  header — which React builds before the fields below it, so a set filled during the walk would
 *  always read as empty there.
 */
function withheldFields(
  sel: { type: string; extractionMode: string; aliasMatching: string; outputStructure: string },
  errors: Record<string, string>,
): Map<string, string> {
  const out = new Map<string, string>();
  for (const name of CONDITIONAL_FIELDS) {
    if (errors[name] || requiredNow(name, sel)) continue;
    const why = withheldReason(name, sel);
    if (why) out.set(name, why);
  }
  return out;
}

/** ONE GROUP, UNDER A BANNER THAT ROLLS WITH THE SCROLL.
 *
 *  WHY THE BANNER STICKS. Even cut to one type's simple form the pane is taller than a viewport,
 *  and the question a control answers is the only thing that says what the control is FOR — an
 *  author who has scrolled past "Where may it be claimed from?" is looking at eight regex lists
 *  with no idea which question they answer. The heading therefore stays at the top edge of its own
 *  group while that group is on screen, and is replaced by the next group's as it arrives.
 *
 *  WHY IT COLLAPSES. Eight groups is eight answers to "where do I look", and an author editing one
 *  line usually needs one of them. Rolling a group shut leaves its banner — so the form still
 *  reads as eight questions rather than as a shorter form with things missing — and the count on
 *  the banner says how many controls are inside, because a collapsed group that is EMPTY and a
 *  collapsed group holding twelve controls must not look identical.
 *
 *  OPEN BY DEFAULT, every one. A form that opens mostly shut hides the fields an author came for
 *  behind a click each, and "what does this line say" stops being answerable by reading.
 */
function Group({ question, note, right, children, visible = true, index, count, open, onToggle }: {
  question: string; note?: ReactNode; right?: ReactNode; children: ReactNode;
  /** False when every field inside is filtered out — a heading over nothing is worse than an
   *  absent section, because it reads as a group whose controls failed to load. */
  visible?: boolean;
  /** Position in the form, shown on the banner. Not decoration: the groups are in the order a
   *  line is decided — what it is, which captions reach it, where it may be claimed from — so the
   *  number says how far through that order the reader is. */
  index: number;
  /** How many controls this group is showing, for the collapsed state to be legible. */
  count: number;
  open: boolean;
  onToggle: () => void;
}) {
  if (!visible) return null;
  // The group's own explanation collapses behind the same ⓘ the fields use, for the same reason:
  // seven headings each carrying a paragraph is most of the form's height before a single control.
  const [showNote, setShowNote] = useState(false);
  const slug = question.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return (
    <Card pad={0} style={{ marginBottom: 12, overflow: "visible" }}>
      {/* `position: sticky` ON THE BANNER, not on a wrapper: the banner scrolls with the page
          until its own card's top edge reaches the pane's top, then holds there while the rest of
          the group passes under it. `zIndex` keeps it over the controls it is holding above. */}
      <div style={{ position: "sticky", top: 0, zIndex: 2, background: color.surface,
                     borderBottom: open ? `1px solid ${color.hairline}` : "none",
                     borderRadius: `${radius.card}px ${radius.card}px 0 0`,
                     display: "flex", alignItems: "baseline", justifyContent: "space-between",
                     gap: 12, flexWrap: "wrap", padding: "10px 13px" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
          <button type="button" onClick={onToggle} aria-expanded={open}
                  data-testid={`li-group-toggle-${slug}`}
                  style={{ display: "flex", alignItems: "baseline", gap: 7, border: 0,
                            background: "transparent", padding: 0, cursor: "pointer",
                            font: "inherit", textAlign: "left" }}>
            <span style={{ fontSize: 9.5, fontWeight: 700, color: color.muted, letterSpacing: 0.4,
                            fontFamily: font.mono, transform: "translateY(-1px)" }}>
              {String(index).padStart(2, "0")}
            </span>
            <span aria-hidden style={{ fontSize: 9, color: color.sec2, width: 8,
                                        transform: open ? "none" : "rotate(-90deg)",
                                        transition: "transform 120ms" }}>▾</span>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: color.ink }}>
              {question}
            </h3>
          </button>
          <span style={{ fontSize: 10, color: color.muted }}>
            {count === 0 ? "nothing to set here" : `${count} field${count === 1 ? "" : "s"}`}
          </span>
          {note && <InfoToggle open={showNote} about={question}
                               onToggle={() => setShowNote((v) => !v)} />}
        </div>
        {right}
      </div>
      {open && (
        <div style={{ padding: "11px 13px 1px" }}>
          {note && showNote && (
            <p style={{ margin: "0 0 11px", paddingLeft: 9, fontSize: 10.5, color: color.muted,
                         lineHeight: 1.5, borderLeft: `2px solid ${color.indigoBorder2}` }}>
              {note}
            </p>
          )}
          {children}
        </div>
      )}
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
  // SIMPLE BY DEFAULT. The screen used to open all 73 controls at once, which is how a
  // configuration screen becomes unreadable: the eight fields that define a line sat among sixty
  // that override a section default or serve a case arising on four lines out of 475.
  const [mode, setMode] = useState<"simple" | "advanced">("simple");
  // OFF by default: the point of the conditional rules is that a control the line cannot use is not
  // on the form. The escape hatch exists so the withholding is auditable rather than mysterious —
  // an author who wants to see what was taken away, and why, can.
  const [showInapplicable, setShowInapplicable] = useState(false);
  // WHICH BANNERS ARE ROLLED SHUT, held here rather than inside each `Group` so that "collapse
  // all" is one state change and so a group's state survives the re-render a keystroke causes.
  // A group ABSENT from the map is OPEN — the default has to be "open" for every group including
  // ones added later, and a set of the shut ones says that without having to enumerate them.
  const [shut, setShut] = useState<Record<string, boolean>>({});
  const toggleGroup = (q: string) => setShut((s) => ({ ...s, [q]: !s[q] }));
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
  /** The selections the conditional rules are read against — the DRAFT value where the author has
   *  touched it, so the form reacts as they change the type rather than after they save. */
  const sel = {
    type: String(g("type", item.type) ?? "extracted"),
    extractionMode: String(g("extraction_mode", item.extraction_mode) ?? "extract"),
    aliasMatching: String(g("alias_matching", item.alias_matching) ?? "enabled"),
    outputStructure: String(g("output_structure", item.output_structure) ?? "value"),
  };
  /** What this line's own selections withhold. Derived from `sel`, so it follows the author's
   *  choice of type immediately rather than after a save. */
  const withheld = withheldFields(sel, errors);
  /** The simple form for THIS line's type, recomputed from the draft so switching the type
   *  re-cuts the form at once rather than after a save. */
  const simple = simpleFieldsFor(sel.type);
  const isSimple = (name: string) => simple.has(name);

  const fld = (name: string, render: (error?: string) => ReactNode) => {
    // THE ONE FILTER POINT. Every control on this screen goes through `fld`, so what a reader is
    // offered is decided here rather than in eight groups that would drift apart.
    //
    // ORDER MATTERS, and it is: a refusal beats everything, then what this line's own selections
    // make meaningless, then what the selections make MANDATORY (which overrides retirement and
    // simple mode both), then retirement, then the mode.
    if (withheld.has(name) && !showInapplicable) return null;
    const forced = requiredNow(name, sel);
    if (RETIRED_FIELDS.has(name) && !forced) return null;
    // A field the server refused is shown WHATEVER the mode, because hiding the control a refusal
    // is addressed to leaves an author with a message and nothing to act on.
    if (mode === "simple" && !isSimple(name) && !errors[name] && !forced) return null;
    return (
      <div data-testid={`li-field-${name}`} key={name}>
        <div data-testid={errors[name] ? `li-field-error-${name}` : undefined}>
          {render(errors[name])}
        </div>
        {/* Shown only while the author has asked to see inapplicable controls, so the reason
            arrives with the control it explains rather than as a list somewhere else. */}
        {withheld.has(name) && (
          <div style={{ fontSize: 10, color: color.amberFg, marginTop: -9, marginBottom: 12 }}>
            does not apply: {withheld.get(name)}
          </div>
        )}
      </div>
    );
  };
  /** Whether ONE named control survives every filter — the same order `fld` applies, and the
   *  single place that answers both "is this group worth a banner" and "how many fields is it
   *  holding". Two copies of this predicate would drift, and the visible way it would drift is a
   *  banner announcing four fields over a group showing three. */
  const shows = (n: string) => {
    const forced = requiredNow(n, sel);
    if (withheldReason(n, sel) && !forced && !showInapplicable && !errors[n]) return false;
    if (RETIRED_FIELDS.has(n) && !forced) return false;
    return mode === "advanced" || isSimple(n) || !!errors[n] || forced;
  };
  const idx = (name: string) => indexErrors[name];

  /** ONE BANNER'S FOUR PROPS, from its position and its field list.
   *
   *  Spread at the call site (`<Group {...band(3, GROUP_FIELDS.gate)} question=… >`) so that
   *  adding a group means adding a number and a list, and cannot mean forgetting to wire its
   *  toggle to the shared state — which would give one group a banner that does not respond. */
  const band = (index: number, fields: readonly string[]) => {
    const shown = fields.filter(shows);
    return {
      index,
      count: shown.length,
      visible: shown.length > 0,
      // KEYED ON THE QUESTION, not on the index: the numbers shift when a group is added and the
      // author's collapsed groups would silently move with them.
      open: !shut[`g${index}`],
      onToggle: () => toggleGroup(`g${index}`),
    };
  };
  /** Every group shut, or every group open — for a reader who wants the whole form at once, or
   *  who wants the eight questions with nothing under them as a table of contents. */
  const BANDS = [1, 2, 3, 4, 5];
  const allShut = BANDS.every((i) => shut[`g${i}`]);
  const setAll = (v: boolean) => setShut(Object.fromEntries(BANDS.map((i) => [`g${i}`, v])));

  const type = g<LineItemType>("type", item.type);
  const noteSource = g<NoteSource | null>("note_source", item.note_source);
  const perLocale = item.aliases_i18n ?? {};
  const otherLocales = Object.keys(perLocale).filter((l) => l !== locale);

  const setNoteSource = (patchNs: Partial<NoteSource>) =>
    patch({ note_source: { ...(noteSource ?? NEW_NOTE_SOURCE), ...patchNs } });

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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                     gap: 10, flexWrap: "wrap", margin: "3px 0 10px" }}>
        <span style={{ fontFamily: font.mono, fontSize: 11, color: color.muted }}>{item.key}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* ROLL THE WHOLE FORM UP OR DOWN. Shut, the eight banners are a table of contents for
              the form; open, it reads as one document. */}
          <button type="button" data-testid="li-bands-all" onClick={() => setAll(!allShut)}
                  style={{ fontSize: 11, cursor: "pointer", padding: "3px 9px",
                            border: `1px solid ${color.cardBorder}`, borderRadius: radius.control,
                            background: "transparent", color: color.sec2 }}>
            {allShut ? "Open all sections" : "Collapse all sections"}
          </button>
          {/* SIMPLE / ADVANCED. Simple is the fields that decide THIS KIND of line — a different
              set for an extracted line, a derived one and an arithmetic one; advanced is every
              override and every mechanism. A field the server REFUSED is shown in either mode —
              hiding the control a refusal is addressed to leaves an author with a message and
              nothing to act on. */}
          <div role="group" aria-label="How much configuration to show"
               style={{ display: "flex", border: `1px solid ${color.cardBorder}`,
                         borderRadius: radius.control, overflow: "hidden" }}>
            {(["simple", "advanced"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)} aria-pressed={mode === m}
                      data-testid={`li-mode-${m}`}
                      style={{ fontSize: 11, cursor: "pointer", padding: "3px 10px", border: 0,
                                textTransform: "capitalize",
                                background: mode === m ? color.indigo : "transparent",
                                color: mode === m ? "#fff" : color.sec2 }}>
                {m}
              </button>
            ))}
          </div>
        </div>
      </div>
      {mode === "simple" && (
        <p style={{ margin: "0 0 10px", fontSize: 10.5, color: color.muted }}>
          {sel.type === "derived"
            ? <>The fields that decide a <b>derived</b> line: the cascade that computes it, and
                whether a printed row may fill it where no rung resolves. Its definition, its
                aliases and its note patterns are under <b>Advanced</b> — nothing reads them on a
                line the matcher and the model are both never asked about.</>
            : sel.type === "calculated" || sel.type === "intermediate"
              ? <>The fields that decide a <b>{sel.type}</b> line: the signed terms it is summed
                  from. Its definition and its note patterns are under <b>Advanced</b> — this
                  line's figure is arithmetic over other lines, not something read off a page.</>
              : <>The fields that define this line: what it means, which captions reach it, and
                  which note its figure may be read out of. <b>Advanced</b> adds the section
                  overrides and the assembly rules — needed on a minority of lines, and inherited
                  from the section otherwise.</>}
        </p>
      )}

      {/* WHAT THIS LINE'S OWN SELECTIONS TOOK OFF THE FORM, stated rather than left to be noticed.
          A control that is absent and a control withheld for a reason look identical on a screen,
          and only one of them is a decision — the same rule `LockedRow` exists for. */}
      {withheld.size > 0 && (
        <p style={{ margin: "0 0 10px", fontSize: 10.5, color: color.muted }}>
          {withheld.size} control{withheld.size === 1 ? "" : "s"} {withheld.size === 1 ? "does" : "do"}{" "}
          not apply to {sel.type === "extracted" ? "an" : "a"} <b>{sel.type}</b> line
          {sel.aliasMatching === "disabled" || sel.extractionMode === "derive"
            ? " with caption matching off" : ""}
          {" "}and {withheld.size === 1 ? "is" : "are"} not shown.{" "}
          <button type="button" data-testid="li-show-inapplicable"
                  onClick={() => setShowInapplicable((v) => !v)}
                  style={{ border: 0, background: "transparent", padding: 0, fontSize: 10.5,
                            color: color.indigo, cursor: "pointer", textDecoration: "underline" }}>
            {showInapplicable ? "hide them again" : "show them anyway"}
          </button>
        </p>
      )}

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
      <Group {...band(1, GROUP_FIELDS.meaning)} question="What is this line, in words?"
             note="What the model reads when the printed caption is not close to any alias.">
        {fld("label", (e) => (
          <TextField label="Label" testid="label" editable={editable} reason={lockReason}
                     help="The name on this screen, the statement grids and the export. Display
                           prose — it has no matching consequence."
                     value={g("label", item.label)} onChange={(v) => patch({ label: v })}
                     error={e} inherited={inh("label", item.label)} />
        ))}
        {fld("definition", (e) => (
          <TextArea label="Definition — the authoritative accounting meaning" testid="definition"
                    editable={editable} rows={4} reason={lockReason}
                    help="The accounting meaning of this line, in prose. When a printed caption does
                          not match any alias, this is the text the model
                          reads to decide whether the caption belongs here
                          — so it is what resolves a wording nobody
                          thought to list."
                    value={g("definition", item.definition)}
                    onChange={(v) => patch({ definition: v ?? "" })}
                    error={e} inherited={inh("definition", item.definition)} />
        ))}
        {fld("prompt", (e) => (
          <TextArea label="Extra instruction for this line, sent to the model" testid="prompt"
                    editable={editable} rows={3} reason={lockReason}
                    help="ADDED TO THE MASTER PROMPT, never replacing it — the global policies still
                          apply. Sent only when this line is offered to the
                          model as a candidate, so it is the place to put
                          the one rule that applies to THIS line and
                          nothing else. Required when the line outputs
                          prose, because prose is written from it."
                    value={g("prompt", item.prompt)}
                    onChange={(v) => patch({ prompt: v ?? "" })}
                    error={e} />
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
      </Group>

      {/* ── 2. RECOGNITION ───────────────────────────────────────────────────────────────────
          THE LOCALE BEING EDITED IS IN THE HEADER, because `aliases` is locale-scoped: it
          replaces THAT locale's list, and the base list too when the locale is the set default.
          The other locales are read-only beside it and say so. A map-shaped write is precisely how
          editing the Chinese aliases clobbers the English ones. */}
      <Group {...band(2, GROUP_FIELDS.recognition)} question="Which printed captions are this line?"
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
        {fld("regex_hints", (e) => (
          <StringListEditor label="Regex hints — positive evidence" testid="regex_hints"
                            editable={editable} variant="mono"
                            help="Patterns that make a caption MORE likely to be this line. Each is
                                  checked when you save, and a
                                  broken one is named — a pattern
                                  that cannot compile would
                                  otherwise match nothing and look
                                  like a caption problem."
                            value={g("regex_hints", item.regex_hints)}
                            onChange={(v) => patch({ regex_hints: v })}
                            error={e} indexErrors={idx("regex_hints")}
                            inherited={inh("regex_hints", item.regex_hints)} />
        ))}
        {fld("keyword_hints", (e) => (
          <StringListEditor label="Keyword hints" testid="keyword_hints" editable={editable}
                            help="Words that count towards a caption matching this line, without
                                  having to write a full pattern."
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
      </Group>

      {/* ── 3. THE GATE ──────────────────────────────────────────────────────────────────────
          WHERE a caption may be claimed from. Hundreds of the set's normalised captions are
          claimed by more than one line item and some of those claims span different statements,
          so the gate is what settles which line a caption reaches. Nearly all of it arrives by
          INHERITANCE from a `section_defaults` entry — hence the badges. */}
      <Group {...band(3, [...GROUP_FIELDS.gate,
                          ...(noteSource ? GROUP_FIELDS.noteSource : [])])}
             question="Where may it be claimed from?"
             note="The gate is authored once per section and claimed by `inherits`; editing a
                   gate field here overrides the section for this line only.">
        {fld("inherits", (e) => (
          <SelectField<string>
            label="Inherits its gate from" testid="inherits" editable={editable} nullable
            reason={lockReason} nullLabel="nothing — this line declares its own gate"
            help="The section this line belongs to. It supplies the statement and the banners this
                  line may be found under, so a line pointed at the wrong section
                  will not be found at all — and correcting this is usually the
                  fix."
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
        {fld("note_selection", (e) => (
          <SelectField<NoteSelection>
            label="Note order priority" testid="note_selection" editable={editable}
            reason={lockReason} options={vocab?.note_selections ?? []}
            labelOf={(o) => (o === "cited_first"
              ? "The note the report itself points to, first"
              : "Whichever note best matches this line")}
            help="When the statement prints “Note 14” beside this line, that is the preparer
                  telling you where the detail is — so by default note 14 is offered first and the
                  rest of the room is filled by the notes that best match this line. Across twelve
                  filings, 92 lines carry such a reference and 30 of them point at a note the
                  matching would not have offered at all. It is not free: only four notes are sent
                  per line, so on 6 of those 92 the cited note pushes out the weakest match. Choose
                  the second option for a line whose printed reference you know points at the wrong
                  disclosure."
            value={g("note_selection", item.note_selection) ?? "cited_first"}
            onChange={(v) => patch({ note_selection: v ?? "cited_first" })}
            error={e} />
        ))}
        {fld("llm_only_if_note_tagged", (e) => (
          <BoolField label="Only ask the model when the face prints a note reference"
                     testid="llm_only_if_note_tagged" editable={editable}
                     help="For a line that is only ever disclosed in a note. With no note
                           reference beside the row there is nothing for the model to read but the
                           caption, so no call is spent — and the line reports 0 (or an empty
                           value for a phrase or prose line) rather than staying blank, because
                           the filing not disclosing it is a different statement from us not
                           finding it. The figure any caption match had read is kept alongside, so
                           the substitution can be audited."
                     onText="No note reference beside the row means the line reports 0, and no
                             model call is spent on it."
                     offText="The line is filled from wherever it can be read, with or without a
                              note reference."
                     value={g("llm_only_if_note_tagged", item.llm_only_if_note_tagged)}
                     onChange={(v) => patch({ llm_only_if_note_tagged: v })}
                     error={e}
                     inherited={inh("llm_only_if_note_tagged", item.llm_only_if_note_tagged)} />
        ))}
        {fld("note_use", (e) => (
          <SelectField<LineItemNoteUse>
            label="What a cited note may be used for" testid="note_use" editable={editable} nullable
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
                     help="Which note this line is read from and which of its rows count.
                           Switching it off writes `null` — no note sourcing for this line."
                     onText="The controls below decide which note and which of its rows count —
                             by pattern, by scored terms, and for a figure stated only in a
                             sentence."
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
                                help="Which rows inside the note count towards this figure. A row
                                      the note prints but no
                                      pattern here matches
                                      contributes nothing, so
                                      widen this when a filing
                                      words a row differently."
                                value={noteSource.row_caption_any}
                                onChange={(v) => setNoteSource({ row_caption_any: v })}
                                error={e} indexErrors={idx("note_source.row_caption_any")} />
            ))}
            {fld("note_source.row_caption_none", (e) => (
              <StringListEditor label="Rows that must be EXCLUDED"
                                testid="note_source-row_caption_none"
                                editable={editable} variant="veto"
                                help="Rows inside the note that must NOT count, even if a pattern
                                      above matched them. Each
                                      is checked when you save
                                      — a broken pattern would
                                      otherwise exclude
                                      nothing and the figure
                                      would quietly include
                                      rows you meant to drop."
                                value={noteSource.row_caption_none}
                                onChange={(v) => setNoteSource({ row_caption_none: v })}
                                error={e} indexErrors={idx("note_source.row_caption_none")} />
            ))}
            {/* ── THE SEMANTIC HALF ────────────────────────────────────────────────────────────
                TERMS, NOT PATTERNS, and the distinction is the reason they are separate controls
                rather than more entries in the lists above. A pattern either fires or it does
                not; a term SCORES, so a phrasing nobody anticipated still ranks instead of
                silently matching nothing. They are also the same TWO LEVELS as the patterns: a
                note's heading names the container, a row's caption names the content, and one
                vocabulary cannot do both jobs — measured, a single blended probe scored 0.000 on
                the note headed 管理費用 for a line whose every token is a depreciation word. */}
            {fld("note_source.note_terms", (e) => (
              <StringListEditor label="Words describing the NOTE this line sits in"
                                testid="note_source-note_terms" editable={editable}
                                help="Plain words, not regexes — scored against each note's
                                      HEADING. This names the container: the note a figure is
                                      disclosed inside (“property, plant and equipment”,
                                      “administrative expenses”), which is often nothing like the
                                      row's own wording. Leave it empty to score the line's label
                                      and description instead."
                                emptyText="Nothing said — the line's own label and description are
                                           scored against note headings instead."
                                value={noteSource.note_terms ?? []}
                                onChange={(v) => setNoteSource({ note_terms: v })}
                                error={e} indexErrors={idx("note_source.note_terms")} />
            ))}
            {fld("note_source.row_terms", (e) => (
              <StringListEditor label="Words describing the ROW inside that note"
                                testid="note_source-row_terms" editable={editable}
                                help="Plain words scored against the note's ROW CAPTIONS — the
                                      content rather than the container. These reach the model as
                                      well as the scorer, so a term here both ranks a row and
                                      tells the model what the line is called; a batch answer
                                      citing a caption that shares nothing with them is refused."
                                emptyText="Nothing said."
                                value={noteSource.row_terms ?? []}
                                onChange={(v) => setNoteSource({ row_terms: v })}
                                error={e} indexErrors={idx("note_source.row_terms")} />
            ))}
            {fld("note_source.row_terms_none", (e) => (
              <StringListEditor label="Words that RULE a row OUT"
                                testid="note_source-row_terms_none" editable={editable}
                                variant="veto"
                                help="The veto half of the scored route: a row whose caption
                                      carries one of these is not this line's, however well it
                                      scores otherwise."
                                emptyText="Nothing vetoed."
                                value={noteSource.row_terms_none ?? []}
                                onChange={(v) => setNoteSource({ row_terms_none: v })}
                                error={e} indexErrors={idx("note_source.row_terms_none")} />
            ))}
            {fld("note_source.prose_any", (e) => (
              <StringListEditor label="Sentences that state the figure in words"
                                testid="note_source-prose_any" editable={editable} variant="mono"
                                help="Regexes over a note's SENTENCES, for a figure the filing
                                      states in prose and tabulates nowhere — “Depreciation
                                      charges of approximately HK$529,841,000 … are included in
                                      other operating expenses”. Authored for sentence length, not
                                      caption length: in that footnote the gap between the two
                                      subjects is 87 characters, so a caption pattern's `.{0,40}`
                                      does not reach. EMPTY MEANS NO PROSE ROUTE — a line with
                                      nothing here yields no prose figure rather than a guess, and
                                      prose is only ever consulted where the row route found
                                      nothing."
                                emptyText="No prose route for this line."
                                value={noteSource.prose_any ?? []}
                                onChange={(v) => setNoteSource({ prose_any: v })}
                                error={e} indexErrors={idx("note_source.prose_any")} />
            ))}
          </div>
        )}
      </Group>

      {/* ── 4. STRUCTURE ─────────────────────────────────────────────────────────────────────
          The tree, and what a parenthood ASSERTS. `rollup` exists because the rollup check would
          otherwise have summed the twelve alternative restatements of the depreciation line. */}
      <Group {...band(4, GROUP_FIELDS.structure)}
             question="How does it sit among the other lines?">
        {fld("parent", (e) => (
          <KeyPicker label="Part of" testid="parent" editable={editable} reason={lockReason}
                     options={keys} exclude={[item.key]}
                     help="The line this one is a component of — its figure can then feed that
                           line's total, and it appears nested underneath
                           it. Leave it empty for a line that stands on
                           its own. A line cannot be part of itself, or
                           of something that is already part of it."
                     value={g("parent", item.parent) || null}
                     onChange={(v) => patch({ parent: v })}
                     error={e} inherited={inh("parent", item.parent)} />
        ))}

      </Group>


      {/* ── 5. ASSEMBLY ──────────────────────────────────────────────────────────────────────
          BOTH BLOCKS STAY ON SCREEN, the current type's expanded and the other collapsed. Hiding
          the one that does not apply is how a type change makes a group vanish and an author
          concludes the field was taken away — and both are needed while a line is being moved
          from one type to the other. */}
      <Group {...band(5, GROUP_FIELDS.assembly)} question="How is its figure obtained?"
             note="A calculated or intermediate line is a signed sum of terms; a derived line is
                   an ordered cascade of attempts, the first that resolves winning.">
        {fld("type", (e) => (
          <SelectField<LineItemType>
            label="Type" testid="type" editable={editable} reason={lockReason}
            help="How this line gets its figure, and therefore which sections below apply: read off
                  the page, added up from parts, or worked out by a rule. A line
                  that says it is calculated but lists no parts cannot produce
                  anything, so that is refused when you save."
            // THE THREE THE SPEC KEEPS, intersected with what the server will accept. Filtered
            // rather than replaced: a deployment whose vocabulary lacks one of them must not be
            // offered it, and one that still serves `intermediate` must not show it.
            options={(vocab?.types ?? []).filter((t) => (SPEC_TYPES as readonly string[])
              .includes(String(t)))}
            labelOf={(v) => TYPE_TONE[v].label}
            helpOf={(v) => ({
              extracted: "read off a printed caption — the only type the model is ever asked about",
              calculated: "summed from its components, which the template's rollup names. Computed "
                        + "whether or not the filing prints the subtotal; a printed figure that "
                        + "disagrees is recorded as a finding rather than used",
              derived: "assembled by the cascade below — ordered rungs over its own parts. Reached "
                     + "by nothing else: no caption, no alias, no semantic probe, and never the "
                     + "model",
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
        <details open={type === "calculated" || type === "intermediate"}>
          <summary style={{ cursor: "pointer", fontSize: 11.5, fontWeight: 600,
                             color: color.ink2, marginBottom: 9 }}>
            The signed sum — for a <b>calculated</b> or <b>intermediate</b> line
          </summary>
          {fld("terms", (e) => (
            <TermRows label="Terms" testid="terms" editable={editable}
                      help="The parts this line is added up from, each with its sign. Every part
                            must name a line that exists — a mistyped
                            name is refused when you save rather than
                            becoming a part that contributes nothing."
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
                       help="Names the built-in rule that works this figure out today, where the
                             settings above only describe it. Clear it
                             to have the rungs above used instead — but
                             a derived line needs one or the other."
                       value={g("implemented_by", item.implemented_by)}
                       onChange={(v) => patch({ implemented_by: v })} error={e} />
          ))}
        </details>
      </Group>

      {/* ── 8. LOCKED ────────────────────────────────────────────────────────────────────────
          NOT AUTHORABLE ≠ ABSENT. The entries and their reasons come from
          `vocab.not_editable` — served rather than restated here, so a field cannot quietly
          disappear from the form with no reason attached, and so a reason this screen does not own
          has exactly one spelling. */}
      {/* THE ONE GROUP WHOSE COUNT IS NOT A FIELD WALK: these rows are served
          (`vocab.not_editable`), not filtered, so the count is simply how many there are. */}
      <Group index={8} count={Object.keys(vocab?.not_editable ?? {}).length}
             open={!shut.g8} onToggle={() => toggleGroup("g8")}
             question="Not editable here"
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
  // THE LINES A REQUEST GROUP MAY NAME — the ones the model is actually asked about. Mirrors
  // `services.line_item_requests.asked_about`, and the server refuses a group naming anything
  // else, so filtering here keeps the picker from offering a member the save would reject.
  // A `derived` parent's figure is its declared cascade's and a residual bucket's is the sweep's.
  const groupable = flat.filter(
    (d) => String(d.type) !== "derived" && String(d.value_scope) !== "exclusive_residual");
  const groupableKeys: KeyOption[] = groupable.map((d) => ({ key: d.key, label: d.label }));

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

      {/* THE MASTER PROMPT — one per configuration, not per line.
          Sent on EVERY mapping call, appended to the framework's fixed reply contract and ahead of
          the global policies, so it is the deployment's standing instruction on how captions should
          be read. A line's own prompt is added on top of this rather than replacing it.
          Collapsed by default: it is set once and then rarely, and open it would be a paragraph
          above every visit to this screen. The fixed contract — the reply shape and the citation
          rules the parser depends on — is deliberately NOT here and is not editable. */}
      {inForce?.id && (
        <MasterPrompt versionId={inForce.id} served={set.prompt ?? ""} canEdit={canEdit} />
      )}

      {/* WHICH LINES SHARE A MODEL REQUEST. Set-level for the same reason the master prompt is: a
          group is a relationship BETWEEN line items, so a per-item field would let two lines
          disagree about which group they are in and there would be nowhere to read the grouping
          off. Read only when the run's grouping mode is `manual` (Settings -> LLM); the card says
          so, because a master nothing reads is worse than no master. */}
      {inForce?.id && (
        <RequestGroups versionId={inForce.id} set={set} eligible={groupableKeys}
                       ineligible={flat.length - groupable.length} canEdit={canEdit} />
      )}

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
