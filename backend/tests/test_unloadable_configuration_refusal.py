"""A CONFIGURATION whose stored definition will not load is refused where there is a caller to tell.

THE DEFECT. ``_run_extraction_task`` reads the run's configuration resolved and UNGUARDED —
deliberately, because no configuration means no caption resolves to a line item at all — so a stored
definition today's schema refuses raised inside the worker, landed in its ``except BaseException``,
and the run was written `failed` after the recorder had been assembled and the file fetched. The
caller got a 202 and a run id, and the only account of what went wrong was a pydantic error in
``run.logs``. Measured on the workspace database: 41 of the 44 rows in the retired store failed to
load, and the picker offered every one of them — so pinning a broken definition is an ordinary
click, not a contrived request.

``GET /languages`` had the same bare load with no handler anywhere above it, which is a 500.

ONE CONFIGURATION ENGINE, so this file was renamed from ``test_unloadable_rulebook_refusal.py`` and
rewritten against ``line_item_versions``. Nothing about the invariant changed and that is the point:
the store a run pins moved from ``ontology_versions`` to ``line_item_versions``
(``extraction_runs.line_item_version_id``), the probe moved from ``routes.ontologies`` to
``routes.extractions.probe_configuration_load``, and the reason the refusal exists did not move at
all. THIS IS THE HIGHEST-VALUE TEST IN THE SET: it proves a bad configuration does not silently
become "no configuration". Do not soften any assertion here into a smoke test — a 422 that names
the row and the reason is the whole behaviour.

Three things this must NOT do, and each has a test here:

* refuse an ADOPTED run. 18 of the 19 succeeded runs in the workspace database are pinned to a
  configuration that no longer loads; re-opening one of those spreads is a mount POST on the same
  pins, and refusing it would make a past extraction unreadable to punish a definition nothing is
  about to load;
* answer the DEFAULT ``/languages`` call with an all-False parity set, or with a 422 about a stored
  row the caller never named;
* render an unloadable set on the configuration screen as though it were healthy.

And one thing it must do, because the two retired ontology-edit files used to: an inline item edit
publishes a NEW version, that version is what the screen then serves, and it is what the next run
pins. Versioned rather than in-place, because a run references the version it used and must stay
explicable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

# ``inherits`` naming a section this set does not declare is the real fault behind those rows, one
# engine later: it is not a load error on the RAW shape, so the definition stores cleanly, and it is
# fatal on the RESOLVED shape a run matches with — the item ends up with no section gate at all.
# 475 of the shipped set's 475 items take their gate from ``section_defaults`` through ``inherits``,
# so this is the failure a stored configuration is most likely to carry unnoticed.
_SECTION = {"is_pl": {"statement": "profit_and_loss", "section_scope": ["is_pl"]}}
_ITEM = {"key": "is_pl__revenue", "label": "Revenue", "aliases": ["Revenue"],
         # ``internal`` because the target template of these fabricated sets declares no keys of its
         # own for them; the key gate ``/line-items`` applies on publish is exempt by definition for
         # the set's own intermediates, and nothing here is testing that gate.
         "namespace": "internal"}


def _definition(key: str, template_key: str, *, loads: bool) -> dict:
    return {
        "schema_version": 1, "line_items_key": key, "target_template_key": template_key,
        "section_defaults": _SECTION,
        "items": [{**_ITEM, "inherits": "is_pl" if loads else "no_such_section"}],
    }


def _clear_load_cache() -> None:
    """Forget every remembered load verdict.

    TWO CACHES, because there are two probes: ``routes.extractions.probe_configuration_load``
    answers the refusal, and ``routes.line_items.probe_line_item_load`` answers the screen and the
    picker. Both are keyed on (id, created_at) and both are correct to be, because
    ``line_item_versions`` is append-only — every publishing path INSERTs a new row and nothing
    UPDATEs a definition. A test that rewrites a stored definition in place is the one mutation the
    app itself never makes, so it has to say so here rather than be believed.
    """
    from app.api.routes.extractions import _LOADABILITY as _RUN_CACHE
    from app.api.routes.line_items import _LOADABILITY as _SCREEN_CACHE

    _RUN_CACHE.clear()
    _SCREEN_CACHE.clear()


def _seed_template(session) -> tuple[str, str]:
    """A stored template for a fabricated configuration to target. Returns (id, template_key).

    A real shipped definition under a key of this test's own, so ``load_template`` and the publish
    gate see a template they accept while nothing here touches the templates other tests read.

    BACKDATED, and that is not cosmetic. ``resolve_template_id`` defaults an unpinned run to the
    LATEST STORED template by ``created_at``, and this suite shares one database — a fabricated
    template stamped "now" would become the default template for every later test that pins none,
    and hand it a one-item configuration to map a filing against. Every test here pins what it
    means to use, so the old stamp costs this module nothing and cannot leak.
    """
    import json
    from datetime import datetime

    from app.db.models import TemplateVersion
    from app.sample.reference import _TEMPLATE

    key = f"tk-cfg-{uuid.uuid4().hex[:8]}"
    definition = {**json.loads(_TEMPLATE.read_text(encoding="utf-8")), "template_key": key}
    row = TemplateVersion(template_key=key, name=key, version=1, definition=definition,
                          created_at=datetime(2020, 1, 1))
    session.add(row)
    session.flush()
    return row.id, key


def _seed_config(session, *, loads: bool, template_key: str | None = None,
                 version: int = 7) -> tuple[str, str, str]:
    """A stored configuration that does (or does not) load. Returns (id, key, template_key)."""
    from app.db.models import LineItemVersion

    key = f"cfg-{'ok' if loads else 'broken'}-{uuid.uuid4().hex[:8]}"
    template_key = template_key or f"tk-load-{uuid.uuid4().hex[:8]}"
    row = LineItemVersion(line_items_key=key, target_template_key=template_key, version=version,
                          definition=_definition(key, template_key, loads=loads))
    session.add(row)
    session.flush()
    return row.id, key, template_key


def _seed_document(session) -> str:
    """A document that has passed the integrity gate. No pipeline runs in this module — the
    refusal happens before one is started, and the adopt case is proved with a stored run row."""
    from app.config import get_settings
    from app.db.models import Document
    from app.ports.object_store import LocalObjectStore
    from app.services.documents import content_hash

    # Unique bytes per document: (tenant, owner, content_hash) is unique, and two tests seeding the
    # same stub would collide on it.
    data = b"%PDF-1.4 stub that never reaches the pipeline " + uuid.uuid4().hex.encode()
    store = LocalObjectStore(get_settings().object_store_root)
    row = Document(filename="unloadable.pdf", fmt="pdf", byte_size=len(data), page_count=1,
                   content_hash=content_hash(data), object_key=store.put_bytes(data),
                   owner="admin", status="integrity_checked")
    session.add(row)
    session.flush()
    return row.id


def _runs(document_id: str) -> list:
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    with SessionLocal() as session:
        return session.query(ExtractionRun).filter(
            ExtractionRun.document_id == document_id).all()


# --- POST /extractions -------------------------------------------------------------------------

def test_pinning_a_configuration_that_will_not_load_is_refused_before_a_run_is_minted(client):
    """422 at the door, naming the configuration and the first reason it will not load — and NO run.

    The old behaviour was a 202 followed by a `failed` run whose only explanation was a pydantic
    error in its logs, after the pipeline had been assembled and the file fetched.
    """
    from app.db.base import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        doc_id = _seed_document(s)
        cfg_id, key, _ = _seed_config(s, loads=False)
        s.commit()

    refused = client.post(f"/api/v1/documents/{doc_id}/extractions",
                          json={"line_item_version_id": cfg_id})
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    # The error CODE is unchanged: ``routes.documents`` reports the same fault under this string and
    # the frontend keys its message on it. "Rulebook" names no engine a user can choose any more, so
    # there is nothing here for a user to stop seeing.
    assert detail["error"] == "rulebook_unloadable"
    # It names WHICH configuration, so the analyst can pin another one without reading a traceback…
    assert detail["line_item_version_id"] == cfg_id
    assert detail["line_items_key"] == key and detail["version"] == 7
    assert detail["pinned"] is True, "the caller chose this configuration; say so"
    # …and WHY, in one line rather than the whole validation dump.
    assert "inherits" in detail["reason"], detail
    assert "inherits" in detail["message"] and key in detail["message"], detail

    # The refusal is the whole point: nothing was started, so there is no run to poll and no
    # `failed` row for a reader to interpret.
    assert _runs(doc_id) == []


def test_a_defaulted_configuration_that_will_not_load_is_refused_as_defaulted(client):
    """The same refusal for a run that pinned NOTHING, and it must say which of the two it is.

    A run whose configuration was resolved for it (``resolve_configuration_id``) fails just as
    completely as one that pinned a broken set, so the gate reads the RESOLVED id. But a caller who
    pinned nothing must not be told to unpin something — ``pinned`` is False and the message names
    the configuration in force for the template instead.
    """
    from app.db.base import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        doc_id = _seed_document(s)
        tpl_id, tpl_key = _seed_template(s)
        _seed_config(s, loads=False, template_key=tpl_key)
        s.commit()

    refused = client.post(f"/api/v1/documents/{doc_id}/extractions",
                          json={"template_version_id": tpl_id})
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert detail["error"] == "rulebook_unloadable"
    assert detail["pinned"] is False, "nothing was pinned; do not tell the caller to unpin it"
    assert "in force" in detail["message"], detail
    assert _runs(doc_id) == []


def test_a_settled_run_pinned_to_a_configuration_that_no_longer_loads_still_opens(client):
    """THE CASE THE GATE MUST NOT BREAK. A spread extracted while its configuration still loaded is
    re-opened by a mount POST on the same pins; the configuration has since stopped loading (the
    schema tightened under it — that is how 41 stored rows got there). The run is ADOPTED, not
    refused: the figures already exist and nothing is about to load anything.
    """
    from app.db.base import SessionLocal, init_db
    from app.db.models import ExtractionRun, LineItemVersion

    init_db()
    with SessionLocal() as s:
        doc_id = _seed_document(s)
        cfg_id, key, _ = _seed_config(s, loads=True)
        s.add(ExtractionRun(
            id=f"run-adopt-{uuid.uuid4().hex[:10]}", document_id=doc_id, status="succeeded",
            line_item_version_id=cfg_id,
            options={"line_item_version_id": cfg_id, "confirm_scope": False},
            progress={}, result={"rows": []}, created_at=datetime.now(timezone.utc)))
        s.commit()
        run_id = s.query(ExtractionRun).filter(
            ExtractionRun.document_id == doc_id).one().id

    # The schema tightens under the stored row. Rewritten in place — the one mutation the app
    # itself never makes, and exactly what a migration that repaired nothing would leave behind.
    with SessionLocal() as s:
        row = s.get(LineItemVersion, cfg_id)
        row.definition = _definition(key, row.target_template_key, loads=False)
        s.commit()
    # The row kept its id AND its created_at, so the probe would answer from cache; cleared to make
    # the test honest — the gate now really would refuse this configuration.
    from app.api.routes.extractions import probe_configuration_load

    _clear_load_cache()
    with SessionLocal() as s:
        assert probe_configuration_load(s.get(LineItemVersion, cfg_id)) is not None

    adopted = client.post(f"/api/v1/documents/{doc_id}/extractions",
                          json={"line_item_version_id": cfg_id})
    assert adopted.status_code == 202, adopted.text
    body = adopted.json()
    assert body["adopted"] is True and body["run_id"] == run_id, body
    assert body["status"] == "succeeded"
    # And still exactly one run: the gate did not mint a doomed second one either.
    assert len(_runs(doc_id)) == 1

    # Asking for a NEW run of the same filing against that configuration IS refused — `force` says
    # "run it again", and running it again is what cannot work.
    forced = client.post(f"/api/v1/documents/{doc_id}/extractions",
                         json={"line_item_version_id": cfg_id, "force": True})
    assert forced.status_code == 422, forced.text
    assert forced.json()["detail"]["error"] == "rulebook_unloadable"
    assert len(_runs(doc_id)) == 1


# --- GET /line-items ---------------------------------------------------------------------------

def test_the_configuration_screen_refuses_an_unloadable_set_rather_than_rendering_it(client):
    """The screen must never draw a definition that cannot be loaded as though it were healthy.

    That is how 41 unloadable rows went unnoticed in the store this one replaces: the picker served
    every row straight from the raw dict. Same message shape as the extraction refusal, off the same
    kind of probe, so a screen and a run can never disagree about which rows are broken.
    """
    from app.db.base import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        cfg_id, key, tpl_key = _seed_config(s, loads=False)
        s.commit()

    r = client.get(f"/api/v1/line-items?template_key={tpl_key}")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "configuration_unloadable"
    assert detail["id"] == cfg_id and detail["line_items_key"] == key
    assert "inherits" in detail["message"], detail


# --- GET /languages ----------------------------------------------------------------------------

def test_naming_an_unloadable_configuration_to_languages_is_a_422_not_a_500(client):
    """The call site ``loader.unknown_keys`` names as having "no handler at all". A caller that
    NAMED a configuration is told it cannot be read, rather than being handed the pydantic error
    dump as a server error — or, worse, an all-False parity set that reads as a measurement of its
    aliases.
    """
    from app.db.base import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        cfg_id, key, _ = _seed_config(s, loads=False)
        s.commit()

    r = client.get(f"/api/v1/languages?line_item_version_id={cfg_id}")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "rulebook_unloadable"
    assert detail["line_item_version_id"] == cfg_id and detail["line_items_key"] == key
    assert "inherits" in detail["message"], detail


def test_the_default_languages_call_survives_an_unloadable_configuration_in_force(client,
                                                                                  monkeypatch):
    """The default call is not a request ABOUT that row, so it is not refused over it — and it does
    not collapse to all-False either: it falls back to the seeded configuration and still reports
    the real supported set (Req 21), which is what this default exists to do.
    """
    from app.db.base import SessionLocal, init_db

    init_db()
    with SessionLocal() as s:
        cfg_id, _key, _template_key = _seed_config(s, loads=False)
        s.commit()

    from app.db.models import LineItemVersion

    def _in_force(session, key_):  # the configuration in force will not load
        return session.get(LineItemVersion, cfg_id)

    monkeypatch.setattr("app.services.config_select.select_for_template", _in_force)

    body = client.get("/api/v1/languages").json()
    assert body["languages"], "a broken configuration must not empty the registry"
    # The seed fallback carries a real alias set, so English resolves as fully supported exactly
    # as it does with a healthy configuration in force. With no set at all both
    # `has_line_item_aliases` and `has_number_format` would be False and this would fail.
    assert "en" in body["fully_supported"], body
    en = next(l for l in body["languages"] if l["locale"] == "en")
    assert en["has_line_item_aliases"] and en["has_number_format"], en


# --- PATCH /line-items/versions/{id}/items -----------------------------------------------------

def test_an_inline_item_edit_publishes_a_version_the_screen_serves_and_the_next_run_pins(
        client, monkeypatch):
    """The whole of what an inline edit has to be, in one pass — replacing the two retired
    ontology-edit files.

    THREE PROPERTIES, and each is a separate defect if it is missing. The edit must publish a NEW
    version rather than mutate the stored one, because a settled run references the version it used
    (``extraction_runs.line_item_version_id``) and mutating a definition would retroactively change
    how that run is explained. The screen must then SERVE that version, which the retired route
    could not: it read the shipped file off disk, so an inline edit published a row the screen was
    unable to see. And the next run must PIN it, because "the configuration in force" is decided by
    ``config_select`` at start time and a run that does not record which version answered is not
    reproducible.

    The pipeline itself is stubbed out: what is under test is the row the endpoint mints, and the
    document here is a 46-byte stub that no extraction could read anyway.
    """
    from app.db.base import SessionLocal, init_db
    from app.db.models import ExtractionRun, LineItemVersion

    init_db()
    with SessionLocal() as s:
        doc_id = _seed_document(s)
        tpl_id, tpl_key = _seed_template(s)
        cfg_id, key, _ = _seed_config(s, loads=True, template_key=tpl_key)
        s.commit()

    edited = client.patch(f"/api/v1/line-items/versions/{cfg_id}/items",
                          json={"key": _ITEM["key"], "aliases": ["Revenue", "Turnover"]})
    assert edited.status_code == 200, edited.text
    published = edited.json()
    # A NEW ROW at the next version of the SAME key — never an update of the one that was edited.
    assert published["id"] != cfg_id
    assert (published["line_items_key"], published["version"]) == (key, 8)
    with SessionLocal() as s:
        original = s.get(LineItemVersion, cfg_id)
        assert original.definition["items"][0]["aliases"] == ["Revenue"], \
            "the version a past run pinned must be exactly as it was"

    # THE SCREEN SERVES THE EDIT, from the database and not from a file.
    shown = client.get(f"/api/v1/line-items?template_key={tpl_key}")
    assert shown.status_code == 200, shown.text
    payload = shown.json()
    assert payload["version"]["id"] == published["id"]
    assert payload["items"][0]["aliases"] == ["Revenue", "Turnover"]

    # THE NEXT RUN PINS IT. The template is pinned and the configuration is not, so the id on the
    # run is the one ``config_select`` chose — which is what makes this a test of the edit taking
    # effect rather than of the caller's own pin being echoed back.
    monkeypatch.setattr("app.api.routes.extractions._run_extraction_task",
                        lambda *a, **k: None)
    started = client.post(f"/api/v1/documents/{doc_id}/extractions",
                          json={"template_version_id": tpl_id})
    assert started.status_code == 202, started.text
    with SessionLocal() as s:
        run = s.get(ExtractionRun, started.json()["run_id"])
        assert run.line_item_version_id == published["id"]
        assert (run.options or {}).get("line_item_version_id") == published["id"]
    # …and the run says so in the record the Workspace reads, so an analyst can see which
    # configuration produced the figures without querying the database.
    assert started.json()["rulebook"]["version"] == 8
