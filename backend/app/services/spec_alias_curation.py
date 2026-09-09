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

ONE CAVEAT ON REACH, and it is not small. A denial only matters if the caption mapper reads the
list it filters, and on the shipped `output_csv_hk_ontology.json` four of the eight spec-governed
concepts declare `alias_matching: disabled` — OPER_EXP (21 aliases), COS (17), SECUR_LTP (28) and
OTHER_RECV_CP (29), 95 alias strings in total. `OntologyMatcher.__init__` puts those in
`_locked`/`_unmatchable` and indexes their aliases nowhere, and `_rule_claim` skips `_unmatchable`
BEFORE it evaluates hints (mapping.py:1720 vs :1727), so their `regex_hints` do not run either.
The denials stay regardless — a lock can be lifted, and the contamination would come straight
back — but the exception is now DECLARED rather than discovered: see
`tests/test_spec_conformance._UNMATCHABLE_BY_DESIGN`, which fails on a new lock and on a stale
waiver alike, so this module and the rulebook cannot quietly stop describing the same thing.

`ALIAS_DENIALS` covers those spec-governed concepts. `_FOREIGN_CAPTION_DENIALS` below is a
second, narrower layer for a different defect with the same shape: the harvested `aliases_zh`
lists are not translations of the English ones, so a concept can end up claiming a Chinese
caption that names a DIFFERENT financial concept than the one its English list will answer to.
`_BORROWED_CAPTION_DENIALS` is a third layer for the mirror-image defect on the ENGLISH side: an
`aliases_en` entry that is not this concept's caption at all but another concept's, or a section
or note HEADING. All three tables are filtered by the same `curate_aliases`/`denied_aliases`
pair, so the generator picks each new one up through the import it already has.
"""
from __future__ import annotations

import re

OPER_EXP = "is_pl__deprec_and_impairment_oper_exp"
COS = "is_pl__deprec_and_impairment_cos"
SECUR_CP = "bs_ca__secur_and_other_fincl_assets_cp"
SECUR_LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
# The eighth spec-governed concept and the only one with NO `ALIAS_DENIALS` entry: §2 of its
# specification ADDS note vocabulary (或有负债 / 未决诉讼 / 对外担保 / ...) rather than excluding
# anything, so there is nothing here to filter. Kept exported rather than deleted because it is
# the negative control on both sides of the pair: tests/test_spec_alias_curation.py:104 asserts a
# contingent-liability caption survives curation untouched, and tests/test_spec_conformance.py
# counts it among `SPEC_GOVERNED` — the eight are eight in both files or the two have drifted.
CONTINGENT = "notes__contingent_liabilities"
DUE_FROM_RP = "bs_nca__due_from_related_parties_ltp"
OTHER_RECV_CP = "bs_ca__other_receivables_cp"
OTHER_RECV_LTP = "bs_nca__other_receivables_ltp"
SALES = "is_pl__sales_revenues"

# Depreciation, opening note: "this rule includes only the depreciation and specified
# lease-related amortisation items listed below. Do not include impairment." An asset's own name
# is its carrying amount, never the period's charge, so those are denied too.
# 17 patterns, shared by both depreciation concepts — both of which are currently locked (see the
# module docstring), so this list filters nothing until one is unlocked.
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
# 11 patterns, shared by the CP and LTP twins. Only the CP twin is matchable today: SECUR_LTP is
# locked (see the module docstring), so this list filters nothing for it until the lock is lifted.
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


# ── foreign-caption denials ───────────────────────────────────────────────────
# A DIFFERENT defect from the ones above, and not one any specification section states: the
# harvested Chinese alias list asserts an equivalence the concept's own English list refuses.
#
# AMORT_INTGBL is amortisation of INTANGIBLE assets. Its English aliases say so and nothing
# else — "Amortisation of intangible assets", "Impairment of intangible assets". Its harvested
# `aliases_zh` additionally claimed the whole 折旧 (DEPRECIATION) family: 折旧, 折旧及摊销,
# 使用权资产的折旧 (depreciation of right-of-use assets) and 物业及设备折旧 (depreciation of
# property and equipment). Depreciation is the charge on TANGIBLE assets; it is a different
# concept, which is exactly why the English list never claimed it.
#
# Measured on the shipped output_csv_hk_ontology matcher before this table existed:
#     match("折旧", statement="profit_and_loss")
#         -> is_pl__amort_and_impairment_intgbl, confidence 1.0, EXACT, needs_review=False
#     match("Depreciation", statement="profit_and_loss")
#         -> None, confidence 0.0, UNMATCHED, needs_review=True
# The same four captions behaved the same way on both amortisation concepts. So a Chinese filing
# got a confident wrong answer where an English filing got an honest refusal.
#
# Nothing downstream repairs it. Neither concept is in extraction.llm_focus_keys (config.toml
# ships eight, and these are not among them) and extraction.llm_focus_only = true, so a row the
# deterministic tiers place here is never forwarded to the model — the mis-map ships uncorrected
# with the LLM on. Refusing the alias is the only tier that can act.
#
# The fix is REFUSE-AND-REDIRECT, not re-ranking: 折旧 and its family stay on
# cf_oper_indirect__depreciation, which legitimately owns them (its English list is headed by
# "Depreciation" and it already carries all four Chinese forms), and is deliberately NOT touched
# here. Statement scoping then does the rest — the cash-flow reconciliation still resolves 折旧
# EXACT at 1.0, while the P&L now refuses it and routes to review, which is precisely what
# English "Depreciation" already did on the P&L.
_DEPRECIATION_IS_NOT_AMORTISATION = (
    r"^使用权资产的折旧$", r"^折旧$", r"^折旧及摊销$", r"^物业及设备折旧$",
)

_FOREIGN_CAPTION_DENIALS: dict[str, tuple[str, ...]] = {
    "is_pl__amort_and_impairment_intgbl": _DEPRECIATION_IS_NOT_AMORTISATION,
    "is_pl__amort_and_impairment_intgbl_cos": _DEPRECIATION_IS_NOT_AMORTISATION,
}


# ── borrowed-caption denials ──────────────────────────────────────────────────
# The mirror image of the layer above, on the ENGLISH side, and a THIRD table rather than more
# rows in `_FOREIGN_CAPTION_DENIALS` because that table's own test pins its membership at exactly
# the two amortisation concepts (tests/test_spec_alias_curation.py:151) and because the two say
# different things: that one is "the harvested Chinese list outclaims the English one", this one
# is "an English alias is not this concept's caption at all".
#
# `aliases_en` was harvested the same way `aliases_zh` was, so it picked up captions that name a
# DIFFERENT concept, and captions that are a section or note HEADING rather than a line at all.
# `mapping.OntologyMatcher._prefer_label_owners` already refuses a borrowed alias when some
# concept holds the caption as its own LABEL — which is why "Related Party Transactions" resolves
# correctly with no statement in hand. It cannot help once statement scoping removes that owner
# from the candidate set, and it cannot help at all when no concept owns the caption as a label.
#
# Measured on the shipped `output_csv_hk_ontology.json` matcher, loaded the way a caller that
# intends to MATCH loads it (`load_ontology(..., resolve=True)`; without `resolve` the section
# layer is not folded in and statement scoping does not run), every one of these was confidence
# 1.0, EXACT, needs_review=False — a confident wrong number, not a missing one:
#     match("Profit/Loss before income tax", statement="profit_and_loss")
#         -> is_pl__amort_and_impairment_intgbl        # a PRE-TAX PROFIT filed as amortisation
#     match("Intangbile assets", statement="profit_and_loss")
#         -> is_pl__amort_and_impairment_intgbl        # the same concept's own harvested typo
#     match("Intangible assets", statement="balance_sheet", section="bs_nca")
#         -> bs_nca__land_use_rights                   # priority 81 over the real claimant's 80
#     match("Related Party Transactions", statement="balance_sheet", section="bs_nca")
#         -> bs_nca__due_from_directors                # first of three priority-81 due_from ties
#     match("Retained Profits", statement="profit_and_loss")
#         -> is_retained__cash_div_pref_shares         # first of six priority-10 ties
# The two ties are settled by declaration order, which `binding.order` step 6 forbids in as many
# words and `_exact_tie` cannot catch here: `output_csv_hk_ontology.json` declares
# `confusable_with` on 0 of its 462 concepts (see `mapping._exact_tie`).
#
# None of these concepts is in `extraction.llm_focus_keys` (config.toml ships eight) and
# `extraction.llm_focus_only = true`, so a row the deterministic tiers place here is never
# forwarded to the model and the mis-map ships uncorrected with the LLM on. Refusing the alias is
# the only tier that can act.
#
# REFUSE-ONLY for three of the four families — the caption goes to UNMATCHED/review, which is the
# honest answer for a figure the rulebook has no line for. "Intangible assets" is a REDIRECT: with
# land_use_rights' borrowed claim gone the caption reaches `bs_nca__other_intangible_assets`
# (priority 80), the leaf actually named. NOT `bs_nca__net_intangibles`, which is the rollup PARENT
# of both (tests/test_output_csv_ontology.py:191) and holds no English claim on the caption anyway.
_PRE_TAX_PROFIT_IS_NOT_AMORTISATION = (
    r"^Profit/Loss before income tax$",
    r"^Intangbile assets$",
)

# A note or section HEADING names the disclosure, not a line in it. "Related Party Transactions"
# is `notes__related_party_transactions`' own label; "Retained Profits" is
# `bs_equity__retained_profits`' own label AND the name of the `is_retained` movement section, so
# neither belongs to a movement line inside it. Both label owners are deliberately NOT touched.
_HEADING_IS_NOT_A_LINE_ITEM = (r"^Related Party Transactions$",)
_SECTION_NAME_IS_NOT_A_MOVEMENT = (r"^Retained Profits$",)

_BORROWED_CAPTION_DENIALS: dict[str, tuple[str, ...]] = {
    "is_pl__amort_and_impairment_intgbl": _PRE_TAX_PROFIT_IS_NOT_AMORTISATION,
    # Land use rights ARE an intangible, but they are one named intangible; the bare caption is
    # the sibling leaf's. Its own "Land use rights" vocabulary is untouched.
    "bs_nca__land_use_rights": (r"^Intangible Assets$",),
    "bs_nca__due_from_directors": _HEADING_IS_NOT_A_LINE_ITEM,
    "bs_nca__due_from_subsidiaries": _HEADING_IS_NOT_A_LINE_ITEM,
    "bs_nca__due_from_mi": _HEADING_IS_NOT_A_LINE_ITEM,
    # ALIAS_DENIALS already denies the same heading to bs_nca__due_from_related_parties_ltp under
    # §3.4 — these three are the same caption on the three concepts no specification section
    # governs, which is why the earlier fix left them behind.
    "is_retained__cash_div_pref_shares": _SECTION_NAME_IS_NOT_A_MOVEMENT,
    "is_retained__proposed_cash_dividends": _SECTION_NAME_IS_NOT_A_MOVEMENT,
    "is_retained__cash_div_common_shares": _SECTION_NAME_IS_NOT_A_MOVEMENT,
    "is_retained__stock_dividends_nc": _SECTION_NAME_IS_NOT_A_MOVEMENT,
    "is_retained__transfer_to_reserves": _SECTION_NAME_IS_NOT_A_MOVEMENT,
    "is_retained__prior_period_adjustments": _SECTION_NAME_IS_NOT_A_MOVEMENT,
    # Six of the seven tie above; this one carries the same borrowed alias but declares
    # `alias_matching: disabled`, so `OntologyMatcher.__init__` indexes its aliases nowhere and
    # the sweep that populates it reads face rows, never captions. Denied anyway: the alias is
    # equally wrong on the residual, and leaving it would resurrect the tie the day the lock is
    # lifted. Its OWN residual vocabulary (`expected_components`) is a separate field and stands.
    "is_retained__other_adj_to_retained_profits": _SECTION_NAME_IS_NOT_A_MOVEMENT,
}


def _denied(canonical_key: str, alias: str) -> bool:
    patterns = (*ALIAS_DENIALS.get(canonical_key, ()),
                *_FOREIGN_CAPTION_DENIALS.get(canonical_key, ()),
                *_BORROWED_CAPTION_DENIALS.get(canonical_key, ()))
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
