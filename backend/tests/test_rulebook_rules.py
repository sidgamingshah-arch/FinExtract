"""The rules master: everything a rulebook asserts ACROSS line items, in one artefact.

WHAT THESE DEFEND. Nine kinds of rule belong to no single line item — the arithmetic identities,
the binding order, the entity/period scope selection, the caption-fold pipeline, the residual
framework, the global conventions, netting, decomposition, and the worked examples. Folding them
into the 475-definition line-item set would force a choice between duplicating each rule onto every
key it mentions and hanging it off one arbitrarily.

Splitting them out removes the structural tie between a rule and the keys it names, so the tie is
asserted instead. That is not hypothetical: the balance-sheet identity in `stages/confidence.py`
named `bs_total_assets` while the shipped set defines `bs_ca__total_assets`, so it matched nothing
for the life of this model and said nothing about it.

And the split makes a gap visible that a missing nested block had hidden. Measured below:
`output_csv_hk` — the rulebook that drives the output template — declares NO identities, NO
cross-concept guards, NO netting rules and NO worked examples, while its sibling declares 19, 6, 2
and 8.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_ontology
from app.schemas.ontology import NettingRule
from app.schemas.rulebook_rules import RulebookRules, from_ontology, load_rulebook_rules

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _json(name: str) -> dict:
    return json.loads((TEMPLATES / name).read_text(encoding="utf-8"))


def _known_keys() -> set[str]:
    return {d.key for d in load_line_item_set(_json("output_csv_hk_line_items.json")).items}


# ── the master carries every kind of rule, not just the arithmetic ───────────────────────────────

def test_the_master_is_broader_than_the_validation_block_it_absorbed():
    """A master holding only identities would have been EMPTY for the rulebook that matters."""
    rules = from_ontology(load_ontology(_json("output_csv_hk_ontology.json")))
    present = rules.blocks_present()

    assert present["identities"] == 0, "this rulebook genuinely declares no arithmetic checks"
    # …yet it declares four other kinds of rule, all of which would have had no home.
    assert present["scope_selection"] == 1
    assert present["normalisation"] == 1
    assert present["binding"] == 1
    assert present["residual_framework"] == 1


def test_the_sibling_rulebook_fills_the_blocks_this_one_leaves_empty():
    ours = from_ontology(load_ontology(_json("output_csv_hk_ontology.json"))).blocks_present()
    theirs = from_ontology(load_ontology(_json("hkfrs_hk_china_ontology.json"))).blocks_present()

    for block in ("identities", "cross_concept_guards", "netting_rules", "worked_examples"):
        assert ours[block] == 0
        assert theirs[block] > 0, f"{block} was expected to be populated in the sibling"


def test_blocks_present_makes_declaring_nothing_visible():
    """The T4 question — does this artefact actually assert anything — answerable at a glance.

    A rules master is exactly the kind of thing that can look authoritative while saying nothing.
    """
    empty = RulebookRules().blocks_present()

    assert set(empty.values()) == {0}
    assert "identities" in empty and "binding" in empty


# ── the key-space tie, extended past the identities ──────────────────────────────────────────────

def test_netting_and_decomposition_keys_are_checked_too():
    """A netting rule naming a renamed key is a protection that has stopped running.

    `validation.unknown_keys` only ever looked at identity expressions; netting rules name keys in
    four separate fields and were unchecked.
    """
    rules = RulebookRules(netting_rules=[
        NettingRule(id="n1", target_key="known_one", subtract_keys=["renamed_away"]),
        NettingRule(id="n2", target_key="known_one", add_keys=["known_two"]),
    ])

    unknown = rules.unknown_keys({"known_one", "known_two"})
    assert unknown == {"netting:n1": ["renamed_away"]}


def test_the_sibling_rules_do_not_fit_this_rulebooks_key_space():
    """So a master is only meaningful against the template it declares."""
    theirs = from_ontology(load_ontology(_json("hkfrs_hk_china_ontology.json")))

    unknown = theirs.unknown_keys(_known_keys())
    assert len(unknown) >= 19, "expected every identity plus the netting rules to be unresolvable"


def test_our_own_master_names_no_key_its_set_lacks():
    rules = load_rulebook_rules(_json("output_csv_hk_rules.json"))

    assert rules.unknown_keys(_known_keys()) == {}


def test_keys_referenced_gathers_from_every_rule_kind():
    theirs = from_ontology(load_ontology(_json("hkfrs_hk_china_ontology.json")))

    keys = theirs.keys_referenced()
    assert len(keys) > 40
    assert all(k for k in keys), "no empty strings should reach the key set"


# ── the envelope ─────────────────────────────────────────────────────────────────────────────────

def test_two_netting_rules_cannot_share_an_id():
    with pytest.raises(ValidationError, match="duplicate netting_rules id"):
        RulebookRules(netting_rules=[NettingRule(id="same", target_key="a_one"),
                                     NettingRule(id="same", target_key="b_two")])


def test_the_shipped_masters_load_and_name_a_template():
    for name in ("output_csv_hk_rules.json", "hkfrs_hk_china_rules.json"):
        rules = load_rulebook_rules(_json(name))

        assert rules.rules_key
        assert rules.target_template_key, f"{name} names no key-space"


def test_a_master_round_trips_through_json():
    original = from_ontology(load_ontology(_json("hkfrs_hk_china_ontology.json")))
    back = load_rulebook_rules(json.loads(original.model_dump_json(exclude_none=True)))

    assert back.blocks_present() == original.blocks_present()
    assert back.keys_referenced() == original.keys_referenced()


def test_the_pre_mapping_blocks_are_present_because_they_are_why_the_ontology_file_survives():
    """`scope_selection` and `normalisation` run for EVERY page before any line item exists.

    `row_reconstruct.in_force_rules()` reads them straight off a rulebook FILE, which is the
    coupling that makes the ontology file undeletable today. Carrying them here is the first step
    to that path having somewhere else to read them from.
    """
    rules = load_rulebook_rules(_json("output_csv_hk_rules.json"))

    assert rules.scope_selection is not None
    assert rules.normalisation is not None
