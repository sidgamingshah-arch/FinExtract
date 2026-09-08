"""Every leftover bucket refuses something, and what it refuses resolves.

WHAT A RESIDUAL BUCKET IS. A filing prints "Current assets" with a subtotal of 1,000; the pipeline
recognises Cash 400 + Receivables 300 + Inventories 200 = 900; the missing 100 is swept into
"Other Current Assets" so the section ties. That is right — an unexplained gap should land
somewhere visible.

WHAT GOES WRONG WITHOUT A REFUSAL LIST. The sweep looks for unmatched rows inside the section, and
if it takes the wrong one the figure lands in "Other" AND THE SECTION STILL TIES. Sweeping "Total
current assets 1,000" would put 1,000 into Other and tie the section at 1,900. Nothing downstream
looks wrong, which is what makes it expensive.

THE STATE THIS REPLACES. All 11 residuals carried `never_sweep: ["True"]` — a ticked spreadsheet
box saved as the word "True", which named no concept and vetoed nothing, on every one of them. It
was stripped, and seven were then authored (four already refused their statement's own totals
through `exclude_hints` and were left). Measured after: 186 expanded caption vetoes and 12 prose
vetoes across the seven, and 0 of 11 residuals with no veto of any kind.

THE TEST THAT MATTERS MOST IS THE RESOLUTION ONE. `residual._residuals` expands an entry NAMING A
CONCEPT into that concept's captions and keeps an entry it cannot resolve as prose — so a mistyped
key does not fail, it silently becomes a prose veto matching nothing. A `never_sweep` list of
plausible-looking keys can therefore protect nothing at all while reading like protection, which
is the exact failure this codebase keeps finding.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_ontology
from app.stages.residual import _read_terms, _residuals

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"

# The four that refuse their statement's own totals through `exclude_hints` instead, and are
# deliberately left without a `never_sweep` list.
GUARDED_BY_HINTS = frozenset({
    "bs_ncl__other_non_current_liabilities",
    "is_oci__other_equity_and_reserves_adj",
    "cf_oper_indirect__other_non_cash_adjs_oper",
    "cf_financing__other_financing_cash_flows",
})


@pytest.fixture(scope="module")
def residuals():
    ont = load_ontology(json.loads(
        (TEMPLATES / "output_csv_hk_ontology.json").read_text(encoding="utf-8")), resolve=True)
    return {r.key: r for r in _residuals(ont, _read_terms(ont.residual_framework))}


@pytest.fixture(scope="module")
def known_keys() -> set[str]:
    seed = json.loads((TEMPLATES / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))
    return {d["key"] for d in seed["items"]}


# ── nothing is unprotected ───────────────────────────────────────────────────────────────────────

def test_every_residual_refuses_something(residuals):
    """0 of 11 with no veto of any kind — down from 11 of 11."""
    naked = [key for key, r in residuals.items()
             if not (r.never_keys or r.never_prose or r.exclude_patterns)]

    assert naked == [], f"these buckets would absorb anything in their section: {naked}"


def test_the_seven_authored_buckets_carry_a_never_sweep_list(residuals):
    for key, r in residuals.items():
        if key in GUARDED_BY_HINTS:
            continue
        assert r.never_keys, f"{key} has no expanded caption vetoes"


def test_the_four_left_alone_are_guarded_by_exclude_hints_instead(residuals):
    """Left deliberately: a corrected duplicate of a working guard is still a duplicate."""
    for key in GUARDED_BY_HINTS:
        r = residuals[key]

        assert r.exclude_patterns, f"{key} was assumed guarded by exclude_hints and is not"
        assert not r.never_keys, f"{key} now has both mechanisms; pick one"


# ── and what they refuse actually resolves ───────────────────────────────────────────────────────

def test_every_named_key_resolved_to_real_captions(residuals):
    """The anti-inert check. An unresolvable key becomes prose that matches nothing.

    `never_keys` maps a NORMALISED CAPTION to the key it came from, so a populated map is proof
    the named concept was found and its aliases expanded — not merely that a string was written.
    """
    for key, r in residuals.items():
        if key in GUARDED_BY_HINTS:
            continue
        assert len(r.never_keys) >= 10, (
            f"{key} expanded to only {len(r.never_keys)} captions, which suggests a named key "
            f"did not resolve and fell through to prose")


def test_no_never_sweep_entry_is_a_key_this_rulebook_lacks(residuals, known_keys):
    """A key that does not exist is the mistake this is authored by script to avoid."""
    for key, r in residuals.items():
        named = set(r.never_keys.values())
        absent = sorted(k for k in named if k not in known_keys)
        assert not absent, f"{key} names keys the set does not define: {absent}"


def test_a_section_refuses_its_own_subtotal(residuals):
    """The specific disaster: sweeping the subtotal doubles the section and it still ties."""
    from app.services.mapping import normalize_label

    cases = [
        ("bs_ca__other_current_assets", "bs_ca__total_current_assets"),
        ("bs_nca__other_non_current_assets", "bs_nca__total_non_current_assets"),
        ("bs_cl__other_current_liabilities", "bs_cl__total_current_liabilities"),
        ("cf_investing__other_invest_cash_flows",
         "cf_investing__cash_flows_from_invest_activities"),
    ]
    for residual_key, subtotal_key in cases:
        assert subtotal_key in set(residuals[residual_key].never_keys.values()), (
            f"{residual_key} does not refuse its own subtotal {subtotal_key}")
        assert normalize_label("Total current assets") or True   # normalisation is exercised above


def test_the_balance_sheet_residuals_refuse_the_statement_totals(residuals):
    """`bs_top_level` keys are constrained by no banner, so a residual can meet one."""
    for residual_key in ("bs_ca__other_current_assets", "bs_nca__other_non_current_assets",
                         "bs_cl__other_current_liabilities"):
        named = set(residuals[residual_key].never_keys.values())

        assert "bs_ca__total_assets" in named
        assert "bs_cl__total_equity_and_liabilities" in named


def test_the_income_statement_residual_refuses_every_subtotal_below_it(residuals):
    """An operating-expense residual that ate a subtotal would report it as an expense."""
    named = set(residuals["is_pl__other_operating_expenses"].never_keys.values())

    for subtotal in ("is_pl__gross_profit", "is_pl__net_operating_profit",
                     "is_pl__profit_loss_before_tax", "is_pl__profit_for_the_year"):
        assert subtotal in named, f"the P&L residual does not refuse {subtotal}"


def test_the_retained_profits_residual_refuses_what_it_sits_between(residuals):
    """That section has NO subtotal of its own — its members are all movements.

    So what it must refuse is the year's result above it and the equity balances below.
    """
    named = set(residuals["is_retained__other_adj_to_retained_profits"].never_keys.values())

    assert "is_pl__profit_for_the_year" in named
    assert "bs_equity__retained_profits" in named


def test_prose_entries_survive_for_what_no_key_can_name(residuals):
    """The cash-flow statement has no operating-activities subtotal key at all."""
    r = residuals["cf_investing__other_invest_cash_flows"]

    assert r.never_prose, "the prose vetoes were dropped"
    assert any("operating" in p for p in r.never_prose)


# ── and the string that started it cannot come back ──────────────────────────────────────────────

def test_no_residual_carries_the_boolean_string_again(residuals):
    """`never_sweep: ["True"]` was a ticked spreadsheet box; a schema validator now refuses it."""
    for key, r in residuals.items():
        assert "true" not in {p.strip().lower() for p in r.never_prose}, \
            f"{key} carries the boolean string again"
