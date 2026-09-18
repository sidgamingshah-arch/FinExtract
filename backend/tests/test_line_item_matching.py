"""The ported matcher: does a caption resolve to the same line item the rulebook resolves it to?

`scripts/parity_line_items.py` is the real proof — 11,433 probes (every caption in the rulebook ×
every PRINTED BANNER in its statement) through both engines, 0 disagreements. This file pins the
BEHAVIOURS that proof depends on, so a regression names itself instead of showing up as a number
dropping in a script nobody ran.

Four of these tests exist because something got it wrong first, and each was worth catching:

  `bs_top_level`      a scope id that names NO section means unconstrained. Compared literally,
                      the statement-level totals were refused under every banner in their own
                      statement — 130 of 133 disagreements, every one a real caption ("total
                      assets", "share capital", 资产总计) that resolves today and would have gone
                      unmapped.
  the four statements the gate narrows by `bs_/pl_/cf_/eq_`, NOT by all seven `StatementType`
                      values. `notes` and `statement_setup` are places a caption is printed, not
                      statements to refuse a concept for belonging elsewhere. The last 3.
  one statement,      the classifier says `changes_in_equity`, `StatementType` says
  two spellings       `equity_changes`. Compared raw, every definition on that statement is
                      refused on every page of it.
  banner text, not    the harness itself. Its first version passed scope ids ("bs_ca", "is_pl")
  scope ids           as the section, and `section_of_banner` maps printed TEXT — so every one
                      resolved to None, the section gate never fired, and a 100% result said
                      nothing about the section logic at all. Probing with real banners moved
                      both-unmatched from 1,186 to 5,464, which is the gate actually refusing.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.schemas.line_items import LineItemSet, load_line_item_set
from app.services.line_item_matching import LineItemMatcher

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


def _matcher(*items: dict) -> LineItemMatcher:
    return LineItemMatcher(LineItemSet.model_validate({"items": list(items)}))


@pytest.fixture(scope="module")
def shipped() -> LineItemMatcher:
    return LineItemMatcher(load_line_item_set(json.loads(SEED.read_text(encoding="utf-8"))))


# ── the exact tier ───────────────────────────────────────────────────────────────────────────────

def test_an_exact_alias_resolves():
    m = _matcher({"key": "a", "label": "Trade receivables", "aliases": ["Debtors"]})

    assert m.match("Debtors").key == "a"
    assert m.match("  DEBTORS  ").key == "a"      # normalisation is the incumbent's own
    assert m.match("Trade receivables").key == "a"


def test_a_caption_nothing_claims_is_unmatched_and_flagged():
    got = _matcher({"key": "a", "aliases": ["Debtors"]}).match("Goodwill")

    assert got.key is None
    assert got.needs_review
    assert "no alias" in got.reason


def test_either_half_of_a_bilingual_caption_can_be_the_alias():
    """"REVENUE 收益" must read the way the monolingual "Revenue" does."""
    m = _matcher({"key": "a", "label": "Revenue", "aliases_i18n": {"zh": ["收益"]}})

    assert m.match("REVENUE 收益").key == "a"
    assert m.match("收益").key == "a"


def test_a_per_locale_alias_is_reachable_without_naming_the_locale():
    """The matcher folds every locale into one index; a page does not announce its language."""
    m = _matcher({"key": "a", "label": "Fixed assets",
                  "aliases_i18n": {"zh-Hans": ["固定资产"], "zh-Hant": ["固定資產"]}})

    assert m.match("固定资产").key == "a"
    assert m.match("固定資產").key == "a"


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────

def test_a_caption_on_one_statement_cannot_claim_another_statements_line():
    """96 aliases in the rulebook are shared across statements; only this refuses them."""
    m = _matcher(
        {"key": "bs_nca__intangibles", "statement": "balance_sheet",
         "section_scope": ["bs_nca"], "aliases": ["Intangible assets"]},
        {"key": "pl_impairment_intangibles", "statement": "profit_and_loss",
         "section_scope": ["is_pl"], "aliases": ["Intangible assets"]})

    assert m.match("Intangible assets", "balance_sheet", "bs_nca").key == "bs_nca__intangibles"
    assert m.match("Intangible assets", "profit_and_loss", "is_pl").key == \
        "pl_impairment_intangibles"


def test_a_section_banner_separates_two_claimants_on_one_statement():
    """A property developer prints one borrowings caption twice, current and non-current.

    THE SECTION ARGUMENT IS PRINTED BANNER TEXT, not a scope id. `section_of_banner` — which the
    gate calls — maps what a page actually prints ("NON-CURRENT LIABILITIES", 流動負債) onto a
    token; a scope id like "bs_ncl" resolves to None and narrows NOTHING. A first version of the
    parity harness passed scope ids and scored 100% while the section gate never fired once.
    """
    m = _matcher(
        {"key": "bs_ncl__borrowings", "statement": "balance_sheet", "section_scope": ["bs_ncl"],
         "aliases": ["Interest-bearing bank and other borrowings"]},
        {"key": "bs_cl__borrowings", "statement": "balance_sheet", "section_scope": ["bs_cl"],
         "aliases": ["Interest-bearing bank and other borrowings"]})
    caption = "Interest-bearing bank and other borrowings"

    assert m.match(caption, "balance_sheet",
                   "NON-CURRENT LIABILITIES").key == "bs_ncl__borrowings"
    assert m.match(caption, "balance_sheet", "CURRENT LIABILITIES").key == "bs_cl__borrowings"
    # And in the language the filing was printed in.
    assert m.match(caption, "balance_sheet", "流動負債").key == "bs_cl__borrowings"


def test_a_scope_id_passed_as_a_banner_narrows_nothing():
    """Stated as a test so nobody re-learns it from a parity run that proves less than it says."""
    from app.services.mapping import section_of_banner

    assert section_of_banner("bs_ncl") is None
    assert section_of_banner("NON-CURRENT LIABILITIES") == "non_current_liabilities"


def test_a_top_level_scope_is_not_constrained_by_any_banner(shipped):
    """`bs_top_level` names NO section, so no banner may refuse it.

    A section hint is the nearest PRECEDING banner, and a statement total routinely carries the
    banner of the last section printed above it. Compared literally this was 130 of 133 parity
    disagreements.
    """
    for banner in ("CURRENT ASSETS", "NON-CURRENT ASSETS", "CURRENT LIABILITIES",
                   "NON-CURRENT LIABILITIES", "EQUITY", "流動負債"):
        got = shipped.match("total assets", "balance_sheet", banner)
        assert got.key == "bs_ca__total_assets", f"refused under {banner!r}"


def test_only_the_four_namespaced_statements_narrow_the_gate(shipped):
    """`notes` and `statement_setup` are places a caption is printed, not statements to refuse by."""
    # `notes` is not one of the four, so a notes caption is not refused for belonging elsewhere.
    assert shipped.match("options", "notes").key == "bs_nca__options_nca"
    assert shipped.match("total assets", "statement_setup").key == "bs_ca__total_assets"
    # …while one of the four does narrow.
    assert shipped.match("options", "cash_flow").key is None


def test_the_two_spellings_of_the_equity_statement_agree():
    """The classifier says `changes_in_equity`; StatementType says `equity_changes`."""
    m = _matcher({"key": "eq_total", "statement": "equity_changes", "aliases": ["Total equity"]})

    assert m.match("Total equity", "changes_in_equity").key == "eq_total"
    assert m.match("Total equity", "equity_changes").key == "eq_total"


def test_an_exclusion_outranks_the_line_items_own_alias():
    """The point of `exclude_hints` is that one line stops a mis-mapping, from any tier."""
    m = _matcher({"key": "a", "aliases": ["Finance costs"],
                  "exclude_hints": ["capitalised"]})

    assert m.match("Finance costs").key == "a"
    assert m.match("Finance costs capitalised").key is None


def test_an_exclusion_is_matched_case_insensitively():
    """Every hint shipped in the rulebook is lowercase; the trap was waiting for the next editor."""
    m = _matcher({"key": "a", "aliases": ["Borrowings"], "exclude_hints": ["non-current"]})

    assert m.match("Borrowings NON-CURRENT").key is None


# ── the locks ────────────────────────────────────────────────────────────────────────────────────

def test_a_residual_is_reachable_by_matching_and_released_by_the_sweep():
    """THE LOCK IS GONE, BY DIRECTIVE, AND THE SECOND GUARD IS WHY THAT IS SAFE.

    `alias_matching: "disabled"` kept a bucket out of every tier — ""Others" matches almost anything
    short, and a figure landing there ties the reconciliation that was supposed to report the gap".
    The others master replaces the field, and the caption lock is not part of what it derives.

    What stands in its place is the framework's prohibition 2, "never populated by alias, regex or
    embedding match", implemented in `stages.residual` as a RELEASE rather than a refusal: a row that
    arrives already claimed for a residual is handed back, its key cleared, flagged
    `residual_alias_populated:<method>`, and re-offered to the section's dedicated concepts before
    the sweep runs. So the MATCHER may now answer with a bucket, and the stage that owns buckets
    undoes it — which is asserted end to end by the no-figures-move comparison over three filings
    rather than here, because this file has no pipeline.
    """
    m = _matcher({"key": "bs_ca__others", "aliases": ["Others"],
                  "value_scope": "exclusive_residual"},
                 {"key": "bs_ca__real", "aliases": ["Prepayments"]})

    assert m.match("Others").key == "bs_ca__others"
    assert m.match("Prepayments").key == "bs_ca__real"


def test_a_residual_is_still_never_offered_to_the_model():
    """The job `alias_matching` was doing that DID have to survive, and the one whose loss cost the
    most: `_never_asked` read the lock, so removing it offered eleven residual buckets to the model.
    A residual carries a section's UNEXPLAINED remainder, so a figure the model files there makes the
    reconciliation that would have reported the gap tie instead. The marker carries it now."""
    from app.schemas.line_items import LineItemDef
    from app.services.line_item_requests import asked_about

    bucket = LineItemDef(key="bs_ca__others", label="Others",
                         value_scope="exclusive_residual")
    ordinary = LineItemDef(key="bs_ca__real", label="Prepayments")

    assert not asked_about(bucket), bucket._never_asked()
    assert asked_about(ordinary)


def test_a_computed_line_is_not_offered_to_a_printed_caption():
    """`extraction_mode: derive` means the framework computes it and a filing does not print it."""
    m = _matcher({"key": "pl_gross_profit", "aliases": ["Gross profit"],
                  "extraction_mode": "derive", "type": "derived",
                  "implemented_by": "rulebook_derivation"})

    assert m.match("Gross profit").key is None


# ── tie-breaking ─────────────────────────────────────────────────────────────────────────────────

def test_a_line_item_that_owns_the_label_beats_a_higher_priority_borrower():
    """A line item may carry another's full label as an over-broad alias."""
    m = _matcher({"key": "borrower", "label": "Something else",
                  "aliases": ["Trade receivables"], "match_priority": 99},
                 {"key": "owner", "label": "Trade receivables", "match_priority": 10})

    assert m.match("Trade receivables").key == "owner"


def test_priority_settles_a_tie_the_gate_leaves_standing():
    m = _matcher({"key": "low", "aliases": ["Deposits"], "match_priority": 10},
                 {"key": "high", "aliases": ["Deposits"], "match_priority": 90})

    assert m.match("Deposits").key == "high"


def test_a_contested_caption_is_settled_by_an_exclusion_on_the_line_it_is_not():
    """REPLACES THREE TESTS THAT PINNED `confusable_with`, and the replacement is the decision.

    That field named a pair each claiming the other's captions, and `_forbidden_tie` refused such a
    pair at equal priority: both keys returned, needs_review set, nothing published. Measured
    against the shipped set, it fired on 8 of 238 equal-priority alias collisions — so 230 identical
    situations already resolved by file order, and on those 8 file order was WRONG (土地使用权,
    the Chinese for land use rights, went to Buildings because Buildings is declared first).

    The pairs are now expressed where a disagreement belongs: an exclusion on the line the caption
    is NOT. `_vetoed` makes that outrank the line's own alias, which is the whole point of the
    field, so the answer is deterministic and readable on the line rather than deferred to whoever
    picks up the review queue.
    """
    m = _matcher({"key": "a", "aliases": ["Notes payable", "Notes payable (current)"],
                  "match_priority": 80, "exclude_hints": [r"^Notes\s+payable$"]},
                 {"key": "b", "aliases": ["Notes payable"], "match_priority": 80})
    got = m.match("Notes payable")

    assert got.key == "b"
    assert not got.needs_review
    # …and the excluded line still wins its own distinctive caption.
    assert m.match("Notes payable (current)").key == "a"


def test_an_exclusion_outranks_the_lines_own_alias():
    """The property the conversion depends on: if it did not hold, the losing line would keep
    claiming the caption and the tie would simply be silent instead of refused."""
    m = _matcher({"key": "a", "aliases": ["Notes payable"], "match_priority": 90,
                  "exclude_hints": [r"^Notes\s+payable$"]},
                 {"key": "b", "aliases": ["Notes payable"], "match_priority": 10})

    assert m.match("Notes payable").key == "b", "the higher-priority line's exclusion was ignored"


def test_a_banner_separates_a_pair_that_would_otherwise_tie():
    """The gate settles most collisions before any of this matters — which is why only 8 of the
    set's 238 equal-priority collisions were ever declared as pairs."""
    m = _matcher({"key": "a", "statement": "balance_sheet", "section_scope": ["bs_ncl"],
                  "aliases": ["Notes payable"], "match_priority": 80},
                 {"key": "b", "statement": "balance_sheet", "section_scope": ["bs_cl"],
                  "aliases": ["Notes payable"], "match_priority": 80})

    assert m.match("Notes payable", "balance_sheet", "CURRENT LIABILITIES").key == "b"


# ── the rule tier ────────────────────────────────────────────────────────────────────────────────

def test_a_regex_hint_matches_when_no_alias_does():
    m = _matcher({"key": "a", "aliases": ["Revenue"], "regex_hints": [r"turnover"]})
    got = m.match("Total turnover for the period")

    assert got.key == "a"
    assert got.confidence == 0.95


def test_keyword_hints_require_every_keyword():
    m = _matcher({"key": "a", "keyword_hints": ["cash", "financing"]})

    assert m.match("Net cash used in financing activities").key == "a"
    assert m.match("Net cash used in investing activities").key is None


def test_a_hint_defeated_by_punctuation_fires_on_the_normalised_spelling():
    """The rule tier sees BOTH spellings: the caption as printed, and normalised.

    What the normalised spelling buys is punctuation removal — `from/(used in)` becomes
    `from used in`, so a hint written against the words matches a line the printed punctuation
    would have defeated. It does NOT remove a bilingual tail: an end-anchored hint is still
    defeated by "… investing activities 投資活動" in both spellings, which is why the anchor
    belongs at the start.
    """
    m = _matcher({"key": "a", "regex_hints": [r"^net cash flows from used in investing"]})

    assert m.match("Net cash flows from/(used in) investing activities 投資活動").key == "a"


def test_an_end_anchored_hint_is_still_defeated_by_a_bilingual_tail():
    """Stated so the limit is documented rather than discovered on a filing."""
    m = _matcher({"key": "a", "regex_hints": [r"^net cash.*investing activities$"]})

    assert m.match("Net cash flows from/(used in) investing activities 投資活動").key is None
    assert m.match("Net cash flows from investing activities").key == "a"


def test_several_rule_hits_stay_ambiguous_and_are_flagged():
    m = _matcher({"key": "a", "regex_hints": ["deposit"], "match_priority": 10},
                 {"key": "b", "regex_hints": ["deposit"], "match_priority": 90})
    got = m.match("Short-term deposit")

    assert got.key == "b"                 # the highest-priority claimant is reported
    assert got.confidence == 0.6
    assert got.needs_review


def test_an_exclusion_vetoes_the_rule_tier_too():
    m = _matcher({"key": "a", "regex_hints": ["borrowings"], "exclude_hints": ["current"]})

    assert m.match("Bank borrowings").key == "a"
    assert m.match("Current bank borrowings").key is None


# ── the shipped set as a whole ───────────────────────────────────────────────────────────────────

def test_the_shipped_set_carries_the_whole_rulebook(shipped):
    # 527, not 543: the live configuration was exported into the shipped seed — see
    # `test_retired_derivations.test_the_shipped_set_is_the_configuration_in_force`.
    assert len(shipped.by_key) == 538  # 538 since the loss allowance split into its two readings — the 坏账准备 COLUMN of a measure grid and an allowance printed as its own ROW — which is what lets CP_P2 subtract it  # 536 since the other-receivables line gained the interest-and-dividends-receivable component its own definition names: a filing that prints 应收利息/应收股利 as siblings of 其他应收款 has not put them inside it  # 535 since the other-receivables NET split into the two ways a note prints one — the 账面价值 COLUMN of a gross/allowance/net grid, and a row whose own reported amount is the net; one part holds one `measure`, and 000709 needs both readings
    assert len(shipped._alias_index) > 1900, "the alias vocabulary did not survive the merge"


def test_the_shipped_set_still_locks_the_lines_that_must_be_locked(shipped):
    """IT WAS 16 AND IS 11, and the difference is the eleven residual buckets leaving this set.

    `_unmatchable` had three clauses and now has two: `extraction_mode == "derive"` and
    `type == "derived"`. The buckets were in it through `alias_matching: "disabled"`, which the
    others master removes — they are kept away from the MODEL by `_never_asked` instead, and away
    from a bucket's own total by the sweep's release. What must still be locked is every line whose
    figure is arithmetic: a caption claiming one of those skips the arithmetic entirely.
    """
    locked = shipped._unmatchable
    derived = {d.key for d in shipped.set.items
               if str(getattr(d.type, "value", d.type)) == "derived"}

    assert derived <= locked, sorted(derived - locked)
    assert len(locked) >= len(derived), (len(locked), len(derived))
