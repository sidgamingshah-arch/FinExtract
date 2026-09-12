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

**What to do about it.** Either give the line an `exclude` the request can act on (a class total is
not the reported total), or move the qualifier out of the definition's tail into `exclude_criteria`
where `line_item_payload` sends it as its own field. This is exactly the class of problem a live
run exists to surface, and it is cheap to fix.

**Reproduce:** `scratchpad/live_run.py` — deterministic baseline then live, same configuration,
`llm_mapping` the only difference, every focus figure diffed.

---

## 10. No PRC measurement beyond the corpus dumps

Everything in item 4 rests on the 18 dumps in `_vocab/`, which are extraction output. Where a
finding says "the document does not contain X", that needs confirming against the PDF before it is
treated as an extraction defect rather than a dump artefact — item 4(b) especially.
