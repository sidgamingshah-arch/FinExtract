"""Two invariants the balance-sheet revision broke while the whole suite stayed green.

Neither restates a schema rule. ``validate_ontology_against_template`` checks that every CONCEPT key
resolves; nothing checked whether a rulebook's several statements of one concept's SECTION agree, and
nothing checked whether two declarations assert the same arithmetic.

Note what is deliberately NOT asserted here: that a rulebook's section ids equal the template's node
ids. They need not — ``mapping.section_token_of_scope`` reads the section name off the END of a scope
id, so ``bs_s3_current_liabilities`` and ``bs_s5_current_liabilities`` are one section to the gate,
and the shipped cash-flow sections are spelled differently in the two files today. Asserting equality
would be inventing a rule the code does not have.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.loader import load_ontology, load_template, resolve_inherits
from app.services.mapping import (CONCEPT_FAMILIES, section_of_banner,
                                  section_token_of_scope)
from app.services.structural_checks import ontology_identities, relations

_SAMPLES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
_ONTOLOGIES = ("hkfrs_hk_china_ontology.json", "hkfrs_hk_china_ontology.json")


@pytest.fixture(scope="module")
def template():
    return load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize("filename", _ONTOLOGIES)
def test_a_concept_is_scoped_to_one_section_by_every_field_that_names_one(filename):
    """A residual whose policy names a different section from the one it inherits sweeps nothing.

    THE DEFECT THIS CLOSES, measured: renaming the balance sheet's section ids and following the
    rename through ``section_defaults``, its ``section_scope`` and every concept's ``inherits`` still
    left two ``residual_policy.section_scope`` values on the old names. ``stages.residual`` UNIONS a
    residual's inherited scope with its policy's, and a residual scoped to two sections has no
    candidate set at all — prohibition 5, "never spans sections" — so both balance-sheet residuals
    stopped sweeping and every unclaimed current-liabilities row came back ``residual_ineligible``
    instead of reaching its section's Others. Nothing in the schema or the loader says a word about
    it: an ``inherits`` naming no section is refused on the WRITE path, and these were not
    ``inherits``. Sixteen residual tests failed with a section id in the message and none of them
    named the field that was wrong.
    """
    raw = resolve_inherits(json.loads((_SAMPLES / filename).read_text(encoding="utf-8")))
    disagreements = []
    for m in raw.get("mappings") or []:
        inherited = {s for s in (m.get("section_scope") or []) if s}
        declared = ((m.get("residual_policy") or {}).get("section_scope")
                    if isinstance(m.get("residual_policy"), dict) else None)
        if declared and inherited and declared not in inherited:
            disagreements.append((m.get("canonical_key"), sorted(inherited), declared))
    assert disagreements == [], (
        f"{filename}: residual_policy.section_scope disagrees with the inherited section_scope, "
        f"which leaves the residual spanning two sections and sweeping neither: {disagreements}")


def _assertion(rel) -> tuple:
    """What a relation ASSERTS, independent of how it was spelled or what it is called.

    Two declarations of one fact differ in ``id`` and agree on everything here, which is why the id
    cannot be the identity: ``identity:bs_balances`` in the template and
    ``ontology_identity:bs_balance`` in the rulebook were the same equation under two names.
    """
    signs = rel.signs or tuple(1 if rel.op == "sum" or i == 0 else -1
                               for i in range(len(rel.components)))
    return (rel.target, tuple(sorted(zip(rel.components, signs))))


@pytest.mark.parametrize("filename", _ONTOLOGIES)
def test_no_two_identities_assert_the_same_arithmetic(template, filename):
    """One equation, one identity — or coverage counts it twice and one break raises two cards.

    THE DEFECT THIS CLOSES: the template declared the footing as ``bs_balances`` and the v2 rulebook
    declared the identical equation as ``bs_balance``. Both resolved, both evaluated, both entered
    the coverage denominator, so "N relations passed" quoted a denominator with a duplicate in it.

    IDENTITIES only, not rollups. A rulebook identity restating a template rollup is the shipped
    design — ten pairs do it across the three statements, and they are how a relation the template can
    only declare as a blocking rollup gets an authored severity and note. Widening this to rollups
    would be asserting a design decision nobody made.
    """
    raw = json.loads((_SAMPLES / filename).read_text(encoding="utf-8"))
    ont = load_ontology(raw, resolve=raw.get("schema_version") == 2)
    seen: dict[tuple, list[str]] = {}
    for rel in relations(template) + ontology_identities(template, ont):
        if rel.broken or not rel.components or rel.kind == "rollup":
            continue
        seen.setdefault(_assertion(rel), []).append(rel.id)
    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    assert duplicates == {}, f"{filename}: one equation declared twice: {duplicates}"


# --- what the income-statement and cash-flow revision could break silently ----------------------

@pytest.mark.parametrize("filename", _ONTOLOGIES)
def test_one_sweep_bucket_per_section(filename):
    """Two residuals in one section leaves one of them permanently unreachable.

    THE DEFECT THIS CLOSES: the revised cash flow's spec marks three catch-all lines inside the
    operating section — ``other_non_cash_adjustments``, ``other_working_capital_movements`` and the
    existing ``others``. ``stages/residual._sweep`` keys its usable residuals as
    ``{r.section: r}``, so the last one authored wins and the other two never sweep a row: they
    would sit on the statement looking like catch-alls and stay empty on every filing, and WHICH one
    survived would depend on position in the JSON file. Both new lines are ordinary concepts with
    their own captions instead.
    """
    raw = json.loads((_SAMPLES / filename).read_text(encoding="utf-8"))
    per_section: dict[str, list[str]] = {}
    for m in raw.get("mappings") or []:
        policy = m.get("residual_policy")
        if not isinstance(policy, dict):
            continue
        per_section.setdefault(policy.get("section_scope") or "", []).append(m["canonical_key"])
    shared = {sec: keys for sec, keys in per_section.items() if len(keys) > 1}
    assert shared == {}, (
        f"{filename}: these sections declare more than one residual, and the sweep can only reach "
        f"one per section, so the rest are unreachable: {shared}")


@pytest.mark.parametrize("filename", _ONTOLOGIES)
def test_a_caption_printed_under_two_sections_of_one_statement_is_a_declared_family(filename):
    """The same caption in two sections needs the family, or whichever concept scores first takes it.

    THE DEFECT THIS CLOSES: the revised cash flow gives the OPERATING section its own "Interest
    paid" concept, because HKAS 7.33 lets a filing classify it as operating or financing. The
    financing one already existed and the captions are byte-identical in English and Chinese, so
    until ``CONCEPT_FAMILIES`` carried the pair the two were one caption with two homes — and the
    family is also what lets a banner RE-ROUTE a refused answer to the right leaf
    (``mapping.family_leaf_named_by``). Nothing else in the schema notices: both concepts are valid,
    both resolve, and the wrong one simply wins by score.

    Same statement only. A key namespace repeated ACROSS statements (``interest_income`` in the P&L
    and in the cash flow) is separated by the statement constraint before the section gate is
    reached. Residuals are excluded — ``alias_matching`` is disabled on them, so they never compete
    for a caption — and they are recognised the way the rest of the engine recognises them, by the
    ``__others`` suffix (``residual._sections_from_template``, the zero-fill test in
    ``structural_checks``). Not by ``residual_policy``, which only the v2 file states, and not by
    ``value_scope``, which the v1 file states on some of its residuals and not others.
    """
    raw = json.loads((_SAMPLES / filename).read_text(encoding="utf-8"))
    familied = {k for _, members in CONCEPT_FAMILIES for k in members}
    by_suffix: dict[str, set[str]] = {}
    for m in raw.get("mappings") or []:
        key = m["canonical_key"]
        if "__" not in key or key.endswith("__others"):
            continue
        namespace, suffix = key.split("__", 1)
        by_suffix.setdefault(f"{namespace.split('_', 1)[0]}:{suffix}", set()).add(key)
    undeclared = {s: sorted(keys) for s, keys in by_suffix.items()
                  if len(keys) > 1 and not keys <= familied}
    assert undeclared == {}, (
        f"{filename}: one caption, two sections of the same statement, no collision family: "
        f"{undeclared}")


def test_the_oci_section_is_not_read_as_the_revenue_section():
    """``pl_s8_other_comprehensive_income`` ends in "income", and so does the revenue section's token.

    THE DEFECT THIS CLOSES, exactly: ``section_token_of_scope`` reads the token off the END of a
    scope id, and ``SECTION_WORDS`` carried no entry for other comprehensive income — so the new
    section resolved to ``income``, the REVENUE section. Every OCI concept would have been admitted
    under a revenue banner and REFUSED under its own, at full confidence, with nothing in the
    rulebook or the schema to show it: the id looks right, the concepts load, and the gate is
    reading a different section from the one the author wrote.

    The third assertion is the other half. The OCI entry has to be tested BEFORE the attribution
    entry, which matches the bare word "comprehensive" — but not so broadly that it steals the
    attribution banner it is not for.
    """
    assert section_token_of_scope("pl_s8_other_comprehensive_income") == \
        "other_comprehensive_income"
    assert section_of_banner("Other comprehensive income") == "other_comprehensive_income"
    assert section_of_banner("其他综合收益") == "other_comprehensive_income"
    assert section_of_banner("Total comprehensive income attributable to owners of the parent") == \
        "total_comprehensive_income_attributable_to"
    # And the revenue section still resolves to itself.
    assert section_token_of_scope("pl_s1_income") == "income"
