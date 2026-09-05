"""Structural validation (template rollups + declared identities) catches mis-mapped values
arithmetically, stays silent when a relation's components weren't all extracted, and routes its
failures to the review queue."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.loader import load_template
from app.schemas.template import Rollup
from app.services.structural_checks import evaluate_structure


def _node(node_id: str, role: str = "line", rollup: list[str] | None = None,
          op: str = "sum") -> dict:
    n = {"node_id": node_id, "canonical_key": node_id, "label": node_id, "role": role}
    if rollup is not None:
        n["rollup"] = {"op": op, "children": rollup}
    return n


def _pl_template(op: str = "sum", identities: list[dict] | None = None) -> dict:
    """Revenue + cost of sales → gross profit, as a template a filing could be spread onto."""
    return {
        "template_key": "t", "name": "t",
        "statements": [{
            "type": "profit_and_loss",
            "sections": [{
                "node_id": "s1", "canonical_key": "s1", "label": "Income", "role": "header",
                "children": [
                    _node("revenue"), _node("cost_of_sales"), _node("other_income"),
                    _node("gross_profit", "subtotal", ["revenue", "cost_of_sales"], op),
                ],
            }],
            "identities": identities or [],
        }],
    }


def _items(**by_key: object) -> list[LineItem]:
    """One line item per canonical key; a value may be a number or {period: number}."""
    out = []
    for key, val in by_key.items():
        li = LineItem(source_label=key, canonical_key=key)
        periods = val if isinstance(val, dict) else {"current": val}
        for period, num in periods.items():
            li.set_value(ExtractedValue(value=Decimal(str(num)), value_raw=Decimal(str(num)),
                                        basis=Basis.CONSOLIDATED, period_label=period))
        out.append(li)
    return out


def _one(report, rule_id: str, status: str | None = None):
    return next(r for r in report.results
                if r.rule_id == rule_id and (status is None or r.status == status))


def test_subtotal_that_adds_up_passes():
    tpl = load_template(_pl_template())
    report = evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600, gross_profit=400))

    res = _one(report, "rollup:gross_profit")
    assert res.status == "pass" and res.difference == 0
    assert res.expected == 400 and res.actual == 400
    assert res.scope_key == "consolidated/current"
    assert report.failed_assertions == []


def test_subtotal_off_by_more_than_tolerance_fails_with_the_arithmetic():
    """A value mapped to the wrong concept shows up as a subtotal that doesn't add up — the
    finding carries the relation, both figures, the difference, the period and the keys."""
    tpl = load_template(_pl_template())
    report = evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600, gross_profit=470))

    res = _one(report, "rollup:gross_profit")
    assert res.status == "fail" and res.kind == "rollup"
    assert (res.expected, res.actual, res.difference) == (400, 470, 70)
    assert res.details["target"] == "gross_profit"
    assert res.details["components"] == ["revenue", "cost_of_sales"]
    assert res.details["basis"] == "consolidated" and res.details["period_label"] == "current"
    assert res.details["component_values"] == {"revenue": "1000", "cost_of_sales": "-600"}
    assert report.failed_assertions and "gross_profit" in report.failed_assertions[0]


def test_missing_child_is_not_evaluable_rather_than_a_mismatch():
    """The template lists every line the framework allows, so an unextracted child must never
    read as a mismatch — that would fail nearly every subtotal on a partial extraction."""
    tpl = load_template(_pl_template())
    report = evaluate_structure(tpl, _items(revenue=1000, gross_profit=400))

    assert report.failures() == []
    res = _one(report, "rollup:gross_profit")
    assert res.status == "skipped"
    assert res.details["reason"] == "components_not_mapped"
    assert res.details["missing"] == ["cost_of_sales"]
    # No value is invented to close the gap.
    assert res.expected is None and res.actual is None


def test_total_or_components_absent_entirely_is_reported_as_coverage_not_failure():
    tpl = load_template(_pl_template())
    report = evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600))

    res = _one(report, "rollup:gross_profit")
    assert res.status == "skipped" and res.details["reason"] == "target_not_extracted"
    assert not report.evaluated()


def test_expenses_stored_negative_or_positive_both_read_correctly():
    """Sums are signed: a cost printed in parentheses arrives negative and simply adds. The same
    figure stored positive breaks the relation, and the sign is named as the suspect."""
    tpl = load_template(_pl_template())

    signed = evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600, gross_profit=400))
    assert _one(signed, "rollup:gross_profit").status == "pass"

    flipped = evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=600, gross_profit=400))
    res = _one(flipped, "rollup:gross_profit")
    assert res.status == "fail" and res.difference == -1200
    assert res.details["sign_suspect"] == "cost_of_sales"


def test_sign_suspect_is_only_named_when_it_is_unambiguous():
    tpl = load_template(_pl_template())
    # Two equal-magnitude components: either flip would balance it, so neither is accused.
    report = evaluate_structure(tpl, _items(revenue=500, cost_of_sales=500, gross_profit=0))
    res = _one(report, "rollup:gross_profit")
    assert res.status == "fail" and res.details["sign_suspect"] is None


def test_tolerance_is_absolute_and_relative():
    tpl = load_template(_pl_template())

    # Absolute floor of 1 for small figures: exactly on the edge passes, a hair over fails.
    assert _one(evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600,
                                               gross_profit=401)),
                "rollup:gross_profit").status == "pass"
    assert _one(evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600,
                                               gross_profit="401.01")),
                "rollup:gross_profit").status == "fail"
    # Relative band (0.1%) on large figures — rounding in the filing isn't a defect.
    big = _items(revenue=20_000_000, cost_of_sales=-18_000_000, gross_profit=2_001_000)
    assert _one(evaluate_structure(tpl, big), "rollup:gross_profit").status == "pass"
    worse = _items(revenue=20_000_000, cost_of_sales=-18_000_000, gross_profit=2_010_000)
    assert _one(evaluate_structure(tpl, worse), "rollup:gross_profit").status == "fail"


def test_each_period_is_checked_on_its_own():
    tpl = load_template(_pl_template())
    report = evaluate_structure(tpl, _items(
        revenue={"current": 1000, "prior": 900},
        cost_of_sales={"current": -600, "prior": -500},
        gross_profit={"current": 400, "prior": 300}))

    results = {r.scope_key: r.status for r in report.evaluated()}
    assert results == {"consolidated/current": "pass", "consolidated/prior": "fail"}


def test_declared_identity_is_evaluated_from_the_template():
    """Identities come from the template's own ``identities``, with their own tolerance."""
    tpl = load_template(_pl_template(identities=[{
        "id": "gp_ties", "lhs": "gross_profit",
        "rhs": {"op": "diff", "children": ["revenue", "other_income"]},
        "tolerance_abs": 5.0, "tolerance_rel": 0.0,
    }]))
    report = evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600, gross_profit=996,
                                            other_income=6))

    res = _one(report, "identity:gp_ties")
    assert res.kind == "identity" and res.status == "pass"    # 1000 − 6 = 994, within 5
    assert res.expected == 994 and res.actual == 996


def test_two_rows_on_one_concept_are_summed_and_a_wrong_pairing_then_fails():
    """Several printed lines legitimately share one concept — three depreciation lines, two tax
    payments, anything a section's residual bucket absorbs — so repeated mappings add, exactly as
    the statement view and the Excel export present them.

    That also detects MORE than refusing to evaluate did. Here the second cost row does not
    belong on the concept, and the arithmetic says so: 1000 + (-600 - 900) is -500, not the 400
    reported. Skipping the relation as "ambiguous" reported nothing at all.
    """
    tpl = load_template(_pl_template())
    items = _items(revenue=1000, cost_of_sales=-600, gross_profit=400)
    items += _items(cost_of_sales=-900)

    res = _one(evaluate_structure(tpl, items), "rollup:gross_profit")
    assert res.status == "fail"
    assert res.expected == -500 and res.actual == 400
    assert res.details["component_values"]["cost_of_sales"] == "-1500"


def test_two_rows_that_do_belong_together_tie():
    """The same summing, when the two lines genuinely are one concept."""
    tpl = load_template(_pl_template())
    items = _items(revenue=1000, cost_of_sales=-600, gross_profit=-500)
    items += _items(cost_of_sales=-900)

    assert _one(evaluate_structure(tpl, items), "rollup:gross_profit").status == "pass"


def test_a_weighted_rollup_is_refused_at_the_upload_gate():
    """``weighted_sum`` was permitted by the schema and declared no weights anywhere, so the only
    thing it could produce was a relation reported "not evaluable" forever — the subtotal it was
    authored on lost its arithmetic guard, and the report read like coverage rather than the
    authoring mistake it was. The op is gone from the schema, so it now fails at the door, where
    there is a request to fail and an author to tell."""
    with pytest.raises(ValidationError) as raised:
        load_template(_pl_template(op="weighted_sum"))
    assert "rollup.op" in str(raised.value)


def test_an_op_this_module_cannot_evaluate_is_never_treated_as_a_plain_sum():
    """A definition stored before the schema tightened can still reach evaluation (nothing
    re-validates the rows already in the database). Guessing "sum" would report the subtotal as
    tying against arithmetic nobody declared."""
    tpl = load_template(_pl_template())
    node = next(n for n in tpl.all_nodes() if n.canonical_key == "gross_profit")
    node.rollup = Rollup.model_construct(op="weighted_sum", children=["revenue", "cost_of_sales"])

    report = evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600, gross_profit=400))
    res = _one(report, "rollup:gross_profit")
    assert res.status == "skipped" and res.details["reason"] == "unsupported_op"


def test_mixed_scales_are_not_compared():
    """Values are not unit-normalized, so a relation across two scales is not evaluable."""
    tpl = load_template(_pl_template())
    items = _items(revenue=1000, cost_of_sales=-600, gross_profit=400)
    thousands = next(li for li in items if li.canonical_key == "cost_of_sales")
    next(iter(thousands.values.values())).unit_ctx.scale_factor = Decimal(1000)

    report = evaluate_structure(tpl, items)
    res = _one(report, "rollup:gross_profit")
    assert res.status == "skipped" and res.details["reason"] == "mixed_scale"


def test_shipped_template_relations_hold_on_a_consistent_spread():
    """The real HKFRS template's own rollups, exercised end-to-end on a small consistent set."""
    import json
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "hkfrs_hk_china_template.json")
    tpl = load_template(json.loads(path.read_text(encoding="utf-8")))
    report = evaluate_structure(tpl, _items(**{
        "cf_cash_flow_from_operating_activities__net_cash_from_operating_activities": 5_094_092,
        "cf_cash_flow_from_investing_activities__net_cash_used_in_investing_activities": 4_044_304,
        "cf_cash_flow_from_financing_activities__net_cash_from_financing_activities": -13_389_527,
        "cf_net_increase_decrease_in_cash_and_cash_equivalents": -4_251_131,
        "cf_opening_cash_and_cash_equivalents": 8_156_453,
        # IAS 7: closing cash is opening plus the net change PLUS the effect of exchange rate
        # movements. While the rollup omitted that third term the relation could not hold on
        # any filing that reports one — it showed up as a phantom mismatch equal to the
        # exchange effect.
        "cf_effect_of_foreign_exchange_rate_changes": 26_703,
        "cf_closing_cash_and_cash_equivalents": 3_932_025,
    }))
    passed = {r.rule_id for r in report.results if r.status == "pass"}
    assert "rollup:cf_net_increase_decrease_in_cash_and_cash_equivalents" in passed
    assert "rollup:cf_closing_cash_and_cash_equivalents" in passed
    assert report.failures() == []
    # Everything the extraction didn't reach is accounted for as not-evaluable, never as a pass.
    assert report.skipped()
    assert len(report.results) == len(report.evaluated()) + len(report.skipped())


def test_a_statement_the_filing_does_not_contain_is_marked_apart_from_a_thin_one():
    """A standalone-only filing has no cash flow statement, and a filing spread onto the cash flow
    alone has no balance sheet. Those relations are still reported — silence is indistinguishable
    from a pass — but with their own reason, because they must not sit in the denominator of a
    coverage rate the way an unextracted row does."""
    import json
    from pathlib import Path

    path = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "hkfrs_hk_china_template.json")
    tpl = load_template(json.loads(path.read_text(encoding="utf-8")))
    report = evaluate_structure(tpl, _items(**{
        "cf_opening_cash_and_cash_equivalents": 8_156_453,
        "cf_closing_cash_and_cash_equivalents": 3_932_025,
    }))

    reasons = {r.details["statement"]: {x.details["reason"] for x in report.results
                                       if x.details["statement"] == r.details["statement"]}
               for r in report.results}
    assert reasons["balance_sheet"] == {"statement_absent"}
    assert reasons["profit_and_loss"] == {"statement_absent"}
    assert "statement_absent" not in reasons["cash_flow"]


def test_stage_flags_the_involved_items_and_records_the_report():
    from app.core.models.document import DocumentModel
    from app.core.stage import PipelineContext
    from app.stages.structural import FLAG, StructuralStage

    doc = DocumentModel(filename="x.pdf")
    doc.line_items = _items(revenue=1000, cost_of_sales=-600, gross_profit=470, other_income=50)
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template = load_template(_pl_template())

    StructuralStage().run(doc, ctx)

    assert doc.structural is not None and len(doc.structural.failures()) == 1
    involved = {li.canonical_key for li in doc.line_items if FLAG in li.confidence.flags}
    assert involved == {"gross_profit", "revenue", "cost_of_sales"}
    # Flagged per value too, and a failed relation can only lower the confidence signal.
    gp = next(li for li in doc.line_items if li.canonical_key == "gross_profit")
    ev = next(iter(gp.values.values()))
    assert FLAG in ev.confidence.flags and ev.confidence.validation == 0.5
    assert ev.confidence.overall < 1.0
    # A line item outside the relation is untouched.
    other = next(li for li in doc.line_items if li.canonical_key == "other_income")
    assert other.confidence.flags == []
    assert any(log.startswith("structural:evaluated=1 failed=1") for log in ctx.logs)


def test_stage_is_silent_without_a_template():
    from app.core.models.document import DocumentModel
    from app.core.stage import PipelineContext
    from app.stages.structural import StructuralStage

    doc = DocumentModel(filename="x.pdf")
    doc.line_items = _items(revenue=1000)
    ctx = PipelineContext(raw_bytes=b"")
    StructuralStage().run(doc, ctx)

    assert doc.structural is None
    assert any("structural:skipped" in log for log in ctx.logs)


def test_structural_failures_reach_the_review_queue():
    from app.api.routes.documents import _build_review

    tpl = load_template(_pl_template())
    report = evaluate_structure(tpl, _items(revenue=1000, cost_of_sales=-600, gross_profit=470))
    structural = [r.model_dump(mode="json") for r in report.results]

    review = _build_review([], "doc.pdf", "en", [], structural)
    check = next(c for c in review["checks"] if c["type"] == "structural")
    assert check["target"] == "gross_profit" and check["delta"] == "70"
    assert any(t["label"] == "Checks" and t["count"] == 1 for t in review["tabs"])
    # Relations that were only skipped raise nothing.
    quiet = evaluate_structure(tpl, _items(revenue=1000, gross_profit=400))
    assert _build_review([], "doc.pdf", "en", [],
                         [r.model_dump(mode="json") for r in quiet.results])["checks"] == []


def test_run_carries_the_structural_report_end_to_end(client):
    """A real run through the API attaches the run's template and stores the structural result —
    including the relations it could not evaluate, so coverage is auditable."""
    import time

    from tests.fixtures.generate import make_native_pdf

    doc_id = client.post("/api/v1/documents",
                         files={"file": ("bs.pdf", make_native_pdf(),
                                         "application/pdf")}).json()["id"]
    onts = client.get("/api/v1/ontologies").json()
    ont = next((o for o in onts if o["ontology_key"] == "hkfrs_hk_china"), onts[0])
    tpls = client.get("/api/v1/templates").json()
    tpl = next((t for t in tpls if t["template_key"] == ont["target_template_key"]), tpls[0])
    client.post(f"/api/v1/documents/{doc_id}/extractions",
                json={"ontology_version_id": ont["id"], "template_version_id": tpl["id"]})
    for _ in range(100):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)

    structural = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["structural"]
    assert structural, "the run should record the template relations it considered"
    assert {r["status"] for r in structural} <= {"pass", "fail", "skipped"}
    # This fixture spreads onto a handful of template lines, so almost nothing is complete. What
    # matters is that a relation is only EVALUATED where the filing supports it, and that a skip
    # always carries the reason it could not run — silence and a default pass are the two outcomes
    # that would let a run reporting nothing read as a clean one.
    assert all(r["details"]["reason"] for r in structural if r["status"] == "skipped")
    # And a relation that DID evaluate did so on a real spread, not on a section taken as nil
    # throughout: `_nil_when_absent` treats a leaf of a residual-owning namespace as nil, which is
    # what makes a partially stated section checkable at all — but never every leaf of it, or the
    # relation would be asserting 0 == 0.
    for r in structural:
        if r["status"] not in ("pass", "fail"):
            continue
        details = r["details"]
        # `assumed_zero` is only attached by the evaluating path, which is the point: a row without
        # it is one of the section reconciliations, not a relation this rule could have widened.
        if "assumed_zero" not in details:
            continue
        assert len(details["assumed_zero"]) < len(details["components"]), r["rule_id"]


def test_balance_identity_is_not_reported_twice():
    """The balance-sheet identity already has its own review check; the structural restatement
    of the same difference is suppressed so the analyst sees one item, not two."""
    from app.api.routes.documents import _accounting_checks

    rows = [{"canonical_key": k,
             "values": [{"basis": "consolidated", "period_label": "current", "value": str(v)}]}
            for k, v in (("bs_total_assets", 100), ("bs_total_equity_and_liabilities", 90))]
    structural = [{"rule_id": "identity:bs_balances", "kind": "identity", "status": "fail",
                   "scope_key": "consolidated/current", "expected": "90", "actual": "100",
                   "difference": "10",
                   # basis/period_label as services/structural_checks.py:716 writes them for every
                   # relation. They were absent here, and suppression compares the COLUMN now (a
                   # consolidated card must not delete a standalone break), so a fixture missing
                   # them describes a relation the evaluator never produces.
                   "details": {"target": "bs_total_assets", "basis": "consolidated",
                               "period_label": "current",
                               "components": ["bs_total_equity_and_liabilities"],
                               "op": "sum", "component_values": {}}}]
    types = [c["type"] for c in _accounting_checks(rows, [], "en", structural)]
    assert types == ["balance"]


def _shipped_template():
    import json
    from pathlib import Path
    path = (Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
            / "hkfrs_hk_china_template.json")
    return load_template(json.loads(path.read_text(encoding="utf-8")))


def test_a_leaf_the_filing_does_not_print_is_nil_where_the_section_sweeps_it():
    """A subtotal split into tiers must not lose its arithmetic check to a line nobody prints.

    THE DEFECT THIS CLOSES, measured on the shipped template: gross profit is struck over THREE
    leaves — revenue, cost of goods sold and purchases of stock-in-trade. Almost no filing prints
    the last one. A missing component used to make the whole relation `skipped` unless the rollup's
    own child list contained the section residual, so ``rollup:pl_gross_profit`` — the most-read
    check on an income statement — would have been not-evaluable on essentially every filing. That
    is the failure mode this module's own docstring calls out: a relation occupying the slot where a
    reviewer expects assurance while proving nothing.

    ``_nil_when_absent`` closes it: the expenses namespace owns an ``__others`` bucket that sweeps
    every printed row it places there, so a leaf of that namespace which is STILL absent is one the
    filing does not print. The pass records it in ``assumed_zero`` so the reader can see the relation
    was checked against a section the filing states partially.
    """
    tpl = _shipped_template()
    report = evaluate_structure(tpl, _items(**{
        "pl_income__revenue_from_operations": 1000,
        "pl_expenses__cost_of_goods_sold": -600,        # the printed cost line, no purchases line
        "pl_gross_profit": 400,
    }))
    res = _one(report, "rollup:pl_gross_profit")
    assert res.status == "pass", res.details
    assert res.details["assumed_zero"] == ["pl_expenses__purchases_of_stock_in_trade"]


def test_a_calculated_component_the_filing_does_not_print_is_never_taken_as_nil():
    """The other half, and the one that would FAIL a correct filing rather than skip it.

    ``pl_operating_profit_ebit`` is struck over ``pl_expenses__total_operating_cost``, a calculated
    subtotal most filings do not print — and one that lives in the ``pl_expenses__`` namespace the
    residual sweeps, so the rule above would reach it if being calculated did not exclude it first.
    Taking it as nil would compare the printed operating profit against total income ALONE — a break
    equal to the entire operating cost, raised as a blocking finding, on a filing where nothing is
    wrong. So it stays a skip, and the tie is covered instead by the rulebook identity over the
    leaves.
    """
    tpl = _shipped_template()
    report = evaluate_structure(tpl, _items(**{
        "pl_income__total_income": 1000,
        "pl_operating_profit_ebit": 400,
    }))
    res = _one(report, "rollup:pl_operating_profit_ebit")
    assert res.status == "skipped"
    assert res.details["missing"] == ["pl_expenses__total_operating_cost"]


def test_the_cash_flow_starting_line_the_filing_did_not_choose_is_nil():
    """Two mutually exclusive starting lines, one rollup, and a check that still runs.

    HKAS 7 lets an indirect-method cash flow start from profit before tax or from profit for the
    year. The spec writes the new operating subtotal as ``SUM(COALESCE(pbt, pfy) + adjustments)``;
    with a missing child skipped by ``rollups.evaluate`` the plain SUM over both IS that coalesce,
    and ``cf_starting_point`` in the rulebook reports a filing that populates both. What is left is
    the check: exactly one starting line is absent on EVERY filing by construction, so without
    ``_nil_when_absent`` this subtotal's arithmetic could never be verified once — not on a thin
    extraction, on any extraction at all.
    """
    tpl = _shipped_template()
    op = "cf_cash_flow_from_operating_activities"
    report = evaluate_structure(tpl, _items(**{
        f"{op}__profit_before_tax": 1000,
        f"{op}__income_tax_expense": 200,
        f"{op}__operating_profit_before_working_capital_changes": 1200,
    }))
    res = _one(report, f"rollup:{op}__operating_profit_before_working_capital_changes")
    assert res.status == "pass", res.details
    assert f"{op}__profit_for_the_year" in res.details["assumed_zero"]
