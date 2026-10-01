"""The workbook importer refuses what it cannot read, and does not flatten what it cannot say.

Each case here used to be ACCEPTED: the importer built a schema-valid template that said something
the author never wrote, and neither the Pydantic model nor ``unknown_keys`` nor
``validate_template`` could notice, because the dict was well-formed. The two families:

* a sheet that cannot be read faithfully — a merged or duplicated header, a missing column, a
  merged data cell, an Identities sheet read by position. Every column has a default for a blank
  cell, so each of these published every affected row at its default;
* a template that says more than the sheet has columns for — residual rollups, KPI ratios,
  statement headings, statements outside the sheet's vocabulary. Rebuilt from the sheet alone, a
  residual became a plain sum (29 false structural failures across the five reference filings,
  two of them on HKEX filings that otherwise show none) and every KPI ratio disappeared.

Every refusal has to be one an administrator can act on, so each test also pins that the message
names the sheet, the cell or column, and what to do.
"""
from __future__ import annotations

import copy
import io
import json
import pathlib

import pytest

pytest.importorskip("openpyxl")

from app.schemas.loader import load_template, unknown_keys, validate_template  # noqa: E402
from app.services.template_xlsx import (  # noqa: E402
    COLUMNS, IDENTITY_COLUMNS, KIND_CALCULATED, KIND_EXTRACTED, KIND_HEADING, TemplateSheetError,
    build_template_xlsx, import_workbook, parse_template_xlsx, workbook_source_key)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app/sample/templates"
_FACE = ("balance_sheet", "profit_and_loss", "cash_flow")


def _load(name: str) -> dict:
    return json.loads((_TEMPLATES / name).read_text(encoding="utf-8"))


def _hk_face() -> dict:
    """The HK template cut to the statements a workbook can hold — every residual rollup, the
    child-less ones included, and the KPI block, all still in it."""
    d = _load("output_csv_hk_v1_template.json")
    d["statements"] = [s for s in d["statements"] if s["type"] in _FACE]
    return d


def _row(statement="Balance sheet", section="", node="", key="", label="", role="line",
         kind=KIND_EXTRACTED, op="", children="", sign="natural") -> dict:
    return {"statement": statement, "section": section, "node_id": node, "canonical_key": key,
            "label": label, "role": role, "kind": kind, "op": op, "children": children,
            "sign": sign}


_ROWS = [
    _row(node="sec", key="sec", label="Current assets", role="header", kind=KIND_HEADING),
    _row(section="sec", key="cash", label="Cash"),
    _row(section="sec", key="recv", label="Receivables", sign="natural_negative"),
    _row(section="sec", key="tot", label="Total", role="subtotal", kind=KIND_CALCULATED,
         op="sum", children="cash\nrecv"),
]


def _book(rows=_ROWS, columns=COLUMNS, identities=None, edit=None) -> bytes:
    """A small workbook in the downloaded shape; ``edit(wb)`` breaks it the way a test needs."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.append([h for _k, h in columns])
    for r in rows:
        ws.append([r.get(k, "") for k, _h in columns])
    if identities is not None:
        ids = wb.create_sheet("Identities")
        ids.append([h for _k, h in IDENTITY_COLUMNS])
        for r in identities:
            ids.append(r)
    if edit:
        edit(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse(book: bytes, previous=None) -> dict:
    return parse_template_xlsx(book, template_key="t", name="T", previous=previous)


def _published(definition: dict):
    """The definition through the same three gates the upload route runs."""
    t = load_template(definition)
    assert unknown_keys(definition, t) == [] and validate_template(t) == []
    return t


def test_the_small_book_reads_when_nothing_is_broken():
    d = _parse(_book())
    sec = d["statements"][0]["sections"][0]
    assert [c["canonical_key"] for c in sec["children"]] == ["cash", "recv", "tot"]
    assert sec["children"][1]["sign"] == "natural_negative"
    _published(d)


# --- the header row -----------------------------------------------------------------------------

def test_a_merged_header_is_refused_with_the_cells_named():
    def merge(wb):
        wb["Template"].merge_cells("A1:B1")              # Statement over Section

    with pytest.raises(TemplateSheetError, match=r"Template sheet, header row: cells A1:B1 are "
                                                 r"merged\. Unmerge them"):
        _parse(_book(edit=merge))


def test_a_recognised_header_seen_twice_is_refused_not_resolved_to_the_last():
    # The probe that was accepted: a blank duplicate 'Sign' at the end turned every natural_negative line natural.
    def dup(wb):
        ws = wb["Template"]
        ws.cell(1, ws.max_column + 1).value = "Sign"

    with pytest.raises(TemplateSheetError) as exc:
        _parse(_book(edit=dup))
    msg = str(exc.value)
    assert "'Sign' appears twice (columns M and O)" in msg and "Delete or rename one" in msg


@pytest.mark.parametrize("dropped", ["Section", "Role", "Sign", "Label (zh)", "Expects note",
                                     "Node ID", "Calculation", "Calculated from"])
def test_every_column_of_ours_is_required(dropped):
    """Section, Role and Sign used to fall back to defaults: no Section flattened the tree into
    top-level sections, no Role erased every subtotal, no Sign erased every
    natural_negative."""
    cols = [(k, h) for k, h in COLUMNS if h != dropped]
    with pytest.raises(TemplateSheetError) as exc:
        _parse(_book(columns=cols))
    msg = str(exc.value)
    assert msg.startswith(f"Template sheet, header row: missing column(s) '{dropped}'.")
    assert "Restore it with the header spelled as downloaded" in msg


def test_the_legacy_required_column_and_the_authors_own_columns_are_still_ignored():
    cols = [("x", "My notes"), *COLUMNS, ("y", "Required")]
    d = _parse(_book(columns=cols))
    assert [c["canonical_key"] for c in d["statements"][0]["sections"][0]["children"]] == [
        "cash", "recv", "tot"]


# --- merged data cells ---------------------------------------------------------------------------

@pytest.mark.parametrize("cells, column", [("A2:A5", "Statement"), ("B3:B5", "Section")])
def test_merged_statement_or_section_cells_are_refused(cells, column):
    """Merged Section cells over a block of lines used to promote all but the first of them to
    top-level sections; merged Statement cells were refused, but as "Statement is required"."""
    def merge(wb):
        wb["Template"].merge_cells(cells)

    with pytest.raises(TemplateSheetError) as exc:
        _parse(_book(edit=merge))
    msg = str(exc.value)
    assert f"Template sheet, cells {cells}: merged across the '{column}' column." in msg
    assert "type the value into every row" in msg


def test_a_merge_confined_to_an_authors_own_column_is_harmless():
    cols = [*COLUMNS, ("x", "My notes")]

    def merge(wb):
        wb["Template"].merge_cells("O2:O4")

    _parse(_book(columns=cols, edit=merge))


# --- the Identities sheet -----------------------------------------------------------------------

_IDENT = ["Balance sheet", "bs_tie", "tot", "sum", "cash\nrecv", 2.0, 0.01]


def test_identities_are_read_by_header_so_reordered_columns_keep_their_values():
    """Read by position, swapping the two tolerance columns moved 0.1% into the absolute slot and
    100% into the relative one, and the identity could no longer fail."""
    def swap(wb):
        ws = wb["Identities"]
        for r in (1, 2):
            a, b = ws.cell(r, 6).value, ws.cell(r, 7).value
            ws.cell(r, 6).value, ws.cell(r, 7).value = b, a

    d = _parse(_book(identities=[_IDENT], edit=swap))
    ident = d["statements"][0]["identities"][0]
    assert (ident["tolerance_abs"], ident["tolerance_rel"]) == (2.0, 0.01)
    assert ident["rhs"] == {"op": "sum", "children": ["cash", "recv"]}
    _published(d)


def test_the_identities_sheet_gets_the_same_header_gates():
    def drop(wb):
        wb["Identities"].delete_cols(7)

    with pytest.raises(TemplateSheetError,
                       match=r"Identities sheet, header row: missing column\(s\) "
                             r"'Tolerance \(rel\)'"):
        _parse(_book(identities=[_IDENT], edit=drop))

    def merge(wb):
        wb["Identities"].merge_cells("C2:C3")

    with pytest.raises(TemplateSheetError, match=r"Identities sheet, cells C2:C3: merged across "
                                                 r"the 'Left \(canonical key\)' column"):
        _parse(_book(identities=[_IDENT, _IDENT[:1] + ["bs_tie2", None] + _IDENT[3:]],
                     edit=merge))


def test_a_tolerance_that_is_not_a_number_is_refused_not_defaulted():
    bad = _IDENT[:5] + ["one", 0.01]
    with pytest.raises(TemplateSheetError, match=r"Identities row 2: Tolerance \(abs\) must be a "
                                                 r"number of zero or more, not 'one'"):
        _parse(_book(identities=[bad]))


def test_an_identity_op_outside_the_schema_is_refused_on_its_row():
    with pytest.raises(TemplateSheetError, match=r"Identities row 2: Calculation must be one of "
                                                 r"diff, sum, not 'weighted_sum'"):
        _parse(_book(identities=[_IDENT[:3] + ["weighted_sum"] + _IDENT[4:]]))


def test_a_deleted_identities_sheet_does_not_silently_drop_the_identities():
    previous = _parse(_book(identities=[_IDENT]))
    with pytest.raises(TemplateSheetError) as exc:
        _parse(_book(), previous=previous)
    assert "no 'Identities' sheet" in str(exc.value) and "bs_tie" in str(exc.value)
    assert "keep the sheet with only its header row" in str(exc.value)
    # …and keeping the sheet with only its header row is the deliberate way to remove them.
    d = _parse(_book(identities=[]), previous=previous)
    assert d["statements"][0]["identities"] == []


# --- what the sheet cannot say ------------------------------------------------------------------

def _with_residual() -> dict:
    """The small book's template with ``tot`` turned into a residual against a reported total."""
    rows = [*_ROWS, _row(section="sec", key="reported", label="Reported total")]
    d = _parse(_book(rows=rows))
    tot = next(c for c in d["statements"][0]["sections"][0]["children"]
               if c["canonical_key"] == "tot")
    tot["rollup"].update({"reported_total_key": "reported", "reported_total_op": "diff"})
    d["kpis"] = {"intermediates": [], "ratios": [{
        "key": "kpi_cash_ratio", "label": "Cash ratio", "category": "Liquidity", "unit": "x",
        "numerator": [{"key": "cash"}], "denominator": [{"key": "reported"}]}]}
    d["statements"][0]["label_i18n"] = {"en": "Statement of financial position"}
    _published(d)
    return d, rows


def test_an_unchanged_residual_the_kpis_and_the_heading_are_carried_and_named():
    previous, rows = _with_residual()
    d, carried = import_workbook(_book(rows=rows), template_key="t", name="T", previous=previous)
    assert d == previous                       # nothing the sheet cannot hold was lost
    assert carried == ["the reported-total and magnitude fields of 1 rollup(s)",
                       "heading(s) of 1 statement(s)", "1 KPI ratio(s) and 0 KPI intermediate(s)"]
    _published(d)


def test_without_a_previous_version_the_sheet_alone_cannot_carry_a_residual():
    """Why carrying is needed at all: the same workbook read on its own is a plain sum."""
    previous, rows = _with_residual()
    d = _parse(_book(rows=rows))
    tot = d["statements"][0]["sections"][0]["children"][2]
    assert tot["rollup"] == {"op": "sum", "children": ["cash", "recv"]} != (
        previous["statements"][0]["sections"][0]["children"][2]["rollup"])


def test_an_edit_that_would_turn_a_residual_into_a_plain_sum_is_refused_on_its_row():
    previous, rows = _with_residual()
    edited = copy.deepcopy(rows)
    edited[3]["children"] = "cash"                    # row 5 of the sheet
    with pytest.raises(TemplateSheetError) as exc:
        _parse(_book(rows=edited), previous=previous)
    msg = str(exc.value)
    assert msg.startswith("Row 5: 'tot' is a residual line in 't' (checked against the reported "
                          "total 'reported'; combined with its reported total by diff)")
    assert "Restore Kind 'calculated', Calculation 'sum' and 'Calculated from' cash, recv" in msg
    assert "or make this edit in the JSON template" in msg

    demoted = copy.deepcopy(rows)
    demoted[3].update(kind=KIND_EXTRACTED, op="", children="")
    with pytest.raises(TemplateSheetError, match="Row 5: 'tot' is a residual line"):
        _parse(_book(rows=demoted), previous=previous)


def test_deleting_a_residuals_reported_total_or_a_kpis_line_is_refused():
    previous, rows = _with_residual()
    with pytest.raises(TemplateSheetError, match="Row 5: 'tot' is checked against the reported "
                                                 "total 'reported', which is no longer in the "
                                                 "Template sheet"):
        _parse(_book(rows=rows[:-1]), previous=previous)

    previous["statements"][0]["sections"][0]["children"][2]["rollup"].pop("reported_total_key")
    previous["statements"][0]["sections"][0]["children"][2]["rollup"].pop("reported_total_op")
    with pytest.raises(TemplateSheetError, match="has KPI\\(s\\) built on line\\(s\\) this "
                                                 "workbook no longer has: kpi_cash_ratio uses "
                                                 "reported"):
        _parse(_book(rows=rows[:-1]), previous=previous)


def test_a_template_with_statements_the_sheet_cannot_hold_is_refused_and_says_so_in_the_file():
    hk = _load("output_csv_hk_v1_template.json")
    book = build_template_xlsx(hk)
    with pytest.raises(TemplateSheetError) as exc:
        parse_template_xlsx(book, template_key=hk["template_key"], name="x", previous=hk)
    msg = str(exc.value)
    assert "statement_setup, covenants_supplemental, notes" in msg
    assert "Edit this template as JSON" in msg
    # The downloaded file says it before anyone edits it.
    import openpyxl

    readme = [r for r in openpyxl.load_workbook(io.BytesIO(book))["Read me"].iter_rows(
        values_only=True)]
    assert any(a == "CANNOT BE UPLOADED BACK" for a, _b in readme)
    assert workbook_source_key(book) == "output_csv_hk_v1"


# --- round trips --------------------------------------------------------------------------------

def test_the_hk_face_statements_round_trip_without_losing_a_residual_or_a_kpi():
    """34 reported totals, the 4 child-less residuals, 3 magnitude conventions, 15 KPI ratios and
    the statement headings all come back — through the same gates the upload route runs."""
    face = _hk_face()
    book = build_template_xlsx(face)
    back, carried = import_workbook(book, template_key=face["template_key"], name=face["name"],
                                    previous=face)
    assert _published(back).model_dump() == load_template(face).model_dump()
    assert carried[-1] == "15 KPI ratio(s) and 0 KPI intermediate(s)"


def test_the_shipped_hkfrs_workbook_round_trips_onto_itself_with_nothing_to_carry():
    tpl = _load("hkfrs_hk_china_template.json")
    back, carried = import_workbook(build_template_xlsx(tpl), template_key=tpl["template_key"],
                                    name=tpl["name"], previous=tpl)
    assert carried == []
    assert _published(back).model_dump() == load_template(tpl).model_dump()


# --- over HTTP ----------------------------------------------------------------------------------

def test_the_upload_route_carries_what_the_sheet_cannot_say_and_reports_it(client):
    face = _hk_face()
    face["template_key"] = "wb_import_gate_probe"
    face["name"] = "Workbook import gate probe"
    made = client.post("/api/v1/templates", json={"definition": face})
    assert made.status_code == 201, made.text
    book = client.get(f"/api/v1/templates/{made.json()['id']}/xlsx").content

    # Onto its own key, unchanged: a new version that kept every residual and KPI, and says so.
    up = client.post("/api/v1/templates/xlsx", files={"file": ("t.xlsx", book, _XLSX)},
                     data={"template_key": face["template_key"], "name": face["name"]})
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["carried_forward"]["from"] == {"template_key": face["template_key"],
                                               "version": made.json()["version"]}
    assert "15 KPI ratio(s) and 0 KPI intermediate(s)" in body["carried_forward"]["kept"]
    stored = client.get(f"/api/v1/templates/{body['id']}").json()["definition"]
    assert load_template(stored).model_dump() == load_template(face).model_dump()

    # Under a NEW key, the workbook's own Read me names its source, so nothing is dropped there
    # either — the fork keeps the source's residuals.
    fork = client.post("/api/v1/templates/xlsx", files={"file": ("fork.xlsx", book, _XLSX)},
                       data={"template_key": "wb_import_gate_fork", "name": "Fork"})
    assert fork.status_code == 201, fork.text
    assert fork.json()["carried_forward"]["from"]["template_key"] == face["template_key"]


def test_the_upload_route_refuses_the_full_hk_template_with_a_reason(client):
    tpl = next(t for t in client.get("/api/v1/templates").json()
               if t["template_key"] == "output_csv_hk_v1" and t["is_latest"])
    book = client.get(f"/api/v1/templates/{tpl['id']}/xlsx").content
    for key in (tpl["template_key"], ""):        # onto its own key, and as a new one
        r = client.post("/api/v1/templates/xlsx", files={"file": ("hk.xlsx", book, _XLSX)},
                        data={"template_key": key, "name": "HK copy"})
        assert r.status_code == 422
        assert "cannot hold: statement_setup" in r.json()["detail"]


def test_a_sign_the_schema_does_not_know_is_refused_on_its_row():
    """The Read me used to offer 'contra', which no schema value spells; the publish gate then
    refused it with an enum error that named no row."""
    rows = [*_ROWS[:2], {**_ROWS[2], "sign": "contra"}, _ROWS[3]]
    with pytest.raises(TemplateSheetError, match="Row 4: Sign must be one of natural, "
                                                 "natural_positive, natural_negative"):
        _parse(_book(rows=rows))
