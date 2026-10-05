r"""THE LEVEL 3 PART READS THE LEVEL 3 COLUMN — the shipped `sub__fa_cp_level_3_total`'s gates.

Every fair-value hierarchy table on the five reference filings prints its levels as COLUMNS, so the
part used to name the level in `row_caption_any` and selected nothing on any of them. It now names
the level in `column_heading_any` (read through `note_columns.matches_patterns`), vetoes Level 1,
Level 2 and the all-levels total in `column_heading_none`, and names ASSET rows — not `\S` — with
vetoes for what a hierarchy table also prints: liabilities, totals, CAS sub-levels, the level
header read as a row, transfers between levels and non-recurring measurements.

The shapes here are the five filings' own, with the reader's headings and periods as it now names
them (header-first reader, `ExtractedValue.column_heading` / `column_period`):

  * 河钢 000709 十三、1 — Level 3 and 合计 under one 期末公允价值 group, `current` the Level 3
    column and `prior` the same year-end's 合计: Level 3 is 909,834,646.75 + 411,683,468.98.
  * 迈捷 300319 十二、1 — the figures stand under 第二层次: Level 3 is nil, nothing is read.
  * China SCE 1966 note 46 — the years are blocks of rows, Level 3 keyed `prior` in both, the
    "(Level 1) (Level 2) (Level 3)" header read as a row of figures 1 and 3: Level 3 is 344,135
    for 2023 and 378,539 for 2022, and the header row is not a figure.

NOTE IDENTIFICATION IS NOT UNDER TEST HERE — the part finds its note by meaning
(`note_terms`), which `line_item_notes` pins. The note is named by title in these cases so each
row and column rule is pinned on its own.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from app.schemas.line_items import load_line_item_set
from app.services import note_sourced

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
KEY = "sub__fa_cp_level_3_total"
PERIODS = {"current", "prior"}


@pytest.fixture(scope="module")
def part():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    item = next(i for i in st.items if i.key == KEY)
    return item.model_copy(update={"note_source": item.note_source.model_copy(update={
        "note_title_any": [r"fair value|公允价值|公允值"]})})


def V(value, heading, period_label, column_period=None):
    return NS(value=Decimal(str(value)), value_raw=None, period_label=period_label,
              column_heading=heading, column_period=column_period, column_index=None,
              basis="consolidated", confidence=NS(flags=[]), provenance=None)


def R(caption, *values, period_hint=""):
    return NS(raw_label=caption, group_hint="", period_hint=period_hint, total_slot="",
              values={f"v{i}": v for i, v in enumerate(values)})


def T(number, title, *rows):
    return NS(title=title, note_number=number, chapter_title="", basis=None, items=list(rows))


def picked(part, note):
    hits = note_sourced.select_rows(part, [note], PERIODS)
    return sorted((h.period, h.amount) for h in hits)


L3_CAS = "期末公允价值 · 第三层次公允价值计量"
L2_CAS = "期末公允价值 · 第二层次公允价值计量"
TOTAL_CAS = "期末公允价值 · 合计"
L1_HK = "Quoted prices in active market (Level 1) 活躍市場報價（第一級）"
L3_HK = "Significant unobservable inputs (Level 3) 重大不可觀察輸入數據（第三級）"


def test_the_part_names_the_level_as_a_column_and_its_rows_as_assets(part):
    ns = part.note_source
    assert ns.column_heading_any and ns.column_heading_none
    assert ns.row_caption_any != [r"\S"]
    assert not any("level" in p.lower() or "第三" in p for p in ns.row_caption_any)


def test_000709_reads_level_3_and_not_the_year_ends_total(part):
    note = T("十三、1", "、以公允价值计量的资产和负债的年末公允价值",
             R("（一）应收款项融资", V("909834646.75", L3_CAS, "current", "current"),
               V("909834646.75", TOTAL_CAS, "prior", "current")),
             R("（二）其他权益工具投资", V("411683468.98", L3_CAS, "current", "current"),
               V("411683468.98", TOTAL_CAS, "prior", "current")),
             R("持续以公允价值计量的资产总额", V("1321518115.73", L3_CAS, "current", "current")),
             R("1.债务工具投资", V("5", L3_CAS, "current", "current")),
             R("（1）债务工具投资", V("6", L3_CAS, "current", "current")),
             R("交易性金融负债", V("7", L3_CAS, "current", "current")),
             R("二、非持续的公允价值计量 持有待售资产", V("8", L3_CAS, "current", "current")))
    assert picked(part, note) == [("current", Decimal("411683468.98")),
                                  ("current", Decimal("909834646.75"))]


def test_300319_level_2_figures_are_not_level_3(part):
    note = T("十二、1", "、以公允价值计量的资产和负债的期末公允价值",
             R("（一）交易性金融资产", V("431478888.81", L2_CAS, "current", "current"),
               V("431478888.81", TOTAL_CAS, "prior", "current")))
    assert picked(part, note) == []


def test_1966_reads_each_years_block_and_not_the_header_row(part):
    caption = "Financial assets at fair value through profit or loss 按公允值計入損益的金融資產"
    header = ("Quoted prices Significant Significant in active observable unobservable market "
              "inputs inputs (Level")
    note = T("46", "FAIR VALUE AND FAIR VALUE HIERARCHY 46. 公允值及公允值層級（續）",
             R(header, V(1, L1_HK, "current"), V(3, L3_HK, "prior"), period_hint="current"),
             R(caption, V(344135, L3_HK, "prior"), period_hint="current"),
             R(header, V(1, L1_HK, "current"), V(3, L3_HK, "prior"), period_hint="prior"),
             R(caption, V(53434, L1_HK, "current"), V(378539, L3_HK, "prior"),
               period_hint="prior"),
             R("Transfer from Level 2 從第二級轉撥", V(84845, L3_HK, "prior"), period_hint="prior"))
    assert picked(part, note) == [("current", Decimal("344135")), ("prior", Decimal("378539"))]
