#!/usr/bin/env python
"""ONE PROVIDER CALL PER LINE ITEM, against whichever provider can actually serve the request.

WHY A RUNNER AND NOT A COMMAND LINE. Three things have to be true at once and each is set in a
different place: the provider has to be one that accepts a ~30,000-token request (Groq's free tier
caps at 8,000 tokens per minute and refuses every one), the batch size has to be 1, and the key has
to reach the process without being written anywhere. Getting one of the three wrong produces a run
that looks fine and quietly used the deterministic tiers, which is the failure this whole area keeps
producing. So the three are set together, here, and echoed before the run starts.

PRECEDENCE MATTERS AND IS EASY TO GET BACKWARDS: `.env` is loaded FIRST and the overrides are
applied AFTER, because pydantic reads `os.environ` at the same precedence either way — load them in
the other order and `.env`'s Groq settings silently win.

    python scripts/run_one_call_per_line.py ../_run8/laisun.pdf \
        --base-url https://generativelanguage.googleapis.com/v1beta/openai/ \
        --key-env GEMINI_API_KEY --model gemini-flash-latest

Add `--batch N` to compare against batching; the default is 1, which is the point of the script.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ENV = pathlib.Path(__file__).resolve().parent.parent / ".env"
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--key-env", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("-o", "--out", default="")
    args = ap.parse_args()

    # 1. the gitignored .env, into this process only
    for line in ENV.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()
    # 2. …then the overrides, so they beat what .env said
    os.environ["FINEX_LLM__PROVIDER"] = "openai_compatible"
    os.environ["FINEX_LLM__BASE_URL"] = args.base_url
    os.environ["FINEX_LLM__API_KEY_ENV"] = args.key_env
    os.environ["FINEX_LLM__MODEL"] = args.model
    os.environ["FINEX_EXTRACTION__LLM_BATCH_MAX_ITEMS"] = str(args.batch)
    os.environ["FINEX_EXTRACTION__LLM_MAX_CONCURRENCY"] = str(args.concurrency)

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    settings = get_settings()
    settings.llm.provider = "openai_compatible"
    settings.llm.base_url = args.base_url
    settings.llm.api_key_env = args.key_env
    settings.llm.model = args.model
    settings.extraction.llm_mapping = True
    settings.extraction.llm_batch_max_items = args.batch
    settings.extraction.llm_max_concurrency = args.concurrency

    pdf = pathlib.Path(args.pdf).resolve()
    print(f"filing      : {pdf.name}")
    print(f"provider    : {settings.llm.base_url}")
    print(f"model       : {settings.llm.model}")
    print(f"key from    : {args.key_env} "
          f"({'set' if os.environ.get(args.key_env) else 'MISSING'})")
    print(f"rows/call   : {settings.extraction.llm_batch_max_items}")
    print(f"concurrency : {settings.extraction.llm_max_concurrency}")
    print(f"focus_only  : {settings.extraction.llm_focus_only}")
    print("running…", flush=True)

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    began = time.time()
    doc, ctx = run_extraction(pdf.read_bytes(), filename=pdf.name,
                              ontology=build_working_view(cfg), template=None, line_items=cfg)
    took = time.time() - began

    by_key: dict[str, list] = {}
    for li in doc.line_items:
        if li.canonical_key:
            by_key.setdefault(li.canonical_key, []).append(li)
    kids: dict[str, list[str]] = {}
    for item in cfg.items:
        if getattr(item, "parent", ""):
            kids.setdefault(item.parent, []).append(item.key)
    defs = {i.key: i for i in cfg.items}

    # THE HEADLINE IS WHETHER THE MODEL WAS ACTUALLY USED. Every other number in this report is
    # meaningless if `llm_calls` is 0 — that is a deterministic run wearing an LLM label, and it is
    # exactly what a rate-limited provider produces.
    calls = getattr(ctx, "llm_calls", 0) or 0
    print(f"\n{'=' * 96}")
    print(f"{pdf.name}: {len(doc.pages)} pages, {took:.0f}s, "
          f"LLM CALLS = {calls}, tokens in/out = "
          f"{getattr(ctx, 'llm_input_tokens', None)}/{getattr(ctx, 'llm_output_tokens', None)}")
    if not calls:
        print("  !! ZERO SUCCESSFUL CALLS — the figures below came from the deterministic tiers.")
    print("=" * 96)

    def figure(key, period="current"):
        for row in by_key.get(key, []):
            for ev in row.values.values():
                if getattr(ev, "period_label", None) == period and ev.value is not None:
                    return ev.value
        return None

    out_rows = []
    for key in FOCUS:
        cur, pri = figure(key), figure(key, "prior")
        rung = ""
        for row in by_key.get(key, []):
            for trail in (row.derivation or {}).values():
                if (trail.get("method") or "").startswith("cascade"):
                    rung = trail["method"] + (f"  [{trail.get('formula')}]"
                                              if trail.get("formula") else "")
                    break
        ks = kids.get(key) or []
        filled = [k for k in ks if by_key.get(k)]
        label = getattr(defs.get(key), "label", "") or key
        print(f"\n  {label}")
        print(f"    CURRENT   {cur if cur is not None else '(empty)'}")
        print(f"    PRIOR     {pri if pri is not None else ''}")
        print(f"    rung      {rung or '(none — face or unmapped)'}")
        print(f"    sub-items {len(filled)} of {len(ks)} filled")
        for k in filled:
            kv = figure(k)
            how = ""
            for row in by_key.get(k, []):
                for trail in (row.derivation or {}).values():
                    how = trail.get("method") or ""
                    break
                if how:
                    break
            print(f"        + {k:<46s} {str(kv):>22s}   {how}")
        out_rows.append({"key": key, "label": label, "current": str(cur) if cur else None,
                         "prior": str(pri) if pri else None, "rung": rung,
                         "sub_items_filled": len(filled), "sub_items": len(ks)})

    interesting = [line for line in ctx.logs
                   if any(t in line for t in ("deterministic_fallback_applied", "focus_routing",
                                              "llm_planned_calls", "batch_"))]
    print("\n  run log:")
    for line in interesting[:12]:
        print(f"    {line}")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps({
            "filing": pdf.name, "seconds": round(took, 1), "llm_calls": calls,
            "model": args.model, "base_url": args.base_url,
            "rows_per_call": args.batch, "concepts": out_rows,
            "log": ctx.logs}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
