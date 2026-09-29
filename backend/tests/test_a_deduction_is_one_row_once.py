"""ONE PRINTED ROW IS ONE QUANTITY ON THE DEDUCTION SIDE TOO — and a face part states the face.

NEW FILE -> backend/tests/test_a_deduction_is_one_row_once.py

THE PUZZLE. On China SCE 1966, with the model route answering, `bs_ca__secur_and_other_fincl_
assets_cp` published 0 where the face prints 344,135 under CURRENT ASSETS. Note 26's single Total
row was cited by three `any_of` note totals — added ONCE, by the same-row guard — and by three
adjustments, the non-current split and the derivatives and other receivables "disclosed inside
those notes" — subtracted THREE TIMES, because the guard covered the base alone. The rung came to
344,135 - 3 x 344,135, `refuse_negative` declined it, and the cascade fell to CP_ZERO.

THREE CHANGES, and the first obvious one was measured and rejected:

1. ONE ROW IS DEDUCTED ONCE, however many adjustments name it.
2. A DEDUCTION THAT IS THE BASE'S OWN ROW REFUSES THE RUNG. Every adjustment here is a PART the
   note discloses inside its total; the row that states the total is not one of its parts, so a
   citation resolving to it says the inputs were not what the rung assumed — the reading
   `refuse_negative` gives a rung below zero. DROPPING the deduction instead was tried first and
   published the contradiction: 1966's LTP_P2 put 344,135 in NON-current assets, and other
   receivables' CP_P1 published twice its face figure.
3. A FACE PART IS NOT DECOMPOSED BY ITS NOTE, and a note-only line takes no split component. With
   the rung refused the cascade reaches FROM_THE_FACE — which found nothing, because the split
   pass had filed the face row's 344,135 into `sub__fa_cp_fvtoci_note_total`, a NOTE part, and
   un-filed the face part. Plus the HKFRS caption "Financial assets at fair value through profit or
   loss" on the current-assets face part, which knew only the CAS 交易性金融资产.

MEASURED, LLM route (spy): 1966 CP 0 -> 344,135 / 431,973, the printed figures, via FROM_THE_FACE,
and section_reconciliation:bs_ca now passes; 688008 CP 4,879,868,511.58 -> 1,783,494,750.68 and
other receivables 0 -> 4,143,856.36, both as printed; 300319 CP 758,738,201.46 -> 431,478,888.81 as
printed, and bs_nca now reconciles; kaming CP 940,525 -> 0, correct — its FVTPL is printed under
NON-current assets. Deterministic route: only 1966 moves, CP absent -> 344,135 / 431,973 and other
current assets back to the prepayments line alone. No structural relation gets worse anywhere.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas.line_items import load_line_item_set
from app.services.line_items import _apply_terms, evaluate
from app.stages.map_ontology import route_fenced_decompositions

_SEED = Path(__file__).resolve().parent.parent / "app/sample/templates/output_csv_hk_line_items.json"
TOTAL = ("p214", "Total", (0.8, 0.5), "344135")          # note 26's Total row
OTHER = ("p214", "Unlisted investments", (0.8, 0.4), "344135")   # same amount, a different row


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(scope="module")
def by_key(shipped):
    return {i.key: i for i in shipped.items}


def _t(ref, role, sign=1):
    return SimpleNamespace(ref=ref, const=None, sign=sign, abs=False, role=role)


# ── the arithmetic ────────────────────────────────────────────────────────────────────────────

def test_two_deductions_reading_one_row_deduct_it_once() -> None:
    terms = [_t("base", "required"), _t("a", "adjustment", -1), _t("b", "adjustment", -1)]
    known = {"base": Decimal("1000"), "a": Decimal("100"), "b": Decimal("100")}
    row = ("p9", "其中：衍生", (0.7, 0.3), "100")

    got = _apply_terms(terms, known, sources={"base": TOTAL, "a": row, "b": row})

    assert got.value == Decimal("900")
    assert [e.get("duplicate_of_row") is not None for e in got.inputs] == [False, False, True]


def test_a_deduction_that_is_the_bases_own_row_contradicts_the_rung() -> None:
    terms = [_t("total", "any_of"), _t("noncurrent", "adjustment", -1)]
    known = {"total": Decimal("344135"), "noncurrent": Decimal("344135")}

    got = _apply_terms(terms, known, sources={"total": TOTAL, "noncurrent": TOTAL})

    assert got.value is None and got.contradiction, got
    assert any(e.get("part_is_the_totals_own_row") for e in got.inputs)


def test_order_does_not_decide_it() -> None:
    """The deduction may be declared ahead of the base term whose row it repeats."""
    terms = [_t("noncurrent", "adjustment", -1), _t("total", "any_of")]
    known = {"total": Decimal("344135"), "noncurrent": Decimal("344135")}

    assert _apply_terms(terms, known, sources={"total": TOTAL, "noncurrent": TOTAL}).contradiction


def test_a_different_row_with_the_same_amount_still_deducts() -> None:
    """The case where zero IS the answer: a note holding nothing but the deducted class."""
    terms = [_t("total", "any_of"), _t("derivatives", "adjustment", -1)]
    known = {"total": Decimal("344135"), "derivatives": Decimal("344135")}

    got = _apply_terms(terms, known, sources={"total": TOTAL, "derivatives": OTHER})

    assert got.value == Decimal("0") and not got.contradiction


def test_without_sources_the_arithmetic_is_what_it_was() -> None:
    terms = [_t("total", "any_of"), _t("a", "adjustment", -1), _t("b", "adjustment", -1)]
    known = {"total": Decimal("10"), "a": Decimal("3"), "b": Decimal("3")}

    assert _apply_terms(terms, known).value == Decimal("4")


# ── through the shipped cascade: 1966's column ────────────────────────────────────────────────

def test_1966s_securities_column_reaches_the_face_rung(by_key) -> None:
    """Note 26's Total cited for three Find 1 parts AND for Level 3: the Level 3 deduction resolves
    to the row the base already reads, so CP_INTERMEDIATE is refused for the contradiction. The
    overshoot residual is refused for the same reason, so CP_ZERO does not fire either, and
    FROM_THE_FACE reads the printed face figure."""
    cp = by_key["bs_ca__secur_and_other_fincl_assets_cp"]
    note_keys = ["sub__fa_cp_fvtpl_note_total", "sub__fa_cp_fvtoci_note_total",
                 "sub__fa_cp_other_fincl_assets_note_total", "sub__fa_cp_level_3_total"]
    known = {k: Decimal("344135") for k in note_keys}
    known["sub__cp_face_trading_fincl_assets"] = Decimal("344135")
    sources = {k: TOTAL for k in note_keys}
    sources["sub__cp_face_trading_fincl_assets"] = ("p103", "Financial assets at fair value "
                                                    "through profit or loss", (0.8, 0.6), "344135")

    residual = evaluate(by_key["sub__fa_cp_intermediate_residual"], known, sources)
    assert residual.value is None, residual
    got = evaluate(cp, known, sources)

    assert got.value == Decimal("344135"), got
    assert got.rung_used == "FROM_THE_FACE"
    assert any(r.startswith("CP_INTERMEDIATE:") for r in got.refused_rungs), got.refused_rungs


# ── the split fence ───────────────────────────────────────────────────────────────────────────

def test_a_face_part_is_not_decomposed_by_its_note(by_key) -> None:
    kept, declined = route_fenced_decompositions(
        [("sub__cp_face_trading_fincl_assets", ["sub__fa_cp_fvtoci_note_total"], "bs_ca")], by_key)

    assert kept == [] and declined == ["sub__cp_face_trading_fincl_assets"]


def test_a_template_face_line_still_splits(by_key) -> None:
    """The first version fenced every `route: face` line and stopped every split in the corpus."""
    decl = ("bs_ca__other_current_assets", ["bs_ca__prepayments_cp"], "bs_ca")

    kept, declined = route_fenced_decompositions([decl], by_key)

    assert kept == [decl] and declined == []


def test_a_note_only_line_takes_no_split_component(by_key) -> None:
    route = by_key["sub__fa_cp_fvtoci_note_total"].route
    assert route in ("note_tables", "prose", None) and route != "face"

    kept, _ = route_fenced_decompositions(
        [("bs_ca__other_current_assets",
          ["sub__fa_cp_fvtoci_note_total", "bs_ca__prepayments_cp"], "bs_ca")], by_key)

    assert kept == [("bs_ca__other_current_assets", ["bs_ca__prepayments_cp"], "bs_ca")]


def test_a_run_with_no_line_item_set_is_unfenced() -> None:
    decl = ("anything", ["a", "b"], "bs_ca")
    assert route_fenced_decompositions([decl], {}) == ([decl], [])


# ── the caption ───────────────────────────────────────────────────────────────────────────────

def test_the_current_securities_face_part_reads_the_hkfrs_caption(by_key) -> None:
    part = by_key["sub__cp_face_trading_fincl_assets"]
    assert "Financial assets at fair value through profit or loss" in part.aliases
    assert "按公允值計量且其變動計入損益的金融資產" in part.aliases
    # CURRENT ASSETS ONLY — kaming prints its FVTPL under non-current assets, and that must not bind.
    assert list(part.section_scope) == ["bs_ca"]
