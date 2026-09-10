#!/usr/bin/env python
"""The eight fields POST DERIVATION, with the sub-line items each one was derived from.

WHAT THIS ANSWERS. The eight fields are `derived`: none of them is read off a page. Each one's
figure is produced by a declared cascade over its own sub-line items, and the 77 sub-items are the
layer that corresponds to something a filing actually prints — every one of them declares no
`extraction_mode`, so every one defaults to `extract`.

So "the output of the eight fields" is only half an answer on its own. A figure with no account of
WHICH rung produced it and which sub-items fed that rung cannot be checked, and the eight fields'
several rungs represent materially different disclosures: for depreciation, P1 sums four
operating-expense notes, P2 takes the profit-before-tax note's own callout, P3 is the total less the
cost-of-sales share. The number alone cannot tell those apart. This prints all three together.

    python scripts/focus_post_derivation.py ../_run8/laisun.pdf ../_run8/suncreate.pdf

`--no-llm` is the default and is not a limitation of the report: the sub-items are filled by the
`note_source` walk in `stages.note_sourced`, which is deterministic. An LLM, where one is
configured, ADDS to that by answering with a sub-line item it can cite (the prose-footnote case),
and the `filled by` column names whichever mechanism actually filled each sub-item.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

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


def _fig(li, period="current"):
    for ev in (li.values or {}).values():
        if getattr(ev, "period_label", None) == period and ev.value is not None:
            return ev.value
    return None


def _text(li):
    for ev in (li.values or {}).values():
        if getattr(ev, "value_text", None):
            return ev.value_text
    return None


def _money(v):
    if v is None:
        return ""
    f = float(v)
    return f"{f:,.2f}" if f % 1 else f"{int(f):,}"


def run(pdf: pathlib.Path, use_llm: bool):
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    settings = get_settings()
    settings.extraction.llm_mapping = bool(use_llm)

    began = time.time()
    doc, ctx = run_extraction(pdf.read_bytes(), filename=pdf.name,
                              ontology=build_working_view(cfg), template=None, line_items=cfg)
    took = time.time() - began

    by_key: dict[str, list] = {}
    for li in doc.line_items:
        if li.canonical_key:
            by_key.setdefault(li.canonical_key, []).append(li)

    defs = {i.key: i for i in cfg.items}
    kids: dict[str, list[str]] = {}
    for i in cfg.items:
        if getattr(i, "parent", ""):
            kids.setdefault(i.parent, []).append(i.key)
    return doc, ctx, cfg, defs, kids, by_key, took


def report(pdf, use_llm):
    doc, ctx, cfg, defs, kids, by_key, took = run(pdf, use_llm)
    line = "=" * 100
    print(f"\n{line}")
    print(f"{pdf.name}  —  {len(doc.pages)} pages, {len(doc.notes)} notes published, "
          f"{len(doc.line_items)} rows, {took:.0f}s, "
          f"llm_calls={getattr(ctx, 'llm_calls', 0) or 0}")
    print(line)

    for key in FOCUS:
        d = defs.get(key)
        rows = by_key.get(key) or []
        cur = next((_fig(r, "current") for r in rows if _fig(r, "current") is not None), None)
        pri = next((_fig(r, "prior") for r in rows if _fig(r, "prior") is not None), None)
        txt = next((_text(r) for r in rows if _text(r)), None)

        # WHICH RUNG, which is the half of the answer the figure cannot carry.
        rung = ""
        for r in rows:
            for tr in (r.derivation or {}).values():
                if (tr.get("method") or "").startswith("cascade"):
                    rung = tr["method"]
                    if tr.get("formula"):
                        rung += f"   [{tr['formula']}]"
                    break

        label = getattr(d, "label", "") or key
        print(f"\n  {label}")
        print(f"    key        {key}   ({getattr(d, 'type', '?')})")
        print(f"    CURRENT    {_money(cur) if cur is not None else (txt or '(empty)')}")
        print(f"    PRIOR      {_money(pri) if pri is not None else ''}")
        print(f"    derived by {rung or '(no cascade rung fired)'}")

        ks = kids.get(key) or []
        filled = [k for k in ks if by_key.get(k)]
        print(f"    sub-items  {len(filled)} of {len(ks)} filled")
        for k in ks:
            krows = by_key.get(k) or []
            if not krows:
                continue
            kc = next((_fig(r, "current") for r in krows if _fig(r, "current") is not None), None)
            kt = next((_text(r) for r in krows if _text(r)), None)
            how = ""
            for r in krows:
                for tr in (r.derivation or {}).values():
                    how = tr.get("method") or ""
                    break
                if how:
                    break
            note = ""
            for r in krows:
                if getattr(r, "note_number", None):
                    note = f"note {r.note_number}"
                    break
            print(f"        {'+':>1} {k:<46s} {_money(kc) if kc is not None else (kt or ''):>22s}"
                  f"   {how:<16s} {note}")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--llm", action="store_true",
                    help="enable the LLM tier as well (needs a configured provider and key)")
    args = ap.parse_args()

    for raw in args.pdfs:
        pdf = pathlib.Path(raw).resolve()
        if not pdf.exists():
            print(f"{raw}: NOT FOUND")
            continue
        report(pdf, args.llm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
