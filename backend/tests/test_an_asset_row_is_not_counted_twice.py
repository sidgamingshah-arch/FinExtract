"""A printed asset row belongs to one column, and the residual sweep must not add it to a second.

TWO INDEPENDENT DOUBLE COUNTS, both on the asset side of a mainland balance sheet, both measured
on 澜起科技 688008 where Total Current Assets published 11,271,578,867.08 against a printed
9,461,304,025.38 and Total Non-Current Assets 3,355,824,527.72 against a printed 2,757,607,361.00.

1. THE RESIDUAL SWEEP. `bs_ca__secur_and_other_fincl_assets_cp`, `bs_ca__other_receivables_cp` and
   `bs_nca__secur_and_other_fincl_assets_ltp` are all `type: derived`, and `mapping._computed_parent`
   makes every derived key unmatchable by caption — deliberately, so a column cannot hold both a
   cascade and its own face aliases. The printed rows 交易性金融资产, 其他应收款 and 其他非流动金融资产
   therefore reached no column at all and `stages.residual` filed each on its section's catch-all:
   `bs_ca__other_current_assets` and `bs_nca__other_non_current_assets`. Those catch-alls are
   SIBLINGS of the columns the note cascades publish on, and both are `op: "sum"` children of the
   section total, so the same money was added to the subtotal twice. The remedy is the one
   `sub__face_principal_revenue` and `sub__rp_face_due_to_cp` already use: the aliases live on a
   `route: "face"` PART, which gives the printed row a home and the parent a last-resort rung.

2. TWO NOTE PARTS CLAIMING ONE NOTE. `sub__ltp_fincl_assets_note_total`'s definition says it reads
   "a note whose heading is SIMPLY financial assets ... under that BARE heading", yet its
   `note_title_any` carried a second pattern matching 其他非流动金融资产 — the caption
   `sub__ltp_other_fincl_assets_note_total`'s definition explicitly claims. Both are `any_of` terms
   of the SAME rung, so the note's total was summed once per part: 688008's LTP column published
   1,150,487,851.94, exactly 2 x the printed 575,243,925.97, in both periods.

3. AND A CURRENT/NON-CURRENT MISCLASSIFICATION, which was a double count too. 其他权益工具投资 is
   printed under 非流动资产 on every A-share balance sheet, and `sub__fa_cp_fvtoci_note_total` — whose
   own definition limits it to "the holdings it classifies as CURRENT assets" — claimed it. On
   688008 that put 22,636,234.66 (the PRIOR figure, at that) on the current securities column while
   the row itself sat in the non-current catch-all; on 河钢股份 000709 it put 411,683,468.98 there
   while `bs_nca__secur_and_other_fincl_assets_ltp` published the same money, so that filing counted
   it THREE times.

WHAT THIS IS NOT. None of the three is a template change: the three new keys all start `sub__`, so
they are PARTS, and `test_template_provisions_line_items` still pins the template split at 462.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.schemas.line_items import load_line_item_set

_SEED = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
         / "output_csv_hk_line_items.json")

FACE_PARTS = {
    "sub__cp_face_trading_fincl_assets": (
        "bs_ca__secur_and_other_fincl_assets_cp", "bs_ca", ("交易性金融资产", "交易性金融資產")),
    "sub__cp_face_other_receivables": (
        "bs_ca__other_receivables_cp", "bs_ca", ("其他应收款", "其他應收款")),
    "sub__ltp_face_other_non_current_fincl_assets": (
        "bs_nca__secur_and_other_fincl_assets_ltp", "bs_nca",
        ("其他非流动金融资产", "其他非流動金融資產")),
    # CAS 其他权益工具投资: read off the face because its note prints the movement as columns.
    "sub__ltp_face_other_equity_instrument_investments": (
        "bs_nca__secur_and_other_fincl_assets_ltp", "bs_nca", ("其他权益工具投资", "其他權益工具投資")),
}


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def by_key(shipped):
    _SHIPPED.update({i.key: i for i in shipped.items})
    return {i.key: i for i in shipped.items}


# --- 1. the face parts, and that they are parts -------------------------------------------------

@pytest.mark.parametrize("key", sorted(FACE_PARTS))
def test_each_swept_caption_now_has_a_part_to_land_on(key, by_key):
    parent, section, aliases = FACE_PARTS[key]
    part = by_key[key]

    assert part.parent == parent
    assert part.route == "face"
    assert part.type == "extracted"
    assert part.namespace == "internal"
    assert part.in_output is False, "a part must never claim a printed column"
    for alias in aliases:
        assert alias in (part.aliases_i18n.get("zh") or []), alias


@pytest.mark.parametrize("key", sorted(FACE_PARTS))
def test_each_part_is_gated_to_the_balance_sheet_and_its_section(key, by_key):
    """The condition `test_line_item_gate` sets for a part that carries aliases: a caption can bind
    it only where a balance sheet is being read, not from any statement at large."""
    _parent, section, _aliases = FACE_PARTS[key]
    part = by_key[key]

    assert part.statement == "balance_sheet"
    assert part.section_scope == [section]
    assert part.inherits == section


@pytest.mark.parametrize("key", sorted(FACE_PARTS))
def test_each_part_is_wired_into_its_parents_last_rung(key, by_key):
    """A part nothing reads would be WORSE than the double count: the printed row would leave the
    catch-all and land nowhere, so the money would be lost instead of counted twice."""
    parent_key, _section, _aliases = FACE_PARTS[key]
    parent = by_key[parent_key]

    face = [r for r in parent.cascade if r.id == "FROM_THE_FACE"]
    assert len(face) == 1, [r.id for r in parent.cascade]
    # `required` where the part is the rung's only reading; `any_of` where the face prints the
    # line as several rows (Securities (LTP): 其他非流动金融资产 and 其他权益工具投资).
    terms = {t.ref: (t.role, t.sign) for t in face[0].terms}
    assert terms.get(key) in (("required", 1), ("any_of", 1)), terms
    assert len(terms) == 1 or all(role == "any_of" for role, _ in terms.values()), terms


def test_an_overshoot_floors_at_zero_before_the_face_is_tried(by_key):
    """The rule for Securities (CP): "if Find 1 − Find 3 is negative, make this field zero". So
    CP_ZERO comes BEFORE the face. It is no longer a constant that always resolves — it resolves
    only when the overshoot residual does — so the face rung after it still answers a filing whose
    notes give none of Find 1."""
    cp = by_key["bs_ca__secur_and_other_fincl_assets_cp"]
    ids = [r.id for r in cp.cascade]

    assert ids.index("CP_ZERO") < ids.index("FROM_THE_FACE")
    zero = next(r for r in cp.cascade if r.id == "CP_ZERO")
    assert any(t.ref == "sub__fa_cp_intermediate_residual" and t.role == "required"
               for t in zero.terms), "CP_ZERO would fire on every filing and hide the face"


@pytest.mark.parametrize("key", sorted(FACE_PARTS))
def test_the_face_rung_never_outranks_the_figure_it_is(key, by_key):
    """It reads the printed row, so it must not displace a printed figure — and it is LAST among
    the rungs that can answer, so it can never displace a note reading that already works."""
    parent_key, _section, _aliases = FACE_PARTS[key]
    parent = by_key[parent_key]
    face = next(r for r in parent.cascade if r.id == "FROM_THE_FACE")

    assert face.outranks_printed is False
    answering = [r.id for r in parent.cascade if r.id != "CP_ZERO"]
    assert answering[-1] == "FROM_THE_FACE", answering


# --- 2. one note, one claimant ------------------------------------------------------------------

_SHIPPED: dict = {}


def _claims(item, caption: str) -> bool:
    """Whether this part's `note_title_any` would claim a note under this heading.

    `re.IGNORECASE`, imported from the module that does the real matching rather than repeated
    here, so a test asserting about a claim cannot disagree with the claim the engine makes.
    """
    from app.services.note_sourced import _FLAGS

    from app.services.line_item_notes import by_meaning_only, heading_covered

    src = item.note_source
    if src is None:
        return False
    # A part that finds its notes BY MEANING claims a heading its `note_terms` cover — and where
    # SIBLINGS cover the same heading, the reader gives it to one of them (`claimed_notes`), so
    # the claim asked about here is that one, not the bare coverage.
    if by_meaning_only(item):
        if not heading_covered(item, caption):
            return False
        from types import SimpleNamespace as NS

        from app.services.line_item_notes import claimed_notes
        siblings = [i for i in _SHIPPED.values()
                    if by_meaning_only(i) and i.parent == item.parent]
        note = NS(title=caption, note_number="1", items=[])
        return "1" in claimed_notes(siblings, [note]).get(item.key, set())
    return any(re.search(p, caption, _FLAGS) for p in (src.note_title_any or []))


def test_only_one_note_part_claims_the_other_non_current_financial_assets_note(by_key):
    """The two that collided, named. Both were `any_of` terms of the same rung, so the note's total
    was added once per claimant and 688008's column published exactly twice the printed figure."""
    owner = by_key["sub__ltp_other_fincl_assets_note_total"]
    bare = by_key["sub__ltp_fincl_assets_note_total"]

    assert _claims(owner, "其他非流动金融资产"), "the part whose definition claims it must claim it"
    assert not _claims(bare, "其他非流动金融资产"), (
        "the BARE-heading part must not claim 其他非流动金融资产; its own definition says it reads "
        "a note headed simply 金融资产 / 非流动金融资产")
    # It still reads the non-current bare headings it exists for. A heading of just 金融资产 /
    # "Financial assets" is NOT a meaning term: as a line-item sum it would claim 交易性金融资产 and
    # every other financial-asset note, so it is left to the model's note search.
    assert _claims(bare, "非流动金融资产")
    assert _claims(bare, "Non-current financial assets")
    assert not _claims(bare, "交易性金融资产")


def test_no_two_any_of_terms_of_one_rung_claim_the_same_caption(by_key):
    """The GENERAL form of the defect, over every cascade in the shipped set.

    `any_of` sums whichever terms resolve, so two parts that can both claim one note heading are a
    double count waiting for a filing that prints it. Asserted over the captions this corpus
    actually prints rather than over all possible strings, which is what makes it a measurement.
    """
    captions = ["其他非流动金融资产", "其他非流動金融資產", "交易性金融资产", "其他权益工具投资",
                "其他应收款", "其他流动资产", "应收账款", "长期股权投资", "其他非流动资产"]
    offenders = []
    for item in by_key.values():
        for rung in item.cascade or ():
            refs = [t.ref for t in rung.terms if t.role == "any_of" and t.ref]
            for caption in captions:
                claiming = [r for r in refs if r in by_key and _claims(by_key[r], caption)]
                if len(claiming) > 1:
                    offenders.append((item.key, rung.id, caption, claiming))
    assert not offenders, offenders


# --- 3. a non-current caption is not read as current --------------------------------------------

def test_the_current_fvtoci_part_does_not_claim_a_non_current_caption(by_key):
    """其他权益工具投资 is printed under 非流动资产 on every A-share balance sheet, and the CURRENT
    part's own definition limits it to "the holdings it classifies as current assets"."""
    current = by_key["sub__fa_cp_fvtoci_note_total"]

    assert not _claims(current, "其他权益工具投资")
    # The English/HKEX headings it exists for are untouched — this is a narrowing, and the HKEX
    # filings in the corpus move no figure because of it.
    assert _claims(current, "financial assets at fair value through other comprehensive income")
    assert _claims(current, "Financial assets at FVTOCI")


def test_the_non_current_twin_still_claims_it(by_key):
    """So the narrowing above moves the caption rather than orphaning it: on a CAS filing
    其他权益工具投资 is read off the FACE by the LTP line's own part, because the note prints its
    movement as columns and was misread on two of three filings."""
    part = by_key["sub__ltp_face_other_equity_instrument_investments"]
    assert "其他权益工具投资" in (part.aliases or [])
    ltp = by_key["bs_nca__secur_and_other_fincl_assets_ltp"]
    assert all(any(t.ref == part.key for t in r.terms) for r in ltp.cascade), [r.id for r in ltp.cascade]
