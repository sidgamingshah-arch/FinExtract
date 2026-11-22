"""THE FOCUS LINE ITEMS MASTER — which line items a focus run asks the model about, PER TEMPLATE.

``extraction.llm_focus_only`` (the Settings screen's switch, OFF as shipped) restricts a run's model
requests to the focus line items of the run's template; every other line keeps what the rules gave
it. This is where an administrator says which lines those are, a template at a time: the
``focus_line_items_by_template`` table (``db.models.FocusLineItem``), kept from the Settings
screen's Focus line items tab through ``api/routes/focus_line_items.py``. It governs the rules
route's questions (``stages.line_item_llm``); the whole-PDF route asks every extracted line either
way.

PER TEMPLATE, BECAUSE KEYS ARE SHARED. 69 line-item keys are held by more than one shipped set
(``bs_nca__goodwill`` is in the HK and the Ind AS sets alike), and the master used to be one list
for every template: a line put in focus for one template was in focus for every set holding its
key, and the shipped HK entry "Sales revenues" was in focus for the Ind AS and US GAAP runs too. A
run now reads its own template's list and no other.

THE LISTS A RUN READS are ``Settings.extraction.llm_focus_keys``, a table keyed by template, which
``stages.line_item_llm`` reads per run (``line_item_requests.focus_list``). This module APPLIES the
master onto it — at startup (``settings_state.load_persisted``) and after every change — as the
Settings screen's other knobs are applied: a change takes effect on the next run and never rewrites
one that already happened. Like them, a second process picks up a change made in another on its
next restart.

SEEDED ONCE. config.toml's ``extraction.llm_focus_keys`` holds the SHIPPED list of each template.
When the table is created (``db.base.init_db``) it is filled — from the single list an older
database kept, each entry under every template whose set names it, so no run asks anything
different; on a fresh database, from config.toml — and from then on the table IS the lists: a
template with no entry has no line in focus, not "use the file". "Restore the shipped list" replaces
a template's entries with the file's.

WHAT A KEY MAY BE. A line-item key of the template's set in force, or a prefix pattern ending in
``*`` that names at least one line of it (``bs_icon__*``, every ICON balance-sheet line). Anything
else is refused, so a typo cannot sit in the master naming a line no run will ever return.

A DERIVED LINE IS NEVER ASKED ITSELF — its figure is its cascade's, computed from its parts
(``line_item_requests.asked_about``) — so putting one in focus alone would restrict a run to a line
nothing asks about. Adding one adds the parts its cascade reads as well, down to the lines that are
asked; the shipped lists are built the same way, each whole beside its parts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import select

PATTERN = "*"
SHIPPED = "shipped"
ADMIN = "admin"

# A line-item key ("bs_ca__cash_and_cash_equivalents", "sub__rp_find_2") or a prefix ending in "*".
_KEY = re.compile(r"^[a-z0-9][a-z0-9_]*\*?$")


class FocusError(ValueError):
    """A key the master refuses, with the reason an admin needs to act on."""


# ── the shipped list ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _shipped() -> dict[str, tuple[str, ...]] | tuple[str, ...]:
    """config.toml's ``extraction.llm_focus_keys`` as written: per template (the shipped shape), or
    one list (the shape it had when every template shared it), de-duplicated in its own order.

    Read from a FRESH ``Settings`` rather than the live one, which this module overwrites with the
    master's lists — so the shipped lists stay what the file (and the environment) says.
    """
    from app.config import Settings

    def clean(keys) -> tuple[str, ...]:
        return tuple(dict.fromkeys(k for k in (str(x).strip() for x in (keys or ())) if k))

    value = Settings().extraction.llm_focus_keys
    if isinstance(value, dict):
        return {str(t): clean(v) for t, v in value.items()}
    return clean(value)


def shipped_templates() -> list[str]:
    """The templates config.toml ships a list for (none when it is written as one list)."""
    value = _shipped()
    return list(value) if isinstance(value, dict) else []


def shipped_keys(template_key: str, lines: list[Line] | None = None) -> tuple[str, ...]:
    """The shipped list of one template. Written as one list, the file's list serves every template:
    a template's share is then the keys that name a line of its set (``lines``)."""
    value = _shipped()
    if isinstance(value, dict):
        return value.get(template_key, ())
    return tuple(k for k in value if lines is not None and matches(k, lines))


# ── the line items a key may name ────────────────────────────────────────────────────────────────

#: How a line answers a focus run: asked about; computed from its parts; or never asked (a line the
#: app or a person fills, or a section's "Others" bucket, which the leftover rows fill).
ASKED = "asked"
COMPUTED = "computed"
NOT_ASKED = "not_asked"


@dataclass
class Line:
    set_key: str
    key: str
    label: str
    statement: str
    kind: str
    parent: str = ""
    # For a computed line: the lines its cascade reads that are asked about, nested parts expanded.
    parts: list[str] = field(default_factory=list)
    template_key: str = ""

    def payload(self) -> dict:
        return {"set": self.set_key, "template": self.template_key, "key": self.key,
                "label": self.label, "statement": self.statement, "kind": self.kind,
                "parent": self.parent, "parts": list(self.parts)}


def _kind(item) -> str:
    from app.services.line_item_requests import asked_about

    if asked_about(item):
        return ASKED
    if str(getattr(item, "type", "") or "") == "derived":
        return COMPUTED
    return NOT_ASKED


def _refs(item) -> list[str]:
    """The keys a line's cascade and its own terms read, in their order."""
    refs = [t.ref for rung in (getattr(item, "cascade", None) or ())
            for t in (getattr(rung, "terms", None) or ()) if getattr(t, "ref", None)]
    refs += [t.ref for t in (getattr(item, "terms", None) or ()) if getattr(t, "ref", None)]
    return list(dict.fromkeys(refs))


def _asked_parts(key: str, by_key: dict, kinds: dict, seen: set | None = None) -> list[str]:
    """The asked-about lines under a computed line, nested computed parts expanded."""
    seen = set() if seen is None else seen
    out: list[str] = []
    for ref in _refs(by_key[key]):
        if ref in seen or ref not in by_key:
            continue
        seen.add(ref)
        if kinds[ref] == ASKED:
            out.append(ref)
        elif kinds[ref] == COMPUTED:
            out.extend(_asked_parts(ref, by_key, kinds, seen))
    return list(dict.fromkeys(out))


def _statement(item) -> str:
    st = getattr(item, "statement", None) or (
        (getattr(item, "statements", None) or [None])[0])
    return str(getattr(st, "value", st) or "")


_SETS_CACHE: dict[tuple, list[Line]] = {}


def lines_in_force(session, template_key: str | None = None) -> list[Line]:
    """Every line of every line-item set in force (one per template) — or of one template's — with
    how a focus run treats it. A set that does not load is left out: it governs no run either."""
    from app.db.models import LineItemVersion
    from app.schemas.line_items import load_line_item_set
    from app.services.config_select import select_for_template

    templates = sorted(set(session.execute(
        select(LineItemVersion.target_template_key)).scalars().all()))
    if template_key is not None:
        templates = [t for t in templates if t == template_key]
    only = template_key
    out: list[Line] = []
    current: set[tuple] = set()
    for tpl in templates:
        row = select_for_template(session, tpl)
        if row is None:
            continue
        cache_key = (tpl, row.id, row.updated_at, row.created_at)
        current.add(cache_key)
        if cache_key not in _SETS_CACHE:
            try:
                st = load_line_item_set(row.definition or {}, resolve=True)
            except Exception as exc:  # noqa: BLE001 - an unloadable set governs nothing
                import logging

                logging.getLogger(__name__).warning(
                    "focus master: line-item set %s v%s does not load and is left out of the "
                    "lines a focus key may name: %s", row.line_items_key, row.version, exc)
                continue
            by_key = {i.key: i for i in st.items}
            kinds = {k: _kind(i) for k, i in by_key.items()}
            parts = {k: _asked_parts(k, by_key, kinds) for k, kind in kinds.items()
                     if kind == COMPUTED}
            # A DERIVED LINE WITH NOTHING ASKED UNDER IT is the app's to fill (the HK set's
            # `periods`, a count of months): in focus it would name nothing a request answers.
            final = {k: (NOT_ASKED if kind == COMPUTED and not parts.get(k) else kind)
                     for k, kind in kinds.items()}
            _SETS_CACHE[cache_key] = [
                Line(set_key=row.line_items_key, key=i.key, label=i.label or i.key,
                     statement=_statement(i), kind=final[i.key],
                     parent=str(getattr(i, "parent", "") or ""),
                     parts=parts.get(i.key, []) if final[i.key] == COMPUTED else [],
                     template_key=tpl)
                for i in st.items]
        out.extend(_SETS_CACHE[cache_key])
    # A version no longer in force (an edit published a newer one) is not kept.
    for stale in [k for k in _SETS_CACHE if k not in current
                  and (only is None or k[0] == only)]:
        del _SETS_CACHE[stale]
    return out


def templates_in_force(session) -> list[dict]:
    """The templates a line-item set is in force for, each with its name and its set, in key order:
    the ones a focus list can be kept for."""
    from app.db.models import TemplateVersion

    by_template: dict[str, str] = {}
    for ln in lines_in_force(session):
        by_template.setdefault(ln.template_key, ln.set_key)
    names: dict[str, str] = {}
    for tv in session.execute(select(TemplateVersion).where(
            TemplateVersion.template_key.in_(list(by_template)))
            .order_by(TemplateVersion.version)).scalars():
        if tv.name:
            names[tv.template_key] = tv.name
    return [{"key": t, "name": names.get(t, t), "set": by_template[t]}
            for t in sorted(by_template)]


def matches(key: str, lines: list[Line]) -> list[Line]:
    """The lines a master entry names: its own key, or every key its prefix pattern begins."""
    if key.endswith(PATTERN):
        prefix = key[:-1]
        return [ln for ln in lines if ln.key.startswith(prefix)]
    return [ln for ln in lines if ln.key == key]


def check_key(raw: str, lines: list[Line]) -> str:
    """The key as stored, or :class:`FocusError` saying why it is refused."""
    key = str(raw or "").strip()
    if not key:
        raise FocusError("Give a line-item key, or a prefix ending in * (for example bs_icon__*).")
    if not _KEY.match(key) or (key.endswith(PATTERN) and len(key) < 3):
        raise FocusError(f"{key!r} is not a line-item key: keys are lower-case letters, digits "
                         f"and underscores, and a prefix pattern ends in a single *.")
    if not matches(key, lines):
        what = "starts with" if key.endswith(PATTERN) else "is named"
        raise FocusError(f"No line item of this template's line-item set {what} "
                         f"{key.rstrip(PATTERN)!r}.")
    return key


# ── the master ───────────────────────────────────────────────────────────────────────────────────

def _rows(session, template_key: str | None = None) -> list:
    from app.db.models import FocusLineItem

    q = select(FocusLineItem)
    if template_key is not None:
        q = q.where(FocusLineItem.template_key == template_key)
    return list(session.execute(q.order_by(FocusLineItem.template_key, FocusLineItem.key))
                .scalars().all())


def keys(session, template_key: str) -> list[str]:
    return [r.key for r in _rows(session, template_key)]


def lists(session) -> dict[str, list[str]]:
    """Every template's focus list, as a run reads them."""
    out: dict[str, list[str]] = {}
    for r in _rows(session):
        out.setdefault(r.template_key, []).append(r.key)
    return out


def apply(session=None) -> dict[str, list[str]] | None:
    """Put the master's lists onto the live settings, which a run reads. Returns them, or None when
    the table cannot be read (a database without it yet), in which case nothing changes."""
    from app.config import get_settings

    try:
        if session is not None:
            listed = lists(session)
        else:
            from app.db.base import SessionLocal

            with SessionLocal() as own:
                listed = lists(own)
    except Exception:  # noqa: BLE001 - best effort, as every settings override is
        return None
    get_settings().extraction.llm_focus_keys = listed
    return listed


def seed_shipped(session, template_key: str, *, replace: bool = False) -> int:
    """Fill one template's list with config.toml's. ``replace`` empties it first ("Restore the
    shipped list"); without it only keys not already there are added. Returns how many were added."""
    from app.db.models import FocusLineItem

    if replace:
        for row in _rows(session, template_key):
            session.delete(row)
        session.flush()
    have = set(keys(session, template_key))
    lines = None if isinstance(_shipped(), dict) else lines_in_force(session, template_key)
    added = 0
    for key in shipped_keys(template_key, lines):
        if key not in have:
            session.add(FocusLineItem(template_key=template_key, key=key, source=SHIPPED, note=""))
            have.add(key)
            added += 1
    session.commit()
    return added


def seed_new_master(*, single_list_table: bool) -> None:
    """Fill the per-template master when its table is first created (``db.base.init_db``).

    From the SINGLE LIST an older database kept, when there is one: each entry goes under every
    template whose set in force it names — exactly the runs that read it until now, so no run asks
    anything different — with its note and its source; an entry naming no line of any set is
    dropped (no run could return it) and logged. The single list's table is then dropped. Without
    one, each template's list is config.toml's.
    """
    import logging

    from sqlalchemy import text

    from app.db.base import SessionLocal
    from app.db.models import FocusLineItem

    log = logging.getLogger(__name__)
    with SessionLocal() as s:
        if single_list_table:
            old = s.execute(text('SELECT "key", note, source FROM focus_line_items')).all()
            lines = lines_in_force(s)
            by_template: dict[str, list[Line]] = {}
            for ln in lines:
                by_template.setdefault(ln.template_key, []).append(ln)
            if by_template or not old:
                dropped: list[str] = []
                placed = 0
                for key, note, source in old:
                    homes = [t for t, ls in sorted(by_template.items()) if matches(key, ls)]
                    if not homes:
                        dropped.append(key)
                    for t in homes:
                        s.add(FocusLineItem(template_key=t, key=key, note=note or "",
                                            source=source or ADMIN))
                        placed += 1
                s.commit()
                s.execute(text("DROP TABLE IF EXISTS focus_line_items"))
                s.commit()
                log.warning(
                    "Focus line items are kept per template now: the single list's %d entries "
                    "became %d, each under every template whose line-item set names it%s.",
                    len(old), placed,
                    f"; {len(dropped)} named no line of any set and were dropped: "
                    f"{', '.join(sorted(dropped))}" if dropped else "")
                return
            log.warning(
                "Focus line items are kept per template now, but no line-item set is in force to "
                "say which template each of the single list's %d entries belongs to: each "
                "template's list is config.toml's, and the single list is left in its table.",
                len(old))
        for template_key in shipped_templates():
            seed_shipped(s, template_key)


def _template_or_refuse(session, template_key: str) -> str:
    key = str(template_key or "").strip()
    known = [t["key"] for t in templates_in_force(session)]
    if key not in known:
        raise FocusError(f"{key!r} is not a template a line-item set is in force for"
                         + (f" (one of: {', '.join(known)})." if known else "."))
    return key


def add(session, template_key: str, raw_key: str, note: str = "", *,
        with_parts: bool = True) -> list[str]:
    """Add a key to one template's list (and, for a computed line, the asked-about parts its
    cascade reads). Returns the keys added; a key already there is kept, its note updated when one
    is given."""
    from app.db.models import FocusLineItem

    template_key = _template_or_refuse(session, template_key)
    lines = lines_in_force(session, template_key)
    key = check_key(raw_key, lines)
    note = str(note or "").strip()[:256]
    wanted = [key]
    if with_parts and not key.endswith(PATTERN):
        for ln in matches(key, lines):
            if ln.kind == COMPUTED:
                wanted += ln.parts
    existing = {r.key: r for r in _rows(session, template_key)}
    added: list[str] = []
    for k in dict.fromkeys(wanted):
        row = existing.get(k)
        if row is None:
            session.add(FocusLineItem(template_key=template_key, key=k, source=ADMIN,
                                      note=note if k == key else f"part of {key}"[:256]))
            added.append(k)
        elif k == key and note:
            row.note = note
    session.commit()
    apply(session)
    return added


def remove(session, item_id: str) -> bool:
    from app.db.models import FocusLineItem

    row = session.get(FocusLineItem, item_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    apply(session)
    return True


def restore_shipped(session, template_key: str) -> int:
    template_key = _template_or_refuse(session, template_key)
    added = seed_shipped(session, template_key, replace=True)
    apply(session)
    return added


def payload(session, template_key: str | None = None) -> dict:
    """One template's list as the Settings screen shows it: each entry with the lines it names, the
    switch that decides whether it is used, the template's shipped list, every line of its set a key
    may name — and the templates a list can be kept for, each with how many entries it holds. With
    no template named, the first."""
    from app.config import get_settings

    templates = templates_in_force(session)
    counts: dict[str, int] = {}
    for r in _rows(session):
        counts[r.template_key] = counts.get(r.template_key, 0) + 1
    known = [t["key"] for t in templates]
    chosen = template_key if template_key in known else (known[0] if known else "")
    if template_key and template_key not in known:
        raise FocusError(f"{template_key!r} is not a template a line-item set is in force for.")
    lines = lines_in_force(session, chosen) if chosen else []
    items = []
    for row in (_rows(session, chosen) if chosen else []):
        named = matches(row.key, lines)
        items.append({
            "id": row.id, "template": row.template_key, "key": row.key, "note": row.note or "",
            "source": row.source or ADMIN,
            "pattern": row.key.endswith(PATTERN),
            "lines": [ln.payload() for ln in named[:1]] if not row.key.endswith(PATTERN) else [],
            "matched": len(named),
            "sets": sorted({ln.set_key for ln in named}),
            "label": (named[0].label if named and not row.key.endswith(PATTERN) else ""),
            "kind": ("" if not named or row.key.endswith(PATTERN) else named[0].kind),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    def shipped_of(t: str) -> tuple[str, ...]:
        # Written as one list, a template's share is the keys naming a line of its set.
        per_template = isinstance(_shipped(), dict)
        return shipped_keys(t, None if per_template else
                            (lines if t == chosen else lines_in_force(session, t)))

    return {
        "focus_only": bool(getattr(get_settings().extraction, "llm_focus_only", False)),
        "template": chosen,
        "templates": [{**t, "entries": counts.get(t["key"], 0),
                       "shipped": len(shipped_of(t["key"]))} for t in templates],
        "items": items,
        "shipped": list(shipped_of(chosen)) if chosen else [],
        # The lines a key may name: those of this template's set a focus run can ask about, and the
        # computed ones whose parts it can. A line nothing asks about (filled by the app, a person
        # or the leftover rows) is not offered.
        "candidates": [ln.payload() for ln in lines if ln.kind in (ASKED, COMPUTED)],
    }
