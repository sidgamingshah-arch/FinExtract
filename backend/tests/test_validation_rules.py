"""Validation rules as a master of their own, bound to a key-space.

WHAT THESE DEFEND. Splitting the rules out of the rulebook removes the structural tie between an
identity and the keys it names, so the tie has to be asserted instead. Measured on the shipped
files: all 19 identities extracted from `hkfrs_hk_china` name keys the 475-definition
`output_csv_hk` set does not define — so a master applied to the wrong key-space reports every
identity as unevaluable, and `structural_checks.ontology_identities` drops a relation whose keys it
cannot resolve. The arithmetic that would have caught a mis-mapped figure stops running, silently.

And the asymmetry the split made visible: `output_csv_hk`, the rulebook that actually drives the
output template, declares ZERO validation rules. A missing nested block looked exactly like a
rulebook that needed none.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_ontology
from app.schemas.validation_rules import (CrossConceptGuard, ValidationIdentity,
                                          ValidationRuleSet, from_ontology,
                                          load_validation_rules)

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _json(name: str) -> dict:
    return json.loads((TEMPLATES / name).read_text(encoding="utf-8"))


# ── expressions name keys, and the keys are checkable ────────────────────────────────────────────

def test_the_keys_an_expression_names_are_recoverable():
    i = ValidationIdentity(id="x", expr="bs_net_assets = bs_equity__total_equity")

    assert i.keys_referenced() == ["bs_net_assets", "bs_equity__total_equity"]


def test_arithmetic_words_are_not_mistaken_for_keys():
    """`abs(...)` is arithmetic; reading it as a key would report a phantom unknown."""
    i = ValidationIdentity(id="x", expr="abs(pl_gross_profit - pl_income__revenue) < 1")

    assert i.keys_referenced() == ["pl_gross_profit", "pl_income__revenue"]


def test_a_key_named_twice_is_listed_once_in_order():
    i = ValidationIdentity(id="x", expr="a_total = b_part + c_part + b_part")

    assert i.keys_referenced() == ["a_total", "b_part", "c_part"]


def test_unknown_keys_are_reported_per_identity():
    rules = ValidationRuleSet(identities=[
        ValidationIdentity(id="good", expr="known_one = known_two"),
        ValidationIdentity(id="stale", expr="known_one = renamed_away"),
    ])

    assert rules.unknown_keys({"known_one", "known_two"}) == {"stale": ["renamed_away"]}


def test_a_master_whose_keys_all_resolve_reports_nothing():
    rules = ValidationRuleSet(identities=[ValidationIdentity(id="ok", expr="a_one = b_two")])

    assert rules.unknown_keys({"a_one", "b_two"}) == {}


# ── the envelope ─────────────────────────────────────────────────────────────────────────────────

def test_two_identities_cannot_share_an_id():
    """One identity silently replacing the other in a report is worse than a load error."""
    with pytest.raises(ValidationError, match="duplicate identity id"):
        ValidationRuleSet(identities=[ValidationIdentity(id="same", expr="a_one = b_two"),
                                      ValidationIdentity(id="same", expr="c_one = d_two")])


def test_severity_defaults_to_warning_not_blocking():
    """The arithmetic cannot tell "cannot be right" from "worth a look", so it must be authored.

    Defaulting to blocking would turn every unclassified difference into a refusal to publish.
    """
    assert ValidationIdentity(id="x", expr="a_one = b_two").severity == "warning"


def test_a_master_round_trips_through_json():
    rules = ValidationRuleSet(
        validation_key="k", target_template_key="t",
        identities=[ValidationIdentity(id="i", expr="a_one = b_two", severity="blocking")],
        cross_concept_guards=[CrossConceptGuard(rule="never load a gross parent with its parts")],
        section_reconciliation="reported - dedicated - residual = 0")
    back = load_validation_rules(json.loads(rules.model_dump_json()))

    assert back.identities[0].severity == "blocking"
    assert back.cross_concept_guards[0].rule.startswith("never load")
    assert back.section_reconciliation


# ── lifting the nested block out of a rulebook ───────────────────────────────────────────────────

def test_the_hkfrs_rulebook_yields_its_nineteen_identities():
    rules = from_ontology(load_ontology(_json("hkfrs_hk_china_ontology.json")))

    assert len(rules.identities) == 19
    assert len(rules.cross_concept_guards) == 6
    assert rules.target_template_key == "hkfrs_hk_china_v1"
    assert any(i.severity == "blocking" for i in rules.identities)


def test_the_output_csv_rulebook_declares_no_validation_at_all():
    """Stated as a test because a missing nested block looked exactly like needing none.

    This is the rulebook that drives the output template, so its arithmetic coverage is the
    coverage that matters — and it is currently the output template's own rollups alone.
    """
    rules = from_ontology(load_ontology(_json("output_csv_hk_ontology.json")))

    assert rules.identities == []
    assert rules.cross_concept_guards == []


# ── the key-space tie, which is what makes a separate master safe ────────────────────────────────

def test_the_two_masters_are_written_in_different_key_spaces():
    """All 19 HKFRS identities name keys the output_csv_hk set does not define.

    So a master is only meaningful against the template it declares. Applying one to the other's
    keys reports every identity as unevaluable, and an unevaluable relation is a check that has
    silently stopped running.
    """
    hkfrs = from_ontology(load_ontology(_json("hkfrs_hk_china_ontology.json")))
    output_keys = {d.key for d in load_line_item_set(
        _json("output_csv_hk_line_items.json")).items}

    unknown = hkfrs.unknown_keys(output_keys)
    assert len(unknown) == len(hkfrs.identities) == 19


def test_the_shipped_masters_load_and_bind_to_a_template():
    for name in ("output_csv_hk_validation.json", "hkfrs_hk_china_validation.json"):
        rules = load_validation_rules(_json(name))

        assert rules.target_template_key, f"{name} names no template key-space"
        assert rules.validation_key


def test_the_output_master_names_no_key_its_own_set_lacks():
    """The tie asserted for the master that matters, so a rename cannot silently disarm a check."""
    rules = load_validation_rules(_json("output_csv_hk_validation.json"))
    known = {d.key for d in load_line_item_set(_json("output_csv_hk_line_items.json")).items}

    assert rules.unknown_keys(known) == {}
