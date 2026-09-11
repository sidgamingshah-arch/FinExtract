#!/usr/bin/env python
"""EVERY PROPOSED VALUE, RUN AGAINST THE WHOLE CORPUS — deterministically.

WHY THIS IS NOT AN AGENT'S JOB. The adversarial pass asked agents four questions about each
proposal, and only ONE of them needs judgement:

    does it match anything?          computation
    what else does it catch?         computation
    is it still overfitted?          computation
    is this the RIGHT note for this line?   judgement

The first three are regex over 3,365 note tables. An agent doing them writes a script, runs it,
and reports — which is slower, costs a provider call, and can misreport what the script said. The
measured cost of getting it wrong is in this file's history: 22 of 29 proposals in the first pass
were refuted because the claimed fire-list was false. So the arithmetic is done here and the agents
keep only the question that is actually theirs.

WHAT IT REPORTS per proposed value:

    REACH        how many of the N filings it claims a heading in — 1 of 18 is a constant
    CLAIMS       the headings it matches, per filing
    DELTA        what it claims that the CURRENT value does not, and what it LOSES
    COLLISIONS   other line items in the same family whose current patterns claim the same
                 heading — two parts claiming one note is how a figure gets double-counted

    python scripts/check_proposals.py --proposals <PROPOSALS_verified.json> --field note_title_any
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


def _corpus() -> dict[str, list[tuple[str, str]]]:
    """filing -> unique (note number, heading). A note split over pages repeats its heading, and a
    continuation fragment is not a separate note to claim."""
    out: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(VOCAB.glob("*.json")):
        if path.name == "INDEX.json":
            continue
        seen: dict[tuple[str, str], None] = {}
        for n in json.loads(path.read_text(encoding="utf-8")).get("notes") or []:
            key = (str(n.get("note") or ""), (n.get("title") or "").strip())
            if key[1]:
                seen.setdefault(key, None)
        out[path.stem] = list(seen)
    return out


def _rows(filing: str, gate: list[re.Pattern] | None = None) -> list[str]:
    """Row captions in one filing — and ONLY from notes the line's own note gate claims.

    THE SCOPE IS THE WHOLE POINT, and getting it wrong made this script lie. `note_sourced.
    select_rows` picks notes by `note_title_any` FIRST and only then matches `row_caption_any`
    against the rows INSIDE those notes. Testing a row pattern against every row in the filing
    therefore reports over-matches that cannot happen: it made `^\\s*total\\b` on the
    contingent-liability total look like it claims "Total Equity" and "TOTAL ASSETS LESS CURRENT
    LIABILITIES", which sit in the balance sheet and in notes that line never claims. Acting on
    that would have narrowed a correct pattern and cost nine filings of reach.

    `gate=None` still means every row, for a caller that genuinely wants the unscoped population.
    """
    path = VOCAB / f"{filing}.json"
    out: list[str] = []
    for n in json.loads(path.read_text(encoding="utf-8")).get("notes") or []:
        if gate is not None:
            title = (n.get("title") or "").strip()
            if not title or not any(rx.search(title) for rx in gate):
                continue
        out.extend(r for r in (n.get("rows") or []) if r)
    return out


def _compile(patterns: list[str]) -> tuple[list[re.Pattern], list[str]]:
    ok, bad = [], []
    for p in patterns:
        try:
            ok.append(re.compile(p, re.I))
        except re.error as exc:
            bad.append(f"{p!r}: {exc}")
    return ok, bad


def _claims(rxs: list[re.Pattern], texts: list[str]) -> list[str]:
    return [t for t in texts if any(rx.search(t) for rx in rxs)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposals", type=pathlib.Path, required=True)
    ap.add_argument("--field", default="", help="only this field, e.g. note_source.note_title_any")
    ap.add_argument("--key", default="", help="only this part key")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    items = {i["key"]: i for i in raw["items"]}
    corpus = _corpus()
    if not corpus:
        print(f"no corpus in {VOCAB} — run scripts/vocab_corpus.py first")
        return 2

    payload = json.loads(args.proposals.read_text(encoding="utf-8"))
    props = [*(payload.get("kept") or [])]
    for c in payload.get("corrected") or []:
        # A CORRECTED PROPOSAL IS THE VERIFIER'S VALUE, not the analyst's — that is the whole point
        # of the correction, and checking the refuted original would measure the wrong list.
        props.append({**c, "proposed": c.get("proposed") or []})

    lines: list[str] = []

    def say(t: str = "") -> None:
        lines.append(t)
        print(t)

    say(f"# Proposals checked against {len(corpus)} filings")
    say()
    n_row_field = 0
    for prop in props:
        key, field = prop.get("key", ""), prop.get("field", "")
        if args.field and field != args.field and not field.endswith(args.field):
            continue
        if args.key and key != args.key:
            continue
        proposed = [p for p in (prop.get("proposed") or []) if isinstance(p, str)]
        if not proposed:
            continue
        short = field.replace("note_source.", "")
        item = items.get(key)
        if item is None:
            say(f"## {key} / {short}   !! not a line item in the shipped set")
            continue
        current = list(((item.get("note_source") or {}).get(short)) or []) \
            if short in ("note_title_any", "row_caption_any", "row_caption_none", "prose_any",
                         "note_terms", "row_terms", "row_terms_none") else list(item.get(short) or [])

        say(f"## {key} / {short}")
        rxs, bad = _compile(proposed)
        if bad:
            say(f"   !! DOES NOT COMPILE: {bad}")
            continue
        cur_rxs, _cur_bad = _compile(current)

        # REGEX FIELDS ONLY GET A VERDICT. `note_terms`, `row_terms`, `row_terms_none` and the
        # prose fields (`definition`, `include_criteria`, `exclude_criteria`) are TERMS, scored by
        # IDF-weighted cosine in `note_context.ContextPool`, never matched. Compiling them as
        # patterns and counting hits measures the wrong thing entirely — a definition scored 0/18
        # here simply means its sentences are not regexes, which is true of every definition. So
        # they are reported and NOT judged, and the judgement stays with an analyst.
        REGEX_FIELDS = {"note_title_any", "row_caption_any", "row_caption_none", "prose_any"}
        if short not in REGEX_FIELDS:
            say(f"   SCORED FIELD, NOT MATCHED — {len(current)} current -> {len(proposed)} "
                f"proposed terms. Not judged here: these are scored by IDF cosine, so a regex hit "
                f"count says nothing about them.")
            say()
            continue
        is_row = short.startswith("row_") or short == "prose_any"
        if is_row:
            n_row_field += 1
            say(f"   (row-level field — checked against row captions, {len(current)} current "
                f"-> {len(proposed)} proposed)")
        # THE LINE'S OWN NOTE GATE, for scoping a row-level check to the notes it actually reaches.
        #
        # THE PROPOSED GATE WHERE THE SAME FILE PROPOSES ONE, and this is a sequencing fix rather
        # than a nicety. A row hint written for a note only the WIDENED gate claims reads 0/18 when
        # scoped by the current gate, which looks like a dead pattern and is really a pattern whose
        # note is not admitted yet. Scoping by the gate this proposal set would establish is the
        # only way to judge the two changes as the pair they have to land as.
        gate_source = next((p.get("proposed") or [] for p in props
                            if p.get("key") == key
                            and str(p.get("field", "")).endswith("note_title_any")), None)
        gate, _bad_gate = _compile(
            gate_source if gate_source is not None
            else list(((item.get("note_source") or {}).get("note_title_any")) or []))
        if gate_source is not None and is_row:
            say("   (rows scoped by the PROPOSED note gate — the two land together)")
        reach_new = reach_old = 0
        gained_total = lost_total = 0
        for filing in corpus:
            texts = _rows(filing, gate or None) if is_row else [t for _n, t in corpus[filing]]
            new = set(_claims(rxs, texts))
            old = set(_claims(cur_rxs, texts)) if cur_rxs else set()
            reach_new += 1 if new else 0
            reach_old += 1 if old else 0
            gained, lost = new - old, old - new
            gained_total += len(gained)
            lost_total += len(lost)
            if gained or lost:
                say(f"   {filing[:38]:40s} +{len(gained):3d} -{len(lost):3d}")
                for g in sorted(gained)[:3]:
                    say(f"        GAINED  {g[:74]}")
                for l in sorted(lost)[:3]:
                    say(f"        LOST    {l[:74]}")
        say(f"   REACH  current {reach_old}/{len(corpus)}  ->  proposed {reach_new}/{len(corpus)}"
            f"   (+{gained_total} texts, -{lost_total})")
        if reach_new <= 1:
            say("   VERDICT  still a constant — reaches at most one filing")
        elif lost_total:
            say(f"   VERDICT  widens reach but DROPS {lost_total} text(s) the current value claims")
        else:
            say("   VERDICT  strictly wider, nothing lost")

        # COLLISIONS — another part of the same parent claiming a heading this one now claims.
        if not is_row:
            parent = item.get("parent") or ""
            sibs = [i for i in raw["items"]
                    if i.get("parent") == parent and i["key"] != key]
            hit: dict[str, list[str]] = {}
            for filing in corpus:
                mine = set(_claims(rxs, [t for _n, t in corpus[filing]]))
                for sib in sibs:
                    s_rxs, _ = _compile(list(((sib.get("note_source") or {})
                                              .get("note_title_any")) or []))
                    if not s_rxs:
                        continue
                    shared = mine & set(_claims(s_rxs, [t for _n, t in corpus[filing]]))
                    if shared:
                        hit.setdefault(sib["key"], []).extend(sorted(shared)[:1])
            if hit:
                say(f"   COLLIDES with {len(hit)} sibling part(s) on a shared heading:")
                for sk, examples in list(hit.items())[:5]:
                    say(f"        {sk[:52]:54s} e.g. {examples[0][:44]}")
        say()

    say(f"({n_row_field} row-level field(s) among them)")
    if args.out:
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
