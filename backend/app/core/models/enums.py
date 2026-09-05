"""Enumerations shared across the domain model."""
from __future__ import annotations

from enum import Enum


class DocFormat(str, Enum):
    PDF = "pdf"
    XLSX = "xlsx"
    XLS = "xls"
    IMAGE = "image"
    HTML = "html"
    UNKNOWN = "unknown"


class PrintedIn(str, Enum):
    """Where an extracted row was PRINTED: on the face of a statement, or inside a note.

    Named for the question rather than for "origin", because the statement payload already has an
    ``origin`` and it answers a different question — whether a figure was read off the document or
    typed by an analyst. Two fields called origin, one meaning face-vs-note and the other
    machine-vs-human, is a confusion no comment survives.

    The distinction is not cosmetic and it is not derivable downstream from anything else. Face and
    note rows reach ``line_items`` through the same reader, they carry the same shape, and a note's
    detail lines sum to a figure the face already reports — so adding the two together double-counts
    the filing. An analyst reading a value also needs to know which one they are looking at before
    they can trust it against the statement. ``note_number`` does not answer this: that field holds
    the note a face row CITES, not the note a row lives in.
    """

    FACE = "face"
    NOTES = "notes"


class PageKind(str, Enum):
    """Classification of a page after the classify stage."""

    FACE = "face"            # face of a primary statement
    NOTES = "notes"
    OTHER = "other"
    COVER = "cover"
    AUDITOR_REPORT = "auditor_report"
    UNKNOWN = "unknown"


class PageSourceKind(str, Enum):
    """Whether a page's text is natively extractable or needs OCR."""

    NATIVE = "native"
    SCANNED = "scanned"
    MIXED = "mixed"


class StatementType(str, Enum):
    STATEMENT_SETUP = "statement_setup"
    BALANCE_SHEET = "balance_sheet"
    PROFIT_AND_LOSS = "profit_and_loss"
    CASH_FLOW = "cash_flow"
    EQUITY_CHANGES = "equity_changes"
    COVENANTS_SUPPLEMENTAL = "covenants_supplemental"
    NOTES = "notes"


class Basis(str, Enum):
    """Consolidated vs standalone — both are extracted in one pass."""

    CONSOLIDATED = "consolidated"
    STANDALONE = "standalone"


class LineRole(str, Enum):
    LINE = "line"
    SUBTOTAL = "subtotal"
    TOTAL = "total"
    HEADER = "header"
    SPACER = "spacer"


class SignConvention(str, Enum):
    NATURAL = "natural"
    NATURAL_POSITIVE = "natural_positive"
    NATURAL_NEGATIVE = "natural_negative"
    DEBIT_POSITIVE = "debit_positive"
    CREDIT_POSITIVE = "credit_positive"
    CONTEXT = "context"


class ValueSource(str, Enum):
    MACHINE = "machine"
    HUMAN = "human"
    FORMULA = "formula"
    IMPORT = "import"


class ReconciliationRole(str, Enum):
    FACE_AGGREGATE = "face_aggregate"
    NOTE_DETAIL = "note_detail"
    NONE = "none"


class LinkRelationship(str, Enum):
    ONE_TO_ONE = "one_to_one"
    NOTE_SPLITS_TO_MANY_FACE = "note_splits_to_many_face"
    MANY_NOTES_TO_ONE_FACE = "many_notes_to_one_face"
    PARTIAL = "partial"


class Severity(str, Enum):
    BLOCKER = "blocker"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class MappingMethod(str, Enum):
    EXACT = "exact"
    RULE = "rule"
    FUZZY = "fuzzy"
    # No longer produced. Kept because a stored run records the method that mapped each of its
    # rows, and the judgement layer compares an acceptance against the method it was made on — so
    # deleting the member would make an old run's rows unreadable rather than historical.
    EMBEDDING = "embedding"
    LLM = "llm"
    UNMATCHED = "unmatched"


class AllocationStatus(str, Enum):
    """How a mapped value was derived — the provenance of the number, not just the label.

    Adopted from field-tested Ind-AS extraction practice: it makes parent/child/residual
    handling auditable and prevents double-counting a gross parent with its children.
    """

    DIRECT_EXCLUSIVE = "direct_exclusive"              # reported directly, no overlap
    CHILD_COMPONENT = "child_component"                # a component of a gross parent
    PARENT_GROSS_EVIDENCE_ONLY = "parent_gross_evidence_only"  # parent kept as evidence, not added
    CALCULATED_RESIDUAL = "calculated_residual"        # parent − confirmed children
    DERIVED_TOTAL = "derived_total"                    # computed from mutually exclusive outputs
    FALLBACK_COMBINED = "fallback_combined"            # assembled from legacy components
    UNMAPPED_REVIEW = "unmapped_review"                # no confident mapping → review
