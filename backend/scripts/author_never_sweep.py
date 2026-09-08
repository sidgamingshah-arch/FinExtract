"""Author `never_sweep` on the seven residual buckets that have no protection at all.

WHAT A RESIDUAL BUCKET DOES. A filing prints "Current assets" with a subtotal of 1,000. The
pipeline recognises Cash 400, Receivables 300, Inventories 200 = 900. The missing 100 is swept
into "Other Current Assets" so the section ties, which is right: an unexplained gap should land
somewhere visible rather than vanish.

WHAT `never_sweep` GUARDS. The sweep looks for unmatched rows inside the section. If it takes the
wrong one the figure lands in "Other" AND THE SECTION STILL TIES, so nothing downstream looks
wrong. The rows to fear are the section's own subtotal (sweeping "Total current assets 1,000"
would put 1,000 into Other and tie the section at 1,900), a derived figure that is not an asset at
all, and a row printed in a different section.

WHY THESE SEVEN. All 11 residuals carried `never_sweep: ["True"]` — a ticked spreadsheet box saved
as the word "True", vetoing nothing — which has been stripped. Four of the 11 do the same job
through `exclude_hints` and are left alone: bs_ncl (excludes net current assets / total assets
less current liabilities), is_oci (profit for the year / total comprehensive income),
cf_oper_indirect (cash generated from operations / net cash from operating) and cf_financing (net
cash from financing / net increase in cash). The other seven have nothing.

EVERY KEY IS VERIFIED TO EXIST BEFORE IT IS WRITTEN, and that is the point of doing this in a
script rather than by hand. `residual._residuals` expands an entry that NAMES A CONCEPT into that
concept's own captions, and keeps an entry it cannot resolve as prose. So a mistyped key does not
fail — it silently becomes a prose veto that matches no caption, which is the inert-configuration
failure this codebase keeps finding. Two keys that would have been natural to write are absent
from this rulebook entirely and are therefore NOT written: there is no `net_assets` and no
`total_assets_less_current_liabilities`, and the cash-flow statement has no operating-activities
subtotal key at all (its prose entry covers that instead).

Run from ``backend``:
    ../.venv/Scripts/python.exe scripts/author_never_sweep.py          # report only
    ../.venv/Scripts/python.exe scripts/author_never_sweep.py --write  # apply
"""
from __future__ import annotations

import json
import pathlib
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

from app.schemas.line_items import load_line_item_set  # noqa: E402

TEMPLATES = pathlib.Path("app/sample/templates")
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"
SEED = TEMPLATES / "output_csv_hk_line_items.json"

# The statement-level totals, scoped `bs_top_level` so no banner constrains them — which is
# exactly why a balance-sheet residual can meet one and must refuse it.
BS_TOTALS = ["bs_ca__total_assets", "bs_cl__total_liabilities",
             "bs_cl__total_equity_and_liabilities"]

# residual key -> (keys it must never absorb, prose for what no key can name)
PLAN: dict[str, tuple[list[str], list[str]]] = {
    "bs_nca__other_non_current_assets": (
        ["bs_nca__total_non_current_assets", *BS_TOTALS],
        ["any row printed in the current assets, equity or liabilities sections"],
    ),
    "bs_ca__other_current_assets": (
        ["bs_ca__total_current_assets", *BS_TOTALS],
        ["any row printed in the non-current assets, equity or liabilities sections"],
    ),
    "bs_equity__other_reserves": (
        # Four, because this section states its total more than one way and a sweep would take
        # whichever the filing happened to print.
        ["bs_equity__total_equity_and_reserves", "bs_equity__equity_and_reserves",
         "bs_equity__permanent_equity", "bs_equity__retained_profits",
         "bs_ca__total_assets", "bs_cl__total_equity_and_liabilities"],
        ["any row printed in the assets or liabilities sections",
         "any movement belonging to the retained-profits reconciliation"],
    ),
    "bs_cl__other_current_liabilities": (
        ["bs_cl__total_current_liabilities", *BS_TOTALS],
        ["any row printed in the non-current liabilities, equity or assets sections"],
    ),
    "is_pl__other_operating_expenses": (
        # Every subtotal on the way down the income statement: an operating-expense residual that
        # ate one of these would report a subtotal as an expense and still tie.
        ["is_pl__gross_profit", "is_pl__net_operating_profit", "is_pl__profit_loss_before_tax",
         "is_pl__profit_loss_bef_extraord_items", "is_pl__profit_for_the_year"],
        ["any row printed in the other comprehensive income section",
         "any row printed in the retained-profits reconciliation"],
    ),
    "is_retained__other_adj_to_retained_profits": (
        # This section has NO subtotal of its own — its members are all movements — so what it
        # must refuse is the figures it sits between: the year's result above it and the equity
        # balances below.
        ["is_pl__profit_for_the_year", "bs_equity__retained_profits",
         "bs_equity__total_equity_and_reserves"],
        ["any row printed on the face of the income statement",
         "any row printed in the equity section other than retained profits"],
    ),
    "cf_investing__other_invest_cash_flows": (
        ["cf_investing__cash_flows_from_invest_activities",
         "cf_financing__cash_flows_from_finance_activities"],
        # No operating-activities subtotal key exists in this rulebook, so it is named in prose.
        ["the net cash flow from operating activities subtotal",
         "any row printed under the operating or financing activities banners",
         "the net increase or decrease in cash and cash equivalents"],
    ),
}

# Left alone: these four already refuse their statement's own totals through `exclude_hints`.
ALREADY_GUARDED = ("bs_ncl__other_non_current_liabilities",
                   "is_oci__other_equity_and_reserves_adj",
                   "cf_oper_indirect__other_non_cash_adjs_oper",
                   "cf_financing__other_financing_cash_flows")


def main() -> int:
    write = "--write" in sys.argv
    known = {d.key for d in load_line_item_set(json.loads(
        SEED.read_text(encoding="utf-8"))).items}
    raw = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
    by_key = {m["canonical_key"]: m for m in raw["mappings"]}

    residuals = [m["canonical_key"] for m in raw["mappings"]
                 if m.get("value_scope") == "exclusive_residual"]
    print(f"  residual buckets in the rulebook : {len(residuals)}")
    print(f"  already guarded by exclude_hints : {len(ALREADY_GUARDED)}")
    print(f"  to author                        : {len(PLAN)}")

    missing_target = [k for k in PLAN if k not in by_key]
    if missing_target:
        print(f"\n  ABORT: these are not concepts in this rulebook: {missing_target}")
        return 1
    if set(PLAN) & set(ALREADY_GUARDED):
        print(f"\n  ABORT: overlap with the already-guarded set: "
              f"{sorted(set(PLAN) & set(ALREADY_GUARDED))}")
        return 1
    uncovered = sorted(set(residuals) - set(PLAN) - set(ALREADY_GUARDED))
    if uncovered:
        print(f"\n  ABORT: residuals in neither list, so they would be silently skipped: "
              f"{uncovered}")
        return 1

    # EVERY NAMED KEY MUST EXIST. An unresolvable entry becomes a prose veto matching no caption.
    bad: dict[str, list[str]] = {}
    for residual, (keys, _prose) in PLAN.items():
        absent = [k for k in keys if k not in known]
        if absent:
            bad[residual] = absent
    if bad:
        print("\n  ABORT: named keys that do not exist in the 475-definition set:")
        for residual, absent in bad.items():
            print(f"     {residual}: {absent}")
        return 1
    print(f"\n  every named key resolves: "
          f"{sum(len(k) for k, _ in PLAN.values())} keys across {len(PLAN)} buckets")

    for residual, (keys, prose) in PLAN.items():
        print(f"\n  {residual}")
        for k in keys:
            print(f"      key   {k}")
        for p in prose:
            print(f"      prose {p}")
        if write:
            by_key[residual]["never_sweep"] = [*keys, *prose]

    if not write:
        print("\n  report only — pass --write to apply")
        return 0

    ONTOLOGY.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  written to {ONTOLOGY}")
    print("  now re-run scripts/build_line_items.py so the line-item seed carries it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
