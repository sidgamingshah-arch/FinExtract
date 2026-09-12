#!/usr/bin/env python
"""WHAT THE CORPUS-WIDE LIVE RUN FOUND — read off the JSON `live_run_corpus.py` wrote.

Four questions, in the order they decide anything:

  1. DID THE PATH RUN? calls made, calls failed, and why — a provider that refuses is a fact about
     the deployment, not about the configuration, and the two must not be confused.
  2. DID ANY FIGURE MOVE? Every focus whole and every part, deterministic against live, per filing.
     A figure that appears is the point; a figure that CHANGES is a much bigger claim and is called
     out separately.
  3. WHAT DID THE MODEL SAY WHEN IT SAID NOTHING? An empty `sources` is a real answer — "this
     filing does not state it in these notes" — and the share of them is the honest measure of how
     much of the configuration this request path can currently serve.
  4. WHAT WENT WRONG THAT IS NOT THE PROVIDER? Unresolved citations, refused row terms, foreign
     keys. Each names a defect in the request rather than in the run.

    python scripts/report_live_corpus.py ../_run8/LIVE_CORPUS.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


def money(v):
    if v in (None, ""):
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)[:16]
    return f"{f:,.2f}" if f % 1 else f"{int(f):,}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=pathlib.Path)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    data = json.loads(args.path.read_text(encoding="utf-8"))
    parts, wholes = data["parts"], data["wholes"]
    filings = data["filings"]

    print("=" * 116)
    print(f"LIVE CORPUS RUN — {len(filings)} filings, {len(parts)} focus parts each")
    print("=" * 116)

    # ── 1. did it run ─────────────────────────────────────────────────────────────────────────
    tc = tf = ti = to = 0
    ran = failed_all = 0
    print(f"\n  {'filing':<38s} {'calls':>6s} {'failed':>7s} {'in':>9s} {'out':>7s} "
          f"{'answered':>9s} {'written':>8s} {'strategy':>16s}")
    for name, e in filings.items():
        live = e.get("live")
        if live is None:
            print(f"  {name[:38]:<38s}   RUN FAILED: {str(e.get('live_error'))[:52]}")
            continue
        logs = " ".join(live["logs"])
        m = re.search(r"calls=(\d+) failed=(\d+) lines_answered=(\d+) figures_written=(\d+)", logs)
        calls, fails, ans, wrote = (int(x) for x in m.groups()) if m else (live["calls"], 0, 0, 0)
        tc += calls; tf += fails; ti += live["in_tokens"]; to += live["out_tokens"]
        ran += 1
        if calls == 0:
            failed_all += 1
        print(f"  {name[:38]:<38s} {calls:>6d} {fails:>7d} {live['in_tokens']:>9,} "
              f"{live['out_tokens']:>7,} {ans:>9d} {wrote:>8d} {live['strategy']:>16s}")
    print(f"\n  TOTAL calls {tc}   failed {tf}   in {ti:,}   out {to:,}"
          f"   filings that made no successful call: {failed_all} of {ran}")

    # ── 2. figures ────────────────────────────────────────────────────────────────────────────
    appeared: list[tuple] = []
    changed: list[tuple] = []
    vanished: list[tuple] = []
    for name, e in filings.items():
        b, l = e.get("baseline"), e.get("live")
        if not b or not l:
            continue
        for key in wholes + parts:
            was, now = b["values"].get(key), l["values"].get(key)
            if was == now:
                continue
            if was is None:
                appeared.append((name, key, now))
            elif now is None:
                vanished.append((name, key, was))
            else:
                changed.append((name, key, was, now))

    print("\n" + "=" * 116)
    print("  FIGURES — deterministic vs live")
    print("=" * 116)
    print(f"\n  APPEARED (empty before, a figure now): {len(appeared)}")
    for name, key, now in appeared:
        tag = " (part)" if key in parts else " WHOLE"
        print(f"      {name[:30]:<30s} {key[:44]:<44s} {money(now):>18s}{tag}")
    print(f"\n  CHANGED (a different figure): {len(changed)}")
    for name, key, was, now in changed:
        tag = " (part)" if key in parts else " WHOLE"
        print(f"      {name[:30]:<30s} {key[:44]:<44s} {money(was):>16s} -> {money(now):>16s}{tag}")
    print(f"\n  VANISHED (a figure before, empty now): {len(vanished)}")
    for name, key, was in vanished:
        print(f"      {name[:30]:<30s} {key[:44]:<44s} was {money(was)}   <-- REGRESSION")

    # ── 3 & 4. what the requests reported ─────────────────────────────────────────────────────
    unresolved = refused = foreign = unanswered = dup = 0
    prose = rows_cited = 0
    for name, e in filings.items():
        for line in (e.get("live") or {}).get("logs", []):
            if "citation NOT resolved" in line:
                unresolved += 1
            if "row_terms_refused" in line:
                refused += 1
            if "foreign_key_ignored" in line:
                foreign += 1
            if "unanswered(" in line:
                unanswered += len(re.findall(r"'", line)) // 2
            if "duplicate_answer_ignored" in line:
                dup += 1
            if "from prose in note" in line:
                prose += 1
            elif re.search(r"= [\d.]+ from \d+ cited row", line):
                rows_cited += 1

    print("\n" + "=" * 116)
    print("  WHAT THE REQUESTS REPORTED")
    print("=" * 116)
    print(f"    figures written from a CITED ROW      {rows_cited}")
    print(f"    figures written from PROSE            {prose}")
    print(f"    citations that resolved to nothing    {unresolved}")
    print(f"    answers refused by row terms          {refused}")
    print(f"    answers naming a line not asked about {foreign}")
    print(f"    duplicate answers ignored             {dup}")
    print(f"    lines asked and not answered          {unanswered}"
          f"   (an empty `sources` is a real answer, not a failure)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
