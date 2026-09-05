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
