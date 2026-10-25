"""A NOTE THAT GIVES ITS COMPARATIVE A PAGE OF ITS OWN: that page is the prior year.

NEW FILE -> backend/tests/test_a_notes_comparative_page_is_its_prior_year.py

THE DEFECT, measured on Far East Horizon (3360). Note 47 prints its fair-value hierarchy once per
year-end, each on its own page:

    page 345   As at 31 December 2024     Level 1   Level 2     Level 3      Total
               Financial assets at FVTPL  327,835   4,101,274   11,330,161   15,759,270
    page 346   As at 31 December 2023
               Financial assets at FVTPL  486,910   4,601,637    3,753,048    8,841,595

A block's period is read from its "As at" caption against the newest year THE NOTE names, and that
was decided per page, so on page 346 the newest year was 2023 and its block was `current`. The Level
3 part (`sub__fa_cp_level_3_total`, the spec's Find 3) then read 11,330,161 + 3,753,048 =
15,083,209 as the 2024 total and nothing for 2023. Lai Sun (0191) did the same with July 2025 and
July 2024: 289,040 + 304,175 = 593,215. The same shape put both years of Far East Horizon's PP&E
depreciation on 2024 (3,151,116 + 2,646,961).

The note's newest year now carries across its pages (`extract_note_tables`' ``note_years``), and
only by the one step a comparative page takes. Geometry is synthetic, in the shape the filing prints.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.core.models.geometry import BBox
from app.schemas.line_items import load_line_item_set
from app.services import note_sourced
from app.services.notes_extract import extract_note_tables
from app.services.row_reconstruct import Word

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
H = 0.0105
COLUMNS = (0.50, 0.62, 0.74, 0.86)
TITLE = "FAIR VALUE HIERARCHY OF FINANCIAL INSTRUMENTS"


def _w(text: str, x0: float, y: float, x1: float | None = None) -> Word:
    x1 = x1 if x1 is not None else x0 + 0.0105 * len(text)
    return Word(text=text, bbox=BBox(x0=x0, y0=y, x1=x1, y1=y + H))


def _caption(text: str, x0: float, y: float, pitch: float = 0.0068) -> list[Word]:
    out, x = [], x0
    for token in text.split():
        out.append(_w(token, x, y, x + pitch * len(token)))
        x += pitch * len(token) + 0.005
    return out


def _hierarchy_page(heading: str, year: int, figures: tuple[str, ...]) -> list[Word]:
    """One year-end's hierarchy table under the note's heading: an "As at" caption opening the
    block, the four level columns, an asset row and a liability row."""
    words = _caption(heading, 0.06, 0.08) + _caption(f"As at 31 December {year}", 0.06, 0.12)
    for x, head in zip(COLUMNS, ("Level 1", "Level 2", "Level 3", "Total")):
        words += _caption(head, x, 0.14)
    words += [_w("RMB'000", x, 0.16) for x in COLUMNS]
    words += _caption("Financial assets at fair value through profit or loss", 0.06, 0.19)
    words += [_w(f, x + 0.01, 0.19) for x, f in zip(COLUMNS, figures)]
    words += _caption("Other non-current liabilities", 0.06, 0.21)
    words += [_w(f, x + 0.01, 0.21)
              for x, f in zip(COLUMNS, ("–", "–", "(1,564,467)", "(1,564,467)"))]
    return words


CURRENT = _hierarchy_page(f"47. {TITLE}", 2024, ("327,835", "4,101,274", "11,330,161", "15,759,270"))
COMPARATIVE = _hierarchy_page(f"47. {TITLE} (continued)", 2023,
                              ("486,910", "4,601,637", "3,753,048", "8,841,595"))


def _read_pages(*pages: list[Word], note_years: dict | None) -> list:
    """The pages read in order, the open note carried from each to the next as the extractor does."""
    tables, carry = [], None
    for index, words in enumerate(pages, start=345):
        opened: list = []
        tables += extract_note_tables(words, page_index=index, document_id="d",
                                      source_kind="native", carry_note=carry, open_out=opened,
                                      note_years=note_years)
        carry = opened[-1] if opened else carry
    return tables


def _hints(tables) -> list[tuple[list[int], str]]:
    return [(t.source_pages, li.period_hint) for t in tables for li in t.items
            if li.raw_label.startswith("Financial assets")]


@pytest.fixture(scope="module")
def level_3_part():
    st = load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)
    item = next(i for i in st.items if i.key == "sub__fa_cp_level_3_total")
    # The note is named by title, so the row and column rules are what is under test — finding the
    # note by meaning is `line_item_notes`' business, pinned there.
    return item.model_copy(update={"note_source": item.note_source.model_copy(
        update={"note_title_any": [r"fair value"]})})


def _level_3(part, tables) -> list[tuple[str, Decimal]]:
    return sorted((h.period, h.amount)
                  for h in note_sourced.select_rows(part, tables, {"current", "prior"}))


def test_the_comparative_page_of_a_note_is_its_prior_year(level_3_part):
    years: dict = {}
    tables = _read_pages(CURRENT, COMPARATIVE, note_years=years)

    assert [t.note_number for t in tables] == ["47", "47"]
    assert _hints(tables) == [([345], "current"), ([346], "prior")]
    assert years == {"47": 2024}
    assert _level_3(level_3_part, tables) == [("current", Decimal("11330161")),
                                              ("prior", Decimal("3753048"))]


def test_read_a_page_at_a_time_the_two_years_were_one(level_3_part):
    """The defect, kept as the premise: with no memo each page decides its own newest year, so the
    comparative page's 2023 block is `current` and both Level 3 figures land on 2024."""
    tables = _read_pages(CURRENT, COMPARATIVE, note_years=None)

    assert _hints(tables) == [([345], "current"), ([346], "current")]
    assert _level_3(level_3_part, tables) == [("current", Decimal("3753048")),
                                              ("current", Decimal("11330161"))]


def test_only_the_year_before_is_lifted():
    """A page two years behind the note is not its comparative, and keeps what it names."""
    two_behind = _hierarchy_page(f"47. {TITLE} (continued)", 2022,
                                 ("1", "2", "3", "6"))
    years = {"47": 2024}
    assert _hints(_read_pages(two_behind, note_years=years)) == [([345], "current")]
    assert years == {"47": 2024}


def test_a_later_page_naming_a_newer_year_keeps_it():
    years = {"47": 2023}
    assert _hints(_read_pages(CURRENT, note_years=years)) == [([345], "current")]
    assert years == {"47": 2024}


def test_another_notes_year_does_not_carry():
    years = {"46": 2024}
    assert _hints(_read_pages(COMPARATIVE, note_years=years)) == [([345], "current")]
    assert years == {"46": 2024, "47": 2023}
