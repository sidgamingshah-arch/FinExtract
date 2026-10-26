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
# …and the row flag of a note row printed nil in every column (`row_reconstruct.NIL_ROW_FLAG`).
NIL_ROW_FLAG = "printed_nil"


def printed_nil(row) -> bool:
    """Whether the row was printed with a nil mark in every column and so carries no figure."""
    return NIL_ROW_FLAG in (getattr(getattr(row, "confidence", None), "flags", None) or ())

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
       its years as blocks of rows (1966's and 佳明's fair-value hierarchies);
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
# `prior_col3`, `prior_restated`, `prior_original` (`row_reconstruct._column_periods`). Not a
# measure suffix.
_KEYED_PERIOD = re.compile(r"^(current|prior)(?:_col\d+|_restated|_original)?$")


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


# ══ A TABLE WHOSE COLUMNS ARE NOT PERIODS, AS THE MODEL IS SHOWN IT ══════════════════════════════
#
# A movement schedule (PP&E, intangibles: asset classes across, opening / additions / disposals /
# closing down, a gross block and an accumulated-depreciation block, often once per year) and an
# ageing schedule (Not due | Less than 6 months | … across) print several figures of ONE period on
# a row. The reader files them under keys that are not years — a matrix column's class name with a
# `column_index`, or a positional `current` / `current_col1` the period header happened to stand
# over — and the request used to send them as if they were: a matrix row went with no figures at
# all (every value carries a `column_index`), an ageing row as {current, current_col1 …}, and a
# headed class table as {current: "Computer software", prior: "Technical know-how"} in `columns`.
#
# `table_layout` is the one reading of such a table, and both sides of a citation use it: the
# request (`note_context.identified_notes`) keys each figure by its printed column, states the
# row's group and the period of its block; `note_sourced.resolve_sources` reads a citation of that
# group / period / column back through the same layout, so what the payload calls a column is what
# the resolver looks up. A period table — every figure's key a period — is left exactly as it was.

_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_DATE_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+((?:19|20)\d{2})\b")
_DATE_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})\b")
_DATE_HAN = re.compile(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_DATE_NUM = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)\d{2})\b")
# A balance row: "Balance as at …", "As at …", "At 31 December …", "期末余额". Its prefix, when it
# has one, is the block heading the reader joined into the caption ("Accumulated depreciation
# Balance as at 1 April 2023").
_BALANCE = re.compile(r"(?:\bbalance\b|\bas\s+at\b|\bas\s+on\b|^at\b|\bat\s+\d|\bopening\b|"
                      r"\bclosing\b|期初|期末|年初|年末)", re.IGNORECASE)
# The headings a movement schedule's blocks are printed under — only for the FIRST block, whose
# heading the reader keeps in the prose and not on a row ("… computers Gross carrying amount
# Balance as at 1 April 2023 …").
_BLOCK_HEADING = re.compile(
    r"(gross carrying (?:amount|value)|gross block|deemed cost|cost or valuation|at cost|cost|"
    r"accumulated (?:depreciation|amorti[sz]ation)(?: and impairment(?: losses)?)?|"
    r"accumulated impairment(?: losses)?|net (?:carrying amount|block|book value)|"
    r"carrying amount|账面原值|原值|累计折旧|累计摊销|减值准备|账面价值)\s*$", re.IGNORECASE)
# The first line of a column header the reader built into a row: "Particulars Not due Less than"
# with the "6" of "6 months" as its figure.
_HEADER_LEAD = re.compile(r"\b(?:particulars|description)\b|^项目|项目$", re.IGNORECASE)
# THE PERIOD A MAINLAND SCHEDULE NAMES IN WORDS, not by date. A CAS movement schedule prints
# "1.期初余额 / 2.本期增加金额 / 3.本期减少金额 / 4.期末余额" and its columns print "本期发生额 · 收入 /
# 上期发生额 · 收入": no date anywhere, and every figure of it was sent `period: unstated` — 澜起's
# right-of-use depreciation 14,777,647.32 under "(1)计提" among them. The words are the period:
# the close of this year and this year's movement are `current`; the opening balance (the close
# of last year) and last year's movement are `prior`.
_RELATIVE_CURRENT = re.compile(r"本期|本年|期末|年末|本报告期|本報告期")
_RELATIVE_PRIOR = re.compile(r"上期|上年|期初|年初|上报告期|上報告期")
# A mainland schedule's block heading, printed in the text before the block's first row and on
# no row: "一、账面原值" over its 1.–4. rows, "二、累计折旧" over the next four.
_CN_SECTION = re.compile(r"([一二三四五六七八九十]{1,3}[、.．]\s*[^\s\d,.，、()（）]{2,16})")
# An enumerator only: "1." of "1.期初余额" is a row number, not the heading the row sits under.
_ENUMERATOR_ONLY = re.compile(r"^[(（]?\s*(?:\d{1,2}|[一二三四五六七八九十]{1,3}|[a-z]|[ivx]{1,4})"
                              r"\s*[)）.、．]?$", re.IGNORECASE)


def relative_slot(text: str | None) -> tuple[str, str]:
    """``(period, word)`` for a caption or heading that names its period in words (`本期`, `期初`
    …), else ``("", "")``. Both kinds in one text ("本期期初") name nothing certain."""
    t = to_simplified(unicodedata.normalize("NFKC", str(text or "")))
    cur, pri = _RELATIVE_CURRENT.search(t), _RELATIVE_PRIOR.search(t)
    if cur and not pri:
        return "current", cur.group(0)
    if pri and not cur:
        return "prior", pri.group(0)
    return "", ""
# A COLUMN HEADING THAT IS A BALANCE: 期初余额 / 期末余额 / 年初余额 / 年末余额 (and their 账面余额 forms).
_LEADING_YEAR = re.compile(r"^\s*((?:19|20)\d{2})\b(?!\s*[-/年])")
_BALANCE_HEADING = re.compile(r"(?:期初|期末|年初|年末)(?:账面)?余额")
_HEADER_FIGURE = re.compile(r"^(?:\d{1,2}|(?:19|20)\d{2})$")
_YEAR_FIGURE = re.compile(r"^(?:19|20)\d{2}$")
_POSITIONAL = re.compile(r"^(current|prior)(?:_col\d+)?$")
UNSTATED = "unstated"
# A BALANCE DATED BEFORE BOTH YEARS — the opening balance of the comparative year ("Balance as at
# 1 April 2023" in a schedule closing 31 March 2024 and 31 March 2025). The table states its date,
# so it is not `unstated`; it is neither year's figure, so it is in neither slot.
EARLIER = "earlier"
# A ROW WHOSE COLUMNS EACH PRINT THEIR OWN YEAR (a period × measure header): no one period is the
# row's, and each figure is filed under the year over its column.
PER_COLUMN = "per column"
# THE DATED TITLE OF A TABLE PRINTED ONCE PER YEAR: "Trade receivables ageing schedule as at
# 31 March 2024" over its own "Particulars | Not due | …" header. Its date is the period of every row
# under it. Latin ageing schedules only — the shape f1 notes 10 and 22 print.
_DATED_TITLE = re.compile(
    r"\b(?:ageing|aging)\s+(?:schedule|analysis)\b[^.;:]{0,60}?\bas\s+(?:at|on)\s+"
    r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+(?:19|20)\d{2}\b", re.IGNORECASE)


# A TITLE, not a sentence: capitalised "As at <date>" with no sentence running on after it, or a
# heading "At <date>:" ("At 31 March 2023 and 1 April 2023:" — the first date is the block's).
_TITLE_DATE = r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]{2,8}\.?,?\s+(?:19|20)\d{2}"
_AS_AT_TITLE = re.compile(
    rf"\b(?:As\s+(?:at|of)\s+{_TITLE_DATE}(?=\s*(?:$|[A-Z0-9(\u3400-\u9fff]))"
    rf"|At\s+{_TITLE_DATE}(?:\s+and\s+{_TITLE_DATE})?\s*[:：])")


def _date_of(text: str) -> tuple[int, int, int] | None:
    """``(year, month, day)`` of the one date a caption prints, else None."""
    text = unicodedata.normalize("NFKC", text or "")
    m = _DATE_HAN.search(text)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _DATE_DMY.search(text)
    if m and m.group(2)[:3].casefold() in _MONTHS:
        return int(m.group(3)), _MONTHS[m.group(2)[:3].casefold()], int(m.group(1))
    m = _DATE_MDY.search(text)
    if m and m.group(1)[:3].casefold() in _MONTHS:
        return int(m.group(3)), _MONTHS[m.group(1)[:3].casefold()], int(m.group(2))
    m = _DATE_NUM.search(text)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    return None


def _date_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    for rx in (_DATE_HAN, _DATE_DMY, _DATE_MDY, _DATE_NUM):
        m = rx.search(text)
        if m:
            return m.group(0).strip()
    return ""


def _figures(row) -> list[tuple[str, object]]:
    return [(str(k), ev) for k, ev in (getattr(row, "values", None) or {}).items()
            if getattr(ev, "value", None) is not None]


def _own_non_period(row) -> bool:
    """Whether this row's own figures say its columns are not periods: a matrix column; a heading
    over a `current`/`prior` key that does not date it (the `whole_row` fence's test); or three or
    more figures in one period slot (an ageing row's buckets under `current`, `current_col1` …)."""
    figs = _figures(row)
    if any(getattr(ev, "column_index", None) is not None for _k, ev in figs):
        return True
    if any(str(getattr(ev, "period_label", "") or "") in FILED_PERIODS
           and getattr(ev, "column_heading", None) and not _key_is_period(ev, row)
           for _k, ev in figs):
        return True
    bases: dict[str, int] = {}
    for _k, ev in figs:
        m = _POSITIONAL.match(str(getattr(ev, "period_label", "") or ""))
        if m:
            bases[m.group(1)] = bases.get(m.group(1), 0) + 1
    return any(n >= 3 for n in bases.values())


def _header_fragment(row, continuing: bool) -> bool:
    """A line of a column header the reader built as a row — "Particulars Not due Less than"
    carrying the "6" of "6 months", then "months" carrying "1" and "3". Its figures are bare
    one- or two-digit numbers or years; the first line names the label column."""
    caption = str(getattr(row, "raw_label", "") or "").strip()
    figs = _figures(row)
    if not all(_HEADER_FIGURE.match(str(ev.value).strip()) for _k, ev in figs):
        return False
    if _HEADER_LEAD.search(caption):
        return bool(figs)
    return continuing and len(caption.split()) <= 4


def _x_of(ev) -> float | None:
    prov = getattr(ev, "provenance", None)
    box = getattr(prov, "value_bbox", None) or getattr(prov, "bbox", None)
    x0 = getattr(box, "x0", None)
    return float(x0) if x0 is not None else None


def _x1_of(ev) -> float | None:
    prov = getattr(ev, "provenance", None)
    box = getattr(prov, "value_bbox", None) or getattr(prov, "bbox", None)
    x1 = getattr(box, "x1", None)
    return float(x1) if x1 is not None else None


def _per_column_periods(row, keys: dict[str, str], newest):
    """``({key: period}, keys)`` for a row whose columns are a period × measure header — two or
    more headings each printing a different year, and the unheaded-by-year measure columns after
    each ("% of total consumption") — else None. Each measure column takes the year of the dated
    column before it, and its heading is given that date, so the two "% of total consumption"
    columns are two keys and not one (the second used to overwrite the first).

    A heading that names its period IN WORDS — "本期发生额 · 收入 | 本期发生额 · 成本 | 上期发生额 · 收入"
    (`relative_slot`) — is a dated column too: every heading names its own, so none is renamed."""
    figs = sorted(_figures(row), key=lambda kv: _x_of(kv[1]) or 0.0)
    headed = [(k, getattr(ev, "column_heading", None)) for k, ev in figs]
    if not all(h for _k, h in headed):
        return None
    dated = {k: _date_of(str(h)) for k, h in headed}
    if len({d for d in dated.values() if d}) < 2 or not dated.get(headed[0][0]):
        worded = {k: relative_slot(str(h))[0] for k, h in headed}
        if len(set(worded.values())) == 2 and all(worded.values()):
            return worded, dict(keys)
        # …AND A MOVEMENT TABLE'S TWO BALANCE COLUMNS, around the movements between them: 000883's
        # 股本 note prints 期初余额 | 发行新股 | 送股 | 公积金转股 | 其他 | 小计 | 期末余额 over one
        # row, 股份总数, and it went out "unstated" — nothing said 6,520,949,388 was the prior year's
        # closing share count. Only the columns whose heading names its period are dated; a
        # movement column names none and keeps the row's.
        named = {k: v for k, v in worded.items() if v}
        if set(named.values()) == {"current", "prior"} and all(
                _BALANCE_HEADING.search(str(h)) for k, h in headed if k in named):
            return named, dict(keys)
        # …AND A HEADING THAT OPENS ON ITS YEAR, with the measures printed after it under that year:
        # 0191's share capital prints "2025 Number of shares | Total amount | 2024 Number of shares
        # | Total amount", and the row went out "unstated", so the 2024 share count could not be
        # filed under the comparative. Two years exactly, the later the current.
        yearly = {k: int(m.group(1)) for k, h in headed
                  for m in [_LEADING_YEAR.match(str(h))] if m}
        if len(set(yearly.values())) == 2 and headed[0][0] in yearly:
            newest_year = max(yearly.values())
            periods, out_keys, last = {}, dict(keys), None
            for k, h in headed:
                if k in yearly:
                    last = yearly[k]
                else:
                    out_keys[k] = f"{keys.get(k, h)} ({last})"
                periods[k] = "current" if last == newest_year else "prior"
            return periods, out_keys
        return None
    periods: dict[str, str] = {}
    out_keys = dict(keys)
    last = None
    for k, h in headed:
        if dated[k]:
            last = (dated[k], _date_text(str(h)))
        else:
            out_keys[k] = f"{keys.get(k, h)} ({last[1]})"
        slot = _slot(last[0], newest)
        if not slot:
            return None
        periods[k] = slot
    return periods, out_keys


_DUPLICATE_SUFFIX = re.compile(r" \(\d\)$")


def _distinct_keys(row, keys: dict[str, str]) -> dict[str, str]:
    """``keys`` with a heading printed over two columns told apart by its place: 0160's PP&E
    matrix prints "Buildings" under right-of-use and under owned assets and "Total" three times,
    and the five figures were sent under two keys — the later overwriting the earlier. The second
    and third print as "Total (2)", "Total (3)", left to right."""
    count: dict[str, int] = {}
    for _k, h in keys.items():
        count[h] = count.get(h, 0) + 1
    if all(n == 1 for n in count.values()):
        return keys
    order = sorted(_figures(row), key=lambda kv: _x_of(kv[1]) or 0.0)
    seen: dict[str, int] = {}
    out = dict(keys)
    for k, _ev in order:
        h = keys.get(k)
        if h is None or count.get(h, 0) < 2:
            continue
        seen[h] = seen.get(h, 0) + 1
        if seen[h] > 1:
            out[k] = f"{h} ({seen[h]})"
    return out


def _earlier(date, newest) -> bool:
    """Whether ``date`` falls before the comparative year's close."""
    return bool(date and newest and date < (newest[0] - 1, newest[1], newest[2]))


def _joined_caption_at(cap: str, text: str, cursor: int) -> tuple[int, str]:
    """``(position, the part found)`` of a caption the reader JOINED from two printed lines, else
    ``(-1, cap)``: the longest run of its closing words the note's text prints after ``cursor``.

    A line the reader keeps as caption-only text runs into the caption of the row below it, and
    the note's text prints the two apart, with the first line's own cells between them — so the
    joined caption is found nowhere and its row takes no period from the table's dated title.
    Measured on Lai Sun note 33: the 2024 provision matrix prints "Expected credit loss rate 0% 1%
    0% 100% 12%" over "Gross carrying amount (HK$’000) … 470,403", the row went out as "Expected
    credit loss rate Gross carrying amount (HK$’000)" with no period, though "As at 31 July 2024"
    is printed over it, and the 2024 gross receivables could not be filed under 2024. At least
    three words, so a stray word cannot stand in for the caption."""
    words = cap.split()
    for start in range(1, len(words) - 2):
        tail = " ".join(words[start:])
        at = text.find(tail, cursor)
        if at >= 0:
            return at, tail
    return -1, cap


def _title_periods(rows, non_period, balance, text) -> dict[int, tuple[int, int, int] | None]:
    """``{row index: date}`` for the rows printed under a dated table title (`_DATED_TITLE`): the
    last such title before the row in the note's text, where every row between them is a
    non-period row with no balance caption — the same table."""
    titles = [(m.start(), _date_of(m.group(0)), _date_text(m.group(0)))
              for m in _DATED_TITLE.finditer(text)]
    pos: dict[int, int] = {}
    found: dict[int, int] = {}            # how much of the caption the text prints there
    cursor = 0
    for i, row in enumerate(rows):
        cap = unicodedata.normalize("NFKC", str(row.raw_label))
        at = text.find(cap, cursor)
        if at < 0:
            at, cap = _joined_caption_at(cap, text, cursor)
        if at >= 0:
            pos[i] = at
            found[i] = len(cap)
            cursor = at + len(cap)
    # "As at 31 December 2024" printed alone over a table's header, once per year — FEH's fair
    # value hierarchy prints the same captions under 2024 and then 2023. A row's own caption
    # ("As at 31 December 2023" opening a movement block) is a balance row, not a title.
    spans = [(pos[i], pos[i] + found[i]) for i in pos]
    titles += [(m.start(), _date_of(m.group(0)), _date_text(m.group(0)))
               for m in _AS_AT_TITLE.finditer(text)
               if not any(a <= m.start() < b for a, b in spans)]
    titles.sort(key=lambda t: t[0])
    if not titles:
        return {}
    out: dict[int, tuple] = {}
    for i in pos:
        if not non_period[i] or balance.get(i):
            continue
        before = [t for t in titles if t[0] < pos[i]]
        if not before:
            continue
        start, date, printed = before[-1]
        between = [j for j in pos if start < pos[j] < pos[i]]
        if date and all(non_period[j] and not balance.get(j) for j in between):
            out[i] = (date, printed)
    return out


def _cn_sections(rows, text: str) -> dict[int, str]:
    """``{row index: heading}`` for the rows a mainland block heading (`_CN_SECTION`) is printed
    immediately above, in the note's text: the heading found between the previous row's caption
    and this one's. Before this the CAS movement schedule's cost and accumulated-depreciation
    blocks were both grouped "1." — the enumerator of "1.期初余额" — and could not be told apart."""
    out: dict[int, str] = {}
    if not text:
        return out
    cursor = 0
    for i, row in enumerate(rows):
        cap = unicodedata.normalize("NFKC", str(row.raw_label))
        at = text.find(cap, cursor)
        if at < 0:
            continue
        found = _CN_SECTION.findall(text[cursor:at])
        if found:
            out[i] = _SPACES.sub("", found[-1])
        cursor = at + len(cap)
    return out


def note_newest(notes) -> dict[str, tuple[int, int, int]]:
    """The latest balance date the non-period rows of each note number print — its current
    period, decided per note across every fragment (a note printing one year per page would
    otherwise call each page's own close "current")."""
    out: dict[str, tuple[int, int, int]] = {}
    for table in notes or ():
        number = str(getattr(table, "note_number", "") or "")
        for row in getattr(table, "items", None) or ():
            if not _own_non_period(row):
                continue
            caption = str(getattr(row, "raw_label", "") or "")
            date = _date_of(caption) if _BALANCE.search(caption) else None
            if date and (number not in out or date > out[number]):
                out[number] = date
    return out


def _slot(date, newest) -> str:
    if not date or not newest:
        return ""
    if date == newest:
        return "current"
    if date == (newest[0] - 1, newest[1], newest[2]):
        return "prior"
    return ""


def table_layout(table, newest: tuple[int, int, int] | None = None) -> dict[int, dict]:
    """``{id(row): layout}`` for the rows of ONE note table whose columns are not periods; a row
    absent from the result is a period row and is sent and resolved as it always was.

    Each layout is ``{"fragment"}`` for a header line the request does not send as a row, else
    ``{"keys": {value key: printed column}, "group", "period", "period_text", "dated", "header"}``:

    * ``keys`` — the printed heading of each figure's column: the reader's heading, else a matrix
      column's name, else ``column N`` in printed order when the reader read no heading;
    * ``group`` — the heading the row sits under (the reader's group, else the block heading:
      "Gross carrying amount", "Accumulated depreciation", "(B) Capital work-in-progress");
    * ``period`` — ``current`` / ``prior`` from the table's own dated balance rows: a balance row
      is the period of its date, a movement row that of the balance closing its block; ``""`` when
      the table does not say (``period_text`` then reads `UNSTATED`), never guessed;
    * ``header`` — the column header as printed, on the first row under a header the reader split
      into rows ("Particulars Not due Less than 6 6 months - 1-2 years …").
    """
    rows = [r for r in (getattr(table, "items", None) or ()) if getattr(r, "raw_label", "")]
    if not rows:
        return {}
    # 1. Header fragments, in runs; each run opens a segment.
    fragment = [False] * len(rows)
    for i, row in enumerate(rows):
        fragment[i] = (not printed_nil(row)
                       and _header_fragment(row, i > 0 and fragment[i - 1]))
    seg = [0] * len(rows)
    s = 0
    for i in range(len(rows)):
        if fragment[i] and (i == 0 or not fragment[i - 1]):
            s += 1
        seg[i] = s
    # 2. Which rows are non-period: their own figures say so, or their segment is a bucketed one
    #    and their keys are among its bucket keys.
    own = [(not fragment[i]) and _own_non_period(r) for i, r in enumerate(rows)]
    seg_keys: dict[int, set[str]] = {}
    for i, row in enumerate(rows):
        if own[i]:
            seg_keys.setdefault(seg[i], set()).update(k for k, _ev in _figures(row))
    # Under a header the reader split into rows (segment 1 on), every figure row is that table's.
    # A row printed nil in every column is a row of the table it is printed in: it has no figure
    # to key, and its block and period are the table's — "(iii) Disputed dues - MSME" under an
    # ageing schedule's dated title.
    non_period = [own[i] or (not fragment[i] and (bool(_figures(r)) or printed_nil(r))
                             and seg[i] in seg_keys
                             and (seg[i] > 0
                                  or {k for k, _ev in _figures(r)} <= seg_keys[seg[i]]))
                  for i, r in enumerate(rows)]
    # A header run is dropped only where a non-period row follows it; a period table's
    # "Particulars 2025 2024" line is not this function's to remove.
    for i in range(len(rows)):
        if fragment[i]:
            j = i
            while j < len(rows) and fragment[j]:
                j += 1
            if j >= len(rows) or not non_period[j]:
                fragment[i] = False
    if not any(non_period) and not any(fragment):
        return {}
    text = unicodedata.normalize("NFKC", str(getattr(table, "source_text", "") or ""))
    out: dict[int, dict] = {}
    # 3. Header text per run, as printed: the source text between the run's first caption and the
    #    next row's caption, else the fragments joined.
    header_for: dict[int, str] = {}
    header_years: dict[int, bool] = {}
    i = 0
    while i < len(rows):
        if not fragment[i]:
            i += 1
            continue
        j = i
        while j < len(rows) and fragment[j]:
            j += 1
        first = unicodedata.normalize("NFKC", str(rows[i].raw_label))
        start = text.find(first)
        end = (text.find(unicodedata.normalize("NFKC", str(rows[j].raw_label)),
                         start + len(first)) if (start >= 0 and j < len(rows)) else -1)
        if start >= 0 and end > start:
            joined = _SPACES.sub(" ", text[start:end]).strip()
        else:
            joined = " ".join(" ".join([str(r.raw_label)] + [str(ev.value) for _k, ev in
                                                               sorted(_figures(r), key=lambda
                                                               kv: _x_of(kv[1]) or 0.0)])
                              for r in rows[i:j])
        if j < len(rows):
            header_for[j] = joined
        years = any(_YEAR_FIGURE.match(str(ev.value).strip()) for r in rows[i:j]
                    for _k, ev in _figures(r))
        for k in range(i, j):
            out[id(rows[k])] = {"fragment": True}
        header_years[seg[i]] = years
        i = j
    # 4. `column N` for unheaded, unnamed cells: figures are set flush right, so a column is the
    #    cluster of right edges its figures share — across the whole table, so `column 5` is the
    #    same printed column in every block. Without a box, the order the keys first appear.
    edges = sorted({x for i, row in enumerate(rows) if non_period[i]
                    for _k, ev in _figures(row)
                    if not getattr(ev, "column_heading", None)
                    and getattr(ev, "column_index", None) is None
                    for x in [_x1_of(ev)] if x is not None})
    bands: list[float] = []
    for x in edges:
        if not bands or x - bands[-1] > 0.012:
            bands.append(x)
        else:
            bands[-1] = x
    first_seen: dict[str, int] = {}

    def _column_n(k: str, ev) -> int:
        x = _x1_of(ev)
        if x is not None and bands:
            return 1 + min(range(len(bands)), key=lambda b: abs(bands[b] - x))
        return first_seen.setdefault(k, len(first_seen) + 1)
    # 5. Groups and block periods, in printed order.
    group = ""
    m = None
    first_np = next((i for i in range(len(rows)) if non_period[i]), None)
    if first_np is not None:
        at = text.find(unicodedata.normalize("NFKC", str(rows[first_np].raw_label)))
        if at > 0:
            m = _BLOCK_HEADING.search(text[max(0, at - 60):at].rstrip())
    if m and seg[first_np] == 0:
        group = m.group(1).strip()
    np_rows = [i for i in range(len(rows)) if non_period[i]]
    groups: dict[int, str] = {}
    sections = _cn_sections(rows, text)
    current_seg = 0
    for i, row in enumerate(rows):
        if seg[i] != current_seg:
            current_seg = seg[i]
            group = ""
            run = next((k for k in range(len(rows)) if seg[k] == seg[i] and fragment[k]), None)
            if run is not None:
                lead = _HEADER_LEAD.split(str(rows[run].raw_label))[0].strip()
                group = lead if len(lead) > 1 else ""
        if fragment[i]:
            continue
        caption = str(row.raw_label).strip()
        b = _BALANCE.search(caption)
        if i in sections:
            group = sections[i]
        if b and b.start() > 0:
            prefix = caption[:b.start()].strip()
            if 0 < len(prefix.split()) <= 6 and not _ENUMERATOR_ONLY.match(prefix):
                group = prefix
        groups[i] = str(getattr(row, "group_hint", "") or "").strip() or group
    dates = {i: (_date_of(rows[i].raw_label) if _BALANCE.search(str(rows[i].raw_label)) else None)
             for i in np_rows}
    balance = {i: bool(_BALANCE.search(str(rows[i].raw_label))) for i in np_rows}
    titled = _title_periods(rows, non_period, balance, text)
    worded: dict[str, tuple[str, str]] = {}   # group → the period its last movement row named
    for pos, i in enumerate(np_rows):
        row = rows[i]
        keys: dict[str, str] = {}
        mixed = any(getattr(ev, "column_index", None) is not None for _k, ev in _figures(row))
        for k, ev in _figures(row):
            heading = getattr(ev, "column_heading", None)
            if heading:
                keys[k] = str(heading)
            elif getattr(ev, "column_index", None) is not None:
                keys[k] = str(getattr(ev, "period_label", "") or k)
            elif (mixed and str(getattr(ev, "period_label", "") or "") in ("current", "prior")):
                # A row that is laid out only because SOME of its cells are matrix columns keeps
                # the period of the cells the reader filed as a period: its year's figure is
                # still the year's figure, beside the components keyed by their column.
                keys[k] = str(ev.period_label)
            else:
                keys[k] = f"column {_column_n(k, ev)}"
        per_column = _per_column_periods(row, keys, newest)
        if per_column:
            keys = per_column[1]
        # After the period × measure renaming, which tells two "% of total consumption" columns
        # apart by their year; what is still printed twice is told apart by its place.
        keys = _distinct_keys(row, keys)
        period, period_text = "", UNSTATED
        earlier = False
        if per_column:
            # EACH COLUMN PRINTS ITS OWN YEAR — "Year ended 31 March 2025 (₹ in lakhs) | % of total
            # consumption | Year ended 31 March 2024 …": the row is no one period's.
            period_text = PER_COLUMN
        elif not header_years.get(seg[i]):
            if dates.get(i):
                period, period_text = _slot(dates[i], newest), "as at " + _date_text(row.raw_label)
                earlier = not period and _earlier(dates[i], newest)
            elif i in titled:
                # A ROW UNDER A DATED TITLE takes the title's date, printed as the title prints it.
                period = _slot(titled[i][0], newest)
                period_text = "as at " + titled[i][1]
            elif not balance.get(i):
                # A movement row: the balance closing its block, in the same group and segment.
                close = next((k for k in np_rows[pos + 1:]
                              if seg[k] == seg[i] and groups.get(k) == groups.get(i)
                              and balance.get(k)), None)
                if close is not None and dates.get(close):
                    period = _slot(dates[close], newest)
                    period_text = "to " + _date_text(rows[close].raw_label)
                else:
                    # Undated: the movement's own words ("2.本期增加金额"), else its child's block
                    # closes at "4.期末余额" — this year's movement either way.
                    period, word = relative_slot(row.raw_label)
                    if not period and close is not None:
                        period, word = relative_slot(rows[close].raw_label)
                    if not period and worded.get(groups.get(i)):
                        # "(1)计提" under "2.本期增加金额" names no period; its parent movement does,
                        # and the block's close may be printed on the next page.
                        period, word = worded[groups.get(i)]
                    if period and not balance.get(i):
                        worded[groups.get(i)] = (period, word)
                    period_text = word or UNSTATED
            else:
                # An undated balance named in words: "4.期末余额" / "1.期初余额".
                period, word = relative_slot(row.raw_label)
                period_text = word or UNSTATED
        out[id(row)] = {"keys": keys, "group": groups.get(i, ""), "period": period,
                        "period_text": period_text, "header": header_for.get(i, ""),
                        "dated": any(dates.values()) or bool(titled),
                        **({"earlier": True} if earlier else {}),
                        **({"key_periods": per_column[0]} if per_column else {})}
    return out


def notes_layout(notes) -> dict[int, dict]:
    """`table_layout` for every table of a document, with each note's current period decided
    across all its fragments — the one call both the request and the resolver make."""
    newest = note_newest(notes)
    # A note with no dated balance row of its own (an ageing note) is slotted by the filing's year
    # end as its other notes print it: the latest balance date any note prints.
    filing = max(newest.values(), default=None)
    out: dict[int, dict] = {}
    previous = None
    for table in notes or ():
        laid = table_layout(table, newest.get(str(getattr(table, "note_number", "") or ""))
                            or filing)
        _continue_the_period(previous, table, out, laid)
        out.update(laid)
        previous = table
    return out


def _laid_rows(table, layout: dict[int, dict]) -> list:
    return [r for r in (getattr(table, "items", None) or ())
            if id(r) in layout and not layout[id(r)].get("fragment")]


def _continue_the_period(previous, table, before: dict[int, dict], laid: dict[int, dict]) -> None:
    """A TABLE CONTINUED ACROSS A PAGE BREAK IS THE SAME TABLE, and takes the period of the table
    it continues. f1 note 10 prints its 31 March 2024 ageing schedule at the foot of one page and
    its last three rows ("(v)", "(vi)", "Total") at the top of the next, under the same column
    heading and with no title — so the continuation's rows were `unstated`.

    Only where that can be SHOWN, never guessed: the fragment just before this one is the same
    note on the page before; this fragment prints no dated title of its own; both print the same
    columns; and the earlier fragment's rows are unfinished (its last row is no total) and dated by
    its title. The rows taken over are the ones the table left `unstated`, and they say where the
    period came from ("as at 31 March 2024 (carried from page 10)")."""
    if previous is None or not laid:
        return
    number = str(getattr(table, "note_number", "") or "")
    if not number or str(getattr(previous, "note_number", "") or "") != number:
        return
    pages, prev_pages = (list(getattr(table, "source_pages", None) or ()),
                         list(getattr(previous, "source_pages", None) or ()))
    if not pages or not prev_pages or pages[0] != prev_pages[-1] + 1:
        return
    if _DATED_TITLE.search(unicodedata.normalize(
            "NFKC", str(getattr(table, "source_text", "") or ""))):
        return
    prev_rows = _laid_rows(previous, before)
    rows = _laid_rows(table, laid)
    if not prev_rows or not rows:
        return
    last = prev_rows[-1]
    last_lay = before[id(last)]
    if not last_lay.get("period") or not str(last_lay.get("period_text", "")).startswith("as at"):
        return
    if str(getattr(getattr(last, "role", ""), "value", getattr(last, "role", ""))) in (
            "total", "subtotal"):
        return                                   # the earlier table ended: this one is another
    columns = [h for h in (getattr(rows[0], "printed_columns", None) or ()) if h]
    if not columns or columns != [h for h in (getattr(last, "printed_columns", None) or ()) if h]:
        return
    carried = (f"{last_lay['period_text']} (carried from page {prev_pages[-1] + 1})")
    for r in rows:
        lay = laid[id(r)]
        if (lay.get("period") or lay.get("key_periods") or lay.get("earlier")
                or lay.get("period_text") != UNSTATED):
            continue
        lay["period"], lay["period_text"] = last_lay["period"], carried
        lay["dated"] = True


def pick_laid_out(row, lay: dict, want: str | None, *, reporting_period: str = "current") -> dict:
    """`pick` for a row of a non-period table, by the printed column the request keyed it under
    (`table_layout` ``keys``) — matrix columns included, which `pick` cannot reach — and filed
    under the period of the row's block. Same result shape as `pick`."""
    want = str(want or "").strip()
    out: dict = {"cells": [], "figures": {}, "basis": None, "assumed": False, "why": "",
                 "blank": False, "matched_by": ""}
    if not want:
        out["why"] = "no column was given"
        return out
    keys = lay.get("keys") or {}
    cells = [(keys.get(k, k), k, ev) for k, ev in _figures(row)]
    listing = "; ".join(dict.fromkeys(h for h, _k, _ev in cells))
    # A heading printed over two columns is keyed "Total", "Total (2)" … (`_distinct_keys`); the
    # bare "Total" still names all of them, and is refused below as naming several figures.
    matched = [(h, k, ev) for h, k, ev in cells
               if h.casefold() == want.casefold()
               or _DUPLICATE_SUFFIX.sub("", h).casefold() == want.casefold()
               or str(getattr(ev, "period_label", "") or "").casefold() == want.casefold()]
    if matched:
        out["matched_by"] = "key"
    else:
        # THE SAME TESTS AS `pick`, so a citation reads the same whichever table shape it lands
        # on: a blank printed column is named as blank, and a short name that is contained in
        # exactly one printed heading ("第三层次", "Level 3") still names that column.
        scored = [(_score(h, want), (h, k, ev)) for h, k, ev in cells]
        best = max((s for s, _c in scored), default=0.0)
        blanks = [(_score(h, want), h) for h in blank_columns(row)]
        best_blank = max((s for s, _h in blanks), default=0.0)
        if max(best, best_blank) < MATCH_FLOOR:
            only = _only_heading_containing(row, _cells(row), want)
            if only is not None:
                scored = [(1.0 if normalise(c[0]) == only else 0.0, c) for _s, c in scored]
                blanks = [(1.0 if normalise(h) == only else 0.0, h) for h in blank_columns(row)]
                best = max((s for s, _c in scored), default=0.0)
                best_blank = max((s for s, _h in blanks), default=0.0)
        if best_blank >= MATCH_FLOOR and best_blank > best:
            heading = next(h for s, h in blanks if s == best_blank)
            out.update(blank=True, why=(
                f"the column {heading!r} is printed on that table but holds no figure on this "
                f"row — a column with no figure is not a figure, and not a zero"))
            return out
        if best < MATCH_FLOOR:
            out["why"] = (f"no column of that row is headed {want!r}"
                          + (f" — its columns are {listing}" if listing else ""))
            return out
        matched = [c for s, c in scored if s == best]
        out["matched_by"] = "heading"
    bases = list(dict.fromkeys(_basis(ev) or "consolidated" for _h, _k, ev in matched))
    basis = "consolidated" if "consolidated" in bases else bases[0]
    matched = [c for c in matched if (_basis(c[2]) or "consolidated") == basis]
    if len({str(ev.value) for _h, _k, ev in matched}) > 1:
        heads = "; ".join(dict.fromkeys(h for h, _k, _ev in matched))
        out["why"] = (f"{want!r} names {len(matched)} columns of that row with different figures "
                      f"({heads}) — give the column's whole heading as given")
        return out
    heading, key, ev = matched[0]
    if lay.get("earlier"):
        out["why"] = ("that row is a balance dated before both years (the opening balance of the "
                      "comparative year) — it is neither year's figure")
        return out
    period, source = (lay.get("period") or None), "row_block"
    if period is None and key in (lay.get("key_periods") or {}):
        period, source = lay["key_periods"][key], "column"
    if period is None and not lay.get("dated"):
        period, source = period_of(ev, row)
    if period is None:
        period, source = reporting_period, "assumed"
    out["cells"].append({"key": str(getattr(ev, "period_label", "") or key), "heading": heading,
                         "period": period, "period_source": source, "value": str(ev.value),
                         "ev": ev})
    out["figures"][period] = str(ev.value)
    out["basis"] = basis
    out["assumed"] = source == "assumed"
    return out
