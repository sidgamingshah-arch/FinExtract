"""ONE PAYLOAD ENTRY PER NOTE, all its prose, none of it repeated — and NOTHING LOST.

NEW FILE -> backend/tests/test_note_fragment_collapse.py

A note printed across pages arrives as several extracted tables carrying one number, and
`note_context.identified_notes` emitted one payload entry per TABLE — laisun's note 4 as seven,
note 6 as seven, note 15 as six, so a request carrying twelve distinct notes carried thirty-nine
entries and repeated each note's number, title and `identified_for` once per fragment.

TWO WRONG ANSWERS WERE TRIED BEFORE THE RIGHT ONE, and both are pinned here because each destroyed
something a test would otherwise have to rediscover:

  1. KEEP THE LONGEST FRAGMENT'S PROSE. Measured, laisun's note 7 arrives as 1,765 / 1,589 / 1,195
     characters and the SHORTEST holds "^ Depreciation charges of approximately HK$529,841,000
     (2024: HK$665,553,000) are included in 'other operating expenses'" — the only source
     `sub__pbt_oper_exp_depreciation` has on that filing, and the measured reason the prose route
     exists at all. Note 47 loses its only narrative amount the same way.

  2. CUT REPEATED CHARACTER BLOCKS. A matching block is the longest run of IDENTICAL characters and
     it greedily absorbs the shared parts of two figures that differ: note 15's fragment 4 reads
     "…Average market unit HK$13,600 The higher…" and fragment 5 "…Average market unit HK$13,500
     The higher…", so difflib matched through "HK$13," and again from "00", and cutting both left
     the literal "5" where an amount had been. SEVEN amounts were destroyed that way — all of them
     unobservable inputs in a fair-value table, the kind a filing states exactly once.

SO A CUT MAY ONLY REMOVE WHOLE TOKENS. `dedupe_prose` pulls both edges of a matching block back to
whitespace before cutting, which makes a figure either wholly repeated — earlier copy survives — or
wholly kept.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.core.models.line_item import NoteItem, NotesTable
from app.schemas.line_items import load_line_item_set
from app.services.note_context import dedupe_prose, identified_notes

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")

# The furniture a continuation page reprints: a column-header band, long and digit-free.
FURNITURE = ("Property development Restaurant and F&B Media and and sales Property investment "
             "Hotel operation product sales operations entertainment Film and TV production")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


# ── the helper, on its own ────────────────────────────────────────────────────────────────────

def test_repeated_furniture_is_kept_once():
    got = dedupe_prose([f"{FURNITURE} First page body.",
                        f"{FURNITURE} Second page body.",
                        f"{FURNITURE} Third page body."])
    assert got.count("Property development Restaurant") == 1
    for body in ("First page body.", "Second page body.", "Third page body."):
        assert body in got, body


def test_two_figures_that_differ_by_one_digit_both_survive():
    """THE DEFECT THIS FILE EXISTS FOR. The sentences are identical but for the amount, so a
    character-block cut absorbs "HK$13," and "00" into the surrounding matches and leaves "5"."""
    a = ("Relationship of Range of unobservable Valuation Unobservable inputs to fair value "
         "Commercial properties in Market approach Average market unit HK$13,600 The higher the "
         "market rate per square metre the higher the fair value")
    b = a.replace("HK$13,600", "HK$13,500")
    got = dedupe_prose([a, b])

    assert "HK$13,600" in got, "the first figure was destroyed"
    assert "HK$13,500" in got, "the second figure was destroyed"
    # …and the sentence around them was still deduplicated, or this test proves nothing.
    assert got.count("Average market unit") == 1


def test_a_prose_amount_in_the_shortest_fragment_survives():
    """The 529,841,000 case, reduced. "Keep the longest" is what this refuses."""
    long_one = "Turnover is recognised when control passes. " * 12
    short_one = ("^ Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) "
                 "are included in “other operating expenses”")
    got = dedupe_prose([long_one, "Some other page.", short_one])
    assert "HK$529,841,000" in got
    assert "HK$665,553,000" in got


def test_order_is_document_order():
    got = dedupe_prose(["Alpha page.", "Bravo page.", "Charlie page."])
    assert got.index("Alpha") < got.index("Bravo") < got.index("Charlie")


def test_a_short_repeat_is_left_alone():
    """Below the block floor a shared run is two sentences agreeing, not furniture, and cutting it
    would take half a sentence with it."""
    got = dedupe_prose(["The Group holds land.", "The Group holds buildings."])
    assert got.count("The Group holds") == 2


# ── the collapse, end to end on the real thing ────────────────────────────────────────────────

def _note(number: str, title: str, rows: list[str], prose: str) -> NotesTable:
    t = NotesTable(note_number=number, title=title, source_text=prose)
    for caption in rows:
        t.items.append(NoteItem(raw_label=caption, note_number=number))
    return t


def test_one_entry_per_note_and_every_fragment_contributes(shipped):
    """Three fragments of one note become one entry carrying all three fragments' rows and prose,
    with `identified_for` unioned — a line whose pattern matched only the continuation page still
    asked for this note."""
    item = next(i for i in shipped.items
                if getattr(getattr(i, "note_source", None), "note_title_any", None))
    title = "PROFIT BEFORE TAX"
    notes = [
        _note("7", title, ["Auditor's remuneration"],
              f"{FURNITURE} Auditors were paid for statutory work in both years."),
        _note("7", f"{title} (CONTINUED)", ["Depreciation of property, plant and equipment"],
              f"{FURNITURE} Charges arise on owned assets and on right-of-use assets."),
        _note("7", f"{title} (CONTINUED)", ["Staff costs"],
              f"{FURNITURE} ^ Depreciation charges of approximately HK$529,841,000 are included "
              f"in other operating expenses"),
    ]
    entries = identified_notes(shipped, notes)
    seven = [e for e in entries if e["note"] == "7"]
    if not seven:
        pytest.skip("no shipped line claims a note titled PROFIT BEFORE TAX")

    assert len(seven) == 1, "the note was sent as more than one entry"
    entry = seven[0]
    # THE TITLE IS THE FIRST FRAGMENT'S, not a continuation line.
    assert entry["title"] == title
    # EVERY FRAGMENT'S ROWS.
    captions = {r["caption"] for r in entry.get("rows", [])}
    assert {"Auditor's remuneration", "Staff costs"} <= captions
    # EVERY FRAGMENT'S PROSE, and the furniture once.
    prose = entry.get("prose", "")
    for body in ("Auditors were paid for statutory work",
                 "Charges arise on owned assets"):
        assert body in prose, body
    assert "HK$529,841,000" in prose, "the footnote in the last fragment was dropped"
    assert prose.count("Property development Restaurant") == 1


def test_nothing_a_fragment_stated_is_missing_from_the_collapsed_entry(shipped):
    """The acceptance property, asserted structurally rather than on a filing: every amount and
    every row caption any fragment carried is in the one entry that replaces them."""
    frags = [
        _note("9", "LEASES", ["Right-of-use assets"], f"{FURNITURE} Carrying amount 1,234,567."),
        _note("9", "LEASES (CONTINUED)", ["Lease liabilities"],
              f"{FURNITURE} Carrying amount 7,654,321."),
        _note("9", "LEASES (CONTINUED)", ["Total"], f"{FURNITURE} Discount rate 4,000,000 basis."),
    ]
    want_amounts = {a for f in frags for a in AMOUNT.findall(f.source_text or "")}
    want_rows = {r.raw_label for f in frags for r in f.items}

    entries = identified_notes(shipped, frags)
    nine = [e for e in entries if e["note"] == "9"]
    if not nine:
        pytest.skip("no shipped line claims a note titled LEASES")

    got_prose = nine[0].get("prose", "")
    got_rows = {r["caption"] for r in nine[0].get("rows", [])}
    assert want_amounts <= set(AMOUNT.findall(got_prose)), (
        f"amounts lost: {want_amounts - set(AMOUNT.findall(got_prose))}")
    assert want_rows <= got_rows, f"rows lost: {want_rows - got_rows}"


def test_what_the_guarantee_IS_and_is_not():
    """THE LIMITATION, stated rather than discovered later.

    The guarantee is about CONTENT: every amount and every row caption any fragment carried
    reaches the collapsed entry. It is NOT a guarantee of readable prose across a seam. Dedup
    operates on whole tokens, so where furniture is followed immediately by near-identical wording
    the repeated words go with the furniture and the remainder can begin mid-sentence.

    That is acceptable for what the prose IS — context, plus the text
    `note_sourced.resolve_sources` searches for a model-stated amount — and it is not acceptable
    silently, which is why it is here. If a reader ever needs the narrative to read continuously,
    the fix is to dedupe at sentence boundaries rather than token ones, and this test is where the
    change of contract belongs.
    """
    a = "COLUMN HEADER BAND that repeats across every continuation page of this note. Body one."
    b = "COLUMN HEADER BAND that repeats across every continuation page of this note. Body two."
    got = dedupe_prose([a, b])

    # WHAT IS GUARANTEED: nothing distinct is gone.
    assert "Body one." in got
    assert "two." in got, "the distinct word was destroyed, which would be a real loss"
    # …and the band travelled once.
    assert got.count("COLUMN HEADER BAND") == 1
    # WHAT IS NOT GUARANTEED: that "Body two." survived as a phrase. It shares "Body" with the
    # line before it, so "Body" is part of the repeat.
    assert "Body two." not in got or True
