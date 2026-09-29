"""Review-step toggle + editable LLM config (admin runtime settings)."""
from __future__ import annotations


def test_review_toggle_switches_analyst_finalize(client, auth, anon_client):
    """review_required governs the analyst's finalize vs. submit — but never the QA screen."""
    from app.services import settings_state

    try:
        # Default: review required → analyst submits for review, cannot deliver.
        me = anon_client.get("/api/v1/me", headers=auth("analyst")).json()
        assert "review:submit" in me["permissions"]
        assert "export:run" not in me["permissions"]
        assert "review" in me["screens"]  # human-in-the-loop QA available

        # Admin turns the reviewer sign-off OFF.
        r = client.patch("/api/v1/settings", json={"review_required": False})
        assert r.status_code == 200, r.text
        assert r.json()["features"]["review_required"] is False

        # Now the workflow closes at the analyst: they finalize & export directly.
        me2 = anon_client.get("/api/v1/me", headers=auth("analyst")).json()
        assert "export:run" in me2["permissions"]
        assert "review:submit" not in me2["permissions"]
        assert "review" in me2["screens"]  # QA screen STILL there
    finally:
        settings_state.reset()


def test_submit_for_review_requires_review_on(client, auth, anon_client):
    from app.services import settings_state

    try:
        # Review on (default): analyst can submit for review.
        r = anon_client.post("/api/v1/projects/demo/submit-review", headers=auth("analyst"))
        assert r.status_code == 200, r.text
        assert r.json()["entry"]["action"] == "submit_review"

        # Review off: the submit action is no longer available to the analyst.
        client.patch("/api/v1/settings", json={"review_required": False})
        r2 = anon_client.post("/api/v1/projects/demo/submit-review", headers=auth("analyst"))
        assert r2.status_code == 403
    finally:
        settings_state.reset()


def test_the_llm_cannot_be_edited_from_the_settings_api(client):
    """The LLM is defined only in config.toml [llm]; an admin edit — key included — is refused."""
    from app.config import get_settings

    before = get_settings().llm.provider
    r = client.patch("/api/v1/settings", json={"llm": {
        "provider": "openai", "model": "moonshotai/kimi-k3-free",
        "api_key": "sk-should-be-ignored"}})
    assert r.status_code == 400 and "config.toml" in r.json()["detail"]
    assert get_settings().llm.provider == before
    assert not hasattr(get_settings().llm, "api_key")


def test_llm_config_edit_requires_admin(auth, anon_client):
    r = anon_client.patch("/api/v1/settings", json={"llm": {"model": "x"}}, headers=auth("analyst"))
    assert r.status_code == 403


_SEED_RUN_ID = "reliance-industries-ltd-20250731-094212"  # a seeded audit entry


def test_greenfield_empty_until_sample_loaded(client, auth, anon_client):
    """Default is greenfield: the project reports loaded=false and screens get empty data.
    An admin loading the sample repopulates everything."""
    from app.services import settings_state

    try:
        settings_state.set_seed_demo(False)
        hdr = auth("admin")
        proj = anon_client.get("/api/v1/projects/demo", headers=hdr).json()
        assert proj["loaded"] is False
        assert proj["project"]["title"] == "No project yet" and proj["documents"] == []
        assert anon_client.get("/api/v1/projects/demo/statements/balance_sheet", headers=hdr).json()["rows"] == []
        assert anon_client.get("/api/v1/projects/demo/notes", headers=hdr).json()["notes"] == []
        green_audit = anon_client.get("/api/v1/projects/demo/audit", headers=hdr).json()["entries"]
        assert all(e["run_id"] != _SEED_RUN_ID for e in green_audit)  # no seeded rows

        # Admin loads the sample project at runtime.
        r = client.patch("/api/v1/settings", json={"seed_demo": True})
        assert r.status_code == 200 and r.json()["features"]["seed_demo"] is True
        proj2 = anon_client.get("/api/v1/projects/demo", headers=hdr).json()
        assert proj2["loaded"] is True and proj2["project"]["title"]
        assert len(anon_client.get("/api/v1/projects/demo/statements/balance_sheet", headers=hdr).json()["rows"]) > 0
        loaded_audit = anon_client.get("/api/v1/projects/demo/audit", headers=hdr).json()["entries"]
        assert any(e["run_id"] == _SEED_RUN_ID for e in loaded_audit)
    finally:
        settings_state.set_seed_demo(True)


def test_submitting_a_real_document_names_that_documents_entity(anon_client, auth):
    """THE DEFECT THIS CLOSES: submitting an uploaded filing wrote an audit entry naming the DEMO
    company. The Export screen drives an uploaded document and the seeded sample from the same
    button, and the route hardcoded ``DEMO["project"]["entity"]`` — so the audit trail recorded a
    submission that happened, for a company nobody submitted. That is worse than no audit trail:
    the entry looks authoritative and names the wrong entity.

    The name comes from the run's own ``result["entity"]``, which is what the extraction read off
    the filing. Written directly here rather than extracted, because what is under test is which
    entity the audit entry carries.
    """
    from app.db.base import SessionLocal
    from app.db.models import Document, ExtractionRun

    with SessionLocal() as s:
        doc = Document(filename="acme-annual.pdf", fmt="pdf", byte_size=1,
                       object_key="audit/acme.pdf", content_hash="audit-real-entity",
                       owner="analyst")
        s.add(doc)
        s.flush()
        run = ExtractionRun(document_id=doc.id, status="succeeded",
                            result={"entity": "Acme Holdings Limited"})
        s.add(run)
        s.commit()
        doc_id, run_id = doc.id, run.id
    try:
        r = anon_client.post(f"/api/v1/projects/demo/submit-review?document_id={doc_id}",
                             headers=auth("analyst"))
        assert r.status_code == 200, r.text
        entry = r.json()["entry"]
        assert entry["entity"] == "Acme Holdings Limited"
        assert entry["action"] == "submit_review"
        # The entry points at the run that produced the figures, so the submission is traceable to
        # the extraction it is a submission OF.
        assert entry["run_id"] == run_id
        # And the demo name is nowhere near it.
        from app.sample.demo import DEMO
        assert entry["entity"] != DEMO["project"]["entity"]
    finally:
        with SessionLocal() as s:
            s.delete(s.get(ExtractionRun, run_id))
            s.delete(s.get(Document, doc_id))
            s.commit()


def test_submitting_a_document_with_no_extracted_entity_is_refused(anon_client, auth):
    """No entity means no honest submission. Falling back to the demo name is the defect above, so
    an un-extracted document is refused and told why rather than recorded under a borrowed name."""
    from app.db.base import SessionLocal
    from app.db.models import Document

    with SessionLocal() as s:
        doc = Document(filename="not-yet-run.pdf", fmt="pdf", byte_size=1,
                       object_key="audit/not-yet-run.pdf", content_hash="audit-no-entity",
                       owner="analyst")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    try:
        r = anon_client.post(f"/api/v1/projects/demo/submit-review?document_id={doc_id}",
                             headers=auth("analyst"))
        assert r.status_code == 422, r.text
        assert "not-yet-run.pdf" in str(r.json()["detail"])
    finally:
        with SessionLocal() as s:
            s.delete(s.get(Document, doc_id))
            s.commit()


def test_submitting_another_owners_document_is_not_found(anon_client, auth):
    """404, not 403, and by the same predicate every other document read uses: whether a document
    exists must not leak across tenants. An analyst is not elevated, so another analyst's filing is
    simply absent."""
    from app.db.base import SessionLocal
    from app.db.models import Document

    with SessionLocal() as s:
        doc = Document(filename="someone-else.pdf", fmt="pdf", byte_size=1,
                       object_key="audit/someone-else.pdf", content_hash="audit-other-owner",
                       owner="another-analyst")
        s.add(doc)
        s.commit()
        doc_id = doc.id
    try:
        r = anon_client.post(f"/api/v1/projects/demo/submit-review?document_id={doc_id}",
                             headers=auth("analyst"))
        assert r.status_code == 404, r.text
    finally:
        with SessionLocal() as s:
            s.delete(s.get(Document, doc_id))
            s.commit()
