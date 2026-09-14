"""The document model that flows through the pipeline.

Each stage *enriches* this immutable-ish model rather than mutating external state,
which makes stages independently testable and re-runnable and gives provenance for
free (each enrichment records which stage/adapter produced it).
"""
from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import DocFormat, PageKind, PageSourceKind
from .buckets import BucketedSource
from .integrity import IntegrityReport
from .line_item import FaceNoteLink, LineItem, NotesTable, UnitContext
from .reports import ReconciliationReport, StructuralReport
from .table import Table


class PageSource(BaseModel):
    index: int
    kind: PageKind = PageKind.UNKNOWN
    source_kind: PageSourceKind = PageSourceKind.NATIVE
    width_pt: float = 0.0
    height_pt: float = 0.0
    dpi: int | None = None
    rotation: int = 0
    text_char_count: int = 0
    text_area_coverage: float = 0.0
    image_area_coverage: float = 0.0
    classification_confidence: float | None = None
    classification_evidence: list[str] = Field(default_factory=list)
    # Which face statement this page belongs to (balance_sheet / profit_and_loss / cash_flow /
    # changes_in_equity), when the classifier could tell. Constrains ontology mapping so a
    # caption cannot resolve to a concept from a different statement. None = undetermined.
    statement: str | None = None
    # Whose figures the page presents: consolidated / company / mixed (a Group column and a Company
    # column side by side, which HK balance sheets print routinely). None = the title said nothing.
    scope: str | None = None
    # The scopes found as COLUMN headers in the top band, when the page carries more than one.
    scope_columns: list[str] = Field(default_factory=list)
    # The page number PRINTED ON THE PAGE, as printed — the report's own folio. It is not
    # ``index + 1``: an annual report's front matter (cover, contents, chairman's statement) means
    # the folio runs behind the file's page count, routinely by several pages. Both numbers are
    # true and neither substitutes for the other, so the viewer shows the position it navigates by
    # AND the number the reader sees on the paper. None when the page prints no folio.
    printed_page: str | None = None
    # Why the classifier decided what it did: the title it matched, whether that title was
    # ambiguous, and the decode margin. Diagnostic — nothing downstream branches on it.
    evidence: dict[str, object] = Field(default_factory=dict)


class DocumentModel(BaseModel):
    """The single artifact enriched stage-by-stage."""

    id: UUID = Field(default_factory=uuid4)
    filename: str = ""
    content_hash: str | None = None
    fmt: DocFormat = DocFormat.UNKNOWN
    object_key: str | None = None
    locale: str | None = None                 # detected primary locale

    pages: list[PageSource] = Field(default_factory=list)
    integrity: IntegrityReport | None = None
    tables: list[Table] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)
    notes: list[NotesTable] = Field(default_factory=list)
    links: list[FaceNoteLink] = Field(default_factory=list)
    reconciliation: ReconciliationReport | None = None
    structural: StructuralReport | None = None   # template rollup/identity validation
    # Which of the thirteen face sections (or Others) each face row and each note belongs to.
    # Membership only —
    # the figures stay on ``line_items``/``notes``; see ``core.models.buckets``.
    buckets: BucketedSource | None = None
    # Confirmed gap-closing decisions: leftover lines a model placed in a section's Others to
    # reconcile a printed subtotal with its components (see stages.gap_closing). Kept so the
    # routing is visible and auditable rather than an unexplained change of mapping.
    gap_routings: list[dict] = Field(default_factory=list)
    # One printed figure claimed by two or more line items, and what was decided about each (see
    # stages.shared_figures). Recorded whether or not a provider answered: a contest nobody
    # resolved is a review item, and a figure the model moved off a line must be explicable
    # afterwards — "the amount, the page, who claimed it, who kept it, and why".
    shared_figures: list[dict] = Field(default_factory=list)
    # Contingent Liabilities' structured narrative + classified/unclassified tables (see
    # services.contingent_liabilities) — a paragraph and two small tables, not a single figure, so
    # it is kept here rather than forced into a LineItem's numeric ``value``.
    contingent_liabilities: dict | None = None
    unit_context: UnitContext | None = None    # detected source currency + scale ("in ₹ crore")
    # Headings that LOOKED like a statement title and resolved to nothing. The lexicon's coverage is
    # otherwise unmeasurable — you cannot tell a filing whose titles are all recognised from one
    # whose titles are all missed, since both produce silence. Review these across a corpus and fold
    # the real vocabulary back into the lexicon.
    unmapped_titles: list[str] = Field(default_factory=list)

    def face_pages(self) -> list[PageSource]:
        return [p for p in self.pages if p.kind == PageKind.FACE]

    def notes_pages(self) -> list[PageSource]:
        return [p for p in self.pages if p.kind == PageKind.NOTES]
