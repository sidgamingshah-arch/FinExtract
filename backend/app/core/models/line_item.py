"""Line items, extracted values, notes, and the face↔note link.

A ``LineItem`` holds a *dict of values keyed by (basis, period)* rather than a
single value, so consolidated and standalone (each with current + prior year) are
represented uniformly and reconciliation/validation operate per basket.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .confidence import ConfidenceVector
from .enums import (
    Basis,
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
    canonical_key: str | None = None
    template_node_id: str | None = None
    ordinal: int = 0
    role: LineRole = LineRole.LINE

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
