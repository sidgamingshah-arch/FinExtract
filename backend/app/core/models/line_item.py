"""Line items, extracted values, notes, and the face↔note link.

A ``LineItem`` holds a *dict of values keyed by (basis, period)* rather than a
single value, so consolidated and standalone (each with current + prior year) are
represented uniformly and reconciliation/validation operate per basket.
"""
from __future__ import annotations

import re
from collections.abc import Container
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .confidence import ConfidenceVector
from .enums import (
    Basis,
    PrintedIn,
    LineRole,
    LinkRelationship,
    ReconciliationRole,
    SignConvention,
    ValueSource,
)
from .geometry import Provenance


class UnitContext(BaseModel):
    """Detected scale + currency for a value, at its most-specific scope."""

    # Empty when the document declares no currency — asserting one (this used to default to INR)
    # mislabels every filing that isn't from that jurisdiction. Consumers render "" as unknown.
    currency: str = ""
    scale_factor: Decimal = Decimal(1)   # lakh=1e5, crore=1e7, thousand=1e3, million=1e6
    units_label: str | None = None
    source_bbox_page: int | None = None


class NoteRef(BaseModel):
    raw: str
    numbers: list[str] = Field(default_factory=list)   # "5", ranges expanded
    subrefs: list[str] = Field(default_factory=list)    # "12(a)"


_NOTE_BASE = re.compile(r"^\s*(\d{1,3})")


def _chapter_match(token: str | None, available: Container[str]) -> str | None:
    """The chapter-qualified note a BARE citation names, when exactly one chapter offers it.

    A mainland filing numbers its notes within each top-level chapter, so a note's identity is
    "七、9" (``services.notes_extract.qualified_note_number``) and the face normally prints the
    same — the 附注 column reads 七、9. A BILINGUAL filing is the case this exists for: a chapter
    heading can appear on its notes pages while the face cites plain "15", and the citation would
    then match nothing at all and the row be left with no note behind it.

    ONLY WHEN ONE CHAPTER OFFERS THE NUMBER. If two do, the bare citation genuinely does not say
    which — that ambiguity is the defect chapter-qualification exists to end, and guessing a
    chapter here would reintroduce it one row at a time. Unlinked is the honest answer.

    ``available`` is a Container, which need not be iterable, so the candidates are formed from
    the token rather than by scanning: there are at most a handful of chapters a filing can use.
    """
    bare = (token or "").strip()
    if not bare or "、" in bare:
        return None                       # already qualified, or nothing to qualify
    hits = [f"{numeral}、{bare}" for numeral in _CHAPTER_NUMERALS
            if f"{numeral}、{bare}" in available]
    return hits[0] if len(hits) == 1 else None


def base_note_number(token: str | None) -> str | None:
    """The parent note a printed sub-reference belongs to — ``"16(b)"`` -> ``"16"``.

    ``None`` for a citation that is already a bare number, so a caller can tell "this token has a
    parent" from "this token IS the parent" without comparing strings.
    """
    m = _NOTE_BASE.match(token or "")
    if m is None:
        return None
    return None if m.group(1) == (token or "").strip() else m.group(1)


# Every CJK numeral a top-level chapter is printed with. A filing has never needed past 二十-odd,
# and the list is finite on purpose: ``_chapter_match`` forms candidates from it rather than
# iterating the note index, which is a Container and need not be iterable.
_CHAPTER_NUMERALS: tuple[str, ...] = tuple(
    [c for c in "一二三四五六七八九"] + ["十"]
    + [f"十{c}" for c in "一二三四五六七八九"]
    + [f"{t}十" for t in "二三四五六七八九"]
    + [f"{t}十{c}" for t in "二三四五六七八九" for c in "一二三四五六七八九"]
)


class ValueKey(BaseModel, frozen=True):
    basis: Basis
    period_end: date | None = None
    period_label: str | None = None


class ExtractedValue(BaseModel):
    value_raw: Decimal | None = None      # exactly as printed (paren-negatives applied)
    value: Decimal | None = None          # sign-normalized (units NOT applied)
    # A FACT THAT IS WORDS RATHER THAN A FIGURE — an audit opinion's wording, a going-concern
    # statement, or prose the model wrote for a line whose `output_structure` asks for it.
    #
    # A SEPARATE FIELD, AND THE THREE ABOVE STAY STRICTLY `Decimal`. Widening `value` to
    # `Decimal | str` was the obvious move and it is the wrong one: a survey of this repository
    # found 139 sites that read a value, 45 of which would raise on a string. Almost every one of
    # them is already written as `if ev.value is None: continue` or
    # `ev.value if ev.value is not None else ev.value_raw` — so a text fact that leaves `value` as
    # None is skipped correctly by all of them, with no edit and no chance of a site being missed.
    # Widening would have turned each of those guards into a hole.
    #
    # SO THE ARITHMETIC NEVER SEES IT, and that is the point rather than a limitation: a sentence
    # has no business in a subtotal, a balance identity or a sign check. Contributing 0 to a total
    # would be worse than being absent, because the total would still balance and nothing would say
    # a line had been skipped.
    #
    # NO SEPARATE `kind` DISCRIMINATOR, deliberately. `value_text is not None` already answers
    # "is this fact words?", and it cannot disagree with itself; a `kind` field could say "prose"
    # beside an empty `value_text`, which is the two-places-storing-one-quantity bug this codebase
    # refuses elsewhere. What the line was CONFIGURED to hold is `LineItemDef.output_structure`;
    # what it actually got is this.
    value_text: str | None = None
    # True when the sign of ``value`` was FLIPPED away from ``value_raw`` by the rulebook's
    # ``global_rules.sign_convention.unsigned_source`` rule — a filing that prints its expenses as
    # unsigned positives. The rulebook asks for the transformation to be recorded on the fact ("set
    # sign_normalised: true on the fact so the transformation is auditable") precisely because it is
    # the one place the engine changes a reported number's sign: ``value_raw`` still holds what the
    # page said, so the two together are the audit trail.
    sign_normalised: bool = False
    reconciled: Decimal | None = None     # after §20 subtraction; always derived from raw
    basis: Basis
    period_end: date | None = None
    period_label: str | None = None
    # Human-readable column header captured from the document (e.g. "31 March 2025"), for
    # DISPLAY only. period_label stays the positional key ("current"/"prior"/…) used for all
    # lookups; this never participates in ValueKey or value matching.
    period_display: str | None = None
    # For a matrix statement (named component columns, not periods): which column this fact was
    # printed in, counting from the left. The column ORDER is part of a statement of changes in
    # equity's meaning — issued capital through to total equity — and it cannot be recovered from
    # ``provenance.bbox`` once the page is sideways, because there the columns advance down the
    # page's y. None for every ordinary period-keyed fact, which has no column axis to record.
    column_index: int | None = None
    unit_ctx: UnitContext = Field(default_factory=UnitContext)
    provenance: Provenance | None = None
    confidence: ConfidenceVector = Field(default_factory=ConfidenceVector)

    @property
    def key(self) -> ValueKey:
        return ValueKey(basis=self.basis, period_end=self.period_end, period_label=self.period_label)


class LineItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    statement_id: UUID | None = None
    parent_id: UUID | None = None

    source_label: str = ""                # text exactly as printed
    display_label: str | None = None      # canonical label (locale-resolved)
    # The section banner this row was printed under ("NON-CURRENT LIABILITIES", 流動負債).
    # A statement prints the same caption under two sections — "Interest-bearing bank and other
    # borrowings" appears once as non-current and once as current — so the caption alone cannot
    # say which concept it is. Mapping uses this to tell them apart.
    section_hint: str | None = None
    # The sub-heading printed above this row WITHIN its section — "Current charge for the year:",
    # "Under-provision in prior years, net:", "Adjustments for:". Distinct from ``section_hint``
    # because it must not scope the row the way a banner does (the rows under "Adjustments for:" are
    # still that section's rows), and kept because some captions have no meaning without it: a tax
    # note itemising an under-provision by geography prints a row whose caption is "Mainland China",
    # and only the sub-heading says what the figure is.
    group_hint: str = ""
    # THIS ROW'S CAPTION IS NOT THE FILING'S OWN. A note prints a block's total on a bare line —
    # the caption is the sub-heading two rows up, and a typesetter does not repeat it — so
    # reconstruction gives the row that heading and says here that it did. Two readers need to know:
    # a reader deciding whether this row's arithmetic may be checked against the rows above it (only
    # a borrowed caption means "I am their total"), and a person looking at a caption that does not
    # appear on the page in that position.
    caption_borrowed: bool = False
    # THE ROWS THIS SUBTOTAL IS THE TOTAL OF, by their ordinal on the page, recorded by the
    # reconstruction that promoted the row. NOT re-derived downstream from `group_hint`: a
    # sub-heading's scope is not closed at the end of its block, so rows printed after it still
    # carry it — and re-deriving pooled those rows into the sum and reported an arithmetically
    # correct note as broken. The builder knows exactly which rows it counted; this is that list.
    component_ordinals: list[int] = Field(default_factory=list)
    canonical_key: str | None = None
    template_node_id: str | None = None
    ordinal: int = 0
    role: LineRole = LineRole.LINE
    # Face or note — set by the reader that produced the row, from the page's own classification,
    # and back-filled for anything synthesised later by the segment stage. None only for a row
    # nothing could attribute, which is reported rather than defaulted: guessing "face" would put a
    # note's money onto the statement.
    printed_in: PrintedIn | None = None

    values: dict[str, ExtractedValue] = Field(default_factory=dict)  # keyed by ValueKey json
    sign_convention: SignConvention = SignConvention.NATURAL
    note_refs: list[NoteRef] = Field(default_factory=list)
    # THE NOTE THIS ROW IS TIED TO — the one it cites, or the one its figure was read from. NOT a
    # statement that the row lives inside a note; the rows printed inside a note are ``NoteItem``s
    # on a ``NotesTable``, never ``LineItem``s, so no value of this field can mean that.
    #
    # The old comment here said "set when this item lives inside a note", and it cost real work
    # before anyone checked it: a reader that trusted it dropped every face row carrying a note
    # reference — which on a filing that prints a note column is nearly all of them — and a
    # four-row balance sheet published one row. The four writers say what it actually holds:
    #
    #   * ``row_reconstruct`` and ``excel_extract`` set it from the note reference PRINTED beside
    #     the row ("Trade receivables … 15"), alongside the same value in ``note_refs``. A citation
    #     by the face, and the common case.
    #   * ``residual._sweep_notes`` sets it on a face row it SYNTHESISES from a note item, naming
    #     where the figure came from. Still a pointer at a note, from the other direction.
    #   * ``map_ontology`` copies the parent's value onto a sole-component row split off it, so the
    #     derived row cites what the row it came from cited.
    #
    # Every reader is consistent with that and none needs the membership reading: ``link_notes`` and
    # ``residual`` fall back to it for a row with no parsed ``note_refs``, ``map_ontology`` and
    # ``prune_notes`` build "notes the face cites" from it (which is what decides that a note is
    # published at all), and the API serves it as the row's note chip.
    #
    # WHETHER A ROW IS FACE OR NOTE IS THE PAGE'S CLASSIFICATION, not this field — ``extract_pdf``
    # reads face and notes pages into one ``line_items`` list, so the page kind is the only thing
    # that separates them (see ``services.buckets``).
    note_number: str | None = None
    reconciliation_role: ReconciliationRole = ReconciliationRole.NONE

    formula: dict | None = None
    # HOW a computed figure was reached, per ``basis:period`` — see services.derivation. A concept
    # assembled out of note datasets rather than read off a caption is not checkable on its figure
    # alone, so where a row has one this carries the rule that produced it and the note lines it
    # consumed, which the statement inspector renders as contributions with click-to-source.
    # OPTIONAL, and empty on most rows: it is set by whatever assembled the figure, not by a stage
    # that fills it on every run.
    derivation: dict | None = None
    is_computed: bool = False
    source: ValueSource = ValueSource.MACHINE
    confidence: ConfidenceVector = Field(default_factory=ConfidenceVector)

    def cited_notes(self) -> list[str]:
        """Every note number this row cites, AS PRINTED, in citation order.

        One definition of "which notes does this row point at", because three stages need it and
        each had written its own: ``link_notes`` (what to link), ``prune_notes`` (what to publish)
        and ``services.buckets`` (which section to file a note under). They disagreed, so a filing
        could have a note published but unlinked, or linked but filed in the wrong section.

        BOTH SPELLINGS OF A SUB-REFERENCE. ``NoteRef`` has a ``subrefs`` field for "16(b)", but
        neither reader uses it — ``row_reconstruct`` and ``excel_extract`` both put the whole
        printed token into ``numbers``, sub-reference and all. Reading only ``subrefs`` therefore
        reads a field that is always empty, so both are read here.

        ``note_number`` is the fallback for a row whose reference was scanned from the note column
        without being parsed into a ``NoteRef`` — the shape ``residual`` gives a face row it
        synthesised out of a note item.
        """
        out: list[str] = []
        for ref in self.note_refs:
            for token in (*ref.numbers, *ref.subrefs):
                token = (token or "").strip()
                if token and token not in out:
                    out.append(token)
        if not out and self.note_number:
            out.append(self.note_number.strip())
        return [t for t in out if t]

    def cited_notes_among(self, available: Container[str]) -> list[str]:
        """The notes this row cites THAT EXIST in ``available``, in citation order.

        A SUB-REFERENCE FALLS BACK TO ITS PARENT NOTE. A filing writes "16(b)" beside a face row and
        prints one note table numbered "16" with (a), (b), (c) inside it — so the citation as
        printed matches no table, and the row was left with no note behind it at all: unlinked,
        its note unpublished, and its section holding nothing that explains the figure.

        The parent is used ONLY when the citation itself names nothing, so a filing that numbers
        the table "16(b)" links to that table exactly, and no row is ever tied to both a sub-note
        and its parent (which would let the reconciliation subtract the same detail twice).

        A BARE CITATION ALSO REACHES A CHAPTER-QUALIFIED NOTE, when exactly one chapter offers
        that number — see :func:`_chapter_match`. A mainland filing's notes are identified by
        chapter and number together ("七、9"), and the face usually prints the same thing; but a
        bilingual filing can carry a chapter heading on its notes pages while its face cites bare
        numbers, and then a citation that names one unambiguous note must not go unlinked.
        """
        out: list[str] = []
        for token in self.cited_notes():
            hit = token if token in available else base_note_number(token)
            if hit is None or hit not in available:
                hit = _chapter_match(token, available)
            if hit and hit in available and hit not in out:
                out.append(hit)
        return out

    def set_value(self, ev: ExtractedValue) -> None:
        self.values[ev.key.model_dump_json()] = ev

    def get_value(self, basis: Basis, period_end: date | None = None,
                  period_label: str | None = None) -> ExtractedValue | None:
        key = ValueKey(basis=basis, period_end=period_end, period_label=period_label)
        return self.values.get(key.model_dump_json())


class NoteItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    notes_table_id: UUID | None = None
    parent_id: UUID | None = None
    raw_label: str = ""
    canonical_key: str | None = None
    values: dict[str, ExtractedValue] = Field(default_factory=dict)
    ordinal: int = 0
    role: LineRole = LineRole.LINE
    # The banner the row was printed under, carried over from the ``LineItem`` reconstruction
    # produced. ``notes_extract`` calls ``build_line_items``, so the section was always computed —
    # and, until this field existed, always discarded at the conversion. That mattered because
    # ``residual._sweep_notes`` synthesises face rows FROM note items, and those rows then reached
    # the sweep with no section, so the first of its three signals was unavailable for every figure
    # sourced from a note.
    section_hint: str | None = None
    # The sub-heading printed above this row inside the note — see ``LineItem.group_hint``. A note
    # is where breakdown-dimension captions live ("Mainland China", "Third parties"), so this is
    # the field that carries their meaning.
    group_hint: str = ""
    # THIS ROW'S CAPTION IS NOT THE FILING'S OWN. A note prints a block's total on a bare line —
    # the caption is the sub-heading two rows up, and a typesetter does not repeat it — so
    # reconstruction gives the row that heading and says here that it did. Two readers need to know:
    # a reader deciding whether this row's arithmetic may be checked against the rows above it (only
    # a borrowed caption means "I am their total"), and a person looking at a caption that does not
    # appear on the page in that position.
    caption_borrowed: bool = False
    # THE ROWS THIS SUBTOTAL IS THE TOTAL OF, by their ordinal on the page, recorded by the
    # reconstruction that promoted the row. NOT re-derived downstream from `group_hint`: a
    # sub-heading's scope is not closed at the end of its block, so rows printed after it still
    # carry it — and re-deriving pooled those rows into the sum and reported an arithmetically
    # correct note as broken. The builder knows exactly which rows it counted; this is that list.
    component_ordinals: list[int] = Field(default_factory=list)
    provenance: Provenance | None = None
    confidence: ConfidenceVector = Field(default_factory=ConfidenceVector)

    def set_value(self, ev: ExtractedValue) -> None:
        self.values[ev.key.model_dump_json()] = ev


class NotesTable(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    note_number: str
    title: str = ""
    basis: Basis | None = None
    source_pages: list[int] = Field(default_factory=list)
    source_text: str = ""
    items: list[NoteItem] = Field(default_factory=list)


class FaceNoteLink(BaseModel):
    """Link between a face line item and the note detail it decomposes into.

    The backbone of Requirement 20 (note→face subtraction reconciliation).
    """

    id: UUID = Field(default_factory=uuid4)
    face_item_id: UUID
    notes_table_id: UUID
    note_number: str
    note_detail_item_ids: list[UUID] = Field(default_factory=list)
    relationship: LinkRelationship = LinkRelationship.ONE_TO_ONE
    coverage: Decimal = Decimal(1)        # fraction of the note total consumed by this link
    link_type: str = "explicit_note_ref"  # explicit_note_ref | inferred | manual
    confidence: float = 1.0
