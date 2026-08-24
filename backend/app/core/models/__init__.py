"""Core domain models — the shared, serializable data that flows through every
pipeline stage and is returned by the API."""
from __future__ import annotations

from .buckets import BucketedSource, BucketSegment
from .confidence import ConfidenceVector
from .document import DocumentModel, PageSource
from .enums import (
    Basis,
    DocFormat,
    LineRole,
    LinkRelationship,
    MappingMethod,
    PrintedIn,
    PageKind,
    PageSourceKind,
    ReconciliationRole,
    Severity,
    SignConvention,
    StatementType,
    ValueSource,
)
from .geometry import BBox, Provenance, Transform
from .integrity import IntegrityFinding, IntegrityReport
from .line_item import (
    ExtractedValue,
    FaceNoteLink,
    LineItem,
    NoteItem,
    NoteRef,
    NotesTable,
    UnitContext,
    ValueKey,
)
from .reports import (
    ReconciliationEntry,
    ReconciliationReport,
    ReviewItemModel,
    RuleResult,
    StructuralReport,
)
from .table import Cell, Table

__all__ = [
    "BBox", "Provenance", "Transform",
    "BucketedSource", "BucketSegment",
    "ConfidenceVector",
    "DocumentModel", "PageSource",
    "Basis", "DocFormat", "LineRole", "LinkRelationship", "MappingMethod",
    "PageKind", "PageSourceKind", "PrintedIn", "ReconciliationRole", "Severity",
    "SignConvention", "StatementType", "ValueSource",
    "IntegrityFinding", "IntegrityReport",
    "ExtractedValue", "FaceNoteLink", "LineItem", "NoteItem", "NoteRef",
    "NotesTable", "UnitContext", "ValueKey",
    "ReconciliationEntry", "ReconciliationReport", "ReviewItemModel", "RuleResult",
    "StructuralReport",
    "Cell", "Table",
]
