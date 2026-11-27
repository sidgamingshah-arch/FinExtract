"""A BOTTOM LINE RESTATED BY THE NEXT STATEMENT ON THE SAME PAGE IS ONE FACT.

NEW FILE -> backend/tests/test_a_bottom_line_restated_on_the_same_page_is_one_fact.py

The duplicate-fact rule (`periods.summable`, and `structural_checks.collect_values` for the checks)
counts two rows once when they name the same concept, report the same amount and were printed in
DIFFERENT places — a statement that prints one caption twice on one page printed it twice on
purpose. "Place" was the page, and that is not enough where a page holds two statements.

Vivo Energy Mauritius prints its statement of comprehensive income under the statement of profit
or loss on printed page 66, and it opens by restating the bottom line:

    STATEMENT OF PROFIT OR LOSS FOR THE YEAR ENDED 31 DECEMBER 2025
        ...
        Profit for the year                           529,539     516,510       y = 426.5
    STATEMENT OF COMPREHENSIVE INCOME FOR THE YEAR ENDED 31 DECEMBER 2025     y = 485.0
        Profit for the year                           529,539     516,510       y = 550.5

Both rows map to is_pl__profit_loss_bef_extraord_items, both are on sheet 34, so they added: the
grid held 1,059,078 / 1,033,020, and the correct 529,539 was flagged "recon". Moroil prints the same
restatement on the NEXT page and it was already one fact. The row below the second title is now
stamped (`pdf_extract._stamp_rows_below_a_closing_title`), and the stamp is part of where it was
printed (`periods.printed_location`).

Each test here fails on the code before the change it pins, except the two that pin what must not
move: two rows printed in one statement still add.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind, PrintedIn
from app.core.models.geometry import BBox, Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.services.periods import BELOW_CLOSING_TITLE_FLAG, concept_value, printed_location

H = 841.89
UNDER_OCI = f"{BELOW_CLOSING_TITLE_FLAG}:STATEMENT OF COMPREHENSIVE INCOME"


def _dict_row(value: str, page: int, *flags: str) -> dict:
    return {"source_label": "Profit for the year", "canonical_key": "k", "flags": list(flags),
            "values": [{"basis": "consolidated", "period_label": "current", "value": value,
                        "provenance": {"page_index": page}}]}


def test_the_restatement_under_a_second_title_on_the_same_page_is_not_added():
    group = [_dict_row("529539", 34), _dict_row("529539", 34, UNDER_OCI)]
    assert concept_value(group, "consolidated", "current") == 529539.0


def test_one_statement_printing_the_same_caption_twice_still_adds():
    group = [_dict_row("100", 12), _dict_row("100", 12)]
    assert concept_value(group, "consolidated", "current") == 200.0
    # …and so do two rows both printed below the same second title
    group = [_dict_row("100", 12, UNDER_OCI), _dict_row("100", 12, UNDER_OCI)]
    assert concept_value(group, "consolidated", "current") == 200.0


def test_the_location_is_the_page_where_no_second_title_is_printed():
    assert printed_location(34) == 34
    assert printed_location(34, ["printed_on:profit_and_loss"]) == 34
    assert printed_location(34, [UNDER_OCI]) == (34, UNDER_OCI)
    assert printed_location(None, [UNDER_OCI]) is None


def _face_row(label: str, value: str, y: float, x0: float = 0.06) -> LineItem:
    row = LineItem(source_label=label, canonical_key="is_pl__profit_loss_bef_extraord_items",
                   printed_in=PrintedIn.FACE)
    box = BBox(x0=x0, y0=y / H, x1=x0 + 0.1, y1=(y + 9.0) / H)
    for period, amount in (("current", value), ("prior", "516510")):
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label=period,
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=34, bbox=box,
                                                           label_bbox=box)))
    return row


def _vivo_sheet_34() -> PageSource:
    left_evidence = {"state": "face", "matched_title": "STATEMENT OF PROFIT OR LOSS",
                     "matched_title_y": 107.4 / H,
                     "closing_title": "STATEMENT OF COMPREHENSIVE INCOME",
                     "closing_title_y": 485.0 / H}
    return PageSource(index=34, kind=PageKind.FACE, statement="profit_and_loss",
                      printed_page="66", evidence={**left_evidence, "spread": {
                          "fold": 0.4975, "halves": [
                              {"side": "left", "kind": "face", "statement": "profit_and_loss",
                               "printed_page": "66", "evidence": left_evidence},
                              {"side": "right", "kind": "face", "statement": "balance_sheet",
                               "printed_page": "67",
                               "evidence": {"matched_title": "STATEMENT OF FINANCIAL POSITION"}}]}})


def test_the_reader_stamps_the_rows_below_the_second_title_of_their_half():
    from app.services.pdf_extract import _stamp_rows_below_a_closing_title

    doc = DocumentModel(filename="vivo.pdf")
    doc.pages = [_vivo_sheet_34()]
    above = _face_row("Profit for the year", "529539", 426.5)
    below = _face_row("Profit for the year", "529539", 550.5)
    beside = _face_row("Total equity", "1207806", 550.5, x0=0.56)     # right half: no such title
    doc.line_items = [above, below, beside]
    logs: list[str] = []
    assert _stamp_rows_below_a_closing_title(doc, logs.append) == 1
    assert UNDER_OCI in below.confidence.flags
    assert not any(f.startswith(BELOW_CLOSING_TITLE_FLAG) for f in above.confidence.flags)
    assert not any(f.startswith(BELOW_CLOSING_TITLE_FLAG) for f in beside.confidence.flags)
    assert _stamp_rows_below_a_closing_title(doc, logs.append) == 0      # once only
    assert logs == ["extract:rows_below_closing_title=1"]


def test_the_checks_count_the_restated_bottom_line_once():
    from app.services.pdf_extract import _stamp_rows_below_a_closing_title
    from app.services.structural_checks import collect_values

    doc = DocumentModel(filename="vivo.pdf")
    doc.pages = [_vivo_sheet_34()]
    doc.line_items = [_face_row("Profit for the year", "529539", 426.5),
                      _face_row("Profit for the year", "529539", 550.5)]
    _stamp_rows_below_a_closing_title(doc, lambda _line: None)
    values = collect_values(doc.line_items).values["is_pl__profit_loss_bef_extraord_items"]
    assert values[("consolidated", "current")] == Decimal("529539")
    assert values[("consolidated", "prior")] == Decimal("516510")
