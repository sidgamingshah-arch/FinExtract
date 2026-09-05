"""NormalizeStage derives sign-normalized value from the printed value_raw using
'Less:'/'Add:' label cues and the ontology's flip_if_label_matches (Req 12)."""
from __future__ import annotations

from app.core.models.document import DocumentModel
from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.stages.normalize import NormalizeStage


def _li(label, raw):
    li = LineItem(source_label=label)
    li.set_value(ExtractedValue(value=raw, value_raw=raw, basis=Basis.CONSOLIDATED,
                                period_label="current"))
    return li


def test_less_cue_makes_value_negative():
    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [_li("Less: accumulated depreciation", 500),
                      _li("Trade receivables", 3410)]
    NormalizeStage().run(doc, PipelineContext(raw_bytes=b""))
    less = next(iter(doc.line_items[0].values.values()))
    tr = next(iter(doc.line_items[1].values.values()))
    assert less.value == -500 and less.value_raw == 500   # normalized distinct from raw
    assert tr.value == 3410                                # untouched line unchanged


def test_add_cue_makes_value_positive():
    doc = DocumentModel(filename="x.pdf")
    doc.line_items = [_li("Add: back depreciation", -200)]
    NormalizeStage().run(doc, PipelineContext(raw_bytes=b""))
    ev = next(iter(doc.line_items[0].values.values()))
    assert ev.value == 200


def test_template_natural_negative_makes_a_contra_asset_negative():
    doc = DocumentModel(filename="x.pdf")
    item = _li("Accumulated depreciation", 500)
    item.canonical_key = "bs_nca__accum_deprec_and_impairment"
    doc.line_items = [item]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.template_def = {"statements": [{"sections": [{"children": [{
        "canonical_key": "bs_nca__accum_deprec_and_impairment",
        "sign": "natural_negative",
    }]}]}]}

    NormalizeStage().run(doc, ctx)

    ev = next(iter(item.values.values()))
    assert ev.value == -500 and ev.value_raw == 500
