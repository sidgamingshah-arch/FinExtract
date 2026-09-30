"""`extraction.llm_parallel_requests`: the first request alone, then several at once, same result.

The first request of a run fills the provider's prompt cache, so it is sent on its own; the rest
are then in flight together, up to the setting. Replies are processed in plan order, so what a run
writes is the same whatever the setting.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.line_items import load_line_item_set
from app.stages.line_item_llm import LineItemLlmStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")
LINES = {"bs_nca__land": ("Land", "100"), "bs_nca__buildings": ("Buildings", "200"),
         "bs_nca__leasehold_improvements": ("Leasehold improvements", "300"),
         "bs_nca__plant_and_equipment": ("Plant and equipment", "400"),
         "bs_nca__furniture_and_fixtures": ("Furniture and fixtures", "500")}


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(autouse=True)
def _restore():
    ex, st = get_settings().extraction, get_settings()
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()),
           ex.llm_parallel_requests, ex.llm_request_grouping, st.llm.provider)
    yield
    (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, ex.llm_parallel_requests,
     ex.llm_request_grouping, st.llm.provider) = was


class _Slow:
    """Answers each line with its own printed row, slowly, recording overlap."""
    id = "slow"

    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.log: list[tuple[str, int]] = []      # ("start"/"end", call number)
        self.n = 0

    def complete_structured(self, *, system, messages, response_schema, **_):
        with self.lock:
            self.n += 1
            me = self.n
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.log.append(("start", me))
        time.sleep(0.05)
        keys = [e["key"] for e in json.loads(messages[-1]["content"])["line_items"]]
        with self.lock:
            self.active -= 1
            self.log.append(("end", me))
        return response_schema.model_validate({"answers": [
            {"key": k, "confidence": 0.9, "reason": "r",
             "sources": [{"statement": "balance_sheet", "caption": LINES[k][0]}]}
            for k in keys if k in LINES]}), {"input_tokens": 1, "output_tokens": 1}


def _run(shipped, workers: int):
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=3, kind=PageKind.FACE, statement="balance_sheet")]
    for key, (caption, value) in LINES.items():
        li = LineItem(source_label=caption, canonical_key=key)
        li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                        value=Decimal(value), value_raw=Decimal(value),
                                        provenance=Provenance(page_index=3))
        doc.line_items.append(li)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items, ctx.ontology = shipped, build_working_view(shipped)
    ex = ctx.settings.extraction
    ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, True, list(LINES)
    ex.llm_request_grouping, ex.llm_parallel_requests = "none", workers
    provider = _Slow()
    ctx.registry.register("llm", "slow", lambda: provider)
    ctx.settings.llm.provider = "slow"
    LineItemLlmStage().run(doc, ctx)
    figures = {li.canonical_key: sorted(str(v.value) for v in li.values.values())
               for li in doc.line_items}
    return figures, provider


def test_the_first_request_finishes_before_any_other_starts(shipped):
    _figures, provider = _run(shipped, workers=4)
    assert provider.n == len(LINES)
    first_end = provider.log.index(("end", 1))
    assert all(event == "end" or n == 1 for event, n in provider.log[:first_end + 1])


def test_after_the_first_the_requests_overlap(shipped):
    _figures, provider = _run(shipped, workers=4)
    assert provider.peak > 1


def test_one_at_a_time_and_in_parallel_write_the_same_figures(shipped):
    serial, p1 = _run(shipped, workers=1)
    parallel, _ = _run(shipped, workers=4)
    assert p1.peak == 1
    assert serial == parallel
