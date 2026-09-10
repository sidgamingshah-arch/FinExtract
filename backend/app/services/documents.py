"""Document analysis + extraction orchestration helpers used by the API.

``analyze_document`` runs the upfront portion of the pipeline (ingest → integrity →
language → classify) so the frontend can show the integrity report and page map
before committing to a full extraction. ``run_extraction`` runs the whole pipeline.
Both run synchronously here; a background worker (arq/RQ/Celery) is the infra-time
swap and does not change these signatures.
"""
from __future__ import annotations

import hashlib

from app.core.models import DocumentModel
from app.core.pipeline import Pipeline, default_pipeline
from app.core.stage import PipelineContext
from app.stages.classify import ClassifyStage
from app.stages.ingest import IngestStage
from app.stages.integrity import IntegrityStage
from app.stages.language import LanguageDetectStage


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _context(data: bytes, object_store=None, ontology=None,
             progress_cb=None, included_pages=None, template=None,
             line_items=None) -> PipelineContext:
    ctx = PipelineContext(raw_bytes=data, object_store=object_store,
                          progress_cb=progress_cb)
    if ontology is not None:
        ctx.ontology = ontology  # attribute read by MapOntologyStage
    if template is not None:
        ctx.template = template  # attribute read by StructuralStage
    if line_items is not None:
        # THE WHOLE CONFIGURED SET, which the working view above is a PROJECTION of and not a
        # replacement for. `build_working_view` produces the 462 matchable concepts and drops the
        # 13 internal sub-line items entirely, and `OntologyMapping` carries neither `note_source`
        # nor `parent` nor `rollup` — so a stage that reads where in the NOTES a figure lives
        # cannot get it from `ctx.ontology`. Attribute read by NoteSourcedStage.
        ctx.line_items = line_items
    if included_pages is not None:
        ctx.included_pages = set(included_pages)
    return ctx


def analyze_document(data: bytes, filename: str = "") -> tuple[DocumentModel, PipelineContext]:
    doc = DocumentModel(filename=filename, content_hash=content_hash(data))
    ctx = _context(data)
    pipe = Pipeline(stages=[
        IngestStage(), IntegrityStage(), LanguageDetectStage(), ClassifyStage(),
    ])
    doc = pipe.run(doc, ctx)
    return doc, ctx


def run_extraction(data: bytes, filename: str = "", ontology=None,
                   progress_cb=None, included_pages=None,
                   template=None, context_cb=None,
                   step_cb=None, line_items=None) -> tuple[DocumentModel, PipelineContext]:
    doc = DocumentModel(filename=filename, content_hash=content_hash(data))
    ctx = _context(data, ontology=ontology, progress_cb=progress_cb,
                   included_pages=included_pages, template=template,
                   line_items=line_items)
    # Progress reported from INSIDE a stage. `progress_cb` fires only between stages, and
    # `map_ontology` is one stage that makes many LLM calls — so without this the percentage, the
    # stage counter and the log tail all sit frozen for the longest part of a run.
    ctx.step_cb = step_cb
    # Hand the context over BEFORE the pipeline starts, for a caller that has to read it DURING the
    # run rather than after it: the API worker flushes the tail of ``ctx.logs`` onto the run row at
    # every progress emit, and the tuple returned below only exists once every stage has finished —
    # which is exactly too late to report on a run in flight.
    if context_cb is not None:
        context_cb(ctx)
    doc = default_pipeline().run(doc, ctx)
    return doc, ctx
