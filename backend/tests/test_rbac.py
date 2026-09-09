"""RBAC + session auth: login, role→permission enforcement, and /me."""
from __future__ import annotations


def test_me_requires_authentication(anon_client):
    # No session token and no role header → 401.
    assert anon_client.get("/api/v1/me").status_code == 401


def test_login_issues_token_and_scopes_analyst(anon_client):
    r = anon_client.post("/api/v1/auth/login", json={"username": "analyst"})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    assert r.json()["user"]["role"] == "analyst"

    me = anon_client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["authenticated"] is True and me["role"] == "analyst" and me["via"] == "session"
    assert "workspace" in me["screens"] and "commentary" in me["screens"]
    # Configuration screens (Template & Line Items authoring, Settings) are admin-only. The
    # analyst still selects a template on the Upload screen (template:select), but the
    # authoring/config screen is hidden from their nav.
    #
    # ``config:line_items`` is the mapping-configuration permission. It was ``config:ontology``
    # while the ontology was a second, selectable configuration surface; the line-item set is the
    # single configuration engine now, so the permission names it and the old string is not served
    # to any role (see tests/test_retired_ontology_path.py).
    assert "template" not in me["screens"] and "settings" not in me["screens"]
    assert "template:select" in me["permissions"]
    assert "config:template" not in me["permissions"]
    assert "config:line_items" not in me["permissions"]


def test_login_rejects_bad_password(anon_client):
    # A wrong (non-empty) password is rejected even in demo mode.
    r = anon_client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_logout_invalidates_token(anon_client):
    tok = anon_client.post("/api/v1/auth/login", json={"username": "reviewer"}).json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    assert anon_client.get("/api/v1/me", headers=hdr).status_code == 200
    anon_client.post("/api/v1/auth/logout", headers=hdr)
    assert anon_client.get("/api/v1/me", headers=hdr).status_code == 401


def test_me_admin_sees_config(auth, anon_client):
    me = anon_client.get("/api/v1/me", headers=auth("admin")).json()
    assert me["role"] == "admin"
    assert "template" in me["screens"] and "settings" in me["screens"]
    assert "config:template" in me["permissions"] and "config:settings" in me["permissions"]
    # The mapping configuration, under its only name. Authoring the line-item set is admin work.
    assert "config:line_items" in me["permissions"]


def test_reviewer_finalizes_but_does_not_configure(auth, anon_client):
    me = anon_client.get("/api/v1/me", headers=auth("reviewer")).json()
    # Reviewer works from an already-configured extraction — no template screen at all.
    assert "review" in me["screens"] and "template" not in me["screens"]
    # Reviewer reviews & finalizes and can deliver, but does no configuration.
    assert "review:finalize" in me["permissions"]
    assert "export:run" in me["permissions"]
    assert "config:template" not in me["permissions"]
    assert "config:line_items" not in me["permissions"]
    assert "config:scope" not in me["permissions"]
    assert "documents:manage" not in me["permissions"]


def test_analyst_runs_pipeline_and_submits(auth, anon_client):
    me = anon_client.get("/api/v1/me", headers=auth("analyst")).json()
    for p in ("documents:manage", "template:select", "pipeline:run", "review:submit"):
        assert p in me["permissions"], p
    # The human-in-the-loop Review Queue (QA checks + low-confidence) stays available to
    # the analyst regardless of the reviewer sign-off flag.
    for scr in ("upload", "scope", "workspace", "review", "export"):
        assert scr in me["screens"], scr
    # With review required (default), the analyst submits for review — cannot deliver.
    assert "export:run" not in me["permissions"]


def test_demo_users_listed_without_secrets(anon_client):
    body = anon_client.get("/api/v1/auth/demo-users").json()
    roles = {u["role"] for u in body["users"]}
    assert roles == {"admin", "reviewer", "analyst"}
    assert all("password" not in u for u in body["users"])


def test_template_read_is_open_but_authoring_requires_admin(auth, anon_client):
    # Viewing the template tree is reference info for any authenticated worker:
    # unauthenticated → 401; analyst/reviewer/admin → 200.
    assert anon_client.get("/api/v1/projects/demo/template").status_code == 401
    assert anon_client.get("/api/v1/projects/demo/template", headers=auth("analyst")).status_code == 200
    assert anon_client.get("/api/v1/projects/demo/template", headers=auth("reviewer")).status_code == 200
    assert anon_client.get("/api/v1/projects/demo/template", headers=auth("admin")).status_code == 200

    # Authoring a template stays admin-only: anon → 401, analyst → 403, admin → 201.
    tpl = {"template_key": "rbac_t", "name": "T", "statements": []}
    assert anon_client.post("/api/v1/templates", json={"definition": tpl}).status_code == 401
    assert anon_client.post("/api/v1/templates", json={"definition": tpl},
                            headers=auth("analyst")).status_code == 403
    assert anon_client.post("/api/v1/templates", json={"definition": tpl},
                            headers=auth("admin")).status_code == 201


def test_edit_allowed_for_working_roles(auth, anon_client):
    # analyst may edit values (simple flow); the mutation is permitted.
    r = anon_client.patch("/api/v1/projects/demo/line-items/ppe",
                          json={"value": 423180, "formula": ""}, headers=auth("analyst"))
    assert r.status_code == 200
    anon_client.delete("/api/v1/projects/demo/line-items/ppe", headers=auth("analyst"))


def test_role_header_ignored_by_default(anon_client):
    # allow_role_header is OFF by default → the X-Role dev header is not accepted (401).
    assert anon_client.get("/api/v1/me", headers={"X-Role": "admin"}).status_code == 401


def test_session_is_authoritative_over_role_header(auth, anon_client, monkeypatch):
    # Even with the dev header enabled, a valid session cannot be escalated by X-Role:
    # the analyst's bearer token wins over "X-Role: admin".
    from app.config import get_settings

    monkeypatch.setattr(get_settings().auth, "allow_role_header", True)
    hdr = {**auth("analyst"), "X-Role": "admin"}
    me = anon_client.get("/api/v1/me", headers=hdr).json()
    assert me["role"] == "analyst" and me["via"] == "session"
    # The header only acts as a fallback when there is NO session.
    me2 = anon_client.get("/api/v1/me", headers={"X-Role": "admin"}).json()
    assert me2["role"] == "admin" and me2["via"] == "role-header"


def test_a_reviewer_can_read_an_analysts_run_including_its_stages_and_log(auth, anon_client):
    """The reviewer is GRANTED the extraction screen without ``pipeline:run`` — read a run, don't
    start one — and ``rbac.SCREENS_BY_ROLE`` says so in a comment naming what they read: "its stages,
    its log, its rows". This pins that claim, because two separate gates have to hold for it and
    neither is obvious from the grant: ``EXTRACTION_VIEW`` (the reviewer holds it) AND the OWNERSHIP
    predicate on the run's document (``documents._can_access``), which the reviewer clears only
    because reviewers are elevated — they work every analyst's queue. A comment that promises a
    reader something the authorization path refuses is worse than no comment.

    The run is written directly rather than extracted: what is under test is the authorization of a
    read, not the pipeline, and a run owned by SOMEONE ELSE is the whole point.
    """
    from datetime import datetime, timezone

    from app.api.routes.extractions import _progress_payload
    from app.db.base import SessionLocal
    from app.db.models import Document, ExtractionRun

    with SessionLocal() as s:
        doc = Document(filename="reviewer-reads.pdf", fmt="pdf", byte_size=1,
                       object_key="rbac/reviewer-reads.pdf",
                       content_hash="rbac-reviewer-read", owner="analyst")
        s.add(doc)
        s.flush()
        # Built by the pipeline's OWN writer, so the record satisfies the served contract
        # (`_served_progress` refuses a partial one, correctly) and this test stays a statement
        # about authorization rather than about the progress shape.
        progress = _progress_payload("structural", 100.0,
                                     started_at=datetime.now(timezone.utc), stage_count=2,
                                     stages_done=["ingest", "structural"])
        run = ExtractionRun(document_id=doc.id, status="succeeded", progress=progress,
                            logs="ingest: ok\nstructural: ok",
                            options={"stages": ["ingest", "structural"]})
        s.add(run)
        s.commit()
        doc_id, run_id = doc.id, run.id
    try:
        got = anon_client.get(f"/api/v1/extractions/{run_id}", headers=auth("reviewer"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["progress"] is not None          # the stage progress the screen ticks off
        assert body["stages"] == ["ingest", "structural"]
        assert body["log_tail"]                       # what the pipeline said while it worked
        # And the reviewer cannot START one: the grant is the screen, not the permission.
        me = anon_client.get("/api/v1/me", headers=auth("reviewer")).json()
        assert "extraction" in me["screens"]
        assert "pipeline:run" not in me["permissions"]
    finally:
        with SessionLocal() as s:
            s.delete(s.get(ExtractionRun, run_id))
            s.delete(s.get(Document, doc_id))
            s.commit()
