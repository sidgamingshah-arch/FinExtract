"""The shipped template + LINE-ITEM CONFIGURATION must reach a RUNNING app, not only a fresh one.

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
express; and publishing rival configuration versions into the shared database would change which
configuration every later test file finds in force.

REWRITTEN FOR THE ONE CONFIGURATION ENGINE. This module was written when the seeder published an
*ontology* beside each template into ``ontology_versions``. That table, its model, its routes and
its selector are gone: line items is the single configuration engine, the seeder publishes
``LineItemVersion`` rows, and ``services.config_select`` (which replaced ``ontology_select``)
decides which stored version is in force. Every live invariant below is the SAME invariant, asked of
the one store — that is the whole point of the rewrite rather than a deletion.

WHAT WAS RETIRED HERE, AND WHY — so nobody reinstates a test for behaviour that was removed on
purpose:

* ``test_the_shipped_key_cannot_also_be_named_as_retired`` — ``reference.RETIRED_ONTOLOGY_KEYS`` is
  deleted. It named the two legacy rulebook keys a refreshed database was meant to report as
  replaced; there is no rulebook key left to retire. Its live half — a shipped file that would not
  survive its own publish gate must not be seeded — is kept, and made the stronger check it always
  wanted to be: see ``test_a_shipped_template_that_fails_its_own_gate_fails_the_boot``.
* the five supersession / ranking cases (``..._wins_over_an_older_incumbent_key``,
  ``a_pre_consolidation_database_...``, the two ``retired_key`` cases, and the pair about an upload
  displacing the shipped rulebook). All of them pinned ``ontology_select``'s five-test precedence
  ranking and ``metadata.supersedes``. ``config_select`` ranks on ONE test — the latest stored set
  wins — and the hole the ranking was really guarding (a skeleton upload becoming the configuration
  real extractions run on) moved to the publish door, where an author is present to be told why.
  Ranking assertions here would pin a mechanism that no longer exists. What survives of that family
  is the only part that was ever about the REFRESH: publishing a changed shipped file must change
  which configuration is in force, which is
  ``test_a_genuinely_changed_shipped_file_still_takes_precedence``.
"""
from __future__ import annotations

import json
import logging

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


def _shipped_line_items() -> dict:
    """The shipped CONFIGURATION — what ``_shipped_ontology()`` used to read, one engine later."""
    from app.sample import reference

    return json.loads(reference._LINE_ITEMS.read_text(encoding="utf-8"))


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


# THE FAMILY THIS MODULE IS ABOUT. The repository ships two templates — the original
# hkfrs_hk_china and the China/PRC output_csv_hk — so "the stored templates" stopped being a single
# answer, and assertions written when there was one family were reading the other one's rows as
# extra versions of theirs. Every query below is scoped to this family, which is what these tests
# actually exercise: the refresh of the shipped hkfrs template.
#
# THE CONFIGURATION KEY IS THE OTHER PAIR'S. There is exactly one shipped line-item set and it
# targets ``output_csv_hk_v1``, so the primary template is seeded TEMPLATE-ONLY and the
# configuration arrives with the extra pair (``reference._EXTRA_PAIRS``). Read off the file rather
# than written out here, for the same reason ``reference.shipped_line_items_key`` reads it: two
# spellings of "which configuration is ours" is one spelling too many.
SUITE_TEMPLATE_KEY = "hkfrs_hk_china_v1"
SUITE_LINE_ITEMS_KEY = _shipped_line_items()["line_items_key"]


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
    if "line_items_key" not in where and hasattr(model, "line_items_key"):
        stmt = stmt.where(model.line_items_key == SUITE_LINE_ITEMS_KEY)
    return list(session.execute(stmt).scalars().all())


def _seeded_template_keys(session: Session) -> list[str]:
    """Every ``template_key`` present in the table, UNSCOPED.

    The one query in this module that deliberately steps outside ``SUITE_TEMPLATE_KEY``. The family
    scoping above is right for the refresh units — they are about the hkfrs template — but an
    invariant that must hold for EVERY shipped key cannot be asserted through a helper that pins the
    query to one of them. That is exactly how the extra pair's published rows went unnoticed.
    """
    from app.db.models import TemplateVersion

    return sorted(set(session.execute(select(TemplateVersion.template_key)).scalars().all()))


def _published_versions(session: Session, template_key: str) -> list[int]:
    """The versions carrying ``is_published`` under ONE key, in version order."""
    from app.db.models import TemplateVersion

    return [r.version for r in _rows(session, TemplateVersion, template_key=template_key)
            if r.is_published]


def _insert_config(session: Session, definition: dict, *, key: str, version: int = 1):
    """One stored configuration, written straight to the table — as an upload or an edit left it.

    ``created_at`` is deliberately left to the column default: ``config_select`` ranks on it in
    PYTHON, so a hand-set aware datetime would be compared against the naive ones sqlite hands
    back and raise instead of ranking. The tests that need an ordering get it from insertion order,
    which is what "the latest stored set wins" means anyway.
    """
    from app.db.models import LineItemVersion

    body = json.loads(json.dumps(definition))
    body["line_items_key"] = key
    row = LineItemVersion(line_items_key=key, target_template_key=body["target_template_key"],
                          version=version, definition=body)
    session.add(row)
    session.commit()
    return row


# --- refreshing ---------------------------------------------------------------------------------

def test_an_empty_database_is_seeded_at_version_1(session):
    """Unchanged behaviour: nothing stored means the shipped files are published as version 1."""
    from app.db.models import LineItemVersion, TemplateVersion
    from app.sample.reference import ensure_reference_data

    tpl, cfg = _shipped_template(), _shipped_line_items()
    notes = ensure_reference_data(session)

    templates = _rows(session, TemplateVersion)
    assert [(r.template_key, r.version, r.is_published) for r in templates] == \
        [(tpl["template_key"], 1, True)]
    assert templates[0].definition == tpl
    configs = _rows(session, LineItemVersion)
    assert [(r.line_items_key, r.version) for r in configs] == [(cfg["line_items_key"], 1)]
    assert configs[0].definition == cfg
    # …and it targets the template it was written for, which is the pair's whole point: a run reads
    # a configuration through ``config_select.select_for_template``, so a set stored against the
    # wrong template key is a set no run will ever find.
    assert configs[0].target_template_key == cfg["target_template_key"]
    # IT SAYS WHAT IT DID, for the reconcile script to print — one note per artefact it
    # published. Counted against the rows rather than fixed at 3: that literal is the number of
    # files the repository happens to ship, so adding a reference set breaks an assertion about
    # note-keeping. The invariant is one note per published row, not the cardinality.
    published = (session.execute(select(func.count()).select_from(TemplateVersion)).scalar()
                 + session.execute(select(func.count()).select_from(LineItemVersion)).scalar())
    assert len(notes) == published, notes
    assert all("published v1" in note for note in notes), notes


def test_a_second_call_against_an_unchanged_file_publishes_nothing(session):
    """The property that lets this run on every boot.

    Without it the refresh is a version-number pump: one row per restart, each identical to the
    last, every one of them deserialized by ``GET /line-items`` on every request to that screen.
    """
    from app.db.models import LineItemVersion, TemplateVersion
    from app.sample.reference import ensure_reference_data

    ensure_reference_data(session)
    before = {m: session.execute(select(func.count()).select_from(m)).scalar()
              for m in (TemplateVersion, LineItemVersion)}

    assert ensure_reference_data(session) == []
    assert {m: session.execute(select(func.count()).select_from(m)).scalar()
            for m in (TemplateVersion, LineItemVersion)} == before
    # …and the first call DID publish, so the equality above is not two zeros agreeing. Stated as
    # "some" rather than as a count: how many files the repository ships is not what idempotence is
    # about, and it moves every time a reference set is added or collapsed.
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


def test_a_stale_stored_configuration_is_refreshed_to_the_shipped_items(session):
    """The same refresh for the CONFIGURATION, whose drift is measured in missing line items.

    This was ``test_a_stale_stored_rulebook_is_refreshed_to_the_shipped_concepts`` and it is the
    same defect one engine later: what a run maps against is a ``line_item_versions`` row, so a
    stored set that has fallen behind the shipped file is a set of definitions the product is
    serving and nobody authored.
    """
    from app.db.models import LineItemVersion
    from app.sample.reference import ensure_reference_data

    shipped = _shipped_line_items()
    ensure_reference_data(session)
    stored = _rows(session, LineItemVersion)[0]
    stale = json.loads(json.dumps(stored.definition))
    stale["items"] = stale["items"][:-12]
    stored.definition = stale
    session.commit()

    ensure_reference_data(session)

    versions = _rows(session, LineItemVersion)
    assert [r.version for r in versions] == [1, 2]
    assert versions[-1].definition == shipped
    assert len(versions[-1].definition["items"]) == len(shipped["items"])


def test_the_four_framework_blocks_survive_the_seed_round_trip(session):
    """A block the SET declares must still be there after the seeder has stored it, and must still
    load — because with one configuration engine there is no second copy to fall back on.

    THE FRAMEWORK LAYER (``schemas.line_items.LineItemSet``: ``normalisation``, ``binding``,
    ``global_rules``, ``scope_selection``, and ``residual_framework`` beside them) used to be read
    off the ontology object at run time while a line-item set merely carried it for projection
    fidelity. It is now the only copy: the caption folds, the column binding, the set-wide rules,
    the page/statement scope and the residual sweep's framework are all read through the working
    view built off THIS row. So "the shipped file carries it" is not enough on its own — it has to
    survive the JSON column and a re-load, which is the round trip every run makes.

    This is also the boot-time non-negotiable pointed the other way: a new schema field ships with
    the data that satisfies it, and this is the assertion that the data actually arrived.
    """
    from app.db.models import LineItemVersion
    from app.sample.reference import ensure_reference_data
    from app.schemas.line_items import load_line_item_set

    blocks = ("normalisation", "binding", "global_rules", "scope_selection", "residual_framework")
    shipped = _shipped_line_items()
    assert [b for b in blocks if not shipped.get(b)] == [], \
        "the shipped configuration must carry the framework blocks the schema declares"

    ensure_reference_data(session)

    stored = _rows(session, LineItemVersion)[-1].definition
    for block in blocks:
        assert stored[block] == shipped[block], block
    # LOADED, not just present. A block stored as JSON the schema then refuses (or silently
    # ignores) is a block that governs nothing, which is precisely the state the merge removed.
    st = load_line_item_set(stored, resolve=True)
    for block in blocks:
        assert getattr(st, block) is not None, block


def test_a_lost_race_to_publish_does_not_fail_the_boot(session, monkeypatch):
    """Two processes booting at once must not leave one of them dead.

    Two servers pointed at one sqlite file race here, which is an ordinary Tuesday for this repo,
    and the refresh made it reachable on EVERY boot after a file edit rather than only on a
    first-ever one: both processes read the same newest version, both insert v N+1, and the loser
    violates ``uq_tpl_ver`` inside the startup event handler, which has nowhere to put an exception.
    Simulated by making the first read return the version the winner has already replaced — what a
    lost race looks like from in here.
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
    """``is_published`` names the shipped definition in force, so it names ONE row PER KEY.

    ASSERTED PER KEY, ACROSS EVERY SEEDED KEY, and that is the point of the rewrite. This unit read
    through ``_rows``, which pins an unscoped query to ``SUITE_TEMPLATE_KEY``, so it only ever
    watched the primary template — while ``_seed_extra_pair`` published each new version of an
    extra pair with ``is_published=True`` and cleared no prior. Measured on ``backend/finex.db``
    before the fix: 19 published rows of ``output_csv_hk_v1`` (v5..v23), every one badged
    "Published" on the Template list. Both writes now clear through
    ``reference._clear_prior_published``.

    The drift is a changed ``name`` on the stored row rather than ``_totals_last``: this has to make
    EVERY seeded template differ from its file, and the totals-last reshuffle is a property of the
    hkfrs profit-and-loss, not of whatever an extra pair happens to ship.
    """
    from app.db.models import TemplateVersion
    from app.sample.reference import ensure_reference_data

    ensure_reference_data(session)
    keys = _seeded_template_keys(session)
    assert len(keys) > 1, keys        # the extra shipped pair is present, or this proves nothing
    for key in keys:
        assert _published_versions(session, key) == [1], key
        stored = _rows(session, TemplateVersion, template_key=key)[0]
        stored.definition = {**stored.definition, "name": f"drifted {key}"}
        session.commit()

    ensure_reference_data(session)

    for key in keys:
        assert [r.version for r in _rows(session, TemplateVersion, template_key=key)] == [1, 2], key
        assert _published_versions(session, key) == [2], key


# --- a shipped file that would not survive its own door -----------------------------------------

def test_a_shipped_template_that_fails_its_own_gate_fails_the_boot(session, tmp_path, monkeypatch):
    """THE BOOT-TIME NON-NEGOTIABLE. A shipped file that the publish gate would refuse must stop
    the start-up, with the path named, BEFORE anything is written.

    This replaces the retired ``..._cannot_also_be_named_as_retired`` contradiction case and keeps
    the only part of it that was ever about a live invariant: ``ensure_reference_data`` stands in
    for the upload routes' checks, and it used to write whatever was on disk. A shipped file that no
    longer loads would then sit in the database as a row the read path chokes on — a 500 on the
    configuration screen, or an extraction reporting SUCCEEDED with its structural checks silently
    gone. A start-up that fails with the offending path named is the cheaper failure, and it can
    only be reached by a defect in the repo's own files.

    The stray top-level key is the cheapest way to fail that gate honestly: a key the schema does
    not declare is DROPPED IN SILENCE, so the file would publish and simply not contain what was
    authored.
    """
    from app.db.models import LineItemVersion, TemplateVersion
    from app.sample import reference

    broken = tmp_path / "broken_template.json"
    broken.write_text(json.dumps({**_shipped_template(), "totally_unknown_key": 1}),
                      encoding="utf-8")
    monkeypatch.setattr(reference, "_TEMPLATE", broken)

    with pytest.raises(reference.ReferenceSeedError) as exc:
        reference.ensure_reference_data(session)

    assert broken.name in str(exc.value)
    assert "totally_unknown_key" in str(exc.value)
    # NOTHING WAS WRITTEN — not the template, and not the extra pair that would have been seeded
    # after it. The gate runs before the first insert, which is what makes the failure safe.
    assert session.execute(select(func.count()).select_from(TemplateVersion)).scalar() == 0
    assert session.execute(select(func.count()).select_from(LineItemVersion)).scalar() == 0


def test_a_shipped_configuration_that_fails_its_own_gate_fails_the_boot(session, tmp_path,
                                                                      monkeypatch):
    """The same gate on the CONFIGURATION half, at the same strength: the boot stops.

    This test used to assert the weaker half and say so — ``_seed_extra_pair`` CAUGHT the refusal,
    logged it at ERROR and returned, so the app came up healthy with no configuration for
    ``output_csv_hk_v1`` and every run recognised nothing. The pair is gated like the primary
    template now (``reference._gate_pair``), and nothing is written by a pass that refuses.
    """
    from app.db.models import LineItemVersion, TemplateVersion
    from app.sample import reference

    broken = tmp_path / "broken_line_items.json"
    broken.write_text(json.dumps({**_shipped_line_items(), "totally_unknown_key": 1}),
                      encoding="utf-8")
    monkeypatch.setattr(reference, "_EXTRA_PAIRS",
                        [(reference._DIR / "output_csv_hk_v1_template.json", broken)])

    with pytest.raises(reference.ReferenceSeedError) as exc:
        reference.ensure_reference_data(session)

    assert broken.name in str(exc.value) and "totally_unknown_key" in str(exc.value)
    assert session.execute(select(func.count()).select_from(LineItemVersion)).scalar() == 0
    assert session.execute(select(func.count()).select_from(TemplateVersion)).scalar() == 0


# --- which configuration the refresh puts in force ----------------------------------------------

def _own_line_items_file(tmp_path, monkeypatch) -> tuple[object, callable]:
    """Point the shipped pair at this test's OWN copy of the configuration file.

    A test about "the shipped file changed" has to be able to change it, and it must not change the
    repo's. ``_EXTRA_PAIRS`` is the seam and not ``_LINE_ITEMS``: the pair list is built at import
    time from that constant, so rebinding the constant alone would leave the seeder reading the real
    file. Returns the path and a writer; the writer bumps mtime, because ``_key_in_file`` caches on
    (path, mtime) and a cache answering from content it no longer reflects is the same stale
    declaration this module exists to remove.
    """
    from app.sample import reference

    path = tmp_path / "own_line_items.json"
    path.write_text(json.dumps(_shipped_line_items()), encoding="utf-8")
    monkeypatch.setattr(reference, "_LINE_ITEMS", path)
    monkeypatch.setattr(reference, "_EXTRA_PAIRS",
                        [(reference._DIR / "output_csv_hk_v1_template.json", path)])

    def write(definition: dict) -> None:
        path.write_text(json.dumps(definition), encoding="utf-8")
        path.touch()

    return path, write


def test_an_edit_made_through_the_product_survives_a_restart(session, tmp_path, monkeypatch):
    """The property the refresh must PRESERVE rather than override.

    THE RULE is that the LATEST configuration wins (``services.config_select``), so a start-up that
    republishes the shipped file unprompted does not merely add a row — it OVERRULES whatever a human
    did last. An analyst corrects an alias on the Line Items screen, restarts the server, and the
    correction is silently out of force with nothing on any screen saying so. That is the same class
    of defect as the drift this refresh was written to close, pointed the other way.

    So the refresh asks "have we published this file's content before?", not "does it differ from the
    newest version". An unchanged file is already stored, nothing is published, and the human's edit
    stays newest — and therefore in force.
    """
    from app.db.models import LineItemVersion
    from app.sample.reference import ensure_reference_data
    from app.services.config_select import select_for_template

    _own_line_items_file(tmp_path, monkeypatch)
    ensure_reference_data(session)
    base = _rows(session, LineItemVersion)[0]

    edited = json.loads(json.dumps(base.definition))
    edited["items"][0]["aliases"] = [*(edited["items"][0].get("aliases") or []), "Analyst"]
    _insert_config(session, edited, key=base.line_items_key, version=base.version + 1)

    notes = ensure_reference_data(session)                       # the restart

    assert not any("line items" in n and "published" in n for n in notes), notes
    assert [r.version for r in _rows(session, LineItemVersion)] == [1, 2]
    in_force = select_for_template(session, base.target_template_key)
    assert "Analyst" in in_force.definition["items"][0]["aliases"]


def test_a_genuinely_changed_shipped_file_still_takes_precedence(session, tmp_path, monkeypatch):
    """The other half: giving a human's edit precedence must not make the shipped file inert.

    Content that has never been published is unseen however many edits sit on top of it, so it
    publishes, becomes the newest version, and runs — which is the whole point of shipping a fix.
    This is also what survives of the retired supersession family: publishing the shipped
    configuration has to CHANGE which one is in force, and ``config_select`` now decides that on
    recency alone rather than on five ranking rules.
    """
    from app.db.models import LineItemVersion
    from app.sample.reference import ensure_reference_data
    from app.services.config_select import select_for_template

    _path, write = _own_line_items_file(tmp_path, monkeypatch)
    ensure_reference_data(session)
    base = _rows(session, LineItemVersion)[0]

    edited = json.loads(json.dumps(base.definition))
    edited["items"][0]["aliases"] = ["Analyst"]
    _insert_config(session, edited, key=base.line_items_key, version=base.version + 1)

    changed = json.loads(json.dumps(_shipped_line_items()))
    changed["metadata"] = {**(changed.get("metadata") or {}), "version": "shipped-later"}
    write(changed)

    notes = ensure_reference_data(session)

    assert any("line items" in n and "published" in n for n in notes), notes
    in_force = select_for_template(session, base.target_template_key)
    assert in_force.definition["metadata"]["version"] == "shipped-later"
    assert in_force.version == 3, "the shipped file publishes ON TOP of the human's edit"


# --- the shipped pair is required, and refused whole --------------------------------------------
#
# ``output_csv_hk_v1`` is an ``_EXTRA_PAIRS`` entry, and it is the template every output_csv_hk run
# targets. A refusal there used to be one ERROR line and a healthy boot: on a fresh database no HK
# template and no configuration at all, on an existing one the previous stored version left in
# force. These pin the boot failing instead, with the file named and nothing written.

HK_TEMPLATE_FILE = "output_csv_hk_v1_template.json"


def _shipped_hk_template() -> dict:
    from app.sample import reference

    return json.loads((reference._DIR / HK_TEMPLATE_FILE).read_text(encoding="utf-8"))


def _own_hk_pair(tmp_path, monkeypatch) -> tuple[callable, callable]:
    """Point the HK pair at this test's OWN copies of both files; returns a writer for each.

    Only the HK pair: the Ind AS pair is left out of ``_EXTRA_PAIRS`` here so what is asserted is
    about one pair. The writers bump mtime for ``_key_in_file``'s cache, as in
    ``_own_line_items_file``.
    """
    from app.sample import reference

    tpl_path, li_path = tmp_path / "own_hk_template.json", tmp_path / "own_hk_line_items.json"
    tpl_path.write_text(json.dumps(_shipped_hk_template()), encoding="utf-8")
    li_path.write_text(json.dumps(_shipped_line_items()), encoding="utf-8")
    monkeypatch.setattr(reference, "_LINE_ITEMS", li_path)
    monkeypatch.setattr(reference, "_EXTRA_PAIRS", [(tpl_path, li_path)])

    def writer(path):
        def write(definition: dict) -> None:
            path.write_text(json.dumps(definition), encoding="utf-8")
            path.touch()
        return write

    return writer(tpl_path), writer(li_path)


def _bad_role(definition: dict) -> dict:
    """The probe's refused HK template: an invalid role on the first node."""
    out = json.loads(json.dumps(definition))
    out["statements"][0]["sections"][0]["role"] = "not_a_role"
    return out


def _counts(session: Session) -> dict:
    from app.db.models import LineItemVersion, TemplateVersion

    return {m.__tablename__: session.execute(select(func.count()).select_from(m)).scalar()
            for m in (TemplateVersion, LineItemVersion)}


NOTHING_STORED = {"template_versions": 0, "line_item_versions": 0}


def test_a_refused_shipped_hk_template_fails_the_boot_of_a_fresh_database(session, tmp_path,
                                                                          monkeypatch):
    from app.sample import reference

    write_tpl, _ = _own_hk_pair(tmp_path, monkeypatch)
    write_tpl(_bad_role(_shipped_hk_template()))

    with pytest.raises(reference.ReferenceSeedError) as exc:
        reference.ensure_reference_data(session)

    assert "own_hk_template.json" in str(exc.value) and "not_a_role" in str(exc.value)
    # Not even the primary template, which was gated first and is fine: one pass, one transaction.
    assert _counts(session) == NOTHING_STORED


def test_a_refused_shipped_hk_template_fails_the_boot_of_an_existing_database(session, tmp_path,
                                                                             monkeypatch):
    from app.sample import reference

    write_tpl, _ = _own_hk_pair(tmp_path, monkeypatch)
    reference.ensure_reference_data(session)
    before = _counts(session)

    write_tpl(_bad_role(_shipped_hk_template()))
    with pytest.raises(reference.ReferenceSeedError, match="own_hk_template.json"):
        reference.ensure_reference_data(session)
    session.rollback()
    assert _counts(session) == before


def test_a_refusal_in_a_later_pair_writes_nothing_from_an_earlier_one(session, tmp_path,
                                                                     monkeypatch):
    """The pairs used to commit one at a time, so a second pair's refusal left the first's rows."""
    from app.sample import reference

    broken = tmp_path / "broken_indas_line_items.json"
    indas = json.loads((reference._DIR / "output_csv_indas_line_items.json")
                       .read_text(encoding="utf-8"))
    broken.write_text(json.dumps({**indas, "totally_unknown_key": 1}), encoding="utf-8")
    monkeypatch.setattr(reference, "_EXTRA_PAIRS", [
        reference._EXTRA_PAIRS[0],
        (reference._DIR / "output_csv_indas_v1_template.json", broken)])

    with pytest.raises(reference.ReferenceSeedError, match="broken_indas_line_items.json"):
        reference.ensure_reference_data(session)
    assert _counts(session) == NOTHING_STORED


@pytest.mark.parametrize("which", ["template", "line_items", "primary"])
def test_a_missing_shipped_file_fails_the_boot_rather_than_being_skipped(session, tmp_path,
                                                                        monkeypatch, which):
    """No deployment ships without these files; one that means to drop a pair edits ``_EXTRA_PAIRS``.

    An absent file used to be skipped in silence — and an absent primary returned before the pairs
    were looked at, so a non-editable install booted with no reference data at all.
    """
    from app.sample import reference

    gone = tmp_path / f"absent_{which}.json"
    hk_tpl, hk_li = reference._EXTRA_PAIRS[0]
    if which == "primary":
        monkeypatch.setattr(reference, "_TEMPLATE", gone)
    else:
        monkeypatch.setattr(reference, "_EXTRA_PAIRS",
                            [(gone, hk_li) if which == "template" else (hk_tpl, gone)])

    with pytest.raises(reference.ReferenceSeedError) as exc:
        reference.ensure_reference_data(session)
    assert gone.name in str(exc.value) and "missing" in str(exc.value)
    assert _counts(session) == NOTHING_STORED


def test_unreadable_json_names_the_file(session, tmp_path, monkeypatch):
    """It used to escape as a bare ``JSONDecodeError``: a line and a column, and no file."""
    from app.sample import reference

    _own_hk_pair(tmp_path, monkeypatch)
    (tmp_path / "own_hk_line_items.json").write_text('{"items": [', encoding="utf-8")

    with pytest.raises(reference.ReferenceSeedError) as exc:
        reference.ensure_reference_data(session)
    assert "own_hk_line_items.json" in str(exc.value) and "JSON" in str(exc.value)


def test_a_set_that_does_not_target_its_partner_fails_the_boot(session, tmp_path, monkeypatch):
    """A set stored against another template key is one ``config_select`` never finds for this one."""
    from app.sample import reference

    _, write_li = _own_hk_pair(tmp_path, monkeypatch)
    write_li({**_shipped_line_items(), "target_template_key": "output_csv_indas_v1"})

    with pytest.raises(reference.ReferenceSeedError, match="not its partner"):
        reference.ensure_reference_data(session)


# --- a reverted shipped file is restored, over the seeder's own write only ----------------------
#
# THE RULE (``reference._plan``): nothing stored -> publish; newest is the shipped content ->
# nothing; content never stored -> publish (a new shipped file wins); content stored earlier ->
# republish it as the newest ONLY when the version in force is the seeder's own write, otherwise
# hold and say so.

def _variant(definition: dict, marker: str) -> dict:
    """Loadable, gate-passing, and wrong: what a silently accepted broken file looks like here.

    Marked in a field each schema declares — a template's ``name``, a set's ``metadata.version`` —
    because an undeclared key is refused by the very gate these files have to pass.
    """
    out = json.loads(json.dumps(definition))
    if "statements" in out:
        out["name"] = marker
    else:
        out["metadata"] = {**(out.get("metadata") or {}), "version": marker}
    return out


def _marker(definition: dict) -> str:
    if "statements" in definition:
        return definition.get("name")
    return (definition.get("metadata") or {}).get("version")


def _newest_row(session: Session, model, key_column, key: str):
    return session.execute(select(model).where(key_column == key)
                           .order_by(model.version.desc())).scalars().first()


def test_a_reverted_template_is_restored_as_the_newest_version(session, tmp_path, monkeypatch,
                                                               caplog):
    from app.db.models import TemplateVersion
    from app.sample import reference

    write_tpl, _ = _own_hk_pair(tmp_path, monkeypatch)
    shipped = _shipped_hk_template()
    key = shipped["template_key"]
    reference.ensure_reference_data(session)                         # boot 1: v1
    write_tpl(_variant(shipped, "broken"))
    reference.ensure_reference_data(session)                         # boot 2: v2, the variant
    write_tpl(shipped)                                                # the file is reverted

    planned = reference.ensure_reference_data(session, dry_run=True)
    with caplog.at_level(logging.WARNING, logger="app.sample.reference"):
        notes = reference.ensure_reference_data(session)             # boot 3

    assert notes == planned, "the dry run describes what the boot does"
    assert any(f"template {key}: restored" in n and "v3" in n for n in notes), notes
    newest = _newest_row(session, TemplateVersion, TemplateVersion.template_key, key)
    assert newest.version == 3 and newest.definition == shipped
    assert _published_versions(session, key) == [3]
    assert any("was reverted" in r.getMessage() for r in caplog.records), caplog.text
    # …and the restore is itself idempotent.
    assert reference.ensure_reference_data(session) == []


def test_a_reverted_configuration_is_restored_and_back_in_force(session, tmp_path, monkeypatch):
    from app.db.models import LineItemVersion
    from app.sample import reference
    from app.services.config_select import select_for_template

    _, write_li = _own_hk_pair(tmp_path, monkeypatch)
    shipped = _shipped_line_items()
    reference.ensure_reference_data(session)
    write_li(_variant(shipped, "broken"))
    reference.ensure_reference_data(session)
    write_li(shipped)

    notes = reference.ensure_reference_data(session)

    assert any("line items" in n and "restored" in n for n in notes), notes
    in_force = select_for_template(session, shipped["target_template_key"])
    assert in_force.version == 3 and in_force.definition == shipped
    assert ([r.seeded_from for r in _rows(session, LineItemVersion)]
            == ["own_hk_line_items.json"] * 3)


def test_a_template_an_administrator_uploaded_after_the_seed_is_not_overwritten(session, tmp_path,
                                                                               monkeypatch):
    """Revert after an UPLOAD through the API: the upload is the version in force and stays it."""
    from app.api.routes.templates import _publish
    from app.db.models import TemplateVersion
    from app.sample import reference

    write_tpl, _ = _own_hk_pair(tmp_path, monkeypatch)
    shipped = _shipped_hk_template()
    key = shipped["template_key"]
    reference.ensure_reference_data(session)
    write_tpl(_variant(shipped, "broken"))
    reference.ensure_reference_data(session)                          # v2, the seeder's
    _publish(session, _variant(shipped, "administrator"))             # v3, a person's
    write_tpl(shipped)

    notes = reference.ensure_reference_data(session)

    assert any(f"template {key}:" in n and "stays in force" in n for n in notes), notes
    newest = _newest_row(session, TemplateVersion, TemplateVersion.template_key, key)
    assert newest.version == 3 and _marker(newest.definition) == "administrator"


def test_a_configuration_an_administrator_published_after_the_seed_is_not_overwritten(
        session, tmp_path, monkeypatch):
    """Under the shipped key, through the real publish route."""
    from app.api.routes.line_items import LineItemSetCreate, create_line_item_set
    from app.sample import reference
    from app.services.config_select import select_for_template

    _, write_li = _own_hk_pair(tmp_path, monkeypatch)
    shipped = _shipped_line_items()
    reference.ensure_reference_data(session)
    write_li(_variant(shipped, "broken"))
    reference.ensure_reference_data(session)
    create_line_item_set(LineItemSetCreate(definition=_variant(shipped, "administrator")),
                         session=session)
    write_li(shipped)

    notes = reference.ensure_reference_data(session)

    assert any("line items" in n and "stays in force" in n for n in notes), notes
    in_force = select_for_template(session, shipped["target_template_key"])
    assert _marker(in_force.definition) == "administrator"
    assert in_force.seeded_from is None


def test_a_set_published_under_an_administrators_own_key_is_not_overwritten(session, tmp_path,
                                                                           monkeypatch):
    """``config_select`` picks the latest set ACROSS keys, so restoring the shipped key over an
    administrator's own key would put the shipped set back in force on top of their work."""
    from app.sample import reference
    from app.services.config_select import select_for_template

    _, write_li = _own_hk_pair(tmp_path, monkeypatch)
    shipped = _shipped_line_items()
    reference.ensure_reference_data(session)
    write_li(_variant(shipped, "broken"))
    reference.ensure_reference_data(session)
    _insert_config(session, _variant(shipped, "administrator"), key="analyst_own_set")
    write_li(shipped)

    notes = reference.ensure_reference_data(session)

    assert any("line items" in n and "stays in force" in n for n in notes), notes
    in_force = select_for_template(session, shipped["target_template_key"])
    assert in_force.line_items_key == "analyst_own_set"


def test_configuration_rows_older_than_the_seeded_from_column_are_held(session, tmp_path,
                                                                      monkeypatch):
    """A database seeded before the column existed: its rows read as "not the seeder's", the
    conservative answer — held and reported, never overwritten on a guess."""
    from app.db.models import LineItemVersion
    from app.sample import reference

    _, write_li = _own_hk_pair(tmp_path, monkeypatch)
    shipped = _shipped_line_items()
    reference.ensure_reference_data(session)
    write_li(_variant(shipped, "broken"))
    reference.ensure_reference_data(session)
    for row in _rows(session, LineItemVersion):
        row.seeded_from = None
    session.commit()
    write_li(shipped)

    notes = reference.ensure_reference_data(session)

    assert any("line items" in n and "stays in force" in n for n in notes), notes
    assert [r.version for r in _rows(session, LineItemVersion)] == [1, 2]
