"""The line-item registry: validate a set of definitions, order them, and evaluate the arithmetic.

Three jobs, kept apart from the schema so the schema stays a description and this stays the
behaviour.

VALIDATION IS THE POINT OF A CONFIGURATOR. Every mistake this catches is one somebody would
otherwise discover as a wrong figure in front of a client: a term naming a line that does not
exist, a formula that depends on itself, a parent that is not a line, an output line whose
children never sum to it. A config surface without these is a faster way to publish a wrong
number, not a safer one.

A MISSING INPUT IS MISSING, NEVER ZERO. `evaluate` returns None the moment a term it needs has no
figure, and names the term that stopped it. Treating an absent input as 0 is how a formula
produces a plausible total from an incomplete filing — the single most dangerous thing an
arithmetic layer can do here, because the result looks like an answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.schemas.line_items import LineItemDef, Term

COMPUTED_TYPES = ("calculated", "intermediate")


@dataclass
class Problem:
    """One thing wrong with a configuration, named where a person can act on it."""

    key: str
    message: str
    severity: str = "error"           # "error" refuses to load; "warning" is published as-is


@dataclass
class Registry:
    """A validated set of definitions, indexed and ordered for evaluation."""

    by_key: dict[str, LineItemDef] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)      # dependencies before dependents
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(p.severity == "error" for p in self.problems)

    def children_of(self, key: str) -> list[LineItemDef]:
        kids = [d for d in self.by_key.values() if d.parent == key]
        return sorted(kids, key=lambda d: (d.order, d.key))

    def output_items(self) -> list[LineItemDef]:
        return [d for d in self.by_key.values() if d.in_output]


def _refs(d: LineItemDef) -> list[str]:
    """Every other line item this definition's arithmetic depends on."""
    out = [t.ref for t in d.terms if t.ref]
    for rung in d.cascade:
        out.extend(t.ref for t in rung.terms if t.ref)
    return out


def build(defs: list[LineItemDef]) -> Registry:
    """Index and order the definitions, collecting every problem rather than raising on the first.

    Collecting is deliberate: someone editing twenty lines in a spreadsheet wants the whole list
    of what is wrong, not the first error and another round trip.
    """
    reg = Registry()
    for d in defs:
        if d.key in reg.by_key:
            reg.problems.append(Problem(d.key, "defined twice — keys must be unique"))
            continue
        reg.by_key[d.key] = d

    for d in reg.by_key.values():
        if d.parent and d.parent not in reg.by_key:
            reg.problems.append(Problem(d.key, f"parent {d.parent!r} is not a line item"))
        if d.parent == d.key:
            reg.problems.append(Problem(d.key, "is its own parent"))
        for ref in _refs(d):
            if ref not in reg.by_key:
                reg.problems.append(Problem(d.key, f"term names {ref!r}, which is not a line item"))
        if d.type in COMPUTED_TYPES:
            for t in d.terms:
                if t.ref == d.key:
                    reg.problems.append(Problem(d.key, "a term refers to the line itself"))

    reg.order = _topological(reg)
    return reg


def _topological(reg: Registry) -> list[str]:
    """Dependencies first, so a formula is only evaluated once its inputs are known.

    A CYCLE IS REPORTED, NOT BROKEN. Two lines defined as each other's input have no value, and
    picking one to evaluate first would invent one. Every key in the cycle is named, because
    "there is a cycle" is not actionable and "these four lines form one" is.
    """
    state: dict[str, int] = {}         # 0 = visiting, 1 = done
    order: list[str] = []

    def visit(key: str, path: list[str]) -> None:
        if state.get(key) == 1:
            return
        if state.get(key) == 0:
            cycle = path[path.index(key):] + [key]
            reg.problems.append(Problem(key, "formula depends on itself, through "
                                             + " -> ".join(cycle)))
            return
        state[key] = 0
        for ref in _refs(reg.by_key[key]):
            if ref in reg.by_key:
                visit(ref, path + [key])
        state[key] = 1
        order.append(key)

    for key in reg.by_key:
        visit(key, [])
    return order


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass
class Evaluation:
    """The result of one line's arithmetic, and enough to explain it on screen."""

    value: Decimal | None
    inputs: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    rung_used: str | None = None
    # Rungs that DID resolve and were passed over for computing below zero. Reported rather than
    # discarded: "P3 computed -50, so P4 was used" is the sentence a reviewer needs, and a silent
    # skip looks identical to a rung whose inputs were simply absent.
    refused_rungs: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.value is not None


def _apply_terms(terms: list[Term], known: dict[str, Decimal | None]) -> Evaluation:
    """The three term roles, and what each one's absence means. See `Term.role`.

    A `required` term missing kills the rung. An `any_of` term missing is fine as long as one of
    its siblings is present. An `adjustment` missing simply does not apply — and, the part that
    matters, an adjustment PRESENT does not on its own make the rung resolvable: a rung whose
    only figure is a deduction has nothing to deduct it from, and treating that as an answer
    produced a negative depreciation charge before the roles were separated.

    Nothing at the base at all is None, never 0: a sum over four absent notes has no value, and
    publishing zero would assert the filing charged none.
    """
    total = Decimal(0)
    inputs: list[dict] = []
    missing: list[str] = []
    base_present = 0          # a `required` or `any_of` figure — something the rung is built on
    for t in terms:
        raw = Decimal(str(t.const)) if t.const is not None else known.get(t.ref)
        if raw is None:
            missing.append(t.ref or "fixed number")
            if t.role == "required":
                return Evaluation(None, inputs, missing)
            continue
        used = abs(raw) if t.abs else raw
        total += t.sign * used
        if t.role != "adjustment":
            base_present += 1
        inputs.append({"ref": t.ref or None, "const": t.const, "sign": t.sign,
                       "abs": t.abs, "role": t.role,
                       "value": str(raw), "used": str(t.sign * used)})
    if base_present == 0:
        return Evaluation(None, inputs, missing)
    return Evaluation(total, inputs, missing)


def evaluate(d: LineItemDef, known: dict[str, Decimal | None]) -> Evaluation:
    """One line's value from the values already known.

    `known` maps key -> value for everything evaluated so far (and for every extracted line, from
    the document). Callers walk `Registry.order`, so a dependency is always present by the time
    its dependent is reached.
    """
    if d.type in COMPUTED_TYPES:
        return _apply_terms(d.terms, known)

    if d.type == "derived":
        refused: list[str] = []
        for rung in d.cascade:
            got = _apply_terms(rung.terms, known)
            if not got.resolved:
                continue
            # A RUNG BELOW ZERO IS NOT AN ANSWER, it is evidence this rung's inputs did not mean
            # what the rung assumed, so the cascade skips the candidate and tries the next rung
            # down. Every shipped cascade computes a charge, a balance or an exposure, none of
            # which can be negative; a rung that resolves to -50 has mistaken its inputs, not
            # found a figure. The refusals are reported rather than swallowed so a reviewer can
            # see which rung was passed over and why the answer came from further down.
            if rung.refuse_negative and got.value is not None and got.value < 0:
                refused.append(f"{rung.id} computed {got.value}")
                continue
            got.rung_used = rung.id
            got.refused_rungs = refused
            return got
        return Evaluation(None, [], [], None, refused_rungs=refused)

    # extracted — the document supplies it; this layer only reports what it was given
    return Evaluation(_dec(known.get(d.key)))


def evaluate_all(reg: Registry, extracted: dict[str, Decimal | None]) -> dict[str, Evaluation]:
    """Every line's value, dependencies first. `extracted` is what the document yielded."""
    known: dict[str, Decimal | None] = dict(extracted)
    out: dict[str, Evaluation] = {}
    for key in reg.order:
        d = reg.by_key[key]
        got = evaluate(d, known)
        out[key] = got
        known[key] = got.value
    return out


def check_rollups(reg: Registry, values: dict[str, Decimal | None], *,
                  rel_tolerance: float = 0.01) -> list[Problem]:
    """Where an output line has sub-line items, whether they come to it.

    A WARNING, NOT AN ERROR, and the distinction matters: sub-line items are the parts a filing
    happened to disclose, and a filing routinely discloses some of them. A parent that disagrees
    with the sum of its children is worth a reviewer's attention, not a refusal to publish.
    Silence here would leave the arithmetic unchecked, which is the thing a configurator most
    needs to be honest about.

    ONLY WHERE `rollup == "sum"`. This check used to run on every parent, which was wrong for the
    line it was written for: the twelve sub-line items under `is_pl__deprec_and_impairment_oper_exp`
    are ALTERNATIVE sources for one figure, which is what the set declares by giving that parent
    `rollup: alternatives` — filings routinely restate the same charge in several of those notes,
    so summing them would have reported the parent as disagreeing with a total that means nothing.
    `parent` was carrying two relations at once; `rollup` separates them.
    """
    out: list[Problem] = []
    for parent in reg.by_key.values():
        if parent.rollup != "sum":
            continue
        kids = [k for k in reg.children_of(parent.key) if k.type != "intermediate"]
        if not kids or parent.type in COMPUTED_TYPES:
            continue                    # a formula already states its own arithmetic
        pv = values.get(parent.key)
        parts = [values.get(k.key) for k in kids]
        if pv is None or not any(p is not None for p in parts):
            continue
        total = sum((p for p in parts if p is not None), Decimal(0))
        scale = max(abs(pv), abs(total))
        if scale and abs(pv - total) / scale > Decimal(str(rel_tolerance)):
            out.append(Problem(
                parent.key,
                f"published {pv} but its {len(kids)} sub-line items come to {total}",
                severity="warning"))
    return out
