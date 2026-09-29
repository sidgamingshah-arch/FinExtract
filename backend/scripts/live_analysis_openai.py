#!/usr/bin/env python3
"""Live LLM extraction + analysis via an OpenAI-compatible gateway.

Same job as ``live_analysis.py`` (map raw captions -> canonical keys; compute ratios
and a one-page commentary), but speaks the **OpenAI Chat Completions** wire format
(``POST {base_url}/chat/completions``) instead of the Anthropic Messages API. Use this
against gateways like TokenRouter / OpenRouter and models such as
``moonshotai/kimi-k3-free`` — anything whose ``base_url`` ends in ``/v1`` and serves
``vendor/model``-style ids.

It reuses the exact schema, system prompt and payload builder from ``live_analysis``,
so the two runners stay in lockstep; only the transport differs. No SDK required —
just ``httpx`` (already a dependency).

Config is read from config.toml [llm] — the only place the LLM is defined:
  base_url              e.g. https://api.tokenrouter.com/v1
  model                 e.g. moonshotai/kimi-k3-free
  <api_key_env>         the key, from the env var named by [llm].api_key_env

Usage:
    # dry run — prints the exact request that WOULD be sent (no key/network needed):
    python scripts/live_analysis_openai.py --dry-run

    # live call:
    python scripts/live_analysis_openai.py --data scripts/sample_data/infosys_fy24.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Make both the backend package and this scripts/ dir importable from anywhere.
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.config import get_settings  # noqa: E402
# Reuse the identical schema, system prompt and payload builder.
from live_analysis import (  # noqa: E402
    DEFAULT_DATA,
    SYSTEM,
    Result,
    build_user_message,
)


def _schema_instruction(response_schema: type[BaseModel]) -> str:
    """Same structured-output instruction the Anthropic adapter uses."""
    schema = json.dumps(response_schema.model_json_schema(), indent=2)
    return (
        "Respond with a single JSON object and NOTHING else — no prose, no code "
        "fences, no explanation before or after. The object MUST validate against "
        "this JSON Schema:\n\n" + schema
    )


def _strip_fences(text: str) -> str:
    """Tolerate a model that wraps JSON in ```json fences despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def build_body(model: str, system: str, user: str, max_tokens: int, json_mode: bool) -> dict:
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def main() -> int:
    ap = argparse.ArgumentParser(description="Live LLM extraction + analysis via an OpenAI-compatible gateway.")
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="Path to the input JSON.")
    ap.add_argument("--model", default=None, help="Override the configured model id.")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--no-json-mode", action="store_true",
                    help="Do not send response_format=json_object (for gateways that reject it).")
    ap.add_argument("--dry-run", action="store_true", help="Print the request without calling the API.")
    args = ap.parse_args()

    data = json.loads(args.data.read_text())
    settings = get_settings()
    model = args.model or settings.llm.model
    base_url = settings.llm.base_url or "https://api.openai.com/v1"
    key_env = settings.llm.api_key_env

    full_system = f"{SYSTEM}\n\n{_schema_instruction(Result)}"
    user_msg = build_user_message(data)
    json_mode = not args.no_json_mode
    body = build_body(model, full_system, user_msg, args.max_tokens, json_mode)

    if args.dry_run:
        print("=== DRY RUN — exact request that would be sent ===")
        print(f"POST         : {_endpoint(base_url)}")
        print(f"model        : {model}")
        print(f"max_tokens   : {args.max_tokens}")
        print(f"json_mode    : {json_mode}")
        print(f"api_key_env  : {key_env} (set this to make the live call)")
        print("\n--- body ---\n" + json.dumps(body, indent=2))
        return 0

    api_key = os.environ.get(key_env)
    if not api_key:
        print(f"ERROR: No API key found. Set the {key_env} environment variable "
              f"(configured via config.toml [llm].api_key_env).", file=sys.stderr)
        print("Tip: run with --dry-run to see the exact request without a key.", file=sys.stderr)
        return 2

    entity = data.get("entity", "the entity")
    print(f"Calling {model} at {base_url} for a live extraction + analysis of {entity} …\n")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=settings.llm.timeout_seconds) as client:
            resp = client.post(_endpoint(base_url), headers=headers, json=body)
            # Some gateways reject response_format; retry once without it.
            if resp.status_code == 400 and json_mode and "response_format" in resp.text.lower():
                print("Gateway rejected response_format; retrying without JSON mode …", file=sys.stderr)
                body = build_body(model, full_system, user_msg, args.max_tokens, json_mode=False)
                resp = client.post(_endpoint(base_url), headers=headers, json=body)
            # GPT-5-family models rename the output cap; retry once with the new key.
            if resp.status_code == 400 and "max_completion_tokens" in resp.text.lower():
                print("Gateway wants max_completion_tokens; retrying with the renamed key …", file=sys.stderr)
                body["max_completion_tokens"] = body.pop("max_tokens")
                resp = client.post(_endpoint(base_url), headers=headers, json=body)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        print(f"ERROR: gateway returned {exc.response.status_code}: "
              f"{exc.response.text[:500]}", file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"ERROR: request failed: {exc}", file=sys.stderr)
        return 2

    payload = resp.json()
    content = payload["choices"][0]["message"]["content"]
    parsed = Result.model_validate_json(_strip_fences(content))
    usage = payload.get("usage", {})

    print("=== LIVE CALL SUCCEEDED ===")
    print(f"model         : {payload.get('model', model)}")
    print(f"response_id   : {payload.get('id')}")
    print(f"request_id    : {resp.headers.get('x-request-id') or resp.headers.get('x-tokenrouter-request-id')}")
    print(f"prompt_tokens : {usage.get('prompt_tokens')}")
    print(f"output_tokens : {usage.get('completion_tokens')}")
    print("\n=== STRUCTURED RESULT ===")
    print(parsed.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
