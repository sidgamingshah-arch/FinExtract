"""A section is reconciled against the subtotal that totals IT, not one that totals a block of it.

`structural_checks.section_relations` reconciles each template section — "reported subtotal minus
every concept printed in the section is zero" — against the candidate subtotal whose rollup
reaches the most of the section's members. Where the template gives the section's bottom lines no
rollup there is no such total to find, and the best candidate was whatever rollup reached
anything at all:

    Ind AS profit and loss   is_pl__total_income_tax   reaches 3 of 17   (current, deferred, earlier)
    US GAAP profit and loss  is_pl__gross_profit       reaches 3 of 22
    ICON other information   cov_icon__write_off_…     reaches 0 of 3

So the check asserted that total tax equals every line of the statement. On JSW Steel 2024-25 —
the one Indian filing whose costs count as signed, so the one it ran on — it failed on both
statements by the whole P&L: standalone 116,451 against a printed tax of 706.

A section's total reaches the section. Every real one on the four shipped templates reaches at
least 35 of its 42 members (HK current assets); every false one reaches a small minority. A
candidate reaching half or less is refused, and the section is reported as one with no total to
reconcile against — `no_reported_subtotal`, as a section with no subtotal node already is — with
the refused candidate named in the result (`no_section_total`).
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
from app.services.structural_checks import evaluate_structure, section_relations

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _node(key: str, role: str = "line", rollup: list[str] | None = None) -> dict:
    n = {"node_id": key, "canonical_key": key, "label": key, "role": role}
    if rollup is not None:
        n["rollup"] = {"op": "sum", "children": rollup}
    return n


def _template(children: list[dict]) -> dict:
    return {"template_key": "t", "name": "t", "statements": [{
        "type": "profit_and_loss", "identities": [],
        "sections": [{"node_id": "is_pl", "canonical_key": "is_pl", "label": "P&L",
                      "role": "header", "children": children}]}]}


def _jsw_shaped() -> dict:
    """The Ind AS face's shape: the bottom lines are subtotals with no rollup, and the one
    subtotal with a rollup totals the tax block."""
    return _template([
        _node("revenue"), _node("other_income"),
        _node("cost_of_materials"), _node("change_in_inventories"), _node("other_expenses"),
        _node("total_expenses", "subtotal"),
        _node("profit_before_tax", "subtotal"),
        _node("current_tax"), _node("deferred_tax"),
        _node("total_tax", "subtotal", ["current_tax", "deferred_tax"]),
        _node("profit_for_the_year", "total"),
    ])


def _rulebook(keys: list[str]):
    return load_ontology(copy.deepcopy({
        "schema_version": 2, "ontology_key": "t", "target_template_key": "t", "locale": "en",
        "section_defaults": {"is_pl": {"sign_convention": "either"}},
        "mappings": [{"canonical_key": k, "label": k, "section_scope": ["is_pl"]} for k in keys],
        "validation": {"section_reconciliation":
                       "Every section with a reported subtotal must account for its printed rows."},
    }), resolve=True)


def _facts(**by_key: object) -> list[LineItem]:
    out = []
    for key, num in by_key.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(str(num)), value_raw=Decimal(str(num)),
                                    basis=Basis.STANDALONE, period_label="current"))
        out.append(li)
    return out


def _one(report, rule_id: str):
    return next((r for r in report.results if r.rule_id == rule_id), None)


# ── the shape ───────────────────────────────────────────────────────────────────────────────────

def test_a_subtotal_that_totals_a_block_is_not_the_sections_total():
    tpl = load_template(_jsw_shaped())
    keys = [n["canonical_key"] for n in _jsw_shaped()["statements"][0]["sections"][0]["children"]]
    (rel,) = section_relations(tpl, _rulebook(keys))
    assert rel.target == ""
    assert rel.extra["no_section_total"] == {"nearest_subtotal": "total_tax", "covers": "2/7"}


def test_a_jsw_shaped_face_is_unreconciled_not_failed():
    """JSW's figures in miniature: costs printed unsigned beside one negative (the change in
    inventories), which is what made the section count as signed and the check run."""
    tpl = load_template(_jsw_shaped())
    keys = [n["canonical_key"] for n in _jsw_shaped()["statements"][0]["sections"][0]["children"]]
    report = evaluate_structure(tpl, _facts(
        revenue=127702, other_income=1865, cost_of_materials=65779, change_in_inventories=-1736,
        other_expenses=30121, total_expenses=94164, profit_before_tax=6543,
        current_tax=1729, deferred_tax=-805, total_tax=924, profit_for_the_year=5619),
        ontology=_rulebook(keys))
    res = _one(report, "section_reconciliation:is_pl")
    assert res is not None and res.status == "skipped", res
    assert res.details["reason"] == "no_reported_subtotal"
    assert res.details["no_section_total"]["nearest_subtotal"] == "total_tax"
    # The tax block's own arithmetic is still checked, by its rollup.
    tax = _one(report, "rollup:total_tax")
    assert tax is not None and tax.status != "fail", tax


def test_a_total_reaching_most_of_its_section_keeps_it():
    """HK's sections carry memo lines their total does not reach (current assets: 35 of 42), and
    they are reconciled as they were."""
    children = [_node("a"), _node("b"), _node("c"), _node("memo"),
                _node("total", "subtotal", ["a", "b", "c"])]
    tpl = load_template(_template(children))
    (rel,) = section_relations(tpl, _rulebook([c["canonical_key"] for c in children]))
    assert rel.target == "total"
    assert "no_section_total" not in rel.extra


def test_half_is_not_enough():
    children = [_node("a"), _node("b"), _node("c"), _node("d"),
                _node("ab", "subtotal", ["a", "b"])]
    tpl = load_template(_template(children))
    (rel,) = section_relations(tpl, _rulebook([c["canonical_key"] for c in children]))
    assert rel.target == "" and rel.extra["no_section_total"]["covers"] == "2/4"


# ── the shipped templates ───────────────────────────────────────────────────────────────────────

def _shipped(template: str, line_items: str):
    from app.schemas.line_items import load_line_item_set
    from app.services.working_view import build_working_view

    tpl = load_template(json.loads((TEMPLATES / template).read_text(encoding="utf-8")))
    raw = json.loads((TEMPLATES / line_items).read_text(encoding="utf-8"))
    return {r.extra.get("section"): r
            for r in section_relations(tpl, build_working_view(load_line_item_set(raw,
                                                                                 resolve=True)))}


@pytest.mark.parametrize("template,line_items,section,nearest,covers", [
    ("output_csv_indas_v1_template.json", "output_csv_indas_line_items.json",
     "is_pl", "is_pl__total_income_tax", "3/17"),
    ("output_csv_usgaap_v1_template.json", "output_csv_usgaap_line_items.json",
     "is_pl", "is_pl__gross_profit", "3/22"),
    ("output_csv_icon_v1_template.json", "output_csv_icon_line_items.json",
     "cov_icon_other_information", "cov_icon__write_off_finished_goods", "0/3"),
])
def test_the_shipped_sections_with_no_total_are_named(template, line_items, section, nearest,
                                                      covers):
    rel = _shipped(template, line_items)[section]
    assert rel.target == ""
    assert rel.extra["no_section_total"] == {"nearest_subtotal": nearest, "covers": covers}


def test_every_other_ind_as_section_keeps_its_total():
    by_section = _shipped("output_csv_indas_v1_template.json", "output_csv_indas_line_items.json")
    assert by_section["bs_ca"].target == "bs_ca__total_current_assets"
    assert by_section["bs_nca"].target == "bs_nca__total_non_current_assets"
    assert by_section["is_oci"].target == "is_oci__total_other_comprehensive_income"
    assert by_section["cf_oper_indirect"].target == \
        "cf_oper_indirect__cash_flows_oper_activ_indirect"
    assert [s for s, r in by_section.items() if "no_section_total" in r.extra] == ["is_pl"]


def test_the_hk_template_keeps_every_section_total():
    """The reference corpus runs on this pair: no section of it changes its subtotal."""
    by_section = _shipped("output_csv_hk_v1_template.json", "output_csv_hk_line_items.json")
    assert not [s for s, r in by_section.items() if "no_section_total" in r.extra]
    assert by_section["is_pl"].target == "is_pl__profit_for_the_year"
    assert by_section["bs_ca"].target == "bs_ca__total_current_assets"
