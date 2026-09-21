# Working notes for this repository

## Config updates are propagated into the working branch

**Standing instruction.** A change to configuration is not finished when it measures well — it is
finished when it is in the branch. Propagate it: commit it into the branch being worked on,
alongside the code and the tests that depend on it, and push.

What counts as configuration here:

| file | what it is |
| --- | --- |
| `backend/app/sample/templates/output_csv_hk_line_items.json` | the line-item set — the concepts, their aliases, their note patterns and their cascades |
| `backend/app/sample/templates/output_csv_hk_v1_template.json` | the output template. **Fixed.** 480 printed keys |
| `backend/app/sample/templates/output_csv_hk_rules.json` | the rulebook |
| `backend/app/sample/templates/output_csv_hk_validation.json` | the validation block |

Three things follow from it:

- **Nothing stays in a scratchpad seed.** Measuring a config variant against a copy under the
  session scratchpad is the right way to work — it keeps the repo's seed untouched while an idea is
  still an idea. But once a variant is the answer, it goes into the branch. A seed file that is
  "the current thinking" and is not in the branch is a config update that was not passed ahead.
- **A change built and then held back is reported, not parked.** Sometimes the right call is not to
  land something: a fix that corrects one filing and breaks another, an instruction that conflicts
  with a measurement. That is a legitimate outcome, and it is still a config update — say so, with
  the figures, rather than leaving it in a scratch file where it reads as forgotten.
- **Say what moved.** A reply that lands a config change names the keys added, modified and removed,
  and whether the fixed template moved. The commit body carries the reasoning; the reply carries the
  delta, so it is not buried.

## The template boundary

The output template is fixed. A new key beginning `sub__` is a PART: `namespace: internal`,
`in_output: false`, no printed column, and it must be wired into a parent's cascade or its figure
reaches nothing. **Any other new key is a new printed column and is not allowed.**

The check is `tests/test_template_provisions_line_items.py`, which pins the split at 462 template
items and counts the internal parts; `tests/test_working_view_parity.py` pins the total item count.
A part that carries face aliases must also declare `statement` AND `section_scope`, or
`tests/test_line_item_gate.py` fails it — a caption could otherwise bind it from any statement.

## A config change is measured against the whole corpus, not one filing

Five reference filings, two accounting regimes. A change that fixes a mainland filing and moves an
HKEX figure has not been measured until the HKEX figure is explained:

- `688008_montage_2024_prc` — 澜起科技, native-text PDF, CAS
- `000709_hesteel_2024_prc` — 河钢股份, CAS
- `300319_maijie_2024_prc` — CAS
- `hkex_1966_ar` — China SCE, HKFRS, thousands
- `hkex_kaming_2025_26` — 嘉民, HKFRS, thousands

Report the movement per filing against the figures the filing PRINTS, and say which structural
relations changed status. "No test failed" is not the same as "no figure moved": most of these
figures are not pinned by a test.

## The branch this work lives on

`.claude/dev-branch` pins `claude/financial-extraction-product-b4t7mp`, and
`.claude/hooks/session-start.sh` fast-forwards the tree towards it on session start — but only when
that cannot lose anything. When the working branch carries commits the pinned branch does not, the
hook declines and says so, because choosing between two histories is a person's decision. Do not
resolve that divergence unasked: report it and let a person choose.
