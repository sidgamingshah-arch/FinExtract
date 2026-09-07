"""Prove the configured LLM provider actually answers, through the app's own adapter.

WHY A SCRIPT AND NOT A TEST. `key_configured: True` on the settings endpoint means only that the
environment variable is non-empty — it says nothing about whether the key is valid, whether the
configured model exists on that provider, or whether the endpoint is reachable through the
corporate proxy. Those three fail differently and all of them surface, today, as an extraction
that runs to completion with LLM-dependent figures quietly missing.

This sends the key to the provider named by ``llm.base_url`` and nowhere else, asks for the
smallest possible structured answer, and prints what came back. No key is ever printed.

Run from ``backend``:  ../.venv/Scripts/python.exe scripts/probe_llm.py
"""
from __future__ import annotations

import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

from pydantic import BaseModel, Field  # noqa: E402

from app.adapters.openai_llm import OpenAiLlmProvider  # noqa: E402
from app.config import get_settings  # noqa: E402



class Answer(BaseModel):
    """Deliberately trivial: the point is the round trip, not the reasoning."""

    statement: str = Field(description="one of: balance_sheet, profit_and_loss, cash_flow")


def main() -> int:
    s = get_settings()
    print(f"  provider   {s.llm.provider}")
    print(f"  endpoint   {s.llm.base_url}")
    print(f"  model      {s.llm.model}")
    print(f"  key from   {s.llm.api_key_env}")

    provider = OpenAiLlmProvider(s)
    try:
        key = provider._api_key()
    except Exception as exc:
        print(f"\n  NO KEY: {exc}")
        return 2
    print(f"  key        present, {len(key)} chars — value never printed")

    print("\n  calling the provider…")
    try:
        answer, meta = provider.complete_structured(
            system="You classify financial statement captions. Answer with the schema only.",
            messages=[{"role": "user",
                       "content": "Which statement is 'Trade and other receivables' on?"}],
            response_schema=Answer,
            max_tokens=256,
        )
    except Exception as exc:
        # A wrong key, a missing model and a blocked endpoint all land here and read differently,
        # which is the whole reason to print the raw message rather than a tidy summary.
        print(f"\n  CALL FAILED: {type(exc).__name__}: {exc}")
        return 1

    print(f"  answered   {answer.statement!r}")
    for field in ("model", "input_tokens", "output_tokens", "confidence"):
        if (v := (meta or {}).get(field)) is not None:
            print(f"  {field:<18} {v}")
    print("\n  the provider is reachable and the key is accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
