# EIR (loan fee & cost amortisation) — captured source logic

Reference capture of the Excel logic for EIR, as supplied. **Nothing here is implemented.** This
file records what each sheet declares, in the supplier's own words, plus the questions the capture
raised — so that when it is built, it is built against a written spec rather than a reading of a
spreadsheet nobody kept.

Scope note: this is a DIFFERENT domain from the financial-statement spreading the rest of this
repo does. EIR here is the effective-interest amortisation of loan fees and transaction costs
(IFRS 9 / Ind AS 109 style), at GL × product grain. It shares no template, concept or section
vocabulary with `output_csv_hk_*`.

Status: capture in progress — sheet 1 of an unknown number received.

---

## Sheet 1 — fee / cost master: which GLs amortise under EIR, and which under SLM

A per-GL, per-product classification table. Supplied as a header-and-field-description template
with no populated rows.

| # | Column | Supplied description |
|---|---|---|
| 1 | `Reference Date` | Effective date from which the EIR computation is generated |
| 2 | `Sr. No.` | Serial number for GL codes |
| 3 | `GL Code` | General Ledger code |
| 4 | `GL Description` | Description of the GL |
| 5 | `Product Code` | Product identifier |
| 6 | `Product Description` | Product name/description |
| 7 | `Fee/Cost Type` | Fee or Transaction Cost |
| 8 | `Nature/ Details of fee / cost` | Detailed nature of the fee / cost GL |
| 9 | `EIR / SLM` | Method of amortisation of fee / cost into either SLM or EIR |

### What this sheet decides

For a given fee or transaction cost sitting in a GL, against a given product, it declares the
**amortisation method**: EIR (spread over the instrument's life via the effective interest rate)
or SLM (straight line). Column 9 is the decision; columns 3–8 are the key and its documentation.

`Reference Date` makes the classification **effective-dated** — the same GL × product can be
classified differently from different dates.

### Open questions (not blocking capture)

1. **Grain / uniqueness.** Is one row unique on (`Reference Date`, `GL Code`, `Product Code`)? Can
   one GL carry two rows for the same product and date — e.g. split by `Fee/Cost Type`?
2. **Effective dating.** Is `Reference Date` a "valid from" where the latest row at or before the
   run's as-of date wins, or is it a stamp identifying one computation run (i.e. the whole file is
   re-supplied per period)?
3. **Closed vocabularies.** Is `Fee/Cost Type` exactly {Fee, Transaction Cost}? Is `EIR / SLM`
   exactly {EIR, SLM}? Anything else to accept — blank, "NA", "Not amortised"?
4. **Unclassified GLs.** What happens to a fee/cost GL that appears in the data and not in this
   master — refuse the row, or a default method?
5. **Product hierarchy.** Is `Product Code` a leaf, or can a row be declared at a product group
   and inherited by its members?

### Not yet supplied

- The amortisation computation itself (EIR rate solve, schedule, catch-up on prepayment or
  modification).
- The transaction/loan-level input (the balances and cash flows the method is applied to).
- The output/report shape.
- Worked examples or tie-out numbers.
