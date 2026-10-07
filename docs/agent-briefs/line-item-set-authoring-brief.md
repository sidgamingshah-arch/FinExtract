# Brief: author the line-item set ("ontology") for a new output template

You are building the **configuration** that lets FinEx extract a new output template from
financial-statement filings. In this product that configuration is called a **line-item set**.
Older documents and people call it the "ontology". The old `*_ontology.json` files and the
"rulebook" (`*_rules.json`, `*_validation.json`) are **legacy**: no run reads them, so do not
produce them.

This brief has four parts:
- what the set is for, and how the pipeline uses each part of it (§1–§3);
- the exact file structure, field by field (§4);
- the patterns to copy (§5);
- the rules your output must pass, the pitfalls the code records, and what to hand back
  (§6–§9).

Paths are relative to the repository root. The three shipped sets are your working examples:

| | HK (HKFRS / CAS filings) | Ind AS (Schedule III filings) | ICON (Indian bank CMA spread) |
|---|---|---|---|
| Template | `backend/app/sample/templates/output_csv_hk_v1_template.json` | `backend/app/sample/templates/output_csv_indas_v1_template.json` | `backend/app/sample/templates/output_csv_icon_v1_template.json` |
| Line-item set | `backend/app/sample/templates/output_csv_hk_line_items.json` | `backend/app/sample/templates/output_csv_indas_line_items.json` | `backend/app/sample/templates/output_csv_icon_line_items.json` |
| Size | 480 keyed nodes (18 section headers + 462 figure lines); 529 items (462 template + 67 internal) | 211 nodes (13 headers + 198 figure lines); 256 items (198 + 58) | 199 nodes (17 headers + 182 figure lines); 182 items, no internal parts |

ICON is the closest example of a set made from a client workbook: `backend/scripts/build_icon_pair.py`
builds both of its files from the workbook's own formulas and a curated caption file
(`backend/scripts/sample_data/icon/captions.json`). Edit those inputs and rebuild; do not hand-edit
the two built files.

The schema lives in `backend/app/schemas/line_items.py`. With the app running,
`GET /api/v1/line-items/schema` returns it as JSON Schema plus a `field_help` text for every
field. Use that as your machine contract.

---

## 1. What the product does, and where your file fits

FinEx reads an annual report (native PDF, scanned PDF, or Excel). It finds the statement
**face** pages and the **notes**, and rebuilds each printed table into rows. It then puts every
figure into a fixed **output template**: a spreadsheet layout of statements, sections and lines,
each line with a `canonical_key` such as `bs_ca__inventories`. Every figure keeps a link to the
page and box it came from.

The **template** says *what* is printed in the output. The **line-item set** says *how to find
each line in a filing*:
- which printed captions mean this line;
- which note and which row or column holds it when the face does not print it;
- how to compute it from other figures;
- what the model is told about it.

```
filing ──► rows rebuilt from pages ──► caption matching (aliases, hints, gate) ─┐
                                       residual sweep into "Others" lines ──────┤
                                       LLM: "which printed row holds line X?" ──┼──► template
                                       note reading (note_source parts) ────────┤    figures
                                       cascades / terms / template rollups ─────┘
                                       structural checks (rollups, identities, section totals)
```

Pipeline order (`backend/app/core/pipeline.py`): extract → **map** (captions) → **residual sweep**
→ normalise signs → link notes → **LLM line-item questions** → **note-sourced parts and
cascades** → … → **structural checks**.

The residual sweep runs *before* any note route or cascade fills a derived parent.

---

## 2. Inputs you will be given

1. **The new template** as JSON (a `TemplateDefinition`). If it is a workbook, it is first
   imported by `backend/app/services/template_xlsx.py`. Treat it as **fixed**: you do not add,
   rename or remove printed keys (§6.1).
2. **Sample filings** of the regime the template targets, if available. You need them to choose
   real printed captions and note headings, and to measure (§8).
3. The two shipped sets above, as worked precedent.

If the template is not yet in the system, it must be published first (`POST /api/v1/templates`).
Publishing **auto-provisions a skeleton set**:
- every figure line gets `{key, label, namespace: "template", type: "extracted", in_output: true,
  inherits: <its section header>}`;
- every section header gets `section_defaults[<header>] = {section_scope: [<header>], statement}`.

That skeleton has **no aliases**, so it recognises nothing, yet it is "the newest set" and so in
force for the template. Your set replaces it.

---

## 3. How each part of a line item is used at run time

Know this before you write a field: much of the schema is consumed in ways the field name does
not suggest.

### 3.1 Caption matching: the face

Source: `backend/app/services/mapping.py`, `backend/app/stages/map_ontology.py`.

**Step 1: normalisation.** Both the aliases and the printed caption are normalised the same
way:
- note citations, `16(a)` markers and bracketed numbers are stripped;
- Traditional Chinese is folded to Simplified;
- CAS sign notes `（亏损以"－"号填列）` and CAS line prefixes (`一、` `1、` `（一）` `其中：` `加：`
  `减：`) are stripped;
- the text is lower-cased, and punctuation becomes spaces.

A bilingual caption ("REVENUE 收益") is also tried as each half.

**Step 2: the tiers, in order.**
1. **Exact alias.** If the normalised caption equals a normalised alias of a line that passes
   the gate (below), it matches at confidence 1.0. Ties are broken by: label owner first, then
   higher `match_priority`, then declaration order.
2. **Rule tier.** `regex_hints`, or `keyword_hints` (every keyword must appear as a substring).
   One claimant scores 0.95 and is accepted. Several claimants score 0.6 and go to review.
3. **Nothing else.** There is **no fuzzy, embedding or semantic tier**. A caption that matches
   no alias or hint is unmapped. It then goes to the residual sweep, or to review.

**Step 3: the gate.** All four conditions must hold before a line can claim a caption:
- **Statement:** the page's statement is in the line's `statements` (empty means any).
- **Section:** the banner the row sits under is in the line's `section_scope` (empty means any).
- **`exclude_hints`:** none of these regexes matches the **raw** printed caption. A veto
  outranks the line's own alias.
- **Exclusive class:** a caption naming exactly one of operating / investing / financing cannot
  go to a line of the other two.

**Step 4: after a match.**
- A line with `route: note_tables` or `prose` refuses face rows.
- Containment rules (`is_gross_parent` / `children_if_decomposed`,
  `global_rules.mutually_exclusive_groups`) stop a parent and its children both being filled
  from the same printed figures.

### 3.2 The residual sweep: "Others" lines

Source: `backend/app/stages/residual.py`.
- Each section may have **one** catch-all line, listed in `others_master.keys`. Printed rows in
  that section that no line claimed are summed into it, itemised.
- **Two catch-alls in one section means neither sweeps.**
- The **prose** in `residual_framework` is executed by keyword: eligibility, prohibitions and
  review triggers. Rewording or deleting a sentence switches its guard off. Copy the shipped
  block and change only what you mean to change.
- The section's closing subtotal is found as the calculated line with the **highest
  `match_priority`** in that section. Give each section's closing total the highest priority
  among its calculated lines.

### 3.3 The model: what it is asked and what it sees

Source: `backend/app/stages/line_item_llm.py`, `backend/app/services/line_item_llm.py`.
- **The question.** For each line it is asked about, the model answers "which printed row
  (face row, note row, or other page) holds this line's figure". It answers with citations
  only, never amounts (prose amounts excepted, and those are verified).
- **What is sent per line:** `key`, `label`, **`definition`**, `exclude_criteria`, the statement
  it is printed in, its `route`, `sign_convention`, and the notes selected for it.
  - **Aliases, hints, regexes and `note_source` are NOT sent.** If the model needs to know a
    spelling or a distinction, write it in `definition`.
- **The system prompt** is the product's fixed contract plus:
  - the set's `prompt`;
  - the `global_rules` policy texts;
  - up to 6 `worked_examples`.
- **Lines never asked:** `type: derived`, `value_scope: exclusive_residual`, and
  `extraction_mode: derive`.
- **Deployment gate.** `backend/config.toml` ships `llm_focus_only = true` with a list
  `llm_focus_keys`. Only lines named there are asked. **A new set's lines get zero model
  questions** unless their keys are added there or focus is switched off. That is a deployment
  setting, not part of your file. Report it (§9).

### 3.4 Note reading: `note_source` on parts

Source: `backend/app/services/note_sourced.py`, `line_item_notes.py`, `note_sections.py`.

A line whose figure lives in a note (or is a component of one) is read by a **part**: a `sub__`
item with a `note_source`. The reader works in three steps.

**Step 1: find the note.** Either:
- the note title matches a `note_title_any` regex; or
- **by meaning:** with no `note_title_any`, the note's heading scores against `note_terms`
  (IDF-cosine, at most 4 notes per line). One note goes to one sibling part.

**Step 2: pick the rows.** The row caption must match `row_caption_any`, and must match no
`row_caption_none` regex (the veto runs last).
- `row_caption_any: ["\\S"]` means "sum the note's line items".
- Matching is case-insensitive and not Han-folded, so write both scripts.
- A note needs **both** a title (or `note_terms`) **and** `row_caption_any`; either one missing
  gives no rows.

**Step 3: pick the column.** By period, by default. Other options:
- `measure` reads a second-measure column (e.g. `allowance` for 坏账准备).
- `from_measure_grid` requires or forbids a two-level header grid.
- `column_heading_any` / `column_heading_none` select a column by its **printed heading** (for
  example "Level 3", "第三层次"). A figure whose column cannot be named fails closed. Two different
  figures for one slot are refused.

**Precedence.** The model's citation wins where it answered. Parts are tried in `order` (this is
the precedence for `rollup: alternatives`).

`section_scope` on a note-reading part narrows **which notes** it may read, not where it
publishes. Do not pin a note part to a face section unless you mean it.

### 3.5 Arithmetic: cascades, terms, rollups

Source: `backend/app/services/line_items.py`, `backend/app/stages/note_sourced.py`,
`backend/app/services/rollups.py`.

**Calculated lines.** Totals and subtotals are computed from the **template's** `rollup`. The
set's `terms` must mirror that rollup exactly (§6.3).

**Derived lines** carry a `cascade`: an ordered list of rungs.
- **The first rung that resolves wins.**
- Within a rung, terms have roles:
  - `required`: if missing, the rung fails;
  - `any_of`: at least one must be present, and the present ones combine by `terms_op`
    (`sum` | `max` | `min` | `first`);
  - `adjustment`: a signed add-on applied after the base. It never resolves a rung on its own.
- `refuse_negative` (default true) skips a rung that comes out below 0.
- `const` is a fixed number in place of `ref`.
- **A printed face figure is kept** unless the winning rung has `outranks_printed: true`.
- `carved_from_face: true` means the figure is already inside other face captions. It is shown
  on its own line but not added to the section total again.

**Parents without a cascade** combine their children by `rollup`:
- `sum`;
- `alternatives`: first in `order`, never summed;
- `none`: nothing carried up.

### 3.6 Checks

Source: `backend/app/services/structural_checks.py`. Evaluated per (basis, period):
- template rollups and identities;
- the set's `validation.identities` (`lhs = a + b - c` over canonical keys);
- `validation.cross_concept_guards`;
- **section reconciliation**.

`cross_check_master.keys` names the calculated lines whose printed subtotal is read off the
filing and compared with the computed one.

**Warning.** Declaring a `validation` block with an empty `section_reconciliation` **switches
section reconciliation off.** Leave `validation` out unless you fill it.

---

## 4. File structure

One JSON object. Top-level keys, in the order the shipped files use:

```jsonc
{
  "schema_version": 1,
  "line_items_key": "output_csv_<new>",          // REQUIRED; must not reuse a shipped key
  "target_template_key": "<template_key>",        // must equal the template's template_key
  "target_template_version": null,
  "locale": "en",
  "supported_locales": ["en"],                    // subset of en, zh, ar, fr
  "metadata": {"name": "...", "version": "1", "changes": ["..."], "vocabulary_note": "..."},
  "section_defaults": { "<section header key>": { ... } },   // §4.2
  "vocabulary": { ... },                          // copy the shipped block (§7)
  "normalisation": { ... },                       // DECLARE IT (§7)
  "binding": { ... },                             // copy the shipped block
  "prompt": "jurisdiction-specific guidance for the model",
  "global_rules": { ... },
  "scope_selection": { ... },                     // DECLARE IT (§7)
  "residual_framework": { ... },                  // copy, change only on purpose (§3.2)
  "items": [ ... ],                               // §4.3
  "others_master": {"note": "...", "keys": ["<one catch-all per section>"]},
  "cross_check_master": {"note": "...", "keys": ["<calculated lines>"], "tolerance": 0.5},
  "prose_grammar": {"note": "...", "connective": [...], "subjects": {"depreciation": [...]}}
}
```

Optional blocks, declared by neither shipped set: `request_groups`, `decomposition_rules`,
`netting_rules`, `worked_examples`, `validation`, `number_format_by_locale` (defaults to western
grouping; set `{"grouping": "indian"}` for lakh/crore grouping if needed).

**Unknown keys are refused at publish** (422 with the JSON path). The one tolerated stray key is
`items[*].note_use_rationale`.

### 4.1 Closed vocabularies

| Field | Values |
|---|---|
| `type` | `extracted` (default) \| `calculated` \| `intermediate` \| `derived` |
| `statement` / `statements` | `statement_setup`, `balance_sheet`, `profit_and_loss`, `cash_flow`, **`equity_changes`** (not `changes_in_equity`), `covenants_supplemental`, `notes` |
| `route` | `face` \| `note_tables` \| `prose` \| `anywhere` \| omitted (permissive) |
| `namespace` | `template` (a printed column) \| `internal` (a part) |
| `rollup` | `sum` (default) \| `alternatives` \| `none` |
| `terms_op` | `sum` \| `max` \| `min` \| `first` |
| term `role` | `required` (default) \| `any_of` \| `adjustment` |
| `extraction_mode` | `extract` (default) \| `extract_or_derive` \| `derive` \| `do_not_extract` |
| `value_scope` | `exclusive_leaf` (default) \| `exclusive_child` \| `exclusive_residual` \| `not_applicable` |
| `temporality` / `unit_of_account` | `instant` \| `duration` / `balance` \| `flow` \| `subtotal` |
| `sign_convention` | `positive_expected` \| `negative_expected` \| `either` |
| `note_use` | `evidence_only` \| `decomposition_allowed` |
| `note_selection` | `cited_first` (default) \| `any` |
| `analyst_bucket` | `current_assets`, `non_current_assets`, `current_liabilities`, `non_current_liabilities`, `equity`, `income`, `expenses`, `interest`, `non_operating`, `cash_flow_operating`, `cash_flow_investing`, `cash_flow_financing`, `changes_in_equity`, `others` |

### 4.2 `section_defaults`: one entry per template section header

Items name their section with `inherits`; the entry is folded into the item at load.
- **Keys must equal the template's section header `canonical_key`s.**
- **An item's own key wins, including an explicit `null` or `[]`.** To inherit a value, leave
  the key out.

Fields: `statement`, `section_scope`, `scopes`, `side`, `temporality`, `unit_of_account`,
`note_use`, `note_use_rationale`, `sign_convention`, `match_priority`, `face_only`,
`analyst_bucket`.

```json
"bs_nca": {"statement": "balance_sheet", "section_scope": ["bs_nca"], "temporality": "instant",
           "face_only": true, "note_use": "decomposition_allowed",
           "sign_convention": "positive_expected", "match_priority": 81}
```

Non-balance-sheet sections are only reconciled if their `sign_convention` is
`positive_expected`.

### 4.3 An item: the fields that matter

**Identity**
- `key`, `label`, `type`, `namespace`, `in_output`, `parent`, `order`, `rollup`, `inherits`.

**Gate**
- `statements` (a list; the singular `statement` is folded in only when `statements` is empty).
- `section_scope`, `match_priority`, `exclude_hints` (regex vetoes on the raw caption).
- `alias_matching` (`disabled` locks the line out of caption matching).

**Recognition**
- `aliases`: printed captions, bare (§6.4).
- `aliases_i18n`: `{"en": [...], "zh": [...]}`. Every locale is indexed for every document.
- `keyword_hints`: plain words; every one must appear.
- `regex_hints`, `pattern`: regexes that must compile.

**For the model**
- **`definition`**: the accounting meaning, what to include and exclude, the spellings to expect.
- `exclude_criteria`: prose exclusions.

**Where to read**
- `route`, `note_source`, `note_selection`, `llm_only_if_note_tagged`, `scopes`, `face_only`.

**Arithmetic**
- `terms` + `terms_op` (calculated / intermediate lines), `cascade` (derived lines).

**Measurement**
- `temporality`, `unit_of_account`, `sign_convention`.
- `sign_rule`: `{"convention": ..., "flip_if_label_matches": [regex]}`.
- `analyst_bucket`.

**Containment**
- `is_gross_parent` + `children_if_decomposed`, `sole_component_of`.

**Residuals**
- `never_sweep`, `expected_components` (prose; no keys, no `|`).

`note_source` fields:

| Kind | Fields |
|---|---|
| Regexes (must compile) | `note_title_any`, `row_caption_any`, `row_caption_none`, `column_heading_any`, `column_heading_none`, `prose_any` |
| Plain terms | `note_terms`, `note_terms_none`, `row_terms`, `row_terms_none`, `prose_landed_in` |
| Other | `measure`, `from_measure_grid`, `prose_subject` |

**Fields that do NOT exist on a line item**, and are refused as stray keys: `label_i18n` (it
lives on the template node only), `description`, `confusable_with`, `section_disambiguation`,
`include_criteria`, `min_confidence_to_auto_accept`, `caption_normalization`.

**Refused at load:**
- `note_source` on a `derived` line.
- `prompt`, `note_selection: any` or `llm_only_if_note_tagged` on a line that is never asked.
- An `intermediate` line without terms.
- A `derived` line without a cascade.
- A regex that does not compile.
- An `exclude_hints` pattern that matches the line's own alias.

---

## 5. Patterns to copy

Shortened from the HK file: the real items carry more aliases, terms and longer notes. Open
the file for the full versions.

### 5.1 A plain face line

```json
{"key": "bs_nca__buildings", "label": "Buildings", "inherits": "bs_nca",
 "namespace": "template", "route": "face",
 "definition": "Buildings and leasehold land and buildings as printed under non-current assets. Not land use rights or prepaid land lease payments.",
 "aliases": ["Buildings", "Land and buildings", "Leasehold land and buildings"],
 "aliases_i18n": {"en": ["Buildings", "Land and buildings", "Leasehold land and buildings"],
                  "zh": ["房屋及建筑物", "房屋及建築物"]},
 "exclude_hints": ["^Land\\s+use\\s+rights$", "^土地使用权$"]}
```

The gate (statement and section) comes from `inherits`. Repeat the label in `aliases` if the
filing prints it verbatim: **the label itself is not an alias.**

### 5.2 A calculated line: mirror the template rollup

```json
{"key": "is_pl__gross_profit", "type": "calculated", "inherits": "is_pl",
 "namespace": "template", "extraction_mode": "extract_or_derive",
 "aliases": ["Gross profit"],
 "terms": [{"ref": "is_pl__sales_revenues", "sign": 1},
           {"ref": "is_pl__total_cost_of_sales", "sign": -1, "abs": true}]}
```

The rules for `terms`:
- They must equal the template node's `rollup.children`, **in the same order**.
- Every term must declare its `sign`.
- `abs: true` with `sign: -1` goes on exactly the rollup's `cost_magnitude_children`.
- A residual catch-all carries no `terms`.

### 5.3 A derived line read from note parts, with a face fallback

```json
{"key": "bs_ca__trade_receivables_related_parties", "type": "derived", "rollup": "alternatives",
 "inherits": "bs_ca", "namespace": "template", "route": "face",
 "cascade": [
  {"id": "NET_OF_ALLOWANCE", "carved_from_face": true,
   "terms": [{"ref": "sub__rp_trade_receivable_gross", "role": "required", "sign": 1},
             {"ref": "sub__rp_trade_receivable_allowance", "role": "adjustment", "sign": -1}],
   "note": "The note publishes gross and allowance; the line is the net."},
  {"id": "FROM_THE_FACE",
   "terms": [{"ref": "sub__rp_face_trade_receivable", "role": "required", "sign": 1}],
   "note": "Fallback to the face row, last, so the note rung is never displaced."}]}
```

**A derived parent carries NO aliases.** Its aliases would make the printed row be refused and
re-roled as a subtotal. Put the captions on a **face part** instead:

```json
{"key": "sub__rp_face_trade_receivable", "parent": "bs_ca__trade_receivables_related_parties",
 "namespace": "internal", "in_output": false, "route": "face",
 "inherits": "bs_ca", "statement": "balance_sheet", "section_scope": ["bs_ca"], "order": 16,
 "aliases": ["Trade receivables from related parties"],
 "aliases_i18n": {"en": ["Trade receivables from related parties"], "zh": []}}
```

### 5.4 A note part

```json
{"key": "sub__rp_trade_receivable_gross", "parent": "bs_ca__trade_receivables_related_parties",
 "namespace": "internal", "in_output": false, "route": "note_tables",
 "statement": "notes", "section_scope": [], "order": 14, "note_selection": "any",
 "definition": "Trade receivables owed by related parties in the related-party balances note, before the loss allowance (账面余额). Not other receivables, prepayments or totals.",
 "note_source": {
   "note_terms": ["related party balances", "related party transactions", "关联方交易", "關聯方交易"],
   "row_caption_any": ["^\\s*(?:trade|accounts?)\\s+receivables?", "^\\s*(?:应收账款|應收賬款)"],
   "row_caption_none": ["^\\s*(?:sub)?total|合计|合計|小计|小計", "allowance|坏账准备|壞賬準備"],
   "row_terms": ["receivables", "应收账款"],
   "from_measure_grid": true}}
```

The allowance twin is identical apart from `"measure": "allowance"`.

To select a printed column, add `"column_heading_any": ["level\\s*3|第三层次|第三層次"]`.

### 5.5 A residual catch-all

List one key per section in `others_master.keys`. The set then forces the line to
`value_scope: exclusive_residual` and gives it a sweep policy. Give it **no** `terms` and **no**
cascade.

### 5.6 More cascade shapes in the HK file

| Line | Shape |
|---|---|
| `bs_nca__due_from_related_parties_ltp` | One rung, `terms_op: max` over three `any_of` parts |
| `bs_ca__secur_and_other_fincl_assets_cp` | Find 1 − Level 3, then `CP_ZERO` = `min(const 0, residual)` with `refuse_negative: false` |
| `is_pl__deprec_and_impairment_oper_exp` | Seven rungs, P1–P7, in falling order of preference |
| `sub__ga_depreciation` | A prose part (`route: prose`, `prose_subject`, `prose_landed_in`) |

---

## 6. Rules your output must pass

### 6.1 The template boundary

- **Every** figure-bearing template node (role line / subtotal / total) has exactly one item
  with that `key`, `namespace: "template"` and `in_output: true`.
  - Section headers get a `section_defaults` entry, not an item.
- **Any other key** must start with `sub__`, be `namespace: "internal"` and
  `in_output: false`, and be **wired in**: named in its parent's `cascade` or `terms`, or under
  a parent that combines by `rollup`. Otherwise its figure reaches nothing.
- **Never invent a printed key.** If a filing prints something the template has no line for,
  either route it to that section's "Others" line or list it as *unplaced* in your report
  (§9). Adding a column is a product decision.

### 6.2 The gate

- **Any item that has aliases must resolve to a non-empty `statements` AND a non-empty
  `section_scope`.** That includes what it inherits. Otherwise a caption could bind it from any
  statement.
- `section_scope` ids must be ones the run's matcher recognises:
  - a standard section key: `bs_nca`, `bs_ca`, `bs_ncl`, `bs_cl`, `bs_equity`, `is_pl`,
    `is_oci`, `is_retained`, `cf_oper_indirect`, `cf_oper_direct`, `cf_investing`,
    `cf_financing`;
  - or an id ending in a banner token: `non_current_assets`, `current_assets`,
    `non_current_liabilities`, `current_liabilities`, `equity`, `income`, `expenses`,
    `income_and_expenses`, `other_comprehensive_income`, `cash_flow_from_operating_activities`,
    `cash_flow_from_investing_activities`, `cash_flow_from_financing_activities`, `tax_expense`,
    `exceptional_items`, `non_operating_expenses`, `notes`, …
  - **An unrecognised id silently becomes "no section constraint" at run time.** The validator
    still accepts it. If the new template uses its own section keys, say which standard section
    each maps to, and flag it.
- Note-reading parts normally carry `statement: "notes"` and `section_scope: []`. Pin them to a
  face section only on purpose.

### 6.3 Arithmetic

- Calculated `terms` equal the template rollup children, in order, with signs as in §5.2.
- Terms and cascade `ref`s name existing keys.
- No cycles; no dangling `parent`.
- **One residual catch-all per section.**
- The section's closing total has the highest `match_priority` among its calculated lines.

### 6.4 Aliases are BARE printed captions

- **No CAS decoration.** Not `加：营业外收入`, `其中：营业收入`, `一、营业总收入`, `（一）…`, or
  `营业利润（亏损以"－"号填列）`. Author `营业外收入`, `营业收入`, `营业利润`.
  `tests/test_pattern_overlap.py` refuses decorated labels and aliases.
- **No unbreakable ties.** The same alias on two lines with overlapping gates and equal
  `match_priority`, where neither owns it as its label, is refused
  (`tests/test_configuration_invariants.py`).
- **`keyword_hints` are broad.** One hit auto-accepts at 0.95. A keyword like "reserves" claims
  every reserve caption. Prefer exact aliases.
- **Raw-text consumers.** `exclude_hints`, every `note_source` regex, `note_terms` and
  `row_terms` see **raw** text: not normalised and not Han-folded. Write Chinese in both
  Simplified and Traditional, and anchor patterns knowing that prefixes and bilingual tails may
  be present.

### 6.5 Never delete a veto that can fire

Removing a `row_caption_none`, `row_terms_none` or `exclude_hints` entry *widens* what is
accepted. Only remove a veto you can show never fires on the corpus.

---

## 7. Pitfalls the code records

1. **Declare `scope_selection` and `normalisation`** in your set, written for the new regime:
   units, currency, entity markers ("Standalone", "Consolidated"), and the unit-caption literals
   the filings print (e.g. "(₹ in Crores)").
   - If they are absent, page reading falls back to the **HK** set's blocks.
   - Every `normalisation.pipeline` sentence must name a step that is implemented. The sentences
     are matched by keyword in `backend/app/services/row_reconstruct.py`.
2. **Copy `vocabulary` from the shipped set.**
   - Its `section_banners`, `scope_tokens`, `statement_prefixes` and `exclusive_vocabularies`
     are validated but are *not* read by the run's matcher.
   - `caption_characters` is installed once per process from the HK file only.
3. **`statement` spelling.** Use `equity_changes` in the set. Templates accept both spellings,
   but `changes_in_equity` in a line-item set is refused.
4. **The template is two levels deep.** A section and its direct children; rollups deeper than
   that are never computed. Keep `node_id == canonical_key`.
5. **`side` and `allow_contra` have no reader.** Do not rely on them.
6. **`children_if_decomposed` without `is_gross_parent` does nothing.**
7. **Code comments carry stale counts** (475 / 539 / 549 items, Ind AS "276"). Trust the files.
8. **The deployment's `llm_focus_keys`** must name the new keys, or no line is asked (§3.3).

---

## 8. How to check your work

Run these from `backend/`:

```bash
# 1. The strictest check: template boundary, gate, orphan parts, registry, locales, rollups.
python scripts/validate_reference_data.py --dir <folder holding your template + set> --errors-only

# 2. Publish it (needs an admin token). The response carries validated_against_template.
#    A 422 names each offending path.
curl -X POST http://127.0.0.1:8000/api/v1/line-items \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"definition": <your set>}'

# 3. The arithmetic and pattern gates (add the new pair to REGIMES in test_formula_in_config.py).
pytest -q tests/test_formula_in_config.py tests/test_pattern_overlap.py \
          tests/test_configuration_invariants.py tests/test_line_item_gate.py
```

**If the pair is to ship as files** (seeded at boot), you must also:
1. register it in `backend/app/sample/reference.py` `_EXTRA_PAIRS`;
2. classify both files in `scripts/validate_reference_data.py` `FILE_ROLES`;
3. add a row to `backend/app/sample/templates/README.md`;
4. follow `tests/test_the_ind_as_set.py`, the precedent for a second set's own tests.

**Measure on real filings.** Run the pipeline on the sample filings of the regime. For each
filing report:
- which template lines were filled, and against what the filing **prints**;
- which reconciliations pass or fail;
- which printed captions reached no line.

"No test failed" is not the same as "the figures are right": most figures are not pinned by a
test.

---

## 9. What to hand back

1. **`<line_items_key>_line_items.json`**: the set, passing §8 step 1 with 0 errors.
2. **A coverage report**, as Markdown:
   - **Counts:** template lines, items (template / internal), and by type (extracted /
     calculated / derived), routes, parts with a `note_source`, and lines with aliases.
   - **Section mapping:** each template section header → the section token it resolves to,
     with any that resolve to nothing flagged.
   - **Lines without aliases or a note route:** what you expect to fill them (model, cascade,
     rollup), or that they stay blank.
   - **Unplaced captions:** printed captions the template has no line for, and where they go
     (an "Others" line, or "needs a product decision").
   - **Assumptions and open questions:** e.g. sign conventions, entity basis, units.
   - **Deployment actions you cannot take:** the `llm_focus_keys` additions, and whether the
     pair should ship as files.
   - **Measurement results** per filing (§8), if filings were available.
3. **Nothing else changed.** Do not edit the template, the shipped sets, `backend/config.toml`
   or code. If something is needed there, say so in the report.

### Authoring order that works

1. Read the template. List its sections, figure lines, rollups and `cost_magnitude_children`.
2. Write `section_defaults`, one per header, with the statement, section and sign expectation.
3. Provision one `template` item per figure line, with `inherits`, `label` and `definition`.
4. Calculated lines: `terms` from the rollups (§5.2).
5. Face lines: bare aliases from real filings, in every language the filings print, plus
   `exclude_hints` where a near-caption belongs elsewhere.
6. Lines the face does not print: decide the source.
   - A note part (§5.4), with a cascade on a `derived` parent (§5.3) when it is computed from
     parts;
   - otherwise leave it to the model with a precise `definition`.
7. One catch-all per section in `others_master`.
8. The set-level blocks: `prompt` for the jurisdiction, `scope_selection`, `normalisation`, and
   `residual_framework` / `binding` / `global_rules` copied and adapted.
9. Validate (§8), fix, measure, and write the report (§9).
