#!/usr/bin/env python
"""WHAT THE `identified_notes` BLOCK COSTS, and how much of it each call could not use.

THE FINDING THIS QUANTIFIES. `trace_line_item.py` showed `identified_notes` at 95,188 characters,
byte-identical in every one of the 21 calls a filing makes — 48% of each request. The block is
document-level evidence placed BESIDE `source_items` rather than inside each one, which was the
right call at the time: repeating it per row would be roughly a megabyte on a 43-row request. But
per CALL it is still repeated, and the question nobody had measured is how much of it any given
call could possibly have used.

THE TEST. A call's answer must be expressed in ITS candidate list. So a note is useful to that call
only if some candidate in it declares that note — `note_source.note_title_any` matched against the
note's own heading, which is exactly how `note_context.identified_notes` claimed the note in the
first place. Every other note in the block is text the call is paying for and cannot act on.

    python scripts/notes_block_waste.py ../_run8/laisun.pdf

Chars/4 for tokens, which is what the batching math itself assumes.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=pathlib.Path)
    args = ap.parse_args()

    from app.config import get_settings
    from app.schemas.line_items import load_line_item_set
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    view = build_working_view(cfg)
    settings = get_settings()

    class Capture:
        id = "notes-waste-probe"

        def __init__(self):
            self.calls: list[dict] = []

        def complete_structured(self, *, system, messages, response_schema, **_):
            self.calls.append({"system": system,
                               "payload": json.loads(messages[-1]["content"])})
            return response_schema.model_validate({"mappings": []}), {}

    cap = Capture()
    settings.extraction.llm_mapping = True
    settings.llm.provider = cap.id
    run_extraction(args.pdf.read_bytes(), filename=args.pdf.name, ontology=view,
                   template=None, line_items=cfg,
                   context_cb=lambda c: c.registry.register("llm", cap.id, lambda: cap))

    if not cap.calls:
        print("no call was built")
        return 1

    # Which notes each LINE ITEM declares, by the same test that claimed them: the note's heading
    # against `note_title_any`. Compiled once.
    declares: dict[str, list] = {}
    for item in cfg.items:
        ns = getattr(item, "note_source", None)
        pats = list(getattr(ns, "note_title_any", []) or []) if ns else []
        if pats:
            declares[item.key] = [re.compile(p, re.I) for p in pats]

    def _title(note) -> str:
        return str(note.get("title") or note.get("note_title") or "")

    def _claims(key: str, note) -> bool:
        return any(p.search(_title(note)) for p in declares.get(key, ()))

    blocks = [json.dumps(c["payload"].get("identified_notes") or [], ensure_ascii=False)
              for c in cap.calls]
    identical = len(set(blocks)) == 1
    block_chars = len(blocks[0])

    print(f"\n{args.pdf.name}")
    print(f"  calls                       {len(cap.calls)}")
    print(f"  identified_notes block      {block_chars:,} chars  (~{block_chars // 4:,} tokens)")
    print(f"  byte-identical across calls {identical}")
    notes = cap.calls[0]["payload"].get("identified_notes") or []
    print(f"  notes in the block          {len(notes)}")

    total_user = sum(len(json.dumps(c["payload"], ensure_ascii=False)) for c in cap.calls)
    print(f"  total user payload          {total_user:,} chars  (~{total_user // 4:,} tokens)")
    print(f"  of which this one block     {block_chars * len(cap.calls):,} chars "
          f"({100 * block_chars * len(cap.calls) / total_user:.0f}%)")

    # THE BLOCK, note by note, with the line items that claim each one.
    print("\n  THE BLOCK, note by note")
    print("  " + "-" * 96)
    claimed_by: dict[int, list[str]] = {}
    for i, note in enumerate(notes):
        size = len(json.dumps(note, ensure_ascii=False))
        owners = sorted(k for k in declares if _claims(k, note))
        claimed_by[i] = owners
        print(f"   {size:>7,}ch  note {str(note.get('note') or note.get('number') or '?'):>4}  "
              f"{_title(note)[:52]:54s} claimed by {len(owners)}")

    # PER CALL: how much of the block any candidate in that call could have used.
    print("\n  PER CALL — how much of the block that call's candidates could act on")
    print("  " + "-" * 96)
    waste = 0
    for n, call in enumerate(cap.calls, 1):
        cands = {c.get("canonical_key") for c in (call["payload"].get("candidates") or [])}
        usable = sum(len(json.dumps(notes[i], ensure_ascii=False))
                     for i in range(len(notes)) if cands & set(claimed_by[i]))
        waste += block_chars - usable
        print(f"   call {n:>3}  candidates {len(cands):>3}  usable notes "
              f"{sum(1 for i in range(len(notes)) if cands & set(claimed_by[i])):>3}/{len(notes)}  "
              f"usable {usable:>7,}ch of {block_chars:,}  "
              f"({100 * usable / block_chars if block_chars else 0:.0f}%)")

    print(f"\n  UNUSABLE NOTE TEXT SHIPPED   {waste:,} chars  (~{waste // 4:,} tokens) across "
          f"{len(cap.calls)} calls")
    print(f"  that is {100 * waste / total_user:.0f}% of the whole run's user payload")

    # And the fragmentation, which is a second and independent saving.
    numbers = Counter(str(note.get("note") or note.get("number") or "?") for note in notes)
    repeats = {k: v for k, v in numbers.items() if v > 1}
    if repeats:
        dup_chars = 0
        for i, note in enumerate(notes):
            num = str(note.get("note") or note.get("number") or "?")
            if numbers[num] > 1 and "CONTINU" in _title(note).upper():
                dup_chars += len(json.dumps(note, ensure_ascii=False))
        print(f"\n  FRAGMENTATION  note numbers appearing more than once: {repeats}")
        print(f"  continuation fragments in the block: {dup_chars:,} chars "
              f"(~{dup_chars // 4:,} tokens) per call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
