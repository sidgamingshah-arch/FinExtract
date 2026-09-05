"""Secur & Other Fincl Assets (CP) / (LTP) — note-sourced parent-minus-deductions, with the
unabsorbed Level 3 fair-value amount carried from CP into LTP.

Implements docs/HKEX_Securities_Other_Financial_Assets_Extraction_Logic_Clean.md. Each field is a
SUM of in-scope financial-asset note totals, less deduction components (derivatives, other
receivables, and — for LTP only — related-party/associate/JV investments) proven included in those
totals, less (CP only) the Level 3 fair-value-hierarchy amount. A CP total the deductions and
Level 3 amount exceed is not negative on the face: CP reports zero and the shortfall reduces LTP
instead, since Level 3 assets are a single pool the two maturities cannot separately double-claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import LineRole, PrintedIn
from app.core.models.line_item import NotesTable
from app.services.mapping import normalize_label

PeriodKey = tuple[str, str]                 # (basis, period_label)

CP_KEY = "bs_ca__secur_and_other_fincl_assets_cp"
LTP_KEY = "bs_nca__secur_and_other_fincl_assets_ltp"

# ── section 4.1: in-scope financial-asset note headings (en + zh) ──────────────────────────────
_ASSET_NOTE_RE = re.compile(
    r"financial\s+assets?\s+at\s+fair\s+value\s+through\s+profit\s+or\s+loss"
    r"|financial\s+assets?\s+at\s+fvtpl"
    r"|financial\s+assets?\s+at\s+fair\s+value\s+through\s+other\s+comprehensive\s+income"
    r"|financial\s+assets?\s+at\s+fvtoci"
    r"|^\s*financial\s+assets?\s*$"
    r"|available.for.sale\s+financial\s+assets"
    r"|held.to.maturity\s+financial\s+assets"
    r"|structured\s+deposits\s+with\s+embedded\s+derivatives"
    r"|other\s+debt\s+investments"
    r"|^\s*debt\s+investments\s*$"
    r"|other\s+financial\s+assets"
    r"|investment\s+securities"
    r"|money\s+market\s+instruments"
    r"|marketable\s+securities"
    r"|short.term\s+money\s+market\s+deposits"
    r"|按公平值計入損益的金融資產|按公平值計入其他全面收益的金融資產|金融資產|可供出售金融資產"
    r"|持有至到期金融資產|含嵌入式衍生工具的結構性存款|債務投資|其他債務投資|其他金融資產"
    r"|投資證券|貨幣市場工具|有價證券|短期貨幣市場存款", re.IGNORECASE)
_FV_HIERARCHY_RE = re.compile(
    r"fair\s+value\s+hierarchy|fair\s+value\s+measurements?"
    r"|公平值層級|公平值計量|金融工具公平值層級", re.IGNORECASE)
_LEVEL_3_RE = re.compile(r"^\s*level\s*3\b|第三級總額|第三層級總額|^\s*第三級|^\s*第三層", re.IGNORECASE)
_LEVEL_1_2_RE = re.compile(r"^\s*level\s*[12]\b|第一級|第二級|第一層|第二層", re.IGNORECASE)

# ── section 4.2: deduction items, matched only inside an already-identified note ────────────────
_DERIVATIVES_RE = re.compile(r"derivatives?(?:\s+financial\s+instruments?)?|衍生工具|衍生金融工具",
                             re.IGNORECASE)
_OTHER_RECEIVABLES_RE = re.compile(r"other\s+receivables|其他應收款項", re.IGNORECASE)
_RELATED_PARTY_RE = re.compile(r"investment\s+in\s+related\s+part(?:y|ies)|於關聯方的投資", re.IGNORECASE)
_ASSOCIATE_RE = re.compile(r"investment\s+in\s+associates?|於聯營公司的投資", re.IGNORECASE)
_JV_RE = re.compile(r"investment\s+in\s+(?:joint\s+ventures?|jv)|於合營企業的投資", re.IGNORECASE)


def _note_matches(table: NotesTable, pattern: re.Pattern) -> bool:
    return bool(pattern.search(table.title or ""))


def _classify_notes(doc: DocumentModel) -> dict[str, str]:
    """note_number -> "current" | "non_current", from the FACE rows that cite it.

    A note carries no section of its own (``NotesTable`` has no ``section_hint``); the balance
    sheet row that cites it does, via the caption's classification band — the same signal
    ``stages.map_ontology`` already reads for this exact pair of concepts.
    """
    available = {t.note_number for t in doc.notes}
    out: dict[str, str] = {}
    for li in doc.line_items:
        if li.printed_in not in (None, PrintedIn.FACE):
            continue
        cited = li.cited_notes_among(available)
        if not cited:
            continue
        norm = normalize_label(li.section_hint or "")
        if "non current assets" in norm:
            kind = "non_current"
        elif "current assets" in norm:
            kind = "current"
        else:
            continue
        for n in cited:
            out.setdefault(n, kind)
    return out


@dataclass
class _Signal:
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


def _note_total(table: NotesTable, pk: PeriodKey) -> tuple[Decimal | None, str, Decimal, list[dict]]:
    """This note's own reported total for one (basis, period): its TOTAL row if it printed one,
    else the sum of its LINE rows (which then also carries any deduction sub-line, exactly as
    printed — the deductions are subtracted back out separately, never assumed absent)."""
    totals, lines = [], []
    currency, scale = "", Decimal(1)
    for item in table.items:
        for ev in item.values.values():
            if ev.value is None or (ev.basis.value, ev.period_label or "") != pk:
                continue
            currency, scale = ev.unit_ctx.currency, ev.unit_ctx.scale_factor
            meta = {"note_number": table.note_number, "note_heading": table.title,
                    "line_item": item.raw_label, "value": str(ev.value), "provenance": ev.provenance}
            if item.role in (LineRole.TOTAL, LineRole.SUBTOTAL):
                totals.append((ev.value, meta))
            elif item.role == LineRole.LINE:
                lines.append((ev.value, meta))
    if totals:
        value = sum(v for v, _ in totals)
        return value, currency, scale, [m for _, m in totals]
    if lines:
        return sum(v for v, _ in lines), currency, scale, [m for _, m in lines]
    return None, currency, scale, []


def _deductions(table: NotesTable, pk: PeriodKey, *, ltp: bool) -> dict[str, _Signal]:
    keys = {"derivatives": _DERIVATIVES_RE, "other_receivables": _OTHER_RECEIVABLES_RE}
    if ltp:
        keys.update({"related_party": _RELATED_PARTY_RE, "associate": _ASSOCIATE_RE, "jv": _JV_RE})
    out = {k: _Signal() for k in keys}
    for item in table.items:
        if item.role not in (LineRole.LINE,):
            continue
        label = item.raw_label or ""
        for key, pattern in keys.items():
            if not pattern.search(label):
                continue
            for ev in item.values.values():
                if ev.value is None or (ev.basis.value, ev.period_label or "") != pk:
                    continue
                out[key].add(ev.value, ev.unit_ctx.currency, ev.unit_ctx.scale_factor,
                             {"note_number": table.note_number, "note_heading": table.title,
                              "line_item": item.raw_label, "value": str(ev.value),
                              "provenance": ev.provenance})
    return out


def _level_3(doc: DocumentModel, pk: PeriodKey) -> tuple[Decimal | None, list[dict]]:
    sig = _Signal()
    for table in doc.notes:
        if not _note_matches(table, _FV_HIERARCHY_RE):
            continue
        for item in table.items:
            label = item.raw_label or ""
            if _LEVEL_1_2_RE.search(label) or not _LEVEL_3_RE.search(label):
                continue
            for ev in item.values.values():
                if ev.value is None or (ev.basis.value, ev.period_label or "") != pk:
                    continue
                sig.add(ev.value, ev.unit_ctx.currency, ev.unit_ctx.scale_factor,
                        {"note_number": table.note_number, "note_heading": table.title,
                         "line_item": item.raw_label, "value": str(ev.value),
                         "provenance": ev.provenance})
    return sig.usable, sig.evidence


def _sum_or_none(values: list[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


@dataclass
class SecurResult:
    value: Decimal | None
    formula_used: str | None
    status: str
    flags: list[str]
    evidence: list[dict]


def compute(doc: DocumentModel) -> dict[PeriodKey, dict[str, SecurResult]]:
    classification = _classify_notes(doc)
    qualifying = [t for t in doc.notes if _note_matches(t, _ASSET_NOTE_RE)
                  and classification.get(t.note_number) in ("current", "non_current")]

    keys: set[PeriodKey] = set()
    for table in doc.notes:
        for item in table.items:
            for ev in item.values.values():
                keys.add((ev.basis.value, ev.period_label or ""))

    out: dict[PeriodKey, dict[str, SecurResult]] = {}
    for pk in keys:
        cp_flags: list[str] = []
        ltp_flags: list[str] = []
        cp_evidence: list[dict] = []
        ltp_evidence: list[dict] = []

        cp_totals: list[Decimal] = []
        cp_deduct: list[Decimal] = []
        ltp_totals: list[Decimal] = []
        ltp_deduct: list[Decimal] = []
        for table in qualifying:
            kind = classification[table.note_number]
            total, currency, scale, total_evidence = _note_total(table, pk)
            if total is None:
                continue
            if not table.items:
                (cp_flags if kind == "current" else ltp_flags).append(
                    f"DEDUCTION_NOT_PROVEN_INCLUDED:{table.note_number}")
                continue
            ded = _deductions(table, pk, ltp=(kind == "non_current"))
            ded_total = _sum_or_none([s.usable for s in ded.values()]) or Decimal(0)
            mixed = any(s.mixed_units for s in ded.values())
            if kind == "current":
                cp_totals.append(total)
                cp_deduct.append(ded_total)
                cp_evidence.extend(total_evidence)
                for s in ded.values():
                    cp_evidence.extend(s.evidence)
                if mixed:
                    cp_flags.append(f"UNIT_MISMATCH:{table.note_number}")
            else:
                ltp_totals.append(total)
                ltp_deduct.append(ded_total)
                ltp_evidence.extend(total_evidence)
                for s in ded.values():
                    ltp_evidence.extend(s.evidence)
                if mixed:
                    ltp_flags.append(f"UNIT_MISMATCH:{table.note_number}")

        find_1_cp = _sum_or_none(cp_totals)
        find_2_cp = _sum_or_none(cp_deduct) if cp_totals else None
        level_3, level_3_evidence = _level_3(doc, pk)

        find_1_ltp = _sum_or_none(ltp_totals)
        find_2_ltp = _sum_or_none(ltp_deduct) if ltp_totals else None

        cp_value, cp_candidate, carryforward, cp_status = _compute_cp(find_1_cp, find_2_cp, level_3)
        if cp_status == "MISSING_LEVEL_3":
            cp_flags.append("MISSING_LEVEL_3")
        elif cp_status == "NOT_COMPUTABLE":
            cp_flags.append("NOT_COMPUTABLE")
        elif cp_status == "NEGATIVE_CP_CARRIED_TO_LTP":
            cp_flags.append("NEGATIVE_CP_CARRIED_TO_LTP")
        cp_formula = ("Find_1_CP - Find_2_CP - Find_3_CP" if cp_status != "NOT_COMPUTABLE" else None)
        out.setdefault(pk, {})["cp"] = SecurResult(
            cp_value, cp_formula, cp_status, cp_flags, cp_evidence + level_3_evidence)

        ltp_value, ltp_status = _compute_ltp(find_1_ltp, find_2_ltp, carryforward)
        if ltp_status == "MISSING_CP_CARRYFORWARD_TO_LTP":
            ltp_flags.append("MISSING_CP_CARRYFORWARD_TO_LTP")
        elif ltp_status == "NOT_COMPUTABLE":
            ltp_flags.append("NOT_COMPUTABLE")
        elif ltp_status == "NEGATIVE_LTP_ADJUSTMENT_REVERSED":
            ltp_flags.append("NEGATIVE_LTP_ADJUSTMENT_REVERSED")
        ltp_formula = ("LTP_base + CP_negative_carryforward_to_LTP"
                       if ltp_status != "NOT_COMPUTABLE" else None)
        out[pk]["ltp"] = SecurResult(ltp_value, ltp_formula, ltp_status, ltp_flags, ltp_evidence)
    return out


def _compute_cp(find_1: Decimal | None, find_2: Decimal | None, level_3: Decimal | None
               ) -> tuple[Decimal | None, Decimal | None, Decimal | None, str]:
    if find_1 is None or find_2 is None:
        return None, None, None, "NOT_COMPUTABLE"
    cp_base = find_1 - find_2
    if level_3 is None:
        return cp_base, None, None, "MISSING_LEVEL_3"
    cp_candidate = cp_base - level_3
    if cp_candidate >= 0:
        return cp_candidate, cp_candidate, Decimal(0), "COMPUTED"
    return Decimal(0), cp_candidate, cp_candidate, "NEGATIVE_CP_CARRIED_TO_LTP"


def _compute_ltp(find_1: Decimal | None, find_2: Decimal | None, carryforward: Decimal | None
                ) -> tuple[Decimal | None, str]:
    if find_1 is None or find_2 is None:
        return None, "NOT_COMPUTABLE"
    ltp_base = find_1 - find_2
    if carryforward is None:
        return max(ltp_base, Decimal(0)), "MISSING_CP_CARRYFORWARD_TO_LTP"
    ltp_candidate = ltp_base + carryforward
    if ltp_candidate < 0:
        return ltp_base, "NEGATIVE_LTP_ADJUSTMENT_REVERSED"
    return ltp_candidate, "COMPUTED"
