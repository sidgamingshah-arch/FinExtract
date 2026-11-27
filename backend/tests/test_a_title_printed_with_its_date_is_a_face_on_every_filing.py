"""A STATEMENT TITLE PRINTED WITH ITS DATE ON THE SAME LINE IS THE PAGE'S TITLE, ON EVERY FILING.

NEW FILE -> backend/tests/test_a_title_printed_with_its_date_is_a_face_on_every_filing.py

Vivo Energy Mauritius' annual report 2025 (IFRS, single entity, Rs'000) heads its four statements

    STATEMENT OF PROFIT OR LOSS FOR THE YEAR ENDED 31 DECEMBER 2025         printed page 66
    STATEMENT OF COMPREHENSIVE INCOME FOR THE YEAR ENDED 31 DECEMBER 2025   page 66, below it
    STATEMENT OF FINANCIAL POSITION AS AT 31 DECEMBER 2025                  page 67
    STATEMENT OF CHANGES IN EQUITY FOR THE YEAR ENDED 31 DECEMBER 2025      page 68
    STATEMENT OF CASH FLOWS FOR THE YEAR ENDED 31 DECEMBER 2025             page 69

Each line carries two numbers, '31' and '2025', and a heading line with more than one number is not
a title candidate (`classify._looks_like_heading`). The cut that offers the title without its date
(`classify._dated_title_candidates`) ran on an Indian filing only, and this filing is not one — so
no page was a statement: 0 face pages, 77 rows all read from the notes, 49 of 49 checks skipped,
and the default whole-PDF route sent 54 sheets in one part, with no part of the statements alone.

The comprehensive-income title is the second title on its half, and `_closing_statement` had no
dated path either: the statement was read as the tail of the profit or loss, and its opening
restatement of "Profit for the year 529,539 / 516,510" as a second bottom line.

Each test here fails on the code before the change it pins, except the two that pin what the cut
must still refuse now that it reaches every filing.
"""
from __future__ import annotations

import pathlib

import pytest

from app.stages import classify

BACKEND = pathlib.Path(__file__).resolve().parent.parent
VIVO = BACKEND.parent / "testdata" / "filings" / "sem_vivo_energy_mauritius_2025.pdf"
H = 841.89          # the height of every printed page of the filing, in points


def _line(text: str, y: float, x0: float = 52.0) -> dict:
    return {"text": text, "y": float(y), "size": 9.0, "bold": False,
            "x0": float(x0), "x1": float(x0 + 5.5 * len(text))}


def _profit_or_loss_half() -> list[dict]:
    """Sheet 34, left half (printed page 66), with the figures it prints, a caption and its
    figures on one line: the two titles, the column band, rows of both statements, the folio and
    the running footer. (Some rows are left out; none is invented.)"""
    return [_line("STATEMENT OF PROFIT OR LOSS FOR THE YEAR ENDED 31 DECEMBER 2025", 107.4),
            _line("2025 2024", 147.9, 330.0),
            _line("Notes Rs'000 Rs'000", 158.0, 300.0),
            _line("Revenue from contracts with customers 7 17,840,538 18,997,007", 172.9),
            _line("Cost of sales (16,475,225) (17,633,837)", 189.0),
            _line("Gross profit 1,365,313 1,363,170", 205.0),
            _line("Other income 8 80,775 44,007", 222.9),
            _line("Operating profit 9 658,492 628,618", 307.5),
            _line("Finance income 10 7,065 11,701", 324.5),
            _line("Finance costs 10 (29,048) (18,485)", 341.5),
            _line("Profit before income tax 641,346 623,278", 392.5),
            _line("Income tax expense 11 ( 111,807) ( 106,768)", 409.5),
            _line("Profit for the year 529,539 516,510", 426.5),
            _line("STATEMENT OF COMPREHENSIVE INCOME FOR THE YEAR ENDED 31 DECEMBER 2025", 485.0),
            _line("2025 2024", 525.5, 330.0),
            _line("Profit for the year 529,539 516,510", 550.5),
            _line("Total comprehensive income for the year 513,773 506,999", 660.5),
            _line("66", 820.7, 30.0),
            _line("VIVO ENERGY MAURITIUS LIMITED | Annual Report 2025", 821.7, 60.0)]


@pytest.mark.parametrize("title, statement", [
    ("STATEMENT OF PROFIT OR LOSS FOR THE YEAR ENDED 31 DECEMBER 2025", "profit_and_loss"),
    ("STATEMENT OF FINANCIAL POSITION AS AT 31 DECEMBER 2025", "balance_sheet"),
    ("STATEMENT OF CHANGES IN EQUITY FOR THE YEAR ENDED 31 DECEMBER 2025", "changes_in_equity"),
    ("STATEMENT OF CASH FLOWS FOR THE YEAR ENDED 31 DECEMBER 2025", "cash_flow"),
])
def test_an_ifrs_title_with_its_date_makes_the_page_its_statement(title, statement):
    lines = [_line(title, 107.4), _line("Notes 2025 2024", 147.9, 300.0),
             _line("Property, plant and equipment 14 1,908,274 1,784,896", 205.5),
             _line("66", 820.7, 30.0)]
    f = classify._features(34, lines, H, "\n".join(line["text"] for line in lines))
    assert f.statement == statement
    assert f.matched_title == title.split(" FOR THE ")[0].split(" AS AT ")[0]
    assert f.matched_title_y == pytest.approx(107.4 / H)    # located by the line it was printed as


def test_the_second_dated_title_on_a_half_is_its_closing_statement():
    lines = _profit_or_loss_half()
    f = classify._features(34, lines, H, "\n".join(line["text"] for line in lines))
    assert f.statement == "profit_and_loss"
    assert f.closing_statement == "comprehensive_income"
    assert f.closing_title == "STATEMENT OF COMPREHENSIVE INCOME"
    assert f.closing_title_y == pytest.approx(485.0 / H)


def test_a_dated_title_with_no_figures_above_it_is_not_a_closing_statement():
    """The floor `_closing_statement` keeps: a title with nothing printed between it and the
    page's own title is that title printed twice, not a second statement."""
    lines = [_line("STATEMENT OF PROFIT OR LOSS FOR THE YEAR ENDED 31 DECEMBER 2025", 107.4),
             _line("STATEMENT OF PROFIT OR LOSS FOR THE YEAR ENDED 31 DECEMBER 2025", 130.0),
             _line("Revenue 7 17,840,538 18,997,007", 172.9)]
    f = classify._features(34, lines, H, "\n".join(line["text"] for line in lines))
    assert f.statement == "profit_and_loss" and f.closing_statement is None


def test_a_sentence_that_ends_in_a_date_is_still_not_a_title():
    """The cut is offered on every filing, so what it refuses matters on every filing: a sentence
    of the auditor's report, a contents line with its page number."""
    for text in ("We have audited the statement of financial position as at 31 December 2025, and",
                 "Statement of financial position as at 31 December 2025 ........ 67"):
        assert classify._dated_title_candidates([_line(text, 300.0)]) == []


@pytest.fixture(scope="module")
def vivo():
    if not VIVO.exists():
        pytest.skip(f"{VIVO.name} not present")
    from app.core.models.document import DocumentModel
    from app.core.stage import PipelineContext
    from app.stages.classify import ClassifyStage
    from app.stages.ingest import IngestStage
    from app.stages.integrity import IntegrityStage
    from app.stages.language import LanguageDetectStage

    raw = VIVO.read_bytes()
    doc, ctx = DocumentModel(filename=VIVO.name), PipelineContext(raw_bytes=raw)
    logs: list[str] = []
    ctx.log = logs.append
    for stage in (IngestStage(), IntegrityStage(), LanguageDetectStage(), ClassifyStage()):
        doc = stage.run(doc, ctx)
    return doc, ctx, logs


def test_the_filing_is_not_indian_and_its_four_statements_are_found(vivo):
    from app.services.page_spread import statements_on

    doc, ctx, logs = vivo
    assert ctx.indian_filing is False
    faces = {p.index: statements_on(p) for p in doc.face_pages()}
    assert faces == {34: ["profit_and_loss", "balance_sheet"],
                     35: ["changes_in_equity", "cash_flow"]}
    assert any(line.startswith("classify:face=2 ") and "'balance_sheet', 'cash_flow', "
               "'changes_in_equity', 'profit_and_loss'" in line for line in logs), logs[-3:]


def test_the_comprehensive_income_title_closes_the_profit_or_loss_half(vivo):
    from app.services.page_spread import spread_halves

    doc, _ctx, logs = vivo
    left = spread_halves(doc.pages[34])[0]
    assert left["statement"] == "profit_and_loss"
    assert left["evidence"]["closing_title"] == "STATEMENT OF COMPREHENSIVE INCOME"
    assert left["evidence"]["closing_title_y"] == pytest.approx(485.0 / H, abs=1e-3)
    assert any("page=34L:closing_title='STATEMENT OF COMPREHENSIVE INCOME'" in line
               for line in logs)


def test_each_statement_half_keeps_its_own_folio(vivo):
    """Sheet 34 prints pages 66 and 67, sheet 35 pages 68 and 69: a figure on the balance sheet
    cites 67 and one on the cash flow 69, not the left page's number (`page_spread.folio_at`)."""
    from app.services.page_spread import folio_at, spread_halves

    doc, _ctx, _logs = vivo
    for sheet, folios in ((34, ["66", "67"]), (35, ["68", "69"])):
        page = doc.pages[sheet]
        assert [h["printed_page"] for h in spread_halves(page)] == folios
        assert page.printed_page == folios[0]
        assert [folio_at(page, 0.25), folio_at(page, 0.75)] == folios
