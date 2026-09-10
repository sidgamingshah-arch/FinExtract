#!/usr/bin/env python
"""Which of the configured providers can actually serve ONE REAL mapping request.

WHY THIS EXISTS. Groq's free tier refuses every request this pipeline makes: its limit is 8,000
tokens per minute and a mapping call is roughly 30,000-40,000, so `llm_calls` comes back 0 and the
run degrades. That is a property of the TIER, not of the pipeline, and the fix is to send the request
somewhere that accepts it. Three keys are configured (Groq, Gemini, OpenRouter), all three speak the
OpenAI-compatible protocol the `openai_compatible` adapter already implements, so choosing between
them is configuration — but only a real request proves which one works.

So this replays a REAL captured request (system prompt + user message, verbatim, from
`capture_real_request.py`) against each candidate and reports what came back. A synthetic
"hello world" probe would prove auth and nothing else: the whole question is whether a ~30k-token
request with a JSON-mode response is accepted.

    python scripts/probe_providers.py ../_run8/REQ_laisun_fixed.json
    python scripts/probe_providers.py ../_run8/REQ_laisun_fixed.json --list-openrouter

Keys are read from the gitignored `.env` into this process only. Nothing is written anywhere.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ENV = pathlib.Path(__file__).resolve().parent.parent / ".env"


def load_env() -> None:
    for line in ENV.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        import os
        os.environ[key.strip()] = value.strip()


# base_url / api_key_env / model. All three are OpenAI-compatible, which is why one adapter serves
# them; Gemini exposes its compatibility layer under /v1beta/openai/.
_GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"
CANDIDATES = [
    ("groq / gpt-oss-120b", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
     "openai/gpt-oss-120b"),
    # Model names matter more than they look: `gemini-2.5-flash` and `gemini-2.0-flash` are BOTH
    # rejected 404 by the compatibility layer for this key ("no longer available to new users"),
    # even though the native `v1beta/models` listing still returns 2.5-flash. So the candidates are
    # taken from that listing, newest first, and the `-latest` alias is included because it is the
    # one name that does not rot.
    ("gemini / flash-latest", _GEMINI, "GEMINI_API_KEY", "gemini-flash-latest"),
    ("gemini / 3.5-flash", _GEMINI, "GEMINI_API_KEY", "gemini-3.5-flash"),
    ("gemini / 3-flash-preview", _GEMINI, "GEMINI_API_KEY", "gemini-3-flash-preview"),
    ("gemini / flash-lite-latest", _GEMINI, "GEMINI_API_KEY", "gemini-flash-lite-latest"),
    ("openrouter / minimax-m2", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
     "minimax/minimax-m2"),
]


def list_openrouter() -> int:
    import os
    import urllib.request
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": "Bearer " + os.environ.get("OPENROUTER_API_KEY", "")})
    data = json.loads(urllib.request.urlopen(req, timeout=90).read())
    models = data.get("data") or []
    print(f"{len(models)} models on OpenRouter")

    def rows(pred, label):
        print(f"\n{label}")
        found = [m for m in models if pred(m)]
        for m in sorted(found, key=lambda m: -(m.get("context_length") or 0))[:14]:
            price = (m.get("pricing") or {}).get("prompt")
            print(f"   {m['id']:54s} ctx={m.get('context_length'):>9}  prompt/token={price}")
        if not found:
            print("   (none)")

    rows(lambda m: "minimax" in m["id"].lower(), "MINIMAX:")
    rows(lambda m: _price(m) == 0.0 and (m.get("context_length") or 0) >= 60000,
         "FREE, context >= 60k:")
    return 0


def _price(m) -> float:
    try:
        return float((m.get("pricing") or {}).get("prompt") or 1.0)
    except (TypeError, ValueError):
        return 1.0


def probe(captured: dict) -> int:
    from app.adapters.openai_llm import OpenAiLlmProvider
    from app.config import get_settings
    from app.services.mapping import LlmBatchDecision

    system = captured["system_prompt"]
    user = json.dumps(captured["user_message"], ensure_ascii=False)
    rows = len(captured["user_message"].get("source_items") or [])
    approx = round((len(system) + len(user)) / 4)
    print(f"replaying a real request: {rows} rows, {len(system) + len(user):,} chars "
          f"~= {approx:,} tokens\n")
    print(f"  {'candidate':26s} {'result':>10s}  detail")

    settings = get_settings()
    ok = []
    for label, base_url, key_env, model in CANDIDATES:
        import os
        if not os.environ.get(key_env):
            print(f"  {label:26s} {'SKIP':>10s}  {key_env} not set")
            continue
        settings.llm.base_url = base_url
        settings.llm.api_key_env = key_env
        settings.llm.model = model
        provider = OpenAiLlmProvider(settings=settings)
        began = time.time()
        try:
            out, meta = provider.complete_structured(
                system=system, messages=[{"role": "user", "content": user}],
                response_schema=LlmBatchDecision, max_tokens=2048)
            took = time.time() - began
            n = len(out.mappings)
            named = sum(1 for m in out.mappings if (m.canonical_key or "").strip())
            print(f"  {label:26s} {'OK':>10s}  {took:5.1f}s  {n} decisions "
                  f"({named} named)  in={meta.get('input_tokens')} "
                  f"out={meta.get('output_tokens')}")
            ok.append((label, base_url, key_env, model))
        except Exception as exc:  # noqa: BLE001
            took = time.time() - began
            msg = str(exc).replace("\n", " ")
            print(f"  {label:26s} {'FAIL':>10s}  {took:5.1f}s  {type(exc).__name__}: {msg[:150]}")

    print()
    if ok:
        print("USABLE:")
        for label, base_url, key_env, model in ok:
            print(f"   {label}")
            print(f"      FINEX_LLM__BASE_URL={base_url}")
            print(f"      FINEX_LLM__API_KEY_ENV={key_env}")
            print(f"      FINEX_LLM__MODEL={model}")
    else:
        print("NONE of the candidates accepted a real request.")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("captured", nargs="?", help="JSON from capture_real_request.py")
    ap.add_argument("--list-openrouter", action="store_true")
    args = ap.parse_args()
    load_env()
    if args.list_openrouter:
        return list_openrouter()
    if not args.captured:
        ap.error("a captured request is required unless --list-openrouter")
    return probe(json.loads(pathlib.Path(args.captured).read_text(encoding="utf-8")))


if __name__ == "__main__":
    raise SystemExit(main())
