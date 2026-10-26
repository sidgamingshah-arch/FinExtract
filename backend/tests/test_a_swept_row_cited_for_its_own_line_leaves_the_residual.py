"""A FACE ROW THE SWEEP PUT ON A SECTION'S RESIDUAL, CITED BY THE MODEL FOR A LINE OF ITS OWN,
LEAVES THE RESIDUAL.

NEW FILE -> backend/tests/test_a_swept_row_cited_for_its_own_line_leaves_the_residual.py

PetroChina's cash flow statement prints its impairment add-backs as rows of their own; no
dedicated line claimed them, so the sweep filed them on Other Non-Cash Adjs(Oper). The model then
answered Amortization & Asset Impairment by citing three of them (743 + 2,367 + 42 = 3,152), and
the three stayed in the residual as well: operating cash flow published 409,684 against a printed
406,532, and 463,560 against 456,847 the year before.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.models import DocumentModel
from app.core.stage import PipelineContext
from app.services.rollups import CARVED_FROM_FACE
from app.stages.line_item_llm import LineItemLlmStage

RESIDUAL = "cf_oper_indirect__other_non_cash_adjs_oper"
AMORT = "cf_oper_indirect__amortization_and_asset_impairment"


def _row(key: str, caption: str, amount: str, method: str, flags=()) -> LineItem:
    li = LineItem(source_label=caption, canonical_key=key)
    ev = ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                        basis=Basis.CONSOLIDATED, period_label="current")
    ev.confidence.flags.extend(flags)
    li.set_value(ev)
    li.confidence.method = method
    return li


def _release(rows, claims, items=()):
    doc = DocumentModel(filename="f.pdf")
    doc.line_items = list(rows)
    ctx = PipelineContext()
    released = LineItemLlmStage._release_swept_rows(
        doc, claims, ctx, SimpleNamespace(items=list(items)))
    return released, ctx


def test_the_cited_swept_rows_leave_the_residual_and_the_rest_stay():
    cited = [_row(RESIDUAL, "Accrual of provision for impairment of receivables, net", "743",
                  "residual"),
             _row(RESIDUAL, "Write down in inventories, net", "2367", "residual"),
             _row(RESIDUAL, "Impairment of other non-current assets", "42", "residual")]
    kept = _row(RESIDUAL, "Safety fund reserve", "105", "residual")
    amort = _row(AMORT, "Amortization & Asset Impairment", "3152", "llm")
    released, ctx = _release(cited + [kept, amort], {str(r.id): [AMORT] for r in cited})
    assert released == 3
    assert all(r.values == {} and f"swept_row_claimed_by:{AMORT}" in r.confidence.flags
               for r in cited)
    assert [str(ev.value) for ev in kept.values.values()] == ["105"]
    assert len([line for line in ctx.logs if "leaves the residual" in line]) == 3


def test_a_line_of_another_statement_section_reads_the_row_and_does_not_take_it():
    swept = _row(RESIDUAL, "Write down in inventories, net", "2367", "residual")
    released, _ = _release([swept], {str(swept.id): ["sub__cfo_impairment"]})
    assert released == 0 and swept.values


def test_a_row_that_is_not_swept_is_not_released_here():
    printed = _row(RESIDUAL, "Other non-cash items", "500", "exact")
    released, _ = _release([printed], {str(printed.id): [AMORT]})
    assert released == 0 and printed.values


def test_a_claimant_that_adds_the_figure_into_no_total_leaves_the_money_where_it_is():
    swept = _row(RESIDUAL, "Write down in inventories, net", "2367", "residual")
    carved = _row(AMORT, "Amortization & Asset Impairment", "2367", "llm", [CARVED_FROM_FACE])
    released, _ = _release([swept, carved], {str(swept.id): [AMORT]})
    assert released == 0 and swept.values
