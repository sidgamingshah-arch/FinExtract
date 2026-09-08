"""Due from Related Parties (LTP) / Other Receivables (CP) — writes
services.related_party_receivables's computed values onto the two balance-sheet LineItems those
canonical keys name.

Runs alongside DeprecImpairmentStage/SecurFinclAssetsStage: after notes are linked and units
normalized, before reconcile, so a tie-out sees the computed figure rather than a blank cell.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.stage import PipelineContext
from app.services.computed_paths import apply_computed, policy_from
from app.services.related_party_receivables import (CP_KEY, LTP_KEY, ReceivablesResult, compute)


def _apply(doc: DocumentModel, canonical_key: str, basis: str, period_label: str,
          result, next_ordinal: list[int], policy, log=None) -> None:
    """Hand the derived figure to the two-path policy — see services.computed_paths.

    This used to write unconditionally, which made the complex path win over the
    rulebook's own reading with no record and no way to choose. The decision now lives in
    one place for all five derivations.
    """
    apply_computed(doc, canonical_key=canonical_key, basis=basis,
                   period_label=period_label, value=result.value,
                   formula=result.formula_used, service="related_party_receivables",
                   evidence=result.evidence, flags=list(result.flags),
                   next_ordinal=next_ordinal, policy=policy, log=log)


class RelatedPartyReceivablesStage:
    name = "related_party_receivables"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        keys = {c.get("canonical_key") for st in (template or {}).get("statements", []) or []
                for sec in st.get("sections", []) or [] for c in (sec.get("children") or [])}
        if LTP_KEY not in keys and CP_KEY not in keys:
            ctx.log("related_party_receivables:skipped(template does not declare either field)")
            return doc
        if not doc.notes and not doc.line_items:
            ctx.log("related_party_receivables:skipped(nothing to search)")
            return doc

        # THE COMPLEX PATH IS SWITCHABLE, as it is in the other four derivation stages. Off, the
        # rulebook's own reading of these concepts is what publishes — see services.computed_paths.
        #
        # THIS ASSIGNMENT WAS MISSING and it crashed a real filing. `_apply` below takes `policy`
        # as an argument, the import was present, and the four sibling stages all assign it here —
        # this one did not, so the call raised `NameError: name 'policy' is not defined`. It lay
        # dormant because the crash is only reachable once a value is actually COMPUTED: every
        # (basis, period) whose result is None hits the `continue` above it. On the 四创电子 filing
        # the first two keys reported NOT_COMPUTABLE, a later one computed, and the whole
        # 210-page extraction died at stage 15 of 21 with no rows at all.
        policy = policy_from(ctx.settings)
        if not policy.runs("related_party_receivables"):
            ctx.log("related_party_receivables:skipped(complex path disabled)")
            return doc

        results = compute(doc)
        next_ordinal = [max((li.ordinal for li in doc.line_items), default=0) + 1]
        applied = 0
        seen: set[tuple[str, str, str]] = set()
        for (basis, period_label), fields in results.items():
            for canonical_key, result in ((LTP_KEY, fields["ltp"]), (CP_KEY, fields["cp"])):
                if result.value is None:
                    # A blank cell is often the CORRECT answer here (the filing discloses no
                    # related-party amount inside any admissible receivable class), but silence
                    # cannot distinguish "searched and genuinely absent" from "never searched" —
                    # so the service's own status and flags are reported rather than dropped. The
                    # value is deliberately still not written: this is the diagnosis, not a figure.
                    # De-duplicated because compute() runs once per (basis, period) key and a real
                    # filing yields dozens of them, which would otherwise flood the run log.
                    key = (canonical_key, result.status, "|".join(result.flags))
                    if key not in seen:
                        seen.add(key)
                        ctx.log(f"related_party_receivables:{canonical_key}"
                                f":not_computed(status={result.status}"
                                f" flags={'|'.join(result.flags) or 'none'})")
                    continue
                _apply(doc, canonical_key, basis, period_label, result, next_ordinal,
                       policy, ctx.log)
                applied += 1
        ctx.log(f"related_party_receivables:{applied} value(s) computed")
        return doc
