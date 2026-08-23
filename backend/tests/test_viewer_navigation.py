"""Getting to the right page of the source document, and being able to say which page it is.

Two numbers name a page of an annual report and they are NOT the same number: its position in the
file, which is what every index in this system means and what the viewer scrolls to, and the folio
printed on the page, which is what the reader sees and what a colleague quotes. Front matter — a
cover, a contents page, a chairman's statement — makes the second run behind the first, routinely by
several pages. Showing only one of them makes the app look like it is navigating to the wrong place.

The other half of getting to a page is being able to search for it, which the viewer cannot do on
its own: it renders page images, so the text has to be searched server-side and come back as boxes
in the same normalized page space provenance uses.
"""
from __future__ import annotations

import pytest

from tests.fixtures.generate import make_hk_running_header_report_pdf, make_native_pdf, make_xlsx

pytest.importorskip("fitz")
pytest.importorskip("reportlab")


def _upload(client, data: bytes, name: str = "report.pdf", mime: str = "application/pdf") -> str:
    r = client.post("/api/v1/documents", files={"file": (name, data, mime)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- the folio itself ---------------------------------------------------------------------------

def test_the_folio_is_read_from_the_margin_and_a_year_is_never_one():
    from app.stages.classify import _printed_folio

    def line(text: str, y: float) -> dict:
        return {"text": text, "y": y, "size": 9.0, "bold": False}

    h = 800.0
    # Bottom margin: where a folio is normally set, and preferred when both margins offer one.
    assert _printed_folio([line("188", 776.0), line("4", 8.0)], h) == "188"
    assert _printed_folio([line("- 12 -", 780.0)], h) == "12"
    # A column heading in the top band is not a page number. Without this, every statement page
    # of a 2025 filing would report its printed page as 2025.
    assert _printed_folio([line("2025", 20.0)], h) is None
    # Inside the body, no number is a folio however folio-shaped it looks.
    assert _printed_folio([line("42", 400.0)], h) is None
    assert _printed_folio([line("Total equity", 776.0)], h) is None
    # A page that prints no folio says so, rather than being given its position.
    assert _printed_folio([], h) is None
    # No page height (a page whose spans could not be read) cannot locate a margin.
    assert _printed_folio([line("188", 776.0)], 0.0) is None


def test_the_pages_screen_serves_the_printed_number_beside_the_file_position(client):
    """The fixture prints folios 00-08 on file pages 1-9 — the off-by-one an analyst sees as "the
    hyperlink went a page too far". Both numbers are served, so neither has to be inferred."""
    doc_id = _upload(client, make_hk_running_header_report_pdf())
    pages = client.get(f"/api/v1/documents/{doc_id}/pages").json()["pages"]
    assert [p["no"] for p in pages] == list(range(1, 10))
    assert [p["printed"] for p in pages] == [f"{i:02d}" for i in range(9)]


def test_a_page_with_no_printed_number_serves_null_not_a_guess(client):
    doc_id = _upload(client, make_native_pdf())
    pages = client.get(f"/api/v1/documents/{doc_id}/pages").json()["pages"]
    assert pages[0]["printed"] is None


# --- searching the source ----------------------------------------------------------------------

def test_search_returns_the_page_and_a_normalized_box_for_every_hit(client):
    doc_id = _upload(client, make_hk_running_header_report_pdf())
    body = client.get(f"/api/v1/documents/{doc_id}/search", params={"q": "Cash and cash"}).json()
    assert body["count"] >= 2, body
    for hit in body["hits"]:
        b = hit["bbox"]
        assert 0.0 <= b["x0"] < b["x1"] <= 1.0
        assert 0.0 <= b["y0"] < b["y1"] <= 1.0
        assert "cash" in hit["snippet"].lower()
        # Each hit names its page BOTH ways, for the same reason the pages screen does.
        assert isinstance(hit["page_index"], int)
        assert hit["printed_page"] == f"{hit['page_index']:02d}"
    # The face page and the note page both carry the phrase, and the hits are in document order.
    assert [h["page_index"] for h in body["hits"]] == sorted(h["page_index"] for h in body["hits"])


def test_search_with_no_match_is_an_empty_result_not_an_error(client):
    doc_id = _upload(client, make_hk_running_header_report_pdf())
    body = client.get(f"/api/v1/documents/{doc_id}/search",
                      params={"q": "goodwill impairment reversal"}).json()
    assert body["count"] == 0 and body["hits"] == [] and body["truncated"] is False


def test_search_caps_its_result_and_says_that_it_did(client):
    """A one-letter-common term matches on every page; the response has to distinguish "these are
    all the hits" from "these are the first n"."""
    doc_id = _upload(client, make_hk_running_header_report_pdf())
    body = client.get(f"/api/v1/documents/{doc_id}/search",
                      params={"q": "the", "limit": 3}).json()
    assert body["count"] == 3 and body["truncated"] is True


def test_search_is_refused_for_a_spreadsheet(client):
    """A workbook has no pages to search or to highlight; the client is told so rather than
    getting an empty result that reads as "nothing in this document says that"."""
    doc_id = _upload(client, make_xlsx(), "book.xlsx",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    r = client.get(f"/api/v1/documents/{doc_id}/search", params={"q": "cash"})
    assert r.status_code == 400


def test_search_needs_a_document_that_exists(client):
    r = client.get("/api/v1/documents/00000000-0000-0000-0000-000000000000/search",
                   params={"q": "cash"})
    assert r.status_code == 404
