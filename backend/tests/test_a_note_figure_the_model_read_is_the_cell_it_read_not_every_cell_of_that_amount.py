"""ON THE WHOLE-PDF ROUTE A NOTE ROW THE MODEL PLACED ON ONE LINE IS NOT READ AGAIN FOR ANOTHER —
BUT ONLY THAT CELL, NOT EVERY CELL OF THE PAGE THAT PRINTS THE SAME AMOUNT
(`services.whole_pdf_rules.model_figures`, `drop_cited`).

`drop_cited` decided "the model already read this note figure" by page and amount alone. One row
can print the same amount in two columns: a fully provided related-party receivable, gross =
allowance. 000709 十四 prints 河北承钢工贸有限责任公司 with 账面余额 565,528,459.41 and 坏账准备
565,528,459.41. With the gross part answered by the model (its sixteen rows, 952,791,540.11) and
the allowance part left empty, the rules' allowance fill dropped that row as "placed on the gross
part": the allowance came to 8,332,678.95 instead of 573,861,138.36, and Trade Receivables
(Related Parties) was published as 944,458,861.16 where the rules route publishes 378,930,401.75.

Now each figure the model placed is found again on the rules' note cell it sits on (by the box
`services.whole_pdf.locate` drew for it), and only that cell is "placed". A figure that cannot be
put on one cell — no box, no cell near it, or two cells equally near — still holds every cell of
its page and amount, as before: that errs on the side of not counting one figure twice.

The filing test drives the REAL pipeline on the real filing's pages, with the shipped stand-in
answering from a SCRIPT (`tests.whole_pdf_standin`): a scripted answer shows what the pipeline does
with that answer, never what a model would answer.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from tests.whole_pdf_standin import filing_path, run_whole_pdf, script_figure, slot

STEM = "000709_hesteel_2024_prc"
GROSS = "sub__rp_trade_receivable_gross"
ALLOWANCE = "sub__rp_trade_receivable_allowance"
PARENT = "bs_ca__trade_receivables_related_parties"

#: 十四 (report page 196): the sixteen related parties' 账面余额, as the rules read them.
_GROSS_ROWS = [
    ("唐山时创高温材料股份有限公司", "1007351.91"), ("青岛河钢新材料科技股份有限公司", "58602282.04"),
    ("青岛河钢复合新材料科技有限公司", "619970.82"), ("合肥河钢新材料科技有限公司", "26800244.18"),
    ("承德中滦煤化工有限公司", "14917090.98"), ("河北承钢工贸有限责任公司", "565528459.41"),
    ("唐山中润煤化工有限公司", "10659341.40"), ("唐山唐钢气体有限公司", "54600891.70"),
    ("哈斯科（唐山）冶金材料科技有限公司", "7219617.97"), ("滦县唐钢气体有限公司", "21523.46"),
    ("普锐特(唐山)冶金技术服务有限公司", "21860769.89"), ("河钢浦项（广东）汽车板有限公司", "2030724.72"),
    ("河钢浦项汽车板有限公司", "113642306.02"), ("河南邯钢福然德汽车部件有限公司", "75075742.17"),
    ("河北邯钢新锐稀有气体科技股份有限公司", "2097.86"), ("青岛河钢大河金属材料有限公司", "203125.58")]


def _box(x0, y0):
    from app.core.models.geometry import BBox
    return BBox(x0=x0, y0=y0, x1=x0 + 0.08, y1=y0 + 0.012)


def _note_doc():
    """One note row printing 565,528,459.41 twice (gross and allowance), and a model row whose
    figure `locate` put on the gross column."""
    from app.core.models import DocumentModel
    from app.core.models.enums import Basis
    from app.core.models.geometry import Provenance
    from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable

    row = NoteItem(raw_label="河北承钢工贸有限责任公司", note_number="十四")
    for key, x0 in (("current", 0.411), ("current:allowance", 0.538)):
        row.values[key] = ExtractedValue(
            value=Decimal("565528459.41"), basis=Basis.CONSOLIDATED, period_label=key,
            provenance=Provenance(page_index=195, bbox=_box(x0, 0.30)))
    doc = DocumentModel()
    doc.notes = [NotesTable(note_number="十四", title="应收关联方款项", items=[row])]

    def model(box):
        li = LineItem(source_label="应收账款", canonical_key=GROSS)
        li.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal("565528459.41"),
                                    value_raw=Decimal("565528459.41"),
                                    provenance=Provenance(page_index=195, bbox=box,
                                                          value_bbox=box)))
        li.confidence.method = "llm"
        li.confidence.flags.append("whole_pdf")
        return li
    return doc, row, model


def _hit(ev):
    return NS(amount=Decimal(str(ev.value)), basis="consolidated", period="current",
              caption="河北承钢工贸有限责任公司", value=ev)


def test_only_the_cell_the_models_figure_sits_on_is_placed():
    from app.services import whole_pdf_rules as R

    doc, row, model = _note_doc()
    doc.line_items = [model(_box(0.4112, 0.3001))]          # on the gross column
    figures = R.model_figures(doc)
    gross, allowance = row.values["current"], row.values["current:allowance"]
    logged: list[str] = []
    kept = R.drop_cited([_hit(gross), _hit(allowance)], figures, ALLOWANCE, logged.append)
    assert [h.value for h in kept] == [allowance]
    assert len(logged) == 1 and "placed this figure on " + GROSS in logged[0]


def test_a_figure_that_cannot_be_put_on_one_cell_still_holds_every_cell_of_its_amount():
    from app.services import whole_pdf_rules as R

    doc, row, model = _note_doc()
    hits = [_hit(row.values["current"]), _hit(row.values["current:allowance"])]
    for box in (None, _box(0.90, 0.80)):                    # no box; a box near no cell
        doc.line_items = [model(box)]
        assert R.drop_cited(hits, R.model_figures(doc), ALLOWANCE) == []


@pytest.mark.skipif(not filing_path(STEM).exists(), reason=f"{STEM}.pdf not present")
def test_the_allowance_beside_a_gross_figure_of_the_same_amount_is_read(tmp_path, monkeypatch):
    total = sum((Decimal(a) for _c, a in _GROSS_ROWS), Decimal(0))
    assert total == Decimal("952791540.11")
    script = {"units": "元", "lines": {GROSS: [script_figure(
        str(total), page=196, caption="应收账款", parts=_GROSS_ROWS)]}}
    doc, ctx, _rec = run_whole_pdf(monkeypatch, tmp_path, STEM, script,
                                   pages=list(range(194, 198)))
    _row, ev = slot(doc, ALLOWANCE)
    assert ev is not None and ev.value == Decimal("573861138.36")
    _row, ev = slot(doc, PARENT)
    assert ev is not None and ev.value == Decimal("378930401.75")
    assert not any(ALLOWANCE in line and "NOT read" in line and "565528459.41" in line
                   for line in ctx.logs)
