"""The eight spec-governed concepts must not carry alias vocabulary their own specification
excludes.

scripts/build_output_csv_template.py enriches each concept's aliases by scraping quoted phrases
out of the Extraction Logic workbook's free-text formula column (`_quoted(logic_text)`). That
column describes the arithmetic, so the phrases it quotes include the components a formula
SUBTRACTS and the notes it merely reads — registering those as aliases points the caption mapper
at exactly the concepts the spec says to deduct or exclude. These tests pin the vocabulary rules
each docs/*_Extraction_Logic*.md states in words, so a regenerated ontology that reintroduces the
contamination fails here instead of silently producing a wrong figure.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.mapping import normalize_label

ONTOLOGY = Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_ontology.json"

OPER_EXP = "is_pl__deprec_and_impairment_oper_exp"
COS = "is_pl__deprec_and_impairment_cos"
SECUR_CP = "bs_ca__secur_and_other_fincl_assets_cp"
SECUR_LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
CONTINGENT = "notes__contingent_liabilities"
DUE_FROM_RP = "bs_nca__due_from_related_parties_ltp"
OTHER_RECV_CP = "bs_ca__other_receivables_cp"
SALES = "is_pl__sales_revenues"

SPEC_GOVERNED = (OPER_EXP, COS, SECUR_CP, SECUR_LTP, CONTINGENT, DUE_FROM_RP, OTHER_RECV_CP, SALES)


@pytest.fixture(scope="module")
def mappings() -> dict[str, dict]:
    raw = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
    return {m["canonical_key"]: m for m in raw["mappings"]}


def _aliases(mapping: dict) -> list[str]:
    """Every string the caption mapper will match against, from all three containers."""
    out = list(mapping.get("aliases") or [])
    for per_locale in (mapping.get("aliases_i18n") or {}).values():
        out += per_locale
    return out


def _normalized(mapping: dict) -> set[str]:
    return {normalize_label(a) for a in _aliases(mapping)}


def _assert_absent(mapping: dict, forbidden: list[str], why: str) -> None:
    present = sorted(_normalized(mapping) & {normalize_label(f) for f in forbidden})
    assert present == [], f"{mapping['canonical_key']} carries {present}: {why}"


def test_every_spec_governed_concept_exists(mappings):
    missing = [k for k in SPEC_GOVERNED if k not in mappings]
    assert missing == []


# ── depreciation: opening note — depreciation and the listed lease amortisation only ──────────
@pytest.mark.parametrize("key", [OPER_EXP, COS])
def test_depreciation_excludes_impairment_and_unrelated_amortisation(mappings, key):
    _assert_absent(
        mappings[key],
        ["Impairment of property,plant and equipments/fixed assets",
         "Depreciation, impairment and amortisation", "摊销", "无形资产摊销",
         "折旧及摊销", "折旧及摊销费用"],
        "the spec includes only depreciation and the listed prepaid-lease amortisation, and "
        "excludes impairment unless a separate rule requires it")


@pytest.mark.parametrize("key", [OPER_EXP, COS])
def test_depreciation_is_not_aliased_to_a_bare_asset_name(mappings, key):
    # An asset's own name is the balance-sheet carrying amount, never the period's charge.
    _assert_absent(
        mappings[key],
        ["Investment Property", "Construction in progress", "Prepaid land lease payment",
         "Property,plant and equipments / fixed assets", "固定资产", "在建工程", "在建资产",
         "投资性房地产", "投资物业", "物业、厂房及设备", "物业及设备"],
        "a bare asset name is the carrying amount, not the depreciation charge")


def test_cos_depreciation_stays_extractable(mappings):
    # COS_P1 = cos_depreciation is read from the Cost of Sales note and reported by the spec as
    # DIRECTLY_EXTRACTED, so this concept must not be computed-only.
    assert mappings[COS].get("extraction_mode") == "extract_or_derive"


# ── securities §4.2/§4.3: deductions and the source note are not the concept ──────────────────
@pytest.mark.parametrize("key", [SECUR_CP, SECUR_LTP])
def test_securities_do_not_alias_their_own_deduction_components(mappings, key):
    _assert_absent(
        mappings[key],
        ["Derivative", "Other receivables", "Investment in related parties/associates/JV",
         "衍生金融工具", "其他应收款项"],
        "§4.2 subtracts these from the note total, so a caption naming one must never bind here")


@pytest.mark.parametrize("key", [SECUR_CP, SECUR_LTP])
def test_securities_do_not_alias_the_fair_value_hierarchy_note(mappings, key):
    _assert_absent(mappings[key], ["fair value measurement/hierarchy"],
                   "§4.3 makes it the source note for the Level 3 total, not the concept")


@pytest.mark.parametrize("key", [SECUR_CP, SECUR_LTP])
def test_securities_do_not_alias_trade_receivables_or_prepayments(mappings, key):
    _assert_absent(
        mappings[key],
        ["按金、预付款项及其他应收款", "按金及预付款项", "贸易及其他应收款",
         "贸易应收款、预付款项及其他应收款", "预付款项、其他应收款及其他资产",
         "预付款项、按金及其他应收款项", "预付款项及其他应收款项"],
        "these are other-receivable captions and belong to bs_ca__other_receivables_cp")


def test_long_term_securities_exclude_the_cp_only_headings(mappings):
    # §6.1: money-market and marketable-securities headings are CP-only unless the filing
    # itself classifies them as non-current — which the note's own face citation decides.
    _assert_absent(
        mappings[SECUR_LTP],
        ["Money market instruments", "Marketable securities", "short term money market deposits",
         "货币市场工具", "有价证券", "短期货币市场存款"],
        "§6.1 restricts these three headings to the current-portion field")


def test_current_securities_keep_the_cp_only_headings(mappings):
    assert {"money market instruments", "marketable securities"} <= _normalized(mappings[SECUR_CP])


# ── related parties §3.4: entrusted loans are excluded outright ───────────────────────────────
def test_due_from_related_parties_excludes_entrusted_loans(mappings):
    aliases = _normalized(mappings[DUE_FROM_RP])
    offending = sorted(a for a in aliases if "entrust" in a or "委托" in a)
    assert offending == [], (
        f"{DUE_FROM_RP} carries entrusted-loan aliases {offending}: §3.4 excludes entrusted "
        "loans from every related-party calculation")


def test_due_from_related_parties_declares_the_entrusted_loan_exclusion(mappings):
    hints = {normalize_label(h) for h in (mappings[DUE_FROM_RP].get("exclude_hints") or [])}
    assert {"委托贷款", "委托借款"} <= hints


def test_no_spec_heading_leaked_in_as_a_printed_caption(mappings):
    # "Find 2: Sum of due from related parties included in Note" is a heading out of the
    # specification's own formula summary, not anything a filing prints.
    for key in SPEC_GOVERNED:
        leaked = sorted(a for a in _aliases(mappings[key])
                        if a.lower().startswith(("find 1", "find 2", "find 3", "sum of")))
        assert leaked == [], f"{key} carries specification prose as an alias: {leaked}"


# ── sales §1/§4: 主营业务收入 only; total 营业收入 is explicitly not a fallback ─────────────
def test_sales_revenues_does_not_alias_total_operating_revenue(mappings):
    _assert_absent(
        mappings[SALES],
        ["营业收入", "营业总收入", "营业额", "销售收入", "收益", "客户合约收益"],
        "§4 forbids total 营业收入 as a fallback when 主营业务收入 is not separately disclosed")


def test_sales_revenues_keeps_its_principal_operations_vocabulary(mappings):
    assert {"主营业务收入", "主营业务"} <= _normalized(mappings[SALES])


def test_sales_revenues_keeps_the_english_hkex_captions(mappings):
    # The prohibition above is about the Chinese pair. On an English HKEX filing "Turnover" IS
    # the revenue line, and stripping it would over-apply a PRC rule.
    assert {"turnover", "revenue"} <= _normalized(mappings[SALES])


# ── contingent liabilities: the note vocabulary of §2, and no longer a stub ───────────────────
def test_contingent_liabilities_carries_the_specified_note_headings(mappings):
    required = ["或有负债", "或有事项", "关联方担保", "未决诉讼", "未决仲裁", "对外担保",
                "担保事项", "承诺及或有事项"]
    have = _normalized(mappings[CONTINGENT])
    missing = sorted(h for h in required if normalize_label(h) not in have)
    assert missing == [], f"{CONTINGENT} is missing §2 note headings: {missing}"


def test_contingent_liabilities_rejects_non_exposure_amount_labels(mappings):
    hints = {normalize_label(h) for h in (mappings[CONTINGENT].get("exclude_hints") or [])}
    assert {"授信额度", "合同总额", "已确认预计负债"} <= hints


def test_contingent_liabilities_is_fully_declared(mappings):
    m = mappings[CONTINGENT]
    for field in ("extraction_mode", "value_scope", "match_priority", "section_disambiguation",
                  "template_note"):
        assert m.get(field), f"{CONTINGENT}.{field} is unset; every sibling concept declares it"


# ── the modes of the four note-assembled pairs agree within each pair ─────────────────────────
@pytest.mark.parametrize("key", [SECUR_CP, OTHER_RECV_CP])
def test_a_service_computed_field_is_not_declared_plain_extract(mappings, key):
    # Its value is computed by a stage that overwrites whatever the caption mapper left. Declaring
    # it a plain `extract` lets an alias-matched caption be consumed by an exclusive_leaf whose
    # value is then discarded, stranding the concept the caption really named.
    assert mappings[key].get("extraction_mode") == "extract_or_derive"


def test_no_spec_governed_concept_declares_an_alias_its_own_hints_delete(mappings):
    import re
    for key in SPEC_GOVERNED:
        m = mappings[key]
        for hint in m.get("exclude_hints") or []:
            for alias in _aliases(m):
                assert not re.search(hint, alias, re.IGNORECASE), (
                    f"{key}: exclude_hint {hint!r} deletes its own alias {alias!r}")
