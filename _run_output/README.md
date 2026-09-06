# Committed run output

Real extraction output, checked in so it can be read on a machine that has never run the pipeline
and has no database. `runs_index.json` lists every run here with its entity, locale, units and
mapping strategy — start there.

These are artifacts, not fixtures: no test reads them, and re-running does not have to reproduce
them byte for byte.

## What is here

| File | What it is |
| --- | --- |
| `runs_index.json` | One entry per run: id, status, entity, locale, units, mapping, row count. |
| `<run>__result.json` | The **complete** result the API would serve — every row, value, provenance, note, reconciliation entry. Enough to inspect a run with no DB. |
| `<run>__focus_items.json` | Just the eight focus concepts, each with its values, flags and full derivation. This is the review view. |
| `<run>__run.log` | That run's own stage-by-stage log, including the `focus_routing` line. |
| `LaiSun_FY2025_statements.xlsx` | Statement-level workbook: every template line, both periods, per basis. |
| `LaiSun_FY2025_rows.xlsx` | Row-level detail: each extracted caption, the concept it reached, and how. |
| `focus_comparison.json` | The Lai Sun eight-item comparison against the supplied reference. |

## The runs

**`LaiSun_FY2025_en`** — Lai Sun Garment (International) Limited, year ended 2025-07-31. 367 pages,
HKEX, English. `HKD` / thousands. **8/8 agreement** on the focus items:
529,841 · 57,576 · 174,822 · 916,919 · 1,310,743 · null · null · 4,995,768.

**`SunCreate_FY2024_zh`** — Sun Create Electronics Co.,Ltd (安徽四创电子), FY2024. 210 pages, CSRC,
Simplified Chinese (`locale=zh`). This one is **not** clean, and the known-wrong items are recorded
here deliberately rather than quietly omitted:

| Item | This run | Correct (PDF-verified) |
| --- | --- | --- |
| Due from Related Parties (LTP) | `null` | **532,030.87** |
| Other Receivables (CP) | 254,937,045.24 | **131,419,251.75** |
| Main Business Revenue | 1,603,146,551.95 | **1,589,859,743.31** |
| Contingent Liabilities | `"No"` | **118,754,500.00** |

Contingent liabilities is **fixed in code** since this run (the run predates the fix) — a fresh run
produces 118,754,500.00. The other three share one unfixed root defect: a note's column grid has no
*measure* axis, so a two-level PRC header (本期/上期 × 收入/成本, 期末/期初 × 账面余额/坏账准备)
collapses into positional `current`/`prior`/`col2`/`col3`. A current-year **cost** therefore sits in
the `prior` slot on the revenue note, and a **provision** sits there on the related-party note. An
unfinished implementation of that fix is preserved at `_wip/measure-axis-unfinished.patch`.

## Reproducing on another machine

In git, so it travels: the code, `backend/config.toml`, and the rulebook —
`backend/app/sample/templates/output_csv_hk_ontology.json`. `app/sample/reference.py` re-seeds the
ontology from that file at startup, so rulebook edits need no database.

Not in git, by intent:

* **`backend/finex.db`** (`*.db`) — run history and uploaded documents. A 38 MB binary that changes
  every run would be stored in full in every future commit, and nothing in it is needed to read the
  results above or to reproduce a run.
* **`_object_store/`** — the uploaded PDFs, content-addressed. Re-upload through the frontend.
* **the provider key** — read at call time from the env var named by `llm.api_key_env`;
  `config.toml` stores that variable's NAME only.

So on a fresh machine: install, start the backend, upload the filing, flip `llm_focus_only` on in
Settings if you want the focused run, and extract. `<run>__focus_items.json` is what to check the
result against.

**One thing that does not travel:** `llm_focus_only` lives in the database (`setting_overrides`),
not git, so a new machine starts from `config.toml`'s `false`. Turn it on in Settings there, or set
`llm_focus_only = true` in `config.toml` before pushing if it should be on everywhere.
