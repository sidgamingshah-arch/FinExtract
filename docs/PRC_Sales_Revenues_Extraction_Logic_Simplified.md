# PRC Sales (Revenues) Extraction Logic

## 1. Target Field

```text
Sales (Revenues)
```

Extract the amount reported as:

```text
主营业务
主营业务收入
```

---

## 2. Extraction Priority

### Priority 1: Face of the Income Statement

Search the face of the income statement for:

```text
主营业务收入
主营业务
```

If a value is explicitly reported, extract that value.

```text
P1 = 主营业务收入 reported on the face of the income statement
```

### Priority 2: Revenue Note

If Priority 1 is unavailable, search the financial statement notes for:

```text
营业收入
营业收入和营业成本
营业收入及营业成本
营业收入、营业成本
```

Within the identified note, locate the row:

```text
主营业务
主营业务收入
```

Extract the amount under the revenue column:

```text
收入
营业收入
主营业务收入
```

```text
P2 = 主营业务 revenue reported in the 营业收入 note
```

---

## 3. Column Selection Rule

Where the table contains separate revenue and cost columns:

```text
Select: 主营业务 × 收入
Do not select: 主营业务 × 成本
```

Exclude columns labelled:

```text
成本
营业成本
主营业务成本
```

---

## 4. Final Logic

```text
Sales (Revenues) = FIRST_VALID(P1, P2)
```

```text
IF P1 is available:
    Sales (Revenues) = P1
ELSE IF P2 is available:
    Sales (Revenues) = P2
ELSE:
    Sales (Revenues) = null
```

Do not use total `营业收入` as a fallback when `主营业务` or `主营业务收入` is not separately disclosed.

---

## 5. Duplicate Control

- Do not add the amount from the face of the income statement to the amount in the note.
- If the same amount appears in both locations, use Priority 1 and retain the note as supporting evidence only.
- Do not add a `主营业务` total to its underlying product, industry, or geographic components.
- Do not extract cost values, percentages, growth rates, or gross margins.

---

## 6. Output Structure

```json
{
  "sales_revenues": {
    "value": null,
    "priority_used": null,
    "source_location": null,
    "source_note": null,
    "row_label": null,
    "column_label": null,
    "currency": null,
    "unit": null,
    "page": null,
    "evidence_text": null,
    "status": null
  }
}
```

---

## 7. Pseudocode

```python
def extract_sales_revenues(face_value, note_value):
    if face_value is not None:
        return {
            "value": face_value,
            "priority_used": "P1",
            "source_location": "income_statement",
            "status": "DIRECTLY_EXTRACTED"
        }

    if note_value is not None:
        return {
            "value": note_value,
            "priority_used": "P2",
            "source_location": "revenue_note",
            "status": "DIRECTLY_EXTRACTED"
        }

    return {
        "value": None,
        "priority_used": None,
        "source_location": None,
        "status": "NOT_FOUND"
    }
```
