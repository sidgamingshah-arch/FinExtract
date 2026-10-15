"""Which period a column is, and whose figures a page is — and the record of why.

Two readings that every downstream check takes on trust, because a column read as the wrong year or
a page read as the wrong entity is internally consistent and balances:

* PERIODS. When a filing prints the original comparative beside a restated one, the RESTATED one is
  the comparative (``restatement_rule``: "load it as the comparative and set restated: true"); the
  original is kept as ``prior_original`` and flagged. Every fact of a restated column carries
  ``restated``. Columns ordered by position — no heading date, no PRC period caption — carry
  ``period_by_position``, and the log says so. A heading that prints a whole date fills
  ``ValueKey.period_end``, and every reader of a slot still finds it by basis and label.
* PAGE CONTEXT. Each face page records why it has its entity (``scope_reason`` on the page's
  evidence: the title, a Group | Company band, carried from page N, a person's correction, a
  Company-only line the reader found, the past-the-notes repeat, the Indian ahead-of-notes rule),
  and ``scope_conflict`` when the title and the band disagree. The pages payload serves both, and
  Page Scope shows them in one line.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest

from app.core.models.document import PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import BBox
from app.core.models.line_item import ExtractedValue, LineItem
from app.services import row_reconstruct as rr
from app.services.row_reconstruct import Word, build_line_items


def _w(text: str, x: float, y: float, width: float = 0.06) -> Word:
    return Word(text=text, bbox=BBox(x0=x, y0=y, x1=x + width, y1=y + 0.012))


def _build(words, **kw):
    logs: list[str] = []
    items, _ = build_line_items(words, page_index=0, document_id=None, source_kind="native",
                               log=logs.append, **kw)
    return items, logs


def _row(items, caption: str) -> LineItem:
    return next(i for i in items if caption in i.source_label)


def _by_label(item: LineItem) -> dict[str, tuple[str, list[str], date | None]]:
    return {v.period_label: (str(v.value), list(v.confidence.flags), v.period_end)
            for v in item.values.values()}


def _figures(y: float, caption: str, *cells: tuple[float, str]) -> list[Word]:
    return [_w(caption, 0.10, y), *(_w(v, x, y) for x, v in cells)]


# ── a restated comparative is the comparative ────────────────────────────────────────────────────

def _original_beside_restated(marker: str = "(restated)") -> list[Word]:
    return [
        _w("2024", 0.62, 0.05), _w("2023", 0.74, 0.05), _w("2023", 0.86, 0.05),
        _w(marker, 0.86, 0.068, 0.08),
        *_figures(0.11, "Inventories", (0.62, "1,000"), (0.74, "900"), (0.86, "950")),
        *_figures(0.15, "Cash", (0.62, "3,000"), (0.74, "2,900"), (0.86, "2,950")),
        *_figures(0.19, "Receivables", (0.62, "2,000"), (0.74, "1,900"), (0.86, "1,950")),
    ]


def test_the_restated_comparative_is_prior_and_the_original_is_kept_beside_it():
    items, logs = _build(_original_beside_restated())
    got = _by_label(_row(items, "Inventories"))
    # The spread, the exports and the checks read "prior": it is now the restated 950. The 900 the
    # filing replaced is kept, under its own label, and says what it is.
    assert got["current"][:2] == ("1000", [])
    assert got["prior"][:2] == ("950", ["restated"])
    assert got["prior_original"][:2] == ("900", ["prior_original"])
    assert set(got) == {"current", "prior", "prior_original"}
    assert any("period_selection=restated_over_original(consolidated/prior;"
               "original_kept_as=prior_original)" in m for m in logs), logs


def test_the_spread_reads_the_restated_figure_as_the_comparative():
    from app.services.periods import split_current_prior

    items, _ = _build(_original_beside_restated("經重列"))
    vals = [{"period_label": v.period_label, "value": str(v.value)}
            for v in _row(items, "Cash").values.values()]
    cur, pri = split_current_prior(vals)
    assert (cur["value"], pri["value"]) == ("3000", "2950")


def test_every_fact_of_a_restated_column_carries_restated():
    """The CR-03 probe: 31.12.2024 | 31.12.2023 (restated) | 1.1.2023 (restated). Both restated
    columns are flagged on every fact, and the current one is not."""
    words = [
        _w("31.12.2024", 0.50, 0.05, 0.08), _w("31.12.2023", 0.65, 0.05, 0.08),
        _w("1.1.2023", 0.80, 0.05, 0.08),
        _w("(restated)", 0.65, 0.068, 0.08), _w("(restated)", 0.80, 0.068, 0.08),
        *_figures(0.12, "Inventories", (0.50, "1,000"), (0.65, "900"), (0.80, "800")),
        *_figures(0.16, "Receivables", (0.50, "2,000"), (0.65, "1,900"), (0.80, "1,800")),
        *_figures(0.20, "Cash", (0.50, "3,000"), (0.65, "2,900"), (0.80, "2,800")),
    ]
    items, _ = _build(words)
    for caption in ("Inventories", "Receivables", "Cash"):
        got = _by_label(_row(items, caption))
        assert got["current"][1] == []
        assert got["prior"][1] == ["restated"]
        assert got["col2"][1] == ["restated"]


def test_without_the_declared_markers_nothing_is_restated():
    """The markers come from the rulebook's sentence; with none named, the second 2023 column is
    an ordinary second comparative and the first keeps "prior", as before the rule."""
    from tests.test_scope_selection import _scope

    items, _ = _build(_original_beside_restated(),
                      scope=_scope(**{"period_selection.restatement_rule": "keep both"}))
    got = _by_label(_row(items, "Inventories"))
    assert got["prior"][:2] == ("900", [])
    assert got["prior_col2"][:2] == ("950", [])


def test_a_kept_original_is_still_a_period_key_to_the_citation_reader():
    from app.services.note_columns import _key_base

    assert _key_base("prior_original") == "prior"
    assert _key_base("prior_restated") == "prior"


# ── a period read by position says so ───────────────────────────────────────────────────────────

def _no_heading() -> list[Word]:
    return [*_figures(0.20, "Inventories", (0.55, "900"), (0.75, "1,000")),
            *_figures(0.23, "Cash", (0.55, "2,900"), (0.75, "3,000")),
            *_figures(0.26, "Receivables", (0.55, "1,900"), (0.75, "2,000"))]


def test_columns_ordered_by_position_are_flagged_and_logged():
    items, logs = _build(_no_heading())
    got = _by_label(_row(items, "Inventories"))
    assert got["current"][:2] == ("900", ["period_by_position"])
    assert got["prior"][:2] == ("1000", ["period_by_position"])
    assert any("period_selection=by_position(consolidated:2_columns" in m for m in logs), logs


def test_dated_columns_are_not_read_by_position():
    words = [_w("2023", 0.55, 0.05), _w("2024", 0.75, 0.05), *_no_heading()]
    items, logs = _build(words)
    got = _by_label(_row(items, "Inventories"))
    assert got == {"prior": ("900", [], None), "current": ("1000", [], None)}
    assert not any("by_position" in m for m in logs), logs


def test_a_prc_period_caption_is_not_a_positional_reading():
    words = [_w("期末余额", 0.55, 0.05, 0.08), _w("期初余额", 0.75, 0.05, 0.08), *_no_heading()]
    items, logs = _build(words)
    got = _by_label(_row(items, "Inventories"))
    assert got["current"][:2] == ("900", [])
    assert got["prior"][:2] == ("1000", [])
    assert not any("by_position" in m for m in logs), logs


def test_a_single_column_is_not_ordered_by_anything():
    words = [*_figures(0.20, "Inventories", (0.75, "1,000")),
             *_figures(0.23, "Cash", (0.75, "3,000")),
             *_figures(0.26, "Receivables", (0.75, "2,000"))]
    items, logs = _build(words)
    assert _by_label(_row(items, "Cash")) == {"current": ("3000", [], None)}
    assert not any("by_position" in m for m in logs), logs


def test_the_workbook_reader_flags_a_positional_sheet_too():
    pytest.importorskip("openpyxl")
    from app.services.excel_extract import extract_workbook
    from tests.test_excel_extract import _sheet

    logs: list[str] = []
    items = extract_workbook(_sheet([["Trade receivables", 3410, 2900],
                                     ["Inventories", 1200, 1100]]),
                             document_id="x", log=logs.append)
    tr = next(li for li in items if li.source_label == "Trade receivables")
    assert {v.period_label: v.confidence.flags for v in tr.values.values()} == {
        "current": ["period_by_position"], "prior": ["period_by_position"]}
    assert any("period_selection=by_position" in m for m in logs), logs


# ── the printed date fills period_end ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("heads, want", [
    (("31 December 2024", "31 December 2023"), (date(2024, 12, 31), date(2023, 12, 31))),
    (("31.12.2024", "31.12.2023"), (date(2024, 12, 31), date(2023, 12, 31))),
    (("2024年12月31日", "2023年12月31日"), (date(2024, 12, 31), date(2023, 12, 31))),
    # A bare year is not a period end.
    (("2024", "2023"), (None, None)),
])
def test_a_heading_that_prints_a_date_fills_period_end(heads, want):
    words = [*(_w(part, 0.55 + 0.20 * i + 0.035 * j, 0.05, 0.03)
               for i, h in enumerate(heads) for j, part in enumerate(h.split())),
             *_no_heading()]
    items, _ = _build(words)
    got = _by_label(_row(items, "Cash"))
    assert (got["current"][2], got["prior"][2]) == want
    assert (got["current"][0], got["prior"][0]) == ("2900", "3000")


def test_the_workbook_reader_fills_period_end_and_takes_the_restated_comparative():
    pytest.importorskip("openpyxl")
    from app.services.excel_extract import extract_workbook
    from tests.test_excel_extract import _sheet

    items = extract_workbook(_sheet([
        ["", "31 December 2024", "31 December 2023", "31 December 2023 (restated)"],
        ["Trade receivables", 3410, 2900, 2950]]), document_id="x")
    tr = next(li for li in items if li.source_label == "Trade receivables")
    got = {v.period_label: (int(v.value), v.confidence.flags, v.period_end)
           for v in tr.values.values()}
    assert got == {"current": (3410, [], date(2024, 12, 31)),
                   "prior": (2950, ["restated"], date(2023, 12, 31)),
                   "prior_original": (2900, ["prior_original"], date(2023, 12, 31))}


def _fact(value, label="current", end=None):
    return ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                          basis=Basis.CONSOLIDATED, period_label=label, period_end=end)


def test_a_slot_is_found_by_its_label_whatever_date_it_carries():
    li = LineItem(source_label="Cash")
    li.set_value(_fact(3000, end=date(2024, 12, 31)))
    assert li.get_value(Basis.CONSOLIDATED, period_label="current").value == 3000
    assert li.get_value(Basis.CONSOLIDATED, date(2024, 12, 31), "current").value == 3000
    assert li.get_value(Basis.CONSOLIDATED, date(2023, 12, 31), "current") is None


def test_a_second_reading_of_a_dated_slot_does_not_sit_beside_it():
    """A figure read under no column has no date; the dated one already holds "current". Keyed by
    the whole key they would be two "current" values on one row."""
    li = LineItem(source_label="Cash")
    rr.store_fact(li, _fact(3000, end=date(2024, 12, 31)))
    rr.store_fact(li, _fact(3100))
    assert [(v.period_label, str(v.value)) for v in li.values.values()] == [("current", "3000")]
    dims = ("entity_scope", "period")
    rr.store_fact(li, _fact(3200), dims)
    assert sorted(v.period_label for v in li.values.values()) == ["current", "current#2"]


def test_two_captions_held_equal_are_compared_column_by_column_whatever_their_dates():
    """`equivalence` and the containment arithmetic compare by (basis, period label). The face's
    "31 December 2024" and a note's "2024" give the same column different keys."""
    from app.stages.map_ontology import MapOntologyStage

    face = LineItem(source_label="Total", canonical_key="a")
    face.set_value(_fact(1000, end=date(2024, 12, 31)))
    part = LineItem(source_label="Part", canonical_key="b")
    part.set_value(_fact(600))
    assert MapOntologyStage._unexplained_columns(face, [part], Decimal(1)) == [
        next(iter(face.values))]


def test_the_served_values_carry_the_printed_date():
    from app.api.routes.extractions import _serialize_rows
    from app.core.models.document import DocumentModel

    li = LineItem(source_label="Cash", canonical_key="bs_ca__cash")
    li.set_value(_fact(3000, end=date(2024, 12, 31)))
    li.set_value(_fact(2900, label="prior"))
    rows = _serialize_rows(DocumentModel(line_items=[li]))
    assert {v["period_label"]: v["period_end"] for v in rows[0]["values"]} == {
        "current": "2024-12-31", "prior": None}


# ── why a page has its entity ────────────────────────────────────────────────────────────────────

def _classified(data: bytes):
    pytest.importorskip("fitz")
    from app.services.documents import analyze_document

    doc, _ctx = analyze_document(data, filename="scope.pdf")
    return doc.pages


def _reason(page) -> dict | None:
    return (page.evidence or {}).get("scope_reason")


def test_a_title_and_a_repeat_past_the_notes_are_recorded():
    from tests.fixtures import generate as g

    pages = _classified(g.make_company_statement_after_notes_pdf())
    assert _reason(pages[0]) == {"entity": "consolidated", "rule": "title",
                                 "title": "CONSOLIDATED STATEMENT OF FINANCIAL POSITION"}
    assert _reason(pages[1]) is None                                    # a notes page
    assert _reason(pages[2]) == {"entity": "company", "rule": "repeat_after_notes",
                                 "title": "STATEMENT OF FINANCIAL POSITION"}


def test_an_untitled_continuation_names_the_page_it_was_carried_from():
    from tests.fixtures import generate as g

    pages = _classified(g.make_consolidated_statement_spanning_two_pages_pdf())
    assert _reason(pages[1]) == {"entity": "consolidated", "rule": "carried", "from_page": 0}


def test_a_statement_page_reclaimed_between_two_faces_names_the_page_before():
    from tests.fixtures import generate as g

    pages = _classified(g.make_statement_spanning_three_pages_pdf())
    assert pages[1].evidence.get("reclaimed_between_face_pages") is True
    assert _reason(pages[1]) == {"entity": "consolidated", "rule": "reclaimed", "from_page": 0}


def test_a_band_that_overrides_the_title_is_a_conflict():
    """The title says consolidated; the column headings name the Group and the Company. The band
    decides, as it always has — and the page now says the two disagreed."""
    from tests.fixtures import generate as g

    pages = _classified(g.make_group_company_pdf())
    assert _reason(pages[0])["rule"] == "band"
    assert _reason(pages[0])["entity"] == "mixed"
    assert pages[0].evidence["scope_conflict"] == {"title": "consolidated",
                                                   "band": ["company", "consolidated"]}
    assert "scope_conflict" in pages[0].classification_evidence


def test_a_band_under_a_title_that_names_no_entity_is_not_a_conflict():
    from tests.fixtures import generate as g

    pages = _classified(g.make_company_limited_header_pdf())
    assert _reason(pages[0])["rule"] == "band"
    assert "scope_conflict" not in pages[0].evidence
    assert "scope_conflict" not in pages[0].classification_evidence


def test_the_indian_rule_and_a_notes_supplement_are_recorded():
    from tests.fixtures import generate as g

    indian = _classified(g.make_indian_standalone_with_notes_pdf("BALANCE SHEET"))
    assert _reason(indian[1])["rule"] == "indian_title_without_consolidated"
    assert _reason(indian[1])["entity"] == "company"
    cas = _classified(g.make_cas_cash_flow_supplement_in_notes_pdf())
    assert _reason(cas[3]) == {"entity": "consolidated", "rule": "supplement_in_notes",
                               "chapter": "consolidated"}


def test_a_title_that_names_no_entity_is_left_to_the_reader():
    from tests.fixtures import generate as g

    pages = _classified(g.make_native_pdf())
    assert _reason(pages[0]) == {"entity": None, "rule": "unresolved", "why": "titled_page"}


def test_a_correction_records_itself_and_clears_the_conflict():
    from app.services import page_overrides as po

    pages = [PageSource(index=0, kind=PageKind.FACE, statement="balance_sheet", scope="mixed",
                        classification_evidence=["scope_conflict"],
                        evidence={"scope_reason": {"entity": "mixed", "rule": "band"},
                                  "scope_conflict": {"title": "consolidated", "band": []}}),
             PageSource(index=1, kind=PageKind.FACE, statement="balance_sheet", scope="mixed",
                        evidence={"matched_title": None})]
    po.apply(pages, po.normalise([{"page": 0, "parts": [
        {"from_y": 0.0, "kind": "face", "statement": "balance_sheet", "entity": "company"}]}]))
    assert pages[0].evidence["scope_reason"] == {"entity": "company", "rule": "corrected"}
    assert "scope_conflict" not in pages[0].evidence
    assert "scope_conflict" not in pages[0].classification_evidence
    assert pages[1].evidence["scope_reason"] == {"entity": "company",
                                                 "rule": "corrected_carried", "from_page": 0}


def _company_only_face() -> list[Word]:
    return [_w("2024", 0.74, 0.05), _w("2023", 0.86, 0.05),
            *_figures(0.09, "Investments in subsidiaries", (0.74, "8,000"), (0.86, "7,500")),
            *_figures(0.13, "Trade receivables", (0.74, "310"), (0.86, "270")),
            *_figures(0.17, "Cash", (0.74, "105"), (0.86, "90"))]


def test_the_reader_says_what_it_decided_the_entity_from():
    decided: list[dict] = []
    _build(_company_only_face(), on_face=True, scope_out=decided)
    assert decided == [{"rule": "company_marker", "entity": "company",
                        "text": "Investments in subsidiaries"}]
    decided.clear()
    _build(_company_only_face(), on_face=True, page_scope="consolidated", scope_out=decided)
    assert decided == [{"rule": "page_scope", "entity": "consolidated"}]
    decided.clear()
    _build(_no_heading(), on_face=True, scope_out=decided)
    assert decided == [{"rule": "default", "entity": "consolidated"}]


def test_a_company_only_line_becomes_the_reason_on_a_page_the_classifier_left_open():
    from app.services.pdf_extract import _record_reader_scope

    ps = PageSource(index=4, kind=PageKind.FACE, statement="balance_sheet",
                    evidence={"scope_reason": {"entity": None, "rule": "unresolved",
                                               "why": "titled_page"}})
    _record_reader_scope(ps, {"rule": "company_marker", "entity": "company",
                              "text": "Investments in subsidiaries"}, lambda _m: None)
    assert ps.evidence["scope_reason"] == {"entity": "company", "rule": "company_marker",
                                           "text": "Investments in subsidiaries", "by": "reader"}
    # A title that named the entity keeps its reason; a band the reader found against it is the
    # conflict, logged.
    logs: list[str] = []
    titled = PageSource(index=5, kind=PageKind.FACE, statement="balance_sheet",
                        scope="consolidated",
                        evidence={"scope_reason": {"entity": "consolidated", "rule": "title",
                                                   "title": "Consolidated balance sheet"}})
    _record_reader_scope(titled, {"rule": "band", "band": ["consolidated", "standalone"]},
                         logs.append)
    assert titled.evidence["scope_reason"]["rule"] == "title"
    assert titled.evidence["scope_conflict"] == {"title": "consolidated",
                                                 "band": ["consolidated", "standalone"],
                                                 "by": "reader"}
    assert "scope_conflict" in titled.classification_evidence
    assert any("page=5:scope_conflict" in m for m in logs), logs


# ── served on the pages payload ──────────────────────────────────────────────────────────────────

def _upload(client, data: bytes, tag: str) -> str:
    res = client.post("/api/v1/documents",
                      files={"file": ("scope.pdf", data + f"\n%test {tag}\n".encode(),
                                      "application/pdf")})
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _card(payload, no):
    return next(c for c in payload["pages"] if c["no"] == no)


def test_the_pages_payload_serves_why_each_page_has_its_entity(client):
    pytest.importorskip("fitz")
    from tests.fixtures import generate as g

    doc_id = _upload(client, g.make_company_statement_after_notes_pdf(), "why")
    payload = client.get(f"/api/v1/documents/{doc_id}/pages").json()
    assert _card(payload, 1)["scope_reason"]["rule"] == "title"
    assert _card(payload, 2)["scope_reason"] is None
    assert _card(payload, 3)["scope_reason"]["rule"] == "repeat_after_notes"
    assert _card(payload, 3)["scope_conflict"] is None

    banded = _upload(client, g.make_group_company_pdf(), "why-band")
    card = _card(client.get(f"/api/v1/documents/{banded}/pages").json(), 1)
    assert card["scope_conflict"] == {"title": "consolidated", "band": ["company", "consolidated"]}


def _company_only_pdf() -> bytes:
    """One untitled-entity balance sheet printing the Company's investment in its subsidiaries."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "STATEMENT OF FINANCIAL POSITION")
    c.setFont("Helvetica", 9)
    y -= 24
    c.drawRightString(430, y, "2025")
    c.drawRightString(510, y, "2024")
    c.setFont("Helvetica", 10)
    for label, cur, pri in (("Investments in subsidiaries", "8,000", "7,500"),
                            ("Trade receivables", "310", "270"),
                            ("Cash and bank balances", "105", "90"),
                            ("Total assets", "8,415", "7,860")):
        y -= 20
        c.drawString(72, y, label)
        c.drawRightString(430, y, cur)
        c.drawRightString(510, y, pri)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_a_run_records_the_company_only_line_on_its_page():
    """Through the whole pipeline: the classifier leaves the untitled-entity page open, the reader
    files it as the Company's from its "Investments in subsidiaries" line, and the run's page says
    so. The page's own entity stays unread — the reader's verdict is reported, not written back."""
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    from app.services.documents import run_extraction

    doc, ctx = run_extraction(_company_only_pdf(), filename="marker.pdf")
    page = doc.pages[0]
    assert page.scope is None
    assert page.evidence["scope_reason"] == {"entity": "company", "rule": "company_marker",
                                             "text": "Investments in subsidiaries", "by": "reader"}
    assert page.evidence["reader_scope"]["rule"] == "company_marker"
    assert {v.basis for li in doc.line_items for v in li.values.values()} == {Basis.STANDALONE}


def test_the_pages_payload_takes_the_reason_the_last_run_recorded(client):
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    doc_id = _upload(client, _company_only_pdf(), "marker")
    before = _card(client.get(f"/api/v1/documents/{doc_id}/pages").json(), 1)
    assert before["scope_reason"] == {"entity": None, "rule": "unresolved", "why": "titled_page"}

    decided = {"entity": "company", "rule": "company_marker",
               "text": "Investments in subsidiaries", "by": "reader"}
    with SessionLocal() as session:
        run = ExtractionRun(id=f"why-{doc_id[:8]}", document_id=doc_id, status="succeeded",
                            options={}, progress={},
                            result={"pages": [{"index": 0, "evidence": {
                                "scope_reason": decided,
                                "reader_scope": {"rule": "company_marker", "entity": "company",
                                                 "text": "Investments in subsidiaries"}}}]})
        session.add(run)
        session.commit()
        run_id = run.id
    try:
        after = _card(client.get(f"/api/v1/documents/{doc_id}/pages").json(), 1)
        assert after["scope_reason"] == decided
        assert after["entity"] is None
        # Once the pages are read another way, that run no longer speaks for them.
        client.put(f"/api/v1/documents/{doc_id}/sections",
                   json={"sections": [{"id": "notes", "on": False}]})
        stale = _card(client.get(f"/api/v1/documents/{doc_id}/pages").json(), 1)
        assert stale["scope_reason"]["rule"] == "unresolved"
    finally:
        with SessionLocal() as session:
            session.delete(session.get(ExtractionRun, run_id))
            session.commit()
