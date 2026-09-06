"""Contingent Liabilities — PRC filings' 或有负债/担保/未决诉讼 notes.

Implements docs/PRC_Contingent_Liabilities_Extraction_Logic_Revised.md. Unlike every other
computed field in this package, the target is not one number: it is a short narrative paragraph
plus a classified-summary table (Letters of Credit, Performance bonds, Bank guarantees, Corporate
guarantees) and an unclassified-items table, built from every item the identified notes disclose —
classified in that fixed priority order so a broad "guarantee" caption never displaces a more
specific instrument, summed once per type and currency, and never inferred to zero from silence.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.models.document import DocumentModel
from app.core.models.enums import LineRole
from app.services.mapping import normalize_label
from app.ports.llm import LlmMeta, LlmProvider

PeriodKey = tuple[str, str]                 # (basis, period_label)

# ── section 2: which notes carry contingent-liability disclosures ──────────────────────────────
# Chinese (PRC) titles plus their English (HKEX) equivalents, since the same canonical note can be
# printed in either language depending on the filing.
_NOTE_HEADING_RE = re.compile(
    r"或有负债|或有事项|关联方担保|关联担保|未决诉讼|未决仲裁|诉讼及仲裁事项|重大诉讼、?仲裁事项"
    r"|对外担保|担保事项|承诺及或有事项|承诺事项及或有事项"
    # \b on the bare English alternative: unbounded, "guarantee" also matched a DEBT-INSTRUMENT
    # note titled "GUARANTEED NOTES", pulling its rows in as contingent exposures (which is what
    # raised POSSIBLE_DUPLICATE / AMOUNT_NOT_DISCLOSED on real filings). "GUARANTEES",
    # "GUARANTEES GIVEN" and "CONTINGENT LIABILITIES AND GUARANTEES" still match.
    r"|contingent\s+liabilit(?:y|ies)|contingencies|\bguarantees?\b(?:\s+given|\s+issued)?"
    r"|pending\s+litigation|pending\s+arbitration|litigation\s+and\s+arbitration",
    re.IGNORECASE)

# ── section 3: amount labels that DO / do not represent the exposure itself ────────────────────
_AMOUNT_LABEL_RE = re.compile(
    r"担保金额|担保余额|担保责任余额|实际担保金额|尚未履行金额|未结金额|涉案金额|诉讼金额|仲裁金额"
    r"|或有负债金额|预计财务影响"
    r"|guarantee(?:d)?\s+amount|amount\s+guaranteed|outstanding\s+amount|amount\s+in\s+dispute"
    r"|amount\s+claimed|estimated\s+financial\s+impact",
    re.IGNORECASE)
_NON_EXPOSURE_LABEL_RE = re.compile(
    r"授信额度|合同总额|累计发生额|交易金额|已偿还金额|已解除担保金额|已确认预计负债"
    r"|credit\s+facility|total\s+contract\s+value|cumulative\s+amount|transaction\s+amount"
    r"|repaid\s+amount|released\s+guarantee|provision\s+recognised",
    re.IGNORECASE)

# ── section 4: classification vocabulary, checked in this priority order ───────────────────────
_LETTER_OF_CREDIT_RE = re.compile(
    r"信用证|已开立信用证|未结信用证|未到期信用证|不可撤销信用证|备用信用证|跟单信用证"
    r"|letters?\s+of\s+credit|standby\s+l/?c|documentary\s+credit",
    re.IGNORECASE)
_PERFORMANCE_BOND_RE = re.compile(
    # 履约保证(?!金): the guarantee term, but not as the prefix of 履约保证金 below.
    r"履约保函|履约保证(?!金)|履约担保|合同履约保函|工程履约保函"
    r"|performance\s+bonds?|performance\s+guarantees?",
    re.IGNORECASE)
# §4.3: 履约保证金 is a performance bond ONLY where the disclosure describes a contingent
# guarantee or bond exposure. Standing alone it names an ordinary refundable deposit — an asset
# the entity paid out, not an obligation it might owe — so it needs corroborating bond language.
_PERFORMANCE_DEPOSIT_RE = re.compile(r"履约保证金")
_BOND_EXPOSURE_RE = re.compile(
    r"保函|担保|或有|guarantee|bond|contingen", re.IGNORECASE)
_BANK_GUARANTEE_RE = re.compile(
    r"银行保函|银行保证|银行出具的保函|融资性保函|非融资性保函|付款保函|预付款保函|投标保函"
    r"|bank\s+guarantees?|banker'?s?\s+guarantees?|bid\s+bonds?",
    re.IGNORECASE)
_CORPORATE_GUARANTEE_RE = re.compile(
    r"公司担保|企业担保|对外担保|关联方担保|为子公司提供担保|为关联方提供担保|债务担保|借款担保"
    r"|融资担保|连带责任保证|保证责任"
    r"|corporate\s+guarantees?|guarantees?\s+(?:given|issued|provided)\s+(?:to|for|on\s+behalf\s+of)"
    # …and the active-voice ordering an HKEX filing actually prints ("given guarantees to banks
    # for facilities utilised by joint ventures"). Added after the passive form so the priority
    # order in _CLASSIFY_ORDER is untouched — an LC / performance bond / bank guarantee still wins.
    r"|(?:given|issued|provided)\s+guarantees?\s+(?:to|for|on\s+behalf\s+of)"
    r"|guarantees?\s+in\s+respect\s+of\s+(?:banking\s+facilities|borrowings|loans)",
    re.IGNORECASE)
_CLASSIFY_ORDER = (
    ("Letters of Credit", _LETTER_OF_CREDIT_RE),
    ("Performance bonds", _PERFORMANCE_BOND_RE),
    ("Bank guarantees", _BANK_GUARANTEE_RE),
    ("Corporate guarantees", _CORPORATE_GUARANTEE_RE),
)
UNCLASSIFIED = "Unclassified contingent liability"

# ── section 7: matter-type phrase, for the short English statement of an unclassified item ─────
_MATTER_TYPES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"未决诉讼|pending\s+litigation|legal\s+proceedings?", re.IGNORECASE), "Pending litigation"),
    (re.compile(r"未决仲裁|pending\s+arbitration", re.IGNORECASE), "Pending arbitration"),
    (re.compile(r"合同纠纷|contract\s+dispute", re.IGNORECASE), "A contract dispute"),
    (re.compile(r"索赔|claims?\s+(?:against|made|received)", re.IGNORECASE), "A claim"),
    (re.compile(r"税务争议|tax\s+dispute", re.IGNORECASE), "A tax dispute"),
    (re.compile(r"产品质量保证|product\s+warrant(?:y|ies)", re.IGNORECASE), "A product warranty matter"),
    (re.compile(r"亏损合同|onerous\s+contracts?", re.IGNORECASE), "An onerous contract"),
    (re.compile(r"环境责任|environmental\s+liabilit(?:y|ies)", re.IGNORECASE), "An environmental liability matter"),
    (re.compile(r"票据追索|bill\s+recourse", re.IGNORECASE), "A bill-recourse liability matter"),
)


def _classify(text: str) -> tuple[str, list[str]]:
    for label, pattern in _CLASSIFY_ORDER:
        m = pattern.search(text)
        if m:
            return label, [m.group(0)]
    deposit = _PERFORMANCE_DEPOSIT_RE.search(text)
    if deposit and _BOND_EXPOSURE_RE.search(text):
        return "Performance bonds", [deposit.group(0)]
    return UNCLASSIFIED, []


def _matter_type(text: str) -> str:
    for pattern, phrase in _MATTER_TYPES:
        if pattern.search(text):
            return phrase
    return "A contingent matter"


@dataclass
class ContingentItem:
    description: str
    classification: str
    classification_basis: list[str]
    amount: Decimal | None
    currency: str | None
    scale: Decimal | None
    note_number: str
    note_heading: str
    page: int | None
    counterparty: str | None
    duplicate_of: str | None = None


def _amount_of(item, pk: PeriodKey) -> tuple[Decimal | None, str | None, Decimal | None, int | None]:
    for ev in item.values.values():
        if ev.value is None or (ev.basis.value, ev.period_label or "") != pk:
            continue
        page = ev.provenance.page_index if ev.provenance else None
        return ev.value, ev.unit_ctx.currency, ev.unit_ctx.scale_factor, page
    return None, None, None, None


# ── section 3: an exposure the note states only in PROSE, with no row of its own ───────────────
# A guarantee is routinely disclosed as a sentence rather than a table line ("guarantees given to
# banks for mortgage loans of end-buyers amounted to approximately HK$375,901,000 (2024: …)"), and
# reading only rows loses it — which understates the concept by whatever the sentence carried.
_PARA_SPLIT_RE = re.compile(r"(?=\((?:[a-z]|[ivx]{1,4})\)\s)")
_PROSE_EXPOSURE_RE = re.compile(
    r"(?:contingent\s+liabilit(?:y|ies)|guarantees?|amount\s+(?:claimed|in\s+dispute))"
    r"[^.]{0,240}?amounted\s+to\s+(?:approximately\s+)?(?:HK\$|RMB|US\$)?\s*([\d,]+)"
    r"\s*\(\s*20\d{2}\s*:\s*(?:HK\$|RMB|US\$)?\s*([\d,]+)\s*\)", re.IGNORECASE)
# Prose spells the amount out in full while the note's table is printed in thousands, so the prose
# figure is divided by the scale THE NOTE ITSELF declares — never by a hardcoded 1,000, which is a
# 1000x error on a millions presentation. A note declaring no scale is left to the narrative
# instead of being guessed onto one.
_NOTE_SCALE_TOKENS = ((re.compile(r"['’]000['’,]?000"), Decimal(1_000_000)),
                      (re.compile(r"['’]000"), Decimal(1000)))


def _prose_divisor(text: str) -> Decimal | None:
    for rx, factor in _NOTE_SCALE_TOKENS:
        if rx.search(text or ""):
            return factor
    return None


def _prose_items(table, pk: PeriodKey, unit, row_amounts: set) -> list[ContingentItem]:
    """Exposures stated in the note's prose for THIS period, excluding any already tabulated."""
    period = (pk[1] or "").lower()
    # The bracketed comparative is what identifies the two periods, so only a genuine
    # current/prior key is served; junk column keys ("col7", "Total") are skipped rather than
    # having a prose amount attributed to them.
    group = 1 if period.startswith("current") else \
        2 if period.startswith(("prior", "comparative")) else 0
    divisor = _prose_divisor(table.source_text or "")
    if not group or divisor is None:
        return []
    currency, scale, page = unit
    out: list[ContingentItem] = []
    for para in _PARA_SPLIT_RE.split(table.source_text or ""):
        m = _PROSE_EXPOSURE_RE.search(para)
        if not m:
            continue
        amount = Decimal(m.group(group).replace(",", "")) / divisor
        if amount in row_amounts:
            continue                          # §6.4: this exposure is already tabulated
        text = re.sub(r"\s+", " ", para).strip()
        classification, basis_terms = _classify(text)
        out.append(ContingentItem(
            description=text[:300], classification=classification,
            classification_basis=basis_terms, amount=amount, currency=currency, scale=scale,
            note_number=table.note_number, note_heading=table.title, page=page,
            counterparty=None))
    return out


def _extract_items(doc: DocumentModel, pk: PeriodKey) -> tuple[list[ContingentItem], bool]:
    """Section 2/3: every qualifying item from every identified note, for one (basis, period)."""
    items: list[ContingentItem] = []
    found_note = False
    # Unit per NOTE, resolved in one pass before the tables are walked: a note is extracted as
    # several fragments and the one carrying the prose often carries no figure, so a per-fragment
    # lookup would leave the prose amount with no currency/scale depending on fragment order.
    note_units: dict[str, tuple] = {}
    for table in doc.notes:
        if not _NOTE_HEADING_RE.search(table.title or "") or table.note_number in note_units:
            continue
        for note_item in table.items:
            amount, currency, scale, page = _amount_of(note_item, pk)
            if amount is not None:
                note_units[table.note_number] = (currency, scale, page)
                break
    for table in doc.notes:
        if not _NOTE_HEADING_RE.search(table.title or ""):
            continue
        found_note = True
        for note_item in table.items:
            if note_item.role in (LineRole.HEADER, LineRole.SPACER, LineRole.TOTAL, LineRole.SUBTOTAL):
                continue                                # section 6.3: never a note's own total row
            label = note_item.raw_label or ""
            text = f"{label} {note_item.group_hint}"
            if _NON_EXPOSURE_LABEL_RE.search(text) and not _AMOUNT_LABEL_RE.search(text):
                continue                                # section 3: not the exposure amount
            classification, basis_terms = _classify(text)
            amount, currency, scale, page = _amount_of(note_item, pk)
            items.append(ContingentItem(
                description=label, classification=classification,
                classification_basis=basis_terms, amount=amount, currency=currency, scale=scale,
                note_number=table.note_number, note_heading=table.title, page=page,
                counterparty=note_item.group_hint or None))
        # …then the exposures this note states only in prose, skipping any amount already read
        # from one of its rows so a figure printed both ways is not counted twice.
        items.extend(_prose_items(
            table, pk, note_units.get(table.note_number, (None, None, None)),
            {it.amount for it in items
             if it.note_number == table.note_number and it.amount is not None}))
    return items, found_note


def _dedupe(items: list[ContingentItem]) -> list[ContingentItem]:
    """§6.4: the same underlying exposure repeated across notes is one item.

    §6.4 names 或有负债, 关联方担保, 对外担保 and 未决诉讼 as places one exposure is printed more
    than once, and says to "treat repeated descriptions of the same underlying exposure as one
    item" — so differing wording is the expected shape of a restatement and cannot be part of the
    key. The indicator that separates two exposures is the counterparty ("same counterparty or
    case"), which is: two guarantees of equal size to DIFFERENT counterparties are two items,
    while the same figure restated under a second note heading is one.

    A repeat inside a single note is not a cross-note restatement, so the note number decides
    whether two matching rows are one item or two.
    """
    seen: dict[tuple, ContingentItem] = {}
    out: list[ContingentItem] = []
    for it in items:
        if it.amount is None:
            out.append(it)                      # §6.5: kept in the narrative, never in a total
            continue
        key = (it.classification, str(it.amount), it.currency, it.scale,
               normalize_label(it.counterparty or ""))
        earlier = seen.get(key)
        if earlier is not None and earlier.note_number != it.note_number:
            earlier.duplicate_of = earlier.duplicate_of or it.note_number
            continue
        if earlier is None:
            seen[key] = it
        out.append(it)
    return out


def _classified_summary(items: list[ContingentItem]) -> list[dict]:
    """§6.2: one row per type AND per currency, and only compatible units are added.

    A scale is part of what makes two amounts addable, so it joins the grouping key — a type
    disclosed in both thousands and millions produces two rows rather than one wrong sum.
    """
    groups: dict[tuple[str, str | None, Decimal | None], dict] = {}
    for it in items:
        if it.classification == UNCLASSIFIED or it.amount is None:
            continue
        key = (it.classification, it.currency, it.scale)
        g = groups.setdefault(key, {"type": it.classification, "amount": Decimal(0),
                                    "currency": it.currency, "scale": it.scale,
                                    "item_count": 0, "source_pages": []})
        g["amount"] += it.amount
        g["item_count"] += 1
        if it.page is not None and it.page not in g["source_pages"]:
            g["source_pages"].append(it.page)
    return list(groups.values())


def _quantifiable_total(classified: list[dict]) -> tuple[Decimal | None, list[str]]:
    """The one figure this concept publishes on its row, when one figure is meaningful.

    §6.2 forbids converting currencies without a reported conversion, and a scale is part of
    what makes two amounts addable. Where the classified groups span more than one
    currency-and-scale, there is no single total to publish: the per-currency subtotals in
    `classified_summary` are the answer, and the row reports none rather than a sum of unlike
    units that would look authoritative.
    """
    if not classified:
        return None, []
    units = {(g["currency"], g["scale"]) for g in classified}
    if len(units) > 1:
        return None, ["MULTIPLE_CURRENCIES_NOT_AGGREGATED"]
    return sum((g["amount"] for g in classified), Decimal(0)), []


def _unclassified_statement(it: ContingentItem) -> str:
    matter = _matter_type(f"{it.description} {it.note_heading}")
    who = f" involving {it.counterparty}" if it.counterparty else ""
    if it.amount is None:
        return f"{matter}{who}; the amount was not disclosed or could not be quantified."
    amt = f"{it.currency + ' ' if it.currency else ''}{it.amount:,}"
    return f"{matter}{who}, with a disclosed exposure of {amt}."


def _summary_paragraph(classified: list[dict], unclassified: list[ContingentItem]) -> str:
    if not classified and not unclassified:
        return ("The filing's contingent-liability notes were identified but disclosed no "
                "items this logic could extract.")
    parts: list[str] = []
    types = sorted({g["type"] for g in classified})
    if types:
        parts.append(f"The filing discloses contingent liabilities relating to {', '.join(types)}.")
        by_type = ", ".join(f"{g['type']} of {g['currency'] + ' ' if g['currency'] else ''}"
                            f"{g['amount']:,}" for g in classified)
        parts.append(f"Quantifiable exposures comprise {by_type}.")
    if unclassified:
        matters = sorted({_matter_type(f"{it.description} {it.note_heading}") for it in unclassified})
        parts.append(f"Additional unclassified matters relate to {', '.join(matters).lower()}.")
    not_quantified = sum(1 for it in unclassified if it.amount is None)
    if not_quantified:
        parts.append(f"{not_quantified} disclosed matter(s) could not be quantified from the filing.")
    return " ".join(parts)


@dataclass
class ContingentLiabilitiesResult:
    summary_paragraph: str
    classified_summary: list[dict]
    unclassified_items: list[dict]
    total_quantifiable: Decimal | None
    status: str
    flags: list[str] = field(default_factory=list)


def compute(doc: DocumentModel) -> dict[PeriodKey, ContingentLiabilitiesResult]:
    matching_notes = [t for t in doc.notes if _NOTE_HEADING_RE.search(t.title or "")]
    if not matching_notes:
        return {}
    keys: set[PeriodKey] = set()
    for table in matching_notes:
        for item in table.items:
            for ev in item.values.values():
                keys.add((ev.basis.value, ev.period_label or ""))
    # A note with no numeric column at all (a pure narrative disclosure) still needs a result, so
    # a matching note with nothing to iterate for it otherwise contributes a default key.
    if not keys:
        keys.add(("consolidated", "current"))

    out: dict[PeriodKey, ContingentLiabilitiesResult] = {}
    for pk in keys:
        raw_items, found_note = _extract_items(doc, pk)
        if not found_note:
            out[pk] = ContingentLiabilitiesResult(
                "", [], [], None, "NO_NOTES_FOUND", ["MISSING_CONTINGENT_LIABILITY_NOTES"])
            continue
        items = _dedupe(raw_items)
        flags = ["POSSIBLE_DUPLICATE"] if any(it.duplicate_of for it in items) else []
        for it in items:
            if it.amount is None:
                flags.append("AMOUNT_NOT_DISCLOSED")
                break
        classified = _classified_summary(items)
        unclassified = [it for it in items if it.classification == UNCLASSIFIED]
        total, total_flags = _quantifiable_total(classified)
        flags.extend(total_flags)
        out[pk] = ContingentLiabilitiesResult(
            _summary_paragraph(classified, unclassified),
            classified,
            [{"short_statement": _unclassified_statement(it),
              "amount": it.amount, "currency": it.currency,
              "source_note": it.note_number, "page": it.page} for it in unclassified],
            total, "COMPUTED", flags)
    return out


# ── optional LLM narrative pass — see docstring on enhance_with_llm ─────────────────────────────
class ContingentLiabilitiesNarrative(BaseModel):
    summary_paragraph: str = Field(
        description="A concise paragraph covering the types identified, the total for each "
                    "quantifiable type, the presence of unclassified matters, and whether any "
                    "could not be quantified. Grounded only in the supplied facts.")
    unclassified_statements: list[str] = Field(
        default_factory=list,
        description="One improved English statement per unclassified item, in the SAME order as "
                    "given, using only the amount/currency/description supplied — never a "
                    "different figure and never a new item.")


_NARRATIVE_SYSTEM = (
    "You are a financial-disclosure analyst. You are given a deterministic classification of a "
    "filing's contingent-liability notes: totals already computed by type and currency, and a "
    "list of items that could not be classified into one of the four standard types. Write:\n"
    "1) A concise summary paragraph covering the types identified, the total for each "
    "quantifiable type, the presence of unclassified matters, and whether any could not be "
    "quantified.\n"
    "2) One short, factual English statement per unclassified item, in the given order.\n"
    "Use ONLY the figures and facts provided — never invent an item, a classification, an "
    "amount, or a counterparty, and never state that no contingent liabilities exist merely "
    "because an amount was not disclosed."
)


def build_narrative_payload(result: ContingentLiabilitiesResult) -> dict:
    return {
        "classified_summary": [
            {"type": g["type"], "amount": str(g["amount"]), "currency": g["currency"],
             "item_count": g["item_count"]}
            for g in result.classified_summary
        ],
        "unclassified_items": [
            {"description": it.get("short_statement"),
             "amount": str(it["amount"]) if it.get("amount") is not None else None,
             "currency": it.get("currency")}
            for it in result.unclassified_items
        ],
    }


def enhance_with_llm(provider: LlmProvider, result: ContingentLiabilitiesResult, *,
                     max_tokens: int = 2048) -> tuple[ContingentLiabilitiesResult, LlmMeta | None]:
    """Rewrite the summary paragraph and each unclassified item's statement in clearer English.

    Classification and every amount stay exactly what the deterministic pass computed — the model
    only ever rewrites prose already grounded in those facts. Nothing here can change a total, add
    an item, or remove one; a mismatched response (wrong statement count) is treated the same as a
    failed call by the caller, which keeps the deterministic version rather than guess a pairing.
    """
    if not result.classified_summary and not result.unclassified_items:
        return result, None
    payload = build_narrative_payload(result)
    messages = [{"role": "user", "content": json.dumps(payload, indent=2)}]
    narrative, meta = provider.complete_structured(
        system=_NARRATIVE_SYSTEM, messages=messages,
        response_schema=ContingentLiabilitiesNarrative, max_tokens=max_tokens)
    assert isinstance(narrative, ContingentLiabilitiesNarrative)
    unclassified = result.unclassified_items
    if len(narrative.unclassified_statements) == len(result.unclassified_items):
        unclassified = [{**it, "short_statement": s}
                        for it, s in zip(result.unclassified_items, narrative.unclassified_statements)]
    enhanced = ContingentLiabilitiesResult(
        narrative.summary_paragraph or result.summary_paragraph,
        result.classified_summary, unclassified, result.total_quantifiable,
        result.status, [*result.flags, "LLM_NARRATIVE"])
    return enhanced, meta
