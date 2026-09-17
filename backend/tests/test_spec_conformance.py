"""The eight spec-governed concepts must not carry alias vocabulary their own specification
excludes.

scripts/build_output_csv_template.py enriches each concept's aliases by scraping quoted phrases
out of the Extraction Logic workbook's free-text formula column (`_quoted(logic_text)`). That
column describes the arithmetic, so the phrases it quotes include the components a formula
SUBTRACTS and the notes it merely reads — registering those as aliases points the caption mapper
at exactly the concepts the spec says to deduct or exclude. These tests pin the vocabulary rules
each docs/*_Extraction_Logic*.md states in words, so a regeneration that reintroduces the
contamination fails here instead of silently producing a wrong figure.

WHICH FILE GOVERNS, now that there is one configuration engine. A run maps against a
``line_item_versions`` row seeded from ``output_csv_hk_line_items.json`` (`LINE_ITEMS` below);
``output_csv_hk_ontology.json`` (`ONTOLOGY`) is the GENERATOR INPUT that set is projected from
(``scripts/build_line_items.py``) and is no longer a stored, selectable or user-visible rulebook —
``extraction.mapping_engine`` is deleted, so nothing chooses between the two any more. The curation
assertions that decide what a run may match are therefore made against the configuration; the
remaining `mappings` assertions guard the source those definitions are generated FROM, which is
where a contaminated alias would come back in.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.mapping import normalize_label

ONTOLOGY = Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_ontology.json"
LINE_ITEMS = (Path(__file__).resolve().parents[1]
              / "app/sample/templates/output_csv_hk_line_items.json")

OPER_EXP = "is_pl__deprec_and_impairment_oper_exp"
COS = "is_pl__deprec_and_impairment_cos"
SECUR_CP = "bs_ca__secur_and_other_fincl_assets_cp"
SECUR_LTP = "bs_nca__secur_and_other_fincl_assets_ltp"
CONTINGENT = "notes__contingent_liabilities"
DUE_FROM_RP = "bs_nca__due_from_related_parties_ltp"
OTHER_RECV_CP = "bs_ca__other_receivables_cp"
SALES = "is_pl__sales_revenues"

SPEC_GOVERNED = (OPER_EXP, COS, SECUR_CP, SECUR_LTP, CONTINGENT, DUE_FROM_RP, OTHER_RECV_CP, SALES)

AMORT_INTGBL = "is_pl__amort_and_impairment_intgbl"
AMORT_INTGBL_COS = "is_pl__amort_and_impairment_intgbl_cos"
CF_DEPRECIATION = "cf_oper_indirect__depreciation"

LAND_USE_RIGHTS = "bs_nca__land_use_rights"
OTHER_INTANGIBLES = "bs_nca__other_intangible_assets"
NET_INTANGIBLES = "bs_nca__net_intangibles"
DUE_FROM_DIRECTORS = "bs_nca__due_from_directors"
DUE_FROM_SUBSIDIARIES = "bs_nca__due_from_subsidiaries"
DUE_FROM_MI = "bs_nca__due_from_mi"
RP_NOTE = "notes__related_party_transactions"
EQUITY_RETAINED = "bs_equity__retained_profits"
RETAINED_MOVEMENTS = (
    "is_retained__cash_div_pref_shares",
    "is_retained__proposed_cash_dividends",
    "is_retained__cash_div_common_shares",
    "is_retained__stock_dividends_nc",
    "is_retained__transfer_to_reserves",
    "is_retained__prior_period_adjustments",
    "is_retained__other_adj_to_retained_profits",
)


@pytest.fixture(scope="module")
def mappings() -> dict[str, dict]:
    raw = json.loads(ONTOLOGY.read_text(encoding="utf-8"))
    return {m["canonical_key"]: m for m in raw["mappings"]}


@pytest.fixture(scope="module")
def line_items() -> dict[str, dict]:
    raw = json.loads(LINE_ITEMS.read_text(encoding="utf-8"))
    return {i["key"]: i for i in raw["items"]}


def _aliases(mapping: dict) -> list[str]:
    """Every alias string the concept DECLARES: `aliases` plus each `aliases_i18n` locale list.

    Declared, not indexed. It used to say "every string the caption mapper will match against",
    which is false for a locked concept: `OntologyMatcher.__init__` puts every concept carrying
    `alias_matching: disabled` into `_locked`/`_unmatchable` and indexes its aliases nowhere
    (mapping.py:1454-1469), so on four of the eight spec-governed concepts the mapper matches
    against NONE of these 95 strings. See `test_every_curated_concept_is_matchable` for the pin
    that keeps that set of exceptions declared rather than discovered.

    The list is also not a set: `aliases_i18n["en"]` repeats `aliases` verbatim in the generated
    artefact, so a caption present in both is returned twice. Harmless here — every caller
    funnels through `_normalized`, which is a set — but it is why a length taken off this helper
    over-counts.
    """
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


# ── the curated concepts must be reachable by matching at all ─────────────────────────────────
# Every test below asserts something about an alias LIST. None of them asserts that the concept
# holding the list is reachable, and four of the eight spec-governed concepts are not: they ship
# `alias_matching: disabled` in output_csv_hk_ontology.json, so `OntologyMatcher.__init__` puts
# them in `_locked`/`_unmatchable` and indexes their aliases nowhere. Measured on the shipped
# file: 21 + 17 + 28 + 29 = 95 declared alias strings across the four (`aliases` + the `zh`
# locale; the `en` locale duplicates `aliases`) that no tier reads, and the two load-bearing
# depreciation `regex_hints` — "^Depreciation\s+of\s+Investment\s+Property..." and
# "^Depreciation\s+of\s+property\s*,\s*plant\s+and\s+equipment..." — never run either, because
# `_rule_claim` skips `_unmatchable` at mapping.py:1720 BEFORE it evaluates hints at :1727.
#
# So a denial list on a locked concept filters a list nothing reads. That is not a reason to drop
# the denial (the lock can be lifted, and then the contamination is back), but it IS a reason to
# force the exception to be DECLARED: `spec_alias_curation` and the rulebook are two files that
# describe one thing, and nothing here compared them — a grep of this module for `alias_matching`
# returned nothing before this test.
#
# MATCHABLE is the negation of `mapping.OntologyMatcher._unmatchable`, which is
# `_locked | _computed_only` — BOTH halves, not just the lock. The audit that asked for this test
# named only `alias_matching != "disabled"`; asserting that alone would let a future unlock of
# `is_pl__deprec_and_impairment_oper_exp` or `bs_nca__secur_and_other_fincl_assets_ltp` delete an
# allowlist entry while the concept stayed unreachable through `extraction_mode: derive`.
#
# WHAT `derive` MEANS NOW, and why every entry below had to be restated. It used to mean "a service
# assembles this figure out of note datasets", so a locked concept still had a producer: the lock
# closed the caption route and the service filled the cell anyway. The derivation services are
# DELETED — deprec_impairment (~208 hand-enumerated note titles, row captions and formula
# variants), secur_fincl_assets (~91), related_party_receivables (~73), sales_revenues (~18) and
# contingent_liabilities' number half (~172; its narrative disclosure survives) — so `derive` names
# no producer at all and is a DEAD END rather than a second route behind the lock. For all four
# concepts below the consequence is the same: the figure must now come from configuration, nothing
# fills the cell in the meantime, and the lock is the only thing between the concept and any figure.
# That is the hand-off to the configuration work; these four are exactly the concepts it has to
# reach, and unlocking them is a separate measured change (it regresses 8 correct mappings today),
# not something to do from this file.
_UNMATCHABLE_BY_DESIGN: dict[str, str] = {
    # `derive` AND locked, and READ THE SECOND LOCK DIFFERENTLY NOW. `extraction_mode: derive` used
    # to mean "a service assembles this instead", so it was a second route to a figure sitting
    # behind the lock. The derivation services are DELETED, so it is a DEAD END: nothing derives
    # this concept any more and the mode names no producer. The figure must come from configuration
    # — a caption binding — and the lock is what currently stands between the concept and any
    # figure at all, so the cell is blank until both are addressed together.
    # Unlocking `alias_matching` here before statement/section is threaded into
    # `mapping._computed_claim` regresses 8 correct mappings to None (7 cash-flow
    # "Depreciation of ..." rows and 1 balance-sheet CIP row), so the lock stays load-bearing until
    # that lands — which is why this entry is a declared waiver and not a fix.
    OPER_EXP: "alias_matching=disabled and extraction_mode=derive, but nothing derives it now — "
              "the mode is a dead end and the lock is the only thing between this concept and any "
              "figure; see mapping.py:1482-1491",
    # Locked only, and `extract_or_derive`. The `derive` half named a service that is gone, and the
    # spec reports COS_P1 as DIRECTLY_EXTRACTED (test_cos_depreciation_stays_extractable), so the
    # lock is not merely the first thing an unlock reaches — it is the ONLY thing keeping this
    # concept from a figure, since there is no computed path behind it to fall back on.
    COS: "alias_matching=disabled; extraction_mode is extract_or_derive and no service derives it "
         "any more, so the lock is the only thing between the concept and any figure at all — it "
         "keeps its 17 aliases and its PPE depreciation regex_hint out of the index",
    # Same pairing as OPER_EXP: `derive` plus the lock, the `derive` half equally a dead end now,
    # and the same 8-mapping regression on an unlock.
    SECUR_LTP: "alias_matching=disabled and extraction_mode=derive, but the service that derived "
               "it is deleted — the mode is a dead end and the lock is the only thing between this "
               "concept and any figure; see mapping.py:1482-1491",
    # Was: "its value is assembled by a service stage". It is not — that stage is deleted and no
    # stage assembles it, so `extract_or_derive` leaves nothing but the caption route, which the
    # lock closes.
    OTHER_RECV_CP: "alias_matching=disabled; no service assembles its value any more and the mode "
                   "is extract_or_derive, so the lock is the only thing between the concept and "
                   "any figure, keeping its 29 aliases unread",
    # Permanent, and the one entry here that is not a defect: this is a section residual bucket,
    # populated by the sweep off the template rather than by any caption. `spec_alias_curation`
    # denies it a borrowed alias anyway — see the comment on `_BORROWED_CAPTION_DENIALS`.
    "is_retained__other_adj_to_retained_profits":
        "a locked residual bucket by design — the sweep reads face rows, never captions",
}


def _curated_keys() -> set[str]:
    """Every canonical_key this module or `spec_alias_curation` makes an assertion about.

    The three denial tables plus the two required-vocabulary tuples in this file plus the
    redirect targets: a REFUSE-AND-REDIRECT fix is only a fix if the concept the caption is
    redirected TO can still be matched, so those belong in the same pin.
    """
    from app.services.spec_alias_curation import (
        ALIAS_DENIALS, _BORROWED_CAPTION_DENIALS, _FOREIGN_CAPTION_DENIALS,
    )
    return (set(ALIAS_DENIALS) | set(_FOREIGN_CAPTION_DENIALS) | set(_BORROWED_CAPTION_DENIALS)
            | set(SPEC_GOVERNED) | set(RETAINED_MOVEMENTS)
            | {CF_DEPRECIATION, OTHER_INTANGIBLES, RP_NOTE, EQUITY_RETAINED})


def test_every_curated_concept_exists_in_the_generated_rulebook(mappings):
    # A denial keyed on a concept the generated file does not declare filters nothing at all, and
    # `_curated_keys` would silently shrink rather than fail.
    missing = sorted(k for k in _curated_keys() if k not in mappings)
    assert missing == []


def test_every_curated_concept_is_matchable(mappings):
    """A curation rule on an unmatchable concept curates a list no tier reads — declare it here.

    `alias_matching: disabled` is not visible from any alias assertion in this file, so the module
    and the rulebook could drift apart in either direction without a test noticing. This makes the
    drift explicit in both: a new lock in a regenerated ontology fails here, and an unlock leaves a
    stale allowlist entry that the companion assertion below fails on.
    """
    unmatchable = {}
    for key in sorted(_curated_keys()):
        m = mappings[key]
        reasons = []
        # Both halves of `OntologyMatcher._unmatchable` (mapping.py:1454-1469).
        if m.get("alias_matching") == "disabled":
            reasons.append("alias_matching=disabled")
        if m.get("extraction_mode") == "derive":
            reasons.append("extraction_mode=derive")
        if reasons:
            unmatchable[key] = ", ".join(reasons)

    undeclared = {k: v for k, v in unmatchable.items() if k not in _UNMATCHABLE_BY_DESIGN}
    assert undeclared == {}, (
        "spec_alias_curation curates these concepts but the caption mapper can never reach them, "
        f"so their denial lists filter nothing: {undeclared}. Either the lock is wrong or the "
        "exception belongs in _UNMATCHABLE_BY_DESIGN with a written reason.")


def test_no_stale_matchability_exception_is_left_behind(mappings):
    # The other direction, and what makes the allowlist self-cleaning: once a concept is unlocked
    # its entry must GO, or the next lock on it lands inside a standing waiver and this file stops
    # reporting it.
    stale = sorted(k for k in _UNMATCHABLE_BY_DESIGN
                   if mappings[k].get("alias_matching") != "disabled"
                   and mappings[k].get("extraction_mode") != "derive")
    assert stale == [], (
        f"{stale} is matchable now — drop the _UNMATCHABLE_BY_DESIGN entry so a future relock is "
        "reported instead of waived")


def test_every_matchability_exception_carries_a_reason():
    # A bare set of keys is a waiver nobody can review; the reason is the reviewable part.
    assert set(_UNMATCHABLE_BY_DESIGN) <= _curated_keys()
    thin = sorted(k for k, why in _UNMATCHABLE_BY_DESIGN.items() if len(why.strip()) < 20)
    assert thin == []


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


# ── amortisation is not depreciation: the Chinese list must not outclaim the English one ──────
@pytest.mark.parametrize("key", [AMORT_INTGBL, AMORT_INTGBL_COS])
def test_intangible_amortisation_does_not_alias_the_depreciation_captions(mappings, key):
    """The harvested `aliases_zh` asserted an equivalence the concept's own English list refuses.

    These concepts are amortisation of INTANGIBLE assets; 折旧 is depreciation, the charge on
    TANGIBLE assets. Measured on the shipped matcher before the denial:
    match("折旧", statement="profit_and_loss") -> is_pl__amort_and_impairment_intgbl, confidence
    1.0, EXACT, needs_review=False, where English "Depreciation" on the same statement returned
    UNMATCHED with review=True. Neither key is in extraction.llm_focus_keys and
    extraction.llm_focus_only = true, so the row is never forwarded to the model and the
    deterministic mis-map ships uncorrected.
    """
    _assert_absent(
        mappings[key],
        ["折旧", "折旧及摊销", "使用权资产的折旧", "物业及设备折旧"],
        "折旧 is depreciation on tangible assets, a different concept — it belongs to "
        f"{CF_DEPRECIATION}, and this concept's English aliases never claimed it")


@pytest.mark.parametrize("key", [AMORT_INTGBL, AMORT_INTGBL_COS])
def test_intangible_amortisation_keeps_its_own_chinese_vocabulary(mappings, key):
    # Refuse-and-redirect, not a blanket strip: the concept must still answer to its own caption.
    assert "无形资产摊销" in _normalized(mappings[key])


def test_the_depreciation_captions_stay_on_the_cash_flow_concept(mappings):
    # Where the redirect lands. This concept legitimately owns the whole family — its English
    # list is headed by "Depreciation" — so the P&L refusal must not have cost the cash-flow
    # reconciliation its binding.
    have = _normalized(mappings[CF_DEPRECIATION])
    missing = sorted(c for c in ("折旧", "折旧及摊销", "使用权资产的折旧", "物业及设备折旧",
                                 "Depreciation") if normalize_label(c) not in have)
    assert missing == [], f"{CF_DEPRECIATION} lost {missing}: the redirect has nowhere to land"


# ── borrowed English captions: another concept's line, or a heading that is no line at all ────
# The mirror image of the layer above, on the English side. `aliases_en` was harvested the same
# way `aliases_zh` was, so it picked up captions naming a DIFFERENT concept and captions that are
# a section or note HEADING. Measured on the shipped matcher loaded the way a caller that intends
# to MATCH loads it (`resolve=True`, which folds the section layer in so statement scoping runs),
# every one below was confidence 1.0, EXACT, needs_review=False — a confident wrong NUMBER:
#     "Profit/Loss before income tax" / P&L      -> is_pl__amort_and_impairment_intgbl
#     "Intangbile assets"            / P&L       -> is_pl__amort_and_impairment_intgbl
#     "Intangible assets"            / bs_nca    -> bs_nca__land_use_rights   (81 beats 80)
#     "Related Party Transactions"   / bs_nca    -> bs_nca__due_from_directors (1st of three 81s)
#     "Retained Profits"             / P&L       -> is_retained__cash_div_pref_shares (1st of six)
# `_prefer_label_owners` already answered the last two correctly with NO statement in hand; the
# defect only appears once statement scoping removes the label owner from the candidate set. None
# of these keys is in extraction.llm_focus_keys and extraction.llm_focus_only = true, so the row
# is never forwarded to the model and the deterministic mis-map ships uncorrected.
# See app/services/spec_alias_curation._BORROWED_CAPTION_DENIALS for the generator-side rules.

def test_intangible_amortisation_does_not_alias_a_pre_tax_profit_figure(mappings):
    _assert_absent(
        mappings[AMORT_INTGBL], ["Profit/Loss before income tax"],
        "a pre-tax profit figure is the P&L bottom line, not the period's amortisation charge — "
        "this alias filed the whole profit on one expense concept")


def test_intangible_amortisation_does_not_alias_the_harvested_typo(mappings):
    # "Intangbile assets" is a typo for the ASSET's own name, which is a balance-sheet carrying
    # amount rather than a charge — wrong twice over, and no filing prints it.
    _assert_absent(mappings[AMORT_INTGBL], ["Intangbile assets"],
                   "a misspelled asset name is not this concept's caption")


def test_land_use_rights_does_not_claim_the_bare_intangibles_caption(mappings):
    # Land use rights ARE an intangible, but they are one NAMED intangible sitting at priority 81,
    # so the borrowed claim outranked the sibling leaf that owns the bare caption at 80.
    _assert_absent(
        mappings[LAND_USE_RIGHTS], ["Intangible Assets"],
        f"the bare caption belongs to {OTHER_INTANGIBLES}, and 81 over 80 took it")


def test_the_bare_intangibles_caption_still_has_its_leaf_to_land_on(mappings):
    # Where the redirect goes. NOT NET_INTANGIBLES: that is the rollup PARENT of both leaves, so a
    # leaf caption bound there would double count against its own children.
    assert "intangible assets" in _normalized(mappings[OTHER_INTANGIBLES])
    assert "intangible assets" not in _normalized(mappings[NET_INTANGIBLES]), (
        f"{NET_INTANGIBLES} is the rollup parent of {LAND_USE_RIGHTS} and {OTHER_INTANGIBLES}")


@pytest.mark.parametrize("key", [DUE_FROM_DIRECTORS, DUE_FROM_SUBSIDIARIES, DUE_FROM_MI])
def test_a_due_from_concept_does_not_claim_the_related_party_note_heading(mappings, key):
    _assert_absent(
        mappings[key], ["Related Party Transactions"],
        f"the caption is a NOTE heading owned by {RP_NOTE}; three concepts claimed it at "
        "priority 81 and declaration order picked the winner")


def test_the_related_party_note_keeps_its_own_heading(mappings):
    # Refuse-and-redirect: the heading must still reach the note concept whose LABEL it is.
    assert "related party transactions" in _normalized(mappings[RP_NOTE])


@pytest.mark.parametrize("key", list(RETAINED_MOVEMENTS))
def test_a_retained_movement_line_does_not_claim_the_section_name(mappings, key):
    _assert_absent(
        mappings[key], ["Retained Profits"],
        f"the caption names the is_retained SECTION and is {EQUITY_RETAINED}' own label, not a "
        "movement inside it; seven concepts claimed it, six of them tied at priority 10")


def test_the_equity_concept_keeps_its_own_label_as_an_alias(mappings):
    # Not touched: "Retained Profits" IS this concept's label, and it is the redirect target for a
    # caption printed on the balance sheet.
    assert "retained profits" in _normalized(mappings[EQUITY_RETAINED])


# ── the same borrowed caption in Chinese ──────────────────────────────────────────────────────
# THE ENGLISH DENIAL LANDED AND THE CHINESE IT HAD ALREADY PULLED ACROSS STAYED. `scripts/
# enrich_output_csv_primary_aliases.py` transfers Chinese by matching the ENGLISH wording, and
# `hkfrs_hk_china_ontology.json`'s `bs_equity__retained_earnings` files exactly these four against
# "Retained profits" — so while all seven movement lines carried that borrowed English caption,
# all seven were handed the four Chinese spellings, and denying the English one did not take them
# back. Measured on the shipped line-item set before the clean-up:
#     match("未分配利润", statement="profit_and_loss")
#         -> is_retained__cash_div_pref_shares, 1.0, EXACT   (first of seven tied at priority 10)
#     match("对所有者（或股东）的分配", statement="profit_and_loss")   -> None, UNMATCHED
#     match("提取法定盈余公积", statement="profit_and_loss")          -> None, UNMATCHED
# A Chinese filing therefore got a confident wrong answer on the caption that names the BALANCE
# and an honest refusal on the two captions these lines exist to read.
RETAINED_BALANCE_ZH = ["保留溢利", "未分配利润", "留存收益", "累计亏损"]

# What each line's movement is actually called, CAS 所有者权益变动表 wording with the HK equivalent.
# One caption per line is enough to pin the redirect; the full lists live in the configuration.
RETAINED_MOVEMENT_ZH = {
    "is_retained__cash_div_pref_shares": "其他权益工具持有者分配",
    "is_retained__proposed_cash_dividends": "拟派现金股利",
    "is_retained__cash_div_common_shares": "对所有者（或股东）的分配",
    "is_retained__stock_dividends_nc": "以未分配利润转增股本",
    "is_retained__transfer_to_reserves": "提取法定盈余公积",
    "is_retained__prior_period_adjustments": "前期差错更正",
}


def _line_item_aliases(entry: dict) -> set[str]:
    out = list(entry.get("aliases") or [])
    for per_locale in (entry.get("aliases_i18n") or {}).values():
        out += per_locale
    return {normalize_label(a) for a in out}


@pytest.mark.parametrize("key", list(RETAINED_MOVEMENTS))
def test_a_retained_movement_line_does_not_claim_the_balance_in_chinese(line_items, key):
    present = sorted(_line_item_aliases(line_items[key])
                     & {normalize_label(z) for z in RETAINED_BALANCE_ZH})
    assert present == [], (
        f"{key} carries {present}: these are four spellings of the retained-earnings BALANCE — "
        f"{EQUITY_RETAINED}' own label and the is_retained SECTION's name — not a movement inside "
        f"it. tests/test_equity_matrix.py shows 保留溢利 is a COLUMN HEADER of the statement of "
        f"changes in equity, so it names the column a movement prints under, never the movement")


def test_the_equity_concept_keeps_its_chinese_balance_vocabulary(line_items):
    """Refuse-and-redirect, the Chinese half. The four spellings must still reach the concept
    whose label they are, or the denial has cost a Chinese filing its retained-earnings line."""
    have = _line_item_aliases(line_items[EQUITY_RETAINED])
    missing = sorted(z for z in RETAINED_BALANCE_ZH if normalize_label(z) not in have)
    assert missing == [], f"{EQUITY_RETAINED} lost {missing}: the redirect has nowhere to land"


@pytest.mark.parametrize("key,caption", sorted(RETAINED_MOVEMENT_ZH.items()))
def test_each_retained_movement_line_carries_its_own_chinese_caption(line_items, key, caption):
    """The positive half, and the reason the denial is not simply a deletion. Removing the
    borrowed balance spellings without authoring the movement wording would leave these seven
    lines with no Chinese vocabulary at all — unmatched on a PRC filing rather than wrongly
    matched, which is better and is still not the line being read."""
    assert normalize_label(caption) in _line_item_aliases(line_items[key])


def test_the_retained_residual_bucket_is_given_no_chinese_vocabulary(line_items):
    """The seventh line is the section's residual, and it gets the denial without the authoring.

    `residual_framework.prohibitions` says a bucket is "never populated by alias, regex or
    embedding match" and it declares `alias_matching: disabled`, so movement wording authored here
    would contradict the bucket's own definition. The stranded spellings still go —
    `_BORROWED_CAPTION_DENIALS` records why it denies them on this key regardless of the lock.
    """
    entry = line_items["is_retained__other_adj_to_retained_profits"]
    assert not (entry.get("aliases_i18n") or {}).get("zh")


@pytest.fixture(scope="module")
def line_item_matcher():
    """The matcher A RUN uses, built off the line-item set rather than the generator's rulebook.

    `resolve=True` folds the section layer in, which is what turns statement scoping on — and
    statement scoping is half of this defect: `_prefer_label_owners` answered 未分配利润 correctly
    with no statement in hand, and the wrong answer only appeared once scoping removed the label
    owner from the candidate set on a profit-and-loss page.
    """
    from app.schemas.line_items import load_line_item_set
    from app.services.mapping import OntologyMatcher
    from app.services.working_view import build_working_view

    raw = json.loads(LINE_ITEMS.read_text(encoding="utf-8"))
    return OntologyMatcher(build_working_view(load_line_item_set(raw, resolve=True)))


@pytest.mark.parametrize("caption", RETAINED_BALANCE_ZH)
def test_a_chinese_balance_caption_no_longer_binds_a_movement_line(line_item_matcher, caption):
    """THE BEHAVIOUR, not the configuration. Each of these matched a movement line EXACT at 1.0 on
    a profit-and-loss page; each must now refuse there and still resolve on the balance sheet."""
    on_pl = line_item_matcher.match(caption, statement="profit_and_loss", section=None)
    assert (getattr(on_pl, "canonical_key", None) or "") not in RETAINED_MOVEMENTS, (
        f"{caption} still binds {on_pl.canonical_key} on the income statement")
    on_bs = line_item_matcher.match(caption, statement="balance_sheet", section=None)
    assert getattr(on_bs, "canonical_key", None) == EQUITY_RETAINED, (
        f"{caption} must still reach {EQUITY_RETAINED} on the balance sheet, not "
        f"{getattr(on_bs, 'canonical_key', None)}")


@pytest.mark.parametrize("key,caption", sorted(RETAINED_MOVEMENT_ZH.items()))
def test_a_chinese_movement_caption_now_binds_its_own_line(line_item_matcher, key, caption):
    """The other half, and the one that says the lines are now READABLE. Every one of these
    returned None before the movement vocabulary was authored."""
    got = line_item_matcher.match(caption, statement="profit_and_loss", section=None)
    assert getattr(got, "canonical_key", None) == key, (
        f"{caption} -> {getattr(got, 'canonical_key', None)}, expected {key}")


def test_no_two_retained_lines_claim_the_same_chinese_caption(line_items):
    """WHAT THE OLD LIST ACTUALLY COST, pinned so it cannot come back by a different route. Seven
    lines sharing one identical vocabulary means declaration order picks the winner and six lines
    are unreachable — so the fix is not only "the right words" but "a different set per line"."""
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for key in RETAINED_MOVEMENTS:
        for alias in ((line_items[key].get("aliases_i18n") or {}).get("zh") or []):
            norm = normalize_label(alias)
            if norm in seen:
                clashes.append(f"{alias} on both {seen[norm]} and {key}")
            seen[norm] = key
    assert clashes == [], clashes


def test_every_denied_borrowed_caption_is_gone_from_the_configuration(line_items):
    """The curation has to hold on the file a RUN reads, and there is now exactly one of those.

    THIS TEST USED TO CHECK BOTH SHIPPED FILES, on the grounds that `extraction.mapping_engine`
    decided which of the two answered — so a deletion in one only was "two different wrong answers
    depending on the engine". That switch is gone: line items is the single configuration engine, a
    run maps against a `line_item_versions` row built from
    `app/sample/templates/output_csv_hk_line_items.json`, and the rulebook JSON beside it is a
    GENERATOR INPUT (`scripts/build_line_items.py` projects it into the set) rather than something a
    run, a user or an operator can select. So the assertion moved to the one file that governs; the
    contaminated alias this pins is exactly as wrong there as it ever was, and a caption left behind
    in the generator source now costs nothing until the set is regenerated — at which point THIS
    test is the one that fires.
    """
    from app.services.spec_alias_curation import denied_aliases

    def _all(entry: dict) -> list[str]:
        out = list(entry.get("aliases") or [])
        for per_locale in (entry.get("aliases_i18n") or {}).values():
            out += per_locale
        return out

    offending: dict[str, list[str]] = {}
    for key in (AMORT_INTGBL, LAND_USE_RIGHTS, DUE_FROM_DIRECTORS, DUE_FROM_SUBSIDIARIES,
                DUE_FROM_MI, *RETAINED_MOVEMENTS):
        removed = denied_aliases(key, _all(line_items[key]))
        if removed:
            offending[key] = removed
    assert offending == {}


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


def test_the_rulebook_refuses_the_face_total_captions_at_every_tier(mappings):
    """Absent from the aliases is not enough: the model can name a concept nothing aliased.

    The captions the filing actually prints are 一、营业总收入 and 其中：营业收入 — the total and
    its own "of which" restatement, both carrying the same figure. `exclude_hints` is the only
    field that binds every tier including the LLM's answer (`OntologyMatcher._allowed`), so the
    prohibition is pinned on the mechanism that enforces it rather than on the alias list.
    """
    from app.schemas.loader import load_ontology
    from app.services.mapping import OntologyMatcher

    matcher = OntologyMatcher(load_ontology(json.loads(ONTOLOGY.read_text(encoding="utf-8"))))

    for caption in ("一、营业总收入", "其中：营业收入", "营业收入", "营业总收入"):
        assert matcher._vetoed(SALES, caption), caption
    # And the concept's own vocabulary survives its own exclusions — see the loader guard.
    for caption in ("主营业务", "主营业务收入", "TURNOVER", "Revenue"):
        assert not matcher._vetoed(SALES, caption), caption


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
    # `template_note` was in this list and is gone: it was authored on 395 of 539 items as 395
    # DISTINCT hand-written sentences and read by no code at all, so it was removed rather than
    # left to imply a decision nothing acts on.
    for field in ("extraction_mode", "value_scope", "match_priority", "section_disambiguation"):
        assert m.get(field), f"{CONTINGENT}.{field} is unset; every sibling concept declares it"


# ── the modes of the four note-assembled pairs agree within each pair ─────────────────────────
@pytest.mark.parametrize("key", [SECUR_CP, OTHER_RECV_CP])
def test_a_service_computed_field_is_not_declared_plain_extract(mappings, key):
    # Its value USED TO BE computed by a stage that overwrote whatever the caption mapper left, and
    # declaring it a plain `extract` let an alias-matched caption be consumed by an exclusive_leaf
    # whose value was then discarded, stranding the concept the caption really named. That stage is
    # deleted and the figure must come from configuration now, so this pin has become the narrower
    # one it should always have been: `extract_or_derive` is what keeps these two out of the
    # exclusive-leaf claim while their caption binding is still being authored. Tightening either
    # to `extract` reopens the stranding, and `derive` would close the caption route that is now
    # their only route, so the mode stays exactly as declared.
    assert mappings[key].get("extraction_mode") == "extract_or_derive"


def test_no_spec_governed_concept_declares_an_alias_its_own_hints_delete(mappings):
    import re
    for key in SPEC_GOVERNED:
        m = mappings[key]
        for hint in m.get("exclude_hints") or []:
            for alias in _aliases(m):
                assert not re.search(hint, alias, re.IGNORECASE), (
                    f"{key}: exclude_hint {hint!r} deletes its own alias {alias!r}")
