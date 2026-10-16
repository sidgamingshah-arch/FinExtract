"""What a run keeps on the record: its corrections, what the page printed, and what produced it.

* Every edit, re-map, revert and comment change APPENDS an event to ``run.result["history"]`` and to
  the audit trail; a revert is an event, not an eraser.
* The served run carries each value's printed figure and its sign / unit flags, the shared figures
  a contest removed, and the key a gap-routed row held before it was routed.
* The run records the commit, the settings in force, the reply contract's hash, the engines and the
  library versions; a settings change that steers extraction makes a new request.
"""
from __future__ import annotations

import json
import pathlib
import uuid

import pytest

from tests.run_pins import run_body

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
_INV = "bs_current_assets__inventories"
_CASH = "bs_current_assets__cash_and_cash_equivalents"


def _row(key, cur, label=None, **extra):
    return {"canonical_key": key, "source_label": label or key, "printed_in": "face",
            "mapping_method": "alias",
            "values": [{"basis": "consolidated", "period_label": "current", "value": str(cur),
                        "scale": "1"}], **extra}


def _seed(rows) -> str:
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    init_db()
    template = json.loads((SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8"))
    with SessionLocal() as s:
        doc = Document(filename="h.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin")
        s.add(doc)
        s.flush()
        tv = TemplateVersion(template_key=f"h-{uuid.uuid4().hex[:8]}", name="H", version=1,
                             definition=template)
        s.add(tv)
        s.flush()
        s.add(ExtractionRun(document_id=doc.id, status="succeeded",
                            options={"template_version_id": tv.id},
                            result={"rows": rows, "filename": "h.pdf", "pages": []}))
        s.commit()
        return doc.id


def _result(doc_id):
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as s:
        return dict(s.query(ExtractionRun).filter(ExtractionRun.document_id == doc_id).one().result)


def _patch(client, doc_id, value, comment=""):
    r = client.patch(f"/api/v1/documents/{doc_id}/line-items/{_INV}",
                     json={"value": value, "formula": "", "basis": "consolidated",
                           "period": "current", "comment": comment})
    assert r.status_code == 200, r.text


# --- R4.1 the history ------------------------------------------------------------------------------

def test_every_correction_appends_an_event_and_a_revert_does_not_erase_them(client):
    doc_id = _seed([_row(_INV, 2200, "Inventories")])
    _patch(client, doc_id, 2300, comment="restated in note 12")
    _patch(client, doc_id, 2400, comment="restated in note 12")      # value only
    _patch(client, doc_id, 2400, comment="per the auditor")         # comment only
    assert client.delete(f"/api/v1/documents/{doc_id}/line-items/{_INV}").status_code == 200

    history = _result(doc_id)["history"]
    assert [e["action"] for e in history] == ["edit", "comment", "edit", "comment", "revert"]
    for e in history:
        assert set(e) >= {"action", "slot", "before", "after", "reason", "by", "at"}
        assert e["by"]
    first, _, second = history[0], history[1], history[2]
    assert first["slot"] == f"{_INV}|consolidated/current"
    assert (first["before"], first["after"]) == ("2200", "2300")
    assert first["reason"] == "restated in note 12"
    assert (second["before"], second["after"]) == ("2300", "2400"), \
        "the second edit's record does not say what it replaced"
    assert history[3]["before"] == "restated in note 12" and history[3]["after"] == "per the auditor"
    revert = history[4]
    assert revert["before"][0]["value"] == "2400" and revert["after"][0]["value"] == "2200"


def test_a_remap_is_on_the_history_and_every_correction_is_on_the_audit_trail(client):
    from app.services import audit

    doc_id = _seed([_row(_INV, 2200, "Inventories"),
                    {**_row(None, 500, "Cash at bank"), "id": "r-cash"}])
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    unmapped = next(c for c in review["checks"] if c["type"] == "unmapped")
    moved = client.post(f"/api/v1/documents/{doc_id}/review/remap",
                        json={"row_ref": unmapped["remap"]["row_ref"], "canonical_key": _CASH,
                              "reason": "the bank balance"})
    assert moved.status_code == 200, moved.text
    _patch(client, doc_id, 2300)

    history = _result(doc_id)["history"]
    remap = history[0]
    assert remap["action"] == "remap" and remap["before"] == "" and remap["after"] == _CASH
    assert remap["reason"] == "the bank balance"
    actions = [e.action for e in audit.recorded(doc_id)]
    assert actions == ["correction_remap", "correction_edit"]


# --- R4.2 what the page printed --------------------------------------------------------------------

def test_the_served_value_carries_the_printed_figure_and_its_flags():
    from decimal import Decimal

    from app.api.routes.extractions import _serialize_rows
    from tests.test_calculated_and_gaps import _gap_doc

    doc = _gap_doc()
    ev = next(iter(doc.line_items[0].values.values()))
    ev.value_raw, ev.value, ev.sign_normalised = Decimal("-120"), Decimal("120"), True
    ev.confidence.flags.extend(["sign_normalised:unsigned_source", "prose_scaled_by:1000",
                                "low_mapping_confidence"])
    value = _serialize_rows(doc)[0]["values"][0]
    assert value["value"] == "120" and value["value_raw"] == "-120"
    assert value["sign_normalised"] is True
    assert value["sign_flags"] == ["sign_normalised:unsigned_source"]
    assert value["unit_flags"] == ["prose_scaled_by:1000"]


def test_a_gap_routed_row_keeps_the_key_it_had(monkeypatch):
    from tests.test_calculated_and_gaps import _Provider, _gap_doc, _run_stage

    doc, _ = _run_stage(_gap_doc(), _Provider(option=1), monkeypatch)
    routing = doc.gap_routings[0]
    prev = routing["previous"]
    assert prev and all("canonical_key" in p and "method" in p for p in prev)
    assert prev[0]["canonical_key"] != routing["others_key"]


# --- R4.4 what produced the run --------------------------------------------------------------------

def test_a_run_records_what_produced_it_and_a_settings_change_is_a_new_request(client):
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun
    from app.services import settings_state
    from tests.fixtures.generate import make_unmapped_row_pdf

    doc_id = client.post("/api/v1/documents",
                         files={"file": ("p.pdf", make_unmapped_row_pdf(),
                                         "application/pdf")}).json()["id"]
    first = client.post(f"/api/v1/documents/{doc_id}/extractions", json=run_body())
    assert first.status_code == 202, first.text
    run_id = first.json()["run_id"]
    import time
    for _ in range(200):
        if client.get(f"/api/v1/extractions/{run_id}").json()["status"] != "running":
            break
        time.sleep(0.05)
    with SessionLocal() as s:
        run = s.get(ExtractionRun, run_id)
        prov = run.options["provenance"]
        engine_version = run.engine_version
    for key in ("commit", "build", "engine_version", "settings", "settings_identity",
                "contract_hash", "ocr_engine", "document_engine", "libraries"):
        assert key in prov, key
    assert prov["engine_version"] == engine_version
    assert "ocr_scanned_pages" in prov["settings"]
    assert prov["libraries"].get("pydantic")
    # The shared figures a contest removed are kept on the result (empty here: no contest ran).
    assert client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["shared_figures"] == []

    same = client.post(f"/api/v1/documents/{doc_id}/extractions", json=run_body()).json()
    assert same["run_id"] == run_id and same.get("adopted") is True

    before = settings_state.extraction_config()["values"]["ocr_scanned_pages"]
    settings_state.set_extraction_config(persist=False, ocr_scanned_pages=not before)
    try:
        changed = client.post(f"/api/v1/documents/{doc_id}/extractions", json=run_body()).json()
        assert changed["run_id"] != run_id, "a settings change was answered by the old run"
    finally:
        settings_state.set_extraction_config(persist=False, ocr_scanned_pages=before)


def test_a_timing_only_setting_does_not_change_the_identity():
    from app.services import run_provenance, settings_state

    values = dict(settings_state.extraction_config()["values"])
    base = run_provenance.settings_identity(values)
    assert run_provenance.settings_identity(
        {**values, "llm_parallel_requests": (values["llm_parallel_requests"] or 1) + 1}) == base
    assert run_provenance.settings_identity(
        {**values, "llm_mapping": not values["llm_mapping"]}) != base
