"""Find 3 — the fair-value hierarchy's Level 3 total of financial assets — read on every filing that
prints one, as docs/HKEX_Securities_Other_Financial_Assets_Extraction_Logic_Clean.md 5.2-5.4 asks.

Measured on the nineteen HK-template filings against the tables they print. Before this, seven read
Level 3 correctly; these did not:

* 002273 水晶光电 十三、1 — the header block was read as its 项目 line alone (three columns, no 合计),
  and inside the section a wrapped caption ended the header climb as prose: 187,541,328.29 unread.
* 湖北能源 000883 十三、1 — header at the foot of page 254 with Level 1 and Level 2 blank, rows on
  page 255; the carried header was refused for its blank columns: 260,683,583.26 unread.
* 688411 海博思创 十三、1 — 项目 on a line of its own, the rows in sections of their own below.
* 688008 澜起 十三、1 — the header split by the page break, its second line read as a header alone.
* Hon Kwok 0160 note 37 — the 2023 table shares a section with the Level 3 movement table, whose
  "HK$’" + "000" units line widened the caption column over the header: 63,862 unread.
* PetroChina 0857 "3. Fair value estimation" — the date is in the lead-in sentence ("As of December
  31, 2024, … as follows:"), so no row had a period: 9,185 / 10,999 unread.
* Lai Sun 0191 note 47 — a caption list missed "Film investments" and derivative assets: 289,040 /
  304,175 for a printed 463,255 / 415,272.
* 300391 长江医药 prints 应收账款融资, which the caption list did not name: 222,168.00 unread.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NotesTable
from app.schemas.line_items import load_line_item_set
from app.services import line_item_notes, note_sourced
from app.services.notes_extract import extract_note_tables

REPO = Path(__file__).resolve().parents[2]
FILINGS = REPO / "testdata" / "filings"
SEED = REPO / "backend" / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"
L3_CAS = "期末公允价值 · 第三层次公允价值计量"
L2_CAS = "期末公允价值 · 第二层次公允价值计量"
TOTAL_CAS = "期末公允价值 · 合计"
PRINTED = "sub__fa_cp_level_3_printed_total"
ROWS = "sub__fa_cp_level_3_rows"
FIND3 = "sub__fa_cp_level_3_total"
CP = "bs_ca__secur_and_other_fincl_assets_cp"


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _read(stem: str, first: int, last: int, carry=None, chapter=None) -> list[NotesTable]:
    """The notes on pages ``first``..``last``, each page continuing the one before it — the
    carries `pdf_extract` passes."""
    fitz = pytest.importorskip("fitz")
    from app.services.pdf_extract import _native_words

    path = FILINGS / f"{stem}.pdf"
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    pdf = fitz.open(path)
    grid = group = header = None
    chapter = list(chapter or (None, 0, "", None))
    years: dict = {}
    tables: list[NotesTable] = []
    for i in range(first, last + 1):
        page = pdf[i]
        words = _native_words(page, page.rect.width, page.rect.height, rotation=0)
        grids, groups, headers, opened = [], [], [], []
        got = extract_note_tables(words, page_index=i, document_id="d", source_kind="native",
                                  carry_note=carry, carry_grid=grid, grid_out=grids,
                                  carry_group=group, group_out=groups, chapter=chapter,
                                  carry_header=header, header_out=headers, open_out=opened,
                                  note_years=years)
        tables.extend(got)
        carry = (opened[-1] if opened else
                 (got[-1].note_number, got[-1].title, got[-1].basis, got[-1].basis_from)
                 if got else carry)
        grid = grids[-1] if grids else None
        group = groups[-1] if groups else None
        header = headers[-1] if headers else None
    return tables


def _row(tables, caption: str, nth: int = 0):
    found = [it for t in tables for it in t.items if (it.raw_label or "").startswith(caption)]
    assert len(found) > nth, f"no row {caption!r}: {[it.raw_label for t in tables for it in t.items]}"
    return found[nth]


def _cells(item) -> dict[str, str]:
    return {str(ev.column_heading or ev.period_label): str(ev.value) for ev in item.values.values()}


# ── the reader ────────────────────────────────────────────────────────────────────────────────

def test_002273_a_two_line_header_over_a_wrapped_caption_names_level_3():
    tables = _read("002273_crystal_optech_2024_prc", 181, 182, carry=("十二、8", ""))
    total = _row(tables, "持续以公允价值计量的资产总额")
    assert _cells(total) == {L2_CAS: "139787177.62", L3_CAS: "187541328.29",
                             TOTAL_CAS: "327328505.91"}


def test_000883_a_header_at_the_foot_of_the_page_with_two_blank_levels_names_the_rows_below():
    tables = _read("000883_hubei_energy_2024_prc", 253, 254,
                   carry=("十三、1", "以公允价值计量的资产和负债的期末公允价值"))
    total = _row(tables, "持续以公允价值计量的资产总额")
    assert _cells(total) == {L3_CAS: "260683583.26", TOTAL_CAS: "260683583.26"}


def test_688411_xiangmu_on_a_line_of_its_own_still_heads_the_table():
    tables = _read("688411_hyperstrong_2024_prc", 283, 284, carry=("十二、5", ""))
    total = _row(tables, "持续以公允价值计量的资产总额")
    assert _cells(total) == {L2_CAS: "109279749.19", TOTAL_CAS: "109279749.19"}
    assert L3_CAS in total.printed_columns, total.printed_columns   # printed, and blank


def test_688008_a_header_split_by_the_page_break_names_the_first_rows():
    tables = _read("688008_montage_2024_prc", 236, 238, carry=("十二、3", ""))
    first = _row(tables, "（一） 交易性金融资产")
    assert set(_cells(first).values()) == {"154131845.21", "1629362905.47", "1783494750.68"}
    assert all(h.startswith("期末公允价值 · ") for h in _cells(first)), _cells(first)


def test_0160_the_comparative_hierarchy_is_read_and_the_movement_table_below_it_is_not():
    tables = _read("hkex_0160_hon_kwok_land_2023_24", 145, 146,
                   carry=("37", "FAIR VALUE AND FAIR VALUE HIERARCHY OF FINANCIAL INSTRUMENTS"))
    fvoci = _row(tables, "Financial asset at fair value thro", nth=1)
    assert fvoci.period_hint == "prior"
    assert [h for h, v in _cells(fvoci).items() if v == "60127" and "Level 3" in h]
    opening = _row(tables, "At 1 January")
    assert all(not ev.column_heading for ev in opening.values.values()), _cells(opening)


def test_0857_a_lead_in_sentence_dates_the_hierarchy_below_it():
    tables = _read("hkex_0857_petrochina_2024", 209, 210, carry=("3", "Fair value estimation"))
    now, then = _row(tables, "Receivables financing"), _row(tables, "Receivables financing", nth=1)
    assert (now.period_hint, then.period_hint) == ("current", "prior")
    assert _cells(now)["Level 3"] == "8868" and _cells(then)["Level 3"] == "10661"


def test_a_lead_in_sentence_does_not_date_a_table_whose_columns_print_years():
    from app.services.notes_extract import _lead_in_anchors
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word

    def w(t, x0, x1, y):
        return Word(text=t, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + 0.011))
    rows = [[w("As at 31 December 2024, the ageing analysis is as follows:", 0.10, 0.80, 0.10)]]
    assert _lead_in_anchors(rows)                      # it is a lead-in …
    rows = [[w("As at 31 December 2024, the Group had no pledged deposits.", 0.10, 0.80, 0.10)]]
    assert not _lead_in_anchors(rows)                  # … and a sentence without "as follows" is not


# ── the configuration ─────────────────────────────────────────────────────────────────────────

def _value(amount, heading, label, period=None):
    return ExtractedValue(basis=Basis.CONSOLIDATED, period_label=label, value=Decimal(amount),
                          value_raw=Decimal(amount), column_heading=heading, column_period=period,
                          provenance=Provenance(page_index=1))


def _note_row(caption, *values, printed=(), hint=""):
    from app.core.models.line_item import NoteItem
    row = NoteItem(raw_label=caption, note_number="十三、1", printed_columns=list(printed),
                   period_hint=hint)
    for v in values:
        row.set_value(v)
    return row


CAS_COLUMNS = ["期末公允价值 · 第一层次公允价值计量", L2_CAS, L3_CAS, TOTAL_CAS]


def _hierarchy(*rows):
    t = NotesTable(note_number="十三、1", title="、以公允价值计量的资产和负债的期末公允价值",
                   chapter_title="公允价值的披露", page_index=1)
    t.items.extend(rows)
    return t


def test_a_printed_level_3_column_left_blank_reads_zero(shipped):
    """688008, 300319, 301577, 000685: the hierarchy prints 第三层次 and nothing in it. Level 3 is
    nil, not missing — the spec raises MISSING_LEVEL_3 only where no hierarchy exists."""
    part = {i.key: i for i in shipped.items}[PRINTED]
    total = _note_row("持续以公允价值计量的资产总额",
                      _value("176402753.81", CAS_COLUMNS[0], "current", "current"),
                      _value("2204606831.44", L2_CAS, "prior", "current"),
                      _value("2381009585.25", TOTAL_CAS, "col2", "current"), printed=CAS_COLUMNS)
    hits = note_sourced.select_rows(part, [_hierarchy(total)], {"current", "prior"})
    assert [(h.period, h.amount, h.column) for h in hits] == [("current", Decimal(0), L3_CAS)]
    assert hits[0].period_source == "blank_printed_column:row_figures"


def test_the_printed_total_is_read_before_the_rows(shipped):
    """002273: the rows count the category 分类为以公允价值计量…的金融资产 129,450,000.00 AND the two
    items printed under it; the table's own total is 187,541,328.29."""
    from app.services.line_items import evaluate
    find3 = {i.key: i for i in shipped.items}[FIND3]
    got = evaluate(find3, {PRINTED: Decimal("187541328.29"), ROWS: Decimal("316991328.29")})
    assert (got.value, got.rung_used) == (Decimal("187541328.29"), "L3_PRINTED_TOTAL")
    got = evaluate(find3, {ROWS: Decimal("1321518115.73")})            # 000709 prints no total
    assert (got.value, got.rung_used) == (Decimal("1321518115.73"), "L3_ROWS")


def test_the_two_readings_both_read_the_hierarchy_note(shipped):
    """Alternatives, not addends: handed to one of them by `claimed_notes`, the other read nothing,
    and a hierarchy with no total row gave no Level 3 at all."""
    notes = [NS(title="FAIR VALUE AND FAIR VALUE HIERARCHY OF FINANCIAL INSTRUMENTS",
                note_number="47", items=[])]
    claims = line_item_notes.claimed_notes(shipped.items, notes)
    assert claims[PRINTED] == {"47"} and claims[ROWS] == {"47"}


def _cp_run(shipped, figures):
    from app.config import get_settings
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    from app.stages.note_sourced import NoteSourcedStage
    doc = DocumentModel(filename="f.pdf")
    for (key, period), amount in figures.items():
        row = next((li for li in doc.line_items if li.canonical_key == key), None)
        if row is None:
            row = LineItem(source_label=key, canonical_key=key, role=LineRole.LINE)
            doc.line_items.append(row)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=7)))
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    NoteSourcedStage().run(doc, ctx)
    return doc


def _slot(doc, key, period):
    li = next((r for r in doc.line_items if r.canonical_key == key), None)
    if li is None:
        return None, []
    ev = next((v for v in li.values.values() if v.period_label == period and v.value is not None),
              None)
    return (ev.value if ev else None), (ev.confidence.flags if ev else [])


def test_cp_zero_reads_find_3_computed_in_the_same_pass(shipped):
    """诚志股份 current: Find 1 1,372,158,938.52, Level 3 2,974,821,765.81 from the hierarchy's rows.
    Find 3 is computed in the childless pass and the residual after it in the same pass, so CP is
    zero and the overshoot reaches LTP. Before, the residual was computed without Find 3 and CP fell
    through to the face."""
    doc = _cp_run(shipped, {("sub__fa_cp_fvtpl_note_total", "current"): "1372158938.52",
                            (ROWS, "current"): "2974821765.81"})
    assert _slot(doc, FIND3, "current")[0] == Decimal("2974821765.81")
    assert _slot(doc, "sub__fa_cp_intermediate_residual", "current")[0] == Decimal("1602662827.29")
    value, flags = _slot(doc, CP, "current")
    assert value == 0 and "missing_level_3" not in flags


def test_cp_without_a_level_3_figure_says_so(shipped):
    """Spec 5.4: no hierarchy for the slot — every CAS comparative year — CP is Find 1 alone and
    carries MISSING_LEVEL_3."""
    doc = _cp_run(shipped, {("sub__fa_cp_fvtpl_note_total", "prior"): "180559509.03",
                            (ROWS, "current"): "0"})
    value, flags = _slot(doc, CP, "prior")
    assert value == Decimal("180559509.03") and "missing_level_3" in flags
    row = next(r for r in doc.line_items if r.canonical_key == CP)
    assert "missing_level_3:consolidated:prior" in row.confidence.flags
