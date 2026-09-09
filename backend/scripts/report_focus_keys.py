#!/usr/bin/env python
"""Report the eight output line items for a run, and say WHERE each figure came from.

WHY THIS EXISTS. The question asked of these runs was not "is the number right" but "did a
generic mechanism produce it, or a hand-enumerated list specific to these filings". Those two
answers look identical in the output — a filled cell is a filled cell — and they are told apart
only by the `method` and the provenance the row carries. So this prints the mechanism beside the
figure rather than the figure alone.

The classification is deliberately blunt, and errs toward calling a win non-generic:

    llm                  the model chose the concept for this caption          GENERIC
    exact / alias / rule the rulebook's own vocabulary matched the caption     GENERIC
    computed:<service>   a derivation stage published it, which on these
                         concepts rests on enumerated candidate titles         NOT GENERIC
    residual / supplemental
                         a sweep placed it, not a concept decision             WEAK

Reads the same `<stem>__result.json` `run_filing.py` writes, so it can be pointed at any run,
including the archived ones.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The eight the run was asked about — read from settings so this cannot drift from what the run
# actually routed to the model.
def _focus_keys() -> list[str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import get_settings
    return list(get_settings().extraction.llm_focus_keys)


_GENERIC = ("llm", "exact", "alias", "rule", "engine")
_WEAK = ("residual", "supplemental", "unclassified")


def _verdict(method: str, flags: list | None = None) -> str:
    """Classify a row, reading the FLAGS as well as `mapping_method`.

    A row can be decided by the model AND then have a derivation stage publish onto it — the
    stage's method overwrites `mapping_method`, and the model's contribution survives only as an
    `llm_reason:` flag. Reading the method alone therefore reported a genuine LLM win as
    `NOT GENERIC`: on the 四创电子 run the caption 长期应收款 was mapped to
    `bs_nca__due_from_related_parties_ltp` by the model, with its reasoning recorded, and the row
    was labelled a computed derivation. Both facts are true and both are now printed.
    """
    m = (method or "").lower()
    model_chose = any(str(f).startswith("llm_reason:") for f in (flags or []))
    if m.startswith("computed"):
        return ("MIXED: the model chose this concept; a derivation published onto the row"
                if model_chose else "NOT GENERIC (derivation over enumerated titles)")
    if any(m.startswith(p) for p in _WEAK):
        return "WEAK (a sweep, not a concept decision)"
    if any(m.startswith(p) for p in _GENERIC):
        return "generic"
    if model_chose:
        return "generic (model chose the concept)"
    return f"unclassified method {method!r}" if m else "no method recorded"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result", type=Path, help="a <stem>__result.json from run_filing.py")
    args = ap.parse_args()

    data = json.loads(args.result.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    keys = _focus_keys()

    mapping = (data.get("mapping") or {})
    print(f"{args.result.name}")
    print(f"  entity   {data.get('entity') or '?'}")
    print(f"  strategy {mapping.get('strategy')!r}  llm_calls={mapping.get('llm_calls')}"
          f"  model={mapping.get('model')!r}")
    if mapping.get("reason"):
        print(f"  reason   {str(mapping['reason'])[:220]}")

    by_key: dict[str, list[dict]] = {}
    for r in rows:
        k = r.get("canonical_key")
        if k:
            by_key.setdefault(k, []).append(r)

    filled = generic = concepts_with_a_figure = 0
    for k in keys:
        hits = by_key.get(k) or []
        print(f"\n  {k}")
        if not hits:
            print("      no row carries this concept at all")
            continue
        got_figure = False
        for r in hits:
            method = r.get("mapping_method") or ""
            verdict = _verdict(method, r.get("flags"))
            caption = r.get("source_label") or ""
            print(f"      caption  {caption[:70]!r}")
            print(f"      method   {method}   -> {verdict}")
            if r.get("flags"):
                print(f"      flags    {', '.join(map(str, r['flags']))[:150]}")
            # A concept carries one row per (basis, period); a row can hold several values.
            values = r.get("values") or []
            if not values:
                print("      value    -- none published --")
            for v in values:
                raw = v.get("value")
                prov = v.get("provenance") or {}
                where = (f"page {prov.get('page_index')}" if prov.get("page_index") is not None
                         else "provenance NULL - untraceable")
                print(f"      value    {str(raw):>20s}  [{v.get('basis')}/"
                      f"{v.get('period_display') or v.get('period_label')}]  {where}")
                if raw is not None:
                    filled += 1
                    got_figure = True
                    if verdict.startswith("generic") or verdict.startswith("MIXED"):
                        generic += 1
        if got_figure:
            concepts_with_a_figure += 1

    print(f"\n  ── {concepts_with_a_figure} of {len(keys)} concepts carry a figure; "
          f"{filled} value(s) in total, {generic} of them from a generic mechanism ──")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
