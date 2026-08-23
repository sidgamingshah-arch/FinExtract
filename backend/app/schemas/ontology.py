"""Ontology schema — rules that describe how to recognise each canonical line item
from arbitrary source descriptions, fix signs, find note refs, and decompose notes.

The ontology is *data, not code*: editable from the frontend, versioned, and
hot-swappable per job. Aliases/keyword/regex hints are **locale-scoped**
(``*_i18n``) so the same ontology drives extraction across the supported languages,
and ``number_format_by_locale`` makes number parsing locale-correct (a wrong
decimal/thousands separator silently corrupts values).

``schema_version: 2`` rulebooks add a SECTION LAYER: the properties that are true of every
concept printed under one section banner (which statement it is on, its ``section_scope``,
instant-vs-duration, the expected sign, the default match priority) are authored once in
``section_defaults`` and claimed by a concept through ``inherits``. Nothing in this module
performs that fold — a concept's own declaration must win over the inherited value, which is a
statement about the RAW definition, not about validated defaults (a pydantic default is
indistinguishable from an authored one). :func:`app.schemas.loader.resolve_inherits` does it
before validation; the fields exist here so the resolved shape validates and so a v1 rulebook,
which has no section layer at all, keeps loading unchanged.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.models.enums import SignConvention, StatementType


class NumberFormat(BaseModel):
    decimal: str = "."
    thousands: str = ","
    # negative markers understood by the parser
    negative: list[str] = Field(default_factory=lambda: ["paren", "minus"])
    # e.g. Indian grouping "1,23,456" vs western "123,456"
    grouping: Literal["western", "indian"] = "western"


class SignRule(BaseModel):
    convention: SignConvention = SignConvention.NATURAL
    flip_if_label_matches: list[str] = Field(default_factory=list)  # regexes


class NoteRefHint(BaseModel):
    expects_note: bool = False
    column_position: str = "after_label"
    regex: str = r"\bnote[s]?\.?\s*(\d+(?:\.\d+)?)"


ValueScope = Literal[
    "exclusive_leaf",       # a stand-alone leaf value, no overlap with others
    "exclusive_child",      # a component of a gross parent (subtract from parent)
    "exclusive_residual",   # parent minus confirmed children (a computed residual)
    "not_applicable",       # headings / non-extracted rows
]
# ``extract_or_derive`` (a subtotal that filings sometimes print and sometimes leave to
# arithmetic) and ``derive`` (one the framework computes) are still EXTRACTABLE. Only
# ``do_not_extract`` suppresses a concept — that is the single value
# ``mapping._extractable_keys`` / ``_concept_payload`` test against — so a derived subtotal that
# a filing DOES print on the face stays a candidate the mapper can recognise. Reading either new
# value as "do not extract" would refuse the printed row and sweep it into the section residual,
# which is the more expensive of the two mistakes.
ExtractionMode = Literal["extract", "extract_or_derive", "derive", "do_not_extract"]

# --- the section layer's vocabularies (v2) ----------------------------------------------------
# Declared as closed sets rather than free strings: these are the values the rulebook's own
# `binding` block reasons over, so a misspelt one ("negative_expcted") must fail the upload gate
# loudly instead of arriving as a value no downstream comparison will ever match.
Temporality = Literal["instant", "duration"]
UnitOfAccount = Literal["balance", "flow", "subtotal"]
NoteUse = Literal["evidence_only", "decomposition_allowed"]
# The sign the concept is EXPECTED to carry — a review trigger, not a transformation. Distinct
# from `SignRule.convention`, which says how to normalise a value; a concept can want both.
SignExpectation = Literal["positive_expected", "negative_expected", "either"]
# Residual concepts are populated only by the section sweep, never by an alias or a rule hint.
AliasMatching = Literal["enabled", "disabled"]


class ResidualPolicy(BaseModel):
    """A residual concept's own copy of the terms it is swept under.

    Repeats what ``residual_framework`` says globally, per concept, because the section a
    residual belongs to is the one term that is NOT global: ``cross_section: false`` only means
    something once you know which section it may not leave.
    """

    framework: str = "residual_framework"
    section_scope: str = ""
    population: str = "sweep_only"
    cross_section: bool = False
    notes_as_source: bool = False
    plug: bool = False          # never (reported subtotal − mapped children)
    itemise: bool = True


class Equivalence(BaseModel):
    """Two captions reporting ONE economic fact ("Net assets" / "Total equity").

    Kept as a rule rather than a shared alias: an alias on both concepts makes whichever is
    declared first win the caption, and the other silently empty.
    """

    # ``with`` is a Python keyword, so the field is named for the alias; ``serialize_by_alias``
    # keeps ``model_dump()`` speaking the authored spelling, which is what the upload gate
    # compares the submitted JSON against (an un-aliased dump would report `equivalence.with`
    # as an undeclared key).
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    with_: str = Field(default="", alias="with")     # the other canonical_key
    relation: str = ""
    rule: str = ""


class OntologyMapping(BaseModel):
    canonical_key: str
    # Human-readable name + a definition of what this canonical concept MEANS. The
    # definition/description is the primary signal for LLM description-based mapping: the
    # model matches a source caption to a concept by meaning, not string similarity.
    label: str = ""
    description: str = ""
    # Authoritative accounting definition (preferred over `description` when present) plus
    # natural-language inclusion / exclusion criteria and easily-confused concepts — all
    # fed to the LLM so the mapping decision is criteria-driven, not string-driven.
    # (Learnings from field-tested Ind-AS extraction ontologies.)
    definition: str = ""
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    confusable_with: list[str] = Field(default_factory=list)  # canonical_keys easy to confuse
    value_scope: ValueScope = "exclusive_leaf"
    extraction_mode: ExtractionMode = "extract"
    # Natural-language residual/decomposition rule for this concept (e.g. "= combined
    # intangible parent − goodwill − IAUD; if reported exclusively, do not subtract").
    decomposition_rule: str | None = None
    others_rule: str | None = None
    aliases: list[str] = Field(default_factory=list)          # default-locale aliases
    aliases_i18n: dict[str, list[str]] = Field(default_factory=dict)  # per-locale
    keyword_hints: list[str] = Field(default_factory=list)
    regex_hints: list[str] = Field(default_factory=list)
    exclude_hints: list[str] = Field(default_factory=list)
    sign_rule: SignRule = Field(default_factory=SignRule)
    note_ref_hint: NoteRefHint = Field(default_factory=NoteRefHint)
    min_confidence_to_auto_accept: float = 0.85

    # --- the section layer (v2) ---------------------------------------------------------------
    # ``inherits`` names a ``section_defaults`` entry; the four fields under it are authored ONLY
    # there and reach a concept through :func:`app.schemas.loader.resolve_inherits`. Declared here
    # so the resolved definition validates — without them the fold would be silently discarded by
    # ``extra='ignore'`` and the whole section layer would be inert.
    inherits: str | None = None
    statement: StatementType | None = None
    section_scope: list[str] = Field(default_factory=list)
    temporality: Temporality | None = None
    # ``None`` means "nothing was said", not "false": v1 rulebooks never expressed face-only-ness,
    # and defaulting them to the v2 global rule would assert a policy they were never written for.
    face_only: bool | None = None

    # Per-concept properties a section default may also supply. All optional: a v1 rulebook
    # declares none of them and must load byte-identically to before.
    unit_of_account: UnitOfAccount | None = None      # balance | flow | subtotal
    note_use: NoteUse | None = None
    note_use_rationale: str | None = None             # WHY a section may source from its note
    notes_as_source_rationale: str | None = None      # …and why one residual may, uniquely
    sign_convention: SignExpectation | None = None
    # Higher is evaluated first, so a long specific caption cannot be pre-empted by a short
    # generic one on token overlap. Residuals are 0 and unreachable by matching.
    match_priority: int | None = None
    alias_matching: AliasMatching = "enabled"
    # Containment: a gross parent must never be loaded additively with the children it contains.
    is_gross_parent: bool = False
    children_if_decomposed: list[str] = Field(default_factory=list)
    # The mirror image of containment: this concept is the child a subtotal collapses to when the
    # face prints the subtotal ALONE. Names the subtotal. Nothing is divided — the whole
    # undifferentiated figure is this child — and the inference is refused as soon as any sibling
    # child is evidenced on the face or in the subtotal's own note, so
    # ``global_rules.no_fabricated_split`` still holds. See ``decomposition_rule`` for the wording.
    sole_component_of: str | None = None
    # Residual concepts: the sweep terms, what the residual is expected to pick up (prose, for
    # the LLM), and the keys it must never absorb however similar the wording.
    residual_policy: ResidualPolicy | None = None
    expected_components: list[str] = Field(default_factory=list)
    never_sweep: list[str] = Field(default_factory=list)
    # Prose the LLM is shown or a reviewer reads; free-form by nature.
    derivation: str | None = None                     # how the value is computed when not printed
    section_disambiguation: str | None = None         # which of two look-alike captions is this
    aggregation_note: str | None = None               # several printed rows sum into one concept
    template_note: str | None = None                  # a known disagreement with the template
    equivalence: Equivalence | None = None

    def meaning(self) -> str:
        """Best available semantic text for description-based matching."""
        return self.definition or self.description or self.label or self.canonical_key

    def aliases_for(self, locale: str | None) -> list[str]:
        out = list(self.aliases)
        if locale and locale in self.aliases_i18n:
            out = out + self.aliases_i18n[locale]
        # also include the English set as a cross-lingual fallback anchor
        if "en" in self.aliases_i18n:
            out = out + self.aliases_i18n["en"]
        return list(dict.fromkeys(out))  # dedupe, keep order


class DecompositionRule(BaseModel):
    """Defines the face↔note tie and the residual-as-'other' reconciliation (§20)."""

    id: str
    face_key: str
    note_match: dict = Field(default_factory=lambda: {"by": "note_number"})
    expected_children: list[str] = Field(default_factory=list)
    reconcile_op: Literal["sum_equals_face"] = "sum_equals_face"
    residual_allowed_as: str | None = "other"
    tolerance_abs: float = 1.0
    tolerance_rel: float = 0.001


class NettingRule(BaseModel):
    """A GENERIC containment-netting policy: a face line MAY be reported inclusive of other lines
    (e.g. a cost of sales stated inclusive of administrative / selling & marketing expenses), and
    when it is, the clean figure nets them out — ``net = target − Σ subtract + Σ add`` (signed, so
    it works whether expenses are stored negative or positive), with the formula surfaced.

    It is NOT applied unconditionally. ``condition`` is a natural-language test an LLM evaluates
    against the actual document first; only when the model confirms the containment (and which of
    the candidate lines are truly included) is the deterministic arithmetic applied. So the policy
    can ship for every template yet stay silent on filings where it doesn't hold (e.g. a property
    developer whose cost of sales excludes admin/S&M). ``subtract_keys``/``add_keys`` are the
    CANDIDATE lines the model chooses from; the math itself stays deterministic and non-destructive
    (the raw figure is retained for audit)."""

    id: str
    target_key: str
    subtract_keys: list[str] = Field(default_factory=list)   # candidate lines possibly contained
    add_keys: list[str] = Field(default_factory=list)
    condition: str = ""                     # NL test the LLM evaluates before applying
    label: str = ""                         # human explanation of the policy
    # Whether the containment has to be positively evidenced in the document (a note itemising
    # the included expenses) before the arithmetic may run — the difference between a policy that
    # ships everywhere and one that fires everywhere.
    evidence_required: bool = False
    on_apply: str = ""                      # what to record on the fact once applied
    # The inverse direction: a face total the note splits into children. Same gate (`condition`),
    # opposite arithmetic, so it belongs on the same rule rather than in a parallel list.
    decompose_into: list[str] = Field(default_factory=list)


class MutuallyExclusiveGroup(BaseModel):
    """An aggregate and the components it contains — populate one side, never both.

    The template's rollup lists them side by side (``bs_equity__reserves`` next to share premium,
    capital and general reserve), so loading both double-counts equity and the balance still ties.
    """

    id: str = ""
    aggregate: str = ""
    components: list[str] = Field(default_factory=list)
    rule: str = ""
    note: str = ""


class GlobalRules(BaseModel):
    credit_balance_lines: list[str] = Field(default_factory=list)  # key globs
    # `paren_means_negative` was here and is deliberately gone. It stated the same rule as
    # `number_format_by_locale[<locale>].negative`, which is the one the parser reads — and reads
    # at EXTRACTION, before this block is ever consulted. So the global copy could not be honoured
    # afterwards: the printed text is not retained, and a figure already negated is
    # indistinguishable from one printed with a minus. A switch that cannot work is worse than no
    # switch, because an author who flips it believes the parsing changed. Parentheses are decided
    # in exactly one place now.
    # Natural-language extraction policies fed to the LLM system prompt so decisions
    # follow one consistent, auditable rulebook (learnings from prior ontologies):
    parent_child_allocation: list[str] = Field(default_factory=list)
    duplicate_fact_rule: str = ""      # face + note = one fact w/ multiple evidence
    other_income_rule: str = ""
    others_policy: list[str] = Field(default_factory=list)
    totals_policy: str = ""            # totals computed from mutually-exclusive outputs
    no_fabricated_split: str = ""
    source_fact_id: str = ""           # composition of the provenance identity
    # Where a note may be a SOURCE rather than evidence — the default is nowhere.
    face_only_default: str = ""
    # The prose behind `SignExpectation`: what "stored negative" means, why the template's
    # subtotal identities only hold under it, and that a wrong sign is a review trigger rather
    # than an auto-correction. An open object: each key is one policy paragraph.
    sign_convention: dict = Field(default_factory=dict)
    mutually_exclusive_groups: list[MutuallyExclusiveGroup] = Field(default_factory=list)


class WorkedExample(BaseModel):
    """A few-shot example injected into the mapping/extraction prompt to anchor policy."""

    scenario: str
    source: dict = Field(default_factory=dict)
    output: list[dict] = Field(default_factory=list)
    validation: str = ""
    instruction: str = ""
    # Addressable, so a review finding or a prompt regression can name the example it came from.
    id: str = ""
    resolution: str = ""      # which concepts claim what, and why, before the output is shown
    reconciliation: str = ""  # the arithmetic that proves the example ties


class OntologyMetadata(BaseModel):
    name: str = ""
    framework: str = ""                # e.g. IND_AS, HKFRS, IFRS
    version: str = ""
    source_template: str = ""
    field_count: int | None = None
    changes: list[str] = Field(default_factory=list)  # changelog
    supersedes: str = ""               # the ontology_key this one replaces
    concept_count: int | None = None
    # Changes that alter how an existing rulebook behaves, and defects knowingly carried forward
    # (a canonical_key with a typo in it that is load-bearing for stored data). Both are recorded
    # because the alternative is a reader rediscovering them from the diff.
    breaking_changes: list[str] = Field(default_factory=list)
    retained_defects: list[str] = Field(default_factory=list)


class Normalisation(BaseModel):
    """The text pipeline applied to BOTH a candidate row label and an alias before comparison.

    Authored rather than assumed because it is what lets one Chinese alias cover both scripts:
    fold Traditional to Simplified on both sides and 銷售成本 compares equal to 销售成本.
    """

    note: str = ""
    pipeline: list[str] = Field(default_factory=list)
    wrapped_caption_rule: str = ""


class Binding(BaseModel):
    """How a printed row is bound to a concept — section first, then rule, then semantic.

    ``order`` is the precedence as prose (the engine implements it; this is the authored
    statement of it), and ``unbound_row_policy`` is the promise that a row no concept claims is
    still emitted for review rather than dropped or forced into a bucket.
    """

    note: str = ""
    order: list[str] = Field(default_factory=list)
    unbound_row_policy: str = ""
    match_priority: str = ""      # prose describing the integer scale used per concept


class EntityScopeSelection(BaseModel):
    default: str = "consolidated"
    rule: str = ""
    signals: list[str] = Field(default_factory=list)
    company_only_markers: list[str] = Field(default_factory=list)


class PeriodSelection(BaseModel):
    default: str = "current_reporting_period"
    rule: str = ""
    restatement_rule: str = ""


class UnitsAndCurrency(BaseModel):
    rule: str = ""
    signals: list[str] = Field(default_factory=list)
    conflict: str = ""


class ScopeSelection(BaseModel):
    """WHICH column to read: Group vs Company, current vs comparative, currency and scale.

    A statement prints several columns of equally valid numbers, so getting this wrong loads the
    wrong figures silently — every downstream check passes because the column is internally
    consistent. ``column_guard`` is why two facts differing only on scope are not duplicates.
    """

    note: str = ""
    entity_scope: EntityScopeSelection = Field(default_factory=EntityScopeSelection)
    period_selection: PeriodSelection = Field(default_factory=PeriodSelection)
    units_and_currency: UnitsAndCurrency = Field(default_factory=UnitsAndCurrency)
    column_guard: str = ""


class ResidualSweep(BaseModel):
    """When the sweep runs and which rows it may take."""

    runs: str = ""
    candidate_set: str = ""
    eligibility: list[str] = Field(default_factory=list)
    cross_section: bool = False
    notes_as_source: bool = False
    derivation: str = "forbidden"
    plug_behaviour: str = ""
    literal_others_caption: str = ""


class ResidualItemisation(BaseModel):
    required: bool = True
    component_fields: list[str] = Field(default_factory=list)
    aggregation: str = "sum_of_components"
    rule: str = ""


class ResidualReconciliation(BaseModel):
    identity: str = ""
    tolerance: str = ""
    on_failure: str = ""
    sections_without_reported_subtotal: str = ""


class ResidualFramework(BaseModel):
    """One definition governing every ``exclusive_residual`` concept.

    Replaces a prose ``others_policy`` that stated the intent and gave the engine no mechanism:
    a residual is the SUM OF ITS SWEPT COMPONENTS, never (reported subtotal − mapped children).
    The difference matters exactly when extraction has missed a row — a plug hides the loss inside
    a plausible number, a sum leaves it as an ``unallocated_gap`` the reconciliation reports.
    """

    note: str = ""
    applies_to: str = ""
    alias_matching: AliasMatching = "disabled"
    population: str = "sweep_only"
    sweep: ResidualSweep = Field(default_factory=ResidualSweep)
    itemisation: ResidualItemisation = Field(default_factory=ResidualItemisation)
    reconciliation: ResidualReconciliation = Field(default_factory=ResidualReconciliation)
    review_triggers: list[str] = Field(default_factory=list)
    prohibitions: list[str] = Field(default_factory=list)


class SectionDefaults(BaseModel):
    """Properties shared by every concept printed under one section banner.

    Authored once and claimed by a concept via ``inherits``; every field is optional, since a
    section states only what is actually true of all of its concepts. The fold happens on the RAW
    definition (:func:`app.schemas.loader.resolve_inherits`) precisely so an unset field here
    stays unset on the concept instead of overwriting it with this model's default.
    """

    statement: StatementType | None = None
    section_scope: list[str] = Field(default_factory=list)
    temporality: Temporality | None = None
    unit_of_account: UnitOfAccount | None = None
    value_scope: ValueScope | None = None
    extraction_mode: ExtractionMode | None = None
    face_only: bool | None = None
    note_use: NoteUse | None = None
    note_use_rationale: str | None = None
    sign_convention: SignExpectation | None = None
    match_priority: int | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class ValidationIdentity(BaseModel):
    """An arithmetic identity over canonical keys, with the consequence of a break.

    ``expr`` is authored, not evaluated here — the structural stage owns evaluation.
    ``severity`` separates "this cannot be right" from "this is usually a classification
    difference worth a look".
    """

    id: str
    expr: str = ""
    severity: Literal["blocking", "warning"] = "warning"
    note: str = ""


class ValidationRules(BaseModel):
    identities: list[ValidationIdentity] = Field(default_factory=list)
    section_reconciliation: str = ""
    # Pairs that are individually plausible and jointly wrong — a gross parent loaded with its
    # components, or an equity balance loaded from a profit-attribution flow.
    cross_concept_guards: list[str] = Field(default_factory=list)


class OntologyDefinition(BaseModel):
    schema_version: int = 1
    ontology_key: str
    target_template_key: str
    target_template_version: int | None = None
    locale: str = "en"
    supported_locales: list[str] = Field(default_factory=lambda: ["en"])
    number_format_by_locale: dict[str, NumberFormat] = Field(
        default_factory=lambda: {"en": NumberFormat()}
    )
    metadata: OntologyMetadata | None = None
    mappings: list[OntologyMapping] = Field(default_factory=list)
    decomposition_rules: list[DecompositionRule] = Field(default_factory=list)
    # Face-line containment netting (e.g. cost of sales stated inclusive of admin / S&M).
    netting_rules: list[NettingRule] = Field(default_factory=list)
    global_rules: GlobalRules = Field(default_factory=GlobalRules)
    worked_examples: list[WorkedExample] = Field(default_factory=list)

    # --- v2 blocks. All optional: a v1 rulebook declares none of them. -------------------------
    normalisation: Normalisation | None = None
    binding: Binding | None = None
    scope_selection: ScopeSelection | None = None
    residual_framework: ResidualFramework | None = None
    # Keyed by section id (``bs_s2_current_assets``), the value a concept's ``inherits`` names.
    section_defaults: dict[str, SectionDefaults] = Field(default_factory=dict)
    validation: ValidationRules | None = None

    def number_format(self, locale: str | None) -> NumberFormat:
        if locale and locale in self.number_format_by_locale:
            return self.number_format_by_locale[locale]
        return self.number_format_by_locale.get("en", NumberFormat())

    def canonical_keys(self) -> set[str]:
        return {m.canonical_key for m in self.mappings}
