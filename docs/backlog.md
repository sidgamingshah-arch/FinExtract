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

## 2. Every request sends the same note several times over

**What breaks.** `note_context.identified_notes` appends one entry per extracted TABLE. A note
printed across pages arrives as many tables carrying one number, so it goes into the payload once
per fragment — each with its own full `prose`, which is the largest field.

**Example, measured.** laisun's note 4 arrives as **7 fragments**, note 6 as 7, note 15 as 6 — the
block is 39 entries for **12 distinct notes**.

| | as sent | one entry per note | saving |
|---|---|---|---|
| laisun | 38,965 tokens | 23,776 | **15,189 — 39%** |
| suncreate | 14,609 | 10,851 | **3,757 — 26%** |

That block rides in every request that selects those notes, so on laisun's 77 requests it is the
dominant cost of the run.

**Solutions, in the order they should be taken.**

1. **Collapse by note number** in `identified_notes`: keep the longest prose, concatenate the rows,
   union the `identified_for`. 26–39% off the note block, and it is the same correction already
   made on the SELECTION side (`notes_for_line_item` caps by note, not fragment — `141aad1`).
2. **Move the block to the system prompt.** It rides in the user message today, which providers do
   not cache, while the cacheable system prefix is ~6,600 characters. Behaviourally identical,
   turns most copies into cache reads.
3. Nothing else is needed — per-request scoping is already done (`build_request` slices the
   identified notes to those THIS request's lines selected).

**Instrument:** `scripts/preflight_live_run.py`.

---

## 3. A note-sourced figure destroys the printed one — PARKED

**DECIDED:** do not fix for now.

`note_sourced._write` sets `value` **and** `value_raw` to the same amount. `value_raw` is supposed
to hold what the filing printed — `stages/note_tag_gate.py:20` documents that convention and
follows it, and `stages/residual.py:536` falls back to it.

**Example.** The face prints total depreciation 587,417; a note discloses the operating-expense
share and that is written to the line. The 587,417 is gone, and the differs-from-printed check has
lost its comparand.

`line_item_llm._write_prose` preserves it correctly, so the two writers disagree — which makes this
cheap to fix by copying the one that is right, whenever it is wanted.

---

## 4. PRC vocabulary — do it at BOTH levels

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

## 10. No PRC measurement beyond the corpus dumps

Everything in item 4 rests on the 18 dumps in `_vocab/`, which are extraction output. Where a
finding says "the document does not contain X", that needs confirming against the PDF before it is
treated as an extraction defect rather than a dump artefact — item 4(b) especially.
