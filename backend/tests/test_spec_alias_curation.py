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
    SECUR_CP, SECUR_LTP, _FOREIGN_CAPTION_DENIALS, curate_aliases, denied_aliases,
)

ONTOLOGY = Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_ontology.json"

AMORT_INTGBL = "is_pl__amort_and_impairment_intgbl"
AMORT_INTGBL_COS = "is_pl__amort_and_impairment_intgbl_cos"
CF_DEPRECIATION = "cf_oper_indirect__depreciation"


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
    # The foreign-caption layer: 折旧 is DEPRECIATION, a charge on tangible assets, and the two
    # intangible-AMORTISATION concepts' own English lists never claim it. Their harvested
    # `aliases_zh` did, so match("折旧", statement="profit_and_loss") returned
    # is_pl__amort_and_impairment_intgbl EXACT at confidence 1.0 with needs_review=False while
    # English "Depreciation" on the same statement returned UNMATCHED with review — a confident
    # wrong answer for a Chinese filing where an English one got an honest refusal.
    (AMORT_INTGBL, "折旧"),
    (AMORT_INTGBL, "折旧及摊销"),
    (AMORT_INTGBL, "使用权资产的折旧"),
    (AMORT_INTGBL, "物业及设备折旧"),
    (AMORT_INTGBL_COS, "折旧"),
    (AMORT_INTGBL_COS, "折旧及摊销"),
    (AMORT_INTGBL_COS, "使用权资产的折旧"),
    (AMORT_INTGBL_COS, "物业及设备折旧"),
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
    # The amortisation concepts keep their OWN vocabulary — the denial is anchored to the bare
    # depreciation captions, not to any string containing 折旧 or 摊销.
    (AMORT_INTGBL, "无形资产摊销"),
    (AMORT_INTGBL, "Amortisation of intangible assets"),
    (AMORT_INTGBL, "Impairment of intangible assets"),
    (AMORT_INTGBL_COS, "无形资产摊销"),
    (AMORT_INTGBL_COS, "无形资产"),
    (AMORT_INTGBL_COS, "其他无形资产"),
    # REFUSE-AND-REDIRECT: the whole 折旧 family stays where it belongs. Nothing is denied to the
    # cash-flow depreciation concept, which legitimately owns these captions.
    (CF_DEPRECIATION, "折旧"),
    (CF_DEPRECIATION, "折旧及摊销"),
    (CF_DEPRECIATION, "使用权资产的折旧"),
    (CF_DEPRECIATION, "物业及设备折旧"),
    (CF_DEPRECIATION, "Depreciation"),
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


def test_the_foreign_caption_layer_filters_in_place_like_the_spec_layer():
    """One filter over both tables, so the generator's alias ranking survives either denial."""
    aliases = ["Amort & Impairment(Intgbl)(COS)", "使用权资产的折旧", "其他无形资产", "折旧",
               "折旧及摊销", "无形资产", "无形资产摊销", "物业及设备折旧"]
    assert curate_aliases(AMORT_INTGBL_COS, aliases) == [
        "Amort & Impairment(Intgbl)(COS)", "其他无形资产", "无形资产", "无形资产摊销"]
    assert denied_aliases(AMORT_INTGBL_COS, aliases) == [
        "使用权资产的折旧", "折旧", "折旧及摊销", "物业及设备折旧"]


def test_the_foreign_caption_layer_is_disjoint_from_the_spec_layer():
    """Neither amortisation concept is one of ALIAS_DENIALS' nine, which is why it needed its own
    table: the generator's single filter reaches both, but the spec-derived rules do not.
    """
    assert set(_FOREIGN_CAPTION_DENIALS) & set(ALIAS_DENIALS) == set()
    assert set(_FOREIGN_CAPTION_DENIALS) == {AMORT_INTGBL, AMORT_INTGBL_COS}
    assert CF_DEPRECIATION not in _FOREIGN_CAPTION_DENIALS


def test_the_committed_ontology_already_satisfies_every_denial():
    """The artefact and the rule set agree, so a rebuild applying these denials is a no-op here.

    This is what makes the two safety nets one: if the generated file ever drifts from the rules
    the generator applies, this fails whichever side moved.
    """
    raw = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
    governed = {**ALIAS_DENIALS, **_FOREIGN_CAPTION_DENIALS}
    offending: dict[str, list[str]] = {}
    for mapping in raw["mappings"]:
        key = mapping["canonical_key"]
        if key not in governed:
            continue
        present = list(mapping.get("aliases") or [])
        for per_locale in (mapping.get("aliases_i18n") or {}).values():
            present += per_locale
        removed = denied_aliases(key, present)
        if removed:
            offending[key] = removed
    assert offending == {}


# ── the enrichment that copied a denied caption's Chinese across ──────────────────────────────

def _enrichment():
    """`scripts/enrich_output_csv_primary_aliases.py`, loaded by path — it is a script, not a
    package module, so there is nothing to import by name."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts/enrich_output_csv_primary_aliases.py"
    spec = importlib.util.spec_from_file_location("_enrich_primary_aliases", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_enrichment_refuses_to_copy_a_denied_chinese_spelling():
    """HOW THE DENIAL WAS OUTFLANKED, pinned so it cannot happen again.

    The transfer is keyed on the ENGLISH wording: for every alias a concept carries, whatever
    Chinese the reference files under the same English is added. `bs_equity__retained_earnings`
    files 未分配利润 and 保留溢利 against "Retained profits", so while the seven `is_retained`
    movement lines carried that borrowed English caption they were handed both — and
    `_SECTION_NAME_IS_NOT_A_MOVEMENT` denying the English one could not take the Chinese back.

    Curating the transfer closes it in both directions: nothing denied is copied IN, and anything
    an earlier run copied is removed the next time this runs.
    """
    enrich = _enrichment()
    definition = {"mappings": [{
        "canonical_key": "is_retained__cash_div_common_shares",
        "label": "Cash Div Common Shares(-)",
        "aliases": ["Retained Profits"],
        "aliases_i18n": {},
    }]}
    reference = {"mappings": [{
        "canonical_key": "bs_equity__retained_earnings", "label": "Retained profits",
        "aliases": [], "aliases_i18n": {"zh": ["未分配利润", "保留溢利"]},
    }]}

    enrich.enrich(definition, reference)

    assert not (definition["mappings"][0]["aliases_i18n"].get("zh")), (
        "a denied Chinese spelling was copied onto a movement line by the English transfer")


def test_the_enrichment_still_copies_a_chinese_spelling_nobody_denied():
    """The control. Curating the transfer must not turn the whole script into a no-op — its job
    is to give a concept the other script's wording, and only the denied pairs are refused."""
    enrich = _enrichment()
    definition = {"mappings": [{
        "canonical_key": "bs_ca__cash_in_hand_and_at_banks", "label": "Cash at bank and on hand",
        "aliases": ["Cash at bank and on hand"], "aliases_i18n": {},
    }]}
    reference = {"mappings": [{
        "canonical_key": "bs_ca__cash", "label": "Cash at bank and on hand",
        "aliases": [], "aliases_i18n": {"zh": ["银行存款及现金"]},
    }]}

    enrich.enrich(definition, reference)

    assert "银行存款及现金" in definition["mappings"][0]["aliases_i18n"]["zh"]
