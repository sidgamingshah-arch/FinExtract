"""Keep the DB's reference template + ontology equal to the files this repo ships.

Loads the shipped HKFRS/IFRS template and its companion rulebook (app/sample/templates/) into the
versioned tables, publishing a new version whenever the file differs from the newest stored one —
idempotent, safe to call on every startup. This is what lets an uploaded document be mapped
against a real ontology out of the box.

REFRESHED ON EVERY BOOT, NOT SEEDED ONCE. This used to write a version 1 only when NO version of
the key existed, and then never look at the files again. So every edit made to
``hkfrs_hk_china_template.json`` and ``hkfrs_hk_china_ontology.json`` after a database's first
startup reached the test suite and no running app: ``tests/conftest.py`` points
``FINEX_DATABASE_URL`` at a fresh temp database, so pytest always seeds the CURRENT file and could
never see the drift, while the dev database went on serving whatever was seeded first.

What that cost, in the words of the person who reported it: "Gross Profit and the other calculated
totals still render at the END of the template". They did. The row builder emits the template's own
order faithfully (``services/statement_rows``); the template it was handed was the pre-revision
profit-and-loss, whose top-level order is the seven sections followed by every calculated total
bunched at the bottom — four revisions of the shipped file, the one-template/one-rulebook
consolidation, a tax bucket removal and 12 new concepts, none of which had ever reached the
product. A seeding function that looks like it keeps the app current and does not is the defect
class this file now exists to prevent.

Comparison is on CANONICAL CONTENT (a sorted, separator-stable JSON dump), not on any field-by-field
guess about what "changed": the drift above was spread across statement order, node ids, rollup
children, aliases, criteria and metadata at once, and a guess about which of those to compare is a
guess that eventually misses one. Identical content adds NOTHING — that is the property that lets
this run on every boot.

THE CONSEQUENCE, stated rather than discovered: an edit made through the ontology editor (or an
uploaded template workbook) ON A SHIPPED KEY is superseded by the file on the next restart, because
the file is what this function holds the database to. Edits meant to last belong in the repo's
files, or under a key of their own — an uploaded rulebook keeps its own ``ontology_key`` and is
never touched here.

ONE template, ONE rulebook. Two generations used to ship side by side — a thin v1 and the v2 that
adds the section layer (``section_defaults`` + ``inherits``), the residual framework and the
validation block — on the reasoning that a run made against v1 should go on resolving to v1. That
reasoning is retired: there is one rulebook, and every concept is authored once in one vocabulary
instead of twice in two. Authoring it twice was not a theoretical cost — a concept written in the
other file's shape was a real defect twice over, caught each time by an invariant test rather than
by review.

Nothing here assumes it is the only rulebook that will ever exist. An uploaded or generated one
takes its place through the same versioned tables, and ``services/ontology_select`` still reads
``metadata.supersedes`` to decide which of several is in force.

Every file is put through the checks ``POST /ontologies`` / ``POST /templates`` apply, BEFORE
anything is written. This path used to write whatever was on disk: a shipped file that no longer
loads would then sit in the database as a row the read path chokes on — a 500 on the ontology
editor, or an extraction that reports SUCCEEDED with its structural checks silently gone (see
``loader.unknown_keys``). A startup that fails with the offending path named is the cheaper
failure, and it can only be reached by a defect in the repo's own files.
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
_ONTOLOGY = _DIR / "hkfrs_hk_china_ontology.json"

# Additional shipped pairs seeded alongside the primary one. Each entry is
# (template_path, ontology_path). Treated identically to the primary pair at
# startup: published when the file differs from the newest stored version, skipped
# when content is already stored, absent files skipped silently.
_EXTRA_PAIRS: list[tuple[Path, Path]] = [
    (_DIR / "output_csv_hk_v1_template.json", _DIR / "output_csv_hk_ontology.json"),
]

# The ontology keys this repo USED to ship the rulebook under, retired by naming them here.
#
# Two generations were seeded side by side until they were consolidated into one file keyed
# ``hkfrs_hk_china`` (commit 3b0529d): the thin ``hkfrs_hk_china_v1``, and ``hkfrs_hk_china_v2``,
# which declared it superseded the thin one. Every database older than that consolidation still
# holds them, and it holds them AHEAD of the shipped rulebook on both of the tests
# ``services/ontology_select`` applies: ``hkfrs_hk_china_v2`` declares a supersession (the shipped
# file declares none) and was seen first (the shipped key is not in such a database at all until
# this function publishes it). Left alone, a 173-concept rulebook with a tax sweep bucket and no
# "Direct costs" alias would stay in force over the 185-concept one this repo ships.
#
# An explicit list of keys THIS REPO shipped — never "any key the shipped set does not name", which
# would also retire a rulebook someone uploaded beside the shipped one and so reopen the hole
# incumbency was added to close (see ``ontology_select.select_for_template``).
#
# Deliberately NOT stamped onto the stored rows. ``schemas.ontology.OntologyMetadata`` declares no
# field for "I have been replaced", so a ``metadata.superseded_by`` written into a stored definition
# is a stray key: ``loader.unknown_keys`` reports it, ``POST /ontologies`` refuses that definition
# on re-upload, and the ontology model drops it on the next round-trip. A mark that evaporates is
# worse than no mark, and it would record one fact in two places that can then disagree.
#
# Retirement is by NAME, so a rulebook uploaded under one of these keys — an export restored from an
# older deployment, say — is treated as the legacy rulebook whose name it took: reported replaced,
# and never in force. Upload under a key of your own. Refusing it at the door belongs to
# ``POST /ontologies``, which is where an author is present to be told.
RETIRED_ONTOLOGY_KEYS = ("hkfrs_hk_china_v1", "hkfrs_hk_china_v2")


class ReferenceSeedError(RuntimeError):
    """A shipped reference file cannot be seeded — it would not survive its own upload gate."""


@lru_cache(maxsize=8)
def _key_in_file(path: str, mtime: float, field: str) -> str:
    """``field`` read off a shipped JSON file, cached on the file's identity AND its mtime.

    Cached because ``select_for_template`` asks for the shipped ontology_key on every request and
    the rulebook is a quarter of a megabyte of JSON. Keyed on (path, mtime) rather than on nothing,
    because a cache that answers from a file it no longer reflects is the same class of stale
    declaration this module was rewritten to remove: an edited file, and a test that monkeypatches
    ``_ONTOLOGY`` to point elsewhere, both change the key and are re-read.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A file that cannot be read or parsed is reported by ``ensure_reference_data``, loudly,
        # with the path in the message. Callers asking only "which key ships?" get "no answer" and
        # fall back to their own ordering rather than crashing a read path on a broken sample file.
        return ""
    # ``isinstance`` and not ``.get`` on faith: valid JSON whose top level is a list, a string or
    # null would raise AttributeError straight out of ``select_for_template``, which is a 500 on the
    # ontology list and the Template detail — the opposite of the fallback promised above.
    return str(raw.get(field) or "") if isinstance(raw, dict) else ""


def shipped_ontology_key() -> str:
    """The ``ontology_key`` of the rulebook this repo ships, or "" when no file ships.

    Read from the file that ships it, so there is ONE spelling of "which rulebook is ours". A
    constant here plus the key in the JSON would be two, and the day they disagreed the product
    would treat the shipped rulebook as somebody's upload.
    """
    if not _ONTOLOGY.exists():
        return ""
    return _key_in_file(str(_ONTOLOGY), _ONTOLOGY.stat().st_mtime, "ontology_key")


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
    was compared rather than assert a bare bool. ``ensure_ascii=False`` on both sides — the rulebook
    is half Chinese, and escaping one side only would make every comparison a difference.
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


def _load_ontology(path: Path, raw: dict, template):
    from app.schemas.loader import load_ontology, unknown_keys, validate_ontology_against_template

    try:
        # ``resolve=True`` on top of plain validation: a concept whose ``inherits`` names no
        # section is not a load error, it is a silent no-op that leaves the concept with no
        # section at all — the one failure mode a shipped rulebook could carry unnoticed.
        ontology = load_ontology(raw)
        load_ontology(raw, resolve=True)
    except Exception as exc:  # noqa: BLE001 — re-raised with the path that has to be fixed
        raise ReferenceSeedError(f"{path.name} is not a valid ontology: {exc}") from exc
    stray = unknown_keys(raw, ontology, limit=10)
    if stray:
        raise ReferenceSeedError(
            f"{path.name} carries keys the ontology schema does not declare, which would be "
            f"dropped in silence: {stray}")
    if ontology.target_template_key == template.template_key:
        errors = validate_ontology_against_template(ontology, template)
        if errors:
            raise ReferenceSeedError(
                f"{path.name} does not validate against {template.template_key!r}: "
                + "; ".join(f"{e.location}: {e.message}" for e in errors[:5]))
    return ontology


def _newest(session: Session, model, key_column, key: str):
    """The highest-``version`` row of one key, or None.

    Ordered rather than "the first row that comes back": authoring and inline ontology edits publish
    further versions, so several rows share a key. Used to number the next version and to report what
    was found — NOT to decide whether to publish; see :func:`_already_stored`.
    """
    return session.execute(
        select(model).where(key_column == key).order_by(model.version.desc())
    ).scalars().first()


def _already_stored(session: Session, model, key_column, key: str, definition: object) -> bool:
    """Whether this exact shipped content is ALREADY stored under this key, at any version.

    WHY THIS AND NOT "does it differ from the newest version". Because an admin's own correction is
    usually the newest version, and comparing against it republishes the file on top of their work
    every time the server restarts. The rule this system now runs on is that the LATEST rulebook wins
    (``services.ontology_select``), so a start-up that republishes the file unprompted silently
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


def _seed_extra_pair(
    session: Session,
    tpl_path: Path,
    ont_path: Path,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    """Seed one extra (template, ontology) pair without the primary-pair legacy logic."""
    from app.db.models import OntologyVersion, TemplateVersion

    notes: list[str] = []
    replaced: list[str] = []

    if not tpl_path.exists() or not ont_path.exists():
        return notes, replaced

    try:
        tpl_raw = json.loads(tpl_path.read_text(encoding="utf-8"))
        template = _load_template(tpl_path, tpl_raw)
        ont_raw = json.loads(ont_path.read_text(encoding="utf-8"))
        _load_ontology(ont_path, ont_raw, template)
    except ReferenceSeedError as exc:
        _LOG.error("extra reference pair (%s, %s) refused: %s", tpl_path.name, ont_path.name, exc)
        return notes, replaced

    tpl_key = tpl_raw["template_key"]
    newest_tpl = _newest(session, TemplateVersion, TemplateVersion.template_key, tpl_key)
    if not _already_stored(session, TemplateVersion, TemplateVersion.template_key, tpl_key, tpl_raw):
        version = 1 if newest_tpl is None else newest_tpl.version + 1
        notes.append(f"template {tpl_key}: published v{version} from {tpl_path.name}")
        if newest_tpl is not None:
            replaced.append(f"template {tpl_key} v{newest_tpl.version} superseded by v{version}")
        if not dry_run:
            session.add(TemplateVersion(
                template_key=tpl_key, name=tpl_raw.get("name", ""), version=version,
                definition=tpl_raw, is_published=True,
            ))

    ont_key = ont_raw["ontology_key"]
    newest_ont = _newest(session, OntologyVersion, OntologyVersion.ontology_key, ont_key)
    if not _already_stored(session, OntologyVersion, OntologyVersion.ontology_key, ont_key, ont_raw):
        version = 1 if newest_ont is None else newest_ont.version + 1
        notes.append(f"ontology {ont_key}: published v{version} from {ont_path.name}")
        if newest_ont is not None:
            replaced.append(f"ontology {ont_key} v{newest_ont.version} superseded by v{version}")
        if not dry_run:
            session.add(OntologyVersion(
                ontology_key=ont_key, target_template_key=ont_raw["target_template_key"],
                version=version, definition=ont_raw,
            ))

    if not dry_run and notes:
        session.commit()

    return notes, replaced


def ensure_reference_data(session: Session, *, dry_run: bool = False) -> list[str]:
    """Hold the stored template + ontology to the shipped files. Returns what it found and did.

    The notes are the report ``scripts/reconcile_reference_data.py`` prints, and what this logs: one
    line per version published, plus one per retired key found stored (a finding, not a write). No
    notes at all is the normal outcome of a restart against a database already carrying this repo's
    files.

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
        # function re-publishes the shipped template and rulebook from app/sample/templates/*.json
        # on EVERY boot, so an empty database is re-seeded rather than left bare.) Both read the
        # same newest version, both insert v N+1, and
        # the loser violates uq_tpl_ver / uq_ont_ver. Retried ONCE, because after the rollback the
        # winner's row is visible: if it carries the shipped content there is nothing left to do,
        # and if it does not, the next version number is now free. A second failure is a state this
        # handler does not understand and is raised — startup failing loudly beats an app serving
        # reference data nobody can account for.
        session.rollback()
        return _refresh(session, dry_run=dry_run)


def _refresh(session: Session, *, dry_run: bool) -> list[str]:
    """One pass of the comparison and its writes, so the retry above can just run it again.

    Everything it decides is derived from the files and the rows it reads here, nothing from the
    attempt before — which is what makes a second attempt after a lost race meaningful rather than a
    replay of a stale answer.
    """
    from app.db.models import OntologyVersion, TemplateVersion

    if not _TEMPLATE.exists() or not _ONTOLOGY.exists():
        return []
    tpl = json.loads(_TEMPLATE.read_text(encoding='utf-8'))
    template = _load_template(_TEMPLATE, tpl)

    raw_ontology = json.loads(_ONTOLOGY.read_text(encoding='utf-8'))
    _load_ontology(_ONTOLOGY, raw_ontology, template)

    ont_key = raw_ontology["ontology_key"]
    if ont_key in RETIRED_ONTOLOGY_KEYS:
        # Refused before anything is written: the shipped key appearing in the retired list would
        # have ``superseded_keys`` report the ONLY current rulebook as replaced, which turns every
        # run's rulebook record into "superseded" and leaves ``select_for_template`` falling back to
        # the whole list. Loud here, unexplainable three screens away.
        raise ReferenceSeedError(
            f"{_ONTOLOGY.name} ships ontology_key {ont_key!r}, which RETIRED_ONTOLOGY_KEYS also "
            f"names as retired — one of the two is wrong; a shipped rulebook cannot retire itself")

    notes: list[str] = []
    # What the refresh REPLACED, kept apart from ``notes`` so it can be logged at WARNING. A stored
    # version differing from the file is either drift this function is here to close or an edit
    # somebody made through the ontology editor / an uploaded workbook on a shipped key — and that
    # edit does not survive this. It is a WARNING and not an INFO because this app configures no
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
            # ``is_published`` names ONE row — the shipped definition in force — so the version it
            # named stops being published when a newer one takes over. Nothing selects on the flag
            # today (the screens read the newest version); a flag left true on two rows is a claim
            # that would be wrong the moment something did.
            for prior in session.execute(
                select(TemplateVersion).where(TemplateVersion.template_key == tpl_key,
                                              TemplateVersion.is_published.is_(True))
            ).scalars().all():
                prior.is_published = False
            session.add(TemplateVersion(
                template_key=tpl_key, name=tpl.get("name", ""), version=version,
                definition=tpl, is_published=True,
            ))

    newest_ont = _newest(session, OntologyVersion, OntologyVersion.ontology_key, ont_key)
    if not _already_stored(session, OntologyVersion, OntologyVersion.ontology_key, ont_key,
                           raw_ontology):
        version = 1 if newest_ont is None else newest_ont.version + 1
        notes.append(f"ontology {ont_key}: published v{version} from {_ONTOLOGY.name}"
                     + ("" if newest_ont is None else f" (stored v{newest_ont.version} differed)"))
        if newest_ont is not None:
            replaced.append(f"ontology {ont_key} v{newest_ont.version} did not match "
                            f"{_ONTOLOGY.name} and has been superseded by v{version} — an alias, "
                            f"criterion or netting rule edited through the ontology editor is NOT "
                            f"replaced by a restart — only a genuinely changed shipped file is "
                            f"published, and then it is the latest and takes precedence")
        if not dry_run:
            session.add(OntologyVersion(
                ontology_key=ont_key, target_template_key=raw_ontology["target_template_key"],
                version=version, definition=raw_ontology,
            ))

    # A legacy key found stored is REPORTED and nothing more. It no longer needs to be excluded from
    # anything: the rulebook in force is simply the latest one, and a legacy rulebook is by definition
    # older than the shipped file that replaced it, so it cannot win. RETIRED_ONTOLOGY_KEYS survives
    # for two narrower jobs — labelling those rows "replaced" on the ontology list
    # (``ontology_select.superseded_keys``), and telling the destructive clean in
    # ``scripts/reconcile_reference_data.py`` which rows an operator may want deleted. Reported here
    # because a database quietly holding two rival rulebooks is worth an operator knowing about.
    retired_present = session.execute(
        select(OntologyVersion.ontology_key)
        .where(OntologyVersion.ontology_key.in_(RETIRED_ONTOLOGY_KEYS)).distinct()
    ).scalars().all()
    for key in sorted(retired_present):
        notes.append(f"ontology {key}: stored but retired — superseded by {ont_key}")

    # Seed each extra shipped pair. No retirement handling or is_published flag — these are
    # supplementary templates that live alongside the primary pair as a second user choice.
    for tpl_path, ont_path in _EXTRA_PAIRS:
        extra_notes, extra_replaced = _seed_extra_pair(session, tpl_path, ont_path, dry_run)
        notes.extend(extra_notes)
        replaced.extend(extra_replaced)

    if not dry_run:
        session.commit()
        # Logged HERE, because the one caller on the startup path (``app/main.py``) has nothing to
        # print to and no reason to know the shape of these notes. Without this, a boot that
        # republished a template and passed over two rival rulebooks left no trace anywhere, and the
        # next reader to ask why the product's mapping behaviour changed had only the row timestamps
        # to go on.
        for note in notes:
            _LOG.info("reference data: %s", note)
        for lost in replaced:
            _LOG.warning("reference data: %s", lost)
    return notes
