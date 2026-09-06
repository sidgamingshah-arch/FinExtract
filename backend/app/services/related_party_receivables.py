"""Due from Related Parties (LTP) / Other Receivables (CP) — PRC filings.

Implements docs/PRC_Related_Party_and_Other_Receivables_Extraction_Logic.md. LTP is the highest of
three independent measurements of the same related-party concept (face/statement-linked, the
receivable notes, the related-party note) — never their sum, since they routinely restate one
another. CP is a gross pool of five in-scope receivable classes less the related-party amount
proven included in that same pool (never the LTP total, which can include amounts outside CP's
pool entirely).

CP has three outcomes, not two. §3.6 lets a nil related-party deduction be a finding when the
gross pool's own lines were searched and carried no related-party marker, so that case computes
normally; a deduction that could not be established at all is null, because returning the gross
pool would put the same related-party money in this row and in the LTP row at once. A negative
candidate is null too: this row is a summed child of Total Current Assets, so publishing a
negative asset breaks the balance-sheet identity two levels up instead of naming the row at fault.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import LineRole, PrintedIn
from app.core.models.line_item import NotesTable
from app.services.restatement import RestatementLedger

PeriodKey = tuple[str, str]                 # (basis, period_label)

LTP_KEY = "bs_nca__due_from_related_parties_ltp"
CP_KEY = "bs_ca__other_receivables_cp"

# ── section 3.3 / 3.4: related-party identification and the entrusted-loan exclusion ───────────
_RELATED_PARTY_RE = re.compile(
    r"关联方|关联单位|关联企业|关联公司|母公司|子公司|联营企业|合营企业|受同一控制方控制的企业"
    r"|其他关联方|应收关联方款项|关联方应收款项|关联方资金往来|关联方往来款")
_ENTRUSTED_LOAN_RE = re.compile(r"委托贷款|委托借款|委托银行贷款")

# ── section 3.2: the net amount rule ──────────────────────────────────────────────────────────
# An amount is on a net basis when it says so — an explicit net carrying amount, or a closing
# balance already stated as less its allowance.
_EXPLICITLY_NET_RE = re.compile(
    r"净额|淨額|账面价值|帳面價值|期末账面价值|减坏账准备|減壞賬準備|减信用损失准备|减损失准备"
    r"|账面余额减")
# A pooled allowance disclosed as its own line: the note carries gross debtor balances and one
# combined provision, so no related-party share of it can be read off without allocating.
_POOLED_ALLOWANCE_RE = re.compile(r"^(?!.*减).*?(坏账准备|壞賬準備|信用损失准备|损失准备|减值准备)")

# THE ALLOWANCE PRINTED AS A COLUMN, NOT A LINE — §3.2's second preference, made computable.
#
# The CSRC-standard related-party note prints its allowance beside every balance rather than
# beneath the block:
#
#     项目名称 | 关联方 |     期末余额     |     期初余额
#                       | 账面余额 | 坏账准备 | 账面余额 | 坏账准备
#
# Every row therefore carries its OWN provision, which is the case §4.3's "cannot be read off
# without allocating" was never about: nothing has to be allocated, the filing already split it.
# `row_reconstruct` labels that second column "<period>:allowance" (see `ColumnGrid`), so the net
# is the row's own subtraction. Before the grid existed, the provision landed in the "prior" slot
# of a two-axis reading and no service could compute gross-minus-provision for one period at all —
# measured on Sun Create Electronics 11077098, note 6(1), where 坏账准备 held the "prior" label.
_ALLOWANCE_MEASURE = "allowance"


def _is_primary_measure(period_label: str | None) -> bool:
    """Whether a value is the PERIOD'S OWN AMOUNT rather than a second measure of it.

    "current" / "prior" are amounts; "current:allowance" is a measure OF that amount and must
    never be summed as though it were a receivable of its own.
    """
    return ":" not in (period_label or "")


def _allowance_beside(values, ev):
    """The 坏账准备 figure printed in the same row, for the same period — or None."""
    want = f"{ev.period_label}:{_ALLOWANCE_MEASURE}"
    return next((o for o in values
                 if o.basis == ev.basis and o.period_label == want and o.value is not None), None)


def _has_pooled_allowance(table: NotesTable) -> bool:
    return any(_POOLED_ALLOWANCE_RE.search(item.raw_label or "")
               for item in table.items if item.role == LineRole.LINE)

# ── section 4.1/4.2/4.3: LTP's target receivable classes and search locations ──────────────────
_LTP_CLASS_RE = re.compile(r"其他应收款项|其他应收款|一年内到期的长期应收款|长期应收款|发放贷款及垫款|贷款及垫款")
_RECEIVABLE_NOTE_HEADING_RE = re.compile(
    r"其他应收款项|其他应收款|一年内到期的非流动资产|一年内到期的长期应收款|长期应收款|发放贷款及垫款|贷款及垫款")
_RELATED_PARTY_NOTE_HEADING_RE = re.compile(
    r"关联方及关联交易|关联方关系及其交易|关联方交易|关联方往来|关联方应收应付款项|关联方余额|应收关联方款项"
    # Every alternative above needs 关联方 sitting next to a specific token. The CSRC-standard
    # caption is 应收、应付关联方等未结算项目情况 — which splits 应收…关联方 with 、应付 and so
    # matched none of them. The consequence was not a missing figure but a LIE: _find_3 logged
    # MISSING_NOTE:related_party_note for a note that had been extracted as seven fragments across
    # pages 189-195, 38 rows on one page. A false "we looked and it is not there" is worse than a
    # crash, because it is indistinguishable in the output from a filing that genuinely discloses
    # nothing. This note covers receivables AND payables together, which is why the caption reads
    # 应收、应付 — direction is decided per ROW by _RELATED_PARTY_NOTE_EXCLUDE_RE, not by heading.
    r"|应收、?应付关联方|关联方等未结算项目|未结算项目情况")
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
    """One candidate's running total, plus what it would take to trust it.

    §3.1 requires units to be normalised before addition, and §9 has UNIT_MISMATCH and
    CURRENCY_MISMATCH to report when they are not — so the currency and scale each contribution
    arrives in are tracked, and a candidate mixing them is unusable rather than quietly wrong.
    """
    value: Decimal | None = None
    currency: str | None = None
    scale: Decimal | None = None
    mixed_currency: bool = False
    mixed_scale: bool = False
    duplicated: bool = False
    net_not_derivable: bool = False
    # §3.2 applied: at least one contribution was reduced by the 坏账准备 printed beside it, so
    # the total is a NET related-party amount and not a gross one. Reported, because "1,000" and
    # "1,000 net of a 300 provision" are different assertions about the same row.
    net_of_allowance: bool = False
    gross: Decimal | None = None
    allowance: Decimal | None = None
    evidence: list[dict] = field(default_factory=list)
    _ledger: RestatementLedger = field(default_factory=RestatementLedger)

    def add(self, amount: Decimal, currency: str | None, scale: Decimal | None,
            meta: dict, *, allowance: Decimal | None = None) -> None:
        # §3.5: "Treat the same balance repeated in the balance sheet, a detailed note, and the
        # related-party note as separate candidate evidence, not additive evidence." Within one
        # candidate that is a restatement — see services.restatement.
        #
        # THE RESTATEMENT LEDGER IS FED THE GROSS, not the net: what identifies the same balance
        # printed twice is the balance, and one note stating it beside its provision does not make
        # it a different receivable from the same figure printed alone.
        # The identity is (WHERE it was printed, WHICH note), not the note number alone. A face
        # line's note_number is the note it CITES, so keying on the number by itself made a face
        # row and the note it points at look like one source and let the same balance in twice.
        note = (meta.get("source_kind") or "note", meta.get("note_number"))
        if self._ledger.is_restatement(amount, currency, scale, note):
            self.duplicated = True
            self.evidence.append(
                {**meta, "duplicate_of_note": self._ledger.source_of(amount, currency, scale)})
            return
        if self.currency is not None and currency != self.currency:
            self.mixed_currency = True
        if self.scale is not None and scale != self.scale:
            self.mixed_scale = True
        if self.currency is None:
            self.currency = currency
        if self.scale is None:
            self.scale = scale
        self.gross = amount if self.gross is None else self.gross + amount
        net = amount
        if allowance is not None:
            # §3.2: "a closing balance already stated as less its allowance". Here the filing
            # states both halves, so the subtraction is the filing's own arithmetic, not an
            # allocation — which is what §4.3 forbids and this is not.
            self.allowance = allowance if self.allowance is None else self.allowance + allowance
            self.net_of_allowance = True
            net = amount - allowance
        self.value = net if self.value is None else self.value + net
        self.evidence.append(meta)

    @property
    def mixed_units(self) -> bool:
        return self.mixed_currency or self.mixed_scale

    @property
    def usable(self) -> Decimal | None:
        """The total, or None when the contributions were never comparable."""
        return None if self.mixed_units else self.value

    def unit_flags(self, name: str) -> list[str]:
        out = []
        if self.mixed_currency:
            out.append(f"CURRENCY_MISMATCH:{name}")
        if self.mixed_scale:
            out.append(f"UNIT_MISMATCH:{name}")
        if self.duplicated:
            out.append(f"POSSIBLE_DUPLICATE:{name}")
        if self.net_not_derivable:
            out.append(f"NET_AMOUNT_NOT_DERIVABLE:{name}")
        if self.net_of_allowance:
            out.append(f"NET_OF_ALLOWANCE:{name}")
        return out


def _note_matches(table: NotesTable, pattern: re.Pattern) -> bool:
    return bool(pattern.search(table.title or ""))


def _add_item(sig: _Signal, table_number: str, table_title: str, label: str, ev, pk: PeriodKey,
              siblings=(), source_kind: str = "note") -> bool:
    """Contribute one figure to a candidate, net of the allowance printed beside it (§3.2).

    ``siblings`` are the other values of the SAME printed row, which is where the row's own
    坏账准备 column lives once ``row_reconstruct`` has read the two-level header. Passing the row
    rather than the single value is what makes the net computable per period — see
    :data:`_ALLOWANCE_MEASURE`.

    ``source_kind`` is "face" for a statement line and "note" for a note row, and it is part of the
    restatement identity rather than decoration. A face line cites the note that breaks it down —
    its ``note_number`` IS that cross-reference (七、9) — so a face row and its own note table both
    presented as note "9", the ledger's ``earlier != note`` test read them as the same source, and
    其他应收款 36,770,065.47 was counted TWICE into one gross pool. The face and the note it points
    at are two printings of one balance, which is precisely what the ledger exists to collapse.
    """
    if ev.value is None or (ev.basis.value, ev.period_label or "") != pk:
        return False
    unit = getattr(ev, "unit_ctx", None)
    prov = _allowance_beside(siblings, ev)
    meta = {"note_number": table_number, "note_heading": table_title, "source_kind": source_kind,
            "line_item": label, "value": str(ev.value), "provenance": ev.provenance}
    if prov is not None:
        meta = {**meta, "gross": str(ev.value), "allowance": str(prov.value),
                "net": str(ev.value - prov.value), "net_rule": "§3.2 账面余额 - 坏账准备"}
    sig.add(ev.value, getattr(unit, "currency", None), getattr(unit, "scale_factor", None),
            meta, allowance=(prov.value if prov is not None else None))
    return True


def _find_1(doc: DocumentModel, pk: PeriodKey, flags: list[str]) -> _Signal:
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
        row = list(li.values.values())
        for ev in row:
            _add_item(sig, li.note_number or "", li.section_hint or "", li.source_label, ev, pk,
                      row, source_kind="face")
    return sig


def _find_2(doc: DocumentModel, pk: PeriodKey, flags: list[str]) -> _Signal:
    """The receivable notes: sum the related-party lines within each note the class heading
    identifies — the class is the note's own, so an item need only prove related-party and not
    an entrusted loan."""
    sig = _Signal()
    found_note = False
    for table in doc.notes:
        if not _note_matches(table, _RECEIVABLE_NOTE_HEADING_RE):
            continue
        found_note = True
        pooled = _has_pooled_allowance(table)
        for item in table.items:
            if item.role != LineRole.LINE:
                continue
            text = f"{item.raw_label} {item.group_hint}"
            if not _RELATED_PARTY_RE.search(text):
                continue
            if _ENTRUSTED_LOAN_RE.search(text):
                flags.append("ENTRUSTED_LOAN_NOT_SEPARABLE")
                continue
            # §4.3: with only gross debtor balances and one pooled allowance, the related-party
            # share of that allowance cannot be read off, and the specification forbids
            # allocating it arbitrarily. The gross figure is still reported — a reviewer needs a
            # number to check — carrying NET_AMOUNT_NOT_DERIVABLE to say it is not yet net.
            row = list(item.values.values())
            # …unless the row carries its OWN 坏账准备 column. Then nothing is pooled about it and
            # nothing is allocated: `_add_item` subtracts the filing's own two halves (§3.2).
            if (pooled and not _EXPLICITLY_NET_RE.search(text)
                    and not any(not _is_primary_measure(e.period_label) for e in row)):
                sig.net_not_derivable = True
            for ev in row:
                _add_item(sig, table.note_number, table.title, item.raw_label, ev, pk, row)
    if not found_note:
        flags.append("MISSING_NOTE:receivable_notes")
    return sig


def _find_3(doc: DocumentModel, pk: PeriodKey, flags: list[str]) -> _Signal:
    """The related-party note: sum its own receivable-class lines, excluding payables/deposits/
    investment/guarantee lines and entrusted loans — the whole note is already related-party."""
    sig = _Signal()
    found_note = False
    for table in doc.notes:
        if not _note_matches(table, _RELATED_PARTY_NOTE_HEADING_RE):
            continue
        found_note = True
        pooled = _has_pooled_allowance(table)
        for item in table.items:
            if item.role != LineRole.LINE:
                continue
            label = item.raw_label or ""
            # THE SUB-HEADING COUNTS TOO, exactly as it does in `_find_2` above. This note prints
            # its 项目名称 column once per block and leaves the rows it governs captioned with
            # nothing but a related party's name, so the class a row belongs to reaches here on
            # `group_hint` — testing the label alone saw one row of a nine-row block.
            text = f"{label} {item.group_hint}"
            if not _RELATED_PARTY_NOTE_ITEM_RE.search(text):
                continue
            row = list(item.values.values())
            if (pooled and not _EXPLICITLY_NET_RE.search(text)
                    and not any(not _is_primary_measure(e.period_label) for e in row)):
                sig.net_not_derivable = True
            if _RELATED_PARTY_NOTE_EXCLUDE_RE.search(text):
                continue
            if _ENTRUSTED_LOAN_RE.search(text):
                flags.append("ENTRUSTED_LOAN_NOT_SEPARABLE")
                continue
            for ev in row:
                _add_item(sig, table.note_number, table.title, label, ev, pk, row)
    if not found_note:
        flags.append("MISSING_NOTE:related_party_note")
    return sig


def _cp_pool(doc: DocumentModel, pk: PeriodKey, flags: list[str]
            ) -> tuple[_Signal, _Signal, bool]:
    """CP_Gross and CP_Related_Party_Deduction: every in-scope-class line (face + notes), and the
    subset of those SAME lines that are also explicitly related-party. Entrusted loans are NOT
    excluded here — CP deducts whatever related-party amount its own gross pool actually carries.

    The third return value is whether the inclusion test COMPLETED. §3.6 permits zero "when all
    relevant notes have been searched and explicitly contain no qualifying amount", so a gross
    pool whose every line was inspected and carried no related-party marker yields a proven zero
    deduction; a pool assembled without any in-scope note to search yields no answer at all.
    """
    gross = _Signal()
    deduction = _Signal()
    inspected = False
    # Which receivable CLASSES actually contributed to the gross pool — the evidence §5.3's
    # "proven to be included in CP_Gross" asks for. Recorded as the pool is built rather than
    # re-derived afterwards, because only the scan knows which matches survived its guards.
    counted_classes: set[str] = set()
    # THE 附注 COLUMN, WHICH ALREADY DISAGREED WITH THE CAPTION AND WAS NEVER CONSULTED.
    #
    # A CAS balance sheet prints every template line whether or not the filer uses it, so a page
    # reads "拆出资金 / 交易性金融资产 / 衍生金融资产 / 应收票据 七、4 / 76,012,834.11" with the
    # first three carrying NO amount at all. Row reconstruction fuses those four captions into one
    # label, and `_CP_CLASS_RE` then matched 拆出资金 — a class in the pool — while the figure it
    # admitted belongs to 应收票据, bills receivable, which is not in the pool at all. That single
    # mis-attribution put 76,012,834.11 into CP_Gross.
    #
    # The filing itself settles it: the row cites note 4, and note 4 is 应收票据. When a FACE row's
    # cited note is a note whose own heading is not an in-scope class, the caption that matched is
    # not the caption that owns the amount, and the match is refused. Only face rows are tested —
    # a note row's note_number is the note it is IN, not one it points at.
    note_titles = {t.note_number: (t.title or "") for t in doc.notes if t.note_number}

    def _cited_note_contradicts(note_number: str, source_kind: str) -> bool:
        if source_kind != "face" or not note_number:
            return False
        title = note_titles.get(note_number)
        if not title:
            return False                      # nothing cited, or the note was never extracted
        return not _CP_NOTE_HEADING_RE.search(title)

    def _scan(label: str, group_hint: str, note_number: str, note_title: str, ev,
              row=(), source_kind: str = "note") -> None:
        nonlocal inspected
        text = f"{label} {group_hint}"
        klass = _CP_CLASS_RE.search(text)
        if not klass:
            return
        if _cited_note_contradicts(note_number, source_kind):
            return
        if not _add_item(gross, note_number, note_title, label, ev, pk, row,
                         source_kind=source_kind):
            return
        counted_classes.add(klass.group(0))
        # This line belongs to the gross pool and has now been tested for a related-party
        # marker, which is what makes a nil deduction a finding rather than a gap.
        inspected = True
        if _RELATED_PARTY_RE.search(text):
            _add_item(deduction, note_number, note_title, label, ev, pk, row,
                      source_kind=source_kind)

    for li in doc.line_items:
        if li.printed_in not in (None, PrintedIn.FACE):
            continue
        row = list(li.values.values())
        for ev in row:
            _scan(li.source_label, li.group_hint, li.note_number or "", li.section_hint or "", ev,
                  row, source_kind="face")

    found_note = False
    for table in doc.notes:
        if not _note_matches(table, _CP_NOTE_HEADING_RE):
            continue
        found_note = True
        for item in table.items:
            if item.role != LineRole.LINE:
                continue
            row = list(item.values.values())
            for ev in row:
                _scan(item.raw_label or "", item.group_hint, table.note_number, table.title, ev,
                      row)
    # §5.3: "Deduct only related-party amounts proven to be included in CP_Gross."
    #
    # THE PROOF IS THE CLASS, and until now nothing supplied it, so the deduction was always zero.
    # The related-party note is titled 应收、应付关联方等未结算项目情况, which matches no
    # `_CP_NOTE_HEADING_RE` alternative — correctly, it is not a receivable-class note — so its rows
    # never entered the scan above, and the scan is the only thing that can mark a row
    # related-party. CP_Gross therefore carried its 其他应收款 pool GROSS of related parties while
    # the LTP row reported the very same related-party money, putting it in two places at once,
    # which is exactly what §2 forbids.
    #
    # What links them is the receivable CLASS. CP_Gross counted 其他应收款; the related-party note
    # discloses the 其他应收款 owed by related parties for the same period; those balances are
    # necessarily part of that pool. Gated on the class having actually been COUNTED into gross —
    # a related-party balance in a class this pool never included is not "proven to be included",
    # and deducting it would understate the row.
    for table in doc.notes:
        if not _note_matches(table, _RELATED_PARTY_NOTE_HEADING_RE):
            continue
        for item in table.items:
            if item.role != LineRole.LINE:
                continue
            label = item.raw_label or ""
            text = f"{label} {item.group_hint}"
            if _RELATED_PARTY_NOTE_EXCLUDE_RE.search(text):
                continue                        # a payable, a deposit, an investment — not this
            klass = _CP_CLASS_RE.search(text)
            if not klass or klass.group(0) not in counted_classes:
                continue
            row = list(item.values.values())
            for ev in row:
                _add_item(deduction, table.note_number, table.title, label, ev, pk, row,
                          source_kind="related_party_note")

    if not found_note and gross.value is None:
        flags.append("MISSING_NOTE:cp_gross_notes")
    flags.extend(gross.unit_flags("CP_Gross"))
    flags.extend(deduction.unit_flags("CP_Related_Party_Deduction"))

    return gross, deduction, inspected


@dataclass
class ReceivablesResult:
    value: Decimal | None
    formula_used: str | None
    status: str
    flags: list[str]
    evidence: list[dict]


def compute(doc: DocumentModel) -> dict[PeriodKey, dict[str, ReceivablesResult]]:
    # ONLY PRIMARY MEASURES ARE PERIODS. Once a two-level header is read, a row also carries
    # "current:allowance" — a measure OF the current period, not a period of its own. Left in this
    # set it became a (basis, period) the whole computation was run for, and the answer published
    # under it would be a sum of bad-debt provisions presented as related-party receivables.
    keys: set[PeriodKey] = set()
    for li in doc.line_items:
        for ev in li.values.values():
            if _is_primary_measure(ev.period_label):
                keys.add((ev.basis.value, ev.period_label or ""))
    for table in doc.notes:
        for item in table.items:
            for ev in item.values.values():
                if _is_primary_measure(ev.period_label):
                    keys.add((ev.basis.value, ev.period_label or ""))

    out: dict[PeriodKey, dict[str, ReceivablesResult]] = {}
    for pk in keys:
        ltp_flags: list[str] = []
        signals = {"Find_1": _find_1(doc, pk, ltp_flags),
                   "Find_2": _find_2(doc, pk, ltp_flags),
                   "Find_3": _find_3(doc, pk, ltp_flags)}
        for name, signal in signals.items():
            ltp_flags.extend(signal.unit_flags(name))

        # §4.5: "A candidate is valid only when ... currency and unit are known or normalized",
        # and §8's max_valid keeps only candidates that are present and non-negative. A negative
        # related-party receivable is not a smaller measurement of the same concept, it is a
        # broken one, and letting it win a MAX would publish it.
        valid = {name: s.usable for name, s in signals.items()
                 if s.usable is not None and s.usable >= 0}
        rejected = [name for name, s in signals.items()
                    if s.usable is not None and s.usable < 0]
        if rejected:
            ltp_flags.append(f"NEGATIVE_RESIDUAL:{','.join(sorted(rejected))}")

        if not valid:
            ltp_result = ReceivablesResult(None, None, "NOT_COMPUTABLE",
                                           ltp_flags + ["NOT_COMPUTABLE"], [])
        else:
            selected = max(valid.values())
            # §4.5 tie handling: select the value once and retain every tied candidate as a
            # supporting source. Agreement between independent measurements corroborates the
            # figure — it is not the duplication §3.5 warns about.
            tied = sorted(name for name, v in valid.items() if v == selected)
            evidence = [e for name in tied for e in signals[name].evidence]
            formula = (f"MAX_VALID({tied[0]})" if len(tied) == 1
                       else f"MAX_VALID({tied[0]}; corroborated by {', '.join(tied[1:])})")
            ltp_result = ReceivablesResult(selected, formula, "COMPUTED", ltp_flags, evidence)
        out.setdefault(pk, {})["ltp"] = ltp_result

        cp_flags: list[str] = []
        gross_sig, deduction_sig, inspected = _cp_pool(doc, pk, cp_flags)
        cp_evidence = gross_sig.evidence + deduction_sig.evidence
        cp_gross = gross_sig.usable
        if cp_gross is None:
            cp_result = ReceivablesResult(None, None, "NOT_COMPUTABLE",
                                          cp_flags + ["NOT_COMPUTABLE"], cp_evidence)
        else:
            candidate, status, extra_flags = _finalize_cp(
                cp_gross, deduction_sig.usable, inspected=inspected)
            formula = ("CP_Gross - CP_Related_Party_Deduction" if candidate is not None else None)
            cp_result = ReceivablesResult(candidate, formula, status,
                                          cp_flags + extra_flags, cp_evidence)
        out[pk]["cp"] = cp_result
    return out


def _finalize_cp(gross: Decimal, deduction: Decimal | None, *, inspected: bool
                ) -> tuple[Decimal | None, str, list[str]]:
    """CP_Gross - CP_Related_Party_Deduction (§5.4).

    Two outcomes are deliberately null rather than a figure with a flag:

    A deduction that could not be established (§8) would otherwise publish the GROSS pool as
    though it were already net of related-party balances — putting the same related-party money
    in this row AND in Due from Related Parties (LTP), which is the double count §3.5 and §5.3
    exist to prevent. §3.6 still allows a zero deduction, but only for a completed search: hence
    `inspected`, which says the gross pool's own lines were tested for a related-party marker
    and carried none.

    A negative candidate (§5.4) "indicates an extraction, scope, unit, or duplication issue".
    This row is a summed child of Other Current Assets and Total Current Assets, so publishing a
    negative asset would silently break the balance-sheet identity two levels up and send the
    reviewer looking at a total whose own arithmetic is sound. A blank names the row that failed.
    """
    flags: list[str] = []
    if deduction is None:
        if not inspected:
            return None, "RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED", [
                "RELATED_PARTY_DEDUCTION_NOT_ESTABLISHED"]
        # §3.6: searched and explicitly nothing there.
        deduction = Decimal(0)
        flags.append("RELATED_PARTY_DEDUCTION_PROVEN_NIL")
    candidate = gross - deduction
    if candidate < 0:
        return None, "NEGATIVE_RESIDUAL", flags + ["NEGATIVE_RESIDUAL"]
    return candidate, "COMPUTED", flags
