"""`anywhere` REACHES EVERY PAGE — the route's name made true.

NEW FILE -> backend/tests/test_anywhere_all_pages.py

WHAT THE ROUTE PROMISED AND COULD NOT DO. `tests/test_anywhere_route.py` pins the one thing it
did: `services.note_sections.open_to` refuses to narrow its note search to its own section. That
is a note-selection rule, and the config screen was describing something much larger — "nothing is
constrained". The pages of a filing that are NEITHER a statement nor a note were unreachable by
every route, for a reason no route could fix: `services.pdf_extract` reconstructs NOTES pages and
FACE pages with a resolved statement, so a five-year summary or a schedule the classifier could not
name had no rows to cite. "Search everywhere" had nowhere to search.

THREE CHANGES, and any one alone leaves the route decorative:

  * EXTRACTION WIDENS. A line declaring `anywhere` makes `pdf_extract` reconstruct every page. It
    is gated on the CONFIGURATION rather than on a setting, and none of the shipped 527 lines
    declares the route, so a shipped run pays nothing.
  * THE PAGES ARE SUPPLIED. `face_context.other_page_rows` blocks them by page, with the folio the
    page prints beside the position the citation is resolved against.
  * A CITATION MAY NAME ONE. `SourceRef.page`, resolved by `note_sourced.resolve_sources` against
    `face_context.other_page_index` — and refused for every other route, because every other
    route's author said where the figure is printed.

AND THE FIGURE IS FLAGGED. `stages.normalize` reads the units a STATEMENT page declares and scales
the document by them; a page the classifier could not name declares nothing this run resolved, so
its magnitude is a step less certain. The line goes to review with the page named rather than
publishing as if it were a statement row.
"""
from __future__ import annotations

import json as _json
import pathlib as _pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.line_items import load_line_item_set
from app.services import face_context, line_item_llm, line_item_routes
from app.services.mapping import SourceRef
from app.services.note_sourced import resolve_sources

FACE_PAGE, OTHER_PAGE = 0, 1
_SEED = (_pathlib.Path(__file__).resolve().parent.parent
         / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(autouse=True)
def _restore_extraction_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()), st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, st.llm.provider) = (
            was[0], was[1], was[2], was[3])


def _row(caption, *, value, page, key=None):
    li = LineItem(source_label=caption, canonical_key=key)
    li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                    value=Decimal(value), value_raw=Decimal(value),
                                    provenance=Provenance(page_index=page))
    return li


def _doc(*, folio=None):
    """A balance-sheet page and a page the classifier could not name."""
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=FACE_PAGE, kind=PageKind.FACE, statement="balance_sheet"),
                 PageSource(index=OTHER_PAGE, kind=PageKind.UNKNOWN, statement=None,
                            printed_page=folio)]
    doc.line_items = [_row("Buildings", value="4200", page=FACE_PAGE, key="bs_nca__buildings"),
                      _row("Five-year turnover", value="8100", page=OTHER_PAGE)]
    return doc


def _set(route):
    return load_line_item_set({
        "section_defaults": {"bs": {"statement": "balance_sheet",
                                    "section_scope": ["bs_nca"]}},
        "items": [{"key": "x", "label": "Turnover five years", "inherits": "bs",
                   "route": route, "definition": "d", "type": "extracted"}]}, resolve=True)


# ── THE BLOCK ────────────────────────────────────────────────────────────────────────────────

def test_other_page_rows_carries_the_pages_that_are_neither_a_statement_nor_a_note():
    blocks = face_context.other_page_rows(_doc())
    assert [b["page"] for b in blocks] == [OTHER_PAGE + 1], "the page number is 1-based"
    assert blocks[0]["rows"] == [{"caption": "Five-year turnover",
                                 "figures": {"current": "8100"}}]


def test_a_statement_row_is_not_in_the_other_pages_block():
    """THE TWO BLOCKS MUST NOT OVERLAP. A row offered twice under two identities invites two
    citations for one fact, and the predicate here is the inverse of `pdf_extract`'s target set."""
    rows = [r for b in face_context.other_page_rows(_doc()) for r in b["rows"]]
    assert not [r for r in rows if r["caption"] == "Buildings"]


def test_a_notes_page_is_not_in_the_other_pages_block():
    doc = _doc()
    doc.pages[OTHER_PAGE].kind = PageKind.NOTES
    assert face_context.other_page_rows(doc) == []


def test_the_folio_travels_beside_the_position_when_the_page_prints_one():
    """Both numbers are true and neither substitutes for the other — an annual report's front
    matter means the folio routinely runs several pages behind the file position."""
    assert face_context.other_page_rows(_doc(folio="118"))[0]["printed_page"] == "118"
    assert "printed_page" not in face_context.other_page_rows(_doc())[0]


def test_no_deterministic_proposal_travels_with_these_rows():
    """There is none to travel: `map_ontology` gates a caption match on the page's statement, so a
    page with no statement reaches no alias index and these rows are unclaimed by construction."""
    rows = [r for b in face_context.other_page_rows(_doc()) for r in b["rows"]]
    assert all("line" not in r for r in rows)


def test_the_index_and_the_block_agree_about_which_rows_exist():
    """The model must not be shown a row it is then told does not resolve."""
    shown = {(b["page"], r["caption"]) for b in face_context.other_page_rows(_doc())
             for r in b["rows"]}
    indexed = {(pg, cap) for pg, cap, _row in face_context.other_page_index(_doc())}
    assert shown == indexed


# ── THE CITATION ─────────────────────────────────────────────────────────────────────────────

def test_a_page_citation_resolves_for_a_line_that_may_read_every_page():
    res, un = resolve_sources([SourceRef(page=OTHER_PAGE + 1, caption="Five-year turnover")], [],
                              pages=face_context.other_page_index(_doc()), allow_pages=True)
    assert not un, un
    assert res[0]["figures"] == {"current": "8100"}
    assert res[0]["page"] == OTHER_PAGE + 1
    assert res[0]["off_statement"] is True, "the caller needs to know the scale is unverified"
    assert res[0]["provenance"] is not None, "the page and bbox come off the extracted row"


def test_a_page_citation_is_refused_for_every_other_route():
    res, un = resolve_sources([SourceRef(page=OTHER_PAGE + 1, caption="Five-year turnover")], [],
                              pages=face_context.other_page_index(_doc()))
    assert not res
    assert "route is `anywhere`" in un[0]["why"], un[0]["why"]


def test_a_page_cited_one_out_resolves_to_nothing_and_says_where_the_caption_is():
    """The caption has to match as well, so an off-by-one never resolves to the neighbour."""
    res, un = resolve_sources([SourceRef(page=99, caption="Five-year turnover")], [],
                              pages=face_context.other_page_index(_doc()), allow_pages=True)
    assert not res
    assert "it is on page 2" in un[0]["why"], un[0]["why"]


def test_a_page_citation_never_falls_through_to_the_notes():
    """The three indexes are alternatives, not a chain: a citation saying page 2 must not publish
    a note's figure because the caption happened to appear there too."""
    from app.core.models.line_item import NoteItem, NotesTable

    note_row = NoteItem(raw_label="Five-year turnover")
    note_row.values["a"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                          value=Decimal("1"), value_raw=Decimal("1"))
    notes = [NotesTable(note_number="7", title="N", items=[note_row])]
    res, un = resolve_sources([SourceRef(page=99, caption="Five-year turnover")], notes,
                              pages=face_context.other_page_index(_doc()), allow_pages=True)
    assert not res, "a page citation resolved against a note"
    assert un


# ── THE REQUEST ──────────────────────────────────────────────────────────────────────────────

def test_the_block_is_omitted_entirely_when_there_is_nothing_to_show():
    plan = type("P", (), {"keys": ("x",), "notes": (), "sections": ()})()
    item = next(iter(_set("anywhere").items))
    assert "other_pages" not in line_item_llm.build_request(plan, {"x": item}, {}, [], [], [])


def test_the_block_is_sent_when_it_is_supplied():
    plan = type("P", (), {"keys": ("x",), "notes": (), "sections": ()})()
    item = next(iter(_set("anywhere").items))
    request = line_item_llm.build_request(plan, {"x": item}, {}, [], [],
                                          face_context.other_page_rows(_doc()))
    assert request["other_pages"][0]["page"] == OTHER_PAGE + 1


def test_the_reply_contract_tells_the_model_how_to_cite_one():
    assert "`other_pages` is supplied" in line_item_llm.REPLY_CONTRACT
    assert "THREE PLACES A FIGURE IS PRINTED" in line_item_llm.REPLY_CONTRACT


# ── END TO END ───────────────────────────────────────────────────────────────────────────────

def _run(route, sources):
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage

    line_items = _set(route)
    doc = _doc()
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = line_items
    ctx.ontology = build_working_view(line_items)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = False
    ctx.settings.extraction.llm_focus_keys = []
    seen: dict = {}

    class _P:
        id = "anywhere"

        def complete_structured(self, *, system, messages, response_schema, **_):
            seen["request"] = _json.loads(messages[-1]["content"])
            return response_schema.model_validate(
                {"answers": [{"key": "x", "confidence": 0.8, "reason": "r",
                              "sources": sources}]}), {}

    ctx.registry.register("llm", "anywhere", lambda: _P())
    ctx.settings.llm.provider = "anywhere"
    LineItemLlmStage().run(doc, ctx)
    return next((r for r in doc.line_items if r.canonical_key == "x"), None), seen, ctx


def test_an_anywhere_line_publishes_a_figure_off_a_page_that_is_neither(_capsys=None):
    row, seen, _ctx = _run("anywhere", [{"page": OTHER_PAGE + 1,
                                         "caption": "Five-year turnover"}])
    assert "other_pages" in seen["request"], "the request did not carry the pages"
    assert row is not None
    assert next(iter(row.values.values())).value == Decimal("8100")
    assert "llm_off_statement_page_scale_unverified:page 2" in row.confidence.flags, \
        row.confidence.flags
    assert "low_mapping_confidence" in row.confidence.flags


def test_a_face_line_is_not_sent_those_pages_and_cannot_cite_one():
    row, seen, ctx = _run("face", [{"page": OTHER_PAGE + 1, "caption": "Five-year turnover"}])
    assert "other_pages" not in seen["request"]
    assert row is None or not [ev for ev in row.values.values() if ev.value is not None]
    assert any("citation NOT resolved" in line for line in ctx.logs), ctx.logs[-4:]


# ── EXTRACTION ───────────────────────────────────────────────────────────────────────────────

def test_the_widening_is_asked_once_per_run_and_the_shipped_set_does_not_ask_for_it():
    shipped = load_line_item_set(_json.loads(_SEED.read_text(encoding="utf-8")), resolve=True)
    assert not line_item_routes.any_reads_every_page(shipped)
    assert line_item_routes.any_reads_every_page(_set("anywhere"))
    assert not line_item_routes.any_reads_every_page(_set("note_tables"))
    assert not line_item_routes.any_reads_every_page(None), \
        "a run with no configured set must not widen"


def _two_page_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _w, h = A4
    for title, rows in (("Consolidated statement of financial position",
                         [("Buildings", "4,200"), ("Land", "1,100")]),
                        ("Five-year financial summary",
                         [("Turnover", "8,100"), ("Profit before tax", "900")])):
        y = h - 72
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, y, title)
        c.setFont("Helvetica", 10)
        for label, value in rows:
            y -= 24
            c.drawString(72, y, label)
            c.drawRightString(500, y, value)
        c.showPage()
    c.save()
    return buf.getvalue()


def _extract(route):
    from app.core.stage import PipelineContext
    from app.services.pdf_extract import extract_pdf

    doc = DocumentModel(filename="ar.pdf", content_hash="h")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, statement="balance_sheet",
                            width_pt=595.0, height_pt=842.0),
                 PageSource(index=1, kind=PageKind.UNKNOWN, statement=None,
                            width_pt=595.0, height_pt=842.0)]
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items = _set(route) if route else None
    extract_pdf(_two_page_pdf(), doc, ctx)
    pages = set()
    for row in doc.line_items:
        for ev in row.values.values():
            if ev.provenance is not None:
                pages.add(int(ev.provenance.page_index))
    return pages, ctx


def test_extraction_reads_only_the_statement_pages_without_the_route():
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    pages, ctx = _extract("face")
    assert pages == {0}, f"a page that is neither a statement nor a note was read anyway: {pages}"
    assert not [line for line in ctx.logs if "anywhere_route_widened" in line]


def test_extraction_reads_every_page_when_a_line_declares_anywhere():
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    pages, ctx = _extract("anywhere")
    assert pages == {0, 1}, f"the widening did not reach the unclassified page: {pages}"
    assert any("anywhere_route_widened_pages=1" in line for line in ctx.logs), ctx.logs[:6]


def test_a_row_the_stage_itself_created_is_not_offered_back_as_printed():
    """`stages.line_item_llm._write` creates a row for a line that had none and gives it the cited
    row's provenance, so it lands on the cited page. Its caption is the LINE'S LABEL, not a printed
    caption, and offering it to the next request would invite a citation of a row nobody typeset."""
    doc = _doc()
    synthesised = _row("Turnover five years", value="8100", page=OTHER_PAGE, key="x")
    synthesised.confidence.method = "llm"
    doc.line_items.append(synthesised)

    captions = {r["caption"] for b in face_context.other_page_rows(doc) for r in b["rows"]}
    assert captions == {"Five-year turnover"}, captions
    assert not [c for _p, c, _r in face_context.other_page_index(doc)
                if c == "Turnover five years"]
