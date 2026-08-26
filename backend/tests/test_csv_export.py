"""The two-column CSV export: the whole extraction on one sheet, line item and value.

The plainest artifact the export module produces, and the one whose readers are other programs —
pasted into a model, loaded by pandas, diffed against last quarter. So the properties worth holding
are not about formatting: they are that the file has exactly two columns, that it does not lose a
figure, that a figure it does carry cannot be mistaken for a different one, and that opening it
does not execute anything.

FOUR THINGS ARE EASY TO GET WRONG HERE and each has a test below.

* A filing prints two years side by side, and often a Group and a Company column as well. Two
  columns have nowhere to put that. Carrying only the first would make this the only export that
  silently drops the comparative year; carrying all of them under an unqualified caption gives a
  consumer "Revenue" twice with two different numbers and no way to tell them apart. So a slot is
  NAMED, and only when the file actually holds more than one.
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
    (period_label, value, basis) — the three fields the slot rule reads."""
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


# --- the shape asked for ---------------------------------------------------------------------

def test_every_row_is_exactly_two_columns():
    """The whole contract of this format. Asserted over EVERY row rather than the header, because
    a stray field on one line is what turns a two-column file into an unparseable one."""
    data = build_rows_csv([_row("Cash", ("current", "1200")),
                           _row("Trade receivables", ("current", "980"))], locale="en")
    table = _read(data)
    assert table[0] == ["Line item", "Value"]
    assert table[1:] == [["Cash", "1200"], ["Trade receivables", "980"]]
    assert {len(r) for r in table} == {2}


def test_a_single_period_filing_gets_bare_captions():
    """The ordinary case comes out as the bare two columns that were asked for — no qualifier
    bolted onto a caption that needs no disambiguating."""
    table = _read(build_rows_csv([_row("Revenue", ("current", "5000"))]))
    assert table[1] == ["Revenue", "5000"]


def test_both_periods_are_carried_and_told_apart():
    """The comparative year is NOT dropped, and the two figures for one caption are
    distinguishable. Either half failing alone is a defect: dropping loses data, and not naming
    hands a consumer two different numbers under one identical key."""
    table = _read(build_rows_csv([_row("Revenue", ("2023", "5000"), ("2022", "4400"))]))
    assert table[1:] == [["Revenue (2023)", "5000"], ["Revenue (2022)", "4400"]]


def test_the_basis_is_named_only_when_the_file_carries_two():
    """A filing printing Group and Company needs the basis in the caption; one printing a single
    basis does not, and adding it would put a word in every caption of every ordinary filing."""
    both = _read(build_rows_csv([_row("Revenue", ("2023", "5000"), ("2023", "3100", "standalone"))]))
    assert both[1:] == [["Revenue (Consolidated 2023)", "5000"],
                        ["Revenue (Standalone 2023)", "3100"]]
    one = _read(build_rows_csv([_row("Revenue", ("2023", "5000"), ("2022", "4400"))]))
    assert one[1] == ["Revenue (2023)", "5000"], "the basis was named with only one basis present"


def test_the_real_period_end_date_is_preferred_over_the_positional_label():
    """`period_label` is positional ("current"); `period_display` is the date the column actually
    printed. A reader of this file has the latter or nothing."""
    rows = [{"source_label": "Revenue",
             "values": [{"period_label": "current", "period_display": "31 December 2023",
                         "value": "5000", "basis": "consolidated"},
                        {"period_label": "prior", "period_display": "31 December 2022",
                         "value": "4400", "basis": "consolidated"}]}]
    table = _read(build_rows_csv(rows))
    assert table[1][0] == "Revenue (31 December 2023)"


def test_a_line_with_no_figure_keeps_its_caption():
    """A line item the extractor read a caption for and no figure is still a line the filing
    prints. Carried with an empty second column, not dropped — dropping is how a reader concludes
    the filing never printed the line."""
    table = _read(build_rows_csv([{"source_label": "Other reserves", "values": []},
                                  _row("Cash", ("current", "1200"))]))
    assert table[1] == ["Other reserves", ""]
    assert table[2] == ["Cash", "1200"]


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
    guard must not fire on the value column, which would turn -450 into the text "'-450"."""
    table = _read(build_rows_csv([_row("Finance costs", ("current", "-450"))]))
    assert table[1] == ["Finance costs", "-450"]


def test_requested_units_convert_the_figures():
    """The unit choice is a conversion of the FIGURES, so it reaches this format even though the
    Include list (which adds analysis SHEETS) cannot."""
    table = _read(build_rows_csv([_row("Cash", ("current", "1200000"))], scale=1 / 1000))
    assert table[1] == ["Cash", "1200"]


def test_headers_are_localized():
    """Two columns, and both of them are headers — an untranslated one is half the file's chrome."""
    assert _read(build_rows_csv([_row("现金", ("current", "1"))], locale="zh"))[0] == ["项目", "金额"]


# --- the sample project's own arm ------------------------------------------------------------

def test_the_sample_csv_leaves_structure_out_and_carries_both_periods():
    """Section and sub-head rows are the sheet's STRUCTURE; this file's contract is line items and
    figures. Both periods are carried, named the way the sample workbook names its columns."""
    statements = {"balance_sheet": {"label": "Balance Sheet", "rows": [
        {"kind": "section", "label": "ASSETS"},
        {"kind": "item", "label": "Cash", "v1": 1200, "v2": 900},
    ]}}
    table = _read(build_statements_csv(statements))
    assert table[1:] == [["Cash (FY25)", "1200"], ["Cash (FY24)", "900"]]
    assert "ASSETS" not in [r[0] for r in table]


def test_the_sample_export_route_serves_csv_and_not_a_workbook(client):
    """The screen offers ONE Format control for both the real and the sample path. Without its own
    arm the sample POST falls through to the workbook and serves an .xlsx named .csv."""
    r = client.post("/api/v1/projects/demo/export", json={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content[:4] != b"PK\x03\x04", "an xlsx was served under a .csv name"
    assert 'filename=spread.csv' in r.headers["content-disposition"]


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


def test_the_route_serves_a_named_csv_of_the_run(client):
    """The whole path: a real extraction, the format asked for, and a file named for the document
    rather than for the format."""
    doc_id = _extract(client)
    r = client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="bs.csv"' in r.headers["content-disposition"]
    table = _read(r.content)
    assert {len(row) for row in table} == {2}
    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    assert len(table) - 1 == sum(max(1, len(row["values"] or [])) for row in rows), \
        "the file carries a different number of figures from the run"


def test_the_route_carries_every_figure_the_run_extracted(client):
    """No figure is lost between the run and the file. Compared as a MULTISET of the numbers, so
    the test holds whatever the fixture's captions or period labels happen to be."""
    doc_id = _extract(client)
    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    expected = sorted(v["value"] for row in rows for v in (row["values"] or [])
                      if v.get("value") is not None)
    got = sorted(r[1] for r in _read(client.get(f"/api/v1/documents/{doc_id}/export",
                                                params={"fmt": "csv"}).content)[1:] if r[1])
    assert expected and got == expected


def test_an_unknown_format_is_still_refused(client):
    """The format is a closed set. Widening the pattern to admit `csv` must not admit anything
    else — an unhandled value falls through to the workbook branch and serves the wrong bytes."""
    doc_id = _extract(client)
    assert client.get(f"/api/v1/documents/{doc_id}/export",
                      params={"fmt": "parquet"}).status_code == 422
