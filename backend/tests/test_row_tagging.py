"""Where a figure was printed, which analyst section it belongs to, and the notes that detail it.

Three facts about an extracted row that the pipeline knew and used to drop:

* WHERE IT WAS PRINTED — on the face of a statement, or inside a note. Face and note rows reach
  ``line_items`` through the same reader and carry the same shape, and a note's detail lines sum to
  a figure the face already reports, so a reader adding both double-counts the filing. ``note_number``
  does not answer it: that holds the note a face row CITES, not the note a row lives in.
* WHICH SECTION OF THE FACE — one of the thirteen an analyst reads a filing in. The segmentation
  already resolved it; carrying it on the row is what stops every consumer re-deriving it.
* WHICH NOTES DETAIL IT — resolved through the links the pipeline built, and restricted to notes the
  run actually parsed, so the linkage can never offer detail that is not there.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.routes.extractions import _serialize_rows
from app.core.models.document import DocumentModel
from app.core.models.enums import DocFormat, PrintedIn
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template
from app.services.buckets import BUCKET_KEYS, segment_source

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


def _run(data: bytes, rulebook, template) -> DocumentModel:
    ctx = PipelineContext(raw_bytes=data)
    ctx.ontology, ctx.template = rulebook, template
    return default_pipeline().run(DocumentModel(filename="f.pdf", fmt=DocFormat.PDF), ctx)


# --- printed_in ---------------------------------------------------------------------------------

def test_every_face_row_says_it_was_printed_on_the_face(rulebook, template):
    from tests.fixtures.generate import make_native_pdf

    doc = _run(make_native_pdf(), rulebook, template)
    assert doc.line_items
    assert {li.printed_in for li in doc.line_items} == {PrintedIn.FACE}
    assert {r["printed_in"] for r in _serialize_rows(doc, rulebook)} == {"face"}


def test_a_row_synthesised_from_a_note_is_not_called_a_face_row(rulebook):
    """The reader stamps what IT produces, and it produces face rows only — the notes branch makes
    note tables. What it cannot stamp is a row built LATER out of note items (the residual sweep
    does exactly that), so the segment stage answers for those from the page they came off. Guessing
    face would put a note's money on the statement."""
    from app.core.models.enums import PageKind
    from app.core.models.document import PageSource
    from app.core.models.geometry import BBox, Provenance
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.core.models.enums import Basis

    def _row(label: str, key: str, page: int) -> LineItem:
        li = LineItem(source_label=label, canonical_key=key)
        li.values["k"] = ExtractedValue(
            value=1, basis=Basis.CONSOLIDATED, period_label="current",
            provenance=Provenance(page_index=page, bbox=BBox(x0=0, y0=0, x1=1, y1=1)))
        return li

    doc = DocumentModel(filename="f.pdf", fmt=DocFormat.PDF)
    doc.pages = [PageSource(index=0, kind=PageKind.FACE), PageSource(index=1, kind=PageKind.NOTES)]
    on_face = _row("Inventories", "bs_current_assets__inventories", 0)
    from_note = _row("Raw materials", "bs_current_assets__inventories", 1)
    doc.line_items = [on_face, from_note]

    segment_source(doc, None)
    assert on_face.printed_in is PrintedIn.FACE
    assert from_note.printed_in is PrintedIn.NOTES


def test_a_stamp_the_reader_already_made_is_not_overwritten(rulebook):
    """The back-fill fills in blanks; it does not re-decide.

    The fixture is built so the two answers DISAGREE — a row stamped FACE whose every figure was
    provenanced to a notes page — because that is the only shape in which the guard does anything.
    Without the disagreement the test passes with the guard deleted, which is the same as not
    testing it."""
    from app.core.models.document import PageSource
    from app.core.models.enums import Basis, PageKind
    from app.core.models.geometry import BBox, Provenance
    from app.core.models.line_item import ExtractedValue, LineItem

    doc = DocumentModel(filename="f.pdf", fmt=DocFormat.PDF)
    doc.pages = [PageSource(index=0, kind=PageKind.NOTES)]
    li = LineItem(source_label="Inventories", printed_in=PrintedIn.FACE)
    li.values["k"] = ExtractedValue(
        value=1, basis=Basis.CONSOLIDATED, period_label="current",
        provenance=Provenance(page_index=0, bbox=BBox(x0=0, y0=0, x1=1, y1=1)))
    doc.line_items = [li]

    segment_source(doc, None)
    assert li.printed_in is PrintedIn.FACE, (
        "the back-fill re-decided a stamp the reader had already made")


# --- the section tag ----------------------------------------------------------------------------

def test_each_served_row_carries_its_analyst_section_and_the_rule_behind_it(rulebook, template):
    from tests.fixtures.generate import make_native_pdf

    rows = {r["source_label"]: r for r in
            _serialize_rows(_run(make_native_pdf(), rulebook, template), rulebook)}

    cash = rows["Cash and cash equivalents"]
    assert cash["bucket"] == "current_assets"
    assert cash["bucket_label"] == "Current assets"
    # …and the rulebook decision the tag was derived from, which is what a reviewer needs when the
    # tag looks wrong.
    assert cash["section"] == "bs_s2_current_assets"

    assert rows["Property, plant and equipment"]["bucket"] == "non_current_assets"
    # The balance sheet's own total spans the sections, so no section tag can hold it.
    assert rows["Total assets"]["bucket"] == "others"
    assert all(r["bucket"] in BUCKET_KEYS for r in rows.values())


def test_the_thirteen_sections_the_taxonomy_offers_are_the_ones_that_were_asked_for():
    """The vocabulary itself, pinned. These are presentation labels an analyst reads, so a rename is
    a product decision and not a refactor."""
    from app.services.buckets import BUCKETS

    assert BUCKETS == (
        ("current_assets", "Current assets"),
        ("non_current_assets", "Non-current assets"),
        ("current_liabilities", "Current liabilities"),
        ("non_current_liabilities", "Non-current liabilities"),
        ("equity", "Equity & reserves"),
        ("income", "Income"),
        ("expenses", "Expenses"),
        ("interest", "Interest"),
        ("non_operating", "Non-operating income & expenses"),
        ("cash_flow_operating", "Cash flow from operations"),
        ("cash_flow_investing", "Cash flow from investing"),
        ("cash_flow_financing", "Cash flow from financing"),
        ("changes_in_equity", "Statement of changes in equity"),
        ("others", "Others"),
    )


def test_the_shipped_rulebook_puts_finance_costs_and_interest_income_in_interest(rulebook):
    """The tag no section can produce, asserted on the shipped rulebook rather than on a fixture —
    the data is the mechanism here, so the data is what has to be right."""
    declared = {m.canonical_key: m.analyst_bucket for m in rulebook.mappings if m.analyst_bucket}
    assert declared == {
        "pl_non_operating_expenses__interest_expense": "interest",
        "pl_non_operating_expenses__interest_income": "interest",
    }
    # And the balance-sheet captions any keyword rule would have caught are NOT interest.
    for key in ("bs_non_current_assets__interests_in_associates",
                "bs_equity__non_controlling_interests"):
        assert next(m for m in rulebook.mappings if m.canonical_key == key).analyst_bucket is None


def test_an_analyst_bucket_naming_nothing_is_refused_at_upload(rulebook, template):
    """The gate. Obeying an unknown tag would create a segment nothing renders and lose its rows."""
    from app.schemas.loader import validate_ontology_against_template

    assert validate_ontology_against_template(rulebook, template) == []

    broken = rulebook.model_copy(deep=True)
    broken.mappings[0].analyst_bucket = "cashflow"        # not a bucket key
    errors = validate_ontology_against_template(broken, template)
    assert len(errors) == 1
    assert "analyst_bucket" in errors[0].message and "cashflow" in errors[0].message


# --- the face-to-note linkage -------------------------------------------------------------------

def test_a_face_row_carries_the_notes_that_detail_it(rulebook, template):
    from tests.fixtures.generate import make_multipage_pdf

    doc = _run(make_multipage_pdf(), rulebook, template)
    assert [n.note_number for n in doc.notes] == ["14"]

    row = next(r for r in _serialize_rows(doc, rulebook)
               if r["source_label"] == "Cash and cash equivalents")
    assert row["notes"] == ["14"]
    assert row["note"] == "14"          # what the page printed in its note column


def test_a_note_reference_with_no_extracted_note_behind_it_is_not_offered_as_a_link(
        rulebook, template):
    """``note`` is the promise the filing makes; ``notes`` is the promise this extraction can keep.
    A page citing note 5 whose note pages were never read must not offer a link to nothing."""
    from tests.fixtures.generate import make_native_pdf

    doc = _run(make_native_pdf(), rulebook, template)        # one page, no notes pages at all
    rows = {r["source_label"]: r for r in _serialize_rows(doc, rulebook)}
    assert rows["Trade receivables"]["note"] == "15"          # printed
    assert rows["Trade receivables"]["notes"] == []           # …and nothing extracted to link to
    assert doc.notes == []


def test_the_linkage_never_names_a_note_the_run_did_not_publish(rulebook, template):
    """``notes`` promises detail that exists, and the links are OLDER than the note list: they are
    built before ``prune_notes`` drops every note no face row cites. So the promise cannot rest on
    stage order — it is intersected with the notes the run actually published."""
    from app.api.routes.extractions import _linked_notes
    from tests.fixtures.generate import make_multipage_pdf

    doc = _run(make_multipage_pdf(), rulebook, template)
    face_id = str(doc.line_items[0].id)
    assert _linked_notes(doc)[face_id] == ["14"]

    # Now the note is gone — which is exactly what prune_notes does to an uncited one — while the
    # link that named it is still on the document.
    doc.notes = []
    assert doc.links, "the fixture must keep its links, or it proves nothing"
    assert _linked_notes(doc) == {}
