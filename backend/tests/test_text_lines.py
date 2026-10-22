"""Text-valued lines — the auditor's opinion, the entity's name, the currency, the framework, the
auditor — are answerable: their request carries the pages that print them, the reply can give the
text as printed with its page, the text is checked against that page, the stage writes it onto the
line and the export writes it (`services.text_lines`, `services.line_item_llm.TEXT_REPLY_CONTRACT`).

Before this, each of these lines was sent as a figure request with nothing in it — no page, no note,
no statement row — under a contract that forbade stating anything, so none could be answered.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.routes.extractions import _build_supplemental_rows, _serialize_rows
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import DocFormat
from app.core.models.line_item import UnitContext
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services import line_item_llm, text_lines
from app.services.line_item_notes import printed_names
from app.services.line_item_requests import RequestPlan
from app.stages import line_item_llm as stage

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
HK_SET = TEMPLATES / "output_csv_hk_line_items.json"
HK_TEMPLATE = TEMPLATES / "output_csv_hk_v1_template.json"

#: The text lines of the HK template, every one of them read off a page as printed words.
TEXT_KEYS = {
    "notes__auditor_s_opinion",
    *(f"statement_setup_controls__{k}" for k in (
        "customer_s_name", "customer_statement_type", "rounding", "source_currency",
        "statement_date", "audit_opinion_stmt_source", "accounting_standard", "accountant",
        "statement_type", "reconcile_to")),
}

AUDIT_PAGE = ("Independent Auditor's Report\nOPINION\nIn our opinion, the consolidated financial "
              "statements give a true and\nfair view of the consolidated financial position.\n"
              "Ernst & Young\nCertified Public Accountants\nHong Kong")
PAGES = [(0, "Annual Report 2024"), (1, "Chairman's statement. The auditor was thanked."),
         (2, AUDIT_PAGE), (3, "Corporate Information\nAUDITOR\nErnst & Young\nCertified Public "
                              "Accountants\nRegistered Public Interest Entity Auditor")]


@pytest.fixture(scope="module")
def items():
    cfg = load_line_item_set(json.loads(HK_SET.read_text(encoding="utf-8")), resolve=True)
    return {i.key: i for i in cfg.items}


# ── the configuration ─────────────────────────────────────────────────────────────────────────

def test_every_text_line_of_the_hk_set_is_read_as_printed_words(items):
    assert {k for k, i in items.items() if text_lines.is_text_line(i)} == TEXT_KEYS


def test_every_text_line_names_the_pages_that_print_it_in_both_scripts(items):
    """A name under four characters selects nothing, so each line needs longer ones — an English
    one for an HKEX filing and a Chinese one for a mainland filing."""
    for key in TEXT_KEYS:
        names = [n for n in printed_names(items[key]) if len(text_lines.fold(n)) >= 4]
        assert any(n.isascii() for n in names), key
        assert any(not n.isascii() for n in names), key


# ── the pages a request carries ───────────────────────────────────────────────────────────────

def test_the_pages_that_print_most_of_the_names_are_selected_not_every_page():
    names = ["Independent Auditor", "Certified Public Accountants", "Auditor"]
    assert text_lines.select_pages(PAGES, names) == [1, 2, 3]
    assert text_lines.select_pages(PAGES, names, cap=2) == [2, 3]
    assert text_lines.select_pages(PAGES, ["Opinion"]) == [2]


def test_a_name_too_short_to_discriminate_selects_nothing():
    assert text_lines.select_pages(PAGES, ["Opi", "AR"]) == []


def test_the_block_numbers_pages_from_one_and_carries_their_folio():
    doc = SimpleNamespace(pages=[PageSource(index=2, printed_page="73")])
    blocks = text_lines.text_pages(PAGES, ["Certified Public Accountants"], doc)
    assert [b["page"] for b in blocks] == [3, 4]
    assert blocks[0]["printed_page"] == "73" and "Ernst & Young" in blocks[0]["text"]


def test_a_text_request_carries_its_pages_and_no_note_or_statement(items):
    key = "statement_setup_controls__accountant"
    plan = RequestPlan(name=key, keys=(key,), notes=())
    blocks = text_lines.text_pages(PAGES, printed_names(items[key]))
    request = line_item_llm.build_text_request(plan, {key: items[key]}, blocks)
    line = request["line_items"][0]
    assert line["key"] == key and line["output"] == "text"
    assert "notes_supplied" not in line and "statement" not in line
    assert set(request) == {"line_items", "text_pages"}
    assert any("Ernst & Young" in b["text"] for b in request["text_pages"])


# ── the reply, and the check against the page ─────────────────────────────────────────────────

def test_the_text_reply_holds_a_text_and_its_page():
    reply = line_item_llm.LineItemTextReply.model_validate_json(json.dumps(
        {"answers": {"items": [{"key": "k", "text": "Ernst & Young", "page": 3}]}}))
    assert reply.answers[0].text == "Ernst & Young" and reply.answers[0].page == 3
    assert "Do NOT state a figure" not in line_item_llm.TEXT_REPLY_CONTRACT


def test_the_figure_reply_is_unchanged_so_no_figure_request_changes():
    fields = set(line_item_llm.LineItemAnswer.model_fields)
    assert fields == {"key", "sources", "role", "signs", "confidence", "reason"}


def test_a_text_is_found_across_line_breaks_and_spacing():
    blocks = [{"page": 3, "text": AUDIT_PAGE}]
    said = ("In our opinion, the consolidated financial statements give a true and fair view of "
            "the consolidated financial position.")
    assert text_lines.locate(said, 3, blocks) == 3
    assert text_lines.locate(said, None, blocks) == 3


def test_a_text_the_cited_page_does_not_print_is_refused():
    blocks = [{"page": 3, "text": AUDIT_PAGE}, {"page": 4, "text": PAGES[3][1]}]
    assert text_lines.locate("KPMG", 3, blocks) is None
    assert text_lines.locate("Registered Public Interest Entity Auditor", 3, blocks) is None
    assert text_lines.locate("Registered Public Interest Entity Auditor", 4, blocks) == 4
    # A page nobody supplied is not where the model read it.
    assert text_lines.locate("Annual Report 2024", 1, blocks) is None


# ── the stage ─────────────────────────────────────────────────────────────────────────────────

class _Provider:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def complete_structured(self, *, system, messages, response_schema, max_tokens):
        self.calls.append({"system": system, "request": json.loads(messages[-1]["content"]),
                           "schema": response_schema})
        return response_schema.model_validate({"answers": self.answers}), {}


def _run_text(items, monkeypatch, answers, keys):
    monkeypatch.setattr("app.services.derived.document_text", lambda data, fmt: PAGES)
    doc = DocumentModel(fmt=DocFormat.PDF, pages=[PageSource(index=i) for i, _ in PAGES])
    ctx = PipelineContext(raw_bytes=b"%PDF")
    provider = _Provider(answers)
    by_key = {k: items[k] for k in keys}
    plans = [RequestPlan(name=k + ":text", keys=(k,), notes=()) for k in keys]
    out = stage.LineItemLlmStage()._ask_text(doc, ctx, provider, plans, by_key, {})
    return doc, ctx, provider, out


def test_the_stage_writes_a_located_text_onto_the_line_with_its_page(items, monkeypatch):
    key = "statement_setup_controls__accountant"
    doc, ctx, provider, (calls, failed, answered) = _run_text(
        items, monkeypatch, [{"key": key, "text": "Ernst & Young", "page": 3,
                              "confidence": 0.95}], [key])
    assert (calls, failed, answered) == (1, 0, 1)
    assert provider.calls[0]["schema"] is line_item_llm.LineItemTextReply
    assert provider.calls[0]["system"] == line_item_llm.TEXT_REPLY_CONTRACT
    assert provider.calls[0]["request"]["text_pages"]
    row = next(li for li in doc.line_items if li.canonical_key == key)
    (ev,) = row.values.values()
    assert ev.value_text == "Ernst & Young" and ev.value is None and ev.value_raw is None
    assert ev.provenance.page_index == 2


def test_the_stage_refuses_a_text_no_supplied_page_prints(items, monkeypatch):
    key = "statement_setup_controls__accountant"
    doc, ctx, _p, (_c, _f, answered) = _run_text(
        items, monkeypatch, [{"key": key, "text": "PricewaterhouseCoopers", "page": 3}], [key])
    assert answered == 0
    # Nothing is written, and no empty row is left to blank the export's own reading of the line.
    assert not [li for li in doc.line_items if li.canonical_key == key]
    assert any("text REFUSED (llm_text_not_on_page)" in line for line in ctx.logs)


def test_a_text_line_is_taken_out_of_a_figure_plan_which_keeps_its_other_lines(items):
    text_key, figure_key = "statement_setup_controls__accountant", "bs_ca__total_assets"
    by_key = {text_key: items[text_key], figure_key: items[figure_key]}
    plan = RequestPlan(name="group", keys=(figure_key, text_key), notes=("7",))
    figures, texts = stage._split_text_plans([plan], by_key)
    assert [p.keys for p in figures] == [(figure_key,)] and figures[0].notes == ("7",)
    assert [p.keys for p in texts] == [(text_key,)] and texts[0].notes == ()
    alone = RequestPlan(name=figure_key, keys=(figure_key,), notes=())
    assert stage._split_text_plans([alone], by_key) == ([alone], [])


# ── the export ────────────────────────────────────────────────────────────────────────────────

def _doc_with_texts(items, monkeypatch, texts: dict[str, tuple[str, int]]):
    answers = [{"key": k, "text": t, "page": p, "confidence": 0.9} for k, (t, p) in texts.items()]
    pages = dict(PAGES)
    pages.update({10: "The presentation currency of these financial statements is Renminbi."})
    monkeypatch.setattr("app.services.derived.document_text",
                        lambda data, fmt: sorted(pages.items()))
    doc = DocumentModel(fmt=DocFormat.PDF, pages=[PageSource(index=i) for i in sorted(pages)],
                        unit_context=UnitContext(currency="CNY", scale_factor=Decimal(1000)))
    ctx = PipelineContext(raw_bytes=b"%PDF")
    by_key = {k: items[k] for k in texts}
    plans = [RequestPlan(name=k, keys=(k,), notes=()) for k in texts]
    stage.LineItemLlmStage()._ask_text(doc, ctx, _Provider(answers), plans, by_key, {})
    return doc


def _supp(doc):
    template = json.loads(HK_TEMPLATE.read_text(encoding="utf-8"))
    rows = _build_supplemental_rows(template_def=template, base_rows=_serialize_rows(doc),
                                    disclosures=[], entity_name="Detected Name", doc_model=doc,
                                    ctx=None, options={})
    return {r["canonical_key"]: r for r in rows}


def test_the_export_writes_the_located_text_on_the_page_it_was_read(items, monkeypatch):
    doc = _doc_with_texts(items, monkeypatch, {
        "statement_setup_controls__accountant": ("Ernst & Young", 3),
        "notes__auditor_s_opinion": ("In our opinion, the consolidated financial statements give "
                                     "a true and fair view of the consolidated financial "
                                     "position.", 3)})
    assert _serialize_rows(doc) == []           # no figure row carries a text
    rows = _supp(doc)
    accountant = rows["statement_setup_controls__accountant"]["values"][0]
    assert accountant["value"] == "Ernst & Young"
    assert accountant["provenance"]["page_index"] == 2
    opinion = rows["notes__auditor_s_opinion"]["values"][0]["value"]
    assert opinion.startswith("In our opinion")


def test_a_controlled_setup_value_stays_and_the_printed_words_are_its_evidence(items, monkeypatch):
    doc = _doc_with_texts(items, monkeypatch, {
        "statement_setup_controls__source_currency": ("Renminbi", 11)})
    value = _supp(doc)["statement_setup_controls__source_currency"]["values"][0]
    assert value["value"] == "CNY"
    assert "printed_as:Renminbi" in value["confidence"]["flags"]
    assert value["provenance"]["page_index"] == 10


def test_without_a_located_text_the_export_says_what_it_said_before(items, monkeypatch):
    doc = _doc_with_texts(items, monkeypatch, {})
    rows = _supp(doc)
    assert rows["statement_setup_controls__customer_s_name"]["values"][0]["value"] == "Detected Name"
    assert not rows["statement_setup_controls__accountant"]["values"]
