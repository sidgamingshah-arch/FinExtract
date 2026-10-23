"""A Schedule III "Financial assets" / "Financial liabilities" heading is the heading of the rows
printed under it, not the first words of the first one.

Division II prints the heading alone, figure-less, over numbered rows:

    Non-current assets
      (d) Financial assets
          (i) Investments                  500
          (ii) Other financial assets       80

The face reader glued it onto the first row — "Financial assets Investments" (Laurus), "Financial
Assets Investments" (Asian Paints), "(d) Financial assets (i) Investments" — and the rows after it
carried no sub-heading. It is now split off (`row_reconstruct._SCHEDULE_III_FINANCIAL_HEADING`): the
row keeps its own caption and every row of the block carries the heading as its `group_hint`.

The split waited on the ICON set. The bare caption "Investments" is `bs_icon__group_investments`'s
own LABEL, so the non-current sweep refused it (prohibition 4, a dedicated concept's own untested
caption) and it reached no line: Laurus Total assets 10511.19 -> 10201.92 and Asian Paints 34534.49
-> 34414.67 against the printed 10511.19 and 34534.49. The ICON set now EXCLUDES the bare caption
from group investments (`captions.json`), so the sweep files it into {Others} exactly as it filed the
joined caption, and Total assets is the printed figure again.

A caption that merely begins with the words is not a heading: "Financial assets at fair value
through profit or loss" (Lai Sun, PetroChina) stays whole.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.core.models.geometry import BBox
from app.services.row_reconstruct import Word, build_line_items

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
ICON = TEMPLATES / "output_csv_icon_line_items.json"
B = "bs_icon__"

_W, _H = 595.0, 842.0
_CHAR, _SPACE = 4.3, 2.2
_LEFT, _INDENT, _DEEP = 62.0, 72.0, 84.0
_CUR, _PRI = 425.0, 520.0
_STEP = 12.0


def _line(text: str, x0: float, y: float) -> list[Word]:
    out, x = [], x0
    for t in text.split():
        out.append(Word(text=t, bbox=BBox(x0=x / _W, y0=y / _H,
                                          x1=(x + _CHAR * len(t)) / _W, y1=(y + 8) / _H)))
        x += _CHAR * len(t) + _SPACE
    return out


def _right(text: str, x1: float, y: float) -> list[Word]:
    return _line(text, x1 - _CHAR * len(text), y)


def _read(*lines, tight: float | None = None):
    """``lines`` are (caption, x0) or (caption, x0, current, prior). With ``tight``, a figure-less
    line is printed that close above the next one, so row clustering merges the two — the
    geometry the Ind AS faces print the heading in."""
    words: list[Word] = _right("2026", _CUR, 100.0) + _right("2025", _PRI, 100.0)
    y = 120.0
    for spec in lines:
        caption, x0 = spec[0], spec[1]
        words += _line(caption, x0, y)
        if len(spec) > 2:
            words += _right(spec[2], _CUR, y) + _right(spec[3], _PRI, y)
        y += tight if (tight is not None and len(spec) == 2) else _STEP
    items, _ = build_line_items(words, page_index=4, document_id=None, source_kind="native",
                                statement="balance_sheet", on_face=True)
    return [(li.source_label, li.group_hint) for li in items]


# ── the reader ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tight", [None, 9.0])
def test_a_financial_assets_heading_heads_its_rows_and_is_no_caption(tight):
    """Laurus: "Financial assets" over "Investments", printed on its own line or tight enough
    above it that the two are clustered into one row."""
    got = _read(
        ("Non-current assets", _LEFT),
        ("Other Intangible assets", _INDENT, "16.48", "17.63"),
        ("Financial assets", _INDENT),
        ("Investments", _DEEP, "890.94", "740.22"),
        ("Other financial assets", _DEEP, "36.68", "44.01"),
        ("Loans", _DEEP, "501.50", "466.50"),
        ("Other non-current assets", _INDENT, "137.85", "105.50"),
        tight=tight,
    )
    captions = dict(got)
    assert "Investments" in captions, got
    assert not any(c.lower().startswith("financial assets") for c, _g in got), got
    assert captions["Investments"] == "Financial assets"
    assert captions["Other financial assets"] == captions["Loans"] == "Financial assets"
    # Set back to the heading's own edge, the next row has left its block.
    assert captions["Other non-current assets"] != "Financial assets"


def test_a_financial_liabilities_heading_heads_the_borrowings():
    """Asian Paints' title case: "Financial Liabilities" tight above "Borrowings"."""
    got = dict(_read(
        ("Non-Current Liabilities", _LEFT),
        ("Financial Liabilities", _INDENT),
        ("Borrowings", _DEEP, "848.94", "37.25"),
        ("Lease Liabilities", _DEEP, "908.29", "900.80"),
        tight=9.0,
    ))
    assert got.get("Borrowings") == "Financial Liabilities", got
    assert got.get("Lease Liabilities") == "Financial Liabilities", got


def test_a_numbered_division_ii_heading_heads_its_numbered_rows():
    """Division II as printed: "(d) Financial assets" over "(i) Investments"."""
    got = _read(
        ("Non-current assets", _LEFT),
        ("(c) Other intangible assets", _INDENT, "100", "120"),
        ("(d) Financial assets", _INDENT),
        ("(i) Investments", _DEEP, "500", "450"),
        ("(ii) Other financial assets", _DEEP, "80", "70"),
        ("(e) Other non-current assets", _INDENT, "120", "110"),
        tight=9.0,
    )
    captions = dict(got)
    assert captions.get("(i) Investments") == "(d) Financial assets", got
    assert captions.get("(ii) Other financial assets") == "(d) Financial assets", got
    assert not any("Financial assets (i)" in c for c, _g in got), got


def test_a_caption_that_begins_with_the_words_is_not_a_heading():
    """Lai Sun / PetroChina print captions of their own that open with "Financial assets"."""
    got = _read(
        ("Non-current assets", _LEFT),
        ("Financial assets at fair value through profit or loss", _INDENT, "1,234", "1,100"),
        ("Financial assets at fair value through other comprehensive income", _INDENT,
         "567", "600"),
    )
    captions = [c for c, _g in got]
    assert "Financial assets at fair value through profit or loss" in captions, got
    assert "Financial assets at fair value through other comprehensive income" in captions, got


@pytest.mark.parametrize("text,heading", [
    ("Financial assets", True), ("Financial Liabilities", True), ("(b) Financial assets", True),
    ("b. Financial assets", True),
    ("Financial assets at fair value through profit or loss", False),
    ("Other financial assets", False), ("Other financial liabilities", False),
])
def test_only_the_bare_heading_is_a_heading(text, heading):
    from app.services.row_reconstruct import _is_face_group_heading

    assert _is_face_group_heading(text) is heading


# ── the ICON set ────────────────────────────────────────────────────────────────────────────────

def _icon() -> dict:
    return json.loads(ICON.read_text(encoding="utf-8"))


def test_the_bare_investments_caption_is_no_untested_group_investments_row():
    """Prohibition 4 indexes a dedicated concept's LABEL, and group investments is labelled
    "Investments": unexcluded, the bare row was refused by the sweep and reached no line."""
    from app.schemas.line_items import load_line_item_set
    from app.services.rollups import section_members
    from app.services.working_view import build_working_view
    from app.stages.residual import _dedicated_captions, _untested_dedicated

    ontology = build_working_view(load_line_item_set(_icon()))
    captions = _dedicated_captions(ontology, section_members(ontology))
    for caption in ("Investments", "(i) Investments"):
        assert _untested_dedicated(captions, "bs_nca", caption) is None, caption
    # …and only the bare caption: a row that names the group is still group investments'.
    assert _untested_dedicated(captions, "bs_nca", "Investments in subsidiaries") \
        == B + "group_investments"


def test_the_exclusion_refuses_the_bare_caption_alone():
    item = next(i for i in _icon()["items"] if i["key"] == B + "group_investments")
    hints = item["exclude_hints"]

    def refused(caption: str) -> bool:
        return any(re.search(h, caption.lower()) for h in hints)

    assert refused("Investments") and refused("(i) Investments") and refused("i. Investments")
    for caption in ("Investments in subsidiaries", "Investments in associates",
                    "Investment in group companies", "Other investments"):
        assert not refused(caption), caption


def test_a_schedule_iii_face_split_at_its_heading_still_reaches_total_assets():
    """End to end on the ICON pair: the heading is no part of a caption, the bare non-current
    "(i) Investments" is swept into {Others}, and every printed total is reached."""
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    from tests.test_the_icon_set import _schedule_iii_pdf, _spread

    shown, rows = _spread(_schedule_iii_pdf())
    captions = {r.get("source_label") or r.get("label") or "" for r in rows}
    assert not any("Financial assets (i)" in c or "Financial liabilities (i)" in c
                   for c in captions), sorted(captions)
    invest = [r for r in rows if (r.get("source_label") or r.get("label")) == "(i) Investments"]
    assert {r.get("canonical_key") for r in invest} == {
        B + "other_non_current_assets_others", B + "other_current_assets_others"}, invest
    assert shown[B + "total_other_non_current_assets"] == 500 + 80 + 120
    assert shown[B + "total_assets"] == 10300
    assert shown[B + "difference_in_bs"] == 0
