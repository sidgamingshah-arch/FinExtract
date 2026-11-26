"""Runtime-mutable settings overlay.

Most settings come from ``config.toml`` / env and change only on redeploy. A few are
meant to be flipped live by an admin from the Settings screen:

  * ``ui_localization``  — localize the whole interface.
  * ``review_required``  — require a reviewer step (else the workflow closes at analyst).
  * NOT the LLM. It is defined only in ``config.toml``'s ``[llm]`` table (see ``app.config``).
    It used to be editable here and persisted, and a saved value outranked the file and the
    environment alike — so the file could name one gateway while every call went to another.
    Rows an older release stored under the ``llm`` scope are deleted at startup, and logged.
  * the EXTRACTION thresholds — the mapping ensemble's accept/candidate/margin bars and the
    reconciliation tolerances (see ``EXTRACTION_KNOBS``). Applied onto the process-wide
    ``Settings.extraction``, which the pipeline reads per run, so a change takes effect on the
    next extraction and never rewrites one that already happened.

Every change is PERSISTED to ``setting_overrides`` and re-applied at startup, so an admin's
edit survives a restart and is shared by every process against the same database. The
config file remains the source of the DEFAULTS — "restore defaults" means what config.toml
shipped, never the last value that happened to be stored.

The in-memory copies are a read-through cache of that table: reads never touch the database,
writes go to both. Two processes changing DIFFERENT settings do not interfere (one row per
setting); two changing the SAME one are last-write-wins, and the losing process keeps its own
value until it restarts or is told again — acceptable for an admin screen, and the reason the
table stores one row per setting rather than a single blob.

**No secrets are persisted.**
"""
from __future__ import annotations

from copy import deepcopy
from typing import NamedTuple

from app.config import get_settings
from app.ports.registry import registry

# Where an override is applied when it is loaded back. One namespace per settings object, so a
# short name like "model" cannot be confused between them.
SCOPE_FEATURES = "features"
SCOPE_LLM = "llm"
SCOPE_EXTRACTION = "extraction"

_RUNTIME: dict = {}
# …and of the extraction defaults, so "restore defaults" means the shipped config values
# rather than whatever the last edit happened to be.
_EXTRACTION_DEFAULTS: dict | None = None
# Whether the persisted overrides have been read into this process yet.
_LOADED = False

class Knob(NamedTuple):
    """One tunable extraction setting, described well enough for a UI to render and validate it
    without knowing anything about mapping.

    The bounds and the guidance live HERE rather than in the frontend, because they are facts
    about the pipeline: a threshold outside its range does not mean anything, and the note on
    each is what stops a well-meaning edit from quietly making mapping worse.
    """

    key: str
    kind: str            # "number" | "bool" | "choice"
    label: str
    help: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    #: For a choice: each value with a short name and one line on what picking it does, so the
    #: screen offers "Only the notes each line chose" rather than a bare "selected". Every choice
    #: has one or none does (tests/test_settings_extraction.py).
    choice_info: tuple[tuple[str, str, str], ...] = ()


# Every extraction setting an admin may change from the Settings screen. Changing one affects
# FUTURE extractions; it never rewrites a run that already happened.
EXTRACTION_KNOBS: tuple[Knob, ...] = (
    Knob("llm_mapping", "bool", "Ask the model about line items",
         "Which CONCEPT a printed caption is, is always decided deterministically — by exact "
         "alias and the rule tier's authored hints — and this switch does not change that. What "
         "it controls is whether the configured provider is asked about LINE ITEMS and the notes "
         "they name: which printed row inside a claimed note holds the figure. Off, or with no "
         "provider, the note-sourced tiers decide that too."),
    Knob("llm_focus_only", "bool", "Ask the model only about focus line items",
         "Off (as shipped): the model is asked about every line item. On: a run's requests are "
         "restricted to the lines on the Focus line items master (its own tab), and every other line keeps "
         "what the deterministic tiers gave it — a way to spend a small provider budget on the "
         "lines under review instead of on the whole filing. With the master empty this does "
         "nothing and says so in the run log. The whole-PDF route asks every extracted line either "
         "way."),
    Knob("llm_request_grouping", "choice", "How line items share a request",
         "Each request carries the NOTES its line items asked for, and that note block is almost "
         "all of what a request costs — measured at 95,188 of a ~196,000-character request, going "
         "out byte-identical in every call of a run. Grouping lets line items needing the same "
         "notes pay for one copy. \"none\" is one request per line item: the baseline, and the "
         "only mode whose answer is attributable to a single line, because nothing else shared "
         "the call. \"identical\" groups lines whose selected note sets match exactly — safe by "
         "construction, since no line receives a note it did not ask for. \"similar\" groups "
         "lines whose note sets overlap by at least the threshold below, which saves more and can "
         "bleed: lines sharing a call can influence each other's answers. \"manual\" uses the "
         "groups authored on the line-item set and asks about everything else one line at a time. "
         "Shipped: \"identical\" — lines needing the same notes, or the same section of a "
         "statement, share one request, at most the number below: 50-63 line-item requests a "
         "filing where \"none\" made 421. Choose \"none\" for the per-line baseline.",
         choices=("none", "identical", "similar", "manual"),
         choice_info=(
             ("none", "One line item per request",
              "The per-line baseline: about 420 requests a filing, and each answer belongs to one "
              "line."),
             ("identical", "Lines that need the same notes share a request",
              "Safe: no line is sent a note it did not ask for. About 50 to 75 requests a filing."),
             ("similar", "Lines whose notes overlap share a request",
              "Fewer requests, but a line may see notes it did not ask for, and lines sharing a "
              "request can sway each other's answers."),
             ("manual", "The groups written on the line-item set",
              "Every line in no group is asked one at a time."),
         )),
    Knob("llm_lines_per_request", "number", "Line items in one request",
         "Read when grouping is \"identical\" or \"similar\": the most line items one request asks "
         "about. A larger group is asked in even parts, each sent the group's notes and the "
         "statement rows its own lines may read. The lines asked with the whole annual report go "
         "in batches of at most 12, and a value below 12 cuts those too. "
         "The reply has to arrive inside one wait (two minutes), and a line's answer is about 125 "
         "tokens at the median, so 20 is a reply of about 2,500. Lower it if requests time out; a "
         "request that fails loses all its lines to the rules.",
         minimum=1, maximum=100, step=1),
    Knob("llm_document_context", "choice", "What the model reads with each question",
         "\"selected\" sends each request only the notes and statements its line items selected: "
         "the smallest requests, and no line is sent the whole annual report — a line that may be "
         "read from anywhere is given the pages that print its name. \"anywhere\" (the default) "
         "does the same for every other line, and asks the lines that may be read from anywhere "
         "with the WHOLE annual report — every page, note and statement — in batches of their "
         "own. \"full\" sends the whole annual report with every question, so the model always "
         "has the full filing in view; it costs the most. The report goes first and is identical "
         "on every request that carries it, so a provider that caches prompts charges the cached "
         "rate for it after the first; the run log shows input, cached and output tokens per "
         "request and a summary at the end.",
         choices=("selected", "anywhere", "full"),
         choice_info=(
             ("selected", "Only the notes each line chose",
              "The smallest requests. A line that may be read from anywhere is given the pages "
              "that print its name, not the whole report."),
             ("anywhere", "The whole report for lines read from anywhere",
              "Each line is sent the notes it chose, and the lines that may be read from anywhere "
              "are asked with the whole annual report, in batches of their own."),
             ("full", "The whole report with every question",
              "The model always has the whole filing in view. Costs the most: the report, often "
              "over 100,000 tokens, goes with every request, at the cached rate after the first "
              "where the provider caches prompts."),
         )),
    Knob("ocr_scanned_pages", "bool", "Read scanned pages (OCR)",
         "A page with a text layer is always read from that layer, exactly as printed — OCR would "
         "only re-guess characters the file already holds — so this changes nothing for a native "
         "PDF. It decides what happens to a page with NO text layer (a scan, a photographed "
         "page): on, the page is rendered and read by the configured OCR engine — Docling, which "
         "runs entirely on this server from its pre-installed models and sends nothing anywhere; "
         "off, the page is skipped and the run log says so. Reading a scanned page is much slower "
         "than reading text, and OCR can misread a digit: each figure's source records that it "
         "was read by OCR, so check a scanned filing's figures against the page."),
    Knob("document_reader", "choice", "Document extraction engine",
         "\"off\" reads every page as before: from the PDF's own text, or with the OCR engine for "
         "a page that has none. \"kensho\" sends the filing to Kensho Extract once and reads the "
         "pages chosen below from its answer — Kensho's text and table cells, placed where it saw "
         "them, then read into rows and columns exactly as a native page is. This SENDS THE PDF "
         "OUTSIDE this server, to the address set in config.toml [document_engine]; the status "
         "below says whether that address and a token are configured. A page Kensho does not "
         "return, or a run where it fails, is read as usual and the run log says so.",
         choices=("off", "kensho"),
         choice_info=(
             ("off", "Off: read the PDF's own text",
              "A page with no text layer goes to the OCR engine above."),
             ("kensho", "Kensho Extract",
              "Sends the PDF outside this server, to the address in config.toml "
              "[document_engine]."),
         )),
    Knob("document_reader_pages", "choice", "Pages the document engine reads",
         "\"scanned\" uses the engine only for pages with no text layer, in place of OCR — a "
         "native page is still read from its own text, exactly as printed. \"all\" reads every "
         "page from the engine's answer instead of the PDF's text layer: for filings whose text "
         "layer is broken or scrambled. Only read when an engine is chosen above.",
         choices=("scanned", "all"),
         choice_info=(
             ("scanned", "Only pages with no text layer",
              "In place of OCR. A native page is still read from its own text."),
             ("all", "Every page",
              "In place of the PDF's text layer, for a filing whose text layer is broken."),
         )),
    Knob("whole_pdf_to_model", "bool", "Send the whole PDF to the model",
         "On in the shipped config.toml. Off, a filing is read as always: rows are rebuilt from "
         "each page and placed on the template's lines by the rules, and the model is only asked "
         "where a line's figure is printed. On, the PDF FILE ITSELF is sent to the model with "
         "the template's lines — the model sees each page as printed, tables and columns intact "
         "— and returns each line's figures with the page it read them on. Every page is sent "
         "and every line is asked, whatever the page classifier found and whatever is set on the "
         "Page Scope screen: Page Scope then changes only what the rules read. The rules still read "
         "the filing alongside it: they read the note tables, check each model figure against "
         "the page it cites and against their own reading, and fill the lines the model leaves "
         "empty; the note screens and the contingent-liabilities narrative are filled as on the "
         "rules route. Units, signs and the template's checks run on the figures exactly as on "
         "the rules'. It costs more: each request carries the pages (text and picture, roughly "
         "1,500 to 4,800 tokens a page), cached after the first request of the run. Needs a "
         "provider that takes files (anthropic, bedrock_gateway, openai, azure_openai); if the "
         "provider cannot be sent the file, or a statement's request fails, the whole filing is "
         "read by the rules route — with the model's line-item questions if \"Ask the model "
         "about line items\" is on — and the run log says why."),
    Knob("whole_pdf_pages_per_request", "number", "Pages in one request (whole PDF)",
         "The most pages one request carries. A longer file is sent in consecutive parts, "
         "each asked every line, and a figure read in two parts keeps the one from a statement "
         "page. Claude reads at most 600 pages a request, and 100 on a model whose context "
         "window is under a million tokens.",
         minimum=1, maximum=600, step=1),
    Knob("whole_pdf_max_request_mb", "number", "Largest request in MB (whole PDF)",
         "The largest request the provider accepts, counting the PDF in base64 (a third larger "
         "than the file). A part over it is split in two until it fits; a single page over it is "
         "not sent and the log names it. Claude's own API takes 32 MB; Amazon Bedrock less.",
         minimum=1, maximum=128, step=1),
    Knob("whole_pdf_lines_per_request", "number", "Template lines per request (whole PDF)",
         "How many template lines one request asks about, at most; a request never mixes two "
         "statements. The default sends a statement's whole line list in one request, which is "
         "what lets the model put each printed row on exactly one line and the rows no line "
         "describes into the section's \"other\" line. Lower it only if replies are cut short; "
         "every request sends the same file, read from the provider's cache after the first.",
         minimum=5, maximum=300, step=1),
    Knob("llm_parallel_requests", "number", "Requests sent at once",
         "How many requests are in flight together: the line-item figure requests, the whole-PDF "
         "requests and the disclosure readings (the few text-line requests go one at a time). For "
         "the first two, the first request of each prompt prefix is sent on its own — it fills "
         "the provider's prompt cache — and the rest follow this many at a time; the disclosure "
         "readings share no prefix and start this many at once. Replies are processed in the same order whatever this is, so it "
         "changes how long a run takes and not what it finds. Lower it if the provider starts "
         "refusing requests for rate limits; 1 sends one request at a time.",
         minimum=1, maximum=16, step=1),
    Knob("llm_group_similarity", "number", "How much note overlap counts as similar",
         "Only read when grouping is \"similar\". The fraction of two line items' selected notes "
         "that must be the same (Jaccard overlap) before they share a request. 1.0 is the same "
         "thing as \"identical\"; lower it to group more aggressively and pay for fewer copies "
         "of the note block, at the cost of lines receiving notes they did not ask for.",
         minimum=0.0, maximum=1.0, step=0.05),
    Knob("llm_note_synonyms", "bool", "Search note headings with the shared synonym list",
         "A line's notes are found by the words their headings share with its note terms, so a "
         "note headed \"Fixed assets\" is not found for a line that says \"Property, plant and "
         "equipment\". On, a note whose heading prints another spelling of the line's own concept "
         "— English, Simplified or Traditional Chinese, from one list in the code — is added to "
         "the line's request, at most two, after every note the configuration found. It changes "
         "which notes the model is shown and nothing else."),
    Knob("llm_note_router", "bool", "Ask the model which notes print each line (one call)",
         "One extra model call per filing, before the line-item requests: the model reads every "
         "note heading and each asked-about line's definition and note terms, and names the notes "
         "that print the line's figure. Up to two of its notes are ADDED to each line's request, "
         "never in place of one the configuration found, and never a policy note or another "
         "entity's. If the call fails the run carries on without it. Off, no call is made."),
    Knob("llm_row_suggestions", "bool", "Suggest a line for each unmapped row (one call)",
         "One extra model call per filing, after the checks: every printed row that reached no line "
         "of the template is shown with its statement, the heading above it, its figures and the "
         "rows beside it, and the model names the template line it belongs on, or none. The "
         "suggestion and its reason appear on the row's review card; nothing is mapped until a "
         "person applies it. If the call fails the cards are shown without one."),
    Knob("llm_disclosures", "bool", "Read the disclosures with the model (one call per topic)",
         "Up to eight extra model calls per filing, one per topic of the Disclosures screen — "
         "auditor's opinion, going concern, contingent liabilities, guarantees, commitments, "
         "related parties, subsequent events, litigation — each with its own prompt, on the passages "
         "where the filing mentions the topic. The model says what is actually disclosed (not just "
         "where the word appears), with the amounts and counterparties as printed; every point must "
         "quote the page it cites or it is dropped. The credit view's report signals follow the "
         "reading. Off, or if a call fails, the screen shows the keyword scan."),
    Knob("llm_gap_routing", "bool", "Close subtotal gaps with the model",
         "When a section subtotal computed from the template's lines differs from the printed "
         "one, offer the model the extracted lines that reached no statement and ask which "
         "belong in that section's Others. Only groups that close the difference in BOTH "
         "periods are offered. Off, the difference stays a review item."),
    Knob("llm_contingent_liabilities", "bool", "Contingent-liabilities summary in plain English",
         "Classification and every total are always deterministic, computed only from the "
         "filing's own figures. Enabled, the model rewrites the summary paragraph and each "
         "unclassified item's short statement in clearer English, grounded only in the supplied "
         "facts. Off, or with no provider configured, the deterministic prose is shown."),
    Knob("evidence_floor", "number", "Alias evidence floor",
         "How nearly a printed caption must BE one of a concept's authored aliases before that "
         "counts as evidence. Nothing maps a row on wording alone — there is no string-similarity "
         "tier — so this decides only whether the deterministic evidence is strong enough to "
         "contradict the model's choice, and whether a caption is the printed name of a concept "
         "the framework computes rather than extracts.",
         minimum=0.0, maximum=1.0, step=0.01),
    Knob("alias_coverage_floor", "number", "Alias coverage floor",
         "How much of the alias the caption must actually explain. This is what stops a short "
         "heading ('LIABILITIES') from being read as a much longer concept name that merely "
         "contains it.",
         minimum=0.0, maximum=1.0, step=0.01),
    Knob("mapping_margin", "number", "Winner margin",
         "How far the winning concept must beat the runner-up before the mapping is accepted. "
         "Below the margin the mapping is declined rather than guessed, and the line is reported "
         "as reaching no template line at all.",
         minimum=0.0, maximum=1.0, step=0.01),
    Knob("auto_accept_confidence", "number", "Auto-accept confidence",
         "Combined confidence at or above which a mapped line is treated as settled. A line "
         "mapped below it is still mapped and still shown, marked as a weak match on its own "
         "confidence badge — it raises no review item, because the answer to a weak match is a "
         "better match rather than an analyst's time. The count of them is served with the review "
         "queue so narrowing that queue cannot read as better data.",
         minimum=0.0, maximum=1.0, step=0.01),
    Knob("recon_abs_tolerance", "number", "Reconciliation tolerance (absolute)",
         "Absolute floor when comparing a note total to the face figure it supports, in the "
         "document's own units.",
         minimum=0.0, maximum=1e9, step=1.0),
    Knob("recon_rel_tolerance", "number", "Reconciliation tolerance (relative)",
         "Relative band for the same comparison, so large figures are not held to sub-unit "
         "precision.",
         minimum=0.0, maximum=1.0, step=0.001),
    Knob("recon_corroboration_rel", "number", "Note-breakdown corroboration band",
         "How near a note total must come to the face figure before we accept the note really "
         "is a BREAKDOWN of it. Beyond this the note is recorded as 'not a breakdown' rather "
         "than reported as a mismatch — most cited notes are analyses or segment tables. "
         "Raising this turns more near-misses into review items; lowering it reports fewer.",
         minimum=0.0, maximum=1.0, step=0.01),
)

_KNOB_BY_KEY = {k.key: k for k in EXTRACTION_KNOBS}


class Placement(NamedTuple):
    """Where a knob sits on the Settings screen, and the one line it is introduced by.

    The screen is split into tabs; a tab into sections. ``summary`` is what a reader sees first —
    the knob's ``help`` is the full explanation, one click away. ``shown_when`` names another knob
    and the values of it under which this one is read at all: with the switch off, a knob that
    only that switch's route reads changes nothing, and showing it is what made the screen heavy.
    ``folded`` marks a section that is rarely changed: the screen shows its heading, how many
    settings it holds and how many differ from the default, and opens it on a click.

    The "route" tab is laid out by the route picker (``ROUTES``): ``llm_mapping`` and
    ``whole_pdf_to_model`` are the two switches the three routes set, and every other knob on that
    tab hangs, through ``shown_when``, off one of them — which is how the picker knows that a
    knob is that route's own option.
    """

    tab: str
    section: str
    summary: str
    shown_when: tuple[str, tuple] | None = None
    folded: bool = False


_ON = (True,)

#: Every knob's place on the screen, in the order the screen lists them.
KNOB_PLACEMENT: dict[str, Placement] = {
    # HOW A FILING IS READ: the two switches the route picker sets…
    "llm_mapping": Placement(
        "route", "Route",
        "Ask the model where each line item's figure is printed. Off: the rules decide every "
        "line."),
    "whole_pdf_to_model": Placement(
        "route", "Route",
        "Send the PDF itself to the model, which reads each line from the pages as printed; the "
        "rules read alongside it and take over the filing if it fails."),
    # …the options of "Rules + model questions" (and of the whole-PDF route's fallback)…
    "llm_document_context": Placement(
        "route", "What the model reads",
        "How much of the annual report goes with each question: only the notes the line chose, "
        "the whole report for lines that may be read from anywhere, or the whole report every "
        "time.",
        ("llm_mapping", _ON)),
    "llm_request_grouping": Placement(
        "route", "How requests are put together",
        "Whether line items that need the same notes share one request, or each is asked on its "
        "own.",
        ("llm_mapping", _ON), folded=True),
    "llm_lines_per_request": Placement(
        "route", "How requests are put together",
        "The most line items one shared request asks about.",
        ("llm_request_grouping", ("identical", "similar")), folded=True),
    "llm_group_similarity": Placement(
        "route", "How requests are put together",
        "How alike two lines' notes must be before they share a request.",
        ("llm_request_grouping", ("similar",)), folded=True),
    "llm_note_synonyms": Placement(
        "route", "Finding notes",
        "Also find a note whose heading uses another name for the line (\"Fixed assets\" for "
        "property, plant and equipment).",
        ("llm_mapping", _ON), folded=True),
    "llm_note_router": Placement(
        "route", "Finding notes",
        "One extra call that asks the model which notes print each line, adding up to two to its "
        "request.",
        ("llm_mapping", _ON), folded=True),
    "llm_focus_only": Placement(
        "route", "Which lines are asked",
        "Ask the model only about the line items on the Focus line items list; the rules read "
        "every other line.",
        ("llm_mapping", _ON)),
    # …and the options of "Whole PDF to the model".
    "whole_pdf_pages_per_request": Placement(
        "route", "Request limits",
        "The most pages in one request; a longer file is sent in parts.",
        ("whole_pdf_to_model", _ON), folded=True),
    "whole_pdf_max_request_mb": Placement(
        "route", "Request limits",
        "The largest request the provider accepts.",
        ("whole_pdf_to_model", _ON), folded=True),
    "whole_pdf_lines_per_request": Placement(
        "route", "Request limits",
        "The most template lines one request asks about.",
        ("whole_pdf_to_model", _ON), folded=True),
    # EVERYTHING ELSE, out of the way on its own tab, the rarely changed groups folded.
    "llm_row_suggestions": Placement(
        "advanced", "Extra model calls",
        "One call that suggests a line for each printed row that reached none. A person applies "
        "it."),
    "llm_disclosures": Placement(
        "advanced", "Extra model calls",
        "Up to eight calls that read the Disclosures screen's topics: going concern, guarantees, "
        "litigation and the rest."),
    "llm_gap_routing": Placement(
        "advanced", "Extra model calls",
        "When a subtotal does not add up, ask the model which unplaced rows close the gap."),
    "llm_contingent_liabilities": Placement(
        "advanced", "Extra model calls",
        "Let the model rewrite the contingent-liabilities summary in clearer English. The "
        "figures stay the rules'."),
    "llm_parallel_requests": Placement(
        "advanced", "Sending requests",
        "How many requests are sent at once. Lower it if the provider refuses requests for rate "
        "limits.",
        folded=True),
    "ocr_scanned_pages": Placement(
        "advanced", "Pages and OCR",
        "Read a page that has no text layer (a scan) with the offline OCR engine. Off: such "
        "pages are skipped.",
        folded=True),
    "document_reader": Placement(
        "advanced", "Pages and OCR",
        "Send the filing to Kensho Extract and read pages from its answer. This sends the PDF "
        "outside this server.",
        folded=True),
    "document_reader_pages": Placement(
        "advanced", "Pages and OCR",
        "Which pages the engine reads: only those with no text layer, or every page.",
        ("document_reader", ("kensho",)), folded=True),
    "evidence_floor": Placement(
        "advanced", "Matching captions",
        "How closely a caption must match one of a line's aliases to count as evidence.",
        folded=True),
    "alias_coverage_floor": Placement(
        "advanced", "Matching captions",
        "How much of the alias the caption must cover, so a short heading is not read as a long "
        "name.",
        folded=True),
    "mapping_margin": Placement(
        "advanced", "Matching captions",
        "How far the winning line must beat the runner-up; below it the row is left unmapped.",
        folded=True),
    "auto_accept_confidence": Placement(
        "advanced", "Matching captions",
        "The confidence at which a mapping counts as settled rather than a weak match.",
        folded=True),
    "recon_abs_tolerance": Placement(
        "advanced", "Reconciliation",
        "The difference allowed between a note total and its face figure, in the filing's units.",
        folded=True),
    "recon_rel_tolerance": Placement(
        "advanced", "Reconciliation",
        "The same allowance as a fraction of the figure, for large figures.",
        folded=True),
    "recon_corroboration_rel": Placement(
        "advanced", "Reconciliation",
        "How near a note total must come before the note counts as a breakdown of the figure.",
        folded=True),
}


class Route(NamedTuple):
    """One of the three ways a filing is read, as the Settings screen's route picker offers it.

    A route is not a setting of its own: it is a reading of two knobs, ``llm_mapping`` and
    ``whole_pdf_to_model`` (``sets``), which is what the pipeline decides on (``core.pipeline`` on
    the second, ``stages.line_item_llm`` on the first). Picking a route on the screen sets those
    knobs and nothing else, so a route can never disagree with what a run does. ``switch`` is the
    knob the route's own options hang off (``Placement.shown_when``); ``fallback`` is what a
    failed run of the route is read with — the same values with these overridden.

    The words are facts about the pipeline, kept here beside the knob help for the same reason:
    a card that says what a route costs or skips must change when the route does.
    """

    id: str
    name: str
    summary: str
    best_for: str
    cost: str
    limits: tuple[str, ...]
    sets: tuple[tuple[str, bool], ...]
    switch: str | None = None
    fallback: tuple[tuple[str, bool], ...] = ()


#: The route picker's cards, in the order it shows them.
ROUTES: tuple[Route, ...] = (
    Route(
        "rules", "Rules only",
        "The rules read every figure from the pages; the model is asked nothing about line items.",
        "Running without a model, or a baseline to compare a model run with.",
        "No line-item requests.",
        ("A line the rules cannot place stays empty or goes to review.",
         "The extra model calls (Advanced tab) still go out while switched on."),
        (("whole_pdf_to_model", False), ("llm_mapping", False))),
    Route(
        "questions", "Rules + model questions",
        "The rules read the pages; then the model is asked where each line item's figure is "
        "printed, and each answer is checked against the rows it cites.",
        "Most filings: the model finds what the rules miss, and an answer in another currency or "
        "scale, or citing a caption the line excludes, is refused.",
        "About 50 to 75 requests a filing as shipped. Tokens grow with how much of the report "
        "goes with each question.",
        ("The model reads the text the rules took from each page, not the page as printed.",
         "A request that fails leaves its lines to the rules."),
        (("whole_pdf_to_model", False), ("llm_mapping", True)),
        switch="llm_mapping"),
    Route(
        "whole_pdf", "Whole PDF to the model",
        "The PDF itself goes to the model, which reads each line from the pages as printed. The "
        "rules still read the note tables alongside it, check each model figure against the page "
        "and their own reading, and fill the lines the model leaves empty.",
        "Filings with complex tables, where seeing the page as printed matters. The note screens "
        "and the contingent-liabilities narrative are still filled.",
        "The most tokens: every request carries the pages (about 1,500 to 4,800 tokens a page, "
        "cached after the first). Few requests: a statement's lines go in one.",
        ("Every page is sent and every line asked: the Page Scope screen changes only what the "
         "rules read beside the model (and the fallback).",
         "Needs a provider that can be sent a file (anthropic, bedrock_gateway, openai, "
         "azure_openai).",
         "If the file cannot be sent, or a statement's request fails, the whole filing is read "
         "by the fallback."),
        (("whole_pdf_to_model", True),),
        switch="whole_pdf_to_model",
        fallback=(("whole_pdf_to_model", False),)),
)


def route_of(values: dict) -> str:
    """The id of the route these knob values read a filing by — exactly one matches any values."""
    for route in ROUTES:
        if all(bool(values.get(key)) == want for key, want in route.sets):
            return route.id
    raise ValueError("no route matches")  # unreachable while ROUTES covers both switches


def _route_payload(route: Route) -> dict:
    return {"id": route.id, "name": route.name, "summary": route.summary,
            "best_for": route.best_for, "cost": route.cost, "limits": list(route.limits),
            "sets": dict(route.sets), "switch": route.switch,
            "fallback": dict(route.fallback) if route.fallback else None}


def provider_takes_files() -> bool:
    """Whether the provider config.toml [llm] names can be sent a PDF file — the whole-PDF route's
    first test (`stages.whole_pdf.WholePdfStage._provider`), asked the same way, so the screen can
    say before a run that every filing would fall back."""
    import app.adapters  # noqa: F401 — import for its registration side effect
    from app.ports.llm import takes_documents

    provider_id = get_settings().llm.provider
    if provider_id == "stub":
        return False
    try:
        return takes_documents(registry.get("llm", provider_id))
    except Exception:  # noqa: BLE001 — an adapter that will not build cannot be sent a file either
        return False


def _persist(scope: str, values: dict) -> None:
    """Write these overrides to the database, one row per setting (upsert).

    Deliberately best-effort: a settings screen must not 500 because the override table is
    momentarily unavailable, and the value has already been applied in-process. The failure is
    that the change does not survive a restart, which is strictly better than losing the edit.
    """
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride

    try:
        with SessionLocal() as session:
            for key, value in values.items():
                row = session.execute(
                    select(SettingOverride).where(SettingOverride.scope == scope,
                                                  SettingOverride.key == key)
                ).scalar_one_or_none()
                if row is None:
                    session.add(SettingOverride(scope=scope, key=key, value={"v": value}))
                else:
                    row.value = {"v": value}
            session.commit()
    except Exception:  # noqa: BLE001 — persistence is best-effort; see above
        pass


def _forget(scope: str) -> None:
    """Drop every persisted override in a scope, so the config file's values apply again."""
    from sqlalchemy import delete

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride

    try:
        with SessionLocal() as session:
            session.execute(delete(SettingOverride).where(SettingOverride.scope == scope))
            session.commit()
    except Exception:  # noqa: BLE001
        pass


def _forget_keys(scope: str, keys) -> None:
    """Drop these persisted overrides of a scope, and only these."""
    from sqlalchemy import delete

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride

    try:
        with SessionLocal() as session:
            session.execute(delete(SettingOverride).where(SettingOverride.scope == scope,
                                                          SettingOverride.key.in_(list(keys))))
            session.commit()
    except Exception:  # noqa: BLE001 — best effort, as `_persist`
        pass


#: SETTINGS THAT ARE GONE. The Settings screen saves every knob whenever any one is changed, so a
#: deployment that saved anything has a row for each; one left in the table would read as
#: configuration and do nothing. Each is removed at startup, what it said logged, as the LLM rows
#: are (`load_persisted`) — and a client that still sends one is ignored (`set_extraction_config`
#: honours only the declared knobs).
#:
#:   whole_pdf_pages  "statements" | "all": which pages the whole-PDF route sent. It now sends
#:                    every page and asks every line, whatever the page classifier found and
#:                    whatever is set on the Page Scope screen (`services.whole_pdf.pages_to_send`);
#:                    a saved "statements" must not narrow it again.
RETIRED_KNOBS = ("whole_pdf_pages",)


def _drop_retired(stored: dict) -> dict:
    """The stored extraction overrides without the retired knobs, whose rows are deleted."""
    retired = {k: stored[k] for k in RETIRED_KNOBS if k in stored}
    if not retired:
        return stored
    import logging
    logging.getLogger(__name__).warning(
        "Removed saved settings that no longer exist (%s): the whole-PDF route sends every page "
        "and asks every line, whatever is set on the Page Scope screen.",
        ", ".join(f"{k}={v!r}" for k, v in sorted(retired.items())))
    _forget_keys(SCOPE_EXTRACTION, retired)
    return {k: v for k, v in stored.items() if k not in retired}


def _stored(scope: str) -> dict:
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride

    try:
        with SessionLocal() as session:
            rows = session.execute(
                select(SettingOverride).where(SettingOverride.scope == scope)).scalars().all()
            return {r.key: (r.value or {}).get("v") for r in rows}
    except Exception:  # noqa: BLE001 — a database without the table yet behaves as "no overrides"
        return {}


def load_persisted() -> None:
    """Re-apply the stored overrides onto this process's settings. Called once at startup.

    Order matters: the config-file DEFAULTS are captured FIRST (``_seed``), so "restore
    defaults" still means what config.toml shipped and not what was previously saved.
    """
    global _LOADED
    _seed()
    _LOADED = True

    feats = _stored(SCOPE_FEATURES)
    for key in ("ui_localization", "review_required", "seed_demo"):
        if key in feats and feats[key] is not None:
            _RUNTIME[key] = bool(feats[key])

    # THE LLM IS NOT STORED HERE ANY MORE (see the module docstring). A row an older release
    # saved would otherwise sit in the table looking like configuration; it is removed, and what
    # it said is logged so nobody wonders where their setting went.
    llm_stored = _stored(SCOPE_LLM)
    if llm_stored:
        import logging
        logging.getLogger(__name__).warning(
            "Removed LLM settings saved from the Settings screen (%s): the LLM is defined only in "
            "config.toml [llm].", ", ".join(f"{k}={v!r}" for k, v in sorted(llm_stored.items())))
        _forget(SCOPE_LLM)
    _warn_if_provider_unregistered()

    ex_stored = _read_as_saved(_drop_retired(_stored(SCOPE_EXTRACTION)))
    if ex_stored:
        try:
            set_extraction_config(**ex_stored, persist=False)
        except ValueError:
            # A stored value that is no longer valid (a knob's range tightened between
            # releases) must not stop the app from starting; the config default stands.
            pass

    # THE FOCUS LINE ITEMS MASTER, onto the list a run reads (`services.focus_master`). Best effort,
    # as above: a database without the table yet leaves config.toml's list in place.
    from app.services.focus_master import apply as apply_focus_master

    apply_focus_master()


# A VALUE SAVED BEFORE ITS SETTING'S MEANING MOVED IS READ AS WHAT IT MEANT WHEN IT WAS SAVED.
#
# `llm_document_context` had two settings, and under "selected" every line that may be read from
# anywhere was still asked with the whole annual report. That is now the setting "anywhere";
# "selected" now sends no line the whole report. The Settings screen saves EVERY knob whenever
# any one is changed, so a stored "selected" from before is almost always the old default, never
# chosen — read as written, it would switch the whole report off for those lines without anyone
# asking. A value saved by this release is saved with this marker beside it and read as it is.
_DOCUMENT_CONTEXT_MARKER = "_llm_document_context_v2"
# THE SAME HAZARD FOR `llm_request_grouping`, whose DEFAULT moved from "none" to "identical" (at the
# user's instruction: about 430 model calls a filing became about 70). The every-knob save stored
# the old default "none" for anyone who saved ANY knob — switching the whole-PDF route off on the
# Settings screen among them, which is how a deployment reaches the normal route — and re-applied
# as written it would keep those deployments on one request per line without anyone choosing it.
# A pre-marker "none" is read as the default this release ships, said in the log, and saved with
# the marker; a "none" chosen from now on is saved with the marker and read as it is.
_REQUEST_GROUPING_MARKER = "_llm_request_grouping_v2"
# AND FOR `whole_pdf_pages_per_request`, whose default moved from 100 to 600 when the shipped
# model's 1,000,000-token window was made the default (`llm.context_window_tokens`). A stored 100
# from before is the old default, saved by the every-knob save or "Restore defaults"; re-applied as
# written it would keep those deployments at 100 pages a part. Read as the shipped default, said in
# the log, and saved with the marker; a 100 chosen from now on is read as it is.
_PAGES_PER_REQUEST_MARKER = "_whole_pdf_pages_per_request_v2"
_OLD_PAGES_PER_REQUEST = 100
#: The marker each setting is saved beside — see `set_extraction_config`.
_MARKERS = {"llm_document_context": _DOCUMENT_CONTEXT_MARKER,
            "llm_request_grouping": _REQUEST_GROUPING_MARKER,
            "whole_pdf_pages_per_request": _PAGES_PER_REQUEST_MARKER}


def _read_as_saved(stored: dict) -> dict:
    """The stored extraction overrides, each value saved before its setting moved read as what it
    meant when it was saved — and that reading saved, with its marker, so it is made once and the
    table says what applies."""
    return _pages_as_saved(_grouping_as_saved(_context_as_saved(stored)))


def _pages_as_saved(stored: dict) -> dict:
    """A pre-marker 100 pages a request read as the number this release ships."""
    value = stored.get("whole_pdf_pages_per_request")
    try:
        old = value is not None and float(value) == _OLD_PAGES_PER_REQUEST
    except (TypeError, ValueError):
        old = False
    if not old or stored.get(_PAGES_PER_REQUEST_MARKER):
        return stored
    shipped = (_EXTRACTION_DEFAULTS or {}).get("whole_pdf_pages_per_request")
    if shipped is None or float(shipped) == _OLD_PAGES_PER_REQUEST:
        return stored
    import logging
    logging.getLogger(__name__).warning(
        "extraction.whole_pdf_pages_per_request: the stored value 100 was saved when 100 was the "
        "default (the Settings screen saves every setting whenever one is changed), so it is read, "
        "and saved, as the shipped default %s, for the model's 1,000,000-token window. Choose 100 "
        "again on the Settings screen for a model with a smaller window.", shipped)
    _persist(SCOPE_EXTRACTION, {"whole_pdf_pages_per_request": shipped,
                                _PAGES_PER_REQUEST_MARKER: True})
    return {**stored, "whole_pdf_pages_per_request": shipped, _PAGES_PER_REQUEST_MARKER: True}


def _grouping_as_saved(stored: dict) -> dict:
    """A pre-marker "none" read as the grouping this release ships (unless that is "none" too)."""
    if stored.get("llm_request_grouping") != "none" or stored.get(_REQUEST_GROUPING_MARKER):
        return stored
    shipped = str((_EXTRACTION_DEFAULTS or {}).get("llm_request_grouping") or "none")
    if shipped == "none":
        return stored
    import logging
    logging.getLogger(__name__).warning(
        "extraction.llm_request_grouping: the stored value 'none' was saved when 'none' was the "
        "default (the Settings screen saves every setting whenever one is changed), so it is read, "
        "and saved, as the shipped default %r: line items needing the same notes or the same "
        "section of a statement share a request. Choose 'none' again on the Settings screen for one "
        "request per line.", shipped)
    _persist(SCOPE_EXTRACTION, {"llm_request_grouping": shipped, _REQUEST_GROUPING_MARKER: True})
    return {**stored, "llm_request_grouping": shipped, _REQUEST_GROUPING_MARKER: True}


def _context_as_saved(stored: dict) -> dict:
    """A pre-marker "selected" read as "anywhere"."""
    if stored.get("llm_document_context") != "selected" or stored.get(_DOCUMENT_CONTEXT_MARKER):
        return stored
    import logging
    logging.getLogger(__name__).warning(
        "extraction.llm_document_context: the stored value 'selected' was saved before the "
        "setting gained 'anywhere', and meant what 'anywhere' means now (lines that may be read "
        "from anywhere are asked with the whole annual report). It is read, and saved, as "
        "'anywhere'; choose 'selected' again on the Settings screen to send no line the report.")
    _persist(SCOPE_EXTRACTION, {"llm_document_context": "anywhere",
                                _DOCUMENT_CONTEXT_MARKER: True})
    return {**stored, "llm_document_context": "anywhere", _DOCUMENT_CONTEXT_MARKER: True}


def _seed() -> None:
    global _EXTRACTION_DEFAULTS
    feats = get_settings().features
    _RUNTIME.setdefault("ui_localization", feats.ui_localization)
    _RUNTIME.setdefault("review_required", feats.review_required)
    _RUNTIME.setdefault("seed_demo", feats.seed_demo)
    if _EXTRACTION_DEFAULTS is None:
        ex = get_settings().extraction
        _EXTRACTION_DEFAULTS = {k.key: getattr(ex, k.key) for k in EXTRACTION_KNOBS}


def get_seed_demo() -> bool:
    """Whether the seeded sample project is currently loaded. Off = greenfield/empty."""
    _seed()
    return bool(_RUNTIME["seed_demo"])


def set_seed_demo(value: bool) -> bool:
    _seed()
    _RUNTIME["seed_demo"] = bool(value)
    _persist(SCOPE_FEATURES, {"seed_demo": _RUNTIME["seed_demo"]})
    return _RUNTIME["seed_demo"]


def get_ui_localization() -> bool:
    _seed()
    return bool(_RUNTIME["ui_localization"])


def set_ui_localization(value: bool) -> bool:
    _seed()
    _RUNTIME["ui_localization"] = bool(value)
    _persist(SCOPE_FEATURES, {"ui_localization": _RUNTIME["ui_localization"]})
    return _RUNTIME["ui_localization"]


def get_review_required() -> bool:
    _seed()
    return bool(_RUNTIME["review_required"])


def set_review_required(value: bool) -> bool:
    _seed()
    _RUNTIME["review_required"] = bool(value)
    _persist(SCOPE_FEATURES, {"review_required": _RUNTIME["review_required"]})
    return _RUNTIME["review_required"]


def _warn_if_provider_unregistered() -> None:
    """Say at startup, not per stage, when config.toml names an LLM provider no adapter answers to."""
    import logging

    import app.adapters  # noqa: F401 — import for its registration side effect

    provider = get_settings().llm.provider
    available = registry.available("llm")
    if provider not in available:
        logging.getLogger(__name__).warning(
            "config.toml [llm] provider=%r is not a registered LLM adapter (available: %s); "
            "LLM steps will be skipped or refused.", provider, ", ".join(sorted(available)))


_SCREEN_ORDER = {key: i for i, key in enumerate(KNOB_PLACEMENT)}


def _placed(knob: Knob) -> Placement:
    # A knob with no placement still appears — on its own "other" tab, introduced by its label —
    # rather than vanishing from the screen. tests/test_settings_extraction.py fails it.
    return KNOB_PLACEMENT.get(knob.key) or Placement("other", "", knob.label)


def _field(k: Knob) -> dict:
    place = _placed(k)
    return {"key": k.key, "kind": k.kind, "label": k.label, "help": k.help,
            "min": k.minimum, "max": k.maximum, "step": k.step,
            "choices": list(k.choices),
            "choice_info": [{"value": v, "label": label, "note": note}
                            for v, label, note in k.choice_info],
            "tab": place.tab, "section": place.section, "summary": place.summary,
            "folded": place.folded,
            "shown_when": ({"key": place.shown_when[0], "values": list(place.shown_when[1])}
                           if place.shown_when else None)}


def _screen_order(knob: Knob) -> int:
    return _SCREEN_ORDER.get(knob.key, len(_SCREEN_ORDER))


def extraction_config() -> dict:
    """The extraction settings an admin may edit, with the shipped default for each.

    ``defaults`` is what the config file said at startup, so the UI can offer "restore defaults"
    and show at a glance which knobs have been moved away from them.
    """
    _seed()
    ex = get_settings().extraction
    return {
        "values": {k.key: getattr(ex, k.key) for k in EXTRACTION_KNOBS},
        "defaults": dict(_EXTRACTION_DEFAULTS or {}),
        # In the screen's order (`KNOB_PLACEMENT`), each with its tab, section, one-line summary,
        # the switch it is read under and whether its section starts folded, so the screen is
        # laid out from this list too.
        "fields": [_field(k) for k in sorted(EXTRACTION_KNOBS, key=_screen_order)],
        # The route picker's three cards (`ROUTES`), and which of them the values above read a
        # filing by.
        "routes": [_route_payload(r) for r in ROUTES],
        "route": route_of({k.key: getattr(ex, k.key) for k in EXTRACTION_KNOBS}),
    }


def set_extraction_config(*, persist: bool = True, **fields) -> dict:
    """Apply admin edits onto the live extraction settings, refusing anything out of range.

    Only the declared knobs are honoured. A value outside a knob's bounds is REJECTED rather
    than clamped: silently substituting a different threshold than the one an admin typed would
    make the screen lie about what the pipeline is doing.

    Raises ValueError naming the offending field.
    """
    _seed()
    ex = get_settings().extraction
    cleaned: dict = {}
    for key, value in fields.items():
        knob = _KNOB_BY_KEY.get(key)
        if knob is None or value is None:
            continue
        if knob.kind == "bool":
            cleaned[key] = bool(value)
            continue
        if knob.kind == "choice":
            if str(value) not in knob.choices:
                raise ValueError(
                    f"{key} must be one of {', '.join(knob.choices)} (got {value!r})")
            cleaned[key] = str(value)
            continue
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number (got {value!r})") from exc
        if knob.minimum is not None and num < knob.minimum:
            raise ValueError(f"{key} must be at least {knob.minimum} (got {num})")
        if knob.maximum is not None and num > knob.maximum:
            raise ValueError(f"{key} must be at most {knob.maximum} (got {num})")
        cleaned[key] = num
    for key, value in cleaned.items():
        setattr(ex, key, value)
    if persist and cleaned:
        # Saved with the marker that says this value has the setting's present meaning — see
        # `_read_as_saved`.
        _persist(SCOPE_EXTRACTION, {**cleaned, **{marker: True for key, marker in _MARKERS.items()
                                                  if key in cleaned}})
    return extraction_config()


def reset_extraction_config() -> dict:
    """Restore every extraction knob to the value the config file shipped.

    The stored rows are DELETED rather than rewritten with the defaults, so a later change to
    config.toml is picked up instead of being masked by a saved copy of the old default.
    """
    _seed()
    ex = get_settings().extraction
    for key, value in (_EXTRACTION_DEFAULTS or {}).items():
        setattr(ex, key, value)
    _forget(SCOPE_EXTRACTION)
    return extraction_config()


def reset(*, persisted: bool = True) -> None:
    """Test helper: drop runtime overrides so config defaults are re-seeded.

    Also clears the persisted rows by default — otherwise one test's saved threshold would be
    re-applied to the next by ``load_persisted``.
    """
    global _EXTRACTION_DEFAULTS, _LOADED
    _RUNTIME.clear()
    _LOADED = False
    if persisted:
        for scope in (SCOPE_FEATURES, SCOPE_LLM, SCOPE_EXTRACTION):
            _forget(scope)
    if _EXTRACTION_DEFAULTS is not None:
        ex = get_settings().extraction
        for k, v in _EXTRACTION_DEFAULTS.items():
            setattr(ex, k, v)
        _EXTRACTION_DEFAULTS = None
