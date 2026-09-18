"""The database -> file direction, which did not exist until `scripts/export_line_items_seed.py`.

WHAT THIS IS FOR. `sample/reference.ensure_reference_data` publishes the shipped seed into
`line_item_versions` at every boot, and `scripts/reconcile_reference_data.py` repairs a database
that has drifted from the files. Both run file -> database. An analyst's work on the Line Items
screen therefore lived in one database: it survives a restart OF THAT DATABASE (`_already_stored`
asks "has this file's content ever been published", not "does it differ from the newest row",
precisely so a boot does not republish the file over a human edit) and does not survive a NEW one —
a fresh container, a redeploy, a colleague's clone, or this suite, which points
`FINEX_DATABASE_URL` at an empty temp database.

THE ROUND TRIP IS THE TEST THAT MATTERS, and it is the one `test_retired_derivations` names the
standard for: "it is in git" and "it is what loads" are different claims and only the second one
matters. So the assertion below is not "the file changed" — it is that a COLD BOOT against an empty
database provisions the edit as the configuration in force.
"""
from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "export_line_items_seed.py"
SEED = REPO / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"
TEMPLATE_KEY = "output_csv_hk_v1"


def _provision(db: pathlib.Path):
    """A database as a fresh container would have it: the shipped seed, published at boot."""
    env_url = f"sqlite:///{db}"
    code = (
        "import os, sys, json;"
        f"os.environ['FINEX_DATABASE_URL']={env_url!r};"
        f"sys.path.insert(0, {str(REPO)!r});"
        "from app.db.base import SessionLocal, init_db;"
        "from app.sample.reference import ensure_reference_data;"
        "init_db();"
        "s=SessionLocal();"
        "print(json.dumps(ensure_reference_data(s)));"
        "s.commit()"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _publish(db: pathlib.Path, mutate_src: str) -> None:
    """Publish a further version, the way the configuration screen does: deep-copy the in-force
    definition, change part of it, store it as the next version."""
    code = (
        "import os, sys, copy;"
        f"os.environ['FINEX_DATABASE_URL']={f'sqlite:///{db}'!r};"
        f"sys.path.insert(0, {str(REPO)!r});"
        "from app.db.base import SessionLocal;"
        "from app.db.models import LineItemVersion;"
        "from app.services import config_select;"
        "s=SessionLocal();"
        f"row=config_select.select_for_template(s, {TEMPLATE_KEY!r});"
        "d=copy.deepcopy(row.definition);"
        "byk={i['key']: i for i in d['items']};"
        f"{mutate_src};"
        "s.add(LineItemVersion(line_items_key=row.line_items_key,"
        " target_template_key=row.target_template_key,"
        " version=row.version+1, definition=d));"
        "s.commit()"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, out.stderr


def _export(db: pathlib.Path, seed: pathlib.Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--seed", str(seed), *extra],
        capture_output=True, text=True, cwd=REPO)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "exp.db"
    _provision(path)
    return path


@pytest.fixture
def seed_copy(tmp_path):
    """A COPY, always. A test that writes the repository's own seed would leave the working tree
    dirty and the next test reading a file this one changed."""
    path = tmp_path / "seed.json"
    path.write_text(SEED.read_text(encoding="utf-8"), encoding="utf-8")
    return path


# ── nothing to say when there is nothing to export ───────────────────────────────────────────────

def test_a_database_that_matches_the_seed_exports_nothing(db, seed_copy):
    got = _export(db, seed_copy)
    assert got.returncode == 0, got.stderr
    assert "nothing to export" in got.stdout
    assert seed_copy.read_text(encoding="utf-8") == SEED.read_text(encoding="utf-8")


def test_a_database_with_no_configuration_store_says_so_rather_than_raising(tmp_path, seed_copy):
    """THE LIKELIEST FIRST RUN. A database predating line items — this repository's own
    `backend/finex.db` is one — has no `line_item_versions` table, and the raw SQLAlchemy error
    buries the remedy under the generated SELECT."""
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    got = _export(empty, seed_copy)
    assert got.returncode == 1
    assert "no line-item configuration store" in got.stdout
    assert "Traceback" not in got.stderr


# ── the round trip ───────────────────────────────────────────────────────────────────────────────

def test_an_edit_survives_into_a_cold_boot_of_a_new_database(db, seed_copy, tmp_path, monkeypatch):
    """THE WHOLE POINT, asserted end to end rather than described.

    An alias is added on one line and one part's note vocabulary widened — the two shapes the Line
    Items screen actually produces. The seed is exported, and then a SECOND, empty database is
    provisioned from the exported file: the edit has to be what that database serves.
    """
    _publish(db, "byk['bs_ca__inventories']['aliases']"
                 ".append('Stock on hand');"
                 "byk['bs_ca__inventories']['aliases_i18n']['en'].append('Stock on hand')")

    got = _export(db, seed_copy, "--apply")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "written:" in got.stdout

    exported = json.loads(seed_copy.read_text(encoding="utf-8"))
    inventories = next(i for i in exported["items"] if i["key"] == "bs_ca__inventories")
    assert "Stock on hand" in inventories["aliases"]

    # …and a COLD BOOT against an empty database serves it. The shipped seed is swapped for the
    # exported file for the duration, because `ensure_reference_data` reads that path.
    backup = SEED.read_text(encoding="utf-8")
    SEED.write_text(seed_copy.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        fresh = tmp_path / "fresh.db"
        _provision(fresh)
        code = (
            "import os, sys;"
            f"os.environ['FINEX_DATABASE_URL']={f'sqlite:///{fresh}'!r};"
            f"sys.path.insert(0, {str(REPO)!r});"
            "from app.db.base import SessionLocal;"
            "from app.services import config_select;"
            "s=SessionLocal();"
            f"row=config_select.select_for_template(s, {TEMPLATE_KEY!r});"
            "inv=next(i for i in row.definition['items'] if i['key']=='bs_ca__inventories');"
            "print('YES' if 'Stock on hand' in inv['aliases'] else 'NO')"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=REPO)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().endswith("YES"), out.stdout
    finally:
        SEED.write_text(backup, encoding="utf-8")


def test_the_written_file_keeps_the_seeds_own_formatting(db, seed_copy):
    """`indent=1` and `ensure_ascii=False`, which is not cosmetic: a re-indent or a `\\uXXXX`
    escape of every Chinese caption turns a two-line change into a 30,000-line diff nobody can
    review, and every alias edit in this repository is reviewed as a diff."""
    _publish(db, "byk['bs_ca__inventories']['aliases'].append('Stock on hand')")
    assert _export(db, seed_copy, "--apply").returncode == 0

    before = SEED.read_text(encoding="utf-8").splitlines()
    after = seed_copy.read_text(encoding="utf-8").splitlines()
    changed = [ln for ln in after if ln not in before]
    assert len(changed) <= 4, f"{len(changed)} changed lines for a one-alias edit: {changed[:6]}"
    assert "Stock on hand" in seed_copy.read_text(encoding="utf-8")
    assert "\\u" not in seed_copy.read_text(encoding="utf-8")


# ── the audit, which the publish gates do not cover ──────────────────────────────────────────────

def test_a_new_printed_column_is_refused(db, seed_copy):
    """The output spread's columns are fixed: a change may add a PART and never a line. The publish
    gate checks each key resolves against the template; it never asks whether the SET has grown a
    key the template declares no column for."""
    _publish(db, "import copy as _c;"
                 "n=_c.deepcopy(byk['bs_ca__inventories']);"
                 "n['key']='bs_ca__brand_new_column';"
                 "n['label']='Brand new column';"
                 "d['items'].append(n)")
    got = _export(db, seed_copy, "--apply")
    assert got.returncode == 3, got.stdout
    assert "template declares no column for" in got.stdout
    assert "bs_ca__brand_new_column" in got.stdout
    assert seed_copy.read_text(encoding="utf-8") == SEED.read_text(encoding="utf-8"), \
        "the seed was written despite the refusal"


def test_a_new_unbreakable_alias_tie_is_refused(db, seed_copy):
    """One alias, two lines, overlapping scope, equal priority, nobody's label — declaration order
    then picks the winner and the other claimant is unreachable for that caption. A two-item
    property, so no single-edit gate can see it."""
    _publish(db, "[(it['aliases'].append('A caption nobody owns'),"
                 "  it['aliases_i18n'].setdefault('en', []).append('A caption nobody owns'),"
                 "  it.__setitem__('match_priority', 81))"
                 " for it in (byk['bs_ca__raw_materials'], byk['bs_ca__wip'])]")
    got = _export(db, seed_copy, "--apply")
    assert got.returncode == 3, got.stdout
    assert "unbreakable alias ties rose from 0 to" in got.stdout


def test_e2e_probe_residue_is_refused(db, seed_copy):
    """A database an e2e suite has run against carries `E2E alias <epoch>` rows. Each is a valid
    edit — the suite edits the SHIPPED key — and must never become the shipped file."""
    _publish(db, "byk['bs_ca__inventories']['aliases'].append('E2E alias 1730000000000')")
    got = _export(db, seed_copy, "--apply")
    assert got.returncode == 3, got.stdout
    assert "e2e probe residue" in got.stdout


def test_force_exports_anyway_but_never_past_a_load_failure(db, seed_copy):
    """`--force` is for an operator who has read the report and decided. It does not reach a set
    that will not load, because that is not a configuration."""
    _publish(db, "byk['bs_ca__inventories']['aliases'].append('E2E alias 1730000000000')")
    got = _export(db, seed_copy, "--apply", "--force")
    assert got.returncode == 0, got.stdout
    assert "exporting anyway" in got.stdout

    # An UNCOMPILABLE regex, which `schemas.line_items._refuse_uncompilable` rejects at load. Not a
    # missing term sign — that loads fine and is only reported, which is why it belongs to the
    # audit `--force` may override rather than to the refusal it may not.
    _publish(db, "byk['bs_ca__inventories']['regex_hints']=['(unclosed']")
    broke = _export(db, seed_copy, "--apply", "--force")
    assert broke.returncode == 2, broke.stdout
    assert "does not load" in broke.stdout


# ── the promise it cannot make ───────────────────────────────────────────────────────────────────

def test_an_edit_a_rebuild_would_undo_is_named(db, seed_copy):
    """Most of what the screen edits is in `ontology_projection.SAME`, which
    `scripts/build_line_items.py` copies from the rulebook VERBATIM — so an exported alias edit on
    one of the 462 rulebook concepts disappears the next time anybody rebuilds the seed from the
    ontology. The `sub__*` parts are configurator-only and not exposed to that.

    Reported rather than silently accepted, because the alternative is an edit that survives the
    export, survives review, and vanishes on an unrelated rebuild.
    """
    _publish(db, "byk['bs_ca__inventories']['aliases'].append('Stock on hand');"
                 "byk['sub__cf_direct_tax_refunds_received']['aliases_i18n']"
                 ".setdefault('zh', []).append('收到的税费返还款')")
    got = _export(db, seed_copy)
    assert got.returncode == 0, got.stdout
    assert "would undo them" in got.stdout
    assert "bs_ca__inventories.aliases" in got.stdout
    # The part is NOT named: nothing projects onto it.
    assert "sub__cf_direct_tax_refunds_received" not in got.stdout.split("would undo them")[1]
