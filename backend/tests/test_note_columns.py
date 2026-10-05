"""One definition of which printed column a figure is in, and which period that column is.

`services.note_columns` is what a model's column citation and a configured column selector will
both read through, so the rules are pinned here once: headings compare normalised and by the
closest containment, patterns search both script halves and fail closed on a missing heading, and
a column's period comes from what the filing PRINTS — never from the positional key.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from app.services import note_columns, row_reconstruct
from app.services.note_columns import heading_matches, matches_patterns, normalise, period_of


def test_the_grid_flag_is_the_readers_own():
    assert note_columns.GRID_FLAG == row_reconstruct.GRID_FLAG


# ── normalise ────────────────────────────────────────────────────────────────────────────────────

def test_normalise_folds_width_script_case_joiner_and_spacing():
    assert normalise("期末公允价值 · 第三层次公允价值计量") == "期末公允价值 第三层次公允价值计量"
    assert normalise("（第三級）") == normalise("(第三级)")
    assert normalise("  Total   總計 ") == "total 总计"
    assert normalise(None) == "" and normalise("") == ""


# ── heading_matches ──────────────────────────────────────────────────────────────────────────────

def test_an_exact_heading_scores_one_whole_or_by_either_script_half():
    assert heading_matches("Total 總計", "total 总计") == 1.0
    assert heading_matches("Total 總計", "Total") == 1.0
    assert heading_matches("Total 總計", "總計") == 1.0


def test_containment_scores_by_closeness_of_length():
    level3 = "Significant unobservable inputs (Level 3) 重大不可觀察輸入數據（第三級）"
    subtotal = heading_matches("Subtotal 小計", "total")
    total = heading_matches("Total 總計", "total")
    assert total == 1.0 and 0 < subtotal < 1
    short = heading_matches(level3, "Level 3")
    longer = heading_matches(level3, "unobservable inputs (Level 3)")
    assert 0 < short < longer < 1
    # among two headings containing the words, the one closest in length scores higher
    assert heading_matches("第三层次公允价值计量", "第三层次") > \
        heading_matches("期末公允价值 · 第三层次公允价值计量", "第三层次")


def test_no_match_and_no_heading_score_zero():
    assert heading_matches("Level 1 第一級", "Level 3") == 0.0
    assert heading_matches(None, "Level 3") == 0.0
    assert heading_matches("Level 3", "") == 0.0


# ── matches_patterns ─────────────────────────────────────────────────────────────────────────────

LEVEL3 = [r"level\s*3\b", r"第三(?:层次|層次|级|級)"]
NOT_TOTAL = [r"\btotal\b", r"合计|合計|总计|總計"]


def test_patterns_search_whole_heading_and_each_script_half():
    assert matches_patterns("Significant unobservable inputs (Level 3) 重大不可觀察輸入數據（第三級）",
                            LEVEL3, NOT_TOTAL)
    assert matches_patterns("期末公允价值 · 第三层次公允价值计量", LEVEL3, NOT_TOTAL)
    # an anchored pattern finds the Chinese half on its own
    assert matches_patterns("Level 3 第三級", [r"^第三級$"])
    assert not matches_patterns("Level 2 第二級", LEVEL3, NOT_TOTAL)


def test_a_veto_wins_and_compiled_patterns_are_accepted():
    assert not matches_patterns("期末公允价值 · 合计", [r"期末"], NOT_TOTAL)
    assert matches_patterns("Total 總計", [re.compile("TOTAL", re.IGNORECASE)])


def test_a_missing_heading_fails_closed():
    assert not matches_patterns(None, LEVEL3)
    assert not matches_patterns("", [])
    assert not matches_patterns(None, [], [])
    # with no pattern on either side, a named column is selected
    assert matches_patterns("Level 1 第一級", [], [])


# ── period_of ────────────────────────────────────────────────────────────────────────────────────

def _value(label, *, column_period=None, flags=()):
    return SimpleNamespace(period_label=label, column_period=column_period,
                           confidence=SimpleNamespace(flags=list(flags)))


def test_the_column_period_comes_first():
    # 000709's fair-value table: key `prior` is the SAME year-end's 合计 under 期末公允价值
    assert period_of(_value("prior", column_period="current"),
                     SimpleNamespace(period_hint="prior")) == ("current", "column")


def test_then_the_rows_block_period():
    # 1966 note 46: the 2022 block's Level 3 cell is keyed `prior` by position and by block alike,
    # and the 2023 block's lone Level 3 cell is keyed `prior` while its block is the current year
    assert period_of(_value("prior"), SimpleNamespace(period_hint="current")) == ("current",
                                                                                  "row_block")


def test_then_the_base_of_a_grid_key_and_only_a_grid_key():
    grid = _value("current:allowance", flags=[row_reconstruct.GRID_FLAG])
    assert period_of(grid, SimpleNamespace(period_hint="")) == ("current", "grid")
    assert period_of(_value("prior:net", flags=[row_reconstruct.GRID_FLAG])) == ("prior", "grid")
    # a positional key is a position, not a period
    assert period_of(_value("current")) == (None, "")
    assert period_of(_value("col2", flags=[row_reconstruct.GRID_FLAG])) == (None, "")


def test_nothing_stated_is_none_and_a_bare_namespace_is_read_safely():
    assert period_of(SimpleNamespace(), None) == (None, "")
    assert period_of(_value("prior", column_period="nonsense"),
                     SimpleNamespace(period_hint="")) == (None, "")
