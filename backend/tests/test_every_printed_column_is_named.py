"""A note table is passed on with EVERY column it prints named — the blank ones included.

`row_reconstruct.read_printed_columns` reads a table's header first, as printed, and the figures
are then placed into it by containment. Measured before: every fair-value hierarchy table on the
five reference filings reached the model with NO headings, because the blank Level 1 / Level 2
columns produced no figure band and their headings ended the climb; and nearest-band assignment,
unvetoed, would have named 300319's Level 2 figure Level 3.

ANNOTATION ONLY. The figure bands — and so every positional key and value — are unchanged; each test
on real geometry below pins the keys alongside the new headings.

The real-geometry sections are in `fixtures/printed_columns_sections.json`: each one's words exactly
as the reader received them at ffd01b5 (text, x0, y0, x1, y1, page-normalised).
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

from app.core.models.geometry import BBox
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import (
    HeaderModel,
    Word,
    _headings_over_their_figures,
    _Placed,
    build_line_items,
    read_printed_columns,
)

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures/printed_columns_sections.json")
                 .read_text(encoding="utf-8"))


def _real(key: str) -> list[Word]:
    return [Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d)) for t, a, b, c, d in FIX[key]["words"]]


def _build(words, *, carry=None, out=None, year=None, page=0):
    items, _ = build_line_items(words, page_index=page, document_id="t", source_kind="native",
                                on_face=False, header_carry=carry, header_out=out,
                                reporting_year=year)
    return items


def _row(items, caption: str):
    return next(li for li in items if li.source_label.startswith(caption))


def _cells(li) -> dict[str, tuple[Decimal, str | None, str | None]]:
    return {ev.period_label: (ev.value, ev.column_heading, ev.column_period)
            for ev in li.values.values()}


# ── synthetic layouts ────────────────────────────────────────────────────────────────────────────

H = 0.011


def _w(text, x0, x1, y):
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _cas_fair_value(level_figures: bool = False):
    """The CAS fair-value layout: 期末公允价值 over four leaves wrapped onto two lines, a row banner
    and a nil-only row between the header and the figures, and figures under Level 2 and 合计."""
    rows = [
        [_w("单位：元", 0.83, 0.89, 0.10)],
        [_w("期末公允价值", 0.54, 0.63, 0.12)],
        [_w("项目", 0.16, 0.19, 0.14), _w("第一层次公允价值计", 0.27, 0.41, 0.14),
         _w("第二层次公允价值计", 0.43, 0.57, 0.14), _w("第三层次公允价值计", 0.59, 0.73, 0.14),
         _w("合计", 0.81, 0.84, 0.145)],
        [_w("量", 0.33, 0.35, 0.155), _w("量", 0.49, 0.51, 0.155), _w("量", 0.65, 0.67, 0.155)],
        [_w("一、持续的公允价值计量", 0.10, 0.26, 0.18)],
        [_w("--", 0.33, 0.35, 0.195), _w("--", 0.49, 0.51, 0.195), _w("--", 0.65, 0.67, 0.195),
         _w("--", 0.81, 0.83, 0.195)],
    ]
    y = 0.22
    for caption in ("（一）交易性金融资产", "（八）应收款项融资", "（三）其他权益工具投资"):
        rows.append([_w(caption, 0.10, 0.24, y), _w("431,478,888.81", 0.47, 0.57, y),
                     _w("431,478,888.81", 0.79, 0.90, y)])
        y += 0.02
    return [w for row in rows for w in row]


def test_every_printed_leaf_is_named_and_a_blank_one_takes_no_figure():
    items = _build(_cas_fair_value())
    li = _row(items, "（一）交易性金融资产")
    assert li.printed_columns == ["期末公允价值 · 第一层次公允价值计量", "期末公允价值 · 第二层次公允价值计量",
                                  "期末公允价值 · 第三层次公允价值计量", "期末公允价值 · 合计"]
    assert li.column_groups == ["期末公允价值"]
    cells = _cells(li)
    # the positional keys are what they always were; the Level 2 figure is named Level 2
    assert set(cells) == {"current", "prior"}
    assert cells["current"][1] == "期末公允价值 · 第二层次公允价值计量"
    assert cells["prior"][1] == "期末公允价值 · 合计"
    # the period the header states over both: 期末
    assert cells["current"][2] == cells["prior"][2] == "current"


def test_a_header_whose_cells_cannot_hold_the_figures_one_to_one_names_nothing():
    """Two figure bands inside one printed column's cell: not this table's header."""
    rows = [
        [_w("2024", 0.48, 0.52, 0.10), _w("2023", 0.78, 0.82, 0.10)],
        [_w("Cost", 0.10, 0.15, 0.13), _w("1,000", 0.45, 0.50, 0.13), _w("2,000", 0.56, 0.61, 0.13)],
        [_w("Depreciation", 0.10, 0.20, 0.15), _w("3,000", 0.45, 0.50, 0.15),
         _w("4,000", 0.56, 0.61, 0.15)],
        [_w("Net", 0.10, 0.13, 0.17), _w("5,000", 0.45, 0.50, 0.17), _w("6,000", 0.56, 0.61, 0.17)],
    ]
    items = _build([w for r in rows for w in r])
    li = _row(items, "Cost")
    assert li.printed_columns == []
    assert {ev.period_label for ev in li.values.values()} == {"current", "prior"}


def test_a_heading_not_standing_over_its_own_figures_is_dropped():
    """Where no header model holds, a nearest-band heading is kept only over its own figures —
    300319's Level 3 heading sits within half a pitch of the Level 2 figures' band."""
    figs = [_Placed(row=0, col=0, word=_w("431,478,888.81", 0.466, 0.572, 0.2)),
            _Placed(row=0, col=1, word=_w("431,478,888.81", 0.790, 0.896, 0.2))]
    kept = _headings_over_their_figures({0: "第三层次", 1: "合计"},
                                        {0: (0.594, 0.730), 1: (0.809, 0.839)}, figs)
    assert kept == {1: "合计"}


def test_a_dated_heading_states_its_period_against_the_newest_year():
    rows = [
        [_w("2024", 0.48, 0.52, 0.10), _w("2023", 0.78, 0.82, 0.10)],
        *[[_w(c, 0.10, 0.20, y), _w("1,000", 0.44, 0.52, y), _w("5,000", 0.74, 0.82, y)]
          for c, y in (("Cash", 0.13), ("Loans", 0.15), ("Total", 0.17))],
    ]
    li = _row(_build([w for r in rows for w in r]), "Cash")
    assert {k: v[2] for k, v in _cells(li).items()} == {"current": "current", "prior": "prior"}
    # one printed year alone is placed only against the filing's own year
    lone = [
        [_w("2022", 0.78, 0.82, 0.10)],
        *[[_w(c, 0.10, 0.20, y), _w("5,000", 0.74, 0.82, y)]
          for c, y in (("Cash", 0.13), ("Loans", 0.15), ("Total", 0.17))],
    ]
    words = [w for r in lone for w in r]
    assert {ev.column_period for ev in _row(_build(words), "Cash").values.values()} == {None}
    assert {ev.column_period for ev in _row(_build(words, year=2023), "Cash").values.values()} \
        == {"prior"}


def test_the_filings_own_year_is_read_off_its_statements_current_column():
    from types import SimpleNamespace

    from app.services.row_reconstruct import face_reporting_year

    def item(*values):
        return SimpleNamespace(values={str(i): SimpleNamespace(
            period_label=label, column_index=None, period_display=display)
            for i, (label, display) in enumerate(values)})
    items = [item(("current", "2024年12月31日"), ("prior", "2023年12月31日")),
             item(("current", "31 December 2024"), ("prior", "31 December 2023")),
             item(("current", "本期发生额 成本"))]
    assert face_reporting_year(items) == 2024
    assert face_reporting_year([]) is None


def test_a_year_inside_a_heading_that_names_something_else_is_no_period():
    model = read_printed_columns([
        [_w("Senior notes due 2024", 0.40, 0.55, 0.10), _w("Bonds due 2026", 0.70, 0.82, 0.10)],
        [_w("Principal", 0.10, 0.18, 0.13), _w("1,000", 0.44, 0.52, 0.13), _w("2,000", 0.74, 0.82, 0.13)],
    ], reporting_year=2024)
    assert model is not None and [c.period for c in model.columns] == [None, None]


def test_a_continuation_with_blank_rows_above_its_figures_reads_no_header():
    """The pin `column_headings` keeps (test_column_headings_reach_the_model): a statement prints
    its blank lines, so on a continuation page there is no header above them to read."""
    rows = [[_w("河钢股份有限公司2024", 0.52, 0.68, 0.05), _w("年年度报告全文", 0.70, 0.90, 0.05)],
            [_w("汇兑收益（损失以“-”号填列）", 0.10, 0.30, 0.09)],
            [_w("净敞口套期收益", 0.10, 0.20, 0.11)],
            [_w("Cost", 0.10, 0.15, 0.13), _w("1,000", 0.46, 0.54, 0.13), _w("2,000", 0.61, 0.69, 0.13)]]
    assert read_printed_columns(rows) is None


# ── the carry: header-less sections of the same table ───────────────────────────────────────────

def _fv_model() -> HeaderModel:
    out: list = []
    _build(_cas_fair_value(), out=out)
    assert isinstance(out[0], HeaderModel)
    return out[0]


def _headless(x_level2: tuple[float, float], y0: float = 0.10):
    rows = []
    for k, caption in enumerate(("其变动计入当期损益", "（4）其他", "（八）应收款项融资")):
        y = y0 + 0.02 * k
        rows.append([_w(caption, 0.10, 0.24, y), _w("118,949,379.33", *x_level2, y),
                     _w("118,949,379.33", 0.79, 0.90, y)])
    return [w for r in rows for w in r]


def test_a_headerless_section_takes_the_carried_header_when_its_figures_fit_it():
    model = _fv_model()
    out: list = []
    li = _row(_build(_headless((0.466, 0.572)), carry=model, out=out), "（4）其他")
    assert out == [model]
    assert li.printed_columns == model.headings
    assert _cells(li)["current"][1] == "期末公允价值 · 第二层次公允价值计量"


def test_never_inherited_silently_figures_that_do_not_fit_are_left_unnamed():
    # a continuation whose first column of figures runs across the carried Level 2 | Level 3
    # boundary (0.581): one band, two cells — a straddle, and the carry is refused
    rows = []
    for k, (caption, x0) in enumerate((("其变动计入当期损益", 0.51), ("（4）其他", 0.535),
                                       ("（八）应收款项融资", 0.555))):
        y = 0.10 + 0.02 * k
        rows += [_w(caption, 0.10, 0.24, y), _w("118,949,379.33", x0, x0 + 0.08, y),
                 _w("118,949,379.33", 0.79, 0.90, y)]
    out: list = []
    li = _row(_build(rows, carry=_fv_model(), out=out), "（4）其他")
    assert out == [None]
    assert li.printed_columns == [] and _cells(li)["current"][1] is None


def test_a_carried_header_holds_only_where_the_table_printed_its_figures():
    """Another table of the same note whose figures happen to fall inside the carried cells, but
    not on the edges the table's own figures stood on, is not the same table (000709's
    related-party note: a settlement table's header reached a trust-income table that way)."""
    out: list = []
    li = _row(_build(_headless((0.44, 0.50)), carry=_fv_model(), out=out), "（4）其他")
    assert out == [None]
    assert li.printed_columns == [] and _cells(li)["current"][1] is None


def test_a_section_that_prints_its_own_header_never_borrows_one():
    own = [[_w("2024", 0.48, 0.52, 0.08), _w("2023", 0.78, 0.82, 0.08)]]
    words = [w for r in own for w in r] + _headless((0.44, 0.52), y0=0.11)
    li = _row(_build(words, carry=_fv_model()), "（4）其他")
    assert li.printed_columns == ["2024", "2023"]


def test_a_section_with_no_figures_names_nothing_and_passes_the_carry_on():
    model = _fv_model()
    out: list = []
    prose = [_w("本公司第三层次公允价值计量项目是其他权益工具投资", 0.10, 0.60, 0.10)]
    assert _build(prose, carry=model, out=out) == []
    assert out == [model]


def test_the_header_is_carried_and_reset_with_the_grid_across_pages():
    """Stated on the caller, as `test_note_group_spans_a_page` states the sub-heading's carry: what
    the next page inherits is the last section's header, and a non-notes page clears it."""
    import inspect

    from app.services import pdf_extract
    extract = inspect.getsource(pdf_extract.extract_pdf)
    assert "carry_header=carry_header, header_out=headers" in extract
    assert "headers[-1] if headers else None" in extract
    # reset on the same break as the note, the grid and the sub-heading
    assert "notes_group = None\n        notes_header = None" in extract


def test_the_note_walk_offers_a_header_only_to_a_later_section_of_the_same_note():
    """300319's 十二、1 is cut by the wrapped row caption "1.以公允价值计量且…": the rows below it are
    a new section with no header. The same note's header is offered to it; another note's is not."""
    def page(second_note: str):
        words = [_w("1、以公允价值计量的资产和负债的期末公允价值", 0.10, 0.50, 0.05)]
        words += [w for w in _cas_fair_value()]
        words += [_w(f"{second_note}以公允价值计量且", 0.10, 0.26, 0.30)]
        words += _headless((0.466, 0.572), y0=0.32)
        return extract_note_tables(words, page_index=0, document_id="t", source_kind="native")
    same = page("1.")
    assert [t.note_number for t in same][-1] == "1"
    tail = same[-1].items
    assert tail and all(ni.printed_columns == same[0].items[0].printed_columns for ni in tail)
    assert {ev.column_heading for ni in tail for ev in ni.values.values()} == {
        "期末公允价值 · 第二层次公允价值计量", "期末公允价值 · 合计"}
    other = page("2.")
    assert other[-1].note_number == "2"
    assert all(ni.printed_columns == [] for ni in other[-1].items)


# ── real geometry ────────────────────────────────────────────────────────────────────────────────

def test_000709_fair_value_levels_are_printed_and_the_figures_stand_under_level_3_and_the_total():
    """河钢股份 000709 page 197, 十三、1: 期末公允价值 over 第一层次 | 第二层次 | 第三层次 | 合计,
    the figures under the last two, a row banner between header and figures."""
    items = _build(_real("000709_fv"))
    li = _row(items, "（一）应收款项融资")
    pc = li.printed_columns
    assert len(pc) == 4 and all(p.startswith("期末公允价值 · ") for p in pc)
    assert [p.split(" · ")[1] for p in pc] == ["第一层次公允价值计量", "第二层次公允价值计量",
                                               "第三层次公允价值计量", "合计"]
    assert li.column_groups == ["期末公允价值"]
    assert _cells(li) == {
        "current": (Decimal("909834646.75"), "期末公允价值 · 第三层次公允价值计量", "current"),
        "prior": (Decimal("909834646.75"), "期末公允价值 · 合计", "current")}
    assert _cells(_row(items, "（二）其他权益工具投资"))["current"][:2] == (
        Decimal("411683468.98"), "期末公允价值 · 第三层次公允价值计量")


def test_300319_figures_stand_under_level_2_not_level_3():
    """迈捷 300319 page 176, 十二、1: the populated column is LEVEL 2 (figures 0.466–0.572 under
    第二层次 at 0.432–0.568); 第三层次 is blank. The header is in the section above the rows."""
    out: list = []
    head = _build(_real("300319_fv_head"), out=out)
    model = out[0]
    assert model is not None and len(model.columns) == 4
    first = _row(head, "（一）交易性金融资")
    assert _cells(first)["current"][1] == "期末公允价值 · 第二层次公允价值计量"
    body = _build(_real("300319_fv_body"), carry=model)
    for caption, amount in (("（4）其他", "431478888.81"), ("（八）应收款项融资", "118949379.33"),
                            ("持续以公允价值计量", "593668126.80")):
        cells = _cells(_row(body, caption))
        assert cells["current"] == (Decimal(amount), "期末公允价值 · 第二层次公允价值计量", "current")
        assert cells["prior"] == (Decimal(amount), "期末公允价值 · 合计", "current")
        assert "第三层次" not in (cells["current"][1] or "")
    # …and without the carried header, the body is left unnamed rather than guessed
    alone = _build(_real("300319_fv_body"))
    assert all(ev.column_heading is None for li in alone for ev in li.values.values())


def test_1966_fair_value_level_1_and_level_3_bands_and_the_block_periods():
    """China SCE 1966 page 254, note 46: two blocks (2023, 2022), the figure bands Level 1 and
    Level 3 (Total has two figures and is no band), and the period stated by each block."""
    tables = extract_note_tables(_real("1966_fv"), page_index=254, document_id="t",
                                 source_kind="native",
                                 carry_note=("46", "FAIR VALUE AND FAIR VALUE HIERARCHY"))
    rows = [ni for t in tables for ni in t.items if ni.raw_label.startswith("Financial assets")]
    assert [ni.period_hint for ni in rows] == ["current", "prior"]
    assert rows[0].printed_columns == [
        "Quoted prices in active market (Level 1) 於活躍市場報價（第一級）",
        "Significant observable inputs (Level 2) 重大可觀察輸入數據（第二級）",
        "Significant unobservable inputs (Level 3) 重大不可觀察輸入數據（第三級）",
        "Total 總計"]
    level1, level3 = rows[0].printed_columns[0], rows[0].printed_columns[2]
    assert _cells(rows[0]) == {"prior": (Decimal("344135"), level3, None)}
    assert _cells(rows[1]) == {"current": (Decimal("53434"), level1, None),
                               "prior": (Decimal("378539"), level3, None)}


def test_kaming_fair_value_then_its_three_levels():
    """嘉民 page 147, note 30(e)(i): Fair value | Level 1 | Level 2 | Level 3, the group over the
    last three, figures under Fair value and Level 2; units "$’000 千元" are not a heading."""
    items = _build(_real("kaming_fv"))
    li = _row(items, "Financial assets at FVTPL")
    assert li.printed_columns == ["Fair value 公平值", "Level 1 第一級", "Level 2 第二級",
                                  "Level 3 第三級"]
    assert li.column_groups == ["Fair value measurement categorised into", "公平值計量分類為"]
    assert _cells(li) == {"current": (Decimal("9956"), "Fair value 公平值", None),
                          "prior": (Decimal("9956"), "Level 2 第二級", None)}


def test_1966_property_note_keeps_its_nine_columns_and_keys():
    """1966 page 193, note 14: nine columns read correctly before, read the same now — with the
    page's folio beside the Total column no longer able to veto them."""
    items = _build(_real("1966_ppe"))
    li = _row(items, "Cost")
    assert li.printed_columns == [
        "Land and building 土地及樓宇", "Leasehold improvements 租賃物業裝修",
        "Furniture, fixtures and office equipments 傢俬、 裝置及辦公室設備",
        "Transportation equipment 運輸工具", "Subtotal 小計", "Leasehold land 租賃土地",
        "Office buildings 樓宇", "Subtotal 小計", "Total 總計"]
    keys = ["current", "prior", *(f"col{k}" for k in range(2, 9))]
    cells = _cells(li)
    assert list(cells) == keys
    assert [cells[k][1] for k in keys] == li.printed_columns
    assert cells["current"][0] == Decimal("352833") and cells["col8"][0] == Decimal("1333857")


def test_000709_related_party_grid_keys_are_unchanged_and_named_period_first():
    """000709 page 195, 十二、5 应收项目: the period × measure grid keys stay as they were."""
    li = _row(_build(_real("000709_rp_grid")), "唐山时创高温材料")
    assert _cells(li) == {
        "current": (Decimal("1007351.91"), "期末余额 · 账面余额", "current"),
        "current:allowance": (Decimal("33089.73"), "期末余额 · 坏账准备", "current"),
        "prior": (Decimal("753779.54"), "期初余额 · 账面余额", "prior"),
        "prior:allowance": (Decimal("39436.49"), "期初余额 · 坏账准备", "prior")}


def test_688008_a_wholly_blank_closing_group_is_still_printed():
    """688008 page 207, 七、30 其他非流动资产: every 期末 column is blank, and the 期初 figures keep
    their grid keys."""
    li = _row(_build(_real("688008_blank_closing")), "预付工程款")
    assert li.printed_columns == ["期末余额 · 账面余额", "期末余额 · 减值准备", "期末余额 · 账面价值",
                                  "期初余额 · 账面余额", "期初余额 · 减值准备", "期初余额 · 账面价值"]
    assert _cells(li) == {
        "prior": (Decimal("6202564.86"), "期初余额 · 账面余额", "prior"),
        "prior:net": (Decimal("6202564.86"), "期初余额 · 账面价值", "prior")}
