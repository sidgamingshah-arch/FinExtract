"""A DISCUSSION OF A STATEMENT IS NOT A STATEMENT, however exactly it names one.

NEW FILE -> backend/tests/test_a_report_section_is_not_a_statement.py

An integrated annual report is built out of named sections — Corporate Overview, Management
Discussion and Analysis, Statutory Reports, the BRSR, Financial Statements — and prints the
section's name at the top of every page of it. Asian Paints' 2025-26 report is 293 pages of which
about 110 are financial.

TWO PAGES OF ITS FRONT MATTER LATCHED A FACE. Page 40 is the Management Discussion and Analysis'
ten-year review, whose table carries the mid-page heading "INCOME STATEMENT" — a perfect match for
the profit-and-loss pattern, covering the whole of its own line. Page 42 is value-creation
narrative whose line "Strong balance sheet supporting" gave "balance sheet" 13 of 30 characters,
0.43, comfortably over the 0.35 `_TITLE_COVERAGE` floor. Both resolved a statement and arrived with
a strong title, worth +6 to the face state.

THE COST WAS NOT TWO PAGES. `(_FACE, _PRE)` costs 8.0 — front matter may become a face but a face
may not become front matter again — while `(_FACE, _NOTES)` and `(_NOTES, _NOTES)` are free. So
once page 40 latched, the 140 pages of ESG, value-creation, governance and statutory narrative
after it could only be NOTES. Measured before this fix:

    decode verdicts   pre 49   face 24   notes 220        first face page 40
    after             pre 187  face 8    notes 98         first face page 183

and 183 is the real Balance Sheet. The notes region now opens at 186, "Notes to the Standalone
Financial Statements". End to end on that filing the filled template rows went from 49 of 51 coming
from a real financial caption to 50 of 50 — the two survivors were "Read more at Page" on
`bs_nca__other_non_current_assets` and a patents-filed count on `bs_nca__patents`.

BOTH GUARDS ARE NEEDED AND THEY CATCH DIFFERENT PAGES: the section header catches page 40, whose
mid-page heading is a perfect title; the stricter mid-page coverage catches page 42, whose running
header ("Value Proposition") is not a standard section name at all.
"""
from __future__ import annotations

import pytest

from app.stages.classify import (_MID_PAGE_TITLE_COVERAGE, _REPORT_SECTION, _TITLE_COVERAGE,
                                 _covers_title, _features, _mid_page_statement)

LINE_H = 12.0
PAGE_H = 792.0


def _lines(*texts: str, start: float = 36.0, step: float = 14.0) -> list[dict]:
    return [{"text": t, "y": start + i * step, "size": 10.0, "bold": False}
            for i, t in enumerate(texts)]


def _feat(*texts: str):
    lines = _lines(*texts)
    return _features(0, lines, PAGE_H, "\n".join(texts))


# --------------------------------------------------------------- the section header ---

@pytest.mark.parametrize("header", [
    "Management Discussion and Analysis",
    "MANAGEMENT DISCUSSION AND ANALYSIS",
    "Business Responsibility and Sustainability Report",
    "BRSR",
    "Report on Corporate Governance",
    "Corporate Governance Report",
    "Board's Report",
    "Boards Report",
    "Corporate Overview",
    "Statutory Reports",
    "Notice of the Annual General Meeting",
    "Value Proposition",
    "Value Creation Model",
])
def test_a_named_report_section_is_narrative(header):
    """Every section an integrated report labels its pages with, other than the statements."""
    assert _REPORT_SECTION.search(header), header


def test_financial_statements_is_not_a_report_section():
    """THE CONTROL THAT MATTERS MOST. "Financial Statements" is the running header the REAL
    statements carry — pages 183 to 235 of that filing print it — so matching it would refuse every
    statement in the report and the classifier would find no face at all."""
    assert not _REPORT_SECTION.search("Financial Statements")
    assert not _REPORT_SECTION.search("Notes to the Standalone Financial Statements")
    assert not _REPORT_SECTION.search("Consolidated Balance Sheet")


def test_the_md_and_a_ten_year_review_is_not_a_face(monkeypatch):
    """PAGE 40, as it is printed: the section header, the review's own heading, and the mid-page
    "INCOME STATEMENT" that a ten-year table puts over its first block."""
    f = _feat("Management Discussion and Analysis",
              "MANAGEMENT DISCUSSION AND ANALYSIS",
              "TEN-YEAR REVIEW",
              "Standalone",
              "INCOME STATEMENT",
              "Revenue from Operations 30,769.5 29,552.7")
    assert f.narrative is True
    assert f.statement is None, f.matched_title
    assert f.strong_title is False


def test_a_value_creation_page_is_not_a_balance_sheet():
    """PAGE 42. Caught by the section header AND, independently, by the coverage floor — the
    sentence fragment is refused even where the header is not recognised."""
    f = _feat("Value Proposition", "VALUE CREATION MODEL", "OUR PURPOSE",
              "Strong balance sheet supporting", "sustained growth")
    assert f.statement is None, f.matched_title


def test_a_statement_title_inside_a_narrative_section_never_resolves():
    """The section header wins over a perfect title match, which is the whole point: a management
    discussion names every statement it discusses."""
    for title in ("Balance Sheet", "Statement of Profit and Loss", "Statement of Cash Flows",
                  "Consolidated Balance Sheet"):
        f = _feat("Management Discussion and Analysis", title, "some commentary follows")
        assert f.statement is None, (title, f.matched_title)


# ------------------------------------------------------- the mid-page coverage floor ---

def test_the_mid_page_floor_is_stricter_than_the_title_band_floor():
    """They answer the same question about different amounts of text. The title band is a handful
    of lines where naming a statement is already strong evidence; the mid-page path considers every
    heading-shaped line on the page."""
    assert _MID_PAGE_TITLE_COVERAGE > _TITLE_COVERAGE


def test_a_sentence_fragment_fails_the_mid_page_floor():
    """"Strong balance sheet supporting" — 13 of 30 characters, 0.43."""
    assert _covers_title("balance sheet", "Strong balance sheet supporting")
    assert not _covers_title("balance sheet", "Strong balance sheet supporting",
                             _MID_PAGE_TITLE_COVERAGE)


def test_a_real_mid_page_title_still_resolves():
    """THE CONTROL WITHOUT WHICH THE FIX IS WORTHLESS. This function exists for the page that
    finishes one statement and opens the next below it, and that title is the statement's name and
    nothing else."""
    lines = _lines("Financial Statements", "Balance Sheet",
                   "TOTAL EQUITY AND LIABILITIES 30,084.26 26,675.48",
                   "Statement of Profit and Loss",
                   "Revenue from Sale of Products 30,621.70 29,270.69")
    # The page's own title band holds the first statement; the second one starts BELOW it, which is
    # the case this function is for. Leaving "Balance Sheet" out of the zone would make it a
    # mid-page candidate as well, and the topmost candidate would rightly win.
    zone = lines[:2]
    statement, _combined, title, _ambig = _mid_page_statement(lines, zone)
    assert statement == "profit_and_loss", (statement, title)
    assert title == "Statement of Profit and Loss", title


def test_the_title_band_floor_is_unchanged():
    """Nothing about the calibrated title-band path moves — `_resolve_statement` defaults to
    `_TITLE_COVERAGE`, so a bilingual one-line title at 0.52 still resolves."""
    f = _feat("CONSOLIDATED STATEMENT OF CASH FLOWS 綜合現金流量表",
              "Net cash from operating activities 1,234 5,678")
    assert f.statement == "cash_flow", f.matched_title


def test_an_ordinary_note_page_is_untouched():
    """Inert where no section header is printed, which is the whole shipped corpus."""
    f = _feat("Notes to the Consolidated Financial Statements",
              "14. Cash and cash equivalents",
              "Cash at bank 1,234 5,678")
    assert f.narrative is False
    assert f.statement is None
