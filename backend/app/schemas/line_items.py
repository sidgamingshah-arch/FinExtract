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

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.models.enums import StatementType
from app.schemas.ontology import (
    AliasMatching,
    ExtractionMode,
    NoteUse,
    ResidualPolicy,
    SignExpectation,
    SignRule,
    Temporality,
    UnitOfAccount,
    ValueScope,
)

LineItemType = Literal["extracted", "calculated", "intermediate", "derived"]

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
# `is_pl__deprec_and_impairment_oper_exp` are alternative sources for ONE figure —
# `deprec_impairment`'s own module docstring says they must never be summed "since they routinely
# restate the same figure" — and `check_rollups` would have summed them.
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
    # nicety: `deprec_impairment._first_valid` skips a candidate below zero and flags
    # NEGATIVE_RESIDUAL, and the config that claimed to port it had no such refusal — so a rung
    # computing -50 would win here and be refused by the service it describes. Default True
    # because every shipped cascade is a charge, a balance or an exposure, none of which can be
    # negative; a definition that genuinely can (a net movement, a carryforward) turns it off.
    refuse_negative: bool = True


class NoteSource(BaseModel):
    """Which note a sub-line item is read from, and which of its rows count.

    This is what the closed enumerations become. `note_title_any` replaces the hard-coded
    `_NOTE_HEADINGS` pattern per dataset, and `row_caption_any` replaces `_QUALIFYING_RE` — the
    162-alternative whitelist that refused a filing writing "Depreciation charge for the year".
    Both take patterns, so widening one is an edit here rather than a release.
    """

    note_title_any: list[str] = Field(default_factory=list)
    row_caption_any: list[str] = Field(default_factory=list)
    row_caption_none: list[str] = Field(default_factory=list)
    # WHICH TEXT THESE PATTERNS ARE AUTHORED AGAINST. The shipped patterns were lifted out of
    # `deprec_impairment`, which matches RAW captions, so folding them through `normalize_label`
    # would stop some of them matching. `mapping_v1` says the opposite — author against normalised
    # text, as the rulebook's aliases are. Saying it per pattern group beats guessing.
    caption_normalization: Literal["none", "mapping_v1"] = "none"

    @model_validator(mode="after")
    def _patterns_compile(self):
        _refuse_uncompilable(
            ("note_title_any", self.note_title_any),
            ("row_caption_any", self.row_caption_any),
            ("row_caption_none", self.row_caption_none),
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
    # Display prose, and — separately — the authoritative accounting meaning. `definition` is what
    # the LLM's description-based tier matches a caption against (`OntologyMapping.meaning()`
    # prefers it over `description`), so collapsing the two would either put display copy into a
    # matching decision or hide the meaning from the screen.
    description: str = ""
    definition: str = ""
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
    aggregation_note: str | None = None
    template_note: str | None = None
    notes_as_source_rationale: str | None = None

    # ── extracted: how the caption is recognised ─────────────────────────────────────────────
    scopes: list[SearchScope] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    side: Side = "none"
    # Whether a caption printed on the opposite side may fill this line. Off by default: a bare
    # "Cash" caption once resolved to an overdraft because nothing forbade it. On only for an
    # instrument that genuinely appears on both sides — a derivative, a swap, an option — where
    # one note table lists both and the row's own column is the only thing that separates them.
    allow_contra: bool = False
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
    min_confidence_to_auto_accept: float = 0.85

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
    # The service that implements this cascade today, when one does. Present so a definition can
    # describe an existing derivation before anything is rewired to read it — the config and the
    # code can be compared before either is trusted.
    implemented_by: str = ""

    @model_validator(mode="after")
    def _coherent(self):
        if self.type == "intermediate":
            self.in_output = False
        if self.type in ("calculated", "intermediate") and not self.terms:
            raise ValueError(f"{self.key}: a {self.type} line needs at least one term")
        if self.type == "derived" and not (self.cascade or self.implemented_by):
            raise ValueError(f"{self.key}: a derived line needs a cascade or `implemented_by`")
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

    # ── the gate, as the matcher will ask it ─────────────────────────────────────────────────
    def claimable_on(self, statement: StatementType | str | None) -> bool:
        """Whether a caption printed on `statement` may be claimed by this line item.

        PERMISSIVE WHEN SILENT. `statement=None` on the definition means nothing was said, so
        every statement is allowed; an unknown statement on the caption side is likewise not a
        reason to refuse, because refusing everything the classifier could not name would delete
        rows rather than mis-file them.
        """
        if self.statement is None or statement is None:
            return True
        want = statement.value if isinstance(statement, StatementType) else str(statement)
        return self.statement.value == want

    def claimable_under(self, section: str | None) -> bool:
        """Whether a caption under this section banner may be claimed by this line item."""
        if not self.section_scope:
            return True                      # unconstrained, not "no banner allowed"
        if not section:
            return True                      # a statement total sits under no banner
        return section.strip().lower() in {s.strip().lower() for s in self.section_scope}

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
    items: list[LineItemDef] = Field(default_factory=list)


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
