# Extraction pipeline

Each stage is `run(doc, ctx) -> DocumentModel`, *enriching* an immutable-ish document
model (`app/core/stage.py`, `app/core/models/document.py`). A BLOCKER integrity finding
short-circuits the rest; everything else runs to completion so all findings and partial
results are available (`app/core/pipeline.py::Pipeline.run`).

## Stages

**Twenty-four stages, assembled by `app/core/pipeline.py::default_pipeline()`.** That
function is the only place the order is stated; `api/routes/extractions.py::pipeline_stage_names`
reads the list off it rather than keeping a copy, and the run row records the list it was
queued with. Do not add a third copy — the list below names each stage and its file, and
its order is `default_pipeline()`'s:

`ingest · integrity · language_detect · classify · extract · map_line_items · residual ·
normalize · link_notes · line_item_llm · note_sourced · contingent_liabilities · assemble_components · shared_figures · reconcile · prune_notes · confidence ·
gap_closing · face_mapping_contract · note_tag_gate · structural · row_suggestions ·
disclosure_reading · segment`

**Why the list is four stages shorter, and why six output lines are now blank.** Five
*derivation* services used to sit between `link_notes` and `reconcile`, and they have been
removed: `deprec_impairment` (532 lines, ~208 enumerated entries), `contingent_liabilities`'
number half (761 lines total, ~172 enumerated branches — 26 note titles, 23 amount labels, 14
non-exposure phrases, 72 classifiers, 19 matter types), `secur_fincl_assets` (619 lines, ~91),
`related_party_receivables` (690 lines, ~73) and `sales_revenues` (129 lines, ~18) —
**2,731 lines carrying ~562 enumerated entries** between them. Each one COMPUTED a figure for a
named output line out of a hand-enumerated list of note titles, row captions and formula
variants, instead of the figure being read off a caption the configuration describes; together
they were the largest body of filing-specific logic in the codebase, and the reason six of the
eight target output lines had no configuration-driven source. `services/computed_paths.py` — the
precedence policy that arbitrated between those derivations and the rulebook's own reading —
went with them, since there is nothing left to arbitrate. **The figures now come from the
line-item configuration**: an alias, a definition and a cascade authored on the Line items
screen, tuned against the specification documents that remain in `docs/`
(`HKEX_Depreciation_*`, `HKEX_Securities_*`, `PRC_Related_Party_*`, `PRC_Sales_Revenues_*`,
`PRC_Contingent_Liabilities_*` — specs for the configuration, no longer descriptions of code).
A cell with no configured source is blank *by design*; the fix is a line-item definition, never
a reinstated stage. Only the contingent-liabilities **disclosure** survives as a stage, because
its output is a paragraph rather than a number.

**Two passes over the first four.** `services/documents.py::analyze_document` runs
`ingest · integrity · language_detect · classify` alone at upload, synchronously, so the
integrity gate and the page scope exist before an extraction is started. The run then goes
through `default_pipeline()` from the beginning, re-reading the stored bytes — so a run
depends on nothing computed at upload and is reproducible from the file alone.

### Functional ordering

The face and its cited notes are processed in this order:

1. Reconstruction identifies the printed statement subsection and stores face rows in that
   subsection.
2. Cited notes stay linked to their face row and subsection.
3. Dedicated line items are matched inside that subsection, ordered by `match_priority`.
4. A reconciled cited note may populate dedicated template concepts first. Unmatched detail from
   the same reconciled note retains the original face concept; a concept already present on the
   face is never loaded again from the note.
5. Only after dedicated face/note mapping does the subsection residual claim remaining face rows.
   If no legitimate subsection residual exists, a unique `engine_unclassified_face` storage key
   keeps the row visible and remappable without entering a real concept's arithmetic.
6. Template rollups are evaluated after their components. A printed subtotal maps to the same
   `extract_or_derive` concept but remains the reported validation value; the frontend and export
   show the computed amount and raise a calculated-mismatch finding when the two differ.

1. **Ingest & route** (`stages/ingest.py`) — MIME/magic detection (not extension).
   Excel → openpyxl (one "page" per sheet); PDF → **per-page** native-vs-scanned detection
   using PyMuPDF text-char-count, text-area coverage, and image-area coverage; image →
   scanned. A page with a text layer *and* more than half its area covered by images is
   `MIXED`. Mixed documents are handled per page, never a document-level flag.
2. **Integrity** (`stages/integrity.py`) — collects **all** findings into an
   `IntegrityReport` (BLOCKER/WARNING/INFO). Nine checks: unrecognised format, corruption,
   password/encryption, no-pages, blank pages, rotation, inconsistent page dimensions,
   hidden Excel sheets, mixed-scan ratio. BLOCKER halts the pipeline, and
   `POST /documents/{id}/extractions` refuses the run at the API boundary rather than
   returning a misleading empty success.
3. **Language detect** (stage name `language_detect`;
   `stages/language.py::LanguageDetectStage`) — sets the document `locale` (drives OCR
   pack, number parsing, line-item aliases). A
   dependency-free script/keyword heuristic (`detect_locale`) covers the seed set; a
   statistical detector (`fasttext`, the `lang` extra) is **not wired in** — the extra
   exists so one can be, nothing imports it today.
4. **Classify** (`stages/classify.py`) — locates FACE (BS/P&L/CF/Equity) vs NOTES vs OTHER.
   No longer a per-page title regex: per-page evidence becomes an **emission** score,
   document order becomes **transition** costs, and a **Viterbi decode** picks the page-kind
   sequence that best explains the whole filing — so one strong title cannot flip the rest
   of the document, and an HK filing that prints the company-only balance sheet *after*
   note 40 can still be recovered. The lexicon carries STRONG (self-anchoring) and WEAK
   (anchor-requiring) patterns per statement, in English and Han. Layout features
   participate — the page's title zone, numeric-token density, running-header suppression —
   and an ambiguous title is recorded in `page.evidence["title_ambig"]` rather than silently
   decided. There is **no LLM tie-break** in this stage. Notes pages are kept (needed for
   §20), and titles that looked like a statement and resolved to nothing land in
   `DocumentModel.unmapped_titles` so the lexicon's coverage is measurable.

   **An ordering layer runs over the decoded sequence** (`_notes_follow_the_face`): a filing
   states its statements and then explains them, so **no notes page precedes the face**. A
   page decoded NOTES before any face page is corrected to OTHER — the numbered-heading
   feature fires on any "1. …" run and front matter is full of them (contents, an auditor's
   report, corporate information), while `PRE → NOTES` costs only 1.0, so a strong enough
   numbered page ahead of the statements could enter the notes state early and everything
   after it then read as notes-or-later. It is corrected to OTHER and never to FACE: the
   page's own evidence did not look like a statement, so refusing the notes reading is all
   the invariant licenses. This is **not** the claim that the face never follows the notes —
   an HKEX filing prints the company-only balance sheet past note 40 — so only the FIRST
   face page anchors it. With no face page anywhere the layer does nothing and logs why:
   a notes section uploaded on its own would otherwise lose every page it has.

   The layer also protects `seen_notes`, which licenses reading an untitled face page as the
   COMPANY's re-presentation of a Group statement. A front-matter page latching that flag
   before the statements were reached could make a filing's own balance sheet read as the
   Company's second copy of one.

   **On a US 10-K, the notes run to their last note and no further** (`_US_10K_NOTE_HEADING`,
   `_US_10K_BACK_PART`, on only when `services.regime.is_us_10k_filing` says so). A 10-K heads
   its notes "Note 6 – Consolidated Financial Statement Details" or "Note 15.    Information
   about Segments", which the numbered-heading pattern never matched (it is case-sensitive and
   wants a space after the number), so a notes page had nothing but its figure density to go on.
   Apple's FY2025 Notes 4 (continued) to 13 — pages 40-50 — were decoded OTHER, and its exhibits
   (the certifications' numbered paragraphs read as note headings) NOTES; Alphabet's pages from
   Item 9 to the signature block were NOTES. Now a "Note N –" heading anywhere on the page is a
   note heading, and a page that OPENS with Item 9-16, Part III or IV, the signatures or an
   exhibit reads as narrative, as an auditor's report does. Apple's notes are pages 36-50 and
   Alphabet's 53-88, each to its last note; every other filing in the corpus, the 10-K layout
   fixtures and the e2e fixtures decode exactly as before.
5. **Extract** (`stages/extract.py`) — rows → `LineItem`s with values keyed by
   (basis, period), note refs, unit context, and provenance. **Table reconstruction happens
   inside this stage**, which is why there is no separate `reconstruct` stage (the comment
   in `default_pipeline()` says so): native pages go through the PyMuPDF text layer into
   `services/row_reconstruct.py`, scanned pages and standalone images go through the OCR
   port into the *same* `row_reconstruct` logic, and Excel is read cell-by-cell by
   `services/excel_extract.py`. The reading rules (which printed column is the Group's,
   which is the current period, what scale the figures are in) come from the rulebook the
   *run* was pinned to — `reconstruction_rules(ctx)`, not whichever rulebook is currently
   in force.
6. **Map** (`map_line_items` — `stages/map_ontology.py` + `services/mapping.py`) — the stage
   NAME and its module name differ, and deliberately: the name is what a run record, the progress
   API and every log line report, and it says line items because that is the only configuration
   there is; the module keeps its filename so the swap cost no import churn. The multi-strategy matcher
   (below). **Its input is the line-item set.** The matcher's questions are still asked of
   an `OntologyDefinition`, but that object is now *built from* the run's `LineItemSet` by
   `services/working_view.py::build_working_view` on the way in — it is a derived,
   in-memory view, never stored, never served and never authored. Line items is the single
   configuration engine; the working view is the adapter that made rewriting three thousand
   lines of proven matcher unnecessary. It is the exact inverse of
   `services/ontology_projection.py` (which had gone the other way, turning 462 rulebook
   concepts into line-item definitions so the set could *be* the rulebook) and it imports
   that module's field lists rather than restating them, because a projection and its
   inverse that each keep their own copy drift silently — a drift moves which line item a
   caption resolves to while every subtotal still ties.
7. **Residual** (`stages/residual.py`) — a printed face line that matched no specific
   concept goes to its own section's residual bucket ("Others") instead of vanishing from
   the statement. Section placement is decided by structure, strongest signal first: the
   section banner above the row, then the next section subtotal at a higher ordinal, then
   the section of the nearest preceding mapped line. With a `schema_version: 2` rulebook
   loaded, *every term of the sweep* is read from the rulebook's `residual_framework`
   (when it runs, which rows are eligible, what a component must record, the identity the
   section must then satisfy, the conditions that send a section to review), overridable
   per concept by `residual_policy` / `never_sweep`.
8. **Normalize** (`stages/normalize.py`) — sign detection and unit-context resolution:
   `Less:`/`Add:` label cues, the line item's `sign_rule.flip_if_label_matches`, and the
   printed-sign tier (parentheses / trailing minus) already decoded by `services/numbers.py`
   at extraction. It also *checks* the rulebook's `sign_convention`
   (`positive_expected` / `negative_expected` / `either`), which is an **expectation, not a
   transformation** — a figure arriving with the opposite sign is flagged and its sign
   confidence drops; the value is left as reported.
9. **Link notes** (`stages/link_notes.py`) — builds `FaceNoteLink`s from each face line's
   citations against an index of the extracted `NotesTable`s, labelling the relationship
   (`ONE_TO_ONE` / `NOTE_SPLITS_TO_MANY_FACE` / `MANY_NOTES_TO_ONE_FACE`) from the citation
   counts. Amount validation is the **reconcile** stage's job, not this one's — see
   [03-reconciliation](03-reconciliation.md).

   **Which notes a row cites is one definition, `LineItem.cited_notes_among`**, shared with
   *prune notes* (what gets published) and the **segment** stage (which section a note is filed
   under). Three stages reading citations three ways meant a filing could have a note published
   but unlinked, or linked but filed where its citing row could not see it. A printed
   sub-reference resolves to its **parent** note when the sub-part is not itself a table —
   a balance sheet saying `16(b)` beside a figure and a notes section printing one table
   numbered `16` is the common HK house style, and the citation as printed matches nothing. The
   fallback applies only when the citation itself names no table, so no row is ever tied to both
   a sub-note and its parent — the reconciliation would subtract the same detail twice.
10. **Line-item requests** (`stages/line_item_llm.py` + `services/line_item_llm.py`) — **the
    only place a model is asked anything about a figure.** For a line item the configuration
    already names, it asks WHERE IN ITS SELECTED NOTES that figure is printed. The answer is a
    CITATION — a note number and a row caption, quoted as the document prints it — and
    `note_sourced.resolve_sources` matches it back against the extracted rows, taking the page and
    the figure OFF THE ROW. The model is given no page index, no bbox and no licence to state an
    amount.

    *What it replaces, and why it is not the same question.* A BATCHED ROW request used to run
    inside `map_line_items`: printed captions grouped by (statement, basis, period), each call
    asking the model to pick a concept from a candidate list. That needed a candidate list, a
    statement/section gate to grade the answer against, a cap on how many concepts fit in one
    call, a thread pool, and a deterministic fallback for every row it forwarded — all of it
    because THE LINE WAS THE UNKNOWN. Here the line is given, so there is nothing to force-fit:
    an answer is a citation, or an empty `sources` meaning "this filing does not state it", which
    is a real answer rather than a failure. Concept mapping is now settled by the deterministic
    ensemble alone and `map_line_items` makes no provider call at all.

    *The one figure the model may state* is an amount disclosed in PROSE and in no table row
    ("Depreciation charges of approximately HK$529,841,000 are included in other operating
    expenses"). It is verified against the note's own text before it is accepted — so the model is
    LOCATING a printed number rather than supplying one — and it is divided by
    `unit_context.scale_factor`, because a sentence states its amount in full where a table states
    it in the statement's units.

    *A row the model's answer creates carries the statement's unit.* A fresh slot takes the row's
    other column's unit, else the face's (`note_sourced._statement_unit`) — how the declared note
    route stamps a note figure, because the figure joins the statement's rows in every check that
    adds them. Each resolved citation carries its row's unit (`note_sourced._unit_of`), and where
    the cited rows declare a scale or currency the statement's declared unit differs from, the slot
    takes the cited unit instead: this route writes the amount as printed and does not rescale it,
    so the checks refuse to add it to the statement's figures (`mixed_scale`). Only what both sides
    declare is compared (`services.units.compare`): on 佳明 the face's scale was never read, so a
    figure cited from a note at HK$'000 takes the face's scale, not the note's. Cited rows in two
    units give no unit. A prose money amount, once divided into the statements' units, takes
    theirs; a count takes none. A slot that already exists keeps its own unit.

    *Which lines are asked about.* `line_item_requests.asked_about` is the boundary: a DERIVED
    PARENT is never asked, because its figure is its declared cascade's and a number written
    straight onto it skips every rung, losing which disclosure it came from and the cross-check
    between rungs. A residual bucket is never asked either — it carries a section's unexplained
    remainder, so a figure filed there makes the reconciliation that would have REPORTED the gap
    tie instead.

    *Which notes a line is shown* (`line_item_notes.note_sets`), in this order, each source
    adding only what the ones before it did not give: the notes the line DECLARES
    (`declared_notes`); the notes the filing CITES beside its face row (`cited_notes`); the notes
    whose HEADING names the line's note terms, scored by the words they share weighted by rarity
    (`notes_for_line_item`, floor `MIN_SCORE`, at most four); a note printing a row under the
    line's own name when the heading search came up short (`named_row_notes`); the note the
    line's note points to (`referenced_notes`). Then two layers that only ever add:

    - *The shared synonym list* (`services.heading_synonyms`, `extraction.llm_note_synonyms`, on).
      Concepts written down once in English, Simplified and Traditional Chinese — "fixed assets"
      is "property, plant and equipment" is 固定资产. A line whose own term IS one of a concept's
      spellings is shown a note whose heading PRINTS another of them, at most `SYNONYM_CAP` (2),
      the note printing the most figures first, marked `via="synonym"`. A term that only contains
      a spelling ("Profit before income tax") names nothing; a short spelling is printed only as a
      whole heading; the longest spelling a heading prints decides its concept (递延所得税 is
      deferred tax, not income tax); a spelling a sibling part names is the sibling's. Measured on
      18 filings: 47 notes added to 34 of 2,826 line-filings, none removed, 46 the right note by
      concept — "Net trade receivables", configured in English, reaches 应收账款 on all eleven
      CAS filings.
    - *The note router* (`services.note_router`, `extraction.llm_note_router`, on). ONE call per
      filing, before the line requests: the model is shown every note heading once (number,
      heading, chapter, a few sub-headings, whether it prints a figure) and each asked-about
      line's configuration (definition cut to 400 characters, note terms, row terms,
      `note_terms_none`, and `part_of` for a part — parts of one figure are split by the note
      each is printed in), and names up to three notes per line that print its figure. The reply
      is checked — an unknown key, an unknown note number or a second answer is dropped and
      counted — and at most `ROUTED_CAP` (2) notes no other source gave are added, marked
      `via="routed"`. A failed call is logged (`line_item_llm:note_router FAILED …`) and the sets
      are the configuration's own. Measured with stand-in answers on the five reference filings
      and Alphabet: 171 picks for 140 lines, 148 of them notes the configuration already gave the
      line, 13 kept out by the fences below or the cap, 10 added — 7 printing a row the line's own row rule
      takes (the non-current FVOCI line reaches 其他权益工具投资 on all three CAS filings; 1966's
      right-of-use depreciation reaches its LEASES note) — at one call of 4,000–16,000 tokens.

    Both layers pass the same fences: never an accounting-policy note (`names_policy`), another
    entity's note, a note printing no figure for a line that reads a row, or a note
    `claimed_notes` gives a SIBLING PART of the same figure (`sibling_claims`) — the router's
    first prompt called fixed assets and property, plant and equipment one subject, and its
    answers sent both depreciation parts to the same note on every reference filing. The run log
    gives `line_item_llm:note_router` (lines, notes, picks, tokens) and `line_item_llm:note_search`
    (over the lines asked: notes each layer added; `routed_agreed`, the router's picks the
    configuration already held; `sibling_refused`).

    The claims that give each note to one part (`claimed_notes`), the deterministic route and the
    document's note text are not changed by either layer; a note added to a line's set has its
    text added to the request (`plan_and_notes`).

    *Which lines share a request.* `extraction.llm_request_grouping` — `none` (one per line, the
    per-line baseline and the only mode whose answer is attributable to one line), `identical`
    (shipped), `similar` (`llm_group_similarity`) or `manual` (the groups authored on the set).
    What grouping buys is the CONTEXT BLOCK a request carries — a note set, or a statement's
    printed rows — paid once for the lines that need it. Under `none`, with the focus list off, a
    run on the HK set made 421 line-item requests, 349 of them one face line each sent its whole
    statement (the balance sheet's rows went out 186 times on 688411). `identical` asks together
    the lines that select the same notes, and the face lines claimable under the same statement
    sections, at most `extraction.llm_lines_per_request` (20) to a request, because a line-item
    request waits `llm.timeout_seconds` (120) and its reply — about 125 tokens a line at the
    median — has to arrive inside it. A larger group is asked in even parts in its own order
    (`line_item_requests.capped`: 77 at 20 is 20, 19, 19, 19), each sent the group's notes and the
    statement rows its own lines may read. The stage cuts what it SENDS: after the focus list, the
    note-tag gate, the text lines and the `anywhere` lines have taken theirs, so the P&L's 89
    planned lines, 77 once its 12 `anywhere` lines go to the report, go in four requests, not
    five. The text lines are split by kind (phrase and narrative) before the cut
    (`line_item_requests.split_text_plans`), so they too are cut by what is sent together; the
    `anywhere` lines' report batches, at most 12 of their own, are cut only by a cap below 12.
    `line_item_requests.as_sent` takes plans through the same steps for the scripts that price and
    sweep them, and a test holds it to what the stage sends. The cap does not cut an authored
    `manual` group. Lines sharing a request can influence
    each other's answers; the per-line fences (a line cites only its own notes, an answer about a
    line not asked is refused, two lines citing one printed row are settled after the run) are the
    same in every mode. A "none" saved by an older Settings screen — which saves every setting
    whenever one is changed, and so stored the old default — is read as the shipped default once,
    logged, and saved with a marker (`settings_state._grouping_as_saved`); a "none" chosen now is
    kept.

    Measured on the six reference filings with a stand-in that records every call and answers
    nothing (no model): all model calls of a run, every stage —

    | filing | `none` (before) | `identical`, 20 a request (now) |
    | --- | --- | --- |
    | 688411 海博思创 | 432 | 71 |
    | 688008 澜起科技 | 433 | 75 |
    | 000709 河钢股份 | 431 | 69 |
    | 300319 麦捷科技 | 432 | 72 |
    | hkex_1966 China SCE | 433 | 67 |
    | hkex_kaming 佳明 | 431 | 60 |

    Of those, the line-item stage's: 421 before (418 figure requests, 2 text requests and the note
    router's one call), 50-63 now (47-60 figure, 2 text, the router); the largest request is 20
    lines, a reply budget of 9,024 tokens, and the text the stage sends outside a cached first
    message falls 6 to 8 times (688411: 11.1M to 1.4M characters). The rest are the disclosure
    readings (8), the unmapped-row suggestions (1), the credit narrative (1) and, on some filings,
    the contingent liabilities narrative and a shared-figure tie-break.

    *A request with nothing to read is not sent.* One that would carry no note, no statement row
    and no other page — a note part whose notes this filing does not print (`sub__ppe_depreciation`,
    `sub__st_borrowings_current_portion_of_ltd` …) — can only be answered "not found". The stage
    names its lines (`line_item_llm:nothing_to_read`) and the rules' reading stands: 13-22 such
    requests a filing under `none`, 5-8 under `identical`.

    *A focus run asks its focus lines and no other.* With `llm_focus_only` on, each planned group
    keeps only its focus lines (`line_item_requests.focused`, with the notes and sections it was
    planned with, and named after the lines it holds), a group with none is not asked, and a kept
    group is then cut by the cap like any other — so every line outside its template's list on the
    Focus line items master (one list per template, `line_item_requests.focus_list`) keeps the
    rules' answer. The log counts these in groups (`focus keys=… groups=kept/all`,
    `groups_skipped_by_focus=`), because the plans are cut into requests later.

    *An `anywhere` line is asked with the whole annual report.* Its plan is set aside
    (`line_item_requests.report_plans`): the line goes into a batch of at most 12, grouped by the
    statement it is printed on. That batch is sent the report (`line_item_llm.build_report`): every
    page's text, every note's rows, every statement and every other page's rows. The report goes
    as one message after the system text and before the question, and is the same bytes on every
    request of the run. The system text gains one fixed paragraph (`REPORT_GUIDE`). Text lines
    with this route are asked the same way, under the text contract.

    *A narrative line is the one line the model writes.* A `prose` line (on the 10-K set, the
    contingent-liabilities and related-party narratives, at the user's request) is asked like a
    text line, in a batch of its own, under `PROSE_REPLY_CONTRACT`: write what its prompt asks
    from the supplied pages alone, cite the pages, copy every amount, date and name as printed.
    The answer is checked before it is written (`text_lines.check_prose`): every cited page was
    sent and every number in the text is printed on one of them, or it is refused and logged.
    One that passes is a `value_text` fact flagged `llm_prose:pages …`, so the export and the
    screens can tell it from words the filing prints (`llm_text:page …`).

    Such a line may cite a sentence on any page by its page, amount and quote. The amount is
    checked against that page's text (`note_sourced._page_sentence`). A number printed with no
    currency beside it is not divided by the statements' scale. The figure is written with that
    page as its provenance, as a cited row's is, so a `carved_from_face` line read from a sentence
    is carved like one read from a row (`note_sourced.carve_note_figures`, which carves a figure
    read off any page but a statement's).

    A report over `REPORT_TOKEN_BUDGET` (900,000 estimated tokens) is not cut down: its lines are
    asked as planned, with the selected context, and the log says so. Every other line's request
    is unchanged.

    *What the model reads with each question* is one switch, `extraction.llm_document_context`
    (Settings screen), cheapest first:

    - `selected`: every request carries only what its lines selected. No line is sent the
      report; an `anywhere` line is asked in its planned request, with the pages that print its
      names.
    - `anywhere` (the default): what is described above.
    - `full`: the report goes with every question, figure and text alike, under one system text
      (`FULL_REPORT_GUIDE` in place of `REPORT_GUIDE`). Every figure request then has the same
      prefix. Each line's `read_from` still says where it may be cited from, and the route fences
      still hold.

    *Prompt caching.* The Anthropic-format adapters (`anthropic`, `bedrock_gateway`) send the
    system text, and a message marked `cache` (the report), as text blocks ending in a
    `cache_control` breakpoint (`adapters._structured.messages_body`; off with
    `llm.prompt_cache = false`). The first request of each prefix is sent on its own and waited
    for, so the requests after it read the prefix from the provider's cache; the rest go
    `extraction.llm_parallel_requests` at a time (4 as shipped, 16 at most). The whole-PDF requests
    follow the same setting, the first of each part alone; the disclosure readings follow it too,
    this many at once from the start (they share no prompt beginning); the few text-line requests
    go one at a time. Replies are processed in plan order whatever it is, so it changes how long a
    run takes and not what it finds. The run log gives every request's input, cached and
    cache-write tokens.

    *Notes many requests share are sent once, cached* (`extraction.llm_shared_notes`, on). A
    request's question carries the notes its lines selected, and no provider caches a question,
    so a note ten requests need was paid for ten times. Where the provider declares
    `prompt_cache_prices` (the two Anthropic-format adapters with `llm.prompt_cache`) and no
    report is in front of the question, a note goes into one block sent as the request's first
    message, marked `cache`, when more requests carry it than the cached block would cost it:
    `write + read·(R−1)` requests, R being the requests that carry a note
    (`line_item_llm.shared_note_ids`). Every other note stays in its question, and a request
    carrying none of the shared notes is sent as before. Sending every note once was measured and
    rejected: it cost more than today on four of the five reference filings, because a request
    that needs two notes would read forty. A line may still cite only the notes it selected —
    the fences key on whether the request was sent the REPORT, not on whether it has a first
    message — so a shared note in front of a request whose lines did not select it is refused if
    cited. The log line `line_item_llm:shared_notes` says how many notes, how many tokens and how
    many requests.

    *Position.* Immediately before `note_sourced`, the deterministic reader of the same
    `note_source` declarations. Every site that stage writes a figure asks `_llm_holds` first, so a
    line answered here keeps its answer and the declared route fills what was left empty — neither
    route overwrites the other.

11. **Note-sourced fills** (`stages/note_sourced.py` + `services/note_sourced.py`) — fills every
    line item that declares WHERE IN THE NOTES its figure lives. `LineItemDef.note_source` names
    regexes for the note's title, for the row captions that COUNT, and for the captions that must
    be EXCLUDED even when a counting pattern claimed them (which is how "depreciation" stops
    picking up "accumulated depreciation" and a fixed-asset table's movement rows).

    *What it replaces.* Thirteen shipped sub-line items author ~650 such patterns, in English,
    Traditional and Simplified Chinese. Nothing read them: the five removed derivation services
    did, and no generic reader took over — so every pattern was authored, versioned, shown on the
    configuration screen and consulted on no run. This reads the DECLARATION, so it serves the
    thirteen that exist and the seventieth nobody has authored yet, with no code change.

    *The two arithmetics, which are not the same question.* WITHIN one note the matched rows are
    components and are summed. ACROSS notes the children are alternative disclosures of one figure,
    and the PARENT's `rollup` decides: `alternatives` takes the first in declared `order`,
    `sum` adds them, `none` carries nothing. Reading the child's rollup instead (which this stage
    first did) makes every parent a summing one, and turns twelve disclosures of one depreciation
    charge into a cost twelve times too large on a statement that still balances.

    *A printed parent always wins.* A parent the filing printed is the filing stating the amount; a
    note-derived one is an inference from a breakdown. The printed figure stands and the
    disagreement is flagged, rather than the inference silently replacing the better number.

    *Position.* After `normalize`, where the removed derivations ran — so a note-derived figure is
    not put through the unsigned-expense cohort vote, which would flip the sign of every expense on
    the statement.

12. **Contingent liabilities** (`stages/contingent_liabilities.py` +
    `services/contingent_liabilities.py`) — **a disclosure stage: it writes narrative, and only
    narrative.** The one output is `DocumentModel.contingent_liabilities` — per
    "basis:period", a summary paragraph plus the classified tables (Letters of Credit,
    Performance bonds, Bank guarantees, Corporate guarantees, then unclassified), summed per
    type AND per currency because unlike units are never converted — rendered as prose for a
    human by the Disclosures screen and by the exports. **Nothing is written onto any
    `LineItem`**: the stage sets no value on any row and takes no part in the grid or in
    reconcile arithmetic. It is the sole survivor of the derivation stages precisely because a
    paragraph is not a number, and it keeps this position — after notes are linked — because
    the narrative is built from linked notes.

    *Removed from it:* the publish onto the ordinary `notes__contingent_liabilities` row. That
    single Decimal came from a total assembled out of **~172 hand-enumerated entries** (26 note
    titles, 23 amount labels, 14 non-exposure phrases, 72 classifier terms, 19 matter types),
    arbitrated against the rulebook's own reading by the deleted `services/computed_paths.py`.
    **The figure must now come from the line-item configuration** — an alias binding the
    或有负债 / contingent-liability caption. Until the configuration describes it that cell is
    blank, deliberately. Specification for the tuning:
    `docs/PRC_Contingent_Liabilities_Extraction_Logic_Revised.md`.

13. **Assemble components** (`stages/assemble_components.py` +
    `services/assemble_components.py`) — SEVERAL PRINTED ROWS MAY BE ONE LINE ITEM'S FIGURE. A
    note that splits a total by function prints four rows that all belong on one line; before
    this, two rows on one concept was an `ambiguous_mapping` and the line went unfilled, because
    that flag cannot tell a genuine component set from a face line repeating the note total behind
    it. Those two cases are indistinguishable afterwards — both produce a bigger number on a
    statement that still balances — so the MODEL DECLARES which it is (`LlmBatchItem.role`,
    `"whole"` or `"component"`, with a `sign`), the mapper marks the row, and only a declared
    component is summed here. Two undeclared rows on one key stay ambiguous. A declared component
    sitting beside a row that states the whole figure is REFUSED and flagged rather than resolved
    either way, because both readings invent a total the filing does not print. Each contribution
    is recorded through `services/derivation.py` — the caption, note and page off the ROW's own
    provenance, the model's reason for including it, and the sign and total — which is what the
    statement inspector already renders as contributions with click-to-source. **Nothing here
    enumerates a caption:** which rows are components was decided by the configuration and the
    model. Runs after `normalize` (components must share a scale and sign before they are added)
    and before `reconcile` (which checks the assembled figure against a printed subtotal).
14. **Shared figures** (`stages/shared_figures.py` + `services/shared_figures.py`) — ONE PRINTED
    FIGURE CLAIMED BY TWO LINE ITEMS, and the model decides which it belongs on. The mirror image
    of stage 13: component assembly answers "several printed rows are one line's figure", this
    answers "one printed figure has been mapped onto several lines" — same evidence, opposite
    direction, and the second is the one that publishes a number twice. Measured:
    `sub__ppe_depreciation` and `sub__fixed_asset_depreciation` both read 366,943,014.10 from one
    note, and their parent's rung sums all five asset lines, so the charge published doubled at
    237,254,155.34 against a true 118,627,077.67.
    Detection is deterministic and knows nothing about the concepts: the same printed cell (page
    and box), or — for a note-sourced figure, which carries no provenance of its own — the note
    rows its derivation trail names. A parent carrying its own child's figure is never a contest,
    because that is the declared arithmetic. The decision is the provider's and has two shapes:
    keep the figure on every line that claimed it (different cuts of one disclosure), or on one
    alone. Runs *after* every stage that writes a figure and *before* `reconcile` — a tie checked
    against a figure counted twice would report a tie that is not true. With no provider, or
    `extraction.llm_shared_figure_tiebreak` off, every contest is recorded on
    `doc.shared_figures` and flagged on the rows and NO figure moves. An answer whose confidence
    is below `extraction.llm_shared_figure_min_confidence` (default 0.6) is treated the same way:
    its proposal is recorded, the contest is flagged `shared_figure_unresolved`, nothing is removed.

15. **Reconcile** (`stages/reconcile.py` + `services/reconcile.py`) — the §20 subtraction
    and the note→face tie grading (see [03-reconciliation](03-reconciliation.md)).
16. **Prune notes** (`stages/prune_notes.py`) — publishes only the notes a face line
    actually references; accounting policies, governance tables and subsequent events are
    noise in the notes index, the export and the review queue. Runs *after* reconcile,
    which needs every extracted note to check the ties. Nothing is deleted from the source
    or from provenance — the log records exactly what was dropped.
17. **Confidence** (`stages/confidence.py`) — sets the `validation` sub-signal on extracted
    values from the checks available at this point (the balance-sheet identity per
    (basis, period), and the note→face tie from the reconcile report), so
    `ConfidenceVector.overall` is capped by participation in a failed check rather than
    reporting a clean OCR/mapping as confident. The row-based rule catalog that populates
    the review queue runs at the API layer instead — see
    [02-data-model-and-schemas](02-data-model-and-schemas.md#validation-engine-feeds-the-review-queue).
18. **Gap closing** (`stages/gap_closing.py` + `services/gap_closing.py`) — a subtotal that
    still does not tie may be missing a line the mapper could not place. **Arithmetic
    proposes and the model disposes**: only a subset of leftovers that closes the gap in
    *both* periods within tolerance is offered (one period is a coincidence, two is
    evidence), and the LLM picks one of those subsets or none — because whether "Pledged
    bank deposits" belongs under current assets is a question about meaning. Deliberately
    **before** the structural checks, so a gap the model closes reports as tied rather than
    as a defect the analyst has to chase. Gated on `extraction.llm_gap_routing` (default on)
    and on a non-`stub` provider; with neither, the gap stays a review item, which is the
    honest outcome. Confirmed routings are kept on `DocumentModel.gap_routings` so the
    decision is inspectable rather than an unexplained change of mapping.
19. **Face mapping contract** (`stages/face_mapping_contract.py`) — the final mapping
   invariant for a run carrying a line-item set. Every face row with a value must either have
   a canonical key or be a verified non-additive aggregate replaced by mapped components.
   Anything else receives a unique `engine_unclassified_face` key outside every line-item and
   template namespace, so it remains stored and appears in the remapping queue without entering
   any calculation. It is never assigned a neighbouring real line item merely to make the
   unmapped count zero. Extraction-only runs that carry no line-item set skip this gate because
   they are not mapping runs.
20. **Note-tag gate** (`stages/note_tag_gate.py`) — a line declaring
    `llm_only_if_note_tagged` reports **0** (or `""` for a phrase/prose line) where the face
    printed no note reference beside its row. Absence of the tag is a statement of
    non-disclosure, not a gap: a blank says "we did not find it", a zero says "the filing does
    not disclose it", and for a line only ever disclosed in a note those differ. The zero is
    unconditional and overwrites what the caption tiers read, which makes this the one stage that
    deliberately discards a tier's answer — so `value_raw` keeps the printed figure and the value
    carries `note_tag_absent_zeroed:<figure>` naming what was displaced. The position is
    load-bearing on both sides: every stage that writes a figure is above it, so the zero is undone
    by nothing; and it is above the structural checks, so a reconciliation cannot report a tie
    against a figure this stage removed. Its other half lives in `map_line_items`, which spends no
    provider call on such a row.
21. **Structural** (`stages/structural.py` + `services/structural_checks.py`) — runs the
    arithmetic the template and the rulebook *declare*: template `rollup`s and statement
    `identities`, the rulebook's `validation.identities`, its
    `validation.cross_concept_guards` and its `validation.section_reconciliation`. Every
    relation produces a row **including the ones that could not be run**, each skip
    carrying a classifiable `reason` (`services/coverage.py`), so partial coverage is
    visible rather than implied. A failure flags the participating line items and values.

22. **Row suggestions** (`stages/row_suggestions.py` + `services/row_suggestions.py`,
    `extraction.llm_row_suggestions`, on) — ONE model call per filing for the printed rows that
    end the run reaching no line: exactly the rows that raise an `unmapped` review card, read
    through the card's own test (`documents._unmapped_card_positions`). Each is shown with its
    statement (its page's), section banner, the heading above it, its figures and the two rows
    printed on each side; beside them, the template's lines for those statements only — the
    card's own remap targets, so never a calculated subtotal or a header — with each line's
    definition cut to 220 characters. The model names the one line a row belongs on, or none.
    The reply is checked: a key off the row's own statement, a line the request did not list, or
    a row number not asked about is dropped and counted. **It suggests and never maps**: the
    suggestion is written on the row (`LineItem.suggestion`, served as `suggestion`) and the row
    stays unmapped; the card offers it as `remap.suggested` — dropped at read time when the
    template no longer holds the line — and "Use this line" only fills the pick and the reason,
    so the row moves when a person applies the same remap a hand pick goes through. A failed call
    is logged (`row_suggestions:FAILED …`) and the cards are shown without one.

23. **Disclosure reading** (`stages/disclosure_reading.py` + `services/disclosure_reading.py`,
    `extraction.llm_disclosures`, on) — the Disclosures screen's eight topics (auditor's opinion,
    going concern, contingent liabilities, guarantees, commitments, related parties, subsequent
    events, litigation) READ by the model, one call per topic, each with its own prompt. The
    presence scan (`derived.scan_disclosures`) finds a keyword, and a keyword is not a finding:
    "going concern" is printed in every basis-of-preparation note and every auditor's statement of
    responsibilities. For each topic the stage collects every passage where the topic's words — the
    scan's patterns plus the phrases that mark a real finding ("material uncertainty", "basis for
    qualified opinion", 无重大或有事项 …) — are printed, ranks them, keeps ~6,000 tokens of them in
    page order, and asks the topic's own question: what the topic is, the answers it can have (a
    status code each, e.g. `material_uncertainty` / `doubt_resolved` / `basis_only` / `not_found`),
    what is NOT a finding for it, and what an analyst needs about a real one (counterparty, amount
    as printed, date, status). The reply is checked: the status must be one of the topic's codes; a
    point must quote the passage it cites, or it is dropped; an amount not printed in its quote is
    removed; a summary sentence with a figure no passage prints is removed; a finding with no point
    left is withheld. The checked reading is written to `DocumentModel.disclosure_readings` and the
    worker adds it to the topic's disclosure entry as `reading` — `present`, `page` and `snippet`
    stay the scan's. The credit view's report signals and the supplemental "auditor's opinion" row
    read `disclosure_reading.raises_signal`: the reading where there is one, the scan where there
    is not. No call for a topic the filing never mentions; a failed call costs only its topic.

24. **Segment** (`stages/segment.py` + `services/buckets.py`) — files every face row and
    every note into the **thirteen face sections** an analyst reads a filing in, plus Others:
    the balance sheet's five (current / non-current assets, current / non-current
    liabilities, equity & reserves), the income statement's four (income, expenses,
    **interest**, non-operating income & expenses), the cash flow's three activities
    (operating, investing, financing), and the statement of changes in equity.
    **Last by necessity, not by convention**: a balance sheet prints five of these on a
    single page, so page classification can never separate them — only a row's resolved
    `section_scope` can, which does not exist until `map_line_items` and `residual` have run.
    **Section first, statement second.** Only `equity_changes` still resolves from the
    statement (a reserve's movement through the year is that statement's content, not the
    balance sheet's closing position); P&L and cash flow are split BY section, so a row of
    theirs whose concept resolved no section is Others/`unresolved` rather than filed under
    its page — a coverage fact made visible. The section → tag edge is *derived* from the
    section id's own phrase, and every banner the extractor can read
    (`mapping.HEADING_ROW_SECTIONS`) is asserted at import to have a tag, so the rulebook
    and this layer cannot drift into two ideas of what "current assets" is; a section with no
    tag is reported in `unknown_sections` rather than counted as Others, and the three real
    sections this taxonomy does not name (other comprehensive income, the two "attributable
    to" sections) are listed explicitly so phrase matching cannot pull them into Income.
    **Interest is declared, not derived**: no statement prints an interest section, so the
    two concepts that are interest say so in the rulebook (`analyst_bucket`) — a rule in code
    would also catch "Interests in associates" and "Non-controlling interests", which are not
    interest at all. The stage also back-fills `printed_in` (face vs note) for any row no
    reader stamped, since it is the last thing to see every row and every page kind together.
    Membership only — the figures stay on `line_items` / `notes` and the
    `/documents/{id}/buckets` endpoints join to them at serve time. Every face row lands in
    exactly ONE bucket (so that side is summable, and `unresolved_face_item_ids` separates
    "belongs in Others" from "nothing could place it"); a note cited from two buckets is
    filed in BOTH, because each section needs it in front of the reader — so the notes side
    is deliberately not a partition, `shared_notes` marks the overlap in every bucket
    holding it, and the index serves a distinct-note count.

**Where a design intent diverged.** Earlier revisions of this document described a
separate `stages/reconstruct.py` converging native and OCR pages on the `Table` / `Cell`
core models. That stage was never built and the file does not exist: reconstruction lives
inside `extract`, and the convergence point in practice is `row_reconstruct.Word` →
`LineItem`, not `Table`. `app/core/models/table.py` and `DocumentModel.tables` are still
declared and stay empty; the intent (one table shape whatever the source) survives in the
shared `row_reconstruct` path.

## Mapping — a combination of methods, LLM as the key driver

`services/mapping.py` — mapping a printed label to a canonical concept is a **combination
of methods, none forced out**; the LLM is a *key driver*, not the sole authority. Each
method contributes and they corroborate one another:

2. **Exact / normalized lexical** — identity alias match; short-circuits (free, no tokens).
3. **Rule / fuzzy** — every method runs and contributes candidate evidence (and
   pre-shortlists candidates for the LLM when the line-item set exceeds
   `extraction.llm_candidate_cap`, default 40).
4. **LLM semantic decision** (`extraction.llm_mapping`, default on) — the driver: shown
   the caption plus candidate concepts *with their criteria* (definition, include /
   exclude, confusable-with, value_scope) and the set's global policies + worked
   examples, it chooses by **meaning**. So "Amounts due from customers" → `trade_receivables`
   with no matching alias, and repeated "Others" captions disambiguate by section context.
**Two tiers that used to be here are gone, and the removals are the contract getting shorter
rather than weaker.** A **fuzzy / string-similarity** tier decided rows on wording, which the
rulebook's own `binding.order` never declared and which produced the error nothing downstream can
see (on the shipped rulebook, "Profit before exceptional items and tax" filed as
`pl_profit_before_tax` at 0.61, accepted and unflagged — two subtotals differing by exactly the
exceptional items, so the statement still tied). A **semantic-embedding** tier existed in the
matcher but never ran: `map_ontology.py` built the matcher without a provider and the only
registered one was a stub that raised. Measured on a real 300-page filing, of the 15 rows fuzzy
used to name, 11 now land in their own section's residual *Others* — still on the statement,
still summing, itemised under their printed label — and 3 become unmapped for a human. Alias
similarity survives as a *measurement* (`_alias_similarity`) used by two guards, and can map
nothing on its own.

**Combination policy:** exact wins outright; otherwise the LLM makes the call but is
**corroborated by the deterministic methods** — agreement nudges confidence up; a strong
lexical disagreement lowers it and flags review; the methods that agreed are recorded
(`MappingResult.agreement`). When no LLM is configured or it abstains, the deterministic
ensemble decides with the margin-over-runner-up accept policy (`extraction.mapping_margin`).
Every value also carries an `allocation_status` (direct / child / residual / …) so
parent-child handling is auditable.

`extraction.mapping_scope` chooses the granularity: `per_statement` (the default, most
accurate) batches a statement's captions into one LLM decision; `per_line` maps each
caption independently (cheaper, less context). Which one ran, and *why* — a run that fell
back to the deterministic ensemble because no provider resolved says so — is recorded on
the run result's `mapping` block, so a degraded run is visibly weaker rather than silently
so.

Per-line LLM token usage accumulates on the pipeline context and is recorded on the
extraction run's audit-log entry. Winning method, confidence and per-strategy scores are
stored on the confidence vector; anything short of a confident, corroborated decision goes
to review, not a guess.

## Values & provenance — grounded extraction (LLM references, never transcribes)

Value-level provenance (click-to-source) is kept trustworthy **even with the LLM at the
centre** by a simple rule: **the LLM references facts, it never emits values.**

1. The source is parsed deterministically into atomic facts, each with its exact origin:
   - **Excel** (`services/excel_extract.py`): every numeric row → a `LineItem` whose
     `ExtractedValue.provenance` points at the precise **sheet + cell** (e.g. `P&L!C14`).
   - **Native PDF** (`services/pdf_extract.py` via PyMuPDF text layer): positioned words →
     rows → line items with **page + normalized bbox** provenance; note refs ("Note 15")
     captured, not mistaken for values.
   - **Scanned PDF / image** (same path): the page is rasterized and sent to the configured
     **OCR provider** (`ocr.engine`); OCR words come back with normalized bboxes and feed
     the *same* `row_reconstruct` logic (source_kind `ocr`). No OCR/LLM is needed for native
     inputs. The recommended **free** engine is **Docling** (`ocr.engine = "docling"`,
     `pip install -e ".[docling]"`) — pip-only, no system binary and no cloud, doing layout
     + OCR + table structure; `adapters/docling_ocr.py` maps its text items to word-level
     tokens with normalized top-left bboxes. For a **cloud** option, **Azure AI Document
     Intelligence** (`ocr.engine = "azure"`, `adapters/azure_doc_intelligence.py`) runs the
     `prebuilt-layout` model over REST (analyze + poll) and maps its word polygons to the
     same normalized-bbox contract; endpoint/model are config, the key is read from the env
     var named by `ocr.azure_api_key_env`. `paddleocr` is another alternative behind
     `.[ocr]`. Every engine is swappable via `ocr.engine` with no pipeline change; a
     `stub` engine that fails loudly is registered for offline use, and the shipped
     `config.toml` selects `docling`. **Tesseract is named in the config comment as an
     accepted value but has no adapter** — selecting it raises from the registry.
2. Mapping then decides *which canonical concept* each fact is, by meaning. In
   `per_statement` mode (`extraction.mapping_scope`, the default) the LLM sees the whole
   statement's captions **by `item_id`** plus the candidate concepts + policies, and
   returns `{item_id → canonical_key, allocation_status, confidence}`. It references ids
   and keys; the **numbers and their sheet/cell (or page/bbox) come from step 1**, so a
   value can always be traced back and verified. `per_line` maps each caption
   independently (less context, cheaper).

This is how accuracy (full-statement LLM context for containment/residual/"Others") and
hard value-level provenance coexist: the model does the semantics, the deterministic layer
owns the numbers and their location.

**In the UI.** An uploaded document's extraction is shown at **`/extraction`**
(`frontend/src/screens/ExtractionView.tsx`, registered as `SCREENS.extraction` in
`frontend/src/screens/config.ts`, step 4 of the stepper). The former per-document URL
`/documents/:id` still resolves: it adopts the id in the path as the active document and
then redirects to `/extraction` (`App.tsx::AdoptDocumentAndRedirect`). Each value's
provenance is a click-to-source chip, and the shared source panel
(`frontend/src/components/SourceViewer.tsx`) adapts to the source kind:
- **PDF** — clicking renders that page, server-rasterized to PNG via
  `GET /documents/{id}/pages/{n}/image` (PyMuPDF), and draws the value's normalized bbox as
  an overlay (percent-positioned, so it survives any display scale).
- **Excel** — clicking a `Sheet!Cell` chip fetches a window of surrounding cells via
  `GET /documents/{id}/cell-context?sheet=&cell=` (`services/excel_extract.cell_context`)
  and renders a mini spreadsheet grid with the exact origin cell highlighted — the
  spreadsheet analogue of the PDF overlay, so click-to-source is uniform across formats.

The run is mapped against the seeded reference configuration (`app/sample/reference.py`
holds the stored template + line-item set equal to the shipped files on every startup), so the
"mapped to" column populates — via the LLM when reachable, else the deterministic alias
tier offline.

**Row reconstruction robustness.** `services/row_reconstruct.py` groups positioned words
(native text layer *or* OCR) into visual rows, then folds a **wrapped label** — a label
that breaks across two tightly-spaced, left-aligned lines (e.g. "Property, plant and" /
"equipment 12,500") — back into one line item so the caption isn't truncated to its last
fragment. The merge is deliberately conservative (paragraph-tight vertical gap + label-
column alignment, with an ALL-CAPS/`:`-suffixed section-header guard) so headings are never
swallowed into the item below them. It also has a second path for a **matrix** statement
(changes in equity), whose columns are equity components rather than periods.

## The whole PDF to the model — an admin switch, on in the shipped config

`extraction.whole_pdf_to_model` (Settings screen; `config.toml [extraction]`). **On in the shipped
`config.toml`**, the value a fresh clone and the Settings screen's "restore defaults" get; the code's
own default (`app/config.py`) is off, so a Settings built without that file stays off. With the
shipped gateway and its key, every PDF run sends the file to the model; with no key, the `stub`
provider (the test suite, `scripts/run_filing.py --no-llm`) or a spreadsheet, the stage falls back
(below) and the rules read the filing exactly as with the switch off. **Off, nothing above
changes**: `core/pipeline.py::pipeline_for` assembles `default_pipeline()` and the stage
below is not in it. **On**, the run assembles `whole_pdf_pipeline()` — the same list with
`whole_pdf` straight after `extract` — and the filing is read differently:

1. `ingest · integrity · language_detect · classify · extract` run as always, so the page map, the
   Page Scope screen, the rules' face rows and the **note tables** are what they were. The stage
   comes after the extraction so the note tables exist on this route too (the model's rows replace
   the face rows, never the notes), so the rules' face rows can be kept beside the model's
   (`PipelineContext.whole_pdf_rules_rows`, set aside unmapped), and so a fallback reads nothing
   twice.
2. `stages/whole_pdf.py` sends the PDF **file itself** to the model, not text the app read out of
   it — **every page, and every line is asked** (`services/whole_pdf.py::pages_to_send`, `plan`),
   whatever the page classifier found and whatever is set on the Page Scope screen. **Page Scope
   affects only the rules route**: on this route it narrows what the rules read beside the model
   (`extract`), which checks the model's figures, fills the lines it leaves empty and is the
   fallback, and the run log says so (`whole_pdf:page_scope_not_applied`). It used to send the
   pages the classifier found to be statements and notes (`whole_pdf_pages = "statements"`, the
   default) or every page (`"all"`), either one narrowed by the page selection, and asked no line
   of a section switched off; with the classifier finding no statement (Vivo Energy Mauritius)
   the auditor's report was left out. The setting is gone, and a value saved for it is removed at
   startup (`settings_state.RETIRED_KNOBS`). The classifier's reading is still used beside the
   model, and cuts no page or line from what is sent: the faces carried in every part and asked
   alone (below), a face line read off another page sent to review (`merge`), and the rules'
   reading each figure is found again in. **One thing it still decides**: a face statement's
   `[read from the statement]` lines are asked only of the statements-alone part, which holds the
   pages the classifier calls that statement's face. Where it calls the wrong page the balance
   sheet, those lines are asked against that page, and the parts holding the real one are asked
   none of them (they are still sent it, and asked the statement's other lines). It is a known
   limit, kept for what the statements-alone part saves; asking the face lines in every part as
   well is a person's choice. A page the rules left unread (out of the selection, a section
   switched off, scanned with no OCR) went to the model, so once a part holding it got a reply it
   is no longer counted unread; a page whose every part failed, or one larger than a request,
   stays unread. A switched-off statement whose lines a request answered is checked on the
   model's figures (`ctx.whole_pdf["statements_read"]`, stored as the run's `mapping.whole_pdf`;
   `services/unread_pages.py::sections_left_unread`); one no request answered, and any run stored
   without that key, stays "not read". The pages go as one or more PDF
   parts cut from the original (`services/whole_pdf.py::build_parts`): at most
   `whole_pdf_pages_per_request` pages (600 as shipped, what Claude reads a request on a
   1,000,000-token window) and `whole_pdf_max_request_mb` a part, and no more than the model's
   window holds with a whole reply's room (`llm.context_window_tokens` less
   `llm.max_output_tokens`, 1,000,000 and 128,000 as shipped; each page put at its text and, an
   upper bound, 4,800 tokens for its picture, `page_tokens`; faces that leave no room for one more
   page are not carried, and a page the window cannot hold is not sent), in even parts, and
   **every part carries the statement faces**, so a note in the second part is read with the
   statements in view — the other pages are cut into smaller parts to make room, never dropped.
   **The statements' face lines are asked once**, in a last part that holds the statement faces
   and nothing else (`faces_only`, the "statements alone"): each statement's whole list of
   `[read from the statement]` lines in one request there. The parts holding the notes are asked
   every other line, the three statements' notes/anywhere lines together in one request
   (`STATEMENTS_REST`, `Plan.ask`). Where nothing would be left to ask the notes parts (a set
   whose lines are all face lines, such as Ind AS), no statements-alone part is made and every
   part is asked every line. A statements-alone request that fails, whole or in part, is asked
   again in the notes part holding most of its pages (`Plan.second_part`); one cut off at every
   halving is not. **The model is asked for every `extracted` line, whatever its `route`** — the face, the
   note tables, anywhere in the report or the narrative, as the rules route asks them
   (`line_item_requests.asked_about`) — the internal `sub__` parts a cascade adds up and the text
   lines (a phrase or prose) included, and for the total the filing prints of every `calculated`
   line (`services/whole_pdf.py::lines_to_ask`). Each line in the question says where to read it
   (`[read from the notes]`) and whether it is a TOTAL, a TEXT, or a PART of the line it feeds.
   Not asked: a `derived` line, whose figure is its cascade's (step 4); an `intermediate`
   working figure; a line the app computes or a person fills (`extraction_mode: derive` —
   the HK set's analyst, status, target currency and control totals); and a part nothing reads
   (no cascade, formula or parent — the HK set's `bs_ca_residual_L3`, which has no definition).
   That is 518 lines on the HK set (485 extracted, of them 79 internal parts and 11 text lines,
   and 33 printed totals), 157 on US GAAP (13 text lines) and 111 on Ind AS. At most
   `whole_pdf_lines_per_request` at a time; a statement's own lines are never asked with another
   statement's, but **the parts one cascade adds up are asked in one request**
   (`services/whole_pdf.py::families` — Securities (CP)'s Find 1 and Level 3 parts with the LTP
   parts its residual reaches), and the question lists them under "Added together": one printed
   holding goes into at most one of them. Each line is sent with its **whole definition** (it was
   cut at 240 characters), and where its `exclude_hints` name captions the definition does not, a
   "Not this line: …" sentence rendered from them (`definition_of`, `hint_caption`). A sentence
   several lines of one request share is sent once, as a numbered rule the lines point to
   (`shared_sentences`); "answer with an empty sources list" is said as "leave it out", and a
   sentence that only repeats the question's own leave-it-out rule is not sent (`for_this_route`,
   `row_as_read`). A checker over the five reference filings finds none of the 4,173 deciding
   clauses lost (`tests/test_the_whole_pdf_question_keeps_every_deciding_clause.py`). The model answers a figure line with its figures —
   basis, period, the amount as printed, the page of the file it read it on, the caption, and
   the printed rows of a figure that is a sum of rows — and a text line with its words and the
   pages they are on.
3. The file is sent as the provider's own file block (`ports/llm.py::LlmMessage.documents`): a
   base64 `document` block on the Anthropic body (`anthropic`, `bedrock_gateway`), ending in a
   cache breakpoint so every request after the first reads it from the prompt cache; a `file`
   part on Chat Completions (`openai`, `openai_compatible`, `azure_openai`). Such a request waits
   `llm.document_timeout_seconds` (600 as shipped; `llm.retries` applies too, so a stalled request
   holds its lines for up to twice that). **The smallest request goes first**, alone: it fills
   the cache, and it answers quickest whether the gateway is up and takes the file. Then the rest
   of that part, the largest first, `llm_parallel_requests` at a time, and each later part the
   same way (`services/whole_pdf.py::Plan.order`). A request that fails is asked once more; a
   reply cut off at its budget is asked again with twice the room where that is within
   `ports/llm.py::ROOM_CEILING` (16,000: what the 600-second wait delivers at about 27 tokens a
   second), and otherwise as two halves, at most three times halved
   (`stages/whole_pdf.py::_request`). A request's first budget is 220 tokens a line and 1,024 more,
   up to the model's reply limit (`llm.max_output_tokens`, 128,000 as shipped): the HK balance
   sheet's 208 lines are budgeted 46,784 where they were capped at 32,000. The real bound on a long
   reply is the wait, `llm.document_timeout_seconds` (600 as shipped, at most 900). On a window
   under 1,000,000 tokens a part holds at most 100 pages, what Claude reads a request there.
4. The stage writes one row per line and basis, with `printed_in` from the page's kind and the
   statement it is on stamped on it (`printed_on:<statement>`, read before the page by
   `buckets.statement_resolver`: a face line's statement; none for a part read in the notes or a
   note's, covenant's or setup line, which the rules route fills after `normalize`), and names the
   stages still to run
   (`PipelineContext.only_stages`, `stages/whole_pdf.py::KEPT_STAGES`): `normalize` (the
   statements' units, the template's sign rules); `link_notes` (links a face row to the notes it
   cites — the model's rows cite none yet, so it links nothing); **`note_sourced`, the configured
   cascades** — each `derived` line (Sales (Revenues), the depreciation lines, the related-party
   and financial-asset lines) is computed from the parts the model read by the same rungs as on
   the rules route, nested parts first, so the HK set's revenue is the revenue note's
   principal-business revenue where the note prints it and the face's otherwise — and, with the
   note tables, it **fills a part the model left empty from the rules' own reading** (a Level 3
   column printed blank reads 0; the parts of a request that failed are read as on the rules
   route), while a slot the model answered, on any of its rows, keeps the model's figure, and a
   note figure the model placed on another line is not read again — that very cell, where the
   model's figure can be put on one, so a row printing one amount in two columns (gross and its
   full allowance) keeps its other column (`services/whole_pdf_rules.py`);
   `contingent_liabilities` (the classified narrative, from the notes); `shared_figures` (one
   printed figure the model put on two lines); `reconcile` (the notes' own arithmetic, and a
   linked face row's tie); `prune_notes` (with no note reference on the model's rows nothing is
   pruned, so the Notes screen shows every note read); `confidence`; `structural` (the template's
   checks, comparing a printed total with its printed parts, both read by the model);
   `disclosure_reading`; and `segment`. Skipped, and logged `stage:<name>:skipped`: the stages
   that map the rules' face captions (`map_line_items`, `residual`), the rules route's own model
   call (`line_item_llm`), the stages that place rows left unplaced (`assemble_components`,
   `gap_closing`, `row_suggestions`), `face_mapping_contract`, and `note_tag_gate`, which would
   zero a model figure for printing no note reference. A text
   line is checked as the rules route checks one, by the same functions (`text_lines.locate`,
   `text_lines.check_prose`): a phrase is written only where the PDF's own text prints it — on the
   page it cites, or on exactly one other page of the part — and a narrative only when every
   number in it is printed on a page it cites; otherwise it is refused and logged
   `whole_pdf:<key>: text REFUSED` or `narrative REFUSED`, and `mapping.whole_pdf.texts_refused`
   counts it.

**Each figure is cited like a rules-route figure.** `services/whole_pdf.py::locate` finds it in
the page's own words — the printed word equal to it, on the row whose caption is most like the one
the model gave — and its provenance carries that box (`bbox`, `value_bbox`) and the caption's
(`label_bbox`), in the page space `pdf_extract` uses, so the grid links it and the viewer
highlights it. A nil is placed by its caption. Each figure also records a trail in the shape a
model's citation records on the rules route (`method: whole_pdf:<check>`, one input per printed
row, each with its own figure and box), so the Workspace's inspector says "read from the face" or
"read from the notes" and lists the rows a sum was added from. A figure the text layer cannot
place (a scanned page, a figure not printed as one word) keeps its page and is flagged
`whole_pdf:not_placed_on_page`.

**Signs, as the rules route sets them.** A figure found on a cell the rules read ends with the
sign the rules give THAT cell on THAT line (`stages/whole_pdf.py::_RulesSigns`): a copy of the
rules' rows, each row the figure was found on filed on the model's line, goes through `normalize`
itself — the printed sign, "Less:"/"Add:", the line's flip and contra rules, a 其中 row under a row
printed as an unsigned expense, the CAS outflow blocks (现金流入小计 … 现金流出小计) — and the sign it
comes out with is the figure's, settled. A cell in a note, for a statement's own line, is turned to
the face's convention as the rules' note-to-face split turns it (1966's lease interest, printed
1,490 in note 7, is -1,490 because the face prints finance costs (1,492,343)). In a sum each row
takes its own cell's sign, except a row the model gave against its printed sign where the rules
leave that sign alone: that is the model taking the row out, and it stands. A figure found on no
cell keeps the route's own rule: a cash-flow figure keeps the model's sign, and a row captioned as a
payment (支付 the verb, never 股份支付, a share-based payment; 购建, 偿还, 分配股利) is negative; any
other is oriented by `normalize` as a face row is. This replaced a caption regex that negated
688008's indirect-method 股份支付 add-back, charged 300319's parent 其中：利息费用 credit and wrote
1966's note-7 lease interest positive. A part read in the notes, and a note's, covenant's or setup
line, are written as read: no statement's rules or checks apply to them, as on the rules route,
where `note_sourced` writes them after `normalize`. The payment rule also counts 股份支付 or 股权支付 followed
by (的)现金 as a payment (回购股份支付的现金, a share buy-back), so only the noun is an add-back.

**A line the model leaves empty takes the rules' figure** (`services/whole_pdf_rules.py::
fill_from_rules`, at the end of the stage). The rules' face rows set aside on the context are put,
on a quiet copy of the context, through the rules' own mapping, residual and `normalize`, so each
figure is the one the rules route publishes, with its sign settled. Every slot (line, basis,
period) of a line the run asked that no model row holds is filled from it, on a row of its own
flagged `whole_pdf:rules_reading`, in the statement's scale and the model's unit for that basis.
Never filled: a slot a model row holds (even where the rules read another figure), a printed cell
the model already put on any line (inside a sum or a whole; by cell, else by page and amount),
and a part withheld because a sibling holds its note money. A slot whose model figure the checks
refused is filled only if a fresh copy of the same checks passes the rules' own cell. Figures with
no column heading take the heading the rules read for their year on their page
(`whole_pdf:period_display_from_rules`), which is what gives Periods and Statement Date. The log
line is `whole_pdf:rules_fill`, the counts are in `ctx.whole_pdf["filled_from_rules"]`. Note-row
lines (a "Yes" a note row says) are not filled: this route leaves notes unmapped. Measured with
scripted answers (no model): 佳明's blanks went from 115 to 1 and China SCE's from 56 to 0, and
every filled slot equals rules-only; the structural checks on both now match rules-only.

**A filled face figure is not counted twice beside the note pieces the model gave for it.** The
model can answer a face figure as its note pieces: 000709's CPLTD Bank 9,936,568,335.00 (note 七、26)
with 一年内到期的非流动负债 18,040,355,658.70 left empty, or 688008's allowance 1,944,451.99 with the
gross left empty, where the CAS rules publish the face NET on the gross. Filled whole, the face
counted that money twice. So:
- `whole_pdf_rules.settle_against_model_pieces`, the first thing `note_sourced` does (signs are
  final there, and nothing has read the filled rows yet), takes off a filled face row the model's
  pieces of it. A piece is a model figure on another template line, in the same slot, read from the
  face row's own note: the note it cites, or, on a face that cites none (000709, 300319), a note whose
  tables print the face figure. Its cell must lie in a block of that note that adds up to the face
  figure, so a table that prints every face row once (300319's 流动性风险 table) gives no piece.
  The amount comes from the template's own arithmetic for the line's section total, so pieces count
  with the sign the section gives them (gross = net + |allowance|). A settled slot keeps the printed
  figure as `value_raw`, is flagged `whole_pdf:rules_reading_less_model:<line>=<amount>` and goes to
  review. Where the pieces reach the whole figure, the slot is left empty and the pieces are flagged
  `whole_pdf:pieces_reach_the_rules_reading`. A line whose own cascade already subtracts its part
  (Trade and other payables) is left alone. Sub__ parts and lines `carve_note_figures` will carve
  are never pieces.
- A piece the rules split out of a face cell (`split_from:<X>`, 1966's lease interest out of finance
  costs) is not filled for a slot where the model holds X, because the model's X already holds it.
  Where X is empty and the model gave the piece on its carved line, X takes the remainder plus that
  piece, which is the face cell whole (`whole_pdf:rules_split_filled_whole`).
- `whole_pdf_rules.review_beside_a_difference`, called from `structural`, flags for review each
  filled figure in a section whose reconciliation shows any difference, even one that passes.
  Flags only; no check status moves.

None of this runs on the rules route, where no row carries `whole_pdf:rules_reading`. Measured with
scripted answers on the five reference filings, every case that came out wrong now gives the printed
figure. bs_cl now passes with 0 on 000709, 300319 and China SCE, and so does bs_ca on 000709 and
688008. 1966's finance costs are now the face cell (1,492,343) / (921,124), p102. Rules-only did not
move. The review goes on the settled figure's own slot (its line, basis and period), so a year whose
section adds up stays out of review.

**Measured** on 688411 (the filing of run 11033486) with a stand-in that answers every question
from the rules route's own figures, as the prompt asks: the template's checks came out as the rules
route's, 58 passing of 93 and none failing, on the statement pages (2 parts, 14 requests) and on
every page (4 parts, 28 requests). Before these two rules the checks passed too, and the figures
did not: 购买商品、接受劳务支付的现金 came out positive and four note parts lost their lines, so
Securities & Other Financial Assets (CP) was empty — no check covers either. Lines whose parts
the rules route adds up from note cells (no single printed figure) stay empty for a stand-in that
can only cite a printed figure; a real model has to add those rows itself.

**This route is the one exception to "the LLM references facts, it never emits values"** (next
section): here the model writes the figure. So every figure is checked against the PDF's own text
(`services/whole_pdf.py::check_figure`): found on the page it cites (`whole_pdf:on_page`); a sum
whose rows are all on the page and add up (`parts_on_page`); on exactly one other page of the part
and moved there, the common case being a model citing the printed folio (`moved`); a nil
(`nil`); a page with no text layer (`no_text`); or not found (`not_found`). `no_text` and
`not_found`, and a line given two different figures for one slot by two parts, raise
`low_mapping_confidence` on that figure's slot and send that one figure to review; a `not_found`
figure's confidence is 0.3, below the auto-accept bar. The line's other figures stay out of review
unless they are in doubt themselves (see "Review is per figure" below). Between two parts' readings (`services/whole_pdf.py::merge`) a confirmed one
wins, then the page the line is read from — a [read from the notes] line prefers a note page, a
statement's line a statement page; where nothing chooses, part 1's figure is kept and the slot is
flagged `whole_pdf:parts_disagree:<kept>@p<n>(part i):<other>@p<m>(part j)`. A [read from the
statement] line kept off a page that is not a statement's is flagged
`whole_pdf:face_line_not_on_a_statement_page`. Both go to review. The run result's
`mapping.whole_pdf` counts each outcome, and names every request that failed
(`requests_failed`) and every line no request answered (`lines_lost`).

**Review is per figure, not per line** (`services/review_state.py`). A slot is one figure: a line,
a basis and a period. A doubt about a figure (not found on its page, two parts disagree, a fill
settled against the model's pieces, a section that does not add up for that column, a residual gap
or plug in that column) is written on that slot's flags. A flag on the ROW means a doubt about the
caption, which covers every slot of the line. `review_state` is the one place that decides whether
a slot is in review and why: the cascades ask it by flags, and the queue (`weak_mappings` counts
lines, `weak_slots` figures), the run and extraction payloads (`review_slots`, `review_row`,
`in_review`, `review_reasons`, worked out per read and never stored), the statement grid
(`review1`/`review2`, `review_why`, `confidence1`/`confidence2`) and the JSON and xlsx exports ask
it by flags and scores. A whole-PDF row's score is its best figure's; a display that shows one
number per line uses `shown_score`, the weakest figure's own score. A fill row's inherited rules
gap is checked against the output's own section check for that column: passing at 0.00 leaves only
the breadcrumb `whole_pdf:rules_gap_answered_by_the_output`, and a missing or skipped check sends
the slot to review (`whole_pdf:rules_gap_unchecked`). An analyst's save takes a slot out of review
only when it changes the figure; a revert brings the review back.

**It never leaves a run with nothing.** No line-item set, a file that is not a PDF, the `stub`
provider, a provider that cannot be sent a file, a first request that fails after its retry (the
file refused, or the provider not answering), no request answered, or a statement whose own lines
no request answered in a part holding its face pages: the stage logs
`whole_pdf:fallback(<why>)`, leaves the document as the extraction left it, and every later stage
runs — the rules read the filing, with nothing read twice.

**Two-page spreads and filings with no face page found** (Mauritius annual reports, where each PDF
page is two printed pages):
- A spread sheet's statements are read by its halves (`whole_pdf.statement_values`). A sheet
  printing the P&L on its left half and the balance sheet on its right counts as a face page of
  both, for the statements-alone part, the merge and the lost-lines guard.
- The question names the printed pages each file page holds (`_spread_note`). A figure cited by
  its printed folio is mapped back to the one page of the part that prints it
  (`page_of_folio`), and the citation names the half the figure sits on.
- A statement with no face page anywhere is judged by its own requests. If no request that asked
  its lines succeeded, the route falls back as above. It used to be skipped, so a failed
  balance-sheet request blanked every balance-sheet line and the run reported success.
- A figure found on no page carries no provenance. It is no longer cited on page 0.
- On a single-entity filing, a figure the model filed as standalone is refiled on the one basis
  the filing prints (`whole_pdf:refiled_basis:standalone->consolidated`).

**What it costs**, measured as the request shape on ten corpus filings with the defaults (no model
called): statement and notes pages are 28 to 180 of 97 to 383 pages; one or two parts of 0.3 to
16 MB. At the default 250 lines a request, a part takes one request per statement: 4 on the US
GAAP set (157 lines), 3 on Ind AS (111), 7 on the HK set (517 lines; the balance sheet's 213 the
largest), and a filing sent in two parts twice that. A page costs its text and its picture —
roughly 1,500 to 4,800 tokens — so
the largest request is an estimated 75,000 to 520,000 input tokens: it needs a model with a 1M
window at 100 pages a part, or fewer pages a part on a smaller one. The requests after the first
of each part read the file at the cached rate. The run log prints each request's input, cached and
output tokens. Measured again with the whole definitions, the auditor's report and the faces in
every part, on the five reference filings (688008, 000709, 300319, 1966, kaming; no model
called): 94 to 176 pages sent (3 to 10 of them the auditor's report), one or two parts of at most
94 pages and 2.7 MB, 7 or 14 requests as before; the largest question, the balance sheet's, is
201,147 characters (about 52,000 estimated tokens) where it was 70,043, and the largest request an
estimated 195,000 to 282,000 input tokens (the app's text estimate plus 1,600 a page for its
picture) where it was 140,000 to 228,000. With the face lines asked in the statements alone
and shared sentences sent once: 8 or 13 requests (18 on a filing sent in four parts); the
largest request an estimated 152,278 (kaming) to 239,539 (1966) input tokens, 15-22% under the
figures above; the whole run 1.33 to 2.43 million, under both earlier plans on all five; the
question text sent fresh on every request 131,566 tokens where it was 232,720. A filing that fit
in one part (300319) is now sent in two, so it writes one more document to the cache: its
weighted cost rises 2.3%. At 600 pages a part (the defaults since a 1,000,000-token window was
made the shipped limit), the notes go in ONE part on 688008, 000709 and kaming, so a run is 8
requests where it was 13: the whole run 6.7-12.8% under 100 pages a part, its weighted cost after
caching 7.8-12.7% under, and the largest request 1.6 to 1.8 times larger (249,618 to 309,985
estimated tokens with 1,600 a picture). 300319 fit in one part already and is unchanged; 1966's
176 pages are put at 978,203 tokens at the window check's 4,800 a picture, before its question,
over the 872,000 the window leaves beside a reply, so it stays in two parts, 13 requests, as
before.

**Every page, every line (the route now; measured with the shipped stand-in recording the
requests, no model called, the HK set, 600 pages a part on the 1,000,000-token window).** Before,
with the shipped "statements": 94 to 176 pages sent of 162 to 270, 8 requests (13 on 1966), the
largest request an estimated 227,276 to 309,742 input tokens at 1,600 a picture, the whole run 1.33
to 2.42 million; and a page selection with a section switched off cut it to 5 to 16 pages and 436
of the 518 lines. Now every filing sends every page — 256 (688008), 208 (000709), 188 (300319), 270
(1966), 162 (kaming) — in two parts holding the notes, each carrying the statement faces, and the
statements alone (7 to 22 pages): 13 requests, 518 lines, whatever Page Scope says. The two notes
parts are what the window check makes of 162 to 270 pages at 4,800 a picture. The largest request
is an estimated 223,861 (kaming) to 352,146 (1966) tokens at 1,600 a picture (492,661 to 800,146 at
4,800, inside the 872,000 the window leaves beside a reply), the whole run 2.24 to 3.56 million:
about 1.5 to 2.3 times the "statements" run, the price of not letting the classifier choose. A
filing whose pages the classifier called nothing (every page "other") was already sent whole and
is unchanged: two parts, no faces carried, 14 requests. One on which it found notes pages but no
statement face — the Vivo Energy Mauritius shape, 54 of 58 sheets sent — sent only those pages
and no auditor's report, as no statement page told the finder where to look; now it sends every
page. **On a smaller window the requests grow about 1.5 to 2.5 times** (the same five filings, the
window check's 4,800 a picture, 100 pages a part on a 200,000-token window, which leaves 136,000
beside a reply): the window check cuts the parts much smaller, and as every page goes there
are more of them — 688008 86 requests before -> 217 now, 000709 86 -> 132, 300319 86 -> 166,
1966 166 -> 246, kaming 193 -> 323. Every request still fits: the largest is an estimated 78,260
to 100,273 tokens and at most 2.4 MB; the whole run is 6.5 to 18.2 million estimated tokens
(kaming the most).

## Progress reporting

There is **no WebSocket and no push channel**. `POST /documents/{id}/extractions` returns
`202` with a `progress_url` naming `GET /api/v1/extractions/{run_id}`, and the client polls
that once a second while the run is `running` (`frontend/src/lib/queries.ts::useExtraction`).

The poll is worth polling: `api/routes/extractions.py::_RunProgress` writes a full
`ExtractionProgress` record onto the run row at every stage transition — phase, pct, the
stage in flight, `stage_index` / `stage_count`, `stages_done`, `started_at`, `elapsed_ms` —
plus a bounded tail of the pipeline log, each in its own short-lived session so a
half-built result is never published. `GET /extractions/{run_id}` serves that record
alongside **the stage list the run was queued with** and the log tail, which is what lets
the Extraction screen tick stages off. A terminal record (`done` / `failed`) is written in
the same commit as the status and the result; a failure **names the stage that was in
flight**. Runs written before this contract existed return `progress: null` rather than a
half-record.

## Adapter ports

`ports/`: `OcrProvider`, `TableStructureProvider`, `LlmProvider` (structured JSON,
temp 0, Pydantic-validated), `ObjectStore` (local impl provided),
`FxConverter`. Concrete impls register into `Registry` keyed by id; config selects them.
Stub engines are registered for every kind and raise a clear message, so a mis-wired
pipeline fails loudly rather than silently.

Which ports are actually *bound* differs, and the difference matters when reading the rest
of these documents:

| Port | Adapters registered | Consumed by the pipeline |
|---|---|---|
| `LlmProvider` | `azure_openai` (default), `anthropic`, `openai` / `openai_compatible`, `stub` | yes — mapping, gap closing, netting, credit narrative |
| `OcrProvider` | `docling`, `azure` (Document Intelligence), `paddleocr`, `stub` | yes — scanned pages and images |
| `ObjectStore` | `local` | yes — uploaded bytes |
| `TableStructureProvider` | `stub` only | **no** — nothing calls it; `row_reconstruct` does the job |
| `FxConverter` | none — the port is left unbound | **no** — currency conversion is not a rate *feed*. It runs off an admin-maintained rate master (`app/db/models.py::FxRate`, `app/services/fx.py`, `GET/POST/PUT/DELETE /fx-rates`), which resolves a pair `direct` or `inverse`-and-flagged and **refuses** rather than triangulating. |
