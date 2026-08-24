"""How an extracted row's values are read — which column is which period, and what figure a
concept actually shows.

Every consumer of an extracted row (the statement view, the Excel export, the note tables, the
accounting checks) has to answer the same two questions, and answering them differently is how a
prior-year figure ends up printed under the current year, or how a validated total stops being
the total on screen. So the answers live here, once.

Extraction labels a value's column as it reads it: ``current`` for the leftmost figure column,
``prior`` for the next, ``colN`` beyond that, and — for a matrix statement such as changes in
equity — the column's own printed heading ("Retained profits"). A row that carries no figure at
all in the current year therefore has values labelled ``prior`` and nothing else, which is
exactly the case the naive "first value is the current one" fallback gets wrong.
"""
from __future__ import annotations

import re

CURRENT = "current"
PRIOR = "prior"

# A column header that names a PERIOD rather than a component. Excel carries the sheet's own
# header text, so this is what separates "31 December 2023" (a period, which must be labelled
# positionally so current/prior resolution works) from "Retained profits" (an equity component,
# whose identity IS its name).
_POSITIONAL = re.compile(r"^(current|prior|col\d+)$", re.IGNORECASE)
_PERIOD_LIKE = re.compile(r"(19|20)\d{2}|\bfy\b|\bq[1-4]\b|年", re.IGNORECASE)


def looks_like_period(label) -> bool:
    """Whether a column header names a period (a year, a date, FY/Q, or a positional key)."""
    text = str(label or "").strip()
    return bool(text) and bool(_POSITIONAL.match(text) or _PERIOD_LIKE.search(text))


def names_a_component(label) -> bool:
    """Whether a column header names something that is NOT a period — an equity component."""
    text = str(label or "").strip()
    return bool(text) and not looks_like_period(text)


def _named(v: dict) -> str:
    return str(v.get("period_label") or "").strip().lower()


def basis_values(row: dict, basis: str) -> list[dict]:
    """A row's values for one basis; a value with no basis is treated as consolidated."""
    return [v for v in (row.get("values") or [])
            if (v.get("basis") or "consolidated") == basis]


def bases_present(rows: list[dict]) -> list[str]:
    """Every basis these rows actually carry a value under, sorted. A value with no basis counts
    as consolidated, the same reading :func:`basis_values` uses."""
    return sorted({(v.get("basis") or "consolidated")
                   for r in rows for v in (r.get("values") or [])})


def effective_basis(rows: list[dict], requested: str) -> tuple[str, str]:
    """``(basis to read, why)`` — the basis a view should actually show.

    THE DEFECT THIS CLOSES. A statement whose rows all carry ONE basis returned nothing when the
    other was asked for, and the Workspace opens on Consolidated. So a filing the extractor labelled
    company-only — one ``company_only_markers`` hit is enough to label a whole page — rendered an
    empty default tab with its figures one tab away, and the analyst has no reason to go looking.

    A DOCUMENT THAT LABELLED ONE BASIS DREW NO DISTINCTION. It printed one set of figures and the
    extractor described the only column there was; that is not a division of the statement into two.
    Asked for the consolidated view, the one set of figures is the answer.

    ONLY TOWARDS CONSOLIDATED, and the asymmetry is the point. Consolidated is the default view and
    the reading a filing gets when nothing says otherwise (``row_reconstruct._basis_for`` returns it
    when no basis band is found at all). Standalone is never a default: clicking it asks for the
    COMPANY's figures specifically, and answering with the Group's would be a wrong number. That
    request keeps its existing named refusal — ``basis_not_extracted``, which the grid states rather
    than showing a blank — and this function must not take it away.

    A filing that prints Group and Company side by side is untouched either way: both bases satisfy
    their own request and return on the first test.
    """
    if any(basis_values(r, requested) for r in rows):
        return requested, "requested"
    if requested != "consolidated":
        # An explicit request for a specific entity's figures. Refused, and told why, upstream.
        return requested, "requested"
    present = bases_present(rows)
    if len(present) == 1:
        # Past the first test the consolidated view holds nothing, so at most one other basis can
        # exist today and the count can only be 0 or 1. Requiring exactly one is what keeps the
        # substitution unambiguous if the vocabulary ever grows a third: two bases nobody asked for
        # have no single answer, and an arbitrary pick would put unattributable figures on the face.
        return present[0], "only_basis_in_document"
    return requested, "requested"


def split_current_prior(vals: list[dict]) -> tuple[dict | None, dict | None]:
    """``(current, prior)`` for one row's values (already filtered to a single basis).

    A value that NAMES its period wins outright, and a period nothing was printed for stays
    ``None``. The positional fallback — first value is current, second is prior — applies only
    when no value in the list names a period at all (columns read as ``col0``/``col1``, or a
    matrix row's component columns), because there the order on the page is the only signal.

    The distinction matters: several filings print a line for one year only (a deposit pledged
    in the prior year and released since). Falling back positionally there takes last year's
    figure and reports it as this year's — inventing a current-year number the document never
    contained, in the one place a reader cannot check it.
    """
    by: dict[str, dict] = {}
    for v in vals:
        lbl = _named(v)
        if lbl in (CURRENT, PRIOR) and lbl not in by:
            by[lbl] = v
    if by:
        return by.get(CURRENT), by.get(PRIOR)
    return (vals[0] if vals else None), (vals[1] if len(vals) > 1 else None)


def period_displays(vals: list[dict]) -> dict[str, str | None]:
    """The printed header text (e.g. "31 December 2023") for whichever periods this row names."""
    out: dict[str, str | None] = {}
    for v in vals:
        lbl = _named(v)
        if lbl in (CURRENT, PRIOR) and lbl not in out:
            out[lbl] = v.get("period_display") or v.get("period_label")
    return out


def slot_for(row: dict, basis: str, period: str) -> dict | None:
    """The one value dict for a (basis, period) — the slot an edit reads and writes.

    ``current``/``prior`` go through :func:`split_current_prior`, so an edit lands on the same
    figure the grid shows even when the extractor labelled the columns positionally.
    """
    vals = basis_values(row, basis)
    if period in (CURRENT, PRIOR):
        cur, prior = split_current_prior(vals)
        return cur if period == CURRENT else prior
    return next((v for v in vals if v.get("period_label") == period), None)


def edited_for(row: dict, basis: str, period: str | None = None) -> bool:
    """Whether this row carries a MANUAL value for the given basis (and period, when named).

    ``edited_slots`` records exactly which (basis, period) figures an analyst typed into, and the
    granularity matters in both directions: an edit to the consolidated column must not claim the
    standalone one, and an edit to THIS year must not claim last year — otherwise correcting one
    figure silently changes the other column of the same row. A run edited before slots were
    recorded only has the row-level flag, so it is honoured for every slot; that stays true to
    what those edits meant when they were made.
    """
    slots = row.get("edited_slots")
    if slots is None:
        return bool(row.get("edited"))
    for s in slots:
        b, _, p = str(s).partition("/")
        if b == basis and (period is None or p == period):
            return True
    return False


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


# A trailing run of note references. A caption carries the notes it cites — "Interest on lease
# liabilities 16(b),(c)" — and the SAME line cited from a different note prints a different run:
# "Interest on lease liabilities 8, 16(b)". The references identify the note, not the concept, so
# they are dropped before two captions are compared. Each token is a bare number or a single letter,
# which is what a reference reduces to once `mapping.normalize_label` has stripped its punctuation.
_NOTE_REF_TAIL = re.compile(r"(?:\s+(?:\d+|[a-z]))+$")
# A token that names something, as opposed to one that could be a reference: two or more letters.
_HAS_WORD = re.compile(r"[^\W\d_]{2,}")


def caption_key(row: dict) -> str:
    """The concept a printed caption NAMES, for deciding whether two rows are the same fact.

    Case, punctuation and script are folded by ``mapping.normalize_label`` (a Traditional caption
    compares equal to its Simplified twin), and the cited-note run is dropped on top of that. A
    caption that is nothing BUT a reference keeps its normalised form rather than collapsing to the
    empty string, which would compare equal to every other empty one.
    """
    from app.services.mapping import normalize_label

    full = normalize_label(str(row.get("source_label") or ""))
    trimmed = _NOTE_REF_TAIL.sub("", full).strip()
    # Only a caption that still NAMES something after the trim has had references stripped. "16(b)"
    # trims to "16", which is not a concept — and two such captions would then compare equal on a
    # digit, so a caption made of nothing but references keeps its whole normalised form.
    if not _HAS_WORD.search(trimmed):
        return full
    return trimmed


def _printed_at(v: dict | None) -> object:
    """Where a value was printed, as far as "is this the same line again" needs to know."""
    prov = (v or {}).get("provenance") or {}
    return prov.get("page_index")


def summable(group: list[dict], basis: str, period: str) -> list[tuple[dict, float]]:
    """The rows whose figures ADD to one concept's total, as ``(row, value)``.

    ENFORCES THE RULEBOOK'S ``duplicate_fact_rule``: "The same economic fact on the face and in a
    note is one fact with multiple evidence references, not two additive values. This also applies
    to two face captions that report the same amount." That policy was declared and then only ever
    appended to the LLM's system prompt, so a run with no LLM reachable summed the duplicates —
    the income statement's "LOSS FOR THE YEAR" and the comprehensive-income statement's restatement
    of it are one printed fact, and adding them doubled a filing's bottom line.

    TWO ROWS ARE ONE FACT when they name the same concept (``caption_key``), report the same amount,
    and were printed in different places. All three are required, and each excludes a real case the
    others would swallow:

    * the amount, because "Bank borrowings" current and non-current are two genuine lines;
    * the caption, because two unrelated lines can carry equal amounts — small ones especially;
    * a different location, because a statement that prints two rows with the same caption and the
      same amount printed them twice on purpose, and those DO add.

    Everything else keeps adding: three depreciation lines into "Depreciation and amortisation",
    four dividend lines into "Dividends received", the odds and ends a section's "Others" absorbs.
    """
    out: list[tuple[dict, float]] = []
    seen: dict[tuple[str, float], object] = {}
    for r in group:
        slot = slot_for(r, basis, period)
        n = _num((slot or {}).get("value"))
        if n is None:
            continue
        ident = (caption_key(r), n)
        where = _printed_at(slot)
        if ident in seen and where is not None and seen[ident] != where:
            continue
        seen.setdefault(ident, where)
        out.append((r, n))
    return out


def concept_value(group: list[dict], basis: str, period: str) -> float | None:
    """The figure the app shows for one concept in one (basis, period).

    Several printed lines legitimately map to one concept (three depreciation lines into
    "Depreciation and amortisation", a handful of odds and ends into a section's "Others"), so
    the default is their sum — of the rows that ADD, which is not always all of them: see
    :func:`summable` for the fact a filing prints twice. A MANUAL edit replaces that outright: it
    is the analyst's answer for the line, not one more contributor to add to the printed ones —
    entering 200 over a combined 150 has to show 200, not 350.

    The statement view, the export and the accounting checks all read the figure through here.
    Reading it differently in any of them means a number gets validated that nobody is shown.
    """
    edited = next((r for r in group if edited_for(r, basis, period)), None)
    if edited is not None:
        return _num((slot_for(edited, basis, period) or {}).get("value"))
    counted = summable(group, basis, period)
    if not counted:
        return None
    return sum(n for _, n in counted)
