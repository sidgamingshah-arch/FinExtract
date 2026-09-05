# HKEX Depreciation Extraction and Computation Logic

## 1. Purpose

Build AI-based extraction logic for the following fields from HKEX annual or interim filings:

1. `Deprec & Impairment (Oper Exp)`
2. `Deprec & Impairment (COS)`

The logic follows the structure in the **English** sheet of the source workbook:

1. Identify the relevant notes.
2. Extract depreciation-related values from each identified note.
3. Store atomic values separately.
4. Apply priority-based formulas.
5. Return one final value for each target field.

> Despite the field name, this rule includes only the depreciation and specified lease-related amortisation items listed below. Do not include impairment unless a separate rule explicitly requires it.

---

## 2. Processing Order

Always calculate the fields in this sequence:

```text
1. Deprec & Impairment (Oper Exp)
2. Deprec & Impairment (COS)
```

`Deprec & Impairment (COS)` may use the final output of `Deprec & Impairment (Oper Exp)`.

---

## 3. Common Extraction Rules

### 3.1 Filing scope

- Process HKEX annual or interim filings only.
- Preserve the reported currency and unit.
- Normalize units before performing arithmetic.
- Do not add English and Traditional Chinese versions of the same disclosure.

### 3.2 Depreciation-related items

Search for the following items within the identified notes:

```text
Depreciation of investment property
Depreciation
Depreciation of fixed assets
Depreciation of property, plant and equipment
Depreciation of construction in progress
Amortisation of prepaid lease payment
Release of prepaid lease payments
```

Traditional Chinese aliases may include:

```text
投資物業折舊
折舊
固定資產折舊
物業、廠房及設備折舊
在建工程折舊
預付租賃款項攤銷
預付租賃款項轉出
```

### 3.3 Value selection

For every extracted value:

- Select the amount included within the identified note only.
- Use the reported expense amount, not accumulated depreciation.
- Preserve the source note, page, original label, currency, and unit.
- Return `null` when the note or value is not found.
- Do not convert a missing value to zero.
- Use zero only when the filing explicitly reports zero.

### 3.4 Duplicate control

The same depreciation value may appear in multiple notes. Values from different datasets are alternative sources and must not be added together unless the formula below explicitly requires it.

Within a dataset:

- Add separately disclosed components only once.
- If a reported total equals its disclosed components, use the total or the components, not both.
- Do not add a subtotal to the line items included in that subtotal.

---

## 4. Step 1: Identify Notes and Extract Values

Create one extraction record for every row below.

### 4.1 Operating expense notes

| Dataset key | Identify notes using these headings | Extract within the identified note |
|---|---|---|
| `rd_depreciation` | Research and development; R&D expenses; Research and development expenses; 研究及開發開支; 研發開支 | All depreciation-related items in Section 3.2 |
| `selling_marketing_depreciation` | Selling and marketing expenses; Selling expenses; Selling and distribution expenses; Distribution costs; Marketing expenses; 銷售及市場推廣開支; 銷售開支; 銷售及分銷開支; 分銷成本 | All depreciation-related items in Section 3.2 |
| `ga_depreciation` | General and administrative expenses; Administrative expenses; G&A expenses; 一般及行政開支; 行政開支 | All depreciation-related items in Section 3.2 |
| `operating_expense_depreciation` | Operating expenses; Other operating expenses; Operating costs; 經營開支; 其他經營開支; 經營成本 | All depreciation-related items in Section 3.2 |

For each dataset key, sum qualifying line items within that note:

```text
note_value = SUM(unique qualifying qualifying values within the note)
```

Calculate the direct operating-expense value only from available operating-expense note values:

```text
opex_direct =
    rd_depreciation
  + selling_marketing_depreciation
  + ga_depreciation
  + operating_expense_depreciation
```

`opex_direct` is valid only when at least one qualifying depreciation value is directly extracted from an operating-expense note.

### 4.2 Profit or loss before taxation note

| Dataset key | Identify notes using these headings | Extract within the identified note |
|---|---|---|
| `pbt_oper_exp_depreciation` | Profit before taxation; Profit before tax; Profit/loss before taxation; Profit before taxation is arrived at after charging; 除稅前溢利; 除稅前利潤; 稅前溢利; 稅前利潤 | First look for a depreciation amount specifically identified as relating to operating expenses |
| `pbt_depreciation` | Same note headings as above | If no operating-expense-specific depreciation is disclosed, extract all qualifying depreciation-related items in Section 3.2 |

Apply the following extraction sequence within the note:

```text
IF a depreciation value is specifically described as relating to operating expenses:
    pbt_oper_exp_depreciation = that specific operating-expense depreciation value
    pbt_depreciation = null for this extraction route
ELSE:
    pbt_oper_exp_depreciation = null
    pbt_depreciation = SUM(unique qualifying depreciation values within the note)
```

Examples of an operating-expense-specific call-out include labels or surrounding text such as:

```text
Depreciation included in operating expenses
Depreciation charged to operating expenses
Depreciation under administrative, selling, R&D or other operating expenses
計入經營開支的折舊
經營開支所包含的折舊
計入行政、銷售、研發或其他經營開支的折舊
```

Do not add the operating-expense-specific value to other depreciation values in the same Profit Before Tax note. The specific value is used directly for `Deprec & Impairment (Oper Exp)` and takes precedence over the general PBT residual calculation.

Do not use a combined `depreciation and amortisation` amount if unrelated amortisation cannot be separated.

### 4.3 Cost of sales note

| Dataset key | Identify notes using these headings | Extract within the identified note |
|---|---|---|
| `cos_depreciation` | Cost of sales; Cost of revenue; Cost of services; Direct operating costs; 銷售成本; 收益成本; 服務成本; 直接經營成本 | Depreciation included in cost of sales |

```text
cos_depreciation = SUM(unique qualifying qualifying values within the note)
```

Do not infer that total depreciation from an expenses-by-nature table is included in Cost of Sales unless the filing explicitly allocates it to Cost of Sales.

### 4.4 Asset notes

| Dataset key | Identify notes using these headings | Extract within the identified note |
|---|---|---|
| `ppe_depreciation` | Property, plant and equipment; 物業、廠房及設備 | Depreciation charged for the applicable period |
| `prepaid_lease_depreciation` | Prepaid land lease payments; Prepaid lease payments; 預付土地租賃款項; 預付租賃款項 | Amortisation or release charged for the applicable period |
| `fixed_asset_depreciation` | Fixed assets; 固定資產 | Depreciation charged for the applicable period |
| `investment_property_depreciation` | Investment property; Investment properties; 投資物業 | Depreciation charged for the applicable period |
| `cip_depreciation` | Construction in progress; 在建工程 | Depreciation charged for the applicable period, only if explicitly disclosed |

Calculate:

```text
asset_note_depreciation =
    ppe_depreciation
  + prepaid_lease_depreciation
  + fixed_asset_depreciation
  + investment_property_depreciation
  + cip_depreciation
```

Avoid double counting where `fixed assets` is a parent note containing PPE, investment property, or another extracted component.

Exclude:

```text
Opening accumulated depreciation
Closing accumulated depreciation
Disposals
Write-offs
Transfers
Reclassifications
Exchange differences
Acquisition movements
Impairment losses
```

### 4.5 Cash flow from operations

| Dataset key | Identify notes or statements using these headings | Extract within the identified section |
|---|---|---|
| `cfo_depreciation` | Cash flow from operating activities; Cash generated from operations; Reconciliation of profit before taxation to cash generated from operations; 經營活動所得現金流量; 除稅前溢利與經營所得現金的對賬 | Depreciation for the applicable period |

```text
cfo_depreciation = SUM(unique qualifying depreciation values)
```

Do not use a combined `depreciation and amortisation` amount when unrelated amortisation cannot be separated.

---

## 5. Step 2: Computation Logic

## 5.1 Deprec & Impairment (Oper Exp)

Calculate all four priority candidates, but select only the first valid candidate.

### Priority 1: Direct operating-expense depreciation

```text
P1 = FIRST_VALID(opex_direct, pbt_oper_exp_depreciation)
```

Selection within P1:

1. Use `opex_direct` when depreciation is extracted from the identified R&D, selling and marketing, general and administrative, or operating expense notes.
2. If those note-level values are unavailable, use `pbt_oper_exp_depreciation` when the Profit Before Tax note specifically identifies depreciation relating to operating expenses.
3. Do not sum `opex_direct` and `pbt_oper_exp_depreciation`, since they may represent the same expense.

### Priority 2: PBT depreciation less Cost of Sales depreciation

```text
P2 = pbt_depreciation - cos_depreciation
```

Valid only when both inputs are available and comparable.

### Priority 3: Asset-note depreciation less Cost of Sales depreciation

```text
P3 = asset_note_depreciation - cos_depreciation
```

Valid only when both inputs are available and comparable.

### Priority 4: Cash-flow depreciation less Cost of Sales depreciation

```text
P4 = cfo_depreciation - cos_depreciation
```

Valid only when both inputs are available and comparable.

### Final selection

```text
oper_exp_final = FIRST_VALID(P1, P2, P3, P4)
oper_exp_priority_used = priority of selected candidate
```

If a candidate is negative:

```text
Mark the candidate invalid.
Continue to the next priority.
Do not automatically convert the negative result to zero.
```

If no candidate is valid:

```text
oper_exp_final = null
status = "NOT_FOUND_OR_NOT_COMPUTABLE"
```

---

## 5.2 Deprec & Impairment (COS)

Run the Oper Exp calculation first.

### Priority 1: Direct Cost of Sales depreciation

```text
COS_P1 = cos_depreciation
```

Valid when depreciation is directly extracted from the Cost of Sales note.

### Priority 2: PBT depreciation less final operating-expense depreciation

```text
COS_P2 = pbt_depreciation - oper_exp_final
```

Valid only when:

- `COS_P1` is unavailable;
- `pbt_depreciation` is available;
- `oper_exp_final` is available; and
- both inputs are comparable.

### Final selection

```text
cos_final = FIRST_VALID(COS_P1, COS_P2)
cos_priority_used = priority of selected candidate
```

If a candidate is negative:

```text
Mark the candidate invalid.
Continue to the next priority.
Do not automatically convert the negative result to zero.
```

If no candidate is valid:

```text
cos_final = null
status = "NOT_FOUND_OR_NOT_COMPUTABLE"
```

---

## 6. Comparability Rules Before Subtraction

Two values may be subtracted only when all conditions below are satisfied:

```text
Same currency
Same normalized unit
Compatible depreciation scope
No unresolved bilingual duplication
No total-versus-component duplication
```

If any comparability condition fails, mark the formula candidate invalid.

---

## 7. Formula Summary

```text
opex_direct =
    rd_depreciation
  + selling_marketing_depreciation
  + ga_depreciation
  + operating_expense_depreciation

asset_note_depreciation =
    ppe_depreciation
  + prepaid_lease_depreciation
  + fixed_asset_depreciation
  + investment_property_depreciation
  + cip_depreciation

Oper Exp P1 = FIRST_VALID(opex_direct, pbt_oper_exp_depreciation)
Oper Exp P2 = pbt_depreciation - cos_depreciation
Oper Exp P3 = asset_note_depreciation - cos_depreciation
Oper Exp P4 = cfo_depreciation - cos_depreciation

Deprec & Impairment (Oper Exp) = FIRST_VALID(P1, P2, P3, P4)

COS P1 = cos_depreciation
COS P2 = pbt_depreciation - Deprec & Impairment (Oper Exp)

Deprec & Impairment (COS) = FIRST_VALID(COS_P1, COS_P2)
```

---

## 8. Suggested Extraction Record

Return one record per extracted atomic value:

```json
{
  "dataset_key": "ga_depreciation",
  "target_field": "Deprec & Impairment (Oper Exp)",
  "note_heading_original": "Administrative expenses",
  "note_heading_normalized": "General and administrative expenses",
  "line_item_original": "Depreciation of property, plant and equipment",
  "line_item_normalized": "Depreciation of property, plant and equipment",
  "value_reported": 12500,
  "currency": "HKD",
  "unit": "HKD thousand",
  "page": 156,
  "evidence_text": "Depreciation of property, plant and equipment 12,500",
  "confidence": 0.96,
  "duplicate_group_id": null
}
```

---

## 9. Suggested Final Output

```json
{
  "deprec_impairment_oper_exp": {
    "value": 12500,
    "priority_used": "P1",
    "formula": "SUM(rd_depreciation, selling_marketing_depreciation, ga_depreciation, operating_expense_depreciation)",
    "status": "EXTRACTED_AND_COMPUTED",
    "source_record_ids": ["record_001"]
  },
  "deprec_impairment_cos": {
    "value": 43000,
    "priority_used": "COS_P1",
    "formula": "cos_depreciation",
    "status": "DIRECTLY_EXTRACTED",
    "source_record_ids": ["record_002"]
  }
}
```

The numbers in this sample output are illustrative only and must not be used as extraction defaults.

---

## 10. Pseudocode

```python
def sum_available(values):
    available = [v for v in values if v is not None]
    return sum(available) if available else None


def valid_non_negative(value):
    return value is not None and value >= 0


def subtract_if_comparable(total, deduction, comparable):
    if total is None or deduction is None or not comparable:
        return None
    result = total - deduction
    return result if result >= 0 else None


opex_direct = sum_available([
    rd_depreciation,
    selling_marketing_depreciation,
    ga_depreciation,
    operating_expense_depreciation,
])

asset_note_depreciation = sum_available_deduplicated([
    ppe_depreciation,
    prepaid_lease_depreciation,
    fixed_asset_depreciation,
    investment_property_depreciation,
    cip_depreciation,
])

oper_exp_candidates = [
    ("P1", first_valid_non_negative([
        opex_direct,
        pbt_oper_exp_depreciation,
    ])),
    ("P2", subtract_if_comparable(
        pbt_depreciation,
        cos_depreciation,
        comparable(pbt_depreciation, cos_depreciation),
    )),
    ("P3", subtract_if_comparable(
        asset_note_depreciation,
        cos_depreciation,
        comparable(asset_note_depreciation, cos_depreciation),
    )),
    ("P4", subtract_if_comparable(
        cfo_depreciation,
        cos_depreciation,
        comparable(cfo_depreciation, cos_depreciation),
    )),
]

oper_exp_priority_used, oper_exp_final = first_valid_non_negative(
    oper_exp_candidates
)

cos_candidates = [
    ("COS_P1", cos_depreciation),
    ("COS_P2", subtract_if_comparable(
        pbt_depreciation,
        oper_exp_final,
        comparable(pbt_depreciation, oper_exp_final),
    )),
]

cos_priority_used, cos_final = first_valid_non_negative(cos_candidates)
```

---

## 11. Required QA Flags

Raise one or more flags where relevant:

```text
MISSING_NOTE
MISSING_VALUE
COMBINED_DEPRECIATION_AND_AMORTISATION
UNIT_MISMATCH
CURRENCY_MISMATCH
POSSIBLE_DUPLICATE
TOTAL_COMPONENT_OVERLAP
NEGATIVE_RESIDUAL
IMPAIRMENT_EXCLUDED
LOW_CONFIDENCE_MAPPING
NOT_FOUND_OR_NOT_COMPUTABLE
```
