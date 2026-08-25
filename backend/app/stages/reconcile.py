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
from app.core.models.reports import ReconciliationEntry, ReconciliationReport
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


class ReconcileStage:
    name = "reconcile"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        ex = ctx.settings.extraction
        tol_abs = Decimal(str(ex.recon_abs_tolerance))
        tol_rel = Decimal(str(ex.recon_rel_tolerance))
        corroboration = Decimal(str(getattr(ex, "recon_corroboration_rel", "0.05")))

        report = ReconciliationReport()
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
