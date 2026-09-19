"""Final storage invariant for statement-face values.

Routing stages may decline a row when assigning a real concept would be a guess. This stage runs
after every mapping opportunity and gives each remaining face value a unique engine-owned key. The
key is deliberately outside every template and ontology namespace: it keeps the row stored and
reviewable without feeding its amount into an unrelated calculation.
"""
from __future__ import annotations

import re
from collections import Counter

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
    """Asks the ROW before the page it was printed on — see `buckets.statement_resolver`. A page
    can carry the tail of one statement and the head of the next, and this contract is keyed by
    statement, so answering by page filed a balance-sheet caption under the income statement."""
    from app.services.buckets import statement_resolver

    statement = statement_resolver(doc)(row)
    return (normalize_statement(statement) or "unknown_statement") if statement \
        else "unknown_statement"


_SLUG_MAX = 44


def _slug(caption: str) -> str:
    """A stable, readable identifier for a printed caption.

    ASCII where the caption gives one, because a key a person has to read in a formula or a
    mapping table should say what it is: "owners_of_the_company", not a hash. A caption with no
    usable ASCII — every Chinese one — falls back to a short digest of its NORMALISED form, so
    the Traditional and Simplified spellings of one caption land on the same key.
    """
    from hashlib import sha1

    from app.services.mapping import normalize_label

    norm = normalize_label(caption or "")
    ascii_slug = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")[:_SLUG_MAX].strip("_")
    if ascii_slug:
        return ascii_slug
    if not norm:
        return "unnamed"
    return "x" + sha1(norm.encode("utf-8")).hexdigest()[:12]


def _storage_key(row, doc: DocumentModel, seen: Counter) -> str:
    """A key for an unmapped face value that is THE SAME ON THE NEXT RUN.

    THIS USED TO BE `row.id.hex` — a uuid minted per run. Measured on two identical runs of one
    367-page filing: 66 unclassified face rows on each side and ZERO keys in common. The rows
    carried real figures the whole time and nothing downstream could ever refer to them, so an
    operator could not name one, attach it under a template line, or put it in a formula: the
    thing they were configuring had a different identity by the time the next run answered.

    Derived from what the filing PRINTED instead — statement, section banner, caption — so the
    same line is the same key run over run. A genuine duplicate (one section printing the same
    caption twice) gets an occurrence index rather than being collapsed, because two printed lines
    are two facts even when they read alike; `seen` is the per-document counter that assigns it,
    and it must be passed in so the numbering is stable across the whole document rather than
    restarting per call.
    """
    statement = _statement_of(row, doc)
    section = section_of_banner(row.section_hint or "") or "unresolved_section"
    slug = _slug(row.source_label or "")
    ordinal = seen[(statement, section, slug)]
    seen[(statement, section, slug)] += 1
    suffix = "" if ordinal == 0 else f"__{ordinal + 1}"
    return f"{UNCLASSIFIED_FACE_PREFIX}{statement}__{section}__{slug}{suffix}"


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

        # ONE counter for the whole document, so the occurrence index of a repeated
        # caption is stable rather than restarting at each row.
        seen: Counter = Counter()
        for row in unresolved:
            row.canonical_key = _storage_key(row, doc, seen)
            row.confidence.mapping = 0.0
            row.confidence.method = "engine_unclassified_face"
            row.confidence.flags.append("engine_unclassified_face")
            row.confidence.flags.append("requires_concept_review")
        labels = [(row.source_label or "<blank>").strip() for row in unresolved]
        ctx.log(f"face_mapping_contract:stored_unclassified={len(unresolved)} "
                f"labels={labels[:10]}")
        return doc
