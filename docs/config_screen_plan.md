# Line Items config — the plan to close it out

The brief was "the config screen is too heavy for a user". Walking all 69 controls field by field
produced a different diagnosis than "too many fields", and that diagnosis is what this plan is
shaped around.

Every number here is measured against the shipped `output_csv_hk_line_items.json` (539 lines) and,
where behaviour is involved, against a real filing. Front-end labels are given first with the
backend field in brackets, because the two rarely resemble each other.

---

## What is actually wrong

**1. Three populations wear one form.** The 69-control form is drawn for every line, but the lines
divide cleanly into three groups that use almost disjoint halves of it:

| population | n | how its figure is obtained | fields it needs |
|---|---|---|---|
| caption-matched wholes | 420 | a caption on a statement face | recognition + the gate |
| formula lines | 42 | arithmetic over other lines | a formula, and nothing else |
| note-read parts | 77 | located inside a note | note source + prompt |

`note_source` is the honest discriminator: present on exactly the 77 parts and **zero** of the 462
wholes. `type` is not — 32 of 33 `calculated` and 6 of 9 `derived` lines are caption-reachable.

**2. Most of the prose was generated, not authored.** Three fields, three ways:

| field | authored | generated |
|---|---|---|
| Counts as this line (`include_criteria`) | 66 | **375** restate the label |
| Does NOT count as this line (`exclude_criteria`) | 69 | **393** are one identical sentence |
| Which of two look-alike captions this is (`section_disambiguation`) | — | **13 distinct values over 395 lines** |

So the form was not heavy with configuration. It was heavy with sentences that restate structured
data the config already holds — and those sentences were being sent to the model.

**3. The same fact is declared three and four times over.** `aliases` says the caption IS "Land",
`regex_hints` says `^Land$`, `keyword_hints` says it CONTAINS "Land". 349 of 396 regex hints and 428
of 635 keywords restate an alias on the same line. `statement`, `note_use` and `unit_of_account` are
each stored per line AND supplied by the section, with the per-line copy winning. `note_title_any`
and `note_terms` are one declaration in two forms, the second mechanically derived from the first
(`'profit loss before tax ation'` from `(?:ation)?`).

**4. A quarter of the controls are already retired.** 25 of 69 are in `_NOT_CONFIGURABLE` — the
server refuses writes — and 8 of those are declared by nobody. They are still rendered, still in the
field lists, still in `withheldReason`'s rules.

---

## Decisions taken

Recorded as decided in conversation. Front-end label first, backend field in brackets.

### Delete outright

| control | field | evidence |
|---|---|---|
| Counts as this line | `include_criteria` | 375/441 generated; 69 authored ones folded into `definition` |
| Easy to confuse with | `confusable_with` | 4 of 539 lines; the LLM half sends an opaque key the request never contains |
| Which of two look-alike captions this is | `section_disambiguation` | 13 distinct values over 395; its deterministic tie-break went with the semantic tier |
| Description | `description` | 85 lines; fold any real "why" into `definition` first |
| Match those patterns against | `note_source.caption_normalization` | default on all 77, **read by no code**, and its help warns of a consequence that cannot occur |
| Reaches the output template | `in_output` | identical to template membership on all 539 |
| Balance, flow or subtotal | `unit_of_account` | **derivable, 539/539 exactly** — see below |
| the whole **"What kind of figure is it?"** band | 7 controls | every one retired, unused, or derivable |
| 25 already-retired controls | — | server already refuses them; removal changes no behaviour |

`unit_of_account` derivation, verified to reproduce all 539 stored values with zero mismatches:

```
type == calculated                                             -> subtotal
statement in {balance_sheet, covenants_supplemental, setup}    -> balance
otherwise (P&L, cash flow, notes, ungated)                     -> flow
```

**The value must still exist in the resolved config** — `rollups.py:125` recognises a subtotal only
by this field (*"it is why the split is not 'the key looks like a total'"*). Drop the control and the
stored values; compute at load.

### Demote

| control | field | to |
|---|---|---|
| Does NOT count as this line | `exclude_criteria` | advanced; values blanked (393/462 were one sentence) |
| Where this line came from | `namespace` | a badge in the line's header, not a control — its own help says it "cannot be set by hand" |
| Recognition | see below | advanced |

### Collapse

**Recognition → one control + a separate veto list** (5 controls → 2). Modes:
*is exactly · contains · contains all of · starts with · ends with*.

| mode | writes to |
|---|---|
| is exactly | `aliases` / `aliases_i18n[locale]` |
| contains / contains all of | `keyword_hints` |
| starts with / ends with | `regex_hints` |

The veto list (`exclude_hints`, 13 lines / 38 patterns) stays **separate** — decided, because one
mis-set dropdown otherwise turns a match into a veto, the hazard the code already names.

Measured: **396 of 396** regex hints are literals or literals with dash/whitespace tolerance; only
**2** use regex as regex. So authors never needed regex — they needed *exactly/contains* plus
normalisation. 392 of 461 keyword lines carry a single keyword, which is "contains".

Locale complication, which the control must show rather than hide: `aliases_i18n` is
`dict[str, list[str]]` while the three hint lists are flat. **170 lines carry a `zh` alias list (753
strings) and 169 of those also carry non-locale hints.** So "is exactly" rows are locale-scoped and
the rest are not. The locale picker stays in the band header and greys out for non-exact rows.

**Note source → 4 controls** (8 fields → 4), pairing each field with its own regex/terms twin:

| merged control | fields | entries today |
|---|---|---|
| which note | `note_title_any` + `note_terms` | 119 + 1,111 |
| which rows count | `row_caption_any` + `row_terms` | 1,172 + 3,039 |
| which rows are excluded | `row_caption_none` + `row_terms_none` | 3,876 + 11,067 |
| prose | `prose_any` | 28 |

Terms are canonical, regex generated, with a raw-pattern escape under advanced. **The note pair does
NOT merge with the row pairs** — that is the container-vs-content split, and it is measured: *"a
single blended probe scored 0.000 on the note headed 管理費用 for a line whose every token is a
depreciation word."*

Roughly half of ~20,000 entries become deletable, since the generated twin disappears.

### Restructure

**Form order.** `type` and the where-question to the top, driving the bands below. Today `type` sits
in band 6 of 7 and `inherits` in band 3 — both below fields they govern.

**The gate becomes three plain questions** instead of 18 engine keys:

| question | values |
|---|---|
| Which statement? | what an annual report has |
| Where in the document? | **Face · Notes · Either · Full report** |
| Under which banner? | the sub-heading within that statement |

The 18 sections become *presets*: choose "Balance sheet / Face / Current assets" and the shape fields
follow. "Full report" gives the 77 parts' deliberate ungating (`section_scope: []` + explicit
`statement: null`) an honest name instead of looking like an omission.

**Why the banner must stay a question:** **170 contested captions are settled by the banner alone** —
same statement, different section. `"Financial asset at FVTPL"` is claimed by both `bs_ca` and
`bs_nca`; `"Shareholders / holding companies"` by `bs_nca` (due from) and `bs_ncl` (due to). Collapse
to Face/Notes/Either/Full and those become indistinguishable.

**"How this line's notes are found" → "Note order priority"** (`note_selection`), values *Notes
tagged to face* / *Any notes*. Semantic becomes unconditional — `patterns` removed from the schema,
the vocabulary and `note_context`; 539 of 539 are already `semantic` and the alternative only ever
removed capability.

This is **new capability, not a rename.** The line-item path has no notion of face-cited notes —
`line_item_notes.py` and `line_item_requests.py` contain zero references to citations, and
`identified_notes(line_item_set, notes)` is not even passed the links. Measured on laisun: **102 of
300 rows carry a printed note reference and all 102 resolve**, reaching 33 distinct notes. That is
the filing's own statement of where the detail is, currently ignored.

### Formula lines keep four fields

For the 42 `calculated` + `derived` lines, remove: `aliases`, `aliases_i18n`, `regex_hints`,
`keyword_hints`, `exclude_hints`, `prompt`, `exclude_criteria`, `note_source`, `note_use`,
`inherits`, `statement`, `section_scope`, `alias_matching`, `match_priority`, `face_only`,
`sign_convention`. Derive `unit_of_account` from `type` and `temporality` from the placing statement
(both verified clean), so `rollups.py` and `normalize.py` keep what they read.

A formula line ends with: **name, definition, type, formula.**

The printed-subtotal cross-check moves to a separate master (below). Without it the system loses the
ability to notice its arithmetic disagrees with the filing — measured, 7 printed captions bound to 6
calculated lines on laisun, and `services/line_items.py` records that *"Total Assets and Profit for
the Year"* depend on it.

### Two new masters — MARKING LAYERS, not separate homes

**All 539 line items stay in the config.** A master does not take lines out of it; it marks a
subset of them. So the config remains the complete inventory and the master is the declaration of
which lines have a special role.

| master | marks | replaces on the line form |
|---|---|---|
| **Others / residual buckets** | ~14 lines as the section leftover | `alias_matching` (14), `value_scope: exclusive_residual` (11), `residual_policy` (11) — three per-line declarations of one small set |
| **Cross-check** | the 33 calculated lines whose printed subtotal is read and compared | the recognition that currently does that reading |

The three fields the others master replaces are the same 11-14 lines every time — all 11
`exclusive_residual` lines also carry `alias_matching: disabled`. One marking, three current
spellings.

`value_scope` then resolves the same way as `unit_of_account`: the **control** leaves the form, and
the **value** is still computed at load (residual where the master says so, `exclusive_leaf`
otherwise) because three readers depend on it — `rollups.py:127` sorts a section into
subtotals/residuals/dedicated, `map_ontology.py:1002` keeps a residual out of a subtotal's children,
and `line_item_requests.py:78` makes `asked_about` return False for one.

`alias_matching` cannot simply be deleted without the master: the template declares **zero
`__others` keys**, so it is currently the only thing keeping 12 catch-all buckets out of the alias
index, and a naming-convention rule would miss `bs_equity__equity_and_reserves`.

---

## Correctness work — independent of the form, and worth more than it

These publish or lose figures. They should not queue behind form work.

**A. A refused component addend publishes a partial sum, and inverts a sign.**
The model says "500,000 − 120,000 = 380,000". One addend fails the caption gate. `resolved` is
filtered and `figures_of` re-runs, but `signs` is **not** filtered and is positional:

| what happens | published |
|---|---|
| nothing refused | 380,000 ✓ |
| B refused | **500,000** — a partial sum, unmarked |
| A refused | **+120,000** — B was declared as a *deduction* and publishes as an addition |

Decided behaviour: **show both values, print the sum including the unverified term, flag the line for
review, mark the unverified term red.** For an unresolved citation (b1) the same applies — option 1,
chosen knowing it reverses *"the model supplies none of the figures"* and would have published the
fabricated RMB 6.6 bn the corpus run caught. `test_export_honesty`'s assertion that *"the number in
the cell is the value the server computed"* must be rewritten to the new posture, deliberately.

Where no number exists at all (b2): publish nothing, raise a review entry, make the answer readable
in the workspace.

**B. 14,943 authored exclusions are inert on the path that produces figures.**
`row_terms_none` (11,067) + `row_caption_none` (3,876) are enforced on the deterministic row and
prose routes and **not on LLM answers** — `content_vetoes` is read only inside `widen_by_content`,
which ships off. Demonstrated on the example line: `Opening balance of accumulated depreciation`,
`Depreciation on disposal` and `Depreciation transferred out` are all explicitly vetoed and all
**accepted** by the LLM-answer gate, because each shares `depreciation`. Those are real rows in a
movement schedule sitting beside the one we want.

**C. An LLM answer vanishes when no citation resolves.**
`if not resolved: continue` fires and **nothing reaches the row** — no flag, no derivation, no
reason. The answer survives only as a `ctx.log` line. Persisting it is the prerequisite for the b2
review entry and for reading the answer in the workspace.

**D. Settings toggle: may an LLM answer be vetoed.** Ships *after* A, because enforcing vetoes
refuses more citations, which makes A's partial sums and sign shifts fire more often.

---

## Data cleanups

| cleanup | detail |
|---|---|
| materialised copies | `statement` (462), `note_use` (462) — all equal their section's value, and the copy currently wins over the section. Keep the 77 explicit `statement: null` — that is a real declaration |
| inert recognition on derived parents | 73 aliases, 5 regex hints, 7 keyword hints on 6 lines — **blocked**, see open decisions |
| 18 template keys with no line item | output rows nothing will ever fill; surface them somewhere |

---

## Decisions closed

1. **Note order priority: PRIORITISE.** Cited notes go first, remaining room still filled by score.
   Strictly better than today and cannot withhold a note the current behaviour would have delivered.
2. **`aliases` removed from formula lines.** All 33 calculated lines carry them today and they are
   what bound the 7 printed subtotals measured on laisun; the cross-check master takes that over.
3. **`order` derived from template order** rather than authored per line.
4. **`value_scope`** — control off the form, residual marking moves to the others master, value
   derived at load (see above).
5. **Sequencing — correctness, deletions, collapses, form.** Reasoning recorded below.

### The calculated formula moves INTO the config

New requirement. All **33 of 33** calculated formulas live in the template's rollup today — `terms`
is declared on **zero** lines, and `services/line_items.py` falls through deliberately: *"if not
d.terms: return … the template owns the rollup"*. Examples:

```
bs_nca__gross_fixed_assets        op=sum,  8 children
bs_nca__total_non_current_assets  op=sum, 34 children
bs_ca__net_trade_receivables      op=sum,  2 children  [trade_receivables_gross, allow_for_doubtful_accounts]
```

Migrating them into `terms` makes the config the single place a figure's derivation is declared,
which is the same argument behind the rest of this plan. Two consequences to handle:

* the fall-through becomes a **conflict** — once `terms` is populated, "the template owns the
  rollup" must stop being the rule, and `evaluate_rows` / `check_rollups` (which read the template's
  version) need repointing;
* **open:** does the template's rollup stay as a cross-check against the config's terms, or is it
  deleted once migrated?

This is the one item that ADDS to the config, and it touches the rollup evaluator rather than the
form — so it sequences with the correctness group, not the form group.

## Sequencing, and why

Ordered by what breaks if it is wrong.

**1. Correctness — A, C, B, D.** A publishes an inverted sign today. C is pure addition (persist an
answer currently discarded), so it is safe and unblocks the b2 surface. B turns on 14,943 exclusions
and therefore changes which figures publish — it needs the no-figures-move check, and it must follow
A because enforcing vetoes causes more refusals, which is exactly what A's bug mishandles. D is B's
switch. The formula migration belongs here too.

**2. The 25 already-retired deletions.** Zero risk — the server already refuses these writes, so
removing the controls cannot change behaviour. Takes about a third of the form away and makes
everything after it easier to reason about.

**3. The collapses.** Recognition to one control plus veto; note source 8 fields to 4. Real risk in
one step: alias normalisation makes the index match MORE, so two lines whose aliases differ only by
a dash would collide. Needs the corpus no-figures-move run and a collision scan before the 349
redundant patterns are deleted. Must follow B, or 15,000 exclusion entries get migrated while their
effect has never been observed.

**4. Form restructuring.** Type and where to the top, the three-question gate, labels, grouping.
Most visible churn, no behavioural risk, and it lands on a form already two-thirds smaller.

The rule underneath: behaviour-changing work while the form still shows what is there; cosmetic work
once behaviour is settled.

---

## Prerequisite investigation, not yet done

**Are derived parents actually caption-unreachable?** `map_ontology` asserts *"a derived parent is
reached by nothing — not the model, not an alias, not a regex, not a semantic probe… four separate
indexes in the backend now agree on it"*. But by the two locks in `line_item_matching:305`, only
**3 of 9** are excluded — the other 6, including `is_pl__deprec_and_impairment_oper_exp`, are
reachable. Either the comment is stale or the real exclusion happens elsewhere.

This decides whether the inert-recognition deletion is a free cleanup or a behaviour change, so it
has to be traced before that cleanup ships.

---

## Risk notes

- **Alias normalisation** (dash and whitespace folding) is the only collapse step that changes
  matching. It makes the alias index match *more*, so two lines whose aliases differ only by a dash
  would collide. Needs `focus_as_published.py --all` no-figures-move plus a collision scan over the
  normalised index before the 349 redundant patterns are deleted.
- **`Template.tsx` is a second author** of `include_criteria`, `exclude_criteria`, `value_scope`,
  `confusable_with` and the hint lists. Any field removed from `ItemEdit` must be removed there too,
  or that screen's save silently drops it — the defect its own comment documents.
- **Enforcing vetoes before collapsing the note-source fields.** Migrating 15,000 entries whose
  effect nobody has ever observed would make a migration bug invisible.
