"""services.spec_alias_curation — the denial rules the ontology generator applies.

tests/test_spec_conformance.py checks the generated artefact; this checks the rule set itself, so
a denial that silently stops matching (a reworded alias, a regex typo) is caught here rather than
only when a rebuild happens to reintroduce the alias it was meant to catch.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.spec_alias_curation import (
    ALIAS_DENIALS, CONTINGENT, COS, DUE_FROM_RP, OPER_EXP, OTHER_RECV_CP, OTHER_RECV_LTP, SALES,
    SECUR_CP, SECUR_LTP, curate_aliases, denied_aliases,
)

ONTOLOGY = Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_ontology.json"


@pytest.mark.parametrize("key,alias", [
    (OPER_EXP, "Depreciation, impairment and amortisation"),
    (OPER_EXP, "摊销"),
    (OPER_EXP, "无形资产摊销"),
    (OPER_EXP, "投资物业"),
    (COS, "Impairment of property,plant and equipments/fixed assets"),
    (COS, "Property,plant and equipments / fixed assets"),
    (SECUR_CP, "Derivative"),
    (SECUR_CP, "Other receivables"),
    (SECUR_CP, "fair value measurement/hierarchy"),
    (SECUR_CP, "衍生金融工具"),
    (SECUR_CP, "按金、预付款项及其他应收款"),
    (SECUR_LTP, "Investment in related parties/associates/JV"),
    (SECUR_LTP, "Marketable securities"),
    (SECUR_LTP, "货币市场工具"),
    (DUE_FROM_RP, "Entrustment loan from shareholders"),
    (DUE_FROM_RP, "Entrusted loan receivables from shareholders"),
    (DUE_FROM_RP, "委托贷款"),
    (DUE_FROM_RP, "Find 2: Sum of due from related parties included in Note"),
    (OTHER_RECV_CP, "合同资产"),
    # "Other receivables" is the receivables that are NOT trade receivables, so a trade-receivable
    # caption cannot belong to either twin. The non-current one claimed "Trade receivables" at
    # match_priority 81 — above the genuine current trade concepts at 80 — and a plain
    # "Trade receivables" line was filed as a NON-CURRENT other receivable at confidence 1.0,
    # taking the figure out of current assets and breaking the printed total.
    (OTHER_RECV_LTP, "Trade receivables"),
    (OTHER_RECV_LTP, "应收账款"),
    (OTHER_RECV_LTP, "贸易及其他应收款"),
    (OTHER_RECV_LTP, "贸易应收款项及票据"),
    (OTHER_RECV_CP, "Trade receivables"),
    (OTHER_RECV_CP, "贸易及其他应收款"),
    (SALES, "营业收入"),
    (SALES, "营业总收入"),
    (SALES, "销售收入"),
])
def test_a_forbidden_alias_is_denied(key, alias):
    assert curate_aliases(key, [alias]) == []
    assert denied_aliases(key, [alias]) == [alias]


@pytest.mark.parametrize("key,alias", [
    # The concept's own field name survives, impairment in the label notwithstanding.
    (OPER_EXP, "Deprec & Impairment(Oper Exp)"),
    (COS, "Deprec & Impairment(COS)"),
    (OPER_EXP, "Depreciation of property, plant and equipment"),
    (OPER_EXP, "折旧"),
    (OPER_EXP, "固定资产折旧"),          # the CHARGE, not the asset name denied above
    # A structured deposit WITH embedded derivatives is a §4.1 in-scope heading, even though
    # "derivatives" appears in it — the denial is anchored to the bare component name.
    (SECUR_CP, "Structured deposits with embedded derivatives"),
    (SECUR_CP, "Money market instruments"),   # CP keeps what §6.1 denies to LTP only
    (SECUR_LTP, "Financial assets at fair value through profit or loss"),
    (DUE_FROM_RP, "Due from related parties"),
    (DUE_FROM_RP, "应收关联方款项"),
    (OTHER_RECV_CP, "其他应收款项"),
    (OTHER_RECV_LTP, "Other Receivables(LTP)"),
    (OTHER_RECV_LTP, "其他应收款项"),
    # Its own vocabulary survives: a NON-trade receivable is exactly what this concept is for.
    (OTHER_RECV_LTP, "Non-trade receivables"),
    (OTHER_RECV_LTP, "Rental deposits"),
    (SALES, "主营业务收入"),
    (SALES, "主营业务"),
    (SALES, "Turnover"),                 # §4's ban is on the Chinese pair, not English captions
    (SALES, "Revenue"),
    (CONTINGENT, "或有负债"),            # nothing is denied to this concept
])
def test_a_permitted_alias_survives(key, alias):
    assert curate_aliases(key, [alias]) == [alias]


def test_curation_preserves_the_order_of_what_it_keeps():
    aliases = ["Deprec & Impairment(Oper Exp)", "摊销", "折旧", "无形资产摊销", "固定资产折旧"]
    assert curate_aliases(OPER_EXP, aliases) == [
        "Deprec & Impairment(Oper Exp)", "折旧", "固定资产折旧"]


def test_a_concept_with_no_denials_is_returned_untouched():
    aliases = ["Total Assets", "资产总计"]
    assert curate_aliases("bs_ca__total_assets", aliases) == aliases


def test_the_committed_ontology_already_satisfies_every_denial():
    """The artefact and the rule set agree, so a rebuild applying these denials is a no-op here.

    This is what makes the two safety nets one: if the generated file ever drifts from the rules
    the generator applies, this fails whichever side moved.
    """
    raw = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
    offending: dict[str, list[str]] = {}
    for mapping in raw["mappings"]:
        key = mapping["canonical_key"]
        if key not in ALIAS_DENIALS:
            continue
        present = list(mapping.get("aliases") or [])
        for per_locale in (mapping.get("aliases_i18n") or {}).values():
            present += per_locale
        removed = denied_aliases(key, present)
        if removed:
            offending[key] = removed
    assert offending == {}
