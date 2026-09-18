"""A figure the filing states in a SENTENCE rather than in a row.

THE CASE, measured on the reference HK filing. The operating-expense share of the depreciation
charge is disclosed in a footnote and nowhere else — "Depreciation charges of approximately
HK$529,841,000 (2024: HK$665,553,000) are included in 'other operating expenses' on the face of the
consolidated income statement" — and 529841 appears in NO extracted row anywhere in the document.
So no row caption reaches it, and the line stayed empty however plainly the filing stated it. It
now fills cascade rung P2 deterministically, with no provider call.

WHY IT NEEDED ITS OWN PATTERN GROUP, and this is the part worth keeping: neither existing artefact
fits prose, and both failures were measured rather than reasoned about.

  * `row_terms` are too LOOSE. They are `row_caption_any` split on `|`, which turns a CONJUNCTION —
    "a depreciation word within forty characters of an expense-function word" — into a DISJUNCTION:
    `depreciation` OR `operating expenses`. A term-based prose rule matched any sentence merely
    mentioning operating expenses: it replaced a depreciation charge of 587,417 with 36,966,000 and
    invented two more figures on lines that should have stayed empty.
  * `row_caption_any` is too TIGHT. Its proximity bounds are authored for a short CAPTION. In this
    footnote the gap between "depreciation" and "operating expenses" is 87 characters — 61 even
    with the amounts stripped — because the sentence carries the figure, the comparative and a verb
    phrase in between. Against `.{0,40}` it does not match at all.

So a prose rule is authored for sentence-length text, and a line that names no destination has no
prose route — opt-in, so an unauthored line yields no figure rather than a guess.

HOW IT IS AUTHORED NOW, and why these tests pass a grammar. The rule used to be raw regex:
`prose_any`, four patterns per line, 28 across the seven lines. Split structurally, every one was
the same three parts — subject, connective, destination — and across the seven lines the first two
were byte identical while only the destination differed. So the shared halves moved to
`LineItemSet.prose_grammar` and a line now says only WHERE THE FIGURE LANDED, in plain words:

    prose_subject:   depreciation                  <- names a shared vocabulary
    prose_landed_in: other operating expenses, 其他经营开支

`services.prose_grammar` compiles those into the four patterns, both word orders and both scripts,
generating every Traditional spelling from the Simplified one. That is why `select_prose` takes the
set's grammar: given only an item it cannot build a sentence pattern, and the right answer then is
nothing rather than a looser match assembled from what is to hand.

AND THE SCALE IS THE OTHER HALF. Prose states a figure in full ("HK$529,841,000"); a table states
it in the statement's units. This stage runs at 10 and `normalize` at 8, so nothing scales what is
written here — published unscaled it is a thousandfold error on the face of the income statement.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.enums import Basis, LineRole
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable, Provenance)
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import note_sourced
from app.services.working_view import build_working_view
from app.stages.note_sourced import NoteSourcedStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")

PART = "sub__pbt_oper_exp_depreciation"
FOOTNOTE = ("^ Depreciation charges of approximately HK$529,841,000 (2024: HK$665,553,000) are "
            "included in “other operating expenses” on the face of the consolidated income "
            "statement.")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def _item(shipped, key=PART):
    return {i.key: i for i in shipped.items}[key]


def _prose(shipped, notes, key=PART):
    """`select_prose` WITH THE SET'S GRAMMAR, which is now the only way it finds anything.

    The subject and connective vocabularies live on the SET (`prose_grammar`), because measured
    across the seven prose lines they were identical on every one; a line says only where the
    figure landed. So a call with no grammar has no patterns to match with, and every test here
    goes through this helper rather than re-deciding that per case.
    """
    return note_sourced.select_prose(_item(shipped, key), notes, shipped.prose_grammar)


def _note(text=FOOTNOTE, *, number="7", title="LOSS FROM OPERATING ACTIVITIES", rows=()):
    return NotesTable(note_number=number, title=title, source_pages=[141],
                      source_text=text, items=list(rows))


# ── the selection ─────────────────────────────────────────────────────────────────────────────

def test_the_footnote_figure_and_its_comparative_are_both_read(shipped):
    hits = _prose(shipped, [_note()])

    assert {(h.period, str(h.amount)) for h in hits} == {
        ("current", "529841000"), ("prior", "665553000")}


def test_a_four_digit_year_is_not_read_as_an_amount(shipped):
    """`(2024: HK$665,553,000)` carries the comparative AND the year labelling it. A naive number
    scan takes 2024 as the first amount in the sentence."""
    hits = _prose(shipped, [_note()])

    assert "2024" not in {str(h.amount) for h in hits}
    assert str(next(h.amount for h in hits if h.period == "current")) == "529841000"


def test_a_line_with_no_prose_destination_yields_nothing(shipped):
    """THE PROSE ROUTE IS OPT-IN, and that is what made it safe to add without auditing all 539
    definitions. A line that names no destination must produce no figure rather than a guess —
    checked with the grammar in hand, so what is being tested is the absent destination and not an
    absent vocabulary."""
    bare = next(i for i in shipped.items
                if getattr(i, "note_source", None)
                and not getattr(i.note_source, "prose_landed_in", None)
                and not getattr(i.note_source, "prose_any", None)
                and getattr(i.note_source, "note_title_any", None))
    assert note_sourced.select_prose(bare, [_note()], shipped.prose_grammar) == []


def test_the_grammar_is_required_and_its_absence_is_not_a_guess(shipped):
    """A CALL WITH NO GRAMMAR HAS NO PATTERNS. The subject and connective vocabularies are
    set-level, so `select_prose` given only an item cannot build a sentence pattern — and the right
    answer then is nothing, not a looser match assembled from what is to hand."""
    assert note_sourced.select_prose(_item(shipped), [_note()], None) == []
    assert _prose(shipped, [_note()]), "with the grammar it must still find the footnote"


def test_a_sentence_with_no_grouped_amount_yields_nothing(shipped):
    """A narrative sentence cannot produce a figure. Requiring thousands separators is what keeps
    years, note numbers and bare percentages out."""
    narrative = ("Depreciation charges are included in other operating expenses on the face of the "
                 "consolidated income statement, as described in the accounting policies.")
    assert _prose(shipped, [_note(narrative)]) == []


def test_the_note_title_still_gates_it(shipped):
    """Prose does not escape the note gate: a sentence in the wrong note is not this line's."""
    assert _prose(shipped, [_note(title="SHARE CAPITAL", number="40")]) == []


def test_a_sibling_whose_container_differs_does_not_claim_the_same_sentence(shipped):
    """THE PRECISION THE PATTERN BUYS. `sub__ga_depreciation` is the administrative-expense share
    and this footnote is about operating expenses — a term-based rule claimed it for both."""
    assert _prose(shipped, [_note()], "sub__ga_depreciation") == []


def test_a_match_cannot_straddle_two_sentences(shipped):
    """`[^.。]` bounds every prose pattern. Without it a depreciation word in one sentence and a
    container in the NEXT would match, and the amount taken could belong to neither."""
    two = ("Depreciation for the year was material. Bank charges of HK$12,345,678 are included in "
           "other operating expenses.")
    hits = _prose(shipped, [_note(two)])

    assert "12345678" not in {str(h.amount) for h in hits}


# ── the scale, and the stage ──────────────────────────────────────────────────────────────────

def _run(shipped, *, scale, notes):
    doc = DocumentModel(filename="f.pdf")
    doc.notes = list(notes)
    # A face row, so the document has a basis and a period for the prose figure to be filed under.
    face = LineItem(source_label="Depreciation", role=LineRole.LINE)
    face.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                  value=Decimal("1"), value_raw=Decimal("1"),
                                  provenance=Provenance(page_index=3)))
    doc.line_items = [face]
    if scale is not None:
        from app.core.models.document import UnitContext
        doc.unit_context = UnitContext(currency="HKD", scale_factor=Decimal(scale))
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.ontology = build_working_view(shipped)
    ctx.line_items = shipped
    NoteSourcedStage().run(doc, ctx)
    return doc, ctx


def _figure(doc, key, period="current"):
    for li in doc.line_items:
        if li.canonical_key == key:
            for ev in li.values.values():
                if getattr(ev, "period_label", None) == period and ev.value is not None:
                    return ev.value
    return None


def test_a_full_prose_amount_is_scaled_to_the_statements_units(shipped):
    """THE THOUSANDFOLD ERROR THIS PREVENTS. The reference filing's statements are in HK$'000, so
    its row-sourced total depreciation is 587,417. The footnote states HK$529,841,000 in full. Left
    unscaled the share would publish as 529,841,000 — a thousand times the total it is part of."""
    doc, _ctx = _run(shipped, scale=1000, notes=[_note()])

    assert _figure(doc, PART) == Decimal("529841")
    assert _figure(doc, PART, "prior") == Decimal("665553")


def test_the_scaled_share_is_smaller_than_the_total_it_is_part_of(shipped):
    """The arithmetic sanity check, and the one that would have caught the unscaled figure without
    anyone knowing the scale factor: a share of the depreciation charge cannot exceed the charge.
    587,417 is the total the row route reads on the same filing."""
    doc, _ctx = _run(shipped, scale=1000, notes=[_note()])

    assert _figure(doc, PART) < Decimal("587417")


def test_an_unscaled_filing_leaves_the_figure_alone(shipped):
    """A filing presented in units needs no division, and dividing anyway would be the same error
    in the other direction."""
    doc, _ctx = _run(shipped, scale=1, notes=[_note()])
    assert _figure(doc, PART) == Decimal("529841000")

    doc, _ctx = _run(shipped, scale=None, notes=[_note()])
    assert _figure(doc, PART) == Decimal("529841000")


def test_the_division_is_flagged_and_the_sentence_travels(shipped):
    """A figure divided by a thousand must be auditable against the words that stated it, so the
    trail carries the sentence and the amount AS WRITTEN while the row carries the factor."""
    doc, _ctx = _run(shipped, scale=1000, notes=[_note()])

    row = next(li for li in doc.line_items if li.canonical_key == PART)
    assert any(f == "prose_scaled_by:1000" for f in row.confidence.flags), row.confidence.flags
    assert any(f.startswith("note_sourced_prose:") for f in row.confidence.flags)
    trail = next(iter(row.derivation.values()))
    assert "529,841,000" in trail["inputs"][0]["label"]
    assert trail["inputs"][0]["value"] == "529841000", "the sentence's own figure must survive"


def test_prose_is_a_fallback_for_a_note_tables_LINE_and_never_displaces_a_ROW(shipped):
    """A row is the filing's own tabulation; a sentence is a narrative restatement of it. So for a
    line that declares `note_tables`, prose is consulted only when the row route found nothing —
    otherwise the second would sometimes replace the first.

    ASSERTED ON AN EDITED SET, and the property is the ROUTE's rather than any shipped line's. The
    six lines carrying prose vocabulary now declare `prose`, which skips the row search outright,
    so no shipped line is in the state this describes and none can be the vehicle for it. The
    fallback is still in the stage and still correct for a `note_tables` line that gains a
    `prose_subject` — that is the right default for a line whose author said its figure IS
    tabulated — so the way to keep asserting it is to build that line.
    """
    edited = shipped.model_copy(deep=True)
    part = next(i for i in edited.items if i.key == PART)
    part.route = "note_tables"

    row = NoteItem(raw_label="Depreciation charged to other operating expenses")
    row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("111111"), value_raw=Decimal("111111"),
                                     provenance=Provenance(page_index=88))
    doc, ctx = _run(edited, scale=1000, notes=[_note(rows=[row])])

    got = _figure(doc, PART)
    assert got == Decimal("111111"), (
        f"prose ({got}) displaced the printed row; prose must only fill what is otherwise empty")
    # THIS ROW, not every row. The other five functional splits still declare `prose` and the
    # sentence in `_note()` names an operating-expense destination they share, so one of them does
    # take it from the prose — correctly, and it says nothing about the line under test. Scanning
    # the whole document asserted the shipped set had no prose line left at all.
    row_out = next(li for li in doc.line_items if li.canonical_key == PART)
    assert not any(f.startswith("note_sourced_prose") for f in row_out.confidence.flags), (
        row_out.confidence.flags)


def test_the_prose_route_does_not_look_at_a_row_at_all(shipped):
    """The other half of the same fence, and what the shipped six now declare. The row above would
    be taken by a `note_tables` line; a `prose` line does not see it, so the sentence answers even
    where a table states something else. That is the point of the route: for a functional split of
    a depreciation charge, a note table is almost always the TOTAL the split is a component of, and
    a figure taken from it is wrong rather than merely differently-sourced.
    """
    part = next(i for i in shipped.items if i.key == PART)
    assert part.route == "prose", part.route

    row = NoteItem(raw_label="Depreciation charged to other operating expenses")
    row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("111111"), value_raw=Decimal("111111"),
                                     provenance=Provenance(page_index=88))
    doc, _ctx = _run(shipped, scale=1000, notes=[_note(rows=[row])])

    assert _figure(doc, PART) == Decimal("529841"), "the sentence did not answer"
    row_out = next(li for li in doc.line_items if li.canonical_key == PART)
    assert any(f.startswith("note_sourced_prose") for f in row_out.confidence.flags)
