# PRC Contingent Liabilities Extraction and Reporting Logic

## 1. Target Field

```text
Contingent Liabilities
```

This field requires a structured narrative and table output rather than a single value.

The output must:

1. Search the specified notes.
2. Extract each relevant contingent liability item and amount.
3. Classify items into the four required types where possible.
4. Sum amounts within each classified type without double counting.
5. Present unclassified items as short statements with amounts.
6. Generate a concise summary paragraph followed by detailed tables.

---

## 2. Step 1: Identify Relevant Notes

Search the financial statement notes for:

```text
或有负债
或有事项
关联方担保
关联担保
未决诉讼
未决仲裁
诉讼及仲裁事项
重大诉讼、仲裁事项
对外担保
担保事项
承诺及或有事项
承诺事项及或有事项
```

Also search cross-referenced tables or sections explicitly linked from these notes.

---

## 3. Step 2: Extract Individual Items

For each relevant item, extract:

```text
Original description
Short English summary
Amount
Currency
Unit
Counterparty or beneficiary, if disclosed
Status, if disclosed
Source note
Page
Evidence text
```

### Amount selection

Use the amount that represents the contingent exposure or obligation.

Possible amount labels include:

```text
担保金额
担保余额
担保责任余额
实际担保金额
尚未履行金额
未结金额
涉案金额
诉讼金额
仲裁金额
或有负债金额
预计财务影响
```

Do not use the following as the exposure amount unless the disclosure explicitly states that the amount represents the contingent liability:

```text
授信额度
合同总额
累计发生额
交易金额
已偿还金额
已解除担保金额
已确认预计负债
```

If multiple amounts are disclosed for one item, select the most specific outstanding exposure or contingent amount. Retain the other amounts as supporting fields.

---

## 4. Step 3: Classify Each Item

Classify each extracted item into one of the four required types.

### 4.1 Corporate Guarantees

Classify as `Corporate guarantees` when the item relates to a guarantee issued for another company, related party, subsidiary, associate, joint venture, customer, supplier, or other corporate counterparty.

Search terms may include:

```text
公司担保
企业担保
对外担保
关联方担保
为子公司提供担保
为关联方提供担保
债务担保
借款担保
融资担保
连带责任保证
保证责任
```

Do not classify an item as a corporate guarantee when the disclosure specifically identifies it as a letter of credit, performance bond, or bank guarantee.

### 4.2 Letters of Credit

Classify as `Letters of Credit` when the item relates to:

```text
信用证
已开立信用证
未结信用证
未到期信用证
不可撤销信用证
备用信用证
跟单信用证
```

### 4.3 Performance Bonds

Classify as `Performance bonds` when the item relates to:

```text
履约保函
履约保证
履约担保
履约保证金
合同履约保函
工程履约保函
```

Use `履约保证金` only where the disclosure describes a contingent guarantee or bond exposure. Do not include an ordinary refundable deposit asset.

### 4.4 Bank Guarantees

Classify as `Bank guarantees` when the item explicitly relates to:

```text
银行保函
银行保证
银行出具的保函
融资性保函
非融资性保函
付款保函
预付款保函
投标保函
```

### 4.5 Unclassified Items

If an item cannot be reliably assigned to one of the four required types, classify it as:

```text
Unclassified contingent liability
```

Common examples may include:

```text
未决诉讼
未决仲裁
合同纠纷
索赔事项
税务争议
产品质量保证
亏损合同
环境责任
票据追索责任
其他或有事项
```

For each unclassified item, create a short factual statement and retain the amount.

---

## 5. Classification Priority

Apply the following order to prevent broad guarantee labels from overriding specific instruments:

```text
1. Letters of Credit
2. Performance bonds
3. Bank guarantees
4. Corporate guarantees
5. Unclassified contingent liability
```

Example:

```text
If an item contains both 对外担保 and 履约保函:
    Classify as Performance bonds.
```

```text
If an item contains both 关联方担保 and 银行保函:
    Classify as Bank guarantees.
```

---

## 6. Aggregation Logic

### 6.1 Classified types

Sum unique items within each type:

```text
Corporate_Guarantees_Total = SUM(unique Corporate guarantees)
Letters_of_Credit_Total = SUM(unique Letters of Credit)
Performance_Bonds_Total = SUM(unique Performance bonds)
Bank_Guarantees_Total = SUM(unique Bank guarantees)
```

### 6.2 Currency and unit control

Sum items only when currency and unit are compatible.

If the filing contains multiple currencies:

```text
Do not convert currencies unless an explicit converted amount is reported.
Present a separate subtotal for each currency.
```

### 6.3 Parent and component control

Do not add a reported total to the components included in that total.

```text
If a note reports:
    Total guarantees = 100
    Corporate guarantees = 60
    Bank guarantees = 40

Use 60 and 40 for type totals.
Do not calculate 100 + 60 + 40.
```

### 6.4 Repeated disclosure control

The same item may appear in:

```text
或有负债
关联方担保
对外担保
未决诉讼
重大事项
```

Treat repeated descriptions of the same underlying exposure as one item.

Use the following duplicate indicators:

```text
Same counterparty or case
Same amount
Same guarantee or case reference
Same underlying obligation
Cross-reference to the same note
```

### 6.5 Undisclosed or non-quantifiable amounts

If an item is disclosed but no amount can be extracted:

```text
amount = null
amount_status = "NOT_DISCLOSED_OR_NOT_QUANTIFIABLE"
```

Include the item in the narrative and detailed table, but exclude it from numeric totals.

---

## 7. Short-Statement Logic for Unclassified Items

Create one concise English statement per unclassified item.

### Statement format

```text
[Type of matter] involving [counterparty or subject, if disclosed], with a disclosed exposure of [currency and amount].
```

If no amount is disclosed:

```text
[Type of matter] involving [counterparty or subject, if disclosed]; the amount was not disclosed or could not be quantified.
```

Examples of acceptable structure:

```text
Pending litigation relating to a contract dispute, with a disclosed claim amount of RMB 12 million.
```

```text
Pending arbitration involving a supplier; the amount was not disclosed or could not be quantified.
```

Do not add legal conclusions, probability assessments, or expected outcomes unless explicitly stated in the filing.

---

## 8. Required Output Format

The user-facing output contains only:

1. A concise summary paragraph.
2. A classified summary table.
3. An unclassified items table, where applicable.

Item-level extraction evidence remains in backend JSON for traceability and is not shown as a separate audit-trail table.

Produce the user-facing output in this order:

### 8.1 Summary paragraph

Generate a short paragraph covering:

```text
The types identified
The total for each quantifiable type
The number or presence of unclassified matters
Whether any matters could not be quantified
```

Suggested structure:

```text
The filing discloses contingent liabilities relating to [identified types].
Quantifiable exposures comprise [type and total by currency].
Additional unclassified matters relate to [brief matter types].
[Number or presence] of disclosed matters could not be quantified from the filing.
```

Do not state that no contingent liabilities exist merely because no amount was disclosed.

### 8.2 Classified summary table

| Type | Amount | Currency | Item count | Source pages |
|---|---:|---|---:|---|
| Corporate guarantees |  |  |  |  |
| Letters of Credit |  |  |  |  |
| Performance bonds |  |  |  |  |
| Bank guarantees |  |  |  |  |

Rules:

- Include a row only when the type is identified.
- Present separate rows when one type contains multiple currencies.
- `Item count` must count unique underlying items, not repeated disclosures.
- Do not place `null` amounts into numeric totals.

### 8.3 Unclassified items table

| Short statement | Amount | Currency | Source note | Page |
|---|---:|---|---|---|
|  |  |  |  |  |

Rules:

- Include one row per unique unclassified item.
- Use `Not disclosed` when no amount is available.
- Keep each statement factual and concise.


---

## 9. Backend Atomic Extraction Record

The following record is retained internally and is not part of the visible response.

```json
{
  "item_id": "CL_001",
  "classification": "Corporate guarantees",
  "classification_basis": ["对外担保", "连带责任保证"],
  "description_original": null,
  "summary_english": null,
  "counterparty_original": null,
  "amount": null,
  "currency": null,
  "unit": null,
  "amount_label_original": null,
  "source_note_original": null,
  "page": null,
  "evidence_text": null,
  "duplicate_group_id": null,
  "amount_status": null,
  "confidence": null
}
```

---

## 10. Suggested User-Facing JSON Output

```json
{
  "contingent_liabilities": {
    "summary_paragraph": null,
    "classified_summary": [
      {
        "type": "Corporate guarantees",
        "amount": null,
        "currency": null,
        "item_count": 0,
        "source_pages": []
      }
    ],
    "unclassified_items": [
      {
        "short_statement": null,
        "amount": null,
        "currency": null,
        "source_note": null,
        "page": null
      }
    ],
    "status": null,
    "qa_flags": []
  }
}
```

---

## 11. Processing Logic

```python
def process_contingent_liabilities(extracted_items):
    unique_items = deduplicate_underlying_exposures(extracted_items)

    for item in unique_items:
        item.classification = classify_with_priority(
            item,
            priority=[
                "Letters of Credit",
                "Performance bonds",
                "Bank guarantees",
                "Corporate guarantees",
                "Unclassified contingent liability",
            ],
        )

        if item.classification == "Unclassified contingent liability":
            item.summary_english = create_short_factual_statement(item)

    classified_summary = aggregate_by_type_and_currency(
        items=[
            item for item in unique_items
            if item.classification != "Unclassified contingent liability"
            and item.amount is not None
        ]
    )

    unclassified_items = [
        item for item in unique_items
        if item.classification == "Unclassified contingent liability"
    ]

    summary_paragraph = create_summary_paragraph(
        classified_summary=classified_summary,
        unclassified_items=unclassified_items,
    )

    return {
        "summary_paragraph": summary_paragraph,
        "classified_summary": classified_summary,
        "unclassified_items": unclassified_items,
        "backend_evidence_records": unique_items,
    }
```

---

## 12. QA Rules

Raise one or more flags where relevant:

```text
MISSING_CONTINGENT_LIABILITY_NOTES
AMOUNT_NOT_DISCLOSED
AMOUNT_LABEL_AMBIGUOUS
CLASSIFICATION_AMBIGUOUS
CURRENCY_NOT_IDENTIFIED
UNIT_NOT_IDENTIFIED
MULTIPLE_CURRENCIES_NOT_AGGREGATED
POSSIBLE_DUPLICATE
PARENT_COMPONENT_OVERLAP
ESTIMATED_AMOUNT_NOT_SEPARABLE
RECOGNIZED_PROVISION_POSSIBLY_INCLUDED
LOW_CONFIDENCE_EXTRACTION
```

---

## 13. Final Rule Summary

```text
1. Search 或有负债, 关联方担保, 未决诉讼, 未决仲裁 and 对外担保 notes.

2. Extract each unique contingent liability item and its disclosed exposure amount.

3. Classify each item, in priority order, as:
   a. Letters of Credit
   b. Performance bonds
   c. Bank guarantees
   d. Corporate guarantees
   e. Unclassified contingent liability

4. Sum unique quantifiable items by type and currency.

5. Convert each unclassified item into a short factual statement with its amount.

6. Return to the user:
   a. Summary paragraph
   b. Classified summary table
   c. Unclassified items table

7. Retain item-level evidence in backend JSON only.
```
