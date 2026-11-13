"""A DATED CAPTION KEEPS ITS DIGITS OUT OF THE FIGURES — two reading defects the Notes screen showed
once every column was on it.

1. China SCE (hkex_1966_ar) prints "At 1 January 2023 於二零二三年一月一日" over its PP&E blocks. The
   date's 1 and 2023 were read as two figures in two columns of their own ("column 1 | column 2"),
   because the note rule that drops a date's digits from a row's figures knew only "As at …" with
   nothing after the year (`row_reconstruct._NOTE_DATED_CAPTION`).
2. 河钢 000709 七、3 heads two tables "① 2024 年12 月31 日，单独计提坏账准备" and "②2024 年12 月31 日，
   组合计提坏账准备". The year read as a figure and the comma as a sentence, so neither line was a
   heading: ① became a row "①" with a figure of 2024, ② was lost, and the ageing table under it was
   read as rows of the first table, at other column positions (`notes_extract._DATED_SUB_TABLE`).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.core.models.geometry import BBox
from app.services.notes_extract import _is_heading, extract_note_tables
from app.services.row_reconstruct import _NOTE_DATED_CAPTION, Word

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures/printed_columns_sections.json")
                 .read_text(encoding="utf-8"))


@pytest.mark.parametrize("caption, dated", [
    ("At 1 January 2023 於二零二三年一月一日", True),
    ("於二零二三年一月一日 At 1 January 2023", True),
    ("As at 31 March 2025", True),
    ("At 31 December 2022 and 1 January 2023", True),
    ("At 31 December 2022, 1 January 2023 於二零二二年十二月三十一日", True),
    ("At 1 January 2023, net of accumulated depreciation", False),
    ("At cost 2023", False),
    ("Attributable to owners", False),
])
def test_a_notes_dated_caption_is_recognised_in_either_script(caption, dated):
    assert bool(_NOTE_DATED_CAPTION.match(caption)) is dated


def test_china_sces_ppe_dates_are_no_longer_figures():
    words = [Word(text=t, bbox=BBox(x0=a, y0=b, x1=c, y1=d))
             for t, a, b, c, d in FIX["1966_ppe"]["words"]]
    tables = extract_note_tables(words, page_index=193, document_id="t", source_kind="native",
                                 carry_note=("14", "PROPERTY, PLANT AND EQUIPMENT"))
    rows = [r for t in tables for r in t.items]
    dated = [r for r in rows if r.raw_label.startswith("At ")]
    assert all(not r.values for r in dated), [(r.raw_label, list(r.values)) for r in dated]
    # The figures of the table are untouched: the year's depreciation still totals -102,572.
    dep = next(r for r in rows if r.raw_label.startswith("Depreciation"))
    assert "-102572" in {str(ev.value) for ev in dep.values.values()}


def _line(*tokens, y=0.30):
    x, out = 0.10, []
    for t in tokens:
        out.append(Word(text=t, bbox=BBox(x0=x, y0=y, x1=x + 0.02 * len(t), y1=y + 0.01)))
        x += 0.02 * len(t) + 0.01
    return out


@pytest.mark.parametrize("tokens, want", [
    (("①", "2024", "年12", "月31", "日，单独计提坏账准备"), ("1", "2024年12月31日单独计提坏账准备")),
    (("②2024", "年12", "月31", "日，组合计提坏账准备"), ("2", "2024年12月31日组合计提坏账准备")),
])
def test_a_dated_sub_table_heading_opens_its_own_table(tokens, want):
    assert _is_heading(_line(*tokens)) == want


def test_a_dated_row_with_a_figure_is_not_a_heading():
    assert _is_heading(_line("①", "2024", "年12", "月31", "日", "1,000,000")) is None
