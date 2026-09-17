"""A CITATION MAY NAME THE FACE — the third stage, and what makes the block of Stage B answerable.

NEW FILE -> backend/tests/test_face_citation.py

Stage B supplies a face line the printed rows of its statement. Until this, the model could see
them and could not lawfully cite one: the contract said "You locate figures in a financial
statement's NOTES" and "do not cite a note that was not supplied to you", `SourceRef` had no way to
say "this is on the face", and `resolve_sources` indexed note tables only. Three changes together,
because any one alone leaves the block visible and unusable.

THE CITATION IS THE VERDICT, which is why no separate confirm/reject field was added. The three
outcomes the contract now promises are each expressible as a citation already:

  * CONFIRM — cite the row the lexical reader already named. The figure is unchanged.
  * CORRECT — cite a different row. The figure moves, and `note_sourced._write` records what it
    displaced and moves the provenance with it.
  * LEAVE — answer with an empty `sources`. `_write_unanswered` keeps the printed figure and no
    longer takes the row's method or score. THAT KEEP IS PROVISIONAL, and the qualification is
    tested next door: where the model gave this line's printed row to a DIFFERENT line, the row
    belongs to that line and this one is emptied — `tests/test_claimed_printed_row.py`.

`note` AND `statement` ARE ALTERNATIVES, NOT A FALLBACK CHAIN, and the tests below pin that: a
caption appearing both in a note and on the face is a different fact in each, so a citation naming
one must never resolve against the other.
"""
from __future__ import annotations

from decimal import Decimal

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable)
import json as _json
import pathlib as _pathlib

import pytest

from app.schemas.line_items import load_line_item_set
from app.services import face_context
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

FACE_PAGE, NOTE_PAGE = 5, 88
_SEED = (_pathlib.Path(__file__).resolve().parent.parent
         / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(autouse=True)
def _restore_extraction_settings():
    """`get_settings` IS `@lru_cache`D, so the settings object is shared with the whole suite.

    These tests turn the provider on and narrow the focus list; leaving either in place is
    invisible here and fatal elsewhere — measured, it took
    `test_failing_provider_degrades.py::test_the_focus_list_names_the_parts_and_not_only_the_wholes`
    down while passing in isolation, which is the worst way for a test to be wrong.
    """
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = (
            was[0], was[1], was[2], was[3])


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(_json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)


def _face_row(caption, *, value, key=None, page=FACE_PAGE):
    """A face row as the lexical mapper leaves it.

    `confidence.method` IS PART OF THE FIXTURE, not decoration. A matched row carries the method
    the mapper stamped, and the protection added in `_write_unanswered` keys on exactly that: a row
    with no deterministic answer is still claimed by the model, correctly, because there is nothing
    to protect. Omitting it here made the LEAVE case below assert against the wrong branch.
    """
    li = LineItem(source_label=caption, canonical_key=key)
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal(value), value_raw=Decimal(value),
                                    provenance=Provenance(page_index=page))
    if key:
        li.confidence.method = "exact"
        li.confidence.mapping = 1.0
    return li


def _doc(rows, statements):
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=i, kind=PageKind.FACE, statement=st)
                 for i, st in sorted(statements.items())]
    doc.line_items = list(rows)
    return doc


def _notes(caption, amount):
    row = NoteItem(raw_label=caption)
    row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=NOTE_PAGE))
    return [NotesTable(note_number="7", title="N", items=[row])]


def test_a_face_citation_resolves_to_the_printed_row():
    doc = _doc([_face_row("Buildings", value="4200", key="bs_nca__buildings")],
               {FACE_PAGE: "balance_sheet"})
    res, un = resolve_sources([SourceRef(statement="balance_sheet", caption="Buildings")],
                              [], face_context.face_index(doc))
    assert not un, un
    assert len(res) == 1
    assert res[0]["on_face"] is True
    assert res[0]["statement"] == "balance_sheet"
    assert res[0]["figures"] == {"current": "4200"}
    assert res[0]["provenance"]["page_index"] == FACE_PAGE, res[0]["provenance"]


def test_the_figure_comes_off_the_row_and_never_from_the_model():
    """The model gave no amount and must not need to: a face row is already extracted and
    normalised by the time the request is made."""
    doc = _doc([_face_row("Buildings", value="4200")], {FACE_PAGE: "balance_sheet"})
    res, _ = resolve_sources([SourceRef(statement="balance_sheet", caption="Buildings",
                                        amount="999999")],
                             [], face_context.face_index(doc))
    assert res[0]["figures"] == {"current": "4200"}, "the model's amount reached the figure"


def test_a_caption_on_another_statement_says_so():
    """"That caption is on another statement" tells an author something "no such row" does not.

    And an UNRESOLVED statement is never named: a row whose page resolved none carries "", which
    rendered as "it is on " with nothing after it — a diagnostic that asserts the caption was
    found somewhere nameable when it was not."""
    doc = _doc([_face_row("Revenue", value="10", page=9)], {9: "profit_and_loss"})
    res, un = resolve_sources([SourceRef(statement="balance_sheet", caption="Revenue")],
                              [], face_context.face_index(doc))
    assert not res
    assert len(un) == 1 and "profit_and_loss" in un[0]["why"], un


def test_a_face_citation_never_falls_through_to_the_notes():
    """A caption in BOTH places is a different fact in each. Falling through would publish the
    note's figure for a citation that said the face."""
    doc = _doc([], {FACE_PAGE: "balance_sheet"})
    res, un = resolve_sources([SourceRef(statement="balance_sheet", caption="Depreciation")],
                              _notes("Depreciation", "777"), face_context.face_index(doc))
    assert not res, "resolved against the notes for a face citation"
    assert len(un) == 1


def test_a_note_citation_never_falls_through_to_the_face():
    """The mirror, and the reason `statement` is only consulted when `note` is empty."""
    doc = _doc([_face_row("Depreciation", value="4200")], {FACE_PAGE: "balance_sheet"})
    res, un = resolve_sources([SourceRef(note="9", caption="Depreciation")],
                              _notes("Depreciation", "777"), face_context.face_index(doc))
    assert not res, "a citation naming note 9 resolved to a face row"
    assert len(un) == 1


def test_a_face_row_printed_with_no_figure_is_refused():
    """A section banner. It carries provenance — every extracted row does — so it IS on the
    statement and IS citable; what it has no figure to give."""
    bare = LineItem(source_label="NON-CURRENT ASSETS", canonical_key=None)
    bare.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                      value=None, value_raw=None,
                                      provenance=Provenance(page_index=FACE_PAGE))
    doc = _doc([bare], {FACE_PAGE: "balance_sheet"})
    res, un = resolve_sources(
        [SourceRef(statement="balance_sheet", caption="NON-CURRENT ASSETS")],
        [], face_context.face_index(doc))
    assert not res
    assert "no figure" in un[0]["why"], un


def test_with_no_face_index_a_face_citation_is_unresolved_not_misresolved():
    """The default. An audit script passes no document, and a citation naming a statement must then
    report unresolved rather than quietly resolving against whatever notes it was given."""
    res, un = resolve_sources([SourceRef(statement="balance_sheet", caption="Depreciation")],
                              _notes("Depreciation", "777"))
    assert not res and len(un) == 1, (res, un)


def test_the_contract_tells_the_model_both_places_and_all_three_outcomes():
    """The prompt is the interface here: a resolver that accepts a face citation is useless if the
    contract still forbids one. Asserted on the SHAPE of the instruction rather than its wording,
    so it can be reworded without breaking, but not silently reverted to notes-only."""
    from app.services.line_item_llm import REPLY_CONTRACT

    assert "NOTES." not in REPLY_CONTRACT.split("\n")[0], "the opening is still notes-only"
    for needed in ("statement_rows", "`statement`", "CONFIRM", "CORRECT", "empty `sources`"):
        assert needed in REPLY_CONTRACT, f"the contract never mentions {needed}"


def test_an_unresolved_statement_is_never_named_in_the_diagnostic():
    """The row is on a page the classifier could not place, so its statement is "". Naming it
    would read "it is on " with nothing after."""
    stray = _face_row("Buildings", value="1", page=999)   # page 999 is in no `statements` map
    doc = _doc([stray], {FACE_PAGE: "balance_sheet"})
    res, un = resolve_sources([SourceRef(statement="balance_sheet", caption="Buildings")],
                              [], face_context.face_index(doc))
    assert not res
    assert un[0]["why"].endswith("matches that caption"), un[0]["why"]


# ── THE THREE OUTCOMES, END TO END THROUGH THE STAGE ─────────────────────────────────────────

def _run(shipped, sources, *, face_value="9999", face_key=None):
    """One request over a document with one face row, answered with `sources`."""
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage

    KEY = "bs_nca__buildings"
    doc = _doc([_face_row("Buildings", value=face_value, key=face_key or KEY),
                _face_row("Land", value="4200", key="bs_nca__land")],
               {FACE_PAGE: "balance_sheet"})
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = shipped
    ctx.ontology = build_working_view(shipped)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = True
    ctx.settings.extraction.llm_focus_keys = [KEY]

    class _P:
        id = "answers"

        def complete_structured(self, *, system, messages, response_schema, **_):
            return response_schema.model_validate(
                {"answers": [{"key": KEY, "confidence": 0.9, "reason": "r",
                              "sources": sources}]}), {}

    ctx.registry.register("llm", "answers", lambda: _P())
    ctx.settings.llm.provider = "answers"
    LineItemLlmStage().run(doc, ctx)
    row = next(r for r in doc.line_items if r.canonical_key == KEY)
    return row, next(iter(row.values.values()))


def test_confirming_the_proposal_leaves_the_figure_and_records_no_displacement(shipped):
    """CONFIRM. The model cites the row the lexical reader already named."""
    row, slot = _run(shipped, [{"statement": "balance_sheet", "caption": "Buildings"}])
    assert slot.value == Decimal("9999"), "the confirmed figure changed"
    assert not [f for f in row.confidence.flags if "displaced_printed" in f], row.confidence.flags


def test_correcting_the_proposal_moves_the_figure_and_records_what_it_replaced(shipped):
    """CORRECT. The model cites a different printed row; the figure moves and the old one is
    named — the behaviour `note_sourced._write` was hardened for."""
    row, slot = _run(shipped, [{"statement": "balance_sheet", "caption": "Land"}])
    assert slot.value == Decimal("4200"), "the corrected figure did not publish"
    assert "line_item_llm_displaced_printed:9999" in row.confidence.flags, row.confidence.flags


def test_leaving_it_alone_keeps_the_printed_figure(shipped):
    """LEAVE. An empty `sources` keeps the printed figure and no longer takes the row's method."""
    row, slot = _run(shipped, [])
    assert slot.value == Decimal("9999"), "the printed figure was not kept"
    assert row.confidence.method == "exact", f"the row was claimed anyway: {row.confidence.method}"
    assert "llm_located_nothing_kept_exact:1.00" in row.confidence.flags, row.confidence.flags
