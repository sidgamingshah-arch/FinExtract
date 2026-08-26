"""The flat CSV export: the whole extraction on one sheet, a row per line item, a column per figure.

The plainest artifact the export module produces, and the one whose readers are other programs —
pasted into a model, loaded by pandas, diffed against last quarter. So the properties worth holding
are not about formatting: they are that the sheet is rectangular, that it does not lose a figure or
put one in the wrong column, and that opening it does not execute anything.

THE SHEET IS AS WIDE AS THE FILING IS. Two columns for a statement that printed one column of
figures; three for one that printed two years; wider again for one that printed Group and Company
as well. The alternative — a fixed two columns with the period named inside the caption — was the
first version of this format, and it was wrong in a way worth recording: a caption is the filing's
own words, an analyst joins and greps on it, and "Revenue (2023)" is not a caption any filing
printed.

FOUR THINGS ARE EASY TO GET WRONG HERE and each has a test below.

* A figure has to land under its own column. Placing by position instead of by (basis, period)
  puts last year's number under this year's heading on any row that skipped a column — silently,
  and in a file nobody reads by eye.
* A caption is text lifted out of an uploaded document. Excel, LibreOffice and Sheets EXECUTE a
  cell beginning "=", "+", "-" or "@", so a filing can carry a formula into the analyst's machine.
  The workbook path already guards this (openpyxl promotes a leading "=" to a live formula, which
  is why `build_rows_xlsx` forces its formula column to a string type); a CSV has no cell type to
  force.
* Excel reads a CSV in the system code page unless the file opens with a byte-order mark. This
  product supports Chinese, Arabic and French, and this file's entire content is captions.
* `float("1200")` is 1200.0, and a figure restated to a precision the filing never printed is a
  different figure to anything that parses this file.
"""
from __future__ import annotations

import time

import pytest

from app.services.export import build_rows_csv, build_statements_csv

pytest.importorskip("fitz")

from tests.fixtures.generate import make_native_pdf


def _row(label: str, *values) -> dict:
    """An extracted row in served shape. ``values`` are (period_label, value) or
    (period_label, value, basis) — the three fields the column rule reads."""
    return {"source_label": label,
            "values": [{"period_label": v[0], "value": v[1],
                        "basis": (v[2] if len(v) > 2 else "consolidated"),
                        "period_display": None}
                       for v in values]}


def _read(data: bytes) -> list[list[str]]:
    import csv
    import io

    # utf-8-SIG on the way in too: a reader that does not strip the mark sees it glued to the first
    # header, which is exactly the failure the mark exists to prevent in the other direction.
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


# --- the sheet is as wide as the filing ------------------------------------------------------

def test_one_column_of_figures_gives_two_columns():
    """A filing that printed a single column needs no heading to tell anything apart, and naming
    the period there would put one on every ordinary file for the benefit of nobody."""
    table = _read(build_rows_csv([_row("Cash", ("current", "1200")),
                                  _row("Trade receivables", ("current", "980"))]))
    assert table == [["Line item", "Value"], ["Cash", "1200"], ["Trade receivables", "980"]]


def test_two_years_give_three_columns_headed_by_the_years():
    """THE WIDENING. The comparative year gets its own column rather than its own row, and the
    caption stays the filing's own words."""
    table = _read(build_rows_csv([_row("Revenue", ("2023", "5000"), ("2022", "4400")),
                                  _row("Cost of sales", ("2023", "-3100"), ("2022", "-2800"))]))
    assert table == [["Line item", "2023", "2022"],
                     ["Revenue", "5000", "4400"],
                     ["Cost of sales", "-3100", "-2800"]]


def test_more_years_keep_widening():
    """Three periods, five columns. Nothing about the shape is capped at two."""
    table = _read(build_rows_csv([_row("Revenue", ("2023", "5000"), ("2022", "4400"),
                                       ("2021", "3900"), ("2020", "3500"))]))
    assert table[0] == ["Line item", "2023", "2022", "2021", "2020"]
    assert table[1] == ["Revenue", "5000", "4400", "3900", "3500"]


def test_group_and_company_get_their_own_columns_headed_by_both():
    """Consolidated and standalone are separate columns, and the heading names the basis as well
    as the period — a heading of just "2023" over two of them would be two columns most readers
    of a CSV keep exactly one of."""
    table = _read(build_rows_csv([
        _row("Revenue", ("2023", "5000"), ("2022", "4400"),
             ("2023", "3100", "standalone"), ("2022", "2700", "standalone"))]))
    assert table[0] == ["Line item", "Consolidated 2023", "Consolidated 2022",
                        "Standalone 2023", "Standalone 2022"]
    assert table[1] == ["Revenue", "5000", "4400", "3100", "2700"]


def test_the_basis_is_named_only_when_the_file_carries_two():
    """One basis needs no basis word, or every caption of every ordinary filing carries one."""
    table = _read(build_rows_csv([_row("Revenue", ("2023", "5000"), ("2022", "4400"))]))
    assert table[0] == ["Line item", "2023", "2022"]


def test_consolidated_columns_come_before_standalone():
    """A total order on the columns, whatever order the rows happen to carry their values in.
    Consolidated leads because it is the default reading of a filing and the view every screen
    opens on."""
    table = _read(build_rows_csv([
        _row("Revenue", ("2023", "3100", "standalone"), ("2023", "5000", "consolidated"))]))
    assert table[0] == ["Line item", "Consolidated 2023", "Standalone 2023"]
    assert table[1] == ["Revenue", "5000", "3100"]


def test_the_columns_keep_the_order_they_were_printed_in():
    """Not sorted by heading — taken off the page. A matrix statement's component columns are
    named, not dated, and "col10" must not sort in front of "col3"."""
    table = _read(build_rows_csv([_row("Balance at 1 January",
                                       ("Share capital", "100"), ("Retained profits", "900"),
                                       ("Translation reserve", "-40"))]))
    assert table[0] == ["Line item", "Share capital", "Retained profits", "Translation reserve"]


def test_the_real_period_end_date_heads_the_column_when_there_is_one():
    """`period_label` is positional ("current"); `period_display` is the date the column actually
    printed. A reader of this file has the heading or nothing."""
    rows = [{"source_label": "Revenue",
             "values": [{"period_label": "current", "period_display": "31 December 2023",
                         "value": "5000", "basis": "consolidated"},
                        {"period_label": "prior", "period_display": "31 December 2022",
                         "value": "4400", "basis": "consolidated"}]}]
    assert _read(build_rows_csv(rows))[0] == ["Line item", "31 December 2023", "31 December 2022"]


# --- the sheet is rectangular, and every figure is under its own heading ----------------------

def test_a_figure_lands_under_its_own_column_not_its_position():
    """THE PLACEMENT DEFECT. A row that carries only the prior year must leave the current-year
    cell EMPTY, not shift its figure left into it. Writing values positionally passes every test
    above and puts last year's number under this year's heading here."""
    table = _read(build_rows_csv([_row("Revenue", ("2023", "5000"), ("2022", "4400")),
                                  _row("Discontinued operations", ("2022", "310"))]))
    assert table[0] == ["Line item", "2023", "2022"]
    assert table[2] == ["Discontinued operations", "", "310"], \
        "a prior-year-only figure was filed under the current year"


def test_every_row_has_exactly_as_many_cells_as_the_header():
    """A row shorter than the header is what turns a readable file into a parse error three
    thousand lines in. Asserted over a deliberately ragged set of rows."""
    table = _read(build_rows_csv([
        _row("Revenue", ("2023", "5000"), ("2022", "4400")),
        _row("Other income", ("2023", "12")),
        {"source_label": "Reserves", "values": []},
        _row("Tax", ("2022", "-90"), ("2023", "-110", "standalone")),
    ]))
    assert len({len(r) for r in table}) == 1, "the sheet is not rectangular"
    assert len(table[0]) == 4


def test_a_run_with_no_figures_at_all_is_still_two_columns():
    """The header promises a Value column, so every row has to carry the cell — even when nothing
    was extracted to put in it."""
    table = _read(build_rows_csv([{"source_label": "Other reserves", "values": []}]))
    assert table == [["Line item", "Value"], ["Other reserves", ""]]


def test_one_row_per_printed_line_never_merged_by_caption():
    """Two sections legitimately print "Total". Adding those together because they read alike
    would fabricate a figure that appears in no filing."""
    table = _read(build_rows_csv([_row("Total", ("current", "500")),
                                  _row("Total", ("current", "700"))]))
    assert table[1:] == [["Total", "500"], ["Total", "700"]]


def test_two_columns_cannot_share_a_heading():
    """A collision is not cosmetic: most readers of a CSV keep exactly one of two identically
    named columns, so it is silently lost data. The printed position distinguishes them."""
    rows = [{"source_label": "Revenue",
             "values": [{"period_label": "current", "period_display": "FY23", "value": "1",
                         "basis": "consolidated"},
                        {"period_label": "prior", "period_display": "FY23", "value": "2",
                         "basis": "consolidated"}]}]
    header = _read(build_rows_csv(rows))[0]
    assert len(set(header)) == len(header), f"duplicate column heading in {header}"


# --- what opening the file must not do ------------------------------------------------------

@pytest.mark.parametrize("caption", ["=1+1", "=cmd|' /c calc'!A1", "+SUM(A1)", "@SUM(A1)",
                                     "-2+3+cmd|' /c calc'!A1"])
def test_a_caption_the_document_printed_cannot_execute(caption):
    """THE INJECTION PATH: the caption is text out of an UPLOADED file, and the download is opened
    by the analyst. The apostrophe is what those applications read as "the rest is literal".

    Asserted on the written field rather than on the row, so a future change that quotes the field
    instead — quoting does NOT disarm this; Excel evaluates the quoted content just the same —
    cannot pass this test."""
    table = _read(build_rows_csv([_row(caption, ("current", "1"))]))
    assert table[1][0] == "'" + caption
    assert not table[1][0].startswith(("=", "+", "-", "@")), "the field can still be executed"


def test_an_ordinary_caption_is_left_exactly_as_printed():
    """The guard fires on the four leads and nothing else. A caption is the filing's own words and
    must reach the file unaltered, or every downstream join against it breaks."""
    table = _read(build_rows_csv([_row("Cash and cash equivalents", ("current", "1")),
                                  _row("(Loss)/profit for the year", ("current", "2")),
                                  _row("本年度即期稅項", ("current", "3"))]))
    assert [r[0] for r in table[1:]] == ["Cash and cash equivalents",
                                         "(Loss)/profit for the year", "本年度即期稅項"]


def test_the_file_opens_as_utf8_in_excel():
    """The byte-order mark. Without it Excel on Windows reads the file in the system code page and
    a Chinese, Arabic or French caption arrives as mojibake — in the export whose whole content is
    captions."""
    data = build_rows_csv([_row("本年度即期稅項", ("current", "1200"))])
    assert data.startswith(b"\xef\xbb\xbf"), "no BOM: Excel will not read this as UTF-8"
    assert "本年度即期稅項" in data.decode("utf-8-sig")


# --- the figures ----------------------------------------------------------------------------

def test_a_figure_is_carried_at_the_precision_it_was_printed():
    """Verbatim, not round-tripped through float. "1200" must not become "1200.0": this file is
    read by programs, and a number restated to a precision the filing never printed is a different
    number to anything comparing them."""
    table = _read(build_rows_csv([_row("Cash", ("current", "1200")),
                                  _row("Rate", ("current", "1.50"))]))
    assert [r[1] for r in table[1:]] == ["1200", "1.50"]


def test_a_negative_is_a_minus_sign_not_a_bracket():
    """The screen shows accounting brackets; a machine-readable dump must not. And the injection
    guard must not fire on a value cell, which would turn -450 into the text "'-450"."""
    table = _read(build_rows_csv([_row("Finance costs", ("current", "-450"))]))
    assert table[1] == ["Finance costs", "-450"]


def test_requested_units_convert_the_figures():
    """The unit choice is a conversion of the FIGURES, so it reaches this format even though the
    Include list (which adds analysis SHEETS) cannot."""
    table = _read(build_rows_csv([_row("Cash", ("2023", "1200000"), ("2022", "900000"))],
                                 scale=1 / 1000))
    assert table[1] == ["Cash", "1200", "900"]


def test_headers_are_localized():
    """The line-item column and the plain Value column both. An untranslated one is half the
    chrome of a two-column file."""
    assert _read(build_rows_csv([_row("现金", ("current", "1"))], locale="zh"))[0] == ["项目", "金额"]


def test_a_localized_basis_heads_the_column():
    """When the basis is part of a heading it has to be in the reader's language too — the one
    export whose headings are its only prose."""
    header = _read(build_rows_csv(
        [_row("现金", ("2023", "1"), ("2023", "2", "standalone"))], locale="zh"))[0]
    assert header == ["项目", "合并 2023", "单独 2023"]


# --- the sample project's own arm ------------------------------------------------------------

def test_the_sample_csv_leaves_structure_out_and_gives_a_column_per_year():
    """Section and sub-head rows are the sheet's STRUCTURE; this file's contract is line items and
    figures. The sample prints two years, so it is three columns."""
    statements = {"balance_sheet": {"label": "Balance Sheet", "rows": [
        {"kind": "section", "label": "ASSETS"},
        {"kind": "item", "label": "Cash", "v1": 1200, "v2": 900},
    ]}}
    table = _read(build_statements_csv(statements))
    assert table == [["Line item", "FY25", "FY24"], ["Cash", "1200", "900"]]


def test_the_sample_export_route_serves_csv_and_not_a_workbook(client):
    """The screen offers ONE Format control for both the real and the sample path. Without its own
    arm the sample POST falls through to the workbook and serves an .xlsx named .csv."""
    r = client.post("/api/v1/projects/demo/export", json={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content[:4] != b"PK\x03\x04", "an xlsx was served under a .csv name"
    assert "filename=spread.csv" in r.headers["content-disposition"]


# --- end to end through the route -------------------------------------------------------------

def _extract(client) -> str:
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("bs.pdf", make_native_pdf(),
                                         "application/pdf")}).json()["id"]
    client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    for _ in range(100):
        r = client.get(f"/api/v1/documents/{doc_id}/run")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            return doc_id
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


def test_the_route_serves_a_named_rectangular_csv_of_the_run(client):
    """The whole path: a real extraction, the format asked for, a file named for the document
    rather than for the format, and a row per extracted line."""
    doc_id = _extract(client)
    r = client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="bs.csv"' in r.headers["content-disposition"]
    table = _read(r.content)
    assert len({len(row) for row in table}) == 1, "the sheet is not rectangular"
    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    assert len(table) - 1 == len(rows), "the file carries a different number of lines from the run"


def test_the_route_carries_every_figure_the_run_extracted(client):
    """No figure is lost between the run and the file. Compared as a MULTISET of the numbers, so
    the test holds whatever the fixture's captions or period labels happen to be."""
    doc_id = _extract(client)
    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    expected = sorted(v["value"] for row in rows for v in (row["values"] or [])
                      if v.get("value") is not None)
    table = _read(client.get(f"/api/v1/documents/{doc_id}/export",
                             params={"fmt": "csv"}).content)[1:]
    got = sorted(cell for row in table for cell in row[1:] if cell)
    assert expected and got == expected


def test_an_unknown_format_is_still_refused(client):
    """The format is a closed set. Widening the pattern to admit `csv` must not admit anything
    else — an unhandled value falls through to the workbook branch and serves the wrong bytes."""
    doc_id = _extract(client)
    assert client.get(f"/api/v1/documents/{doc_id}/export",
                      params={"fmt": "parquet"}).status_code == 422


def test_a_positional_column_gets_the_same_heading_the_workbook_uses():
    """`current` and `prior` are tokens the extractor writes when the filing printed no date it
    could resolve — not words anyone wrote. A file whose headings are its only prose must not stand
    a lowercase internal name in them, and must not disagree with the flat workbook's own columns.
    A resolved period-end date still wins over both (see the test above)."""
    table = _read(build_rows_csv([_row("Cash", ("current", "1200"), ("prior", "900"))]))
    assert table[0] == ["Line item", "Current", "Prior"]
    zh = _read(build_rows_csv([_row("现金", ("current", "1"), ("prior", "2"))], locale="zh"))
    assert zh[0] == ["项目", "本期", "上期"]
