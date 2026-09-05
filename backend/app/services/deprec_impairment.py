"""Deprec & Impairment (Oper Exp) / (COS) — note-sourced extraction + priority cascade.

Implements docs/HKEX_Depreciation_Extraction_Logic_Revised.md. Neither field is read off a single
printed caption: each is assembled from up to twelve note-level datasets (R&D, selling &
marketing, G&A, other operating expenses, profit-before-tax reconciliation, cost of sales, five
asset notes, cash flow from operations), then resolved by trying a fixed priority order of
candidates and keeping the first one that is present, non-negative and unit-comparable — never by
summing alternative sources together, since they routinely restate the same figure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import LineRole
from app.core.models.line_item import NotesTable

PeriodKey = tuple[str, str]                 # (basis, period_label)

OPER_EXP_KEY = "is_pl__deprec_and_impairment_oper_exp"
COS_KEY = "is_pl__deprec_and_impairment_cos"

# ── section 3.2: qualifying depreciation-related lines (en + zh) ────────────────────────────────
_QUALIFYING_RE = re.compile(
    r"depreciation\s+of\s+investment\s+propert"
    r"|depreciation\s+of\s+fixed\s+assets"
    r"|depreciation\s+of\s+property\s*,?\s*plant\s+and\s+equipment"
    r"|depreciation\s+of\s+construction\s+in\s+progress"
    r"|depreciation\s+of\s+right.?of.?use\s+assets"
    r"|amortisation\s+of\s+prepaid\s+lease\s+payments?"
    r"|release\s+of\s+prepaid\s+lease\s+payments?"
    r"|^\s*depreciation\s*$"
    r"|投資物業折舊|固定資產折舊|物業[,、]?\s*廠房及設備折舊|在建工程折舊|使用權資產折舊|預付租賃款項攤銷|預付租賃款項轉出|^折舊$",
    re.IGNORECASE,
)
# An asset note's own reconciliation prints "Depreciation charge for the year" against opening/
# closing accumulated-depreciation balances; the balances are not a charge and must not join it.
_CHARGE_RE = re.compile(
    r"depreciation\s+charge(?:d)?(?:\s+for\s+the\s+(?:year|period))?|charge\s+for\s+the\s+(?:year|period)"
    r"|本(?:年度?|期)折舊|折舊費用", re.IGNORECASE)
_MOVEMENT_EXCLUDE_RE = re.compile(
    r"^\s*at\s+\d|^\s*at\s+(?:1|31)|opening|closing|disposal|write.?off|transfer|reclassif"
    r"|exchange\s+difference|acquisition|impairment"
    r"|累計|期初|期末|處置|轉撥|滙兌|匯兌|收購|減值", re.IGNORECASE)
_INTANGIBLE_RE = re.compile(r"intangible|goodwill|無形資產|商譽", re.IGNORECASE)
_COMBINED_DA_RE = re.compile(r"depreciation\s+and\s+amortisation|折舊及攤銷|折舊攤銷", re.IGNORECASE)
_OPEX_SPECIFIC_RE = re.compile(
    r"included\s+in\s+[\"'\u201c\u201d]?(?:other\s+)?operating\s+expenses"
    r"|charged\s+to\s+[\"'\u201c\u201d]?(?:other\s+)?operating\s+expenses"
    r"|under\s+administrative,?\s*selling,?\s*r\s*&\s*d\s*or\s*other\s*operating\s*expenses"
    r"|計入經營開支的折舊|經營開支所包含的折舊|計入行政、銷售、研發或其他經營開支的折舊", re.IGNORECASE)
# The stronger form of the callout above: a specific figure per period, not just the fact of the
# split. "Depreciation charges of approximately HK$X (2024: HK$Y) are included in 'other operating
# expenses'..." is itself the P1 answer — summing this note's individual asset-class depreciation
# lines would not reproduce it (they cover a different, wider disclosure), so the two never merge.
# The trailing ",000" is mandatory: prose spells out the full dollar amount, but every other figure
# in these notes is already in the document's own thousands scale, so it is dropped on capture
# rather than carried into a value a thousand times too large.
_EXPLICIT_OPEX_DEP_RE = re.compile(
    r"depreciation\s+charges?\s+of\s+approximately\s+(?:HK\$|RMB|US\$)?\s*([\d,]+),000"
    r"\s*\(\s*20\d{2}\s*:\s*(?:HK\$|RMB|US\$)?\s*([\d,]+),000\s*\)"
    r".{0,100}?included\s+in\s+[\"'\u201c\u201d]?(?:other\s+)?operating\s+expenses",
    re.IGNORECASE | re.DOTALL)


# ── section 4: which note headings feed which dataset ───────────────────────────────────────────
_NOTE_HEADINGS: dict[str, re.Pattern] = {
    "rd_depreciation": re.compile(
        r"research\s+and\s+development|r\s*&\s*d\s+expenses?|研究及開發開支|研發開支", re.IGNORECASE),
    "selling_marketing_depreciation": re.compile(
        r"selling\s+and\s+marketing|selling\s+expenses?|selling\s+and\s+distribution"
        r"|distribution\s+costs?|marketing\s+expenses?"
        r"|銷售及市場推廣開支|銷售開支|銷售及分銷開支|分銷成本", re.IGNORECASE),
    "ga_depreciation": re.compile(
        r"general\s+and\s+administrative|administrative\s+expenses?|g\s*&\s*a\s+expenses?"
        r"|一般及行政開支|行政開支", re.IGNORECASE),
    "operating_expense_depreciation": re.compile(
        r"operating\s+expenses?|operating\s+costs?|經營開支|其他經營開支|經營成本", re.IGNORECASE),
    "pbt": re.compile(
        r"profit\s*/?\s*loss\s+before\s+tax(?:ation)?|profit\s+before\s+tax(?:ation)?"
        r"|arrived\s+at\s+after\s+charging"
        r"|(?:profit|loss).{0,30}from\s+operating\s+activities"
        r"|除稅前溢利|除稅前利潤|稅前溢利|稅前利潤", re.IGNORECASE),
    "cos_depreciation": re.compile(
        r"cost\s+of\s+sales|cost\s+of\s+revenue|cost\s+of\s+services|direct\s+operating\s+costs?"
        r"|銷售成本|收益成本|服務成本|直接經營成本", re.IGNORECASE),
    "ppe_depreciation": re.compile(r"property\s*,?\s*plant\s+and\s+equipment|物業、廠房及設備", re.IGNORECASE),
    "prepaid_lease_depreciation": re.compile(
        r"prepaid\s+land\s+lease\s+payments?|prepaid\s+lease\s+payments?"
        r"|預付土地租賃款項|預付租賃款項", re.IGNORECASE),
    "fixed_asset_depreciation": re.compile(r"fixed\s+assets?|固定資產", re.IGNORECASE),
    "investment_property_depreciation": re.compile(r"investment\s+propert(?:y|ies)|投資物業", re.IGNORECASE),
    "cip_depreciation": re.compile(r"construction\s+in\s+progress|在建工程", re.IGNORECASE),
    "cfo_depreciation": re.compile(
        r"cash\s+flow\s+from\s+operating\s+activities|cash\s+generated\s+from\s+operations"
        r"|reconciliation\s+of\s+profit\s+before\s+taxation\s+to\s+cash"
        r"|經營活動所得現金流量|除稅前溢利與經營所得現金的對賬", re.IGNORECASE),
}
_OPEX_DIRECT_KEYS = (
    "rd_depreciation", "selling_marketing_depreciation", "ga_depreciation",
    "operating_expense_depreciation")
_ASSET_NOTE_KEYS = (
    "ppe_depreciation", "prepaid_lease_depreciation", "fixed_asset_depreciation",
    "investment_property_depreciation", "cip_depreciation")


@dataclass
class _Signal:
    """One dataset's total for one (basis, period), plus what it would take to trust it."""
    value: Decimal | None = None
    currency: str | None = None
    scale: Decimal | None = None
    mixed_units: bool = False
    evidence: list[dict] = field(default_factory=list)

    def add(self, amount: Decimal, currency: str, scale: Decimal, meta: dict) -> None:
        if self.currency is not None and (currency != self.currency or scale != self.scale):
            self.mixed_units = True
        else:
            self.currency, self.scale = currency, scale
        self.value = amount if self.value is None else self.value + amount
        self.evidence.append(meta)

    @property
    def usable(self) -> Decimal | None:
        return None if self.mixed_units else self.value


def _qualifies(label: str, *, asset_note: bool) -> bool:
    if _INTANGIBLE_RE.search(label) or _COMBINED_DA_RE.search(label):
        return False
    if asset_note:
        if _MOVEMENT_EXCLUDE_RE.search(label):
            return False
        return bool(_QUALIFYING_RE.search(label) or _CHARGE_RE.search(label))
    return bool(_QUALIFYING_RE.search(label))


def _note_matches(table: NotesTable, pattern: re.Pattern) -> bool:
    return bool(pattern.search(table.title or ""))


def _explicit_opex_depreciation(table: NotesTable) -> tuple[Decimal, Decimal] | None:
    """The (current, prior) figures named in an explicit "included in operating expenses" callout,
    when the note states them as amounts rather than only the fact of the split."""
    m = _EXPLICIT_OPEX_DEP_RE.search(table.source_text or "")
    if not m:
        return None
    return Decimal(m.group(1).replace(",", "")), Decimal(m.group(2).replace(",", ""))


def _collect(doc: DocumentModel) -> tuple[dict[str, dict[PeriodKey, _Signal]], dict[str, bool]]:
    """Every dataset's per-(basis, period) total, plus which dataset keys found no note at all."""
    datasets: dict[str, dict[PeriodKey, _Signal]] = {
        k: {} for k in list(_NOTE_HEADINGS) if k != "pbt"}
    datasets["pbt_oper_exp_depreciation"] = {}
    datasets["pbt_depreciation"] = {}
    note_found = {k: False for k in datasets}
    note_found["pbt"] = False

    for table in doc.notes:
        for key, pattern in _NOTE_HEADINGS.items():
            if not _note_matches(table, pattern):
                continue
            if key != "pbt":
                note_found[key] = True
            asset_note = key in _ASSET_NOTE_KEYS
            explicit_opex_dep = None
            if key == "pbt":
                note_found["pbt"] = True
                explicit_opex_dep = _explicit_opex_depreciation(table)
                # A note-wide toggle with no per-period figures still routes every qualifying
                # line into the opex-specific bucket; an explicit callout instead names its own
                # amount, so the qualifying lines stay in the general pbt total beside it.
                opex_specific = explicit_opex_dep is None and bool(
                    _OPEX_SPECIFIC_RE.search(table.source_text or ""))
                dest = "pbt_oper_exp_depreciation" if opex_specific else "pbt_depreciation"
                note_found[dest] = True
            else:
                dest = key
            for item in table.items:
                if item.role in (LineRole.SUBTOTAL, LineRole.TOTAL, LineRole.HEADER, LineRole.SPACER):
                    continue
                label = item.raw_label or ""
                if not _qualifies(label, asset_note=asset_note):
                    continue
                for ev in item.values.values():
                    if ev.value is None:
                        continue
                    sig = datasets[dest].setdefault((ev.basis.value, ev.period_label or ""), _Signal())
                    sig.add(ev.value, ev.unit_ctx.currency, ev.unit_ctx.scale_factor,
                            {"dataset_key": dest, "note_number": table.note_number,
                             "note_heading": table.title, "line_item": label,
                             "value": str(ev.value), "provenance": ev.provenance})
            if explicit_opex_dep is not None:
                # The callout is prose and carries no unit context of its own; borrow the first
                # figure this note reports anywhere as evidence of what unit it is in.
                any_ev = next((ev for it in table.items for ev in it.values.values()
                              if ev.value is not None), None)
                if any_ev is not None:
                    current, prior = explicit_opex_dep
                    for period_label, amount in (("current", current), ("prior", prior)):
                        opex_sig = datasets["pbt_oper_exp_depreciation"].setdefault(
                            (any_ev.basis.value, period_label), _Signal())
                        opex_sig.add(amount, any_ev.unit_ctx.currency, any_ev.unit_ctx.scale_factor,
                                    {"dataset_key": "pbt_oper_exp_depreciation",
                                     "note_number": table.note_number,
                                     "note_heading": table.title,
                                     "line_item": "explicit opex-inclusion callout",
                                     "value": str(amount), "provenance": any_ev.provenance})
                    note_found["pbt_oper_exp_depreciation"] = True
    return datasets, note_found



def _sum_available(values: list[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _comparable(a: _Signal | None, b: _Signal | None) -> bool:
    if a is None or b is None or a.mixed_units or b.mixed_units:
        return False
    if a.currency is None or b.currency is None:
        return True
    return a.currency == b.currency and a.scale == b.scale


def _subtract_if_comparable(total: Decimal | None, deduction: Decimal | None,
                            comparable: bool) -> Decimal | None:
    if total is None or deduction is None or not comparable:
        return None
    return total - deduction               # negativity is judged by the caller, never clamped here


@dataclass
class DeprecResult:
    value: Decimal | None
    priority_used: str | None
    status: str
    flags: list[str]
    evidence: list[dict]


def _first_valid(candidates: list[tuple[str, Decimal | None]],
                 evidence_by_priority: dict[str, list[dict]]) -> tuple[str | None, Decimal | None, list[str]]:
    flags: list[str] = []
    for label, value in candidates:
        if value is None:
            continue
        if value < 0:
            flags.append("NEGATIVE_RESIDUAL")
            continue
        return label, value, flags
    return None, None, flags


def compute(doc: DocumentModel) -> dict[PeriodKey, dict[str, DeprecResult]]:
    """Both fields for every (basis, period) the document's notes carry a candidate for."""
    datasets, note_found = _collect(doc)
    doc_flags: list[str] = []
    for key, found in note_found.items():
        if not found:
            doc_flags.append(f"MISSING_NOTE:{key}")
    # No Cost of Sales note anywhere in the filing means P2-P4 can never subtract a cos_depreciation
    # they will never see, and P1 needs an explicit "included in operating expenses" callout this
    # kind of filing never makes either — every candidate is structurally unreachable. Rather than
    # report "not disclosed" for a figure the filing plainly shows (just not split by function),
    # the whole pbt-note total is taken as Oper Exp with a review flag, never silently as COS.
    cos_note_absent = not note_found.get("cos_depreciation", True)

    keys = set()
    for per_key in datasets.values():
        keys.update(per_key.keys())

    out: dict[PeriodKey, dict[str, DeprecResult]] = {}
    for pk in keys:
        def sig(name: str) -> _Signal | None:
            return datasets.get(name, {}).get(pk)

        def val(name: str) -> Decimal | None:
            s = sig(name)
            return s.usable if s is not None else None

        flags = list(doc_flags)
        for name in datasets:
            s = sig(name)
            if s is not None and s.mixed_units:
                flags.append(f"UNIT_MISMATCH:{name}")

        opex_direct = _sum_available([val(k) for k in _OPEX_DIRECT_KEYS])
        asset_note_depreciation = _sum_available([val(k) for k in _ASSET_NOTE_KEYS])
        cos_dep = val("cos_depreciation")
        pbt_dep = val("pbt_depreciation")
        pbt_oper_specific = val("pbt_oper_exp_depreciation")
        cfo_dep = val("cfo_depreciation")
        p5 = pbt_dep if cos_note_absent else None

        p1_label, p1_val, p1_flags = _first_valid(
            [("P1", opex_direct if opex_direct is not None else pbt_oper_specific)], {})
        p2 = _subtract_if_comparable(pbt_dep, cos_dep, _comparable(sig("pbt_depreciation"), sig("cos_depreciation")))
        p3 = _subtract_if_comparable(asset_note_depreciation, cos_dep,
                                     _comparable(sig("ppe_depreciation") or sig("fixed_asset_depreciation"),
                                                 sig("cos_depreciation")))
        p4 = _subtract_if_comparable(cfo_dep, cos_dep, _comparable(sig("cfo_depreciation"), sig("cos_depreciation")))

        oper_label, oper_val, oper_flags = _first_valid(
            [("P1", opex_direct if opex_direct is not None else pbt_oper_specific),
             ("P2", p2), ("P3", p3), ("P4", p4), ("P5", p5)], {})
        if oper_label == "P5":
            oper_flags.append("ASSUMED_NO_COS_SPLIT_DISCLOSED")

        oper_evidence: list[dict] = []
        if oper_label == "P1":
            src = "opex_direct" if opex_direct is not None else "pbt_oper_exp_depreciation"
            if src == "opex_direct":
                for k in _OPEX_DIRECT_KEYS:
                    s = sig(k)
                    if s is not None:
                        oper_evidence.extend(s.evidence)
            else:
                s = sig("pbt_oper_exp_depreciation")
                if s is not None:
                    oper_evidence.extend(s.evidence)
        elif oper_label == "P2":
            for name in ("pbt_depreciation", "cos_depreciation"):
                s = sig(name)
                if s is not None:
                    oper_evidence.extend(s.evidence)
        elif oper_label == "P3":
            for k in (*_ASSET_NOTE_KEYS, "cos_depreciation"):
                s = sig(k)
                if s is not None:
                    oper_evidence.extend(s.evidence)
        elif oper_label == "P4":
            for name in ("cfo_depreciation", "cos_depreciation"):
                s = sig(name)
                if s is not None:
                    oper_evidence.extend(s.evidence)
        elif oper_label == "P5":
            s = sig("pbt_depreciation")
            if s is not None:
                oper_evidence.extend(s.evidence)

        oper_status = "EXTRACTED_AND_COMPUTED" if oper_val is not None else "NOT_FOUND_OR_NOT_COMPUTABLE"
        oper_flags_all = flags + oper_flags
        if oper_val is None:
            oper_flags_all.append("NOT_FOUND_OR_NOT_COMPUTABLE")

        cos_p1 = cos_dep
        # `oper_val` is a resolved figure, not a dataset signal, so the comparability check falls
        # back to the pbt signal's own unit consistency (the operand it is actually subtracted from).
        # Not when P5 assigned the whole pbt total to Oper Exp: that assumption says nothing about
        # how much of it is COS, so treating the remainder as a hard zero would trade one guess for
        # another rather than leaving the genuinely unknown split unknown.
        pbt_signal = sig("pbt_depreciation")
        cos_p2 = _subtract_if_comparable(
            pbt_dep, oper_val,
            comparable=(oper_label != "P5" and pbt_signal is not None and not pbt_signal.mixed_units
                       and oper_val is not None))
        cos_label, cos_val, cos_flags = _first_valid(
            [("COS_P1", cos_p1), ("COS_P2", cos_p2)], {})

        cos_evidence: list[dict] = []
        if cos_label == "COS_P1":
            s = sig("cos_depreciation")
            if s is not None:
                cos_evidence.extend(s.evidence)
        elif cos_label == "COS_P2":
            s = sig("pbt_depreciation")
            if s is not None:
                cos_evidence.extend(s.evidence)
            cos_evidence.extend(oper_evidence)

        cos_status = ("DIRECTLY_EXTRACTED" if cos_label == "COS_P1"
                      else "EXTRACTED_AND_COMPUTED" if cos_val is not None
                      else "NOT_FOUND_OR_NOT_COMPUTABLE")
        cos_flags_all = flags + cos_flags
        if cos_val is None:
            cos_flags_all.append("NOT_FOUND_OR_NOT_COMPUTABLE")

        out[pk] = {
            "oper_exp": DeprecResult(oper_val, oper_label, oper_status, oper_flags_all, oper_evidence),
            "cos": DeprecResult(cos_val, cos_label, cos_status, cos_flags_all, cos_evidence),
        }
    return out
