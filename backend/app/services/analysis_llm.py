"""LLM-backed financial analysis — the structured schema, prompt, and runner used by
the ``POST /projects/{id}/analysis`` endpoint.

Feeds the project's extracted line items to the configured LLM provider (Anthropic or
any OpenAI-compatible gateway — see app/adapters) and asks for canonical mappings plus
ratios and a one-page commentary, validated against :class:`Result`. Returns the parsed
result together with the provider's token-usage meta so the run can be written to the
audit log. Mirrors scripts/live_analysis.py, but as an importable service the API uses.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.ports.llm import LlmMeta, LlmProvider
from app.sample.demo import BALANCE_SHEET, CASH_FLOW, DEMO, PROFIT_AND_LOSS

_STMT_HINTS = (
    (PROFIT_AND_LOSS, "profit_and_loss"),
    (BALANCE_SHEET, "balance_sheet"),
    (CASH_FLOW, "cash_flow"),
)


class Mapping(BaseModel):
    source_label: str
    canonical_key: str = Field(description="snake_case canonical template key, e.g. revenue_from_operations")
    statement: str = Field(description="balance_sheet | profit_and_loss | cash_flow")
    sign_convention: str = Field(description="as_reported | expense_negative | contra_negative")
    confidence: float = Field(ge=0, le=1)


class Ratio(BaseModel):
    name: str
    value: float
    unit: str = Field(description="x | % | ratio")
    basis: str = Field(description="the arithmetic used, e.g. 'PAT / revenue * 100'")
    assessment: str = Field(description="good | watch | risk")


class Analysis(BaseModel):
    headline: str
    ratios: list[Ratio]
    revenue_growth_pct: float
    profit_growth_pct: float
    strengths: list[str]
    risks: list[str]
    caveats: str


class Result(BaseModel):
    entity: str
    mappings: list[Mapping]
    analysis: Analysis


SYSTEM = (
    "You are a financial-statement extraction and analysis engine for Ind-AS / IFRS "
    "filings. You are given an entity's raw line-item captions and values for a period "
    "and its prior period. Do two things:\n"
    "1) For every line item, map the raw caption to a canonical snake_case key, its "
    "statement, and its sign convention.\n"
    "2) Compute standard ratios (net margin, revenue growth YoY, current ratio, "
    "return on equity, and any others that are well-supported) and write a concise "
    "one-page analysis with a headline, strengths and risks.\n"
    "Use ONLY the figures provided — never invent line items or numbers. If a ratio "
    "cannot be computed from the given figures, omit it. Values are in the stated "
    "currency and units."
)


def build_demo_payload() -> dict:
    """Assemble the analysis input from the seeded demo project's statements."""
    proj = DEMO["project"]
    line_items = []
    for rows, hint in _STMT_HINTS:
        for r in rows:
            if r.get("kind") == "item" and (r.get("v1") is not None or r.get("v2") is not None):
                line_items.append({
                    "raw_label": r["label"],
                    "statement_hint": hint,
                    "v1": r.get("v1"),
                    "v2": r.get("v2"),
                })
    return {
        "entity": proj["entity"],
        "basis": "consolidated",
        "standard": proj.get("standard", "Ind-AS"),
        "currency": proj.get("currency", "INR"),
        "units": proj.get("units", "crore"),
        "period": "FY2025",
        "prior_period": "FY2024",
        "line_items": line_items,
    }


def build_user_message(data: dict) -> str:
    return (
        "Extract and analyse the following. Column v1 is the current period "
        f"({data.get('period')}), v2 the prior period ({data.get('prior_period')}).\n\n"
        + json.dumps(data, indent=2)
    )


def run_analysis(provider: LlmProvider, data: dict, *, max_tokens: int = 4096) -> tuple[Result, LlmMeta]:
    """Call the provider for a structured analysis; returns (result, token-usage meta)."""
    messages = [{"role": "user", "content": build_user_message(data)}]
    result, meta = provider.complete_structured(
        system=SYSTEM, messages=messages, response_schema=Result, max_tokens=max_tokens,
    )
    return result, meta  # type: ignore[return-value]


# --- Credit-narrative LLM pass ----------------------------------------------------------
# A short qualitative narrative that RATIONALISES the already-computed deterministic credit
# view (stance + rating factors + report signals). The model is told to ground itself only in
# the supplied factors/flags and never invent figures — the numbers stay deterministic; the
# LLM only writes the prose. Off unless a real provider is configured (see the endpoint).

class CreditNarrative(BaseModel):
    narrative: str = Field(description="A concise 3–5 sentence credit narrative grounded ONLY "
                                       "in the supplied factors and report signals. No new numbers.")


_LOCALE_NAME = {"en": "English", "zh": "Simplified Chinese", "ar": "Arabic", "fr": "French"}

CREDIT_SYSTEM = (
    "You are a credit analyst. You are given a company's already-computed credit assessment: "
    "an overall stance, a set of rating factors (each with a value and a strong/adequate/weak "
    "rating), and narrative signals scanned from its annual report (e.g. going concern, "
    "qualified audit opinion, contingent liabilities, guarantees, litigation).\n"
    "Write a concise 3–5 sentence credit narrative that explains the assessment: lead with the "
    "overall stance, cite the main supporting and constraining factors by name, and call out any "
    "report signals and their implication for creditworthiness.\n"
    "Use ONLY the figures and facts provided — never invent line items, numbers, or events. Do "
    "not give investment advice. Write plainly and objectively."
)


def build_credit_payload(credit: dict, *, entity: str = "", locale: str = "en") -> dict:
    return {
        "entity": entity or "the company",
        "output_language": _LOCALE_NAME.get(locale, "English"),
        "overall_stance": credit.get("stance_label"),
        "deterministic_summary": credit.get("summary"),
        "rating_factors": [
            {"category": f.get("category"), "metric": f.get("label"),
             "value": f.get("display"), "rating": f.get("tone_label")}
            for f in credit.get("factors", [])
        ],
        "report_signals": [
            {"signal": fl.get("label"), "implication": fl.get("implication"),
             "report_page": fl.get("page")}
            for fl in credit.get("flags", [])
        ],
    }


class NettingDecision(BaseModel):
    applies: bool = Field(description="True only if the statement clearly shows the target line is "
                                      "reported inclusive of the named candidate lines")
    subtract_keys: list[str] = Field(default_factory=list,
                                     description="candidate keys the target INCLUDES (subset of the input candidates)")
    add_keys: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


NETTING_SYSTEM = (
    "You are a financial-statement analyst applying a containment-netting policy. A policy proposes "
    "that a TARGET line may be reported INCLUSIVE of other lines; if so, the clean figure nets those "
    "lines out. You are given the target line, a set of CANDIDATE lines (each with its label and "
    "value as actually extracted), and a natural-language condition.\n"
    "Decide whether, for THIS statement, the target is genuinely reported inclusive of each candidate. "
    "Choose keys ONLY from the provided candidates. Be conservative: if there is no clear evidence in "
    "the labels/values/condition that the target contains a candidate, do NOT include it. If none "
    "apply, return applies=false with an empty list. Never guess or invent keys or numbers."
)


def run_netting_evaluation(provider: LlmProvider, payload: dict, *,
                           max_tokens: int = 400) -> tuple[NettingDecision, LlmMeta]:
    """Ask the provider whether a containment-netting policy applies to THIS statement, and which
    candidate lines are actually contained. Returns (decision, token-usage meta)."""
    result, meta = provider.complete_structured(
        system=NETTING_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)}],
        response_schema=NettingDecision, max_tokens=max_tokens,
    )
    return result, meta  # type: ignore[return-value]


def credit_figures_digest(credit: dict) -> str:
    """A digest of the figures a credit narrative is written from (``build_credit_payload``): the
    stance, each rating factor's figure and rating, each report signal and its page.

    Stored with the narrative, so a reader can tell whether the figures it describes are still the
    ones on screen. Labels and sentences are left out — a factor's ``display`` is the figure itself
    ("1.42×") in every locale — so the same figures read in another language give the same digest.
    """
    import hashlib

    basis = {"basis": credit.get("basis"), "stance": credit.get("stance"),
             "factors": [[f.get("category_key"), f.get("key"), f.get("display"), f.get("tone")]
                         for f in credit.get("factors") or []],
             "flags": [[fl.get("key"), fl.get("severity"), fl.get("page")]
                       for fl in credit.get("flags") or []]}
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def run_credit_narrative(provider: LlmProvider, credit: dict, *, entity: str = "",
                         locale: str = "en", max_tokens: int = 700) -> tuple[CreditNarrative, LlmMeta]:
    """Call the provider for a grounded credit narrative; returns (narrative, token-usage meta)."""
    payload = build_credit_payload(credit, entity=entity, locale=locale)
    messages = [{"role": "user", "content":
                 "Write the credit narrative for the following assessment. Respond in "
                 f"{payload['output_language']}.\n\n" + json.dumps(payload, indent=2, ensure_ascii=False)}]
    result, meta = provider.complete_structured(
        system=CREDIT_SYSTEM, messages=messages, response_schema=CreditNarrative,
        max_tokens=max_tokens,
    )
    return result, meta  # type: ignore[return-value]
