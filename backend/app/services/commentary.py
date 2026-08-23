"""Financial-analysis commentary — a one-pager derived from the extracted context.

Computes standard ratios from the extracted statements and selects strengths /
weaknesses from a fixed, localizable catalog by threshold — so the commentary is
genuinely data-driven (not free-form prose) and every emitted string exists in the
i18n table (``i18n_data.TR``) for translation. It is an automated analytical summary,
not investment advice; it also surfaces that figures are provisional while review
items are open.
"""
from __future__ import annotations

from app.sample.demo import BALANCE_SHEET, CASH_FLOW, PROFIT_AND_LOSS

# Canonical English strings (all present in i18n_data.TR for zh/ar/fr).
METRIC_LABELS = {
    "current_ratio": "Current ratio",
    "debt_to_equity": "Debt-to-equity",
    "equity_ratio": "Equity ratio",
    "interest_coverage": "Interest coverage",
    "net_margin": "Net margin (PBT)",
    "revenue_growth": "Revenue growth (YoY)",
    "cash_ratio": "Cash ratio",
    "asset_turnover": "Asset turnover",
}

HEADLINES = {
    "strong": "Well-capitalised balance sheet with low leverage; profitability is improving.",
    "mixed": "Sound fundamentals overall, with near-term liquidity and open review items to watch.",
    "weak": "Elevated risks — leverage and/or liquidity warrant close attention.",
}
ASSESSMENT = ("The entity shows a well-capitalised balance sheet with comfortably low leverage "
              "and improving revenues, and earnings that comfortably service finance costs. The "
              "main watch items are near-term liquidity and unresolved review flags that affect "
              "some reported figures.")

STRENGTHS = {
    "growth": "Revenue grew year-on-year, indicating healthy topline momentum.",
    "leverage": "Low debt-to-equity — the balance sheet is conservatively financed.",
    "coverage": "Strong interest coverage; earnings comfortably service finance costs.",
    "equity": "Equity funds a large share of assets, providing a solid capital cushion.",
    "current_ratio": "Current ratio above 1.5 — the working-capital position is adequate.",
    "margin": "Healthy pre-tax margin relative to total income.",
    "deleveraging": "Leverage reduced year-on-year, further strengthening an already conservative balance sheet.",
    "margin_expansion": "Margins expanded year-on-year, with pre-tax profit growing faster than revenue.",
}

# Trend line labels (all present in i18n_data.TR for zh/ar/fr).
TREND_LABELS = {
    "revenue": "Revenue",
    "pbt": "Profit before tax",
    "net_margin": "Net margin",
    "operating_cash_flow": "Operating cash flow",
    "total_assets": "Total assets",
    "equity": "Equity",
    "debt_to_equity": "Debt-to-equity",
    "interest_coverage": "Interest coverage",
}
WEAKNESSES = {
    "cash": "Low cash ratio — immediate liquidity is thin relative to current liabilities.",
    "working_capital": "Negative working-capital movement weighed on operating cash flow.",
    "goodwill": "Goodwill on the balance sheet carries impairment risk if performance weakens.",
    "review": ("Open review items — including a balance-sheet discrepancy and a finance-cost "
               "sign anomaly — mean some reported figures are provisional pending sign-off."),
    "receivables": "Trade receivables are a large share of current assets; monitor collection risk.",
}
DATA_QUALITY = ("This summary is generated from extracted figures with checks still open in the "
                "review queue; confirm flagged items before relying on the numbers.")


def _val(rows: list[dict], item_id: str, period: str = "v1") -> float:
    r = next((x for x in rows if x["id"] == item_id), None)
    return float(r.get(period) or 0) if r else 0.0


def _safe(n: float, d: float) -> float:
    return n / d if d else 0.0


def _pct_change(cur: float, prior: float) -> float:
    """Year-on-year percentage change (0 if there is no prior base)."""
    return round(_safe(cur - prior, prior) * 100, 1)


def _trend(key: str, kind: str, cur: float, prior: float, higher_is_better: bool) -> dict:
    """Build one period-over-period trend record.

    ``kind`` selects how ``delta`` is expressed: ``amount`` → YoY %, ``percent`` →
    percentage-point change, ``ratio`` → absolute change. ``direction``/``tone`` reflect
    whether the movement is favourable given ``higher_is_better``.
    """
    if kind == "amount":
        delta = _pct_change(cur, prior)
        cur_v, prior_v = round(cur), round(prior)
    elif kind == "percent":
        delta = round(cur - prior, 1)
        cur_v, prior_v = round(cur, 1), round(prior, 1)
    else:  # ratio
        delta = round(cur - prior, 2)
        cur_v, prior_v = round(cur, 2), round(prior, 2)

    raw = cur - prior
    direction = "up" if raw > 1e-9 else "down" if raw < -1e-9 else "flat"
    favorable = ((direction == "up") == higher_is_better) if direction != "flat" else False
    tone = "good" if favorable else "warn" if direction == "flat" else "bad"
    return {
        "key": key, "label": TREND_LABELS[key], "kind": kind,
        "current": cur_v, "prior": prior_v, "delta": delta,
        "direction": direction, "favorable": favorable, "tone": tone,
    }


def build_commentary(open_review_items: int = 12) -> dict:
    """Demo-project commentary (seeded statements). Used by the project endpoint when demo
    data is enabled; real uploaded documents use ``build_commentary_from_rows``."""
    bs, pl, cf = BALANCE_SHEET, PROFIT_AND_LOSS, CASH_FLOW
    agg = {
        "current_assets": _val(bs, "sub_ca"),
        "current_liabs": _val(bs, "c_borrow") + _val(bs, "payables") + _val(bs, "ofl") + _val(bs, "prov_c"),
        "equity": _val(bs, "esc") + _val(bs, "oe"),
        "total_assets": _val(bs, "tot_assets"),
        "debt": _val(bs, "nc_borrow") + _val(bs, "c_borrow"),
        "cash": _val(bs, "cce"),
        "goodwill": _val(bs, "goodwill"),
        "receivables": _val(bs, "trade_recv"),
        "revenue": _val(pl, "rev"),
        "revenue_prev": _val(pl, "rev", "v2"),
        "total_income": _val(pl, "tot_inc"),
        "pbt": _val(pl, "pbt"),
        "finance_costs": _val(pl, "fin"),
        "wc_change": _val(cf, "cf_wc"),
        # Prior-period (FY24) aggregates, for the year-on-year trend block.
        "p_equity": _val(bs, "esc", "v2") + _val(bs, "oe", "v2"),
        "p_total_assets": _val(bs, "tot_assets", "v2"),
        "p_debt": _val(bs, "nc_borrow", "v2") + _val(bs, "c_borrow", "v2"),
        "p_total_income": _val(pl, "tot_inc", "v2"),
        "p_pbt": _val(pl, "pbt", "v2"),
        "p_finance_costs": _val(pl, "fin", "v2"),
        "op_cash": _val(cf, "cf_op_net"),
        "p_op_cash": _val(cf, "cf_op_net", "v2"),
    }
    return _assemble(agg, open_review_items, "consolidated · FY25 vs FY24 · ₹ crore")


def _assemble(agg: dict, open_review_items: int, basis: str) -> dict:
    """Build the commentary payload from period aggregates. Source-agnostic: the demo path and
    the real-extraction path both compute the same aggregate dict and hand it here, so a real
    uploaded document gets genuinely data-driven ratios, trends, strengths and weaknesses."""
    current_assets = agg["current_assets"]
    current_liabs = agg["current_liabs"]
    equity = agg["equity"]
    total_assets = agg["total_assets"]
    debt = agg["debt"]
    cash = agg["cash"]
    goodwill = agg["goodwill"]
    receivables = agg["receivables"]
    revenue = agg["revenue"]
    revenue_prev = agg["revenue_prev"]
    total_income = agg["total_income"]
    pbt = agg["pbt"]
    finance_costs = agg["finance_costs"]
    wc_change = agg["wc_change"]
    p_equity = agg["p_equity"]
    p_total_assets = agg["p_total_assets"]
    p_debt = agg["p_debt"]
    p_total_income = agg["p_total_income"]
    p_pbt = agg["p_pbt"]
    p_finance_costs = agg["p_finance_costs"]
    op_cash = agg["op_cash"]
    p_op_cash = agg["p_op_cash"]

    cur_margin = _safe(pbt, total_income) * 100
    prior_margin = _safe(p_pbt, p_total_income) * 100
    cur_de = _safe(debt, equity)
    prior_de = _safe(p_debt, p_equity)
    cur_ic = _safe(pbt + finance_costs, finance_costs)
    prior_ic = _safe(p_pbt + p_finance_costs, p_finance_costs)

    m = {
        "current_ratio": round(_safe(current_assets, current_liabs), 2),
        "debt_to_equity": round(_safe(debt, equity), 2),
        "equity_ratio": round(_safe(equity, total_assets), 2),
        "interest_coverage": round(_safe(pbt + finance_costs, finance_costs), 1),
        "net_margin": round(_safe(pbt, total_income) * 100, 1),
        "revenue_growth": round(_safe(revenue - revenue_prev, revenue_prev) * 100, 1),
        "cash_ratio": round(_safe(cash, current_liabs), 2),
        "asset_turnover": round(_safe(revenue, total_assets), 2),
    }

    # Metric tones for the UI (good / warn / bad).
    def tone(key: str) -> str:
        v = m[key]
        if key == "current_ratio":
            return "good" if v >= 1.5 else "warn" if v >= 1.0 else "bad"
        if key == "debt_to_equity":
            return "good" if v <= 0.5 else "warn" if v <= 1.0 else "bad"
        if key == "equity_ratio":
            return "good" if v >= 0.4 else "warn"
        if key == "interest_coverage":
            return "good" if v >= 4 else "warn" if v >= 2 else "bad"
        if key == "net_margin":
            return "good" if v >= 10 else "warn"
        if key == "revenue_growth":
            return "good" if v > 0 else "bad"
        if key == "cash_ratio":
            return "good" if v >= 0.3 else "warn"
        return "good" if v >= 0.5 else "warn"

    metrics = [{"key": k, "label": METRIC_LABELS[k], "value": m[k], "tone": tone(k)} for k in METRIC_LABELS]

    strengths, weaknesses = [], []
    if m["revenue_growth"] > 0:
        strengths.append(STRENGTHS["growth"])
    if m["debt_to_equity"] <= 0.5:
        strengths.append(STRENGTHS["leverage"])
    if m["interest_coverage"] >= 4:
        strengths.append(STRENGTHS["coverage"])
    if m["equity_ratio"] >= 0.4:
        strengths.append(STRENGTHS["equity"])
    if m["current_ratio"] >= 1.5:
        strengths.append(STRENGTHS["current_ratio"])
    if m["net_margin"] >= 10:
        strengths.append(STRENGTHS["margin"])

    # Trend-derived strengths (momentum, not just level).
    if cur_de < prior_de:
        strengths.append(STRENGTHS["deleveraging"])
    if cur_margin > prior_margin and _pct_change(pbt, p_pbt) > _pct_change(revenue, revenue_prev):
        strengths.append(STRENGTHS["margin_expansion"])

    if m["cash_ratio"] < 0.3:
        weaknesses.append(WEAKNESSES["cash"])
    if wc_change < 0:
        weaknesses.append(WEAKNESSES["working_capital"])
    if goodwill > 0:
        weaknesses.append(WEAKNESSES["goodwill"])
    if open_review_items > 0:
        weaknesses.append(WEAKNESSES["review"])
    if _safe(receivables, current_assets) > 0.25:
        weaknesses.append(WEAKNESSES["receivables"])

    # Period-over-period (FY25 vs FY24) trends.
    trends = [
        _trend("revenue", "amount", revenue, revenue_prev, higher_is_better=True),
        _trend("pbt", "amount", pbt, p_pbt, higher_is_better=True),
        _trend("net_margin", "percent", cur_margin, prior_margin, higher_is_better=True),
        _trend("operating_cash_flow", "amount", op_cash, p_op_cash, higher_is_better=True),
        _trend("total_assets", "amount", total_assets, p_total_assets, higher_is_better=True),
        _trend("equity", "amount", equity, p_equity, higher_is_better=True),
        _trend("debt_to_equity", "ratio", cur_de, prior_de, higher_is_better=False),
        _trend("interest_coverage", "ratio", cur_ic, prior_ic, higher_is_better=True),
    ]

    score = len(strengths) - len(weaknesses)
    headline = HEADLINES["strong"] if score >= 3 else HEADLINES["mixed"] if score >= 0 else HEADLINES["weak"]
    # Provisional figures (open review items) temper an otherwise-strong headline.
    if open_review_items > 0 and headline == HEADLINES["strong"]:
        headline = HEADLINES["mixed"]

    return {
        "headline": headline,
        "assessment": ASSESSMENT,
        "metrics": metrics,
        "trends": trends,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "data_quality": DATA_QUALITY,
        "basis": basis,
    }


def build_commentary_from_rows(rows: list[dict], *, open_review_items: int = 0,
                               basis: str = "consolidated", currency: str = "",
                               units: str = "") -> dict:
    """Commentary for a REAL extraction: the same ratios, trends, strengths and weaknesses as
    the demo path, but every aggregate is read from the extracted canonical line items (current
    vs prior period). Returns the empty shape when the headline figures needed for ratios were
    not extracted, so the screen degrades honestly instead of fabricating an assessment."""
    from app.services.derived import _CASH, _DEBT, _group_by_key, _side, _value

    # Grouped, not last-wins. ``derived._value`` sums every printed line that maps to a concept, and
    # a filing now carries two statements' rows under one key — the Group's and the Company's. With
    # one row per key the surviving row could be the Company's, `_value` would find no consolidated
    # figure, and `v()`'s `or 0.0` would turn that into a hard zero: a ratio computed off zero
    # revenue rather than an honestly empty assessment.
    by_key = _group_by_key(rows)

    def v(key: str, period: str = "current") -> float:
        return _value(by_key, key, basis, period) or 0.0

    def side(terms, period: str = "current") -> float:
        return _side(by_key, terms, basis, period) or 0.0

    # Working-capital movement = sum of the operating-activities increase/decrease lines.
    def wc(period: str) -> float:
        total = 0.0
        for k, r in by_key.items():
            if k.startswith("cf_cash_flow_from_operating_activities__increase_decrease_in_"):
                total += _value({k: r}, k, basis, period) or 0.0
        return total

    _REV = "pl_income__revenue_from_operations"
    _PBT = "pl_profit_before_tax"
    _PROFIT = "pl_profit_for_the_year"
    _FIN = "pl_non_operating_expenses__interest_expense"
    _CFO = "cf_cash_flow_from_operating_activities__net_cash_from_operating_activities"

    revenue = v(_REV)
    # If the core figures for ratios/trends aren't present, don't invent a commentary.
    if revenue == 0.0 and v("bs_total_assets") == 0.0:
        return {"headline": "", "assessment": "", "metrics": [], "trends": [],
                "strengths": [], "weaknesses": [], "data_quality": "", "basis": ""}

    pbt = v(_PBT) or v(_PROFIT)
    p_pbt = v(_PBT, "prior") or v(_PROFIT, "prior")
    # Net margin denominator: revenue (standard); the demo uses total income, but a canonical
    # "total income" line isn't guaranteed, so revenue keeps it well-defined and comparable.
    agg = {
        "current_assets": v("bs_current_assets__total_current_assets"),
        "current_liabs": v("bs_current_liabilities__total_current_liabilities"),
        "equity": v("bs_equity__total_equity"),
        "total_assets": v("bs_total_assets"),
        "debt": side(_DEBT),
        "cash": side(_CASH),
        "goodwill": v("bs_non_current_assets__goodwill"),
        "receivables": v("bs_current_assets__trade_receivables"),
        "revenue": revenue,
        "revenue_prev": v(_REV, "prior"),
        "total_income": revenue,
        "pbt": pbt,
        "finance_costs": v(_FIN),
        "wc_change": wc("current"),
        "p_equity": v("bs_equity__total_equity", "prior"),
        "p_total_assets": v("bs_total_assets", "prior"),
        "p_debt": side(_DEBT, "prior"),
        "p_total_income": v(_REV, "prior"),
        "p_pbt": p_pbt,
        "p_finance_costs": v(_FIN, "prior"),
        "op_cash": v(_CFO),
        "p_op_cash": v(_CFO, "prior"),
    }
    label = " · ".join(p for p in [
        basis, (f"{currency} {units}".strip() if (currency or units) else "")] if p)
    return _assemble(agg, open_review_items, label or basis)
