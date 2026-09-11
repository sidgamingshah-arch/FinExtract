#!/usr/bin/env python
"""Does every line that can RECOGNISE a Simplified heading also SCORE against one?

THE ASYMMETRY THIS FINDS. A line item reaches its notes two ways, and they read different fields:

  * `note_source.note_title_any` — regexes, matched against the heading. Authored with both scripts
    on most lines ("長期待攤費用|长期待摊费用").
  * `note_source.note_terms` — the text the SEMANTIC probe is built from
    (`line_item_notes.note_probe`), scored against the heading's tokens.

If the patterns carry Simplified and the terms do not, then on a PRC filing the line is reachable
only by regex. That is not merely "less good": the two are a UNION by design, and semantic
selection is what covers the note an author's patterns did not anticipate. A Traditional-only
`note_terms` turns the union into a single mechanism on exactly the filings where the patterns are
hardest to write — and it shows up as a note scoring 0.000 against the line it belongs to, which
reads like a selection failure rather than missing vocabulary.

`services.note_context.subject_tokens` emits Han character BIGRAMS, so Traditional and Simplified
share no token unless the characters are identical. 折舊 and 折旧 do not intersect at all.

WHAT IT DOES NOT DO is convert anything. Traditional -> Simplified is not a character map for
financial vocabulary (攤銷/摊销 is, 預付土地租賃款項/预付土地租赁款项 is, but 計入/计入 vs a term a
CAS filer would simply write differently is not), so the report names the gap and leaves the wording
to a reviewer. `app.services.han.to_simplified` exists and is used for NORMALISATION; using it to
generate authored vocabulary would put machine output in a field whose whole value is that a person
wrote it.

    python scripts/audit_script_coverage.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def _han(text: str) -> str:
    return "".join(HAN.findall(text or ""))


def main() -> int:
    from app.schemas.line_items import load_line_item_set
    from app.services.han import to_simplified
    from app.services.line_item_requests import asked_about

    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    rows = []
    for item in cfg.items:
        source = getattr(item, "note_source", None)
        if source is None:
            continue
        pats = list(getattr(source, "note_title_any", None) or ())
        terms = list(getattr(source, "note_terms", None) or ())
        if not pats:
            continue

        pat_han = _han(" ".join(pats))
        term_han = _han(" ".join(terms))
        # "Carries Simplified" is tested by whether folding to Simplified CHANGES the string: a
        # Traditional-only field folds to something different, a field already carrying Simplified
        # forms contains them verbatim.
        pat_has_simp = bool(pat_han) and any(
            to_simplified(ch) == ch for ch in pat_han if to_simplified(ch) != ch) or bool(
            set(pat_han) & set(to_simplified(pat_han)))
        # Clearer test: does the field contain the SIMPLIFIED spelling of its own Han content?
        pat_simp_present = bool(pat_han) and to_simplified(pat_han) != pat_han and any(
            to_simplified(c) in pat_han for c in pat_han if to_simplified(c) != c)
        term_simp_present = bool(term_han) and any(
            to_simplified(c) in term_han for c in term_han if to_simplified(c) != c)

        rows.append({
            "key": item.key,
            "asked": asked_about(item),
            "patterns": len(pats),
            "terms": len(terms),
            "pat_han": bool(pat_han),
            "term_han": bool(term_han),
            "pat_simplified": pat_simp_present,
            "term_simplified": term_simp_present,
            "term_list": terms,
        })

    asked = [r for r in rows if r["asked"]]
    print("=" * 100)
    print(f"{len(rows)} lines declare note_title_any; {len(asked)} of them are asked about")
    print("=" * 100)

    no_terms = [r for r in asked if r["terms"] == 0]
    han_pat_no_han_term = [r for r in asked if r["pat_han"] and not r["term_han"]]
    simp_pat_no_simp_term = [r for r in asked
                             if r["pat_simplified"] and not r["term_simplified"]]

    print(f"\n  {len(no_terms):>4}  no `note_terms` at all — the probe falls back to blended prose")
    for r in no_terms[:12]:
        print(f"          {r['key']}")
    if len(no_terms) > 12:
        print(f"          … and {len(no_terms) - 12} more")

    print(f"\n  {len(han_pat_no_han_term):>4}  patterns carry Han, `note_terms` carries NONE"
          f" — unreachable semantically on any CJK filing")
    for r in han_pat_no_han_term[:12]:
        print(f"          {r['key']}")
        print(f"             terms {r['term_list'][:6]}")

    print(f"\n  {len(simp_pat_no_simp_term):>4}  patterns carry SIMPLIFIED, `note_terms` does not"
          f" — regex-only on a PRC filing")
    for r in simp_pat_no_simp_term:
        print(f"          {r['key']}")
        print(f"             terms {r['term_list'][:8]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
