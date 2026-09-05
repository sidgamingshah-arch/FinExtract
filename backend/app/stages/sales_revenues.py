"""Sales (Revenues) — writes services.sales_revenues's Priority 2 (note) fallback onto the is_pl
LineItem, but only for a (basis, period) the ordinary mapper left with no value at all.

Runs alongside the other computed-field stages: after notes are linked and units normalized,
before reconcile. Unlike DeprecImpairmentStage/SecurFinclAssetsStage/RelatedPartyReceivablesStage,
this concept is genuinely printed on the face most of the time — Priority 1 is already satisfied by
the alias-matching mapper before this stage runs, so this stage must never overwrite that reading.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services.derivation import build, input_from_evidence, record
from app.services.sales_revenues import SALES_REVENUES_KEY, compute_note_fallback


class SalesRevenuesStage:
    name = "sales_revenues"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        keys = {c.get("canonical_key") for st in (template or {}).get("statements", []) or []
                for sec in st.get("sections", []) or [] for c in (sec.get("children") or [])}
        if SALES_REVENUES_KEY not in keys:
            ctx.log("sales_revenues:skipped(template does not declare the field)")
            return doc
        if not doc.notes:
            ctx.log("sales_revenues:skipped(no notes extracted)")
            return doc

        row = next((li for li in doc.line_items if li.canonical_key == SALES_REVENUES_KEY), None)
        covered = {(ev.basis.value, ev.period_label or "") for ev in (row.values.values() if row else [])}

        results = compute_note_fallback(doc)
        applied = 0
        for (basis, period_label), result in results.items():
            if (basis, period_label) in covered or result.value is None:
                continue
            if row is None:
                row = LineItem(source_label=SALES_REVENUES_KEY, canonical_key=SALES_REVENUES_KEY,
                               ordinal=max((li.ordinal for li in doc.line_items), default=0) + 1)
                doc.line_items.append(row)
            src_prov = next((e["provenance"] for e in result.evidence if e.get("provenance")), None)
            row.set_value(ExtractedValue(value=result.value, value_raw=result.value,
                                         basis=Basis(basis), period_label=period_label,
                                         provenance=src_prov))
            row.confidence.method = f"computed:sales_revenues:{result.priority_used}"
            row.derivation = record(
                row.derivation, basis=basis, period_label=period_label,
                derivation=build(
                    method="sales_revenues",
                    # P2 is the only priority this module supplies: P1 is the face caption, read
                    # by the ordinary mapper before this stage runs.
                    formula="P2 · the 主营业务/主营业务收入 row of the 营业收入 note",
                    inputs=[input_from_evidence(e) for e in result.evidence],
                    result=result.value, flags=result.flags))
            for flag in result.flags:
                if flag not in row.confidence.flags:
                    row.confidence.flags.append(flag)
            applied += 1
        ctx.log(f"sales_revenues:{applied} value(s) filled from the note fallback")
        return doc
