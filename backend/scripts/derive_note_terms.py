#!/usr/bin/env python
"""Derive each line item's semantic TERM SETS from its own authored regexes.

WHY DERIVE RATHER THAN AUTHOR. The instruction is that the descriptors must not overfit the two
filings in hand, and hand-writing 77 x 3 term sets while looking at laisun and suncreate is exactly
how overfitting happens: the terms would come out as the phrasings those two documents use. The
existing patterns are a better source precisely because of where they came from — they were written
from the repo's own specifications, in English, Traditional and Simplified Chinese, to cover
phrasings NEITHER filing uses. Stripping them to their literal vocabulary inherits that generality
instead of re-earning it.

WHAT IS EXTRACTED. A pattern like

    general\\s+and\\s+administrative|administrative\\s+expenses?|管理费用|管理費用

is a set of alternatives over literal words with regex glue between them. This keeps the words and
throws the glue away, yielding

    ["general and administrative", "administrative expenses", "管理费用", "管理費用"]

WHAT IS DELIBERATELY DROPPED, and each one is a decision:

  * ANCHORS AND QUANTIFIERS (`^`, `$`, `{0,30}`, `.*`) — position and repetition are matching
    instructions, and a term is scored not matched.
  * CHARACTER CLASSES (`[销銷]`) are EXPANDED, not dropped: `[销銷]售` becomes both 销售 and 銷售,
    because the class exists to cover two scripts and a term set must carry both.
  * A FRAGMENT SHORTER THAN THREE LETTERS, or one that is pure punctuation, contributes no subject
    and would only add noise to the IDF.
  * NUMBER-ONLY alternatives (`^\\s*\\d+[.、)]`) — a note NUMBER is not a subject.

WHAT THIS CANNOT FIX. If a line's `note_title_any` never named the container, its derived
`note_terms` will not either — the derivation inherits coverage, it does not invent it. The report
names every line whose note terms come out empty, which is the list someone has to author by hand.

    python scripts/derive_note_terms.py            # report only
    python scripts/derive_note_terms.py --write
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

# Regex glue that carries no subject. Order matters: the classes are expanded before this runs.
_GLUE = re.compile(r"\\s\+|\\s\*|\\d\+|\\w\+|\\.|\\b|[\^$()?*+|]|\{[^}]*\}|\[\^[^\]]*\]")
_CLASS = re.compile(r"\[([^\]^]+)\]")
_NON_GROUP = re.compile(r"\(\?:")
_LETTERS = re.compile(r"[a-z]{3,}|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _expand_classes(pattern: str) -> list[str]:
    """`[销銷]售` -> ['销售', '銷售'].

    A character class in these patterns is almost always the two Han scripts for one word, so
    dropping it would lose one script entirely. Expanded to the cartesian product, capped because a
    pattern with several classes would otherwise blow up.
    """
    classes = _CLASS.findall(pattern)
    if not classes:
        return [pattern]
    if len(classes) > 4:
        return [_CLASS.sub(lambda m: m.group(1)[0], pattern)]
    out = []
    for combo in itertools.product(*[list(c) for c in classes]):
        rebuilt = pattern
        for choice in combo:
            # A LAMBDA, NOT THE STRING: `re.sub` reads a replacement string as a TEMPLATE, so a
            # class holding a backslash raises "bad escape". The choice is a literal character.
            rebuilt = _CLASS.sub(lambda _m, c=choice: c, rebuilt, count=1)
        out.append(rebuilt)
    return out[:24]


def terms_of(patterns) -> list[str]:
    """The literal vocabulary a group of patterns is written over."""
    seen: dict[str, None] = {}
    for raw in (patterns or ()):
        for expanded in _expand_classes(str(raw)):
            expanded = _NON_GROUP.sub("(", expanded)
            for alternative in expanded.split("|"):
                text = _GLUE.sub(" ", alternative)
                text = re.sub(r"[\\/,.;:_\-\u3001\u3002()\[\]{}]+", " ", text)
                text = " ".join(text.split()).strip().lower()
                if not text or not _LETTERS.search(text):
                    continue
                seen.setdefault(text, None)
    return list(seen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    from app.schemas.line_items import load_line_item_set

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    empty_notes, empty_rows, filled = [], [], 0
    for item in raw["items"]:
        source = item.get("note_source")
        if not source:
            continue
        note_terms = terms_of(source.get("note_title_any"))
        row_terms = terms_of(source.get("row_caption_any"))
        none_terms = terms_of(source.get("row_caption_none"))
        source["note_terms"] = note_terms
        source["row_terms"] = row_terms
        source["row_terms_none"] = none_terms
        filled += 1
        if not note_terms:
            empty_notes.append(item["key"])
        if not row_terms:
            empty_rows.append(item["key"])

    print(f"{filled} line items given term sets")
    print(f"  note_terms empty : {len(empty_notes)}  {empty_notes[:5]}")
    print(f"  row_terms empty  : {len(empty_rows)}  {empty_rows[:5]}")

    counts = [(len(i['note_source'].get('note_terms') or []),
               len(i['note_source'].get('row_terms') or []))
              for i in raw["items"] if i.get("note_source")]
    print(f"  note_terms per item: min {min(c[0] for c in counts)} "
          f"median {sorted(c[0] for c in counts)[len(counts) // 2]} "
          f"max {max(c[0] for c in counts)}")
    print(f"  row_terms  per item: min {min(c[1] for c in counts)} "
          f"median {sorted(c[1] for c in counts)[len(counts) // 2]} "
          f"max {max(c[1] for c in counts)}")

    sample = next(i for i in raw["items"] if i.get("key") == "sub__ga_depreciation")
    print("\n  sub__ga_depreciation, the case a blended probe got wrong:")
    print(f"    note_terms -> {sample['note_source']['note_terms'][:8]}")
    print(f"    row_terms  -> {sample['note_source']['row_terms'][:5]}")

    if not args.write:
        print("\n(report only — pass --write to save)")
        return 0
    load_line_item_set(json.loads(json.dumps(raw)), resolve=True)
    SEED.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
