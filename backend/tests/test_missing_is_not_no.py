"""Missing is not No, and unread is not absent.

THE YES/NO ROWS. Pledged assets, secured borrowings and derivatives came back "No" at confidence
1.0 whenever ANY of the eight disclosures the presence scan knows was found (none of them about
these topics), related-party transactions "No" whenever the scan missed, and Confirmed with RM "No"
always. Each is now blank, flagged `not_determined` and at low confidence, unless something the run
read bears on it: a note line mapped to the line, or for related-party transactions the related-
party disclosure itself.

THE TARGET CURRENCY. The run converts nothing, so a requested target currency was a label over
figures still in the filing's own currency. It is written only when the figures are in it.

THE UNREAD PAGES. A page left out of the page selection, read only for a switched-off section, or
scanned with no OCR engine has no rows, and a statement on it read exactly like one the filing does
not print: its relations were filed STATEMENT_ABSENT, outside the coverage denominator. The reader
now records each such page on the run, coverage calls the statement NOT_READ and counts it, and the
export's validation caption names the pages.
"""
from __future__ import annotations

import io
import json
import pathlib
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.routes.extractions import NOT_DETERMINED, _build_supplemental_rows
from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind, PageSourceKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable, UnitContext
from app.services import coverage as cov_mod
from app.services import unread_pages
from app.services.coverage import coverage
from app.services.export import unread_caption, validation_caption
from app.services.page_reading import narrow_targets
from app.services.structural_checks import evaluate_structure

_TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
_YES_NO = ("notes__related_party_transactions", "notes__pledged_assets",
           "notes__secure_borrowings", "notes__derivatives", "notes__confirmed_with_rm")


def _supplemental(template: str, *, disclosures=(), notes=(), options=None, currency="HKD"):
    tpl = json.loads((_TEMPLATES / template).read_text(encoding="utf-8"))
    doc = SimpleNamespace(unit_context=UnitContext(currency=currency, scale_factor=Decimal(1000)),
                          notes=list(notes), fmt=None, pages=[])
    out = _build_supplemental_rows(template_def=tpl, base_rows=[], disclosures=list(disclosures),
                                   entity_name="X", doc_model=doc, ctx=None,
                                   options=options or {})
    return {r["canonical_key"]: r for r in out}


def _answer(row):
    return [v["value"] for v in row["values"]]


# --- the Yes/No rows -------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["output_csv_hk_v1_template.json",
                                      "output_csv_indas_v1_template.json"])
@pytest.mark.parametrize("found", [[], [{"key": "going_concern", "present": True}],
                                   [{"key": "auditor_qualification", "present": True},
                                    {"key": "commitments", "present": True}]],
                         ids=["nothing found", "going concern found", "two unrelated found"])
def test_a_miss_or_an_unrelated_disclosure_answers_nothing(template, found):
    rows = _supplemental(template, disclosures=found)
    for key in _YES_NO:
        row = rows[key]
        assert _answer(row) == [], (key, _answer(row))
        assert NOT_DETERMINED in row["flags"], key
        assert "low_mapping_confidence" in row["flags"], key
        assert row["mapping_confidence"] < 0.7, key


def test_the_related_party_disclosure_still_answers_related_party_transactions():
    rows = _supplemental("output_csv_hk_v1_template.json",
                         disclosures=[{"key": "related_party", "present": True, "page": 41}])
    row = rows["notes__related_party_transactions"]
    assert _answer(row) == ["Yes"]
    assert NOT_DETERMINED not in row["flags"]
    # …and it is no evidence for the other three, which it used to turn into "No".
    for key in ("notes__pledged_assets", "notes__secure_borrowings", "notes__derivatives"):
        assert _answer(rows[key]) == [] and NOT_DETERMINED in rows[key]["flags"], key


def test_a_note_line_mapped_to_the_line_answers_yes_from_that_note():
    item = NoteItem(raw_label="Bank deposits pledged", canonical_key="notes__pledged_assets")
    item.values["current"] = ExtractedValue(
        basis=Basis.CONSOLIDATED, period_label="current", value=Decimal("1200"),
        value_raw=Decimal("1200"), provenance=Provenance(page_index=40))
    note = NotesTable(note_number="31", title="Pledge of assets", items=[item])
    rows = _supplemental("output_csv_hk_v1_template.json", notes=[note])
    row = rows["notes__pledged_assets"]
    assert _answer(row) == ["Yes"]
    assert NOT_DETERMINED not in row["flags"]
    assert row["values"][0]["provenance"]["page_index"] == 40
    assert "supplemental:note_line:31" in row["values"][0]["confidence"]["flags"]
    # Confirmed with RM is a workflow record, never a filing's: no note answers it.
    assert NOT_DETERMINED in rows["notes__confirmed_with_rm"]["flags"]


# --- the target currency ---------------------------------------------------------------------

def test_a_target_currency_is_not_written_over_figures_that_were_not_converted():
    row = _supplemental("output_csv_hk_v1_template.json", options={"target_currency": "USD"},
                        currency="HKD")["statement_setup_controls__target_currency"]
    assert _answer(row) == []
    assert "target_currency_not_applied:USD" in row["flags"]


def test_a_target_currency_the_figures_are_already_in_is_written():
    row = _supplemental("output_csv_hk_v1_template.json", options={"target_currency": "hkd"},
                        currency="HKD")["statement_setup_controls__target_currency"]
    assert _answer(row) == ["HKD"]
    nothing = _supplemental("output_csv_hk_v1_template.json")[
        "statement_setup_controls__target_currency"]
    assert _answer(nothing) == [] and not any(f.startswith("target_currency_not_applied")
                                              for f in nothing["flags"])


# --- recording the unread pages --------------------------------------------------------------

def _pages():
    return [PageSource(index=0, kind=PageKind.FACE, statement="balance_sheet", printed_page="12"),
            PageSource(index=1, kind=PageKind.FACE, statement="cash_flow", printed_page="14"),
            PageSource(index=2, kind=PageKind.NOTES)]


def test_the_reader_says_which_pages_a_persons_choices_leave_unread():
    dropped: list = []
    kept = narrow_targets(_pages(), included={0, 2}, dropped=dropped)
    assert [p.index for p in kept] == [0, 2]
    assert [(p.index, why) for p, why in dropped] == [(1, "out_of_scope")]

    dropped = []
    kept = narrow_targets(_pages(), sections_off={"cash_flow"}, dropped=dropped)
    assert [p.index for p in kept] == [0, 2]
    assert [(p.index, why) for p, why in dropped] == [(1, "section_off")]

    e = unread_pages.entry(_pages()[1], "out_of_scope")
    assert e == {"page": 2, "folio": "14", "reason": "out_of_scope", "sections": ["cash_flow"]}


def test_the_statements_not_read():
    entries = [unread_pages.entry(_pages()[1], "out_of_scope")]
    assert unread_pages.statements_not_read(entries) == {"cash_flow"}
    # A statement switched off and found on a page is not read, even where that page was read for
    # another section; one switched off and printed nowhere is simply absent.
    assert unread_pages.statements_not_read([], {"balance_sheet", "profit_and_loss"},
                                            _pages()) == {"balance_sheet"}
    assert unread_pages.statements_not_read([], {"notes"}, _pages()) == set()


def _three_page_pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _w, h = A4
    for title, rows in (("Consolidated statement of financial position",
                         [("Buildings", "4,200"), ("Land", "1,100")]),
                        ("Consolidated statement of cash flows",
                         [("Net cash from operating activities", "900")]),
                        ("Consolidated statement of cash flows (continued)",
                         [("Cash at end of year", "1,300")])):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, h - 72, title)
        c.setFont("Helvetica", 10)
        y = h - 72
        for label, value in rows:
            y -= 24
            c.drawString(72, y, label)
            c.drawRightString(500, y, value)
        c.showPage()
    c.save()
    return buf.getvalue()


def _extract(*, included=None, off=None, ocr_on=True):
    from app.core.stage import PipelineContext
    from app.services.pdf_extract import extract_pdf

    doc = DocumentModel(filename="ar.pdf", content_hash="h")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, statement="balance_sheet",
                            width_pt=595.0, height_pt=842.0, printed_page="12"),
                 PageSource(index=1, kind=PageKind.FACE, statement="cash_flow",
                            width_pt=595.0, height_pt=842.0, printed_page="14"),
                 # No text layer as far as the classifier is concerned: read only through OCR.
                 PageSource(index=2, kind=PageKind.FACE, statement="cash_flow",
                            source_kind=PageSourceKind.SCANNED, width_pt=595.0, height_pt=842.0)]
    settings = get_settings()
    was = (settings.extraction.ocr_scanned_pages, settings.ocr.engine)
    settings.extraction.ocr_scanned_pages, settings.ocr.engine = ocr_on, "stub"
    try:
        ctx = PipelineContext(raw_bytes=b"", settings=settings)
        ctx.included_pages = set(included) if included is not None else None
        ctx.sections_off = set(off) if off else None
        extract_pdf(_three_page_pdf(), doc, ctx)
    finally:
        settings.extraction.ocr_scanned_pages, settings.ocr.engine = was
    return doc, ctx


def test_a_run_records_every_page_it_meant_to_read_and_did_not():
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")

    doc, ctx = _extract()
    assert doc.unread_pages == [{"page": 3, "folio": None, "reason": "scanned_no_ocr",
                                 "sections": ["cash_flow"], "ocr": "none"}]
    assert any("extract:unread_pages=1" in line for line in ctx.logs)

    doc, _ = _extract(ocr_on=False)
    assert [e["ocr"] for e in doc.unread_pages] == ["off"]

    doc, _ = _extract(included={0})
    assert [(e["page"], e["reason"]) for e in doc.unread_pages] == [
        (2, "out_of_scope"), (3, "out_of_scope")]

    doc, _ = _extract(off={"cash_flow"})
    assert [(e["page"], e["reason"]) for e in doc.unread_pages] == [
        (2, "section_off"), (3, "section_off")]


# --- coverage ----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def hk_template():
    from app.schemas.loader import load_template
    return load_template(json.loads((_TEMPLATES / "hkfrs_hk_china_template.json")
                                    .read_text(encoding="utf-8")))


def _facts(figures: dict) -> list[LineItem]:
    out = []
    for key, num in figures.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(num), value_raw=Decimal(num),
                                    basis=Basis.CONSOLIDATED, period_label="current"))
        out.append(li)
    return out


def _balance_sheet_only(template):
    stmt = {k: s for s in template.statements
            for k in [n.canonical_key for n in template._walk(s.sections) if n.canonical_key]}
    return _facts({k: "100" for k, s in stmt.items() if s.type.value == "balance_sheet"})


def test_a_statement_nobody_read_is_not_read_and_counts(hk_template):
    items = _balance_sheet_only(hk_template)
    absent = coverage([r.model_dump(mode="json")
                       for r in evaluate_structure(hk_template, items).results])
    unread = coverage([r.model_dump(mode="json")
                       for r in evaluate_structure(hk_template, items,
                                                   not_read={"cash_flow"}).results])

    assert absent.statements["cash_flow"].status == cov_mod.ABSENT
    cf = unread.statements["cash_flow"]
    assert cf.status == cov_mod.NOT_READ
    assert cf.skips.get("NOT_READ") == cf.declarable > 0
    # It stays in the denominator: the run checked less of what the filing may print.
    assert unread.aggregate.declarable == absent.aggregate.declarable + cf.declarable
    assert unread.aggregate.status != cov_mod.PASSED
    assert {a["statement"] for a in unread.alarms
            if a["code"] == cov_mod.ALARM_NOT_READ} == {"cash_flow"}
    assert "not_read=cash_flow" in unread.headline()


def test_a_rule_that_cannot_run_does_not_turn_an_unread_statement_into_proved_nothing():
    rows = [{"rule_id": "cf1", "status": "skipped",
             "details": {"statement": "cash_flow", "reason": "statement_not_read"}},
            {"rule_id": "cf2", "status": "skipped",
             "details": {"statement": "cash_flow", "reason": "section_signs_not_declared_one_way"}}]
    report = coverage(rows)
    cf = report.statements["cash_flow"]
    assert cf.status == cov_mod.NOT_READ
    assert cf.skips == {"NOT_READ": 1, "SIGNS_NOT_ESTABLISHED": 1}
    assert [a["code"] for a in report.alarms if a.get("statement") == "cash_flow"] == [
        cov_mod.ALARM_NOT_READ]


def test_the_filing_is_not_read_only_when_nothing_in_it_was():
    rows = [{"rule_id": "cf1", "status": "skipped",
             "details": {"statement": "cash_flow", "reason": "statement_not_read"}},
            {"rule_id": "bs1", "status": "skipped",
             "details": {"statement": "balance_sheet", "reason": "target_not_extracted"}}]
    report = coverage(rows)
    assert report.statements["cash_flow"].status == cov_mod.NOT_READ
    assert report.statements["balance_sheet"].status == cov_mod.UNVALIDATED
    # The balance sheet was read and proved nothing: the filing is unvalidated, not unread.
    assert report.aggregate.status == cov_mod.UNVALIDATED


# --- the export caption ------------------------------------------------------------------------

_UNREAD = [{"page": 41, "folio": "39", "reason": "scanned_no_ocr", "sections": ["cash_flow"],
            "ocr": "off"},
           {"page": 42, "folio": None, "reason": "scanned_no_ocr", "sections": ["cash_flow"],
            "ocr": "off"},
           {"page": 7, "folio": "5", "reason": "out_of_scope", "sections": ["profit_and_loss"]}]


def test_the_validation_caption_names_the_pages_not_read():
    passed = {"available": True, "aggregate": {"status": "PASSED", "passed": 3, "failed": 0,
                                               "evaluated": 3, "declarable": 3},
              "alarms": [], "unread_pages": _UNREAD}
    text, severe = validation_caption(passed, "en")
    assert text == ("3 pages were not read: p.39, p.42 scanned and OCR is off; "
                    "p.5 left out of the page selection.")
    assert severe is False
    assert validation_caption({**passed, "unread_pages": []}, "en") is None

    partial = {**passed, "aggregate": {**passed["aggregate"], "status": "PARTIAL",
                                       "declarable": 9}}
    assert validation_caption(partial, "en")[0].endswith(text)

    unavailable = {"available": False, "reason": "no_relations", "reason_label": "Nothing.",
                   "unread_pages": _UNREAD[2:]}
    assert validation_caption(unavailable, "en")[0] == (
        "Nothing. 1 page was not read: p.5 left out of the page selection.")


@pytest.mark.parametrize("locale", ["zh", "ar", "fr"])
def test_the_caption_is_in_the_readers_language(locale):
    en, there = unread_caption(_UNREAD, "en"), unread_caption(_UNREAD, locale)
    assert there and there != en
    for english in ("pages were not read", "scanned and OCR is off",
                    "left out of the page selection"):
        assert english not in there, (locale, english)


def test_a_long_list_names_the_first_pages_and_counts_the_rest():
    many = [{"page": n, "folio": None, "reason": "out_of_scope", "sections": ["notes"]}
            for n in range(1, 21)]
    assert unread_caption(many, "en") == (
        "20 pages were not read: p.1, p.2, p.3, p.4, p.5, p.6, p.7, p.8 and 12 more left out of "
        "the page selection.")


# --- end to end ----------------------------------------------------------------------------------

def test_a_page_left_out_of_the_selection_reaches_coverage_the_workspace_and_the_export(client):
    pytest.importorskip("fitz")
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_rich_pdf

    doc_id = client.post("/api/v1/documents",
                         files={"file": ("rich.pdf", make_rich_pdf(),
                                         "application/pdf")}).json()["id"]
    pages = client.get(f"/api/v1/documents/{doc_id}/pages").json()["pages"]
    pl = [p["no"] - 1 for p in pages if p.get("statement") == "profit_and_loss"]
    assert pl, [(p["no"], p.get("kind"), p.get("statement")) for p in pages]
    keep = [p["no"] - 1 for p in pages if p["no"] - 1 not in pl]
    assert client.put(f"/api/v1/documents/{doc_id}/scope",
                      json={"included_pages": keep}).status_code == 200
    client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    for _ in range(200):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    run = client.get(f"/api/v1/documents/{doc_id}/run").json()
    assert run["status"] == "succeeded"

    unread = run["result"]["unread_pages"]
    assert [(e["page"], e["reason"], e["sections"]) for e in unread] == [
        (i + 1, "out_of_scope", ["profit_and_loss"]) for i in pl]

    spread = client.get(f"/api/v1/documents/{doc_id}/statement",
                        params={"statement": "balance_sheet"}).json()
    assert spread["unread_pages"] == unread

    # Every relation on the income statement is "not read" — neither "not in this filing" nor
    # "the filing prints no subtotal", both of which are claims about pages nobody read.
    pl_reasons = {r["details"].get("reason") for r in run["result"]["structural"]
                  if r["details"].get("statement") == "profit_and_loss"}
    assert pl_reasons == {"statement_not_read"}, pl_reasons

    cov = client.get(f"/api/v1/documents/{doc_id}/review").json()["coverage"]
    assert cov["unread_pages"] == unread
    statuses = {s["statement"]: s["status"] for s in cov["statements"]}
    assert statuses["profit_and_loss"] == "NOT_READ", statuses
    assert any(a["code"] == "STATEMENT_NOT_READ" and a["statement"] == "profit_and_loss"
               for a in cov["alarms"])
    not_read = next(s for s in cov["skips"] if s["bucket"] == "NOT_READ")
    assert not_read["counts_in_denominator"] is True

    exported = client.get(f"/api/v1/documents/{doc_id}/export",
                          params={"fmt": "json"}).json()
    assert f"p.{pl[0] + 1} left out of the page selection" in \
        exported["validation"]["caption"]["text"]


# --- the screen strings ------------------------------------------------------------------------

_FRONTEND = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
_SCREEN_KEYS = {
    _FRONTEND / "i18n.ts": ("ws.unread.headOne", "ws.unread.head", "ws.unread.outOfScope",
                            "ws.unread.sectionOff", "ws.unread.ocrOff", "ws.unread.noOcr",
                            "ws.unread.more", "ws.unread.open"),
    _FRONTEND / "i18n" / "screens" / "review.ts": ("r.cov.notRead",),
}


@pytest.mark.parametrize("path", sorted(_SCREEN_KEYS, key=str), ids=lambda p: p.name)
def test_the_banner_and_the_coverage_row_are_in_every_locale_with_their_figures(path):
    import re

    from tests.test_explained_by_ui_strings import LOCALES, _blocks

    if not path.exists():                       # pragma: no cover - a backend-only checkout
        pytest.skip(f"{path} is not present")
    blocks = _blocks(path.read_text(encoding="utf-8"))

    def text(locale, key):
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', blocks.get(locale, ""))
        return m.group(1) if m else ""

    for key in _SCREEN_KEYS[path]:
        english = text("en", key)
        assert english, key
        for locale in LOCALES:
            there = text(locale, key)
            assert there.strip(), (locale, key)
            assert set(re.findall(r"\{[a-z0-9_]+\}", there)) == \
                set(re.findall(r"\{[a-z0-9_]+\}", english)), (locale, key)
            if locale != "en":
                assert there != english, (locale, key)
