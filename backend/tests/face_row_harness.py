"""Synthetic face rows through the shipped line-item set's mapping and residual stages.

Shared by the tests that pin how a row printed on a face is BOUND (`stages.map_ontology`) and SWEPT
(`stages.residual`): the same harness `test_one_set_equity_and_cash_flow_bindings` uses, with the
row marked as printed on the face, as `pdf_extract` marks every face row.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from functools import lru_cache

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import Basis, LineRole, PageKind, PrintedIn
from app.core.models.line_item import ExtractedValue, LineItem, Provenance
from app.core.stage import PipelineContext

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
SEED = TEMPLATES / "output_csv_hk_line_items.json"


@lru_cache(maxsize=1)
def shipped():
    from app.schemas.line_items import load_line_item_set

    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


def row(caption: str, current, prior=None, *, page: int = 0, section: str | None = None,
        role: LineRole = LineRole.LINE, flags: tuple[str, ...] = (),
        basis: Basis = Basis.CONSOLIDATED) -> LineItem:
    li = LineItem(source_label=caption, role=role, section_hint=section,
                  printed_in=PrintedIn.FACE)
    for period, value in (("current", current), ("prior", prior)):
        if value is not None:
            li.set_value(ExtractedValue(basis=basis, period_label=period, value=Decimal(value),
                                        value_raw=Decimal(value),
                                        provenance=Provenance(page_index=page)))
    li.confidence.flags.extend(flags)
    return li


def face(statement: str, index: int = 0) -> PageSource:
    return PageSource(index=index, kind=PageKind.FACE, statement=statement)


def run(rows: list[LineItem], pages: list[PageSource], *, residual: bool = True):
    from app.services.working_view import build_working_view
    from app.stages.map_ontology import MapOntologyStage
    from app.stages.residual import ResidualStage

    for i, li in enumerate(rows):
        li.ordinal = i
    doc = DocumentModel(filename="f.pdf")
    doc.pages = pages
    doc.line_items = rows
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    logs: list[str] = []
    ctx.log = logs.append
    ctx.ontology = build_working_view(shipped())
    ctx.line_items = shipped()
    ctx.template_def = json.loads((TEMPLATES / "output_csv_hk_v1_template.json")
                                  .read_text(encoding="utf-8"))
    MapOntologyStage().run(doc, ctx)
    if residual:
        ResidualStage().run(doc, ctx)
    return doc, logs
