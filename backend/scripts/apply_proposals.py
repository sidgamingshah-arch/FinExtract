#!/usr/bin/env python
"""Apply a checked proposal file to the shipped set — and refuse to apply an unchecked one.

WHAT IT WILL NOT DO. It re-runs the same measurement `check_proposals.py` reports and applies ONLY
values that are strictly wider: every text the current value claims must still be claimed. A
proposal that drops anything is skipped and named, because a drop is a judgement about whether the
dropped rows were wanted and that judgement is not this script's to make.

    python scripts/apply_proposals.py --proposals scripts/_proposal_ALL.json          # dry run
    python scripts/apply_proposals.py --proposals scripts/_proposal_ALL.json --write
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

REGEX_FIELDS = {"note_title_any", "row_caption_any", "row_caption_none", "prose_any"}


def _compile(values: list[str]) -> list[re.Pattern]:
    out = []
    for v in values:
        try:
            out.append(re.compile(v, re.I))
        except re.error:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposals", type=pathlib.Path, required=True)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    items = {i["key"]: i for i in raw["items"]}

    corpus: dict[str, tuple[list[str], list[dict]]] = {}
    for path in sorted(VOCAB.glob("*.json")):
        if path.name == "INDEX.json":
            continue
        notes = json.loads(path.read_text(encoding="utf-8")).get("notes") or []
        corpus[path.stem] = ([(n.get("title") or "").strip() for n in notes if
                              (n.get("title") or "").strip()], notes)

    payload = json.loads(args.proposals.read_text(encoding="utf-8"))
    props = payload.get("kept") or []
    gates = {p["key"]: p["proposed"] for p in props
             if str(p.get("field", "")).endswith("note_title_any") and p.get("proposed")}

    applied, skipped = [], []
    for prop in props:
        key = prop.get("key")
        field = str(prop.get("field", "")).replace("note_source.", "")
        proposed = [p for p in (prop.get("proposed") or []) if isinstance(p, str)]
        if not proposed or key not in items or field not in REGEX_FIELDS:
            if proposed and field not in REGEX_FIELDS and key in items:
                # A SCORED FIELD IS APPLIED WITHOUT A REACH TEST, because reach is not what it
                # means — it is scored by IDF cosine. The level audit (`note_row_mixing.py`) is
                # what judges these, and it ran before this.
                items[key].setdefault("note_source", {})[field] = proposed
                applied.append(f"{key}/{field} (scored field, {len(proposed)} terms)")
            continue

        item = items[key]
        ns = item.get("note_source") or {}
        current = list(ns.get(field) or [])
        new_rx, cur_rx = _compile(proposed), _compile(current)
        gate = _compile(gates.get(key, list(ns.get("note_title_any") or [])))
        is_row = field.startswith("row_") or field == "prose_any"

        lost = 0
        for _filing, (heads, notes) in corpus.items():
            if is_row:
                texts = [r for n in notes
                         if (n.get("title") or "").strip()
                         and any(g.search((n.get("title") or "").strip()) for g in gate)
                         for r in (n.get("rows") or []) if r]
            else:
                texts = heads
            old = {t for t in texts if any(rx.search(t) for rx in cur_rx)}
            new = {t for t in texts if any(rx.search(t) for rx in new_rx)}
            lost += len(old - new)

        if lost:
            skipped.append(f"{key}/{field} — would DROP {lost} text(s); a drop needs a human")
            continue
        item.setdefault("note_source", {})[field] = proposed
        applied.append(f"{key}/{field} ({len(current)} -> {len(proposed)} patterns)")

    print(f"APPLIED {len(applied)}:")
    for a in applied:
        print(f"   {a}")
    if skipped:
        print(f"\nSKIPPED {len(skipped)}:")
        for s in skipped:
            print(f"   {s}")
    for w in payload.get("withdrawn") or []:
        print(f"\nWITHDRAWN BY THE AUTHOR  {w['key']}\n   {w['why']}")

    if not args.write:
        print("\n(dry run — pass --write to apply)")
        return 0

    # THE REAL LOADER BEFORE ANYTHING IS WRITTEN, twice, exactly as the publish endpoint does.
    from app.schemas.line_items import load_line_item_set
    raw["items"] = list(items.values())
    try:
        load_line_item_set(raw, resolve=False)
        load_line_item_set(raw, resolve=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\nREFUSED — the set no longer loads, nothing written:\n   {exc}")
        return 1
    SEED.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {SEED.name}; loads clean unresolved AND resolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
