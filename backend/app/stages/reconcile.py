"""Reconciliation stage (§20) — applies the note→face subtraction.

For each face line that cites a note, per (basis, period), it subtracts the note detail lines
that are *themselves* ingested as distinct template lines (avoiding double counting), writes
the reconciled figure onto the face ``ExtractedValue.reconciled`` (always derived from the raw
face value, so re-running is idempotent), and records a ``ReconciliationEntry``.

It also grades how well the note total ties back to the face figure. Three things make that
grading non-obvious, and getting them wrong floods the review queue with non-findings:

* **A cited note is usually not a breakdown of the face figure.** "Profit before tax" lists
  selected items charged and credited; a segment note analyses by division; a commitments note
  is a schedule. None of them sum to the face line. Only a genuine decomposition ties, so a
  residual far from the face figure is graded ``unconfirmed`` rather than asserted as a
  mismatch — see ``services.reconcile``.
* **One note number can span several tables** (continuation pages, sub-analyses). Emitting one
  entry per table multiplies the same question. Exactly one entry per
  (face line, note, basis, period) is recorded, using whichever table corroborates best.
* **Not every extracted column is a reported period.** A third or fourth numeric column
  (maturity dates, coupon rates, an entity column) is not comparable to a face figure, so only
  the reported periods take part in the tie.

The arithmetic core lives in ``services.reconcile`` (pure, unit-tested); this stage wires it
to the document model.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.models import DocumentModel
from app.core.models.enums import LineRole
from app.core.models.reports import (
    NoteBlockSubtotalCheck,
    ReconciliationEntry,
    ReconciliationReport,
)
from app.core.stage import PipelineContext
from app.services.reconcile import (
    TIE_TIED,
    TIE_UNTIED,
    NoteDetail,
    ReconcileInput,
    reconcile_face,
    tolerance,
)

# The reported periods a face figure can be tied against. Extraction also emits positional
# columns ("col2", "col3", …) for tables with extra numeric columns; those are not periods
# and comparing a note total to one of them is meaningless.
_TIE_PERIODS = ("current", "prior")


def _note_value(item, basis, period_label) -> Decimal | None:
    """A note detail item's value for one (basis, period)."""
    for ev in item.values.values():
        if ev.basis == basis and ev.period_label == period_label:
            return ev.value if ev.value is not None else ev.value_raw
    return None


def _check_block_subtotals(doc, report: ReconciliationReport, tol_abs, tol_rel, log) -> None:
    """Check every subtotal a note printed on a bare line against the rows it totals.

    THE ONE CHECK THAT NEEDS NO TEMPLATE AND NO MAPPING. A template rollup compares a face subtotal
    to the components the rulebook declares; a note tie compares a note's total to a face figure.
    Both need extraction to have RECOGNISED the lines first. This compares a figure the filing
    printed to the figures printed directly above it, inside one note — so it corroborates numbers
    the rulebook has never heard of, which is most of what a note contains.

    It matters most exactly where §20 reads a note. That split re-derives a block's total by summing
    the block's detail rows, and nothing compared that sum to the total the filing printed underneath
    them: agreement was assumed. On the measured filing they do agree, and now that is a verified
    fact rather than a coincidence nobody checked.

    THE MEMBERS ARE TOLD TO US, NOT GUESSED. `component_ordinals` is recorded by the reconstruction
    that promoted the row, off the rows it actually counted. Re-deriving them here from `group_hint`
    equality was tried and was wrong twice over: a sub-heading's scope is not closed at the end of
    its block, so rows printed AFTER the block still carry it and were pooled into the sum; and two
    blocks in one note can carry the SAME heading (a "Current tax:" under Group and another under
    Company), so the caption is not an identity in the first place. Both produced a "does not add up"
    assertion on a note that adds up, which is worse than not checking.

    ONLY A ROW WHOSE CAPTION WAS BORROWED from its block, which is the only row claiming to be the
    sum of the rows above it. A note's SUBTOTAL role also arrives from `notes_extract.note_row_role`,
    read off the printed caption — and a row the filing captioned "Sub-total" inside an effective-rate
    RECONCILIATION is not a sum of anything: that table derives a charge from profit before tax, so
    summing the rows above it produces a difference of the whole profit figure. A schedule that
    derives is not a schedule that adds.

    RUN BEFORE THE ``doc.links`` EARLY EXIT, and this is not incidental. A note's internal arithmetic
    is checkable whether or not any face line happens to cite that note — a filing whose face cites
    nothing still publishes notes that add up or do not. Putting this after the exit would silently
    skip every note in exactly the runs where the face gave us least.
    """
    for note in doc.notes:
        by_ordinal = {it.ordinal: it for it in note.items}
        for it in note.items:
            if it.role is not LineRole.SUBTOTAL or not it.caption_borrowed:
                continue
            components = [by_ordinal[o] for o in it.component_ordinals if o in by_ordinal]
            if len(components) < 2:
                # Nothing to check it against — the rows were pruned, or the builder counted fewer
                # than it filed. Not reported as a pass: a check that could not run must never read
                # as one that ran and held.
                continue
            for ev in it.values.values():
                printed = ev.value if ev.value is not None else ev.value_raw
                if printed is None or ev.period_label not in _TIE_PERIODS:
                    continue
                parts = [_note_value(c, ev.basis, ev.period_label) for c in components]
                parts = [v for v in parts if v is not None]
                # EVERY component must carry this column, not merely some. A block whose rows are
                # ragged in a period — one row printing a figure only for the prior year — has no
                # sum to compare, and a partial sum would report a break the page does not contain.
                if len(parts) != len(components):
                    continue
                printed_d, computed = Decimal(printed), sum(parts)
                diff = printed_d - computed
                report.note_block_subtotals.append(NoteBlockSubtotalCheck(
                    note_number=note.note_number or "", block=it.group_hint or "",
                    basis=ev.basis.value, period_label=ev.period_label,
                    printed=printed_d, computed=computed, difference=diff,
                    within_tolerance=abs(diff) <= tolerance(printed_d, tol_abs, tol_rel),
                    component_count=len(components)))

    checks = report.note_block_subtotals
    broken = [c for c in checks if not c.within_tolerance]
    for c in broken:
        report.failed_assertions.append(
            f"Note {c.note_number} block {c.block!r} does not add up "
            f"({c.basis}/{c.period_label}): printed {c.printed}, components sum to {c.computed} "
            f"(difference {c.difference})")
    if log and checks:
        # Counted separately from the note→face ties below. They are different claims, and one
        # number covering both would let a run with no face ties at all read as validated.
        log(f"reconcile:block_subtotals={len(checks)} broken={len(broken)}")


class ReconcileStage:
    name = "reconcile"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        ex = ctx.settings.extraction
        tol_abs = Decimal(str(ex.recon_abs_tolerance))
        tol_rel = Decimal(str(ex.recon_rel_tolerance))
        corroboration = Decimal(str(getattr(ex, "recon_corroboration_rel", "0.05")))

        report = ReconciliationReport()
        # BEFORE the early exit: see `_check_block_subtotals`. A note either adds up or it does
        # not, and that is true of a filing whose face cites no note at all.
        _check_block_subtotals(doc, report, tol_abs, tol_rel, ctx.log)
        if not doc.links:
            doc.reconciliation = report
            ctx.log("reconcile:no_links")
            return doc

        face_by_id = {li.id: li for li in doc.line_items}
        note_by_id = {nt.id: nt for nt in doc.notes}
        # Canonical keys that appear as their OWN face line — a note detail mapping to one of
        # these is double-counted and must be subtracted from the aggregate.
        distinct_face_keys = {li.canonical_key for li in doc.line_items if li.canonical_key}

        # One question per (face line, note number): a note spanning several tables must not
        # ask it once per table.
        tables_by_link: dict[tuple, list] = {}
        for link in doc.links:
            note = note_by_id.get(link.notes_table_id)
            if note is None or link.face_item_id not in face_by_id:
                continue
            tables_by_link.setdefault((link.face_item_id, link.note_number), []).append(
                (note, link))

        def _details(face, note, ev) -> list[NoteDetail]:
            details: list[NoteDetail] = []
            for it in note.items:
                if it.role in (LineRole.SUBTOTAL, LineRole.TOTAL):
                    continue                      # a note's own subtotal isn't a detail
                dv = _note_value(it, ev.basis, ev.period_label)
                if dv is None:
                    continue
                maps = bool(it.canonical_key and it.canonical_key in distinct_face_keys
                            and it.canonical_key != face.canonical_key)
                details.append(NoteDetail(item_id=str(it.id), value=Decimal(dv),
                                          maps_to_distinct_template_line=maps))
            return details

        def _orientation(face, note) -> int:
            """Which sign convention this note's figures are in, relative to this face line's.

            A note is a schedule OF a charge and prints it positive; a P&L presents the same charge
            as a deduction and prints it negative. Comparing the two without establishing which is
            which reported a residual of twice the figure on a note that reconciles exactly, and
            graded it ``unconfirmed`` — so the tie said "this note is not a breakdown of that
            figure" about a note that is one, which is the opposite of what this grading is for.

            ONE ORIENTATION FOR ALL OF THIS FACE LINE'S PERIODS, decided here rather than inside
            the arithmetic, because a single period gives a flip nothing to be consistent with and
            would let it absorb any sign error. Requiring every period to tie under the SAME
            orientation makes it a claim about presentation, which a per-period error cannot meet.
            The default stays ``1``, so a note that does not reconcile either way is graded exactly
            as before.
            """
            for orientation in (1, -1):
                ties = 0
                for ev in face.values.values():
                    raw = ev.value if ev.value is not None else ev.value_raw
                    if raw is None or ev.period_label not in _TIE_PERIODS:
                        continue
                    details = _details(face, note, ev)
                    if not details:
                        return 1
                    total = sum(d.value for d in details) * orientation
                    if abs(Decimal(raw) - total) > tolerance(Decimal(raw), tol_abs, tol_rel):
                        ties = 0
                        break
                    ties += 1
                if ties:
                    return orientation
            return 1

        def _outcome(face, note, ev, orientation):
            """Reconcile one face value against one note table (None when the table has no
            comparable detail for this basis/period)."""
            details = _details(face, note, ev)
            if not details:
                return None
            raw = ev.value if ev.value is not None else ev.value_raw
            return reconcile_face(ReconcileInput(
                note_orientation=orientation,
                face_item_id=str(face.id), note_number=note.note_number or "",
                raw_face_value=Decimal(raw), details=details,
                # From configuration, not the dataclass defaults — these are tunable from the
                # Settings screen, and a knob that does not reach the code it names is worse
                # than no knob at all.
                tolerance_abs=tol_abs, tolerance_rel=tol_rel,
                corroboration_rel=corroboration))

        for (face_id, note_number), pairs in tables_by_link.items():
            face = face_by_id[face_id]
            # Established per (face line, table) BEFORE any period is graded, so every period of
            # one face line is compared under the same convention.
            orientations = {id(note): _orientation(face, note) for note, _link in pairs}
            for ev in face.values.values():
                raw = ev.value if ev.value is not None else ev.value_raw
                if raw is None or ev.period_label not in _TIE_PERIODS:
                    continue
                # The table that corroborates best is the one this face figure is broken
                # down by; the rest are other tables that happen to share the note number.
                best, best_link = None, None
                for note, link in pairs:
                    out = _outcome(face, note, ev, orientations[id(note)])
                    if out is None:
                        continue
                    if best is None or abs(out.residual) < abs(best.residual):
                        best, best_link = out, link
                if best is None:
                    continue
                # Only a corroborated breakdown may restate the face figure; otherwise the
                # reported value stands untouched.
                if best.tie_status in (TIE_TIED, TIE_UNTIED):
                    ev.reconciled = best.reconciled
                report.entries.append(ReconciliationEntry(
                    face_item_id=str(face.id),
                    # One entry per FACE LINE, so the entry has to say which face line — by a
                    # name that means the same thing next run. The review queue serves one card
                    # per (note, basis, period) covering every untied face line on that note, and
                    # it can only list them (and fingerprint them) if they are named.
                    face_key=(face.canonical_key or face.source_label or ""),
                    note_number=note_number,
                    basis=ev.basis.value, period_label=ev.period_label,
                    raw_face=best.raw_face, subtracted=best.subtracted,
                    reconciled=best.reconciled, residual=best.residual,
                    within_tolerance=best.within_tolerance, tie_status=best.tie_status,
                    relationship=best_link.relationship.value))
                if best.tie_status == TIE_UNTIED:
                    report.failed_assertions.append(
                        f"Note {note_number} does not tie to "
                        f"{face.canonical_key or face.source_label} "
                        f"({ev.basis.value}/{ev.period_label}): residual {best.residual}")

        doc.reconciliation = report
        graded = {s: sum(1 for e in report.entries if e.tie_status == s)
                  for s in ("tied", "untied", "unconfirmed")}
        ctx.log(f"reconcile:entries={len(report.entries)} tied={graded['tied']} "
                f"untied={graded['untied']} unconfirmed={graded['unconfirmed']}")
        return doc
