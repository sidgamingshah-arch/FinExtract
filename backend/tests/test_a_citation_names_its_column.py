"""A MODEL MAY CITE ONE COLUMN OF A ROW, and the figure is filed under the period printed over it.

A row of a fair-value hierarchy, a PPE movement or a provision grid holds several figures of ONE
year — a level, an asset class, a measure each — and the line is one of them. The only citation
the reply could express was the whole row, whose POSITIONAL keys were then written as if they were
years: on 河钢股份 000709's 十三、1 the Level 3 figure went in as `current` and the same year-end's
合计 as `prior`; on 1966's note 14 the Depreciation row filed Land and building as this year and
Leasehold improvements as last year, with the Total, -102,572, in `col8`.

`SourceRef.column` names the column — the printed heading as given, or the key — and
`note_columns.pick` takes only that cell, filed under the period printed over its column or the
row's block, and says so when nothing printed dated it. A column the table prints with no figure on
the row is refused with its own reason: it is not a figure, and never a zero.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable
from app.services import note_columns, note_context
from app.services.line_item_llm import REPLY_CONTRACT, LineItemReply
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures/printed_columns_sections.json")
                 .read_text(encoding="utf-8"))


def _real(key: str) -> list[Word]:
    return [Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d)) for t, a, b, c, d in FIX[key]["words"]]


def _tables(key: str, number: str, title: str, page: int) -> list[NotesTable]:
    return extract_note_tables(_real(key), page_index=page, document_id="t", source_kind="native",
                               carry_note=(number, title))


def _rows(tables, caption: str) -> list[NoteItem]:
    return [ni for t in tables for ni in t.items if ni.raw_label.startswith(caption)]


@pytest.fixture(scope="module")
def fv_000709():
    return _tables("000709_fv", "十三、1", "公允价值的披露", 197)


@pytest.fixture(scope="module")
def ppe_1966():
    return _tables("1966_ppe", "14", "PROPERTY, PLANT AND EQUIPMENT", 193)


@pytest.fixture(scope="module")
def fv_300319():
    return _tables("300319_fv_head", "十二、1", "公允价值的披露", 176)


LEVEL3_709 = "期末公允价值 · 第三层次公允价值计量"


# ── note_columns.pick, on real rows ──────────────────────────────────────────────────────────────

def test_000709_level_3_is_this_years_figure_only(fv_000709):
    """十三、1: 期末公允价值 over the levels, so the Level 3 cell is the CURRENT year-end — and only
    it is taken; the 合计 standing in `prior` is the same year-end, not last year."""
    row = _rows(fv_000709, "（一）应收款项融资")[0]
    for want in (LEVEL3_709, "第三层次公允价值计量", "第三层次"):
        got = note_columns.pick(row, want)
        assert got["why"] == "", (want, got["why"])
        assert got["figures"] == {"current": "909834646.75"}, want
        assert [(c["key"], c["period"], c["period_source"]) for c in got["cells"]] == [
            ("current", "current", "column")]
        assert got["assumed"] is False
    assert note_columns.pick(row, "合计")["figures"] == {"current": "909834646.75"}


def test_300319_level_3_is_printed_and_blank_so_it_is_refused_not_zero(fv_300319):
    """十二、1: the figures stand under 第二层次; 第三层次 is printed and holds nothing."""
    row = _rows(fv_300319, "（一）交易性金融资产")[0]
    assert "第三层次" in " ".join(note_columns.blank_columns(row))
    for want in ("第三层次", "第三层次公允价值计量", "期末公允价值 · 第三层次公允价值计量"):
        got = note_columns.pick(row, want)
        assert got["blank"] is True and got["figures"] == {}, want
        assert "holds no figure" in got["why"] and "not a zero" in got["why"]
    assert note_columns.pick(row, "第二层次")["figures"] == {"current": "431478888.81"}


def test_1966_total_column_takes_its_period_from_the_rows_block(ppe_1966):
    """Note 14: the columns are asset classes; the 2023 block states the year on the rows."""
    row = _rows(ppe_1966, "Depreciation")[0]
    assert row.period_hint == "current"
    got = note_columns.pick(row, "Total 總計")
    assert got["figures"] == {"current": "-102572"}
    assert [(c["key"], c["period_source"]) for c in got["cells"]] == [("col8", "row_block")]
    # its Chinese half, its English half, and its key name the same cell
    for want in ("Total", "總計", "col8"):
        assert note_columns.pick(row, want)["figures"] == {"current": "-102572"}, want


def test_two_columns_printed_alike_in_one_period_are_refused(ppe_1966):
    """1966 prints "Subtotal 小計" twice — over the property classes and over the land classes."""
    row = _rows(ppe_1966, "Depreciation")[0]
    got = note_columns.pick(row, "Subtotal 小計")
    assert got["figures"] == {} and "different figures in one period" in got["why"]


def test_a_heading_the_table_does_not_print_is_refused_with_its_columns(ppe_1966):
    row = _rows(ppe_1966, "Depreciation")[0]
    got = note_columns.pick(row, "Investment properties")
    assert got["figures"] == {} and "Land and building" in got["why"]


# ── note_columns.pick, the rules on their own ────────────────────────────────────────────────────

def _ev(key, value, heading=None, period=None, basis=Basis.CONSOLIDATED, page=1, flags=()):
    ev = ExtractedValue(value=Decimal(value), value_raw=Decimal(value), basis=basis,
                        period_label=key, column_heading=heading, column_period=period,
                        provenance=Provenance(page_index=page, text_snippet=str(value)))
    ev.confidence.flags.extend(flags)
    return ev


def _note_row(*evs, printed=(), hint="") -> NoteItem:
    row = NoteItem(raw_label="应收账款", printed_columns=list(printed), period_hint=hint)
    for ev in evs:
        row.set_value(ev)
    return row


def test_one_heading_over_two_periods_is_taken_once_in_each():
    """期末 坏账准备 and 期初 坏账准备: one measure, two periods — both, each under its own."""
    row = _note_row(_ev("current", "100", "期末余额 · 账面余额", "current"),
                    _ev("current:allowance", "7", "期末余额 · 坏账准备", "current"),
                    _ev("prior", "90", "期初余额 · 账面余额", "prior"),
                    _ev("prior:allowance", "6", "期初余额 · 坏账准备", "prior"))
    assert note_columns.pick(row, "坏账准备")["figures"] == {"current": "7", "prior": "6"}
    assert note_columns.pick(row, "期初余额 · 坏账准备")["figures"] == {"prior": "6"}


def test_a_column_nothing_dates_is_filed_this_year_and_says_it_was_assumed():
    """嘉民's fair-value table read without its block: Level 2's year is printed nowhere."""
    row = _note_row(_ev("current", "9956", "Fair value 公平值"), _ev("prior", "9956", "Level 2 第二級"),
                    printed=("Fair value 公平值", "Level 1 第一級", "Level 2 第二級", "Level 3 第三級"))
    got = note_columns.pick(row, "Level 2 第二級")
    assert got["figures"] == {"current": "9956"} and got["assumed"] is True
    assert got["cells"][0]["period_source"] == "assumed"
    assert note_columns.pick(row, "Level 3")["blank"] is True


def test_an_unheaded_period_table_reads_its_key_as_the_period():
    row = _note_row(_ev("current", "5"), _ev("prior", "4"))
    got = note_columns.pick(row, "prior")
    assert got["figures"] == {"prior": "4"} and got["assumed"] is False


def test_the_picked_cells_share_one_basis_and_carry_their_own_page():
    row = _note_row(_ev("current", "5", "Level 3", page=10),
                    _ev("current", "3", "Level 3", basis=Basis.STANDALONE, page=11))
    got = note_columns.pick(row, "Level 3")
    assert got["basis"] == "consolidated" and got["figures"] == {"current": "5"}
    assert got["cells"][0]["ev"].provenance.page_index == 10


def test_a_namespace_row_reads_the_same():
    """Rows reach the helper as plain namespaces in some readers; nothing but attributes is read."""
    ev = SimpleNamespace(period_label="col2", value=Decimal("8"), column_heading="Level 3",
                         column_period=None, column_index=None, basis="consolidated",
                         confidence=SimpleNamespace(flags=[]), provenance=None)
    row = SimpleNamespace(values={"a": ev}, printed_columns=["Level 1", "Level 3"],
                          period_hint="prior")
    assert note_columns.pick(row, "Level 3")["figures"] == {"prior": "8"}
    assert note_columns.pick(row, "Level 1")["blank"] is True


# ── resolve_sources: the note arm and the face arm ───────────────────────────────────────────────

def test_a_column_citation_resolves_to_that_cell_and_says_which(fv_000709):
    resolved, unresolved = resolve_sources(
        [SourceRef(note="十三、1", caption="（一）应收款项融资", column=LEVEL3_709)], fv_000709)
    assert unresolved == []
    (entry,) = resolved
    assert entry["figures"] == {"current": "909834646.75"}
    assert entry["figures_by_basis"] == {"consolidated": {"current": "909834646.75"}}
    assert entry["column"]["heading"] == LEVEL3_709 and entry["column"]["assumed"] is False
    assert entry["provenance"]["page_index"] == 197


def test_a_blank_column_citation_is_unresolved_with_its_own_reason(fv_300319):
    resolved, unresolved = resolve_sources(
        [SourceRef(note="十二、1", caption="（一）交易性金融资产", column="第三层次")], fv_300319)
    assert resolved == []
    (bad,) = unresolved
    assert bad["column"] == "第三层次" and "holds no figure" in bad["why"]


def test_on_the_face_a_key_is_a_period():
    from app.core.models.line_item import LineItem
    face_row = LineItem(source_label="Trade receivables")
    face_row.set_value(_ev("current", "10", "2024"))
    face_row.set_value(_ev("prior", "9", "2023"))
    resolved, _ = resolve_sources([SourceRef(statement="balance_sheet",
                                             caption="Trade receivables", column="prior")],
                                  [], [("balance_sheet", "Trade receivables", face_row)])
    assert resolved[0]["figures"] == {"prior": "9"} and resolved[0]["on_face"] is True


# ── the request and the contract ─────────────────────────────────────────────────────────────────

def test_the_reply_schema_and_the_contract_carry_the_column():
    assert "column" in SourceRef.model_fields
    assert "column" in json.dumps(LineItemReply.model_json_schema())
    for needed in ("`column`", "printed_columns", "not a figure, and not a zero",
                   "is refused — name the column"):
        assert needed in REPLY_CONTRACT, needed
    # it stands after the paragraph on figure keys, which it qualifies
    assert REPLY_CONTRACT.index("ONE COLUMN OF A ROW") > REPLY_CONTRACT.index(
        "WHAT A FIGURE KEY IS")


def _identified(tables):
    from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
    items = [LineItemDef(key="k", label="k",
                         note_source=NoteSource(note_title_any=[".*"]))]
    return note_context.identified_notes(LineItemSet(items=items), tables)


def test_the_request_lists_every_printed_column_where_one_is_blank(fv_300319, fv_000709):
    """Every column the table prints, sent once per change, on a row where one holds no figure —
    and the bytes are the same every time they are built."""
    for tables in (fv_300319, fv_000709):
        built = _identified(tables)
        again = _identified(tables)
        assert json.dumps(built, ensure_ascii=False) == json.dumps(again, ensure_ascii=False)
        rows = [r for e in built for r in e.get("rows", ())]
        sent = [r["printed_columns"] for r in rows if "printed_columns" in r]
        assert len(sent) == 1, sent
        assert any("第一层次" in h for h in sent[0]) and any("合计" in h for h in sent[0])
        assert "columns" in rows[0]


def test_a_table_with_every_column_filled_sends_no_printed_columns(ppe_1966):
    built = _identified(ppe_1966)
    rows = [r for e in built for r in e.get("rows", ())]
    cost = next(r for r in rows if r["caption"].startswith("Cost"))
    assert "printed_columns" not in cost


# ── through the stage, with the model answering ──────────────────────────────────────────────────

LEVEL3_KEY = "sub__fa_cp_level_3_total"


@pytest.fixture(autouse=True)
def _restore_settings():
    from app.config import get_settings
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = was


def _run(notes, cite):
    from app.config import get_settings
    from app.core.stage import PipelineContext
    from app.schemas.line_items import load_line_item_set
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage
    from tests.spy_line_item_llm import SpyLineItemLlm

    seed = (pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "output_csv_hk_line_items.json")
    shipped = load_line_item_set(json.loads(seed.read_text(encoding="utf-8")), resolve=True)
    spy = SpyLineItemLlm(only={LEVEL3_KEY}, cite={LEVEL3_KEY: cite})
    doc = DocumentModel(filename="f.pdf", locale="zh")
    doc.notes = list(notes)
    doc.line_items = []
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [LEVEL3_KEY]
    ctx.registry.register("llm", "spy", lambda: spy)
    ctx.settings.llm.provider = "spy"
    LineItemLlmStage().run(doc, ctx)
    row = next((li for li in doc.line_items if li.canonical_key == LEVEL3_KEY), None)
    return spy, row


def test_the_stage_files_a_cited_column_under_its_printed_period(fv_000709):
    spy, row = _run(fv_000709, ("十三、1", "（一）应收款项融资", "", "", LEVEL3_709))
    assert spy.citations[0]["column"] == LEVEL3_709
    assert row is not None
    assert {ev.period_label: ev.value for ev in row.values.values()} == {
        "current": Decimal("909834646.75")}
    assert f"llm_column:{LEVEL3_709}" in row.confidence.flags
    assert "llm_column_period_assumed" not in row.confidence.flags
    trail = json.dumps(row.derivation, ensure_ascii=False, default=str)
    assert f"[{LEVEL3_709}]" in trail


def test_the_stage_flags_a_column_whose_period_it_assumed():
    row_in = _note_row(_ev("current", "9956", "Fair value 公平值"),
                       _ev("prior", "9956", "Level 2 第二級"),
                       printed=("Fair value 公平值", "Level 1 第一級", "Level 2 第二級"))
    row_in.raw_label = "Financial assets at FVTPL"
    table = NotesTable(note_number="30", title="FAIR VALUE", items=[row_in])
    _spy, row = _run([table], ("30", "Financial assets at FVTPL", "", "", "Level 2 第二級"))
    assert {ev.period_label for ev in row.values.values()} == {"current"}
    assert {"llm_column:Level 2 第二級", "llm_column_period_assumed",
            "low_mapping_confidence"} <= set(row.confidence.flags)


def test_the_stage_writes_no_positional_column_as_a_period(ppe_1966):
    """A whole-row citation of 1966's Depreciation row: its columns are classes, not years, so it is
    refused — nothing is written, and the row says to name the column."""
    _spy, row = _run(ppe_1966, ("14", "Depreciation 折舊"))
    labels = {ev.period_label for ev in row.values.values()} if row is not None else set()
    assert labels == set(), labels
    assert any("llm_citation_unresolved" in f and "give the `column`" in f
               for f in row.confidence.flags), row.confidence.flags


# ── THE WHOLE-ROW FENCE ──────────────────────────────────────────────────────────────────────────

def _whole(tables, note, caption):
    return resolve_sources([SourceRef(note=note, caption=caption)], tables)


def test_000709_a_whole_row_of_levels_is_refused_and_lists_its_columns(fv_000709):
    """Before: Level 3 filed as current and the same year-end's 合计 as prior."""
    resolved, unresolved = _whole(fv_000709, "十三、1", "（一）应收款项融资")
    assert resolved == []
    (bad,) = unresolved
    assert bad["why"].startswith("that row's columns are not periods — give the `column`")
    assert "第一层次公允价值计量" in bad["why"] and "合计" in bad["why"]
    assert len(bad["columns"]) == 4


def test_1966_a_whole_row_of_asset_classes_in_one_block_is_refused(ppe_1966):
    """The block is 2023: its `prior` key is the Leasehold improvements column, not 2022."""
    resolved, unresolved = _whole(ppe_1966, "14", "Depreciation 折舊")
    assert resolved == [] and "not periods" in unresolved[0]["why"]


def test_1966_fair_value_rows_in_year_blocks_are_refused_whole_but_answer_by_column():
    """Note 46 prints 2023 and 2022 as blocks of rows; Level 1 and Level 3 are the keyed columns.
    Whole, the 2022 row filed Level 1 as this year and Level 3 as last year."""
    tables = _tables("1966_fv", "46", "FAIR VALUE AND FAIR VALUE HIERARCHY", 254)
    rows = [ni for t in tables for ni in t.items if ni.raw_label.startswith("Financial assets")]
    assert [r.period_hint for r in rows] == ["current", "prior"]
    for row in rows:
        _kept, why = note_columns.whole_row(row)
        assert why.startswith("that row's columns are not periods"), row.period_hint
    assert note_columns.pick(rows[0], "Level 3")["figures"] == {"current": "344135"}
    assert note_columns.pick(rows[1], "Level 3")["figures"] == {"prior": "378539"}
    assert note_columns.pick(rows[1], "Total")["blank"] is True


def test_a_period_table_is_untouched_and_keeps_only_its_two_periods():
    """Headings whose printed period agrees with the key: read as before, less `col2` and the
    measures and the restatements."""
    row = _note_row(_ev("current", "10", "2024年12月31日", "current"),
                    _ev("prior", "9", "2023年12月31日", "prior"),
                    _ev("col2", "8", "2022年12月31日"),
                    _ev("current:allowance", "1", "期末 · 坏账准备", "current"),
                    _ev("current_col3", "7", "2024年12月31日（重述）", "current"))
    table = NotesTable(note_number="7", title="应收账款", items=[row])
    resolved, unresolved = _whole([table], "7", "应收账款")
    assert unresolved == []
    assert resolved[0]["figures"] == {"current": "10", "prior": "9"}
    assert resolved[0]["figures_by_basis"] == {"consolidated": {"current": "10", "prior": "9"}}


def test_an_unheaded_row_is_untouched_less_its_positions():
    row = _note_row(_ev("current", "10"), _ev("prior", "9"), _ev("col2", "8"))
    table = NotesTable(note_number="7", title="应收账款", items=[row])
    resolved, unresolved = _whole([table], "7", "应收账款")
    assert unresolved == [] and resolved[0]["figures"] == {"current": "10", "prior": "9"}


def test_a_grid_row_keeps_its_primary_measure():
    grid = (note_columns.GRID_FLAG,)
    row = _note_row(_ev("current", "100", "期末余额 · 账面余额", flags=grid),
                    _ev("current:allowance", "7", "期末余额 · 坏账准备", flags=grid))
    table = NotesTable(note_number="7", title="应收账款", items=[row])
    resolved, unresolved = _whole([table], "7", "应收账款")
    assert unresolved == [] and resolved[0]["figures"] == {"current": "100"}


def test_a_heading_dated_against_its_key_is_refused():
    row = _note_row(_ev("current", "10", "2023年12月31日", "prior"))
    table = NotesTable(note_number="7", title="应收账款", items=[row])
    resolved, unresolved = _whole([table], "7", "应收账款")
    assert resolved == [] and "not periods" in unresolved[0]["why"]


def test_a_block_row_whose_key_is_its_own_block_period_is_untouched():
    """1966's movement rows with the year on the block, where the one figure's key IS that year."""
    row = _note_row(_ev("current", "10", "Total 總計"), hint="current")
    table = NotesTable(note_number="14", title="PPE", items=[row])
    resolved, unresolved = _whole([table], "14", "应收账款")
    assert unresolved == [] and resolved[0]["figures"] == {"current": "10"}


def test_a_row_with_only_positional_figures_asks_for_a_column(ppe_1966):
    """"Acquisition of a subsidiary" prints only col2, col4, col8 — no period key at all."""
    resolved, unresolved = _whole(ppe_1966, "14", "Acquisition of a subsidiary")
    assert resolved == []
    assert unresolved[0]["why"].startswith("that row prints no current- or prior-period figure")


def test_a_kept_second_column_of_a_period_is_cited_by_its_key():
    """1966 note 30 keys its amounts `current_col1` (2023) and `prior_col3` (2022) — a period's
    second column. Whole, the row holds no filed period; by key, each is its own slot's period."""
    row = _note_row(_ev("current_col1", "9817976"), _ev("prior_col3", "10742959"))
    assert note_columns.whole_row(row)[1].startswith(
        "that row prints no current- or prior-period figure")
    got = note_columns.pick(row, "prior_col3")
    assert got["figures"] == {"prior": "10742959"} and got["assumed"] is False
    assert got["cells"][0]["period_source"] == "key"


def test_a_face_citation_keeps_only_its_periods():
    from app.core.models.line_item import LineItem
    face_row = LineItem(source_label="Trade receivables")
    face_row.set_value(_ev("current", "10", "2024"))
    face_row.set_value(_ev("current_restated", "11", "2024 (restated)"))
    resolved, unresolved = resolve_sources(
        [SourceRef(statement="balance_sheet", caption="Trade receivables")],
        [], [("balance_sheet", "Trade receivables", face_row)])
    assert unresolved == [] and resolved[0]["figures"] == {"current": "10"}
