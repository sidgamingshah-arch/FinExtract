"""The coverage contract for structural validation — how much of the declared structure a run
actually verified, stated so it cannot be mistaken for how much of it passed.

``structural_checks`` emits one row per declared relation with ``pass`` / ``fail`` / ``skipped``
and no denominator. Read naively that is a trap: a statement with 3 passes and 11 skips reports
three passes and no failures, and every consumer that divides passes by *checked* relations calls
it 100%. The eleven relations nobody could run are exactly the ones that would have caught the
mis-mapping.

So this module reports TWO numbers and refuses to reduce them to one:

* ``validation_rate`` = pass / evaluated — of the relations that ran, how many held;
* ``coverage_rate``   = evaluated / declarable — how many of the relations that this filing MAKES
  ANSWERABLE ran at all.

Neither is meaningful alone and neither is ever emitted alone (:meth:`CoverageReport.as_dict`
carries both or neither). ``pass / (pass + fail)`` is not offered under any name: it equals the
validation rate, and every use of it as "the" score is the collapse this module exists to prevent.

``declarable`` counts the relations declared for the statements PRESENT in the filing — not the
relations that ran. That is what makes the coverage rate fall when extraction is thin: a relation
skipped because its inputs were never extracted stays in the denominator, because better
extraction would recover it. The skip taxonomy is where that judgement is recorded, and the split
between its first two buckets is the whole point:

* ``INPUT_ABSENT`` — recoverable. A row was not extracted, two participants disagreed on scale,
  a guard's precondition was not met. Belongs in an improvement backlog.
* ``TAUTOLOGICAL`` — NOT recoverable, however good extraction gets. An input to the relation was
  derived from the relation, so it cannot fail. No amount of extraction quality turns it into a
  check; the relation has to be re-authored or the derivation removed.
* ``NO_REPORTED_SUBTOTAL`` — the filing (or the template) prints no subtotal to reconcile against.
* ``SIGNS_NOT_ESTABLISHED`` — the inputs are all there and the rule is sound, but the SIGN
  CONVENTION its sum needs is not declared for the section they sit in, so adding them is not the
  template's arithmetic. Recoverable like ``INPUT_ABSENT`` and separate from it because the fix is
  a rulebook declaration rather than better extraction: the rulebook says expenses are stored
  negative, a CAS filing prints them unsigned, and nothing in the output-CSV rulebook declares
  which concepts to orient (see ``structural_checks._signs_established``). Naming it apart is the
  point — folded into ``INPUT_ABSENT`` it would sit in an extraction backlog no extraction fixes.
* ``UNEVALUABLE_RULE`` — the rule as authored cannot be run at all: an unparsable expression, a
  term naming nothing, an op with no semantics, a guard sentence matching no predicate. It counts
  in the denominator (it was declared for a statement that is present) but it is not an extraction
  problem and never becomes one, so folding it into ``INPUT_ABSENT`` would put an authoring defect
  into the extraction backlog and quietly keep it there.
* ``STATEMENT_ABSENT`` — the only bucket that does NOT count. A standalone-only filing has no cash
  flow statement; holding it against the run's coverage would make every filing look incomplete.

Four alarm states are reported explicitly, because each of them is invisible in the counts:

* a statement whose every declarable relation was skipped is ``UNVALIDATED`` — it has no failures
  and it has proved nothing, and it must never render as "passed";
* more ``TAUTOLOGICAL`` skips than evaluated relations means the validation layer is mostly
  checking its own arithmetic;
* a guard whose precondition is ``"always"`` coming back skipped cannot be a fact about the
  filing — it is a defect in this pipeline, and it is labelled one;
* a rule the rulebook declared ``blocking`` that cannot run AS AUTHORED is ``UNENFORCEABLE``. It
  fires on no filing ever, so "no blocking failures" is not evidence of anything: the slot a
  reviewer reads as assurance is empty. It shipped that way once — a blocking cash-flow identity
  naming three terms no template declares — which is why it gets an alarm of its own rather than
  sharing the generic defect code.

The rows themselves say which is which: a rule the RULEBOOK authored so that it can never run
carries the distinct ``error`` status rather than ``skipped`` (see
``structural_checks.AUTHORING_REASONS``). It is still one of the three buckets here — an unrunnable
rule is not a pass and not a fail, so it counts as unchecked — and the buckets always total.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.core.models import RuleResult

# Skip reason (as emitted by ``structural_checks``) → taxonomy bucket. A reason missing from this
# table lands in ``UNCLASSIFIED``, which COUNTS in the denominator and raises an alarm: a new skip
# reason must never dilute a coverage rate by dropping out of the arithmetic unnoticed.
TAXONOMY: dict[str, str] = {
    "target_not_extracted": "INPUT_ABSENT",
    "components_not_mapped": "INPUT_ABSENT",
    "mixed_scale": "INPUT_ABSENT",
    "ambiguous_mapping": "INPUT_ABSENT",
    "precondition_absent": "INPUT_ABSENT",
    "derived_input": "TAUTOLOGICAL",
    "no_reported_subtotal": "NO_REPORTED_SUBTOTAL",
    "component_signs_not_established": "SIGNS_NOT_ESTABLISHED",
    "section_signs_not_declared_one_way": "SIGNS_NOT_ESTABLISHED",
    "statement_absent": "STATEMENT_ABSENT",
    "unsupported_op": "UNEVALUABLE_RULE",
    "unparsable_expr": "UNEVALUABLE_RULE",
    "unresolved_terms": "UNEVALUABLE_RULE",
    "repeated_term": "UNEVALUABLE_RULE",
    "guard_unrecognised": "UNEVALUABLE_RULE",
    "guard_components_unknown": "UNEVALUABLE_RULE",
}
UNCLASSIFIED = "UNCLASSIFIED"
# The one bucket outside the denominator. Everything else is a relation this filing could have
# answered — including the ones it structurally never will, which is why they are named separately
# rather than dropped.
NOT_DECLARABLE = ("STATEMENT_ABSENT",)

ALARM_UNVALIDATED = "UNVALIDATED"
ALARM_TAUTOLOGY = "TAUTOLOGICAL_EXCEEDS_EVALUATED"
ALARM_PIPELINE_DEFECT = "PIPELINE_DEFECT"
# A rule declared ``blocking`` that the engine cannot run as authored. Named apart from
# PIPELINE_DEFECT because the two ask for different things: that one is a rule to repair, this one
# is an assurance to stop trusting until it is.
ALARM_UNENFORCEABLE = "BLOCKING_RULE_UNENFORCEABLE"

# Statuses a statement may carry. "PASSED" is deliberately unreachable while anything declarable
# was skipped: a partial verification is PARTIAL, never a pass.
UNVALIDATED = "UNVALIDATED"
ABSENT = "ABSENT"
FAILED = "FAILED"
PARTIAL = "PARTIAL"
PASSED = "PASSED"


def _row(result: RuleResult | dict) -> dict:
    """One result as a plain dict, whether it arrived as a model or as stored JSON.

    The persisted run carries ``result["structural"]`` as dumped dicts, so coverage can be
    recomputed from a stored run months later without re-running the pipeline.
    """
    if isinstance(result, dict):
        return result
    return result.model_dump(mode="json")


@dataclass
class Buckets:
    """The three outcomes, always all three. A caller cannot obtain one without the others."""

    passed: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped


@dataclass
class StatementCoverage:
    """Coverage for one statement, or for the whole filing when ``statement`` is ``"__all__"``."""

    statement: str
    buckets: Buckets = field(default_factory=Buckets)
    # taxonomy bucket -> count, for the skipped rows only
    skips: dict[str, int] = field(default_factory=dict)

    @property
    def evaluated(self) -> int:
        return self.buckets.passed + self.buckets.failed

    @property
    def declarable(self) -> int:
        """Relations this filing makes answerable: everything except the absent-statement rows."""
        return self.evaluated + sum(n for bucket, n in self.skips.items()
                                    if bucket not in NOT_DECLARABLE)

    @property
    def validation_rate(self) -> float | None:
        """pass / evaluated. ``None`` when nothing ran — 0/0 is not 1.0, and rendering it as
        1.0 is precisely how an unvalidated statement comes to look verified."""
        return None if self.evaluated == 0 else round(self.buckets.passed / self.evaluated, 4)

    @property
    def coverage_rate(self) -> float | None:
        """evaluated / declarable. ``None`` when the filing declares nothing answerable here."""
        return None if self.declarable == 0 else round(self.evaluated / self.declarable, 4)

    @property
    def recoverable_skips(self) -> int:
        """Skips a better extraction would turn into checks — the improvement backlog."""
        return self.skips.get("INPUT_ABSENT", 0)

    @property
    def status(self) -> str:
        if self.declarable == 0:
            return ABSENT
        if self.evaluated == 0:
            return UNVALIDATED
        if self.buckets.failed:
            return FAILED
        return PASSED if self.coverage_rate == 1.0 else PARTIAL

    def as_dict(self) -> dict:
        return {
            "statement": self.statement,
            "passed": self.buckets.passed,
            "failed": self.buckets.failed,
            "skipped": self.buckets.skipped,
            "evaluated": self.evaluated,
            "declarable": self.declarable,
            # Both rates or neither: emitting the validation rate on its own is the collapse.
            "validation_rate": self.validation_rate,
            "coverage_rate": self.coverage_rate,
            "skips": dict(sorted(self.skips.items())),
            "status": self.status,
        }


@dataclass
class CoverageReport:
    statements: dict[str, StatementCoverage] = field(default_factory=dict)
    aggregate: StatementCoverage = field(
        default_factory=lambda: StatementCoverage(statement="__all__"))
    alarms: list[dict] = field(default_factory=list)

    def unvalidated(self) -> list[str]:
        return sorted(s for s, cov in self.statements.items() if cov.status == UNVALIDATED)

    def unenforceable(self) -> list[dict]:
        """The blocking rules that cannot run as authored — the assurance this run does NOT have
        despite declaring it. Surfaced on its own because it is invisible in every count: such a
        rule produces no failure, so it is indistinguishable from a rule that held."""
        return [a for a in self.alarms if a.get("code") == ALARM_UNENFORCEABLE]

    def as_dict(self) -> dict:
        return {
            "aggregate": self.aggregate.as_dict(),
            "statements": [cov.as_dict() for _, cov in sorted(self.statements.items())],
            "alarms": self.alarms,
        }

    def headline(self) -> str:
        """One line for the run log. Both rates, always, plus the three buckets."""
        agg = self.aggregate
        def pct(value: float | None) -> str:
            return "n/a" if value is None else f"{value * 100:.0f}%"
        return (f"coverage:{agg.status} pass={agg.buckets.passed} fail={agg.buckets.failed} "
                f"skip={agg.buckets.skipped} validation_rate={pct(agg.validation_rate)}"
                f"({agg.buckets.passed}/{agg.evaluated}) "
                f"coverage_rate={pct(agg.coverage_rate)}({agg.evaluated}/{agg.declarable}) "
                f"unvalidated={','.join(self.unvalidated()) or 'none'} "
                # A blocking rule that cannot fire is not visible in any of the numbers above.
                f"unenforceable_blocking={len(self.unenforceable())} "
                f"alarms={len(self.alarms)}")


def coverage(results: Iterable[RuleResult | dict]) -> CoverageReport:
    """Bucket every structural result by statement, with the two rates and the alarm states."""
    report = CoverageReport()
    rows = [_row(r) for r in results]

    for row in rows:
        details = row.get("details") or {}
        statement = details.get("statement") or "unknown"
        cov = report.statements.setdefault(statement, StatementCoverage(statement=statement))
        for target in (cov, report.aggregate):
            status = row.get("status")
            if status == "pass":
                target.buckets.passed += 1
            elif status == "fail":
                target.buckets.failed += 1
            else:
                # ``skipped`` and the authoring-defect ``error`` both land here: neither is a pass
                # and neither is a fail, so both are unchecked, and the three buckets total the
                # rows. The taxonomy below is where the two part company.
                target.buckets.skipped += 1
                bucket = TAXONOMY.get(details.get("reason") or "", UNCLASSIFIED)
                target.skips[bucket] = target.skips.get(bucket, 0) + 1

    for statement in report.unvalidated():
        # No failures and nothing proved. Reported as its own state so no consumer can read the
        # absence of failures as a pass.
        report.alarms.append({"code": ALARM_UNVALIDATED, "statement": statement,
                              "skipped": report.statements[statement].buckets.skipped})

    for name, cov in [*sorted(report.statements.items()), ("__all__", report.aggregate)]:
        tautological = cov.skips.get("TAUTOLOGICAL", 0)
        if tautological and tautological > cov.evaluated:
            report.alarms.append({
                "code": ALARM_TAUTOLOGY, "statement": name,
                "tautological": tautological, "evaluated": cov.evaluated,
                "note": "more relations fed by derived values than relations actually checked",
            })

    for row in rows:
        details = row.get("details") or {}
        reason = details.get("reason") or ""
        # ``skipped`` and the authoring-defect ``error`` status alike: anything that did not run.
        if row.get("status") in ("pass", "fail"):
            continue
        bucket = TAXONOMY.get(reason, UNCLASSIFIED)
        # A guard that needs nothing from the filing cannot legitimately be unevaluable, and a
        # rule the engine cannot parse or resolve is an authoring/plumbing defect, not coverage.
        if row.get("kind") == "guard" and details.get("precondition") == "always" \
                and reason != "statement_absent":
            report.alarms.append({"code": ALARM_PIPELINE_DEFECT, "rule_id": row.get("rule_id"),
                                  "reason": reason, "precondition": "always"})
        elif bucket in ("UNEVALUABLE_RULE", UNCLASSIFIED):
            report.alarms.append({"code": ALARM_PIPELINE_DEFECT, "rule_id": row.get("rule_id"),
                                  "reason": reason or "unknown",
                                  "detail": details.get("terms") or details.get("expr")
                                  or details.get("op") or ""})
        # …and when the rulebook declared that unrunnable rule BLOCKING, the run is also missing an
        # assurance it claims to have. That never shows up in the buckets — an unrunnable rule
        # produces no failure — so it is stated outright, per rule, with its statement named.
        if bucket == "UNEVALUABLE_RULE" and details.get("severity") == "blocking":
            report.alarms.append({
                "code": ALARM_UNENFORCEABLE, "rule_id": row.get("rule_id"),
                "statement": details.get("statement") or "unknown", "reason": reason,
                "severity": "blocking",
                "detail": details.get("terms") or details.get("expr") or details.get("op") or "",
                "note": "declared blocking and cannot run as authored, so it fires on no filing",
            })
    return report


def blocking_failures(results: Iterable[RuleResult | dict]) -> list[dict]:
    """Failed relations the rulebook marked ``blocking``.

    The severity is the rulebook's, so the two consequences differ: a blocking break caps the
    value's validation confidence (it cannot be right), a warning is surfaced without capping (it
    is usually a classification difference worth a look). Collapsing them would either bury the
    blocking ones or hold every filing to the warnings.
    """
    return [r for r in (_row(x) for x in results)
            if r.get("status") == "fail"
            and (r.get("details") or {}).get("severity", "blocking") == "blocking"]


def warning_failures(results: Iterable[RuleResult | dict]) -> list[dict]:
    return [r for r in (_row(x) for x in results)
            if r.get("status") == "fail"
            and (r.get("details") or {}).get("severity") == "warning"]


def sections_blocked_from_auto_approval(
        results: Iterable[RuleResult | dict]) -> dict[str, list[str]]:
    """Section id → the canonical keys in it, for every section reconciliation that failed.

    The rulebook says a failure "blocks auto-approval of that section", and the block has to bite
    on something: the keys are what the caller lowers confidence on, so no value in an
    unreconciled section can clear the auto-accept threshold.
    """
    out: dict[str, list[str]] = {}
    for row in (_row(x) for x in results):
        details = row.get("details") or {}
        if row.get("kind") != "section_reconciliation" or row.get("status") != "fail":
            continue
        if not details.get("blocks_auto_approval"):
            continue
        section = details.get("section") or row.get("rule_id") or ""
        keys = [details.get("target") or "", *(details.get("components") or [])]
        out.setdefault(section, [])
        for key in keys:
            if key and key not in out[section]:
                out[section].append(key)
    return out
