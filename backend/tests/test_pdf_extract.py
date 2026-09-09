"""PDF extraction: native text layer (PyMuPDF) and the scanned/OCR path — both producing
line items with page + normalized-bbox provenance."""
from __future__ import annotations

import json
import pathlib
import time

import pytest

pytest.importorskip("fitz")

from tests.fixtures.generate import make_native_pdf

_SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


def _hkfrs_configuration(client) -> dict:
    """Publish the HKFRS spread's configuration as a LINE-ITEM SET, and hand back its version row.

    WHY THIS EXISTS. These tests pinned ``ontology_version_id`` at the seeded ``hkfrs_hk_china``
    rulebook and asserted the ``hkfrs_hk_china_v1`` spread it produces — its canonical keys, its
    section labels, its ``label_i18n``, its ratios. There is one configuration engine now and the
    ONE shipped set targets ``output_csv_hk_v1``, so ``hkfrs_hk_china_v1`` is seeded
    TEMPLATE-ONLY and a run against it maps nothing until somebody publishes a set that targets it
    (``app/sample/reference.py``, stated there as the instructed consequence). Pinning the shipped
    set instead would silently move every assertion below onto a different spread.

    So the test publishes what a configurator would: the same rulebook content, through
    ``services.ontology_projection`` — the proven projection, 462/462 concepts with nothing
    homeless — as a ``LineItemSet`` on the ``/line-items`` publish gate. Nothing here is an
    ontology the product stores or serves: the file is repo DATA read off disk, the projection is
    an internal function, and what reaches the database is a line-item version like any other.

    ``section_defaults`` is carried but FILTERED to the fields the line-item schema declares: the
    rulebook's ``SectionDefaults`` also has ``value_scope``, ``extraction_mode``, ``include`` and
    ``exclude``, which the stray-key gate refuses. It cannot be dropped altogether — every
    projected concept keeps its ``inherits``, and a set whose sections are missing fails its
    resolved load with ``UnknownInheritsError`` (measured: all 183 concepts, and the run dies in
    the worker rather than at the publish gate).
    """
    from app.schemas.line_items import SectionDefaults
    from app.schemas.loader import load_ontology
    from app.services.ontology_projection import build_definitions
    from app.services.working_view import _jsonable

    raw = json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    ont = load_ontology(raw, resolve=True)
    items, _census = build_definitions(list(ont.mappings), [])
    declared = set(SectionDefaults.model_fields)
    sections = {name: {k: v for k, v in _jsonable(sec).items() if k in declared}
                for name, sec in (raw.get("section_defaults") or {}).items()}
    definition = {
        "line_items_key": "hkfrs_hk_china",
        "target_template_key": ont.target_template_key,
        "section_defaults": sections,
        "locale": ont.locale,
        "supported_locales": list(ont.supported_locales),
        "number_format_by_locale": _jsonable(ont.number_format_by_locale),
        "residual_framework": _jsonable(ont.residual_framework),
        "normalisation": _jsonable(ont.normalisation),
        "binding": _jsonable(ont.binding),
        "global_rules": _jsonable(ont.global_rules),
        "scope_selection": _jsonable(ont.scope_selection),
        "decomposition_rules": _jsonable(ont.decomposition_rules),
        "netting_rules": _jsonable(ont.netting_rules),
        "worked_examples": _jsonable(ont.worked_examples),
        "validation": _jsonable(ont.validation),
        "items": items,
    }
    # Configuration is an admin surface (``CONFIG_LINE_ITEMS``), so the publish is made as admin;
    # every run below is still started by the fixture's default caller.
    r = client.post("/api/v1/line-items", json={"definition": definition},
                    headers={"X-Role": "admin"})
    assert r.status_code == 201, r.text
    return r.json()


def _pins(client) -> tuple[dict, dict]:
    """The configuration above and the template it targets, as a run pins them."""
    cfg = _hkfrs_configuration(client)
    tpl = next(t for t in client.get("/api/v1/templates").json()
               if t["template_key"] == cfg["target_template_key"])
    return cfg, tpl


def _await_run(client, doc_id: str) -> dict:
    """Extraction is a background job now; poll the run until it finishes and return its
    result (raises if it fails or never completes)."""
    for _ in range(100):
        r = client.get(f"/api/v1/documents/{doc_id}/run")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            return r.json()["result"]
        if r.status_code == 200 and r.json().get("status") == "failed":
            raise AssertionError("extraction failed")
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


def test_native_pdf_extracts_values_with_bbox_provenance():
    from app.core.models.enums import Basis
    from app.services.documents import run_extraction

    doc, ctx = run_extraction(make_native_pdf(), filename="bs.pdf")
    assert doc.fmt.value == "pdf"
    labels = {li.source_label for li in doc.line_items}
    assert any("Trade receivables" in lbl for lbl in labels)

    tr = next(li for li in doc.line_items if "Trade receivables" in li.source_label)
    val = tr.get_value(Basis.CONSOLIDATED, period_label="current")
    assert val is not None and int(val.value) == 3410      # "3,410" parsed
    assert val.provenance is not None and val.provenance.page_index == 0
    assert val.provenance.bbox is not None                  # real click-to-source geometry
    assert val.provenance.source_kind == "native"
    assert tr.note_number == "15"                           # "Note 15" captured, not a value


def test_extraction_api_returns_rows_with_provenance(client):
    """Upload → run extraction → the run result carries view-ready rows + provenance."""
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]
    r = client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "running"                  # async: kicked off, poll for result
    rows = _await_run(client, doc_id)["rows"]
    assert rows and any(row["values"] for row in rows)
    tr = next(row for row in rows if "Trade receivables" in row["source_label"])
    prov = tr["values"][0]["provenance"]
    assert prov["source_kind"] == "native" and prov["page_index"] == 0 and prov["bbox"]
    assert tr["note"] == "15"


def test_document_integrity_endpoint_shapes_real_report(client):
    """A real uploaded file surfaces its own pre-flight integrity in the screen's shape."""
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]
    r = client.get(f"/api/v1/documents/{doc_id}/integrity")
    assert r.status_code == 200, r.text
    body = r.json()
    assert 0 <= body["score"] <= 100
    assert body["grade"] and body["summary"]
    assert len(body["stats"]) == 4 and any(s["label"] == "Pages" for s in body["stats"])
    assert body["issues"]  # at least the "no issues" row for a clean native PDF
    assert all({"title", "detail", "pages", "note", "status", "severity"} <= set(i) for i in body["issues"])


def test_real_review_and_export_from_extraction(client):
    """A real uploaded file → extract → review queue + Excel/JSON export, all from the
    document's own run (no demo data)."""
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]
    client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    assert _await_run(client, doc_id)["rows"]

    # Latest run is fetchable for the export preview/counts.
    run = client.get(f"/api/v1/documents/{doc_id}/run")
    assert run.status_code == 200 and run.json()["result"]["rows"]

    # Review queue derives from the real rows.
    rev = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert set(rev) == {"run_id", "checks", "tabs", "summary", "judgements", "coverage",
                        "remap_targets", "weak_mappings"}
    assert rev["tabs"][0]["label"] == "All"
    assert rev["summary"]["open"] + rev["summary"]["passed"] >= 1

    # JSON export contains the real line items with source locations.
    j = client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "json"})
    assert j.status_code == 200 and j.headers["content-type"].startswith("application/json")
    body = j.json()
    assert body["source_document"] == "bs.pdf" and body["line_item_count"] >= 1
    assert any("Trade receivables" in (li["source_label"] or "") for li in body["line_items"])

    # Excel export is a real workbook.
    x = client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "excel"})
    assert x.status_code == 200
    assert x.content[:2] == b"PK" and len(x.content) > 200          # xlsx = zip


def test_real_pages_and_statement_from_document(client):
    """Page Scope + Workspace read the real document: page classification (pre-extraction)
    and the extracted statement grouped by the template."""
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]

    # Pages are available before extraction (confirm-scope step).
    pg = client.get(f"/api/v1/documents/{doc_id}/pages").json()
    assert pg["total"] >= 1 and pg["total"] == len(pg["pages"])
    assert pg["focused"] + pg["skipped"] == pg["total"]
    assert all({"no", "cls", "conf", "included", "scan"} <= set(p) for p in pg["pages"])

    # Attach the seeded configuration/template so mapping produces canonical keys (as the UI does).
    cfg, tpl = _pins(client)
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    st = client.get(f"/api/v1/documents/{doc_id}/statement", params={"statement": "balance_sheet"}).json()
    assert st["statement"] == "balance_sheet" and st["viewer"]["company"] == "bs.pdf"
    items = [r for r in st["rows"] if r["kind"] == "item"]
    assert items and any("Trade receivables" in (r.get("source_label") or "") for r in items)
    assert all(r["v1"] is None or isinstance(r["v1"], (int, float)) for r in st["rows"])

    # Standalone basis (not extracted) → valid but empty grid, never demo data.
    sa = client.get(f"/api/v1/documents/{doc_id}/statement",
                    params={"statement": "balance_sheet", "basis": "standalone"}).json()
    assert sa["rows"] == []


def test_dual_basis_extracted_in_one_pass():
    """A Consolidated | Standalone two-level header → both bases extracted for each line."""
    from app.core.models.enums import Basis
    from app.services.documents import run_extraction
    from tests.fixtures.generate import make_dual_basis_pdf

    doc, _ = run_extraction(make_dual_basis_pdf(), filename="dual.pdf")
    tr = next(li for li in doc.line_items if "Trade receivables" in li.source_label)
    cc = tr.get_value(Basis.CONSOLIDATED, period_label="current")
    cp = tr.get_value(Basis.CONSOLIDATED, period_label="prior")
    sc = tr.get_value(Basis.STANDALONE, period_label="current")
    sp = tr.get_value(Basis.STANDALONE, period_label="prior")
    assert cc and int(cc.value) == 3410 and cp and int(cp.value) == 2900
    assert sc and int(sc.value) == 3100 and sp and int(sp.value) == 2700


def test_dual_basis_statement_selects_by_basis(client):
    from tests.fixtures.generate import make_dual_basis_pdf

    doc_id = client.post(
        "/api/v1/documents", files={"file": ("dual.pdf", make_dual_basis_pdf(), "application/pdf")}
    ).json()["id"]
    cfg, tpl = _pins(client)
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    _await_run(client, doc_id)

    def tr_value(basis):
        st = client.get(f"/api/v1/documents/{doc_id}/statement",
                        params={"statement": "balance_sheet", "basis": basis}).json()
        row = next(x for x in st["rows"]
                   if x["kind"] == "item" and "Trade receivables" in (x.get("source_label") or ""))
        return row["v1"]

    assert tr_value("consolidated") == 3410
    assert tr_value("standalone") == 3100        # different basis → different value, one pass


def _extract_with_configuration(client, filename="bs.pdf", data=None):
    doc_id = client.post(
        "/api/v1/documents",
        files={"file": (filename, data or make_native_pdf(), "application/pdf")},
    ).json()["id"]
    cfg, tpl = _pins(client)
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    _await_run(client, doc_id)
    return doc_id


def test_real_line_item_edit_persists_to_statement(client):
    doc_id = _extract_with_configuration(client)
    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    key = next(r["canonical_key"] for r in rows if r.get("canonical_key"))

    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/{key}",
                     json={"value": 12345, "formula": "=A+B"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "edited" and r.json()["value"] == "12345"

    st = client.get(f"/api/v1/documents/{doc_id}/statement", params={"statement": "balance_sheet"}).json()
    row = next(x for x in st["rows"] if x["id"] == key)
    assert row["v1"] == 12345 and row["status"] == "edited" and row["formula"] == "=A+B"
    assert row["editable"] is True


def test_derived_analysis_ratios_disclosures_notes(client):
    """Ratios, disclosure scan, and free-form notes — all derived from the extraction."""
    from tests.fixtures.generate import make_rich_pdf

    doc_id = _extract_with_configuration(client, filename="rich.pdf", data=make_rich_pdf())
    a = client.get(f"/api/v1/documents/{doc_id}/analysis").json()

    # Ratios computed from extracted totals.
    cr = next(r for r in a["ratios"] if r["key"] == "current_ratio")
    assert cr["available"] and cr["value"] == 2.0 and cr["display"] == "2.0×"
    # The full catalog is always returned (unavailable ones flagged, never fabricated).
    assert any(not r["available"] for r in a["ratios"])

    # Qualitative disclosures detected in the notes text, with a page.
    disc = {d["key"]: d for d in a["disclosures"]}
    assert disc["contingent_liabilities"]["present"] and disc["contingent_liabilities"]["page"] == 3
    assert disc["auditor_qualification"]["present"] and disc["guarantees"]["present"]

    # Free-form notes generated from the movements/ratios.
    assert any("Trade receivables" in n["title"] for n in a["notes"])
    assert any("Current ratio" in n["text"] for n in a["notes"])

    # Disclosure labels localize in the output locale (Req 21).
    fr = client.get(f"/api/v1/documents/{doc_id}/analysis", params={"locale": "fr"}).json()
    fr_disc = {d["key"]: d["label"] for d in fr["disclosures"]}
    assert fr_disc["contingent_liabilities"] == "Passifs éventuels"


def test_analysis_and_export_sheets_present(client):
    """The formatted export carries Note details / Ratios / Disclosures sheets alongside
    statements, and no longer a Highlights/commentary sheet."""
    import io as _io

    import openpyxl

    from tests.fixtures.generate import make_rich_pdf

    doc_id = _extract_with_configuration(client, filename="rich.pdf", data=make_rich_pdf())
    x = client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "excel", "layout": "statement"})
    wb = openpyxl.load_workbook(_io.BytesIO(x.content))
    assert {"Note details", "Ratios", "Disclosures", "Credit Analysis"} <= set(wb.sheetnames)
    assert "Highlights" not in wb.sheetnames
    credit_text = " | ".join(str(v) for row in wb["Credit Analysis"].iter_rows(values_only=True)
                             for v in row if v)
    assert "Overall stance" in credit_text and "Rating factors" in credit_text
    assert "Report signals" in credit_text
    # The JSON export carries the same credit block.
    j = client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "json"}).json()
    assert "credit" in j["analysis"] and "stance" in j["analysis"]["credit"]
    ratios_text = " | ".join(str(v) for row in wb["Ratios"].iter_rows(values_only=True) for v in row if v)
    assert "Current ratio" in ratios_text and "2.0×" in ratios_text
    # Credit-focused ratios and their category headers are present (listed even when a given
    # input isn't extracted — the full catalog always shows).
    assert "Leverage" in ratios_text and "Coverage" in ratios_text
    assert "Net debt / EBITDA" in ratios_text and "Debt-service coverage (DSCR)" in ratios_text
    disc_text = " | ".join(str(v) for row in wb["Disclosures"].iter_rows(values_only=True) for v in row if v)
    assert "Contingent liabilities" in disc_text


def test_formatted_statement_export_is_template_driven(client):
    """The default Excel export mirrors the run's template — one sheet per statement, with
    the template's sections/subtotals/totals and localized labels — for ANY such template."""
    import openpyxl
    import io as _io

    doc_id = _extract_with_configuration(client)

    # Statement layout (default) → a workbook with a Balance Sheet sheet built from the template.
    x = client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "excel", "layout": "statement"})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    wb = openpyxl.load_workbook(_io.BytesIO(x.content))
    # A sheet per statement in the template.
    assert {"Balance Sheet", "Profit & Loss", "Cash Flow"} <= set(wb.sheetnames)
    ws = wb["Balance Sheet"]
    joined = " | ".join(str(v) for row in ws.iter_rows(values_only=True) for v in row if v)
    assert "Current assets" in joined                 # a template section header
    assert "Total current assets" in joined           # a template subtotal (structural anchor)
    assert "Trade receivables" in joined              # an extracted, template-labelled line
    assert "Inventories" in joined                    # a NON-extracted template line still appears

    # The FULL template is written out — every line item, not just the extracted ones.
    labelled = sum(
        1
        for name in wb.sheetnames
        for row in wb[name].iter_rows(min_row=6, values_only=True)
        if row and row[0]
    )
    assert labelled >= 150, f"expected the full template (~150+ rows), got {labelled}"

    # Localized (zh) labels come from the template's label_i18n.
    zh = client.get(f"/api/v1/documents/{doc_id}/export",
                    params={"fmt": "excel", "layout": "statement", "locale": "zh"})
    wbz = openpyxl.load_workbook(_io.BytesIO(zh.content))
    zjoined = " | ".join(str(v) for row in wbz["Balance Sheet"].iter_rows(values_only=True)
                         for v in row if v)
    assert "流动资产" in zjoined and "应收账款" in zjoined       # section + line in Chinese

    # Flat layout is still available.
    flat = client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "excel", "layout": "flat"})
    assert flat.status_code == 200 and flat.content[:2] == b"PK"


def test_real_statement_localized_and_basis_echoed(client):
    """The Workspace statement resolves labels in the output locale (label_i18n) and echoes
    the requested basis (Req 21, 13)."""
    doc_id = _extract_with_configuration(client)
    zh = client.get(f"/api/v1/documents/{doc_id}/statement",
                    params={"statement": "balance_sheet", "locale": "zh"}).json()
    labels = " | ".join(r.get("label", "") for r in zh["rows"])
    assert "流动资产" in labels          # "Current assets" section localized from label_i18n
    # A standalone request echoes the requested basis (not hardcoded consolidated).
    sa = client.get(f"/api/v1/documents/{doc_id}/statement",
                    params={"statement": "balance_sheet", "basis": "standalone"}).json()
    assert sa["basis"] == "standalone"


def test_edit_then_revert_restores_original(client):
    doc_id = _extract_with_configuration(client)
    st0 = client.get(f"/api/v1/documents/{doc_id}/statement", params={"statement": "balance_sheet"}).json()
    item = next(r for r in st0["rows"] if r["kind"] == "item")
    key, original = item["id"], item["v1"]

    client.patch(f"/api/v1/documents/{doc_id}/line-items/{key}", json={"value": 99999, "formula": "=x"})
    st1 = client.get(f"/api/v1/documents/{doc_id}/statement", params={"statement": "balance_sheet"}).json()
    edited = next(r for r in st1["rows"] if r["id"] == key)
    assert edited["v1"] == 99999 and edited["status"] == "edited"

    r = client.delete(f"/api/v1/documents/{doc_id}/line-items/{key}")
    assert r.status_code == 200 and r.json()["reverted"] is True
    st2 = client.get(f"/api/v1/documents/{doc_id}/statement", params={"statement": "balance_sheet"}).json()
    reverted = next(r for r in st2["rows"] if r["id"] == key)
    assert reverted["v1"] == original and reverted["status"] is None and reverted["formula"] is None


def test_real_notes_index_and_detail(client):
    doc_id = _extract_with_configuration(client)
    notes = client.get(f"/api/v1/documents/{doc_id}/notes").json()
    assert notes["count"] >= 1 and notes["linked"] >= 1
    assert any(n["no"] == "15" for n in notes["notes"])      # make_native_pdf cites "Note 15"

    detail = client.get(f"/api/v1/documents/{doc_id}/notes/15").json()
    assert detail["no"] == "15" and detail["linked_label"]
    assert detail["rows"] and detail["reconciliation"] is None


def test_note_table_rows_prefers_printed_year_headers():
    from app.api.routes.documents import _note_table_rows

    rows = [
        {
            "label": "Value at start of the year",
            "values": [
                {"period_label": "current", "period_display": "2024", "value": "10"},
                {"period_label": "prior", "period_display": "2025", "value": "12"},
            ],
        },
        {
            "label": "Amortisation provided during the year",
            "values": [
                {"period_label": "current", "period_display": "2024", "value": "2"},
                {"period_label": "prior", "period_display": "2025", "value": "3"},
            ],
        },
    ]

    table = _note_table_rows(rows, ["current", "prior"])
    assert table["columns"] == ["2024", "2025"]
    assert table["rows"][0]["values"] == {"2024": "10", "2025": "12"}
    assert table["rows"][1]["values"] == {"2024": "2", "2025": "3"}


def test_note_detail_tables_extracted_from_notes_page():
    """A notes page ("Note 14: Cash and cash equivalents" + its breakdown rows) is parsed
    into a NotesTable with the detail line items behind the face figure."""
    from app.services.documents import run_extraction
    from tests.fixtures.generate import make_multipage_pdf

    doc, _ = run_extraction(make_multipage_pdf(), filename="multi.pdf")
    assert doc.notes, "expected a note detail table parsed from the notes page"
    n14 = next(nt for nt in doc.notes if nt.note_number == "14")
    assert "Cash" in n14.title
    labels = {it.raw_label for it in n14.items}
    assert any("Cash on hand" in lbl for lbl in labels)
    assert any("Balances with banks" in lbl for lbl in labels)
    # The detail values carry page provenance from the notes page (page index 1).
    hand = next(it for it in n14.items if "Cash on hand" in it.raw_label)
    ev = next(iter(hand.values.values()))
    assert int(ev.value) == 204 and ev.provenance.page_index == 1


def test_extracted_note_detail_served_and_exported(client):
    """The extracted note breakdown flows to the All-Notes endpoint and the export's
    'Note details' sheet — the real notes, not auto-generated commentary."""
    import io as _io

    import openpyxl

    from tests.fixtures.generate import make_multipage_pdf

    doc_id = _extract_with_configuration(client, filename="multi.pdf", data=make_multipage_pdf())

    # All-Notes index prefers the extracted detail tables (high-confidence, with rows).
    notes = client.get(f"/api/v1/documents/{doc_id}/notes").json()
    assert any(n["no"] == "14" and n["conf"] == "high" for n in notes["notes"])

    detail = client.get(f"/api/v1/documents/{doc_id}/notes/14").json()
    assert detail["no"] == "14" and detail["page"] == 2     # notes page (1-based)
    row_labels = {r["label"] for r in detail["rows"]}
    assert any("Cash on hand" in lbl for lbl in row_labels)
    hand = next(r for r in detail["rows"] if "Cash on hand" in r["label"])
    assert hand["v1"] == 204

    # The export's "Note details" sheet carries the same breakdown.
    x = client.get(f"/api/v1/documents/{doc_id}/export",
                   params={"fmt": "excel", "layout": "statement"})
    wb = openpyxl.load_workbook(_io.BytesIO(x.content))
    assert "Note details" in wb.sheetnames
    text = " | ".join(str(v) for row in wb["Note details"].iter_rows(values_only=True)
                       for v in row if v)
    assert "Cash on hand" in text and "Balances with banks" in text


def test_note_detail_serves_the_same_period_labels_as_the_statement(client):
    """The Notes screen used to print "FY25"/"FY24" as literals above these very columns, so on any
    filing whose periods are not those two it labelled the same figures differently from the
    Workspace. The endpoint now serves `periods` — the SAME key, in the same order, the statement
    routes serve — derived from exactly the value lists the note's v1/v2 came out of.
    """
    from tests.fixtures.generate import make_multipage_pdf

    doc_id = _extract_with_configuration(client, filename="multi.pdf", data=make_multipage_pdf())

    detail = client.get(f"/api/v1/documents/{doc_id}/notes/14").json()
    assert len(detail["periods"]) == 2 and all(p.strip() for p in detail["periods"])
    stmt = client.get(f"/api/v1/documents/{doc_id}/statement",
                      params={"statement": "balance_sheet"}).json()
    assert detail["periods"] == stmt["periods"]

    # The Current/Prior fallback was reaching a zh/ar/fr reader in English — this fixture's columns
    # carry no printed date, so it is exactly the path that was broken.
    zh = client.get(f"/api/v1/documents/{doc_id}/notes/14", params={"locale": "zh"}).json()
    assert zh["periods"] == ["本期", "上期"]
    fr = client.get(f"/api/v1/documents/{doc_id}/notes/14", params={"locale": "fr"}).json()
    assert fr["periods"] == ["Actuel", "Précédent"]


def test_real_integrity_and_review_localized(client):
    doc_id = _extract_with_configuration(client)
    zh = client.get(f"/api/v1/documents/{doc_id}/integrity", params={"locale": "zh"}).json()
    assert any(s["label"] == "页数" for s in zh["stats"])       # "Pages" localized
    assert zh["grade"] and zh["grade"] != "Ready to extract"

    rev = client.get(f"/api/v1/documents/{doc_id}/review", params={"locale": "fr"}).json()
    assert rev["tabs"][0]["label"] == "Tous"                    # "All" localized (fr)


def test_can_access_enforces_ownership():
    from app.api.routes.documents import _can_access
    from app.security import Principal, Role

    class _Doc:
        owner = "alice"

    bob = Principal(role=Role.ANALYST, username="bob", name="Bob", via="session")
    alice = Principal(role=Role.ANALYST, username="alice", name="Alice", via="session")
    reviewer = Principal(role=Role.REVIEWER, username="rev", name="Rev", via="session")
    assert not _can_access(_Doc(), bob)          # another analyst's document
    assert _can_access(_Doc(), alice)            # the owner
    assert _can_access(_Doc(), reviewer)         # reviewer works the whole queue


def test_document_ownership_scoping(anon_client, auth):
    up = anon_client.post(
        "/api/v1/documents", files={"file": ("own.pdf", make_native_pdf(), "application/pdf")},
        headers=auth("analyst"),
    )
    doc_id = up.json()["id"]
    # The analyst sees their own upload; a reviewer (elevated) can open it too.
    mine = anon_client.get("/api/v1/documents", headers=auth("analyst")).json()["documents"]
    assert any(d["id"] == doc_id for d in mine)
    assert anon_client.get(f"/api/v1/documents/{doc_id}", headers=auth("reviewer")).status_code == 200
    assert anon_client.get(f"/api/v1/documents/{doc_id}/integrity", headers=auth("reviewer")).status_code == 200
    # Unauthenticated is rejected outright.
    assert anon_client.get(f"/api/v1/documents/{doc_id}").status_code == 401


def test_review_and_run_404_before_extraction(client):
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("x.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]
    # No run yet → /run and /export 404; /review returns an empty (but valid) queue.
    assert client.get(f"/api/v1/documents/{doc_id}/run").status_code == 404
    assert client.get(f"/api/v1/documents/{doc_id}/export", params={"fmt": "json"}).status_code == 404
    rev = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert rev["summary"] == {"open": 0, "accepted": 0, "stale": 0, "conflict": 0,
                              "passed": 0}
    # The empty queue is the SAME shape as a real one — every tab, not the one-tab literal this
    # branch used to hand-write — so the screen has no second empty-state to render.
    # Three tabs, not five: a tab whose types select nothing is a filter that empties the list.
    assert [t["label"] for t in rev["tabs"]] == ["All", "Checks", "Unmapped"]
    assert rev["coverage"] == {"available": False, "reason": "not_extracted",
                               "reason_label": rev["coverage"]["reason_label"]}
    assert rev["coverage"]["reason_label"]


def test_missing_integrity_report_is_not_shown_as_clean(client):
    """A document with no stored integrity report must NOT be reported as a clean all-clear."""
    from app.api.routes.documents import _serialize_document_integrity

    class _Row:
        integrity_report = None
        page_count = 3
    body = _serialize_document_integrity(_Row())
    assert body["grade"] == "Not analyzed" and body["score"] == 0
    assert all(i["severity"] != "ok" for i in body["issues"])


def test_page_image_endpoint_renders_png(client):
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]
    r = client.get(f"/api/v1/documents/{doc_id}/pages/0/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) > 100


def test_reference_configuration_seeded_and_attached_run_maps(client):
    """The shipped line-item configuration is seeded; attaching it to a run populates canonical keys
    (deterministic here — the alias tier maps offline without an LLM)."""
    from app.config import get_settings
    from app.services import settings_state  # noqa: F401 (import parity with other tests)

    cfg, tpl = _pins(client)

    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]

    s = get_settings()
    prev = s.extraction.llm_mapping
    s.extraction.llm_mapping = False  # force the deterministic ensemble (no network) for the test
    try:
        r = client.post(f"/api/v1/documents/{doc_id}/extractions",
                        json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
        assert r.status_code == 202, r.text
        rows = _await_run(client, doc_id)["rows"]
        tr = next(row for row in rows if "Trade receivables" in row["source_label"])
        assert tr["canonical_key"], "expected the caption to map to a canonical concept"
    finally:
        s.extraction.llm_mapping = prev


def test_scanned_page_routes_through_ocr_port():
    """A scanned page is rasterized and sent to the configured OCR provider; its words
    (already normalized) reconstruct into line items with source_kind 'ocr'."""
    from app.config import get_settings
    from app.core.models.document import DocumentModel, PageSource
    from app.core.models.enums import DocFormat, PageKind, PageSourceKind
    from app.core.models.geometry import BBox
    from app.core.stage import PipelineContext
    from app.ports.registry import registry
    from app.services.pdf_extract import extract_pdf

    class _FakeOcr:
        id = "fakeocr"

        def recognize(self, image_bytes, *, lang="en"):
            return {"words": [
                {"text": "Cash", "bbox": BBox(x0=0.10, y0=0.20, x1=0.20, y1=0.23), "confidence": 0.99},
                {"text": "and", "bbox": BBox(x0=0.21, y0=0.20, x1=0.26, y1=0.23), "confidence": 0.99},
                {"text": "equivalents", "bbox": BBox(x0=0.27, y0=0.20, x1=0.40, y1=0.23), "confidence": 0.99},
                {"text": "1,204", "bbox": BBox(x0=0.80, y0=0.20, x1=0.90, y1=0.23), "confidence": 0.98},
            ], "angle": 0.0}

        def detect_orientation(self, image_bytes):
            return 0.0

    registry.register("ocr", "fakeocr", _FakeOcr)
    settings = get_settings()
    prev = settings.ocr.engine
    settings.ocr.engine = "fakeocr"
    try:
        doc = DocumentModel(filename="scan.pdf", content_hash="h1")
        doc.fmt = DocFormat.PDF
        doc.pages = [PageSource(index=0, source_kind=PageSourceKind.SCANNED, kind=PageKind.FACE)]
        ctx = PipelineContext(raw_bytes=make_native_pdf())
        added = extract_pdf(make_native_pdf(), doc, ctx)
        assert added >= 1
        item = doc.line_items[0]
        assert "Cash" in item.source_label
        ev = next(iter(item.values.values()))
        assert int(ev.value) == 1204
        assert ev.provenance.source_kind == "ocr" and ev.provenance.bbox is not None
    finally:
        settings.ocr.engine = prev


def test_template_authoring_roundtrip(client):
    """A template can be authored from the frontend: POST a definition, it is validated,
    versioned, listed, and selectable to drive a run (Req 4)."""
    tpls = client.get("/api/v1/templates").json()
    hk = next(t for t in tpls if t["template_key"] == "hkfrs_hk_china_v1")
    definition = client.get(f"/api/v1/templates/{hk['id']}").json()["definition"]

    before = max(t["version"] for t in tpls if t["template_key"] == "hkfrs_hk_china_v1")
    r = client.post("/api/v1/templates", json={"definition": definition})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["template_key"] == "hkfrs_hk_china_v1" and body["version"] == before + 1

    listed = client.get("/api/v1/templates").json()
    assert any(t["id"] == body["id"] for t in listed)
    assert client.get(f"/api/v1/templates/{body['id']}").status_code == 200

    bad = client.post("/api/v1/templates", json={"definition": {"not": "a template"}})
    assert bad.status_code == 422


def test_a_page_card_carries_the_measured_confidence_and_never_a_category_literal(client):
    """FINDING 7, backend half. `_conf_cat` computed the classifier's percentage and the page builder
    threw it away (``cat, _ = …``), so the screen printed a hardcoded literal PER CATEGORY instead —
    a page scored 0.40 rendered "54%", and `_conf_cat(None)` answered ('med', 60) so a page with no
    score at all rendered "78%". The measured figure is served beside the category, and it is null
    where nothing measured it, so a screen can say so instead of printing a number."""
    from app.api.routes.documents import _build_pages, _conf_cat

    pages = [{"index": 0, "kind": "face", "classification_confidence": 0.40,
              "source_kind": "native"},
             {"index": 1, "kind": "notes", "classification_confidence": 0.91,
              "source_kind": "native"},
             # No classification confidence at all — the case that used to render as "78%".
             {"index": 2, "kind": "other", "source_kind": "scanned"}]
    cards = _build_pages(pages)["pages"]
    # THE ASSERTIONS THAT FAIL WITH THE DEFECT RESTORED: the measured percentage is on the payload…
    assert [c["conf_pct"] for c in cards] == [40, 91, None]
    # …and it is the classifier's own number, not a function of the badge category.
    assert [c["conf"] for c in cards] == ["low", "high", "med"]
    assert _conf_cat(None) == ("med", None)
    assert _conf_cat(0.40) == ("low", 40)

    # And through the route, on a real document, every card carries the key.
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]
    served = client.get(f"/api/v1/documents/{doc_id}/pages").json()["pages"]
    assert served and all("conf_pct" in p for p in served)
    for p in served:
        assert p["conf_pct"] is None or 0 <= p["conf_pct"] <= 100


def test_a_note_detail_row_carries_the_measured_confidence_beside_its_badge(client):
    """The same literal reached the Notes detail table: every 'high' row printed "96%" whatever it
    scored. A note has no measured confidence of its own, so the index says so with a null rather than
    letting a category stand in for a measurement."""
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", make_native_pdf(), "application/pdf")}
    ).json()["id"]
    cfg, tpl = _pins(client)
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]})
    index = client.get(f"/api/v1/documents/{doc_id}/notes").json()["notes"]
    for n in index:
        assert n["conf_pct"] is None, "a note carries no measurement, so it may serve no percentage"

    # A detail row that DOES carry a score serves it. The stored run is given one note table whose
    # rows carry real confidences, because whether THIS fixture's pages parse into a note table is a
    # property of the fixture, not of the contract being pinned.
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter(
            ExtractionRun.document_id == doc_id).order_by(ExtractionRun.created_at.desc()).first()
        run.result = {**run.result, "note_details": [{
            "no": 12, "title": "Trade receivables", "page": 7,
            "rows": [{"label": "Trade receivables, gross", "confidence": 0.72,
                      "values": [{"period_label": "current", "value": "500"}]},
                     {"label": "Loss allowance", "confidence": 0.93,
                      "values": [{"period_label": "current", "value": "-20"}]},
                     {"label": "Unscored line", "values": [{"period_label": "current",
                                                            "value": "480"}]}]}]}
        session.commit()

    detail = client.get(f"/api/v1/documents/{doc_id}/notes/12").json()
    rows = detail["rows"]
    # THE ASSERTIONS THAT FAIL WITH THE DEFECT RESTORED: the badge carries the row's OWN measured
    # percentage, so two rows in one category cannot print the same number…
    assert [r.get("conf") for r in rows] == ["med", "high", None]
    assert [r.get("conf_pct") for r in rows] == [72, 93, None]
    # …and a row nothing scored carries no confidence key at all rather than a fabricated 'med'/78%.
    assert "conf" not in rows[2] and "conf_pct" not in rows[2]
