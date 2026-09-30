"""`extraction.llm_document_context = "full"`: the whole document with every line-item question.

What is pinned: the admin setting exists and is validated; a full-document request is TWO messages,
the document first and the question second; the document is built deterministically (same bytes
twice) and carries every note, claimed or not; and every adapter reports the cached share of the
prompt in the same terms.
"""
from __future__ import annotations

import json

from app.core.models import DocumentModel, NotesTable
from app.core.models.line_item import NoteItem
from app.ports.llm import anthropic_usage, openai_usage
from app.services import line_item_llm
from app.services.line_item_config import load_shipped_set
from app.services.settings_state import EXTRACTION_KNOBS


def test_the_admin_setting_offers_selected_and_full_and_defaults_to_selected():
    from app.config import ExtractionSettings
    knob = next(k for k in EXTRACTION_KNOBS if k.key == "llm_document_context")
    assert knob.kind == "choice" and knob.choices == ("selected", "full")
    assert ExtractionSettings().llm_document_context == "selected"


class _Recorder:
    id = "rec"

    def __init__(self):
        self.calls = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        return response_schema(answers=[]), {"input_tokens": 10, "output_tokens": 1}


def test_a_full_document_request_is_the_document_then_the_question():
    rec = _Recorder()
    line_item_llm.ask(rec, "SYSTEM", {"line_items": [{"key": "a"}]}, max_tokens=100,
                      document="THE DOCUMENT")
    msgs = rec.calls[0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "user"]
    assert msgs[0]["content"] == "THE DOCUMENT"
    assert json.loads(msgs[1]["content"]) == {"line_items": [{"key": "a"}]}


def test_without_a_document_the_request_is_one_message_as_before():
    rec = _Recorder()
    line_item_llm.ask(rec, "SYSTEM", {"line_items": [], "notes": []}, max_tokens=100)
    assert len(rec.calls[0]["messages"]) == 1


def _doc() -> DocumentModel:
    doc = DocumentModel(filename="f.pdf")
    doc.notes = [
        NotesTable(note_number="7", title="Profit before taxation",
                   items=[NoteItem(raw_label="Depreciation")], source_text="Charged in the year."),
        # A note NO line item claims: in "selected" mode it never reaches the model.
        NotesTable(note_number="41", title="Events after the reporting period",
                   source_text="None."),
    ]
    return doc


def test_the_document_carries_every_note_and_is_the_same_bytes_every_time():
    st = load_shipped_set(resolve=True)
    first = line_item_llm.document_message(line_item_llm.build_document(_doc(), st))
    again = line_item_llm.document_message(line_item_llm.build_document(_doc(), st))
    assert first == again
    notes = json.loads(first.split("\n", 1)[1])["notes"]
    assert [n["note"] for n in notes] == ["7", "41"]


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
