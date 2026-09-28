"""The parts that read a mainland 关联方应收应付款项 table are in front of the model.

`llm_focus_only` is on, so a line the focus list does not name is never asked about, and none of
these seven was named: on a CAS filing the model never saw the one note that tabulates a related
party's balance by line item, and every figure below was the deterministic route's alone. Each is a
PART (`extract`, parented) — the layer a note actually prints — so each is one a call can answer.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from app.schemas.line_items import load_line_item_set
from app.services.line_item_requests import asked_about

RELATED_PARTY_BALANCE_PARTS = (
    "sub__rp_find_2",
    "sub__rp_find_3_gross",
    "sub__rp_find_3_allowance",
    "sub__rp_trade_receivable_gross",
    "sub__rp_trade_receivable_allowance",
    "sub__rp_other_payable",
    "sub__rp_payable_ltp",
)

_BACKEND = Path(__file__).resolve().parents[1]
_SET = _BACKEND / "app/sample/templates/output_csv_hk_line_items.json"


def test_they_are_on_the_focus_list():
    """Read off the SHIPPED file rather than the settings object, which other tests reconfigure."""
    shipped = tomllib.loads((_BACKEND / "config.toml").read_text(encoding="utf-8"))
    focus = set(shipped["extraction"]["llm_focus_keys"])
    assert set(RELATED_PARTY_BALANCE_PARTS) <= focus, sorted(set(RELATED_PARTY_BALANCE_PARTS) - focus)


def test_each_is_a_part_the_model_can_answer():
    shipped = load_line_item_set(json.loads(_SET.read_text(encoding="utf-8")), resolve=True)
    by_key = {i.key: i for i in shipped.items}
    for key in RELATED_PARTY_BALANCE_PARTS:
        item = by_key[key]
        assert asked_about(item), key
        assert getattr(item, "parent", ""), key
