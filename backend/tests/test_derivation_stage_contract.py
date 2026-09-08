"""Every derivation stage must build its path policy before it publishes a figure.

THE CRASH THIS CLOSES. `stages/related_party_receivables` passed `policy` into its `_apply` and
never assigned it. The import was there, the parameter was there, and the four sibling stages all
build it the same way — this one simply did not, so the call raised

    NameError: name 'policy' is not defined

DORMANT UNTIL IT WASN'T. The line is only reached once a value is actually COMPUTED: every
(basis, period) whose result is None hits a `continue` above it. So the whole test suite, and
every filing where that derivation found nothing, passed straight over it. On the 四创电子 filing
the first two keys reported NOT_COMPUTABLE, a later one computed, and a 210-page extraction died
at stage 15 of 21 having produced no rows at all.

WHY THIS IS A CONTRACT TEST rather than one more case in that stage's own suite: the five
derivation stages share a shape — build the policy, honour `policy.runs(...)`, hand the policy to
the writer — and the defect was a stage that had four fifths of it. Checking the shape across all
five catches the next one that drifts, which a test written against one stage cannot.
"""
from __future__ import annotations

import ast
import builtins
import inspect
import pathlib

import pytest

from app.stages import (contingent_liabilities, deprec_impairment, related_party_receivables,
                        sales_revenues, secur_fincl_assets)

# The five stages that compute a figure through `services.computed_paths`, with the service name
# each one declares to `policy.runs(...)`.
DERIVATIONS = {
    "secur_fincl_assets": secur_fincl_assets,
    "related_party_receivables": related_party_receivables,
    "deprec_impairment": deprec_impairment,
    "sales_revenues": sales_revenues,
    "contingent_liabilities": contingent_liabilities,
}


def _source(module) -> str:
    return pathlib.Path(inspect.getfile(module)).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(DERIVATIONS))
def test_the_stage_builds_its_policy(name):
    """The assignment whose absence was the bug."""
    assert "policy_from(ctx.settings)" in _source(DERIVATIONS[name]), (
        f"{name} never builds a policy, so any code path that uses one raises NameError")


@pytest.mark.parametrize("name", sorted(DERIVATIONS))
def test_the_stage_honours_the_switch(name):
    """`policy.runs(...)` is what makes the complex path switchable at all."""
    assert "policy.runs(" in _source(DERIVATIONS[name]), f"{name} cannot be switched off"


@pytest.mark.parametrize("name", sorted(DERIVATIONS))
def test_every_name_the_stage_uses_is_bound_before_use(name):
    """The general form of the defect: a name read in a function nothing assigns.

    Walks each function in the module and reports a local that is LOADED without ever being
    stored, bound as a parameter, or available as a global/builtin. That is precisely the shape of
    `policy` here — used, imported-adjacent, never assigned — and it is invisible to a test suite
    that does not reach the line.
    """
    module = DERIVATIONS[name]
    tree = ast.parse(_source(module))
    # `dir(__builtins__)` is wrong here: under pytest `__builtins__` is a DICT, so it
    # returns dict methods and every real builtin — str, int, getattr — reads as
    # unbound. The `builtins` module is the reliable source.
    module_names = (set(dir(module)) | set(dir(builtins))
                    | {"__file__", "__name__", "__doc__"})

    problems: list[str] = []
    for func in [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        bound = {a.arg for a in func.args.args} | {a.arg for a in func.args.kwonlyargs}
        if func.args.vararg:
            bound.add(func.args.vararg.arg)
        if func.args.kwarg:
            bound.add(func.args.kwarg.arg)
        for node in ast.walk(func):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not func:
                bound.add(node.name)                      # a nested def binds its own name
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.comprehension):
                for target in ast.walk(node.target):
                    if isinstance(target, ast.Name):
                        bound.add(target.id)
        for node in ast.walk(func):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in bound and node.id not in module_names:
                    problems.append(f"{func.name}() reads {node.id!r} at line {node.lineno} "
                                    f"and nothing binds it")

    assert not problems, f"{name}: " + "; ".join(sorted(set(problems))[:6])


def test_the_regression_itself_is_pinned():
    """Named explicitly, so the fix cannot be reverted quietly."""
    source = _source(related_party_receivables)

    assert "policy = policy_from(ctx.settings)" in source
    # And the guard, which is the reason the assignment exists rather than being tidied away.
    assert 'policy.runs("related_party_receivables")' in source
