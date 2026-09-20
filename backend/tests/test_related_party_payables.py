"""The related-party PAYABLES, which every filing in the corpus left empty.

The receivable side has been built out for a while: `sub__rp_find_3` reads the related-party note's
账面余额 and 坏账准备 and nets them, and `bs_nca__due_from_related_parties_ltp` picks the largest of
three readings. The payable side had nothing. Eight concepts — Due to Related Parties(CP) and (LTP),
the shareholder, director, subsidiary and minority-interest variants, and the two trade splits —
carried a `route: face` and, as their only recognition, the template's own compound label ("Due to
related parties / Associates / Jointly controlled entities / fellow subsidiaries/Affiliates"), a
caption no filing prints. So they could not fire, and did not, on any of the five filings.

WHAT THE FILING ACTUALLY PRINTS. 000709's 关联方应收应付款项 note has an ②应付项目 table laid out
by GROUP, and the groups are exactly the distinctions the template draws:

    应付账款：          <- trade payables to related parties        合计   818,991,135.15
      唐钢美锦（唐山）煤化工有限公司            37,492,764.37
      ...
    其他应付款：        <- everything else owed to them             合计   102,310,636.03
    长期应付款：        <- the non-current part                     合计   856,059,679.92

The ROW captions are counterparty NAMES; the group heading is the caption an author would write.
`select_rows` already matches a row by its own caption OR by its group — this adds the two parts
that ask for the groups.

ONLY TWO OF THE THREE. Making a concept note-sourced means making it a DERIVED PARENT, and
`mapping._computed_parent` locks a derived parent out of the matcher so a figure cannot arrive by a
route that skips the cascade. `bs_cl__trade_payables_related_parties` has a caption a test pins to
it — `test_a_resolved_tie_kept_a_home_for_every_caption_it_split` guards "Trade payables to related
parties", split away from the (CP) aggregate it is a part of — so that column stays on the face and
its 818,991,135.15 is still out of reach. A printed column can be face-matchable or note-sourced,
not both.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.line_items import load_line_item_set

_SEED = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")

PARTS = {"sub__rp_other_payable": "bs_cl__due_to_related_parties_cp",
         "sub__rp_payable_ltp": "bs_ncl__due_to_related_parties_ltp"}


@pytest.fixture(scope="module")
def shipped():
    return {i.key: i for i in
            load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")),
                               resolve=True).items}


# --- what the parts are ------------------------------------------------------------------------

@pytest.mark.parametrize("part,parent", sorted(PARTS.items()))
def test_each_part_reads_the_note_and_names_its_parent(shipped, part, parent):
    p = shipped[part]

    assert p.route == "note_tables"
    assert p.parent == parent
    assert p.namespace == "internal" and not p.in_output
    assert p.note_source is not None


@pytest.mark.parametrize("part,group", [
    ("sub__rp_other_payable", "其他应付款："),
    ("sub__rp_payable_ltp", "长期应付款："),
])
def test_the_part_asks_for_the_group_the_note_prints(shipped, part, group):
    """The rows carry counterparty names, so the group heading is what selects them — and
    `select_rows` matches `row_caption_any` against the group as well as the caption."""
    import re

    patterns = shipped[part].note_source.row_caption_any
    assert any(re.search(p, group) for p in patterns), (group, patterns)


@pytest.mark.parametrize("part,other", [
    ("sub__rp_other_payable", "长期应付款："),
    ("sub__rp_payable_ltp", "其他应付款："),
])
def test_neither_part_claims_the_others_group(shipped, part, other):
    """Two lines reading one table have to divide it, or the current and non-current payables
    publish the same figure."""
    import re

    assert not any(re.search(p, other) for p in shipped[part].note_source.row_caption_any)


def test_the_trade_payable_group_is_claimed_by_neither(shipped):
    """It is a separate printed column, and it stays on the FACE — see the module docstring."""
    import re

    for part in PARTS:
        assert not any(re.search(p, "应付账款：")
                       for p in shipped[part].note_source.row_caption_any)
    trade = shipped["bs_cl__trade_payables_related_parties"]
    assert trade.type != "derived"
    assert "Trade payables to related parties" in trade.aliases


def test_a_total_and_a_transaction_volume_are_refused(shipped):
    """The vetoes that keep a group's 合计 — and the note's transaction table — out of the sum.
    The veto is applied to the group AND to the row's own caption, so a row admitted by its group
    is still refused by its caption."""
    import re

    for part in PARTS:
        vetoes = shipped[part].note_source.row_caption_none
        for refused in ("合计", "期初余额", "交易金额", "本期发生额", "担保", "委托贷款",
                        "坏账准备", "合同负债"):
            assert any(re.search(v, refused) for v in vetoes), (part, refused)


def test_the_payable_vocabulary_is_not_vetoed_as_the_receivable_side_vetoes_it(shipped):
    """`sub__rp_find_3_gross` refuses 应付 / due to — it reads the RECEIVABLE side. These two
    would refuse their own subject if they inherited that."""
    import re

    for part in PARTS:
        vetoes = shipped[part].note_source.row_caption_none
        for wanted in ("其他应付款", "长期应付款", "due to a related party"):
            assert not any(re.search(v, wanted, re.IGNORECASE) for v in vetoes), (part, wanted)


# --- and how the parent takes it ----------------------------------------------------------------

@pytest.mark.parametrize("part,parent", sorted(PARTS.items()))
def test_the_parent_is_a_derived_parent_whose_note_rung_computes_one_required_term(
        shipped, part, parent):
    """The NOTE rung is this part's, and it is the rung the mainland filings resolve.

    A SECOND RUNG NOW SITS BENEATH IT and the assertion names the note rung rather than the whole
    cascade. `mapping._computed_parent` forbids a `derived` column from matching a caption, so
    these two columns could not read their own printed row: measured, China SCE 1966's
    "Due to related parties 應付關聯方款項" 2,588,416 / 2,583,308 was swept to
    `bs_cl__other_current_liabilities` and 嘉民's "Loan from ultimate holding company" 36,800 plus
    "Loans from controlling shareholder" 544,254 / 544,665 to
    `bs_ncl__other_non_current_liabilities` — each filing's only related-party balance, in the
    wrong column. The aliases therefore live on FACE parts and the cascade falls back to them, in
    the shape `is_pl__sales_revenues` already uses (P1 the note, P2 the face).
    """
    p = shipped[parent]

    assert p.type == "derived"
    note_rungs = [r for r in p.cascade if r.id != "FROM_THE_FACE"]
    assert [t.ref for rung in note_rungs for t in rung.terms] == [part]
    assert all(t.role == "required" for rung in note_rungs for t in rung.terms)
    assert all(rung.refuse_negative for rung in p.cascade)
    # …AND THE FACE RUNG IS LAST, so it can never displace a note reading that already works.
    assert [r.id for r in p.cascade][-1] == "FROM_THE_FACE"
    face = p.cascade[-1]
    assert not face.outranks_printed
    assert all(t.ref.startswith("sub__rp_face_") and t.sign == 1 for t in face.terms)


@pytest.mark.parametrize("parent", sorted(set(PARTS.values())))
def test_the_parent_carries_no_recognition(shipped, parent):
    """`mapping._computed_parent` locks a derived parent out of the matcher, so an alias left on
    one reads as intent that cannot be honoured. What these two carried was the template's own
    compound label, which matched nothing on any of the five filings."""
    p = shipped[parent]

    assert not p.aliases
    assert not p.keyword_hints
