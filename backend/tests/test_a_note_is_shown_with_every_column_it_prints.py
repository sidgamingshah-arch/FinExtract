"""A NOTE IS SHOWN WITH EVERY COLUMN IT PRINTS (`documents._note_table_rows`, `_note_tables`,
`extractions._serialize_notes`).

The Notes screen keyed a note's figures by their period label or display, so a PP&E note's columns
reached it as "current, prior, col2 …" — Land, Buildings, Plant and Total unnamed — two columns
printing the same year collapsed into one with the second's figures overwriting the first's, and
every table a note prints was laid into one grid of every column any of them has (13 to 16 columns
on 河钢股份 000709, most cells empty).

Pinned here:
  * a column is named by what is printed over it, and ordered as printed;
  * two columns printing the same heading stay two columns;
  * a column header line is not a row; a row keeps its block and, on a laid-out table, its year;
  * a note's tables are shown one by one, a table continued over a page joined back up;
  * the worker serializes each figure's printed column and each row's block.
"""
from __future__ import annotations

from decimal import Decimal

from app.api.routes.documents import _note_index, _note_table_rows, _note_tables


def _v(label, value, *, column=None, heading=None, display=None, basis="consolidated", x=None,
       index=None):
    v = {"period_label": label, "value": value, "basis": basis}
    if column:
        v["column"] = column
    if heading:
        v["column_heading"] = heading
    if display:
        v["period_display"] = display
    if x is not None:
        v["provenance"] = {"bbox": {"x0": x, "y0": 0.5, "x1": x + 0.05, "y1": 0.51}}
    if index is not None:
        v["column_index"] = index
    return v


PPE = ["Land", "Buildings", "Plant", "Total"]


def _ppe_row(label, figures, *, block="", period="", role="line"):
    return {"label": label, "role": role, "block": block, "block_period": period,
            "values": [_v(k, f, column=c, x=0.4 + 0.1 * i)
                       for i, (k, c, f) in enumerate(zip(("current", "prior", "col2", "col3"),
                                                         PPE, figures)) if f is not None]}


def test_a_ppe_note_is_every_asset_column_under_its_printed_heading():
    rows = [{"label": "Land Buildings Plant Total", "header_line": True, "values": []},
            _ppe_row("At 1 January 2024", ["500", "2000", "3000", "5500"], block="Cost",
                     period="current"),
            _ppe_row("Additions", [None, "150", "400", "550"], block="Cost", period="current"),
            _ppe_row("At 31 December 2024", ["500", "2150", "3400", "6050"], block="Cost",
                     period="current", role="total")]
    table = _note_table_rows(rows, ["2024", "2023"])
    assert table["layout"] == "columns"
    assert table["columns"] == PPE
    assert [r["label"] for r in table["rows"]] == ["At 1 January 2024", "Additions",
                                                    "At 31 December 2024"]
    assert table["rows"][1]["values"] == {"Land": None, "Buildings": "150", "Plant": "400",
                                          "Total": "550"}
    assert table["rows"][1]["section"] == "Cost" and table["rows"][1]["period"] == "2024"
    assert table["rows"][2]["kind"] == "tot"


def test_columns_are_ordered_as_printed_across_the_page():
    row = {"label": "Depreciation",
           "values": [_v("col3", "-90", column="Total", x=0.8),
                      _v("current", "-10", column="Land", x=0.4),
                      _v("prior", "-80", column="Buildings", x=0.6)]}
    assert _note_table_rows([row], [])["columns"] == ["Land", "Buildings", "Total"]
    # A matrix's own column index wins over the page position (a sideways page runs down y).
    row = {"label": "Balance", "values": [_v("current", "1", column="B", x=0.2, index=2),
                                          _v("prior", "2", column="A", x=0.9, index=1)]}
    assert _note_table_rows([row], [])["columns"] == ["A", "B"]


def test_two_columns_printing_the_same_heading_stay_two_columns():
    row = {"label": "Bank loans",
           "values": [_v("current", "10", display="2024", x=0.5),
                      _v("prior", "12", display="2024", x=0.6),
                      _v("current", "3", display="2024", basis="standalone", x=0.7)]}
    table = _note_table_rows([row], ["2024", "2023"])
    assert table["columns"] == ["2024 · Group", "2024 · Group (2)", "2024 · Company"]
    assert list(table["rows"][0]["values"].values()) == ["10", "12", "3"]


def test_the_printed_heading_names_a_period_column_and_the_period_is_the_fallback():
    row = {"label": "Trade receivables",
           "values": [_v("current", "100", heading="期末余额 · 账面余额"),
                      _v("prior", "90")]}
    assert _note_table_rows([row], ["2024", "2023"])["columns"] == ["期末余额 · 账面余额", "2023"]
    row = {"label": "x", "values": [_v("col5", "1")]}
    assert _note_table_rows([row], [])["columns"] == ["Column 1"]


def test_a_note_is_shown_table_by_table_and_a_continued_table_is_one():
    details = [
        {"no": "7", "title": "Receivables", "page": 130, "printed_page": "128", "rows": [
            {"label": "应收账款", "values": [_v("current", "1", heading="账面余额"),
                                         _v("prior", "2", heading="坏账准备"),
                                         _v("col2", "3", heading="账面价值")]}]},
        {"no": "7", "title": "Receivables (continued)", "page": 131, "printed_page": "129",
         "rows": [{"label": "1年以内", "values": [_v("current", "5", display="2024"),
                                               _v("prior", "4", display="2023")]}]},
        {"no": "7", "title": "Receivables (continued)", "page": 132, "rows": [
            {"label": "1-2年", "values": [_v("current", "6", display="2024"),
                                         _v("prior", "7", display="2023")]},
            {"label": "期初余额 本期计提 期末余额", "header_line": True, "values": []},
            {"label": "坏账准备", "values": [_v("current", "8", heading="期初余额"),
                                         _v("prior", "9", heading="本期计提")]}]},
    ]
    note = _note_index(details)["7"]
    tables = _note_tables(note["rows"], ["2024", "2023"])
    assert [t["columns"] for t in tables] == [["账面余额", "坏账准备", "账面价值"],
                                              ["2024", "2023"], ["期初余额", "本期计提"]]
    # The ageing table continued from p.129 onto the next page is one table, starting where it
    # started.
    assert [r["label"] for r in tables[1]["rows"]] == ["1年以内", "1-2年"]
    assert [t["page"] for t in tables] == ["128", "129", 132]


def test_the_worker_serializes_each_figures_printed_column():
    from app.api.routes.extractions import _serialize_notes
    from app.core.models.document import DocumentModel
    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, NoteItem, NotesTable

    row = NoteItem(raw_label="Depreciation", printed_columns=["Land", "Total"])
    for key, value, heading in (("current", "-10", "Land"), ("prior", "-90", "Total")):
        row.set_value(ExtractedValue(value=Decimal(value), value_raw=Decimal(value),
                                     basis=Basis.CONSOLIDATED, period_label=key,
                                     column_heading=heading))
    doc = DocumentModel(filename="f.pdf")
    doc.notes = [NotesTable(note_number="14", title="PPE", items=[row])]
    served = _serialize_notes(doc)[0]["rows"][0]
    assert served["printed_columns"] == ["Land", "Total"]
    assert [(v["period_label"], v["column_heading"]) for v in served["values"]] == [
        ("current", "Land"), ("prior", "Total")]
