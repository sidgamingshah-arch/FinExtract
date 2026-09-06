# Committed run output

The output of one real extraction, checked in so it can be read on a machine that has not run the
pipeline. These are artifacts, not fixtures — no test reads them, and re-running does not have to
reproduce them byte for byte.

| File | What it is |
| --- | --- |
| `LaiSun_FY2025_statements.xlsx` | The statement-level output: every template line, both periods, per basis. |
| `LaiSun_FY2025_rows.xlsx` | The row-level detail behind it — each extracted caption, the concept it reached, and how. |
| `focus_comparison.json` | The eight focus concepts, each with our value, the reference value, the method that produced it, and its flags. |

## The run

* **Filing** — Lai Sun Garment (International) Limited, annual report, year ended 2025-07-31.
  367 pages, HKEX, English with a Traditional Chinese section.
* **Basis** — `consolidated`, template `HK000`.
* **Provider** — OpenRouter `minimax/minimax-m2.7:free`, with `llm.reasoning_max_tokens = 3000`.
  Focus routing was on: 45 rows went to the model, 240 were decided deterministically, and 14
  subsections were skipped outright. Seven provider calls, ~2 minutes wall clock.

## Reproducing it elsewhere

Everything the pipeline needs is in git **except the filing itself and the provider key**:

* the code, and `backend/config.toml`;
* the rulebook — `backend/app/sample/templates/output_csv_hk_ontology.json`. `app/sample/reference.py`
  re-seeds the ontology from that file at startup, so rulebook edits travel with the repo and do
  **not** need the database;
* `extraction.llm_focus_keys`, in `config.toml`. The switch that acts on them,
  `llm_focus_only`, ships **off** and is stored per-deployment in the database, not in git — so
  turn it on from the Settings screen on the new machine, or set `llm_focus_only = true` in
  `config.toml` before pushing if it should be on by default everywhere.

Not in git, by intent:

* **`backend/finex.db`** — run history, uploaded documents, and admin setting overrides. Ignored by
  `*.db`. A 37 MB binary that changes on every run would be stored in full in every future commit,
  and nothing in it is needed to reproduce a run: the rulebook comes from the seed above and the
  settings from `config.toml`.
* **`_object_store/`** — the uploaded PDF, content-addressed. Upload the filing again through the
  frontend on the new machine.
* **the provider key** — read at call time from the environment variable named by
  `llm.api_key_env`. `config.toml` stores that variable's NAME only.

So on a fresh machine: install, start the backend, upload the same PDF, flip `llm_focus_only` on in
Settings if you want the focused run, and extract. The eight figures in `focus_comparison.json` are
what to check the result against.
