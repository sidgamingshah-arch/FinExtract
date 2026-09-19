# Part 3 — Formula Blueprint

Row-level logic per source sheet, as supplied. The `SEED / EXCEPTION` column is where the source
workbook's own quirks are recorded — several are defects the spec says to REPLACE rather than
reproduce; see "What the spec says to fix" in `README.md`.

## EMI-based-Fixed

| Block | Field | Formula / algorithm | Seed / exception | Validation |
|---|---|---|---|---|
| B4:B11 | Inputs | Contractual rate, fee, start/end dates, gross amount, frequency, EMI and loan ID. | B5 currently equals 2% of B8. | End date after start; inputs numeric. |
| A18:B71 | Dates / days | First scheduled month-end; next date = `EOMONTH(previous,1)`; days = current date − prior date/start date. | Pattern: `A19=EOMONTH(A18,1)`; `B19=A19-A18`. | Days positive; last date = maturity. |
| C18:G71 | Contractual schedule | Opening = prior closing; interest = opening × B4 × days / 365; principal = B10 − interest; total = interest + principal; closing = opening + interest − total. | First opening = B8. **Replace source terminal manual interest adjustment with a visible true-up policy.** | Principal total = gross; final closing = 0. |
| H18:L71 | EIR schedule | First opening = gross − fee; then prior net closing. Interest = opening × J14 × days / 365; principal/total follow contractual schedule; closing = opening + interest − total. | EIR at J14. | Goal Seek final L71 to 0 by changing J14. |
| M18:O71 | Fee roll-forward | Amortisation = EIR interest − contractual interest; unamortised = prior balance − amortisation; accumulated = cumulative amortisation. | First unamortised = fee − first amortisation. | M total and final O = fee; final N = 0. |
| A73:E114 | Journals | Paired entries for loan recognition, fee receipt, fee deferral, EIR amortisation, interest accrual, instalment receipt and presentation reclasses. | Incremental EIR may equal current cumulative less prior cumulative. | Each narration block balances. |
| A117:E131 | Prepayment | Book current accrual/amortisation and immediately recognise the full remaining unamortised fee in P&L. | Source uses N32 for remaining fee in scenario. | Post-prepayment fee = 0. |
| A135:D150 | Partial drawdown | Fee allocated to drawdown = total fee × disbursed amount / sanctioned amount; retain balance for later drawdown. | Periodic amortisation follows allocated fee only. | Allocated + retained = total fee. |

## Bullet Repayment-Fixed

| Block | Field | Formula / algorithm | Seed / exception | Validation |
|---|---|---|---|---|
| A15:B26 | Dates / days | Generate monthly dates; calculate actual days using declared ACT/360 convention. | Source uses `EDATE` and inclusive adjustments in later rows. | Document inclusive-day policy. |
| C15:G26 | Contractual schedule | Interest = opening × B2 × days / 360; principal = 0 before maturity and B6 at maturity; total = interest + principal; closing roll-forward. | Terminal principal = B6. | Final closing = 0. |
| H15:N26 | EIR / fee schedule | Initial net = B6−B3; EIR interest = opening × J11 × days / 360; same cash receipts; fee amortisation = EIR interest − contractual interest; fee rolls down. | Opening fee at N14. | Goal Seek L26=0; M total=B3; final N=0. |

## EMI based floating rate loan

| Block | Field | Formula / algorithm | Seed / exception | Validation |
|---|---|---|---|---|
| C11:C16 | Initial economics | Net carrying amount = gross − upfront fee + commission paid; EMI derived from contractual rate, tenor and amount. | Source `D14=C11−C14+C16`; PMT at E15. | Net fee = fee − commission. |
| B25:G105 | Contractual monthly schedule | For each row, map reset period; interest = opening gross × applicable rate × days/basis; principal = EMI − interest; total = EMI; closing roll-forward. | **Source switches quarter rate cells and mixes /360 and /365.** | One explicit policy basis in rebuild. |
| I25:P105 | EIR monthly schedule | Opening net; EIR interest using rate applicable to reset block; principal = receipt − EIR interest; closing = opening + interest − receipt; amortisation = EIR interest − contractual interest. | Initial opening = D14. | Final net balance and fee = 0. |
| Q:R and U:V | Reporting cut-off | Cumulative amortisation to reporting date; prorate a straddling period by elapsed days and carry remainder. | Source contains quarter-end partial-period allocations. | Amortised + unamortised = net fee. |
| X:AC | Analytics | ROI income = contractual interest; EIR income = ROI + amortisation; implied rate = income/opening × basis/days; spread = EIR rate − ROI. | Percentages remain numeric. | EIR income − ROI income = amortisation. |

## EMI based- Floating rate loans

| Block | Field | Formula / algorithm | Seed / exception | Validation |
|---|---|---|---|---|
| B4:B13 | Origination | Contractual rate = benchmark + spread; annual principal = gross / number of annual repayments. | `B8=SUM(B6:B7)`; `B13=B4/5`. | Rate components reconcile. |
| A20:G25 | Original contractual | Interest = opening gross × B8 × days / 365; principal = annual principal; total = interest + principal; closing roll-forward. | Opening row has no cash flow. | Final gross = 0. |
| I20:O25 | Original EIR | Initial net = gross − fee; EIR interest = opening × K17 × days / 365; receipts follow contractual; amortisation = EIR interest − contractual interest. | EIR spread `K16=K15−B8`. | Final net and fee = 0. |
| D4:F4 / K29 | Reset | New contractual rate = revised benchmark + original spread; new EIR = new contractual rate + original EIR spread. | `F4=E4+B7`; `K29=E4+B7+K16`. | EIR spread unchanged. |
| A32:M36 | Revised schedule | Rebuild future contractual receipts with revised rate and roll the EIR carrying amount prospectively using revised EIR. | Reset opening PV is shown in I32/M32. | Schedule ends at zero. |
| B41:B43 | Modification | Source presentation: modification gain/(loss) = PV of revised future cash flows − pre-reset carrying value. | `B43=B42−B41`. | Label sign policy and PV date. |

## Bullet -Floating rate loans

| Block | Field | Formula / algorithm | Seed / exception | Validation |
|---|---|---|---|---|
| B4:B13 | Origination | Contractual rate = benchmark + spread; principal pattern = bullet; net opening = gross − fee. | `B8=SUM(B6:B7)`. | Rate components reconcile. |
| A20:G25 | Original contractual | Interest = gross × contractual rate × days / 365; principal = 0 until maturity then full gross; total receipt = interest + principal. | Terminal `E25=B4`. | Final gross = 0. |
| I20:O25 | Original EIR | EIR interest = opening net × EIR × days / 365; same contractual receipts; amortisation = EIR interest − contractual interest. | EIR spread = EIR − contractual rate. | Final net and fee = 0. |
| D4:F4 / K29 | Reset | New rate = new benchmark + original spread; new EIR = new rate + original EIR spread. | Same framework as floating EMI. | Original EIR spread preserved. |
| A32:M36 | Revised bullet | Apply revised rate to interest-only rows, repay full principal at maturity, and roll revised EIR schedule with same receipts. | Full principal only at maturity. | Final balances = 0. |
| B41:B43 | Modification | Modification gain/(loss) = revised PV − pre-reset carrying value under source sign. | `B43=B42−B41`. | PV date and discount rate explicit. |

## CC Loan - SLM

| Block | Field | Formula / algorithm | Seed / exception | Validation |
|---|---|---|---|---|
| B4:B10 | Inputs | Rate, fee, facility dates, limit, utilised amount and repayment timing. | B5 currently = 2% of B8. | Utilisation ≤ sanctioned limit. |
| A17:G21 | Utilisation schedule | Interest = utilised opening × B4 × days / 365; repay principal on assumed date; total = interest + principal; closing roll-forward. | Full B9 repaid at row 21. | Final utilised balance = 0. |
| A26:D38 | SLM fee | Total days = maturity − start; period fee = original fee × period days / total days; unamortised = prior balance − period fee. | `C27=$D$26×B27/$B$39`. | Day total=B39; fee total=B5; final unamortised=0. |

## Assumptions

| Block | Field | Formula / algorithm | Seed / exception | Validation |
|---|---|---|---|---|
| A1:A8 | Governance | Visible metadata for scope, method, day-count, excluded scenarios, disclaimer and accounting framework. | Generic sheet states bullet fixed, Goal Seek and ACT/360. | Model-specific terms override conflicts. |

## ALL MODELS

| Block | Field | Formula / algorithm | Seed / exception | Validation |
|---|---|---|---|---|
| Control block | Controls | Final gross; final net; principal total; fee total; final unamortised; journal difference; date sequence; reset mapping. | Show PASS/FAIL with tolerance. | All applicable controls pass before delivery. |
