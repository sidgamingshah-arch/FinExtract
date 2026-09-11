#!/usr/bin/env python
"""The eight focus figures AS THE PRODUCT PUBLISHES THEM — through `concept_value`.

WHY THIS EXISTS, AND IT IS A CORRECTION. `focus_post_derivation.py` reports the FIRST row carrying
a canonical key that has a value:

    cur = next((_fig(r, "current") for r in rows if _fig(r, "current") is not None), None)

The product does not do that. `services.periods.concept_value` — which the statement grid, the
accounting checks and the xlsx export all read through (`export._cell_value`,
`routes/documents._row_value`) — SUMS every row mapping to the concept, less the rows `summable`
judges to be the same fact printed twice. So for any concept carried by more than one row, the
older script has been reporting a number the product never shows.

MEASURED, that is not hypothetical: `bs_nca__secur_and_other_fincl_assets_ltp` is carried by FOUR
rows on the reference filing (two consolidated classes, two standalone), and
`map_line_items:split_declined` says so in the log.

This script serialises the document with the SAME `_serialize_rows` the API worker uses and then
asks `concept_value`, so what it prints is what a reader would see. Both numbers are shown side by
side, because the difference between them is itself the finding.

    python scripts/focus_as_published.py ../_run8/laisun.pdf ../_run8/suncreate.pdf
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

FOCUS = ["is_pl__sales_revenues",
         "is_pl__deprec_and_impairment_oper_exp",
         "is_pl__deprec_and_impairment_cos",
         "bs_ca__secur_and_other_fincl_assets_cp",
         "bs_nca__secur_and_other_fincl_assets_ltp",
         "bs_nca__due_from_related_parties_ltp",
         "bs_ca__other_receivables_cp",
         "notes__contingent_liabilities"]


def _money(v):
    if v is None:
        return "—"
    f = float(v)
    return f"{f:,.2f}" if f % 1 else f"{int(f):,}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+", type=pathlib.Path)
    args = ap.parse_args()

    from app.api.routes.extractions import _serialize_rows
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.periods import concept_value, summable
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    view = build_working_view(cfg)
    settings = get_settings()
    settings.extraction.llm_mapping = False
    defs = {i.key: i for i in cfg.items}

    for pdf in args.pdfs:
        doc, _ctx = run_extraction(pdf.read_bytes(), filename=pdf.name, ontology=view,
                                   template=None, line_items=cfg)
        rows = _serialize_rows(doc, view)
        print("\n" + "=" * 100)
        print(f"{pdf.name}  —  {len(rows)} rows")
        print("=" * 100)
        print(f"{'key':<44}{'PUBLISHED':>16}{'first-row':>16}  rows  contributions")
        print("-" * 100)
        for key in FOCUS:
            group = [r for r in rows if r.get("canonical_key") == key]
            published = concept_value(group, "consolidated", "current")
            counted = summable(group, "consolidated", "current")
            first = next((n for _r, n in
                          [(r, next((float(v["value"]) for v in (r.get("values") or [])
                                     if v.get("basis") == "consolidated"
                                     and v.get("period_label") == "current"
                                     and v.get("value") is not None), None))
                           for r in group] if n is not None), None)
            contrib = ", ".join(f"{_money(n)}" for _r, n in counted) or "—"
            flag = "  <<< DIFFERS" if (published is not None and first is not None
                                       and abs(published - first) > 0.5) else ""
            print(f"{key:<44}{_money(published):>16}{_money(first):>16}"
                  f"{len(group):>6}  {contrib}{flag}")
            if len(counted) > 1:
                for r, n in counted:
                    print(f"{'':<44}{'':>16}{'':>16}{'':>6}    + {_money(n):>16}  "
                          f"{str(r.get('source_label'))[:44]!r}")
        # Every concept in the set carried by more than one row, since the above is only the eight.
        multi = {}
        for r in rows:
            k = r.get("canonical_key")
            if k:
                multi[k] = multi.get(k, 0) + 1
        wide = {k: n for k, n in multi.items() if n > 1 and str(getattr(defs.get(k), "type", ""))
                == "derived"}
        print(f"\n  derived concepts carried by MORE THAN ONE row: {wide or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
