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
