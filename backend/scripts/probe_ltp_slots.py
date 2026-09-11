#!/usr/bin/env python
"""Every value slot on one line item, with its basis — to see WHY a figure was not replaced.

The symptom: `bs_nca__secur_and_other_fincl_assets_ltp` carries a trail reading `cascade:LTP_P1`
(so `_fill_by_cascade` reached `_write`) while the figure the report reads is the printed 128,412
rather than the rung's 788,507. Either the overwrite did not fire, or it wrote to a DIFFERENT slot
and the reader picked the other one. This prints every row and every slot so the answer is read
rather than reasoned about.

    python scripts/probe_ltp_slots.py ../_run8/laisun.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

KEYS = ["bs_nca__secur_and_other_fincl_assets_ltp",
        "sub__ltp_nc_portion_of_fincl_asset_notes"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    args = ap.parse_args()

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    settings = get_settings()
    settings.extraction.llm_mapping = False

    doc, ctx = run_extraction(args.pdf.read_bytes(), filename=args.pdf.name,
                              ontology=build_working_view(cfg), template=None, line_items=cfg)

    for key in KEYS:
        rows = [li for li in doc.line_items if li.canonical_key == key]
        print(f"\n{key}  —  {len(rows)} row(s) in the document")
        for n, li in enumerate(rows, 1):
            print(f"  row {n}: source_label={li.source_label!r} role={li.role}")
            for slot, ev in (li.values or {}).items():
                basis = getattr(ev, "basis", None)
                print(f"      slot={slot!r:26s} basis={str(getattr(basis, 'value', basis))!r:16s} "
                      f"period={str(getattr(ev, 'period_label', None))!r:10s} "
                      f"value={ev.value}")
            print(f"      flags {list(li.confidence.flags or [])}")
            for name, tr in (li.derivation or {}).items():
                print(f"      trail[{name}] method={tr.get('method')} flags={tr.get('flags')}")

    # THE LOG LINES THE STAGE WROTE, which say which branch it took.
    wanted = ("bs_nca__secur_and_other_fincl_assets_ltp", "cascade_over_printed", "kept over")
    print("\nrelevant log lines")
    for line in (getattr(ctx, "logs", None) or []):
        text = line if isinstance(line, str) else str(getattr(line, "message", line))
        if any(w in text for w in wanted):
            print(f"  {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
