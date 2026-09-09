#!/usr/bin/env python
"""What each part of a mapping request costs, so what to cut is a measurement not a taste.

Builds a real request (no network) and reports the character cost of every key, and within
`candidates`, of every per-concept field — because that block is the bulk and the question is
which of its fields earn their place.
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import get_settings                                        # noqa: E402
from app.schemas.line_items import load_line_item_set                      # noqa: E402
from app.services.mapping import OntologyMatcher                           # noqa: E402
from app.services.working_view import build_working_view                   # noqa: E402

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


class _Capture:
    id = "capture"

    def __init__(self):
        self.system = ""
        self.user = ""

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.system, self.user = system, messages[-1]["content"]
        raise RuntimeError("captured")


def main() -> int:
    settings = get_settings()
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    ontology = build_working_view(st)
    spy = _Capture()
    m = OntologyMatcher(ontology, locale="en", settings=settings, llm_provider=spy)
    try:
        m.match_batch([("r1", "Trade and other receivables")],
                      statement="balance_sheet", sections={"r1": None})
    except Exception:
        pass
    if not spy.user:
        print("no request built")
        return 1

    payload = json.loads(spy.user)
    total = len(spy.system) + len(spy.user)
    line = "─" * 72

    print(f"\n{line}\nTOTAL  {total:,} chars  (~{total // 4:,} tokens)\n{line}")
    print(f"  {'system prompt':28s} {len(spy.system):8,d}  {100*len(spy.system)//total:3d}%")
    for key, value in payload.items():
        cost = len(json.dumps(value, ensure_ascii=False))
        print(f"  {key:28s} {cost:8,d}  {100*cost//total:3d}%")

    cands = payload.get("candidates") or []
    if not cands:
        return 0
    print(f"\n{line}\nINSIDE `candidates` — {len(cands)} concepts\n{line}")
    field_cost: Counter = Counter()
    field_on: Counter = Counter()
    for c in cands:
        for k, v in c.items():
            field_cost[k] += len(json.dumps(v, ensure_ascii=False))
            field_on[k] += 1
    block = sum(field_cost.values())
    print(f"  {'field':28s} {'chars':>8s} {'% of block':>11s} {'on':>6s}")
    for k, cost in field_cost.most_common():
        print(f"  {k:28s} {cost:8,d} {100*cost/block:10.1f}% {field_on[k]:5d}/{len(cands)}")
    print(f"\n  candidates block: {block:,} chars = {100*block//total}% of the whole request")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
