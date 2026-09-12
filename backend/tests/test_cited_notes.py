"""THE FILING'S OWN NOTE REFERENCE REACHES THE MODEL, ahead of anything a probe scored.

NEW FILE -> backend/tests/test_cited_notes.py

WHAT THE LINE-ITEM PATH DID NOT HAVE. Every note in a request got there by scoring: a probe built
from the line's prose against each note's heading, then a content widening when that came up short.
Both are inferences about which note is about this line. Meanwhile `stages.link_notes` has been
resolving the printed "Note 14" beside a face caption into `doc.links` all along — for the
note-to-face reconciliation — and `line_item_notes` had no reference to it; `identified_notes` was
not even passed the document.

A printed reference is not an inference. It is the preparer saying where the detail is.

MEASURED ACROSS THE TWELVE-FILING CORPUS, by running the deterministic extraction on each:

    3,533 face rows          863 printed note references
    150 (line, filing) pairs whose citation resolves to a note the run actually holds
     92 asked-about lines carrying a citation
     30 of those cite a note THE SCORER DID NOT DELIVER      <- the gain
      6 where that citation would DISPLACE a scored note     <- the cost, and it is real

THE PLAN SAID THIS "CANNOT WITHHOLD A NOTE THE CURRENT BEHAVIOUR WOULD HAVE DELIVERED". That is
false, and the corpus says so: the per-line cap is 4, so on 6 of the 92 a citation pushes the
lowest-scoring note out. It is still the right trade — a preparer's reference beats a probe that
scored 0.3 — but it is a trade, not a free addition, and recording it as free would have been the
kind of claim this codebase keeps having to walk back.

Five of the twelve filings yield no usable citation at all (the link stage finds none, or none lands
on a row the mapper placed), so nothing changes for them. `cited=None` is that same code path.
"""
from __future__ import annotations

import unittest.mock
from decimal import Decimal
from uuid import uuid4

from app.core.models.enums import Basis
from app.core.models.line_item import (ExtractedValue, FaceNoteLink, LineItem, NoteItem, NotesTable,
                                       Provenance)
from app.schemas.line_items import LineItemDef, LineItemSet, NoteSource
from app.services import line_item_llm, line_item_notes, note_context


def _note(number: str, title: str, captions: tuple[str, ...] = ()) -> NotesTable:
    table = NotesTable(note_number=number, title=title, page_index=9)
    for caption in captions:
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("1"), value_raw=Decimal("1"),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


# EIGHT UNRELATED HEADINGS, because the header scorer is IDF-WEIGHTED. The first draft of these
# tests built four notes all titled "Inventories…" and every probe scored 0.000 — a term present in
# every heading distinguishes nothing, which is the scorer working correctly and the fixture being
# wrong. A realistic pool is the only kind that tests anything here.
_POOL = ("Inventories", "Share capital", "Property plant and equipment",
         "Trade and other receivables", "Bank borrowings", "Income tax",
         "Cash and cash equivalents", "Related party transactions")


def _filing(*extra: NotesTable) -> list[NotesTable]:
    notes = [_note(str(i + 1), title) for i, title in enumerate(_POOL)]
    notes.extend(extra)
    return notes


class _Doc:
    """The two attributes `cited_notes` reads. A real `DocumentModel` carries many others and none
    of them matter here, so a stub states exactly what the function depends on."""

    def __init__(self, line_items, links):
        self.line_items = line_items
        self.links = links


def _face_row(key: str) -> LineItem:
    return LineItem(source_label=key, canonical_key=key)


def _item(key: str, **kw) -> LineItemDef:
    kw.setdefault("label", key)
    kw.setdefault("note_source", NoteSource(note_terms=["nothing this filing says"]))
    return LineItemDef(key=key, **kw)


# ── reading the links ─────────────────────────────────────────────────────────────────────────

def test_a_printed_reference_is_read_off_the_face_row() -> None:
    row = _face_row("bs_ca__inventories")
    doc = _Doc([row], [FaceNoteLink(face_item_id=row.id, notes_table_id=uuid4(),
                                    note_number="14")])

    assert line_item_notes.cited_notes(doc) == {"bs_ca__inventories": ("14",)}


def test_two_notes_cited_by_one_line_both_travel_in_document_order() -> None:
    """A line whose detail is split ("Notes 14, 15") wants both."""
    row = _face_row("bs_ca__inventories")
    doc = _Doc([row], [
        FaceNoteLink(face_item_id=row.id, notes_table_id=uuid4(), note_number="14"),
        FaceNoteLink(face_item_id=row.id, notes_table_id=uuid4(), note_number="15"),
    ])

    assert line_item_notes.cited_notes(doc)["bs_ca__inventories"] == ("14", "15")


def test_the_same_reference_printed_twice_is_one_note() -> None:
    """A face caption appears on the consolidated AND the standalone statement, so two rows carry
    the key and usually the same reference."""
    consolidated, standalone = _face_row("bs_ca__inventories"), _face_row("bs_ca__inventories")
    doc = _Doc([consolidated, standalone], [
        FaceNoteLink(face_item_id=consolidated.id, notes_table_id=uuid4(), note_number="14"),
        FaceNoteLink(face_item_id=standalone.id, notes_table_id=uuid4(), note_number="14"),
    ])

    assert line_item_notes.cited_notes(doc)["bs_ca__inventories"] == ("14",)


def test_a_reference_on_an_unplaced_row_names_nothing() -> None:
    """`canonical_key` empty means the mapper could not place the row. A citation there names a
    note for a line that does not exist, so it is dropped rather than keyed on the empty string."""
    unplaced = LineItem(source_label="Something the mapper could not place")
    doc = _Doc([unplaced], [FaceNoteLink(face_item_id=unplaced.id, notes_table_id=uuid4(),
                                         note_number="14")])

    assert line_item_notes.cited_notes(doc) == {}


def test_no_document_is_not_an_error() -> None:
    """The audit scripts are handed reconstructed note tables and no face rows at all."""
    assert line_item_notes.cited_notes(None) == {}
    assert line_item_notes.cited_notes(_Doc([], [])) == {}


# ── what reaches the note set ─────────────────────────────────────────────────────────────────

def test_a_cited_note_is_delivered_where_no_probe_would_have_found_it() -> None:
    """THE GAIN, and the shape of all 30 corpus cases: the line's prose names nothing in this
    filing's headings, so scoring delivers nothing, and the filing itself says note 1."""
    item = _item("bs_ca__inventories",
                 note_source=NoteSource(note_terms=["deferred consideration payable"]))
    notes = _filing()

    without = line_item_notes.note_sets([item], notes)
    assert not [h for h in without.get(item.key, ()) if h.note == "1"]

    with_citation = line_item_notes.note_sets([item], notes, cited={item.key: ("1",)})
    assert [h.note for h in with_citation[item.key]] == ["1"]
    assert with_citation[item.key][0].via == "cited", "the provenance must say it was not scored"
    assert with_citation[item.key][0].title == "Inventories"


def test_a_citation_to_a_note_this_document_does_not_have_is_dropped() -> None:
    """A reference the pruner dropped or the parser never built would put a note NUMBER in the
    request with no text under it — worse than nothing, because the model is told to look there."""
    item = _item("bs_ca__inventories")
    got = line_item_notes.note_sets([item], _filing(), cited={item.key: ("99",)})

    assert not [h for h in got.get(item.key, ()) if h.note == "99"]


def test_the_cited_note_comes_first_and_is_not_duplicated_by_the_scorer() -> None:
    item = _item("x", note_source=NoteSource(note_terms=["inventories"]))
    got = line_item_notes.note_sets([item], _filing(), cited={item.key: ("1",)})[item.key]

    assert got[0].note == "1"
    assert [h.note for h in got].count("1") == 1, "the scorer added it a second time"
    assert got[0].via == "cited", "the scorer's own hit took the position"


def test_a_citation_displaces_the_lowest_scoring_note_at_the_cap() -> None:
    """THE COST, asserted rather than glossed. `cap` bounds the set, so a citation added to a full
    set pushes one out — 6 of the 92 corpus cases. The order is what makes that the right one to
    lose: what goes is the note the scorer ranked last.
    """
    item = _item("x", note_source=NoteSource(note_terms=["cash and cash equivalents", "bank"]))
    notes = _filing()

    scored = line_item_notes.note_sets([item], notes, cap=2)[item.key]
    assert [h.note for h in scored] == ["7", "5"], [(h.note, h.score) for h in scored]

    with_citation = line_item_notes.note_sets([item], notes, cap=2,
                                              cited={item.key: ("1",)})[item.key]
    assert [h.note for h in with_citation] == ["1", "7"], [h.note for h in with_citation]
    assert scored[-1].note not in {h.note for h in with_citation}, (
        "the displaced note should be the one the scorer ranked last")


def test_a_cited_note_does_not_switch_off_content_widening(monkeypatch) -> None:
    """THE TRAP THIS AVOIDS. A cited hit enters with score 1.0, and the widening test used to read
    the best score across the whole set — so citing a note would have reported a confident header
    match and suppressed widening, on exactly the lines most likely to need it (a thin probe and a
    printed reference tend to go together). The decision reads the SCORED hits only.

    `WIDEN_BELOW` is 0.0 in the shipped default — content widening is built and deliberately OFF,
    for the measured reason recorded above its definition — so the threshold is raised here.
    Asserting the interaction at the default would assert nothing: the branch cannot be entered.
    """
    monkeypatch.setattr(line_item_notes, "WIDEN_BELOW", 0.40)
    item = _item("x", note_source=NoteSource(row_terms=["deferred consideration"],
                                             note_terms=["nothing in these headings"]))
    notes = _filing(_note("9", "Acquisition of a subsidiary", ("deferred consideration",)))

    got = line_item_notes.note_sets([item], notes, cited={item.key: ("1",)})[item.key]
    vias = {h.note: h.via for h in got}
    assert vias.get("1") == "cited"
    assert vias.get("9") == "content", f"widening did not run: {vias}"


def test_nothing_changes_when_no_citation_is_passed() -> None:
    """The path five of the twelve filings take, and every audit script."""
    items = [_item("a", note_source=NoteSource(note_terms=["inventories"])),
             _item("b", note_source=NoteSource(note_terms=["share capital"]))]
    notes = _filing()

    plain = line_item_notes.note_sets(items, notes)
    explicit = line_item_notes.note_sets(items, notes, cited={})
    assert plain, "the fixture scored nothing, so this would assert nothing"
    assert ({k: [h.note for h in v] for k, v in plain.items()}
            == {k: [h.note for h in v] for k, v in explicit.items()})


# ── and into the document-level context pass ──────────────────────────────────────────────────

def test_a_cited_note_outranks_a_better_scoring_one_inside_the_budget() -> None:
    """`identified_notes` caps what SIMILARITY may add (`_SEMANTIC_NOTE_BUDGET`) because a note
    number pulls in every fragment carrying it — 23 numbers became 56 extra tables and one request
    went from 30,407 to 82,299 tokens. A citation spends that budget like a guess does (it is not
    an AUTHORED declaration, which is what the unconditional pass is for) but it spends it first.
    """
    items = [_item("x", note_source=NoteSource(note_terms=["cash and cash equivalents", "bank"]))]
    notes = _filing()
    st = LineItemSet(items=items)

    # Both candidates arrive when there is room for both.
    plain = [str(r.get("note")) for r in note_context.identified_notes(st, notes)]
    assert set(plain) == {"5", "7"}, plain

    # With room for ONE, the citation decides which survives — and it is the note the filing
    # printed, not the one that scored higher.
    with unittest.mock.patch.object(note_context, "_SEMANTIC_NOTE_BUDGET", 1):
        cited = [str(r.get("note")) for r in
                 note_context.identified_notes(st, notes, cited={"x": ("5",)})]
        scored_only = [str(r.get("note")) for r in note_context.identified_notes(st, notes)]

    assert cited == ["5"], cited
    assert scored_only == ["7"], f"the higher score must win when nothing is cited: {scored_only}"


def test_a_cited_note_the_scorer_missed_reaches_the_payload_WITH_ITS_TEXT() -> None:
    """THE DEFECT THE END-TO-END REVIEW FOUND, and the reason this file needed a payload-level test
    rather than only a note-set one.

    A note reaches a request only by being in BOTH halves: `note_sets` decides the plan's numbers,
    and `build_request` takes the note TEXT from `identified_notes`, filtered to those numbers. The
    first version of the citation work sorted `identified_notes`'s candidates so a cited note came
    FIRST — which does nothing for a cited note that scored NOTHING, and that is exactly the case
    the capability exists for (30 of the 92 corpus citations point at a note scoring did not
    deliver).

    So the line's `notes_supplied` named note 1 and the payload carried no text for it. The model
    was pointed at a note it could not read — worse than not offering it at all, and the same
    failure the "citation to a note this document does not have" case refuses from the other
    direction. A citation now ENTERS the candidate set rather than reordering it.
    """
    item = _item("x", parent="p",
                 note_source=NoteSource(note_terms=["deferred consideration payable"],
                                        row_terms=["deferred consideration"]))
    notes = [_note(str(i + 1), t, ("some row",)) for i, t in enumerate(_POOL)]
    st = LineItemSet(items=[item])
    cited = {"x": ("1",)}

    # Nothing in the line's prose names any of these headings, so scoring finds note 1 for no
    # reason at all — which is the premise.
    assert not [n for n in note_context.identified_notes(st, notes)
                if str(n.get("note")) == "1"]

    sets = line_item_notes.note_sets([item], notes, cited=cited)
    identified = note_context.identified_notes(st, notes, cited=cited)

    class _Plan:
        keys = ("x",)
        notes = tuple(h.note for h in sets["x"])
        name = "x"

    payload = line_item_llm.build_request(_Plan(), {"x": item}, {"x": _Plan.notes}, identified)
    supplied = payload["line_items"][0]["notes_supplied"]
    carried = {str(n.get("note")) for n in payload["notes"]}

    assert "1" in supplied, supplied
    assert not [n for n in supplied if n not in carried], (
        f"named to the model with no text under it: "
        f"{[n for n in supplied if n not in carried]}")
    # …and the text is really there, not an empty shell.
    note_one = next(n for n in payload["notes"] if str(n.get("note")) == "1")
    assert note_one.get("rows"), note_one
