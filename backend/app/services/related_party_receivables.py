"""Due from Related Parties (LTP) / Other Receivables (CP) — PRC filings.

Implements docs/PRC_Related_Party_and_Other_Receivables_Extraction_Logic.md. LTP is the highest of
three independent measurements of the same related-party concept (face/statement-linked, the
receivable notes, the related-party note) — never their sum, since they routinely restate one
another. CP is a gross pool of five in-scope receivable classes less the related-party amount
proven included in that same pool (never the LTP total, which can include amounts outside CP's
pool entirely).

UNLIKE services.deprec_impairment / services.secur_fincl_assets, a condition that would otherwise
null a value here still returns the best-available figure, tagged with the review flag that
explains why: a blank cell gives a reviewer nothing to check, and this framework's failure mode is
requiring one number's arithmetic to check another, not the arithmetic that produced it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import LineRole, PrintedIn
from app.core.models.line_item import NotesTable

PeriodKey = tuple[str, str]                 # (basis, period_label)

LTP_KEY = "bs_nca__due_from_related_parties_ltp"
CP_KEY = "bs_ca__other_receivables_cp"

# ── section 3.3 / 3.4: related-party identification and the entrusted-loan exclusion ───────────
_RELATED_PARTY_RE = re.compile(
    r"关联方|关联单位|关联企业|关联公司|母公司|子公司|联营企业|合营企业|受同一控制方控制的企业"
    r"|其他关联方|应收关联方款项|关联方应收款项|关联方资金往来|关联方往来款")
_ENTRUSTED_LOAN_RE = re.compile(r"委托贷款|委托借款|委托银行贷款")

# ── section 4.1/4.2/4.3: LTP's target receivable classes and search locations ──────────────────
_LTP_CLASS_RE = re.compile(r"其他应收款项|其他应收款|一年内到期的长期应收款|长期应收款|发放贷款及垫款|贷款及垫款")
_RECEIVABLE_NOTE_HEADING_RE = re.compile(
    r"其他应收款项|其他应收款|一年内到期的非流动资产|一年内到期的长期应收款|长期应收款|发放贷款及垫款|贷款及垫款")
_RELATED_PARTY_NOTE_HEADING_RE = re.compile(
    r"关联方及关联交易|关联方关系及其交易|关联方交易|关联方往来|关联方应收应付款项|关联方余额|应收关联方款项")
_RELATED_PARTY_NOTE_ITEM_RE = re.compile(
    r"其他应收款项|其他应收款|一年内到期的长期应收款|长期应收款|发放贷款及垫款|贷款及垫款"
    r"|其他应收关联方款项|应收关联方款项")
_RELATED_PARTY_NOTE_EXCLUDE_RE = re.compile(
    r"应付关联方款项|关联方应付款项|预收款项|合同负债|关联方投资|担保金额")

# ── section 5.1/5.2: CP's gross pool (current-portion classes only) ────────────────────────────
_CP_CLASS_RE = re.compile(r"其他应收款项|其他应收款|一年内到期的长期应收款|一年内到期的贷款及垫款|拆出资金|往来款")
_CP_NOTE_HEADING_RE = re.compile(
    r"其他应收款项|其他应收款|一年内到期的非流动资产|一年内到期的长期应收款|一年内到期的贷款及垫款"
    r"|发放贷款及垫款|拆出资金|往来款|其他流动资产")


@dataclass
class _Signal:
    value: Decimal | None = None
    evidence: list[dict] = field(default_factory=list)

    def add(self, amount: Decimal, meta: dict) -> None:
        self.value = amount if self.value is None else self.value + amount
        self.evidence.append(meta)


def _note_matches(table: NotesTable, pattern: re.Pattern) -> bool:
    return bool(pattern.search(table.title or ""))


def _add_item(sig: _Signal, table_number: str, table_title: str, label: str, ev, pk: PeriodKey) -> bool:
    if ev.value is None or (ev.basis.value, ev.period_label or "") != pk:
        return False
    sig.add(ev.value, {"note_number": table_number, "note_heading": table_title,
                       "line_item": label, "value": str(ev.value), "provenance": ev.provenance})
    return True


def _find_1(doc: DocumentModel, pk: PeriodKey, flags: list[str]) -> Decimal | None:
    """Balance-sheet / statement-linked disclosures: face rows naming an in-scope class AND
    explicitly identified as related-party, excluding entrusted loans."""
    sig = _Signal()
    for li in doc.line_items:
        if li.printed_in not in (None, PrintedIn.FACE):
            continue
        text = f"{li.source_label} {li.group_hint}"
        if not _LTP_CLASS_RE.search(text) or not _RELATED_PARTY_RE.search(text):
            continue
        if _ENTRUSTED_LOAN_RE.search(text):
            flags.append("ENTRUSTED_LOAN_NOT_SEPARABLE")
            continue
        for ev in li.values.values():
            _add_item(sig, li.note_number or "", li.section_hint or "", li.source_label, ev, pk)
    return sig.value


def _find_2(doc: DocumentModel, pk: PeriodKey, flags: list[str]) -> Decimal | None:
    """The receivable notes: sum the related-party lines within each note the class heading
    identifies — the class is the note's own, so an item need only prove related-party and not
    an entrusted loan."""
    sig = _Signal()
    found_note = False
    for table in doc.notes:
        if not _note_matches(table, _RECEIVABLE_NOTE_HEADING_RE):
            continue
        found_note = True
        for item in table.items:
            if item.role != LineRole.LINE:
                continue
            text = f"{item.raw_label} {item.group_hint}"
            if not _RELATED_PARTY_RE.search(text):
                continue
            if _ENTRUSTED_LOAN_RE.search(text):
                flags.append("ENTRUSTED_LOAN_NOT_SEPARABLE")
                continue
            for ev in item.values.values():
                _add_item(sig, table.note_number, table.title, item.raw_label, ev, pk)
    if not found_note:
        flags.append("MISSING_NOTE:receivable_notes")
    return sig.value


def _find_3(doc: DocumentModel, pk: PeriodKey, flags: list[str]) -> Decimal | None:
    """The related-party note: sum its own receivable-class lines, excluding payables/deposits/
    investment/guarantee lines and entrusted loans — the whole note is already related-party."""
    sig = _Signal()
    found_note = False
    for table in doc.notes:
        if not _note_matches(table, _RELATED_PARTY_NOTE_HEADING_RE):
            continue
        found_note = True
        for item in table.items:
            if item.role != LineRole.LINE:
                continue
            label = item.raw_label or ""
            if not _RELATED_PARTY_NOTE_ITEM_RE.search(label):
                continue
            if _RELATED_PARTY_NOTE_EXCLUDE_RE.search(label):
                continue
            if _ENTRUSTED_LOAN_RE.search(label):
                flags.append("ENTRUSTED_LOAN_NOT_SEPARABLE")
                continue
            for ev in item.values.values():
                _add_item(sig, table.note_number, table.title, label, ev, pk)
    if not found_note:
        flags.append("MISSING_NOTE:related_party_note")
    return sig.value


def _cp_pool(doc: DocumentModel, pk: PeriodKey, flags: list[str]
            ) -> tuple[Decimal | None, Decimal | None, list[dict]]:
    """CP_Gross and CP_Related_Party_Deduction: every in-scope-class line (face + notes), and the
    subset of those SAME lines that are also explicitly related-party. Entrusted loans are NOT
    excluded here — CP deducts whatever related-party amount its own gross pool actually carries."""
    gross = _Signal()
    deduction = _Signal()

    def _scan(label: str, group_hint: str, note_number: str, note_title: str, ev) -> None:
        text = f"{label} {group_hint}"
        if not _CP_CLASS_RE.search(text):
            return
        if not _add_item(gross, note_number, note_title, label, ev, pk):
            return
        if _RELATED_PARTY_RE.search(text):
            _add_item(deduction, note_number, note_title, label, ev, pk)

    for li in doc.line_items:
        if li.printed_in not in (None, PrintedIn.FACE):
            continue
        for ev in li.values.values():
            _scan(li.source_label, li.group_hint, li.note_number or "", li.section_hint or "", ev)

    found_note = False
    for table in doc.notes:
        if not _note_matches(table, _CP_NOTE_HEADING_RE):
            continue
        found_note = True
        for item in table.items:
            if item.role != LineRole.LINE:
                continue
            for ev in item.values.values():
                _scan(item.raw_label or "", item.group_hint, table.note_number, table.title, ev)
    if not found_note and gross.value is None:
        flags.append("MISSING_NOTE:cp_gross_notes")

    return gross.value, deduction.value, gross.evidence + deduction.evidence


@dataclass
class ReceivablesResult:
    value: Decimal | None
    formula_used: str | None
    status: str
    flags: list[str]
    evidence: list[dict]


def compute(doc: DocumentModel) -> dict[PeriodKey, dict[str, ReceivablesResult]]:
    keys: set[PeriodKey] = set()
    for li in doc.line_items:
        for ev in li.values.values():
            keys.add((ev.basis.value, ev.period_label or ""))
    for table in doc.notes:
        for item in table.items:
            for ev in item.values.values():
                keys.add((ev.basis.value, ev.period_label or ""))

    out: dict[PeriodKey, dict[str, ReceivablesResult]] = {}
    for pk in keys:
        ltp_flags: list[str] = []
        find_1 = _find_1(doc, pk, ltp_flags)
        find_2 = _find_2(doc, pk, ltp_flags)
        find_3 = _find_3(doc, pk, ltp_flags)
        candidates = {"Find_1": find_1, "Find_2": find_2, "Find_3": find_3}
        present = {name: v for name, v in candidates.items() if v is not None}
        if not present:
            ltp_result = ReceivablesResult(None, None, "NOT_COMPUTABLE",
                                           ltp_flags + ["NOT_COMPUTABLE"], [])
        else:
            selected = max(present.values())
            selected_name = next(name for name, v in present.items() if v == selected)
            tied = [name for name, v in present.items() if v == selected]
            flags = list(ltp_flags)
            if selected < 0:
                flags.append("NEGATIVE_RESIDUAL")
            if len(tied) > 1:
                flags.append(f"POSSIBLE_DUPLICATE:{','.join(tied)}")
            ltp_result = ReceivablesResult(selected, f"MAX_VALID({selected_name})", "COMPUTED",
                                           flags, [])
        out.setdefault(pk, {})["ltp"] = ltp_result

        cp_flags: list[str] = []
        cp_gross, cp_deduction, cp_evidence = _cp_pool(doc, pk, cp_flags)
        if cp_gross is None:
            cp_result = ReceivablesResult(None, None, "NOT_COMPUTABLE",
                                          cp_flags + ["NOT_COMPUTABLE"], cp_evidence)
        else:
            candidate, extra_flags = _finalize_cp(cp_gross, cp_deduction)
            cp_result = ReceivablesResult(candidate, "CP_Gross - CP_Related_Party_Deduction",
                                          "COMPUTED", cp_flags + extra_flags, cp_evidence)
        out[pk]["cp"] = cp_result
    return out


def _finalize_cp(gross: Decimal, deduction: Decimal | None) -> tuple[Decimal, list[str]]:
    """CP_Gross - CP_Related_Party_Deduction, populated even when the result is negative or the
    deduction could not be established — a review flag, never a blank cell, is what tells a
    reviewer this candidate needs a second look rather than giving them nothing to check."""
    flags: list[str] = []
    if deduction is None:
        flags.append("RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED")
        deduction = Decimal(0)
    candidate = gross - deduction
    if candidate < 0:
        flags.append("NEGATIVE_RESIDUAL")
    return candidate, flags
