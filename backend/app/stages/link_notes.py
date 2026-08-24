"""Note-linking stage — builds ``FaceNoteLink``s.

For each face ``LineItem`` that references a note, looks up the referenced ``NotesTable`` and
records a ``FaceNoteLink`` carrying the note's detail item ids. Which notes a row references is
``LineItem.cited_notes_among`` — one definition shared with ``prune_notes`` (what gets published)
and ``services.buckets`` (which section a note is filed under), so a note cannot be published
without being linked or filed somewhere its citing row cannot see it. These links are consumed by the reconcile stage (§20), which subtracts
already-ingested detail lines from the face aggregate and checks that the note total ties
back to the face figure.
"""
from __future__ import annotations

from app.core.models import DocumentModel
from app.core.models.enums import LinkRelationship, ReconciliationRole
from app.core.models.line_item import FaceNoteLink
from app.core.stage import PipelineContext


class LinkNotesStage:
    name = "link_notes"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        if not doc.line_items or not doc.notes:
            ctx.log("link_notes:skipped")
            return doc

        index: dict[str, list] = {}
        for nt in doc.notes:
            index.setdefault(str(nt.note_number), []).append(nt)

        # How many distinct face items cite each note → drives the relationship label. Counted
        # over the notes each row RESOLVES to (``LineItem.cited_notes_among``), so a row citing
        # "16(b)" counts against note 16 exactly as the link below is built against it.
        cite_count: dict[str, int] = {}
        for li in doc.line_items:
            for num in li.cited_notes_among(index):
                cite_count[num] = cite_count.get(num, 0) + 1

        built = 0
        for li in doc.line_items:
            refs = li.cited_notes_among(index)
            if not refs:
                continue
            for num in refs:
                for nt in index[num]:
                    rel = (LinkRelationship.MANY_NOTES_TO_ONE_FACE if len(refs) > 1
                           else LinkRelationship.NOTE_SPLITS_TO_MANY_FACE if cite_count.get(num, 0) > 1
                           else LinkRelationship.ONE_TO_ONE)
                    doc.links.append(FaceNoteLink(
                        face_item_id=li.id, notes_table_id=nt.id, note_number=num,
                        note_detail_item_ids=[it.id for it in nt.items],
                        relationship=rel, link_type="explicit_note_ref"))
                    built += 1
                li.reconciliation_role = ReconciliationRole.FACE_AGGREGATE

        ctx.log(f"link_notes:links={built}")
        return doc
