"""Structural validation stage — runs the declared arithmetic over the mapped line items.

Wires ``services.structural_checks`` (pure, unit-tested) to the document model: it stores the
report on ``doc.structural`` and flags every line item and value that participates in a failed
relation, so a mis-mapped or mis-signed number reaches the review queue even though its label
matched perfectly.

The rulebook's ``validation`` block is passed in with the template, so the run also evaluates the
rulebook's own identities, its cross-concept guards and its section reconciliation. Three
consequences differ here, and they differ because the rulebook says they do:

* a ``blocking`` break caps the value's validation confidence — the figure cannot be right;
* a ``warning`` break is flagged but does not cap. ``global_rules.sign_convention`` states it
  outright ("a review trigger, not an auto-correction"), and capping on warnings would hold every
  filing with a classification difference below the auto-accept threshold;
* a failed section reconciliation blocks AUTO-APPROVAL of that section, which is enforced the only
  way the pipeline can enforce it: every value in the section is capped below the auto-accept
  confidence, so no figure in an unreconciled section is ever accepted without a reviewer.

It runs after the confidence stage on purpose: that stage assigns ``validation`` outright for
the balance identity, while a structural failure may only *lower* an existing signal — so it
has to see the final value, not be overwritten by it.
"""
from __future__ import annotations

from app.core.models import DocumentModel
from app.core.stage import PipelineContext
from app.services.coverage import (
    blocking_failures,
    coverage,
    sections_blocked_from_auto_approval,
    warning_failures,
)
from app.services.structural_checks import evaluate_structure
from app.services.unread_pages import statements_not_read

FLAG = "structural_mismatch"
FLAG_WARNING = "structural_warning"
FLAG_SECTION = "section_unreconciled"
_VALIDATION_CAP = 0.5     # same weight as a note that doesn't tie: a failed check dominates


def _participants(rows: list[dict]) -> set[tuple[str, str, str | None]]:
    """(canonical_key, basis, period) triples that took part in a failed relation — the total and
    its components alike, since the arithmetic cannot say which one is wrong."""
    out: set[tuple[str, str, str | None]] = set()
    for res in rows:
        d = res.get("details") or {}
        for key in (d.get("target"), *(d.get("components") or [])):
            if key:
                out.add((key, d.get("basis"), d.get("period_label")))
    return out


class StructuralStage:
    name = "structural"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        template = getattr(ctx, "template", None)   # attribute set by services.documents
        # A run with no rows because its pages were not read (every page scanned and no OCR) is
        # still evaluated: its relations are reported as not read rather than not reported at all.
        if template is None or not (doc.line_items or doc.unread_pages):
            ctx.log("structural:skipped(no template or no line items)")
            return doc

        ontology = getattr(ctx, "ontology", None)
        # The statements whose pages this run did not read: their relations are skipped as not
        # read and stay in the coverage denominator (services/coverage.py NOT_READ).
        not_read = statements_not_read(doc.unread_pages, getattr(ctx, "sections_off", None),
                                       doc.pages)
        if not_read:
            ctx.log(f"structural:statements_not_read={','.join(sorted(not_read))}")
        report = evaluate_structure(template, doc.line_items, ontology=ontology,
                                    not_read=not_read)
        doc.structural = report

        rows = [r.model_dump(mode="json") for r in report.results]
        blocking = _participants(blocking_failures(rows))
        warnings = _participants(warning_failures(rows))
        # A section whose reconciliation failed blocks auto-approval for every key in it, on every
        # column — the gap is a property of the section, not of one period's figure.
        blocked_sections = sections_blocked_from_auto_approval(rows)
        blocked_keys = {k for keys in blocked_sections.values() for k in keys}

        flagged = warned = 0
        for li in doc.line_items:
            if not li.canonical_key:
                continue
            hit = warn = section_hit = False
            for ev in li.values.values():
                triple = (li.canonical_key, ev.basis.value, ev.period_label)
                if li.canonical_key in blocked_keys:
                    section_hit = True
                    ev.confidence.flags.append(FLAG_SECTION)
                if triple in blocking or li.canonical_key in blocked_keys:
                    hit = True
                    flagged += 1
                    ev.confidence.flags.append(FLAG)
                    prior = ev.confidence.validation
                    ev.confidence.validation = min(_VALIDATION_CAP,
                                                   prior if prior is not None else 1.0)
                elif triple in warnings:
                    # Flagged, not capped: the rulebook calls this a review trigger.
                    warn = True
                    warned += 1
                    ev.confidence.flags.append(FLAG_WARNING)
            if hit:
                li.confidence.flags.append(FLAG)
            if warn:
                li.confidence.flags.append(FLAG_WARNING)
            if section_hit:
                li.confidence.flags.append(FLAG_SECTION)

        cov = coverage(rows)
        ctx.log(f"structural:evaluated={len(report.evaluated())} "
                f"failed={len(report.failures())} not_evaluable={len(report.skipped())} "
                f"flagged_values={flagged}")
        # Both rates and the three buckets, in the persisted run log. A count of passes on its own
        # is the number that makes a barely-verified extraction look clean.
        ctx.log(cov.headline())
        if blocked_sections:
            ctx.log("structural:auto_approval_blocked=" + ",".join(sorted(blocked_sections)))
        for alarm in cov.alarms:
            ctx.log("structural:alarm " + " ".join(f"{k}={v}" for k, v in alarm.items()))
        return doc
