#!/usr/bin/env python
"""FOR A LINE WHOSE NOTE IS FOUND AND WHOSE ROW IS NOT — what the filings actually print.

`two_level_coverage.py` names 24 line items where LEVEL 1 claims a note on every CAS filing and
LEVEL 2 matches no row inside it. That is the expensive failure: the note is carried in the request
and nothing can be read from it, so the line is empty AND paid for.

These lines are not short of vocabulary — they carry 19 to 32 Chinese row terms each. So the
question is not "what is missing" but "what did the author expect the row to be called, and what
does a CAS filer actually call it". This prints both, side by side, for one line at a time:

  * the notes LEVEL 1 claimed, per filing
  * EVERY row caption inside them
  * which of the line's `row_terms` are Han, and which of them matched anything anywhere
  * the captions that contain a term the line's `row_terms_none` VETOES, since an over-broad veto
    is indistinguishable from missing vocabulary from the outside

    python scripts/row_vocab_gap.py sub__cfo_depreciation
    python scripts/row_vocab_gap.py --family depreciation
"""
from __future__ import annotations

import argparse
import collections
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


def cas_dumps():
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
        if not HAN.search(" ".join(str(n.get("title") or "") for n in notes)):
            continue
        if sum(1 for n in nums if HAN_ENUM.match(n)) <= len(nums) * 0.3:
            continue
        d["_file"] = p.name
        out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*")
    ap.add_argument("--family", default="")
    ap.add_argument("--max-rows", type=int, default=26)
    args = ap.parse_args()

    from app.core.models.line_item import NoteItem, NotesTable
    from app.services.line_item_notes import header_pool, notes_for_line_item
    from app.schemas.line_items import load_line_item_set

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    by_key = {i.key: i for i in cfg.items}
    keys = list(args.keys)
    if args.family:
        keys += [i.key for i in cfg.items if args.family in i.key]
    if not keys:
        raise SystemExit("give a key or --family")

    built = []
    for d in cas_dumps():
        tables = []
        for n in d["notes"]:
            t = NotesTable(note_number=str(n.get("note") or n.get("number") or ""),
                           title=str(n.get("title") or ""))
            for cap in (n.get("rows") or ()):
                label = cap if isinstance(cap, str) else str(cap.get("caption") or "")
                if label:
                    t.items.append(NoteItem(raw_label=label))
            tables.append(t)
        built.append((d["_file"], tables, header_pool(tables)))

    for key in dict.fromkeys(keys):
        item = by_key.get(key)
        if item is None:
            print(f"\n!! {key}: not in the set")
            continue
        src = item.note_source
        parent = by_key.get(getattr(item, "parent", "") or "")
        pats = []
        for raw in (getattr(src, "note_title_any", None) or ()):
            try:
                pats.append(re.compile(raw, re.IGNORECASE))
            except re.error:
                pass
        row_terms = [t for t in (getattr(src, "row_terms", None) or []) if HAN.search(t)]
        vetoes = [t for t in (getattr(src, "row_terms_none", None) or []) if t]

        print("\n" + "=" * 114)
        print(f"{key}")
        print(f"  {item.label}")
        print("=" * 114)
        print(f"  Han row_terms ({len(row_terms)}): {row_terms[:18]}")
        print(f"  row_terms_none ({len(vetoes)}): {vetoes[:12]}")

        term_hits = collections.Counter()
        veto_hits = collections.Counter()
        captions = collections.Counter()
        shown = 0
        for fname, tables, pool in built:
            titles = {t.note_number: (t.title or "") for t in tables}
            claimed = {num for num, ti in titles.items() if any(p.search(ti) for p in pats)}
            claimed |= {h.note for h in notes_for_line_item(item, pool, parent=parent)}
            if not claimed:
                continue
            rows_here = [(t.note_number, titles.get(t.note_number, ""), r.raw_label or "")
                         for t in tables if t.note_number in claimed for r in t.items]
            if not rows_here:
                continue
            for _num, _ti, lab in rows_here:
                captions[lab] += 1
                for rt in row_terms:
                    if rt in lab:
                        term_hits[rt] += 1
                for v in vetoes:
                    if v and v in lab:
                        veto_hits[v] += 1
            if shown < 3:
                shown += 1
                print(f"\n  --- {fname[:34]}  notes claimed: {sorted(claimed)[:6]}")
                for num, ti, lab in rows_here[:args.max_rows]:
                    mark = "MATCH" if any(rt in lab for rt in row_terms) else (
                        "VETO " if any(v and v in lab for v in vetoes) else "     ")
                    print(f"      {mark} {num:<9s} {ti[:16]:<16s} {lab[:60]!r}")
                if len(rows_here) > args.max_rows:
                    print(f"      … {len(rows_here) - args.max_rows} more rows")

        print(f"\n  ROW TERMS THAT MATCHED ANYTHING:  {dict(term_hits) or 'NONE'}")
        print(f"  VETOES THAT FIRED:                {dict(veto_hits) or 'none'}")
        print(f"  most common captions in the claimed notes:")
        for lab, n in captions.most_common(14):
            print(f"      {n:>3d}x  {lab[:74]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
