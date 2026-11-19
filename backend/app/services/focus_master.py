"""THE FOCUS LINE ITEMS MASTER — which line items a focus run asks the model about.

``extraction.llm_focus_only`` (the Settings screen's switch, OFF as shipped) restricts a run's model
requests to the focus line items; every other line keeps what the rules gave it. This is where an
administrator says which lines those are: the ``focus_line_items`` table
(``db.models.FocusLineItem``), kept from the Settings screen through
``api/routes/focus_line_items.py``. It governs the rules route's questions
(``stages.line_item_llm``); the whole-PDF route asks every extracted line either way.

THE LIST A RUN READS is ``Settings.extraction.llm_focus_keys``, which ``stages.line_item_llm`` reads
per run. This module APPLIES the master onto it — at startup (``settings_state.load_persisted``) and
after every change — as the Settings screen's other knobs are applied: a change takes effect on the
next run and never rewrites one that already happened. Like them, a second process picks up a change
made in another on its next restart.

SEEDED ONCE, FROM config.toml. config.toml's ``extraction.llm_focus_keys`` is the SHIPPED list. When
the table is created (``db.base.init_db``) it is filled from that list, and from then on the table
IS the list: an empty table means no line is in focus, not "use the file". "Restore the shipped
list" replaces the table with the file's list.

WHAT A KEY MAY BE. A line-item key of a set in force, or a prefix pattern ending in ``*`` that names
at least one line of one (``bs_icon__*``, every ICON balance-sheet line). Anything else is refused,
so a typo cannot sit in the master naming a line no run will ever return.

A DERIVED LINE IS NEVER ASKED ITSELF — its figure is its cascade's, computed from its parts
(``line_item_requests.asked_about``) — so putting one in focus alone would restrict a run to a line
nothing asks about. Adding one adds the parts its cascade reads as well, down to the lines that are
asked; the shipped list is built the same way, each whole beside its parts.
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
def shipped_keys() -> tuple[str, ...]:
    """config.toml's ``extraction.llm_focus_keys``, de-duplicated in its own order.

    Read from a FRESH ``Settings`` rather than the live one, which this module overwrites with the
    master's list — so the shipped list stays what the file (and the environment) says.
    """
    from app.config import Settings

    keys = (str(k).strip() for k in (Settings().extraction.llm_focus_keys or ()))
    return tuple(dict.fromkeys(k for k in keys if k))


# ── the line items a key may name ────────────────────────────────────────────────────────────────

#: How a line answers a focus run: asked about; computed from its parts; or never asked (a line the
#: app or a person fills, or a section's "Others" bucket, which the leftover rows fill).
ASKED = "asked"
COMPUTED = "computed"
NOT_ASKED = "not_asked"
#: An entry whose key the sets in force treat differently (asked in one, computed in another).
MIXED = "mixed"


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

    def payload(self) -> dict:
        return {"set": self.set_key, "key": self.key, "label": self.label,
                "statement": self.statement, "kind": self.kind, "parent": self.parent,
                "parts": list(self.parts)}


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


def lines_in_force(session) -> list[Line]:
    """Every line of every line-item set in force (one per template), with how a focus run treats
    it. A set that does not load is left out: it governs no run either."""
    from app.db.models import LineItemVersion
    from app.schemas.line_items import load_line_item_set
    from app.services.config_select import select_for_template

    templates = sorted(set(session.execute(
        select(LineItemVersion.target_template_key)).scalars().all()))
    out: list[Line] = []
    current: set[tuple] = set()
    for template_key in templates:
        row = select_for_template(session, template_key)
        if row is None:
            continue
        cache_key = (row.id, row.updated_at, row.created_at)
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
                     parts=parts.get(i.key, []) if final[i.key] == COMPUTED else [])
                for i in st.items]
        out.extend(_SETS_CACHE[cache_key])
    # A version no longer in force (an edit published a newer one) is not kept.
    for stale in [k for k in _SETS_CACHE if k not in current]:
        del _SETS_CACHE[stale]
    return out


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
        raise FocusError(f"No line item of the line-item sets in force {what} "
                         f"{key.rstrip(PATTERN)!r}.")
    return key


# ── the master ───────────────────────────────────────────────────────────────────────────────────

def _rows(session) -> list:
    from app.db.models import FocusLineItem

    return list(session.execute(select(FocusLineItem).order_by(FocusLineItem.key)).scalars().all())


def keys(session) -> list[str]:
    return [r.key for r in _rows(session)]


def apply(session=None) -> list[str] | None:
    """Put the master's list onto the live settings, which a run reads. Returns the list, or None
    when the table cannot be read (a database without it yet), in which case nothing changes."""
    from app.config import get_settings

    try:
        if session is not None:
            listed = keys(session)
        else:
            from app.db.base import SessionLocal

            with SessionLocal() as own:
                listed = keys(own)
    except Exception:  # noqa: BLE001 - best effort, as every settings override is
        return None
    get_settings().extraction.llm_focus_keys = listed
    return listed


def seed_shipped(session=None, *, replace: bool = False) -> int:
    """Fill the master with config.toml's list. ``replace`` empties it first ("Restore the shipped
    list"); without it only keys not already there are added. Returns how many were added."""
    from app.db.models import FocusLineItem

    def _do(s) -> int:
        if replace:
            for row in _rows(s):
                s.delete(row)
            s.flush()
        have = set(keys(s))
        added = 0
        for key in shipped_keys():
            if key not in have:
                s.add(FocusLineItem(key=key, source=SHIPPED, note=""))
                have.add(key)
                added += 1
        s.commit()
        return added

    if session is not None:
        return _do(session)
    from app.db.base import SessionLocal

    with SessionLocal() as own:
        return _do(own)


def add(session, raw_key: str, note: str = "", *, with_parts: bool = True) -> list[str]:
    """Add a key (and, for a computed line, the asked-about parts its cascade reads). Returns the
    keys added; a key already in the master is kept, its note updated when one is given."""
    from app.db.models import FocusLineItem

    lines = lines_in_force(session)
    key = check_key(raw_key, lines)
    note = str(note or "").strip()[:256]
    wanted = [key]
    if with_parts and not key.endswith(PATTERN):
        for ln in matches(key, lines):
            if ln.kind == COMPUTED:
                wanted += ln.parts
    existing = {r.key: r for r in _rows(session)}
    added: list[str] = []
    for k in dict.fromkeys(wanted):
        row = existing.get(k)
        if row is None:
            session.add(FocusLineItem(key=k, source=ADMIN,
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


def restore_shipped(session) -> int:
    added = seed_shipped(session, replace=True)
    apply(session)
    return added


def payload(session) -> dict:
    """The master as the Settings screen shows it: each entry with the lines it names, the switch
    that decides whether it is used, the shipped list, and every line a key may name."""
    from app.config import get_settings

    lines = lines_in_force(session)
    by_key: dict[str, list[Line]] = {}
    for ln in lines:
        by_key.setdefault(ln.key, []).append(ln)
    items = []
    for row in _rows(session):
        named = matches(row.key, lines)
        items.append({
            "id": row.id, "key": row.key, "note": row.note or "", "source": row.source or ADMIN,
            "pattern": row.key.endswith(PATTERN),
            "lines": [ln.payload() for ln in named[:1]] if not row.key.endswith(PATTERN) else [],
            "matched": len(named),
            "sets": sorted({ln.set_key for ln in named}),
            "label": (named[0].label if named and not row.key.endswith(PATTERN) else ""),
            # A key several sets hold may be asked in one and computed in another: "mixed" says
            # so rather than reporting the first set's answer as everyone's.
            "kind": ("" if not named or row.key.endswith(PATTERN)
                     else named[0].kind if len({ln.kind for ln in named}) == 1 else MIXED),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        })
    return {
        "focus_only": bool(getattr(get_settings().extraction, "llm_focus_only", False)),
        "items": items,
        "shipped": list(shipped_keys()),
        # The lines a key may name: those a focus run can ask about, and the computed ones whose
        # parts it can. A line nothing asks about (filled by the app, a person or the leftover rows)
        # is not offered.
        "candidates": [ln.payload() for ln in lines if ln.kind in (ASKED, COMPUTED)],
    }
