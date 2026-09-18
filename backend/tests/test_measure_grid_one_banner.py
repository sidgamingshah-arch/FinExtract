"""A PRC note that states its period ONCE and divides its columns by measure.

    期末余额                                     <- one banner over the whole table
    项目    | 账面余额 | 坏账准备 | 账面价值        <- three measures

`_prc_period_row` used to require exactly two period captions — the reported period and its
comparative, one each — so this header was not a grid at all and the three measure columns fell
back to positional labels `current` / `prior` / `col2`. On 河钢股份 000709 note 6(2) that put the
GROSS in `current`, the bad-debt PROVISION in `prior` and the net carrying amount in a slot nothing
reads, and the comparative (printed as the same table on the next page under 期初余额) repeated it
— which is how `bs_ca__other_receivables_cp` came to publish 2,407,734,161.14 against a printed net
of 683,092,791.26.

Three things had to change together and each is pinned here, because any one of them alone leaves
the page reading exactly as it did.
"""
from __future__ import annotations

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

LINE_H = 0.012
PITCH_Y = 0.024
# Copied from the filing: three columns ~0.21 apart, captions set left of their right-aligned
# figures by a sixth of that pitch. The offsets are what the measure band's tolerance is measured
# against, so inventing tidier ones would test a layout the defect never had.
COL_FIG = [0.4562, 0.6516, 0.8735]
COL_CAP = [0.3959, 0.6023, 0.8111]


def _w(text: str, xc: float, y0: float, width: float = 0.06) -> Word:
    return Word(text=text, bbox=BBox(x0=xc - width / 2, y0=y0,
                                     x1=xc + width / 2, y1=y0 + LINE_H))


def _table(banner: str, measures: tuple[str, ...], rows: list[tuple[str, tuple[str, ...]]],
           *, extra: list[Word] | None = None) -> list[Word]:
    words = [_w(banner, 0.6046, 0.10)]
    words.append(_w("项目", 0.1831, 0.10 + PITCH_Y * 0.5))
    for c, cap in enumerate(measures):
        words.append(_w(cap, COL_CAP[c], 0.10 + PITCH_Y))
    y = 0.10 + PITCH_Y * 2
    for caption, cells in rows:
        words.append(_w(caption, 0.12, y))
        for c, text in enumerate(cells):
            words.append(_w(text, COL_FIG[c], y, width=0.094))
        y += PITCH_Y
    return words + (extra or [])


def _build(words: list[Word]):
    logs: list[str] = []
    items, _ = build_line_items(words, page_index=132, document_id="d", source_kind="native",
                                on_face=False, log=logs.append)
    return items, logs


GROSS_ALLOWANCE_NET = ("账面余额", "坏账准备", "账面价值")
NOTE_6_2 = [
    ("其他应收款", ("871,232,076.76", "188,139,285.50", "683,092,791.26")),
    ("合计", ("871,232,076.76", "188,139,285.50", "683,092,791.26")),
]


def test_one_period_banner_over_three_measures_is_a_grid():
    items, logs = _build(_table("期末余额", GROSS_ALLOWANCE_NET, NOTE_6_2))
    assert any("column_grid=period_x_measure(期末余额/账面余额,期末余额/坏账准备,"
               "期末余额/账面价值)" in m for m in logs), logs
    row = next(li for li in items if li.source_label == "其他应收款")
    got = {ev.period_label: str(ev.value) for ev in row.values.values()}
    assert got == {"current": "871232076.76",
                   "current:allowance": "188139285.50",
                   "current:net": "683092791.26"}


def test_the_comparative_table_on_the_next_page_is_the_prior_period():
    """The same three columns under 期初余额. Printed as a SEPARATE table, so nothing but its own
    banner says it is last year — and with the banner unread both tables claimed `current`.

    Two rows, as the filing prints them (the item and its 合计): one grid row cannot establish a
    column, and `_value_column_bands` still refuses to let it — see that function.
    """
    items, _ = _build(_table("期初余额", GROSS_ALLOWANCE_NET, [
        ("其他应收款", ("853,409,293.12", "164,675,215.09", "688,734,078.03")),
        ("合计", ("853,409,293.12", "164,675,215.09", "688,734,078.03")),
    ]))
    row = next(li for li in items if li.source_label == "其他应收款")
    got = {ev.period_label: str(ev.value) for ev in row.values.values()}
    assert got == {"prior": "853409293.12",
                   "prior:allowance": "164675215.09",
                   "prior:net": "688734078.03"}


def test_the_net_carrying_amount_is_not_the_primary_measure():
    """账面余额 keeps the bare period label even with 账面价值 printed beside it.

    A part that reads the primary and deducts 坏账准备 from it — `sub__rp_find_3`, and
    `sub__cp_other_receivables_gross` feeding CP_P2 — would otherwise deduct the allowance from a
    figure already net of it, and be wrong by the allowance twice over.
    """
    items, _ = _build(_table("期末余额", GROSS_ALLOWANCE_NET, NOTE_6_2))
    row = next(li for li in items if li.source_label == "其他应收款")
    bare = next(ev for ev in row.values.values() if ev.period_label == "current")
    assert str(bare.value) == "871232076.76"


def test_a_movement_schedule_on_one_row_is_still_not_two_periods():
    """期初余额 | 本期增加 | 本期减少 is FOUR captions once 期末余额 is counted — three of them
    contain 本期/期末 and would land in the reported slot together. The relaxation accepts ONE
    caption or a clean two-slot partition, and neither describes this row, so it keeps the
    positional reading its printed order is the only meaning of."""
    words = [_w(cap, COL_CAP[c] if c < 3 else 0.95, 0.10)
             for c, cap in enumerate(("期初余额", "本期增加", "本期减少"))]
    words.append(_w("项目", 0.1831, 0.10))
    y = 0.10 + PITCH_Y
    for caption, cells in [("坏账准备", ("100.00", "20.00", "5.00")),
                           ("合计", ("100.00", "20.00", "5.00"))]:
        words.append(_w(caption, 0.12, y))
        for c, text in enumerate(cells):
            words.append(_w(text, COL_FIG[c], y, width=0.094))
        y += PITCH_Y
    items, logs = _build(words)
    assert not any("column_grid=period_x_measure" in m for m in logs), logs
    row = next(li for li in items if li.source_label == "坏账准备")
    # No column was relabelled as a MEASURE of another period, which is the whole exposure: the
    # positional labels this keeps are meaningless-but-harmless, and a measure suffix invented from
    # a row that states four movements would file an increase as last year's balance.
    assert not any(":" in (ev.period_label or "") for ev in row.values.values())


def test_a_two_row_table_still_refuses_a_stray_figure_as_a_column():
    """The column minimum was relaxed for a table shorter than it — note 6(2) has two valued rows,
    so all three of its clusters held two members and all three were refused. The relaxation is
    gated on the clusters AGREEING with the widest row: a FOURTH cluster as well supported as the
    real three is a column the widest row's geometry does not explain, so the bar stays at
    `_COL_MIN_ROWS` and nothing — the stray included — becomes a column.

    A ONE-OFF figure needs no such gate and does not get one: it forms a cluster of one, which is
    below the relaxed bar too, so the grid is read and the stray is left out of it. That case is
    what `test_one_period_banner_over_three_measures_is_a_grid` already covers.
    """
    stray: list[Word] = []
    for i in range(2):
        y = 0.10 + PITCH_Y * (5 + i)
        stray += [_w("注：", 0.12, y), _w("12.00", 0.55, y, width=0.03)]
    items, logs = _build(_table("期末余额", GROSS_ALLOWANCE_NET, NOTE_6_2, extra=stray))
    row = next(li for li in items if li.source_label == "其他应收款")
    labels = {ev.period_label for ev in row.values.values()}
    assert not any(":" in (lbl or "") for lbl in labels), logs
