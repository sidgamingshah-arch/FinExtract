"""Specification-derived alias curation for the eight concepts docs/*_Extraction_Logic*.md govern.

scripts/build_output_csv_template.py builds each concept's alias list from the ontology workbook
and then enriches it with quoted phrases scraped out of the Extraction Logic workbook's free-text
formula column. That column describes arithmetic, so the phrases it quotes include the components
a formula DEDUCTS ("Derivative", "Other receivables"), the notes it merely reads ("fair value
measurement/hierarchy"), instruments a rule explicitly excludes ("Entrustment loan to related
parties/..."), and occasionally the specification's own prose ("Find 2: Sum of due from related
parties included in Note"). As aliases those point the caption mapper at precisely the concepts
their specification says to exclude, which produces a wrong figure rather than a missing one.

This module states, per concept, what its specification forbids and requires. The generator
applies it so a rebuild cannot reintroduce the contamination; tests/test_spec_conformance.py pins
the same rules against the generated artefact, so the two cannot drift apart silently.
"""
from __future__ import annotations

import re

OPER_EXP = "is_pl__deprec_and_impairment_oper_exp"
COS = "is_pl__deprec_and_impairment_cos"
SECUR_CP = "bs_ca__secur_and_other_fincl_assets_cp"
SECUR_LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
CONTINGENT = "notes__contingent_liabilities"
DUE_FROM_RP = "bs_nca__due_from_related_parties_ltp"
OTHER_RECV_CP = "bs_ca__other_receivables_cp"
OTHER_RECV_LTP = "bs_nca__other_receivables_ltp"
SALES = "is_pl__sales_revenues"

# Depreciation, opening note: "this rule includes only the depreciation and specified
# lease-related amortisation items listed below. Do not include impairment." An asset's own name
# is its carrying amount, never the period's charge, so those are denied too.
_DEPRECIATION_DENIALS = (
    r"^Depreciation, impairment and amortisation$",
    r"^Impairment of ",
    r"^Investment Property$",
    r"^Construction in progress$",
    r"^Prepaid land lease payment$",
    r"^Property,plant and equipments / fixed assets$",
    r"^摊销$", r"^无形资产摊销$", r"^折旧及摊销$", r"^折旧及摊销费用$",
    r"^固定资产$", r"^在建工程$", r"^在建资产$", r"^投资性房地产$", r"^投资物业$",
    r"^物业、厂房及设备$", r"^物业及设备$",
)

# Securities §4.2: these are subtracted FROM the note total. §4.3: the fair-value hierarchy is
# the source note for the Level 3 amount. The trade-receivable and prepayment captions are
# contamination — they are bs_ca__other_receivables_cp's own vocabulary.
_SECURITIES_DENIALS = (
    r"^Derivative$", r"^Other receivables$",
    r"^Investment in related parties/associates/JV$",
    r"fair value measurement/hierarchy",
    r"^衍生金融工具$", r"^其他应收款项$",
    r"^按金", r"^贸易", r"^预付款项", r"^合同资产$", r"^以摊余成本计量的金融资产$",
)

# "Other receivables" is, by definition, the receivables that are NOT trade receivables — so a
# caption naming a trade receivable cannot belong to it. This is an accounting contradiction
# rather than a preference, and it was doing measurable damage: bs_nca__other_receivables_ltp
# claimed "Trade receivables" as an exact alias at match_priority 81, outranking the genuine
# current trade-receivable concepts at 80. A filing printing plain "Trade receivables" was
# therefore filed as a NON-CURRENT other receivable at confidence 1.0, which also broke the
# balance sheet: the figure left the current subtotal, and the printed total stopped agreeing
# with its components.
#
# Section disambiguation is what normally separates the current and non-current claimants of one
# caption — the same instrument legitimately appears in both — but it can only decide when the
# filing prints the section headers, and priority breaks the tie when it cannot. The fix is
# therefore to stop the contradiction being claimable at all, not to re-rank the priorities.
#
# §5.1 of the PRC receivables logic says the same thing for the CP twin, whose gross pool is
# 其他应收款项 / 其他应收款 / 一年内到期的长期应收款 / 一年内到期的贷款及垫款 / 拆出资金 / 往来款 —
# trade receivables are not among them.
_TRADE_RECEIVABLE_DENIALS = (
    r"^Trade receivables$", r"^Trade and other receivables",
    r"^Accounts and other receivables$",
    r"^应收账款", r"^贸易", r"^应收客户合同工程款$", r"^未开票应收款$",
)

ALIAS_DENIALS: dict[str, tuple[str, ...]] = {
    OPER_EXP: _DEPRECIATION_DENIALS,
    COS: _DEPRECIATION_DENIALS,
    SECUR_CP: _SECURITIES_DENIALS,
    # §6.1: the money-market and marketable-securities headings are CP-only unless the filing
    # itself classifies them as non-current, which the note's own face citation decides.
    SECUR_LTP: (*_SECURITIES_DENIALS, r"^Money market instruments$", r"^Marketable securities$",
                r"^short term money market deposits$", r"^货币市场工具$", r"^有价证券$",
                r"^短期货币市场存款$"),
    # §3.4: entrusted loans are excluded from every related-party calculation. "Find n: ..." is
    # the specification's own formula-summary prose.
    DUE_FROM_RP: (r"[Ee]ntrust", r"委托", r"^Find \d", r"^Related Party Transactions$"),
    # §5.1's pool includes neither contract assets nor trade receivables.
    OTHER_RECV_CP: (r"^合同资产$", r"^合約資產$", *_TRADE_RECEIVABLE_DENIALS),
    OTHER_RECV_LTP: (r"^合同资产$", r"^合約資產$", *_TRADE_RECEIVABLE_DENIALS),
    # §4: "Do not use total 营业收入 as a fallback when 主营业务 or 主营业务收入 is not separately
    # disclosed." The prohibition is on the Chinese pair — on an English HKEX filing "Turnover"
    # IS the revenue line, so the English captions stay.
    SALES: (r"^收益$", r"^营业总收入$", r"^营业收入$", r"^营业额$", r"^销售收入$",
            r"^客户合约收益$"),
}


def _denied(canonical_key: str, alias: str) -> bool:
    patterns = ALIAS_DENIALS.get(canonical_key)
    if not patterns:
        return False
    return any(re.search(p, alias, re.IGNORECASE) for p in patterns)


def curate_aliases(canonical_key: str, aliases: list[str]) -> list[str]:
    """`aliases` with everything this concept's specification excludes removed.

    Order is preserved: the generator's own ranking of a concept's aliases is meaningful, and
    denial is a filter over it rather than a re-sort.
    """
    return [a for a in aliases if not _denied(canonical_key, a)]


def denied_aliases(canonical_key: str, aliases: list[str]) -> list[str]:
    """The complement of :func:`curate_aliases` — what was removed, for the build log."""
    return [a for a in aliases if _denied(canonical_key, a)]
