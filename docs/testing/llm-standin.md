# Testing the model route without a model endpoint (the LLM stand-in)

The pipeline asks a model where a line item's figure is printed. Whether it can answer depends on
the context the request carries: which rows, from which page, labelled with which period, in which
unit. A badly shaped context loses the meaning before any model sees it. This tool lets you see every
request **exactly as the gateway would send it**, have someone answer it as the model would, judge
whether the context made it answerable, and compare the result with what the filing prints.

Everything is driven by one script, `backend/scripts/llm_standin.py`. It changes nothing in the app:
it registers its own model provider (`standin`) for the run.

## What you need

- The annual report as a PDF in `_filings/` (gitignored: filings are client documents).
- The template and line-item set to run, by file name under `backend/app/sample/templates/`.
- Optionally, a **key**: what the filing actually prints for each line (format below).

All outputs go to `_standin/<filing-stem>__<template>/` (gitignored).

## The procedure

Run from `backend/`.

1. **Capture.** Runs the pipeline; every model call is recorded and, having no answer yet, abstains.

       python scripts/llm_standin.py capture ../_filings/X.pdf \
           --template output_csv_icon_v1_template.json --set output_csv_icon_line_items.json

   `--focus all` asks about every line rather than the configured focus; `--out DIR` overrides the
   folder.
2. **Lint.** Mechanical checks of every request's context; no model involved. Prints a table.

       python scripts/llm_standin.py lint ../_standin/X__output_csv_icon_v1

3. **Sample.** Picks requests for the blind pass, spread over stages, statements and lint outcomes,
   always including some with no lint hit. Writes `sample.json`.

       python scripts/llm_standin.py sample ../_standin/X__output_csv_icon_v1 --n 12

4. **Answer blind.** For each sampled id, the answerer reads `requests/<id>.json` (the `body` field
   is exactly what the model receives) and writes two files: `answers/<id>.txt` (the reply the model
   would give) and `audit/<id>.json` (the judgement, below). See the brief.
5. **Validate.** Every answer parses with its request's schema exactly as the gateway parses a reply;
   every audit record and the key are well formed.

       python scripts/llm_standin.py validate DIR [--key KEY.json]

6. **Replay.** Re-runs with the answers. An answer can change a later stage's input, which then makes
   a new request; replay repeats until no new request is pending (at most 3 rounds) and prints the
   count each round. Answer the new ones and replay again if needed.

       python scripts/llm_standin.py replay ../_filings/X.pdf --template T --set S --out DIR

7. **Report.** Writes `report.md`.

       python scripts/llm_standin.py report DIR --key KEY.json

## What is in the output folder

| path | what it is |
| --- | --- |
| `requests/<id>.json` | one request: `id` (hash of the body), `stage` (the pipeline stage that made the call: `line_item_llm`, `shared_figures`, `gap_closing`, `contingent_liabilities`...), `schema` (reply schema name), `line_keys` (the keys asked about), `body` (exactly as sent), `chars`, `est_tokens` (chars/4) |
| `requests/<id>.md` | the same request rendered for people; the system prompt is written once to `system/<hash>.md` and linked, not repeated. The answerer reads the `.json`. |
| `run/` | the run's own output: `spread.json`, `rows.json` (with provenance), `notes.json`, `pages.json`, `structural.json`, `log.txt` |
| `index.json` | every request of the latest run: stage, keys, size, answered or pending |
| `lint.json`, `sample.json`, `answers/`, `audit/`, `report.md` | as above |

## The answerer's brief

You are the model behind the gateway. You see only the request body: the system prompt and the
user message. Do not open the PDF, the run outputs or the key; the point is to find out whether the
context alone is enough.

- Answer as the system prompt instructs, in the JSON shape its schema instruction gives. Write the
  raw reply to `answers/<id>.txt` (code fences are fine; they are stripped as the gateway strips them).
- If a figure cannot be placed from the context, say nothing for it, as the prompt allows. Do not guess.
- Then write `audit/<id>.json`: for each line, what you needed, where you found it (block, caption,
  figures) or that you did not, and any assumption you had to make. List every problem in the context
  you noticed, quoting the context.

## The audit rubric (`audit/<id>.json`)

```json
{ "id": "...", "line_keys": ["..."],
  "verdict": "answerable | answerable_with_assumption | not_answerable",
  "per_line": [{ "key": "...", "needed": "one sentence",
                 "found": {"block": "...", "caption": "...", "figures": {...}} ,
                 "verdict": "...", "assumption": "text or null" }],
  "problems": [{ "kind": "...", "detail": "text quoting the context" }],
  "would_need": "what would have made it answerable", "confidence": 0.0 }
```

- `answerable`: the context names the row and its period and unit without doubt.
- `answerable_with_assumption`: you could answer only by assuming something the context does not say
  (which column is current, which entity, that a figure is in lakhs). Write the assumption down.
- `not_answerable`: the figure is not there, or cannot be told apart from another.

Problem kinds: `rows_not_from_statement`, `period_label_unclear`, `fragment_as_row`, `no_unit`,
`duplicate_caption`, `note_not_supplied`, `definition_unclear`, `definition_conflicts_contract`
(the definition says one thing and the prompt's rules another), `entity_unclear`, `truncated`, `other`.

## The lint checks

Each hit carries the evidence quoted from the request.

| check | fires when |
| --- | --- |
| `rows_not_from_statement` | a row in a `statement_rows` block whose source (matched back to `run/rows.json` by caption and figure) is a notes page, is printed in the notes, or is on a page classified as another statement |
| `period_label_unclear` | figures keyed other than `current`/`prior` (`col2`, `current_col1`...); a row with prior but no current where the block's other rows have current; or a block whose current and prior column headers read the same |
| `fragment_as_row` | a caption that reads as a cut sentence (ends on `₹`, `in`, `of`, a trailing preposition, an unclosed bracket, or runs past 18 words) and carries a figure |
| `no_unit` | the request carries figures but states no unit, scale or currency anywhere |
| `duplicate_caption` | one caption with different figures inside one block |
| `note_not_supplied` | a line whose `read_from` or definition points to a note, with `notes_supplied` empty (a line said to be "not in a note" does not count) |
| `entity_doubtful` | a block labelled with an entity (standalone/consolidated) the run's face pages do not have |
| `oversize` | a request above the budget (`lint --budget`, est. tokens, default 32,000) |

## The key (`--key`)

What the filing prints, written by someone reading the filing:

```json
{ "filing": "X", "template": "output_csv_icon_v1", "unit": "₹ in lakhs", "basis": "standalone",
  "lines": { "<key>": { "current": "123.45", "prior": "120.00", "page": 14,
                        "where": "note 26 row 'Other income'", "comment": "..." } } }
```

`null` means the filing does not print it. The older scratch shape (numbers instead of strings,
`units`, `source` for `where`, `confidence`) is accepted and converted.

## The report

- **Requests:** total, by stage, answered, pending, sizes.
- **Lint:** each check, the number of requests hit, two examples quoting the context.
- **Blind audit:** verdict counts; problem kinds ranked, with an example; what the answerer said it
  would need.
- **Against the key**, per line and period, the cause of each miss:
  - `correct` — the run published what the filing prints;
  - `lost_in_context` — the filing prints it but no request asking about the line carried that
    figure; the lint checks and audit problems on those requests are named;
  - `not_in_filing` — the filing prints nothing and the run published something;
  - `model_misread` — the request carried the figure, it was answered, and the result is still wrong;
  - `unanswered` — the request carried the figure but has no answer yet;
  - `deterministic` — the line was not asked; the miss is in the rule-based route.
- **Top fixes:** the context faults behind the most `lost_in_context` misses, with the code that
  builds that part of the payload (`face_context.py` for statement rows, `note_context.py` for note
  blocks, `line_item_notes.py` for which notes a line gets, `line_item_llm.py` for the payload).

A fault counts against every miss on a request that carries it, so the top-fixes figures overlap;
read them as a ranking, not a sum.
