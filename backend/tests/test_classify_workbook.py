"""A worksheet gets a statement, so a spreadsheet is scoped like a page.

THE DEFECT THIS CLOSES. ``ClassifyStage.run`` returned early for anything that is not a PDF, so
every worksheet kept ``statement=None`` from ingest. A statement is not decoration downstream, it is
a BOUNDARY:

* ``residual._section_of_row`` guards each of its structural signals with
  ``if statement and statement_of(nxt) not in (None, statement)``. With ``statement=None`` the guard
  is inert, so the walk runs past the end of the sheet it started on — a balance-sheet row could take
  its section from a cash-flow subtotal on a later sheet.
* ``residual._route_by_template`` is keyed by statement type outright, so it placed no Excel row at
  all.
* the mapping stage had no statement to scope a row's candidate list by, so every spreadsheet row was mapped with the
  whole ontology in front of it instead of one statement's concepts.

The title vocabulary is not duplicated for spreadsheets: ``statement_of_sheet`` feeds the sheet name
and the sheet's leading text cells through the same ``_title_candidates`` / ``_resolve_statement``
that a printed page's lines go through, so the strong/weak patterns, the negative filter, the
two-line join, the OCI collapse and the contents-page guard all apply unchanged.

The one deliberate difference is ``test_a_tab_name_does_not_need_the_english_anchor``.
"""
from __future__ import annotations

import io

import openpyxl
import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import DocFormat, PageKind, PageSourceKind
from app.core.stage import PipelineContext
from app.stages.classify import ClassifyStage, statement_of_sheet
from app.services.excel_extract import extract_workbook


# ── the resolver ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tab, statement", [
    ("Balance Sheet", "balance_sheet"),
    ("Financial Position", "balance_sheet"),
    ("Cash Flow", "cash_flow"),
    ("Cash Flows", "cash_flow"),
    ("Income Statement", "profit_and_loss"),
    ("Profit and Loss", "profit_and_loss"),
    ("Changes in Equity", "changes_in_equity"),
    ("綜合財務狀況表", "balance_sheet"),
    ("現金流量表", "cash_flow"),
    ("權益變動表", "changes_in_equity"),
])
def test_a_tab_name_names_the_statement(tab, statement):
    assert statement_of_sheet(tab, [])[0] == statement


@pytest.mark.parametrize("tab", ["Cover", "Notes", "Assumptions", "Sheet1", "Data", "Inputs",
                                 "Cash", "FX rates", "Equity", "Index"])
def test_a_tab_that_names_no_statement_resolves_to_none(tab):
    """Refusing is the safe answer: ``kind`` gates ``doc.face_pages()``, so calling a cover sheet or a
    list of assumptions a face would put its rows into the statement. "Equity" is in this list on
    purpose — it is a section of the balance sheet, not the equity STATEMENT, and the weak pattern
    for that statement requires "changes in … equity"."""
    assert statement_of_sheet(tab, [])[0] is None


def test_a_tab_name_does_not_need_the_english_anchor():
    """The one deliberate difference from the page path. ``_resolve_statement`` only lets a weak
    pattern fire on text carrying an English structural anchor ("statement of", "balance sheet"),
    because a page's prose says "cash flows" constantly and a weak match alone would classify an
    auditor's paragraph. A worksheet TAB is not prose — it is a deliberate label of what the sheet
    holds — so the sheet-name candidate is self-anchoring. Without that, the commonest tab name in
    any model resolves to nothing."""
    assert statement_of_sheet("Cash Flow", [])[0] == "cash_flow"
    # …and the page path is unmoved: the same words in a line of page text still need the anchor.
    from app.stages.classify import _resolve_statement, _title_candidates
    unanchored = _title_candidates([{"text": "Cash Flow", "y": 0.0, "size": 0.0, "bold": False}])
    assert _resolve_statement(unanchored)[0] is None


def test_a_title_inside_the_sheet_is_used_when_the_tab_is_unhelpful():
    """The abbreviations a modeller actually types — "BS", "CF", "P&L" — name nothing on their own,
    and do not need to: the sheet itself carries the real title."""
    assert statement_of_sheet("BS", ["ABC Holdings Limited",
                                     "Consolidated statement of financial position",
                                     "As at 31 December 2024"])[0] == "balance_sheet"
    assert statement_of_sheet("CF", ["綜合現金流量表"])[0] == "cash_flow"


def test_a_title_split_across_two_cells_is_joined():
    """Inherited from `_title_candidates` rather than reimplemented."""
    assert statement_of_sheet("Sheet2", ["CONSOLIDATED STATEMENT OF",
                                         "CASH FLOWS"])[0] == "cash_flow"


def test_a_contents_sheet_naming_every_statement_resolves_to_none():
    """Also inherited: a sheet listing all four is an index, not any one of them."""
    assert statement_of_sheet("Contents", [
        "Consolidated statement of financial position",
        "Consolidated statement of profit or loss",
        "Consolidated statement of cash flows",
        "Consolidated statement of changes in equity"])[0] is None


# ── the stage ──────────────────────────────────────────────────────────────────────────────────
SHEETS: dict[str, list[list]] = {
    "Cover": [["ABC Holdings Limited"], ["Annual Report 2024"]],
    "BS": [["Consolidated statement of financial position"], ["As at 31 December 2024"],
           [None, "2024", "2023"], ["NON-CURRENT ASSETS", None, None], ["Goodwill", 8000, 7000],
           ["CURRENT ASSETS", None, None], ["Inventories", 1234, 5678]],
    "Cash Flow": [[None, "2024", "2023"], ["Operating activities", None, None],
                  ["Profit before tax", 900, 800]],
    "Assumptions": [["Discount rate", "8%"]],
}


def _workbook_bytes(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _classified(sheets: dict[str, list[list]]) -> tuple[DocumentModel, bytes]:
    data = _workbook_bytes(sheets)
    doc = DocumentModel(filename="f.xlsx", fmt=DocFormat.XLSX)
    doc.pages = [PageSource(index=i, source_kind=PageSourceKind.NATIVE, kind=PageKind.UNKNOWN)
                 for i in range(len(sheets))]
    ClassifyStage().run(doc, PipelineContext(raw_bytes=data))
    return doc, data


def test_the_stage_names_each_sheets_statement():
    doc, _ = _classified(SHEETS)
    got = {(p.evidence or {}).get("sheet"): p.statement for p in doc.pages}
    assert got == {"Cover": None, "BS": "balance_sheet",
                   "Cash Flow": "cash_flow", "Assumptions": None}


def test_only_a_sheet_with_a_statement_becomes_a_face_page():
    doc, _ = _classified(SHEETS)
    assert [(p.evidence or {}).get("sheet") for p in doc.face_pages()] == ["BS", "Cash Flow"]
    unresolved = [p for p in doc.pages if not p.statement]
    assert all(p.kind is PageKind.UNKNOWN for p in unresolved), (
        "a sheet whose title did not resolve was guessed at rather than left alone")


def test_a_format_the_stage_cannot_read_is_still_returned_untouched():
    """The early return has to stay for the formats that have no classifier."""
    doc = DocumentModel(filename="f.html", fmt=DocFormat.HTML)
    doc.pages = [PageSource(index=0, source_kind=PageSourceKind.NATIVE, kind=PageKind.UNKNOWN)]
    ClassifyStage().run(doc, PipelineContext(raw_bytes=b"<html></html>"))
    assert doc.pages[0].statement is None
    assert doc.pages[0].kind is PageKind.UNKNOWN


def test_a_workbook_that_cannot_be_reopened_does_not_fail_the_run():
    doc = DocumentModel(filename="f.xlsx", fmt=DocFormat.XLSX)
    doc.pages = [PageSource(index=0, source_kind=PageSourceKind.NATIVE, kind=PageKind.UNKNOWN)]
    ClassifyStage().run(doc, PipelineContext(raw_bytes=b"not a workbook"))
    assert doc.pages[0].statement is None


# ── the payoff: the boundary reaches the code that needs it ────────────────────────────────────
def _statement_by_row(doc, stmt_by_page):
    """Each row's statement, read the way the mapping stage reads it (``_statement_of``)."""
    out = []
    for li in doc.line_items:
        page = next((ev.provenance.page_index for ev in li.values.values()
                     if ev.provenance is not None), None)
        out.append((li, stmt_by_page.get(page) if page is not None else None))
    return out


def test_rows_from_different_sheets_get_different_statements():
    """What the statement is FOR. The mapping stage scopes every row's candidate list by the
    statement of the page it was printed on (``map_ontology._statement_of``), so with every sheet
    unclassified no row got a statement-scoped list at all. Two classified sheets must now yield
    two statements across the workbook's rows.

    This used to be asserted through ``map_ontology.batch_groups``, which grouped rows by
    (statement, basis, period) so a batched LLM call could see a whole statement at once. That
    call is gone — rows are mapped one at a time and deterministically — so the boundary is
    observed where it is now actually read: per row.
    """
    doc, data = _classified(SHEETS)
    doc.line_items = extract_workbook(data, document_id="d1")
    assert doc.line_items, "the workbook produced no rows"

    stmt_by_page = {p.index: p.statement for p in doc.pages if p.statement}
    assert stmt_by_page, "no sheet carried a statement, so the boundary is still absent"

    by_row = _statement_by_row(doc, stmt_by_page)
    named = sorted({stmt for _li, stmt in by_row if stmt})
    assert named == ["balance_sheet", "cash_flow"], (
        f"rows were not scoped by statement: {sorted({s for _l, s in by_row}, key=str)}")

    # And no row takes its statement from a sheet it was not printed on, which is the cross-sheet
    # walk the guard exists to stop.
    for li, stmt in by_row:
        if not stmt:
            continue
        pages = {ev.provenance.page_index for ev in li.values.values()
                 if ev.provenance is not None}
        assert len(pages) == 1, f"row {li.source_label!r} spans sheets {pages}"
        assert stmt_by_page[next(iter(pages))] == stmt


def test_a_sheet_with_no_statement_leaves_its_rows_unscoped():
    """An unclassified sheet's rows must not borrow a neighbouring statement — they resolve to
    ``None``, which is what a row we cannot place is supposed to get: an unconstrained match."""
    doc, data = _classified(SHEETS)
    doc.line_items = extract_workbook(data, document_id="d1")
    stmt_by_page = {p.index: p.statement for p in doc.pages if p.statement}
    unclassified = {p.index for p in doc.pages if not p.statement}
    for li, stmt in _statement_by_row(doc, stmt_by_page):
        pages = {ev.provenance.page_index for ev in li.values.values()
                 if ev.provenance is not None}
        if pages & unclassified:
            assert stmt is None, f"a row on an unclassified sheet was scoped to {stmt}"
