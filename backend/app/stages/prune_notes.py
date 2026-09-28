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


def _sourced_note_numbers(doc: DocumentModel) -> set[str]:
    """Note numbers a line's derivation trail names — the notes its figure was READ FROM.

    THE FACE IS NOT THE ONLY THING THAT POINTS AT A NOTE. A part filled from a note (`note_source`,
    or a row the model cited) records the note on every input of its trail, and that note is where
    a reviewer clicks through to check the figure. The face points at the note that EXPLAINS a
    line; the trail points at the note a figure was TAKEN from, and the two differ exactly where
    the note-sourced routes earn their keep: a mainland related-party table is printed under its
    chapter's own number and no face row cites it, so it was dropped here — after its figures had
    been published from it — and the trail behind 688008's 应收账款 英特尔公司 balance pointed at a
    note the result did not contain.

    Every input, not only the counted ones: an alternative the rollup did not take is still shown in
    the trail, and its note is what makes that line of the trail checkable. The parent of a
    sub-reference is kept for the reason `_face_note_numbers` gives.
    """
    out: set[str] = set()
    for li in doc.line_items:
        for slot in (getattr(li, "derivation", None) or {}).values():
            for item in (slot or {}).get("inputs") or ():
                token = str((item or {}).get("note") or "").strip()
                if not token:
                    continue
                out.add(token)
                if (base := base_note_number(token)):
                    out.add(base)
    return out


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

        # …AND THE SAME ANSWER WHEN THE CITATIONS RESOLVE TO (ALMOST) NOTHING, which is the hole the
        # emptiness test below leaves open.
        #
        # THE FAILURE, measured on this corpus. A mainland statement-of-changes-in-equity page whose
        # text layer comes out of PyMuPDF in two-character shards produces face rows whose labels are
        # CJK fragments ('和减' out of 所有者投入和减少资本, '润分' out of 利润分配), and a numeric
        # shard beside one of them is read as a note reference — `'.00'` becoming note `'00'` through
        # `_note_ref_value`'s leading-dot strip, or a bare `'81'` off the tail of a shredded amount.
        # `wanted` is then NON-EMPTY and names nothing that exists, so the branch below does not fire
        # and the loop after it drops every note:
        #
        #     1223214527   172 tables   wanted=['00']              resolves to nothing   kept 0
        #     8ad0c02c     365 tables   wanted=['3']               resolves to nothing   kept 0
        #     ee164920     413 tables   wanted=['81']              resolves to nothing   kept 0
        #     3bfe0c0e     408 tables   wanted=['00','50','七、50'] resolves to one       kept 1
        #
        # ONE JUNK TOKEN DESTROYS 413 NOTE TABLES. And these were the best-read filings in the
        # corpus — they build more tables than `b09ca2c1`, which publishes all 261 of its own only
        # because it produced NO citation at all and reached the branch below. The two filings that
        # looked healthy were failing safe, not working.
        #
        # SO THE TEST IS WHETHER THE CITATIONS RESOLVE, not whether any were produced. A face note
        # column that was read correctly names notes this document has; one that was mis-read names
        # numbers nothing answers to, and that is indistinguishable — from here — from not having
        # been read at all. It is the identical question the branch below answers, and
        # `_is_face_item` answers for a document with no classifiable face page, so it gets the
        # identical answer: keep everything and say why.
        #
        # A SHARE OF A SUBSTANTIAL NOTES SECTION, not an absolute count, and the first version of
        # this guard got that wrong in a way the unit tests caught. It read
        # `len(resolved) < 3`, which is true of a two-note FIXTURE where one note is legitimately
        # referenced — so the guard fired on nine tests whose whole point is that an unreferenced
        # note IS dropped. An absolute floor cannot tell "few citations because the document is
        # small" from "few citations because the column was mis-read".
        #
        # A RATIO CAN. A note column that was read names a real share of the notes the document
        # has; one that was mis-read names almost none of them:
        #
        #     1223214527    0 of 172 resolve   0.0%   guard fires
        #     8ad0c02c      0 of 365           0.0%   fires
        #     ee164920      0 of 413           0.0%   fires
        #     3bfe0c0e      1 of 408           0.2%   fires   <- why `== 0` is too strict a test
        #     2024 AR      29 of  53          54.7%   does not fire
        #     a 2-note test fixture, 1 cited    50%    does not fire
        #
        # AND A SIZE FLOOR, because a ratio over a handful of notes is noise: two notes with none
        # resolving is 0% and is not evidence of anything. The failure this guards against is a
        # mis-read note COLUMN on a filing with a real notes section, so it only applies where
        # there is one. Every affected filing has 172-413 notes; every fixture has one to three.
        _NEGLIGIBLE = 0.02
        _MIN_NOTES = 20
        have = {str(nt.note_number).strip() for nt in doc.notes if nt.note_number is not None}
        resolved = wanted & have
        if (wanted and len(have) >= _MIN_NOTES
                and len(resolved) / len(have) < _NEGLIGIBLE):
            # The unresolved tokens are named, because "the face's note column was not read" is a
            # claim about the READER and a reviewer needs to see what it thought it saw.
            ctx.log(f"prune_notes:face_citations_do_not_resolve kept={len(doc.notes)} dropped=0 "
                    f"resolved={sorted(resolved)} unresolved={sorted(wanted - have)}")
            return doc

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

        # THE NOTES A PUBLISHED FIGURE WAS READ FROM, added only here — past both guards, so they
        # neither decide whether the face's note column was read nor stand in for it when it was
        # not. Either of those branches has already kept every note.
        sourced = _sourced_note_numbers(doc) - wanted
        wanted = wanted | sourced

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
        kept_for_trail = sorted({str(nt.note_number).strip() for nt in kept
                                 if str(nt.note_number).strip() in sourced})
        ctx.log(f"prune_notes:kept={len(kept)} dropped={len(dropped)}"
                + (f" kept_for_derivations={kept_for_trail}" if kept_for_trail else "")
                + (f" dropped_notes={sorted(dropped)}" if dropped else ""))
        return doc
