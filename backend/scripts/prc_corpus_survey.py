#!/usr/bin/env python
"""WHICH FILINGS ARE CAS/PRC, AND WHAT THEY PRINT — the corpus this vocabulary is judged against.

Reads the dumps `scripts/vocab_corpus.py` already made (`../_vocab/*.json`), so it is instant and
can be re-run freely. Nothing here is scored; it establishes the population.

WHY THE SPLIT MATTERS. A CAS filing and an HKFRS filing are different documents, not the same
document in another language, and a value tuned on one regime tells you nothing about the other:

  * CAS numbers its notes with Han enumerators inside chapters (五、1, 七、25) and itemises far
    more of them; HKFRS numbers them 1..n.
  * A CAS note heading is a short noun phrase (median 8 characters); an HKFRS one is a sentence of
    small words (median 32).
  * Simplified and Traditional share no character bigram unless the glyphs are identical, so
    `services.note_context.subject_tokens` treats 折旧 and 折舊 as unrelated vocabulary.

    python scripts/prc_corpus_survey.py
"""
from __future__ import annotations

import collections
import io
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
VOCAB = pathlib.Path(__file__).resolve().parent.parent.parent / "_vocab"

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
LATIN = re.compile(r"[A-Za-z]{3,}")
HAN_ENUM = re.compile(r"^[一二三四五六七八九十百]+\s*[、．]")
# The Simplified-only and Traditional-only glyphs that separate the two regimes on sight.
SIMP_ONLY = set("务产额资负债权计为财报关联营销开损后价评种类别际")
TRAD_ONLY = set("務產額資負債權計為財報關聯營銷開損後價評種類別際")


def load():
    out = []
    for p in sorted(VOCAB.glob("*.json")):
        if p.name == "INDEX.json":
            continue
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and "notes" in d:
            d["_file"] = p.name
            out.append(d)
    return out


def classify(d):
    notes = d.get("notes") or []
    titles = [str(n.get("title") or "") for n in notes]
    numbers = [str(n.get("note") or n.get("number") or "") for n in notes]
    blob = " ".join(titles)
    han = bool(HAN.search(blob))
    han_enum = sum(1 for n in numbers if HAN_ENUM.match(n))
    simp = len(set(blob) & SIMP_ONLY)
    trad = len(set(blob) & TRAD_ONLY)
    if not han:
        regime = "HK/EN  (no Han in any heading)"
    elif han_enum > len(numbers) * 0.3:
        regime = "PRC/CAS"
    elif simp > trad:
        regime = "PRC-ish (Simplified, Arabic numbering)"
    elif trad > simp:
        regime = "HK bilingual (Traditional)"
    else:
        regime = "mixed/unclear"
    lens = sorted(len(t) for t in titles) or [0]
    return {
        "file": d["_file"],
        "pages": d.get("pages"),
        "regime": regime,
        "fragments": len(notes),
        "distinct": len(set(numbers)),
        "han_enum": han_enum,
        "simp_glyphs": simp,
        "trad_glyphs": trad,
        "median_title": lens[len(lens) // 2],
        "rows": sum(len(n.get("rows") or ()) for n in notes),
    }


def main() -> int:
    docs = load()
    rows = [classify(d) for d in docs]
    rows.sort(key=lambda r: (r["regime"], -(r["distinct"] or 0)))

    print("=" * 118)
    print(f"{len(rows)} filings in {VOCAB}")
    print("=" * 118)
    print(f"  {'file':<44s} {'pages':>5s} {'frags':>6s} {'notes':>6s} {'rows':>6s} "
          f"{'medTitle':>8s} {'regime':<38s}")
    for r in rows:
        print(f"  {r['file'][:44]:<44s} {str(r['pages'] or '?'):>5s} {r['fragments']:>6d} "
              f"{r['distinct']:>6d} {r['rows']:>6d} {r['median_title']:>8d} {r['regime']:<38s}")

    by = collections.Counter(r["regime"] for r in rows)
    print(f"\n  by regime: {dict(by)}")

    prc = [r for r in rows if r["regime"].startswith("PRC")]
    print(f"\n  PRC/CAS filings usable for vocabulary work: {len(prc)}")
    print(f"    total distinct notes {sum(r['distinct'] for r in prc):,}"
          f"   total rows {sum(r['rows'] for r in prc):,}")
    print(f"    files: {[r['file'] for r in prc]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
