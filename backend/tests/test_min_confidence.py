"""There is ONE accept bar, and it is the global one. No per-line-item override.

THE DECISION THIS PINS. `LineItemDef` used to carry `min_confidence_to_auto_accept: float = 0.85`.
It was read by nothing — not by the ported matcher, and NOT BY THE INCUMBENT, whose four accept
decisions all compare against `settings.extraction.auto_accept_confidence` (default 0.80) at
mapping.py:1682, :1863, :1919 and :2219. The field carried 0.85 on all 462 projected definitions
and 0 of 475 overrode it.

It was REMOVED rather than wired up, deliberately. Enforcing it would have invented a policy
nobody authored, and an expensive one: the per-item default (0.85) was stricter than the live
global bar (0.80), so switching it on would newly route to review every row scoring in between.

These tests exist because the field could plausibly come back — a rulebook still declares it, so a
future projection change could carry it in again and it would once more read like a control that
controls nothing. That is the failure mode this whole exercise keeps finding, so it gets a test
rather than a comment.
"""
from __future__ import annotations

import json
import pathlib

from app.config import get_settings
from app.schemas.line_items import LineItemDef, LineItemSet, load_line_item_set
from app.services import line_item_matching
from app.services.line_item_matching import LineItemMatcher
from app.services.ontology_projection import SAME

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


# ── the field is gone, and stays gone ────────────────────────────────────────────────────────────

def test_a_line_item_declares_no_accept_bar_of_its_own():
    assert "min_confidence_to_auto_accept" not in LineItemDef.model_fields


def test_the_projection_does_not_carry_the_rulebook_copy_back_in():
    """A rulebook still declares the field; the projection must not reintroduce it.

    `OntologyMapping.min_confidence_to_auto_accept` is still there — that is the rulebook's own
    schema and its data has the field — so the only thing stopping it landing on 462 definitions
    again is this omission from the passthrough list.
    """
    assert "min_confidence_to_auto_accept" not in SAME


def test_no_shipped_definition_carries_the_field():
    raw = json.loads(SEED.read_text(encoding="utf-8"))

    carriers = [d["key"] for d in raw["items"] if "min_confidence_to_auto_accept" in d]
    assert not carriers, f"{len(carriers)} definitions carry a removed field, e.g. {carriers[:3]}"


def test_a_match_result_surfaces_no_per_item_bar():
    m = LineItemMatcher(LineItemSet(items=[LineItemDef(key="a", aliases=["Cash"])]))
    got = m.match("Cash")

    assert not hasattr(got, "min_confidence_to_auto_accept")
    assert not hasattr(got, "clears_auto_accept_bar")


def test_the_matcher_holds_no_helper_for_it_either():
    assert not hasattr(LineItemMatcher, "_accept_bar_of")


# ── and the global bar is real ───────────────────────────────────────────────────────────────────

def test_the_global_accept_bar_exists_and_is_the_one_in_force():
    """Stated so the removal cannot be read as "there is no bar at all"."""
    bar = get_settings().extraction.auto_accept_confidence

    assert bar is not None
    assert 0.0 < bar <= 1.0


def test_the_removed_default_was_stricter_than_the_live_bar():
    """Why enforcement would have been a change, not a no-op.

    The per-item default was 0.85 and the global bar is 0.80, so wiring the field up would have
    newly flagged every row scoring in that band — a policy decision disguised as plumbing.
    """
    assert get_settings().extraction.auto_accept_confidence < 0.85


def test_the_deterministic_tiers_emit_the_four_confidences_the_bar_is_judged_against():
    """Context for anyone re-adding a per-item bar: these are the only values it would see."""
    m = LineItemMatcher(LineItemSet(items=[
        LineItemDef(key="exact", label="Trade receivables"),
        LineItemDef(key="one_rule", regex_hints=["turnover"]),
        LineItemDef(key="rule_a", regex_hints=["deposit"], match_priority=10),
        LineItemDef(key="rule_b", regex_hints=["deposit"], match_priority=90),
    ]))

    assert m.match("Trade receivables").confidence == 1.0
    assert m.match("Total turnover").confidence == 0.95
    assert m.match("Short-term deposit").confidence == 0.6
    assert m.match("Nothing claims this").confidence == 0.0


def test_the_shipped_set_still_loads_and_matches_after_the_removal():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")))
    m = LineItemMatcher(st)

    # 527, not 543: the live configuration was exported into the shipped seed — see
    # `test_retired_derivations.test_the_shipped_set_is_the_configuration_in_force`.
    assert len(st.items) == 531  # 531 since the two direct-method TAX face parts split the template's single Income Taxes Paid(Direct) column, as the two 营业外 parts before them split Other Non-Operating Inc(Exp)
    assert m.match("total assets", "balance_sheet", "CURRENT ASSETS").key == "bs_ca__total_assets"


def test_the_module_docstring_records_why_rather_than_only_what():
    """A removal with no rationale invites the next person to add it straight back."""
    doc = line_item_matching.__doc__ or ""

    assert "NO PER-CONCEPT ACCEPT BAR" in doc
    assert "auto_accept_confidence" in doc
