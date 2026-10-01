# Reference data — which of these files the product reads

Twelve files ship here. **Five are read by the product**: `app/sample/reference.py` seeds them into
the versioned tables at every boot (and holds the database to them on every restart), and a run
reads them from there. **The other seven are read by no boot and no run.** They are inputs to build
scripts, or fixtures for tests, or both; editing one changes no extracted figure.

| file | role | read by | what it is |
| --- | --- | --- | --- |
| `output_csv_hk_v1_template.json` | live | boot seed (`reference._EXTRA_PAIRS`) | the template every `output_csv_hk` run targets — 480 nodes, **fixed** |
| `output_csv_hk_line_items.json` | live | boot seed; `services/line_item_config.SEED`; `services/row_reconstruct.in_force_rules` | THE configuration: the line-item set for `output_csv_hk_v1` |
| `output_csv_indas_v1_template.json` | live | boot seed (`reference._EXTRA_PAIRS`) | the Ind AS template |
| `output_csv_indas_line_items.json` | live | boot seed (`reference._EXTRA_PAIRS`) | the Ind AS line-item set, for `output_csv_indas_v1` |
| `hkfrs_hk_china_template.json` | live | boot seed (`reference._TEMPLATE`) | the primary template, seeded with no configuration beside it |
| `output_csv_hk_ontology.json` | build input | `scripts/build_line_items.py`; about a dozen test files | the old rulebook; the build projects it into the line-item set |
| `output_csv_hk_line_items_configured.json` | build input | `scripts/build_line_items.py` | the hand-maintained half of that build's merge |
| `output_csv_hk_rules.json` | legacy | `tests/test_rulebook_rules.py` | a "rules master" split out of the rulebook |
| `output_csv_hk_validation.json` | legacy | `tests/test_validation_rules.py`, `tests/test_structural_reaches_the_run.py` | a "validation master"; ships empty |
| `hkfrs_hk_china_ontology.json` | legacy | ~40 test files; the HKFRS builder scripts | the HKFRS rulebook; no line-item set replaces it |
| `hkfrs_hk_china_rules.json` | legacy | `tests/test_rulebook_rules.py` | the HKFRS rules master |
| `hkfrs_hk_china_validation.json` | legacy | `tests/test_validation_rules.py` | the HKFRS validation master |

## How this was established

Measured, not assumed — and re-measured on every test run:

- **App code names the seven unread files only in docstrings and comments.** No string literal in
  `app/` names one (`tests/test_reference_data_validator.py::test_no_app_code_names_a_file_the_role_table_calls_unread`).
- **The boot seed reads only templates and line-item sets.** With all seven unread files deleted
  from a copy of the backend, `ensure_reference_data` on a fresh database published all five live
  files, and the API, export and extraction tests (`test_api`, `test_csv_export`,
  `test_extraction_idempotency`, `test_export_honesty`) passed. The only failures were six tests in
  `test_coverage_contract` that open `hkfrs_hk_china_ontology.json` as their own fixture.
- **A run is handed the line-item set, never an ontology.** The matcher's ontology is a view built
  in memory from the set (`services/working_view.build_working_view`); no route uploads an
  ontology, rules or validation file.
- **The live list is `sample.reference`'s own.** `FILE_ROLES` in
  `backend/scripts/validate_reference_data.py` is held to `reference._TEMPLATE`,
  `reference._EXTRA_PAIRS` and `line_item_config.SEED` by a test.

## What that means for an edit

- **To change what a run does, edit a live file.** The template rollups compute every calculated
  total; the line-item set decides everything else — aliases, gates, note sources, cascades, the
  framework blocks (`scope_selection`, `normalisation`, `binding`, `residual_framework`,
  `global_rules`) and any `validation` identities.
- **An edit to an unread file governs nothing.** An identity added to
  `output_csv_hk_validation.json` is evaluated by no run (a run evaluates the line-item set's own
  `validation` block); a netting rule or group added to `output_csv_hk_rules.json` is applied by no
  run. `output_csv_hk_rules.json`'s `residual_framework` already differs from the set's.
- **Do not rebuild the shipped set from the ontology without reading the diff.** The ontology has
  drifted from the curated set (aliases on 12 concepts, `aliases_i18n` on 20, `extraction_mode` on
  16, `item_type` on 45), and `output_csv_hk_line_items_configured.json` from the set, so
  `scripts/build_line_items.py` would put back what has since been curated.

## Validation

```bash
cd backend
python scripts/validate_reference_data.py     # a report grouped by file; exit 1 on any ERROR
```

Every file is checked. A **live** file gets the boot gate, the publish gate, the registry verdict
and the structural checks no gate runs — rollup cycles, a `reported_total_key` naming nothing, a
repeated `node_id`, unknown locales, a part whose figure reaches no output column, an aliased line
with no statement gate, a `section_scope` naming no section, the template boundary in both
directions, and gross-parent declarations against the template's own rollups. An **unread** file
must load (a script or a test reads it), and anything it names that its template does not, or
that the live set says differently, is a WARN: drift made visible, not a defect that can move a
figure. CI runs the validator on every push.
