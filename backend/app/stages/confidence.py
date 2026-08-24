"""Confidence + validation stage.

Sets the ``validation`` sub-signal on extracted values from the accounting checks the
pipeline can run at this point, so ``ConfidenceVector.overall`` is modulated by whether a
value participates in a check that failed (a hard balance mismatch or a note that doesn't
tie caps the score, rather than a clean OCR/mapping producing false confidence):

* balance-sheet identity — total assets == total equity and liabilities, per (basis, period)
* note→face tie          — from the reconcile stage's report

The row-based rule catalog (subtotal rollups etc.) that also feeds the review queue is run
at the API layer against the served rows; this stage handles the value-level signal.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models import DocumentModel
from app.core.stage import PipelineContext

_ASSETS = "bs_total_assets"
_EQ_LIAB = "bs_total_equity_and_liabilities"
_TOL = Decimal(1)


def _raw(ev):
    return ev.value if ev.value is not None else ev.value_raw


class ConfidenceStage:
    name = "confidence"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        # Propagate the row's mapping confidence + method onto each of its values, so the
        # confidence vector is complete per value (not just per row) and ``overall`` combines
        # mapping with the validation signal set below.
        for li in doc.line_items:
            for ev in li.values.values():
                ev.confidence.mapping = li.confidence.mapping
                ev.confidence.method = li.confidence.method

        # A LIST per key, not the last row that claimed it. Two statements in one filing print the
        # same lines — the Group's balance sheet and the Company's own — so a key is now genuinely
        # held by more than one row, and keeping only the last one meant the identity was checked
        # against whichever page came last and the other basis went unchecked entirely.
        by_key: dict[str, list] = {}
        for li in doc.line_items:
            if li.canonical_key:
                by_key.setdefault(li.canonical_key, []).append(li)

        failed = 0
        for assets in by_key.get(_ASSETS, []):
            for ev in assets.values.values():
                match = next((e for eqliab in by_key.get(_EQ_LIAB, [])
                              for e in eqliab.values.values()
                              if e.basis == ev.basis and e.period_label == ev.period_label), None)
                a, e = _raw(ev), (_raw(match) if match else None)
                if a is None or e is None:
                    continue
                ok = abs(Decimal(a) - Decimal(e)) <= _TOL
                ev.confidence.validation = 1.0 if ok else 0.4
                match.confidence.validation = 1.0 if ok else 0.4
                if not ok:
                    failed += 1
                    ev.confidence.flags.append("balance_mismatch")

        # Note→face ties that failed lower the face value's validation signal. Only a
        # corroborated breakdown that does not tie counts: an "unconfirmed" entry means the
        # cited note is not a breakdown of this figure (an analysis or segment note), which
        # says nothing about whether the figure is right.
        if doc.reconciliation is not None:
            bad = {(e.face_item_id, e.basis, e.period_label)
                   for e in doc.reconciliation.entries if e.tie_status == "untied"}
            if bad:
                for li in doc.line_items:
                    for ev in li.values.values():
                        if (str(li.id), ev.basis.value, ev.period_label) in bad:
                            prior = ev.confidence.validation
                            ev.confidence.validation = min(0.5, prior if prior is not None else 1.0)
                            ev.confidence.flags.append("note_untied")
                            failed += 1

        ctx.log(f"confidence:line_items={len(doc.line_items)} validation_failures={failed}")
        return doc
