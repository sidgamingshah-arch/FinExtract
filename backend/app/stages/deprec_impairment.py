"""Deprec & Impairment (Oper Exp) / (COS) — writes services.deprec_impairment's computed values
onto the two is_pl LineItems those canonical keys name.

Runs after notes are extracted, normalized and linked (the computation reads note-level
depreciation lines directly, and needs them in the document's single normalized unit), and before
reconcile/structural checks, so a tie-out sees the computed figure rather than a blank cell.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services.derivation import build, input_from_evidence, record
from app.services.deprec_impairment import (COS_FORMULA, COS_KEY, OPER_EXP_FORMULA, OPER_EXP_KEY,
                                            DeprecResult, compute)


def _apply(doc: DocumentModel, canonical_key: str, basis: str, period_label: str,
          result: DeprecResult, next_ordinal: list[int]) -> None:
    if result.value is None:
        return
    row = next((li for li in doc.line_items if li.canonical_key == canonical_key), None)
    if row is None:
        row = LineItem(source_label=canonical_key, canonical_key=canonical_key,
                       ordinal=next_ordinal[0])
        next_ordinal[0] += 1
        doc.line_items.append(row)
    src_prov = next((e["provenance"] for e in result.evidence if e.get("provenance")), None)
    ev = ExtractedValue(value=result.value, value_raw=result.value,
                        basis=Basis(basis), period_label=period_label,
                        provenance=src_prov)
    row.set_value(ev)
    row.confidence.method = f"computed:deprec_impairment:{result.priority_used}"
    formulae = COS_FORMULA if canonical_key == COS_KEY else OPER_EXP_FORMULA
    row.derivation = record(
        row.derivation, basis=basis, period_label=period_label,
        derivation=build(method="deprec_impairment",
                         formula=formulae.get(result.priority_used or "", result.priority_used),
                         inputs=[input_from_evidence(e) for e in result.evidence],
                         result=result.value, flags=result.flags))
    for flag in result.flags:
        if flag not in row.confidence.flags:
            row.confidence.flags.append(flag)


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

        results = compute(doc)
        next_ordinal = [max((li.ordinal for li in doc.line_items), default=0) + 1]
        applied = 0
        for (basis, period_label), fields in results.items():
            for canonical_key, result in ((OPER_EXP_KEY, fields["oper_exp"]), (COS_KEY, fields["cos"])):
                if result.value is None:
                    continue
                _apply(doc, canonical_key, basis, period_label, result, next_ordinal)
                applied += 1
        ctx.log(f"deprec_impairment:{applied} value(s) computed")
        return doc
