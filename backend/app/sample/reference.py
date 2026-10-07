"""Keep the DB's reference template + LINE-ITEM SET equal to the files this repo ships.

Loads the shipped templates and the shipped line-item set (app/sample/templates/) into the versioned
tables, publishing a new version whenever a file differs from the newest stored one — idempotent,
safe to call on every startup. This is what lets an uploaded document be mapped against a real
configuration out of the box.

ONE CONFIGURATION ENGINE, AND IT IS LINE ITEMS. This module used to seed an *ontology* beside each
template into ``ontology_versions``; that table, its model, its routes and its selector are deleted.
The seeder therefore publishes ``LineItemVersion`` rows and nothing else: there is no second engine
to choose between, so there is no second thing to seed, and no ``mapping_engine`` switch for a boot
to have an opinion about. Do not reinstate an ontology pair here — a stored, selectable rulebook is
exactly the surface the merge removed.

ONE PAIR CARRIES CONFIGURATION. ``output_csv_hk_v1`` is the template every output_csv_hk run
targets, and ``output_csv_hk_line_items.json`` is the configuration for it. The other shipped
template, ``hkfrs_hk_china_v1``, is now seeded TEMPLATE-ONLY: there is one configuration and it
targets output_csv_hk_v1, so nothing is published against the HKFRS template. That is the
instructed consequence of collapsing two engines into one configuration, not an oversight — a run
against ``hkfrs_hk_china_v1`` finds no set in force (``services.config_select`` returns None) and
maps nothing until somebody publishes a set that targets it. Coverage loss was accepted explicitly:
a blank cell a configurator can fix beats a filled cell produced by a path that is supposed to be
gone.

REFRESHED ON EVERY BOOT, NOT SEEDED ONCE. This used to write a version 1 only when NO version of
the key existed, and then never look at the files again. So every edit made to the shipped files
after a database's first startup reached the test suite and no running app: ``tests/conftest.py``
points ``FINEX_DATABASE_URL`` at a fresh temp database, so pytest always seeds the CURRENT file and
could never see the drift, while the dev database went on serving whatever was seeded first.

What that cost, in the words of the person who reported it: "Gross Profit and the other calculated
totals still render at the END of the template". They did. The row builder emits the template's own
order faithfully (``services/statement_rows``); the template it was handed was the pre-revision
profit-and-loss, whose top-level order is the seven sections followed by every calculated total
bunched at the bottom — four revisions of the shipped file, a tax bucket removal and 12 new
definitions, none of which had ever reached the product. A seeding function that looks like it keeps
the app current and does not is the defect class this file now exists to prevent.

Comparison is on CANONICAL CONTENT (a sorted, separator-stable JSON dump), not on any field-by-field
guess about what "changed": the drift above was spread across statement order, ids, rollup children,
aliases, criteria and metadata at once, and a guess about which of those to compare is a guess that
eventually misses one. Identical content adds NOTHING — that is the property that lets this run on
every boot.

WHEN THE FILE WINS, stated rather than discovered (the rule is :func:`_plan`): a shipped file whose
content was never stored publishes on top of whatever is in force, an edit made through the
configuration screen or an uploaded workbook ON A SHIPPED KEY included, because shipping a fix has to
change what runs. A file whose content WAS stored before is republished as the newest only over the
seeder's own last write — the reverted-file case — and never over a version somebody else published
after it. Edits meant to outlive the next shipped change belong in the repo's files, or under a key
of their own; an uploaded set keeps its own ``line_items_key`` and is never written here.

Every file is put through the checks the publish routes apply, BEFORE anything is written. This path
used to write whatever was on disk: a shipped file that no longer loads would then sit in the
database as a row the read path chokes on — a 500 on the configuration screen, or an extraction that
reports SUCCEEDED with its structural checks silently gone (see ``loader.unknown_keys``). A startup
that fails with the offending path named is the cheaper failure, and it can only be reached by a
defect in the repo's own files.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

_LOG = logging.getLogger(__name__)
_DIR = Path(__file__).resolve().parent / "templates"
_TEMPLATE = _DIR / "hkfrs_hk_china_template.json"
# THE configuration file. One engine, one shipped set; ``_ONTOLOGY`` stood here and is gone with the
# rulebook it named.
_LINE_ITEMS = _DIR / "output_csv_hk_line_items.json"

# Additional shipped pairs seeded alongside the primary template. Each entry is
# (template_path, line_items_path). Treated identically to the primary template at
# startup, by one rule (``_plan``), and REQUIRED exactly as it is: a file that is
# missing, unreadable or refused by its gate stops the boot with the file named
# (``_gate_pair``). A deployment that means to ship without a pair removes it here.
_EXTRA_PAIRS: list[tuple[Path, Path]] = [
    (_DIR / "output_csv_hk_v1_template.json", _LINE_ITEMS),
    # IND AS, so an analyst can CHOOSE it on the extraction screen.
    #
    # A SECOND SHIPPED PAIR IS NOT A SECOND ENGINE, which is what the paragraph above warns
    # against. `services.config_select` answers "which set is in force" PER TEMPLATE — "more than
    # one set can target the same template" — and the Ind AS set declares
    # `target_template_key: output_csv_indas_v1`, its own. So publishing it adds a choice to the
    # picker and changes nothing about which set answers for `output_csv_hk_v1`. What the warning
    # forbids is reinstating a stored, selectable RULEBOOK beside the line items; this is a second
    # line-item set, the one engine there is.
    (_DIR / "output_csv_indas_v1_template.json", _DIR / "output_csv_indas_line_items.json"),
    # ICON, the Indian bank credit-monitoring (CMA-style) spread, for the same reason: its own
    # template and its own set (`target_template_key: output_csv_icon_v1`), built by
    # `scripts/build_icon_pair.py` from the Company v3 workbook draft.
    (_DIR / "output_csv_icon_v1_template.json", _DIR / "output_csv_icon_line_items.json"),
]

# Item-level keys the line-item schema does not declare AND whose loss is already measured,
# explained and pinned by a test — so the stray-key gate below must not read them as a shipped file
# authored against some other format.
#
# Exactly one, ``note_use_rationale``. It is a member of ``ontology_projection.SAME`` with no home
# on ``LineItemDef``, so the projection wrote it onto 394 of the set's 475 items on disk and
# pydantic's ``extra='ignore'`` drops it at load. It is NOT lost information: ``section_defaults``
# declares the same field, folding the section layer brings it back on every one of those items, and
# ``services/working_view`` measures both halves of that (``UNHOMED``, and the 394-item difference
# between ``resolve=False`` and ``resolve=True``) with ``tests/test_working_view_parity.py`` pinning
# it. Named here rather than computed from ``UNHOMED`` on purpose: importing the projection would
# make a BOOT depend on the ontology-shaped modules this merge is retiring, which is the one
# dependency direction that must not exist.
#
# Anything else undeclared still fails the gate, which is the whole point — the allowance is one
# measured key, not "ignore stray keys on items".
_ITEM_KEYS_WITH_NO_HOME = ("note_use_rationale",)


class ReferenceSeedError(RuntimeError):
    """A shipped reference file cannot be seeded — it would not survive its own upload gate."""


@lru_cache(maxsize=8)
def _key_in_file(path: str, mtime: float, field: str) -> str:
    """``field`` read off a shipped JSON file, cached on the file's identity AND its mtime.

    Cached because callers ask "which set / which template ships?" on request paths and the shipped
    set is a quarter of a megabyte of JSON. Keyed on (path, mtime) rather than on nothing, because a
    cache that answers from a file it no longer reflects is the same class of stale declaration this
    module was rewritten to remove: an edited file, and a test that monkeypatches ``_LINE_ITEMS`` to
    point elsewhere, both change the key and are re-read.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A file that cannot be read or parsed is reported by ``ensure_reference_data``, loudly,
        # with the path in the message. Callers asking only "which key ships?" get "no answer" and
        # fall back to their own ordering rather than crashing a read path on a broken sample file.
        return ""
    # ``isinstance`` and not ``.get`` on faith: valid JSON whose top level is a list, a string or
    # null would raise AttributeError straight out of the caller, which is a 500 on a read path —
    # the opposite of the fallback promised above.
    return str(raw.get(field) or "") if isinstance(raw, dict) else ""


def shipped_line_items_key() -> str:
    """The ``line_items_key`` of the configuration this repo ships, or "" when no file ships.

    Read from the file that ships it, so there is ONE spelling of "which configuration is ours". A
    constant here plus the key in the JSON would be two, and the day they disagreed the product
    would treat the shipped configuration as somebody's upload.

    Replaces ``shipped_ontology_key()``. Same job, one engine.
    """
    if not _LINE_ITEMS.exists():
        return ""
    return _key_in_file(str(_LINE_ITEMS), _LINE_ITEMS.stat().st_mtime, "line_items_key")


def shipped_template_key() -> str:
    """The ``template_key`` of the template this repo ships, or "" when no file ships."""
    if not _TEMPLATE.exists():
        return ""
    return _key_in_file(str(_TEMPLATE), _TEMPLATE.stat().st_mtime, "template_key")


def _canonical(definition: object) -> str:
    """A stable text rendering of a definition, for comparing stored content against the file.

    Sorted keys and fixed separators, so the comparison is by CONTENT and by nothing else: the
    stored side has been through a JSON column and the file side through ``json.loads``, and this
    makes "same content" independent of key order and of any type the round-trip narrows (a tuple
    returns as a list, and a raw ``==`` would then call every boot a change and publish an endless
    chain of identical versions). It is also one string per definition, so a report can show what
    was compared rather than assert a bare bool. ``ensure_ascii=False`` on both sides — the shipped
    configuration is half Chinese, and escaping one side only would make every comparison a
    difference.
    """
    return json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load_template(path: Path, raw: dict):
    from app.schemas.loader import load_template, unknown_keys, validate_template

    try:
        template = load_template(raw)
    except Exception as exc:  # noqa: BLE001 — re-raised with the path that has to be fixed
        raise ReferenceSeedError(f"{path.name} is not a valid template: {exc}") from exc
    stray = unknown_keys(raw, template, limit=10)
    if stray:
        raise ReferenceSeedError(
            f"{path.name} carries keys the template schema does not declare, which would be "
            f"dropped in silence: {stray}")
    # The same reference check the upload route runs (``routes.templates._publish``), which this
    # seeder claims to stand in for and did not run: a rollup child naming no node, an identity term
    # naming nothing, a KPI term naming a key the template never declares, a cycle among the KPI
    # intermediates. None of those stop a definition from LOADING — they stop it from ever computing
    # anything, silently, which is precisely what a shipped file must not be allowed to do.
    errors = validate_template(template)
    if errors:
        raise ReferenceSeedError(
            f"{path.name} would be refused by the template upload gate: "
            + "; ".join(f"{e.location}: {e.message}" for e in errors[:10]))
    return template


def _load_line_item_set(path: Path, raw: dict):
    """The shipped configuration, put through the gate ``/line-items`` applies before it is stored.

    Stands where ``_load_ontology`` stood, and keeps its contract exactly: a shipped file that would
    not survive its own door must fail the BOOT, with the path named, because the alternative is a
    stored row the read path chokes on and an extraction that reports SUCCEEDED with its checks
    gone. This is the boot-time non-negotiable, so it is deliberately the loudest thing in the file.

    Three checks, the same three the definition faces on publish:

    * ``load_line_item_set(raw, resolve=False)`` — the shape as authored.
    * ``load_line_item_set(raw, resolve=True)`` — the shape as MATCHED. A definition whose
      ``inherits`` names no section is not a load error, it is a silent no-op that leaves the item
      with no section gate at all — the one failure mode a shipped configuration could carry
      unnoticed. 475 of 475 items take their gate from ``section_defaults`` through ``inherits``, so
      the unresolved load alone proves almost nothing.
    * ``unknown_keys`` — a key the schema does not declare is dropped in silence, so the file
      publishes and simply does not contain what was authored.

    NOT checked here: ``services.line_items.build``'s registry verdict. A dangling formula input or
    a cascade with no terms is a CONFIGURATION problem, which the screen shows as a named problem
    and a configurator fixes in place; failing the boot on it would leave the whole product
    unreachable over one editable cell. The shipped set is clean today (0 problems over 475 items) —
    the distinction is about who can act on the failure, not about whether it currently fires.
    """
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import unknown_keys

    try:
        st = load_line_item_set(raw, resolve=False)
        load_line_item_set(raw, resolve=True)
    except Exception as exc:  # noqa: BLE001 — re-raised with the path that has to be fixed
        raise ReferenceSeedError(f"{path.name} is not a valid line-item set: {exc}") from exc
    # Walked WHOLE (a high limit) and truncated after filtering, not before: ``unknown_keys`` stops
    # walking once it has ``limit`` hits, and the ~394 accounted-for ``note_use_rationale`` paths
    # would otherwise fill the report and hide a real stray key later in the file.
    stray = [p for p in unknown_keys(raw, st, limit=5000) if not _accounted_for(p)][:10]
    if stray:
        raise ReferenceSeedError(
            f"{path.name} carries keys the line-item schema does not declare, which would be "
            f"dropped in silence: {stray}")
    return st


def _accounted_for(path: str) -> bool:
    """Whether one stray-key path is the single measured, test-pinned drop (see the tuple above).

    Scoped to ``items[...]`` and to the leaf name, so the allowance cannot widen by accident: the
    same field name appearing at set level, or on ``section_defaults``, is still a stray key,
    because there it IS declared and a stray report would mean something else had gone wrong.
    """
    return path.startswith("items[") and path.rsplit(".", 1)[-1] in _ITEM_KEYS_WITH_NO_HOME


def _newest(session: Session, model, key_column, key: str):
    """The highest-``version`` row of one key, or None.

    Ordered rather than "the first row that comes back": authoring and inline configuration edits
    publish further versions, so several rows share a key. Read ONCE per key and per pass, and both
    the decision (:func:`_plan`) and the next version number come off that one read — so a pass
    that lost a race to another booting process fails on the unique constraint and is retried,
    rather than deciding on one row and numbering from another.
    """
    return session.execute(
        select(model).where(key_column == key).order_by(model.version.desc())
    ).scalars().first()


def _stored_earlier(session: Session, model, key_column, key: str, definition: object) -> bool:
    """Whether this exact content is stored under this key, at ANY version.

    Only one of the questions :func:`_plan` asks. It used to be the whole rule (as
    ``_already_stored``), and on its own it was a trap: a loadable but wrong shipped file that booted
    once became the newest version, and reverting the file then changed nothing, because the
    reverted content was "already stored". Measured: boot 2 published a variant as v2; boot 3, on
    the restored file, returned no notes and left v2 newest and published, and
    ``scripts/reconcile_reference_data.py --apply`` reached the same no-op.
    """
    canonical = _canonical(definition)
    return any(_canonical(row.definition) == canonical for row in session.execute(
        select(model).where(key_column == key)).scalars().all())


# What :func:`_plan` decides for one shipped file.
_NOTHING = "nothing"      # the newest version already carries the shipped content
_PUBLISH = "publish"      # nothing stored, or content never stored before: publish as the newest
_RESTORE = "restore"      # stored earlier, and the version in force is the seeder's own: republish
_HOLD = "hold"            # stored earlier, and somebody else's version is in force: leave it


def _plan(session: Session, newest, model, key_column, key: str, definition: object,
          *, seeder_in_force: bool) -> str:
    """THE RULE for one shipped file against its key. Four cases, asked in this order:

    1. Nothing stored under the key: PUBLISH v1.
    2. The newest version's content IS the shipped content: NOTHING. What lets this run every boot.
    3. The shipped content was never stored under the key: PUBLISH it as the newest. A genuinely
       new shipped file still wins over whatever is in force, an administrator's edit included —
       that is what shipping a fix means, and the superseded version is reported at WARNING.
    4. The shipped content WAS stored, at an older version: RESTORE it as the newest only when the
       version in force is this seeder's OWN write (``seeder_in_force``). That is the reverted file:
       the seeder published something, the file went back, and the database follows the file. When
       somebody else's version is in force — an upload through the API, an inline edit, a template
       upload's provisioned set — HOLD: it stays in force, because nothing about the shipped file
       is new and republishing it would overrule a person's work on every restart. The hold is a
       note, so the reconcile report says the file is not what runs.

    "The seeder's own write" is a recorded fact rather than a guess from content, because an
    administrator may upload exactly what the file says: see :func:`_template_seeded` and
    :func:`_line_items_seeded`.
    """
    if newest is None:
        return _PUBLISH
    if _canonical(newest.definition) == _canonical(definition):
        return _NOTHING
    if not _stored_earlier(session, model, key_column, key, definition):
        return _PUBLISH
    return _RESTORE if seeder_in_force else _HOLD


def _template_seeded(newest) -> bool:
    """Whether the newest version of a TEMPLATE key is this seeder's own write.

    ``is_published`` answers it, and on every database already in use: only this module sets the
    flag (``routes/templates._publish`` never does, and the column defaults False), and
    :func:`_clear_prior_published` moves it to the newest seeded row. So an administrator's upload
    on a shipped key is newest and unflagged, and a newest flagged row is one this seeder wrote.
    Templates resolve by highest version within the key, so the newest row is the one in force.
    """
    return bool(newest is not None and newest.is_published)


def _line_items_seeded(session: Session, key: str, target_template_key: str) -> bool:
    """Whether the configuration IN FORCE for the pair's template is this seeder's own write.

    In force, not "the newest of the shipped key": ``services.config_select`` picks the latest set
    across every key that targets the template, so an administrator's set uploaded under a key of
    their own is what runs, and restoring the shipped key over it would put the shipped set back in
    force on top of their work. ``LineItemVersion.seeded_from`` is written by this module and by no
    other writer; a row older than the column carries null and reads as "not the seeder's", the
    conservative answer — on such a database a reverted configuration is HELD, and reported, once,
    until a person republishes it.
    """
    from app.services.config_select import select_for_template

    in_force = select_for_template(session, target_template_key)
    return bool(in_force is not None and in_force.line_items_key == key
                and in_force.seeded_from)


def _clear_prior_published(session: Session, model, key_column, key: str) -> None:
    """Drop ``is_published`` from every stored row of ONE key, before a new version claims it.

    ``is_published`` names ONE row — the shipped definition in force — so the version it named stops
    being published when a newer one takes over. Nothing SELECTs on the flag today (template
    resolution goes through ``is_latest`` and ``max(version)``), so a flag left true on two rows is a
    claim that would be wrong the moment something did — and it is already wrong on the one screen
    that PRINTS it: ``frontend/src/screens/TemplateList.tsx`` badges every ``is_published`` row
    "Published".

    MODULE-LEVEL BECAUSE EVERY TEMPLATE WRITE NEEDS IT. This loop lived inline in ``_refresh`` and
    so covered the primary pair only, while the extra-pair seeder published each new version of an
    extra pair with ``is_published=True`` and cleared no prior. Measured on ``backend/finex.db``:
    ``output_csv_hk_v1`` — which IS the extra pair, and the template every output_csv_hk run targets
    — carried the flag on 19 rows at once (v5..v23), and the repo-root ``finex.db`` on all 6 of its
    rows. Not a one-off state: it reproduces on any deployment where an extra shipped template
    changes more than once. Kept as one helper rather than a second copy of the query, because two
    implementations of "which row is published" are how the two sets came to disagree in the first
    place.

    Only rows that ALREADY carry the flag are touched, so this can only clear and never badge: an
    uploaded revision of a shipped key is stored with ``is_published`` False on purpose
    (``routes/templates.py::_publish`` sets it never; ``models.TemplateVersion`` defaults it False),
    and stays False through this.

    TEMPLATES ONLY, now. ``LineItemVersion`` declares no ``is_published`` — the flag no reader
    consulted was not carried across to the one configuration table — so the configuration half of a
    pair has nothing to clear and does not call this.
    """
    for prior in session.execute(
        select(model).where(key_column == key, model.is_published.is_(True))
    ).scalars().all():
        prior.is_published = False


def _read_shipped(path: Path) -> dict:
    """One shipped file, read and parsed — or a ``ReferenceSeedError`` naming it.

    A MISSING FILE IS REFUSED, where it used to be skipped in silence (the primary template returned
    ``[]`` and an absent pair was passed over). No deployment of this repo legitimately ships
    without one: all of them are tracked in git and the documented install is editable
    (``pip install -e``). The one install that lacks them is a non-editable ``pip install .`` —
    ``pyproject.toml`` declares no package data, so the JSON never reaches site-packages — and that
    used to boot healthy with no reference data at all, every run mapping nothing. A deployment that
    MEANS to drop a pair says so by removing it from ``_EXTRA_PAIRS``, in a reviewed change, not by
    deleting a file and letting the boot shrug.

    Unparseable JSON is refused with the name too: it used to escape as a bare ``JSONDecodeError``
    carrying a line and column and no file.
    """
    if not path.exists():
        raise ReferenceSeedError(
            f"{path.name} is missing from {path.parent} — every shipped reference file is required "
            f"(install with `pip install -e`; a pair is dropped by removing it from _EXTRA_PAIRS)")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReferenceSeedError(f"{path.name} cannot be read as JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReferenceSeedError(
            f"{path.name} is not a JSON object (its top level is {type(raw).__name__})")
    return raw


def _gate_pair(tpl_path: Path, li_path: Path) -> tuple[dict, dict]:
    """One shipped (template, line-item set) pair, through every gate — or a ``ReferenceSeedError``.

    THE PAIR FAILS THE BOOT, as the primary template always did. It used to be caught here, logged
    at ERROR and skipped, and ``output_csv_hk_v1`` — the template every output_csv_hk run targets —
    IS this pair. Measured: a refused HK template booted healthy; on a fresh database the stored
    keys were ``hkfrs_hk_china_v1`` and ``output_csv_indas_v1`` and the one configuration the
    product ships was simply absent, and on an existing one the previous stored version stayed in
    force. One ERROR line, which nothing reads, was the whole of the record.

    Two checks beyond the per-file gates, both about the PAIR: the set must name a
    ``line_items_key`` to be versioned under, and it must target its partner — a set stored against
    another template key is one ``config_select`` never finds for this template.
    """
    tpl_raw = _read_shipped(tpl_path)
    _load_template(tpl_path, tpl_raw)
    li_raw = _read_shipped(li_path)
    _load_line_item_set(li_path, li_raw)
    if not li_raw.get("line_items_key"):
        raise ReferenceSeedError(f"{li_path.name} declares no line_items_key to be versioned under")
    if li_raw.get("target_template_key") != tpl_raw["template_key"]:
        raise ReferenceSeedError(
            f"{li_path.name} targets {li_raw.get('target_template_key')!r}, not its partner "
            f"{tpl_path.name} ({tpl_raw['template_key']!r})")
    return tpl_raw, li_raw


def _seed_template(session: Session, path: Path, tpl: dict, dry_run: bool,
                   notes: list[str], replaced: list[str]) -> None:
    """Hold one template key to its shipped file, by :func:`_plan`. Writes; never commits."""
    from app.db.models import TemplateVersion

    key = tpl["template_key"]
    newest = _newest(session, TemplateVersion, TemplateVersion.template_key, key)
    action = _plan(session, newest, TemplateVersion, TemplateVersion.template_key, key, tpl,
                   seeder_in_force=_template_seeded(newest))
    if action == _NOTHING:
        return
    if action == _HOLD:
        notes.append(f"template {key}: {path.name} matches an earlier stored version, but v"
                     f"{newest.version} was not written by this seeder and stays in force")
        return
    version = 1 if newest is None else newest.version + 1
    if action == _RESTORE:
        notes.append(f"template {key}: restored {path.name} as v{version} over the seeder's v"
                     f"{newest.version}")
        replaced.append(f"template {key} v{newest.version} is no longer the newest: {path.name} "
                        f"was reverted to content stored earlier and is republished as v{version}")
    else:
        notes.append(f"template {key}: published v{version} from {path.name}"
                     + ("" if newest is None else f" (stored v{newest.version} differed)"))
        if newest is not None:
            replaced.append(f"template {key} v{newest.version} is no longer the newest: "
                            f"{path.name} has changed and is published as v{version}, which "
                            f"takes precedence for the next run")
    if not dry_run:
        _clear_prior_published(session, TemplateVersion, TemplateVersion.template_key, key)
        session.add(TemplateVersion(
            template_key=key, name=tpl.get("name", ""), version=version,
            definition=tpl, is_published=True,
        ))


def _seed_line_items(session: Session, path: Path, li: dict, dry_run: bool,
                     notes: list[str], replaced: list[str]) -> None:
    """Hold one line-item key to its shipped file, by :func:`_plan`. Writes; never commits.

    A ``LineItemVersion`` row is what an extraction run PINS (``extraction_runs.line_item_version_id``),
    so this is the row that makes a run reproducible — which is why the set is stored rather than
    read off disk at match time.
    """
    from app.db.models import LineItemVersion

    key, target = li["line_items_key"], li["target_template_key"]
    newest = _newest(session, LineItemVersion, LineItemVersion.line_items_key, key)
    action = _plan(session, newest, LineItemVersion, LineItemVersion.line_items_key, key, li,
                   seeder_in_force=_line_items_seeded(session, key, target))
    if action == _NOTHING:
        return
    if action == _HOLD:
        notes.append(f"line items {key}: {path.name} matches an earlier stored version, but the "
                     f"set in force for {target} was not written by this seeder and stays in force")
        return
    version = 1 if newest is None else newest.version + 1
    if action == _RESTORE:
        notes.append(f"line items {key}: restored {path.name} as v{version} over the seeder's v"
                     f"{newest.version}")
        replaced.append(f"line items {key} v{newest.version} is no longer the newest: {path.name} "
                        f"was reverted to content stored earlier and is republished as v{version}")
    else:
        notes.append(f"line items {key}: published v{version} from {path.name}")
        if newest is not None:
            replaced.append(f"line items {key} v{newest.version} superseded by v{version}")
    if not dry_run:
        session.add(LineItemVersion(
            line_items_key=key, target_template_key=target, version=version, definition=li,
            seeded_from=path.name,
        ))


def ensure_reference_data(session: Session, *, dry_run: bool = False) -> list[str]:
    """Hold the stored templates + line-item set to the shipped files. Returns what it found and did.

    The notes are the report ``scripts/reconcile_reference_data.py`` prints, and what this logs: one
    line per version published. No notes at all is the normal outcome of a restart against a database
    already carrying this repo's files.

    ``dry_run`` returns the same notes and writes nothing, so that script can show an operator what
    it is about to publish BEFORE it publishes it. A flag on this function rather than a second
    function that answers "what would change?": two implementations of one comparison are how the
    report comes to describe something other than what the run does.
    """
    from sqlalchemy.exc import IntegrityError

    try:
        return _refresh(session, dry_run=dry_run)
    except IntegrityError:
        # TWO PROCESSES BOOTING AT ONCE, which is a normal state for this repo: any two servers
        # pointed at one sqlite file race here. (The e2e suite is no longer one of them — it sets
        # FINEX_DATABASE_URL to its own scratch database and wipes it per run, so it stopped sharing
        # ``backend/finex.db`` with a developer's server. The race is still reachable by two servers
        # started by hand, and it is the wipe that makes the suite's own start-up safe: this
        # function re-publishes the shipped files from app/sample/templates/*.json on EVERY boot, so
        # an empty database is re-seeded rather than left bare.) Both read the same newest version,
        # both insert v N+1, and the loser violates uq_tpl_ver / uq_li_ver. Retried ONCE, because
        # after the rollback the winner's row is visible: if it carries the shipped content there is
        # nothing left to do, and if it does not, the next version number is now free. A second
        # failure is a state this handler does not understand and is raised — startup failing loudly
        # beats an app serving reference data nobody can account for.
        session.rollback()
        return _refresh(session, dry_run=dry_run)


def _refresh(session: Session, *, dry_run: bool) -> list[str]:
    """One pass of the comparison and its writes, so the retry above can just run it again.

    Everything it decides is derived from the files and the rows it reads here, nothing from the
    attempt before — which is what makes a second attempt after a lost race meaningful rather than a
    replay of a stale answer.

    EVERY FILE IS GATED BEFORE ANYTHING IS WRITTEN, and the pass is ONE transaction. The extra pairs
    used to be gated one at a time and committed one at a time, so a refusal in the second pair left
    the first pair's rows behind it; now a refusal anywhere — the primary template, the HK pair, the
    Ind AS pair — raises with the file named and the database is as it was.

    THE PRIMARY PAIR IS TEMPLATE-ONLY. ``hkfrs_hk_china_template.json`` is seeded; the rulebook that
    used to be seeded beside it is deleted along with the engine that read it, and no line-item set
    replaces it because the one shipped configuration targets ``output_csv_hk_v1``. Instructed, not
    forgotten — see the module docstring.
    """
    tpl = _read_shipped(_TEMPLATE)
    _load_template(_TEMPLATE, tpl)
    pairs = [(tpl_path, li_path, *_gate_pair(tpl_path, li_path))
             for tpl_path, li_path in _EXTRA_PAIRS]

    notes: list[str] = []
    # What the refresh REPLACED, kept apart from ``notes`` so it can be logged at WARNING. A stored
    # version differing from the file is either drift this function is here to close or an edit
    # somebody made through the configuration screen / an uploaded workbook on a shipped key — and
    # that edit does not survive this. It is a WARNING and not an INFO because this app configures no
    # logging, so only WARNING and above reaches stderr through ``logging.lastResort``: the one
    # message that must not be swallowed is the one saying something a user typed has been replaced.
    replaced: list[str] = []
    _seed_template(session, _TEMPLATE, tpl, dry_run, notes, replaced)

    # WHAT WAS HERE AND IS GONE, so nobody reinstates it: the primary pair's ontology publish, and
    # the ``RETIRED_ONTOLOGY_KEYS`` sweep that reported ``hkfrs_hk_china_v1`` / ``hkfrs_hk_china_v2``
    # as stored-but-replaced. Both belonged to a world with several rival rulebooks to rank. There is
    # one configuration engine now, its rows live in ``line_item_versions``, and ``ontology_versions``
    # is dropped by ``db.base`` on the way past — so there is no legacy row left to find, nothing to
    # label "replaced", and no key that could retire itself.

    # Seed each shipped (template, configuration) pair. These carry the configuration; the primary
    # template above carries none. Both template writes go through :func:`_clear_prior_published`,
    # so "one published row per key" is one rule with one implementation instead of a property of
    # whichever pair happened to be the primary one — it left 19 published rows in
    # ``backend/finex.db`` while it was not.
    for tpl_path, li_path, tpl_raw, li_raw in pairs:
        _seed_template(session, tpl_path, tpl_raw, dry_run, notes, replaced)
        _seed_line_items(session, li_path, li_raw, dry_run, notes, replaced)

    if not dry_run:
        session.commit()
        # Logged HERE, because the one caller on the startup path (``app/main.py``) has nothing to
        # print to and no reason to know the shape of these notes. Without this, a boot that
        # republished a template and a configuration left no trace anywhere, and the next reader to
        # ask why the product's mapping behaviour changed had only the row timestamps to go on.
        for note in notes:
            _LOG.info("reference data: %s", note)
        for lost in replaced:
            _LOG.warning("reference data: %s", lost)
    return notes
