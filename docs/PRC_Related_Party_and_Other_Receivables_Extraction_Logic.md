# PRC Related-Party Receivables and Other Receivables Extraction Logic

## 1. Purpose

Build AI-based extraction and computation logic for the following fields from PRC filings prepared under Chinese Accounting Standards for Business Enterprises:

1. `Due from Related Parties (LTP)`
2. `Other Receivables (CP)`

The process must:

1. Identify the relevant Chinese financial statement sections and notes.
2. Extract atomic net balances and related-party components.
3. Avoid double counting across statements, notes, subtotals, and repeated disclosures.
4. Compute three independent candidates for `Due from Related Parties (LTP)`.
5. Select the highest valid candidate.
6. Compute `Other Receivables (CP)` by deducting only the related-party amounts included in its own gross input.

---

## 2. Processing Order

Calculate the fields in this sequence:

```text
1. Due from Related Parties (LTP)
2. Other Receivables (CP)
```

However, retain atomic related-party amounts by source line item. Do not use the entire final `Due from Related Parties (LTP)` value as the deduction for `Other Receivables (CP)` because the final LTP value may include amounts outside the CP gross pool.

---

## 3. Common Extraction Rules

### 3.1 Filing scope

- Process PRC filings prepared under `企业会计准则` or `中国企业会计准则`.
- Search the financial statements and notes in Simplified Chinese.
- Preserve the reported currency and unit.
- Normalize units before addition, subtraction, or comparison.
- Retain source page, section, original label, value, currency, unit, and evidence text.

### 3.2 Net amount rule

Extract the net carrying amount after applicable impairment or loss allowance.

Preferred order:

```text
1. Explicit net carrying amount / 账面价值 / 净额
2. Closing balance less loss allowance or bad-debt provision
3. Statement amount where the statement presents the net carrying amount
```

Examples:

```text
净额
账面价值
期末账面价值
期末余额减坏账准备
账面余额减信用损失准备
账面余额减损失准备
```

Do not add gross balance and net balance together.

### 3.3 Related-party identification

Treat an amount as related-party only when the filing explicitly identifies the counterparty or balance as related-party, such as:

```text
关联方
关联单位
关联企业
关联公司
母公司
子公司
联营企业
合营企业
受同一控制方控制的企业
其他关联方
应收关联方款项
关联方应收款项
关联方资金往来
关联方往来款
```

Do not infer related-party status solely from a counterparty name.

### 3.4 Exclusion

Exclude entrusted loans from all related-party calculations:

```text
委托贷款
委托借款
委托银行贷款
```

If a disclosed total combines entrusted loans with other related-party receivables and the entrusted-loan component cannot be separated, mark the candidate as not computable.

### 3.5 Duplicate control

- Treat the same balance repeated in the balance sheet, a detailed note, and the related-party note as separate candidate evidence, not additive evidence.
- Within each candidate, count each underlying receivable once.
- Do not add a subtotal to its components.
- Do not add debtor-level rows to a matching reported total.
- Use stable duplicate keys based on line item, counterparty, amount, and source context.
- If the same related-party balance appears under two source notes but represents the same underlying receivable, retain one value within that candidate.

### 3.6 Missing and zero values

- Return `null` when a value cannot be found or computed.
- Do not convert missing values to zero.
- Use zero only when explicitly reported or when all relevant notes have been searched and explicitly contain no qualifying amount.

---

# 4. Due from Related Parties (LTP)

## 4.1 Target concept

Extract net receivable balances attributable to related parties from the following underlying receivable classes, excluding entrusted loans:

```text
其他应收款项
其他应收款
一年内到期的长期应收款
长期应收款
发放贷款及垫款
贷款及垫款
```

The final value is the highest valid amount among three independently calculated candidates.

---

## 4.2 Candidate 1: Balance Sheet and Statement-Linked Disclosures

### Search locations

Search:

```text
资产负债表
合并资产负债表
流动资产
非流动资产
```

Search the statement line items and directly linked breakdowns for:

```text
其他应收款项
其他应收款
一年内到期的非流动资产
一年内到期的长期应收款
长期应收款
发放贷款及垫款
贷款及垫款
```

### Extraction rule

Extract only the net related-party amounts explicitly identified within these line items.

```text
Find_1_components = unique net related-party balances from the statement or statement-linked disclosures

Find_1 = SUM(Find_1_components excluding entrusted loans)
```

If `一年内到期的非流动资产` is reported as a combined total, extract only the component explicitly attributable to `一年内到期的长期应收款`. Do not use the entire combined total.

---

## 4.3 Candidate 2: Receivable Notes

### Search note headings

```text
其他应收款项
其他应收款
一年内到期的非流动资产
一年内到期的长期应收款
长期应收款
发放贷款及垫款
贷款及垫款
```

### Search breakdowns within the notes

```text
按欠款方归集的期末余额
按欠款方归集的期末余额前五名
按款项性质分类
关联方组合
关联方款项
应收关联方款项
关联方余额
关联方往来
```

### Extraction rule

Extract and sum the unique net related-party amounts from the receivable notes.

```text
Find_2_components = unique net related-party balances in the specified receivable notes

Find_2 = SUM(Find_2_components excluding entrusted loans)
```

Where only gross debtor balances and a pooled loss allowance are available, do not arbitrarily allocate the pooled allowance to related parties. Use the gross related-party balance only if the note explicitly states that the amount is already net or that no allowance applies. Otherwise flag the component as `NET_AMOUNT_NOT_DERIVABLE`.

---

## 4.4 Candidate 3: Related Parties and Related-Party Transactions Note

### Search note headings

```text
关联方及关联交易
关联方关系及其交易
关联方交易
关联方往来
关联方应收应付款项
关联方余额
应收关联方款项
```

### Search line items

Within the related-party note, extract receivable balances corresponding to:

```text
其他应收款项
其他应收款
一年内到期的长期应收款
长期应收款
发放贷款及垫款
贷款及垫款
其他应收关联方款项
应收关联方款项
```

### Extraction rule

```text
Find_3_components = unique net related-party receivable balances in the related-party note

Find_3 = SUM(Find_3_components excluding entrusted loans)
```

Do not include:

```text
应付关联方款项
关联方应付款项
预收款项
合同负债
关联方投资 balances
担保金额 unless it is an actual receivable balance
Transaction volume without an outstanding receivable balance
```

---

## 4.5 Final Computation

A candidate is valid only when:

```text
The source location was found
The qualifying components were extracted on a net basis
Entrusted loans were excluded
Currency and unit are known or normalized
The candidate contains no unresolved duplicate
```

Calculate:

```text
Due_from_Related_Parties_LTP = MAX_VALID(Find_1, Find_2, Find_3)
```

Tie handling:

```text
IF two or more valid candidates have the same maximum value:
    Select the value once
    Retain all tied candidates as supporting sources
```

Do not add `Find_1`, `Find_2`, and `Find_3`. They are alternative measurements of the same target concept.

### Suggested candidate output

```json
{
  "find_1": null,
  "find_2": null,
  "find_3": null,
  "selected_value": null,
  "selected_candidate": null,
  "supporting_candidate_ids": [],
  "excluded_entrusted_loans": [],
  "source_record_ids": [],
  "qa_flags": []
}
```

---

# 5. Other Receivables (CP)

## 5.1 Target concept

Calculate the net balance of the following in-scope receivable classes, less the net related-party amounts included within those same classes:

```text
其他应收款项
其他应收款
一年内到期的长期应收款
一年内到期的贷款及垫款
拆出资金
往来款
```

The output must exclude related-party components but must not deduct related-party balances belonging to line items outside this list.

---

## 5.2 Identify Notes and Extract Gross Pool

### Search note headings

```text
其他应收款项
其他应收款
一年内到期的非流动资产
一年内到期的长期应收款
一年内到期的贷款及垫款
发放贷款及垫款
拆出资金
往来款
其他流动资产
```

### Atomic gross values

Extract the net carrying amount for each in-scope class:

```text
other_receivables_net
current_long_term_receivables_net
current_loans_and_advances_net
funds_placed_net
current_account_receivables_net
```

Calculate:

```text
CP_Gross =
    other_receivables_net
  + current_long_term_receivables_net
  + current_loans_and_advances_net
  + funds_placed_net
  + current_account_receivables_net
```

Only add a component when the filing separately identifies it. Do not add an `其他流动资产` total if the qualifying components within it have already been extracted.

---

## 5.3 Extract Related-Party Deduction Within the Gross Pool

For each gross component, extract the net related-party amount included in that same component:

```text
other_receivables_related_party_net
current_long_term_receivables_related_party_net
current_loans_and_advances_related_party_net
funds_placed_related_party_net
current_account_receivables_related_party_net
```

Calculate:

```text
CP_Related_Party_Deduction =
    other_receivables_related_party_net
  + current_long_term_receivables_related_party_net
  + current_loans_and_advances_related_party_net
  + funds_placed_related_party_net
  + current_account_receivables_related_party_net
```

Important:

- Deduct only related-party amounts proven to be included in `CP_Gross`.
- Do not deduct the entire `Due from Related Parties (LTP)` final value.
- Do not deduct `长期应收款` unless the amount is specifically classified as `一年内到期的长期应收款` and included in `CP_Gross`.
- Do not deduct non-current loans and advances.
- Do not deduct entrusted loans merely because entrusted loans were excluded from the LTP field. For this CP field, follow the specified gross pool and deduct only qualifying related-party amounts included in that pool.

---

## 5.4 Final Computation

```text
Other_Receivables_CP_candidate =
    CP_Gross
  - CP_Related_Party_Deduction
```

Final rule:

```text
IF Other_Receivables_CP_candidate >= 0:
    Other_Receivables_CP = Other_Receivables_CP_candidate
ELSE:
    Other_Receivables_CP = null
    raise NEGATIVE_RESIDUAL
```

A negative result indicates an extraction, scope, unit, or duplication issue. Do not automatically set a negative result to zero.

### Suggested final output

```json
{
  "cp_gross": null,
  "cp_related_party_deduction": null,
  "value": null,
  "formula": "CP_Gross - CP_Related_Party_Deduction",
  "gross_component_record_ids": [],
  "deduction_component_record_ids": [],
  "status": null,
  "qa_flags": []
}
```

---

# 6. Shared Atomic Extraction Record

Return one record for every extracted component:

```json
{
  "record_id": "record_001",
  "target_field": "Due from Related Parties (LTP)",
  "candidate": "Find_2",
  "source_note_original": "其他应收款",
  "line_item_original": "应收关联方款项",
  "counterparty_original": null,
  "receivable_class": "other_receivables",
  "related_party": true,
  "entrusted_loan": false,
  "gross_or_net": "net",
  "value_reported": null,
  "currency": null,
  "unit": null,
  "page": null,
  "evidence_text": null,
  "duplicate_group_id": null,
  "confidence": null
}
```

---

# 7. Formula Summary

```text
DUE FROM RELATED PARTIES (LTP)

Find 1 =
    Sum of unique net related-party balances identified in the balance sheet
    and statement-linked disclosures for the specified receivable classes,
    excluding entrusted loans

Find 2 =
    Sum of unique net related-party balances identified in the specified
    receivable notes, excluding entrusted loans

Find 3 =
    Sum of unique net related-party receivable balances identified in the
    related-party note, excluding entrusted loans

Due from Related Parties (LTP) = MAX_VALID(Find 1, Find 2, Find 3)


OTHER RECEIVABLES (CP)

CP Gross =
    Other receivables net
  + Current portion of long-term receivables net
  + Current portion of loans and advances net
  + Funds placed net
  + Current-account receivables net

CP Related-Party Deduction =
    Related-party net amounts included within those same CP Gross components

Other Receivables (CP) =
    CP Gross - CP Related-Party Deduction
```

---

# 8. Pseudocode

```python
def sum_or_null(values):
    available = [value for value in values if value is not None]
    return sum(available) if available else None


def max_valid(candidates):
    valid = {
        name: value
        for name, value in candidates.items()
        if value is not None and value >= 0
    }
    if not valid:
        return None, None, []

    selected_value = max(valid.values())
    tied_sources = [
        name for name, value in valid.items()
        if value == selected_value
    ]
    return selected_value, tied_sources[0], tied_sources


find_1 = sum_or_null(deduplicate(find_1_related_party_net_values))
find_2 = sum_or_null(deduplicate(find_2_related_party_net_values))
find_3 = sum_or_null(deduplicate(find_3_related_party_net_values))

(
    due_from_related_parties_ltp,
    selected_candidate,
    supporting_candidates,
) = max_valid({
    "Find_1": find_1,
    "Find_2": find_2,
    "Find_3": find_3,
})

cp_gross = sum_or_null(deduplicate([
    other_receivables_net,
    current_long_term_receivables_net,
    current_loans_and_advances_net,
    funds_placed_net,
    current_account_receivables_net,
]))

cp_related_party_deduction = sum_or_null(deduplicate([
    other_receivables_related_party_net,
    current_long_term_receivables_related_party_net,
    current_loans_and_advances_related_party_net,
    funds_placed_related_party_net,
    current_account_receivables_related_party_net,
]))

if cp_gross is None:
    other_receivables_cp = None
    cp_status = "NOT_COMPUTABLE"
else:
    # A proven search with no included related-party balance may supply zero.
    deduction = cp_related_party_deduction
    if deduction is None:
        other_receivables_cp = None
        cp_status = "RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED"
    else:
        candidate = cp_gross - deduction
        if candidate >= 0:
            other_receivables_cp = candidate
            cp_status = "COMPUTED"
        else:
            other_receivables_cp = None
            cp_status = "NEGATIVE_RESIDUAL"
```

---

# 9. Required QA Flags

```text
MISSING_NOTE
MISSING_VALUE
NET_AMOUNT_NOT_DERIVABLE
ENTRUSTED_LOAN_NOT_SEPARABLE
RELATED_PARTY_STATUS_NOT_EXPLICIT
RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED
DEDUCTION_NOT_PROVEN_INCLUDED
UNIT_MISMATCH
CURRENCY_MISMATCH
POSSIBLE_DUPLICATE
PARENT_COMPONENT_OVERLAP
NEGATIVE_RESIDUAL
LOW_CONFIDENCE_MAPPING
NOT_COMPUTABLE
```
