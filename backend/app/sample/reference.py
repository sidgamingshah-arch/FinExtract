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

THE CONSEQUENCE, stated rather than discovered: an edit made through the configuration screen (or an
uploaded template workbook) ON A SHIPPED KEY is superseded by the file on the next restart, because
the file is what this function holds the database to. Edits meant to last belong in the repo's
files, or under a key of their own — an uploaded set keeps its own ``line_items_key`` and is never
touched here.

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
# startup: published when the file differs from the newest stored version, skipped
# when content is already stored, absent files skipped silently.
_EXTRA_PAIRS: list[tuple[Path, Path]] = [
    (_DIR / "output_csv_hk_v1_template.json", _LINE_ITEMS),
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
    publish further versions, so several rows share a key. Used to number the next version and to
    report what was found — NOT to decide whether to publish; see :func:`_already_stored`.
    """
    return session.execute(
        select(model).where(key_column == key).order_by(model.version.desc())
    ).scalars().first()


def _already_stored(session: Session, model, key_column, key: str, definition: object) -> bool:
    """Whether this exact shipped content is ALREADY stored under this key, at any version.

    WHY THIS AND NOT "does it differ from the newest version". Because an admin's own correction is
    usually the newest version, and comparing against it republishes the file on top of their work
    every time the server restarts. The rule this system now runs on is that the LATEST configuration
    wins (``services.config_select``), so a start-up that republishes the file unprompted silently
    overrules a human edit — the analyst fixes an alias, restarts, and their fix is gone with nothing
    saying so. Asking "have we published this file's content before?" leaves their edit latest, and
    therefore in force, which is what they asked for by making it.

    A genuinely NEW shipped file is still unseen, so it still publishes and still wins — that is the
    property the whole refresh exists for.

    The one case this gives up: reverting the shipped file to content published earlier is a no-op
    here, because that content IS stored. Rare, and recoverable on purpose rather than by accident —
    ``scripts/reconcile_reference_data.py --apply`` republishes the file as the newest version when an
    operator says to. Chosen deliberately over the alternative, which loses somebody's work on every
    restart.
    """
    canonical = _canonical(definition)
    return any(_canonical(row.definition) == canonical for row in session.execute(
        select(model).where(key_column == key)).scalars().all())


def _clear_prior_published(session: Session, model, key_column, key: str) -> None:
    """Drop ``is_published`` from every stored row of ONE key, before a new version claims it.

    ``is_published`` names ONE row — the shipped definition in force — so the version it named stops
    being published when a newer one takes over. Nothing SELECTs on the flag today (template
    resolution goes through ``is_latest`` and ``max(version)``), so a flag left true on two rows is a
    claim that would be wrong the moment something did — and it is already wrong on the one screen
    that PRINTS it: ``frontend/src/screens/TemplateList.tsx`` badges every ``is_published`` row
    "Published".

    MODULE-LEVEL BECAUSE ``_seed_extra_pair`` NEEDS IT TOO. This loop lived inline in ``_refresh``
    and so covered the primary pair only, while ``_seed_extra_pair`` published each new version of an
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


def _seed_extra_pair(
    session: Session,
    tpl_path: Path,
    li_path: Path,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    """Seed one (template, line-item set) pair without the primary-pair legacy logic."""
    from app.db.models import LineItemVersion, TemplateVersion

    notes: list[str] = []
    replaced: list[str] = []

    if not tpl_path.exists() or not li_path.exists():
        return notes, replaced

    try:
        tpl_raw = json.loads(tpl_path.read_text(encoding="utf-8"))
        template = _load_template(tpl_path, tpl_raw)
        li_raw = json.loads(li_path.read_text(encoding="utf-8"))
        _load_line_item_set(li_path, li_raw)
    except ReferenceSeedError as exc:
        _LOG.error("extra reference pair (%s, %s) refused: %s", tpl_path.name, li_path.name, exc)
        return notes, replaced

    tpl_key = tpl_raw["template_key"]
    newest_tpl = _newest(session, TemplateVersion, TemplateVersion.template_key, tpl_key)
    if not _already_stored(session, TemplateVersion, TemplateVersion.template_key, tpl_key, tpl_raw):
        version = 1 if newest_tpl is None else newest_tpl.version + 1
        notes.append(f"template {tpl_key}: published v{version} from {tpl_path.name}")
        if newest_tpl is not None:
            replaced.append(f"template {tpl_key} v{newest_tpl.version} superseded by v{version}")
        if not dry_run:
            _clear_prior_published(session, TemplateVersion, TemplateVersion.template_key, tpl_key)
            session.add(TemplateVersion(
                template_key=tpl_key, name=tpl_raw.get("name", ""), version=version,
                definition=tpl_raw, is_published=True,
            ))

    # The configuration half. A ``LineItemVersion`` row is what an extraction run PINS
    # (``extraction_runs.line_item_version_id``), so this is the row that makes a run reproducible —
    # which is why the set is stored rather than read off disk at match time.
    li_key = li_raw["line_items_key"]
    newest_li = _newest(session, LineItemVersion, LineItemVersion.line_items_key, li_key)
    if not _already_stored(session, LineItemVersion, LineItemVersion.line_items_key, li_key, li_raw):
        version = 1 if newest_li is None else newest_li.version + 1
        notes.append(f"line items {li_key}: published v{version} from {li_path.name}")
        if newest_li is not None:
            replaced.append(f"line items {li_key} v{newest_li.version} superseded by v{version}")
        if not dry_run:
            session.add(LineItemVersion(
                line_items_key=li_key, target_template_key=li_raw["target_template_key"],
                version=version, definition=li_raw,
            ))

    if not dry_run and notes:
        session.commit()

    return notes, replaced


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

    THE PRIMARY PAIR IS TEMPLATE-ONLY. ``hkfrs_hk_china_template.json`` is seeded; the rulebook that
    used to be seeded beside it is deleted along with the engine that read it, and no line-item set
    replaces it because the one shipped configuration targets ``output_csv_hk_v1``. Instructed, not
    forgotten — see the module docstring.
    """
    from app.db.models import TemplateVersion

    if not _TEMPLATE.exists():
        return []
    tpl = json.loads(_TEMPLATE.read_text(encoding='utf-8'))
    _load_template(_TEMPLATE, tpl)

    notes: list[str] = []
    # What the refresh REPLACED, kept apart from ``notes`` so it can be logged at WARNING. A stored
    # version differing from the file is either drift this function is here to close or an edit
    # somebody made through the configuration screen / an uploaded workbook on a shipped key — and
    # that edit does not survive this. It is a WARNING and not an INFO because this app configures no
    # logging, so only WARNING and above reaches stderr through ``logging.lastResort``: the one
    # message that must not be swallowed is the one saying something a user typed has been replaced.
    replaced: list[str] = []
    tpl_key = tpl["template_key"]
    newest_tpl = _newest(session, TemplateVersion, TemplateVersion.template_key, tpl_key)
    if not _already_stored(session, TemplateVersion, TemplateVersion.template_key, tpl_key, tpl):
        version = 1 if newest_tpl is None else newest_tpl.version + 1
        notes.append(f"template {tpl_key}: published v{version} from {_TEMPLATE.name}"
                     + ("" if newest_tpl is None else f" (stored v{newest_tpl.version} differed)"))
        if newest_tpl is not None:
            replaced.append(f"template {tpl_key} v{newest_tpl.version} is no longer the newest: "
                            f"{_TEMPLATE.name} has changed and is published as v{version}, which "
                            f"takes precedence for the next run")
        if not dry_run:
            _clear_prior_published(session, TemplateVersion, TemplateVersion.template_key, tpl_key)
            session.add(TemplateVersion(
                template_key=tpl_key, name=tpl.get("name", ""), version=version,
                definition=tpl, is_published=True,
            ))

    # WHAT WAS HERE AND IS GONE, so nobody reinstates it: the primary pair's ontology publish, and
    # the ``RETIRED_ONTOLOGY_KEYS`` sweep that reported ``hkfrs_hk_china_v1`` / ``hkfrs_hk_china_v2``
    # as stored-but-replaced. Both belonged to a world with several rival rulebooks to rank. There is
    # one configuration engine now, its rows live in ``line_item_versions``, and ``ontology_versions``
    # is dropped by ``db.base`` on the way past — so there is no legacy row left to find, nothing to
    # label "replaced", and no key that could retire itself.

    # Seed each shipped (template, configuration) pair. These carry the configuration; the primary
    # template above carries none.
    #
    # It DOES set is_published on the template, and this comment used to say it did not: the claim
    # sat 149 lines below the ``is_published=True`` it denied, and the missing prior-clearing loop it
    # excused left 19 published rows in ``backend/finex.db``. Both template writes now go through
    # :func:`_clear_prior_published`, so "one published row per key" is one rule with one
    # implementation instead of a property of whichever pair happened to be the primary one.
    for tpl_path, li_path in _EXTRA_PAIRS:
        extra_notes, extra_replaced = _seed_extra_pair(session, tpl_path, li_path, dry_run)
        notes.extend(extra_notes)
        replaced.extend(extra_replaced)

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
