#!/usr/bin/env python
"""WHICH NOTE HEADERS THE CONFIG CLAIMS, AND WHICH RELEVANT ONES IT MISSES.

HOW THIS EXERCISE IS JUDGED, and it is deliberately NOT by the eight published figures. A figure
appearing is a weak signal: it can appear because one pattern happens to fit one filing's exact
wording, which is the overfitting this is meant to find rather than reward. What matters is whether
a line item's declared vocabulary reaches the note headers a competent analyst would say it lives
in — across every filing, including the five nobody tuned it against.

So this reports, per line item:

  CLAIMED    the note headers its `note_title_any` regexes actually match, per filing
  REACH      how many of the 7 filings it claims anything in at all — 1 of 7 is an overfitted
             pattern wearing a config field, however well it works on that one
  CANDIDATES headers it does NOT claim but which share vocabulary with the line's own terms, so a
             reader can see what is being missed and decide whether it SHOULD be claimed

CANDIDATES ARE A PROMPT, NOT A VERDICT. Term overlap is the same IDF-weighted cosine the semantic
selector uses, and it is exactly as fallible here: a header naming the container ("PROPERTY, PLANT
AND EQUIPMENT") shares no words with a line naming the content ("depreciation of fixed assets"),
which is the measured container-for-content problem. A low-scoring candidate may still be the right
note, and a high-scoring one may be irrelevant. The list is here so a human reads headers rather
than guessing at them.

    python scripts/note_header_coverage.py --family is_pl__deprec_and_impairment_oper_exp
    python scripts/note_header_coverage.py --all-focus --out ../_vocab/COVERAGE.md
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")
VOCAB = pathlib.Path(__file__).resolve().parent.parent.parent / "_vocab"

FOCUS = ["is_pl__sales_revenues",
         "is_pl__deprec_and_impairment_oper_exp",
         "is_pl__deprec_and_impairment_cos",
         "bs_ca__secur_and_other_fincl_assets_cp",
         "bs_nca__secur_and_other_fincl_assets_ltp",
         "bs_nca__due_from_related_parties_ltp",
         "bs_ca__other_receivables_cp",
         "notes__contingent_liabilities"]


def _corpus() -> dict[str, list[dict]]:
    """filing -> its note tables, from the corpus `vocab_corpus.py` wrote."""
    out: dict[str, list[dict]] = {}
    for path in sorted(VOCAB.glob("*.json")):
        if path.name == "INDEX.json":
            continue
        out[path.stem] = json.loads(path.read_text(encoding="utf-8")).get("notes") or []
    return out


def _headers(notes: list[dict]) -> list[tuple[str, str]]:
    """(note number, heading) deduplicated — a note split over pages repeats its heading, and a
    continuation fragment is not a separate note to claim."""
    seen: dict[tuple[str, str], None] = {}
    for n in notes:
        key = (str(n.get("note") or ""), (n.get("title") or "").strip())
        if key[1]:
            seen.setdefault(key, None)
    return list(seen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="")
    ap.add_argument("--all-focus", action="store_true")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--candidates", type=int, default=6)
    args = ap.parse_args()

    from app.services.line_item_notes import header_pool, note_probe
    from app.services.note_context import subject_tokens

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    items = {i["key"]: i for i in raw["items"]}
    parents = FOCUS if args.all_focus else [args.family]
    corpus = _corpus()
    if not corpus:
        print(f"no corpus in {VOCAB} — run scripts/vocab_corpus.py first")
        return 2

    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)
        print(text)

    say(f"# Note-header coverage — {len(corpus)} filings")
    say()
    say("REACH is how many filings a line claims anything in. 1 of 7 is an overfitted pattern.")
    say("CANDIDATES are headers the line does NOT claim but shares vocabulary with — a prompt for a")
    say("reader, not a verdict: a container-naming header shares no words with a content-naming")
    say("line, which is the measured failure this cannot see.")

    for parent_key in parents:
        kids = [i for i in raw["items"] if i.get("parent") == parent_key]
        say()
        say(f"## {parent_key} — {len(kids)} parts")
        for kid in kids:
            ns = kid.get("note_source") or {}
            pats = [p for p in (ns.get("note_title_any") or [])]
            compiled = []
            for p in pats:
                try:
                    compiled.append((p, re.compile(p, re.I)))
                except re.error as exc:
                    say(f"  !! {kid['key']}: {p!r} does not compile: {exc}")
            claimed: dict[str, list[str]] = {}
            for filing, notes in corpus.items():
                hits = [f"{num or '?'} {title}" for num, title in _headers(notes)
                        if any(rx.search(title) for _p, rx in compiled)]
                if hits:
                    claimed[filing] = hits
            say()
            say(f"### {kid['key']}")
            say(f"  patterns  {len(pats)}: {pats[:3]}{' …' if len(pats) > 3 else ''}")
            say(f"  note_terms {list(ns.get('note_terms') or [])[:8]}")
            say(f"  REACH     {len(claimed)} of {len(corpus)} filings")
            for filing, hits in claimed.items():
                say(f"    {filing[:34]:36s} {len(hits):3d}  e.g. {hits[0][:70]}")
            if not claimed:
                say("    claims NOTHING in any filing")

            # CANDIDATES — scored by the same probe the semantic selector uses, over the headers
            # this line does NOT already claim.
            probe = set(subject_tokens(note_probe(_As(kid))))
            if probe:
                scored: list[tuple[float, str, str]] = []
                for filing, notes in corpus.items():
                    for num, title in _headers(notes):
                        if any(rx.search(title) for _p, rx in compiled):
                            continue
                        toks = set(subject_tokens(title))
                        if not toks:
                            continue
                        overlap = len(probe & toks) / (len(probe | toks) or 1)
                        if overlap > 0:
                            scored.append((overlap, filing, f"{num or '?'} {title}"))
                scored.sort(reverse=True)
                if scored:
                    say(f"  CANDIDATES not claimed (top {args.candidates} by term overlap):")
                    for score, filing, header in scored[:args.candidates]:
                        say(f"    {score:5.3f}  {filing[:24]:26s} {header[:64]}")

    if args.out:
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


class _As:
    """A dict as the attribute-ish object `note_probe` expects, so the corpus can be scored without
    loading the whole pydantic set."""

    def __init__(self, d: dict):
        self._d = d

    def __getattr__(self, name):
        v = self._d.get(name)
        if name == "note_source" and isinstance(v, dict):
            return _As(v)
        return v


if __name__ == "__main__":
    raise SystemExit(main())
