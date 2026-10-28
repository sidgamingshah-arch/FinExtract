"""THE TWO OTHER OPERATING CASH ROWS OF A CAS DIRECT-METHOD SECTION FOLD INTO TWO DIRECT LINES.

A CAS cash-flow statement presents the operating section by the direct method on its face, and
prints two rows the HK template has no line for: 收到其他与经营活动有关的现金 above
经营活动现金流入小计, and 支付其他与经营活动有关的现金 above 经营活动现金流出小计. Both reached no
concept and stayed unclassified, so the template's direct-method lines summed to the operating cash
flow less both. 澜起科技 688008 2024, consolidated: 1,554,897,677.72 against a printed
1,691,321,506.14 (+295,347,782.24 received, -158,923,953.82 paid).

The user's decision: fold them in. Each is a part of the direct line it is printed beside — other
receipts of Cash Receipts from Customers, other payments of Cash Paid to Suppliers — and arrives
on a carrier of its own beside that line's printed row (`CascadeRung.adds_to_printed`), as
other-business revenue arrives beside 其他收益. The tax refunds were already folded, into Income
Taxes Paid(Direct), by `sub__cf_direct_tax_refunds_received`.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.periods import concept_value
from app.services.working_view import build_working_view
from app.stages.note_sourced import ARRIVED_BESIDE_PRINTED, NoteSourcedStage

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

RECEIPTS, SUPPLIERS = ("cf_oper_direct__cash_receipts_from_customers",
                       "cf_oper_direct__cash_paid_to_suppliers")
OTHER_RECEIPTS, OTHER_PAYMENTS = ("sub__cf_direct_other_operating_receipts",
                                  "sub__cf_direct_other_operating_payments")
EMPLOYEES = "cf_oper_direct__cash_paid_to_employees"
TAXES = "cf_oper_direct__income_taxes_paid_direct"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _items(shipped):
    return {i.key: i for i in shipped.items}


# ── the configuration ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("part,parent,caption,sign", [
    (OTHER_RECEIPTS, RECEIPTS, "收到其他与经营活动有关的现金", "positive_expected"),
    (OTHER_PAYMENTS, SUPPLIERS, "支付其他与经营活动有关的现金", "negative_expected")])
def test_each_part_is_an_internal_face_part_wired_beside_its_parents_printed_row(
        shipped, part, parent, caption, sign):
    items = _items(shipped)
    p = items[part]
    assert p.namespace == "internal" and p.in_output is False and p.parent == parent
    assert p.route == "face" and p.sign_convention == sign
    # A FACE ALIAS, AND THE GATE IT NEEDS: bindable only off a cash-flow statement's direct section.
    assert caption in (p.aliases_i18n or {}).get("zh", [])
    assert str(getattr(p.statement, "value", p.statement)) == "cash_flow"
    assert list(p.section_scope) == ["cf_oper_direct"]
    (rung,) = items[parent].cascade
    assert [t.ref for t in rung.terms] == [part] and [t.sign for t in rung.terms] == [1]
    assert rung.adds_to_printed is True and rung.outranks_printed is False
    # A PAYMENT IS NEGATIVE, so the rung must not refuse a negative figure.
    assert rung.refuse_negative is (sign == "positive_expected")


def test_the_parents_tell_the_model_not_to_add_the_other_row(shipped):
    items = _items(shipped)
    assert "do not add the other row yourself" in items[RECEIPTS].definition
    assert "do not add the other row yourself" in items[SUPPLIERS].definition


@pytest.mark.parametrize("caption,key", [
    ("收到其他与经营活动有关的现金", OTHER_RECEIPTS), ("支付其他与经营活动有关的现金", OTHER_PAYMENTS),
    # 600990 (四创电子) prints the consolidated payments row without 的; unbound it was swept into
    # the indirect residual and the direct lines came out 89,365,652.61 over the printed total.
    ("收到其他与经营活动有关现金", OTHER_RECEIPTS), ("支付其他与经营活动有关现金", OTHER_PAYMENTS)])
def test_the_printed_row_binds_its_part(shipped, caption, key):
    res = OntologyMatcher(build_working_view(shipped)).match(
        caption, statement="cash_flow", section="经营活动产生的现金流量")
    assert res.canonical_key == key, res


# ── the stage, on 688008's direct-method operating section ─────────────────────────────────────

def _face(key: str, caption: str, current: str, prior: str, *, method=None) -> LineItem:
    row = LineItem(source_label=caption, canonical_key=key)
    for slot, period, amount in (("c", "current", current), ("p", "prior", prior)):
        row.values[slot] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                          value=Decimal(amount), value_raw=Decimal(amount).copy_abs(),
                                          provenance=Provenance(page_index=160))
    if method is not None:
        row.confidence.method = method
    return row


def _montage(method=None) -> list[LineItem]:
    """688008's consolidated operating section, as `normalize` leaves it: outflows negated."""
    return [
        _face(RECEIPTS, "销售商品、提供劳务收到的现金", "3576434663.11", "2297192754.40",
              method=method),
        _face("sub__cf_direct_tax_refunds_received", "收到的税费返还", "17805020.80", "247201.56"),
        _face(OTHER_RECEIPTS, "收到其他与经营活动有关的现金", "295347782.24", "238896174.55"),
        _face(SUPPLIERS, "购买商品、接受劳务支付的现金", "-1378915150.88", "-929659183.95",
              method=method),
        _face(EMPLOYEES, "支付给职工及为职工支付的现金", "-626115370.66", "-579934914.25"),
        _face("sub__cf_direct_income_taxes_paid", "支付的各项税费", "-34311484.65",
              "-145286160.53"),
        _face(OTHER_PAYMENTS, "支付其他与经营活动有关的现金", "-158923953.82", "-150206172.67"),
    ]


def _run(shipped, rows) -> DocumentModel:
    doc = DocumentModel(filename="688008.pdf")
    doc.line_items = list(rows)
    ctx = PipelineContext(settings=get_settings())
    ctx.line_items = shipped
    return NoteSourcedStage().run(doc, ctx)


def _figure(doc, key, period="current"):
    """The figure the app shows for the line — `concept_value` over every carrier."""
    rows = []
    for li in doc.line_items:
        if li.canonical_key == key:
            row = li.model_dump(mode="json")
            row["values"] = list((row.get("values") or {}).values())
            rows.append(row)
    got = concept_value(rows, "consolidated", period)
    return None if got is None else Decimal(str(got)).quantize(Decimal("0.01"))


def test_each_other_row_arrives_beside_its_direct_lines_printed_row(shipped):
    rows = _montage()
    doc = _run(shipped, rows)
    assert _figure(doc, RECEIPTS) == Decimal("3871782445.35")        # + 295,347,782.24
    assert _figure(doc, RECEIPTS, "prior") == Decimal("2536088928.95")
    assert _figure(doc, SUPPLIERS) == Decimal("-1537839104.70")      # + -158,923,953.82
    assert _figure(doc, SUPPLIERS, "prior") == Decimal("-1079865356.62")
    # THE PRINTED ROWS KEEP THEIR FIGURES; each part arrives on a carrier of its own.
    assert rows[0].values["c"].value == Decimal("3576434663.11")
    assert rows[3].values["c"].value == Decimal("-1378915150.88")
    for key, rung in ((RECEIPTS, "OTHER_OPERATING_RECEIPTS"), (SUPPLIERS, "OTHER_OPERATING_PAYMENTS")):
        (arrived,) = [li for li in doc.line_items if li.canonical_key == key and li not in rows]
        assert ARRIVED_BESIDE_PRINTED in arrived.confidence.flags
        assert arrived.derivation["consolidated:current"]["method"] == f"cascade:{rung}"


def test_the_direct_method_lines_add_to_the_operating_cash_flow_printed(shipped):
    doc = _run(shipped, _montage())
    for period, printed in (("current", "1691321506.14"), ("prior", "731249699.11")):
        total = sum((_figure(doc, k, period) for k in (RECEIPTS, SUPPLIERS, EMPLOYEES, TAXES)),
                    Decimal(0))
        assert total == Decimal(printed), (period, total)


def test_a_model_answer_for_the_direct_line_does_not_stop_the_part_arriving(shipped):
    doc = _run(shipped, _montage(method="llm"))
    assert _figure(doc, RECEIPTS) == Decimal("3871782445.35")
    assert _figure(doc, SUPPLIERS) == Decimal("-1537839104.70")


def test_without_the_other_rows_the_direct_lines_are_their_printed_rows(shipped):
    """An HKFRS direct-method statement prints neither row: nothing arrives."""
    doc = _run(shipped, [r for r in _montage() if r.canonical_key not in (OTHER_RECEIPTS,
                                                                         OTHER_PAYMENTS)])
    assert _figure(doc, RECEIPTS) == Decimal("3576434663.11")
    assert _figure(doc, SUPPLIERS) == Decimal("-1378915150.88")
    assert len([li for li in doc.line_items if li.canonical_key == RECEIPTS]) == 1
