"""A CAS MOVEMENT TABLE READS AS ONE TABLE, across its page breaks — and a share count says it is one.

Measured on the stand-in, before this:

* 000883's 七、21 固定资产情况 reached the model with "（1）计" twice — 2,927,435,003.23 under
  累计折旧 and 48,994,007.22 under 减值准备 — both grouped 一、账面原值：, keyed current / prior /
  col2 … col4 with no heading, under the summary table's title "、固定资产"; its page-189 rows went
  to a note titled "处".
* 300319's and 002273's continuation pages sent their 累计折旧 rows keyed col2 … col4: page 151
  was read as a matrix, which named its columns and passed no header on.
* 600990's table was split at a blank "（3）企业合并增加" into a note so titled, and its columns
  were named "单位：元 其他设备" and "币种：人民币 合计".
* 600990's 坏账准备的情况 folded each caption's second line onto the next: "合合计".
* 000883's 股本 note sent 股份总数 "unstated" under "CNY units (x1)".

Read from the reference filings' own pages, carried page to page as `pdf_extract` carries them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models.enums import Basis
from app.core.models.line_item import NoteItem, NotesTable
from app.schemas.line_items import load_line_item_set
from app.services import note_context
from app.services.notes_extract import extract_note_tables

REPO = Path(__file__).resolve().parents[2]
FILINGS = REPO / "testdata" / "filings"
SEED = REPO / "backend" / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"


def _read(stem: str, first: int, last: int, chapter=("七", 7, "合并财务报表项目注释", None)
          ) -> list[NotesTable]:
    """The notes on pages ``first``..``last``, each page continuing the one before it."""
    fitz = pytest.importorskip("fitz")
    from app.services.pdf_extract import _native_words

    path = FILINGS / f"{stem}.pdf"
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    pdf = fitz.open(path)
    carry = grid = group = header = None
    chapter = list(chapter)
    tables: list[NotesTable] = []
    for i in range(first, last + 1):
        page = pdf[i]
        words = _native_words(page, page.rect.width, page.rect.height, rotation=0)
        grids, groups, headers, opened = [], [], [], []
        got = extract_note_tables(words, page_index=i, document_id="d", source_kind="native",
                                  carry_note=carry, carry_grid=grid, grid_out=grids,
                                  carry_group=group, group_out=groups, chapter=chapter,
                                  carry_header=header, header_out=headers, open_out=opened)
        tables.extend(got)
        carry = (opened[-1] if opened else
                 (got[-1].note_number, got[-1].title, got[-1].basis, got[-1].basis_from)
                 if got else carry)
        grid = grids[-1] if grids else None
        group = groups[-1] if groups else None
        header = headers[-1] if headers else None
    return tables


def _rows(tables, title: str):
    return [(t.source_pages[0], it) for t in tables if t.title == title for it in t.items]


def _by_heading(item) -> dict[str, str]:
    return {str(ev.column_heading or ev.period_label): str(ev.value) for ev in item.values.values()}


def test_hubei_fixed_assets_is_one_table_with_its_blocks_and_its_columns():
    tables = _read("000883_hubei_energy_2024_prc", 187, 189)
    rows = _rows(tables, "固定资产情况")
    # The table's own title on BOTH continuation pages — not "、固定资产", not "处".
    assert {page for page, _ in rows} == {188, 189}, [(t.title, t.source_pages) for t in tables]
    charges = [it for _, it in rows if it.raw_label == "（1）计提"]
    assert [it.group_hint for it in charges] == ["二、累计折旧", "三、减值准备"]
    assert _by_heading(charges[0])["合计"] == "2927435003.23"
    assert _by_heading(charges[0])["房屋及建筑物"] == "943105039.76"
    assert _by_heading(charges[1])["合计"] == "48994007.22"
    closing = [it for page, it in rows if page == 189 and it.raw_label == "1.期末账面价值"]
    assert closing and closing[0].group_hint == "四、账面价值"
    assert _by_heading(closing[0])["合计"] == "65174285084.63"
    # A blank row of the investment-property table above is a row, not a note.
    assert not any(t.title in ("外购", "其他转", "处") for t in tables)


@pytest.mark.parametrize("stem,first,last,note,charge", [
    ("300319_maijie_2024_prc", 151, 152, "七、13", "224191676.69"),
    ("002273_crystal_optech_2024_prc", 148, 149, "七、21", "479980535.24"),
])
def test_a_page_after_a_matrix_page_is_named_by_its_header(stem, first, last, note, charge):
    rows = _rows(_read(stem, first, last), "固定资产情况")
    charges = [it for _, it in rows if it.raw_label == "（1）计提"]
    assert charges and charges[0].group_hint == "二、累计折旧"
    assert _by_heading(charges[0]).get("合计") == charge, _by_heading(charges[0])


def test_sun_create_fixed_assets_is_not_split_at_a_blank_row_and_names_its_columns():
    tables = _read("600990_sun_create_2024_prc", 139, 139)
    assert not any(t.title == "企业合并增加" for t in tables)
    rows = _rows(tables, "固定资产情况")
    charge = [it for _, it in rows if it.raw_label == "（1）计提" and it.group_hint == "二、累计折旧"]
    assert charge and _by_heading(charge[0])["合计"] == "82456200.10"
    headings = set(_by_heading(charge[0]))
    assert headings == {"房屋及建筑物", "机器设备", "运输工具", "电子设备", "其他设备", "合计"}


def test_a_caption_wrapped_below_its_figures_stays_with_its_row():
    tables = _read("600990_sun_create_2024_prc", 132, 132)
    captions = [it.raw_label for t in tables if t.title == "坏账准备的情况" for it in t.items]
    assert captions[:4] == ["按单项计提坏账准备", "按组合计提坏账准备", "其中：账龄组合", "合计"]


def test_a_share_count_row_states_its_periods_and_that_it_counts_shares():
    tables = [t for t in _read("000883_hubei_energy_2024_prc", 216, 216) if t.title == "、股本"]
    assert tables
    lis = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    blocks = note_context.identified_notes(lis, tables, every_note=True)
    row = next(r for b in blocks for r in b.get("rows", ()) if r["caption"] == "股份总数")
    assert row["periods"] == {"期初余额": "prior", "期末余额": "current"}
    assert row["figures"]["期初余额"] == "6520949388.00"
    assert row["measure"] == note_context.SHARE_COUNT_NOTE


def test_share_count_captions():
    assert note_context.share_count_caption("股份总数")
    assert note_context.share_count_caption("Number of ordinary shares in issue")
    # The money row of a share-capital table prints the count in its caption; its figure is money.
    assert not note_context.share_count_caption(
        "Issued and fully paid 2,253,204,500 ordinary shares")
    assert not note_context.share_count_caption("Share premium")


def _table(title: str, number: str) -> NotesTable:
    t = NotesTable(note_number=number, title=title, basis=None, source_pages=[1])
    t.items.append(NoteItem(raw_label="x", ordinal=0))
    return t


def test_the_share_count_line_is_sent_the_share_capital_note():
    lis = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    notes = [_table("SHARE CAPITAL", "40"), _table("SHARE CAPITAL 30. 股本", "30"),
             _table("、股本", "七、53"), _table("EARNINGS PER SHARE", "13")]
    blocks = note_context.identified_notes(lis, notes)
    key = "supplemental_data__num_outstanding_common_shares"
    claimed = {b["note"] for b in blocks if key in b["identified_for"]}
    assert {"40", "30", "七、53"} <= claimed
    assert "13" not in claimed
    item = next(i for i in lis.items if i.key == key)
    # Heading vocabulary only: it steers which notes are sent and reads no row.
    assert set(item.note_source.model_dump(exclude_defaults=True)) == {"note_terms"}


def test_a_heading_at_a_pages_foot_is_what_the_next_page_continues():
    """Kaishan prints （1）按账龄披露 at the foot of page 168 with no header under it; the page-169
    ageing rows continue IT, not the last section on 168 that printed a header (七、4's
    本期实际核销的应收票据情况)."""
    tables = _read("300257_kaishan_2024_prc", 167, 169)
    ageing = [t for t in tables if t.source_pages == [169] and t.items][0]
    assert (ageing.note_number, ageing.title) == ("七、5", "按账龄披露")


def test_a_matrix_page_passes_on_the_block_it_ends_in():
    """688411's 固定资产情况 is read as a matrix on page 239, which ends on 二、累计折旧; page 240's
    "（1） 计提" is that block's charge, not ungrouped and not the impairment block's."""
    rows = _rows(_read("688411_hyperstrong_2024_prc", 239, 240), "固定资产情况")
    charge = [it for page, it in rows if page == 240 and it.raw_label.replace(" ", "") == "（1）计提"]
    assert charge and charge[0].group_hint == "二、累计折旧"


def test_a_heading_that_opens_on_its_year_dates_its_column():
    """0191's SHARE CAPITAL prints "2025 Number of shares | Total amount | 2024 Number of shares |
    Total amount"; the 2024 count is the comparative's, and each amount takes its year."""
    tables = [t for t in _read("hkex_0191_lai_sun_garment_2024_25", 305, 305, (None, 0, "", None))
              if t.title == "SHARE CAPITAL"]
    lis = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    blocks = note_context.identified_notes(lis, tables, every_note=True)
    row = next(r for b in blocks for r in b.get("rows", ())
               if r["caption"] == "Issued and fully paid ordinary shares")
    assert row["periods"]["2025 Number of shares"] == "current"
    assert row["periods"]["2024 Number of shares"] == "prior"
    assert row["periods"]["Total amount (2024)"] == "prior"
