"""Two reading rules an Indian annual report needs, and the gate that keeps them to Indian filings.

* A statement title printed WITH ITS DATE on the same line ("Standalone Balance Sheet as at 31 March
  2025") is the page's title. It carries two numbers, so the heading test refused it and the page was
  never a face.
* A row OPENED by its enumerator ("(1) Current tax") keeps the numeral in its caption. Read as a
  figure, "(1)" was the amount -1 and the row was dropped with its tax charge.

Both are switched on only when `services.regime.is_indian_filing` says so, because the reference
corpus they would otherwise reach (HKEX and mainland filings) was not re-measured with them on.
"""
from __future__ import annotations

import pytest

from app.services.regime import is_indian_filing
from app.stages import classify


@pytest.mark.parametrize("text", [
    "(₹ in lakhs)", "(Rs. in Lakhs)", "Amount in Rs. crores", "(INR in crores)",
    "prepared in accordance with Indian Accounting Standards (Ind AS)",
    "as required by the Companies Act, 2013", "CIN: L29100MH2005PLC151234",
])
def test_indian_vocabulary_is_recognised(text):
    assert is_indian_filing(["Balance Sheet", text])


@pytest.mark.parametrize("text", [
    "HK$'000", "RMB'000", "(Expressed in Renminbi)", "人民币元", "US$ million",
    "Indian Rupee (INR) exposure is not significant",
])
def test_other_filings_are_not(text):
    assert not is_indian_filing(["CONSOLIDATED STATEMENT OF FINANCIAL POSITION", text])


@pytest.mark.parametrize("line, title", [
    ("Standalone Balance Sheet as at 31 March 2025", "Standalone Balance Sheet"),
    ("Standalone Statement of Profit and Loss for the year ended 31st March, 2025",
     "Standalone Statement of Profit and Loss"),
    ("BALANCE SHEET AS AT MARCH 31, 2025", "BALANCE SHEET"),
    ("Consolidated Balance Sheet as at 31.03.2025", "Consolidated Balance Sheet"),
    ("Statement of Profit and Loss for the period ended 30 September 2024",
     "Statement of Profit and Loss"),
])
def test_a_title_with_its_date_is_offered_as_the_title(line, title):
    assert classify._without_date_tail(line) == title


@pytest.mark.parametrize("line", [
    "Revenue from operations 12,000 10,500",
    "We have audited the standalone balance sheet as at 31 March 2025, and the statement",
    "Standalone Balance Sheet as at 31 March 2025 ......... 145",
])
def test_a_row_or_a_sentence_is_not(line):
    assert classify._dated_title_candidates([{"text": line, "y": 10.0}]) == []


def _page(title: str) -> list[dict]:
    return [{"text": "SAMPLE INDUSTRIES LIMITED", "y": 40.0, "size": 11.0, "bold": True},
            {"text": title, "y": 56.0, "size": 10.5, "bold": True},
            {"text": "(₹ in lakhs)", "y": 70.0, "size": 8.0, "bold": False},
            {"text": "Particulars Note No. As at 31 March 2025 As at 31 March 2024", "y": 84.0,
             "size": 8.3, "bold": True},
            {"text": "(a) Property, plant and equipment 3 4,200 3,900", "y": 98.0, "size": 8.3,
             "bold": False}]


def test_the_dated_title_makes_the_page_a_balance_sheet_only_on_an_indian_filing():
    lines = _page("Standalone Balance Sheet as at 31 March 2025")
    text = "\n".join(line["text"] for line in lines)
    on = classify._features(1, lines, 842.0, text, dated_titles=True)
    off = classify._features(1, lines, 842.0, text, dated_titles=False)
    assert on.statement == "balance_sheet"
    assert on.matched_title == "Standalone Balance Sheet"
    assert on.matched_title_y == pytest.approx(56.0 / 842.0)   # located by the line it was printed as
    assert off.statement is None                                # the gate: unchanged elsewhere


def _row(*tokens: str):
    from app.core.models.geometry import BBox
    from app.services.row_reconstruct import Word

    return [Word(text=t, bbox=BBox(x0=0.05 + 0.1 * i, y0=0.5, x1=0.1 + 0.1 * i, y1=0.51))
            for i, t in enumerate(tokens)]


def test_an_enumerated_row_keeps_its_numeral_only_when_switched_on():
    from app.services.row_reconstruct import _scan_row, enumerated_rows

    row = _row("(1)", "Current", "tax", "330", "240")
    with enumerated_rows(True):
        labels, _note, values = _scan_row(row)
    assert [w.text for w in labels] == ["(1)", "Current", "tax"]
    assert [w.text for w in values] == ["330", "240"]
    # Off — the reading every non-Indian filing keeps: the numeral is a figure.
    labels, _note, values = _scan_row(row)
    assert [w.text for w in values][0] == "(1)"
    # A figure-only row is untouched either way: "(1)" followed by a figure is a figure.
    with enumerated_rows(True):
        _labels, _note, values = _scan_row(_row("(1)", "330", "240"))
    assert [w.text for w in values] == ["(1)", "330", "240"]
