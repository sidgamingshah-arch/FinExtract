"""A face row citing "16(b)" of a note table numbered "16".

WHY THIS HAS ITS OWN MODULE: which notes a row cites is read by three stages that had each written
their own answer — ``link_notes`` (what to link), ``prune_notes`` (what to publish) and
``services.buckets`` (which section to file a note under). A filing printing a sub-reference broke
all three the same way and the failure was silent in each: the note was pruned, so the linkage had
nothing to link, so the section held nothing explaining the figure. Nothing was ever wrong-looking
— the note simply was not there.

``NoteRef`` has a ``subrefs`` field for exactly this, and it is a trap: NEITHER reader populates it.
``row_reconstruct`` and ``excel_extract`` both put the whole printed token, sub-part and all, into
``numbers``. A guard reading only ``subrefs`` therefore reads a field that is always empty and
tests written against that shape pass while the product stays broken — which is what happened, so
the tests below assert the READERS' shape and the docstring says why.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.routes.extractions import _serialize_rows
from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat
from app.core.models.line_item import LineItem, NoteRef, base_note_number
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template
from tests.fixtures.generate import make_sub_note_pdf

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def rulebook():
    return load_ontology(json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text()),
                         resolve=True)


@pytest.fixture(scope="module")
def template():
    return load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text()))


# --- the rule itself ----------------------------------------------------------------------------

def test_the_parent_of_a_citation_is_named_only_when_there_is_one():
    """``None`` for a bare number, so a caller can tell "has a parent" from "IS the parent" without
    comparing strings — the check that decides whether to fall back at all."""
    assert base_note_number("16(b)") == "16"
    assert base_note_number("7A") == "7"
    assert base_note_number(" 16 ") is None      # already the parent
    assert base_note_number("16") is None
    assert base_note_number("") is None and base_note_number(None) is None
    assert base_note_number("(b)") is None       # nothing to fall back to


def _row(*, numbers=(), subrefs=(), note_number=None) -> LineItem:
    li = LineItem(source_label="Trade receivables", note_number=note_number)
    if numbers or subrefs:
        li.note_refs = [NoteRef(raw="x", numbers=list(numbers), subrefs=list(subrefs))]
    return li


def test_a_citation_is_read_from_the_field_the_readers_actually_write():
    """Both spellings, because the field the schema intends for sub-references is dead."""
    assert _row(numbers=["16(b)"]).cited_notes() == ["16(b)"]        # what the readers produce
    assert _row(subrefs=["16(b)"]).cited_notes() == ["16(b)"]        # what the schema intends
    assert _row(numbers=["30", "31"]).cited_notes() == ["30", "31"]  # a parsed range, in order


def test_the_note_column_is_the_fallback_for_a_row_with_nothing_parsed():
    """The shape ``residual`` gives a face row it synthesised out of a note item: no ``NoteRef``,
    just the note it came from."""
    assert _row(note_number="14").cited_notes() == ["14"]
    # …and it does not duplicate a citation the refs already carry.
    assert _row(numbers=["14"], note_number="14").cited_notes() == ["14"]


def test_a_sub_reference_resolves_to_the_parent_note_only_when_itself_is_not_a_table():
    """The whole rule in three lines. Falling back UNCONDITIONALLY would tie one row to both a
    sub-note and its parent, and the reconciliation would then subtract the same detail twice."""
    assert _row(numbers=["16(b)"]).cited_notes_among({"16"}) == ["16"]        # falls back
    assert _row(numbers=["16(b)"]).cited_notes_among({"16(b)", "16"}) == ["16(b)"]   # exact wins
    assert _row(numbers=["16(b)"]).cited_notes_among({"18"}) == []            # neither exists


def test_a_row_citing_two_sub_notes_of_one_table_names_that_table_once():
    """"16(a) and 16(b)" is one note table, and a doubled entry would be counted as two citations —
    which is what decides the link's relationship label."""
    assert _row(numbers=["16(a)", "16(b)"]).cited_notes_among({"16"}) == ["16"]


# --- the same rule, all the way through a real filing -------------------------------------------

@pytest.fixture(scope="module")
def sub_note_run(rulebook, template) -> DocumentModel:
    """A filing whose balance sheet cites 16(b) and whose notes print one table numbered 16."""
    ctx = PipelineContext(raw_bytes=make_sub_note_pdf())
    ctx.ontology, ctx.template = rulebook, template
    return default_pipeline().run(DocumentModel(filename="f.pdf", fmt=DocFormat.PDF), ctx)


def test_the_reader_puts_the_sub_part_in_numbers_and_leaves_subrefs_empty(sub_note_run):
    """The premise the three stages have to be written against, pinned on a real read so it cannot
    quietly change under them."""
    row = next(li for li in sub_note_run.line_items if li.source_label == "Trade receivables")
    assert [r.numbers for r in row.note_refs] == [["16(b)"]]
    assert [r.subrefs for r in row.note_refs] == [[]]
    assert row.note_number == "16(b)"


def test_the_note_a_sub_reference_points_at_is_published(sub_note_run):
    """``prune_notes`` publishes only cited notes, so a citation it cannot read deletes the note."""
    assert [n.note_number for n in sub_note_run.notes] == ["16"]


def test_the_face_row_is_linked_to_it(sub_note_run):
    """The linkage the reconciliation and the API both read."""
    row = next(li for li in sub_note_run.line_items if li.source_label == "Trade receivables")
    assert [(str(l.face_item_id), l.note_number) for l in sub_note_run.links] == [
        (str(row.id), "16")]


def test_the_served_row_offers_the_note_as_its_detail(sub_note_run, rulebook):
    """What an analyst sees: the row says which note explains it, and ``note`` still shows the
    citation the page printed."""
    row = next(r for r in _serialize_rows(sub_note_run, rulebook)
               if r["source_label"] == "Trade receivables")
    assert row["note"] == "16(b)"      # as printed
    assert row["notes"] == ["16"]      # what can actually be opened


def test_the_note_is_filed_in_the_section_of_the_row_that_cites_it(sub_note_run):
    """The section store is joined to the row by the API, so a note filed elsewhere is a note the
    analyst reading this section cannot see — the figure would be there with nothing behind it."""
    seg = next(s for s in sub_note_run.buckets.segments if s.bucket == "current_assets")
    assert seg.note_numbers == ["16"]
    assert sub_note_run.buckets.unresolved_note_numbers == []


# --- and what the analyst opens ------------------------------------------------------------------

def test_the_notes_screen_names_the_face_row_that_cites_a_sub_note(client):
    """The last link in the chain. ``/notes/{no}`` names the face line a note explains, and it used
    to find it by re-parsing the printed note column — so a row citing "16(b)" appeared under no
    note even once the note itself was published. It reads the row's resolved linkage now."""
    import time

    doc_id = client.post("/api/v1/documents", files={
        "file": ("subnote.pdf", make_sub_note_pdf(), "application/pdf")}).json()["id"]
    onts = client.get("/api/v1/ontologies").json()
    ont = next(o for o in onts if o["ontology_key"] == "hkfrs_hk_china")
    tpls = client.get("/api/v1/templates").json()
    tpl = next(t for t in tpls if t["template_key"] == ont["target_template_key"])
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"ontology_version_id": ont["id"], "template_version_id": tpl["id"]})
    for _ in range(200):
        r = client.get(f"/api/v1/documents/{doc_id}/run")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("extraction did not finish")

    assert [n["no"] for n in client.get(f"/api/v1/documents/{doc_id}/notes").json()["notes"]] == [16]
    detail = client.get(f"/api/v1/documents/{doc_id}/notes/16").json()
    assert detail["linked_label"] == "Trade receivables"
    assert detail["linked_line"] == "bs_current_assets__trade_receivables"
