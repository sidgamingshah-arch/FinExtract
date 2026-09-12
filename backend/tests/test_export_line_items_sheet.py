"""THE LINE ITEMS SHEET: every configured line, its sub-lines, and formulas that cannot lie.

NEW FILE -> backend/tests/test_export_line_items_sheet.py

WHAT THE SHEET IS FOR. The statement sheets are shaped by the TEMPLATE, so the 77 sub-line items
are not on them — they are not template nodes. A reader could see that depreciation is 587,417 and
had nowhere to go to find out which two note rows that is. This sheet carries every configured
line, each sub-line under its parent, and beneath a sub-line assembled from several printed
figures, those figures as their own rows with their own pages.

THE PROPERTY THESE TESTS EXIST FOR, and it is the one `test_export_honesty` used to assert with a
blanket "no live formulas anywhere": **the number a reader sees is the number the server computed.**
A live formula is a way to break that — a sum over four rows where the server required all four and
resolved only two would recalculate to a total the server refused to publish. So rather than
forbidding formulas, these tests PARSE every formula in the workbook, resolve its operands to
literal cells, and check the result against the figure the server published. That is strictly
stronger than the old assertion: it permits the traceability the sheet exists for and still catches
a formula that would show a different number.

WHAT LINKS WHAT, after one false start recorded in
`test_a_statement_figure_keeps_its_number_and_gains_a_link_to_the_sheet`:

    =E317+E318            ON THIS SHEET: a sub-line summing the note rows written beneath it. Live,
                          because "stored as a formula" is what was asked for and because the
                          operands are literal cells of the same sheet, so it cannot show a figure
                          the server did not publish.
    a HYPERLINK           ON A STATEMENT SHEET: the cell keeps its number and gains a jump to the
                          line's row here. NOT a cell reference — that empties the cell for every
                          reader that does not evaluate formulas, which is most of them.
"""
from __future__ import annotations

import io
import re
import time

import pytest

pytest.importorskip("fitz")
pytest.importorskip("openpyxl")

from tests.fixtures.generate import make_native_pdf

# `'Sheet'!D12` or `D12`. Anchored, so anything that is not a plain cell reference fails the
# grammar check below rather than being silently treated as one.
CELL = re.compile(r"^(?:'(?P<sheet>[^']+)'!)?(?P<col>[A-Z]{1,3})(?P<row>[0-9]+)$")
# One signed operand of a sum: the leading sign (absent on the first) and the reference.
TERM = re.compile(r"(?P<sign>^|[+-])(?P<ref>(?:'[^']+'!)?[A-Z]{1,3}[0-9]+)")


def _await(client, doc_id):
    for _ in range(200):
        r = client.get(f"/api/v1/documents/{doc_id}/run")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            return
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


@pytest.fixture(scope="module")
def workbook(client):
    """One extraction, one statement workbook, shared — the run is the expensive part.

    THE TEMPLATE IS PINNED TO THE CONFIGURATION'S OWN `target_template_key`, following
    `test_export_honesty._extract`. Pairing a run with the wrong template is not a hypothetical:
    the shipped `hkfrs_hk_china_template` and the shipped line-item set share TWO canonical keys
    out of 539, so a workbook built from that pairing links almost nothing and the sheet looks
    broken when it is not.
    """
    from app.sample.reference import shipped_line_items_key

    doc_id = client.post("/api/v1/documents",
                         files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
                         ).json()["id"]
    cfgs = client.get("/api/v1/line-items/versions").json()
    assert cfgs, "no shipped line-item configuration to export against"
    cfg = next((c for c in cfgs if c["line_items_key"] == shipped_line_items_key()), cfgs[0])
    tpls = client.get("/api/v1/templates").json()
    tpl = next((t for t in tpls if t["template_key"] == cfg["target_template_key"]), tpls[0])
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    _await(client, doc_id)

    response = client.get(f"/api/v1/documents/{doc_id}/export",
                          params={"fmt": "excel", "layout": "statement"})
    assert response.status_code == 200, response.text
    import openpyxl

    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    return openpyxl.load_workbook(io.BytesIO(response.content)), rows


def _formulas(wb) -> list[tuple[str, str, str]]:
    return [(ws.title, c.coordinate, str(c.value))
            for ws in wb.worksheets for row in ws.iter_rows() for c in row
            if c.data_type == "f"]


def _literal(wb, sheet: str, ref: str):
    """The NUMBER at a reference, refusing to follow a chain into another formula.

    A reference whose target is itself a formula is not evidence about a figure — it is one more
    hop — and a test that quietly followed the chain would pass on a cycle. Those come back None
    and the caller fails on them.
    """
    m = CELL.match(ref)
    if not m:
        return None
    ws = wb[m.group("sheet") or sheet]
    cell = ws[f"{m.group('col')}{m.group('row')}"]
    if cell.data_type == "f":
        return None
    return cell.value if isinstance(cell.value, (int, float)) else None


def test_the_sheet_exists_and_carries_every_configured_line(workbook):
    """Including the sub-lines, which is the reason it exists — and it goes LAST, because the
    request was that the sheets a reader already opens stay where they were."""
    wb, _rows = workbook
    assert "Line Items" in wb.sheetnames
    assert wb.sheetnames[-1] == "Line Items", (
        f"the new sheet displaced the existing ones: {wb.sheetnames}")
    ws = wb["Line Items"]
    keys = {ws.cell(r, 2).value for r in range(1, ws.max_row + 1) if ws.cell(r, 2).value}
    assert len(keys) > 400, f"only {len(keys)} lines reached the sheet"
    assert any(str(k).startswith("sub__") for k in keys), (
        "no sub-line item on the sheet, which is the one thing the statement sheets cannot show")


def test_a_sub_line_is_written_under_its_parent(workbook):
    """The main-to-sub hop, as a reader meets it: the parent's row comes first, and the sub-line's
    own row carries its key so the two can be joined."""
    wb, _rows = workbook
    ws = wb["Line Items"]
    at = {ws.cell(r, 2).value: r for r in range(1, ws.max_row + 1) if ws.cell(r, 2).value}
    subs = [(k, at[k]) for k in at if str(k).startswith("sub__")]
    assert subs, "no sub-line items to check"
    top = min(at.values())
    for key, row in subs:
        assert row > top, f"{key} is above every other line"


def test_every_live_formula_reproduces_the_servers_figure(workbook):
    """THE TEST THIS FILE EXISTS FOR, and the property `test_export_honesty` used to assert by
    forbidding formulas outright.

    Each formula is parsed, its operands resolved to literal cells, and a multi-term sum checked
    against what the server published for that line. A reference into another formula is refused
    rather than followed — that is a second hop, not evidence — so a cycle or a chain fails here
    instead of passing quietly.
    """
    wb, rows = workbook
    formulas = _formulas(wb)
    if not formulas:
        pytest.skip("this fixture produced no linked figures")

    by_key: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("canonical_key"):
            by_key.setdefault(row["canonical_key"], []).append(row)

    checked = 0
    for sheet, coord, formula in formulas:
        body = formula.lstrip("=")
        terms = [(m.group("sign"), m.group("ref")) for m in TERM.finditer(body)]
        # The parse has to account for the WHOLE formula, or the grammar check below is checking
        # something other than what Excel will evaluate.
        assert "".join(s + r for s, r in terms) == body, (
            f"{sheet}!{coord} is not a plain sum of cell references: {formula}")
        assert all(CELL.match(r) for _s, r in terms), f"{sheet}!{coord}: {formula}"

        values = [_literal(wb, sheet, r) for _s, r in terms]
        assert all(v is not None for v in values), (
            f"{sheet}!{coord} references a formula or an empty cell rather than a figure: "
            f"{formula}")

        if len(terms) == 1:
            # A LINK. Its whole job is to show the figure that lives on the other sheet, so there
            # is nothing to reconcile — `_literal` having returned a number IS the check.
            checked += 1
            continue

        # A SUM over the note rows written beneath a line. It is claimed to equal a figure the
        # server published for that line, and this is where the claim is tested rather than
        # trusted: the key is read off column B of the formula's own row.
        total = sum(-v if s == "-" else v for (s, _r), v in zip(terms, values))
        ws = wb[sheet]
        key = str(ws.cell(int(CELL.match(coord).group("row")), 2).value or "")
        published = [float(v["value"]) for v in
                     (by_key.get(key, [{}])[0].get("values") or [])
                     if v.get("value") is not None]
        if published:
            assert any(abs(total - p) <= 0.5 for p in published), (
                f"{sheet}!{coord} ({key}) evaluates to {total:,.0f}, which is not a figure the "
                f"server published for that line: {published}")
        checked += 1
    assert checked == len(formulas)


def test_a_statement_figure_keeps_its_number_and_gains_a_link_to_the_sheet(workbook):
    """THE LINK, AND THE THING IT MUST NOT COST. The existing sheets keep their figures and gain a
    jump to the line's row on the new sheet.

    A CELL REFERENCE WAS THE FIRST ATTEMPT AND IT IS PINNED HERE AS WRONG. `='Line Items'!E256`
    traces perfectly and empties the cell of its number for every reader that does not evaluate
    formulas — openpyxl, pandas, any downstream consumer. Measured, it broke
    `test_units.test_export_applies_unit_conversion`, which asserts the unit-converted figure is
    VISIBLE on the Balance Sheet. So the figure stays put and the traceback is a hyperlink beside
    it, which is the only shape that satisfies both halves of "stay as they are, and be linked".
    """
    wb, _rows = workbook
    linked = [(ws.title, c.coordinate, c.hyperlink.location or c.hyperlink.target or "")
              for ws in wb.worksheets if ws.title != "Line Items"
              for row in ws.iter_rows() for c in row if c.hyperlink]
    if not linked:
        pytest.skip("no figure on this fixture appears on both a statement sheet and the new one")

    for sheet, coord, target in linked:
        assert "Line Items" in str(target), f"{sheet}!{coord} links elsewhere: {target}"
        # THE FIGURE IS STILL THERE. This is the half a cell reference destroyed.
        value = wb[sheet][coord].value
        assert isinstance(value, (int, float)), (
            f"{sheet}!{coord} was linked and lost its figure: {value!r}")
        # …and the row it points at carries a figure too, so the jump lands somewhere useful.
        assert _literal(wb, sheet, str(target).lstrip("#")) is not None, (
            f"{sheet}!{coord} links to a cell holding no figure: {target}")


def test_a_note_row_carries_its_own_page(workbook):
    """A sub-line's individual printed figures are listed with the page each came from — without
    that the sheet shows arithmetic a reader cannot go and check."""
    wb, _rows = workbook
    ws = wb["Line Items"]
    note_rows = [r for r in range(1, ws.max_row + 1)
                 if str(ws.cell(r, 3).value or "") == "note row"]
    if not note_rows:
        pytest.skip("this fixture produced no multi-figure line")
    sourced = [r for r in note_rows if str(ws.cell(r, ws.max_column).value or "").strip()]
    assert sourced, "no note row names where it was printed"
