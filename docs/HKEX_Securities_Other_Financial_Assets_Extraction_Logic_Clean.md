# HKEX Securities and Other Financial Assets Extraction Logic

## 1. Purpose

Build AI-based extraction and computation logic for the following fields from HKEX filings:

1. `Secur & Other Fincl Assets (CP)`
2. `Secur & Other Fincl Assets (LTP)`

1. Identify the relevant notes.
2. Extract note totals and specified components.
3. Store atomic values separately.
4. Apply the defined formulas and negative-result rules.
5. Return one final value for each target field.

---

## 2. Processing Order

Always calculate the fields in this sequence:

```text
1. Secur & Other Fincl Assets (CP)
2. Secur & Other Fincl Assets (LTP)
```

The LTP calculation uses the negative CP candidate when the CP calculation falls below zero. The negative CP candidate represents the portion of Level 3 that could not be absorbed by CP.

---

## 3. Common Extraction Rules

### 3.1 Filing scope

- Process HKEX filings only.
- Preserve the reported currency and unit.
- Normalize units before performing arithmetic.
- Do not add English and Traditional Chinese versions of the same disclosure.
- Search financial-statement notes and supporting fair-value tables.

### 3.2 Value handling

For every extracted amount:

- Preserve the source note, page, original label, currency, and unit.
- Return `null` when a note or value is not found.
- Do not treat a missing value as zero.
- Use zero only when the filing explicitly reports zero or when the computation rule below sets the output to zero.
- Use net carrying amounts where both gross and net amounts are shown, unless the note total clearly represents the required carrying value.

### 3.3 Parent and component control

For each identified note:

- Extract the note total once.
- Extract specified deduction components only from within that same note.
- Do not add a note total to its underlying line items.
- Do not deduct the same component twice.
- Do not deduct a component merely because the same term appears elsewhere in the filing.
- A deduction must be demonstrably included in the extracted note total.

---

## 4. Shared Note and Item Dictionary

## 4.1 In-scope financial asset notes

Search for the following note headings and close semantic variants.

```text
Financial assets at fair value through profit or loss
Financial asset at FVTPL
Financial assets at fair value through other comprehensive income
Financial asset at FVTOCI
Financial asset
Financial assets
Available-for-sale financial assets
Held-to-maturity financial assets
Structured deposits with embedded derivatives
Debt investments
Other debt investments
Other financial assets
Investment securities
Money market instruments
Marketable securities
Short-term money market deposits
```

Traditional Chinese aliases may include:

```text
按公平值計入損益的金融資產
按公平值計入其他全面收益的金融資產
金融資產
可供出售金融資產
持有至到期金融資產
含嵌入式衍生工具的結構性存款
債務投資
其他債務投資
其他金融資產
投資證券
貨幣市場工具
有價證券
短期貨幣市場存款
```

## 4.2 Deduction items

Within each identified note, search for amounts attributable to:

```text
Derivative
Derivatives
Derivative financial instruments
Other receivables
Investment in related parties
Investment in associates
Investment in joint ventures
Investment in JV
```

Traditional Chinese aliases may include:

```text
衍生工具
衍生金融工具
其他應收款項
於關聯方的投資
於聯營公司的投資
於合營企業的投資
```

The CP field deducts only derivatives and other receivables.

The LTP field deducts derivatives, other receivables, and investments in related parties, associates, or joint ventures.

## 4.3 Fair value hierarchy note

Search for:

```text
Fair value hierarchy
Fair value measurement
Fair value measurements
Fair value hierarchy of financial instruments
```

Traditional Chinese aliases may include:

```text
公平值層級
公平值計量
金融工具公平值層級
```

Within the fair value hierarchy table, extract:

```text
Level 3 total
第三級總額
第三層級總額
```

Do not extract Level 1 or Level 2 amounts.

---

# 5. Secur & Other Fincl Assets (CP)

## 5.1 Identify Current Asset Notes

Search the Current Assets section and related notes for:

```text
Financial assets at fair value through profit or loss
Financial assets at fair value through other comprehensive income
Financial asset / Financial assets
Available-for-sale financial assets
Held-to-maturity financial assets
Structured deposits with embedded derivatives
Debt investments
Other debt investments
Other financial assets
Investment securities
Money market instruments
Marketable securities
Short-term money market deposits
```

Only use amounts classified under Current Assets for this field.

## 5.2 Extract Atomic Values

For every identified Current Asset note, extract:

```text
note_total
included_derivatives
included_other_receivables
```

Also extract the Level 3 total from the fair value hierarchy table:

```text
level_3_total
```

### Dataset structure

```json
{
  "note_key": "fvtpl_current",
  "note_heading_original": "Financial assets at fair value through profit or loss",
  "classification": "current_asset",
  "note_total": null,
  "included_derivatives": null,
  "included_other_receivables": null,
  "page": null,
  "currency": null,
  "unit": null,
  "evidence_text": null
}
```

## 5.3 Aggregate Finds

```text
Find_1_CP = SUM(unique note_total across all identified Current Asset notes)

Find_2_CP =
    SUM(unique included_derivatives)
  + SUM(unique included_other_receivables)

Find_3_CP = level_3_total
```

`Find_2_CP` may be treated as zero only when the relevant Current Asset notes were found and neither deduction component is included in those note totals. If the inclusion test cannot be completed, keep `Find_2_CP` as `null` and flag the result.

## 5.4 Computation Logic

### Primary formula

```text
CP_candidate = Find_1_CP - Find_2_CP - Find_3_CP
```

### Final CP value and LTP carry-forward

```text
IF CP_candidate >= 0:
    CP_final = CP_candidate
    CP_negative_carryforward_to_LTP = 0
ELSE:
    CP_final = 0
    CP_negative_carryforward_to_LTP = CP_candidate
```

Important:

- If `CP_candidate` is negative, do not replace the negative candidate with `Find_1_CP - Find_2_CP`.
- The reported CP output is zero.
- Preserve the original negative `CP_candidate` and pass it to the LTP computation.
- Because the carry-forward is negative, adding it to LTP reduces the LTP value by the unabsorbed Level 3 amount.

Equivalent positive residual representation:

```text
LTP_level_3_residual = ABS(CP_negative_carryforward_to_LTP)
```

The LTP logic may therefore be expressed either as:

```text
LTP_base + CP_negative_carryforward_to_LTP
```

or equivalently:

```text
LTP_base - LTP_level_3_residual
```

### Missing Level 3

```text
IF the fair value hierarchy note or Level 3 value is not found:
    CP_final = Find_1_CP - Find_2_CP
    CP_negative_carryforward_to_LTP = null
    raise MISSING_LEVEL_3
```

Do not assume that a missing Level 3 disclosure equals zero.

---

# 6. Secur & Other Fincl Assets (LTP)

## 6.1 Identify Non-current Asset Notes

Search the Non-current Assets section and related notes for:

```text
Financial assets at fair value through profit or loss
Financial assets at fair value through other comprehensive income
Financial asset / Financial assets
Available-for-sale financial assets
Held-to-maturity financial assets
Structured deposits with embedded derivatives
Debt investments
Other debt investments
Other financial assets
Investment securities
```

Only use amounts classified under Non-current Assets for this field.

Do not include the following CP-only search headings unless the filing separately classifies them as non-current:

```text
Money market instruments
Marketable securities
Short-term money market deposits
```

## 6.2 Extract Atomic Values

For every identified Non-current Asset note, extract:

```text
note_total
included_derivatives
included_other_receivables
included_related_party_investments
included_associate_investments
included_joint_venture_investments
```

### Dataset structure

```json
{
  "note_key": "fvoci_non_current",
  "note_heading_original": "Financial assets at fair value through other comprehensive income",
  "classification": "non_current_asset",
  "note_total": null,
  "included_derivatives": null,
  "included_other_receivables": null,
  "included_related_party_investments": null,
  "included_associate_investments": null,
  "included_joint_venture_investments": null,
  "page": null,
  "currency": null,
  "unit": null,
  "evidence_text": null
}
```

## 6.3 Aggregate Finds

```text
Find_1_LTP = SUM(unique note_total across all identified Non-current Asset notes)

Find_2_LTP =
    SUM(unique included_derivatives)
  + SUM(unique included_other_receivables)
  + SUM(unique included_related_party_investments)
  + SUM(unique included_associate_investments)
  + SUM(unique included_joint_venture_investments)
```

`Find_2_LTP` may be treated as zero only when the relevant Non-current Asset notes were found and none of the specified deduction components is included in those note totals. If the inclusion test cannot be completed, keep `Find_2_LTP` as `null` and flag the result.

## 6.4 Computation Logic

Use the negative CP candidate carried forward by the CP calculation. ### Base formula

```text
LTP_base = Find_1_LTP - Find_2_LTP
```

### Final formula

```text
IF CP_negative_carryforward_to_LTP is available:
    LTP_final = LTP_base + CP_negative_carryforward_to_LTP
ELSE:
    LTP_final = LTP_base
    raise MISSING_CP_CARRYFORWARD_TO_LTP
```

Since `CP_negative_carryforward_to_LTP` is either zero or negative:

- A zero carry-forward leaves LTP unchanged.
- A negative carry-forward reduces LTP by the unabsorbed part of Level 3.

Use three separate LTP values for auditability:

```text
LTP_base = Find_1_LTP - Find_2_LTP
LTP_candidate = LTP_base + CP_negative_carryforward_to_LTP
LTP_final = LTP_candidate if LTP_candidate >= 0, otherwise LTP_base
```

Equivalent formula using a positive residual:

```text
LTP_final = LTP_base - ABS(CP_negative_carryforward_to_LTP)
```

If the adjusted LTP candidate is negative:

```text
LTP_final = LTP_base
raise NEGATIVE_LTP_ADJUSTMENT_REVERSED
```

In other words, when applying the negative CP carry-forward would make LTP negative, do not apply the carry-forward. Use the unadjusted `LTP_base` as the final LTP value.

---

## 7. Formula Summary

```text
CP Find 1 = Sum of in-scope Current Asset note totals
CP Find 2 = Derivatives + Other receivables included in CP Find 1
CP Find 3 = Total Level 3 amount from fair value hierarchy

CP candidate = CP Find 1 - CP Find 2 - CP Find 3

If CP candidate >= 0:
    CP final = CP candidate
    CP negative carry-forward to LTP = 0

If CP candidate < 0:
    CP final = 0
    CP negative carry-forward to LTP = CP candidate


LTP Find 1 = Sum of in-scope Non-current Asset note totals

LTP Find 2 =
    Derivatives
  + Other receivables
  + Investments in related parties
  + Investments in associates
  + Investments in joint ventures
  included in LTP Find 1

LTP base = LTP Find 1 - LTP Find 2

LTP final =
    LTP base + CP negative carry-forward to LTP

Equivalent formula:

LTP final =
    LTP base - ABS(CP negative carry-forward to LTP)

If adjusted LTP candidate < 0:
    LTP final = LTP base
```

---

## 8. Comparability Rules Before Arithmetic

Values may be added or subtracted only when:

```text
Currency matches
Normalized unit matches
Current/non-current classification matches the target field
Deduction components are included in the relevant note totals
No bilingual duplication exists
No parent-component duplication exists
```

If any condition fails, mark the relevant calculation as not computable.

---

## 9. Suggested Final Output

```json
{
  "secur_other_fincl_assets_cp": {
    "find_1": null,
    "find_2": null,
    "find_3_level_3": null,
    "value": null,
    "formula_used": null,
    "cp_candidate": null,
    "cp_negative_carryforward_to_ltp": null,
    "status": null,
    "source_record_ids": [],
    "qa_flags": []
  },
  "secur_other_fincl_assets_ltp": {
    "find_1": null,
    "find_2": null,
    "cp_negative_carryforward_to_ltp": null,
    "value": null,
    "formula_used": null,
    "status": null,
    "source_record_ids": [],
    "qa_flags": []
  }
}
```

---

## 10. Pseudocode

```python
def sum_or_null(values):
    available = [v for v in values if v is not None]
    return sum(available) if available else None


def compute_cp(find_1, find_2, level_3):
    if find_1 is None or find_2 is None:
        return None, None, None, "NOT_COMPUTABLE"

    cp_base = find_1 - find_2

    if level_3 is None:
        return cp_base, None, None, "MISSING_LEVEL_3"

    cp_candidate = cp_base - level_3

    if cp_candidate >= 0:
        return cp_candidate, cp_candidate, 0, "COMPUTED"

    return 0, cp_candidate, cp_candidate, "NEGATIVE_CP_CARRIED_TO_LTP"


def compute_ltp(find_1, find_2, cp_negative_carryforward):
    if find_1 is None or find_2 is None:
        return None, "NOT_COMPUTABLE"

    ltp_base = find_1 - find_2

    if cp_negative_carryforward is None:
        return max(ltp_base, 0), "MISSING_CP_CARRYFORWARD_TO_LTP"

    ltp_candidate = ltp_base + cp_negative_carryforward

    if ltp_candidate < 0:
        return ltp_base, "NEGATIVE_LTP_ADJUSTMENT_REVERSED"

    return ltp_candidate, "COMPUTED"


find_1_cp = sum_or_null(current_asset_note_totals)
find_2_cp = sum_or_null([
    current_included_derivatives,
    current_included_other_receivables,
])

(
    cp_final,
    cp_candidate,
    cp_negative_carryforward_to_ltp,
    cp_status,
) = compute_cp(find_1_cp, find_2_cp, level_3_total)

find_1_ltp = sum_or_null(non_current_asset_note_totals)
find_2_ltp = sum_or_null([
    non_current_included_derivatives,
    non_current_included_other_receivables,
    non_current_included_related_party_investments,
    non_current_included_associate_investments,
    non_current_included_joint_venture_investments,
])

ltp_final, ltp_status = compute_ltp(
    find_1_ltp,
    find_2_ltp,
    cp_negative_carryforward_to_ltp,
)
```

---

## 11. Required QA Flags

```text
MISSING_NOTE
MISSING_VALUE
MISSING_LEVEL_3
MISSING_CP_CARRYFORWARD_TO_LTP
UNIT_MISMATCH
CURRENCY_MISMATCH
CLASSIFICATION_MISMATCH
DEDUCTION_NOT_PROVEN_INCLUDED
POSSIBLE_DUPLICATE
PARENT_COMPONENT_OVERLAP
NEGATIVE_CP_CARRIED_TO_LTP
NEGATIVE_LTP_ADJUSTMENT_REVERSED
LOW_CONFIDENCE_MAPPING
NOT_COMPUTABLE
```
