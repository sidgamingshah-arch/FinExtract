#!/usr/bin/env python
"""DOES WIDENING BY CONTENT RECOVER NOTES THE HEADER SEARCH MISSED, AND WHAT DOES IT COST?

`line_item_notes.widen_by_content` runs when a line's best note-HEADING score is below
`WIDEN_BELOW` — it stops asking which heading is about the line and asks which note CONTAINS the
rows the line is looking for. This measures both halves of that trade against the only ground truth
available: the authored `note_title_any` regexes, which demonstrably work because they produced
every figure in the focus runs.

    RECALL   how many (line, authored note) pairs are DELIVERED to a request, header-only
             against header-plus-widening.
    COST     notes per line, and how many lines widen at all.
    REACH    notes admitted by widening that the patterns do NOT name — neither proof of a
             mistake nor of a find, and the number a reviewer should look at, because that is
             where a figure nothing else reaches would come from.

    python scripts/measure_widening.py
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


def dumps(cas_only: bool):
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
        nums = [str(n.get("note") or n.get("number") or "") for n in notes]
        is_cas = (HAN.search(" ".join(str(n.get("title") or "") for n in notes))
                  and sum(1 for n in nums if HAN_ENUM.match(n)) > len(nums) * 0.3)
        if cas_only and not is_cas:
            continue
        d["_file"] = p.name
        d["_cas"] = bool(is_cas)
        out.append(d)
    return out


def tables_of(d):
    from app.core.models.line_item import NoteItem, NotesTable

    out = []
    for n in d["notes"]:
        t = NotesTable(note_number=str(n.get("note") or n.get("number") or ""),
                       title=str(n.get("title") or ""))
        for cap in (n.get("rows") or ()):
            label = cap if isinstance(cap, str) else str(cap.get("caption") or "")
            if label:
                t.items.append(NoteItem(raw_label=label))
        out.append(t)
    return out


def truth(item, tables) -> set[str]:
    pats = []
    for raw in (getattr(getattr(item, "note_source", None), "note_title_any", None) or ()):
        try:
            pats.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue
    if not pats:
        return set()
    return {t.note_number for t in tables
            if any(p.search(t.title or "") for p in pats)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-regimes", action="store_true", help="include the HK filings too")
    args = ap.parse_args()

    from app.schemas.line_items import load_line_item_set
    from app.services.line_item_notes import (MIN_SCORE, WIDEN_BELOW, header_pool,
                                              notes_for_line_item, widen_by_content)
    from app.services.line_item_requests import asked_about

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    by_key = {i.key: i for i in cfg.items}
    items = [i for i in cfg.items
             if asked_about(i) and getattr(i, "note_source", None) is not None]

    CAP = 4
    rows = []
    for d in dumps(cas_only=not args.all_regimes):
        tables = tables_of(d)
        pool = header_pool(tables)
        hdr_hit = wide_hit = 0
        pairs = 0
        hdr_notes = wide_notes = 0
        widened_lines = 0
        off_pattern = 0
        for item in items:
            parent = by_key.get(getattr(item, "parent", "") or "")
            hits = notes_for_line_item(item, pool, min_score=MIN_SCORE, cap=CAP, parent=parent)
            header_set = {h.note for h in hits}
            best = max((h.score for h in hits), default=0.0)

            extra: list = []
            if best < WIDEN_BELOW and len(hits) < CAP:
                seen = set(header_set)
                for e in widen_by_content(item, tables, cap=CAP - len(hits)):
                    if e.note not in seen:
                        extra.append(e)
                        seen.add(e.note)
            if extra:
                widened_lines += 1
            wide_set = header_set | {e.note for e in extra}

            want = truth(item, tables)
            if want:
                pairs += len(want)
                hdr_hit += len(want & header_set)
                wide_hit += len(want & wide_set)
            hdr_notes += len(header_set)
            wide_notes += len(wide_set)
            off_pattern += len({e.note for e in extra} - want)

        rows.append({
            "file": d["_file"], "cas": d["_cas"], "pairs": pairs,
            "hdr": hdr_hit, "wide": wide_hit,
            "hdr_notes": hdr_notes, "wide_notes": wide_notes,
            "widened": widened_lines, "off": off_pattern, "lines": len(items),
        })

    print("=" * 114)
    print(f"WIDENING BY CONTENT — {len(rows)} filings, {len(items)} asked-about lines, "
          f"cap={CAP}, WIDEN_BELOW={WIDEN_BELOW}")
    print("=" * 114)
    print(f"  {'filing':<34s} {'authored pairs':>14s} {'header':>8s} {'+widen':>8s} "
          f"{'notes/line':>12s} {'lines widened':>14s} {'off-pattern':>12s}")
    tp = th = tw = thn = twn = tl = to = 0
    for r in rows:
        gain = r["wide"] - r["hdr"]
        print(f"  {r['file'][:34]:<34s} {r['pairs']:>14d} {r['hdr']:>8d} "
              f"{r['wide']:>5d} {('+' + str(gain)) if gain else '  ':>3s} "
              f"{r['hdr_notes']/r['lines']:>5.2f}->{r['wide_notes']/r['lines']:<5.2f} "
              f"{r['widened']:>14d} {r['off']:>12d}")
        tp += r["pairs"]; th += r["hdr"]; tw += r["wide"]
        thn += r["hdr_notes"]; twn += r["wide_notes"]; tl += r["widened"]; to += r["off"]
    n = len(rows) * len(items)
    print(f"\n  TOTAL authored pairs {tp}   delivered header-only {th} "
          f"({100.0*th/max(1,tp):.1f}%)   with widening {tw} ({100.0*tw/max(1,tp):.1f}%)"
          f"   RECOVERED {tw-th}")
    print(f"  notes per line  {thn/max(1,n):.2f} -> {twn/max(1,n):.2f} "
          f"(+{100.0*(twn-thn)/max(1,thn):.0f}%)")
    print(f"  lines that widened at all: {tl} of {n} line-filing pairs")
    print(f"  notes admitted by widening that the patterns do NOT name: {to}")
    print("\n  ^ that last number is the one to review: neither proof of a mistake nor of a find.")
    print("    A pattern names the notes an author ANTICIPATED; widening is for the filing that")
    print("    captioned its note something nobody anticipated, which is the case no regex covers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
