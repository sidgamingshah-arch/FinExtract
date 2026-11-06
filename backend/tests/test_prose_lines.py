"""NARRATIVE LINES — the one kind of line the model WRITES rather than locates.

A `prose` line (`output_structure: prose`) is a short narrative of what a filing discloses: on the
10-K set, its contingent liabilities and its related-party transactions, which the user asked for
and which no span of one page holds. The product's rule is that the model locates what a page
prints, so this is a deliberate exception, and these tests hold it to what makes it one:

  * the line LOADS, its instruction its `prompt` folded into `definition`, and a prose line with
    nothing to write from is still refused (`LineItemDef._coherent`);
  * it is ASKED like a text line — the pages, or the whole report for an `anywhere` line — under a
    contract and a reply schema of its own (`line_item_llm.PROSE_REPLY_CONTRACT`,
    `LineItemProseReply`), batched apart from the phrases;
  * its answer is CHECKED before it is written (`text_lines.check_prose`): it cites pages that
    were sent, and every number it writes is printed on one of them, or nothing is written;
  * what is written is LABELLED — a `value_text` fact flagged `llm_prose:pages …` — and the export
    passes the label through (`routes.extractions._text_facts`).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.api.routes.extractions import (
    _build_supplemental_rows,
    _serialize_rows,
    _text_facts,
)
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import DocFormat
from app.core.stage import PipelineContext
from app.schemas.line_items import LineItemDef, LineItemSet, load_line_item_set
from app.services import line_item_llm, line_item_requests, text_lines
from app.services.line_item_requests import RequestPlan
from app.stages import line_item_llm as stage

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"
US_SET = TEMPLATES / "output_csv_usgaap_line_items.json"
US_TEMPLATE = TEMPLATES / "output_csv_usgaap_v1_template.json"

CC, RP = "notes_contingencies__narrative", "notes_related__narrative"
CC_PHRASE = "notes_contingencies__assessment"

# A fictional filing's notes, one disclosure a page. `page` in a request is the 1-based position.
PAGES = [
    (0, ("Bluegate Retail Inc.\nAnnual Report on Form 10-K for the fiscal year ended "
         "December 31, 2024")),
    (1, ("NOTE 14 — COMMITMENTS AND CONTINGENCIES\nLegal proceedings. The Company is a defendant "
         "in product liability claims relating to its patio heaters. At December 31, 2024 the "
         "Company had accrued $410 million for these claims. The Company estimates the "
         "reasonably possible loss in excess of the amount accrued at up to $95 million.")),
    (2, ("Guarantees. At December 31, 2024, the Company had $2,950 million of standby letters "
         "of credit and surety bonds outstanding, principally supporting its insurance "
         "programs.")),
    (3, ("NOTE 15 — RELATED PARTY TRANSACTIONS\nThe Company leases its headquarters from an "
         "entity controlled by its chairman, for rent of $18 million in 2024.")),
]
SUPPLIED = [{"page": i + 1, "text": t} for i, t in PAGES]

GOOD = ("The Company is a defendant in product liability claims, for which it had accrued $410 "
        "million at December 31, 2024, with a reasonably possible loss in excess of the accrual "
        "of up to $95 million. It also had $2,950 million of standby letters of credit and surety "
        "bonds outstanding.")


@pytest.fixture(scope="module")
def items():
    cfg = load_line_item_set(json.loads(US_SET.read_text(encoding="utf-8")), resolve=True)
    return {i.key: i for i in cfg.items}


# ── it loads ──────────────────────────────────────────────────────────────────────────────────

def test_a_prose_line_loads_and_its_prompt_is_its_instruction():
    line = LineItemDef(key="x", output_structure="prose", definition="A narrative of X.",
                       prompt="In at most two sentences, describe X.")
    assert line.output_structure == "prose" and line.prompt == ""
    assert line.definition == "A narrative of X.\n\nIn at most two sentences, describe X."
    alone = LineItemDef(key="y", output_structure="prose", prompt="Describe Y.")
    assert alone.definition == "Describe Y."


def test_a_prose_line_with_nothing_to_write_from_is_refused():
    with pytest.raises(ValueError, match="nothing to write from"):
        LineItemDef(key="x", output_structure="prose")
    with pytest.raises(ValueError, match="nothing to write from"):
        LineItemSet.model_validate({"line_items_key": "k", "target_template_key": "t",
                                    "items": [{"key": "x", "output_structure": "prose",
                                               "prompt": "   "}]})


def test_a_prose_line_of_arithmetic_is_still_refused():
    with pytest.raises(ValueError, match="no calculation that produces a sentence"):
        LineItemDef(key="x", type="calculated", output_structure="prose", prompt="Describe X.",
                    terms=[{"ref": "y", "sign": 1}])


def test_the_shipped_narratives_are_text_lines_the_model_writes(items):
    for key in (CC, RP):
        assert text_lines.is_text_line(items[key]) and text_lines.is_prose_line(items[key]), key
        assert "None disclosed." in items[key].definition, key
    assert text_lines.is_text_line(items[CC_PHRASE])
    assert not text_lines.is_prose_line(items[CC_PHRASE])
    # Its pages, when it is not sent the whole report, are chosen by what its note is about.
    assert "commitments and contingencies" in text_lines.prose_names(items[CC])


# ── it is checked ─────────────────────────────────────────────────────────────────────────────

def test_a_narrative_whose_numbers_are_printed_on_its_cited_pages_passes():
    assert text_lines.check_prose(GOOD, [2, 3], SUPPLIED) is None
    assert text_lines.prose_numbers(GOOD) == ["410", "31", "2024", "95", "2,950"]


def test_a_number_the_cited_pages_do_not_print_is_refused():
    tampered = GOOD.replace("$410 million", "$420 million")
    why = text_lines.check_prose(tampered, [2, 3], SUPPLIED)
    assert why.startswith("llm_prose_number_not_on_page") and "420" in why
    # A sum the model worked out is not a printed figure either.
    assert text_lines.check_prose("In total $3,360 million.", [2, 3], SUPPLIED) is not None


def test_a_number_printed_only_on_a_page_it_does_not_cite_is_refused():
    why = text_lines.check_prose(GOOD, [2], SUPPLIED)
    assert why.startswith("llm_prose_number_not_on_page") and "2,950" in why


def test_a_narrative_must_cite_a_page_that_was_sent():
    assert text_lines.check_prose(GOOD, [], SUPPLIED).startswith("llm_prose_no_page")
    assert text_lines.check_prose(GOOD, [2, 9], SUPPLIED).startswith(
        "llm_prose_page_not_supplied")


def test_separators_currency_and_spacing_do_not_count_and_a_number_is_never_part_of_another():
    page = [{"page": 1, "text": "letters of credit of $ 2 ,950 million and 1 234 bonds; "
                                "tax rate 21% ; 4,100 claims"}]
    assert text_lines.check_prose("$2,950 million of letters of credit", [1], page) is None
    assert text_lines.check_prose("1,234 bonds", [1], page) is None
    assert text_lines.check_prose("a rate of 21.0%", [1], page) is None
    assert text_lines.check_prose("410 claims", [1], page) is not None
    assert text_lines.check_prose("100 claims", [1], page) is not None


def test_a_narrative_longer_than_a_few_sentences_is_refused():
    long = "The Company discloses claims. " * 80
    assert text_lines.check_prose(long, [2], SUPPLIED).startswith("llm_prose_too_long")


# ── it is asked, checked and written ──────────────────────────────────────────────────────────

class _Provider:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def complete_structured(self, *, system, messages, response_schema, max_tokens):
        self.calls.append({"system": system, "messages": messages,
                           "request": json.loads(messages[-1]["content"]),
                           "schema": response_schema})
        mine = [a for a in self.answers
                if a["key"] in {e["key"] for e in self.calls[-1]["request"]["line_items"]}]
        return response_schema.model_validate({"answers": mine}), {}


def _doc():
    return DocumentModel(fmt=DocFormat.PDF, pages=[PageSource(index=i) for i, _ in PAGES])


def _ask(items, monkeypatch, answers, plans, *, report=False):
    monkeypatch.setattr("app.services.derived.document_text", lambda data, fmt: PAGES)
    doc, ctx = _doc(), PipelineContext(raw_bytes=b"%PDF")
    provider = _Provider(answers)
    by_key = {k: items[k] for p in plans for k in p.keys}
    if report:
        out = stage.LineItemLlmStage()._ask_text(
            doc, ctx, provider, [], by_key, {}, report_plans=plans,
            report={"pages": SUPPLIED}, report_text="THE ANNUAL REPORT")
    else:
        out = stage.LineItemLlmStage()._ask_text(doc, ctx, provider, plans, by_key, {})
    return doc, ctx, provider, out


def _row(doc, key):
    return next((li for li in doc.line_items if li.canonical_key == key), None)


def test_a_narrative_is_asked_under_its_own_contract_and_written_with_its_label(items, monkeypatch):
    plans = [RequestPlan(name=CC + ":text", keys=(CC,), notes=())]
    doc, ctx, provider, (calls, failed, answered) = _ask(
        items, monkeypatch, [{"key": CC, "text": GOOD, "pages": [2, 3], "confidence": 0.8,
                              "reason": "note 14"}], plans)
    assert (calls, failed, answered) == (1, 0, 1)
    call = provider.calls[0]
    assert call["schema"] is line_item_llm.LineItemProseReply
    assert call["system"] == line_item_llm.PROSE_REPLY_CONTRACT
    (line,) = call["request"]["line_items"]
    assert line["key"] == CC and line["output"] == "narrative"
    assert "In at most four sentences" in line["definition"]
    assert any("COMMITMENTS AND CONTINGENCIES" in b["text"] for b in call["request"]["text_pages"])
    row = _row(doc, CC)
    (ev,) = row.values.values()
    assert ev.value_text == GOOD and ev.value is None
    assert ev.provenance.page_index == 1 and ev.provenance.producer == "line_item_llm:prose"
    assert "llm_prose:pages 2, 3" in row.confidence.flags
    assert not [f for f in row.confidence.flags if f.startswith("llm_text")]
    assert any("narrative WRITTEN BY THE MODEL (llm_prose, pages 2, 3)" in m for m in ctx.logs)
    assert any("narratives_written=1" in m for m in ctx.logs)


def test_a_narrative_with_a_number_the_filing_does_not_print_is_refused_and_nothing_written(
        items, monkeypatch):
    plans = [RequestPlan(name=CC, keys=(CC,), notes=())]
    doc, ctx, _p, (_c, _f, answered) = _ask(
        items, monkeypatch,
        [{"key": CC, "text": GOOD.replace("$95 million", "$96 million"), "pages": [2, 3]}], plans)
    assert answered == 0 and _row(doc, CC) is None
    assert any("narrative REFUSED (llm_prose_number_not_on_page — 96" in m for m in ctx.logs)


def test_an_empty_narrative_writes_nothing(items, monkeypatch):
    plans = [RequestPlan(name=CC, keys=(CC,), notes=())]
    doc, ctx, _p, (_c, _f, answered) = _ask(items, monkeypatch,
                                             [{"key": CC, "text": "", "pages": []}], plans)
    assert answered == 0 and _row(doc, CC) is None
    assert any("nothing written" in m for m in ctx.logs)


def test_a_plan_holding_a_phrase_and_a_narrative_is_asked_as_two(items, monkeypatch):
    plans = [RequestPlan(name="group:text", keys=(CC_PHRASE, CC), notes=())]
    _d, _ctx, provider, (calls, _f, _a) = _ask(items, monkeypatch, [], plans)
    assert calls == 2
    by_schema = {c["schema"]: c for c in provider.calls}
    phrase = by_schema[line_item_llm.LineItemTextReply]
    prose = by_schema[line_item_llm.LineItemProseReply]
    assert [e["key"] for e in phrase["request"]["line_items"]] == [CC_PHRASE]
    assert phrase["system"] == line_item_llm.TEXT_REPLY_CONTRACT
    assert [e["key"] for e in prose["request"]["line_items"]] == [CC]


def test_with_the_whole_report_a_narrative_is_written_from_any_page(items, monkeypatch):
    plans = [RequestPlan(name="report:notes:prose", keys=(CC, RP), notes=())]
    rp = ("The Company leases its headquarters from an entity controlled by its chairman, for "
          "rent of $18 million in 2024.")
    doc, _ctx, provider, (calls, _f, answered) = _ask(
        items, monkeypatch, [{"key": CC, "text": GOOD, "pages": [2, 3]},
                             {"key": RP, "text": rp, "pages": 4}], plans, report=True)
    assert (calls, answered) == (1, 2)
    call = provider.calls[0]
    assert call["system"] == (line_item_llm.PROSE_REPLY_CONTRACT + "\n\n"
                              + line_item_llm.PROSE_REPORT_GUIDE)
    assert call["messages"][0]["content"] == "THE ANNUAL REPORT"
    assert "text_pages" not in call["request"]
    assert "llm_prose:pages 4" in _row(doc, RP).confidence.flags


def test_the_report_batches_a_narrative_apart_from_the_phrases(items):
    keys = [CC_PHRASE, CC, "notes_related__disclosure", RP]
    by_key = {k: items[k] for k in keys}
    plans = [RequestPlan(name=k, keys=(k,), notes=()) for k in keys]
    kept, report = line_item_requests.report_plans(plans, by_key)
    assert kept == []
    assert [(p.name, p.keys) for p in report] == [
        ("report:notes", (CC_PHRASE, "notes_related__disclosure")),
        ("report:notes:prose", (CC, RP))]


def test_the_contract_asks_for_what_the_check_holds_it_to():
    contract = line_item_llm.PROSE_REPLY_CONTRACT
    assert "ONLY what the pages under `text_pages` print" in contract
    assert "Write NO number the pages do not print" in contract
    assert "`pages`" in contract and "empty `text`" in contract
    assert "Copy every amount, percentage, date and name exactly" in contract
    reply = line_item_llm.LineItemProseReply.model_validate_json(json.dumps(
        {"answers": {"items": [{"key": "k", "text": "t", "pages": [3, 4]}]}}))
    assert reply.answers[0].pages == [3, 4]
    assert line_item_llm.LineItemProseAnswer(key="k", pages=7).pages == [7]
    # No phrase request changes: its contract, its guide and its reply are as they were.
    assert set(line_item_llm.LineItemTextAnswer.model_fields) == {
        "key", "text", "page", "confidence", "reason"}


# ── it is labelled where it is served ─────────────────────────────────────────────────────────

def test_the_export_serves_the_narrative_with_its_label(items, monkeypatch):
    plans = [RequestPlan(name=CC, keys=(CC,), notes=())]
    doc, _ctx, _p, _out = _ask(items, monkeypatch,
                               [{"key": CC, "text": GOOD, "pages": [2, 3], "confidence": 0.8}],
                               plans)
    fact = _text_facts(doc)[CC]
    assert fact["text"] == GOOD and fact["flags"] == ["llm_prose:pages 2, 3"]
    assert fact["provenance"]["page_index"] == 1
    template = json.loads(US_TEMPLATE.read_text(encoding="utf-8"))
    rows = _build_supplemental_rows(template_def=template, base_rows=_serialize_rows(doc),
                                    disclosures=[], entity_name="Bluegate", doc_model=doc,
                                    ctx=None, options={})
    (row,) = [r for r in rows if r["canonical_key"] == CC]
    (value,) = row["values"]
    assert value["value"] == GOOD
    assert "llm_prose:pages 2, 3" in value["confidence"]["flags"]
    assert "llm_prose:pages 2, 3" in row["flags"]
    assert value["provenance"]["page_index"] == 1
