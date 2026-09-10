#!/usr/bin/env python
"""A part of a line is not bound to a statement or a section, because it is not printed in one.

WHY UNSCOPED, AND WHY THE OBVIOUS ALTERNATIVE IS WRONG. Each of the 77 parts declares
``inherits: notes``, and ``section_defaults["notes"]`` gives it ``statement: notes`` with
``section_scope: ["notes"]``. That made a part unofferable for a face row: with a statement
declared, ``_in_statement(part, "balance_sheet")`` is False.

The first correction here scoped each part to its PARENT's sections plus ``notes`` — bounded, and
wrong. It encodes where the WHOLE is reported onto where the PART is printed, and those differ by
design: that is what a part IS. The case that proves it is depreciation. The figure is printed in
the balance-sheet note on fixed assets, whose banner resolves to ``non_current_assets``, while its
whole (``is_pl__deprec_and_impairment_oper_exp``) is a profit-and-loss line. Measured under the
parent scoping: ``sub__ppe_depreciation`` offered under ``income_and_expenses`` and ``notes``, and
REFUSED under ``non_current_assets`` — so the PP&E-note reading was lost entirely.

Any single statement or section is wrong for the same reason. So a part declares neither, and
``_in_statement`` allows a concept it cannot place.

WHAT STILL PROTECTS THE MAPPING, since the gate no longer does:

  * NO PART DECLARES AN ALIAS — zero, measured, and zero collisions with a template concept's
    aliases. The exact, rule and fuzzy tiers work on alias evidence, so no deterministic tier can
    bind a caption to a part. Admitting them changes no string-evidence outcome.
  * A part is a candidate the model may choose, and every LLM mapping carries its confidence, its
    reason and the review machinery.
  * ``match_priority`` 80 against a median of 81 keeps them below the statement's own concepts in
    the reading order, and ``llm_candidate_cap`` (raised to 60 for this) bounds how many go.

NOTE CONTEXT IS A SEPARATE MECHANISM and was never statement-bound: ``identified_notes`` passes
every note a ``note_source`` declaration names, in full, and the semantic pool scores units by
IDF cosine with no statement filter. This script does not touch either.

    python scripts/unscope_parts.py            # report only
    python scripts/unscope_parts.py --write
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SEED = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    from app.schemas.line_items import load_line_item_set

    raw = json.loads(SEED.read_text(encoding="utf-8"))
    parts = [i for i in raw["items"] if i.get("parent")]
    before = {}
    for item in parts:
        before[item["key"]] = (item.get("statement", "<absent>"),
                               tuple(item.get("section_scope") or ()))
        item["statement"] = None
        item["section_scope"] = []

    from collections import Counter
    print(f"{len(parts)} parts unscoped")
    print(f"  from: {Counter(before.values()).most_common()}")
    print("  to  : statement=None, section_scope=[]")

    # The protection that replaces the gate, asserted here so a set that broke it cannot be written.
    aliased = [i["key"] for i in parts if (i.get("aliases") or i.get("aliases_i18n"))]
    if aliased:
        print(f"\n  !! REFUSING: {len(aliased)} parts declare an alias, so a deterministic tier "
              f"could bind a caption to one with no gate left to stop it: {aliased[:6]}")
        return 1
    print(f"  checked: 0 of {len(parts)} parts declare an alias, so no deterministic tier can "
          f"bind one")

    if not args.write:
        print("\n(report only — pass --write to save)")
        return 0

    load_line_item_set(json.loads(json.dumps(raw)), resolve=True)
    SEED.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
