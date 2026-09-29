"""An HKFRS balance sheet's non-current FVTPL and FVOCI rows are Securities (LTP), not the residual.

`bs_nca__secur_and_other_fincl_assets_ltp` is `derived`, so a printed row reaches it only through its
face part, and that part named the CAS caption 其他非流动金融资产 alone. 嘉民 kaming prints
"Financial assets at fair value through profit or loss 按公平值計入損益之金融資產" (9,956) and the FVOCI
row (9,935) under Non-current assets, so both were swept into Other Non-current Assets.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.schemas.line_items import load_line_item_set

_SET = Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_line_items.json"
PART = "sub__ltp_face_other_non_current_fincl_assets"


def test_the_non_current_face_part_names_the_hkfrs_captions():
    item = {i.key: i for i in load_line_item_set(
        json.loads(_SET.read_text(encoding="utf-8")), resolve=True).items}[PART]
    aliases = set(item.aliases or ())
    for caption in ("Financial assets at fair value through profit or loss",
                    "Financial assets at fair value through other comprehensive income",
                    "按公平值計入損益之金融資產", "按公平值計入其他全面收益之金融資產"):
        assert caption in aliases, caption
    # Scoped to the non-current section, so the same caption under Current assets stays the
    # current part's.
    assert list(item.section_scope) == ["bs_nca"] and item.statement == "balance_sheet"
