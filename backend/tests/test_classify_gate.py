"""Page classification recognises IFRS/HKFRS P&L phrasing (Req 19), and the integrity gate
is enforced at the API boundary (Req 17)."""
from __future__ import annotations

import io

import pytest

pytest.importorskip("fitz")


def _pnl_or_loss_pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, h - 60, "Consolidated Statement of Profit or Loss")
    c.setFont("Helvetica", 10)
    c.drawString(72, h - 90, "Revenue from operations      20,000")
    c.drawString(72, h - 110, "Operating profit      3,200")
    c.showPage(); c.save()
    return buf.getvalue()


def test_profit_or_loss_page_classifies_as_face():
    from app.core.models import PageKind
    from app.services.documents import run_extraction

    doc, _ = run_extraction(_pnl_or_loss_pdf(), filename="pnl.pdf")
    assert any(p.kind == PageKind.FACE for p in doc.pages), \
        "a 'Statement of Profit or Loss' page should classify as FACE"
    labels = {li.source_label for li in doc.line_items}
    assert any("Revenue" in l for l in labels)


def test_real_shaped_annual_report_classifies_face_and_notes():
    """A realistic HKEX/IFRS report: the auditor's report (which mentions face phrases in prose)
    must NOT be FACE; the three consolidated statements ARE FACE; and the notes section — opened
    by '1 General information' with no 'Notes to…' banner — is captured as NOTES, so continuation
    note pages that mention 'profit or loss' in prose stay NOTES rather than being mis-read as face."""
    from app.core.models import PageKind
    from app.services.documents import run_extraction
    from tests.fixtures.generate import make_annual_report_pdf

    doc, _ = run_extraction(make_annual_report_pdf(), filename="ar.pdf")
    kinds = {p.index: p.kind for p in doc.pages}
    assert kinds[1] != PageKind.FACE, "the auditor's report is not a statement face"
    for i in (2, 3, 4):
        assert kinds[i] == PageKind.FACE, f"page {i} is a consolidated face statement"
    # Both note pages (5: '1 General information', 6: '18 Cash…') are captured as NOTES even
    # though the section opens without a 'Notes to…' banner.
    assert kinds[5] == PageKind.NOTES and kinds[6] == PageKind.NOTES
    assert len(doc.notes_pages()) >= 2


def test_chinese_hk_prc_titles_and_note_patterns():
    """HK-listed PRC entities often file bilingually: the classifier recognises the Chinese
    face-statement titles and note markers, and still rejects a Chinese prose mention that
    isn't a heading."""
    import re

    from app.stages.classify import (
        _NOTES_BANNER, _NUMBERED_HEADING, _resolve_statement, _title_candidates, _title_zone)

    def title(*lines):
        """Resolve a heading through the stage's REAL path: title zone → heading-shaped candidates →
        resolution. Going straight to _resolve_statement would skip the heading filter, which is the
        very thing the prose case below is asserting."""
        raw = [{"text": t, "y": float(i * 20), "size": 14.0, "bold": True}
               for i, t in enumerate(lines)]
        return _resolve_statement(_title_candidates(_title_zone(raw)))[0]

    assert title("合并资产负债表", "货币资金 1,204") == "balance_sheet"
    assert title("綜合現金流量表") == "cash_flow"
    assert title("合并利润表") == "profit_and_loss"
    # A Chinese prose sentence that merely mentions a statement name is not a title heading: it ends
    # in a full stop, so it is not heading-shaped, and the narrative gate on the page rejects it too.
    assert title("独立核数师报告", "我们审计了合并利润表及合并现金流量表。") is None
    assert any(re.search(rx, "合并财务报表附注") for rx in _NOTES_BANNER)
    assert _NUMBERED_HEADING.search("14. 現金及現金等價物")


def test_a_chinese_statement_title_below_a_completed_table_is_recognised():
    from app.stages.classify import _features

    lines = [
        {"text": "某公司2024年年度报告", "y": 0.0, "size": 10.0, "bold": False},
        {"text": "负债合计", "y": 20.0, "size": 10.0, "bold": False},
        {"text": "2,867,784,856.15", "y": 30.0, "size": 10.0, "bold": False},
        {"text": "合并利润表", "y": 400.0, "size": 14.0, "bold": True},
    ]

    feature = _features(74, lines, 800.0, "\n".join(line["text"] for line in lines))

    assert feature.statement == "profit_and_loss"
    assert feature.matched_title == "合并利润表"
    assert feature.matched_title_y == 0.5


def test_running_header_report_regions(client):
    """A report with a bilingual running header on every page (as real HK/PRC filings have):
    the Financial Highlights page that quotes 'Summary of Statement of Profit or Loss' and the
    auditor's report must be OTHER; the three statements (with titles split across two lines)
    are FACE; the two note pages are NOTES; the five-year summary back-matter is OTHER."""
    from app.core.models import PageKind
    from app.services.documents import run_extraction
    from tests.fixtures.generate import make_hk_running_header_report_pdf

    doc, _ = run_extraction(make_hk_running_header_report_pdf(), filename="hk.pdf")
    k = {p.index: p.kind for p in doc.pages}
    assert k[1] != PageKind.FACE, "Financial Highlights quotes a statement name but isn't one"
    assert k[2] != PageKind.FACE, "the auditor's report is not a statement face"
    for i in (3, 4, 5):
        assert k[i] == PageKind.FACE, f"page {i} is a consolidated statement (split title)"
    assert k[6] == PageKind.NOTES and k[7] == PageKind.NOTES
    assert k[8] != PageKind.NOTES, "the five-year summary is back-matter, not a note"


def test_integrity_gate_blocks_extraction(client):
    # A non-document upload is detected as an unknown/corrupt format → BLOCKER integrity finding.
    up = client.post("/api/v1/documents",
                     files={"file": ("junk.pdf", b"this is not a pdf at all", "application/pdf")})
    assert up.status_code in (200, 201), up.text
    doc_id = up.json()["id"]
    report = up.json().get("integrity_report") or {}
    assert any(f.get("severity") == "blocker" for f in report.get("findings", []))
    r = client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "integrity_blocked"


# --- the ordering layer: notes follow the face --------------------------------------------------

def test_a_notes_page_before_the_face_is_refused():
    """The invariant, on the decode path directly: a filing states its statements and then explains
    them, so a page decoded as a note before any face page has been seen is not a note.

    Corrected to the front-matter state rather than to FACE — the page's own evidence did not look
    like a statement, which is why the decode did not choose FACE for it, so refusing the notes
    reading is all this licenses."""
    from app.stages.classify import _FACE, _NOTES, _POST, _PRE, _notes_follow_the_face

    logs: list[str] = []
    got = _notes_follow_the_face([_NOTES, _PRE, _FACE, _NOTES, _POST], log=logs.append)
    assert got == [_PRE, _PRE, _FACE, _NOTES, _POST]
    assert logs and "notes_before_face=[0]" in logs[0] and "first_face=2" in logs[0]


def test_a_face_page_after_the_notes_is_left_alone():
    """NOT the same claim as "the face never follows the notes", which is false: an HKEX filing
    prints the Company's own balance sheet past note 40, and recovering that page is what the
    notes-to-face transition exists for. Only the FIRST face page anchors the invariant."""
    from app.stages.classify import _FACE, _NOTES, _notes_follow_the_face

    path = [_FACE, _NOTES, _NOTES, _FACE, _NOTES]
    assert _notes_follow_the_face(list(path)) == path


def test_a_filing_with_no_face_page_keeps_its_notes():
    """Fail-open, and the reason: with no face page anywhere this cannot know where the face would
    have been, and a notes section uploaded on its own would lose every page it has. Doing nothing
    and saying so beats emptying the notes index to satisfy an invariant it cannot locate."""
    from app.stages.classify import _NOTES, _PRE, _notes_follow_the_face

    logs: list[str] = []
    path = [_PRE, _NOTES, _NOTES]
    assert _notes_follow_the_face(list(path), log=logs.append) == path
    assert any("no_face_page_in_filing" in m for m in logs), logs


def test_a_filing_already_in_order_is_untouched_and_silent():
    """No log for the ordinary case: a run's log is read when something looked wrong, so a line
    that appears on every filing is noise that hides the ones that matter."""
    from app.stages.classify import _FACE, _NOTES, _PRE, _notes_follow_the_face

    logs: list[str] = []
    path = [_PRE, _FACE, _FACE, _NOTES]
    assert _notes_follow_the_face(list(path), log=logs.append) == path
    assert logs == []
