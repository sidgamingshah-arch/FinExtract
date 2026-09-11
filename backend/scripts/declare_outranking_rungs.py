#!/usr/bin/env python
"""Declare `outranks_printed` on the rungs that RECONSTRUCT rather than restate.

WHY THESE FOUR AND NOT THE OTHER FORTY-ODD. The test is the rung's own subject, read off its
authored `note`, not its position or its magnitude:

  * `bs_nca__secur_and_other_fincl_assets_ltp`, all four rungs. Every one computes the NON-CURRENT
    PORTION of the in-scope financial-asset notes, less derivatives, less other receivables, less
    equity-method investments, with or without the current-portion carry-forward. A balance sheet
    prints "financial assets at fair value through profit or loss" as a line; it does not print
    that subtraction anywhere. So a caption binding to this line has found a DIFFERENT quantity —
    measured on laisun, 128,412 against the rung's 788,507 — and the rung is the answer.

  * Revenue's eight rungs, by contrast, get NOTHING. P1 is "主营业务收入 reported on the face of the
    income statement": the face IS this cascade's first choice, so a printed figure is what the
    cascade was looking for rather than a rival to it. P2-P4 are the revenue note's own total, the
    same figure from another page. P5-P8 are axis reconstructions ("by product", "by industry
    segment", "by geography", "by sales channel") which should SUM to the face and, when a filing's
    axis table is only partly read, do not — measured, P6 alone published 2,609,259 against the
    face's 4,995,768.

  * The remaining derived lines' rungs are left off until someone reads them the same way. Off is
    the safe default: it is the behaviour that has been in force, so an unexamined cascade changes
    nothing.

AND THE MODE COMES WITH IT. With the rung able to win on its own merits, the LTP line no longer
needs `extraction_mode: derive` to protect its figure, so it joins the other six derived lines at
`extract` — which is what lets a printed row fill it on a filing where no rung resolves at all.

Run:  python scripts/declare_outranking_rungs.py [--revert]
"""
from __future__ import annotations

import argparse
import json
import pathlib

SET = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
       / "output_csv_hk_line_items.json")

LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
OUTRANKING = {LTP: {"LTP_P1", "LTP_P2", "LTP_P3", "LTP_P4"}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    doc = json.loads(SET.read_text(encoding="utf-8"))
    changed: list[str] = []
    for item in doc.get("items", []):
        wanted = OUTRANKING.get(item["key"])
        if not wanted:
            continue
        for rung in item.get("cascade") or []:
            on = (not args.revert) and rung.get("id") in wanted
            if bool(rung.get("outranks_printed")) == on:
                continue
            if on:
                rung["outranks_printed"] = True
            else:
                rung.pop("outranks_printed", None)
            changed.append(f"{item['key']}/{rung['id']} -> outranks_printed={on}")
        # The mode the rung's own authority replaces. `derive` was holding the figure; the
        # declaration above now does, so the line can be `extract` like its six siblings.
        mode = "derive" if args.revert else "extract"
        if item.get("extraction_mode") != mode:
            changed.append(f"{item['key']} -> extraction_mode={mode}")
            item["extraction_mode"] = mode

    SET.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{SET.name}: {len(changed)} changed")
    for line in changed:
        print(f"    {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
