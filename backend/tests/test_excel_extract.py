"""Deterministic Excel extraction → line items with exact cell-level provenance."""
from __future__ import annotations

import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from app.core.models.enums import Basis
from app.services.excel_extract import cell_context, extract_workbook


def _wb_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "P&L"
    ws.append(["Particulars", "FY2025", "FY2024"])
    ws.append(["Revenue from operations", 964700, 901300])
    ws.append(["Other income", 3619, 2701])
    ws.append(["Total income", 968319, 904001])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_values_and_cell_provenance():
    items = extract_workbook(_wb_bytes(), document_id="doc1")
    labels = {li.source_label for li in items}
    assert {"Revenue from operations", "Other income", "Total income"} <= labels
    assert "Particulars" not in labels  # header row is not a data row

    rev = next(li for li in items if li.source_label == "Revenue from operations")
    # A period header is labelled POSITIONALLY and keeps its printed text as the display, exactly
    # as the native-PDF path does. Storing "FY2025" as the label meant nothing downstream
    # recognised the column, so current/prior resolution fell back to position for every
    # spreadsheet row — and a line with a figure in the prior column only had it reported as the
    # current year.
    cur = rev.get_value(Basis.CONSOLIDATED, period_label="current")
    assert cur is not None and int(cur.value) == 964700
    assert cur.period_display == "FY2025"
    # Exact, verifiable provenance — sheet + cell — with no OCR/LLM involved.
    assert cur.provenance is not None
    assert cur.provenance.sheet == "P&L"
    assert cur.provenance.cell == "B2"          # row 2, first value column
    assert cur.provenance.label_cell == "A2"
    assert cur.provenance.source_kind == "spreadsheet"
    prior = rev.get_value(Basis.CONSOLIDATED, period_label="prior")
    assert prior is not None and cur.provenance.cell != prior.provenance.cell
    assert prior.period_display == "FY2024"


def test_cell_context_windows_around_the_target():
    ctx = cell_context(_wb_bytes(), sheet="P&L", cell="B2", radius=2)
    assert ctx["sheet"] == "P&L" and ctx["target"] == "B2"
    # exactly one target cell, and it holds the right value
    target = [c for line in ctx["grid"] for c in line if c["is_target"]]
    assert len(target) == 1
    assert target[0]["ref"] == "B2" and target[0]["value"] == "964700" and target[0]["numeric"]
    # the label to the left is in the window and readable
    assert any(c["value"] == "Revenue from operations" for line in ctx["grid"] for c in line)
    assert "A" in ctx["col_letters"] and "B" in ctx["col_letters"]


def test_cell_context_rejects_bad_ref_and_unknown_sheet():
    with pytest.raises(ValueError):
        cell_context(_wb_bytes(), sheet="P&L", cell="not-a-cell")
    with pytest.raises(KeyError):
        cell_context(_wb_bytes(), sheet="Nope", cell="B2")


def test_cell_context_endpoint(client):
    import io as _io

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BS"
    ws.append(["Item", "2025"])
    ws.append(["Cash and cash equivalents", 1204])
    buf = _io.BytesIO()
    wb.save(buf)

    doc_id = client.post(
        "/api/v1/documents",
        files={"file": ("bs.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()["id"]

    r = client.get(f"/api/v1/documents/{doc_id}/cell-context", params={"sheet": "BS", "cell": "B2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "B2"
    assert any(c["is_target"] and c["value"] == "1204" for line in body["grid"] for c in line)

    # PDF documents have no cell context.
    from tests.fixtures.generate import make_native_pdf
    pdf_id = client.post(
        "/api/v1/documents", files={"file": ("x.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]
    assert client.get(f"/api/v1/documents/{pdf_id}/cell-context",
                      params={"sheet": "S", "cell": "A1"}).status_code == 400


def test_excel_flows_through_the_pipeline():
    from app.services.documents import run_extraction

    doc, ctx = run_extraction(_wb_bytes(), filename="statements.xlsx")
    assert doc.fmt.value in ("xlsx", "xls")
    assert len(doc.line_items) >= 3
    assert any(ev.provenance and ev.provenance.cell
               for li in doc.line_items for ev in li.values.values())


# --- scope_selection on a spreadsheet ---------------------------------------------------------
# The workbook path reads the same rulebook the PDF path does. Each test below is one declared
# block, and each of them was previously wrong on a sheet a filer would recognise.

def _sheet(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BS"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_a_group_company_band_is_read_and_is_not_mistaken_for_the_period_header():
    """"Group | Company" was in neither list the sheet reader had: the band went undetected, so
    the Company's figures were filed as the Group's — and the band ROW was then read as the period
    header, which named the columns after the entities instead of the years."""
    data = _sheet([
        ["", "Group", None, "Company", None],
        ["", "2024", "2023", "2024", "2023"],
        ["Trade receivables", 3410, 2900, 310, 270],
        ["Cash and cash equivalents", 1204, 980, 105, 90],
    ])
    items = extract_workbook(data, document_id="x")
    tr = next(li for li in items if li.source_label == "Trade receivables")
    got = {(v.basis.value, v.period_label): (int(v.value), v.period_display)
           for v in tr.values.values()}
    assert got == {
        ("consolidated", "current"): (3410, "2024"),
        ("consolidated", "prior"): (2900, "2023"),
        ("standalone", "current"): (310, "2024"),
        ("standalone", "prior"): (270, "2023"),
    }


def test_the_label_column_does_not_band_the_sheet():
    """The band scan used to read the LABEL column, where the statement's own scope word lives.
    Paired with a caption over a value column it made a band out of a heading and a column — and
    the two periods came out as two entities, one of them the wrong way round."""
    data = _sheet([
        ["Company", None, "Group"],
        [None, "31 December 2024", "31 December 2023"],
        ["Trade receivables", 3410, 2900],
        ["Cash and cash equivalents", 1204, 980],
    ])
    items = extract_workbook(data, document_id="x")
    tr = next(li for li in items if li.source_label == "Trade receivables")
    assert {(v.basis.value, v.period_label) for v in tr.values.values()} == {
        ("consolidated", "current"), ("consolidated", "prior")}


def test_one_basis_caption_does_not_band_the_sheet():
    """Both-or-nothing, as on the PDF path: a single caption cannot split a comparative, and
    reading it as one turns last year's column into another entity's current year."""
    data = _sheet([
        ["", "Consolidated", None],
        ["", "2024", "2023"],
        ["Trade receivables", 3410, 2900],
    ])
    items = extract_workbook(data, document_id="x")
    tr = next(li for li in items if li.source_label == "Trade receivables")
    assert {(v.basis.value, v.period_label) for v in tr.values.values()} == {
        ("consolidated", "current"), ("consolidated", "prior")}


def test_the_current_column_is_the_later_dated_header():
    """``period_selection``: the current period is the column whose heading is the latest date,
    not the leftmost column."""
    data = _sheet([
        ["", "31 December 2023", "31 December 2024"],
        ["Trade receivables", 2900, 3410],
    ])
    items = extract_workbook(data, document_id="x")
    tr = next(li for li in items if li.source_label == "Trade receivables")
    cur = tr.get_value(Basis.CONSOLIDATED, period_label="current")
    assert cur is not None and int(cur.value) == 3410 and cur.period_display == "31 December 2024"


def test_a_restated_comparative_keeps_the_original_beside_it():
    """``restatement_rule``: the restated column IS the comparative, and the original printed next
    to it is kept as "prior_original" rather than overwritten — as on the PDF path."""
    data = _sheet([
        ["", "2024", "2023", "2023 (restated)"],
        ["Trade receivables", 3410, 2900, 2950],
    ])
    items = extract_workbook(data, document_id="x")
    tr = next(li for li in items if li.source_label == "Trade receivables")
    got = {v.period_label: int(v.value) for v in tr.values.values()}
    assert got == {"current": 3410, "prior": 2950, "prior_original": 2900}
    flags = {v.period_label: v.confidence.flags for v in tr.values.values()}
    assert flags == {"current": [], "prior": ["restated"], "prior_original": ["prior_original"]}


def test_a_per_column_currency_annotation_beats_the_sheet_unit():
    """A Hong Kong workbook routinely prints two currencies side by side — a CNY column beside an HKD
    one. The sheet-level scan joins the whole header row into one blob and matches the first declared
    signal in it, so ONE currency was stamped on every fact and the other column's figures were
    reported in money that is not theirs. Nothing downstream can catch it: the currency is metadata,
    so no total disagrees, and an FX translation then applies the wrong rate to a right figure."""
    data = _sheet([
        ["", "RMB'000", "HK$'000"],
        ["", "2024", "2023"],
        ["Trade receivables", 3410, 3760],
    ])
    items = extract_workbook(data, document_id="x")
    tr = next(li for li in items if li.source_label == "Trade receivables")
    got = {v.period_label: (v.unit_ctx.currency, int(v.unit_ctx.scale_factor), int(v.value))
           for v in tr.values.values()}
    assert got == {"current": ("CNY", 1000, 3410), "prior": ("HKD", 1000, 3760)}


def test_the_sheet_unit_is_persisted_on_every_fact():
    """``units_and_currency``: resolved from this statement's header (a sheet cannot see a cover
    page), recorded on every fact, and never applied to the figures."""
    data = _sheet([
        ["", "RMB'000", "RMB'000"],
        ["", "2024", "2023"],
        ["Trade receivables", 3410, 2900],
    ])
    items = extract_workbook(data, document_id="x")
    tr = next(li for li in items if li.source_label == "Trade receivables")
    units = {(v.unit_ctx.currency, int(v.unit_ctx.scale_factor)) for v in tr.values.values()}
    assert units == {("CNY", 1000)}
    assert int(tr.get_value(Basis.CONSOLIDATED, period_label="current").value) == 3410
