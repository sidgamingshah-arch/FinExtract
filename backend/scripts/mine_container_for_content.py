#!/usr/bin/env python
"""GO FROM THE ROW TO THE NOTE: which headings actually contain the content a line wants?

THE MISTAKE THIS EXISTS TO CATCH, and it is a LEVEL 1 mistake that looks like a level 2 one.
`sub__cfo_depreciation` claims note 五、5 on every CAS filing — heading 现金流 (cash flow) — and
matches no row in it, because that note is the cash-flow STATEMENT SUMMARY: 经营活动现金流入小计,
筹资活动产生的现金流量净额. The depreciation add-back is not in it. It is in
现金流量表补充资料 — the supplementary note that reconciles net profit to operating cash flow, where
CSRC prescribes the row 固定资产折旧、油气资产折耗、生产性生物资产折旧.

So the line's LEVEL 2 vocabulary was right all along and its LEVEL 1 vocabulary pointed at the
wrong container. Reported as "level 1 claims a note, level 2 matches nothing", that is
indistinguishable from thin row terms — and fixing the row terms would have achieved nothing.

WHAT THIS DOES. It inverts the search. Instead of asking "which notes does this line's heading
vocabulary find", it asks "in this corpus, WHICH NOTE HEADINGS CONTAIN A ROW THIS LINE'S OWN
`row_terms` MATCH?" Those headings are, by construction, the containers that hold the content the
line is defined by — so they are the level-1 vocabulary the line should carry, discovered from the
filings rather than guessed.

The ranking is by how many DISTINCT FILINGS a heading appears in with a matching row, because a
heading that holds the row in eight of nine filings is house style and one that holds it in one is
that filer's quirk.

    python scripts/mine_container_for_content.py --family depreciation
    python scripts/mine_container_for_content.py sub__cfo_depreciation --show-rows
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
# The enumerator delimiter that extraction leaves on a CAS heading, stripped for comparison only.
LEAD = re.compile(r"^[\s、．）)]+")


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
    ap.add_argument("--show-rows", action="store_true")
    ap.add_argument("--min-filings", type=int, default=2)
    args = ap.parse_args()

    from app.schemas.line_items import load_line_item_set
    from app.services.line_item_requests import asked_about

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    by_key = {i.key: i for i in cfg.items}
    keys = list(args.keys)
    if args.family:
        keys += [i.key for i in cfg.items if args.family in i.key]
    if not keys:
        keys = [i.key for i in cfg.items
                if asked_about(i) and getattr(i, "note_source", None) is not None]

    dumps = cas_dumps()
    print("=" * 116)
    print(f"CONTAINER-FOR-CONTENT over {len(dumps)} CAS/PRC filings")
    print("=" * 116)

    for key in dict.fromkeys(keys):
        item = by_key.get(key)
        if item is None or getattr(item, "note_source", None) is None:
            continue
        src = item.note_source
        row_terms = [t for t in (getattr(src, "row_terms", None) or []) if HAN.search(t)]
        vetoes = [t for t in (getattr(src, "row_terms_none", None) or []) if t and HAN.search(t)]
        note_terms = [t for t in (getattr(src, "note_terms", None) or []) if HAN.search(t)]
        if not row_terms:
            continue

        # heading -> {filing: [matching row captions]}
        found: dict[str, dict[str, list]] = collections.defaultdict(lambda: collections.defaultdict(list))
        for d in dumps:
            for n in d["notes"]:
                title = LEAD.sub("", str(n.get("title") or "")).strip()
                if not title:
                    continue
                for cap in (n.get("rows") or ()):
                    lab = cap if isinstance(cap, str) else str(cap.get("caption") or "")
                    if not lab:
                        continue
                    if any(v in lab for v in vetoes):
                        continue
                    if any(rt in lab for rt in row_terms):
                        found[title][d["_file"]].append(lab)

        ranked = sorted(found.items(), key=lambda kv: (-len(kv[1]), -sum(len(v) for v in kv[1].values())))
        ranked = [(t, f) for t, f in ranked if len(f) >= args.min_filings]
        if not ranked:
            continue

        already = lambda t: any(nt and (nt in t or t in nt) for nt in note_terms)   # noqa: E731
        missing = [(t, f) for t, f in ranked if not already(t)]

        print(f"\n{'=' * 116}")
        print(f"{key}   —   {item.label}")
        print(f"  note_terms now: {note_terms}")
        print(f"{'=' * 116}")
        head = "headings in the corpus that CONTAIN this line's rows"
        print(f"  {head:<60s} {'filings':>8s} {'rows':>5s}  have?")
        for title, per in ranked[:12]:
            nrows = sum(len(v) for v in per.values())
            flag = "yes" if already(title) else "NO "
            print(f"    {title[:60]:<60s} {len(per):>6d}/{len(dumps):<1d} {nrows:>5d}  {flag}")
        if missing:
            print(f"\n  >>> {len(missing)} heading(s) hold this line's rows and are NOT in note_terms:")
            for title, per in missing[:6]:
                print(f"      {title!r}   ({len(per)} filings)")
                if args.show_rows:
                    sample = [r for v in per.values() for r in v][:4]
                    for r in sample:
                        print(f"          row {r[:70]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
