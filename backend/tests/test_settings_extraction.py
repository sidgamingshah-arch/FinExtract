"""The mapping/reconciliation thresholds are tunable from the front end.

Three things have to hold for that to be safe rather than dangerous:

* a change must actually reach the pipeline — a knob that does not move the code it names is
  worse than no knob, because the screen then misreports what the extraction is doing;
* a value outside a knob's range must be REFUSED, not clamped, for the same reason;
* only an admin may write, though anyone may read (the UI needs the flags).
"""
from __future__ import annotations

import pytest

from app.services.settings_state import reset


@pytest.fixture(autouse=True)
def _restore_settings():
    """Never let one test's threshold leak into another's extraction."""
    yield
    reset()


def _admin(client):
    tok = client.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_every_knob_is_described_well_enough_to_render_and_validate(client):
    """The screen builds its controls from these descriptors, so a knob added on the backend
    appears in the UI with no frontend change — and the bounds the UI enforces are by
    construction the ones the API enforces."""
    body = client.get("/api/v1/settings", headers=_admin(client)).json()

    fields = {f["key"]: f for f in body["extraction_fields"]}
    assert set(fields) == set(body["extraction"]), "described knobs must match reported values"
    assert set(body["extraction_defaults"]) == set(body["extraction"])

    for key, f in fields.items():
        assert f["kind"] in ("number", "bool", "choice"), key
        assert f["label"] and f["help"], key           # every knob explains itself
        if f["kind"] == "number":
            assert f["min"] is not None and f["max"] is not None, key
        if f["kind"] == "choice":
            assert f["choices"], key

    # The thresholds a user would actually come here to change.
    assert "evidence_floor" in fields and "auto_accept_confidence" in fields


def test_an_edit_reaches_the_settings_the_pipeline_reads(client):
    from app.config import get_settings

    res = client.patch("/api/v1/settings", headers=_admin(client),
                       json={"extraction": {"evidence_floor": 0.62}})
    assert res.status_code == 200
    assert res.json()["extraction"]["evidence_floor"] == 0.62
    # …and the live object the mapper is constructed from, not just the response.
    assert get_settings().extraction.evidence_floor == 0.62


def test_the_mapper_honours_a_changed_threshold(client):
    """End of the chain: the matcher reads the edited value, so the next extraction behaves
    differently. Without this the screen would be decorative.

    Observed through the guard the threshold still governs. No tier maps a row on wording, so a
    looser bar can no longer be shown by a near miss becoming a mapping — what it changes is
    whether a caption is recognised as the printed name of a concept the framework COMPUTES, which
    takes the row off the table instead of letting it be re-homed onto a neighbour."""
    from app.config import get_settings
    from app.schemas.ontology import OntologyDefinition, OntologyMapping
    from app.services.mapping import OntologyMatcher

    onto = OntologyDefinition(
        ontology_key="t", target_template_key="t", locale="en",
        mappings=[
            OntologyMapping(canonical_key="pl_top_level__profit_before_tax",
                            label="Profit before tax", aliases=["Profit before tax"]),
            OntologyMapping(canonical_key="pl_top_level__profit_before_exceptional_items_and_tax",
                            label="Profit before exceptional items and tax",
                            aliases=["Profit before exceptional items and tax"],
                            extraction_mode="derive"),
        ])
    caption = "Profit before exceptional items and taxation"

    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"extraction": {"evidence_floor": 0.40, "alias_coverage_floor": 0.30}})
    loose = OntologyMatcher(onto, locale="en", settings=get_settings())
    res = loose.match(caption)
    assert res.computed_claim == "pl_top_level__profit_before_exceptional_items_and_tax"

    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"extraction": {"evidence_floor": 0.99, "alias_coverage_floor": 0.99}})
    strict = OntologyMatcher(onto, locale="en", settings=get_settings())
    # The same caption no longer clears the bar, so nothing claims it either way.
    assert strict.match(caption).computed_claim is None


def test_the_reconciliation_tolerances_reach_the_reconcile_stage(client):
    """These were exposed on the settings screen while the stage still used its dataclass
    defaults — the classic dead knob. The corroboration band is what decides whether a note
    mismatch becomes a review item, so it has to be the configured one."""
    from decimal import Decimal

    from app.config import get_settings
    from app.core.models.document import DocumentModel
    from app.core.models.enums import Basis, LinkRelationship
    from app.core.models.line_item import ExtractedValue, FaceNoteLink, LineItem, NoteItem, NotesTable
    from app.core.stage import PipelineContext
    from app.stages.reconcile import ReconcileStage

    def build():
        face = LineItem(source_label="Trade payables", canonical_key="x")
        face.set_value(ExtractedValue(value=Decimal(1000), value_raw=Decimal(1000),
                                      basis=Basis.CONSOLIDATED, period_label="current"))
        item = NoteItem(raw_label="detail")
        item.set_value(ExtractedValue(value=Decimal(900), value_raw=Decimal(900),
                                      basis=Basis.CONSOLIDATED, period_label="current"))
        note = NotesTable(note_number="9", items=[item])
        doc = DocumentModel(filename="f.pdf")
        doc.line_items = [face]
        doc.notes = [note]
        doc.links = [FaceNoteLink(face_item_id=face.id, notes_table_id=note.id, note_number="9",
                                  relationship=LinkRelationship.ONE_TO_ONE)]
        return doc

    ctx = PipelineContext(raw_bytes=b"")
    ctx.settings = get_settings()

    # Residual is 100 on a face of 1000 — 10%. With the shipped 5% band that is "not a
    # breakdown", so nothing is raised.
    doc = build()
    ReconcileStage().run(doc, ctx)
    assert doc.reconciliation.entries[0].tie_status == "unconfirmed"
    assert doc.reconciliation.failed_assertions == []

    # Widen the band past 10% and the SAME numbers become a reportable discrepancy.
    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"extraction": {"recon_corroboration_rel": 0.20}})
    ctx.settings = get_settings()
    doc = build()
    ReconcileStage().run(doc, ctx)
    assert doc.reconciliation.entries[0].tie_status == "untied"
    assert doc.reconciliation.failed_assertions


@pytest.mark.parametrize("payload,expect", [
    ({"evidence_floor": 1.4}, "evidence_floor"),
    ({"evidence_floor": -0.1}, "evidence_floor"),
    ({"auto_accept_confidence": 2}, "auto_accept_confidence"),
    ({"mapping_scope": "nonsense"}, "mapping_scope"),
])
def test_an_out_of_range_value_is_refused_and_names_the_field(client, payload, expect):
    """Clamping would be worse than refusing: the screen would then show a threshold the
    pipeline is not using."""
    h = _admin(client)
    before = client.get("/api/v1/settings", headers=h).json()["extraction"]
    res = client.patch("/api/v1/settings", headers=h, json={"extraction": payload})
    assert res.status_code == 422
    assert expect in res.json()["detail"]
    assert client.get("/api/v1/settings", headers=h).json()["extraction"] == before


def test_restore_defaults_returns_the_shipped_configuration(client):
    h = _admin(client)
    shipped = client.get("/api/v1/settings", headers=h).json()["extraction_defaults"]

    client.patch("/api/v1/settings", headers=h,
                 json={"extraction": {"evidence_floor": 0.31, "mapping_margin": 0.42}})
    moved = client.get("/api/v1/settings", headers=h).json()["extraction"]
    assert moved["evidence_floor"] == 0.31

    restored = client.patch("/api/v1/settings", headers=h,
                            json={"reset_extraction": True}).json()["extraction"]
    assert restored == shipped


def test_only_an_admin_may_change_the_thresholds(client):
    """Reading is open — the UI needs the flags — but these change how every future extraction
    behaves, so writing is admin-only."""
    tok = client.post("/api/v1/auth/login", json={"username": "analyst"}).json()["token"]
    analyst = {"Authorization": f"Bearer {tok}"}

    assert client.get("/api/v1/settings", headers=analyst).status_code == 200
    assert client.patch("/api/v1/settings", headers=analyst,
                        json={"extraction": {"evidence_floor": 0.1}}).status_code == 403
    # …and nothing moved.
    assert client.get("/api/v1/settings",
                      headers=_admin(client)).json()["extraction"]["evidence_floor"] != 0.1


def test_an_unknown_knob_is_ignored_rather_than_set(client):
    """The patch model is explicit, so an unknown field cannot smuggle a value into settings."""
    h = _admin(client)
    res = client.patch("/api/v1/settings", headers=h,
                       json={"extraction": {"native_min_chars": 1, "evidence_floor": 0.6}})
    assert res.status_code == 200
    assert res.json()["extraction"]["evidence_floor"] == 0.6
    assert "native_min_chars" not in res.json()["extraction"]
    from app.config import get_settings
    assert get_settings().extraction.native_min_chars != 1


def test_every_described_knob_is_actually_settable(client):
    """The guard against the bug this file was written after: the patch model used to be a
    field-per-knob copy of the knob list, and one knob was missing from it — so a PATCH naming
    that knob returned 200 while pydantic silently dropped it, and the screen reported a value
    the pipeline never received. Anything the API describes must be settable through it.
    """
    h = _admin(client)
    body = client.get("/api/v1/settings", headers=h).json()

    for f in body["extraction_fields"]:
        current = body["extraction"][f["key"]]
        if f["kind"] == "bool":
            target = not current
        elif f["kind"] == "choice":
            target = next(c for c in f["choices"] if c != current)
        else:
            # A value inside the bounds that is definitely not the current one.
            lo = f["min"] if f["min"] is not None else 0.0
            hi = f["max"] if f["max"] is not None else 1.0
            target = round(lo + (hi - lo) * 0.3, 4)
            if target == current:
                target = round(lo + (hi - lo) * 0.6, 4)

        res = client.patch("/api/v1/settings", headers=h,
                           json={"extraction": {f["key"]: target}})
        assert res.status_code == 200, (f["key"], res.text)
        assert res.json()["extraction"][f["key"]] == target, (
            f"{f['key']} is described by the API but a PATCH did not change it")
        client.patch("/api/v1/settings", headers=h, json={"reset_extraction": True})
