"""The shipped template + rulebook must reach a RUNNING app, not only a fresh one.

``ensure_reference_data`` used to write a version 1 when no version of the key existed and never
look at the files again, so every revision made after a database's first startup was invisible to
the app using that database. The suite could not see it: ``conftest.py`` points
``FINEX_DATABASE_URL`` at a fresh temp database, so pytest always seeded the current file. The
reader's symptom was "Gross Profit and the other calculated totals still render at the END of the
template" — true of the definition the app was serving, which was the pre-revision profit-and-loss
with every calculated total bunched at the bottom.

Every test here builds its OWN database from ``Base.metadata`` and calls the function directly. Not
the session-scoped ``client`` fixture, for two reasons: this is a test about what happens on the
SECOND boot against an existing database, which the shared fixture (seeded once, at startup) cannot
express; and publishing rival rulebook versions into the shared database would change which rulebook
every later test file finds in force.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


@pytest.fixture()
def session(tmp_path):
    """A database of this test's own, at the current model."""
    from app.db import models  # noqa: F401 — registers the tables on Base.metadata
    from app.db.base import Base

    engine = create_engine(f"sqlite:///{tmp_path}/reference.db", future=True)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _shipped_template() -> dict:
    from app.sample import reference

    return json.loads(reference._TEMPLATE.read_text(encoding="utf-8"))


def _shipped_ontology() -> dict:
    from app.sample import reference

    return json.loads(reference._ONTOLOGY.read_text(encoding="utf-8"))


def _pl_order(definition: dict) -> list[str]:
    """The profit-and-loss statement's TOP-LEVEL node ids, in template order.

    This is the order the statement builder emits and the screen renders, so it is the shape of the
    reader's complaint: a stale definition puts every calculated total after every section.
    """
    for statement in definition.get("statements") or []:
        if statement.get("type") == "profit_and_loss":
            return [s.get("node_id") for s in statement.get("sections") or []]
    return []


def _totals_last(definition: dict) -> dict:
    """The shipped template with the P&L's calculated totals moved to the END.

    A copy of the drift found in the real ``backend/finex.db``: the sections in order, then
    ``pl_gross_profit``, ``pl_operating_profit_ebit`` and the other totals bunched behind them.
    Built by MOVING the shipped file's own nodes rather than by pasting the stale definition in, so
    the fixture cannot rot away from the file it is a stale version of.
    """
    out = json.loads(json.dumps(definition))
    for statement in out["statements"]:
        if statement.get("type") != "profit_and_loss":
            continue
        sections = statement["sections"]
        statement["sections"] = ([s for s in sections if s.get("children")]
                                + [s for s in sections if not s.get("children")])
    return out


# THE FAMILY THIS MODULE IS ABOUT. The repository ships two reference sets now — the original
# hkfrs_hk_china and the China/PRC output_csv_hk — so "the stored templates" and "the stored
# rulebooks" stopped being single answers, and assertions written when there was one family were
# reading the other one's rows as extra versions of theirs. Every query below is scoped to this
# family, which is what these tests actually exercise: the refresh, supersession and
# retired-key handling of the shipped hkfrs set.
SUITE_TEMPLATE_KEY = "hkfrs_hk_china_v1"
SUITE_ONTOLOGY_PREFIX = "hkfrs_hk_china"


def _rows(session: Session, model, **where) -> list:
    """The stored rows of one model, ordered by version — scoped to this module's family.

    Scoping lives here rather than at each call site so a third reference set cannot quietly
    reintroduce the same class of failure at whichever call site was missed.
    """
    stmt = select(model).order_by(model.version)
    for column, value in where.items():
        stmt = stmt.where(getattr(model, column) == value)
    if "template_key" not in where and hasattr(model, "template_key"):
        stmt = stmt.where(model.template_key == SUITE_TEMPLATE_KEY)
    if "ontology_key" not in where and hasattr(model, "ontology_key"):
        stmt = stmt.where(model.ontology_key.startswith(SUITE_ONTOLOGY_PREFIX))
    return list(session.execute(stmt).scalars().all())


def _insert_ontology(session: Session, definition: dict, *, key: str, supersedes: str | None = None,
                     version: int = 1, created_at: datetime | None = None):
    """One stored rulebook, written straight to the table — as an upload or an old seed left it."""
    from app.db.models import OntologyVersion

    body = json.loads(json.dumps(definition))
    body["ontology_key"] = key
    metadata = dict(body.get("metadata") or {})
    metadata.pop("supersedes", None)
    if supersedes:
        metadata["supersedes"] = supersedes
    body["metadata"] = metadata
    row = OntologyVersion(ontology_key=key, target_template_key=body["target_template_key"],
                          version=version, definition=body)
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    session.commit()
    return row


# --- refreshing ---------------------------------------------------------------------------------

def test_an_empty_database_is_seeded_at_version_1(session):
    """Unchanged behaviour: nothing stored means the shipped pair is published as version 1."""
    from app.db.models import OntologyVersion, TemplateVersion
    from app.sample.reference import ensure_reference_data

    tpl, ont = _shipped_template(), _shipped_ontology()
    notes = ensure_reference_data(session)

    templates = _rows(session, TemplateVersion)
    assert [(r.template_key, r.version, r.is_published) for r in templates] == \
        [(tpl["template_key"], 1, True)]
    assert templates[0].definition == tpl
    ontologies = _rows(session, OntologyVersion)
    assert [(r.ontology_key, r.version) for r in ontologies] == [(ont["ontology_key"], 1)]
    assert ontologies[0].definition == ont
    # IT SAYS WHAT IT DID, for the reconcile script to print — one note per artefact it
    # published. Counted against the rows rather than fixed at 2: that literal was the number of
    # reference sets the repository happened to ship, so adding the China/PRC set broke an
    # assertion about note-keeping. The invariant is the pairing, not the cardinality.
    published = (session.execute(select(func.count()).select_from(TemplateVersion)).scalar()
                 + session.execute(select(func.count()).select_from(OntologyVersion)).scalar())
    assert len(notes) == published, notes
    assert all("published v1" in note for note in notes), notes


def test_a_second_call_against_an_unchanged_file_publishes_nothing(session):
    """The property that lets this run on every boot.

    Without it the refresh is a version-number pump: one row per restart, each identical to the
    last, every one of them deserialized by ``GET /ontologies`` on every request to that screen.
    """
    from app.db.models import OntologyVersion, TemplateVersion
    from app.sample.reference import ensure_reference_data

    ensure_reference_data(session)
    before = {m: session.execute(select(func.count()).select_from(m)).scalar()
              for m in (TemplateVersion, OntologyVersion)}

    assert ensure_reference_data(session) == []
    assert {m: session.execute(select(func.count()).select_from(m)).scalar()
            for m in (TemplateVersion, OntologyVersion)} == before
    # …and the first call DID publish, so the equality above is not two zeros agreeing. Stated as
    # "some" rather than as one each: the count is how many reference sets the repository ships,
    # which is not what idempotence is about and moved when the China/PRC set was added.
    assert all(count > 0 for count in before.values()), before


def test_a_stale_stored_template_is_refreshed_to_the_shipped_order(session, caplog):
    """THE DEFECT THIS UNIT EXISTS FOR, in the reader's own terms.

    Seed, drift the stored definition to the shape the real database holds (calculated totals last),
    call again. A seed-once function finds a version of the key present, writes nothing, and the app
    goes on serving the stale order for as long as that database lives.
    """
    from app.db.models import TemplateVersion
    from app.sample.reference import ensure_reference_data

    shipped = _shipped_template()
    ensure_reference_data(session)
    stored = _rows(session, TemplateVersion)[0]
    stored.definition = _totals_last(stored.definition)      # a NEW dict: a JSON column tracks no
    session.commit()                                         # in-place mutation of the old one
    assert _pl_order(stored.definition) != _pl_order(shipped)

    with caplog.at_level(logging.WARNING, logger="app.sample.reference"):
        notes = ensure_reference_data(session)
    # Superseding a stored definition is reported, at a level this app's absent logging config still
    # lets through: what it replaces can be an edit somebody made, and a silent replacement of a
    # user's edit is the one outcome nobody would think to look for.
    assert any("published as v2" in r.getMessage() for r in caplog.records), caplog.text

    versions = _rows(session, TemplateVersion)
    assert [r.version for r in versions] == [1, 2]
    assert versions[-1].definition == shipped
    assert _pl_order(versions[-1].definition) == _pl_order(shipped)
    # The complaint in one assertion: gross profit sits under the cost of sales it is computed from,
    # not behind every section of the statement.
    order = _pl_order(versions[-1].definition)
    assert order.index("pl_gross_profit") < order.index("pl_s2_expenses")
    assert any("published v2" in n for n in notes), notes


def test_a_stale_stored_rulebook_is_refreshed_to_the_shipped_concepts(session):
    """The same refresh for the rulebook, whose drift was 173 concepts against the shipped 185."""
    from app.db.models import OntologyVersion
    from app.sample.reference import ensure_reference_data

    shipped = _shipped_ontology()
    ensure_reference_data(session)
    stored = _rows(session, OntologyVersion)[0]
    stale = json.loads(json.dumps(stored.definition))
    stale["mappings"] = stale["mappings"][:-12]
    stale["metadata"] = {**stale["metadata"], "concept_count": len(stale["mappings"])}
    stored.definition = stale
    session.commit()

    ensure_reference_data(session)

    versions = _rows(session, OntologyVersion)
    assert [r.version for r in versions] == [1, 2]
    assert versions[-1].definition == shipped
    assert len(versions[-1].definition["mappings"]) == len(shipped["mappings"])


def test_a_lost_race_to_publish_does_not_fail_the_boot(session, monkeypatch):
    """Two processes booting at once must not leave one of them dead.

    The e2e suite starts its own uvicorn against the same ``backend/finex.db`` a developer's server
    holds, so this is an ordinary Tuesday here, and the refresh made it reachable on EVERY boot
    after a file edit rather than only on a first-ever one: both processes read the same newest
    version, both insert v N+1, and the loser violates ``uq_tpl_ver`` inside the startup event
    handler, which has nowhere to put an exception. Simulated by making the first read return the
    version the winner has already replaced — what a lost race looks like from in here.
    """
    from app.db.models import TemplateVersion
    from app.sample import reference

    shipped = _shipped_template()
    reference.ensure_reference_data(session)
    stale = _rows(session, TemplateVersion)[0]
    stale.definition = _totals_last(stale.definition)
    session.add(TemplateVersion(                 # the winner's row, published while we were reading
        template_key=shipped["template_key"], name=shipped.get("name", ""), version=2,
        definition=shipped, is_published=True))
    session.commit()

    real_newest, seen = reference._newest, []

    def _stale_read(sess, model, key_column, key):
        if model is TemplateVersion and not seen:
            seen.append(key)
            return _rows(sess, TemplateVersion)[0]        # v1: the read that lost the race
        return real_newest(sess, model, key_column, key)

    monkeypatch.setattr(reference, "_newest", _stale_read)
    assert reference.ensure_reference_data(session) == []      # retried, and nothing left to do

    versions = _rows(session, TemplateVersion)
    assert [r.version for r in versions] == [1, 2]             # no duplicate, no third version
    assert versions[-1].definition == shipped


def test_the_refresh_leaves_one_published_template_version(session):
    """``is_published`` names the shipped definition in force, so it names ONE row."""
    from app.db.models import TemplateVersion
    from app.sample.reference import ensure_reference_data

    ensure_reference_data(session)
    stored = _rows(session, TemplateVersion)[0]
    stored.definition = _totals_last(stored.definition)
    session.commit()
    ensure_reference_data(session)

    published = [r.version for r in _rows(session, TemplateVersion) if r.is_published]
    assert published == [2]


def test_the_shipped_key_cannot_also_be_named_as_retired(session, monkeypatch):
    """A contradiction that would retire the only current rulebook, refused before any write.

    ``superseded_keys`` would then report the shipped rulebook as replaced. That no longer changes
    WHICH rulebook runs — selection is "the latest stored wins" and filters nothing — but it still
    mislabels: every run pinned to an older version of the only rulebook the deployment owns would
    read "superseded", and the ontology list would show the shipped rulebook as replaced by nothing
    in particular. Loud at startup, unexplainable three screens away.
    """
    from app.db.models import OntologyVersion
    from app.sample import reference

    monkeypatch.setattr(reference, "RETIRED_ONTOLOGY_KEYS",
                        (*reference.RETIRED_ONTOLOGY_KEYS, _shipped_ontology()["ontology_key"]))
    with pytest.raises(reference.ReferenceSeedError) as exc:
        reference.ensure_reference_data(session)

    assert _shipped_ontology()["ontology_key"] in str(exc.value)
    assert session.execute(select(func.count()).select_from(OntologyVersion)).scalar() == 0


# --- which rulebook the refresh puts in force ---------------------------------------------------

def test_the_refreshed_rulebook_wins_over_an_older_incumbent_key(session):
    """Publishing the shipped rulebook has to CHANGE which one is in force.

    The adversary is a pair of rulebooks the repo has never shipped, so nothing is retired here and
    the ranking is what decides: the survivor of the pair declares a supersession (the shipped file
    declares none) and both were stored a month before the refresh. Ranked by
    declaration-then-incumbency the shipped rulebook loses twice, and publishing it buys the reader
    nothing at all — which is exactly what the database this unit was written against was doing.
    """
    from app.sample.reference import RETIRED_ONTOLOGY_KEYS, ensure_reference_data
    from app.services.ontology_select import select_for_template

    shipped = _shipped_ontology()
    old = datetime.now(timezone.utc) - timedelta(days=30)
    for key in ("hkfrs_hk_china_aa_local", "hkfrs_hk_china_bb_local"):
        assert key not in RETIRED_ONTOLOGY_KEYS               # not retirement: the ranking
    _insert_ontology(session, shipped, key="hkfrs_hk_china_aa_local", created_at=old)
    _insert_ontology(session, shipped, key="hkfrs_hk_china_bb_local",
                     supersedes="hkfrs_hk_china_aa_local", version=17,
                     created_at=old + timedelta(seconds=1))

    ensure_reference_data(session)

    row = select_for_template(session, shipped["target_template_key"])
    assert row is not None
    assert row.ontology_key == shipped["ontology_key"]
    assert row.definition == shipped


def test_a_pre_consolidation_database_ends_up_on_the_shipped_rulebook(session):
    """The real database, end to end: the two keys this repo used to ship, then one refresh.

    ``hkfrs_hk_china_v1`` and ``hkfrs_hk_china_v2`` are what every database older than the
    one-rulebook consolidation holds, and ``hkfrs_hk_china`` is not in such a database at all until
    the refresh publishes it. Afterwards the shipped rulebook is in force and carries the shipped
    content — the whole point of publishing it.
    """
    from app.sample.reference import ensure_reference_data
    from app.services.ontology_select import select_for_template

    shipped = _shipped_ontology()
    old = datetime.now(timezone.utc) - timedelta(days=30)
    _insert_ontology(session, shipped, key="hkfrs_hk_china_v1", created_at=old)
    _insert_ontology(session, shipped, key="hkfrs_hk_china_v2", supersedes="hkfrs_hk_china_v1",
                     version=17, created_at=old + timedelta(seconds=1))

    ensure_reference_data(session)

    row = select_for_template(session, shipped["target_template_key"])
    assert row is not None
    assert (row.ontology_key, row.version) == (shipped["ontology_key"], 1)
    assert row.definition == shipped


def test_a_retired_key_is_not_in_force_and_reads_as_replaced(session):
    """A key the shipped set no longer names must not be selectable, and must SAY it is replaced.

    ``GET /ontologies`` and every run's rulebook record are built from ``superseded_keys``: a stale
    rulebook the selector quietly passes over, while the list still shows it as live, is how a run
    came to be labelled with a rulebook it was not read against.
    """
    from app.sample.reference import RETIRED_ONTOLOGY_KEYS, ensure_reference_data
    from app.services.ontology_select import (
        rulebooks_for_template,
        select_for_template,
        superseded_keys,
    )

    shipped = _shipped_ontology()
    assert "hkfrs_hk_china_v2" in RETIRED_ONTOLOGY_KEYS
    _insert_ontology(session, shipped, key="hkfrs_hk_china_v2", supersedes="hkfrs_hk_china_v1")
    ensure_reference_data(session)

    rows = rulebooks_for_template(session, shipped["target_template_key"])
    assert superseded_keys(rows) == {"hkfrs_hk_china_v2"}
    assert select_for_template(session,
                               shipped["target_template_key"]).ontology_key == \
        shipped["ontology_key"]


def test_a_retired_key_alone_in_the_database_is_still_usable(session):
    """Retirement needs the replacement to be PRESENT.

    A database whose reference data has not been refreshed holds only the legacy rulebook. Reporting
    it as replaced would label a run against the only rulebook the deployment owns "superseded". Note
    what this no longer claims: the selector does not filter replaced rulebooks out (it takes the
    latest stored, full stop), so a blanket retirement can no longer leave it with nothing to choose —
    it can only make the answer's LABEL wrong, which is why the flag still has to be right.
    """
    from app.services.ontology_select import (
        rulebooks_for_template,
        select_for_template,
        superseded_keys,
    )

    shipped = _shipped_ontology()
    _insert_ontology(session, shipped, key="hkfrs_hk_china_v2", supersedes="hkfrs_hk_china_v1")

    rows = rulebooks_for_template(session, shipped["target_template_key"])
    assert superseded_keys(rows) == set()
    assert select_for_template(session,
                               shipped["target_template_key"]).ontology_key == "hkfrs_hk_china_v2"


def test_an_uploaded_rulebook_that_claims_nothing_does_not_displace_the_shipped_one(session):
    """The property incumbency was added for, now carried by "the shipped rulebook wins".

    Made as adversarial as the ranking allows: the upload's key sorts after the shipped one, it
    carries a higher edit version, and it was stored FIRST — so it wins every test except being the
    rulebook this repo ships. A draft someone uploaded to try something must not become the rulebook
    real extractions run on.
    """
    from app.sample.reference import ensure_reference_data
    from app.services.ontology_select import select_for_template

    shipped = _shipped_ontology()
    _insert_ontology(session, shipped, key="hkfrs_hk_china_zz_draft", version=9,
                     created_at=datetime.now(timezone.utc) - timedelta(days=30))
    ensure_reference_data(session)

    assert "hkfrs_hk_china_zz_draft" > shipped["ontology_key"]     # the sort order alone would lose
    assert select_for_template(session,
                               shipped["target_template_key"]).ontology_key == \
        shipped["ontology_key"]


def test_an_uploaded_replacement_that_declares_the_supersession_still_takes_over(session):
    """Being shipped is not being unbeatable — a successor still takes over by SAYING so.

    Kept beside the test above because the two are one decision: an upload cannot displace the
    shipped rulebook by arriving, and can displace it by declaring that it replaces it. Without this
    the product could never adopt a rulebook authored outside the repo.
    """
    from app.sample.reference import ensure_reference_data
    from app.services.ontology_select import select_for_template

    shipped = _shipped_ontology()
    ensure_reference_data(session)
    _insert_ontology(session, shipped, key="hkfrs_hk_china_next",
                     supersedes=shipped["ontology_key"])

    assert select_for_template(session,
                               shipped["target_template_key"]).ontology_key == \
        "hkfrs_hk_china_next"


def _own_ontology_file(tmp_path, monkeypatch) -> tuple[object, callable]:
    """Point ``reference._ONTOLOGY`` at this test's OWN copy of the shipped rulebook.

    A test about "the shipped file changed" has to be able to change it, and it must not change the
    repo's. Returns the path and a writer; the writer bumps mtime, because ``_key_in_file`` caches on
    (path, mtime) and a cache answering from content it no longer reflects is the same stale
    declaration this module exists to remove.
    """
    from app.sample import reference

    path = tmp_path / "own_ontology.json"
    path.write_text(json.dumps(_shipped_ontology()))
    monkeypatch.setattr(reference, "_ONTOLOGY", path)

    def write(definition: dict) -> None:
        path.write_text(json.dumps(definition))
        path.touch()

    return path, write


def test_an_edit_made_through_the_product_survives_a_restart(session, tmp_path, monkeypatch):
    """The property the refresh must PRESERVE rather than override.

    THE RULE is that the LATEST rulebook wins (``services.ontology_select``), so a start-up that
    republishes the shipped file unprompted does not merely add a row — it OVERRULES whatever a human
    did last. An analyst corrects an alias from the Template screen, restarts the server, and the
    correction is silently out of force with nothing on any screen saying so. That is the same class
    of defect as the drift this refresh was written to close, pointed the other way.

    So the refresh asks "have we published this file's content before?", not "does it differ from the
    newest version". An unchanged file is already stored, nothing is published, and the human's edit
    stays newest — and therefore in force.
    """
    from app.db.models import OntologyVersion
    from app.sample.reference import ensure_reference_data
    from app.services.ontology_select import select_for_template

    _own_ontology_file(tmp_path, monkeypatch)
    ensure_reference_data(session)
    base = _rows(session, OntologyVersion)[0]

    edited = json.loads(json.dumps(base.definition))
    edited["mappings"][0]["aliases"] = [*(edited["mappings"][0].get("aliases") or []), "Analyst"]
    session.add(OntologyVersion(
        ontology_key=base.ontology_key, target_template_key=base.target_template_key,
        version=base.version + 1, definition=edited))
    session.commit()

    notes = ensure_reference_data(session)                       # the restart

    assert not any("ontology" in n and "published" in n for n in notes), notes
    assert [r.version for r in _rows(session, OntologyVersion)] == [1, 2]
    in_force = select_for_template(session, base.target_template_key)
    assert "Analyst" in in_force.definition["mappings"][0]["aliases"]


def test_a_genuinely_changed_shipped_file_still_takes_precedence(session, tmp_path, monkeypatch):
    """The other half: giving a human's edit precedence must not make the shipped file inert.

    Content that has never been published is unseen however many edits sit on top of it, so it
    publishes, becomes the newest version, and runs — which is the whole point of shipping a fix.
    """
    from app.db.models import OntologyVersion
    from app.sample.reference import ensure_reference_data
    from app.services.ontology_select import select_for_template

    _path, write = _own_ontology_file(tmp_path, monkeypatch)
    ensure_reference_data(session)
    base = _rows(session, OntologyVersion)[0]

    edited = json.loads(json.dumps(base.definition))
    edited["mappings"][0]["aliases"] = ["Analyst"]
    session.add(OntologyVersion(
        ontology_key=base.ontology_key, target_template_key=base.target_template_key,
        version=base.version + 1, definition=edited))
    session.commit()

    changed = json.loads(json.dumps(_shipped_ontology()))
    changed["metadata"] = {**(changed.get("metadata") or {}), "version": "shipped-later"}
    write(changed)

    notes = ensure_reference_data(session)

    assert any("ontology" in n and "published" in n for n in notes), notes
    in_force = select_for_template(session, base.target_template_key)
    assert in_force.definition["metadata"]["version"] == "shipped-later"
