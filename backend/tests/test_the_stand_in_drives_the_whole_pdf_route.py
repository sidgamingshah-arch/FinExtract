"""THE STAND-IN DRIVES THE WHOLE-PDF ROUTE (`scripts/llm_standin.py`) — finding
standin_capture:shipped_standin_cannot_capture_whole_pdf.

The shipped stand-in declared no `takes_documents`, so with `extraction.whole_pdf_to_model` on (as
config.toml ships it) the whole-PDF stage fell back at once — `whole_pdf:fallback(provider standin
cannot be sent a file)` — and a capture meant to show what "send the whole PDF" sends recorded the
rules route instead. Now the stand-in takes files when asked (off by default, so a capture of the
rules route is what it was), records each file as a placeholder naming the part, and can answer a
whole-PDF request from a SCRIPT keyed by line, with the figures on the report's own pages — so a
whole-PDF run goes end to end with no model. A script is the script's answer, never a model's.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import fitz
import pytest

from app.ports.llm import takes_documents
from app.services import whole_pdf as W
from app.services.whole_pdf import WholePdfReply
from tests.whole_pdf_standin import TEMPLATES, run_whole_pdf, script_figure, standin_module

standin = standin_module()


def _hk():
    from app.schemas.line_items import load_line_item_set

    return load_line_item_set(json.loads((TEMPLATES / "output_csv_hk_line_items.json")
                                         .read_text(encoding="utf-8")), resolve=True)


def _pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((40, 72), text, fontsize=9)
    return doc.tobytes()


def _question(keys: list[str], part_pages: list[int], number: int = 1, count: int = 1) -> str:
    by_key = {i.key: i for i in _hk().items}
    return W.question([by_key[k] for k in keys], W.Part(pages=part_pages, pdf=b""), number, count)


CASH = "bs_ca__cash_in_hand_and_at_banks"
TRADING = "sub__cp_face_trading_fincl_assets"


# ── takes files only when asked ────────────────────────────────────────────────────────────────

def test_the_stand_in_takes_a_file_only_when_asked(tmp_path: Path):
    assert not takes_documents(standin.Recorder(tmp_path / "a").provider())
    assert takes_documents(standin.Recorder(tmp_path / "b", takes_documents=True).provider())
    # A script is for the whole-PDF requests, so giving one is asking for the file.
    assert takes_documents(standin.Recorder(tmp_path / "c", whole_pdf_script={}).provider())


# ── a scripted reply ───────────────────────────────────────────────────────────────────────────

def test_a_scripted_figure_is_answered_by_the_part_that_holds_its_page_on_that_parts_page():
    """`page` is the REPORT's page; the part holding report pages 11-13 (0-based 10-12) answers a
    figure on report page 12 as its own page 2 — the number a model reading the part gives — and
    leaves out one printed in another part, and any line the request does not ask."""
    script = {"units": "元", "lines": {
        CASH: [script_figure("100", page=12, caption="货币资金"),
               script_figure("90", page=40, caption="货币资金", period="prior")],
        TRADING: [script_figure("5", page=13)],
        "bs_ca__inventories": [script_figure("7", page=12)]}}
    reply = standin.scripted_whole_pdf_reply(
        script, question=_question([CASH, TRADING], [10, 11, 12]), part=1,
        part_pages=[10, 11, 12], response_schema=WholePdfReply)
    got = {line.key: [(f.period, f.amount, f.page) for f in line.figures] for line in reply.lines}
    assert got == {CASH: [("current", "100", 2)], TRADING: [("current", "5", 3)]}
    assert reply.units == "元"


def test_a_script_can_answer_one_part_differently_leave_a_line_out_and_give_text():
    script = {"lines": {CASH: [script_figure("100", page=1)],
                        TRADING: [script_figure("5", page=1)],
                        "notes__auditor_s_opinion": {"text": "Unqualified", "pages": [2]}},
              "parts": {"2": {"lines": {CASH: [script_figure("101", page=2)]}}},
              "leave_out": [TRADING]}
    keys = [CASH, TRADING, "notes__auditor_s_opinion"]
    first = standin.scripted_whole_pdf_reply(script, question=_question(keys, [0, 1], 1, 2),
                                             part=1, part_pages=[0], response_schema=WholePdfReply)
    second = standin.scripted_whole_pdf_reply(script, question=_question(keys, [1], 2, 2),
                                              part=2, part_pages=[1], response_schema=WholePdfReply)
    assert [(ln.key, [f.amount for f in ln.figures]) for ln in first.lines] == [(CASH, ["100"])]
    assert {ln.key: ([f.amount for f in ln.figures], ln.text, ln.text_pages)
            for ln in second.lines} == {CASH: (["101"], "", []),
                                        "notes__auditor_s_opinion": ([], "Unqualified", [1])}


def test_a_scripted_failure_raises_as_a_failed_request():
    question = _question([TRADING], [0])
    for fail in ({"statement": "balance sheet"}, {"key": TRADING}, {"part": 1}):
        with pytest.raises(standin.ScriptedFailure):
            standin.scripted_whole_pdf_reply({"fail": [fail]}, question=question, part=1,
                                             part_pages=None, response_schema=WholePdfReply)
    reply = standin.scripted_whole_pdf_reply({"fail": [{"part": 2}, {"statement": "notes"}]},
                                             question=question, part=1, part_pages=None,
                                             response_schema=WholePdfReply)
    assert reply.lines == []


# ── the file a request carries ─────────────────────────────────────────────────────────────────

def test_a_file_is_recorded_as_a_placeholder_naming_its_part_and_the_report_pages_it_holds(
        tmp_path: Path):
    filing = _pdf(["cover", "balance sheet 100", "note 5"])
    part = fitz.open()
    part.insert_pdf(fitz.open(stream=filing, filetype="pdf"), from_page=1, to_page=2)
    data = base64.b64encode(part.tobytes()).decode("ascii")
    messages = [{"role": "user", "content": _question([CASH], [1, 2]),
                 "documents": [{"media_type": "application/pdf", "data": data,
                                "name": "document-1.pdf"}]}]
    script = {"lines": {CASH: [script_figure("100", page=2, caption="balance sheet")]}}
    rec = standin.Recorder(tmp_path, whole_pdf_script=script, filing_bytes=filing)
    reply, meta = rec.handle(system=W.SYSTEM, messages=messages, response_schema=WholePdfReply,
                             max_tokens=1000, stage="whole_pdf")
    assert meta["model"] == "standin-scripted"
    assert [(ln.key, ln.figures[0].page) for ln in reply.lines] == [(CASH, 1)]
    [path] = (tmp_path / "requests").glob("*.json")
    text = path.read_text(encoding="utf-8")
    assert data not in text and "<pdf base64: " in text
    saved = json.loads(text)
    assert saved["line_keys"] == [CASH]
    doc = saved["documents"][0]
    assert (doc["part"], doc["pages"], doc["report_pages"]) == (1, 2, [1, 2])
    call = rec.calls[-1]
    assert call["scripted"] and call["answered"] and call["scripted_lines"] == 1
    # The same part asked again is the same request.
    rec.handle(system=W.SYSTEM, messages=messages, response_schema=WholePdfReply,
               max_tokens=1000, stage="whole_pdf")
    assert rec.calls[-1]["id"] == call["id"]


# ── a script from a rules run ──────────────────────────────────────────────────────────────────

def test_a_script_from_a_rules_run_answers_each_asked_line_with_its_printed_figure_and_rows():
    """The answer a compliant model gives where the rules read the filing right: the face figure
    on its page; a note part the rules added up, with the rows it added; nothing for a figure with
    no printed source, for a line the route does not ask, or for a line left out."""
    prov = {"page_index": 149, "text_snippet": "交易性金融资产"}
    result = {"rows": [
        {"canonical_key": TRADING, "values": [
            {"basis": "consolidated", "period_label": "current", "value": "1783494750.68",
             "value_raw": "1783494750.68", "provenance": prov}]},
        {"canonical_key": "sub__fa_cp_fvtpl_note_total", "source_label": "x", "values": [
            {"basis": "consolidated", "period_label": "current", "value": "1783494750.68",
             "value_raw": "1783494750.68", "provenance": None}],
         "derivation": {"consolidated:current": {"inputs": [
             {"label": "结构性存款", "value": "1629362905.47", "counted": True,
              "provenance": {"page_index": 181}},
             {"label": "权益工具投资", "value": "154131845.21", "counted": True,
              "provenance": {"page_index": 181}}]}}},
        {"canonical_key": "sub__fa_cp_level_3_rows", "values": [
            {"basis": "consolidated", "period_label": "current", "value": "0", "value_raw": "0",
             "provenance": None}]},
        {"canonical_key": "bs_ca__secur_and_other_fincl_assets_cp", "values": [
            {"basis": "consolidated", "period_label": "current", "value": "1",
             "value_raw": "1", "provenance": prov}]},
        {"canonical_key": CASH, "values": [
            {"basis": "consolidated", "period_label": "current", "value": "9", "value_raw": "9",
             "provenance": prov}]}]}
    script = standin.whole_pdf_script_from_result(result, _hk(), leave_out=[CASH], units="元")
    assert script["units"] == "元"
    assert script["lines"] == {
        TRADING: [{"basis": "consolidated", "period": "current", "amount": "1783494750.68",
                   "page": 150, "caption": "交易性金融资产"}],
        "sub__fa_cp_fvtpl_note_total": [{
            "basis": "consolidated", "period": "current", "amount": "1783494750.68", "page": 182,
            "caption": "结构性存款",
            "parts": [{"caption": "结构性存款", "amount": "1629362905.47"},
                      {"caption": "权益工具投资", "amount": "154131845.21"}]}]}


def test_the_command_line_writes_a_script_from_a_result(tmp_path: Path):
    result = tmp_path / "x__result.json"
    result.write_text(json.dumps({"rows": [{"canonical_key": TRADING, "values": [
        {"basis": "consolidated", "period_label": "prior", "value_raw": "5",
         "provenance": {"page_index": 3, "text_snippet": "t"}}]}]}), encoding="utf-8")
    out = tmp_path / "script.json"
    assert standin.main(["whole-pdf-script", str(result), "--set",
                         "output_csv_hk_line_items.json", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["lines"][TRADING][0]["page"] == 4


# ── end to end ─────────────────────────────────────────────────────────────────────────────────

def test_end_to_end_the_whole_pdf_route_reads_the_script_and_does_not_fall_back(monkeypatch,
                                                                                tmp_path):
    """The helper later tests build on: a filing (here a two-page one) read by the whole-PDF
    route, the stand-in answering from a script."""
    face = ["CONSOLIDATED STATEMENT OF FINANCIAL POSITION", "RMB          2024      2023",
            "Cash and cash equivalents   2,000   1,800"]
    doc = fitz.open()
    for lines in (["Annual report 2024"], face):
        page = doc.new_page()
        for i, text in enumerate(lines):
            page.insert_text((40, 72 + 16 * i), text, fontsize=9)
    script = {"units": "RMB", "lines": {CASH: [script_figure("2000", page=2, caption="Cash"),
                                               script_figure("1800", page=2, caption="Cash",
                                                             period="prior")]}}
    out, ctx, rec = run_whole_pdf(monkeypatch, tmp_path, doc.tobytes(), script)
    assert not any(line.startswith("whole_pdf:fallback") for line in ctx.logs)
    assert ctx.mapping_strategy == "whole_pdf"
    cash = next(li for li in out.line_items if li.canonical_key == CASH)
    assert sorted(str(v.value_raw) for v in cash.values.values()) == ["1800", "2000"]
    assert all("whole_pdf:on_page" in v.confidence.flags for v in cash.values.values())
    whole = [c for c in rec.calls if c["schema"] == "WholePdfReply"]
    assert whole and all(c["documents"][0]["report_pages"] == [0, 1] for c in whole)
