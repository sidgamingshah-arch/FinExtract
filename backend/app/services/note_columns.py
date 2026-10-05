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
