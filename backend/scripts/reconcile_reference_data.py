"""Bring an existing database's reference data back to the files this repo ships.

Written for one database and kept, because the state it repairs can only be reached by a database
that predates the fix and the same state will be reached again by anyone restoring an old copy.
Idempotent: a reconciled database reports "nothing to reconcile" on every later run.

WHAT WENT WRONG. ``sample/reference.ensure_reference_data`` used to publish a version 1 only when no
version of the key existed, and then never look at the shipped files again — so every revision made
after a database's first startup reached the tests (``tests/conftest.py`` points
``FINEX_DATABASE_URL`` at a fresh temp database) and never reached the app using that database. The
development database showed it in every one of its rows:

* all four stored ``hkfrs_hk_china_v1`` template versions carried the PRE-REVISION profit-and-loss,
  whose top-level order is the seven sections followed by every calculated total bunched at the end
  (``pl_gross_profit``, ``pl_operating_profit_ebit``, ``pl_total_expenses``, …). The statement
  builder emits the template's own order faithfully; that order was what it was given. "Gross Profit
  and the other calculated totals still render at the END of the template" was a true report about a
  template four revisions stale.
* the stored configuration was four revisions behind the shipped file, so a fix an author had
  published never reached the product.
* 16 of the 18 stored configuration versions were Playwright junk. The e2e suite runs uvicorn with
  ``cwd: ../backend``, so its three configuration-editing probes published their
  ``E2E alias <epoch>`` / ``E2E includes <epoch>`` / ``E2E netting <epoch>`` edits straight into the
  development database (frontend/e2e/smoke.spec.ts:423, :452, :489).

``ensure_reference_data`` now refreshes on every boot, so the drift closes by itself. This script is
for the part a startup path must NOT do: deleting. It removes every stored configuration version
carrying an e2e probe marker, whatever key it is under — the suite edits the SHIPPED key, so that is
the rule which keeps working after any one-off cleanup — and then refreshes the template and the
configuration from the files.

WHAT WAS HERE AND IS GONE, so nobody reinstates it. This script used to read ``ontology_versions``
and sweep every version of a key listed in ``sample/reference.RETIRED_ONTOLOGY_KEYS``. Both are
deleted with the second configuration store: line items is the single configuration engine, its rows
live in ``line_item_versions``, ``db.base`` drops the old table on the way past, and one engine has
no rival key to retire. What it reports on instead is ``line_item_versions`` — and, per version, how
many extraction runs PIN it through ``extraction_runs.line_item_version_id``, which is the fact that
decides whether deleting a row costs a stored run its configuration record.

WHAT IT DELIBERATELY DOES NOT TOUCH, stated because leaving it unsaid would read as an oversight:
older versions of the SHIPPED template and configuration keys. None of them carries an e2e marker,
some are pinned by stored extraction runs, and the newest version is what every reader gets
(``sortTemplates`` on the Template screen, ``config_select.select_for_template`` for a run, and a run
names the version id it read). An unreferenced older version is history, not clutter. What it prints
instead is how many of them there are, and which are pinned, so the operator can prune deliberately.

A run that pinned a configuration version this deletes is reported BEFORE anything is written: its
configuration record changes from "pinned" to "missing"
(api/routes/extractions.configuration_record). That is the price of deleting the junk, and the plan
states it rather than discovering it later.

    cd backend
    python scripts/reconcile_reference_data.py                       # report only
    python scripts/reconcile_reference_data.py --db /tmp/copy.db     # …against a copy
    python scripts/reconcile_reference_data.py --apply               # write

Deletion is not reversible, so writing takes ``--apply``. Try it on a copy first
(``cp finex.db /tmp/copy.db``): ``--db`` points every part of this script at that file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# The three configuration-editing e2e probes build their strings as `E2E alias ${Date.now()}` and so
# on (frontend/e2e/smoke.spec.ts:423, :452, :489), which is why an epoch is part of the pattern: it
# cannot match an alias a human authored, and it cannot match the shipped files — the suite asserts
# on these exact strings, so the day one is renamed the rename lands in one place.
_E2E_PROBE = re.compile(r"E2E (?:alias|includes|netting) \d{10,}")

# THIS checkout's backend root, ahead of anything else on the path. Running
# ``python scripts/reconcile_reference_data.py`` puts ``backend/scripts`` on sys.path and NOT
# ``backend``, so ``import app`` resolves through whatever editable install the interpreter can
# see — another checkout, whose ``ensure_reference_data`` may still be the seed-once version this
# script exists to repair the damage from. It would then report a reconciliation it never performed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _items(definition: dict | None) -> int:
    """How many line items a stored configuration defines — the size of what is being compared."""
    return len((definition or {}).get("items") or [])


def _pl_order(definition: dict | None) -> list[str]:
    """The profit-and-loss statement's top-level node ids, in stored order.

    Printed because it is the reader-visible face of the drift: the shipped order interleaves each
    calculated total with the section it is computed from, and the stale one puts all of them last.
    """
    for statement in (definition or {}).get("statements") or []:
        if statement.get("type") == "profit_and_loss":
            return [str(s.get("node_id") or "") for s in statement.get("sections") or []]
    return []


def _e2e_markers(definition: dict | None) -> list[str]:
    return sorted(set(_E2E_PROBE.findall(json.dumps(definition or {}, ensure_ascii=False))))


def _pins(session) -> dict[str, int]:
    """``line_item_version_id`` -> how many stored runs pin it.

    The whole reason this script can report what a deletion costs. A run records the configuration
    it read the filing against, and that pin is what makes the run reproducible — so "which stored
    versions are load-bearing" is a question about ``extraction_runs``, not about which row is
    newest.
    """
    from sqlalchemy import func, select

    from app.db.models import ExtractionRun

    return {str(vid): int(n) for vid, n in session.execute(
        select(ExtractionRun.line_item_version_id, func.count())
        .where(ExtractionRun.line_item_version_id.is_not(None))
        .group_by(ExtractionRun.line_item_version_id)) if vid}


def _summary(session, label: str) -> None:
    """What the database says right now, in the facts the drift showed up in."""
    from sqlalchemy import select

    from app.db.models import LineItemVersion, TemplateVersion
    from app.sample.reference import shipped_line_items_key, shipped_template_key
    from app.services.config_select import select_for_template

    tpl_key, cfg_key = shipped_template_key(), shipped_line_items_key()
    pinned = _pins(session)
    print(f"\n{label}")
    templates = list(session.execute(
        select(TemplateVersion).where(TemplateVersion.template_key == tpl_key)
        .order_by(TemplateVersion.version)).scalars().all())
    if not templates:
        print(f"  template {tpl_key}: not stored")
    else:
        published = [f"v{r.version}" for r in templates if r.is_published] or ["none"]
        print(f"  template {tpl_key}: {len(templates)} version(s), "
              f"newest v{templates[-1].version}, published {', '.join(published)}")
        order = ", ".join(_pl_order(templates[-1].definition))
        print(f"    P&L top-level order: {order or 'none'}")

    rows = list(session.execute(
        select(LineItemVersion).order_by(LineItemVersion.line_items_key, LineItemVersion.version)
    ).scalars().all())
    by_key: dict[str, list] = {}
    for r in rows:
        by_key.setdefault(r.line_items_key, []).append(r)
    # In force is asked PER TEMPLATE, because that is the question the selector answers: a stored
    # set targeting some other template is not competing with this one and must not be labelled as
    # though it lost.
    chosen = (select_for_template(session, key)
              for key in {r.target_template_key for r in rows if r.target_template_key})
    in_force = {c.id for c in chosen if c is not None}
    for key, versions in sorted(by_key.items()):
        mark = " [IN FORCE]" if any(r.id in in_force for r in versions) else ""
        junk = sum(1 for r in versions if _e2e_markers(r.definition))
        runs = sum(pinned.get(r.id, 0) for r in versions)
        print(f"  configuration {key}: {len(versions)} version(s) v{versions[0].version}-"
              f"v{versions[-1].version}, {_items(versions[-1].definition)} line items"
              f"{', ' + str(junk) + ' carrying e2e probe edits' if junk else ''}"
              f"{', ' + str(runs) + ' run(s) pin one' if runs else ''}{mark}")
        for r in versions:
            if n := pinned.get(r.id, 0):
                print(f"    v{r.version} pinned by {n} extraction run(s)  (id {r.id})")
    if cfg_key and cfg_key not in by_key:
        print(f"  configuration {cfg_key}: NOT STORED — the shipped set is not in this database")
    if rows and not in_force:
        print("  in force: nothing")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Reconcile a database's reference template + line-item set to the shipped "
                    "files.")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete and publish (default is a report)")
    ap.add_argument("--db", default="",
                    help="SQLite file to reconcile (default: the app's configured database). Point "
                         "this at a copy to rehearse.")
    args = ap.parse_args()

    if args.db:
        path = Path(args.db).expanduser().resolve()
        if not path.exists():
            print(f"no such database: {path}")
            return 1
        # Set BEFORE the first app import: ``app.db.base`` builds its engine from the settings at
        # import time and ``get_settings`` is lru_cached, so exporting this afterwards would leave
        # the script reading its own report off the copy while writing to the dev database.
        os.environ["FINEX_DATABASE_URL"] = f"sqlite:///{path}"

    from sqlalchemy import select

    from app.db.base import SessionLocal, engine, init_db
    from app.db.models import LineItemVersion
    from app.sample.reference import ensure_reference_data

    init_db()
    print(f"database: {engine.url}")
    with SessionLocal() as session:
        _summary(session, "BEFORE")

        rows = list(session.execute(
            select(LineItemVersion)
            .order_by(LineItemVersion.line_items_key, LineItemVersion.version)
        ).scalars().all())
        # ONE deletion rule, where there used to be two. The retired-key sweep went with the second
        # configuration store — there is one engine now, so no key can be "the one we no longer
        # name". A probe marker is a fact about the CONTENT of a row and stays true whatever the
        # key is called.
        doomed = [(r, f"e2e probe edit ({markers[0]})")
                  for r in rows if (markers := _e2e_markers(r.definition))]

        # Reported before the plan is acted on: a run pins the line_item_version_id it read the
        # filing against, and deleting it leaves the run's configuration record reading "missing".
        pinned = _pins(session)
        orphaned = [(r, pinned[r.id]) for r, _ in doomed if r.id in pinned]

        print("\nPLAN — what --apply would do, in this order")
        for r, why in doomed:
            print(f"  delete   configuration {r.line_items_key:22} v{r.version:<4} {why}")
        if not doomed:
            print("  delete   nothing — no stored configuration carries an e2e probe edit")
        # Staged inside the transaction and flushed, so the refresh is planned against the database
        # the deletions leave behind rather than the one they started from — otherwise the plan
        # would miss a republish that only becomes necessary once a polluted newest version is gone.
        for r, _ in doomed:
            session.delete(r)
        session.flush()
        # Printed verbatim: a note is either an action ("published v5 from …") or a finding, and
        # prefixing both with one verb of this script's own choosing labelled the findings as work.
        for note in ensure_reference_data(session, dry_run=True):
            print(f"  {note}")
        for r, n in orphaned:
            print(f"  NOTE     {n} extraction run(s) pin {r.line_items_key} v{r.version}, which "
                  f"this deletes; their configuration record becomes \"missing\"")

        if not args.apply:
            session.rollback()          # the staged deletions, undone: this run wrote nothing
            print("\nreport only — pass --apply to write")
            return 0

        session.commit()
        print("\nAPPLIED")
        for note in ensure_reference_data(session):
            print(f"  {note}")
        _summary(session, "AFTER")
    return 0


if __name__ == "__main__":
    sys.exit(main())
