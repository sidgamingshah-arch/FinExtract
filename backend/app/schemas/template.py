"""Template schema — the target output definition, authored/uploaded from the UI.

A template is an ordered section→node tree. Subtotal/total nodes declare a ``rollup``
listing exactly which children feed them, so subtotals can be *recomputed* and
compared to the extracted value (feeding the validation engine and review queue).
Statement-level ``identities`` (Assets = Liabilities + Equity) and set-level
``cross_statement_ties`` are declarative. Canonical labels carry ``label_i18n`` so
output can render in the same language set as input (multilingual parity).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.models.enums import LineRole, SignConvention, StatementType


class Rollup(BaseModel):
    # ``weighted_sum`` used to be permitted here and is deliberately gone. Nothing declared
    # the weights — there was no field to declare them in — so ``structural_checks`` could
    # only report such a relation as skipped, and a template naming the op lost the arithmetic
    # guard on exactly the node it was authored on: the subtotal validated as "not evaluable"
    # forever, which reads like coverage rather than the authoring mistake it is. Adding a
    # ``weights`` field instead would invent a shape no face statement needs (a weighted average
    # is an EPS denominator, not a subtotal), so the op is refused at the upload gate where there
    # is a request to fail and an author to tell. See ``SUPPORTED_OPS`` for the read-path guard
    # that still refuses an op reaching evaluation from an older stored definition.
    op: Literal["sum", "diff"] = "sum"
    children: list[str] = Field(default_factory=list)  # node_ids


class TemplateNode(BaseModel):
    node_id: str
    canonical_key: str
    label: str
    label_i18n: dict[str, str] = Field(default_factory=dict)  # {"en": .., "zh": .., "ar": .., "fr": ..}
    role: LineRole = LineRole.LINE
    sign: SignConvention = SignConvention.NATURAL
    expects_note: bool = False
    required: bool = False
    children: list["TemplateNode"] = Field(default_factory=list)
    rollup: Rollup | None = None

    def resolve_label(self, locale: str | None) -> str:
        if locale and locale in self.label_i18n:
            return self.label_i18n[locale]
        return self.label_i18n.get("en", self.label)


class Identity(BaseModel):
    id: str
    lhs: str                                   # canonical_key / node_id
    rhs: Rollup                                # combination of terms
    tolerance_abs: float = 1.0
    tolerance_rel: float = 0.001


class TemplateStatement(BaseModel):
    type: StatementType

    @field_validator("type", mode="before")
    @classmethod
    def _fold_equity_spelling(cls, v):
        """Accept ``changes_in_equity``, which is the spelling everything but this enum uses.

        THE HOLE THIS CLOSES. ``StatementType`` spells the statement ``equity_changes``; the page
        classifier, the workbook importer's `Statement` column, the API and the front end all say
        ``changes_in_equity``. ``services.mapping.normalize_statement`` already folds the two for
        COMPARISON, so a rulebook authored either way scopes correctly — but nothing folded them on
        the way IN, so a template workbook with a "Changes in equity" row passed the importer's own
        validation and was then refused by this schema with an enum error naming four values, one of
        which the importer never produces. The equity statement was, in practice, un-uploadable.

        Folded here rather than by renaming the enum: ``changes_in_equity`` is already the canonical
        spelling by weight of use, and the enum's VALUE is what stored rulebooks validate their
        ``statement`` field against — changing it would invalidate authored definitions to fix an
        input path.
        """
        return "equity_changes" if v == "changes_in_equity" else v
    default_sign_convention: SignConvention = SignConvention.NATURAL
    # The statement's own heading, per output language. Undeclared until now, but READ in two
    # places off the raw stored definition — ``documents._stmt_label`` and the Template screen's
    # tree via ``templates._loc(stmt, locale)`` — so it was authorable only by accident, and
    # refusing undeclared keys on upload turned "works but is not in the schema" into a 422 for
    # the one mechanism that gives a statement a localized heading at all. Without it every locale
    # falls back to the statement type titled ("Balance Sheet"), which is why the shipped
    # template's heading is untranslated on that screen today.
    label: str = ""
    label_i18n: dict[str, str] = Field(default_factory=dict)  # {"en": .., "zh": .., …}
    sections: list[TemplateNode] = Field(default_factory=list)
    identities: list[Identity] = Field(default_factory=list)

    def resolve_label(self, locale: str | None) -> str:
        if locale and locale in self.label_i18n:
            return self.label_i18n[locale]
        return self.label_i18n.get("en", self.label)


# --- KPIs -------------------------------------------------------------------------------------
# A KPI is ``numerator ÷ denominator`` with a unit (×, %, days) — the shape
# ``services.derived._RATIOS`` is written in, and the shape this block declares, so a template can
# own the catalog the KPI view serves instead of it being reachable only by editing code.
#
# WHY A BLOCK OF ITS OWN, AND NOT A NODE IN A STATEMENT'S SECTION TREE:
#
# * A section tree is a tree of lines a filing PRINTS. Every ``canonical_key`` in it is something
#   the ontology maps a caption onto, what ``validate_ontology_against_template`` checks a rulebook
#   against, what the Template screen offers aliases and a sign convention for, and what the
#   editable template workbook publishes as an extracted or calculated row. A ratio is none of
#   those: no filing prints it, no caption maps to it, and it has no sign convention. Declared
#   there it would be a mappable line that can never be mapped.
# * ``structural_checks`` reads relations off the section tree and ``coverage`` counts them. A ratio
#   is not an arithmetic identity — a rollup pointed at one would either fail on arithmetic nobody
#   declared or, since nothing prints a ratio, skip on ``target_not_extracted`` on every filing
#   forever. That is the ``weighted_sum`` trap the ``Rollup`` comment above describes: a relation
#   that reads like coverage and can never run. Living outside ``statements`` is what makes the
#   exclusion structural instead of a filter somebody can forget.
# * ``StatementType`` has no member for a KPI page and must not grow one: the three face statements
#   are what a filing contains, and a fourth pseudo-statement would enter the coverage denominator
#   and the statement dispatch as though a document could print it.


class KpiTerm(BaseModel):
    """One input to a ratio side or to a calculated intermediate: a canonical key and a sign."""

    key: str
    # Candidates tried after ``key``, first one the filing reported wins. Not decoration: DIO and
    # DPO divide by cost of goods sold, which plenty of HK filings never break out, and fall back
    # to the total operating cost subtotal so the ratio still computes rather than reading as
    # unavailable on a document that does state the cost base (services.derived._COST_BASE).
    fallback_keys: list[str] = Field(default_factory=list)
    # +1 adds, -1 subtracts. A side is a SIGNED SUM, which is why nothing here declares an ``op``:
    # "sum" and "diff" are one arithmetic written two ways, and two spellings of one quantity are
    # two things that have to keep agreeing.
    sign: Literal[1, -1] = 1
    # An aggregate a filing reports only part of — total debt is eight possible lines, and a company
    # carrying two of them still has a total. Optional-and-absent contributes nothing;
    # REQUIRED-and-absent makes the whole side unavailable, which is what stops EBITDA being served
    # as the depreciation add-back alone on a filing where EBIT was never extracted.
    optional: bool = False


class KpiIntermediate(BaseModel):
    """A figure a ratio is built from that no filing prints — EBITDA, net debt, capital employed.

    Declared once and named, rather than inlined into every ratio's term list, because that is the
    difference between a user being able to revise "what we mean by net debt" and having to find the
    same eight keys repeated across four ratios. ``terms`` is a signed sum over canonical keys and
    over other intermediates, so ``net_debt`` may be written in terms of ``total_debt``; a cycle is
    refused at the upload gate (``schemas.loader.validate_template``).

    Deliberately NOT expressible: an average balance ((opening + closing) / 2). It needs a term
    from a different PERIOD and a divisor, and no signed sum over one column can state either — so
    it would have to be declared as something the engine then reports as unevaluable forever, which
    is the trap ``Rollup`` documents. Nothing in the catalog needs one: every ratio in it divides by
    a closing balance.
    """

    key: str
    label: str
    label_i18n: dict[str, str] = Field(default_factory=dict)
    terms: list[KpiTerm] = Field(min_length=1)


class KpiRatio(BaseModel):
    """One KPI: a signed sum over the numerator, divided by a signed sum over the denominator.

    A missing required input on either side, or a denominator of zero, makes the KPI *unavailable*
    — it is never fabricated, and never quietly reported as 0 (``services.derived.compute_ratios``).

    There is no ``formula`` field. The formula shown beside a KPI is DERIVED from these terms and
    their resolved labels, because a prose formula authored beside the arithmetic is a second
    spelling of it: an author who edits the terms and not the sentence ships a number whose stated
    derivation is a lie, and nothing can detect that. The built-in catalog keeps its prose because
    it is code under review; an uploaded template is not.
    """

    key: str
    label: str
    label_i18n: dict[str, str] = Field(default_factory=dict)
    # The five the KPI view can group, order and localize (``documents._KPI_CATEGORY_I18N``,
    # ``derived._CATEGORY_ORDER``). Free text would let an author declare a category that renders
    # untranslated and sorts last — a field that looks live and is not — so it is refused at the
    # gate instead, where there is a request to fail and an author to tell.
    category: Literal["Liquidity", "Leverage", "Coverage", "Efficiency", "Profitability"]
    # Exactly the units ``derived._UNIT_SCALE`` knows how to scale and ``derived._display`` knows
    # how to render. tests/test_template_kpis.py holds the two tables to each other.
    unit: Literal["x", "%", "days"]
    numerator: list[KpiTerm] = Field(min_length=1)
    denominator: list[KpiTerm] = Field(min_length=1)


class KpiBlock(BaseModel):
    """The template's KPI catalog: the intermediates first, then the ratios built from them.

    An empty block means "this template declares no KPIs", and the KPI view then falls back to the
    built-in catalog and SAYS which one it used — see ``documents._build_kpi_statement``.
    """

    intermediates: list[KpiIntermediate] = Field(default_factory=list)
    ratios: list[KpiRatio] = Field(default_factory=list)

    def keys(self) -> list[str]:
        """Every key this block declares, intermediates and ratios alike, in declaration order."""
        return [i.key for i in self.intermediates] + [r.key for r in self.ratios]


class CrossStatementTie(BaseModel):
    id: str
    lhs: dict                                  # {"statement": .., "key": ..}
    rhs: dict
    match_on: list[str] = Field(default_factory=lambda: ["basis", "period_end"])
    tolerance_abs: float = 1.0


class TemplateDefinition(BaseModel):
    schema_version: int = 1
    template_key: str
    name: str
    statements: list[TemplateStatement] = Field(default_factory=list)
    # The KPI catalog this template owns. Empty (the default) means every template authored before
    # this block existed keeps behaving exactly as it did: the KPI view falls back to the built-in
    # catalog and says so.
    kpis: KpiBlock = Field(default_factory=KpiBlock)
    cross_statement_ties: list[CrossStatementTie] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys(self) -> "TemplateDefinition":
        """One key, one thing it names — across the section trees AND the KPI block.

        This used to count duplicates in ``all_canonical_keys()``, which returns a SET: the
        duplicates were collapsed before they were counted, so the check could never report one and
        a JSON template repeating a key published happily (the workbook route refused it, so the two
        authoring doors disagreed). Two nodes sharing a key silently merge every figure mapped to it
        and every rollup naming it.

        The KPI keys are in the same namespace on purpose. A ratio or an intermediate sharing a key
        with a printed line would make "what is this figure" ambiguous in the one place that matters:
        a ratio term names a key, and it must resolve to either the extracted line or the derived
        figure, never to whichever the reader assumes.
        """
        keys = [n.canonical_key for n in self.all_nodes()] + self.kpis.keys()
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"Duplicate canonical_key(s) in template: {sorted(dupes)}")
        return self

    def _walk(self, nodes: list[TemplateNode]):
        for n in nodes:
            yield n
            yield from self._walk(n.children)

    def all_nodes(self):
        for st in self.statements:
            yield from self._walk(st.sections)

    def all_canonical_keys(self) -> set[str]:
        return {n.canonical_key for n in self.all_nodes()}

    def node_ids(self) -> set[str]:
        return {n.node_id for n in self.all_nodes()}


TemplateNode.model_rebuild()
