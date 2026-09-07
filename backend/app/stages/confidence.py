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

# THE BALANCE-SHEET IDENTITY'S OPERANDS, as a list of spellings rather than one.
#
# THIS CHECK WAS DEAD. It was written against `bs_total_assets` /
# `bs_total_equity_and_liabilities`, which is the LEGACY 183-concept rulebook's spelling. Measured
# on what actually ships: neither key occurs in the 462-concept rulebook or in the 475-definition
# line-item set — the live spellings are `bs_ca__total_assets` and
# `bs_cl__total_equity_and_liabilities`. So `by_key.get(_ASSETS, [])` returned an empty list on
# every document, the loop body never ran, no `validation` signal was set, no `balance_mismatch`
# was ever raised — and nothing said so. Assets = equity + liabilities is the most basic check
# there is on a balance sheet, and it has been silently absent.
#
# A LIST, because the rulebook that supplies the keys is swappable and each generation spells them
# differently; the identity is the same identity. First spelling present in the document wins, and
# `_resolve_operands` below reports when NONE is present instead of returning quietly.
_ASSET_KEYS: tuple[str, ...] = ("bs_ca__total_assets", "bs_total_assets")
_EQ_LIAB_KEYS: tuple[str, ...] = ("bs_cl__total_equity_and_liabilities",
                                  "bs_total_equity_and_liabilities")


def _resolve_operands(by_key: dict) -> tuple[str | None, str | None]:
    """The first spelling of each operand the document actually carries.

    RETURNS None RATHER THAN GUESSING, and the caller flags that rather than skipping quietly.
    The failure this replaces was not a wrong answer — it was silence: a protective check whose
    operands had been renamed out from under it, doing nothing, indistinguishable from a filing
    whose balance sheet happens not to state a total.
    """
    assets = next((k for k in _ASSET_KEYS if by_key.get(k)), None)
    eq_liab = next((k for k in _EQ_LIAB_KEYS if by_key.get(k)), None)
    return assets, eq_liab


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
        # The tolerance is the SAME knob the rest of the reconciliation uses. It was a private
        # `Decimal(1)` here while `extraction.recon_abs_tolerance` — read at six other sites and
        # exposed to the operator on the Settings screen — held the identical default. A second
        # copy is not a missing setting, it is a duplicated one: move the knob and this check
        # keeps its own answer.
        tol = Decimal(str(getattr(ctx.settings.extraction, "recon_abs_tolerance", 1)))
        assets_key, eq_liab_key = _resolve_operands(by_key)
        if not (assets_key and eq_liab_key):
            # SAY SO. The old code's empty `by_key.get(...)` made a renamed operand look exactly
            # like a filing that states no total, so the check's absence was unobservable.
            missing = [name for name, key in (("total_assets", assets_key),
                                              ("total_equity_and_liabilities", eq_liab_key))
                       if not key]
            ctx.log(f"confidence:balance_identity_not_checked:missing={'+'.join(missing)}:"
                    f"looked_for={'|'.join(_ASSET_KEYS)}/{'|'.join(_EQ_LIAB_KEYS)}")
        else:
            for assets in by_key.get(assets_key, []):
                for ev in assets.values.values():
                    match = next((e for eqliab in by_key.get(eq_liab_key, [])
                                  for e in eqliab.values.values()
                                  if e.basis == ev.basis
                                  and e.period_label == ev.period_label), None)
                    a, e = _raw(ev), (_raw(match) if match else None)
                    if a is None or e is None:
                        continue
                    ok = abs(Decimal(a) - Decimal(e)) <= tol
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
