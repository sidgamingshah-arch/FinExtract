#!/usr/bin/env python
"""THE LIVE LINE-ITEM REQUEST PATH, over every filing available — deterministic, then with a model.

WHAT IS CONTROLLED. The same four focus PARTS on every filing, `grouping = none`, one request per
line. Four rather than all 85 because 85 keys over 20 filings is roughly 1,500 requests and four
million tokens, which no free tier serves — and because four parts chosen to feed four different
wholes make a change visible in a PUBLISHED FIGURE rather than only in a log:

    sub__fixed_asset_depreciation     -> is_pl__deprec_and_impairment_oper_exp
    sub__prepaid_lease_depreciation   -> is_pl__deprec_and_impairment_oper_exp
    sub__face_principal_revenue       -> is_pl__sales_revenues
    sub__cl_contingency_note_exposures            -> notes__contingent_liabilities

EACH FILING IS EXTRACTED TWICE, with `llm_mapping` the only difference, and the eight focus wholes
plus the four parts are read through `_serialize_rows` then `concept_value` — the same boundary the
grid, the accounting checks and the xlsx export read through, so what is reported is what a reader
would see.

PACED ON PURPOSE. A token-per-minute tier answers a 429 with "try again in 43s"; the adapter
honours that but only three times, so a run that leans on retries degrades to the deterministic
path and reports itself as such. Sleeping between filings keeps the window clear instead.

RESULTS ARE WRITTEN AFTER EVERY FILING, so a run that is interrupted is still a result.

    python scripts/live_run_corpus.py --out ../_run8/LIVE_CORPUS.json
    python scripts/live_run_corpus.py --out … --pace 75 --limit 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = ROOT / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"

PARTS = [
    "sub__fixed_asset_depreciation",
    "sub__prepaid_lease_depreciation",
    "sub__face_principal_revenue",
    "sub__cl_contingency_note_exposures",
]
WHOLES = [
    "is_pl__sales_revenues", "is_pl__deprec_and_impairment_oper_exp",
    "is_pl__deprec_and_impairment_cos", "bs_ca__secur_and_other_fincl_assets_cp",
    "bs_nca__secur_and_other_fincl_assets_ltp", "bs_nca__due_from_related_parties_ltp",
    "bs_ca__other_receivables_cp", "notes__contingent_liabilities",
]


def sources() -> list[tuple[str, pathlib.Path]]:
    """Every filing that can be read, deduplicated by CONTENT.

    The store holds the same bytes under two filenames; running both would double the cost and
    report one filing twice.
    """
    found: list[tuple[str, pathlib.Path]] = []
    for folder in ("_filings", "_run8"):
        d = ROOT.parent / folder
        if d.exists():
            found += [(p.stem, p) for p in sorted(d.glob("*.pdf"))]
    db = ROOT / "finex.db"
    store = ROOT / "_object_store"
    if db.exists() and store.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for filename, key in con.execute("select filename, object_key from documents"):
            blob = store / str(key)
            if blob.exists():
                found.append((pathlib.Path(str(filename)).stem, blob))

    out, seen = [], set()
    for name, path in found:
        try:
            digest = hashlib.sha1(path.read_bytes()).hexdigest()
        except Exception:
            continue
        if digest in seen:
            continue
        seen.add(digest)
        out.append((name, path))
    return out


def one(path: pathlib.Path, name: str, live: bool) -> dict:
    from app.api.routes.extractions import _serialize_rows
    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.periods import concept_value
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    s = get_settings()
    s.extraction.llm_mapping = bool(live)
    s.extraction.llm_focus_only = True
    s.extraction.llm_focus_keys = list(PARTS)
    s.extraction.llm_request_grouping = "none"

    t0 = time.time()
    doc, ctx = run_extraction(path.read_bytes(), filename=f"{name}.pdf",
                              ontology=build_working_view(cfg), template=None, line_items=cfg)
    rows = _serialize_rows(doc)

    def value(key):
        mine = [r for r in rows if r.get("canonical_key") == key]
        got = concept_value(mine, "consolidated", "current") if mine else None
        return str(got) if got is not None else None

    return {
        "values": {k: value(k) for k in WHOLES + PARTS},
        "logs": [l for l in (ctx.logs or []) if "line_item_llm" in l],
        "seconds": round(time.time() - t0, 1),
        "calls": getattr(ctx, "llm_calls", 0),
        "in_tokens": getattr(ctx, "llm_input_tokens", 0),
        "out_tokens": getattr(ctx, "llm_output_tokens", 0),
        "strategy": getattr(ctx, "mapping_strategy", ""),
        "reason": getattr(ctx, "mapping_strategy_reason", ""),
        "notes": len(doc.notes or ()),
        "rows": len(doc.line_items or ()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--pace", type=float, default=70.0,
                    help="seconds to wait after each live filing, for a per-minute token tier")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true",
                    help="keep the filings already in --out and run only the rest")
    args = ap.parse_args()

    todo = sources()
    if args.limit:
        todo = todo[:args.limit]

    # RESUMING IS THE NORMAL CASE, not the exception. A paced run over eighteen filings outlives
    # the session that started it, and the first version rewrote `--out` from scratch — so a
    # restart discarded every filing already paid for. Keyed on a LIVE result rather than on the
    # filing being present: a row with only a baseline never reached a provider.
    results: dict = {"parts": PARTS, "wholes": WHOLES, "filings": {}}
    if args.resume and args.out.exists():
        prior = json.loads(args.out.read_text(encoding="utf-8"))
        results["filings"] = {k: v for k, v in (prior.get("filings") or {}).items()
                              if v.get("live")}
        done = set(results["filings"])
        todo = [(n, p) for n, p in todo if n not in done]
        print(f"resuming: {len(done)} filing(s) already have a live result, {len(todo)} to go",
              flush=True)

    print(f"{len(todo)} filings, 4 focus parts each, paced {args.pace}s", flush=True)
    for n, (name, path) in enumerate(todo, 1):
        print(f"\n[{n}/{len(todo)}] {name}  ({path.stat().st_size:,} bytes)", flush=True)
        entry: dict = {"path": str(path)}
        try:
            entry["baseline"] = one(path, name, live=False)
            print(f"      baseline {entry['baseline']['seconds']}s  "
                  f"notes={entry['baseline']['notes']}", flush=True)
        except Exception as exc:
            entry["baseline_error"] = f"{type(exc).__name__}: {exc}"
            print(f"      BASELINE FAILED {entry['baseline_error'][:120]}", flush=True)
            traceback.print_exc(limit=2)
        try:
            entry["live"] = one(path, name, live=True)
            L = entry["live"]
            print(f"      live     {L['seconds']}s  calls={L['calls']} "
                  f"in={L['in_tokens']:,} out={L['out_tokens']:,} strategy={L['strategy']}",
                  flush=True)
            for line in L["logs"]:
                if "=" in line and ("consolidated" in line or "calls=" in line
                                    or "citation NOT" in line or "refused" in line):
                    print(f"         {line}", flush=True)
        except Exception as exc:
            entry["live_error"] = f"{type(exc).__name__}: {exc}"
            print(f"      LIVE FAILED {entry['live_error'][:160]}", flush=True)
        results["filings"][name] = entry
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        if n < len(todo) and args.pace:
            time.sleep(args.pace)

    print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
