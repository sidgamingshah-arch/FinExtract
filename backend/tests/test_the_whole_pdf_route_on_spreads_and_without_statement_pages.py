"""THE WHOLE-PDF ROUTE ON A FILING OF TWO-PAGE SPREADS, AND ON ONE WHOSE STATEMENTS WERE NOT FOUND.

NEW FILE -> backend/tests/test_the_whole_pdf_route_on_spreads_and_without_statement_pages.py

Measured on Vivo Energy Mauritius 2025 (57 of its 58 sheets two-page spreads) with the scripted
stand-in, before its dated titles were read:

  * NO STATEMENT PAGE: the lost-lines guard skipped every statement (`if not holders: continue`),
    so a failed balance-sheet request blanked all 208 balance-sheet lines and the run reported
    success; the auditor's report was searched for only before a run of statement pages, so it
    was not sent; and nothing said so. A statement with no face page is now judged by its own
    requests, every page is sent (the auditor's report with them, `W.pages_to_send`, the rule
    since ed4cbe3), and the plan and the fill say so.
  * SPREADS: a part's page prints two folios. The question now names them; a printed folio the
    model cites is mapped back through them; a figure found on no page of the part carries no
    page rather than page 0 (the cover — 73 of 112 figures were cited there).
  * THE FOLD: a right-page figure took its caption from the left page of the same row
    (property, plant and equipment 1,908,274 on p67 got "Gross profit" from p66).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import fitz

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import PageKind
from app.core.models.geometry import BBox
from app.services import whole_pdf as W
from app.stages import whole_pdf as stage


def _item(key, statement):
    return SimpleNamespace(key=key, namespace="template", in_output=True, statement=statement)


def _plan(parts, chunks):
    lines = [it for c in chunks for it in c]
    return SimpleNamespace(parts=parts, chunks=chunks, lines=lines)


def test_a_statement_with_no_face_page_is_lost_when_its_own_request_failed():
    bs = W.Chunk([_item("bs_ca__inventories", "balance_sheet"),
                  _item("bs_ca__total_assets", "balance_sheet")], "balance_sheet")
    pl = W.Chunk([_item("is_pl__sales_revenues", "profit_and_loss")], "profit_and_loss")
    parts = [W.Part(pages=list(range(0, 30)), pdf=b"")]
    doc = DocumentModel(pages=[PageSource(index=i, kind=PageKind.NOTES) for i in range(30)])
    asked = stage._Asked(failed=[{"part": 1, "statement": "balance_sheet"}],
                         answered={0: {"is_pl__sales_revenues"}})
    why, lost = stage._lost_lines(doc, _plan(parts, [bs, pl]), asked)
    assert why is not None and "balance sheet" in why
    assert set(lost) == {"bs_ca__inventories", "bs_ca__total_assets"}


def test_a_statement_with_no_face_page_whose_request_succeeded_is_not_lost():
    bs = W.Chunk([_item("bs_ca__inventories", "balance_sheet")], "balance_sheet")
    parts = [W.Part(pages=[0, 1], pdf=b"")]
    doc = DocumentModel(pages=[PageSource(index=i, kind=PageKind.NOTES) for i in range(2)])
    asked = stage._Asked(answered={0: {"bs_ca__inventories"}})
    assert stage._lost_lines(doc, _plan(parts, [bs]), asked) == (None, [])


def _pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_the_auditors_report_is_sent_when_no_statement_page_was_classified():
    """No statement page classified: the auditor's report is sent all the same, because every
    page is (`W.pages_to_send`). It used to be found by its heading before a run of statement
    pages only, so on such a filing it was not sent."""
    raw = _pdf(["Annual report", "Chairman's statement",
                "INDEPENDENT AUDITOR'S REPORT", "INDEPENDENT AUDITOR'S REPORT (continued)",
                "Notes to the financial statements"])
    doc = DocumentModel(pages=[PageSource(index=i, kind=PageKind.OTHER) for i in range(5)])
    pages, why = W.pages_to_send(doc)
    assert pages == [0, 1, 2, 3, 4] and why == "every page"
    assert {2, 3} <= set(pages)
    import json
    import pathlib

    from app.schemas.line_items import load_line_item_set

    templates = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
    hk = load_line_item_set(json.loads((templates / "output_csv_hk_line_items.json")
                                       .read_text(encoding="utf-8")), resolve=True)
    plan = W.plan(doc, raw, hk, max_pages=600, max_request_bytes=20 * 1024 * 1024,
                  lines_per_request=40)
    assert plan.pages == [0, 1, 2, 3, 4]
    assert sorted(p for part in plan.parts for p in part.pages) == [0, 1, 2, 3, 4]


def _part():
    part = W.Part(pages=[34, 35], pdf=b"")
    part.folios = {34: ("66", "67"), 35: ("68", "69")}
    return part


def test_a_cited_printed_folio_maps_back_to_the_file_page_that_prints_it():
    part = _part()
    assert W.page_of_folio(part, 67) == 34
    assert W.page_of_folio(part, "69") == 35
    assert W.page_of_folio(part, 70) is None
    assert W.page_of_folio(W.Part(pages=[1], pdf=b""), 1) is None      # no folios known


def test_the_question_names_the_printed_pages_of_each_spread():
    note = W._spread_note(_part())
    assert "two-page spreads" in note and "1: 66-67; 2: 68-69" in note
    single = W.Part(pages=[3], pdf=b"")
    single.folios = {3: ("12",)}
    assert W._spread_note(single) == ""


def test_the_folios_of_each_half_are_read_off_the_classified_spread():
    sheet = PageSource(index=34, kind=PageKind.FACE, printed_page="66", evidence={"spread": {
        "fold": 0.4975, "halves": [{"side": "left", "printed_page": "66"},
                                   {"side": "right", "printed_page": None}]}})
    page = PageSource(index=3, kind=PageKind.NOTES, printed_page="12")
    folios = W.page_folios(DocumentModel(pages=[sheet, page]))
    assert folios == {34: ("66", "67"), 3: ("12",)}


def _word(text, x0, x1, y=0.30):
    return W.PageWord(text=text, box=BBox(x0=x0, y0=y, x1=x1, y1=y + 0.012))


def test_a_right_page_figure_takes_its_caption_from_the_right_page():
    words = [_word("Gross", 0.05, 0.08), _word("profit", 0.085, 0.11),
             _word("1,365,313", 0.36, 0.40),
             _word("Property,", 0.55, 0.60), _word("plant", 0.605, 0.63),
             _word("1,908,274", 0.86, 0.90)]
    hit = words[-1]
    assert [w.text for w in W._row_caption(hit, words, 0.4975)] == ["Property,", "plant"]
    # Without the fold the row ran on across it, and the caption was the left page's.
    assert [w.text for w in W._row_caption(hit, words)] == ["Gross", "profit"]


def test_a_figure_placed_by_caption_alone_is_looked_for_on_each_page_of_the_spread():
    words = [_word("Total", 0.05, 0.08), _word("assets", 0.085, 0.11),
             _word("Inventories", 0.55, 0.62)]
    page = W.PageText(numbers=set(), words=words, fold=0.4975)
    box, label = W.locate(Decimal("0"), "Inventories", page)
    assert box is not None and box.x0 >= 0.5


def _readings_for(cited_page):
    """One figure, total equity 1,207,806, printed on BOTH sheets of the part — the balance sheet
    (sheet 34, folio 67) and the equity statement (sheet 35, folio 68) — answered citing a page."""
    part = _part()
    item = SimpleNamespace(key="bs_equity__total_equity_and_reserves", namespace="template",
                           in_output=True, statement="balance_sheet", output_structure=None)
    chunk = W.Chunk([item], "balance_sheet")
    reply = W.WholePdfReply(lines=[W.WholePdfLine(key=item.key, figures=[W.WholePdfFigure(
        amount="1,207,806", page=cited_page, caption="Total equity")])])
    texts = {p: W.PageText(numbers={Decimal("1207806")}, words=[]) for p in part.pages}
    doc = DocumentModel(pages=[PageSource(index=p, kind=PageKind.FACE) for p in part.pages])
    readings, _, _, _ = stage._readings(doc, [part], [chunk], {(0, 0): reply}, texts)
    return readings


def test_a_figure_cited_by_its_printed_folio_is_placed_on_the_sheet_that_prints_it():
    """Printed on two sheets of the part, the figure was not found on the page it cited (no file
    page 67) and was left unplaced; the folio names the balance sheet's sheet."""
    (r,) = _readings_for(67)
    assert r.check.status == "moved" and r.check.page == 34
    assert "cited printed page 67" in r.check.note


def test_a_figure_cited_by_its_file_page_stays_there():
    (r,) = _readings_for(2)
    assert r.check.status == "on_page" and r.check.page == 35
