"""The coverage contract, re-verified end to end rather than on hand-written rows.

:mod:`app.services.coverage` exists to stop "3 relations passed" reading as "the statement is
validated", and the only way to know it still does is to run a real filing through the worker and
hold the stored result to the contract:

* THREE buckets, always all three, and they total the rows — nothing quietly leaves the arithmetic;
* both rates or neither, and no third number anywhere: ``pass / (pass + fail)`` IS the validation
  rate, and offering it alone under any name is the collapse this module prevents;
* the skip taxonomy applied to every unrun row, with the absent-statement bucket the only one
  outside the denominator;
* the alarm states firing on the conditions they name — a statement that proved nothing, and a
  guard that promised to answer unconditionally and did not.

The real-run assertions are deliberately shape-based, not thresholds: extraction quality moves, and
a contract test that fails because the mapper got better is a test nobody trusts. The alarm states
are then forced deterministically against the same shipped template and rulebook, because an alarm
that has never been seen to fire is not evidence of anything.
"""
from __future__ import annotations

import copy
import json
import time
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.loader import load_ontology, load_template
from app.services import coverage as cov_mod
from app.services.coverage import coverage
from app.services.structural_checks import STATUS_AUTHORING_ERROR, evaluate_structure
from tests.fixtures.generate import make_native_pdf, make_unmapped_row_pdf

_SAMPLES = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


@pytest.fixture(scope="module")
def raw_ontology() -> dict:
    return json.loads((_SAMPLES / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def template():
    return load_template(json.loads((_SAMPLES / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))


def _ontology(raw: dict):
    return load_ontology(copy.deepcopy(raw), resolve=True)


def _facts(figures: dict, basis: Basis = Basis.CONSOLIDATED,
           period: str | None = "current") -> list[LineItem]:
    out = []
    for key, num in figures.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(str(num)), value_raw=Decimal(str(num)),
                                    basis=basis, period_label=period))
        out.append(li)
    return out


def _rows(report) -> list[dict]:
    return [r.model_dump(mode="json") for r in report.results]


def _extracted(client, *, template: bool = True, ontology: bool = True) -> str:
    """One filing through the worker, with the v2 rulebook selected unless told otherwise.

    ``template=False, ontology=True`` is the combination the API and the upload screen both allow and
    the one finding E was reproduced on: the run maps its lines against the rulebook, so the
    template-derived check builders have mapped concepts to work with, while the run names no
    template at all.
    """
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("bs.pdf", make_unmapped_row_pdf(),
                                         "application/pdf")}).json()["id"]
    options: dict = {}
    if template or ontology:
        ont = next(o for o in client.get("/api/v1/ontologies").json()
                   if o["ontology_key"] == "hkfrs_hk_china")
        tpl = next(t for t in client.get("/api/v1/templates").json()
                   if t["template_key"] == ont["target_template_key"])
        options = {}
        if ontology:
            options["ontology_version_id"] = ont["id"]
        if template:
            options["template_version_id"] = tpl["id"]
    client.post(f"/api/v1/documents/{doc_id}/extractions", json=options)
    for _ in range(200):
        if client.get(f"/api/v1/documents/{doc_id}/run").json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    assert client.get(f"/api/v1/documents/{doc_id}/run").json()["status"] == "succeeded"
    return doc_id


def _real_run(client) -> list[dict]:
    """One filing through the worker with the v2 rulebook selected, as the stored rows."""
    doc_id = _extracted(client)
    return client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["structural"]


def test_the_coverage_contract_holds_end_to_end_on_a_real_filing(client, template, raw_ontology):
    rows = _real_run(client)
    report = coverage(rows)
    agg = report.aggregate
    per_statement = [cov for _n, cov in sorted(report.statements.items())]

    # --- three buckets, always all three, and they account for every row ------------------------
    assert rows and agg.buckets.total == len(rows)
    assert (agg.buckets.passed + agg.buckets.failed + agg.buckets.skipped) == len(rows)
    assert sum(c.buckets.total for c in per_statement) == len(rows)
    # The run verified something and nowhere near everything, which is the situation the contract
    # is written for — a partial verification must never render as a pass.
    assert agg.evaluated > 0 and agg.buckets.skipped > 0
    assert agg.coverage_rate < 1.0 and agg.status != cov_mod.PASSED

    # --- both rates or neither, and no collapsed single score anywhere --------------------------
    for cov in [agg, *per_statement]:
        emitted = cov.as_dict()
        assert {"passed", "failed", "skipped", "evaluated", "declarable"} <= emitted.keys()
        assert ("validation_rate" in emitted) == ("coverage_rate" in emitted)
        # pass/(pass+fail) is the validation rate; under any other name it is the collapse.
        assert not {"pass_rate", "score", "rate", "success_rate", "health"} & emitted.keys()
        assert emitted["validation_rate"] == (
            None if cov.evaluated == 0 else round(cov.buckets.passed / cov.evaluated, 4))
        assert emitted["coverage_rate"] == (
            None if cov.declarable == 0 else round(cov.evaluated / cov.declarable, 4))
    # Both rates travel in the run log too, each with the fraction it was computed from, so a
    # reader cannot take one for the other.
    line = report.headline()
    assert f"({agg.buckets.passed}/{agg.evaluated})" in line
    assert f"({agg.evaluated}/{agg.declarable})" in line

    # --- the skip taxonomy, applied to every row that did not run ------------------------------
    unrun = [r for r in rows if r["status"] not in ("pass", "fail")]
    assert len(unrun) == agg.buckets.skipped
    assert all(r["details"].get("reason") in cov_mod.TAXONOMY for r in unrun)
    assert sum(agg.skips.values()) == agg.buckets.skipped
    assert cov_mod.UNCLASSIFIED not in agg.skips
    # Only the absent-statement rows leave the denominator; every other unrun row stays in it.
    assert agg.declarable == agg.evaluated + sum(
        n for bucket, n in agg.skips.items() if bucket not in cov_mod.NOT_DECLARABLE)
    for cov in per_statement:
        if cov.status == cov_mod.ABSENT:
            assert set(cov.skips) <= set(cov_mod.NOT_DECLARABLE)

    # --- a healthy run raises no defect alarm --------------------------------------------------
    # Every relation and guard the shipped rulebook declares resolves against the shipped
    # template, so nothing here is unrunnable and nothing blocking is unenforceable.
    assert not [a for a in report.alarms
                if a["code"] in (cov_mod.ALARM_PIPELINE_DEFECT, cov_mod.ALARM_UNENFORCEABLE)]
    assert report.unenforceable() == [] and "unenforceable_blocking=0" in line

    # --- UNVALIDATED: a statement whose every declarable relation was skipped -------------------
    # Forced, so the alarm is seen firing rather than assumed: one opening-cash figure makes the
    # cash flow statement PRESENT — so its relations are declarable — while none of them can run.
    ont = _ontology(raw_ontology)
    starved = coverage(_rows(evaluate_structure(
        template, _facts({"cf_opening_cash_and_cash_equivalents": 8_156_453}), ontology=ont)))
    cash = starved.statements["cash_flow"]
    assert cash.evaluated == 0 and cash.declarable > 0
    assert cash.status == cov_mod.UNVALIDATED and cash.status != cov_mod.PASSED
    assert cash.buckets.failed == 0                 # no failures, and nothing proved
    assert cash.validation_rate is None             # 0/0 is not 1.0
    assert "cash_flow" in starved.unvalidated()
    # One alarm per unvalidated statement, no more and no fewer.
    assert sorted(a["statement"] for a in starved.alarms
                  if a["code"] == cov_mod.ALARM_UNVALIDATED) == starved.unvalidated()

    # --- PIPELINE_DEFECT: an unconditional guard that answered nothing -------------------------
    # "…together with any of its listed components" reads its list from the rulebook, so emptying
    # that list leaves the guard nothing to compare. Its precondition is "always", so having no
    # answer cannot be a fact about the filing.
    blinded = copy.deepcopy(raw_ontology)
    group = next(g for g in blinded["global_rules"]["mutually_exclusive_groups"]
                 if g["aggregate"] == "bs_equity__reserves")
    group["components"] = []
    report2 = evaluate_structure(template, _facts({"bs_equity__reserves": 900}),
                                ontology=_ontology(blinded))
    guard = next(r for r in report2.results
                 if r.rule_id == "guard:mutually_exclusive:bs_equity__reserves")
    assert guard.status == STATUS_AUTHORING_ERROR and guard.details["precondition"] == "always"
    cov2 = coverage(_rows(report2))
    assert [a for a in cov2.alarms
            if a["code"] == cov_mod.ALARM_PIPELINE_DEFECT
            and a["rule_id"] == guard.rule_id and a["precondition"] == "always"]
    # …and it is not filed as something better extraction would fix.
    assert cov2.statements[guard.details["statement"]].skips.get("UNEVALUABLE_RULE") == 1

    # A guard that genuinely needs something of the filing is the opposite case: consolidation
    # eliminates investments in subsidiaries, so on a company-only column that guard has nothing
    # to say. Its precondition says so, so the skip is recoverable coverage, not an alarm.
    company = evaluate_structure(
        template, _facts({"bs_non_current_assets__investments_in_subsidiaries": 4_500_000},
                         basis=Basis.STANDALONE), ontology=ont)
    skipped = next(r for r in company.results if r.rule_id.startswith(
        "guard:consolidation_eliminated:"))
    assert skipped.status == "skipped" and skipped.details["reason"] == "precondition_absent"
    assert skipped.details["precondition"] == "a consolidated column"
    cov3 = coverage(_rows(company))
    assert not [a for a in cov3.alarms if a["code"] == cov_mod.ALARM_PIPELINE_DEFECT]
    assert cov3.statements["balance_sheet"].recoverable_skips >= 1


# ---------------------------------------------------------------------------------------------
# The report has to REACH A HUMAN. Until it did, coverage went to ctx.log → run.logs, which no
# endpoint serves — so the Review screen showed failures and said nothing at all about the
# relations that were never evaluable. It is now derived at the point it is served, inside the
# review payload, and these tests hold the served block to the same numbers the module computes.
# ---------------------------------------------------------------------------------------------

def _served(client, doc_id: str) -> dict:
    return client.get(f"/api/v1/documents/{doc_id}/review").json()["coverage"]


def test_the_served_block_relabels_the_report_and_recomputes_no_number(client):
    doc_id = _extracted(client)
    stored = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["structural"]
    expected = coverage(stored).as_dict()
    block = _served(client, doc_id)

    assert block["available"] is True
    assert block["run_id"] and block["engine_version"]
    # Every number in the aggregate and in each statement row is the module's own; the block adds
    # `label` and `status_label` and nothing else.
    assert {k: v for k, v in block["aggregate"].items()
            if k not in ("label", "status_label")} == expected["aggregate"]
    assert [{k: v for k, v in row.items() if k not in ("label", "status_label")}
            for row in block["statements"]] == expected["statements"]
    assert block["aggregate"]["label"] and block["aggregate"]["status_label"]
    assert all(row["label"] and row["status_label"] for row in block["statements"])

    # The buckets total, and only the absent-statement bucket sits outside the denominator.
    assert sum(s["count"] for s in block["skips"]) == block["aggregate"]["skipped"]
    assert {s["bucket"]: s["count"] for s in block["skips"]} \
        == expected["aggregate"]["skips"]
    assert [s["bucket"] for s in block["skips"] if not s["counts_in_denominator"]] \
        == [b for b in cov_mod.NOT_DECLARABLE if b in expected["aggregate"]["skips"]]
    assert all(s["label"] and s["meaning"] for s in block["skips"])


def test_the_band_and_the_checks_cannot_contradict_when_the_two_id_spellings_disagree(client):
    """One response must not serve template-derived findings above "no template was attached".

    The template id is written in two places when a run is created — the ``template_version_id``
    COLUMN and ``options["template_version_id"]`` — and a run built straight from options leaves the
    column None, which two existing fixtures already do. ``_coverage_block`` read the column while
    the check builders read the option, so such a run served structural/uncomputed checks under a
    band saying no template was attached: a self-contradiction inside one payload, and the exact
    misread the band was added to prevent. Both now resolve the id through ``_run_template_id``.
    """
    import uuid

    from app.api.routes.documents import _coverage_block, _run_template_id, _template_for_run
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun, TemplateVersion

    structural = [{"rule_id": "tpl:bs_total_assets", "kind": "rollup", "status": "fail",
                   "scope_key": "consolidated/current", "expected": 100, "actual": 90,
                   "details": {"target": "bs_total_assets", "components": ["bs_ca__cash"],
                               "statement": "balance_sheet", "basis": "consolidated",
                               "period_label": "current",
                               "component_values": {"bs_ca__cash": "90"},
                               "sign_suspect": None}}]
    init_db()
    with SessionLocal() as session:
        doc = Document(filename="split.pdf", fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin",
                       status="extracted")
        session.add(doc)
        tv = TemplateVersion(template_key=f"s-{uuid.uuid4().hex[:8]}", name="S", version=1,
                             definition={"statements": []})
        session.add(tv)
        session.flush()
        # The disagreement itself: the option carries the id, the column does not.
        run = ExtractionRun(document_id=doc.id, status="succeeded",
                            options={"template_version_id": tv.id},
                            result={"rows": [], "filename": "split.pdf",
                                    "structural": structural})
        session.add(run)
        session.commit()
        assert run.template_version_id is None and _run_template_id(run) == tv.id
        block = _coverage_block(run, _template_for_run(session, run), "en")
        doc_id = doc.id

    # The band resolves the template the checks resolved, so it reports coverage instead of
    # denying that a template exists.
    assert block["available"] is True and block.get("reason") is None
    served = client.get(f"/api/v1/documents/{doc_id}/review").json()
    checks = served["checks"]
    assert [c["type"] for c in checks] == ["structural"]
    # The contradiction, stated as an invariant: findings derived from a template cannot sit above
    # a band claiming none was attached.
    if not served["coverage"]["available"]:
        assert served["coverage"]["reason"] != "no_template", [c["type"] for c in checks]
    assert served["coverage"]["available"] is True


def test_a_run_that_named_no_template_is_given_none(client):
    """FINDING E, the other half. Agreeing on how to READ the id (``_run_template_id``) is worth
    nothing while one reader answers "no template" and the other substitutes one.

    The template is genuinely optional — ``ExtractionOptions.template_version_id`` defaults to None
    and the upload screen allows it — and there ARE seeded templates in the database, so the fallback
    always found one. The findings, node labels and computed subtotals it produced were attributed to
    a template the analyst never chose, with nothing on the screen saying which.
    """
    from sqlalchemy import select

    from app.api.routes.documents import _run_template_id, _template_for_run
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun, TemplateVersion

    doc_id = _extracted(client, template=False)
    # THE RUN NOW NAMES A TEMPLATE, and that is deliberate: ``resolve_template_id`` settles the
    # latest stored template when the caller pins none, and PINS IT ON THE RUN. That is a default
    # rather than the substitution this test was written about — the run records what it used and
    # the screen names it, so nothing is attributed to a template the analyst cannot see.
    #
    # The invariant this test exists for is about READ time, so it is asserted where it lives:
    # strip the run's template and the readers must answer "none" in agreement, rather than one of
    # them finding a template to fall back to. (``tests/test_template_default.py`` holds the
    # resolver's own half — including that a run genuinely naming none still resolves to None.)
    with SessionLocal() as session:
        # A template really is available to fall back to, so none of this is vacuous.
        assert session.execute(select(TemplateVersion)).scalars().first() is not None
        run = session.query(ExtractionRun).filter(
            ExtractionRun.document_id == doc_id).order_by(
                ExtractionRun.created_at.desc()).first()
        run.template_version_id = None
        run.options = {**(run.options or {}), "template_version_id": None}
        session.commit()
        assert _run_template_id(run) is None
        assert _template_for_run(session, run) is None

    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert review["coverage"] == {"available": False, "reason": "no_template",
                                  "reason_label": review["coverage"]["reason_label"]}
    assert {c["type"] for c in review["checks"]} <= {"unmapped", "low_confidence", "balance",
                                                     "equity_tie", "note_tie"}

    # And the payload-level half, on rows that WOULD produce template-derived cards: a printed
    # subtotal that disagrees with its extracted components is exactly what `_calculated_checks`
    # reports — but only a template can say that the line IS a subtotal and what it is made of. With
    # the fallback in place this run served those cards under the "no template" band.
    def _row(key, cur):
        return {"canonical_key": key, "source_label": key,
                "values": [{"basis": "consolidated", "period_label": "current",
                            "value": str(cur)}]}

    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter(
            ExtractionRun.document_id == doc_id).order_by(
                ExtractionRun.created_at.desc()).first()
        run.result = {**run.result, "rows": [
            _row("bs_current_assets__inventories", 2200),
            _row("bs_current_assets__trade_receivables", 1310),
            _row("bs_current_assets__total_current_assets", 9999)]}
        session.commit()
    after = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert after["coverage"]["reason"] == "no_template"
    assert [c["type"] for c in after["checks"]] == [], \
        "template-derived findings served above a band stating no template was attached"

    # …and the resolution itself, once the payload has shown what it decides.
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter(
            ExtractionRun.document_id == doc_id).order_by(
                ExtractionRun.created_at.desc()).first()
        assert _template_for_run(session, run) is None


def test_both_rates_travel_together_and_no_third_score_is_invented(client):
    block = _served(client, _extracted(client))
    for row in [block["aggregate"], *block["statements"]]:
        assert ("validation_rate" in row) == ("coverage_rate" in row)
        assert (row["validation_rate"] is None) == (row["evaluated"] == 0)
        assert (row["coverage_rate"] is None) == (row["declarable"] == 0)
        # pass/(pass+fail) IS the validation rate; under any other name it is the collapse.
        assert not {"pass_rate", "score", "rate", "success_rate", "health", "pct",
                    "percent"} & row.keys()


def test_unenforceable_alarms_come_first_and_every_alarm_is_labelled():
    """Ordering matters because an unenforceable blocking rule is the one alarm invisible in every
    count — it produces no failure, so it reads exactly like a rule that held.

    The rows are hand-built here because the presenter is what is under test; that these alarms
    fire on a real filing is asserted above, against the shipped rulebook.
    """
    from app.api.routes.documents import _coverage_block

    def row(rule_id, status, statement, reason=None, severity=None):
        details = {"statement": statement}
        if reason:
            details["reason"] = reason
        if severity:
            details["severity"] = severity
        return {"rule_id": rule_id, "kind": "rollup", "status": status, "details": details}

    rows = [
        # a statement that proved nothing → UNVALIDATED
        row("tpl:pl", "skipped", "profit_and_loss", "target_not_extracted"),
        # a BLOCKING rule the engine cannot run as authored → UNENFORCEABLE (+ PIPELINE_DEFECT)
        row("identity:unrunnable", STATUS_AUTHORING_ERROR, "balance_sheet",
            "unresolved_terms", "blocking"),
        row("tpl:bs", "pass", "balance_sheet"),
    ]
    assert [a for a in coverage(rows).alarms if a["code"] == cov_mod.ALARM_UNENFORCEABLE]

    class _Run:
        id = "run-x"
        engine_version = "0.1.0"
        template_version_id = "tpl-x"
        result = {"structural": rows}

    block = _coverage_block(_Run(), None, "en")
    codes = [a["code"] for a in block["alarms"]]
    assert codes[0] == cov_mod.ALARM_UNENFORCEABLE
    assert block["alarms"][0]["assurance_gap"] is True
    assert block["alarms"][0]["rule_id"] == "identity:unrunnable"
    assert all(a["label"] and a["text"] for a in block["alarms"])
    assert all(a["assurance_gap"] is (a["code"] == cov_mod.ALARM_UNENFORCEABLE)
               for a in block["alarms"])
    # coverage.py's raw English `note` is not served; the localized sentence replaces it.
    assert all("note" not in a for a in block["alarms"])
    # An UNVALIDATED alarm IS included even though the statement row carries the status too — the
    # client renders this list only and never synthesises an alarm from a status, so it cannot
    # appear twice.
    assert cov_mod.ALARM_UNVALIDATED in codes
    assert len(codes) == len(coverage(rows).alarms)          # no alarm dropped, none duplicated


def test_the_three_unavailable_reasons_resolve_and_are_never_rendered_as_zeros(client):
    """"0 of 0 relations evaluated" is the exact misread this module exists to prevent, so an
    unavailable report says WHY in words and carries no counts at all."""
    no_run = client.post("/api/v1/documents",
                         files={"file": ("norun.pdf", make_native_pdf(),
                                         "application/pdf")}).json()["id"]
    assert _served(client, no_run) == {
        "available": False, "reason": "not_extracted",
        "reason_label": _served(client, no_run)["reason_label"]}

    # Extracted with the rulebook and NO template: structural validation never ran, while the lines
    # ARE mapped — so the template-derived builders would have had concepts to build cards from.
    #
    # The run's template is cleared explicitly, because a run created through the API now always
    # NAMES one: ``resolve_template_id`` settles the latest stored template when the caller pins
    # none. A run naming no template is still a state the product must serve honestly — runs stored
    # before that resolver existed are exactly it — and this reason is how it says so.
    from app.db.base import SessionLocal as _Session
    from app.db.models import ExtractionRun as _Run

    no_tpl_doc = _extracted(client, template=False)
    with _Session() as session:
        run = session.query(_Run).filter(_Run.document_id == no_tpl_doc).one()
        run.template_version_id = None
        run.options = {**(run.options or {}), "template_version_id": None}
        session.commit()
    no_tpl = _served(client, no_tpl_doc)
    assert no_tpl["reason"] == "no_template" and no_tpl["available"] is False
    # …AND NOT ONE TEMPLATE-DERIVED FINDING SITS ABOVE IT. This is the assertion the docstring
    # promised and nothing made: `_template_for_run` fell back to the newest seeded
    # TemplateVersion, so this very run served 2 calculated_mismatch and 2 uncomputed cards — built
    # from another template's rollup children and node labels — under a band stating that no
    # template was attached.
    served_checks = client.get(f"/api/v1/documents/{no_tpl_doc}/review").json()["checks"]
    assert not [c for c in served_checks
                if c["type"] in ("structural", "calculated_mismatch", "uncomputed")], \
        [c["type"] for c in served_checks]

    # A template WAS attached and declared nothing for this filing — an authoring gap, said out
    # loud rather than rendered as a clean sheet.
    from app.db.base import SessionLocal
    from app.db.models import ExtractionRun

    doc_id = _extracted(client)
    with SessionLocal() as session:
        run = session.query(ExtractionRun).filter(
            ExtractionRun.document_id == doc_id).one()
        run.result = {**run.result, "structural": []}
        session.commit()
    none_declared = _served(client, doc_id)
    assert none_declared["reason"] == "no_relations" and none_declared["available"] is False

    for block in (no_tpl, none_declared):
        assert block["reason_label"]
        assert not {"aggregate", "statements", "skips", "alarms"} & block.keys()


def test_failed_relations_reported_by_a_card_above_are_counted_once(client):
    """`_structural_checks` suppresses a failed relation whose target already has its own check,
    so coverage.failed can legitimately exceed the number of structural cards. The difference is
    derived from the SAME `covered` set that suppressed them."""
    from app.api.routes.documents import _build_review

    rows = [{"canonical_key": "bs_total_assets",
             "values": [{"basis": "consolidated", "period_label": "current", "value": "100"}]},
            {"canonical_key": "bs_total_equity_and_liabilities",
             "values": [{"basis": "consolidated", "period_label": "current", "value": "90"}]}]
    # `difference` as services/structural_checks.py:713 always sets it for an arithmetic relation
    # (`difference=diff`). It was omitted here, and suppression now compares the DIFFERENCE — a card
    # reporting 10 about this line does not stand in for a relation reporting 2,500 about it — so a
    # fixture without one describes a relation the evaluator never produces.
    structural = [{"rule_id": "tpl:bs_total_assets", "status": "fail", "difference": 10,
                   "scope_key": "consolidated/current", "expected": 90, "actual": 100,
                   "details": {"target": "bs_total_assets", "components": [],
                               "statement": "balance_sheet", "basis": "consolidated",
                               "period_label": "current", "component_values": {}}}]
    review = _build_review(rows, "d.pdf", "en", [], structural, None,
                           coverage_block={"available": True, "aggregate": {}, "statements": [],
                                           "skips": [], "alarms": [], "run_id": "r",
                                           "engine_version": "0"})
    # The balance identity owns bs_total_assets, so the structural relation raises no card…
    assert not [c for c in review["checks"] if c["type"] == "structural"]
    # …and the band says so, rather than letting its failed count look unaccounted for.
    assert review["coverage"]["failed_reported_elsewhere"] == 1


def test_accepting_a_finding_changes_no_coverage_number(client):
    """A failed relation stays failed:1 while its finding reads "accepted". Judgement is about
    findings; coverage is about what was evaluable, and collapsing the two rebuilds the trap."""
    doc_id = _extracted(client, template=False)
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    before = review["coverage"]
    assert review["checks"]

    for check in review["checks"]:
        r = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                        json={"subject_key": check["subject_key"],
                              "evidence_digest": check["evidence_digest"],
                              "reason": "Looked; it stands."})
        assert r.status_code == 200, r.text
    after = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert after["summary"]["accepted"] == len(review["checks"])
    assert after["coverage"] == before


def test_every_word_the_coverage_band_prints_is_translated():
    """The band is deliberately verbal — "Nothing verified", not "0%" — so an untranslated string
    is a reader seeing English on a zh/ar/fr screen. Every label, meaning and alarm sentence is
    checked against the English one, in all three other locales.
    """
    from app.api.routes.documents import _coverage_block, _coverage_unavailable

    rows = [
        {"rule_id": "tpl:pl", "status": "skipped",
         "details": {"statement": "profit_and_loss", "reason": "target_not_extracted"}},
        {"rule_id": "tpl:bs", "status": "pass", "details": {"statement": "balance_sheet"}},
        {"rule_id": "tpl:bs2", "status": "fail", "details": {"statement": "balance_sheet"}},
        {"rule_id": "tpl:cf", "status": "skipped",
         "details": {"statement": "cash_flow", "reason": "statement_absent"}},
        {"rule_id": "tpl:bs3", "status": "skipped",
         "details": {"statement": "balance_sheet", "reason": "derived_input"}},
        {"rule_id": "tpl:bs4", "status": "skipped",
         "details": {"statement": "balance_sheet", "reason": "no_reported_subtotal"}},
        {"rule_id": "id:x", "status": STATUS_AUTHORING_ERROR,
         "details": {"statement": "balance_sheet", "reason": "unresolved_terms",
                     "severity": "blocking"}},
        {"rule_id": "tpl:unk", "status": "skipped",
         "details": {"statement": "balance_sheet", "reason": "a_reason_nobody_classified"}},
    ]

    class _Run:
        id = "run-x"
        engine_version = "0.1.0"
        template_version_id = "tpl-x"
        result = {"structural": rows}

    en = _coverage_block(_Run(), None, "en")
    # The fixture exercises every bucket and every alarm code, so nothing is checked vacuously.
    assert {s["bucket"] for s in en["skips"]} == set(cov_mod.TAXONOMY.values()) | {
        cov_mod.UNCLASSIFIED}
    assert {a["code"] for a in en["alarms"]} == {
        cov_mod.ALARM_UNENFORCEABLE, cov_mod.ALARM_UNVALIDATED, cov_mod.ALARM_PIPELINE_DEFECT}

    for locale in ("zh", "ar", "fr"):
        loc = _coverage_block(_Run(), None, locale)
        assert loc["aggregate"]["label"] != en["aggregate"]["label"]
        assert loc["aggregate"]["status_label"] != en["aggregate"]["status_label"]
        assert all(row["status_label"] != row_en["status_label"]
                   for row, row_en in zip(loc["statements"], en["statements"]))
        for got, expected in zip(loc["skips"], en["skips"]):
            assert got["label"] != expected["label"], (locale, got["bucket"])
            assert got["meaning"] != expected["meaning"], (locale, got["bucket"])
        for got, expected in zip(loc["alarms"], en["alarms"]):
            assert got["label"] != expected["label"], (locale, got["code"])
            assert got["text"] != expected["text"], (locale, got["code"])
        for reason in ("not_extracted", "no_template", "no_relations"):
            assert _coverage_unavailable(reason, locale)["reason_label"] \
                != _coverage_unavailable(reason, "en")["reason_label"], (locale, reason)
