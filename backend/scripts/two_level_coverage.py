#!/usr/bin/env python
"""VOCABULARY COVERAGE AT BOTH LEVELS, over every PRC/CAS filing in the corpus.

THE TWO LEVELS ARE DIFFERENT QUESTIONS AND NEED DIFFERENT VOCABULARY. Conflating them is the
mistake this script exists to stop anyone repeating:

  LEVEL 1 — WHICH NOTE. Scored/matched against the note HEADING, which names the CONTAINER:
            固定资产 (fixed assets), 应收账款 (trade receivables). Two fields read it —
            `note_source.note_terms` (scored by `line_item_notes.note_probe`) and
            `note_source.note_title_any` (regex).
  LEVEL 2 — WHICH ROW INSIDE IT. Matched against the row CAPTION, which names the CONTENT:
            固定资产折旧 (depreciation OF fixed assets), 本期计提 (charge for the period). Read by
            `note_source.row_terms` / `row_caption_any`, and only inside a note level 1 claimed.

A line whose level-1 vocabulary names the CHARGE will never find the note, because no CAS filing
captions a note "depreciation of fixed assets" — it captions it 固定资产 and puts the charge in a
row. A line whose level-2 vocabulary names the CONTAINER matches the note's own total instead of
the charge. Both failures look identical from the output — an empty line — which is why they are
reported apart here.

WHAT IT REPORTS, per line item, across all CAS filings at once:

  * L1 REACH   — does any heading match the regex, and does the probe rank the right one?
  * L1 IDF     — a heading its terms appear in VERBATIM but that still scores low. This is the
                 failure a reviewer would never guess: 固定资产 occurs in many of a CAS filing's
                 headings (固定资产, 固定资产清理, 在建工程转固定资产…), so its bigrams carry almost
                 no weight and the correct note does not stand out. More vocabulary does not fix
                 it; a MORE SPECIFIC term does.
  * L2 REACH   — inside the notes level 1 claimed, do the row terms match any caption?
  * THE GAP    — a note claimed with no row matched is the expensive case: the request carries the
                 note and nothing can be read from it.

    python scripts/two_level_coverage.py
    python scripts/two_level_coverage.py --keys sub__ppe_depreciation,sub__cfo_depreciation
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOCAB = ROOT.parent / "_vocab"
SEED = ROOT / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
HAN_ENUM = re.compile(r"^[一二三四五六七八九十百]+\s*[、．]")


def cas_filings():
    """Every CAS/PRC dump: Han headings with Han chapter enumerators."""
    out = []
    for p in sorted(VOCAB.glob("*.json")):
        if p.name == "INDEX.json":
            continue
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            continue
        notes = d.get("notes") if isinstance(d, dict) else None
        if not notes:
            continue
        numbers = [str(n.get("note") or n.get("number") or "") for n in notes]
        if not HAN.search(" ".join(str(n.get("title") or "") for n in notes)):
            continue
        if sum(1 for n in numbers if HAN_ENUM.match(n)) <= len(numbers) * 0.3:
            continue
        d["_file"] = p.name
        out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", default="", help="comma-separated line item keys; default all")
    ap.add_argument("--only-gaps", action="store_true")
    args = ap.parse_args()

    from app.core.models.line_item import NoteItem, NotesTable
    from app.services.line_item_notes import (MIN_SCORE, header_pool, note_probe,
                                              notes_for_line_item)
    from app.services.line_item_requests import asked_about
    from app.schemas.line_items import load_line_item_set
    from app.services.note_context import subject_tokens

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    by_key = {i.key: i for i in cfg.items}
    wanted = [k for k in (args.keys.split(",") if args.keys else [])
              if k] or [i.key for i in cfg.items
                        if asked_about(i) and getattr(i, "note_source", None) is not None]

    filings = cas_filings()
    print("=" * 118)
    print(f"TWO-LEVEL COVERAGE over {len(filings)} CAS/PRC filings, "
          f"{len(wanted)} line items, min_score={MIN_SCORE}")
    print("=" * 118)

    # Rebuild each filing's notes as model objects once, so the real selector runs on them.
    built = []
    for d in filings:
        tables = []
        for n in d["notes"]:
            t = NotesTable(note_number=str(n.get("note") or n.get("number") or ""),
                           title=str(n.get("title") or ""),
                           source_text=str(n.get("prose") or ""))
            for cap in (n.get("rows") or ()):
                label = cap if isinstance(cap, str) else str(cap.get("caption") or "")
                if label:
                    t.items.append(NoteItem(raw_label=label))
            tables.append(t)
        built.append((d["_file"], tables, header_pool(tables)))

    rows = []
    for key in wanted:
        item = by_key.get(key)
        if item is None:
            continue
        source = item.note_source
        parent = by_key.get(getattr(item, "parent", "") or "")
        terms = list(getattr(source, "note_terms", None) or [])
        row_terms = list(getattr(source, "row_terms", None) or [])
        pats = []
        for raw in (getattr(source, "note_title_any", None) or ()):
            try:
                pats.append(re.compile(raw, re.IGNORECASE))
            except re.error:
                pass
        probe_tokens = set(subject_tokens(note_probe(item, parent)))
        han_terms = [t for t in terms if HAN.search(t)]
        han_rows = [t for t in row_terms if HAN.search(t)]

        l1_regex = l1_probe = l2_hit = claimed_no_row = idf_starved = 0
        examples = []
        for fname, tables, pool in built:
            titles = {t.note_number: (t.title or "") for t in tables}
            regex_notes = {num for num, title in titles.items()
                           if any(p.search(title) for p in pats)}
            hits = notes_for_line_item(item, pool, parent=parent)
            probe_notes = {h.note for h in hits}
            if regex_notes:
                l1_regex += 1
            if probe_notes:
                l1_probe += 1

            # IDF STARVATION: a heading containing a term verbatim that the probe did NOT return.
            verbatim = {num for num, title in titles.items()
                        if any(t and t in title for t in han_terms)}
            starved = verbatim - probe_notes
            if starved:
                idf_starved += 1
                if len(examples) < 3:
                    num = sorted(starved)[0]
                    examples.append((fname, num, titles.get(num, "")[:30]))

            # LEVEL 2 inside what level 1 claimed.
            claimed = regex_notes | probe_notes
            matched = False
            for t in tables:
                if t.note_number not in claimed:
                    continue
                for r in t.items:
                    lab = (r.raw_label or "")
                    if any(rt and rt in lab for rt in han_rows):
                        matched = True
                        break
                if matched:
                    break
            if matched:
                l2_hit += 1
            elif claimed:
                claimed_no_row += 1

        rows.append({
            "key": key, "n": len(built),
            "han_terms": len(han_terms), "han_rows": len(han_rows),
            "l1_regex": l1_regex, "l1_probe": l1_probe,
            "l2": l2_hit, "claimed_no_row": claimed_no_row, "starved": idf_starved,
            "ex": examples,
        })

    n = len(built)
    rows.sort(key=lambda r: (r["l2"], r["l1_probe"]))
    print(f"\n  {'line item':<46s} {'L1 rx':>6s} {'L1 prb':>7s} {'L2 row':>7s} "
          f"{'claim/norow':>12s} {'idf':>4s} {'zhN':>4s} {'zhR':>4s}")
    print(f"  {'-'*46} {'-'*6} {'-'*7} {'-'*7} {'-'*12} {'-'*4} {'-'*4} {'-'*4}")
    for r in rows:
        if args.only_gaps and r["l2"] == n:
            continue
        print(f"  {r['key'][:46]:<46s} {r['l1_regex']:>4d}/{n:<1d} {r['l1_probe']:>5d}/{n:<1d} "
              f"{r['l2']:>5d}/{n:<1d} {r['claimed_no_row']:>10d}/{n:<1d} "
              f"{r['starved']:>4d} {r['han_terms']:>4d} {r['han_rows']:>4d}")

    print("\n" + "=" * 118)
    print("  SUMMARY — where the vocabulary is thin, by LEVEL")
    print("=" * 118)
    no_zh_note = [r for r in rows if r["han_terms"] == 0]
    no_zh_row = [r for r in rows if r["han_rows"] == 0]
    l1_dead = [r for r in rows if r["l1_regex"] == 0 and r["l1_probe"] == 0]
    l2_dead = [r for r in rows if r["l2"] == 0 and (r["l1_regex"] or r["l1_probe"])]
    starved = [r for r in rows if r["starved"] >= n * 0.5]

    print(f"\n  {len(no_zh_note):>4}  LEVEL 1 has NO Chinese note_terms — cannot score a CAS heading")
    for r in no_zh_note[:10]:
        print(f"          {r['key']}")
    print(f"\n  {len(no_zh_row):>4}  LEVEL 2 has NO Chinese row_terms — cannot match a CAS row caption")
    for r in no_zh_row[:10]:
        print(f"          {r['key']}")
    print(f"\n  {len(l1_dead):>4}  LEVEL 1 finds NOTHING on any CAS filing (no regex, no probe)")
    for r in l1_dead[:12]:
        print(f"          {r['key']}   zhNote={r['han_terms']} zhRow={r['han_rows']}")
    print(f"\n  {len(l2_dead):>4}  LEVEL 1 claims a note on every filing, LEVEL 2 matches NO row"
          f" — the note is paid for and unreadable")
    for r in l2_dead[:12]:
        print(f"          {r['key']}   zhRow={r['han_rows']}")
    print(f"\n  {len(starved):>4}  IDF-STARVED on half the corpus or more: a heading contains the "
          f"term verbatim\n        yet the probe does not return it — needs a MORE SPECIFIC term, "
          f"not more terms")
    for r in starved[:10]:
        print(f"          {r['key']}")
        for fname, num, title in r["ex"]:
            print(f"             {fname[:26]:<26s} note {num:<8s} {title!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
