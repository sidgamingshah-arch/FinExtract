"""The rules master — everything a rulebook says that is NOT about one line item.

THE SEPARATION THIS COMPLETES. Merging the ontology into the line-item model put 475 definitions
in one place, each answering "what is this line, where may it be claimed from, how is it
recognised". But a rulebook says more than that, and the rest of what it says belongs to no single
line:

    validation           net assets = total equity. An identity BETWEEN lines.
    binding              the ORDER the tiers run in, for every caption on every page.
    scope_selection      which entity column and which period a page is read under, before any
                         line item exists.
    normalisation        the caption-fold pipeline the whole rulebook is keyed on.
    residual_framework   one definition governing every leftover bucket at once.
    global_rules         credit-balance conventions, the duplicate-fact rule, totals policy.
    netting              a face line stated inclusive of another, so the two must not both load.
    decomposition        the face-to-note tie and its reconciliation.
    worked_examples      what the answer SHOULD be, for a scenario the prose cannot pin down.

Folding these into the line-item set forces a bad choice: duplicate the rule onto every key it
mentions, or hang it off one arbitrarily and hide it from the others. And they change on a
different rhythm — a line item changes when a filing uses new wording, `binding` changes when the
framework does.

WHY BROADER THAN THE VALIDATION MASTER IT ABSORBS. `validation_rules.ValidationRuleSet` came
first and covered the arithmetic checks only, which turned out to be the smallest of the nine.
Measured on the shipped rulebooks: `output_csv_hk` — the one that drives the output template —
carries global_rules (8 keys), binding (4), scope_selection (5), residual_framework (9) and
normalisation (3), and NO validation at all; `hkfrs_hk_china` carries all of those plus 19
identities, 6 guards, 2 netting rules and 8 worked examples. A master that held only validation
would have been empty for the rulebook that matters.

NOTHING IS REDECLARED. Every block below is the ontology's own model, imported. Two schemas for
one idea is the failure the whole merge exists to end, and it has already cost this codebase two
statement-spelling bugs.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.schemas.ontology import (
    Binding,
    DecompositionRule,
    GlobalRules,
    NettingRule,
    Normalisation,
    ResidualFramework,
    ScopeSelection,
    WorkedExample,
)
from app.schemas.validation_rules import ValidationRuleSet


class RulebookRulesMetadata(BaseModel):
    name: str = ""
    version: str = ""
    supersedes: str | None = None
    changes: list[str] = Field(default_factory=list)
    breaking_changes: list[str] = Field(default_factory=list)


class RulebookRules(BaseModel):
    """One master of everything a rulebook asserts across line items, bound to a key-space."""

    schema_version: int = 1
    rules_key: str = ""
    # WHICH KEY-SPACE THE RULES ARE WRITTEN IN, and it is not decoration. Splitting the rules out
    # of the rulebook removes the structural tie between an identity and the keys it names, so the
    # tie has to be asserted instead — see `unknown_keys`. Measured: all 19 identities extracted
    # from `hkfrs_hk_china` name keys the 475-definition `output_csv_hk` set does not define.
    target_template_key: str = ""
    target_template_version: int | None = None
    locale: str = "en"
    metadata: RulebookRulesMetadata = Field(default_factory=RulebookRulesMetadata)

    # ── how a page is read, before any line item is in hand ──────────────────────────────────
    # `scope_selection` decides the entity column and the period; `normalisation` is the caption
    # fold the whole rulebook is keyed on. Both run for EVERY page and neither is about a concept,
    # which is why `row_reconstruct.in_force_rules()` reaches into a rulebook file to get them —
    # the coupling that makes the ontology file undeletable today.
    scope_selection: ScopeSelection | None = None
    normalisation: Normalisation | None = None

    # ── how a caption is bound to a line item ────────────────────────────────────────────────
    binding: Binding | None = None

    # ── what may not be loaded together, and how a face ties to its note ─────────────────────
    netting_rules: list[NettingRule] = Field(default_factory=list)
    decomposition_rules: list[DecompositionRule] = Field(default_factory=list)

    # ── the leftover buckets, governed once for all of them ──────────────────────────────────
    residual_framework: ResidualFramework | None = None

    # ── conventions that hold everywhere ─────────────────────────────────────────────────────
    global_rules: GlobalRules = Field(default_factory=GlobalRules)

    # ── the arithmetic checks, absorbed from the earlier validation master ───────────────────
    validation: ValidationRuleSet = Field(default_factory=ValidationRuleSet)

    # ── the answers, for scenarios prose cannot pin down ─────────────────────────────────────
    worked_examples: list[WorkedExample] = Field(default_factory=list)

    @model_validator(mode="after")
    def _distinct_rule_ids(self):
        """Two rules of one kind sharing an id is one silently shadowing the other in a report."""
        for label, items in (("netting_rules", self.netting_rules),
                             ("decomposition_rules", self.decomposition_rules)):
            seen: set[str] = set()
            clashes = sorted({r.id for r in items if r.id in seen or seen.add(r.id)})  # type: ignore[func-returns-value]
            if clashes:
                raise ValueError(f"duplicate {label} id(s): {', '.join(clashes)}")
        return self

    # ── what this master says, and whether it can still reach it ─────────────────────────────

    def blocks_present(self) -> dict[str, int]:
        """Which blocks carry anything, and how much. The T4 question, answerable at a glance.

        A rules master is exactly the kind of artefact that can look authoritative while asserting
        nothing — `output_csv_hk` declares no validation block at all, and a missing nested block
        looked identical to a rulebook that needed none. This makes "declares nothing" visible.
        """
        return {
            "scope_selection": 1 if self.scope_selection else 0,
            "normalisation": 1 if self.normalisation else 0,
            "binding": 1 if self.binding else 0,
            "netting_rules": len(self.netting_rules),
            "decomposition_rules": len(self.decomposition_rules),
            "residual_framework": 1 if self.residual_framework else 0,
            "identities": len(self.validation.identities),
            "cross_concept_guards": len(self.validation.cross_concept_guards),
            "worked_examples": len(self.worked_examples),
        }

    def keys_referenced(self) -> set[str]:
        """Every line-item key any rule in this master names."""
        keys = set(self.validation.keys_referenced())
        for rule in self.netting_rules:
            keys.add(rule.target_key)
            keys.update(rule.subtract_keys)
            keys.update(rule.add_keys)
            keys.update(rule.decompose_into)
        for rule in self.decomposition_rules:
            keys.add(rule.face_key)
            keys.update(rule.expected_children)
        return {k for k in keys if k}

    def unknown_keys(self, known: set[str]) -> dict[str, list[str]]:
        """Rule id -> the keys it names that `known` does not define.

        THE CHECK THAT MAKES A SEPARATE MASTER SAFE, and it now covers netting and decomposition
        as well as the identities. A renamed line item leaves a rule naming a key that no longer
        exists; the consumer skips a rule it cannot resolve; and the protection stops running with
        no signal at all. That is the same shape as the balance-sheet identity that had been dead
        for the life of this model because it named `bs_total_assets` while the set defines
        `bs_ca__total_assets`.
        """
        out: dict[str, list[str]] = dict(self.validation.unknown_keys(known))
        for label, rules, fields in (
                ("netting", self.netting_rules,
                 ("target_key", "subtract_keys", "add_keys", "decompose_into")),
                ("decomposition", self.decomposition_rules,
                 ("face_key", "expected_children"))):
            for rule in rules:
                named: list[str] = []
                for field in fields:
                    value = getattr(rule, field, None)
                    named.extend([value] if isinstance(value, str) else list(value or []))
                missing = [k for k in named if k and k not in known]
                if missing:
                    out[f"{label}:{rule.id}"] = missing
        return out


def load_rulebook_rules(data: dict) -> RulebookRules:
    return RulebookRules.model_validate(data)


def from_ontology(ontology, *, rules_key: str = "") -> RulebookRules:
    """Lift every cross-line-item block out of a rulebook into a master of its own.

    THE MIGRATION PATH, and the reason both shapes must stay readable for now: the consumers still
    read the nested blocks — `row_reconstruct.in_force_rules()` reads `scope_selection` and
    `normalisation` straight off a rulebook FILE for every page — so the master is built FROM the
    rulebook rather than replacing it, and the two can be diffed until those consumers move.
    """
    from app.schemas.validation_rules import from_ontology as validation_from_ontology

    key = rules_key or f"{getattr(ontology, 'ontology_key', '')}_rules"
    return RulebookRules(
        rules_key=key,
        target_template_key=getattr(ontology, "target_template_key", ""),
        target_template_version=getattr(ontology, "target_template_version", None),
        locale=getattr(ontology, "locale", "en") or "en",
        scope_selection=getattr(ontology, "scope_selection", None),
        normalisation=getattr(ontology, "normalisation", None),
        binding=getattr(ontology, "binding", None),
        netting_rules=list(getattr(ontology, "netting_rules", None) or []),
        decomposition_rules=list(getattr(ontology, "decomposition_rules", None) or []),
        residual_framework=getattr(ontology, "residual_framework", None),
        global_rules=getattr(ontology, "global_rules", None) or GlobalRules(),
        validation=validation_from_ontology(ontology, validation_key=f"{key}_validation"),
        worked_examples=list(getattr(ontology, "worked_examples", None) or []),
    )
