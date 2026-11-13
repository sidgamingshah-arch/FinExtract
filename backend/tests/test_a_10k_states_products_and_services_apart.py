"""A 10-K STATES THE COST OF ITS PRODUCTS AND OF ITS SERVICES APART, and the template carries both.

Regulation S-X Rule 5-03 asks a filer to state the cost of its tangible products and of its services
separately. Apple prints them as a bare "Products" / "Services" pair under "Cost of sales:";
Microsoft as "Product" / "Service and other" under "Cost of revenue:".

THE REVENUE HALVES ARE NOT LINES. "Net sales of products" and "Revenues from services" were removed
at the user's request: on Madrigal's 10-K the products line took the only revenue row the face
prints ('Product revenue, net') and left total revenues empty. A caption naming product sales binds
net sales again, as it did before the split; the same bare pair under the revenue heading binds
nothing.

Pinned here:
  * two cost lines placed where a filer prints them, with no rollup — the cost of revenue keeps
    exactly the arithmetic it had;
  * one identity that CHECKS the split against the cost of revenue the face prints, skipped where a
    half is not printed;
  * a caption naming its half binds by itself, a bare one only read with its heading on a 10-K —
    the sub-heading it arrives as on Apple's face, or the banner on Microsoft's;
  * "Cost of products sold" alone is still a filer's whole cost of revenue.

The figures are Apple FY2025 (millions): 2023 / 2024 / 2025.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.core.models.line_item import LineItem
from app.schemas.line_items import load_line_item_set
from app.schemas.loader import load_template
from app.services import rollups
from app.services.mapping import OntologyMatcher
from app.services.structural_checks import evaluate_structure
from app.services.working_view import build_working_view
from app.stages.map_ontology import BLOCK_HEADING_FLAG
from tests.test_a_10k_statement_of_comprehensive_income import _item, _map, _row

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
TEMPLATE = TEMPLATES / "output_csv_usgaap_v1_template.json"
SET = TEMPLATES / "output_csv_usgaap_line_items.json"

REMOVED = ("is_pl__products_revenue", "is_pl__services_revenue")
COST_PRODUCTS, COST_SERVICES = "is_pl__cost_of_products", "is_pl__cost_of_services"
REVENUES, COST = "is_pl__sales_revenues", "is_pl__cost_of_revenue"


@pytest.fixture(scope="module")
def template():
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def usgaap():
    return load_line_item_set(json.loads(SET.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def matcher(usgaap):
    return OntologyMatcher(build_working_view(usgaap))


def _is_pl(template):
    pl = next(s for s in template["statements"] if s["type"] == "profit_and_loss")
    return pl, next(sec for sec in pl["sections"] if sec["node_id"] == "is_pl")


def test_the_revenue_halves_are_not_lines(template, usgaap):
    keys = {n["canonical_key"] for st in template["statements"] for sec in st["sections"]
            for n in sec["children"]}
    for key in REMOVED:
        assert key not in keys and key not in {i.key for i in usgaap.items}, key


def test_the_cost_halves_sit_where_a_filer_prints_them_and_add_into_nothing(template):
    _pl, section = _is_pl(template)
    order = [n["canonical_key"] for n in section["children"]]
    assert order[:3] == ["is_pl__net_sales", "is_pl__other_revenues", REVENUES]
    assert order[order.index(COST_PRODUCTS):order.index(COST) + 1] == [COST_PRODUCTS, COST_SERVICES,
                                                                       COST]
    nodes = {n["canonical_key"]: n for n in section["children"]}
    for key in (COST_PRODUCTS, COST_SERVICES):
        assert nodes[key]["role"] == "line" and "rollup" not in nodes[key], key
    # THE TOTALS KEEP THEIR ARITHMETIC: total revenues is net sales plus other revenues, and the
    # cost of revenue is still a printed line.
    assert nodes[REVENUES]["rollup"] == {"op": "sum",
                                         "children": ["is_pl__net_sales", "is_pl__other_revenues"]}
    assert "rollup" not in nodes[COST]


def test_one_identity_checks_the_cost_split_against_the_printed_cost(template):
    pl, _section = _is_pl(template)
    ids = {i["id"]: i for i in pl["identities"]}
    assert "revenues_are_products_plus_services" not in ids
    assert ids["cost_of_revenue_is_products_plus_services"] == {
        "id": "cost_of_revenue_is_products_plus_services", "lhs": COST,
        "rhs": {"op": "sum", "children": [COST_PRODUCTS, COST_SERVICES]}}


@pytest.mark.parametrize("caption, key", [
    # Named by itself.
    ("Cost of product revenue", COST_PRODUCTS), ("Cost of services", COST_SERVICES),
    ("Cost of service revenue", COST_SERVICES),
    # Read with its heading.
    ("Cost of sales Products", COST_PRODUCTS), ("Cost of sales Services", COST_SERVICES),
    ("Cost of revenue Product", COST_PRODUCTS), ("Cost of revenue Service and other", COST_SERVICES),
    # Product sales are net sales again, as before the split.
    ("Net product sales", "is_pl__net_sales"), ("Product sales", "is_pl__net_sales"),
    # The totals, and a single cost row, are what they were.
    ("Total net sales", REVENUES), ("Revenue", REVENUES), ("Net sales", "is_pl__net_sales"),
    ("Total cost of sales", COST), ("Cost of sales", COST), ("Cost of products sold", COST),
])
def test_each_cost_half_binds_its_line_and_the_totals_do_not_move(matcher, caption, key):
    hit = matcher.match(caption, statement="profit_and_loss", section=None)
    assert hit is not None and hit.canonical_key == key, (caption, hit)


@pytest.mark.parametrize("caption", ["Products", "Product", "Services", "Service and other"])
def test_a_bare_half_binds_no_line_by_itself(matcher, caption):
    hit = matcher.match(caption, statement="profit_and_loss", section=None)
    assert hit is None or not hit.canonical_key, (caption, hit)


def _apple_rows():
    """Apple's face: the headings arrive as each row's sub-heading."""
    return [_row("Products $", ("298085", "294866", "307003"), sub="Net sales:", page=49),
            _row("Services", ("85200", "96169", "109158"), sub="Net sales:", page=49),
            _row("Total net sales", ("383285", "391035", "416161"), page=49),
            _row("Products", ("189282", "185233", "194116"), sub="Cost of sales:", page=49),
            _row("Services", ("24855", "25119", "26844"), sub="Cost of sales:", page=49),
            _row("Total cost of sales", ("214137", "210352", "220960"), page=49)]


def _microsoft_rows():
    """Microsoft's face: the headings arrive as each row's BANNER, with no sub-heading."""
    return [_row("Product $", ("64699", "64773", "63946"), banner="Revenue:", page=49),
            _row("Service and other", ("147216", "180349", "217778"), banner="Revenue:", page=49),
            _row("Total revenue", ("211915", "245122", "281724"), banner="Revenue:", page=49),
            _row("Product", ("17804", "15272", "14017"), banner="Cost of revenue:", page=49),
            _row("Service and other", ("48059", "58842", "73814"), banner="Cost of revenue:", page=49),
            _row("Total cost of revenue", ("65863", "74114", "87831"), banner="Cost of revenue:",
                 page=49)]


# The revenue halves bind no line; the cost halves bind theirs.
SPLIT = [None, None, REVENUES, COST_PRODUCTS, COST_SERVICES, COST]


@pytest.mark.parametrize("rows", [_apple_rows, _microsoft_rows], ids=["apple", "microsoft"])
def test_a_10ks_bare_cost_halves_bind_read_with_their_heading(usgaap, rows):
    doc = _map(rows(), usgaap)
    assert [r.canonical_key for r in doc.line_items] == SPLIT
    flagged = [r.canonical_key for r in doc.line_items if BLOCK_HEADING_FLAG in r.confidence.flags]
    assert flagged == [COST_PRODUCTS, COST_SERVICES]


def test_off_a_10k_a_bare_half_is_still_not_placed(usgaap):
    doc = _map(_apple_rows(), usgaap, us_10k=False)
    assert [r.canonical_key for r in doc.line_items] == [None, None, REVENUES, None, None, COST]


def test_costs_and_expenses_lends_its_rows_no_half(usgaap):
    """"Costs and expenses:" heads every cost of a statement, not the cost of revenue."""
    doc = _map([_row("Products", ("1", "2", "3"), sub="Costs and expenses:", page=49)], usgaap)
    assert [r.canonical_key for r in doc.line_items] == [None]


# ── THE CHECK, AND WHAT IT NEVER DOES ───────────────────────────────────────────────────────

APPLE = {REVENUES: ("383285", "391035", "416161"), COST_PRODUCTS: ("189282", "185233", "194116"),
         COST_SERVICES: ("24855", "25119", "26844"), COST: ("214137", "210352", "220960")}
RULE = "identity:cost_of_revenue_is_products_plus_services"


def _identity(template, usgaap, figures):
    report = evaluate_structure(load_template(template), [_item(k, v) for k, v in figures.items()],
                                ontology=build_working_view(usgaap))
    return {r.status if r.status != "skipped" else f"skipped:{r.details.get('reason')}"
            for r in report.results if r.rule_id == RULE}


def test_apples_cost_split_adds_up_to_the_cost_of_revenue(template, usgaap):
    assert _identity(template, usgaap, APPLE) == {"pass"}


def test_a_half_read_wrong_breaks_the_identity(template, usgaap):
    wrong = {**APPLE, COST_SERVICES: ("24855", "25119", "109158")}
    assert _identity(template, usgaap, wrong) == {"pass", "fail"}


def test_one_cost_half_alone_is_skipped_not_failed(template, usgaap):
    got = _identity(template, usgaap, {COST_SERVICES: ("8319", "10534", "12000"),
                                       COST: ("96773", "97690", "95000")})
    assert got == {"skipped:components_not_mapped"}


def test_the_published_cost_of_revenue_is_never_the_sum_of_the_halves(template):
    printed = {COST: 97690.0, COST_SERVICES: 10534.0}
    calc = rollups.evaluate(template, lambda key: printed.get(key))
    assert COST not in calc or not calc[COST].computable


def test_a_single_cost_of_products_sold_row_is_still_the_whole_cost_of_revenue(matcher):
    li = LineItem(source_label="Cost of products sold")
    hit = matcher.match(li.source_label, statement="profit_and_loss", section=None)
    assert hit is not None and hit.canonical_key == COST
