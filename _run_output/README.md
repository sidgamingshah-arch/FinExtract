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

Both are LLM-backed (`strategy: llm_description`), not deterministic fallbacks — check
`mapping.llm_calls` in `runs_index.json` if in doubt, because a run with no provider resolved still
succeeds and simply maps far fewer rows.

**`LaiSun_FY2025_en`** — Lai Sun Garment (International) Limited, year ended 2025-07-31. 367 pages,
HKEX, English, `HKD` / thousands. 7 LLM calls. **8/8 on the focus items:**

| Item | Value |
| --- | --- |
| Deprec & Impairment (Oper Exp) | 529,841 |
| Deprec & Impairment (COS) | 57,576 |
| Secur & Other Fincl Assets (CP) | 0 |
| Secur & Other Fincl Assets (LTP) | 628,486 |
| Contingent Liabilities | 1,310,743 |
| Due from Related Parties (LTP) | null |
| Other Receivables (CP) | null |
| Sales (Revenues) | 4,995,768 |

CP is 0 and LTP is 628,486 because Find_3 (the Level 3 fair-value column, 463,255) exceeds CP's
Find_1 of 174,822; the 288,433 residual carries into LTP. `__focus_items.json` holds the full
derivation for each, and its contributions now sum to the published figure.

**`SunCreate_FY2024_zh`** — Sun Create Electronics Co.,Ltd (安徽四创电子), FY2024. 210 pages, CSRC,
Simplified Chinese, `CNY`. 8 LLM calls. **3/4 — one item is still wrong, and knowingly so:**

| Item | This run | Correct |
| --- | --- | --- |
| Due from Related Parties (LTP) | **532,030.87** | ✓ |
| Other Receivables (CP) | **131,419,251.75** | ✓ |
| Contingent Liabilities | **118,754,500.00** | ✓ |
| Main Business Revenue | 1,603,146,551.95 | **1,589,859,743.31** |

That last one regresses ONLY when the LLM is enabled, which is why it must be recorded here rather
than assumed fixed: measured with `llm_mapping=false` it produces 1,589,859,743.31 correctly.

The cause is known and unfixed. The concept wants the 主营业务 row of the 营业收入 note;
`spec_alias_curation.py` denies the face caption `^营业收入$` outright ("§4: Do not use total
营业收入 as a fallback"). But that denial filters ALIAS LISTS and is never applied to an
LLM-proposed binding, so the model bound `其中：营业收入` — the face TOTAL — and its own recorded
reason quotes the right figure as justification for the wrong row:

    llm_reason: Operating revenue detail (其中：营业收入) supported by note 61 showing
                主营业务 1,589,859,743.31. Matches sales_revenues definition

`stages/sales_revenues.py` then skips any slot already filled, so the correct value it computed was
discarded. Two changes are needed: enforce the spec's alias denials against LLM bindings, and let a
spec-derived figure displace a weaker binding rather than yield to it.

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
