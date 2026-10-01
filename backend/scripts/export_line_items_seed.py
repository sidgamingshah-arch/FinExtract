#!/usr/bin/env python
"""Write the configuration in force in a DATABASE back into the file this repo ships.

THE DIRECTION THAT DID NOT EXIST. `sample/reference.ensure_reference_data` publishes the shipped
seed into `line_item_versions` at every boot, and `scripts/reconcile_reference_data.py` repairs a
database that has drifted from the files. Both run file -> database. Nothing ran the other way, so
an analyst's work on the Line Items screen lived in exactly one database:

  * IT DOES survive a restart of that database. The boot refresh (`sample.reference._plan`)
    publishes over a human edit only when the file has changed to content never stored, and
    restores earlier content only over its OWN last write — never merely because the file differs
    from the newest row. The edit stays newest, and `config_select` says the latest set wins, so it
    stays in force.
  * IT DOES NOT survive a NEW database — a fresh container, a redeploy, a colleague's clone, or the
    test suite, which points `FINEX_DATABASE_URL` at an empty temp database. There the seed is all
    there is, and `tests/test_retired_derivations` puts the reason plainly: "it is in git" and "it
    is what loads" are different claims and only the second one matters.

This script closes that gap. It reads the row a RUN would read — `config_select.select_for_template`,
not "the newest version", because those differ once two keys target one template — audits it against
what the repository is allowed to ship, and writes it to the seed.

    cd backend
    python scripts/export_line_items_seed.py                      # report only
    python scripts/export_line_items_seed.py --db /tmp/copy.db    # …against a copy
    python scripts/export_line_items_seed.py --apply              # write the seed

WHAT IT REFUSES, and why each refusal is not already covered by the publish gates. An edit reaching
a row passed three of them: its key resolves against the target template, the schema declares every
field, and the set recognises something. Those are properties of ONE edit. These are properties of
what the repository ships, and no gate looks at them:

  * E2E RESIDUE. A database an e2e suite has run against carries `E2E alias <epoch>` rows, which
    are a valid edit and must never become the shipped file.
  * A NEW PRINTED COLUMN. The output spread's columns are fixed: a change may add a PART and never
    a line. The publish gate checks each key resolves against the template; it never asks whether
    the SET has grown a key the template declares no column for.
  * A NEW UNBREAKABLE ALIAS TIE. One alias claimed by two lines with overlapping scope, equal
    priority and no label owner — declaration order then picks the winner and the other claimant is
    unreachable for that caption. This is a two-item property, so no single-edit gate can see it,
    and the shipped count is currently 0.
  * A DANGLING `parent` OR `terms.ref`, and a term with no sign.

`--force` overrides the audit and is for an operator who has read the report and decided; it cannot
override a load failure, because a set that does not load is not a configuration.

WHAT IT CANNOT PROMISE, said here rather than discovered later. Most of what the Line Items screen
edits — `aliases`, `aliases_i18n`, `keyword_hints`, `regex_hints`, `exclude_hints`, `sign_rule`,
`match_priority`, `label`, `definition` — is in `ontology_projection.SAME`, which
`scripts/build_line_items.py` copies from the rulebook into the seed VERBATIM. So an exported edit
to one of those fields on one of the 462 rulebook concepts is undone the next time anybody rebuilds
the seed from the ontology. The `sub__*` parts are configurator-only and not exposed to that. The
report names every exported change that sits in this position, because the alternative is an edit
that survives the export, survives review, and disappears on an unrelated rebuild.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SEED = REPO / "app" / "sample" / "templates" / "output_csv_hk_line_items.json"
TEMPLATE = REPO / "app" / "sample" / "templates" / "output_csv_hk_v1_template.json"
DEFAULT_TEMPLATE_KEY = "output_csv_hk_v1"


def _write_seed(path: pathlib.Path, definition: dict) -> None:
    """The seed's own formatting, which is not cosmetic: `indent=1` and `ensure_ascii=False` are
    what the file is written with, and a re-indent or an \\uXXXX escape of every Chinese caption
    turns a two-line change into a 30,000-line diff nobody can review."""
    with path.open("w", encoding="utf-8") as fh:
        json.dump(definition, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def _items(definition: dict) -> dict[str, dict]:
    return {str(i.get("key") or ""): i for i in (definition.get("items") or [])}


def _field_diff(before: dict, after: dict) -> list[tuple[str, str]]:
    """(key, field) for every item field that differs, plus added and removed items."""
    old, new = _items(before), _items(after)
    out: list[tuple[str, str]] = []
    for key in sorted(set(new) - set(old)):
        out.append((key, "<added>"))
    for key in sorted(set(old) - set(new)):
        out.append((key, "<removed>"))
    for key in sorted(set(old) & set(new)):
        for field in sorted(set(old[key]) | set(new[key])):
            if old[key].get(field) != new[key].get(field):
                out.append((key, field))
    for block in sorted(set(before) | set(after)):
        if block != "items" and before.get(block) != after.get(block):
            out.append(("<top level>", block))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="sqlite path or SQLAlchemy URL; default is the app's own")
    ap.add_argument("--template-key", default=DEFAULT_TEMPLATE_KEY,
                    help=f"the template whose in-force set to export (default {DEFAULT_TEMPLATE_KEY})")
    ap.add_argument("--seed", default=str(SEED), help="the file to write")
    ap.add_argument("--apply", action="store_true", help="write the seed; otherwise report only")
    ap.add_argument("--force", action="store_true",
                    help="export although the audit found something; never overrides a load failure")
    args = ap.parse_args()

    if args.db:
        url = args.db if "://" in args.db else f"sqlite:///{pathlib.Path(args.db).resolve()}"
        # Before app.config is imported by anything below, which reads it once.
        os.environ["FINEX_DATABASE_URL"] = url

    from sqlalchemy.exc import OperationalError

    from app.db.base import SessionLocal
    from app.schemas.line_items import load_line_item_set
    from app.services import config_select, line_item_audit, ontology_projection

    seed_path = pathlib.Path(args.seed)
    shipped = json.loads(seed_path.read_text(encoding="utf-8"))

    with SessionLocal() as session:
        try:
            row = config_select.select_for_template(session, args.template_key)
        except OperationalError as exc:
            # THE LIKELIEST FIRST RUN, so it gets a sentence rather than a traceback. A database
            # that predates line items — this repository's own `backend/finex.db` is one — has no
            # `line_item_versions` table at all, and the raw SQLAlchemy error buries the remedy
            # under the generated SELECT.
            if "line_item_versions" not in str(exc):
                raise
            print("\nthis database has no line-item configuration store, so there is nothing to "
                  "export.\n  It predates line items, or has never had the app booted against "
                  "it: start the services once and\n  `sample.reference.ensure_reference_data` "
                  "creates the table and publishes the shipped seed into it.")
            return 1
        if row is None:
            print(f"no line-item set targets {args.template_key!r} in this database — "
                  f"nothing to export")
            return 1
        exported = copy.deepcopy(row.definition or {})
        identity = (f"{row.line_items_key} v{row.version} (id {row.id}, "
                    f"created {row.created_at})")

    print(f"in force: {identity}")
    print(f"seed    : {seed_path}")

    # A SET THAT DOES NOT LOAD IS NOT A CONFIGURATION, and --force does not reach this.
    try:
        resolved = load_line_item_set(copy.deepcopy(exported), resolve=True)
    except Exception as exc:  # noqa: BLE001 — the reason is the output
        print(f"\nREFUSED: the stored row does not load, so it cannot be shipped:\n  {exc}")
        return 2

    changes = _field_diff(shipped, exported)
    if not changes:
        print("\nnothing to export — the seed already is the configuration in force")
        return 0

    print(f"\n{len(changes)} change(s) against the shipped seed:")
    for key, field in changes[:40]:
        print(f"  {key}: {field}")
    if len(changes) > 40:
        print(f"  … and {len(changes) - 40} more")

    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    findings: list[str] = []
    for label, found in (
            ("e2e probe residue", line_item_audit.junk_markers(exported)),
            ("printed keys the template declares no column for",
             line_item_audit.keys_outside_the_template(exported, template)),
            ("dangling parent / terms.ref", line_item_audit.dangling_references(exported)),
            ("terms with no sign", line_item_audit.unsigned_terms(exported)),
    ):
        if found:
            findings.append(f"{label}: {found[:8]}{' …' if len(found) > 8 else ''}")

    # A RATCHET, not a threshold: what is refused is an INCREASE, because adding a tie is how a
    # well-meant alias makes an existing line unreachable.
    was = len(line_item_audit.unbreakable_ties(
        load_line_item_set(copy.deepcopy(shipped), resolve=True)))
    now = len(line_item_audit.unbreakable_ties(resolved))
    print(f"\nunbreakable alias ties: seed {was} -> exported {now}")
    if now > was:
        findings.append(f"unbreakable alias ties rose from {was} to {now}")

    # WHAT A REBUILD WOULD UNDO — see this module's docstring.
    governed = set(ontology_projection.SAME) | set(ontology_projection.RENAMED.values())
    fragile = sorted({f"{k}.{f}" for k, f in changes
                      if f in governed and not k.startswith(line_item_audit.PART_PREFIX)})
    if fragile:
        print(f"\n{len(fragile)} exported change(s) sit on fields `scripts/build_line_items.py` "
              f"copies from the rulebook VERBATIM, so a rebuild from the ontology would undo them. "
              f"Make the same edit in output_csv_hk_ontology.json to keep it:")
        for entry in fragile[:20]:
            print(f"  {entry}")
        if len(fragile) > 20:
            print(f"  … and {len(fragile) - 20} more")

    if findings:
        print("\nAUDIT FOUND:")
        for finding in findings:
            print(f"  - {finding}")
        if not args.force:
            print("\nREFUSED. Re-run with --force to export anyway, having read the above.")
            return 3
        print("\n--force given: exporting anyway.")

    if not args.apply:
        print("\nreport only — re-run with --apply to write the seed")
        return 0

    _write_seed(seed_path, exported)
    print(f"\nwritten: {seed_path}")
    print("A fresh database now provisions this configuration at boot "
          "(sample.reference.ensure_reference_data). Commit the file for that to be true "
          "anywhere but this working copy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
