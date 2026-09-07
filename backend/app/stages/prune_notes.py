"""Publish only the notes the face statements actually point to.

A filing's notes section covers far more than the statements: accounting policies, segment
commentary, governance tables, subsequent events. Only the notes a face line REFERENCES
explain a published figure, and those are the only ones this product is asked to deliver —
an unreferenced note is noise in the notes index, the export and the review queue.

Runs after reconciliation on purpose: the note→face subtraction checks need every note that
was extracted, so pruning earlier would weaken the reconciliation that decides which figures
are trustworthy. Pruning here means one filter governs every consumer of ``doc.notes``
(extraction result, notes index, Excel export) rather than each re-deriving the rule.

Nothing is deleted from the source document or from provenance — this only decides what is
PUBLISHED, and the log records exactly what was dropped so a missing note is explainable.

The rule needs the face to POINT at something. A mainland filing need not print a 附注 column
beside its statement lines, and where it does not there is no evidence to prune on — so nothing is
pruned, rather than everything (see the ``not wanted`` branch, and ``_is_face_item`` for the same
answer to the same question about a filing with no classifiable face page).
"""
from __future__ import annotations

from app.core.models import DocumentModel
from app.core.models.line_item import base_note_number
from app.core.stage import PipelineContext


def _is_face_item(li, doc: DocumentModel) -> bool:
    """Whether a line item was printed on a statement face page.

    When no page was classified as a face (a filing we could not classify), every item counts
    — failing closed there would publish nothing at all.
    """
    face_pages = {p.index for p in doc.face_pages()}
    if not face_pages:
        return True
    pages = {ev.provenance.page_index for ev in li.values.values()
             if ev.provenance is not None}
    return not pages or bool(pages & face_pages)


def _face_note_numbers(doc: DocumentModel) -> set[str]:
    """Note numbers referenced from the face of the statements, and the parent of each.

    Which notes a row cites is ``LineItem.cited_notes`` — the one definition the linker and the
    section segmentation also use, so what gets PUBLISHED here cannot disagree with what gets
    linked and filed. Unlike those two this is a set of names, not a resolution against the notes
    that exist: it is compared against every note's number below, and it is also the fallback for
    a run where linking was skipped, so it must not depend on the linker having run.

    A SUB-REFERENCE ALSO KEEPS ITS PARENT: a row citing "12(a)" is explained by the note table
    numbered "12", and dropping that table would leave the figure with nothing behind it. Both
    names are kept because a filing may number the table either way. ``note_number`` is added
    unconditionally rather than as a fallback — a row synthesised from a note item names its note
    there and nowhere else, and pruning that note would delete the row's own source.
    """
    wanted: set[str] = set()
    for li in doc.line_items:
        # Items extracted from a note table also carry note_number; only the face counts here.
        if not _is_face_item(li, doc):
            continue
        for token in (*li.cited_notes(), (li.note_number or "").strip()):
            if not token:
                continue
            wanted.add(token)
            if (base := base_note_number(token)):
                wanted.add(base)
    return {w for w in wanted if w}


class PruneNotesStage:
    name = "prune_notes"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        if not doc.notes:
            return doc
        if not ctx.settings.extraction.prune_unreferenced_notes:
            ctx.log(f"prune_notes:disabled kept={len(doc.notes)}")
            return doc

        # Prefer the authoritative face->note links built by the link stage: they are what
        # "linked to an item on the face" means, and they record the note table actually
        # matched. Restricted to links whose face item really sits on a face page, so a
        # note-internal cross-reference can never keep a note alive on its own.
        face_ids = {li.id for li in doc.line_items if _is_face_item(li, doc)}
        linked = {str(link.note_number).strip() for link in doc.links
                  if link.face_item_id in face_ids and link.note_number is not None}
        # Textual references are the fallback for a run where linking was skipped, and a
        # backstop for a note whose table the linker could not match.
        wanted = linked | _face_note_numbers(doc)
        if not wanted:
            # NO CITATION EVIDENCE AT ALL, which is not the same as evidence that no note is
            # wanted — and the two were being treated as one. A mainland filing need not print a
            # 附注 column beside its statement lines, and 河钢股份 000709 does not: not one of its
            # face rows carries a note reference, `link_notes` can therefore build nothing
            # (`link_type="explicit_note_ref"` is the only kind of link there is), and this branch
            # then discarded all 285 extracted note tables — the notes index, the note detail in
            # the export and the review queue's note evidence, gone, on a filing whose notes were
            # read correctly.
            #
            # So the notes are kept, and the asymmetry of the two mistakes is the argument. Keeping
            # them on a filing that genuinely cites none publishes tables the analyst did not ask
            # for: visible, and recoverable by reading past them. Dropping them on a filing that
            # prints no note column publishes nothing and says nothing about what was lost — no
            # consumer of the result can tell the notes ever existed. ``_is_face_item`` one
            # function above already decided the identical question the same way, for a document
            # with no classifiable face page: "failing closed there would publish nothing at all".
            #
            # The log names this reason rather than the old one, because "no face references" read
            # as a finding about the filing when it was a finding about the reader.
            ctx.log(f"prune_notes:no_note_column_on_the_face kept={len(doc.notes)} dropped=0")
            return doc

        kept, dropped = [], []
        for nt in doc.notes:
            number = str(nt.note_number).strip() if nt.note_number is not None else ""
            # A note whose number matches a face reference is published; so is one whose
            # number is the parent of a referenced sub-ref ("12" for a cited "12(a)").
            if number and number in wanted:
                kept.append(nt)
            else:
                dropped.append(number or "?")

        doc.notes = kept
        ctx.log(f"prune_notes:kept={len(kept)} dropped={len(dropped)}"
                + (f" dropped_notes={sorted(dropped)}" if dropped else ""))
        return doc
