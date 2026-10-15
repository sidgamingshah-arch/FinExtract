"""Safe spreadsheet-style formula engine.

Edits can be entered as formulas (Req 10). This evaluates a formula to a number **without
``eval``**: it parses to a whitelisted AST (numbers, the arithmetic operators + - * / and
unary minus, parentheses, the functions SUM/AVG/MIN/MAX/ABS/ROUND, and *references*) and
walks it with a caller-supplied resolver that maps a reference to a value.

References are canonical line-item keys (e.g. ``bs_current_assets__inventories``) or bare
cell-like names the caller resolves; an unknown reference raises ``FormulaError`` rather
than silently evaluating to zero, so a broken formula surfaces instead of corrupting a value.
"""
from __future__ import annotations

import ast
from typing import Callable

_FUNCS: dict[str, Callable] = {
    "SUM": lambda *a: sum(a),
    "AVG": lambda *a: (sum(a) / len(a)) if a else 0.0,
    "MIN": min,
    "MAX": max,
    "ABS": abs,
    "ROUND": lambda *a: _round(*a),
}


def _round(value: float, digits: float = 0) -> float:
    """ROUND(x) and ROUND(x, n). Every literal reaches here as a float — `_eval` returns float so
    arithmetic is uniform — and Python's `round` refuses a float for its DIGITS argument, so the
    documented two-argument form always raised. The digits must be a whole number; a fractional
    one is refused rather than truncated, because ROUND(x, 1.5) names no rounding anyone wrote."""
    if float(digits) != int(digits):
        raise ValueError("ROUND digits must be a whole number")
    return round(value, int(digits))


class FormulaError(ValueError):
    """A formula could not be parsed or evaluated (bad syntax, unknown ref, or a bad op)."""


def evaluate(formula: str, resolver: Callable[[str], float]) -> float:
    """Evaluate ``formula`` (a leading '=' is optional). ``resolver(name)`` returns the value
    of a reference or raises KeyError/ValueError for an unknown one. Raises FormulaError on
    anything outside the whitelist."""
    expr = (formula or "").strip()
    if expr.startswith("="):
        expr = expr[1:].strip()
    if not expr:
        raise FormulaError("empty formula")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"could not parse formula: {exc}") from exc
    return float(_eval(tree.body, resolver))


def _eval(node: ast.AST, resolver: Callable[[str], float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise FormulaError("only numeric literals are allowed")
        return float(node.value)
    if isinstance(node, ast.Name):
        try:
            return float(resolver(node.id))
        except (KeyError, ValueError, TypeError) as exc:
            raise FormulaError(f"unknown reference: {node.id}") from exc
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, resolver), _eval(node.right, resolver)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise FormulaError("division by zero")
            return left / right
        raise FormulaError(f"operator not allowed: {type(node.op).__name__}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand, resolver)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id.upper() not in _FUNCS:
            raise FormulaError("only SUM/AVG/MIN/MAX/ABS/ROUND are allowed")
        if node.keywords:
            raise FormulaError("keyword arguments are not allowed")
        args = [_eval(a, resolver) for a in node.args]
        try:
            return float(_FUNCS[node.func.id.upper()](*args))
        except (TypeError, ValueError) as exc:
            raise FormulaError(f"bad arguments to {node.func.id}") from exc
    raise FormulaError(f"expression not allowed: {type(node).__name__}")


def references(formula: str) -> set[str]:
    """The names a formula refers to (a function name is not a reference). Empty for a formula
    that does not parse."""
    expr = (formula or "").strip().lstrip("=").strip()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in called}


def stored_formulas(row: dict) -> dict[str, str]:
    """``{"basis/period": formula}`` for the slots an analyst drove by a formula.

    An edit records its formula against the slot it wrote (``formulas``). A row edited before that
    record existed has one ``formula`` for the whole row, which can be placed only when a single
    slot was edited; otherwise the slot it drove is unknown and its figure stays as typed.
    """
    got = row.get("formulas")
    if isinstance(got, dict):
        return {str(k): str(v) for k, v in got.items() if v}
    slots = row.get("edited_slots") or []
    if row.get("formula") and row.get("edited") and len(slots) == 1:
        return {str(slots[0]): str(row["formula"])}
    return {}


def figure_text(value: float) -> str:
    """A computed figure as an edit stores it: whole numbers without a decimal point."""
    v = float(value)
    return str(int(v)) if v == int(v) else str(v)


def reevaluate(rows: list[dict]) -> list[dict]:
    """The rows with every stored formula evaluated again against the figures as they now stand.

    An edit evaluates its formula once, when it is typed, and stores the result; a line it
    references can change afterwards (an edit, a revert, a remap). Read through here, the formula's
    line shows what the formula gives today. References resolve as the edit route resolves them —
    the concept's figure in the same basis and period, its own edit first (``periods.concept_value``)
    — and a formula that reads another formula's line is evaluated after it.

    The stored run is never written: a row whose figure is recomputed is served as a copy. A
    formula that cannot be evaluated now (a line it names has no figure any more), or that reads
    itself through other formulas, keeps the figure last stored, and the copy says why in
    ``formula_errors`` — the evaluator's own message ("unknown reference: …", "circular
    reference"), as the edit route reports a formula it refuses.
    """
    from app.services.periods import concept_value, slot_for

    driven = [(i, slot, f) for i, r in enumerate(rows) for slot, f in stored_formulas(r).items()]
    if not driven:
        return rows
    out = list(rows)
    copied: set[int] = set()

    def own(i: int) -> dict:
        if i not in copied:
            r = dict(out[i])
            r["values"] = [dict(v) for v in r.get("values") or []]
            out[i] = r
            copied.add(i)
        return out[i]

    # Evaluation order: a formula after every formula whose line it reads in the same slot. What
    # is left when nothing more can be ordered reads its own line, and keeps its stored figure.
    def node(d: tuple[int, str, str]) -> tuple[str, str]:
        return str(rows[d[0]].get("canonical_key") or ""), d[1]

    pending = list(driven)
    order: list[tuple[int, str, str]] = []
    while pending:
        waiting = {node(d) for d in pending}
        ready = [d for d in pending if not {(ref, d[1]) for ref in references(d[2])} & waiting]
        if not ready:
            break
        order += ready
        pending = [d for d in pending if d not in ready]

    for i, slot_id, f in order:
        basis, _, period = slot_id.partition("/")

        def resolve(name: str, basis: str = basis, period: str = period) -> float:
            n = concept_value([r for r in out if r.get("canonical_key") == name], basis, period)
            if n is None:
                raise KeyError(name)
            return n

        try:
            text = figure_text(evaluate(f, resolve))
        except FormulaError as exc:
            row = own(i)
            row["formula_errors"] = {**(row.get("formula_errors") or {}), slot_id: str(exc)}
            continue
        slot = slot_for(out[i], basis, period)
        if slot is not None and str(slot.get("value")) != text:
            row = own(i)
            slot = slot_for(row, basis, period)
            slot["value"] = text
    for i, slot_id, _f in pending:
        row = own(i)
        row["formula_errors"] = {**(row.get("formula_errors") or {}),
                                 slot_id: "circular reference"}
    return out
