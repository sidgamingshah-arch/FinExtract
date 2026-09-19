"""The structural checks assert the arithmetic the template DECLARES, and nothing it does not.

Three ways the evaluator was asserting equations nobody wrote, each of them reporting a break on
every filing in the corpus and none of them a defect in the filing:

* a rollup that names a ``reported_total_key`` is a RESIDUAL — "my printed total less the children
  that found a more specific home" — and reading it as ``target == sum(children)`` asserts the
  remainder is always zero. 30 of the output-CSV template's 61 rollups are shaped that way,
  including the section sweep buckets, and the one on ``bs_ca__trade_and_other_receivables``
  reported 1,525% on 000709;
* a plain ``sum`` needs its components' SIGNS, and on a statement that nets they are not
  established: the rulebook says expenses are stored negative, a CAS filing prints them unsigned,
  and the output-CSV rulebook declares ``negative_expected`` on none of its 462 concepts. So
  ``gross_profit = revenue + cost_of_sales`` reported 9,098% with both figures positive;
* a section's subtotal has to be the one that covers THE SECTION. Picking the first candidate took
  a statement total nested among the members (``bs_ca__total_assets`` for the current-asset
  section) and reported the section short by everything outside it.

And one ordering rule the fixes needed: a premise a relation cannot satisfy must never be reported
ahead of a fact about the FILING that makes the premise moot.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.loader import load_ontology, load_template
from app.services.structural_checks import evaluate_structure, relations, section_relations

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
OUTPUT_CSV_TEMPLATE = _SAMPLES / "output_csv_hk_v1_template.json"
OUTPUT_CSV_LINE_ITEMS = _SAMPLES / "output_csv_hk_line_items.json"
HKFRS_ONTOLOGY = _SAMPLES / "hkfrs_hk_china_ontology.json"


@pytest.fixture(scope="module")
def shipped():
    return load_template(json.loads(OUTPUT_CSV_TEMPLATE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def shipped_rulebook():
    from app.schemas.line_items import load_line_item_set
    from app.services.working_view import build_working_view

    raw = json.loads(OUTPUT_CSV_LINE_ITEMS.read_text(encoding="utf-8"))
    return build_working_view(load_line_item_set(raw, resolve=True))


def _node(node_id: str, role: str = "line", rollup: list[str] | None = None,
          reported_total_key: str | None = None) -> dict:
    n = {"node_id": node_id, "canonical_key": node_id, "label": node_id, "role": role}
    if rollup is not None:
        n["rollup"] = {"op": "sum", "children": rollup}
        if reported_total_key:
            n["rollup"]["reported_total_key"] = reported_total_key
            n["rollup"]["reported_total_op"] = "diff"
    return n


def _bs_template(reported_total_key: str | None = None) -> dict:
    """"Other current assets" with two children broken out of it — the residual shape."""
    return {
        "template_key": "t", "name": "t",
        "statements": [{
            "type": "balance_sheet",
            "sections": [{
                "node_id": "bs_ca", "canonical_key": "bs_ca", "label": "Current assets",
                "role": "header",
                "children": [
                    _node("prepayments"), _node("deposits"),
                    _node("other_current_assets", "line", ["prepayments", "deposits"],
                          reported_total_key),
                ],
            }],
            "identities": [],
        }],
    }


def _pl_template() -> dict:
    return {
        "template_key": "t", "name": "t",
        "statements": [{
            "type": "profit_and_loss",
            "sections": [{
                "node_id": "is_pl", "canonical_key": "is_pl", "label": "Income", "role": "header",
                "children": [
                    _node("revenue"), _node("cost_of_sales"),
                    _node("gross_profit", "subtotal", ["revenue", "cost_of_sales"]),
                ],
            }],
            "identities": [],
        }],
    }


def _facts(**by_key: object) -> list[LineItem]:
    out = []
    for key, num in by_key.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(str(num)), value_raw=Decimal(str(num)),
                                    basis=Basis.CONSOLIDATED, period_label="current"))
        out.append(li)
    return out


def _one(report, rule_id: str):
    return next((r for r in report.results if r.rule_id == rule_id), None)


def _rulebook(sign_conventions: dict[str, str], sections: tuple[str, ...] = ()):
    """A minimal resolved rulebook that declares nothing but its sections' sign conventions."""
    raw = {
        "schema_version": 2, "ontology_key": "t", "target_template_key": "t", "locale": "en",
        "section_defaults": {s: {"sign_convention": c} for s, c in sign_conventions.items()},
        "mappings": [{"canonical_key": k, "label": k, "section_scope": [s]}
                     for s, keys in ((s, sections) for s in sign_conventions) for k in keys],
    }
    return load_ontology(copy.deepcopy(raw), resolve=True)


# ── a residual is not a sum ───────────────────────────────────────────────────────────────────

def test_a_rollup_that_names_a_reported_total_declares_no_equality():
    """`published = my printed total − the children broken out` is a SUBTRACTION, and the children
    are what is left OUT of the figure. Asserting they add to it asserts the remainder is zero."""
    plain = relations(load_template(_bs_template()))
    assert [r.id for r in plain] == ["rollup:other_current_assets"]

    residual = relations(load_template(_bs_template("other_current_assets")))
    assert residual == [], "a residual bucket declares no equation to check"


def test_the_shipped_template_has_thirty_residual_rollups_and_none_of_them_is_asserted(shipped):
    """THE MEASUREMENT, so a template revision that turns a residual into a real sum is visible.

    `tests/test_formula_in_config.test_no_residual_bucket_was_turned_into_a_sum` forbids the same
    mistake on the configuration side, where writing `terms` for a residual "would turn the
    unexplained remainder into the sum of the parts, which is a different figure and a
    plausible-looking one". This is that rule on the relation side.
    """
    residual, plain = [], []
    for st in shipped.statements:
        for node in shipped._walk(st.sections):
            rollup = node.rollup
            if rollup is None or not rollup.children or node.canonical_key is None:
                continue
            (residual if rollup.reported_total_key else plain).append(node.canonical_key)

    assert len(residual) == 30 and len(plain) == 31
    # Every one of the 30 points at ITSELF: the figure is its own printed total less the breakouts.
    asserted = {r.target for r in relations(shipped) if r.kind == "rollup"}
    assert asserted & set(residual) == set()
    assert "bs_ca__other_current_assets" in residual and "bs_cl__other_current_liabilities" in residual
    # And the real sums are still asserted, or this fix would have removed the whole check.
    assert set(plain) <= asserted and "bs_nca__net_intangibles" in asserted


# ── a plain sum needs its signs ───────────────────────────────────────────────────────────────

def test_a_sum_on_a_netting_statement_is_not_asserted_where_the_signs_are_not_declared():
    """Revenue +1000 and an UNSIGNED cost of 600 make `gross_profit` 1600 by flat addition, and
    the printed 400 then reads as a 300% break. The premise is reported instead."""
    tpl = load_template(_pl_template())
    ont = _rulebook({"is_pl": "either"}, ("revenue", "cost_of_sales", "gross_profit"))

    res = _one(evaluate_structure(tpl, _facts(revenue=1000, cost_of_sales=600, gross_profit=400),
                                  ontology=ont), "rollup:gross_profit")
    assert res.status == "skipped"
    assert res.details["reason"] == "component_signs_not_established"
    assert res.details["sections"] == ["is_pl"]


def test_a_section_the_rulebook_declares_one_signed_is_still_checked():
    """`positive_expected` says the members are magnitudes however the filing prints them, which is
    exactly the declaration a flat sum needs — so the gate must not swallow those."""
    tpl = load_template(_pl_template())
    ont = _rulebook({"is_pl": "positive_expected"},
                    ("revenue", "cost_of_sales", "gross_profit"))

    res = _one(evaluate_structure(tpl, _facts(revenue=1000, cost_of_sales=600, gross_profit=1600),
                                  ontology=ont), "rollup:gross_profit")
    assert res.status == "pass" and res.difference == 0


def test_the_balance_sheet_is_never_gated_because_it_does_not_net():
    """Both shipped rulebooks declare their EQUITY section `either` — a deficit and treasury shares
    carry the sign the filing prints — and `total equity = owners + NCI` is still plain addition
    that has to stay checked. Gating it took a whole file's fixture down with it once
    (`tests/test_review_suppression`), which is why this is pinned rather than left implied."""
    tpl = load_template(_bs_template())
    ont = _rulebook({"bs_ca": "either"}, ("prepayments", "deposits", "other_current_assets"))

    res = _one(evaluate_structure(tpl, _facts(prepayments=40, deposits=60,
                                              other_current_assets=100), ontology=ont),
               "rollup:other_current_assets")
    assert res.status == "pass" and res.difference == 0


def test_a_rulebook_that_declares_no_sign_conventions_gates_nothing():
    """`ontology` is optional and additive — a caller validating a spread against a TEMPLATE alone
    gets the template's own relations, and a rulebook silent on sections must behave the same."""
    tpl = load_template(_pl_template())
    items = _facts(revenue=1000, cost_of_sales=-600, gross_profit=400)

    for ont in (None, _rulebook({})):
        res = _one(evaluate_structure(tpl, items, ontology=ont), "rollup:gross_profit")
        assert res.status == "pass" and res.difference == 0


def test_the_shipped_rulebook_declares_one_signed_sections_only_on_the_balance_sheet(
        shipped, shipped_rulebook):
    """WHY THE GATE BITES AT ALL, measured on the shipped configuration: four sections declare
    `positive_expected` and all four are balance-sheet asset or liability sections, so every
    netting statement's plain sums are unverified until the rulebook says how they are signed."""
    declared = {s: str((spec.get("sign_convention") if isinstance(spec, dict)
                        else getattr(spec, "sign_convention", None)) or "")
                for s, spec in (getattr(shipped_rulebook, "section_defaults", None) or {}).items()}
    assert {s for s, c in declared.items() if c == "positive_expected"} == {
        "bs_nca", "bs_ca", "bs_ncl", "bs_cl"}
    # And not one concept declares a per-concept convention for `normalize` to orient it by, which
    # is the gap the gate reports rather than asserts.
    assert declared["is_pl"] == "either" and declared["bs_equity"] == "either"


# ── a section's subtotal is the one that covers the section ───────────────────────────────────

def test_a_sections_subtotal_is_the_candidate_that_covers_the_section(shipped, shipped_rulebook):
    """`bs_ca__total_assets` is declared INSIDE the current-asset section — it is the balance
    sheet's grand total, nested there because that is where a filing prints it — so taking the
    first candidate reconciled the section against every asset the company owns. The subtotal is
    picked by how much of THIS section its own rollup reaches, and how little else."""
    by_section = {r.extra.get("section"): r for r in section_relations(shipped, shipped_rulebook)}

    current = by_section["bs_ca"]
    assert current.target == "bs_ca__total_current_assets"
    assert "bs_ca__total_assets" not in current.components, \
        "the grand total is not a member of the section it is printed in"
    assert "bs_ca__total_current_assets" not in current.components

    assert by_section["bs_nca"].target == "bs_nca__total_non_current_assets"
    assert by_section["bs_cl"].target == "bs_cl__total_current_liabilities"
    assert by_section["bs_ncl"].target == "bs_ncl__total_non_current_liabilities"


# ── ordering: a fact about the filing outranks a premise about the rule ───────────────────────

def test_a_section_with_no_printed_subtotal_says_so_before_naming_an_unmet_premise():
    """Whether the section's signs are declared is MOOT when the filing printed no subtotal to
    reconcile against, and reporting the premise sent a reader to fix a declaration that would
    change nothing. The rulebook's own answer for this case is `no_reported_subtotal`."""
    tpl = load_template(_pl_template())
    ont = load_ontology(copy.deepcopy({
        "schema_version": 2, "ontology_key": "t", "target_template_key": "t", "locale": "en",
        "section_defaults": {"is_pl": {"sign_convention": "either"}},
        "mappings": [{"canonical_key": k, "label": k, "section_scope": ["is_pl"]}
                     for k in ("revenue", "cost_of_sales", "gross_profit")],
        "validation": {"section_reconciliation":
                       "Every section with a reported subtotal must account for its printed rows."},
    }), resolve=True)

    # The subtotal itself is absent; only the members were extracted.
    report = evaluate_structure(tpl, _facts(revenue=1000, cost_of_sales=-600), ontology=ont)
    res = _one(report, "section_reconciliation:is_pl")
    assert res is not None and res.status == "skipped"
    assert res.details["reason"] == "no_reported_subtotal"

    # With the subtotal printed, the unmet premise is what is left to report.
    report = evaluate_structure(tpl, _facts(revenue=1000, cost_of_sales=-600, gross_profit=400),
                                ontology=ont)
    assert _one(report, "section_reconciliation:is_pl").details["reason"] == \
        "section_signs_not_declared_one_way"
