"""Note-linking stage — builds ``FaceNoteLink``s.

For each face ``LineItem`` that references a note (via ``note_refs``/``note_number``),
looks up the referenced ``NotesTable`` and records a ``FaceNoteLink`` carrying the note's
detail item ids. These links are consumed by the reconcile stage (§20), which subtracts
already-ingested detail lines from the face aggregate and checks that the note total ties
back to the face figure.
"""
from __future__ import annotations

import re

from app.core.models import DocumentModel
from app.core.models.enums import LinkRelationship, ReconciliationRole
from app.core.models.line_item import FaceNoteLink
from app.core.stage import PipelineContext


_BASE_NUMBER = re.compile(r"^\s*(\d{1,3})")


def _refs(li) -> list[str]:
    """All note numbers a face line references, in citation order.

    SUB-REFERENCES COUNT, and both spellings of them. A filing writes "16(b)" for a sub-note, which
    the reader parses into ``NoteRef.subrefs`` and NOT into ``numbers`` — so a row citing only a
    sub-note used to produce no link at all, and its note detail was unreachable from the face row
    that pointed at it. Both are offered: the sub-reference as printed (a filing that numbers its
    note tables "16(b)" too) and its base number (one that numbers the table "16" and its parts
    inside), and whichever exists is the one that links.
    """
    nums: list[str] = []
    for ref in li.note_refs:
        nums.extend(n for n in ref.numbers if n)
        for sub in ref.subrefs:
            if not sub:
                continue
            nums.append(sub)
            if (base := _BASE_NUMBER.match(sub)) and base.group(1) not in nums:
                nums.append(base.group(1))
    if not nums and li.note_number:
        nums.append(li.note_number)
    # de-dupe, preserve order
    seen: set[str] = set()
    return [n for n in nums if not (n in seen or seen.add(n))]


class LinkNotesStage:
    name = "link_notes"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        if not doc.line_items or not doc.notes:
            ctx.log("link_notes:skipped")
            return doc

        index: dict[str, list] = {}
        for nt in doc.notes:
            index.setdefault(str(nt.note_number), []).append(nt)

        # How many distinct face items cite each note → drives the relationship label.
        cite_count: dict[str, int] = {}
        for li in doc.line_items:
            for num in _refs(li):
                if num in index:
                    cite_count[num] = cite_count.get(num, 0) + 1

        built = 0
        for li in doc.line_items:
            refs = _refs(li)
            if not refs:
                continue
            for num in refs:
                tables = index.get(num)
                if not tables:
                    continue
                for nt in tables:
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
