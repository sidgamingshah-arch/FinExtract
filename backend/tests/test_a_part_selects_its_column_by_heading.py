r"""A NOTE PART MAY SELECT ITS COLUMN BY THE HEADING THE FILING PRINTS OVER IT.

`NoteSource.column_heading_any` / `column_heading_none` are regex lists read through
`services.note_columns.matches_patterns` — the one definition a model's column citation reads
through too. Both empty, the default, and `select_rows` reads exactly as it did.

WHY. Every fair-value hierarchy table on the five reference filings prints its levels as COLUMNS,
and `period_label` is positional: on 河钢股份 000709's table `current` is the Level 3 column and
`prior` is the SAME year-end's 合计; on China SCE 1966's the Level 3 column is keyed `prior` in both
year blocks. A part reading Level 3 by row caption selected nothing anywhere, and a part reading it
by key would have filed a year-end 合计 as the prior year.

These cases are the shapes, built from plain namespaces so each rule is pinned on its own:

  * the selector is ANDed after the row gates and stands in for the positional key;
  * a selected figure is filed under `note_columns.period_of` — the period over the column, else
    the row's block — and refused when nothing printed states one;
  * a figure whose column the reader could not name FAILS CLOSED; a veto wins;
  * one figure per (row, basis, period): two admitted columns with different figures are refused;
  * a column-selected `\S` reader is not kept out of a block-period table;
  * the trail names the column; a broken column pattern is refused at load and named at run.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from app.schemas.line_items import NoteSource
from app.services import note_sourced
from app.services.line_item_audit import _NOTE_SOURCE_GATE_PAIRS


def V(value, heading=None, period_label="current", column_period=None, column_index=None,
      flags=()):
    return NS(value=Decimal(str(value)), value_raw=None, period_label=period_label,
              column_heading=heading, column_period=column_period, column_index=column_index,
              basis="consolidated", confidence=NS(flags=list(flags)), provenance=None)


def R(caption, *values, period_hint="", total_slot=""):
    return NS(raw_label=caption, group_hint="", period_hint=period_hint, total_slot=total_slot,
              values={f"v{i}": v for i, v in enumerate(values)})


def T(*rows, number="46", title="Fair value hierarchy"):
    return NS(title=title, note_number=number, chapter_title="", basis=None, items=list(rows))


def part(rows=(r"\S",), **selector):
    return NS(key="sub__probe_level_3", note_source=NoteSource(
        note_title_any=[r"fair value"], row_caption_any=list(rows), **selector))


LEVEL3 = {"column_heading_any": [r"第三层次|第三級|level\s*(3|three)"],
          "column_heading_none": [r"第[一二]|level\s*(1|2)|合计|total"]}
PERIODS = {"current", "prior"}


def _picked(hits):
    return sorted((h.period, h.amount) for h in hits)


def test_the_default_is_empty_and_reads_as_before() -> None:
    src = NoteSource()
    assert (src.column_heading_any, src.column_heading_none) == ([], [])
    # CAS shape: Level 3 keyed `current`, the same year-end's 合计 keyed `prior`.
    note = T(R("交易性金融资产",
               V(909834646.75, "第三层次公允价值计量", "current", "current"),
               V(1321518115.73, "合计", "prior", "current")))
    hits = note_sourced.select_rows(part(), [note], PERIODS)
    # Without a selector the positional keys are read as periods — the defect the selector exists
    # to avoid, pinned here so the default visibly did not change.
    assert _picked(hits) == [("current", Decimal("909834646.75")),
                             ("prior", Decimal("1321518115.73"))]


def test_the_level_3_column_is_read_and_filed_under_the_period_printed_over_it() -> None:
    note = T(R("交易性金融资产",
               V(909834646.75, "第三层次公允价值计量", "current", "current"),
               V(1321518115.73, "合计", "prior", "current")))
    hits = note_sourced.select_rows(part(**LEVEL3), [note], PERIODS)
    assert _picked(hits) == [("current", Decimal("909834646.75"))]
    assert hits[0].column == "第三层次公允价值计量"
    assert hits[0].period_source == "column"


def test_a_block_table_files_each_figure_under_its_rows_year() -> None:
    """1966 note 46: the years are blocks of rows and Level 3 is keyed `prior` in both."""
    heading_1 = "Quoted prices in active market (Level 1) 活躍市場報價（第一級）"
    heading_3 = "Significant unobservable inputs (Level 3) 重大不可觀察輸入數據（第三級）"
    note = T(
        R("Financial assets at fair value through profit or loss",
          V(344135, heading_3, "prior"), period_hint="current"),
        R("Financial assets at fair value through profit or loss",
          V(53434, heading_1, "current"), V(378539, heading_3, "prior"), period_hint="prior"))
    # `\S` is a line-item sum, which a block-period table would otherwise keep out entirely.
    hits = note_sourced.select_rows(part(**LEVEL3), [note], PERIODS)
    assert _picked(hits) == [("current", Decimal("344135")), ("prior", Decimal("378539"))]
    assert {h.period_source for h in hits} == {"row_block"}
    # …and the row-only reader still keeps out of it, as before.
    assert note_sourced.select_rows(part(), [note], PERIODS) == []


def test_the_selector_stands_in_for_the_block_total_column() -> None:
    """A movement row with a block period and a total slot reads the SELECTED column, not the
    total — the selector is the part's own statement of which column it wants."""
    note = T(R("Depreciation charge",
               V(16847, "Leased properties", "current"), V(77707, "Total", "col3"),
               period_hint="current", total_slot="col3"))
    plain = note_sourced.select_rows(part(rows=[r"depreciation"]), [note], PERIODS)
    assert _picked(plain) == [("current", Decimal("77707"))]
    chosen = note_sourced.select_rows(
        part(rows=[r"depreciation"], column_heading_any=[r"leased"]), [note], PERIODS)
    assert _picked(chosen) == [("current", Decimal("16847"))]


def test_an_unnamed_column_fails_closed_and_an_unstated_period_is_refused() -> None:
    note = T(R("交易性金融资产",
               V(1, None, "current", "current"),                  # no heading at all
               V(2, "第三层次", "prior", None)))                   # heading, but no period printed
    assert note_sourced.select_rows(part(**LEVEL3), [note], PERIODS) == []


def test_the_veto_wins_and_column_index_is_lifted_only_for_a_selected_value() -> None:
    note = T(R("交易性金融资产",
               V(5, "第三层次 合计", "current", "current", column_index=2),
               V(7, "第三层次", "prior", "prior", column_index=3),
               V(9, "第二层次", "col2", "prior", column_index=1)))
    hits = note_sourced.select_rows(part(**LEVEL3), [note], PERIODS)
    assert _picked(hits) == [("prior", Decimal("7"))]
    # A row-only part still refuses every matrix value.
    assert note_sourced.select_rows(part(), [note], PERIODS) == []


def test_two_admitted_columns_in_one_period_are_one_figure_or_none() -> None:
    same = T(R("FVTPL", V(10, "Level 3", "current", "current"),
               V(10, "第三級", "prior", "current")))
    assert _picked(note_sourced.select_rows(part(**LEVEL3), [same], PERIODS)) == [
        ("current", Decimal("10"))]
    differ = T(R("FVTPL", V(10, "Level 3", "current", "current"),
                 V(11, "第三級", "prior", "current")))
    assert note_sourced.select_rows(part(**LEVEL3), [differ], PERIODS) == []


def test_the_row_gates_still_apply_first() -> None:
    note = T(R("Financial liabilities", V(4, "Level 3", "current", "current")),
             R("Financial assets", V(6, "Level 3", "current", "current")))
    item = NS(key="sub__probe", note_source=NoteSource(
        note_title_any=[r"fair value"], row_caption_any=[r"financial"],
        row_caption_none=[r"liabilit"], **LEVEL3))
    hits = note_sourced.select_rows(item, [note], PERIODS)
    assert _picked(hits) == [("current", Decimal("6"))]


def test_the_trail_names_the_column() -> None:
    note = T(R("交易性金融资产", V(9, "第三层次", "current", "current")))
    hit, = note_sourced.select_rows(part(**LEVEL3), [note], PERIODS)
    entry = note_sourced._trail_input(hit, counted=True)
    assert entry["column"] == "第三层次"
    assert "column '第三层次' matched" in entry["excerpt"]
    assert "current from the column" in entry["excerpt"]
    # A row-only hit's trail is exactly what it was.
    plain, = note_sourced.select_rows(part(), [T(R("x", V(1)))], PERIODS)
    entry = note_sourced._trail_input(plain, counted=True)
    assert "column" not in entry and entry["excerpt"] == "note 'Fair value hierarchy' row matched /\\S/"


def test_a_broken_column_pattern_is_refused_at_load_and_named_at_run() -> None:
    with pytest.raises(ValueError, match=r"column_heading_any\[0\]"):
        NoteSource(column_heading_any=["("])
    with pytest.raises(ValueError, match=r"column_heading_none\[1\]"):
        NoteSource(column_heading_none=["ok", "[unclosed"])
    item = NS(key="sub__probe", note_source=NoteSource.model_construct(
        **{**NoteSource().model_dump(), "note_title_any": ["fair value"], "row_caption_any": [r"\S"],
           "column_heading_any": ["("]}))
    assert note_sourced.bad_patterns(item) == ["sub__probe.note_source.column_heading_any: /(/"]
    # An admit list none of whose patterns compiles admits nothing, rather than every column.
    note = T(R("x", V(1, "Level 3", "current", "current")))
    assert note_sourced.select_rows(item, [note], PERIODS) == []


def test_the_audit_knows_the_pair() -> None:
    assert ("column_heading_any", "column_heading_none") in _NOTE_SOURCE_GATE_PAIRS
