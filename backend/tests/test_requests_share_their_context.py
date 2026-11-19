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
from app.core.models.line_item import ExtractedValue, LineItem, NoteItem, NotesTable
from app.schemas.line_items import RequestGroup, load_line_item_set
from app.services import line_item_routes, text_lines
from app.services.line_item_requests import (
    RequestPlan,
    _sections_of_item,
    asked_about,
    capped,
    focus_keys,
    focused,
    lines_per_request,
    plan_requests,
    relabel,
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


def _groups(cfg, mode: str, notes=()):
    """The plans as `plan_requests` makes them: whole groups, no cap (the stage cuts)."""
    st = get_settings()
    st.extraction.llm_request_grouping = mode
    return plan_requests(cfg, list(notes), st)


def _note(number: str, title: str, rows):
    table = NotesTable(note_number=number, title=title, page_index=9)
    for caption, amount in rows:
        row = NoteItem(raw_label=caption, note_number=number)
        row.set_value(ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal(amount), value_raw=Decimal(amount),
                                     provenance=Provenance(page_index=9)))
        table.items.append(row)
    return table


#: Notes that put several of the shipped set's lines on ONE note set: the trade-payables lines on
#: note 23 (a face line among them), the related-party lines on note 30, the depreciation parts.
NOTES = (_note("14", "PROPERTY, PLANT AND EQUIPMENT", [("Depreciation", "120"), ("Buildings", "300")]),
         _note("七、21", "固定资产", [("折旧", "50"), ("房屋及建筑物", "80")]),
         _note("23", "TRADE AND OTHER PAYABLES", [("Trade payables", "900"), ("Accruals", "100")]),
         _note("30", "RELATED PARTY TRANSACTIONS", [("Sales to related parties", "10"),
                                                     ("Purchases from related parties", "5")]))


# ── the cap ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cap", [1, 7, 20, 100])
def test_every_asked_line_is_in_exactly_one_part_whatever_the_cap(shipped, cap):
    """THE PARTITION, which a caller depends on: cutting a group into parts must neither drop a
    line nor ask it twice."""
    want = {i.key for i in shipped.items if asked_about(i)}
    parts = capped(_groups(shipped, "identical", NOTES), cap)
    keys = [k for p in parts for k in p.keys]
    assert len(keys) == len(set(keys)), "a line is in two plans"
    assert set(keys) == want, "a line is in no plan"
    assert max(len(p.keys) for p in parts) <= cap


@pytest.mark.parametrize("cap", [1, 2, 7, 12, 20])
def test_parts_are_even_whatever_the_size(cap):
    """EVEN, NOT CEILING-SIZED: 77 at 20 is 20, 19, 19, 19 and 50 at 7 is 7, 7, 6, 6, 6, 6, 6, 6 —
    the parts of a group differ by one line at most, so no part is a statement block sent for one
    or two lines. Pinned on every size, not on the shipped groups' sizes, which happen to cut
    evenly either way at 20."""
    for n in range(1, 120):
        plan = RequestPlan(name=f"balance_sheet/bs_cl+{n - 1}", keys=tuple(f"k{i}" for i in range(n)),
                           notes=(), sections=(("balance_sheet", "bs_cl"),))
        parts = capped([plan], cap)
        sizes = [len(p.keys) for p in parts]
        assert [k for p in parts for k in p.keys] == list(plan.keys), (n, cap)
        assert len(parts) == -(-n // cap) and max(sizes) <= cap, (n, cap, sizes)
        assert max(sizes) - min(sizes) <= 1, (n, cap, sizes)
    group = RequestPlan(name="x", keys=tuple(f"k{i}" for i in range(77)), notes=())
    assert [len(p.keys) for p in capped([group], 20)] == [20, 19, 19, 19]
    group = RequestPlan(name="x", keys=tuple(f"k{i}" for i in range(50)), notes=())
    assert [len(p.keys) for p in capped([group], 7)] == [7, 7, 6, 6, 6, 6, 6, 6]


def test_a_cut_section_group_keeps_its_sections_and_numbers_its_parts(shipped):
    """A face group cut at 20: every part is the group's next lines, claimable under exactly the
    group's pairs, and named after the section with its number — `balance_sheet/bs_cl#2`."""
    whole = [p for p in _groups(shipped, "identical") if len(p.keys) > 20]
    assert whole, "no group is over 20 lines; the fixture no longer exercises the cap"
    by_key = {i.key: i for i in shipped.items}
    for group in whole:
        parts = capped([group], 20)
        assert [k for p in parts for k in p.keys] == list(group.keys), group.name
        for number, part in enumerate(parts, start=1):
            assert part.sections == group.sections and part.notes == group.notes == ()
            assert part.name == f"{group.name.rsplit('+', 1)[0]}#{number}", part.name
            for key in part.keys:
                assert _sections_of_item(by_key[key]) == tuple(part.sections), key


def test_a_cut_note_group_sends_every_part_the_groups_notes(shipped):
    """A note group cut into parts: EVERY PART carries the group's note block, which is also each
    of its lines' `notes_supplied` (`plan_and_notes` takes a line's notes from its group) — so a
    part never names a note it does not send. Each part is named after its own first line, so a
    failed request's log line names a line it held."""
    noted = [p for p in _groups(shipped, "identical", NOTES) if p.notes and len(p.keys) > 2]
    assert noted, "no note group of three or more lines; the fixture no longer exercises this"
    for group in noted:
        parts = capped([group], 2)
        assert len(parts) == -(-len(group.keys) // 2)
        for part in parts:
            assert part.notes == group.notes and part.notes, (group.name, part.name)
            assert part.sections == group.sections, (group.name, part.name)
            assert part.name in (part.keys[0], f"{part.keys[0]}+{len(part.keys) - 1}"), part.name
        assert len({p.name for p in parts}) == len(parts)


def test_a_trimmed_plan_is_named_after_the_lines_it_holds():
    """THE RUN LOG NAMES A REQUEST BY ITS PLAN, and a failed request's name is the only record of
    which lines it lost to the rules — so a plan named after its first line is renamed when that
    line leaves it, a section's plan counts its lines again, an authored name is kept."""
    by_line = RequestPlan(name="a+3", keys=("a", "b", "c", "d"), notes=("7",))
    by_section = RequestPlan(name="balance_sheet/bs_cl+9", keys=tuple("abcdefghij"), notes=())
    authored = RequestPlan(name="Depreciation, one note", keys=("a", "b", "c"), notes=("7",))
    assert relabel(by_line, ("c", "d")).name == "c+1"
    assert relabel(by_line, ("d",)).name == "d"
    assert relabel(by_section, ("b", "c")).name == "balance_sheet/bs_cl+1"
    assert relabel(authored, ("b",)).name == "Depreciation, one note"
    kept = focused([by_line, by_section, authored], {"c", "z"})
    assert [(p.name, p.keys) for p in kept] == [("c", ("c",)), ("c", ("c",)),
                                                 ("Depreciation, one note", ("c",))]
    assert all(p.notes == q.notes for p, q in zip(kept, (by_line, by_section, authored)))


def test_focus_keys_expand_a_pattern_to_this_sets_own_lines(shipped):
    keys = focus_keys(["bs_nca__land", "bs_cl__*", "nope__*"], shipped)
    assert "bs_nca__land" in keys
    assert keys - {"bs_nca__land"} == {i.key for i in shipped.items
                                       if i.key.startswith("bs_cl__") and asked_about(i)}


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


# ── what the stage sends ────────────────────────────────────────────────────────────────────

FACE = {"bs_nca__land": ("Land", "100"), "bs_nca__buildings": ("Buildings", "200")}
#: A part read only from its note; this document prints no note at all.
NOTE_ONLY = "sub__ppe_depreciation"


class _Recorder:
    """Records every line-item request (its question, and whether a first message — the report —
    went before it) and answers each face line with its printed row."""

    id = "recorder"

    def __init__(self):
        self.requests: list[dict] = []
        self.with_report: list[bool] = []

    def complete_structured(self, *, system, messages, response_schema, **_):
        if response_schema.__name__ != "LineItemReply":
            return response_schema(), {"model": "recorder"}
        body = json.loads(messages[-1]["content"])
        self.requests.append(body)
        self.with_report.append(len(messages) > 1)
        return response_schema.model_validate({"answers": [
            {"key": e["key"], "confidence": 0.9, "reason": "r",
             "sources": [{"statement": "balance_sheet", "caption": FACE[e["key"]][0]}]}
            for e in body["line_items"] if e["key"] in FACE]}), {"input_tokens": 1,
                                                                 "output_tokens": 1}


def _run(cfg, keys, *, mode="none", context="selected", cap=None, pl=False):
    """The stage on a filing that prints two balance-sheet rows (and, with `pl`, one P&L row), asked
    about `keys` (the focus list; None asks every line)."""
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view

    doc = DocumentModel(filename="ar.pdf")
    doc.pages = [PageSource(index=3, kind=PageKind.FACE, statement="balance_sheet")]
    rows = [(k, c, v, 3) for k, (c, v) in FACE.items()]
    if pl:
        doc.pages.append(PageSource(index=4, kind=PageKind.FACE, statement="profit_and_loss"))
        rows.append(("is_pl__revenue", "Revenue", "900", 4))
    for key, caption, value, page in rows:
        li = LineItem(source_label=caption, canonical_key=key)
        li.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                        value=Decimal(value), value_raw=Decimal(value),
                                        provenance=Provenance(page_index=page))
        doc.line_items.append(li)
    ctx = PipelineContext(raw_bytes=b"", settings=get_settings())
    ctx.line_items, ctx.ontology = cfg, build_working_view(cfg)
    ex = ctx.settings.extraction
    ex.llm_mapping, ex.llm_focus_only = True, keys is not None
    ex.llm_focus_keys = list(keys or ())
    ex.llm_request_grouping, ex.llm_parallel_requests, ex.llm_note_router = mode, 2, False
    if cap is not None:
        ex.llm_lines_per_request = cap
    # THE SELECTED CONTEXT, unless a test says otherwise: under the shipped "anywhere", a line read
    # from anywhere (`bs_nca__gross_fixed_assets` among them) is moved into a batch asked with the
    # whole report, which always has something to read.
    ex.llm_document_context = context
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
    # THE PROGRESS PANEL COUNTS WHAT IS SENT, from its first step to its last.
    sent = len(provider.requests)
    assert steps and all(total == sent for _done, total in steps), steps
    assert steps[-1] == (sent, sent), steps


def test_when_nothing_is_sent_the_panel_counts_nothing(shipped):
    """Every request skipped: the panel's one step is 0 of 0, which it does not show as a count —
    not "0 of 1" for the rest of the stage."""
    _doc, _ctx, provider, steps = _run(shipped, [NOTE_ONLY])
    assert provider.requests == [] and steps == [(0, 0)], steps


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


def test_a_group_is_cut_by_the_lines_it_sends(shipped):
    """THE CAP IS APPLIED TO WHAT GOES, after the `anywhere` lines have left for the report (and
    the text lines for their own requests): the P&L section's planned lines include `anywhere`
    ones, and cut before they left, its remaining lines went in one request more than the cap
    needs, each with the whole statement. Cut after, they go in ceil(sent / 20) even parts."""
    by_key = {i.key: i for i in shipped.items}
    (pl,) = [g for g in _groups(shipped, "identical") if g.name.startswith("profit_and_loss/")
             and len(g.keys) > 20]
    sent = [k for k in pl.keys if not line_item_routes.reads_every_page(by_key[k])
            and not text_lines.is_text_line(by_key[k])]
    assert len(sent) < len(pl.keys) and -(-len(sent) // 20) < -(-len(pl.keys) // 20), \
        "the P&L group no longer loses enough lines to the report to need fewer parts"
    _doc, _ctx, provider, _steps = _run(shipped, None, mode="identical", context="anywhere",
                                        pl=True)
    mine = [r for r, report in zip(provider.requests, provider.with_report)
            if not report and {e["key"] for e in r["line_items"]} <= set(pl.keys)]
    sizes = [len(r["line_items"]) for r in mine]
    assert sum(sizes) == len(sent), sizes
    assert len(mine) == -(-len(sent) // 20) and max(sizes) - min(sizes) <= 1, sizes


def test_the_stage_does_not_cut_an_authored_group(shipped):
    """An authored ("manual") group is asked as its author wrote it, whatever the cap — it exists
    because those lines are better judged together."""
    members = ["bs_nca__land", "bs_nca__buildings", "bs_nca__leasehold_improvements",
               "bs_nca__plant_and_equipment"]
    cfg = shipped.model_copy(update={"request_groups": [RequestGroup(name="Fixed assets",
                                                                     members=members)]})
    _doc, ctx, provider, _steps = _run(cfg, members, mode="manual", cap=2)
    assert [sorted(e["key"] for e in r["line_items"]) for r in provider.requests] == [
        sorted(members)]
    assert any("request=Fixed assets lines=4" in line for line in ctx.logs)


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
    whole = [p for p in _groups(shipped, "identical") if "bs_nca__land" in p.keys]
    assert len(whole) == 1 and len(whole[0].keys) > 1, "land shares no group; fixture moved"
    _doc, ctx, provider, _steps = _run(shipped, ["bs_nca__land"], mode="identical")
    assert [[e["key"] for e in r["line_items"]] for r in provider.requests] == [["bs_nca__land"]]
    assert provider.requests[0].get("statement_rows")
    assert any(line.startswith("line_item_llm:focus keys=1 requests=1/") for line in ctx.logs)


# ── a value saved before the default moved ──────────────────────────────────────────────────
#
# The Settings screen saves EVERY knob whenever one changes, so a stored "none" from a release that
# shipped "none" is almost always that default, never chosen — read as written it would keep the
# deployment on one request per line after "identical" shipped.

def _admin(client):
    tok = client.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _restart() -> None:
    from app.services import settings_state
    settings_state.reset(persisted=False)
    settings_state.load_persisted()


def _rows() -> dict:
    from sqlalchemy import select

    from app.db.base import SessionLocal
    from app.db.models import SettingOverride
    from app.services import settings_state
    with SessionLocal() as s:
        return {r.key: (r.value or {}).get("v") for r in s.execute(
            select(SettingOverride).where(
                SettingOverride.scope == settings_state.SCOPE_EXTRACTION)).scalars().all()}


@pytest.fixture
def overrides(client):
    from app.services import settings_state
    settings_state.reset()
    yield client
    settings_state.reset()


def test_a_none_saved_before_is_read_and_saved_as_the_shipped_grouping(overrides):
    from app.db.base import SessionLocal
    from app.db.models import SettingOverride
    from app.services import settings_state
    with SessionLocal() as s:
        s.add(SettingOverride(scope=settings_state.SCOPE_EXTRACTION, key="llm_request_grouping",
                              value={"v": "none"}))
        s.commit()
    _restart()
    assert get_settings().extraction.llm_request_grouping == "identical"
    rows = _rows()
    assert rows["llm_request_grouping"] == "identical", "the reading is saved, so made once"
    assert rows[settings_state._REQUEST_GROUPING_MARKER] is True
    _restart()
    assert get_settings().extraction.llm_request_grouping == "identical"


def test_a_none_chosen_now_survives_a_restart(overrides):
    client = overrides
    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"extraction": {"llm_request_grouping": "none"}})
    assert get_settings().extraction.llm_request_grouping == "none"
    _restart()
    assert get_settings().extraction.llm_request_grouping == "none"
    live = client.get("/api/v1/settings", headers=_admin(client)).json()["extraction"]
    assert live["llm_request_grouping"] == "none"
    assert not any(k.startswith("_") for k in live), "the marker is not a setting"


# ── parallel calls ───────────────────────────────────────────────────────────────────────────

def test_the_disclosure_readings_follow_the_parallel_setting(monkeypatch):
    """The one switch an admin lowers when the gateway refuses for rate limits lowers the
    disclosure readings too. They had a fixed 4 of their own."""
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
