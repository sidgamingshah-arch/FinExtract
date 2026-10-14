"""Naming the printed row that explains why a calculated line differs from the document.

A calculated line shows what its components come to (services/rollups.py), and the document's own
figure for it is kept beside the computation. When the two differ, the commonest cause is one row
printed on the same statement that the mapping placed on no line of the template: the filing's total
includes it and the computation does not. This module finds that row, when there is exactly one,
and proves the claim by recomputing the total with the row mapped onto a line the total is built
from.

READ TIME ONLY. Nothing here moves a row or writes ``run.result``: the explanation is recomputed on
every request, and the only write that acts on it is the analyst's own POST /review/remap. That is
the difference from ``services/gap_closing``, which routes rows at PIPELINE time through a model
provider — and which is inert on every shipped template, because its ``_section_of`` needs a
``<namespace>__others`` line and the HK template declares 0 of 480, Ind AS 0 of 211, ICON 0 of 199.
So the gaps are built here from the calculated lines directly, and ``find_gaps`` is not used.

A row is named only when all of this holds:

* the line differs from what was printed in BOTH periods, beyond the cross-check tolerance. One
  period matching is a coincidence; two is evidence;
* the row has a figure in both periods, and ONE orientation ``s`` (+1 or -1) makes ``s × figure``
  equal the difference in both periods, within max(tolerance, 0.5% of the difference);
* the row was printed on the pages of the line's own statement, and its caption is not a printed
  total, a result line, a breakdown ("of which"), a per-share figure, a note or year heading,
  narrative or a signature block;
* mapping the row onto a line of its printed section that the total is built from moves the total
  by exactly ``s × figure`` in both periods. The sign a section takes in a total depends on the
  template's own arithmetic — diff rollups, magnitude members, subtotals that fall back to their
  printed figure — so it is MEASURED with ``rollups.evaluate_rows`` rather than derived;
* no second row qualifies. Two rows that both explain a line leave it unexplained.

Several unexplained differences are deliberately left alone. A gap made of two or more rows, a
mapped row that sits on the wrong line, and a header row mapped into a residual are different
defects with different remedies, and naming an unplaced row for any of them would send the analyst
to the wrong fix.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from app.services.caption_shape import prose_reasons
from app.services.mapping import normalize_statement, section_token_of_scope
from app.services.periods import (
    CURRENT, PRIOR, basis_values, caption_key, concept_value, edited_for, split_current_prior)
from app.services.rollups import calculated_nodes, evaluate_rows
from app.stages.face_mapping_contract import UNCLASSIFIED_FACE_PREFIX, is_unclassified_face_key

#: A row is matched against the difference within this share of it, or the cross-check tolerance
#: when that is wider: rounding in the filing grows with the size of the figures.
REL_TOLERANCE = 0.005
#: More rows than this matching one difference arithmetically is a coincidence of figures, and no
#: single row can then be named. Bounds the simulations below as well.
MAX_HITS_PER_TARGET = 2
#: Each simulation is two evaluations of the whole template. The destinations are ranked so the
#: first one is almost always accepted.
MAX_SIMULATIONS_PER_HIT = 3

_PERIODS = (CURRENT, PRIOR)


@dataclass(frozen=True)
class Explanation:
    """One printed row that accounts for a calculated line's difference in both periods."""

    target: str
    basis: str
    #: The template statement whose sections hold ``target``.
    statement: str
    row_index: int
    #: Provenance of the row's current and prior figure.
    source: dict | None
    source2: dict | None
    #: The row's figures AS PRINTED, never oriented.
    v1: float
    v2: float
    orientation: int
    #: Printed minus computed for the target, per period: the convention of the assertion a card
    #: makes about it (magnitudes are compared, see documents._assertion_of).
    gap1: float
    gap2: float
    #: Computed after the mapping minus printed, per period: the convention of a card's delta.
    remainder1: float
    remainder2: float
    closes: bool
    #: The row's printed section token, when the destinations were drawn from it. None when the
    #: row's section is unknown or the run has no section vocabulary to read it against.
    section: str | None
    #: The lines the row may be mapped onto: those of its section that the target is built from,
    #: or every line the target is built from when ``section`` is None. In template order.
    pool: tuple[str, ...]
    #: The line the verification mapped the row onto. Internal: nothing preselects it.
    destination: str


# --- the caption filter -------------------------------------------------------------------------
# A row that the filing printed as a total, a result or a breakdown of the line above is not a
# missing line: mapping it would count its components a second time. The enumerator prefix covers
# "III. Total income", "(B) total ..." and "2) Total"; the lookahead keeps "(B) total outstanding
# dues of creditors ...", which is a line of its own and the true explanation on six measured runs.
_TOTAL_CAPTION = re.compile(
    r"^\W*(?:(?:[ivxlc]{1,6}|\d{1,2}|[a-z])\s*[.)]\s*)?(?:sub[\s-]*)?total\b(?!\s+outstanding)",
    re.I)
# A bracketed sum of enumerators: "(I + II)", "(III - IV)", "[10 + 11(iii)]", "(12 -13)".
_FORMULA_CAPTION = re.compile(
    r"[(\[]\s*(?:[ivxlc]{1,6}|\d{1,2}(?:\s*\([a-z0-9]{1,4}\))?|[a-z])\s*[+\-−–]\s*"
    r"(?:[ivxlc]{1,6}|\d{1,2}(?:\s*\([a-z0-9]{1,4}\))?|[a-z])"
    r"(?:\s*[+\-−–]\s*(?:[ivxlc]{1,6}|\d{1,2}(?:\s*\([a-z0-9]{1,4}\))?|[a-z]))*\s*[)\]]", re.I)
# Results and breakdowns. The CAS totals are matched anywhere in the caption, because a merged
# caption carries them mid-string.
_RESULT_CAPTION = re.compile(
    r"\b(?:profit|loss|surplus|deficit)\s*(?:/\s*\(?loss\)?\s*)?(?:before|for|after)\b"
    r"|^\W*(?:[ivxlc]{1,6}[.)]\s*)?(?:gross|operating)\s+(?:profit|loss|margin)\b"
    r"|^\W*net\s+(?:current\s+)?(?:assets|liabilities|worth)\b"
    r"|^\W*net\s+(?:increase|decrease)\b.*\bcash\b|^\W*net\s+cash\b"
    r"|cash\s+(?:generated|used)\s+(?:from|in)\s+operations"
    r"|^\W*cash and cash equivalents\s+(?:at|as)\b"
    r"|合计|合計|总计|總計|小计|小計|总额|總額|归属于|歸屬於"
    r"|营业利润|營業利潤|净利润|淨利潤|营业总收入|營業總收入|营业总成本|營業總成本|净增加额|淨增加額"
    r"|^\W*(?:其中|of which|including)\b", re.I)
# The statement's footer and signature block. caption_shape's prose test does not catch these:
# they are short, and they carry no finite verb.
_BOILERPLATE = re.compile(
    r"accompanying notes|integral part|for and on behalf|\bDIN\b|place\s*:|date\s*:|"
    r"chairman|managing director|chief financial officer|company secretary|as per our report|"
    r"membership\s+no|firm\s+registration|\bFRN\b|\bpartner\b|chartered accountants", re.I)
# Wider than residual._PER_SHARE, which misses "per equity share" and the bare "(1) Basic (₹)".
_PER_SHARE = re.compile(
    r"per (?:equity |ordinary )?share|earnings per|\bbasic\b|\bdiluted\b|每股|\(₹\)|\bcents\b|"
    r"\beps\b|number of (?:equity |ordinary )?shares|weighted average", re.I)
# A note-column heading read as a row: "No.", "Notes", "Note No.".
_NOTE_HEADER = re.compile(r"^\W*(?:no\.?|notes?|note\s*no\.?)\W*$", re.I)
_NOTE_WORD = re.compile(r"\bnotes?\b|附注|附註", re.I)

# "residual_ineligible:printed in another section(pl_expenses)" — the residual stage records the
# section a row was printed under when it declines to sweep it.
_ELSEWHERE_FLAG = re.compile(r"^residual_ineligible:printed in another section\((.+)\)$")


def not_a_line(row: dict, v1: float | None, v2: float | None) -> str | None:
    """Why this row cannot be the line a total is missing, or None when it can be.

    The first reason that applies: 'printed total', 'narrative', 'per-share', 'note reference' or
    'year header'. ``v1``/``v2`` are the row's two figures; a heading read as a row carries the note
    number or the year as its "figure", which is how those two are told apart from a real amount.
    """
    from app.stages.residual import _NOTE_REF_ONLY

    caption = (row.get("source_label") or "").strip()
    if (row.get("role") in ("subtotal", "total") or _TOTAL_CAPTION.search(caption)
            or _FORMULA_CAPTION.search(caption) or _RESULT_CAPTION.search(caption)):
        return "printed total"
    if prose_reasons(caption) or _BOILERPLATE.search(caption):
        return "narrative"
    if _PER_SHARE.search(caption):
        return "per-share"
    figures = [x for x in (v1, v2) if x is not None]
    if (_NOTE_HEADER.match(caption) or _NOTE_REF_ONLY.match(caption)
            or (_NOTE_WORD.search(caption) and figures
                and all(float(x).is_integer() and abs(x) < 200 for x in figures))):
        return "note reference"
    if figures and all(float(x).is_integer() and 1990 <= x <= 2100 for x in figures):
        return "year header"
    return None


# --- what a row says about itself ---------------------------------------------------------------
def row_section(row: dict) -> str | None:
    """The section token the row was printed under, or None when nothing records it.

    The served ``section`` first, then the section the residual stage recorded when it declined the
    row, then the section segment of an engine key
    (``engine_unclassified_face__<statement>__<section>__<slug>``) unless it is unresolved.
    """
    if row.get("section"):
        return section_token_of_scope(str(row["section"])) or str(row["section"])
    for flag in row.get("flags") or []:
        m = _ELSEWHERE_FLAG.match(str(flag))
        if m:
            return section_token_of_scope(m.group(1)) or None
    key = row.get("canonical_key") or ""
    if key.startswith(UNCLASSIFIED_FACE_PREFIX):
        parts = key[len(UNCLASSIFIED_FACE_PREFIX):].split("__")
        if len(parts) > 1 and parts[1] != "unresolved_section":
            return parts[1]
    return None


def row_statement(row: dict) -> str | None:
    """The statement a flag says the row was printed on, or None.

    The engine key's statement segment is deliberately not read: it is the statement of the PAGE
    the row was printed on, and one measured filing's standalone balance-sheet rows carry
    ``engine_unclassified_face__profit_and_loss__`` keys.
    """
    for flag in row.get("flags") or []:
        for prefix in ("match_statement:", "printed_on:"):
            if str(flag).startswith(prefix):
                return normalize_statement(str(flag).split(":", 1)[1]) or None
    return None


def place(prov: dict | None) -> tuple | None:
    """Where a figure was printed, as something to compare: a sheet, or a page index."""
    if not prov:
        return None
    if prov.get("source_kind") == "spreadsheet" and prov.get("sheet"):
        return ("sheet", prov["sheet"])
    page = prov.get("page_index")
    return None if page is None else ("page", int(page))


def subtree(nodes: dict[str, dict], key: str) -> list[str]:
    """Every key a calculated line is built from, at any depth, in discovery order.

    Through the rollup's children, its reported total, and — for a rollup that reuses its total's
    components — the reported total's children. ``key`` itself is not included.
    """
    seen: list[str] = []
    known: set[str] = set()
    stack = [key]
    while stack:
        roll = (nodes.get(stack.pop()) or {}).get("rollup") or {}
        kids = list(roll.get("children") or [])
        total = roll.get("reported_total_key")
        if total:
            kids.append(total)
            if roll.get("use_reported_total_components") and total in nodes:
                kids += [c for c in ((nodes[total].get("rollup") or {}).get("children") or [])
                         if c != key]
        for child in kids:
            if child != key and child not in known:
                known.add(child)
                seen.append(child)
                if child in nodes:
                    stack.append(child)
    return seen


def section_tokens(line_item_set) -> dict[str, str]:
    """canonical key → the section token its configured ``section_scope`` names.

    Items whose scope names no section (a statement-level total) are left out, and a run with no
    readable line-item set gives {} — the explanation then draws on every line the total is built
    from rather than on the row's section.
    """
    if line_item_set is None:
        return {}
    out: dict[str, str] = {}
    for item in getattr(line_item_set, "items", None) or []:
        scope = list(getattr(item, "section_scope", None) or [])
        token = section_token_of_scope(scope[0]) if scope else None
        if token:
            out[item.key] = token
    return out


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _computable(c) -> bool:
    return c is not None and not c.cycle and c.computable


def _statements_of(template_def: dict | None) -> dict[str, str]:
    """canonical key → the type of the template statement that declares it."""
    out: dict[str, str] = {}
    for stmt in (template_def or {}).get("statements", []):
        for sec in stmt.get("sections") or []:
            for node in [sec, *(sec.get("children") or [])]:
                if node.get("canonical_key"):
                    out.setdefault(node["canonical_key"], stmt.get("type") or "")
    return out


@dataclass(frozen=True)
class _Candidate:
    index: int
    v1: float
    v2: float
    cur: dict
    prior: dict
    place1: tuple | None
    place2: tuple | None
    statement: str | None
    section: str | None


def _candidates(rows: list[dict], positions: Sequence[int], basis: str,
                stats: dict) -> list[_Candidate]:
    """The rows that may be named: printed lines with a figure in both periods and nothing else
    accounting for them."""
    # The same fact printed twice — once on a mapped line, once beside it — is one fact
    # (periods.summable's rule), so its unmapped copy is not what a total is missing. Keyed on the
    # figures first: the caption is normalised only for a row whose two figures coincide.
    mapped: dict[tuple[float, float], list[int]] = {}
    for j, r in enumerate(rows):
        key = r.get("canonical_key")
        if not key or is_unclassified_face_key(key):
            continue
        cur, prior = split_current_prior(basis_values(r, basis))
        v1, v2 = _num((cur or {}).get("value")), _num((prior or {}).get("value"))
        if v1 is not None and v2 is not None:
            mapped.setdefault((v1, v2), []).append(j)
    dropped = stats.setdefault("dropped", {})
    out: list[_Candidate] = []
    for i in positions:
        r = rows[i]
        # A part (`sub__`) reaches its parent through the cascade, so it is already counted; on one
        # measured filing such a row matched a gap of 52,192 to within 45, a false explanation.
        if (r.get("canonical_key") or "").startswith("sub__"):
            dropped["part"] = dropped.get("part", 0) + 1
            continue
        # The analyst said this row belongs on no line.
        if r.get("mapping_method") == "manual_unmap":
            dropped["unmapped by hand"] = dropped.get("unmapped by hand", 0) + 1
            continue
        vals = basis_values(r, basis)
        if not vals:
            continue
        cur, prior = split_current_prior(vals)
        v1, v2 = _num((cur or {}).get("value")), _num((prior or {}).get("value"))
        if v1 is None or v2 is None:
            dropped["one period"] = dropped.get("one period", 0) + 1
            continue
        why = not_a_line(r, v1, v2)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        twins = [j for j in mapped.get((v1, v2), []) if j != i]
        if twins and any(caption_key(rows[j]) == caption_key(r) for j in twins):
            dropped["printed twice"] = dropped.get("printed twice", 0) + 1
            continue
        out.append(_Candidate(index=i, v1=v1, v2=v2, cur=cur, prior=prior,
                              place1=place(cur.get("provenance")),
                              place2=place(prior.get("provenance")),
                              statement=row_statement(r), section=row_section(r)))
    stats["candidates"] = stats.get("candidates", 0) + len(out)
    return out


def explain_gaps(rows: list[dict], template_def: dict | None, basis: str, *,
                 candidates: Sequence[int], remap_targets: Sequence[str], tolerance: float,
                 section_of_key: Mapping[str, str] | None = None,
                 pages: Sequence[dict] | None = None,
                 targets: Collection[str] | None = None,
                 netted_cur: dict[str, float] | None = None,
                 netted_prior: dict[str, float] | None = None,
                 calc_cur: dict | None = None, calc_prior: dict | None = None,
                 stats: dict | None = None) -> dict[str, Explanation]:
    """calculated key → the one printed row that explains its difference, for one basis.

    ``candidates`` are the positions of the rows that raise an unmapped card, so a named row always
    has a card to map it from. ``remap_targets`` are the keys a row may be mapped onto, in template
    order. ``section_of_key`` maps a key to its configured section token. ``pages`` are the run's
    classified pages, which put a continuation page of a statement in scope. ``targets`` narrows the
    lines examined; ``calc_cur``/``calc_prior`` reuse an evaluation the caller already made, with
    the same ``netted_cur``/``netted_prior``. ``stats`` collects counts for measurement.

    ``rows`` are never mutated.
    """
    stats = stats if stats is not None else {}
    nodes = calculated_nodes(template_def)
    if not nodes or not candidates:
        return {}
    # The candidates first: they do not depend on the arithmetic, and a basis with none to offer
    # then costs no evaluation at all.
    pool_rows = _candidates(rows, candidates, basis, stats)
    if not pool_rows:
        return {}
    netted = {CURRENT: netted_cur, PRIOR: netted_prior}
    calc = {CURRENT: calc_cur if calc_cur is not None
            else evaluate_rows(template_def, rows, basis, CURRENT, netted=netted_cur),
            PRIOR: calc_prior if calc_prior is not None
            else evaluate_rows(template_def, rows, basis, PRIOR, netted=netted_prior)}
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("canonical_key"):
            groups.setdefault(r["canonical_key"], []).append(r)

    # THE GAPS, from the calculated lines themselves: computable, printed and not typed over in
    # both periods, and different beyond the tolerance in both.
    gaps: dict[str, tuple[float, float, float, float]] = {}
    for key in nodes:
        if targets is not None and key not in targets:
            continue
        c1, c2 = calc[CURRENT].get(key), calc[PRIOR].get(key)
        if not (_computable(c1) and _computable(c2)):
            continue
        group = groups.get(key, [])
        if any(edited_for(x, basis, p) for x in group for p in _PERIODS):
            continue
        printed1, printed2 = concept_value(group, basis, CURRENT), concept_value(group, basis, PRIOR)
        if printed1 is None or printed2 is None:
            continue
        gap1, gap2 = printed1 - c1.value, printed2 - c2.value
        if abs(gap1) > tolerance and abs(gap2) > tolerance:
            gaps[key] = (gap1, gap2, printed1, printed2)
        elif abs(gap1) > tolerance or abs(gap2) > tolerance:
            stats["one_period_gaps"] = stats.get("one_period_gaps", 0) + 1
    stats["gaps"] = stats.get("gaps", 0) + len(gaps)
    if not gaps:
        return {}

    statement_of = _statements_of(template_def)
    order = {k: n for n, k in enumerate(remap_targets)}
    # One simulation answers every target: mapping row i onto d moves every total above d at once.
    simulated: dict[tuple[int, str], tuple[dict, dict]] = {}
    out: dict[str, Explanation] = {}
    for target, (gap1, gap2, printed1, printed2) in gaps.items():
        statement = statement_of.get(target) or ""
        sub = subtree(nodes, target)
        inside = set(sub)
        # Where the statement was printed: its own lines' pages, and every page the classifier read
        # as this statement, which is what brings in a continuation page.
        places: set[tuple] = set()
        for k in [target, *sub]:
            for x in groups.get(k, []):
                if str(x.get("printed_in") or "").lower() == "notes":
                    continue
                for v in basis_values(x, basis):
                    at = place(v.get("provenance"))
                    if at is not None:
                        places.add(at)
        for pg in pages or []:
            if (pg.get("statement") and pg.get("index") is not None
                    and normalize_statement(pg.get("statement")) == statement):
                places.add(("page", int(pg["index"])))
        tau1 = max(tolerance, REL_TOLERANCE * abs(gap1))
        tau2 = max(tolerance, REL_TOLERANCE * abs(gap2))

        hits: list[tuple[_Candidate, int]] = []
        for cand in pool_rows:
            if cand.statement and cand.statement != statement:
                continue
            if cand.place1 not in places or (cand.place2 is not None
                                             and cand.place2 not in places):
                continue
            # ONE orientation for both periods: a row that matches this year added and last year
            # subtracted is two coincidences, not an explanation.
            s = next((s for s in (1, -1)
                      if abs(s * cand.v1 - gap1) <= tau1 and abs(s * cand.v2 - gap2) <= tau2),
                     None)
            if s is not None:
                hits.append((cand, s))
        if len(hits) > MAX_HITS_PER_TARGET:
            stats.setdefault("ambiguous", []).append(target)
            continue
        if not hits:
            continue

        # A line under a subtotal that falls back to its printed figure would REPLACE that printed
        # figure rather than add to the total, so lines whose calculated parents compute in both
        # periods are tried first. The simulation still decides.
        parents: dict[str, list[str]] = {}
        for nk in [target, *sub]:
            if nk in nodes:
                for child in (nodes[nk].get("rollup") or {}).get("children") or []:
                    parents.setdefault(child, []).append(nk)

        def clear(d: str) -> bool:
            return all(_computable(calc[CURRENT].get(pk)) and _computable(calc[PRIOR].get(pk))
                       for pk in parents.get(d, []))

        dests = [k for k in remap_targets if k in inside]
        verified: list[Explanation] = []
        for cand, s in hits:
            section = cand.section if section_of_key else None
            if section:
                pool = [d for d in dests if section_of_key.get(d) == section]
                if not pool:
                    # The row's section is not part of this total.
                    stats.setdefault("section_not_in_total", []).append(target)
                    continue
            else:
                pool = list(dests)
            ranked = sorted(pool, key=lambda d: (not clear(d), order[d]))
            found = None
            for d in ranked[:MAX_SIMULATIONS_PER_HIT]:
                after = simulated.get((cand.index, d))
                if after is None:
                    sim = list(rows)
                    sim[cand.index] = {**rows[cand.index], "canonical_key": d}
                    after = tuple(evaluate_rows(template_def, sim, basis, p, netted=netted[p])
                                  for p in _PERIODS)
                    simulated[(cand.index, d)] = after
                    stats["simulations"] = stats.get("simulations", 0) + 1
                a1, a2 = after[0].get(target), after[1].get(target)
                if not (_computable(a1) and _computable(a2)):
                    continue
                if (abs((a1.value - calc[CURRENT][target].value) - s * cand.v1) <= tolerance
                        and abs((a2.value - calc[PRIOR][target].value) - s * cand.v2)
                        <= tolerance):
                    found = (d, a1.value, a2.value)
                    break
            if found is None:
                stats.setdefault("unverified", []).append(target)
                continue
            destination, after1, after2 = found
            remainder1, remainder2 = after1 - printed1, after2 - printed2
            verified.append(Explanation(
                target=target, basis=basis, statement=statement, row_index=cand.index,
                source=cand.cur.get("provenance"), source2=cand.prior.get("provenance"),
                v1=cand.v1, v2=cand.v2, orientation=s, gap1=gap1, gap2=gap2,
                remainder1=remainder1, remainder2=remainder2,
                closes=abs(remainder1) <= tolerance and abs(remainder2) <= tolerance,
                section=section, pool=tuple(pool), destination=destination))
        if len({e.row_index for e in verified}) > 1:
            stats.setdefault("ambiguous", []).append(target)
            continue
        if verified:
            out[target] = verified[0]
    return out
