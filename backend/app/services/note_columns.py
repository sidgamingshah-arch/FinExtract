"""Which printed column a note figure stands in, and which period that column is — one definition.

The reader names every figure's column from the table's printed header
(``ExtractedValue.column_heading``), lists every column the table prints
(``NoteItem.printed_columns``, blank ones included) and records the period the header states over
each column (``ExtractedValue.column_period``). Two routes then pick a figure BY COLUMN rather than
by its positional key — a model citing a column of a row, and a configured part selecting one (a
fair-value table's Level 3 column, a provision table's 坏账准备). They must read the same column the
same way, or the two routes publish different figures from one printed cell, so both call this
module and neither re-derives a rule of its own.

WHY NOT THE KEY. ``period_label`` is positional: on 河钢股份 000709's fair-value table ``current``
is the Level 3 column and ``prior`` is the SAME year-end's 合计; on 迈捷 300319's ``current`` is
Level 2. The key cannot say which column a figure is in, or which year.

Everything here reads attributes with ``getattr`` and a default, because rows and values reach it
as model objects and, in tests and some readers, as plain namespaces.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.services.han import has_han, to_simplified

# The value flag a two-level PRC header sets — mirrored from `row_reconstruct.GRID_FLAG` rather
# than imported, so a reader of this module does not pull in the whole reconstructor.
GRID_FLAG = "column_grid:period_x_measure"

_SPACES = re.compile(r"\s+")
# The joiner the reader puts between a period group and its column ("期末余额 · 账面余额").
_JOINERS = re.compile(r"[·•]")
_PERIODS = ("current", "prior")


def normalise(heading: str | None) -> str:
    """One canonical form for comparing two printed headings: NFKC (full-width brackets and
    digits become ASCII), Traditional folded to Simplified, case folded, the reader's own group
    joiner and every run of white space collapsed to one space. Empty for no heading."""
    if not heading:
        return ""
    text = unicodedata.normalize("NFKC", str(heading))
    text = to_simplified(text).casefold()
    text = _JOINERS.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _script_halves(text: str) -> list[str]:
    """The heading whole, and — for a bilingual heading — its Han and its Latin half, each on its
    own: "Total 總計" is also "Total" and "總計". Split by word, a word carrying any Han character
    belonging to the Han half."""
    out = [text]
    words = text.split(" ")
    han = " ".join(w for w in words if has_han(w)).strip()
    latin = " ".join(w for w in words if w and not has_han(w)).strip()
    if han and latin:
        out.extend([han, latin])
    return out


def heading_matches(heading: str | None, want: str | None) -> float:
    """How well a printed column heading answers to ``want``: 1.0 for the same heading once
    normalised (whole, or either script half of a bilingual one), else the containment score —
    the shorter's length over the longer's, so among several headings containing the words the
    one closest in length wins — and 0.0 for no match. A missing heading matches nothing."""
    h, w = normalise(heading), normalise(want)
    if not h or not w:
        return 0.0
    best = 0.0
    for part in _script_halves(h):
        if part == w:
            return 1.0
        short, long_ = (part, w) if len(part) <= len(w) else (w, part)
        if short and short in long_:
            best = max(best, len(short) / len(long_))
    return best


def _compiled(patterns: Iterable[str | re.Pattern] | None) -> list[re.Pattern]:
    out: list[re.Pattern] = []
    for p in patterns or ():
        out.append(p if isinstance(p, re.Pattern) else re.compile(p, re.IGNORECASE))
    return out


def matches_patterns(heading: str | None, any_patterns: Iterable[str | re.Pattern] | None,
                     none_patterns: Iterable[str | re.Pattern] | None = None) -> bool:
    """Whether a printed column heading is selected by a configured pattern pair.

    Each regular expression is SEARCHED, case-insensitively, on the whole heading and on each
    script half of a bilingual one, so "第三級" finds "Significant unobservable inputs (Level 3)
    重大不可觀察輸入數據（第三級）" and an English pattern is never defeated by the Chinese beside it.
    Selected when an ``any`` pattern finds it (or there are none) and no ``none`` pattern does.

    FAILS CLOSED. A figure with no heading is never selected, whatever the patterns: a column the
    reader could not name is not evidence for the column a part asked for.
    """
    if not heading:
        return False
    text = unicodedata.normalize("NFKC", str(heading))
    texts = _script_halves(_SPACES.sub(" ", text).strip())
    wanted = _compiled(any_patterns)
    vetoes = _compiled(none_patterns)
    if wanted and not any(p.search(t) for p in wanted for t in texts):
        return False
    return not any(p.search(t) for p in vetoes for t in texts)


def period_of(value, row=None) -> tuple[str | None, str]:
    """``(period, source)`` for a figure picked BY ITS COLUMN — the period it is filed under —
    or ``(None, "")`` when nothing the filing printed states one.

    In this order, and never from the positional key alone:

    1. ``column`` — the period the printed header states over the figure's column
       (``value.column_period``: 期末公允价值 → current);
    2. ``row_block`` — the period of the row's block (``row.period_hint``), for a table that prints
       its years as blocks of rows (1966's and 嘉民's fair-value hierarchies);
    3. ``grid`` — the base of a key read from a two-level period × measure header, which only a
       GRID-flagged value carries ('current:allowance' → current).

    A bare ``current``/``prior``/``col2`` is NOT a period here: on a table whose columns are levels,
    classes or measures it is a position.
    """
    period = getattr(value, "column_period", None)
    if period in _PERIODS:
        return period, "column"
    hint = getattr(row, "period_hint", None) if row is not None else None
    if hint in _PERIODS:
        return hint, "row_block"
    confidence = getattr(value, "confidence", None)
    flags = getattr(confidence, "flags", None) or ()
    label = getattr(value, "period_label", None) or ""
    if GRID_FLAG in flags:
        base = label.split(":", 1)[0]
        if base in _PERIODS:
            return base, "grid"
    return None, ""


# ══ A CITATION OF ONE COLUMN OF A ROW ════════════════════════════════════════════════════════════
#
# The periods a figure may be FILED under. Every other key a note row carries — `col2`, a measure
# `current:allowance`, a kept restatement `current_col3` / `current_restated` — is a position or a
# variant, and a line item has no slot for it.
FILED_PERIODS = _PERIODS
# Below this a heading is not the column the citation named: "第三层次" is a quarter of
# "期末公允价值 · 第三层次公允价值计量" and reads as noise beside a closer heading.
MATCH_FLOOR = 0.5


def _cells(row) -> list:
    """The row's figures a citation can name: not a matrix column, and printed with a value."""
    return [ev for ev in (getattr(row, "values", None) or {}).values()
            if getattr(ev, "column_index", None) is None and getattr(ev, "value", None) is not None]


def _leaf(heading: str | None) -> str:
    """The column's own heading under any group the reader joined over it: "期末余额 · 坏账准备" is
    also "坏账准备". The whole heading when there is no group."""
    if not heading:
        return ""
    return _JOINERS.split(str(heading))[-1].strip()


def _score(heading: str | None, want: str) -> float:
    return max(heading_matches(heading, want), heading_matches(_leaf(heading), want))


def _basis(ev) -> str:
    b = getattr(ev, "basis", "")
    return str(getattr(b, "value", b) or "")


def row_columns(row) -> list[str]:
    """Every column the row's table prints, by heading, in printed order — the header's list when
    the reader read one, else the headings the row's own figures carry, in key order."""
    printed = [h for h in (getattr(row, "printed_columns", None) or ()) if h]
    if printed:
        return list(dict.fromkeys(printed))
    return list(dict.fromkeys(str(ev.column_heading) for ev in _cells(row)
                              if getattr(ev, "column_heading", None)))


def blank_columns(row) -> list[str]:
    """The printed columns no figure of THIS row stands in, in printed order: a blank 第三层次
    beside a populated 第二层次. Compared normalised, so a heading two columns share is blank
    only when neither holds a figure."""
    held = {normalise(getattr(ev, "column_heading", None)) for ev in _cells(row)}
    return [h for h in dict.fromkeys(getattr(row, "printed_columns", None) or ())
            if h and normalise(h) not in held]


def _key_is_period(ev, row) -> bool:
    """Whether this figure's positional key may be read as the period it is filed under: a bare
    `current`/`prior` whose column the header did not name (an unheaded period table — the reading
    a whole-row citation has always taken), or whose printed period agrees with it."""
    label = str(getattr(ev, "period_label", "") or "")
    if label not in FILED_PERIODS:
        return False
    if not getattr(ev, "column_heading", None):
        return True
    return period_of(ev, row)[0] == label


def whole_row(row) -> tuple[list, str]:
    """``(cells, refusal)`` for a citation of a row WITHOUT a column.

    Only the row's ``current`` and ``prior`` are kept: ``col2``, a measure ``current:allowance``,
    a restatement ``current_col3`` are positions or variants, and a line has no slot for them.

    REFUSED (``refusal`` non-empty) when those two are not periods — a figure the header names
    whose period `period_of` cannot state (a fair-value level, an asset class, with no block period
    over the row) or states as the other one (000709's ``prior`` is the same year-end's 合计;
    1966's PPE block is all 2023, so its ``prior`` is the Leasehold improvements column). Filing
    such a row by its keys would publish a column as a year. A period table — headings whose
    period agrees with the key, or no headings at all — is untouched.
    """
    kept = [ev for ev in _cells(row)
            if str(getattr(ev, "period_label", "") or "") in FILED_PERIODS]
    columns = row_columns(row)
    listing = "; ".join(columns[:12])
    # The ask comes first, so a reason cut short on a row's flag still says what to do.
    ask = (" — give the `column` this line's figure is in"
           + (f"; the row's columns are {listing}" if listing else ""))
    if any(getattr(ev, "column_heading", None) and not _key_is_period(ev, row) for ev in kept):
        return kept, "that row's columns are not periods" + ask
    if not kept and _cells(row):
        return kept, "that row prints no current- or prior-period figure" + ask
    return kept, ""


# A filed period's key, bare or as the reader keeps a second column of one slot: `current_col1`,
# `prior_col3`, `prior_restated` (`row_reconstruct._column_periods`). Not a measure suffix.
_KEYED_PERIOD = re.compile(r"^(current|prior)(?:_col\d+|_restated)?$")


def _key_base(label: str) -> str | None:
    """The period a key's own slot names — `prior_col3` → prior — or None for a position."""
    m = _KEYED_PERIOD.match(label or "")
    return m.group(1) if m else None


def _only_heading_containing(row, cells, want: str) -> str | None:
    """The one distinct printed heading (normalised) of the row's table that contains ``want``,
    whole or by a script half — None when none does, or when two or more do."""
    w = normalise(want)
    if len(w) < 2:
        return None
    headings = {normalise(h) for h in (getattr(row, "printed_columns", None) or ()) if h}
    headings |= {normalise(getattr(ev, "column_heading", None)) for ev in cells}
    headings.discard("")
    hits = [h for h in headings if any(w in part for part in _script_halves(h))]
    return hits[0] if len(hits) == 1 else None


def pick(row, want: str | None, *, reporting_period: str = "current",
         keys_are_periods: bool = False) -> dict:
    """The figure(s) of ONE column of a row, as a citation names it — and the period each is filed
    under. One definition for every route that picks a figure by its column.

    Returns ``{"cells", "figures": {period: str}, "basis", "assumed", "why", "blank",
    "matched_by"}``; ``why`` is non-empty when the citation is refused, and then nothing is taken.
    Each cell is ``{"key", "heading", "period", "period_source", "value", "ev"}``.

    WHICH CELL. A positional key given as is (``col8``, ``current``) is taken first; otherwise the
    printed heading — the column's whole heading or its own under a group, by `heading_matches` —
    at the best score and not below `MATCH_FLOOR` (below it, only a ``want`` contained in exactly
    one distinct printed heading of the table). Several columns at that score are taken only
    when each is in a DIFFERENT period (期末 坏账准备 and 期初 坏账准备 are this year's and last
    year's); two in one period with different figures are refused, because the citation did not
    say which. A printed column that holds no figure on this row is refused with its own reason:
    it is not a figure, and never a zero.

    WHICH PERIOD. `period_of` — the period printed over the column, then the row's block. Else,
    for a bare key whose column is not named as anything else, the key itself (an unheaded period
    table, the reading `whole_row` takes; on the face of a statement,
    ``keys_are_periods``). Else
    the REPORTING period, and the result says it was ``assumed`` so the caller flags it: nothing
    the filing printed said which year that column is.

    ONE BASIS. The picked cells share one: consolidated when present, else the first.
    """
    want = str(want or "").strip()
    cells = _cells(row)
    out: dict = {"cells": [], "figures": {}, "basis": None, "assumed": False, "why": "",
                 "blank": False, "matched_by": ""}
    if not want:
        out["why"] = "no column was given"
        return out
    listing = "; ".join(row_columns(row)[:12])
    matched = [ev for ev in cells
               if str(getattr(ev, "period_label", "") or "").casefold() == want.casefold()]
    if matched:
        out["matched_by"] = "key"
    else:
        scored = [(_score(getattr(ev, "column_heading", None), want), ev) for ev in cells]
        best = max((s for s, _ev in scored), default=0.0)
        blanks = [(_score(h, want), h) for h in blank_columns(row)]
        best_blank = max((s for s, _h in blanks), default=0.0)
        if max(best, best_blank) < MATCH_FLOOR:
            # BELOW THE FLOOR, ONE PRINTED COLUMN AND NO OTHER. "第三层次" is under half of
            # "第三层次公允价值计量" and "Level 3" a sixth of 1966's "Significant unobservable inputs
            # (Level 3)", yet each names exactly one column of its table. Taken only when it is
            # contained in ONE distinct heading the row's table prints; in two it is ambiguous.
            only = _only_heading_containing(row, cells, want)
            if only is not None:
                scored = [(1.0 if normalise(getattr(ev, "column_heading", None)) == only else 0.0,
                           ev) for ev in cells]
                blanks = [(1.0 if normalise(h) == only else 0.0, h) for h in blank_columns(row)]
                best = max((s for s, _ev in scored), default=0.0)
                best_blank = max((s for s, _h in blanks), default=0.0)
        if best_blank >= MATCH_FLOOR and best_blank > best:
            heading = next(h for s, h in blanks if s == best_blank)
            out.update(blank=True, why=(
                f"the column {heading!r} is printed on that table but holds no figure on this "
                f"row — a column with no figure is not a figure, and not a zero"))
            return out
        if best < MATCH_FLOOR:
            out["why"] = (f"no column of that row is headed {want!r}"
                          + (f" — its columns are {listing}" if listing else
                             " — the row's columns carry no printed heading"))
            return out
        matched = [ev for s, ev in scored if s == best]
        out["matched_by"] = "heading"
    bases = list(dict.fromkeys(_basis(ev) or "consolidated" for ev in matched))
    basis = "consolidated" if "consolidated" in bases else bases[0]
    matched = [ev for ev in matched if (_basis(ev) or "consolidated") == basis]
    by_period: dict[str, list] = {}
    picked: list[dict] = []
    for ev in matched:
        period, source = period_of(ev, row)
        label = str(getattr(ev, "period_label", "") or "")
        base = _key_base(label)
        if period is None and base and (
                keys_are_periods or not getattr(ev, "column_heading", None)):
            period, source = base, "key"
        if period is None:
            period, source = reporting_period, "assumed"
        by_period.setdefault(period, []).append(ev)
        picked.append({"key": label, "heading": getattr(ev, "column_heading", None) or "",
                       "period": period, "period_source": source, "value": str(ev.value),
                       "ev": ev})
    for period, evs in by_period.items():
        if len({str(ev.value) for ev in evs}) > 1:
            heads = "; ".join(dict.fromkeys(str(getattr(ev, "column_heading", None)
                                                or getattr(ev, "period_label", "")) for ev in evs))
            out["why"] = (f"{want!r} names {len(evs)} columns of that row with different figures "
                          f"in one period ({heads}) — give the column's whole heading as given")
            return out
    seen: set[str] = set()
    for cell in picked:
        if cell["period"] in seen:
            continue                     # the same figure under one period, named twice
        seen.add(cell["period"])
        out["cells"].append(cell)
        out["figures"][cell["period"]] = cell["value"]
    out["basis"] = basis
    out["assumed"] = any(c["period_source"] == "assumed" for c in out["cells"])
    return out
