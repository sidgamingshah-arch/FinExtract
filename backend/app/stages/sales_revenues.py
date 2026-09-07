"""Sales (Revenues) — writes services.sales_revenues's Priority 2 (note) fallback onto the is_pl
LineItem for a (basis, period) that carries no Priority 1 reading of its own.

Runs alongside the other computed-field stages: after notes are linked and units normalized,
before reconcile. Unlike DeprecImpairmentStage/SecurFinclAssetsStage/RelatedPartyReceivablesStage,
this concept is genuinely printed on the face most of the time — Priority 1 is already satisfied by
the alias-matching mapper before this stage runs, and a genuine P1 reading is never overwritten.

A READING IS NOT PRIORITY 1 JUST BECAUSE SOMETHING BOUND IT. §2 names the P1 caption — the
主营业务 pair, or the English Turnover/Revenue/Sales — and §4 forbids total 营业收入 outright, so a
face reading under a caption like 一、营业总收入 or 其中：营业收入 is the figure the spec refuses
rather than a weaker answer to defer to. This stage used to skip any (basis, period) that already
held a value, whoever put it there, so a single such binding discarded the correct figure this
module had already computed: 1,603,146,551.95 published against a correct 1,589,859,743.31 on Sun
Create Electronics. Worse, TWO face captions carry that total on one filing — 一、营业总收入 and
其中：营业收入, the total and its own "of which" restatement — so when both were bound the concept
published the same figure twice.

The rulebook's ``exclude_hints`` refuse those captions at every mapping tier, and that is where
the prohibition belongs. It is not where the prohibition can be RELIED on: the rulebook in force
is a database row, and a run against a stored rulebook older than the shipped file binds them
again with nothing to say so. This stage therefore reads the spec itself, and holds whatever the
mapper managed to bind to it — the concept's figure does not depend on which rulebook version a
machine happens to have seeded.

A refused reading is UNBOUND rather than merely overwritten: it is a real printed line (total
operating revenue is a genuine figure, just not this concept's), so it goes to
``face_mapping_contract`` for its own engine key and lands in the review queue as a caption
nothing could place — which is what an unbound face row does everywhere else.
"""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.services.computed_paths import apply_computed, policy_from
from app.services.derivation import build, input_from_evidence, record
from app.services.sales_revenues import (
    SALES_REVENUES_KEY,
    compute_note_fallback,
    has_revenue_note,
    is_priority_one_caption,
)


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

        # THE COMPLEX PATH IS SWITCHABLE. Off, the rulebook's own reading of these
        # concepts is what publishes — see services.computed_paths.
        policy = policy_from(ctx.settings)
        if not policy.runs("sales_revenues"):
            ctx.log("sales_revenues:skipped(complex path disabled)")
            return doc

        results = compute_note_fallback(doc)

        # WHAT THE MAPPER BOUND, split by whether the spec accepts the caption as Priority 1.
        # Only on a filing that carries a 营业收入 note: with no note there is nothing to displace
        # a face reading with, and this spec has no jurisdiction over that filing's captions.
        bound = [li for li in doc.line_items if li.canonical_key == SALES_REVENUES_KEY]
        # UNBINDING IS THE COMPLEX PATH'S MOST AGGRESSIVE ACT — it discards a reading the mapper
        # made from a caption the filing really printed — so it is the first thing the precedence
        # has to govern. Under `generic` or `corroborate` the printed reading is what publishes,
        # and throwing it away would make those settings mean nothing for this concept: §4's
        # refusal becomes a review flag instead of a deletion.
        may_unbind = policy.precedence_for(SALES_REVENUES_KEY) == "complex"
        refused = ([li for li in bound if not is_priority_one_caption(li.source_label)]
                   if has_revenue_note(doc) and may_unbind else [])
        if not may_unbind and has_revenue_note(doc):
            kept = [li for li in bound if not is_priority_one_caption(li.source_label)]
            for li in kept:
                for flag in ("spec_refused_caption", "requires_concept_review"):
                    if flag not in li.confidence.flags:
                        li.confidence.flags.append(flag)
            if kept:
                ctx.log("sales_revenues:kept "
                        f"{[(li.source_label or '').strip() for li in kept]} — §4 refuses these "
                        "captions but the precedence is not `complex`, so they are flagged for "
                        "review rather than unbound")
        for li in refused:
            bound.remove(li)
            li.canonical_key = None
            li.confidence.mapping = 0.0
            for flag in ("spec_refused_caption", "requires_concept_review"):
                if flag not in li.confidence.flags:
                    li.confidence.flags.append(flag)
        if refused:
            ctx.log("sales_revenues:unbound "
                    f"{[(li.source_label or '').strip() for li in refused]} — §4 refuses a face "
                    "caption that is not 主营业务/主营业务收入 or the English equivalent")

        row = next(iter(bound), None)
        # Every slot a SURVIVING reading covers, across all of them: a filing may print the P1
        # caption twice (a consolidated face and a standalone one), and reading the slots off
        # only the first row would overwrite the second with the note figure.
        covered = {(ev.basis.value, ev.period_label or "")
                   for li in bound for ev in li.values.values()}
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
