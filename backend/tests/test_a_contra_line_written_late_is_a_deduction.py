"""A BALANCE-SHEET CONTRA LINE WRITTEN AFTER `normalize` IS STORED AS THE DEDUCTION IT IS.

NEW FILE -> backend/tests/test_a_contra_line_written_late_is_a_deduction.py

`normalize` negates a contra line (`sign: natural_negative`) so the parent the template sums it into
deducts it. The model route and the note-sourced cascades write after it (`note_sourced._write`)
and put the figure on the row as the page printed it — and a CAS 坏账准备 cell is printed unsigned.
Measured on 湖北能源 000883: the allowance went out +409,601,688.47 and Net Trade Receivables
(Gross + Allowance) published 5,814,237,880.67 against a printed 4,995,034,503.73; the same on
every CAS filing of the corpus.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace as NS

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.services.rollups import balance_sheet_deductions
from app.stages.normalize import orient_balance_sheet_contras

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _template(name: str) -> dict:
    return json.loads((TEMPLATES / name).read_text(encoding="utf-8"))


def _line(key: str, *amounts: str) -> LineItem:
    li = LineItem(canonical_key=key, source_label=key, ordinal=0)
    for period, amount in zip(("current", "prior"), amounts):
        li.set_value(ExtractedValue(value_raw=Decimal(amount), value=Decimal(amount),
                                    basis=Basis.CONSOLIDATED, period_label=period))
    return li


def _run(*lines: LineItem):
    doc = DocumentModel.model_construct(line_items=list(lines))
    ctx = NS(template=_template("output_csv_hk_v1_template.json"), logs=[])
    ctx.log = ctx.logs.append
    return orient_balance_sheet_contras(doc, ctx, by="test"), doc, ctx


def _figures(li: LineItem) -> list[Decimal]:
    return [ev.value for ev in li.values.values()]


def test_the_balance_sheets_contra_lines_and_no_others():
    assert balance_sheet_deductions(_template("output_csv_hk_v1_template.json")) == {
        "bs_ca__allow_for_doubtful_accounts", "bs_equity__treasury_shares",
        "bs_nca__accum_deprec_and_impairment", "bs_nca__accum_goodwill_amortized",
        "bs_nca__accum_intgbl_assets_amort"}
    assert balance_sheet_deductions(_template("output_csv_icon_v1_template.json")) == {
        "bs_icon__accumulated_amortization", "bs_icon__accumulated_depreciation"}


def test_an_allowance_cited_unsigned_is_stored_negative():
    allowance = _line("bs_ca__allow_for_doubtful_accounts", "409601688.47", "359155797.09")
    changed, _doc, ctx = _run(allowance)
    assert changed == 2
    assert _figures(allowance) == [Decimal("-409601688.47"), Decimal("-359155797.09")]
    # The printed figure stays as printed, beside the stored one.
    assert [ev.value_raw for ev in allowance.values.values()] == [
        Decimal("409601688.47"), Decimal("359155797.09")]
    assert "sign_rule:natural_negative" in allowance.confidence.flags
    assert ctx.logs == ["test:balance_sheet_contras_oriented=2"]


def test_a_deduction_already_negative_and_a_pl_contra_line_are_left_alone():
    printed_negative = _line("bs_ca__allow_for_doubtful_accounts", "-81")
    # A net credit printed under an unsigned expense keeps its sign: `normalize`'s business.
    interest = _line("is_pl__interest_expense", "9816.85")
    changed, _doc, _ctx = _run(printed_negative, interest)
    assert changed == 0
    assert _figures(printed_negative) == [Decimal("-81")]
    assert _figures(interest) == [Decimal("9816.85")]


def test_a_slot_normalize_had_already_oriented_is_oriented_again_when_overwritten():
    allowance = _line("bs_ca__allow_for_doubtful_accounts", "100")
    (ev,) = allowance.values.values()
    ev.sign_normalised = True          # set by `normalize` on the face row before the model wrote
    _run(allowance)
    assert _figures(allowance) == [Decimal("-100")]
