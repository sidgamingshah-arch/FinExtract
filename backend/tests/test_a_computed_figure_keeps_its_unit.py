"""Two defects that cost a printed related-party balance its column, and a column its figures.

THE FIRST IS A UNIT. `stages.note_sourced._write` created a fresh `ExtractedValue` without a
`unit_ctx`, so a figure a cascade computed took `UnitContext`'s default — no currency, scale 1 —
while every sibling on a filing that declares a scale carries RMB'000. `structural_checks` then
refuses the whole relation as `mixed_scale` rather than comparing thousands to units, so the
figure was right and the section became unverifiable. Measured on China SCE 1966: giving
`bs_cl__due_to_related_parties_cp` a face rung published its printed 2,588,416 / 2,583,308 and
cost the filing `bs_cl__total_current_liabilities`, a relation that had tied EXACTLY at
131,532,808 and 123,650,889. The unit now travels with the figure — from the rung's own inputs,
or failing that from the other column of the row it is written onto.

THE SECOND IS A COLUMN. `_detect_matrix` trims an edge cluster standing off on its own, because
"a note column, or label digits that happened to line up" is not a component column. The test is
2.5x the median pitch, and a CAS equity statement defeats it: 其他权益工具 prints as three
sub-columns — 优先股 / 永续债 / 其他 — between 实收资本 and 资本公积, and a parent-company statement
uses none of them, so nothing clusters in that gap. On 澜起科技 688008 page index 161 the gap came
to 0.2239 against a limit of 0.2193 — OVER BY 0.0048 OF PAGE WIDTH, about 3.4pt on A4 — and the
real share-capital column was popped. `value_left` fell to 0.4064, the share-capital figure drawn
at x 0.1931-0.2679 was read as CAPTION TEXT, and the statement published
'一、上年年末余额 1,138,740,286.00', '（二）所有者投入 6,048,987.00' and three more: five figures that
never became values, in captions that then matched no vocabulary and so defeated the tail splice
as well.

WHAT SEPARATES THE TWO CASES, measured over every page of the corpus that reaches the matrix
reader. SUPPORT: a component column carries a figure on every data row, and every genuine stray
the trim drops is partial — 1966 page 266 at 4/7, 嘉民 pages 115/151/152 at 5/14 and 8/12, 688008
page 100 at 4/6 — while the two clusters that should survive are 3/3 and 4/4. Support alone is not
enough, because a YEAR can be on every row: 1966's senior-notes note (page index 220) ends all ten
of its captions with one, and that cluster is 10/10. So the second condition is that the cluster's
members are AMOUNTS — carrying a thousands separator or a decimal point — which `_is_money_like`,
asking only for digits, cannot tell.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.models.enums import Basis
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, UnitContext
from app.services.row_reconstruct import Word, _amount_shaped, _detect_matrix, _group_rows
from app.stages.note_sourced import _row_unit, _unit_of_terms, _write

THOUSANDS = UnitContext(currency="CNY", scale_factor=Decimal(1000))


# ── the unit travels with the figure ──────────────────────────────────────────────────────────

def _row(key: str, *slots: tuple[str, str, str], unit: UnitContext | None = None) -> LineItem:
    li = LineItem(source_label=key, canonical_key=key)
    for basis, period, amount in slots:
        li.set_value(ExtractedValue(
            basis=Basis(basis), period_label=period,
            value=Decimal(amount), value_raw=Decimal(amount),
            **({"unit_ctx": unit} if unit is not None else {})))
    return li


def test_a_computed_figure_takes_the_unit_of_the_inputs_it_was_computed_from():
    """The 1966 shape: the face PART carries RMB'000 and the derived column is written fresh."""
    part = _row("sub__rp_face_due_to_cp", ("consolidated", "current", "2588416"), unit=THOUSANDS)
    parent = LineItem(source_label="Due to Related Parties(CP)",
                      canonical_key="bs_cl__due_to_related_parties_cp")
    unit = _unit_of_terms([{"ref": "sub__rp_face_due_to_cp", "sign": 1}],
                          {"sub__rp_face_due_to_cp": part}, "consolidated", "current")
    assert unit is THOUSANDS
    _write(parent, "consolidated", "current", Decimal("2588416"), unit_ctx=unit)
    got = next(iter(parent.values.values()))
    assert got.value == Decimal("2588416")
    assert got.unit_ctx.scale_factor == Decimal(1000)
    assert got.unit_ctx.currency == "CNY"


def test_without_the_fix_a_fresh_slot_defaults_to_no_unit_at_all():
    """The defect, stated as behaviour so nobody reinstates it: `UnitContext`'s default is scale
    1 and no currency, which is what made the section `mixed_scale`."""
    parent = LineItem(source_label="x", canonical_key="bs_cl__due_to_related_parties_cp")
    _write(parent, "consolidated", "current", Decimal("2588416"))
    got = next(iter(parent.values.values()))
    assert got.unit_ctx.scale_factor == Decimal(1) and got.unit_ctx.currency == ""


def test_the_other_column_of_the_same_row_is_the_fallback_unit():
    """A parent filled for one period and then the other: the row's own unit is the better guess
    than the default, and it needs no inputs to find."""
    parent = _row("bs_cl__due_to_related_parties_cp",
                  ("consolidated", "current", "2588416"), unit=THOUSANDS)
    assert _row_unit(parent) is THOUSANDS
    _write(parent, "consolidated", "prior", Decimal("2583308"))
    prior = next(ev for ev in parent.values.values() if ev.period_label == "prior")
    assert prior.unit_ctx.scale_factor == Decimal(1000)


def test_a_unitless_input_is_not_offered_as_a_unit():
    """`_unit_of_terms` answers None rather than handing back a default dressed up as a finding —
    otherwise the fallback below it never runs."""
    part = _row("sub__x", ("consolidated", "current", "1"))
    assert _unit_of_terms([{"ref": "sub__x"}], {"sub__x": part}, "consolidated", "current") is None
    assert _unit_of_terms([{"ref": "absent"}], {}, "consolidated", "current") is None
    assert _unit_of_terms(None, {}, "consolidated", "current") is None


# ── an amount is not a year ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("token", ["1,138,740,286.00", "6,048,987.00", "-338,193,337.80",
                                   "(2,200,000)", "13,613,559", "0.3837", "7.375", "-284,303.92"])
def test_a_printed_amount_is_amount_shaped(token):
    assert _amount_shaped([token])


@pytest.mark.parametrize("token", ["2024", "2023", "2026", "220", "162", "256", "38", "5"])
def test_a_bare_integer_is_not_amount_shaped(token):
    """A YEAR is the cluster the trim exists to drop and `_is_money_like` accepts it: 1966's
    senior-notes note prints one at the end of all ten of its captions."""
    assert not _amount_shaped([token])


def test_one_bare_integer_disqualifies_the_whole_cluster():
    assert not _amount_shaped(["1,138,740,286.00", "2024"])
    assert not _amount_shaped([])


# ── the trim keeps a column and still drops a stray ───────────────────────────────────────────

ROW_H = 0.016


def _matrix_words(edges_and_values: list[tuple[float, list[str | None]]]) -> list[Word]:
    """A matrix whose columns are given as (right edge, one token per data row)."""
    words: list[Word] = []
    rows = max(len(v) for _e, v in edges_and_values)
    for r in range(rows):
        y = 0.20 + r * 0.05
        words.append(Word(text=f"row{r}", bbox=BBox(x0=0.05, y0=y, x1=0.12, y1=y + ROW_H)))
        for edge, vals in edges_and_values:
            tok = vals[r] if r < len(vals) else None
            if tok is None:
                continue
            words.append(Word(text=tok, bbox=BBox(x0=edge - 0.008 * len(tok), y0=y,
                                                  x1=edge, y1=y + ROW_H)))
    return words


def _bands(words: list[Word]):
    m = _detect_matrix(_group_rows(words, 0.012), None)
    return None if m is None else m.bands


AMOUNTS = ["1,138,740,286.00", "5,432,387,416.86", "3,478,053,735.01"]


def test_a_fully_supported_cluster_of_amounts_is_kept_however_wide_the_gap():
    """688008 page index 161 to scale: 实收资本 at 0.2679, then a gap where the three unused
    其他权益工具 sub-columns would be, then the evenly pitched run."""
    bands = _bands(_matrix_words([
        (0.2679, AMOUNTS), (0.4918, AMOUNTS), (0.5715, AMOUNTS),
        (0.7390, AMOUNTS), (0.8222, AMOUNTS), (0.9099, AMOUNTS)]))
    assert bands is not None
    assert len(bands) == 6, bands
    assert bands[0][1] == pytest.approx(0.2679, abs=1e-3)


def test_a_partially_supported_cluster_is_still_dropped():
    """Every genuine stray this trim removes is partial — a date fragment in a label or an inline
    note reference appears on some rows, not all."""
    bands = _bands(_matrix_words([
        (0.1538, ["1,000.00", None, None]), (0.5667, AMOUNTS), (0.6429, AMOUNTS),
        (0.7191, AMOUNTS), (0.7952, AMOUNTS), (0.8714, AMOUNTS)]))
    assert bands is not None
    assert len(bands) == 5, bands
    assert bands[0][1] == pytest.approx(0.5667, abs=1e-3)


def test_a_fully_supported_cluster_of_years_is_dropped():
    """1966's senior-notes note: the year at the end of every caption clusters on all ten rows,
    so support alone would keep it as a column."""
    bands = _bands(_matrix_words([
        (0.1912, ["2024", "2023", "2025"]), (0.4714, AMOUNTS), (0.5381, AMOUNTS),
        (0.6048, AMOUNTS), (0.7381, AMOUNTS), (0.8047, AMOUNTS), (0.8714, AMOUNTS)]))
    assert bands is not None
    assert len(bands) == 6, bands
    assert bands[0][1] == pytest.approx(0.4714, abs=1e-3)
