#!/usr/bin/env python
"""Declare `extraction_mode: extract` on every `type: derived` line — the expectation, in the data.

WHY IT IS AN EXPECTATION AND NOT A PREFERENCE. The nine derived lines in the shipped set carried
THREE different extraction modes between them — `extract` on revenue, `derive` on two, and
`extract_or_derive` on the rest — while being the same kind of thing: a parent whose figure is
produced by a declared cascade over its own parts. Nothing read the difference in a way that made
those three modes mean three things, so the set was asserting a distinction it did not have.

WHAT THE MODE ACTUALLY DECIDES for a derived line, now that both axes are separated:

    type: derived       a declared cascade fills the line (`services.line_items.evaluate`)
    extraction_mode     whether a PRINTED ROW may fill it where no rung fires

and the second is the one that had to be settled. Measured on the reference filings, three of the
eight focus lines publish nothing at all because no rung fires and no caption is allowed to reach
them; `extract` is what lets the printed row be read in that case, and it is the documented intent
of `extract_or_derive` already ("a filing that does print the subtotal must have the printed row
read", `mapping._computed_claim`). Revenue is the proof: it is the one derived line that was
already `extract`, and it is the one that publishes 4,995,768 off the face with no rung firing.

THE MODEL IS STILL NEVER ASKED. That guarantee used to ride on the mode, which is why flipping it
would once have been unsafe. It now rides on the type — `OntologyMapping.item_type`, read by
`mapping._llm_withheld` — so it holds for all nine regardless of what the mode says. Writing the
mode therefore changes what the DETERMINISTIC tiers may do and nothing about what the model sees.

Run:  python scripts/mark_derived_extract.py [--revert]
"""
from __future__ import annotations

import argparse
import json
import pathlib

SETS = [pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
        / "output_csv_hk_line_items.json"]

# THE TWO DERIVED LINES THIS DOES NOT TOUCH, each with the measurement that decided it.
#
# `statement_setup_controls__periods` is not a figure. It has no cascade at all
# (`implemented_by: rulebook_derivation`), it declares `match_priority: 90` — the highest in the
# set — and its keyword hints are `months`, `year ended`, `six months`, `quarter`, `period`. Those
# are words half the income statement's captions contain. `derive` is what currently keeps every
# caption off it, and making it matchable would let a priority-90 control claim rows that belong to
# real lines.
#
# `bs_nca__secur_and_other_fincl_assets_ltp` WAS FLIPPED AND MEASURED, AND THE FIGURE MOVED. Run on
# laisun, `extract` publishes 128,412 (prior 119,364) where `derive` publishes 788,507 (941,274),
# and the document yields 296 bound rows instead of 297. The cascade is unaffected — rung LTP_P1
# still fires and still computes 788,507 from `sub__ltp_nc_portion_of_fincl_asset_notes`.
#
# THE PRECEDENCE IS WHY, and it is worth naming because it is what `extract` BUYS on a derived
# line: `stages/note_sourced` keeps the PRINTED figure over the cascade and flags the difference
# (`note_sourced_differs_from_printed:cascade`, written at note_sourced.py:458). So the cascade
# fills only what a printed row left empty. That is the right behaviour in general — revenue relies
# on it, publishing 4,995,768 off the face while rung P6 computes 2,609,259 — but on this one line
# it replaces the figure this work settled on, and which of the two is correct is a question about
# the filing rather than about the configuration. So the declaration stays as it was.
#
# The other derived line whose mode really changed — `is_pl__deprec_and_impairment_oper_exp`,
# `derive` -> `extract` — was measured the same way and moved nothing: 529,841 before and after,
# because no printed row reaches it at all (its figure is stated only in a footnote sentence).
EXCEPT = {"statement_setup_controls__periods", "bs_nca__secur_and_other_fincl_assets_ltp"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true",
                    help="put the three original modes back, from the record below")
    args = ap.parse_args()

    # What each key declared BEFORE, so `--revert` is exact rather than approximate.
    was = {
        "bs_nca__due_from_related_parties_ltp": "extract_or_derive",
        "bs_nca__secur_and_other_fincl_assets_ltp": "derive",
        "bs_ca__secur_and_other_fincl_assets_cp": "extract_or_derive",
        "bs_ca__other_receivables_cp": "extract_or_derive",
        "is_pl__sales_revenues": "extract",
        "is_pl__deprec_and_impairment_cos": "extract_or_derive",
        "is_pl__deprec_and_impairment_oper_exp": "derive",
        "notes__contingent_liabilities": "extract_or_derive",
    }

    for path in SETS:
        doc = json.loads(path.read_text(encoding="utf-8"))
        changed = []
        for item in doc.get("items", []):
            if item.get("type") != "derived" or item["key"] in EXCEPT:
                continue
            target = was[item["key"]] if args.revert else "extract"
            if item.get("extraction_mode") != target:
                changed.append((item["key"], item.get("extraction_mode"), target))
                item["extraction_mode"] = target
        # `indent=1` AND NO ADDED NEWLINE, matching how the file is already written. Reformatting
        # it would put 539 items of churn in the diff and bury the eight lines that changed.
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{path.name}: {len(changed)} changed")
        for key, before, after in changed:
            print(f"    {key:48s} {before} -> {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
