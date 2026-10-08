"""WHICH REPORTING REGIME A FILING IS PRINTED IN, where a reading rule is only safe for one of them.

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
