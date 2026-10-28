"""`extraction.llm_document_context`: what the model reads with each line-item question.

ONE SWITCH, THREE SETTINGS, cheapest first:

  * "selected"  each request carries only what its lines selected — their notes, their statements'
                rows, the pages that print their names. No line is sent the whole annual report,
                an `anywhere` line included;
  * "anywhere"  the same, and every line whose route is `anywhere` is asked with the WHOLE annual
                report, in batches of its own (pinned in `test_anywhere_reads_the_whole_report.py`).
                The default;
  * "full"      the whole annual report with EVERY question, figure and text alike, under one
                system text — so every figure request begins with the same bytes and a provider
                caches the report once.

IT WAS TWO SWITCHES IN ONE. This setting was "selected" | "full", where "full" sent every request a
document of its own (every note and every statement, no page's text), and the `anywhere` lines
were asked with the report under either. That second document is gone: "full" sends the report.

What is pinned: the three choices and the default; what each setting sends; the report too large
to send under "full"; a value saved before the setting had three choices; the two-message
request; and the cached share every adapter reports.
"""
from __future__ import annotations

import json
import threading
import time
import tomllib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.core.models import DocumentModel, NotesTable
from app.core.models.line_item import NoteItem
from app.db.base import SessionLocal
from app.db.models import SettingOverride
from app.ports.llm import anthropic_usage, openai_usage
from app.services import line_item_llm, settings_state
from app.services.line_item_config import load_shipped_set
from app.services.settings_state import EXTRACTION_KNOBS
from app.stages.line_item_llm import _document_context
from tests.test_anywhere_reads_the_whole_report import AUDITOR, _answers, _figure, _Recorder, _run


@pytest.fixture(autouse=True)
def _restore_settings():
    st = get_settings()
    ex = st.extraction
    was = (ex.llm_mapping, ex.llm_focus_only, list(ex.llm_focus_keys or ()),
           ex.llm_parallel_requests, ex.llm_document_context, st.llm.provider)
    try:
        yield
    finally:
        (ex.llm_mapping, ex.llm_focus_only, ex.llm_focus_keys, ex.llm_parallel_requests,
         ex.llm_document_context, st.llm.provider) = was


def _texts():
    return {"setup__accountant": {"key": "setup__accountant", "text": AUDITOR, "page": 3,
                                  "confidence": 0.9}}


# ── THE SETTING ──────────────────────────────────────────────────────────────────────────────

def test_the_setting_offers_three_choices_and_ships_anywhere():
    from app.config import ExtractionSettings
    knob = next(k for k in EXTRACTION_KNOBS if k.key == "llm_document_context")
    assert knob.kind == "choice" and knob.choices == ("selected", "anywhere", "full")
    assert ExtractionSettings().llm_document_context == "anywhere"
    shipped = tomllib.loads((Path(__file__).resolve().parent.parent / "config.toml")
                            .read_text(encoding="utf-8"))
    assert shipped["extraction"]["llm_document_context"] == "anywhere"
    with pytest.raises(ValueError, match="one of selected, anywhere, full"):
        settings_state.set_extraction_config(llm_document_context="document", persist=False)


def test_a_value_this_build_does_not_know_is_read_as_the_default():
    for value, read in (("selected", "selected"), ("anywhere", "anywhere"), ("full", "full"),
                        ("document", "anywhere"), ("", "anywhere"), (None, "anywhere")):
        assert _document_context(NS(settings=NS(extraction=NS(
            llm_document_context=value)))) == read, value


# ── WHAT EACH SETTING SENDS ──────────────────────────────────────────────────────────────────

def test_selected_sends_no_line_the_report():
    provider = _Recorder(_answers(), _texts())
    doc, ctx = _run(provider, llm_document_context="selected")
    assert provider.calls and all(len(c["messages"]) == 1 for c in provider.calls), \
        "no request carries a document"
    assert not any(line_item_llm.REPORT_GUIDE in c["system"] for c in provider.calls)
    assert not any(line_item_llm.TEXT_REPORT_GUIDE in c["system"] for c in provider.calls)
    assert not any(line.startswith("line_item_llm:report") for line in ctx.logs)
    # THE `anywhere` LINES ARE ASKED AS THEY WERE PLANNED — one request each, with what they
    # selected — as the face line is.
    asked = sorted(tuple(e["key"] for e in json.loads(c["messages"][0]["content"])["line_items"])
                   for c in provider.calls if c["schema"] == "LineItemReply")
    assert asked == [("bs_nca__buildings",), ("sup__employees",), ("sup__tnw",)]
    # …so no sentence on a page is accepted by its page number: that needs the report's text.
    assert _figure(doc, "sup__employees") is None
    assert any("line_item_llm:tokens document_context=selected" in line for line in ctx.logs)


def test_full_sends_the_report_with_every_question_under_one_system_text():
    provider = _Recorder(_answers(), _texts())
    doc, ctx = _run(provider, extra_face=2, llm_document_context="full")
    figure_calls = [c for c in provider.calls if c["schema"] == "LineItemReply"]
    assert len(figure_calls) == 4, "three face lines one by one, the two anywhere lines together"
    assert all(len(c["messages"]) == 2 for c in provider.calls), "every request carries the report"
    report = figure_calls[0]["messages"][0]
    assert report["content"].startswith("THE ANNUAL REPORT") and report["cache"] is True
    assert all(c["messages"][0] == report for c in provider.calls), "the same report every time"
    # ONE PREFIX FOR EVERY FIGURE REQUEST: the same system text, the `anywhere` batch's included.
    (system,) = {c["system"] for c in figure_calls}
    assert system.endswith(line_item_llm.FULL_REPORT_GUIDE)
    assert line_item_llm.REPORT_GUIDE not in system, "the paragraph for anywhere-only requests"
    # THE QUESTION ALONE, the face line's too: its notes and its statement are in the report.
    questions = {json.loads(c["messages"][1]["content"])["line_items"][0]["key"]:
                 json.loads(c["messages"][1]["content"]) for c in figure_calls}
    assert set(questions["bs_nca__buildings"]) == {"line_items"}
    assert questions["bs_nca__buildings"]["line_items"][0]["read_from"].startswith(
        "the face of its statement only")
    # THE TEXT LINE IS SENT THE SAME REPORT, under its own contract.
    (text_call,) = [c for c in provider.calls if c["schema"] == "LineItemTextReply"]
    assert text_call["system"].endswith(line_item_llm.TEXT_REPORT_GUIDE)
    accountant = next(li for li in doc.line_items if li.canonical_key == "setup__accountant")
    assert next(iter(accountant.values.values())).value_text == AUDITOR
    # A SENTENCE ON A PAGE IS STILL CITED BY AN `anywhere` LINE, and checked against that page.
    assert _figure(doc, "sup__employees") == Decimal("2431")
    assert _figure(doc, "sup__tnw") == Decimal("1100000")
    assert any(line.startswith("line_item_llm:report pages=3 ") and "other_requests=3" in line
               and line.endswith("context=full") for line in ctx.logs), ctx.logs


def test_full_sends_its_first_request_alone_and_then_the_rest():
    """One prefix, so one request goes first: sent beside it, the others would each pay for the
    report in full instead of reading it from the cache."""
    class _Overlap(_Recorder):
        def __init__(self):
            super().__init__(_answers())
            self.lock, self.events = threading.Lock(), []

        def complete_structured(self, **kw):
            if kw["response_schema"].__name__ == "LineItemReply":
                with self.lock:
                    self.events.append("start")
            time.sleep(0.05)
            try:
                return super().complete_structured(**kw)
            finally:
                if kw["response_schema"].__name__ == "LineItemReply":
                    with self.lock:
                        self.events.append("end")

    provider = _Overlap()
    _run(provider, extra_face=3, llm_parallel_requests=4, llm_document_context="full")
    assert provider.events.count("start") == 5, provider.events
    assert provider.events[:3] == ["start", "end", "start"], provider.events


def test_full_with_a_report_too_large_asks_every_line_with_the_selected_context(monkeypatch):
    monkeypatch.setattr(line_item_llm, "REPORT_TOKEN_BUDGET", 10)
    provider = _Recorder(_answers(), _texts())
    _doc, ctx = _run(provider, llm_document_context="full")
    assert any(line.startswith("line_item_llm:report_too_large")
               and "the 4 line(s) are asked with the selected context" in line
               for line in ctx.logs), ctx.logs
    assert all(len(c["messages"]) == 1 for c in provider.calls)
    assert not any(line_item_llm.FULL_REPORT_GUIDE in c["system"] for c in provider.calls)


def test_the_report_carries_every_note_whether_a_line_claims_it_or_not():
    doc = DocumentModel(filename="f.pdf")
    doc.notes = [
        NotesTable(note_number="7", title="Profit before taxation",
                   items=[NoteItem(raw_label="Depreciation")], source_text="Charged in the year."),
        # A note NO line item claims: under "selected" it never reaches the model.
        NotesTable(note_number="41", title="Events after the reporting period",
                   source_text="None."),
    ]
    st = load_shipped_set(resolve=True)
    first = line_item_llm.report_message(line_item_llm.build_report(doc, st, []))
    assert first == line_item_llm.report_message(line_item_llm.build_report(doc, st, []))
    assert [n["note"] for n in json.loads(first.split("\n", 1)[1])["notes"]] == ["7", "41"]


# ── A VALUE SAVED BEFORE THE SETTING HAD THREE CHOICES ───────────────────────────────────────
#
# Under the two-choice setting, "selected" still asked every `anywhere` line with the whole
# report — what "anywhere" means now. The Settings screen saves every knob whenever one changes,
# so a stored "selected" from then is almost always the old default nobody chose.

def _admin(client):
    tok = client.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _restart() -> None:
    settings_state.reset(persisted=False)
    settings_state.load_persisted()


def _rows() -> dict:
    with SessionLocal() as s:
        return {r.key: (r.value or {}).get("v") for r in s.execute(
            select(SettingOverride).where(
                SettingOverride.scope == settings_state.SCOPE_EXTRACTION)).scalars().all()}


def _saved_by_an_older_release(value: str) -> None:
    with SessionLocal() as s:
        s.add(SettingOverride(scope=settings_state.SCOPE_EXTRACTION, key="llm_document_context",
                              value={"v": value}))
        s.commit()


@pytest.fixture
def overrides(client):
    settings_state.reset()
    yield client
    settings_state.reset()


def test_a_selected_saved_before_is_read_and_saved_as_anywhere(overrides):
    _saved_by_an_older_release("selected")
    _restart()
    assert get_settings().extraction.llm_document_context == "anywhere"
    assert _rows()["llm_document_context"] == "anywhere", "the reading is saved, so made once"
    _restart()
    assert get_settings().extraction.llm_document_context == "anywhere"


def test_a_selected_chosen_now_survives_a_restart(overrides):
    client = overrides
    client.patch("/api/v1/settings", headers=_admin(client),
                 json={"extraction": {"llm_document_context": "selected"}})
    assert get_settings().extraction.llm_document_context == "selected"
    _restart()
    assert get_settings().extraction.llm_document_context == "selected"
    live = client.get("/api/v1/settings", headers=_admin(client)).json()["extraction"]
    assert live["llm_document_context"] == "selected"
    assert not any(k.startswith("_") for k in live), "the marker is not a setting"


def test_a_full_saved_before_is_read_as_it_is(overrides):
    _saved_by_an_older_release("full")
    _restart()
    assert get_settings().extraction.llm_document_context == "full"


# ── THE REQUEST ──────────────────────────────────────────────────────────────────────────────

class _Asked:
    id = "rec"

    def __init__(self):
        self.calls = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        return response_schema(answers=[]), {"input_tokens": 10, "output_tokens": 1}


def test_a_request_sent_a_document_is_the_document_then_the_question():
    rec = _Asked()
    line_item_llm.ask(rec, "SYSTEM", {"line_items": [{"key": "a"}]}, max_tokens=100,
                      document="THE DOCUMENT")
    msgs = rec.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "user"]
    assert msgs[0]["content"] == "THE DOCUMENT"
    assert json.loads(msgs[1]["content"]) == {"line_items": [{"key": "a"}]}


def test_without_a_document_the_request_is_one_message_as_before():
    rec = _Asked()
    line_item_llm.ask(rec, "SYSTEM", {"line_items": [], "notes": []}, max_tokens=100)
    assert len(rec.calls[0]["messages"]) == 1


def test_every_adapter_reports_the_cached_share_as_part_of_the_whole_prompt():
    # Chat Completions: prompt_tokens already includes the cached tokens.
    assert openai_usage({"prompt_tokens": 1000, "completion_tokens": 5,
                         "prompt_tokens_details": {"cached_tokens": 800}}) == {
        "input_tokens": 1000, "output_tokens": 5, "cached_input_tokens": 800,
        "cache_write_tokens": 0}
    # Messages API: input_tokens EXCLUDES reads and writes, so the whole prompt is the sum.
    assert anthropic_usage({"input_tokens": 50, "output_tokens": 5,
                            "cache_read_input_tokens": 800,
                            "cache_creation_input_tokens": 150}) == {
        "input_tokens": 1000, "output_tokens": 5, "cached_input_tokens": 800,
        "cache_write_tokens": 150}
    # Nothing reported is zero, not an error.
    assert openai_usage(None)["cached_input_tokens"] == 0
