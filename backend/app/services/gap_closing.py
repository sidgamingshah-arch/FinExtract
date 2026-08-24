"""Closing a subtotal gap by routing leftover lines into a section's Others.

The face carries the COMPUTED figure for every calculated line (see services.rollups). When that
differs from the figure the document printed, the most common cause is not bad arithmetic — it is
a line the mapper could not place. The filing's subtotal includes it; our computation does not.

At the same time the extraction usually holds a handful of lines that reached no face statement at
all — captions the mapper found no concept for, or found a concept for that no statement carries.
This module asks whether any of those, dropped into the section's residual "Others" bucket, would
account for exactly the difference.

Two things decide it, and both must agree:

* **Arithmetic** proposes. Only a subset of leftovers whose figures close the gap in BOTH periods
  within tolerance is even offered — one period is a coincidence waiting to happen, two is
  evidence. This bounds the search and makes a wrong answer arithmetically visible.
* **The model disposes.** Whether "Pledged bank deposits" belongs under current assets is a
  question about meaning, not about numbers, and a subset that happens to add up is not a reason
  to move a line into a section it does not belong to. The provider picks one of the offered
  subsets, or none.

With no provider configured nothing is routed: the gap stays a review item, which is the honest
outcome rather than a guess dressed as a reconciliation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import combinations

from pydantic import BaseModel, Field

# A gap smaller than this is float noise from summing, not a missing line.
MIN_GAP = 0.5
# Search bounds. A section's genuine leftovers are a handful; beyond that the combinatorics say
# more about the extraction being broken than about which line is missing.
MAX_CANDIDATES = 14
MAX_SUBSET_SIZE = 3
MAX_SUBSETS_OFFERED = 8


@dataclass
class Leftover:
    """An extracted line that reaches no face statement — a candidate for a section's Others."""
    index: int                      # position in the run's rows, so the decision can be applied
    label: str
    canonical_key: str | None
    current: float | None
    prior: float | None
    page: int | None
    note: str | None = None


@dataclass
class Gap:
    """A calculated line whose computed figure differs from the one the document printed."""
    target_key: str
    target_label: str
    others_key: str
    section_label: str
    statement: str
    basis: str
    printed_current: float | None
    computed_current: float | None
    printed_prior: float | None
    computed_prior: float | None

    @property
    def current(self) -> float:
        """How much the printed subtotal exceeds our computation this year (signed)."""
        return (self.printed_current or 0.0) - (self.computed_current or 0.0)

    @property
    def prior(self) -> float | None:
        if self.printed_prior is None or self.computed_prior is None:
            return None
        return self.printed_prior - self.computed_prior


class OthersRoutingDecision(BaseModel):
    """The model's answer: which offered option (if any) is a genuine member of the section."""
    option: int = Field(default=-1, description="1-based index of the chosen option, or -1 for none")
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0, le=1)


ROUTING_SYSTEM = (
    "You are a financial-statement analyst reconciling a spread against the filing it came from.\n"
    "A SUBTOTAL computed from the template's line items does not equal the subtotal printed in the "
    "document. The difference is usually a line the mapper could not place: the filing's subtotal "
    "includes it, ours does not.\n"
    "You are given the section, the subtotal, the difference, and a set of OPTIONS. Each option is "
    "a group of extracted lines that reached no statement and whose figures close the difference "
    "exactly, in both periods. The arithmetic is already verified — do not re-check it.\n"
    "Your job is the question arithmetic cannot answer: do those lines genuinely BELONG in this "
    "section of this statement? Judge by the caption's meaning, the section it would join, and the "
    "page it was printed on.\n"
    "Choose the option whose lines are true members of the section. Choose -1 — and prefer -1 — "
    "when no option's lines plausibly belong there: adding up is a coincidence, not a reason. A "
    "line from a note table, a narrative sentence, or another statement's figures does not belong "
    "in this section no matter what it sums to. Never invent lines or numbers."
)


@dataclass
class Routing:
    """A confirmed decision: these leftover rows belong in this section's Others."""
    others_key: str
    target_key: str
    basis: str
    moved: list[int] = field(default_factory=list)      # indices into the run's rows
    labels: list[str] = field(default_factory=list)
    gap_current: float = 0.0
    gap_prior: float | None = None
    rationale: str = ""
    confidence: float = 0.0
    provider: str = ""
    model: str = ""


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def leftovers(rows: list[dict], template_def: dict | None, basis: str) -> list[Leftover]:
    """Extracted lines that reach no face statement, in document order.

    "Reaches no face" is decided against the keys the template actually PUTS ON A GRID
    (``_declared_line_keys``), not against the statement PREFIX of the key. The prefix test was true
    only while the statement builder rendered any prefix-matching key under an "Other extracted
    items" heading: a row mapped to ``bs_something_the_template_never_declared`` was on the balance
    sheet, so treating it as placed was right. That heading is gone — an off-template row now renders
    nowhere and is raised as an ``unmapped`` review finding instead (one category, two ways in) — so
    the prefix test would call such a row placed while nothing shows it, and it would be the one row a
    gap-closing pass could most usefully have offered.

    Otherwise the same population as before, and for the same reason: these are the only figures
    whose placement is still open, so they are the only honest candidates for a gap.
    """
    from app.api.routes.documents import _declared_line_keys, _is_named_column
    from app.services.periods import basis_values, split_current_prior

    declared = _declared_line_keys(template_def)
    out: list[Leftover] = []
    for i, r in enumerate(rows):
        vals = basis_values(r, basis)
        if not vals:
            continue
        # A matrix-shaped row (equity components) is already on the changes-in-equity face.
        if all(_is_named_column(v.get("period_label")) for v in vals):
            continue
        key = r.get("canonical_key") or ""
        if key and key in declared:
            continue
        cur, prior = split_current_prior(vals)
        c, p = _num((cur or {}).get("value")), _num((prior or {}).get("value"))
        if c is None and p is None:
            continue
        prov = (cur or prior or {}).get("provenance") or {}
        out.append(Leftover(index=i, label=r.get("source_label") or "", canonical_key=key or None,
                            current=c, prior=p, page=prov.get("page_index"),
                            note=r.get("note")))
    return out


def _section_of(template_def: dict | None, target_key: str) -> tuple[str, str, str] | None:
    """(others_key, section_label, statement_type) for the section a calculated line subtotals.

    A section's subtotal shares its namespace with the section's residual bucket
    ("bs_current_assets__total_current_assets" → "bs_current_assets__others"), which is where a
    line that belongs to the section but to no specific concept goes.
    """
    if "__" not in (target_key or ""):
        return None                      # a statement-level total has no single section
    namespace = target_key.split("__", 1)[0]
    others = f"{namespace}__others"
    for stmt in (template_def or {}).get("statements", []):
        for sec in stmt.get("sections") or []:
            children = {c.get("canonical_key") for c in sec.get("children") or []}
            if others in children and target_key in children:
                return others, sec.get("label") or namespace, stmt.get("type") or ""
    return None


def find_gaps(rows: list[dict], template_def: dict | None, basis: str,
              locale: str = "en") -> list[Gap]:
    """Section subtotals whose computed figure differs from the printed one, with room to fix."""
    from app.services.periods import concept_value
    from app.services.rollups import evaluate_rows, node_labels

    names = node_labels(template_def, locale)
    calc_cur = evaluate_rows(template_def, rows, basis, "current", locale)
    calc_prior = evaluate_rows(template_def, rows, basis, "prior", locale)

    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = r.get("canonical_key")
        if k:
            groups.setdefault(k, []).append(r)

    out: list[Gap] = []
    for key, c in calc_cur.items():
        if c.cycle or not c.computable:
            continue
        section = _section_of(template_def, key)
        if section is None:
            continue
        printed = concept_value(groups.get(key, []), basis, "current")
        if printed is None or abs(printed - (c.value or 0.0)) <= MIN_GAP:
            continue
        cp = calc_prior.get(key)
        out.append(Gap(
            target_key=key, target_label=names.get(key, key), others_key=section[0],
            section_label=section[1], statement=section[2], basis=basis,
            printed_current=printed, computed_current=c.value,
            printed_prior=concept_value(groups.get(key, []), basis, "prior"),
            computed_prior=cp.value if (cp and cp.computable) else None,
        ))
    return out


def viable_subsets(cands: list[Leftover], gap: Gap, *, tol: float = MIN_GAP,
                   max_size: int = MAX_SUBSET_SIZE,
                   limit: int = MAX_SUBSETS_OFFERED) -> list[list[Leftover]]:
    """Groups of leftovers whose figures close the gap — in both periods when both are known.

    Requiring both years is what separates evidence from coincidence: a single figure matching a
    difference happens by chance in a statement full of numbers; the same lines matching last
    year's difference too does not. Smaller groups are preferred — one missing line is a far more
    likely explanation than three.
    """
    if abs(gap.current) <= tol:
        return []
    pool = cands[:MAX_CANDIDATES]
    prior_gap = gap.prior
    found: list[list[Leftover]] = []
    for size in range(1, max_size + 1):
        for combo in combinations(pool, size):
            cur_sum = sum(c.current or 0.0 for c in combo)
            if abs(cur_sum - gap.current) > tol:
                continue
            if prior_gap is not None and abs(prior_gap) > tol:
                if any(c.prior is None for c in combo):
                    continue
                if abs(sum(c.prior or 0.0 for c in combo) - prior_gap) > tol:
                    continue
            found.append(list(combo))
            if len(found) >= limit:
                return found
    return found


def _payload(gap: Gap, options: list[list[Leftover]], locale: str) -> dict:
    return {
        "statement": gap.statement,
        "section": gap.section_label,
        "subtotal": {"label": gap.target_label, "canonical_key": gap.target_key},
        "printed_in_document": None if gap.printed_current is None else f"{gap.printed_current:,.0f}",
        "computed_from_template_lines": None if gap.computed_current is None
                                        else f"{gap.computed_current:,.0f}",
        "difference_current": f"{gap.current:,.0f}",
        "difference_prior": None if gap.prior is None else f"{gap.prior:,.0f}",
        "options": [
            {"option": i + 1,
             "lines": [{"caption": c.label,
                        "current": None if c.current is None else f"{c.current:,.0f}",
                        "prior": None if c.prior is None else f"{c.prior:,.0f}",
                        "printed_on_page": None if c.page is None else c.page + 1,
                        "cites_note": c.note}
                       for c in combo]}
            for i, combo in enumerate(options)
        ],
        "would_be_placed_in": gap.others_key,
        "output_language": locale,
    }


def resolve_gap(provider, gap: Gap, cands: list[Leftover], *, locale: str = "en",
                max_tokens: int = 600, min_confidence: float = 0.0) -> Routing | None:
    """Offer the arithmetically viable options to the provider and return the one it confirms.

    A provider that errors, declines, or names an option outside the offered set routes nothing —
    the gap then stays a review item, which is what it was before this ran.
    """
    options = viable_subsets(cands, gap)
    if not options:
        return None
    result, meta = provider.complete_structured(
        system=ROUTING_SYSTEM,
        messages=[{"role": "user",
                   "content": json.dumps(_payload(gap, options, locale), ensure_ascii=False,
                                         indent=2)}],
        response_schema=OthersRoutingDecision, max_tokens=max_tokens,
    )
    choice = int(getattr(result, "option", -1) or -1)
    if choice < 1 or choice > len(options):
        return None
    conf = float(getattr(result, "confidence", 0.0) or 0.0)
    if conf < min_confidence:
        return None
    chosen = options[choice - 1]
    return Routing(
        others_key=gap.others_key, target_key=gap.target_key, basis=gap.basis,
        moved=[c.index for c in chosen], labels=[c.label for c in chosen],
        gap_current=gap.current, gap_prior=gap.prior,
        rationale=str(getattr(result, "rationale", "") or ""), confidence=conf,
        provider=getattr(provider, "id", "") or "",
        model=str((meta or {}).get("model", "") if isinstance(meta, dict)
                  else getattr(meta, "model", "") or ""),
    )


def resolve_all(provider, rows: list[dict], template_def: dict | None, *,
                locale: str = "en", max_tokens: int = 600) -> list[dict]:
    """Every gap the provider confirms a fix for, as plain dicts to cache on the run.

    Bases are handled independently: a consolidated subtotal and a standalone one are different
    statements and a line belongs to one of them, not both. A leftover already claimed by an
    earlier gap is not offered again, so one line cannot be spent twice.
    """
    out: list[dict] = []
    for basis in ("consolidated", "standalone"):
        cands = leftovers(rows, template_def, basis)
        if not cands:
            continue
        claimed: set[int] = set()
        for gap in find_gaps(rows, template_def, basis, locale):
            pool = [c for c in cands if c.index not in claimed]
            if not pool:
                break
            try:
                routing = resolve_gap(provider, gap, pool, locale=locale, max_tokens=max_tokens)
            except Exception:  # noqa: BLE001 — provider unreachable/misconfigured → route nothing
                continue
            if routing is None:
                continue
            claimed.update(routing.moved)
            out.append({
                "others_key": routing.others_key, "target_key": routing.target_key,
                "basis": routing.basis, "moved": routing.moved, "labels": routing.labels,
                "gap_current": routing.gap_current, "gap_prior": routing.gap_prior,
                "rationale": routing.rationale, "confidence": routing.confidence,
                "provider": routing.provider, "model": routing.model,
            })
    return out
