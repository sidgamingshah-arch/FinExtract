"""A table reaches the model with its columns named, and a statement with its entity.

Measured before: China SCE 1966's property note — nine columns — went to the model as `current`,
`prior`, `col2` … `col8`, the first two named as two years and the printed headings only in the
prose; 000709's group and company balance sheets arrived as one block, 货币资金 31.8bn and 24.5bn
side by side with nothing to say which was the group's; and an HKEX "Group | Company" row put both
`current` figures under one key, the company's overwriting the group's.

Display only. The figure keys stay positional — every deterministic reader keys on them — so
nothing here can move a published figure.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem
from app.services import face_context
from app.services.row_reconstruct import Word, column_headings

H = 0.011


def _w(text, x, y, width=None):
    width = width if width is not None else 0.008 * max(len(text), 2)
    return Word(text=text, bbox=BBox(x0=x - width / 2, y0=y, x1=x + width / 2, y1=y + H))


BANDS = [0.50, 0.65, 0.80]


def _figures(y):
    return [_w("Cost", 0.15, y), *(_w(f"{n:,}", x, y) for n, x in zip((1000, 2000, 3000), BANDS))]


def test_stacked_bilingual_headings_a_column_group_and_a_units_line():
    rows = [
        [_w("14. PROPERTY AND EQUIPMENT", 0.20, 0.10, 0.25)],
        [_w("Property and equipment", 0.575, 0.13, 0.17)],          # over the first two columns
        [_w("Land and", 0.50, 0.16), _w("Leasehold", 0.65, 0.16), _w("Total", 0.80, 0.16)],
        [_w("building", 0.50, 0.175), _w("improvements", 0.65, 0.175)],
        [_w("土地及樓宇", 0.50, 0.19), _w("物業裝修", 0.65, 0.19), _w("總計", 0.80, 0.19)],
        [_w("RMB’000", x, 0.205) for x in BANDS],
        [_w("At 1 January 2023:", 0.15, 0.22, 0.14)],
        _figures(0.24),
    ]
    headings, groups = column_headings(rows, BANDS)
    assert headings == {0: "Land and building 土地及樓宇", 1: "Leasehold improvements 物業裝修",
                        2: "Total 總計"}
    assert groups == ["Property and equipment"]


def test_the_running_header_is_never_a_heading():
    rows = [[_w("河钢股份有限公司2024", 0.60, 0.05), _w("年年度报告全文", 0.80, 0.05)],
            [_w("期末余额", 0.50, 0.10), _w("期初余额", 0.65, 0.10)],
            _figures(0.13)]
    headings, _ = column_headings(rows, BANDS[:2])
    assert headings == {0: "期末余额", 1: "期初余额"}


def test_a_continuation_page_with_blank_rows_above_its_figures_has_no_band():
    """A statement prints its blank lines: those are rows of the table, so the header is above
    them — and on a continuation page there is none to read."""
    rows = [[_w("河钢股份有限公司2024", 0.60, 0.05), _w("年年度报告全文", 0.80, 0.05)],
            [_w("汇兑收益（损失以“-”号填列）", 0.20, 0.09, 0.2)],
            [_w("净敞口套期收益", 0.15, 0.11)],
            _figures(0.13)]
    assert column_headings(rows, BANDS[:2]) == ({}, [])


# ── what the model receives ──────────────────────────────────────────────────────────────────────

def _row(label, figures, page=3):
    li = LineItem(source_label=label, role=LineRole.LINE)
    for (basis, period, value, heading) in figures:
        li.set_value(ExtractedValue(
            value=Decimal(value), value_raw=Decimal(value), basis=basis, period_label=period,
            column_heading=heading,
            provenance={"page_index": page, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}))
    return li


def test_statements_are_one_block_per_entity_and_a_side_by_side_row_keeps_both_figures():
    doc = SimpleNamespace(
        pages=[SimpleNamespace(index=3, statement="balance_sheet")],
        line_items=[_row("Cash and bank balances", [
            (Basis.CONSOLIDATED, "current", 318, "2024 Group"),
            (Basis.STANDALONE, "current", 245, "2024 Company")])])
    blocks = face_context.face_rows(doc, ["balance_sheet"])
    assert [(b["entity"], b["rows"][0]["figures"]) for b in blocks] == [
        ("consolidated", {"current": "318"}), ("standalone", {"current": "245"})]
    assert blocks[0]["columns"] == {"current": "2024 Group"}


def test_a_note_row_names_its_columns_once_and_again_when_they_change():
    from app.services.note_context import identified_notes

    def note_row(label, figures):
        li = _row(label, figures)
        return NoteItem(raw_label=label, values=li.values)

    first = note_row("Cost", [(Basis.CONSOLIDATED, "current", 1, "Land"),
                          (Basis.CONSOLIDATED, "prior", 2, "Buildings"),
                          (Basis.CONSOLIDATED, "col2", 3, "Total")])
    second = note_row("Depreciation", [(Basis.CONSOLIDATED, "current", 4, "Land"),
                                   (Basis.CONSOLIDATED, "prior", 5, "Buildings"),
                                   (Basis.CONSOLIDATED, "col2", 6, "Total")])
    third = note_row("Movement", [(Basis.CONSOLIDATED, "current", 7, "2024"),
                              (Basis.CONSOLIDATED, "prior", 8, "2023")])
    note = SimpleNamespace(title="14. PROPERTY", note_number="14", items=[first, second, third],
                           source_text="")
    import json
    import pathlib

    from app.schemas.line_items import load_line_item_set
    seed = pathlib.Path(__file__).resolve().parents[1] / "app/sample/templates/output_csv_hk_line_items.json"
    shipped = load_line_item_set(json.loads(seed.read_text(encoding="utf-8")), resolve=True)
    rows = identified_notes(shipped, [note], every_note=True)[0]["rows"]
    assert rows[0]["columns"] == {"current": "Land", "prior": "Buildings", "col2": "Total"}
    assert "columns" not in rows[1]
    assert rows[2]["columns"] == {"current": "2024", "prior": "2023"}


def test_a_mainland_period_over_its_measures_names_each_column_period_first():
    """期末余额 | 期初余额 printed over 账面余额 | 跌价准备 each: the period is a group, not part of the
    middle column's name (it read "期初余额跌价准备")."""
    bands = [0.40, 0.50, 0.65, 0.75]
    rows = [[_w("期末余额", 0.45, 0.10), _w("期初余额", 0.70, 0.10)],
            [_w("账面余额", 0.40, 0.12), _w("跌价准备", 0.50, 0.12),
             _w("账面余额", 0.65, 0.12), _w("跌价准备", 0.75, 0.12)],
            [_w("原材料", 0.15, 0.15), *(_w("1,000", x, 0.15) for x in bands)]]
    headings, groups = column_headings(rows, bands)
    assert headings == {0: "期末余额 · 账面余额", 1: "期末余额 · 跌价准备",
                        2: "期初余额 · 账面余额", 3: "期初余额 · 跌价准备"}
    assert groups == ["期末余额", "期初余额"]


def test_two_headings_read_into_one_column_are_dropped_not_merged():
    """Narrow headings wrapping inside tight columns put two phrases of one line in one column."""
    bands = [0.40, 0.70]
    rows = [[_w("应收账款期末余", 0.37, 0.10, 0.05), _w("合同资产", 0.43, 0.10, 0.03),
             _w("比例", 0.70, 0.10)],
            [_w("客户一", 0.15, 0.13), _w("1,000", 0.40, 0.13), _w("2,000", 0.70, 0.13)]]
    headings, _ = column_headings(rows, bands)
    assert headings == {1: "比例"}
