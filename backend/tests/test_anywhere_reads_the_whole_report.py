"""AN `anywhere` LINE IS ASKED WITH THE WHOLE ANNUAL REPORT — batched, cached, citable by sentence.

NEW FILE -> backend/tests/test_anywhere_reads_the_whole_report.py

WHAT CHANGED. A line whose route is `anywhere` was sent what every request is sent — the notes its
configuration selected, its own statement's rows — plus at most four other pages whose rows are
captioned with one of its names (`face_context.other_page_rows`). Its route said "anywhere"; its
request said "these few pages". It is now sent the report:

  * EVERY PAGE'S TEXT, and every note, statement and other page in rows
    (`services.line_item_llm.build_report`), in one message sent before the question;
  * IN BATCHES of at most twelve lines, grouped by the statement the line is printed on
    (`services.line_item_requests.report_plans`), so a filing pays for the report a few times;
  * CACHED: the report message carries a prompt-cache breakpoint (`ports.llm.LlmMessage.cache`),
    and the first request of each prefix goes alone so the others read it from the cache;
  * AND A SENTENCE ON ANY PAGE MAY BE CITED by its page and its amount, checked against that page's
    text (`services.note_sourced._page_sentence`). A number printed with no currency beside it — a
    headcount, a share count, a ratio — is not divided by the statements' units.

Every other line's request is unchanged, byte for byte: that is pinned here too, because it is what
keeps the answers recorded for them valid.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.config import get_settings
from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis, DocFormat, PageKind
from app.core.models.geometry import Provenance
from app.core.models.line_item import (ExtractedValue, LineItem, NoteItem, NotesTable,
                                       UnitContext)
from app.schemas.line_items import load_line_item_set
from app.services import line_item_llm, line_item_requests, text_lines
from app.services.line_item_requests import RequestPlan
from app.services.mapping import SourceRef
from app.services.note_sourced import _currency_beside, resolve_sources
from app.stages.line_item_llm import _prose_in_statement_units

HEADCOUNT = "As at 31 December 2024, the Group had 2,431 employees (2023: 2,210)."
COVENANT = "Tangible net worth shall not be less than HK$1,100,000,000 at any time."
AUDITOR = "Ernst & Young, Certified Public Accountants"


@pytest.fixture(autouse=True)
def _restore_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()),
           ex.llm_parallel_requests, st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, ex.llm_parallel_requests,
         st.llm.provider) = was


def _pdf(pages: list[str]) -> bytes:
    import fitz
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text, fontsize=9)
    return pdf.tobytes()


PAGES = ["CONSOLIDATED STATEMENT OF FINANCIAL POSITION\nBuildings 4,200",
         "DIRECTORS' REPORT\n" + HEADCOUNT + "\n" + COVENANT,
         "INDEPENDENT AUDITOR'S REPORT\n" + AUDITOR]


def _doc() -> DocumentModel:
    doc = DocumentModel(filename="ar.pdf")
    doc.fmt = DocFormat.PDF
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, statement="balance_sheet"),
                 PageSource(index=1, kind=PageKind.UNKNOWN, statement=None, printed_page="12"),
                 PageSource(index=2, kind=PageKind.UNKNOWN, statement=None)]
    row = LineItem(source_label="Buildings", canonical_key="bs_nca__buildings")
    row.values["v"] = ExtractedValue(basis=Basis.CONSOLIDATED, period_label="current",
                                     value=Decimal("4200"), value_raw=Decimal("4200"),
                                     provenance=Provenance(page_index=0))
    doc.line_items = [row]
    note = NotesTable(note_number="7", title="PROPERTY, PLANT AND EQUIPMENT",
                      items=[NoteItem(raw_label="Buildings")],
                      source_text="Buildings are stated at cost.")
    doc.notes = [note]
    # A FILING PRESENTED IN THOUSANDS: a money amount stated in full in a sentence is divided by
    # 1,000 to sit in the statements' units, and a headcount must not be.
    doc.unit_context = UnitContext(currency="HKD", scale_factor=Decimal(1000))
    return doc


def _set(extra_face: int = 0):
    """A face line, two `anywhere` figure lines and an `anywhere` text line — and `extra_face`
    more face lines, each a request of its own."""
    extra = [{"key": f"bs_nca__face_{n}", "label": f"Face {n}", "inherits": "bs", "route": "face",
              "definition": "a face row", "type": "extracted"} for n in range(extra_face)]
    return load_line_item_set({
        "section_defaults": {"bs": {"statement": "balance_sheet", "section_scope": ["bs_nca"]},
                             "sup": {"statement": "covenants_supplemental"},
                             "setup": {"statement": "statement_setup"}},
        "items": [
            {"key": "bs_nca__buildings", "label": "Buildings", "inherits": "bs", "route": "face",
             "definition": "the buildings row", "type": "extracted"},
            {"key": "sup__employees", "label": "Num Employees", "inherits": "sup",
             "route": "anywhere", "definition": "the headcount", "type": "extracted"},
            {"key": "sup__tnw", "label": "Min Tangible Net Worth", "inherits": "sup",
             "route": "anywhere", "definition": "the covenant", "type": "extracted"},
            {"key": "setup__accountant", "label": "Accountant", "inherits": "setup",
             "route": "anywhere", "definition": "the auditor's name", "type": "extracted",
             "output_structure": "phrase"}, *extra]}, resolve=True)


class _Recorder:
    """Records every call and answers by key."""
    id = "report"

    def __init__(self, figures: dict, texts: dict | None = None):
        self.figures, self.texts = figures, texts or {}
        self.calls: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, **_):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages],
                           "schema": response_schema.__name__})
        asked = [e["key"] for e in json.loads(messages[-1]["content"])["line_items"]]
        pool = self.texts if response_schema.__name__ == "LineItemTextReply" else self.figures
        return response_schema.model_validate(
            {"answers": [pool[k] for k in asked if k in pool]}), {"input_tokens": 1}


def _run(provider, doc=None, extra_face: int = 0, **extraction):
    from app.core.stage import PipelineContext
    from app.services.working_view import build_working_view
    from app.stages.line_item_llm import LineItemLlmStage

    doc = doc or _doc()
    st = _set(extra_face)
    ctx = PipelineContext(raw_bytes=_pdf(PAGES), settings=get_settings())
    ctx.line_items, ctx.ontology = st, build_working_view(st)
    ctx.settings.extraction.llm_mapping = True
    ctx.settings.extraction.llm_focus_only = False
    ctx.settings.extraction.llm_focus_keys = []
    for name, value in extraction.items():
        setattr(ctx.settings.extraction, name, value)
    ctx.registry.register("llm", "report", lambda: provider)
    ctx.settings.llm.provider = "report"
    LineItemLlmStage().run(doc, ctx)
    return doc, ctx


def _figure(doc, key, period="current"):
    for li in doc.line_items:
        if li.canonical_key == key:
            for ev in li.values.values():
                if ev.period_label == period and ev.value is not None:
                    return ev.value
    return None


# ── THE BATCHES ──────────────────────────────────────────────────────────────────────────────

class _Item:
    def __init__(self, key, route, statement):
        self.key, self.route, self.statement, self.inherits = key, route, statement, ""


def test_every_anywhere_line_leaves_its_plan_for_a_batch_of_its_own_statement():
    items = ([_Item(f"bs{i}", "anywhere", "balance_sheet") for i in range(14)]
             + [_Item(f"pl{i}", "anywhere", "profit_and_loss") for i in range(3)]
             + [_Item("face1", "face", "balance_sheet"), _Item("note1", "note_tables", "")])
    by_key = {i.key: i for i in items}
    plans = [RequestPlan(name=i.key, keys=(i.key,), notes=(f"n{i.key}",)) for i in items]
    kept, report = line_item_requests.report_plans(plans, by_key)
    assert [p.keys for p in kept] == [("face1",), ("note1",)], "the other lines stay as planned"
    # FOURTEEN BALANCE-SHEET LINES ARE TWO EVEN BATCHES, not twelve and two.
    assert [len(p.keys) for p in report] == [7, 7, 3]
    assert {p.name for p in report} == {"report:balance_sheet#1", "report:balance_sheet#2",
                                        "report:profit_and_loss"}
    assert sorted(k for p in report for k in p.keys) == sorted(
        i.key for i in items if i.route == "anywhere")
    # A LINE'S OWN NOTES TRAVEL WITH IT, as its `notes_supplied`.
    assert report[2].notes == ("npl0", "npl1", "npl2")


def test_a_plan_with_no_anywhere_line_is_untouched():
    by_key = {"face1": _Item("face1", "face", "balance_sheet")}
    plans = [RequestPlan(name="face1", keys=("face1",), notes=())]
    assert line_item_requests.report_plans(plans, by_key) == (plans, [])


def test_the_shipped_set_asks_its_anywhere_lines_in_a_few_batches():
    from app.services.line_item_config import load_shipped_set
    st = load_shipped_set()
    by_key = {i.key: i for i in st.items if line_item_requests.asked_about(i)}
    plans = [RequestPlan(name=k, keys=(k,), notes=()) for k in by_key]
    kept, report = line_item_requests.report_plans(plans, by_key)
    moved = [k for p in report for k in p.keys]
    assert len(moved) == len(set(moved)) == 98
    assert all(len(p.keys) <= line_item_requests.REPORT_LINES_PER_REQUEST for p in report)
    assert len(report) <= 14, [p.name for p in report]
    assert len(kept) == len(by_key) - 98


# ── THE REPORT ───────────────────────────────────────────────────────────────────────────────

def test_the_report_is_every_page_then_every_note_statement_and_other_page():
    from app.services.derived import document_text
    doc = _doc()
    report = line_item_llm.build_report(doc, _set(), document_text(_pdf(PAGES), "pdf"))
    assert list(report) == ["pages", "notes", "statement_rows"], \
        "no other page prints a row here, so that block is omitted"
    assert [p["page"] for p in report["pages"]] == [1, 2, 3], "every page, 1-based"
    assert report["pages"][1]["printed_page"] == "12"
    assert "2,431 employees" in report["pages"][1]["text"]
    # A NOTE'S ROWS AND NOT ITS PROSE, which its pages already carry.
    assert [n["note"] for n in report["notes"]] == ["7"]
    assert "prose" not in report["notes"][0]
    message = line_item_llm.report_message(report)
    assert message == line_item_llm.report_message(
        line_item_llm.build_report(_doc(), _set(), document_text(_pdf(PAGES), "pdf"))), \
        "the same bytes every time: the property the prompt cache keys on"
    assert json.loads(message.split("\n", 1)[1]) == report
    assert line_item_llm.report_page_texts(report)[2] == report["pages"][1]["text"]


def test_every_page_keeps_an_empty_page_so_the_numbers_are_the_files():
    pages = text_lines.every_page([(0, "a  \nb"), (1, ""), (2, "c")])
    assert [(p["page"], p["text"]) for p in pages] == [(1, "a\nb"), (2, ""), (3, "c")]


def test_the_token_estimate_counts_a_chinese_character_as_a_token():
    assert line_item_llm.estimated_tokens("abcd" * 10) == 10
    assert line_item_llm.estimated_tokens("资产负债表") == 5


# ── THE SENTENCE ON A PAGE ───────────────────────────────────────────────────────────────────

def _texts():
    return {1: PAGES[0], 2: PAGES[1], 3: PAGES[2]}


def test_a_sentence_on_any_page_is_located_and_checked_against_that_page():
    res, un = resolve_sources([SourceRef(page=2, amount="2,431", quote=HEADCOUNT)], [],
                              allow_pages=True, pages=[], page_texts=_texts())
    assert not un, un
    (hit,) = res
    assert hit["figures"] == {"prose": "2431"} and hit["prose"] is True
    assert hit["provenance"] == {"page_index": 1, "source": "page_prose"}
    assert hit["prose_prior"] == "2210", "the comparative the sentence prints beside it"
    assert hit["prose_money"] is False, "no currency is printed against a headcount"


def test_an_amount_the_page_does_not_print_is_refused():
    res, un = resolve_sources([SourceRef(page=2, amount="2,432", quote=HEADCOUNT)], [],
                              allow_pages=True, pages=[], page_texts=_texts())
    assert not res and "does not appear in page 2's text" in un[0]["why"]
    res, un = resolve_sources([SourceRef(page=9, amount="2,431")], [], allow_pages=True,
                              pages=[], page_texts=_texts())
    assert not res and "not a page of this filing" in un[0]["why"]


def test_without_the_report_no_sentence_is_accepted_by_page():
    res, un = resolve_sources([SourceRef(page=2, amount="2,431", quote=HEADCOUNT)], [],
                              allow_pages=True, pages=[])
    assert not res and un, "only a line asked with the whole report may cite a page's sentence"


def test_money_is_told_from_a_count_by_the_currency_printed_against_it():
    assert _currency_beside("not be less than HK$", ",000 at any time")
    assert _currency_beside("净资产不低于人民币", "元")
    assert _currency_beside("revenue of ", " million dollars")
    assert _currency_beside("", "万元")
    assert not _currency_beside("the Group had ", " employees")
    assert not _currency_beside("a current ratio of ", " times")
    res, _un = resolve_sources([SourceRef(page=2, amount="1,100,000,000", quote=COVENANT)], [],
                               allow_pages=True, pages=[], page_texts=_texts())
    assert res[0]["prose_money"] is True


def test_a_count_is_not_divided_by_the_statements_units_and_money_is():
    thousands = Decimal(1000)
    assert _prose_in_statement_units(Decimal("2431"), thousands,
                                     [{"prose_money": False}]) == Decimal("2431")
    assert _prose_in_statement_units(Decimal("1100000000"), thousands,
                                     [{"prose_money": True}]) == Decimal("1100000")
    # A SCALE WORD STILL MULTIPLIES A COUNT: "1,250 million shares".
    assert _prose_in_statement_units(Decimal("1250"), thousands,
                                     [{"prose_money": False, "prose_scale": "1000000"}]) \
        == Decimal("1250000000")
    # A NOTE'S SENTENCE CARRIES NO SUCH READING AND IS MONEY, as before.
    assert _prose_in_statement_units(Decimal("529841000"), thousands, [{}]) == Decimal("529841")


# ── THE RUN ──────────────────────────────────────────────────────────────────────────────────

def _answers():
    return {"sup__employees": {"key": "sup__employees", "confidence": 0.9, "reason": "r",
                               "sources": [{"page": 2, "amount": "2,431", "quote": HEADCOUNT}]},
            "sup__tnw": {"key": "sup__tnw", "confidence": 0.9, "reason": "r",
                         "sources": [{"page": 2, "amount": "1,100,000,000", "quote": COVENANT}]}}


def test_an_anywhere_line_is_asked_with_the_report_and_every_other_line_as_before():
    provider = _Recorder(_answers(), {"setup__accountant": {
        "key": "setup__accountant", "text": AUDITOR, "page": 3, "confidence": 0.9}})
    doc, ctx = _run(provider)
    figure_calls = [c for c in provider.calls if c["schema"] == "LineItemReply"]
    face = [c for c in figure_calls if len(c["messages"]) == 1]
    report = [c for c in figure_calls if len(c["messages"]) == 2]
    assert len(face) == 1 and len(report) == 1, "the two anywhere figure lines share one batch"
    # THE FACE LINE'S REQUEST IS WHAT IT WAS: one message, the system text without the guide.
    assert json.loads(face[0]["messages"][0]["content"])["line_items"][0]["key"] == \
        "bs_nca__buildings"
    assert line_item_llm.REPORT_GUIDE not in face[0]["system"]
    assert "cache" not in face[0]["messages"][0]
    # THE REPORT REQUEST: the report first, marked for the cache, then the question.
    first, question = report[0]["messages"]
    assert first["content"].startswith("THE ANNUAL REPORT") and first["cache"] is True
    assert "cache" not in question
    assert report[0]["system"].endswith(line_item_llm.REPORT_GUIDE)
    lines = json.loads(question["content"])["line_items"]
    assert [e["key"] for e in lines] == ["sup__employees", "sup__tnw"]
    assert all(e["read_from"].startswith("anywhere in the annual report") for e in lines)
    assert set(json.loads(question["content"])) == {"line_items"}, "the question alone"
    # A HEADCOUNT IS PUBLISHED AS PRINTED; MONEY IN THE STATEMENTS' THOUSANDS.
    assert _figure(doc, "sup__employees") == Decimal("2431")
    assert _figure(doc, "sup__employees", "prior") == Decimal("2210")
    assert _figure(doc, "sup__tnw") == Decimal("1100000")
    row = next(li for li in doc.line_items if li.canonical_key == "sup__employees")
    assert "prose_sourced_value:page 2" in row.confidence.flags
    assert any("from prose in page 2" in line for line in ctx.logs)
    # THE TEXT LINE IS ASKED WITH THE SAME REPORT, under its own contract.
    (text_call,) = [c for c in provider.calls if c["schema"] == "LineItemTextReply"]
    assert text_call["messages"][0]["content"] == first["content"]
    assert text_call["system"].endswith(line_item_llm.TEXT_REPORT_GUIDE)
    accountant = next(li for li in doc.line_items if li.canonical_key == "setup__accountant")
    assert next(iter(accountant.values.values())).value_text == AUDITOR
    assert any(line.startswith("line_item_llm:report pages=3 ") for line in ctx.logs)


def test_the_first_request_of_each_prefix_goes_alone():
    """Two prefixes — the system text alone, and the report's system text with the report after
    it. The first request of each goes on its own, and nothing else starts until both are back:
    sent beside them, a request would pay for the prefix in full instead of reading it cached."""
    import threading
    import time

    class _Overlap(_Recorder):
        def __init__(self):
            super().__init__(_answers())
            self.lock, self.events = threading.Lock(), []

        def complete_structured(self, **kw):
            if kw["response_schema"].__name__ == "LineItemReply":
                with self.lock:
                    self.events.append(("start", len(kw["messages"])))
            time.sleep(0.05)
            try:
                return super().complete_structured(**kw)
            finally:
                if kw["response_schema"].__name__ == "LineItemReply":
                    with self.lock:
                        self.events.append(("end", len(kw["messages"])))

    provider = _Overlap()
    _run(provider, extra_face=3, llm_parallel_requests=4)
    events = provider.events
    assert len([e for e in events if e[0] == "start"]) == 5, events   # four face lines, one batch
    assert [e[0] for e in events[:4]] == ["start", "start", "end", "end"], events
    assert {e[1] for e in events[:2]} == {1, 2}, "one of each prefix went first"


def test_a_report_too_large_to_send_leaves_the_lines_to_the_selected_context(monkeypatch):
    monkeypatch.setattr(line_item_llm, "REPORT_TOKEN_BUDGET", 10)
    provider = _Recorder(_answers())
    doc, ctx = _run(provider)
    assert any(line.startswith("line_item_llm:report_too_large") for line in ctx.logs)
    figure_calls = [c for c in provider.calls if c["schema"] == "LineItemReply"]
    assert all(len(c["messages"]) == 1 for c in figure_calls)
    asked = {json.loads(c["messages"][0]["content"])["line_items"][0]["key"]
             for c in figure_calls}
    assert {"sup__employees", "sup__tnw"} <= asked, "asked one at a time, as planned"
    # Without the report no sentence is accepted by its page.
    assert _figure(doc, "sup__employees") is None


def test_a_report_with_no_page_text_asks_no_text_line():
    provider = _Recorder(_answers())
    doc = _doc()
    doc.fmt = DocFormat.UNKNOWN            # no text layer can be read: the report is rows alone
    _run(provider, doc=doc)
    assert not [c for c in provider.calls if c["schema"] == "LineItemTextReply"]
    assert [c for c in provider.calls if len(c["messages"]) == 2], \
        "the figure lines are still asked with the report"


# ── THE STAND-IN READS A REPORT REQUEST ──────────────────────────────────────────────────────

def test_the_stand_in_reads_a_cited_row_from_the_report_message():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "llm_standin.py"
    spec = importlib.util.spec_from_file_location("llm_standin_report", path)
    standin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standin)
    report = {"pages": [{"page": 1, "text": "x"}],
              "statement_rows": [{"statement": "balance_sheet", "rows": []}]}
    body = {"messages": [{"role": "user", "content": line_item_llm.report_message(report)},
                         {"role": "user", "content": json.dumps({"line_items": [{"key": "k"}]})}]}
    payload = standin.payload_of(body)
    assert payload["line_items"] == [{"key": "k"}]
    assert payload["pages"] == report["pages"]
    assert payload["statement_rows"] == report["statement_rows"]
    assert standin.line_keys_of(payload) == ["k"]
