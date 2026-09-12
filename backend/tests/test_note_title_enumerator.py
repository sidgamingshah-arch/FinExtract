"""A NOTE HEADING ARRIVES CARRYING AN ENUMERATOR, and every anchored pattern was defeated by it.

NEW FILE -> backend/tests/test_note_title_enumerator.py

MEASURED: 1,486 of 3,359 note headings across the 18-filing corpus (44%) begin with a BARE
separator — "、 公司概况", "、其他应收款" — because the heading is extracted from a line whose Han
enumerator ("五、") has been split off, leaving the separator at the front of the title.

Almost every authored `note_title_any` is anchored with an optional ARABIC enumerator and nothing
else::

    ^\\s*(?:\\d+[.、)]?\\s*)?其他应收款

That group cannot pass a leading "、", so `sub__rp_other_receivables_note` matched NONE of its own
notes on 11 of 18 filings while its pattern spells that exact phrase. And the cost is not one
missing table: `note_context.identified_notes` is what decides whether a note reaches a request at
all, so a pattern that misses there withholds the note's ROWS **and** its PROSE from the model, and
the line comes back empty for a reason no log states.

WHAT IS ASSERTED HERE: that a pattern reaches its note whatever enumerator precedes it, that
nothing in the MIDDLE of a heading is loosened, that a row CAPTION is still matched strictly (a
caption carries no enumerator, so stripping one would be widening a pattern for nothing), and that
the note which then travels carries its prose.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.core.models.line_item import NoteItem, NotesTable
from app.schemas.line_items import load_line_item_set
from app.services.note_context import identified_notes, matches_title, title_variants

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


# ── the helper ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("、其他应收款", "其他应收款"),
    ("、 其他应收款", "其他应收款"),
    ("）其他应收款按款项性质分类情况", "其他应收款按款项性质分类情况"),
    ("14. PROPERTY AND EQUIPMENT", "PROPERTY AND EQUIPMENT"),
    ("五、 固定资产", "固定资产"),
])
def test_the_leading_enumerator_is_offered_stripped(title, expected):
    variants = title_variants(title)
    assert variants[0] == title, "the heading as extracted must be tried FIRST and unchanged"
    assert expected in variants, f"{title!r} -> {variants}"


def test_a_heading_with_no_enumerator_yields_one_variant():
    """So the 56% of headings that never had the problem pay nothing for the fix."""
    assert title_variants("REVENUE") == ("REVENUE",)


def test_nothing_in_the_middle_of_a_heading_is_touched():
    """Only the LEADING run is removed. A separator inside a heading is part of its name —
    "應收款項、其他應收款項" is one compound caption, not two."""
    inner = "預付款項、其他應收款項及其他資產"
    assert title_variants(inner) == (inner,)


# ── the behaviour that matters ────────────────────────────────────────────────────────────────

def test_an_anchored_pattern_reaches_its_note_whatever_precedes_it(shipped):
    """THE CASE THIS FILE EXISTS FOR, on the shipped pattern rather than an invented one."""
    import re

    item = {i.key: i for i in shipped.items}["sub__rp_other_receivables_note"]
    patterns = [re.compile(p, re.IGNORECASE) for p in item.note_source.note_title_any]
    assert patterns, "the line declares no note pattern, so this test proves nothing"

    for title in ("其他应收款", "、其他应收款", "、 其他应收款", "五、 其他应收款"):
        assert any(matches_title(p, title) for p in patterns), title
    # …and it still does NOT claim a note it has nothing to do with.
    for title in ("、 公司概况", "、编制基础", "REVENUE"):
        assert not any(matches_title(p, title) for p in patterns), title


def test_the_note_travels_with_its_prose(shipped):
    """The delivery half of the fix. A pattern that matches is worth nothing if the note reaches
    the request without the narrative — for a line whose figure is stated only in a sentence, the
    prose IS the evidence, and a note with no extracted table still has to carry it."""
    number = "五、15"
    prose = ("本公司对其他应收款的预期信用损失按照组合计提坏账准备，金额为 36,772,000 元，"
             "其中账龄一年以内的部分占比最大。")
    table = NotesTable(note_number=number, title="、其他应收款", source_text=prose)
    # No rows at all: exactly the shape that makes the prose route the only route.
    entries = identified_notes(shipped, [table])

    mine = [e for e in entries if str(e.get("note")) == number]
    assert mine, "the note was not identified at all, so nothing could travel"
    assert not (mine[0].get("rows") or []), "this fixture is meant to have no extracted rows"
    assert "36,772,000" in str(mine[0].get("prose") or ""), (
        "the note reached the request without the sentence that carries its figure")


def test_a_row_caption_is_still_matched_strictly(shipped):
    """A CAPTION IS NOT A HEADING. It carries no enumerator, so stripping a leading character from
    one would widen a pattern for no reason — `_matches_any` is left alone and only
    `_matches_title_any` is tolerant. Asserted through the row selector's own helper."""
    import re

    from app.services.note_sourced import _matches_any, _matches_title_any

    compiled = [("probe", re.compile(r"^depreciation", re.IGNORECASE))]
    # A heading is reached through the enumerator…
    assert _matches_title_any("14. Depreciation and amortisation", compiled)
    # …and a caption is not given the same licence.
    assert _matches_any("Depreciation charge", compiled)
    assert _matches_any("Accumulated depreciation", compiled) is None
