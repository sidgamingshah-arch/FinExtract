#!/usr/bin/env python
"""Move a derived parent's CAPTION RECOGNITION down onto the part that reads the face.

WHY THIS IS THE COROLLARY OF THE LOCK, and not an optimisation. A derived parent's figure is its
cascade's and nothing else's — not the model's, not an alias's, not a semantic probe's — so the
nine parents are unreachable by every matching tier. Measured, that cost three figures on the two
reference filings:

    is_pl__sales_revenues                      4,995,768 -> 2,609,259   (fell to rung P6)
    bs_ca__secur_and_other_fincl_assets_cp       174,822 -> (empty)
    bs_nca__due_from_related_parties_ltp  203,287,842.45 -> (empty)

None of those was ever the cascade's answer. Each arrived from a printed caption binding to the
PARENT, which is precisely the short-circuit the lock exists to stop: a figure on the parent means
no rung ran, so the record loses which of the filing's disclosures the number came from.

BUT THE FACE IS A LEGITIMATE SOURCE, and the cascades say so themselves. Revenue's rung P1 is
`sub__face_principal_revenue`, whose note reads "主营业务收入 reported on the face of the income
statement" — the author's intent is that the face figure enters through P1, as a rung, with a trail.
It never did, because the parent hoarded the recognition: all 77 parts declare zero aliases, zero
regex hints and no `match_priority`, while the parents carry 9-23 aliases each at priorities 19-81.
So the face row bound to the parent every time and P1 stayed empty.

WHAT THIS MOVES, and it MOVES rather than copies — the parents must keep nothing, or the lock is
only half applied and a future reader sees recognition on a concept nothing can reach:

    aliases, aliases_i18n, regex_hints, keyword_hints, match_priority

TWO PARENTS ONLY. They are the two with a part built to read the face. The other six parents'
parts are all note totals, so there is nowhere for their recognition to go and their face route is
genuinely absent until someone authors a face part for them — which is a decision about the
filings, not a migration.

Run:  python scripts/move_recognition_to_face_parts.py [--revert]
"""
from __future__ import annotations

import argparse
import json
import pathlib

SET = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
       / "output_csv_hk_line_items.json")

# parent -> the part whose cascade rung reads the FACE. Both are named in the part's own key and
# confirmed by the rung note that consumes it.
FACE_PART = {
    "is_pl__sales_revenues": "sub__face_principal_revenue",
    "bs_nca__due_from_related_parties_ltp": "sub__rp_bs_face_receivables",
}

MOVED = ("aliases", "aliases_i18n", "regex_hints", "keyword_hints", "match_priority")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true", help="move the recognition back up")
    args = ap.parse_args()

    doc = json.loads(SET.read_text(encoding="utf-8"))
    items = {i["key"]: i for i in doc["items"]}
    moves: list[str] = []

    for parent_key, part_key in FACE_PART.items():
        parent, part = items.get(parent_key), items.get(part_key)
        if parent is None or part is None:
            print(f"  SKIP {parent_key}: {'parent' if parent is None else part_key} not in the set")
            continue
        src, dst = (part, parent) if args.revert else (parent, part)
        for field in MOVED:
            if field not in src:
                continue
            value = src.pop(field)
            dst[field] = value
            size = len(value) if isinstance(value, (list, dict)) else value
            moves.append(f"{field:16s} {src['key']} -> {dst['key']}  ({size})")

    SET.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{SET.name}: {len(moves)} field(s) moved")
    for line in moves:
        print(f"    {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
