"""A SUBTOTAL'S FORMULA IS DECLARED IN THE CONFIGURATION, and agrees with the template's.

NEW FILE -> backend/tests/test_formula_in_config.py

WHAT CHANGED. The 33 `calculated` lines used to name no `terms` at all: their components lived only
in the target template's `rollup: {op: "sum", children: [...]}`, and `services/line_items.evaluate`
deliberately fell through — "no terms, so report what the document supplied and let the template
compare it against the components". The argument was that two copies of one arithmetic would drift.

The requirement is that a figure's derivation be declared where the rest of the line is declared, so
31 of the 33 now carry `terms`, generated from that same rollup. The drift argument does not go
away by being overruled — it is what this file is for.

THE TWO THAT DO NOT MIGRATE, and it is a limit of `Term` rather than an oversight.
`bs_ca__inventories` and `bs_equity__retained_profits` declare `reported_total_key` pointing at
THEMSELVES with `reported_total_op: diff`: the line's figure is its own PRINTED total less the
components the filing broke out. `Term.ref` names ANOTHER line, and a line naming its own reported
figure is a cycle, so there is nothing to write. They keep the fall-through.

NO PUBLISHED FIGURE MOVED. `services/rollups.evaluate` — the one entry point for the statement API,
the Excel export and the KPI layer — still reads the TEMPLATE's rollup. The config-side evaluator
had no live caller on a calculated line (`stages/note_sourced` calls it for DERIVED parents only),
so populating `terms` added a declaration without changing an output. That is also why this test
matters more than it looks: the two copies are not yet reconciled by anything at runtime.
"""
from __future__ import annotations

import json
import pathlib

from app.schemas.line_items import load_line_item_set

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"
TEMPLATE = TEMPLATES / "output_csv_hk_v1_template.json"

# The two whose rollup subtracts from their own reported total.
SELF_REFERENCING = {"bs_ca__inventories", "bs_equity__retained_profits"}


def _template_rollups() -> dict[str, dict]:
    """canonical_key -> rollup, for every template node that declares one."""
    out: dict[str, dict] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("rollup"):
                key = node.get("key") or node.get("canonical_key")
                if key:
                    out[key] = node["rollup"]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    return out


def _resolved():
    return {d.key: d for d in load_line_item_set(json.loads(SEED.read_text(encoding="utf-8"))).items}


def test_every_calculated_line_declares_its_formula_or_says_why_not() -> None:
    lines = _resolved()
    calculated = [k for k, d in lines.items()
                  if str(getattr(d.type, "value", d.type)) == "calculated"]

    assert len(calculated) == 33, len(calculated)
    without = {k for k in calculated if not lines[k].terms}
    assert without == SELF_REFERENCING, (
        f"a calculated line with no formula that is not one of the two known cases: "
        f"{sorted(without - SELF_REFERENCING)}")


def test_the_configured_formula_is_the_templates_formula() -> None:
    """THE DRIFT CHECK. Two declarations of one arithmetic is what the old comment warned about;
    this is the assertion that makes the second copy safe to have.

    Term for term and in order, because a sum is order-insensitive but a reader comparing the two
    is not — and `evaluate` walks them in order to build the trail an analyst reads.
    """
    lines, rollups = _resolved(), _template_rollups()
    for key, item in lines.items():
        if not item.terms or str(getattr(item.type, "value", item.type)) != "calculated":
            continue
        children = list((rollups.get(key) or {}).get("children") or [])
        assert [t.ref for t in item.terms] == children, key
        # `cost_magnitude_children` is the template's way of saying a child arrives as a MAGNITUDE
        # rather than a signed amount; `abs` is `Term`'s. The two must name the same children, or
        # the export path and the configuration would publish different figures for one line.
        magnitude = set((rollups.get(key) or {}).get("cost_magnitude_children") or ())
        assert {t.ref for t in item.terms if t.abs} == magnitude, key
        # AND THE SIGN IS DECIDED BY THAT, term for term.
        #
        # `rollups.Component.contribution` returns `-abs(value) * sign` for a magnitude member and
        # the caller applies `sign` again, so in a `sum` a magnitude child contributes exactly
        # `-abs(value)`. `services/line_items._apply_terms` spells the same arithmetic as
        # `sign * abs(raw)`, which needs `sign: -1` to agree. A magnitude term written `sign: 1`
        # would ADD the charge where the template subtracts it — the drift this pins.
        #
        # Everything else adds, because these formulas are generated from a `sum`. A bare sign flip
        # is a real change to the arithmetic and has to be authored on both sides deliberately
        # rather than arrive by regeneration.
        assert {t.ref: t.sign for t in item.terms} == {
            t.ref: (-1 if t.ref in magnitude else 1) for t in item.terms}, key


def test_every_term_names_a_line_that_exists() -> None:
    """A formula referring to nothing evaluates to nothing, silently — the registry would report an
    unresolvable dependency, and a dangling ref is the way a regeneration goes wrong."""
    lines = _resolved()
    dangling = {k: [t.ref for t in d.terms if t.ref and t.ref not in lines]
                for k, d in lines.items() if d.terms}
    assert not {k: v for k, v in dangling.items() if v}, \
        {k: v for k, v in dangling.items() if v}


def test_no_residual_bucket_was_turned_into_a_sum() -> None:
    """THE MIGRATION'S ONE REAL HAZARD. 32 template rollups are NOT calculated lines and all 32
    declare `reported_total_key` — they are residuals, computed as a reported total less what was
    broken out of it. Eleven of them are the `exclusive_residual` buckets. Writing `terms` for one
    would turn "the unexplained remainder" into "the sum of the parts", which is a different figure
    and a plausible-looking one.
    """
    lines, rollups = _resolved(), _template_rollups()
    residual = [k for k, d in lines.items()
                if str(getattr(d.value_scope, "value", d.value_scope)) == "exclusive_residual"]

    assert len(residual) == 11, len(residual)
    assert all(k in rollups for k in residual), "a residual with no template rollup to sweep it"
    assert not [k for k in residual if lines[k].terms], (
        [k for k in residual if lines[k].terms])
