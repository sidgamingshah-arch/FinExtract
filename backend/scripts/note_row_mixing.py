#!/usr/bin/env python
"""IS THIS HINT ABOUT THE NOTE OR ABOUT THE ROW? Measured, per value, over the whole corpus.

THE RULE BEING ENFORCED. A note's heading names the CONTAINER and a row's caption names the
CONTENT, and the two need separate vocabularies:

    note_title_any / note_terms        WHICH NOTE the figure lives in      "administrative
                                                                            expenses", 管理费用
    row_caption_any / row_terms /      WHICH ROW inside it counts          "depreciation of fixed
    row_caption_none / row_terms_none                                       assets", 固定资产折旧

MIXING THEM IS NOT A STYLE POINT — it is the measured failure this codebase already has on record:
a single blended probe scored 0.000 against the very heading its line belongs to, because every one
of its tokens was a content word. A value written for the wrong level does not degrade gracefully;
it degrades to silence.

HOW MIXING IS DETECTED. Every value is run against BOTH populations — the corpus's note headings
and its row captions — and the ratio is the evidence:

    a `note_*` value should hit HEADINGS and hit few rows
    a `row_*`  value should hit ROWS and hit few headings

A value hitting overwhelmingly the wrong population is MIXED. A value hitting both heavily is
AMBIGUOUS: it may be correct (some words genuinely appear at both levels) but it cannot
discriminate, which is what the two levels exist to do.

    python scripts/note_row_mixing.py --shipped          # audit the 77 parts as they stand
    python scripts/note_row_mixing.py --proposals <f>     # audit a proposal file
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

FOCUS = ["is_pl__sales_revenues", "is_pl__deprec_and_impairment_oper_exp",
         "is_pl__deprec_and_impairment_cos", "bs_ca__secur_and_other_fincl_assets_cp",
         "bs_nca__secur_and_other_fincl_assets_ltp", "bs_nca__due_from_related_parties_ltp",
         "bs_ca__other_receivables_cp", "notes__contingent_liabilities"]

NOTE_FIELDS = ("note_title_any", "note_terms")
ROW_FIELDS = ("row_caption_any", "row_caption_none", "row_terms", "row_terms_none")
# `prose_any` is a THIRD level — sentences, not captions — so it is reported and not classified.
PROSE_FIELDS = ("prose_any",)


def _corpus() -> tuple[list[str], list[str]]:
    """(every distinct note heading, every row caption) across the corpus."""
    heads: dict[str, None] = {}
    rows: dict[str, None] = {}
    for path in sorted(VOCAB.glob("*.json")):
        if path.name == "INDEX.json":
            continue
        for n in json.loads(path.read_text(encoding="utf-8")).get("notes") or []:
            title = (n.get("title") or "").strip()
            if title:
                heads.setdefault(title, None)
            for r in n.get("rows") or []:
                if (r or "").strip():
                    rows.setdefault(r.strip(), None)
    return list(heads), list(rows)


def _hits(values: list[str], texts: list[str], *, as_regex: bool) -> int:
    """How many texts a value list reaches.

    REGEX FIELDS ARE SEARCHED; TERM FIELDS ARE SUBSTRING-TESTED, because a term is not a pattern —
    it is scored by IDF cosine after tokenisation, and a substring test is the closest honest proxy
    for "does this vocabulary appear at this level at all".
    """
    if as_regex:
        rxs = []
        for v in values:
            try:
                rxs.append(re.compile(v, re.I))
            except re.error:
                continue
        if not rxs:
            return 0
        return sum(1 for t in texts if any(rx.search(t) for rx in rxs))
    low = [v.strip().lower() for v in values if v.strip()]
    if not low:
        return 0
    return sum(1 for t in texts if any(v in t.lower() for v in low))


def _verdict(field: str, on_heads: int, on_rows: int) -> str:
    """MIXED / AMBIGUOUS / clean — and the thresholds are deliberately blunt.

    A 3:1 ratio the wrong way is the line. Finer thresholds would imply a precision this
    measurement does not have: a value can legitimately touch both levels, and the point is to
    surface the ones written at the wrong level, not to score every value.
    """
    if on_heads == 0 and on_rows == 0:
        return "DEAD — reaches neither headings nor rows"
    wants_heads = field in NOTE_FIELDS
    right, wrong = (on_heads, on_rows) if wants_heads else (on_rows, on_heads)
    if right == 0:
        return f"MIXED — a {field} value reaching only the other level ({wrong} there, 0 here)"
    if wrong > right * 3:
        return f"MIXED — {wrong} at the wrong level against {right} at the right one"
    if wrong > right:
        return f"AMBIGUOUS — {wrong} wrong-level against {right} right-level; cannot discriminate"
    return "clean"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shipped", action="store_true")
    ap.add_argument("--proposals", type=pathlib.Path, default=None)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    heads, rows = _corpus()
    if not heads:
        print(f"no corpus in {VOCAB} — run scripts/vocab_corpus.py first")
        return 2

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    items = {i["key"]: i for i in raw["items"]}
    lines: list[str] = []

    def say(t: str = "") -> None:
        lines.append(t)
        print(t)

    say(f"# note/row level audit — {len(heads)} distinct headings, {len(rows)} row captions")
    say()

    bad: list[str] = []
    if args.shipped:
        say("## The shipped values")
        say()
        for parent in FOCUS:
            for item in [i for i in raw["items"] if i.get("parent") == parent]:
                ns = item.get("note_source") or {}
                for field in NOTE_FIELDS + ROW_FIELDS:
                    values = list(ns.get(field) or [])
                    if not values:
                        continue
                    as_rx = field.endswith("_any") or field.endswith("_none") \
                        and "caption" in field
                    as_rx = "caption" in field or field == "note_title_any"
                    h = _hits(values, heads, as_regex=as_rx)
                    r = _hits(values, rows, as_regex=as_rx)
                    v = _verdict(field, h, r)
                    if v != "clean":
                        bad.append(f"{item['key']}/{field}")
                        say(f"{item['key']:54s} {field:17s} heads {h:5d}  rows {r:5d}  {v}")
        say()
        say(f"{len(bad)} value(s) are MIXED, AMBIGUOUS or DEAD across the 77 parts")

    if args.proposals:
        say()
        say("## The proposals")
        say()
        payload = json.loads(args.proposals.read_text(encoding="utf-8"))
        props = list(payload.get("kept") or [])
        props += [{**c, "proposed": c.get("proposed") or []}
                  for c in (payload.get("corrected") or [])]
        for prop in props:
            field = str(prop.get("field", "")).replace("note_source.", "")
            # DEFINITIONS AND CRITERIA ARE OUT OF SCOPE by instruction — they are prose for a human
            # to write, and nothing here can judge them.
            if field in ("definition", "include_criteria", "exclude_criteria"):
                say(f"{prop.get('key','')[:54]:54s} {field:17s} SKIPPED — prose, out of scope")
                continue
            if field in PROSE_FIELDS:
                say(f"{prop.get('key','')[:54]:54s} {field:17s} sentence-level, not classified")
                continue
            values = [p for p in (prop.get("proposed") or []) if isinstance(p, str)]
            if not values or field not in NOTE_FIELDS + ROW_FIELDS:
                continue
            as_rx = "caption" in field or field == "note_title_any"
            h = _hits(values, heads, as_regex=as_rx)
            r = _hits(values, rows, as_regex=as_rx)
            say(f"{prop.get('key','')[:54]:54s} {field:17s} heads {h:5d}  rows {r:5d}  "
                f"{_verdict(field, h, r)}")

    if args.out:
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
