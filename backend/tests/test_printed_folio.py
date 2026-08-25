"""A citation names the page number the filing PRINTED, not the page's position in the file.

A PDF page has two numbers. Its position in the file is what the viewer scrolls to, what provenance
carries, what the page-scope selection is expressed in, and what the review queue's judgement anchor
is built from. The folio the publisher printed on it is what the page in the reader's hand says.

They are not the same, and the gap is a property of the document's front matter rather than
something to compute — measured across two real HK filings the offsets were 0 and 1. A citation
exists so a person can go and look, and the only number they can look up is the one on the paper.

WHAT THE READER ACTUALLY SAW, and it was not a missing folio. The viewer's page badge already
printed BOTH: `p.186 · 184`. Both true, and nothing to say which was which — reported as "it is
showing 2 page numbers". So the pair is labelled in the badge (the one place both belong, since it
sits on the image) and every other citation names the folio alone.

THE RULE THIS MODULE DEFENDS: never redefine a field that already means "sheet position". The folio
arrives as a sibling everywhere, and the identity sites keep the index untouched — swapping a folio
into the judgement anchor would re-key every stored human acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.routes.documents import _note_index, _prov_anchor, _prov_label
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import DocFormat
from app.core.pipeline import default_pipeline
from app.core.stage import PipelineContext
from app.schemas.loader import load_ontology, load_template
from app.services.export import _prov_str

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


# --- the citation ---------------------------------------------------------------------------------

def test_a_citation_names_the_printed_folio():
    prov = {"source_kind": "pdf", "page_index": 185, "printed_page": "184"}
    assert _prov_label(prov) == "p.184"


def test_the_sheet_position_is_the_fallback_and_keeps_its_shape():
    """A spreadsheet has no folio, and neither does a page whose footer could not be read — 5 of
    270 on the filing measured. The fallback is unchanged, which also keeps the click-to-source e2e
    working: it finds the chip by the "p.N" shape."""
    assert _prov_label({"source_kind": "pdf", "page_index": 0}) == "p.1"
    assert _prov_label({"source_kind": "pdf", "page_index": 185, "printed_page": None}) == "p.186"
    assert _prov_label({"source_kind": "spreadsheet", "sheet": "BS", "cell": "C7"}) == "BS!C7"


def test_the_export_cites_it_identically():
    """An exported citation travels further than an on-screen one — it is read by someone holding
    the PDF and not the app. A workbook that cites a page differently from the screen it came from
    is a support call, so the two are character-for-character the same."""
    for prov in ({"source_kind": "pdf", "page_index": 185, "printed_page": "184"},
                 {"source_kind": "pdf", "page_index": 185},
                 {"source_kind": "spreadsheet", "sheet": "BS", "cell": "C7"}):
        assert _prov_str(prov) == _prov_label(prov), prov


# --- the identity sites must not move -------------------------------------------------------------

def test_the_judgement_anchor_still_uses_the_sheet_index():
    """THE ONE THAT WOULD DO REAL DAMAGE. ``_prov_anchor`` feeds ``judgement.subject_key``, the
    primary key of a persisted human acceptance. If the folio went in there, every stored accept and
    dismiss would re-key and detach from its card. The label and the anchor are deliberately two
    different functions over the same provenance, and only the label moved."""
    prov = {"source_kind": "pdf", "page_index": 185, "printed_page": "184",
            "label_bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.21}}
    anchor = _prov_anchor(prov)

    assert anchor.startswith("p185#"), anchor
    assert "184" not in anchor, anchor
    # And the anchor is unchanged by the folio being present at all.
    assert _prov_anchor({k: v for k, v in prov.items() if k != "printed_page"}) == anchor


# --- the note span --------------------------------------------------------------------------------

def _table(no: str, page: int, folio: str | None, labels: list[str]) -> dict:
    return {"no": no, "title": f"NOTE {no}", "page": page, "printed_page": folio,
            "rows": [{"label": lab, "role": "line", "confidence": 1.0, "values": []}
                     for lab in labels]}


def test_a_notes_span_is_cited_by_folio_and_navigated_by_sheet():
    """Both, side by side, because the screen needs both: ``pages`` scrolls the viewer and sizes the
    page stack, ``printed_pages`` is what the header says."""
    index = _note_index([
        _table("15", 196, "195", ["At 1 January"]),
        _table("15", 197, "196", ["Additions"]),
        _table("15", 199, "198", ["At 31 December"]),
    ])

    note = index["15"]
    assert note["pages"] == [196, 197, 199]
    assert note["printed_pages"] == ["195", "196", "198"]
    assert note["page"] == 196
    assert note["printed_page"] == "195"


def test_the_folios_follow_the_sheet_order_they_were_sorted_into():
    """A folio is a STRING and sorts lexicographically ('100' < '99'), so it can never be the sort
    key. The pages are ordered numerically and the folios are read off in that order."""
    # Folios where a string sort DIVERGES from the sheet order: sorted(["99", "100"]) is
    # ["100", "99"], so a lexicographic sort would report the span backwards.
    index = _note_index([
        _table("30", 100, "100", ["b"]),
        _table("30", 99, "99", ["a"]),
    ])

    assert index["30"]["pages"] == [99, 100]
    assert index["30"]["printed_pages"] == ["99", "100"]
    assert index["30"]["page"] == 99
    assert index["30"]["printed_page"] == "99"


def test_a_note_with_no_folio_reports_none_rather_than_a_guess():
    index = _note_index([_table("7", 184, None, ["Interest"])])

    assert index["7"]["pages"] == [184]
    assert index["7"]["printed_pages"] == []
    assert index["7"]["printed_page"] is None


# --- end to end -----------------------------------------------------------------------------------

def test_the_folio_reaches_every_provenance_the_api_serves():
    """It is added in ``_prov_dict``, the ONE serializer both face rows and note rows pass through,
    so every citation in the product is reached without threading a page map through the builders
    whose positional signatures tests call directly."""
    from app.api.routes.extractions import _serialize_notes, _serialize_rows
    from tests.fixtures.generate import make_hk_income_statement_pdf

    ontology = load_ontology(
        json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text()), resolve=True)
    template = load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text()))
    ctx = PipelineContext(raw_bytes=make_hk_income_statement_pdf())
    ctx.ontology, ctx.template = ontology, template
    doc = default_pipeline().run(DocumentModel(filename="is.pdf", fmt=DocFormat.PDF), ctx)

    # The fixture prints "184" as its folio on its only sheet.
    assert [p.printed_page for p in doc.pages] == ["184"]

    provs = [v["provenance"] for r in _serialize_rows(doc) for v in (r.get("values") or [])
             if v.get("provenance")]
    assert provs, "no provenance was serialized"
    for prov in provs:
        assert prov["page_index"] == 0, "the sheet index must not move"
        assert prov["printed_page"] == "184"
        assert _prov_label(prov) == "p.184"
    # And the note serializer carries it too (this fixture has no notes, so assert the shape).
    assert _serialize_notes(doc) == []


def test_a_document_with_no_folios_serves_none_everywhere():
    """The lookup is None when the document prints no folio at all, and every reader falls back.
    An .xlsx can never have one: the folio is written downstream of a PDF-only branch."""
    from app.api.routes.extractions import _folio_lookup

    doc = DocumentModel(filename="f.xlsx", fmt=DocFormat.XLSX)
    doc.pages = [PageSource(index=0), PageSource(index=1)]

    assert _folio_lookup(doc) is None

    doc.pages[1].printed_page = "7"
    lookup = _folio_lookup(doc)
    assert lookup is not None
    assert lookup(0) is None and lookup(1) == "7"
