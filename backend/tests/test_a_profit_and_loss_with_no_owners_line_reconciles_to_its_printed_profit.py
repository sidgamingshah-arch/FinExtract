"""A PROFIT AND LOSS WITH NO OWNERS' LINE IS RECONCILED AGAINST THE PROFIT IT PRINTS.

NEW FILE -> backend/tests/test_a_profit_and_loss_with_no_owners_line_reconciles_to_its_printed_profit.py

The fixed template reconciles its profit-and-loss section to Profit for the year, and its rollup
makes that line Profit(Loss) Bef Extraord Items plus the extraordinary items plus Minority
Interests(PL): the owners' share, which is what the line-item set's aliases bind to it ("Owners of
the Company"). A single entity, or a group with no non-controlling interests, prints no owners'
line — its "Profit for the year" binds Profit(Loss) Bef Extraord Items — so the reconciliation was
`no_reported_subtotal` on every run: Vivo Energy Mauritius 2025 (p66) and 佳明 2025/26 (p61).

By the template's own arithmetic the total IS that printed figure where its other children are not
printed, so the section is reconciled against it (`structural_checks._printed_equal_of_total`).
A filing that prints the owners' line (Moroil: "Owners of the Company" 74,428, p67) is reconciled
to that line as before.

Also here: a section's residual that the sweep left empty is nil, not unknown, in the section's
rollups (`_nil_when_absent`).
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
    li.set_value(ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                                basis=Basis.CONSOLIDATED, period_label="current",
                                provenance=Provenance(source_kind="pdf", page_index=0,
                                                      bbox=BBox(x0=0.6, y0=0.5, x1=0.7, y1=0.51))))
    return li


def _node(node_id: str, role: str = "line", rollup: list[str] | None = None) -> dict:
    n = {"node_id": node_id, "canonical_key": node_id, "label": node_id, "role": role}
    if rollup is not None:
        n["rollup"] = {"op": "sum", "children": rollup}
    return n


KEYS = ("revenue", "costs", "tax", "pbt", "profit_bef", "minority", "profit_for_the_year")


def _check(items):
    tpl = load_template({
        "template_key": "t", "name": "t",
        "statements": [{"type": "profit_and_loss", "sections": [{
            "node_id": "is_pl", "canonical_key": "is_pl", "label": "Profit or loss",
            "role": "header",
            "children": [
                _node("revenue"), _node("costs"),
                _node("pbt", "subtotal", ["revenue", "costs"]),
                _node("tax"),
                _node("profit_bef", "subtotal", ["pbt", "tax"]),
                _node("minority"),
                _node("profit_for_the_year", "total", ["profit_bef", "minority"]),
            ]}], "identities": []}]})
    ont = load_ontology(copy.deepcopy({
        "schema_version": 2, "ontology_key": "t", "target_template_key": "t", "locale": "en",
        "section_defaults": {"is_pl": {"sign_convention": "either"}},
        "mappings": [{"canonical_key": k, "label": k, "section_scope": ["is_pl"]} for k in KEYS],
        "validation": {"section_reconciliation":
                       "Every section with a reported subtotal must account for its printed rows."},
    }), resolve=True)
    report = evaluate_structure(tpl, items, ontology=ont)
    return [r for r in report.results if r.rule_id == "section_reconciliation:is_pl"]


def test_a_single_entity_is_reconciled_against_its_printed_profit():
    """Vivo's shape, p66: revenue, costs in brackets, tax, "Profit for the year" — no owners."""
    got = _check([_row("revenue", "17840538"), _row("costs", "-17199192"),
                  _row("pbt", "641346"), _row("tax", "-111807"), _row("profit_bef", "529539")])
    evaluated = [r for r in got if r.status in ("pass", "fail")]
    assert [r.status for r in evaluated] == ["pass"]
    assert evaluated[0].details["reconciled_to"] == "profit_bef"
    assert evaluated[0].details["total_not_printed"] == "profit_for_the_year"


def test_a_wrong_figure_still_fails_against_the_printed_profit():
    got = _check([_row("revenue", "17840538"), _row("costs", "-17199192"),
                  _row("tax", "111807"), _row("profit_bef", "529539")])
    assert [r.status for r in got if r.status in ("pass", "fail")] == ["fail"]


def test_a_filing_that_prints_the_owners_share_is_reconciled_to_it():
    """Moroil's shape, p67: the owners' line is the template's total and stays the target."""
    got = _check([_row("revenue", "100"), _row("costs", "-60"), _row("tax", "-10"),
                  _row("profit_bef", "30"), _row("minority", "-4"),
                  _row("profit_for_the_year", "26")])
    evaluated = [r for r in got if r.status in ("pass", "fail")]
    assert [r.status for r in evaluated] == ["pass"]
    assert "reconciled_to" not in evaluated[0].details


def test_no_printed_figure_at_all_is_still_no_reported_subtotal():
    got = _check([_row("revenue", "100"), _row("costs", "-60")])
    assert [r.details.get("reason") for r in got] == ["no_reported_subtotal"]


def test_a_printed_non_controlling_interest_alone_is_not_the_profit():
    """Laurus 2025/26's shape (p273): the profit lines were not bound, only "Non-controlling
    interests" (1.35). The first form of the rule reconciled the whole section to that line and
    failed it by 6,855.97; a plain line beside an unprinted subtotal is no stand-in for the total."""
    got = _check([_row("revenue", "100"), _row("costs", "-60"), _row("tax", "-10"),
                  _row("minority", "-1.35")])
    assert [r.details.get("reason") for r in got] == ["no_reported_subtotal"]
    assert not any("reconciled_to" in r.details for r in got)


def _cash_check(items):
    """A closing-cash roll: opening cash (a line) and three section totals."""
    tpl = load_template({
        "template_key": "t", "name": "t",
        "statements": [{"type": "cash_flow", "sections": [{
            "node_id": "cf", "canonical_key": "cf", "label": "Cash flow", "role": "header",
            "children": [
                _node("receipts"), _node("payments"),
                _node("cfo", "subtotal", ["receipts", "payments"]),
                _node("capex"), _node("cfi", "subtotal", ["capex"]),
                _node("dividends"), _node("cff", "subtotal", ["dividends"]),
                _node("opening"),
                _node("closing", "total", ["opening", "cfo", "cfi", "cff"]),
            ]}], "identities": []}]})
    keys = ("receipts", "payments", "cfo", "capex", "cfi", "dividends", "cff", "opening", "closing")
    ont = load_ontology(copy.deepcopy({
        "schema_version": 2, "ontology_key": "t", "target_template_key": "t", "locale": "en",
        "section_defaults": {"cf": {"sign_convention": "either"}},
        "mappings": [{"canonical_key": k, "label": k, "section_scope": ["cf"]} for k in keys],
        "validation": {"section_reconciliation":
                       "Every section with a reported subtotal must account for its printed rows."},
    }), resolve=True)
    report = evaluate_structure(tpl, items, ontology=ont)
    return [r for r in report.results if r.rule_id == "section_reconciliation:cf"]


def test_one_printed_section_total_of_a_cash_roll_is_not_the_closing_cash():
    """JSW 2024/25's shape: of the closing-cash roll only the investing total was read. The other
    totals are not nil because they were not read, so the section is not reconciled to it."""
    got = _cash_check([_row("receipts", "500"), _row("payments", "-300"), _row("capex", "-90"),
                       _row("cfi", "-90"), _row("dividends", "-40")])
    assert not any("reconciled_to" in r.details for r in got)
    assert [r.status for r in got if r.status in ("pass", "fail")] == []
