"""`bs_ca__other_receivables_cp`'s net input, read the two ways a filing prints it.

A note states a net carrying amount either as a ROW of its own (账面价值) — where the row's own
reported amount IS the net — or as a COLUMN of a 账面余额 | 坏账准备 | 账面价值 grid, where the row
is captioned by the item and its reported amount is the GROSS. `note_source.measure` selects one
column and holds one value, so the two readings are two cascade rungs.

MEASURED ON 河钢股份 000709, which needs BOTH: its consolidated note 6(2) prints the grid and its
parent-company note 18(2) prints a plain comparative. Reading the grid's row as a net amount is how
the line came to publish 2,407,734,161.14 against a printed 683,092,791.26 — a part matching the
caption 其他应收款 added the net, this year's gross and last year's gross together.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

from app.schemas.line_items import load_line_item_set
from app.services.line_items import evaluate

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


def _defs():
    cfg = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    return {d.key: d for d in cfg.items}


def test_the_rows_reported_amount_is_preferred_and_the_column_is_the_fallback():
    """ON A GRID THE ROW READING CANNOT ANSWER: it declares `from_measure_grid: false`, so the
    grid's PRIMARY column — the GROSS, 871,232,076.76 against a net of 683,092,791.26 on 000709 —
    is refused to it, and only the net column is left. Both readings present therefore means the
    note printed a plain comparative AND a grid, where the plain row's reported amount is the net;
    it goes first so a misread grid (000709's parent-company p201) never displaces it."""
    got = evaluate(_defs()["sub__cp_other_receivables_net"], {
        "sub__cp_other_receivables_net_col": Decimal("683092791.26"),
        "sub__cp_other_receivables_net_row": Decimal("683092791.26"),
    })
    assert got.rung_used == "NET_REPORTED"
    got = evaluate(_defs()["sub__cp_other_receivables_net"], {
        "sub__cp_other_receivables_net_col": Decimal("683092791.26")})
    assert got.value == Decimal("683092791.26") and got.rung_used == "NET_COLUMN"

def test_the_rows_reported_amount_answers_when_no_net_column_was_printed():
    """A one-level table states the carrying amount and has no allowance column beside it to
    deduct — 000709's parent-company note, where this is the only figure there is."""
    got = evaluate(_defs()["sub__cp_other_receivables_net"], {
        "sub__cp_other_receivables_net_row": Decimal("28589918178.13"),
    })
    assert got.value == Decimal("28589918178.13")
    assert got.rung_used == "NET_REPORTED"


def test_neither_reading_leaves_the_line_empty_rather_than_guessing():
    got = evaluate(_defs()["sub__cp_other_receivables_net"], {})
    assert got.value is None


def test_the_two_net_readings_differ_only_in_which_COLUMN_they_take():
    """Same note titles, same captions, same vetoes — two readings of one ROW, not two searches. A
    difference anywhere else would make the rung order a choice between different rows, which is
    not what it is for.

    TWO FIELDS SELECT A COLUMN, not one. `measure` names a suffixed column and
    `from_measure_grid` says whether the page had a two-level header at all, which is the only way
    to ask about the PRIMARY column — it has no suffix. The column reading asks for `:net`, which
    exists only on a grid, so it needs no second condition; the row reading has to say `False`,
    because the primary of a grid is a GROSS and it must not take one.
    """
    defs = _defs()
    col = defs["sub__cp_other_receivables_net_col"].note_source
    row = defs["sub__cp_other_receivables_net_row"].note_source
    assert (col.measure, col.from_measure_grid) == ("net", None)
    assert (row.measure, row.from_measure_grid) == ("", False)
    picks = {"measure", "from_measure_grid"}
    assert col.model_dump(exclude=picks) == row.model_dump(exclude=picks)


def test_a_grid_that_prints_no_net_column_falls_to_the_face_rather_than_the_gross():
    """000709's related-party note prints 账面余额 | 坏账准备 and no net column. The gross and
    allowance parts that computed a net from it (CP_P2) went with the simplification: none of them
    decided a figure on the five filings. What must not come back is the gross published as the
    net — so with neither net reading, CP_P1 (which REQUIRES the net) does not resolve and the
    printed row answers."""
    defs = _defs()
    assert "sub__cp_other_receivables_gross" not in defs
    assert [r.id for r in defs["bs_ca__other_receivables_cp"].cascade] == ["CP_P1", "FROM_THE_FACE"]
    got = evaluate(defs["bs_ca__other_receivables_cp"], {
        "sub__cp_interest_and_dividends_receivable": Decimal("40000000"),
        "sub__cp_face_other_receivables": Decimal("1287570989.98")})
    assert got.rung_used == "FROM_THE_FACE" and got.value == Decimal("1287570989.98")

# ── the definition named a component nothing read ─────────────────────────────────────────────

def test_interest_and_dividends_receivable_are_a_component_of_the_line():
    """THE MISMATCH THIS CLOSED. `bs_ca__other_receivables_cp`'s definition says the line covers
    "interest and dividends receivable and other debtors", and no term of either rung read them.

    A filing that breaks them out has NOT put them in its other-receivables figure. 河钢股份
    000709's note lists 应收利息, 应收股利 and 其他应收款 as three siblings adding to one 合计, so
    the net of the third alone was short by the second — 683,092,791.26 published against a
    balance-sheet 其他应收款 of 913,899,591.26, a gap of exactly the 230,806,800.00 of 应收股利.
    With the component read, all four of that filing's columns equal the note's own 合计, and so
    does 澜起科技 688008's parent-company column (40,000,000.00 + 1,247,570,989.98).
    """
    defs = _defs()
    line = defs["bs_ca__other_receivables_cp"]
    assert "dividends receivable" in line.definition
    # EVERY RUNG THAT BUILDS THE LINE FROM NOTE COMPONENTS, which is every rung but
    # `FROM_THE_FACE`. That one reads the balance sheet's own 其他应收款 row, and this test's own
    # arithmetic is the proof it must NOT add the component: 683,092,791.26 + 230,806,800.00 is
    # 913,899,591.26, and 913,899,591.26 is the printed figure. The face row already contains the
    # interest and dividends — 000709 prints them as a 其中 breakdown BENEATH it — so reading them
    # into the face rung would count the 应收股利 twice. An exact partition rather than a filter,
    # so a note rung silently dropping the component still fails here.
    note_rungs = [r for r in line.cascade if r.id != "FROM_THE_FACE"]
    assert {r.id for r in line.cascade} - {r.id for r in note_rungs} == {"FROM_THE_FACE"}
    assert note_rungs
    for rung in note_rungs:
        legs = [(t.ref, t.role, t.sign) for t in rung.terms]
        assert ("sub__cp_interest_and_dividends_receivable", "any_of", 1) in legs, rung.id
    face = next(r for r in line.cascade if r.id == "FROM_THE_FACE")
    assert [(t.ref, t.role) for t in face.terms] == [
        ("sub__cp_face_other_receivables", "required")]
    # `any_of`, so a note that folds them into its other-receivables row — which identifies no
    # such component — still resolves the rung on whatever it did identify.
    got = evaluate(line, {"sub__cp_other_receivables_net": Decimal("683092791.26")})
    assert got.value == Decimal("683092791.26")
    got = evaluate(line, {"sub__cp_other_receivables_net": Decimal("683092791.26"),
                          "sub__cp_interest_and_dividends_receivable": Decimal("230806800.00")})
    assert got.value == Decimal("913899591.26")


def test_the_component_reads_a_row_and_not_a_sentence():
    """It is a tabulated row — 应收股利 with a figure beside it — so it declares `note_tables`. The
    six functional depreciation splits are the set's only `prose` lines and this is not one."""
    part = _defs()["sub__cp_interest_and_dividends_receivable"]
    assert part.route == "note_tables"
    assert not part.note_source.prose_subject


# ── a 账面余额 | 坏账准备 grid, which is the shape CP_P2 exists for ─────────────────────────────

def test_the_row_reading_refuses_the_grids_primary_column():
    """A grid states its GROSS in the primary column (no measure suffix), and a plain comparative
    states its reported amount there too; `from_measure_grid` is what tells them apart."""
    defs = _defs()
    assert defs["sub__cp_other_receivables_net_row"].note_source.from_measure_grid is False

def test_the_allowance_readings_went_with_the_gross():
    """With no gross part there is nothing to deduct an allowance from: the net is read directly,
    as a row or as the grid's net column."""
    defs = _defs()
    for key in ("sub__cp_other_receivables_loss_allowance", "sub__cp_other_receivables_loss_allowance_col",
                "sub__cp_other_receivables_loss_allowance_row"):
        assert key not in defs, key

def test_the_primary_of_a_grid_and_of_a_plain_comparative_are_different_quantities():
    """The whole reason the flag is a flag. 账面余额 stays the PRIMARY measure — a part that deducts
    坏账准备 from the primary (`sub__rp_find_3`) depends on that and moving the gross to a suffix
    would break it — so what separates the two readings is not the column's name but whether the
    page had a two-level header at all."""
    defs = _defs()
    assert defs["sub__rp_find_3_gross"].note_source.measure == ""
    assert defs["sub__rp_find_3_gross"].note_source.from_measure_grid is None, (
        "Find 3 must keep reading the primary however the page was headed")
