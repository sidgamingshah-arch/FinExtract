"""Secur & Other Fincl Assets (CP) / (LTP) — writes services.secur_fincl_assets's computed values
onto the two balance-sheet LineItems those canonical keys name.

Runs alongside DeprecImpairmentStage: after notes are linked and units normalized, before
reconcile, so a tie-out sees the computed figure rather than a blank cell.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services.derivation import build, input_from_evidence, record
from app.services.secur_fincl_assets import CP_KEY, LTP_KEY, SecurResult, compute


def _apply(doc: DocumentModel, canonical_key: str, basis: str, period_label: str,
          result: SecurResult, next_ordinal: list[int]) -> None:
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
    row.confidence.method = f"computed:secur_fincl_assets:{result.formula_used}"
    row.derivation = record(
        row.derivation, basis=basis, period_label=period_label,
        derivation=build(method="secur_fincl_assets", formula=result.formula_used,
                         inputs=[input_from_evidence(e) for e in result.evidence],
                         result=result.value, flags=result.flags))
    for flag in result.flags:
        if flag not in row.confidence.flags:
            row.confidence.flags.append(flag)


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

        results = compute(doc)
        next_ordinal = [max((li.ordinal for li in doc.line_items), default=0) + 1]
        applied = 0
        for (basis, period_label), fields in results.items():
            for canonical_key, result in ((CP_KEY, fields["cp"]), (LTP_KEY, fields["ltp"])):
                if result.value is None:
                    continue
                _apply(doc, canonical_key, basis, period_label, result, next_ordinal)
                applied += 1
        ctx.log(f"secur_fincl_assets:{applied} value(s) computed")
        return doc
