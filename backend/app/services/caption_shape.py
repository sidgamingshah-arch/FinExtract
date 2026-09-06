"""Is a note row's caption a LINE-ITEM NAME, or a sentence?

map_ontology's per-line pass runs over every LINE row of every extracted note, so on a real filing
it runs over prose. Measured on the two reference filings:

    English, 367pp — 627 note LINE rows, 40 mapped / 587 unmapped.
        unmapped p50=28 chars, p90=85, max=419
        ("HK$237,892,000 and HK$222,784,000, respectively, mainly represented sales proceeds rec…")
        46 of 587 carry a finite verb; 9 carry a cross-reference idiom.
    Chinese, 210pp — 859 note LINE rows.
        MAPPED captions max SEVEN characters (递延所得税资产, 递延所得税负债)
        while its prose runs 106-156 ("四创电子股份有限公司（以下简称"本公司"或"公司"）为境内公开发行A…").

THAT GAP IS WHY THERE IS NO SINGLE LENGTH RULE. A real Chinese caption and a real English one
differ by an order of magnitude in characters, so the 180/60/100 thresholds calibrated on an
English filing would have suppressed almost every genuine Chinese caption. The script is measured
per row and the budget chosen accordingly, and a cross-reference idiom disqualifies in either
script because it is a pointer, not a balance.
"""
from __future__ import annotations

import re

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# A finite verb makes a clause; a caption names a thing. Deliberately a small, high-precision list.
_VERB = re.compile(
    r"\b(?:is|are|was|were|has|have|had|been|be|will|would|shall|may|represents?|represented"
    r"|comprises?|comprised|includes?|included|amounted|entered|prepared|calculated|levied"
    r"|applied|shall)\b", re.IGNORECASE)
# "see note 12" / 详见附注 — a pointer to a figure elsewhere, never a figure's own caption.
_XREF = re.compile(
    r"set\s+out\s+(?:in|on)\b|refer\s+to\b|as\s+described\s+in\b|see\s+note\b"
    r"|详见|參見|参见|見附註|见附注|另见|另見", re.IGNORECASE)

# Chinese: real captions measured at <=7 characters, prose at 106+. 40 sits in the empty middle,
# nearly 6x above the longest real caption and well under the shortest prose.
_CJK_MAX = 40
# Latin: real captions reach p90=94 and prose starts well above that, so length alone cannot
# separate them — 120 catches only the unambiguous tail, and the verb test carries the rest.
_LATIN_MAX = 120
# A verb inside something caption-length is not evidence; inside a long span it is a sentence.
_VERB_MIN_LEN = 60


def prose_reasons(label: str) -> list[str]:
    """Why this caption reads as prose. Empty means it looks like a line-item name."""
    text = (label or "").strip()
    if not text:
        return []
    out: list[str] = []
    cjk = len(_CJK.findall(text))
    if cjk:
        # Script decided per ROW, not per document: a filing carries both, and an HK report quotes
        # English inside a Chinese note as readily as the reverse.
        if cjk > _CJK_MAX:
            out.append(f"cjk_chars={cjk}>{_CJK_MAX}")
    elif len(text) > _LATIN_MAX:
        out.append(f"latin_chars={len(text)}>{_LATIN_MAX}")
    if len(text) > _VERB_MIN_LEN and _VERB.search(text):
        out.append("finite_verb")
    if _XREF.search(text):
        out.append("cross_reference")
    return out
