"""Review of the whole-PDF sign decision and the rules fill: defects found and fixed.

A FIGURE FOUND ON NO CELL THE RULES READ keeps the route's own sign rule for a cash-flow row: a
payment caption is an outflow, negative whether or not the model said so (`stages.whole_pdf.
_CAS_PAYMENT`). The sign fix made 股份支付 / 股权支付 a noun (a share-based payment, an add-back in
the indirect method), which is right for 688008 p227's 股份支付. But a CAS caption also uses those
characters as the VERB: 回购股份支付的现金 (cash paid to buy back shares) and 购买少数股东股权支付的
现金 (cash paid for a minority stake) are payments. The look-behind took them for the noun, so an
unsigned repurchase the rules read no cell for came out POSITIVE on Purchase/Sale of Treasury Stock
— the opposite of what the route wrote before the fix, and of the outflow it is.

No filing in the corpus prints either caption, so the test drives `_as_read` directly with the
figure a model gives as printed (unsigned), as the route's own sign tests do.
"""
from __future__ import annotations

import json
from decimal import Decimal


def _items():
    from app.schemas.line_items import load_line_item_set
    from tests.whole_pdf_standin import HK_SET, TEMPLATES

    cfg = load_line_item_set(json.loads((TEMPLATES / HK_SET).read_text(encoding="utf-8")),
                             resolve=True)
    return {i.key: i for i in cfg.items}


def _reading(key, caption, amount):
    from app.services import whole_pdf as W

    return W.Reading(key=key, basis="consolidated", period="current", amount=Decimal(amount),
                     caption=caption, period_end="", part=0, check=W.Check("on_page", 0),
                     on_face=True)


def test_a_share_repurchase_or_a_minority_stake_paid_in_cash_is_a_payment():
    from app.stages.whole_pdf import _CAS_PAYMENT

    for paid in ("回购股份支付的现金", "回购股份所支付的现金", "购买少数股东股权支付的现金",
                 "收购子公司少数股东股权支付的现金", "購買少數股東股權支付的現金",
                 "回购股份支付现金"):
        assert _CAS_PAYMENT.search(paid), paid
    # The noun stays a noun — the indirect method's add-back and the note's wording.
    for noun in ("股份支付", "以权益结算的股份支付", "股份支付费用", "股权支付",
                 "以现金结算的股份支付", "股份支付計入所有者權益的金額"):
        assert not _CAS_PAYMENT.search(noun), noun


def test_an_unlocated_share_repurchase_is_written_as_the_outflow_it_is():
    """Found on no cell (no verdict): the caption decides, and a repurchase is paid out."""
    from app.stages.whole_pdf import _as_read

    item = _items()["cf_financing__purchase_sale_treasury_stock"]
    flags: list = []
    value, settled = _as_read(item, _reading(item.key, "回购股份支付的现金", "300031332.07"), flags)
    assert (value, settled) == (Decimal("-300031332.07"), True)
    assert "whole_pdf:cas_outflow_negated" in flags
    # The indirect method's share-based payment, an add-back, keeps its printed sign.
    other = _items()["cf_oper_indirect__other_non_cash_adjs_oper"]
    assert _as_read(other, _reading(other.key, "股份支付", "58848996.62"), []) == (
        Decimal("58848996.62"), True)
