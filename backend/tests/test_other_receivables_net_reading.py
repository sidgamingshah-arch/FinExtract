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


def test_the_net_column_is_preferred_over_the_rows_reported_amount():
    """Both present is the grid case: the row's reported amount is the gross, so taking it would
    publish 871,232,076.76 where the filing prints 683,092,791.26."""
    got = evaluate(_defs()["sub__cp_other_receivables_net"], {
        "sub__cp_other_receivables_net_col": Decimal("683092791.26"),
        "sub__cp_other_receivables_net_row": Decimal("871232076.76"),
    })
    assert got.value == Decimal("683092791.26")
    assert got.rung_used == "NET_COLUMN"


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


def test_only_the_column_part_asks_for_the_net_measure():
    """The two parts differ in `measure` and in nothing else: same note titles, same captions, same
    vetoes. A difference anywhere else would make them two searches rather than two readings of one
    row, and the rung order would then be choosing between different rows."""
    defs = _defs()
    col = defs["sub__cp_other_receivables_net_col"].note_source
    row = defs["sub__cp_other_receivables_net_row"].note_source
    assert col.measure == "net"
    assert row.measure == ""
    assert col.model_dump(exclude={"measure"}) == row.model_dump(exclude={"measure"})


def test_the_gross_and_allowance_parts_are_untouched_by_this():
    """CP_P2 is `gross - allowance`, and 账面余额 keeps the bare period label precisely so that
    subtraction stays valid when a 账面价值 column is printed beside it.

    BOTH READ THE PRIMARY MEASURE, which is a boundary worth pinning rather than a thing to fix
    here. They find their figures by CAPTION — a row reading 账面余额, a row reading 坏账准备 — so on
    a filing whose gross and allowance are COLUMNS of one row captioned 其他应收款 neither matches,
    and CP_P2 cannot fire. That costs nothing on this corpus, because a filing printing that grid
    prints the 账面价值 column with it and CP_P1 answers from the net directly. Giving the allowance
    part `measure: "allowance"` would trade the caption reading for the column one, not add it —
    the same two-readings problem the rungs above exist for — and no filing here exercises it.
    """
    defs = _defs()
    assert defs["sub__cp_other_receivables_gross"].note_source.measure == ""
    assert defs["sub__cp_other_receivables_loss_allowance"].note_source.measure == ""


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
    for rung in line.cascade:
        legs = [(t.ref, t.role, t.sign) for t in rung.terms]
        assert ("sub__cp_interest_and_dividends_receivable", "any_of", 1) in legs, rung.id
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
