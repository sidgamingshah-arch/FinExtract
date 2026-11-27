"""A TAX CHARGE PRINTED AS ONE LINE IS IN THE PROFIT-AND-LOSS SECTION'S SUM.

NEW FILE -> backend/tests/test_a_tax_charge_printed_as_one_line_is_in_the_profit_and_loss.py

The section reconciliation sums a section's LEAVES, and total income tax is a subtotal of the
current, deferred and earlier-year taxes. A filing that prints only "Income tax" put its charge on
a key the sum never read, so the profit-and-loss section failed by exactly the printed charge — once
its checks ran at all (a note-assembled cost no longer hides the face's signs; see
`test_a_figure_assembled_from_the_notes_does_not_decide_the_face_signs`). Measured: Hon Kwok 2023/24
by (46,314), Far East Horizon 2024 by (3,502,126), PetroChina 2024 by (57,753).

The balance sheet already lets a printed subtotal stand in for parts nobody found
(`test_the_checks_count_what_the_grid_counts`). A section that nets may do the same once THIS
FILING prints it signed: its subtotal is then added as printed, like every other line in it.
"""
from __future__ import annotations

import copy
from decimal import Decimal

from app.core.models.enums import Basis, PrintedIn
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.schemas.loader import load_ontology, load_template
from app.services.structural_checks import evaluate_structure


def _row(key: str, amount: str) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key, printed_in=PrintedIn.FACE)
    ev = ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                        basis=Basis.CONSOLIDATED, period_label="current",
                        provenance=Provenance(source_kind="pdf", page_index=0,
                                              bbox=BBox(x0=0.6, y0=0.5, x1=0.7, y1=0.51)))
    li.set_value(ev)
    return li


def _node(node_id: str, role: str = "line", rollup: list[str] | None = None) -> dict:
    n = {"node_id": node_id, "canonical_key": node_id, "label": node_id, "role": role}
    if rollup is not None:
        n["rollup"] = {"op": "sum", "children": rollup}
    return n


KEYS = ("revenue", "cost_of_sales", "admin", "current_tax", "deferred_tax", "total_tax", "profit")


def _section(items):
    tpl = load_template({
        "template_key": "t", "name": "t",
        "statements": [{"type": "profit_and_loss", "sections": [{
            "node_id": "is_pl", "canonical_key": "is_pl", "label": "Profit or loss",
            "role": "header",
            "children": [
                _node("revenue"), _node("cost_of_sales"), _node("admin"),
                _node("current_tax"), _node("deferred_tax"),
                _node("total_tax", "subtotal", ["current_tax", "deferred_tax"]),
                _node("profit", "total", ["revenue", "cost_of_sales", "admin", "total_tax"]),
            ]}], "identities": []}]})
    ont = load_ontology(copy.deepcopy({
        "schema_version": 2, "ontology_key": "t", "target_template_key": "t", "locale": "en",
        "section_defaults": {"is_pl": {"sign_convention": "either"}},
        "mappings": [{"canonical_key": k, "label": k, "section_scope": ["is_pl"]} for k in KEYS],
        "validation": {"section_reconciliation":
                       "Every section with a reported subtotal must account for its printed rows."},
    }), resolve=True)
    report = evaluate_structure(tpl, items, ontology=ont)
    return next(r for r in report.results if r.rule_id == "section_reconciliation:is_pl"
                and r.status in ("pass", "fail"))


def test_a_signed_face_that_prints_only_its_total_tax_reconciles():
    """Hon Kwok's shape: revenue, costs in brackets, one tax line, the profit."""
    res = _section([_row("revenue", "1086515"), _row("cost_of_sales", "-505170"),
                    _row("admin", "-86377"), _row("total_tax", "-46314"),
                    _row("profit", "448654")])
    assert res.status == "pass"
    assert res.details["stood_in_for_parts"] == {"total_tax": "-46314"}


def test_once_a_tax_part_is_printed_the_parts_are_the_reading():
    res = _section([_row("revenue", "1000"), _row("cost_of_sales", "-600"),
                    _row("current_tax", "-50"), _row("total_tax", "-80"), _row("profit", "320")])
    assert res.status == "fail" and res.difference == Decimal("-30")
    assert "stood_in_for_parts" not in res.details


def _section_with_gross_profit(items):
    tpl = load_template({
        "template_key": "t", "name": "t",
        "statements": [{"type": "profit_and_loss", "sections": [{
            "node_id": "is_pl", "canonical_key": "is_pl", "label": "Profit or loss",
            "role": "header",
            "children": [
                _node("revenue"), _node("materials"), _node("royalties"), _node("admin"),
                _node("cost_of_sales", "subtotal", ["materials", "royalties"]),
                _node("gross_profit", "subtotal", ["cost_of_sales"]),
                _node("current_tax"), _node("deferred_tax"),
                _node("total_tax", "subtotal", ["current_tax", "deferred_tax"]),
                _node("profit", "total", ["revenue", "gross_profit", "admin", "total_tax"]),
            ]}], "identities": []}]})
    keys = ("revenue", "materials", "royalties", "admin", "cost_of_sales", "gross_profit",
            "current_tax", "deferred_tax", "total_tax", "profit")
    ont = load_ontology(copy.deepcopy({
        "schema_version": 2, "ontology_key": "t", "target_template_key": "t", "locale": "en",
        "section_defaults": {"is_pl": {"sign_convention": "either"}},
        "mappings": [{"canonical_key": k, "label": k, "section_scope": ["is_pl"]} for k in keys],
        "validation": {"section_reconciliation":
                       "Every section with a reported subtotal must account for its printed rows."},
    }), resolve=True)
    report = evaluate_structure(tpl, items, ontology=ont)
    return next(r for r in report.results if r.rule_id == "section_reconciliation:is_pl"
                and r.status in ("pass", "fail"))


def test_a_running_subtotal_printed_beside_its_parts_does_not_stand_in():
    """Moroil 2025 (p67): "Cost of operations" was swept into a line outside gross profit's reach,
    and gross profit standing in as well counted it twice. Only the tax bucket stands in."""
    res = _section_with_gross_profit([
        _row("revenue", "1852716"), _row("admin", "-1565644"), _row("gross_profit", "287072"),
        _row("total_tax", "-10272"), _row("profit", "276800")])
    assert res.status == "pass"
    assert res.details["stood_in_for_parts"] == {"total_tax": "-10272"}
