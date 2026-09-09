from __future__ import annotations

import pytest

from tests.fixtures.generate import make_native_pdf

pytest.importorskip("fitz")


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_upload_document_returns_integrity_and_pages(client):
    pdf_bytes = make_native_pdf()  # reuse identical bytes so dedup can trigger
    r = client.post("/api/v1/documents", files={"file": ("bs.pdf", pdf_bytes, "application/pdf")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["format"] == "pdf"
    assert body["page_count"] == 1
    assert body["integrity_report"] is not None
    assert body["locale"] == "en"

    # Re-upload the SAME bytes → dedup by content hash.
    r2 = client.post("/api/v1/documents", files={"file": ("bs.pdf", pdf_bytes, "application/pdf")})
    assert r2.json()["duplicate_of"] is not None


def test_documents_upload_gated_and_listed(anon_client, auth):
    pdf_bytes = make_native_pdf()
    # Analyst may upload (documents:manage) …
    r = anon_client.post("/api/v1/documents", files={"file": ("g.pdf", pdf_bytes, "application/pdf")},
                         headers=auth("analyst"))
    assert r.status_code == 201, r.text
    # … but a reviewer may not.
    r2 = anon_client.post("/api/v1/documents", files={"file": ("g2.pdf", pdf_bytes, "application/pdf")},
                          headers=auth("reviewer"))
    assert r2.status_code == 403
    # The uploaded document shows up in the real documents list.
    listed = anon_client.get("/api/v1/documents", headers=auth("analyst")).json()["documents"]
    assert any(d["name"] == "g.pdf" and d["ext"] == "PDF" for d in listed)
    # Listing requires a session.
    assert anon_client.get("/api/v1/documents").status_code == 401


def test_template_and_line_items_create_and_language_parity(client):
    template = {
        "template_key": "api_tpl",
        "name": "API Template",
        "statements": [{
            "type": "balance_sheet",
            "sections": [{
                "node_id": "cash", "canonical_key": "cash", "label": "Cash", "role": "line",
                "label_i18n": {"en": "Cash", "zh": "现金", "ar": "النقد", "fr": "Trésorerie"},
            }],
        }],
    }
    admin = {"X-Role": "admin"}
    r = client.post("/api/v1/templates", json={"definition": template}, headers=admin)
    assert r.status_code == 201, r.text
    tpl_id = r.json()["id"]

    # Line items IS the configuration engine — there is no second one to publish against. This
    # block posted an ``ontology`` to ``/api/v1/ontologies`` and scoped the parity call with an
    # ``ontology_version_id``; both are gone with the ontology store, so the same parity question
    # is asked of a published LINE-ITEM SET. The alias half of parity is measured over the
    # matcher's working view of this set, so declaring the four locales on one item that the
    # matcher indexes is what makes the four fully supported.
    line_items = {
        "line_items_key": "api_items",
        "target_template_key": "api_tpl",
        "number_format_by_locale": {loc: {} for loc in ("en", "zh", "ar", "fr")},
        "items": [{
            "key": "cash",
            "label": "Cash",
            "aliases_i18n": {"en": ["Cash"], "zh": ["现金"], "ar": ["النقد"], "fr": ["Trésorerie"]},
        }],
    }
    r = client.post("/api/v1/line-items", json={"definition": line_items}, headers=admin)
    assert r.status_code == 201, r.text
    li_id = r.json()["id"]

    r = client.get(f"/api/v1/languages?template_version_id={tpl_id}&line_item_version_id={li_id}")
    assert r.status_code == 200
    body = r.json()
    assert set(body["fully_supported"]) == {"en", "zh", "ar", "fr"}


def test_line_item_set_rejects_unknown_template(client):
    """A configuration that names no stored template cannot be validated, so it cannot publish.

    Was ``test_ontology_rejects_unknown_template``. The item carries an alias so the request gets
    PAST the recognises-nothing door and is refused for the reason under test — the target template.
    """
    line_items = {"line_items_key": "orphan", "target_template_key": "nope",
                  "items": [{"key": "cash", "aliases": ["Cash"]}]}
    r = client.post("/api/v1/line-items", json={"definition": line_items},
                    headers={"X-Role": "admin"})
    assert r.status_code == 422


def test_starting_an_extraction_names_the_progress_mechanism_that_exists(client):
    """The API must not advertise a route it does not serve.

    The 202 body used to carry ``stream_url: /api/v1/extractions/{run_id}/stream`` — a WebSocket
    endpoint that exists nowhere in this codebase. Progress is POLLED (docs/architecture
    06-testing-and-roadmap.md), the prose was corrected to say so, and the false claim survived in
    machine-readable form in the response one file away. So: no key names a stream, and the URL the
    body DOES name answers.
    """
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("poll.pdf", make_native_pdf(),
                                         "application/pdf")}).json()["id"]
    started = client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    assert started.status_code == 202, started.text
    body = started.json()

    assert not [k for k in body if "stream" in k]
    assert not [v for v in body.values() if isinstance(v, str) and v.endswith("/stream")]
    # The mechanism the response names is the one the client polls, and it answers.
    polled = client.get(body["progress_url"])
    assert polled.status_code == 200, body["progress_url"]
    assert polled.json()["run_id"] == body["run_id"]
    # …and the route the old field pointed at is genuinely absent, not merely unmentioned.
    assert client.get(f"/api/v1/extractions/{body['run_id']}/stream").status_code == 404


def test_extraction_confirm_scope_defaults_to_auto(client):
    """confirm_scope defaults to False (auto mode) and round-trips when set."""
    pdf_bytes = make_native_pdf()
    doc_id = client.post(
        "/api/v1/documents", files={"file": ("bs.pdf", pdf_bytes, "application/pdf")}
    ).json()["id"]

    # Default: no confirm_scope in body → auto mode (False).
    r = client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as s:
        assert s.get(ExtractionRun, run_id).options["confirm_scope"] is False

    # Explicit confirm mode round-trips.
    r = client.post(
        f"/api/v1/documents/{doc_id}/extractions", json={"confirm_scope": True}
    )
    assert r.status_code == 202, r.text
    with SessionLocal() as s:
        assert s.get(ExtractionRun, r.json()["run_id"]).options["confirm_scope"] is True
