"""The structural checks count a figure the way the grid publishes it.

Measured on four CAS filings (河钢 000709, 四创 600990, 湖北能源 000883, 海博思创 688411): each failed
the same balance-sheet and cash-flow checks while every figure the grid printed was right, because
the checks added figures up differently from the grid.

* ONE FACT PRINTED TWICE. "经营活动产生的现金流量净额" is printed on the cash flow statement and
  again at the foot of the supplement note. The grid counts it once (`periods.summable`); the
  checks added both rows and asserted twice the printed figure.
* A PRINTED SUBTOTAL WITH NO PARTS FOUND. The current-asset section is summed over its leaves, so
  a printed 存货 — whose template parts are raw materials, work in progress and finished goods —
  reached the sum only through parts no CAS face prints.
* A CARVED FIGURE THE FACE HANDED OVER. 湖北能源's 长期应付款 prints exactly the related-party
  amount its note reads, gave it up, and the related-party line — still marked as carved, shown
  but added nowhere — left it out of the section too.
"""
from __future__ import annotations

import copy
from decimal import Decimal
from types import SimpleNamespace

from app.core.models import DocumentModel, PageKind
from app.core.models.document import PageSource
from app.core.models.enums import Basis, PrintedIn
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, NoteRef, Provenance
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template
from app.services import derivation
from app.services.rollups import CARVED_FROM_FACE, SETTLED_FROM_FACE
from app.services.structural_checks import collect_values, evaluate_structure

FACE, NOTES = 0, 1


def _prov(page: int, y: float = 0.5, period: str = "current") -> Provenance:
    x = 0.6 if period == "current" else 0.8
    return Provenance(source_kind="pdf", page_index=page,
                      bbox=BBox(x0=x, y0=y, x1=x + 0.1, y1=y + 0.01))


def _row(key: str, amount: str, page: int | None, label: str | None = None,
         flags: tuple[str, ...] = ()) -> LineItem:
    li = LineItem(source_label=label or key, canonical_key=key)
    ev = ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                        basis=Basis.CONSOLIDATED, period_label="current",
                        provenance=None if page is None else _prov(page))
    ev.confidence.flags.extend(flags)
    li.set_value(ev)
    return li


def _node(node_id: str, role: str = "line", rollup: list[str] | None = None) -> dict:
    n = {"node_id": node_id, "canonical_key": node_id, "label": node_id, "role": role}
    if rollup is not None:
        n["rollup"] = {"op": "sum", "children": rollup}
    return n


# ── one fact printed twice ────────────────────────────────────────────────────────────────────

CFO = "经营活动产生的现金流量净额"


def test_the_operating_total_printed_on_the_statement_and_in_its_note_is_one_fact():
    """湖北能源 prints 6,432,044,580.01 on p.115 and again on p.228."""
    vals = collect_values([_row("cfo", "6432044580.01", 115, CFO),
                           _row("cfo", "6432044580.01", 228, CFO)])
    assert vals.get("cfo", ("consolidated", "current")) == Decimal("6432044580.01")


def test_the_same_caption_printed_twice_on_one_page_still_adds():
    """The rule needs a DIFFERENT place, as the grid's does: one schedule printing a fee twice
    printed it twice."""
    vals = collect_values([_row("fee", "100", 12, "Management fee"),
                           _row("fee", "100", 12, "Management fee")])
    assert vals.get("fee", ("consolidated", "current")) == Decimal("200")


def test_different_amounts_or_captions_still_add():
    vals = collect_values([_row("dep", "2937455476.06", 228, "固定资产折旧"),
                           _row("dep", "66924825.46", 228, "使用权资产折旧"),
                           _row("loan", "10", 3, "Bank borrowings"),
                           _row("loan", "10", 9, "Other borrowings")])
    assert vals.get("dep", ("consolidated", "current")) == Decimal("3004380301.52")
    assert vals.get("loan", ("consolidated", "current")) == Decimal("20")


def test_a_row_with_no_page_neither_anchors_nor_is_dropped():
    vals = collect_values([_row("cfo", "5", None, CFO), _row("cfo", "5", 115, CFO)])
    assert vals.get("cfo", ("consolidated", "current")) == Decimal("10")


def test_the_cash_flow_rollup_passes_on_the_grids_figure():
    tpl = load_template({
        "template_key": "t", "name": "t",
        "statements": [{"type": "cash_flow", "sections": [{
            "node_id": "cf", "canonical_key": "cf", "label": "Operating", "role": "header",
            "children": [_node("np"), _node("adj"), _node("cfo", "total", ["np", "adj"])]}],
            "identities": []}]})
    items = [_row("np", "2219319748.69", 228), _row("adj", "4212724831.32", 228),
             _row("cfo", "6432044580.01", 115, CFO), _row("cfo", "6432044580.01", 228, CFO)]
    res = next(r for r in evaluate_structure(tpl, items).results if r.rule_id == "rollup:cfo")
    assert res.status == "pass" and res.actual == Decimal("6432044580.01")


# ── a printed subtotal stands in for parts nobody found ──────────────────────────────────────

def _bs():
    tpl = load_template({
        "template_key": "t", "name": "t",
        "statements": [{"type": "balance_sheet", "sections": [{
            "node_id": "bs_ca", "canonical_key": "bs_ca", "label": "Current assets",
            "role": "header",
            "children": [
                _node("cash"), _node("raw"), _node("wip"), _node("fg"),
                _node("inventories", "subtotal", ["raw", "wip", "fg"]),
                _node("total_ca", "total", ["cash", "inventories"]),
            ]}], "identities": []}]})
    ont = load_ontology(copy.deepcopy({
        "schema_version": 2, "ontology_key": "t", "target_template_key": "t", "locale": "en",
        "section_defaults": {"bs_ca": {"sign_convention": "positive_expected"}},
        "mappings": [{"canonical_key": k, "label": k, "section_scope": ["bs_ca"]}
                     for k in ("cash", "raw", "wip", "fg", "inventories", "total_ca")],
        "validation": {"section_reconciliation":
                       "Every section with a reported subtotal must account for its printed rows."},
    }), resolve=True)
    return tpl, ont


def _section(items):
    tpl, ont = _bs()
    report = evaluate_structure(tpl, items, ontology=ont)
    return next(r for r in report.results if r.rule_id == "section_reconciliation:bs_ca")


def test_a_printed_inventories_line_with_no_parts_found_is_in_the_section():
    """四创 600990: 货币资金 451,359,912.13 and 存货 1,232,581,548.57, no breakdown on the face."""
    res = _section([_row("cash", "451359912.13", FACE), _row("inventories", "1232581548.57", FACE),
                    _row("total_ca", "1683941460.70", FACE)])
    assert res.status == "pass"
    assert res.details["stood_in_for_parts"] == {"inventories": "1232581548.57"}


def test_once_a_part_is_found_the_parts_are_the_reading():
    """A subtotal printed beside a part found is not counted again; the shortfall stays a gap."""
    res = _section([_row("cash", "100", FACE), _row("inventories", "60", FACE),
                    _row("raw", "40", NOTES), _row("total_ca", "160", FACE)])
    assert res.status == "fail" and res.difference == Decimal("20")
    assert "stood_in_for_parts" not in res.details


def test_the_parts_summing_to_the_printed_line_pass_without_a_stand_in():
    res = _section([_row("cash", "100", FACE), _row("inventories", "60", FACE),
                    _row("raw", "40", NOTES), _row("fg", "20", NOTES),
                    _row("total_ca", "160", FACE)])
    assert res.status == "pass" and "stood_in_for_parts" not in res.details


# ── a carved figure the face handed over whole is counted on its line ─────────────────────────

def _carved_line(key: str, amount: str, note: str) -> LineItem:
    """A related-party line read off ONE note row, marked carved as its cascade rung marks it."""
    li = LineItem(source_label=key, canonical_key=key)
    ev = ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                        basis=Basis.CONSOLIDATED, period_label="current",
                        provenance=_prov(NOTES, 0.5))
    ev.confidence.flags.append(CARVED_FROM_FACE)
    li.set_value(ev)
    li.derivation = derivation.record(None, basis="consolidated", period_label="current",
                                      derivation=derivation.build(
                                          method="note_sourced:sum_of_1_rows", formula=None,
                                          inputs=[{"label": "x", "note": note, "value": amount,
                                                   "provenance": _prov(NOTES, 0.5).model_dump(
                                                       mode="json")}],
                                          result=amount))
    return li


def _face(key: str, amount: str, note: str) -> LineItem:
    li = _row(key, amount, FACE, "长期应付款")
    li.printed_in = PrintedIn.FACE
    li.note_refs = [NoteRef(raw=note, numbers=[note])]
    return li


def _settle(rows):
    from app.stages.note_sourced import _settle_against_the_face

    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = rows
    ctx = PipelineContext()
    ctx.ontology = SimpleNamespace(mappings=[SimpleNamespace(
        canonical_key="bs_ncl__other_non_current_liabilities", value_scope="exclusive_residual")])
    items = [SimpleNamespace(key=k, section_scope=["bs_ncl"], parent="", in_output=True)
             for k in ("bs_ncl__other_non_current_liabilities",
                       "bs_ncl__due_to_related_parties_ltp")]
    _settle_against_the_face(doc, ctx, items)
    return doc


def test_a_residual_handing_over_the_whole_figure_makes_the_line_counted():
    """湖北能源 000883: 长期应付款 955,133,808.33, the related-party note the same."""
    face = _face("bs_ncl__other_non_current_liabilities", "955133808.33", "48")
    line = _carved_line("bs_ncl__due_to_related_parties_ltp", "955133808.33", "48")
    _settle([face, line])
    (fev,) = face.values.values()
    (lev,) = line.values.values()
    assert fev.value == 0
    assert CARVED_FROM_FACE not in lev.confidence.flags
    assert SETTLED_FROM_FACE in lev.confidence.flags
    vals = collect_values([face, line])
    slot = ("consolidated", "current")
    assert ("bs_ncl__due_to_related_parties_ltp", slot) not in vals.carved
    assert (vals.get("bs_ncl__other_non_current_liabilities", slot)
            + vals.get("bs_ncl__due_to_related_parties_ltp", slot)) == Decimal("955133808.33")


def test_a_carved_figure_the_residual_prints_only_part_of_stays_inside_the_residual():
    """河钢 000709's shape: the residual carries more than the line, so nothing is handed over by
    halves — the residual keeps its printed figure and the line stays shown, not added."""
    face = _face("bs_ncl__other_non_current_liabilities", "11053477089.43", "48")
    line = _carved_line("bs_ncl__due_to_related_parties_ltp", "856059679.92", "48")
    _settle([face, line])
    (fev,) = face.values.values()
    (lev,) = line.values.values()
    assert fev.value == Decimal("11053477089.43")
    assert CARVED_FROM_FACE in lev.confidence.flags


def test_carving_never_re_marks_a_figure_the_face_handed_over():
    from app.stages.note_sourced import carve_note_figures

    line = _row("k", "5", NOTES, flags=(SETTLED_FROM_FACE,))
    line.printed_in = PrintedIn.NOTES
    doc = DocumentModel(filename="f.pdf")
    doc.pages.append(PageSource(index=FACE, kind=PageKind.FACE))
    doc.pages.append(PageSource(index=NOTES, kind=PageKind.NOTES))
    doc.line_items = [line]
    ctx = PipelineContext()
    ctx.line_items = SimpleNamespace(items=[SimpleNamespace(key="k", carved_from_face=True)])
    carve_note_figures(doc, ctx, by="test")
    (ev,) = line.values.values()
    assert CARVED_FROM_FACE not in ev.confidence.flags
