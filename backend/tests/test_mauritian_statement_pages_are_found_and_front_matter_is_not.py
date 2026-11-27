"""THE MAURITIAN STATEMENT PAGES ARE FOUND, AND THE FRONT MATTER AROUND THEM IS NOT A STATEMENT.

Five classification defects the Mauritian filings showed, each pinned on the page text it was
measured on:

  * A TITLE SPLIT OVER LINES UNDER THE ISSUER'S LINE. Lux Island Resorts 2025 heads every
    statement "LUX ISLAND RESORTS LTD AND ITS SUBSIDIARIES" / "CONSOLIDATED AND SEPARATE
    STATEMENTS" / "OF FINANCIAL POSITION". Joined only from the first line of the run, the join
    reached its 90-character cap before the statement's name, so the balance sheet (p122), the
    profit or loss (p123), the comprehensive income (p124) and the cash flow (p127) were no faces.
    `classify._title_candidates(every_start=True)`, asked only where the ordinary candidates name
    nothing.
  * A SUMMARY PAGE IS NOT A STATEMENT. Lux sheet 17 ("FINANCIAL HIGHLIGHTS & RATIOS", "BALANCE
    SHEET STATUS AT A GLANCE") and Phoenix Beverages sheet 29 ("GROUP FINANCIAL SUMMARY") print
    statement names over five years of figures; they were read as faces and fed the grid
    (`classify._SUMMARY_PAGE`, on the title zone only).
  * A STATEMENT'S FOOTER THAT REFERS TO THE AUDITOR'S REPORT IS NOT THE REPORT ("Independent
    Auditor’s report on pages 116 to 121.", Lux p124), and a typeset apostrophe still names the
    directors' report ("DIRECTORS’ REPORT", Gamma Civic p75) — `classify._NARRATIVE`.
  * A FOLIO SET INSIDE THE FOOTER ("68 | Mauritius Oil Refineries Ltd and its Subsidiaries |
    Annual Report 2025") is the page number: Moroil had none on any of its 133 pages.
  * FIGURES GROUPED WITH SPACES ARE TABLE ROWS: Phoenix's pension note (printed 190) "Liability in
    the statements of financial position 207 446" was read as a balance-sheet title part-way down
    the page (`classify._AMOUNT_LINE`).

And IFRS's "separate" statements are the parent's own: Lux's "SEPARATE STATEMENT OF CHANGES IN
EQUITY" (p126) is the company's (`classify._SCOPE_COMPANY`).
"""
from __future__ import annotations

import pathlib

import pytest

from app.stages import classify

BACKEND = pathlib.Path(__file__).resolve().parent.parent
FILINGS = BACKEND.parent / "testdata" / "filings"
H = 779.53                      # Lux Island Resorts' printed-page height, in points


def _line(text: str, y: float, x0: float = 56.7, size: float = 8.5, bold: bool = False) -> dict:
    return {"text": text, "y": float(y), "size": size, "bold": bold,
            "x0": float(x0), "x1": float(x0 + 4.6 * len(text))}


def _features(lines: list[dict], index: int = 62, page_h: float = H):
    return classify._features(index, lines, page_h, "\n".join(line["text"] for line in lines))


def _lux_head(*title: str, period: str) -> list[dict]:
    """The head every Lux statement prints: the issuer and the report as running heads, the title
    over two lines in 18pt, the period, then the entity band."""
    out = [_line("LUX ISLAND RESORTS LTD AND ITS SUBSIDIARIES", 36.7, 36.7, 7.0),
           _line("INTEGRATED ANNUAL REPORT 2025", 36.7, 296.6, 7.0)]
    out += [_line(t, 72.0 + 20.0 * k, 56.7, 18.0, True) for k, t in enumerate(title)]
    out += [_line(period, 120.0, 56.7, 10.0, True),
            _line("THE GROUP", 143.1, 348.4, 9.0, True), _line("THE COMPANY", 143.1, 444.1, 9.0, True),
            _line("Notes", 143.2, 284.9), _line("Rs’000", 165.8, 342.3, 8.5, True)]
    return out


_LUX_FOOT = [_line("The notes set out on pages 128 to 214 form an integral part of these financial "
                   "statements.", 696.2),
             _line("Independent Auditor’s report on pages 116 to 121.", 707.2),
             _line("122", 732.3, 36.7, 7.0)]


@pytest.mark.parametrize("second, period, statement", [
    ("OF FINANCIAL POSITION", "AS AT 30 JUNE 2025", "balance_sheet"),
    ("OF PROFIT OR LOSS", "FOR THE YEAR ENDED 30 JUNE 2025", "profit_and_loss"),
    ("OF COMPREHENSIVE INCOME", "FOR THE YEAR ENDED 30 JUNE 2025", "comprehensive_income"),
    ("OF CASH FLOWS", "FOR THE YEAR ENDED 30 JUNE 2025", "cash_flow"),
])
def test_a_title_split_under_the_issuers_line_names_its_statement(second, period, statement):
    lines = (_lux_head("CONSOLIDATED AND SEPARATE STATEMENTS", second, period=period)
             + [_line("Property, plant and equipment", 190.0, 59.0),
                _line("13,617,744", 190.0, 342.3), _line("12,946,383", 190.0, 393.0)]
             + _LUX_FOOT)
    f = _features(lines)
    assert f.statement == statement
    assert f.matched_title == f"CONSOLIDATED AND SEPARATE STATEMENTS {second}"
    assert not f.narrative                       # the footer only refers to the auditor's report


def test_the_join_from_a_later_line_is_offered_only_when_asked():
    zone = classify._title_zone(_lux_head("CONSOLIDATED AND SEPARATE STATEMENTS",
                                          "OF FINANCIAL POSITION", period="AS AT 30 JUNE 2025"))
    want = "CONSOLIDATED AND SEPARATE STATEMENTS OF FINANCIAL POSITION"
    assert want not in [c["text"] for c in classify._title_candidates(zone)]
    assert want in [c["text"] for c in classify._title_candidates(zone, every_start=True)]
    # the ordinary candidates name nothing, which is the only case `_features` asks again
    assert classify._resolve_statement(classify._title_candidates(zone))[0] is None


def test_a_title_the_ordinary_join_reads_keeps_its_title():
    """Moroil 2025 (printed 68): a two-line title with nothing above it is read as before."""
    lines = [_line("STATEMENTS OF PROFIT OR LOSS AND OTHER", 60.0, 56.7, 14.0, True),
             _line("COMPREHENSIVE INCOME", 78.0, 56.7, 14.0, True),
             _line("YEAR ENDED JUNE 30, 2025", 96.0, 56.7, 9.0, True),
             _line("THE GROUP", 120.0, 330.0, 9.0, True), _line("THE COMPANY", 120.0, 450.0, 9.0, True),
             _line("Revenue 5 2,431,170 2,249,018", 150.0)]
    f = _features(lines, index=66, page_h=841.89)
    assert f.statement == "profit_and_loss"
    assert f.matched_title.startswith("STATEMENTS OF PROFIT OR LOSS AND OTHER")


@pytest.mark.parametrize("head, body", [
    # Lux sheet 17, left half (printed 32): five years of highlights
    (["FINANCIAL HIGHLIGHTS", "& RATIOS"],
     [_line("YEAR ENDED 30 JUNE", 147.6, 340.7, 9.0, True),
      _line("STATEMENT OF PROFIT OR LOSS", 195.3, 59.0, 9.0, True),
      _line("Total income 10,661,854 9,762,976 8,265,011 6,810,220 2,334,695", 209.6, 59.0)]),
    # Lux sheet 17, right half (printed 33): an infographic of the balance sheet and cash flow
    (["BALANCE SHEET", "STATUS AT A GLANCE"],
     [_line("AS AT 30 JUNE 2025", 120.0, 56.7, 10.0, True),
      _line("CONSOLIDATED STATEMENT OF CASH FLOW", 300.0, 56.7, 10.0, True),
      _line("Rs’000 2,380,076", 330.0, 56.7)]),
])
def test_a_summary_heading_makes_the_page_no_statement(head, body):
    lines = ([_line("LUX ISLAND RESORTS LTD AND ITS SUBSIDIARIES", 36.7, 36.7, 7.0)]
             + [_line(t, 72.0 + 20.0 * k, 56.7, 18.0, True) for k, t in enumerate(head)] + body)
    f = _features(lines, index=17)
    assert f.narrative
    assert f.statement is None


def test_a_group_financial_summary_is_no_statement():
    """Phoenix Beverages 2025 sheet 29, right half (printed 57)."""
    lines = [_line("GOVERNANCE AND", 30.0), _line("OUR FINANCIALS", 42.0),
             _line("GROUP FINANCIAL SUMMARY", 80.0, 56.7, 16.0, True),
             _line("Statements of profit or loss & other comprehensive income (MUR M)", 110.0),
             _line("Revenue 13 400 12 171 11 040 9 518 8 066", 130.0)]
    f = _features(lines, index=29, page_h=841.89)
    assert f.narrative and f.statement is None


def test_a_statement_that_mentions_a_summary_below_its_title_is_still_a_statement():
    lines = (_lux_head("CONSOLIDATED AND SEPARATE STATEMENTS", "OF FINANCIAL POSITION",
                       period="AS AT 30 JUNE 2025")
             + [_line("Summary of material accounting policies 2", 400.0, 59.0)] + _LUX_FOOT)
    assert _features(lines).statement == "balance_sheet"


@pytest.mark.parametrize("text", [
    "INDEPENDENT AUDITOR’S REPORT",
    "INDEPENDENT AUDITOR'S REPORT TO THE SHAREHOLDERS OF LUX ISLAND RESORTS LTD",
    "DIRECTORS’ REPORT",                         # Gamma Civic 2024, p75: a typeset apostrophe
    "DIRECTORS' REPORT",
])
def test_the_report_pages_themselves_are_still_prose(text):
    assert classify._NARRATIVE.search(text)


@pytest.mark.parametrize("text", [
    "Independent Auditor’s report on pages 116 to 121.",
    "Independent auditor's report is set out on pages 63 to 65.",
])
def test_a_footer_that_refers_to_the_auditors_report_is_not_the_report(text):
    assert not classify._NARRATIVE.search(text)


@pytest.mark.parametrize("footer, folio", [
    ("68 | Mauritius Oil Refineries Ltd and its Subsidiaries | Annual Report 2025", "68"),
    ("Mauritius Oil Refineries Ltd and its Subsidiaries | Annual Report 2025 | 69", "69"),
])
def test_a_folio_set_inside_the_footer_is_the_page_number(footer, folio):
    lines = [_line("NOTES TO THE FINANCIAL STATEMENTS", 60.0), _line("Cash at bank 12,345", 300.0),
             _line(footer, 815.0, 56.7, 7.0)]
    assert classify._printed_folio(lines, 841.89) == folio


@pytest.mark.parametrize("footer", [
    "Mauritius Oil Refineries Ltd | Annual Report 2025",          # no number at either end
    "Note 12 | Borrowings 2025",                                  # a number, not a bare one
])
def test_a_footer_with_no_bare_number_at_its_ends_has_no_folio(footer):
    lines = [_line("Cash at bank 12,345", 300.0), _line(footer, 815.0, 56.7, 7.0)]
    assert classify._printed_folio(lines, 841.89) is None


@pytest.mark.parametrize("text, is_amount", [
    ("Liability in the statements of financial position 207 446 199 872", True),
    ("(757 040)", True),
    ("As at 30 June 2025", False),               # a date is not an amount
    ("Note 18 2025 2024", False),
])
def test_figures_grouped_with_spaces_are_amounts(text, is_amount):
    assert bool(classify._AMOUNT_LINE.search(text)) is is_amount


@pytest.mark.parametrize("title, scope", [
    ("LUX ISLAND RESORTS LTD AND ITS SUBSIDIARIES SEPARATE STATEMENT OF CHANGES IN EQUITY",
     "company"),
    ("CONSOLIDATED AND SEPARATE STATEMENTS OF FINANCIAL POSITION", "consolidated"),
    ("CONSOLIDATED STATEMENT OF CHANGES IN EQUITY", "consolidated"),
    ("STATEMENT OF CASH FLOWS", None),
])
def test_a_separate_statement_is_the_companys(title, scope):
    assert classify._title_scope(title) == scope


def _classified(stem: str):
    pdf = FILINGS / f"{stem}.pdf"
    if not pdf.exists():
        pytest.skip(f"{pdf.name} not present")
    from app.core.models.document import DocumentModel
    from app.core.stage import PipelineContext
    from app.stages.classify import ClassifyStage
    from app.stages.ingest import IngestStage
    from app.stages.integrity import IntegrityStage
    from app.stages.language import LanguageDetectStage

    doc, ctx = DocumentModel(filename=pdf.name), PipelineContext(raw_bytes=pdf.read_bytes())
    ctx.log = lambda _line: None
    for stage in (IngestStage(), IntegrityStage(), LanguageDetectStage(), ClassifyStage()):
        doc = stage.run(doc, ctx)
    return doc


def test_lux_island_resorts_statements_are_its_faces_and_its_highlights_are_not():
    from app.services.page_spread import spread_halves, statements_on

    doc = _classified("sem_lux_island_resorts_2025")
    faces = {p.index: statements_on(p) for p in doc.face_pages()}
    assert faces == {62: ["balance_sheet", "profit_and_loss"],
                     63: ["profit_and_loss", "changes_in_equity"],
                     64: ["changes_in_equity", "cash_flow"]}
    assert [h["scope"] for h in spread_halves(doc.pages[64])] == ["company", "mixed"]
    assert [h["printed_page"] for h in spread_halves(doc.pages[64])] == ["126", "127"]


def test_moroil_pages_carry_their_folios():
    doc = _classified("sem_moroil_2025")
    folios = {p.index: p.printed_page for p in doc.pages}
    assert sum(1 for f in folios.values() if f) >= 120
    assert [folios[i] for i in (66, 67, 68)] == ["68", "69", "70"]
