"""Which template a run lays its spread out on when the caller pins none: THE LATEST ONE STORED.

THE DEFAULT USED TO BE THE SHIPPED TEMPLATE, and that is the defect this module exists for. A run
that pinned nothing resolved against ``shipped_template_key()``, so an uploaded template never became
the default — it had to be pinned on every single run, and any run where that was forgotten laid the
figures out on the shipped HKFRS grid instead. It succeeded, so nothing said otherwise.

Reported by the person it happened to: a template and a 400-concept rulebook uploaded and tested
against for days, while runs that omitted the pin were reading neither.

WHY THIS IS A DEFAULT AND NOT THE SUBSTITUTION THIS CODEBASE REMOVED. ``_template_for_run`` has no
read-time fallback, deliberately: a payload that serves template-derived findings above a band saying
"no template was attached" is self-contradictory, and findings attributed to a template the analyst
never chose are worse than absent ones. That invariant is untouched and the tests below hold it.

What changed is WHEN the question is answered. The template is resolved once, when the run is
created, and PINNED ON THE RUN — the same standard the rulebook default already meets. So the run
records which template shaped its spread, the rulebook record names that template, the screen shows
it, and the analyst can pin a different one. Nothing is guessed at read time.
"""
from __future__ import annotations

import json

import pytest

from tests.fixtures.generate import make_unmapped_row_pdf

pytest.importorskip("fitz")
pytest.importorskip("reportlab")


@pytest.fixture(autouse=True)
def _leave_no_template_behind():
    """Remove every template this module publishes, after each test.

    NOT HOUSEKEEPING — the suite's database is session-scoped, and the behaviour under test here is
    that THE LATEST STORED TEMPLATE IS THE DEFAULT. A template left behind is therefore the default
    for every test that runs afterwards, and this module's first draft broke 35 unrelated tests
    exactly that way.

    Worth stating plainly, because it is a property of the feature and not of the tests: the default
    is global mutable state. Publishing a template changes what the next run of every OTHER document
    lays its spread out on. That is what "latest wins" means, and it is the reason a run pins the
    resolved id at creation rather than resolving it again at read time — an existing spread keeps
    the template it was built on no matter what is published later.
    """
    from app.db.base import SessionLocal
    from app.db.models import TemplateVersion

    with SessionLocal() as session:
        before = {r.id for r in session.query(TemplateVersion).all()}
    yield
    with SessionLocal() as session:
        for row in session.query(TemplateVersion).all():
            if row.id not in before:
                session.delete(row)
        session.commit()


def _publish_template(client, key: str, *, name: str = "") -> dict:
    """A second template under its own key, cloned from the shipped definition.

    Cloned rather than hand-built because the point is which template is CHOSEN, not whether an
    unusual definition parses — and ``POST /templates`` validates what it is given, so a skeleton
    would be refused at the door for reasons that have nothing to do with this module.
    """
    shipped = client.get("/api/v1/templates").json()[0]
    definition = json.loads(json.dumps(
        client.get(f"/api/v1/templates/{shipped['id']}").json()["definition"]))
    definition["template_key"] = key
    definition["name"] = name or key
    r = client.post("/api/v1/templates",
                    json={"template_key": key, "name": name or key, "definition": definition})
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, options: dict | None = None) -> dict:
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("bs.pdf", make_unmapped_row_pdf(),
                                         "application/pdf")}).json()["id"]
    client.post(f"/api/v1/documents/{doc_id}/extractions", json=options or {})
    return client.get(f"/api/v1/documents/{doc_id}/run").json()


def _stored(run_id: str) -> tuple:
    """(column, option, template_key) for a run, read off the ROW.

    The served payload does not expose either id — deliberately, it serves the template's CONTENT
    (`statements`) rather than its identity — so the contract this module is about lives on the row,
    which is exactly where ``_run_template_id`` reads it.
    """
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun, TemplateVersion

    with SessionLocal() as session:
        run = session.get(ExtractionRun, run_id)
        col = run.template_version_id
        opt = (run.options or {}).get("template_version_id")
        row = session.get(TemplateVersion, col) if col else None
        return col, opt, (row.template_key if row is not None else None)


# --- the default -----------------------------------------------------------------------------------

def test_a_run_that_pins_nothing_uses_the_latest_template_not_the_shipped_one(client):
    """THE REPORTED DEFECT, end to end. Publish a template of your own and it is what the next run
    lays its spread out on — without pinning it, which is what the upload screen sends."""
    from app.sample.reference import shipped_template_key

    mine = _publish_template(client, "my_csv_spread", name="My CSV spread")
    assert shipped_template_key() != "my_csv_spread", "the fixture must not be the shipped key"

    run = _run(client)                                   # no pins at all
    col, opt, key = _stored(run["run_id"])

    assert key == "my_csv_spread", "the run laid its spread out on the shipped template"
    assert col == mine["id"] and opt == mine["id"]


def test_the_resolver_prefers_the_newest_version_of_any_template(client):
    """"Latest" is the newest VERSION OF ANY TEMPLATE, not the newest key to appear.

    Re-publishing a revision of an older template makes that template current again, which is what
    an author editing a spread means by publishing it. Asserted with THREE publishes so the two
    readings diverge: by newest key the answer would be ``second``, by newest version it is
    ``first`` — which is republished last.
    """
    from app.api.routes.extractions import resolve_template_id
    from app.db.base import SessionLocal
    from app.db.models import TemplateVersion

    _publish_template(client, "first_spread")
    _publish_template(client, "second_spread")
    _publish_template(client, "first_spread")          # a second version of the FIRST key

    with SessionLocal() as session:
        tid = resolve_template_id(session, None, None)
        row = session.get(TemplateVersion, tid)

    assert row.template_key == "first_spread", "the newest KEY won instead of the newest version"
    assert row.version == 2


def test_a_caller_pin_always_wins(client):
    """Only the ABSENCE of a pin is filled in. Reproducing an earlier spread on an older template
    has to stay possible, or the default becomes a substitution."""
    from app.api.routes.extractions import resolve_template_id
    from app.db.base import SessionLocal

    shipped_id = client.get("/api/v1/templates").json()[0]["id"]
    _publish_template(client, "newer_spread")

    with SessionLocal() as session:
        assert resolve_template_id(session, shipped_id, None) == shipped_id


def test_a_pinned_rulebook_decides_its_own_template(client):
    """Defaulting the template must not MANUFACTURE the mismatch ``start_extraction`` refuses.

    A caller who pins a rulebook and no template has named one half of the pair. Taking the newest
    template overall would pair that rulebook with a template it is not written for — a 422 about a
    template the caller never chose. So the default is the newest version of the template the
    rulebook TARGETS, and the pair agrees by construction.
    """
    from app.api.routes.extractions import resolve_template_id
    from app.db.base import SessionLocal
    from app.db.models import TemplateVersion

    ont = next(o for o in client.get("/api/v1/ontologies").json()
               if o["ontology_key"] == "hkfrs_hk_china")
    _publish_template(client, "unrelated_spread")      # newest overall, wrong target

    with SessionLocal() as session:
        tid = resolve_template_id(session, None, ont["id"])
        row = session.get(TemplateVersion, tid)

    assert row.template_key == ont["target_template_key"]
    assert row.template_key != "unrelated_spread"


def test_the_run_records_the_template_it_resolved(client):
    """WHAT MAKES THIS A DEFAULT RATHER THAN A SILENT SUBSTITUTION. The run stores the resolved id in
    both places ``_run_template_id`` reads, so no reader of one payload can answer "no template"
    while another serves findings from one."""
    from app.api.routes.documents import _run_template_id
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    mine = _publish_template(client, "recorded_spread")
    run = _run(client)
    col, opt, key = _stored(run["run_id"])

    # BOTH places, because either can be the only one a caller populates and the two readers of
    # this question must never disagree (see ``_run_template_id``).
    assert col == mine["id"], "the column does not name the resolved template"
    assert opt == mine["id"], "the options do not name the resolved template"
    assert key == "recorded_spread"
    with SessionLocal() as session:
        assert _run_template_id(session.get(ExtractionRun, run["run_id"])) == mine["id"]


def test_the_worker_lays_the_spread_out_on_the_resolved_template(client):
    """The id has to reach the WORKER, not just the row. The worker reads
    ``options["template_version_id"]``; a run that names a template and does not use it is worse
    than one that names none."""
    mine = _publish_template(client, "worker_spread")
    run = _run(client)
    col, opt, _key = _stored(run["run_id"])

    assert opt == mine["id"] == col, \
        "the worker was handed a different template from the one the run records"
    # And the rulebook the run settled on is the one in force for THAT template, so the pair the
    # run actually used cannot disagree. No rulebook targets this brand-new key, so the honest
    # answer is an empty record rather than the shipped rulebook silently paired with it.
    assert run["result"]["rulebook"]["target_template_key"] in ("worker_spread", "")


# --- the invariant this must not break -------------------------------------------------------------

def test_a_run_naming_no_template_is_still_given_none_at_read_time(client):
    """FINDING E, AND IT STANDS. The resolver runs when a run is CREATED; it is not a read-time
    fallback. A run that genuinely names no template — an older run, or one built straight from
    options, as several callers and fixtures do — must still resolve to None, so the coverage band
    and the check builders cannot disagree about whether a template was attached.
    """
    from app.api.routes.documents import _run_template_id, _template_for_run
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    _publish_template(client, "available_to_fall_back_to")   # so the assertion is not vacuous

    with SessionLocal() as session:
        run = ExtractionRun(id="run-no-template", document_id="doc-x", status="succeeded",
                            options={}, result={})
        session.add(run)
        session.commit()
        assert _run_template_id(run) is None
        assert _template_for_run(session, run) is None


def test_nothing_stored_resolves_to_none_rather_than_inventing_a_template(client):
    """The template is optional. With no template stored at all the answer is None, not an error and
    not a fabrication — and every caller already handles None."""
    from app.api.routes.extractions import resolve_template_id
    from app.db.base import SessionLocal
    from app.db.models import TemplateVersion

    with SessionLocal() as session:
        # THE IDS ARE PART OF THE STATE TO RESTORE, not an incidental. Recreating these rows with
        # fresh ids left the database looking correct and then emptied it anyway: the module's
        # cleanup fixture deletes every template whose id it did not see BEFORE the test, so
        # restored-with-new-ids rows were treated as this test's own litter and swept. Every test
        # after this one then ran against a product with no template at all — 35 failures, all of
        # them reading "the shipped template is not stored".
        kept = [{"id": r.id, "template_key": r.template_key, "name": r.name, "version": r.version,
                 "definition": r.definition, "is_published": r.is_published,
                 "created_at": r.created_at}
                for r in session.query(TemplateVersion).all()]
        session.query(TemplateVersion).delete()
        session.commit()
        try:
            assert resolve_template_id(session, None, None) is None
        finally:
            for row in kept:
                session.add(TemplateVersion(**row))
            session.commit()
