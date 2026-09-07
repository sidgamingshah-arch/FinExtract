"""Deprec & Impairment (Oper Exp) / (COS) — writes services.deprec_impairment's computed values
onto the two is_pl LineItems those canonical keys name.

Runs after notes are extracted, normalized and linked (the computation reads note-level
depreciation lines directly, and needs them in the document's single normalized unit), and before
reconcile/structural checks, so a tie-out sees the computed figure rather than a blank cell.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext
from app.services.computed_paths import apply_computed, policy_from
from app.services.deprec_impairment import (COS_FORMULA, COS_KEY, OPER_EXP_FORMULA, OPER_EXP_KEY,
                                            DeprecResult, compute)


def _apply(doc: DocumentModel, canonical_key: str, basis: str, period_label: str,
          result, next_ordinal: list[int], policy, log=None) -> None:
    """Hand the derived figure to the two-path policy — see services.computed_paths.

    This used to write unconditionally, which made the complex path win over the
    rulebook's own reading with no record and no way to choose. The decision now lives in
    one place for all five derivations.
    """
    apply_computed(doc, canonical_key=canonical_key, basis=basis,
                   period_label=period_label, value=result.value,
                   formula=result.priority_used, service="deprec_impairment",
                   evidence=result.evidence, flags=list(result.flags),
                   next_ordinal=next_ordinal, policy=policy, log=log)


class DeprecImpairmentStage:
    name = "deprec_impairment"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        keys = {c.get("canonical_key") for st in (template or {}).get("statements", []) or []
                for sec in st.get("sections", []) or [] for c in (sec.get("children") or [])}
        if OPER_EXP_KEY not in keys and COS_KEY not in keys:
            ctx.log("deprec_impairment:skipped(template does not declare either field)")
            return doc
        if not doc.notes:
            ctx.log("deprec_impairment:skipped(no notes extracted)")
            return doc

        # THE COMPLEX PATH IS SWITCHABLE. Off, the rulebook's own reading of these
        # concepts is what publishes — see services.computed_paths.
        policy = policy_from(ctx.settings)
        if not policy.runs("deprec_impairment"):
            ctx.log("deprec_impairment:skipped(complex path disabled)")
            return doc

        results = compute(doc)
        next_ordinal = [max((li.ordinal for li in doc.line_items), default=0) + 1]
        applied = 0
        for (basis, period_label), fields in results.items():
            for canonical_key, result in ((OPER_EXP_KEY, fields["oper_exp"]), (COS_KEY, fields["cos"])):
                if result.value is None:
                    continue
                _apply(doc, canonical_key, basis, period_label, result, next_ordinal,
                       policy, ctx.log)
                applied += 1
        # WHY, when nothing was computed. A run that says "0 value(s) computed" and no more is
        # indistinguishable from a filing that discloses no depreciation — and on 688008 the
        # charge IS disclosed, as a combined depreciation-and-amortisation line the spec declines.
        why = sorted({f for r in results.values() for res in r.values() for f in res.flags
                      if f.startswith(("COMBINED_CHARGE_ONLY", "MISSING_NOTE"))}) if not applied \
            else []
        ctx.log(f"deprec_impairment:{applied} value(s) computed"
                + (f" ({', '.join(why)})" if why else ""))
        return doc
