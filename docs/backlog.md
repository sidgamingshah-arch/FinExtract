# Backlog

Kept in the repo rather than in a scratchpad, because it is asked about repeatedly and a list that
dies with a session is not a list. Each item says what BREAKS, for whom, with a measured example —
and the instrument that produced the measurement, so nobody has to re-derive it.

Decisions already taken are recorded as decisions, not re-opened.

---

## 1. The config screen offers fields that cannot apply to the line

**DECIDED:** relevance is by LINE ITEM TYPE, and only relevant fields should appear. This is the
same instruction as "Simple and advanced should be based on field type not generic" — restated
because the first pass implemented it for the simple/advanced SPLIT and not for the whole form.

**What breaks.** A `derived` line's figure comes from its declared cascade over sub-line items.
Nothing recognises it, nothing is asked about it, and no note is read for it. Yet the form still
offers `note_source` (note title patterns, note terms, row terms), aliases, regex and keyword
hints, and a prompt.

**Example.** Open `is_pl__deprec_and_impairment_oper_exp`. Author `note_title_any` patterns for it,
`note_terms`, a `prompt`. Every one of them is inert. Set `llm_only_if_note_tagged: true` and the
endpoint **accepts** it and it does nothing. Nothing on screen says so.

**Solution.** Drive the whole field set from `type`, not just the simple/advanced banding:

| type | what can possibly apply |
|---|---|
| `derived` | name, statement, type, the cascade — and nothing about recognition |
| `calculated` | name, statement, type, the terms it is computed from |
| `extracted` | the full recognition surface: aliases, hints, note source, prompt, include/exclude |

The runtime already agrees — `line_item_requests.asked_about` excludes derived parents by type. The
schema's own `_never_asked()` does NOT (it only knows residuals), so the two spellings of one rule
disagree and the screen believes the wrong one. 8 of the current test failures are this.

---

## 2. Every request repeats a note's ENVELOPE — and the fix is caching, not collapsing

**CORRECTED.** This entry claimed 26-39% off the note block by collapsing fragments. That number
was measured on a collapse that **keeps the longest prose and throws the rest away** — and the
fragments do not carry the same prose. Measured on laisun's note 4: 7 fragments with prose lengths
934 / 2,411 / 2,338 / 1,729 / 2,791 / 1,139 / 1,322, and **0 of the 21 pairs** has one contained in
the other. They are successive pages of one long narrative, not copies.

So that 39% was 37,777 characters of the filing's own narrative being discarded. The **lossless**
collapse — one entry per note with the prose CONCATENATED in page order — is worth much less:

| | as sent | lossy collapse | **lossless collapse** |
|---|---|---|---|
| laisun (39 entries / 12 notes) | 38,965 tok | 23,776 (-39%, **loses 37,777 chars**) | **36,386 (-7%)** |
| suncreate (60 entries / 37 notes) | 14,609 tok | 10,851 (-26%, loses 6,423 chars) | **13,008 (-11%)** |

**What is genuinely repeated** is the ENVELOPE: the note number, the title and `identified_for`,
once per fragment — 7 times for note 4. That is the 7-11%.

**So the attacks reverse in priority.**

1. **MOVE THE BLOCK TO THE SYSTEM PROMPT.** It rides in the user message, which providers do not
   cache, while the cacheable system prefix is ~6,600 characters. Behaviourally identical, and it
   turns most copies of a ~36,000-token block into cache reads. This is the large win and it loses
   nothing.
2. **Collapse fragments losslessly** — one entry per note, prose concatenated, rows concatenated,
   `identified_for` unioned. 7-11%, and it also makes the block easier to read.
3. Per-request scoping is already done (`build_request` slices the identified notes to those THIS
   request's lines selected).

**Do NOT keep only the longest prose.** A footnote stating a figure no row carries is the whole
reason the prose travels — that is how the operating-expense depreciation share is reachable at all
— and it can sit in any fragment.

**Instruments:** `scripts/preflight_live_run.py`, and the fragment measurement above.

---

## 3. `note_sourced._write` overwrites `value_raw` — PARKED, and the impact is smaller than stated

**DECIDED:** do not fix for now. The measurement below supports that rather than merely allowing it.

**CORRECTED.** This entry said "the printed figure is gone and the differs-from-printed check has
lost its comparand". Neither is true today:

* **No reader observes it.** Every consumer of `value_raw` uses it only as a FALLBACK for a missing
  `value` — `structural_checks.py:201`, `stages/confidence.py:54`, `stages/residual.py:536`,
  `services/assemble_components.py:76` and `services/note_sourced.py:167` are all
  `ev.value if ev.value is not None else ev.value_raw`. `_write` always sets `value`, so the
  fallback never fires.
* **It never reaches a person.** `_serialize_rows` emits `value` and not `value_raw`, so the field
  is absent from the API, from every screen and from the export.
* **The printed-vs-derived check does not use it.** `note_sourced` compares `existing.value`
  against the note-derived figure BEFORE writing and keeps the printed one with a
  `note_sourced_differs_from_printed:` flag. That comparison happens earlier and reads another
  field.

**What IS lost, precisely.** `normalize` leaves `value_raw` as the printed magnitude with the
printed sign and `value` as the sign-normalised figure — so on a row where a sign cue applied they
DIFFER. `_write` sets both to the note-derived amount, so for those rows the printed magnitude and
the sign decision are unrecoverable, and unlike `note_tag_gate` there is no flag naming what was
displaced (`note_tag_absent_zeroed:<figure>` is that stage's convention, and it backfills
`value_raw` only when empty rather than overwriting it).

**So the cost is latent, not live:** the day something wants the printed original — an audit
column, a "what did the page say" view — it will not be there for note-sourced rows.
`line_item_llm._write_prose` already does it correctly, which makes the fix a copy of the writer
that is right.

---

## 4. PRC vocabulary — do it at BOTH levels (PARTLY DONE)

**DONE SO FAR, and the first fix was not a vocabulary fix at all.** Measured, 1,486 of 3,359 note
headings in the corpus (44%) begin with a BARE separator — `、其他应收款` — because the heading is
extracted from a line whose Han enumerator (`五、`) has been split off. Every authored
`note_title_any` is anchored with an optional ARABIC enumerator and nothing else, so none of them
could pass it: `sub__rp_other_receivables_note` matched NONE of its own notes on 11 of 18 filings
while its pattern spells that exact phrase. Fixed in the MATCHER
(`note_context.title_variants` / `matches_title`, applied at all three sites — `identified_notes`,
the row route and the prose route) rather than by editing hundreds of anchors, each of which would
have been a chance to widen one by accident.

Then three per-line additions, each checked against the note's actual ROWS before authoring:
`sub__ltp_fincl_assets_note_total` (+ `其他非流动金融资产`, 8 filings),
`sub__ppe_depreciation` (+ `property and equipment` / `物業及設備` — the existing pattern required
"plant"), `sub__revenue_note_principal_revenue` (+ a bare `^revenue$`).

**Expected-source delivery: 501 -> 518 of 508 checkable pairs.** ABSENT 878 -> 861.

**WHAT THE CHECKING CAUGHT, and it is why the remaining 861 are not a bulk edit.** The 0.70+
similarity band is NOT uniformly correct. Of the 32 lines it flagged: `一年内到期的非流动资产` for
`cp_current_loans_advances_*` (16 pairs) is WRONG — the note's rows are
`一年内到期的长期应收款` and `一年内到期的银行大额存单`, certificates of deposit and long-term
receivables, not loans and advances. `、其他` ("other") and `GENERAL INFORMATION` are scoring
artifacts on short headings. Several name LIABILITIES for an ASSET line. Every remaining candidate
needs its note's rows read before a pattern is authored.

---

## 4b. The original analysis

**DECIDED:** the work is at two levels, and they are different questions. Conflating them is what
produced a wrong diagnosis twice.

| level | matched against | names | fields |
|---|---|---|---|
| **1 — which note** | the note HEADING | the CONTAINER: 固定资产 | `note_terms` (scored), `note_title_any` (regex) |
| **2 — which row in it** | the row CAPTION | the CONTENT: 固定资产折旧 | `row_terms`, `row_caption_any` |

A line whose level-1 vocabulary names the CHARGE never finds the note, because no CAS filing
captions a note "depreciation of fixed assets" — it captions it 固定资产 and puts the charge in a
row. A line whose level-2 vocabulary names the CONTAINER matches the note's own total instead of
the charge. Both look identical from the output — an empty line.

### What the corpus says, over all 9 CAS/PRC filings

| | |
|---|---|
| lines with no Chinese `note_terms` (level 1) | **0** |
| lines with no Chinese `row_terms` (level 2) | **0** |
| level 1 finds nothing on any CAS filing | **1** — `sub__rp_loans_and_advances_note` |
| **level 1 claims a note, level 2 matches no row** | **24** |

So the vocabulary is not absent — those 24 lines carry 19–32 Chinese row terms each. Three distinct
causes sit behind that number, and only one of them is vocabulary:

**(a) The level-1 vocabulary points at the wrong container.** `sub__cfo_depreciation` claims note
五、5 on every CAS filing — heading 现金流 — and matches nothing, because that note is the cash-flow
STATEMENT SUMMARY (经营活动现金流入小计, 筹资活动产生的现金流量净额). The depreciation add-back is
not in it.

**(b) THE NOTE IT SHOULD READ IS NOT EXTRACTED AT ALL.** The CAS net-profit-to-operating-cash-flow
reconciliation — 将净利润调节为经营活动现金流量, inside 现金流量表补充资料 — is where that
add-back lives, and this line's `note_terms` already name it correctly. It is absent from all 18
filings, tested four ways: no note title matches it; `折耗` occurs 0 times; `间接法` 0 times;
`调节` appears in **1 row caption out of ~20,000**. No vocabulary work can reach a note the
document model does not contain. **This is an extraction-coverage gap, and it should be confirmed
against the PDFs before any more terms are written for that family.**

**(c) Genuine level-2 gaps** on the guarantee/contingency family (`sub__cl_*`), where level 1 finds
a note and the row wording is a real mismatch.

**Instruments:** `scripts/two_level_coverage.py` (both levels, all CAS filings),
`scripts/row_vocab_gap.py` (one line: claimed notes vs their actual row captions),
`scripts/mine_container_for_content.py` (inverts the search — which headings in the corpus CONTAIN
the rows a line wants, ranked by how many filings agree),
`scripts/prc_corpus_survey.py` (the population: 9 CAS/PRC, 4 HK-bilingual-Traditional, 5 HK/EN).

---

## 5. `similar` grouping — DONE, threshold is 0.70

**DECIDED:** look at what threshold would help. Swept, and 0.70 is the answer
(`scripts/sweep_group_similarity.py`).

**What breaks.** Choose `llm_request_grouping = "similar"` expecting a saving over `identical` and
get byte-identical output — 34 requests on laisun, 49 on suncreate. At the shipped
`llm_group_similarity = 0.8` no additional pair of line items ever merges, and nothing says so.

**Why 0.80 could never bind.** After `notes_for_line_item`'s cap a line carries one to three
notes, and Jaccard on small sets is coarse: {7} vs {7,12} is 0.50, {7,12} vs {7,19} is 0.33. No
such pair reaches 0.80, so only IDENTICAL sets merged — which is the other mode.

**Measured on laisun, all 518 asked-about lines:**

| | requests | ~tokens | **largest request** |
|---|---|---|---|
| `identical` | 214 | 810,058 | 74,750 |
| **`similar` 0.70** | **195** (−9%) | **760,996** (−6%) | **74,750** (flat) |
| `similar` 0.60 | 144 | 635,872 | 113,254 (+51%) |
| `similar` 0.30 | 42 | 547,255 | 261,113 |

The LARGEST request is the constraint, not the total — a gateway refuses per request, and a refused
request loses its lines to the deterministic route. 0.70 is the only value that cuts the count
without moving it. Below 0.70 every further saving is bought by making one request bigger.

---

## 6. The 33 subtotals → `calculated` — DONE

**DECIDED:** move them. Moved: the type split is now 497 `extracted` / 33 `calculated` / 9
`derived`.

**Example.** `Total assets` is typed `extracted` while three other declarations say it is computed:
`role: subtotal` with a `rollup` in the template, `unit_of_account: "subtotal"`, and
`extraction_mode: extract_or_derive`. The export already treats a contradicting printed figure as a
FINDING rather than the answer.

**Two things had to move first, and the second would have lost 33 figures.**

1. `_coherent` refused a `calculated` line with no terms. Relaxed — `terms` is one of TWO places
   the arithmetic can be declared and for a subtotal the template's `rollup` is authoritative (all
   33 carry children, 2 to 34 each). Populating `terms` instead would put a second copy of the
   components in the configuration, free to drift. An `intermediate` line is still refused: it
   appears in no template, so `terms` is the only place its arithmetic could live.
2. `services.line_items.evaluate` branches on type, so a `calculated` line took the
   `_apply_terms` route unconditionally and would have resolved to **None** for all 33 — Total
   Assets and Profit for the Year going blank. It now reports the document's figure when a computed
   line names no terms, which is the honest reading: no terms means the arithmetic lives elsewhere,
   and `check_rollups` is what compares the printed figure against the components.

**Verified:** all **66** published subtotal cells across the two reference filings are byte-identical
before and after (`scratchpad/subtotals_published.py`) — the focus report is blind to this set, so
it needed its own check.

**The trap was avoided, not disarmed.** `unit_of_account: "subtotal"` stays on all 33.
`services/rollups.py:125` is the sole discriminator in `section_members`, and flattening it in the
same change takes all 20 sections to `no_reported_subtotal` — 12 tied become 0 tied.

---

## 7. "Cross-statement allowed" — CLOSED

**DECIDED:** "no statement declared" and "cross-statement allowed" are the same thing. No separate
boolean. The permissive-when-silent behaviour of `claimable_on` / `_in_statement` stands as the
intended semantics, which is how the 77 parts reach a note on a different statement from their
whole.

---

## 8. ~250 lines of dead selection code

`note_context.build_pool` and `ContextPool.select` lost their only caller when the row-driven LLM
path went. Read by tests only.

**What they did, concretely.** For ONE PRINTED ROW — say a face caption `Trade receivables` — they
built a pool of every note and face row in the filing, scored each against a probe assembled from
that row's rival candidate concepts, and returned the best few subject to three caps: at most 3
notes, at most 3 face rows, and 1,200 characters total. The row then travelled to the model with
those units attached as its context.

**What it actually returned, on the reference filing.** `build_pool` made **1,764 units** from
laisun — every note and every face row. Asked for the context of a trade-receivables caption
(`probe = "trade receivables from third parties amounts due from customers loss allowance"`,
`notes_cap=3, face_cap=3, char_budget=1200`) it returned:

```
face   ref=cash_flow    ''
note   ref=23           'INVESTMENTS IN ASSOCIATES'
note   ref=46           'FINANCIAL INSTRUMENTS BY CATEGORY (CONTINUED)'
```

None of the three is the trade-receivables note. That is the mechanism working as designed and
still being poor: it scores a row's probe against a pool in which a forty-row note shares more
tokens with anything than a short one does, which is exactly the breadth-over-subject problem
`line_item_notes` was written to avoid by scoring HEADERS instead of whole notes.

**Why it has no equivalent now.** The unit is the LINE ITEM, and a line's notes come from its own
configuration — `note_title_any` plus, where `note_selection` is `semantic`, the headings its
`note_terms` score against. That is a stronger statement of relevance than a per-row similarity,
and it needs no cap to ration a row's share of a call because a row has no share.

**Why it was kept.** It is the mechanism if a line-item request ever needs to widen BEYOND the notes
its configuration named — which is a live question, since 44 of suncreate's authored notes score
below the floor. `ContextUnit`, `ContextPool.__init__` and `ContextPool._score` are NOT dead:
`line_item_notes.header_pool` and `notes_for_line_item` are built on them.

---

## 9. The extraction screen's stage list does not match the pipeline

**IN PROGRESS.** The screen was written against the pipeline before the row-driven LLM path was
removed and `line_item_llm` added, so its stage names, count and descriptions can no longer be
right. `docs/architecture/01-extraction-pipeline.md` is held to `default_pipeline()` by
`tests/test_docs_match_the_pipeline.py`; the SCREEN has no such guard.

---

## 11. THE FIRST LIVE RUN — what it proved, and the one finding it produced

Run on laisun, four focus parts, `grouping = none`, against the configured provider. **4 calls, 0
failures, 0 unresolved citations**, 19,159 in / 1,446 out, 129s. `mapping_strategy` reported
`llm_line_items`.

**What it validated in production for the first time.** None of this had ever been exercised
against a real model — every test uses a stub.

* The reply contract holds: every citation the model gave resolved to an extracted row or a
  verified prose amount. Zero unresolved.
* **The prose path works, including the scale fix.** It found `HK$375,901,000` in note 45's
  narrative, verified it against the note's own text, and divided by 1,000. Without the fix landed
  in `405093a` it would have published 375,901,000 — a thousandfold error on the face.
* **An empty answer is a real answer.** Three of the four lines returned no `sources` — "this
  filing does not state it in these notes" — rather than force-fitting a row. That is the
  behaviour the contract asks for and it had never been seen from a model.
* Non-interference held: `notes__contingent_liabilities` kept its cascade figure (934,842) while
  its part took the new one, so nothing overwrote anything.
* No existing figure moved. Revenue 4,995,768, depreciation 529,841, LTP 788,507 — all identical
  to the deterministic baseline.

**THE FINDING, and it is about the CONFIGURATION rather than the model.** The figure landed on
`sub__cl_reported_total`, whose definition reads "the single aggregate figure the filing itself
prints for its contingent exposure … the answer only for a filing that gives this one figure and
itemises no instrument types". The sentence it came from says:

> "As at 31 July 2025, in respect of **these** guarantees, the contingent liabilities of the Group
> amounted to approximately HK$375,901,000 (2024: HK$594,086,000)."

"These guarantees" is ONE CLASS — mortgage guarantees to end-buyers — and laisun does itemise
instrument types, so by its own definition this line should not have been answered for this
filing. The model was not shown that exclusion in a form it could act on: the definition's
qualifier is prose at the end of a long paragraph, and nothing in the request says "only if the
note itemises nothing".

**No published figure is wrong today** — the parent's cascade correctly declined the part. The risk
is latent: a filing whose cascade does reach the reported-total rung would take a subset figure as
the total.

**What to do about it — and the first answer here was wrong, so it is recorded as a correction.**
This was written as "the exclusion is prose at a paragraph's tail, so move it to `exclude_criteria`
where `line_item_payload` sends it as its own field", on the assumption that the qualifier never
reached the model. MEASURED, IT DID. The line's `instruction` field — carried on 100% of requests —
reads in full:

> Take the note's own printed total only where the note itemises no instrument types, and never add
> it to any type amount it already includes.

So the constraint travelled, as its own top-level payload field, in the imperative voice, and was
not followed. That makes this a MODEL failure on this instance rather than a configuration gap, and
moving the same words into `exclude_criteria` would change which JSON key carries them and nothing
else. What is worth doing instead is the deterministic guard the words describe: the run already
knows whether the note itemises instrument types, because it has the note's rows — so refuse a
`cl_reported_total` citation from a note that has itemised rows, the way
`caption_agrees_with_row_terms` refuses a container caption. A rule the code can check does not
depend on the model honouring a sentence.

**The general point, measured across the whole configuration.** All 77 parts carry exclusion
language ("never", "rather than", "only where") in `definition` or `instruction`, and NOT ONE has an
`exclude_criteria` field — the 77 parts are exactly the 77 of 518 lines with no `include_criteria`,
no `exclude_criteria` and no `section_disambiguation`, and exactly the 77 that carry a `prompt`
instead. The parts were authored in a different field vocabulary from the 441 wholes. That is worth
knowing, but it is a tidiness question, not an information one: the content reaches the model either
way.

**Reproduce:** `scratchpad/live_run.py` for the run; `scripts/audit_request_context.py` for the
field-presence table that corrected this.

---

## 10. No PRC measurement beyond the corpus dumps

Everything in item 4 rests on the 18 dumps in `_vocab/`, which are extraction output. Where a
finding says "the document does not contain X", that needs confirming against the PDF before it is
treated as an extraction defect rather than a dump artefact — item 4(b) especially.

---

## 12. Half the notes a line selects never reach its request — REAL, AND RANKED WRONG AT FIRST

**DOWNGRADED, AND THE CORRECTION IS THE USEFUL PART OF THIS ITEM.** This was written as "the
largest item on the list", on the strength of 80-of-158 note delivery. That number is right and the
conclusion drawn from it was wrong, because it does not distinguish the two kinds of note a line
gets:

* a note its authored `note_title_any` NAMES — the source the configuration says the figure is
  printed in. `note_context.identified_notes` passes these **unconditionally**; they are explicitly
  exempt from `_SEMANTIC_NOTE_BUDGET`.
* a note SIMILARITY proposed. These are additive candidates, and these are what the budget drops.

Measured directly (`scripts/audit_expected_source.py`, 77 lines x 18 filings = 1,386 pairs): of the
508 pairs where the configuration names an expected note AND the filing contains it, the request
carried it in **501 — 99%**. Seven were withheld. So the budget is not keeping the model from the
evidence we expect it to read; it is trimming the guesses around it, which is what it was written to
do.

WHAT IS STILL WORTH DOING. The 12-of-72 empty payloads are real, and a request with no note in it
is still a request that cannot be answered — reserving each line its top-1 note fixes that for ~44%
more context (table below). But it is a completeness improvement, not the binding constraint, and
**item 4's vocabulary gap is what actually withholds evidence**: 878 of 1,386 pairs have NO note
matching the line's pattern at all, and 77 of those are a heading the filing really does print
(scored 0.70+ by similarity) that the pattern simply misses. Those 77 are a two-line fix each.

**The original framing, kept because the measurement stands on its own:** Measured over all 18
filings and the four focus parts: of **158 notes those lines selected, 80 reached a request — 50%.**
Twelve of the 72 requests carried **no note at all** despite the line having selected one to four.

**What breaks, for whom.** A request with no note in it cannot be answered, and the model correctly
returns nothing. In the run log that is indistinguishable from "this filing does not disclose it" —
so the failure reads as a model or vocabulary problem and sends effort at the prompt, which cannot
fix it. `sub__face_principal_revenue` is worst hit: on 7 of 18 filings it selected notes and
received none.

**The mechanism.** `line_item_llm.build_request` intersects the line's own selection with
`note_context.identified_notes`:

```python
wanted = set(plan.notes)
"notes": [n for n in identified if str(n.get("note", "")) in wanted] if wanted else []
```

`identified_notes` passes every REGEX-CLAIMED note unconditionally, but bounds its semantic half by
`note_context._SEMANTIC_NOTE_BUDGET = 8` — eight note numbers **for the whole document**, ranked
globally by score across all 518 lines at once. A line whose best heading scores 0.40 loses its only
note to another line's 0.85, and is sent an empty payload. The per-line cap of 4 in
`notes_for_line_item` cannot see this, and neither can the request: by the time `build_request` runs,
the note is simply not in `identified` to be found.

**Why the bound was set, and what changed under it.** Its own comment records the measurement: "23
numbers added by 77 line items became 56 extra TABLES — `identified_notes` went from 31 tables to 87
and one request from 30,407 tokens to 82,299." That blowup was real and it was counted **in
fragments**, when the function emitted one payload entry per extracted table. Item 5's
fragment-collapse fix made it one entry per note NUMBER, so the cost of admitting a note fell by
roughly the fragment multiplier — laisun's 39 entries became 12. The bound was never re-derived
against the new cost basis. The rationale's other half — "an inference does not earn what a
declaration earns" — still stands, and argues for a smaller reserve rather than for no bound.

**Measured options** (`scripts/audit_request_context.py`, and the sweep in this item's reproduce
line). "reachable" counts requests where a row in the payload passes
`caption_agrees_with_row_terms` — the run's own acceptance gate — or prose carries an amount near
the line's terms:

| change | notes delivered | reachable requests | empty payloads | all-518 context |
|---|---|---|---|---|
| today (`budget = 8`) | 80/158 (50%) | 43 | 15 | 32.5M chars |
| `budget = 40` | 148/158 (93%) | 55 | 6 | 48.0M (+47%) |
| no bound | 158/158 (100%) | 58 | 3 | 48.5M (+49%) |
| **reserve each line its top-1, keep `budget = 8`** | **146/158 (92%)** | **55** | **6** | **47.0M (+44%)** |

**What to do.** Reserve every line its single best-scoring note before the global cut, then fill the
remaining budget as now. It dominates raising the cap — same reachability as `budget = 40` at
slightly lower total context — and it is the principled shape: the bound exists to stop *unbounded*
guessing, not to leave a line with nothing. It also keeps the declaration/inference distinction the
comment argues for, because one note per line is the smallest possible inference.

The cost is real and is not free: ~44% more context on a full 518-line run. Worth stating plainly
when the change is made rather than discovered afterwards.

**Not yet implemented, deliberately.** It changes which notes reach every request on every run, so
it needs the `focus_as_published.py --all` no-figure-moves check across the corpus, which belongs
with the change rather than with the branch being merged.

**Reproduce:** `python scripts/audit_request_context.py` for the per-filing verdicts and the
delivery share; the budget sweep and the top-1-reserve variant are in this session's transcript and
re-derivable by setting `note_context._SEMANTIC_NOTE_BUDGET` and re-running the audit.

---

## 13. Two open defects found while clearing the test suite for merge

Both are captured by tests rather than described, so neither can be lost. Both are on the ONTOLOGY
(row-driven) route, not the line-item route this branch builds.

### (a) The expenses residual routes nothing at all — one row, two failing assertions

`pl_expenses__others` used to receive a statement TOTAL by mistake: "LOSS FROM OPERATING
ACTIVITIES" had no alias, fell through to the residual router, and landed in a bucket whose own
rulebook exclusion reads "Section subtotals and statement totals". That half is fixed — the total
now maps to `pl_operating_profit_ebit`.

**The genuine expense row went with it.** Measured on the transcribed HKEX fixture:

| row | files under | should be |
|---|---|---|
| Fair value losses on investment properties, net  −508,569 | `engine_unclassified_face__profit_and_loss` | `pl_expenses__others` |

So the residual holds ZERO rows, and because the row is in no section the subtotal sums over,
`pl_operating_profit_ebit` computes **−387,591 against the printed −896,160 — a gap of 508,569,
which is that row to the rupee.** Every other subtotal on the statement agrees with the filing, and
`test_every_printed_line_of_the_statement_is_filed` still passes because the engine bucket IS a
canonical key — which is exactly why this was invisible.

Refusing a statement total and routing an unaliased expense row are two questions, and the sweep
currently answers both with "no". Fixing the routing makes both tests pass with no other change.

**Marked `xfail(strict=True)`** in `tests/test_hk_income_statement.py`, so it fails loudly if the
routing is fixed and the marker is left behind.

### (b) Two face-reading PARTS declare aliases and no part has a statement gate

`sub__face_principal_revenue` and `sub__rp_bs_face_receivables` are the only 2 of 77 parts that
declare aliases. They need them: both read a row off the FACE of a statement rather than out of a
note — their `note_source.note_title_any` matches "consolidated balance sheet" and "consolidated
income statement" — so without aliases the deterministic route cannot reach them at all, and
recognition was moved DOWN to them from their derived parents precisely so it would sit on the
layer that corresponds to a printed row.

**The risk.** A part declares no `statement` and no `section_scope`, deliberately — pinning one
loses the note that prints it (`tests/test_line_item_gate.py` carries the measurement). So an alias
on a part is bindable from ANY statement, and `mapping.match` now returns on the FIRST exact alias
hit, so a caption "Revenue" on a cash-flow statement can reach `sub__face_principal_revenue` with
nothing left to refuse it. Not observed in the corpus run; reported because the guard that would
have caught it is the one being exempted.

**The likely fix, and why it is not done here.** Pin exactly these two to their own statement. That
is coherent with the gate test's own argument — it objects to pinning a part printed in a NOTE whose
whole sits on another statement, and a face part is definitionally on one named statement — but it
changes where a part may bind on every run, so it belongs with a `focus_as_published.py --all`
no-figures-move check rather than with a merge.

**The guard is narrowed, not removed:** the exemption names those two keys, so a THIRD part
acquiring aliases still fails `tests/test_line_item_gate.py`.

---

## 14. THE ROW-TERMS GATE ADMITTED THE CONTAINER CAPTION IT WAS WRITTEN TO REFUSE — FIXED

**FIXED** by `line_item_notes.discriminating_tokens`, which narrows the accepted set twice: to
tokens from terms the author named ALONE (`depreciation`, `折旧` — never `and` or `assets`, which
exist only inside a phrase), then minus the line's own note-level vocabulary, which names the
CONTAINER by construction and is what removes `资产` from `固定资产折旧` while leaving `折旧`.

Chosen by measurement rather than intuition, against 8 captions that must pass and 6 that must be
refused: today's rule passed 8 and wrongly accepted 3; the first narrowing alone still accepted
`固定资产`; both together pass 8 and wrongly accept 0. All 77 authored lines keep a NON-EMPTY,
smaller set, and `discriminating_tokens` falls back to the full set rather than return nothing —
an empty set would refuse every caption for that line and lose its figures silently.

**What it changed, and the number went DOWN because it had been wrong.** Reachability over the 4
focus lines 65% -> 47% (ROW PRESENT 43 -> 25, those verdicts moving to NO CANDIDATE); over all 77
authored lines 786 -> 665 (57% -> 48%). **No published figure moved** — verified by extracting each
available filing twice, once with the old rule patched back in, and diffing every concept through
`concept_value`: 0 of 311 and 0 of 323. The deterministic path recorded 0 refusals either way,
which is the honest caveat: on those filings `map_ontology`'s call site never fired, so that is
evidence of no harm rather than a strong test of it.

**The residual limit, recorded in `tests/test_row_terms_gate.py::test_what_the_narrowing_does_NOT_fix`:**
the subtraction removes the vocabulary of the line's OWN note, so a row term naming a DIFFERENT
note's container still contributes its tokens — `sub__operating_expense_depreciation` reads the
EXPENSES note and still accepts a bare `固定资产`. Weaker than what was fixed, and note selection is
what keeps that caption away from it.

---

## 14b. The original finding, kept because the measurement stands

**This is the most consequential finding of the corpus audit, and it invalidates a number I
reported before measuring it.**

`line_item_notes.caption_agrees_with_row_terms` accepts a caption that shares **one** subject token
with the line's `row_terms`. Its own docstring argues one token is enough, and gives the measured
case it was written for: the face row "Other operating expenses" (1,026,959, a real income-statement
total) bound to a depreciation part, refused because that caption "shares no token with the line's
row terms — every one of which is a depreciation phrase".

**Every one of which is NOT a depreciation phrase, token by token.** `row_terms` are multi-word
PHRASES and `subject_tokens` splits them into individual words, so for
`sub__fixed_asset_depreciation` the accepted token set contains:

```
assets  fixed  property  plant  equipment  investment  lease  payments
prepaid  progress  construction  release  right  use  and   <-- "and"
```

`and` comes from `depreciation of property plant and equipment` and `amortization and
depreciation`. So **any caption containing the word "and" passes the gate for this line.** Measured
against the shipped configuration:

| caption offered to `sub__fixed_asset_depreciation` | gate | shares only |
|---|---|---|
| `Depreciation charge` | pass | `depreciation` — correct |
| `Deposits and other receivables` | **pass** | `and` |
| `Contract assets, net (i)` | **pass** | `assets` |
| `固定资产` (fixed assets — the CONTAINER) | **pass** | `固定` `定资` `资产` |
| `Unlisted equity investments, at fair value` | refuse | — |

The Han half fails the same way for a different reason: a compound term like `固定资产折旧`
("fixed-asset depreciation") is ONE word, so its bigrams include `资产` — the container noun — and
any caption naming assets at all clears the gate.

**What it costs.** Two things, and the second is why this is at the top of the list:

1. A wrong figure reaching a part. The gate is the last line of defence after
   `resolve_sources`, and on the container-caption error it does not hold. The measured
   1,026,959 case was stopped by a cascade guard (`refuse_negative`) that held only because the
   wrong number happened to be negative.
2. **It silently inflates every measurement built on it.** `scripts/audit_request_context.py`
   defines "the evidence was in the payload" as "a row passes this gate", on the reasoning that the
   gate is what the run itself would accept. That made the headline read *"the figure was reachable
   in 47 of 72 requests (65%)"*. That number is an **UPPER BOUND, not an estimate**: at least 8 of
   the 43 row-based verdicts rest only on a word taken from a multi-word phrase (`'Deposits and
   other receivables'` for a depreciation line), and the Han verdicts are additionally suspect for
   the compound-bigram reason above. **The corrected claim is "at most 65%".**

**It also explains the run.** The live corpus run produced figures on 2 of 15 filings while the
audit said the evidence was present in most of them. Reading the actual note, the model declined
rows this gate would have accepted — so the model was the stricter of the two, and the gap between
"65%" and "2 of 15" is largely this defect rather than model failure.

**What to do.** The check is the right idea at the wrong granularity: it should ask whether the
caption names the line's CONTENT, and it currently asks whether it shares any word with anything the
author typed. Three candidate fixes, cheapest first:

* **Require the match on a term the author named ALONE.** `折旧`, `depreciation`, `amortisation`
  are authored as standalone terms; `assets` and `and` never are. This is a two-line change and
  removes `and`, `assets`, `property`, `plant` from the accepted set at a stroke.
* **Subtract the note-level vocabulary.** A token that appears in `note_terms` / `note_title_any`
  names the CONTAINER by construction, so it cannot be what distinguishes the row. This is the
  two-level distinction item 4 is about, applied to the gate.
* **For a Han compound, require the operative tail** (`折旧` in `固定资产折旧`) rather than any
  bigram.

The first two are compatible and should probably both happen.

**Not done here.** It makes the gate STRICTER, which can only remove figures — so it needs the
`focus_as_published.py --all` no-figures-move check plus a re-run of the corpus audit to confirm the
recall it costs is zero. That is a measurement session of its own.

**Reproduce:** the token set and the caption table are printed by
`caption_agrees_with_row_terms(by['sub__fixed_asset_depreciation'], caption)` against
`subject_tokens`; the 8-of-43 count is a weak-token sweep over
`scripts/audit_request_context.py`'s own payloads.
