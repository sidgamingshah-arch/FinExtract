"""Computing the template's calculated lines, and the rulebook's section arithmetic.

A template marks each line `extracted` (read off the document) or `calculated` — a subtotal,
a total, a net figure — declared with a `rollup {op, children}` naming exactly which lines feed
it. This module evaluates those declarations.

A v2 ontology states the same kind of arithmetic from the other side: not "this node is the sum of
those nodes" but "this SECTION must account for every row printed under it"
(``residual_framework.reconciliation``). :func:`section_members` and :func:`reconcile_section`
evaluate that identity, and they live here rather than in the residual stage so there is one module
that knows how a total is built from its parts — the two must not disagree about what counts as a
contributor.

Why the computed figure is the one that gets shown: a subtotal read off the page is a fourth
opinion, alongside the lines it is meant to be the sum of. When they disagree, showing the
printed one puts a number on the face that its own components contradict, and every total above
it inherits the contradiction. Showing the computed one makes the spread internally consistent by
construction — and turns the disagreement into a finding, which is what the review queue is for.

The printed figure is never discarded. It is what the divergence is measured against.

Evaluation is dependency-ordered because rollups nest: a statement total rolls up section
subtotals which roll up their own lines. A cycle (a template that declares A from B and B from A)
is reported rather than recursed into.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: A figure read out of captions the face already carries — shown on its line, not added into its
#: section. Written by a `carved_from_face` cascade rung (`stages.note_sourced`); read here and by
#: the structural checks.
CARVED_FROM_FACE = "carved_from_face"

#: How far a printed line may differ from its disclosed parts and still be taken to contain them.
_PARTS_TOLERANCE = 1.0

# Ops a template may declare. `diff` is "first term minus the rest" — how a net figure is
# written (net current assets = current assets − current liabilities).
SUM = "sum"
DIFF = "diff"
WEIGHTED = "weighted_sum"


@dataclass
class Component:
    """One input to a calculated line, and the figure it contributed."""
    canonical_key: str
    label: str
    value: float | None
    sign: int = 1
    # Whether the figure was read as a MAGNITUDE rather than a signed amount — see
    # ``cost_magnitude_children`` on the rollup. Carried rather than folded into ``value``
    # because the two answer different questions: ``value`` is what the source said, and
    # ``contribution`` is what the arithmetic did with it.
    as_magnitude: bool = False

    @property
    def contribution(self) -> float | None:
        """The figure as it ENTERED the total, before the formula's own sign is applied.

        For a magnitude member the amount is taken absolute and spent as a cost, so the total
        moves DOWN by it wherever it sits — in a flat sum of negative cost lines and in a
        residual that subtracts it from a reported parent alike. Pre-multiplying by ``sign``
        is what makes those two agree: the caller applies ``sign`` either way.
        """
        if self.value is None:
            return None
        return -abs(self.value) * self.sign if self.as_magnitude else self.value


@dataclass
class Calculated:
    """One calculated line's evaluation for a single (basis, period)."""
    canonical_key: str
    op: str
    components: list[Component] = field(default_factory=list)
    value: float | None = None
    # False when NO component had a figure, so there was nothing to compute from. Distinct from
    # a computed zero, and the difference matters: one is a gap, the other is an answer.
    computable: bool = False
    cycle: bool = False

    @property
    def formula(self) -> str:
        """The arithmetic as written, e.g. "12,800 + 2,150 + 3,410" or "19,564 − 8,120"."""
        joiner = " − " if self.op == DIFF else " + "
        parts = []
        for i, c in enumerate(self.components):
            figure = c.contribution
            if figure is None:
                parts.append("—")
            else:
                parts.append(f"{abs(figure):,.0f}" if i or figure >= 0 else f"({abs(figure):,.0f})")
        return joiner.join(parts)


@dataclass
class SectionMembers:
    """The concepts a v2 rulebook prints under one ``section_scope`` id, split by their role.

    ``unit_of_account`` is what splits them, and it is why the split is not "the key looks like a
    total": a section may print TWO subtotals (cash generated from operations, then net cash from
    operating activities) and neither is a contributor to the other — adding an intermediate
    subtotal into the section's own sum counts every line above it a second time.
    """

    section: str
    statement: str | None = None
    # Subtotals in ascending ``match_priority``, so the section's CLOSING subtotal is last: the
    # rulebook ranks the long specific caption above the intermediate one it contains.
    subtotals: list[str] = field(default_factory=list)
    dedicated: list[str] = field(default_factory=list)
    residuals: list[str] = field(default_factory=list)


def section_members(ontology) -> dict[str, SectionMembers]:
    """Per ``section_scope`` id, the concepts printed under it.

    Reads the RESOLVED ontology: ``section_scope``, ``statement`` and ``unit_of_account`` are
    authored on ``section_defaults`` and reach a concept through ``inherits``, so an unresolved
    definition yields no sections at all rather than a partial answer.
    """
    out: dict[str, SectionMembers] = {}
    for m in getattr(ontology, "mappings", []) or []:
        if not m.section_scope:
            continue
        section = m.section_scope[0]
        entry = out.get(section)
        if entry is None:
            statement = m.statement.value if getattr(m.statement, "value", None) else m.statement
            entry = out[section] = SectionMembers(section=section, statement=statement)
        if m.unit_of_account == "subtotal":
            entry.subtotals.append(m.canonical_key)
        elif m.value_scope == "exclusive_residual":
            entry.residuals.append(m.canonical_key)
        else:
            entry.dedicated.append(m.canonical_key)
    priority = {m.canonical_key: (m.match_priority or 0) for m in getattr(ontology, "mappings", [])}
    for entry in out.values():
        entry.subtotals.sort(key=lambda k: priority.get(k, 0))
    return out


@dataclass
class SectionRecon:
    """One section's reconciliation for one (basis, period).

    ``status`` separates the three outcomes the framework distinguishes: ``tied``,
    ``unallocated_gap`` (the section does not account for its own printed rows), and
    ``no_reported_subtotal`` (nothing to reconcile against — itemised, but unverified).
    """

    section: str
    subtotal_key: str = ""
    reported: float | None = None
    dedicated_total: float | None = None
    residual_total: float | None = None
    contributors: int = 0
    components: int = 0
    tolerance: float = 0.0
    diff: float | None = None
    status: str = "no_reported_subtotal"


def reconcile_section(members: SectionMembers, value_of, components: list[float], *,
                      rounding_unit: float = 1.0, per_row_tolerance: bool = True) -> SectionRecon:
    """Evaluate ``reported_section_subtotal − Σ(dedicated) − Σ(residual components) = 0``.

    ``value_of(key)`` gives the figure the document supplies for a concept in this column (None
    where the concept is absent). ``components`` are the residual's swept component values —
    passed in rather than looked up, because the identity is stated over the COMPONENTS: a residual
    that carried a value with no components behind it is the plug the framework forbids, and it
    would tie here while proving nothing.

    The residual is never solved for. A break is reported as a difference, not absorbed.
    """
    reported = None
    subtotal_key = ""
    # The section's closing subtotal is the highest-priority one that the document actually
    # printed; an intermediate subtotal is not the section's total.
    for key in reversed(members.subtotals):
        val = value_of(key)
        if val is not None:
            reported, subtotal_key = val, key
            break

    dedicated_total: float | None = None
    contributors = 0
    for key in members.dedicated:
        val = value_of(key)
        if val is None:
            continue
        contributors += 1
        dedicated_total = val if dedicated_total is None else dedicated_total + val

    residual_total = sum(components) if components else None
    recon = SectionRecon(
        section=members.section, subtotal_key=subtotal_key, reported=reported,
        dedicated_total=dedicated_total, residual_total=residual_total,
        contributors=contributors, components=len(components),
    )
    # "one rounding unit per contributing row": every row that feeds the identity may have been
    # rounded independently, so the allowance grows with the number of rows, not with the figure.
    rows = contributors + len(components) + (1 if subtotal_key else 0)
    recon.tolerance = rounding_unit * (rows if per_row_tolerance else 1)
    if reported is None:
        return recon
    recon.diff = reported - (dedicated_total or 0.0) - (residual_total or 0.0)
    recon.status = "tied" if abs(recon.diff) <= recon.tolerance else "unallocated_gap"
    return recon


def calculated_nodes(template_def: dict | None) -> dict[str, dict]:
    """canonical_key → the node, for every template line that declares a rollup."""
    out: dict[str, dict] = {}
    for stmt in (template_def or {}).get("statements", []):
        for sec in stmt.get("sections") or []:
            for node in [sec, *(sec.get("children") or [])]:
                key = node.get("canonical_key")
                # `rollup` is PRESENT and null on every non-calculated node once the template has
                # been through Pydantic, so `get("rollup", {})` hands back None, not the default.
                rollup = node.get("rollup") or {}
                if key and (rollup.get("children") or rollup.get("reported_total_key")):
                    out[key] = node
    return out


def node_roles(template_def: dict | None) -> dict[str, str]:
    """canonical_key -> the ROLE the template declares for it ("line"/"subtotal"/"total"/"header").

    The template is the only thing that knows a caption is a subtotal. A filing does not mark them:
    "Gross profit", "LOSS FROM OPERATING ACTIVITIES", "LOSS BEFORE TAX" and "LOSS FOR THE YEAR" are
    printed exactly like the lines they total, so a reader of the PAGE cannot tell, and every one of
    them reached the pipeline as ``LineRole.LINE``. The template declares each of them a total, and
    that verdict has two consequences a line does not get: its figure is the sum of its children
    rather than a reading of the page, and it is never swept into a section residual.
    """
    out: dict[str, str] = {}
    for stmt in (template_def or {}).get("statements", []):
        for sec in stmt.get("sections") or []:
            for node in [sec, *(sec.get("children") or [])]:
                key, role = node.get("canonical_key"), node.get("role")
                if key and role:
                    out[key] = role
    return out


def node_labels(template_def: dict | None, locale: str = "en") -> dict[str, str]:
    """canonical_key → the template's label for it, so a component can be named."""
    out: dict[str, str] = {}
    for stmt in (template_def or {}).get("statements", []):
        for sec in stmt.get("sections") or []:
            for node in [sec, *(sec.get("children") or [])]:
                key = node.get("canonical_key")
                if key:
                    out[key] = (node.get("label_i18n") or {}).get(locale) or node.get("label") \
                        or key
    return out


def _readers(rows: list[dict], basis: str, period: str, netted: dict[str, float] | None):
    """``(reported, overridden)`` over a run's rows — the two callbacks :func:`evaluate` needs.

    ``netted`` restates a component's figure under a netting rule and is applied HERE, before any
    rollup reads it: a subtotal computed from the un-netted component no longer equals the
    components printed beneath it, and the spread would contradict itself on its own face.
    """
    from app.services.periods import concept_value, edited_for

    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = r.get("canonical_key")
        if key:
            groups.setdefault(key, []).append(r)

    def reported(key: str):
        if netted and key in netted:
            return netted[key]
        return concept_value(groups.get(key, []), basis, period)

    def overridden(key: str) -> bool:
        return any(edited_for(x, basis, period) for x in groups.get(key, []))

    def carved(key: str) -> bool:
        """The figure was read out of captions the face already carries — see
        ``CascadeRung.carved_from_face``. Shown on its line; not added into a total again."""
        return any(CARVED_FROM_FACE in ((v.get("confidence") or {}).get("flags") or ())
                   for x in groups.get(key, []) for v in x.get("values") or ()
                   if v.get("basis") == basis and v.get("period_label") == period)

    return groups, reported, overridden, carved


def evaluate_rows(template_def: dict | None, rows: list[dict], basis: str, period: str,
                  locale: str = "en",
                  netted: dict[str, float] | None = None) -> dict[str, "Calculated"]:
    """Evaluate the calculated lines directly from a run's extracted rows.

    The single entry point for the statement API, the Excel export and the KPI layer, so none of
    the three can disagree with the others about what a subtotal is. Inputs are read through
    ``periods.concept_value``, which means a component's manual correction flows straight into
    every subtotal above it.
    """
    _, reported, overridden, carved = _readers(rows, basis, period, netted)
    return evaluate(template_def, reported, labels=node_labels(template_def, locale),
                    overridden=overridden, carved=carved)


def figures_as_shown(template_def: dict | None, rows: list[dict], basis: str, period: str,
                     locale: str = "en",
                     netted: dict[str, float] | None = None) -> dict[str, float]:
    """Every concept's figure AS THE GRID SHOWS IT, for one (basis, period).

    Precedence per period, the same three tests the Workspace row builder applies: an analyst's
    MANUAL value outranks the arithmetic, then the COMPUTED figure where the template says the line
    is made of others and they were extracted, then the PRINTED figure for a line with nothing to
    compute from.

    WHY THIS EXISTS, and it is the whole point. Everything that reasons about a filing's numbers has
    to reason about the same numbers. The statement grid and the export already shared one resolver;
    the KPI layer did not — it read printed figures only (``derived._value`` →
    ``periods.concept_value``). So a calculated line the filing does not print had a figure on screen
    and none in the ratios: with the income statement's operating profit gaining a formula, the grid
    showed a computed EBIT while EBIT interest coverage, EBIT margin and EBITDA all reported the
    input missing. One quantity, two answers, and no screen that could show them disagreeing.

    Per period on purpose. An analyst who corrects the current column has said nothing about last
    year, and a period whose components were not extracted is not made computable by the other
    period's being so.
    """
    groups, reported, overridden, carved = _readers(rows, basis, period, netted)
    calc = evaluate(template_def, reported, labels=node_labels(template_def, locale),
                    overridden=overridden, carved=carved)
    out: dict[str, float] = {}
    for key in groups:
        value = reported(key)
        if value is not None:
            out[key] = float(value)
    for key, c in calc.items():
        # An answered line is settled; the arithmetic does not get to overrule it.
        if c.computable and not overridden(key):
            out[key] = float(c.value)
    return out


def _order(nodes: dict[str, dict]) -> tuple[list[str], set[str]]:
    """Calculated keys in dependency order, plus the keys caught in a cycle.

    A total that rolls up subtotals has to be evaluated after them, or it sums the printed
    figures it was supposed to replace.
    """
    order: list[str] = []
    state: dict[str, int] = {}          # 1 = visiting, 2 = done
    cyclic: set[str] = set()

    def visit(key: str, path: tuple[str, ...]) -> None:
        if state.get(key) == 2:
            return
        if state.get(key) == 1:
            cyclic.update(path[path.index(key):] if key in path else (key,))
            return
        state[key] = 1
        for child in (nodes[key].get("rollup") or {}).get("children") or []:
            if child in nodes:
                visit(child, path + (key,))
        state[key] = 2
        order.append(key)

    for key in nodes:
        visit(key, ())
    return order, cyclic


def evaluate(template_def: dict | None, reported, *, labels: dict[str, str] | None = None,
             prefer_calculated: bool = True, overridden=None,
             carved=None) -> dict[str, Calculated]:
    """Evaluate every calculated line for one (basis, period).

    ``reported(key)`` returns the figure the DOCUMENT gives for a canonical key — the extracted
    value, or an analyst's manual override where there is one. ``prefer_calculated`` decides what
    a nested rollup sees when it reaches another calculated line: True feeds it the computed
    figure (so the whole tree is consistent with the leaves), False feeds it the printed one
    (which is what a reported-vs-computed comparison has to measure against).
    """
    nodes = calculated_nodes(template_def)
    names = labels or node_labels(template_def)
    order, cyclic = _order(nodes)
    out: dict[str, Calculated] = {}

    def figure(key: str) -> float | None:
        # A line an analyst has answered for is settled: their value is what the grid shows, so it
        # is what every total above it must be built from. Preferring the computation there would
        # accept the override on the line itself and ignore it one row up — the spread would show
        # a subtotal that its own components contradict.
        if overridden is not None and overridden(key):
            return reported(key)
        if prefer_calculated and key in out and out[key].computable:
            return out[key].value
        return reported(key)

    for key in order:
        rollup = nodes[key].get("rollup") or {}
        op = str(rollup.get("op") or SUM).lower()
        calc = Calculated(canonical_key=key, op=op, cycle=key in cyclic)
        if calc.cycle:
            out[key] = calc
            continue
        reported_total_key = rollup.get("reported_total_key")
        reported_total = reported(reported_total_key) if reported_total_key else None
        reported_total_op = str(rollup.get("reported_total_op") or SUM).lower()
        total: float | None = reported_total
        children = rollup.get("children") or []
        if rollup.get("use_reported_total_components") and reported_total_key in nodes:
            children = [child for child in (nodes[reported_total_key].get("rollup") or {})
                        .get("children") or [] if child != key]
        # MEMBERS WHOSE FIGURE IS A MAGNITUDE, not a signed amount. Every cost line in this
        # statement arrives negative because that is how the filing prints it and how `normalize`
        # reads it — but a line a SERVICE computes is written after `normalize` and carries the
        # magnitude it was assembled from. In a flat `sum` those two conventions cannot both be
        # right: a positive cost adds where its siblings subtract, so the total moves by twice
        # the figure. Declared per rollup, so the same concept can be a magnitude in a `sum` of
        # costs and stay untouched in the residual rollups that subtract it from a reported total.
        magnitude = set(rollup.get("cost_magnitude_children") or [])
        for i, child in enumerate(children):
            # A FIGURE ALREADY INSIDE ITS SIBLINGS is not added again — in a sum or in a residual's
            # deductions alike. See ``CascadeRung.carved_from_face``.
            if carved is not None and carved(child):
                continue
            # In a `diff`, the first term is added and the rest subtracted.
            sign = -1 if reported_total is not None and reported_total_op == DIFF \
                else -1 if (op == DIFF and i > 0) else 1
            val = figure(child)
            component = Component(canonical_key=child, label=names.get(child, child),
                                  value=val, sign=sign, as_magnitude=child in magnitude)
            calc.components.append(component)
            # The absolute amount FIRST, then the sign the formula gives it, so a charge that
            # arrives as a magnitude is spent rather than credited back.
            contribution = component.contribution
            if contribution is not None:
                total = (sign * contribution if total is None
                         else total + sign * contribution)
        calc.value = total
        # A parent-minus-components residual is not valid when one of its declared deductions is
        # absent. Missing disclosure is not zero unless the source explicitly says so.
        requires_complete = reported_total is not None or nodes[key].get("role") == "line"
        complete = not requires_complete or all(component.value is not None
                              for component in calc.components)
        calc.computable = total is not None and complete
        # A LINE WHOSE PARTS ARE ONLY PARTLY DISCLOSED — net fixed assets, the one plain rollup on a
        # `line` (gross fixed assets + accumulated depreciation). A face that prints net figures
        # discloses no accumulated depreciation, so the line was never computable; and what it fell
        # back to lost the parts printed beside it. Each face line is a separate figure, so:
        #
        #   * the line is not printed, but parts are (1966 prints its fixed assets only by class):
        #     the line is the sum of the parts disclosed;
        #   * the line is printed AND parts are printed as lines of their own (a mainland 固定资产,
        #     with 在建工程 and 使用权资产 below it): the line is the printed figure plus those
        #     parts — unless they already add up to it, in which case the printed figure stands.
        #
        # MEASURED: 000709 non-current assets 175.5bn against a printed 203.7bn, short by exactly
        # 在建工程 + 使用权资产 (28.25bn); 688008, 300319 and 1966 short by their gross figure too.
        if (not calc.computable and total is not None and reported_total is None
                and nodes[key].get("role") == "line" and not reported_total_key):
            own = reported(key)
            if own is None:
                calc.value, calc.computable = total, True
            elif abs(float(own) - float(total)) > _PARTS_TOLERANCE:
                calc.value, calc.computable = float(own) + float(total), True
                calc.components.insert(0, Component(canonical_key=key,
                                                    label=names.get(key, key) + " (printed)",
                                                    value=float(own)))
        out[key] = calc
    for key in cyclic:
        if key in out:
            out[key].cycle = True
    return out
