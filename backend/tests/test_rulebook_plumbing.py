"""Choosing a rulebook has to change what a run DOES, and a run has to say which one it used.

Two failures make the choice meaningless, and neither is visible in a figure:

* the run was pinned to rulebook A and its pages were read by rulebook B, because reconstruction
  took ``scope_selection`` / ``normalisation`` from whichever rulebook is shipped as the one in
  force instead of from the run's own. The figures that come back are internally consistent, so
  nothing downstream notices;
* the extraction view re-derived, on every reload, which rulebook "must" be in force and labelled
  the run with that — so a filing read against a rulebook that is not the current one was presented
  as having been read against it. Which rulebook produced a figure is part of the figure.

"RULEBOOK" HERE IS A LINE-ITEM SET. It was an ``ontology_versions`` row read off
``hkfrs_hk_china_ontology.json``; line items is the single configuration engine, so the store is
``line_item_versions``, the definition is a ``LineItemSet``, and the run's pin is
``line_item_version_id``. The two blocks these tests turn — ``scope_selection`` and
``normalisation`` — ship on the line-item set unchanged, which is why the merge left this file's
subject intact.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

RULEBOOK = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "output_csv_hk_line_items.json")


def _blocks() -> tuple[dict, dict]:
    raw = json.loads(RULEBOOK.read_text(encoding="utf-8"))
    return copy.deepcopy(raw["scope_selection"]), copy.deepcopy(raw["normalisation"])


def _definition(key: str, template_key: str, *, signals=None) -> dict:
    """A configuration that declares only the two blocks reconstruction reads, so a difference in
    behaviour can only have come from the block — not from an item, a template or a locale.

    ``supersedes`` used to be a keyword here. Supersession is gone with the ontology store (see
    ``services.config_select``): selection is "the latest stored set wins", full stop, so a
    declaration about a KEY cannot decide what runs and there is nothing for a test to seed.
    """
    scope, normalisation = _blocks()
    if signals is not None:
        scope["entity_scope"]["signals"] = signals
    return {
        "schema_version": 1, "line_items_key": key, "target_template_key": template_key,
        "scope_selection": scope, "normalisation": normalisation,
    }


def _seed_rulebook(session, key: str, template_key: str, **kw) -> str:
    from app.db.models import LineItemVersion

    row = LineItemVersion(line_items_key=key, target_template_key=template_key, version=1,
                          definition=_definition(key, template_key, **kw))
    session.add(row)
    session.flush()
    return row.id


def _seed_document(session, data: bytes, filename: str) -> str:
    from app.config import get_settings
    from app.db.models import Document
    from app.ports.object_store import LocalObjectStore
    from app.services.documents import content_hash

    store = LocalObjectStore(get_settings().object_store_root)
    row = Document(filename=filename, fmt="pdf", byte_size=len(data), page_count=1,
                   content_hash=content_hash(data), object_key=store.put_bytes(data),
                   owner="admin", status="integrity_checked")
    session.add(row)
    session.flush()
    return row.id


def _slots(row: dict) -> dict[tuple[str, str | None], str | None]:
    return {(v["basis"], v["period_label"]): v["value"] for v in row["values"]}


def _run_pinned_to(document_id: str, line_item_version_id: str) -> dict:
    """Run the extraction exactly as the route does — same task, same options — and return the
    stored result.

    The pin is ``line_item_version_id`` in both places the run keeps it, the options dict and the
    column. Both were ``ontology_version_id``, against a store that no longer exists.
    """
    from app.api.routes.extractions import _run_extraction_task
    from app.db.base import SessionLocal
    from app.db.models import Document, ExtractionRun

    run_id = f"probe-{uuid.uuid4().hex[:12]}"
    with SessionLocal() as s:
        doc = s.get(Document, document_id)
        options = {"line_item_version_id": line_item_version_id}
        s.add(ExtractionRun(id=run_id, document_id=document_id, status="running",
                            options=options, progress={}, result=None,
                            line_item_version_id=line_item_version_id))
        s.commit()
        object_key, filename = doc.object_key, doc.filename
    _run_extraction_task(run_id, object_key, filename, options, "probe", "stub", "stub")
    with SessionLocal() as s:
        run = s.get(ExtractionRun, run_id)
        assert run.status == "succeeded", run.logs
        return run.result


def test_the_rulebook_a_run_is_pinned_to_is_the_rulebook_its_pages_are_read_with():
    """Two rulebooks, identical but for one ``scope_selection`` signal list, against ONE filing.

    The filing is headed "Group | Company" the way an HKEX balance sheet is. The rulebook that
    declares those words as ``entity_scope.signals`` bands the page into two bases, so the Company's
    receivables stay the Company's; the one with the list emptied cannot see the band, reads four
    columns as two periods of one basis, and files the Company's figures as the Group's. Same PDF,
    same code, different rulebook — so the pin is what decided how the page was READ, which is the
    whole point of pinning one.
    """
    pytest.importorskip("reportlab")
    pytest.importorskip("fitz")
    from tests.fixtures.generate import make_group_company_pdf

    from app.db.base import SessionLocal, init_db

    init_db()
    template_key = f"tk-pin-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        doc_id = _seed_document(s, make_group_company_pdf(), "group_company.pdf")
        declared = _seed_rulebook(s, f"rb-declared-{uuid.uuid4().hex[:8]}", template_key)
        blind = _seed_rulebook(s, f"rb-blind-{uuid.uuid4().hex[:8]}", template_key, signals=[])
        s.commit()

    def receivables(result: dict) -> dict:
        row = next(r for r in result["rows"] if "Trade receivables" in r["source_label"])
        return _slots(row)

    with_signals = receivables(_run_pinned_to(doc_id, declared))
    without_signals = receivables(_run_pinned_to(doc_id, blind))

    # The declared rulebook keeps the two entities apart, figure for figure.
    assert with_signals == {
        ("consolidated", "current"): "3410", ("consolidated", "prior"): "2900",
        ("standalone", "current"): "310", ("standalone", "prior"): "270",
    }, with_signals
    # The other one cannot: one basis, and the Company's two columns are filed as two further
    # PERIODS of that one entity — the mis-load the signals exist to prevent.
    assert len({basis for basis, _ in without_signals}) == 1, without_signals
    assert len(without_signals) == 4 and set(without_signals.values()) == {
        "3410", "2900", "310", "270"}, without_signals
    assert with_signals != without_signals


def test_a_run_read_against_an_older_rulebook_is_never_labelled_as_the_one_in_force(client):
    """Pinning a rulebook that is NOT the one in force is legitimate — reproducing an earlier spread
    needs it — and labelling the result as the rulebook in force is an audit failure. The run
    records which rulebook it used, and both views report the recorded value, so a reload cannot
    re-derive a flattering answer: the same run comes back ``pinned``, naming the configuration that
    IS in force, however the client asked for it.

    THE STATUS WORD IS ``pinned``, NOT ``superseded``. ``superseded`` was answered from
    ``metadata.supersedes`` on the ontology — a DECLARATION about a key that explicitly did not
    decide what runs — and it is gone with the ontology store (see ``rulebook_record`` and
    ``services.config_select``). What remains is the fact this test was always really about: this
    run did not use the configuration in force, and here is the one that is. Which of the two sets
    below is in force is decided by the only rule there is now, latest-stored-wins, so ``new_key``
    is seeded second.
    """
    pytest.importorskip("reportlab")
    from tests.fixtures.generate import make_native_pdf

    from app.db.base import SessionLocal, init_db

    init_db()
    template_key = f"tk-sup-{uuid.uuid4().hex[:8]}"
    old_key, new_key = f"rb-old-{uuid.uuid4().hex[:8]}", f"rb-new-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        doc_id = _seed_document(s, make_native_pdf(), "older_rulebook.pdf")
        old_id = _seed_rulebook(s, old_key, template_key)
        s.commit()
        new_id = _seed_rulebook(s, new_key, template_key)   # stored last, so this one is in force
        s.commit()

    started = client.post(f"/api/v1/documents/{doc_id}/extractions",
                          json={"line_item_version_id": old_id})
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    # Stated at the moment the run starts, before any figure exists to label.
    assert started.json()["rulebook"]["status"] == "pinned"

    run = client.get(f"/api/v1/extractions/{run_id}").json()
    assert run["status"] == "succeeded", run
    rb = run["rulebook"]
    assert rb["status"] == "pinned" and rb["in_force"] is False, rb
    assert rb["line_items_key"] == old_key
    # …and it names what IS in force, so the reader is told what the alternative was.
    assert rb["in_force_line_items_key"] == new_key, rb
    # The same claim travels on the result, with whether the pinned rulebook actually loaded.
    assert run["result"]["rulebook"]["status"] == "pinned"
    assert run["result"]["rulebook"]["applied"] is True

    # The reload path — the document's latest run — reports the recorded value, not a fresh guess.
    reloaded = client.get(f"/api/v1/documents/{doc_id}/run").json()
    assert reloaded["rulebook"]["status"] == "pinned"
    assert reloaded["rulebook"]["in_force"] is False

    # The one in force, pinned explicitly, is the one run that may claim to be in force.
    current = client.post(f"/api/v1/documents/{doc_id}/extractions",
                          json={"line_item_version_id": new_id})
    assert current.status_code == 202, current.text
    rb2 = client.get(f"/api/v1/extractions/{current.json()['run_id']}").json()["rulebook"]
    assert rb2["status"] == "in_force" and rb2["in_force"] is True, rb2


# RETIRED: test_the_rulebook_that_runs_is_never_also_reported_as_the_replaced_one.
#
# It pinned SUPERSESSION, which is deliberately removed. The test seeded a rulebook, a successor
# declaring ``metadata.supersedes`` against its key, then a v2 of the original, and asserted that
# ``rulebook_record`` reported ``status="in_force"`` alongside ``superseded=True`` rather than a
# sentence that refuted itself ("you used the replaced rulebook — the one in force is that same
# rulebook").
#
# Line items is the single configuration engine and it has ONE selection rule: the latest stored set
# for the template wins (``services.config_select.select_for_template``). ``metadata.supersedes``,
# ``superseded_keys``, ``rulebooks_for_template`` and the ``superseded`` field on the served record
# are all gone with the ontology store, so the contradiction this test guarded is not reachable —
# there is no second, declarative answer to "is this current" left to disagree with the first. The
# half that still matters, "a run that did not use the configuration in force must not be labelled as
# though it had", is asserted above as ``status == "pinned"``.
#
# Do not reinstate either the field or this test: a declaration about a key that does not decide what
# runs is exactly the surface the merge removed.
