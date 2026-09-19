# EIR (loan fee & cost amortisation) — captured source logic

Reference capture of the Excel logic for EIR, as supplied. **Nothing here is implemented.** This
file records what each sheet declares, in the supplier's own words, plus the questions the capture
raised — so that when it is built, it is built against a written spec rather than a reading of a
spreadsheet nobody kept.

Scope note: this is a DIFFERENT domain from the financial-statement spreading the rest of this
repo does. EIR here is the effective-interest amortisation of loan fees and transaction costs
(IFRS 9 / Ind AS 109 style), at GL × product grain. It shares no template, concept or section
vocabulary with `output_csv_hk_*`.

Status: **all three parts of the computation spec received**, plus four data/logic sheets.

* Part 1 — the build handoff: conventions, inputs, build sequence, controls. Below.
* Part 2 — [Sheet Specifications](sheet-specifications.md): per-model requirements.
* Part 3 — [Formula Blueprint](formula-blueprint.md): row-level logic per block, with the source
  workbook's own quirks recorded.

The seven source sheets part 1 refers to are all named in part 2: six models
(`EMI-based-Fixed`, `Bullet Repayment-Fixed`, `EMI based floating rate loan`,
`EMI based- Floating rate loans`, `Bullet -Floating rate loans`, `CC Loan - SLM`) plus
`Assumptions`. The four data/logic sheets captured below are separate from those seven.

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

---

## Sheet 4 — fee allocation across facilities, and progressive recognition

The first sheet with actual logic in it rather than a header row. Titled *"Illustration on fee
allocation (in case of no tagging of fees to individual facility for same borrower)"* — so it
governs the case where a fee arrives against a BORROWER and not against one facility.

### The illustration as supplied

Fact pattern — one borrower, one fee, two facilities:

| Particulars | Amount |
|---|---|
| Total fees collected from borrower | 1,000,000 |
| Facility A sanctioned amount | 600,000,000 |
| Facility B sanctioned amount | 400,000,000 |
| Total sanctioned amount | 1,000,000,000 |

Allocation: Facility A 60%, Facility B 40%.

Partial disbursement, Facility A:

| Particulars | Amount |
|---|---|
| Facility A sanctioned | 600,000,000 |
| Allocated Fee | 600,000 |
| Drawdown | 150,000,000 |
| Fee considered for EIR | 150,000 |
| Unutilised fee | 450,000 |

> Note: The unutilized fee of INR 4,50,000 shall be stored at the Facility (Loan ID) level until
> further drawdown or facility expiry.

Subsequent drawdown, Facility A:

| Particulars | Amount |
|---|---|
| Assume additional drawdown of facility A | 300,000,000 |
| Total disbursement of facility A | 450,000,000 |
| Facility A sanctioned amount | 600,000,000 |
| Allocated Fee | 600,000 |
| Total fee to be considered for EIR | 450,000 |
| Fee already considered for EIR in previous drawdowns | 150,000 |
| Additional fee to be considered in EIR | 300,000 |
| Balance unutilized fee | 150,000 |

Expiry: *"If no further drawdown occurs and the facility expires, the remaining unutilized fee of
INR 1,50,000 shall be recognized in P&L through the following accounting entry."*

### The rules this implies

Every figure above reproduces from these five, checked:

1. **Allocation is pro-rata on SANCTIONED amount**, not on drawn amount:
   `allocation% = facility sanctioned / total sanctioned across the borrower's facilities`
2. `allocated fee = total fee collected × allocation%`
3. **Fee taken into EIR is pro-rata on cumulative drawdown against sanctioned**:
   `cumulative fee in EIR = allocated fee × (cumulative drawdown / sanctioned)`
4. **Each drawdown recognises the increment, computed cumulatively** — not a per-drawdown
   calculation: `additional fee this drawdown = cumulative fee in EIR (now) − fee already in EIR`.
   That is the form in the sheet, and it is the one that cannot drift.
5. `unutilised fee = allocated fee − cumulative fee in EIR`, **held at Facility (Loan ID) level**
   until further drawdown or expiry, then to P&L.

### What this settles about the earlier sheets

* **Facility = Loan ID.** The note says so outright ("stored at the Facility (Loan ID) level"), so
  sheet 2's `Loan ID` is a facility and the EIR schedule runs per facility.
* **`CIF ID` earns its place.** An untagged fee arrives against the borrower, is allocated across
  that borrower's facilities, and `CIF ID` is the key that collects them — which is why sheet 2
  carries both it and `Loan ID`.
* **Fees ARE in scope, and their input is still missing.** This illustration is fee INCOME
  collected from a borrower. Sheet 2 carries vendor costs (DSA, legal) with a `Vedor Name` column
  and no fee figures. So either a fee input sheet has not been supplied, or sheet 2 is meant to
  serve both with the vendor column blank for fees. Unresolved.

### Open questions

1. **Unutilised fee is an asset or a liability in the meantime.** It is "stored at Facility level"
   — as what? Deferred income? The expiry entry is referenced ("the following accounting entry")
   and **was not supplied**; nor is the entry for the drawdown recognitions. Needed before any of
   this can post.
2. **Revolving facilities.** "Cumulative drawdown" is unambiguous for a term loan drawn in
   tranches. For a revolver where the borrower repays and redraws, cumulative gross drawdown can
   exceed sanctioned several times over and rule 3 would then recognise more than the allocated
   fee. Is it cumulative gross, peak outstanding, or is the ratio capped at 100%?
3. **Rounding and the residual.** 60/40 divides cleanly. Three facilities at a third each will not,
   and the allocated fees must still sum to the fee collected. Rounding rule, and which facility
   takes the residual?
4. **Sanction changes after allocation.** If Facility B is enhanced or cancelled after the fee was
   allocated, is the allocation recomputed — restating fee already taken into EIR — or fixed at the
   date of collection?
5. **Cancellation vs expiry.** The sheet covers expiry. Is a facility cancelled early, or one that
   never draws at all, treated the same way (whole allocated fee to P&L)?
6. **Does the same allocation apply to untagged COSTS?** Sheet 1 classifies both `Fee` and
   `Transaction Cost`, and this illustration only speaks of fees. If an untagged cost arrives at
   borrower level, is it allocated by the same pro-rata rule?
7. **Partial-period interaction with `Reference Date`.** A drawdown between two reference dates —
   is the increment recognised at the drawdown date or at the next reference date?

---

# The computation spec

Supplied separately from the sheets, as "EIR MODEL — LLM BUILD HANDOFF". Part 1 of 3.

> **Purpose** — Specification for recreating every model in this workbook. The seven source sheets
> remain unchanged.
>
> **Copy-ready master instruction** — Build each model as a transparent, formula-driven Excel
> schedule. Separate inputs, contractual cash flows, Ind AS carrying value, fee amortisation,
> accounting entries and validation checks. Never hard-code calculated schedule outputs.

## Part 1 — global conventions, inputs, build sequence, controls

### 1. Global conventions

| | |
|---|---|
| Perspective | Lender / financial-asset perspective |
| Initial carrying amount | Gross amount disbursed − directly attributable fees received + eligible transaction costs or commissions paid |
| Cash-flow signs | In schedules, balances and receipts are positive. **For XIRR only**, initial net disbursement is negative and subsequent borrower receipts are positive |
| Fee amortisation | EIR interest − contractual interest; this accretes the net carrying amount toward the contractual settlement amount |
| Precision | Calculate at full precision; display currency to 2 decimals. Never round intermediate rows unless policy requires it |

### 2. Required inputs

| Group | Fields |
|---|---|
| Identity | Loan ID, product type, currency |
| Dates | Start/disbursement, first payment, maturity, reset dates, reporting date, prepayment date if applicable |
| Economics | Gross disbursement, sanctioned limit where relevant, rate or benchmark plus spread, fees, eligible costs/commission, payment frequency, principal pattern |
| Conventions | Day-count basis, month-end rule, business-day rule, reset frequency, prospective/reset treatment |

### 3. Common build sequence

1. **Validate** — dates chronological; numeric rates/fees; supported frequency; principal pattern fully settles unless revolving.
2. **Generate dates** — every contractual cash-flow date through maturity, preserving month-end behaviour; calculate actual days.
3. **Contractual schedule** — opening gross; contractual interest = opening × annual rate × days/basis; principal; total receipt; closing gross.
4. **Net opening** — gross disbursement − fees received + eligible costs/commission paid.
5. **Solve EIR** — iteratively change EIR until the final EIR closing balance is zero. XIRR is an alternative *only* when its compounding convention is intended.
6. **EIR schedule** — opening net; EIR interest = opening × EIR × days/basis; contractual total receipt; closing net = opening + EIR interest − receipt.
7. **Fee roll-forward** — period amortisation = EIR interest − contractual interest; unamortised fee = prior balance − amortisation; accumulated = cumulative amortisation.
8. **Outputs** — EIR, EIR spread for floating loans, gross/net balances, fee roll-forward, modification result where applicable.

### 4. Mandatory controls

| Control | Assertion |
|---|---|
| Gross roll-forward | opening gross + contractual interest − total receipt = closing gross, every period |
| EIR roll-forward | opening net + EIR interest − total receipt = closing net, every period |
| Fee identity | period amortisation = EIR interest − contractual interest |
| Fee exhaustion | cumulative amortisation = original net fee; final unamortised fee = zero, within tolerance |
| Maturity | final gross and net balances = zero, within tolerance |
| Journals | every debit/credit block balances |
| Floating reset | the original EIR spread stays fixed unless a separate modification assessment says otherwise |

### 5. Build rules

* **Formulas** — references and copied formulas, not hard-coded outputs; assumptions separate from calculations.
* **Dynamic rows** — parameter-driven schedules over fixed row blocks; stop at maturity and label the terminal cash flow.
* **Errors** — flag missing inputs, negative days, unsupported frequency, non-zero terminal balances, fee over-amortisation, solve failure.
* **Audit trail** — expose the EIR cash-flow vector plus controls for balance, principal, fee and journal reconciliation.
* **Detail** — *Sheet Specifications* for model requirements, *Formula Blueprint* for row logic and exceptions.

### What part 1 pins down

* **Ind AS** (109) — so Indian GAAP, consistent with the INR figures in sheet 4. IFRS 9 equivalent.
* **The EIR is solved off the SCHEDULE, not off XIRR.** Step 5 root-solves the rate that drives the
  final net closing balance to zero, where each period accrues `opening × EIR × days/basis` — simple
  accrual over actual days. XIRR compounds. **These give different rates**, and the spec is explicit
  that XIRR is a fallback "only when its compounding convention is intended". Worth stating plainly
  because it is the kind of difference that reconciles to nothing later.
* **"Original net fee"** is named by the fee-exhaustion control and is the quantity in step 4:
  fees received − eligible costs paid. That is what must fully amortise.
* **Floating loans carry a fixed EIR spread**, re-derived at inception and held across resets; only
  a modification assessment may change it.

### Open questions

1. **Is the deliverable Excel, or this product?** The master instruction says "Build each model as a
   transparent, formula-driven **Excel** schedule", with build rules about copied formulas, dynamic
   row blocks and an audit trail of exposed cells. Read literally it specifies a workbook, not a
   Python service. Everything else in this repository is the latter. Which is wanted — a generated
   workbook, an implementation whose outputs tie to one, or both — changes the design completely,
   and it is the first thing to settle.
2. **Sign of the amortisation when costs exceed fees.** Step 4 makes the net opening
   `gross − fees + costs`, so with costs greater than fees the net opening is ABOVE gross, the EIR
   is BELOW the contractual rate, and `EIR interest − contractual interest` is negative — the
   carrying amount amortises down rather than accreting. The convention says "accretes toward",
   which reads as one direction. Confirm both are intended and that no control assumes positivity.
3. **Tolerance is required but not quantified.** Three controls say "within tolerance" (fee
   exhaustion, maturity balances) and step 5 needs a convergence criterion. Absolute, relative, or
   currency units — and what value?
4. **Day-count basis is an input, not a fixed convention** (30/360, actual/365, actual/actual …),
   and step 2 says "calculate actual days" while step 3 divides by "basis". The supported set needs
   naming, and whether month-end and business-day rules shift the cash-flow DATE, the day count, or
   both.
5. **"Principal pattern fully settles unless revolving"** — so revolving facilities are in scope
   here too, and the maturity control ("final balances zero") cannot apply to them. What replaces it?
   This is the same gap sheet 4's cumulative-drawdown rule left open.
6. **Prepayment** appears in the required inputs and in step 8's "modification result", but no rule
   is given for catch-up on prepayment or for the modification test itself. Presumably in parts 2–3.

---

## Part 2 and 3 — synthesis

Captured verbatim in [sheet-specifications.md](sheet-specifications.md) and
[formula-blueprint.md](formula-blueprint.md). What follows is what they establish.

### The six models

| Model | Rate | Principal pattern | Basis | Fee method | Distinctive |
|---|---|---|---|---|---|
| EMI-based-Fixed | fixed | level EMI | ACT/365 | EIR | journals, prepayment, partial drawdown; 54 rows |
| Bullet Repayment-Fixed | fixed | bullet | **ACT/360** | EIR | 12 periods; terminal receipt = interest + full principal |
| EMI based floating rate loan | floating, quarterly reset | level EMI | source mixes /360 and /365 — **rebuild on one** | EIR | commission netting, reporting cut-offs, ROI/EIR analytics |
| EMI based- Floating rate loans | floating | equal annual principal | ACT/365 | EIR | modification result |
| Bullet -Floating rate loans | floating | bullet | ACT/365 | EIR | modification result |
| CC Loan - SLM | — | cash-credit utilisation | ACT/365 | **SLM** | day-weighted fee |

### Formulas the three parts settle

**SLM now has a formula**, which closes the loop on sheets 1 and 3 — they decide EIR *or* SLM, and
until now only EIR had a method:

    total days   = maturity − facility start
    period fee   = original fee × period days / total days
    unamortised  = prior balance − period fee

Day-weighted straight line, not calendar-month straight line.

**The floating-rate reset does NOT re-solve the EIR.** This is the most consequential formula in
the spec:

    original contractual rate = benchmark + spread
    original EIR              = solved to zero final net balance
    EIR spread                = original EIR − original contractual rate     ← fixed for life
    ── at reset ──
    new contractual rate      = new benchmark + ORIGINAL spread
    new EIR                   = new contractual rate + ORIGINAL EIR spread

So the EIR is shifted by a constant, never re-solved. That is what part 1's "original EIR spread
stays fixed unless a separate modification assessment says otherwise" means in arithmetic.

**Prepayment** — book the current period's accrual and amortisation, then recognise the *entire*
remaining unamortised fee in P&L at once. Control: post-prepayment fee = 0.

**Partial drawdown** — `fee allocated = total fee × disbursed / sanctioned`, balance retained for
later drawdown, `allocated + retained = total fee`. The same rule as the fee-allocation
illustration (sheet 4), independently stated, which is a useful corroboration.

**Reporting cut-off** — cumulative amortisation to the reporting date, with a straddling period
prorated by elapsed days and the remainder carried. Control: amortised + unamortised = net fee.
This answers what the sheets' `Reference Date` does to a mid-period drawdown.

**Net fee** — `fee received − commission paid`, named on the floating EMI sheet and consistent with
part 1's initial carrying amount.

**Analytics** — ROI income = contractual interest; EIR income = ROI + amortisation; implied rate =
income / opening × basis / days; spread = EIR rate − ROI. Control: EIR income − ROI = amortisation.

**Journals**, by name: loan recognition, fee receipt, fee deferral, EIR amortisation, interest
accrual, instalment receipt, presentation reclasses. Named — the debit/credit lines themselves are
still not supplied.

### What the spec says to FIX rather than reproduce

This is a remediation spec, not a faithful-reproduction spec, and it is explicit about it:

* `EMI-based-Fixed` — "**Replace source terminal manual interest adjustment with a visible true-up
  policy**". The source plugs its last period by hand.
* `EMI based floating rate loan` — "Source mixes ACT/360 and ACT/365 and fixed row blocks. Rebuild
  parameterically and explicitly define the basis for each calculation."
* `Bullet Repayment-Fixed` — "Source uses `EDATE` and inclusive adjustments in later rows. Document
  inclusive-day policy."
* `Assumptions` — "Do not apply the generic bullet/fixed/ACT-360 set to sheets that explicitly use
  floating rates or another basis." The generic assumptions sheet **contradicts** the specific ones.
* `EMI-based-Fixed` and `CC Loan - SLM` — "B5 currently equals 2% of B8": the fee is a formula off
  the gross amount, i.e. an input that is actually derived. Whether it stays derived or becomes a
  true input needs deciding.

Anything built from this should therefore be validated against the spec's controls, **not** against
the source workbook's numbers — the two are expected to differ where the source is wrong.

### Open questions

Superseding the earlier lists where they overlap.

1. **Excel or this product?** Still the first thing to settle, and parts 2–3 sharpen it rather than
   answer it: the blueprint is keyed by literal cell ranges (`B4:B11`, `A18:B71`, `J14`) and names
   `EOMONTH`, `EDATE`, `PMT` and Goal Seek. That is a workbook specification. If the deliverable is
   a Python service, the ranges are provenance for the logic and nothing more; if it is a generated
   workbook, they are the layout contract. Radically different builds.
2. **Reset versus modification — the spec appears to do both, and doing both double-counts.** On the
   two floating sheets it (a) rolls the carrying amount prospectively at the new EIR, and (b)
   computes a modification gain/(loss) as `PV of revised future cash flows − pre-reset carrying
   value`. Under Ind AS 109 B5.4.5 a benchmark reset on a floating-rate instrument is handled
   prospectively, with no gain or loss; a modification is a separate event, measured by discounting
   at the **original** EIR. Recognising a P&L difference at every reset *and* rolling forward at the
   new EIR would take the same economics twice. Which is intended?
3. **The modification discount rate is never stated.** "PV date and discount rate explicit" is
   listed as a *validation* — i.e. you must declare it — and no part of the spec declares it.
   Original EIR, revised EIR, or the new contractual rate? The answer changes the gain/loss.
4. **The controls do not all apply to every model.** `CC Loan - SLM` has no EIR at all, so part 1's
   "EIR roll-forward" and "fee identity = EIR interest − contractual interest" cannot hold for it.
   The ALL MODELS control block says "all *applicable* controls" — the applicability matrix needs
   writing down rather than inferring.
5. **Revolving.** Part 1 admits a principal pattern that does not settle "unless revolving", and
   `CC Loan - SLM` is a cash-credit facility, but the maturity control ("final gross and net = 0")
   cannot apply to a revolver. What replaces it? Same gap the fee-allocation illustration left on
   cumulative drawdown.
6. **Tolerance is required by five controls and quantified by none.** Plus the Goal Seek convergence
   criterion. Absolute, relative, or currency units — and what value?
7. **`CC Loan - SLM`: the fee period is not the utilisation period.** Five months' utilisation
   against a 366-day fee period. Confirm the fee amortises over the *facility* life regardless of
   utilisation, and what happens if the facility is closed early.
8. **Sign of the amortisation when costs exceed fees** — carried forward from part 1 and still open.
   With commission netting on the floating EMI sheet, `net fee = fee − commission` can be negative.

---

### Not yet supplied

- The amortisation computation itself (EIR rate solve, schedule, catch-up on prepayment or
  modification).
- The loan's own terms and cash flows — sheet 2 gives the cost to amortise but not the balance,
  rate, tenor or repayment schedule to amortise it over.
- The fee INCOME side, if it is amortised (sheet 1 admits `Fee`; sheet 2 carries only vendor costs).
- The ACCOUNTING ENTRIES. Sheet 4 names one ("through the following accounting entry") and does
  not give it, and none is supplied for the drawdown recognitions or for the unutilised-fee
  holding account.
- The fee INCOME input file — sheet 4 proves fees are in scope, and no sheet carries fee amounts.
- The output/report shape.
- Worked examples for the EIR computation itself. Sheet 4's allocation arithmetic is worked and
  reproduces exactly; the effective-interest schedule it feeds is not.
- **The journal entries themselves.** Named on `EMI-based-Fixed` (loan recognition, fee receipt,
  fee deferral, EIR amortisation, interest accrual, instalment receipt, presentation reclasses) and
  referenced by the fee-allocation illustration, but no debit/credit lines are given anywhere — and
  a control says every block must balance.
- **The source workbook.** Everything captured here is the SPEC; the seven sheets' actual numbers
  have not been supplied, so nothing can be tied out yet.
- **The fee income input file** — sheet 4 proves fees are in scope and no sheet carries fee amounts.
- The applicability matrix of controls to models.
- Tolerances.
