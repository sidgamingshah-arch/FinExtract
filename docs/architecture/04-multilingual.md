# Multilingual support — input = output parity (Requirement 21)

The system must extract from documents in language L **and** display/export in
language L, for the *same* set of languages. Parity is enforced structurally, not by
convention.

**Seed set:** English (en), Chinese (zh), Arabic (ar — RTL), French (fr).

## Input side (extraction)

- **Language detection** (`stages/language.py::LanguageDetectStage`, stage name
  `language_detect`) sets the document `locale`, which drives OCR pack selection, number
  parsing, and line-item alias selection. It is a dependency-free script/keyword heuristic
  (`detect_locale`) covering the seed set; the `fasttext` extra exists so a statistical
  detector *can* be plugged in, and nothing imports it today.
- **Locale-aware number parsing** (`services/numbers.py`) — decimal/thousands
  separators and grouping differ by locale (`1,234.56` US · `1.234,56` FR ·
  `1,23,456` Indian). Driven by the line-item set's per-locale `NumberFormat`
  (`number_format_by_locale`), which is also the **only** place parentheses-as-negative is
  decided — the duplicate global switch was removed because it could not be honoured after
  extraction, when only the signed magnitude survives.
- **Per-locale line-item aliases** — a line item's `aliases_i18n` is locale-scoped;
  the matcher pulls the active locale's aliases plus the English set as a cross-lingual
  anchor. Traditional/Simplified Han is folded by `services/han.py` (with the `cjk` extra,
  falling back to a built-in table of financial-statement variant pairs), and the classifier's
  lexicon carries Han patterns directly. A **multilingual embedding model** was once the intended
  cross-lingual fallback; the tier it needed was never wired to a provider and has been removed
  (see [01-extraction-pipeline](01-extraction-pipeline.md)), so cross-lingual mapping rests on the
  per-locale aliases, the Han folding, the rule tier's hints — which now read the normalised
  caption, so a hint fires on a bilingual line — and the LLM, which is multilingual by nature.

## Output side

- **Localized canonical labels** — `TemplateNode.label_i18n`; `resolve_label(locale)`
  falls back to English.
- **Frontend i18n** — hand-rolled: `frontend/src/i18n.ts` (core chrome) merging
  `src/i18n/screens/*.ts`, exposed as `useT()`. There is no react-i18next dependency.
  `Intl` / `toLocaleString` for numbers and dates. **RTL** (Arabic): `dir="rtl"` on the
  document root mirrors the layout; the normalized-bbox overlay is unaffected because
  coords stay in page space.
- **Localized export** — Excel/JSON labels + number formats in the chosen locale
  (`services/export.py`).

## Parity registry (the guarantee)

`schemas/languages.py` — a language is `supported` **only when all five** parity
artifacts exist for it:

1. OCR pack, 2. locale number-format rules, 3. line-item aliases for the active
template, 4. template `label_i18n`, 5. UI translation bundle.

`evaluate_parity` computes this per locale from the template and the line-item
configuration; the `/languages` API
surfaces `supported` + `missing`. The UI offers only fully-supported languages for
both input and output, so the two sets are identical **by construction**. Adding a
language = supplying those five artifacts (data/assets), not an engineering change.

Verified by `tests/test_schemas.py::test_language_parity_full_for_seed_set` and
`tests/test_api.py` (all four seed languages report `supported` given a fully
localized template + line-item set; Arabic reports `rtl=True`).

## Localization scope — data by default, whole UI by admin choice

The product distinguishes **two locales**, so the multilingual capability targets what
actually matters (the extracted numbers) without forcing a translated interface on
everyone:

- **Output / data locale** — always applied to the *extracted financial output*:
  statement line-item labels, statement names, and note content. This is what the
  top-bar language picker selects.
- **App locale** — the language of the interface chrome (nav, screens, prose). It is
  **English by default**; localizing the whole interface is an **admin-controlled**
  feature flag (`features.ui_localization`, toggled on the Settings screen — see
  [08-configuration-and-auth.md](08-configuration-and-auth.md)).

Frontend policy (`frontend/src/store.ts::useAppLocale`): `appLocale = ui_localization ?
locale : "en"`. Data screens (Workspace, Notes, Extraction, Export) always fetch with the
output `locale`; app-prose screens (Upload, Integrity, Scope, Review, Template, Analysis)
fetch with `appLocale`, and `useT()` translates chrome with `appLocale`.
`document.dir` is `rtl` only when the
*interface* is localized to Arabic — data-only Arabic keeps an LTR layout while the
Arabic line items still render right-to-left within their cells (Unicode bidi).

The backend localization machinery is unchanged (each endpoint localizes whatever
`locale` it is given); the two-locale policy lives in the client and the admin flag is
the switch. This directly answers the requirement to localize *just the extracted
financial output and line items*, while keeping whole-app localization available as
admin configuration.

## Frontend (implemented)

The React app surfaces the parity directly:
- A **language (output) switcher** in the top bar, populated from `GET /languages`
  (`frontend/src/components/shell/LanguageSwitcher.tsx`) — only fully-supported
  locales are offered, so the input and output language sets are identical.
- **i18n chrome** (`frontend/src/i18n.ts` + `src/i18n/screens/*.ts`) for the nav rail,
  pipeline stepper, workspace toolbar/column headers, and every screen in en/zh/ar/fr —
  surfaced only when an admin enables whole-interface localization.
- **RTL** — with interface localization on, selecting Arabic sets `dir="rtl"` on the
  document root, mirroring the whole layout (nav rail, source viewer, grid).
- **Localized output labels** — the workspace fetches statements with the output
  `locale` so line-item labels render in the chosen language *regardless of the
  interface language*, while the left source panel keeps the document's own (English)
  label — a direct visual demonstration of input→output parity. The backend resolution is
  covered by `tests/test_projects.py::test_localized_statement_labels`; the four-locale
  rendering itself has no automated check.

Statement-caption translations are standard IFRS/Ind-AS terminology for
demonstration; a native financial-language review is recommended before production.
