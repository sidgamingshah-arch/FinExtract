"""The two paths to a figure, and which of them publishes.

Eight concepts are reachable both by the GENERIC path (the rulebook: an alias names the caption,
the banner scopes it) and by the COMPLEX path (five hand-written derivations, each assembling the
figure from note datasets through a priority cascade). Nothing used to choose between them: every
derivation stage ran after mapping and called `row.set_value` unconditionally, so the complex path
won silently and the reading it displaced was discarded with no record.

These tests pin the decision now that there is one. The default must reproduce that old behaviour
exactly — `complex` — because the reference filings were graded under it; what changes is that the
decision is visible, per-concept configurable, and that a derivation which LOSES still leaves its
working on the row.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.services.computed_paths import (COMPLEX_SERVICES, FLAG_CORROBORATED, FLAG_DISAGREE,
                                         FLAG_DISPLACED, FLAG_FILLED_GAP, PathPolicy,
                                         policy_from, resolve_write)

KEY = "is_pl__deprec_and_impairment_oper_exp"


def _policy(precedence="complex", by_key=None, enabled=True, services=(), rel=0.01):
    return PathPolicy(complex_enabled=enabled, precedence_default=precedence,
                      by_key=dict(by_key or {}), enabled_services=tuple(services),
                      rel_tolerance=rel)


def _row(value=None, basis="consolidated", period="current"):
    """A row the GENERIC path has (or has not) valued."""
    row = LineItem(source_label="Depreciation", canonical_key=KEY, ordinal=1)
    if value is not None:
        row.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                     basis=Basis(basis), period_label=period))
    return row


# --- the default has to be the old behaviour ----------------------------------------------------

def test_the_default_precedence_is_the_behaviour_the_reference_filings_were_graded_under():
    """`complex`, and it publishes over a printed reading. Changing this default would silently
    re-grade every recorded run, which is why it is asserted rather than assumed."""
    out = resolve_write(_policy(), KEY, _row("100"), "consolidated", "current", Decimal("140"))

    assert out.wrote is True
    assert FLAG_DISPLACED in out.flags, "what the derivation displaced must be recorded"


def test_a_settings_object_predating_these_fields_still_gets_the_old_behaviour():
    class Old:
        pass

    p = policy_from(Old())
    assert p.complex_enabled is True and p.precedence_default == "complex"
    assert p.runs("deprec_impairment") is True


# --- generic ------------------------------------------------------------------------------------

def test_generic_precedence_keeps_the_printed_reading():
    out = resolve_write(_policy("generic"), KEY, _row("100"), "consolidated", "current",
                        Decimal("140"))

    assert out.wrote is False
    assert FLAG_DISAGREE in out.flags
    assert "100" in out.reason and "140" in out.reason, "the reason must name both figures"


def test_generic_precedence_still_says_so_when_the_two_paths_agree():
    out = resolve_write(_policy("generic"), KEY, _row("100"), "consolidated", "current",
                        Decimal("100"))

    assert out.wrote is False
    assert FLAG_CORROBORATED in out.flags and FLAG_DISAGREE not in out.flags


# --- corroborate --------------------------------------------------------------------------------

def test_corroborate_keeps_the_printed_reading_and_flags_the_disagreement():
    out = resolve_write(_policy("corroborate"), KEY, _row("100"), "consolidated", "current",
                        Decimal("140"))

    assert out.wrote is False
    assert out.flags == (FLAG_DISAGREE,)


def test_a_difference_inside_the_reconciliation_band_is_agreement_not_a_finding():
    """The same relative band the reconciler uses, so "these agree" means one thing in both
    places rather than two."""
    out = resolve_write(_policy("corroborate", rel=0.01), KEY, _row("1000"), "consolidated",
                        "current", Decimal("1005"))

    assert FLAG_CORROBORATED in out.flags and FLAG_DISAGREE not in out.flags


# --- the case the old code could not express ----------------------------------------------------

def test_a_derivation_fills_a_gap_the_generic_path_left_under_every_precedence():
    """The old code never knew a second path had answered, so it could not tell "I am overwriting
    a printed figure" from "I am the only source there is". Filling a gap is always right."""
    for precedence in ("complex", "generic", "corroborate"):
        out = resolve_write(_policy(precedence), KEY, _row(None), "consolidated", "current",
                            Decimal("140"))
        assert out.wrote is True, precedence
        assert FLAG_DISPLACED not in out.flags


def test_a_row_the_mapper_created_but_never_valued_is_a_gap_not_a_reading():
    """A row exists for every template line so the statement can show it. Present-but-empty is
    not a competing answer."""
    out = resolve_write(_policy("generic"), KEY, _row(None), "consolidated", "current",
                        Decimal("140"))
    assert out.wrote is True and FLAG_FILLED_GAP in out.flags


def test_a_reading_for_a_different_period_is_not_a_competing_reading():
    row = _row("100", period="prior")
    out = resolve_write(_policy("generic"), KEY, row, "consolidated", "current", Decimal("140"))
    assert out.wrote is True, "the prior-period figure says nothing about the current one"


def test_a_derivation_that_produced_nothing_never_writes():
    out = resolve_write(_policy(), KEY, _row("100"), "consolidated", "current", None)
    assert out.wrote is False and out.flags == ()


# --- the configuration surface ------------------------------------------------------------------

def test_a_per_concept_override_beats_the_global_default():
    """WHICH concepts is a deployment decision and lives in config.toml; WHETHER is the admin's
    switch. Both exist, so the per-key answer has to win."""
    policy = _policy("complex", by_key={KEY: "generic"})

    assert policy.precedence_for(KEY) == "generic"
    assert policy.precedence_for("bs_ca__other_receivables_cp") == "complex"
    assert resolve_write(policy, KEY, _row("100"), "consolidated", "current",
                         Decimal("140")).wrote is False


def test_an_unrecognised_precedence_falls_back_rather_than_failing_a_run():
    assert _policy("complex", by_key={KEY: "nonsense"}).precedence_for(KEY) == "complex"
    assert _policy("nonsense").precedence_for(KEY) == "complex"


def test_switching_the_complex_path_off_stops_every_derivation():
    """The measurement this exists for: what the rulebook alone can do."""
    policy = _policy(enabled=False)
    assert all(not policy.runs(s) for s in COMPLEX_SERVICES)


def test_naming_services_restricts_the_complex_path_to_those():
    policy = _policy(services=("deprec_impairment",))
    assert policy.runs("deprec_impairment") is True
    assert policy.runs("sales_revenues") is False
    assert policy.runs("contingent_liabilities") is False


def test_an_empty_service_list_means_all_of_them_not_none_of_them():
    """The same convention as `llm_focus_keys`: empty is "no restriction", not "nothing"."""
    policy = _policy(services=())
    assert all(policy.runs(s) for s in COMPLEX_SERVICES)
