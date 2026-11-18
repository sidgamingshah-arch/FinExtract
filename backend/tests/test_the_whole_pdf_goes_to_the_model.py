"""THE WHOLE PDF TO THE MODEL, IN PLACE OF THE EXTRACTION — an admin switch
(`extraction.whole_pdf_to_model`, `stages.whole_pdf`, `services.whole_pdf`).

Off, a run is what it was: the stage is not in the pipeline. On, the pages are classified, the PDF
FILE itself goes to the model with the template's lines, the model's figures are checked against
the page each cites in the PDF's own text, and the stages that rebuild rows from the page are
skipped. A provider that cannot take a file, or a first request that fails, leaves the filing to
the rules.

No test here calls a model. The provider below is a SCRIPTED STAND-IN: it answers from a fixed
table, so the tests show the wiring — what is sent, what is written, what is checked and what is
skipped — and say nothing about how well a model reads a filing.
"""
from __future__ import annotations

import json
import pathlib
from typing import ClassVar

import fitz
import pytest

from app.config import get_settings
from app.core.models import DocumentModel
from app.core.models.document import PageSource
from app.core.models.enums import PageKind
from app.core.stage import PipelineContext
from app.ports.registry import registry
from app.schemas.line_items import load_line_item_set
from app.services import whole_pdf as W

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"


def _set(name: str = "output_csv_usgaap_line_items.json"):
    return load_line_item_set(json.loads((TEMPLATES / name).read_text(encoding="utf-8")),
                              resolve=True)


def _pdf(pages: list[list[str]]) -> bytes:
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        y = 72
        for text in lines:
            page.insert_text((40, y), text, fontsize=9)
            y += 16
    return doc.tobytes()


_BALANCE_SHEET = ["CONSOLIDATED BALANCE SHEETS", "(in millions of U.S. dollars)",
                  "                                   2024        2023",
                  "Cash and cash equivalents          1,200       1,000",
                  "Accounts receivable                  300         250",
                  "Total current assets               1,500       1,250",
                  "Total assets                       4,500       4,250",
                  "Total liabilities                  2,000       1,900",
                  "Total stockholders' equity         2,500       2,350",
                  "Total liabilities and stockholders' equity   4,500   4,250"]
_FILING = [["Annual Report 2024 - Example Corp"], _BALANCE_SHEET,
           ["Notes to consolidated financial statements",
            "Note 5 Receivables: trade 280, other 20, total 300"]]


# ── the request: a file block on every adapter ─────────────────────────────────────────────────

_DOC = {"media_type": "application/pdf", "data": "JVBERi0xLjcK", "name": "document-1.pdf"}


def _messages(with_file: bool):
    m = {"role": "user", "content": "Template lines …"}
    if with_file:
        m["documents"] = [_DOC]
    return [m]


@pytest.mark.parametrize("adapter", ["anthropic", "bedrock_gateway"])
def test_an_anthropic_body_sends_the_pdf_as_a_document_block_before_the_question(adapter):
    from app.adapters.anthropic_llm import AnthropicLlmProvider
    from app.adapters.bedrock_gateway_llm import BedrockGatewayLlmProvider
    from app.services.whole_pdf import WholePdfReply

    provider = (AnthropicLlmProvider if adapter == "anthropic" else BedrockGatewayLlmProvider)()
    build = provider.build_request if adapter == "anthropic" else provider.build_body
    body = build(system="s", messages=_messages(True), response_schema=WholePdfReply,
                 max_tokens=100)
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"] == {"type": "base64", "media_type": "application/pdf",
                                    "data": "JVBERi0xLjcK"}
    # The file is what every request of the run sends again: it ends in a cache breakpoint.
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[-1] == {"type": "text", "text": "Template lines …"}
    # A message with no file is sent exactly as before.
    plain = build(system="s", messages=_messages(False), response_schema=WholePdfReply,
                  max_tokens=100)
    assert plain["messages"][0]["content"] == "Template lines …"


def test_a_chat_completions_body_sends_the_pdf_as_a_file_part_named_neutrally():
    from app.adapters.openai_llm import OpenAiLlmProvider
    from app.services.whole_pdf import WholePdfReply

    body = OpenAiLlmProvider().build_body(system="s", messages=_messages(True),
                                          response_schema=WholePdfReply, temperature=0.0,
                                          max_tokens=100)
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "file", "file": {
        "filename": "document-1.pdf", "file_data": "data:application/pdf;base64,JVBERi0xLjcK"}}
    assert parts[-1] == {"type": "text", "text": "Template lines …"}
    plain = OpenAiLlmProvider().build_body(system="s", messages=_messages(False),
                                           response_schema=WholePdfReply, temperature=0.0,
                                           max_tokens=100)
    assert plain["messages"][1]["content"] == "Template lines …"


def test_a_request_carrying_a_pdf_waits_the_document_timeout():
    from app.adapters._structured import request_timeout

    s = get_settings()
    assert request_timeout(s, _messages(True)) == s.llm.document_timeout_seconds
    assert request_timeout(s, _messages(False)) == s.llm.timeout_seconds


def test_which_providers_take_a_file():
    from app.adapters.stubs import StubLlmProvider
    from app.ports.llm import takes_documents

    for pid in ("anthropic", "bedrock_gateway", "openai", "openai_compatible", "azure_openai"):
        assert takes_documents(registry.get("llm", pid)), pid
    assert not takes_documents(StubLlmProvider())


# ── which pages, in which parts ────────────────────────────────────────────────────────────────

def _doc(kinds: list[PageKind]) -> DocumentModel:
    doc = DocumentModel(filename="x.pdf")
    doc.pages = [PageSource(index=i, kind=k,
                            statement="balance_sheet" if k == PageKind.FACE else None)
                 for i, k in enumerate(kinds)]
    return doc


def test_the_pages_sent_are_the_pages_the_page_scope_screen_reads():
    doc = _doc([PageKind.COVER, PageKind.FACE, PageKind.OTHER, PageKind.NOTES, PageKind.NOTES])
    assert W.pages_to_send(doc, choice="statements") == ([1, 3, 4], "statements")
    assert W.pages_to_send(doc, choice="all")[0] == [0, 1, 2, 3, 4]
    # A person's selection narrows them, as it narrows what the rules read.
    assert W.pages_to_send(doc, choice="statements", included_pages={1, 3, 9}) == (
        [1, 3], "page_scope")
    # …and so do the sections they switched off.
    pages, why = W.pages_to_send(doc, choice="statements", sections_off={"notes"})
    assert pages == [1] and "sections off: notes" in why
    none = _doc([PageKind.COVER, PageKind.OTHER])
    pages, why = W.pages_to_send(none, choice="statements")
    assert pages == [0, 1] and why.startswith("all(")


def test_a_long_selection_is_sent_in_parts_each_holding_its_own_pages():
    raw = _pdf([[f"page {i + 1}"] for i in range(5)])
    parts, dropped = W.build_parts(raw, [0, 1, 3, 4], max_pages=3,
                                   max_request_bytes=20 * 1024 * 1024)
    assert [p.pages for p in parts] == [[0, 1], [3, 4]] and dropped == []   # even, not 3 + 1
    first = fitz.open(stream=parts[0].pdf, filetype="pdf")
    assert [first.load_page(i).get_text().strip() for i in range(first.page_count)] == [
        "page 1", "page 2"]
    assert parts[1].original_page(1) == 3 and parts[1].original_page(3) is None
    assert [len(c) for c in W.balanced(list(range(101)), 100)] == [51, 50]
    assert [len(c) for c in W.balanced(list(range(100)), 100)] == [100]


def test_a_part_over_the_request_size_is_halved_and_a_page_too_large_is_named():
    raw = _pdf([[f"page {i + 1}"] for i in range(4)])
    whole, _ = W.build_parts(raw, [0, 1, 2, 3], max_pages=10, max_request_bytes=10 ** 9)
    one_page = W.build_parts(raw, [0], max_pages=1, max_request_bytes=10 ** 9)[0][0].b64_bytes
    room = W.REQUEST_OVERHEAD_BYTES + (whole[0].b64_bytes + one_page) // 2
    halved, dropped = W.build_parts(raw, [0, 1, 2, 3], max_pages=10, max_request_bytes=room)
    assert len(halved) > 1 and sorted(p for part in halved for p in part.pages) == [0, 1, 2, 3]
    none, dropped = W.build_parts(raw, [0, 1], max_pages=10,
                                  max_request_bytes=W.REQUEST_OVERHEAD_BYTES + 10)
    assert none == [] and dropped == [0, 1]


# ── which lines, in which requests ─────────────────────────────────────────────────────────────

def test_the_lines_asked_are_the_printed_figures_and_totals_of_the_template():
    hk = _set("output_csv_hk_line_items.json")
    asked = W.lines_to_ask(hk)
    keys = {it.key for it in asked}
    assert not any(k.startswith("sub__") for k in keys)
    assert {it.type for it in asked} <= {"extracted", "calculated"}
    assert all(getattr(it, "output_structure", "value") == "value" for it in asked)
    assert "bs_ca__cash_in_hand_and_at_banks" in keys
    by_type = {it.type for it in hk.items if it.key in keys}
    assert "calculated" in by_type             # the printed totals, so the checks have both sides
    chunks = W.chunk_lines(asked, 60)
    assert all(len(c) <= 60 for c in chunks)
    assert all(len({W.statement_of(it) for it in c}) == 1 for c in chunks)


def test_the_question_names_each_line_and_marks_a_total():
    lines = [it for it in W.lines_to_ask(_set()) if it.key in (
        "bs_ca__cash_and_cash_equivalents", "bs_ca__total_current_assets")]
    text = W.question(lines, W.Part(pages=[1, 2, 3], pdf=b""), 2, 3)
    assert "holds 3 pages" in text and "part 2 of 3" in text
    assert "- bs_ca__cash_and_cash_equivalents: Cash and cash equivalents —" in text
    assert "- bs_ca__total_current_assets: " in text and " TOTAL" in text


# ── reading a figure, and finding it on its page ───────────────────────────────────────────────

@pytest.mark.parametrize("text,value", [
    ("1200", "1200"), ("-1,234.5", "-1234.5"), ("(1,234.5)", "-1234.5"), ("—", "0"),
    ("HK$ 12,000", "12000"), ("abc", None), ("1.2.3", None)])
def test_an_amount_is_read_as_a_signed_number(text, value):
    got = W.parse_amount(text)
    assert (str(got) if got is not None else None) == value


def _part_and_texts():
    raw = _pdf(_FILING)
    pages = [0, 1, 2]
    return W.Part(pages=pages, pdf=raw), W.page_numbers(raw, pages)


def test_a_figure_on_its_cited_page_is_confirmed():
    part, texts = _part_and_texts()
    from decimal import Decimal as D

    assert W.check_figure(D(1200), [], 1, part, texts).status == "on_page"
    assert W.check_figure(D(-1200), [], 1, part, texts).status == "on_page"


def test_a_figure_printed_on_exactly_one_other_page_is_moved_there():
    part, texts = _part_and_texts()
    from decimal import Decimal as D

    check = W.check_figure(D(4500), [], 0, part, texts)       # cited the cover
    assert (check.status, check.page) == ("moved", 1)


def test_a_figure_on_no_page_or_on_several_is_not_confirmed():
    part, texts = _part_and_texts()
    from decimal import Decimal as D

    assert W.check_figure(D(9999), [], 1, part, texts).status == "not_found"
    # 300 is printed on the balance sheet and in the note: cited on the cover, it cannot be placed.
    assert W.check_figure(D(300), [], 0, part, texts).status == "not_found"


def test_a_sum_of_rows_is_confirmed_when_each_row_is_on_the_page_and_they_add_up():
    part, texts = _part_and_texts()
    from decimal import Decimal as D

    assert W.check_figure(D(300), [D(280), D(20)], 2, part, texts).status == "parts_on_page"
    assert W.check_figure(D(301), [D(280), D(20)], 2, part, texts).status == "not_found"
    assert W.check_figure(D(0), [], 1, part, texts).status == "nil"


def test_a_page_with_no_text_cannot_confirm_a_figure():
    from decimal import Decimal as D

    part = W.Part(pages=[0], pdf=b"")
    assert W.check_figure(D(10), [], 0, part, {0: set()}).status == "no_text"


def test_two_readings_of_one_figure_keep_the_confirmed_one_and_name_the_other():
    from decimal import Decimal as D

    def r(amount, status, face):
        return W.Reading(key="k", basis="consolidated", period="current", amount=D(amount),
                         caption="", period_end="", part=0, check=W.Check(status, 3),
                         on_face=face)
    kept = W.merge([r("10", "not_found", True), r("12", "on_page", False)])
    only = kept[("k", "consolidated", "current")]
    assert only.amount == D(12) and only.others == ["10@p4"]
    face = W.merge([r("12", "on_page", False), r("12", "on_page", True)])
    assert face[("k", "consolidated", "current")].on_face


# ── the stage, end to end, with a scripted stand-in ────────────────────────────────────────────

class _Scripted:
    """A STAND-IN, not a model: answers the lines it has a figure for, from a fixed table."""
    id = "scripted-pdf"
    takes_documents = True

    TABLE: ClassVar[dict] = {"bs_ca__cash_and_cash_equivalents": [("current", "1200", 1), ("prior", "1000", 1)],
             "bs_ca__accounts_receivable": [("current", "300", 1), ("prior", "250", 1)],
             "bs_ca__total_current_assets": [("current", "1500", 1), ("prior", "1250", 1)],
             "bs_ca__total_assets": [("current", "4500", 1), ("prior", "4250", 1)],
             "bs_cl__total_liabilities": [("current", "2000", 1), ("prior", "1900", 1)],
             "bs_equity__total_equity": [("current", "2500", 1), ("prior", "2350", 1)],
             "bs_cl__total_equity_and_liabilities": [("current", "4500", 1),
                                                      ("prior", "4250", 1)]}

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[dict] = []

    def complete_structured(self, *, system, messages, response_schema, temperature=0.0,
                            max_tokens=2048):
        self.sent.append(messages[0])
        if self.fail:
            raise RuntimeError("HTTP 400: this endpoint does not take a file")
        lines = [{"key": key, "figures": [{"basis": "consolidated", "period": p, "amount": a,
                                           "page": pg, "caption": key} for p, a, pg in figs]}
                 for key, figs in self.TABLE.items() if f"- {key}:" in messages[0]["content"]]
        reply = response_schema.model_validate({"units": "in millions of U.S. dollars",
                                                "lines": lines})
        return reply, {"input_tokens": 1000, "output_tokens": 100, "model": "scripted"}


@pytest.fixture
def run(monkeypatch):
    """`run_extraction` with the switch on and the stand-in registered as the provider."""
    from app.schemas.loader import load_template
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    def _run(provider=None, **extraction):
        s = get_settings()
        for name, value in {"whole_pdf_to_model": True, "whole_pdf_pages": "statements",
                            "llm_disclosures": False, **extraction}.items():
            monkeypatch.setattr(s.extraction, name, value)
        if provider is not None:
            monkeypatch.setitem(registry._factories.setdefault("llm", {}), provider.id,
                                lambda: provider)
            monkeypatch.setattr(s.llm, "provider", provider.id)
        st = _set()
        tpl = load_template(json.loads((TEMPLATES / "output_csv_usgaap_v1_template.json")
                                       .read_text(encoding="utf-8")))
        return run_extraction(_pdf(_FILING), filename="example.pdf",
                              ontology=build_working_view(st), template=tpl, line_items=st)
    return _run


def test_the_model_reads_the_file_and_the_extraction_is_skipped(run):
    provider = _Scripted()
    doc, ctx = run(provider)
    # The file went with every request, and only the statement and notes pages were in it.
    assert provider.sent and all(len(m["documents"]) == 1 for m in provider.sent)
    assert ctx.whole_pdf["pages_sent"] == [2, 3]
    sent = fitz.open(stream=__import__("base64").b64decode(provider.sent[0]["documents"][0]["data"]),
                     filetype="pdf")
    assert sent.page_count == 2
    # The rows are the model's, one per line and basis, on the template's keys.
    by_key = {li.canonical_key: li for li in doc.line_items}
    assert set(by_key) == set(_Scripted.TABLE)
    cash = by_key["bs_ca__cash_and_cash_equivalents"]
    assert {v.period_label: str(v.value_raw) for v in cash.values.values()} == {
        "current": "1200", "prior": "1000"}
    assert {v.provenance.page_index for v in cash.values.values()} == {1}      # page 2
    assert all("whole_pdf:on_page" in v.confidence.flags for v in cash.values.values())
    # The stages that rebuild rows from the page did not run; units and the checks did.
    assert "stage:extract:skipped" in ctx.logs and "stage:map_line_items:skipped" in ctx.logs
    assert "stage:normalize:start" in ctx.logs and "stage:structural:start" in ctx.logs
    assert doc.unit_context is not None and str(doc.unit_context.scale_factor) == "1000000"
    passed = {(r.rule_id, r.details.get("period_label")) for r in doc.structural.results
              if r.status == "pass"}
    assert ("identity:bs_balances", "current") in passed
    assert ctx.mapping_strategy == "whole_pdf" and ctx.llm_calls == len(provider.sent)


def test_a_figure_the_page_does_not_print_goes_to_review(run):
    provider = _Scripted()
    provider.TABLE = {**_Scripted.TABLE,
                      "bs_ca__inventories": [("current", "777", 1)]}    # not on the page
    doc, ctx = run(provider)
    inv = next(li for li in doc.line_items if li.canonical_key == "bs_ca__inventories")
    assert "low_mapping_confidence" in inv.confidence.flags
    assert inv.confidence.mapping < get_settings().extraction.auto_accept_confidence
    assert ctx.whole_pdf["checks"].get("not_found") == 1


@pytest.mark.parametrize("case", ["stub", "no_files", "first_request_fails"])
def test_a_run_the_model_cannot_read_is_read_by_the_rules(run, case):
    if case == "stub":
        _doc, ctx = run(None)
    elif case == "no_files":
        provider = _Scripted()
        provider.takes_documents = False
        _doc, ctx = run(provider)
    else:
        _doc, ctx = run(_Scripted(fail=True))
    assert any(line.startswith("whole_pdf:fallback(") for line in ctx.logs)
    assert ctx.only_stages is None and ctx.mapping_strategy != "whole_pdf"
    assert "stage:extract:start" in ctx.logs
    assert not any(line.startswith("stage:") and line.endswith(":skipped") for line in ctx.logs)


def test_switched_off_the_stage_is_not_in_the_pipeline(monkeypatch):
    from app.core.pipeline import default_pipeline, pipeline_for, whole_pdf_pipeline
    from app.stages.whole_pdf import KEPT_STAGES

    s = get_settings()
    monkeypatch.setattr(s.extraction, "whole_pdf_to_model", False)
    names = [st.name for st in pipeline_for(s).stages]
    assert names == [st.name for st in default_pipeline().stages] and "whole_pdf" not in names
    monkeypatch.setattr(s.extraction, "whole_pdf_to_model", True)
    on = [st.name for st in pipeline_for(s).stages]
    assert on == [st.name for st in whole_pdf_pipeline().stages]
    assert on.index("whole_pdf") == on.index("classify") + 1
    assert KEPT_STAGES <= set(on)             # every stage it keeps is a stage that exists


def test_the_shipped_config_turns_it_on_and_the_code_default_is_off():
    """On by default VIA CONFIG: config.toml is what a fresh clone and "restore defaults" get; a
    Settings built without that file stays off."""
    import tomllib

    from app.config import BACKEND_DIR, ExtractionSettings

    shipped = tomllib.loads((BACKEND_DIR / "config.toml").read_text(encoding="utf-8"))
    assert shipped["extraction"]["whole_pdf_to_model"] is True
    assert ExtractionSettings().whole_pdf_to_model is False


def test_a_stage_can_name_the_stages_still_to_run():
    from app.core.pipeline import Pipeline

    ran = []

    class S:
        def __init__(self, name):
            self.name = name

        def run(self, doc, ctx):
            ran.append(self.name)
            if self.name == "a":
                ctx.only_stages = frozenset({"a", "c"})
            return doc

    ctx = PipelineContext()
    Pipeline(stages=[S("a"), S("b"), S("c")]).run(DocumentModel(), ctx)
    assert ran == ["a", "c"] and "stage:b:skipped" in ctx.logs


# ── the admin switch ───────────────────────────────────────────────────────────────────────────

def test_the_switch_and_its_limits_are_admin_settings(monkeypatch):
    from app.services import settings_state as S

    fields = {f["key"]: f for f in S.extraction_config()["fields"]}
    assert fields["whole_pdf_to_model"]["kind"] == "bool"
    assert fields["whole_pdf_pages"]["choices"] == ["statements", "all"]
    assert (fields["whole_pdf_pages_per_request"]["min"],
            fields["whole_pdf_pages_per_request"]["max"]) == (1, 600)
    ex = get_settings().extraction
    for key in ("whole_pdf_to_model", "whole_pdf_pages", "whole_pdf_lines_per_request"):
        monkeypatch.setattr(ex, key, getattr(ex, key))
    # An admin can switch it off, and back on, from the Settings screen.
    S.set_extraction_config(persist=False, whole_pdf_to_model=False, whole_pdf_pages="all",
                            whole_pdf_lines_per_request=40)
    assert ex.whole_pdf_to_model is False and ex.whole_pdf_pages == "all"
    S.set_extraction_config(persist=False, whole_pdf_to_model=True)
    assert ex.whole_pdf_to_model is True
    with pytest.raises(ValueError):
        S.set_extraction_config(persist=False, whole_pdf_pages="some")
    with pytest.raises(ValueError):
        S.set_extraction_config(persist=False, whole_pdf_pages_per_request=601)


# ── what the first real run (11033486, 323 pages, CAS) showed ──────────────────────────────────

def test_a_model_row_is_on_its_lines_statement_not_its_pages():
    """Page 136 of 11033486 ends the parent company's balance sheet and starts the income
    statement; the classifier names the page `profit_and_loss`. Fifteen balance-sheet lines read
    there were resolved by the PAGE, failed normalize's balance/flow check and lost their line."""
    from decimal import Decimal as D

    from app.services.buckets import statement_resolver
    from app.stages.whole_pdf import _rows

    hk = _set("output_csv_hk_line_items.json")
    doc = DocumentModel(filename="x.pdf")
    doc.pages = [PageSource(index=0, kind=PageKind.FACE, statement="profit_and_loss")]
    reading = W.Reading(key="bs_cl__total_liabilities", basis="standalone", period="current",
                        amount=D("7824458227.57"), caption="负债合计", period_end="", part=0,
                        check=W.Check("on_page", 0), on_face=True)
    rows = _rows(doc, hk, {("bs_cl__total_liabilities", "standalone", "current"): reading},
                 "scripted")
    assert statement_resolver(doc)(rows[0]) == "balance_sheet"


def test_the_question_carries_the_rules_the_rules_route_applies_by_layout():
    # CAS prints outflows unsigned under 现金流出小计; the rules negate the block, the model is told.
    assert "现金流出小计" in W.SYSTEM and "negative even where" in W.SYSTEM
    # The residual sweep: a row no line describes goes into its section's other line, once.
    assert "exactly one line" in W.SYSTEM and '"other" line' in W.SYSTEM


def test_a_statement_is_asked_in_one_request_by_default():
    """At 60 lines a request the HK balance sheet went in four requests, none seeing the lines the
    others would claim, so none could fill an "other" line."""
    from app.config import ExtractionSettings

    per = ExtractionSettings().whole_pdf_lines_per_request
    hk = W.lines_to_ask(_set("output_csv_hk_line_items.json"))
    chunks = W.chunk_lines(hk, per)
    assert len(chunks) == len({W.statement_of(it) for it in hk})
    assert max(len(c) for c in chunks) > 60
    assert W.max_tokens_for(len(chunks[0])) <= 32_000
