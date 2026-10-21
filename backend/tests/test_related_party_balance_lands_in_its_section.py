"""A related-party balance moved out of a face line lands on the related-party line of the SAME
section, and what leaves equals what arrives, so the printed section total is unchanged.

Measured on 中山公用 000685 (CAS, consolidated) before this rule: the related-party note files
38,748,507.73 / 92,901,866.49 under 其他应收款, Other Receivables (CP) deducted it, and nothing
received it — Due from Related Parties (CP) was a face-only line — so total current assets and total
assets fell short of print by exactly those amounts, while the whole 应收项目 table, 179,535,189.14 /
131,422,923.71, was published as a NON-CURRENT balance.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.line_items import evaluate

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

FACE = Decimal("86128419.63")        # 000685's printed 其他应收款, current year
RP = Decimal("38748507.73")          # the related-party note's 其他应收款 balances


@pytest.fixture(scope="module")
def defs():
    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {d.key: d for d in cfg.items}


@pytest.mark.parametrize("parts", [
    # the note rung answers (000685 reads its other-receivables note)
    {"sub__cp_other_receivables_net": FACE, "sub__cp_face_other_receivables": FACE},
    # only the face row is read
    {"sub__cp_face_other_receivables": FACE},
])
def test_what_leaves_other_receivables_arrives_on_the_current_related_party_line(defs, parts):
    inputs = dict(parts, sub__cp_other_receivables_rp=RP)
    other = evaluate(defs["bs_ca__other_receivables_cp"], inputs)
    due_from = evaluate(defs["bs_ca__due_from_related_parties_cp"], inputs)

    assert other.value == FACE - RP
    assert due_from.value == RP
    # the section total reads both lines, so it is the printed face figure again
    assert other.value + due_from.value == FACE


def test_the_moved_balance_is_counted_in_the_section_total(defs):
    """NOT carved_from_face: it left a face line, so the current-assets total counts it only here."""
    line = defs["bs_ca__due_from_related_parties_cp"]
    assert str(line.type) == "derived"
    assert all(not r.carved_from_face for r in line.cascade)


def test_a_printed_current_related_party_row_adds_to_what_moved(defs):
    line = defs["bs_ca__due_from_related_parties_cp"]
    printed = Decimal("4065231")
    assert evaluate(line, {"sub__rp_face_due_from_cp": printed}).value == printed
    assert evaluate(line, {"sub__rp_face_due_from_cp": printed,
                           "sub__cp_other_receivables_rp": RP}).value == printed + RP
    assert evaluate(line, {}).value is None


def test_no_current_balance_feeds_the_non_current_line(defs):
    """Find 2 and Find 3 feed Due from Related Parties (LTP); a current receivable's row is not
    theirs, and the portion of a long-term receivable due within one year is current too."""
    import re

    for key in ("sub__rp_find_3_gross", "sub__rp_find_3_allowance", "sub__rp_find_2"):
        ns = defs[key].note_source
        counts = [re.compile(p, re.IGNORECASE) for p in ns.row_caption_any]
        vetoes = [re.compile(p, re.IGNORECASE) for p in ns.row_caption_none]

        def taken(caption):
            return any(p.search(caption) for p in counts) and not any(
                v.search(caption) for v in vetoes)

        if key != "sub__rp_find_2":
            assert taken("长期应收款"), key
            for current in ("应收账款", "其他应收款", "一年内到期的长期应收款",
                            "Trade receivables", "Other receivables"):
                assert not taken(current), (key, current)
    assert "其他应收款" not in defs["sub__rp_find_2"].note_source.note_terms


def test_every_received_figure_leaves_the_line_it_belongs_to(defs):
    """What arrives equals what leaves: a `received` term is deducted, on every rung, by the line
    that is its configured parent — so no section total moves when it is moved."""
    received = [(d.key, t.ref) for d in defs.values() for r in d.cascade or ()
                for t in r.terms if t.received]
    assert ("bs_ca__due_from_related_parties_cp", "sub__cp_other_receivables_rp") in received
    for receiver, ref in received:
        source = defs[defs[ref].parent]
        assert source.key != receiver
        assert str(source.inherits) == str(defs[receiver].inherits), "the same section"
        for rung in source.cascade:
            assert any(t.ref == ref and t.sign == -1 for t in rung.terms), (source.key, rung.id)


def test_the_receiving_line_counts_the_received_row_as_its_child(defs):
    """`stages.note_sourced` evaluates a cascade only in the columns its children fill. The
    received part's configured parent is Other Receivables (CP), so without the declaration Due
    from Related Parties (CP) was never evaluated on a filing printing no related-party row."""
    from app.stages.note_sourced import _children_by_parent

    kids = _children_by_parent(list(defs.values()))
    assert "sub__cp_other_receivables_rp" in kids["bs_ca__due_from_related_parties_cp"]
    assert "sub__cp_other_receivables_rp" in kids["bs_ca__other_receivables_cp"]


def test_a_received_term_arrives_with_a_plus_sign():
    from app.schemas.line_items import Term

    with pytest.raises(ValueError):
        Term(ref="x", sign=-1, received=True)
