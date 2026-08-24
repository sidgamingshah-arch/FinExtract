"""The thirteen face sections a filing's source is segmented into, plus Others, and what each
holds.

WHY A SEGMENTATION AND NOT A COPY. Each segment names the face rows, notes and pages that belong
to its bucket; it does not carry their figures. A figure stored twice is two places computing one
quantity, and here the copy would be the one an analyst reads — so the membership is persisted and
the API joins it to ``line_items`` at serve time. What is new information (which content belongs to
which bucket) is stored; what already exists (the numbers) is referenced.

EVERY FACE ROW LANDS IN EXACTLY ONE BUCKET. That is what makes the face side safe to sum, and
``unresolved_face_item_ids`` is what stops "everything is placed" from being achieved by sweeping
the difficult cases into Others unnoticed: rows in Others because the balance sheet's own totals
span sections are a different fact from rows in Others because nothing could place them, and only
the second is a coverage failure.

A NOTE, BY CONTRAST, IS FILED IN EVERY BUCKET THAT CITES IT. A borrowings note split across current
and non-current belongs to both sections, and an analyst reading either one needs the note in front
of them — a pointer to the other bucket is not the same thing. The cost is real and is stated rather
than avoided: a shared note's figures appear more than once across the store, so the notes side is
NOT safe to add up. ``shared_notes`` names, in each bucket holding it, every note that is also
filed elsewhere, and the buckets index serves a distinct-note count beside the per-bucket totals —
so anything that needs a filing-level total can subtract the overlap, and the duplication is
visible instead of silent.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BucketSegment(BaseModel):
    """One bucket's membership. Ids and numbers, never figures — see the module docstring."""

    bucket: str
    label: str
    # Face rows printed on a statement face and resolved to this bucket.
    face_item_ids: list[str] = Field(default_factory=list)
    # Every note a face row in this bucket cites. A note cited from two buckets is filed in BOTH:
    # a borrowings note split across current and non-current belongs to each section, and an analyst
    # reading either one needs it there rather than a pointer somewhere else.
    note_numbers: list[str] = Field(default_factory=list)
    # Which of this bucket's notes are also filed in another bucket. The consequence of filing in
    # both is that a note's figures appear more than once across the store, so anything that ADDS
    # the buckets up has to subtract the overlap — this is the list that makes that possible, and
    # what stops the duplication being silent.
    shared_notes: list[str] = Field(default_factory=list)
    face_pages: list[int] = Field(default_factory=list)
    note_pages: list[int] = Field(default_factory=list)
    # The template ``section_scope`` ids that resolved into this bucket, so a reader can see WHY
    # a row is here without re-deriving the rule.
    sections: list[str] = Field(default_factory=list)


class BucketedSource(BaseModel):
    """The whole segmentation, in the order the buckets are presented."""

    segments: list[BucketSegment] = Field(default_factory=list)
    # Face rows that reached Others because nothing could place them — not because they belong
    # there. A coverage fact, measurable across a corpus.
    unresolved_face_item_ids: list[str] = Field(default_factory=list)
    unresolved_note_numbers: list[str] = Field(default_factory=list)
    # A template section this vocabulary has no bucket for. Its rows are in Others and this names
    # the section, because a taxonomy that silently swallows a whole section of a filing reads
    # exactly like one that covers it.
    unknown_sections: list[str] = Field(default_factory=list)

    def segment(self, bucket: str) -> BucketSegment | None:
        return next((s for s in self.segments if s.bucket == bucket), None)
