"""WHICH REPORTING REGIME A FILING IS PRINTED IN, where a reading rule is only safe for one of them.

Two regimes are told apart: an Indian annual report (below) and a US Form 10-K
(`is_us_10k_filing`). Every filing that is neither — the HKEX and mainland corpus the readers are
calibrated on — is read exactly as before.

An Indian annual report prints two things the readers otherwise refuse, both measured on Schedule III
statements laid out the way the Companies Act prescribes:

* a statement TITLE carrying its own date on the same line — "Standalone Balance Sheet as at 31 March
  2025", "Statement of Profit and Loss for the year ended 31st March, 2025". A heading line with more
  than one number is not a title candidate (`stages.classify._looks_like_heading`), so the page was
  never a face and nothing on it was read;
* rows OPENED by a bracketed or dotted numeral — Schedule III's own "(1) Current tax" / "(2) Deferred
  tax". The numeral parses as the amount -1 (`row_reconstruct._num`), text after a figure is neither
  caption nor figure, and the row was dropped with its tax charge.

Both rules are correct for any filing, but the reference corpus the readers are calibrated on (HKEX
and mainland filings) is not where they were measured, so they are switched on for an Indian filing
only. The test is the vocabulary no other corpus prints: the rupee sign, lakh / crore scaling, "Ind
AS", the Companies Act 2013 and a Corporate Identity Number. Lifting the gate is a decision for the
day the reference corpus is re-measured with the rules on.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_INDIAN = re.compile(
    r"₹"
    r"|\b(?:lakhs?|lacs|crores?)\b"
    r"|\bRs\.?\s*(?:in\s+)?(?:lakhs?|lacs|crores?|millions?|thousands?)\b"
    r"|\bInd\s?AS\b"
    r"|\bCompanies\s+Act,?\s+2013\b"
    r"|\bCIN\b[:\s-]*[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b",
    re.IGNORECASE)


def is_indian_filing(texts: Iterable[str]) -> bool:
    """Whether any page of the filing prints vocabulary only an Indian filing prints."""
    return any(_INDIAN.search(t or "") for t in texts)


# A US FORM 10-K, by the words its cover and its auditor's report print and no other filing in the
# corpus does. The form's own name is required: a Hong Kong or mainland annual report can mention the
# SEC, the PCAOB or US GAAP (a dual-listed issuer files a Form 20-F, which this does not match), but
# only a 10-K's cover says "FORM 10-K". It must come with one more SEC signal, so a passing mention
# of a US filer's 10-K in another report's text is not enough.
_FORM_10K = re.compile(r"\bForm\s+10-K\b", re.IGNORECASE)
_SEC_SIGNAL = re.compile(
    r"\bSecurities\s+and\s+Exchange\s+Commission\b"
    r"|\bRegistered\s+Public\s+Accounting\s+Firm\b"
    r"|\bSecurities\s+Exchange\s+Act\s+of\s+1934\b",
    re.IGNORECASE)


def is_us_10k_filing(texts: Iterable[str]) -> bool:
    """Whether the filing is a US Form 10-K: its form named, and the SEC named beside it.

    Switches on the readings measured on 10-K pages only (`row_reconstruct.us_10k_rows`): a
    note's footnote ("(2) Principal, unamortized discount, …") is not a row, and nor is its
    column-heading line ("Maturity  Coupon Rate  Effective Rate  2024  2025"). The HKEX, mainland
    and Indian corpus never sees either.
    """
    texts = list(texts)
    return (any(_FORM_10K.search(t or "") for t in texts)
            and any(_SEC_SIGNAL.search(t or "") for t in texts))
