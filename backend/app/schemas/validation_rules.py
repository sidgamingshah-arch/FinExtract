"""Validation rules — a master of its own, separate from the line items.

WHY SEPARATE. A line item declares what one line IS: where it may be claimed from, how its caption
is recognised, how its value is assembled. A validation rule declares a relation BETWEEN line
items — "net assets equals total equity", "gross profit ties to revenue less cost of sales" — and
belongs to no single one of them. Folding these into the line-item set would have put a
cross-cutting assertion inside a per-item artefact, which forces two bad choices: duplicate the
rule onto every key it mentions, or hang it off one arbitrarily and hide it from the others.

They also change on a different rhythm and for different reasons. A line item changes when a
filing uses new wording; an identity changes when the accounting framework or the output template
changes shape. One master each means an alias edit cannot invalidate an identity, and a new
identity cannot force a re-publish of 475 definitions.

WHAT THIS REPLACES. `OntologyDefinition.validation`, a block nested inside the rulebook. The
shipped rulebooks show why the nesting was awkward: `hkfrs_hk_china` declares 19 identities plus
cross-concept guards and a section-reconciliation rule, while `output_csv_hk` — the rulebook that
actually drives the output template — declares NO validation block at all, so
`structural_checks.ontology_identities` returns an empty list for it and the only arithmetic on
that path is the output template's own 66 rollups. That asymmetry was invisible: nothing named the
missing master, because a missing nested block looks exactly like a rulebook that needs none.

EXPRESSIONS ARE AUTHORED, NOT EVALUATED HERE. `expr` is carried verbatim and the structural stage
owns evaluation, unchanged. This module's job is to make the rules addressable, versioned and
checkable against a key-space — not to become a second evaluator.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# A canonical key as it appears inside an `expr`. Deliberately the same character class the
# line-item and ontology key spaces use, so `keys_referenced` finds every key an author wrote and
# `unknown_keys` can hold the master to a real key-space.
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")

# Words that look like keys to the regex above but are arithmetic, not references.
_NOT_A_KEY = frozenset({"abs", "min", "max", "sum", "and", "or", "not", "if", "else"})


class ValidationIdentity(BaseModel):
    """An arithmetic identity over line-item keys, with the consequence of a break.

    `severity` separates "this cannot be right" from "this is usually a classification difference
    worth a look" — a distinction that has to be authored, because the arithmetic cannot tell them
    apart. A balance sheet that does not balance is blocking; a gross-profit tie that misses by the
    amount of a reclassified expense is a warning worth a reviewer's eye.
    """

    id: str
    expr: str = ""
    severity: Literal["blocking", "warning"] = "warning"
    note: str = ""

    def keys_referenced(self) -> list[str]:
        """Every key-shaped token in the expression, in order, deduped.

        Used to hold the master to a key-space. An identity naming a key nothing defines is not a
        harmless typo: `ontology_identities` skips a relation whose keys it cannot resolve, so the
        check silently stops running and a broken balance sheet passes.
        """
        seen: dict[str, None] = {}
        for token in _KEY.findall(self.expr or ""):
            if token.lower() in _NOT_A_KEY:
                continue
            seen.setdefault(token, None)
        return list(seen)


class CrossConceptGuard(BaseModel):
    """A pair that is individually plausible and jointly wrong.

    A gross parent loaded together with the components it already contains, or an equity balance
    filled from a profit-attribution flow. Each figure passes every per-line check; only the
    combination is impossible, which is why this cannot be expressed as an identity over one line.

    Carried as authored text — `structural_checks` resolves it — because the shipped rulebook
    writes these as prose and turning them into a typed pair would make a wording edit a
    behaviour change while some of the prose names no key at all.
    """

    rule: str
    note: str = ""


class ValidationRuleSet(BaseModel):
    """One master of validation rules, bound to a template key-space."""

    schema_version: int = 1
    validation_key: str = ""
    # WHICH KEY-SPACE THE EXPRESSIONS ARE WRITTEN IN. Required in spirit and checked by
    # `unknown_keys`: the 19 identities shipped for `hkfrs_hk_china` name keys like
    # `bs_net_assets` and `pl_gross_profit`, which do not exist in the `output_csv_hk` set at all.
    # Applying one master's rules to the other's keys would report every identity as unevaluable,
    # or worse, resolve a same-named key to a different concept.
    target_template_key: str = ""
    target_template_version: int | None = None
    metadata: dict = Field(default_factory=dict)

    identities: list[ValidationIdentity] = Field(default_factory=list)
    cross_concept_guards: list[CrossConceptGuard] = Field(default_factory=list)
    # Every section with a reported subtotal must reconcile: reported − Σdedicated − Σresidual = 0.
    # Authored as a statement of the rule; the residual framework carries the terms it runs on.
    section_reconciliation: str = ""

    @model_validator(mode="after")
    def _distinct_ids(self):
        """Two identities sharing an id is one identity silently replacing the other in a report."""
        seen: set[str] = set()
        clashes = sorted({i.id for i in self.identities
                          if i.id in seen or seen.add(i.id)})  # type: ignore[func-returns-value]
        if clashes:
            raise ValueError(f"duplicate identity id(s): {', '.join(clashes)}")
        return self

    def keys_referenced(self) -> set[str]:
        return {key for identity in self.identities for key in identity.keys_referenced()}

    def unknown_keys(self, known: set[str]) -> dict[str, list[str]]:
        """Identity id -> the keys it names that `known` does not define.

        THE CHECK THAT MAKES A SEPARATE MASTER SAFE. Splitting the rules out of the rulebook means
        nothing structurally ties them to a key-space any more, so the tie has to be asserted.
        Without it a renamed line item leaves an identity referring to a key that no longer exists,
        `ontology_identities` quietly drops the relation, and the arithmetic that would have caught
        a mis-mapped figure stops running with no signal at all.
        """
        out: dict[str, list[str]] = {}
        for identity in self.identities:
            missing = [k for k in identity.keys_referenced() if k not in known]
            if missing:
                out[identity.id] = missing
        return out


def load_validation_rules(data: dict) -> ValidationRuleSet:
    return ValidationRuleSet.model_validate(data)


def from_ontology(ontology, *, validation_key: str = "") -> ValidationRuleSet:
    """Lift a rulebook's nested `validation` block into a master of its own.

    The migration path, and the reason the two shapes must stay interconvertible for now:
    `structural_checks` still reads the nested block, so the master is built FROM it rather than
    replacing it, and the two can be diffed until the consumers move.
    """
    block = getattr(ontology, "validation", None)
    return ValidationRuleSet(
        validation_key=validation_key or f"{getattr(ontology, 'ontology_key', '')}_validation",
        target_template_key=getattr(ontology, "target_template_key", ""),
        target_template_version=getattr(ontology, "target_template_version", None),
        identities=[ValidationIdentity(id=i.id, expr=i.expr, severity=i.severity, note=i.note)
                    for i in (getattr(block, "identities", None) or [])],
        cross_concept_guards=[CrossConceptGuard(rule=g)
                              for g in (getattr(block, "cross_concept_guards", None) or [])],
        section_reconciliation=getattr(block, "section_reconciliation", "") or "",
    )
