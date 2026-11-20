"""THE STATEMENT SETUP CONTROLS — the export's check that the spread holds together.

The Statement Setup / Controls block carries five figures a reader uses to trust the rest of the
sheet: Total Assets, Total Equity & Reserves & Liab, Total Income & Expenses, Difference and
Unexplained Adj to Ret Profits. Written by `api.routes.extractions._build_supplemental_rows`.

WHAT THEY CHECK IS THE SPREAD, NOT THE FILING. A filing always balances; what may not is the set of
lines we read from it. So every figure here is the figure the exported grid itself shows for that
line (`rollups.figures_as_shown`: an analyst's correction first, then the sum of the lines the
template says the total is made of where those were read, then the printed figure), on the basis the
filing's statements print. Difference is then 0 exactly when the spread's assets equal its equity
and liabilities, and shows the gap when a line was missed or counted twice.

THEY USED TO READ THE PRINTED TOTAL, through a type slip: the computed totals were looked up and
handed to a decimal parser as `Calculated` objects, which it cannot read, so every control fell back
to the printed row — on a filing that prints its totals, a Difference of 0 whatever the extraction
did, and on one that does not (the HKEX net-assets format), every control blank. The fallback also
took the first value of the row whatever its basis. Measured on the reference filings: China SCE
(1966) and 佳明 showed blank controls; their spreads are out by 1,908,536 and 130,755 (thousands).

UNEXPLAINED ADJ TO RET PROFITS IS A ROLL-FORWARD. Last year's closing retained profits, plus this
year's profit, plus the retained-profits movements the spread identifies (dividends, stock dividends,
transfers to reserves, prior-period adjustments, other adjustments), against this year's closing
retained profits; what is left is the unexplained adjustment. It used to be "0" whenever Difference
was 0 and blank otherwise, and never looked at retained profits at all.

  * THE PROFIT IS "Profit for the year" as the template defines it, which already deducts the
    minority interests' share (`is_pl__profit_for_the_year` rolls up `is_pl__minority_interests_pl`):
    the owners' profit, the one that reaches the company's retained profits.
  * A MOVEMENT THE SWEEP PUT ON A LINE IS NOT EXPLAINED. A statement of changes in equity prints the
    year's profit in its retained-profits column, no retained-profits line claims that row, and the
    residual sweep puts it on Other Adj to Retained Profits — counted there, the profit would be
    counted twice (measured: 1966 and 佳明 both carried exactly the year's loss on that line). A line
    the spread identifies, or an analyst answered for, counts; a sweep is by construction the part no
    line explains, which is what this row reports.
  * A MOVEMENT NOT READ COUNTS AS NOTHING, as in any roll-forward plug: a filing that pays no
    dividend prints no dividend row, and a dividend the extraction missed is exactly what the
    unexplained figure is there to show.
  * BLANK, AND SAID WHY, when closing or opening retained profits or the profit is not there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

#: The keys each control reads, the output_csv template's first and the older HKFRS template's
#: after it, whichever the template in use carries.
ASSETS = ("bs_ca__total_assets", "bs_total_assets")
EQUITY_AND_LIABILITIES = ("bs_cl__total_equity_and_liabilities", "bs_total_equity_and_liabilities")
PROFIT = ("is_pl__profit_for_the_year", "pl_profit_for_the_year")
RETAINED = ("bs_equity__retained_profits",)
#: The retained-profits movements the template spreads, each carrying its own sign (dividends and
#: transfers are `natural_negative`). Proposed dividends are left out: a dividend proposed after the
#: year end is not deducted from that year's retained profits.
MOVEMENTS = ("is_retained__cash_div_pref_shares", "is_retained__cash_div_common_shares",
             "is_retained__stock_dividends_nc", "is_retained__transfer_to_reserves",
             "is_retained__prior_period_adjustments", "is_retained__other_adj_to_retained_profits")

#: Below this a difference is rounding: the figures are carried as floats to the cent.
_ZERO = Decimal("0.005")

SETUP_KEYS = {
    "total_assets": "statement_setup_controls__total_assets",
    "total_equity_and_liabilities": "statement_setup_controls__total_equity_reserves_liab",
    "total_income": "statement_setup_controls__total_income_expenses",
    "difference": "statement_setup_controls__difference",
    "unexplained_retained": "statement_setup_controls__unexplained_adj_to_ret_profits",
}


@dataclass
class Controls:
    total_assets: Decimal | None = None
    total_equity_and_liabilities: Decimal | None = None
    total_income: Decimal | None = None
    difference: Decimal | None = None
    unexplained_retained: Decimal | None = None
    #: Per setup key, what a reader of the row needs to know: whether a total is the sum of the
    #: spread's lines or the printed figure, what the roll-forward could not find, what it did not
    #: count as explained.
    flags: dict[str, list[str]] = field(default_factory=dict)


def money(value: float | Decimal | None) -> Decimal | None:
    """A figure to the cent, written without a trailing `.0` when it is whole."""
    if value is None:
        return None
    d = Decimal(repr(round(float(value), 2)))
    if abs(d) < _ZERO:
        return Decimal(0)
    return d.quantize(Decimal(1)) if d == d.to_integral_value() else d


def compute(template_def: dict | None, rows: list[dict], basis: str) -> Controls:
    """The five controls for the current period, on `basis`, as the exported grid shows the lines."""
    from app.services.rollups import evaluate_rows, figures_as_shown, swept_lines

    out = Controls()
    if not template_def:
        return out
    shown = figures_as_shown(template_def, rows, basis, "current")
    prior = figures_as_shown(template_def, rows, basis, "prior")
    calc = evaluate_rows(template_def, rows, basis, "current")

    def pick(keys: tuple[str, ...], setup: str) -> Decimal | None:
        for key in keys:
            if shown.get(key) is not None:
                c = calc.get(key)
                # The sum of the lines unless an analyst answered for the total (the figure shown
                # is then theirs) or nothing it is made of was read (the printed figure).
                summed = (c is not None and c.computable and c.value is not None
                          and float(c.value) == shown[key])
                out.flags.setdefault(SETUP_KEYS[setup], []).append(
                    f"control:{'sum_of_lines' if summed else 'as_shown'}:{key}")
                return money(shown[key])
        return None

    out.total_assets = pick(ASSETS, "total_assets")
    out.total_equity_and_liabilities = pick(EQUITY_AND_LIABILITIES, "total_equity_and_liabilities")
    out.total_income = pick(PROFIT, "total_income")
    if out.total_assets is not None and out.total_equity_and_liabilities is not None:
        out.difference = money(out.total_assets - out.total_equity_and_liabilities)

    out.unexplained_retained = _roll_forward(shown, prior, swept_lines(rows, basis, "current"),
                                             out.flags.setdefault(SETUP_KEYS["unexplained_retained"],
                                                                  []))
    return out


def _roll_forward(shown: dict, prior: dict, swept: set[str], flags: list[str]) -> Decimal | None:
    closing = next((shown[k] for k in RETAINED if shown.get(k) is not None), None)
    opening = next((prior[k] for k in RETAINED if prior.get(k) is not None), None)
    profit = next((shown[k] for k in PROFIT if shown.get(k) is not None), None)
    missing = [name for name, v in (("closing_retained_profits", closing),
                                    ("opening_retained_profits", opening),
                                    ("profit_for_the_year", profit)) if v is None]
    if missing:
        flags.extend(f"retained_rollforward:missing:{name}" for name in missing)
        return None
    explained = Decimal(str(opening)) + Decimal(str(profit))
    if not any(shown.get(key) is not None for key in MOVEMENTS):
        flags.append("retained_rollforward:no_movement_read")
    for key in MOVEMENTS:
        if shown.get(key) is None:
            continue
        if key in swept:
            flags.append(f"retained_rollforward:swept_not_explained:{key}")
            continue
        explained += Decimal(str(shown[key]))
        flags.append(f"retained_rollforward:counted:{key}")
    return money(Decimal(str(closing)) - explained)
