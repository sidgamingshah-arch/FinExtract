"""Line-item definitions — the single declarative source of truth for a line item.

WHY THIS EXISTS, AND WHAT IT REPLACED. Two models used to describe a financial line item.

  The ONTOLOGY said which of 462 concepts may CLAIM a caption printed on this statement, under
  this banner — a per-concept refusal. `mapping._allowed` is the conjunction of four gates
  (statement, section banner, the concept's own `exclude_hints`, and exclusive-class membership),
  handed INTO the alias lookup as a predicate rather than applied after it, because when two
  concepts claim one alias the winner must be the one that fits where the caption was printed.

  The CONFIGURATOR said how a line item is ASSEMBLED once values are already keyed — cascades,
  signed sums, note-level decomposition. Its engine took `dict[str, Decimal | None]`: no caption,
  no page, no banner ever entered it, so it sat strictly downstream of the decision the ontology
  existed to make.

They are now ONE model, and the scoping half is the reason. Measured on the shipped rulebook:
1,969 distinct normalised caption strings, 420 of them claimed by more than one concept, and 96
shared across DIFFERENT statements — `intangible assets` is claimed by two balance-sheet concepts
and two P&L concepts, and nothing in the caption separates them. Strip the gate and those resolve
at the exact tier, confidence 1.0, `needs_review=False`: wrong figures on the face, not blanks,
with every subtotal still tying. Those 420 are PER-CONCEPT facts, so they cannot be lifted into a
global rules layer — which is precisely why the gate had to come down into this file rather than
stay above it.

A definition says three kinds of thing, and the TYPE decides which apply:

  WHERE IT MAY BE CLAIMED FROM (every type) — the gate. `statement` + `section_scope`, folded in
                from `section_defaults` via `inherits` so it is authored once per section rather
                than 462 times. `match_priority` breaks a tie the gate leaves standing.
  HOW ITS CAPTION IS RECOGNISED (extracted) — aliases per locale, regex and keyword hints, regex
                vetoes, prose criteria for the LLM, and `note_source` for a note-level part.
  HOW ITS VALUE IS ASSEMBLED (calculated / intermediate / derived) — signed sums, absolute
                values, and ordered cascades whose first resolving rung wins.

VOCABULARIES ARE IMPORTED, NEVER RESTATED. `statement`, `section_scope`'s tokens, the sign,
temporality and residual types all come from `app.schemas.ontology` and `app.core.models.enums`.
This is not tidiness. The first version of this file declared its own `SearchScope` containing
`income_statement` — a token that appeared NOWHERE else in the backend, where the classifier,
`StatementType` and every template say `profit_and_loss`. Read as a gate that single spelling
would have refused every P&L concept on every page. A parallel vocabulary is how that happens, so
there is no longer a parallel vocabulary.

FLAT, NOT NESTED, and keyed by `parent`. A sub-line item is a line item, so nesting them would
mean two shapes for one thing and a different editor at each depth. But `parent` alone was
overloaded: it was carrying display nesting AND arithmetic rollup, and the twelve parts of the
depreciation line are ALTERNATIVE restatements of one figure, not addends — summing them is
meaningless. `rollup` now says which relation a parent holds over its children.

ABSENT IS NOT EMPTY. Every gate field is optional and `None`/empty means PERMISSIVE — "nothing
was said", never "nothing is allowed". A definition that declares no statement is claimable
anywhere, exactly as it was before the gate existed.
"""
from __future__ import annotations

import functools
import itertools
import re
from typing import Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.models.enums import StatementType
from app.schemas.ontology import (
    AliasMatching,
    Binding,
    DecompositionRule,
    ExtractionMode,
    GlobalRules,
    NettingRule,
    Normalisation,
    NoteUse,
    NumberFormat,
    ResidualFramework,
    ResidualPolicy,
    ScopeSelection,
    SignExpectation,
    SignRule,
    Temporality,
    UnitOfAccount,
    ValidationRules,
    ValueScope,
    WorkedExample,
)

LineItemType = Literal["extracted", "calculated", "intermediate", "derived"]

# WHAT A LINE ITEM OUTPUTS. `type` says how the value ARRIVES (read off the page, or arithmetic);
# this says what KIND of thing it is, which is a different question and until now had one possible
# answer.
#
#   "value"  — a number. THE DEFAULT, and what every shipped line is. Everything that totals,
#              reconciles or checks an identity assumes this.
#   "phrase" — a short piece of text taken FROM the document (an audit opinion's wording, a going-
#              concern statement, a covenant's stated threshold as printed).
#   "prose"  — text GENERATED for this line by the model from the line's own `prompt`. Nothing is
#              read off the page; the prompt is the specification.
#
# A NON-NUMERIC LINE IS NOT A FIGURE, and the arithmetic must not pretend otherwise: it has no sign
# to expect, no unit of account, and it cannot be a component of a subtotal or appear in a balance
# identity. Contributing 0 to a total would be worse than being absent, because the total would
# still balance and nothing would say the line had been skipped.
OutputStructure = Literal["value", "phrase", "prose"]

# HOW THIS LINE'S NOTES ARE FOUND. `semantic` scores the line's own meaning against each note's
# HEADER (`services.line_item_notes`); `patterns` matches the authored `note_source.note_title_any`
# regexes against the same headers (`note_context.identified_notes`).
NoteSelection = Literal["semantic", "patterns"]

# Where a caption may be READ FROM, in the order they are searched — a search ORDER, not a gate.
# `notes` leads by default: a note states the figure the face only summarises, and for the eight
# output lines the note IS the authoritative source, which is why the derivation services read
# notes and not the face.
#
# THE TOKENS ARE `StatementType`'s OWN, plus the two places that are not statements. Spelled any
# other way this list is unusable as a gate, because nothing else in the backend would recognise
# the values — see the module docstring.
SearchScope = Literal["notes", "balance_sheet", "profit_and_loss", "cash_flow",
                      "equity_changes", "covenants_supplemental", "statement_setup",
                      "front_matter"]
DEFAULT_SCOPES: tuple[SearchScope, ...] = ("notes",)

# `from_section` resolves through the section banner the caption sits under. It is the right
# answer for a balance-sheet line printed INSIDE a section, and no answer at all for a statement
# total, which sits under none — those must say the side outright.
Side = Literal["from_section", "asset", "liability", "equity", "none"]

SECTION_SIDE: dict[str, Side] = {
    # The template/analyst section ids…
    "current_assets": "asset", "non_current_assets": "asset",
    "current_liabilities": "liability", "non_current_liabilities": "liability",
    "equity": "equity",
    # …and the rulebook's own, so a folded `section_scope` resolves a side without a second
    # translation table for anyone to forget to update.
    "bs_ca": "asset", "bs_nca": "asset",
    "bs_cl": "liability", "bs_ncl": "liability",
    "bs_equity": "equity",
}

# Which key-space a definition's `key` lives in. The ontology checks every canonical_key against
# the output template at both doors; 13 of the shipped sub-line items are deliberately OFF-template
# (a note-level part of a line, not a line), so an unconditional check cannot be copied over. This
# field is what lets the gate be exact instead of disabled.
Namespace = Literal["template", "internal"]

# What a parent asserts about its children. The overload this fixes was real: the twelve parts of
# `is_pl__deprec_and_impairment_oper_exp` are alternative sources for ONE figure — filings
# routinely restate the same charge across several of those notes, so they must never be summed —
# and `check_rollups` would have summed them. `alternatives` is how a parent says so.
Rollup = Literal["sum", "alternatives", "none"]


class Term(BaseModel):
    """One addend of a formula: a line item, a fixed number, or the absolute value of a line item.

    `abs` is separate from `sign` on purpose. A filing prints accumulated depreciation as
    -1,842,330 or in brackets; `abs` says "take the magnitude", `sign` says "and subtract it".
    Collapsing them would make one of the two impossible to express.
    """

    ref: str = ""                      # another line item's key
    const: float | None = None         # …or a fixed number instead
    sign: Literal[1, -1] = 1
    abs: bool = False
    # WHAT THIS TERM'S ABSENCE MEANS. Three roles, because the shipped cascades need exactly
    # three and a boolean could only say two of them:
    #
    #   required    absent -> the whole rung fails. "Revenue minus cost of sales" is not
    #               revenue when the filing prints no cost of sales.
    #   any_of      at least one term of this role must be present for the rung to resolve, and
    #               those that are get summed. "The four operating-expense notes" is still a sum
    #               when a filing discloses three of them.
    #   adjustment  contributes when present, and NEVER justifies the rung on its own.
    #
    # The third is the one a boolean could not express, and its absence was a real defect: with
    # every term merely "optional", a rung whose only present figure was the cost-of-sales
    # DEDUCTION resolved to a negative depreciation charge — a figure assembled out of a
    # deduction with nothing to deduct it from. Caught by a test rather than by a filing, which
    # is the only reason it never reached an output.
    role: Literal["required", "any_of", "adjustment"] = "required"

    @model_validator(mode="after")
    def _one_source(self):
        if bool(self.ref) == (self.const is not None):
            raise ValueError("a term needs exactly one of `ref` or `const`")
        if self.const is not None and self.abs:
            raise ValueError("`abs` applies to a referenced line item, not to a fixed number")
        return self


class CascadeRung(BaseModel):
    """One attempt in a derived line's priority order.

    `terms` rather than a string expression: the rungs of the shipped cascades are all signed
    sums over named datasets (`pbt_depreciation - cos_depreciation`), and an expression language
    would need a parser, a precedence table and its own error reporting to say the same thing.
    """

    id: str                            # "P1", "Find_2" — as the run log and the audit trail name it
    terms: list[Term] = Field(default_factory=list)
    note: str = ""                     # why this rung exists, for the person reading the screen
    # A RUNG THAT COMPUTES BELOW ZERO IS REFUSED, and the next rung is tried. This is not a
    # nicety: a rung resolving to -50 has mistaken what its inputs meant — the commonest case is
    # a cascade whose only present figure was a DEDUCTION with nothing to deduct it from — and
    # publishing it would be worse than falling through. Default True because every shipped
    # cascade is a charge, a balance or an exposure, none of which can be negative; a definition
    # that genuinely can (a net movement, a carryforward) turns it off.
    refuse_negative: bool = True
    # MAY THIS RUNG DISPLACE A FIGURE THE FILING PRINTED? Off by default, because the ordinary case
    # is that a printed row and a cascade are two routes to the SAME number and the printed one is
    # the filing's own statement of it.
    #
    # THE CASE THAT NEEDS IT ON, and it is a distinction between kinds of rung rather than a
    # preference. Compare the two rungs measured on the reference filings:
    #
    #   bs_nca__secur_and_other_fincl_assets_ltp / LTP_P1 — "from the rows the in-scope notes
    #     themselves classify as non-current, less derivatives, less other receivables, less
    #     equity-method investments, plus the CP carry-forward". NO PRINTED ROW CAN STATE THAT. It
    #     computes 788,507 while a caption binds 128,407 — a different quantity, not a worse
    #     reading of the same one. Keeping the printed figure meant the rung the author wrote
    #     decided nothing whenever the matcher happened to bind a row.
    #
    #   is_pl__sales_revenues / P6 — "alternative reconstruction from the INDUSTRY /
    #     OPERATING-SEGMENT axis", reached only after every route the spec describes. This line's
    #     P1 IS the face ("主营业务收入 reported on the face of the income statement"), so a printed
    #     figure here is precisely what the cascade's own top rung was looking for. Measured:
    #     letting P6 displace it published 2,609,259 — one industry segment — in place of the
    #     4,995,768 the face prints as TURNOVER.
    #
    # So the question is whether the rung RECONSTRUCTS something the face does not state, or
    # RESTATES something it does. Only the author of the cascade knows, which is why this is a
    # declaration and not a heuristic over rung order or magnitude — both of which fit these two
    # filings and neither of which means anything.
    outranks_printed: bool = False


class NoteSource(BaseModel):
    """Which note a sub-line item is read from, and which of its rows count.

    This is what the closed enumerations become. Note titles and qualifying row captions used to
    be hand-enumerated in code — a per-dataset heading pattern and a 162-alternative caption
    whitelist that refused a filing writing "Depreciation charge for the year". Both are patterns
    here, so widening one is an edit to the configuration rather than a release.
    """

    note_title_any: list[str] = Field(default_factory=list)
    row_caption_any: list[str] = Field(default_factory=list)
    row_caption_none: list[str] = Field(default_factory=list)
    # THE SEMANTIC HALF, and the two levels are the SAME two levels as the patterns above. That
    # parallel is the whole design: which note, then which rows inside it, are different questions
    # searched against different text, and they need different vocabularies.
    #
    #     note_terms      what the NOTE is about   -> scored against note HEADERS
    #     row_terms       what the ROW is called   -> scored against ROW CAPTIONS
    #     row_terms_none  captions that must not count
    #
    # WHY BOTH LEVELS NEED THEIR OWN TERMS, measured. A note's heading names the CONTAINER and a
    # row names the CONTENT: `sub__ga_depreciation` is disclosed in the note headed 管理费用
    # (administrative expenses) as a row reading 固定资产折旧 (depreciation of fixed assets). A single
    # blended probe scored 0.000 against that heading, because every one of its Han tokens was a
    # depreciation word and none was an administrative-expenses word. One term set cannot do both
    # jobs, and the failure is silent: a wrong vocabulary scores zero exactly like an absent note.
    #
    # THESE ARE TERMS, NOT PATTERNS — plain vocabulary, scored by IDF-weighted cosine
    # (`services.line_item_notes`) rather than matched. So they are not compiled, an unanticipated
    # phrasing still scores instead of not firing, and they carry no regex syntax to get wrong.
    # They are authored in every script the filings print: the tokeniser emits Han character
    # bigrams, so a Chinese term matches a Chinese heading without a segmenter.
    # PROSE NEEDS ITS OWN PATTERNS, and that is measured rather than assumed. A figure a filing
    # states only in a sentence cannot be reached by either of the two artefacts above:
    #
    #   * `row_terms` are too LOOSE. They are `row_caption_any` split on `|`, which turns a
    #     conjunction — "a depreciation word within forty characters of an expense-function word" —
    #     into a disjunction: `depreciation` OR `operating expenses`. Measured, a term-based prose
    #     rule matched any sentence merely mentioning operating expenses and replaced a depreciation
    #     charge of 587,417 with 36,966,000, inventing two more figures on lines that should have
    #     stayed empty.
    #   * `row_caption_any` is too TIGHT. Its proximity bounds are authored for a SHORT caption. In
    #     the reference footnote the gap between "depreciation" and "operating expenses" is 87
    #     characters — 61 even with the amounts stripped — because the sentence puts the figure, the
    #     comparative and a verb phrase in between. Against `.{0,40}` it does not match at all.
    #
    # So a prose pattern is authored for sentence-length text: wider bounds, and the connective the
    # sentence actually uses ("included in", "charged to", 計入). EMPTY MEANS NO PROSE ROUTE for this
    # line, which is why the field is opt-in rather than defaulted — an unauthored line produces no
    # prose figure rather than a guess.
    prose_any: list[str] = Field(default_factory=list)
    note_terms: list[str] = Field(default_factory=list)
    row_terms: list[str] = Field(default_factory=list)
    row_terms_none: list[str] = Field(default_factory=list)
    # WHICH TEXT THESE PATTERNS ARE AUTHORED AGAINST. The shipped patterns were lifted out of
    # code that matched RAW captions, so folding them through `normalize_label` would stop some of
    # them matching. `mapping_v1` says the opposite — author against normalised text, as the
    # rulebook's aliases are. Saying it per pattern group beats guessing.
    caption_normalization: Literal["none", "mapping_v1"] = "none"

    @model_validator(mode="after")
    def _patterns_compile(self):
        _refuse_uncompilable(
            ("note_title_any", self.note_title_any),
            ("row_caption_any", self.row_caption_any),
            ("row_caption_none", self.row_caption_none),
            ("prose_any", self.prose_any),
        )
        return self


def _refuse_uncompilable(*groups: tuple[str, list[str]]) -> None:
    """Every configured pattern must be a valid regex, checked where it is written.

    Not defensive padding. Splitting a shipped 34-alternative regex on ``|`` to seed this config
    tore ``^\\s*at\\s+(?:1|31)`` into ``^\\s*at\\s+(?:1`` and ``31)`` — two fragments that compile
    nowhere and match nothing. Read at extraction time that is a silent hole: the exclusion simply
    stops excluding, and the wrong rows get summed into a figure nobody can trace back. Refusing it
    here means a bad pattern is a load error on a screen that names it, which is the entire reason
    this configuration layer exists.
    """
    bad = []
    for field, values in groups:
        for i, raw in enumerate(values):
            try:
                re.compile(raw)
            except re.error as exc:
                bad.append(f"{field}[{i}] {raw!r}: {exc}")
    if bad:
        raise ValueError("pattern does not compile — " + "; ".join(bad))


class SectionDefaults(BaseModel):
    """What is true of every line item printed under one section banner, authored once.

    THE REASON THE TWO-LAYER MODEL SURVIVED THE MERGE. Measured on the shipped rulebook: 462 of
    462 concepts declare `inherits`, ZERO declare `statement`, and three declare `section_scope`.
    All of the gate arrives from EIGHTEEN entries of this shape, and five of its fields
    (`statement`, `temporality`, `note_use`, `note_use_rationale`, `sign_convention`) are sourced
    nowhere else at all. Flattening that into per-item declarations would be 462 × 6 hand-authored
    values with nothing checking they agree — and `test_template_rulebook_coherence` exists
    because section declarations drifting out of agreement was already a real problem once.

    Every field is optional: a set with no section layer must load exactly as it did before.
    """

    statement: StatementType | None = None
    section_scope: list[str] = Field(default_factory=list)
    scopes: list[SearchScope] | None = None
    side: Side | None = None
    temporality: Temporality | None = None
    unit_of_account: UnitOfAccount | None = None
    note_use: NoteUse | None = None
    note_use_rationale: str | None = None
    sign_convention: SignExpectation | None = None
    match_priority: int | None = None
    face_only: bool | None = None
    analyst_bucket: str | None = None


class LineItemDef(BaseModel):
    """One configured line item — the whole of what used to take a concept plus a definition."""

    key: str
    label: str = ""
    type: LineItemType = "extracted"
    # What this line OUTPUTS — see `OutputStructure`. Defaults to a number, so every existing
    # configuration keeps exactly the meaning it had.
    output_structure: OutputStructure = "value"
    # Display prose, and — separately — the authoritative accounting meaning. `definition` is what
    # the LLM's description-based tier matches a caption against (`OntologyMapping.meaning()`
    # prefers it over `description`), so collapsing the two would either put display copy into a
    # matching decision or hide the meaning from the screen.
    description: str = ""
    definition: str = ""
    # EXTRA INSTRUCTION FOR THIS LINE, sent to the model beside this concept's definition when it
    # is offered as a candidate (`mapping._concept_payload`).
    #
    # APPENDS, NEVER REPLACES. The set-level ``prompt`` and the framework's own base instruction
    # both still apply — a per-line prompt that replaced them would silently drop the global
    # policies (parent/child allocation, the duplicate-fact rule, the totals policy) for whichever
    # captions happened to be offered that concept, and nothing in the output would say so.
    #
    # ONLY MEANINGFUL ON AN ``extracted`` LINE, and the validator below refuses it elsewhere: a
    # calculated or derived line gets its figure from arithmetic, and an ``extraction_mode:
    # "derive"`` concept is never offered to the model at all (`mapping._unmatchable`), so a prompt
    # authored on one would be text that is never sent — the kind of configuration that looks like
    # it is working because nothing contradicts it.
    prompt: str = ""
    # Whether it reaches the statement screens and the export. An intermediate never does; the
    # validator below enforces that rather than trusting whoever edits the file.
    in_output: bool = True
    # The line this one is a part of, and WHAT THAT PARENTHOOD MEANS arithmetically.
    parent: str = ""
    rollup: Rollup = "sum"
    # Sub-line items in the order they should be shown under their parent. Display order only —
    # `match_priority` is the matching tie-break, and conflating them silently reorders matching
    # every time someone drags a row on the screen.
    order: int = 0
    namespace: Namespace = "template"

    # ── the gate: where this line item may be claimed from ───────────────────────────────────
    # `inherits` names a `section_defaults` entry. Folded in by `resolve_line_item_inherits`
    # BEFORE validation, because "declared wins" is a statement about the raw dict: once
    # validated, an inherited value is indistinguishable from an authored one and from a default.
    inherits: str | None = None
    # `None` means claimable on any statement — "nothing was said", not "nothing is allowed".
    statement: StatementType | None = None
    # The section banners this line item may be claimed under. EMPTY MEANS UNCONSTRAINED. The
    # rulebook spells the same idea with a sentinel (`['bs_top_level']` = no banner constrains
    # this concept); an empty list says it without a magic string.
    section_scope: list[str] = Field(default_factory=list)
    # Descending tie-break for the collisions the gate leaves standing — 65 of the 420 measured.
    # Residuals are 0 and unreachable by matching.
    match_priority: int | None = None
    # The lock that makes a line item unreachable by every matching tier while still fillable by
    # the residual sweep. Sixteen concepts rely on it.
    alias_matching: AliasMatching = "enabled"
    extraction_mode: ExtractionMode = "extract"
    # Leaf versus residual bucket. `type` says how a value ARRIVES; this says whether this line
    # item may overlap another, which is a different question and the sweep needs both.
    value_scope: ValueScope = "exclusive_leaf"
    residual_policy: ResidualPolicy | None = None
    expected_components: list[str] = Field(default_factory=list)
    never_sweep: list[str] = Field(default_factory=list)
    # Canonical keys this one is easy to confuse with. Routes an unresolvable pair to review
    # instead of letting the engine pick one at confidence 1.0.
    confusable_with: list[str] = Field(default_factory=list)

    # ── containment: a gross parent and the children it already contains ────────────────────
    # THE LAST DISCRIMINATOR, and leaving it out was measurable. Projecting all 462 concepts
    # through this model with only the gate, label ownership and `match_priority` left 31 caption
    # collisions with no way to tell the claimants apart — and they were all of one shape:
    # `bs_ca__trade_and_other_receivables` against `bs_ca__trade_receivables_gross`,
    # `bs_equity__accum_oth_eqty_rsrv_inc` against `bs_equity__hedging_reserves`. Same statement,
    # same banner, same priority, and one contains the other. `mapping.py` and `map_ontology.py`
    # both read these, so omitting them was not a documentation loss but a correctness one: with
    # nothing to separate a gross parent from its child, the caption resolves to whichever was
    # declared first and the two are then loaded additively — double-counting a figure the filing
    # printed once.
    is_gross_parent: bool = False
    children_if_decomposed: list[str] = Field(default_factory=list)
    # The mirror image: this line item is the child a subtotal collapses to when the face prints
    # the subtotal ALONE. Names the subtotal. Nothing is divided — the whole undifferentiated
    # figure is this child — and the inference must be refused as soon as any sibling is evidenced.
    sole_component_of: str | None = None

    # ── prose the LLM is shown or a reviewer reads ───────────────────────────────────────────
    # Free-form by nature, and carried rather than dropped: `section_disambiguation` is read by
    # `mapping.py` and answers "which of two look-alike captions is this", which is the question
    # the 31 collisions above ask. The rest document a decision someone will otherwise re-litigate.
    decomposition_rule: str | None = None
    others_rule: str | None = None
    section_disambiguation: str | None = None
    derivation: str | None = None
    notes_as_source_rationale: str | None = None

    # ── extracted: how the caption is recognised ─────────────────────────────────────────────
    scopes: list[SearchScope] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    side: Side = "none"
    # Whether a caption printed on the opposite side may fill this line. Off by default: a bare
    # "Cash" caption once resolved to an overdraft because nothing forbade it. On only for an
    # instrument that genuinely appears on both sides — a derivative, a swap, an option — where
    # one note table lists both and the row's own column is the only thing that separates them.
    allow_contra: bool = False
    # ONLY ASK THE MODEL ABOUT THIS LINE WHEN THE FACE PRINTS A NOTE REFERENCE BESIDE IT, and when
    # it does not, the line's value is 0 (a numeric output) or "" (a text one).
    #
    # OFF BY DEFAULT, so every existing configuration keeps exactly the meaning it had. On, it says
    # two things about this line at once:
    #
    #   * The note tag is the EVIDENCE THRESHOLD. A line whose figure is only ever explained by a
    #     note is not answerable from the face caption alone, so a row printed with no note
    #     reference is not worth a provider call — the model would be guessing from the caption.
    #   * ABSENCE IS A FACT, NOT A GAP. Where the filing prints no note reference for such a line,
    #     the line is reported as zero rather than left blank. A blank says "we did not find it";
    #     a zero says "the filing does not disclose it", and for a line that is only ever disclosed
    #     in a note those are different statements.
    #
    # THE ZERO IS UNCONDITIONAL, and that is a deliberate choice with a cost worth stating: it
    # overwrites whatever the deterministic tiers read off the page. A caption tier that matched a
    # figure on a note-less row has that figure replaced by 0. That is the requested behaviour —
    # the note tag is the authority, not the caption — and `stages.note_tag_gate` records the
    # displaced figure in a flag so the substitution is auditable rather than silent.
    #
    # `extraction_mode: extract` ONLY. A derived or derivable line takes its figure from declared
    # arithmetic, so a note reference beside a printed row says nothing about whether that
    # arithmetic should run; `_coherent` refuses the combination rather than ignoring it.
    llm_only_if_note_tagged: bool = False
    # HOW THE NOTES FOR THIS LINE ARE FOUND. `semantic` by default: the line's own meaning scored
    # against each note's header, which needs no pattern per phrasing and degrades to a low score
    # rather than to silence where a regex would simply not fire.
    #
    # MEASURED ON THE TWO REFERENCE FILINGS, against the authored regexes as ground truth (they are
    # what produced every figure in the focus runs, so the notes they match are notes the line
    # really is in) — `scripts/calibrate_line_item_notes.py`:
    #
    #     laisun (English)      41 (line, note) pairs    95.1% found within the top 10
    #     suncreate (Chinese)  120 pairs                  40.0% within the top 10
    #
    # THE GAP IS NOT THE METHOD, IT IS WHAT THE LINES SAY ABOUT THEMSELVES. A header names the
    # CONTAINER and a line item names the CONTENT: `sub__ga_depreciation` belongs in the note headed
    # 管理费用 (administrative expenses) and `sub__ppe_depreciation` in the one headed PROPERTY,
    # PLANT AND EQUIPMENT. Both are correct pairings that score ~0, because nothing in either line's
    # prose names the container it is disclosed inside — the authored regexes carried that knowledge
    # instead. Naming the container in `description` is what closes it, and unlike a regex that text
    # also reaches the model.
    #
    # SO `patterns` IS NOT DEPRECATED. Set it on a line whose disclosure the regexes already pin and
    # whose prose does not yet name it; the two selectors answer the same question and the choice is
    # per line rather than per run.
    note_selection: NoteSelection = "semantic"
    aliases: list[str] = Field(default_factory=list)
    # Per-locale aliases. A single flat list cannot hold the Han half as data: the shipped
    # rulebook carries 473 distinct zh aliases and the matcher folds EVERY locale into one index,
    # so a locale-blind list would either lose them or mix scripts in one field.
    aliases_i18n: dict[str, list[str]] = Field(default_factory=dict)
    pattern: str = ""
    regex_hints: list[str] = Field(default_factory=list)
    keyword_hints: list[str] = Field(default_factory=list)
    # REGEX VETOES, matched against the raw caption. Renamed from `exclude`, which was doing the
    # work of two different fields: the rulebook has `exclude_hints` (regexes that veto a match)
    # AND `exclude` (prose criteria shown to the LLM). Folding prose into a regex-validated list
    # either fails validation or, worse, compiles as an accidental veto.
    exclude_hints: list[str] = Field(default_factory=list)
    include_criteria: list[str] = Field(default_factory=list)
    exclude_criteria: list[str] = Field(default_factory=list)
    note_source: NoteSource | None = None
    # Whether a cited note may be a SOURCE for this line or only evidence for it.
    note_use: NoteUse | None = None
    face_only: bool | None = None
    # NO PER-LINE-ITEM ACCEPT BAR. There was one — `min_confidence_to_auto_accept: float = 0.85`
    # — and it was removed on a deliberate decision rather than wired up, because it was read by
    # NOTHING: not by the ported matcher and not by the incumbent, whose four accept decisions all
    # compare against the global `settings.extraction.auto_accept_confidence` (default 0.80). It
    # carried 0.85 on all 462 projected definitions and 0 of 475 overrode it.
    #
    # Wiring it instead of removing it would have been a policy nobody authored, and a costly one:
    # the shipped per-item default was STRICTER than the live global bar, so enforcement would
    # newly route to review every row scoring between 0.80 and 0.85. A declared control that
    # nothing consults reads as a control, which is the failure this model keeps finding — so it
    # is gone, and the global knob is the one bar. Re-add it only alongside the code that reads it.

    # ── measurement properties ───────────────────────────────────────────────────────────────
    temporality: Temporality | None = None
    unit_of_account: UnitOfAccount | None = None
    # An EXPECTATION, never a transformation — `sign_rule` performs the flip. Distinct again from
    # `Term.sign`, which is arithmetic inside one formula.
    sign_convention: SignExpectation | None = None
    sign_rule: SignRule | None = None
    analyst_bucket: str | None = None

    # ── calculated / intermediate ────────────────────────────────────────────────────────────
    terms: list[Term] = Field(default_factory=list)

    # ── derived ──────────────────────────────────────────────────────────────────────────────
    cascade: list[CascadeRung] = Field(default_factory=list)
    # An in-code implementer for this line, naming what computes it. Kept as an escape hatch and
    # as the alternative the validator below accepts, but the shipped set names one for no line —
    # every derived line there carries a `cascade`, which is the configuration-driven route.
    implemented_by: str = ""

    @model_validator(mode="after")
    def _coherent(self):
        if self.type == "intermediate":
            self.in_output = False
        # A `calculated` LINE MAY NAME NO TERMS, and 33 shipped lines do exactly that.
        #
        # It used to be refused. The reason it is not is that `terms` is one of TWO places the
        # arithmetic can be declared, and for a subtotal the other one is authoritative: the
        # template's `rollup: {op: "sum", children: [...]}`, which all 33 carry with 2 to 34
        # children each. Requiring `terms` as well would put a second copy of the components in the
        # configuration, free to drift from the template that actually evaluates them.
        #
        # `services.line_items.evaluate` reads the absence the same way: no terms, so report what
        # the document supplied and let the template's `check_rollups` compare it against the
        # components. An `intermediate` line is different — it exists only to be an input to
        # something else, appears in no template and no export, so terms are the only place its
        # arithmetic could live and their absence is still an error.
        if self.type == "intermediate" and not self.terms:
            raise ValueError(f"{self.key}: an intermediate line needs at least one term — it "
                             f"appears in no template, so `terms` is the only place its "
                             f"arithmetic can be declared")
        if self.type == "derived" and not (self.cascade or self.implemented_by):
            raise ValueError(f"{self.key}: a derived line needs a cascade or `implemented_by`")
        # A PROMPT ON A LINE NOTHING IS ASKED ABOUT would never be sent. Only an `extracted` line
        # is offered to the model as a candidate for a printed caption; a calculated or
        # intermediate line is arithmetic over other lines, and a derived one is computed. Refused
        # rather than ignored, because text that is stored, shown on the screen and never used is
        # indistinguishable from text that is working.
        # A GENERATED OR LIFTED VALUE CANNOT COME OUT OF ARITHMETIC. `calculated`, `intermediate`
        # and `derived` lines get their figure by summing or computing other lines, and there is no
        # arithmetic that yields a sentence — so the pair is incoherent rather than merely unusual.
        if self.output_structure != "value" and self.type != "extracted":
            raise ValueError(
                f"{self.key}: a `{self.output_structure}` line is text, and a `{self.type}` line "
                f"gets its value from arithmetic — there is no calculation that produces a "
                f"sentence. Set the type to `extracted`, or the output structure back to `value`.")
        # PROSE IS SPECIFIED BY THE PROMPT AND BY NOTHING ELSE. With no prompt there is nothing to
        # generate from, so the line would publish an empty cell for a reason no reader could see.
        if self.output_structure == "prose" and not self.prompt.strip():
            raise ValueError(
                f"{self.key}: a `prose` line is written by the model from this line's own prompt, "
                f"and no prompt is set — there is nothing to generate from. Write the prompt, or "
                f"choose `phrase` to lift the text off the page instead.")
        if self.prompt.strip() and self.type != "extracted":
            raise ValueError(
                f"{self.key}: a prompt is only sent for an `extracted` line, and this one is "
                f"`{self.type}` — its figure comes from arithmetic, so the model is never asked "
                f"about it. Clear the prompt, or change the type if the line is in fact read off "
                f"the page.")
        # BOTH OF THESE ARE ABOUT WHAT THE MODEL IS ASKED, so both are refused on a line nothing
        # asks about. `note_selection` chooses what note context a line's request carries and
        # `llm_only_if_note_tagged` whether a request is spent at all — so on a line with no
        # request they are controls that silently do nothing, which is worse than a message: the
        # author sets one, sees no effect, and has nothing to read.
        #
        # REFUSED ON THE NARROW SET. This once refused on every non-`extract` mode and every
        # `derived` type — 41 concepts — which was too wide, because `extract_or_derive` lines ARE
        # asked about. Dropping the refusal entirely was the over-correction; `_never_asked` now
        # names the three declarations that genuinely withhold a line and this refuses on those.
        never = self._never_asked()
        if never:
            for field in ("note_selection", "llm_only_if_note_tagged"):
                value = getattr(self, field, None)
                if field == "note_selection" and str(value or "semantic") == "semantic":
                    continue            # the default says nothing; only an explicit choice does
                if field == "llm_only_if_note_tagged" and not value:
                    continue
                raise ValueError(
                    f"{self.key}: `{field}` asks what the model is given, and the model is never "
                    f"asked about this line — {never}. Put it on the part whose rung reads that "
                    f"source, or change the declaration that withholds the line.")
        # A `note_source` ON A DERIVED LINE FILLS THE PARENT DIRECTLY AND SKIPS ITS CASCADE.
        # `stages.note_sourced._declared_items` reads every item that declares one, so this would
        # WORK — and working is the bug: the figure arrives on the parent, no rung runs, and the
        # record loses which disclosure it came from. None of the nine shipped derived lines
        # declares one, which is the behaviour this makes explicit rather than lucky.
        if self.note_source is not None and str(getattr(self, "type", "")) == "derived":
            raise ValueError(
                f"{self.key}: `note_source` names where in the notes a figure is read from, and a "
                f"`derived` line is not read — its figure is its declared cascade's. A note source "
                f"here would fill the parent directly and skip every rung. Put it on the PART "
                f"whose rung reads that note.")
        if self.side == "from_section" and not self._can_read_a_section():
            raise ValueError(
                f"{self.key}: `from_section` needs a statement that prints section banners — "
                "put the balance sheet in `scopes`, or name a `section_scope`; there is nowhere "
                "else to read a side from")
        _refuse_uncompilable(("pattern", [self.pattern] if self.pattern else []),
                             ("exclude_hints", self.exclude_hints),
                             ("regex_hints", self.regex_hints))
        if self.sign_rule is not None:
            _refuse_uncompilable(("sign_rule.flip_if_label_matches",
                                  self.sign_rule.flip_if_label_matches))
        return self

    def _can_read_a_section(self) -> bool:
        return ("balance_sheet" in self.scopes
                or self.statement == StatementType.BALANCE_SHEET
                or any(s in SECTION_SIDE for s in self.section_scope))

    def _never_asked(self) -> str | None:
        """Why the model is never asked about this line, or `None` if it is asked.

        THE CONFIGURATION-SIDE SPELLING OF `_llm_withheld` (`services.mapping`), and that set is
        now the SECTION RESIDUALS and nothing else. Every other concept is offered, derived
        parents included, because some of a filing's addition and subtraction is the model's to do
        — it can read four note rows and say they are one subtotal, which no declared cascade
        anticipated.

        A residual is the exception for a reason that is not about arithmetic: its purpose is to
        carry the UNEXPLAINED remainder, so a figure filed there makes the reconciliation that
        would have reported the gap tie instead. `alias_matching: "disabled"` is the declared
        switch that marks one, which is what `mapping._locked` reads.

        THREE DECLARATIONS ANSWER IT, for three different reasons, and the answer NAMES which one
        so an author can act on it:

          * `type: derived` — its figure is its declared cascade's.
          * `alias_matching: disabled` — a section residual, filled by the sweep.
          * `extraction_mode: derive` — the framework computes it; there is no source to locate.

        `extract_or_derive` IS ASKED ABOUT, and that is the boundary that moved. It means "printed
        on some filings, arithmetic on others", and a request asks WHERE a figure is printed rather
        than asking the model to work one out — so a figure sitting in a note is locatable whether
        or not the arithmetic could also reach it.

        THIS ONCE ANSWERED FOR 41 CONCEPTS — every non-`extract` mode and every `derived` type —
        and the two model-facing flags were refused on the strength of it. That set was too WIDE,
        and the correction went too far the other way: the refusals were dropped entirely and this
        was narrowed to the residuals alone, which left `line_item_requests.asked_about` and the
        config screen disagreeing about the same question. One spelling, narrow set, refusals back.
        """
        if str(getattr(self, "type", "")) == "derived":
            return ("this one is `derived`, so its figure is its declared cascade's — a number "
                    "written straight onto the parent skips every rung, which loses which "
                    "disclosure it came from and the cross-check between rungs")
        if str(self.alias_matching) == "disabled":
            return ("this one is a section residual, which carries a section's unexplained "
                    "remainder and is filled by the sweep rather than by any answer")
        if str(getattr(self, "extraction_mode", "")) == "derive":
            return ("this one is `derive`, so the framework computes it and no printed caption "
                    "may claim it — there is no source to locate")
        return None

    # ── the gate, as the matcher will ask it ─────────────────────────────────────────────────
    def claimable_on(self, statement: StatementType | str | None,
                     normalize: Callable[[object], str] | None = None) -> bool:
        """Whether a caption printed on `statement` may be claimed by this line item.

        PERMISSIVE WHEN SILENT. `statement=None` on the definition means nothing was said, so
        every statement is allowed; an unknown statement on the caption side is likewise not a
        reason to refuse, because refusing everything the classifier could not name would delete
        rows rather than mis-file them.

        `normalize` folds both sides into ONE spelling and a caller that gates must pass it. One
        statement has two names in this codebase: the page classifier and every caller say
        `changes_in_equity`, while `StatementType` — which this field validates against — spells
        it `equity_changes`. Compared raw, a definition on that statement is refused on every page
        of it, which is the same failure the `income_statement` token would have caused. Folded
        through `services.mapping.normalize_statement`, they agree.
        """
        if self.statement is None or statement is None:
            return True
        if normalize is None:
            want = statement.value if isinstance(statement, StatementType) else str(statement)
            return self.statement.value == want
        return normalize(self.statement) == normalize(statement)

    def claimable_under(self, section: str | None,
                        resolve: Callable[[str], str | None] | None = None) -> bool:
        """Whether a caption under this section banner may be claimed by this line item.

        `resolve` maps a `section_scope` id to the BANNER TOKEN it names, and passing it is not
        optional for correctness — it is optional only so a caller with compact ids (the shipped
        section layer uses `bs_ca`, `is_pl`, `notes`, which name themselves) need not supply one.

        WHY IT EXISTS. Scope ids and banner tokens are authored for different readers. A scope id
        carries the section's printed position as well as its name (`bs_s4_non_current_liabilities`)
        while a banner names the section itself, so the two cannot be string-compared. And the
        `*_top_level` ids name NO section at all: they hold the statement-level totals, which no
        banner may constrain, because a section hint is the nearest PRECEDING banner and a
        statement total routinely carries the banner of the last section printed above it.

        Compared literally instead, `bs_ca__total_assets` — scoped `['bs_top_level']` — is refused
        under every banner in its own statement. Measured: that alone was 130 of the 133
        disagreements against the incumbent matcher, every one of them a real caption ("total
        assets", "share capital", 资产总计) resolving today and going unmapped after the port.
        """
        if not section:
            return True                      # a statement total sits under no banner
        if resolve is None:
            scopes = {s.strip().lower() for s in self.section_scope}
        else:
            scopes = {tok.strip().lower() for s in self.section_scope if (tok := resolve(s))}
        if not scopes:
            # Either nothing was declared, or everything declared names no section. Both mean
            # unconstrained — "nothing was said", never "no banner allowed".
            return True
        return section.strip().lower() in scopes

    def resolved_side(self, section: str | None) -> Side:
        """The side in force, given the section a caption was actually found under.

        Returns "none" for `from_section` where the section says nothing — a statement total sits
        under no banner — so the caller can report an unanswered side instead of guessing one.
        """
        if self.side != "from_section":
            return self.side
        return SECTION_SIDE.get((section or "").strip().lower(), "none")

    def aliases_for(self, locale: str | None) -> list[str]:
        """Every alias in force for one locale, deduped, order preserved.

        Mirrors `OntologyMapping.aliases_for` exactly, English included as a cross-lingual anchor,
        so the two can be compared field-for-field during the migration.
        """
        out = list(self.aliases)
        if locale and locale in self.aliases_i18n:
            out = out + self.aliases_i18n[locale]
        if "en" in self.aliases_i18n:
            out = out + self.aliases_i18n["en"]
        return list(dict.fromkeys(out))

    def meaning(self) -> str:
        """Best available semantic text for description-based matching."""
        return self.definition or self.description or self.label or self.key


class LineItemSetMetadata(BaseModel):
    name: str = ""
    version: str = ""
    supersedes: str | None = None
    changes: list[str] = Field(default_factory=list)
    breaking_changes: list[str] = Field(default_factory=list)
    # What is true of this set's `vocabulary` block that its own contents cannot say: which engine
    # consults it. Declared as a FIELD rather than left as a JSON key because pydantic ignores
    # unknown keys here, and a note the loader drops would never reach the payload the Line Items
    # screen serves — the same failure `scripts/build_line_items.py:report_caption_characters`
    # exists to shout about.
    #
    # MUST BE PAIRED WITH THE GENERATOR. `build_line_items.py` writes this file's `metadata` block
    # literally, so a note added only to the JSON is reinstated away on the next regeneration.
    vocabulary_note: str = ""


class SectionBanner(BaseModel):
    """One section token and the printed headings that name it, in every language it appears in.

    THIS WAS `mapping.SECTION_WORDS`, a Python tuple. It decides what `section_of_banner` makes of
    a printed heading, and therefore which section gate applies to every row beneath it — so it is
    mapping data of the most consequential kind, and it was unreachable to anyone configuring the
    system. A filing whose balance sheet says "CURRENT ASSETS AND LIABILITIES" or 流動資產淨值
    could not be accommodated without a release.

    ORDER MATTERS AND IS PRESERVED. The list is matched longest-first for a reason the original
    comment states plainly: "non current liabilities" also ends with "current liabilities", and
    reading it as the current-liability section would let a current-liability banner claim every
    non-current concept. The loader keeps declaration order, so a set must declare the longer
    heading first — which `refuse_shadowed_banners` checks rather than trusting.
    """

    token: str
    headings: list[str] = Field(default_factory=list)
    # Whether a row carrying ONLY this heading, with no figures, may declare this section.
    #
    # THIS WAS `mapping.HEADING_ROW_SECTIONS`, and the eight it holds are the unambiguous ones: no
    # line item in a statement is CAPTIONED "Current assets" or "Operating activities", so a row
    # saying only that is a heading. The rest are matched on words that are themselves complete
    # captions — `income` matches "Revenue" and "Turnover", `profit_attributable_to` matches
    # "attributable to" inside "Profit attributable to owners of the parent" — so a row carrying
    # one is far more likely to be an item with no figures than a heading, and treating it as a
    # banner would scope every row below it wrongly.
    heading_row: bool = False


class UmbrellaBanner(BaseModel):
    """A heading that spans MORE THAN ONE section, and therefore scopes nothing.

    THIS WAS AN INLINE CONDITIONAL: `("equity" in folded or "权益" in folded) and ("liabilit" in
    folded or "负债" in folded)`. IFRS statements print "EQUITY AND LIABILITIES" above the Equity,
    Non-current and Current sub-banners, and reading it as the equity section would refuse every
    liability line item beneath it.

    `groups` is a conjunction of disjunctions: at least one word from EVERY group must appear for
    the banner to be umbrella. Two groups of two express the original exactly, and the shape
    generalises to the umbrella headings other frameworks print without another code change.
    """

    id: str = ""
    groups: list[list[str]] = Field(default_factory=list)
    note: str = ""

    def spans_sections(self, folded: str) -> bool:
        return bool(self.groups) and all(any(w in folded for w in group) for group in self.groups)


class ExclusiveVocabulary(BaseModel):
    """Words a caption may name only ONE of, so naming another member refuses the match.

    THIS WAS `mapping.EXCLUSIVE_VOCABULARIES`. IAS 7 divides cash flows into exactly three
    activities and a statement labels each subtotal with its own, so a caption saying "financing
    activities" is not the investing subtotal under any reading. It cannot be left to similarity:
    "Net cash used in investing activities" and "Net cash flows used in financing activities"
    differ by one word in seven, which token similarity scores at 0.92 — above any threshold
    anyone would pick — and the consequence is silent, with the financing figure filed under
    investing and the financing line left empty.

    Declare one only where naming a member genuinely rules out the others for EVERY filing: this
    gate cannot be overridden by evidence, so a merely-usually-true grouping refuses correct
    mappings.
    """

    id: str = ""
    members: list[str] = Field(default_factory=list)
    note: str = ""


class ConceptFamily(BaseModel):
    """Line items a statement prints under ONE caption, differing only in which section they are.

    THIS WAS `mapping.CONCEPT_FAMILIES`, and its own comment said so: "a typed family block on the
    schema is the right home". The banner above the row is the only evidence separating them, so
    when a decision names the right kind of thing and the wrong variant, the banner corrects it to
    the sibling instead of the answer being discarded — and discarding it drops the row to a weaker
    path, which for the P&L bottom line loses the largest figure on the statement (a wrapped
    bilingual "TOTAL COMPREHENSIVE / LOSS FOR THE YEAR" reaches the matcher as the bare fragment
    "LOSS FOR THE YEAR", an alias of the OTHER bottom line).

    A FAMILY IS NOT `confusable_with`, and the two must not be conflated. `confusable_with` is a
    confusion GRAPH whose mutual pairs connect into a single 47-concept component in the shipped
    rulebook — share capital to reserves to NCI to the tax lines to both bottom lines — so
    re-routing anywhere inside it would move an answer between concepts that are different facts.
    A family is a small, closed set of variants of one fact.
    """

    id: str
    siblings: list[str] = Field(default_factory=list)
    note: str = ""


# ── caption normalisation: authorable patterns, and the property that keeps them safe ────────
#
# WHY THESE ARE VOCABULARY. `normalize_label` carries three CAS strippers, and each one exists
# because of a shape a FILING printed: a line enumerator and its 其中：/加：/减： component markers
# (`_CAS_LINE_PREFIX`), the sign-convention parenthetical a mainland caption carries about itself
# (`_CAS_SIGN_NOTE`), and the orphaned tail a wrapped caption leaves on the front of the next one
# (`_CAS_ORPHAN_HEAD`). The middle one was measured on 四创电子 (11077098): twenty-three captions
# carried it and it defeated the match on every one, leaving the whole bottom of the income
# statement unreachable. That is the definition of vocabulary — the next framework's enumerator
# should be an edit, not a release.
#
# WHAT THE ORDER IS AND IS NOT DOING, MEASURED TWICE. `normalize_label` says "ORDER MATTERS
# HERE". Across all six permutations on the eight real mainland captions in
# `scripts/demo_code_vs_config.py`, 0 of 8 change answer, which reads as a refutation — but that
# corpus carries no orphaned wrap fragment, and the fragment is the whole point of the third
# rule. Add the string `_CAS_ORPHAN_HEAD`'s own comment quotes and the answer flips: on
# `填列） 三、营业利润（亏损以“－”号填列）` three of six orders give `营业利润` and three give
# `三、营业利润`, and the same 3/3 split holds on `parity_normalisation.py`'s `cas_stacked` shape.
# 4 of 12 order-sensitive, and the demo's corpus could not see any of them.
#
# SO THERE ARE TWO KINDS OF ORDER-DEPENDENCE, and only one of them is a defect.
#
#   REVELATION — one transform's removal UNBLOCKS another. Both `_CAS_ORPHAN_HEAD` and
#   `_CAS_LINE_PREFIX` are anchored at `^`, so while the orphaned fragment sits in front of the
#   enumerator the prefix rule cannot see it: strip the fragment first and 三、 goes, strip it
#   second and 三、 stays. `normalize_label`'s comment says exactly this ("it sits in FRONT of the
#   enumerator the prefix rule is looking for"). The patterns never contend for a character —
#   measured, their match spans are disjoint on every probe — and the wrong order UNDER-strips.
#   An under-stripped caption keeps more of what the filing printed, so it either matches or it
#   does not; it cannot become a caption the filing never printed.
#
#   OVERLAP — two transforms claim the SAME characters, and the order decides which text is
#   destroyed. Substitute one plausible authored orphan head — `^.*?[）)]\s*`, which is what
#   someone reaching for "drop the fragment up to the first closing bracket" writes — and its span
#   on 减：营业成本（以“-”号填列） is the whole caption, swallowing both the enumerator's span and
#   the sign note's. Three of six orders then ERASE THE CAPTION ENTIRELY and three leave
#   营业成本. What survives depends on the order and is text the filing never printed as a caption.
#
# THE SEVERITIES ARE NOT THE SAME SEVERITY, which is the reason this file refuses one and records
# the other. `parity_normalisation.py` measured the difference on a real perturbation: of 1,050
# moved resolutions, 976 fell to unmatched and 74 landed on a DIFFERENT concept — "an unmatched
# row is swept into its section's residual and itemised under its own label, so the statement
# still ties and a reviewer can see the caption; a row on the wrong specific line also ties and
# looks finished." Revelation can only produce the first. Overlap is how you get the second.
#
# So the invariant is that no two declared transforms OVERLAP. Order-dependence without overlap
# is real, is recorded, and is what the declaration order is for.

_META = set("\\[](){}|?*+^$.")

# The parse walk below reads a compiled pattern's own syntax tree. The module that exposes it was
# renamed in 3.11 (`sre_parse` -> `re._parser`, with the old name kept as a deprecated shim), and
# both are private, so this is tried in order and the whole feature degrades to `_literal_run`
# rather than failing to import.
try:                                          # 3.11+
    import re._parser as _PARSER              # type: ignore[import-not-found]
except ImportError:                           # pragma: no cover - 3.8-3.10
    try:
        import sre_parse as _PARSER           # type: ignore[no-redef]
    except ImportError:                       # pragma: no cover
        _PARSER = None                        # type: ignore[assignment]


@functools.lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    """One compiled object per pattern string, because the order check applies each one a few
    thousand times per load (207 probes × six orders on the shipped three) and `re`'s own cache is
    512 entries shared with everything else in the process."""
    return re.compile(pattern)


def _fold(text: str) -> str:
    """Whitespace-collapsed, exactly as `normalize_label` finishes.

    THE COMPARISON IS MADE HERE AND NOT ON THE RAW SUBSTITUTION RESULT, because `normalize_label`
    collapses runs of whitespace and strips the ends three lines after the CAS rules run — so a
    difference in spacing is not observable by anything downstream and cannot change which line
    item a caption resolves to.

    Measured, the raw comparison manufactures findings whose entire content is a space. On the
    enumerator-plus-sign-note probe ` 一 、  （a号 填列 ）` the shipped rules give `' '` one way and
    `''` the other: `_CAS_LINE_PREFIX`'s trailing `\\s*` consumes the space the sign-note
    substitution left when the prefix runs second, and there is nothing following it to consume
    when the prefix runs first. Folded, both are `''`. A list of order-sensitive probes is only
    worth reading if every line in it is a real one.
    """
    return re.sub(r"\s+", " ", text).strip()


def _literal_run(pattern: str) -> str:
    """The pattern's literal characters, as a LAST RESORT when the parse walk is unavailable.

    Crude on purpose — it keeps character-class contents and quantifier bounds, so what comes back
    usually matches nothing. That is acceptable: a probe no transform matches is inert rather than
    wrong (every order leaves it alone), so this degrades the corpus instead of corrupting it.
    """
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2                            # skip the escape and whatever it escapes
            continue
        if c not in _META:
            out.append(c)
        i += 1
    return "".join(out)


def _emit(seq) -> str:
    """One shortest-ish string for a parsed sub-pattern.

    DISPATCH IS ON THE OPCODE'S NAME, not on the constant, because the constants live in the
    private module that was renamed in 3.11 and importing them by name from either spelling is
    one more thing to get wrong per version. The names have been stable since the module existed.

    Zero-width assertions (`^`, `$`, a lookahead) contribute nothing, which is what makes the
    result a probe rather than a proof: `(?!\\d)` is satisfied by the emitted text or it is not,
    and `_witness_of` checks rather than assumes.
    """
    out: list[str] = []
    for op, av in seq:
        name = str(op)
        if "REPEAT" in name:                  # MAX_REPEAT / MIN_REPEAT / POSSESSIVE_REPEAT
            lo, hi, sub = av
            # One copy for an optional atom, so `[^（(]*?` still contributes a character and the
            # probe exercises the run rather than skipping it. Capped at 4 so a `{1000}` bound
            # cannot turn a probe into a kilobyte.
            reps = min(lo if lo > 0 else 1, hi if isinstance(hi, int) else 4, 4)
            out.append(_emit(sub) * reps)
        elif "NOT_LITERAL" in name:
            out.append("x" if av != ord("x") else "y")
        elif "LITERAL" in name:
            out.append(chr(av))
        elif name == "ANY":
            out.append("x")
        elif name == "IN":
            out.append(_from_class(av))
        elif name == "CATEGORY":
            out.append(_from_category(av))
        elif name == "SUBPATTERN":
            out.append(_emit(av[3]))
        elif name == "ATOMIC_GROUP":
            out.append(_emit(av))
        elif name == "BRANCH":
            out.append(_emit(av[1][0]))       # the first alternative; one witness is enough
        # AT / ASSERT / ASSERT_NOT / GROUPREF and anything a future version adds: zero-width or
        # unrepresentable, so they contribute nothing and the verification step decides.
    return "".join(out)


def _from_category(cat) -> str:
    c = str(cat)
    if "NOT" in c:
        return "-"                            # not a digit, not a space, not a word character
    return "1" if "DIGIT" in c else " " if "SPACE" in c else "a"


def _from_class(items) -> str:
    """One character satisfying a character class, negation included."""
    if items and str(items[0][0]) == "NEGATE":
        banned: set[str] = set()
        for op, av in items[1:]:
            name = str(op)
            if "LITERAL" in name:
                banned.add(chr(av))
            elif name == "RANGE":
                # Bounded: a negated class over a Han block is a 20,000-character range and
                # expanding it to pick one letter would be the slowest line in the file.
                banned.update(chr(c) for c in range(av[0], min(av[1], av[0] + 256) + 1))
        return next((c for c in "abcxyz1 " if c not in banned), "?")
    for op, av in items:
        name = str(op)
        if "LITERAL" in name:
            return chr(av)
        if name == "RANGE":
            return chr(av[0])
        if name == "CATEGORY":
            return _from_category(av)
    return "x"


@functools.lru_cache(maxsize=512)
def _witness_of(pattern: str) -> str:
    """A short string this pattern matches, synthesised FROM THE PATTERN ITSELF, or "".

    WHY THE PROBES ARE DERIVED THIS WAY. A behavioural order check is only as good as the strings
    it runs on, and the two obvious corpora are both wrong on their own. The set's captions are
    stored CLEAN — the rulebook holds 营业利润, never 三、营业利润（亏损以“－”号填列） — so a corpus
    of aliases alone would exercise the identity transform and none of the strippers, which is the
    same trap `parity_normalisation.py` documents at length and answers by synthesising printed
    variants. A hand-written probe list is worse still: it pins whatever its author already thought
    of, and a transform someone adds next year is checked against probes written before it existed.
    Deriving one witness per declared pattern means every transform brings its own probe, so the
    corpus grows with the declaration instead of behind it.

    VERIFIED, NEVER ASSUMED. The walk is a heuristic over a private parse tree; what it returns is
    checked with `search` and discarded for `_literal_run` if it does not match, so a witness the
    generator gets wrong weakens the corpus rather than silently passing the check.
    """
    if _PARSER is not None:
        try:
            candidate = _emit(_PARSER.parse(pattern))
        except Exception:                     # a private parser, so any failure is possible
            candidate = ""
        if candidate and _compiled(pattern).search(candidate):
            return candidate
    return _literal_run(pattern)


def _stride(seq: list[str], cap: int) -> list[str]:
    """At most `cap` items, spread evenly and deterministically over `seq`.

    Evenly rather than the first `cap`, because the shipped set is ordered by statement and the
    first 210 of its 475 items are the balance sheet — the first 32 captions in file order are all
    `bs_nca`. A prefix sample would probe no P&L caption at all, and the CAS sign note is a P&L
    phenomenon: it was measured on twenty-three captions at the bottom of an income statement.
    """
    if len(seq) <= cap:
        return list(seq)
    step = len(seq) / cap
    return [seq[int(i * step)] for i in range(cap)]


def _overlap_message(overlaps: list[str]) -> str:
    """The refusal, naming the transforms and the probe — bounded like the `inherits` one.

    Three shown rather than all of them: one overlapping pattern conflicts on most of the corpus
    at once, so the hundredth line says nothing the first three did not, and a wall of Han probes
    is how a real message gets skimmed past.
    """
    return ("caption transforms OVERLAP, so which text survives depends on the order they happen "
            f"to be declared in — {len(overlaps)} probe(s) affected: " + "; ".join(overlaps[:3])
            + (f" (+{len(overlaps) - 3} more)" if len(overlaps) > 3 else ""))


# How many of the set's own captions reach the corpus. The live filter usually cuts far harder
# than either of these; they exist so a transform that matches EVERY caption (a bare `^` anchor,
# say) cannot turn one model load into a several-second regex run.
_PROBE_CAPTIONS = 200
# Captions that get decorated with each witness. Smaller because the decoration multiplies by
# 2 × the number of transforms, and the decorated probes are the redundant half: a caption that
# already trips a transform is in the corpus undecorated.
_DECORATED_CAPTIONS = 32
# Above this many transforms the full-pipeline sample stops being every permutation. 5! = 120
# orders × a few hundred probes × 5 substitutions each is where a model load starts being felt,
# and the pairwise scan has already covered every two-transform interaction by then.
_PERMUTE_UP_TO = 4


class CaptionTransform(BaseModel):
    """One caption-normalisation substitution, declared rather than compiled in.

    `id` is not decoration: it is what the overlap refusal names, and a message saying "the second
    and third patterns overlap" sends a reviewer counting list entries in a JSON file.

    THE THREE SHIPPED CAS RULES NEED EXACTLY THESE FIELDS, which is why there are no others:
    `_CAS_SIGN_NOTE` replaces EVERY occurrence with a space (a wrap merge glues two captions and
    two sign notes onto one line), `_CAS_ORPHAN_HEAD` replaces the FIRST with nothing, and
    `_CAS_LINE_PREFIX` replaces the first with nothing UP TO THREE TIMES because a continuation
    line can carry an enumerator and a component marker at once.

    `passes` is the numeric half of a threshold, and the split is the one this codebase makes
    everywhere: the NUMBER is config, the fact that the loop is bounded at all is code. An
    unbounded fixed-point loop over an authored pattern is a caption of nothing but markers away
    from spinning, so `apply` iterates to a fixed point and stops.
    """

    id: str
    pattern: str
    # A SPACE, NOT THE EMPTY STRING, because the failure mode of "" is silent: dropping a stripped
    # middle glues its neighbours into a token neither caption carried. Five of the seven
    # substitutions in `normalize_label` use a space for exactly that reason; the two that use ""
    # are the two anchored at the front, where the leading space would be stripped anyway.
    replacement: str = " "
    # `re.sub`'s own convention — 0 is every occurrence — so a reader who knows the stdlib knows
    # this field, and a reader who does not looks the right thing up.
    count: int = Field(default=0, ge=0)
    # Bounded at 8: the worst real caption in the corpus (`cas_stacked` in
    # `parity_normalisation.py` — an orphan fragment, an enumerator and a component marker on one
    # wrapped line) reaches its fixed point in two, and the shipped loop bounds at three. A
    # pattern still moving after eight is consuming one character per pass, which is a different
    # bug and should be read as one.
    passes: int = Field(default=1, ge=1, le=8)
    note: str = ""                            # why this shape appears in a filing, for a reviewer

    @model_validator(mode="after")
    def _named_and_compilable(self):
        if not self.id.strip():
            raise ValueError("a caption transform needs an `id` — it is what the overlap "
                             "refusal names")
        _refuse_uncompilable((f"caption_transforms[{self.id}].pattern", [self.pattern]))
        return self

    def apply(self, text: str) -> str:
        """This transform alone, run to a fixed point within `passes`."""
        pattern = _compiled(self.pattern)
        for _ in range(self.passes):
            folded = pattern.sub(self.replacement, text, count=self.count)
            if folded == text:
                break
            text = folded
        return text

    def matches(self, text: str) -> bool:
        return bool(_compiled(self.pattern).search(text))

    def spans(self, text: str) -> list[tuple[int, int]]:
        """The character ranges ONE PASS of this transform would replace.

        The overlap test is a comparison of these, and it has to honour `count` to mean anything:
        `_CAS_ORPHAN_HEAD` replaces the FIRST match only, so a second match further along the
        string is not a range it claims and an intersection there would be a phantom conflict.

        ZERO-WIDTH MATCHES ARE DROPPED. A pattern that can match the empty string (`\\s*` alone,
        or a group every branch of which is optional) reports a match at every position while
        replacing nothing, so counting those as claimed characters would make every such transform
        overlap everything, refuse the set, and name a conflict over text neither pattern touches.
        """
        out: list[tuple[int, int]] = []
        for m in _compiled(self.pattern).finditer(text):
            if m.start() != m.end():
                out.append((m.start(), m.end()))
                if self.count and len(out) >= self.count:
                    break
        return out

    def witness(self) -> str:
        """A string this pattern matches, built from the pattern — see `_witness_of`."""
        return _witness_of(self.pattern)


class PatternOverlap(ValueError):
    """Two declared caption transforms claim the same characters, so the order decides the fold."""


def _contended(a: CaptionTransform, b: CaptionTransform, probe: str) -> str:
    """The characters both transforms would replace, on `probe` or on either's output. "" if none.

    The INTERSECTION of the two spans and not their union, because this string is the evidence
    printed after "both claim" in the refusal, and the union is text one of them merely surrounds.

    THREE STATES, NOT ONE, because a pair can be disjoint on the input and contend on what one of
    them leaves behind — the shipped `_CAS_LINE_PREFIX` matches nothing at all until the orphaned
    fragment in front of it is gone, so judging that pair on the probe alone would be reading a
    transform with no spans at all and concluding whatever it liked from that.

    THE KNOWN GAP, stated rather than papered over: mid-`passes` states are not walked, so a pair
    that first collides on a transform's second or third pass is classified as a revelation and
    lands in `order_sensitive_probes` instead of being refused. It is detected either way — the
    disagreement is what triggered this call — so it is visible and not silent. Reaching that gap
    takes one transform manufacturing the other's material out of its own output, which no shipped
    or plausible authored rule does.
    """
    for state in (probe, a.apply(probe), b.apply(probe)):
        for s1, e1 in a.spans(state):
            for s2, e2 in b.spans(state):
                if s1 < e2 and s2 < e1:
                    return state[max(s1, s2):min(e1, e2)]
    return ""


class TransformOrderCheck(BaseModel):
    """What running the declared transforms in several orders found, sorted by what it costs.

    `overlaps` refuse the set; `revelations` do not. The split is the whole content of this model
    and `transform_order_check` argues it: overlap destroys text the filing printed and what
    survives is a caption it never printed, while a revelation only under-strips. Kept as two
    lists of message strings rather than a structured pair because every consumer of them —
    the refusal, a screen, a build script — wants the sentence, and the sentence has to name the
    two transforms and the probe or it is not actionable.
    """

    overlaps: list[str] = Field(default_factory=list)
    revelations: list[str] = Field(default_factory=list)


class MappingVocabulary(BaseModel):
    """The vocabularies the MATCHER runs on, which used to be Python constants.

    These decide which line item a caption resolves to, or whether it resolves at all — on the
    engine that reads them. None of it was reachable to anyone configuring the system, which is the
    whole objection: a reviewer could read all 475 definitions and still not know why a row landed
    where it did.

    WHICH ENGINE READS THEM. The six SCOPING blocks below — `section_banners`,
    `umbrella_banners`, `scope_tokens`, `statement_prefixes`, `statement_spellings`,
    `exclusive_vocabularies`, 37 declarations in the shipped set — are read by
    `services.line_item_matching.Vocabulary`, constructed from `LineItemMatcher.__init__`. That
    is THE matcher: line items is the single configuration engine, so these six decide the
    answers, and what a reviewer reads here is what the run did.

    WHAT THE PARAGRAPH HERE USED TO SAY, so nobody reinstates it: it said these six decided
    NOTHING, because `LineItemMatcher` was reached only from `stages/map_ontology.py` under
    `extraction.mapping_engine == "line_items"` while `config.py` shipped `"ontology"` — the
    answers came instead from `services.mapping`'s module constants (`SECTION_WORDS`,
    `_COMPACT_SECTION_TOKENS`, `_STATEMENT_OF_PREFIX`, `_STATEMENT_SPELLINGS`,
    `EXCLUSIVE_VOCABULARIES`, and the umbrella rule inside `section_of_banner`), which many call
    sites read with no set in hand. That made this block a MIRROR of live code. The engine switch
    is being removed, so the mirror becomes the original; those module constants are the copy with
    no configured source, and `tests/test_buckets_vocabulary.py` pins 16 of the 37 (`scope_tokens`
    12, `statement_prefixes` 4) equal to them precisely so a third spelling arriving through a
    stale seed cannot hide behind the fall-back-to-code behaviour below.

    TWO EXCEPTIONS. The first is not a scoping read: `section_banners` IS walked on every load, by
    `refuse_shadowed_banners` (below, 1264-1285), which refuses a set whose longer heading is
    declared after one containing it — self-consistency, so the declaration order is checked even
    where nothing consults the declaration. The second is real and is not one of the six:
    `caption_characters` is installed into `mapping` at startup by `services.line_item_config`, so
    it is live on BOTH engines. `caption_transforms` is read by nothing yet and says so at its own
    field.

    EMPTY MEANS "USE THE BUILT-IN", not "no vocabulary". A set that declares none of this must
    behave exactly as it did before these fields existed — otherwise adding the block to the
    schema would silently change every existing rulebook's answers.
    """

    # banner heading -> section token. Order-sensitive; longest heading first.
    section_banners: list[SectionBanner] = Field(default_factory=list)
    # Headings that span more than one section and so scope nothing, tested BEFORE the banners.
    umbrella_banners: list[UmbrellaBanner] = Field(default_factory=list)
    # `section_scope` id -> section token, for a template using compact ids ("bs_ca") rather than
    # descriptive ones ("bs_s1_current_assets"). Both must resolve through one vocabulary.
    scope_tokens: dict[str, str] = Field(default_factory=dict)
    # key namespace prefix -> statement. The FALLBACK reading of which statement a line item is
    # on, for a definition that declares none.
    statement_prefixes: dict[str, str] = Field(default_factory=dict)
    # One statement, two names. The classifier says `changes_in_equity`; `StatementType` spells it
    # `equity_changes`. Both sides fold through this before comparison, because a declaration the
    # gate cannot compare to the classifier's verdict refuses every line item in that statement on
    # every page of it.
    statement_spellings: dict[str, str] = Field(default_factory=dict)
    exclusive_vocabularies: list[ExclusiveVocabulary] = Field(default_factory=list)
    families: list[ConceptFamily] = Field(default_factory=list)
    # A hard-coded correction of a line item's declared section, which `mapping` held as
    # `_KEY_SECTION_OVERRIDES`. Carried for completeness, but the projection now writes the
    # correction straight into `section_scope`, so a merged set should declare none.
    section_overrides: dict[str, str] = Field(default_factory=dict)
    # The caption-normalisation substitutions, in the order they are applied. Declaration order IS
    # the application order and it is load-bearing for the reason `normalize_label`'s own comment
    # gives — an anchored rule cannot see past what is still in front of it — so the ordering
    # freedom `refuse_overlapping_transforms` buys is not "any order works", it is "no order
    # destroys text another rule was going to read".
    #
    # EMPTY MEANS THE BUILT-IN, as everywhere else in this class: a set declaring none folds
    # captions through `mapping.normalize_label`'s own chain, exactly as every shipped set does
    # today. NOTHING READS THIS YET — `normalize_label` still holds the three CAS rules as module
    # constants, and wiring it to prefer a declaration is a change to `services.mapping` held by
    # `scripts/parity_normalisation.py`, which pins the fold of 22,000 captions and is the only
    # thing that can tell a migration from a regression. Declared config that nothing consults is
    # normally worse than none — it reads as a control — so what this field earns before that
    # wiring lands is the validator below: the span-disjointness these three patterns have by
    # authorship becomes a property an authored set is REFUSED for lacking, which had to exist
    # before the patterns could leave code at all.
    caption_transforms: list[CaptionTransform] = Field(default_factory=list)
    # THE CHARACTER INVENTORIES the caption patterns are BUILT FROM — brackets, quote marks, the
    # note words, colon marks, digit and Han code-point ranges, the CAS enumerators, delimiters,
    # component markers and sign-note words. `mapping.build_caption_patterns` consumes exactly this
    # shape and falls back to `mapping._BUILTIN_CAPTION_INVENTORY` ENTRY BY ENTRY, so a set
    # declaring one key keeps the built-in for the other twelve.
    #
    # THIS IS THE HALF THAT IS SAFE TO AUTHOR, and the distinction is the whole conclusion of the
    # boundary work. The pattern SHAPE — the `^` anchors, the `[^（(]` non-crossing classes, "both
    # ends must carry a quote", the digit caps — is a protective skeleton, and exposing a
    # `pattern: str` lets one edit satisfy the vocabulary half and destroy the skeleton on the same
    # line. Exposing the inventory instead cannot: a new quote mark or bracket width is additive
    # and the skeleton is unreachable.
    #
    # It is what a filing forces. Measured: with `_ABBREV_GLOSS` disabled, 1,050 of 1,993 rulebook
    # captions resolve differently and 74 land on a DIFFERENT concept — and a filing that glosses
    # with ＂ or ﹁﹂ rather than the twelve marks below gets exactly that outcome today, with
    # nowhere in 475 definitions to say those marks count.
    caption_characters: dict[str, list] = Field(default_factory=dict)

    def apply_caption_transforms(self, text: str) -> str:
        """Every declared transform, in declaration order. Untouched when none are declared.

        One implementation, so a consumer never grows a second one — the mistake
        `line_item_matching` opens its module docstring by refusing ("NORMALISATION IS IMPORTED,
        NEVER REIMPLEMENTED"), because two engines reading two folds agree on nothing useful.
        """
        for transform in self.caption_transforms:
            text = transform.apply(text)
        return text

    def transform_probes(self, captions: Iterable[str] = ()) -> list[str]:
        """The strings the order check runs on, MOST DIAGNOSTIC FIRST.

        Ordered deliberately, because a message shows the first probe that failed and a real alias
        out of the set is a sentence a reviewer can act on where `'（a号 填列 ）x） '` is not.

          1. The set's own captions THAT A TRANSFORM ALREADY MATCHES. A caption no transform
             matches is provably order-free — every transform is a no-op on it, so the string
             never changes and no later transform can start matching either — which makes this
             filter a proof rather than a sample.

             MEASURED, THIS KEEPS NONE OF THEM: 0 of the 2,006 caption strings in the shipped
             set (and 0 of the ontology's 1,993) is matched by any of the three CAS patterns,
             because a rulebook stores what a caption IS and every one of these patterns exists
             for what a filing PRINTS around it. That number is the whole argument for the other
             two sources — a corpus of stored aliases would exercise the identity transform and
             nothing else, which is the trap `parity_normalisation.py` documents at length and
             answers the same way, by synthesising the printed form.
          2. Those same captions WEARING each transform's witness, front and back. The printed
             shape the clean rulebook never holds: a wrapped caption arrives with the previous
             one's tail glued to its head. Also where a caption's OWN brackets meet a
             bracket-hunting pattern — the case `_CAS_ORPHAN_HEAD` calls out by name
             ("Profit/(loss) before tax" must survive).
          3. The witnesses themselves and every ordered pairing of them, spaced and unspaced. The
             only probes a bare vocabulary has, and — since a witness is by construction the
             material its pattern claims — the ones that collide wherever two patterns do.
        """
        transforms = self.caption_transforms
        if not transforms:
            return []
        witnesses = [w for w in (t.witness() for t in transforms) if w]
        clean = list(dict.fromkeys(c.strip() for c in captions if c and c.strip()))

        probes = _stride([c for c in clean if any(t.matches(c) for t in transforms)],
                         _PROBE_CAPTIONS)
        probes += [side for caption in _stride(clean, _DECORATED_CAPTIONS)
                   for w in witnesses for side in (w + caption, caption + w)]
        probes += witnesses
        probes += [glue.join(pair) for pair in itertools.permutations(witnesses, 2)
                   for glue in ("", " ")]
        return list(dict.fromkeys(p for p in probes if p))

    def _order_samples(self) -> list[tuple[int, ...]]:
        """Which whole-pipeline orders get compared.

        Every permutation while that is cheap. Beyond `_PERMUTE_UP_TO` it becomes the declared
        order, its reverse, and each single adjacent swap — a sample, and said to be one. It is
        not the load-bearing half of the check: the pairwise scan below covers every two-transform
        interaction exhaustively, and this exists for a three-way one, where A's output makes B
        match something C would otherwise have taken.
        """
        n = len(self.caption_transforms)
        base = tuple(range(n))
        if n <= _PERMUTE_UP_TO:
            return list(itertools.permutations(base))
        orders = [base, base[::-1]]
        for i in range(n - 1):
            swapped = list(base)
            swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
            orders.append(tuple(swapped))
        return list(dict.fromkeys(orders))

    def _in_order(self, order: tuple[int, ...], text: str) -> str:
        for i in order:
            text = self.caption_transforms[i].apply(text)
        return text

    def transform_order_check(self, captions: Iterable[str] = ()) -> TransformOrderCheck:
        """Run the declared transforms in several orders and sort the disagreements into two.

        BEHAVIOURAL, NOT SYNTACTIC. Deciding regex disjointness statically is undecidable in
        general, and an attempt at it would be a guess wearing the clothes of a proof — refusing
        sound configurations and passing unsound ones. So the transforms are RUN and disagreement
        is the evidence. That is sound in the direction that matters: a reported conflict is a real
        pair of strings the fold produced, never a false alarm. It is a floor and not a
        certificate in the other direction — a corpus can miss an overlap — which is why
        `transform_probes` derives probes from the patterns rather than from a list someone
        maintains and forgets to extend.

        WHY A DISAGREEMENT IS NOT AUTOMATICALLY A DEFECT. Measured, the three shipped CAS rules
        disagree across orders on any probe carrying an orphaned wrap fragment, and they must:
        both `_CAS_ORPHAN_HEAD` and `_CAS_LINE_PREFIX` are anchored at `^`, so the fragment hides
        the enumerator until it is stripped. Their match SPANS never intersect. Refusing that
        would refuse the very patterns this field exists to hold, and would be refusing a
        strictly-less-stripped caption — which loses a match and lands the row in the section
        residual under its own label, where a reviewer sees it. `spans` intersecting is the other
        thing: two patterns claiming the same characters, where the order decides which text is
        DESTROYED and what survives is a caption the filing never printed. That is how a figure
        reaches a line item that has nothing to do with it, with every subtotal still tying.

        THE PAIRWISE SCAN COMES FIRST because it is the only one that names the culprits.
        Functions that commute pairwise compose to the same result under every permutation, so a
        non-commuting pair IS the finding; the whole-pipeline sample is consulted only when no
        pair can be blamed, and then it names two orders instead.
        """
        transforms = self.caption_transforms
        report = TransformOrderCheck()
        if len(transforms) < 2:
            return report                     # one transform has no order to be dependent on

        for probe in self.transform_probes(captions):
            pair = next(
                ((a, b, ab, ba) for a, b in itertools.combinations(transforms, 2)
                 if (ab := _fold(b.apply(a.apply(probe))))
                 != (ba := _fold(a.apply(b.apply(probe))))),
                None)
            if pair is not None:
                a, b, ab, ba = pair
                claimed = _contended(a, b, probe)
                where = (f"{a.id!r} and {b.id!r}" if claimed
                         else f"{a.id!r} then {b.id!r}")
                line = (f"{where} disagree on {probe!r}: {a.id}->{b.id} gives {ab!r}, "
                        f"{b.id}->{a.id} gives {ba!r}")
                if claimed:
                    report.overlaps.append(line + f" — both claim {claimed!r}")
                else:
                    report.revelations.append(line)
                continue                      # one blamed pair per probe says enough
            results: dict[str, tuple[int, ...]] = {}
            for order in self._order_samples():
                results.setdefault(_fold(self._in_order(order, probe)), order)
            if len(results) > 1:
                (r1, o1), (r2, o2) = list(results.items())[:2]
                names = lambda o: " -> ".join(transforms[i].id for i in o)  # noqa: E731
                # No pair to blame, so no pair's spans to compare. Recorded rather than refused:
                # the evidence available says "three of these interact", which is not evidence
                # that any two of them destroy each other's text.
                report.revelations.append(
                    f"no single pair is to blame but the pipeline is order-dependent on {probe!r}: "
                    f"{names(o1)} gives {r1!r}, {names(o2)} gives {r2!r}")
        return report

    @model_validator(mode="after")
    def refuse_overlapping_transforms(self):
        """Declared transforms that claim the same characters are refused.

        WHAT THIS IS INSTEAD OF. The claim it replaces was that the ORDER of the CAS transforms
        had to stay in code because reordering them changes the answer. Reordering them does change
        the answer — 4 of 12 probes, once the corpus carries an orphaned wrap fragment — but the
        order is not what protects anything, because it protects nothing once someone can author
        the patterns. Swap in a plausible orphan head (`^.*?[）)]\\s*`), keep the shipped order, and
        the caption is erased anyway. What separates the shipped rules from that one is not their
        order, it is that their match spans do not intersect, and that is the property held here.

        REFUSED AT THE DOOR rather than reported, for the reason `_refuse_uncompilable` gives one
        class up. An erased or half-eaten caption maps to nothing or to the wrong thing, the row
        is swept into its section's residual and itemised under its own label, and the section
        still ties — so there is no broken subtotal for anyone to notice, and the only place this
        can be caught is where the pattern is written.
        """
        ids = [t.id for t in self.caption_transforms]
        if len(set(ids)) != len(ids):
            # Checked here rather than passed over because every message this validator can emit
            # identifies a transform by its id, and two transforms sharing one leaves a refusal
            # naming a pattern the reader cannot find.
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise PatternOverlap(
                f"caption transform ids must be unique — {', '.join(map(repr, dupes))} "
                f"declared more than once, and every refusal here names transforms by id")
        overlaps = self.transform_order_check().overlaps
        if overlaps:
            raise PatternOverlap(_overlap_message(overlaps))
        return self

    @model_validator(mode="after")
    def refuse_shadowed_banners(self):
        """A longer heading declared AFTER one it contains can never match.

        Longest-first is the rule and declaration order is how this file expresses it, so an author
        who writes "current liabilities" above "non current liabilities" has silently disabled the
        second — every non-current banner would read as current, and every non-current concept
        would be claimable under it. Refused here because the symptom is a figure on the wrong line
        of a balance sheet that still ties.
        """
        seen: list[str] = []
        for banner in self.section_banners:
            for heading in banner.headings:
                low = heading.strip().lower()
                for earlier in seen:
                    if earlier != low and earlier in low:
                        raise ValueError(
                            f"banner heading {heading!r} contains {earlier!r}, which is declared "
                            f"earlier and would always match first — declare the longer heading "
                            f"before the shorter one")
                seen.append(low)
        return self


class RequestGroup(BaseModel):
    """Line items an author has declared should share ONE model request.

    A GROUP IS A RELATIONSHIP BETWEEN LINE ITEMS, so it cannot be a field on one of them. A
    per-item "group name" would let two items disagree about which group they are in, and there
    would be no single place to read the grouping off — the same reason `prompt` and
    `section_defaults` are set-level rather than repeated per item.

    WHAT IT IS FOR. `extraction.llm_request_grouping` can group line items by the note set they
    computed ("identical"/"similar"), which is cheap and mechanical. This is the fourth mode: the
    author says which lines belong together, for the cases a score cannot see — a subtotal better
    judged beside the lines it is made of, or two lines whose DISTINCTION is the thing the model
    keeps getting wrong and is best asked about once.
    """

    model_config = ConfigDict(extra="forbid")

    # Names the group in the run log and on the screen. An authored name is the point:
    # "Depreciation, one note" in a log is a decision a reader can check, "group 3" is not.
    name: str = ""
    members: list[str] = Field(default_factory=list)
    # Why these belong together, for the next author. Nothing matches on it.
    note: str = ""


class LineItemSet(BaseModel):
    """A whole set of definitions, with the things that are true of the set rather than an item.

    THE SEED USED TO BE A BARE JSON ARRAY, which left nowhere to say the one thing the ontology
    says at its own door: which template these keys bind to. `OntologyDefinition` requires
    `target_template_key` and checks every canonical_key against that template at BOTH doors, and
    an array cannot carry that — so the check could not be copied and the gate had to be designed
    instead. `namespace` on each item is the other half: 13 of the shipped sub-line items are
    deliberately off-template.
    """

    schema_version: int = 1
    line_items_key: str = ""
    target_template_key: str = ""
    target_template_version: int | None = None
    locale: str = "en"
    supported_locales: list[str] = Field(default_factory=lambda: ["en"])
    metadata: LineItemSetMetadata = Field(default_factory=LineItemSetMetadata)
    section_defaults: dict[str, SectionDefaults] = Field(default_factory=dict)
    # The vocabularies the matcher runs on — banners, scope tokens, statement spellings, exclusive
    # classes, families. Formerly Python constants in `services.mapping`.
    vocabulary: MappingVocabulary = Field(default_factory=MappingVocabulary)
    # Families whose siblings this set does not define. Populated at load, never authored: a
    # dangling family is inert, and inert config that LOOKS declared is worse than none.
    dangling_families: dict[str, list[str]] = Field(default_factory=dict, exclude=True)
    # Probes on which the declared caption transforms' ORDER changes the fold without any two of
    # them claiming the same characters. Populated at load, never authored, same reason as above:
    # this is order-dependence that is real, is not a defect, and is invisible to everything else.
    order_sensitive_probes: list[str] = Field(default_factory=list, exclude=True)
    # --- THE FRAMEWORK LAYER ------------------------------------------------------------------
    # Everything from here to `items` is true of the SET, not of one definition, and is what the
    # pipeline reads through the working view built off this set. These blocks used to live only
    # on the ontology JSON, which is why the comments below used to say they governed nothing: a
    # set carried them for projection fidelity while `extraction.mapping_engine` shipped
    # `"ontology"` and every run went through the rulebook object instead. That switch is what is
    # being removed — line items is the single configuration engine, so a block declared HERE is
    # the one the run uses, and there is no second copy to prefer.
    #
    # ABSENT OR EMPTY DECLARES NOTHING. None of these fall back to a built-in framework: an
    # omitted `binding` says no binding rules were stated, not "use the ones compiled into the
    # code". This is the same rule as the gate fields at the top of this module and it is the
    # reason a user can turn a behaviour OFF from configuration at all.

    # Caption/figure normalisation — the folds applied before a caption is looked up at all.
    normalisation: Normalisation | None = None
    # How a resolved figure binds to a column/period/entity.
    binding: Binding | None = None
    # Set-wide rules that are not per-item: the only one of the four with a non-None default,
    # because `GlobalRules`' own field defaults ARE the declaration when the block is absent.
    # THE MASTER PROMPT. Appended to the framework's base instruction for every mapping call, so
    # this is where a deployment states how it wants captions read — before any per-line prompt.
    #
    # WHY IT IS APPENDED RATHER THAN A REPLACEMENT. The base instruction carries the contract the
    # rest of the system depends on: map to exactly ONE concept, reference item_id, never output
    # values, return an empty key when nothing fits. A configuration that could replace it could
    # produce a reply the parser refuses, and the failure would arrive as "the model returned
    # nothing usable" rather than as a configuration error. So this widens the instruction and
    # cannot break its shape.
    #
    # EMPTY MEANS NOTHING IS ADDED — it does not restore some built-in wording, in keeping with
    # every other block here.
    prompt: str = ""
    # THE MANUAL GROUPING MASTER — read only when `extraction.llm_request_grouping` is "manual".
    # Empty is the normal state of one being built and is not an error: the master says which lines
    # SHARE a request, never which lines GET one, so an unnamed line still gets its own
    # (`services.line_item_requests._manual_plans`).
    request_groups: list[RequestGroup] = Field(default_factory=list)
    global_rules: GlobalRules = Field(default_factory=GlobalRules)
    # Which pages/statements the run is allowed to search in the first place.
    scope_selection: ScopeSelection | None = None

    # The rest of the framework layer, carried so the working view is COMPLETE even though the
    # shipped set declares none of them — an undeclared block must be a real, empty declaration
    # the set owns, not a hole the working view has to go somewhere else to fill.
    decomposition_rules: list[DecompositionRule] = Field(default_factory=list)
    # Face-line containment netting (e.g. cost of sales stated inclusive of admin / S&M).
    netting_rules: list[NettingRule] = Field(default_factory=list)
    worked_examples: list[WorkedExample] = Field(default_factory=list)
    validation: ValidationRules | None = None
    # Per-locale digit/sign/bracket conventions. `{"en": NumberFormat()}` is not a fallback: it is
    # the declaration that the one supported locale uses the plain convention, matching the
    # default `supported_locales` above.
    number_format_by_locale: dict[str, NumberFormat] = Field(
        default_factory=lambda: {"en": NumberFormat()}
    )

    # The one definition of every `exclusive_residual` line item — what may be swept, how it is
    # itemised, how the section must reconcile, what is forbidden outright. Authored, not derived,
    # and read off THIS set: with line items as the single configuration engine the residual sweep
    # takes its framework from here, so this is the artefact `residual_policy` on an item is a
    # per-item override OF. (The comment this replaces said the block governed nothing and named
    # `stages/residual.py`, `structural_checks.py` and `rulebook_rules.py` reading
    # `ctx.ontology.residual_framework` instead — that second copy is what is going away, and the
    # copy here is the survivor, not the mirror.)
    #
    # NOT `exclude=True` like the two inert fields above, because this one is not populated at
    # load — it is authored, `residual_policy` is in `ontology_projection.SAME`, and a per-item
    # policy dumped with `exclude_unset` only means anything against a framework that survives the
    # round trip. Dropping it is not an option either: this model declares no `model_config`, so
    # pydantic's default `extra="ignore"` would swallow the block silently on the next load — the
    # same reason every block above is declared here explicitly rather than left to `extra`.
    residual_framework: ResidualFramework | None = None
    items: list[LineItemDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _request_groups_are_answerable(self):
        """A declared request group must name lines that exist, once each, and that get asked.

        THREE REFUSALS, each excluding a failure the others would swallow:

          * AN UNKNOWN MEMBER. A typo would silently shrink the group, so an author would see a
            group of four asking about three with nothing saying which one was dropped.
          * A KEY IN TWO GROUPS. Both requests would claim the same line and the second answer
            would overwrite the first, with nothing recording that a contest happened.
          * A MEMBER THE MODEL IS NEVER ASKED ABOUT. A derived parent's figure is its declared
            cascade's and a residual bucket's is the sweep's, so a group holding either carries a
            line no request can answer. Refused rather than dropped, for the reason
            `llm_only_if_note_tagged` is refused rather than ignored: a member that silently does
            nothing is worse than a message.

        EMPTY IS NOT AN ERROR. It is the state of every set today, and "manual" over an empty
        master degrades to one request per line item, which is what "none" does.
        """
        if not self.request_groups:
            return self
        by_key = {i.key: i for i in self.items}
        seen: dict[str, str] = {}
        for group in self.request_groups:
            named = group.name or "(unnamed)"
            unknown = [m for m in group.members if m not in by_key]
            if unknown:
                raise ValueError(
                    f"request group {named!r} names line items that do not exist: {unknown}")
            for member in group.members:
                if member in seen:
                    raise ValueError(
                        f"{member} is in two request groups ({seen[member]!r} and {named!r}); a "
                        f"line item shares exactly one request")
                seen[member] = named
                item = by_key[member]
                if str(item.type) == "derived":
                    raise ValueError(
                        f"request group {named!r} names {member}, which is `derived` — its figure "
                        f"comes from its declared cascade and the model is never asked about it. "
                        f"Name one of its parts instead.")
                if str(item.value_scope) == "exclusive_residual":
                    raise ValueError(
                        f"request group {named!r} names {member}, which is a section residual — it "
                        f"carries the unexplained remainder and is filled by the sweep, not by an "
                        f"answer.")
        return self

    @model_validator(mode="after")
    def _record_dangling_families(self):
        """A family whose siblings this set does not define is INERT, and says so out loud.

        Not an error, because a set may legitimately carry a vocabulary written for a wider
        key-space. But silence here produced exactly the failure this whole exercise is against:
        the nine families lifted out of `mapping.CONCEPT_FAMILIES` name keys like
        `bs_current_liabilities__current_lease_liabilities`, which belong to the HKFRS rulebook —
        the `output_csv_hk` set defines NONE of them, so all nine families resolved to nothing
        while looking, on the screen and in the file, exactly like working configuration.

        Recording it means `scripts/build_line_items.py` can refuse to emit an inert family and a
        reader can see which ones do nothing, instead of both believing a re-route exists.
        """
        known = {d.key for d in self.items}
        if not known:
            return self                     # nothing to check against yet
        self.dangling_families = {
            f.id: [s for s in f.siblings if s not in known]
            for f in self.vocabulary.families
            if any(s not in known for s in f.siblings)
        }
        return self

    def live_families(self) -> list[ConceptFamily]:
        """Only the families every sibling of which this set defines."""
        return [f for f in self.vocabulary.families if f.id not in self.dangling_families]

    def caption_corpus(self) -> list[str]:
        """Every caption string this set carries — label, aliases, and every locale's aliases.

        The same three fields `parity_normalisation.build_corpus` reads off the ontology, and for
        the same reason: these are the strings a filing is expected to print, so they are the only
        real text available to a check with no filing in front of it. Measured on the shipped set:
        2,006 distinct strings, 478 of them carrying Han. The Han half is the half that matters
        here — it is the only place a CAS witness is glued to text of its own script, and
        therefore the only place a bracket-hunting pattern can reach into a caption rather than
        stopping at a script boundary.
        """
        out: dict[str, None] = {}
        for d in self.items:
            for caption in [d.label, *d.aliases,
                            *(a for group in d.aliases_i18n.values() for a in group)]:
                if caption and caption.strip():
                    out.setdefault(caption.strip(), None)
        return list(out)

    @model_validator(mode="after")
    def refuse_transforms_overlapping_on_own_captions(self):
        """The overlap check again, this time over the captions THIS set declares.

        `MappingVocabulary` cannot do it: it is nested inside the set and has no view of the 475
        definitions, so on its own it runs on pattern-derived probes only — and pydantic validates
        the nested model first, so an overlap those probes can see is refused before this ever
        runs.

        WHAT THIS ADDS IS SMALLER THAN IT LOOKS, and the honest version is worth writing down: a
        witness IS the material its pattern claims, so two overlapping patterns collide on the
        witness glues almost by construction. Every attempt to build a pair that overlaps on a
        real caption and not on a glue failed — the greedy orphan head, a bare parenthetical
        stripper, a `$`-anchored trailing stripper — each was caught one level up. This runs
        anyway, for 22ms on the shipped set, because "I could not construct one" is not "none
        exists", and because it is what puts the set's 2,006 real captions into the corpus at all
        (207 probes against 15).

        THE REVELATIONS ARE RECORDED, NOT RAISED, on the precedent `dangling_families` sets two
        validators up: not an error, but not silent either. A set whose declared order is
        load-bearing on some string is a set someone can break by reordering a list, and the three
        shipped CAS rules ARE such a set — 6 probes of 207, and 4 of the 12 real captions in
        `scripts/demo_code_vs_config.py`'s corpus once an orphaned fragment is added to it.
        Nothing but this list would tell them so.

        """
        if self.vocabulary.caption_transforms and self.items:
            report = self.vocabulary.transform_order_check(self.caption_corpus())
            if report.overlaps:
                raise PatternOverlap(_overlap_message(report.overlaps))
            self.order_sensitive_probes = report.revelations
        return self


class UnknownInheritsError(ValueError):
    """A definition inherits a `section_defaults` entry that does not exist."""


def resolve_line_item_inherits(data: dict | list) -> dict:
    """Fold each `section_defaults` entry into every item naming it via `inherits`.

    A key declared ON THE ITEM always wins; the section supplies only what the item is silent
    about. Returns a new dict — the input is left alone, because a caller may hand this the
    stored definition of a live row.

    DELIBERATELY NOT A PYDANTIC VALIDATOR, for the same two reasons `loader.resolve_inherits` is
    not. Once validated, an item that inherited `match_priority` is indistinguishable from one
    that declared it and from one that declared nothing (the model's default filled the field), so
    "declared wins" can only be decided on the raw dict. And resolving before validation means the
    RESOLVED shape is what gets validated, so a bad section default fails at the door rather than
    reaching an item.

    Accepts a bare list — the shape the seed had before the envelope existed — and returns it
    wrapped, so nothing that still hands over an array breaks.
    """
    if isinstance(data, list):
        return {"items": list(data)}

    sections = data.get("section_defaults")
    items = data.get("items")
    if not isinstance(sections, dict) or not isinstance(items, list):
        return data                          # no section layer: leave it exactly alone

    resolved: list[object] = []
    missing: list[str] = []
    for entry in items:
        if not isinstance(entry, dict) or "inherits" not in entry:
            resolved.append(entry)
            continue
        name = entry["inherits"]
        base = sections.get(name) if isinstance(name, str) else None
        if not isinstance(base, dict):
            # A silent no-op here is the failure this function exists to prevent, one level up:
            # the item would validate, load, and carry none of its section's gate — no
            # `section_scope`, so nothing could ever place it. Collected rather than raised on the
            # spot so one pass names every offender.
            missing.append(f"{entry.get('key', '?')} inherits {name!r}")
            continue
        # Deep-copied per item so items sharing a section do not share its mutable lists.
        merged = {k: (list(v) if isinstance(v, list) else v) for k, v in base.items()}
        merged.update(entry)
        resolved.append(merged)

    if missing:
        known = ", ".join(sorted(sections)) or "(none)"
        raise UnknownInheritsError(
            f"{len(missing)} definition(s) inherit a section_defaults entry that does not exist: "
            + "; ".join(missing[:10])
            + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else "")
            + f". Declared sections: {known}")
    return {**data, "items": resolved}


def load_line_item_set(data: dict | list, *, resolve: bool = True) -> LineItemSet:
    """Validate a set, folding the section layer in first unless told not to.

    `resolve` defaults to TRUE here, the opposite of `loader.load_ontology`. That default is
    right for the ontology, whose read path has jobs — rendering the editor, showing what a
    concept itself declares — where an inherited value merged in silently would be wrong. This
    set has no such editor yet, and every current caller intends to MATCH or to display the gate
    in force, so the safe default is the resolved one. The screen asks for `resolve=False` when
    it wants to show what an item itself declares.
    """
    return LineItemSet.model_validate(resolve_line_item_inherits(data) if resolve
                                      else ({"items": list(data)} if isinstance(data, list)
                                            else data))
