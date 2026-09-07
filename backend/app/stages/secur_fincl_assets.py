"""Secur & Other Fincl Assets (CP) / (LTP) — writes services.secur_fincl_assets's computed values
onto the two balance-sheet LineItems those canonical keys name.

Runs alongside DeprecImpairmentStage: after notes are linked and units normalized, before
reconcile, so a tie-out sees the computed figure rather than a blank cell.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext
from app.services.computed_paths import apply_computed, policy_from
from app.services.secur_fincl_assets import CP_KEY, LTP_KEY, SecurResult, compute


def _apply(doc: DocumentModel, canonical_key: str, basis: str, period_label: str,
          result, next_ordinal: list[int], policy, log=None) -> None:
    """Hand the derived figure to the two-path policy — see services.computed_paths.

    This used to write unconditionally, which made the complex path win over the
    rulebook's own reading with no record and no way to choose. The decision now lives in
    one place for all five derivations.
    """
    apply_computed(doc, canonical_key=canonical_key, basis=basis,
                   period_label=period_label, value=result.value,
                   formula=result.formula_used, service="secur_fincl_assets",
                   evidence=result.evidence, flags=list(result.flags),
                   next_ordinal=next_ordinal, policy=policy, log=log)


class SecurFinclAssetsStage:
    name = "secur_fincl_assets"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        keys = {c.get("canonical_key") for st in (template or {}).get("statements", []) or []
                for sec in st.get("sections", []) or [] for c in (sec.get("children") or [])}
        if CP_KEY not in keys and LTP_KEY not in keys:
            ctx.log("secur_fincl_assets:skipped(template does not declare either field)")
            return doc
        if not doc.notes:
            ctx.log("secur_fincl_assets:skipped(no notes extracted)")
            return doc

        # THE COMPLEX PATH IS SWITCHABLE. Off, the rulebook's own reading of these
        # concepts is what publishes — see services.computed_paths.
        policy = policy_from(ctx.settings)
        if not policy.runs("secur_fincl_assets"):
            ctx.log("secur_fincl_assets:skipped(complex path disabled)")
            return doc

        results = compute(doc)
        next_ordinal = [max((li.ordinal for li in doc.line_items), default=0) + 1]
        applied = 0
        for (basis, period_label), fields in results.items():
            for canonical_key, result in ((CP_KEY, fields["cp"]), (LTP_KEY, fields["ltp"])):
                if result.value is None:
                    continue
                _apply(doc, canonical_key, basis, period_label, result, next_ordinal,
                       policy, ctx.log)
                applied += 1
        ctx.log(f"secur_fincl_assets:{applied} value(s) computed")
        return doc
