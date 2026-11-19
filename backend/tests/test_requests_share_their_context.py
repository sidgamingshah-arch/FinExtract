"""Fewer model calls on the normal route: lines that share a context share a request, at most
`extraction.llm_lines_per_request` of them, and a request with nothing to read is not sent.

NEW FILE -> backend/tests/test_requests_share_their_context.py

WHY. Asked: "Under the normal route why would you want to have over 500 calls, is there a way to be
smart about it. Also, can we have parallel calls". With the focus list off, `llm_request_grouping =
"none"` made one request per line — measured on the six reference filings, 431-433 model calls a
filing, 349 of them a single face line each sent its whole statement, and 13-22 of them requests
carrying no note, no statement row and no page at all. "identical" now ships, capped at 20 lines a
request (the reply has to arrive inside one 120-second wait), and a request with nothing to read is
named in the log instead of sent. Parallel calls were already there (`llm_parallel_requests`, 4 as
shipped); the disclosure readings now follow the same setting instead of a fixed 4 of their own.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.schemas.line_items import RequestGroup, load_line_item_set
from app.services.line_item_requests import (
    _sections_of_item,
    asked_about,
    lines_per_request,
    plan_requests,
)
from app.stages.line_item_llm import LineItemLlmStage

SEED = (pathlib.Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def shipped():
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=True)


@pytest.fixture(autouse=True)
def _restore():
    """`get_settings` is `lru_cache`d: the settings object is the whole suite's."""
    st = get_settings()
    ex = st.extraction
    names = ("llm_mapping", "llm_focus_only", "llm_focus_keys", "llm_request_grouping",
             "llm_lines_per_request", "llm_parallel_requests", "llm_note_router",
             "llm_document_context")
    was = {n: getattr(ex, n) for n in names}
    provider = st.llm.provider
    try:
        yield
    finally:
        for n, v in was.items():
            setattr(ex, n, v)
        st.llm.provider = provider


def _plans(cfg, mode: str, cap: int):
    st = get_settings()
    st.extraction.llm_request_grouping = mode
    st.extraction.llm_lines_per_request = cap
    return plan_requests(cfg, [], st)


# ── the cap ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cap", [1, 7, 20, 100])
def test_every_asked_line_is_in_exactly_one_plan_whatever_the_cap(shipped, cap):
    """THE PARTITION, which a caller depends on: cutting a group into parts must neither drop a
    line nor ask it twice."""
    want = {i.key for i in shipped.items if asked_about(i)}
    keys = [k for p in _plans(shipped, "identical", cap) for k in p.keys]
    assert len(keys) == len(set(keys)), "a line is in two plans"
    assert set(keys) == want, "a line is in no plan"
    assert max(len(p.keys) for p in _plans(shipped, "identical", cap)) <= cap


def test_a_group_over_the_cap_is_asked_in_even_parts_in_its_own_order(shipped):
    """77 at 20 is 20, 19, 19, 19 — not 20, 20, 20, 17 — and each part is the group's next lines,
    with the group's statement section and notes: cutting changes how many requests there are, not
    what any line is shown."""
    whole = [p for p in _plans(shipped, "identical", 100) if len(p.keys) > 20]
    assert whole, "no group is over 20 lines; the fixture no longer exercises the cap"
    parts = _plans(shipped, "identical", 20)
    by_key = {i.key: i for i in shipped.items}
    for group in whole:
        mine = [p for p in parts if p.keys[0] in group.keys]
        assert [k for p in mine for k in p.keys] == list(group.keys), group.name
        sizes = [len(p.keys) for p in mine]
        assert len(mine) == -(-len(group.keys) // 20), (group.name, sizes)
        assert max(sizes) - min(sizes) <= 1, (group.name, sizes)
        for part in mine:
            assert part.sections == group.sections, (part.name, part.sections)
            assert set(part.notes) <= set(group.notes), part.name
            for key in part.keys:
                if group.notes:
                    break
                # A face group: every line in a part is still claimable under exactly its pairs.
                assert _sections_of_item(by_key[key]) == tuple(part.sections), key
        assert len({p.name for p in mine}) == len(mine), "two parts share a name in the log"


def test_the_cap_leaves_none_and_an_authored_group_alone(shipped):
    """"none" asks one line a request whatever the cap. An authored group ("manual") is asked as
    its author wrote it — it exists because those lines are better judged together."""
    assert all(len(p.keys) == 1 for p in _plans(shipped, "none", 3))
    parts = [i.key for i in shipped.items
             if getattr(i, "parent", "") == "is_pl__deprec_and_impairment_oper_exp"][:6]
    assert len(parts) == 6
    cfg = shipped.model_copy(update={"request_groups": [RequestGroup(name="Depreciation",
                                                                     members=parts)]})
    mine = [p for p in _plans(cfg, "manual", 3) if p.name == "Depreciation"]
    assert len(mine) == 1 and sorted(mine[0].keys) == sorted(parts)


def test_the_cap_is_read_from_the_settings_and_never_below_one():
    class _Ex:
        llm_lines_per_request = 0

    class _S:
        extraction = _Ex()

    assert lines_per_request(_S()) == 20          # 0 / unset reads as the shipped 20
    _S.extraction.llm_lines_per_request = 5
    assert lines_per_request(_S()) == 5
    _S.extraction.llm_lines_per_request = -3
    assert lines_per_request(_S()) == 1


# ── nothing to read, no request ─────────────────────────────────────────────────────────────

FACE = {"bs_nca__land": ("Land", "100"), "bs_nca__buildings": ("Buildings", "200")}
#: A part read only from its note; this document prints no note at all.
NOTE_ONLY = "sub__ppe_depreciation"


class _Recorder:
    """Records every line-item request and answers each face line with its printed row."""

    id = "recorder"

    def __init__(self):
        self.requests: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, **_):
        if response_schema.__name__ != "LineItemReply":
            return response_schema(), {"model": "recorder"}
        body = json.loads(messages[-1]["content"])
        self.requests.append(body)
        return response_schema.model_validate({"answers": [
            {"key": e["key"], "confidence": 0.9, "reason": "r",
             "sources": [{"statement": "balance_sheet", "caption": FACE[e["key"]][0]}]}
            for e in body["line_items"] if e["key"] in FACE]}), {"input_tokens": 1,
                                                                 "output_tokens": 1}


def _run(cfg, keys, *, mode="none"):
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view

    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=3, kind=PageKind.FACE, statement="balance_sheet")]
    for key, (caption, value) in FACE.items():
        li = LineItem(source_label=caption, canonical_key=key)
        li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                        value=Decimal(value), value_raw=Decimal(value),
                                        provenance=Provenance(page_index=3))
        doc.line_items.append(li)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items, ctx.ontology = cfg, build_working_view(cfg)
    ex = ctx.settings.extraction
    ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys = True, True, list(keys)
    ex.llm_request_grouping, ex.llm_parallel_requests, ex.llm_note_router = mode, 2, False
    # THE SELECTED CONTEXT ONLY: under the shipped "anywhere", a line read from anywhere
    # (`bs_nca__gross_fixed_assets` among them) is moved into a batch asked with the whole report,
    # which always has something to read and is not what these tests are about.
    ex.llm_document_context = "selected"
    steps: list[tuple[int, int]] = []
    ctx.step_cb = lambda done, total, _label: steps.append((done, total))
    provider = _Recorder()
    ctx.registry.register("llm", "recorder", lambda: provider)
    ctx.settings.llm.provider = "recorder"
    LineItemLlmStage().run(doc, ctx)
    return doc, ctx, provider, steps


def test_a_request_with_nothing_to_read_is_not_sent(shipped):
    """The note part's request would carry no note (this filing prints none), no statement row
    (its route is the notes) and no page: the model could only answer "not found". It is named in
    the log and not sent; the face lines are asked as before and still answered."""
    _doc, ctx, provider, steps = _run(shipped, [*FACE, NOTE_ONLY])
    asked = [e["key"] for r in provider.requests for e in r["line_items"]]
    assert sorted(asked) == sorted(FACE)
    assert all(r.get("statement_rows") for r in provider.requests)
    idle = [line for line in ctx.logs if line.startswith("line_item_llm:nothing_to_read")]
    assert len(idle) == 1 and NOTE_ONLY in idle[0] and "requests_not_sent=1" in idle[0], idle
    # THE PROGRESS PANEL COUNTS WHAT IS SENT, so a run with a skipped request still reaches 100%.
    done, total = steps[-1]
    assert (done, total) == (len(provider.requests), len(provider.requests)), steps


def test_grouped_face_lines_go_in_one_request_with_their_statement(shipped):
    """Under the shipped mode the two non-current-asset lines share one request — one copy of the
    balance sheet's rows instead of one each — and each is still answered with its own row."""
    doc, _ctx, provider, _steps = _run(shipped, list(FACE), mode="identical")
    assert len(provider.requests) == 1
    assert sorted(e["key"] for e in provider.requests[0]["line_items"]) == sorted(FACE)
    assert provider.requests[0].get("statement_rows")
    for key, (_caption, value) in FACE.items():
        got = [ev.value for li in doc.line_items if li.canonical_key == key
               for ev in li.values.values()]
        assert Decimal(value) in got, (key, got)


def test_a_group_trimmed_by_the_note_tag_gate_keeps_its_statement(shipped):
    """`llm_only_if_note_tagged` drops a line from its request when no face row cites a note it
    selected. The plan was rebuilt from its name, keys and notes, losing its sections — so the rest
    of a grouped request was sent no statement (and is now not sent at all, having nothing to read).
    It keeps them."""
    items = [i.model_copy(update={"llm_only_if_note_tagged": True}) if i.key == "bs_nca__buildings"
             else i for i in shipped.items]
    cfg = shipped.model_copy(update={"items": items})
    _doc, ctx, provider, _steps = _run(cfg, list(FACE), mode="identical")
    assert any(line.startswith("line_item_llm:note_tag_gate_skipped_lines=1") for line in ctx.logs)
    assert [[e["key"] for e in r["line_items"]] for r in provider.requests] == [["bs_nca__land"]]
    assert provider.requests[0].get("statement_rows"), "the trimmed request lost its statement"


def test_a_focus_run_asks_its_focus_lines_and_no_other_line_of_their_group(shipped):
    """Under grouping the focus list trims a group to its focus lines — one request, the group's
    statement block — instead of asking the whole group: every line outside the list keeps what the
    rules gave it, as the Settings screen says. Here one of the section's lines is in focus."""
    whole = [p for p in _plans(shipped, "identical", 20) if "bs_nca__land" in p.keys]
    assert len(whole) == 1 and len(whole[0].keys) > 1, "land shares no group; fixture moved"
    _doc, ctx, provider, _steps = _run(shipped, ["bs_nca__land"], mode="identical")
    assert [[e["key"] for e in r["line_items"]] for r in provider.requests] == [["bs_nca__land"]]
    assert provider.requests[0].get("statement_rows")
    assert any(line.startswith("line_item_llm:focus keys=1 requests=1/") for line in ctx.logs)


# ── parallel calls ───────────────────────────────────────────────────────────────────────────

def test_the_disclosure_readings_follow_the_parallel_setting(monkeypatch):
    """One switch for every batch of calls a run makes: an admin who lowers
    `llm_parallel_requests` because the gateway refuses for rate limits lowers the disclosure
    readings too. They had a fixed 4 of their own."""
    from app.core.stage import PipelineContext
    from app.ports.registry import registry
    from app.services import derived, disclosure_reading
    from app.stages.disclosure_reading import DisclosureReadingStage

    seen: list[int] = []

    def _read(provider, pages, **kw):
        seen.append(kw.get("parallel"))
        return {}

    monkeypatch.setattr(disclosure_reading, "read_disclosures", _read)
    monkeypatch.setattr(derived, "document_text", lambda raw, fmt: [(0, "Contingent liabilities")])
    monkeypatch.setattr(registry, "get", lambda kind, pid: object())
    ctx = PipelineContext(raw_bytes=b"%PDF", settings=get_settings())
    ctx.settings.llm.provider = "recorder"
    for width in (2, 7):
        ctx.settings.extraction.llm_parallel_requests = width
        DisclosureReadingStage().run(DocumentModel(filename="ar.pdf"), ctx)
    assert seen == [2, 7]
