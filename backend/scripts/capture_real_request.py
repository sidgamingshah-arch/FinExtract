#!/usr/bin/env python
"""ONE REAL MAPPING REQUEST, captured off a real filing, itemised block by block.

`show_llm_request.py` prints the request's SHAPE on stand-in data — no PDF, so no identified notes
and no real per-row context. That is the right tool for "what fields exist". It is the wrong tool for
"what is actually sent", because the two biggest blocks of a real request are the ones stand-in data
cannot produce.

This runs the real pipeline against a real PDF with a provider that records the first request and
then lets the run continue, so what is dumped is the genuine payload: the system prompt as sent, the
user message as sent, every block's size in characters and estimated tokens, and — the point of the
exercise — WHICH KEYS the model is actually able to name.

    python scripts/capture_real_request.py ../_run8/laisun.pdf -o _request_laisun.json

Estimated tokens are chars/4, which is what the batching math itself assumes; they are for
apportioning the request, not for billing.
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


class _CaptureFirst:
    """Records EVERY request's size, and the first one verbatim.

    Every call, not just the first, because the question "what does one call cost" is only half of
    "what does a filing cost" — the calls differ in row count and in how many candidates their
    statement offers, and a projection built off call 1 alone would be wrong in both directions.
    """

    id = "capture"

    def __init__(self) -> None:
        self.calls = 0
        self.system = ""
        self.user = ""
        self.per_call: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.calls += 1
        user = messages[-1]["content"]
        if self.calls == 1:
            self.system, self.user = system, user
        try:
            payload = json.loads(user)
        except Exception:
            payload = {}
        sizes = {k: len(json.dumps(v, ensure_ascii=False)) for k, v in payload.items()}
        self.per_call.append({
            "call": self.calls,
            "rows": len(payload.get("source_items") or []),
            "candidates": len(payload.get("candidates") or []),
            "system_chars": len(system),
            "block_chars": sizes,
            "total_chars": len(system) + sum(sizes.values()),
        })
        # An empty mapping list is a valid reply meaning "nothing resolved", so the pipeline
        # proceeds on its deterministic tiers rather than dying inside the capture.
        return response_schema.model_validate({"mappings": []}), {}


def _tokens(text: str) -> int:
    return round(len(text) / 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="_request.json")
    ap.add_argument("--batch", type=int, default=None,
                    help="rows per call (extraction.llm_batch_max_items). --batch 1 is one call "
                         "per line item.")
    args = ap.parse_args()

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    settings = get_settings()
    settings.extraction.llm_mapping = True
    if args.batch:
        settings.extraction.llm_batch_max_items = args.batch

    spy = _CaptureFirst()
    pdf = pathlib.Path(args.pdf).resolve()

    # `map_ontology` resolves its provider with `registry.get("llm", settings.llm.provider)`, so
    # registering the spy under a name and selecting that name intercepts a real run without
    # touching the pipeline — and, importantly, without spending a provider budget.
    from app.ports.registry import registry

    registry.register("llm", "capture", lambda: spy)
    settings.llm.provider = "capture"

    began = time.time()
    doc, _ctx = run_extraction(pdf.read_bytes(), filename=pdf.name,
                               ontology=build_working_view(cfg), template=None, line_items=cfg)
    took = time.time() - began

    if not spy.user:
        print(f"No request was captured after {spy.calls} call(s). Either llm_mapping stayed off "
              f"or the provider factory was not the interception point.")
        return 1

    payload = json.loads(spy.user)
    blocks = []
    for key, value in payload.items():
        text = json.dumps(value, ensure_ascii=False)
        blocks.append({"block": key,
                       "kind": (f"list of {len(value)}" if isinstance(value, list)
                                else "string" if isinstance(value, str) else type(value).__name__),
                       "chars": len(text), "tokens": _tokens(text)})
    sys_block = {"block": "SYSTEM PROMPT", "kind": "string",
                 "chars": len(spy.system), "tokens": _tokens(spy.system)}
    total = sys_block["chars"] + sum(b["chars"] for b in blocks)
    for b in [sys_block] + blocks:
        b["pct"] = round(100.0 * b["chars"] / total, 1)

    # WHICH KEYS THE MODEL CAN NAME, which is the question the block sizes do not answer.
    offered = [c.get("canonical_key") for c in (payload.get("candidates") or [])]
    from_notes = sorted({k for e in (payload.get("identified_notes") or [])
                         for k in (e.get("identified_for") or ())})
    by_key = {i.key: i for i in cfg.items}
    derived = {k for k, i in by_key.items() if getattr(i, "cascade", None)}
    subs = {k for k, i in by_key.items() if getattr(i, "parent", "")}

    fleet_chars = sum(c["total_chars"] for c in spy.per_call)
    fleet_rows = sum(c["rows"] for c in spy.per_call)
    # WHAT IS PAID ONCE PER CALL vs ONCE PER ROW, which is the whole of the batching economics.
    per_call_fixed = [c["total_chars"] - c["block_chars"].get("source_items", 0)
                      for c in spy.per_call]
    row_chars = sum(c["block_chars"].get("source_items", 0) for c in spy.per_call)
    out = {
        "filing": pdf.name,
        "batch_max_items": settings.extraction.llm_batch_max_items,
        "seconds": round(took, 1),
        "whole_run": {
            "calls": spy.calls,
            "rows_decided": fleet_rows,
            "chars": fleet_chars,
            "tokens": _tokens("x" * fleet_chars),
            "fixed_chars_per_call_avg": round(sum(per_call_fixed) / max(1, len(per_call_fixed))),
            "row_chars_total": row_chars,
            "row_chars_per_row_avg": round(row_chars / max(1, fleet_rows)),
        },
        "per_call": spy.per_call,
        "calls_in_the_run": spy.calls,
        "rows_in_this_call": len(payload.get("source_items") or []),
        "totals": {"chars": total, "tokens": _tokens("x" * total)},
        "blocks": sorted([sys_block] + blocks, key=lambda b: -b["chars"]),
        "user_message_top_level_keys": sorted(payload),
        "nameable_keys": {
            "offered_as_candidates": len(offered),
            "named_by_identified_notes": len(from_notes),
            "candidates_that_are_derived_parents": sorted(set(offered) & derived),
            "candidates_that_are_sub_items": sorted(set(offered) & subs),
            "identified_for_that_are_derived_parents": sorted(set(from_notes) & derived),
            "identified_for_that_are_sub_items": len(set(from_notes) & subs),
        },
        "system_prompt": spy.system,
        "user_message": payload,
    }
    dest = pathlib.Path(args.out)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    w = out["whole_run"]
    print(f"{pdf.name}: batch_max_items={out['batch_max_items']}, {took:.1f}s")
    print(f"  WHOLE RUN: {w['calls']} call(s) deciding {w['rows_decided']} rows, "
          f"{w['chars']:,} chars ~= {w['tokens']:,} tokens")
    print(f"    paid ONCE PER CALL (avg): {w['fixed_chars_per_call_avg']:,} chars")
    print(f"    paid ONCE PER ROW  (avg): {w['row_chars_per_row_avg']:,} chars")
    print(f"  rows per call: {[c['rows'] for c in spy.per_call]}")
    print("\n  --- call 1, itemised ---")
    print(f"  {out['rows_in_this_call']} rows asked about in this one call")
    print(f"  {total:,} chars ~= {_tokens('x' * total):,} tokens\n")
    print(f"  {'block':28s} {'chars':>8s} {'tokens':>8s}  {'%':>5s}  kind")
    for b in out["blocks"]:
        print(f"  {b['block']:28s} {b['chars']:>8,} {b['tokens']:>8,}  {b['pct']:>5}  {b['kind']}")
    n = out["nameable_keys"]
    print(f"\n  candidates offered            : {n['offered_as_candidates']}")
    print(f"  of those, DERIVED PARENTS     : {len(n['candidates_that_are_derived_parents'])}"
          f"  {n['candidates_that_are_derived_parents'] or ''}")
    print(f"  of those, sub-items           : {len(n['candidates_that_are_sub_items'])}")
    print(f"  keys named via identified_for : {n['named_by_identified_notes']}"
          f"  (sub-items: {n['identified_for_that_are_sub_items']}, "
          f"derived parents: {len(n['identified_for_that_are_derived_parents'])})")
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
