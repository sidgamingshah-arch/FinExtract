"""A person can correct how a filing's pages are read, part by part.

The classifier reads each page's printed titles and decides: face or notes or neither, which
statement, whose figures — and cuts a page where a second statement starts part-way down (000709
page 88 prints the Group's cash flow, then 6、母公司现金流量表 at y=0.77). When it is wrong there was
no way to say so: Page Scope could only switch a page out. An override says, for one page, the
parts it is made of top to bottom, and every later run reads the page that way.
"""
from __future__ import annotations

import io

import pytest

from app.core.models.document import PageSource
from app.core.models.enums import Basis, PageKind
from app.services import page_overrides as po

pytest.importorskip("fitz")


def _parts(*specs):
    return [{"from_y": y, "kind": k, "statement": s, "entity": e} for y, k, s, e in specs]


# ── what an override may say ─────────────────────────────────────────────────────────────────────

def test_parts_are_sorted_and_a_page_with_no_parts_drops_its_override():
    got = po.normalise([
        {"page": 3, "parts": _parts((0.6, "face", "balance_sheet", "company"),
                                    (0.0, "face", "cash_flow", "consolidated"))},
        {"page": 5, "parts": []}])
    assert got == [{"page": 3, "parts": _parts((0.0, "face", "cash_flow", "consolidated"),
                                               (0.6, "face", "balance_sheet", "company"))}]


@pytest.mark.parametrize("entry, says", [
    ({"page": 9, "parts": _parts((0.0, "notes", None, None))}, "not a page of this document"),
    ({"page": 0, "parts": _parts((0.2, "notes", None, None))}, "must start at the top"),
    ({"page": 0, "parts": _parts((0.0, "face", None, None))}, "names its statement"),
    ({"page": 0, "parts": _parts((0.0, "face", "cash_flow", "group"))}, "consolidated or company"),
    ({"page": 0, "parts": _parts((0.0, "table", None, None))}, "kind must be one of"),
    ({"page": 0, "parts": _parts((0.0, "notes", None, None), (0.0, "other", None, None))},
     "same place"),
])
def test_an_override_that_cannot_be_applied_says_which_page_and_why(entry, says):
    with pytest.raises(po.OverrideError, match=says):
        po.normalise([entry], page_count=4)


# ── applied after the classifier ─────────────────────────────────────────────────────────────────

def _page(i, kind, statement=None, scope=None, **evidence):
    return PageSource(index=i, kind=kind, statement=statement, scope=scope, evidence=evidence)


def test_a_page_presents_its_last_face_part_and_loses_the_classifiers_cut():
    pages = [_page(0, PageKind.FACE, "cash_flow", "consolidated", matched_title="5、合并现金流量表",
                   statement_before_title="balance_sheet", closing_title="6、母公司现金流量表")]
    po.apply(pages, po.normalise([{"page": 0, "parts": _parts(
        (0.0, "face", "cash_flow", "consolidated"), (0.77, "face", "cash_flow", "company"))}]))
    p = pages[0]
    assert (p.kind, p.statement, p.scope) == (PageKind.FACE, "cash_flow", "company")
    assert "statement_before_title" not in p.evidence and "closing_title" not in p.evidence
    assert "user_override" in p.classification_evidence


def test_the_last_part_carries_on_to_untitled_continuations_and_stops_at_a_title():
    pages = [_page(0, PageKind.OTHER),
             _page(1, PageKind.FACE, "changes_in_equity", "consolidated", matched_title=None),
             _page(2, PageKind.FACE, "changes_in_equity", "consolidated",
                   matched_title="7、合并所有者权益变动表")]
    logs: list[str] = []
    po.apply(pages, po.normalise([{"page": 0, "parts": _parts(
        (0.0, "face", "cash_flow", "company"))}]), log=logs.append)
    assert [(p.kind, p.statement, p.scope) for p in pages] == [
        (PageKind.FACE, "cash_flow", "company"), (PageKind.FACE, "cash_flow", "company"),
        (PageKind.FACE, "changes_in_equity", "consolidated")]
    assert any("page=1:override_carried_from=0" in line for line in logs)


def test_the_screen_starts_from_the_classifiers_own_cut():
    page = {"kind": "face", "statement": "cash_flow", "scope": "company", "evidence": {
        "matched_title_y": 0.77, "statement_before_title": "cash_flow",
        "scope_before_title": "consolidated"}}
    assert po.classifier_parts(page) == _parts((0.0, "face", "cash_flow", "consolidated"),
                                               (0.77, "face", "cash_flow", "company"))
    assert po.classifier_parts({"kind": "cover"}) == _parts((0.0, "other", None, None))


# ── a run reads the page the way the override says ───────────────────────────────────────────────

def _two_statement_page() -> bytes:
    """One page: an income statement at the top, a balance sheet from about 45% down."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    rows = [(height - 72, "Statement of profit or loss", None),
            (height - 110, "Revenue", "1,000"), (height - 134, "Cost of sales", "(400)"),
            (height - 380, "Statement of financial position", None),
            (height - 420, "Cash and cash equivalents", "2,000"),
            (height - 444, "Trade receivables", "3,000")]
    for y, label, value in rows:
        c.setFont("Helvetica-Bold" if value is None else "Helvetica", 12 if value is None else 10)
        c.drawString(72, y, label)
        if value:
            c.drawRightString(500, y, value)
    c.showPage()
    c.save()
    return buf.getvalue()


def _read(parts):
    from app.services.documents import run_extraction

    overrides = [{"page": 0, "parts": parts}] if parts else None
    doc, ctx = run_extraction(_two_statement_page(), filename="two.pdf",
                              page_overrides=overrides)
    rows = {}
    for li in doc.line_items:
        ev = next(iter(li.values.values()))
        printed_on = [f for f in li.confidence.flags if f.startswith("printed_on:")]
        rows[li.source_label] = (int(ev.value), ev.basis, printed_on[0] if printed_on else None)
    return rows, ctx


def test_two_face_parts_are_read_as_two_statements_with_their_own_entities():
    rows, ctx = _read(_parts((0.0, "face", "profit_and_loss", "consolidated"),
                             (0.42, "face", "balance_sheet", "company")))
    assert rows["Revenue"] == (1000, Basis.CONSOLIDATED, "printed_on:profit_and_loss")
    assert rows["Cash and cash equivalents"] == (2000, Basis.STANDALONE,
                                                 "printed_on:balance_sheet")
    assert any("page=0:override(" in str(x) for x in ctx.logs)


def test_a_part_marked_notes_or_not_read_yields_no_face_rows():
    rows, _ = _read(_parts((0.0, "face", "profit_and_loss", None), (0.42, "other", None, None)))
    assert "Revenue" in rows and "Cash and cash equivalents" not in rows
    rows, _ = _read(_parts((0.0, "face", "profit_and_loss", None), (0.42, "notes", None, None)))
    assert "Cash and cash equivalents" not in rows


# ── saved with the document, shown on Page Scope ─────────────────────────────────────────────────

def _upload(client, data, name):
    return client.post("/api/v1/documents",
                       files={"file": (name, data, "application/pdf")}).json()["id"]


def test_an_override_is_saved_listed_and_refused_when_wrong(client):
    doc_id = _upload(client, _two_statement_page(), "two-api.pdf")
    bad = client.put(f"/api/v1/documents/{doc_id}/page-overrides",
                     json={"overrides": [{"page": 0, "parts": _parts((0.0, "face", None, None))}]})
    assert bad.status_code == 422 and "page 1, part 1" in bad.text

    parts = _parts((0.0, "face", "profit_and_loss", "consolidated"),
                   (0.42, "face", "balance_sheet", "company"))
    res = client.put(f"/api/v1/documents/{doc_id}/page-overrides",
                     json={"overrides": [{"page": 0, "parts": parts}]})
    assert res.status_code == 200, res.text
    card = client.get(f"/api/v1/documents/{doc_id}/pages").json()["pages"][0]
    assert card["overridden"] is True and card["parts"] == parts
    assert (card["kind"], card["statement"], card["entity"]) == ("face", "balance_sheet", "company")

    client.put(f"/api/v1/documents/{doc_id}/page-overrides", json={"overrides": []})
    card = client.get(f"/api/v1/documents/{doc_id}/pages").json()["pages"][0]
    assert card["overridden"] is False


def test_saving_an_override_asks_for_a_new_run_which_records_it(client):
    import time

    doc_id = _upload(client, _two_statement_page(), "two-run.pdf")

    def extract():
        res = client.post(f"/api/v1/documents/{doc_id}/extractions", json={}).json()
        for _ in range(200):
            r = client.get(f"/api/v1/documents/{doc_id}/run")
            if r.status_code == 200 and r.json().get("status") in ("succeeded", "failed"):
                return res["run_id"]
            time.sleep(0.05)
        raise AssertionError("extraction did not finish")

    first = extract()
    assert extract() == first                         # nothing changed: the same run answers
    parts = _parts((0.0, "face", "profit_and_loss", None), (0.42, "other", None, None))
    client.put(f"/api/v1/documents/{doc_id}/page-overrides",
               json={"overrides": [{"page": 0, "parts": parts}]})
    second = extract()
    assert second != first
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as session:
        options = session.get(ExtractionRun, second).options
    assert options["page_overrides"] == [{"page": 0, "parts": parts}]
