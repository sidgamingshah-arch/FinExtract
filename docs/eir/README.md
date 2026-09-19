# EIR (loan fee & cost amortisation) — captured source logic

Reference capture of the Excel logic for EIR, as supplied. **Nothing here is implemented.** This
file records what each sheet declares, in the supplier's own words, plus the questions the capture
raised — so that when it is built, it is built against a written spec rather than a reading of a
spreadsheet nobody kept.

Scope note: this is a DIFFERENT domain from the financial-statement spreading the rest of this
repo does. EIR here is the effective-interest amortisation of loan fees and transaction costs
(IFRS 9 / Ind AS 109 style), at GL × product grain. It shares no template, concept or section
vocabulary with `output_csv_hk_*`.

Status: capture in progress — sheets 1, 2 and 3 of an unknown number received.

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

---

## Sheet 2 — the cost data itself, at loan level

Where sheet 1 classifies, this one carries the amounts. Supplied as a header-and-field-description
template with no populated rows.

| # | Column | Supplied description |
|---|---|---|
| 1 | `Reference Date` | Effective date from which the EIR computation is generated |
| 2 | `Sr. No.` | Serial number for GL codes |
| 3 | `CIF ID` | *(no description supplied)* |
| 4 | `Loan ID` | *(no description supplied)* |
| 5 | `GL Code` | General Ledger code |
| 6 | `GL Description` | Description of the GL |
| 7 | `Product Code` | Product identifier |
| 8 | `Product Description` | Product name/description |
| 9 | `Vedor Name` *(sic — "Vendor Name" in the field list)* | Name of vendor to whom paid |
| 10 | `Nature of cost` | Detailed nature of the cost like DSA expnes, Legal expense |
| 11 | `Amount` | *(no description supplied)* |
| 12 | `EIR / SLM` | Method of ammortisation of fee / cost into either SLM or EIR |

### How it differs from sheet 1

Sheet 1 is a **master**, keyed by GL × product, that decides a method. Sheet 2 is **data**, keyed
down to the individual loan, that carries a cost to amortise. Three of its columns are new and
are exactly the three the field list does not describe:

* `CIF ID` — the customer (Customer Information File identifier), so costs roll up per borrower;
* `Loan ID` — the instrument the cost attaches to, which is the grain the EIR schedule runs at;
* `Amount` — the figure being amortised.

Two columns changed rather than appeared: `Vedor Name` and `Nature of cost` are new here, and
sheet 1's `Fee/Cost Type` is **absent**. With a vendor column, an amount paid, and the examples
given (DSA expense, legal expense), sheet 2 reads as the **transaction-cost** side only — costs
paid to third parties at origination. If fee INCOME is amortised too, its data is not in this
sheet.

### The method is stated here as well as on sheet 1

*(Superseded by "The three sheets declare the same decision at three grains" below, once sheet 3
arrived and made this a three-rung cascade rather than a straight conflict. Kept because the
failure modes it names are still the ones to design against.)*

`EIR / SLM` appears on BOTH sheets. Either

* sheet 1 is authoritative and sheet 2's column is a denormalised copy looked up from it — in
  which case the two can disagree in a supplied file, and something has to decide which wins and
  say so; or
* sheet 2 is authoritative per row and sheet 1 is reference/defaults — in which case a row may
  legitimately depart from its GL's classification, and sheet 1 cannot be used to validate it.

These are not interchangeable and the answer is not inferable from the headers. Recorded here
rather than guessed.

### Further open questions

1. **Grain.** Is a row unique on (`Reference Date`, `Loan ID`, `GL Code`)? Can one loan carry two
   rows for the same GL — two invoices from different vendors for the same nature of cost?
2. **`Amount` sign and units.** Is a cost positive or negative? Which currency, and is it ever
   other than the loan's own?
3. **`Sr. No.`** is described as "serial number for GL codes" on both sheets, but here the row is a
   loan-level cost rather than a GL. Is it a line number within the file, or still a GL serial
   carried over from sheet 1?
4. **`CIF ID` vs `Loan ID`.** Is `CIF ID` derivable from `Loan ID`, or can a cost be supplied
   against a customer with no loan named?
5. **Costs with no loan.** Can a cost arrive at product level and need allocating across that
   product's loans, or is `Loan ID` always populated?

---

## Sheet 3 — the product-level default

The same decision again, at the broadest grain. Supplied as a header-and-field-description
template with no populated rows.

| # | Column | Supplied description |
|---|---|---|
| 1 | `Reference Date` | Effective date from which the EIR computation is generated |
| 2 | `Sr. No.` | Serial number |
| 3 | `Product Code` | Product identifier |
| 4 | `Product Description` | Product name/description |
| 5 | `EIR / SLM` | Method of ammortisation of fee / cost into either SLM or EIR |

Note `Sr. No.` is described here as plain "Serial number", where sheets 1 and 2 both say "Serial
number for GL codes" — consistent with there being no GL on this sheet, and evidence that the
description on sheet 2 (a loan-level row, not a GL) was carried over rather than meant.

---

## The three sheets declare the same decision at three grains

`EIR / SLM` is stated on all three, each time keyed more narrowly than the last:

| Sheet | Key | Grain |
|---|---|---|
| 3 | `Product Code` | broadest — one method per product |
| 1 | `GL Code` × `Product Code` | a GL's method within a product |
| 2 | `Loan ID` (× `GL Code`, × vendor) | narrowest — one method per supplied cost row |

Read together these are almost certainly a **precedence cascade**: the product sets a default, a
GL refines it, and a transaction row may state its own. That is the same shape as this repo's
`section_defaults` → per-concept override, and it would make the redundancy intentional rather
than a denormalisation hazard.

**This supersedes the two-way framing recorded under sheet 2 above** — the question is not which
of two sheets wins, but whether the cascade runs narrowest-wins as it appears to, and what happens
at each rung when it is silent or contradicted:

1. **Is narrowest-wins right?** Sheet 2 beats sheet 1 beats sheet 3 — confirm, because the reverse
   (a product-level mandate that overrides a row) is a legitimate policy too.
2. **Does a rung have to be present?** If a loan's product is absent from sheet 3 but its GL is on
   sheet 1, does the run proceed on sheet 1 alone, or is the missing product an error?
3. **Is disagreement an error or an override?** A sheet-2 row saying SLM where sheet 1 says EIR for
   its GL: is that the row exercising its right to differ, or a data fault to report? These need
   different code and different screens.
4. **Effective dating across rungs.** Each sheet carries its own `Reference Date`. Are the three
   read at one as-of date, or can a product's classification be dated differently from the GL
   classification that refines it?

### Not yet supplied

- The amortisation computation itself (EIR rate solve, schedule, catch-up on prepayment or
  modification).
- The loan's own terms and cash flows — sheet 2 gives the cost to amortise but not the balance,
  rate, tenor or repayment schedule to amortise it over.
- The fee INCOME side, if it is amortised (sheet 1 admits `Fee`; sheet 2 carries only vendor costs).
- The output/report shape.
- Worked examples or tie-out numbers.
