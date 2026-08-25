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


def base_note_number(token: str | None) -> str | None:
    """The parent note a printed sub-reference belongs to — ``"16(b)"`` -> ``"16"``.

    ``None`` for a citation that is already a bare number, so a caller can tell "this token has a
    parent" from "this token IS the parent" without comparing strings.
    """
    m = _NOTE_BASE.match(token or "")
    if m is None:
        return None
    return None if m.group(1) == (token or "").strip() else m.group(1)


class ValueKey(BaseModel, frozen=True):
    basis: Basis
    period_end: date | None = None
    period_label: str | None = None


class ExtractedValue(BaseModel):
    value_raw: Decimal | None = None      # exactly as printed (paren-negatives applied)
    value: Decimal | None = None          # sign-normalized (units NOT applied)
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
        """
        out: list[str] = []
        for token in self.cited_notes():
            hit = token if token in available else base_note_number(token)
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
