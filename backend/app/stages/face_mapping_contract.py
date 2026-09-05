"""Final storage invariant for statement-face values.

Routing stages may decline a row when assigning a real concept would be a guess. This stage runs
after every mapping opportunity and gives each remaining face value a unique engine-owned key. The
key is deliberately outside every template and ontology namespace: it keeps the row stored and
reviewable without feeding its amount into an unrelated calculation.
"""
from __future__ import annotations

from app.core.models import DocumentModel
from app.core.models.enums import PrintedIn
from app.core.stage import PipelineContext
from app.services.mapping import normalize_statement, section_of_banner
from app.stages.residual import _NOTE_REF_ONLY


UNCLASSIFIED_FACE_PREFIX = "engine_unclassified_face__"


def is_unclassified_face_key(key: str | None) -> bool:
    return bool(key and key.startswith(UNCLASSIFIED_FACE_PREFIX))


def _flag_value(flags: list[str], prefix: str) -> str:
    return next((flag.split(":", 1)[1] for flag in flags if flag.startswith(prefix)), "")


def _has_value(row) -> bool:
    return any(value.value is not None for value in row.values.values())


def _mapped_children(rows: list, keys: set[str]) -> bool:
    mapped = {row.canonical_key for row in rows if row.canonical_key}
    return bool(keys) and keys <= mapped


def _resolved_as_evidence(parent, rows: list) -> bool:
    """Whether mapped children verifiably replace this non-additive aggregate."""
    flags = parent.confidence.flags

    aggregate = _flag_value(flags, "unfiled_aggregate:")
    contained = set(filter(None, _flag_value(flags, "contains_mapped_children:").split(",")))
    if aggregate and _mapped_children(rows, contained):
        return True

    aggregate = _flag_value(flags, "note_decomposed_from:")
    expected = set(filter(None, _flag_value(flags, "decomposed_into:").split(",")))
    if not aggregate or not expected or not parent.note_number:
        return False
    split_flag = f"split_from:{aggregate}"
    actual = {
        row.canonical_key
        for row in rows
        if row.canonical_key in expected
        and row.note_number == parent.note_number
        and split_flag in row.confidence.flags
    }
    return expected <= actual


def _statement_of(row, doc: DocumentModel) -> str:
    for value in row.values.values():
        page = getattr(value.provenance, "page_index", None)
        if page is None:
            continue
        statement = next((item.statement for item in doc.pages
                          if item.index == page and item.statement), None)
        if statement:
            return normalize_statement(statement) or "unknown_statement"
    return "unknown_statement"


def _storage_key(row, doc: DocumentModel) -> str:
    statement = _statement_of(row, doc)
    section = section_of_banner(row.section_hint or "") or "unresolved_section"
    return f"{UNCLASSIFIED_FACE_PREFIX}{statement}__{section}__{row.id.hex}"


class FaceMappingContractStage:
    name = "face_mapping_contract"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        if getattr(ctx, "ontology", None) is None:
            ctx.log("face_mapping_contract:skipped(no ontology)")
            return doc
        unresolved = [
            row for row in doc.line_items
            if row.printed_in is not PrintedIn.NOTES
            and _has_value(row)
            and not _NOTE_REF_ONLY.match(row.source_label or "")
            and not row.canonical_key
            and not _resolved_as_evidence(row, doc.line_items)
        ]
        if not unresolved:
            ctx.log("face_mapping_contract:passed")
            return doc

        for row in unresolved:
            row.canonical_key = _storage_key(row, doc)
            row.confidence.mapping = 0.0
            row.confidence.method = "engine_unclassified_face"
            row.confidence.flags.append("engine_unclassified_face")
            row.confidence.flags.append("requires_concept_review")
        labels = [(row.source_label or "<blank>").strip() for row in unresolved]
        ctx.log(f"face_mapping_contract:stored_unclassified={len(unresolved)} "
                f"labels={labels[:10]}")
        return doc
