"""Sales (Revenues) — PRC filings' 主营业务/主营业务收入, note-fallback only.

Implements docs/PRC_Sales_Revenues_Extraction_Logic_Simplified.md. Priority 1 — a face caption
naming 主营业务/主营业务收入, or the English HKEX equivalents Turnover/Revenue/Sales — is already
what the ordinary alias-matching mapper does, so extraction_mode stays "extract" and this module
supplies only Priority 2: the fallback the mapper cannot do on its own, which is to open a
营业收入 note and read the specific 主营业务/主营业务收入 row's revenue figure, never the note's own
combined total row read generically and never a cost column.

Total 营业收入 is deliberately NOT among the concept's Chinese aliases, because §4 forbids it as a
fallback when 主营业务收入 is not separately disclosed — the mapper would otherwise bind total
revenue here as its Priority 1 answer, which is a wrong figure rather than a missing one. The
English captions stay: §4's prohibition is about the Chinese pair, and on an English filing
Turnover IS the revenue line. See app/services/spec_alias_curation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import LineRole

PeriodKey = tuple[str, str]                 # (basis, period_label)

SALES_REVENUES_KEY = "is_pl__sales_revenues"

_NOTE_HEADING_RE = re.compile(r"营业收入和营业成本|营业收入及营业成本|营业收入、营业成本|营业收入")
_ROW_RE = re.compile(r"主营业务收入|主营业务")
_COST_RE = re.compile(r"成本")


# THE ONLY CAPTIONS §2 ADMITS AS PRIORITY 1. The spec names the target twice and both times it is
# the 主营业务 pair; §4 then forbids total 营业收入 outright, "when 主营业务 or 主营业务收入 is not
# separately disclosed". So a face reading under any other Chinese caption is not a weaker P1 — it
# is the figure §4 refuses, and the note's 主营业务 row is the answer that displaces it.
#
# The English captions are admitted because on an HKEX filing Turnover IS the revenue line, and a
# bilingual filing can pair an English face caption with a Chinese 营业收入 note. Everything below
# is also unanchored: a real caption carries numbering, punctuation and a bilingual tail
# ("一、营业总收入", "Revenue from contracts with customers 客戶合約收益").
_P1_CAPTION_RE = re.compile(
    r"主营业务|主營業務|turnover|revenue|sales|income from operations", re.IGNORECASE)


def is_priority_one_caption(caption: str | None) -> bool:
    """Whether a FACE caption is one the spec accepts as Priority 1 for this concept.

    Asked of a reading that already occupies the concept, not of a candidate: the mapper binds
    the face before this module runs, and the question here is whether what it bound is the
    thing §2 asked for or the total §4 refuses.
    """
    return bool(caption and _P1_CAPTION_RE.search(caption))


def has_revenue_note(doc: DocumentModel) -> bool:
    """Whether the filing carries a 营业收入 note at all — the precondition for P2.

    Gates the displacement: on a filing with no such note there is nothing to displace a face
    reading WITH, and this spec has no jurisdiction over how that filing captions its revenue.
    """
    return any(_NOTE_HEADING_RE.search(table.title or "") for table in doc.notes)


def _is_primary_measure(period_label: str | None) -> bool:
    """Whether this value is the PERIOD'S OWN AMOUNT rather than a second measure of it.

    THE ROW TEST ABOVE CANNOT SEE A COST COLUMN. `_COST_RE` tests the row's CAPTION, and the note
    this module exists for does not caption its cost rows — it captions its cost COLUMNS:

        项目    |      本期发生额      |      上期发生额
                |   收入   |   成本   |   收入   |   成本
        主营业务 | 1,589,859,743.31 | 1,389,417,976.40 | 1,920,773,532.54 | 1,590,200,224.12

    One row, four figures, two of them costs. Harvesting `item.values.values()` wholesale took all
    four: on the measured filing (Sun Create Electronics, 11077098) that published this year's
    COST, 1,389,417,976.40, as `is_pl__sales_revenues` for the prior period — a cost figure under
    a revenue concept, and internally consistent enough that nothing downstream could see it.

    `row_reconstruct` now labels a non-primary measure "<period>:<slug>" ("current:cost"), so the
    bare label IS the amount and a suffixed one never is. §4's prohibition on reading a cost as
    revenue is therefore enforced on the column as well as on the caption.
    """
    return ":" not in (period_label or "")


@dataclass
class SalesRevenuesResult:
    value: Decimal | None
    priority_used: str | None
    status: str
    flags: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)


def compute_note_fallback(doc: DocumentModel) -> dict[PeriodKey, SalesRevenuesResult]:
    """Priority 2 only. Priority 1 (the face) is read by the ordinary mapper before this stage
    ever runs; a (basis, period) already carrying a face value is left alone by the caller."""
    out: dict[PeriodKey, SalesRevenuesResult] = {}
    for table in doc.notes:
        if not _NOTE_HEADING_RE.search(table.title or ""):
            continue
        for item in table.items:
            if item.role in (LineRole.HEADER, LineRole.SPACER, LineRole.TOTAL, LineRole.SUBTOTAL):
                continue
            label = item.raw_label or ""
            text = f"{label} {item.group_hint}"
            # The column-selection rule (section 3): a row naming both 主营业务 and 成本 is the
            # cost column of the same breakdown, not the revenue one — never selected.
            if not _ROW_RE.search(text) or _COST_RE.search(text):
                continue
            for ev in item.values.values():
                if ev.value is None:
                    continue
                if not _is_primary_measure(ev.period_label):
                    continue           # a 成本 column of this same row — see `_is_primary_measure`
                pk = (ev.basis.value, ev.period_label or "")
                meta = {"note_number": table.note_number, "note_heading": table.title,
                        "line_item": label, "value": str(ev.value), "provenance": ev.provenance}
                existing = out.get(pk)
                if existing is None:
                    out[pk] = SalesRevenuesResult(ev.value, "P2", "DIRECTLY_EXTRACTED",
                                                  evidence=[meta])
                elif existing.value == ev.value:
                    existing.evidence.append(meta)          # same figure repeated — extra evidence
                else:
                    existing.flags.append("POSSIBLE_DUPLICATE")  # two different note figures found
    return out
