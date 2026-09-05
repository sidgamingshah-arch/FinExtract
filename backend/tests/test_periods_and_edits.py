"""Four defects an analyst hits in the Workspace grid, each pinned by the behaviour it broke.

1. A line printed for the PRIOR year only had its figure reported as the current year's.
2. A typed value did not become the value on screen — the wrong basis, a 404, or a re-sum.
3. Last year's figures carried no source location, so only half the grid was traceable.
4. Extracted figures that reach no face statement, and the KPIs computed off the statements,
   had nowhere on the front end to appear.
"""
from __future__ import annotations

from app.api.routes.documents import _build_statement, _period_labels, _row_value
from app.services.periods import concept_value, split_current_prior


def _v(period, value, *, basis="consolidated", page=3, x0=0.6, display=None):
    return {"basis": basis, "period_label": period, "value": str(value),
            "period_display": display,
            "provenance": {"source_kind": "native", "page_index": page,
                           "bbox": {"x0": x0, "y0": 0.2, "x1": x0 + 0.05, "y1": 0.21}}}


# --------------------------------------------------------------------------------------
# 1. A prior-only figure must NOT be reported as the current year
# --------------------------------------------------------------------------------------
def test_a_prior_only_row_leaves_the_current_period_empty():
    # "Pledged deposits" was released during the year: last year has a figure, this year has
    # none. Taking the first value positionally reported last year's number as this year's.
    cur, prior = split_current_prior([_v("prior", 2_031_012)])
    assert cur is None
    assert prior is not None and prior["value"] == "2031012"


def test_a_current_only_row_leaves_the_prior_period_empty():
    cur, prior = split_current_prior([_v("current", 500)])
    assert prior is None
    assert cur["value"] == "500"


def test_unnamed_columns_still_fall_back_to_the_printed_order():
    # A page with no columnar structure labels its figures col0/col1 — there, position is the
    # only signal we have, so the fallback must stay.
    cur, prior = split_current_prior([_v("col0", 10), _v("col1", 20)])
    assert (cur["value"], prior["value"]) == ("10", "20")


def test_a_prior_only_row_does_not_leak_into_the_statement_grid():
    rows = [{"source_label": "Pledged deposits", "canonical_key": "bs_current_assets__pledged",
             "values": [_v("prior", 2_031_012)]}]
    d = _build_statement(rows, None, "balance_sheet", "f.pdf")
    row = next(r for r in d["rows"] if r["id"] == "bs_current_assets__pledged")
    assert row["v1"] is None
    assert row["v2"] == 2_031_012


def test_the_column_headers_are_read_from_the_period_each_value_names():
    # The first row carries only a prior figure. Taking the headers positionally from it made
    # BOTH columns read as the prior period.
    rows = [
        {"source_label": "Pledged deposits", "canonical_key": "bs_a",
         "values": [_v("prior", 1, display="31 December 2022")]},
        {"source_label": "Cash", "canonical_key": "bs_b",
         "values": [_v("current", 2, display="31 December 2023"),
                    _v("prior", 3, display="31 December 2022")]},
    ]
    assert _period_labels(rows, "consolidated", "en") == ["31 December 2023", "31 December 2022"]


def test_duplicate_printed_headers_fall_back_to_current_and_prior():
    rows = [{"source_label": "Other intangible assets", "canonical_key": "bs_note",
             "values": [_v("current", 1, display="At 1 August 2023"),
                        _v("prior", 2, display="At 1 August 2023")]}]
    assert _period_labels(rows, "consolidated", "en") == ["Current", "Prior"]


# --------------------------------------------------------------------------------------
# 2. A typed value becomes the value on screen
# --------------------------------------------------------------------------------------
def test_a_manual_value_replaces_a_combined_figure_instead_of_joining_it():
    # Three printed lines map to one concept, so the grid shows their sum (150). Typing 200 has
    # to show 200 — the old behaviour added the edit to the others and showed 350.
    group = [
        {"canonical_key": "cf_others", "values": [_v("current", 100)],
         "edited": True, "edited_slots": ["consolidated/current"]},
        {"canonical_key": "cf_others", "values": [_v("current", 30)]},
        {"canonical_key": "cf_others", "values": [_v("current", 20)]},
    ]
    group[0]["values"][0]["value"] = "200"
    assert concept_value(group, "consolidated", "current") == 200


def test_unedited_lines_that_share_a_concept_are_still_summed():
    group = [{"canonical_key": "k", "values": [_v("current", 100)]},
             {"canonical_key": "k", "values": [_v("current", 30)]}]
    assert concept_value(group, "consolidated", "current") == 130


def test_an_edit_to_one_basis_does_not_claim_the_other():
    group = [{"canonical_key": "k", "edited": True, "edited_slots": ["standalone/current"],
              "values": [_v("current", 7, basis="consolidated"),
                         _v("current", 9, basis="standalone")]},
             {"canonical_key": "k", "values": [_v("current", 5, basis="consolidated")]}]
    # Standalone was typed → that figure stands alone. Consolidated was not → still summed.
    assert concept_value(group, "standalone", "current") == 9
    assert concept_value(group, "consolidated", "current") == 12


def test_the_accounting_checks_read_the_same_figure_the_grid_shows():
    # _row_value used to return the FIRST matching row, while the grid summed them — so the
    # balance identity validated a number nobody was shown.
    rows = [{"canonical_key": "bs_total_assets", "values": [_v("current", 100)]},
            {"canonical_key": "bs_total_assets", "values": [_v("current", 40)]}]
    assert _row_value(rows, "bs_total_assets") == 140


# --------------------------------------------------------------------------------------
# 3. Both periods are traceable to the page they were printed on
# --------------------------------------------------------------------------------------
def test_both_periods_carry_their_own_source_location():
    rows = [{"source_label": "Cash", "canonical_key": "bs_cash",
             "values": [_v("current", 10, page=3, x0=0.6), _v("prior", 20, page=7, x0=0.8)]}]
    d = _build_statement(rows, None, "balance_sheet", "f.pdf")
    row = next(r for r in d["rows"] if r["id"] == "bs_cash")
    assert row["source"]["page_index"] == 3
    assert row["source2"]["page_index"] == 7


def test_each_contributing_line_carries_both_of_its_source_locations():
    rows = [{"source_label": "Line A", "canonical_key": "bs_o",
             "values": [_v("current", 10, page=3), _v("prior", 11, page=9)]},
            {"source_label": "Line B", "canonical_key": "bs_o",
             "values": [_v("current", 20, page=4), _v("prior", 21, page=10)]}]
    d = _build_statement(rows, None, "balance_sheet", "f.pdf")
    row = next(r for r in d["rows"] if r["id"] == "bs_o")
    assert [c["source"]["page_index"] for c in row["contributions"]] == [3, 4]
    assert [c["source2"]["page_index"] for c in row["contributions"]] == [9, 10]
    assert [c["src2"] for c in row["contributions"]] == ["p.10", "p.11"]


# --------------------------------------------------------------------------------------
# 4. KPI and Additional-items views
# --------------------------------------------------------------------------------------
def _ratio_rows():
    return [
        {"source_label": "Total current assets",
         "canonical_key": "bs_current_assets__total_current_assets",
         "values": [_v("current", 300), _v("prior", 200)]},
        {"source_label": "Total current liabilities",
         "canonical_key": "bs_current_liabilities__total_current_liabilities",
         "values": [_v("current", 150, page=5), _v("prior", 200, page=11)]},
    ]


def test_the_kpi_view_computes_both_periods_and_labels_its_own_units():
    d = _build_statement(_ratio_rows(), None, "kpi", "f.pdf")
    row = next(r for r in d["rows"] if r["id"] == "kpi_current_ratio")
    assert (row["v1"], row["v2"]) == (2.0, 1.0)
    assert row["display1"] == "2.0×" and row["display2"] == "1.0×"
    # Nothing for the currency/magnitude selectors to scale — a ratio is not an amount.
    assert d["units_scale_factor"] == 1.0 and d["currency"] == "" and d["units"] == ""
    assert d["presentation"] == "raw"
    # Derived: the fix for a wrong KPI is to fix the line items it came from.
    assert row["editable"] is False


def test_a_kpi_lists_its_inputs_with_the_page_each_was_printed_on():
    d = _build_statement(_ratio_rows(), None, "kpi", "f.pdf")
    row = next(r for r in d["rows"] if r["id"] == "kpi_current_ratio")
    labels = [c["label"] for c in row["contributions"]]
    assert labels == ["numerator: Total current assets",
                      "denominator: Total current liabilities"]
    den = row["contributions"][1]
    assert (den["v1"], den["v2"]) == (150.0, 200.0)
    assert den["source"]["page_index"] == 5 and den["source2"]["page_index"] == 11


def test_a_kpi_whose_inputs_are_missing_is_shown_as_unavailable_not_dropped():
    d = _build_statement(_ratio_rows(), None, "kpi", "f.pdf")
    row = next(r for r in d["rows"] if r["id"] == "kpi_gross_gearing")
    assert row["v1"] is None and row["display1"] == "—" and row["status"] == "missing"


def test_an_equity_movement_is_not_reported_as_an_additional_item():
    # Its columns are components, not periods; it is on the changes-in-equity face already.
    rows = [{"source_label": "Loss for the year", "canonical_key": None, "values": [
        _v("Retained profits", -8_401_124), _v("Total equity", -8_401_124)]}]
    d = _build_statement(rows, None, "additional_items", "f.pdf")
    assert d["rows"] == []


# --------------------------------------------------------------------------------------
# The edit endpoint, end to end over HTTP
# --------------------------------------------------------------------------------------
def _seed_run(session, rows, template_def=None):
    """A document with one extraction run carrying `rows`."""
    from app.db.models import Document, ExtractionRun, TemplateVersion

    import uuid

    doc = Document(filename="f.pdf", fmt="pdf", byte_size=1, page_count=1,
                   content_hash=uuid.uuid4().hex, object_key="k",
                   owner="admin", status="extracted")
    session.add(doc)
    session.flush()
    options = {}
    if template_def is not None:
        tv = TemplateVersion(template_key=f"t-{uuid.uuid4().hex[:8]}", name="t", version=1,
                             definition=template_def)
        session.add(tv)
        session.flush()
        options["template_version_id"] = tv.id
    run = ExtractionRun(document_id=doc.id, status="succeeded", options=options,
                        result={"rows": rows, "filename": "f.pdf"})
    session.add(run)
    session.commit()
    return doc.id


def _session():
    from app.db.base import SessionLocal, init_db

    init_db()
    return SessionLocal()


def _statement(client, doc_id, statement="balance_sheet", basis="consolidated"):
    r = client.get(f"/api/v1/documents/{doc_id}/statement",
                   params={"statement": statement, "basis": basis})
    assert r.status_code == 200, r.text
    return r.json()


def test_editing_the_standalone_column_changes_the_standalone_figure(client):
    with _session() as s:
        doc_id = _seed_run(s, [{"source_label": "Cash", "canonical_key": "bs_cash",
                                "values": [_v("current", 10, basis="consolidated"),
                                           _v("current", 20, basis="standalone")]}])
    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/bs_cash",
                     json={"value": 99, "formula": "", "period": "current",
                           "basis": "standalone"},)
    assert r.status_code == 200, r.text
    assert r.json()["current"] == 99
    # The basis that was edited moved; the other did not. Without a basis on the request every
    # edit landed on consolidated, so an analyst on the standalone grid saw nothing happen.
    row = next(x for x in _statement(client, doc_id, basis="standalone")["rows"]
               if x["id"] == "bs_cash")
    assert row["v1"] == 99 and row["status"] == "edited"
    other = next(x for x in _statement(client, doc_id)["rows"] if x["id"] == "bs_cash")
    assert other["v1"] == 10 and other["status"] is None


_MINI_TEMPLATE = {
    "schema_version": 1, "template_key": "mini", "name": "Mini",
    "statements": [{"type": "balance_sheet", "sections": [
        {"node_id": "s1", "label": "Current assets", "children": [
            {"canonical_key": "bs_current_assets__cash", "label": "Cash", "role": "line"},
            {"canonical_key": "bs_current_assets__inventories", "label": "Inventories",
             "role": "line"}]}]}]}


def test_a_template_line_the_document_never_yielded_can_be_entered_by_hand(client):
    with _session() as s:
        doc_id = _seed_run(s, [{"source_label": "Cash", "canonical_key": "bs_current_assets__cash",
                                "values": [_v("current", 10)]}], template_def=_MINI_TEMPLATE)
    blank = next(x for x in _statement(client, doc_id)["rows"]
                 if x["id"] == "bs_current_assets__inventories")
    assert blank["status"] == "missing" and blank["editable"] is True

    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/bs_current_assets__inventories",
                     json={"value": 4_200, "formula": "", "period": "current",
                           "basis": "consolidated"})
    assert r.status_code == 200, r.text          # used to be a silent 404
    row = next(x for x in _statement(client, doc_id)["rows"]
               if x["id"] == "bs_current_assets__inventories")
    assert row["v1"] == 4_200 and row["status"] == "edited"

    # Reverting a line that only ever existed by hand removes it again.
    d = client.delete(f"/api/v1/documents/{doc_id}/line-items/bs_current_assets__inventories",)
    assert d.status_code == 200 and d.json()["reverted"] is True
    back = next(x for x in _statement(client, doc_id)["rows"]
                if x["id"] == "bs_current_assets__inventories")
    assert back["v1"] is None and back["status"] == "missing"


def test_a_concept_that_is_in_neither_the_run_nor_the_template_is_still_refused(client):
    with _session() as s:
        doc_id = _seed_run(s, [{"source_label": "Cash", "canonical_key": "bs_current_assets__cash",
                                "values": [_v("current", 10)]}], template_def=_MINI_TEMPLATE)
    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/bs_not_a_concept",
                     json={"value": 1, "formula": "", "period": "current",
                           "basis": "consolidated"})
    assert r.status_code == 404


def test_editing_a_combined_line_shows_the_figure_that_was_typed(client):
    with _session() as s:
        doc_id = _seed_run(s, [
            {"source_label": "Sundry A", "canonical_key": "bs_x__others",
             "values": [_v("current", 100), _v("prior", 90)]},
            {"source_label": "Sundry B", "canonical_key": "bs_x__others",
             "values": [_v("current", 50), _v("prior", 40)]}])
    before = next(x for x in _statement(client, doc_id)["rows"] if x["id"] == "bs_x__others")
    assert before["v1"] == 150 and len(before["contributions"]) == 2

    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/bs_x__others",
                     json={"value": 200, "formula": "", "period": "current",
                           "basis": "consolidated"})
    assert r.status_code == 200 and r.json()["current"] == 200
    after = next(x for x in _statement(client, doc_id)["rows"] if x["id"] == "bs_x__others")
    assert after["v1"] == 200                     # not 350
    assert after["v2"] == 130                     # the prior period is untouched
    # The printed lines still show their own figures, so the override stays auditable.
    assert [c["v1"] for c in after["contributions"]] == [200.0, 50.0]


def test_the_prior_period_can_be_edited_on_its_own(client):
    with _session() as s:
        doc_id = _seed_run(s, [{"source_label": "Cash", "canonical_key": "bs_cash",
                                "values": [_v("current", 10), _v("prior", 20)]}])
    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/bs_cash",
                     json={"value": 21, "formula": "", "period": "prior",
                           "basis": "consolidated"})
    assert r.status_code == 200, r.text
    row = next(x for x in _statement(client, doc_id)["rows"] if x["id"] == "bs_cash")
    assert (row["v1"], row["v2"]) == (10, 21)     # only the column that was edited moved


# --------------------------------------------------------------------------------------
# A period column is not an equity component
# --------------------------------------------------------------------------------------
def test_a_spreadsheets_date_headers_are_periods_not_equity_components():
    # Excel extraction puts the sheet's real header TEXT in period_label. Read as component
    # names, every spreadsheet row becomes an "equity movement" — which would both corrupt the
    # changes-in-equity view and hide the row from Additional items, since that view excludes
    # rows already on the equity face.
    from app.api.routes.documents import _is_named_column, _matrix_rows

    assert _is_named_column("Retained profits") is True
    assert _is_named_column("Total equity") is True
    for header in ("2023", "31 December 2023", "FY2024", "Q4 2023", "二零二三年"):
        assert _is_named_column(header) is False, header

    excel_row = [{"canonical_key": None, "source_label": "Sundry income", "values": [
        {"basis": "consolidated", "period_label": "2023", "value": "10"},
        {"basis": "consolidated", "period_label": "2022", "value": "9"}]}]
    assert _matrix_rows(excel_row, "consolidated") == []

