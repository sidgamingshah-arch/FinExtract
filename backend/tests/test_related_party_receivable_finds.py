"""The two Finds that never fired, and the caption that had nowhere to go.

`bs_nca__due_from_related_parties_ltp` takes the LARGEST of three readings of one quantity — the
face's own figure (Find 1), the four receivable notes (Find 2) and the related-party note (Find 3).
Only Find 3 ever answered, so "the largest of three" was deciding from a sample of one on every
filing in the corpus.

FIND 1 WAS A DEAD DECLARATION: `type: derived` with `terms: []` and `cascade: []`, no aliases, no
route. Nothing could fill it. It now sums the NON-CURRENT related-party receivable columns the
balance sheet prints — see `test_note_sourced` for why only those.

FIND 2 COULD NOT REACH ITS NOTES, for two reasons, both measured:

  * 关联借款 — a related-party LOAN printed as a row of the 其他应收款 note's by-nature table —
    matched none of its patterns, which all read 关联方… and none of which reaches the same words
    without the 方. On 000709's standalone note it is 88,000,000.00 in both years, beside 保证金,
    押金 and 货款.
  * The English other-receivables title was anchored at `^`, and an HKEX filing titles the note by
    everything it covers. China SCE prints "PREPAYMENTS, OTHER RECEIVABLES AND DEPOSITS", so the
    note holding its other receivables was never searched.

AND THE CAPTION. The governed ontology gives `bs_nca__due_from_related_parties_ltp` 23 English and
11 Chinese aliases — "Due from related parties", "Related companies current account", 应收关联方款项
— and it is a DERIVED PARENT, which `mapping._computed_parent` locks out of the matcher. Every one
of them was inert, and China SCE's balance sheet prints "Due from related parties 應收關聯方款項"
4,065,231 under CURRENT ASSETS: it reached `bs_ca__other_current_assets`, a related-party
receivable published as an other current asset. The words now sit on the CURRENT column, which is
matchable and is the label owner for that caption.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.mapping import OntologyMatcher
from app.services.working_view import build_working_view

_SEED = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def cfg():
    return load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def shipped(cfg):
    return {i.key: i for i in cfg.items}


@pytest.fixture(scope="module")
def matcher(cfg):
    return OntologyMatcher(build_working_view(cfg))


# --- Find 2 can now reach its notes --------------------------------------------------------------

@pytest.mark.parametrize("caption", ["关联借款", "关联方借款", "關聯借款", "4、关联借款"])
def test_a_related_party_loan_row_is_selected(shipped, caption):
    """关联借款 says what 关联方借款 says without the 方, and 000709 prints it that way."""
    patterns = shipped["sub__rp_find_2"].note_source.row_caption_any

    assert any(re.search(p, caption, re.IGNORECASE) for p in patterns), caption


@pytest.mark.parametrize("title", [
    "PREPAYMENTS, OTHER RECEIVABLES AND DEPOSITS",
    "Prepayments, other receivables and deposits",
    "其他应收款",
    "24. OTHER RECEIVABLES",
])
def test_the_other_receivables_note_qualifies_however_it_is_titled(shipped, title):
    """By meaning now: the heading carries every word of one of Find 2's note terms."""
    from app.services.line_item_notes import heading_covered

    assert heading_covered(shipped["sub__rp_find_2"], title), title


@pytest.mark.parametrize("title", ["应收账款", "预付款项", "RELATED PARTY TRANSACTIONS", "TRADE RECEIVABLES"])
def test_a_note_on_another_subject_is_not_find_2s(shipped, title):
    """What the anchored Chinese pattern used to guard: Find 2 reads the other-receivables,
    long-term-receivables and loans-and-advances notes, not the trade-receivable or prepayment
    notes beside them."""
    from app.services.line_item_notes import heading_covered

    assert not heading_covered(shipped["sub__rp_find_2"], title), title


def test_a_payable_row_is_still_refused(shipped):
    """Find 2 reads the RECEIVABLE side, so the 应付 vetoes stay."""
    vetoes = shipped["sub__rp_find_2"].note_source.row_caption_none

    for refused in ("应付关联方款项", "合计", "期初余额", "交易金额", "委托贷款"):
        assert any(re.search(v, refused) for v in vetoes), refused


# --- and the face caption reaches the column that owns it ----------------------------------------

@pytest.mark.parametrize("caption", [
    "Due from related parties 應收關聯方款項",
    "Due from related companies",
    "Related companies current account",
    "應收關聯方款項",
])
def test_a_current_related_party_receivable_reaches_the_current_column(matcher, caption):
    res = matcher.match(caption, statement="balance_sheet",
                        section="CURRENT ASSETS 流動資產")

    assert res.canonical_key == "bs_ca__due_from_related_parties_cp", caption


def test_the_derived_parent_still_carries_no_recognition(shipped):
    """The words moved BECAUSE the parent cannot use them — it must not end up holding them."""
    assert not shipped["bs_nca__due_from_related_parties_ltp"].aliases
    assert not shipped["sub__rp_find_1"].aliases, (
        "Find 1 with aliases would be a second claimant on every one of those captions at equal "
        "priority with no label owner — the unbreakable-alias-tie shape held at zero")


def test_the_caption_has_exactly_one_home_among_the_matchable_lines(cfg):
    """What the tie ceiling is for: one caption, one claimant that can actually be reached."""
    import collections

    claimed = collections.defaultdict(set)
    for item in cfg.items:
        for alias in (item.aliases or []):
            claimed[" ".join(alias.split()).casefold()].add(item.key)
    for caption in ("due from related parties", "應收關聯方款項"):
        assert claimed[caption] == {"bs_ca__due_from_related_parties_cp"}, claimed[caption]
