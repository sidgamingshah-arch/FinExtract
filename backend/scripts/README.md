# Scripts

## `run_filing.py` — one filing through the real pipeline, headless

Triage a new filing with no browser and no dev server. Drives the API in process
(`POST /documents` → `POST /documents/{id}/extractions` → poll `run-status` → read `/run` and
`/extractions/{run_id}`), so it is the same code path an upload takes rather than a second
spelling of it. Its own scratch database and object store, so a triage run never touches
`backend/finex.db` and never inherits the rulebook versions a previous run left in force.

```bash
cd backend
python scripts/run_filing.py /path/to/filing.pdf --no-llm --out _scratch/run
```

`--no-llm` is the deterministic route: the stub provider, no network call, and the run reports
`strategy: deterministic` so a degraded run can never be mistaken for a full-capability one. It is
the same switch `tests/conftest.py` uses, and it is NOT the same as turning the LLM tiers off —
measured, that is worse, because a dozen behaviours exist to consult that path.

Four files land in `--out`:

| File | What it is |
| --- | --- |
| `<stem>__result.json` | the complete result the API would serve — every row, value, provenance, note |
| `<stem>__run.log` | that run's stage-by-stage log, written even when the run FAILED |
| `<stem>__summary.json` | what mapped and what did not, plus the rulebook the run **recorded** beside the one this script asked for |
| `<stem>__unmapped.csv` | every face caption that reached no concept, with its printed page |

`__unmapped.csv` is the triage artefact. A deterministic run's characteristic failure is not a
wrong number but a caption nothing claimed, and `rulebook_recorded` differing from
`rulebook_requested` explains a wrong figure on its own.

## `live_analysis.py` — real Claude extraction + analysis for one entity

Feeds an entity's line items to Claude through the project's real `AnthropicLlmProvider`
and asks it to (1) map each raw source caption to a canonical Ind-AS/IFRS key + statement
+ sign convention, and (2) compute ratios and a one-page financial-analysis commentary
grounded only in the supplied figures. Prints the Anthropic `request_id` and token usage
as proof of a genuine call.

```bash
cd backend
pip install -e ".[llm]"          # installs the anthropic SDK

# 1) Dry run — prints the exact request that WOULD be sent (no key needed):
python scripts/live_analysis.py --dry-run

# 2) Live call — set the key named in config.toml [llm].api_key_env (default below):
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/live_analysis.py --data scripts/sample_data/infosys_fy24.json

# Point at your own extracted data, or override the model:
python scripts/live_analysis.py --data mydata.json --model claude-opus-4-8
```

The model, timeout and endpoint come from `config.toml` `[llm]`; the API key is read at
call time from the environment (never stored in config). The bundled
`sample_data/infosys_fy24.json` holds **approximate, publicly-reported** figures purely
as a realistic input — in the real pipeline these are replaced by values extracted from
the uploaded filing; verify against the official report before relying on any number.

`--dry-run` and the adapter's parsing/meta handling are covered by
`tests/test_anthropic_llm.py` (no network required).
